$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PATH = (Join-Path $projectRoot '.venv/Scripts') + ';' + (Join-Path $projectRoot '.tools/node') + ';' + (Join-Path $projectRoot '.tools/pgsql/bin') + ';' + $env:PATH
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectRoot '.tools/playwright'
Write-Output 'Project Python, Node.js, PostgreSQL tools and browser cache configured for this terminal.'
