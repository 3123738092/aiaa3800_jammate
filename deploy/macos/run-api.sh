#!/bin/bash
set -euo pipefail
umask 077
base="$HOME/jammate-team"
cd -P "${1:-$base/current}"
port="${2:-4215}"
source "$base/config/providers.env"
export PATH="$base/bin:$PATH"
export PYTHONPATH="$PWD/src:$PWD"
export PYTHONUNBUFFERED=1
export JAMMATE_RELEASE="$(cat .release 2>/dev/null || true)"
export JAMMATE_DRAIN_FILE="$base/config/deploy-drain"
export JAMMATE_XFYUN_ASR_CONFIG="$base/config/xfyun-asr.secrets.json"
export JAMMATE_LLM_CONFIG="$base/config/chatanywhere-llm.secrets.json"
export JAMMATE_AUDIO_ROUTER_CONFIG="${JAMMATE_AUDIO_ROUTER_CONFIG:-$JAMMATE_LLM_CONFIG}"
export JAMMATE_ASR_PRIMARY=qwen
export JAMMATE_MRT2_ENGINE=mlx JAMMATE_MRT2_MODEL=mrt2_small
export MAGENTA_HOME="$base/models"
export JAMMATE_PUBLIC_ORIGIN_FILE="$base/config/backup-origin"
if [ -s "$base/config/public-origin" ]; then
    export JAMMATE_PUBLIC_ORIGIN="$(cat "$base/config/public-origin")"
fi
python="$base/venv/bin/python"
if [ -s .python ]; then
    python="$(cat .python)"
fi
exec "$python" -m jammate.input_server --implementation workspace --port "$port"
