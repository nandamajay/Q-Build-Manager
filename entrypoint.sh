#!/bin/bash

# Defaults
USER_ID=${HOST_UID:-1000}
GROUP_ID=${HOST_GID:-1000}

echo ">> Configuring container for UID: $USER_ID / GID: $GROUP_ID"

# 1. Handle Group
# If the group 'builder' already exists, modify its GID to match the host
if getent group builder >/dev/null; then
    groupmod -g $GROUP_ID builder
else
    # Otherwise, create it if the GID isn't taken by someone else
    if ! getent group $GROUP_ID >/dev/null; then
        groupadd -g $GROUP_ID builder
    fi
fi

# 2. Handle User
if id builder >/dev/null 2>&1; then
    # User exists, modify UID and GID
    usermod -u $USER_ID -g $GROUP_ID builder
else
    # Create user if it doesn't exist
    useradd -u $USER_ID -g $GROUP_ID -m -s /bin/bash builder
fi

# 3. Permissions
chown -R $USER_ID:$GROUP_ID /home/builder

# 4. Execute
export HOME=/home/builder
exec /usr/sbin/gosu $USER_ID:$GROUP_ID "$@"
