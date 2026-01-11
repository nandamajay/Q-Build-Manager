import os
import yaml
import glob
import subprocess
import pty
import threading
import signal
import shutil
import time
import re
import json
from flask import Flask, render_template_string, request, redirect, abort, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

SERVER_PORT = int(os.environ.get("WEB_PORT", 5000))
WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")
BUILD_DIR_BASE = os.path.join(WORK_DIR, "meta-qcom-builds")
BUILD_STATES = {}

# --- HELPERS ---
def get_disk_usage():
    try:
        total, used, free = shutil.disk_usage(WORK_DIR)
        return int((used / total) * 100), int(free // (2**30))
    except: return 0, 0

def sync_registry():
    if not os.path.exists(REGISTRY_FILE): reg = {}
    else:
        try:
            with open(REGISTRY_FILE, "r") as f: reg = yaml.safe_load(f) or {}
        except: reg = {}
    
    if not os.path.exists(BUILD_DIR_BASE): os.makedirs(BUILD_DIR_BASE, exist_ok=True)
    
    # Add missing
    found = [d for d in os.listdir(BUILD_DIR_BASE) if os.path.isdir(os.path.join(BUILD_DIR_BASE, d))]
    updated = False
    for p in found:
        if p not in reg: reg[p] = os.path.join(BUILD_DIR_BASE, p); updated = True
    
    # Remove deleted
    for n, p in list(reg.items()):
        if not os.path.exists(p) and BUILD_STATES.get(n,{}).get('status') != 'deleting':
            del reg[n]; updated = True
            
    if updated:
        with open(REGISTRY_FILE, "w") as f: yaml.dump(reg, f)
    return reg

def get_config(project_name):
    reg = sync_registry()
    path = reg.get(project_name)
    if not path: return None, None
    cfg_path = os.path.join(path, "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f: return path, yaml.safe_load(f)
    return path, {}

def background_delete(path, name):
    try: shutil.rmtree(path)
    except: pass

# --- HTML TEMPLATES ---
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Q-Build Manager V10</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@4.19.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@4.19.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.5.0/lib/xterm-addon-fit.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/styles/atom-one-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/highlight.min.js"></script>
    <style>
        .nav-token { cursor: pointer; transition: all 0.2s; border-bottom: 1px dotted rgba(255,255,255,0.2); }
        .nav-token:hover { background-color: rgba(59, 130, 246, 0.3); color: #60a5fa !important; border-bottom: 1px solid #60a5fa; }
        .hljs { background: transparent; padding: 0; } 
    </style>
</head>
<body class="bg-gray-900 text-gray-100 font-sans min-h-screen flex flex-col">
    <nav class="bg-gray-800 p-4 border-b border-gray-700">
        <div class="container mx-auto flex justify-between items-center">
            <a href="/" class="text-2xl font-bold text-blue-400"><i class="fas fa-microchip mr-2"></i>Q-Build <span class="text-xs text-green-400">V10</span></a>
            <div class="flex items-center space-x-6">
                <div class="flex items-center space-x-2 text-sm">
                    <i class="fas fa-hdd text-gray-400"></i>
                    <span class="text-gray-400">{{ disk_free }}GB Free</span>
                </div>
                <a href="/create" class="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded shadow">+ New Project</a>
            </div>
        </div>
    </nav>
    <div class="container mx-auto p-4 flex-grow">
        {{ body_content | safe }}
    </div>
</body>
</html>
"""

EXPLORER_HTML = """
<div class="flex h-[80vh] bg-gray-800 rounded-lg shadow-lg overflow-hidden border border-gray-700">
    <div class="w-1/4 bg-gray-900 border-r border-gray-700 flex flex-col">
        <div class="p-3 border-b border-gray-700 bg-gray-800 font-bold flex justify-between"><span>{{ project }}</span><a href="/" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></a></div>
        <div class="overflow-y-auto flex-grow p-2 text-sm font-mono">
            {% if parent_dir %}<a href="/code/{{ project }}/{{ parent_dir }}" class="block p-1 text-yellow-400 hover:bg-gray-800"><i class="fas fa-level-up-alt mr-2"></i>..</a>{% endif %}
            {% for d in dirs %}<a href="/code/{{ project }}/{{ current_path }}/{{ d }}" class="block p-1 text-blue-400 hover:bg-gray-800 truncate"><i class="fas fa-folder mr-2"></i>{{ d }}</a>{% endfor %}
            {% for f in files %}<a href="/code/{{ project }}/{{ current_path }}/{{ f }}" class="block p-1 text-gray-300 hover:bg-gray-800 truncate"><i class="far fa-file mr-2"></i>{{ f }}</a>{% endfor %}
        </div>
    </div>
    <div class="w-3/4 flex flex-col bg-[#282c34] relative">
        <div class="p-2 bg-gray-800 border-b border-gray-700 text-xs text-gray-400 flex justify-between"><span>{{ current_path }}</span><span>{{ file_size }}</span></div>
        <div class="flex-grow overflow-auto p-4" id="codeContainer">
            {% if is_file %}
            <pre><code class="language-{{ ext }}" id="codeBlock" style="font-family: 'Fira Code', monospace; line-height: 1.5;">{{ content }}</code></pre>
            {% else %}
            <div class="flex items-center justify-center h-full text-gray-500"><div class="text-center"><i class="fas fa-code text-6xl mb-4 opacity-20"></i><p>Select a file to view content</p></div></div>
            {% endif %}
        </div>
        
        <!-- Definition Results Modal -->
        <div id="defModal" class="hidden absolute top-10 right-10 bg-gray-800 border border-gray-600 p-4 rounded shadow-2xl z-50 w-96 max-h-96 overflow-y-auto">
            <div class="flex justify-between items-center mb-2 border-b border-gray-700 pb-2">
                <h4 class="font-bold text-sm text-blue-400">Definitions</h4>
                <button onclick="closeModal()" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></button>
            </div>
            <div id="defList" class="space-y-2 text-xs font-mono"></div>
        </div>
    </div>
</div>

<script>
    // 1. Highlight Syntax
    hljs.highlightAll();

    // 2. Tokenize Words for Navigation (Wait for HLJS to finish)
    setTimeout(() => {
        const codeBlock = document.getElementById('codeBlock');
        if (!codeBlock) return;

        // Recursive function to walk TEXT nodes only
        function makeLinks(node) {
            if (node.nodeType === 3) { // Text Node
                const text = node.nodeValue;
                if (!text.trim()) return; // Skip empty whitespace
                
                // Split by non-word characters, keeping delimiters
                const parts = text.split(/([a-zA-Z_][a-zA-Z0-9_]*)/);
                
                if (parts.length > 1) {
                    const fragment = document.createDocumentFragment();
                    parts.forEach(part => {
                        // Check if it's a valid C identifier
                        if (/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(part)) {
                            const span = document.createElement('span');
                            span.className = 'nav-token';
                            span.textContent = part;
                            span.onclick = (e) => { 
                                e.stopPropagation(); 
                                findDef(part); 
                            };
                            fragment.appendChild(span);
                        } else {
                            fragment.appendChild(document.createTextNode(part));
                        }
                    });
                    node.parentNode.replaceChild(fragment, node);
                }
            } else if (node.nodeType === 1) { // Element Node (e.g., HLJS span)
                // Convert childNodes to array to avoid live-collection issues while modifying
                Array.from(node.childNodes).forEach(makeLinks);
            }
        }
        
        makeLinks(codeBlock);
        console.log("Navigation tokens applied.");
    }, 100); // 100ms delay ensures HighlightJS is done

    function findDef(symbol) {
        if (!symbol) return;
        var list = document.getElementById('defList');
        var modal = document.getElementById('defModal');
        list.innerHTML = '<div class="text-gray-400"><i class="fas fa-spinner fa-spin"></i> Searching...</div>';
        modal.classList.remove('hidden');

        fetch(`/search_def/{{ project }}/${symbol}`)
            .then(r => r.json())
            .then(data => {
                list.innerHTML = '';
                if (data.results.length === 0) {
                    list.innerHTML = '<div class="text-red-400">No definitions found for ' + symbol + '.</div>';
                } else {
                    data.results.forEach(res => {
                        var link = document.createElement('a');
                        link.href = `/code/{{ project }}/${res.file}#line-${res.line}`;
                        link.target = "_blank"; // Open in new tab so we don't lose context
                        link.className = "block p-2 hover:bg-gray-700 rounded border border-transparent hover:border-gray-600 transition";
                        link.innerHTML = `<div class="text-blue-300 font-bold">${res.file}:${res.line}</div><div class="text-gray-500 truncate italic">${res.context}</div>`;
                        list.appendChild(link);
                    });
                }
            })
            .catch(e => {
                list.innerHTML = '<div class="text-red-500">Error searching.</div>';
            });
    }

    function closeModal() {
        document.getElementById('defModal').classList.add('hidden');
    }
</script>
"""

DASHBOARD_HTML = """<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">{% for name, path in projects.items() %}{% if states.get(name, {}).get('status') != 'deleting' %}<div class="bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-lg relative group"><h3 class="text-xl font-bold mb-1">{{ name }}</h3><p class="text-gray-400 text-xs mb-4 truncate">{{ path }}</p><div class="flex justify-between items-center mt-4"><div class="flex space-x-2"><a href="/build/{{ name }}" class="bg-green-700 hover:bg-green-600 px-3 py-2 rounded text-white text-sm" title="Build Console"><i class="fas fa-hammer"></i> Build</a><a href="/code/{{ name }}/" class="bg-purple-700 hover:bg-purple-600 px-3 py-2 rounded text-white text-sm" title="Source Code"><i class="fas fa-code"></i></a></div><a href="/delete/{{ name }}" class="text-red-400 hover:text-red-300 px-3 py-2 opacity-0 group-hover:opacity-100 transition" onclick="return confirm('Delete {{ name }} permanently?')"><i class="fas fa-trash"></i></a></div><div class="absolute top-4 right-4 h-3 w-3 rounded-full {{ 'bg-yellow-500 animate-pulse' if states.get(name, {}).get('status') == 'running' else 'bg-green-500' if states.get(name, {}).get('status') == 'done' else 'bg-gray-600' }}"></div></div>{% endif %}{% else %}<div class="col-span-3 text-center py-20 text-gray-500"><p>No projects found.</p></div>{% endfor %}</div>"""
BUILD_CONSOLE_HTML = """<div class="flex flex-col h-full space-y-4"><div class="bg-gray-800 p-4 rounded-lg shadow flex justify-between items-center"><div><h2 class="text-2xl font-bold">{{ project }}</h2><div class="text-sm text-gray-400 mt-1">Status: <span id="statusBadge" class="font-bold">UNKNOWN</span></div></div><div class="flex items-center space-x-4 bg-gray-900 p-2 rounded border border-gray-700" id="topoControl"><label class="text-sm text-gray-400 font-bold mr-2">Topology:</label><label class="inline-flex items-center cursor-pointer"><input type="radio" name="topo" value="ASOC" class="form-radio text-blue-600" checked><span class="ml-2 text-sm">ASOC</span></label><label class="inline-flex items-center cursor-pointer"><input type="radio" name="topo" value="AudioReach" class="form-radio text-blue-600"><span class="ml-2 text-sm">AudioReach</span></label></div><div class="flex space-x-3 items-center"><a href="/code/{{ project }}/" target="_blank" class="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded text-white"><i class="fas fa-external-link-alt mr-1"></i> Browse Code</a><button onclick="stopBuild()" id="stopBtn" class="hidden bg-red-600 text-white px-6 py-2 rounded">STOP</button><button onclick="startBuild()" id="buildBtn" class="bg-green-600 text-white px-6 py-2 rounded"><i class="fas fa-play mr-1"></i> Start Build</button><a href="/" class="bg-gray-700 px-4 py-2 rounded text-white">Back</a></div></div><div id="terminal" class="flex-grow bg-black rounded h-[600px]"></div></div><script>var socket = io(); var project = '{{ project }}'; var term = new Terminal({theme:{background:'#000',foreground:'#e5e5e5'}}); var fitAddon = new FitAddon.FitAddon(); term.loadAddon(fitAddon); term.open(document.getElementById('terminal')); fitAddon.fit(); socket.on('connect', function() { socket.emit('join_project', {project: project}); }); socket.on('log_chunk', function(msg){ term.write(msg.data); }); socket.on('build_status', function(msg){ updateUI(msg.status); }); function updateUI(status){ var b=document.getElementById('buildBtn'); var s=document.getElementById('stopBtn'); var t=document.getElementById('topoControl'); document.getElementById('statusBadge').innerText=status.toUpperCase(); if(status=='running'){b.classList.add('hidden'); s.classList.remove('hidden'); t.classList.add('opacity-50', 'pointer-events-none');} else {b.classList.remove('hidden'); s.classList.add('hidden'); t.classList.remove('opacity-50', 'pointer-events-none');}} function startBuild(){ term.clear(); var topo = document.querySelector('input[name="topo"]:checked').value; socket.emit('start_build',{project:project, topology: topo}); } function stopBuild(){ socket.emit('stop_build',{project:project}); }</script>"""
CREATE_STEP1_HTML = """<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg"><h2 class="text-2xl font-bold mb-6">Step 1: Project Name</h2><form action="/create_step2" method="POST" class="space-y-4" onsubmit="document.getElementById('btn').innerHTML='<i class=\'fas fa-spinner fa-spin\'></i> Cloning...';"><div><label class="block text-sm text-gray-400 mb-1">Project Name</label><input type="text" name="name" required class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white"></div><button id="btn" type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4">Next <i class="fas fa-arrow-right ml-2"></i></button></form></div>"""
CREATE_STEP2_HTML = """<div class="max-w-2xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg"><h2 class="text-2xl font-bold mb-6">Step 2: Configuration</h2><form action="/finish_create" method="POST" class="space-y-6"><input type="hidden" name="name" value="{{ project }}"><div><label class="block text-sm text-gray-400 mb-1">Target Board</label><select name="board" class="w-full bg-gray-900 border border-gray-600 rounded p-3 text-white">{% for b in boards %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select></div><button type="submit" class="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold mt-4">Create Project</button></form></div>"""

# --- ROUTES ---
@app.route('/')
def index():
    reg = sync_registry()
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(DASHBOARD_HTML, projects=reg, states=BUILD_STATES))

@app.route('/create')
def create_step1_view():
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=CREATE_STEP1_HTML)

@app.route('/create_step2', methods=['POST'])
def create_step2_action():
    name = request.form['name']
    proj_path = os.path.join(BUILD_DIR_BASE, name)
    os.makedirs(proj_path, exist_ok=True)
    repo_path = os.path.join(proj_path, "meta-qcom")
    if not os.path.exists(repo_path): subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", repo_path], check=True)
    ci_path = os.path.join(repo_path, "ci")
    boards = [f for f in os.listdir(ci_path) if f.endswith('.yml')] if os.path.exists(ci_path) else []
    boards.sort()
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(CREATE_STEP2_HTML, project=name, boards=boards))

