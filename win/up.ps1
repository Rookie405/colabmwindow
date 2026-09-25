# collabosm up (Windows): restore/create the A100-80GB High-RAM box, bootstrap, serve, save endpoint.
#
#   pwsh -File win\up.ps1
#   pwsh -File win\up.ps1 -CacheSize 524288 -CpuCacheGB 8
#
# Same order as scripts/up.sh -- the expensive steps come last:
#   1. restore/verify the box (a 40 GB box is rejected + stopped in ~1 min, before any download)
#   2. upload the kit        3. bootstrap (wheel + ~100 GiB weights)
#   4. serve (API + cloudflared tunnel)   5. save URL + key to .local\endpoint.json
param(
    [string]$Session          = $(if ($env:SESSION) { $env:SESSION } else { 'collabosm' }),
    [ValidateSet('wheel','source')][string]$Runtime = 'wheel',
    [int]$CacheSize           = 262144,   # total KV tokens across jobs, multiple of 256
    [int]$CacheQuant          = 4,        # KV bits
    [double]$CpuCacheGB       = 0,        # pinned-RAM 2nd-tier KV cache (8 = ~737K tokens warm prefix)
    [double]$RecurrentCacheGB = 4,        # Gated-DeltaNet checkpoint store
    [int]$Ndt                 = 4,        # MTP draft depth
    [int]$Gcs                 = 4096,     # generator chunk size (8192 = faster prefill)
    [int]$Port                = 8090,
    [int]$WaitMin             = 50,
    [switch]$KeepAlive,                   # hold the VM open (costs CU while idle!)
    [switch]$RestartServer                # reload api_server.py even if one is running (after server updates; ~2-3 min)
)
$script:Tag = 'up'
. (Join-Path $PSScriptRoot 'common.ps1')

if ($CacheSize % 256 -ne 0) { throw "CacheSize must be a multiple of 256" }
$colabPy = Get-ColabPython
Repair-KernelClient
$t0 = Get-Date

# ------------------------------------------------------------------ 1. the box
Say "restoring/creating the A100-80GB High-RAM box (session: $Session)"
$restoreArgs = @((Join-Path $ScriptsDir 'restore.py'), '-n', $Session, '--colab', (Get-ColabExe))
if ($KeepAlive) { $restoreArgs += '--keepalive' }
& $colabPy @restoreArgs
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    switch ($rc) {
        3 { Say '!! too many assignments on the account - run win\status.ps1 / colab sessions' }
        6 { Say '!! a 40 GB box was drawn and already stopped (~0.13 CU). Just re-run up.ps1.' }
        default { Say "!! restore failed (rc=$rc)" }
    }
    exit $rc
}

# ------------------------------------------------------------ 2. push the kit
Say 'uploading the toolkit'
foreach ($f in 'bootstrap.sh','serve.sh','api_server.py','status.py','probe_gpu.py','bootstrap_ok.py','get_endpoint.py') {
    # re-encode to LF in case a Windows git checkout converted line endings
    $tmp = Join-Path $StateDir $f
    Write-LfFile $tmp ([System.IO.File]::ReadAllText((Join-Path $ScriptsDir $f)))
    $o = Invoke-Colab upload -s $Session $tmp "/content/$f"
    if ($LASTEXITCODE -eq 0) { Say "  ok   $f" } else { Say "  FAIL $f :: $($o.Trim())" }
}

# ------------------------------------------------------------ 3. bootstrap
Say "bootstrapping (runtime=$Runtime, cache=$CacheSize, cq=$CacheQuant, ccs=${CpuCacheGB}GB, rcs=${RecurrentCacheGB}GB, ndt=$Ndt, gcs=$Gcs)"
$envFile = Join-Path $StateDir 'collabosm_env.sh'
Write-LfFile $envFile @"
export RUNTIME=$Runtime
export CACHE_SIZE=$CacheSize
export CACHE_QUANT=$CacheQuant
export CPU_CACHE_GB=$CpuCacheGB
export RECURRENT_CACHE_GB=$RecurrentCacheGB
export NDT=$Ndt
export GCS=$Gcs
export PORT=$Port
"@
Invoke-Colab upload -s $Session $envFile /content/collabosm_env.sh | Out-Null

