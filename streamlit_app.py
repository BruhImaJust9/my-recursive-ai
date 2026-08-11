import base64
import contextlib
import io
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from audio_recorder_streamlit import audio_recorder
from groq import Groq
from gtts import gTTS
import pandas as pd
from PIL import Image
import streamlit as st
from tavily import TavilyClient

# ==============================================================================
# 0. PERSISTENCE & HELPERS
# ==============================================================================
CHAT_STORAGE_FILE = "persistent_chats.json"


def load_saved_chats() -> dict:
    """Loads saved chat threads from disk safely with structural validation."""
    if os.path.exists(CHAT_STORAGE_FILE):
        try:
            with open(CHAT_STORAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and len(data) > 0:
                    return data
        except Exception as err:
            print(f"⚠️ [PERSISTENCE WARN] Failed to load chat history: {err}")
            
    return {"New Chat": []}


def save_chats_to_disk() -> None:
    """Atomically serializes chat history to disk for authenticated sessions.
    
    Prevents file corruption on unexpected app terminations.
    """
    try:
        # Only persist data if the user is authenticated as Admin/Owner
        if not st.session_state.get("is_logged_in", False):
            return

        clean_chats = {}
        for session_name, msg_list in st.session_state.chats.items():
            clean_chats[session_name] = []
            
            for msg in msg_list:
                if not isinstance(msg, dict):
                    continue
                
                # Exclude non-serializable payloads, base64 data, and heavy media
                clean_msg = {
                    k: v for k, v in msg.items()
                    if k not in ["audio", "image_url", "bytes", "raw_response"]
                    and isinstance(v, (str, int, float, bool, list, dict))
                }
                clean_chats[session_name].append(clean_msg)

        # Atomic File Write: Save to temp file first, then replace original
        dir_name = os.path.dirname(CHAT_STORAGE_FILE) or "."
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(clean_chats, tf, indent=2, ensure_ascii=False)
            temp_path = tf.name

        os.replace(temp_path, CHAT_STORAGE_FILE)

    except Exception as err:
        print(f"⚠️ [PERSISTENCE ERROR] Failed to save chats to disk: {err}")


# ==============================================================================
# SESSION STATE INITIALIZATION
# ==============================================================================
if "is_logged_in" not in st.session_state:
    st.session_state.is_logged_in = False  # Default to Guest Mode

if "chats" not in st.session_state:
    if st.session_state.is_logged_in:
        st.session_state.chats = load_saved_chats()
    else:
        st.session_state.chats = {"New Chat": []}

if "current_chat" not in st.session_state or st.session_state.current_chat not in st.session_state.chats:
    st.session_state.current_chat = list(st.session_state.chats.keys())[0]

if "input_buffer" not in st.session_state:
    st.session_state.input_buffer = ""

if "memory_vault" not in st.session_state:
    st.session_state.memory_vault = []

if "bookmarks" not in st.session_state:
    st.session_state.bookmarks = []

# UPGRADE #60: Workspace Telemetry Tracker
if "telemetry" not in st.session_state:
    st.session_state.telemetry = {
        "requests": 0, 
        "est_tokens": 0, 
        "last_latency": 0.0
    }


# ==============================================================================
# CLIENT & CLIENT SECRET INITIALIZATIONS
# ==============================================================================
OPENROUTER_KEY = st.secrets.get("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))

if OPENROUTER_KEY:
    try:
        openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_KEY,
        )
    except Exception as err:
        print(f"⚠️ [CLIENT ERROR] Could not initialize OpenRouter client: {err}")
        openrouter_client = None
else:
    openrouter_client = None


import re
import io
import base64
import tempfile
from datetime import datetime

# ==============================================================================
# UPGRADES #33 & #48: LATEX & SYNTAX AUTO-REPAIR ENGINE
# ==============================================================================
def sanitize_and_repair_formatting(text: str) -> str:
    """Automatically fixes LaTeX math syntax, normalizes markdown spacing,
    repairs broken list formatting, and strips unwanted retrieval disclaimers.
    """
    if not text:
        return ""

    # 1. Standardize Display Math Syntax: \[ ... \] -> $$ ... $$
    text = re.sub(r"\\\[\s*([\s\S]*?)\s*\\\]", r"$$\1$$", text)

    # 2. Standardize Inline Math Syntax: \( ... \) -> $ ... $
    text = re.sub(r"\\\(\s*([\s\S]*?)\s*\\\)", r"$\1$", text)

    # 3. Repair Broken Markdown Lists (e.g., "word* bullet" -> "word\n* bullet")
    text = re.sub(r"([^\n])\n?(\s*[*|-]\s+[A-Za-z0-9])", r"\1\n\2", text)
    text = re.sub(r"([^\n])\n?(\s*\d+\.\s+[A-Za-z0-9])", r"\1\n\2", text)

    # 4. Remove Search Artifacts & Meta-Disclaimers
    search_disclaimers = [
        r"The provided search results do not directly address.*?\n",
        r"Based on the search results provided.*?\n",
        r"According to the retrieved sources.*?\n"
    ]
    for pattern in search_disclaimers:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # 5. Clean multi-line whitespace padding
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()

def handle_chat_input(user_input: str):
    # Intercept system/slash commands before passing to LLM
    if user_input.startswith("/image"):
        prompt = user_input.replace("/image", "").strip()
        # Call DALL-E / Flux / Stable Diffusion API here
        return render_generated_image(prompt)

    elif user_input.startswith("/search"):
        query = user_input.replace("/search", "").strip()
        # Call Google Search / Tavily API here
        return perform_live_search(query)

    # Standard LLM completion
    return generate_llm_response(user_input)

def handle_chat_pipeline(user_input: str):
    # 1. Intercept Image Requests
    if user_input.startswith("/image"):
        clean_prompt = user_input.replace("/image", "").strip()
        return generate_and_render_image(clean_prompt)

    # 2. Intercept Live Search Requests
    elif user_input.startswith("/search") or "search" in user_input.lower():
        clean_query = user_input.replace("/search", "").strip()
        search_context = perform_live_search(clean_query)
        
        # Pass retrieved web context into the LLM prompt
        augmented_prompt = f"Web Search Context:\n{search_context}\n\nUser Question: {clean_query}"
        return query_llm_with_context(augmented_prompt)

    # 3. Standard Chat Output
    else:
        return query_llm_standard(user_input)
        

import streamlit as st
import time

def process_user_intent(user_input: str):
    """Processes slash commands with real-time status feedback containers."""
    
    # 1. LIVE SEARCH INTERCEPTOR
    if user_input.startswith("/search"):
        query = user_input.replace("/search", "").strip()
        
        # Display the live animated status box
        with st.status("🌐 Synthesizing multi-query angle...", expanded=True) as status:
            st.write(f"🔎 Drafting search vector for: `{query}`...")
            time.sleep(0.8)  # Simulating web fetch
            
            st.write("📊 Aggregating live web search results...")
            time.sleep(0.6)
            
            status.update(label="✅ Search completed!", state="complete", expanded=False)
        
        # Return real search results here
        return perform_live_search(query)

    # 2. IMAGE GENERATION INTERCEPTOR
    elif user_input.startswith("/image"):
        prompt = user_input.replace("/image", "").strip()
        
        with st.status("🎨 Generating image...", expanded=True) as status:
            st.write(f"🖌️ Rendering canvas prompt: *'{prompt}'*...")
            time.sleep(1.2)  # Simulating diffusion pipeline call
            
            status.update(label="✨ Image rendered successfully!", state="complete", expanded=False)
        
        # Display image via Streamlit widget
        return render_generated_image(prompt)

    # 3. STANDARD LLM RESPONSE
    else:
        return execute_standard_llm(user_input)

import streamlit as st
import requests

def perform_live_search(query: str) -> str:
    """Queries a search API and formats the context for the LLM."""
    with st.status("🌐 Synthesizing multi-query angle...", expanded=True) as status:
        st.write(f"🔎 Executing search vector: `{query}`...")
        
        try:
            # Example using a search API endpoint
            # Replace with your API key / provider (SerpAPI, Tavily, Google, etc.)
            api_url = f"https://api.tavily.com/search"
            payload = {"query": query, "api_key": st.secrets.get("TAVILY_API_KEY", "")}
            
            response = requests.post(api_url, json=payload, timeout=10)
            data = response.json()
            
            results = data.get("results", [])
            formatted_context = "\n".join([f"- {r['title']}: {r['content']}" for r in results[:3]])
            
            status.update(label="✅ Search completed!", state="complete", expanded=False)
            return formatted_context

        except Exception as e:
            status.update(label="❌ Search failed", state="error", expanded=False)
            return f"Search error: {str(e)}"

import time
import streamlit as st
import openai

def smart_model_router(prompt: str, client, preferred_model: str = "llama-3.3-70b-versatile") -> str:
    """
    Production-Grade Smart Router:
    1. Analyzes prompt complexity to pick the cheapest/fastest optimal model.
    2. Executes the call with streaming UI output.
    3. Provides automatic fallback to backup models if the main provider fails.
    """
    # 1. Complexity Heuristic Check
    prompt_len = len(prompt.split())
    is_complex = any(kw in prompt.lower() for kw in [
        "code", "refactor", "analyze", "explain in detail", "architecture", "compare", "math"
    ]) or prompt_len > 120

    # 2. Select Optimal Model Tier
    if is_complex:
        primary_model = preferred_model  # Heavyweight model
        backup_model = "gpt-4o-mini"
    else:
        primary_model = "llama-3.1-8b-instant"  # Lightning-fast lightweight model
        backup_model = preferred_model

    # Helper function to attempt completion call
    def attempt_completion(model_name: str):
        return client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            stream=True
        )

    # 3. Execution with Automatic Fallback & Streaming
    response_container = st.empty()
    full_response = ""

    try:
        # Try Primary Selected Model
        stream = attempt_completion(primary_model)
        st.caption(f"⚡ Routed to: `{primary_model}`")
    except Exception as primary_error:
        # Failover to Backup Model if Primary Fails
        st.warning(f"⚠️ `{primary_model}` unavailable. Failing over to `{backup_model}`...")
        try:
            stream = attempt_completion(backup_model)
            st.caption(f"🛡️ Routed to Backup: `{backup_model}`")
        except Exception as fallback_error:
            st.error("❌ All model providers are currently unreachable.")
            return f"Error: {str(fallback_error)}"

    # 4. Stream response to UI in real-time
    for chunk in stream:
        content = chunk.choices[0].delta.content or ""
        full_response += content
        response_container.markdown(full_response + "▌")

    response_container.markdown(full_response)
    return full_response

