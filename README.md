# Q-Build-Manager

**Q-Build-Manager** is an automated, Dockerized workspace manager for Qualcomm Yocto (KAS) projects. It abstracts complex build setups into a simple User Interface, allowing developers to configure, build, and manage `meta-qcom` projects without dealing with dependency hell.

## 🚀 Features

*   **Hybrid Interface**: Choose between a robust **CLI/TUI** or a modern **Web Dashboard**.
*   **Zero-Setup Environment**: Runs inside a Docker container with all Yocto/BitBake dependencies pre-installed (Ubuntu 22.04, Python 3, KAS).
*   **Web Dashboard**:
    *   Create projects and scan for supported boards automatically.
    *   **Live Build Streaming**: Watch BitBake logs in real-time via xterm.js in your browser.
    *   **Progress Tracking**: Visual progress bar for build completion.
    *   **Topology Switching**: Toggle between `ASOC` (Multimedia) and `AudioReach` (Proprietary) instantly.
*   **Dynamic Port Selection**: prevents conflicts on shared build servers.
*   **Safe Artifacts**: Builds are stored in a persistent `/work` directory, keeping your host machine clean.

## 🛠️ Prerequisites

*   **Docker** installed on the host machine.
*   **Git** configured.

## 🏃 Quick Start

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/nandamajay/Q-Build-Manager.git
    cd Q-Build-Manager
    ```

2.  **Run the Manager**
    ```bash
    ./run.sh
    ```

3.  **Choose Mode**
    *   **Option 1 (CLI)**: Runs the text-based menu in your terminal.
    *   **Option 2 (Web)**: Starts the Web Server. You will be asked for a port (e.g., `8080`, `5000`, `9000`).

4.  **Access Web UI**
    If running on a remote server, setup an SSH tunnel:
    ```bash
    ssh -L <PORT>:localhost:<PORT> user@remote-server
    ```
    Then open `http://localhost:<PORT>` in your browser.

## 📂 Directory Structure

*   `run.sh`: Main entry point. Handles Docker mounting and port configuration.
*   `web_manager.py`: Flask + SocketIO application for the Web UI.
*   `q-build-manager.py`: Legacy CLI/TUI application.
*   `Dockerfile`: Defines the build environment (Ubuntu 22.04 + Yocto tools).
*   `work/`: (Auto-created) Stores all your source code and build artifacts.

## 🔧 Advanced

*   **Caching**: To enable shared sstate-cache, ensure `/local/mnt/workspace/sstate-cache` is mounted in `run.sh`.
*   **Topology**: You can switch between Open Source (ASOC) and Proprietary (AR) workflows dynamically in the Build Settings panel.

