"""투영 헤드(ProjectionHead) 학습 워커 (QThread).

흐름:
1. ``TrainingDataStore`` 에서 매칭 쌍(positive) 을 모두 로드.
2. 이미지 풀을 Slot 별로 그룹핑 — Cross-slot negative 샘플링 풀로 사용.
3. 백본 임베딩을 미리 추출해 메모리/디스크 캐시 (`embedder.compute_*` 대신 직접
   백본을 호출하여 ‘기본’ 백본 출력 1280-d 를 보장).
4. 헤드만 ``TripletMarginLoss`` 로 학습 (Adam, lr=1e-3, 5-10 epochs).
5. ``registry.make_new_name()`` 으로 새 이름 부여, ``triplet_model.save_head()``
   로 ``.pt`` 저장 + ``registry.write_meta()``.
6. 끝나면 ``embedder.invalidate_caches()`` 호출.
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from . import embedder as emb_mod
from . import registry
from . import triplet_model
from .dataset import TrainingDataStore, TrainingPair


# ---------------------------------------------------------------------------
class TrainerSignals(QObject):
    """학습 단계별 시그널."""
    backbone_progress = pyqtSignal(int, int)               # done, total
    epoch_progress = pyqtSignal(int, int, float)           # epoch, total, loss
    finished = pyqtSignal(str)                             # 새 모델 이름
    failed = pyqtSignal(str)


# ---------------------------------------------------------------------------
class TrainHeadWorker(QThread):
    """투영 헤드만 fine-tune 하는 백그라운드 학습 작업자."""

    DEFAULT_EPOCHS = 8
    DEFAULT_BATCH = 64
    DEFAULT_LR = 1e-3
    DEFAULT_MARGIN = 0.3

    def __init__(self,
                 store: TrainingDataStore,
                 *,
                 epochs: int = DEFAULT_EPOCHS,
                 batch_size: int = DEFAULT_BATCH,
                 lr: float = DEFAULT_LR,
                 margin: float = DEFAULT_MARGIN,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._epochs = epochs
        self._batch = batch_size
        self._lr = lr
        self._margin = margin
        self.signals = TrainerSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    def run(self) -> None:                          # type: ignore[override]
        try:
            new_name = self._train()
            self.signals.finished.emit(new_name)
        except _AbortTraining as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:                    # pragma: no cover
            self.signals.failed.emit(f"학습 실패: {exc}")

    # ------------------------------------------------------------------
    def _train(self) -> str:
        if not triplet_model.is_available():
            raise _AbortTraining("torch / torchvision 이 설치되어 있지 않습니다")

        # 평가 로그의 confirmed pick 도 학습 데이터에 자동 통합 (#5).
        try:
            self._store.append_evaluation_picks()
        except Exception:
            pass

        pairs = self._store.load_all()
        if len(pairs) < 5:
            raise _AbortTraining(
                "학습 데이터가 너무 적습니다 (5쌍 이상 필요)"
            )

        # Slot 별 그룹핑 — cross-slot negative 샘플링
        slot_to_paths: dict[str, set[str]] = {}
        for p in pairs:
            slot_to_paths.setdefault(p.slot, set()).update(
                [p.ref_path, p.val_path]
            )

        if len(slot_to_paths) < 2:
            raise _AbortTraining(
                "Slot 이 1개뿐이라 cross-slot negative 가 부족합니다"
            )

        # 사용할 모든 이미지 path 수집 (존재하지 않는 파일은 제외)
        all_paths: list[str] = []
        for s in slot_to_paths.values():
            all_paths.extend(s)
        all_paths = sorted({p for p in all_paths if Path(p).exists()})
        if not all_paths:
            raise _AbortTraining("학습용 이미지 파일이 모두 사라졌습니다")

        # 1) 백본 임베딩 사전 추출 (도메인 전처리 공유, 배치) -----------
        import torch

        backbone = emb_mod._load_backbone()    # type: ignore[attr-defined]
        feat_cache: dict[str, "torch.Tensor"] = {}

        total = len(all_paths)
        BATCH = 32
        idx = 0
        pending: list[tuple[str, "torch.Tensor"]] = []

        def _flush_backbone():
            if not pending:
                return
            keys = [k for k, _ in pending]
            tensors = [t for _, t in pending]
            x = torch.stack(tensors)
            with torch.no_grad():
                f = backbone(x).detach().cpu()
            for k, v in zip(keys, f):
                feat_cache[k] = v.flatten()
            pending.clear()

        for path in all_paths:
            if self._stop:
                raise _AbortTraining("사용자가 학습을 중단했습니다")
            t = emb_mod.make_input_tensor(Path(path))
            idx += 1
            if t is None:
                self.signals.backbone_progress.emit(idx, total)
                continue
            pending.append((path, t))
            if len(pending) >= BATCH:
                _flush_backbone()
            if idx == 1 or idx == total or idx % 5 == 0:
                self.signals.backbone_progress.emit(idx, total)
        _flush_backbone()
        self.signals.backbone_progress.emit(total, total)

        # 유효 쌍만 필터링 (둘 다 캐시에 있어야 함)
        usable_pairs: list[TrainingPair] = [
            p for p in pairs
            if p.ref_path in feat_cache and p.val_path in feat_cache
        ]
        if len(usable_pairs) < 5:
            raise _AbortTraining(
                "임베딩으로 변환 가능한 학습 쌍이 부족합니다"
            )

        # 2) 헤드 학습 -----------------------------------------------
        from torch import nn, optim
        head = triplet_model.ProjectionHead()
        head.train()
        opt = optim.Adam(head.parameters(), lr=self._lr)
        loss_fn = nn.TripletMarginLoss(margin=self._margin, p=2)

        # Slot → cache key 인덱스 (negative 샘플링)
        slot_keys: dict[str, list[str]] = {
            s: [p for p in paths if p in feat_cache]
            for s, paths in slot_to_paths.items()
        }
        slot_keys = {s: v for s, v in slot_keys.items() if v}

        rng = random.Random(int(time.time()))

        # 모든 사용 가능한 cross-slot 풀을 1차원으로 펼친 텐서 (hard mining 용)
        all_slot_idx: list[tuple[str, str]] = []        # (slot, path)
        for s, paths in slot_keys.items():
            for q in paths:
                all_slot_idx.append((s, q))

        loss_history: list[float] = []
        for epoch in range(1, self._epochs + 1):
            if self._stop:
                raise _AbortTraining("사용자가 학습을 중단했습니다")
            rng.shuffle(usable_pairs)
            ep_loss = 0.0
            n_batches = 0
            # 첫 에폭은 랜덤 negative (워밍업), 이후는 hard mining.
            use_hard = epoch > 1

            for start in range(0, len(usable_pairs), self._batch):
                if self._stop:
                    raise _AbortTraining("사용자가 학습을 중단했습니다")
                batch = usable_pairs[start: start + self._batch]

                # 1) anchor / positive 텐서 구성
                a_keys = [p.ref_path for p in batch]
                p_keys = [p.val_path for p in batch]
                slots = [p.slot for p in batch]
                A_raw = torch.stack([feat_cache[k] for k in a_keys])
                P_raw = torch.stack([feat_cache[k] for k in p_keys])
                zA = nn.functional.normalize(head(A_raw), p=2, dim=1)
                zP = nn.functional.normalize(head(P_raw), p=2, dim=1)

                # 2) negative 선택 — 워밍업이면 랜덤, 그 이후엔 hard mining.
                n_paths: list[Optional[str]] = []
                for i, pair in enumerate(batch):
                    if use_hard:
                        n_paths.append(self._hard_negative(
                            zA[i], pair.slot, all_slot_idx,
                            feat_cache, head, rng,
                        ))
                    else:
                        n_paths.append(self._sample_negative(
                            pair.slot, slot_keys, rng, anchor=pair.ref_path,
                        ))

                # negative 가 못 잡힌 행은 제거
                keep_idx = [i for i, n in enumerate(n_paths) if n is not None]
                if not keep_idx:
                    continue
                zA = zA[keep_idx]
                zP = zP[keep_idx]
                N_raw = torch.stack([feat_cache[n_paths[i]] for i in keep_idx])
                zN = nn.functional.normalize(head(N_raw), p=2, dim=1)

                loss = loss_fn(zA, zP, zN)
                opt.zero_grad()
                loss.backward()
                opt.step()
                ep_loss += float(loss.detach().cpu())
                n_batches += 1

            avg = (ep_loss / n_batches) if n_batches else 0.0
            loss_history.append(avg)
            self.signals.epoch_progress.emit(epoch, self._epochs, avg)

        # 3) 저장 — 임시 파일로 먼저 쓰고 atomic rename ---------------
        head.eval()
        name = registry.make_new_name(datetime.now())
        info = registry.ModelInfo(
            name=name,
            weights_path=registry.paths.models_dir() / f"{name}.pt",
            meta_path=registry.paths.models_dir() / f"{name}.json",
            eval_path=registry.paths.evaluations_dir() / f"{name}.jsonl",
        )
        tmp = info.weights_path.with_suffix(".pt.tmp")
        triplet_model.save_head(head, tmp)
        tmp.replace(info.weights_path)
        # pairs.jsonl 의 내용 SHA1 — 학습 시점 데이터 식별자 (스펙 §8.2-c)
        pairs_hash = ""
        try:
            import hashlib
            jsonl = self._store.path()
            if jsonl.exists():
                pairs_hash = hashlib.sha1(jsonl.read_bytes()).hexdigest()
        except Exception:
            pass
        registry.write_meta(info, {
            "name": name,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "backbone": "mobilenet_v3_small",
            "head_dims": list(head.dims),
            "num_train_pairs": len(usable_pairs),
            "pairs_used": len(usable_pairs),
            "pairs_hash": pairs_hash,
            "notes": "",
            "epochs": self._epochs,
            "batch_size": self._batch,
            "lr": self._lr,
            "margin": self._margin,
            "loss_history": [round(x, 4) for x in loss_history],
        })

        # 직전 모델 가중치를 백업 (rollback 가능) — #10 위험 완화
        prev_active = registry.get_active()
        if prev_active != registry.BASIC:
            prev_info = registry.find(prev_active)
            if prev_info and prev_info.weights_path.exists():
                # 동일 작업 폴더에 .prev.pt 로 한 단계만 보관
                try:
                    backup = prev_info.weights_path.with_suffix(".prev.pt")
                    if backup.exists():
                        backup.unlink()
                    prev_info.weights_path.replace(backup)
                    # 백업한 자리에 원본을 다시 두진 않는다 — 이전 모델은 그대로
                    backup.replace(prev_info.weights_path)
                except OSError:
                    pass

        # active 갱신 + latest.txt 기록 + 임베더 캐시 무효화 + 디스크 cnn 캐시 정리
        registry.set_active(name)
        registry.set_latest(name)
        emb_mod.invalidate_caches()
        try:
            from ..similarity import pipeline as _pipe
            _pipe.invalidate_cnn_cache()
        except Exception:
            pass
        return name

    # ------------------------------------------------------------------
    @staticmethod
    def _sample_negative(slot: str,
                         slot_keys: dict[str, list[str]],
                         rng: random.Random,
                         *,
                         anchor: str) -> Optional[str]:
        candidates_slots = [s for s in slot_keys.keys() if s != slot]
        if not candidates_slots:
            return None
        for _ in range(8):
            s = rng.choice(candidates_slots)
            pool = slot_keys.get(s) or []
            if not pool:
                continue
            pick = rng.choice(pool)
            if pick != anchor:
                return pick
        return None

    @staticmethod
    def _hard_negative(z_anchor,
                       anchor_slot: str,
                       all_slot_idx: list[tuple[str, str]],
                       feat_cache: dict[str, "object"],
                       head,
                       rng: random.Random,
                       *,
                       sample_size: int = 64) -> Optional[str]:
        """무작위로 sample_size 개의 cross-slot 임베딩을 뽑은 뒤, anchor 와 가장
        가까운 것을 negative 로 선택 (semi-hard mining).

        대규모 풀에서 매 step 전부 비교하면 OOM/속도 문제가 있으니 sampling 기반.
        """
        import torch
        from torch.nn import functional as F
        pool = [p for (s, p) in all_slot_idx if s != anchor_slot]
        if not pool:
            return None
        rng.shuffle(pool)
        cand = pool[:sample_size]
        if not cand:
            return None
        feats = torch.stack([feat_cache[p] for p in cand])
        with torch.no_grad():
            z = F.normalize(head(feats), p=2, dim=1)
            sims = (z * z_anchor.detach().unsqueeze(0)).sum(dim=1)
            best = int(sims.argmax().item())
        return cand[best]


class _AbortTraining(Exception):
    """학습 중단을 graceful 하게 알리는 내부 예외."""
