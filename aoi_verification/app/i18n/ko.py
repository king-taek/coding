"""모든 사용자 노출 문자열(한국어)을 한 곳에 모아둔 모듈.

UI/로그/툴팁/오류 메시지 모두 이 모듈을 통해 참조합니다.
번역이나 일괄 수정 시 이 파일만 보면 됩니다.
"""

# ── 앱/메타 ────────────────────────────────────────────────────────────────
APP_TITLE = "AOI 검증"
APP_DEVELOPER = "임현택 (HyunTaek Lim)"
APP_AFFILIATION = "Bump 2 Dept. / AOI Engineer"

# ── 공통 버튼/액션 ─────────────────────────────────────────────────────────
BTN_OK = "확인"
BTN_CANCEL = "취소"
BTN_BACK = "뒤로"
BTN_NEXT = "다음"
BTN_START = "검증 시작"
BTN_BROWSE = "폴더 선택…"
BTN_VERIFY = "검증"
BTN_EXCLUDE = "제외"
BTN_UNDO = "되돌리기"
BTN_SKIP = "건너뛰기"
BTN_RETRY_SKIP = "Skip 재시도"
BTN_SELECT_MODE = "선택 모드"
BTN_CANCEL_SELECT_MODE = "선택 해제"
BTN_REMOVE_FROM_TARGET = "검증 대상에서 제거"
BTN_MOVE_TO_EXCLUDE = "제외로 이동"
BTN_MOVE_TO_TARGET = "검증 대상으로 이동"
BTN_BACK_TO_CENTER = "중앙으로 복귀(재결정)"
BTN_BATCH_EXCLUDE = "일괄 제외"
BTN_BATCH_VERIFY = "일괄 검증으로 이동"
BTN_EXPORT_EXCEL = "엑셀로 저장"
BTN_OPEN_RESULT = "결과 폴더 열기"
BTN_NEW_SESSION = "새 검증 시작"

# ── 셋업 페이지 ────────────────────────────────────────────────────────────
SETUP_TITLE = "AOI 검증 — 시작 설정"
SETUP_MODE_LABEL = "검증 모드"
SETUP_MODE_SINGLE = "한쪽만 검증"
SETUP_MODE_CROSS = "양쪽 교차검증"
SETUP_REF_GROUP = "기준 장비"
SETUP_VAL_GROUP = "검증 장비"
SETUP_FOLDER_LABEL = "최상위 폴더"
SETUP_MACHINE_LABEL = "호기 번호"
SETUP_THRESHOLD_LABEL = "유사도 임계치"
SETUP_FOLDER_PLACEHOLDER = "폴더를 선택해 주세요"
SETUP_MACHINE_PLACEHOLDER = "예) 1호기"
SETUP_HINT = (
    "기준 장비와 검증 장비는 서로 다른 호기의 폴더입니다.\n"
    "두 폴더의 하위 Slot 폴더 이름이 같을 때 매칭됩니다."
)

# ── 검증 단계 헤더 ─────────────────────────────────────────────────────────
STAGE1_TITLE = "Stage 1 — 후보 선별"
STAGE2_TITLE = "Stage 2 — 유사도 기반 매칭"
RESULT_TITLE = "검증 결과"

PANEL_LEFT_CANDIDATES = "검증 후보들 (남은 사진)"
PANEL_CENTER_DECIDE = "검증 결정할 사진"
PANEL_RIGHT_TARGETS = "검증 대상 (검증하기로 한 사진들)"
PANEL_BOTTOM_EXCLUDED = "검증 하지 않을 사진 (제외됨)"

PANEL_SKIP_LIST = "Skip 된 사진들"
PANEL_MATCH_REF = "기준 사진"
PANEL_MATCH_CANDIDATES = "검증 장비 후보"

# ── 줌-뷰 윈도우 ───────────────────────────────────────────────────────────
ZOOM_TITLE_TARGETS = "검증 대상인 사진들 — {slot}"
ZOOM_TITLE_EXCLUDED = "검증 하지 않을 사진 — {slot}"
ZOOM_TITLE_CANDIDATES = "검증 후보 사진들 — {slot}"
ZOOM_BTN_EXCLUDE = "검증에서 제외"
ZOOM_BTN_TO_TARGET = "검증 대상으로 변경"
ZOOM_BTN_TO_CENTER = "재결정으로 복귀"

