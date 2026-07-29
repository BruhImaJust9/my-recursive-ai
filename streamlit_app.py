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

# Initialize Bookmarks Storage
if "bookmarks" not in st.session_state:
    st.session_state.bookmarks = []

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

def build_dynamic_system_prompt(user_input, base_personality, language):
    # Base core instructions
    prompt = f"You are an adaptable AI workspace assistant acting as a {base_personality}."
    
    # 🏎️ Auto-detect domain context (e.g., Sports / Complex Analytics)
    sports_keywords = ["nascar", "nfl", "nba", "prediction", "stats", "race", "game"]
    if any(kw in user_input.lower() for kw in sports_keywords):
        prompt += (
            "\n\n[MODE: ANALYTICAL SPORTS EXPERT]"
            "\n- Provide zero generic fluff."
            "\n- Use structured confidence scores (%) and tactical 'Why' bullet points."
            "\n- Use team-colored visual markers/emojis for readability."
        )
    
    # 💻 Auto-detect coding context
    elif any(kw in user_input.lower() for kw in ["code", "python", "error", "streamlit", "function"]):
        prompt += (
            "\n\n[MODE: SENIOR SOFTWARE ENGINEER]"
            "\n- Diagnoses root causes clearly before offering code."
            "\n- Write clean, production-ready code blocks without unnecessary intro prose."
        )

    # 🌐 Append global settings (Feature #18)
    if language != "English":
        prompt += f"\n\nCRITICAL: Respond entirely in {language}."

    return prompt

def run_deep_research_agent(topic: str, client, selected_model) -> str:
    """Autonomous agent that plans sub-queries, executes multiple searches, and compiles a research brief."""
    if not TAVILY_KEY:
        return "⚠️ Missing `TAVILY_API_KEY` for Deep Research!"

    tavily = TavilyClient(api_key=TAVILY_KEY)
    
    plan_prompt = f"Break down this research topic into 3 distinct, specific search queries to get comprehensive coverage: '{topic}'. Output ONLY 3 queries, one per line."
    try:
        plan_res = client.chat.completions.create(
            model=selected_model,
            messages=[{"role": "user", "content": plan_prompt}]
        )
        sub_queries = [q.strip(" 123456789.-*") for q in plan_res.choices[0].message.content.strip().split("\n") if q.strip()][:3]
    except Exception:
        sub_queries = [topic]

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

    synthesis_prompt = (
        f"You are a Lead Intelligence Analyst. Based on the following information, produce a research brief on: '{topic}'.\n\n"
        f"DYNAMIC TONE & FORMAT RULE:\n"
        f"- If the topic is professional/academic, use headers like: 📌 Executive Summary, 🔍 Key Findings, and 💡 Tactical Implications.\n"
        f"- If the topic is casual, sports-related, or pop culture, use headers like: 🏆 Top Contenders, 📊 Key Trends, and 💡 The Verdict.\n\n"
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
    
    if len(lines) >= 3:
        try:
            cleaned_lines = [re.sub(r'^\||\|$', '', line) for line in lines if not re.match(r'^[\vert{}\s:-]+$', line)]
            data = [[cell.strip() for cell in line.split("|")] for line in cleaned_lines]
            
            if len(data) > 1:
                df = pd.DataFrame(data[1:], columns=data[0])
                for col in df.columns:
                    df[col] = pd.to_numeric(df[col].str.replace(',', ''), errors='ignore')
                
                num_cols = df.select_dtypes(include=['number']).columns.tolist()
                
                if num_cols:
                    st.markdown("#### 📊 Dynamic Visual Canvas")
                    st.dataframe(df, use_container_width=True)
                    chart_type = st.radio("Chart Type:", ["Bar", "Line"], horizontal=True, key=f"chart_type_{hash(response_text)}")
                    if chart_type == "Bar":
                        st.bar_chart(df.set_index(df.columns[0])[num_cols])
                    else:
                        st.line_chart(df.set_index(df.columns[0])[num_cols])
        except Exception:
            pass

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

def generate_chat_title(first_prompt: str, client) -> str:
    """Generates a concise 3-4 word title for a new chat session."""
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Create a 2-4 word title for this user prompt. Do not use quotes or punctuation. Return ONLY the title text."},
                {"role": "user", "content": first_prompt}
            ],
            max_tokens=10,
            temperature=0.3
        )
        title = completion.choices[0].message.content.strip().title()
        return title if title else "New Session"
    except Exception:
        return "New Session"

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
            model="llama-3.1-8b-instant",
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