$startBoot = Join-Path $StateDir 'start_bootstrap.py'
Write-LfFile $startBoot @'
import subprocess
print(subprocess.run("nohup bash -lc 'source /content/collabosm_env.sh && bash /content/bootstrap.sh' "
                     "> /content/bootstrap.log 2>&1 & echo BOOTSTRAPPING", shell=True,
                     capture_output=True, text=True).stdout)
'@
$startServe = Join-Path $StateDir 'start_serve.py'
Write-LfFile $startServe @'
import subprocess
print(subprocess.run("nohup bash -lc 'bash /content/serve.sh' > /content/serve_launch.log 2>&1 & echo SERVING",
                     shell=True, capture_output=True, text=True).stdout)
'@
$o = Invoke-Colab exec -s $Session --timeout 150 -f $startBoot
if ($o -notmatch 'BOOTSTRAPPING') { Say "!! could not start bootstrap:`n$o"; exit 1 }

# ------------------------------------------------------------ 4. wait, then serve
Say "waiting up to $WaitMin min (bootstrap ~11 min from nothing, model load 2-6 min)"
$deadline = (Get-Date).AddMinutes($WaitMin)
$served = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 45
    if (-not $served) {
        $b = Invoke-Colab exec -s $Session --timeout 60 -f (Join-Path $ScriptsDir 'bootstrap_ok.py')
        if ($b -match 'bootstrap_failed') {
            Say "!! bootstrap failed:`n$b"
            Say "   the VM is still billing - fix and re-run, or stop it: win\down.ps1"
            exit 1
        }
        if ($b -match 'bootstrap_ok') {
            # Don't restart a server that is already up (a restart reloads the model: 2-6 min).
            $pre = Invoke-Colab exec -s $Session --timeout 60 -f (Join-Path $ScriptsDir 'status.py')
            if (-not $RestartServer -and ($pre -match 'health:\s+200' -or $pre -match 'engine_running:\s+True')) {
                Say 'bootstrap OK, API server already running/loading -> reusing it'
            } else {
                Say 'bootstrap OK -> launching serve.sh (model load 2-6 min)'
                Invoke-Colab exec -s $Session --timeout 150 -f $startServe | Out-Null
            }
            $served = $true
        } else {
            $tail = ($b -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join ' | '
            Say "  bootstrap running... $tail"
            continue
        }
    }
    $s = Invoke-Colab exec -s $Session --timeout 60 -f (Join-Path $ScriptsDir 'status.py')
    $stage = ($s -split "`n" | Where-Object { $_ -match '^stage:' }) -join ''
    if (-not $stage) { Say "  !! status.py gave no stage line:`n$s" } else { Say "  $($stage.Trim())" }
    if ($s -match 'serve_failed|serve_timeout') {
        Say "!! serve failed. Inspect /content/serve.log:  colab exec -s $Session  (or win\status.ps1)"
        exit 1
    }
    if ($s -match 'health:\s+200' -and $s -match 'tunnel:\s+https://') {
        $e = Invoke-Colab exec -s $Session --timeout 60 -f (Join-Path $ScriptsDir 'get_endpoint.py')
        $line = ($e -split "`n" | Where-Object { $_ -like 'ENDPOINT *' } | Select-Object -First 1)
        $info = $line.Substring(9) | ConvertFrom-Json
        $rec = [ordered]@{ session = $Session; url = $info.url; key = $info.key;
                           base_url = "$($info.url)/v1"; model = 'qwen3.8-flash-next-exl3';
                           started = (Get-Date).ToString('s') }
        ($rec | ConvertTo-Json) | Set-Content -Encoding UTF8 (Join-Path $StateDir 'endpoint.json')
        $mins = [int]((Get-Date) - $t0).TotalMinutes
        Say "READY in $mins min"
        Write-Host ""
        Write-Host "  base_url : $($rec.base_url)"
        Write-Host "  api_key  : $($rec.key)"
        Write-Host "  model    : $($rec.model)"
        Write-Host "  saved to : .local\endpoint.json"
        Write-Host ""
        Write-Host "  smoke test : pwsh -File win\test.ps1"
        Write-Host "  WHEN DONE  : pwsh -File win\down.ps1   (~7.52 CU/h while up)"
        exit 0
    }
}
Say "!! timed out after $WaitMin min - the VM is still billing. Check win\status.ps1 or run win\down.ps1"
exit 1
