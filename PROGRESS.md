# KinKeeper 전면 점검 진행상태 (chore/overhaul)

> 요금/세션 중단 시 이 파일을 보고 이어서 진행. 새 세션에서 "kinkeeper overhaul 이어서 해줘"라고 하면 됨.
> 작업 위치: `/home/ktg/projects/kinkeeper-overhaul` (worktree — 라이브 서비스는 `/home/ktg/projects/kinkeeper` main에서 계속 동작)
> 테스트: `/home/ktg/projects/kinkeeper/.venv/bin/pytest -q` (worktree 디렉터리에서 실행)

## 단계

- [x] P0. 셋업 — worktree `chore/overhaul` 생성, 테스트 통과 확인 (29 passed)
- [x] P1-감사. 완료 (2026-07-10): 원시 103건 → 중복제거 70건+2R 8건 → **확정 77 / 기각 1** (critical 2, high 22, medium 32, low 21). 상세: `docs/audit-p1-confirmed.json`
- [ ] P1-수정. 확정 버그 수정 — 10개 그룹 순차(각 그룹 1커밋, 전체 pytest 통과 필수):
  - G1 음력/날짜 크래시 [0,2,3,4,5,24,52,54] / G2 중복발송·수명주기 [1,26,27,28,29,30,56] / G3 HTML escape [11,12,13,14,38,39,40,41,42]
  - G4a 웹 수명주기 [15,16,43,44,60,63] / G4b 웹 검증·CSRF [18,25,37,45,46,47,58,59,61,62] / G5 봇 견고성 [9,23,34,35,36,48,55,74,76]
  - G6 시간대·다이어트 [6,7,31,32,33] / G7 발송기·설정 [8,10,19,67,68,69,75] / G8 배포·마이그레이션 [20,21,49,50,70,71,72,73] / G9 잔여 [17,22,57,64,66]
  - **보류(뒤 단계로)**: #51 alembic 기반 테스트(P6), #53 윤달 지원(P2 결정), #65 async 내 동기 DB(P2 결정)
- [ ] P2. 아키텍처 리뷰 — 안 도는 것/죽은 코드/과잉 엔지니어링 → 보고 + 안전한 것 정리
- [ ] P3. 리팩토링 (중복 제거, 단순화) + 커밋
- [ ] P4. 주석/독스트링 정비 + 커밋
- [ ] P5. 문서 — README, ARCHITECTURE, .env.example, 운영 문서 + 커밋
- [ ] P6. 테스트 보강 (누락 영역) + 커밋
- [ ] P7. 최종 검증(전체 테스트/린트/타입) → main 머지 + 서비스 재시작 (사용자 확인 후)

## 워크플로 실행 기록

- P1 fix run: runId `wf_79aee676-8bd`, 스크립트 `/home/ktg/.claude/projects/-home-ktg-projects-kinkeeper-overhaul/e7c47067-c97e-4720-bdd6-c3d6ee6c51a3/workflows/scripts/kinkeeper-fix-p1-wf_79aee676-8bd.js` — 10그룹 순차(opus), 그룹당 1커밋. 끊기면 resumeFromRunId로 재개(완료 그룹은 캐시).

- P1 audit run: runId `wf_d985770b-ba8`, 스크립트 `/home/ktg/.claude/projects/-home-ktg-projects-kinkeeper-overhaul/e7c47067-c97e-4720-bdd6-c3d6ee6c51a3/workflows/scripts/kinkeeper-bug-audit-wf_d985770b-ba8.js` (끊기면 resumeFromRunId로 재개, 저널: 같은 세션 transcript dir의 journal.jsonl)
  - 1차 실행: 파인더 12/12 완료(결과는 저널에 캐시됨) 후 세션 한도로 dedup/critic 실패 → 결과 빈 값
  - 2차(재개) 실행: 13:04 UTC 재개 — 파인더는 캐시 재생, dedup부터 라이브. 판정 에이전트(dedup/verify/critic/2라운드 파인더)는 model=opus로 라우팅

## 커밋 로그

- (진행하며 기록)

## 메모

- 서비스: systemd --user 유닛 3개(kinkeeper-bot/web/web-tailscale)가 main 체크아웃에서 실행 중 — worktree 작업은 라이브에 영향 없음.
- 알려진 이슈(감사에서 재확인 예정): scheduled_notifications 행 무한 증식(매일 rebuild가 cancel+재삽입), lunar_to_solar 윤달 미지원, 텔레그램 HTML escape 누락 의심(member.name 등).
