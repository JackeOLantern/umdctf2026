#!/usr/bin/env bash
set -euo pipefail
ARTDIR="${1:-choppediver_artifacts_v2}"
python3 -m pip install websockets pillow numpy
python3 solve_choppediver_v2.py \
  --url wss://choppediver.challs.umdctf.io:4443/ \
  --insecure -v \
  --artifact-dir "$ARTDIR"