@app.route('/finish_create', methods=['POST'])
def finish_create():
    name = request.form['name']; board = request.form['board']
    proj_path = os.path.join(BUILD_DIR_BASE, name)
    cfg = {"kas_files": f"meta-qcom/ci/{board}", "image": "qcom-multimedia-image"}
    with open(os.path.join(proj_path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    return redirect('/')

@app.route('/delete/<name>')
def delete(name):
    reg = sync_registry()
    if name in reg:
        path = reg[name]
        BUILD_STATES[name] = {'status': 'deleting'}
        threading.Thread(target=background_delete, args=(path, name)).start()
        del reg[name]
        with open(REGISTRY_FILE, "w") as f: yaml.dump(reg, f)
    return redirect('/')

@app.route('/code/<name>/', defaults={'req_path': ''})
@app.route('/code/<name>/<path:req_path>')
def code_explorer(name, req_path):
    try:
        root_path, _ = get_config(name)
        if not root_path: return redirect('/')
        abs_root = os.path.abspath(root_path)
        abs_req = os.path.abspath(os.path.join(abs_root, req_path))
        if not abs_req.startswith(abs_root): return abort(403)
        
        pct, free = get_disk_usage()
        
        if os.path.isdir(abs_req):
            try: items = sorted(os.listdir(abs_req))
            except: items = []
            dirs = [i for i in items if os.path.isdir(os.path.join(abs_req, i)) and not i.startswith('.')]
            files = [i for i in items if os.path.isfile(os.path.join(abs_req, i)) and not i.startswith('.')]
            parent = os.path.relpath(os.path.dirname(abs_req), abs_root)
            if parent == '.': parent = ''
            if req_path == '': parent = None
            return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(EXPLORER_HTML, project=name, current_path=req_path, dirs=dirs, files=files, parent_dir=parent, is_file=False))
        elif os.path.isfile(abs_req):
            try:
                with open(abs_req, 'r', errors='replace') as f: content = f.read(100000)
            except Exception as e: content = f"Error reading file: {e}"
            _, ext = os.path.splitext(abs_req)
            ext = ext.lstrip('.')
            if ext in ['yml', 'yaml']: ext = 'yaml'
            elif ext in ['py']: ext = 'python'
            elif ext in ['bb', 'inc', 'conf']: ext = 'bash'
            elif ext in ['c', 'h', 'cpp']: ext = 'c'
            
            parent_dir_abs = os.path.dirname(abs_req)
            try: items = sorted(os.listdir(parent_dir_abs))
            except: items = []
            dirs = [i for i in items if os.path.isdir(os.path.join(parent_dir_abs, i)) and not i.startswith('.')]
            files = [i for i in items if os.path.isfile(os.path.join(parent_dir_abs, i)) and not i.startswith('.')]
            rel_parent = os.path.relpath(parent_dir_abs, abs_root)
            if rel_parent == '.': rel_parent = ''
            return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(EXPLORER_HTML, project=name, current_path=rel_parent, dirs=dirs, files=files, parent_dir=os.path.dirname(rel_parent) if rel_parent else None, is_file=True, content=content, ext=ext, file_size=f"{os.path.getsize(abs_req)} bytes"))
    except Exception as e:
        return f"Explorer Error: {str(e)}", 500
    return abort(404)

