$ErrorActionPreference = "Stop"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Load .env.local before applying defaults so user-edited settings (MT5 path,
# provider keys, commission estimate and account allow-list) reach the bridge.
$EnvFile = Join-Path $Root ".env.local"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $key = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($key, "Process"))) {
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
            }
        }
    }
}

function Set-EnvDefault([string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, "Process"))) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

Set-EnvDefault "MT5_ROOT" "C:\Program Files\MetaTrader 5"
Set-EnvDefault "SYMBOL" "XAUUSD"
Set-EnvDefault "TIMEFRAME" "M5"
Set-EnvDefault "MT5_BRIDGE_HOST" "127.0.0.1"
Set-EnvDefault "MT5_BRIDGE_PORT" "8765"
Set-EnvDefault "MAGIC" "987654"
Set-EnvDefault "XGB_CONFIRMATION_ENABLED" "1"
Set-EnvDefault "XGB_THRESHOLD" "0.58"
Set-EnvDefault "XGB_TRAIN_BARS" "600"
Set-EnvDefault "XGB_HORIZON" "3"
Set-EnvDefault "XGB_MODEL_REFRESH_SEC" "300"
Set-EnvDefault "XGB_MIN_ROWS" "160"
Set-EnvDefault "XGB_MIN_VALIDATION_AUC" "0.55"
Set-EnvDefault "MAX_SPREAD_POINTS" "80"
Set-EnvDefault "BRAIN_MAX_SPREAD_POINTS" "80"
Set-EnvDefault "BRAIN_MIN_MARGIN_LEVEL" "400"
Set-EnvDefault "RISK_BASE_BASKET_PCT" "0.0035"
Set-EnvDefault "RISK_MAX_BASKET_PCT" "0.0050"
Set-EnvDefault "RISK_RECOVERY_RESERVE_PCT" "0.0010"
Set-EnvDefault "RISK_DAILY_LOSS_PCT" "0.0200"
Set-EnvDefault "RISK_MAX_PEAK_DD_PCT" "0.0500"
Set-EnvDefault "RISK_MAX_MARGIN_UTIL_PCT" "0.25"
Set-EnvDefault "RISK_MIN_MARGIN_LEVEL" "400"
Set-EnvDefault "RISK_STOP_ATR_MULTIPLIER" "1.5"
Set-EnvDefault "COMPOUND_MAX_BONUS_PCT" "0.0005"
Set-EnvDefault "COMPOUND_PROFIT_SHARE" "0.25"
Set-EnvDefault "BASKET_MIN_NET_PROFIT" "1.00"
Set-EnvDefault "ESTIMATED_ROUND_TRIP_COST_PER_LOT" "0.0"
Set-EnvDefault "RECOVERY_ENABLED" "1"
Set-EnvDefault "RECOVERY_ARM_ATR_MULTIPLIER" "0.75"
Set-EnvDefault "RECOVERY_PULLBACK_MIN_FRACTION" "0.25"
Set-EnvDefault "RECOVERY_PULLBACK_MAX_FRACTION" "0.65"
Set-EnvDefault "RECOVERY_PENDING_EXPIRY_MINUTES" "30"
Set-EnvDefault "RECOVERY_CHECK_SEC" "5"
Set-EnvDefault "SMART_PROFIT_ENABLED" "1"
Set-EnvDefault "SMART_PROFIT_TRAIL_POINTS" "60"
Set-EnvDefault "SMART_PROFIT_STEP_POINTS" "5"
Set-EnvDefault "SMART_PROFIT_BE_OFFSET_POINTS" "0"
Set-EnvDefault "SMART_PROFIT_CHECK_SEC" "0.25"
Set-EnvDefault "SMART_BULK_EXIT_ENABLED" "1"
Set-EnvDefault "SMART_BULK_EXIT_CHECK_US" "500000"
Set-EnvDefault "SMART_BULK_EXIT_MAX_WORKERS" "10"

$Mt5Root = [Environment]::GetEnvironmentVariable("MT5_ROOT", "Process")
$BridgeDir = Join-Path $Root "bridge"
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

Write-Host ""
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js is not installed. Install Node.js LTS, then rerun this launcher." }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm is not installed/available. Install Node.js LTS, then rerun this launcher." }

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " NovaFury AI Algo Bot - MT5 V2 BALANCED" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "MT5 folder : $Mt5Root"
Write-Host "Symbol     : XAUUSD (auto-resolve broker suffix)"
Write-Host "Timeframe  : M5"
Write-Host "Bridge     : http://127.0.0.1:8765"
Write-Host "XGBoost    : M5 entry confirmation (0.58 threshold)"
Write-Host "AI Ensemble: LightGBM + CatBoost + Meta-labeling (purged walk-forward validation)"
Write-Host ""

if (-not (Test-Path $Mt5Root)) {
    Write-Warning "MT5 folder was not found at $Mt5Root. Set MT5_ROOT in .env.local if your terminal is installed elsewhere."
}

if (-not (Test-Path $Python)) {
    Write-Host "[1/4] Creating Python environment..." -ForegroundColor Yellow
    py -3 -m venv $Venv
    if ($LASTEXITCODE -ne 0) { python -m venv $Venv }
}
if (-not (Test-Path $Python)) { throw "Python venv could not be created." }

Write-Host "[2/4] Installing/refreshing MT5 Python connector..." -ForegroundColor Yellow
& $Python -m pip install --disable-pip-version-check -q --upgrade pip
& $Python -m pip install --disable-pip-version-check -q -r (Join-Path $BridgeDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "MetaTrader5 Python package installation failed." }

