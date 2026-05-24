"""모든 사용자 노출 문자열(한국어)을 한 곳에 모아둔 모듈.

UI/로그/툴팁/오류 메시지 모두 이 모듈을 통해 참조합니다.
번역이나 일괄 수정 시 이 파일만 보면 됩니다.
"""

# ── 앱/메타 ────────────────────────────────────────────────────────────────
APP_TITLE = "AOI 검증"
# 개발자 크레딧 — 주요 화면/상태바 공통 표시.
CREDIT = "Developed by 임현택"

# ── OpenVINO 자동 설치 안내 ───────────────────────────────────────────────
OPENVINO_OFFER_TITLE = "Intel GPU / NPU 가속 활성화"
OPENVINO_OFFER_BODY = (
    "Intel CPU 가 감지되었습니다.\n"
    "OpenVINO 를 설치하면 Intel GPU (Iris Xe / Arc) 와 NPU (AI Boost) "
    "가속이 자동으로 활성화되어 유사도 계산이 빨라집니다.\n\n"
    "지금 설치할까요? (약 200 MB)"
)
OPENVINO_OFFER_BTN_INSTALL = "지금 설치"
OPENVINO_OFFER_BTN_LATER = "다음에"
OPENVINO_OFFER_BTN_NEVER = "다시 보지 않기"
OPENVINO_INSTALL_PROGRESS = "OpenVINO 설치 중…"
OPENVINO_INSTALL_DONE = (
    "OpenVINO 설치 완료!\n프로그램을 다시 시작하면 Intel GPU / NPU 가속이 적용됩니다."
)
OPENVINO_INSTALL_FAILED_FMT = (
    "OpenVINO 설치에 실패했습니다 — {error}\n\n"
    "수동으로 시도해보세요:  pip install openvino"
)
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
BTN_UNDO = "되돌리기(Z)"
BTN_SKIP = "잠시 보류"            # 더이상 표시되지 않음 (#3) — 호환용
BTN_NO_MATCH = "매칭 없음"
BTN_RETRY_SKIP = "보류 재시도"
BTN_SELECT_MODE = "선택 모드"
BTN_CANCEL_SELECT_MODE = "선택 해제"

# ── 다중 선택 다이얼로그 (Stage 1 선택 모드) ─────────────────────────────
BULK_SELECT_TITLE_FMT = "{panel} — 다중 선택"
BULK_SELECT_HINT = (
    "사진을 클릭해서 선택/해제하세요. 선택된 사진들에 아래 액션이 적용됩니다."
)
BULK_SELECT_SUMMARY_FMT = "선택됨: {n} 장"
BULK_SELECT_EMPTY = "표시할 사진이 없습니다."
BULK_SELECT_ALL = "전체 선택"
BULK_DESELECT_ALL = "선택 해제"
INLINE_SELECT_COUNT_FMT = "선택 {n}장"
BTN_REMOVE_FROM_TARGET = "검증 대상에서 제거"
BTN_MOVE_TO_EXCLUDE = "제외로 이동"
BTN_MOVE_TO_TARGET = "검증 대상으로 이동"
BTN_BACK_TO_CENTER = "중앙으로 복귀(재결정)"
BTN_BATCH_EXCLUDE = "선택 항목 검증 제외"
BTN_BATCH_VERIFY = "선택 항목 검증 대상 지정"
BTN_VIEW_EXCLUDED_FMT = "검증 제외 사진 보기 ({n})"
# Stage 1 ‘선택 종료’ — 미결정 사진 모두 제외 처리 후 다음 단계로.
BTN_END_SELECTION = "선택 종료"
END_SELECTION_CONFIRM_TITLE = "선택 종료"
END_SELECTION_CONFIRM_FMT = (
    "남은 {n} 장의 미결정 사진을 모두 ‘검증 제외’ 로 처리하고 "
    "다음 단계로 진행할까요?"
)
BULK_SELECT_EXCLUDED_TITLE = "검증 제외 사진"

BTN_EXPORT_EXCEL = "엑셀로 저장"
BTN_OPEN_RESULT = "결과 폴더 열기"
BTN_NEW_SESSION = "새 검증 시작"
BTN_REVIEW_MATCHES = "매칭 결과 검토"

