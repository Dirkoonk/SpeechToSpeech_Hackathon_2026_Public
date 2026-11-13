#!/usr/bin/env bash
# Sketch unoq/sketch/lingo_mcu auf dem UNO Q kompilieren (und hochladen).
# Aufruf aus Git Bash:  bash unoq/mcu.sh [compile|upload]
# Hochladen läuft LOKAL auf dem Board (remoteocd ohne Netzwerkadresse),
# damit nie versehentlich ein anderer UNO Q im WLAN beschrieben wird.
set -euo pipefail
ACTION="${1:-upload}"
TARGET="${UNOQ:?Bitte UNOQ=arduino@<IP-des-Boards> setzen}"
SSH=ssh
[ -x /c/WINDOWS/System32/OpenSSH/ssh.exe ] && SSH=/c/WINDOWS/System32/OpenSSH/ssh.exe
bash "$(dirname "$0")/sync.sh"
"$SSH" -o BatchMode=yes "$TARGET" "ACTION=$ACTION bash -s" <<'EOF'
set -euo pipefail
FQBN=arduino:zephyr:unoq
SK=~/lingo/unoq/sketch/lingo_mcu
BUILD=/tmp/lingo_mcu_build
arduino-cli compile --fqbn $FQBN --build-path $BUILD $SK 2>&1 | grep -vE "^$" | tail -4
[ "$ACTION" = upload ] || exit 0
arduino-cli upload --fqbn $FQBN --input-dir $BUILD 2>&1 | tail -5
EOF
