# Find uv.exe and add its folder to the USER PATH permanently (no admin needed).
#   pwsh -File win\add-uv-to-path.ps1
$ErrorActionPreference = 'Stop'

$cands = @(
    (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
    (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe'),
    (Join-Path $env:USERPROFILE 'miniforge3\Scripts\uv.exe'),
    (Join-Path $env:USERPROFILE 'miniforge3\Library\bin\uv.exe'),
    (Join-Path $env:USERPROFILE 'scoop\shims\uv.exe')
)
$uv = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $uv) {
    Write-Host 'uv.exe not in the usual places; searching your user profile (may take a minute)...'
    $uv = Get-ChildItem -Path $env:USERPROFILE, $env:LOCALAPPDATA -Filter uv.exe -Recurse -File `
            -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}

if (-not $uv) {
    Write-Host 'uv.exe not found -> installing the official build to ~\.local\bin'
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (-not (Test-Path $uv)) { throw 'uv install failed' }
}

$dir = Split-Path $uv -Parent
Write-Host "found: $uv"

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($userPath -split ';' | Where-Object { $_ })
if ($parts -contains $dir) {
    Write-Host "already in USER PATH: $dir"
} else {
    [Environment]::SetEnvironmentVariable('Path', (($parts + $dir) -join ';'), 'User')
    Write-Host "added to USER PATH: $dir"
}
$env:Path = "$dir;$env:Path"      # also for this window
Write-Host ("uv version: " + (& $uv --version))
Write-Host 'Open a NEW terminal (and restart VS Code / Antigravity) for other windows to see it.'