@app.route('/build/<name>')
def build_page(name): 
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(BUILD_CONSOLE_HTML, project=name))

# --- NEW: SYMBOL SEARCH API ---
@app.route('/search_def/<project>/<symbol>')
def search_definition(project, symbol):
    root_path, _ = get_config(project)
    if not root_path: return jsonify({'results': []})
    
    search_paths = []
    meta_qcom = os.path.join(root_path, "meta-qcom")
    kernel_src = os.path.join(root_path, "build/tmp/work-shared") 
    
    if os.path.exists(meta_qcom): search_paths.append(meta_qcom)
    if os.path.exists(kernel_src): search_paths.append(kernel_src)
    if not search_paths: search_paths.append(root_path) 
    
    results = []
    grep_cmd = [
        "grep", "-rnI", 
        "--include=*.c", "--include=*.h", "--include=*.cpp", "--include=*.dts", "--include=*.dtsi",
        "-E", f"^(struct|union|enum|class|#define|typedef).*{symbol}\\b",
        *search_paths
    ]
    try:
        out = subprocess.check_output(grep_cmd, stderr=subprocess.DEVNULL).decode('utf-8')
        for line in out.splitlines():
            if not line: continue
            parts = line.split(':', 2)
            if len(parts) >= 3:
                rel_path = os.path.relpath(parts[0], root_path)
                results.append({'file': rel_path, 'line': parts[1], 'context': parts[2].strip()[:100]})
    except: pass

    if not results:
        grep_cmd_loose = [
             "grep", "-rnI", "--include=*.c", "--include=*.h", "-E", f"^{symbol}\\(", *search_paths
        ]
        try:
            out = subprocess.check_output(grep_cmd_loose, stderr=subprocess.DEVNULL).decode('utf-8')
            for line in out.splitlines():
                parts = line.split(':', 2)
                if len(parts) >= 3:
                    rel_path = os.path.relpath(parts[0], root_path)
                    results.append({'file': rel_path, 'line': parts[1], 'context': parts[2].strip()[:100]})
        except: pass

    return jsonify({'results': results[:20]})

