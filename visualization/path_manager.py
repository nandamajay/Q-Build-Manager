import os
import glob
import sys

class PathManager:
    def __init__(self, project_root, mode=None):
        self.root = os.path.abspath(project_root)
        self.mode = mode

    def debug(self, msg):
        print(f"[PathManager] {msg}", file=sys.stdout)

    def get_dts_base_path(self):
        self.debug(f"Searching in {self.root} (Mode: {self.mode})")

        # STRATEGY 1: Meta-Qcom Smart Search (Handles tmp-glibc, tmp, etc.)
        # Look for build/tmp*/work-shared or tmp*/work-shared
        
        possible_roots = [
            os.path.join(self.root, "build"),
            self.root
        ]

        for start_node in possible_roots:
            if not os.path.exists(start_node): continue
            
            # Glob for any tmp directory (tmp, tmp-glibc, etc.)
            tmp_pattern = os.path.join(start_node, "tmp*")
            tmp_dirs = glob.glob(tmp_pattern)
            
            for tmp in tmp_dirs:
                # Look for work-shared inside tmp
                ws_pattern = os.path.join(tmp, "work-shared", "*", "kernel-source", "arch", "arm64", "boot", "dts", "qcom")
                matches = glob.glob(ws_pattern)
                if matches:
                    self.debug(f"Found Yocto path: {matches[0]}")
                    return matches[0]

        # STRATEGY 2: Upstream / Standard
        candidates = [
            os.path.join(self.root, "arch/arm64/boot/dts/qcom"),
            os.path.join(self.root, "linux", "arch", "arm64", "boot", "dts", "qcom"),
            os.path.join(self.root, "kernel-source", "arch", "arm64", "boot", "dts", "qcom"),
        ]
        for p in candidates:
            if os.path.exists(p):
                self.debug(f"Found Standard path: {p}")
                return p

        # STRATEGY 3: Deep Search (Limited)
        self.debug("Falling back to limited deep walk...")
        for root, dirs, files in os.walk(self.root):
            # Prune massive dirs
            if 'out' in dirs: dirs.remove('out')
            if '.git' in dirs: dirs.remove('.git')
            if 'sstate-cache' in dirs: dirs.remove('sstate-cache')
            
            # Don't go too deep
            if root.count(os.sep) - self.root.count(os.sep) > 4:
                continue

            if "dts" in dirs:
                check = os.path.join(root, "dts", "qcom")
                if os.path.exists(check):
                    self.debug(f"Found deep match: {check}")
                    return check
        
        return None

    def list_dts_files(self):
        base = self.get_dts_base_path()
        if not base: return []
        try:
            return sorted([f for f in os.listdir(base) if f.endswith('.dts') or f.endswith('.dtsi')])
        except:
            return []
