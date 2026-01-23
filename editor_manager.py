import os
import subprocess
import json
from flask import Blueprint, render_template_string, request, jsonify

editor_bp = Blueprint('editor_bp', __name__)

IDE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Pro Editor - {{ project }}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.css" />
    <!-- Icons -->
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        :root { 
            --bg-dark: #1e1e1e; 
            --bg-panel: #252526; 
            --accent: #007acc; 
            --text: #cccccc; 
            --border: #3e3e42; 
        }
        * { box-sizing: border-box; }
        html, body { height: 100%; margin: 0; background-color: var(--bg-dark); color: var(--text); font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; overflow: hidden; }
        
        /* LAYOUT GRID */
        #app { display: flex; flex-direction: column; height: 100vh; }
        
        /* TOP TOOLBAR */
        #toolbar { 
            height: 35px; background: #333333; border-bottom: 1px solid #252526; 
            display: flex; align-items: center; padding: 0 10px; gap: 10px;
        }
        .tool-btn { 
            background: #444; color: #fff; border: 1px solid #555; 
            padding: 4px 12px; font-size: 13px; cursor: pointer; border-radius: 3px; 
            display: flex; align-items: center; gap: 6px;
        }
        .tool-btn:hover { background: #555; }
        .tool-btn.primary { background: var(--accent); border-color: var(--accent); }
        .tool-btn.primary:hover { background: #0062a3; }

        /* MIDDLE AREA (Sidebar + Editor) */
        #workspace { flex: 1; display: flex; overflow: hidden; position: relative; }
        
        /* SIDEBAR */
        #sidebar { width: 250px; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
        #sidebar-header { padding: 10px; font-weight: bold; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #888; }
        #file-tree { flex: 1; overflow-y: auto; }
        
        /* EDITOR AREA */
        #editor-wrapper { flex: 1; position: relative; display: flex; flex-direction: column; }
        #monaco-container { flex: 1; }

        /* TERMINAL PANEL (Fixed at Bottom) */
        #terminal-panel {
            height: 25%; /* DEFAULT 25% */
            background: #1e1e1e;
            border-top: 1px solid var(--accent);
            display: none; /* Hidden by default */
            flex-direction: column;
            position: absolute;
            bottom: 0; left: 0; right: 0;
            z-index: 100;
            box-shadow: 0 -2px 10px rgba(0,0,0,0.5);
        }
        #terminal-panel.open { display: flex; }
        #terminal-panel.maximized { height: 50%; }

        #term-header { 
            height: 28px; background: #2d2d2d; border-bottom: 1px solid #333; 
            display: flex; justify-content: space-between; align-items: center; padding: 0 10px; 
            font-size: 12px; font-weight: bold; color: #ddd; cursor: default; user-select: none;
        }
        #term-actions i { cursor: pointer; margin-left: 10px; padding: 3px; }
        #term-actions i:hover { color: white; }
        
        #xterm-container { flex: 1; overflow: hidden; padding: 5px; background: #000; }

        /* Tree Items */
        .t-item { padding: 3px 10px; cursor: pointer; white-space: nowrap; font-size: 13px; color: #bbb; display: flex; align-items: center; }
        .t-item:hover { background: #2a2d2e; color: #fff; }
        .t-icon { width: 18px; display: inline-block; text-align: center; margin-right: 5px; }
        .is-dir { font-weight: bold; color: #e7e7e7; }
        .git-mod { color: #e2c08d; }

        /* STATUS BAR */
        #statusbar { height: 22px; background: var(--accent); color: white; font-size: 12px; display: flex; align-items: center; padding: 0 10px; }
    </style>
</head>
<body>

<div id="app">
    <!-- 1. TOOLBAR -->
    <div id="toolbar">
        <div style="font-weight:bold; margin-right:15px;">Project: {{ project }}</div>
        <button class="tool-btn" onclick="saveFile()"><i class="fas fa-save"></i> Save</button>
        <button class="tool-btn" onclick="refreshTree()"><i class="fas fa-sync"></i> Refresh</button>
        <div style="flex:1"></div>
        <button class="tool-btn primary" onclick="toggleTerminal()"><i class="fas fa-terminal"></i> Terminal (Ctrl+J)</button>
    </div>

    <!-- 2. WORKSPACE -->
    <div id="workspace">
        <div id="sidebar">
            <div id="sidebar-header">Explorer</div>
            <div id="file-tree">Loading...</div>
        </div>
        
        <div id="editor-wrapper">
            <div id="monaco-container"></div>
            
            <!-- 3. TERMINAL (Overlay at bottom of editor) -->
            <div id="terminal-panel">
                <div id="term-header">
                    <span><i class="fas fa-code"></i> TERMINAL / CONSOLE</span>
                    <div id="term-actions">
                        <i class="fas fa-arrows-alt-v" onclick="toggleTermSize()" title="Toggle Size (25% / 50%)"></i>
                        <i class="fas fa-times" onclick="toggleTerminal()" title="Close"></i>
                    </div>
                </div>
                <div id="xterm-container"></div>
            </div>
        </div>
    </div>

    <!-- 4. STATUS -->
    <div id="statusbar">
        <span id="status-msg">Ready</span>
        <div style="flex:1"></div>
        <span id="cursor-pos">Ln 1, Col 1</span>
    </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/addons/fit/fit.min.js"></script>

<script>
    const project = "{{ project }}";
    let currentPath = {{ initial_file | tojson }};
    let editor, term;
    let termOpen = false;

    // --- MONACO INIT ---
    require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }});
    require(['vs/editor/editor.main'], function () {
        editor = monaco.editor.create(document.getElementById('monaco-container'), {
            value: "// Select a file to view",
            language: 'javascript',
            theme: 'vs-dark',
            automaticLayout: true,
            minimap: { enabled: false }
        });

        if(currentPath && currentPath !== "None") loadFile(currentPath);

        // Events
        editor.onDidChangeCursorPosition(e => {
            document.getElementById('cursor-pos').innerText = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
        });
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyJ, toggleTerminal);
        
        initTerminal();
        refreshTree();
    });

    // --- TERMINAL LOGIC ---
    function initTerminal() {
        Terminal.applyAddon(fit);
        term = new Terminal({
            cursorBlink: true,
            fontSize: 14,
            fontFamily: 'Consolas, "Courier New", monospace',
            theme: { background: '#000000', foreground: '#f0f0f0' }
        });
        term.open(document.getElementById('xterm-container'));
        term.fit();
        
        term.write('\\r\\n\\x1b[1;34mQ-Genie Terminal\\x1b[0m\\r\\n');
        prompt();

        let cmd = "";
        
        // Input Handling
        term.on('key', (key, ev) => {
            const printable = !ev.altKey && !ev.altGraphKey && !ev.ctrlKey && !ev.metaKey;

            if (ev.keyCode === 13) { // Enter
                term.write('\\r\\n');
                if (cmd.trim()) runCommand(cmd.trim());
                else prompt();
                cmd = "";
            } else if (ev.keyCode === 8) { // Backspace
                if (cmd.length > 0) {
                    cmd = cmd.slice(0, -1);
                    term.write('\\b \\b');
                }
            } else if (printable) {
                cmd += key;
                term.write(key);
            }
        });
    }

    function prompt() {
        term.write('\\r\\n\\x1b[1;32m$ \\x1b[0m');
    }

    function runCommand(command) {
        // Simple client-side check for clear
        if(command === 'clear') { term.clear(); prompt(); return; }

        fetch('/editor/api/term', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ project: project, cmd: command })
        }).then(r => r.json()).then(data => {
            if(data.output) {
                // Convert newlines for xterm
                let out = data.output.replace(/\\n/g, '\\r\\n');
                term.write(out);
            }
            prompt();
        }).catch(err => {
            term.write('\\x1b[31mError connecting to server\\x1b[0m');
            prompt();
        });
    }

    function toggleTerminal() {
        const p = document.getElementById('terminal-panel');
        termOpen = !termOpen;
        
        if(termOpen) {
            p.classList.add('open');
            setTimeout(() => { term.fit(); term.focus(); }, 100); // Focus is key!
        } else {
            p.classList.remove('open');
            editor.focus();
        }
    }

    function toggleTermSize() {
        const p = document.getElementById('terminal-panel');
        p.classList.toggle('maximized');
        setTimeout(() => term.fit(), 100);
    }

    // --- FILE EXPLORER (LAZY) ---
    function refreshTree() {
        loadDir('', document.getElementById('file-tree'));
    }

    function loadDir(path, container) {
        container.innerHTML = '<div style="padding:10px; color:#666;">Loading...</div>';
        fetch(`/editor/api/tree?project=${project}&path=${path}`).then(r => r.json()).then(nodes => {
            container.innerHTML = "";
            
            // Sort folders first
            nodes.sort((a,b) => (a.type===b.type)?a.name.localeCompare(b.name):(a.type==='dir'?-1:1));

            nodes.forEach(n => {
                let div = document.createElement('div');
                div.className = "t-item";
                if(n.type === 'dir') div.classList.add("is-dir");
                if(n.git === 'M') div.classList.add("git-mod");

                let icon = n.type==='dir' ? '<i class="fas fa-folder t-icon"></i>' : '<i class="fas fa-file-code t-icon"></i>';
                
                div.innerHTML = `${icon} <span>${n.name}</span>`;
                div.onclick = () => {
                    if(n.type === 'file') loadFile(n.path);
                    else {
                        // Simple drill down (replaces view for this demo)
                        // For a full tree, we would append children. 
                        // To keep it fast/simple, we just append a sub-container
                        if(div.nextSibling && div.nextSibling.className === 'sub-tree') {
                            div.nextSibling.remove(); // Toggle close
                        } else {
                            let sub = document.createElement('div');
                            sub.className = 'sub-tree';
                            sub.style.paddingLeft = "15px";
                            div.parentNode.insertBefore(sub, div.nextSibling);
                            loadDir(n.path, sub);
                        }
                    }
                };
                container.appendChild(div);
            });
        });
    }

    function loadFile(path) {
        document.getElementById('status-msg').innerText = "Loading: " + path;
        fetch(`/editor/api/read?project=${project}&path=${path}`).then(r=>r.json()).then(d=>{
            if(d.error) { alert(d.error); return; }
            
            let ext = path.split('.').pop();
            let lang = 'plaintext';
            if(['js','json'].includes(ext)) lang = 'javascript';
            if(['py'].includes(ext)) lang = 'python';
            if(['c','h','cpp'].includes(ext)) lang = 'c';
            if(['html'].includes(ext)) lang = 'html';
            if(['sh','bb'].includes(ext)) lang = 'shell';

            editor.setModel(monaco.editor.createModel(d.content, lang));
            currentPath = path;
            document.getElementById('status-msg').innerText = path;
        });
    }

    function saveFile() {
        if(!currentPath) return;
        const val = editor.getValue();
        fetch('/save_file', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ project:project, path:currentPath, content:val })
        }).then(r=>r.json()).then(d => {
            document.getElementById('status-msg').innerText = d.status === 'ok' ? "Saved successfully" : "Save Failed";
        });
    }
