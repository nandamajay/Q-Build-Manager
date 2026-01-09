import os
import sys
import glob
import shutil
import subprocess
import yaml

WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")

CATEGORY_MAP = {
    "1": {"name": "meta-qcom", "dir": "meta-qcom-builds"},
    "2": {"name": "upstream", "dir": "upstream-builds"},
    "3": {"name": "qclinux", "dir": "qclinux-builds"}
}

# --- TUI HELPER FUNCTIONS ---
def tui_msgbox(title, text):
    subprocess.run(["whiptail", "--title", title, "--msgbox", text, "10", "60"])

def tui_yesno(title, text):
    res = subprocess.run(["whiptail", "--title", title, "--yesno", text, "10", "60"], stderr=subprocess.PIPE)
    return res.returncode == 0

def tui_input(title, text):
    res = subprocess.run(["whiptail", "--title", title, "--inputbox", text, "10", "60"], stderr=subprocess.PIPE)
    return res.stderr.decode().strip()

def tui_menu(title, text, items):
    cmd = ["whiptail", "--title", title, "--menu", text, "20", "70", "10"] + items
    res = subprocess.run(cmd, stderr=subprocess.PIPE)
    return res.stderr.decode().strip()

def tui_radiolist(title, text, items):
    cmd = ["whiptail", "--title", title, "--radiolist", text, "20", "70", "10"] + items
    res = subprocess.run(cmd, stderr=subprocess.PIPE)
    return res.stderr.decode().strip()

# --- LOGIC ---

def load_registry():
    if not os.path.exists(REGISTRY_FILE): return {}
    with open(REGISTRY_FILE, "r") as f: return yaml.safe_load(f) or {}

def save_registry(data):
    with open(REGISTRY_FILE, "w") as f: yaml.dump(data, f)

def sync_registry():
    reg = load_registry()
    cleaned = {n: p for n, p in reg.items() if os.path.exists(p)}
    if len(cleaned) != len(reg): save_registry(cleaned)
    return cleaned

def setup_workspace():
    # 1. Select Category
    cat_items = [
        "1", "Meta-Qcom (Standard)",
        "2", "Upstream (Future)",
        "3", "QCLinux (Future)"
    ]
    sel = tui_menu("New Project", "Select Project Type:", cat_items)
    
    if not sel or sel != "1":
        if sel: tui_msgbox("Info", "This category is not yet implemented.")
        return

    category = CATEGORY_MAP[sel]

    # 2. Project Name
    base_build_dir = os.path.join(WORK_DIR, category["dir"])
    os.makedirs(base_build_dir, exist_ok=True)
    
    project_name = tui_input("Project Name", "Enter a name (e.g., 'rb3-audio-test'):")
    if not project_name: return

    project_path = os.path.join(base_build_dir, project_name)
    if os.path.exists(project_path):
        tui_msgbox("Error", f"Project already exists:\n{project_path}")
        return

    # 3. Clone
    if not tui_yesno("Confirm Setup", f"Create new project in:\n{project_path}?"): return
    
    os.system('clear')
    print(f"Initializing {project_name}...")
    src_dir = os.path.join(project_path, "meta-qcom")
    os.makedirs(project_path, exist_ok=True)
    try:
        subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", src_dir], check=True)
    except:
        tui_msgbox("Error", "Git clone failed. Check internet.")
        return

    # 4. Scan Boards
    config_files = glob.glob(os.path.join(src_dir, "ci/*.yml"))
    if not config_files:
        tui_msgbox("Error", "No boards found in meta-qcom/ci/")
        return

    boards = sorted([os.path.basename(f).replace(".yml", "") for f in config_files])
    
    radio_items = []
    for i, b in enumerate(boards):
        status = "ON" if i == 0 else "OFF"
        radio_items.extend([b, "", status])

    selected_board = tui_radiolist("Select Board", "Target Board:", radio_items)
    if not selected_board: return

    # 5. Topology
    topo_items = [
        "1", "ASOC (Default Multimedia)",
        "2", "AR (AudioReach Proprietary)"
    ]
    topo_sel = tui_menu("Topology", f"Select Audio Topology:", topo_items)
    if not topo_sel: return

    board_file = f"meta-qcom/ci/{selected_board}.yml"
    
    if topo_sel == '2':
        topo_name = "AR"
        distro_file = "meta-qcom/ci/qcom-distro-prop-image.yml"
        target_image = "qcom-multimedia-proprietary-image"
    else:
        topo_name = "ASOC"
        distro_file = "meta-qcom/ci/qcom-distro.yml"
        target_image = "qcom-multimedia-image"

    full_distro_path = os.path.join(project_path, distro_file)
    if os.path.exists(full_distro_path):
        kas_string = f"{board_file}:{distro_file}"
    else:
        tui_msgbox("Warning", f"{distro_file} missing.\nUsing board config only.")
        kas_string = board_file

    # 6. Save Config
    reg = load_registry()
    reg[project_name] = project_path
    save_registry(reg)

    config_data = {
        "board": selected_board, 
        "kas_files": kas_string,
        "image": target_image,
        "category": category["name"],
        "topology": topo_name
    }
    
    with open(os.path.join(project_path, "config.yaml"), "w") as f:
        yaml.dump(config_data, f)

    if tui_yesno("Success", "Project Created. Build now?"):
        run_build_logic(project_name, project_path)

def run_build():
    reg = sync_registry()
    if not reg:
        tui_msgbox("Info", "No projects found. Create one first.")
        return
    
    menu_items = []
    for name, path in reg.items():
        # Display name and path relative to work dir for clarity
        rel_path = path.replace("/work/", "")
        menu_items.extend([name, rel_path])

    sel = tui_menu("Project Selection", "Select a project to build:", menu_items)
    if sel:
        run_build_logic(sel, reg[sel])

def run_build_logic(name, path):
    cfg_path = os.path.join(path, "config.yaml")
    if not os.path.exists(cfg_path):
        tui_msgbox("Error", "Config file corrupted/missing.")
        return
    
    with open(cfg_path) as f: cfg = yaml.safe_load(f)
    
    target_image = cfg.get("image", "qcom-multimedia-image")
    kas_files = cfg.get("kas_files", "")
    
    os.system('clear')
    print(f"--- Q-Build-Manager: {name} ---")
    print(f"Board:  {cfg.get('board')}")
    print(f"Target: {target_image}")
    print("--------------------------------")
    
    kas_cmd = f"bash -c 'set -o pipefail; bitbake {target_image} | tee build.log'"
    cmd = f"kas shell {kas_files} -c \"{kas_cmd}\""
    
    try:
        subprocess.run(cmd, shell=True, check=True, cwd=path, executable="/bin/bash")
        input("\n[SUCCESS] Build Finished. Press Enter...")
    except:
        input("\n[FAILURE] Build Failed. Press Enter to view log...")

def main_menu():
    while True:
        items = [
            "1", "Create New Project (Start Here)",
            "2", "Build Existing Project",
            "3", "Sync Registry (Maintenance)",
            "4", "Exit"
        ]
        choice = tui_menu("Q-Build-Manager", "Qualcomm Yocto Build Assistant", items)
        
        if choice == '1': setup_workspace()
        elif choice == '2': run_build()
        elif choice == '3': 
            sync_registry()
            tui_msgbox("Done", "Registry Cleaned.")
        else:
            sys.exit(0)

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        sys.exit(0)
