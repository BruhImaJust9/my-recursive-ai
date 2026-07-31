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
import time
import urllib.request
import pandas as pd
from groq import Groq
from tavily import TavilyClient
from gtts import gTTS
from audio_recorder_streamlit import audio_recorder

# ==========================================
# 0. PERSISTENCE HELPERS
# ==========================================
CHAT_STORAGE_FILE = "persistent_chats.json"

def load_saved_chats():
    if os.path.exists(CHAT_STORAGE_FILE):
        try:
            with open(CHAT_STORAGE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"Chat 1": []}

def save_chats_to_disk():
    try:
        clean_chats = {}
        for session_name, msg_list in st.session_state.chats.items():
            clean_chats[session_name] = []
            for msg in msg_list:
                clean_msg = {k: v for k, v in msg.items() if k not in ["audio", "image_url"]}
                clean_chats[session_name].append(clean_msg)
                
        with open(CHAT_STORAGE_FILE, "w") as f:
            json.dump(clean_chats, f, indent=2)
    except Exception:
        pass

if "chats" not in st.session_state:
    st.session_state.chats = load_saved_chats()

if "current_chat" not in st.session_state:
    st.session_state.current_chat = list(st.session_state.chats.keys())[0]

if "input_buffer" not in st.session_state:
    st.session_state.input_buffer = ""

# ==========================================
# 1. PAGE SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")

