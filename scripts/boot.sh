#!/usr/bin/env bash
# 컨테이너 부팅 — 서버만 띄운다.
#
# ★ 예전에는 여기서 키워드 인덱스(data/keywords.db, 310MB)를 만들었다. 지웠다.
#   인덱스는 이제 본 DB(Supabase)의 kw_week·kw_meta·kw_item·kw_neighbor에 있고
#   web/api.py는 그 표만 읽는다 — 로컬 파일을 읽는 코드가 한 줄도 없다.
#   그런데도 배포마다 10분짜리 src.extract를 돌려 **아무도 안 읽는 파일**을
#   만들고 있었다. 저메모리 경로로도 726MB를 쓰므로 작은 인스턴스에서는
#   그 자체가 OOM 위험이었다.
#
#   인덱스는 GPU가 있는 PC에서 `python -m src.index_build`로 만들어 DB에 올린다.
#   컨테이너가 할 일이 아니다.

set -u
cd "$(dirname "$0")/.."

echo "[boot] 서버 시작 :${PORT:-8000}"
exec python -m uvicorn web.server:app --host 0.0.0.0 --port "${PORT:-8000}" \
     --proxy-headers --forwarded-allow-ips='*'
