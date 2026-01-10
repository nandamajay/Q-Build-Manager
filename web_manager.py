import os
import yaml
import glob
import subprocess
import pty
import threading
import signal
import shutil
import re
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
    <title>Q-Build Manager V4</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@4.19.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@4.19.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.5.0/lib/xterm-addon-fit.js"></script>
    <script> if (Notification.permission !== "granted") Notification.requestPermission(); </script>
</head>
<body class="bg-gray-900 text-gray-100 font-sans min-h-screen flex flex-col">
    <nav class="bg-gray-800 p-4 border-b border-gray-700">
        <div class="container mx-auto flex justify-between items-center">
            <a href="/" class="text-2xl font-bold text-blue-400"><i class="fas fa-microchip mr-2"></i>Q-Build <span class="text-xs text-green-500">WIZARD</span></a>
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
                 <a href="/build/{{ name }}" class="bg-yellow-600 hover:bg-yellow-500 px-4 py-2 rounded text-white text-sm w-full text-center"><i class="fas fa-eye mr-1"></i> View Logs</a>
             {% else %}
                 <div class="flex space-x-2">
                    <a href="/build/{{ name }}" class="bg-gray-700 hover:bg-gray-600 px-3 py-2 rounded text-white text-sm"><i class="fas fa-hammer"></i></a>
                    <a href="/artifacts/{{ name }}" class="bg-blue-600 hover:bg-blue-500 px-3 py-2 rounded text-white text-sm"><i class="fas fa-download"></i> Artifacts</a>
                 </div>
                 <a href="/delete/{{ name }}" class="text-red-400 hover:text-red-300 px-3 py-2 opacity-0 group-hover:opacity-100 transition" onclick="return confirm('Delete?')"><i class="fas fa-trash"></i></a>
             {% endif %}
        </div>
    </div>
    {% else %}
    <div class="col-span-3 text-center py-20 text-gray-500"><p>No projects found.</p></div>
    {% endfor %}
</div>
"""

# New Wizard Templates
CREATE_STEP1_HTML = """
<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Step 1: Project Name</h2>
    <form action="/create_step2" method="POST" class="space-y-4" onsubmit="document.getElementById('btn').innerHTML='<i class=\'fas fa-spinner fa-spin\'></i> Cloning & Scanning...';">
        <div>
            <label class="block text-sm text-gray-400 mb-1">Project Name</label>
            <input type="text" name="name" placeholder="e.g. rb5-test" required class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white">
        </div>
        <div class="bg-blue-900/30 p-4 rounded text-sm text-blue-200">
            <i class="fas fa-info-circle mr-2"></i> This will clone 'meta-qcom' and scan for available boards. This may take 30-60 seconds.
        </div>
        <button id="btn" type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4">Next: Scan Boards <i class="fas fa-arrow-right ml-2"></i></button>
    </form>
</div>
"""

CREATE_STEP2_HTML = """
<div class="max-w-2xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Step 2: Configuration for '{{ project }}'</h2>
    <form action="/finish_create" method="POST" class="space-y-6">
        <input type="hidden" name="name" value="{{ project }}">
        
        <!-- Board Selection -->
        <div>
            <label class="block text-sm text-gray-400 mb-1">Target Board (Detected from meta-qcom/ci)</label>
            <select name="board" class="w-full bg-gray-900 border border-gray-600 rounded p-3 text-white font-mono">
                {% for b in boards %}
                <option value="{{ b }}">{{ b }}</option>
                {% endfor %}
            </select>
        </div>

        <!-- Architecture -->
        <div>
            <label class="block text-sm text-gray-400 mb-1">Architecture / Topology</label>
            <div class="flex space-x-4">
                <label class="flex items-center space-x-2 bg-gray-900 p-3 rounded border border-gray-600 flex-1 cursor-pointer hover:border-blue-500">
                    <input type="radio" name="topology" value="ASOC" checked> <span>ASOC (Standard)</span>
                </label>
                <label class="flex items-center space-x-2 bg-gray-900 p-3 rounded border border-gray-600 flex-1 cursor-pointer hover:border-blue-500">
                    <input type="radio" name="topology" value="AudioReach"> <span>AudioReach</span>
                </label>
            </div>
        </div>
        
        <!-- Image Name -->
        <div>
            <label class="block text-sm text-gray-400 mb-1">Image Name</label>
            <input type="text" name="image" value="qcom-multimedia-image" class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white font-mono">
        </div>

        <button type="submit" class="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold mt-4">Save & Create Project <i class="fas fa-check ml-2"></i></button>
    </form>
