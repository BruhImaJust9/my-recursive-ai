import streamlit as st
import urllib.parse
from PIL import Image
import os
import base64
import io
import re
import tempfile
import random
import pandas as pd
from groq import Groq
from tavily import TavilyClient
from gtts import gTTS
from audio_recorder_streamlit import audio_recorder

# ==========================================
# 1. PAGE SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")
st.title("🤖 Intelligent AI Workspace")
st.caption("Powered by Groq Llama 3, Free Web Search, Image Generation & Voice")

# Initialize Groq Client
GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets! Please add it to continue.")
    client = None

# Initialize Multi-Chat Sessions
if "chats" not in st.session_state:
    st.session_state.chats = {"Chat 1": []}

if "current_chat" not in st.session_state:
    st.session_state.current_chat = "Chat 1"

# Helper reference for current active message list
current_messages = st.session_state.chats[st.session_state.current_chat]

# Initialize Persistent Memory Vault
if "memory_vault" not in st.session_state:
    st.session_state.memory_vault = []

if not current_messages:
    current_messages.append({
        "role": "assistant", 
        "content": "Hey there! I'm powered by Groq. Ask me anything, upload an image to analyze, try `/search <topic>`, `/generate <prompt>`, or speak using the ➕ menu!"
    })

# ==========================================
# 2. HELPER FUNCTIONS
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

def run_deep_research_agent(topic: str, client, selected_model) -> str:
    """Autonomous agent that plans sub-queries, executes multiple searches, and compiles a research brief."""
    if not TAVILY_KEY:
        return "⚠️ Missing `TAVILY_API_KEY` for Deep Research!"

    tavily = TavilyClient(api_key=TAVILY_KEY)
    
    # 1. Ask the AI to generate 3 targeted sub-queries
    plan_prompt = f"Break down this research topic into 3 distinct, specific search queries to get comprehensive coverage: '{topic}'. Output ONLY 3 queries, one per line."
    try:
        plan_res = client.chat.completions.create(
            model=selected_model,
            messages=[{"role": "user", "content": plan_prompt}]
        )
        sub_queries = [q.strip(" 123456789.-*") for q in plan_res.choices[0].message.content.strip().split("\n") if q.strip()][:3]
    except Exception:
        sub_queries = [topic]

    # 2. Gather search results across all queries
    compiled_findings = []
    for q in sub_queries:
        try:
            res = tavily.search(query=q, max_results=3)
            for r in res.get("results", []):
                compiled_findings.append(f"Source [{r.get('title', 'Link')}]: {r.get('content', '')}")
        except Exception:
            continue

    if not compiled_findings:
        return "No deep research findings could be retrieved."

    # 3. Synthesize findings into an Executive Brief
    synthesis_prompt = (
        f"You are a Lead Intelligence Analyst. Based on the following information, produce a research brief on: '{topic}'.\n\n"
        f"DYNAMIC TONE & FORMAT RULE:\n"
        f"- If the topic is professional/academic (e.g., business, technology, finance), use headers like: 📌 Executive Summary, 🔍 Key Findings, and 💡 Tactical Implications.\n"
        f"- If the topic is casual, sports-related, or pop culture (e.g., sports debates, movies, entertainment), drop corporate jargon! Use headers like: 🏆 Top Contenders, 📊 Key Trends, and 💡 The Verdict.\n\n"
        f"NATURAL SYNTHESIS PROTOCOL:\n"
        f"State facts authoritatively. Never mention 'search results', 'raw data', or 'snippets'.\n\n"
        f"Information:\n" + "\n\n".join(compiled_findings[:8])
    )

    summary_res = client.chat.completions.create(
        model=selected_model,
        messages=[{"role": "user", "content": synthesis_prompt}]
    )
    return summary_res.choices[0].message.content