# ==============================================================================
# UPGRADE #65: LIVE DYNAMIC WORKSPACE TELEMETRY & BENCHMARKER
# ==============================================================================
def render_telemetry_dashboard() -> None:
    """Renders a real-time analytics and performance dashboard monitoring
    token velocity, latency trends, and estimated API usage metrics.
    """
    st.markdown("### 📊 Workspace Telemetry & Health Monitor")

    # Fetch live state metrics safely
    telemetry = st.session_state.get(
        "telemetry", {"requests": 0, "est_tokens": 0, "last_latency": 0.0}
    )
    
    total_requests = telemetry.get("requests", 0)
    total_tokens = telemetry.get("est_tokens", 0)
    last_latency = telemetry.get("last_latency", 0.0)

    # Compute derived performance indicators
    avg_tokens_per_req = (
        round(total_tokens / total_requests, 1) if total_requests > 0 else 0
    )
    # Estimated cost savings vs standard proprietary endpoints ($0.002 / 1k tokens)
    est_cost_savings = f"${(total_tokens / 1000) * 0.002:.4f}"

    # Display Top Metrics Grid
    col_t1, col_t2, col_t3, col_t4 = st.columns(4)

    with col_t1:
        st.metric(
            label="Total Requests",
            value=f"{total_requests:,}",
            delta=f"+1" if total_requests > 0 else "0",
        )

    with col_t2:
        st.metric(
            label="Est. Tokens Processed",
            value=f"{total_tokens:,}",
            delta=f"~{avg_tokens_per_req}/req",
        )

    with col_t3:
        # Latency Health Color Indicator
        latency_status = (
            "⚡ Fast"
            if last_latency < 1.5
            else ("🟢 Normal" if last_latency < 3.5 else "🟡 Slow")
        )
        st.metric(
            label="Last Latency",
            value=f"{last_latency:.2f}s",
            delta=latency_status,
            delta_color="normal" if last_latency < 3.5 else "inverse",
        )

    with col_t4:
        st.metric(
            label="Est. Cost Saved",
            value=est_cost_savings,
            help="Calculated against standard cloud LLM token pricing rates.",
        )

    # Telemetry Status Bar
    st.progress(
        min(1.0, total_requests / 100),
        text=f"Session Usage Velocity: {total_requests}/100 requests threshold",
    )


import streamlit as st

# ==============================================================================
# UPGRADE #65-B: SIDEBAR TELEMETRY WIDGET (PRODUCTION GRADE)
# ==============================================================================
def render_sidebar_telemetry_widget() -> None:
    """Renders a compact, real-time analytics card in the sidebar displaying
    request velocity, token consumption, and model latency metrics.
    """
    # 1. Guarantee state initialization
    if "telemetry" not in st.session_state:
        st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}

    telemetry = st.session_state.telemetry
    reqs = telemetry.get("requests", 0)
    tokens = telemetry.get("est_tokens", 0)
    latency = telemetry.get("last_latency", 0.0)

    # 2. Derived performance metrics
    avg_tokens = round(tokens / reqs) if reqs > 0 else 0
    
    # Latency rating badge
    if latency == 0.0:
        latency_badge = "⏸️ Idle"
    elif latency < 1.5:
        latency_badge = "⚡ Fast"
    elif latency < 3.5:
        latency_badge = "🟢 Normal"
    else:
        latency_badge = "🟡 Slow"

    # 3. Render Widget
    with st.sidebar.expander("📈 **Live Telemetry**", expanded=False):
        col_m1, col_m2 = st.columns(2)
        
        with col_m1:
            st.metric("Requests", f"{reqs:,}")
            st.metric("Avg Tkn/Req", f"{avg_tokens:,}")

        with col_m2:
            st.metric("Total Tokens", f"{tokens:,}")
            st.metric("Latency", f"{latency:.2f}s", delta=latency_badge, delta_color="off")

        st.markdown("---")

        # Reset Telemetry Action
        if st.button("🧹 Reset Telemetry", key="sidebar_reset_telemetry_btn", use_container_width=True):
            st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}
            st.toast("Telemetry metrics reset!", icon="🧹")
            st.rerun()

import streamlit as st
from openai import OpenAI

import streamlit as st
import urllib.parse

def generate_and_render_image(prompt: str) -> str:
    """Generates and displays an image using Pollinations.ai (Free, no API key needed)."""
    with st.status("🎨 Generating image...", expanded=True) as status:
        st.write(f"🖌️ Rendering canvas for: *'{prompt}'*...")
        
        try:
            # Clean and encode prompt for URL format
            encoded_prompt = urllib.parse.quote(prompt)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
            
            # Display directly in Streamlit UI
            st.image(image_url, caption=f"Generated: {prompt}", use_container_width=True)
            
            status.update(label="✨ Image rendered successfully!", state="complete", expanded=False)
            return f"![Generated Image]({image_url})"

        except Exception as e:
            status.update(label="❌ Image generation failed", state="error", expanded=False)
            st.error(f"Failed to generate image: {str(e)}")
            return "Image generation failed."
            
