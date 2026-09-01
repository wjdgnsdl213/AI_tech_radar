# 다른 컴퓨터로 옮기기

> 2026-08-31 기준. 옮긴 뒤 이 문서대로 하면 하던 데서 그대로 이어진다.

---

## 0. 새 컴퓨터에서 3분 만에 시작하기

```bash
git clone https://github.com/wjdgnsdl213/AI_tech_radar.git
cd AI_tech_radar
pip install -r requirements.txt

# 수동으로 옮길 건 .env 하나뿐이다 (git에 없음 — 의도적)
#   DATABASE_URL을 기존 컴퓨터와 "똑같이" 넣는 게 핵심 (아래 참조)

python -m src.db --summary        # 접속 확인 — 82,103건 안팎이 보이면 정상
python -m src.collect             # 일일 수집 (소스 순회)
```

**DB는 이제 Supabase(Postgres)에 있다 — git에는 코드만 있다.**
`data/radar.db`는 더 이상 버전 관리하지 않는다(2026-08-31 전환, §1 참조).
따로 옮길 건 `.env` 하나뿐이고, 그 안의 `DATABASE_URL`이 진짜 핵심이다.

> ⚠️ `.env`는 절대 커밋하지 않는다. API 키·SMTP 비밀번호·DB 접속정보가 들어 있고,
> 한 번 커밋하면 나중에 지워도 git 이력에 영구히 남는다.

---

## 1. 무엇을 옮기고 무엇을 버리나

| 대상 | 옮기나 | 이유 |
|---|---|---|
| `src/`, `config.yaml`, `seeds/`, `*.md` | ✅ **git** | 코드·설정 |
| **`.env`의 `DATABASE_URL`** | ⚠️ **수동 복사(필수)** | 이게 없으면 로컬 빈 sqlite로 떨어진다 |
| **`.env`의 나머지 키** | ⚠️ **수동 복사** | API 키·SMTP 비밀번호 |
| `data/radar.db` | ❌ **git 추적 안 함(2026-08-31~)** | 아래 참조 |
| `data/raw/*.jsonl`, `data/checkpoints/` | ❌ **git 추적 안 함** | 각 컴퓨터의 로컬 크롤링 이력일 뿐 |
| `data/trends.db` | ❌ 불필요 | 구 스키마. 이미 이관 완료 |
| HuggingFace 모델 캐시 | ❌ 불필요 | 새 PC에서 자동 재다운로드(bge-m3 약 2.2GB) |

**결론: `git clone` + `.env`(특히 `DATABASE_URL`) 수동 복사면 끝이다.**

### ⚠️ 2026-08-31: DB를 로컬 sqlite → Supabase(Postgres) 공유 DB로 전환했다
그 전까지는 컴퓨터 두 대가 각자 로컬 `radar.db`를 만들고 git으로 주고받는 방식이었다.
문제가 두 가지였다:
  1. **크기** — DB가 계속 커져서 65MB까지 갔고, GitHub는 100MB에서 푸시 자체를 막는다.
  2. **병합 충돌** — 두 컴퓨터가 각자 세션에서 동시에 DB를 바꾸면 git이 바이너리라
     자동 병합을 못 한다. 실제로 한 번 겪었고(GeekNews Wayback 백필 커밋 vs
     score/insight 파이프라인 커밋), 수작업으로 두 DB를 표 단위로 합쳐야 했다.

지금은 두 컴퓨터가 **같은 Postgres를 실시간으로** 본다. 병합이라는 개념 자체가 없어졌다.
`db.py`가 SQLAlchemy Core로 엔진 비종속 설계돼 있었던 덕에 `DATABASE_URL` 한 줄
교체로 끝났다(코드 변경 없음). 기존 sqlite 데이터(82,103건, item_axes·digests
포함)는 ID를 보존하며 전량 이전했다.

**새 컴퓨터를 설정할 때 `DATABASE_URL`을 빼먹으면** `config.yaml`의 기본값
(`sqlite:///data/radar.db`)으로 조용히 떨어져서, 그 컴퓨터만 빈 로컬 DB로
따로 놀게 된다. 반드시 기존 컴퓨터의 `.env`에서 `DATABASE_URL` 값을 그대로
복사해 넣을 것.

---

## 2. 새 컴퓨터에서 할 일

### ① 코드 배치
폴더를 통째로 복사했으면 이 단계는 건너뛴다.
git 원격을 쓸 거면:
```bash
git clone <원격주소> ai_tech_radar
cd ai_tech_radar
# 그 다음 data/ 와 .env 를 수동 복사
```

### ② 파이썬 환경
```bash
python --version          # 3.11 이상 권장 (기존 환경은 3.14.5)
pip install -r requirements.txt
```

