import os
import yaml
import glob
import subprocess
import pty
import threading
import signal
import shutil
from flask import Flask, render_template_string, request, redirect, jsonify, send_from_directory
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
    percent = (used / total) * 100
    return int(percent), int(free // (2**30)) # GB

def find_artifacts(project_path):
    # Try to find the deploy/images directory
    # Common path: <project>/build/tmp/deploy/images/
    # We search recursively for a folder named 'images' to be safe
    for root, dirs, files in os.walk(project_path):
        if "tmp/deploy/images" in root:
            return root
    return None

# --- HTML TEMPLATES ---
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Q-Build Manager Pro</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@4.19.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@4.19.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.5.0/lib/xterm-addon-fit.js"></script>
    <style> .xterm-viewport { overflow-y: auto !important; } </style>
</head>
<body class="bg-gray-900 text-gray-100 font-sans min-h-screen flex flex-col">
    <nav class="bg-gray-800 p-4 border-b border-gray-700">
        <div class="container mx-auto flex justify-between items-center">
            <a href="/" class="text-2xl font-bold text-blue-400"><i class="fas fa-microchip mr-2"></i>Q-Build Manager <span class="text-xs text-yellow-500">PRO</span></a>
            <div class="flex items-center space-x-6">
                <!-- Disk Usage Widget -->
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
                 <a href="/build/{{ name }}" class="bg-yellow-600 hover:bg-yellow-500 px-4 py-2 rounded text-white text-sm w-full text-center">
                    <i class="fas fa-eye mr-1"></i> View Logs
                 </a>
             {% else %}
                 <div class="flex space-x-2">
                    <a href="/build/{{ name }}" class="bg-gray-700 hover:bg-gray-600 px-3 py-2 rounded text-white text-sm">
                        <i class="fas fa-hammer"></i>
                    </a>
                    <a href="/artifacts/{{ name }}" class="bg-blue-600 hover:bg-blue-500 px-3 py-2 rounded text-white text-sm">
                        <i class="fas fa-download"></i> Artifacts
                    </a>
                 </div>
                 <a href="/delete/{{ name }}" class="text-red-400 hover:text-red-300 px-3 py-2 opacity-0 group-hover:opacity-100 transition" onclick="return confirm('Delete?')">
                    <i class="fas fa-trash"></i>
                 </a>
             {% endif %}
        </div>
    </div>
    {% else %}
    <div class="col-span-3 text-center py-20 text-gray-500">
        <p>No projects found.</p>
    </div>
    {% endfor %}
</div>
"""

ARTIFACTS_HTML = """
<div class="bg-gray-800 p-6 rounded-lg shadow-lg">
    <div class="flex justify-between items-center mb-6">
        <h2 class="text-2xl font-bold"><i class="fas fa-box-open text-blue-400 mr-2"></i> Artifacts: {{ project }}</h2>
        <a href="/" class="bg-gray-700 px-4 py-2 rounded">Back</a>
    </div>
    {% if not files %}
        <div class="p-4 bg-yellow-900 text-yellow-100 rounded">No artifacts found. Build might have failed or path is invalid.</div>
    {% else %}
        <ul class="space-y-2">
        {% for f in files %}
            <li class="flex justify-between items-center bg-gray-900 p-3 rounded hover:bg-gray-700">
                <span class="font-mono text-sm text-green-400">{{ f }}</span>
                <a href="/download/{{ project }}/{{ f }}" class="bg-gray-600 hover:bg-green-600 px-3 py-1 rounded text-xs"><i class="fas fa-download"></i> Download</a>
            </li>
        {% endfor %}
        </ul>
    {% endif %}
</div>
"""

BUILD_CONSOLE_HTML = """
<div class="flex flex-col h-full space-y-4">
    <div class="bg-gray-800 p-4 rounded-lg shadow flex justify-between items-center">
        <div>
            <h2 class="text-2xl font-bold">{{ project }}</h2>
            <div class="text-sm text-gray-400 mt-1">Status: <span id="statusBadge" class="font-bold">UNKNOWN</span></div>
        </div>
        <div class="flex space-x-4 items-center">
            <button onclick="stopBuild()" id="stopBtn" class="hidden bg-red-600 hover:bg-red-500 text-white px-6 py-2 rounded shadow"><i class="fas fa-stop"></i> STOP BUILD</button>
            <button onclick="startBuild()" id="buildBtn" class="bg-green-600 hover:bg-green-500 text-white px-6 py-2 rounded shadow"><i class="fas fa-play"></i> Start Build</button>
            <a href="/" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded text-white">Back</a>
        </div>
    </div>
    <div class="bg-gray-800 rounded-full h-5 w-full overflow-hidden border border-gray-700 relative">
        <div id="progressBar" class="bg-blue-600 h-full w-0 transition-all duration-300"></div>
        <span id="progressText" class="absolute inset-0 flex items-center justify-center text-xs font-bold text-white drop-shadow-md">0%</span>
    </div>
    <div id="terminal" class="flex-grow bg-black rounded shadow-lg border border-gray-700 overflow-hidden h-[600px]"></div>
</div>

<script>
    var socket = io();
    var project = '{{ project }}';
    var term = new Terminal({ theme: { background: '#000000', foreground: '#e5e5e5' } });
    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById('terminal'));
    fitAddon.fit();
    window.onresize = () => fitAddon.fit();

    socket.emit('join_project', {project: project});

    socket.on('log_chunk', function(msg) {
        term.write(msg.data);
        const match = msg.data.match(/Tasks:\\s+(\\d+)\\s+of\\s+(\\d+)/);
        if (match) {
            const pct = Math.round((parseInt(match[1]) / parseInt(match[2])) * 100);
            document.getElementById('progressBar').style.width = pct + '%';
            document.getElementById('progressText').innerText = pct + '%';
        }
    });
    
    socket.on('build_status', function(msg) {
        const buildBtn = document.getElementById('buildBtn');
        const stopBtn = document.getElementById('stopBtn');
        const badge = document.getElementById('statusBadge');
        
        badge.innerText = msg.status.toUpperCase();
        
        if (msg.status === 'running') {
            buildBtn.classList.add('hidden');
            stopBtn.classList.remove('hidden');
            badge.className = "text-yellow-500 font-bold animate-pulse";
        } else {
            buildBtn.classList.remove('hidden');
            stopBtn.classList.add('hidden');
            badge.className = (msg.status === 'done' ? 'text-green-500' : 'text-red-500') + " font-bold";
        }
    });

    function startBuild() { term.clear(); socket.emit('start_build', {project: project}); }
    function stopBuild() { 
        if(confirm("Are you sure you want to kill the build?")) {
            socket.emit('stop_build', {project: project}); 
        }
    }
