#!/usr/bin/env bash
# 컨테이너 부팅 — 키워드 인덱스를 확인하고 서버를 띄운다.
#
# ★ 인덱스가 없다고 부팅을 막지 않는다.
#   data/keywords.db는 연관어·급상승·기관 세 화면만 쓴다. 없으면 그 화면이
#   비어 나올 뿐 나머지는 정상이다(실측). 6분짜리 생성을 기다리느라 배포가
#   멈추면, 정작 멀쩡한 화면들까지 그동안 못 본다.
#   그래서 **서버를 먼저 띄우고 인덱스는 뒤에서 만든다.**
#
# ★ 볼륨이 붙어 있으면 재배포해도 인덱스가 남는다. 그때는 건너뛴다.

set -u
cd "$(dirname "$0")/.."

DB="data/keywords.db"
PORT="${PORT:-8000}"

build_index() {
    if [ -f "$DB" ] && [ "$(stat -c%s "$DB" 2>/dev/null || echo 0)" -gt 10000000 ]; then
        echo "[boot] 키워드 인덱스 있음 ($(du -h "$DB" | cut -f1)) — 건너뜀"
        return 0
    fi
    echo "[boot] 키워드 인덱스 생성 시작 (약 10분, 그동안 연관어·급상승·기관은 빈 화면)"
    echo "[boot]   --low-memory: 두 번 훑어 메모리를 아낀다. 기본 경로는 최대 924MB를"
    echo "[boot]   쓰는데 작은 인스턴스에서는 그대로 OOM으로 죽는다(실측)."
    # ★ 표준출력으로 흘린다. 파일에 넣으면 컨테이너에서 볼 방법이 없다 —
    #   호스트 로그는 stdout만 보여준다. 실패해도 원인을 못 찾는다.
    # ★ pipefail이 없으면 파이프의 **마지막** 명령(sed)의 종료 코드를 보게 되어
    #   python이 죽어도 성공으로 읽힌다. 실패를 놓치는 전형적인 함정이다.
    set -o pipefail
    if python -m src.extract --scope all --min-df 2 --low-memory 2>&1 | sed "s/^/[extract] /"; then
        echo "[boot] 키워드 인덱스 생성 완료"
    else
        # 실패해도 서버는 계속 돈다 — 세 화면만 비고 나머지는 멀쩡하다
        echo "[boot] ⚠ 키워드 인덱스 생성 실패 — 위 [extract] 줄을 확인하세요."
        echo "[boot]   메모리가 부족하면 여기서 죽습니다(8만 5천 건을 훑습니다)."
    fi
}

build_index &        # 뒤에서 만든다

echo "[boot] 서버 시작 :$PORT"
exec python -m uvicorn web.server:app --host 0.0.0.0 --port "$PORT" \
     --proxy-headers --forwarded-allow-ips='*'
