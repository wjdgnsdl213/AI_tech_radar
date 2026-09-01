# SAB Trend — 웹 서비스 이미지
#
# 이 이미지는 **보여주기만** 한다. 수집·임베딩 필터·AI 해설은 GPU가 있는 PC에서
# 돌고 결과가 Supabase에 쌓인다. 그래서 torch도 모델도 넣지 않는다(3GB → 400MB대).
#
# 하나만 예외로 컨테이너가 직접 만든다: 키워드 인덱스(data/keywords.db).
#   본 DB에서 6분이면 다시 만들 수 있는 파생물인데 310MB라 저장소로 못 옮긴다.
#   src.extract는 kiwipiepy만 쓰므로 이 이미지에서 그대로 돌아간다.

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
