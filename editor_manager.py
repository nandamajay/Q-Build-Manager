import os
import subprocess
import json
from flask import Blueprint, render_template_string, request, jsonify
import sys

# --- SAFE IMPORT HELPER ---
# This fixes the issue where the editor crashes because it can't find 'get_config'
def get_config_safe(project_name):
    # Try importing from web_manager (module)
    try:
        from web_manager import get_config
        return get_config(project_name)
    except ImportError:
        # If running as main, try importing from __main__
        try:
            from __main__ import get_config
            return get_config(project_name)
        except ImportError:
            return None, {}

# --- AI HELPER IMPORT ---
try:
    import ai_helper
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

editor_bp = Blueprint('editor_bp', __name__)

IDE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <title>Pro Editor - {{ project }}</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.css" rel="stylesheet"/>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet"/>
    <style>
        :root { --bg-dark: #1e1e1e; --bg-panel: #252526; --accent: #007acc; --text: #cccccc; --border: #3e3e42; }
        * { box-sizing: border-box; }
        html, body { height: 100%; margin: 0; background-color: var(--bg-dark); color: var(--text); font-family: 'Segoe UI', sans-serif; overflow: hidden; }
        #app { display: flex; flex-direction: column; height: 100vh; }
        
        /* TOOLBAR */
        #toolbar { height: 35px; background: #333; border-bottom: 1px solid #252526; display: flex; align-items: center; padding: 0 10px; gap: 10px; }
        .tool-btn { background: #444; color: #fff; border: 1px solid #555; padding: 4px 12px; font-size: 13px; cursor: pointer; border-radius: 3px; display: flex; align-items: center; gap: 6px; }
        .tool-btn:hover { background: #555; }
        .tool-btn.primary { background: var(--accent); border-color: var(--accent); }
        .tool-btn.ai { background: #6a1b9a; border-color: #8e24aa; } 
        .tool-btn.ai:hover { background: #8e24aa; }

        /* WORKSPACE */
        #workspace { flex: 1; display: flex; overflow: hidden; position: relative; }
        #sidebar { width: 260px; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
        #sidebar-tabs { display: flex; border-bottom: 1px solid var(--border); }
        .tab-btn { flex: 1; padding: 8px; cursor: pointer; background: var(--bg-panel); border: none; color: #888; font-weight: bold; font-size: 11px; }
        .tab-btn.active { color: white; border-bottom: 2px solid var(--accent); background: #1e1e1e; }
        
        #file-tree, #search-panel { flex: 1; overflow-y: auto; display:none; }
        #file-tree.active, #search-panel.active { display:block; }
        
        /* AI MODAL */
        #ai-modal {
            position: absolute; top: 50px; left: 50%; transform: translateX(-50%);
            width: 400px; background: #252526; border: 1px solid #6a1b9a;
            box-shadow: 0 4px 15px rgba(0,0,0,0.5); z-index: 2000; padding: 15px; display: none; border-radius: 5px;
        }
        #ai-input { width: 100%; padding: 8px; background: #333; color: white; border: 1px solid #444; margin-bottom: 10px; }
        #ai-status { font-size: 11px; color: #888; margin-top: 5px; }

        #search-box { width: 95%; margin: 10px auto; background: #3c3c3c; border: 1px solid #555; color: white; padding: 5px; display: block; }
        .search-res { font-size: 12px; padding: 5px 10px; border-bottom: 1px solid #333; cursor: pointer; }
        .res-file { color: #4ec9b0; font-weight: bold; }
        .res-text { color: #ce9178; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

        #editor-wrapper { flex: 1; position: relative; display: flex; flex-direction: column; }
        #monaco-container { flex: 1; }
        #terminal-panel { height: 25%; background: #1e1e1e; border-top: 1px solid var(--accent); display: none; flex-direction: column; position: absolute; bottom: 0; left: 0; right: 0; z-index: 100; }
        #terminal-panel.open { display: flex; }
        #terminal-panel.maximized { height: 50%; }
        #xterm-container { flex: 1; overflow: hidden; padding: 5px; background: #000; }
        
        .t-item { padding: 3px 10px; cursor: pointer; white-space: nowrap; font-size: 13px; color: #bbb; display: flex; align-items: center; }
        .t-item:hover { background: #2a2d2e; color: #fff; }
        .is-dir { font-weight: bold; color: #e7e7e7; }
        .git-mod { color: #e2c08d; }
        #statusbar { height: 22px; background: var(--accent); color: white; font-size: 12px; display: flex; align-items: center; padding: 0 10px; }
    </style>
</head>
<body>

<div id="app">
    <div id="toolbar">
        <div style="font-weight:bold; margin-right:15px;">{{ project }}</div>
        <button class="tool-btn" onclick="saveFile()"><i class="fas fa-save"></i> Save</button>
        <button class="tool-btn" onclick="refreshTree()"><i class="fas fa-sync"></i></button>
        <div style="width:1px; height:20px; background:#555; margin:0 5px;"></div>
        <button class="tool-btn ai" onclick="openAI()"><i class="fas fa-magic"></i> Smart Assist</button>
        <div style="flex:1"></div>
        <button class="tool-btn primary" onclick="toggleTerminal()"><i class="fas fa-terminal"></i> Term</button>
    </div>

    <div id="ai-modal">
        <div style="font-weight:bold; color: #ce9178; margin-bottom:5px;">AI Code Generator</div>
        <input id="ai-input" placeholder="e.g., 'create a flask route'..." type="text"/>
        <div style="display:flex; justify-content:flex-end; gap:5px;">
            <button class="tool-btn" onclick="closeAI()">Cancel</button>
            <button class="tool-btn ai" onclick="submitAI()">Generate</button>
        </div>
        <div id="ai-status"></div>
    </div>

    <div id="workspace">
        <div id="sidebar">
            <div id="sidebar-tabs">
                <button class="tab-btn active" onclick="switchTab('files')">FILES</button>
                <button class="tab-btn" onclick="switchTab('search')">SEARCH</button>
            </div>
            <div class="active" id="file-tree">Loading...</div>
            <div id="search-panel">
                <input id="search-box" onkeyup="if(event.key==='Enter') doSearch()" placeholder="Search..." type="text"/>
                <div id="search-results"></div>
            </div>
        </div>
        <div id="editor-wrapper">
            <div id="monaco-container"></div>
            <div id="terminal-panel">
                <div style="height:25px; background:#333; display:flex; justify-content:flex-end;">
                     <i class="fas fa-arrows-alt-v" onclick="toggleTermSize()" style="color:white; padding:5px; cursor:pointer;"></i>
                     <i class="fas fa-times" onclick="toggleTerminal()" style="color:white; padding:5px; cursor:pointer;"></i>
                </div>
                <div id="xterm-container" onclick="term.focus()"></div>
            </div>
        </div>
    </div>
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
    var project = "{{ project }}";
    var currentPath = {{ initial_file | tojson }};
    var editor, term;
    var termOpen = false;

    // Load Monaco
    require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }});
    require(['vs/editor/editor.main'], function () {
        editor = monaco.editor.create(document.getElementById('monaco-container'), {
            value: "// Select a file", language: 'javascript', theme: 'vs-dark', automaticLayout: true
        });
        
        // Load initial file if present
        if(currentPath && currentPath !== "None") {
            loadFile(currentPath);
        }

        // Shortcuts
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyI, openAI);
        
        editor.onDidChangeCursorPosition(function(e) {
            document.getElementById('cursor-pos').innerText = "Ln " + e.position.lineNumber + ", Col " + e.position.column;
        });

        initTerminal();
        refreshTree();
    });

    // --- AI Functions ---
    function openAI() { 
        document.getElementById('ai-modal').style.display = 'block'; 
        document.getElementById('ai-input').focus(); 
    }
    function closeAI() { 
        document.getElementById('ai-modal').style.display = 'none'; 
        editor.focus(); 
    }
    function submitAI() {
        var prompt = document.getElementById('ai-input').value;
        var status = document.getElementById('ai-status');
        if(!prompt) return;
        status.innerText = "Generating...";
        
        fetch('/editor/api/ai', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ prompt: prompt, language: editor.getModel().getLanguageId() })
        }).then(function(r) { return r.json(); })
          .then(function(data) {
            if(data.code) {
                var pos = editor.getPosition();
                editor.executeEdits("ai", [{ range: new monaco.Range(pos.lineNumber, pos.column, pos.lineNumber, pos.column), text: data.code }]);
                closeAI();
                document.getElementById('status-msg').innerText = "Code generated!";
            } else {
                status.innerText = "Error: " + data.error;
            }
        }).catch(function(e) { status.innerText = "Request Failed"; });
    }
    
    document.getElementById('ai-input').addEventListener("keyup", function(e) {
        if (e.key === "Enter") submitAI();
        if (e.key === "Escape") closeAI();
    });

    // --- Terminal ---
    var cmdBuffer = "";
    function initTerminal() {
        Terminal.applyAddon(fit);
        term = new Terminal({ cursorBlink: true, fontSize: 13, theme: { background: '#1e1e1e' } });
        term.open(document.getElementById('xterm-container'));
        term.fit();
        term.write('\\r\\n$ ');
        
        term.on('data', function(key) {
             var code = key.charCodeAt(0);
             if (code === 13) { // Enter
                 term.write('\\r\\n');
                 if(cmdBuffer.trim()) runCommand(cmdBuffer.trim());
                 else term.write('$ ');
                 cmdBuffer = "";
             } else if (code === 127) { // Backspace
                 if (cmdBuffer.length > 0) { 
                    cmdBuffer = cmdBuffer.slice(0, -1); 
                    term.write('\\b \\b'); 
                 }
             } else {
                 cmdBuffer += key;
                 term.write(key);
             }
        });
    }
    
    function runCommand(cmd) {
        if(cmd === 'clear') { term.clear(); term.write('$ '); return; }
        fetch('/editor/api/term', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ project: project, cmd: cmd })
        }).then(function(r) { return r.json(); })
          .then(function(data) {
            var out = data.output || "";
            out = out.replace(/\\n/g, '\\r\\n');
            term.write(out);
            term.write('\\r\\n$ ');
        });
    }
    
    function toggleTerminal() {
        var p = document.getElementById('terminal-panel');
        termOpen = !termOpen;
        if(termOpen) { 
            p.classList.add('open'); 
            setTimeout(function() { term.fit(); term.focus(); }, 150); 
        } else { 
            p.classList.remove('open'); 
            editor.focus(); 
        }
    }
    function toggleTermSize() { 
        document.getElementById('terminal-panel').classList.toggle('maximized'); 
        setTimeout(function() { term.fit(); }, 100); 
    }

    // --- Files & Navigation ---
    function switchTab(t) {
        document.getElementById('file-tree').classList.remove('active');
        document.getElementById('search-panel').classList.remove('active');
        var tabs = document.querySelectorAll('.tab-btn');
        tabs.forEach(function(b) { b.classList.remove('active'); });
        
        if(t==='files') {
            document.getElementById('file-tree').classList.add('active');
            tabs[0].classList.add('active');
        } else {
            document.getElementById('search-panel').classList.add('active');
            tabs[1].classList.add('active');
        }
    }
    
    function doSearch() {
        var q = document.getElementById('search-box').value;
        var resDiv = document.getElementById('search-results');
        resDiv.innerHTML = "Searching...";
        fetch('/editor/api/search', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ project:project, query:q })
        }).then(function(r) { return r.json(); })
          .then(function(d) {
            resDiv.innerHTML = "";
            d.results.forEach(function(r) {
                var div = document.createElement('div');
                div.className = "search-res";
                div.innerHTML = '<div class="res-file">' + r.file + ':' + r.line + '</div><div class="res-text">' + r.text + '</div>';
                div.onclick = function() {
                    loadFile(r.file);
                    setTimeout(function() { editor.revealLineInCenter(parseInt(r.line)); }, 500);
                };
                resDiv.appendChild(div);
            });
        });
    }
    
    function refreshTree() { loadDir('', document.getElementById('file-tree')); }
    
    function loadDir(path, container) {
        // Safe string concatenation
        var url = '/editor/api/tree?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path);
        
        fetch(url).then(function(r) { return r.json(); })
          .then(function(nodes) {
            container.innerHTML = "";
            if(nodes.error) {
                container.innerHTML = "<div style='color:red; padding:5px;'>Error: " + nodes.error + "</div>";
                return;
            }
            // Sort dirs first
            nodes.sort(function(a,b) { 
                if(a.type === b.type) return a.name.localeCompare(b.name);
                return (a.type==='dir' ? -1 : 1);
            });
            
            nodes.forEach(function(n) {
                var div = document.createElement('div');
                div.className = "t-item";
                if(n.type === 'dir') div.classList.add("is-dir");
                
                var icon = (n.type === 'dir') ? '📂' : '📄';
                div.innerHTML = icon + " " + n.name;
                
                div.onclick = function(e) {
                    e.stopPropagation();
                    if(n.type === 'file') {
                        loadFile(n.path);
                    } else {
                        // Toggle folder
                        if(div.nextSibling && div.nextSibling.classList.contains('sub')) {
                            div.nextSibling.remove();
                        } else {
                            var sub = document.createElement('div'); 
                            sub.className = 'sub'; 
                            sub.style.paddingLeft = "15px"; 
                            div.after(sub); 
                            loadDir(n.path, sub);
                        }
                    }
                };
                container.appendChild(div);
            });
        }).catch(function(e) {
            console.error(e);
            container.innerHTML = "Err loading tree";
        });
    }
    
    function loadFile(path) {
        var url = '/editor/api/read?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path);
        fetch(url).then(function(r) { return r.json(); })
        .then(function(d) {
            var ext = path.split('.').pop();
            var lang = 'plaintext';
            if(ext === 'py') lang = 'python';
            if(ext === 'js') lang = 'javascript';
            if(ext === 'html') lang = 'html';
            if(ext === 'json') lang = 'json';
            if(ext === 'c' || ext === 'h' || ext === 'cpp') lang = 'cpp';
            
            // Create model
            var model = monaco.editor.createModel(d.content, lang);
            editor.setModel(model);
            
            currentPath = path;
            document.getElementById('status-msg').innerText = "Opened: " + path;
        });
    }
    
    function saveFile() {
        if(!currentPath) return;
        fetch('/save_file', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ project:project, path:currentPath, content:editor.getValue() })
        }).then(function(r){ return r.json(); })
          .then(function(d) { document.getElementById('status-msg').innerText = "Saved " + currentPath; });
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

