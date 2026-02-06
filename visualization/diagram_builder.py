
import re
import hashlib

class DiagramBuilder:
    def __init__(self, parser):
        print("[V26] High-Level Block Engine Loaded")
        self.parser = parser

    def _get_safe_id(self, raw_id):
        # ADVANCED MECHANISM: Generate a unique MD5 hash for every node.
        # This guarantees that weird characters in DTS never break the diagram.
        if not raw_id: return "node_unknown"
        hash_object = hashlib.md5(raw_id.encode())
        return "node_" + hash_object.hexdigest()[:8]

    def _is_high_level_node(self, node):
        # FILTER: Only show relevant hardware blocks
        # 1. Reject Includes/Headers
        label = node.get('label', '').lower()
        nid = node.get('id', '').lower()
        
        if "#include" in label or "dt-bindings" in label:
            return False
        if "pinctrl" in nid or "gpio" in nid:
            return False
            
        # 2. Accept known hardware types
        # (If type is unknown, we include it only if it has connections)
        return True

    def sanitize_label(self, text):
        if not text: return "Block"
        # Strip complex code characters
        clean = text.split('"')[0] # Take first part if quoted
        clean = clean.split('<')[0] # Strip generic brackets
        
        # Clean specific noise
        clean = clean.replace('_', ' ').title()
        
        # Truncate
        if len(clean) > 25:
            clean = clean[:22] + "..."
            
        return clean

    def build_all(self):
        return {
            "hardware": self.build_hardware_diagram(),
            "dailinks": self.build_dailinks_diagram(),
            "routing": self.build_routing_diagram()
        }

    def build_hardware_diagram(self):
        lines = ["graph TD"]
        
        # Professional Styling
        lines.append("classDef snd fill:#ff9900,stroke:#333,stroke-width:2px,color:white,rx:5,ry:5")
        lines.append("classDef soc fill:#2962ff,stroke:#333,stroke-width:0px,color:white,rx:2,ry:2")
        lines.append("classDef codec fill:#00c853,stroke:#333,stroke-width:0px,color:white,rx:5,ry:5")
        lines.append("classDef gen fill:#607d8b,stroke:#333,color:white")

        nodes = self.parser.get_hardware_nodes()
        conns = self.parser.get_hardware_connections()
        
        # Track valid IDs to avoid ghost connections
        valid_ids = set()

        # 1. Build Nodes
        for n in nodes:
            if not self._is_high_level_node(n):
                continue

            raw_id = n['id']
            safe_id = self._get_safe_id(raw_id)
            valid_ids.add(raw_id)
            
            label = self.sanitize_label(n['label'])
            
            # Assign Class based on Type
            ntype = n.get('type', 'generic')
            css_class = "gen"
            shape_open, shape_close = "[", "]"

            if ntype == 'sndcard':
                css_class = "snd"
                shape_open, shape_close = "([", "])"
            elif ntype == 'soc':
                css_class = "soc"
                shape_open, shape_close = "[", "]"
            elif ntype == 'codec':
                css_class = "codec"
                shape_open, shape_close = "([", "])"

            # Syntax: id["Label"]:::class
            lines.append(f'{safe_id}{shape_open}"{label}"{shape_close}:::{css_class}')
            
            # Simple Click Callback (No complex JSON passing to avoid syntax errors)
            lines.append(f'click {safe_id} callNodeCallback "{raw_id}"')

        # 2. Build Connections
        for src, dst, lbl in conns:
            if src in valid_ids and dst in valid_ids:
                s_id = self._get_safe_id(src)
                d_id = self._get_safe_id(dst)
                
                # Clean Label
                l_clean = self.sanitize_label(lbl)
                if not l_clean: l_clean = "link"
                
                # Robust Syntax: A -- "Label" --> B
                if s_id != d_id:
                    lines.append(f'{s_id} -- "{l_clean}" --> {d_id}')

        return "\n".join(lines)

    def build_dailinks_diagram(self):
        lines = ["graph LR"]
        lines.append("classDef cpu fill:#2962ff,color:white")
        lines.append("classDef codec fill:#00c853,color:white")
        
        for link in self.parser.dailinks:
            name = self.sanitize_label(link['name'])
            
            cpus = [c for c in link['cpu']]
            codecs = [c for c in link['codec']]
            
            for c in cpus:
                sid = self._get_safe_id(c)
                lines.append(f'{sid}["{c}"]:::cpu')
            
            for c in codecs:
                sid = self._get_safe_id(c)
                lines.append(f'{sid}["{c}"]:::codec')
                
            for cpu in cpus:
                for codec in codecs:
                    s = self._get_safe_id(cpu)
                    d = self._get_safe_id(codec)
                    lines.append(f'{s} -- "{name}" --> {d}')
                    
        return "\n".join(lines)

    def build_routing_diagram(self):
        lines = ["graph LR"]
        for src, dst in self.parser.routing:
            s = self._get_safe_id(src)
            d = self._get_safe_id(dst)
            lines.append(f'{s} --> {d}')
        return "\n".join(lines)

    def build_graph_json(self):
        """Return a renderer-agnostic graph model for Cytoscape.
        {
          "nodes": [{"id": str, "label": str, "type": str, "full_name": str}],
          "edges": [{"source": str, "target": str, "kind": str, "label": str}]
        }
        """
        def add_node(node_map, raw_id, label=None, ntype='component', full_name=None):
            if not raw_id:
                return None
            sid = self._get_safe_id(raw_id) if hasattr(self, '_get_safe_id') else raw_id
            if sid not in node_map:
                lab = label if label is not None else (raw_id or '')
                if hasattr(self, 'sanitize_label'):
                    lab = self.sanitize_label(lab)
                node_map[sid] = {
                    'id': sid,
                    'label': (lab or '').replace('\"',''),
                    'type': ntype or 'component',
                    'full_name': full_name or raw_id
                }
            return sid

        nodes_src = getattr(self.parser, 'get_hardware_nodes', lambda: [])() or []
        node_map = {}
        edges = []

        # 1) Hardware nodes
        for n in nodes_src:
            raw_id = n.get('id','')
            ntype = n.get('type','component')
            label = n.get('label','')
            full_name = n.get('full_name','')
            add_node(node_map, raw_id, label, ntype, full_name)

        # Helper to append edge and ensure endpoints exist
        def add_edge(kind, src, dst, label=''):
            s = add_node(node_map, src)
            d = add_node(node_map, dst)
            if not s or not d:
                return
            edges.append({'source': s, 'target': d, 'kind': kind, 'label': (label or '').replace('\"','')})

        # 2) Hardware connections
        try:
            hw_conns = getattr(self.parser, 'get_hardware_connections', lambda: [])() or []
        except Exception:
            hw_conns = []
        for src, dst, lbl in hw_conns:
            add_edge('hardware', src, dst, lbl)

        # 3) DAI link nodes and edges
        for link in getattr(self.parser, 'dailinks', []) or []:
            name = (link.get('name') or '').replace('\"','')
            for cpu in link.get('cpu', []) or []:
                add_node(node_map, cpu, cpu, 'cpu', cpu)
            for codec in link.get('codec', []) or []:
                add_node(node_map, codec, codec, 'codec', codec)
            for cpu in link.get('cpu', []) or []:
                for codec in link.get('codec', []) or []:
                    add_edge('dai', cpu, codec, name)

        # 4) Routing edges (endpoints might be arbitrary strings)
        for src, dst in getattr(self.parser, 'routing', []) or []:
            add_edge('routing', (src or ''), (dst or ''), '')

        return {'nodes': list(node_map.values()), 'edges': edges}
