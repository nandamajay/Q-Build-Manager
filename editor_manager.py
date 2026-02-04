import os
import subprocess
import json
import sys
import threading
import tempfile
from flask import Blueprint, render_template_string, request, jsonify

# --- SAFE IMPORT HELPER ---
# Prevents crashes if web_manager isn't ready when this module loads
def get_config_safe(project_name):
    """Safely retrieves project configuration from web_manager."""
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

# --- GLOBAL CHAT HISTORY ---
# Stores conversation history in memory: { 'project_name': [messages...] }
chat_histories = {}

# --- GIT HELPER FUNCTIONS ---
def find_git_root(start_path):
    """
    Recursively searches upwards for a .git directory OR file.
    Note: In submodules/worktrees, .git is a file, not a directory.
    """
    if not start_path or not os.path.exists(start_path):
        return None
    
    path = os.path.abspath(start_path)
    
    # Safety check to prevent infinite loops (stop at root)
    while path != os.path.dirname(path): 
        git_check = os.path.join(path, '.git')
        # FIX: Check if it exists (file OR dir), not just isdir
        if os.path.exists(git_check): 
            return path
        path = os.path.dirname(path)
    
    return None

def get_cwd_context(project, path_arg):
    """
    Determines the best working directory for Git commands to avoid Exit 128.
    """
    project_root, _ = get_config_safe(project)
    if not project_root:
        return os.getcwd()

    # Determine start point for search (File location or Project Root)
    search_start = project_root
    if path_arg:
        full_path = os.path.join(project_root, path_arg)
        if os.path.exists(full_path):
            search_start = os.path.dirname(full_path) if os.path.isfile(full_path) else full_path

    # Search for Git Root (.git folder/file)
    git_root = find_git_root(search_start)
    
    # If not found near file, try project root
    if not git_root:
        git_root = find_git_root(project_root)

    # Return git root if found, else project root
    return git_root if git_root else project_root