### ③ `.env` 확인
`.env.example`을 복사해 만들었다면 아래 값이 채워져 있어야 한다:
```
NAVER_CLIENT_ID / NAVER_CLIENT_SECRET     # 소상공인 축 수집
ANTHROPIC_API_KEY                          # AI 해설(3주차)
CONTACT_EMAIL                              # 크롤링 User-Agent에 들어감
DATABASE_URL                               # 비워두면 sqlite:///data/radar.db
```

### ④ 동작 확인
```bash
python -m src.db --summary
```
아래처럼 나오면 정상 이관이다:
```
전체 항목  64,433 건
  sobiz_news    39,197   2025-09-16 ~ 2026-08-25
  hackernews    10,264   2023-08-27 ~ 2026-08-30
  geeknews       9,623   2025-02-12 ~ 2026-08-30
  naver_news     5,349   2026-08-27 ~ 2026-08-30
```
(날짜가 지났으면 그만큼 더 쌓여 있는 게 정상이다)

---

## 3. ⚡ 새 PC가 GPU가 있다면 (중요)

**이 프로젝트의 유일한 성능 병목은 임베딩이다.** 옮기는 이유이기도 하다.

```
현재 CPU:  600건 → 약 2.5분   →  전체 49,543건이면 약 3.5시간
GPU 사용시:                       약 5~15분 (10~30배)
```

GPU가 있으면 **CUDA 빌드 torch를 따로 설치**해야 한다. 그냥 `pip install torch`는 CPU 버전이다:

```bash
pip uninstall -y torch
pip install torch --index-url https://download.pytorch.org/whl/cu126
```
(CUDA 버전은 `nvidia-smi`로 확인 후 맞춰서. 이 PC는 드라이버 CUDA 13.1 / RTX 3070 →
 cu126으로 설치했고 `torch 2.13.0+cu126`이 GPU를 정상 인식했다. 실측 임베딩 1,000건/2초)

확인:
```bash
python -c "import torch; print('GPU:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

`sentence-transformers`는 GPU가 잡히면 자동으로 쓴다. 코드 수정 불필요.

---

## 4. 하던 작업 이어가기

### 진행 상황 (2026-08-30 기준)

**MVP 파이프라인이 끝에서 끝까지 이어졌다.**

```
수집 → prefilter → filter → score → insight → digest → mailer + web
 ✅      ✅         ✅       ✅       ✅*       ✅        ✅
                                      * ANTHROPIC_API_KEY가 없어 dry-run까지만 검증