# ── 매칭 결과 검토 (#18) ───────────────────────────────────────────────────
REVIEW_DIALOG_TITLE = "매칭 결과 검토"
REVIEW_HINT = (
    "잘못 매칭된 행은 [삭제] 로 표시(빨간 테두리)한 뒤 [확인] 을 누르면\n"
    "결과에서 제외됩니다.  제외된 사진은 ‘매치 실패’ 로 분류되어 매치 실패\n"
    "사진 검토에서 ‘매칭 취소 목록’ 으로 다시 검토할 수 있습니다."
)
REVIEW_BTN_DELETE = "삭제"
REVIEW_BTN_UNDELETE = "삭제 취소 ↩"
REVIEW_REMOVED_FMT = "{n} 개의 매칭이 결과에서 제외되었습니다."

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
    " [매치 확정] 을 누르세요. 후보를 더블클릭/우클릭하면 크게 비교할 수 있습니다."
)
UNMATCHED_REVIEW_NO_CANDIDATES = "이 슬롯에는 검증 장비 후보가 없습니다."
UNMATCHED_REVIEW_DONE_FMT = "{n} 건의 신규 매칭을 확정했습니다."
UNMATCHED_REVIEW_EMPTY = "검토할 매치 실패 사진이 없습니다."
UNMATCHED_CONFIRM_ON_CLOSE = (
    "선택(파란 테두리)했지만 아직 확정하지 않은 후보가 있습니다.\n"
    "선택한 대로 매칭하시겠습니까?"
)
BTN_UNMATCHED_PICK = "이 사진으로 매칭"
BTN_UNMATCHED_CONFIRM = "매치 확정"
BTN_UNMATCHED_SELECT_THIS = "이 후보로 선택"
BTN_UNMATCHED_NEXT = "건너뛰기 (다음)"
BTN_UNMATCHED_PREV = "← 이전"
BTN_UNMATCHED_CLOSE = "검토 종료"

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
    "단축키:  ← 또는 1 = 검증   /   → 또는 2 = 제외   /   Z = 되돌리기"
)
SHORTCUT_STAGE2_TOOLTIP = "단축키:  S = 잠시 보류    N = 매칭 없음 확정"
PANEL_NO_MATCH_LIST = "매칭 없음 확정"

# ── 사진 크기 슬라이더 ────────────────────────────────────────────────────
IMAGE_SIZE_LABEL = "사진 크기"
SLOT_LABEL_FMT = "Slot: {slot}"

# ── 사용 방법 토글 ────────────────────────────────────────────────────────
HOWTO_TOGGLE_OPEN = "사용 방법 ▾"
HOWTO_TOGGLE_CLOSE = "사용 방법 ▴"

# ── 썸네일 빠른 모드 ──────────────────────────────────────────────────────
SPEED_MODE_LABEL = "빠른 모드 (썸네일 화질 낮춤)"
SPEED_MODE_TOOLTIP = (
    "사진이 많을 때 썸네일/중간 이미지 화질을 자동으로 낮춰 처리 시간을\n"
    "줄입니다. 체크하면 항상 가장 빠른 티어 (140px / Q65) 를 사용합니다.\n"
    "결과 엑셀에 들어가는 중간 이미지에도 영향을 줍니다."
)

