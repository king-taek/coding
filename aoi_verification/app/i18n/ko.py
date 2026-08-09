"""모든 사용자 노출 문자열(한국어)을 한 곳에 모아둔 모듈.

UI/로그/툴팁/오류 메시지 모두 이 모듈을 통해 참조합니다.
번역이나 일괄 수정 시 이 파일만 보면 됩니다.
"""

# ── 앱/메타 ────────────────────────────────────────────────────────────────
APP_TITLE = "AOI 검증"
# 개발자 크레딧 — 주요 화면/상태바 공통 표시.
CREDIT = "Developed by 임현택"

# ── 시작 스플래시(로고 + 로딩) ────────────────────────────────────────────
SPLASH_MODULES = "구성 요소를 불러오는 중…"
SPLASH_PAGES = "화면을 준비하는 중…"
SPLASH_READY = "준비 완료"

# ── OpenVINO 자동 설치 안내 ───────────────────────────────────────────────
OPENVINO_OFFER_TITLE = "Intel GPU 가속 활성화"
OPENVINO_OFFER_BODY = (
    "Intel CPU 가 감지되었습니다.\n"
    "OpenVINO 를 설치하면 Intel GPU (Iris Xe / Arc) 가속이 자동으로 "
    "활성화되어 유사도 계산이 빨라집니다.\n\n"
    "지금 설치할까요? (약 200 MB)"
)
OPENVINO_OFFER_BTN_INSTALL = "지금 설치"
OPENVINO_OFFER_BTN_LATER = "다음에"
OPENVINO_OFFER_BTN_NEVER = "다시 보지 않기"
OPENVINO_INSTALL_PROGRESS = "OpenVINO 설치 중…"
OPENVINO_INSTALL_DONE = (
    "OpenVINO 설치 완료!\n프로그램을 다시 시작하면 Intel GPU 가속이 적용됩니다."
)
OPENVINO_INSTALL_FAILED_FMT = (
    "OpenVINO 설치에 실패했습니다 — {error}\n\n"
    "수동으로 시도해보세요:  pip install openvino"
)

# ── 공통 버튼/액션 ─────────────────────────────────────────────────────────
BTN_OK = "확인"
BTN_CANCEL = "취소"
# 진행 중인 작업을 멈추는 버튼(로딩 오버레이) — '취소' 와 뜻이 다르다.
BTN_STOP = "중지"
BTN_BACK_TO_SETUP = "← 설정으로"
BTN_START = "검증 시작"
# 시작 직후 — 무거운 구성 요소(영상 처리·가속)를 백그라운드에서 불러오는 동안의 표기.
# 창은 먼저 뜨고, 준비가 끝나면 원래 이름(BTN_START)으로 돌아오며 활성화된다.
BTN_START_PREPARING = "준비 중…"
BTN_BROWSE = "폴더 선택…"
BTN_VERIFY = "검증"
BTN_EXCLUDE = "제외"
BTN_UNDO = "되돌리기(Z)"
BTN_NO_MATCH = "매칭 없음"
BTN_MATCH_UNDO = "되돌리기"
MATCH_UNDO_TOOLTIP = "직전 매칭/매칭없음 결정을 취소합니다 (Ctrl+Z)"
BTN_RETRY_SKIP = "보류 재시도"
BTN_SELECT_MODE = "선택 모드"
# Stage 1 좌측 패널 — 사진을 여러 장 고르면 헤더에 나타나는 일괄 액션.
# ★ 예전엔 클릭 선택이 아무 데도 연결되지 않아 '테두리만 생기고 할 수 있는 게 없는'
#   죽은 기능이었다.  라벨에 개수를 넣어 무엇에 적용되는지 분명히 한다.
BTN_INLINE_VERIFY_FMT = "선택 {n} 장 검증"
BTN_INLINE_EXCLUDE_FMT = "선택 {n} 장 제외"

# ── 다중 선택 다이얼로그 (Stage 1 선택 모드) ─────────────────────────────
BULK_SELECT_TITLE_FMT = "{panel} — 다중 선택"
BULK_SELECT_HINT = (
    "사진을 클릭하거나 빈 곳에서 드래그해 여러 장을 선택/해제하세요. "
    "선택된 사진들에 아래 액션이 적용됩니다."
)
BULK_SELECT_SUMMARY_FMT = "선택됨: {n} 장"
BULK_SELECT_EMPTY = "표시할 사진이 없습니다."
BULK_SELECT_ALL = "전체 선택"
BULK_DESELECT_ALL = "선택 해제"
# 사진 크기 슬라이더 / 페이지네이션 / 우클릭 확대 (대량 표시 대응)
BULK_SIZE_LABEL = "사진 크기"
BULK_PAGE_PREV = "◀ 이전"
BULK_PAGE_NEXT = "다음 ▶"
BULK_PAGE_LABEL_FMT = "페이지 {page} / {total}"
BULK_TILE_ZOOM_TOOLTIP = "우클릭하여 크게 보기"
BTN_MOVE_TO_EXCLUDE = "제외로 이동"
BTN_MOVE_TO_TARGET = "검증 대상으로 이동"
BTN_BACK_TO_CENTER = "중앙으로 복귀(재결정)"
BTN_BATCH_EXCLUDE = "선택 항목 검증 제외"
BTN_BATCH_VERIFY = "선택 항목 검증 대상 지정"
BTN_VIEW_EXCLUDED_FMT = "검증 제외 사진 보기 ({n})"
# Stage 1 ‘선택 종료’ — 미결정 사진 모두 제외 처리 후 다음 단계로.
BTN_END_SELECTION = "선택 종료"
# Stage 1 에서 설정 화면으로 돌아갈 때 — 이미 내린 결정이 사라지므로 한 번 묻는다.
MATCH_CANCEL_CONFIRM_TITLE = "매칭 중단"
# ★ 중단은 '잠깐 멈춤' 이 아니다 — 정확도 사고를 막기 위해 계산 결과를 전부 버린다
#   (남기면 설정을 바꿔 다시 돌려도 1회차 결과가 재사용된다).  그 사실을 말해 준다.
MATCH_CANCEL_CONFIRM_FMT = (
    "진행 중인 매칭을 중단할까요?  ({done} / {total} 까지 진행)\n\n"
    "지금까지 계산한 결과는 저장되지 않고, 다시 시작하면 처음부터 계산합니다."
)
SELECT_BACK_CONFIRM_TITLE = "설정으로 돌아가기"
SELECT_BACK_CONFIRM_FMT = (
    "지금까지 결정한 {n} 장이 사라지고 설정 화면으로 돌아갑니다.\n"
    "계속할까요?"
)
END_SELECTION_CONFIRM_TITLE = "선택 종료"
END_SELECTION_CONFIRM_FMT = (
    "남은 {n} 장의 미결정 사진을 모두 ‘검증 제외’ 로 처리하고 "
    "다음 단계로 진행할까요?"
)
BULK_SELECT_EXCLUDED_TITLE = "검증 제외 사진"

BTN_EXPORT_EXCEL = "엑셀로 저장"
BTN_OPEN_SAVED_FILE = "파일 열기"
BTN_OPEN_SAVED_FOLDER = "폴더 열기"
BTN_RETRY_SAVE = "다시 시도"
BTN_NEW_SESSION = "새 검증 시작"
NEW_SESSION_CONFIRM_TITLE = "새 검증 시작"
NEW_SESSION_CONFIRM_BODY = (
    "아직 엑셀로 저장하지 않았습니다.  지금 새 검증을 시작하면 이번 결과가 사라집니다.\n"
    "계속할까요?"
)
# 결과 화면 → 전용 검토 화면으로 되돌아가기(검토 결과는 그대로 보존된다).
BTN_BACK_TO_REVIEW = "← 검토 화면으로"

