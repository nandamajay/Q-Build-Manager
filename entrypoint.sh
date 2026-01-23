#!/bin/bash
set -e

# 1. Update UID/GID to match the Host User (avoids permission errors)
# We use '|| true' to suppress errors if the ID already exists
if [ ! -z "$HOST_UID" ] && [ ! -z "$HOST_GID" ]; then
    groupmod -o -g "$HOST_GID" builder 2>/dev/null || true
    usermod -o -u "$HOST_UID" -g "$HOST_GID" builder 2>/dev/null || true
fi

# 2. Fix permissions for the /work directory
chown -R builder:builder /work 2>/dev/null || true

# 3. Drop privileges and run the command (python3 web_manager.py)
# 'gosu' swaps from root -> builder user
if [ "${1:0:1}" = '-' ]; then
    set -- gosu builder "$@"
else
    # Check if we are running python, if so run as builder
    if [[ "$1" == "python"* ]]; then
        exec gosu builder "$@"
    else
        # Allow running other commands (like bash) as passed
        exec "$@"
    fi
fi