# ── 유사도 엔진 모드 + 강화/KLA 전처리 ────────────────────────────────────
ENGINE_CARD_TITLE = "유사도 엔진"
ENGINE_MODE_BASIC = "기본 모드 (정밀 비교)"
ENGINE_MODE_FAST = "고속 모드 (대용량 권장)"
ENGINE_MODE_TOOLTIP = (
    "기본 모드: 모든 후보를 정밀 비교 (정확하지만 대용량에서 느림).\n"
    "고속 모드: 가벼운 이미지 특징으로 후보를 빠르게 추린 뒤, 상위 후보만\n"
    "  정밀 비교로 재정렬합니다.  사진이 수천 장 이상일 때 권장.\n"
    "  (별도 설치/인터넷 없이 동작합니다.)"
)
ENGINE_FAST_UNAVAILABLE = (
    "고속 모드를 사용할 수 없어 기본 모드로 진행합니다 (torch 미설치)."
)
# 고속 모드 의존성 설치 안내
FAST_DEPS_TITLE = "고속 모드 준비"
FAST_DEPS_BODY_FMT = (
    "고속 모드(임베딩 + ANN)를 사용하려면 아래 패키지 설치가 필요합니다.\n"
    "설치하지 않으면 고속 모드가 기본 모드로 폴백되어 속도 차이가 없습니다.\n\n"
    "  • {pkgs}\n\n"
    "지금 설치할까요?  (인터넷 필요)"
)
FAST_DEPS_NOTE_OPENVINO = (
    "\n※ openvino 는 Intel GPU/NPU 가속용(선택) — 설치 시 임베딩이 크게 빨라집니다."
)
FAST_DEPS_BTN_INSTALL = "지금 설치"
FAST_DEPS_BTN_BASIC = "기본 모드로 진행"
FAST_DEPS_INSTALLING = "고속 모드 패키지 설치 중…\n{line}"
FAST_DEPS_DONE = (
    "설치 완료!  [검증 시작] 을 다시 누르면 고속 모드로 진행됩니다."
)
FAST_DEPS_DONE_RESTART = (
    "설치는 끝났지만 적용하려면 프로그램을 다시 시작해야 합니다.\n"
    "재시작 후 [검증 시작] 을 누르면 고속 모드가 적용됩니다."
)
FAST_DEPS_FAILED_FMT = (
    "설치 실패 — {error}\n\n"
    "수동 설치:  pip install hnswlib"
)
CENTER20_REF_LABEL = "기준 사진 중앙 30%만 사용"
CENTER20_VAL_LABEL = "검증 사진 중앙 30%만 사용"
CENTER20_TOOLTIP = (
    "유사도 계산 시 사진의 중앙 30% 영역만 사용합니다.\n"
    "테두리/배경 차이를 무시하고 중심부 패턴에 집중할 때 유용합니다.\n"
    "기준·검증을 각각 켤 수 있으며, 보통 둘 다 켜는 것이 정확합니다.\n"
    "썸네일/엑셀 이미지는 원본 그대로 유지됩니다."
)
PRE_GROUP_TITLE = "강화 전처리 (계산 전용 — 화면 표시는 원본 유지)"
PRE_GRAYSCALE_LABEL = "흑백 + 고감도"
PRE_CONTRAST_LABEL = "고대비"
KLA_CROP_LABEL = "KLA 정보영역 잘라내기 (상·하단 텍스트)"
PERSIST_SCORES_LABEL = "유사도 점수 디스크 캐시 (재실행 시 재계산 생략)"
PERSIST_SCORES_TOOLTIP = (
    "basic 엔진에서 계산한 (기준, 검증) 쌍의 유사도 점수를 디스크에 저장합니다.\n"
    "같은 사진/설정으로 다시 실행하면 저장된 점수를 불러와 재계산을 건너뜁니다.\n"
    "사진이 바뀌거나 전처리/엔진 설정이 달라지면 자동으로 다시 계산합니다."
)
PRE_GROUP_TOOLTIP = (
    "유사도 계산에만 적용되는 이미지 보정입니다. 썸네일/엑셀 이미지는\n"
    "원본 그대로 유지됩니다. 각 옵션은 독립적으로 켤 수 있습니다."
)
SIZE_TIER_NOTICE_FMT = (
    "사진이 많아 썸네일 화질을 자동 조정했습니다 ({thumb}px / Q{q})"
)

# ── 상태 바: 메모리 / 진행 ────────────────────────────────────────────────
MEMORY_USAGE_FMT = "메모리 사용량: {mb} MB"
MEMORY_PRESSURE_TOAST = "메모리 사용량이 높아 캐시를 정리했습니다"

# ── Stage 2 더 크게 보기 ───────────────────────────────────────────────────
BTN_EXPAND_VIEW = "더 크게 보기"
EXPAND_VIEW_TOOLTIP = "이 사진을 크게 보기 (←/→ 이전·다음, Enter 매칭, Esc 돌아가기)"
BTN_CONFIRM_AS_MATCH = "매치"            # 확대 보기 — 단순화 (#2)
BTN_BACK_TO_GRID = "돌아가기"            # 확대 보기 — 화살표 제거 (#2)
BTN_EXPAND_PREV = "◀ 이전"
BTN_EXPAND_NEXT = "다음 ▶"
EXPAND_POSITION_FMT = "{cur} / {total}"

