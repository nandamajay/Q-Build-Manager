# Q-Build-Manager

**A Dockerized, Hybrid (CLI + Web) Workspace Manager for Qualcomm Yocto Builds.**

Q-Build-Manager abstracts the complexity of `kas`, `bitbake`, and Yocto layer management into a clean, modern interface. It is designed to turn a standard Linux machine into a powerful **Kernel Development Workstation**.

![Status](https://img.shields.io/badge/Status-Active-success)
![Docker](https://img.shields.io/badge/Docker-v20.10+-blue)
![Yocto](https://img.shields.io/badge/Yocto-Scarthgap%20Compatible-orange)

---

## 🚀 Key Features

### 1. 🖥️ Centralized Dashboard
- Manage multiple build projects (workspaces) from a single view.
- Real-time status indicators (Building, Idle, Failed).
- Disk usage monitoring and automated cleanup.

### 2. ⚡ Dynamic Configuration
- **Topology Switching:** Toggle between **ASOC** (Standard) and **AudioReach** (spf) topologies with one click.
- **Board Support:** Auto-scans `meta-qcom/ci` to discover available board configurations.

### 3. 🧭 Smart Code Explorer
- **Web-Based IDE:** Browse source code (`.c`, `.dts`, `.bb`) directly in the browser.
- **Click-to-Definition:** Click on any struct, function, or macro to instantly jump to its definition (indexes `workspace/sources`, `meta-qcom`, and `kernel-source`).
- **Devtool Integration:** Automatically prioritizes modified sources in `workspace/sources` over read-only layers.

### 4. 🛠️ Kernel Dev Kit (Beta)
- **Devtool GUI:** Run `devtool modify` and `devtool reset` via button clicks.
- **Recipe Scanner:** Auto-discovers available build targets (recipes) for the current board.

---

## 📦 Installation

### Prerequisites
- Docker Engine
- Linux Host (Ubuntu 20.04/22.04 recommended)

### Quick Start
1. **Clone the repository:**
   ```bash
   git clone https://github.com/nandamajay/Q-Build-Manager.git
   cd Q-Build-Manager

   ./run.sh
