import os
import subprocess
import json
import sys
import threading
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
<html>
<head>
    <meta charset="utf-8"/>
    <title>Pro Editor AI - {{ project }}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.css" rel="stylesheet"/>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/addons/fit/fit.min.js"></script>
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
        
        /* LAYOUT */
        #workspace { flex: 1; display: flex; overflow: hidden; position: relative; }
        #sidebar { width: 220px; background: var(--bg-panel); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
        
        /* EDITOR & TERMINAL */
        #center-area { flex: 1; display: flex; flex-direction: column; min-width: 0; position: relative; }
        #monaco-container { flex: 1; }
        #terminal-panel { height: 30%; background: #1e1e1e; border-top: 1px solid var(--accent); display: none; flex-direction: column; }
        #xterm-container { flex: 1; overflow: hidden; }

        /* CHAT SIDEBAR */
        #chat-panel { width: 350px; background: #202124; border-left: 1px solid var(--border); display: none; flex-direction: column; transition: width 0.3s ease; }
        #chat-panel.active { display: flex; }
        #chat-panel.wide { width: 50%; }
        #chat-panel.full { width: 75%; }
        
        .chat-header { padding: 10px 15px; background: #2d2d2d; border-bottom: 1px solid #3e3e42; display: flex; justify-content: space-between; align-items: center; }
        .chat-msgs { flex: 1; overflow-y: auto; padding: 15px; display: flex; flex-direction: column; gap: 15px; background: #1e1e1e; }
        
        .msg-row { display: flex; gap: 10px; max-width: 100%; }
        .msg-row.user { justify-content: flex-end; }
        .avatar { width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; }
        .avatar.bot { background: #e65100; color: white; }
        .avatar.user { background: #1976d2; color: white; }
        .msg-bubble { padding: 10px 14px; border-radius: 12px; font-size: 13px; line-height: 1.4; max-width: 80%; word-wrap: break-word; }
        .msg-row.bot .msg-bubble { background: #333; color: #e0e0e0; border-top-left-radius: 2px; }
        .msg-row.user .msg-bubble { background: linear-gradient(135deg, #007acc, #005f9e); color: white; border-top-right-radius: 2px; }
        
        /* MODALS */
        .modal { position: absolute; top: 10%; left: 50%; transform: translateX(-50%); background: #252526; border: 1px solid var(--accent); z-index: 999; display: none; padding: 15px; box-shadow: 0 0 15px rgba(0,0,0,0.5); flex-direction: column; }
        
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
    <button class="tool-btn" onclick="toggleGit()" style="background:#f4511e; border-color:#d84315;"><i class="fab fa-git-alt"></i> Git</button>
    <div style="width:1px; height:20px; background:#555;"></div>
    <button class="tool-btn gen" onclick="openGenModal()"><i class="fas fa-code"></i> AI Gen</button>
    <button class="tool-btn magic" onclick="explainCode()"><i class="fas fa-wand-magic-sparkles"></i> Explain</button>
    <div style="flex:1"></div>
    <button class="tool-btn primary" onclick="toggleTerminal()">Term</button>
    <button class="tool-btn" onclick="toggleChat()"><i class="fas fa-robot"></i> Chat</button>
</div>

<div id="workspace">
    <div id="sidebar">
        <div style="padding:5px 10px; background:#333; font-weight:bold; font-size:12px; display:flex; justify-content:space-between; align-items:center;">
            <span>FILES</span>
            <div style="gap:5px; display:flex;">
                <i class="fas fa-file-medical" onclick="createItem('file')" style="cursor:pointer" title="New File"></i>
                <i class="fas fa-folder-plus" onclick="createItem('dir')" style="cursor:pointer" title="New Folder"></i>
                <i class="fas fa-sync" onclick="refreshTree()" style="cursor:pointer" title="Refresh"></i>
            </div>
        </div>
        <div id="file-tree" style="flex:1; overflow-y:auto;"></div>
    </div>

    <!-- MAIN EDITOR AREA -->
    <div id="center-area">
        <div id="monaco-container"></div>
        <div id="terminal-panel"><div id="xterm-container"></div></div>

        <!-- GIT MODAL -->
        <div class="modal" id="git-modal" style="width: 900px; height: 700px;">
            <div style="display:flex; justify-content:space-between; border-bottom:1px solid #444; padding-bottom:10px; margin-bottom:10px;">
                <h3 style="margin:0; color:white;"><i class="fab fa-git-alt" style="color:#f4511e"></i> Source Control</h3>
                <div>
                    <button class="tool-btn" onclick="configureGit()" style="display:inline-flex; padding: 2px 8px; margin-right:5px; font-size:11px;">⚙️ Setup</button>
                    <button onclick="document.getElementById('git-modal').style.display='none'" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:16px;">X</button>
                </div>
            </div>
            
            <div style="flex:1; display:flex; gap:10px; overflow:hidden;"> 
                <div style="width: 200px; display:flex; flex-direction:column; gap:8px;">
                    <div style="font-size:11px; font-weight:bold; color:#888;">STAGING</div>
                    <button class="tool-btn" onclick="runGit('add .')">➕ Add All</button>
                    <button class="tool-btn" onclick="runGit('restore --staged .')">➖ Reset All</button>
                    
                    <div style="font-size:11px; font-weight:bold; color:#888; margin-top:10px;">COMMIT</div>
                    <input id="commit-msg" placeholder="Message..." style="width:100%; padding:4px; background:#333; border:1px solid #555; color:white; font-size:12px;"/>
                    <button class="tool-btn primary" onclick="gitCommit()">Commit</button>
                    
                    <div style="font-size:11px; font-weight:bold; color:#888; margin-top:10px;">TOOLS</div>
                    <button class="tool-btn" onclick="runGit('log --oneline --graph --decorate -n 50')">📜 History</button>
                    <button class="tool-btn" onclick="runGit('format-patch -1 HEAD')">📤 Create Patch</button>
                    
                    <!-- NEW PATCH UPLOAD -->
                    <input type="file" id="patch-upload" style="display:none;" onchange="uploadPatch(this)"/>
                    <button class="tool-btn" onclick="document.getElementById('patch-upload').click()">📥 Apply Patch (PC)</button>
                </div>
                
                <div style="flex:1; display:flex; flex-direction:column; min-width:0;">
                    <pre id="git-output" style="background:#111; color:#ddd; font-family:monospace; font-size:12px; flex:1; padding:10px; overflow-y:auto; border:1px solid #444; margin:0; white-space:pre-wrap;">// Git Output...</pre>
                    <div style="display:flex; margin-top:10px; gap:5px;">
                        <span style="padding:5px; background:#333; color:#aaa; font-family:monospace;">git</span>
                        <input id="custom-git" onkeyup="if(event.key==='Enter') runCustomGit()" placeholder="status" style="flex:1; background:#222; border:1px solid #444; color:white; padding:5px; font-family:monospace;"/>
                        <button class="tool-btn" onclick="runCustomGit()">Run</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- AI GEN & EXPLAIN MODALS (Hidden for brevity, same as before) -->
        <div class="modal" id="gen-modal" style="width:400px; height:200px;">
            <h3 style="margin-top:0; color:white;">AI Code Gen</h3>
            <textarea id="gen-prompt" placeholder="Describe code..." style="width:100%; height:80px; background:#1e1e1e; color:white; border:1px solid #444;"></textarea>
            <div style="margin-top:10px; text-align:right;">
                <button class="tool-btn" onclick="document.getElementById('gen-modal').style.display='none'">Cancel</button>
                <button class="tool-btn primary" onclick="submitGenCode()">Generate</button>
            </div>
        </div>
        <div class="modal" id="explain-modal" style="width: 60%; max-height: 80vh; overflow-y: auto;">
             <div style="display:flex; justify-content:space-between;"><h3 style="margin:0; color:white;">Explanation</h3> <button onclick="document.getElementById('explain-modal').style.display='none'" style="cursor:pointer; background:none; border:none; color:white;">X</button></div>
             <div id="explain-content" style="white-space: pre-wrap; line-height: 1.5; margin-top:10px;"></div>
        </div>
    </div>

    <!-- CHAT SIDEBAR -->
    <div id="chat-panel">
        <div class="chat-header">
            <span><i class="fas fa-robot"></i> QGenie Chat</span>
            <div style="display:flex; gap:10px;">
                <i class="fas fa-arrows-alt-h" onclick="toggleChatWidth()" title="Expand" style="cursor:pointer; color:#aaa;"></i>
                <i class="fas fa-times" onclick="toggleChat()" style="cursor:pointer; color:#aaa;"></i>
            </div>
        </div>
        <div class="chat-msgs" id="chat-msgs">
            <div class="msg-row bot"><div class="avatar bot"><i class="fas fa-robot"></i></div><div class="msg-bubble">Hi! Paste a build error, and I'll find the line for you.</div></div>
        </div>
        <div style="padding:15px; background:#2d2d2d; border-top:1px solid #3e3e42;">
            <div style="display:flex; gap:5px;">
                <input id="chat-input" onkeyup="if(event.key==='Enter') sendChat()" placeholder="Type or paste error..." style="flex:1; padding:8px; background:#333; border:1px solid #555; color:white; border-radius:4px;"/>
                <button onclick="sendChat()" style="background:var(--accent); border:none; color:white; width:35px; border-radius:4px;"><i class="fas fa-paper-plane"></i></button>
            </div>
        </div>
    </div>
</div>

<div id="statusbar">
    <span id="status-msg">Ready</span>
    <span style="flex:1"></span>
    <span id="cursor-pos">Ln 1, Col 1</span>
</div>

<script>
    var project = "{{ project }}";
    var currentPath = {{ initial_file | tojson }};
    var currentDir = ""; 
    var editor, term;

    // --- MONACO SETUP ---
    require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }});
    require(['vs/editor/editor.main'], function () {
        editor = monaco.editor.create(document.getElementById('monaco-container'), {
            value: "// Select a file to edit",
            language: 'python', theme: 'vs-dark', automaticLayout: true
        });
        if(currentPath && currentPath !== "None") loadFile(currentPath);
        refreshTree(); initTerminal(); 
        editor.onDidChangeCursorPosition(e => document.getElementById('cursor-pos').innerText = "Ln " + e.position.lineNumber + ", Col " + e.position.column);
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
    });

    // --- FILE OPERATIONS ---
    function refreshTree(path) {
        if(path === undefined) path = currentDir;
        currentDir = path;
        fetch('/editor/api/tree?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path))
        .then(r=>r.json()).then(nodes => {
            var c = document.getElementById('file-tree'); c.innerHTML = "";
            if(path !== "") {
                var up = document.createElement('div'); up.className = "t-item is-dir"; up.innerHTML = '<i class="fas fa-level-up-alt"></i> ..';
                up.style.color = "#aaa"; up.onclick = () => refreshTree(path.split('/').slice(0,-1).join('/')); c.appendChild(up);
            }
            nodes.forEach(n => {
                var d = document.createElement('div'); d.className = "t-item " + (n.type==='dir'?'is-dir':'');
                d.innerText = (n.type==='dir'?'📂 ':'📄 ') + n.name;
                d.onclick = () => { n.type === 'dir' ? refreshTree(n.path) : loadFile(n.path); };
                c.appendChild(d);
            });
        });
    }

    function createItem(type) {
        var name = prompt("Name:"); if(!name) return;
        var newPath = (currentDir ? currentDir + '/' : '') + name;
        fetch('/editor/api/create', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ project: project, path: newPath, type: type })})
        .then(r => r.json()).then(d => { if(!d.error) { refreshTree(); if(type==='file') loadFile(newPath); } });
    }

    function loadFile(path) {
        document.getElementById('status-msg').innerText = "Loading " + path + "...";
        fetch('/editor/api/read?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path))
        .then(r=>r.json()).then(d => {
            if(d.error) { alert(d.error); return; }
            var ext = path.split('.').pop();
            editor.setModel(monaco.editor.createModel(d.content, ext==='py'?'python':(ext==='js'?'javascript':'plaintext')));
            currentPath = path; document.getElementById('status-msg').innerText = "Opened: " + path;
        });
    }

    function saveFile() {
        if(!currentPath) return;
        fetch('/save_file', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ project:project, path:currentPath, content:editor.getValue() })})
        .then(r=>r.json()).then(d => document.getElementById('status-msg').innerText = d.error ? "Error: "+d.error : "Saved");
    }

    // --- GIT FUNCTIONS ---
    function toggleGit() {
        var m = document.getElementById('git-modal');
        if (m.style.display === 'none') { m.style.display = 'flex'; runGit('status'); } else { m.style.display = 'none'; }
    }

    function configureGit() {
        // Fetch existing identity first
        fetch('/editor/api/git/identity?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(currentPath||""))
        .then(r => r.json())
        .then(d => {
            var name = prompt("Git Username:", d.name || "");
            if (name === null) return;
            var email = prompt("Git Email:", d.email || "");
            if (email === null) return;
            
            runGit(`config --global user.name "${name}"`);
            setTimeout(() => runGit(`config --global user.email "${email}"`), 500);
            setTimeout(() => runGit(`config --global --add safe.directory "*"`), 1000);
        });
    }

    function uploadPatch(input) {
        var file = input.files[0];
        if(!file) return;
        
        var out = document.getElementById('git-output');
        out.innerText += "\\n\\n$ Uploading & applying " + file.name + "...";

        var fd = new FormData();
        fd.append('patch', file);
        fd.append('project', project);
        fd.append('path', currentPath || "");

        fetch('/editor/api/git/apply_patch', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(d => {
            out.innerText += "\\n" + (d.output || d.error);
            out.scrollTop = out.scrollHeight;
            input.value = ""; // Reset for next use
        });
    }

    function runGit(args) {
        var out = document.getElementById('git-output');
        out.innerText += `\\n\\n$ git ${args} ...`;
        fetch('/editor/api/term', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ project: project, cmd: 'git ' + args, path: currentPath||currentDir })})
        .then(r => r.json()).then(d => {
            out.innerText += "\\n" + (d.output.trim() || "Done.");
            out.scrollTop = out.scrollHeight;
            if(args.includes('format-patch')) refreshTree();
        });
    }

    function gitCommit() {
        var msg = document.getElementById('commit-msg').value;
        if (!msg) { alert("Enter a commit message"); return; }
        runGit('commit -m "' + msg + '"');
        document.getElementById('commit-msg').value = '';
    }

    function runCustomGit() {
        var cmd = document.getElementById('custom-git').value;
        if (cmd) { runGit(cmd); document.getElementById('custom-git').value = ''; }
    }

    // --- OTHER ---
    function toggleChat() { document.getElementById('chat-panel').classList.toggle('active'); if(editor) editor.layout(); }
    function toggleChatWidth() {
        var p = document.getElementById('chat-panel');
        if(p.classList.contains('wide')) { p.classList.remove('wide'); p.classList.add('full'); }
        else if(p.classList.contains('full')) { p.classList.remove('full'); }
        else { p.classList.add('wide'); }
        if(editor) setTimeout(() => editor.layout(), 300);
    }

    function openGenModal() { document.getElementById('gen-modal').style.display='block'; }
    function submitGenCode() {
        fetch('/editor/api/ai_gen', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ prompt:document.getElementById('gen-prompt').value, language: editor.getModel().getLanguageId() })})
        .then(r=>r.json()).then(d=>{ if(d.code) { editor.executeEdits("ai", [{ range: editor.getSelection(), text: d.code }]); document.getElementById('gen-modal').style.display='none'; }});
    }

    function explainCode() {
        document.getElementById('explain-modal').style.display='block'; document.getElementById('explain-content').innerText = "Thinking...";
        fetch('/editor/api/explain', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ code:editor.getModel().getValueInRange(editor.getSelection()) || editor.getValue() })})
        .then(r=>r.json()).then(d=> document.getElementById('explain-content').innerText = d.explanation);
    }
    
    function sendChat() {
        var i = document.getElementById('chat-input'), txt = i.value; if(!txt) return;
        var b = document.getElementById('chat-msgs');
        b.innerHTML += '<div class="msg-row user"><div class="msg-bubble">'+txt+'</div><div class="avatar user"><i class="fas fa-user"></i></div></div>';
        i.value = ''; b.scrollTop = b.scrollHeight;
        fetch('/editor/api/chat_context', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ project:project, message:txt, code_context:editor.getValue(), current_file:currentPath })})
        .then(r=>r.json()).then(d=>{
            b.innerHTML += '<div class="msg-row bot"><div class="avatar bot"><i class="fas fa-robot"></i></div><div class="msg-bubble">'+d.response+'</div></div>';
            b.scrollTop = b.scrollHeight;
        });
    }

    function initTerminal() {
        try {
            Terminal.applyAddon(fit); term = new Terminal({ fontSize: 13, theme: { background: '#1e1e1e' } });
            term.open(document.getElementById('xterm-container')); term.fit(); term.write('$ ');
            var cmd="";
            term.on('data', k=>{
                if(k.charCodeAt(0)===13){ term.write('\\r\\n'); if(cmd.trim()) runTerm(cmd.trim()); else term.write('$ '); cmd=""; }
                else if(k.charCodeAt(0)===127){ if(cmd.length>0){ cmd=cmd.slice(0,-1); term.write('\\b \\b'); } }
                else { cmd+=k; term.write(k); }
            });
        } catch(e) {}
    }
    function runTerm(c) {
        fetch('/editor/api/term', { method:'POST',headers:{'Content-Type':'application/json'}, body:JSON.stringify({project:project, cmd:c})})
        .then(r=>r.json()).then(d=>{ term.write(d.output.replace(/\\n/g,'\\r\\n')); term.write('\\r\\n$ '); });
    }
    function toggleTerminal() { 
        var t = document.getElementById('terminal-panel'); t.style.display = (t.style.display === 'flex' ? 'none' : 'flex'); if(t.style.display === 'flex' && term) term.fit();
    }
