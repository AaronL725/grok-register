$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $projectRoot ".outlook-register.pid"
$stopped = $false

try {
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -in @("python.exe", "pythonw.exe") -and
            $_.CommandLine -like "*OutlookRegister*main.py*"
        }

    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped = $true
    }

    if (Test-Path -LiteralPath $pidFile) {
        $rawPid = (Get-Content -Raw -LiteralPath $pidFile).Trim()
        $launcherPid = 0
        if ([int]::TryParse($rawPid, [ref]$launcherPid) -and $launcherPid -gt 0) {
            Stop-Process -Id $launcherPid -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }

    if ($stopped) {
        Write-Host "Microsoft mailbox register stopped."
    } else {
        Write-Host "Microsoft mailbox register is not running."
    }
    exit 0
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
