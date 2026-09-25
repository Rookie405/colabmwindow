"""Runs ON the Colab VM: print the public tunnel URL and the API key as one line.

Output: `ENDPOINT {"url": "...", "key": "...", "stage": "..."}`
"""
import json
import os
import re

d = {"url": None, "key": None, "stage": None}
try:
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",
                  open("/content/tunnel.log", errors="replace").read())
    d["url"] = m.group(0) if m else None
except Exception:
    pass
if os.path.exists("/content/api-key.txt"):
    d["key"] = open("/content/api-key.txt").read().strip()
if os.path.exists("/content/STATUS"):
    d["stage"] = open("/content/STATUS", errors="replace").read().strip()
print("ENDPOINT " + json.dumps(d))