def render_data_canvas(response_text: str):
    """Detects Markdown tables or CSV structures and renders interactive charts."""
    lines = [line.strip() for line in response_text.split("\n") if "|" in line]
    
    # Must have at least 3 markdown table lines (header, separator, data)
    if len(lines) >= 3:
        try:
            # Clean markdown table borders
            cleaned_lines = [re.sub(r'^\||\|$', '', line) for line in lines if not re.match(r'^[|\s:-]+$', line)]
            
            # Parse into a list of rows
            data = [[cell.strip() for cell in line.split("|")] for line in cleaned_lines]
            
            if len(data) > 1:
                df = pd.DataFrame(data[1:], columns=data[0])
                
                # Attempt to convert numerical columns
                for col in df.columns:
                    df[col] = pd.to_numeric(df[col].str.replace(',', ''), errors='ignore')
                
                num_cols = df.select_dtypes(include=['number']).columns.tolist()
                
                if num_cols:
                    st.markdown("#### 📊 Dynamic Visual Canvas")
                    st.dataframe(df, use_container_width=True)
                    
                    # Render chart based on numerical data
                    chart_type = st.radio("Chart Type:", ["Bar", "Line"], horizontal=True, key=f"chart_type_{hash(response_text)}")
                    if chart_type == "Bar":
                        st.bar_chart(df.set_index(df.columns[0])[num_cols])
                    else:
                        st.line_chart(df.set_index(df.columns[0])[num_cols])
        except Exception:
            pass  # Fall back gracefully if parsing fails

def audit_response(original_prompt: str, ai_response: str, client, model_name: str) -> str:
    """Uses a secondary model pass to evaluate the accuracy and logic of a response."""
    audit_system_prompt = (
        "You are an impartial AI auditor. Review the user's prompt and the assistant's response. "
        "Provide: 1) A Confidence Rating (e.g., 95/100), 2) A brief check for accuracy/logic, "
        "and 3) Any necessary corrections or missing nuances."
    )
    
    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": audit_system_prompt},
                {"role": "user", "content": f"User Prompt: {original_prompt}\n\nAssistant Response:\n{ai_response}"}
            ]
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Audit Error: {str(e)}"

def safe_execute(func, default_return=None, error_msg="Feature temporarily unavailable."):
    """Executes any app feature safely, catching errors without breaking the UI."""
    try:
        return func()
    except Exception as e:
        st.warning(f"⚠️ {error_msg}")
        with st.expander("🔧 View Error Diagnostic"):
            st.code(str(e), language="python")
        return default_return

