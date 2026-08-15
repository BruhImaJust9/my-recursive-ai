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
# 0. PERSISTENCE & HELPERS (TRIPLE-GUARDED DISK ENGINE)
# ==============================================================================
CHAT_STORAGE_FILE = "persistent_chats.json"

def load_saved_chats() -> dict:
    """Loads saved chat threads with structural validation and schema repair."""
    if not os.path.exists(CHAT_STORAGE_FILE):
        return {"New Chat": []}
        
    try:
        # Guard 1: Verify non-empty file size before attempting disk read
        if os.path.getsize(CHAT_STORAGE_FILE) == 0:
            return {"New Chat": []}

        with open(CHAT_STORAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            # Guard 2: Schema type check (Must be a dict containing lists)
            if isinstance(data, dict) and len(data) > 0:
                validated_data = {}
                for chat_title, messages in data.items():
                    if isinstance(messages, list):
                        validated_data[chat_title] = messages
                return validated_data if validated_data else {"New Chat": []}

    except json.JSONDecodeError as err:
        # Guard 3: Corrupt JSON recovery — auto-backup corrupt file instead of crashing
        backup_path = f"{CHAT_STORAGE_FILE}.corrupt.{int(time.time())}"
        if os.path.exists(CHAT_STORAGE_FILE):
            os.rename(CHAT_STORAGE_FILE, backup_path)
        print(f"⚠️ [PERSISTENCE WARN] Corrupt JSON backed up to {backup_path}: {err}")
    except Exception as err:
        print(f"⚠️ [PERSISTENCE WARN] Failed to load chat history: {err}")
        
    return {"New Chat": []}


def save_chats_to_disk() -> None:
    """Atomically serializes chat history with payload sanitization."""
    try:
        # Guard 1: Session state existence check
        if "chats" not in st.session_state:
            return

        # Guard 2: Authorization gate
        if not st.session_state.get("is_logged_in", False):
            return

        clean_chats = {}
        for session_name, msg_list in st.session_state.chats.items():
            if not isinstance(msg_list, list):
                continue
                
            clean_chats[session_name] = []
            for msg in msg_list:
                if not isinstance(msg, dict):
                    continue
                
                # Exclude binary data, base64 payloads, and large non-serializable objects
                clean_msg = {
                    k: v for k, v in msg.items()
                    if k not in ["audio", "image_url", "bytes", "raw_response"]
                    and isinstance(v, (str, int, float, bool, list, dict))
                }
                clean_chats[session_name].append(clean_msg)

        # Guard 3: Safe Atomic File Write (Write -> Flush -> Sync -> Replace)
        dir_name = os.path.dirname(CHAT_STORAGE_FILE) or "."
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(clean_chats, tf, indent=2, ensure_ascii=False)
            tf.flush()
            os.fsync(tf.fileno())  # Force buffer flushing directly to disk hardware
            temp_path = tf.name

        os.replace(temp_path, CHAT_STORAGE_FILE)

    except Exception as err:
        print(f"⚠️ [PERSISTENCE ERROR] Atomic write failure: {err}")


# ==============================================================================
# 1. SESSION STATE INITIALIZATION (SAFE DEFAULT HOOKS)
# ==============================================================================
if "is_logged_in" not in st.session_state:
    st.session_state.is_logged_in = False  # Default Guest Mode

if "chats" not in st.session_state:
    if st.session_state.is_logged_in:
        st.session_state.chats = load_saved_chats()
    else:
        st.session_state.chats = {"New Chat": []}

# Guard: Validate current chat selection against available threads
if "current_chat" not in st.session_state or st.session_state.current_chat not in st.session_state.chats:
    chat_keys = list(st.session_state.chats.keys())
    st.session_state.current_chat = chat_keys[0] if chat_keys else "New Chat"
    if st.session_state.current_chat not in st.session_state.chats:
        st.session_state.chats[st.session_state.current_chat] = []

if "input_buffer" not in st.session_state:
    st.session_state.input_buffer = ""

if "memory_vault" not in st.session_state:
    st.session_state.memory_vault = []

if "bookmarks" not in st.session_state:
    st.session_state.bookmarks = []

if "telemetry" not in st.session_state:
    st.session_state.telemetry = {
        "requests": 0, 
        "est_tokens": 0, 
        "last_latency": 0.0
    }


# ==============================================================================
# 2. CLIENT & API INITIALIZATIONS (FAULT-TOLERANT INSTANTIATION)
# ==============================================================================
# Guard 1: Double-layer API Key lookup (Secrets -> Environment Variable)
OPENROUTER_KEY = st.secrets.get("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))

openrouter_client = None
if OPENROUTER_KEY:
    try:
        # Guard 2: Instantiate client safely with explicit request timeouts
        openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_KEY,
            timeout=30.0,
            max_retries=2
        )
    except Exception as err:
        print(f"⚠️ [CLIENT ERROR] OpenRouter initialization failed: {err}")


# ==============================================================================
# 3. LATEX & FORMATTING AUTO-REPAIR ENGINE
# ==============================================================================
def sanitize_and_repair_formatting(text: str) -> str:
    """Fixes LaTeX math syntax, markdown lists, and strips disclaimers."""
    if not text or not isinstance(text, str):
        return ""

    try:
        # Guard 1: Convert block LaTeX \[ ... \] to display math $$ ... $$
        text = re.sub(r"\\\[\s*([\s\S]*?)\s*\\\]", r"$$\1$$", text)

        # Guard 2: Convert inline LaTeX \( ... \) to inline math $ ... $
        text = re.sub(r"\\\(\s*([\s\S]*?)\s*\\\)", r"$\1$", text)

        # Guard 3: Fix mangled markdown bullet list spacing
        text = re.sub(r"([^\n])\n?(\s*[*|-]\s+[A-Za-z0-9])", r"\1\n\2", text)
        text = re.sub(r"([^\n])\n?(\s*\d+\.\s+[A-Za-z0-9])", r"\1\n\2", text)

        # Clean search disclaimers and multi-line gaps
        search_disclaimers = [
            r"The provided search results do not directly address.*?\n",
            r"Based on the search results provided.*?\n",
            r"According to the retrieved sources.*?\n"
        ]
        for pattern in search_disclaimers:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
        
    except Exception as err:
        print(f"⚠️ [FORMATTING ERROR] Failed to clean string: {err}")
        return text

import io
import json
import pandas as pd

def extract_text_from_upload(uploaded_file, max_char_limit: int = 50000) -> str:
    """
    Parses uploaded files across multiple MIME types safely, returning clean plain text.
    """
    if uploaded_file is None:
        return ""
    
    file_type = uploaded_file.type
    file_name = uploaded_file.name.lower()
    
    try:
        if file_name.endswith(".csv") or "csv" in file_type:
            df = pd.read_csv(uploaded_file)
            return f"--- CSV Content ({file_name}) ---\n" + df.to_markdown(index=False)
            
        elif file_name.endswith(".json") or "json" in file_type:
            content = json.load(uploaded_file)
            return f"--- JSON Content ({file_name}) ---\n" + json.dumps(content, indent=2)
            
        elif file_name.endswith(".txt") or file_name.endswith(".md"):
            raw_bytes = uploaded_file.read()
            text = raw_bytes.decode("utf-8", errors="replace")
            return text[:max_char_limit]
            
        elif file_name.endswith(".pdf"):
            # Requires pypdf
            try:
                from pypdf import PdfReader
                reader = PdfReader(uploaded_file)
                extracted_pages = [page.extract_text() for page in reader.pages if page.extract_text()]
                full_text = "\n\n".join(extracted_pages)
                return full_text[:max_char_limit]
            except ImportError:
                return "⚠️ PDF parsing requires `pypdf` package installed."
                
        else:
            # General fallback decode
            raw_bytes = uploaded_file.read()
            return raw_bytes.decode("utf-8", errors="replace")[:max_char_limit]

    except Exception as e:
        return f"⚠️ Error parsing file '{file_name}': {str(e)}"

def build_reasoning_prompt(user_query: str, system_context: str = "") -> str:
    """
    Forces the model to execute explicit step-by-step reasoning 
    and self-verification before producing the final response.
    """
    reasoning_instructions = """
You are an expert analytical assistant. Before providing your final response, follow these strict internal reasoning steps:

1. **Deconstruct**: Identify the core components, constraints, and implicit goals of the user's request.
2. **Draft Logic**: Outline the logical steps or architectural design needed to answer thoroughly.
3. **Self-Correction & Edge Cases**: Review your draft logic. Identify potential flaws, edge cases, missing context, or logic traps. Refine your plan.
4. **Final Synthesis**: Provide the clear, accurate, and structured final answer.

Structure your response clearly, showing your reasoning process where helpful.
"""
    return f"{reasoning_instructions}\n\nContext:\n{system_context}\n\nUser Query: {user_query}"

def re_rank_context(query: str, retrieved_docs: list[str], top_k: int = 3) -> list[str]:
    """
    Ranks retrieved context chunks by exact relevance to the query.
    (Requires `sentence_transformers` or a re-ranking API like Cohere).
    """
    try:
        from sentence_transformers import CrossEncoder
        # Load a lightweight, high-accuracy cross-encoder model
        model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
        
        # Pair query with each document snippet
        pairs = [[query, doc] for doc in retrieved_docs]
        scores = model.predict(pairs)
        
        # Sort documents by score descending
        doc_score_pairs = sorted(zip(retrieved_docs, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, score in doc_score_pairs[:top_k]]
    except Exception:
        # Fallback to returning original top_k if cross-encoder is unavailable
        return retrieved_docs[:top_k]

def solve_with_self_consistency(client, model: str, query: str, num_samples: int = 3) -> str:
    """
    Generates multiple reasoning paths and asks the model to evaluate 
    and synthesize the most robust solution.
    """
    samples = []
    
    # Step 1: Generate multiple reasoning trajectories
    for i in range(num_samples):
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Solve this problem step-by-step. Show all work."},
                {"role": "user", "content": query}
            ],
            temperature=0.6,  # Allow slight variance in solution paths
        )
        samples.append(response.choices[0].message.content)
    
    # Step 2: Synthesize the solutions into one authoritative answer
    synthesis_prompt = f"""
Below are {num_samples} different solutions to the same problem:

{chr(10).join([f"--- Solution {idx+1} ---\n{s}" for idx, s in enumerate(samples)])}

Review all solutions above. Identify any logical errors or discrepancies, extract the correct elements, and produce the single best, fully verified final solution.
"""
    
    final_completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": synthesis_prompt}],
        temperature=0.1,  # Low temperature for precise synthesis
    )
    
    return final_completion.choices[0].message.content

