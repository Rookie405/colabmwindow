# collabm: open the interactive chat. If the endpoint is down, offer to (re)start the A100 first.
#   collabm                    (after install-pwsh7.ps1 added it to your profile)
#   pwsh -File win\collabm.ps1 [--up] [--here] [-c] [-p "one-shot question"] [--show-thinking]
# Opens in ..\collabm-workspace (or $env:COLLABM_WORKSPACE / --here); conversations are saved there.
$script:Tag = 'collabm'
. (Join-Path $PSScriptRoot 'common.ps1')

$autoUp = $args -contains '--up'
$here   = $args -contains '--here'
$pass = @($args | Where-Object { $_ -notin @('--up', '--here') })

# Workspace: conversations, COLLABM.md, @file and /run all live here.
# Default: ..\collabm-workspace next to this repo; override with $env:COLLABM_WORKSPACE or --here.
if ($here) {
    $workspace = (Get-Location).Path
} elseif ($env:COLLABM_WORKSPACE) {
    $workspace = $env:COLLABM_WORKSPACE
} else {
    $workspace = Join-Path (Split-Path $Root -Parent) 'collabm-workspace'
}
if (-not (Test-Path $workspace)) {
    New-Item -ItemType Directory -Path $workspace | Out-Null
    $tpl = Join-Path $Root 'workspace-template'
    if (Test-Path $tpl) { Copy-Item -Path (Join-Path $tpl '*') -Destination $workspace -Recurse -Force }
}
$workspace = (Resolve-Path $workspace).Path

function Test-Endpoint {
    $e = Get-Endpoint
    if (-not $e) { return $false }
    try {
        $r = Invoke-WebRequest -Uri "$($e.url)/health" -UseBasicParsing -TimeoutSec 10
        return $r.StatusCode -eq 200
    } catch { return $false }
}

if (-not (Test-Endpoint)) {
    Say 'endpoint not reachable (VM stopped, tunnel changed, or never started).'
    $go = $autoUp
    if (-not $go) {
        $ans = Read-Host 'Start/resume the A100 now via win\up.ps1? It bills ~7.52 CU/h until win\down.ps1 [y/N]'
        $go = $ans -match '^(y|yes)$'
    }
    if (-not $go) { exit 1 }
    & (Join-Path $PSScriptRoot 'up.ps1')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Endpoint)) { Say '!! endpoint still not up; see output above.'; exit 1 }
}

$py = Get-ColabPython          # the colab CLI venv already has requests, rich, prompt_toolkit
Set-Location $workspace
& $py (Join-Path $Root 'client\collabm.py') --workdir $workspace @pass
exit $LASTEXITCODE