```

| 단계 | 파일 | 상태 |
|---|---|---|
| 수집 | `collect.py` + `sources/{hackernews,geeknews,geeknews_wayback,naver_news}.py` | ✅ 소스 순회 + 실패 격리 |
| 축 태깅 | `prefilter.py` | ✅ 64,433건 5초 |
| 임베딩 필터 | `filter.py` | ✅ 부정 시드 대비 점수 + 캐시 + 군집 중복제거 |
| 교차 점수 | `score.py` | ✅ |
| AI 해설 | `insight.py` | ✅ L1 238건 + L2. 실행당 약 $0.57 |
| 다이제스트 | `digest.py` | ✅ 한 번 생성 → 메일/웹 공통 |
| 메일 | `mailer.py` | 🟡 코드 완성, SMTP 미설정 |
| 웹 | `web/server.py` | ✅ /, /digest/{week}, /search, /item/{id}, /weeks |
| 배치 | `run_pipeline.py`, `run_daily.bat`, `run_weekly.bat` | ✅ |

**데이터**: 64,433건 (sobiz 39,197 / HN 10,264 / GeekNews 9,623 / 네이버 5,349)
필터 통과 3,046건. 3축 교집합 101건.

### 필터 품질 — 목표 미달 상태다

| 시점 | precision | 비고 |
|---|---|---|
| 초기 | **0.240** | 오통과 19건 중 17건이 `ai` 축 단독 일반 AI 뉴스 |
| 부정 시드 도입 후 | **0.600** | 목표 0.85에는 아직 못 미침 |

⚠️ **이 수치는 AI가 채운 잠정 라벨 기준이다**(`data/labels/labels_ai.csv`).
필터를 만든 쪽이 그 필터를 채점한 것이라 낙관적으로 편향돼 있다.
`data/labels/labels.csv`(사람용, 비어 있음)를 채워 재측정해야 한다.

```bash
# labels.csv 의 label 열에 1(관련)/0(무관)을 채운 뒤
python -m src.evaluate                 # 사람 라벨 기준
python -m src.evaluate --ai            # AI 잠정 라벨 기준 (참고용)
python -m src.evaluate --refresh       # 필터를 바꾼 뒤 판정만 다시 읽기
```

### 다음 레버는 시드다
`seeds/seed_sentences.txt`와 `seeds/team_profile.md`가 아직 TODO 초안이다.
PLAN §13-1이 "가장 중요한 미결정 사항"으로 꼽은 항목이고, 실제로 precision을
가장 크게 움직일 입력이다. 팀 과제 목록·사용 기술·최근 보고서 제목만 있어도 된다.

### 사람이 해야 하는 것 (막혀 있음)

| # | 항목 | 없으면 |
|---|---|---|
| 1 | ~~`ANTHROPIC_API_KEY`~~ | ✅ 완료 — 해설 동작 확인 |
| 2 | ~~`CONTACT_EMAIL`~~ | ✅ 완료 |
| 3 | `.env`에 SMTP 4종 | 메일 push 불가 |
| 4 | `labels.csv` 50건 라벨링 | precision 실측 불가 |
| 5 | 시드·팀 프로파일 실제 내용 | 필터 품질이 초안 수준에 머묾 |
| 6 | `DATA_GO_KR_KEY` | 규제 1차 출처 어댑터 (아래) |

## 📊 사람 라벨 50건 실측 (2026-09-01) — 임베딩 필터가 값을 못 하고 있다

사람이 라벨한 50건(관련 27 / 무관 23)으로 처음 제대로 쟀다. 그동안의 0.600은
AI가 만든 잠정 라벨 위의 숫자였다.

| 규칙 | precision | recall | 비고 |
|---|---|---|---|
| **지금 필터(임베딩)** | **0.560** | **0.519** | 95% 구간 0.37~0.73 |
| 축 1개 이상 (필터 없음) | 0.605 | 0.963 | 95% 구간 0.46~0.74 |
| 축 2개 이상 | 0.800 | 0.148 | 표본 5건뿐 |
| 축 1개 이상 & HN 제외 | 0.657 | 0.852 | |

**필터를 끄는 쪽이 precision도 같거나 높고 recall은 두 배다.**
precision 차이(0.56 vs 0.605)는 표본이 작아 구별되지 않지만, recall 차이
(0.519 vs 0.963)는 명백하다 — 관련 있는 것의 절반을 버리면서 정확도는 못 얻고 있다.

### 시드를 고쳐도 안 된다 (교차검증으로 확인)
사람 라벨로 시드를 만들어 **반대쪽 절반**을 재봤다(2겹):
    라벨로 만든 시드  AUC 0.677
    지금 초안 시드    AUC 0.683
같다. 시드 품질 문제가 아니라 **접근이 이 구분을 못 담는다.**

### 왜인가
사람이 가른 경계가 주제가 아니라 **성격**이었다:
  관련 : AIDC 특별법, AI 판사 제도, 농식품 빅데이터 클라우드, 장애인고용공단 실증,
         Apache Iceberg, OpenAI 커머스 제휴 — 기관·정책·산업의 움직임
  무관 : Cursor for Userscripts, Microsoft Amplifier, local LLM 빠르게,
         "코딩이 해결됐다면…", 낙관주의 에세이 — 개발자 개인의 도구·의견
둘 다 주제는 AI다. 문장 임베딩은 주제를 담지 축의 성격을 담지 않는다.

### 실제로 듣는 신호는 축 개수다
    0축 14% · 1축 58% · 2축 75% · 3축 100%  (관련 비율)
CLAUDE.md의 "축을 많이 걸칠수록 높게 띄운다"는 전제가 라벨로 확인됐다.
다이제스트는 이미 cross_score로 정렬하므로 지면 위쪽은 이 숫자보다 낫다.

### 선택지 (사용자 결정 필요)
1. 임베딩 필터를 끄고 축 1개 이상 + cross_score 정렬 — 지금 코드로 바로 됨
2. 필터를 LLM 분류로 교체 — Haiku로 후보 4.8만 건에 약 $50, 위 경계는 프롬프트로
   설명 가능해 보인다
3. 그대로 두고 지면 위쪽(2축 이상)만 신뢰 — 2축 이상은 precision 0.800

⚠️ n=50이라 구간이 넓다. 어느 쪽으로 가든 라벨을 더 모으면 판단이 단단해진다.

### ⚠️ F7 규제 알림은 소스가 0개다 (MVP인데 미해결)
판정 방식은 **소스 기반**으로 확정했고 배선(`sources.<name>.regulatory: true` →
`collect.py:_stamp_regulatory`)도 끝났다. 그런데 붙일 소스가 없다. 조사 결과:

| 후보 | 상태 |
|---|---|
| 개인정보위 | RSS 없음. `robots.txt`가 `/bbs/`(고시·보도자료 위치)를 크롤러에 금지. **크롤링하지 않는다** |
| 국회 의안정보 OPEN API | API 키 필요 (미보유) |
| 공공데이터포털 | `DATA_GO_KR_KEY` 필요 (미보유) |

→ **공공데이터포털에서 키를 발급받는 게 유일한 정공법이다.** 무료·즉시 발급.
   키가 생기면 `sources/pipc.py`·`sources/assembly.py`를 붙인다(config에 자리 있음).

### ⚠️ 환경 함정 — brotli 1.0.9
anthropic SDK 1.x는 httpx2를 쓰는데, httpx2의 Brotli 디코더가
`Decompressor.process(data, output_buffer_limit=...)`를 호출한다. brotli 1.0.9의
`process()`는 키워드 인자를 안 받아서 **모든 응답이 APIConnectionError로 터진다.**
응답 압축 해제 단계에서 나는 오류라 네트워크 문제처럼 보이는 게 함정이다(curl은 된다).
  pip install -U brotli      # 1.2.0 이상
requirements.txt에 하한을 박아뒀다.

### 미결정
- **HN 백필 10,070건 재수집** — Algolia 쿼리 버그 시기 수집분이라 오염돼 있고
  무엇을 놓쳤는지도 모른다. 지금 설정(`queries: []`)으로 3년치를 다시 받으면
  약 9만 건. 기존 항목은 삭제되지 않으므로(upsert DO NOTHING) 추가만 된다.
- 넓은 네이버 쿼리 5개가 3일치로 1,000건 상한에 도달 — 연 65만 건 규모.
  볼륨이 부담되면 이 쿼리부터 좁힌다.

---

## 5. 주의사항

### GeekNews는 "하루 조금씩" 방식으로 수집한다
`python -m src.collect`를 **하루 1회** 실행한다 (`run_daily.bat` + 작업 스케줄러).

| 경로 | 양 | 성격 |
|---|---|---|
| RSS 전방 수집 | 매일 50건(피드 전체) | 배포용 공개 채널. 부담 없음 |
| 과거분 보충 | 하루 **150건** (`daily_crawl_limit`) | 1.5초 간격, 약 4분 |

**이력**: 2026-08-26에 연속 수집하다 약 290건 지점에서 403이 걸렸다.
당시 코드가 403을 '실패한 항목'으로 보고 **199회를 더 두드린 게 진짜 문제**였다.
지금은 403/429를 받으면 그 실행을 즉시 중단하고 커서를 남긴 뒤 다음 날 재개한다.
150건/일로 실측한 결과 차단 없이 137건이 수집됐다.

> ⚠️ **차단을 우회하지 않는다.** User-Agent 변경·프록시·간격 축소는 하지 않는다.
> 403이 자주 뜨면 `daily_crawl_limit`을 **낮춘다**. 서버가 거부하면 멈추는 게 전제다.

누적 예상: 150건/일 × 30일 ≈ 4,500건 ≈ GeekNews 4~5개월치.
더 빨리 받아야 하면 운영자에게 문의하는 게 맞는 순서.

### `import_sobiz.py`는 외부 경로에 의존한다
```yaml
import_sobiz:
  db_path: "../news_keyword/data/trends.db"
