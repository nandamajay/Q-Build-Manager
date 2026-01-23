import os
import json
try:
    from qgenie import ChatMessage, QGenieClient
    QGENIE_AVAILABLE = True
except ImportError:
    QGENIE_AVAILABLE = False

def get_client():
    if not QGENIE_AVAILABLE: return None
    return QGenieClient()

def chat_with_history(history_file, system_context, user_question, file_data=None):
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r') as f: history = json.load(f)
        except: pass

    client = get_client()
    if not client:
        return "⚠️ QGenie SDK not installed."

    full_prompt = user_question
    if file_data:
        full_prompt += f"\n\n[ATTACHED FILE: {file_data.get('name')}]\nCONTENT:\n```\n{file_data.get('content')}\n```\n"

    messages = [ChatMessage(role="system", content=system_context)]
    for h in history[-4:]:
        messages.append(ChatMessage(role="user", content=h['user']))
        messages.append(ChatMessage(role="assistant", content=h['bot']))
    messages.append(ChatMessage(role="user", content=full_prompt))

    try:
        resp = client.chat(messages=messages)
        response_text = resp.first_content
    except Exception as e:
        response_text = f"❌ AI Error: {str(e)}"

    history.append({'user': full_prompt, 'bot': response_text})
    if len(history) > 20: history = history[-20:]
    try:
        with open(history_file, 'w') as f: json.dump(history, f)
    except: pass

    return response_text

def generate_code_snippet(language, prompt):
    client = get_client()
    if not client: return f"# Error: QGenie SDK not available for {language}"

    system_prompt = (
        f"You are an expert {language} coding assistant. "
        "Output ONLY raw code. Do not use Markdown blocks (```). "
        "Do not add explanations."
    )
    messages = [ChatMessage(role="system", content=system_prompt), ChatMessage(role="user", content=prompt)]
    try:
        resp = client.chat(messages=messages)
        code = resp.first_content.strip()
        if code.startswith("```"): code = code.split('\n', 1)[1]
        if code.endswith("```"): code = code.rsplit('\n', 1)[0]
        return code
    except Exception as e: return f"# Error: {str(e)}"
