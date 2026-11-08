# Upload dist update artifacts to marvar.id SFTP /telefon/
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$env:NODE_OPTIONS = "--use-system-ca"
python -m pip install -q paramiko
python (Join-Path $Root "scripts\upload_telefon_update.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
