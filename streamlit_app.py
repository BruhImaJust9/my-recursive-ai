import streamlit as st
import urllib.parse
from PIL import Image
import os
import base64
import io
import re
import tempfile
import uuid
import pandas as pd
from groq import Groq
from tavily import TavilyClient
from gtts import gTTS
from audio_recorder_streamlit import audio_recorder

# Optional PDF parsing
try:
    import pypdf
except ImportError:
    pypdf = None

# ==========================================
# 1. PAGE SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="AI Master Studio", page_icon="🤖", layout="wide")
st.title("🤖 Intelligent AI Workspace (Master Studio)")
st.caption("Powered by Groq, Multi-Chat, Real-Time Streaming, Doc Analysis, Vision & Voice")

# Initialize Groq Client
GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))
if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets! Please add it to continue.")
    client = None

# ==========================================
# 2. MULTI-CHAT SESSION STATE MANAGEMENT
# ==========================================
if "chats" not in st.session_state:
    default_id = str(uuid.uuid4())[:8]
    st.session_state.chats = {
        default_id: {
            "title": "New Chat",
            "messages": [
                {"role": "assistant", "content": "Hey there! Ask me anything, upload images or documents, try `/search <topic>`, `/generate <prompt>`, or record voice!"}
            ]
        }
    }
    st.session_state.current_chat_id = default_id

if st.session_state.current_chat_id not in st.session_state.chats:
    st.session_state.current_chat_id = list(st.session_state.chats.keys())[0]

current_chat = st.session_state.chats[st.session_state.current_chat_id]

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
TAVILY_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))

def execute_free_search(query: str) -> str:
    """Bulletproof web search using Tavily API."""
    if not TAVILY_KEY:
        return "⚠️ Missing `TAVILY_API_KEY` in Streamlit secrets!"

    try:
        tavily = TavilyClient(api_key=TAVILY_KEY)
        response = tavily.search(query=query, max_results=5)
        results = response.get("results", [])
        if not results:
            return "No matching search results found."
            
        sources = []
        for r in results:
            title = r.get("title", "No Title")
            content = r.get("content", "No content snippet.")
            url = r.get("url", "#")
            sources.append(f"**{title}**\nSnippet: {content}\nURL: {url}")
            
        return "\n\n".join(sources)
    except Exception as e:
        return f"Search error: {str(e)}"

def optimize_search_query(user_prompt: str) -> str:
    cleaned = user_prompt.lower().replace("search", "").replace("what are", "").strip()
    if "world cup" in cleaned or "super bowl" in cleaned:
        return f'"{cleaned}" final score champions match recap -betting -odds'
    if "movie" in cleaned or "grossing" in cleaned or "box office" in cleaned:
        return f'"{cleaned}" box office worldwide stats'
    elif "standings" in cleaned:
        return f'"{cleaned}" scores standings results'
    return cleaned

def clean_search_query(user_query: str) -> str:
    query = user_query.lower().replace("/search", "").strip().strip("!? ")
    stop_phrases = ["the ", "a ", "an ", "who won ", "what is ", "tell me about "]
    for phrase in stop_phrases:
        if query.startswith(phrase):
            query = query[len(phrase):].strip()
    if " " in query and not ('"' in query or "'" in query):
        return f'"{query}"'
    return query

def get_image_url(prompt: str) -> str:
    encoded_prompt = urllib.parse.quote(prompt.strip())
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=800&nologo=true"

def encode_image_to_base64(image: Image.Image) -> str:
    buffered = io.BytesIO()
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")
    image.thumbnail((1024, 1024))
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def strip_thinking_process(text: str) -> str:
    if not text:
        return ""
    cleaned_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return cleaned_text.strip()

def extract_file_content(uploaded_file) -> str:
    """Reads text from .txt, .py, .md, .csv, and .pdf files."""
    filename = uploaded_file.name.lower()
    try:
        if filename.endswith(('.txt', '.py', '.md', '.json', '.html', '.css', '.js')):
            return uploaded_file.getvalue().decode('utf-8')
        elif filename.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
            return f"CSV Data Snippet (First 20 rows):\n{df.head(20).to_string()}"
        elif filename.endswith('.pdf'):
            if pypdf:
                reader = pypdf.PdfReader(uploaded_file)
                text = ""
                for page in reader.pages[:10]:
                    text += page.extract_text() or ""
                return text if text else "Could not extract text from PDF."
            else:
                return "PDF parser not installed (`pip install pypdf`). Please convert PDF to text."
    except Exception as e:
        return f"Error parsing document: {str(e)}"
    return ""

def transcribe_audio_groq(audio_bytes: bytes, client) -> str:
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
            temp_audio.write(audio_bytes)
            temp_audio_path = temp_audio.name

        with open(temp_audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(temp_audio_path, file.read()),
                model="whisper-large-v3",
                response_format="json",
                language="en",
            )
        os.remove(temp_audio_path)
        return transcription.text
    except Exception as e:
        return f"Speech-to-Text Error: {str(e)}"