</script>
</body>
</html>
"""

# --- BACKEND ---
@editor_bp.route('/editor/view/<project>/')
@editor_bp.route('/editor/view/<project>/<path:filepath>')
def open_editor(project, filepath=""):
    return render_template_string(IDE_HTML, project=project, initial_file=filepath if filepath else "None")

@editor_bp.route('/editor/api/tree')
def get_tree_lazy():
    from web_manager import get_config
    project = request.args.get('project')
    rel_path = request.args.get('path', '')
    root_path, _ = get_config(project)
    abs_path = os.path.join(root_path, rel_path)
    
    nodes = []
    try:
        # Basic Git Check (is modified?)
        git_mod = []
        try:
            cmd = ['git', 'status', '--porcelain', '.']
            # Run git in the specific folder to speed up
            out = subprocess.check_output(cmd, cwd=abs_path, text=True, stderr=subprocess.DEVNULL)
            for l in out.splitlines():
                if 'M' in l[:2]: git_mod.append(l[3:].strip())
        except: pass

        for item in os.listdir(abs_path):
            if item.startswith('.') or item == 'build': continue
            full = os.path.join(abs_path, item)
            is_dir = os.path.isdir(full)
            p = os.path.join(rel_path, item)
            
            nodes.append({
                'name': item,
                'path': p,
                'type': 'dir' if is_dir else 'file',
                'git': 'M' if item in git_mod else ''
            })
    except: pass
    return jsonify(nodes)

@editor_bp.route('/editor/api/read')
def read_file_api():
    from web_manager import get_config
    root, _ = get_config(request.args.get('project'))
    try:
        with open(os.path.join(root, request.args.get('path')), 'r', errors='replace') as f:
            return jsonify({'content': f.read()})
    except Exception as e: return jsonify({'error': str(e)})

@editor_bp.route('/editor/api/term', methods=['POST'])
def run_term():
    from web_manager import get_config
    data = request.json
    root, _ = get_config(data.get('project'))
    cmd = data.get('cmd', '')
    
    try:
        # Run command in project root
        # Redirect stderr to stdout to capture errors
        out = subprocess.check_output(cmd, shell=True, cwd=root, stderr=subprocess.STDOUT, text=True)
        return jsonify({'output': out})
    except subprocess.CalledProcessError as e:
        return jsonify({'output': e.output})
    except Exception as e:
        return jsonify({'output': f"Error executing command: {str(e)}"})
