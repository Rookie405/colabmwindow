# bin/

Intentionally empty of binaries.

The runtime is a **prebuilt wheel** referenced by URL in `manifest.json` and installed by
`scripts/bootstrap.sh`; nothing needs to be committed here. This directory exists so that
`bin/url.txt` (gitignored) can hold a personal mirror URL if you would rather not hit GitHub releases
from the VM:

```bash
cp bin/url.txt.example bin/url.txt
$EDITOR bin/url.txt          # one URL, no trailing whitespace
```

Was this worth leaving empty? Yes, and here is the evidence: through the Colab contents proxy a single
84 MB upload failed repeatedly (`SSLEOFError`) while two ~42 MB halves succeeded. If you ever *do* need
to move a large artifact by hand, split it; do not fight the proxy with one big PUT.