# ── 셋업 화면 사용 설명 ────────────────────────────────────────────────────
SETUP_HOW_TO_USE_TITLE = "사용 방법"
SETUP_HOW_TO_USE_BODY = (
    "① 검증 모드를 선택합니다  ·  한쪽만 검증 / 양쪽 교차검증\n"
    "② 기준 장비와 검증 장비의 폴더와 호기 번호를 입력합니다\n"
    "③ 유사도 임계치를 조정합니다  (기본 70%)\n"
    "④ [검증 시작] 을 누르면 다음 순서로 진행됩니다\n"
    "      ㄱ. 후보 선별 — 결정할 사진을 한 장씩 보면서 [✓ 검증] / [✕ 제외]\n"
    "      ㄴ. 유사도 매칭 — 기준 사진별로 가장 비슷한 검증 사진을 선택\n"
    "      ㄷ. 결과 저장 — 양식 폴더의 양식.xlsx 를 복사하여 자동 저장\n"
    "단축키 — ← / 1 = 검증,  → / 2 = 제외,  Z = 되돌리기,  S = 건너뛰기"
)

# ── 양식 파일 / 저장 파일 명명 ─────────────────────────────────────────────
TEMPLATE_DIR_NAME = "양식"
TEMPLATE_FILE_NAME = "양식.xlsx"
RESULT_FILE_TITLE_FMT = "AOI {val} 검증 ({ref} 기준).xlsx"
TEMPLATE_NOT_FOUND_TITLE = "양식 파일 없음"
TEMPLATE_NOT_FOUND_BODY = (
    "‘양식’ 폴더 안의 ‘양식.xlsx’ 를 찾을 수 없습니다.\n"
    "기본 양식으로 결과를 생성합니다.\n\n"
    "확인한 경로: {path}"
)
WORKING_FILE_READY_FMT = "결과 파일이 준비되었습니다:\n{path}"
WORKING_FILE_LABEL = "결과 파일 위치"

# ── 학습 모델 / 동의 / 정확도 ──────────────────────────────────────────────
CONSENT_TITLE = "학습 데이터 사용 동의"
CONSENT_BODY_FMT = (
    "이번 세션의 매칭 쌍 {n} 개를\n"
    "유사도 모델의 학습 데이터로 추가할까요?\n\n"
    "(다음 [모델 재학습] 시 반영됩니다.\n"
    " 매칭되지 않은 사진은 사용하지 않습니다.)"
)
CONSENT_OK_FMT = "학습 데이터 {n} 쌍이 추가되었습니다."
CONSENT_FAIL_FMT = "학습 데이터 저장 실패: {error}"

MODEL_CARD_TITLE = "학습 모델"
MODEL_OPTION_BASIC = "기본 탐지 모드 (학습 모델 미사용)"
MODEL_OPTION_FMT = (
    "{name}  ·  {pairs}쌍 학습  ·  Hit@5 {hit5}% [{lo}~{hi}]  ·  {evals}회"
)
MODEL_OPTION_NO_ACC_FMT = "{name}  ·  {pairs}쌍 학습  ·  평가 데이터 부족"
MODEL_OPTION_BASELINE_FMT = (
    "기본 탐지 모드  ·  Hit@5 {hit5}% [{lo}~{hi}]  ·  {evals}회 평가"
)
MODEL_DELTA_FMT = "  (vs 기본 모드: {sign}{delta}%p)"
MODEL_WEAKEST_SLOT_FMT = "최약 슬롯: {slot}  ·  Hit@5 {hit5}%  ·  {picks}회"
MODEL_DATA_COUNT_FMT = "수집된 학습 데이터: {n} 쌍"
MODEL_NO_TORCH = "torch 가 설치되어 있지 않아 학습 기능을 사용할 수 없습니다."

BTN_RETRAIN = "모델 재학습 시작"
BTN_REFRESH_ACC = "정확도 갱신"
BTN_DELETE_MODEL = "모델 삭제"
BTN_EXPORT_MODEL = "내보내기"
BTN_IMPORT_MODEL = "가져오기"
EXPORT_DIALOG_TITLE = "모델 내보내기"
IMPORT_DIALOG_TITLE = "모델 가져오기"
EXPORT_DONE_FMT = "모델을 내보냈습니다:\n{path}"
IMPORT_DONE_FMT = "모델 ‘{name}’ 을 가져왔습니다."
EXPORT_FAIL_FMT = "내보내기 실패: {error}"
IMPORT_FAIL_FMT = "가져오기 실패: {error}"

LOAD_BACKBONE_FMT = "백본 임베딩 추출 중… {done} / {total}"
LOAD_TRAIN_FMT = "헤드 학습 중… 에폭 {epoch} / {total}  ·  loss {loss:.3f}"

