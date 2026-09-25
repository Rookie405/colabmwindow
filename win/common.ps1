# Shared helpers for the Windows (PowerShell) entry points of collabosm.
# Dot-sourced by setup.ps1 / up.ps1 / down.ps1 / status.ps1 / test.ps1.
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.

$ErrorActionPreference = 'Stop'
$script:Root       = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:ScriptsDir = Join-Path $Root 'scripts'
$script:StateDir   = Join-Path $Root '.local'          # gitignored: endpoint, key, temp files
if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }

# Force UTF-8 between PowerShell and the Python CLI (Japanese Windows defaults to cp932).
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)

# google-colab-cli imports POSIX `termios` at startup and crashes on native Windows.
# A stub on PYTHONPATH fixes every command except `colab console`. Inherited by
# restore.py's subprocess calls too.
$compat = Join-Path $PSScriptRoot 'compat'
if ($IsWindows -or $env:OS -eq 'Windows_NT') {
    if (-not $env:PYTHONPATH) { $env:PYTHONPATH = $compat }
    elseif ($env:PYTHONPATH -notlike "*$compat*") { $env:PYTHONPATH = "$compat;$env:PYTHONPATH" }
}

function Say([string]$msg) {
    Write-Host ("[{0} {1}] {2}" -f $script:Tag, (Get-Date -Format 'HH:mm:ss'), $msg)
}

function Get-ColabExe {
    if ($env:COLAB) { return $env:COLAB }
    $cmd = Get-Command colab -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cand = Join-Path $env:USERPROFILE '.local\bin\colab.exe'
    if (Test-Path $cand) { return $cand }
    throw "colab CLI not found. Run: powershell -ExecutionPolicy Bypass -File win\setup.ps1"
}

function Get-UvExe {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in @((Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
                     (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'),
                     (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe'),
                     (Join-Path $env:USERPROFILE 'miniforge3\Scripts\uv.exe'),
                     (Join-Path $env:USERPROFILE 'miniforge3\Library\bin\uv.exe'))) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

function Get-ColabPython {
    # The python inside the uv tool venv of google-colab-cli (restore.py imports colab_cli).
    if ($env:COLAB_PY) { return $env:COLAB_PY }
    $dirs = @()
    $uv = Get-UvExe
    if ($uv) { try { $d = (& $uv tool dir 2>$null); if ($d) { $dirs += $d.Trim() } } catch {} }
    if ($env:APPDATA) { $dirs += (Join-Path $env:APPDATA 'uv\tools') }
    foreach ($d in $dirs) {
        $py = Join-Path $d 'google-colab-cli\Scripts\python.exe'
        if (Test-Path $py) { return $py }
    }
    throw "google-colab-cli tool venv not found. Run win\setup.ps1 first."
}

function Write-LfFile([string]$path, [string]$text) {
    # Files executed on the Linux VM must be LF + UTF-8 without BOM.
    $text = $text -replace "`r`n", "`n"
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-Colab {
    # Runs the colab CLI, returns stdout+stderr as one string, never throws on nonzero exit.
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ArgList)
    $colab = Get-ColabExe
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try   { $out = & $colab @ArgList 2>&1 | ForEach-Object { "$_" } | Out-String }
    finally { $ErrorActionPreference = $prev }
    return $out
}

function Get-Endpoint {
    $f = Join-Path $StateDir 'endpoint.json'
    if (-not (Test-Path $f)) { return $null }
    return Get-Content $f -Raw | ConvertFrom-Json
}

function Repair-KernelClient {
    # google-colab-cli 0.7.2 pins jupyter-kernel-client==0.8, but its runtime.py needs
    # `jupyter_kernel_client.JupyterSubprotocol`, which only exists from 0.9.0. With 0.8
    # every `colab exec` dies with AttributeError. 1.0.x removes KernelClient, so pin 0.9.0.
    $py = Get-ColabPython
    $ok = (& $py -c "import jupyter_kernel_client as j; print(hasattr(j,'JupyterSubprotocol') and hasattr(j,'KernelClient'))" 2>$null)
    if ("$ok".Trim() -ne 'True') {
        Say 'pinning jupyter-kernel-client==0.9.0 into the colab CLI venv (0.8 breaks colab exec)'
        $uv = Get-UvExe
        if ($uv) {
            & $uv pip install --python $py 'jupyter-kernel-client==0.9.0'
        } else {
            # uv not on PATH: fall back to pip inside the tool venv
            & $py -m ensurepip --upgrade 2>&1 | Out-Null
            & $py -m pip install --disable-pip-version-check 'jupyter-kernel-client==0.9.0'
        }
        if ($LASTEXITCODE -ne 0) { throw 'could not install jupyter-kernel-client==0.9.0' }
    }
}
