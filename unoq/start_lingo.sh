#!/usr/bin/env bash
# Lingo starten (auf dem UNO Q): App im Vollbild auf dem angeschlossenen Display.
# Der llama-server läuft NICHT dauerhaft: Die App startet ihn erst, wenn Assistent oder
# Dolmetscher gewählt wird, und beendet ihn danach wieder (spart ~1,1 GB RAM).
#   bash ~/lingo/unoq/start_lingo.sh            Demo (keine Logs)
#   bash ~/lingo/unoq/start_lingo.sh --debug    Logs + CSV (~/lingo_results.csv)
# Weitere Optionen werden an lingo_app.py durchgereicht (--window, --no-ble, --save).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export DISPLAY="${DISPLAY:-:0}" XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
# Display darf in der Demo nicht schwarz werden (Bildschirmschoner/DPMS aus, aufwecken)
xset s off s noblank -dpms 2>/dev/null || true
xset dpms force on 2>/dev/null; xset s reset 2>/dev/null || true
# Mikro-Verstärkung: 100 % übersteuert und nimmt zu viel Hintergrund auf (geht nach Neustart verloren)
amixer -q -c Set sset Mic "${LINGO_MIC_GAIN:-11}" 2>/dev/null || true
pkill -f "python.*lingo_app.py" 2>/dev/null || true
cd "$HERE/python"
exec "$HOME/lingo-venv/bin/python" lingo_app.py "$@"
