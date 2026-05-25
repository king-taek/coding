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
    "사진을 클릭하거나 빈 곳에서 드래그해 여러 장을 선택/해제하세요. "
    "선택된 사진들에 아래 액션이 적용됩니다."
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
BTN_UNMATCHED_NEXT = "다음 사진"
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

# ── 유사도 엔진 모드 + 중앙 전처리 ────────────────────────────────────────
ENGINE_CARD_TITLE = "유사도 엔진"
ENGINE_MODE_BASIC = "기본 모드 (정밀 비교)"
ENGINE_MODE_EFFICIENCY = "고효율 모드 (CPU+GPU)"
ENGINE_MODE_TOOLTIP = (
    "기본 모드: 모든 후보를 정밀 비교 (정확하지만 대용량에서 느림).\n"
    "고효율 모드: Intel GPU(MobileNetV3) 임베딩으로 후보를 빠르게 추리고,\n"
    "  CPU 고전(pHash+ORB+SSIM)으로 상위 후보를 정밀 재채점해 융합합니다\n"
    "  (Intel GPU+OpenVINO 권장, 없으면 CPU 고전 단독으로 자동 폴백).\n"
    "  GPU 와 CPU 가 동시에 가동됩니다."
)
ACCEL_CONCURRENCY_LABEL = "동시 추론 수"
ACCEL_CONCURRENCY_TOOLTIP = (
    "고효율 모드에서 GPU/NPU 가 동시에 처리할 추론 개수(in-flight) 입니다.\n"
    "값을 높일수록 NPU/GPU 메모리 사용량과 시간당 처리량(throughput)이 함께\n"
    "올라갑니다 — 계산(모델·점수) 자체는 동일합니다.\n"
    "NPU 기준 값이며 GPU 는 그 절반을 사용합니다.  작업 관리자/상태바로 메모리를\n"
    "보면서 ~80% 정도까지 올려도 됩니다(정확한 상한이 아니라 강도 조절)."
)
# 고효율 모드 장치 토글 + 정적 배치 B (단기 테스트용).
DEVICE_CPU_LABEL = "CPU"
DEVICE_GPU_LABEL = "GPU"
DEVICE_NPU_LABEL = "NPU"
DEVICE_TOGGLE_TOOLTIP = (
    "고효율 모드에서 사용할 연산 장치 (테스트용).  끄면 그 장치 유닛을 띄우지\n"
    "않습니다.  전부 끄면 안전하게 CPU 로 폴백합니다.  계산 결과는 동일."
)
EMBED_BATCH_LABEL = "배치 B"
EMBED_BATCH_TOOLTIP = (
    "정적 배치 B 재컴파일 (테스트용).  1=끔(현행).  >1 이면 요청당 B장을 한 번에\n"
    "추론해 NPU/GPU 점유율을 더 높일 수 있습니다.  단, NPU 가 정적 배치를 지원\n"
    "해야 하며 실패 시 해당 유닛은 비활성(상태바 툴팁의 에러 참고).  계산 결과는 동일."
)
ENGINE_EFFICIENCY_CPU_ONLY = (
    "가속 장치(Intel GPU/NPU)가 없어 CPU만으로 고효율 모드를 실행합니다."
)
ACCEL_UNITS_FMT = "가속: {units}"
CENTER_CROP_LABEL = "사진 중앙 30%만 사용 (기준·검증)"
CENTER_CROP_TOOLTIP = (
    "유사도 계산 시 사진의 중앙 30% 영역만 사용합니다.\n"
    "테두리/배경 차이를 무시하고 중심부 패턴에 집중할 때 유용합니다.\n"
    "켜면 기준·검증 사진 모두에 적용됩니다.\n"
    "썸네일/엑셀 이미지는 원본 그대로 유지됩니다."
)
PRE_GROUP_TITLE = "강화 전처리 (계산 전용 — 화면 표시는 원본 유지)"
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

