#!/bin/bash
set -e

# Default to 1000 if not set
HOST_UID=${HOST_UID:-1000}
HOST_GID=${HOST_GID:-1000}

echo ">> Configuring container for UID: $HOST_UID / GID: $HOST_GID"

# Handle Group
if getent group builder > /dev/null 2>&1; then
    groupmod -g $HOST_GID builder
else
    groupadd -g $HOST_GID builder
fi

# Handle User
if id -u builder > /dev/null 2>&1; then
    usermod -u $HOST_UID -g $HOST_GID builder
else
    useradd -u $HOST_UID -g $HOST_GID -m -s /bin/bash builder
fi

# Ensure permissions
chown -R builder:builder /work

# Switch to 'builder' user and run the command
exec gosu builder "$@"