TRAIN_DONE_FMT = "모델 ‘{name}’ 학습 완료. 다음 검증부터 적용됩니다."
TRAIN_KEPT_BASIC_FMT = (
    "모델 ‘{name}’ 학습은 완료했으나 기본 탐지 모드보다 정확도가 낮아\n"
    "기본 모드를 유지합니다 (새 모델 Hit@1 {new}% vs 기본 {basic}%).\n"
    "학습 데이터가 더 모이면 자동으로 다시 시도됩니다."
)
TRAIN_FAIL_FMT = "학습 실패: {error}"
AUTO_RETRAIN_STARTED_FMT = (
    "수집된 학습 데이터 {n} 쌍으로 모델 재학습을 백그라운드에서 시작합니다.\n"
    "결과 화면을 그대로 두고 다른 작업을 해도 됩니다."
)
AUTO_RETRAIN_DONE_FMT = (
    "자동 재학습 완료 — 새 모델 ‘{name}’ 이 적용되었습니다."
)
AUTO_RETRAIN_KEPT_BASIC_FMT = (
    "자동 재학습 완료 — 모델 ‘{name}’ 은 저장되었지만, 기본 탐지 모드 보다\n"
    "정확도가 낮아 기본 모드를 유지합니다."
)
TRAIN_NEED_MORE_DATA = (
    "학습 데이터가 부족합니다. 매칭 쌍을 더 모은 뒤 다시 시도해 주세요."
)
TRAIN_CONFIRM_TITLE = "모델 재학습"
TRAIN_CONFIRM_BODY_FMT = (
    "현재 수집된 {n} 쌍의 데이터로 새 모델을 학습합니다.\n"
    "기존 모델은 그대로 유지되고, 새 모델이 생성됩니다.\n"
    "계속할까요?"
)

DELETE_CONFIRM_TITLE = "모델 삭제"
DELETE_CONFIRM_BODY_FMT = (
    "‘{name}’ 모델을 삭제하시겠습니까?\n"
    "가중치 파일, 메타 정보, 평가 로그가 모두 제거됩니다."
)

ACC_REFRESH_NO_CHANGE = "갱신할 모델이 없거나, 평가 데이터가 부족합니다."
ACC_REFRESH_DONE_FMT = "정확도 갱신 완료. {renamed} 개의 모델 파일이 변경되었습니다."

MODEL_TOOLTIP = (
    "정확도 = 모델 추천 순위와 사용자가 실제 선택한 후보의 일치도입니다.\n"
    "같은 슬롯에 중복 사진이 많을 경우 절대값보다는 모델 간 비교용으로 보세요."
)

# ── 로딩/진행 ──────────────────────────────────────────────────────────────
LOAD_THUMBNAIL_FMT = "썸네일 생성 중… {done} / {total}"
LOAD_STAGE_PREP = "다음 단계 준비 중…"
LOAD_FEATURE_FMT = "검증 장비 특징 추출 중… {done} / {total}"
LOAD_FEATURE_DONE = "검증 장비 특징 추출 완료 — 이후 매칭은 즉시 처리됩니다"
LOAD_SCORING_FMT = "유사도 계산 중… {done} / {total}"
# 진행 단계(phase)를 실제 작업에 맞춰 표시 (#8) — phase 예: '이미지 특징 분석',
# '유사도 계산'.  done/total 과 함께 사용자가 지금 무슨 작업인지 알 수 있다.
LOAD_PHASE_FMT = "{phase} 중… {done} / {total}"
PHASE_FEATURE = "이미지 특징 분석"
PHASE_SCORING = "유사도 계산"
LOAD_PRECOMPUTE_FMT = (
    "유사도 계산 중… {done} / {total}"
)
# 수동 모드: 첫 슬롯만 기다리고 나머지는 백그라운드 (#streaming).
LOAD_PRECOMPUTE_FIRST_SLOT = (
    "첫 슬롯 유사도 계산 중… 잠시만 기다려 주세요."
)
LOAD_PRECOMPUTE_WAIT_FMT = (
    "{slot} 슬롯 유사도 계산을 기다리는 중… 다음 슬롯은 백그라운드에서 준비됩니다"
)
PRECOMPUTE_BG_STATUS_FMT = "백그라운드 유사도 계산: {idx} / {total} 슬롯 완료"
PRECOMPUTE_BG_DONE = "유사도 계산 완료"
LOAD_AUTO_MATCH_FMT = "자동 매치 진행 중… {done} / {total}"

