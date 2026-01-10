import os
import yaml
import glob
import subprocess
import pty
import re
import threading
from flask import Flask, render_template_string, request, redirect, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# Read Port from Environment
SERVER_PORT = int(os.environ.get("WEB_PORT", 5000))

WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")
CATEGORY_MAP = {
    "1": {"name": "meta-qcom", "dir": "meta-qcom-builds"},
    "2": {"name": "upstream", "dir": "upstream-builds"},
    "3": {"name": "qclinux", "dir": "qclinux-builds"}
}

# --- HTML LAYOUT ---
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
    <style>
        .scrollbar-hide::-webkit-scrollbar { display: none; }
    </style>
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

# --- PAGE FRAGMENTS ---
DASHBOARD_HTML = """
<h2 class="text-xl mb-4 text-gray-400">Your Projects</h2>
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    {% for name, path in projects.items() %}
    <div class="bg-gray-800 p-6 rounded-lg border border-gray-700 shadow-lg hover:border-blue-500 transition relative">
        <h3 class="text-2xl font-bold mb-2">{{ name }}</h3>
        <p class="text-gray-400 text-sm mb-4 truncate" title="{{ path }}">{{ path }}</p>
        <div class="flex justify-between mt-4">
             <a href="/build/{{ name }}" class="bg-green-600 hover:bg-green-500 px-4 py-2 rounded text-white"><i class="fas fa-hammer mr-1"></i> Build</a>
             <a href="/delete/{{ name }}" class="text-red-400 hover:text-red-300 px-3 py-2" onclick="return confirm('Delete registry entry?')"><i class="fas fa-trash"></i></a>
        </div>
    </div>
    {% else %}
    <div class="col-span-3 text-center py-20 text-gray-500">
        <i class="fas fa-box-open text-6xl mb-4"></i>
        <p>No projects found. Create one to get started!</p>
    </div>
    {% endfor %}
</div>
"""

CREATE_HTML = """
<div class="max-w-2xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6 border-b border-gray-700 pb-2">Create New Project</h2>
    <form action="/create" method="POST" class="space-y-4">
        <div>
            <label class="block text-gray-400 mb-1">Project Name</label>
            <input type="text" name="name" required class="w-full bg-gray-900 border border-gray-600 rounded p-2 focus:border-blue-500 outline-none" placeholder="e.g. rb3-audio-demo">
        </div>
        <div>
            <label class="block text-gray-400 mb-1">Project Type</label>
            <select name="category" class="w-full bg-gray-900 border border-gray-600 rounded p-2">
                <option value="1">Meta-Qcom (Standard)</option>
                <option value="2">Upstream (Experimental)</option>
            </select>
        </div>
        <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4">Next Step: Scan Boards</button>
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

        <h3 class="text-lg font-bold mb-2">Initial Topology</h3>
        <div class="flex space-x-4 mb-6">
            <label class="flex items-center space-x-2 cursor-pointer bg-gray-700 px-4 py-2 rounded">
                <input type="radio" name="topology" value="ASOC" checked class="text-blue-500"> <span>ASOC (Multimedia)</span>
            </label>
            <label class="flex items-center space-x-2 cursor-pointer bg-gray-700 px-4 py-2 rounded">
                <input type="radio" name="topology" value="AR" class="text-blue-500"> <span>AudioReach (Proprietary)</span>
            </label>
        </div>

        <button type="submit" class="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold">Complete Setup</button>
    </form>
</div>
"""