def generate_markdown_export(chat_list, memory_vault) -> str:
    """Formats active chat messages and memory vault into a clean Markdown document."""
    md = "# 🚀 AI Workspace Export\n"
    md += f"**Export Date:** Active Session\n\n"
    
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
    suggestions = []
    if "```" in response_text:
        suggestions.append("🔍 Explain this code step-by-step")
        suggestions.append("⚡ Optimize this code for speed")
    elif any(term in response_text.lower() for term in ["equation", "formula", "calculate", "math"]):
        suggestions.append("🧮 Show alternative solution method")
        suggestions.append("📝 Give me a practice problem on this")
    else:
        suggestions.append("💡 Give me 3 real-world examples")
        suggestions.append("📌 Summarize this in 2 bullet points")
        suggestions.append("❓ What are the main criticisms of this?")
    return suggestions

def get_image_url(prompt: str):
    try:
        encoded_prompt = urllib.parse.quote(prompt.strip())
        url = f"[https://image.pollinations.ai/prompt/](https://image.pollinations.ai/prompt/){encoded_prompt}?width=800&height=800&nologo=true"
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

    # 🌐 Feature #18: Multi-Language Output Toggle
    st.markdown("---")
    st.header("🌐 Response Language")
    target_language = st.selectbox(
        "Select Output Language:",
        ["English", "Spanish", "French", "German", "Mandarin", "Japanese", "Portuguese", "Italian"]
    )

    # 📦 Feature #15: Workspace Export Engine
    st.markdown("---")
    with st.expander("📦 Session Export & Backup"):
        st.caption("Download your active chat history and memory vault facts.")
        
        active_chats = st.session_state.chats.get(st.session_state.current_chat, [])
        
        if active_chats:
            md_data = generate_markdown_export(active_chats, st.session_state.memory_vault)
            st.download_button(
                label="📄 Export as Markdown (.md)",
                data=md_data,
                file_name="workspace_chat_export.md",
                mime="text/markdown",
                use_container_width=True
            )

            sanitized_chats = []
            for msg in active_chats:
                clean_msg = {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                }
                if "image_url" in msg and isinstance(msg["image_url"], str):
                    clean_msg["image_url"] = msg["image_url"]
                sanitized_chats.append(clean_msg)

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

    # 🎨 Feature #19: Custom UI Themes
    st.markdown("---")
    st.header("🎨 Visual Theme")
    theme_choice = st.selectbox(
        "Select Accent Preset:",
        ["Default Streamlit", "Neon Cyberpunk", "Midnight Blue", "Emerald Hacker", "Sunset Warmth"]
    )
    
    # Apply Custom CSS Injection based on theme selection
    theme_styles = {
        "Neon Cyberpunk": """
            <style>
                .stApp { background-color: #0d0f18; color: #00ffcc; }
                .stButton>button { background-color: #ff007f; color: white; border-radius: 8px; }
            </style>
        """,
        "Midnight Blue": """
            <style>
                .stApp { background-color: #0b132b; color: #e0e1dd; }
                .stButton>button { background-color: #1c2541; color: #48cae4; border: 1px solid #48cae4; }
            </style>
        """,
        "Emerald Hacker": """
            <style>
                .stApp { background-color: #051923; color: #00a896; }
                .stButton>button { background-color: #028090; color: #f0f3f4; }
            </style>
        """,
        "Sunset Warmth": """
            <style>
                .stApp { background-color: #2b1e1e; color: #f4a261; }
                .stButton>button { background-color: #e76f51; color: white; }
            </style>
        """
    }

    if theme_choice in theme_styles:
        st.markdown(theme_styles[theme_choice], unsafe_allow_html=True)

    # 🔖 Feature #17: Saved Bookmarks & Snippets
    st.markdown("---")
    with st.expander("🔖 Saved Snippets & Bookmarks"):
        st.caption("Quickly view or copy your pinned responses!")
        if st.session_state.bookmarks:
            for b_idx, bookmark in enumerate(st.session_state.bookmarks):
                st.markdown(f"**Snippet #{b_idx + 1}**")
                st.text_area(
                    label=f"Bookmark {b_idx + 1}",
                    value=bookmark,
                    height=100,
                    key=f"bm_val_{b_idx}",
                    label_visibility="collapsed"
                )
                if st.button("🗑️ Remove", key=f"del_bm_{b_idx}", use_container_width=True):
                    st.session_state.bookmarks.pop(b_idx)
                    st.rerun()
                st.markdown("---")
        else:
            st.info("No saved snippets yet. Click '🔖 Save Snippet' on any AI message!")

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

    auto_play_voice = st.toggle("🔊 Auto-Play Voice Answers", value=False)

    st.markdown("---")
    st.header("🎭 AI Personality")
    personality = st.selectbox(
        "Choose AI Persona:",
        ["Helpful Assistant", "Code Expert", "Sarcastic Buddy", "Strict Tutor"]
    )

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

    with st.expander("📊 Session Analytics"):
        active_list = st.session_state.chats[st.session_state.current_chat]
        msg_count = len(active_list)
        char_count = sum(len(m.get("content", "")) for m in active_list if isinstance(m.get("content"), str))
        st.write(f"**Total Messages:** {msg_count}")
        st.write(f"**Total Characters:** {char_count:,}")

    # Dynamic temperature assignment
