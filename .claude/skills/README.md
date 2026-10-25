# .claude/skills — 설치한 외부 스킬 (2026-10-18, 사용자 요청)

| 폴더 | 출처 · 고정 커밋 | 라이선스 | 무엇 |
|---|---|---|---|
| `ponytail/` · `ponytail-review/` · `ponytail-audit/` · `ponytail-debt/` · `ponytail-help/` · `ponytail-gain/` | https://github.com/DietrichGebert/ponytail `skills/*` @ `09c434ab` | MIT (`LICENSE`) | 코드를 덜 쓰게 하는 지침 + diff 검토 · 저장소 감사 · 미뤄 둔 일 목록 |
| `graphify/` | https://github.com/safishamsi/graphify `graphify/skill.md` + `graphify/skills/claude/references/` @ `86c57be8` (0.9.92) | Apache-2.0 (`LICENSE` · `NOTICE`) | 코드 호출 그래프(`graphify-out/`, `.gitignore` 에 있음) |

- 원본을 고치지 않고 그대로 복사했다. ponytail 원본 플러그인의 hook · band 는 연결하지 않았다. 갱신은 같은 경로로 다시 복사한다.
- `.claude/` 는 자동 업데이트(`updater._UPDATE_SKIP_TOP`) 밖이라 사용자 PC 로 배포되지 않는다.
- ponytail 강도는 **lite** 로 쓴다(CLAUDE.md) — 위의 Karpathy 지침과 겹치고 "정확도는 절대 깨지 않는다" 가 우선한다.
- graphify 는 처음 실행할 때 `graphifyy` 를 pip 으로 설치한다(개발 PC 전용 — `requirements.txt` 와 무관). 코드만 볼 때는 `--code-only` 로 돌린다(문서 · 이미지는 Claude 로 보낸다).
- 스킬을 갱신하면 `python scripts/internal/verify_no_forbidden.py` 와 `test_no_spyder_conda.py` 가 통과하는지 확인한다.

## 10/18 추가 설치 (사용자 요청 — 다양하게, 자동으로)

| 폴더 | 출처 · 고정 커밋 | 라이선스 | 무엇 |
|---|---|---|---|
| `brainstorming/` · `systematic-debugging/` · `test-driven-development/` · `writing-plans/` · `executing-plans/` · `subagent-driven-development/` · `dispatching-parallel-agents/` · `requesting-code-review/` · `receiving-code-review/` · `verification-before-completion/` · `finishing-a-development-branch/` · `using-git-worktrees/` · `writing-skills/` · `using-superpowers/` | https://github.com/obra/superpowers `skills/*` 6.1.1 (Defect_Tracking 과 같은 사본) | MIT (`.claude/licenses/superpowers-MIT.txt`) | 계획 → 테스트 먼저 → 체계적 디버깅 → 완료 전 검증 → 리뷰 요청/받기 흐름 |
| `interview-me/` · `documentation-and-adrs/` · `observability-and-instrumentation/` · `deprecation-and-migration/` · `security-and-hardening/` · `performance-optimization/` | https://github.com/addyosmani/agent-skills `skills/*` @ `aeac41d4` | MIT (`.claude/licenses/addyosmani-agent-skills-MIT.txt`) | 요구 묻기 · 결정 기록 · 로그 · 삭제/이전 · 보안 · 성능. 체크리스트는 `.claude/references/` |
| `change-impact/` · `codebase-onboarding/` · `code-tour/` · `python-testing/` · `regex-vs-llm-structured-text/` · `skill-stocktake/` + 에이전트 `.claude/agents/silent-failure-hunter.md` · `python-reviewer.md` | https://github.com/affaan-m/ecc @ `76aaa77d` (v2.2.3) 에서 발췌 | MIT (`.claude/licenses/ecc-MIT.txt`) | 변경 영향 · 코드 안내 · pytest · 파서 판단 · 스킬 점검 · 조용한 실패/파이썬 리뷰 |
| `impeccable/` | https://github.com/pbakaus/impeccable `.claude/skills/impeccable` @ `cf3d2fa0` (3.9.1) + 에이전트 `.claude/agents/impeccable-manual-edit-applier.md` | Apache-2.0 (`LICENSE` · `NOTICE.md`) | UI 설계 · 감사 · 다듬기 명령 모음 |
| `ui-ux-pro-max/` | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill `.claude/skills/ui-ux-pro-max` @ `56ea9cf1` (2.15.0) | MIT (`.claude/licenses/ui-ux-pro-max-MIT.txt`) | 로컬 CSV 로 팔레트 · 글꼴 · UX 지침 검색(네트워크 없음) |
| `design-dna/` | https://github.com/zanwei/design-dna @ `593e39bc` | MIT (`LICENSE`) | 참고 화면에서 토큰 · 느낌 · 효과를 JSON 으로 뽑고 적용 |
| `awesome-design-md/` | https://github.com/VoltAgent/awesome-design-md `design-md/` @ `13be5c05` | MIT (`LICENSE`) | DESIGN.md 73개 참고 라이브러리(SKILL.md 는 이 저장소용으로 새로 씀) |
| `diagram-design/` | https://github.com/cathrynlavery/diagram-design `skills/diagram-design` @ `550fd66e` | MIT (`.claude/licenses/diagram-design-MIT.txt` · `diagram-design-THIRD_PARTY_LICENSES.md`) | 설명서용 구조도 · 흐름도(HTML/SVG) |

