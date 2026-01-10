import os
import yaml
import glob
import subprocess
import pty
import threading
import signal
import shutil
import re
import mimetypes
from flask import Flask, render_template_string, request, redirect, jsonify, send_from_directory, abort, url_for
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

SERVER_PORT = int(os.environ.get("WEB_PORT", 5000))
WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")
BUILD_STATES = {}

def load_registry():
    if not os.path.exists(REGISTRY_FILE): return {}
    with open(REGISTRY_FILE, "r") as f: return yaml.safe_load(f) or {}

def save_registry(data):
    with open(REGISTRY_FILE, "w") as f: yaml.dump(data, f)

def get_config(project_name):
    reg = load_registry()
    path = reg.get(project_name)
    if not path: return None, None
    cfg_path = os.path.join(path, "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f: return path, yaml.safe_load(f)
    return path, {}

def get_disk_usage():
    total, used, free = shutil.disk_usage(WORK_DIR)
    return int((used / total) * 100), int(free // (2**30))

def find_artifacts(project_path):
    for root, dirs, files in os.walk(project_path):
        if "tmp/deploy/images" in root: return root
    return None

# --- HTML TEMPLATES ---
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Q-Build Manager V5</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@4.19.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@4.19.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.5.0/lib/xterm-addon-fit.js"></script>
    <!-- Code Highlighting -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/styles/atom-one-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/highlight.min.js"></script>
    <script>hljs.highlightAll();</script>
</head>
<body class="bg-gray-900 text-gray-100 font-sans min-h-screen flex flex-col">
    <nav class="bg-gray-800 p-4 border-b border-gray-700">
        <div class="container mx-auto flex justify-between items-center">
            <a href="/" class="text-2xl font-bold text-blue-400"><i class="fas fa-microchip mr-2"></i>Q-Build <span class="text-xs text-purple-500">EXPLORER</span></a>
            <div class="flex items-center space-x-6">
                <div class="flex items-center space-x-2 text-sm">
                    <i class="fas fa-hdd text-gray-400"></i>
                    <div class="w-32 h-3 bg-gray-700 rounded-full overflow-hidden border border-gray-600">
                        <div class="h-full {{ 'bg-red-500' if disk_pct > 90 else 'bg-green-500' }}" style="width: {{ disk_pct }}%"></div>
                    </div>
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

DASHBOARD_HTML = """
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    {% for name, path in projects.items() %}
    <div class="bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-lg hover:border-blue-500 transition relative group">
        <div class="flex justify-between items-start">
            <div>
                <h3 class="text-xl font-bold mb-1">{{ name }}</h3>
                <p class="text-gray-400 text-xs mb-4 truncate w-48">{{ path }}</p>
            </div>
            {% if states.get(name, {}).get('status') == 'running' %}
            <span class="animate-pulse bg-yellow-600 text-white text-xs px-2 py-1 rounded">BUILDING</span>
            {% elif states.get(name, {}).get('status') == 'done' %}
            <span class="bg-green-600 text-white text-xs px-2 py-1 rounded">SUCCESS</span>
            {% elif states.get(name, {}).get('status') == 'failed' %}
            <span class="bg-red-600 text-white text-xs px-2 py-1 rounded">FAILED</span>
            {% endif %}
        </div>
        <div class="flex justify-between mt-4 items-center">
             {% if states.get(name, {}).get('status') == 'running' %}
                 <a href="/build/{{ name }}" class="bg-yellow-600 hover:bg-yellow-500 px-4 py-2 rounded text-white text-sm w-full text-center"><i class="fas fa-eye mr-1"></i> Logs</a>
             {% else %}
                 <div class="flex space-x-2">
                    <a href="/build/{{ name }}" class="bg-gray-700 hover:bg-gray-600 px-3 py-2 rounded text-white text-sm" title="Build"><i class="fas fa-hammer"></i></a>
                    <a href="/code/{{ name }}/" class="bg-purple-700 hover:bg-purple-600 px-3 py-2 rounded text-white text-sm" title="Source Code"><i class="fas fa-code"></i></a>
                    <a href="/artifacts/{{ name }}" class="bg-blue-600 hover:bg-blue-500 px-3 py-2 rounded text-white text-sm" title="Artifacts"><i class="fas fa-download"></i></a>
                 </div>
                 <a href="/delete/{{ name }}" class="text-red-400 hover:text-red-300 px-3 py-2 opacity-0 group-hover:opacity-100 transition" onclick="return confirm('WARNING: This will delete the ENTIRE project folder from disk.\n\nAre you sure?')"><i class="fas fa-trash"></i></a>
             {% endif %}
        </div>
    </div>
    {% else %}
    <div class="col-span-3 text-center py-20 text-gray-500"><p>No projects found.</p></div>
    {% endfor %}
</div>
"""

EXPLORER_HTML = """
<div class="flex h-[80vh] bg-gray-800 rounded-lg shadow-lg overflow-hidden border border-gray-700">
    <!-- Sidebar -->
    <div class="w-1/4 bg-gray-900 border-r border-gray-700 flex flex-col">
        <div class="p-3 border-b border-gray-700 bg-gray-800 font-bold flex justify-between">
            <span>{{ project }}</span>
            <a href="/" class="text-gray-400 hover:text-white"><i class="fas fa-times"></i></a>
        </div>
        <div class="overflow-y-auto flex-grow p-2 text-sm font-mono">
            {% if parent_dir %}
            <a href="/code/{{ project }}/{{ parent_dir }}" class="block p-1 text-yellow-400 hover:bg-gray-800"><i class="fas fa-level-up-alt mr-2"></i>..</a>
            {% endif %}
            
            {% for d in dirs %}
            <a href="/code/{{ project }}/{{ current_path }}/{{ d }}" class="block p-1 text-blue-400 hover:bg-gray-800 truncate"><i class="fas fa-folder mr-2"></i>{{ d }}</a>
            {% endfor %}
            
            {% for f in files %}
            <a href="/code/{{ project }}/{{ current_path }}/{{ f }}" class="block p-1 text-gray-300 hover:bg-gray-800 truncate"><i class="far fa-file mr-2"></i>{{ f }}</a>
            {% endfor %}
        </div>
    </div>
    
    <!-- Main Content -->
    <div class="w-3/4 flex flex-col bg-[#282c34]">
        <div class="p-2 bg-gray-800 border-b border-gray-700 text-xs text-gray-400 flex justify-between">
            <span>{{ current_path }}</span>
            <span>{{ file_size }}</span>
        </div>
        <div class="flex-grow overflow-auto p-4">
            {% if is_file %}
                <pre><code class="language-{{ ext }}">{{ content }}</code></pre>
            {% else %}
                <div class="flex items-center justify-center h-full text-gray-500">
                    <div class="text-center">
                        <i class="fas fa-code text-6xl mb-4 opacity-20"></i>
                        <p>Select a file to view content</p>
                    </div>
                </div>
            {% endif %}
        </div>
    </div>
</div>
"""

# Reusing previous Wizard Templates (omitted for brevity, they are identical to V4)
CREATE_STEP1_HTML = """<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg"><h2 class="text-2xl font-bold mb-6">Step 1: Project Name</h2><form action="/create_step2" method="POST" class="space-y-4" onsubmit="document.getElementById('btn').innerHTML='<i class=\'fas fa-spinner fa-spin\'></i> Cloning...';"><div><label class="block text-sm text-gray-400 mb-1">Project Name</label><input type="text" name="name" required class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white"></div><button id="btn" type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4">Next <i class="fas fa-arrow-right ml-2"></i></button></form></div>"""
CREATE_STEP2_HTML = """<div class="max-w-2xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg"><h2 class="text-2xl font-bold mb-6">Step 2: Configuration</h2><form action="/finish_create" method="POST" class="space-y-6"><input type="hidden" name="name" value="{{ project }}"><div><label class="block text-sm text-gray-400 mb-1">Target Board</label><select name="board" class="w-full bg-gray-900 border border-gray-600 rounded p-3 text-white">{% for b in boards %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select></div><div><label class="block text-sm text-gray-400 mb-1">Topology</label><div class="flex space-x-4"><label class="flex items-center space-x-2 bg-gray-900 p-3 rounded border border-gray-600 flex-1"><input type="radio" name="topology" value="ASOC" checked> <span>ASOC</span></label><label class="flex items-center space-x-2 bg-gray-900 p-3 rounded border border-gray-600 flex-1"><input type="radio" name="topology" value="AudioReach"> <span>AudioReach</span></label></div></div><button type="submit" class="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold mt-4">Create Project</button></form></div>"""
BUILD_CONSOLE_HTML = """<div class="flex flex-col h-full space-y-4"><div class="bg-gray-800 p-4 rounded-lg shadow flex justify-between items-center"><div><h2 class="text-2xl font-bold">{{ project }}</h2><div class="text-sm text-gray-400 mt-1">Status: <span id="statusBadge" class="font-bold">UNKNOWN</span></div></div><div class="flex space-x-3 items-center"><button onclick="stopBuild()" id="stopBtn" class="hidden bg-red-600 text-white px-6 py-2 rounded">STOP</button><button onclick="startBuild()" id="buildBtn" class="bg-green-600 text-white px-6 py-2 rounded">BUILD</button><a href="/" class="bg-gray-700 px-4 py-2 rounded text-white">Back</a></div></div><div id="terminal" class="flex-grow bg-black rounded h-[600px]"></div></div><script>var socket = io(); var project = '{{ project }}'; var term = new Terminal({theme:{background:'#000',foreground:'#e5e5e5'}}); var fitAddon = new FitAddon.FitAddon(); term.loadAddon(fitAddon); term.open(document.getElementById('terminal')); fitAddon.fit(); socket.emit('join_project', {project:project}); socket.on('log_chunk', function(msg){ term.write(msg.data); }); socket.on('build_status', function(msg){ updateUI(msg.status); }); function updateUI(status){ var b=document.getElementById('buildBtn'); var s=document.getElementById('stopBtn'); document.getElementById('statusBadge').innerText=status.toUpperCase(); if(status=='running'){b.classList.add('hidden'); s.classList.remove('hidden');}else{b.classList.remove('hidden'); s.classList.add('hidden');}} function startBuild(){term.clear(); socket.emit('start_build',{project:project});} function stopBuild(){socket.emit('stop_build',{project:project});}</script>"""

# --- ROUTES ---
@app.route('/')
def index():
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(DASHBOARD_HTML, projects=load_registry(), states=BUILD_STATES))

@app.route('/create')
def create_step1_view():
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=CREATE_STEP1_HTML)