# ── 단축키 ────────────────────────────────────────────────────────────────
SHORTCUT_TOOLTIP = (
    "단축키:  ← 또는 1 = 검증   /   → 또는 2 = 제외   /   Z = 되돌리기"
)
SHORTCUT_STAGE2_TOOLTIP = "단축키:  S = 건너뛰기"

# ── 로딩/진행 ──────────────────────────────────────────────────────────────
LOAD_THUMBNAIL_FMT = "썸네일 생성 중… {done} / {total}"
LOAD_FEATURE_FMT = "검증 장비 특징 추출 중… {done} / {total}"
LOAD_SCAN = "폴더 스캔 중…"
LOAD_EXPORT = "엑셀로 저장 중…"
LOAD_PRECOMPUTE_REF = "기준 장비 특징 추출 중… {done} / {total}"

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
    "한쪽에만 존재하는 Slot 이 있습니다 — 매칭에서 제외됩니다.\n\n"
    "기준 전용: {ref_only}\n검증 전용: {val_only}"
)
INFO_RESUME_TITLE = "이전 검증 이어하기"
INFO_RESUME_BODY = "진행 중인 검증이 있습니다. 이어서 하시겠습니까?"
INFO_NEW_SESSION = "새로 시작"
INFO_RESUME = "이어서 하기"
INFO_PHASE_TRANSITION_TITLE = "단계 전환"
INFO_PHASE_A_TO_MATCH = "Phase A 후보 선별이 끝났습니다. 매칭으로 넘어갑니다."
INFO_PHASE_A_TO_B = "Phase A 가 끝났습니다. 이어서 Phase B (역방향) 를 시작합니다."
INFO_PHASE_B_TO_MATCH = "Phase B 후보 선별이 끝났습니다. 매칭으로 넘어갑니다."
INFO_ALL_DONE = "모든 검증이 끝났습니다. 결과를 저장해 주세요."
INFO_NO_MATCH_FOUND = "임계치 이상인 후보가 없습니다. 자동으로 Skip 처리됩니다."
INFO_ALREADY_MATCHED_SECTION = "이미 매칭됨 (자동 제외)"

# ── 페이즈 표시 ────────────────────────────────────────────────────────────
PHASE_LABEL_FMT = (
    "Phase A: {a_ref} 기준 선별 → Phase A: 매칭 → "
    "Phase B: {b_ref} 기준 선별 → Phase B: 매칭 → 결과 저장"
)
PHASE_A_SELECT = "Phase A — 후보 선별"
PHASE_A_MATCH = "Phase A — 매칭"
PHASE_B_SELECT = "Phase B — 후보 선별 (역방향)"
PHASE_B_MATCH = "Phase B — 매칭 (역방향)"

# ── 저장/엑셀 ──────────────────────────────────────────────────────────────
SAVE_DIALOG_TITLE = "결과 엑셀 저장 위치 선택"
SAVE_FILENAME_FMT = "AOI검증결과_{ref}_vs_{val}_{ts}.xlsx"
SAVE_SUCCESS_FMT = "엑셀 저장 완료:\n{path}"
SAVE_FAIL_FMT = "엑셀 저장 실패:\n{error}"
EXPORT_TEMPLATE_NOT_FOUND = (
    "양식.xlsx 템플릿을 찾을 수 없습니다. 기본 양식으로 저장합니다."
)
SHEET_MISS_FAST = "미탐_빠른호기"
SHEET_MISS_SLOW = "미탐_늦은호기"
SLOT_MISMATCH_SHEET = "Slot 불일치 목록"

# ── 일반 상태 표시 ─────────────────────────────────────────────────────────
SLOT_COUNT_FMT = "{slot}: 기준 {ref}장 / 검증 {val}장"
COUNT_PLUS_N_FMT = "+{n}"
PROGRESS_SLOT_FMT = "{slot}  ·  {done} / {total}"
GROUP_HEADER_FMT = "{slot}  ·  {count} 장"

# ── 오류 ────────────────────────────────────────────────────────────────
ERR_GENERIC = "오류가 발생했습니다: {error}"
ERR_LOAD_IMAGE_FMT = "이미지를 불러올 수 없습니다: {path}"
ERR_FEATURE_FMT = "특징 추출 실패: {path}"
