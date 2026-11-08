# Build AiPhone.exe (windowless) + Neutralino shell + portable zip + update.json
# Output:
#   dist\AiPhone.exe
#   dist\telefon-portable\   (run telefon.exe here)
#   dist\telefon.zip
#   dist\update.json
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:NODE_OPTIONS = "--use-system-ca"

function Ensure-NeutralinoBinaries {
    $Desktop = Join-Path $Root "desktop"
    $WinBin = Join-Path $Desktop "bin\neutralino-win_x64.exe"
    $Client = Join-Path $Desktop "resources\js\neutralino.js"
    if ((Test-Path $WinBin) -and (Test-Path $Client) -and ((Get-Item $Client).Length -gt 1000)) {
        return
    }
    Write-Host "Fetching Neutralino binaries..."
    $binDir = Join-Path $Desktop "bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $zipPath = Join-Path $env:TEMP "neutralinojs-v5.5.0.zip"
    curl.exe -L --retry 3 -o $zipPath "https://github.com/neutralinojs/neutralinojs/releases/download/v5.5.0/neutralinojs-v5.5.0.zip"
    if (-not (Test-Path $zipPath) -or (Get-Item $zipPath).Length -lt 100000) {
        throw "Failed to download Neutralino binaries"
    }
    Expand-Archive -Path $zipPath -DestinationPath $binDir -Force

    if (-not (Test-Path $Client) -or (Get-Item $Client).Length -lt 1000) {
        $tmpdir = Join-Path $env:TEMP "neu-lib-npm-build"
        if (Test-Path $tmpdir) { Remove-Item -Recurse -Force $tmpdir }
        New-Item -ItemType Directory -Path $tmpdir | Out-Null
        Push-Location $tmpdir
        try {
            npm init -y | Out-Null
            npm install @neutralinojs/lib@5.5.0 --no-save | Out-Null
            Copy-Item (Join-Path $tmpdir "node_modules\@neutralinojs\lib\dist\neutralino.js") $Client -Force
        } finally {
            Pop-Location
        }
    }
    if (-not (Test-Path $WinBin)) { throw "neutralino-win_x64.exe missing after download" }
    if (-not (Test-Path $Client) -or (Get-Item $Client).Length -lt 1000) {
        throw "neutralino.js client missing after download"
    }
}

Write-Host "Installing build + runtime deps..."
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements-runtime.txt pyinstaller

# Remove old onedir output so it doesn't confuse deploy
$OldDir = Join-Path $Root "dist\AiPhone"
if (Test-Path $OldDir) {
    Write-Host "Removing old folder build dist\AiPhone ..."
    Remove-Item -Recurse -Force $OldDir
}

Write-Host "Running PyInstaller (onefile, windowless)..."
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

Ensure-NeutralinoBinaries

Write-Host "Building Neutralino shell..."
Push-Location (Join-Path $Root "desktop")
try {
    neu build --release 2>&1
    if ($LASTEXITCODE -ne 0) { throw "neu build failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

$NeuDist = Join-Path $Root "desktop\dist\telefon"
$NeuExe = Join-Path $NeuDist "telefon-win_x64.exe"
$NeuRes = Join-Path $NeuDist "resources.neu"
if (-not (Test-Path $NeuExe)) {
    # newer/older neu naming fallbacks
    $cand = Get-ChildItem $NeuDist -Filter "*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { $NeuExe = $cand.FullName }
}
if (-not (Test-Path $NeuExe)) { throw "Neutralino exe not found under $NeuDist" }
if (-not (Test-Path $NeuRes)) { throw "resources.neu not found under $NeuDist" }

$Portable = Join-Path $Root "dist\telefon-portable"
if (Test-Path $Portable) { Remove-Item -Recurse -Force $Portable }
New-Item -ItemType Directory -Force -Path (Join-Path $Portable "logs") | Out-Null

Copy-Item $NeuExe (Join-Path $Portable "telefon.exe") -Force
Copy-Item $NeuRes (Join-Path $Portable "resources.neu") -Force
Copy-Item $Exe (Join-Path $Portable "AiPhone.exe") -Force
Copy-Item (Join-Path $Root "dist\.env.example") (Join-Path $Portable ".env.example") -Force
if (Test-Path (Join-Path $Root "dist\.env")) {
    Copy-Item (Join-Path $Root "dist\.env") (Join-Path $Portable ".env") -Force
}

# Copy WebView2 loader / extra files from neu dist if present
Get-ChildItem $NeuDist -File | Where-Object {
    $_.Name -notin @((Split-Path $NeuExe -Leaf), "resources.neu")
} | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $Portable $_.Name) -Force
}

$Zip = Join-Path $Root "dist\telefon.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path (Join-Path $Portable "*") -DestinationPath $Zip -Force

$hash = (Get-FileHash -Algorithm SHA256 -Path $Zip).Hash.ToLowerInvariant()
$version = "1.0.0"
$configPath = Join-Path $Root "desktop\neutralino.config.json"
if (Test-Path $configPath) {
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($cfg.version) { $version = [string]$cfg.version }
    } catch { }
}

$manifest = [ordered]@{
    version     = $version
    sha256      = $hash
    url         = "https://telefon.telepat.uz/soft/telefon.zip"
    publishedAt = (Get-Date).ToUniversalTime().ToString("o")
}
$ManifestPath = Join-Path $Root "dist\update.json"
($manifest | ConvertTo-Json -Depth 5) | Set-Content -Path $ManifestPath -Encoding utf8

# Local install starts "up to date" vs this build's package hash
$state = [ordered]@{
    sha256    = $hash
    version   = $version
    url       = $manifest.url
    checkedAt = $manifest.publishedAt
}
($state | ConvertTo-Json -Depth 5) | Set-Content -Path (Join-Path $Portable "update-state.json") -Encoding utf8

# Also stage a launcher copy name for manual / Update URL hosting
Copy-Item (Join-Path $Portable "telefon.exe") (Join-Path $Root "dist\telefon.exe") -Force

$sizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
$zipMb = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host ""
Write-Host "Done:"
Write-Host "  Backend : $Exe ($sizeMb MB)"
Write-Host "  Portable: $Portable"
Write-Host "  Zip     : $Zip ($zipMb MB)"
Write-Host "  Manifest: $ManifestPath"
Write-Host "  SHA-256 : $hash"
Write-Host ""
Write-Host "Upload to https://telefon.telepat.uz/soft/ :"
Write-Host "  1) dist\update.json"
Write-Host "  2) dist\telefon.zip"
Write-Host "  3) dist\telefon.exe   (Update URL / manual download)"
Write-Host ""
Write-Host "Server install: unzip telefon.zip, put .env beside telefon.exe, run telefon.exe"
Write-Host "First Enable (or auto-start) runs AiPhone.exe --no-autostart; shell owns login autostart."