# ── 매칭 결과 검토 (#18) ───────────────────────────────────────────────────
REVIEW_DIALOG_TITLE = "매칭 결과 검토"
REVIEW_HINT = (
    "잘못 매칭된 행은 [삭제] 로 표시(빨간 테두리)한 뒤 [확인] 을 누르면\n"
    "결과에서 제외됩니다.  제외된 사진은 ‘매치 실패’ 로 분류되어 매치 실패\n"
    "사진 검토에서 ‘매칭 취소 목록’ 으로 다시 검토할 수 있습니다."
)
REVIEW_BTN_DELETE = "삭제"
REVIEW_BTN_UNDELETE = "삭제 취소 ↩"

# ── 셋업 페이지 ────────────────────────────────────────────────────────────
# 이전 값 "AOI Recipe Verificator" 는 (a) 오타(verificator → verifier) 이고
# (b) 나머지가 전부 한국어인 화면에 영어만 남아 있었다.
SETUP_TITLE = "AOI 레시피 검증"
SETUP_REF_GROUP = "기준 장비"
SETUP_VAL_GROUP = "검증 장비"
SETUP_FOLDER_LABEL = "최상위 폴더"
SETUP_MACHINE_LABEL = "호기 번호"
SETUP_THRESHOLD_LABEL = "유사도 임계치"
SETUP_FOLDER_PLACEHOLDER = "폴더를 선택해 주세요"
SETUP_MACHINE_PLACEHOLDER = "예) 1호기"

# ── 검증 단계 헤더 ─────────────────────────────────────────────────────────
STAGE1_TITLE = "Stage 1 — 후보 선별"
STAGE2_TITLE = "Stage 2 — 유사도 기반 매칭"
STAGE2_TITLE_COORD = "Stage 2 — 좌표 기반 매칭 (v2)"
RESULT_TITLE = "검증 결과"
# 결과 요약 — 핵심 수치는 타일로, 나머지는 문장으로.
RESULT_MACHINES_FMT = "기준 장비: {ref}    검증 장비: {val}"
RESULT_SLOT_ONLY_REF_FMT = "Slot 불일치  ·  기준 전용: {names}"
RESULT_SLOT_ONLY_VAL_FMT = "Slot 불일치  ·  검증 전용: {names}"
VALUE_NONE = "없음"
STAT_MATCHED = "매칭 성공"
STAT_OVER_TOLERANCE = "허용 초과"
STAT_NO_MATCH = "매치 없음"
STAT_SLOT_MISMATCH = "Slot 불일치"
MATCH_REVIEW_TITLE = "매치 검토"

PANEL_LEFT_CANDIDATES = "검증 후보들 (남은 사진)"
PANEL_CENTER_DECIDE = "검증 결정할 사진"
PANEL_RIGHT_TARGETS = "검증 대상 (검증하기로 한 사진들)"
# 우측 패널이 비었을 때의 안내 — 처음 쓰는 사람이 이 칸의 용도를 알 수 있게.
PANEL_RIGHT_EMPTY = "→ 또는 [✓ 검증] 으로 보낸 사진이 여기에 쌓입니다."

PANEL_MATCH_REF = "기준 사진"
PANEL_MATCH_CANDIDATES = "검증 장비 후보"

# Stage 2 의 보류/매칭없음 사진 팝업
BTN_VIEW_SKIPPED_FMT = "보류된 사진 보기 ({n})"
SKIPPED_DIALOG_TITLE = "보류 / 매칭 없음 사진"
SKIPPED_SECTION_DEFER_FMT = "잠시 보류 ({n} 장)"
SKIPPED_SECTION_NO_MATCH_FMT = "매칭 없음 확정 ({n} 장)"
SKIPPED_DIALOG_EMPTY = "보류 / 매칭 없음 사진이 없습니다."

# 매치 실패 사진 검토 다이얼로그 (#8)
BTN_REVIEW_UNMATCHED = "매치 실패 사진 검토"
UNMATCHED_REVIEW_TITLE = "매치 실패 사진 검토 — {n} 장"
UNMATCHED_REVIEW_PROGRESS_FMT = "{idx} / {total} — {slot}"
UNMATCHED_REVIEW_HINT = (
    "매치 실패한 기준 사진을 하나씩 검토합니다. 같은 슬롯의 검증 장비 후보를"
    " 유사도 순으로 보여줍니다. 맞는 사진을 클릭해 선택(파란 테두리)한 뒤"
    " [매치 확정] 을 누르세요. 후보를 우클릭하면 크게 비교할 수 있습니다."
)
# 좌표로 매칭한 세션에서 이 화면만 유사도로 줄을 세운다는 사실을 알린다 (U-18).
#   좌표 모드로 검증했는데 후보 순서가 좌표와 무관해 보여 혼란을 준 지점이다.
UNMATCHED_REVIEW_COORD_NOTE = (
    "이 검증은 좌표 기준으로 매칭했지만, 이 화면의 후보 순서는 좌표 거리가 아니라"
    " 사진 유사도입니다. 좌표로 짝을 찾지 못한 사진들이라 유사도로 다시 줄을 세웁니다."
)
UNMATCHED_REVIEW_NO_CANDIDATES = "이 슬롯에는 검증 장비 후보가 없습니다."
UNMATCHED_REVIEW_DONE_FMT = "{n} 건의 신규 매칭을 확정했습니다."
UNMATCHED_REVIEW_EMPTY = "검토할 매치 실패 사진이 없습니다."
UNMATCHED_CONFIRM_ON_CLOSE = (
    "선택(파란 테두리)했지만 아직 확정하지 않은 후보가 있습니다.\n"
    "선택한 대로 매칭하시겠습니까?"
)
BTN_UNMATCHED_CONFIRM = "매치 확정"
BTN_UNMATCHED_SELECT_THIS = "이 후보로 선택"
BTN_UNMATCHED_NEXT = "다음 사진"
BTN_UNMATCHED_PREV = "← 이전"
BTN_UNMATCHED_CLOSE = "검토 종료"

# ‘매칭 취소’ 결합 (#14) — 결과 검토가 MissEntry.note 에 이 표시를 남기고
# (`result_page`), 실패 검토가 그것을 보고 목록 뒤로 모은다
# (`unmatched_review_dialog._is_cancelled`).  두 곳이 각자 문자열을 하드코딩하면
# 한쪽만 고쳤을 때 **예외 없이 정렬만 조용히 망가진다** — 여기 한 곳에서 정한다.
CANCELLED_NOTE_MARK = "매칭 취소"
CANCELLED_NOTE = f"{CANCELLED_NOTE_MARK} (검토에서 삭제)"
UNMATCHED_CANCELLED_SEPARATOR = f"── {CANCELLED_NOTE_MARK} 목록 ──"

# ── 줌-뷰 윈도우 ───────────────────────────────────────────────────────────
ZOOM_TITLE_TARGETS = "검증 대상인 사진들 — {slot}"
ZOOM_TITLE_EXCLUDED = "검증 하지 않을 사진 — {slot}"
ZOOM_TITLE_CANDIDATES = "검증 후보 사진들 — {slot}"
ZOOM_BTN_EXCLUDE = "검증에서 제외"
ZOOM_BTN_TO_TARGET = "검증 대상으로 변경"
ZOOM_BTN_TO_CENTER = "재결정으로 복귀"
ZOOM_BTN_PICK_MATCH = "이 사진으로 매칭"