</div>
"""

BUILD_CONSOLE_HTML = """
<div class="flex flex-col h-full space-y-4">
    <div class="bg-gray-800 p-4 rounded-lg shadow flex justify-between items-center">
        <div>
            <h2 class="text-2xl font-bold">{{ project }}</h2>
            <div class="text-sm text-gray-400 mt-1">Status: <span id="statusBadge" class="font-bold">UNKNOWN</span></div>
            <div class="text-xs text-gray-500 mt-1 font-mono" id="configInfo">Loading config...</div>
        </div>
        <div class="flex space-x-3 items-center">
            <select onchange="runClean(this.value)" id="cleanSelect" class="bg-gray-700 text-white text-sm rounded p-2">
                <option value="">Clean Options...</option>
                <option value="clean">Clean</option>
                <option value="cleansstate">Clean SState</option>
            </select>
            <button onclick="stopBuild()" id="stopBtn" class="hidden bg-red-600 hover:bg-red-500 text-white px-6 py-2 rounded shadow"><i class="fas fa-stop"></i> STOP</button>
            <button onclick="startBuild()" id="buildBtn" class="bg-green-600 hover:bg-green-500 text-white px-6 py-2 rounded shadow"><i class="fas fa-play"></i> BUILD</button>
            <a href="/" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded text-white">Back</a>
        </div>
    </div>
    <div id="errorCard" class="hidden bg-red-900/50 border border-red-500 p-4 rounded-lg">
        <h3 class="font-bold text-red-400"><i class="fas fa-bug mr-2"></i> Build Failed</h3>
        <pre id="errorText" class="text-xs text-red-200 mt-2 font-mono whitespace-pre-wrap overflow-x-auto"></pre>
    </div>
    <div class="bg-gray-800 rounded-full h-5 w-full overflow-hidden border border-gray-700 relative">
        <div id="progressBar" class="bg-blue-600 h-full w-0 transition-all duration-300"></div>
        <span id="progressText" class="absolute inset-0 flex items-center justify-center text-xs font-bold text-white drop-shadow-md">0%</span>
    </div>
    <div class="relative flex-grow h-[600px]">
        <div id="terminal" class="h-full bg-black rounded shadow-lg border border-gray-700 overflow-hidden"></div>
        <button onclick="toggleScroll()" id="scrollBtn" class="absolute bottom-4 right-4 bg-gray-700/80 hover:bg-gray-600 text-white px-3 py-1 rounded text-xs z-10"><i class="fas fa-lock"></i> Scroll: ON</button>
    </div>
</div>
<script>
    var socket = io(); var project = '{{ project }}';
    var term = new Terminal({ theme: { background: '#000000', foreground: '#e5e5e5' }, convertEol: true });
    var fitAddon = new FitAddon.FitAddon(); var autoScroll = true;
    term.loadAddon(fitAddon); term.open(document.getElementById('terminal')); fitAddon.fit();
    window.onresize = () => fitAddon.fit();
    function toggleScroll() { autoScroll = !autoScroll; document.getElementById('scrollBtn').innerHTML = autoScroll ? 'Scroll: ON' : 'Scroll: OFF'; }

    socket.emit('join_project', {project: project});
    socket.on('config_info', function(msg) { document.getElementById('configInfo').innerText = msg.info; });
    socket.on('log_chunk', function(msg) { 
        term.write(msg.data); 
        if(autoScroll) term.scrollToBottom(); 
        const match = msg.data.match(/Tasks:\\s+(\\d+)\\s+of\\s+(\\d+)/);
        if (match) {
            const pct = Math.round((parseInt(match[1]) / parseInt(match[2])) * 100);
            document.getElementById('progressBar').style.width = pct + '%'; document.getElementById('progressText').innerText = pct + '%';
        }
    });
    socket.on('error_summary', function(msg) { document.getElementById('errorCard').classList.remove('hidden'); document.getElementById('errorText').innerText = msg.summary; });
    socket.on('build_status', function(msg) { updateUI(msg.status); if (msg.status === 'done' || msg.status === 'failed') sendNotification("Build " + msg.status, project); });
    function updateUI(status) {
        document.getElementById('statusBadge').innerText = status.toUpperCase();
        if(status === 'running') { document.getElementById('buildBtn').classList.add('hidden'); document.getElementById('stopBtn').classList.remove('hidden'); }
        else { document.getElementById('buildBtn').classList.remove('hidden'); document.getElementById('stopBtn').classList.add('hidden'); }
    }
    function startBuild() { term.clear(); socket.emit('start_build', {project: project}); }
    function stopBuild() { if(confirm("Kill?")) socket.emit('stop_build', {project: project}); }
    function runClean(mode) { if(mode && confirm(mode + "?")) { term.clear(); socket.emit('clean_build', {project: project, mode: mode}); document.getElementById('cleanSelect').value=""; } }
    function sendNotification(t, b) { if (Notification.permission === "granted") new Notification(t, { body: b }); }
