#!/bin/bash

# Defaults
USER_ID=${HOST_UID:-1000}
GROUP_ID=${HOST_GID:-1000}

echo ">> Configuring container for UID: $USER_ID / GID: $GROUP_ID"

# 1. Create Group if missing
if ! getent group $GROUP_ID >/dev/null; then
    groupadd -g $GROUP_ID builder
fi

# 2. Create User if missing
if ! getent passwd $USER_ID >/dev/null; then
    useradd -u $USER_ID -g $GROUP_ID -m -s /bin/bash builder
else
    # If user exists (e.g. ubuntu), modify it
    usermod -u $USER_ID -g $GROUP_ID builder 2>/dev/null || true
fi

# 3. FIX: DO NOT chown /work recursively. It hangs on Yocto builds.
# Only fix the home directory permissions
chown -R $USER_ID:$GROUP_ID /home/builder

# 4. Execute the command as the user
export HOME=/home/builder
exec /usr/sbin/gosu $USER_ID:$GROUP_ID "$@"
