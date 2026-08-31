@echo off
REM ============================================================================
REM 일일 배치 - 수집 + 처리. Windows 작업 스케줄러가 매일 부른다.
REM
REM   등록:  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
REM   해제:  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Remove
REM
REM * 매일 돌려야 하는 이유: 수집은 소급되지 않는다.
REM   네이버 검색 API는 쿼리당 최근 1,000건이 상한이고 기간 지정이 없다.
REM   오늘 안 받으면 오늘 기사는 영영 못 받는다. 처리(축·점수·필터)는 언제든
REM   다시 적용할 수 있지만 수집만은 되돌릴 수 없다.
REM
REM 소요: 약 8분 (수집 1분 + 처리 2분 + 키워드 추출 5분)
REM ============================================================================

cd /d "%~dp0"
if not exist logs mkdir logs

REM PATH에 어떤 python이 잡힐지는 스케줄러 환경에 따라 다르다. 의존성(torch·psycopg 등)이
REM 설치된 인터프리터를 고정하고, 없으면 PATH의 python으로 넘어간다.
set "PY=C:\Users\wjdgn\anaconda3\python.exe"
if not exist "%PY%" set "PY=python"

REM 날짜별 로그 - 한 파일에 계속 붙이면 몇 달 뒤 열어보지도 못할 크기가 된다
for /f "tokens=1-3 delims=/- " %%a in ("%date%") do set "TODAY=%%a%%b%%c"
set "LOG=logs\daily_%TODAY%.log"

echo ============================================================ >> "%LOG%"
echo [START] %date% %time% >> "%LOG%"
"%PY%" -m src.run_pipeline --daily >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo [END] %date% %time% (exit=%RC%) >> "%LOG%"

REM 30일보다 오래된 로그는 지운다
forfiles /p logs /m *.log /d -30 /c "cmd /c del @path" >nul 2>&1

exit /b %RC%