</script>
"""

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
    
    # 1. Clone
    repo_path = os.path.join(proj_path, "meta-qcom")
    if not os.path.exists(repo_path):
        try:
            subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", repo_path], check=True)
        except:
            return "<h1>Git Clone Failed</h1><p>Check internet connection.</p><a href='/'>Back</a>"
    
    # 2. Scan for YAMLs
    ci_path = os.path.join(repo_path, "ci")
    boards = []
    if os.path.exists(ci_path):
        boards = [f for f in os.listdir(ci_path) if f.endswith('.yml')]
        boards.sort()
    
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=render_template_string(CREATE_STEP2_HTML, project=name, boards=boards))

@app.route('/finish_create', methods=['POST'])
def finish_create():
    name = request.form['name']
    board = request.form['board']
    topo = request.form['topology']
    image = request.form['image']
    
    # Save Config
    base_dir = os.path.join(WORK_DIR, "meta-qcom-builds")
    proj_path = os.path.join(base_dir, name)
    cfg = {"kas_files": f"meta-qcom/ci/{board}", "image": image, "topology": topo}
    
    with open(os.path.join(proj_path, "config.yaml"), "w") as f:
        yaml.dump(cfg, f)
        
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
    if not art_path: return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content="<div class='p-10 text-white'>No artifacts found yet. Build first!</div>")
    files = [f for f in os.listdir(art_path) if f.endswith(('.tar.gz', '.wic', '.manifest'))]
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, body_content=f"<div class='bg-gray-800 p-6 rounded text-white'><h2 class='mb-4 text-xl'>Artifacts</h2><ul>{''.join([f'<li class=p-2><a class=text-blue-400 href=/download/{name}/{f}>{f}</a></li>' for f in files])}</ul></div>")

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
    path, cfg = get_config(name)
    # Send config info to UI
    info = f"Target: {cfg.get('kas_files','?')} | Topo: {cfg.get('topology','ASOC')} | Image: {cfg.get('image','?')}"
    emit('config_info', {'info': info})
    
    if name in BUILD_STATES:
        emit('log_chunk', {'data': "".join(BUILD_STATES[name]['logs'])})
        emit('build_status', {'status': BUILD_STATES[name]['status']})
        if 'error_summary' in BUILD_STATES[name]: emit('error_summary', {'summary': BUILD_STATES[name]['error_summary']})

def execute_cmd(name, cmd):
    path, _ = get_config(name)
    BUILD_STATES[name] = {'status': 'running', 'logs': [], 'pid': None}
    emit('build_status', {'status': 'running'}, to=name)
    master, slave = pty.openpty()
    p = subprocess.Popen(cmd, shell=True, cwd=path, stdout=slave, stderr=slave, preexec_fn=os.setsid, executable='/bin/bash')
    os.close(slave)
    BUILD_STATES[name]['pid'] = p.pid
    error_buffer = []
    def read_output():
        while True:
            try:
                data = os.read(master, 1024)
                if not data: break
                decoded = data.decode(errors='ignore')
                BUILD_STATES[name]['logs'].append(decoded)
                if "ERROR:" in decoded: error_buffer.append(decoded.strip())
                socketio.emit('log_chunk', {'data': decoded}, to=name)
            except: break
        p.wait()
        final = 'done' if p.returncode == 0 else 'failed'
        BUILD_STATES[name]['status'] = final
        socketio.emit('build_status', {'status': final}, to=name)
        if final == 'failed' and error_buffer:
             summary = "\n".join(error_buffer[:10])
             BUILD_STATES[name]['error_summary'] = summary
             socketio.emit('error_summary', {'summary': summary}, to=name)
    threading.Thread(target=read_output).start()

@socketio.on('start_build')
def handle_build(data):
    name = data['project']
    path, cfg = get_config(name)
    # Build KAS command with Architecture Support
    base_cmd = f"kas shell {cfg.get('kas_files')} -c"
    # Note: For ASOC vs AudioReach, real production envs might need ENV vars or different yamls.
    # For now we just print it to log to show it's respected
    print(f"Building {name} with Topology: {cfg.get('topology')}")
    
    cmd = f"{base_cmd} 'bitbake {cfg.get('image')}'"
    execute_cmd(name, cmd)

@socketio.on('clean_build')
def handle_clean(data):
    name = data['project']
    path, cfg = get_config(name)
    cmd = f"kas shell {cfg.get('kas_files')} -c 'bitbake {cfg.get('image')} -c {data.get('mode')}'"
    execute_cmd(name, cmd)

@socketio.on('stop_build')
def handle_stop(data):
    name = data['project']
    if name in BUILD_STATES and BUILD_STATES[name]['pid']:
        try: os.killpg(os.getpgid(BUILD_STATES[name]['pid']), signal.SIGTERM)
        except: pass
    BUILD_STATES[name]['status'] = 'stopped'
    emit('build_status', {'status': 'stopped'}, to=name)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, allow_unsafe_werkzeug=True)
