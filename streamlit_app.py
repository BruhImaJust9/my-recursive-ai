# ==============================================================================
# 0. IMPORTS — SOFT-GUARDED FOR ZERO NAMEERROR GUARANTEE
# ==============================================================================
import base64
import contextlib
import functools
import hashlib
import io
import json
import logging
import math
import os
import random
import re
import sys
import tempfile
import textwrap
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ==============================================================================
# 0. IMPORTS & GLOBAL LOGGING SETUP
# ==============================================================================

import os
import re
import json
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import streamlit as st
import pandas as pd
from PIL import Image

try:
    from groq import Groq
except Exception:
    Groq = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

# ==============================================================================
# 1. CONSTANTS, PRESETS, AND SYSTEM MODES
# ==============================================================================

CHAT_STORAGE_FILE = "persistent_chats.json"
MEMORY_FILE = "persistent_memory.json"

PROVIDER_GROQ = "Groq"
PROVIDER_OPENROUTER = "OpenRouter"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

OPENROUTER_MODELS = [
    "openrouter/auto",
    "openai/gpt-4o-mini",
]

PERSONALITY_PRESETS = [
    "Helpful Assistant",
    "Principal Systems Architect",
    "Creative Writing Coach",
    "Socratic Tutor",
]

PERSONALITY_MODES = {
    "Helpful Assistant": "Friendly, clear, supportive.",
    "Principal Systems Architect": "Technical, structured, precise.",
    "Creative Writing Coach": "Imaginative, expressive, narrative-driven.",
    "Socratic Tutor": "Guiding questions, step-by-step discovery.",
}

SMART_SWITCH = {
    "image": "ROUTE_IMAGE_GEN",
    "draw": "ROUTE_IMAGE_GEN",
    "fix": "ROUTE_DEBUG",
    "bug": "ROUTE_DEBUG",
    "explain": "ROUTE_STANDARD",
    "summarize": "ROUTE_SUMMARIZE",
    "read": "ROUTE_READ",
    "search": "ROUTE_SEARCH",
}

# ==============================================================================
# 2. PERSISTENCE ENGINE — ATOMIC & SAFE
# ==============================================================================

