import streamlit as st
import urllib.parse
from PIL import Image
import os
import base64
import io
import re
import tempfile
import random
import json
import urllib.request
import pandas as pd
from groq import Groq
from tavily import TavilyClient
from gtts import gTTS
from audio_recorder_streamlit import audio_recorder

# ==========================================
# 0. PERSISTENCE HELPERS (Defined First!)
# ==========================================
CHAT_STORAGE_FILE = "persistent_chats.json"

def load_saved_chats():
    """Loads chat history from disk if it exists."""
    if os.path.exists(CHAT_STORAGE_FILE):
        try:
            with open(CHAT_STORAGE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"Chat 1": []}

def save_chats_to_disk():
    """Saves active session state chats to disk automatically."""
    try:
        clean_chats = {}
        for session_name, msg_list in st.session_state.chats.items():
            clean_chats[session_name] = []
            for msg in msg_list:
                clean_msg = {k: v for k, v in msg.items() if k != "audio"}
                clean_chats[session_name].append(clean_msg)
                
        with open(CHAT_STORAGE_FILE, "w") as f:
            json.dump(clean_chats, f, indent=2)
    except Exception:
        pass

# ==========================================
# 1. PAGE SETUP & STYLING FIXES
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")

# 🎨 CSS FIX: Removes the white rectangle/outline around the audio recorder iframe
st.markdown(
    """
    <style>
        iframe[title="audio_recorder_streamlit.audio_recorder"] {
            border: none !important;
            outline: none !important;
            box-shadow: none !important;
        }
        div[data-testid="stHorizontalBlock"] > div {
            border: none !important;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🤖 Intelligent AI Workspace")
st.caption("Powered by Groq Llama 3, Free Web Search, Image Generation & Voice")

# Initialize Groq Client
GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets! Please add it to continue.")
    client = None

# Initialize Sessions
if "chats" not in st.session_state:
    st.session_state.chats = load_saved_chats()

if "current_chat" not in st.session_state:
    st.session_state.current_chat = list(st.session_state.chats.keys())[0]

if "memory_vault" not in st.session_state:
    st.session_state.memory_vault = []

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def transcribe_audio_groq(audio_bytes, groq_client):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name
        
        with open(tmp_path, "rb") as file_to_transcribe:
            transcription = groq_client.audio.transcriptions.create(
                file=(tmp_path, file_to_transcribe.read()),
                model="whisper-large-v3",
                response_format="text"
            )
        os.remove(tmp_path)
        return str(transcription).strip()
    except Exception as e:
        return f"Speech-to-Text Error: {e}"

def generate_speech_audio(text_content):
    try:
        clean_text = re.sub(r'[*_#`~]', '', text_content)
        clean_text = clean_text[:300]
        if not clean_text.strip():
            return None
        tts = gTTS(text=clean_text, lang='en', slow=False)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp.read()
    except Exception:
        return None

def generate_chat_title(first_prompt, groq_client):
    try:
        res = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": "Generate a concise 2-4 word title for this prompt. Return ONLY the title."},
                      {"role": "user", "content": first_prompt}],
            temperature=0.3
        )
        return res.choices[0].message.content.strip().replace('"', '')
    except Exception:
        return "New Session"

def execute_free_search(query):
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
        results = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        clean_results = [re.sub(r'<[^>]+>', '', r).strip() for r in results[:4]]
        return "\n---\n".join(clean_results) if clean_results else "No live web results found."
    except Exception as e:
        return f"Search Failed: {e}"

def run_deep_research_agent(topic, groq_client, model_name):
    step1 = execute_free_search(f"{topic} overview breakdown")
    step2 = execute_free_search(f"{topic} latest updates facts")
    combined_data = f"--- OVERVIEW ---\n{step1}\n\n--- UPDATES ---\n{step2}"
    
    prompt = f"Topic: {topic}\nContext:\n{combined_data}\n\nSynthesize a structured research brief."
    try:
        res = groq_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "system", "content": prompt}],
            temperature=0.2
        )
        return res.choices[0].message.content
    except Exception as e:
        return f"Research failed: {e}"

def get_image_url(prompt):
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=512&height=512&nologo=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as resp:
            return resp.read()
    except Exception:
        return None

def classify_user_intent(prompt_text, groq_client, model_name):
    try:
        res = groq_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "Classify intent as CHAT, SEARCH, GENERATE, or RESEARCH. Return ONLY the category."},
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.0
        )
        intent = res.choices[0].message.content.strip().upper()
        return intent if intent in ["CHAT", "SEARCH", "GENERATE", "RESEARCH"] else "CHAT"
    except Exception:
        return "CHAT"

def build_dynamic_system_prompt(user_text, personality, target_lang, style):
    prompt = f"You are a helpful AI assistant. Personality mode: {personality}. Respond in language: {target_lang}."
    if style == "TECHNICAL":
        prompt += " Focus on structured code snippets, technical accuracy, and concise explanations."
    elif style == "ANALYTICAL":
        prompt += " Focus on logical breakdowns, organized data, and bullet points."
    return prompt

# ==========================================
# 3. SIDEBAR CONTROLS & UPGRADE #22
# ==========================================
with st.sidebar:
    st.header("⚙️ Workspace Controls")
    
    selected_model = st.selectbox(
        "Select AI Model:",
        ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]
    )
    
    personality = st.selectbox("AI Personality:", ["Helpful", "Witty", "Professional", "Sarcastic"])
    target_language = st.selectbox("Language:", ["English", "Spanish", "French", "German"])
    
    st.markdown("---")
    st.subheader("💬 Chat Sessions")
    
    # Session Selector
    chat_names = list(st.session_state.chats.keys())
    current_index = chat_names.index(st.session_state.current_chat) if st.session_state.current_chat in chat_names else 0
    selected_chat = st.selectbox("Switch Chat:", chat_names, index=current_index)
    st.session_state.current_chat = selected_chat
    
    if st.button("➕ New Chat"):
        new_name = f"Chat {len(st.session_state.chats) + 1}"
        st.session_state.chats[new_name] = []
        st.session_state.current_chat = new_name
        save_chats_to_disk()
        st.rerun()

    # 🚀 UPGRADE #22: Chat Exporter & Manager
    st.markdown("---")
    st.subheader("📤 Export & Manage Chat")
    
    active_chat = st.session_state.chats[st.session_state.current_chat]
    
    if active_chat:
        # Convert Chat to Markdown String for Export
        md_export = f"# Chat Transcript: {st.session_state.current_chat}\n\n"
        for msg in active_chat:
            role = "👤 **User**" if msg["role"] == "user" else "🤖 **Assistant**"
            md_export += f"{role}:\n{msg['content']}\n\n---\n\n"
        
        st.download_button(
            label="📥 Export Chat (.md)",
            data=md_export,
            file_name=f"{st.session_state.current_chat.lower().replace(' ', '_')}_export.md",
            mime="text/markdown"
        )
    
    col_clear, col_del = st.columns(2)
    with col_clear:
        if st.button("🧹 Clear"):
            st.session_state.chats[st.session_state.current_chat] = []
            save_chats_to_disk()
            st.rerun()
            
    with col_del:
        if len(st.session_state.chats) > 1:
            if st.button("🗑️ Delete"):
                del st.session_state.chats[st.session_state.current_chat]
                st.session_state.current_chat = list(st.session_state.chats.keys())[0]
                save_chats_to_disk()
                st.rerun()

    st.markdown("---")
    use_custom_override = st.checkbox("Enable Custom System Prompt")
    custom_system_override = st.text_area("Custom System Prompt:") if use_custom_override else ""

    uploaded_doc = st.file_uploader("Upload File (TXT/CSV)", type=["txt", "csv"])
    doc_context = ""
    if uploaded_doc:
        if uploaded_doc.name.endswith(".csv"):
            df = pd.read_csv(uploaded_doc)
            doc_context = df.to_string()
        else:
            doc_context = uploaded_doc.read().decode("utf-8")

    uploaded_img = st.file_uploader("Upload Image for Analysis", type=["png", "jpg", "jpeg"])
    image_to_analyze = Image.open(uploaded_img) if uploaded_img else None

# ==========================================
# 4. CHAT HISTORY DISPLAY
# ==========================================
active_chat_list = st.session_state.chats[st.session_state.current_chat]

for msg in active_chat_list:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "image_url" in msg:
            st.image(msg["image_url"])
        if "audio" in msg and msg["audio"]:
            st.audio(msg["audio"], format="audio/mp3")

# ==========================================
# 5. INPUT LOGIC & ROUTING
# ==========================================
user_input = st.chat_input("Ask anything, use /search, /generate, or /research...")
audio_bytes = audio_recorder(text="🎤 Record Voice", recording_color="#e84c3d", neutral_color="#6aa84f")

final_input = user_input
transcribed = ""

if audio_bytes and client:
    transcribed = transcribe_audio_groq(audio_bytes, client)
    if transcribed and not transcribed.startswith("Speech-to-Text Error"):
        last_ai_msg = next((m["content"] for m in reversed(active_chat_list) if m["role"] == "assistant"), "")
        if last_ai_msg and (transcribed in last_ai_msg or last_ai_msg in transcribed):
            st.warning("⚠️ Audio feedback loop detected and muted!")
            st.stop()
        else:
            final_input = transcribed

if final_input and client:
    # Auto Title Generator
    if len(active_chat_list) == 0:
        new_title = generate_chat_title(final_input, client)
        if new_title != "New Session":
            st.session_state.chats[new_title] = st.session_state.chats.pop(st.session_state.current_chat)
            st.session_state.current_chat = new_title
            active_chat_list = st.session_state.chats[st.session_state.current_chat]

    active_chat_list.append({"role": "user", "content": final_input})
    save_chats_to_disk()
    with st.chat_message("user"):
        st.markdown(final_input)

    detected_intent = "CHAT"
    if final_input.lower().startswith("/generate"):
        detected_intent = "GENERATE"
    elif final_input.lower().startswith("/search"):
        detected_intent = "SEARCH"
    elif final_input.lower().startswith("/research"):
        detected_intent = "RESEARCH"
    elif not image_to_analyze:
        detected_intent = classify_user_intent(final_input, client, selected_model)

    detected_style = "TECHNICAL" if any(kw in final_input.lower() for kw in ["code", "python", "error", "streamlit"]) else "GENERAL"

    # ROUTE 1: Image Generation
    if detected_intent == "GENERATE":
        clean_prompt = final_input.replace("/generate", "").strip()
        with st.spinner("🎨 Rendering image..."):
            img_bytes = get_image_url(clean_prompt)
            if img_bytes:
                img_b64 = f"data:image/png;base64,{base64.b64encode(img_bytes).decode('utf-8')}"
                active_chat_list.append({
                    "role": "assistant",
                    "content": f"Here is your generated image for: **'{clean_prompt}'**",
                    "image_url": img_b64
                })
            else:
                active_chat_list.append({"role": "assistant", "content": "⚠️ Image generation failed."})

    # ROUTE 2: Search
    elif detected_intent == "SEARCH":
        clean_query = final_input.replace("/search", "").strip()
        with st.spinner("🔍 Querying live web search..."):
            search_data = execute_free_search(clean_query)
            search_prompt = (
                f"User requested real-time search information for: '{clean_query}'.\n"
                f"Live Search Data:\n{search_data}\n\n"
                f"Synthesize this factual context clearly using structured headings and concise takeaways."
            )
            response = client.chat.completions.create(
                model=selected_model,
                messages=[{"role": "system", "content": search_prompt}],
                temperature=0.2
            )
            reply = response.choices[0].message.content
            active_chat_list.append({"role": "assistant", "content": reply})

    # ROUTE 3: Research
    elif detected_intent == "RESEARCH":
        clean_topic = final_input.replace("/research", "").strip()
        with st.spinner("🕵️ Agent performing deep research..."):
            brief = run_deep_research_agent(clean_topic, client, selected_model)
            active_chat_list.append({"role": "assistant", "content": brief})

    # ROUTE 4: Standard Chat
    else:
        system_prompt = custom_system_override.strip() if (use_custom_override and custom_system_override.strip()) else build_dynamic_system_prompt(final_input, personality, target_language, detected_style)
        if doc_context:
            system_prompt += f"\n\n[FILE CONTEXT]:\n{doc_context[:4000]}"

        messages_payload = [{"role": "system", "content": system_prompt}]
        for m in active_chat_list:
            if isinstance(m.get("content"), str):
                messages_payload.append({"role": m["role"], "content": m["content"]})

        with st.spinner("Thinking..."):
            response = client.chat.completions.create(
                model=selected_model,
                messages=messages_payload,
                temperature=0.7
            )
            assistant_reply = response.choices[0].message.content
            audio_data = generate_speech_audio(assistant_reply)
            active_chat_list.append({"role": "assistant", "content": assistant_reply, "audio": audio_data})

    save_chats_to_disk()
    st.rerun()
