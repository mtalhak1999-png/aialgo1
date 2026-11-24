$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BridgePidFile = Join-Path $Root ".novafury_bridge.pid"
$DashboardPidFile = Join-Path $Root ".novafury_dashboard.pid"

# Never kill the bridge while it may still own pending recovery orders.
$bridgePid = $null
if (Test-Path $BridgePidFile) {
    try { $bridgePid = [int](Get-Content $BridgePidFile -Raw).Trim() } catch { $bridgePid = $null }
}
if ($bridgePid) {
    $bridgeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$bridgePid" -ErrorAction SilentlyContinue
    if ($bridgeProcess -and $bridgeProcess.CommandLine -match "mt5_bridge\.py") {
        try {
            $disarm = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/disarm" -ContentType "application/json" -Body "{}" -TimeoutSec 5
            if (-not $disarm.ok) {
                Write-Warning "Bridge disarm/recovery cleanup was not confirmed. The bridge has been left running. Inspect MT5 pending orders and bridge logs before stopping it."
                exit 1
            }
            Write-Host "Auto execution disarmed and recovery-order cleanup verified." -ForegroundColor Green
        } catch {
            Write-Warning "Could not confirm bridge disarm. The bridge has been left running to avoid abandoning recovery orders. $($_.Exception.Message)"
            exit 1
        }
    }
}

# Stop only PIDs recorded by this project's launcher, and verify their command line.
if (Test-Path $DashboardPidFile) {
    try {
        $dashboardPid = [int](Get-Content $DashboardPidFile -Raw).Trim()
        $dashboardProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$dashboardPid" -ErrorAction SilentlyContinue
        if ($dashboardProcess -and $dashboardProcess.CommandLine -match "npm run dev") {
            & taskkill.exe /T /F /PID $dashboardPid | Out-Null
        }
    } catch { Write-Warning "Could not stop recorded dashboard process: $($_.Exception.Message)" }
}
if ($bridgePid) {
    $bridgeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$bridgePid" -ErrorAction SilentlyContinue
    if ($bridgeProcess -and $bridgeProcess.CommandLine -match "mt5_bridge\.py") {
        & taskkill.exe /T /F /PID $bridgePid | Out-Null
    }
}
Remove-Item $BridgePidFile, $DashboardPidFile -Force -ErrorAction SilentlyContinue
Write-Host "NovaFury project dashboard and bridge processes stopped. Open MT5 positions remain open with their broker-side stop-losses; stopping the app does not flatten the account." -ForegroundColor Green
