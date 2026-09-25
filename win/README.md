# collabosm on Windows

**Requires PowerShell 7 (`pwsh`).** Every script refuses to run under Windows PowerShell 5.1,
except `install-pwsh7.ps1`, which is the bootstrap that installs pwsh 7.

Native PowerShell entry points for the collabosm pipeline (Qwen3.8-Flash-Next on one Colab
A100-80GB High-RAM, exposed as an OpenAI-compatible endpoint). No WSL / Git Bash needed; the
bash scripts in `scripts/` still run *on the Colab VM*.

## Quickstart

```powershell
cd <repo>                      # e.g. C:\work\colabmwindow
powershell -ExecutionPolicy Bypass -File win\install-pwsh7.ps1   # once, in 5.1: installs pwsh 7, then runs setup.ps1
pwsh -File win\setup.ps1   # (re-run) uv, google-colab-cli, Google sign-in, CU balance
pwsh -File win\up.ps1      # box -> bootstrap -> serve  (~15-20 min cold)
pwsh -File win\test.ps1    # health + one chat completion
pwsh -File win\down.ps1    # STOP THE VM when done
```

`up.ps1` saves `base_url` / `api_key` to `.local\endpoint.json` (gitignored). Point any
OpenAI-compatible client at `base_url` with that key.

`win\status.ps1` shows server-side sessions (what is billing), the VM stage, and the saved endpoint.

## Knobs (`up.ps1` parameters)

| parameter | default | meaning |
|---|---|---|
| `-Session` | `collabosm` | local session name |
| `-CacheSize` | `262144` | total KV tokens across all jobs (multiple of 256) |
| `-CacheQuant` | `4` | KV bits |
| `-CpuCacheGB` | `0` | pinned-RAM 2nd-tier KV cache; `8` = ~737K tokens of warm prefix, 0.58 s resume vs ~10 s |
| `-RecurrentCacheGB` | `4` | Gated-DeltaNet checkpoint store (raise to tens of GB for very long resumable histories) |
| `-Ndt` | `4` | MTP draft depth |
| `-Gcs` | `4096` | generator chunk size (`8192` measured fastest prefill) |
| `-KeepAlive` | off | hold the VM open while idle (burns CU) |

Suggested long-context agent profile:
`win\up.ps1 -CacheSize 524288 -CpuCacheGB 8 -RecurrentCacheGB 24 -Gcs 8192`

## Budget

| shape | CU/h | can run this model |
|---|---:|---|
| A100 80GB High-RAM | ~7.52 | yes |
| A100 40GB standard | ~5.37 | **no** (~63.6 GiB of weights must be VRAM-resident) |

Hours available = CU balance / 7.52 (`colab usage` shows the balance). Every `up.ps1` from a
fresh VM re-downloads ~100 GiB of weights (~5 min) and re-loads the model, so batch your work
into one session instead of cycling up/down for single experiments. Colab idle-prunes an
unattended VM after ~90 min.

## Changes vs upstream `architectds/collabosm`

- `scripts/up.sh`: also uploads `api_server.py` + `bootstrap_ok.py`, and actually launches
  `serve.sh` after `BOOTSTRAP_OK` (upstream never did, so it could only time out).
- `scripts/api_server.py`: enforces the generated API key (`Authorization: Bearer ...` or
  `x-api-key`) on `/v1/*`; upstream left the public trycloudflare URL open. `/health` stays open.
- `scripts/restore.py`: uses the native `shape=HIGH_RAM` of google-colab-cli >= 0.7 (the #47
  fix, `colab new --high-mem`), falls back to the URL-builder patch on older CLIs; finds the
  CLI's uv tool venv on Windows; records `machine_shape` in the local session.
- `scripts/status.py`: detects `api_server.py` and sends the key to `/v1/models`.
- `scripts/get_endpoint.py` (new, runs on VM): prints tunnel URL + key for `up.ps1`.
- `win/` (new): PowerShell entry points; files uploaded to the VM are forced to LF.

