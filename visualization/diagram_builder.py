import re
import hashlib

class DiagramBuilder:
    def __init__(self, parser):
        print("[V26] High-Level Block Engine Loaded")
        self.parser = parser

    def _get_safe_id(self, raw_id):
        if not raw_id:
            return "node_unknown"
        hash_object = hashlib.md5(raw_id.encode())
        return "node_" + hash_object.hexdigest()[:8]

    def _is_high_level_node(self, node):
        label = node.get('label', '').lower()
        nid = node.get('id', '').lower()
        if "#include" in label or "dt-bindings" in label:
            return False
        if "pinctrl" in nid or "gpio" in nid:
            return False
        return True

    def sanitize_label(self, text):
        if not text:
            return "Block"
        clean = text.split('"')[0]
        clean = clean.split('<')[0]
        clean = clean.replace('_', ' ').title()
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
        lines.append("classDef snd fill:#ff9900,stroke:#333,stroke-width:2px,color:white,rx:5,ry:5")
        lines.append("classDef soc fill:#2962ff,stroke:#333,stroke-width:0px,color:white,rx:2,ry:2")
        lines.append("classDef codec fill:#00c853,stroke:#333,stroke-width:0px,color:white,rx:5,ry:5")
        lines.append("classDef gen fill:#607d8b,stroke:#333,color:white")
        nodes = self.parser.get_hardware_nodes()
        conns = self.parser.get_hardware_connections()
        valid_ids = set()
        for n in nodes:
            if not self._is_high_level_node(n):
                continue
            raw_id = n['id']
            safe_id = self._get_safe_id(raw_id)
            valid_ids.add(raw_id)
            label = self.sanitize_label(n['label'])
            ntype = n.get('type', 'generic')
            css_class = "gen"
            shape_open, shape_close = "[", "]"
            if ntype == 'sndcard':
                css_class = "snd"; shape_open, shape_close = "([", "])"
            elif ntype == 'soc':
                css_class = "soc"; shape_open, shape_close = "[", "]"
            elif ntype == 'codec':
                css_class = "codec"; shape_open, shape_close = "([", "])"
            lines.append(f'{safe_id}{shape_open}"{label}"{shape_close}:::{css_class}')
            lines.append(f'click {safe_id} callNodeCallback "{raw_id}"')
        for src, dst, lbl in conns:
            if src in valid_ids and dst in valid_ids:
                s_id = self._get_safe_id(src); d_id = self._get_safe_id(dst)
                l_clean = self.sanitize_label(lbl) or "link"
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
                sid = self._get_safe_id(c); lines.append(f'{sid}["{c}"]:::cpu')
            for c in codecs:
                sid = self._get_safe_id(c); lines.append(f'{sid}["{c}"]:::codec')
            for cpu in cpus:
                for codec in codecs:
                    s = self._get_safe_id(cpu); d = self._get_safe_id(codec)
                    lines.append(f'{s} -- "{name}" --> {d}')
        return "\n".join(lines)

    def build_routing_diagram(self):
        lines = ["graph LR"]
        for src, dst in self.parser.routing:
            s = self._get_safe_id(src); d = self._get_safe_id(dst)
            lines.append(f'{s} --> {d}')
        return "\n".join(lines)

    def build_graph_json(self):
        """
        Return a renderer-agnostic graph model for Cytoscape with lane groups.
        nodes: id,label,type,full_name,parent
        edges: source,target,kind,label
        """
        def add_node(node_map, raw_id, label=None, ntype='component', full_name=None, parent=None):
            if not raw_id: return None
            sid = self._get_safe_id(raw_id) if hasattr(self, '_get_safe_id') else raw_id
            if sid not in node_map:
                lab = label if label is not None else (raw_id or '')
                if hasattr(self, 'sanitize_label'): lab = self.sanitize_label(lab)
                node_map[sid] = {'id': sid, 'label': (lab or '').replace('"',''),
                                 'type': ntype or 'component', 'full_name': full_name or raw_id}
            if parent: node_map[sid]['parent'] = parent
            return sid
        def classify_parent(ntype, label, full_name):
            lbl = (label or '').lower(); fn = (full_name or '').lower()
            if any(x in lbl for x in ['spkr','speaker']) or any(x in fn for x in ['spkr','speaker']): return 'grp_speakers'
            if ntype in ('codec','amp') or any(x in lbl for x in ['wcd','wsa','codec','amp']) or any(x in fn for x in ['wcd','wsa','codec','amp']): return 'grp_peripherals'
            if any(x in lbl for x in ['soundwire','pcm','tdm','dmic']) or any(x in fn for x in ['soundwire','pcm','tdm','dmic']): return 'grp_buses'
            if ntype == 'soc' or any(x in lbl for x in ['lpass','spf','lpaif','qdsp','q6']): return 'grp_lpass'
            if ntype == 'sndcard': return 'grp_host'
            return 'grp_lpass'
        def ensure_parent(node_map, sid):
            if not sid: return
            n = node_map.get(sid, {})
            if 'parent' not in n:
                n['parent'] = classify_parent(n.get('type'), n.get('label'), n.get('full_name'))
                node_map[sid] = n
        def add_edge(kind, src, dst, label=''):
            s = add_node(node_map, src); d = add_node(node_map, dst)
            if not s or not d: return
            ensure_parent(node_map, s); ensure_parent(node_map, d)
            edges.append({'source': s, 'target': d, 'kind': kind, 'label': (label or '').replace('"','')})
        # lanes
        node_map, edges = {}, []
        for gid, glabel in [('grp_host','Host / APSS'), ('grp_lpass','LPASS'), ('grp_buses','Audio Buses'), ('grp_peripherals','Peripherals'), ('grp_speakers','Speakers')]:
            node_map[gid] = {'id': gid, 'label': glabel, 'type': 'group', 'full_name': glabel}
        # anchors
        add_node(node_map, 'host.app',  'App Framework',       'component', 'App Framework (AudioFlinger/ALSA)', 'grp_host')
        add_node(node_map, 'host.kdrv', 'Kernel ALSA Drivers', 'component', 'Kernel ALSA Drivers',               'grp_host')
        add_node(node_map, 'host.ddr',  'DMA Buffers (DDR)',   'component', 'DMA Buffers (DDR)',                 'grp_host')
        add_node(node_map, 'lpass.spf', 'SPF',                 'soc',       'SPF (Signal Processing Framework)', 'grp_lpass')
        add_node(node_map, 'lpass.rx',  'LPAIF RX',            'soc',       'LPAIF RX + Interpolator',           'grp_lpass')
        add_node(node_map, 'lpass.va',  'LPAIF VA',            'soc',       'LPAIF VA + Decimator',              'grp_lpass')
        add_node(node_map, 'bus.swm',   'SoundWire Master',    'component', 'SoundWire Master',                  'grp_buses')
        add_node(node_map, 'bus.pcm',   'PCM/TDM Ports',       'component', 'PCM/TDM Ports',                     'grp_buses')
        add_node(node_map, 'bus.dmic',  'DMIC Interface',      'component', 'DMIC Interface',                    'grp_buses')
        add_node(node_map, 'sink.speakers', 'Speakers',        'component', 'Speakers',                           'grp_speakers')
        for s,d in [('host.app','host.kdrv'), ('host.kdrv','host.ddr'), ('host.ddr','lpass.spf'), ('lpass.spf','lpass.rx'), ('lpass.spf','lpass.va'), ('lpass.spf','bus.swm'), ('lpass.spf','bus.pcm'), ('lpass.spf','bus.dmic')]:
            add_edge('hardware', s, d, '')
        # hardware nodes filtered
        nodes_src = getattr(self.parser, 'get_hardware_nodes', lambda: [])() or []
        for n in nodes_src:
            try:
                if hasattr(self, '_is_high_level_node') and not self._is_high_level_node(n):
                    continue
            except Exception:
                pass
            raw_id = n.get('id',''); ntype = n.get('type','component'); label = n.get('label',''); full_name = n.get('full_name','')
            sid = add_node(node_map, raw_id, label, ntype, full_name)
            if sid:
                node_map[sid]['parent'] = 'grp_host' if ntype == 'sndcard' else classify_parent(ntype, label, full_name)
                if ntype == 'sndcard':
                    add_edge('hardware', sid, 'host.kdrv', '')
        # dai links
        for link in (getattr(self.parser, 'dailinks', []) or []):
            name = (link.get('name') or '').replace('"','')
            cpus = link.get('cpu', []) or []
            codecs = link.get('codec', []) or []
            for cpu in cpus:
                add_node(node_map, cpu, cpu, 'cpu', cpu, 'grp_lpass')
            for codec in codecs:
                parent = 'grp_peripherals' if any(p in codec.lower() for p in ['wcd','wsa','codec','amp']) else classify_parent('codec', codec, codec)
                add_node(node_map, codec, codec, 'codec', codec, parent)
            for cpu in cpus:
                for codec in codecs:
                    add_edge('dai', cpu, codec, name)
            for codec in codecs:
                lc = codec.lower()
                if 'wcd' in lc:
                    add_edge('hardware', 'bus.swm', codec, '')
                elif 'wsa' in lc or 'amp' in lc:
                    add_edge('hardware', 'bus.swm', codec, '')
                    add_edge('hardware', codec, 'sink.speakers', '')
                else:
                    add_edge('hardware', 'bus.pcm', codec, '')
        # routing
        for src_id, dst_id in (getattr(self.parser, 'routing', []) or []):
            add_edge('routing', (src_id or ''), (dst_id or ''), '')
        # optional DMICs
        has_dmic = any('dmic' in (node_map[k]['label'] or '').lower() or 'dmic' in (node_map[k]['full_name'] or '').lower() for k in node_map.keys())
        if has_dmic:
            add_node(node_map, 'periph.dmic', 'DMICs', 'component', 'Digital Microphones', 'grp_peripherals')
            add_edge('hardware', 'bus.dmic', 'periph.dmic', '')
        return {'nodes': list(node_map.values()), 'edges': edges}