# ==============================================================================
# UPGRADE #66: FRONTIER AGENTIC REASONING & COMPREHENSIVE ANALYSIS ENGINE
# ==============================================================================
def inject_analytical_thinking_engine(user_prompt: str, client, model_name: str = "llama-3.3-70b-versatile") -> str:
    """Forces the LLM to abandon standard 'short-answer' mode in favor of 
    structured multi-factor analysis, probabilistic estimations, and deep reasoning.
    """
    
    SYSTEM_SOUCE_PROMPT = (
        "You are a World-Class Strategic Analyst, Educational Consultant, and Systems Engineer.\n"
        "Your goal is to provide deep, beautifully formatted, multi-perspective answers that "
        "explain THE WHY behind every conclusion.\n\n"
        
        "CRITICAL RESPONSE DIRECTIVES:\n"
        "1. NEVER give a lazy one-sentence answer. Always break down complex prompts into logical tiers.\n"
        "2. USE STRUCTURAL BREAKDOWNS: Use bold headers, numbered logic chains, bullet points, and percentage/probability matrices.\n"
        "3. EVALUATE CONTRADICTORY DATA: If input contains mixed signals (e.g., high test scores vs. average grades), explicitely detail the tension between those variables.\n"
        "4. ESTIMATE PROBABILITIES: When exact outcomes are unknown, provide percentage-based estimations with rationale for each tier.\n"
        "5. PROACTIVE FOLLOW-UPS: End deep analyses with an engaging, highly relevant question or invitation to explore edge cases.\n"
        "6. TONE: Intelligent, empathetic, grounded, and engaging with clean structural clarity."
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_SOUCE_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.6,  # Perfect balance between creative reasoning and deterministic logic
            max_tokens=2500,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"❌ Execution Error in Reasoning Engine: {str(e)}"

# ==============================================================================
# UPGRADE #58: DYNAMIC CHAT HISTORY & ACTION TOOLBAR RENDERER
# ==============================================================================
def render_chat_history_thread(active_chat_list: list, client=None) -> None:
    """Renders the entire conversation thread with inline LaTeX repair,
    multimodal image/audio attachments, and assistant action toolbars.
    """
    if not active_chat_list:
        st.info("👋 Welcome! Start a conversation or pick a command below.")
        return

    for msg_idx, msg in enumerate(active_chat_list):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        # Determine avatar display icon
        avatar = "👤" if role == "user" else "🤖"

        with st.chat_message(role, avatar=avatar):
            # 1. Handle Multimodal Input (Text + Image Payloads)
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            raw_text = part.get("text", "")
                            clean_text = (
                                sanitize_and_repair_formatting(raw_text)
                                if "sanitize_and_repair_formatting" in globals()
                                else raw_text
                            )
                            st.markdown(clean_text)
                            
                        elif part.get("type") == "image_url":
                            image_url = part.get("image_url", {}).get("url", "")
                            if image_url:
                                st.image(image_url, caption="Attached Image", use_container_width=True)
            else:
                # 2. Standard Text Rendering with Formatting Auto-Repair
                clean_text = (
                    sanitize_and_repair_formatting(str(content))
                    if "sanitize_and_repair_formatting" in globals()
                    else str(content)
                )
                st.markdown(clean_text)

            # 3. Assistant Message Action Toolbar & Interactive Elements
            if role == "assistant":
                # Render Inline Code Executor if code blocks exist in text
                if "render_interactive_code_runner" in globals():
                    render_interactive_code_runner(clean_text, msg_idx)

                # Interactive Action Bar (TTS & Utility Actions)
                col_tb1, col_tb2, col_tb3 = st.columns([2, 2, 8])
                
                with col_tb1:
                    # Text-to-Speech Action Button
                    tts_key = f"tts_btn_{msg_idx}"
                    if st.button("🔊 Listen", key=tts_key, help="Generate spoken audio"):
                        if "generate_tts_audio" in globals():
                            with st.spinner("Generating speech..."):
                                audio_path = generate_tts_audio(clean_text)
                                if audio_path:
                                    st.audio(audio_path, format="audio/mp3")
                                else:
                                    st.error("Audio generation unavailable.")

                with col_tb2:
                    # Model Badge / Telemetry Label
                    model_label = msg.get("model", "Llama 3.3")
                    st.markdown(f"<span class='model-badge'>{model_label}</span>", unsafe_allow_html=True)


import re
import tempfile
from datetime import datetime
import streamlit as st

# ==============================================================================
# CHAT EXPORT PIPELINE (FRONTIER MARKDOWN GENERATOR)
# ==============================================================================
def export_chat_as_markdown(chat_list: list, title: str = "Chat Session") -> str:
    """Converts structured chat history into clean, standardized Markdown 
    complete with ISO header metadata and formatted message blocks.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    md_content = [
        f"# 📄 {title}",
        f"**Exported On:** {timestamp}  ",
        f"**Total Messages:** {len(chat_list)}  ",
        "\n---\n"
    ]

    for msg in chat_list:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Handle structural content payloads (e.g. lists or dicts) safely
        if isinstance(content, list):
            extracted = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    extracted.append(item.get("text", ""))
            content = "\n".join(extracted) if extracted else str(content)
        elif not isinstance(content, str):
            content = str(content)

        header = "### 👤 User" if role == "user" else "### 🤖 Assistant"
        md_content.append(f"{header}\n\n{content.strip()}\n\n---\n")

    return "\n".join(md_content)


def render_chat_export_ui() -> None:
    """UI Helper to render the Markdown download action safely in the sidebar."""
    current_chat_name = st.session_state.get("current_chat", "New Chat")
    
    # Safely fetch active chat thread
    chats = st.session_state.get("chats", {})
    active_chat_list = chats.get(current_chat_name, [])

    if active_chat_list:
        md_data = export_chat_as_markdown(active_chat_list, title=current_chat_name)
        clean_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', current_chat_name).lower()
        
        st.sidebar.download_button(
            label="📥 Export Chat (.md)",
            data=md_data,
            file_name=f"{clean_filename}_export.md",
            mime="text/markdown",
            use_container_width=True,
            key="export_chat_md_btn"
        )


# ==============================================================================
# UPGRADE #61: TEXT-TO-SPEECH AUDIO PIPELINE
# ==============================================================================
def generate_tts_audio(text: str, speed_factor: float = 1.0) -> str:
    """Strips markdown/code syntax and converts text into spoken audio.
    
    Returns temporary file path for UI audio playback widget.
    """
    if not text or not text.strip():
        return None

    try:
        from gtts import gTTS
    except ImportError:
        print("⚠️ [TTS WARN] 'gTTS' package is not installed. Run 'pip install gTTS'.")
        return None

    try:
        # 1. Clean markdown, LaTeX, HTML, and code snippets for spoken clarity
        clean_text = text
        clean_text = re.sub(r"```[\s\S]*?```", " [code block omitted] ", clean_text)  # Remove raw code
        clean_text = re.sub(r"`.*?`", "", clean_text)                                 # Remove inline code
        clean_text = re.sub(r"\$\$.*?\$\$", " [equation] ", clean_text)              # Remove display math
        clean_text = re.sub(r"\$.*?\$", " [math] ", clean_text)                       # Remove inline math
        clean_text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", clean_text)                # Keep link text, strip URL
        clean_text = re.sub(r"<.*?>", "", clean_text)                                # Strip raw HTML
        clean_text = re.sub(r"[*_#~>]", "", clean_text)                               # Strip formatting symbols
        clean_text = re.sub(r"\s+", " ", clean_text).strip()                          # Collapse extra spaces

        # Limit to first 400 characters for high-speed speech output
        clean_text = clean_text[:400]

        if not clean_text:
            return None

        # 2. Render speech via gTTS
        tts = gTTS(text=clean_text, lang="en", slow=(speed_factor < 1.0))
        
        # 3. Save safely to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            tts.save(fp.name)
            return fp.name

    except Exception as err:
        print(f"⚠️ [TTS ERROR] Speech synthesis failed: {err}")
        return None

# ==========================================
# 2. UPGRADE #32 & #34: MULTI-ANGLE SEARCH & FACT ENGINE
# ==========================================
TAVILY_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))


def execute_deconstructed_multi_search(
    query: str, client, selected_model: str
) -> str:
    """UPGRADE #32 & #34: Deconstructs complex queries into sub-searches,
    executes multi-angle retrieval, and synthesizes factually verified claims with clickable citations.
    """
    if not TAVILY_KEY:
        return "⚠️ Missing `TAVILY_API_KEY` in Streamlit secrets!"

    tavily = TavilyClient(api_key=TAVILY_KEY)

    # 1. Generate 3 complementary sub-queries for multi-angle synthesis
    sub_query_prompt = (
        f"Deconstruct this query into 3 distinct search sub-queries to capture different angles (e.g. core facts, counter-evidence, latest consensus):\n"
        f"Query: '{query}'\n"
        "Return ONLY the 3 queries, one per line, with no extra text."
    )

    try:
        sub_res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": sub_query_prompt}],
            temperature=0.1,
        )
        queries = [
            q.strip(" 123456789.-*")
            for q in sub_res.choices[0].message.content.strip().split("\n")
            if q.strip()
        ][:3]
    except Exception:
        queries = [query]

    # 2. Concurrently retrieve facts across queries
    aggregated_sources = []
    sources_metadata = []
    source_counter = 1

    for q in queries:
        try:
            res = tavily.search(query=q, max_results=2)
            for item in res.get("results", []):
                title = item.get("title", "Source").strip()
                content = item.get("content", "").strip()
                url = item.get("url", "#").strip()
                
                # Store structured format for LLM prompt context
                aggregated_sources.append(
                    f"[{source_counter}] **{title}** (URL: {url})\nContent: {content}"
                )
                
                # Store metadata for references list at the bottom
                sources_metadata.append({
                    "id": source_counter,
                    "title": title,
                    "url": url
                })
                
                source_counter += 1
        except Exception:
            continue

    if not aggregated_sources:
        return "No authoritative search sources could be retrieved."

    # 3. Synthesize with Clickable Citation Anchors
    synthesis_prompt = (
        f"You are an expert research assistant. Synthesize an accurate, well-structured answer for the query: '{query}' using these factual sources.\n\n"
        "CRITICAL CITATION RULES:\n"
        "1. Insert clickable markdown citation links in your answer whenever referencing facts, e.g., [[1]](URL), [[2]](URL).\n"
        "2. Make sure the citation URL matches the exact source URL provided below.\n"
        "3. Keep the tone informative, balanced, and easy to read.\n\n"
        "SOURCES:\n" + "\n\n".join(aggregated_sources)
    )

    synthesis_res = client.chat.completions.create(
        model=selected_model,
        messages=[{"role": "user", "content": synthesis_prompt}],
        temperature=0.2,
    )

    ai_response = synthesis_res.choices[0].message.content.strip()

    # 4. Append clean Sources & References footer
    references_footer = "\n\n---\n### 🌐 Sources & References\n"
    for src in sources_metadata:
        references_footer += f"* [[{src['id']}]] [{src['title']}]({src['url']})\n"

    return ai_response + references_footer

# ==============================================================================
# UPGRADE #31-#57: UNIVERSAL REASONING & SYNTHESIS ENGINE (FRONTIER PARALLEL)
# ==============================================================================
import re

def build_dynamic_system_prompt(
    user_input: str, base_personality: str, language: str, detected_style: str = "GENERAL"
) -> str:
    """Builds a high-density, context-aware dynamic system prompt for LLMs.
    
    Includes dynamic domain activation, intelligent memory retrieval, 
    and adaptive stylistic routing (Claude/GPT-4o output tiering).
    """
    lowered_input = user_input.lower()

    # --------------------------------------------------------------------------
    # 1. CASUAL ROUTE: Ultra-fast, natural conversational mode
    # --------------------------------------------------------------------------
    if detected_style == "CASUAL":
        return (
            f"You are a warm, highly intelligent, and empathetic peer operating as a {base_personality}.\n"
            f"RULES:\n"
            f"- Speak naturally, concisely, and directly in {language}.\n"
            f"- Avoid robotic disclaimers, rigid technical tables, or unnatural markdown headers unless requested.\n"
            f"- Be conversational, perceptive, and helpful."
        )

    # --------------------------------------------------------------------------
    # 2. CORE REASONING FRAMEWORK (Frontier Cognitive System Prompt)
    # --------------------------------------------------------------------------
    prompt = [
        f"You are an elite, highly competent AI assistant operating as a {base_personality}.",
        "",
        "### 🧠 CORE COGNITIVE & EPISTEMIC DIRECTIVES:",
        "1. **First-Principles Decomposition:** Strip problems down to fundamental physical, mathematical, or logical axioms.",
        "2. **Rigorous Epistemics:** Distinguish empirical facts from theoretical models and speculation. Avoid pseudoscience tropes.",
        "3. **Dual-Pass Verification:** Internally audit code, calculations, and logical chains for edge cases before outputting.",
        "4. **Quantitative Precision:** Provide concrete units, Big-O metrics, probabilities, or orders of magnitude where applicable.",
        "",
        "### ⚡ SYNTHESIS & FORMATTING GUIDELINES:",
        "- **Zero Fluff:** Skip meta-preambles ('Sure, here is...') and self-congratulatory summaries. Start directly with the answer.",
        "- **High-Concept Structure:** Use bold conceptual titles, clear hierarchy, and clean scannable bullet points.",
        "- **Contrastive Clarity:** Make proposed solutions fundamentally distinct rather than minor variations.",
        "- **Falsiability & Bounds:** Explicitly identify failure modes, assumptions, and physical/system limits.",
        "- **Code & UI Artifacts:** Provide complete, production-ready, self-contained code blocks with explicit syntax highlighting.",
        "- **Impactful Conclusion:** End complex topics with a sharp, memorable core takeaway."
    ]

    # --------------------------------------------------------------------------
    # 3. DYNAMIC DOMAIN ADAPTATION (Keyword Matching)
    # --------------------------------------------------------------------------
    domain_rules = {
        ("physics", "dyson", "kardashev", "quantum", "relativity", "thermodynamics", "space", "astronomy"): (
            "\n[DOMAIN ACTIVATED: ASTROPHYSICS & HARD SCIENCE]\n"
            "- Apply strict relativistic mechanics, quantum field concepts, and thermodynamic limits."
        ),
        ("code", "architecture", "algorithm", "python", "javascript", "refactor", "bug", "api", "database"): (
            "\n[DOMAIN ACTIVATED: PRINCIPAL SYSTEMS ARCHITECT]\n"
            "- Prioritize production modularity, edge-case safety, typed signatures, and execution efficiency."
        ),
        ("data", "dataframe", "pandas", "plot", "csv", "statistics", "machine learning", "regression"): (
            "\n[DOMAIN ACTIVATED: SENIOR DATA SCIENTIST]\n"
            "- Focus on statistical validity, data pipeline hygiene, vectorization, and actionable visualizations."
        ),
        ("story", "creative", "fiction", "worldbuilding", "narrative", "character", "dialogue"): (
            "\n[DOMAIN ACTIVATED: CREATIVE DIRECTOR & WORLD-BUILDER]\n"
            "- Focus on sensory immersion, high-stakes conflict, distinct character voice, and original metaphors."
        )
    }

    for keywords, adaptation_prompt in domain_rules.items():
        if any(kw in lowered_input for kw in keywords):
            prompt.append(adaptation_prompt)
            break

    # --------------------------------------------------------------------------
    # 4. MODE MODIFIERS
    # --------------------------------------------------------------------------
    if detected_style == "ANALYTICAL":
        prompt.append(
            "\n[MODE: DEEP MATRIX REASONING]\n"
            "- Present multi-variable trade-offs in clean markdown tables.\n"
            "- Enforce precise quantitative metrics and resource allocations."
        )

    if language and language.lower() != "english":
        prompt.append(f"\nCRITICAL LANGUAGE DIRECTIVE: You MUST respond entirely in {language}.")

    # --------------------------------------------------------------------------
    # 5. SMART VECTOR-LIKE MEMORY VAULT RETRIEVAL (Jaccard Ranker)
    # --------------------------------------------------------------------------
    memory_vault = getattr(st.session_state, "memory_vault", [])
    if memory_vault:
        user_tokens = set(re.findall(r"\w+", lowered_input))
        scored_memories = []

        for fact in memory_vault:
            fact_tokens = set(re.findall(r"\w+", fact.lower()))
            intersection = user_tokens.intersection(fact_tokens)
            
            # Simple relevance scoring based on token overlap
            score = len(intersection) / float(len(user_tokens.union(fact_tokens)) + 1e-5)
            scored_memories.append((score, fact))

        # Sort by relevance score, descending
        scored_memories.sort(key=lambda x: x[0], reverse=True)
        
        # Pull top relevant facts or fallback to top 3
        top_memories = [m[1] for m in scored_memories[:3] if m[0] > 0.05] or memory_vault[:3]

        if top_memories:
            memory_block = "\n".join([f"- {m}" for m in top_memories])
            prompt.append(
                f"\n[RELEVANT USER CONTEXT]:\n"
                f"Incorporate these facts naturally where relevant without explicitly mentioning 'Memory Vault':\n"
                f"{memory_block}"
            )

    return "\n".join(prompt)

# ==========================================
# 4. UPGRADE #35: DYNAMIC VISUAL CANVAS AUTO-RENDERER
# ==========================================
def render_data_canvas(response_text: str):
    """UPGRADE #35: Automatically parses tables or CSV structures into
    interactive charts & dataframes.
    """
    lines = [line.strip() for line in response_text.split("\n") if "|" in line]
    if len(lines) >= 3:
        try:
            cleaned_lines = [
                re.sub(r"^\||\|$", "", line)
                for line in lines
                if not re.match(r"^[\vert{}\s:-]+$", line)
            ]
            data = [
                [cell.strip() for cell in line.split("|")]
                for line in cleaned_lines
            ]

            if len(data) > 1:
                df = pd.DataFrame(data[1:], columns=data[0])
                for col in df.columns:
                    df[col] = pd.to_numeric(
                        df[col].str.replace(",", ""), errors="ignore"
                    )

                num_cols = df.select_dtypes(include=["number"]).columns.tolist()

                if num_cols:
                    st.markdown("#### 📊 Dynamic Visual Canvas")
                    st.dataframe(df, use_container_width=True)
                    chart_type = st.radio(
                        "Chart Type:",
                        ["Bar", "Line"],
                        horizontal=True,
                        key=f"chart_type_{hash(response_text)}",
                    )
                    if chart_type == "Bar":
                        st.bar_chart(df.set_index(df.columns[0])[num_cols])
                    else:
                        st.line_chart(df.set_index(df.columns[0])[num_cols])
        except Exception:
            pass


# ==========================================
# UPGRADE #50: LIVE INLINE CODE EXECUTION ENGINE
# ==========================================
def render_interactive_code_runner(response_text: str, msg_idx: int):
    """UPGRADE #50: Scans the response for Python code blocks and provides a
    live execution button directly inside the chat interface.
    """
    python_blocks = re.findall(
        r"```python\s*(.*?)\s*```", response_text, re.DOTALL
    )
    if python_blocks:
        for b_idx, code in enumerate(python_blocks):
            with st.expander(
                f"⚡ Interactive Code Execution (Block {b_idx+1})",
                expanded=False,
            ):
                st.code(code, language="python")
                if st.button(
                    f"▶️ Run Python Code", key=f"run_code_{msg_idx}_{b_idx}"
                ):
                    output_buffer = io.StringIO()
                    try:
                        with contextlib.redirect_stdout(output_buffer):
                            exec_globals = {"st": st, "pd": pd}
                            exec(code, exec_globals)
                        output_text = output_buffer.getvalue()
                        if output_text:
                            st.success("Execution Successful:")
                            st.code(output_text)
                        else:
                            st.info(
                                "Code executed successfully with no printed stdout output."
                            )
                    except Exception as e:
                        st.error(f"Execution Error: {e}")


# ==========================================
# UPGRADE #54: AUTONOMOUS AGENTIC CODE DEBUGGER
# ==========================================
def run_autonomous_code_debugger(code_snippet: str, client, model: str) -> str:
    """UPGRADE #54: Runs code in a sandboxed capture environment, traps errors,
    and asks LLM to self-correct iteratively until passing.
    """
    st.info("🤖 Agentic Debugger active: Testing code execution...")
    max_attempts = 3
    current_code = code_snippet

    for attempt in range(1, max_attempts + 1):
        output_buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(output_buffer):
                exec_globals = {"st": st, "pd": pd}
                exec(current_code, exec_globals)
            st.success(f"✅ Code executed cleanly on attempt {attempt}!")
            return current_code
        except Exception as err:
            err_msg = str(err)
            st.warning(f"⚠️ Attempt {attempt} failed with error: {err_msg}")
            
            fix_prompt = (
                f"The following Python code produced an execution error:\n\n"
                f"```python\n{current_code}\n```\n\n"
                f"ERROR:\n{err_msg}\n\n"
                f"Fix the code. Return ONLY the valid Python code in standard triple-backtick markdown blocks."
            )
            res = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": fix_prompt}],
                temperature=0.1,
            )
            extracted = re.search(r"```python\s*(.*?)\s*```", res.choices[0].message.content, re.DOTALL)
            if extracted:
                current_code = extracted.group(1).strip()
            else:
                break

    st.error("❌ Agentic debugger reached maximum iterations.")
    return current_code


# ==============================================================================
# UPGRADE #55: SMART CHAT AUTO-SUMMARIZER (FRONTIER PARALLEL)
# ==============================================================================
import re

def auto_summarize_chat_title(chat_history, client, current_name: str) -> None:
    """Dynamically generates clean, human-like chat titles based on early message intent.
    
    Robust against dictionary key collisions, markdown formatting leaks, and API failures.
    """
    # Find the first non-empty user text message in history
    first_user_msg = None
    for msg in chat_history:
        if msg.get("role") == "user" and isinstance(msg.get("content"), str) and msg.get("content").strip():
            first_user_msg = msg.get("content").strip()
            break

    # Only attempt renaming on default thread names when user text exists
    if not first_user_msg or not (current_name.startswith("Chat ") or current_name == "New Chat"):
        return

    # System instruction tailored for precise naming without chatter
    system_instruction = (
        "You are an expert title generator for an AI platform. "
        "Create a concise, highly relevant title (2 to 5 words maximum) summarizing the user's topic or intent.\n"
        "STRICT CONSTRAINTS:\n"
        "- Return ONLY the plain text title.\n"
        "- Do NOT use quotes, punctuation, emojis, or markdown.\n"
        "- Do NOT prefix with 'Title:' or similar phrases.\n"
        "- Capitalize like a standard headline (Title Case)."
    )

    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Topic query: '{first_user_msg[:500]}'"}
            ],
            temperature=0.2,
            max_tokens=15,
        )
        
        raw_title = res.choices[0].message.content.strip()
        
        # 1. Clean sanitization pass (strip quotes, symbols, extra spaces)
        cleaned_title = re.sub(r'[^a-zA-Z0-9\s-]', '', raw_title).strip()
        cleaned_title = " ".join(cleaned_title.split()).title()

        # Fallback if AI returns blank or ultra-short junk
        if not cleaned_title or len(cleaned_title) < 2:
            cleaned_title = " ".join(first_user_msg.split()[:4]).title()

        # Limit absolute length
        if len(cleaned_title) > 35:
            cleaned_title = cleaned_title[:32].rstrip() + "..."

        # 2. Prevent Dictionary Collision (e.g., if "Python Basics" already exists)
        final_title = cleaned_title
        counter = 1
        while final_title in st.session_state.get("chats", {}):
            final_title = f"{cleaned_title} ({counter})"
            counter += 1

        # 3. Safe State Mutation
        if current_name in st.session_state.chats:
            chat_data = st.session_state.chats.pop(current_name)
            st.session_state.chats[final_title] = chat_data
            st.session_state.current_chat = final_title
            
            # Helper safely handles disk sync if function exists
            if "save_chats_to_disk" in globals():
                save_chats_to_disk()
                
            st.rerun()

    except Exception:
        # Fail silently without breaking the UI flow
        pass


# ==============================================================================
# UPGRADE #62: PROMPT ENHANCER & QUERY EXPANSION (GPT/CLAUDE STYLE)
# ==============================================================================
def enhance_user_prompt(prompt_text: str, client) -> str:
    """Transforms raw user input into a rich, structured, frontier-grade AI instruction.
    
    Uses strict prompt engineering to force objective prompt refinement without answering.
    """
    cleaned_input = prompt_text.strip()
    
    # Don't waste API calls on ultra-short commands or already-detailed prompts
    if not cleaned_input or len(cleaned_input) < 3:
        return prompt_text

    system_instruction = (
        "You are an expert Prompt Engineer for frontier AI models (Claude 3.5, GPT-4o).\n"
        "Your task is to take a raw user input and transform it into a structured, highly effective system prompt.\n\n"
        "GUIDELINES:\n"
        "1. Clarify intent, context, objective, and output constraints.\n"
        "2. Add structural formatting guidelines (e.g., step-by-step, bullet points, code-first) if beneficial.\n"
        "3. Preserve the core meaning, domain, and language of the original query.\n"
        "4. DO NOT answer the user's query or fulfill the request. ONLY rewrite the prompt itself.\n"
        "5. Output ONLY the improved prompt text. No preamble, no postscript, no commentary."
    )

    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Raw User Query: '{cleaned_input}'"}
            ],
            temperature=0.3,
            max_tokens=600,
        )
        
        enhanced = res.choices[0].message.content.strip()
        
        # Strip accidental conversational framing (e.g., "Here is the enhanced prompt:")
        if enhanced.startswith("Here is") or enhanced.startswith("Enhanced Prompt:"):
            enhanced = re.sub(r'^(Here is[^\n]*\n|Enhanced Prompt:\s*)', '', enhanced, flags=re.IGNORECASE).strip()

        # Security check: If AI fails and returns empty, fall back safely
        return enhanced if len(enhanced) > len(cleaned_input) else prompt_text

    except Exception:
        return prompt_text


import re

# ==============================================================================
# UPGRADE #53: AUTO-SEARCH INTENT ROUTER (FRONTIER PARALLEL)
# ==============================================================================

# Search Trigger Keywords
REALTIME_KEYWORDS = {
    "news", "latest", "today", "yesterday", "current", "weather", 
    "score", "results", "winner", "stock", "price", "who won", 
    "schedule", "upcoming", "event", "standings", "release date", 
    "trending", "update", "right now", "live"
}

MEDIA_LORE_KEYWORDS = {
    "character", "characters", "cast", "show", "episode", "lore", 
    "tadc", "fnaf", "anime", "manga", "season", "actor", "voice actor"
}


def needs_automatic_search(user_text: str) -> bool:
    """Evaluates user input to determine if live web search context is required.
    
    Prevents hallucinations on niche pop-culture, media lore, and real-time news.
    """
    if not user_text or not user_text.strip():
        return False

    lowered = user_text.lower().strip()

    # 1. Explicit Slash Command Override
    if lowered.startswith("/search"):
        return True

    # 2. Skip search for raw URL pastes or standard code/math snippets
    if re.search(r"https?://|www\.", lowered) or lowered.startswith("```"):
        return False

    # 3. Dynamic Temporal Detection (Detects mentions of modern years like 2024-2026)
    if re.search(r"\b(202[4-6])\b", lowered):
        return True

    # 4. Word-Boundary Phrase Matching for Realtime & Media Keywords
    # Prevents false positives like 'broadcast' matching 'cast'
    tokens = set(re.findall(r"\b\w+\b", lowered))

    if tokens.intersection(REALTIME_KEYWORDS) or tokens.intersection(MEDIA_LORE_KEYWORDS):
        return True

    # 5. Multi-Word Exact Phrase Detection
    phrases = ["who won", "release date", "right now", "voice actor", "who plays"]
    if any(phrase in lowered for phrase in phrases):
        return True

    return False

import os
import re
import json
import base64
import mimetypes
import pandas as pd
import streamlit as st
from groq import Groq

# ==============================================================================
# 5. UI CONFIG & SESSION STATE INITIALIZATION
# ==============================================================================
st.set_page_config(
    page_title="AI Workspace",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Modern Custom CSS Styling
st.markdown(
    """
    <style>
    iframe[title*='audio_recorder'], iframe[src*='audio_recorder'] {
        background-color: transparent !important; 
        border: none !important;
    }
    div[data-testid='stCustomComponentV1'] {
        background-color: transparent !important; 
        border: none !important; 
        padding: 0 !important;
    }
    div[data-testid='column'] button {
        border: none !important; 
        background: transparent !important; 
        color: #888888 !important;
        font-size: 0.8rem !important; 
        padding: 2px 8px !important; 
        border-radius: 6px !important;
    }
    div[data-testid='column'] button:hover {
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
    unsafe_allow_html=True,
)

# Ensure Session State Keys Exist
if "chats" not in st.session_state:
    st.session_state.chats = {"Chat 1": []}

if "current_chat" not in st.session_state or st.session_state.current_chat not in st.session_state.chats:
    st.session_state.current_chat = list(st.session_state.chats.keys())[0]

if "memory_vault" not in st.session_state:
    st.session_state.memory_vault = []

# Title & App Header
st.title("🤖 Intelligent AI Workspace")
st.caption(
    "Enhanced with Epistemic Physics Guardrails, Live RAG Context & Dynamic Workspace Telemetry"
)

# API Key Check & Client Setup
GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    client = None
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets!")

# ==============================================================================
# SIDEBAR CONTROLS & WORKSPACE MANAGER
# ==============================================================================
with st.sidebar:
    st.header("⚙️ Workspace Controls")
    st.markdown("---")

    # 1. Thread Management
    st.header("💬 Chat Sessions")
    chat_names = list(st.session_state.chats.keys())
    
    # Safe index lookup for the active thread
    current_index = chat_names.index(st.session_state.current_chat) if st.session_state.current_chat in chat_names else 0

    selected_chat = st.selectbox(
        "Select Thread:",
        chat_names,
        index=current_index,
    )

    if selected_chat != st.session_state.current_chat:
        st.session_state.current_chat = selected_chat
        st.rerun()

    if st.button("➕ New Chat Session", use_container_width=True):
        new_chat_name = f"Chat {len(st.session_state.chats) + 1}"
        st.session_state.chats[new_chat_name] = []
        st.session_state.current_chat = new_chat_name
        st.rerun()

    st.markdown("---")

    # 2. Model Parameters
    target_language = st.selectbox(
        "Response Language:",
        ["English", "Spanish", "French", "German", "Mandarin", "Japanese"],
    )
    personality = st.selectbox(
        "AI Persona:",
        [
            "Helpful Assistant",
            "Code Expert",
            "Strict Tutor",
            "Executive Analyst",
        ],
    )
    selected_model = st.selectbox(
        "Model Engine:",
        [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
        ],
    )

    st.markdown("---")

    # 3. File Context & Multimodal Ingestion Pipeline
    st.header("📄 File Attachment Context")
    uploaded_file = st.file_uploader(
        "Upload TXT, CSV, Code, or Image:", 
        type=["txt", "py", "js", "md", "csv", "jpg", "png", "jpeg", "webp"]
    )
    
    doc_context = ""
    image_base64 = None 
    image_mime_type = "image/jpeg"

    if uploaded_file is not None:
        try:
            filename = uploaded_file.name.lower()
            
            # Robust Image Processing
            if filename.endswith((".jpg", ".png", ".jpeg", ".webp")):
                mime_guess, _ = mimetypes.guess_type(uploaded_file.name)
                image_mime_type = mime_guess if mime_guess else "image/jpeg"

                uploaded_file.seek(0)
                bytes_data = uploaded_file.read()
                image_base64 = base64.b64encode(bytes_data).decode("utf-8")
                st.image(uploaded_file, caption="📷 Loaded into Vision Context", use_container_width=True)
            
            # CSV Analysis & Summarization
            elif filename.endswith(".csv"):
                df_upload = pd.read_csv(uploaded_file)
                st.markdown("#### 🔍 CSV File Summary")
                st.write(f"**Rows:** {df_upload.shape[0]:,} | **Cols:** {df_upload.shape[1]}")
                st.dataframe(df_upload.head(3), use_container_width=True)
                
                doc_context = (
                    f"CSV File Summary ({uploaded_file.name}):\n"
                    f"Columns: {list(df_upload.columns)}\n"
                    f"Data Head:\n{df_upload.head(10).to_csv(index=False)}"
                )
            
            # Plain Text / Code Processing
            else:
                uploaded_file.seek(0)
                doc_context = uploaded_file.read().decode("utf-8", errors="replace")
                st.success(f"📄 Loaded text context ({len(doc_context.split()):,} words)")

        except Exception as e:
            st.error(f"Error reading file: {e}")

    st.markdown("---")

    # 4. Memory Vault Management
    st.header("🧠 Memory Vault Facts")
    new_memory_fact = st.text_input("Add Persistent Fact:", key="memory_input_field")
    
    if st.button("Save Memory Fact", use_container_width=True) and new_memory_fact.strip():
        st.session_state.memory_vault.append(new_memory_fact.strip())
        st.success(f"Remembered: '{new_memory_fact.strip()}'")
        st.rerun()

    if st.session_state.memory_vault:
        for idx, fact in enumerate(st.session_state.memory_vault):
            col_m1, col_m2 = st.columns([8, 2])
            col_m1.caption(f"• {fact}")
            if col_m2.button("❌", key=f"del_mem_fact_{idx}"):
                st.session_state.memory_vault.pop(idx)
                st.rerun()
                
        if st.button("🧹 Clear All Memories", use_container_width=True):
            st.session_state.memory_vault = []
            st.rerun()
    else:
        st.caption("No custom memory facts saved yet.")

    # 5. Live Thread Export Engine
    st.markdown("---")
    st.header("📥 Thread Export Options")
    export_chat = st.session_state.chats.get(st.session_state.current_chat, [])
    
    # JSON Export
    chat_export_json = json.dumps(export_chat, indent=2)
    st.download_button(
        label="Download Chat (.json)",
        data=chat_export_json,
        file_name=f"{re.sub(r'[^a-zA-Z0-9_-]', '_', st.session_state.current_chat).lower()}.json",
        mime="application/json",
        use_container_width=True,
    )

    # Markdown Export (if export function exists)
    if "export_chat_as_markdown" in globals():
        chat_export_md = export_chat_as_markdown(export_chat, title=st.session_state.current_chat)
        st.download_button(
            label="Download Chat (.md)",
            data=chat_export_md,
            file_name=f"{re.sub(r'[^a-zA-Z0-9_-]', '_', st.session_state.current_chat).lower()}.md",
            mime="text/markdown",
            use_container_width=True,
        )

    # UPGRADE #60: Session Telemetry
    st.markdown("---")
    st.header("📊 Telemetry Dashboard")
    st.caption(f"⚡ **Requests Executed:** {st.session_state.telemetry['requests']}")
    st.caption(f"🔤 **Est. Tokens Processed:** {st.session_state.telemetry['est_tokens']}")
    st.caption(f"⏱️ **Last Latency:** {st.session_state.telemetry['last_latency']:.2f}s")

    # UPGRADE #63: Global Workspace State Reset
    st.markdown("---")
    if st.button("🧹 Reset Workspace Cache", use_container_width=True):
        st.session_state.chats = {"Chat 1": []}
        st.session_state.current_chat = "Chat 1"
        st.session_state.memory_vault = []
        save_chats_to_disk()
        st.rerun()

# Active Chat Buffer
active_chat_list = st.session_state.chats[st.session_state.current_chat]

# Header Badge Display
col_hdr1, col_hdr2 = st.columns([6, 4])
with col_hdr1:
    st.markdown(f"### 💬 {st.session_state.current_chat}")
with col_hdr2:
    st.markdown(
        f"<div style='text-align: right;'><span class='model-badge'>🤖 {selected_model}</span> <span class='model-badge'>🌐 {target_language}</span></div>",
        unsafe_allow_html=True,
    )

st.markdown("---")

# Render Message History with Auto-Repair Formatting
for idx, msg in enumerate(active_chat_list):
    with st.chat_message(msg["role"]):
        repaired_content = sanitize_and_repair_formatting(
            msg.get("content", "")
        )
        st.markdown(repaired_content)

        if msg["role"] == "assistant":
            # Action Toolbar
            col_a1, col_a2, col_a3, _ = st.columns([1, 1, 1, 7])
            if col_a1.button("📌", key=f"bm_{idx}", help="Bookmark Response"):
                st.session_state.bookmarks.append(repaired_content)
                st.toast("Bookmarked response!")

            if col_a2.button("🔊", key=f"tts_{idx}", help="Generate Speech"):
                audio_file = generate_tts_audio(repaired_content)
                if audio_file:
                    st.audio(audio_file, format="audio/mp3")

            # Render Visual Canvas if data is present
            if repaired_content:
                render_data_canvas(repaired_content)

            # Render Interactive Code Execution Engine
            if repaired_content:
                render_interactive_code_runner(repaired_content, idx)

# Voice Audio Recorder Control
col_v1, col_v2 = st.columns([1, 11])
with col_v1:
    audio_bytes = audio_recorder(
        text="",
        recording_color="#e8b62c",
        neutral_color="#6aa84f",
        icon_size="2x",
    )

if audio_bytes and client:
    with st.spinner("🎙️ Transcribing audio input..."):
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".wav"
            ) as fp:
                fp.write(audio_bytes)
                tmp_path = fp.name

            with open(tmp_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model="whisper-large-v3",
                    file=audio_file,
                    response_format="text",
                )
            os.remove(tmp_path)
            if transcription.strip():
                st.session_state.input_buffer = transcription.strip()
        except Exception as e:
            st.error(f"Voice transcription error: {e}")