def classify_user_intent(user_prompt: str, client, selected_model: str) -> str:
    """Uses a fast model pass to automatically route prompts to the correct feature."""
    classification_system_prompt = (
        "You are an intent classifier for an AI workspace. "
        "Analyze the user's input and respond with EXACTLY ONE word from this list:\n"
        "- GENERATE (if they want to create/draw an image)\n"
        "- SEARCH (if they ask for real-time news, sports, weather, or recent facts)\n"
        "- RESEARCH (if they ask for an in-depth report, deep dive, or multi-source investigation)\n"
        "- CHAT (for standard questions, coding, conversation, or math)\n\n"
        "Output ONLY the single classification keyword."
    )
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",  # Ultra-fast model for low-latency classification
            messages=[
                {"role": "system", "content": classification_system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=5,
            temperature=0.0
        )
        intent = completion.choices[0].message.content.strip().upper()
        return intent if intent in ["GENERATE", "SEARCH", "RESEARCH"] else "CHAT"
    except Exception:
        return "CHAT"

import json

def generate_markdown_export(chat_list, memory_vault) -> str:
    """Formats active chat messages and memory vault into a clean Markdown document."""
    md = "# 🚀 AI Workspace Export\n"
    md += f"**Export Date:** {st.session_state.get('current_time', 'Active Session')}\n\n"
    
    if memory_vault:
        md += "## 🧠 Memory Vault Facts\n"
        for mem in memory_vault:
            md += f"- {mem}\n"
        md += "\n---\n"

    md += "## 💬 Chat Transcript\n\n"
    for idx, msg in enumerate(chat_list, 1):
        role = msg.get("role", "user").capitalize()
        content = msg.get("content", "")
        md += f"### {role}\n{content}\n\n"
        if "image_url" in msg:
            md += "*[Image Generated]*\n\n"
    
    return md

def optimize_search_query(user_prompt: str, category: str = "general") -> str:
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

def generate_action_cards(response_text: str):
    """Detects content type and creates interactive suggestion chips."""
    suggestions = []
    
    # Detect Code
    if "```" in response_text:
        suggestions.append("🔍 Explain this code step-by-step")
        suggestions.append("⚡ Optimize this code for speed")
    
    # Detect Math or Science
    elif any(term in response_text.lower() for term in ["equation", "formula", "calculate", "math"]):
        suggestions.append("🧮 Show alternative solution method")
        suggestions.append("📝 Give me a practice problem on this")
        
    # Standard Chat / General Topics
    else:
        suggestions.append("💡 Give me 3 real-world examples")
        suggestions.append("📌 Summarize this in 2 bullet points")
        suggestions.append("❓ What are the main criticisms of this?")
        
    return suggestions

import urllib.request

def get_image_url(prompt: str):
    """Fetches image bytes directly from Pollinations AI with proper browser headers."""
    try:
        encoded_prompt = urllib.parse.quote(prompt.strip())
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=800&nologo=true"
        
        # Pass a realistic User-Agent so the request isn't blocked
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read()
    except Exception as e:
        return None

def encode_image_to_base64(image: Image.Image) -> str:
    buffered = io.BytesIO()
    
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")
        
    image.thumbnail((1024, 1024))
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def convert_chat_to_text(messages) -> str:
    chat_log = []
    for msg in messages:
        role = msg.get("role", "unknown").capitalize()
        content = msg.get("content", "")
        chat_log.append(f"{role}:\n{content}\n" + "-" * 40)
    return "\n\n".join(chat_log)

def strip_thinking_process(text: str) -> str:
    if not text:
        return ""
    cleaned_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return cleaned_text.strip()

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

def extract_file_content(uploaded_file) -> str:
    if uploaded_file is None:
        return ""
    
    file_name = uploaded_file.name.lower()
    
    if file_name.endswith((".txt", ".py", ".csv", ".md", ".json", ".html", ".css")):
        try:
            return uploaded_file.read().decode("utf-8", errors="ignore")
        except Exception as e:
            return f"[Error reading text file: {str(e)}]"
            
    elif file_name.endswith(".pdf"):
        try:
            import pypdf
            reader = pypdf.PdfReader(uploaded_file)
            text = ""
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            return text
        except ImportError:
            return "[Error: `pypdf` library is not installed. Install it via `pip install pypdf` to analyze PDFs!]"
        except Exception as e:
            return f"[Error reading PDF: {str(e)}]"
            
    return ""

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

def generate_stream_response(stream):
    """Extracts plain text chunks from Groq's streaming response."""
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

# ==========================================
# 3. SIDEBAR & CONTROLS
# ==========================================
image_to_analyze = None

with st.sidebar:
    st.header("⚙️ Workspace Controls")
    st.markdown("---")
    
    # 🗂️ Multi-Chat Session Controls
    st.header("💬 Chat Sessions")
    chat_names = list(st.session_state.chats.keys())
    selected_chat = st.selectbox("Select Thread:", chat_names, index=chat_names.index(st.session_state.current_chat))
    
    if selected_chat != st.session_state.current_chat:
        st.session_state.current_chat = selected_chat
        st.rerun()

 # 📦 Feature #15: Workspace Export Engine
    st.markdown("---")
    with st.expander("📦 Session Export & Backup"):
        st.caption("Download your active chat history and memory vault facts.")
        
        active_chats = st.session_state.chats.get(st.session_state.current_chat, [])
        
        if active_chats:
            # Generate Markdown file data
            md_data = generate_markdown_export(active_chats, st.session_state.memory_vault)
            st.download_button(
                label="📄 Export as Markdown (.md)",
                data=md_data,
                file_name="workspace_chat_export.md",
                mime="text/markdown",
                use_container_width=True
            )
            
            # 🧹 Sanitize chat history for JSON export (stripping non-serializable binary data)
            sanitized_chats = []
            for msg in active_chats:
                clean_msg = {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                }
                # Keep text/URL fields, skip raw audio/image BytesIO objects
                if "image_url" in msg and isinstance(msg["image_url"], str):
                    clean_msg["image_url"] = msg["image_url"]
                sanitized_chats.append(clean_msg)

            # Generate JSON backup data safely
            json_data = json.dumps({
                "memory_vault": st.session_state.memory_vault,
                "chat_history": sanitized_chats
            }, indent=2)
            
            st.download_button(
                label="💾 Export Raw Backup (.json)",
                data=json_data,
                file_name="workspace_backup.json",
                mime="application/json",
                use_container_width=True
            )
        else:
            st.info("Start a chat session to enable export options.")
            
    # 🧠 Feature #12: Global Memory Vault
    st.markdown("---")
    with st.expander("🧠 Persistent Memory Vault"):
        st.caption("Information saved here is remembered across ALL chat sessions!")
        
        new_memory = st.text_input("Add a fact about yourself/project:", key="new_memory_input")
        if st.button("💾 Save Memory", use_container_width=True):
            if new_memory.strip():
                st.session_state.memory_vault.append(new_memory.strip())
                st.success("Saved to memory!")
                st.rerun()

        if st.session_state.memory_vault:
            st.markdown("**Current Memories:**")
            for m_idx, mem in enumerate(st.session_state.memory_vault):
                st.write(f"• {mem}")
            
            if st.button("🗑️ Clear All Memories", use_container_width=True):
                st.session_state.memory_vault = []
                st.rerun()

    if st.button("➕ New Chat Session", use_container_width=True):
        new_chat_name = f"Chat {len(st.session_state.chats) + 1}"
        st.session_state.chats[new_chat_name] = []
        st.session_state.current_chat = new_chat_name
        st.rerun()

    auto_play_voice = st.sidebar.toggle("🔊 Auto-Play Voice Answers", value=False)

    st.markdown("---")
    st.header("🎭 AI Personality")
    personality = st.selectbox(
        "Choose AI Persona:",
        ["Helpful Assistant", "Code Expert", "Sarcastic Buddy", "Strict Tutor"]
    )

    # 🧠 Model Selection
    st.markdown("---")
    st.header("🧠 Choose AI Model")
    selected_model = st.selectbox(
        "Select Model:",
        [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768"
        ]
    )

    # Document File Uploader
    st.markdown("---")
    st.header("📄 Document Analysis")
    uploaded_doc = st.file_uploader(
        "Upload Doc or Code (TXT, PDF, CSV, PY, JSON)",
        type=["txt", "pdf", "csv", "md", "json", "py"],
        key="doc_uploader"
    )
    
    doc_context = ""
    if uploaded_doc:
        doc_context = extract_file_content(uploaded_doc)
        st.success(f"📄 Attached `{uploaded_doc.name}` ({len(doc_context)} characters loaded)")
    
    st.markdown("---")
    st.header("📷 Vision Image")
    uploaded_file = st.file_uploader(
        "Upload Image to Analyze", 
        type=["png", "jpg", "jpeg", "webp"],
        key="sidebar_file_uploader"
    )
    if uploaded_file:
        image_to_analyze = Image.open(uploaded_file)
        st.image(image_to_analyze, caption="Sidebar Attached Image", use_container_width=True)
        st.success("Image attached! Ask a question in chat about it.")

    # Quality of Life Shortcuts
    st.markdown("---")
    st.header("⚡ Shortcuts")
    
    sample_prompts = [
        "/search What are the top trending space discoveries this week?",
        "Explain quantum computing using an analogy about pizza.",
        "/generate A futuristic neon city covered in bioluminescent plants",
        "Give me a 5-minute productivity hack for studying."
    ]
    if st.button("🎲 Surprise Me!", use_container_width=True):
        random_prompt = random.choice(sample_prompts)
        st.session_state.chats[st.session_state.current_chat].append({"role": "user", "content": random_prompt})
        st.rerun()

    # Export & Management Controls
    st.markdown("---")
    if current_messages:
        chat_text = convert_chat_to_text(current_messages)
        st.download_button(
            label="📥 Export Chat History",
            data=chat_text,
            file_name=f"{st.session_state.current_chat}_history.txt",
            mime="text/plain",
            use_container_width=True
        )

    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state.chats[st.session_state.current_chat] = []
        st.rerun()

    # Analytics Dashboard Badge
    with st.expander("📊 Session Analytics"):
        active_list = st.session_state.chats[st.session_state.current_chat]
        msg_count = len(active_list)
        char_count = sum(len(m.get("content", "")) for m in active_list if isinstance(m.get("content"), str))
        st.write(f"**Total Messages:** {msg_count}")
        st.write(f"**Total Characters:** {char_count:,}")

# ==========================================
# 4. CHAT HISTORY DISPLAY
# ==========================================
for idx, msg in enumerate(st.session_state.chats[st.session_state.current_chat]):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        
        # 📊 Feature #9: Dynamic Data Visual Canvas
        if msg["role"] == "assistant" and "content" in msg and msg["content"]:
            render_data_canvas(msg["content"])

        if "image_url" in msg:
            st.image(msg["image_url"], use_container_width=True)
        elif "uploaded_img" in msg:
            st.image(msg["uploaded_img"], use_container_width=True)
        
    if "audio" in msg and msg["audio"]:
        # Auto-plays the latest assistant response if Hands-Free Mode is ON!
        is_latest_msg = (idx == len(st.session_state.chats[st.session_state.current_chat]) - 1)
        st.audio(msg["audio"], format="audio/mp3", autoplay=(auto_play_voice and is_latest_msg))

        # 🛡️ Feature #8: Fact-Check / Audit Button for Assistant Responses
        if msg["role"] == "assistant" and "content" in msg and msg["content"]:
            with st.expander("🛡️ Verify & Audit Response"):
                if st.button("Run Self-Critique", key=f"audit_btn_{idx}"):
                    previous_prompt = "General query"
                    if idx > 0 and st.session_state.chats[st.session_state.current_chat][idx-1]["role"] == "user":
                        previous_prompt = st.session_state.chats[st.session_state.current_chat][idx-1]["content"]
                    
                    with st.spinner("Analyzing response accuracy..."):
                        audit_result = audit_response(previous_prompt, msg["content"], client, selected_model)
                        st.info(audit_result)

# ==========================================
# 5. INPUT LOGIC & ROUTING
# ==========================================

# 1. Popover container for attachments & voice
col_popover, _ = st.columns([1, 12])

popover_image = None
audio_bytes = None

with col_popover:
    with st.popover("➕", help="Quick Actions, Attachments & Voice"):
        st.markdown("### 🎙️ Voice Input")
        audio_bytes = audio_recorder(
            text="Click to Record Voice",
            recording_color="#e84c3d",
            neutral_color="#6aa84f",
            icon_name="microphone",
            icon_size="2x",
            key="voice_recorder"
        )
        
        st.markdown("---")
        st.markdown("### 📷 Attach Image")
        popover_file = st.file_uploader(
            "Upload Vision Image", 
            type=["png", "jpg", "jpeg", "webp"],
            key="popover_file_uploader"
        )
        if popover_file:
            popover_image = Image.open(popover_file)
            st.image(popover_image, caption="Popover Attached Image", use_container_width=True)
            st.success("Image attached! Type a prompt below.")

        st.markdown("---")
        st.caption("🔍 **Live Search:** `/search <topic>`")
        st.caption("🎨 **Generate Image:** `/generate <prompt>`")

# 2. Main Chat Input (Outside columns, pinned to bottom!)
user_input = st.chat_input("Type a question, ask about an image, /search, or /generate...")

recorded_text = ""
if audio_bytes and client:
    with st.spinner("🎙️ Transcribing voice..."):
        recorded_text = transcribe_audio_groq(audio_bytes, client)

recorded_text = ""
if audio_bytes and client:
    with st.spinner("🎙️ Transcribing voice..."):
        recorded_text = transcribe_audio_groq(audio_bytes, client)

active_chat_list = st.session_state.chats[st.session_state.current_chat]

# 1. Determine prompt from text input, audio, OR recent button click
final_input = None
if user_input:
    final_input = user_input
elif recorded_text:
    final_input = recorded_text
elif active_chat_list and active_chat_list[-1]["role"] == "user":
    # Triggered by "Surprise Me!" or Quick Action buttons!
    final_input = active_chat_list[-1]["content"]

active_image = popover_image if popover_image else image_to_analyze

# 2. Only append user message if typed/spoken (buttons already appended it)
if final_input and client:
    active_chat_list = st.session_state.chats[st.session_state.current_chat]
    
    if user_input or recorded_text:
        user_data = {"role": "user", "content": final_input}
        if active_image:
            user_data["uploaded_img"] = active_image
        active_chat_list.append(user_data)

    # 🔀 Feature #13: Detect intent automatically if no explicit slash command is used
    detected_intent = "CHAT"
    if final_input.lower().startswith("/generate"):
        detected_intent = "GENERATE"
    elif final_input.lower().startswith("/search"):
        detected_intent = "SEARCH"
    elif final_input.lower().startswith("/research"):
        detected_intent = "RESEARCH"
    elif not active_image:
        # Run auto-classifier for plain text/voice inputs!
        detected_intent = classify_user_intent(final_input, client, selected_model)
    
   # 🎨 ROUTE 1: Safe Image Generation
    if detected_intent == "GENERATE":
        prompt = re.sub(r'^/generate', '', final_input, flags=re.IGNORECASE).strip()
        with st.spinner("🎨 Creating your image safely..."):
            img_bytes = safe_execute(
                lambda: get_image_url(prompt), 
                default_return=None, 
                error_msg="Could not generate image right now. Please try again!"
            )
            
        if img_bytes:
            active_chat_list.append({
                "role": "assistant", 
                "content": f"Here is your generated image for: **'{prompt}'**",
                "image_url": img_bytes
            })

  # 🔍 ROUTE 2: Enhanced Web Search
    elif detected_intent == "SEARCH":
        cleaned_query = clean_search_query(final_input)
        optimized_query = optimize_search_query(cleaned_query)
        
        search_text = safe_execute(
            lambda: execute_free_search(optimized_query),
            default_return="No search results could be retrieved at this time.",
            error_msg="Web Search encountered a connection issue."
        )
        
        # 🎨 Enhanced System Prompt for Beautiful Formatting
        search_system_prompt = (
            "You are a world-class news editor and research assistant.\n"
            "Analyze the search results and format your response with high visual appeal:\n"
            "1. Use clear, bold Headings (###) to separate distinct sections.\n"
            "2. Use bullet points and relevant emojis (e.g., 🏆, 🏎️, ⚽, 📊, 🏁) to make data scan-friendly.\n"
            "3. If scores or match results are present, create a clean mini-table or formatted summary block at the top.\n"
            "4. If there are conflicting search results, explicitly note the discrepancy.\n"
            "5. End with 1-2 thoughtful, optional follow-up research questions or next steps.\n\n"
            "INTENT DISAMBIGUATION RULE:\n"
            "If the prompt uses ambiguous terms (e.g., 'football' meaning Soccer vs. NFL), analyze search snippets to determine the dominant context. "
            "If both contexts appear, split your response into clear sections (e.g., '### Soccer' and '### American Football (NFL)').\n\n"
            "NATURAL SYNTHESIS PROTOCOL (NO FOURTH-WALL BREAKS):\n"
            "Never mention 'search results', 'Tavily', 'raw data', 'snippets', or 'the web search'. "
            "State facts directly and authoritatively as if you naturally know the information."
        )

        stream = client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": search_system_prompt},
                {"role": "user", "content": f"Query: '{optimized_query}'\n\nSearch Results:\n{search_text}"}
            ],
            stream=True
        )

        with st.chat_message("assistant"):
            clean_response = st.write_stream(generate_stream_response(stream))
        
        clean_response = strip_thinking_process(clean_response)
        
        # Audio rendering safe wrap
        audio_data = safe_execute(lambda: generate_speech_audio(clean_response), default_return=None)
        active_chat_list.append({"role": "assistant", "content": clean_response, "audio": audio_data})

    # 🕵️ ROUTE 3: Safe Deep Research Agent
    elif detected_intent == "RESEARCH":
        topic = re.sub(r'^/research', '', final_input, flags=re.IGNORECASE).strip()
        
        with st.spinner(f"🕵️ Autonomous Agent analyzing '{topic}'..."):
            # Wrapped research agent call inside safe_execute
            brief = safe_execute(
                lambda: run_deep_research_agent(topic, client, selected_model),
                default_return="Research agent could not complete the multi-source analysis.",
                error_msg="Deep Research Agent ran into an API timeout."
            )
        
        # Audio rendering safe wrap
        audio_data = safe_execute(lambda: generate_speech_audio(brief), default_return=None)
        active_chat_list.append({"role": "assistant", "content": brief, "audio": audio_data})

    # 🕵️ ROUTE 5: Deep Research Agent
    elif final_input.lower().startswith("/research"):
        topic = final_input.replace("/research", "").strip()
        with st.spinner(f"🕵️ Autonomous Agent analyzing '{topic}' across multiple sources..."):
            brief = run_deep_research_agent(topic, client, selected_model)
        
        audio_data = generate_speech_audio(brief)
        active_chat_list.append({
            "role": "assistant",
            "content": brief,
            "audio": audio_data
        })