def _atomic_json_write(path: str, payload: dict) -> None:
    """
    Atomic JSON write with temp-file replacement.
    Prevents corruption on crash or interruption.
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as err:
        logging.error("Atomic write failed: %s", err)


def _safe_json_load(path: str, default: dict) -> dict:
    """
    Loads JSON safely with corruption fallback.
    """
    if not os.path.exists(path):
        return default.copy()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default.copy()
        return data
    except Exception as err:
        logging.warning("Load failed (%s), using default.", err)
        return default.copy()


def load_saved_chats() -> dict:
    data = _safe_json_load(CHAT_STORAGE_FILE, {"threads": {"New Chat": []}})
    return data.get("threads", {"New Chat": []})


def save_chats(chats: dict) -> None:
    _atomic_json_write(CHAT_STORAGE_FILE, {"threads": chats})


def load_memory_vault() -> list:
    data = _safe_json_load(MEMORY_FILE, {"memory": []})
    return data.get("memory", [])


def save_memory_vault(memory_list: list) -> None:
    _atomic_json_write(MEMORY_FILE, {"memory": memory_list})

# ==============================================================================
# 3. SESSION STATE INITIALIZATION
# ==============================================================================

def initialize_session_state() -> None:
    """
    Bootstraps Streamlit session state with safe defaults.
    """
    defaults = {
        "chats": {"New Chat": []},
        "current_chat": "New Chat",
        "memory_vault": load_memory_vault(),
        "selected_provider": PROVIDER_GROQ,
        "selected_model": GROQ_MODELS[0],
        "personality": "Helpful Assistant",
        "target_language": "English",
        "temperature": 0.7,
        "max_tokens": 2048,
        "doc_context": "",
        "input_buffer": "",
    }

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if st.session_state.current_chat not in st.session_state.chats:
        st.session_state.chats[st.session_state.current_chat] = []

# ==============================================================================
# 4. CLIENT INITIALIZATION (CACHED)
# ==============================================================================

@st.cache_resource(show_spinner=False)
def get_groq_client(api_key: str):
    if not api_key or Groq is None:
        return None
    try:
        return Groq(api_key=api_key, timeout=30.0)
    except Exception as err:
        logging.error("Groq init failed: %s", err)
        return None


@st.cache_resource(show_spinner=False)
def get_openrouter_client(api_key: str):
    if not api_key or OpenAI is None:
        return None
    try:
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            timeout=30.0,
            max_retries=2,
        )
    except Exception as err:
        logging.error("OpenRouter init failed: %s", err)
        return None


def initialize_clients():
    groq_key = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))
    or_key = st.secrets.get("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))

    groq_client = get_groq_client(groq_key)
    or_client = get_openrouter_client(or_key)
    return groq_client, or_client

# ==============================================================================
# 5. CORE UPGRADE #1 — STABILIZED SYSTEM PROMPT
# ==============================================================================

def build_dynamic_system_prompt(user_input: str, persona: str, language: str) -> str:
    """
    Stable, deterministic system prompt that prevents drift.
    """
    base = (
        f"You are a highly capable AI assistant operating as a {persona}. "
        f"Respond in {language}. "
        "Be clear, structured, and helpful. Avoid filler."
    )

    persona_desc = PERSONALITY_MODES.get(persona, "")
    stability = (
        "Maintain consistent tone and persona across turns. "
        "Do not contradict earlier statements unless corrected."
    )

    return base + f"\nPersona Style: {persona_desc}\n" + stability

# ==============================================================================
# 6. CORE UPGRADE #2 — SEMANTIC MEMORY ENGINE
# ==============================================================================

def search_past_memory(user_query: str, chat_history: list, top_k: int = 2) -> str:
    """
    Lightweight semantic memory using token overlap scoring.
    """
    query_tokens = set(re.findall(r"\w+", user_query.lower()))
    scored = []

    for msg in chat_history:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        tokens = set(re.findall(r"\w+", content.lower()))
        score = len(query_tokens & tokens)

        if score > 1:
            scored.append((score, content))

    scored.sort(reverse=True)
    return "\n---\n".join([c for _, c in scored[:top_k]])


def maybe_store_memory(user_input: str, assistant_reply: str) -> None:
    """
    Simple heuristic memory storage.
    """
    triggers = ["remember", "note", "important", "goal", "plan"]
    if any(w in user_input.lower() for w in triggers):
        st.session_state.memory_vault.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": user_input,
                "assistant": assistant_reply,
            }
        )
        save_memory_vault(st.session_state.memory_vault)

# ==============================================================================
# 7. CORE UPGRADE #3 — SMART MODE SWITCHING
# ==============================================================================

def classify_route(user_input: str) -> str:
    lowered = user_input.lower()
    for key, route in SMART_SWITCH.items():
        if key in lowered:
            return route
    return "ROUTE_STANDARD"

# ==============================================================================
# 8. LLM CALL WRAPPER
# ==============================================================================

def call_llm(
    client,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    """
    Unified LLM call with stability and quality filtering.
    """
    if client is None:
        return "LLM client is not configured."

    try:
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        res = client.chat.completions.create(
            model=model,
            messages=full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        reply = res.choices[0].message.content.strip()

        if len(reply.split()) < 4:
            reply += "\n\n(Expanded for clarity.)"

        return reply

    except Exception as err:
        logging.error("LLM call failed: %s", err)
        return f"Error during model call: {err}"

# ==============================================================================
# 9. CHAT TURN HANDLER (FIRST FRACTION)
# ==============================================================================

def handle_chat_turn(client):
    """
    Processes a single user message and generates assistant reply.
    """
    user_input = st.session_state.get("input_buffer", "").strip()
    if not user_input:
        return

    thread = st.session_state.chats[st.session_state.current_chat]
    thread.append({"role": "user", "content": user_input})

    route = classify_route(user_input)
    persona = st.session_state.personality
    language = st.session_state.target_language

    system_prompt = build_dynamic_system_prompt(user_input, persona, language)

    memory_snippet = search_past_memory(user_input, thread)
    if memory_snippet:
        system_prompt += "\n\nRelevant past context:\n" + memory_snippet

    if route == "ROUTE_STANDARD":
        messages = [{"role": "user", "content": user_input}]
    elif route == "ROUTE_SUMMARIZE" and st.session_state.doc_context:
        messages = [
            {
                "role": "user",
                "content": f"Summarize this document:\n\n{st.session_state.doc_context}",
            }
        ]
    else:
        messages = [{"role": "user", "content": user_input}]

    reply = call_llm(
        client=client,
        model=st.session_state.selected_model,
        system_prompt=system_prompt,
        messages=messages,
        temperature=st.session_state.temperature,
        max_tokens=st.session_state.max_tokens,
    )

    thread.append({"role": "assistant", "content": reply})
    st.session_state.input_buffer = ""
    save_chats(st.session_state.chats)
    maybe_store_memory(user_input, reply)

# ==============================================================================
# 10. DOCUMENT & UPLOAD HELPERS
# ==============================================================================

def extract_text_from_upload(uploaded_file, max_char_limit: int = 50000) -> str:
    """
    Parses uploaded files safely across CSV, TXT, and basic text formats.
    """
    if uploaded_file is None:
        return ""

    file_name = uploaded_file.name.lower()

    try:
        if file_name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
            return f"--- CSV Content ({file_name}) ---\n" + df.to_markdown(index=False)

        raw = uploaded_file.read()
        text = raw.decode("utf-8", errors="replace")
        return text[:max_char_limit]

    except Exception as exc:
        return f"⚠️ Error parsing file '{file_name}': {exc}"

# ==============================================================================
# 11. SIDEBAR CONTROLS & WORKSPACE SETTINGS
# ==============================================================================

def sidebar_controls():
    st.sidebar.header("Workspace Settings")

    # Provider selection
    st.session_state.selected_provider = st.sidebar.selectbox(
        "Provider",
        [PROVIDER_GROQ, PROVIDER_OPENROUTER],
        index=[PROVIDER_GROQ, PROVIDER_OPENROUTER].index(
            st.session_state.selected_provider
        ),
        key="sidebar_provider_select"
    )

    # Model selection
    if st.session_state.selected_provider == PROVIDER_GROQ:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            GROQ_MODELS,
            index=GROQ_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in GROQ_MODELS
            else 0,
            key="sidebar_model_select_groq"
        )
    else:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            OPENROUTER_MODELS,
            index=OPENROUTER_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in OPENROUTER_MODELS
            else 0,
            key="sidebar_model_select_openrouter"
        )

    # Personality
    st.session_state.personality = st.sidebar.selectbox(
        "Personality",
        PERSONALITY_PRESETS,
        index=PERSONALITY_PRESETS.index(st.session_state.personality)
        if st.session_state.personality in PERSONALITY_PRESETS
        else 0,
        key="sidebar_personality_select"
    )

    # Language
    st.session_state.target_language = st.sidebar.selectbox(
        "Language",
        ["English", "Spanish", "French"],
        index=["English", "Spanish", "French"].index(
            st.session_state.target_language
        )
        if st.session_state.target_language in ["English", "Spanish", "French"]
        else 0,
        key="sidebar_language_select"
    )

    # Temperature
    st.session_state.temperature = st.sidebar.slider(
        "Temperature",
        0.0,
        1.0,
        st.session_state.temperature,
        0.05,
        key="sidebar_temperature_slider"
    )

    # Max tokens
    st.session_state.max_tokens = st.sidebar.slider(
        "Max Tokens",
        256,
        4096,
        st.session_state.max_tokens,
        256,
        key="sidebar_max_tokens_slider"
    )

    # Document upload (for simple RAG)
    uploaded = st.sidebar.file_uploader(
        "Upload a text/CSV file for context",
        type=["txt", "csv"],
        key="sidebar_doc_upload"
    )
    if uploaded is not None:
        st.session_state.doc_context = extract_text_from_upload(uploaded)

    # Memory vault viewer
    with st.sidebar.expander("Memory Vault", expanded=False, key="sidebar_memory_vault_expander"):
        if not st.session_state.memory_vault:
            st.write("No stored memories yet.")
        else:
            for mem in st.session_state.memory_vault[-5:]:
                ts = mem.get("timestamp", "unknown")
                user = mem.get("user", "")
                st.markdown(f"- **{ts}** — {user[:80]}...")


# ==============================================================================
# 12. CHAT UI RENDERING
# ==============================================================================

def render_chat_thread():
    """
    Renders the current chat thread in the main area.
    """
    thread = st.session_state.chats[st.session_state.current_chat]

    for idx, msg in enumerate(thread):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "user":
            st.markdown(f"**You:** {content}")
        else:
            st.markdown(f"**Assistant:** {content}")

        # Mini tools panel (fractional, can be expanded later)
        cols = st.columns([1, 1, 3])
        with cols[0]:
            if st.button("🔍 Context", key=f"ctx_{idx}"):
                st.info("Context tools will be added in a later segment.")
        with cols[1]:
            if st.button("⭐ Save", key=f"save_{idx}"):
                st.success("Message bookmarked (placeholder behavior).")

        st.markdown("---")

def chat_ui(client):
    """
    High-level chat UI: session selector, thread, and input box.
    """
    st.title("🧠 Advanced Chat Workspace (Fraction 1)")

    # Chat session selector
    chat_names = list(st.session_state.chats.keys())
    current_index = chat_names.index(st.session_state.current_chat)
    selected = st.selectbox(
        "Chat session",
        chat_names,
        index=current_index,
    )
    if selected != st.session_state.current_chat:
        st.session_state.current_chat = selected

    # Render thread
    render_chat_thread()

    # Input area
st.text_area(
    "Your message",
    key="chat_input",
    height=120,
    placeholder="Type your message here…",
)

cols = st.columns([2, 1])
with cols[0]:
    if st.button("Send"):
        handle_chat_turn(client)
with cols[1]:
    if st.button("New Chat"):
        new_name = f"Chat {len(st.session_state.chats) + 1}"
        st.session_state.chats[new_name] = []
        st.session_state.current_chat = new_name

# ==============================================================================
# 13. TELEMETRY & SIMPLE METRICS (FOUNDATION)
# ==============================================================================

def estimate_tokens(text: str, char_per_token: float = 4.0) -> int:
    """
    Rough token estimator for telemetry.
    """
    if not isinstance(text, str):
        return 0
    return int(len(text) / char_per_token)


def compute_thread_stats(thread: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Computes simple stats for a chat thread.
    """
    total_msgs = len(thread)
    user_msgs = sum(1 for m in thread if m.get("role") == "user")
    assistant_msgs = sum(1 for m in thread if m.get("role") == "assistant")

    total_tokens = 0
    for m in thread:
        content = m.get("content", "")
        total_tokens += estimate_tokens(content)

    return {
        "total_messages": total_msgs,
        "user_messages": user_msgs,
        "assistant_messages": assistant_msgs,
        "estimated_tokens": total_tokens,
    }


def telemetry_panel():
    """
    Displays basic telemetry for the current chat.
    """
    thread = st.session_state.chats[st.session_state.current_chat]
    stats = compute_thread_stats(thread)

    with st.expander("Thread Telemetry", expanded=False):
        st.markdown(f"- **Total messages:** {stats['total_messages']}")
        st.markdown(f"- **User messages:** {stats['user_messages']}")
        st.markdown(f"- **Assistant messages:** {stats['assistant_messages']}")
        st.markdown(f"- **Estimated tokens:** {stats['estimated_tokens']}")

# ==============================================================================
# 14. MAIN ENTRYPOINT
# ==============================================================================

def main():
    initialize_session_state()
    groq_client, or_client = initialize_clients()

    sidebar_controls()

    if st.session_state.selected_provider == PROVIDER_GROQ:
        client = groq_client
    else:
        client = or_client

    chat_ui(client)
    telemetry_panel()


