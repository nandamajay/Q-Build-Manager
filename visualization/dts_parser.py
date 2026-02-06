
import re
import os

class DtsNode:
    def __init__(self, name, label=None, parent=None):
        self.name = name
        self.label = label
        self.parent = parent
        self.props = {}
        self.children = []

    def get_path(self):
        return f"{self.parent.get_path()}/{self.name}" if self.parent else self.name

class DtsParser:
    def __init__(self, base_path):
        self.base_path = base_path
        self.root = DtsNode("/")
        self.labels = {}
        self.includes = set()
        self.routing = []
        self.dailinks = []

    def parse(self, filename):
        self._parse_recursive(filename, self.root)
        self._post_process_routing()
        self._post_process_dailinks()
        return self

    def _parse_recursive(self, filename, current_root):
        # FIX: Handle both absolute/relative and angle-bracket paths
        clean_name = filename.strip('<>"')
        
        # 1. Try direct path
        candidates = [
            os.path.join(self.base_path, clean_name),
            os.path.join(self.base_path, "qcom", clean_name), # Common subdir
            clean_name # Absolute or already resolved
        ]
        
        path = None
        for c in candidates:
            if os.path.exists(c) and os.path.isfile(c):
                path = c
                break
        
        if not path or path in self.includes: 
            return

        self.includes.add(path)
        
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
        except: return

        # FIX: Regex matches both "file.dtsi" and <file.dtsi>
        includes = re.findall(r'#include\s+["<]([^">]+)[">]', content)
        for inc in includes: 
            # Recursively parse includes
            self._parse_recursive(inc, current_root)

        # Cleanup comments
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        content = re.sub(r'//.*', '', content)
        
        # Parse Nodes
        tokens = re.split(r'([\{\};])', content)
        stack = [current_root]
        buffer = ""

        for token in tokens:
            if token == '{':
                header = buffer.strip()
                label = None
                name = header
                if ':' in header:
                    parts = header.split(':')
                    label = parts[0].strip(); name = parts[-1].strip()
                
                if name == '/': node = self.root
                elif name.startswith('&'):
                    node = self.labels.get(name[1:], stack[-1]) 
                else:
                    node = DtsNode(name, label, stack[-1]); stack[-1].children.append(node)
                
                if label: self.labels[label] = node
                stack.append(node); buffer = ""
            elif token == '}':
                if len(stack) > 1: stack.pop()
                buffer = ""
            elif token == ';':
                stmt = buffer.strip()
                if stmt and stack:
                    if '=' in stmt: k, v = stmt.split('=', 1); stack[-1].props[k.strip()] = v.strip()
                    else: stack[-1].props[stmt] = True
                buffer = ""
            else: buffer += token

    def _post_process_routing(self):
        snd = self.get_sound_card_node()
        if snd and "audio-routing" in snd.props:
            raw = snd.props["audio-routing"]
            parts = re.findall(r'"([^"]+)"', raw)
            for i in range(0, len(parts)-1, 2): self.routing.append((parts[i], parts[i+1]))

    def _post_process_dailinks(self):
        snd = self.get_sound_card_node()
        if snd:
            for child in snd.children:
                if 'dai-link' in child.name or 'link-name' in child.props:
                    self.dailinks.append({
                        "name": child.props.get("link-name", child.name).replace('"', ''),
                        "cpu": self._extract_phandle(child, "cpu"),
                        "codec": self._extract_phandle(child, "codec"),
                        "platform": self._extract_phandle(child, "platform")
                    })

    def _extract_phandle(self, node, subnode_name):
        sub = next((c for c in node.children if c.name == subnode_name), None)
        if not sub: return []
        val = sub.props.get("sound-dai", "")
        return re.findall(r'&([\w_]+)', val)

    def get_sound_card_node(self):
        queue = [self.root]
        # Broad Search for Sound Card
        candidates = []
        while queue:
            n = queue.pop(0)
            # Check 1: Explicit compatible string
            if "compatible" in n.props:
                if "sndcard" in n.props["compatible"] or "audio-card" in n.props["compatible"]:
                    return n
            
            # Check 2: Check property existence
            if "audio-routing" in n.props:
                return n
                
            # Check 3: Name match (weakest)
            if "sound" in n.name and "pinctrl" not in n.name:
                candidates.append(n)
                
            queue.extend(n.children)
        
        return candidates[0] if candidates else None

    def get_hardware_nodes(self):
        hw = []
        snd = self.get_sound_card_node()
        
        # Always add Sound Card if found
        if snd: 
            hw.append({"id": snd.label or f"snd_{id(snd)}", "label": "Sound Card", "type": "sndcard", "full_name": snd.name})
        else:
            # Fallback: Scan everything for known audio components even if sound card missing
            pass

        queue = [self.root]
        while queue:
            n = queue.pop(0)
            comp = n.props.get("compatible", "")
            t = "component"
            
            # Detect type by compatible string
            if "qcom,wcd" in comp or "wcd9" in n.name: t="codec"
            elif "qcom,wsa" in comp or "wsa8" in n.name: t="amp"
            elif "qcom,lpass" in comp or "lpass" in n.name: t="soc"
            
            if t != "component":
                clean = (n.label or n.name).split('@')[0].upper()
                hw.append({"id": n.label or f"n_{id(n)}", "label": clean, "type": t, "full_name": n.name})
            queue.extend(n.children)
        return hw

    def get_hardware_connections(self):
        conns = []
        snd = self.get_sound_card_node()
        if not snd: return []
        snd_id = snd.label or f"snd_{id(snd)}"
        for link in self.dailinks:
            for c in link['cpu']: conns.append((snd_id, c, "CPU: " + link['name']))
            for c in link['codec']: conns.append((snd_id, c, "Codec: " + link['name']))
            for cpu in link['cpu']:
                for codec in link['codec']: conns.append((cpu, codec, "DAI"))
        return conns
