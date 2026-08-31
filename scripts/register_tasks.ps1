<#
.SYNOPSIS
  AI 빅데이터 트렌드 — Windows 작업 스케줄러 등록/해제.

.DESCRIPTION
  일간(수집·처리)과 주간(해설·다이제스트) 배치를 등록한다.

  ★ 로그온 상태에서만 실행된다(-LogonType Interactive).
    "로그온하지 않아도 실행"으로 만들려면 계정 비밀번호를 저장해야 하는데,
    개인 PC에서 그럴 이유가 없다. 대신 StartWhenAvailable을 켜서 PC가 꺼져
    있었거나 로그온 전이었으면 **깨어난 직후 밀린 실행을 한 번 돌린다.**

  ★ 수집은 소급되지 않는다(네이버 API가 쿼리당 최근 1,000건 상한).
    PC를 며칠 꺼두면 그 며칠치 뉴스는 영영 못 받는다. StartWhenAvailable이
    복구해 주는 건 "오늘 것"까지다.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -DailyAt 07:00
  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Remove
#>
param(
    [string]$DailyAt  = "06:00",
    [string]$WeeklyAt = "07:30",
    [string]$WeeklyDay = "Monday",
    [switch]$Remove
)

# ★ 이 파일은 반드시 **UTF-8 BOM**으로 저장한다.
#   Windows PowerShell 5.1은 BOM이 없는 .ps1을 ANSI(한국어 환경은 cp949)로 읽는다.
#   그러면 아래 한글 작업 이름이 깨진 바이트가 되고, Register-ScheduledTask가
#   ERROR_INVALID_NAME(0x8007007B, "파일 이름·디렉터리 이름 또는 볼륨 레이블
#   구문이 잘못되었습니다")로 실패한다 — 원인이 인코딩이라는 걸 알기 어렵다(실측).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tasks = @(
    @{ Name = "AI-트렌드 일간수집"; Bat = "run_daily.bat" },
    @{ Name = "AI-트렌드 주간발행"; Bat = "run_weekly.bat" }
)

if ($Remove) {
    foreach ($t in $tasks) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Host "  해제: $($t.Name)"
        } else {
            Write-Host "  없음: $($t.Name)"
        }
    }
    return
}

# 배치 파일이 실제로 있는지 먼저 본다 — 없는 걸 등록하면 매일 조용히 실패한다
foreach ($t in $tasks) {
    $p = Join-Path $root $t.Bat
    if (-not (Test-Path $p)) { throw "배치 파일이 없습니다: $p" }
}

# 공통 설정
#   StartWhenAvailable  PC가 꺼져 있어 놓친 실행을 깨어난 뒤 한 번 돌린다
#   MultipleInstances   앞 실행이 안 끝났으면 새로 띄우지 않는다
#                       (run_pipeline의 잠금과 이중 안전장치)
#   ExecutionTimeLimit  멈춘 실행이 영원히 남지 않게 3시간에서 끊는다
# ★ 백틱(`) 줄바꿈을 쓰지 않는다 — 뒤에 공백 하나만 붙어도 조용히 깨지고,
#   줄바꿈 문자(LF/CRLF)에도 예민하다. 해시테이블 스플래팅이 안전하다.
$settingArgs = @{
    StartWhenAvailable       = $true   # 놓친 실행을 깨어난 뒤 한 번 돌린다
    MultipleInstances        = "IgnoreNew"  # 앞 실행이 안 끝났으면 새로 안 띄운다
    ExecutionTimeLimit       = (New-TimeSpan -Hours 3)   # 멈춘 실행을 끊는다
    AllowStartIfOnBatteries  = $true
    DontStopIfGoingOnBatteries = $true
    RestartCount             = 2
    RestartInterval          = (New-TimeSpan -Minutes 15)
}
$settings = New-ScheduledTaskSettingsSet @settingArgs

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$triggers = @{
    "AI-트렌드 일간수집" = New-ScheduledTaskTrigger -Daily -At $DailyAt
    "AI-트렌드 주간발행" = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $WeeklyDay -At $WeeklyAt
}

foreach ($t in $tasks) {
    $bat = Join-Path $root $t.Bat
    # cmd /c 로 감싸야 종료 코드가 스케줄러 '마지막 실행 결과'에 제대로 올라온다
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ('/c "' + $bat + '"') -WorkingDirectory $root
    if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
    }
    $reg = @{
        TaskName    = $t.Name
        Action      = $action
        Trigger     = $triggers[$t.Name]
        Settings    = $settings
        Principal   = $principal
        Description = "AI 빅데이터 트렌드 — $($t.Bat)"
    }
    Register-ScheduledTask @reg | Out-Null
    Write-Host "  등록: $($t.Name)  ->  $($t.Bat)"
}

Write-Host ""
Write-Host "  일간  매일 $DailyAt        수집 + 처리 (약 8분)"
Write-Host "  주간  매주 $WeeklyDay $WeeklyAt  해설 + 다이제스트 + 메일"
Write-Host ""
Write-Host "  확인:      Get-ScheduledTask -TaskName 'AI-트렌드*' | Format-Table TaskName,State"
Write-Host "  즉시 실행: Start-ScheduledTask -TaskName 'AI-트렌드 일간수집'"
Write-Host "  로그:      logs\daily_YYYYMMDD.log"
