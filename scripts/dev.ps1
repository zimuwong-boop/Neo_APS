param([ValidateSet('Start', 'Stop', 'Status')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PATH = (Join-Path $projectRoot '.tools/node') + ';' + $env:PATH
$logRoot = Join-Path $projectRoot '.tools/logs'
$pidPath = Join-Path $logRoot 'dev-pids.json'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$nodePath = Join-Path $projectRoot '.tools/node/node.exe'
if ($Action -eq 'Start') {
    if (Test-Path -LiteralPath $pidPath) {
        $saved = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        foreach ($id in @($saved.backend, $saved.frontend)) {
            if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
                throw 'A development process is already running; use Status or Stop first.'
            }
        }
    }
    & $pythonPath scripts/setup-local-db.py
    if ($LASTEXITCODE -ne 0) { throw 'Database startup failed.' }
    & $pythonPath backend/manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
    $web = Start-Process -FilePath $pythonPath -ArgumentList @('backend/manage.py', 'runserver', '127.0.0.1:8000', '--noreload') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'backend.log') -RedirectStandardError (Join-Path $logRoot 'backend-error.log') -PassThru
    $front = Start-Process -FilePath $nodePath -ArgumentList @('node_modules/vite/bin/vite.js') -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'frontend.log') -RedirectStandardError (Join-Path $logRoot 'frontend-error.log') -PassThru
    @{ backend = $web.Id; frontend = $front.Id } | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
    Write-Output 'Development services started. Vue: http://127.0.0.1:5173 ; Django: http://127.0.0.1:8000'
} elseif ($Action -eq 'Stop') {
    if (Test-Path -LiteralPath $pidPath) {
        $saved = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        foreach ($id in @($saved.backend, $saved.frontend)) {
            $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
            if ($processInfo -and $processInfo.ExecutablePath -in @($pythonPath, $nodePath)) {
                Stop-Process -Id $id
            }
        }
        Remove-Item -LiteralPath $pidPath
    }
    $dataPath = Join-Path $env:LOCALAPPDATA 'NeoAPS/postgres-dev'
    & .\.tools\pgsql\bin\pg_ctl.exe -D $dataPath -m fast -w stop
} else {
    foreach ($address in @('http://127.0.0.1:8000/api/v1/health/', 'http://127.0.0.1:5173/api/v1/health/')) {
        try { $response = Invoke-WebRequest -Uri $address -UseBasicParsing; Write-Output "$address : $($response.StatusCode)" }
        catch { Write-Output "$address : not ready" }
    }
}
