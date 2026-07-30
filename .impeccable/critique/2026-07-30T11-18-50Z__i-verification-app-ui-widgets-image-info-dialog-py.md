---
target: 단일 사진 정보 다이얼로그
total_score: 20
p0_count: 0
p1_count: 3
timestamp: 2026-07-30T11-18-50Z
slug: i-verification-app-ui-widgets-image-info-dialog-py
---
Method: dual-agent (A: 디자인 리뷰 · B: 실측/검출) — 두 평가를 격리 실행 후 통합.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | 드래그 중 피드백 0, 드롭 거절 시 무반응 |
| 2 | Match System / Real World | 3 | 엑셀과 같은 도메인 언어. 상태가 데이터 줄과 같은 모양 |
| 3 | User Control and Freedom | 3 | Esc·닫기 동작. 닫기 어포던스 2개(중복) |
| 4 | Consistency and Standards | 1 | 여백·모노·액자·autoDefault·스크롤 정책 전부 앱 관습 이탈 |
| 5 | Error Prevention | 2 | 좁은 창에서 수치가 잘려 오독 가능 |
| 6 | Recognition Rather Than Recall | 2 | 절대좌표와 die 내부 좌표 구분을 화면이 말하지 않음 |
| 7 | Flexibility and Efficiency | 1 | 단축키·니모닉 없음, 파일 대화상자가 매번 cwd |
| 8 | Aesthetic and Minimalist Design | 2 | 힌트 상시 노출 + 경로줄 중복 + 화면 60% 공백 |
| 9 | Error Recovery | 1 | 복구 안내가 도달 불가(dead code) |
| 10 | Help and Documentation | 3 | 카피는 정확하나 상시 노출이라 로드 후 소음 |
| **Total** | | **20/40** | **Acceptable — 상당한 개선 필요** |

## Anti-Patterns Verdict

**LLM 판정**: 표면은 slop, 코어는 아니다. 앱은 '도면(Datum)' 언어(타이틀블록·하이라인 눈금·모노 수치·판정 스탬프)를 갖고 `style.qss` 에 role 이 전부 있는데, 이 다이얼로그는 `setProperty("role", …)` 를 **0회** 썼다. 계측 화면인데 계측기로 보이지 않는다.

**결정적 스캔**: 번들 detector 는 `.qss`/`.py` 를 스캔 대상 확장자로 갖지 않아 **0 파일 스캔**(빈 결과 = 통과 아님, 커버리지 0). 카나리아 CSS 로 엔진 동작은 확인. QSS 를 렌더해 `.css` 로 넘겨도 0 findings — false positive 없음, 기여 증거도 없음.

**실측(에이전트 B)**: 대비비 22쌍 **전부 통과**(최저 5.41, 프로젝트 자체 게이트 5.0 상회). i18n 하드코딩 0. 클릭 타깃 전부 통과. 하드 결함 7건 — 포커스 표시 없는 탭 스톱, 341자 경로에서 내용 1357px vs 뷰포트 652px 가로 넘침, 640px 폭 텍스트 잘림, 594px 높이 바닥, 닫기 버튼 2개, 읽기전용 필드가 편집 필드와 픽셀 동일, 루트 여백 Qt 기본값.

## What's Working

1. **진실의 단일 출처** — `single_info` 가 엑셀 D열과 같은 생산자. "화면 값과 결과 파일 값이 다르다"는 사고를 구조적으로 불가능하게 만든다.
2. **패널 통째 교체 렌더** — `deleteLater` 지연 겹침을 예외 처리가 아니라 구조로 제거.
3. **빈 어포던스를 만들지 않음** — 값 없는 항목은 행 자체를 만들지 않고, 복사할 게 없으면 버튼을 잠근다.

## Priority Issues

- **[P1] 복구 안내가 도달 불가** — `IMAGE_INFO_EMPTY` 는 행이 0개일 때만 뜨는데 파일명/폴더 행이 항상 생겨 영원히 안 뜬다. 테스트가 그 버그를 `assert ... not in texts` 로 고정해 두었고 docstring 은 정반대를 말한다. → 판정을 "계측이 0개"로 변경.
- **[P1] 가로 넘침 / 수치 잘림** — 공백 없는 긴 경로는 wordWrap 이 듣지 않아 어떤 크기에서도 가로 스크롤. CLAUDE.md 금지 사항. → 가운데 생략 + 툴팁.
- **[P1] 디자인 언어 이탈** — 모노 수치·THUMB_FRAME 액자·16/10 여백·타이틀블록 미사용. → 앱의 role 어휘로 전면 교체.
- **[P2] 읽기전용 경로 필드** — 편집 가능한 입력란과 픽셀 동일, 첫 포커스가 여기로, 아래 파일명/폴더 행과 완전 중복. → 제거.
- **[P2] 라벨 행과 결함 줄 구분 0** — 결함 줄이 폴더 값의 둘째 줄처럼 읽힌다. → 그룹 + 타이틀블록.

## Persona Red Flags

**Alex(파워유저)**: 단축키 0, 파일 대화상자 시작 경로 `""`, 복사마다 모달 확인 클릭.
**Sam(접근성)**: `QScrollArea` 가 탭 스톱인데 포커스 표시 없음(WCAG 2.4.7), `autoDefault` 로 Enter 가 마지막 버튼 재발동, 값 라벨이 키보드 선택 불가, 미리보기에 접근 이름 없음.
**Riley(엣지)**: 640px 에서 수치 잘림+가로 스크롤, 341자 경로에서 두 표기가 서로 다르게 잘림, 정보파일 없는 폴더에서 정상/비정상 상태 구별 불가, 사진 아닌 파일 드롭 시 무반응.

## Questions to Consider

1. full_bleed 를 요구해 놓고 미리보기가 420px 고정이면 규칙을 문자만 지킨 것 아닌가?
2. 상단 경로줄과 파일명/폴더 행 중 하나는 없어도 되지 않나?
3. 사용자가 여는 진짜 단위가 "한 장"인가 "한 폴더"인가? 후자라면 파일 대화상자 왕복이 구조적 세금이다.
