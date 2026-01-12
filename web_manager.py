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
import datetime
from flask import Flask, render_template_string, request, redirect, abort, jsonify
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

SERVER_PORT = int(os.environ.get("WEB_PORT", 5000))
WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")
BUILD_DIR_BASE = os.path.join(WORK_DIR, "meta-qcom-builds")
TOOLS_DIR = os.path.join(WORK_DIR, "common_tools")
BUILD_STATES = {}

# --- HELPERS ---
def get_disk_usage():
    try:
        total, used, free = shutil.disk_usage(WORK_DIR)
        return int((used / total) * 100), int(free // (2**30))
    except: return 0, 0

def ensure_tools():
    """Ensure common tools (mkbootimg, initramfs, linux-firmware) exist."""
    if not os.path.exists(TOOLS_DIR): os.makedirs(TOOLS_DIR, exist_ok=True)
    
    # 1. mkbootimg
    if not os.path.exists(os.path.join(TOOLS_DIR, "mkbootimg")):
        subprocess.run(["git", "clone", "--depth", "1", "https://android.googlesource.com/platform/system/tools/mkbootimg", os.path.join(TOOLS_DIR, "mkbootimg")])
        
    # 2. initramfs
    initramfs_path = os.path.join(TOOLS_DIR, "initramfs-test.cpio.gz")
    if not os.path.exists(initramfs_path):
        subprocess.run(["wget", "https://snapshots.linaro.org/member-builds/qcomlt/testimages/arm64/1379/initramfs-test-image-qemuarm64-20230321073831-1379.rootfs.cpio.gz", "-O", initramfs_path])

    # 3. linux-firmware (Shared)
    fw_path = os.path.join(TOOLS_DIR, "linux-firmware")
    if not os.path.exists(fw_path):
        subprocess.run(["git", "clone", "--depth", "1", "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git", fw_path])

def sync_registry():
    if not os.path.exists(REGISTRY_FILE): reg = {}
    else:
        try:
            with open(REGISTRY_FILE, "r") as f: reg = yaml.safe_load(f) or {}
        except: reg = {}
    
    if not os.path.exists(BUILD_DIR_BASE): os.makedirs(BUILD_DIR_BASE, exist_ok=True)
    found = [d for d in os.listdir(BUILD_DIR_BASE) if os.path.isdir(os.path.join(BUILD_DIR_BASE, d))]
    updated = False
    
    for p in found:
        path = os.path.join(BUILD_DIR_BASE, p)
        if p not in reg: 
            # Default to Yocto if unknown, but try to detect
            ptype = 'yocto'
            if os.path.exists(os.path.join(path, 'linux')): ptype = 'upstream'
            reg[p] = {'path': path, 'type': ptype, 'created': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'modified': 'Unknown'}
            updated = True
            
    for n, data in reg.items():
        if isinstance(data, dict) and os.path.exists(data.get('path', '')):
            try:
                mtime = os.path.getmtime(data['path'])
                reg[n]['modified'] = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
            except: pass
            
    if updated:
        with open(REGISTRY_FILE, "w") as f: yaml.dump(reg, f)
    return reg

def get_config(project_name):
    reg = sync_registry()
    data = reg.get(project_name)
    if not data: return None, None
    path = data['path']
    cfg_path = os.path.join(path, "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f: return path, yaml.safe_load(f)
    return path, {}

def background_delete(path, name):
    try: shutil.rmtree(path)
    except: pass

def run_build_task(cmd, name):
    BUILD_STATES[name] = {'status': 'running', 'logs': [], 'pid': None}
    socketio.emit('build_status', {'status': 'running'}, to=name)
    path, _ = get_config(name)
    
    master, slave = pty.openpty()
    p = subprocess.Popen(cmd, shell=True, cwd=path, stdout=slave, stderr=slave, preexec_fn=os.setsid, executable='/bin/bash')
    os.close(slave)
    BUILD_STATES[name]['pid'] = p.pid

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

# --- HTML TEMPLATES ---
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Q-Build Manager V26</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@4.19.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@4.19.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.5.0/lib/xterm-addon-fit.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/styles/atom-one-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/highlight.min.js"></script>
    <style>
        .nav-token { cursor: pointer; border-bottom: 1px dotted rgba(255,255,255,0.2); }
        .nav-token:hover { background-color: rgba(59, 130, 246, 0.3); color: #60a5fa !important; border-bottom: 1px solid #60a5fa; }
        .hljs { background: transparent; padding: 0; } 
        .code-container { display: flex; font-family: 'Fira Code', monospace; line-height: 1.5; font-size: 13px; }
        .line-numbers { text-align: right; padding-right: 15px; color: #6b7280; user-select: none; border-right: 1px solid #374151; margin-right: 15px; min-width: 40px; }
        .code-content { flex-grow: 1; overflow-x: auto; }
        textarea.editor { width: 100%; height: 100%; background: #1f2937; color: #e5e7eb; font-family: 'Fira Code', monospace; font-size: 13px; border: none; outline: none; resize: none; line-height: 1.5; padding: 0; }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 font-sans min-h-screen flex flex-col">
    <nav class="bg-gray-800 p-4 border-b border-gray-700">
        <div class="container mx-auto flex justify-between items-center">
            <a href="/" class="text-2xl font-bold text-blue-400"><i class="fas fa-microchip mr-2"></i>Q-Build <span class="text-xs text-white font-bold bg-green-600 px-1 rounded">V26 PRO</span></a>
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
    <div class="w-1/5 bg-gray-900 border-r border-gray-700 flex flex-col">
        <div class="p-3 border-b border-gray-700 bg-gray-800 font-bold flex justify-between"><span>{{ project }}</span><a href="/build/{{ project }}" class="text-xs bg-gray-700 px-2 py-1 rounded hover:bg-gray-600">Back</a></div>
        <div class="overflow-y-auto flex-grow p-2 text-sm font-mono">
            {% if parent_dir %}<a href="/code/{{ project }}/{{ parent_dir }}" class="block p-1 text-yellow-400 hover:bg-gray-800"><i class="fas fa-level-up-alt mr-2"></i>..</a>{% endif %}
            {% for d in dirs %}<a href="/code/{{ project }}/{{ current_path }}/{{ d }}" class="block p-1 text-blue-400 hover:bg-gray-800 truncate"><i class="fas fa-folder mr-2"></i>{{ d }}</a>{% endfor %}
            {% for f in files %}<a href="/code/{{ project }}/{{ current_path }}/{{ f }}" class="block p-1 text-gray-300 hover:bg-gray-800 truncate"><i class="far fa-file mr-2"></i>{{ f }}</a>{% endfor %}
        </div>
    </div>
    <div class="w-4/5 flex flex-col bg-[#282c34] relative">
        <div class="p-2 bg-gray-800 border-b border-gray-700 text-xs text-gray-400 flex justify-between items-center">
            <span class="font-mono text-blue-300">{{ current_path }}</span>
            <div class="flex space-x-2">
                {% if is_file %}
                <button id="editBtn" onclick="enableEdit()" class="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded text-white text-xs"><i class="fas fa-pen mr-1"></i> Edit</button>
                <button id="saveBtn" onclick="saveFile()" class="hidden bg-green-600 hover:bg-green-500 px-3 py-1 rounded text-white text-xs"><i class="fas fa-save mr-1"></i> Save</button>
                <button id="cancelBtn" onclick="location.reload()" class="hidden bg-gray-600 hover:bg-gray-500 px-3 py-1 rounded text-white text-xs">Cancel</button>
                {% endif %}
            </div>
        </div>
        <div class="flex-grow overflow-auto p-4 relative" id="codeContainer">
            {% if is_file %}
            <div class="code-container" id="readView">
                <div class="line-numbers">{% for i in range(1, line_count + 1) %}<div>{{ i }}</div>{% endfor %}</div>
                <div class="code-content"><pre><code class="language-{{ ext }}" id="codeBlock">{{ content }}</code></pre></div>
            </div>
            <div class="code-container hidden h-full" id="editView">
                <div class="line-numbers">{% for i in range(1, line_count + 1) %}<div>{{ i }}</div>{% endfor %}</div>
                <div class="code-content h-full"><textarea id="fileEditor" class="editor" spellcheck="false">{{ content }}</textarea></div>
            </div>
            {% else %}
            <div class="flex items-center justify-center h-full text-gray-500"><p>Select a file to view content</p></div>
            {% endif %}
        </div>
    </div>
</div>
<script>
    hljs.highlightAll();
    function enableEdit() { document.getElementById('readView').classList.add('hidden'); document.getElementById('editView').classList.remove('hidden'); document.getElementById('editBtn').classList.add('hidden'); document.getElementById('saveBtn').classList.remove('hidden'); document.getElementById('cancelBtn').classList.remove('hidden'); }
    function saveFile() {
        var content = document.getElementById('fileEditor').value;
        var btn = document.getElementById('saveBtn'); btn.innerHTML = 'Saving...';
        fetch('/save_file', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ project: '{{ project }}', path: '{{ current_path }}', content: content }) }).then(r => r.json()).then(data => { if(data.status === 'ok') location.reload(); else { alert('Error: ' + data.error); btn.innerHTML = 'Save'; } });
    }
</script>
"""

DASHBOARD_HTML = """<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">{% for name, data in projects.items() %}{% if states.get(name, {}).get('status') != 'deleting' %}<div class="bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-lg relative group"><h3 class="text-xl font-bold mb-1">{{ name }}</h3><div class="flex justify-between"><p class="text-gray-400 text-xs mb-2 truncate">{{ data.path }}</p><span class="text-xs px-2 py-0.5 rounded {{ 'bg-blue-900 text-blue-200' if data.get('type')=='upstream' else 'bg-yellow-900 text-yellow-200' }}">{{ data.get('type', 'yocto').upper() }}</span></div><div class="text-xs text-gray-500 mb-4"><p><i class="far fa-clock"></i> {{ data.created }}</p></div><div class="flex justify-between items-center mt-4"><div class="flex space-x-2"><a href="/build/{{ name }}" class="bg-green-700 hover:bg-green-600 px-3 py-2 rounded text-white text-sm"><i class="fas fa-hammer"></i> Build</a><a href="/code/{{ name }}/" class="bg-purple-700 hover:bg-purple-600 px-3 py-2 rounded text-white text-sm"><i class="fas fa-code"></i></a></div><a href="/delete/{{ name }}" class="text-red-400 hover:text-red-300 px-3 py-2 opacity-0 group-hover:opacity-100 transition" onclick="return confirm('Delete {{ name }} permanently?')"><i class="fas fa-trash"></i></a></div><div class="absolute top-4 right-4 h-3 w-3 rounded-full {{ 'bg-yellow-500 animate-pulse' if states.get(name, {}).get('status') == 'running' else 'bg-green-500' if states.get(name, {}).get('status') == 'done' else 'bg-gray-600' }}"></div></div>{% endif %}{% else %}<div class="col-span-3 text-center py-20 text-gray-500"><p>No projects found.</p></div>{% endfor %}</div>"""

BUILD_CONSOLE_HTML = """
<div class="flex flex-col h-full space-y-4">
    <div class="bg-gray-800 p-4 rounded-lg shadow flex justify-between items-center">
        <div><h2 class="text-2xl font-bold">{{ project }}</h2><div class="text-sm text-gray-400 mt-1 flex items-center gap-2"><span class="px-2 py-0.5 rounded bg-gray-700 text-white text-xs">{{ type.upper() }}</span> Status: <span id="statusBadge" class="font-bold">IDLE</span></div></div>
        
        {% if type == 'yocto' %}
        <!-- YOCTO CONTROLS -->
        <div class="flex items-center space-x-4 bg-gray-900 p-2 rounded border border-gray-700" id="topoControl">
            <label class="text-sm text-gray-400 font-bold mr-2">Topology:</label>
            <label class="inline-flex items-center cursor-pointer"><input type="radio" name="topo" value="ASOC" class="form-radio text-blue-600" checked><span class="ml-2 text-sm">ASOC</span></label>
            <label class="inline-flex items-center cursor-pointer"><input type="radio" name="topo" value="AudioReach" class="form-radio text-blue-600"><span class="ml-2 text-sm">AudioReach</span></label>
        </div>
        <div class="flex items-center space-x-2 bg-gray-900 p-1 rounded border border-gray-700">
             <select id="cleanType" class="bg-gray-800 text-white text-sm border-none rounded p-1"><option value="clean">Quick Clean</option><option value="cleanall">Deep Clean</option></select>
             <button onclick="runClean()" class="bg-orange-800 hover:bg-orange-700 px-3 py-1 rounded text-white text-sm"><i class="fas fa-broom"></i></button>
        </div>
        {% else %}
        <!-- UPSTREAM CONTROLS -->
        <div class="flex items-center space-x-4 bg-gray-900 p-2 rounded border border-gray-700">
             <div class="flex flex-col">
                 <label class="text-xs text-gray-400">Firmware Target</label>
                 <select id="fwTarget" class="bg-gray-800 text-white text-xs border border-gray-600 rounded p-1 w-32"><option value="loading">Loading...</option></select>
             </div>
             <div class="flex flex-col">
                 <label class="text-xs text-gray-400">DTB Name</label>
                 <input type="text" id="dtbName" value="lemans-evk.dtb" class="bg-gray-800 text-white text-xs border border-gray-600 rounded p-1 w-32">
             </div>
             <button onclick="scanFw()" class="text-gray-400 hover:text-white"><i class="fas fa-sync"></i></button>
        </div>
        {% endif %}

        <div class="flex space-x-3 items-center">
            <a href="/code/{{ project }}/" target="_blank" class="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded text-white"><i class="fas fa-external-link-alt mr-1"></i> Code</a>
            <button onclick="stopBuild()" id="stopBtn" class="hidden bg-red-600 text-white px-6 py-2 rounded">STOP</button>
            <button onclick="startBuild()" id="buildBtn" class="bg-green-600 text-white px-6 py-2 rounded"><i class="fas fa-play mr-1"></i> Build</button>
            <a href="/" class="bg-gray-700 px-4 py-2 rounded text-white">Back</a>
        </div>
    </div>
    
    {% if type == 'yocto' %}
    <div class="bg-gray-800 p-4 rounded-lg shadow border-l-4 border-yellow-500">
        <h3 class="text-lg font-bold mb-2 text-yellow-500"><i class="fas fa-tools mr-2"></i>Kernel Dev Kit</h3>
        <div class="flex items-center space-x-4">
            <div class="flex-grow relative">
                <input type="text" id="recipeName" value="linux-qcom-next" list="common_recipes" class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white font-mono" placeholder="Recipe Name">
                <datalist id="common_recipes"><option value="linux-qcom-next"><option value="audioreach-kernel"></datalist>
            </div>
            <button onclick="runDevtool('modify')" class="bg-blue-700 hover:bg-blue-600 text-white px-4 py-2 rounded text-sm"><i class="fas fa-edit mr-1"></i> Modify</button>
            <button onclick="runDevtool('reset')" class="bg-red-900 hover:bg-red-800 text-white px-4 py-2 rounded text-sm"><i class="fas fa-undo mr-1"></i> Reset</button>
        </div>
    </div>
    {% endif %}

    <div id="terminal" class="flex-grow bg-black rounded h-[500px]"></div>
</div>

<script>
    var socket = io(); var project = '{{ project }}'; var ptype = '{{ type }}';
    var term = new Terminal({theme:{background:'#000',foreground:'#e5e5e5'}}); 
    var fitAddon = new FitAddon.FitAddon(); term.loadAddon(fitAddon); term.open(document.getElementById('terminal')); fitAddon.fit(); 

    socket.on('connect', function() { socket.emit('join_project', {project: project}); if(ptype=='upstream') socket.emit('scan_fw', {}); });
    socket.on('log_chunk', function(msg){ term.write(msg.data); });
    socket.on('build_status', function(msg){ updateUI(msg.status); });
    socket.on('fw_list', function(msg){
        var sel = document.getElementById('fwTarget'); sel.innerHTML = '';
        msg.targets.forEach(t => { var opt = document.createElement('option'); opt.value = t; opt.text = t; if(t.includes('8775')) opt.selected=true; sel.appendChild(opt); });
    });

    function updateUI(status){ 
        var b=document.getElementById('buildBtn'); var s=document.getElementById('stopBtn'); 
        document.getElementById('statusBadge').innerText=status.toUpperCase(); 
        if(status=='running'){ b.classList.add('hidden'); s.classList.remove('hidden'); } 
        else { b.classList.remove('hidden'); s.classList.add('hidden'); }
    } 
    function startBuild(){ 
        term.clear(); 
        if(ptype == 'yocto') {
            var topo = document.querySelector('input[name="topo"]:checked').value; 
            socket.emit('start_build',{project:project, topology: topo}); 
        } else {
            var fw = document.getElementById('fwTarget').value;
            var dtb = document.getElementById('dtbName').value;
            socket.emit('start_build', {project: project, fw_target: fw, dtb: dtb});
        }
    } 
    function stopBuild(){ socket.emit('stop_build',{project:project}); }
    function runClean() { if(confirm("Clean build artifacts?")) { term.clear(); socket.emit('clean_build', {project: project, type: document.getElementById('cleanType').value}); } }
    function runDevtool(action) { var r = document.getElementById('recipeName').value; if(confirm(action.toUpperCase() + " " + r + "?")) { term.clear(); socket.emit('devtool_action', {project: project, action: action, recipe: r}); } }
    function scanFw() { socket.emit('scan_fw', {}); }
</script>
"""

CREATE_STEP1_HTML = """
<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Step 1: Project Setup</h2>
    <form action="/create_step2" method="POST" class="space-y-4">
        <div><label class="block text-sm text-gray-400 mb-1">Project Name</label><input type="text" name="name" required class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white"></div>
        <div>
            <label class="block text-sm text-gray-400 mb-1">Build System</label>
            <div class="grid grid-cols-2 gap-4">
                <label class="cursor-pointer border border-gray-600 rounded p-4 hover:bg-gray-700 flex flex-col items-center">
                    <input type="radio" name="type" value="yocto" checked class="mb-2">
                    <span class="font-bold text-yellow-400">Yocto (KAS)</span>
                    <span class="text-xs text-gray-500 text-center">Full OS Image (Meta-Qualcomm)</span>
                </label>
                <label class="cursor-pointer border border-gray-600 rounded p-4 hover:bg-gray-700 flex flex-col items-center">
                    <input type="radio" name="type" value="upstream" class="mb-2">
                    <span class="font-bold text-blue-400">Upstream Kernel</span>
                    <span class="text-xs text-gray-500 text-center">Kernel.org + Firmware + BootImg</span>
                </label>
            </div>
        </div>
        <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4">Next <i class="fas fa-arrow-right ml-2"></i></button>
    </form>
</div>
"""

CREATE_STEP2_HTML = """
<div class="max-w-2xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Step 2: Configuration</h2>
    <form action="/finish_create" method="POST" class="space-y-6">
        <input type="hidden" name="name" value="{{ project }}">
        <input type="hidden" name="type" value="{{ type }}">
        
        {% if type == 'yocto' %}
        <div>
            <label class="block text-sm text-gray-400 mb-1">Yocto Target Board</label>
            <select name="board" class="w-full bg-gray-900 border border-gray-600 rounded p-3 text-white">{% for b in boards %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select>
        </div>
        {% else %}
        <div>
            <label class="block text-sm text-gray-400 mb-1">Kernel Repository</label>
            <select name="kernel_repo" class="w-full bg-gray-900 border border-gray-600 rounded p-3 text-white">
                <option value="git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git">Linux Stable (torvalds/linux.git)</option>
                <option value="git://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git">Linux Next (next/linux-next.git)</option>
            </select>
            <p class="text-xs text-gray-500 mt-2"><i class="fas fa-info-circle"></i> Firmware selection happens at build time.</p>
        </div>
        {% endif %}
        
        <button type="submit" class="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold mt-4">Create Project</button>
    </form>
</div>
"""

# --- ROUTES ---
@app.route('/')
def index():
    threading.Thread(target=ensure_tools).start() # Trigger tool check in bg
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
    ptype = request.form['type']
    proj_path = os.path.join(BUILD_DIR_BASE, name)
    os.makedirs(proj_path, exist_ok=True)
    
    boards = []
    if ptype == 'yocto':
        repo_path = os.path.join(proj_path, "meta-qcom")
        if not os.path.exists(repo_path): subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", repo_path], check=True)
        ci_path = os.path.join(repo_path, "ci")
        boards = [f for f in os.listdir(ci_path) if f.endswith('.yml')] if os.path.exists(ci_path) else []
        boards.sort()
        
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(CREATE_STEP2_HTML, project=name, type=ptype, boards=boards))

@app.route('/finish_create', methods=['POST'])
def finish_create():
    name = request.form['name']
    ptype = request.form['type']
    proj_path = os.path.join(BUILD_DIR_BASE, name)
    
    cfg = {'type': ptype, 'created': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
    
    if ptype == 'yocto':
        cfg['kas_files'] = f"meta-qcom/ci/{request.form['board']}"
        cfg['image'] = "qcom-multimedia-image"
    else:
        cfg['kernel_repo'] = request.form['kernel_repo']
        
    with open(os.path.join(proj_path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    sync_registry()
    return redirect('/')

@app.route('/delete/<name>')
def delete(name):
    reg = sync_registry()
    if name in reg:
        path = reg[name]['path']
        BUILD_STATES[name] = {'status': 'deleting'}
        threading.Thread(target=background_delete, args=(path, name)).start()
        del reg[name]
        with open(REGISTRY_FILE, "w") as f: yaml.dump(reg, f)
    return redirect('/')

@app.route('/code/<name>/', defaults={'req_path': ''})
@app.route('/code/<name>/<path:req_path>')
def code_explorer(name, req_path):
    # (Existing Logic - Preserved)
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
            line_count = content.count('\n') + 1
            parent_dir_abs = os.path.dirname(abs_req)
            try: items = sorted(os.listdir(parent_dir_abs))
            except: items = []
            dirs = [i for i in items if os.path.isdir(os.path.join(parent_dir_abs, i)) and not i.startswith('.')]
            files = [i for i in items if os.path.isfile(os.path.join(parent_dir_abs, i)) and not i.startswith('.')]
            rel_parent = os.path.relpath(parent_dir_abs, abs_root)
            if rel_parent == '.': rel_parent = ''
            return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(EXPLORER_HTML, project=name, current_path=rel_parent, dirs=dirs, files=files, parent_dir=os.path.dirname(rel_parent) if rel_parent else None, is_file=True, content=content, ext=ext, line_count=line_count))
    except Exception as e: return f"Explorer Error: {str(e)}", 500
    return abort(404)

@app.route('/save_file', methods=['POST'])
def save_file_endpoint():
    data = request.get_json()
    name = data.get('project')
    rel_path = data.get('path')
    content = data.get('content')
    path, _ = get_config(name)
    if not path: return jsonify({'error': 'Project not found'}), 404
    try:
        with open(os.path.join(path, rel_path), 'w') as f: f.write(content)
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/build/<name>')
def build_page(name): 
    pct, free = get_disk_usage()
    path, cfg = get_config(name)
    ptype = cfg.get('type', 'yocto')
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(BUILD_CONSOLE_HTML, project=name, type=ptype))

# --- SOCKET EVENTS ---
@socketio.on('join_project')
def handle_join(data):
    join_room(data['project'])
    name = data['project']
    if name in BUILD_STATES: 
        if 'logs' in BUILD_STATES[name]: emit('log_chunk', {'data': "".join(BUILD_STATES[name]['logs'])})
        emit('build_status', {'status': BUILD_STATES[name].get('status', 'unknown')})

@socketio.on('start_build')
def handle_build(data):
    name = data['project']
    path, cfg = get_config(name)
    ptype = cfg.get('type', 'yocto')

    if ptype == 'yocto':
        # --- YOCTO FLOW ---
        topo = data.get('topology', 'ASOC')
        cfg['topology'] = topo
        with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
        distro = 'meta-qcom/ci/qcom-distro-prop-image.yml' if topo == 'AudioReach' else 'meta-qcom/ci/qcom-distro.yml'
        kas_args = f"{cfg.get('kas_files')}:{distro}"
        cmd = f"kas shell {kas_args} -c 'bitbake {cfg.get('image')}'"
        threading.Thread(target=run_build_task, args=(cmd, name)).start()
    else:
        # --- UPSTREAM FLOW ---
        fw_target = data.get('fw_target', 'sa8775p')
        dtb_name = data.get('dtb', 'lemans-evk.dtb')
        repo = cfg.get('kernel_repo')
        
        # Paths
        linux_dir = os.path.join(path, "linux")
        mod_dir = os.path.join(linux_dir, "modules_dir")
        fw_dir = os.path.join(linux_dir, "firmwares_dir")
        
        # Tools paths
        mkboot = os.path.join(TOOLS_DIR, "mkbootimg", "mkbootimg.py")
        initramfs = os.path.join(TOOLS_DIR, "initramfs-test.cpio.gz")
        fw_src = os.path.join(TOOLS_DIR, "linux-firmware", "qcom", fw_target)
        
        # Bash Script Construction
        script = [
            f"echo '--- UPSTREAM BUILD STARTED FOR {name} ---'",
            f"echo 'Target Firmware: {fw_target}'",
            f"echo 'DTB: {dtb_name}'",
            
            # 1. Clone
            f"if [ ! -d 'linux' ]; then echo '>> Cloning Kernel...'; git clone --depth 1 {repo} linux; fi",
            "cd linux",
            "mkdir -p modules_dir firmwares_dir test_utils",
            
            # 2. Build Kernel
            "export ARCH=arm64",
            "export CROSS_COMPILE=aarch64-linux-gnu-",
            "echo '>> Configuring...'",
            "make -j$(nproc) defconfig",
            "echo '>> Compiling Image & Modules...'",
            "make -j$(nproc) Image.gz dtbs modules",
            
            # 3. Install Modules
            "echo '>> Installing Modules...'",
            "make -j$(nproc) modules_install INSTALL_MOD_PATH=modules_dir INSTALL_MOD_STRIP=1",
            "cd modules_dir",
            "find . | cpio -o -H newc | gzip -9 > ../modules.cpio.gz",
            "cd ..",
            
            # 4. Prepare Firmware
            "echo '>> Packaging Firmware...'",
            f"mkdir -p firmwares_dir/lib/firmware/qcom/{fw_target}",
            f"if [ -d '{fw_src}' ]; then cp -r {fw_src}/* firmwares_dir/lib/firmware/qcom/{fw_target}/; else echo 'WARNING: Firmware source not found'; fi",
            "cd firmwares_dir",
            "find . | cpio -o -H newc | gzip -9 > ../firmwares.cpio.gz",
            "cd ..",
            
            # 5. Final RootFS
            "echo '>> Creating Final Initramfs...'",
            # Create dummy test_utils if missing to satisfy cat
            "touch test_utils.cpio.gz", 
            f"cat {initramfs} modules.cpio.gz firmwares.cpio.gz test_utils.cpio.gz > final-initramfs.cpio.gz",
            
            # 6. Mkbootimg
            "echo '>> Generating Boot Image...'",
            f"python3 {mkboot} --kernel arch/arm64/boot/Image.gz --cmdline 'root=/dev/ram0 console=tty0 console=ttyMSM0,115200n8 clk_ignore_unused pd_ignore_unused' --ramdisk final-initramfs.cpio.gz --dtb arch/arm64/boot/dts/qcom/{dtb_name} --pagesize 2048 --header_version 2 --output ../boot-rb8.img",
            
            "echo '--- SUCCESS: boot-rb8.img created ---'"
        ]
        
        full_cmd = " && ".join(script)
        threading.Thread(target=run_build_task, args=(full_cmd, name)).start()

@socketio.on('clean_build')
def handle_clean(data):
    name = data['project']
    clean_type = data.get('type', 'clean') 
    path, cfg = get_config(name)
    topo = cfg.get('topology', 'ASOC')
    distro = 'meta-qcom/ci/qcom-distro-prop-image.yml' if topo == 'AudioReach' else 'meta-qcom/ci/qcom-distro.yml'
    kas_args = f"{cfg.get('kas_files')}:{distro}"
    cmd = f"kas shell {kas_args} -c 'bitbake -c {clean_type} {cfg.get('image')}'"
    threading.Thread(target=run_build_task, args=(cmd, name)).start()

@socketio.on('devtool_action')
def handle_devtool(data):
    name = data['project']; action = data['action']; recipe = data['recipe']
    path, cfg = get_config(name)
    topo = cfg.get('topology', 'ASOC')
    distro = 'meta-qcom/ci/qcom-distro-prop-image.yml' if topo == 'AudioReach' else 'meta-qcom/ci/qcom-distro.yml'
    kas_args = f"{cfg.get('kas_files')}:{distro}"
    if action == 'modify': cmd = f"kas shell {kas_args} -c 'bitbake {recipe}; devtool modify {recipe}'"
    else: cmd = f"kas shell {kas_args} -c 'devtool {action} {recipe}'"
    threading.Thread(target=run_build_task, args=(cmd, name)).start()

@socketio.on('stop_build')
def handle_stop(data):
    name = data['project']
    if name in BUILD_STATES and BUILD_STATES[name]['pid']:
        try: os.killpg(os.getpgid(BUILD_STATES[name]['pid']), signal.SIGTERM)
        except: pass
    emit('build_status', {'status': 'stopped'}, to=name)

@socketio.on('scan_fw')
def handle_scan_fw(data):
    # Lists subdirectories in tools/linux-firmware/qcom
    fw_base = os.path.join(TOOLS_DIR, "linux-firmware", "qcom")
    targets = []
    if os.path.exists(fw_base):
        targets = [d for d in os.listdir(fw_base) if os.path.isdir(os.path.join(fw_base, d))]
    else:
        targets = ['sa8775p', 'sm8550', 'sc8280xp'] # Fallback if not yet cloned
    socketio.emit('fw_list', {'targets': sorted(targets)})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, allow_unsafe_werkzeug=True)
