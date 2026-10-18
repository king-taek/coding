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