- `jupyter-kernel-client` is pinned to **0.9.0** inside the CLI venv (`Repair-KernelClient` in
  `win/common.ps1`, run by setup.ps1 and up.ps1). CLI 0.7.2 declares `==0.8`, but 0.8 lacks
  `JupyterSubprotocol`, so every `colab exec` fails with `AttributeError`. Do not
  `uv tool upgrade google-colab-cli` (it reverts this); re-run `win\setup.ps1` if you do.

## Decode speed vs what collabm shows

collabm prints two numbers after each reply, e.g.
`227 in / 56 out · 7.3s · 7.6 tok/s end-to-end · server 1.1s = 51 tok/s`.

| number | what it measures |
|---|---|
| **server tok/s** | generated tokens / time inside the Generator on the A100 (prefill + decode) |
| **end-to-end tok/s** | generated tokens / wall time seen by the client, including everything below |

Reference figures on this box (upstream `docs/MEASURED.md`, A100-80GB, Q4 KV):

| | tok/s |
|---|---:|
| decode, MTP `ndt=4` | 75-97 (78.6 at ~0 ctx, 97.4 at 30K, 90.1 at 114K) |
| decode, no MTP | ~56 |
| prefill | ~2,500-2,800 (`-Gcs 8192`: ~3,900) |

Why end-to-end is lower, especially for short replies:

- **Fixed overhead per request**: HTTPS through the cloudflared quick tunnel to the Colab region
  and back, plus JSON handling - roughly 0.5-2 s regardless of length. A 56-token reply is
  dominated by it; a 2,000-token reply barely notices it.
- **No streaming yet**: the reply arrives in one piece at the end, so time-to-first-token equals
  the whole generation time.
- **First request after (re)load** pays a one-time kernel autotune (several seconds).
- **Thinking tokens count**: `out` includes the hidden `<think>` reasoning, which is often most
  of the tokens.
- **Prefill**: long prompts (big `@file` attachments, long conversations) add prompt_tokens /
  ~2,700 s; repeated prefixes hit the prompt cache (`cached` in the server log) and are nearly free.
- **MTP acceptance varies** with the text (42-64% measured); code and repetitive text decode faster.

To measure the card rather than the network, ask for a long answer (1,000+ tokens) and read the
`server` figure; the server also logs `new=... end=... Xs (Y tok/s)` per request in
`/content/serve.log`.

## Windows compatibility shim

google-colab-cli 0.7.x imports the POSIX-only `termios` module at startup, so every `colab`
command crashes on native Windows (`ModuleNotFoundError: No module named 'termios'`).
`win/compat/termios.py` is a stub; `win/common.ps1` prepends `win\compat` to `PYTHONPATH`, so
all `win\*.ps1` scripts (and `restore.py`'s subprocess calls) work. To use the `colab` CLI by
hand in the same shell:

```powershell
$env:PYTHONPATH = "<repo>\win\compat"
colab sessions
```

`colab console` remains unsupported on Windows; use `colab exec` instead.

## collabm: interactive chat (PowerShell 7)

```powershell
powershell -ExecutionPolicy Bypass -File win\install-pwsh7.ps1   # once: pwsh 7 + profile shortcuts
# then, in a NEW PowerShell 7 window:
collabm            # chat; offers to start the A100 if the endpoint is down (collabm --up: no prompt)
collabm-up | collabm-status | collabm-test | collabm-down
```

`client/collabm.py` runs on the google-colab-cli venv python (requests/rich/prompt_toolkit are
already there). Enter sends, Alt+Enter inserts a newline, `@path` attaches a file, `/help` lists
commands (`/clear /system /think /temp /max /file /run /retry /undo /cd /save /status /exit`).
One-shot: `collabm -p "question"`. Transcripts: `.local\transcripts\`.

Known limits: replies arrive in one piece (the server has no token streaming yet), and the
cloudflared quick tunnel cuts any single response after ~100 s, so keep `/max` moderate (4096 at
~90 tok/s is ~45 s).
