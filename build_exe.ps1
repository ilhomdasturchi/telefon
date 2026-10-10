# Build a single AiPhone.exe (assets inside; .env goes beside the exe).
# Output: dist\AiPhone.exe
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:NODE_OPTIONS = "--use-system-ca"

Write-Host "Installing build + runtime deps..."
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements-runtime.txt pyinstaller

# Remove old onedir output so it doesn't confuse deploy
$OldDir = Join-Path $Root "dist\AiPhone"
if (Test-Path $OldDir) {
    Write-Host "Removing old folder build dist\AiPhone ..."
    Remove-Item -Recurse -Force $OldDir
}

Write-Host "Running PyInstaller (onefile)..."
python -m PyInstaller --noconfirm --clean AiPhone.spec

$Exe = Join-Path $Root "dist\AiPhone.exe"
if (-not (Test-Path $Exe)) {
    throw "Build failed: $Exe not found"
}

Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root "dist\.env.example") -Force
$LocalEnv = Join-Path $Root ".env"
if (Test-Path $LocalEnv) {
    Copy-Item $LocalEnv (Join-Path $Root "dist\.env") -Force
    Write-Host "Copied local .env into dist\ (keep private)."
}

$sizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Done: $Exe ($sizeMb MB)"
Write-Host "Server: copy AiPhone.exe + .env (same folder), then run AiPhone.exe"
Write-Host "First run registers Windows login auto-start. Opt out: AiPhone.exe --no-autostart"
