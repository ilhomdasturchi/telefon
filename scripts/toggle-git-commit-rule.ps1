# Toggle short Git-commit Cursor rule on/off (one click).
# Enabled  = .cursor/rules/git-commit-message.mdc + marked block in .cursorrules
# Disabled = both removed
# Optional: -Root C:\path\to\project (defaults to this repo)
param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
Set-Location $Root

$RuleDir = Join-Path $Root ".cursor\rules"
$RulePath = Join-Path $RuleDir "git-commit-message.mdc"
$CursorRules = Join-Path $Root ".cursorrules"

$Begin = "# BEGIN git-commit-short"
$End = "# END git-commit-short"

$RuleBody = @"
---
description: Short imperative Git commit subjects only (~50 chars)
alwaysApply: true
---

# Git commit messages

When generating a Git commit message for this repository:

- Output exactly ONE short subject line
- Max ~50 characters (hard cap 72)
- Imperative mood: Fix..., Add..., Scale...
- No body, no bullets, no file-by-file summary
- Never start with "This commit..."
"@

$LegacyBlock = @"
$Begin
When generating a Git commit message for this repository:
- Output exactly ONE short subject line
- Max ~50 characters (hard cap 72)
- Imperative mood: Fix..., Add..., Scale...
- No body, no bullets, no file-by-file summary
- Never start with "This commit..."
$End
"@

function Remove-LegacyBlock([string]$text) {
    if ([string]::IsNullOrEmpty($text)) { return "" }
    $pattern = "(?ms)^\s*$([regex]::Escape($Begin))\s*.*?$([regex]::Escape($End))\s*"
    return [regex]::Replace($text, $pattern, "").TrimEnd() + "`n"
}

function Add-LegacyBlock([string]$text) {
    $clean = Remove-LegacyBlock $text
    if ([string]::IsNullOrWhiteSpace($clean)) {
        return $LegacyBlock.TrimEnd() + "`n"
    }
    return $clean.TrimEnd() + "`n`n" + $LegacyBlock.TrimEnd() + "`n"
}

$utf8 = New-Object System.Text.UTF8Encoding $false
$enabled = Test-Path $RulePath

if ($enabled) {
    Remove-Item -Force $RulePath
    if (Test-Path $CursorRules) {
        $next = Remove-LegacyBlock ([System.IO.File]::ReadAllText($CursorRules))
        if ([string]::IsNullOrWhiteSpace($next)) {
            Remove-Item -Force $CursorRules
        } else {
            [System.IO.File]::WriteAllText($CursorRules, $next.TrimEnd() + "`n", $utf8)
        }
    }
    Write-Host "Git commit rule: OFF (uninstalled)"
    exit 0
}

New-Item -ItemType Directory -Force -Path $RuleDir | Out-Null
[System.IO.File]::WriteAllText($RulePath, $RuleBody.TrimEnd() + "`n", $utf8)

$existing = ""
if (Test-Path $CursorRules) {
    $existing = [System.IO.File]::ReadAllText($CursorRules)
}
[System.IO.File]::WriteAllText($CursorRules, (Add-LegacyBlock $existing).TrimEnd() + "`n", $utf8)

Write-Host "Git commit rule: ON (installed)"
exit 0
