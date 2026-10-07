#!/bin/bash
set -euo pipefail

HOST="${1:-pi@192.168.1.10}"
SRC="${2:-custom_components/ha_felicity/.}"
DST="/home/pi/homeassistant/custom_components/ha_felicity"

rsync -avz --delete \
  -e ssh \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  "${SRC%/}/" \
  "$HOST:$DST/"