</script>
</body>
</html>
"""

# --- SHARED GIT HELPERS ---
def find_git_root(start_path):
    path = os.path.abspath(start_path)
    while path != '/':
        if os.path.isdir(os.path.join(path, '.git')): return path
        path = os.path.dirname(path)
    return None

def get_cwd_context(project, path_arg):
    r, _ = get_config_safe(project)
    if not r: return None
    cwd = r
    if path_arg:
        full = os.path.join(r, path_arg)
        if os.path.isfile(full): cwd = os.path.dirname(full)
        elif os.path.isdir(full): cwd = full
    # Git Root Detect
    g = find_git_root(cwd)
    return g if g else cwd

@editor_bp.route('/editor/view/<project>/')
@editor_bp.route('/editor/view/<project>/<path:filepath>')
def open_editor(project, filepath=""): return render_template_string(IDE_HTML, project=project, initial_file=filepath if filepath else "None")

@editor_bp.route('/editor/api/read')
def read_file():
    p, path = request.args.get('project'), request.args.get('path')
    r, _ = get_config_safe(p)
    try:
        with open(os.path.join(r, path), 'r', errors='replace') as f: return jsonify({'content': f.read()})
    except Exception as e: return jsonify({'error': str(e)})

@editor_bp.route('/editor/api/chat_context', methods=['POST'])
def chat_context():
    if not AI_AVAILABLE: return jsonify({'response': "AI unavailable"})
    d = request.json
    try:
        from qgenie import QGenieClient, ChatMessage
        r = QGenieClient().chat(messages=[
            ChatMessage(role="system", content=f"Ctx: {d.get('current_file')}"),
            ChatMessage(role="user", content=d.get('message'))
        ])
        return jsonify({'response': r.first_content})
    except Exception as e: return jsonify({'response': str(e)})

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
    p, path = request.args.get('project'), request.args.get('path', '')
    r, _ = get_config_safe(p)
    t = os.path.join(r, path)
    if not os.path.exists(t): return jsonify([])
    n = []
    for i in os.listdir(t):
        if i.startswith('.'): continue
        fp = os.path.join(t, i)
        n.append({'name':i, 'path':os.path.join(path, i), 'type':'dir' if os.path.isdir(fp) else 'file'})
    n.sort(key=lambda x:(x['type']!='dir', x['name']))
    return jsonify(n)

@editor_bp.route('/editor/api/term', methods=['POST'])
def term():
    d=request.json
    cwd = get_cwd_context(d['project'], d.get('path'))
    try:
        env = os.environ.copy(); env['GIT_PAGER'] = 'cat'
        res = subprocess.run(d['cmd'], shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        return jsonify({'output': res.stdout.decode('utf-8', errors='replace')})
    except Exception as e: return jsonify({'output': f"Error: {str(e)}"})

# --- NEW GIT ENDPOINTS ---
@editor_bp.route('/editor/api/git/identity')
def git_identity():
    cwd = get_cwd_context(request.args.get('project'), request.args.get('path'))
    name = subprocess.run('git config user.name', shell=True, cwd=cwd, stdout=subprocess.PIPE).stdout.decode().strip()
    email = subprocess.run('git config user.email', shell=True, cwd=cwd, stdout=subprocess.PIPE).stdout.decode().strip()
    return jsonify({'name': name, 'email': email})

@editor_bp.route('/editor/api/git/apply_patch', methods=['POST'])
def apply_patch():
    try:
        if 'patch' not in request.files: return jsonify({'error': 'No file part'})
        f = request.files['patch']
        if f.filename == '': return jsonify({'error': 'No selected file'})
        
        cwd = get_cwd_context(request.form.get('project'), request.form.get('path'))
        patch_path = os.path.join(cwd, f.filename)
        f.save(patch_path)
        
        res = subprocess.run(f'git apply "{f.filename}"', shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        os.remove(patch_path) # Cleanup
        
        out = res.stdout.decode('utf-8', errors='replace')
        return jsonify({'output': out if out.strip() else "Patch applied successfully."})
    except Exception as e:
        return jsonify({'error': str(e)})

@editor_bp.route('/editor/api/create', methods=['POST'])
def create_item():
    d = request.json
    r, _ = get_config_safe(d['project'])
    abs_path = os.path.abspath(os.path.join(r, d['path']))
    if not abs_path.startswith(r): return jsonify({'error': 'Invalid path'})
    try:
        if d['type'] == 'dir': os.makedirs(abs_path, exist_ok=True)
        else: open(abs_path, 'w').close()
    except Exception as e: return jsonify({'error': str(e)})
    return jsonify({'status': 'ok'})

# --- AI GEN ENDPOINTS (Abbreviated for space, assume same functionality) ---
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
        r = QGenieClient().chat(messages=[ChatMessage(role="user", content=f"Explain: {request.json.get('code')}")])
        return jsonify({'explanation': r.first_content})
    except Exception as e: return jsonify({'explanation':str(e)})
