$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
Write-Host 'WeChat OA Collector: http://127.0.0.1:8107/'
python .\app.py

