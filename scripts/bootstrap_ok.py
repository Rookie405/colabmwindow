"""Runs ON the Colab VM: did bootstrap finish, and did it fail?

Prints one of bootstrap_not_started / bootstrap_failed / bootstrap_ok / bootstrap_running.
While running it also prints one clean progress line (no tqdm carriage returns):
    PROGRESS stage=weights downloaded=42.1/100.1 GiB
"""
import os
import subprocess

log = "/content/bootstrap.log"
if not os.path.exists(log):
    print("bootstrap_not_started")
    raise SystemExit(0)
txt = open(log, errors="replace").read()
if "BOOTSTRAP_FAILED" in txt:
    print("bootstrap_failed")
    print(txt[-1500:].replace("\r", "\n"))
elif "BOOTSTRAP_OK" in txt:
    print("bootstrap_ok")
else:
    print("bootstrap_running")
    stage = "?"
    if os.path.exists("/content/STATUS"):
        stage = open("/content/STATUS", errors="replace").read().strip().replace("stage=", "")
    model_dir = os.environ.get("MODEL_DIR", "/content/exl3")
    got = 0.0
    try:
        out = subprocess.run(["du", "-sb", model_dir], capture_output=True, text=True).stdout
        got = int(out.split()[0]) / 2 ** 30 if out else 0.0
    except Exception:
        pass
    print("PROGRESS stage=%s downloaded=%.1f/100.1 GiB" % (stage, got))