# ── 단축키 ────────────────────────────────────────────────────────────────
SHORTCUT_TOOLTIP = (
    "단축키:  → = 검증   /   ← = 제외   /   Z = 되돌리기"
)
SHORTCUT_STAGE2_TOOLTIP = "단축키:  N = 매칭 없음 확정    Ctrl+Z = 되돌리기"

# ── 사진 크기 슬라이더 ────────────────────────────────────────────────────
IMAGE_SIZE_LABEL = "사진 크기"
SLOT_LABEL_FMT = "Slot: {slot}"

# ── 사용 방법 토글 ────────────────────────────────────────────────────────
HOWTO_TOGGLE_OPEN = "사용 방법 ▾"
HOWTO_TOGGLE_CLOSE = "사용 방법 ▴"

# ── 유사도 엔진 모드 + 중앙 전처리 ────────────────────────────────────────
ENGINE_CARD_TITLE = "매칭 설정"
# 구형(유사도) 모드 — 명시적 스위치.  '펼치기 = 켜기' 였던 옛 동작을 없앴다.
LEGACY_SWITCH_TITLE = "유사도 엔진(구형) 사용"
LEGACY_SWITCH_DESC = (
    "끄면 좌표 매칭(파일명/INI/KLA 좌표 데이터)을 씁니다 — 권장 기본값.\n"
    "켜면 유사도 점수가 임계치 이상인 가장 가까운 후보를 자동 선택합니다."
)
LEGACY_MODE_HINT = "좌표 데이터가 없는 예전 자료에 쓰는 대체 경로입니다."
# 지금 어떤 파라미터가 유효한지 문장으로 알려준다(비활성 컨트롤의 이유).
ENGINE_ACTIVE_COORD = "좌표 매칭 사용 중 — 허용 오차가 판정 기준입니다."
ENGINE_ACTIVE_LEGACY_FMT = "구형 {sub} 모드 사용 중 — 유사도 임계치가 판정 기준입니다."
# 문장 안에 넣을 짧은 이름(타일 라벨의 괄호까지 넣으면 괄호가 겹친다).
ENGINE_MODE_BASIC_SHORT = "기본"
ENGINE_MODE_EFFICIENCY_SHORT = "고효율"
# ── 상단 모드 배지 — "지금 무슨 모드야?"를 스크롤 없이 2초 안에 ───────────────────
# 채점자 5인이 전부 같은 지적을 했다: 판정 기준 문장이 엔진 카드 안(A안은 화면 밖,
# C안은 접힘 아래)에 있어, 무슨 엔진·무슨 수치로 도는지 알려면 찾아 내려가야 했다.
# 배지는 **판정 기준 값까지** 담는다 — 모드 이름만으로는 오조작을 못 막는다.
MODE_BADGE_CAPTION = "판정 기준"
# ★ (이름, 수치) 로 나눈다 — 이름은 한국어(본문 서체), 수치만 모노.  한 라벨에 모노를
#   걸면 모노 서체에 한글 글리프가 없어 문장 안에서 서체가 갈린다.
#   배지·C안 요약이 **같은 함수**(`SetupPage.judgement_text`)에서 문장을 받는다.
JUDGE_NAME_COORD = "좌표 매칭 · 허용 오차"
JUDGE_VALUE_COORD_FMT = "{tol:.0f} µm"
JUDGE_NAME_LEGACY_FMT = "구형 {sub} · 유사도 임계치"
JUDGE_VALUE_LEGACY_FMT = "{th:.0f} %"
# ★ 배치에 종속된 안내를 하지 않는다 — "아래 '매칭 설정' 카드" 라고 쓰면 C안처럼
#   상세를 접어 두는 배치에서는 화면에 없는 것을 가리킨다.
MODE_BADGE_TOOLTIP = (
    "지금 이 설정으로 매칭합니다.  ‘매칭 설정’ 에서 바꿀 수 있습니다."
)
COORD_NO_DATA_MSG = (
    "좌표 정보가 없습니다.\n\n"
    "이미지 파일명 또는 폴더에 좌표 데이터(Camtek LIVE / INI / KLA .001)가\n"
    "없어 좌표 매칭을 진행할 수 없습니다.\n\n"
    "매칭 설정에서 ‘유사도 엔진(구형) 사용’ 을 켠 뒤 다시 시작하세요."
)
SCORE_DIST_FMT = "{dist:.0f} µm"
SCORE_DIST_OVER_FMT = "{dist:.0f} µm (허용범위 초과)"
# 구형(유사도) 엔진의 점수 표기 — 좌표 모드의 µm 거리에 대응.  같은 포맷이 네 곳
# (매치 검토 행·차순위 타일·매칭 목록·실패 검토)에 흩어져 갈라져 있었다.
SCORE_SIMILARITY_FMT = "{pct:.1f} %"
ENGINE_MODE_BASIC = "기본 모드"
ENGINE_MODE_EFFICIENCY = "고효율 모드 (사진이 많을 때 권장)"
COORD_TOLERANCE_LABEL = "허용 오차"
# 허용 오차 ± 버튼 — Qt 기본 스핀 버튼(잘린 막대·10px 타깃) 대체.
TOL_STEP_UP = "허용 오차 50 µm 늘리기"
TOL_STEP_DOWN = "허용 오차 50 µm 줄이기"
COORD_TOLERANCE_TOOLTIP = (
    "좌표 매칭 모드에서 두 defect 좌표의 최대 허용 거리 (µm).\n"
    "이 값 이하면 동일 defect 으로 간주합니다.\n"
    "기본값 200 µm 은 한 die 크기가 40 mm 안팎일 때 약 0.5% 수준입니다.\n"
    "die 가 작은 자재에서는 그에 맞춰 낮춰 주세요."
)
# 기준 폴더에서 읽어낸 die 크기 안내 — 허용 오차를 자재에 맞게 정하도록 돕는다.
# 값을 대신 바꾸지는 않는다(기존 결과가 조용히 달라지면 안 된다).
DIE_SIZE_DETECTED_FMT = "감지된 die: {x:,.0f} × {y:,.0f} µm  ({src})"
DIE_SIZE_SRC_KLA = "KLA .001 DiePitch"
# 폴더를 훑는 동안 보여 줄 문구.  ★ 빈 줄로 두지 않는다 — 이 스캔은 자재에 따라
# 1초를 넘고(슬롯 25 × 결함 3,000 실측 1.4초), 아무 표시도 없으면 '안내가 원래
# 없는 폴더' 와 구분되지 않는다.  진행을 모를 때도 멈춰 보이게 두지 않는다는
# 로딩바 규칙(CLAUDE.md)과 같은 취지다.
DIE_SIZE_CHECKING = "die 크기 확인 중…"
# ⚠ **Camtek INI 좌표가 있는데 die pitch 를 못 정한 경우에만** 띄운다.
# KLA 슬롯·LIVE 파일명 슬롯은 die 크기 없이도 좌표가 나오므로 경고 대상이 아니다.
# 매칭 자체는 절대 wafer 좌표로 정상 동작한다 — 못 하는 건 die 단위 표기뿐이다.
DIE_SIZE_NOT_FOUND = (
    "die 크기를 찾지 못해 **절대 wafer 좌표**로 매칭합니다 (매칭은 정상).\n"
    "die 내부 좌표·row 표기를 보려면 결과 폴더에 "
    "Params_WaferInfo.ini(DieStep_X/Y) 또는 ProductInfo.ini(XDieIndex/YDieIndex) 가 필요합니다."
)
ENGINE_EFFICIENCY_CPU_ONLY = (
    "가속 장치(Intel GPU)가 없어 CPU만으로 고효율 모드를 실행합니다."
)
ACCEL_UNITS_FMT = "가속: {units}"
SIZE_TIER_NOTICE_FMT = (
    "사진이 많아 썸네일 화질을 자동 조정했습니다 ({thumb}px / Q{q})"
)

