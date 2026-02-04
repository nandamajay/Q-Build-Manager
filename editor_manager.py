import os
import subprocess
import json
import sys
import threading
import tempfile
import ast
import builtins
from flask import Blueprint, render_template_string, request, jsonify

# --- SAFE IMPORT HELPER ---
def get_config_safe(project_name):
    try:
        import web_manager
        return web_manager.get_config(project_name)
    except ImportError:
        return os.getcwd(), {}
    except AttributeError:
        return os.getcwd(), {}

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
    <title>Pro Editor AI - {{ project }}</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.css" rel="stylesheet"/>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet"/>
    <style>
        :root { --bg-dark: #1e1e1e; --bg-panel: #252526; --accent: #007acc; --text: #cccccc; --border: #3e3e42; }
        * { box-sizing: border-box; }
        body { height: 100vh; margin: 0; background-color: var(--bg-dark); color: var(--text); font-family: 'Segoe UI', sans-serif; overflow: hidden; display: flex; flex-direction: column; }
        
        /* TOOLBAR */
        #toolbar { height: 40px; background: #333; border-bottom: 1px solid #252526; display: flex; align-items: center; padding: 0 10px; gap: 8px; }
        .tool-btn { background: #444; color: #fff; border: 1px solid #555; padding: 5px 10px; font-size: 13px; cursor: pointer; border-radius: 3px; display: flex; align-items: center; gap: 6px; }
        .tool-btn:hover { background: #555; }
        .tool-btn.primary { background: var(--accent); border-color: var(--accent); }
        .tool-btn.magic { background: #6a1b9a; border-color: #8e24aa; } 
        .tool-btn.gen { background: #2e7d32; border-color: #43a047; } 

        /* LAYOUT */
        #workspace { flex: 1; display: flex; overflow: hidden; position: relative; }
        #sidebar { width: 220px; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
        
        /* EDITOR & TERMINAL */
        #center-area { flex: 1; display: flex; flex-direction: column; min-width: 0; position: relative; }
        #monaco-container { flex: 1; }
        #terminal-panel { height: 30%; background: #1e1e1e; border-top: 1px solid var(--accent); display: none; flex-direction: column; }
        
        /* CHAT SIDEBAR (RESIZABLE) */
        #chat-panel { width: 350px; background: #1f1f1f; border-left: 1px solid var(--border); display: none; flex-direction: column; transition: width 0.2s; }
        #chat-panel.active { display: flex; }
        #chat-panel.w-25 { width: 25vw; }
        #chat-panel.w-50 { width: 50vw; }
        
        /* ERROR GLYPH (RED CROSS) */
        .error-glyph { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='%23ff5555'%3E%3Cpath d='M8 0a8 8 0 100 16A8 8 0 008 0zm3.5 10.1l-1.4 1.4L8 9.4l-2.1 2.1-1.4-1.4L6.6 8 4.5 5.9l1.4-1.4L8 6.6l2.1-2.1 1.4 1.4L9.4 8l2.1 2.1z'/%3E%3C/svg%3E") no-repeat center center; background-size: contain; }
        
        /* AI JUMP HIGHLIGHT */
        .highlight-line { background: rgba(255, 0, 0, 0.3) !important; border-top: 1px solid #ff5555; border-bottom: 1px solid #ff5555; }

        /* CHAT UI */
        .chat-header { padding: 8px; background: #2d2d2d; border-bottom: 1px solid #3e3e42; display: flex; justify-content: space-between; align-items: center; }
        .chat-msgs { flex: 1; overflow-y: auto; padding: 10px; font-size: 13px; }
        .msg { margin-bottom: 8px; padding: 8px; border-radius: 4px; max-width: 90%; word-wrap: break-word; }
        .msg.user { background: #007acc; color: white; align-self: flex-end; margin-left: auto; }
        .msg.bot { background: #3e3e42; color: #ddd; }
        
        /* MODALS */
        .modal { position: absolute; top: 20%; left: 50%; transform: translateX(-50%); width: 400px; background: #252526; border: 1px solid var(--accent); z-index: 999; display: none; padding: 15px; box-shadow: 0 0 15px rgba(0,0,0,0.5); }
        
        /* FILE TREE */
        .t-item { padding: 2px 10px; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 13px; }
        .t-item:hover { background: #333; }
        .is-dir { color: #fff; font-weight: bold; }
        
        /* STATUS BAR */
        #statusbar { height: 22px; background: var(--accent); color: white; display: flex; align-items: center; padding: 0 10px; font-size: 12px; }
    </style>
</head>
<body>

<div id="toolbar">
    <div style="font-weight:bold; color:#fff; margin-right:10px;">{{ project }}</div>
    <button class="tool-btn" onclick="saveFile()"><i class="fas fa-save"></i> Save</button>
    <div style="width:1px; height:20px; background:#555;"></div>
    
    <button class="tool-btn gen" onclick="openGenModal()"><i class="fas fa-code"></i> AI Gen</button>
    <button class="tool-btn magic" onclick="explainCode()"><i class="fas fa-wand-magic-sparkles"></i> Explain</button>
    
    <div style="flex:1"></div>
    <span id="lint-badge" style="color:#aaa; font-size:12px; margin-right:10px;"><i class="fas fa-check"></i> OK</span>
    <button class="tool-btn primary" onclick="toggleTerminal()">Term</button>
    <button class="tool-btn" onclick="toggleChat()"><i class="fas fa-robot"></i> Chat</button>
</div>

<div id="workspace">
    <div id="sidebar">
        <!-- REPLACED HEADER (Line 110) -->
        <div style="padding:5px 10px; background:#333; font-weight:bold; font-size:12px; display:flex; justify-content:space-between; align-items:center;">
            <span>FILES</span>
            <div style="gap:10px; display:flex;">
                <i class="fas fa-file-medical" title="New File" style="cursor:pointer" onclick="createItem('file')"></i>
                <i class="fas fa-folder-plus" title="New Folder" style="cursor:pointer" onclick="createItem('dir')"></i>
                <i class="fas fa-sync" title="Refresh" style="cursor:pointer" onclick="refreshTree()"></i>
            </div>
        </div>

        <div id="file-tree" style="flex:1; overflow-y:auto;"></div>
    </div>


    <!-- MAIN EDITOR -->
    <div id="center-area">
        <div id="monaco-container"></div>
        <div id="terminal-panel"><div id="xterm-container" style="flex:1;"></div></div>

        <!-- GEN CODE MODAL -->
        <div id="gen-modal" class="modal">
            <h3 style="margin-top:0; color:white;">AI Code Gen</h3>
            <textarea id="gen-prompt" style="width:100%; height:80px; background:#1e1e1e; color:white; border:1px solid #444;" placeholder="Describe code..."></textarea>
            <div style="margin-top:10px; text-align:right;">
                <button onclick="document.getElementById('gen-modal').style.display='none'">Cancel</button>
                <button onclick="submitGenCode()" style="background:var(--accent); color:white; border:none; padding:5px 10px;">Generate</button>
            </div>
        </div>
        
        <!-- EXPLAIN MODAL -->
        <div id="explain-modal" class="modal" style="width: 60%; max-height: 80vh; overflow-y: auto;">
             <div style="display:flex; justify-content:space-between;"><h3 style="margin:0; color:white;">Explanation</h3> <button onclick="document.getElementById('explain-modal').style.display='none'">X</button></div>
             <div id="explain-content" style="white-space: pre-wrap; line-height: 1.5; margin-top:10px;"></div>
        </div>
    </div>

    <!-- CHAT SIDEBAR -->
    <div id="chat-panel">
        <div class="chat-header">
            <span>QGenie Chat</span>
            <div>
                <button title="Default" onclick="resizeChat('')" style="font-size:10px;">D</button>
                <button title="25%" onclick="resizeChat('w-25')" style="font-size:10px;">25%</button>
                <button title="50%" onclick="resizeChat('w-50')" style="font-size:10px;">50%</button>
                <i class="fas fa-times" onclick="toggleChat()" style="margin-left:5px; cursor:pointer;"></i>
            </div>
        </div>
        <div id="chat-msgs" class="chat-msgs">
            <div class="msg bot">Hi! Paste a build error, and I'll find the line for you.</div>
        </div>
        <div style="padding:10px; border-top:1px solid #3e3e42;">
            <input id="chat-input" style="width:100%; padding:5px; background:#333; border:1px solid #555; color:white;" placeholder="Type or paste error..." onkeyup="if(event.key==='Enter') sendChat()">
        </div>
    </div>
</div>

<div id="statusbar">
    <span id="status-msg">Ready</span>
    <span style="flex:1"></span>
    <span id="cursor-pos">Ln 1, Col 1</span>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/addons/fit/fit.min.js"></script>

<script>
    var project = "{{ project }}";
    var currentPath = {{ initial_file | tojson }};
    var currentDir = ""; // State for current directory
    var editor, term, lintTimer;

    require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }});
    require(['vs/editor/editor.main'], function () {
        editor = monaco.editor.create(document.getElementById('monaco-container'), {
            value: "// Select a file to edit",
            language: 'python',
            theme: 'vs-dark',
            automaticLayout: true,
            glyphMargin: true,
            minimap: { enabled: true }
        });

        if(currentPath && currentPath !== "None") loadFile(currentPath);
        refreshTree(); // Initial Load
        initTerminal();

        editor.onDidChangeCursorPosition(e => {
            document.getElementById('cursor-pos').innerText = "Ln " + e.position.lineNumber + ", Col " + e.position.column;
        });
        
        editor.onDidChangeModelContent(() => {
            clearTimeout(lintTimer);
            lintTimer = setTimeout(runLinter, 800);
        });
        
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
    });

    // --- CREATE FILE/FOLDER ---
    function createItem(type) {
        var name = prompt("Enter Name for new " + (type==='dir'?'Folder':'File') + ":");
        if(!name) return;
        
        // Handle path logic (append to currentDir)
        var newPath = (currentDir ? currentDir + '/' : '') + name;
        
        fetch('/editor/api/create', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ 
                project: project, 
                path: newPath, 
                type: type 
            })
        }).then(r => r.json()).then(d => {
            if(d.error) {
                alert("Error: " + d.error);
            } else {
                refreshTree(); // Reload tree to show new item
                if(type === 'file') loadFile(newPath); // Auto-open if file
            }
        });
    }

    // --- FIXED FILE EXPLORER ---
    function refreshTree(path) {
        if(path === undefined) path = currentDir;
        currentDir = path;
        
        var c = document.getElementById('file-tree');
        c.innerHTML = '<div style="padding:5px; color:#aaa;">Loading...</div>';

        fetch('/editor/api/tree?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path))
        .then(r=>r.json()).then(nodes => {
            c.innerHTML = "";
            
            // Add "Up" Button (..)
            if(path !== "") {
                var up = document.createElement('div');
                up.className = "t-item is-dir";
                up.innerHTML = '<i class="fas fa-level-up-alt"></i> ..';
                up.style.color = "#aaa";
                
                var parts = path.split('/');
                parts.pop();
                var parent = parts.join('/');
                up.onclick = () => refreshTree(parent);
                c.appendChild(up);
            }

            nodes.forEach(n => {
                var d = document.createElement('div');
                d.className = "t-item " + (n.type==='dir'?'is-dir':'');
                d.innerText = (n.type==='dir'?'📂 ':'📄 ') + n.name;
                
                // CLICK HANDLER: Directories drill down, Files open
                d.onclick = () => {
                    if(n.type === 'dir') {
                        refreshTree(n.path);
                    } else {
                        loadFile(n.path);
                    }
                };
                c.appendChild(d);
            });
        });
    }

    // --- FILE OPERATIONS ---
    function loadFile(path) {
        document.getElementById('status-msg').innerText = "Loading " + path + "...";
        fetch('/editor/api/read?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path))
        .then(r=>r.json()).then(d => {
            if(d.error) { alert("Error opening file:\\n" + d.error); return; }
            var ext = path.split('.').pop();
            var lang = 'plaintext';
            if(ext==='py') lang='python'; if(ext==='c'||ext==='cpp') lang='cpp';
            if(ext==='js') lang='javascript'; if(ext==='json') lang='json'; if(ext==='html') lang='html';
            
            editor.setModel(monaco.editor.createModel(d.content, lang));
            currentPath = path;
            document.getElementById('status-msg').innerText = "Opened: " + path;
            
            // Clear highlights
            if(window.hlDecorations) window.hlDecorations = editor.deltaDecorations(window.hlDecorations, []);
            runLinter();
        });
    }

    function saveFile() {
        if(!currentPath) return;
        fetch('/save_file', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ project:project, path:currentPath, content:editor.getValue() })
        }).then(r=>r.json()).then(d => { 
            if(d.error) alert("Save Failed: " + d.error);
            else { document.getElementById('status-msg').innerText = "Saved"; runLinter(); }
        });
    }

    // --- LINTER & AI FEATURES ---
    function runLinter() {
        if(!currentPath) return;
        var code = editor.getValue();
        fetch('/editor/api/lint', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ code: code, language: editor.getModel().getLanguageId(), project: project })
        }).then(r => r.json()).then(data => {
            var markers = [];
            data.errors.forEach(err => {
                markers.push({
                    severity: monaco.MarkerSeverity.Error,
                    startLineNumber: err.line, startColumn: 1, endLineNumber: err.line, endColumn: 1000,
                    message: err.message
                });
            });
            monaco.editor.setModelMarkers(editor.getModel(), 'owner', markers);
            
            var newDecorations = data.errors.map(err => {
                return {
                    range: new monaco.Range(err.line, 1, err.line, 1),
                    options: { isWholeLine: false, glyphMarginClassName: 'error-glyph', glyphMarginHoverMessage: { value: err.message } }
                };
            });
            window.lintDecorations = editor.deltaDecorations(window.lintDecorations||[], newDecorations);

            var badge = document.getElementById('lint-badge');
            if(data.errors.length > 0) {
                badge.innerHTML = '<i class="fas fa-times-circle" style="color:#ff5555"></i> ' + data.errors.length;
                badge.style.color = "#ff5555";
            } else {
                badge.innerHTML = '<i class="fas fa-check-circle" style="color:#55ff55"></i> OK';
                badge.style.color = "#aaa";
            }
        });
    }

    function toggleChat() { document.getElementById('chat-panel').classList.toggle('active'); editor.layout(); }
    function resizeChat(c) { document.getElementById('chat-panel').className = 'active ' + c; editor.layout(); }
    
    function sendChat() {
        var i = document.getElementById('chat-input');
        var txt = i.value; if(!txt) return;
        var b = document.getElementById('chat-msgs');
        b.innerHTML += '<div class="msg user">'+txt+'</div>';
        i.value = '';
        
        fetch('/editor/api/chat_context', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ project:project, message:txt, code_context:editor.getValue(), current_file:currentPath })
        }).then(r=>r.json()).then(d=>{
            var resp = d.response;
            var jumpLine = null;
            var match = resp.match(/<<<JUMP:(\d+)>>>/);
            if(match) { jumpLine = parseInt(match[1]); resp = resp.replace(match[0], ''); }
            
            b.innerHTML += '<div class="msg bot">'+resp+'</div>';
            b.scrollTop = b.scrollHeight;
            
            if(jumpLine) {
                editor.revealLineInCenter(jumpLine);
                editor.setPosition({lineNumber: jumpLine, column: 1});
                var dec = { range: new monaco.Range(jumpLine,1,jumpLine,1), options: { isWholeLine:true, className:'highlight-line' } };
                window.hlDecorations = editor.deltaDecorations(window.hlDecorations||[], [dec]);
            }
        });
    }

    // --- UTILS ---
    function openGenModal() { document.getElementById('gen-modal').style.display='block'; }
    function submitGenCode() {
        var p = document.getElementById('gen-prompt').value;
        if(!p) return;
        fetch('/editor/api/ai_gen', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ prompt:p, language: editor.getModel().getLanguageId() })
        }).then(r=>r.json()).then(d=>{
            if(d.code) {
                editor.executeEdits("ai", [{ range: editor.getSelection(), text: d.code }]);
                document.getElementById('gen-modal').style.display='none';
            } else alert(d.error);
        });
    }

    function explainCode() {
        var code = editor.getModel().getValueInRange(editor.getSelection()) || editor.getValue();
        if(!code.trim()) return alert("Select code first");
        document.getElementById('explain-modal').style.display='block';
        document.getElementById('explain-content').innerText = "Thinking...";
        fetch('/editor/api/explain', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ code:code })
        }).then(r=>r.json()).then(d=> document.getElementById('explain-content').innerText = d.explanation);
    }

    function initTerminal() {
        Terminal.applyAddon(fit);
        term = new Terminal({ fontSize: 13, theme: { background: '#1e1e1e' } });
        term.open(document.getElementById('xterm-container'));
        term.fit();
        term.write('$ ');
        var cmd="";
        term.on('data', k=>{
            if(k.charCodeAt(0)===13){
                term.write('\\r\\n');
                if(cmd.trim()){
                     fetch('/editor/api/term', {
                        method:'POST',headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({project:project, cmd:cmd.trim()})
                     }).then(r=>r.json()).then(d=>{
                         term.write(d.output.replace(/\\n/g,'\\r\\n'));
                         term.write('\\r\\n$ ');
                     });
                } else term.write('$ ');
                cmd="";
            } else if(k.charCodeAt(0)===127){
                if(cmd.length>0){ cmd=cmd.slice(0,-1); term.write('\\b \\b'); }
            } else { cmd+=k; term.write(k); }
        });
    }
    function toggleTerminal() { document.getElementById('terminal-panel').classList.toggle('open'); setTimeout(()=>term.fit(), 200); }
