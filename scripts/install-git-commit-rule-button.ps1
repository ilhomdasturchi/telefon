# Install the status-bar "Commit rule" button into Cursor extensions
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Src = Join-Path $Root "tools\git-commit-rule-toggle"
$ExtRoot = Join-Path $env:USERPROFILE ".cursor\extensions"
$Dest = Join-Path $ExtRoot "telefon.telefon-git-commit-rule-0.0.1"

if (-not (Test-Path $Src)) {
    throw "Extension source missing: $Src"
}
New-Item -ItemType Directory -Force -Path $ExtRoot | Out-Null
if (Test-Path $Dest) {
    Remove-Item -Recurse -Force $Dest
}
Copy-Item -Recurse -Force $Src $Dest
Write-Host "Installed status-bar button to:"
Write-Host "  $Dest"
Write-Host ""
Write-Host "Reload Cursor window to see it (bottom-right):"
Write-Host "  Ctrl+Shift+P -> Developer: Reload Window"
Write-Host "Then click bottom-right status bar:  Commit rule ON/OFF  (sparkle icon)"
