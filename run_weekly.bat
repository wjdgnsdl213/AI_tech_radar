@echo off
REM 주간 배치 — AI 해설 + 다이제스트 + 메일 발송. 작업 스케줄러에 주 1회 등록한다.
REM
REM 등록 예시 (월요일 07:00):
REM   schtasks /create /tn "ai-tech-radar-weekly" /tr "%~dp0run_weekly.bat" /sc weekly /d MON /st 07:00
REM
REM 일간 배치(run_daily.bat)가 수집·처리를 마쳐둔 상태를 전제로 한다.
REM LLM 장애나 키 누락이면 해설만 건너뛰고 다이제스트는 그대로 나간다(fail_open).

cd /d "%~dp0"
if not exist logs mkdir logs

echo ============================================================ >> "logs\weekly.log"
echo [START] %date% %time% >> "logs\weekly.log"

REM 가상환경을 쓴다면 아래를 .venv\Scripts\python.exe 로 교체
python -m src.run_pipeline --weekly >> "logs\weekly.log" 2>&1

echo [END] %date% %time% (exit=%errorlevel%) >> "logs\weekly.log"