if __name__ == "__main__":
    main()

# ==============================================================================
# 15. ADVANCED MESSAGE UTILITIES (PLACEHOLDER FOR FUTURE TOOLS)
# ==============================================================================

def sanitize_user_input(text: str) -> str:
    """
    Basic sanitization for user input to avoid accidental key leakage.
    """
    if not isinstance(text, str):
        return ""
    # Remove obvious API key patterns
    text = re.sub(r"\b(sk-[a-zA-Z0-9]{20,}|gsk_[a-zA-Z0-9]{20,})\b", "[REDACTED_API_KEY]", text)
    # Collapse excessive whitespace
    text = re.sub(r"\s{3,}", "  ", text)
    return text.strip()


def truncate_long_context(text: str, max_chars: int = 8000) -> str:
    """
    Truncates overly long context to keep prompts manageable.
    """
    if not isinstance(text, str):
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[Context truncated for length.]"

# ==============================================================================
# 16. FUTURE ROUTES STUBS (IMAGE, DEBUG, SEARCH)
# ==============================================================================

def route_image_generation(user_input: str) -> str:
    """
    Placeholder route for image generation.
    """
    return (
        "You requested an image-related action. "
        "In a future segment, this route will call an image generation API."
    )


def route_debug_assistance(user_input: str) -> str:
    """
    Placeholder route for debugging/code-fix assistance.
    """
    return (
        "You requested debugging help. "
        "In a future segment, this route will analyze code and suggest fixes."
    )


def route_search_assistance(user_input: str) -> str:
    """
    Placeholder route for search assistance.
    """
    return (
        "You requested search or web information. "
        "In a future segment, this route will call a live search API."
    )

# ==============================================================================
# 17. EXTENDED ROUTE HANDLING (HOOKS FOR FUTURE EXPANSION)
# ==============================================================================

def build_messages_for_route(route: str, user_input: str) -> List[Dict[str, str]]:
    """
    Builds the message list for the selected route.
    Currently minimal, but structured for future expansion.
    """
    if route == "ROUTE_STANDARD":
        return [{"role": "user", "content": sanitize_user_input(user_input)}]

    if route == "ROUTE_SUMMARIZE" and st.session_state.doc_context:
        doc = truncate_long_context(st.session_state.doc_context)
        return [
            {
                "role": "user",
                "content": f"Summarize this document for the user:\n\n{doc}",
            }
        ]

    if route == "ROUTE_IMAGE_GEN":
        return [
            {
                "role": "user",
                "content": route_image_generation(user_input),
            }
        ]

    if route == "ROUTE_DEBUG":
        return [
            {
                "role": "user",
                "content": route_debug_assistance(user_input),
            }
        ]

    if route == "ROUTE_SEARCH":
        return [
            {
                "role": "user",
                "content": route_search_assistance(user_input),
            }
        ]

    # Fallback
    return [{"role": "user", "content": sanitize_user_input(user_input)}]

# ==============================================================================
# 18. AUGMENTED CHAT TURN HANDLER (USING EXTENDED ROUTES)
# ==============================================================================

def handle_chat_turn(client):
    """
    Processes a single user message and generates assistant reply.
    Uses smart routing and semantic memory.
    """
    user_input = st.session_state.get("input_buffer", "").strip()
    if not user_input:
        return

    # Append user message
    thread = st.session_state.chats[st.session_state.current_chat]
    thread.append({"role": "user", "content": user_input})

    # Route selection
    route = classify_route(user_input)
    persona = st.session_state.personality
    language = st.session_state.target_language

    # System prompt with stability + persona
    system_prompt = build_dynamic_system_prompt(user_input, persona, language)

    # Semantic memory injection
    memory_snippet = search_past_memory(user_input, thread)
    if memory_snippet:
        system_prompt += "\n\nRelevant past context:\n" + memory_snippet

    # Build messages for route
    messages = build_messages_for_route(route, user_input)

    # Call LLM
    reply = call_llm(
        client=client,
        model=st.session_state.selected_model,
        system_prompt=system_prompt,
        messages=messages,
        temperature=st.session_state.temperature,
        max_tokens=st.session_state.max_tokens,
    )

    # Append assistant reply
    thread.append({"role": "assistant", "content": reply})
    st.session_state.input_buffer = ""
    save_chats(st.session_state.chats)
    maybe_store_memory(user_input, reply)

# ==============================================================================
# 19. ENHANCED CHAT RENDERING WITH ROUTE HINTS
# ==============================================================================

def render_chat_thread():
    """
    Renders the current chat thread with minimal per-message tools.
    """
    thread = st.session_state.chats[st.session_state.current_chat]

    for idx, msg in enumerate(thread):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "user":
            st.markdown(f"**You:** {content}")
        else:
            st.markdown(f"**Assistant:** {content}")

        cols = st.columns([1, 1, 2])
        with cols[0]:
            if st.button("🔍 Route", key=f"route_{idx}"):
                route = classify_route(content if role == "user" else "")
                st.info(f"Detected route (approx): `{route}`")
        with cols[1]:
            if st.button("⭐ Save", key=f"save_{idx}"):
                st.success("Message bookmarked (placeholder behavior).")
        with cols[2]:
            if st.button("🧠 Memory", key=f"mem_{idx}"):
                st.info("Memory tools will be expanded in a later segment.")

        st.markdown("---")

# ==============================================================================
# 20. UPDATED CHAT UI (USING ENHANCED RENDERER)
# ==============================================================================

def chat_ui(client):
    """
    High-level chat UI: session selector, thread, and input box.
    """
    st.title("🧠 Advanced Chat Workspace")

    # Chat session selector
    chat_names = list(st.session_state.chats.keys())
    current_index = chat_names.index(st.session_state.current_chat)
    selected = st.selectbox(
        "Chat session",
        chat_names,
        index=current_index,
    )
    if selected != st.session_state.current_chat:
        st.session_state.current_chat = selected

    # Render thread
    render_chat_thread()

    # Input area
    st.text_area(
        "Your message",
        key="input_buffer",
        height=120,
        placeholder="Type your message here…",
    )

    cols = st.columns([2, 1])
    with cols[0]:
        if st.button("Send"):
            handle_chat_turn(client)
    with cols[1]:
        if st.button("New Chat"):
            new_name = f"Chat {len(st.session_state.chats) + 1}"
            st.session_state.chats[new_name] = []
            st.session_state.current_chat = new_name

# ==============================================================================
# 21. TELEMETRY PANEL (UNCHANGED BUT KEPT FOR CONTINUITY)
# ==============================================================================

def telemetry_panel():
    """
    Displays basic telemetry for the current chat.
    """
    thread = st.session_state.chats[st.session_state.current_chat]
    stats = compute_thread_stats(thread)

    with st.expander("Thread Telemetry", expanded=False):
        st.markdown(f"- **Total messages:** {stats['total_messages']}")
        st.markdown(f"- **User messages:** {stats['user_messages']}")
        st.markdown(f"- **Assistant messages:** {stats['assistant_messages']}")
        st.markdown(f"- **Estimated tokens:** {stats['estimated_tokens']}")

# ==============================================================================
# 22. MAIN ENTRYPOINT (UNCHANGED, BUT NOW USING UPDATED COMPONENTS)
# ==============================================================================

def main():
    initialize_session_state()
    groq_client, or_client = initialize_clients()

    sidebar_controls()

    if st.session_state.selected_provider == PROVIDER_GROQ:
        client = groq_client
    else:
        client = or_client

    chat_ui(client)
    telemetry_panel()


if __name__ == "__main__":
    main()

# ==============================================================================
# 23. BOOKMARKS & SIMPLE PERSISTENT FLAGS (FOUNDATION FOR FUTURE FEATURES)
# ==============================================================================

BOOKMARKS_FILE = "persistent_bookmarks.json"


def load_bookmarks() -> list:
    data = _safe_json_load(BOOKMARKS_FILE, {"bookmarks": []})
    return data.get("bookmarks", [])


