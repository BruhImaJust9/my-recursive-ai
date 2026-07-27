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
        f"You are a Lead Intelligence Analyst. Based on the following raw search data, produce an Executive Research Brief on: '{topic}'.\n\n"
        f"Include:\n- 📌 Executive Summary\n- 🔍 Key Findings & Trends\n- 💡 Tactical Implications\n\n"
        f"Raw Data:\n" + "\n\n".join(compiled_findings[:8])
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
    if user_input or recorded_text:
        user_data = {"role": "user", "content": final_input}
        if active_image:
            user_data["uploaded_img"] = active_image
        active_chat_list.append(user_data)
    
   # 🎨 ROUTE 1: Image Generation
    if final_input.lower().startswith("/generate") or "generate an image" in final_input.lower():
        prompt = final_input.replace("/generate", "").strip()
        img_bytes = get_image_url(prompt)
        
        if img_bytes:
            active_chat_list.append({
                "role": "assistant", 
                "content": f"Here is your generated image for: **'{prompt}'**",
                "image_url": img_bytes
            })
        else:
            active_chat_list.append({
                "role": "assistant",
                "content": "⚠️ Sorry, image generation timed out or failed. Please try again!"
            })

    # 🔍 ROUTE 2: Web Search
    elif final_input.lower().startswith("/search"):
        cleaned_query = clean_search_query(final_input)
        optimized_query = optimize_search_query(cleaned_query)
        search_text = execute_free_search(optimized_query)
        
        # 1. Start stream with inline web search messages
        stream = client.chat.completions.create(
            model=selected_model,
            messages=[
                {
                    "role": "system", 
                    "content": "Today's date is in 2026. You are a helpful assistant summarizing live web search results."
                },
                {"role": "user", "content": f"Query: '{optimized_query}'\n\nSearch Results:\n{search_text}"}
            ],
            stream=True
        )

        # 2. Pass stream through generator helper for clean streaming
        with st.chat_message("assistant"):
            clean_response = st.write_stream(generate_stream_response(stream))
        
        # 3. Clean tags and generate audio safely
        clean_response = strip_thinking_process(clean_response)
        audio_data = generate_speech_audio(clean_response)
        
        # 4. Save to active chat history
        active_chat_list.append({
            "role": "assistant", 
            "content": clean_response,
            "audio": audio_data
        })

    # 👀 ROUTE 3: Vision Analysis
    elif active_image is not None:
        base64_img = encode_image_to_base64(active_image)
        completion = client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": final_input},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                    ]
                }
            ]
        )
        response_text = completion.choices[0].message.content
        clean_response = strip_thinking_process(response_text)
        audio_data = generate_speech_audio(clean_response)
        
        active_chat_list.append({
            "role": "assistant", 
            "content": clean_response,
            "audio": audio_data
        })

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

        formatted_history = [
            {"role": "system", "content": system_prompts[personality]}
        ]

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
        
        # 1. Save response to active chat
        active_chat_list.append({
            "role": "assistant", 
            "content": clean_response,
            "audio": audio_data
        })

        # 💡 2. Render Smart Action Cards right before rerun
        suggestions = generate_action_cards(clean_response)
        st.markdown("##### 🚀 Quick Actions & Next Steps:")
        cols = st.columns(len(suggestions))
        
        for idx, option in enumerate(suggestions):
            if cols[idx].button(option, key=f"suggest_{idx}_{len(active_chat_list)}"):
                active_chat_list.append({"role": "user", "content": option})
                st.rerun()

    st.rerun()
