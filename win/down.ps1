# Stop the VM. On a metered plan this is the most important command.
#   pwsh -File win\down.ps1 [-Session collabosm]
param([string]$Session = $(if ($env:SESSION) { $env:SESSION } else { 'collabosm' }))
$script:Tag = 'down'
. (Join-Path $PSScriptRoot 'common.ps1')

Say "stopping '$Session' ..."
Write-Host (Invoke-Colab stop -s $Session)
Say 'remaining server-side assignments (anything listed is STILL BILLING):'
Write-Host (Invoke-Colab sessions)
$ep = Join-Path $StateDir 'endpoint.json'
if (Test-Path $ep) { Remove-Item $ep -Force }
Write-Host 'A100 80GB High-RAM ~7.52 CU/h. Double-check with: colab sessions / colab usage'
