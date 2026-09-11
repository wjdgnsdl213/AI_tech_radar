# Reviews and research workspace implementation plan

**Goal:** 승인된 진행 중 월간 리뷰, 주간 비교 리뷰, 스크랩·메모, 이슈 추적, 보고용 묶음, 과제 검토를 구현한다.

**Architecture:** FastAPI + SQLAlchemy의 기존 읽기 경로를 유지한다. 직접 작성한 리뷰는 `content/reviews/*.json`에서 읽으며 날짜별 집계는 DB에서 계산한다. 로그인 없는 서비스의 개인 작업 데이터는 브라우저 저장소에 저장하고 JSON 백업·병합 복원과 Markdown/인쇄용 HTML 내보내기를 제공한다.

**Tech Stack:** Python, SQLAlchemy Core, FastAPI, plain JavaScript/CSS; 추가 런타임 의존성 없음.

**Spec:** 사용자가 2026-09-11 대화에서 앞선 제안 전체와 API 없는 직접 리뷰 작성을 승인했다. 이 문서가 세부 설계를 함께 기록한다.

## Global constraints

- Claude API를 호출해 새 리뷰를 생성하지 않는다. 기존 기사·법령 요약 설정은 별도로 유지한다.
- 리뷰 조회는 읽기 전용. 직접 작성한 해설은 파일 우선, 기존 DB 해설은 과거 자료로 보존한다.
- 진행 중 기간은 KST 달력 기준 시작일부터 현재까지. 비교는 직전 기간의 동일 경과시간까지로 제한한다.
- 해설 기준일·데이터 기준일·진행/마감/확정 상태를 구분하고, 해설 없는 기간도 집계를 표시한다.
- 리뷰 근거는 실제 기사 ID·제목·원문 링크. 발표/보도 사실과 팀에 대한 해석을 구분한다.
- 개인 저장은 이 브라우저 범위임을 표시한다. 실패 시 저장 성공을 표시하지 않는다. 복원은 검증 후 병합하며 기존 메모를 덮어쓰지 않는다.
- URL은 http/https만 허용하고 입력·리뷰 텍스트는 HTML escape한다.

## Task 1: Review data and zero-API publication

- [x] `tests/test_reviews.py`: 월 경계, ISO 연말 주차, 부분 기간 비교, 자료 없는 현재 월, 직접 작성 리뷰 우선, 잘못된 리뷰 입력 검증을 실제 SQLite 데이터로 테스트한다.
- [x] `src/reviews.py`: `period_window(kind, period, now)`, `read_editorial(period)`, `review_data(engine, kind, period, now)`, `issue_data(engine, query, days)`를 구현한다.
- [x] `web/reviews_api.py`: `/api/reviews`, `/api/reviews/periods`, `/api/issues`, `/api/task-candidates` 읽기 엔드포인트와 명시적 422 검증을 구현한다.
- [x] `src/digest.py`, `src/report.py`, `web/api.py`: 직접 작성 리뷰를 같은 원천에서 읽도록 연결하고 기존 과제 정보를 저장 시 보존한다.
- [x] `src/insight.py`, `config.yaml`: 리뷰 생성 모드를 manual로 하고 L2/L3·주간 과제 API 호출을 차단한다. L1에는 영향이 없다.
- [x] 실제 기사·기존 주간 요약을 읽고 현재 주, 지난 주, 이번 달 리뷰 JSON을 작성한다. 네트워크 조회는 승인된 읽기 작업만 수행한다.

## Task 2: Personal research workspace

- [x] `tests/workspace.test.cjs`: 저장/수정 후 메모 유지, 저장 실패의 원자성, 복원 검증·병합, unsafe URL 제거, 내보내기 선택 범위를 테스트한다.
- [x] `web/static/workspace-store.js`: 버전 있는 개인 상태, 스크랩/메모/주제, 이슈 구독, 과제 상태/담당자/의견/이력을 구현한다.
- [x] `web/static/workspace.js`, `workspace.css`: 리뷰 주/월 선택, 진행 상태·기간 비교, 근거 및 스크랩; 이슈 검색과 날짜순 목록; 보관함 필터/편집/선택/백업/복원; 선택 묶음 HTML/Markdown; 과제 후보 가져오기와 상태별 검토 보드를 구현한다.
- [x] `web/static/index.html`, `app.js`: 메뉴·라우트·기사 상세와 스크랩 버튼을 연결한다. 기존 탐색/기간 선택을 유지한다.

## Task 3: Verification and handoff

- [x] `python -m pytest tests/test_reviews.py -q` 및 기존 핵심 테스트 실행.
- [x] `node --test tests/workspace.test.cjs`; JS syntax 검사.
- [x] 읽기 전용 실제 DB API smoke와 로컬 브라우저 데스크톱/모바일 QA. 저장/편집/새로고침/이슈 추적/과제 상태 변경/보고 미리보기 확인. 백업·병합 복원과 선택 내보내기는 저장 모듈 테스트로 확인.
- [x] README에 갱신 방식, 개인 저장 범위, 백업/복원, 기존 L1 API 사용 범위를 기록한다.
- [x] 직접 작성 리뷰 근거와 날짜 검증, git diff 검토. 배포는 실행하지 않으며 로컬 테스트 서버에서 확인한다.

## Verification notes

- 2026-09-11: Python 42 tests, Node 7 tests passed. 3 editorial files reference 10 unique source IDs; all URLs match the source database exactly.
- Read-only smoke covers weekly/monthly reviews, period lists, issue timelines, task candidates, and HTML/Markdown monthly reports; malformed periods return 422.
- Browser: review/article save, note edit + reload, task add/status/owner/comment, issue query/follow, selected HTML preview verified. Mobile 390px viewport verified with no horizontal overflow; added a reachable mobile menu.
- In-app browser's download event timed out for the client Blob download, without console errors. Exported content is verified in module tests and the same HTML is visually checked in the in-app preview; OS file-save completion was not verified.
