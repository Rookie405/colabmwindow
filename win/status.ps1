# One-glance status of the VM + endpoint.
param([string]$Session = $(if ($env:SESSION) { $env:SESSION } else { 'collabosm' }))
$script:Tag = 'status'
. (Join-Path $PSScriptRoot 'common.ps1')

Write-Host '--- server-side sessions'
Write-Host (Invoke-Colab sessions)
Write-Host "--- VM status ($Session)"
Write-Host (Invoke-Colab exec -s $Session --timeout 60 -f (Join-Path $ScriptsDir 'status.py'))
$e = Get-Endpoint
if ($e) { Write-Host ("--- local endpoint record`n  base_url: {0}/v1`n  api_key:  {1}" -f $e.url, $e.key) }