</script>
"""

# --- ROUTES ---
@app.route('/')
def index():
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(DASHBOARD_HTML, projects=load_registry(), states=BUILD_STATES))

@app.route('/create', methods=['GET', 'POST'])
def create():
    if request.method == 'GET':
        pct, free = get_disk_usage()
        # reusing previous create html (simplified for brevity)
        return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content="""
<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Create New Project</h2>
    <form action="/create" method="POST" class="space-y-4">
        <input type="text" name="name" placeholder="Project Name" required class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white">
        <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4">Scan & Create</button>
    </form>
</div>""")
    
    name = request.form['name']
    base_dir = os.path.join(WORK_DIR, "meta-qcom-builds")
    proj_path = os.path.join(base_dir, name)
    os.makedirs(proj_path, exist_ok=True)
    
    # Clone and setup (Simplified for this snippet - assuming repo exists or git clone)
    if not os.path.exists(os.path.join(proj_path, "meta-qcom")):
        subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", os.path.join(proj_path, "meta-qcom")], check=True)
    
    # Save default config
    cfg = {"board": "qcm6490-idp", "kas_files": "meta-qcom/ci/qcm6490-idp.yml", "image": "qcom-multimedia-image", "topology": "ASOC"}
    with open(os.path.join(proj_path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    
    reg = load_registry()
    reg[name] = proj_path
    save_registry(reg)
    return redirect('/')

@app.route('/build/<name>')
def build_page(name):
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(BUILD_CONSOLE_HTML, project=name))

@app.route('/artifacts/<name>')
def view_artifacts(name):
    path, _ = get_config(name)
    pct, free = get_disk_usage()
    art_path = find_artifacts(path)
    if not art_path:
        return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(ARTIFACTS_HTML, project=name, files=[]))
    
    # Get recent files only (images, manifests)
    files = [f for f in os.listdir(art_path) if f.endswith(('.tar.gz', '.wic', '.manifest'))]
    files.sort()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(ARTIFACTS_HTML, project=name, files=files))

@app.route('/download/<name>/<filename>')
def download_artifact(name, filename):
    path, _ = get_config(name)
    art_path = find_artifacts(path)
    return send_from_directory(art_path, filename, as_attachment=True)

@app.route('/delete/<name>')
def delete(name):
    r = load_registry()
    if name in r: del r[name]; save_registry(r)
    return redirect('/')

# --- SOCKET ---
@socketio.on('join_project')
def handle_join(data):
    name = data['project']
    join_room(name)
    if name in BUILD_STATES:
        emit('log_chunk', {'data': "".join(BUILD_STATES[name]['logs'])})
        emit('build_status', {'status': BUILD_STATES[name]['status']})
    else:
        emit('build_status', {'status': 'idle'})

@socketio.on('start_build')
def handle_build(data):
    name = data['project']
    if name in BUILD_STATES and BUILD_STATES[name]['status'] == 'running': return
    
    path, cfg = get_config(name)
    cmd = f"kas shell {cfg.get('kas_files', 'meta-qcom/ci/qcm6490-idp.yml')} -c 'bitbake {cfg.get('image', 'qcom-multimedia-image')}'"
    
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
                decoded = data.decode(errors='ignore')
                BUILD_STATES[name]['logs'].append(decoded)
                socketio.emit('log_chunk', {'data': decoded}, to=name)
            except: break
        p.wait()
        # If killed, status might already be set to stopped
        if BUILD_STATES[name]['status'] == 'running':
            final = 'done' if p.returncode == 0 else 'failed'
            BUILD_STATES[name]['status'] = final
            socketio.emit('build_status', {'status': final}, to=name)

    threading.Thread(target=read_output).start()

@socketio.on('stop_build')
def handle_stop(data):
    name = data['project']
    if name in BUILD_STATES and BUILD_STATES[name]['status'] == 'running':
        pid = BUILD_STATES[name]['pid']
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                BUILD_STATES[name]['logs'].append("\r\n\x1b[1;31m!!! USER STOPPED BUILD !!!\x1b[0m\r\n")
                socketio.emit('log_chunk', {'data': "\r\n!!! STOPPED !!!\r\n"}, to=name)
            except Exception as e:
                print(f"Error stopping: {e}")
        BUILD_STATES[name]['status'] = 'stopped'
        emit('build_status', {'status': 'stopped'}, to=name)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, allow_unsafe_werkzeug=True)
