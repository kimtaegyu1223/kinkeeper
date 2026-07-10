# KinKeeper 전면 점검 진행상태 (chore/overhaul)

> 요금/세션 중단 시 이 파일을 보고 이어서 진행. 새 세션에서 "kinkeeper overhaul 이어서 해줘"라고 하면 됨.
> 작업 위치: `/home/ktg/projects/kinkeeper-overhaul` (worktree — 라이브 서비스는 `/home/ktg/projects/kinkeeper` main에서 계속 동작)
> 테스트: `/home/ktg/projects/kinkeeper/.venv/bin/pytest -q` (worktree 디렉터리에서 실행)

## 단계

- [x] P0. 셋업 — worktree `chore/overhaul` 생성, 테스트 통과 확인 (29 passed)
- [x] P1-감사. 완료 (2026-07-10): 원시 103건 → 중복제거 70건+2R 8건 → **확정 77 / 기각 1** (critical 2, high 22, medium 32, low 21). 상세: `docs/audit-p1-confirmed.json`
- [x] P1-수정. 완료 (2026-07-10): 10그룹 전부 커밋(26ee9f1..4be4170), 74건 수정 + 3건 정당 사유 스킵(#45 스키마 필요→P3, #23 BotFather 설정이라 문서화, #74 #28에서 기수정). 검증: 148 passed / ruff / mypy 클린.
  - **이월 백로그**: #45+#53 음력 (month,day,is_leap) 스키마 전환(P3), #51 alembic 기반 테스트 스키마(P6), #65 async 내 동기 DB(P2 판단), exactly-once 발송(SELECT FOR UPDATE/'sending' 상태 — P2 판단), upsert의 ON CONFLICT 전환(P3)
- 원계획 (10개 그룹 순차, 각 그룹 1커밋, 전체 pytest 통과 필수):
  - G1 음력/날짜 크래시 [0,2,3,4,5,24,52,54] / G2 중복발송·수명주기 [1,26,27,28,29,30,56] / G3 HTML escape [11,12,13,14,38,39,40,41,42]
  - G4a 웹 수명주기 [15,16,43,44,60,63] / G4b 웹 검증·CSRF [18,25,37,45,46,47,58,59,61,62] / G5 봇 견고성 [9,23,34,35,36,48,55,74,76]
  - G6 시간대·다이어트 [6,7,31,32,33] / G7 발송기·설정 [8,10,19,67,68,69,75] / G8 배포·마이그레이션 [20,21,49,50,70,71,72,73] / G9 잔여 [17,22,57,64,66]
  - **보류(뒤 단계로)**: #51 alembic 기반 테스트(P6), #53 윤달 지원(P2 결정), #65 async 내 동기 DB(P2 결정)
- [x] P2. 완료 (2026-07-10): 리뷰 28건 → 즉시적용 5(커밋 5c199c2) / P3행 8 / **사용자결정 5(P7에서 질문)** / 기각 4. 상세: `docs/arch-p2-plan.json`
  - P7 질문 목록: ①다이어트 기능 폐기/유지/활성화 ②horizon 60/90/365 ③음력 윤달·2/30 해당자 유무(스키마 전환 여부) ④healthz 모니터 연결 ⑤pending 마이그레이션 배포 절차
  - 의식적 수용: exactly-once 발송 미도입(at-least-once + 24h staleness + 조건부 마킹으로 충분, 가족 규모)
  - **사용자 승인(2026-07-10): P2~P6 각 단계 끝나면 다음 단계 자동 진행. 묻지 말 것. 단 P7(main 머지+라이브 재시작)만 최종 확인 받기.**
- [x] P3. 완료 (2026-07-10): 7항목 7커밋(7100f1b..5e66483) — 시간헬퍼 _time.py 통합, 잔재 추상화 제거, upsert 통일+ON CONFLICT(DO NOTHING/DO UPDATE), timezone 컬럼 제거(마이그레이션 c7f3a9e21b04 파일만), _REGISTRY 단일출처, TypedDict(config_schemas.py), job-queue extra 제거. 검증 148 passed/ruff/mypy 클린.
- 원계획: arch-p2-plan.json의 refactor_p3 중 결정 비의존 항목(시간헬퍼 통합/target_ids 제거/upsert 통일+ON CONFLICT/timezone 컬럼 제거/타입 정합화/TypedDict/job-queue extra). 결정 의존 부분(enum diet 값, horizon 값, 음력 스키마)은 P7 이후로.
- [x] P4. 완료 (2026-07-10): 2커밋(57b7ae9 죽은코드, 57fcec0 주석) — 낡은 주석 5건 정정(취소→삭제, 격주 요일), 관문 모듈 독스트링 4건, 노이즈 제거. 148 passed.
- [x] P5. 완료 (2026-07-10): 4커밋(b6aae48 README, 8c62da3 ARCHITECTURE 재작성, db3a7dd OPERATIONS 신설, web/README 현행화). 문서-코드 불일치 8건 정정(건강검진 알림 방식, 상태값 failed, 다이어트 플래그, 프로세스 3유닛 등). .env.example은 검증 결과 수정 불필요.
- [x] P6. 완료 (2026-07-10): 1커밋(8945e0d) — alembic 체인·스키마 정합·downgrade 검증 추가(148→150). notifier/config는 P1 커버 확인으로 중복 작성 안 함.
- [ ] P7. 최종 검증(전체 테스트/린트/타입) → main 머지 + 서비스 재시작 (사용자 확인 후)

## 워크플로 실행 기록

- P2 arch review run: runId `wf_9eb428cb-2d1`, 스크립트 `.../kinkeeper-arch-review-p2-wf_9eb428cb-2d1.js` — 4렌즈 리뷰(병렬 opus) → 종합 → 안전 정리 적용
- P1 fix run: runId `wf_79aee676-8bd`, 스크립트 `/home/ktg/.claude/projects/-home-ktg-projects-kinkeeper-overhaul/e7c47067-c97e-4720-bdd6-c3d6ee6c51a3/workflows/scripts/kinkeeper-fix-p1-wf_79aee676-8bd.js` — 10그룹 순차(opus), 그룹당 1커밋. 끊기면 resumeFromRunId로 재개(완료 그룹은 캐시).

- P1 audit run: runId `wf_d985770b-ba8`, 스크립트 `/home/ktg/.claude/projects/-home-ktg-projects-kinkeeper-overhaul/e7c47067-c97e-4720-bdd6-c3d6ee6c51a3/workflows/scripts/kinkeeper-bug-audit-wf_d985770b-ba8.js` (끊기면 resumeFromRunId로 재개, 저널: 같은 세션 transcript dir의 journal.jsonl)
  - 1차 실행: 파인더 12/12 완료(결과는 저널에 캐시됨) 후 세션 한도로 dedup/critic 실패 → 결과 빈 값
  - 2차(재개) 실행: 13:04 UTC 재개 — 파인더는 캐시 재생, dedup부터 라이브. 판정 에이전트(dedup/verify/critic/2라운드 파인더)는 model=opus로 라우팅

## 커밋 로그

- (진행하며 기록)

## 메모

- 서비스: systemd --user 유닛 3개(kinkeeper-bot/web/web-tailscale)가 main 체크아웃에서 실행 중 — worktree 작업은 라이브에 영향 없음.
- 알려진 이슈(감사에서 재확인 예정): scheduled_notifications 행 무한 증식(매일 rebuild가 cancel+재삽입), lunar_to_solar 윤달 미지원, 텔레그램 HTML escape 누락 의심(member.name 등).
