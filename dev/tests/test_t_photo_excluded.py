"""``.t.`` 사진은 **항상 뺀다** — 묻지 않는다(사용자 요청, 네 번째 결정).

이 자리의 이력: '항상 뺀다'(옛 `is_ignored_name`) → '항상 넣는다'(`0b676d1`) →
'세션마다 예시 사진과 함께 묻는다'(`79cc7f4`, `TPhotoAskDialog`) → **'항상 뺀다'**
(지금).  묻는 팝업은 검증을 시작할 때마다 클릭을 하나 더 요구했고, 사용자가 '늘
뺀다' 로 결정했다.  다음 사람이 한쪽으로 되돌릴 때 이 이력을 알도록 적어 둔다.

열거(`models.slot._list_images`)가 유일한 소스이므로 여기서 빠지면 썸네일·매칭·
엑셀·슬롯별 장수까지 전부 따라온다 — 흐름 어디에도 두 번째 거름망이 필요 없다.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

from aoi_verification.app import i18n
from aoi_verification.app.models import slot as slot_mod
from aoi_verification.app.models.slot import has_t_token, is_ignored_name

_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 판정 자체 — **부분 문자열이 아니라 점 토큰**이다
# ---------------------------------------------------------------------------
def test_only_a_lone_t_token_counts():
    assert has_t_token("-86955.68631.t.1.jpg") is True
    assert has_t_token("t.1.jpg") is True
    # 실물 다수가 같은 자리에 'c' 를 쓴다 — 그건 해당하지 않는다.
    assert has_t_token("272646.165679.c.1000203959.2.jpeg") is False
    assert has_t_token("W6459076XYG1_2_0_23_2.jpg") is False


def test_a_t_inside_a_word_is_not_a_token():
    """이름 어딘가에 t 가 있다고 걸리면 멀쩡한 사진이 통째로 빠진다."""
    for name in ("test.jpg", "target.1.jpg", "1.tt.2.jpg", "1.T2.3.jpg"):
        assert has_t_token(name) is False, name
        assert is_ignored_name(name) is False, name


# ---------------------------------------------------------------------------
# 열거에서 빠진다 — 그래서 어느 단계에도 오지 않는다
# ---------------------------------------------------------------------------
def test_enumeration_drops_them():
    assert is_ignored_name("-86955.68631.t.1.jpg") is True
    assert is_ignored_name("CognexInSight17xx_Bottom.jpg") is True     # 전경 사진도
    assert is_ignored_name("272646.165679.c.1000203959.2.jpeg") is False


def _touch(p: pathlib.Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00")


def test_scan_never_returns_a_t_photo(tmp_path):
    """스캔 결과(썸네일·매칭·엑셀의 입력)에 `.t.` 사진이 한 장도 없다."""
    for side in ("ref", "val"):
        _touch(tmp_path / side / "S1" / "1.2.c.9.jpeg")
        _touch(tmp_path / side / "S1" / "-86955.68631.t.1.jpg")
        _touch(tmp_path / side / "S1" / "-11.22.t.3.jpg")
    sr = slot_mod.scan(tmp_path / "ref", tmp_path / "val")
    for items in (sr.slots["S1"].ref_images, sr.slots["S1"].val_images):
        names = [it.filename for it in items]
        assert names == ["1.2.c.9.jpeg"], f"`.t.` 사진이 남았다: {names}"
    # 슬롯 선택 팝업이 보여 주는 장수도 같은 답이다.
    assert slot_mod.count_images(tmp_path / "ref" / "S1") == 1


# ---------------------------------------------------------------------------
# 묻는 경로는 **지워졌다** — 남아 있으면 '항상 뺀다' 가 조용히 다시 뒤집힌다
# ---------------------------------------------------------------------------
def test_the_prompt_is_gone():
    """팝업 위젯·문구·호출부가 전부 없어야 한다.  하나라도 남으면 죽은 코드가 아니라
    '다음 사람이 다시 켜기 쉬운 스위치' 다."""
    assert not (_ROOT / "aoi_verification/app/ui/widgets/t_photo_ask_dialog.py"
                ).exists(), "묻는 팝업 위젯이 남아 있다"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(
            "aoi_verification.app.ui.widgets.t_photo_ask_dialog")
    assert not [n for n in dir(i18n.KO) if n.startswith("T_PHOTO_ASK")], \
        "묻는 팝업의 문구가 ko.py 에 남아 있다"
    src = (_ROOT / "aoi_verification/app/ui/main_window.py").read_text(
        encoding="utf-8")
    assert "_ask_about_t_photos" not in src, "스캔 흐름이 아직 묻는다"
