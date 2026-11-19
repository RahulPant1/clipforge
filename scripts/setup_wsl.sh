#!/usr/bin/env bash
# One-time setup on WSL (Ubuntu) with Miniconda + NVM. Run from repo root:  bash scripts/setup_wsl.sh
set -euo pipefail

ENV_NAME=${ENV_NAME:-clipforge}
GEMMA_MODEL=${GEMMA_MODEL:-gemma3:4b}

echo "==> System packages (ffmpeg, unzip, fonts)"
sudo apt-get update -y && sudo apt-get install -y ffmpeg unzip curl fonts-dejavu-core

echo "==> Deno (JS runtime yt-dlp needs for YouTube challenges)"
if ! command -v deno >/dev/null; then
  curl -fsSL https://deno.land/install.sh | sh -s -- -y
  echo 'export PATH="$HOME/.deno/bin:$PATH"' >> ~/.bashrc
fi

echo "==> Conda env: $ENV_NAME"
eval "$(conda shell.bash hook)"
conda env list | grep -q "^$ENV_NAME " || conda create -n "$ENV_NAME" python=3.11 -y
conda activate "$ENV_NAME"

echo "==> Python deps"
pip install -r backend/requirements-dev.txt
# CUDA 12 runtime libs for faster-whisper (ctranslate2) — avoids system-wide CUDA installs in WSL
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"

echo "==> Frontend deps"
if command -v nvm >/dev/null 2>&1 || [ -s "$HOME/.nvm/nvm.sh" ]; then
  set +u  # nvm.sh references unset variables
  source "$HOME/.nvm/nvm.sh"; nvm use --lts >/dev/null || nvm install --lts
  set -u
fi
(cd frontend && npm install)

echo "==> Backend .env"
[ -f backend/.env ] || cp backend/.env.example backend/.env

echo "==> Ollama model"
if command -v ollama >/dev/null; then
  ollama pull "$GEMMA_MODEL"
else
  echo "   ollama CLI not found in WSL. If Ollama runs on Windows, pull there:  ollama pull $GEMMA_MODEL"
  echo "   and make sure WSL can reach it (see README > Ollama on Windows)."
fi

echo "==> Checks"
nvidia-smi --query-gpu=name,memory.total --format=csv || echo "   nvidia-smi not visible — Whisper will need WHISPER_DEVICE=cpu"
ffmpeg -hide_banner -encoders 2>/dev/null | grep -q h264_nvenc && echo "   NVENC available: set ENCODER=h264_nvenc in backend/.env" || echo "   NVENC not in this ffmpeg build — keep ENCODER=libx264"

echo "Done. Start everything with:  bash scripts/dev.sh"
