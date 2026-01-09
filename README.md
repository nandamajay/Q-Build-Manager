# Q-Build-Manager

**Q-Build-Manager** is an automated workspace manager for Qualcomm Yocto (KAS) projects.

It is designed to simplify the onboarding process for new members by abstracting the complex command-line arguments and directory structures required for building `meta-qcom` images.

## Features

- **Zero Setup:** Just requires Docker. No host dependencies.
- **TUI (Text UI):** Simple blue-screen menus to guide you.
- **Project Isolation:** Automatically organizes builds into `meta-qcom-builds/` and other categories.
- **Auto-Config:** Fetches the latest boards from `meta-qcom` dynamically.
- **Topology Support:** Easily switch between ASOC and AudioReach topologies.

## Quick Start

1. **Clone the Repo:**
   ```bash
   git clone <repo_url> q-build-manager
   cd q-build-manager
   ```

2. **Run the Tool:**
   ```bash
   ./run.sh
   ```
   *Select **Option 2** (Build Image) on the very first run.*

3. **Create a Project:**
   Select **"1. Create New Project"** in the menu and follow the prompts.

## Directory Layout
- `q-build-manager.py`: The core automation logic.
- `work/`: The hidden workspace where all source code and builds are stored (Git Ignored).
