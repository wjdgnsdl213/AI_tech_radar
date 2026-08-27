@echo off
REM 일일 수집 배치 — Windows 작업 스케줄러에 등록해 하루 1회 실행한다.
REM %~dp0 = 이 배치 파일이 있는 폴더 → 스케줄러가 어디서 호출하든 경로가 맞는다.
REM
REM 등록 예시 (관리자 권한 명령 프롬프트):
REM   schtasks /create /tn "ai-tech-radar" /tr "%~dp0run_daily.bat" /sc daily /st 06:00
REM
REM GeekNews 과거분은 config.yaml의 daily_crawl_limit(기본 150건)만큼만 받는다.
REM 서버가 403으로 거부하면 그 실행은 즉시 멈추고 다음 날 같은 지점에서 재개한다.

cd /d "%~dp0"
if not exist logs mkdir logs

echo ============================================================ >> "logs\collect.log"
echo [START] %date% %time% >> "logs\collect.log"

REM 가상환경을 쓴다면 아래를 .venv\Scripts\python.exe 로 교체
python -m src.collect >> "logs\collect.log" 2>&1

echo [END] %date% %time% (exit=%errorlevel%) >> "logs\collect.log"