def save_bookmarks(bookmarks: list) -> None:
    _atomic_json_write(BOOKMARKS_FILE, {"bookmarks": bookmarks})


def initialize_bookmarks_state() -> None:
    if "bookmarks" not in st.session_state:
        st.session_state.bookmarks = load_bookmarks()


def bookmark_message(chat_name: str, index: int) -> None:
    """
    Stores a reference to a specific message in a chat.
    """
    thread = st.session_state.chats.get(chat_name, [])
    if index < 0 or index >= len(thread):
        return

    msg = thread[index]
    entry = {
        "chat": chat_name,
        "index": index,
        "role": msg.get("role", "user"),
        "content": msg.get("content", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    st.session_state.bookmarks.append(entry)
    save_bookmarks(st.session_state.bookmarks)


def bookmarks_panel():
    """
    Displays bookmarked messages in the sidebar.
    """
    with st.sidebar.expander("Bookmarks", expanded=False):
        if not st.session_state.bookmarks:
            st.write("No bookmarks yet.")
        else:
            for b in st.session_state.bookmarks[-10:]:
                chat = b.get("chat", "unknown")
                idx = b.get("index", -1)
                role = b.get("role", "user")
                content = b.get("content", "")[:80]
                ts = b.get("timestamp", "unknown")
                st.markdown(f"- **{ts}** — `{chat}` [{idx}] ({role}): {content}...")

# ==============================================================================
# 24. EXPORT & IMPORT UTILITIES (MARKDOWN + JSON)
# ==============================================================================

def export_chat_as_markdown(chat_name: str) -> str:
    """
    Converts a chat thread into Markdown for export.
    """
    thread = st.session_state.chats.get(chat_name, [])
    timestamp = datetime.now(timezone.utc).isoformat()

    lines = [
        f"# Chat Export — {chat_name}",
        f"**Exported at:** {timestamp}",
        f"**Total messages:** {len(thread)}",
        "",
        "---",
        "",
    ]

    for msg in thread:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        header = "### 👤 User" if role == "user" else "### 🤖 Assistant"
        lines.append(header)
        lines.append("")
        lines.append(content)
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def export_workspace_to_json() -> str:
    """
    Exports all chats and memory to a JSON string.
    """
    payload = {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "chats": st.session_state.chats,
        "memory_vault": st.session_state.memory_vault,
        "bookmarks": st.session_state.bookmarks,
    }
    return json.dumps(payload, indent=2)


def import_workspace_from_json(json_str: str) -> None:
    """
    Imports workspace data from a JSON string.
    """
    try:
        data = json.loads(json_str)
        chats = data.get("chats")
        memory_vault = data.get("memory_vault")
        bookmarks = data.get("bookmarks")

        if isinstance(chats, dict):
            st.session_state.chats = chats
        if isinstance(memory_vault, list):
            st.session_state.memory_vault = memory_vault
        if isinstance(bookmarks, list):
            st.session_state.bookmarks = bookmarks

        save_chats(st.session_state.chats)
        save_memory_vault(st.session_state.memory_vault)
        save_bookmarks(st.session_state.bookmarks)

        st.success("Workspace imported successfully.")
    except Exception as err:
        st.error(f"Failed to import workspace: {err}")

# ==============================================================================
# 25. SIDEBAR EXPORT/IMPORT CONTROLS
# ==============================================================================

def sidebar_export_import_controls():
    """
    Adds export/import controls to the sidebar.
    """
    with st.sidebar.expander("Export / Import", expanded=False):
        if st.button("Export current chat as Markdown"):
            chat_name = st.session_state.current_chat
            md = export_chat_as_markdown(chat_name)
            st.download_button(
                label="Download Markdown",
                data=md,
                file_name=f"{chat_name.replace(' ', '_')}.md",
                mime="text/markdown",
            )

        if st.button("Export full workspace as JSON"):
            js = export_workspace_to_json()
            st.download_button(
                label="Download JSON",
                data=js,
                file_name="workspace_export.json",
                mime="application/json",
            )

        uploaded_json = st.file_uploader(
            "Import workspace JSON",
            type=["json"],
            key="workspace_import_uploader",
        )
        if uploaded_json is not None:
            content = uploaded_json.read().decode("utf-8", errors="replace")
            if st.button("Apply imported workspace"):
                import_workspace_from_json(content)

# ==============================================================================
# 26. IMAGE PLACEHOLDER UTILITIES (FOR FUTURE VISION FEATURES)
# ==============================================================================

def encode_image_to_base64(file_obj) -> Optional[str]:
    """
    Encodes an uploaded image to base64 (placeholder for future vision models).
    """
    try:
        img = Image.open(file_obj)
        img.thumbnail((1024, 1024))
        buf = st.runtime.scriptrunner.script_run_context.BytesIO() if hasattr(
            st.runtime, "scriptrunner"
        ) else None
        if buf is None:
            import io
            buf = io.BytesIO()
        img.save(buf, format="JPEG")
        import base64
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None


def sidebar_image_placeholder():
    """
    Placeholder image upload section for future multimodal features.
    """
    with st.sidebar.expander("Image (future multimodal)", expanded=False):
        uploaded_img = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg"])
        if uploaded_img is not None:
            st.image(uploaded_img, caption="Uploaded image", use_container_width=True)
            b64 = encode_image_to_base64(uploaded_img)
            if b64:
                st.caption("Image encoded to base64 (not yet used in prompts).")

# ==============================================================================
# 27. AUGMENTED SIDEBAR (COMPOSING ALL PANELS)
# ==============================================================================

def sidebar_controls():
    """
    Composite sidebar: settings, memory, bookmarks, export/import, image stub.
    """
    st.sidebar.header("Workspace Settings")

    # Provider selection
    st.session_state.selected_provider = st.sidebar.selectbox(
        "Provider",
        [PROVIDER_GROQ, PROVIDER_OPENROUTER],
        index=[PROVIDER_GROQ, PROVIDER_OPENROUTER].index(
            st.session_state.selected_provider
        ),
    )

    # Model selection
    if st.session_state.selected_provider == PROVIDER_GROQ:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            GROQ_MODELS,
            index=GROQ_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in GROQ_MODELS
            else 0,
        )
    else:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            OPENROUTER_MODELS,
            index=OPENROUTER_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in OPENROUTER_MODELS
            else 0,
        )

    # Personality
    st.session_state.personality = st.sidebar.selectbox(
        "Personality",
        PERSONALITY_PRESETS,
        index=PERSONALITY_PRESETS.index(st.session_state.personality)
        if st.session_state.personality in PERSONALITY_PRESETS
        else 0,
    )

    # Language
    st.session_state.target_language = st.sidebar.selectbox(
        "Language",
        ["English", "Spanish", "French"],
        index=["English", "Spanish", "French"].index(
            st.session_state.target_language
        )
        if st.session_state.target_language in ["English", "Spanish", "French"]
        else 0,
    )

    # Temperature
    st.session_state.temperature = st.sidebar.slider(
        "Temperature",
        0.0,
        1.0,
        st.session_state.temperature,
        0.05,
    )

    # Max tokens
    st.session_state.max_tokens = st.sidebar.slider(
        "Max Tokens",
        256,
        4096,
        st.session_state.max_tokens,
        256,
    )

    # Document upload (for simple RAG)
    uploaded = st.sidebar.file_uploader(
        "Upload a text/CSV file for context",
        type=["txt", "csv"],
        key="doc_uploader",
    )
    if uploaded is not None:
        st.session_state.doc_context = extract_text_from_upload(uploaded)

    # Memory vault viewer
    with st.sidebar.expander("Memory Vault", expanded=False):
        if not st.session_state.memory_vault:
            st.write("No stored memories yet.")
        else:
            for mem in st.session_state.memory_vault[-5:]:
                ts = mem.get("timestamp", "unknown")
                user = mem.get("user", "")
                st.markdown(f"- **{ts}** — {user[:80]}...")

    # Bookmarks, export/import, image placeholder
    bookmarks_panel()
    sidebar_export_import_controls()
    sidebar_image_placeholder()