# ── 자동화 수준 (#3 올인원 모드) ───────────────────────────────────────────
AUTOMATION_TITLE = "자동화 수준"
AUTOMATION_MANUAL = "수동 — 모든 단계 직접 처리 (기존 방식)"
AUTOMATION_USER_SELECT = "사진 직접 선택 + 매치는 자동"
AUTOMATION_AUTO_ALL = "모든 사진 자동 — Stage 1 건너뛰기"
AUTOMATION_HINT = (
    "자동 모드에서는 임계치 이상에서 가장 점수가 높은 후보가 자동으로 선택됩니다.\n"
    "‘모든 사진 자동’ 은 Stage 1 을 건너뛰고 모든 기준 사진을 자동으로 매치합니다.\n"
    "자동 매치 종료 후 결과 화면에서 [매칭 결과 검토] 로 잘못된 매치를 제거할 수 있습니다."
)
AUTO_REVIEW_HINT_FMT = (
    "자동 매치 완료 — 총 {n_match} 쌍이 자동으로 매치되었고,\n"
    "{n_miss} 장은 임계치 미달로 ‘매칭 없음’ 처리되었습니다.\n"
    "[매칭 결과 검토] 로 결과를 확인해 주세요."
)
# ── 매치 검토 페이지 ───────────────────────────────────────────────────────
MATCH_REVIEW_TITLE = "매치 검토"
MATCH_REVIEW_HINT = (
    "자동 매치 결과를 한 줄씩 확인하세요.  매치가 잘못된 경우 [매치 없음]\n"
    "을 누르면 그 사진은 엑셀에 ‘기준 사진 + 빨간 파일명 (미매칭)’ 행으로\n"
    "들어갑니다.  실수로 누른 경우 [되돌리기] 로 다시 매치 상태로 복귀."
)
BTN_MARK_NO_MATCH = "매치 없음 ✕"
BTN_RESTORE_MATCH = "되돌리기 ↩"
BTN_FINISH_REVIEW = "검토 완료 ▶"
RUNNERUP_TOOLTIP = "클릭하면 이 사진으로 매치를 교체합니다."
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
    "한쪽에만 존재하는 Slot 이 있습니다.\n"
    "사용자가 직접 슬롯 매핑을 정해주실 수 있습니다.\n\n"
    "기준 전용: {ref_only}\n검증 전용: {val_only}"
)
SLOT_MAP_TITLE = "Slot 수동 매핑"
SLOT_MAP_HINT = (
    "한쪽에만 존재하는 슬롯을 사용자가 직접 짝지어 줄 수 있습니다.\n"
    "예) 기준 ‘Slot_01’ ↔ 검증 ‘S01’ 처럼 명명 규칙이 다를 때 사용하세요.\n"
    "짝지어지지 않은 슬롯은 매칭에서 제외됩니다."
)
SLOT_MAP_REF_LABEL = "기준"
SLOT_MAP_VAL_LABEL = "검증"
SLOT_MAP_ADD = "추가"
SLOT_MAP_REMOVE = "선택 해제"
SLOT_MAP_OPEN = "매핑 다이얼로그 열기"

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

# 오류 로그 기록 안내 (#4) — 상세는 ‘오류 목록’ 폴더의 txt 파일에 남긴다.
ERROR_LOGGED = "오류가 기록되었습니다"

# ── UI 개선 (#11 / #13 / #16) ─────────────────────────────────────────────
# (사용 안 함) 예전 ‘검토에서 삭제한 사진’ 하단 섹션 제목 — 행을 옮기지 않고
# 제자리 빨간 테두리로 표시하도록 되돌렸다 (#1).
MATCH_REVIEW_DELETED_SECTION = "검토에서 삭제한 사진"
# 썸네일 우클릭 컨텍스트 메뉴 — 원본 크게 보기 (#13).
CTX_VIEW_LARGER = "크게보기"
# 좌우 비교 뷰어에서 ‘이 후보로 매치’ 액션 버튼 (#4).
BTN_MATCH_THIS = "이 후보로 매치"
# 차순위 후보 ‘후보 한 줄 더 보기’ / ‘접기’ 버튼 (#5/#4).
RUNNERUP_MORE_ROW = "후보 한 줄 더 보기 ▾"
RUNNERUP_LESS_ROW = "접기 ▴"
# (사용 안 함) 예전 ‘+N개 더 보기’ + 표시 개수 입력 다이얼로그 (#16).
RUNNERUP_MORE_FMT = "+{n}개 더 보기"
RUNNERUP_MORE_TITLE = "후보 더 보기"
RUNNERUP_MORE_PROMPT = "후보를 몇 개까지 보시겠습니까?"