@editor_bp.route('/editor/api/ai', methods=['POST'])
def ai_generate():
    if not AI_AVAILABLE:
        return jsonify({'error': 'AI Helper module not found'})
    data = request.json
    try:
        code = ai_helper.generate_code_snippet(data.get('language', 'python'), data.get('prompt', ''))
        return jsonify({'code': code})
    except Exception as e:
        return jsonify({'error': str(e)})

@editor_bp.route('/editor/api/tree')
def get_tree():
    project = request.args.get('project')
    rel = request.args.get('path', '')
    
    root, _ = get_config_safe(project)
    if not root:
        return jsonify({'error': 'Project not found'}), 404
        
    path = os.path.join(root, rel)
    nodes = []
    try:
        for i in os.listdir(path):
            if i.startswith('.') or i=='build': continue
            fp = os.path.join(path, i)
            nodes.append({'name': i, 'path': os.path.join(rel, i), 'type': 'dir' if os.path.isdir(fp) else 'file'})
    except Exception as e: 
        return jsonify({'error': str(e)})
        
    return jsonify(nodes)

@editor_bp.route('/editor/api/read')
def read_file_api():
    root, _ = get_config_safe(request.args.get('project'))
    try:
        with open(os.path.join(root, request.args.get('path')), 'r') as f: 
            return jsonify({'content': f.read()})
    except: 
        return jsonify({'content': "// Error reading file"})

