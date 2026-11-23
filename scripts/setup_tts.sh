#!/usr/bin/env bash
# Optional: "Your voice" (Qwen3-TTS voice cloning) in its own conda env, so its torch/transformers never touch
# ClipForge's. ~3 GB of packages now; the model (~4 GB) downloads on the first build. ClipForge starts it only
# when a voice is built or speaks (backend/tts/qwen_worker.py), then it exits and the GPU is free again.
set -euo pipefail
ENV=${TTS_ENV:-clipforge-tts}
CONDA=${CONDA_EXE:-$HOME/miniconda3/bin/conda}
BASE=$("$CONDA" info --base)
PY="$BASE/envs/$ENV/bin/python"

[ -x "$PY" ] || "$CONDA" create -y -q -n "$ENV" python=3.12
"$PY" -m pip install -q -U qwen-tts
# qwen-tts pulls the newest torch (CUDA 13.x), which segfaults on CUDA init with the WSL2 driver seen here
# (592.82): pin the CUDA 12.8 build, like the rest of ClipForge.
"$PY" -m pip install -q "torch==2.9.*" "torchaudio==2.9.*" --index-url https://download.pytorch.org/whl/cu128

env -u LD_LIBRARY_PATH "$PY" -c "import torch, qwen_tts; assert torch.cuda.is_available(); \
print('TTS env ready:', torch.__version__, torch.cuda.get_device_name(0))"
[ "$ENV" = clipforge-tts ] || echo "Set TTS_PYTHON=$PY in backend/.env"
