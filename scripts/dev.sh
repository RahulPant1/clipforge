#!/usr/bin/env bash
# Starts FastAPI (8000) + Vite (5173). Ctrl+C stops both.  Usage: bash scripts/dev.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

eval "$(conda shell.bash hook)"
conda activate "${ENV_NAME:-clipforge}"
# nvm (and some conda hooks) reference unset variables — relax `set -u` while loading them
set +u
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  source "$HOME/.nvm/nvm.sh"
  nvm use --lts >/dev/null 2>&1 || nvm use default >/dev/null 2>&1 || true  # any Node 18+ works
fi
set -u
export PATH="$HOME/.deno/bin:$PATH"

# cuBLAS/cuDNN from pip wheels for faster-whisper on GPU
# (namespace packages in newer wheels: __file__ is None, so use __path__)
LIBS=$(python -c 'import nvidia.cublas.lib as b, nvidia.cudnn.lib as d; print(list(b.__path__)[0] + ":" + list(d.__path__)[0])' 2>/dev/null || true)
[ -n "$LIBS" ] && export LD_LIBRARY_PATH="$LIBS:${LD_LIBRARY_PATH:-}"

cd "$ROOT/backend"
# Graceful-shutdown timeout: video streams aborted by the browser can leave the Vite proxy holding a stalled
# connection open, and without a timeout --reload waits for it forever (API silently stops responding).
uvicorn app.main:app --reload --port 8000 --timeout-graceful-shutdown 3 &
API=$!
cd "$ROOT/frontend"
npm run dev &
UI=$!

trap 'kill $API $UI 2>/dev/null' INT TERM EXIT
echo "API → http://localhost:8000/docs    UI → http://localhost:5173"
wait
