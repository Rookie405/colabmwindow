#!/usr/bin/env bash
# collabosm down: stop the VM. On a metered plan this is the most important script here.
#
#   bash scripts/down.sh            # stop session $SESSION
#   SESSION=mybox bash scripts/down.sh
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
COLAB=${COLAB:-$(command -v colab 2>/dev/null || echo "$HOME/.local/bin/colab")}
SESSION=${SESSION:-collabosm}
CU_PER_HOUR=${CU_PER_HOUR:-7.52}     # A100 SXM4 High-RAM (80 GB / 167 GB) on our account

echo "[down] stopping '$SESSION' ..."
out=$($COLAB stop -s "$SESSION" 2>&1) && echo "$out" || { echo "$out"; echo "[down] stop returned nonzero"; }

echo "[down] remaining server-side assignments:"
$COLAB sessions 2>&1 | sed 's/^/  /' || true

cat <<EOF

[down] If an assignment is still listed above, it is still billing.
       A100-80GB High-RAM costs about ${CU_PER_HOUR} CU/h (~\$0.75/h). 200 CU/month ≈ 26.6 h.
       Nothing here starts a keep-alive daemon on purpose: a keep-alive is what turns
       a 2 h session into a 24 h one.
EOF