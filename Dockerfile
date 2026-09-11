# SAB Trend — 웹 서비스 이미지
#
# 이 이미지는 **보여주기만** 한다. 수집·임베딩 필터·AI 해설은 GPU가 있는 PC에서
# 돌고 결과가 Supabase에 쌓인다. 그래서 torch도 모델도 넣지 않는다(3GB → 400MB대).
#
# 키워드 인덱스도 더는 여기서 만들지 않는다. kw_week·kw_meta·kw_item·kw_neighbor로
#   본 DB에 들어가 있고 web/api.py는 그 표만 읽는다. 예전엔 부팅 때 10분짜리
#   src.extract를 돌려 310MB짜리 로컬 파일을 만들었는데, 옮긴 뒤로는 아무도
#   그 파일을 읽지 않았다. 인덱스는 PC에서 `python -m src.index_build`로 만든다.

FROM python:3.12-slim

# 빌드 도구는 kiwipiepy 설치에만 필요하다. 남겨두면 이미지가 커지므로 나중에 지운다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성을 먼저 넣어 코드가 바뀌어도 이 층이 캐시되게 한다
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt \
    && apt-get purge -y build-essential && apt-get autoremove -y

COPY src/ ./src/
COPY web/ ./web/
COPY content/ ./content/
COPY config.yaml ./
COPY scripts/boot.sh ./scripts/boot.sh
RUN chmod +x scripts/boot.sh && mkdir -p data/processed data/checkpoints data/raw logs

# 파이썬 출력이 버퍼에 갇히면 배포 로그에서 진행 상황을 볼 수 없다
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
EXPOSE 8000

# 컨테이너가 살아 있는지와 **DB에 붙는지**는 다른 문제다. /healthz는 후자까지 본다.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/healthz || exit 1

CMD ["./scripts/boot.sh"]
