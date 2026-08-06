$ErrorActionPreference = "Stop"

$outlookRoot = "G:\OutlookRegister"
$python = Join-Path $outlookRoot ".venv\Scripts\python.exe"
$registerScript = Join-Path $outlookRoot "main.py"
$pidFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) ".outlook-register.pid"

try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "OutlookRegister Python environment was not found: $python"
    }
    if (-not (Test-Path -LiteralPath $registerScript)) {
        throw "OutlookRegister script was not found: $registerScript"
    }

    $running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -in @("python.exe", "pythonw.exe") -and
            $_.CommandLine -like "*OutlookRegister*main.py*"
        } |
        Select-Object -First 1
    if ($running) {
        Write-Host "Microsoft mailbox register is already running."
        exit 0
    }

    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @("-B", $registerScript) `
        -WorkingDirectory $outlookRoot `
        -PassThru
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII
    Write-Host "Microsoft mailbox register started."
    exit 0
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