@editor_bp.route('/editor/api/term', methods=['POST'])
def run_term():
    data = request.json
    root, _ = get_config_safe(data.get('project'))
    try:
        out = subprocess.check_output(data['cmd'], shell=True, cwd=root, stderr=subprocess.STDOUT, text=True)
        return jsonify({'output': out})
    except Exception as e: 
        return jsonify({'output': str(e)})

@editor_bp.route('/save_file', methods=['POST'])
def save_file_route():
    data = request.json
    root, _ = get_config_safe(data['project'])
    try:
        with open(os.path.join(root, data['path']), 'w') as f: f.write(data['content'])
        return jsonify({'status': 'ok'})
    except: 
        return jsonify({'status': 'error'})

@editor_bp.route('/editor/api/search', methods=['POST'])
def search_grep():
    project = request.json.get('project')
    query = request.json.get('query')
    root, _ = get_config_safe(project)
    try:
        cmd = ["grep", "-rnI", "--exclude-dir=.git", "--exclude-dir=build", "--max-count=50", query, "."]
        out = subprocess.check_output(cmd, cwd=root, text=True, stderr=subprocess.DEVNULL)
        results = []
        for line in out.splitlines():
            parts = line.split(':', 2)
            if len(parts) == 3:
                results.append({'file': parts[0].replace('./',''), 'line': parts[1], 'text': parts[2].strip()[:80]})
        return jsonify({'results': results})
    except: 
        return jsonify({'results': []})
