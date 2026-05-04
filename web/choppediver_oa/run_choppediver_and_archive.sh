#!/usr/bin/env bash
set -euo pipefail

ARTDIR="${1:-choppediver_artifacts}"
URL="${CHOPPEDIVER_URL:-wss://choppediver.challs.umdctf.io:4443/}"

python3 -m pip install websockets pillow numpy
python3 solve_choppediver_all_in_one.py \
  --url "$URL" \
  --insecure \
  -v \
  --artifact-dir "$ARTDIR"

echo
printf 'Artifacts written to: %s\n' "$ARTDIR"
printf 'Archive: %s\n' "$ARTDIR/choppediver_artifacts.zip"
printf 'Event log: %s\n' "$ARTDIR/events.jsonl"
printf 'Summary: %s\n' "$ARTDIR/summary.txt"
if [[ -f "$ARTDIR/flag.txt" ]]; then
  printf 'Flag: '
  cat "$ARTDIR/flag.txt"
fi