@app.route('/create_step2', methods=['POST'])
def create_step2_action():
    name = request.form['name']
    base_dir = os.path.join(WORK_DIR, "meta-qcom-builds")
    proj_path = os.path.join(base_dir, name)
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
    name = request.form['name']; board = request.form['board']; topo = request.form['topology']
    proj_path = os.path.join(WORK_DIR, "meta-qcom-builds", name)
    cfg = {"kas_files": f"meta-qcom/ci/{board}", "image": "qcom-multimedia-image", "topology": topo}
    with open(os.path.join(proj_path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    reg = load_registry(); reg[name] = proj_path; save_registry(reg)
    return redirect('/')

@app.route('/delete/<name>')
def delete(name):
    reg = load_registry()
    if name in reg:
        path = reg[name]
        # Safety Check: Must be inside /work/meta-qcom-builds
        safe_base = os.path.join(WORK_DIR, "meta-qcom-builds")
        if os.path.commonpath([path, safe_base]) == safe_base:
            try:
                shutil.rmtree(path) # Real deletion
            except Exception as e:
                print(f"Delete failed: {e}")
        del reg[name]
        save_registry(reg)
    return redirect('/')

@app.route('/code/<name>/', defaults={'req_path': ''})
@app.route('/code/<name>/<path:req_path>')
def code_explorer(name, req_path):
    root_path, _ = get_config(name)
    if not root_path: return redirect('/')
    
    # Security: Prevent escaping root
    abs_root = os.path.abspath(root_path)
    abs_req = os.path.abspath(os.path.join(abs_root, req_path))
    if not abs_req.startswith(abs_root): return abort(403)
    
    pct, free = get_disk_usage()
    
    if os.path.isdir(abs_req):
        # List dir
        try:
            items = sorted(os.listdir(abs_req))
        except: items = []
        dirs = [i for i in items if os.path.isdir(os.path.join(abs_req, i)) and not i.startswith('.')]
        files = [i for i in items if os.path.isfile(os.path.join(abs_req, i)) and not i.startswith('.')]
        
        # Calculate parent for ".." link
        parent = os.path.relpath(os.path.dirname(abs_req), abs_root)
        if parent == '.': parent = ''
        if req_path == '': parent = None
        
        return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(EXPLORER_HTML, project=name, current_path=req_path, dirs=dirs, files=files, parent_dir=parent, is_file=False))
    
    elif os.path.isfile(abs_req):
        # Show file content
        try:
            with open(abs_req, 'r', errors='replace') as f:
                content = f.read(100000) # Limit size for performance
        except Exception as e:
            content = f"Error reading file: {e}"
        
        # Determine language for highlighting
        _, ext = os.path.splitext(abs_req)
        ext = ext.lstrip('.')
        if ext in ['yml', 'yaml']: ext = 'yaml'
        elif ext in ['py']: ext = 'python'
        elif ext in ['bb', 'inc', 'conf']: ext = 'bash' # Bitbake looks like bash
        
        # Get Directory listing for sidebar (same as parent dir)
        parent_dir_abs = os.path.dirname(abs_req)
        try:
            items = sorted(os.listdir(parent_dir_abs))
        except: items = []
        dirs = [i for i in items if os.path.isdir(os.path.join(parent_dir_abs, i)) and not i.startswith('.')]
        files = [i for i in items if os.path.isfile(os.path.join(parent_dir_abs, i)) and not i.startswith('.')]
        
        # Rel path for sidebar links
        rel_parent = os.path.relpath(parent_dir_abs, abs_root)
        if rel_parent == '.': rel_parent = ''
        
        return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(EXPLORER_HTML, project=name, current_path=rel_parent, dirs=dirs, files=files, parent_dir=os.path.dirname(rel_parent) if rel_parent else None, is_file=True, content=content, ext=ext, file_size=f"{os.path.getsize(abs_req)} bytes"))
    
    return abort(404)

