# KinKeeper 전면 점검 진행상태 (chore/overhaul)

> 요금/세션 중단 시 이 파일을 보고 이어서 진행. 새 세션에서 "kinkeeper overhaul 이어서 해줘"라고 하면 됨.
> 작업 위치: `/home/ktg/projects/kinkeeper-overhaul` (worktree — 라이브 서비스는 `/home/ktg/projects/kinkeeper` main에서 계속 동작)
> 테스트: `/home/ktg/projects/kinkeeper/.venv/bin/pytest -q` (worktree 디렉터리에서 실행)

## 단계

- [x] P0. 셋업 — worktree `chore/overhaul` 생성, 테스트 통과 확인 (29 passed)
- [ ] P1. 버그/로직버그 전수 감사 (멀티에이전트 워크플로) → 확정 버그 수정 + 커밋
- [ ] P2. 아키텍처 리뷰 — 안 도는 것/죽은 코드/과잉 엔지니어링 → 보고 + 안전한 것 정리
- [ ] P3. 리팩토링 (중복 제거, 단순화) + 커밋
- [ ] P4. 주석/독스트링 정비 + 커밋
- [ ] P5. 문서 — README, ARCHITECTURE, .env.example, 운영 문서 + 커밋
- [ ] P6. 테스트 보강 (누락 영역) + 커밋
- [ ] P7. 최종 검증(전체 테스트/린트/타입) → main 머지 + 서비스 재시작 (사용자 확인 후)

## 워크플로 실행 기록

- P1 audit run: (실행 후 기록)

## 커밋 로그

- (진행하며 기록)

## 메모

- 서비스: systemd --user 유닛 3개(kinkeeper-bot/web/web-tailscale)가 main 체크아웃에서 실행 중 — worktree 작업은 라이브에 영향 없음.
- 알려진 이슈(감사에서 재확인 예정): scheduled_notifications 행 무한 증식(매일 rebuild가 cancel+재삽입), lunar_to_solar 윤달 미지원, 텔레그램 HTML escape 누락 의심(member.name 등).
