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
# 0. PERSISTENCE HELPERS
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
                clean_msg = {k: v for k, v in msg.items() if k not in ["audio", "image_url"]}
                clean_chats[session_name].append(clean_msg)
                
        with open(CHAT_STORAGE_FILE, "w") as f:
            json.dump(clean_chats, f, indent=2)
    except Exception:
        pass

# Initialize Multi-Chat Sessions (Loads from disk!)
if "chats" not in st.session_state:
    st.session_state.chats = load_saved_chats()

if "current_chat" not in st.session_state:
    st.session_state.current_chat = list(st.session_state.chats.keys())[0]

# ==========================================
# 1. PAGE SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")

# 🎨 CSS FIX: Injected directly after set_page_config to ensure full override
st.markdown(
    """
    <style>
        /* Target audio recorder iframe and all component containers */
        iframe[title*="audio_recorder"],
        div[data-testid="stCustomComponentV1"],
        div[data-testid="stCustomComponentV1"] > iframe {
            background-color: transparent !important;
            background: transparent !important;
            border: none !important;
            outline: none !important;
            box-shadow: none !important;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🤖 Intelligent AI Workspace")
st.caption("Powered by Groq Llama 3, Free Web Search, Image Generation & Voice")
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

def build_dynamic_system_prompt(user_input, base_personality, language, detected_style="GENERAL"):
    """Dynamically shapes system prompt instructions tailored to intent and domain context."""
    prompt = f"You are an adaptable AI workspace assistant acting as a {base_personality}."
    
    # Sports / Data Analytics
    if detected_style == "ANALYTICAL":
        prompt += (
            "\n\n[MODE: ANALYTICAL SPORTS EXPERT]"
            "\n- Provide zero generic fluff."
            "\n- Use structured confidence scores (%) and tactical 'Why' bullet points."
            "\n- Use team-colored visual markers/emojis for readability."
        )
    # Coding / Technical
    elif detected_style == "TECHNICAL":
        prompt += (
            "\n\n[MODE: SENIOR SOFTWARE ENGINEER]"
            "\n- Diagnoses root causes clearly before offering code."
            "\n- Write clean, production-ready code blocks without unnecessary intro prose."
        )

    if language != "English":
        prompt += f"\n\nCRITICAL RULE: Respond entirely in {language}."

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
    """Uses a secondary model pass to evaluate accuracy and logic."""
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
    """Generates a concise 2-4 word title for a chat session."""
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Create a 2-4 word title for this prompt. No quotes or punctuation. Return ONLY the title text."},
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
    """Classifies prompt intent for dynamic execution routing."""
    classification_system_prompt = (
        "You are an intent classifier. Analyze input and respond with EXACTLY ONE word:\n"
        "- GENERATE (if requesting to draw/create an image)\n"
        "- SEARCH (if asking for real-time news, stats, sports, weather, facts)\n"
        "- RESEARCH (if asking for an in-depth report or multi-source report)\n"
        "- CHAT (standard query, coding, general conversation)\n"
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

    # 🚀 Upgrade #22: Export & Management
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

    # 🔖 Bookmarks Tab
    with st.expander("🔖 Bookmarks"):
        if st.session_state.bookmarks:
            for b_idx, bookmark in enumerate(st.session_state.bookmarks):
                st.text_area(f"Snippet {b_idx+1}", bookmark, height=80, key=f"bm_{b_idx}")
        else:
            st.info("No bookmarks saved yet.")

# ==========================================
# 4. CHAT HISTORY DISPLAY
# ==========================================

st.caption(f"⚙️ **Active Config:** Language: `{target_language}` | Theme: `{theme_choice}`")
st.markdown("---")

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
                        st.toast("Snippet saved!", icon="🔖")

        if "image_url" in msg:
            st.image(msg["image_url"], use_container_width=True)
        
        if "audio" in msg and msg["audio"]:
            is_latest_msg = (idx == len(st.session_state.chats[st.session_state.current_chat]) - 1)
            st.audio(msg["audio"], format="audio/mp3", autoplay=(auto_play_voice and is_latest_msg))

# ==========================================
# 5. INPUT LOGIC & DYNAMIC ROUTING
# ==========================================

user_input = st.chat_input("Ask anything, use /search, /generate, or /research...")
audio_bytes = audio_recorder(text="🎤 Record Voice", recording_color="#e84c3d", neutral_color="#6aa84f")

final_input = user_input
if audio_bytes and client:
    transcribed = transcribe_audio_groq(audio_bytes, client)
    if transcribed and not transcribed.startswith("Speech-to-Text Error"):
        final_input = transcribed

if final_input and client:
    active_chat_list = st.session_state.chats[st.session_state.current_chat]
    
    # Auto Title Generator on First Message
    if len(active_chat_list) == 1 and active_chat_list[0].get("role") == "assistant":
        new_title = generate_chat_title(final_input, client)
        if new_title != "New Session":
            st.session_state.chats[new_title] = st.session_state.chats.pop(st.session_state.current_chat)
            st.session_state.current_chat = new_title
            active_chat_list = st.session_state.chats[st.session_state.current_chat]

    # Append User Input
    active_chat_list.append({"role": "user", "content": final_input})
    with st.chat_message("user"):
        st.markdown(final_input)

    # 1. Intent Detection Routing
    detected_intent = "CHAT"
    if final_input.lower().startswith("/generate"):
        detected_intent = "GENERATE"
    elif final_input.lower().startswith("/search"):
        detected_intent = "SEARCH"
    elif final_input.lower().startswith("/research"):
        detected_intent = "RESEARCH"
    elif not image_to_analyze:
        detected_intent = classify_user_intent(final_input, client, selected_model)

    # 2. Dynamic Style Auto-Detection
    sports_keywords = ["nascar", "nfl", "nba", "prediction", "stats", "race", "game"]
    if any(kw in final_input.lower() for kw in sports_keywords):
        detected_style = "ANALYTICAL"
    elif any(kw in final_input.lower() for kw in ["code", "python", "error", "streamlit", "def"]):
        detected_style = "TECHNICAL"
    else:
        detected_style = "GENERAL"

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

    # ROUTE 2: Free Web Search
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

    # ROUTE 3: Deep Research Agent
    elif detected_intent == "RESEARCH":
        clean_topic = final_input.replace("/research", "").strip()
        with st.spinner("🕵️ Agent performing multi-step deep research..."):
            brief = run_deep_research_agent(clean_topic, client, selected_model)
            active_chat_list.append({"role": "assistant", "content": brief})

    # ROUTE 4: Standard Chat with Dynamic Context & Fluid Temperature Tuning (STREAMING ENABLED! 🚀)
    else:
        # Prompt Studio Override vs Dynamic System Builder
        if use_custom_override and custom_system_override.strip():
            system_prompt = custom_system_override.strip()
        else:
            system_prompt = build_dynamic_system_prompt(final_input, personality, target_language, detected_style)

        # Append File Context
        if doc_context:
            system_prompt += f"\n\n[USER ATTACHED FILE CONTEXT]:\n{doc_context[:4000]}"

        # Append Memory Vault
        if st.session_state.memory_vault:
            system_prompt += "\n\n[MEMORY VAULT FACTS]:\n" + "\n".join([f"- {m}" for m in st.session_state.memory_vault])

        # Message Payload Construction
        messages_payload = [{"role": "system", "content": system_prompt}]
        for m in active_chat_list:
            if isinstance(m.get("content"), str):
                messages_payload.append({"role": m["role"], "content": m["content"]})

        active_temp = 0.3 if detected_style in ["ANALYTICAL", "TECHNICAL"] else 0.7

        with st.chat_message("assistant"):
            # 1. Initiate Streaming Completion from Groq
            stream = client.chat.completions.create(
                model=selected_model,
                messages=messages_payload,
                temperature=active_temp,
                stream=True  # 👈 Enables streaming tokens!
            )
            
            # 2. Helper generator to yield chunks to Streamlit in real-time
            def stream_generator():
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            # 3. Stream text directly into the UI live as it generates!
            assistant_reply = st.write_stream(stream_generator)

        # 4. Generate audio and append final reply to chat history
        audio_data = generate_speech_audio(assistant_reply)
        active_chat_list.append({"role": "assistant", "content": assistant_reply, "audio": audio_data})

    save_chats_to_disk()
    st.rerun()