# ⚡ ROUTE 4: Standard Chat
    else:
        system_prompts = {
            "Helpful Assistant": "You are a friendly and helpful AI assistant.",
            "Code Expert": "You are a master programmer. Give clean, well-commented code snippets.",
            "Sarcastic Buddy": "You are a witty, slightly sarcastic friend who likes to joke around.",
            "Strict Tutor": "You are a precise, educational tutor. Explain concepts clearly and encourage critical thinking."
        }

        # Include Memory Vault facts into standard chat system context!
        vault_context = ""
        if st.session_state.memory_vault:
            vault_context = "\n\nUser Context Facts to remember:\n" + "\n".join([f"- {m}" for m in st.session_state.memory_vault])

        formatted_history = [
            {"role": "system", "content": system_prompts[personality] + vault_context}
        ]

        # 🧠 Feature #12: Inject Memory Vault Context!
        if st.session_state.memory_vault:
            memory_context = "\n".join([f"- {m}" for m in st.session_state.memory_vault])
            formatted_history.append({
                "role": "system",
                "content": f"Here are persistent facts you know about the user across sessions:\n{memory_context}"
            })

        if doc_context:
            formatted_history.append({
                "role": "system",
                "content": f"The user uploaded a document named '{uploaded_doc.name}'. Here is its content:\n\n{doc_context[:10000]}"
            })

        for m in active_chat_list:
            if "content" in m and isinstance(m["content"], str):
                if not isinstance(m["content"], list):
                    formatted_history.append({"role": m["role"], "content": m["content"]})

        stream = client.chat.completions.create(
            model=selected_model,
            messages=formatted_history,
            stream=True
        )

        with st.chat_message("assistant"):
            clean_response = st.write_stream(generate_stream_response(stream))

        clean_response = strip_thinking_process(clean_response)
        audio_data = generate_speech_audio(clean_response)
        
        # Save response to active chat
        active_chat_list.append({
            "role": "assistant", 
            "content": clean_response,
            "audio": audio_data
        })

        # Render Smart Action Cards
        suggestions = generate_action_cards(clean_response)
        st.markdown("##### 🚀 Quick Actions & Next Steps:")
        cols = st.columns(len(suggestions))
        
        for idx, option in enumerate(suggestions):
            if cols[idx].button(option, key=f"suggest_{idx}_{len(active_chat_list)}"):
                active_chat_list.append({"role": "user", "content": option})
                st.rerun()

    st.rerun()
