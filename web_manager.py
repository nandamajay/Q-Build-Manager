
import os
import yaml
import glob
import subprocess
import pty
import threading
import signal
from flask import Flask, render_template_string, request, redirect, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

SERVER_PORT = int(os.environ.get("WEB_PORT", 5000))
WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")

# --- UTILS ---
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

def generate_kas_config(path, board, topology):
    board_file = f"meta-qcom/ci/{board}.yml"
    if topology == 'AR':
        distro_file = "meta-qcom/ci/qcom-distro-prop-image.yml"
        image = "qcom-multimedia-proprietary-image"
    else:
        distro_file = "meta-qcom/ci/qcom-distro.yml"
        image = "qcom-multimedia-image"

    full_distro = os.path.join(path, distro_file)
    kas_string = f"{board_file}:{distro_file}" if os.path.exists(full_distro) else board_file
    return kas_string, image

# --- HTML TEMPLATES ---
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Q-Build Manager</title>
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
            <a href="/" class="text-2xl font-bold text-blue-400"><i class="fas fa-microchip mr-2"></i>Q-Build Manager</a>
            <div class="space-x-4">
                <span class="text-gray-400 text-sm"><i class="fas fa-network-wired"></i> Port {{ port }}</span>
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
    <div class="bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-lg hover:border-blue-500 transition relative">
        <h3 class="text-xl font-bold mb-2">{{ name }}</h3>
        <p class="text-gray-400 text-xs mb-4 truncate">{{ path }}</p>
        <div class="flex justify-between mt-4">
             <a href="/build/{{ name }}" class="bg-green-600 hover:bg-green-500 px-4 py-2 rounded text-white text-sm"><i class="fas fa-hammer mr-1"></i> Build</a>
             <a href="/delete/{{ name }}" class="text-red-400 hover:text-red-300 px-3 py-2" onclick="return confirm('Delete registry entry?')"><i class="fas fa-trash"></i></a>
        </div>
    </div>
    {% else %}
    <div class="col-span-3 text-center py-20 text-gray-500">
        <i class="fas fa-box-open text-6xl mb-4"></i>
        <p>No projects found.</p>
    </div>
    {% endfor %}
</div>
"""

CREATE_HTML = """
<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Create New Project</h2>
    <form action="/create" method="POST" class="space-y-4">
        <div>
            <label class="block text-gray-400 mb-1">Project Name</label>
            <input type="text" name="name" required class="w-full bg-gray-900 border border-gray-600 rounded p-2 focus:border-blue-500 outline-none">
        </div>
        <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4">Scan Boards & Create</button>
    </form>
</div>
"""

BOARD_SELECT_HTML = """
<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-4">Select Target Board</h2>
    <form action="/finish_setup" method="POST">
        <input type="hidden" name="project_name" value="{{ project_name }}">
        <input type="hidden" name="project_path" value="{{ project_path }}">
        <div class="space-y-2 max-h-64 overflow-y-auto pr-2 mb-6 border border-gray-700 p-2 rounded">
            {% for b in boards %}
            <label class="flex items-center space-x-3 p-2 bg-gray-900 rounded cursor-pointer hover:bg-gray-700">
                <input type="radio" name="board" value="{{ b }}" class="h-5 w-5 text-blue-500" {% if loop.first %}checked{% endif %}>
                <span class="font-mono">{{ b }}</span>
            </label>
            {% endfor %}
        </div>
        <button type="submit" class="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold">Finish Setup</button>
    </form>
</div>
"""

BUILD_CONSOLE_HTML = """
<div class="flex flex-col h-full space-y-4">
    <div class="bg-gray-800 p-4 rounded-lg shadow flex justify-between items-center">
        <div>
            <h2 class="text-2xl font-bold">{{ project }}</h2>
            <div class="text-sm text-gray-400 mt-1">
                Topology: 
                <span id="topoDisplay" class="font-bold text-white bg-gray-700 px-2 py-0.5 rounded ml-1">{{ config.topology }}</span>
            </div>
        </div>
        <div class="flex space-x-4 items-center">
            <!-- Settings Panel -->
            <div class="bg-gray-900 px-3 py-2 rounded border border-gray-700 flex items-center space-x-3">
                <span class="text-xs text-gray-500 uppercase font-bold">Build Settings</span>
                <label class="cursor-pointer flex items-center space-x-1">
                    <input type="radio" name="topo" value="ASOC" onclick="setTopo('ASOC')" {% if config.topology == 'ASOC' %}checked{% endif %}>
                    <span class="text-sm">ASOC</span>
                </label>
                <label class="cursor-pointer flex items-center space-x-1">
                    <input type="radio" name="topo" value="AR" onclick="setTopo('AR')" {% if config.topology == 'AR' %}checked{% endif %}>
                    <span class="text-sm">AR</span>
                </label>
            </div>
            
            <button onclick="startBuild()" id="buildBtn" class="bg-green-600 hover:bg-green-500 text-white px-6 py-2 rounded shadow"><i class="fas fa-play"></i> Start Build</button>
            <a href="/" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded text-white">Back</a>
        </div>
    </div>

    <!-- Progress -->
    <div class="bg-gray-800 rounded-full h-5 w-full overflow-hidden border border-gray-700 relative">
        <div id="progressBar" class="bg-blue-600 h-full w-0 transition-all duration-300"></div>
        <span id="progressText" class="absolute inset-0 flex items-center justify-center text-xs font-bold text-white drop-shadow-md">0%</span>
    </div>

    <div id="terminal" class="flex-grow bg-black rounded shadow-lg border border-gray-700 overflow-hidden h-[600px]"></div>