# ── 상태 바: CPU/GPU 사용량 ──────────────────────────────────────────────
# CPU 는 실제 사용률(%), GPU 는 가동/대기(추론 중 여부) — Intel GPU 의 실제
# 점유율(%)은 이식성 있게 얻을 수 없어 '가동/대기'로 표시한다.
USAGE_CPU_FMT = "CPU {pct}%"
USAGE_GPU_FMT = "GPU {state}"
USAGE_STATE_BUSY = "가동"
USAGE_STATE_IDLE = "대기"
USAGE_STATE_NONE = "없음"
USAGE_SEP = "   "

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
    "① 자동화 수준을 선택합니다  ·  사진 직접 선택 / 모두 자동\n"
    "② 기준 장비와 검증 장비의 폴더와 호기 번호를 입력합니다\n"
    "③ 유사도 엔진을 고릅니다  ·  기본 모드 / 고효율 모드(CPU+GPU)\n"
    "④ 유사도 임계치를 조정합니다\n"
    "⑤ [검증 시작] 을 누르면 다음 순서로 진행됩니다\n"
    "      ㄱ. 후보 선별 — 기준 사진을 한 장씩 보면서 [✓ 검증] / [✕ 제외]\n"
    "          (‘모두 자동’ 은 이 단계를 건너뜁니다)\n"
    "      ㄴ. 유사도 매칭 — 자동 매치 후 ‘매치 검토’ 에서 확인·교체\n"
    "      ㄷ. 매치 실패 사진 검토 — 실패한 기준 사진의 후보를 다시 확인\n"
    "      ㄹ. 결과 저장 — 양식 폴더의 양식.xlsx 를 복사하여 자동 저장\n"
    "매치 검토·실패 검토에서 ‘크게 보기’ 로 기준·후보를 나란히 비교(←/→ 이동)\n"
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
PHASE_EMBED = "후보 생성 (GPU 임베딩)"      # 고효율 모드 1단계 — 유사도 계산 직전
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
# slot명 불일치 시, KLA 사진 속 WaferID 를 OCR 로 읽는 동안 표시.
LOAD_OCR_WAFERID = "KLA 사진 속 WaferID를 읽고 있습니다…"
LOAD_OCR_WAFERID_FMT = "KLA 사진 속 WaferID를 읽는 중… {done} / {total}"

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
INFO_PHASE_A_TO_MATCH = "후보 선별이 끝났습니다. 매칭으로 넘어갑니다."
INFO_NO_MATCH_FOUND = "임계치 이상인 후보가 없습니다. 자동으로 Skip 처리됩니다."
INFO_ALREADY_MATCHED_SECTION = "이미 매칭됨 (자동 제외)"

# ── 저장/엑셀 ──────────────────────────────────────────────────────────────
SAVE_DIALOG_TITLE = "결과 엑셀 저장 위치 선택"
SAVE_FILENAME_FMT = "AOI검증결과_{ref}_vs_{val}_{ts}.xlsx"
SAVE_SUCCESS_FMT = "엑셀 저장 완료:\n{path}"
SAVE_FAIL_FMT = "엑셀 저장 실패:\n{error}"
EXPORT_TEMPLATE_NOT_FOUND = (
    "양식.xlsx 템플릿을 찾을 수 없습니다. 기본 양식으로 저장합니다."
)
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
# 매치 검토 각 행 slot 라벨 아래 ‘크게 보기’ 버튼 — 좌우 비교 뷰어를 연다.
BTN_VIEW_LARGER = "크게 보기"
# 좌우 비교 뷰어에서 ‘이 후보로 매치’ 액션 버튼 (#4).
BTN_MATCH_THIS = "이 후보로 매치"
# 차순위 후보 ‘후보 한 줄 더 보기’ / ‘접기’ 버튼 (#5/#4).
RUNNERUP_MORE_ROW = "후보 한 줄 더 보기 ▾"
RUNNERUP_LESS_ROW = "접기 ▴"
# (사용 안 함) 예전 ‘+N개 더 보기’ + 표시 개수 입력 다이얼로그 (#16).
RUNNERUP_MORE_FMT = "+{n}개 더 보기"
RUNNERUP_MORE_TITLE = "후보 더 보기"
RUNNERUP_MORE_PROMPT = "후보를 몇 개까지 보시겠습니까?"