if detected_intent in ["SEARCH", "RESEARCH"] or "code" in final_input.lower():
    active_temp = 0.3  # Precision mode: factual & sharp
else:
    active_temp = 0.7  # Creative/Conversational mode: expressive & fluid

response = client.chat.completions.create(
    model=selected_model,
    messages=messages_payload,
    temperature=active_temp  # <-- Set dynamically!
)

# 🛠️📦 Feature #20: Prompt Studio & Session Exporter
    st.markdown("---")
    st.header("🛠️ Prompt Studio & Export")
    
    # 1. Custom Prompt Override
    use_custom_override = st.toggle("Enable Prompt Studio Override", value=False)
    custom_system_override = ""
    if use_custom_override:
        custom_system_override = st.text_area(
            "Studio System Prompt:",
            value="You are an elite expert analyst. Break down complex topics into actionable bullet points.",
            height=100
        )

    # 2. Export Conversation Session
    active_chat_data = st.session_state.chats.get(st.session_state.current_chat, [])
    if active_chat_data:
        # Convert active session to markdown format
        md_export = f"# Chat Session: {st.session_state.current_chat}\n\n"
        for msg in active_chat_data:
            role_name = "User" if msg['role'] == 'user' else "AI Assistant"
            md_export += f"### 👤 {role_name}\n{msg.get('content', '')}\n\n---\n\n"
            
        st.download_button(
            label="📥 Export Chat as Markdown",
            data=md_export,
            file_name=f"{st.session_state.current_chat.replace(' ', '_').lower()}_export.md",
            mime="text/markdown",
            use_container_width=True
        )

# ==========================================
# 4. CHAT HISTORY DISPLAY
# ==========================================

# 🌐🎨 Feature #18 & #19 Status Badge
st.caption(f"⚙️ **Active Workspace Config:** Language: `{target_language}` | Theme Preset: `{theme_choice}`")
st.markdown("---")  # <-- NO SPACES BEFORE THIS LINE!

