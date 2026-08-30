# ai-tech-radar

사내 **AI·빅데이터팀** 트렌드 레이더. 주제는 **AI · 빅데이터 · 소상공인** 세 축.

주간 다이제스트를 메일로 밀고(push), 축적된 항목을 웹에서 검색한다(pull).
정렬 1순위는 **교차 점수** — 세 축을 많이 걸칠수록 위로 올린다. 팀의 실제 업무가
교집합에 있기 때문이고, 팀이 필터링에 쓰는 시간을 없애는 게 이 도구의 존재 이유다.

- 기획·근거: [`PLAN.md`](PLAN.md)
- 작업 규칙·원칙: [`CLAUDE.md`](CLAUDE.md)
- 다른 PC로 옮기기·현재 진행 상황: [`HANDOFF.md`](HANDOFF.md)

---

## 빠른 시작

```bash
git clone https://github.com/wjdgnsdl213/AI_tech_radar.git
cd AI_tech_radar
pip install -r requirements.txt

cp .env.example .env      # 키를 채운다 (아래 표 참고)
python -m src.db --summary
```

GPU가 있으면 **CUDA 빌드 torch를 따로** 설치한다. 임베딩이 유일한 병목이다.

```bash
pip uninstall -y torch
pip install torch --index-url https://download.pytorch.org/whl/cu126
python -c "import torch; print(torch.cuda.is_available())"
```

---

## 파이프라인

```
수집 ──→ data/raw/*.jsonl ──→ DB ──→ prefilter ──→ filter ──→ score ──→ insight ──→ digest ─┬─→ mailer
                                     축 키워드      임베딩     교차 점수   AI 해설    구성 결정  └─→ web
                                      (무료)       (비쌈)                (LLM)
```

| 단계 | 명령 | 하는 일 |
|---|---|---|
| 수집 | `python -m src.collect` | 소스 순회. 실패한 소스는 격리하고 계속 |
| 백필 | `python -m src.backfill --source hn` | 1회성 대량. 체크포인트로 재개 |
| 재적재 | `python -m src.ingest_raw` | 원본 jsonl → DB (스키마 변경·엔진 이전) |
| 축 태깅 | `python -m src.prefilter` | 축 키워드 매칭 → `item_axes` |
| 필터 | `python -m src.filter` | 시드 대비 점수 + 신디케이션 제거 |
| 점수 | `python -m src.score` | 교차 점수 → `items.cross_score` |
| 키워드 | `python -m src.extract` | 명사 n-gram → `item_keywords` |
| 급상승 | `python -m src.trend --out reports/` | 주간 급상승 키워드 + CSV |
| 해설 | `python -m src.insight` | L1 항목 해설 / L2 주간 흐름 |
| 다이제스트 | `python -m src.digest` | 구성 결정 → `digests` |
| 메일 | `python -m src.mailer --dry-run` | 짧게 발송 |
| 연관어 | `python -m src.graph --out reports/` | 브릿지 노드 = 과제 후보 |
| 웹 | `uvicorn web.server:app` | SPA 5탭 + JSON API |
| 테스트 | `pytest tests/ -q` | 핵심 판정 로직 22개 |
| 배치 | `python -m src.run_pipeline --daily` / `--weekly` | 위를 순서대로 |

**단계는 전부 독립 실행된다.** 중간부터 다시 돌려도 되고, 각 단계는 멱등이다.

### 왜 일간과 주간을 나누나
**수집은 소급되지 않는다.** 네이버 뉴스 API는 쿼리당 최근 1,000건이 상한이라
오늘 안 받으면 오늘 기사는 영영 못 받는다. 반면 필터·점수·해설은 원본만 쌓여
있으면 나중에 전부 소급 적용된다. 그래서 수집·처리는 매일, 비용이 드는 해설과
발송은 주 1회로 나눈다.

```
run_daily.bat    → run_pipeline --daily    (collect → prefilter → filter → score)
run_weekly.bat   → run_pipeline --weekly   (insight → digest → mailer)
```
Windows 작업 스케줄러에 등록한다. 등록 명령은 각 `.bat` 파일 주석에 있다.

---

## 소스

| 소스 | 축 | 백필 | 비고 |
|---|---|---|---|
| Hacker News | AI·빅데이터 | ✅ 2007~ | Algolia API, 무인증. `points>=30` 컷 |
| GeekNews | AI·빅데이터 | ⚠️ 하루 150건 | 개인 운영 사이트. **차단을 우회하지 않는다** |
| GeekNews (Wayback) | AI·빅데이터 | ✅ 28,526건 | Internet Archive 경유. 원 서버 요청 0건 |
| 네이버 뉴스 | 소상공인·AI·빅데이터·규제 | ❌ 불가 | **매일 돌려야 한다** (쿼리당 1,000건 상한) |
| sobiz DB 임포트 | 소상공인 | 1회성 | `import_sobiz` — 과거 baseline |
| 개인정보위·국회 의안 | 규제 | — | **미구현.** 공공데이터포털 API 키 필요 |

크롤링 원칙: 제목·요약·링크만 저장한다. 본문 저장·재배포하지 않는다.
`robots.txt`의 `Disallow`는 절대 접근하지 않고, 서버가 403/429로 거부하면 그 실행을
즉시 멈춘다. User-Agent 변경·프록시·간격 축소로 우회하지 않는다.

---

## 환경변수 (`.env`)