BUILD_CONSOLE_HTML = """
<div class="flex flex-col h-full space-y-4">
    <!-- Header & Settings -->
    <div class="bg-gray-800 p-4 rounded-lg shadow flex justify-between items-start">
        <div>
            <h2 class="text-2xl font-bold text-white">{{ project }}</h2>
            <p class="text-sm text-gray-400">Path: {{ path }}</p>
        </div>
        
        <!-- Topology Switcher -->
        <div class="bg-gray-900 p-3 rounded border border-gray-700">
            <form id="configForm" class="flex items-center space-x-4 text-sm">
                <span class="text-gray-400 font-bold">Topology:</span>
                <label class="flex items-center space-x-2 cursor-pointer">
                    <input type="radio" name="topology" value="ASOC" onclick="updateConfig('ASOC')" 
                           {% if config.topology == 'ASOC' %}checked{% endif %} class="text-blue-500"> 
                    <span>ASOC</span>
                </label>
                <label class="flex items-center space-x-2 cursor-pointer">
                    <input type="radio" name="topology" value="AR" onclick="updateConfig('AR')"
                           {% if config.topology == 'AR' %}checked{% endif %} class="text-blue-500"> 
                    <span>AudioReach</span>
                </label>
                <div class="h-6 w-px bg-gray-600 mx-2"></div>
                <div class="text-xs text-gray-500">
                    Target: <span id="targetLabel" class="text-gray-300">{{ config.image }}</span>
                </div>
            </form>
        </div>

        <div class="space-x-2">
            <button onclick="startBuild()" id="buildBtn" class="bg-green-600 hover:bg-green-500 text-white px-6 py-2 rounded shadow transition">
                <i class="fas fa-play"></i> Start Build
            </button>
            <a href="/" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded text-white">Back</a>
        </div>
    </div>

    <!-- Progress Bar -->
    <div class="bg-gray-800 rounded-full h-4 w-full overflow-hidden border border-gray-700 relative">
        <div id="progressBar" class="bg-blue-500 h-full w-0 transition-all duration-300"></div>
        <span id="progressText" class="absolute inset-0 flex items-center justify-center text-xs font-bold text-white shadow-black drop-shadow-md">0%</span>
    </div>

    <!-- Terminal -->
    <div id="terminal" class="flex-grow bg-black rounded shadow-lg border border-gray-700 overflow-hidden h-[600px]"></div>
</div>

<script>
    var socket = io();
    var term = new Terminal({
        cursorBlink: true,
        fontFamily: 'Menlo, monospace',
        fontSize: 14,
        theme: { background: '#1a1b26', foreground: '#a9b1d6' }
    });
    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(document.getElementById('terminal'));
    fitAddon.fit();
    window.onresize = () => fitAddon.fit();

    term.writeln('\\x1b[1;34m--- Ready to Build ---\\x1b[0m');
    term.writeln('Select Topology above if needed, then click "Start Build".');

    // Handle Config Update
    function updateConfig(topo) {
        fetch('/update_config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({project: '{{ project }}', topology: topo})
        })
        .then(r => r.json())
        .then(data => {
            document.getElementById('targetLabel').innerText = data.image;
            term.writeln('\\r\\n\\x1b[33mConfiguration updated to ' + topo + '. Target: ' + data.image + '\\x1b[0m');
        });
    }

    // Output Streaming
    socket.on('build_output', function(msg) {
        term.write(msg.data);
        
        // Simple progress parsing
        const regex = /Tasks:\s+(\d+)\s+of\s+(\d+)/; 
        const match = msg.data.match(regex);
        if (match) {
            const current = parseInt(match[1]);
            const total = parseInt(match[2]);
            const pct = Math.round((current / total) * 100);
            document.getElementById('progressBar').style.width = pct + '%';
            document.getElementById('progressText').innerText = pct + '%';
        }
    });

    socket.on('build_done', function(msg) {
        term.writeln('\\r\\n\\x1b[1;32m=== BUILD COMPLETED ===\\x1b[0m');
        document.getElementById('buildBtn').disabled = false;
        document.getElementById('buildBtn').classList.remove('opacity-50', 'cursor-not-allowed');
    });

    function startBuild() {
        term.clear();
        document.getElementById('buildBtn').disabled = true;
        document.getElementById('buildBtn').classList.add('opacity-50', 'cursor-not-allowed');
        document.getElementById('progressBar').style.width = '0%';
        socket.emit('start_build', {project: '{{ project }}'});
    }
</script>
"""

# --- HELPERS ---
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

    # Check if files exist to avoid blind errors
    full_distro = os.path.join(path, distro_file)
    kas_string = f"{board_file}:{distro_file}" if os.path.exists(full_distro) else board_file
    
    return kas_string, image

def render_page(template, **kwargs):
    content = render_template_string(template, **kwargs)
    return render_template_string(BASE_HTML, body_content=content, port=SERVER_PORT)

