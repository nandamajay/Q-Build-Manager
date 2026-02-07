import re
import hashlib

class DiagramBuilder:
    def __init__(self, parser):
        print("[V26] High-Level Block Engine Loaded")
        self.parser = parser

    def _get_safe_id(self, raw_id):
        if not raw_id:
            return "node_unknown"
        return "node_" + hashlib.md5(raw_id.encode()).hexdigest()[:8]

    def _is_high_level_node(self, node):
        label = node.get('label', '').lower()
        nid   = node.get('id', '').lower()
        if '#include' in label or 'dt-bindings' in label:
            return False
        if 'pinctrl' in nid or 'gpio' in nid:
            return False
        return True

    def sanitize_label(self, text):
        if not text:
            return 'Block'
        clean = text.split('"')[0]
        clean = clean.split('<')[0]
        clean = clean.replace('_', ' ').title()
        if len(clean) > 25:
            clean = clean[:22] + '...'
        return clean

    def build_all(self):
        return {
            'hardware': self.build_hardware_diagram(),
            'dailinks': self.build_dailinks_diagram(),
            'routing':  self.build_routing_diagram()
        }

    # Mermaid helpers (unchanged)
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
            raw_id  = n['id']
            safe_id = self._get_safe_id(raw_id)
            valid_ids.add(raw_id)
            label = self.sanitize_label(n['label'])
            ntype = n.get('type', 'generic')
            css   = 'gen'
            o,c   = '[',']'
            if ntype == 'sndcard': css='snd'; o,c='([','])'
            elif ntype == 'soc':   css='soc'
            elif ntype == 'codec': css='codec'; o,c='([','])'
            lines.append(f"{safe_id}{o}\"{label}\"{c}:::{css}")
            lines.append(f"click {safe_id} callNodeCallback \"{raw_id}\"")
        for src,dst,lbl in conns:
            if src in valid_ids and dst in valid_ids:
                s = self._get_safe_id(src); d = self._get_safe_id(dst)
                l = self.sanitize_label(lbl) or 'link'
                if s != d:
                    lines.append(f"{s} -- \"{l}\" --> {d}")
        return "\n".join(lines)

    def build_dailinks_diagram(self):
        lines=["graph LR","classDef cpu fill:#2962ff,color:white","classDef codec fill:#00c853,color:white"]
        for link in self.parser.dailinks:
            name   = self.sanitize_label(link['name'])
            cpus   = [c for c in link['cpu']]
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
        lines=["graph LR"]
        for src,dst in self.parser.routing:
            s=self._get_safe_id(src); d=self._get_safe_id(dst)
            lines.append(f"{s} --> {d}")
        return "\n".join(lines)

    # Cytoscape JSON (dynamic SWR + PCM fallback)
    def build_graph_json(self):
        import re

        def add_node(node_map, raw_id, label=None, ntype='component', full_name=None, parent=None):
            if not raw_id:
                return None
            sid = self._get_safe_id(raw_id)
            if sid not in node_map:
                lab = label if label is not None else (raw_id or '')
                lab = self.sanitize_label(lab)
                node_map[sid] = {
                    'id': sid,
                    'label': (lab or '').replace('"',''),
                    'type': ntype or 'component',
                    'full_name': full_name or raw_id
                }
            if parent is not None:
                node_map[sid]['parent'] = parent
            return sid

        def classify_parent_for(sid):
            n = node_map.get(sid, {})
            if not n: return 'grp_lpass'
            if n.get('type') == 'group':
                return None  # groups must stay top-level
            lbl = (n.get('label') or '').lower(); fn = (n.get('full_name') or '').lower(); t = n.get('type')
            if any(x in lbl for x in ['spkr','speaker']) or any(x in fn for x in ['spkr','speaker']): return 'grp_speakers'
            if t in ('codec','amp') or any(x in lbl for x in ['wcd','wsa','codec','amp','max98357']) or any(x in fn for x in ['wcd','wsa','codec','amp','max98357']): return 'grp_peripherals'
            if t == 'bus' or any(x in lbl for x in ['soundwire','pcm','tdm','dmic']): return 'grp_buses'
            if t == 'sndcard': return 'grp_host'
            return 'grp_lpass'

        def ensure_parent(sid):
            if not sid: return
            n = node_map.get(sid, {})
            if not n or n.get('type') == 'group':
                return  # never parent groups inside other groups
            if 'parent' not in n or n['parent'] is None:
                node_map[sid]['parent'] = classify_parent_for(sid)

        def add_edge(kind, src_raw, dst_raw, label=''):
            s = add_node(node_map, src_raw); d = add_node(node_map, dst_raw)
            if not s or not d: return
            ensure_parent(s); ensure_parent(d)
            edges.append({'source': s, 'target': d, 'kind': kind, 'label': (label or '').replace('"','')})

        def is_macro(tok):
            tl = (tok or '').lower()
            return tl.startswith('lpass_') and ('macro' in tl)

        def macro_name(tok):
            tl = (tok or '').lower()
            if 'rxmacro' in tl: return 'rxmacro','Lpaif Rx'
            if 'txmacro' in tl: return 'txmacro','Lpaif Tx'
            if 'wsamacro' in tl: return 'wsamacro','Wsa Macro'
            if 'vamacro' in tl: return 'vamacro','Va Macro'
            return 'macro','Macro'

        def is_platform(tok):
            tl = (tok or '').lower(); return tl.startswith('q6apm') or tl == 'q6apm'

        def is_swr(tok):
            return re.search(r'\bswr(\d+)\b', (tok or '').lower()) is not None

        def swr_index(tok):
            m = re.search(r'\bswr(\d+)\b', (tok or '').lower()); return int(m.group(1)) if m else None

        def endpoint_label(tok):
            tl = (tok or '').lower()
            if 'left_spkr' in tl:  return 'Left Spk'
            if 'right_spkr' in tl: return 'Right Spk'
            if 'wcd' in tl:        return tok.upper()
            if 'wsa' in tl:        return tok.upper()
            if 'max98357' in tl:   return 'MAX98357A'
            return self.sanitize_label(tok)

        def endpoint_type(tok):
            tl = (tok or '').lower()
            if 'spkr' in tl: return 'speaker'
            if 'wsa' in tl or 'max98357' in tl: return 'amp'  # treat MAX98357A as amp (I2S/TDM)
            return 'codec'

        # groups (top-level containers)
        node_map = {
            'grp_host':       {'id':'grp_host','label':'Host / APSS','type':'group','full_name':'Host / APSS'},
            'grp_lpass':      {'id':'grp_lpass','label':'LPASS','type':'group','full_name':'LPASS'},
            'grp_buses':      {'id':'grp_buses','label':'Audio Buses','type':'group','full_name':'Audio Buses'},
            'grp_peripherals':{'id':'grp_peripherals','label':'Peripherals','type':'group','full_name':'Peripherals'},
            'grp_speakers':   {'id':'grp_speakers','label':'Speakers','type':'group','full_name':'Speakers'},
        }
        edges = []

        # Anchors
        add_node(node_map, 'host.app',  'App Framework',       'component', 'App Framework (AudioFlinger/ALSA)', 'grp_host')
        add_node(node_map, 'host.kdrv', 'Kernel ALSA Drivers', 'component', 'Kernel ALSA Drivers',               'grp_host')
        add_node(node_map, 'host.ddr',  'DMA Buffers (DDR)',   'component', 'DMA Buffers (DDR)',                 'grp_host')
        add_node(node_map, 'lpass.spf', 'Spf',                 'soc',       'SPF (Signal Processing Framework)', 'grp_lpass')
        add_node(node_map, 'bus.pcm',   'Pcm/Tdm Ports',       'bus',       'PCM/TDM Ports',                     'grp_buses')
        add_node(node_map, 'bus.dmic',  'Dmic Interface',      'bus',       'DMIC Interface',                    'grp_buses')

        add_edge('hardware', 'host.app', 'host.kdrv')
        add_edge('hardware', 'host.kdrv','host.ddr')
        add_edge('hardware', 'host.ddr', 'lpass.spf')

        # Base hardware nodes — do NOT force parents here (except sndcard)
        for n in (getattr(self.parser,'get_hardware_nodes',lambda:[])() or []):
            try:
                if hasattr(self,'_is_high_level_node') and not self._is_high_level_node(n):
                    continue
            except Exception:
                pass
            sid = add_node(node_map, n.get('id',''), n.get('label',''), n.get('type','component'), n.get('full_name',''))
            if sid and n.get('type') == 'sndcard':
                node_map[sid]['parent'] = 'grp_host'
                add_edge('hardware', n.get('id',''), 'host.kdrv')

        # DAI links -> dynamic wiring
        for link in (getattr(self.parser,'dailinks',[]) or []):
            name   = (link.get('name') or '').replace('"','')
            cpus   = link.get('cpu',   []) or []
            codecs = link.get('codec', []) or []

            for cpu in cpus:
                add_node(node_map, cpu, cpu, 'cpu', cpu, 'grp_lpass')

            macros   = []   # (macro_id, label)
            masters  = {}   # swr_index -> 'bus.swrN'
            endpoints= []   # real DTS tokens in this link

            for tok in codecs:
                if not tok: continue
                if is_platform(tok):
                    continue
                if is_macro(tok):
                    mkey,mlab = macro_name(tok)
                    mid = f'lpass.{mkey}'
                    macros.append((mid, mlab))
                    add_node(node_map, mid, mlab, 'soc', f'LPASS {mlab}', 'grp_lpass')
                    continue
                if is_swr(tok):
                    idx = swr_index(tok)
                    if idx is not None and idx not in masters:
                        bid = f'bus.swr{idx}'
                        add_node(node_map, bid, f'SWR{idx} Master', 'bus', f'SoundWire SWR{idx} Master', 'grp_buses')
                        masters[idx] = bid
                    continue
                # endpoints -> enforce Peripherals/Speakers, never LPASS
                typ    = endpoint_type(tok)
                parent = 'grp_speakers' if typ == 'speaker' else 'grp_peripherals'
                add_node(node_map, tok, endpoint_label(tok), typ, tok, parent)
                endpoints.append(tok)

            # SPF to Macros or to Masters directly
            if macros:
                for mid,_ in macros:
                    add_edge('hardware', 'lpass.spf', mid)
                for mid,_ in macros:
                    for _,bid in masters.items():
                        add_edge('hardware', mid, bid)
            else:
                for _,bid in masters.items():
                    add_edge('hardware', 'lpass.spf', bid)

            # Wire masters or fall back to PCM/TDM
            if masters and endpoints:
                has_speakers = any('spkr' in (e or '').lower() for e in endpoints)
                has_wsa      = any('wsa'  in (e or '').lower() for e in endpoints)
                for _,bid in masters.items():
                    if has_speakers and not has_wsa:
                        wsa_auto = 'periph.wsa_auto'
                        add_node(node_map, wsa_auto, 'Wsa Amplifier', 'amp', 'WSA Amplifier', 'grp_peripherals')
                        add_edge('hardware', bid, wsa_auto)
                        for ep in endpoints:
                            if 'spkr' in ep.lower():
                                add_edge('hardware', wsa_auto, ep)
                    for ep in endpoints:
                        if has_speakers and not has_wsa and 'spkr' in ep.lower():
                            continue
                        add_edge('hardware', bid, ep)

            elif macros and endpoints:
                for mid,_ in macros:
                    for ep in endpoints:
                        add_edge('hardware', mid, ep)
            else:
                # No SWR masters and no macros: classic PCM/TDM path
                # Ensure SPF -> PCM/TDM Ports is visible for this link
                add_edge('hardware', 'lpass.spf', 'bus.pcm')
                for ep in endpoints:
                    add_edge('hardware', 'bus.pcm', ep)

            # VA macro path (non-SoundWire)
            if any(mid for mid,_ in macros if mid.endswith('.vamacro')):
                add_edge('hardware', 'lpass.vamacro', 'bus.dmic')
                add_node(node_map, 'periph.dmic', 'DMICs', 'component', 'Digital Microphones', 'grp_peripherals')
                add_edge('hardware', 'bus.dmic', 'periph.dmic')

            # DAI edges preserved
            for cpu in cpus:
                for codec in codecs:
                    add_edge('dai', cpu, codec, name)

            # If any endpoint looks like a WCD codec, attach a Headset/Earpiece sink
            if any(('wcd' in (ep or '').lower()) for ep in endpoints):
                add_node(node_map, 'sink.headset', 'Headphones / Earpiece', 'speaker', 'Headphones / Earpiece', 'grp_speakers')
                for ep in endpoints:
                    if 'wcd' in (ep or '').lower():
                        add_edge('hardware', ep, 'sink.headset')

        # Assign parents where missing (skip group nodes)
        for sid in list(node_map.keys()):
            ensure_parent(sid)

        # Optional: DMICs if hinted anywhere
        has_dmic = any('dmic' in (node_map[k]['label'] or '').lower() or 'dmic' in (node_map[k]['full_name'] or '').lower()
                       for k in node_map.keys())
        if has_dmic and 'periph.dmic' not in node_map:
            add_node(node_map, 'periph.dmic', 'DMICs', 'component', 'Digital Microphones', 'grp_peripherals')
            add_edge('hardware', 'bus.dmic', 'periph.dmic')

        return {'nodes': list(node_map.values()), 'edges': edges}