# ── 상태 바: 메모리 / 진행 ────────────────────────────────────────────────
MEMORY_USAGE_FMT = "메모리 사용량: {mb} MB"
MEMORY_PRESSURE_TOAST = "메모리 사용량이 높아 캐시를 정리했습니다"

# ※ 상태 바의 CPU/GPU 사용량 문구(USAGE_*)와 'Intel GPU 가속' 디바이스 표시는 제거했다.
#   상태바는 사용자가 행동을 바꿀 수 있는 정보만 담는다 — 메모리 압박은 '슬롯을 나눠
#   돌리라'는 행동으로 이어지지만, GPU 가동 여부로 사용자가 할 수 있는 일은 없었다.

# ── Stage 2 더 크게 보기 ───────────────────────────────────────────────────
EXPAND_VIEW_TOOLTIP = "이 사진을 크게 보기 (←/→ 이전·다음, Enter 매칭, Esc 돌아가기)"
BTN_CONFIRM_AS_MATCH = "매치"            # 확대 보기 — 단순화 (#2)
BTN_BACK_TO_GRID = "돌아가기"            # 확대 보기 — 화살표 제거 (#2)
BTN_EXPAND_PREV = "◀ 이전"
BTN_EXPAND_NEXT = "다음 ▶"
EXPAND_POSITION_FMT = "{cur} / {total}"

# ── 셋업 화면 사용 설명 ────────────────────────────────────────────────────
SETUP_HOW_TO_USE_TITLE = "사용 방법"
SETUP_HOW_TO_USE_BODY = (
    "① 기준·검증 장비의 폴더와 호기 번호를 입력합니다\n"
    "② 자동화 수준을 선택합니다  ·  사진 직접 선택 / 모두 자동\n"
    "③ 허용 오차를 설정합니다  (기본 200 µm)\n"
    "      ※ 좌표 데이터가 없을 때만 ‘유사도 엔진(구형) 사용’ 스위치를 켭니다\n"
    "          (켜면 판정 기준이 허용 오차에서 유사도 임계치로 바뀝니다)\n"
    "④ [검증 시작] 을 누르면 다음 순서로 진행됩니다\n"
    "      ㄱ. 후보 선별 — 사진 직접 선택 시 기준 사진을 한 장씩 [✕ 제외] / [✓ 검증]\n"
    "          (‘모두 자동’ 은 이 단계를 건너뜁니다)\n"
    "      ㄴ. 매칭 — 상단 ‘판정 기준’ 배지의 기준으로 자동 매치 후 ‘매치 검토’에서 확인·교체\n"
    "      ㄷ. 결과 저장 — 양식 폴더의 양식.xlsx 를 복사하여 자동 저장\n"
    "매치 검토에서 ‘크게 보기’로 기준·후보를 나란히 비교(←/→ 이동)\n"
    # ★ 2단계(좌표 매칭) 단축키는 적지 않는다.  자동화 수준 두 가지가 **둘 다 자동
    #   매치**라(utils/prefs.py AUTO_MODES) 그 화면에서 사람이 누를 것이 없다.  예전에는
    #   'N = 매칭 없음' 을 안내해 두었는데, 그 화면이 보이는 유일한 순간은 차단 오버레이가
    #   덮고 있는 자동 매치 중이라 — 안내를 따라 누르면 **처리 중이던 사진이 조용히
    #   '매치 없음' 으로 확정돼 엑셀에 사실이 아닌 미탐이 남았다.**  화면에 지키지 못할
    #   약속을 적어 두지 않는다.
    "단축키 — 후보 선별: → = 검증,  ← = 제외,  Z = 되돌리기"
)

# ── 양식 파일 / 저장 파일 명명 ─────────────────────────────────────────────
RESULT_FILE_TITLE_FMT = "AOI {val} 검증 ({ref} 기준).xlsx"
TEMPLATE_NOT_FOUND_TITLE = "양식 파일 없음"
TEMPLATE_NOT_FOUND_BODY = (
    "‘양식’ 폴더 안의 ‘양식.xlsx’ 를 찾을 수 없습니다.\n"
    "기본 양식으로 결과를 생성합니다.\n\n"
    "확인한 경로: {path}"
)
WORKING_FILE_LABEL = "결과 파일 위치"

# ── 로딩/진행 ──────────────────────────────────────────────────────────────
# ★ 로딩 문구에 진행 수치({done}/{total})를 넣지 마라.  진행 수치는 진행바 **아래
#   모노 라벨**(LoadingOverlay._count_label)이 전담한다 — 문구에도 넣으면 같은 숫자가
#   한 화면에 두 번 보인다("썸네일 생성 중 147 / 480" + "147 / 480").  단일 출처 규칙.
LOAD_THUMBNAIL = "썸네일 생성 중…"
LOAD_STAGE_PREP = "다음 단계 준비 중…"
# 검토 화면 진입 — 행을 나눠 만드는 동안(수백 건이면 몇 초).
LOAD_REVIEW_ROWS = "검토 목록 준비 중…"
LOAD_FEATURE = "검증 장비 특징 추출 중…"
LOAD_SCORING = "유사도 계산 중…"
PHASE_FEATURE = "이미지 특징 분석"
PHASE_SCORING = "유사도 계산"
PHASE_EMBED = "후보 생성 (GPU 임베딩)"      # 고효율 모드 1단계 — 유사도 계산 직전
PHASE_COORD = "좌표 매칭 중"                # 좌표 기반 매칭 v2
PHASE_COORD_PARSE = "좌표 파싱 중…"         # 좌표 매칭 1단계
# 수동 모드: 첫 슬롯만 기다리고 나머지는 백그라운드 (#streaming).
# 선행 단계(특징 분석/임베딩) 동안에도 '유사도 계산' 으로 오인되지 않도록 중립 문구.
LOAD_PRECOMPUTE_FIRST_SLOT = (
    "첫 슬롯 준비 중… 잠시만 기다려 주세요."
)
LOAD_PRECOMPUTE_WAIT_FMT = (
    "{slot} 슬롯 유사도 계산을 기다리는 중… 다음 슬롯은 백그라운드에서 준비됩니다"
)
PRECOMPUTE_BG_STATUS_FMT = "백그라운드 유사도 계산: {idx} / {total} 슬롯 완료"
PRECOMPUTE_BG_DONE = "유사도 계산 완료"

