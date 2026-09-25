# Smoke-test the endpoint saved by up.ps1 (health, models, one chat completion).
#   powershell -ExecutionPolicy Bypass -File win\test.ps1 [-Prompt "..."] [-MaxTokens 256]
param([string]$Prompt = 'In two sentences: what is a Gated-DeltaNet layer?', [int]$MaxTokens = 256)
$script:Tag = 'test'
. (Join-Path $PSScriptRoot 'common.ps1')

$e = Get-Endpoint
if (-not $e) { throw 'no .local\endpoint.json - run win\up.ps1 first' }
$h = @{ Authorization = "Bearer $($e.key)" }

Say "health: $(Invoke-RestMethod -Uri "$($e.url)/health" -TimeoutSec 20)"
$m = Invoke-RestMethod -Uri "$($e.base_url)/models" -Headers $h -TimeoutSec 20
Say ("models: " + (($m.data | ForEach-Object { $_.id }) -join ', '))

$body = @{ model = $e.model; max_tokens = $MaxTokens; temperature = 0.6;
           messages = @(@{ role = 'user'; content = $Prompt }) } | ConvertTo-Json -Depth 5
$sw = [Diagnostics.Stopwatch]::StartNew()
$r = Invoke-RestMethod -Method Post -Uri "$($e.base_url)/chat/completions" -Headers $h `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 600
$sw.Stop()
$ct = $r.usage.completion_tokens
Say ("{0} prompt + {1} completion tokens in {2:N1}s ({3:N1} t/s end-to-end)" -f `
     $r.usage.prompt_tokens, $ct, $sw.Elapsed.TotalSeconds, ($ct / [Math]::Max($sw.Elapsed.TotalSeconds, 0.001)))
Write-Host "`n$($r.choices[0].message.content)`n"

Write-Host 'OpenAI-compatible client settings (PowerShell):'
Write-Host "  `$env:OPENAI_BASE_URL = '$($e.base_url)'"
Write-Host "  `$env:OPENAI_API_KEY  = '$($e.key)'"
