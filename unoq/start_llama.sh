#!/usr/bin/env bash
# llama-server auf dem UNO Q starten (läuft auf dem Board).
# RAM-sparsam: 1 Slot, 2048 Token Kontext, kleine Batches, Prompt-Cache im
# Host-RAM auf 128 MiB begrenzt (Standard wären 8 GiB!), Modell per mmap.
# Gemessen (llama-bench, 4x A53): Prompt ~11,5 Tok/s, Ausgabe ~5,5 Tok/s;
# FA/KV-Quantisierung bringen hier nichts. Fester System-Prompt wird per
# Context-Checkpoint wiederverwendet (-cms 0).
# Aufruf: bash ~/lingo/unoq/start_llama.sh        (startet im Hintergrund)
#         bash ~/lingo/unoq/start_llama.sh --fg   (im Vordergrund)
set -euo pipefail
MODELS="${LINGO_MODELS:-/home/arduino/models}"
BIN="${LLAMA_BIN:-/home/arduino/opt/llama/llama-b11988}"
MODEL="$MODELS/llm/Qwen3.5-0.8B-Q8_0.gguf"
PORT=8080

if curl -s -m 1 "http://127.0.0.1:$PORT/health" | grep -q ok; then
  echo "llama-server läuft bereits auf :$PORT"; exit 0
fi

# 9 Slots (1 Dolmetscher + 8 Themen des Assistenten) mit je 1024 Token Kontext: Jedes Thema
# behält seine gelesenen Daten im Speicher (siehe SLOT in s2s_pipeline.py). ~1,4 GB statt ~1,1 GB RAM.
# Der zusätzliche Host-Cache (-cram) bleibt aus: Er traf bei diesem Modell nur manchmal.
SLOTS="${LLAMA_SLOTS:-9}"
ARGS=(-m "$MODEL" --host 127.0.0.1 --port $PORT
      -c $((SLOTS * 1024)) -np "$SLOTS" -t 4 -b 256 -ub 256
      -cram 0 -cms 0 --no-webui)

export LD_LIBRARY_PATH="$BIN"
if [ "${1:-}" = "--fg" ]; then
  exec "$BIN/llama-server" "${ARGS[@]}"
fi
nohup "$BIN/llama-server" "${ARGS[@]}" > /tmp/llama-server.log 2>&1 < /dev/null &
for i in $(seq 1 60); do
  curl -s -m 1 "http://127.0.0.1:$PORT/health" | grep -q ok && { echo "llama-server bereit (:$PORT)"; exit 0; }
  sleep 1
done
echo "llama-server startet nicht, siehe /tmp/llama-server.log" >&2; exit 1