</script>
</body>
</html>
"""

# --- BACKEND ---
@editor_bp.route('/editor/view/<project>/')
@editor_bp.route('/editor/view/<project>/<path:filepath>')
def open_editor(project, filepath=""):
    return render_template_string(IDE_HTML, project=project, initial_file=filepath if filepath else "None")

@editor_bp.route('/editor/api/read')
def read_file():
    project = request.args.get('project')
    path = request.args.get('path')
    root, _ = get_config_safe(project)
    if not root: return jsonify({'error': 'Project not found'})
    abs_path = os.path.join(root, path)
    if not os.path.exists(abs_path): return jsonify({'error': f"File not found: {path}"})
    if os.path.isdir(abs_path): return jsonify({'error': "Cannot open directory"})
    try:
        with open(abs_path, 'r', errors='replace') as f: return jsonify({'content': f.read()})
    except Exception as e: return jsonify({'error': str(e)})

@editor_bp.route('/editor/api/chat_context', methods=['POST'])
def chat_context():
    if not AI_AVAILABLE: return jsonify({'response': "AI unavailable"})
    d = request.json
    try:
        from qgenie import QGenieClient, ChatMessage
        sys_prompt = (
            f"You are a coding assistant. Context File: {d.get('current_file')}\n"
            f"Code Content (Partial):\n{d.get('code_context')[:5000]}\n\n"
            "Task: Answer the user's question or analyze the error.\n"
            "IMPORTANT: If the user provides an error or asks about a bug, and you can identify "
            "the specific line number in the provided code that causes it, append exactly "
            "'<<<JUMP:123>>>' (replace 123 with the line number) to the end of your response."
        )
        r = QGenieClient().chat(messages=[
            ChatMessage(role="system", content=sys_prompt),
            ChatMessage(role="user", content=d.get('message'))
        ])
        return jsonify({'response': r.first_content})
    except Exception as e: return jsonify({'response': str(e)})

@editor_bp.route('/editor/api/lint', methods=['POST'])
def lint():
    data = request.json
    code = data.get('code', '')
    lang = data.get('language', '')
    errors = []
    if not code.strip(): return jsonify({'errors':[]})

    # STRICT PYTHON LINTER
    if lang == 'python':
        try:
            tree = ast.parse(code)
            defined = set(dir(builtins))
            defined.add('self')
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)): defined.add(node.name)
                elif isinstance(node, ast.Import):
                    for n in node.names: defined.add(n.asname or n.name)
                elif isinstance(node, ast.ImportFrom):
                    for n in node.names: defined.add(n.asname or n.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store): defined.add(node.id)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id not in defined: errors.append({'line': node.lineno, 'message': f"Undefined name '{node.id}'"})
        except SyntaxError as e: errors.append({'line': e.lineno, 'message': f"Syntax: {e.msg}"})
            
    # STRICT C/CPP LINTER
    elif lang in ['c', 'cpp']:
        with tempfile.NamedTemporaryFile(suffix=".c", mode='w') as t:
            t.write(code)
            t.flush()
            try:
                res = subprocess.run(
                    ['gcc', '-fsyntax-only', '-Werror=implicit', '-x', 'c', t.name], 
                    capture_output=True, text=True
                )
                if res.returncode != 0:
                    for line in res.stderr.splitlines():
                        if ': error:' in line:
                            parts = line.split(':')
                            try: errors.append({'line': int(parts[1]), 'message': parts[-1].strip()})
                            except: pass
            except: pass
            
    return jsonify({'errors': errors})

@editor_bp.route('/save_file', methods=['POST'])
def save_file():
    d=request.json
    r,_=get_config_safe(d['project'])
    try:
        with open(os.path.join(r, d['path']), 'w') as f: f.write(d['content'])
        return jsonify({'status':'ok'})
    except Exception as e: return jsonify({'error':str(e)})

@editor_bp.route('/editor/api/tree')
def get_tree():
    p = request.args.get('project')
    path = request.args.get('path', '')
    r, _ = get_config_safe(p)
    if not r: return jsonify([])
    t = os.path.join(r, path)
    if not os.path.exists(t): return jsonify([])
    n = []
    for i in os.listdir(t):
        if i.startswith('.'): continue
        fp = os.path.join(t, i)
        n.append({'name':i, 'path':os.path.join(path, i), 'type':'dir' if os.path.isdir(fp) else 'file'})
    n.sort(key=lambda x:(x['type']!='dir', x['name']))
    return jsonify(n)

@editor_bp.route('/editor/api/ai_gen', methods=['POST'])
def ai_gen():
    if not AI_AVAILABLE: return jsonify({'error':"AI unavailable"})
    try:
        from qgenie import QGenieClient, ChatMessage
        r = QGenieClient().chat(messages=[ChatMessage(role="user", content=f"Write {request.json.get('language')} code: {request.json.get('prompt')}. Only code.")])
        return jsonify({'code': r.first_content.replace('```python','').replace('```','').strip()})
    except Exception as e: return jsonify({'error':str(e)})

@editor_bp.route('/editor/api/explain', methods=['POST'])
def explain():
    if not AI_AVAILABLE: return jsonify({'explanation':"AI unavailable"})
    try:
        from qgenie import QGenieClient, ChatMessage
        r = QGenieClient().chat(messages=[
            ChatMessage(role="system", content="Explain code."),
            ChatMessage(role="user", content=request.json.get('code',''))
        ])
        return jsonify({'explanation': r.first_content})
    except Exception as e: return jsonify({'explanation':str(e)})

@editor_bp.route('/editor/api/term', methods=['POST'])
def term():
    d=request.json
    r,_=get_config_safe(d['project'])
    try:
        o = subprocess.check_output(d['cmd'], shell=True, cwd=r, stderr=subprocess.STDOUT, text=True)
        return jsonify({'output': o})
    except Exception as e: return jsonify({'output': str(e)})

@editor_bp.route('/editor/api/create', methods=['POST'])
def create_item():
    d = request.json
    project = d.get('project')
    rel_path = d.get('path')  # Relative path including new name
    item_type = d.get('type') # 'file' or 'dir'
    
    root, _ = get_config_safe(project)
    if not root: return jsonify({'error': 'Project not found'})
    
    # Secure the path
    abs_path = os.path.abspath(os.path.join(root, rel_path))
    if not abs_path.startswith(root):
        return jsonify({'error': 'Invalid path security'})

    try:
        if item_type == 'dir':
            os.makedirs(abs_path, exist_ok=True)
        else:
            # Create empty file if it doesn't exist
            if not os.path.exists(abs_path):
                with open(abs_path, 'w') as f: 
                    pass 
    except Exception as e:
        return jsonify({'error': str(e)})
        
    return jsonify({'status': 'ok'})
