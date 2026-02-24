#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate shortvideo-agent

shortvideo-agent --help
shortvideo-agent run --help