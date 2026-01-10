#!/bin/bash
IMAGE_NAME="q-build-manager-hybrid"
TUI_SCRIPT="q-build-manager.py"
WEB_SCRIPT="web_manager.py"

echo "=== Q-Build Manager (Hybrid) ==="
echo "1. CLI / TUI Mode (Blue Screen)"
echo "2. Web Mode (Browser Dashboard)"
echo "3. Rebuild Docker Image"
read -p "Select Option: " choice

mkdir -p $(pwd)/work

case $choice in
    1)
        echo "Starting CLI Mode..."
        docker run -it --rm \
            -v $(pwd)/work:/work \
            -v $(pwd)/$TUI_SCRIPT:/work/$TUI_SCRIPT \
            -e HOST_UID=$(id -u) \
            -e HOST_GID=$(id -g) \
            $IMAGE_NAME python3 /work/$TUI_SCRIPT
        ;;
    2)
        read -p "Enter Port to use (default 5000): " WEB_PORT
        WEB_PORT=${WEB_PORT:-5000}
        
        echo "-----------------------------------------------------"
        echo "Starting Web Server on Port $WEB_PORT"
        echo "On your laptop, run: ssh -L $WEB_PORT:localhost:$WEB_PORT nandam@hu-nandam-hyd"
        echo "Then open: http://localhost:$WEB_PORT"
        echo "-----------------------------------------------------"
        
        docker run -it --rm \
            -p $WEB_PORT:$WEB_PORT \
            -v $(pwd)/work:/work \
            -v $(pwd)/$WEB_SCRIPT:/work/$WEB_SCRIPT \
            -e HOST_UID=$(id -u) \
            -e HOST_GID=$(id -g) \
            -e WEB_PORT=$WEB_PORT \
            $IMAGE_NAME python3 /work/$WEB_SCRIPT
        ;;
    3)
        echo "Rebuilding Image..."
        docker build -t $IMAGE_NAME .
        ;;
    *)
        echo "Invalid option."
        ;;
esac
