# Install PowerShell 7 (pwsh) and add collabosm shortcuts to its profile.
#   powershell -ExecutionPolicy Bypass -File win\install-pwsh7.ps1
# Afterwards, in a NEW "PowerShell 7" window:  collabm
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# 1. PowerShell 7
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if ($pwsh) {
    Write-Host "PowerShell 7 already installed: $($pwsh.Source)"
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host 'installing PowerShell 7 via winget ...'
    winget install --id Microsoft.PowerShell --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget failed ($LASTEXITCODE)" }
} else {
    Write-Host 'winget not found. Install the MSI from https://aka.ms/powershell-release?tag=stable then re-run this script.'
    exit 1
}

# 2. profile block (PowerShell 7 reads Documents\PowerShell\Microsoft.PowerShell_profile.ps1)
$docs = [Environment]::GetFolderPath('MyDocuments')   # follows OneDrive redirection
if (-not $docs) { $docs = Join-Path $HOME 'Documents' }
$profilePath = Join-Path $docs 'PowerShell\Microsoft.PowerShell_profile.ps1'
New-Item -ItemType Directory -Force -Path (Split-Path $profilePath) | Out-Null
if (-not (Test-Path $profilePath)) { New-Item -ItemType File -Path $profilePath | Out-Null }

$block = @"
# >>> collabosm >>>
`$global:CollabosmRoot = '$root'
# lets the plain `colab` CLI run on Windows (termios stub) and talk UTF-8
if (`$env:PYTHONPATH -notlike "*collabosm\win\compat*") { `$env:PYTHONPATH = "`$CollabosmRoot\win\compat;`$env:PYTHONPATH".TrimEnd(';') }
`$env:PYTHONUTF8 = '1'
function collabm        { & "`$CollabosmRoot\win\collabm.ps1" @args }
function collabm-up     { & "`$CollabosmRoot\win\up.ps1" @args }
function collabm-down   { & "`$CollabosmRoot\win\down.ps1" @args }
function collabm-status { & "`$CollabosmRoot\win\status.ps1" @args }
function collabm-test   { & "`$CollabosmRoot\win\test.ps1" @args }
# <<< collabosm <<<
"@

$text = [IO.File]::ReadAllText($profilePath)
$pattern = '(?s)# >>> collabosm >>>.*?# <<< collabosm <<<\r?\n?'
if ($text -match $pattern) { $text = [regex]::Replace($text, $pattern, '') }
$text = $text.TrimEnd() + "`r`n`r`n" + $block + "`r`n"
[IO.File]::WriteAllText($profilePath, $text.TrimStart(), (New-Object Text.UTF8Encoding($true)))
Write-Host "profile updated: $profilePath"

Write-Host @"

Done. Open a NEW 'PowerShell 7' window (Windows Terminal: dropdown -> PowerShell) and use:
  collabm            chat (offers to start the A100 if it is down; 'collabm --up' starts without asking)
  collabm-up         start / resume the VM and server
  collabm-status     what is running / billing
  collabm-test       smoke test
  collabm-down       STOP THE VM (billing)
Tip: in Windows Terminal settings, set 'Default profile' to PowerShell (7).
"@