def generate_speech_audio(text: str) -> bytes:
    clean_text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    clean_text = re.sub(r'[*_#~`]', '', clean_text).strip()
    if not clean_text:
        clean_text = "Here is your response."
    tts = gTTS(text=clean_text[:500], lang='en')
    fp = io.BytesIO()
    tts.write_to_fp(fp)
    fp.seek(0)
    return fp.read()

def stream_groq_response(stream):
    """Generator function to stream Groq response chunks into st.write_stream."""
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

def render_html_artifact_if_present(text: str):
    """Extracts and renders live HTML code blocks as interactive Artifact previews."""
    html_blocks = re.findall(r'```html\s*(.*?)\s*```', text, re.DOTALL)
    for code in html_blocks:
        st.markdown("### 🎨 Interactive Artifact Preview")
        st.components.v1.html(code, height=350, scrolling=True)

# ==========================================
# 4. SIDEBAR (MULTI-CHAT & FILES)
# ==========================================
image_to_analyze = None
file_context_str = ""

with st.sidebar:
    st.header("🗂️ Chat Sessions")
    
    if st.button("➕ New Chat", use_container_width=True):
        new_id = str(uuid.uuid4())[:8]
        st.session_state.chats[new_id] = {
            "title": "New Chat",
            "messages": [{"role": "assistant", "content": "How can I help you in this new session?"}]
        }
        st.session_state.current_chat_id = new_id
        st.rerun()

    chat_options = {cid: cdata["title"] for cid, cdata in st.session_state.chats.items()}
    selected_id = st.selectbox(
        "Switch Chat",
        options=list(chat_options.keys()),
        format_func=lambda cid: chat_options[cid],
        index=list(chat_options.keys()).index(st.session_state.current_chat_id)
    )
    if selected_id != st.session_state.current_chat_id:
        st.session_state.current_chat_id = selected_id
        st.rerun()

    st.markdown("---")
    st.header("⚙️ Workspace Attachments")
    
    uploaded_image = st.file_uploader(
        "Attach Image (Vision)", 
        type=["png", "jpg", "jpeg", "webp"],
        key="sidebar_img_uploader"
    )
    if uploaded_image:
        image_to_analyze = Image.open(uploaded_image)
        st.image(image_to_analyze, caption="Attached Image", use_container_width=True)

    uploaded_doc = st.file_uploader(
        "Attach Document (PDF, TXT, CSV, Code)",
        type=["txt", "py", "md", "csv", "pdf", "json"],
        key="sidebar_doc_uploader"
    )
    if uploaded_doc:
        file_context_str = extract_file_content(uploaded_doc)
        st.success(f"Loaded: `{uploaded_doc.name}`")

    st.markdown("---")
    if st.button("🗑️ Delete Current Chat", use_container_width=True):
        if len(st.session_state.chats) > 1:
            del st.session_state.chats[st.session_state.current_chat_id]
            st.session_state.current_chat_id = list(st.session_state.chats.keys())[0]
            st.rerun()
        else:
            current_chat["messages"] = []
            st.rerun()

# ==========================================
# 5. CHAT HISTORY DISPLAY
# ==========================================
for msg in current_chat["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "image_url" in msg:
            st.image(msg["image_url"], use_container_width=True)
        elif "uploaded_img" in msg:
            st.image(msg["uploaded_img"], use_container_width=True)
        render_html_artifact_if_present(msg.get("content", ""))

# ==========================================
# 6. INPUT CONTROLS & ROUTING
# ==========================================
col_popover, col_input = st.columns([1, 12])
popover_image = None
audio_bytes = None

with col_popover:
    with st.popover("➕", help="Quick Actions & Voice"):
        st.markdown("### 🎙️ Voice Input")
        audio_bytes = audio_recorder(
            text="Record Voice",
            recording_color="#e84c3d",
            neutral_color="#6aa84f",
            icon_name="microphone",
            icon_size="2x",
            key="voice_recorder"
        )
        st.markdown("---")
        st.caption("🔍 `/search <topic>`")
        st.caption("🎨 `/generate <prompt>`")

with col_input:
    user_input = st.chat_input("Ask a question, analyze documents, /search, or /generate...")

recorded_text = ""
if audio_bytes and client:
    with st.spinner("🎙️ Transcribing voice..."):
        recorded_text = transcribe_audio_groq(audio_bytes, client)

final_input = user_input if user_input else recorded_text
active_image = popover_image if popover_image else image_to_analyze

if final_input and client:
    if len(current_chat["messages"]) <= 1:
        current_chat["title"] = final_input[:20] + "..."

    full_prompt_text = final_input
    if file_context_str:
        full_prompt_text = f"Context from uploaded file:\n
http://googleusercontent.com/immersive_entry_chip/0
