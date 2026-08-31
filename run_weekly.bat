@echo off
REM ============================================================================
REM 주간 배치 - AI 해설 + 다이제스트 + 메일. 작업 스케줄러가 주 1회 부른다.
REM
REM 일간 배치가 수집·처리를 마쳐둔 상태를 전제로 한다. 그래서 일간(06:00)보다
REM 충분히 뒤인 07:30에 돈다 - 일간이 약 8분 걸리므로 겹치지 않는다.
REM 그래도 겹치면 run_pipeline의 잠금이 막고 종료 코드 0으로 조용히 건너뛴다.
REM
REM LLM 장애나 키 누락이면 해설만 건너뛰고 다이제스트는 그대로 나간다(fail_open).
REM SMTP 미설정이면 메일 단계만 실패하고 나머지는 정상 완료된다.
REM ============================================================================

cd /d "%~dp0"
if not exist logs mkdir logs

set "PY=C:\Users\wjdgn\anaconda3\python.exe"
if not exist "%PY%" set "PY=python"

for /f "tokens=1-3 delims=/- " %%a in ("%date%") do set "TODAY=%%a%%b%%c"
set "LOG=logs\weekly_%TODAY%.log"

echo ============================================================ >> "%LOG%"
echo [START] %date% %time% >> "%LOG%"
"%PY%" -m src.run_pipeline --weekly >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo [END] %date% %time% (exit=%RC%) >> "%LOG%"

forfiles /p logs /m *.log /d -30 /c "cmd /c del @path" >nul 2>&1

exit /b %RC%
