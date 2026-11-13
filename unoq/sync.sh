#!/usr/bin/env bash
# Repo-Stand (alle von Git verfolgten Dateien + lokale Änderungen) vom Laptop
# nach /home/arduino/lingo auf den UNO Q kopieren. Kein GitHub-Zugang auf dem
# Board nötig. Aufruf aus Git Bash: UNOQ=arduino@<IP> bash unoq/sync.sh
# Windows: nutzt das Windows-OpenSSH (ssh-agent mit dem eigenen SSH-Schlüssel).
set -euo pipefail
TARGET="${UNOQ:?Bitte UNOQ=arduino@<IP-des-Boards> setzen}"
SSH=ssh
[ -x /c/WINDOWS/System32/OpenSSH/ssh.exe ] && SSH=/c/WINDOWS/System32/OpenSSH/ssh.exe
cd "$(git rev-parse --show-toplevel)"
git ls-files -z --cached --others --exclude-standard \
  | tar --null -T - -czf - \
  | "$SSH" -o BatchMode=yes "$TARGET" 'mkdir -p ~/lingo && tar xzf - -C ~/lingo && echo "sync ok: ~/lingo"'