# ── 자동 업데이트 ─────────────────────────────────────────────────────────
UPDATE_AVAILABLE_TITLE = "업데이트 있음"
UPDATE_AVAILABLE_BODY = "새 버전이 있습니다. 지금 업데이트할까요?"
UPDATE_UNKNOWN_CURRENT = "최신 버전을 받아 적용할까요?"
UPDATE_DOWNLOADING = "업데이트 다운로드 중…"
UPDATE_DONE_RESTART = "업데이트가 적용되었습니다.\n프로그램을 종료합니다. 다시 실행해 주세요."
# exe 배포본은 실행 중인 app 폴더를 바꿀 수 없어 새 버전을 받아두기만 하고, 다음 실행 때
# 런처가 교체한다.  '적용되었다' 고 하면 사실과 다르므로 문구를 나눈다.
UPDATE_DONE_RESTART_STAGED = (
    "새 버전을 받았습니다.\n프로그램을 종료합니다. 다시 실행하면 적용됩니다."
)
# 이번 업데이트로 필요한 패키지 목록(requirements.txt)이 바뀐 경우의 추가 안내.
# 자동 업데이트는 앱 소스만 바꾸고 **의존성은 다시 설치하지 않는다**(번들 런타임 보존).
UPDATE_DEPS_CHANGED = (
    "\n\n[중요] 이번 업데이트로 필요한 패키지 목록이 바뀌었습니다. 다음 실행 전에 "
    "의존성을 갱신해 주세요:\n"
    " · 포터블: 앱 폴더의 python\\python.exe -m pip install -r requirements.txt 실행"
    "(또는 최신 포터블 빌드 사용)\n"
    " · 개발/소스: git pull 후 scripts\\run_this_before.py 를 다시 실행"
)
# exe 배포본에서 새 패키지 **설치가 실패해** 적용을 포기한 경우.
# 코드만 바꾸면 앱이 안 켜지므로 구버전을 그대로 유지하고, 무엇을 받아야 하는지 알려준다.
UPDATE_NEEDS_NEW_BUNDLE = (
    "이번 업데이트에 필요한 패키지를 설치하지 못해 적용하지 않았습니다.\n"
    "지금 버전은 그대로 사용하실 수 있습니다.\n\n"
    "인터넷 연결을 확인한 뒤 다시 시도해 주세요.\n"
    "계속 실패하면 관리자에게 새 배포본 'AOI_Verify' 폴더 전체(zip)를 요청하세요.\n"
    "app 폴더만 바꾸면 앱이 실행되지 않습니다."
)
UPDATE_FAILED = "업데이트에 실패했습니다. 잠시 후 다시 시도해 주세요."
UPDATE_CHECKING = "업데이트 확인 중…"
UPDATE_LATEST = "최신 버전입니다."
UPDATE_UNKNOWN = "업데이트를 확인할 수 없습니다. 인터넷 연결을 확인해 주세요."
UPDATE_GIT_HINT = "개발(git) 환경입니다. 'git pull' 로 업데이트하세요."
# 첫 화면 '업데이트 확인' 버튼 라벨(좌상단 도움말 메뉴 대체).
MENU_CHECK_UPDATE = "업데이트 확인"
LOAD_AUTO_MATCH = "자동 매치 진행 중…"

# ── 자동화 수준 (#3 올인원 모드) ───────────────────────────────────────────
AUTOMATION_TITLE = "자동화 수준"
AUTOMATION_USER_SELECT = "사진 직접 선택 + 매치는 자동"
AUTOMATION_AUTO_ALL = "모든 사진 자동 — 후보 선별 건너뛰기"
# 세그먼트에 들어가는 짧은 라벨.  긴 설명은 위 두 문구가 툴팁으로 맡는다 —
# 34px 세그먼트에 문장을 넣으면 '작게'라는 의도가 폭으로 되돌아온다.
AUTOMATION_USER_SELECT_SHORT = "사진 직접 선택"
AUTOMATION_AUTO_ALL_SHORT = "모든 사진 자동"
AUTO_REVIEW_HINT_FMT = (
    "자동 매치 완료 — 총 {n_match} 쌍이 자동으로 매치되었고,\n"
    "{n_miss} 장은 임계치 미달로 ‘매칭 없음’ 처리되었습니다.\n"
    "[매칭 결과 검토] 로 결과를 확인해 주세요."
)
# ── 매치 검토 페이지 ───────────────────────────────────────────────────────
BTN_MARK_NO_MATCH = "매치 없음 ✕"
BTN_RESTORE_MATCH = "되돌리기 ↩"
BTN_FINISH_REVIEW = "검토 완료 ▶"
RUNNERUP_TOOLTIP = "클릭하면 이 사진으로 매치를 교체합니다."
# A2 밀집 리스트 — 상단 집계 바 / 판정 칩 / 컴팩트 토글 (#A2)
TALLY_OK_FMT = "일치 {n}"
TALLY_OVER_FMT = "허용 초과 {n}"
TALLY_NO_MATCH_FMT = "매치 없음 {n}"
TALLY_COORD_FAILED_FMT = "매치 실패 {n}"
BTN_FINISH_REVIEW_KEPT_FMT = "검토 완료 · 유지 {n}"   # ⏎ 제거 — Enter 단축키가 없어졌다
CHIP_OVER = "허용 초과"
CHIP_NO_MATCH = "매치 없음"
BTN_NO_MATCH_COMPACT = "×"
BTN_RESTORE_COMPACT = "↩"
REVIEW_EMPTY_HINT = (
    # ★ 화면의 버튼 이름과 같게 적는다 — 예전엔 [완료] 라고 안내했지만 실제 버튼은
    #   '검토 완료 · 유지 n' 이라, 있지도 않은 버튼을 찾게 만들었다.
    "자동 매치된 항목이 없습니다.  [검토 완료] 를 누르면 결과 화면으로 이동합니다."
)
# 검토 리스트 컬럼 헤더 — ★ 이미지 세 종류를 **각자 자기 사진 위**에 놓기 위해
# 하나였던 'COL_IMAGES = 기준 · 검증 · 후보' 를 셋으로 쪼갰다(한 라벨이면 어느 것도
# 자기 사진 위에 없다).  폭 정렬은 match_review_page._sync_header_widths 가 맞춘다.
COL_SLOT = "슬롯"
COL_REF = "기준"
COL_VAL = "검증"
COL_CANDIDATES = "후보"
COL_DISTANCE = "거리(µm)"
# 구형(유사도) 엔진에서는 같은 열이 거리가 아니라 유사도를 보여준다.
COL_SIMILARITY = "유사도(%)"
COL_VERDICT = "판정"
COMPARE_REF_CAPTION_FMT = "기준 — {name}"
# 비교 뷰어 상단 조작 힌트.  확대/이동은 **마우스가 올라간 쪽 사진에만** 적용된다
# (사용자 결정) — 기준을 그대로 두고 후보만 파고들어 볼 수 있다.
COMPARE_HINT = "← → 후보 이동 · 휠 확대/축소 · 드래그 이동 (사진마다 따로)"
# ※ 검토 화면의 키보드 상호작용(↑↓ 줄 이동 · R 매치 없음 · Enter 검토 완료)과 키캡 힌트
#   줄, '확인 필요만' 필터는 제거했다.  '매치 없음'은 행마다 있는 ✕ 토글, '검토 완료'는
#   상단 버튼이 한다.  Enter 가 검토 전체를 끝내던 것은 특히 위험했다.
# 카드 제목 옆 '?' 도움말 토글 (하드코딩 금지 — 사용자 노출 문자열은 여기 모은다)
HELP_TOGGLE_TOOLTIP = "설명 보기/숨기기"

# ※ 배치안 스위처(LAYOUT_*)와 C안 요약(SETUP_DETAIL_*·SUMMARY_*) 문구는 제거했다 —
#   사용자가 '순서형' 배치를 확정하면서 비교용 스위처와 미선택 2안이 함께 사라졌다.
#   지금 판정 기준을 말하는 것은 상단 모드 배지(judgement_text) 하나다.