</div>

<script>
    var socket = io();
    var term = new Terminal({
        cursorBlink: true,
        fontFamily: 'Menlo, monospace',
        fontSize: 14,
        theme: { background: '#000000', foreground: '#e5e5e5' }
    });
    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById('terminal'));
    fitAddon.fit();
    window.onresize = () => fitAddon.fit();

    term.writeln('\\x1b[1;34m--- Ready to Build ---\\x1b[0m');
    term.writeln('Select Topology above if needed, then click "Start Build".');

    function setTopo(val) {
        fetch('/update_config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project: '{{ project }}', topology: val})
        }).then(r => r.json()).then(d => {
            document.getElementById('topoDisplay').innerText = val;
            term.writeln('\\r\\n\\x1b[33m[Config] Switched to ' + val + '\\x1b[0m');
        });
    }

    socket.on('build_output', function(msg) {
        // Fix for raw newlines coming from python
        term.write(msg.data);
        
        // Progress Bar Logic
        const match = msg.data.match(/Tasks:\\s+(\\d+)\\s+of\\s+(\\d+)/);
        if (match) {
            const pct = Math.round((parseInt(match[1]) / parseInt(match[2])) * 100);
            document.getElementById('progressBar').style.width = pct + '%';
            document.getElementById('progressText').innerText = pct + '%';
        }
    });

    socket.on('build_done', function() {
        term.writeln('\\r\\n\\x1b[1;32m=== BUILD COMPLETED ===\\x1b[0m');
        document.getElementById('buildBtn').disabled = false;
        document.getElementById('buildBtn').classList.remove('opacity-50');
    });

    function startBuild() {
        term.clear();
        document.getElementById('buildBtn').disabled = true;
        document.getElementById('buildBtn').classList.add('opacity-50');
        socket.emit('start_build', {project: '{{ project }}'});
    }
</script>
"""

# --- FLASK ROUTES ---
@app.route('/')
def index():
    return render_template_string(BASE_HTML, body_content=render_template_string(DASHBOARD_HTML, projects=load_registry()), port=SERVER_PORT)

@app.route('/create', methods=['GET', 'POST'])
def create():
    if request.method == 'GET':
        return render_template_string(BASE_HTML, body_content=render_template_string(CREATE_HTML), port=SERVER_PORT)
    
    name = request.form['name']
    base_dir = os.path.join(WORK_DIR, "meta-qcom-builds")
    proj_path = os.path.join(base_dir, name)
    
    if os.path.exists(proj_path): return "Project exists."
    os.makedirs(proj_path, exist_ok=True)
    
    src = os.path.join(proj_path, "meta-qcom")
    try:
        subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", src], check=True)
    except: return "Git clone failed"

    boards = sorted([os.path.basename(f).replace(".yml","") for f in glob.glob(os.path.join(src, "ci/*.yml"))])
    return render_template_string(BASE_HTML, body_content=render_template_string(BOARD_SELECT_HTML, boards=boards, project_name=name, project_path=proj_path), port=SERVER_PORT)

@app.route('/finish_setup', methods=['POST'])
def finish_setup():
    name = request.form['project_name']
    path = request.form['project_path']
    board = request.form['board']
    
    kas, img = generate_kas_config(path, board, "ASOC")
    cfg = {"board": board, "kas_files": kas, "image": img, "topology": "ASOC"}
    
    with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    reg = load_registry()
    reg[name] = path
    save_registry(reg)
    return redirect('/')

@app.route('/build/<name>')
def build_page(name):
    path, cfg = get_config(name)
    if not path: return redirect('/')
    return render_template_string(BASE_HTML, body_content=render_template_string(BUILD_CONSOLE_HTML, project=name, path=path, config=cfg), port=SERVER_PORT)

@app.route('/update_config', methods=['POST'])
def update_config():
    d = request.json
    path, cfg = get_config(d['project'])
    cfg['topology'] = d['topology']
    cfg['kas_files'], cfg['image'] = generate_kas_config(path, cfg['board'], d['topology'])
    with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    return jsonify({"status":"ok"})

@app.route('/delete/<name>')
def delete(name):
    r = load_registry()
    if name in r: 
        del r[name]
        save_registry(r)
    return redirect('/')

# --- BUILD LOGIC ---
@socketio.on('start_build')
def handle_build(data):
    name = data['project']
    path, cfg = get_config(name)
    
    # We use "script" command to fool Bitbake into thinking it has a TTY for colors
    cmd = f"kas shell {cfg['kas_files']} -c 'bitbake {cfg['image']}'"
    
    emit('build_output', {'data': f"\r\n\x1b[1;36m>> Starting Build for {name}...\x1b[0m\r\n"})
    emit('build_output', {'data': f">> Topology: {cfg['topology']}\r\n"})
    emit('build_output', {'data': f">> Command: {cmd}\r\n\r\n"})
    
    # Run process with PTY to capture output correctly
    master, slave = pty.openpty()
    p = subprocess.Popen(cmd, shell=True, cwd=path, stdout=slave, stderr=slave, preexec_fn=os.setsid, executable='/bin/bash')
    os.close(slave)
    
    def read_output():
        while True:
            try:
                # Read 1024 bytes at a time
                data = os.read(master, 1024)
                if not data: break
                # Decode and emit
                emit('build_output', {'data': data.decode(errors='ignore')})
            except OSError:
                break
        p.wait()
        emit('build_done', {})

    threading.Thread(target=read_output).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, allow_unsafe_werkzeug=True)
