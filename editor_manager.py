import os
import subprocess
import json
from flask import Blueprint, render_template_string, request, jsonify

editor_bp = Blueprint('editor_bp', __name__)

# --- ROBUST MONACO EDITOR TEMPLATE ---
MONACO_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Pro Editor - {{ filename }}</title>
    <style>
        html, body { height: 100%; margin: 0; background-color: #1e1e1e; color: #ccc; overflow: hidden; }
        #layout { display: flex; flex-direction: column; height: 100vh; }
        #toolbar { 
            height: 40px; background: #252526; display: flex; align-items: center; 
            padding: 0 15px; border-bottom: 1px solid #333; flex-shrink: 0; 
        }
        #container { flex-grow: 1; position: relative; }
        .btn { 
            background: #0e639c; color: white; border: none; padding: 6px 12px; 
            cursor: pointer; font-size: 13px; margin-right: 10px; border-radius: 2px; 
        }
        .btn:hover { background: #1177bb; }
        .status { font-family: 'Segoe UI', sans-serif; font-size: 12px; margin-left: auto; color: #aaa; }
        #loading-msg { 
            position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); 
            font-family: sans-serif; color: #666; font-size: 20px; 
        }
    </style>
</head>
<body>
    <div id="layout">
        <div id="toolbar">
            <button class="btn" onclick="saveFile()">Save (Ctrl+S)</button>
            <button class="btn" style="background: #444;" onclick="generateTags()">Re-Index Symbols</button>
            <span class="status" id="status">Ready</span>
        </div>
        <div id="container">
            <div id="loading-msg">Loading Editor...</div>
        </div>
    </div>

    <!-- 1. Load Monaco from CDN -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs/loader.min.js"></script>
    
    <script>
        // 2. Safe Data Injection from Python
        const fileContent = {{ content | tojson }};
        const project = "{{ project }}";
        const filePath = "{{ path }}";
        const lang = "{{ language }}";

        // 3. Configure Monaco Loader
        require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.44.0/min/vs' }});

        require(['vs/editor/editor.main'], function () {
            // Remove loading message
            document.getElementById('loading-msg').style.display = 'none';

            // Create Editor
            var editor = monaco.editor.create(document.getElementById('container'), {
                value: fileContent,
                language: lang,
                theme: 'vs-dark',
                automaticLayout: true,
                minimap: { enabled: true }
            });

            // Bind Ctrl+S to Save
            editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function() {
                saveFile();
            });

            // Global access for save function
            window.editor = editor;

            // Definition Provider (Ctags)
            monaco.languages.registerDefinitionProvider(lang, {
                provideDefinition: async function(model, position, token) {
                    var word = model.getWordAtPosition(position);
                    if (!word) return;
                    
                    try {
                        const response = await fetch('/editor/api/def', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ project: project, symbol: word.word })
                        });
                        const data = await response.json();
                        
                        if(data.found) {
                            return {
                                uri: monaco.Uri.parse(window.location.origin + "/editor/view/" + project + "/" + data.file),
                                range: new monaco.Range(parseInt(data.line), 1, parseInt(data.line), 1)
                            };
                        }
                    } catch(e) { console.error(e); }
                    return [];
                }
            });
        });

        function saveFile() {
            if(!window.editor) return;
            const btn = document.querySelector('.btn');
            document.getElementById('status').innerText = "Saving...";
            
            fetch('/save_file', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ project: project, path: filePath, content: window.editor.getValue() })
            }).then(r => r.json()).then(d => {
                document.getElementById('status').innerText = d.status === 'ok' ? "Saved Successfully" : "Error Saving";
                setTimeout(() => document.getElementById('status').innerText = "Ready", 2000);
            }).catch(e => {
                document.getElementById('status').innerText = "Network Error";
            });
        }

        function generateTags() {
            document.getElementById('status').innerText = "Indexing...";
            fetch('/editor/api/reindex', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ project: project })
            }).then(r => r.json()).then(d => {
                alert(d.message);
                document.getElementById('status').innerText = "Ready";
            });
        }
    </script>
</body>
</html>
"""

@editor_bp.route('/editor/view/<project>/<path:filepath>')
def open_editor(project, filepath):
    from web_manager import get_config
    root_path, _ = get_config(project)
    
    abs_path = os.path.join(root_path, filepath)
    if not os.path.exists(abs_path): return "File not found", 404
    
    try:
        with open(abs_path, 'r', errors='replace') as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file: {e}", 500
    
    ext = os.path.splitext(filepath)[1]
    lang = 'plaintext'
    if ext in ['.c', '.h', '.cpp']: lang = 'c'
    elif ext in ['.py']: lang = 'python'
    elif ext in ['.sh', '.bb', '.conf']: lang = 'shell'
    elif ext in ['.json', '.dts', '.dtsi']: lang = 'json'
    elif ext in ['.yml', '.yaml']: lang = 'yaml'

    # Using standard Jinja2 render is safer than manual replacement
    return render_template_string(MONACO_HTML, 
                                  project=project, 
                                  path=filepath, 
                                  filename=os.path.basename(filepath), 
                                  content=content, 
                                  language=lang)

@editor_bp.route('/editor/api/reindex', methods=['POST'])
def reindex_ctags():
    from web_manager import get_config
    project = request.json.get('project')
    path, _ = get_config(project)
    # Run Ctags
    cmd = ["ctags", "-R", "--exclude=.git", "--exclude=build", "-f", ".tags", "."]
    try:
        subprocess.Popen(cmd, cwd=path)
        return jsonify({'status': 'ok', 'message': 'Indexing started in background.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@editor_bp.route('/editor/api/def', methods=['POST'])
def find_definition():
    from web_manager import get_config
    project = request.json.get('project')
    symbol = request.json.get('symbol')
    path, _ = get_config(project)
    
    tag_file = os.path.join(path, ".tags")
    if not os.path.exists(tag_file):
        return jsonify({'found': False})
    
    try:
        cmd = ["grep", f"^{symbol}\t", ".tags"]
        out = subprocess.check_output(cmd, cwd=path, text=True).splitlines()
        if out:
            parts = out[0].split('\t')
            if len(parts) >= 2:
                file_rel = parts[1]
                # Try getting line number
                try:
                    l_out = subprocess.check_output(["grep", "-n", symbol, file_rel], cwd=path, text=True).split(':')[0]
                except: l_out = "1"
                return jsonify({'found': True, 'file': file_rel, 'line': l_out})
    except: pass
        
    return jsonify({'found': False})