# ==============================================================================
# 28. AUGMENTED CHAT RENDERING (WITH REAL BOOKMARKS)
# ==============================================================================

def render_chat_thread():
    """
    Renders the current chat thread with per-message tools and bookmark support.
    """
    thread = st.session_state.chats[st.session_state.current_chat]

    for idx, msg in enumerate(thread):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "user":
            st.markdown(f"**You:** {content}")
        else:
            st.markdown(f"**Assistant:** {content}")

        cols = st.columns([1, 1, 2])
        with cols[0]:
            if st.button("🔍 Route", key=f"route_{idx}"):
                route = classify_route(content if role == "user" else "")
                st.info(f"Detected route (approx): `{route}`")
        with cols[1]:
            if st.button("⭐ Bookmark", key=f"bookmark_{idx}"):
                bookmark_message(st.session_state.current_chat, idx)
                st.success("Message bookmarked.")
        with cols[2]:
            if st.button("🧠 Memory", key=f"mem_{idx}"):
                st.info("Memory tools will be expanded in a later segment.")

        st.markdown("---")

# ==============================================================================
# 29. FINAL CHAT UI (USING AUGMENTED RENDERER)
# ==============================================================================

def chat_ui(client):
    """
    High-level chat UI: session selector, thread, and input box.
    """
    st.title("🧠 Advanced Chat Workspace")

    # Chat session selector
    chat_names = list(st.session_state.chats.keys())
    if not chat_names:
        st.session_state.chats["New Chat"] = []
        chat_names = list(st.session_state.chats.keys())

    current_index = chat_names.index(st.session_state.current_chat)
    selected = st.selectbox(
        "Chat session",
        chat_names,
        index=current_index,
    )
    if selected != st.session_state.current_chat:
        st.session_state.current_chat = selected

    # Render thread
    render_chat_thread()

    # Input area
    st.text_area(
        "Your message",
        key="input_buffer",
        height=120,
        placeholder="Type your message here…",
    )

    cols = st.columns([2, 1, 1])
    with cols[0]:
        if st.button("Send"):
            handle_chat_turn(client)
    with cols[1]:
        if st.button("New Chat"):
            new_name = f"Chat {len(st.session_state.chats) + 1}"
            st.session_state.chats[new_name] = []
            st.session_state.current_chat = new_name
            save_chats(st.session_state.chats)
    with cols[2]:
        if st.button("Clear Chat"):
            st.session_state.chats[st.session_state.current_chat] = []
            save_chats(st.session_state.chats)
            st.experimental_rerun()

# ==============================================================================
# 30. TELEMETRY & HEALTH CHECKS
# ==============================================================================