# 보기 옵션 (셋업 상단)
# ★ 어두운 모드가 하나로 정리돼 **on/off 스위치**가 됐다.  한때 '벨럼 · 청사진 · 흑연'
#   3택이었고, 모드가 둘이라 boolean 으로 못 쓴다는 이유로 3칩 선택기를 뒀다.  청사진을
#   지우면서 그 이유가 사라졌으므로 '모션 줄이기' 와 같은 어휘(스위치)로 통일한다.
DARK_MODE_LABEL = "다크 모드"
DARK_MODE_TOOLTIP = (
    "불 끈 제도지에 흑연 선 — 무채·눈부심 최소(야간·어두운 작업장).\n"
    "끄면 밝은 벨럼 시트로 돌아갑니다.\n"
    "검증을 시작하기 전에만 바꿀 수 있습니다(진행 중 상태 보호)."
)
# ※ '모션 줄이기' 토글은 제거했다 — 모션은 항상 켜진다(사용자 결정).
LOAD_SCAN = "폴더 스캔 중…"
LOAD_EXPORT = "엑셀로 저장 중…"

# ── 경고/안내 모달 ─────────────────────────────────────────────────────────
WARN_SAME_PATH_TITLE = "경로 확인"
WARN_SAME_PATH_BODY = (
    "기준 장비와 검증 장비의 경로가 동일합니다.\n"
    "정말로 같은 폴더를 비교하시겠습니까?"
)
WARN_PATH_NOT_EXIST = "선택한 경로가 존재하지 않습니다:\n{path}"
WARN_NO_SLOTS = "두 폴더에 공통된 Slot 이 존재하지 않습니다."
WARN_NO_IMAGES = "선택된 Slot 에 이미지가 없습니다."
WARN_SLOT_MISMATCH_TITLE = "Slot 불일치"
WARN_SLOT_MISMATCH_FMT = (
    "한쪽에만 존재하는 Slot 이 있습니다.\n"
    "사용자가 직접 슬롯 매핑을 정해주실 수 있습니다.\n\n"
    "기준 전용: {ref_only}\n검증 전용: {val_only}"
)
SLOT_MAP_TITLE = "Slot 수동 매핑"
SLOT_MAP_HINT = (
    "남은 슬롯을 직접 짝지어 주세요. 양쪽에서 하나씩 골라 ‘묶기’, 다시 누르면 해제."
)
SLOT_MAP_REF_LABEL = "기준 (남은 슬롯)"
SLOT_MAP_VAL_LABEL = "검증 (남은 슬롯)"
SLOT_MAP_ADD = "묶기 ↔"
SLOT_MAP_REMOVE = "선택 해제"
SLOT_MAP_PAIRS_LABEL = "묶은 쌍"
SLOT_MAP_OPEN = "매핑 다이얼로그 열기"
# 확인창 본문용 완결 문장 — 라벨에 " ?" 를 붙여 만들던 비문을 대체한다.
SLOT_MAP_ASK = "슬롯을 직접 짝지어 주시겠습니까?"

# ── 일부 슬롯만 진행 (Setup) ───────────────────────────────────────────────
SLOT_SELECT_BTN_TOOLTIP = (
    "기준 폴더의 슬롯 중 이번 검증에서 진행할 슬롯만 선택합니다.\n"
    "선택하지 않으면 모든 공통 슬롯을 진행합니다."
)
SLOT_SELECT_TITLE = "진행할 슬롯 선택"
# 다이얼로그 상단 — 지금 무엇이 진행되는지 **큰 글자로** 말한다(이전엔 '선택 3 / 24' 뿐).
SLOT_SELECT_COUNT_HEAD_FMT = "{total}개 중 {n}개 진행"
SLOT_SELECT_COUNT_HEAD_ALL = "모든 슬롯 진행 ({total}개)"
SLOT_SELECT_SEARCH_PLACEHOLDER = "슬롯 이름으로 찾기"
SLOT_SELECT_FILTER_HIT_FMT = "‘{q}’ 검색 결과 {n}개"
SLOT_SELECT_FILTER_NONE_FMT = "‘{q}’ 와 맞는 슬롯이 없습니다"
SLOT_SELECT_PICK_VISIBLE = "검색 결과 선택"
# 0개로 확인하면 호출부가 '전체 진행'으로 정규화한다 — 의도와 정반대라 막고 이유를 말한다.
SLOT_SELECT_NEED_ONE = "최소 한 개는 선택해야 합니다."
# ※ SLOT_SELECT_KEY_HINT 는 제거했다 — 타일 키보드 조작을 없앴으므로(사용자 결정)
#   안내할 키가 남지 않았다.  화면에 지키지 못할 약속을 적어 두지 않는다.
SLOT_SELECT_HINT = (
    "이번 검증에서 진행할 슬롯을 눌러 고르세요. 이름을 알면 위에서 바로 찾을 수 있습니다.\n"
    "고르지 않은 슬롯은 스캔·매칭에서 제외됩니다."
)
SLOT_SELECT_ALL = "전체 선택"
SLOT_SELECT_NONE = "전체 해제"
# 진행 범위 타일 — 상태를 옆 라벨이 아니라 타일 자신이 말한다.
# ── 실행 옵션 카드 — '자동화 수준' + '진행 범위' 를 한 카드에 담는다.
RUN_OPTIONS_TITLE = "실행 옵션"
RUN_OPTIONS_HINT = (
    "· 자동화 수준 — ‘모든 사진 자동’은 후보 선별(Stage 1)을 건너뛰고 모든 기준 사진을"
    " 자동으로 매치합니다. 종료 후 결과 화면에서 [매칭 결과 검토]로 잘못된 매치를"
    " 제거할 수 있습니다.\n"
    "· 진행 범위 — 기본은 기준 폴더의 모든 슬롯입니다. ‘일부 슬롯만’ 을 고르면 이번"
    " 검증에서 진행할 슬롯을 직접 선택합니다(급한 슬롯만 먼저 돌릴 때)."
)
SCOPE_TITLE = "진행 범위"
SCOPE_ALL = "모든 슬롯"
SCOPE_SUBSET = "일부 슬롯만…"
SCOPE_SUBSET_COUNT_FMT = "일부 슬롯 ({n}/{total})"
# 입력 유효성 — 왜 [검증 시작] 을 누를 수 없는지 눈에 보이게.
SETUP_INVALID_FOLDER = "폴더를 찾을 수 없습니다."
START_BLOCKED_HINT = "기준·검증 폴더를 먼저 지정하세요."
# 폴더는 다 골랐는데 아직 구성 요소를 불러오는 중일 때 — 곧 저절로 풀린다.
START_PREPARING_HINT = "구성 요소를 불러오는 중입니다. 잠시만 기다려 주세요."
# 그 불러오기가 실패한 경우(설치 손상 등) — 시작 버튼은 잠긴 채로 둔다.
BACKEND_LOAD_FAILED_FMT = (
    "영상 처리 구성 요소를 불러오지 못했습니다.\n"
    "프로그램을 다시 설치하거나 관리자에게 문의하세요.\n\n{err}")
SLOT_SELECT_NEED_REF = "먼저 기준 폴더를 선택하세요."
SLOT_SELECT_EMPTY = "기준 폴더에서 슬롯(하위 폴더)을 찾지 못했습니다."

