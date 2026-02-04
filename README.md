# Q-Build Manager V30 - Demo Guide

This tool automates Yocto and Upstream Kernel builds with a modern Web UI, Integrated IDE (Pro Editor), and AI assistance.

## 🚀 1. Setup & Launch

1.  **Clone the Repository** (on the build server):
    ```bash
    git clone https://github.com/nandamajay/Q-Build-Manager.git
    cd Q-Build-Manager
    ```

2.  **Start the Tool**:
    ```bash
    ./run.sh
    ```
    *   Select **Web Interface** mode.
    *   Enter a port number (e.g., `1345`).

3.  **Establish Secure Tunnel**:
    *   Ask the user to open a **Command Prompt / Terminal** on their *local laptop*.
    *   Run the following command (replace `1345` with your chosen port):
        ```bash
        ssh -L 1345:localhost:1345 nandam@hu-nandam-hyd .
        ```
        *(This forwards the server's port 1345 to the local machine's port 1345)*

4.  **Open in Browser**:
    *   Navigate to: [http://localhost:1345](http://localhost:1345)

---

## 🔍 2. Demo Walkthrough & Validation

Follow these steps to validate and demonstrate each feature to the team.

### 🖥️ Feature 1: Dashboard & Project Management
*   **Action**: Land on the Home Dashboard.
*   **Validation**:
    *   Verify all Yocto/Kernel projects are listed.
    *   Check that the status (IDLE/BUILDING) is accurate.

### ⚙️ Feature 2: Build Console (Real-time Logs)
*   **Action**: Click the green **Build** button on any project.
*   **Validation**:
    *   Observe the **xterm.js console** opening.
    *   **Check**: Logs should stream smoothly (no lag/stripping).
    *   **Check**: ANSI colors (green success, red failure) should render correctly.
    *   *Note: This replaces the old text-box log viewer.*

### 📝 Feature 3: Pro Editor (IDE)
*   **Action**: Click the purple **Code** button.
*   **Validation**:
    *   **File Tree**: Click "New File" icon -> Create `demo_test.py`.
    *   **Edit**: Type some Python code.
    *   **Save**: Press `Ctrl+S` or click "Save". Verify "Saved" status in the footer.

### 🤖 Feature 4: AI Coding & Diff View (Highlight)
*   **Action**: Inside the Editor, click **AI Gen**.
*   **Validation**:
    *   **Prompt**: Enter *"Write a function to calculate factorial with error handling"*.
    *   **Click**: "Preview Changes".
    *   **Verify**: The **Diff View Modal** opens.
        *   **Left**: Original Code.
        *   **Right**: AI's proposed code.
    *   **Action**: Click **"Apply Changes"** to merge it.

### 🐙 Feature 5: Git Integration (Visual)
*   **Action**: Click the orange **Git** button.
*   **Validation**:
    *   **History**: Click "History" to see the commit graph.
    *   **Patching**: Click "Apply Patch" -> Select a `.patch` file from your local PC.
        *   *Verify*: Output shows "Patch applied successfully".
    *   **Commit**: Type a message and click "Commit".

### 💬 Feature 6: Context-Aware Chat
*   **Action**: Open the **Chat** sidebar.
*   **Validation**:
    *   **Ask**: *"What does this file do?"*
    *   **Verify**: The AI reads the currently open file (Smart Context) and explains it.
    *   **Verify**: Markdown code blocks are formatted with copy buttons.

---