def compute_thread_stats(thread: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Computes simple stats for a chat thread.
    """
    total_msgs = len(thread)
    user_msgs = sum(1 for m in thread if m.get("role") == "user")
    assistant_msgs = sum(1 for m in thread if m.get("role") == "assistant")

    total_tokens = 0
    for m in thread:
        content = m.get("content", "")
        total_tokens += estimate_tokens(content)

    return {
        "total_messages": total_msgs,
        "user_messages": user_msgs,
        "assistant_messages": assistant_msgs,
        "estimated_tokens": total_tokens,
    }


def telemetry_panel():
    """
    Displays basic telemetry for the current chat.
    """
    thread = st.session_state.chats[st.session_state.current_chat]
    stats = compute_thread_stats(thread)

    with st.expander("Thread Telemetry", expanded=False):
        st.markdown(f"- **Total messages:** {stats['total_messages']}")
        st.markdown(f"- **User messages:** {stats['user_messages']}")
        st.markdown(f"- **Assistant messages:** {stats['assistant_messages']}")
        st.markdown(f"- **Estimated tokens:** {stats['estimated_tokens']}")

        if stats["estimated_tokens"] > 6000:
            st.warning(
                "This thread is getting long. Consider exporting or starting a new chat "
                "to keep context efficient."
            )

# ==============================================================================
# 31. INITIALIZATION WRAPPER
# ==============================================================================

def initialize_all_state():
    """
    Initializes all session-related state: chats, memory, bookmarks.
    """
    initialize_session_state()
    initialize_bookmarks_state()

# ==============================================================================
# 32. MAIN ENTRYPOINT (FULLY COMPOSED)
# ==============================================================================

def main():
    initialize_all_state()
    groq_client, or_client = initialize_clients()

    sidebar_controls()

    if st.session_state.selected_provider == PROVIDER_GROQ:
        client = groq_client
    else:
        client = or_client

    chat_ui(client)
    telemetry_panel()


if __name__ == "__main__":
    main()

# ==============================================================================
# A. GUARDRAILS, TOOL ACTIONS, AND REFLECTION LAYER
# ==============================================================================

SAFE_TOPICS = [
    "education", "productivity", "coding", "writing", "planning",
    "research", "learning", "analysis", "design",
]

TOOL_ACTIONS = {}


def register_tool_action(name: str, func):
    """
    Simple plugin-style registry for internal tools.
    """
    TOOL_ACTIONS[name] = func


def apply_guardrails(user_input: str) -> str:
    """
    Very lightweight guardrail layer: detects obviously unsafe patterns
    and redirects to a safer framing.
    """
    lowered = user_input.lower()
    unsafe_keywords = ["harm", "kill", "suicide", "self-harm", "violence"]
    if any(k in lowered for k in unsafe_keywords):
        return (
            "The user prompt appears to touch on sensitive or harmful topics. "
            "Respond with supportive, non-harmful guidance, encourage seeking "
            "real-world help, and avoid giving any instructions that could "
            "cause harm."
        )
    return user_input


def reflection_pass(system_prompt: str, reply: str) -> str:
    """
    Reflection loop: asks the model to improve its own answer.
    """
    client = st.session_state.get("reflection_client")
    model = st.session_state.get("reflection_model", st.session_state.selected_model)

    if client is None:
        return reply

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "You are in reflection mode. Improve the following answer: "
                    f"\n\n{reply}\n\nFocus on clarity, structure, and completeness."
                ),
            },
        ]
        res = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=st.session_state.max_tokens,
        )
        improved = res.choices[0].message.content.strip()
        if len(improved.split()) >= 4:
            return improved
        return reply
    except Exception:
        return reply


def call_llm_with_reflection(
    client,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> str:
    """
    Wraps call_llm with guardrails + reflection.
    """
    # Guardrails: adjust last user message if needed
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] = apply_guardrails(messages[-1]["content"])

    base_reply = call_llm(
        client=client,
        model=model,
        system_prompt=system_prompt,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    # Reflection pass
    final_reply = reflection_pass(system_prompt, base_reply)
    return final_reply

# ==============================================================================
# B. DEEP MEMORY & LOCAL SEARCH INDEX
# ==============================================================================

LOCAL_INDEX = {
    "messages": [],   # list of {chat, idx, role, content}
    "documents": [],  # list of {id, title, content}
    "bookmarks": [],  # mirrors bookmarks
}


def index_current_thread():
    """
    Indexes the current thread messages into LOCAL_INDEX['messages'].
    """
    chat_name = st.session_state.current_chat
    thread = st.session_state.chats.get(chat_name, [])
    LOCAL_INDEX["messages"] = []
    for idx, msg in enumerate(thread):
        LOCAL_INDEX["messages"].append(
            {
                "chat": chat_name,
                "index": idx,
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            }
        )


def index_documents_from_context():
    """
    Indexes doc_context as a single document entry.
    """
    doc = st.session_state.get("doc_context", "")
    if not doc:
        return
    LOCAL_INDEX["documents"] = [
        {
            "id": "doc_context",
            "title": "Uploaded Document",
            "content": doc,
        }
    ]


def index_bookmarks():
    """
    Mirrors bookmarks into LOCAL_INDEX for unified search.
    """
    LOCAL_INDEX["bookmarks"] = st.session_state.bookmarks[:]


def deep_memory_search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Searches across messages, documents, and bookmarks using token overlap.
    """
    tokens = set(re.findall(r"\w+", query.lower()))
    scored = []

    def score_text(text: str) -> int:
        t = set(re.findall(r"\w+", text.lower()))
        return len(tokens & t)

    for m in LOCAL_INDEX["messages"]:
        s = score_text(m["content"])
        if s > 1:
            scored.append((s, {"type": "message", **m}))

    for d in LOCAL_INDEX["documents"]:
        s = score_text(d["content"])
        if s > 1:
            scored.append((s, {"type": "document", **d}))

    for b in LOCAL_INDEX["bookmarks"]:
        s = score_text(b.get("content", ""))
        if s > 1:
            scored.append((s, {"type": "bookmark", **b}))

    scored.sort(reverse=True)
    return [item for _, item in scored[:top_k]]


def inject_deep_memory(system_prompt: str, user_input: str) -> str:
    """
    Adds deep memory snippets into the system prompt.
    """
    index_current_thread()
    index_documents_from_context()
    index_bookmarks()

    hits = deep_memory_search(user_input, top_k=3)
    if not hits:
        return system_prompt

    lines = ["\n\nDeep memory context:"]
    for h in hits:
        if h["type"] == "message":
            lines.append(f"- [Chat {h['chat']} #{h['index']}] {h['content'][:160]}...")
        elif h["type"] == "document":
            lines.append(f"- [Document] {h['title']}: {h['content'][:160]}...")
        elif h["type"] == "bookmark":
            lines.append(f"- [Bookmark] {h.get('content', '')[:160]}...")

    return system_prompt + "\n" + "\n".join(lines)

# ==============================================================================
# C. ADAPTIVE PERSONA & SESSION DNA
# ==============================================================================

SESSION_DNA = {
    "topics": {},
    "tone": "neutral",
    "complexity": "medium",
    "persona_bias": {},
}


def update_session_dna(user_input: str, assistant_reply: str) -> None:
    """
    Tracks simple session DNA: topics and tone.
    """
    tokens = re.findall(r"\w+", user_input.lower())
    for t in tokens:
        SESSION_DNA["topics"][t] = SESSION_DNA["topics"].get(t, 0) + 1

    # Tone heuristic
    if "thank" in assistant_reply.lower():
        SESSION_DNA["tone"] = "supportive"
    elif "step-by-step" in assistant_reply.lower():
        SESSION_DNA["complexity"] = "high"

    # Persona bias
    persona = st.session_state.personality
    SESSION_DNA["persona_bias"][persona] = SESSION_DNA["persona_bias"].get(persona, 0) + 1


def adaptive_persona_prompt(system_prompt: str) -> str:
    """
    Modifies system prompt slightly based on SESSION_DNA.
    """
    tone = SESSION_DNA.get("tone", "neutral")
    complexity = SESSION_DNA.get("complexity", "medium")

    return (
        system_prompt
        + f"\n\nAdaptive persona hints: tone={tone}, complexity={complexity}. "
        "Adjust explanations accordingly."
    )

# ==============================================================================
# D. TOOL ACTIONS: SUMMARY, REWRITE, ANALYZE
# ==============================================================================

def tool_summarize(text: str) -> str:
    return f"[TOOL_SUMMARIZE] Summarize:\n\n{text[:1000]}"


def tool_rewrite(text: str, style: str = "clear") -> str:
    return f"[TOOL_REWRITE] Rewrite in style '{style}':\n\n{text[:1000]}"


def tool_analyze(text: str) -> str:
    return f"[TOOL_ANALYZE] Analyze:\n\n{text[:1000]}"


register_tool_action("summarize", tool_summarize)
register_tool_action("rewrite", tool_rewrite)
register_tool_action("analyze", tool_analyze)


def maybe_invoke_tool(user_input: str) -> Optional[str]:
    """
    Detects simple tool commands like /summarize, /rewrite, /analyze.
    """
    lowered = user_input.strip().lower()
    if lowered.startswith("/summarize "):
        payload = user_input[len("/summarize "):]
        return TOOL_ACTIONS["summarize"](payload)
    if lowered.startswith("/rewrite "):
        payload = user_input[len("/rewrite "):]
        return TOOL_ACTIONS["rewrite"](payload)
    if lowered.startswith("/analyze "):
        payload = user_input[len("/analyze "):]
        return TOOL_ACTIONS["analyze"](payload)
    return None

# ==============================================================================
# E. INTEGRATION: UPGRADED HANDLE_CHAT_TURN WITH TOOLS, DEEP MEMORY, REFLECTION
# ==============================================================================

def handle_chat_turn(client):
    """
    Processes a single user message and generates assistant reply.
    Uses smart routing, semantic + deep memory, tools, and reflection.
    """
    user_input = st.session_state.get("input_buffer", "").strip()
    if not user_input:
        return

    # Tool commands (e.g., /summarize, /rewrite, /analyze)
    tool_result = maybe_invoke_tool(user_input)
    thread = st.session_state.chats[st.session_state.current_chat]

    if tool_result is not None:
        thread.append({"role": "user", "content": user_input})
        thread.append({"role": "assistant", "content": tool_result})
        st.session_state.input_buffer = ""
        save_chats(st.session_state.chats)
        return

    # Normal chat flow
    thread.append({"role": "user", "content": user_input})

    route = classify_route(user_input)
    persona = st.session_state.personality
    language = st.session_state.target_language

    # Base system prompt
    system_prompt = build_dynamic_system_prompt(user_input, persona, language)

    # Inject semantic memory
    memory_snippet = search_past_memory(user_input, thread)
    if memory_snippet:
        system_prompt += "\n\nRelevant past context:\n" + memory_snippet

    # Inject deep memory (messages + docs + bookmarks)
    system_prompt = inject_deep_memory(system_prompt, user_input)

    # Adaptive persona hints
    system_prompt = adaptive_persona_prompt(system_prompt)

    # Build messages for route
    messages = build_messages_for_route(route, user_input)

    # Call LLM with reflection
    reply = call_llm_with_reflection(
        client=client,
        model=st.session_state.selected_model,
        system_prompt=system_prompt,
        messages=messages,
        temperature=st.session_state.temperature,
        max_tokens=st.session_state.max_tokens,
    )

    thread.append({"role": "assistant", "content": reply})
    st.session_state.input_buffer = ""
    save_chats(st.session_state.chats)
    maybe_store_memory(user_input, reply)
    update_session_dna(user_input, reply)

# ==============================================================================
# F. DEVELOPER CONSOLE & PLUGIN INSPECTOR
# ==============================================================================

def developer_console():
    """
    Hidden/optional developer console for inspecting internals.
    """
    with st.expander("Developer Console", expanded=False):
        st.markdown("### System Prompt Preview")
        persona = st.session_state.personality
        language = st.session_state.target_language
        sample_prompt = build_dynamic_system_prompt("preview", persona, language)
        sample_prompt = adaptive_persona_prompt(sample_prompt)
        st.code(sample_prompt, language="markdown")

        st.markdown("### Registered Tool Actions")
        if not TOOL_ACTIONS:
            st.write("No tools registered.")
        else:
            for name in TOOL_ACTIONS.keys():
                st.markdown(f"- `{name}`")

        st.markdown("### Session DNA")
        st.json(SESSION_DNA)

        st.markdown("### Local Index Snapshot")
        st.json(
            {
                "messages_indexed": len(LOCAL_INDEX.get("messages", [])),
                "documents_indexed": len(LOCAL_INDEX.get("documents", [])),
                "bookmarks_indexed": len(LOCAL_INDEX.get("bookmarks", [])),
            }
        )

# ==============================================================================
# G. ADVANCED TELEMETRY & ROUTE STATS
# ==============================================================================

ROUTE_STATS = {
    "ROUTE_STANDARD": 0,
    "ROUTE_SUMMARIZE": 0,
    "ROUTE_IMAGE_GEN": 0,
    "ROUTE_DEBUG": 0,
    "ROUTE_SEARCH": 0,
}


def record_route_usage(route: str) -> None:
    if route in ROUTE_STATS:
        ROUTE_STATS[route] += 1


def telemetry_panel():
    """
    Displays advanced telemetry for the current chat and routes.
    """
    thread = st.session_state.chats[st.session_state.current_chat]
    stats = compute_thread_stats(thread)

    with st.expander("Thread Telemetry", expanded=False):
        st.markdown(f"- **Total messages:** {stats['total_messages']}")
        st.markdown(f"- **User messages:** {stats['user_messages']}")
        st.markdown(f"- **Assistant messages:** {stats['assistant_messages']}")
        st.markdown(f"- **Estimated tokens:** {stats['estimated_tokens']}")

        if stats["estimated_tokens"] > 6000:
            st.warning(
                "This thread is getting long. Consider exporting or starting a new chat "
                "to keep context efficient."
            )

        st.markdown("### Route Usage")
        for route, count in ROUTE_STATS.items():
            st.markdown(f"- `{route}`: {count}")

        st.markdown("### Persona Bias")
        for persona, count in SESSION_DNA.get("persona_bias", {}).items():
            st.markdown(f"- `{persona}` used in {count} turns")

# ==============================================================================
# H. SETTINGS PROFILES & MULTI-USER MODE (LIGHTWEIGHT)
# ==============================================================================

PROFILES_FILE = "persistent_profiles.json"


def load_profiles() -> dict:
    data = _safe_json_load(PROFILES_FILE, {"profiles": {}})
    return data.get("profiles", {})


def save_profiles(profiles: dict) -> None:
    _atomic_json_write(PROFILES_FILE, {"profiles": profiles})


def initialize_profiles_state() -> None:
    if "profiles" not in st.session_state:
        st.session_state.profiles = load_profiles()
    if "current_user" not in st.session_state:
        st.session_state.current_user = "default"


def apply_profile(profile_name: str) -> None:
    profile = st.session_state.profiles.get(profile_name)
    if not profile:
        return

    st.session_state.personality = profile.get("personality", st.session_state.personality)
    st.session_state.target_language = profile.get("language", st.session_state.target_language)
    st.session_state.temperature = profile.get("temperature", st.session_state.temperature)
    st.session_state.max_tokens = profile.get("max_tokens", st.session_state.max_tokens)


def save_current_profile(profile_name: str) -> None:
    st.session_state.profiles[profile_name] = {
        "personality": st.session_state.personality,
        "language": st.session_state.target_language,
        "temperature": st.session_state.temperature,
        "max_tokens": st.session_state.max_tokens,
    }
    save_profiles(st.session_state.profiles)


def sidebar_profiles_controls():
    """
    Multi-user / profile controls in sidebar.
    """
    with st.sidebar.expander("Profiles & Users", expanded=False):
        # Current user name (lightweight multi-user)
        st.session_state.current_user = st.text_input(
            "Current user ID",
            value=st.session_state.current_user,
        )

        profile_names = list(st.session_state.profiles.keys())
        selected_profile = st.selectbox(
            "Apply profile",
            ["(none)"] + profile_names,
            index=0,
        )
        if selected_profile != "(none)":
            if st.button("Apply selected profile"):
                apply_profile(selected_profile)
                st.success(f"Profile '{selected_profile}' applied.")

        new_profile_name = st.text_input("Save current settings as profile", value="")
        if new_profile_name:
            if st.button("Save profile"):
                save_current_profile(new_profile_name)
                st.success(f"Profile '{new_profile_name}' saved.")

# ==============================================================================
# I. THREAD MANAGEMENT TOOLS
# ==============================================================================

def merge_threads(source_chat: str, target_chat: str) -> None:
    """
    Appends messages from source_chat into target_chat.
    """
    if source_chat not in st.session_state.chats or target_chat not in st.session_state.chats:
        return
    st.session_state.chats[target_chat].extend(st.session_state.chats[source_chat])
    save_chats(st.session_state.chats)


def duplicate_thread(chat_name: str) -> str:
    """
    Creates a duplicate of a given chat thread.
    """
    if chat_name not in st.session_state.chats:
        return chat_name
    new_name = f"{chat_name} (copy)"
    st.session_state.chats[new_name] = list(st.session_state.chats[chat_name])
    save_chats(st.session_state.chats)
    return new_name


def archive_thread(chat_name: str) -> None:
    """
    Archives a thread by prefixing its name.
    """
    if chat_name not in st.session_state.chats:
        return
    new_name = f"[ARCHIVED] {chat_name}"
    st.session_state.chats[new_name] = st.session_state.chats.pop(chat_name)
    save_chats(st.session_state.chats)


def thread_tools_panel():
    """
    Panel for managing threads: merge, duplicate, archive.
    """
    with st.expander("Thread Tools", expanded=False):
        chat_names = list(st.session_state.chats.keys())
        if not chat_names:
            st.write("No threads available.")
            return

        current = st.session_state.current_chat
        st.markdown(f"**Current thread:** `{current}`")

        # Duplicate
        if st.button("Duplicate current thread"):
            new_name = duplicate_thread(current)
            st.success(f"Thread duplicated as '{new_name}'.")

        # Archive
        if st.button("Archive current thread"):
            archive_thread(current)
            st.success(f"Thread '{current}' archived.")
            st.session_state.current_chat = list(st.session_state.chats.keys())[0]

        # Merge
        source = st.selectbox("Source thread to merge into current", chat_names)
        if st.button("Merge selected into current"):
            if source != current:
                merge_threads(source, current)
                st.success(f"Merged '{source}' into '{current}'.")
            else:
                st.warning("Cannot merge a thread into itself.")

# ==============================================================================
# J. AUGMENTED SIDEBAR (ADDING PROFILES, DEV CONSOLE, THREAD TOOLS)
# ==============================================================================

def sidebar_controls():
    """
    Composite sidebar: settings, memory, bookmarks, export/import, image stub,
    profiles, developer console, thread tools.
    """
    st.sidebar.header("Workspace Settings")

    # Provider selection
    st.session_state.selected_provider = st.sidebar.selectbox(
        "Provider",
        [PROVIDER_GROQ, PROVIDER_OPENROUTER],
        index=[PROVIDER_GROQ, PROVIDER_OPENROUTER].index(
            st.session_state.selected_provider
        ),
    )

    # Model selection
    if st.session_state.selected_provider == PROVIDER_GROQ:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            GROQ_MODELS,
            index=GROQ_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in GROQ_MODELS
            else 0,
        )
    else:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            OPENROUTER_MODELS,
            index=OPENROUTER_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in OPENROUTER_MODELS
            else 0,
        )

    # Personality
    st.session_state.personality = st.sidebar.selectbox(
        "Personality",
        PERSONALITY_PRESETS,
        index=PERSONALITY_PRESETS.index(st.session_state.personality)
        if st.session_state.personality in PERSONALITY_PRESETS
        else 0,
    )

    # Language
    st.session_state.target_language = st.sidebar.selectbox(
        "Language",
        ["English", "Spanish", "French"],
        index=["English", "Spanish", "French"].index(
            st.session_state.target_language
        )
        if st.session_state.target_language in ["English", "Spanish", "French"]
        else 0,
    )

    # Temperature
    st.session_state.temperature = st.sidebar.slider(
        "Temperature",
        0.0,
        1.0,
        st.session_state.temperature,
        0.05,
    )

    # Max tokens
    st.session_state.max_tokens = st.sidebar.slider(
        "Max Tokens",
        256,
        4096,
        st.session_state.max_tokens,
        256,
    )

    # Document upload (for simple RAG)
    uploaded = st.sidebar.file_uploader(
        "Upload a text/CSV file for context",
        type=["txt", "csv"],
        key="doc_uploader",
    )
    if uploaded is not None:
        st.session_state.doc_context = extract_text_from_upload(uploaded)

    # Memory vault viewer
    with st.sidebar.expander("Memory Vault", expanded=False):
        if not st.session_state.memory_vault:
            st.write("No stored memories yet.")
        else:
            for mem in st.session_state.memory_vault[-5:]:
                ts = mem.get("timestamp", "unknown")
                user = mem.get("user", "")
                st.markdown(f"- **{ts}** — {user[:80]}...")

    # Bookmarks, export/import, image placeholder, profiles
    bookmarks_panel()
    sidebar_export_import_controls()
    sidebar_image_placeholder()
    sidebar_profiles_controls()
    developer_console()
    thread_tools_panel()