# ── 기준 사진 재사용 (#6) ──────────────────────────────────────────────────
REF_REUSE_TITLE = "이전 기준 사진 재사용"
REF_REUSE_BODY_FMT = (
    "이 기준 폴더로 이전에 매치를 진행한 기록이 있습니다.\n"
    "그때 직접 고른 기준 사진 {n} 장을 그대로 사용하시겠습니까?\n\n"
    "예 — 해당 사진들을 자동으로 ‘검증 대상’ 으로 옮긴 상태에서 시작합니다.\n"
    "아니오 — 처음부터 직접 고릅니다."
)
# KLA slot 해석 단계 — 로딩창에 현재 진행 단계(정보파일/OCR)를 실시간 표시.
LOAD_KLA_INFO = "KLA slot 매칭 중 — 정보파일 분석…"
LOAD_KLA_OCR = "KLA WaferID 판독 (OCR) 중…"

# slot 매칭 실패 시 'KLA 가 어느 쪽?' 확인 — 호기가 K-n 이면 자동, 아니면 묻는다.
KLA_ASK_TITLE = "KLA 장비 확인"
# 팝업 최상단에 크게·색상으로 강조되는 핵심 질문(#2).
KLA_ASK_SIDE_HEADING = "KLA 장비가 어느 쪽인가요?"
KLA_ASK_SIDE_BODY = "KLA(WaferID) 장비 위치를 선택하세요. 없으면 ‘KLA 아님’."
KLA_SIDE_REF = "기준"
KLA_SIDE_VAL = "검증"
KLA_SIDE_BOTH = "둘다"
KLA_SIDE_NONE = "KLA 아님"

INFO_RESUME_TITLE = "이전 검증 설정 복원"
# ★ 문구를 사실대로 적는다.  '이어하기' 라고 했지만 실제로 복원되는 것은 폴더·호기·
#   임계치 같은 **입력값뿐**이고, 선별 결정은 처음부터 다시 한다.  다만 직접 고른
#   기준 사진은 기록해 두므로 선별 화면에서 '재사용할까요?' 로 되돌려 받을 수 있다.
INFO_RESUME_BODY = (
    "마치지 못한 검증이 있습니다.  이전 입력값(폴더·호기·판정 기준)을 복원해 "
    "처음부터 다시 시작할까요?\n\n"
    "선별은 다시 하게 되지만, 직접 고르셨던 기준 사진은 선별 화면에서 "
    "재사용할지 물어봅니다."
)
INFO_PHASE_TRANSITION_TITLE = "단계 전환"
INFO_PHASE_A_TO_MATCH = "후보 선별이 끝났습니다. 매칭으로 넘어갑니다."
INFO_NO_MATCH_FOUND = "임계치 이상인 후보가 없습니다. 자동으로 Skip 처리됩니다."
INFO_ALREADY_MATCHED_SECTION = "이미 매칭됨 (자동 제외)"

# ── 저장/엑셀 ──────────────────────────────────────────────────────────────
SAVE_DIALOG_TITLE = "결과 엑셀 저장 위치 선택"
SAVE_FILENAME_FMT = "AOI검증결과_{ref}_vs_{val}_{ts}.xlsx"
SAVE_SUCCESS_FMT = "엑셀 저장 완료:\n{path}"
SAVE_FAIL_FMT = "엑셀 저장 실패:\n{error}"
# ★ 가장 흔한 실패는 결과 파일이 엑셀에서 열려 있는 경우다.  OS 원문만 보여 주면
#   무엇을 해야 할지 알 수 없으므로 **다음 행동을 먼저** 말한다.
SAVE_FAIL_LOCKED_FMT = (
    "결과 파일에 쓸 수 없습니다.\n\n"
    "그 파일이 엑셀에서 열려 있으면 닫은 뒤 다시 시도해 주세요.\n"
    "(다른 프로그램이 쓰고 있거나 폴더 권한이 없을 때도 같은 오류가 납니다.)\n\n"
    "원문: {error}"
)
SLOT_MISMATCH_SHEET = "Slot 불일치 목록"
SHEET_UNMATCHED = "미매칭 사진"
# 미매칭 행 D열 — 결함 geometry(area/width/length/contrast) 표기.  Surface.flt 에서
# 환산해 파일명 아래 회색 글씨로 덧붙인다.  값을 못 얻을 때의 명시적 마커:
GEOM_NOT_SUPPORTED = "측정정보 미지원 자재"   # Surface.flt 자체가 없는 자재(예: KLA)
GEOM_NO_DATA = "측정정보 없음"               # Surface.flt 는 있으나 매칭 실패
# KLA 결함 — 변환값(Camtek 좌표계) 아래에 KLA 자체 원본 좌표(XREL/YREL)도 표기.
# ★ 줄바꿈은 붙이지 않는다 — 줄을 잇는 쪽(엑셀 exporter)이 "\n" 을 앞에 붙인다.
EXPORT_KLA_NATIVE_FMT = "KLA원본 x {x:.0f} / y {y:.0f} ㎛"
# 결과 화면 — 전체 양식(E~H 수기 영역) 포함 옵션 (기본 해제, #3).
EXPORT_FULL_TEMPLATE_LABEL = "전체 양식(E~H 수기 영역) 포함"
EXPORT_FULL_TEMPLATE_TOOLTIP = (
    "체크하면 E~H 수기 입력 영역을 포함한 ‘전체 양식’ 시트도 함께 저장합니다.\n"
    "기본(해제)은 요약·미매칭 시트만 생성해 더 빠르고 가벼운 파일이 됩니다."
)
# 결과 화면 — 미매칭 사진만 원본 화질로 임베드 옵션 (기본 해제).
EXPORT_UNMATCHED_ORIGINAL_LABEL = "미매칭 사진만 원본 화질로 넣기"
EXPORT_UNMATCHED_ORIGINAL_TOOLTIP = (
    "체크하면 미매칭 사진만 원본 화질 그대로 임베드합니다.\n"
    "매칭된 사진은 중간 화질 캐시를 그대로 써서 파일이 크게 늘지 않습니다.\n"
    "‘사진을 원본 화질로 넣기’ 를 켜면 이 옵션과 무관하게 모든 사진이 원본 화질입니다."
)
# 결과 화면 — 사진을 원본 화질로 임베드 옵션 (기본 해제).
EXPORT_ORIGINAL_QUALITY_LABEL = "사진을 원본 화질로 넣기"
EXPORT_ORIGINAL_QUALITY_TOOLTIP = (
    "체크하면 셀에 들어가는 사진을 줄이지 않고 원본 화질 그대로 임베드합니다.\n"
    "기본(해제)은 중간 화질 캐시를 사용해 더 빠르고 가벼운 파일이 됩니다.\n"
    "원본은 화질이 좋지만 파일 용량이 크게 늘고 저장이 느려질 수 있습니다."
)

