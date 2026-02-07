import os
import yaml
import glob
import subprocess
import pty
import threading
from visualization.path_manager import PathManager
from visualization.dts_parser import DtsParser
from visualization.diagram_builder import DiagramBuilder
import signal
import shutil
import time
import re
import datetime
import json
import ai_helper
import codecs
from flask import Flask, render_template_string, request, redirect, abort, jsonify, send_file
from editor_manager import editor_bp 
from flask_socketio import SocketIO, emit, join_room

# --- CONFIGURATION ---
SERVER_PORT = int(os.environ.get("WEB_PORT", 5000))
WORK_DIR = "/work"
REGISTRY_FILE = os.path.join(WORK_DIR, "projects_registry.yaml")
# RESTORED ORIGINAL PATHS
YOCTO_BASE = os.path.join(WORK_DIR, "meta-qcom-builds")
UPSTREAM_BASE = os.path.join(WORK_DIR, "upstream-builds")
TOOLS_DIR = os.path.join(WORK_DIR, "common_tools")

# --- QGENIE SDK SETUP ---
QGENIE_AVAILABLE = False
try:
    from qgenie import ChatMessage, QGenieClient
    QGENIE_AVAILABLE = True
except ImportError:
    pass

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.register_blueprint(editor_bp)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

BUILD_STATES = {}

# --- HELPER FUNCTIONS (RESTORED) ---
def get_disk_usage():
    try:
        total, used, free = shutil.disk_usage(WORK_DIR)
        return int((used / total) * 100), int(free // (2**30))
    except: return 0, 0

def ensure_tools():
    """Background task to ensure tools like mkbootimg and firmware exist."""
    if not os.path.exists(TOOLS_DIR): os.makedirs(TOOLS_DIR, exist_ok=True)
    
    # 1. mkbootimg
    mkboot = os.path.join(TOOLS_DIR, "mkbootimg")
    if not os.path.exists(mkboot):
        subprocess.run(["git", "clone", "--depth", "1", "https://android.googlesource.com/platform/system/tools/mkbootimg", mkboot])
    
    # 2. initramfs
    initramfs_path = os.path.join(TOOLS_DIR, "initramfs-test.cpio.gz")
    if not os.path.exists(initramfs_path):
        subprocess.run(["wget", "https://snapshots.linaro.org/member-builds/qcomlt/testimages/arm64/1379/initramfs-test-image-qemuarm64-20230321073831-1379.rootfs.cpio.gz", "-O", initramfs_path])
    
    # 3. Linux Firmware
    fw_path = os.path.join(TOOLS_DIR, "linux-firmware")
    if not os.path.exists(fw_path):
        subprocess.run(["git", "clone", "--depth", "1", "https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git", fw_path])

def sync_registry():
    """Scans directories to find projects and rebuilds registry."""
    reg = {}
    os.makedirs(YOCTO_BASE, exist_ok=True)
    os.makedirs(UPSTREAM_BASE, exist_ok=True)
    
    def scan_dir(base_path, default_type):
        if not os.path.exists(base_path): return
        try:
            found = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]
            for p in found:
                full_path = os.path.abspath(os.path.join(base_path, p))
                ptype = default_type
                cfg_path = os.path.join(full_path, "config.yaml")
                created = "Unknown"; modified = "Unknown"
                
                # Get Config Data
                if os.path.exists(cfg_path):
                    try:
                        with open(cfg_path) as f: 
                            c = yaml.safe_load(f)
                            if c:
                                if 'type' in c: ptype = c['type']
                                if 'created' in c: created = c['created']
                    except: pass
                
                reg[p] = {'path': full_path, 'type': ptype, 'created': created}
        except Exception as e: print(f"Error scanning {base_path}: {e}")

    scan_dir(YOCTO_BASE, 'yocto')
    scan_dir(UPSTREAM_BASE, 'upstream')
    
    with open(REGISTRY_FILE, "w") as f: yaml.dump(reg, f)
    return reg

def get_config(project_name):
    # Try memory first, then file
    reg = sync_registry() # Sync to ensure we find restored projects
    data = reg.get(project_name)
    if not data: return None, None
    path = data['path']
    cfg_path = os.path.join(path, "config.yaml")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path) as f: return path, yaml.safe_load(f)
        except: return path, {}
    return path, {}

def find_yocto_image(path, machine):
    deploy_dir = os.path.join(path, "build/tmp/deploy/images", machine)
    if not os.path.exists(deploy_dir): return None
    candidates = glob.glob(os.path.join(deploy_dir, "*-image-*.rootfs.wic.zst"))
    if candidates: return candidates[0] 
    return None

def background_delete(path, name):
    try: shutil.rmtree(path)
    except: pass

def run_build_task(cmd, name):
    BUILD_STATES[name] = {'status': 'running', 'logs': [], 'pid': None}
    socketio.emit('build_status', {'status': 'running'}, to=name)
    path, _ = get_config(name)
    

    # Standard PTY execution
    master, slave = pty.openpty()
    p = subprocess.Popen(cmd, shell=True, cwd=path, stdout=slave, stderr=slave, preexec_fn=os.setsid, executable='/bin/bash')
    os.close(slave)
    BUILD_STATES[name]['pid'] = p.pid
    
    # IMPROVEMENT: Use incremental decoder and larger buffer
    decoder = codecs.getincrementaldecoder("utf-8")(errors='replace')
    
    while True:
        try:
            # Increase buffer to 16KB to reduce overhead and splitting
            data = os.read(master, 16384) 
            if not data: break
            
            # Decode safely, buffering incomplete bytes for the next chunk
            d = decoder.decode(data, final=False)
            
            if d:
                BUILD_STATES[name]['logs'].append(d)
                socketio.emit('log_chunk', {'data': d}, to=name)
        except OSError: 
            break  # Input/Output error (process likely ended)
        except Exception as e:
            print(f"Log Error: {e}")
            break

    p.wait()
    final_status = 'done' if p.returncode == 0 else 'failed'
    BUILD_STATES[name]['status'] = final_status
    socketio.emit('build_status', {'status': final_status}, to=name)
    socketio.emit('check_artifacts', {'project': name}, to=name)

    if final_status == 'failed':
        error_context = "".join(BUILD_STATES[name]['logs'][-50:])
        socketio.emit('build_failed_context', {'context': error_context}, to=name)

# --- HTML TEMPLATES ---

BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8"/>
    <title>Q-Build V30 AI</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet"/>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/xterm@4.19.0/css/xterm.css" rel="stylesheet"/>
    <script src="https://cdn.jsdelivr.net/npm/xterm@4.19.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.5.0/lib/xterm-addon-fit.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/styles/atom-one-dark.min.css" rel="stylesheet"/>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.7.0/highlight.min.js"></script>
    <style>
        .hljs { background: transparent; padding: 0; } 
        .code-container { display: flex; font-family: 'Fira Code', monospace; line-height: 1.5; font-size: 13px; }
        .line-numbers { text-align: right; padding-right: 15px; color: #6b7280; user-select: none; border-right: 1px solid #374151; margin-right: 15px; min-width: 40px; }
        .code-content { flex-grow: 1; overflow-x: auto; }
        textarea.editor { width: 100%; height: 100%; background: #1f2937; color: #e5e7eb; font-family: 'Fira Code', monospace; font-size: 13px; border: none; outline: none; resize: none; line-height: 1.5; padding: 0; }
        .proj-pane::-webkit-scrollbar { width: 8px; }
        .proj-pane::-webkit-scrollbar-track { background: #1f2937; }
        .proj-pane::-webkit-scrollbar-thumb { background: #4b5563; border-radius: 4px; }
        
        /* CHAT WIDGET STYLES */
        .chat-widget { position: fixed; bottom: 20px; right: 20px; width: 500px; height: 75vh; background: #1f2937; border: 1px solid #374151; border-radius: 12px; display: flex; flex-direction: column; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); z-index: 9999; transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1); transform: translateY(110%); }
        .chat-visible { transform: translateY(0); }
        .chat-header { background: #111827; padding: 15px; border-bottom: 1px solid #374151; border-radius: 12px 12px 0 0; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
        .chat-messages { flex-grow: 1; overflow-y: auto; padding: 15px; font-size: 16px; background: #1f2937; scroll-behavior: smooth; }
        .msg-user { background: #3b82f6; color: white; padding: 10px 14px; border-radius: 12px 12px 0 12px; margin-bottom: 12px; align-self: flex-end; max-width: 85%; margin-left: auto; word-wrap: break-word; }
        .msg-bot { background: #374151; color: #e5e7eb; padding: 10px 14px; border-radius: 12px 12px 12px 0; margin-bottom: 12px; align-self: flex-start; max-width: 90%; word-wrap: break-word; }
        .chat-input-area { padding: 12px; background: #111827; border-top: 1px solid #374151; display: flex; align-items: center; border-radius: 0 0 12px 12px; gap: 8px; }
        .chat-toggle-btn { position: fixed; bottom: 20px; right: 20px; z-index: 9998; width: 70px; height: 70px; border-radius: 30px; background: #4f46e5; color: white; display: flex; align-items: center; justify-content: center; font-size: 30px; box-shadow: 0 10px 15px rgba(0,0,0,0.3); cursor: pointer; transition: transform 0.2s; }
        .chat-toggle-btn:hover { transform: scale(1.1); background: #4338ca; }
        .hidden-btn { display: none; }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen flex flex-col font-sans">
    <nav class="bg-gray-800 p-4 border-b border-gray-700 shadow-lg relative z-40">
        <div class="container mx-auto flex justify-between items-center">
            <a class="text-2xl font-bold text-blue-400 flex items-center" href="/">
                <i class="fas fa-microchip mr-3"></i>Q-Build 
                <span class="ml-2 text-xs text-white font-bold bg-green-600 px-2 py-0.5 rounded">V30 RESTORED</span>
            </a>
            <div class="flex items-center space-x-6">
                <div class="flex items-center space-x-2 text-sm bg-gray-700 px-3 py-1 rounded-full">
                    <i class="fas fa-hdd text-gray-400"></i>
                    <span class="text-gray-300 font-mono">{{ disk_free }}GB Free</span>
                </div>
                <a class="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded shadow transition font-bold" href="/create">
                    <i class="fas fa-plus mr-1"></i> New Project
                </a>
            </div>
        </div>
    </nav>
    <div class="container mx-auto p-4 flex-grow relative z-0">
        {{ body_content | safe }}
    </div>

    <!-- GLOBAL CHAT TOGGLE BUTTON -->
    <div class="chat-toggle-btn" id="chatToggle" onclick="toggleChat()">
        <i class="fas fa-robot"></i>
    </div>

    <!-- GLOBAL CHAT WIDGET -->
    <div class="chat-widget" id="chatWidget">
        <div class="chat-header" onclick="toggleChat()">
            <div class="flex items-center gap-2">
                <span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                <span class="font-bold text-white">QGenie Assistant</span>
            </div>
            <i class="fas fa-chevron-down text-gray-400 hover:text-white"></i>
        </div>
        
        <div class="chat-messages" id="chatMessages">
            <div class="msg-bot">
                Hello! I'm QGenie. I am ready to assist you.<br>
                <span class="text-xs text-gray-400 mt-1 block">Context: {{ project if project and project != 'GLOBAL' else 'General Dashboard' }}</span>
            </div>
        </div>

        <!-- File Preview Area -->
        <div id="filePreview" class="bg-gray-800 px-4 py-1 text-xs text-yellow-400 hidden flex justify-between items-center border-t border-gray-700">
            <span id="fileName">file.txt</span>
            <button onclick="clearFile()" class="text-red-400 hover:text-red-300"><i class="fas fa-times"></i></button>
        </div>
        
        <div class="chat-input-area">
            <input type="file" id="fileInput" class="hidden" onchange="handleFileSelect(this)">
            <button class="text-gray-400 hover:text-white transition" onclick="document.getElementById('fileInput').click()" title="Attach File (Text/Log/Code)">
                <i class="fas fa-paperclip"></i>
            </button>
            <input class="flex-grow bg-gray-800 text-white border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500 transition" 
                   id="chatInput" onkeypress="handleChatKey(event)" placeholder="Ask or attach logs..." type="text"/>
            <button class="text-blue-500 hover:text-blue-400 transition transform hover:scale-110" onclick="sendChat()">
                <i class="fas fa-paper-plane text-lg"></i>
            </button>
        </div>
    </div>

    <script>
        var currentProject = '{{ project }}';
        var chatOpen = false;
        var currentFile = null; 
        var lastErrorContext = "";

        function toggleChat() { 
            var w = document.getElementById('chatWidget'); 
            var btn = document.getElementById('chatToggle');
            chatOpen = !chatOpen; 
            
            if(chatOpen) { 
                w.classList.add('chat-visible'); 
                btn.classList.add('hidden-btn');
                loadChatHistory();
                setTimeout(() => document.getElementById('chatInput').focus(), 300);
            } else { 
                w.classList.remove('chat-visible'); 
                btn.classList.remove('hidden-btn');
            }
        }

        function handleChatKey(e) { if(e.key === 'Enter') sendChat(); }

        function handleFileSelect(input) {
            if (input.files && input.files[0]) {
                var file = input.files[0];
                if (file.size > 20000) { alert("File too large (Max 20KB)."); input.value = ''; return; }
                var reader = new FileReader();
                reader.onload = function(e) {
                    currentFile = { name: file.name, content: e.target.result };
                    document.getElementById('fileName').innerText = "📎 " + file.name;
                    document.getElementById('filePreview').classList.remove('hidden');
                };
                reader.readAsText(file);
            }
        }

        function clearFile() {
            currentFile = null;
            document.getElementById('fileInput').value = '';
            document.getElementById('filePreview').classList.add('hidden');
        }

        function addUserMessage(text, fileName=null) {
            var div = document.createElement('div'); div.className = 'msg-user'; 
            var content = text;
            if(fileName) content += `<br><span class="text-xs text-yellow-300 font-mono"><i class="fas fa-file-alt mr-1"></i>${fileName} attached</span>`;
            div.innerHTML = content;
            var box = document.getElementById('chatMessages');
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
        }
        
        function addBotMessage(text, isErrorAction=false) {
            var div = document.createElement('div'); div.className = 'msg-bot'; 
            var formatted = text.replace(/\*\*(.*?)\*\*/g, '<b>$1</b>');
            formatted = formatted.replace(/```(.*?)```/gs, '<pre class="bg-black p-2 rounded mt-1 text-xs overflow-x-auto">$1</pre>');
            div.innerHTML = formatted;
            if(isErrorAction) {
                var btn = document.createElement('button');
                btn.className = "block mt-2 bg-red-600 hover:bg-red-500 text-white text-xs px-3 py-1.5 rounded shadow font-bold";
                btn.innerHTML = "<i class='fas fa-bug mr-1'></i> Analyze Build Failure";
                btn.onclick = function() { sendChat("Please analyze the build failure based on the logs."); };
                div.appendChild(btn);
            }
            var box = document.getElementById('chatMessages');
            box.appendChild(div);
            box.scrollTop = box.scrollHeight;
        }

        function sendChat(forceMsg=null) {
            var inp = document.getElementById('chatInput');
            var txt = forceMsg || inp.value;
            if(!txt && !currentFile) return;
            if(!txt) txt = "Analyze this file.";

            addUserMessage(txt, currentFile ? currentFile.name : null);
            inp.value = '';
            
            var loadId = 'loading-' + Date.now();
            var box = document.getElementById('chatMessages');
            var loadDiv = document.createElement('div'); 
            loadDiv.className = 'msg-bot text-gray-400 italic flex items-center gap-2'; 
            loadDiv.id = loadId; 
            loadDiv.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Thinking...';
            box.appendChild(loadDiv);
            box.scrollTop = box.scrollHeight;
            
            var payload = { project: currentProject, question: txt, file: currentFile };
            if(lastErrorContext) { payload.context = lastErrorContext; lastErrorContext = ""; }
            clearFile();

            fetch('/chat_api', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(r => r.json()).then(data => {
                document.getElementById(loadId).remove();
                addBotMessage(data.response);
            }).catch(e => {
                document.getElementById(loadId).remove();
                addBotMessage("❌ Error contacting QGenie Server.");
            });
        }

        function loadChatHistory() {
            var url = '/chat_history/' + (currentProject === 'GLOBAL' ? 'GLOBAL' : currentProject);
            fetch(url).then(r => r.json()).then(data => {
                var box = document.getElementById('chatMessages');
                box.innerHTML = ''; 
                var welcome = document.createElement('div'); welcome.className = 'msg-bot';
                welcome.innerHTML = currentProject === 'GLOBAL' 
                    ? '<b>Hello!</b> Global Assistant ready.' 
                    : '<b>Hello!</b> Context loaded for <b>' + currentProject + '</b>.';
                box.appendChild(welcome);
                if(data.history) {
                    data.history.forEach(h => { addUserMessage(h.user); addBotMessage(h.bot); });
                }
                box.scrollTop = box.scrollHeight;
            });
        }
    </script>
</body>
</html>
"""

DASHBOARD_HTML = """
<div class="flex flex-col md:flex-row gap-6 h-[80vh]">
    <div class="w-full md:w-1/2 flex flex-col bg-gray-800 rounded-lg shadow-lg border border-gray-700">
        <div class="p-4 border-b border-gray-700 bg-gray-900 rounded-t-lg"><h3 class="text-xl font-bold text-yellow-500"><i class="fas fa-layer-group mr-2"></i>Meta-Qcom (Yocto)</h3></div>
        <div class="p-4 overflow-y-auto proj-pane flex-grow space-y-4">
            {% for name, data in projects.items() %}
            {% if data.get('type') == 'yocto' and states.get(name, {}).get('status') != 'deleting' %}
            <div onclick="location.href='/build/{{ name }}'" class="bg-gray-700 p-4 rounded border border-gray-600 hover:border-yellow-500 transition cursor-pointer relative group">
                <div class="flex justify-between items-start">
                    <div><h4 class="font-bold text-lg text-white">{{ name }}</h4><p class="text-gray-400 text-[10px] font-mono">{{ data.path }}</p></div>
                    <span class="px-2 py-1 rounded text-xs font-bold bg-gray-600 text-gray-300">{{ states.get(name, {}).get('status', 'IDLE').upper() }}</span>
                </div>
                <div class="flex justify-between items-center mt-3">
                    <div class="flex space-x-2">
                        <a href="/build/{{ name }}" class="bg-green-700 hover:bg-green-600 px-3 py-1 rounded text-white text-xs font-bold">Build</a>
                        <a href="/code/{{ name }}/" class="bg-purple-700 hover:bg-purple-600 px-3 py-1 rounded text-white text-xs">Code</a>
                        <a class="bg-yellow-600 hover:bg-yellow-500 px-3 py-1 rounded text-white text-xs font-bold" href="/viz/{{ name }}"><i class="fas fa-project-diagram"></i> Viz</a>
                    </div>
                    <a href="/delete/{{ name }}" onclick="return confirm('Delete?'); event.stopPropagation()" class="text-red-400 hover:text-red-300 opacity-0 group-hover:opacity-100"><i class="fas fa-trash"></i></a>
                </div>
            </div>
            {% endif %}
            {% endfor %}
        </div>
    </div>
    <div class="w-full md:w-1/2 flex flex-col bg-gray-800 rounded-lg shadow-lg border border-gray-700">
        <div class="p-4 border-b border-gray-700 bg-gray-900 rounded-t-lg"><h3 class="text-xl font-bold text-blue-400"><i class="fab fa-linux mr-2"></i>Upstream Kernel</h3></div>
        <div class="p-4 overflow-y-auto proj-pane flex-grow space-y-4">
            {% for name, data in projects.items() %}
            {% if data.get('type') == 'upstream' and states.get(name, {}).get('status') != 'deleting' %}
            <div onclick="location.href='/build/{{ name }}'" class="bg-gray-700 p-4 rounded border border-gray-600 hover:border-blue-400 transition cursor-pointer relative group">
                <div class="flex justify-between items-start">
                    <div><h4 class="font-bold text-lg text-white">{{ name }}</h4><p class="text-gray-400 text-[10px] font-mono">{{ data.path }}</p></div>
                    <span class="px-2 py-1 rounded text-xs font-bold bg-gray-600 text-gray-300">{{ states.get(name, {}).get('status', 'IDLE').upper() }}</span>
                </div>
                <div class="flex justify-between items-center mt-3">
                    <div class="flex space-x-2">
                        <a href="/build/{{ name }}" class="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded text-white text-xs font-bold">Build</a>
                        <a href="/code/{{ name }}/" class="bg-purple-700 hover:bg-purple-600 px-3 py-1 rounded text-white text-xs">Code</a>
                        <a class="bg-yellow-600 hover:bg-yellow-500 px-3 py-1 rounded text-white text-xs font-bold" href="/viz/{{ name }}"><i class="fas fa-project-diagram"></i> Viz</a>
                    </div>
                    <a href="/delete/{{ name }}" onclick="return confirm('Delete?'); event.stopPropagation()" class="text-red-400 hover:text-red-300 opacity-0 group-hover:opacity-100"><i class="fas fa-trash"></i></a>
                </div>
            </div>
            {% endif %}
            {% endfor %}
        </div>
    </div>
</div>
"""



VIZ_HTML = r'''
<!DOCTYPE html>
<html>
<head>
  <title>Audio Architect Viz</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.3/css/all.min.css" rel="stylesheet"/>
  <!-- Cytoscape Core + Layouts -->
  <script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
  <script src="https://unpkg.com/dagre@0.8.5/dist/dagre.min.js"></script>
  <script src="https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
  <style>
    html, body { height: 100%; width: 100%; margin: 0; overflow: hidden; background-color: #121212; color:#d4d4d4; }
    #main-viewport { width: 100%; height: calc(100vh - 110px); background:#1e1e1e; position: relative; }
    #cy { width: 100%; height: 100%; }
    .tab-btn.active { border-bottom: 2px solid #3b82f6; color:#3b82f6; }
  </style>

</head>
<body class="flex flex-col h-screen">
  <!-- HEADER -->
  <div class="bg-gray-800 p-3 shadow-md flex justify-between items-center shrink-0 z-20">
    <div class="flex items-center space-x-4">
      <h2 class="text-lg font-bold text-white">{{ project }}</h2>
      <span class="px-2 py-0.5 rounded bg-blue-600 text-xs text-white">{{ type }}</span>
    </div>
    <div class="flex items-center space-x-2">
      <input type="text" id="file-filter" placeholder="Filter files..." class="bg-gray-900 border border-gray-600 rounded px-2 py-1 text-sm text-gray-300 w-32" onkeyup="filterFiles()">
      <select class="bg-gray-900 text-sm p-1 border border-gray-600 rounded w-64 text-gray-300" id="file-select"></select>
      <button class="bg-green-600 hover:bg-green-700 px-3 py-1 rounded text-white text-sm" onclick="generate()">
        <i class="fas fa-play"></i> Viz
      </button>
      <!-- Layout selector -->
      <select id="layoutSelect" class="bg-gray-900 text-sm p-1 border border-gray-600 rounded text-gray-300">
        <option value="cose-bilkent">Layout: COSE</option>
        <option value="dagre">Layout: Dagre</option>
      </select>
      <!-- Exports -->
      <div class="relative group">
        <button class="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-white text-sm">
          <i class="fas fa-download"></i>
        </button>
        <div class="absolute right-0 mt-1 w-36 bg-gray-800 rounded shadow-lg hidden group-hover:block z-50 border border-gray-700">
          <a href="#" onclick="downloadPNG()" class="block px-4 py-2 text-sm text-gray-300 hover:bg-gray-700">Download PNG</a>
        </div>
      </div>
      <button class="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-white text-sm" onclick="fitToScreen()" title="Fit">
        <i class="fas fa-expand-arrows-alt"></i>
      </button>
    </div>
  </div>

  <!-- TABS -->
  <div class="bg-gray-800 border-t border-gray-700 flex px-4 space-x-6 text-sm shrink-0 z-20">
    <button class="tab-btn active py-2" id="tab-hardware" onclick="switchTab('hardware')">Hardware View</button>
    <button class="tab-btn py-2" id="tab-dailinks" onclick="switchTab('dailinks')">DAI Links</button>
    <button class="tab-btn py-2" id="tab-routing" onclick="switchTab('routing')">Audio Routing</button>
  </div>

  <!-- MAIN VIEWPORT -->
  <div id="main-viewport">
    <div id="loading" class="hidden absolute inset-0 flex items-center justify-center bg-gray-900 bg-opacity-75 z-50">
      <div class="text-blue-400 font-bold text-xl"><i class="fas fa-spinner fa-spin"></i> Processing...</div>
    </div>
    <div id="cy"></div>
  </div>

<script>
  // Register layouts
  if (typeof cytoscape !== 'undefined') {
    if (typeof cytoscapeCoseBilkent !== 'undefined') { cytoscape.use(cytoscapeCoseBilkent); }
    if (typeof cytoscapeDagre !== 'undefined') { cytoscape.use(cytoscapeDagre); }
  }

  let allFiles = [];
  let currentData = null;   // full API payload
  let graph = null;         // currentData.graph
  let activeTab = 'hardware';
  let cy = null;

  window.onload = function() {
    // Populate DTS list
    fetch(`/api/viz/list_dts?project={{ project }}&mode={{ type }}`)
      .then(r => r.json())
      .then(data => { allFiles = data.files || []; populateSelect(allFiles); });
  }

  

  function populateSelect(files) {
    const sel = document.getElementById('file-select');
    sel.innerHTML = '';
    files.forEach(f => { const opt = document.createElement('option'); opt.value = f; opt.innerText = f; sel.appendChild(opt); });
  }
  function filterFiles() {
    const term = document.getElementById('file-filter').value.toLowerCase();
    populateSelect((allFiles||[]).filter(f => f.toLowerCase().includes(term)));
  }

  async function generate() {
    const file = document.getElementById('file-select').value;
    if (!file) return;
    document.getElementById('loading').classList.remove('hidden');
    try {
      const res = await fetch('/api/viz/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: "{{ project }}", mode: "{{ type }}", filename: file })
      });
      currentData = await res.json();
      graph = currentData.graph || { nodes: [], edges: [] };
      renderActiveTab();
    } catch (e) {
      alert('Error: ' + e);
    } finally {
      document.getElementById('loading').classList.add('hidden');
    }
  }

  function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + tab).classList.add('active');
    renderActiveTab();
  }
function filterGraph(kind) {
  if (!graph) return { elements: [] };
  const edges = (graph.edges || []).filter(e => e.kind === kind);
  const nodeIds = new Set();
  edges.forEach(e => { nodeIds.add(e.source); nodeIds.add(e.target); });
  // Start with only nodes that participate
  let nodes = (graph.nodes || []).filter(n => nodeIds.has(n.id));
  // If none (e.g., hardware had no edges earlier), include all
  if (nodes.length === 0 && (graph.nodes||[]).length) nodes = graph.nodes;
  // ALWAYS include group containers so lanes appear
  const groups = (graph.nodes || []).filter(n => n.type === 'group');
  nodes = [...groups, ...nodes];
  // Map to Cytoscape elements (FIXED)
  const cyNodes = nodes.map(n => ({ data: {
    id: n.id,
    label: n.label || n.id,
    type: n.type || 'component',
    full_name: n.full_name || '',
    parent: n.parent || undefined
  }}));
  const cyEdges = edges.map(e => ({ data: { id: (e.source + '->' + e.target + ':' + e.label).slice(0,160), source: e.source, target: e.target, kind: e.kind, label: e.label || '' } }));
  return { elements: [...cyNodes, ...cyEdges] };
}

  function getElementsForActiveTab() {
    const kindMap = { 'hardware': 'hardware', 'dailinks': 'dai', 'routing': 'routing' };
    return filterGraph(kindMap[activeTab] || 'hardware').elements;
  }

function renderActiveTab() {
  const elements = getElementsForActiveTab();
  const layoutName = 'dagre';

  const style = [
    // Base node
    { selector: 'node', style: {
        'background-color': '#607d8b', 'label': 'data(label)', 'color':'#eee', 'font-size':'10px',
        'text-valign': 'center', 'text-halign': 'center',
        'width': 'label', 'height': 'label', 'padding':'6px',
        'border-width': 1, 'border-color': '#374151',
        'shape': 'round-rectangle'
    }},
    // LANE (group) styling
    { selector: 'node[type = "group"]', style: {
        'shape':'round-rectangle','background-color':'#111827','border-color':'#374151','border-width':2,
        'label':'data(label)','text-valign':'top','text-halign':'center','font-weight':'bold','color':'#9ca3af','padding':'20px'
    }},
    { selector: '$node > node', style: { 'padding': '4px' }},

    // Types
    { selector: 'node[type = "sndcard"]',  style: { 'background-color':'#ff9900', 'shape':'round-rectangle', 'font-weight':'bold' }},
    { selector: 'node[type = "codec"]',    style: { 'background-color':'#00c853', 'shape':'round-rectangle' }},
    { selector: 'node[type = "soc"]',      style: { 'background-color':'#2962ff', 'shape':'round-rectangle' }},

    // Buses & SWR lanes
    { selector: 'node[type = "bus"]',      style: { 'background-color':'#0ea5e9','color':'#022c22','border-color':'#155e75','border-width':1 }},
    { selector: 'node[id ^= "bus.swr"]',   style: { 'background-color':'#06b6d4','border-color':'#0e7490','border-width':1,'font-weight':'bold' }},

    // Amplifiers & Speakers
    { selector: 'node[type = "amp"]',      style: { 'background-color':'#f59e0b','color':'#1f2937','border-color':'#b45309' }},
    { selector: 'node[type = "speaker"]',  style: { 'background-color':'#fb923c','color':'#1f2937','border-color':'#c2410c' }},

    // Edges
    { selector: 'edge',                    style: {
        'width': 2, 'line-color':'#999', 'target-arrow-color':'#999', 'target-arrow-shape':'triangle', 'curve-style':'bezier',
        'label': 'data(label)', 'font-size':'9px', 'text-background-color':'#1e1e1e', 'text-background-opacity':1, 'text-background-padding':'2px', 'color':'#ccc'
    }},
    { selector: 'edge[kind = "dai"]',      style: { 'line-color':'#3b82f6', 'target-arrow-color':'#3b82f6' }},
    { selector: 'edge[kind = "routing"]',  style: { 'line-color':'#eab308', 'target-arrow-color':'#eab308' }}
  ];

  const layoutOpts = { name:'dagre', rankDir:'LR', nodeSep:60, edgeSep:20, rankSep:100, ranker:'tight-tree' };

  if (!cy) {
    cy = cytoscape({ container: document.getElementById('cy'), elements, style, layout: layoutOpts, pixelRatio: 1 });
    cy.on('tap', 'node', function(evt){
      const d = evt.target.data();
      const q = encodeURIComponent(d.full_name || d.label || d.id);
      window.open(`/search?project={{ project }}&q=${q}`, '_blank');
    });
  } else {
    cy.elements().remove();
    cy.add(elements);
    cy.style().fromJson(style).update();
    cy.layout(layoutOpts).run();
  }
}

</script>
</body>
</html>
'''

BUILD_CONSOLE_HTML = """
<div class="flex flex-col h-full space-y-4">
    <div class="bg-gray-800 p-4 rounded-lg shadow">
        <div class="flex justify-between items-center mb-4">
            <div><h2 class="text-2xl font-bold">{{ project }}</h2><div class="text-sm text-gray-400 mt-1"><span class="px-2 py-0.5 rounded bg-gray-700 text-white text-xs">{{ type.upper() }}</span> Status: <span class="font-bold" id="statusBadge">IDLE</span></div></div>
            <div class="flex space-x-3 items-center">
                <a class="bg-purple-600 hover:bg-purple-500 px-4 py-2 rounded text-white" href="/code/{{ project }}/" target="_blank"><i class="fas fa-external-link-alt mr-1"></i> Code</a>
                <a class="bg-yellow-600 hover:bg-yellow-500 px-4 py-2 rounded text-white" href="/viz/{{ project }}"><i class="fas fa-project-diagram mr-1"></i> Viz</a>
                <button class="hidden bg-red-600 text-white px-6 py-2 rounded" id="stopBtn" onclick="stopBuild()">STOP</button>
                <button class="bg-green-600 text-white px-6 py-2 rounded" id="buildBtn" onclick="startBuild()"><i class="fas fa-play mr-1"></i> Build</button>
                <a class="bg-gray-700 px-4 py-2 rounded text-white" href="/">Back</a>
            </div>
        </div>
        <!-- CONTROLS -->
        <div class="flex flex-wrap gap-4 items-center bg-gray-900 p-3 rounded border border-gray-700">
            {% if type == 'yocto' %}
            <div class="flex items-center space-x-2 border-r border-gray-600 pr-4">
                <label class="text-xs text-gray-400 font-bold uppercase">Topology</label>
                <label class="inline-flex items-center cursor-pointer"><input checked="" class="form-radio text-blue-600" name="topo" type="radio" value="ASOC"/><span class="ml-2 text-sm">ASOC</span></label>
                <label class="inline-flex items-center cursor-pointer"><input class="form-radio text-blue-600" name="topo" type="radio" value="AudioReach"/><span class="ml-2 text-sm">AR</span></label>
            </div>
            <div class="flex items-center space-x-2">
                 <select class="bg-gray-800 text-white text-sm border-none rounded p-1" id="cleanType"><option value="clean">Quick Clean</option><option value="cleanall">Deep Clean</option></select>
                 <button class="bg-orange-800 hover:bg-orange-700 px-3 py-1 rounded text-white text-sm" onclick="runClean()"><i class="fas fa-broom"></i></button>
            </div>
            {% else %}
            <!-- UPSTREAM CONTROLS (RESTORED) -->
            <div class="flex flex-col border-r border-gray-600 pr-4 mr-2">
                <label class="text-xs text-gray-400">Kernel Version</label>
                <div class="flex space-x-1">
                    <select class="bg-gray-800 text-white text-xs border border-gray-600 rounded p-1" id="refType" onchange="toggleRefInput()">
                        <option value="latest">Latest</option>
                        <option value="tag">Tag</option>
                        <option value="branch">Branch</option>
                    </select>
                    <input class="hidden bg-gray-800 text-white text-xs border border-gray-600 rounded p-1 w-40" id="refInput" list="refData" placeholder="Search..."/>
                    <datalist id="refData"></datalist>
                </div>
            </div>
            <div class="flex flex-col">
                <label class="text-xs text-gray-400">Image Name</label>
                <input class="bg-gray-800 text-white text-xs border border-gray-600 rounded p-1 w-32" id="imgName" placeholder="boot.img" type="text"/>
            </div>
            <div class="flex flex-col">
                <label class="text-xs text-gray-400">Firmware</label>
                <input class="bg-gray-800 text-white text-xs border border-gray-600 rounded p-1 w-32" id="fwTarget" list="fwList" placeholder="Search..."/>
                <datalist id="fwList"><option value="loading">Loading...</option></datalist>
            </div>
            <div class="flex flex-col">
                <label class="text-xs text-gray-400">DTB</label>
                <div class="flex space-x-1">
                   <input class="bg-gray-800 text-white text-xs border border-gray-600 rounded p-1 w-40" id="dtbSelect" list="dtbList" placeholder="Search..." value="lemans-evk.dtb"/>
                   <datalist id="dtbList"><option value="lemans-evk.dtb"></option></datalist>
                   <button class="text-gray-400 hover:text-white" onclick="scanDtb()" title="Scan DTBs"><i class="fas fa-sync"></i></button>
                </div>
            </div>
            <div class="border-l border-gray-600 pl-4">
                 <button class="bg-orange-800 hover:bg-orange-700 px-3 py-1 rounded text-white text-sm mt-4" onclick="runClean()"><i class="fas fa-broom"></i> Clean</button>
            </div>
            {% endif %}
        </div>
        
        <!-- ARTIFACTS -->
        <div class="mt-4 p-2 bg-gray-900 rounded border border-gray-600 hidden flex justify-between items-center" id="artifactArea">
            <div class="flex items-center gap-2">
                <i class="fas fa-gift text-yellow-400"></i>
                <span class="text-sm font-bold text-gray-300">Artifact Available:</span>
                <span class="font-mono text-xs text-blue-300" id="artifactPath"></span>
            </div>
            <a class="bg-blue-600 hover:bg-blue-500 px-3 py-1 rounded text-white text-xs font-bold" href="#" id="downloadLink"><i class="fas fa-download mr-1"></i> Download</a>
        </div>
    </div>
    
    {% if type == 'yocto' %}
    <div class="bg-gray-800 p-4 rounded-lg shadow border-l-4 border-yellow-500">
        <h3 class="text-lg font-bold mb-2 text-yellow-500"><i class="fas fa-tools mr-2"></i>Kernel Dev Kit</h3>
        <div class="flex items-center space-x-4">
            <div class="flex-grow relative">
                <input class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm text-white font-mono" id="recipeName" list="common_recipes" placeholder="Recipe Name" type="text" value="linux-qcom-next"/>
                <datalist id="common_recipes"><option value="linux-qcom-next"></option><option value="audioreach-kernel"></option></datalist>
            </div>
            <button class="bg-blue-700 hover:bg-blue-600 text-white px-4 py-2 rounded text-sm" onclick="runDevtool('modify')"><i class="fas fa-edit mr-1"></i> Modify</button>
            <button class="bg-red-900 hover:bg-red-800 text-white px-4 py-2 rounded text-sm" onclick="runDevtool('reset')"><i class="fas fa-undo mr-1"></i> Reset</button>
        </div>
    </div>
    {% endif %}
    <div class="flex-grow bg-black rounded h-[500px]" id="terminal"></div>
</div>
<script>
    var socket = io(); var project = '{{ project }}'; var ptype = '{{ type }}';
    var term = new Terminal({theme:{background:'#000',foreground:'#e5e5e5'}}); 
    var fitAddon = new FitAddon.FitAddon(); term.loadAddon(fitAddon); term.open(document.getElementById('terminal')); fitAddon.fit(); 
    
    socket.on('connect', function() { 
        socket.emit('join_project', {project: project}); 
        if(ptype=='upstream') { 
            socket.emit('scan_fw', {}); 
            socket.emit('scan_dtb', {project: project}); 
        }
        socket.emit('check_artifacts', {project: project});
    });
    
    socket.on('log_chunk', function(msg){ term.write(msg.data); });
    socket.on('build_status', function(msg){ updateUI(msg.status); });
    socket.on('fw_list', function(msg){
        var list = document.getElementById('fwList'); list.innerHTML = '';
        msg.targets.forEach(t => { var opt = document.createElement('option'); opt.value = t; list.appendChild(opt); });
    });
    socket.on('dtb_list', function(msg){
        var list = document.getElementById('dtbList'); 
        if(msg.dtbs.length > 0) {
            list.innerHTML = '';
            msg.dtbs.forEach(d => { var opt = document.createElement('option'); opt.value = d; list.appendChild(opt); });
        }
    });
    socket.on('git_refs', function(msg){
        var list = document.getElementById('refData');
        list.innerHTML = '';
        msg.refs.forEach(r => { var opt = document.createElement('option'); opt.value = r; list.appendChild(opt); });
        var inp = document.getElementById('refInput');
        inp.placeholder = "Type to search...";
        inp.disabled = false;
    });
    socket.on('artifact_found', function(msg){
        var area = document.getElementById('artifactArea');
        var path = document.getElementById('artifactPath');
        var link = document.getElementById('downloadLink');
        if(msg.found) {
            area.classList.remove('hidden');
            path.innerText = msg.path;
            link.href = "/download_artifact/" + project + "?file=" + encodeURIComponent(msg.filename);
        } else {
            area.classList.add('hidden');
        }
    });
    
    socket.on('build_failed_context', function(msg){
        if(typeof addBotMessage === 'function') {
            addBotMessage("Build Failed! 🔴", true);
            lastErrorContext = msg.context; 
        }
    });

    function toggleRefInput() {
        var type = document.getElementById('refType').value;
        var inp = document.getElementById('refInput');
        if(type === 'latest') { 
            inp.classList.add('hidden'); 
        } else { 
            inp.classList.remove('hidden'); 
            inp.value = '';
            inp.placeholder = "Loading...";
            inp.disabled = true;
            socket.emit('get_git_refs', {project: project, type: type});
        }
    }

    function updateUI(status){ 
        var b=document.getElementById('buildBtn'); var s=document.getElementById('stopBtn'); 
        document.getElementById('statusBadge').innerText=status.toUpperCase(); 
        if(status=='running'){ b.classList.add('hidden'); s.classList.remove('hidden'); } 
        else { b.classList.remove('hidden'); s.classList.add('hidden'); socket.emit('check_artifacts', {project: project}); }
    } 
    
    function startBuild(){ 
        term.clear(); 
        if(ptype == 'yocto') {
            var topo = document.querySelector('input[name="topo"]:checked').value; 
            socket.emit('start_build',{project:project, topology: topo}); 
        } else {
            var fw = document.getElementById('fwTarget').value;
            var dtb = document.getElementById('dtbSelect').value;
            var img = document.getElementById('imgName').value;
            var refType = document.getElementById('refType').value;
            var refVal = document.getElementById('refInput').value;
            if (refType !== 'latest' && !refVal) { alert("Please select a " + refType); return; }
            if(!fw || fw === 'loading') fw = 'sa8775p';
            socket.emit('start_build', {project: project, fw_target: fw, dtb: dtb, img_name: img, git_ref_type: refType, git_ref_val: refVal});
        }
    } 
    function stopBuild(){ socket.emit('stop_build',{project:project}); }
    function runClean() { if(confirm("Clean build artifacts?")) { term.clear(); socket.emit('clean_build', {project: project, type: (ptype=='yocto' ? document.getElementById('cleanType').value : 'upstream')}); } }
    function runDevtool(action) { var r = document.getElementById('recipeName').value; if(confirm(action.toUpperCase() + " " + r + "?")) { term.clear(); socket.emit('devtool_action', {project: project, action: action, recipe: r}); } }
    function scanDtb() { socket.emit('scan_dtb', {project: project}); }
</script>
"""

EXPLORER_HTML = """
<div class="flex h-[80vh] bg-gray-800 rounded-lg shadow-lg overflow-hidden border border-gray-700">
    <div class="w-1/5 bg-gray-900 border-r border-gray-700 flex flex-col">
        <div class="p-3 border-b border-gray-700 bg-gray-800 font-bold flex justify-between"><span>{{ project }}</span><a class="text-xs bg-gray-700 px-2 py-1 rounded hover:bg-gray-600" href="/build/{{ project }}">Back</a></div>
        <div class="overflow-y-auto flex-grow p-2 text-sm font-mono">
            {% if parent_dir %}<a class="block p-1 text-yellow-400 hover:bg-gray-800" href="/code/{{ project }}/{{ parent_dir }}"><i class="fas fa-level-up-alt mr-2"></i>..</a>{% endif %}
            {% for d in dirs %}<a class="block p-1 text-blue-400 hover:bg-gray-800 truncate" href="/code/{{ project }}/{{ current_path }}/{{ d }}"><i class="fas fa-folder mr-2"></i>{{ d }}</a>{% endfor %}
            {% for f in files %}<a class="block p-1 text-gray-300 hover:bg-gray-800 truncate" href="/code/{{ project }}/{{ current_path }}/{{ f }}"><i class="far fa-file mr-2"></i>{{ f }}</a>{% endfor %}
        </div>
    </div>
    <div class="w-4/5 flex flex-col bg-[#282c34] relative">
        <div class="p-2 bg-gray-800 border-b border-gray-700 text-xs text-gray-400 flex justify-between items-center">
            <span class="font-mono text-blue-300">{{ current_path }}</span>
            <div class="flex space-x-2 items-center">
                <span class="text-xs text-gray-500 mr-2"><i class="fas fa-mouse-pointer"></i> Double-click to search</span>
                {% if is_file %}
                <button class="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded text-white text-xs" id="editBtn" onclick="window.open(window.location.pathname.replace('/code/', '/editor/view/'), '_blank')"><i class="fas fa-code mr-1"></i> Pro Editor</button>
                <button class="hidden bg-green-600 hover:bg-green-500 px-3 py-1 rounded text-white text-xs" id="saveBtn" onclick="saveFile()"><i class="fas fa-save mr-1"></i> Save</button>
                <button class="hidden bg-gray-600 hover:bg-gray-500 px-3 py-1 rounded text-white text-xs" id="cancelBtn" onclick="location.reload()">Cancel</button>
                {% endif %}
            </div>
        </div>
        <div class="flex-grow overflow-auto p-4 relative" id="codeContainer">
            {% if is_file %}
            <div class="code-container" id="readView">
                <div class="line-numbers">{% for i in range(1, line_count + 1) %}<div id="L{{ i }}">{{ i }}</div>{% endfor %}</div>
                <div class="code-content"><pre><code class="language-{{ ext }}" id="codeBlock">{{ content }}</code></pre></div>
            </div>
            <div class="code-container hidden h-full" id="editView">
                <div class="line-numbers">{% for i in range(1, line_count + 1) %}<div>{{ i }}</div>{% endfor %}</div>
                <div class="code-content h-full"><textarea class="editor" id="fileEditor" spellcheck="false">{{ content }}</textarea></div>
            </div>
            {% else %}
            <div class="flex items-center justify-center h-full text-gray-500"><p>Select a file to view content</p></div>
            {% endif %}
        </div>
    </div>
</div>
<script>
    hljs.highlightAll();
    window.onload = function() {
        var hash = window.location.hash;
        if(hash) {
            var el = document.getElementById('L' + hash.replace('#L', ''));
            if(el) { el.scrollIntoView({block: 'center'}); el.style.color = '#fbbf24'; el.style.fontWeight = 'bold'; }
        }
    };
    document.getElementById('codeBlock').addEventListener('dblclick', function(e) {
        var selection = window.getSelection().toString().trim();
        if(selection && selection.length > 2) {
            window.open('/search?project={{ project }}&q=' + encodeURIComponent(selection), '_blank');
        }
    });
    function enableEdit() { document.getElementById('readView').classList.add('hidden'); document.getElementById('editView').classList.remove('hidden'); document.getElementById('editBtn').classList.add('hidden'); document.getElementById('saveBtn').classList.remove('hidden'); document.getElementById('cancelBtn').classList.remove('hidden'); }
    function saveFile() {
        var content = document.getElementById('fileEditor').value;
        var btn = document.getElementById('saveBtn'); btn.innerHTML = 'Saving...';
        fetch('/save_file', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ project: '{{ project }}', path: '{{ current_path }}', content: content }) }).then(r => r.json()).then(data => { if(data.status === 'ok') location.reload(); else { alert('Error: ' + data.error); btn.innerHTML = 'Save'; } });
    }
</script>
"""

CREATE_STEP1_HTML = """
<div class="max-w-xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Step 1: Project Setup</h2>
    <form action="/create_step2" class="space-y-4" method="POST">
        <div><label class="block text-sm text-gray-400 mb-1">Project Name</label><input class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-white" name="name" required="" type="text"/></div>
        <div>
            <label class="block text-sm text-gray-400 mb-1">Build System</label>
            <div class="grid grid-cols-2 gap-4">
                <label class="cursor-pointer border border-gray-600 rounded p-4 hover:bg-gray-700 flex flex-col items-center">
                    <input checked="" class="mb-2" name="type" type="radio" value="yocto"/>
                    <span class="font-bold text-yellow-400">Yocto (KAS)</span>
                </label>
                <label class="cursor-pointer border border-gray-600 rounded p-4 hover:bg-gray-700 flex flex-col items-center">
                    <input class="mb-2" name="type" type="radio" value="upstream"/>
                    <span class="font-bold text-blue-400">Upstream Kernel</span>
                </label>
            </div>
        </div>
        <button class="w-full bg-blue-600 hover:bg-blue-500 py-3 rounded font-bold mt-4" type="submit">Next <i class="fas fa-arrow-right ml-2"></i></button>
    </form>
</div>
"""

CREATE_STEP2_HTML = """
<div class="max-w-2xl mx-auto bg-gray-800 p-8 rounded-lg shadow-lg">
    <h2 class="text-2xl font-bold mb-6">Step 2: Configuration</h2>
    <form action="/finish_create" class="space-y-6" method="POST">
        <input name="name" type="hidden" value="{{ project }}"/>
        <input name="type" type="hidden" value="{{ type }}"/>
        
        {% if type == 'yocto' %}
        <div>
            <label class="block text-sm text-gray-400 mb-1">Yocto Target Board</label>
            <select class="w-full bg-gray-900 border border-gray-600 rounded p-3 text-white" name="board">{% for b in boards %}<option value="{{ b }}">{{ b }}</option>{% endfor %}</select>
        </div>
        {% else %}
        <div>
            <label class="block text-sm text-gray-400 mb-1">Kernel Repository</label>
            <select class="w-full bg-gray-900 border border-gray-600 rounded p-3 text-white" name="kernel_repo">
                <option value="git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git">Linux Stable</option>
                <option value="git://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git">Linux Next</option>
            </select>
        </div>
        {% endif %}
        
        <button class="w-full bg-green-600 hover:bg-green-500 py-3 rounded font-bold mt-4" type="submit">Create Project</button>
    </form>
</div>
"""

SEARCH_HTML = """
<div class="max-w-6xl mx-auto bg-gray-800 p-6 rounded-lg shadow-lg h-[85vh] flex flex-col">
    <div class="flex justify-between items-center mb-4">
        <h2 class="text-2xl font-bold">Search: <span class="text-yellow-400">{{ query }}</span></h2>
        <a href="/code/{{ project }}/" class="text-sm bg-gray-700 px-3 py-1 rounded hover:bg-gray-600">Back to Code</a>
                <a class="bg-yellow-600 hover:bg-yellow-500 px-4 py-2 rounded text-white" href="/viz/{{ project }}"><i class="fas fa-project-diagram mr-1"></i> Viz</a>
    </div>
    <div class="flex-grow overflow-y-auto bg-gray-900 p-4 rounded border border-gray-700 font-mono text-sm">
        {% if results %}
            {% for r in results %}
            <div class="mb-2">
                <a href="/code/{{ project }}/{{ r.file }}#L{{ r.line }}" class="text-blue-400 hover:underline break-all">{{ r.file }}:{{ r.line }}</a>
                <div class="text-gray-400 pl-4 whitespace-pre-wrap">{{ r.content }}</div>
            </div>
            {% endfor %}
        {% else %}
            <div class="text-gray-500 text-center mt-10">No results found for "{{ query }}"</div>
        {% endif %}
    </div>
</div>
"""

# --- ROUTES ---
@app.route('/')
def index():
    threading.Thread(target=ensure_tools).start()
    reg = sync_registry()
    pct, free = get_disk_usage()
    # RESTORED GLOBAL PROJECT CONTEXT
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, project='GLOBAL', body_content=render_template_string(DASHBOARD_HTML, projects=reg, states=BUILD_STATES))

@app.route('/create')
def create_step1_view():
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, project='GLOBAL', body_content=CREATE_STEP1_HTML)

@app.route('/create_step2', methods=['POST'])
def create_step2_action():
    name = request.form['name']
    ptype = request.form['type']
    base_dir = YOCTO_BASE if ptype == 'yocto' else UPSTREAM_BASE
    proj_path = os.path.join(base_dir, name)
    os.makedirs(proj_path, exist_ok=True)
    
    boards = []
    if ptype == 'yocto':
        repo_path = os.path.join(proj_path, "meta-qcom")
        if not os.path.exists(repo_path): subprocess.run(["git", "clone", "https://github.com/qualcomm-linux/meta-qcom.git", repo_path], check=True)
        ci_path = os.path.join(repo_path, "ci")
        boards = [f for f in os.listdir(ci_path) if f.endswith('.yml')] if os.path.exists(ci_path) else []
        boards.sort()
    
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, project='GLOBAL', body_content=render_template_string(CREATE_STEP2_HTML, project=name, type=ptype, boards=boards))

@app.route('/finish_create', methods=['POST'])
def finish_create():
    name = request.form['name']; ptype = request.form['type']
    base_dir = YOCTO_BASE if ptype == 'yocto' else UPSTREAM_BASE
    proj_path = os.path.join(base_dir, name)
    
    cfg = {'type': ptype, 'created': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
    if ptype == 'yocto':
        cfg['kas_files'] = f"meta-qcom/ci/{request.form['board']}"
        cfg['image'] = "qcom-multimedia-image"
    else: cfg['kernel_repo'] = request.form['kernel_repo']
    
    with open(os.path.join(proj_path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
    sync_registry()
    return redirect('/')

@app.route('/delete/<name>')
def delete(name):
    # Restored background delete logic
    reg = sync_registry()
    if name in reg:
        path = reg[name]['path']
        BUILD_STATES[name] = {'status': 'deleting'}
        threading.Thread(target=background_delete, args=(path, name)).start()
        # Remove from local registry immediately
        del reg[name]
        with open(REGISTRY_FILE, "w") as f: yaml.dump(reg, f)
    return redirect('/')

@app.route('/download_artifact/<name>')
def download_artifact(name):
    path, cfg = get_config(name)
    if not path: return abort(400)
    filename = request.args.get('file')
    if not filename: return abort(400)
    abs_path = os.path.abspath(os.path.join(path, filename))
    if not abs_path.startswith(os.path.abspath(path)): return abort(403)
    if os.path.exists(abs_path): return send_file(abs_path, as_attachment=True)
    return abort(404)

@app.route('/search')
def search_view():
    project = request.args.get('project'); query = request.args.get('q')
    if not project or not query: return "Missing params", 400
    path, _ = get_config(project)
    
    results = []
    if path:
        # Secure grep (Restored)
        safe_query = re.escape(query)
        cmd = ["grep", "-rnI", safe_query, "."]
        try:
            out = subprocess.check_output(cmd, cwd=path, text=True, timeout=5)
            lines = out.splitlines()[:50] 
            for line in lines:
                parts = line.split(':', 2)
                if len(parts) >= 3:
                    results.append({'file': parts[0], 'line': parts[1], 'content': parts[2]})
        except: pass
        
    pct, free = get_disk_usage()
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, project=project, body_content=render_template_string(SEARCH_HTML, project=project, query=query, results=results))

@app.route('/code/<name>/', defaults={'req_path': ''})
@app.route('/code/<name>/<path:req_path>')
def code_explorer(name, req_path):
    # RESTORED FULL EXPLORER LOGIC
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
            return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, project=name, body_content=render_template_string(EXPLORER_HTML, project=name, current_path=req_path, dirs=dirs, files=files, parent_dir=parent, is_file=False))
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
            return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, project=name, body_content=render_template_string(EXPLORER_HTML, project=name, current_path=rel_parent, dirs=dirs, files=files, parent_dir=os.path.dirname(rel_parent) if rel_parent else None, is_file=True, content=content, ext=ext, line_count=line_count))
    except Exception as e: return f"Explorer Er: {str(e)}", 500
    return abort(404)

@app.route('/save_file', methods=['POST'])
def save_file_endpoint():
    data = request.get_json()
    name = data.get('project'); rel_path = data.get('path'); content = data.get('content')
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
    return render_template_string(BASE_HTML, disk_pct=pct, disk_free=free, project=name, body_content=render_template_string(BUILD_CONSOLE_HTML, project=name, type=ptype))

# --- CHAT API (WITH FILE SUPPORT) ---
# --- CHAT API ---
@app.route('/chat_api', methods=['POST'])
def chat_api():
    data = request.get_json()
    project = data.get('project', 'GLOBAL')
    question = data.get('question', '')
    context_logs = data.get('context', '')
    file_data = data.get('file', None)

    # 1. Determine Paths & Context
    history_file = os.path.join(WORK_DIR, "global_chat_history.json")
    system_context = "You are QGenie, a helper for Qualcomm Q-Build Manager."

    if project and project != "GLOBAL":
        path, cfg = get_config(project)
        if path:
            history_file = os.path.join(path, "qgenie_history.json")
            system_context = f"You are QGenie. Project: {project}. Type: {cfg.get('type', 'Unknown')}."
    
    # This was likely the line causing the Syntax Error:
    if context_logs:
        system_context += f"\n\nRECENT LOGS:\n{context_logs}"

    # 2. Call the Shared Helper
    # Ensure ai_helper is imported at the top of the file!
    try:
        resp = ai_helper.chat_with_history(history_file, system_context, question, file_data)
    except Exception as e:
        resp = f"Error calling AI Helper: {str(e)}"
    
    return jsonify({'response': resp})

@app.route('/chat_history/<project>')
def chat_history(project):
    if project == 'GLOBAL':
        history_file = os.path.join(WORK_DIR, "global_chat_history.json")
    else:
        path, _ = get_config(project)
        history_file = os.path.join(path, "qgenie_history.json") if path else None

    if history_file and os.path.exists(history_file):
        with open(history_file) as f: return jsonify({'history': json.load(f)})
    return jsonify({'history': []})

# --- SOCKET EVENTS ---
@socketio.on('join_project')
def handle_join(data):
    join_room(data['project'])
    name = data['project']
    if name in BUILD_STATES: 
        if 'logs' in BUILD_STATES[name]: emit('log_chunk', {'data': "".join(BUILD_STATES[name]['logs'])})
        emit('build_status', {'status': BUILD_STATES[name].get('status', 'unknown')})

@socketio.on('check_artifacts')
def handle_check_artifacts(data):
    name = data['project']
    path, cfg = get_config(name)
    if not path: return
    found = False; filename = ""; abs_p = ""
    target_name = cfg.get('target_image', 'boot.img')
    if cfg.get('type') == 'upstream':
        img = os.path.join(path, target_name)
        if os.path.exists(img): found = True; filename = target_name; abs_p = img
    else:
        kas_files = cfg.get('kas_files', '')
        machine = 'qcm6490'
        if 'iq-9075' in kas_files: machine = 'iq-9075-evk'
        elif 'rb5' in kas_files: machine = 'rb5'
        elif 'lemans' in kas_files: machine = 'lemans-evk'
        img = find_yocto_image(path, machine)
        if img: found = True; filename = os.path.relpath(img, path); abs_p = img
    socketio.emit('artifact_found', {'found': found, 'path': abs_p, 'filename': filename}, to=name)

# RESTORED GIT FETCH LOGIC (ls-remote)
@socketio.on('get_git_refs')
def handle_get_refs(data):
    name = data.get('project'); rtype = data.get('type')
    path, cfg = get_config(name); repo = cfg.get('kernel_repo')
    refs = []
    if repo:
        cmd_arg = "--heads" if rtype == 'branch' else "--tags"
        try:
            # Using ls-remote avoids needing a local checkout
            out = subprocess.check_output(["git", "ls-remote", cmd_arg, repo], text=True, timeout=10)
            for line in out.splitlines():
                parts = line.split('\t')
                if len(parts) > 1:
                    ref = parts[1].replace('refs/heads/', '').replace('refs/tags/', '')
                    if not ref.endswith('^{}'): refs.append(ref)
        except Exception as e: refs = [f"Error: {e}"]
    emit('git_refs', {'refs': sorted(refs)})

@socketio.on('start_build')
def handle_build(data):
    name = data['project']
    path, cfg = get_config(name)
    ptype = cfg.get('type', 'yocto')
    
    if ptype == 'yocto':
        topo = data.get('topology', 'ASOC')
        cfg['topology'] = topo
        with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
        distro = 'meta-qcom/ci/qcom-distro-prop-image.yml' if topo == 'AudioReach' else 'meta-qcom/ci/qcom-distro.yml'
        kas_args = f"{cfg.get('kas_files')}:{distro}"
        cmd = f"kas shell {kas_args} -c 'bitbake {cfg.get('image')}'"
        threading.Thread(target=run_build_task, args=(cmd, name)).start()
    else:
        # Upstream Logic (Restored & Improved)
        fw_target = data.get('fw_target', 'sa8775p')
        dtb_name = data.get('dtb', 'lemans-evk.dtb')
        img_name = data.get('img_name', '').strip()
        if not img_name: img_name = 'boot.img'
        
        git_ref_type = data.get('git_ref_type', 'latest')
        git_ref_val = data.get('git_ref_val', '')

        cfg['target_image'] = img_name
        with open(os.path.join(path, "config.yaml"), "w") as f: yaml.dump(cfg, f)
        
        repo = cfg.get('kernel_repo')
        mkboot = os.path.join(TOOLS_DIR, "mkbootimg", "mkbootimg.py")
        initramfs = os.path.join(TOOLS_DIR, "initramfs-test.cpio.gz")
        fw_src = os.path.join(TOOLS_DIR, "linux-firmware", "qcom", fw_target)
        
        script = [
            f"echo '--- UPSTREAM BUILD STARTED FOR {name} ---'",
            f"echo 'Target Firmware: {fw_target}'", f"echo 'Output Image: {img_name}'",
            f"if [ ! -d 'linux' ]; then echo '>> Cloning Kernel...'; git clone {repo} linux; fi", "cd linux"
        ]
        
        if git_ref_type != 'latest' and git_ref_val:
            script.append(f"echo '>> Fetching updates...'")
            script.append("git fetch --all")
            script.append(f"echo '>> Checking out {git_ref_val}...'")
            script.append(f"git checkout {git_ref_val}")
            if git_ref_type == 'branch': script.append(f"git pull origin {git_ref_val}")
        else: 
            script.append("echo '>> Using Latest (Default Branch)...'")
            script.append("git checkout $(git remote show origin | grep 'HEAD branch' | cut -d' ' -f5) || true")
            script.append("git pull")
        
        script.extend([
            "mkdir -p modules_dir firmwares_dir test_utils",
            "export ARCH=arm64", "export CROSS_COMPILE=aarch64-linux-gnu-",
            "echo '>> Configuring...'", "make -j$(nproc) defconfig", 
            "echo '>> Compiling Image & Modules...'", "make -j$(nproc) Image.gz dtbs modules",
            "echo '>> Installing Modules...'", "make -j$(nproc) modules_install INSTALL_MOD_PATH=modules_dir INSTALL_MOD_STRIP=1",
            "cd modules_dir", "find . | cpio -o -H newc | gzip -9 > ../modules.cpio.gz", "cd ..",
            "echo '>> Packaging Firmware...'",
            f"mkdir -p firmwares_dir/lib/firmware/qcom/{fw_target}",
            f"if [ -d '{fw_src}' ]; then cp -r {fw_src}/* firmwares_dir/lib/firmware/qcom/{fw_target}/; else echo 'WARNING: Firmware source not found'; fi",
            "cd firmwares_dir", "find . | cpio -o -H newc | gzip -9 > ../firmwares.cpio.gz", "cd ..",
            "echo '>> Creating Final Initramfs...'",
            "touch test_utils.cpio.gz", f"cat {initramfs} modules.cpio.gz firmwares.cpio.gz test_utils.cpio.gz > final-initramfs.cpio.gz",
            "echo '>> Generating Boot Image...'",
            f"python3 {mkboot} --kernel arch/arm64/boot/Image.gz --cmdline 'root=/dev/ram0 console=tty0 console=ttyMSM0,115200n8 clk_ignore_unused pd_ignore_unused' --ramdisk final-initramfs.cpio.gz --dtb arch/arm64/boot/dts/qcom/{dtb_name} --pagesize 2048 --header_version 2 --output ../{img_name}",
            f"echo '--- SUCCESS: {img_name} created ---'"
        ])
        threading.Thread(target=run_build_task, args=(" && ".join(script), name)).start()

@socketio.on('clean_build')
def handle_clean(data):
    name = data['project']; clean_type = data.get('type', 'clean'); path, cfg = get_config(name)
    if cfg.get('type') == 'yocto':
        topo = cfg.get('topology', 'ASOC')
        distro = 'meta-qcom/ci/qcom-distro-prop-image.yml' if topo == 'AudioReach' else 'meta-qcom/ci/qcom-distro.yml'
        kas_args = f"{cfg.get('kas_files')}:{distro}"
        cmd = f"kas shell {kas_args} -c 'bitbake -c {clean_type} {cfg.get('image')}'"
    else: cmd = "cd linux && make clean"
    threading.Thread(target=run_build_task, args=(cmd, name)).start()

@socketio.on('devtool_action')
def handle_devtool(data):
    name = data['project']; action = data['action']; recipe = data['recipe']; path, cfg = get_config(name)
    topo = cfg.get('topology', 'ASOC')
    distro = 'meta-qcom/ci/qcom-distro-prop-image.yml' if topo == 'AudioReach' else 'meta-qcom/ci/qcom-distro.yml'
    kas_args = f"{cfg.get('kas_files')}:{distro}"
    cmd = f"kas shell {kas_args} -c 'bitbake {recipe}; devtool modify {recipe}'" if action == 'modify' else f"kas shell {kas_args} -c 'devtool {action} {recipe}'"
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
    # RESTORED TOOLS_DIR LOGIC
    fw_base = os.path.join(TOOLS_DIR, "linux-firmware", "qcom")
    targets = [d for d in os.listdir(fw_base) if os.path.isdir(os.path.join(fw_base, d))] if os.path.exists(fw_base) else ['sa8775p', 'sm8550']
    socketio.emit('fw_list', {'targets': sorted(targets)})

@socketio.on('scan_dtb')
def handle_scan_dtb(data):
    name = data.get('project'); path, cfg = get_config(name); dtbs = []
    if path:
        dts_path = os.path.join(path, "linux/arch/arm64/boot/dts/qcom")
        if os.path.exists(dts_path): dtbs = [os.path.basename(f).replace('.dts', '.dtb') for f in glob.glob(os.path.join(dts_path, "*.dts"))]
    if not dtbs: dtbs = ['lemans-evk.dtb'] 
    socketio.emit('dtb_list', {'dtbs': sorted(dtbs)})



# --- VISUALIZATION ROUTES ---
# (Inserted by fix_web_manager_final.py)

@app.route('/viz/<project>')
def viz_dashboard(project):
    """Renders the Visualization Page"""
    path, cfg = get_config(project)
    # Retry sync if project not found (covers some edge cases)
    if not path:
        sync_registry()
        path, cfg = get_config(project)
        
    if not path: 
        return "Project path not found. Please ensure project exists.", 404
        
    pct, free = get_disk_usage()
    # Check if VIZ_HTML exists
    if 'VIZ_HTML' not in globals():
        return "Error: VIZ_HTML template is missing from web_manager.py", 500
        
    return render_template_string(BASE_HTML, 
                                  disk_pct=pct, disk_free=free, 
                                  project=project, 
                                  body_content=render_template_string(VIZ_HTML, project=project, type=cfg.get('type', 'upstream')))

@app.route('/api/viz/list_dts')
def api_list_dts():
    """API to populate the Dropdown"""
    project = request.args.get('project')
    mode = request.args.get('mode')
    path, _ = get_config(project)
    if not path: return jsonify({'files': []})
    
    # Lazy import to ensure package exists
    try:
        from visualization.path_manager import PathManager
    except ImportError:
        return jsonify({'error': 'Visualization package missing'}), 500

    pm = PathManager(path, mode)
    files = pm.list_dts_files()
    return jsonify({'files': files})

@app.route('/api/viz/generate', methods=['POST'])
def api_viz_generate():
    """API to parse and return Mermaid Code"""
    data = request.json
    project = data.get('project')
    mode = data.get('mode')
    filename = data.get('filename')
    path, _ = get_config(project)
    
    if not path: return jsonify({'error': 'Project not found'}), 404
    
    try:
        from visualization.path_manager import PathManager
        from visualization.dts_parser import DtsParser
        from visualization.diagram_builder import DiagramBuilder
    except ImportError:
         return jsonify({'error': 'Visualization package missing'}), 500

    pm = PathManager(path, mode)
    base_path = pm.get_dts_base_path()
    if not base_path: return jsonify({'error': 'DTS path not found'}), 404
    
    parser = DtsParser(base_path)
    parsed_data = parser.parse(filename)
    
    # [V16 FIX] Initialize Parser with Base Path
    parser = DtsParser(base_path)
    parser.parse(filename)
    
    # [V16 FIX] Build all diagrams
    builder = DiagramBuilder(parser)
    diagrams = builder.build_all()
    # Ensure JSON graph is included for Cytoscape (Step-2 harden)
    try:
        diagrams["graph"] = builder.build_graph_json()
    except Exception:
        diagrams["graph"] = {"nodes": [], "edges": []}

    return jsonify(diagrams)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=SERVER_PORT, allow_unsafe_werkzeug=True)