# Reuse existing build/socket routes (omitted for brevity, assume standard V4 logic here)
@app.route('/build/<name>')
def build_page(name): return render_template_string(BASE_HTML, disk_pct=0, disk_free=0, body_content=render_template_string(BUILD_CONSOLE_HTML, project=name))
@app.route('/artifacts/<name>')
def view_artifacts(name): return "Artifacts Placeholder"

# --- SOCKET ---
@socketio.on('join_project')
def handle_join(data):
    join_room(data['project'])
    if data['project'] in BUILD_STATES: emit('log_chunk', {'data': "".join(BUILD_STATES[data['project']]['logs'])})
    
@socketio.on('start_build')
def handle_build(data):
    name = data['project']
    path, cfg = get_config(name)
    cmd = f"kas shell {cfg.get('kas_files')} -c 'bitbake {cfg.get('image')}'"
    BUILD_STATES[name] = {'status': 'running', 'logs': [], 'pid': None}
    emit('build_status', {'status': 'running'}, to=name)
    master, slave = pty.openpty()
    p = subprocess.Popen(cmd, shell=True, cwd=path, stdout=slave, stderr=slave, preexec_fn=os.setsid, executable='/bin/bash')
    os.close(slave)
    BUILD_STATES[name]['pid'] = p.pid
    def read_output():
        while True:
            try:
                data = os.read(master, 1024); 
                if not data: break
                d=data.decode(errors='ignore'); BUILD_STATES[name]['logs'].append(d); socketio.emit('log_chunk', {'data': d}, to=name)
            except: break
        p.wait()
        socketio.emit('build_status', {'status': 'done' if p.returncode==0 else 'failed'}, to=name)
    threading.Thread(target=read_output).start()

@socketio.on('stop_build')
def handle_stop(data):
    name = data['project']
    if name in BUILD_STATES and BUILD_STATES[name]['pid']:
        try: os.killpg(os.getpgid(BUILD_STATES[name]['pid']), signal.SIGTERM)
        except: pass
    emit('build_status', {'status': 'stopped'}, to=name)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, allow_unsafe_werkzeug=True)
