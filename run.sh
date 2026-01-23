#!/bin/bash

# --- CONFIGURATION ---
IMAGE_NAME="q-build-manager-hybrid"
TUI_SCRIPT="q-build-manager.py"
WEB_SCRIPT="web_manager.py"
KEY_FILE=".qgenie_key"
# ---------------------

# 1. API KEY HANDLING (Secure & Persistent)
if [ -f "$KEY_FILE" ]; then
    source $KEY_FILE
else
    echo "----------------------------------------------------"
    echo " FIRST RUN SETUP: QGenie API Key"
    echo "----------------------------------------------------"
    echo " To enable AI features, please paste your API Key below."
    echo " (Get it from: https://qgenie-chat.qualcomm.com/settings)"
    echo " Leave empty to run in 'Simulation Mode' (No AI)."
    read -p " API Key: " INPUT_KEY
    
    if [ ! -z "$INPUT_KEY" ]; then
        echo "QGENIE_API_KEY=\"$INPUT_KEY\"" > $KEY_FILE
        chmod 600 $KEY_FILE
        export QGENIE_API_KEY="$INPUT_KEY"
        echo ">> Key saved to $KEY_FILE"
    else
        echo ">> No key provided. AI will be disabled."
    fi
fi

# Ensure work directory exists
mkdir -p $(pwd)/work

# 2. AUTO-BUILD CHECK
if [[ "$(docker images -q $IMAGE_NAME 2> /dev/null)" == "" ]]; then
    echo ">> Image '$IMAGE_NAME' not found. Building now..."
    docker build -t $IMAGE_NAME .
fi

# 3. MAIN MENU (Restored)
while true; do
    echo "=========================================="
    echo "   Q-Build Manager (AI Enabled)"
    echo "=========================================="
    echo "1. CLI / TUI Mode (Blue Screen Console)"
    echo "2. Web Mode (Browser Dashboard)"
    echo "3. Rebuild Docker Image"
    echo "4. Exit"
    echo "------------------------------------------"
    read -p "Select Option [1-4]: " choice

    case $choice in
        1)
            echo ">> Starting CLI Mode..."
            docker run -it --rm \
                -v $(pwd)/work:/work \
                -v $(pwd)/$TUI_SCRIPT:/work/$TUI_SCRIPT \
                -e HOST_UID=$(id -u) \
                -e HOST_GID=$(id -g) \
                -e QGENIE_API_KEY="$QGENIE_API_KEY" \
                $IMAGE_NAME python3 /work/$TUI_SCRIPT
            ;;
        2)
            read -p "Enter Port to use [default 5000]: " WEB_PORT
            WEB_PORT=${WEB_PORT:-5000}

            echo "-----------------------------------------------------"
            echo ">> Starting Web Server..."
            echo ">> Open Browser: http://localhost:$WEB_PORT"
            echo "-----------------------------------------------------"

            docker run -it --rm \
                -p $WEB_PORT:$WEB_PORT \
                -v $(pwd)/work:/work \
                -v $(pwd)/$WEB_SCRIPT:/work/$WEB_SCRIPT \
		-v $(pwd)/editor_manager.py:/work/editor_manager.py \
                -e HOST_UID=$(id -u) \
                -e HOST_GID=$(id -g) \
                -e WEB_PORT=$WEB_PORT \
                -e QGENIE_API_KEY="$QGENIE_API_KEY" \
                $IMAGE_NAME python3 /work/$WEB_SCRIPT
            ;;
        3)
            echo ">> Rebuilding Docker Image..."
            docker build -t $IMAGE_NAME .
            echo ">> Build Complete."
            read -p "Press Enter to continue..."
            ;;
        4)
            echo "Exiting..."
            exit 0
            ;;
        *)
            echo "Invalid option."
            ;;
    esac
done
