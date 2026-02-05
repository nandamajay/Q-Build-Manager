#!/bin/bash
set -e

# 0) Control flags (defaults: do NOT recursively chown /work)
CHOWN_WORK="${CHOWN_WORK:-0}"

# 1) Update UID/GID to match the Host User (avoids permission errors)
#    Safe if they already exist.
if [ ! -z "$HOST_UID" ] && [ ! -z "$HOST_GID" ]; then
  groupmod -o -g "$HOST_GID" builder 2>/dev/null || true
  usermod  -o -u "$HOST_UID" -g "$HOST_GID" builder 2>/dev/null || true
fi

# 2) Optional: fix permissions for /work (expensive on large trees)
if [ "$CHOWN_WORK" = "1" ]; then
  echo ">> CHOWN_WORK=1: chown -R builder:builder /work (this may take time)..."
  chown -R builder:builder /work 2>/dev/null || true
fi

# 3) Drop privileges and run the command (python3 web_manager.py or others)
if [[ "${1:0:1}" = '-' ]]; then
  set -- gosu builder "$@"
else
  if [[ "$1" == "python"* ]]; then
    exec gosu builder "$@"
  else
    exec "$@"
  fi
fi