# ==============================================================================
# 4. LIVE SEARCH INTEGRATION ENGINE
# ==============================================================================
def perform_live_search(query: str) -> str:
    """Queries Tavily Search API with fallback web query formatting."""
    # Guard 1: Empty input check
    clean_query = query.strip() if query else ""
    if not clean_query:
        return "Search query was empty."

    tavily_key = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))
    
    # Guard 2: Fallback if API Key is completely missing
    if not tavily_key:
        st.warning("⚠️ Tavily API Key not found. Falling back to synthetic search mode.")
        return f"Simulated context for: '{clean_query}'. Please configure TAVILY_API_KEY in secrets."

    api_url = "https://api.tavily.com/search"
    payload = {
        "query": clean_query, 
        "api_key": tavily_key,
        "search_depth": "basic",
        "max_results": 3
    }
    
    try:
        # Guard 3: Hard Timeout to keep UI responsive
        response = requests.post(api_url, json=payload, timeout=8)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            if not results:
                return "No matching live web results found."
                
            formatted = []
            for idx, r in enumerate(results[:3], 1):
                title = r.get("title", "Untitled Source")
                content = r.get("content", "No description available.")
                formatted.append(f"{idx}. **{title}**: {content}")
            return "\n\n".join(formatted)
        else:
            return f"Search API returned error HTTP {response.status_code}."

    except requests.exceptions.Timeout:
        return "Search service timed out (8s limit exceeded)."
    except Exception as e:
        return f"Search execution error: {str(e)}"

import time
import functools
import logging

