param([ValidateSet('Build', 'Start', 'Stop', 'Status', 'Backup')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
switch ($Action) {
    'Build' { docker build -f deploy/Dockerfile -t neo-aps:0.1.0 . }
    'Start' {
        $existing = docker ps -a --filter 'name=^neo-aps$' --format '{{.Names}}'
        if ($existing -eq 'neo-aps') { docker start neo-aps }
        else {
            docker volume create neo_aps_data
            docker run -d --name neo-aps --label project=neo-aps --init --restart unless-stopped --stop-timeout 60 --env-file deploy/.env -p 127.0.0.1:8080:8000 --mount type=volume,source=neo_aps_data,target=/var/lib/neo-aps --log-opt max-size=10m --log-opt max-file=3 neo-aps:0.1.0
        }
    }
    'Stop' { docker stop neo-aps }
    'Status' { docker ps -a --filter 'name=^neo-aps$' --format '{{.Names}} {{.Status}} {{.Ports}}' }
    'Backup' {
        New-Item -ItemType Directory -Force -Path backups | Out-Null
        $target = 'backups/neo-aps-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.dump'
        docker exec neo-aps /app/deploy/backup.sh /tmp/neo-aps.dump
        if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
        docker cp neo-aps:/tmp/neo-aps.dump $target
        Write-Output "Backup saved: $target"
    }
}
if ($LASTEXITCODE -ne 0) { throw 'Docker command failed.' }