# --- ROUTES ---
@app.route('/')
def index():
    return render_page(DASHBOARD_HTML, projects=load_registry())

@app.route('/create', methods=['GET', 'POST'])
def create():
    if request.method == 'GET':
        return render_page(CREATE_HTML)
    
    name = request.form['name']
    cat_id = request.form['category']
    
    category = CATEGORY_MAP.get(cat_id, CATEGORY_MAP["1"])
    base_dir = os.path.join(WORK_DIR, category["dir"])
    proj_path = os.path.join(base_dir, name)
    
    if os.path.exists(proj_path):
        return f"Error: Project {name} already exists."
    
    os.makedirs(proj_path, exist_ok=True)
    
    src_dir = os.path.join(proj_path, "meta-qcom")
    try:
        subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", src_dir], check=True)
    except:
        return "Error: Git clone failed."

    config_files = glob.glob(os.path.join(src_dir, "ci/*.yml"))
    boards = sorted([os.path.basename(f).replace(".yml", "") for f in config_files])
    
    return render_page(BOARD_SELECT_HTML, boards=boards, project_name=name, project_path=proj_path)

@app.route('/finish_setup', methods=['POST'])
def finish_setup():
    p_name = request.form['project_name']
    p_path = request.form['project_path']
    board = request.form['board']
    topo = request.form.get('topology', 'ASOC')
    
    kas_string, image = generate_kas_config(p_path, board, topo)
    
    cfg = {"board": board, "kas_files": kas_string, "image": image, "topology": topo}
    with open(os.path.join(p_path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    
    reg = load_registry()
    reg[p_name] = p_path
    save_registry(reg)
    
    return redirect('/')

@app.route('/delete/<name>')
def delete(name):
    reg = load_registry()
    if name in reg:
        del reg[name]
        save_registry(reg)
    return redirect('/')

@app.route('/build/<name>')
def build_page(name):
    path, cfg = get_config(name)
    if not path: return redirect('/')
    return render_page(BUILD_CONSOLE_HTML, project=name, path=path, config=cfg)

@app.route('/update_config', methods=['POST'])
def update_config():
    data = request.json
    project = data.get('project')
    new_topo = data.get('topology')
    
    path, cfg = get_config(project)
    if not cfg: return jsonify({"error": "No config"}), 404
    
    # Update config logic
    cfg['topology'] = new_topo
    cfg['kas_files'], cfg['image'] = generate_kas_config(path, cfg['board'], new_topo)
    
    with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    
    return jsonify({"status": "updated", "image": cfg['image']})

@socketio.on('start_build')
def handle_build(data):
    project = data['project']
    path, cfg = get_config(project)
    
    # FIX: Ensure directory exists before touching local.conf (if we were to touch it)
    # But for now, we remove the manual local.conf touch to prevent the crash.
    # We rely on KAS to do the right thing.
    
    kas_cmd = f"kas shell {cfg['kas_files']} -c 'bitbake {cfg['image']}'"
    
    emit('build_output', {'data': f'\\r\\n\\x1b[1;36m>> Starting KAS Build for {project}...\\x1b[0m\\r\\n'})
    emit('build_output', {'data': f'>> Topology: {cfg["topology"]}\\r\\n'})
    emit('build_output', {'data': f'>> Command: {kas_cmd}\\r\\n\\r\\n'})
    
    master, slave = pty.openpty()
    
    # Run process with PTY to capture colors and progress bars
    process = subprocess.Popen(
        kas_cmd, 
        shell=True, 
        cwd=path, 
        stdout=slave, 
        stderr=slave, # Capture errors into the same stream
        preexec_fn=os.setsid, 
        executable='/bin/bash'
    )
    os.close(slave)
    
    def read_output():
        while True:
            try:
                output = os.read(master, 1024)
                if not output: break
                emit('build_output', {'data': output.decode(errors='ignore')})
            except OSError:
                break
        process.wait()
        emit('build_done', {'data': 'Done'})

    threading.Thread(target=read_output).start()

if __name__ == '__main__':
    print(f"Flask starting on port {SERVER_PORT}...")
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, allow_unsafe_werkzeug=True)