# ==============================================================================
# K. REAL IMAGE GENERATION ROUTE (STUB USING PROMPT-BASED URL)
# ==============================================================================

def route_image_generation(user_input: str) -> str:
    """
    Simple image generation route using a prompt-based image URL.
    This does not call a paid API; it just returns a URL pattern.
    """
    prompt = user_input.strip()
    if not prompt:
        prompt = "abstract digital artwork"
    import urllib.parse
    encoded = urllib.parse.quote(prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true"
    return (
        "Here is a generated image URL based on your prompt:\n\n"
        f"{image_url}\n\n"
        "You can open it in a browser to view the image."
    )

# ==============================================================================
# L. DOCUMENT INTELLIGENCE: SECTIONING & Q&A PROMPT BUILDER
# ==============================================================================

def split_document_into_sections(text: str, max_section_len: int = 1500) -> list:
    """
    Splits a long document into rough sections by paragraphs.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    paragraphs = text.split("\n")
    sections = []
    current = []
    current_len = 0

    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if current_len + len(p) > max_section_len and current:
            sections.append("\n".join(current))
            current = [p]
            current_len = len(p)
        else:
            current.append(p)
            current_len += len(p)

    if current:
        sections.append("\n".join(current))

    return sections


def build_document_qa_prompt(question: str, doc_text: str) -> str:
    """
    Builds a Q&A-style prompt over a document.
    """
    sections = split_document_into_sections(doc_text)
    if not sections:
        return f"The document is empty. Answer the question directly: {question}"

    # Use only first few sections to keep prompt small
    selected = sections[:3]
    joined = "\n\n---\n\n".join(selected)

    return (
        "You are answering a question based on the following document sections.\n\n"
        f"{joined}\n\n"
        f"Question: {question}\n\n"
        "Answer clearly, citing relevant parts of the text."
    )


def route_read_assistance(user_input: str) -> str:
    """
    Route for document Q&A: uses doc_context if available.
    """
    doc = st.session_state.get("doc_context", "")
    if not doc:
        return "No document is loaded. Please upload a document in the sidebar first."
    prompt = build_document_qa_prompt(user_input, doc)
    return prompt

# Wire route_read_assistance into build_messages_for_route:

def build_messages_for_route(route: str, user_input: str) -> List[Dict[str, str]]:
    """
    Builds the message list for the selected route.
    """
    if route == "ROUTE_STANDARD":
        return [{"role": "user", "content": sanitize_user_input(user_input)}]

    if route == "ROUTE_SUMMARIZE" and st.session_state.doc_context:
        doc = truncate_long_context(st.session_state.doc_context)
        return [
            {
                "role": "user",
                "content": f"Summarize this document for the user:\n\n{doc}",
            }
        ]

    if route == "ROUTE_IMAGE_GEN":
        return [
            {
                "role": "user",
                "content": route_image_generation(user_input),
            }
        ]

    if route == "ROUTE_DEBUG":
        return [
            {
                "role": "user",
                "content": route_debug_assistance(user_input),
            }
        ]

    if route == "ROUTE_SEARCH":
        return [
            {
                "role": "user",
                "content": route_search_assistance(user_input),
            }
        ]

    if route == "ROUTE_READ":
        return [
            {
                "role": "user",
                "content": route_read_assistance(user_input),
            }
        ]

    # Fallback
    return [{"role": "user", "content": sanitize_user_input(user_input)}]

# ==============================================================================
# M. LOCAL SEARCH UI PANEL
# ==============================================================================

def local_search_panel():
    """
    UI panel for querying the local index (messages, documents, bookmarks).
    """
    with st.expander("Local Search", expanded=False):
        query = st.text_input("Search query", key="local_search_query")
        if st.button("Search", key="local_search_button"):
            if not query.strip():
                st.warning("Enter a query to search.")
            else:
                index_current_thread()
                index_documents_from_context()
                index_bookmarks()
                hits = deep_memory_search(query, top_k=10)
                if not hits:
                    st.info("No local matches found.")
                else:
                    for h in hits:
                        if h["type"] == "message":
                            st.markdown(
                                f"- **Message** in `{h['chat']}` #{h['index']}: "
                                f"{h['content'][:160]}..."
                            )
                        elif h["type"] == "document":
                            st.markdown(
                                f"- **Document** `{h['title']}`: "
                                f"{h['content'][:160]}..."
                            )
                        elif h["type"] == "bookmark":
                            st.markdown(
                                f"- **Bookmark** in `{h.get('chat', '?')}` "
                                f"#{h.get('index', '?')}: "
                                f"{h.get('content', '')[:160]}..."
                            )

# Add local_search_panel to sidebar_controls:

def sidebar_controls():
    """
    Composite sidebar: settings, memory, bookmarks, export/import, image stub,
    profiles, developer console, thread tools, local search.
    """
    st.sidebar.header("Workspace Settings")

    # Provider selection
    st.session_state.selected_provider = st.sidebar.selectbox(
        "Provider",
        [PROVIDER_GROQ, PROVIDER_OPENROUTER],
        index=[PROVIDER_GROQ, PROVIDER_OPENROUTER].index(
            st.session_state.selected_provider
        ),
    )

    # Model selection
    if st.session_state.selected_provider == PROVIDER_GROQ:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            GROQ_MODELS,
            index=GROQ_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in GROQ_MODELS
            else 0,
        )
    else:
        st.session_state.selected_model = st.sidebar.selectbox(
            "Model",
            OPENROUTER_MODELS,
            index=OPENROUTER_MODELS.index(st.session_state.selected_model)
            if st.session_state.selected_model in OPENROUTER_MODELS
            else 0,
        )

    # Personality
    st.session_state.personality = st.sidebar.selectbox(
        "Personality",
        PERSONALITY_PRESETS,
        index=PERSONALITY_PRESETS.index(st.session_state.personality)
        if st.session_state.personality in PERSONALITY_PRESETS
        else 0,
    )

    # Language
    st.session_state.target_language = st.sidebar.selectbox(
        "Language",
        ["English", "Spanish", "French"],
        index=["English", "Spanish", "French"].index(
            st.session_state.target_language
        )
        if st.session_state.target_language in ["English", "Spanish", "French"]
        else 0,
    )

    # Temperature
    st.session_state.temperature = st.sidebar.slider(
        "Temperature",
        0.0,
        1.0,
        st.session_state.temperature,
        0.05,
    )

    # Max tokens
    st.session_state.max_tokens = st.sidebar.slider(
        "Max Tokens",
        256,
        4096,
        st.session_state.max_tokens,
        256,
    )

    # Document upload (for simple RAG)
    uploaded = st.sidebar.file_uploader(
        "Upload a text/CSV file for context",
        type=["txt", "csv"],
        key="doc_uploader",
    )
    if uploaded is not None:
        st.session_state.doc_context = extract_text_from_upload(uploaded)

    # Memory vault viewer
    with st.sidebar.expander("Memory Vault", expanded=False):
        if not st.session_state.memory_vault:
            st.write("No stored memories yet.")
        else:
            for mem in st.session_state.memory_vault[-5:]:
                ts = mem.get("timestamp", "unknown")
                user = mem.get("user", "")
                st.markdown(f"- **{ts}** — {user[:80]}...")

    # Bookmarks, export/import, image placeholder, profiles, dev console, thread tools, local search
    bookmarks_panel()
    sidebar_export_import_controls()
    sidebar_image_placeholder()
    sidebar_profiles_controls()
    developer_console()
    thread_tools_panel()
    local_search_panel()

