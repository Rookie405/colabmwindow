"""Runs ON the Colab VM: report the box identity and the numbers that decide go/no-go.

Prints exactly one line beginning with `PROBE ` so the caller can parse it without
depending on anything else on stdout.

NOTE for anyone tempted to shortcut this: NEVER detect an 80 GB card by grepping the
output for "80". An A100's compute capability is sm_80, so a 40 GB box prints "80"
all over the place and will report itself as suitable. Compare vram_GiB.
"""
import json
import os
import subprocess
import sys


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception:
        return ""


d = {}
try:
    import torch
    d["torch"] = torch.__version__
    d["cuda"] = getattr(torch.version, "cuda", None)
    if torch.cuda.is_available():
        d["device"] = torch.cuda.get_device_name(0)
        p = torch.cuda.get_device_properties(0)
        d["cc"] = "%d.%d" % (p.major, p.minor)          # 8.0 for A100 -- NOT a size test
        d["vram_bytes"] = int(p.total_memory)
        d["vram_GiB"] = round(p.total_memory / 2 ** 30, 1)
        d["gpu_count"] = torch.cuda.device_count()
    else:
        d["cuda_available"] = False
except Exception as exc:
    d["torch_err"] = repr(exc)[:200]

try:
    d["ram_GiB"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
except Exception:
    pass

d["python"] = sys.version.split()[0]
d["cpus"] = os.cpu_count()
d["disk_free"] = sh("df -BG --output=avail /content | tail -1").strip()
d["nvcc"] = sh("nvcc --version 2>/dev/null | grep -o 'release [0-9.]*'").strip()
d["hostname"] = sh("hostname")

print("PROBE " + json.dumps(d))