```
**이미 임포트를 마쳤으므로(39,197건) 새 PC에 `news_keyword` 폴더가 없어도 된다.**
나중에 소상공인 축을 갱신하려면 그때 sobiz 프로젝트도 같이 옮기거나 경로를 맞춘다.

### 백그라운드 실행은 세션과 함께 죽는다 — 다만 이제 재개된다
긴 작업(임베딩·백필)은 터미널을 닫으면 끊긴다. 하지만 양쪽 다 재개 경로가 있다:
  · 백필   `data/checkpoints/*.json` 커서 → 같은 명령으로 이어서
  · 임베딩 `data/processed/embeddings_*.npz` → 배치마다 저장, 다음 실행은 신규분만
임베딩 캐시는 gitignore 대상이다(재생성 가능하고 4만 건에 약 90MB라 커밋하면
radar.db와 함께 레포가 급격히 무거워진다).

### DB 엔진 — Supabase(Postgres)로 확정 (2026-08-31)
`.env`의 `DATABASE_URL`이 진실 소스다. 사내 DB 서버가 나중에 생기면 그때 다시
한 줄만 바꾸면 된다(SQLAlchemy Core라 코드 변경 없음).

---

## 6. 미결정 사항 (PLAN.md §13과 동일)

| # | 항목 | 언제까지 |
|---|---|---|
| 1 | 팀 과제 목록·기술 스택 → 시드 고도화 | 범용으로 진행 가능. 첫 다이제스트 후 피드백으로 대체 |
| 2 | 웹 배포 위치 (사내 서버?) | 3주차 |
| 3 | 사내 SMTP 사용 가능 여부 | 3주차 |
| 4 | ~~사내 공용 DB 서버 유무~~ | ✅ 완료 — Supabase(Postgres)로 확정. 사내 DB 생기면 재검토 |
| 5 | HN `story_text` 500자 | **현행 유지로 결정됨** |