def retry_with_exponential_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    allowed_exceptions: tuple = (Exception,)
):
    """
    Decorator for retrying unstable network calls using exponential backoff with jitter.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except allowed_exceptions as err:
                    if attempt == max_retries:
                        logging.error(f"Function {func.__name__} failed after {max_retries} attempts. Error: {err}")
                        raise err
                    
                    sleep_time = delay + (random.uniform(0, 0.5) * delay)  # Add jitter
                    logging.warning(f"Attempt {attempt} failed for {func.__name__}. Retrying in {sleep_time:.2f}s... Error: {err}")
                    time.sleep(sleep_time)
                    delay *= backoff_factor
        return wrapper
    return decorator

# Usage Example:
# @retry_with_exponential_backoff(max_retries=3, initial_delay=1.5)
# def call_llm_api(messages): ...


# ==============================================================================
# 5. SMART MODEL ROUTER & CONVERSATION MEMORY ENGINE
# ==============================================================================
def smart_model_router(prompt: str, client, preferred_model: str = "llama-3.3-70b-versatile", conversation_history: list = None) -> str:
    """Routes prompts dynamically based on complexity and sends context history."""
    if conversation_history is None:
        conversation_history = []

    # Guard 1: Client availability check
    if not client:
        return "❌ Error: OpenAI/OpenRouter client is not initialized. Please check API keys."

    # Guard 2: Format conversation history into API schema
    formatted_messages = []
    for msg in conversation_history:
        if isinstance(msg, dict) and "role" in msg and "content" in msg:
            formatted_messages.append({"role": msg["role"], "content": str(msg["content"])})

    # Append current input
    formatted_messages.append({"role": "user", "content": prompt})

    # Guard 3: Complexity Analysis Heuristic
    prompt_len = len(prompt.split())
    is_complex = any(kw in prompt.lower() for kw in [
        "code", "refactor", "analyze", "explain in detail", "architecture", "compare", "math"
    ]) or prompt_len > 120

    if is_complex:
        primary_model = preferred_model
        backup_model = "gpt-4o-mini"
    else:
        primary_model = "llama-3.3-70b-versatile"
        backup_model = preferred_model

    def attempt_completion(model_name: str):
        return client.chat.completions.create(
            model=model_name,
            messages=formatted_messages,  # 👈 Keeps conversation context!
            temperature=0.7,
            stream=True
        )

    response_container = st.empty()
    full_response = ""

    # Execution with Failover
    try:
        stream = attempt_completion(primary_model)
        st.caption(f"⚡ Model Tier: `{primary_model}`")
    except Exception as primary_error:
        st.warning(f"⚠️ `{primary_model}` unavailable. Failing over to `{backup_model}`...")
        try:
            stream = attempt_completion(backup_model)
            st.caption(f"🛡️ Backup Model Tier: `{backup_model}`")
        except Exception as fallback_error:
            st.error("❌ All model endpoints are currently unreachable.")
            return f"Execution Error: {str(fallback_error)}"

    # Stream output to UI safely
    try:
        for chunk in stream:
            if chunk.choices and len(chunk.choices) > 0:
                content = chunk.choices[0].delta.content or ""
                full_response += content
                response_container.markdown(full_response + "▌")
        
        response_container.markdown(full_response)
        return full_response
    except Exception as stream_err:
        st.error(f"⚠️ Stream disrupted: {stream_err}")
        return full_response if full_response else "Stream rendering error."

import re

def sanitize_sensitive_data(text: str) -> str:
    """
    Scubs potential PII and secret patterns from text before sending to LLM endpoints.
    """
    if not isinstance(text, str) or not text.strip():
        return text

    # Redact Emails
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[REDACTED_EMAIL]', text)
    
    # Redact IPv4 Addresses
    text = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[REDACTED_IP]', text)
    
    # Redact API Key Patterns (e.g. sk-..., gsk_..., or standard hex/base64 patterns)
    text = re.sub(r'\b(sk-[a-zA-Z0-9]{20,}|gsk_[a-zA-Z0-9]{20,})\b', '[REDACTED_API_KEY]', text)
    
    # Redact US Phone Numbers
    text = re.sub(r'\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b', '[REDACTED_PHONE]', text)

    return text

def enforce_context_window(
    messages: list[dict], 
    max_token_budget: int = 4090, 
    char_per_token_ratio: float = 4.0
) -> list[dict]:
    """
    Prunes older chat messages (preserving system prompt and latest prompt) 
    to fit within defined token budgets.
    """
    if not messages:
        return []

    # Preserve System Message if present
    system_msg = [m for m in messages if m.get("role") == "system"]
    conversation_msgs = [m for m in messages if m.get("role") != "system"]

    def estimate_tokens(msg_list):
        total_chars = sum(len(str(m.get("content", ""))) for m in msg_list)
        return int(total_chars / char_per_token_ratio)

    # Prune from oldest conversation messages until within budget
    while conversation_msgs and (estimate_tokens(system_msg + conversation_msgs) > max_token_budget):
        if len(conversation_msgs) <= 1:
            break  # Always keep the latest user input
        conversation_msgs.pop(0)

    return system_msg + conversation_msgs


# ==============================================================================
# 6. IMAGE GENERATION ENGINE (POLLINATIONS & BROWSING SURVIVABILITY)
# ==============================================================================
def generate_and_render_image(prompt: str) -> str:
    """Renders generated images with timeout protection and direct UI fallback."""
    error_message = None
    
    # Guard 1: Prompt Validation
    clean_prompt = prompt.strip() if prompt else "abstract digital artwork"

    with st.status("🎨 Generating image...", expanded=True) as status:
        try:
            encoded_prompt = urllib.parse.quote(clean_prompt)
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
            
            # Guard 2: Browser Headers & Extended 60s Timeout
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            response = requests.get(image_url, headers=headers, timeout=60)
            
            # Guard 3: Verification of Status Code
            if response.status_code == 200:
                st.image(response.content, caption=f"Generated: {clean_prompt}", use_container_width=True)
                status.update(label="✨ Image rendered successfully!", state="complete", expanded=False)
                return f"![Generated Image]({image_url})"
            else:
                error_message = f"HTTP {response.status_code}: API endpoint refused request."
                status.update(label="❌ Generation failed", state="error", expanded=True)

        except requests.exceptions.Timeout:
            error_message = "ReadTimeout: Image generation took longer than 60 seconds."
            status.update(label="❌ Generation timed out", state="error", expanded=True)
        except Exception as e:
            error_message = f"Exception caught: {type(e).__name__} - {str(e)}"
            status.update(label="❌ Generation failed", state="error", expanded=True)

    if error_message:
        st.error(f"🚨 **Image Error Details:** {error_message}")
        return f"Image generation failed: {error_message}"


# ==============================================================================
# 7. WORKSPACE TELEMETRY DASHBOARDS
# ==============================================================================
def render_telemetry_dashboard() -> None:
    """Renders analytical workspace performance cards."""
    st.markdown("### 📊 Workspace Telemetry & Health Monitor")

    telemetry = st.session_state.get("telemetry", {"requests": 0, "est_tokens": 0, "last_latency": 0.0})
    
    total_requests = telemetry.get("requests", 0)
    total_tokens = telemetry.get("est_tokens", 0)
    last_latency = telemetry.get("last_latency", 0.0)

    avg_tokens_per_req = round(total_tokens / total_requests, 1) if total_requests > 0 else 0
    est_cost_savings = f"${(total_tokens / 1000) * 0.002:.4f}"

    col_t1, col_t2, col_t3, col_t4 = st.columns(4)
    with col_t1:
        st.metric(label="Total Requests", value=f"{total_requests:,}")
    with col_t2:
        st.metric(label="Est. Tokens Processed", value=f"{total_tokens:,}", delta=f"~{avg_tokens_per_req}/req")
    with col_t3:
        latency_status = "⚡ Fast" if last_latency < 1.5 else ("🟢 Normal" if last_latency < 3.5 else "🟡 Slow")
        st.metric(label="Last Latency", value=f"{last_latency:.2f}s", delta=latency_status)
    with col_t4:
        st.metric(label="Est. Cost Saved", value=est_cost_savings)


def render_sidebar_telemetry_widget() -> None:
    """Renders sidebar metrics card."""
    if "telemetry" not in st.session_state:
        st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}

    telemetry = st.session_state.telemetry
    reqs = telemetry.get("requests", 0)
    tokens = telemetry.get("est_tokens", 0)
    latency = telemetry.get("last_latency", 0.0)

    avg_tokens = round(tokens / reqs) if reqs > 0 else 0
    latency_badge = "⏸️ Idle" if latency == 0.0 else ("⚡ Fast" if latency < 1.5 else ("🟢 Normal" if latency < 3.5 else "🟡 Slow"))

    with st.sidebar.expander("📈 **Live Telemetry**", expanded=False):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.metric("Requests", f"{reqs:,}")
            st.metric("Avg Tkn/Req", f"{avg_tokens:,}")
        with col_m2:
            st.metric("Total Tokens", f"{tokens:,}")
            st.metric("Latency", f"{latency:.2f}s", delta=latency_badge, delta_color="off")

        st.markdown("---")
        if st.button("🧹 Reset Telemetry", key="sidebar_reset_telemetry_btn", use_container_width=True):
            st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}
            st.toast("Telemetry metrics reset!", icon="🧹")
            st.rerun()

import json
from datetime import datetime

def export_session_to_json(chats_dict: dict) -> str:
    """
    Serializes active session threads into a structured JSON string for export.
    """
    export_payload = {
        "version": "1.0",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "threads": chats_dict
    }
    return json.dumps(export_payload, indent=2)


def import_session_from_json(json_str: str) -> dict:
    """
    Validates and restores chat history from an imported JSON payload.
    """
    try:
        data = json.loads(json_str)
        if isinstance(data, dict) and "threads" in data and isinstance(data["threads"], dict):
            return data["threads"]
        elif isinstance(data, dict):
            return data
    except Exception as e:
        logging.error(f"Failed to import session JSON: {e}")
    return {}


# ==============================================================================
# 8. CHAT THREAD RENDERER & ACTION TOOLBAR
# ==============================================================================
def render_chat_history_thread(active_chat_list: list, client=None) -> None:
    """Renders active conversation history with interactive elements."""
    if not active_chat_list:
        st.info("👋 Welcome! Ask a question or use slash commands like `/image` or `/search`.")
        return

    for msg_idx, msg in enumerate(active_chat_list):
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "user")
        content = msg.get("content", "")
        avatar = "👤" if role == "user" else "🤖"

        with st.chat_message(role, avatar=avatar):
            # Guard against multi-modal dictionary objects
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            st.markdown(sanitize_and_repair_formatting(part.get("text", "")))
                        elif part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            if url:
                                st.image(url, caption="Attached Image", use_container_width=True)
            else:
                st.markdown(sanitize_and_repair_formatting(str(content)))

            # Assistant Action Toolbar
            if role == "assistant":
                col_tb1, col_tb2 = st.columns([3, 7])
                with col_tb1:
                    if st.button("📋 Copy Text", key=f"copy_btn_{msg_idx}"):
                        st.toast("Text copied to view context!", icon="📋")
                with col_tb2:
                    st.caption("✨ llama-3.3-70b-versatile" Pipeline)

import os
import re
import io
import tempfile
import contextlib
from datetime import datetime
import streamlit as st

# Safe conditional imports to prevent hard app crashes if dependencies are missing
try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

try:
    from gtts import gTTS
except ImportError:
    gTTS = None


# ==============================================================================
# 1. CHAT EXPORT PIPELINE (FRONTIER MARKDOWN GENERATOR)
# ==============================================================================
def export_chat_as_markdown(chat_list: list, title: str = "Chat Session") -> str:
    """Converts structured chat history into clean, standardized Markdown."""
    # Guard 1: Defensive type normalization
    if not isinstance(chat_list, list):
        chat_list = []
    
    clean_title = str(title) if title else "Chat Session"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    md_content = [
        f"# 📄 {clean_title}",
        f"**Exported On:** {timestamp}  ",
        f"**Total Messages:** {len(chat_list)}  ",
        "\n---\n"
    ]

    for msg in chat_list:
        # Guard 2: Skip non-dict message payloads safely
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Guard 3: Structural content normalization (handling arrays, dicts, primitives)
        if isinstance(content, list):
            extracted = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    extracted.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    extracted.append(item)
            content = "\n".join(extracted) if extracted else str(content)
        elif not isinstance(content, str):
            content = str(content)

        header = "### 👤 User" if role == "user" else "### 🤖 Assistant"
        md_content.append(f"{header}\n\n{content.strip()}\n\n---\n")

    return "\n".join(md_content)


def render_chat_export_ui() -> None:
    """UI Helper to render the Markdown download action safely in the sidebar."""
    try:
        current_chat_name = st.session_state.get("current_chat", "New Chat")
        chats = st.session_state.get("chats", {})
        
        # Guard: Type validation on storage retrieved from session state
        if not isinstance(chats, dict):
            chats = {}
            
        active_chat_list = chats.get(current_chat_name, [])
        if not isinstance(active_chat_list, list):
            active_chat_list = []

        if active_chat_list:
            md_data = export_chat_as_markdown(active_chat_list, title=current_chat_name)
            
            # Guard: File name sanitization against OS-level invalid file path characters
            clean_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', str(current_chat_name)).lower()
            if not clean_filename:
                clean_filename = "chat_export"

            st.sidebar.download_button(
                label="📥 Export Chat (.md)",
                data=md_data,
                file_name=f"{clean_filename}_export.md",
                mime="text/markdown",
                use_container_width=True,
                key="export_chat_md_btn"
            )
    except Exception as err:
        st.sidebar.caption(f"⚠️ Export feature currently unavailable.")
        print(f"⚠️ [EXPORT ERROR]: {err}")


# ==============================================================================
# 2. TEXT-TO-SPEECH AUDIO PIPELINE
# ==============================================================================
def generate_tts_audio(text: str, speed_factor: float = 1.0) -> str:
    """Strips formatting syntax and converts text into spoken audio."""
    # Guard 1: Input validation
    if not text or not isinstance(text, str) or not text.strip():
        return None

    # Guard 2: Dependency check
    if gTTS is None:
        print("⚠️ [TTS WARN] 'gTTS' package is not installed.")
        return None

    try:
        clean_text = text
        clean_text = re.sub(r"```[\s\S]*?```", " [code block omitted] ", clean_text)
        clean_text = re.sub(r"`.*?`", "", clean_text)
        clean_text = re.sub(r"\$\$.*?\$\$", " [equation] ", clean_text)
        clean_text = re.sub(r"\$.*?\$", " [math] ", clean_text)
        clean_text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", clean_text)
        clean_text = re.sub(r"<.*?>", "", clean_text)
        clean_text = re.sub(r"[*_#~>]", "", clean_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        # Enforce reasonable buffer boundary for local generation
        clean_text = clean_text[:400]
        if not clean_text:
            return None

        # Guard 3: Safe TTS rendering and tempfile handling
        tts = gTTS(text=clean_text, lang="en", slow=(speed_factor < 1.0))
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            tts.save(fp.name)
            return fp.name

    except Exception as err:
        print(f"⚠️ [TTS ERROR] Speech synthesis failed: {err}")
        return None


# ==============================================================================
# 3. MULTI-ANGLE SEARCH & FACT SYNTHESIS ENGINE
# ==============================================================================
def execute_deconstructed_multi_search(query: str, client, selected_model: str) -> str:
    """Deconstructs queries, performs parallel retrieval, and synthesizes citations."""
    tavily_key = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))
    
    # Guard 1: Environment & dependency checks
    if not tavily_key:
        return "⚠️ Search skipped: Missing `TAVILY_API_KEY` in secrets or environment."
    if TavilyClient is None:
        return "⚠️ Search skipped: `tavily-python` SDK is not installed."
    if not client:
        return "⚠️ Search skipped: OpenAI/OpenRouter LLM client is not initialized."

    try:
        tavily = TavilyClient(api_key=tavily_key)
    except Exception as err:
        return f"⚠️ Failed to instantiate Tavily Client: {err}"

    # Step 1: Sub-query Generation with Fallback
    queries = [query]
    sub_query_prompt = (
        f"Deconstruct this query into 3 distinct search sub-queries to capture different angles:\n"
        f"Query: '{query}'\n"
        "Return ONLY the 3 queries, one per line, with no extra text."
    )

    try:
        sub_res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": sub_query_prompt}],
            temperature=0.1,
            timeout=10.0
        )
        raw_queries = sub_res.choices[0].message.content.strip().split("\n")
        parsed_queries = [re.sub(r"^[0-9\.\-\*\s]+", "", q).strip() for q in raw_queries if q.strip()]
        if parsed_queries:
            queries = parsed_queries[:3]
    except Exception:
        queries = [query]  # Fallback to single raw query if LLM deconstruction times out

    # Step 2: Retrieval loop
    aggregated_sources = []
    sources_metadata = []
    source_counter = 1

    for q in queries:
        try:
            res = tavily.search(query=q, max_results=2)
            for item in res.get("results", []):
                title = str(item.get("title", "Source")).strip()
                content = str(item.get("content", "")).strip()
                url = str(item.get("url", "#")).strip()
                
                aggregated_sources.append(
                    f"[{source_counter}] **{title}** (URL: {url})\nContent: {content}"
                )
                sources_metadata.append({
                    "id": source_counter,
                    "title": title,
                    "url": url
                })
                source_counter += 1
        except Exception:
            continue

    if not aggregated_sources:
        return "No authoritative search sources could be retrieved at this time."

    # Step 3: Synthesis
    synthesis_prompt = (
        f"Synthesize an accurate, well-structured answer for: '{query}' using these factual sources.\n\n"
        "CRITICAL CITATION RULES:\n"
        "1. Insert clickable markdown citation links in your answer whenever referencing facts, e.g., [[1]](URL).\n"
        "2. Keep the tone informative, balanced, and scannable.\n\n"
        "SOURCES:\n" + "\n\n".join(aggregated_sources)
    )

    try:
        synthesis_res = client.chat.completions.create(
            model=selected_model,
            messages=[{"role": "user", "content": synthesis_prompt}],
            temperature=0.2,
            timeout=30.0
        )
        ai_response = synthesis_res.choices[0].message.content.strip()
    except Exception as err:
        return f"⚠️ Fact synthesis error: {err}"

    # Step 4: Append references footer
    references_footer = "\n\n---\n### 🌐 Sources & References\n"
    for src in sources_metadata:
        references_footer += f"* [[{src['id']}]] [{src['title']}]({src['url']})\n"

    return ai_response + references_footer


# ==============================================================================
# 4. SYSTEM PROMPT BUILDER & VECTOR-LIKE MEMORY RETRIEVAL
# ==============================================================================
def build_dynamic_system_prompt(
    user_input: str, base_personality: str, language: str, detected_style: str = "GENERAL"
) -> str:
    """Builds a high-density, context-aware dynamic system prompt."""
    lowered_input = str(user_input).lower() if user_input else ""
    safe_lang = str(language) if language else "English"
    safe_persona = str(base_personality) if base_personality else "Helpful Assistant"

    if detected_style == "CASUAL":
        return (
            f"You are a warm, highly intelligent peer operating as a {safe_persona}.\n"
            f"RULES:\n"
            f"- Speak naturally, concisely, and directly in {safe_lang}.\n"
            f"- Avoid disclaimers or rigid headers unless asked.\n"
            f"- Be conversational, perceptive, and helpful."
        )

    prompt = [
        f"You are an elite AI assistant operating as a {safe_persona}.",
        "",
        "### 🧠 CORE COGNITIVE DIRECTIVES:",
        "1. **First-Principles Reasoning:** Deconstruct complex queries into core logical elements.",
        "2. **Zero Fluff:** Start directly with the answer without preambles like 'Sure, here is...'.",
        "3. **Quantitative Precision:** Use concrete units, probabilities, or metrics where applicable.",
        "4. **Production Code:** Provide clean, runnable code with explicit syntax highlighting."
    ]

    # Domain adaptation keywords
    domain_rules = {
        ("code", "architecture", "algorithm", "python", "javascript", "refactor", "bug", "api", "database"): (
            "\n[DOMAIN ACTIVATED: PRINCIPAL SYSTEMS ARCHITECT]\n"
            "- Focus on modularity, edge-case safety, typed signatures, and execution efficiency."
        ),
        ("data", "dataframe", "pandas", "plot", "csv", "statistics", "machine learning"): (
            "\n[DOMAIN ACTIVATED: SENIOR DATA SCIENTIST]\n"
            "- Focus on statistical validity, data hygiene, vectorization, and clean visualizations."
        )
    }

    for keywords, adaptation_prompt in domain_rules.items():
        if any(kw in lowered_input for kw in keywords):
            prompt.append(adaptation_prompt)
            break

    if safe_lang.lower() != "english":
        prompt.append(f"\nCRITICAL LANGUAGE DIRECTIVE: You MUST respond entirely in {safe_lang}.")

    # Jaccard Memory Retrieval Guard
    try:
        memory_vault = getattr(st.session_state, "memory_vault", [])
        if isinstance(memory_vault, list) and memory_vault:
            user_tokens = set(re.findall(r"\w+", lowered_input))
            if user_tokens:
                scored_memories = []
                for fact in memory_vault:
                    if not isinstance(fact, str):
                        continue
                    fact_tokens = set(re.findall(r"\w+", fact.lower()))
                    union_len = len(user_tokens.union(fact_tokens))
                    score = len(user_tokens.intersection(fact_tokens)) / float(union_len if union_len > 0 else 1)
                    scored_memories.append((score, fact))

                scored_memories.sort(key=lambda x: x[0], reverse=True)
                top_memories = [m[1] for m in scored_memories[:3] if m[0] > 0.05]

                if top_memories:
                    memory_block = "\n".join([f"- {m}" for m in top_memories])
                    prompt.append(
                        f"\n[RELEVANT USER CONTEXT]:\n"
                        f"Incorporate these relevant user facts naturally:\n{memory_block}"
                    )
    except Exception as err:
        print(f"⚠️ [MEMORY RETRIEVAL WARN]: {err}")

    return "\n".join(prompt)


# ==============================================================================
# 5. DYNAMIC VISUAL CANVAS AUTO-RENDERER
# ==============================================================================
def render_data_canvas(response_text: str):
    """Parses markdown tables or CSV structures into dynamic charts & dataframes."""
    if not response_text or "|" not in response_text:
        return

    # Guard 1: Require Pandas library
    if pd is None:
        return

    try:
        lines = [line.strip() for line in response_text.split("\n") if "|" in line]
        if len(lines) < 3:
            return

        cleaned_lines = [
            re.sub(r"^\||\|$", "", line)
            for line in lines
            if not re.match(r"^[\vert{}\s:-]+$", line)
        ]
        
        data = [[cell.strip() for cell in line.split("|")] for line in cleaned_lines]

        if len(data) > 1:
            headers = data[0]
            rows = data[1:]
            df = pd.DataFrame(rows, columns=headers)

            for col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="ignore")

            num_cols = df.select_dtypes(include=["number"]).columns.tolist()

            if num_cols:
                st.markdown("#### 📊 Dynamic Visual Canvas")
                st.dataframe(df, use_container_width=True)
                
                chart_key = f"chart_type_{abs(hash(response_text))}"
                chart_type = st.radio("Chart Representation:", ["Bar", "Line"], horizontal=True, key=chart_key)
                
                index_col = df.columns[0]
                if chart_type == "Bar":
                    st.bar_chart(df.set_index(index_col)[num_cols])
                else:
                    st.line_chart(df.set_index(index_col)[num_cols])
    except Exception as err:
        # Silently fail on non-standard tables to prevent visual disruptions
        print(f"⚠️ [CANVAS RENDER WARN]: {err}")


# ==============================================================================
# 6. INLINE INTERACTIVE CODE EXECUTION ENGINE
# ==============================================================================
def render_interactive_code_runner(response_text: str, msg_idx: int):
    """Provides an interactive Python code execution block within the chat interface."""
    if not response_text or "```python" not in response_text:
        return

    python_blocks = re.findall(r"```python\s*(.*?)\s*```", response_text, re.DOTALL)
    if not python_blocks:
        return

    for b_idx, code in enumerate(python_blocks):
        clean_code = code.strip()
        if not clean_code:
            continue

        expander_title = f"⚡ Interactive Python Execution (Block {b_idx + 1})"
        with st.expander(expander_title, expanded=False):
            st.code(clean_code, language="python")
            
            run_key = f"run_code_{msg_idx}_{b_idx}_{abs(hash(clean_code))}"
            if st.button("▶️ Execute Code", key=run_key):
                output_buffer = io.StringIO()
                
                # Isolated namespace setup
                exec_globals = {
                    "st": st,
                    "pd": pd,
                    "re": re,
                    "datetime": datetime
                }

                try:
                    # Redirect stdout to capture print() calls safely
                    with contextlib.redirect_stdout(output_buffer):
                        exec(clean_code, exec_globals)
                    
                    output_text = output_buffer.getvalue().strip()
                    if output_text:
                        st.success("Execution Completed:")
                        st.code(output_text)
                    else:
                        st.info("Executed successfully with no printed stdout output.")

                except Exception as execution_err:
                    st.error(f"Execution Error: {type(execution_err).__name__} - {execution_err}")

import os
import re
import io
import json
import base64
import contextlib
import mimetypes
from datetime import datetime
import streamlit as st

# Safe conditional imports for external third-party SDKs
try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from groq import Groq
except ImportError:
    Groq = None

try:
    from audio_recorder_streamlit import audio_recorder
except ImportError:
    audio_recorder = None


# ==============================================================================
# UPGRADE #54: AUTONOMOUS AGENTIC CODE DEBUGGER (HARDENED)
# ==============================================================================
def run_autonomous_code_debugger(code_snippet: str, client, model: str) -> str:
    """Runs code in a sandboxed execution environment, traps errors, and asks
    LLM to self-correct iteratively up to max bounds.
    """
    if not code_snippet or not isinstance(code_snippet, str):
        st.error("❌ Invalid code snippet provided for debugging.")
        return code_snippet or ""

    st.info("🤖 Agentic Debugger Active: Safely testing code execution...")
    max_attempts = 3
    current_code = code_snippet.strip()

    for attempt in range(1, max_attempts + 1):
        output_buffer = io.StringIO()
        try:
            # Environment namespace setup
            exec_globals = {
                "st": st,
                "pd": pd,
                "re": re,
                "datetime": datetime
            }

            with contextlib.redirect_stdout(output_buffer):
                exec(current_code, exec_globals)

            st.success(f"✅ Code executed cleanly on attempt {attempt}!")
            return current_code

        except Exception as err:
            err_msg = f"{type(err).__name__}: {err}"
            st.warning(f"⚠️ Attempt {attempt} failed: {err_msg}")

            if not client:
                st.error("❌ Debugger halted: No active LLM client available for auto-correction.")
                return current_code

            fix_prompt = (
                f"The following Python code produced an execution error:\n\n"
                f"```python\n{current_code}\n```\n\n"
                f"ERROR:\n{err_msg}\n\n"
                f"Fix the code. Return ONLY valid Python code inside standard triple-backtick markdown blocks."
            )

            try:
                res = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": fix_prompt}],
                    temperature=0.1,
                    timeout=20.0
                )
                
                raw_response = res.choices[0].message.content or ""
                extracted = re.search(r"```python\s*(.*?)\s*```", raw_response, re.DOTALL)
                
                if extracted and extracted.group(1).strip():
                    current_code = extracted.group(1).strip()
                else:
                    st.error("❌ Agentic debugger could not extract corrected Python block from response.")
                    break
            except Exception as api_err:
                st.error(f"❌ LLM correction request failed: {api_err}")
                break

    st.error("❌ Agentic debugger reached maximum iterations without clean resolution.")
    return current_code


# ==============================================================================
# UPGRADE #55: SMART CHAT AUTO-SUMMARIZER (HARDENED)
# ==============================================================================
def auto_summarize_chat_title(chat_history: list, client, current_name: str) -> None:
    """Dynamically generates clean chat titles based on early user intent.
    
    Guards against state race conditions, dictionary key collisions, and API outages.
    """
    if not isinstance(chat_history, list) or not chat_history:
        return

    # Find the first non-empty user text message
    first_user_msg = None
    for msg in chat_history:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                first_user_msg = content.strip()
                break

    # Only rename default dynamic naming threads
    safe_current_name = str(current_name) if current_name else ""
    if not first_user_msg or not (safe_current_name.startswith("Chat ") or safe_current_name == "New Chat"):
        return

    if not client:
        return

    system_instruction = (
        "You are an expert title generator for an AI platform. "
        "Create a concise, highly relevant title (2 to 5 words maximum) summarizing the topic.\n"
        "STRICT CONSTRAINTS:\n"
        "- Return ONLY the plain text title.\n"
        "- Do NOT use quotes, punctuation, emojis, or markdown.\n"
        "- Do NOT prefix with 'Title:' or similar phrases.\n"
        "- Capitalize like a standard headline (Title Case)."
    )

    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Topic query: '{first_user_msg[:500]}'"}
            ],
            temperature=0.2,
            max_tokens=15,
            timeout=8.0
        )

        raw_title = res.choices[0].message.content or ""
        cleaned_title = re.sub(r'[^a-zA-Z0-9\s-]', '', raw_title).strip()
        cleaned_title = " ".join(cleaned_title.split()).title()

        if not cleaned_title or len(cleaned_title) < 2:
            cleaned_title = " ".join(first_user_msg.split()[:4]).title()

        if len(cleaned_title) > 35:
            cleaned_title = cleaned_title[:32].rstrip() + "..."

        # Safe Session State Dictionary Atomic Mutation
        chats = st.session_state.get("chats", {})
        if not isinstance(chats, dict):
            return

        final_title = cleaned_title
        counter = 1
        while final_title in chats:
            final_title = f"{cleaned_title} ({counter})"
            counter += 1

        if safe_current_name in chats:
            chat_data = chats.pop(safe_current_name)
            chats[final_title] = chat_data
            st.session_state.chats = chats
            st.session_state.current_chat = final_title

            if "save_chats_to_disk" in globals() and callable(globals()["save_chats_to_disk"]):
                try:
                    globals()["save_chats_to_disk"]()
                except Exception as save_err:
                    print(f"⚠️ Disk save sync error: {save_err}")

            st.rerun()

    except Exception as err:
        print(f"⚠️ [TITLE AUTO-SUMMARIZER WARN]: {err}")


# ==============================================================================
# UPGRADE #62: PROMPT ENHANCER & QUERY EXPANSION
# ==============================================================================
def enhance_user_prompt(prompt_text: str, client) -> str:
    """Transforms raw user input into a rich, structured instruction payload."""
    if not prompt_text or not isinstance(prompt_text, str):
        return ""

    cleaned_input = prompt_text.strip()
    if len(cleaned_input) < 3 or not client:
        return prompt_text

    system_instruction = (
        "You are an expert Prompt Engineer for frontier AI models.\n"
        "Transform raw user input into a structured, highly effective instruction prompt.\n\n"
        "GUIDELINES:\n"
        "1. Clarify intent, context, objective, and output constraints.\n"
        "2. Add structural formatting guidelines where beneficial.\n"
        "3. Preserve the core meaning, domain, and language of the original query.\n"
        "4. DO NOT answer the query. ONLY rewrite the prompt itself.\n"
        "5. Output ONLY the improved prompt text without commentary or preambles."
    )

    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"Raw User Query: '{cleaned_input}'"}
            ],
            temperature=0.3,
            max_tokens=600,
            timeout=10.0
        )

        enhanced = res.choices[0].message.content or ""
        enhanced = enhanced.strip()

        # Clean conversational wrappers safely
        enhanced = re.sub(r'^(Here is[^\n]*\n|Enhanced Prompt:\s*)', '', enhanced, flags=re.IGNORECASE).strip()

        return enhanced if len(enhanced) >= len(cleaned_input) else prompt_text

    except Exception as err:
        print(f"⚠️ [PROMPT ENHANCER WARN]: {err}")
        return prompt_text


# ==============================================================================
# UPGRADE #53: AUTO-SEARCH INTENT ROUTER
# ==============================================================================
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
    """Evaluates user input to determine if live web search context is required."""
    if not user_text or not isinstance(user_text, str) or not user_text.strip():
        return False

    lowered = user_text.lower().strip()

    if lowered.startswith("/search"):
        return True

    # Skip search on URLs or explicit code blocks
    if re.search(r"https?://|www\.", lowered) or lowered.startswith("```"):
        return False

    # Temporal detection for recent years
    if re.search(r"\b(202[4-6])\b", lowered):
        return True

    tokens = set(re.findall(r"\b\w+\b", lowered))
    if tokens.intersection(REALTIME_KEYWORDS) or tokens.intersection(MEDIA_LORE_KEYWORDS):
        return True

    phrases = ["who won", "release date", "right now", "voice actor", "who plays"]
    if any(phrase in lowered for phrase in phrases):
        return True

    return False


# ==============================================================================
# UI CONFIG & WORKSPACE CONTROLS INITIALIZATION
# ==============================================================================
st.set_page_config(
    page_title="AI Workspace",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Workspace CSS
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

# Initialize Session State Keys Defensively
if "chats" not in st.session_state or not isinstance(st.session_state.chats, dict):
    st.session_state.chats = {"Chat 1": []}

if "current_chat" not in st.session_state or st.session_state.current_chat not in st.session_state.chats:
    st.session_state.current_chat = list(st.session_state.chats.keys())[0]

if "memory_vault" not in st.session_state or not isinstance(st.session_state.memory_vault, list):
    st.session_state.memory_vault = []

if "bookmarks" not in st.session_state or not isinstance(st.session_state.bookmarks, list):
    st.session_state.bookmarks = []

if "telemetry" not in st.session_state or not isinstance(st.session_state.telemetry, dict):
    st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}

# Title & App Header
st.title("🤖 Intelligent AI Workspace")
st.caption("Enhanced with Epistemic Guardrails, Live Search & Workspace Telemetry")

# API Client Initialization
GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY and Groq is not None:
    try:
        client = Groq(api_key=GROQ_KEY)
    except Exception as err:
        client = None
        st.error(f"⚠️ Groq client initialization failed: {err}")
else:
    client = None
    st.warning("⚠️ Missing `GROQ_API_KEY` or Groq SDK package!")

# Helper formatting sanitizer placeholder guard
def sanitize_and_repair_formatting(text: str) -> str:
    if not isinstance(text, str):
        return str(text or "")
    return text

# ==============================================================================
# SIDEBAR CONTROLS & WORKSPACE MANAGER
# ==============================================================================
with st.sidebar:
    st.header("⚙️ Workspace Controls")
    st.markdown("---")

    # 1. Thread Management
    st.header("💬 Chat Sessions")
    chat_names = list(st.session_state.chats.keys())
    
    current_index = chat_names.index(st.session_state.current_chat) if st.session_state.current_chat in chat_names else 0

    selected_chat = st.selectbox("Select Thread:", chat_names, index=current_index)

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
    target_language = st.selectbox("Response Language:", ["English", "Spanish", "French", "German", "Mandarin", "Japanese"])
    personality = st.selectbox("AI Persona:", ["Helpful Assistant", "Code Expert", "Strict Tutor", "Executive Analyst"])
    selected_model = st.selectbox("Model Engine:", ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"])

    st.markdown("---")

    # 3. Multimodal & File Ingestion
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
            filename = str(uploaded_file.name).lower()
            
            if filename.endswith((".jpg", ".png", ".jpeg", ".webp")):
                mime_guess, _ = mimetypes.guess_type(uploaded_file.name)
                image_mime_type = mime_guess if mime_guess else "image/jpeg"

                uploaded_file.seek(0)
                bytes_data = uploaded_file.read()
                image_base64 = base64.b64encode(bytes_data).decode("utf-8")
                st.image(uploaded_file, caption="📷 Loaded into Vision Context", use_container_width=True)
            
            elif filename.endswith(".csv"):
                if pd is not None:
                    df_upload = pd.read_csv(uploaded_file)
                    st.markdown("#### 🔍 CSV File Summary")
                    st.write(f"**Rows:** {df_upload.shape[0]:,} | **Cols:** {df_upload.shape[1]}")
                    st.dataframe(df_upload.head(3), use_container_width=True)
                    
                    doc_context = (
                        f"CSV File Summary ({uploaded_file.name}):\n"
                        f"Columns: {list(df_upload.columns)}\n"
                        f"Data Head:\n{df_upload.head(10).to_csv(index=False)}"
                    )
                else:
                    uploaded_file.seek(0)
                    doc_context = uploaded_file.read().decode("utf-8", errors="replace")
                    st.info("📄 Standard pandas package missing; loaded CSV as raw string.")
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
        for idx, fact in enumerate(list(st.session_state.memory_vault)):
            col_m1, col_m2 = st.columns([8, 2])
            col_m1.caption(f"• {fact}")
            if col_m2.button("❌", key=f"del_mem_fact_{idx}"):
                if idx < len(st.session_state.memory_vault):
                    st.session_state.memory_vault.pop(idx)
                    st.rerun()
                
        if st.button("🧹 Clear All Memories", use_container_width=True):
            st.session_state.memory_vault = []
            st.rerun()
    else:
        st.caption("No custom memory facts saved yet.")

    # 5. Thread Export Engine
    st.markdown("---")
    st.header("📥 Thread Export Options")
    export_chat = st.session_state.chats.get(st.session_state.current_chat, [])
    
    try:
        chat_export_json = json.dumps(export_chat, indent=2)
        clean_chat_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', str(st.session_state.current_chat)).lower()
        
        st.download_button(
            label="Download Chat (.json)",
            data=chat_export_json,
            file_name=f"{clean_chat_filename}.json",
            mime="application/json",
            use_container_width=True,
        )
    except Exception as err:
        st.caption("JSON Export unavailable.")

    # Telemetry Dashboard
    st.markdown("---")
    st.header("📊 Telemetry Dashboard")
    st.caption(f"⚡ **Requests Executed:** {st.session_state.telemetry.get('requests', 0)}")
    st.caption(f"🔤 **Est. Tokens Processed:** {st.session_state.telemetry.get('est_tokens', 0)}")
    st.caption(f"⏱️ **Last Latency:** {st.session_state.telemetry.get('last_latency', 0.0):.2f}s")

    # Global Reset
    st.markdown("---")
    if st.button("🧹 Reset Workspace Cache", use_container_width=True):
        st.session_state.chats = {"Chat 1": []}
        st.session_state.current_chat = "Chat 1"
        st.session_state.memory_vault = []
        st.rerun()

# Active Chat Buffer Render
active_chat_list = st.session_state.chats.get(st.session_state.current_chat, [])

col_hdr1, col_hdr2 = st.columns([6, 4])
with col_hdr1:
    st.markdown(f"### 💬 {st.session_state.current_chat}")
with col_hdr2:
    st.markdown(
        f"<div style='text-align: right;'><span class='model-badge'>🤖 {selected_model}</span> <span class='model-badge'>🌐 {target_language}</span></div>",
        unsafe_allow_html=True,
    )

st.markdown("---")

# Render Message History
for idx, msg in enumerate(active_chat_list):
    if not isinstance(msg, dict):
        continue
    role = msg.get("role", "user")
    
    with st.chat_message(role):
        repaired_content = sanitize_and_repair_formatting(msg.get("content", ""))
        st.markdown(repaired_content)

        if role == "assistant":
            col_a1, col_a2, col_a3, _ = st.columns([1, 1, 1, 7])
            if col_a1.button("📌", key=f"bm_{idx}", help="Bookmark Response"):
                st.session_state.bookmarks.append(repaired_content)
                st.toast("Bookmarked response!")

            if col_a2.button("🔊", key=f"tts_{idx}", help="Generate Speech"):
                if "generate_tts_audio" in globals():
                    audio_file = generate_tts_audio(repaired_content)
                    if audio_file:
                        st.audio(audio_file, format="audio/mp3")

            if "render_data_canvas" in globals():
                render_data_canvas(repaired_content)

            if "render_interactive_code_runner" in globals():
                render_interactive_code_runner(repaired_content, idx)

# Voice Audio Recorder
col_v1, col_v2 = st.columns([1, 11])
with col_v1:
    if audio_recorder is not None:
        audio_bytes = audio_recorder(
            text="",
            recording_color="#e8b62c",
            neutral_color="#6aa84f",
            icon_size="2x",
        )
    else:
        audio_bytes = None

if audio_bytes and client:
    with st.spinner("🎙️ Transcribing audio input..."):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as fp:
                fp.write(audio_bytes)
                tmp_path = fp.name

            with open(tmp_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model="whisper-large-v3",
                    file=audio_file,
                    response_format="text",
                )
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
            if transcription and str(transcription).strip():
                st.session_state.input_buffer = str(transcription).strip()
        except Exception as e:
            st.error(f"Voice transcription error: {e}")


def classify_user_intent(prompt: str, client, model_name: str) -> str:
    """Analyzes user prompt and automatically decides routing.
    Returns: 'IMAGE', 'DEBUG', 'SEARCH', 'READ', or 'CHAT'
    """
    if not prompt or not isinstance(prompt, str):
        return "CHAT"

    lowered = prompt.lower().strip()

    if lowered.startswith("/image") or lowered.startswith("/draw"): 
        return "IMAGE"
    if lowered.startswith("/debug"): 
        return "DEBUG"
    if lowered.startswith("/search"): 
        return "SEARCH"
    if lowered.startswith("/read"): 
        return "READ"

    image_triggers = ["draw", "generate an image", "picture of", "paint", "create an image"]
    if any(trigger in lowered for trigger in image_triggers):
        return "IMAGE"

    debug_triggers = ["traceback", "syntaxerror", "fix this code", "def ", "import "]
    if "error" in lowered and any(trig in lowered for trig in debug_triggers):
        return "DEBUG"

    if needs_automatic_search(prompt) or any(kw in lowered for kw in REALTIME_KEYWORDS):
        return "SEARCH"

    return "CHAT"

import os
import re
import time
import random
import urllib.parse
import streamlit as st

# Safe conditional imports
try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Safe client references initialized from session state or environment
client = globals().get("client", None)
openrouter_client = globals().get("openrouter_client", None)
selected_model = st.session_state.get("selected_model", "llama-3.3-70b-versatile")
personality = st.session_state.get("personality", "Helpful Assistant")
target_language = st.session_state.get("target_language", "English")

# Retrieve uploaded context safely from state if initialized elsewhere
doc_context = st.session_state.get("doc_context", globals().get("doc_context", ""))
image_base64 = globals().get("image_base64", None)
image_mime_type = globals().get("image_mime_type", "image/jpeg")
uploaded_file = globals().get("uploaded_file", None)


# Safe fallback helper definitions
def sanitize_and_repair_formatting(text: str) -> str:
    if not isinstance(text, str):
        return str(text or "")
    return text


def build_dynamic_system_prompt(prompt: str, persona: str, lang: str, style: str) -> str:
    return f"You are a {persona}. Reply in {lang}. Tone style: {style}."


# ==============================================================================
# FEATURE #3: DOCUMENT CANVAS & RAG INSPECTOR
# ==============================================================================
if doc_context and isinstance(doc_context, str) and doc_context.strip():
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

            display_text = (
                doc_context
                if full_view or len(doc_context) <= 2000
                else doc_context[:2000] + "\n\n... [Truncated for Preview]"
            )

            st.text_area(
                label="Document Content Preview",
                value=display_text,
                height=200 if not full_view else 400,
                disabled=True,
                label_visibility="collapsed",
                key="rag_doc_preview_area",
            )

        with col_canvas2:
            st.markdown("### 📊 Document Metrics")
            st.metric("Total Words", f"{word_count:,}")
            st.metric("Total Characters", f"{char_count:,}")
            st.metric("Est. Read Time", f"~{est_read_time} min")

            st.markdown("---")

            # Safe Context Reset Action
            if st.button("🧹 Clear Canvas", use_container_width=True, key="clear_doc_canvas_btn"):
                st.session_state.doc_context = ""
                doc_context = ""

                if "uploader_key" in st.session_state:
                    del st.session_state["uploader_key"]

                st.success("Document context cleared!")
                st.rerun()


# ==============================================================================
# INPUT EXECUTION PIPELINE (FRONTIER DISPATCHER)
# ==============================================================================
user_input = st.chat_input("Ask anything, use /search, /image, /debug, or /enhance...")

# Handle buffer redirects (e.g., prompt presets or voice input)
if st.session_state.get("input_buffer") and not user_input:
    user_input = st.session_state.input_buffer
    st.session_state.input_buffer = ""

if user_input and client:
    start_time = time.time()
    final_reply = ""  # Scope accumulator for response and telemetry

    # 1. Thread Binding & Message Mutation
    current_thread = st.session_state.get("current_chat", "New Chat")
    if "chats" not in st.session_state or not isinstance(st.session_state.chats, dict):
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

    # 3. Dynamic Intent Classification & Route Mapping
    lowered_input = user_input.lower().strip()

    if lowered_input.startswith("/search"):
        detected_route = "ROUTE_SEARCH"
    elif lowered_input.startswith(("/image", "/imagine", "/draw")):
        detected_route = "ROUTE_IMAGE_GEN"
    elif lowered_input.startswith("/debug"):
        detected_route = "ROUTE_DEBUG"
    elif lowered_input.startswith("/read"):
        detected_route = "ROUTE_READ"
    else:
        if "classify_user_intent" in globals():
            try:
                classified = classify_user_intent(user_input, client, selected_model)
                route_map = {
                    "IMAGE": "ROUTE_IMAGE_GEN",
                    "DEBUG": "ROUTE_DEBUG",
                    "SEARCH": "ROUTE_SEARCH",
                    "READ": "ROUTE_READ",
                }
                detected_route = route_map.get(classified, "ROUTE_STANDARD")
            except Exception:
                detected_route = "ROUTE_STANDARD"
        else:
            detected_route = "ROUTE_STANDARD"

    # 4. Append User Message & Display
    active_chat_list.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 5. Query Disambiguation & Heuristic Parsing
    processed_prompt = user_input
    if "tadc" in lowered_input and "character" in lowered_input:
        processed_prompt += " (referring to the cast of 'The Amazing Digital Circus')"

    # 6. Adaptive Temperature & Style Tuning
    casual_triggers = {"hi", "hello", "hey", "howdy", "sup", "what's up", "thanks", "thank you", "cool", "nice"}
    analytical_keywords = ["compare", "vs", "probability", "percent", "rate", "code", "architecture", "refactor", "math"]

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

    # 7. ROUTE DISPATCHER EXECUTION
    with st.chat_message("assistant"):

        # --- ROUTE A: LIVE WEB SEARCH ---
        if detected_route == "ROUTE_SEARCH":
            st.info("🔍 *Auto-Detected: Web Search Activated*")
            clean_query = re.sub(r"^/search\s*", "", user_input, flags=re.IGNORECASE).strip()

            if "execute_deconstructed_multi_search" in globals():
                with st.spinner("🔍 Deconstructing query & synthesizing multi-angle search..."):
                    raw_reply = execute_deconstructed_multi_search(clean_query, client, selected_model)
                    final_reply = sanitize_and_repair_formatting(raw_reply)
                    st.markdown(final_reply)
            elif "perform_live_search" in globals():
                with st.status("🌐 Searching the web...", expanded=True) as status:
                    raw_search_data = perform_live_search(clean_query)
                    status.update(label="✅ Search completed!", state="complete", expanded=False)

                    synthesis_prompt = (
                        f"Answer using ONLY the search context:\n\nCONTEXT:\n{raw_search_data}\n\nQUERY:\n{clean_query}"
                    )
                    completion = client.chat.completions.create(
                        model=selected_model,
                        messages=[{"role": "user", "content": synthesis_prompt}],
                        temperature=0.2,
                    )
                    final_reply = completion.choices[0].message.content or ""
                    st.markdown(final_reply)
            else:
                final_reply = "⚠️ Search tool function `perform_live_search` is not defined."
                st.warning(final_reply)

        # --- ROUTE B: AI IMAGE GENERATION ---
        elif detected_route == "ROUTE_IMAGE_GEN":
            clean_prompt = re.sub(
                r"^/(image|imagine|draw|generate)\s*", "", user_input, flags=re.IGNORECASE
            ).strip()
            if not clean_prompt:
                clean_prompt = user_input

            with st.spinner("🎨 Generating high-quality AI artwork..."):
                enhanced_prompt = f"{clean_prompt}, high resolution, detailed, vivid colors"
                encoded_prompt = urllib.parse.quote(enhanced_prompt)
                seed_val = random.randint(1, 99999)

                img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed_val}&model=flux&enhance=true&nologo=true"
                final_reply = f"🎨 **Generated Image for:** *'{clean_prompt}'*\n\n![AI Image]({img_url})"
                st.markdown(final_reply)

        # --- ROUTE C: AUTONOMOUS AGENTIC CODE DEBUGGER ---
        elif detected_route == "ROUTE_DEBUG":
            st.info("🛠️ *Auto-Detected: Code Debugger Activated*")
            clean_code = re.sub(r"^/debug\s*", "", user_input, flags=re.IGNORECASE).strip()

            if "run_autonomous_code_debugger" in globals():
                fixed_code = run_autonomous_code_debugger(clean_code, client, selected_model)
                final_reply = f"```python\n{fixed_code}\n```"
            else:
                st.toast("🛠️ Diagnostic trace initiated...", icon="🔍")
                final_reply = f"**System Debug Payload:**\n* Active Thread: `{current_thread}`\n* Style: `{detected_style}`\n* Temp: `{active_temperature}`"

            st.markdown(final_reply)

        # --- ROUTE D: WEB SCRAPER / PAGE READER ---
        elif detected_route == "ROUTE_READ":
            target_url = re.sub(r"^/read\s*", "", user_input, flags=re.IGNORECASE).strip()

            with st.spinner(f"🌐 Fetching content from {target_url}..."):
                if requests and BeautifulSoup:
                    try:
                        res = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                        soup = BeautifulSoup(res.text, "html.parser")
                        paragraphs = [p.get_text() for p in soup.find_all("p")]
                        page_text = " ".join(paragraphs)[:4000]

                        prompt_with_url = (
                            f"Analyze and summarize the content from {target_url}:\n\n{page_text}"
                        )
                        response = client.chat.completions.create(
                            model=selected_model,
                            messages=[{"role": "user", "content": prompt_with_url}],
                            temperature=active_temperature,
                        )
                        final_reply = response.choices[0].message.content or ""
                        st.markdown(final_reply)
                    except Exception as e:
                        final_reply = f"Failed to fetch web page: {e}"
                        st.error(final_reply)
                else:
                    final_reply = "⚠️ `requests` or `beautifulsoup4` module is missing."
                    st.warning(final_reply)

        # --- ROUTE E: STANDARD CHAT COMPLETION & VISION ---
        else:
            system_prompt = build_dynamic_system_prompt(
                processed_prompt, personality, target_language, detected_style
            )
            system_prompt += "\n\n[STRICT CONTEXT RULE]: Always maintain awareness of prior chat history."

            if doc_context:
                system_prompt += f"\n\n[USER ATTACHED FILE CONTEXT]:\n{doc_context[:4000]}"

            messages_payload = [{"role": "system", "content": system_prompt}]

            for m in active_chat_list[:-1]:
                if isinstance(m.get("content"), str):
                    messages_payload.append({"role": m["role"], "content": m["content"]})

            # Branch 1: Vision Ingestion
            if image_base64 and openrouter_client:
                prompt_text = (
                    user_input.strip() if user_input.strip() else "Describe and analyze this image in detail."
                )
                vision_messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{image_mime_type};base64,{image_base64}"},
                            },
                        ],
                    }
                ]

                vision_models = [
                    "openrouter/auto",
                    "google/gemma-3-12b-it:free",
                    "qwen/qwen-2.5-vl-72b-instruct:free",
                ]

                success = False
                with st.spinner("👁️ Analyzing image with Vision..."):
                    for model_slug in vision_models:
                        try:
                            response = openrouter_client.chat.completions.create(
                                model=model_slug,
                                messages=vision_messages,
                                temperature=active_temperature,
                            )
                            final_reply = response.choices[0].message.content or ""
                            st.markdown(final_reply)
                            success = True
                            break
                        except Exception:
                            continue

                if not success:
                    st.info("📷 Image attached (Vision endpoints busy). Fallback to standard text model...")
                    file_name_str = uploaded_file.name if uploaded_file else "Attachment"
                    fallback_msg = f"[Attached Image: {file_name_str}]\nUser Prompt: {prompt_text}"
                    messages_payload.append({"role": "user", "content": fallback_msg})

                    response = client.chat.completions.create(
                        model=selected_model,
                        messages=messages_payload,
                        temperature=active_temperature,
                    )
                    final_reply = response.choices[0].message.content or ""
                    st.markdown(final_reply)

            # Branch 2: Streaming Standard Text
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
                        if chunk.choices and chunk.choices[0].delta.content:
                            yield chunk.choices[0].delta.content

                raw_reply = st.write_stream(stream_generator)
                final_reply = sanitize_and_repair_formatting(raw_reply)

    # 8. Record Response to State Thread Buffer
    if final_reply:
        active_chat_list.append({"role": "assistant", "content": final_reply})

    # ==============================================================================
    # TELEMETRY, SUMMARIZATION & STATE WRAP-UP
    # ==============================================================================
    latency_seconds = round(time.time() - start_time, 2)

    input_word_count = len(user_input.split())
    output_word_count = len(final_reply.split()) if final_reply else 100
    estimated_request_tokens = int((input_word_count + output_word_count) * 1.33)

    if "telemetry" not in st.session_state or not isinstance(st.session_state.telemetry, dict):
        st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}

    st.session_state.telemetry["requests"] += 1
    st.session_state.telemetry["est_tokens"] += estimated_request_tokens
    st.session_state.telemetry["last_latency"] = latency_seconds

    # Auto-rename dynamic thread title on initial conversation
    if "auto_summarize_chat_title" in globals() and callable(globals()["auto_summarize_chat_title"]):
        try:
            auto_summarize_chat_title(
                chat_history=active_chat_list, client=client, current_name=st.session_state.current_chat
            )
        except Exception as err:
            print(f"⚠️ [SUMMARIZER WARN] Thread renaming skipped: {err}")

    # Synchronize Disk Persistence if function exists
    if "save_chats_to_disk" in globals() and callable(globals()["save_chats_to_disk"]):
        try:
            globals()["save_chats_to_disk"]()
        except Exception as err:
            print(f"⚠️ [DISK SYNC WARN]: {err}")

    st.rerun()
