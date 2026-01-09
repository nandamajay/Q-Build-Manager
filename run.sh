#!/bin/bash
IMAGE_NAME="q-build-manager-img"
SCRIPT_NAME="q-build-manager.py"

echo "=== Q-Build-Manager Launcher ==="
echo "1. Start Manager (UI)"
echo "2. Build/Update Docker Environment"
echo "3. Force Rebuild (No Cache)"
read -p "Select: " choice

case $choice in
    2) docker build -t $IMAGE_NAME . ;;
    3) docker build --no-cache -t $IMAGE_NAME . ;;
esac

mkdir -p $(pwd)/work

docker run -it --rm \
    -v $(pwd)/work:/work \
    -v $(pwd)/$SCRIPT_NAME:/work/$SCRIPT_NAME \
    -e HOST_UID=$(id -u) \
    -e HOST_GID=$(id -g) \
    $IMAGE_NAME