# --- SOCKET ---
@socketio.on('join_project')
def handle_join(data):
    join_room(data['project'])
    name = data['project']
    if name in BUILD_STATES: 
        if 'logs' in BUILD_STATES[name]:
            emit('log_chunk', {'data': "".join(BUILD_STATES[name]['logs'])})
        emit('build_status', {'status': BUILD_STATES[name].get('status', 'unknown')})

@socketio.on('start_build')
def handle_build(data):
    name = data['project']
    topo = data.get('topology', 'ASOC')
    path, cfg = get_config(name)
    cfg['topology'] = topo
    with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    
    distro = 'meta-qcom/ci/qcom-distro-prop-image.yml' if topo == 'AudioReach' else 'meta-qcom/ci/qcom-distro.yml'
    kas_args = f"{cfg.get('kas_files')}:{distro}"
    cmd = f"kas shell {kas_args} -c 'bitbake {cfg.get('image')}'"

    BUILD_STATES[name] = {'status': 'running', 'logs': [], 'pid': None}
    emit('build_status', {'status': 'running'}, to=name)
    
    master, slave = pty.openpty()
    p = subprocess.Popen(cmd, shell=True, cwd=path, stdout=slave, stderr=slave, preexec_fn=os.setsid, executable='/bin/bash')
    os.close(slave)
    BUILD_STATES[name]['pid'] = p.pid

    def read_output():
        while True:
            try:
                data = os.read(master, 1024)
                if not data: break
                d = data.decode(errors='ignore')
                BUILD_STATES[name]['logs'].append(d)
                socketio.emit('log_chunk', {'data': d}, to=name)
            except: break
        p.wait()
        final_status = 'done' if p.returncode == 0 else 'failed'
        BUILD_STATES[name]['status'] = final_status
        socketio.emit('build_status', {'status': final_status}, to=name)
    threading.Thread(target=read_output).start()

@socketio.on('stop_build')
def handle_stop(data):
    name = data['project']
    if name in BUILD_STATES and BUILD_STATES[name]['pid']:
        try: os.killpg(os.getpgid(BUILD_STATES[name]['pid']), signal.SIGTERM)
        except: pass
    emit('build_status', {'status': 'stopped'}, to=name)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, debug=True, use_reloader=True, allow_unsafe_werkzeug=True)
