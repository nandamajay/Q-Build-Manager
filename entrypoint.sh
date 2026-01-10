#!/bin/bash
set -e

USER_ID=${HOST_UID:-1000}
GROUP_ID=${HOST_GID:-1000}

# Handle Root Host Case
if [ "$USER_ID" -eq 0 ]; then
    USER_ID=1000
    GROUP_ID=1000
fi

if ! getent group "$GROUP_ID" > /dev/null; then
    groupadd -g "$GROUP_ID" builder
fi

if ! id -u builder > /dev/null 2>&1; then
    useradd -u "$USER_ID" -g "$GROUP_ID" -m builder
fi

# Fix permissions
chown -R builder:builder /work

export TERM=xterm-256color
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8

# Switch to builder and run the command passed from docker run
exec gosu builder "$@"
