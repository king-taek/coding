"""정답 만들기 모드 (검증용) — ref 마다 정답 val 을 **복수 선택**해 저장.

`.1/.2` 형제처럼 동일 위치의 여러 캡처가 모두 정답일 수 있으므로, 한 ref 에
여러 정답을 표시할 수 있다.  후보는 고전 유사도순으로 정렬해 보여줘 정답이
상단에 오게 한다(전체 풀 스크롤 가능 — recall 실패 케이스도 직접 찾아 표시).

출력: 결과/레퍼런스/groundtruth_{session}.jsonl
  {"type":"truth_config", ...}
  {"type":"truth", "slot":..., "ref_filename":..., "correct":[val 파일명...], "n_correct":N}
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QScrollArea,
                             QVBoxLayout, QWidget)

from ... import i18n
from ..widgets.neon_button import NeonButton
from ..widgets.scalable_image import ScalableImage
from ..widgets.thumb_grid import ThumbEntry, ThumbGrid


class _ScoreSignals(QObject):
    done = pyqtSignal(object, object)        # (ref_path, [(ImageItem, score)])


class _ScoreWorker(QThread):
    """한 ref 에 대해 슬롯 val 들을 고전 점수로 정렬(라벨 무관, 표시용 순서)."""

    def __init__(self, ref, vals, cfg, parent=None) -> None:
        super().__init__(parent)
        self._ref = ref
        self._vals = list(vals)
        self._cfg = cfg
        self.signals = _ScoreSignals()

    def run(self) -> None:
        try:
            from ...workers.matcher import score_ref_classical
            cands = score_ref_classical(self._ref, self._vals, threshold=0.0, cfg=self._cfg)
            out = [(c.item, float(c.score)) for c in cands]
        except Exception:
            out = [(v, 0.0) for v in self._vals]      # 폴백: 풀 순서 그대로
        self.signals.done.emit(self._ref.path, out)


class GroundTruthPage(QWidget):
    """ref별 정답(복수) 선택 → jsonl 저장."""

    finished = pyqtSignal(str)               # 저장 경로
    cancelled = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cfg = None
        self._session_id = ""
        self._queue: List[Tuple[str, object, list]] = []
        self._idx = 0
        self._truth: dict = {}               # (slot, ref_name) -> [val names]
        self._score_cache: dict = {}         # ref_path -> [(item, score)]
        self._worker: Optional[_ScoreWorker] = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self.title = QLabel("정답 만들기 — 정답인 사진을 모두 클릭(복수 선택)", self)
        self.title.setProperty("role", "title")
        root.addWidget(self.title)
        self.progress = QLabel("", self)
        root.addWidget(self.progress)

        body = QHBoxLayout()
        # 좌: 기준(ref) 이미지
        self.center_img = ScalableImage()
        left_scroll = QScrollArea(self)
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(self.center_img)
        left_scroll.setMinimumWidth(320)
        body.addWidget(left_scroll, stretch=2)
        # 우: 후보 그리드(복수 토글 선택)
        self.grid = ThumbGrid(columns=4, inline_select=True, truncate=False)
        self.grid.inline_changed.connect(self._on_selection_changed)
        right_scroll = QScrollArea(self)
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(self.grid)
        body.addWidget(right_scroll, stretch=3)
        root.addLayout(body, stretch=1)

        self.sel_label = QLabel("선택된 정답: 0", self)
        root.addWidget(self.sel_label)

        bar = QHBoxLayout()
        self.btn_prev = NeonButton("◀ 이전", role="ghost")
        self.btn_prev.clicked.connect(lambda: self._goto(self._idx - 1))
        bar.addWidget(self.btn_prev)
        self.btn_clear = NeonButton("정답 없음(선택 해제)", role="ghost")
        self.btn_clear.clicked.connect(self._clear_selection)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        self.btn_next = NeonButton("다음 ▶", role="primary")
        self.btn_next.clicked.connect(lambda: self._goto(self._idx + 1))
        bar.addWidget(self.btn_next)
        self.btn_save = NeonButton("저장 및 종료", role="primary")
        self.btn_save.clicked.connect(self._on_save)
        bar.addWidget(self.btn_save)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def load_state(self, tasks, *, cfg, session_id: str) -> None:
        self._cfg = cfg
        self._session_id = session_id or time.strftime("%Y%m%d_%H%M%S")
        self._queue = []
        for slot, refs, vals in tasks:
            for r in refs:
                self._queue.append((slot, r, list(vals)))
        self._idx = 0
        self._truth = {}
        self._score_cache = {}
        if self._queue:
            self._show(0)

    # ------------------------------------------------------------------
    def _key(self, i):
        slot, ref, _vals = self._queue[i]
        return (slot, Path(ref.path).name)

    def _show(self, i: int) -> None:
        if not (0 <= i < len(self._queue)):
            return
        self._idx = i
        slot, ref, vals = self._queue[i]
        self.center_img.set_image(ref.path)
        self.progress.setText(
            f"{i + 1} / {len(self._queue)}    슬롯 {slot}    기준: {Path(ref.path).name}")
        self.btn_prev.setEnabled(i > 0)
        cached = self._score_cache.get(ref.path)
        if cached is not None:
            self._populate(cached)
        else:
            self.grid.set_entries([])
            self.sel_label.setText("후보 계산 중…")
            self._start_worker(ref, vals)

    def _start_worker(self, ref, vals) -> None:
        self._worker = _ScoreWorker(ref, vals, self._cfg, parent=self)
        self._worker.signals.done.connect(self._on_scored)
        self._worker.start()

    def _on_scored(self, ref_path, scored) -> None:
        self._score_cache[ref_path] = scored
        # 결과가 도착했을 때 여전히 그 ref 를 보고 있을 때만 반영.
        if 0 <= self._idx < len(self._queue) and self._queue[self._idx][1].path == ref_path:
            self._populate(scored)

    def _populate(self, scored) -> None:
        entries = [ThumbEntry(item=it, extra={"score": s}) for it, s in scored]
        self.grid.set_entries(entries)
        # 이전 선택 복원
        chosen = set(self._truth.get(self._key(self._idx), []))
        for tile in self.grid.tiles():
            if tile.entry.item.path.name in chosen:
                tile.set_inline_selected(True)
        self._update_sel_label()

    def _on_selection_changed(self) -> None:
        self._save_current()
        self._update_sel_label()

    def _save_current(self) -> None:
        if not (0 <= self._idx < len(self._queue)):
            return
        names = [it.path.name for it in self.grid.inline_selected_items()]
        self._truth[self._key(self._idx)] = names

    def _update_sel_label(self) -> None:
        n = len(self.grid.inline_selected_items())
        self.sel_label.setText(f"선택된 정답: {n}")

    def _clear_selection(self) -> None:
        self.grid.set_all_inline_selected(False)
        self._save_current()
        self._update_sel_label()

    def _goto(self, i: int) -> None:
        self._save_current()
        if i >= len(self._queue):
            self._on_save()
            return
        self._show(max(0, i))

    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        self._save_current()
        try:
            from ...utils import paths
            out_dir = paths.results_dir() / "레퍼런스"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"groundtruth_{self._session_id}.jsonl"
            n_with = sum(1 for v in self._truth.values() if v)
            with out_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "truth_config", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "session_id": self._session_id, "n_refs": len(self._queue),
                    "n_refs_with_answer": n_with,
                }, ensure_ascii=False) + "\n")
                for slot, ref, _vals in self._queue:
                    key = (slot, Path(ref.path).name)
                    correct = self._truth.get(key, [])
                    f.write(json.dumps({
                        "type": "truth", "slot": slot,
                        "ref_filename": Path(ref.path).name,
                        "correct": correct, "n_correct": len(correct),
                    }, ensure_ascii=False) + "\n")
            QMessageBox.information(
                self, i18n.KO.APP_TITLE,
                f"정답 {n_with}/{len(self._queue)} ref 저장 완료.\n\n{out_path}\n\n"
                "이 파일을 GitHub 에 올려주세요.")
            self.finished.emit(str(out_path))
        except Exception as exc:
            QMessageBox.warning(self, i18n.KO.APP_TITLE, f"정답 저장 실패:\n{exc!r}")
