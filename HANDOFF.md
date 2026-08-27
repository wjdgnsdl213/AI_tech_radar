# 다른 컴퓨터로 옮기기

> 2026-08-27 기준. 옮긴 뒤 이 문서대로 하면 하던 데서 그대로 이어진다.

---

## 0. 새 컴퓨터에서 3분 만에 시작하기

```bash
git clone https://github.com/wjdgnsdl213/AI_tech_radar.git
cd AI_tech_radar
pip install -r requirements.txt

# 아래 3개를 옛 PC에서 수동 복사 (git에 없음)
#   .env
#   data/radar.db
#   data/raw/*.jsonl
#   data/checkpoints/*.json

python -m src.db --summary        # 이관 확인
python -m src.collect             # 일일 수집 (RSS + GeekNews 과거분 150건)
```

**코드는 GitHub에 있다.** 데이터·비밀키만 따로 옮기면 된다.

---

## 1. 무엇을 옮기고 무엇을 버리나

| 대상 | 크기 | 옮기나 | 이유 |
|---|---|---|---|
| `src/`, `config.yaml`, `seeds/`, `*.md` | 작음 | ✅ **git** | 코드·설정 |
| **`.env`** | 699B | ✅ **수동 복사** | API 키. git에 안 올라감(의도적) |
| **`data/radar.db`** | 36MB | ✅ **수동 복사** | 수집 데이터 49,543건 |
| **`data/raw/*.jsonl`** | 6.6MB | ✅ **수동 복사** | 원본. 스키마 바뀌면 여기서 재적재 |
| **`data/checkpoints/`** | 9KB | ✅ **수동 복사** | 백필 재개 지점 |
| `data/trends.db` | 36MB | ❌ **불필요** | 구 스키마. `radar.db`로 이미 이관 완료 |
| `logs_*.txt` | 작음 | ❌ 불필요 | 작업 로그 |
| HuggingFace 모델 캐시 | **4.7GB** | ❌ 불필요 | 새 PC에서 자동 재다운로드(bge-m3 약 2.2GB) |

**결론: 폴더 통째로 복사해도 되고(78MB), `data/trends.db`만 빼면 42MB다.**
가장 간단한 방법은 `ai_tech_radar` 폴더 전체를 USB나 클라우드로 복사하는 것.

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
전체 항목  49,543 건
  sobiz_news    39,197   2025-09-16 ~ 2026-08-25
  hackernews    10,070   2023-08-27 ~ 2026-08-26
  geeknews         276   2026-08-18 ~ 2026-08-26
```

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
pip install torch --index-url https://download.pytorch.org/whl/cu124
```
(CUDA 버전은 `nvidia-smi`로 확인 후 맞춰서. cu121/cu124 등)

확인:
```bash
python -c "import torch; print('GPU:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

`sentence-transformers`는 GPU가 잡히면 자동으로 쓴다. 코드 수정 불필요.

---

## 4. 하던 작업 이어가기

### 방금까지 한 것
- 1주차 완료: 소스 어댑터 / 백필(체크포인트) / DB(SQLAlchemy) / 원본 재적재
- **시드 진단 완료** — 아래 결론이 나왔다

### 바로 적용해야 할 진단 결과
`config.yaml`에 아직 반영 안 된 값:
```yaml
filter:
  threshold: 0.55        # 현재 0.50 → 너무 낮음(37.7% 통과, 경계선이 전부 무관한 항목)

score:
  axis_count_weight: {1: 0.5, 2: 3.0, 3: 8.0}   # 1축 항목을 더 억제
```

근거(600건 표본):
| 임계값 | 통과율 |
|---|---|
| 0.50 | 37.7% ← 현재. 노이즈 과다 |
| **0.55** | **11.3% ← 권장** |
| 0.60 | 3.8% (너무 빡빡) |

### 다음 작업 (2주차)
1. `src/prefilter.py` — 축 키워드 매칭(무료). **임베딩 전에 볼륨을 줄이는 게 필수**
2. `src/filter.py` — 시드 centroid 임베딩 필터
3. `src/score.py` — 교차 점수 + `item_axes` 태깅
4. **임베딩 결과 DB 캐싱** — 세션이 끊겨도 재계산 안 하게 (CPU에서 특히 중요)
5. 수동 라벨 50건으로 precision 측정 → 목표 ≥ 85%

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

### 백그라운드 실행은 세션과 함께 죽는다
긴 작업(임베딩·백필)은 터미널을 닫으면 끊긴다. 체크포인트가 있는 백필은 재개되지만,
임베딩은 아직 캐싱이 없어 처음부터다. → 2주차 4번 항목이 그래서 필요하다.

### DB 엔진은 아직 미정
지금은 SQLite. 배포 시 `.env`의 `DATABASE_URL` 한 줄로 PostgreSQL 등으로 교체 가능하게
추상화(SQLAlchemy Core)해뒀다. **사내 공용 DB 서버가 있는지 확인 필요.**

---

## 6. 미결정 사항 (PLAN.md §13과 동일)

| # | 항목 | 언제까지 |
|---|---|---|
| 1 | 팀 과제 목록·기술 스택 → 시드 고도화 | 범용으로 진행 가능. 첫 다이제스트 후 피드백으로 대체 |
| 2 | 웹 배포 위치 (사내 서버?) | 3주차 |
| 3 | 사내 SMTP 사용 가능 여부 | 3주차 |
| 4 | 사내 공용 DB 서버 유무 | 배포 전 |
| 5 | HN `story_text` 500자 | **현행 유지로 결정됨** |