st.markdown(
    """
    <style>
        iframe[title*="audio_recorder"],
        iframe[src*="audio_recorder"] {
            background-color: transparent !important;
            border: none !important;
            outline: none !important;
            box-shadow: none !important;
        }

        div[data-testid="stCustomComponentV1"] {
            background-color: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0px !important;
            margin: 0px !important;
        }

        div[data-testid="stVerticalBlock"] > div {
            border: none !important;
            background: transparent !important;
        }

        iframe {
            border: 0px none transparent !important;
        }

        div[data-testid="column"] button {
            border: none !important;
            background: transparent !important;
            color: #888888 !important;
            font-size: 0.8rem !important;
            padding: 2px 8px !important;
            border-radius: 6px !important;
            transition: all 0.2s ease-in-out;
        }
        div[data-testid="column"] button:hover {
            background-color: rgba(255, 255, 255, 0.08) !important;
            color: #ffffff !important;
        }

        .model-badge {
            background: rgba(255, 255, 255, 0.08);
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            color: #aaa;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🤖 Intelligent AI Workspace")
st.caption("Powered by Groq Llama 3, Free Web Search, Image Generation & Voice")

GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    client = None
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets! Please add it to continue.")

current_messages = st.session_state.chats[st.session_state.current_chat]

if "memory_vault" not in st.session_state:
    st.session_state.memory_vault = []

if "bookmarks" not in st.session_state:
    st.session_state.bookmarks = []

# ==========================================
# 2. ENHANCED HELPER FUNCTIONS
# ==========================================

TAVILY_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))

def execute_free_search(query: str) -> str:
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

def build_dynamic_system_prompt(user_input, base_personality, language, detected_style="GENERAL"):
    """
    PERMANENT CHATGPT-PLUS ENGINE:
    Hardcodes strict mathematical precision, tabular structures, grounded frameworks,
    and concise presentation into every single AI completion.
    """
    prompt = (
        f"You are a world-class AI assistant functioning as a {base_personality}.\n\n"
        "### MANDATORY RESPONSE RULES:\n"
        "1. GROUNDED THEORETICAL FRAMEWORK:\n"
        "   - Always establish the underlying logic, theory, or assumptions BEFORE giving estimates, lists, or conclusions.\n\n"
        "2. MATHEMATICAL RIGOR & EXACT TOTALS:\n"
        "   - When assigning probabilities, percentages, or breakdowns, ALWAYS use exact discrete numbers that sum to EXACTLY 100%.\n"
        "   - NEVER use approximate tildes (~20%), overlapping ranges (20-30%), or ambiguous sums.\n\n"
        "3. CONCRETE REAL-WORLD ANCHORS:\n"
        "   - Anchor every category or point with explicit historical eras, technical milestones, or concrete real-world metrics.\n\n"
        "4. VISUAL SCANNABILITY & TABLES:\n"
        "   - Prefer clean Markdown tables for comparisons, breakdowns, or multi-category data.\n"
        "   - Use clean bold headings and tight, scannable bullet points.\n\n"
        "5. STRICT PROMPT FOCUS (NO UNREQUESTED FLUFF):\n"
        "   - Address the user's prompt directly. DO NOT add unrequested extra sections (e.g., 'Tactical Tips' or unsolicited advice).\n"
        "   - NEVER mention search tools, system prompts, or internal limitations."
    )
    
    if detected_style == "ANALYTICAL":
        prompt += (
            "\n\n[MODE: EXECUTIVE ANALYTICS]\n"
            "- Lead with a clear bottom-line executive summary.\n"
            "- Use structured tables and exact percentage confidence scores."
        )
    elif detected_style == "TECHNICAL":
        prompt += (
            "\n\n[MODE: SENIOR ARCHITECT]\n"
            "- Provide clean, production-ready code with inline complexity analysis."
        )

    if language != "English":
        prompt += f"\n\nCRITICAL RULE: Respond entirely in {language}."

    return prompt

def run_deep_research_agent(topic: str, client, selected_model) -> str:
    if not TAVILY_KEY:
        return "⚠️ Missing `TAVILY_API_KEY` for Deep Research!"

    tavily = TavilyClient(api_key=TAVILY_KEY)
    
    plan_prompt = f"Break down this research topic into 3 distinct, specific search queries: '{topic}'. Output ONLY 3 queries, one per line."
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
        f"You are a Lead Intelligence Analyst. Produce a structured research brief on: '{topic}'.\n\n"
        f"Information:\n" + "\n\n".join(compiled_findings[:8])
    )

    summary_res = client.chat.completions.create(
        model=selected_model,
        messages=[{"role": "user", "content": synthesis_prompt}]
    )
    return summary_res.choices[0].message.content

def render_data_canvas(response_text: str):
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

def generate_chat_title(first_prompt: str, client) -> str:
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Create a 2-4 word title for this prompt. Return ONLY the title text."},
                {"role": "user", "content": first_prompt}
            ],
            max_tokens=10,
            temperature=0.3
        )
        title = completion.choices[0].message.content.strip().title()
        return title if title else "New Session"
    except Exception:
        return "New Session"

def classify_user_intent(user_prompt: str, client, selected_model: str) -> str:
    classification_system_prompt = (
        "You are an intent classifier. Analyze input and respond with EXACTLY ONE word:\n"
        "- GENERATE (if explicitly asking to generate or draw an image)\n"
        "- SEARCH (if asking for live current events, weather, stock prices, or specific live stats)\n"
        "- RESEARCH (if asking for an in-depth multi-source report)\n"
        "- CHAT (for standard questions, thought experiments, coding, writing, or analysis)\n"
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
    md = "# 🚀 AI Workspace Export\n\n"
    if memory_vault:
        md += "## 🧠 Memory Vault Facts\n"
        for mem in memory_vault:
            md += f"- {mem}\n"
        md += "\n---\n"

    md += "## 💬 Chat Transcript\n\n"
    for msg in chat_list:
        role = msg.get("role", "user").capitalize()
        content = msg.get("content", "")
        md += f"### {role}\n{content}\n\n"
    return md

def get_image_url(prompt: str):
    try:
        encoded_prompt = urllib.parse.quote(prompt.strip())
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=800&nologo=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read()
    except Exception:
        None

def extract_file_content(uploaded_file) -> str:
    if uploaded_file is None:
        return ""
    file_name = uploaded_file.name.lower()
    if file_name.endswith((".txt", ".py", ".csv", ".md", ".json", ".html", ".css")):
        try:
            return uploaded_file.read().decode("utf-8", errors="ignore")
        except Exception as e:
            return f"[Error reading file: {str(e)}]"
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
        except Exception as e:
            return f"[Error reading PDF: {str(e)}]"
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

# ==========================================
# 3. SIDEBAR & CONTROLS
# ==========================================
image_to_analyze = None

with st.sidebar:
    st.header("⚙️ Workspace Controls")
    st.markdown("---")
    
    st.header("💬 Chat Sessions")
    chat_names = list(st.session_state.chats.keys())
    selected_chat = st.selectbox("Select Thread:", chat_names, index=chat_names.index(st.session_state.current_chat))

    active_chat_export = st.session_state.chats[st.session_state.current_chat]
    if active_chat_export:
        md_data = generate_markdown_export(active_chat_export, st.session_state.memory_vault)
        st.download_button(
            label="📥 Export Chat (.md)",
            data=md_data,
            file_name=f"{st.session_state.current_chat.lower().replace(' ', '_')}.md",
            mime="text/markdown",
            use_container_width=True
        )

    col_clr, col_del = st.columns(2)
    with col_clr:
        if st.button("🧹 Clear", use_container_width=True):
            st.session_state.chats[st.session_state.current_chat] = []
            save_chats_to_disk()
            st.rerun()
            
    with col_del:
        if len(st.session_state.chats) > 1:
            if st.button("🗑️ Delete", use_container_width=True):
                del st.session_state.chats[st.session_state.current_chat]
                st.session_state.current_chat = list(st.session_state.chats.keys())[0]
                save_chats_to_disk()
                st.rerun()
                
    if selected_chat != st.session_state.current_chat:
        st.session_state.current_chat = selected_chat
        st.rerun()

    if st.button("➕ New Chat Session", use_container_width=True):
        new_chat_name = f"Chat {len(st.session_state.chats) + 1}"
        st.session_state.chats[new_chat_name] = []
        st.session_state.current_chat = new_chat_name
        st.rerun()

    st.markdown("---")
    st.header("🌐 Response Language")
    target_language = st.selectbox(
        "Select Output Language:",
        ["English", "Spanish", "French", "German", "Mandarin", "Japanese", "Portuguese", "Italian"]
    )

    st.markdown("---")
    st.header("🎭 AI Persona & Model")
    personality = st.selectbox("Choose AI Persona:", ["Helpful Assistant", "Code Expert", "Sarcastic Buddy", "Strict Tutor"])
    selected_model = st.selectbox("Select Model:", ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"])

    st.markdown("---")
    st.header("🛠️ Prompt Studio Override")
    use_custom_override = st.toggle("Enable Studio Override", value=False)
    custom_system_override = ""
    if use_custom_override:
        custom_system_override = st.text_area(
            "Studio System Prompt:",
            value="You are an elite expert analyst. Break down complex topics into actionable bullet points.",
            height=100
        )

    st.markdown("---")
    st.header("📄 Document & Vision Inputs")
    uploaded_doc = st.file_uploader("Upload Doc/Code", type=["txt", "pdf", "csv", "md", "json", "py"], key="doc_uploader")
    doc_context = extract_file_content(uploaded_doc) if uploaded_doc else ""
    if doc_context:
        st.success(f"Attached `{uploaded_doc.name}`")

    uploaded_file = st.file_uploader("Upload Image to Analyze", type=["png", "jpg", "jpeg", "webp"], key="sidebar_file_uploader")
    if uploaded_file:
        image_to_analyze = Image.open(uploaded_file)
        st.image(image_to_analyze, caption="Sidebar Image", use_container_width=True)

    st.markdown("---")
    st.header("🎤 Voice Controls")
    st.session_state.sidebar_audio_bytes = audio_recorder(text="Record Voice", recording_color="#e84c3d", neutral_color="#6aa84f")

    st.markdown("---")
    st.header("🧠 Memory Vault")
    new_memory = st.text_input("Add Fact:", key="new_memory_input")
    if st.button("💾 Save Memory", use_container_width=True):
        if new_memory.strip():
            st.session_state.memory_vault.append(new_memory.strip())
            st.success("Memory Saved!")
            st.rerun()

    if st.session_state.memory_vault:
        st.caption("Memories: " + ", ".join(st.session_state.memory_vault))

    auto_play_voice = st.toggle("🔊 Auto-Play Voice Answers", value=False)

    st.markdown("---")
    theme_choice = st.selectbox("Visual Theme:", ["Default Streamlit", "Neon Cyberpunk", "Midnight Blue", "Emerald Hacker", "Sunset Warmth"])

    with st.expander("🔖 Bookmarks"):
        if st.session_state.bookmarks:
            for b_idx, bookmark in enumerate(st.session_state.bookmarks):
                st.text_area(f"Snippet {b_idx+1}", bookmark, height=80, key=f"bm_{b_idx}")
        else:
            st.info("No bookmarks saved yet.")

# ==========================================
# 4. CHAT DISPLAY & TOOLBAR
# ==========================================

active_chat_list = st.session_state.chats[st.session_state.current_chat]

col_hdr1, col_hdr2 = st.columns([6, 4])
with col_hdr1:
    st.markdown(f"### 💬 {st.session_state.current_chat}")
with col_hdr2:
    st.markdown(f"<div style='text-align: right;'><span class='model-badge'>🤖 {selected_model}</span> <span class='model-badge'>🌐 {target_language}</span></div>", unsafe_allow_html=True)

st.markdown("---")

if not active_chat_list:
    st.markdown("#### What would you like to explore today?")
    sc1, sc2 = st.columns(2)
    with sc1:
        if st.button("💡 Compare Python vs Rust performance", use_container_width=True):
            st.session_state.input_buffer = "Compare Python vs Rust performance with clear examples."
            st.rerun()
        if st.button("🎨 /generate Futuristic neon Cyberpunk city", use_container_width=True):
            st.session_state.input_buffer = "/generate Futuristic neon Cyberpunk city"
            st.rerun()
    with sc2:
        if st.button("🔍 /search Latest news on AI technology", use_container_width=True):
            st.session_state.input_buffer = "/search Latest news on AI technology"
            st.rerun()
        if st.button("🕵️ /research Quantum Computing breakthroughs", use_container_width=True):
            st.session_state.input_buffer = "/research Quantum Computing breakthroughs"
            st.rerun()

for idx, msg in enumerate(active_chat_list):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        
        if msg["role"] == "assistant" and "content" in msg and msg["content"]:
            render_data_canvas(msg["content"])

            col_bm, col_stats, _ = st.columns([1, 3, 5])
            with col_bm:
                if st.button("🔖 Save", key=f"bookmark_btn_{idx}"):
                    if msg["content"] not in st.session_state.bookmarks:
                        st.session_state.bookmarks.append(msg["content"])
                        st.toast("Snippet saved!", icon="🔖")

            with col_stats:
                if "metrics" in msg:
                    st.caption(f"⚡ {msg['metrics']['tokens']} tokens | ⏱️ {msg['metrics']['speed']} t/s")

        if "image_url" in msg:
            st.image(msg["image_url"], use_container_width=True)
        
        if "audio" in msg and msg["audio"]:
            is_latest_msg = (idx == len(active_chat_list) - 1)
            st.audio(msg["audio"], format="audio/mp3", autoplay=(auto_play_voice and is_latest_msg))

trigger_re_execution = False

if len(active_chat_list) > 1 and active_chat_list[-1]["role"] == "assistant":
    col_reg, col_ed, _ = st.columns([1.2, 1.2, 7.6])
    
    with col_reg:
        if st.button("🔄 Regenerate", key="btn_subtle_regen"):
            active_chat_list.pop()
            save_chats_to_disk()
            trigger_re_execution = True
            
    with col_ed:
        if st.button("✏️ Edit Prompt", key="btn_subtle_edit"):
            active_chat_list.pop()
            if active_chat_list and active_chat_list[-1]["role"] == "user":
                last_user_msg = active_chat_list.pop()
                st.session_state.input_buffer = last_user_msg["content"]
            save_chats_to_disk()
            st.rerun()

# ==========================================
# 5. INPUT LOGIC & DYNAMIC ROUTING
# ==========================================

default_prompt = st.session_state.input_buffer if st.session_state.input_buffer else ""
user_input = st.chat_input("Ask anything, use /search, /generate, or /research...")

if st.session_state.input_buffer:
    st.session_state.input_buffer = ""

final_input = user_input
if "sidebar_audio_bytes" in st.session_state and st.session_state.sidebar_audio_bytes:
    transcribed = transcribe_audio_groq(st.session_state.sidebar_audio_bytes, client)
    if transcribed and not transcribed.startswith("Speech-to-Text Error"):
        final_input = transcribed
        st.session_state.sidebar_audio_bytes = None

if trigger_re_execution and active_chat_list and active_chat_list[-1]["role"] == "user":
    final_input = active_chat_list[-1]["content"]

if final_input and 'client' in globals() and client is not None:
    if len(active_chat_list) == 0 or (len(active_chat_list) == 1 and active_chat_list[0].get("role") == "assistant"):
        new_title = generate_chat_title(final_input, client)
        if new_title != "New Session":
            st.session_state.chats[new_title] = st.session_state.chats.pop(st.session_state.current_chat)
            st.session_state.current_chat = new_title
            active_chat_list = st.session_state.chats[st.session_state.current_chat]

    if not trigger_re_execution:
        active_chat_list.append({"role": "user", "content": final_input})
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

    detected_style = "ANALYTICAL" if any(kw in final_input.lower() for kw in ["stats", "prediction", "compare", "percent", "rate", "code"]) else "GENERAL"

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

    elif detected_intent == "SEARCH":
        clean_query = final_input.replace("/search", "").strip()
        with st.spinner("🔍 Querying live web search..."):
            search_data = execute_free_search(clean_query)
            search_prompt = f"User requested real-time search information for: '{clean_query}'.\nLive Search Data:\n{search_data}"
            response = client.chat.completions.create(
                model=selected_model,
                messages=[{"role": "system", "content": search_prompt}],
                temperature=0.2
            )
            reply = response.choices[0].message.content
            active_chat_list.append({"role": "assistant", "content": reply})

    elif detected_intent == "RESEARCH":
        clean_topic = final_input.replace("/research", "").strip()
        with st.spinner("🕵️ Agent performing multi-step deep research..."):
            brief = run_deep_research_agent(clean_topic, client, selected_model)
            active_chat_list.append({"role": "assistant", "content": brief})

    else:
        system_prompt = build_dynamic_system_prompt(final_input, personality, target_language, detected_style)
        if doc_context:
            system_prompt += f"\n\n[USER ATTACHED FILE CONTEXT]:\n{doc_context[:4000]}"
        if st.session_state.memory_vault:
            system_prompt += "\n\n[MEMORY VAULT FACTS]:\n" + "\n".join([f"- {m}" for m in st.session_state.memory_vault])

        messages_payload = [{"role": "system", "content": system_prompt}]
        for m in active_chat_list:
            if isinstance(m.get("content"), str):
                messages_payload.append({"role": m["role"], "content": m["content"]})

        # Drop temperature to 0.2 for analytical/technical prompts to ensure exact math & structure
        active_temperature = 0.2 if detected_style == "ANALYTICAL" else 0.7

        with st.chat_message("assistant"):
            start_time = time.time()
            stream = client.chat.completions.create(
                model=selected_model,
                messages=messages_payload,
                temperature=active_temperature, # 👈 Update variable here!
                stream=True
            )

        with st.chat_message("assistant"):
            start_time = time.time()
            stream = client.chat.completions.create(
                model=selected_model,
                messages=messages_payload,
                temperature=0.7,
                stream=True
            )
            
            def stream_generator():
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            assistant_reply = st.write_stream(stream_generator)
            elapsed_time = max(time.time() - start_time, 0.01)

        token_count = len(assistant_reply.split()) * 1.3
        speed = round(token_count / elapsed_time, 1)

        audio_data = generate_speech_audio(assistant_reply)
        active_chat_list.append({
            "role": "assistant", 
            "content": assistant_reply, 
            "audio": audio_data,
            "metrics": {"tokens": int(token_count), "speed": speed}
        })

    save_chats_to_disk()
    st.rerun()
