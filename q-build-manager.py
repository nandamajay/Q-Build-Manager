
import os
import sys
import subprocess
import glob
import yaml
import shutil

WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")

def run_cmd(cmd, cwd=None):
    subprocess.run(cmd, shell=True, check=True, cwd=cwd, executable='/bin/bash')

def menu(title, options):
    args = ["whiptail", "--title", title, "--menu", "Select Option", "20", "70", "10"]
    for k, v in options.items():
        args.extend([k, v])
    result = subprocess.run(args, stderr=subprocess.PIPE, text=True)
    return result.stderr.strip()

def main():
    while True:
        choice = menu("Q-Build Manager (CLI)", {
            "1": "Create New Project",
            "2": "Build Existing Project",
            "3": "Exit"
        })
        
        if not choice or choice == "3":
            sys.exit(0)
            
        if choice == "1":
            name = subprocess.check_output(["whiptail", "--inputbox", "Project Name:", "10", "60"], stderr=subprocess.STDOUT).decode().strip()
            path = os.path.join(WORK_DIR, "meta-qcom-builds", name)
            os.makedirs(path, exist_ok=True)
            
            print("Cloning meta-qcom...")
            subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", os.path.join(path, "meta-qcom")])
            
            # Board Scan
            boards = glob.glob(os.path.join(path, "meta-qcom/ci/*.yml"))
            board_map = {str(i): os.path.basename(b).replace(".yml", "") for i, b in enumerate(boards)}
            b_choice = menu("Select Board", board_map)
            board = board_map[b_choice]
            
            # Config
            board_file = f"meta-qcom/ci/{board}.yml"
            distro_file = "meta-qcom/ci/qcom-distro.yml" # Defaulting to standard
            kas_string = f"{board_file}:{distro_file}"
            
            cfg = {"board": board, "kas_files": kas_string, "image": "qcom-multimedia-image"}
            with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
            
            # Register
            if os.path.exists(REGISTRY_FILE):
                with open(REGISTRY_FILE) as f: reg = yaml.safe_load(f) or {}
            else: reg = {}
            reg[name] = path
            with open(REGISTRY_FILE, "w") as f: yaml.dump(reg, f)
            
        elif choice == "2":
            if not os.path.exists(REGISTRY_FILE): continue
            with open(REGISTRY_FILE) as f: reg = yaml.safe_load(f)
            
            p_map = {str(i): k for i, k in enumerate(reg.keys())}
            p_choice = menu("Select Project", p_map)
            name = p_map[p_choice]
            path = reg[name]
            
            with open(os.path.join(path, "config.yaml")) as f: cfg = yaml.safe_load(f)
            
            cmd = f"kas shell {cfg['kas_files']} -c 'bitbake {cfg['image']}'"
            os.system(f"cd {path} && {cmd}")

if __name__ == "__main__":
    main()
