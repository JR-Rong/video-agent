#!/bin/bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# 1) Download & install Miniconda (if not exists)
if ! command -v conda >/dev/null 2>&1; then
  echo "[1/4] Installing Miniconda..."
  MINICONDA="$HOME/miniconda3"
  if [ ! -d "$MINICONDA" ]; then
    curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
    bash /tmp/miniconda.sh -b -p "$MINICONDA"
  fi
  # shellcheck disable=SC1091
  source "$MINICONDA/etc/profile.d/conda.sh"
else
  echo "[1/4] Conda already installed."
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
fi

# 2) Create env
ENV_NAME="shortvideo-agent"
PY_VER="3.11"
if ! conda env list | awk '{print \$1}' | grep -qx "$ENV_NAME"; then
  echo "[2/4] Creating conda env: $ENV_NAME"
  conda create -y -n "$ENV_NAME" python="$PY_VER" pip
else
  echo "[2/4] Conda env exists: $ENV_NAME"
fi

conda activate "$ENV_NAME"

# 3) Install dependencies
echo "[3/4] Installing dependencies..."
pip install -U pip
pip install -e .

# Optional: install ffmpeg via conda (recommended)
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Installing ffmpeg via conda-forge..."
  conda install -y -c conda-forge ffmpeg
fi

echo "[4/4] Done."
echo "Activate: conda activate $ENV_NAME"
echo "Run: bash scripts/run_linux.sh"