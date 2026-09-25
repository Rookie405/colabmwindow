# One-time setup on Windows: uv + google-colab-cli + Google sign-in + CU balance check.
#   pwsh -File win\setup.ps1
$script:Tag = 'setup'
. (Join-Path $PSScriptRoot 'common.ps1')

# 1. uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say 'installing uv (astral.sh)'
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Say ("uv: " + (& uv --version))

# 2. google-colab-cli (>= 0.7 has native --high-mem, i.e. the A100-80GB shape)
$have = $false
try { $have = [bool](& uv tool list 2>$null | Select-String '^google-colab-cli') } catch {}
if (-not $have) {
    Say 'installing google-colab-cli'
    & uv tool install 'google-colab-cli>=0.7.2'
} else {
    Say 'google-colab-cli already installed'
}
& uv tool update-shell 2>$null | Out-Null
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
Say ("colab: " + (Invoke-Colab version).Trim())
Say ("colab python: " + (Get-ColabPython))
Repair-KernelClient

# 3. sign in (opens a browser on first use) and show the compute-unit balance
Say 'checking account / compute units (a browser sign-in may open)'
Write-Host (Invoke-Colab usage)

Write-Host @"

Budget reference (from docs/RUNBOOK.md):
  A100 80GB High-RAM  ~7.52 CU/h   <- required by this model (Qwen3.8-Flash-Next 4.05bpw)
  A100 40GB standard  ~5.37 CU/h   <- CANNOT load this model
  hours = CU balance / 7.52

Next:  pwsh -File win\up.ps1
"@