# ── 단일 사진 정보 ─────────────────────────────────────────────────────────
# 사진 한 장에서 뽑은 결함 계측.  `coords.single_info` 가 유일한 생산자이고 두 화면이
# **같은 수치**를 쓴다 — 엑셀 미매칭 행(D열)은 `_FMT`(한 줄), 정보 화면은
# `_LABEL`/`_VALUE_FMT`(제도 시트 표의 2열).  셋은 한 자리에서 같은 값으로 채워진다.
DEFECT_RECIPE_ZONE_LABEL = "recipe / zone"
DEFECT_RECIPE_ZONE_VALUE_FMT = "{recipe} / {zone}"
DEFECT_RECIPE_ZONE_FMT = "recipe {recipe} / zone {zone}"
DEFECT_AREA_LABEL = "area"
DEFECT_AREA_VALUE_FMT = "{v:.2f} ㎛²"
DEFECT_WIDTH_LABEL = "width"
DEFECT_WIDTH_VALUE_FMT = "{v:.2f} ㎛"
DEFECT_LENGTH_LABEL = "length"
DEFECT_LENGTH_VALUE_FMT = "{v:.2f} ㎛"
DEFECT_CONTRAST_LABEL = "contrast"
DEFECT_CONTRAST_VALUE_FMT = "{v:.2f}"
# contrast 는 일부 자재(예: PI)만 측정 — 0 은 ‘측정 안 함’이라 0.00 대신 ‘—’.
DEFECT_CONTRAST_NONE_VALUE = "—"
DEFECT_COLROW_LABEL = "col / row"
DEFECT_COLROW_VALUE_FMT = "{col} / {row}"
DEFECT_COLROW_FMT = "col {col} / row {row}"
DEFECT_XY_LABEL = "x / y"
DEFECT_XY_VALUE_FMT = "{x:.0f} / {y:.0f} ㎛"
DEFECT_XY_FMT = "x {x:.0f} / y {y:.0f} ㎛"
DEFECT_KLA_NATIVE_LABEL = "KLA원본 x / y"
# die pitch 를 못 찾아 die 로 쪼개지 못한 경우(source="camtek_abs") — 있는 사실만 적는다.
# col 은 pitch 없이도 정확하지만 row 기준·die 내부 좌표는 알 수 없으므로 꾸며내지 않는다.
DEFECT_COL_ONLY_LABEL = "col"
DEFECT_COL_ONLY_FMT = "col {col} (row 미상 — die 크기 정보 없음)"
DEFECT_ABS_XY_LABEL = "wafer x / y"
DEFECT_ABS_XY_FMT = "wafer x {x:.0f} / y {y:.0f} ㎛"

# 화면 전용 라벨 — 파일 하나에서만 의미 있는 항목(엑셀에는 나가지 않는다).
IMAGE_INFO_GROUP_FILE = "파일"
IMAGE_INFO_GROUP_DEFECT = "결함 계측"
IMAGE_INFO_COL_VALUE = "값"
IMAGE_INFO_ROW_FILE = "파일명"
IMAGE_INFO_ROW_FOLDER = "폴더"
IMAGE_INFO_ROW_WAFER_ID = "WaferID"
IMAGE_INFO_ROW_SOURCE = "좌표 출처"
IMAGE_INFO_ROW_ABS_XY = "절대 X / Y"
IMAGE_INFO_ROW_PIXEL = "픽셀 크기"
IMAGE_INFO_ABS_XY_FMT = "{x:.0f} / {y:.0f} ㎛"
IMAGE_INFO_PIXEL_FMT = "{v:.3f} ㎛/px"
# 판정 스탬프 — 측정정보 유무를 한눈에.  미지원/없음은 GEOM_* 를 그대로 쓴다.
IMAGE_INFO_STAMP_MEASURED = "측정정보 있음"
# DefectCoord.source 코드 → 사람이 읽는 이름.
IMAGE_INFO_SOURCE_NAMES = {
    "camtek_live": "Camtek LIVE 파일명",
    "camtek_ini": "Camtek 정보파일(INI)",
    "camtek_abs": "Camtek 정보파일(INI) — 절대 wafer 좌표",
    "kla": "KLA 정보파일",
}

# 다이얼로그.
IMAGE_INFO_BUTTON = "사진 정보 보기"
IMAGE_INFO_TITLE = "단일 사진 정보"
IMAGE_INFO_SUBTITLE = "결과 엑셀에 찍히는 것과 같은 좌표·계측값을 사진 한 장에서 바로 읽습니다"
IMAGE_INFO_PICK = "사진 선택"
IMAGE_INFO_PICK_ANOTHER = "다른 사진"
IMAGE_INFO_FILE_FILTER_FMT = "사진 파일 ({patterns})"
IMAGE_INFO_DROP_HERE = "여기에 놓으세요"
IMAGE_INFO_NO_FILE = (
    "사진을 이 창으로 끌어다 놓거나, ‘사진 선택’(Ctrl+O) 을 누르세요.\n\n"
    "계측값은 사진이 든 폴더의 정보파일에서 읽습니다 — "
    "ColorImageGrabingInfo.ini(Camtek) · KLA 정보파일 · Surface.flt.\n"
    "사진만 따로 복사한 폴더에서는 일부 항목이 나오지 않습니다."
)
IMAGE_INFO_EMPTY = (
    "이 사진에서 읽을 수 있는 결함 정보가 없습니다.\n"
    "폴더에 정보파일(ColorImageGrabingInfo.ini / KLA 정보파일 / Surface.flt)이 "
    "있는지 확인하세요."
)
IMAGE_INFO_COPY = "전체 복사"
IMAGE_INFO_COPIED = "복사했습니다"

# ── 일반 상태 표시 ─────────────────────────────────────────────────────────
COUNT_PLUS_N_FMT = "+{n}"
PROGRESS_SLOT_FMT = "{slot}  ·  {done} / {total}"
GROUP_HEADER_FMT = "{slot}  ·  {count} 장"
# 후보 선별 상단의 Slot 별 전체 장수 — 참고용 (#2).
LOT_COUNTS_PREFIX = "Slot별 장수:  "

# ── UI 개선 (#11 / #13 / #16) ─────────────────────────────────────────────
# 썸네일 우클릭 컨텍스트 메뉴 — 원본 크게 보기 (#13).
CTX_VIEW_LARGER = "크게보기"
# 매치 검토 각 행 slot 라벨 아래 ‘크게 보기’ 버튼 — 좌우 비교 뷰어를 연다.
# ★ 확대 글리프(⤢/⧉ 등)를 **붙이지 않는다.**  이 글자들은 폰트 폴백이 플랫폼마다 달라
#   컬러 이모지로 렌더되는 경우가 있다(실측: 이 리눅스 캡처 박스에서 주황·파랑 화살표로
#   나왔다) — 무채색 도면 팔레트를 깨고, 대비 측정까지 오염시킨다.  버튼임은 테두리와
#   라운드가 말한다(role="rowAction").
BTN_VIEW_LARGER = "크게 보기"
# 좌우 비교 뷰어에서 ‘이 후보로 매치’ 액션 버튼 (#4).
BTN_MATCH_THIS = "이 후보로 매치"
# 차순위 후보 ‘후보 한 줄 더 보기’ / ‘접기’ 버튼 (#5/#4).
RUNNERUP_MORE_ROW = "후보 한 줄 더 보기 ▾"
RUNNERUP_LESS_ROW = "접기 ▴"

# ── 앱 내 메시지 시트 버튼 (widgets/sheet_host.py) ─────────────────────────
# 별도 OS 창(QMessageBox)을 대체하므로 표준 버튼 라벨도 여기서 관리한다.
# ※ 확인/취소는 이미 있는 공통 버튼을 그대로 쓴다(같은 말을 두 번 정의하지 않는다).
MSG_BTN_OK = BTN_OK
MSG_BTN_CANCEL = BTN_CANCEL
MSG_BTN_YES = "예"
MSG_BTN_NO = "아니오"
MSG_BTN_CLOSE = "닫기"