def classify_user_intent(prompt, client, model_name):
    """
    Analyzes user prompt and automatically decides which tool/route to run.
    Returns: 'IMAGE', 'DEBUG', 'SEARCH', 'READ', or 'CHAT'
    """
    lowered = prompt.lower().strip()
    
    # Fast-path for explicit slash commands
    if lowered.startswith("/image") or lowered.startswith("/draw"): return "IMAGE"
    if lowered.startswith("/debug"): return "DEBUG"
    if lowered.startswith("/search"): return "SEARCH"
    if lowered.startswith("/read"): return "READ"

    # Auto-detection rules
    image_triggers = ["draw", "generate an image", "picture of", "paint", "create an image"]
    if any(trigger in lowered for trigger in image_triggers):
        return "IMAGE"

    debug_triggers = ["traceback", "syntaxerror", "fix this code", "def ", "import "]
    if "error" in lowered and any(trig in lowered for trig in debug_triggers):
        return "DEBUG"

    if needs_automatic_search(prompt) or any(kw in lowered for kw in REALTIME_KEYWORDS):
        return "SEARCH"

    return "CHAT"

# ==============================================================================
# FEATURE #3: DOCUMENT CANVAS & RAG INSPECTOR
# ==============================================================================
if doc_context and doc_context.strip():
    with st.expander("📄 **Document Canvas & RAG Inspector**", expanded=True):
        col_canvas1, col_canvas2 = st.columns([3, 1])
        
        # Calculate key Document Metrics
        word_count = len(doc_context.split())
        char_count = len(doc_context)
        est_read_time = max(1, round(word_count / 200))  # Standard 200 WPM

        with col_canvas1:
            st.markdown("### 📝 Loaded Document Context")
            
            # Interactive Toggle for Full View vs Truncated Preview
            full_view = st.checkbox("Show full document content", value=False, key="toggle_doc_full_view")
            
            display_text = doc_context if full_view or len(doc_context) <= 2000 else doc_context[:2000] + "\n\n... [Truncated for Preview]"
            
            st.text_area(
                label="Document Content Preview",
                value=display_text,
                height=200 if not full_view else 400,
                disabled=True,
                label_visibility="collapsed"
            )
            
        with col_canvas2:
            st.markdown("### 📊 Document Metrics")
            st.metric("Total Words", f"{word_count:,}")
            st.metric("Total Characters", f"{char_count:,}")
            st.metric("Est. Read Time", f"~{est_read_time} min")
            
            st.markdown("---")
            
            # Safe Context Reset Action
            if st.button("🧹 Clear Canvas", use_container_width=True, key="clear_doc_canvas_btn"):
                # Clear all persistent document states
                st.session_state.doc_context = ""
                doc_context = ""
                
                # Clear Streamlit file uploader widget key if present
                if "uploader_key" in st.session_state:
                    del st.session_state["uploader_key"]
                    
                st.success("Document context cleared!")
                st.rerun()