| 키 | 필요 시점 | 없으면 |
|---|---|---|
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 수집 | 네이버 소스만 실패(나머지는 계속) |
| `CONTACT_EMAIL` | 수집 | 크롤링 UA에 `contact: unknown`으로 나감 |
| `ANTHROPIC_API_KEY` | 해설 | 해설 없이 다이제스트만 나감 (fail-open) |
| `ANTHROPIC_WORKSPACE_ID` | 해설 | 조직 계정 키라면 **필수**. 없으면 전 요청 400 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `MAIL_FROM` / `MAIL_TO` | 메일 | 발송 불가 (`--dry-run`은 됨) |
| `DATA_GO_KR_KEY` | 규제 소스 | 규제 알림 소스 미구현 |
| `DATABASE_URL` | 배포 | 비우면 `sqlite:///data/radar.db` |

`.env`는 커밋하지 않는다. 한 번 커밋하면 지워도 git 이력에 영구히 남는다.

---

## 필터 품질 측정

성공 기준은 **precision ≥ 85%** (수동 라벨 50건)다.

```bash
python -m src.evaluate --make-labels   # 표본 CSV 생성 (통과 25 / 탈락 25)
# data/labels/labels.csv 의 label 열에 1(관련)/0(무관) 을 채운다. 애매하면 0
python -m src.evaluate                 # 지표 + 오류 사례
python -m src.evaluate --refresh       # 필터를 고친 뒤 판정만 다시 읽기
```

표본을 통과·탈락 반반으로 뽑는 이유: precision만 재면 임계값을 극단적으로 올려
3건만 통과시켜도 100%가 나온다. 놓친 것(recall)을 같이 봐야 한다.

`data/labels/labels_ai.csv`는 **AI가 채운 잠정 라벨**이다(`--ai`로 측정).
필터를 만든 쪽이 그 필터를 채점한 것이라 낙관적으로 편향돼 있으니 참고용으로만 쓴다.

## 테스트

```bash
pytest tests/ -q
```

임계값을 실측으로 정한 함수들(축 키워드 3종 매칭 규칙, 신디케이션 포함도, 교차 점수,
ISO 주차 연산, n-gram 조각 판정)을 덮는다. 값이 흔들려도 파이프라인은 정상 종료하므로
회귀가 조용히 지나간다 — 그래서 근거가 된 실제 사례를 그대로 테스트로 박아뒀다.

---

## 웹 화면

```
uvicorn web.server:app --port 8000
```

상단 바에 브랜드와 메뉴가 함께 있고, 본문은 좌우 2단(본문 + 사이드) 대시보드다.

| 메뉴 | 내용 |
|---|---|
| 이번 주 | AI 요약 + 칩 필터[전체·교집합·AI·빅데이터·소상공인] + 항목<br>사이드: 급상승 · 브릿지 키워드 |
| 검색 | 키워드·주제·기간 + 기간 프리셋 → 표 → **엑셀 내려받기** (S4) |
| 연관어 네트워크 | **키워드를 검색하면** 함께 언급되는 말을 방사형으로 그린다.<br>노드를 클릭하면 그 키워드가 나온 기사가 옆에 뜬다 |
| 급상승 | 비중 기준 상승폭 + 최근 5주 추이 |

지난 주차는 별도 탭이 아니라 **'이번 주' 화면의 주차 선택**으로 본다 — 날짜만 바꾸는 일에
화면을 따로 둘 이유가 없다.

연관어는 전체 지도를 미리 그려두지 않고 **검색할 때마다 그 키워드 주변만** 만든다
(`/api/ego`). 전체를 400 노드로 압축하면 어느 주제에도 안 맞는 그림이 되고 일반어가
상위를 먹는다. 계산도 훨씬 싸다. '교집합 과제 후보 발견'이라는 F9 본래 목적은
'이번 주' 사이드의 **브릿지 키워드** 목록이 계속 담당한다.

정적 파일은 수정 시각을 URL에 붙여(`app.js?v=…`) 캐시를 무효화한다. 이게 없으면
배포해도 사용자 브라우저가 옛 스크립트를 계속 쓴다.

**주차 표기**는 `2026-W35`가 아니라 `2026년 8월 4주차`로 낸다(`src/digest.week_label`).
기준일은 월요일이 아니라 **목요일**이다 — ISO 주차는 목요일이 속한 해로 정의되므로,
월요일로 잡으면 `2026-W01`이 '2025년 12월 5주차'가 되어 연초가 전년으로 밀린다.

화면에 **교차 점수·관련도 같은 내부 지표는 노출하지 않는다.** 정렬에만 쓴다 —
팀원에게 "교차 27.2"는 아무 뜻이 없다.

화면은 `web/static/`의 SPA가 그리고 서버는 `web/api.py`가 JSON만 냅니다.
**항목 선정·점수·브릿지는 전부 `src/*.py`가 정한 걸 그대로 씁니다** — 화면에서 다시
고르면 메일·CSV와 갈라지기 때문입니다.
예전 서버 렌더 화면은 `/legacy` 아래에 남겨뒀습니다.

## DB

SQLAlchemy Core만 쓴다. 드라이버 직접 호출·방언 전용 SQL을 쓰지 않아서
`DATABASE_URL` 한 줄로 엔진을 교체할 수 있다.

```
개발    sqlite:///data/radar.db
배포    postgresql+psycopg://user:pw@host:5432/aitrend
사내    oracle+oracledb://…  /  mysql+pymysql://…
```

| 테이블 | 내용 |
|---|---|
| `items` | 수집 항목. 처리 단계가 컬럼을 채워 나간다 |
| `item_axes` | 항목 × 축 (다대다) |
| `item_keywords` | 항목 × 키워드 × 주차 — 급상승·연관어 계산용 (PLAN §3-B) |
| `digests` | 주간 다이제스트 (`lead`=L2, `body`=구성) |

`published_at`(발행일)과 `collected_at`(수집일)은 반드시 분리한다 — 백필 데이터는
수집일이 오늘이고 발행일이 몇 년 전이라, 안 나누면 트렌드 계산이 전부 망가진다.