# --- HTML TEMPLATE ---
IDE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8"/>
    <title>Pro Editor AI - {{ project }}</title>
    
    <!-- CODE EDITOR LIB (Monaco) -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js"></script>
    
    <!-- TERMINAL LIB (XTerm) -->
    <link href="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.css" rel="stylesheet"/>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/xterm.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/xterm/3.14.5/addons/fit/fit.min.js"></script>
    
    <!-- ICONS -->
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet"/>
    
    <!-- MARKDOWN LIB (Optional - with fallback) -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/4.3.0/marked.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    
    <style>
        :root { 
            --bg-dark: #1e1e1e; 
            --bg-panel: #252526; 
            --accent: #007acc; 
            --text: #cccccc; 
            --border: #3e3e42; 
        }
        * { box-sizing: border-box; }
        body { 
            height: 100vh; margin: 0; 
            background-color: var(--bg-dark); color: var(--text); 
            font-family: 'Segoe UI', sans-serif; 
            overflow: hidden; display: flex; flex-direction: column; 
        }
        
        /* TOOLBAR */
        #toolbar { 
            height: 40px; background: #333; border-bottom: 1px solid #252526; 
            display: flex; align-items: center; padding: 0 10px; gap: 8px; 
        }
        .tool-btn { 
            background: #444; color: #fff; border: 1px solid #555; 
            padding: 5px 10px; font-size: 13px; cursor: pointer; 
            border-radius: 3px; display: flex; align-items: center; gap: 6px; 
        }
        .tool-btn:hover { background: #555; }
        .tool-btn.primary { background: var(--accent); border-color: var(--accent); }
        .tool-btn.magic { background: #6a1b9a; border-color: #8e24aa; } 
        .tool-btn.gen { background: #2e7d32; border-color: #43a047; }
        
        /* LAYOUT */
        #workspace { flex: 1; display: flex; overflow: hidden; position: relative; }
        #sidebar { 
            width: 220px; background: var(--bg-panel); 
            border-right: 1px solid var(--border); 
            display: flex; flex-direction: column; 
        }
        
        /* EDITOR & TERMINAL */
        #center-area { flex: 1; display: flex; flex-direction: column; min-width: 0; position: relative; }
        #monaco-container { flex: 1; }
        #terminal-panel { 
            height: 30%; background: #1e1e1e; border-top: 1px solid var(--accent); 
            display: none; flex-direction: column; 
        }
        #xterm-container { flex: 1; overflow: hidden; }

        /* CHAT SIDEBAR (ENHANCED) */
        #chat-panel { 
            width: 350px; background: #202124; border-left: 1px solid var(--border); 
            display: none; flex-direction: column; transition: width 0.3s ease; 
        }
        #chat-panel.active { display: flex; }
        #chat-panel.wide { width: 50%; }
        #chat-panel.full { width: 75%; }
        
        .chat-header { 
            padding: 10px 15px; background: #2d2d2d; border-bottom: 1px solid #3e3e42; 
            display: flex; justify-content: space-between; align-items: center; 
        }
        .chat-msgs { 
            flex: 1; overflow-y: auto; padding: 20px; 
            display: flex; flex-direction: column; gap: 20px; background: #1e1e1e; 
        }
        
        .msg-row { display: flex; gap: 12px; max-width: 100%; }
        .msg-row.user { justify-content: flex-end; }
        .avatar { 
            width: 32px; height: 32px; border-radius: 50%; 
            display: flex; align-items: center; justify-content: center; 
            font-size: 14px; flex-shrink: 0; margin-top: 5px;
        }
        .avatar.bot { background: #e65100; color: white; }
        .avatar.user { background: #1976d2; color: white; }
        
        /* MARKDOWN STYLING */
        .msg-bubble { 
            padding: 12px 16px; border-radius: 8px; font-size: 14px; 
            line-height: 1.6; max-width: 85%; word-wrap: break-word; 
        }
        .msg-row.bot .msg-bubble { background: #2d2d2d; color: #e0e0e0; border: 1px solid #3e3e42; }
        .msg-row.user .msg-bubble { 
            background: linear-gradient(135deg, #007acc, #005f9e); 
            color: white; 
        }
        
        /* Syntax Highlight & Copy Button */
        .msg-bubble pre { 
            background: #111; padding: 10px; border-radius: 6px; 
            overflow-x: auto; margin: 10px 0; border: 1px solid #444; position: relative;
        }
        .msg-bubble code { font-family: 'Consolas', monospace; font-size: 13px; }
        
        /* Copy Button Style */
        .copy-btn {
            position: absolute; top: 5px; right: 5px;
            background: #444; border: 1px solid #555; color: white;
            padding: 4px 8px; border-radius: 4px; cursor: pointer;
            font-size: 11px; opacity: 0.8;
        }
        .copy-btn:hover { opacity: 1; background: #666; }

        .msg-bubble p { margin: 0 0 10px 0; }
        .msg-bubble p:last-child { margin: 0; }
        
        /* MODALS */
        .modal { 
            position: absolute; top: 10%; left: 50%; transform: translateX(-50%); 
            background: #252526; border: 1px solid var(--accent); z-index: 999; 
            display: none; padding: 15px; box-shadow: 0 0 15px rgba(0,0,0,0.5); 
            flex-direction: column; 
        }
        
        /* FILE TREE */
        .t-item { 
            padding: 2px 10px; cursor: pointer; white-space: nowrap; 
            overflow: hidden; text-overflow: ellipsis; font-size: 13px; 
        }
        .t-item:hover { background: #333; }
        .is-dir { color: #fff; font-weight: bold; }
        
        /* STATUS BAR */
        #statusbar { 
            height: 22px; background: var(--accent); color: white; 
            display: flex; align-items: center; padding: 0 10px; font-size: 12px; 
        }
    </style>
</head>
<body>

<div id="toolbar">
    <div style="font-weight:bold; color:#fff; margin-right:10px;">{{ project }}</div>
    <button class="tool-btn" onclick="saveFile()"><i class="fas fa-save"></i> Save</button>
    
    <!-- GIT BUTTON -->
    <button class="tool-btn" onclick="toggleGit()" style="background:#f4511e; border-color:#d84315;">
        <i class="fab fa-git-alt"></i> Git
    </button>

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
                    <!-- NEW SETUP BUTTON -->
                    <button class="tool-btn" onclick="configureGit()" style="display:inline-flex; padding: 2px 8px; margin-right:5px; font-size:11px;">⚙️ Setup</button>
                    <button onclick="document.getElementById('git-modal').style.display='none'" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:16px;">X</button>
                </div>
            </div>
            
            <div style="flex:1; display:flex; gap:10px; overflow:hidden;"> 
                <!-- Left: Actions -->
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
                    
                    <!-- UPLOAD PATCH BUTTON -->
                    <input type="file" id="patch-upload" style="display:none;" onchange="uploadPatch(this)"/>
                    <button class="tool-btn" onclick="document.getElementById('patch-upload').click()">📥 Apply Patch (PC)</button>
                </div>
                
                <!-- Right: Output -->
                <div style="flex:1; display:flex; flex-direction:column; min-width:0;">
                    <pre id="git-output" style="background:#111; color:#ddd; font-family:monospace; font-size:12px; flex:1; padding:10px; overflow-y:auto; border:1px solid #444; margin:0; white-space:pre-wrap;">// Git Output will appear here...</pre>
                    
                    <div style="display:flex; margin-top:10px; gap:5px;">
                        <span style="padding:5px; background:#333; color:#aaa; font-family:monospace;">git</span>
                        <input id="custom-git" onkeyup="if(event.key==='Enter') runCustomGit()" placeholder="status" style="flex:1; background:#222; border:1px solid #444; color:white; padding:5px; font-family:monospace;"/>
                        <button class="tool-btn" onclick="runCustomGit()">Run</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- AI GEN MODAL -->
        <div class="modal" id="gen-modal" style="width:400px; height:200px;">
            <h3 style="margin-top:0; color:white;">AI Code Gen</h3>
            <textarea id="gen-prompt" placeholder="Describe code..." style="width:100%; height:80px; background:#1e1e1e; color:white; border:1px solid #444;"></textarea>
            <div style="margin-top:10px; text-align:right;">
                <button class="tool-btn" onclick="document.getElementById('gen-modal').style.display='none'">Cancel</button>
                <button class="tool-btn primary" onclick="submitGenCode()">Generate</button>
            </div>
        </div>
        
        <!-- EXPLAIN MODAL -->
        <div class="modal" id="explain-modal" style="width: 60%; max-height: 80vh; overflow-y: auto;">
             <div style="display:flex; justify-content:space-between;"><h3 style="margin:0; color:white;">Explanation</h3> <button onclick="document.getElementById('explain-modal').style.display='none'" style="cursor:pointer; background:none; border:none; color:white;">X</button></div>
             <div id="explain-content" style="white-space: pre-wrap; line-height: 1.5; margin-top:10px;"></div>
        </div>
    </div>

    <!-- CHAT SIDEBAR (ENHANCED) -->
    <div id="chat-panel">
        <div class="chat-header">
            <span><i class="fas fa-robot"></i> QGenie Chat</span>
            <div style="display:flex; gap:10px;">
                <!-- NEW CLEAR BUTTON -->
                <i class="fas fa-trash-alt" onclick="clearChat()" title="Clear History" style="cursor:pointer; color:#aaa;"></i>
                
                <i class="fas fa-arrows-alt-h" onclick="toggleChatWidth()" title="Expand/Collapse" style="cursor:pointer; color:#aaa;"></i>
                <i class="fas fa-times" onclick="toggleChat()" style="cursor:pointer; color:#aaa;"></i>
            </div>
        </div>
        <div class="chat-msgs" id="chat-msgs">
            <div class="msg-row bot">
                <div class="avatar bot"><i class="fas fa-robot"></i></div>
                <div class="msg-bubble">
                    <p><b>Hello!</b> I am your project assistant.</p>
                    <p>I can now read other files if you mention them (e.g. <i>"How does utils.py work?"</i>).</p>
                </div>
            </div>
        </div>
        <div style="padding:15px; background:#2d2d2d; border-top:1px solid #3e3e42;">
            <div style="display:flex; gap:5px;">
                <input id="chat-input" onkeyup="if(event.key==='Enter') sendChat()" placeholder="Type query (e.g. 'Check utils.py')..." style="flex:1; padding:8px; background:#333; border:1px solid #555; color:white; border-radius:4px;"/>
                <button id="send-btn" onclick="sendChat()" style="background:var(--accent); border:none; color:white; width:35px; border-radius:4px; cursor:pointer;"><i class="fas fa-paper-plane"></i></button>
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
            language: 'python',
            theme: 'vs-dark',
            automaticLayout: true
        });

        if(currentPath && currentPath !== "None") loadFile(currentPath);
        refreshTree(); 
        initTerminal(); 

        editor.onDidChangeCursorPosition(e => {
            document.getElementById('cursor-pos').innerText = "Ln " + e.position.lineNumber + ", Col " + e.position.column;
        });
        
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
    });

    // --- FILE OPERATIONS ---
    function refreshTree(path) {
        if(path === undefined) path = currentDir;
        currentDir = path;
        
        var c = document.getElementById('file-tree');
        c.innerHTML = '<div style="padding:5px; color:#aaa;">Loading...</div>';

        fetch('/editor/api/tree?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path))
        .then(r=>r.json()).then(nodes => {
            c.innerHTML = "";
            if(path !== "") {
                var up = document.createElement('div');
                up.className = "t-item is-dir";
                up.innerHTML = '<i class="fas fa-level-up-alt"></i> ..';
                up.style.color = "#aaa";
                var parts = path.split('/'); parts.pop();
                var parent = parts.join('/');
                up.onclick = () => refreshTree(parent);
                c.appendChild(up);
            }
            nodes.forEach(n => {
                var d = document.createElement('div');
                d.className = "t-item " + (n.type==='dir'?'is-dir':'');
                d.innerText = (n.type==='dir'?'📂 ':'📄 ') + n.name;
                d.onclick = () => { n.type === 'dir' ? refreshTree(n.path) : loadFile(n.path); };
                c.appendChild(d);
            });
        });
    }

    function createItem(type) {
        var name = prompt("Enter Name for new " + (type==='dir'?'Folder':'File') + ":");
        if(!name) return;
        var newPath = (currentDir ? currentDir + '/' : '') + name;
        fetch('/editor/api/create', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ project: project, path: newPath, type: type })
        }).then(r => r.json()).then(d => {
            if(d.error) alert("Error: " + d.error);
            else { refreshTree(); if(type === 'file') loadFile(newPath); }
        });
    }

    function loadFile(path) {
        document.getElementById('status-msg').innerText = "Loading " + path + "...";
        fetch('/editor/api/read?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(path))
        .then(r=>r.json()).then(d => {
            if(d.error) { alert("Error: " + d.error); return; }
            var ext = path.split('.').pop();
            var lang = 'plaintext';
            if(ext==='py') lang='python'; if(ext==='js') lang='javascript';
            if(editor) {
                editor.setModel(monaco.editor.createModel(d.content, lang));
                currentPath = path;
                document.getElementById('status-msg').innerText = "Opened: " + path;
            }
        });
    }

    function saveFile() {
        if(!currentPath) return;
        fetch('/save_file', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ project:project, path:currentPath, content:editor.getValue() })
        }).then(r=>r.json()).then(d => { 
            document.getElementById('status-msg').innerText = d.error ? "Error: "+d.error : "Saved";
        });
    }

    // --- GIT FUNCTIONS ---
    function toggleGit() {
        var m = document.getElementById('git-modal');
        if (m.style.display === 'none') {
            m.style.display = 'flex';
            runGit('status'); 
        } else {
            m.style.display = 'none';
        }
    }

    function configureGit() {
        fetch('/editor/api/git/identity?project=' + encodeURIComponent(project) + '&path=' + encodeURIComponent(currentPath||""))
        .then(r => r.json())
        .then(d => {
            var name = prompt("Enter Git Username:", d.name || "");
            if (name === null) return;
            var email = prompt("Enter Git Email:", d.email || "");
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

        fetch('/editor/api/git/apply_patch', {
            method: 'POST',
            body: fd
        })
        .then(r => r.json())
        .then(d => {
            out.innerText += "\\n" + (d.output || d.error);
            out.scrollTop = out.scrollHeight;
            input.value = ""; 
        });
    }

    function runGit(args) {
        var out = document.getElementById('git-output');
        out.innerText += `\\n\\n$ git ${args} ...`;
        
        var ctxPath = currentPath || currentDir || "";
        
        fetch('/editor/api/term', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ project: project, cmd: 'git ' + args, path: ctxPath })
        }).then(r => r.json()).then(d => {
            if(!d.output.trim()) d.output = "Done (No output returned)";
            out.innerText += "\\n" + d.output;
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
        if (!cmd) return;
        runGit(cmd);
        document.getElementById('custom-git').value = '';
    }

    // --- CHAT & AI UTILS (ROBUST) ---
    function toggleChat() { document.getElementById('chat-panel').classList.toggle('active'); if(editor) editor.layout(); }
    
    function toggleChatWidth() {
        var p = document.getElementById('chat-panel');
        if(p.classList.contains('wide')) { p.classList.remove('wide'); p.classList.add('full'); }
        else if(p.classList.contains('full')) { p.classList.remove('full'); }
        else { p.classList.add('wide'); }
        if(editor) setTimeout(() => editor.layout(), 300);
    }
    
    function clearChat() {
        if(!confirm("Clear chat history?")) return;
        document.getElementById('chat-msgs').innerHTML = '<div class="msg-row bot"><div class="avatar bot"><i class="fas fa-robot"></i></div><div class="msg-bubble">History cleared. How can I help?</div></div>';
        fetch('/editor/api/chat_clear', {
            method: 'POST', 
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ project: project })
        });
    }

    // --- ROBUST PARSER (WORKS OFFLINE) ---
    function parseMarkdownSafe(text) {
        // If library loaded, use it
        if (typeof marked !== 'undefined') {
            try { return marked.parse(text); } catch(e) { console.error("Marked fail", e); }
        }
        
        // Custom Mini-Parser if library is blocked
        // 1. Code Blocks
        var t = text.replace(/```([\s\S]*?)```/g, function(m, code) {
            return '<pre><code>' + code.replace(/</g, '&lt;') + '</code></pre>';
        });
        // 2. Inline Code
        t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
        // 3. Bold
        t = t.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
        // 4. Newlines
        t = t.replace(/\\n/g, '<br>');
        return t;
    }

    function addCopyButtons() {
        document.querySelectorAll('pre').forEach(pre => {
            if (pre.querySelector('.copy-btn')) return; // Already exists
            
            var btn = document.createElement('button');
            btn.className = 'copy-btn';
            btn.innerHTML = '<i class="fas fa-copy"></i> Copy';
            
            btn.onclick = () => {
                var code = pre.querySelector('code');
                var text = code ? code.innerText : pre.innerText;
                navigator.clipboard.writeText(text).then(() => {
                    btn.innerHTML = '<i class="fas fa-check"></i> Copied';
                    setTimeout(() => btn.innerHTML = '<i class="fas fa-copy"></i> Copy', 2000);
                });
            };
            
            pre.appendChild(btn);
        });
    }

    function sendChat() {
        var i = document.getElementById('chat-input');
        var txt = i.value; 
        if(!txt) return;
        
        var b = document.getElementById('chat-msgs');
        
        // Render User Msg
        var userHtml = '<div class="msg-row user"><div class="msg-bubble">' + parseMarkdownSafe(txt) + '</div><div class="avatar user"><i class="fas fa-user"></i></div></div>';
        b.innerHTML += userHtml;
        i.value = '';
        b.scrollTop = b.scrollHeight;

        // Get Editor Content (Safe)
        var codeVal = "";
        try { if(editor) codeVal = editor.getValue(); } catch(e) {}

        fetch('/editor/api/chat_context', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ project:project, message:txt, code_context:codeVal, current_file:currentPath })
        }).then(r=>r.json()).then(d=>{
            // Render Bot Msg
            var botText = parseMarkdownSafe(d.response);
            var botHtml = '<div class="msg-row bot"><div class="avatar bot"><i class="fas fa-robot"></i></div><div class="msg-bubble">'+botText+'</div></div>';
            b.innerHTML += botHtml;
            
            // Syntax Highlight (Try Safe)
            if(typeof hljs !== 'undefined') {
                try {
                    b.querySelectorAll('pre code').forEach((block) => {
                        hljs.highlightElement(block);
                    });
                } catch(e) {}
            }
            
            // Add Copy Buttons
            addCopyButtons();
            
            b.scrollTop = b.scrollHeight;
        }).catch(err => {
            b.innerHTML += '<div style="color:red; font-size:12px; padding:10px;">Network Error: ' + err + '</div>';
        });
    }

    function openGenModal() { document.getElementById('gen-modal').style.display='block'; }
    
    function submitGenCode() {
        var p = document.getElementById('gen-prompt').value;
        fetch('/editor/api/ai_gen', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ prompt:p, language: editor.getModel().getLanguageId() })
        }).then(r=>r.json()).then(d=>{
            if(d.code) {
                editor.executeEdits("ai", [{ range: editor.getSelection(), text: d.code }]);
                document.getElementById('gen-modal').style.display='none';
            }
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
        try {
            if(typeof Terminal !== 'undefined') {
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
            } else {
                document.getElementById('xterm-container').innerHTML = "<div style='padding:10px; color:orange'>Terminal Lib offline</div>";
            }
        } catch(e) { console.log("Terminal failed: " + e); }
    }
    
    function toggleTerminal() { 
        var t = document.getElementById('terminal-panel');
        t.style.display = (t.style.display === 'flex' ? 'none' : 'flex');
        if(t.style.display === 'flex' && term) term.fit();
    }
</script>
</body>
</html>
"""

# --- BACKEND ENDPOINTS ---

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

# --- NEW: SMART CHAT CONTEXT ---
@editor_bp.route('/editor/api/chat_context', methods=['POST'])
def chat_context():
    if not AI_AVAILABLE: return jsonify({'response': "AI unavailable"})
    
    data = request.json
    project = data.get('project')
    user_msg = data.get('message', '')
    current_code = data.get('code_context', '')
    current_file = data.get('current_file', 'Unknown')
    
    root, _ = get_config_safe(project)
    
    # 1. SMART FILE DETECTION (If user asks "Check web_manager.py", read it)
    extra_context = ""
    try:
        # Collect all filenames in project
        all_files = []
        for r, dirs, files in os.walk(root):
            for f in files: all_files.append(f)
            
        # Check if any file name appears in the user message
        matches = [f for f in all_files if f in user_msg and f != os.path.basename(current_file)]
        
        for m in matches[:3]: # Limit to 3 files to save tokens
            # Find full path
            for r, dirs, files in os.walk(root):
                if m in files:
                    with open(os.path.join(r, m), 'r', errors='replace') as f:
                        content = f.read()
                        extra_context += f"\n--- REFERENCED FILE: {m} ---\n{content[:4000]}\n" # Limit size per file
                    break
    except Exception:
        pass

    # 2. FILE TREE CONTEXT (Help AI understand project structure)
    tree_context = "Project Structure:\n"
    try:
        for item in os.listdir(root):
            if not item.startswith('.'):
                tree_context += f"- {item}\n"
    except:
        pass

    # 3. ADD LINE NUMBERS
    lines = current_code.split('\n')
    numbered_code = "\n".join([f"{i+1}: {line}" for i, line in enumerate(lines)])
    
    # 4. MANAGE HISTORY
    if project not in chat_histories:
        chat_histories[project] = []
    
    # 5. BUILD PROMPT
    from qgenie import QGenieClient, ChatMessage
    
    sys_prompt = (
        f"You are a Senior Developer. Project: {project}\n"
        f"{tree_context}\n"
        f"Currently Editing: {current_file}\n\n"
        f"--- CODE CONTENT ---\n"
        f"{numbered_code}\n\n"
        f"{extra_context}\n"
        f"Instructions: Answer in Markdown. Be concise. Cite line numbers."
    )
    
    # Append User Message to History
    chat_histories[project].append(ChatMessage(role="user", content=user_msg))
    
    # Send System Prompt + Last 8 Messages
    messages_to_send = [ChatMessage(role="system", content=sys_prompt)] + chat_histories[project][-8:]
    
    try:
        # Call AI
        response = QGenieClient().chat(messages=messages_to_send)
        bot_reply = response.first_content
        
        # Save Reply
        chat_histories[project].append(ChatMessage(role="assistant", content=bot_reply))
        
        return jsonify({'response': bot_reply})
        
    except Exception as e:
        return jsonify({'response': f"AI Error: {str(e)}"})

@editor_bp.route('/editor/api/chat_clear', methods=['POST'])
def chat_clear():
    p = request.json.get('project')
    if p in chat_histories: del chat_histories[p]
    return jsonify({'status':'ok'})

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
    d = request.json
    
    # 1. SMART CONTEXT DETECTION (Fixes Exit 128)
    cwd = get_cwd_context(d['project'], d.get('path'))

    try:
        # FORCE GIT TO AVOID PAGER (prevents hangs)
        env = os.environ.copy()
        env['GIT_PAGER'] = 'cat'

        # Use subprocess.run to capture stderr and avoid crashes on non-utf8 chars
        res = subprocess.run(
            d['cmd'], 
            shell=True, 
            cwd=cwd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, # Combine stdout/stderr
            env=env
        )
        
        # KEY FIX: errors='replace' prevents 0xf6 crash
        output = res.stdout.decode('utf-8', errors='replace')
        
        if res.returncode != 0:
            return jsonify({'output': f"COMMAND FAILED (Exit {res.returncode}):\n{output}"})
            
        return jsonify({'output': output})

    except Exception as e: 
        return jsonify({'output': f"Execution Error: {str(e)}"})

# --- GIT IDENTITY ENDPOINT ---
@editor_bp.route('/editor/api/git/identity')
def git_identity():
    cwd = get_cwd_context(request.args.get('project'), request.args.get('path'))
    # Use 'git config' to read values
    name = subprocess.run('git config user.name', shell=True, cwd=cwd, stdout=subprocess.PIPE).stdout.decode().strip()
    email = subprocess.run('git config user.email', shell=True, cwd=cwd, stdout=subprocess.PIPE).stdout.decode().strip()
    return jsonify({'name': name, 'email': email})

# --- APPLY PATCH FILE ENDPOINT ---
@editor_bp.route('/editor/api/git/apply_patch', methods=['POST'])
def apply_patch():
    try:
        if 'patch' not in request.files: return jsonify({'error': 'No file part'})
        f = request.files['patch']
        if f.filename == '': return jsonify({'error': 'No selected file'})
        
        cwd = get_cwd_context(request.form.get('project'), request.form.get('path'))
        patch_path = os.path.join(cwd, f.filename)
        
        # Save temp file
        f.save(patch_path)
        
        # Apply
        res = subprocess.run(f'git apply "{f.filename}"', shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        
        # Cleanup
        if os.path.exists(patch_path):
            os.remove(patch_path)
        
        out = res.stdout.decode('utf-8', errors='replace')
        
        if res.returncode != 0:
            return jsonify({'error': out})
            
        return jsonify({'output': out if out.strip() else "Patch applied successfully."})
        
    except Exception as e:
        return jsonify({'error': str(e)})

@editor_bp.route('/editor/api/create', methods=['POST'])
def create_item():
    d = request.json
    project = d.get('project')
    rel_path = d.get('path')
    item_type = d.get('type')
    
    root, _ = get_config_safe(project)
    if not root: return jsonify({'error': 'Project not found'})
    
    abs_path = os.path.abspath(os.path.join(root, rel_path))
    if not abs_path.startswith(root):
        return jsonify({'error': 'Invalid path security'})

    try:
        if item_type == 'dir':
            os.makedirs(abs_path, exist_ok=True)
        else:
            if not os.path.exists(abs_path):
                with open(abs_path, 'w') as f: pass 
    except Exception as e:
        return jsonify({'error': str(e)})
        
    return jsonify({'status': 'ok'})