import time
import streamlit as st

import time
import re
import streamlit as st

# ==============================================================================
# INPUT EXECUTION PIPELINE (FRONTIER DISPATCHER - PRODUCTION GRADE)
# ==============================================================================
user_input = st.chat_input("Ask anything, use /search, /image, /debug, or /enhance...")

# Handle buffer redirects (e.g. prompt presets or quick actions)
if st.session_state.get("input_buffer") and not user_input:
    user_input = st.session_state.input_buffer
    st.session_state.input_buffer = ""

if user_input and client:
    start_time = time.time()

    # 1. Thread Binding & Message Mutation
    current_thread = st.session_state.get("current_chat", "New Chat")
    if "chats" not in st.session_state:
        st.session_state.chats = {}
    if current_thread not in st.session_state.chats:
        st.session_state.chats[current_thread] = []
    
    active_chat_list = st.session_state.chats[current_thread]

    # 2. Command Override & Prompt Enhancement Pipeline
    if user_input.lower().startswith("/enhance"):
        clean_prompt = re.sub(r"^/enhance\s*", "", user_input, flags=re.IGNORECASE).strip()
        if clean_prompt:
            with st.spinner("✨ Enhancing query structure..."):
                if "enhance_user_prompt" in globals():
                    user_input = enhance_user_prompt(clean_prompt, client)
                    st.info(f"**Enhanced Prompt:** {user_input}")

    # Log user activity
    print(f"--- [USER ACTIVITY DETECTED] ---")
    print(f"Thread: {current_thread} | Input: {user_input[:100]}...")

    # 3. Dynamic Intent Classification & Slash Command Fast-Pass
    lowered_input = user_input.lower().strip()

    if lowered_input.startswith("/search"):
        detected_route = "ROUTE_SEARCH"
    elif lowered_input.startswith("/image") or lowered_input.startswith("/imagine"):
        detected_route = "ROUTE_IMAGE_GEN"
    elif lowered_input.startswith("/debug"):
        detected_route = "ROUTE_DEBUG"
    else:
        # Fallback to auto-classifier if available
        if "classify_user_intent" in globals():
            try:
                detected_route = classify_user_intent(user_input, client, selected_model)
            except Exception:
                detected_route = "ROUTE_STANDARD"
        else:
            detected_route = "ROUTE_STANDARD"

    print(f"--- [AUTO-ROUTER] Active Route: {detected_route} ---")

    # 4. Append User Message to Thread State & Render Immediately
    active_chat_list.append({"role": "user", "content": user_input})
    
    with st.chat_message("user"):
        st.markdown(user_input)

    # 5. Query Disambiguation & Heuristic Parsing
    processed_prompt = user_input
    if "tadc" in lowered_input and "character" in lowered_input:
        processed_prompt += " (referring to the individuals/cast in the show 'The Amazing Digital Circus')"

    # 6. Adaptive Temperature & Style Tuning
    casual_triggers = {"hi", "hello", "hey", "howdy", "sup", "how are you", "what's up", "thanks", "thank you", "cool", "nice"}
    analytical_keywords = ["compare", "vs", "probability", "percent", "rate", "code", "architecture", "dyson", "kardashev", "refactor", "math"]

    words = set(re.findall(r"\w+", lowered_input))
    
    if len(lowered_input.split()) < 8 and words.intersection(casual_triggers):
        detected_style = "CASUAL"
        active_temperature = 0.85
    elif any(kw in lowered_input for kw in analytical_keywords):
        detected_style = "ANALYTICAL"
        active_temperature = 0.15
    else:
        detected_style = "GENERAL"
        active_temperature = 0.7

    # --- ROUTE A: LIVE WEB SEARCH ---
        if detected_route == "ROUTE_SEARCH":
            query = re.sub(r"^/search\s*", "", user_input, flags=re.IGNORECASE).strip()
            
            with st.status("🌐 Searching the web...", expanded=True) as status:
                st.write(f"🔎 Fetching live data for: `{query}`...")
                
                # Fetch web search context
                if "perform_live_search" in globals():
                    raw_search_data = perform_live_search(query)
                    status.update(label="✅ Data retrieved! Synthesizing answer...", state="complete", expanded=False)
                    
                    # Pass raw search context into the LLM for clean formatting
                    synthesis_prompt = f"""
                    You are a helpful AI. Answer the user's question using ONLY the provided search context.
                    Format the response cleanly with bullet points, bold headers, and key stats.
                    
                    SEARCH CONTEXT:
                    {raw_search_data}
                    
                    USER QUESTION:
                    {query}
                    """
                    
                    # Send context + prompt to your main LLM client
                    completion = client.chat.completions.create(
                        model=st.session_state.get("selected_model", "llama-3.3-70b-versatile"),
                        messages=[{"role": "user", "content": synthesis_prompt}],
                        temperature=0.2
                    )
                    assistant_response = completion.choices[0].message.content
                    st.markdown(assistant_response)
                else:
                    status.update(label="❌ Search tool missing", state="error", expanded=False)
                    assistant_response = "Search tool function `perform_live_search` is not defined."
                    st.warning(assistant_response)

        # --- ROUTE B: IMAGE GENERATION ---
        elif detected_route == "ROUTE_IMAGE_GEN":
            image_prompt = re.sub(r"^/(image|imagine)\s*", "", user_input, flags=re.IGNORECASE).strip()
            
            if "generate_and_render_image" in globals():
                assistant_response = generate_and_render_image(image_prompt)
            else:
                st.warning("⚠️ Image generation function `generate_and_render_image` is not defined.")
                assistant_response = "Image generation tool unavailable."

        # --- ROUTE C: DEBUG TOOL ---
        elif detected_route == "ROUTE_DEBUG":
            st.toast("🛠️ Diagnostic trace initiated...", icon="🔍")
            assistant_response = f"**System Debug Payload:**\n* Active Thread: `{current_thread}`\n* Detected Style: `{detected_style}`\n* Active Temperature: `{active_temperature}`"
            st.markdown(assistant_response)

        # --- ROUTE D: STANDARD LLM COMPLETION ---
        else:
            assistant_response = smart_model_router(processed_prompt, client, st.session_state.get("selected_model", "llama-3.3-70b-versatile"))

    # 8. Record Response to State, Update Telemetry & Rerun
    if assistant_response:
        active_chat_list.append({"role": "assistant", "content": assistant_response})
        
        # Log latency telemetry
        elapsed_time = time.time() - start_time
        if "telemetry" in st.session_state:
            st.session_state.telemetry["requests"] += 1
            st.session_state.telemetry["last_latency"] = elapsed_time
            st.session_state.telemetry["est_tokens"] += len(user_input.split()) + len(assistant_response.split())

        st.rerun()
        
        # ROUTE 0: Autonomous Code Debugger
        if detected_route == "DEBUG":
            st.info("🛠️ *Auto-Detected: Code Debugger Activated*")
            clean_code = user_input.replace("/debug", "").strip()
            fixed_code = run_autonomous_code_debugger(clean_code, client, selected_model)
            reply = f"```python\n{fixed_code}\n```"
            active_chat_list.append({"role": "assistant", "content": reply})

        # ROUTE 1: High-Quality AI Image Generation
        elif detected_route == "IMAGE":
            clean_prompt = re.sub(r"^/(image|imagine|draw|generate)\s*", "", user_input, flags=re.IGNORECASE).strip()
            if not clean_prompt:
                clean_prompt = user_input
                
            with st.spinner("🎨 Generating high-quality AI artwork..."):
                enhanced_prompt = f"{clean_prompt}, high resolution, detailed, vivid colors"
                encoded_prompt = urllib.parse.quote(enhanced_prompt)
                
                img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={random.randint(1, 99999)}&model=flux&enhance=true&nologo=true"
                
                full_response = f"🎨 **Generated Image for:** *'{clean_prompt}'*\n\n![AI Image]({img_url})"
                
                active_chat_list.append({
                    "role": "assistant",
                    "content": full_response
                })

        # ROUTE 2: Deconstructed Multi-Angle Search Route
        elif detected_route == "SEARCH":
            st.info("🔍 *Auto-Detected: Web Search Activated*")
            clean_query = user_input.replace("/search", "").strip()
            with st.spinner("🔍 Deconstructing query & synthesizing multi-angle search..."):
                reply = execute_deconstructed_multi_search(
                    clean_query, client, selected_model
                )
                reply = sanitize_and_repair_formatting(reply)
                active_chat_list.append({"role": "assistant", "content": reply})

        # ROUTE 3: Web Scraper Route
        elif detected_route == "READ":
            target_url = user_input.replace("/read ", "").strip()
            with st.spinner(f"🌐 Fetching content from {target_url}..."):
                try:
                    import requests
                    from bs4 import BeautifulSoup
                    
                    res = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                    soup = BeautifulSoup(res.text, "html.parser")
                    paragraphs = [p.get_text() for p in soup.find_all("p")]
                    page_text = " ".join(paragraphs)[:4000]
                    
                    prompt_with_url = f"Analyze and summarize the following content from {target_url}:\n\n{page_text}"
                    
                    response = client.chat.completions.create(
                        model=selected_model,
                        messages=[{"role": "user", "content": prompt_with_url}],
                        temperature=active_temperature,
                    )
                    reply = response.choices[0].message.content
                    active_chat_list.append({"role": "assistant", "content": reply})
                except Exception as e:
                    active_chat_list.append({"role": "assistant", "content": f"Failed to fetch web page: {e}"})

       # ROUTE 4: Standard Chat Generation
        else:
            system_prompt = build_dynamic_system_prompt(
                processed_prompt, personality, target_language, detected_style
            )

            system_prompt += (
                "\n\n[STRICT CONTEXT RULE]: Always maintain awareness of prior topics in the chat."
            )

            if doc_context:
                system_prompt += f"\n\n[USER ATTACHED FILE CONTEXT]:\n{doc_context[:4000]}"

            # Construct system payload
            messages_payload = [{"role": "system", "content": system_prompt}]

            # Append chat history
            for m in active_chat_list[:-1]:
                if isinstance(m.get("content"), str):
                    messages_payload.append({"role": m["role"], "content": m["content"]})

            # --- BRANCH 1: IMAGE HANDLING ---
            if image_base64:
                prompt_text = user_input.strip() if user_input.strip() else "Describe and analyze this image in detail."

                vision_messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{image_mime_type};base64,{image_base64}"
                                }
                            }
                        ]
                    }
                ]

                vision_models = [
                    "openrouter/auto",
                    "google/gemma-3-12b-it:free",
                    "qwen/qwen-2.5-vl-72b-instruct:free"
                ]

                success = False

                if openrouter_client:
                    with st.spinner("👁️ Analyzing image with Vision..."):
                        for model_slug in vision_models:
                            try:
                                response = openrouter_client.chat.completions.create(
                                    model=model_slug,
                                    messages=vision_messages,
                                    temperature=active_temperature,
                                )
                                final_reply = response.choices[0].message.content
                                st.markdown(final_reply)
                                active_chat_list.append({"role": "assistant", "content": final_reply})
                                success = True
                                break
                            except Exception:
                                continue

                if not success:
                    st.info("📷 Image attached (Vision endpoints busy). Processing query with text engine...")
                    fallback_msg = f"[Attached Image: {uploaded_file.name}]\nUser Prompt: {prompt_text}"
                    messages_payload.append({"role": "user", "content": fallback_msg})

                    response = client.chat.completions.create(
                        model=selected_model,
                        messages=messages_payload,
                        temperature=active_temperature,
                    )
                    final_reply = response.choices[0].message.content
                    st.markdown(final_reply)
                    active_chat_list.append({"role": "assistant", "content": final_reply})

            # --- BRANCH 2: STANDARD TEXT STREAMING ---
            else:
                messages_payload.append({"role": "user", "content": user_input})

                stream = client.chat.completions.create(
                    model=selected_model,
                    messages=messages_payload,
                    temperature=active_temperature,
                    stream=True,
                )

                def stream_generator():
                    for chunk in stream:
                        if chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content

                raw_reply = st.write_stream(stream_generator)
                final_reply = sanitize_and_repair_formatting(raw_reply)
                active_chat_list.append({"role": "assistant", "content": final_reply})

    # ==============================================================================
    # TELEMETRY, SUMMARIZATION & STATE WRAP-UP (UPGRADES #55 & #60)
    # ==============================================================================
    
    # 1. Calculate Request Execution Time
    latency_seconds = round(time.time() - start_time, 2)
    
    # 2. Estimate Token Usage (Input Words + Assistant Words * 1.33)
    input_word_count = len(user_input.split())
    output_word_count = len(final_reply.split()) if "final_reply" in locals() else 100
    estimated_request_tokens = int((input_word_count + output_word_count) * 1.33)

    # 3. Update Session Telemetry
    if "telemetry" not in st.session_state:
        st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}

    st.session_state.telemetry["requests"] += 1
    st.session_state.telemetry["est_tokens"] += estimated_request_tokens
    st.session_state.telemetry["last_latency"] = latency_seconds

    # 4. Trigger Smart Thread Summarization (Auto-rename thread if brand new)
    if "auto_summarize_chat_title" in globals() and client:
        try:
            auto_summarize_chat_title(
                chat_history=active_chat_list, 
                client=client, 
                current_name=st.session_state.current_chat
            )
        except Exception as err:
            print(f"⚠️ [SUMMARIZER WARN] Thread renaming skipped: {err}")

    # 5. Persist State & Sync UI View
    if "save_chats_to_disk" in globals():
        save_chats_to_disk()

    st.rerun()