for idx, msg in enumerate(st.session_state.chats[st.session_state.current_chat]):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        
        if msg["role"] == "assistant" and "content" in msg and msg["content"]:
            render_data_canvas(msg["content"])

            col_bm, _ = st.columns([1, 4])
            with col_bm:
                if st.button("🔖 Save Snippet", key=f"bookmark_btn_{idx}"):
                    if msg["content"] not in st.session_state.bookmarks:
                        st.session_state.bookmarks.append(msg["content"])
                        st.toast("Snippet saved to your Bookmarks tab!", icon="🔖")
                    else:
                        st.toast("Snippet is already saved!", icon="ℹ️")

        if "image_url" in msg:
            st.image(msg["image_url"], use_container_width=True)
        elif "uploaded_img" in msg:
            st.image(msg["uploaded_img"], use_container_width=True)
        
        if "audio" in msg and msg["audio"]:
            is_latest_msg = (idx == len(st.session_state.chats[st.session_state.current_chat]) - 1)
            st.audio(msg["audio"], format="audio/mp3", autoplay=(auto_play_voice and is_latest_msg))

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

if final_input and client:
    # ... (your chat recording and title generation logic above) ...

    # 1. Detect Intent
    detected_intent = "CHAT"
    if final_input.lower().startswith("/generate"):
        detected_intent = "GENERATE"
    elif final_input.lower().startswith("/search"):
        detected_intent = "SEARCH"
    elif final_input.lower().startswith("/research"):
        detected_intent = "RESEARCH"
    elif not active_image:
        detected_intent = classify_user_intent(final_input, client, selected_model)

    # 2. Auto-Detect Style (Prevents NameError!)
    sports_keywords = ["nascar", "nfl", "nba", "prediction", "stats", "race", "game"]
    if any(kw in final_input.lower() for kw in sports_keywords):
        detected_style = "ANALYTICAL"
    elif any(kw in final_input.lower() for kw in ["code", "python", "error", "streamlit"]):
        detected_style = "TECHNICAL"
    else:
        detected_style = "GENERAL"

    # ROUTE 1: Image Generation
    if detected_intent == "GENERATE":
        # ... image logic ...
        pass

    # ROUTE 2: Web Search
    elif detected_intent == "SEARCH":
        # ... search logic ...
        pass

    # ROUTE 3: Deep Research
    elif detected_intent == "RESEARCH":
        # ... research logic ...
        pass

    # ROUTE 4: Standard Dynamic Chat
    else:
        # Check Feature #20 Override first
        if 'use_custom_override' in locals() and use_custom_override and custom_system_override.strip():
            system_prompt = custom_system_override.strip()
        else:
            system_prompt = f"You are a {personality}. Help the user to the best of your ability."

        # Dynamically inject style instructions based on detected_style
        if detected_style == "ANALYTICAL":
            system_prompt += (
                "\n\n[ANALYTICAL MODE ACTIVE]"
                "\n- Provide zero generic fluff."
                "\n- Use structured confidence scores (%) and tactical 'Why' bullet points."
                "\n- Use colored visual indicators/emojis for team/data readability."
            )
        elif detected_style == "TECHNICAL":
            system_prompt += "\n\n[TECHNICAL MODE ACTIVE]\n- Diagnose root causes clearly and provide clean code blocks."

        # Feature #18 Language Injection
        if 'target_language' in locals() and target_language != "English":
            system_prompt += f"\n\nCRITICAL LANGUAGE RULE: Respond entirely in {target_language}."

        if st.session_state.memory_vault:
            system_prompt += "\nSaved User Information:\n" + "\n".join([f"- {m}" for m in st.session_state.memory_vault])

        messages_payload = [{"role": "system", "content": system_prompt}]
        for m in active_chat_list:
            if isinstance(m.get("content"), str):
                messages_payload.append({"role": m["role"], "content": m["content"]})

        with st.spinner("Thinking..."):
            response = client.chat.completions.create(
                model=selected_model,
                messages=messages_payload,
                temperature=0.4 if detected_style == "ANALYTICAL" else 0.7
            )
            assistant_reply = response.choices[0].message.content
            active_chat_list.append({"role": "assistant", "content": assistant_reply})

    st.rerun()
