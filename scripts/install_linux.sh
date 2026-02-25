#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

ENV_NAME="shortvideo-agent"
PY_VER="3.11"

echo "[0/7] Writing ~/.condarc (TUNA mirrors, strict)..."
cat > ~/.condarc << 'EOF'
show_channel_urls: true
channel_priority: strict
ssl_verify: true

default_channels:
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r
  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/msys2

custom_channels:
  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
  pytorch: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud
  nvidia: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud

channels:
  - conda-forge
  - defaults

remote_connect_timeout_secs: 60
remote_read_timeout_secs: 180
EOF

echo "----- ~/.condarc -----"
cat ~/.condarc
echo "----------------------"

# 1) Init conda
if ! command -v conda >/dev/null 2>&1; then
  echo "[1/7] Installing Miniconda..."
  MINICONDA="$HOME/miniconda3"
  if [ ! -d "$MINICONDA" ]; then
    curl -fsSL -o /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
    bash /tmp/miniconda.sh -b -p "$MINICONDA"
  fi
  # shellcheck disable=SC1091
  source "$MINICONDA/etc/profile.d/conda.sh"
else
  echo "[1/7] Conda already installed."
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh"
fi

# remove base condarc (repo.anaconda.com injection)
CONDA_BASE="$(conda info --base)"
BASE_CONDARC="${CONDA_BASE}/.condarc"
if [ -f "$BASE_CONDARC" ]; then
  echo "Found base condarc at $BASE_CONDARC, backing up to ${BASE_CONDARC}.bak"
  cp "$BASE_CONDARC" "${BASE_CONDARC}.bak"
  rm -f "$BASE_CONDARC"
fi

echo "[2/7] show-sources:"
conda config --show-sources || true

echo "[3/7] conda base: $CONDA_BASE"
ENVS_DIR="$CONDA_BASE/envs"
TARGET_PREFIX="$ENVS_DIR/$ENV_NAME"
ALT_PREFIX_1="$HOME/miniconda3/envs/$ENV_NAME"
ALT_PREFIX_2="$HOME/anaconda3/envs/$ENV_NAME"

echo "Target env prefix: $TARGET_PREFIX"
mkdir -p "$ENVS_DIR"

# If env exists in other conda distributions, warn
if [ -d "$ALT_PREFIX_1" ] && [ "$TARGET_PREFIX" != "$ALT_PREFIX_1" ] && [ ! -d "$TARGET_PREFIX" ]; then
  echo "Warning: Found env in miniconda3: $ALT_PREFIX_1"
  echo "But current conda base is: $CONDA_BASE"
  echo "You are likely mixing two conda installs. This script will create env under current base."
fi
if [ -d "$ALT_PREFIX_2" ] && [ "$TARGET_PREFIX" != "$ALT_PREFIX_2" ] && [ ! -d "$TARGET_PREFIX" ]; then
  echo "Warning: Found env in anaconda3: $ALT_PREFIX_2"
fi

echo "[4/7] Creating/ensuring env: $ENV_NAME"
if [ -d "$TARGET_PREFIX" ]; then
  echo "Env directory exists: $TARGET_PREFIX"
else
  conda clean --all -y || true
  conda create -y -n "$ENV_NAME" python="$PY_VER" pip
fi

echo "[5/7] Activating env..."
conda activate "$ENV_NAME"

echo "[6/7] Installing project..."
python -m pip install -U pip
pip install -e .

echo "[7/7] Installing ffmpeg if missing..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  conda install -y -c conda-forge ffmpeg
fi

echo "Done."
echo "Activate: conda activate $ENV_NAME"
echo "Run: shortvideo-agent --help"