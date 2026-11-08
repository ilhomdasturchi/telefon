# Install the Telefon Cursor extension (activity-aware call notify)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Src = Join-Path $Root "tools\telefon"
$ExtRoot = Join-Path $env:USERPROFILE ".cursor\extensions"
$Dest = Join-Path $ExtRoot "personal.telefon-0.5.1"

if (-not (Test-Path $Src)) {
    throw "Extension source missing: $Src"
}
New-Item -ItemType Directory -Force -Path $ExtRoot | Out-Null

# Remove older copies so Cursor loads the new version
Get-ChildItem $ExtRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "personal.telefon-*" -and $_.FullName -ne $Dest } |
    ForEach-Object { Remove-Item -Recurse -Force $_.FullName }

if (Test-Path $Dest) {
    Remove-Item -Recurse -Force $Dest
}
Copy-Item -Recurse -Force $Src $Dest
Write-Host "Installed Telefon extension to:"
Write-Host "  $Dest"
Write-Host ""
Write-Host "Reload Cursor window:"
Write-Host "  Ctrl+Shift+P -> Developer: Reload Window"
Write-Host ""
Write-Host "After a task: calls only if you are NOT busy in Cursor."
Write-Host "  Inside Cursor + doing something  -> no call"
Write-Host "  Inside Cursor + idle / outside Cursor -> call after idle seconds"
Write-Host "Decisions are logged in: View -> Output -> Telefon"