- 원본을 고치지 않고 그대로 복사했다(awesome-design-md 의 `SKILL.md` 만 예외). hook 은 하나도 연결하지 않았다 — ECC 의 gateguard 처럼 수정마다 멈추게 하는 것이 있어서다. ECC 의 토큰 · 모델 설정도 가져오지 않았다.
- 설치 전 NVIDIA SkillSpector(`--no-llm`)로 전부 검사했다. 걸린 항목은 공격 용어를 설명하는 문서 · 스킬 파일을 고치라는 안내(writing-skills) · 중첩 코드 블록을 위한 폭 0 공백(improve-animations `PLAN-TEMPLATE.md`) 같은 오탐이었고, 스크립트에 바깥 전송은 없었다.
- Addy 스킬이 `../../references/*.md` 로 가리키는 체크리스트는 `.claude/references/` 에 있다. 폴더 안에 라이선스가 없는 출처의 원문은 `.claude/licenses/` 에 있다.
- 겹칠 때 우선순위는 `CLAUDE.md` 에 적었다. 갱신은 같은 고정 커밋 경로에서 다시 복사한다.
- 데스크톱(PyQt6) 앱이라 디자인 스킬의 CSS · 웹폰트 · CDN 조언은 버리고 원칙만 쓴다. diagram-design 은 설명서 그림용이고 사진 파일 정책은 그대로다.
- 추가 뒤 `python scripts/internal/verify_no_forbidden.py` 와 `test_no_spyder_conda.py` 통과를 확인했다.

## 모션 레퍼런스 링크 (2026-10-20, 사용자 요청)

| 폴더 | 출처 | 라이선스 | 무엇 |
|---|---|---|---|
| `motion-reference/` | https://motion-reference.takayustudio.jp/ (TakayuStudio) — 외부 파일 없음, `SKILL.md` 만 이 저장소에서 새로 씀 | **명시 없음**("개인 학습용 레퍼런스") — 그래서 링크만 둔다 | 모션 이름 · 시간 · 이징을 찾는 사전. 코드 · 설명 · 목록은 복사하지 않고 수치와 원리만 참고 |

- 받아 온 파일이 없어 SkillSpector 검사는 하지 않았다.

## deep-interview (2026-10-25, 사용자 요청)

| 폴더 | 출처 | 라이선스 | 무엇 |
|---|---|---|---|
| `deep-interview/` | 현택님이 올린 zip(`SKILL.md` 1개) — 내용상 oh-my-claudecode 의 `deep-interview` 스킬(고정 커밋 미상) | zip 에 라이선스 파일 없음 | 질문을 하나씩 하며 '모호함 점수'가 기준 아래로 내려갈 때까지 요구사항을 다듬고 스펙을 쓴다 |

- 원본 그대로 복사했다. 스크립트 · 실행 파일은 없고 마크다운 1개뿐이며 바깥 전송 지시도 없었다.
- 원본은 oh-my-claudecode 플러그인 전제다 — 이어서 부르는 `oh-my-claudecode:plan` · `autopilot` · `ralph` · `team` · `autoresearch` 와 `.omc/` 상태 폴더는 이 저장소에 없다. 인터뷰와 스펙 작성만 쓰고, 실행 단계는 이 저장소의 `writing-plans` · `executing-plans` 로 넘긴다. 스펙 파일(`.omc/specs/`)은 커밋하지 않는다.
- `interview-me` 와 겹친다: 가벼운 확인은 `interview-me`, 크고 모호한 요구를 점수로 다듬을 때 `deep-interview`.