Write-Host "[3/4] Starting MT5 bridge..." -ForegroundColor Yellow

# Open the user's MT5 terminal when it exists. The Python bridge also initializes it.
$terminal = Get-ChildItem -Path $Mt5Root -Filter "terminal64.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $terminal) {
    $terminal = Get-ChildItem -Path $Mt5Root -Filter "terminal.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($terminal) {
    Write-Host "Opening MT5 terminal: $($terminal.FullName)" -ForegroundColor Green
    Start-Process -FilePath $terminal.FullName
} else {
    Write-Warning "terminal64.exe was not found under $Mt5Root. The bridge will try the normal MT5 installation."
}

$bridgeLog = Join-Path $Root "mt5_bridge.log"
$bridgeErr = Join-Path $Root "mt5_bridge_error.log"
if (Test-Path $bridgeLog) { Remove-Item $bridgeLog -Force -ErrorAction SilentlyContinue }
if (Test-Path $bridgeErr) { Remove-Item $bridgeErr -Force -ErrorAction SilentlyContinue }
$bridge = Start-Process -FilePath $Python -ArgumentList "`"$BridgeDir\mt5_bridge.py`"" -WorkingDirectory $Root -PassThru -WindowStyle Minimized -RedirectStandardOutput $bridgeLog -RedirectStandardError $bridgeErr
$bridge.Id | Set-Content -Path (Join-Path $Root ".novafury_bridge.pid") -Encoding ascii
$bridgeHealthy = $false
for ($i = 0; $i -lt 20; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2
        if ($health.ok -and $health.service -eq "NovaFury MT5 bridge" -and $health.version -eq "2.0") { $bridgeHealthy = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $bridgeHealthy) {
    if ($bridge -and -not $bridge.HasExited) { Stop-Process -Id $bridge.Id -Force -ErrorAction SilentlyContinue }
    throw "NovaFury V2 bridge health/version check failed. Dashboard was not started. Check mt5_bridge_error.log and port 8765."
}

Write-Host "[4/4] Starting dashboard..." -ForegroundColor Yellow
Push-Location $Root
# Always reconcile frontend dependencies so an older/broken node_modules from a previous run cannot block startup.
Write-Host "Refreshing dashboard dependencies..." -ForegroundColor DarkGray
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Standard npm dependency resolution failed. Retrying with --legacy-peer-deps..."
    npm install --legacy-peer-deps --no-audit --no-fund
}
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm install failed. Check Node.js/npm installation and package dependencies." }
$dash = Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run dev -- --host 127.0.0.1 --port 3000" -WorkingDirectory $Root -PassThru -WindowStyle Minimized
$dash.Id | Set-Content -Path (Join-Path $Root ".novafury_dashboard.pid") -Encoding ascii
Pop-Location

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:3000" -TimeoutSec 2
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if ($ready) {
    Start-Process "http://127.0.0.1:3000"
} else {
    Write-Warning "Dashboard did not answer on port 3000 yet. Try http://127.0.0.1:3000 manually."
}

Write-Host ""
Write-Host "NovaFury V2 is running. Trading remains DISARMED until explicitly armed in the dashboard." -ForegroundColor Green
Write-Host "Dashboard: http://127.0.0.1:3000" -ForegroundColor Green
Write-Host "Bridge   : http://127.0.0.1:8765" -ForegroundColor Green
Write-Host "Bridge log: $bridgeLog" -ForegroundColor DarkGray
Write-Host ""
Write-Host "BALANCED PROFILE: XAUUSD only | 10-ticket basket | ATR-based broker SL | 0.35% starting basket risk" -ForegroundColor Cyan
Write-Host "Hard limits: 0.50% maximum basket allocation incl. recovery reserve; 2% daily loss; 5% peak drawdown; 25% margin utilisation." -ForegroundColor Cyan
Write-Host "Confirm a DEMO account, verify AI readiness, then explicitly arm Auto Trading from the dashboard." -ForegroundColor Yellow
Write-Host "OpenAI + Claude are optional supervisors; configure and enable them in Dashboard > Settings." -ForegroundColor DarkGray
Write-Host "Use STOP_MT5_BOT.ps1 to disarm, cancel recovery orders and stop only this project's processes." -ForegroundColor DarkGray

try {
    while ($true) {
        Start-Sleep -Seconds 2
        if (Test-Path $bridgeLog) {
            $tail = Get-Content $bridgeLog -Tail 3 -ErrorAction SilentlyContinue
            if ($tail) { $tail | ForEach-Object { Write-Host $_ -ForegroundColor DarkGray } }
        }
    }
} finally {
    if ($dash -and -not $dash.HasExited) { Stop-Process -Id $dash.Id -Force -ErrorAction SilentlyContinue }
    if ($bridge -and -not $bridge.HasExited) {
        $safeToStopBridge = $false
        try {
            $disarm = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/disarm" -ContentType "application/json" -Body "{}" -TimeoutSec 5
            $safeToStopBridge = [bool]$disarm.ok
        } catch { Write-Warning "Bridge disarm not confirmed; leaving bridge alive so broker recovery orders are not abandoned. Run STOP_MT5_BOT.ps1 after reviewing the bridge." }
        if ($safeToStopBridge) { Stop-Process -Id $bridge.Id -Force -ErrorAction SilentlyContinue }
    }
}
