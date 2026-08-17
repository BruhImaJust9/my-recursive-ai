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

# OpenTelemetry-style logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    stream=sys.stdout,
)

# Streamlitimport streamlit as st

# Data & Images
import pandas as pd
from PIL import Image

# Groq
try:
    from groq import Groq
except Exception as _import_err:
    Groq = None
    logging.warning("[IMPORT] groq unavailable: %s", _import_err)

# OpenAI client (used for OpenRouter)
try:
    from openai import OpenAI
except Exception as _import_err:
    OpenAI = None
    logging.warning("[IMPORT] openai unavailable: %s", _import_err)

# Tavily Search
try:
    from tavily import TavilyClient
except Exception as _import_err:
    TavilyClient = None
    logging.warning("[IMPORT] tavily unavailable: %s", _import_err)

# Text-to-Speech
try:
    from gtts import gTTS
except Exception as _import_err:
    gTTS = None
    logging.warning("[IMPORT] gtts unavailable: %s", _import_err)

# Voice recorder widget
try:
    from audio_recorder_streamlit import audio_recorder
except Exception as _import_err:
    audio_recorder = None
    logging.warning("[IMPORT] audio_recorder_streamlit unavailable: %s", _import_err)

# HTTP (used for search & scraping)
try:
    import requests
except Exception as _import_err:
    requests = None
    logging.warning("[IMPORT] requests unavailable: %s", _import_err)

# HTML parsing (used for /read page scraping)
try:
    from bs4 import BeautifulSoup
except Exception as _import_err:
    BeautifulSoup = None
    logging.warning("[IMPORT] beautifulsoup4 unavailable: %s", _import_err)

# PDF parsing
try:
    from pypdf import PdfReader
except Exception as _import_err:
    PdfReader = None
    logging.warning("[IMPORT] pypdf unavailable: %s", _import_err)

# Optional sentence-transformers for re-ranking (soft fail)
try:
    from sentence_transformers import CrossEncoder
except Exception as _import_err:
    CrossEncoder = None
    logging.warning("[IMPORT] sentence_transformers unavailable: %s", _import_err)


# ==============================================================================
# 1. CONFIGURATION & CONSTANTS
# ==============================================================================
CHAT_STORAGE_FILE = "persistent_chats.json"
BOOKMARKS_FILE = "persistent_bookmarks.json"
MEMORY_FILE = "persistent_memory.json"
SETTINGS_FILE = "persistent_settings.json"

DEFAULT_SYSTEM_PROMPT = (
    "You are an elite AI assistant. Be precise, thorough, and directly useful. "
    "Avoid unnecessary disclaimers or filler preambles."
)

PROVIDER_GROQ = "Groq"
PROVIDER_OPENROUTER = "OpenRouter"

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "whisper-large-v3",  # voice only
]

OPENROUTER_MODELS = [
    "openrouter/auto",
    "google/gemma-3-12b-it:free",
    "qwen/qwen-2.5-vl-72b-instruct:free",
    "openai/gpt-4o-mini",
    "anthropic/claude-3-haiku:free",
]

VISION_MODELS = [
    "openrouter/auto",
    "google/gemma-3-12b-it:free",
    "qwen/qwen-2.5-vl-72b-instruct:free",
]

REALTIME_KEYWORDS = {
    "news", "latest", "today", "yesterday", "current", "weather",
    "score", "results", "winner", "stock", "price", "who won",
    "schedule", "upcoming", "event", "standings", "release date",
    "trending", "update", "right now", "live",
}

MEDIA_LORE_KEYWORDS = {
    "character", "characters", "cast", "show", "episode", "lore",
    "tadc", "fnaf", "anime", "manga", "season", "actor", "voice actor",
}

PERSONALITY_PRESETS = [
    "Helpful Assistant",
    "Principal Systems Architect",
    "Senior Data Scientist",
    "Ruthless Code Reviewer",
    "Creative Writing Coach",
    "Socratic Tutor",
    "Concise Executive Advisor",
]

LANGUAGE_PRESETS = [
    "English", "Spanish", "French", "German",
    "Chinese", "Japanese", "Portuguese", "Russian", "Arabic", "Korean",
]


# ==============================================================================
# 2. PERSISTENCE ENGINE (ATOMIC, TRIPLE-GUARDED)
# ==============================================================================
def _atomic_json_write(file_path: str, payload: dict) -> bool:
    """
    Safe atomic JSON serialization using temp-file + fsync + replace.
    Returns True on success, False on failure.
    """
    try:
        dir_name = os.path.dirname(file_path) or "."
        os.makedirs(dir_name, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_name, delete=False, encoding="utf-8"
        ) as tf:
            json.dump(payload, tf, indent=2, ensure_ascii=False)
            tf.flush()
            os.fsync(tf.fileno())
            temp_path = tf.name
        os.replace(temp_path, file_path)
        return True
    except Exception as err:
        logging.error("[PERSISTENCE] Atomic write failed: %s", err)
        return False


def _safe_json_load(file_path: str, default: dict) -> dict:
    """
    Loads JSON with corruption recovery, schema validation, and backup.
    """
    if not os.path.exists(file_path):
        return default.copy()

    try:
        size = os.path.getsize(file_path)
        if size == 0:
            return default.copy()

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            logging.warning("[PERSISTENCE] Root type invalid; using default.")
            return default.copy()

        # Validate each chat thread is a list
        sanitized = {}
        for k, v in data.items():
            if isinstance(v, list):
                sanitized[k] = v
            else:
                sanitized[k] = []
        return sanitized if sanitized else default.copy()

    except json.JSONDecodeError as err:
        backup = f"{file_path}.corrupt.{int(time.time())}"
        if os.path.exists(file_path):
            os.rename(file_path, backup)
        logging.warning("[PERSISTENCE] Corrupt JSON backed up to %s: %s", backup, err)
        return default.copy()
    except Exception as err:
        logging.warning("[PERSISTENCE] Load error: %s", err)
        return default.copy()


def load_saved_chats() -> dict:
    """Thread-safe chat history loader with schema repair."""
    return _safe_json_load(CHAT_STORAGE_FILE, {"New Chat": []})


def save_chats_to_disk() -> None:
    """
    Serializes in-memory chat state to disk, stripping binary blobs.
    Only persists if user is logged in.
    """
    if not st.session_state.get("is_logged_in", False):
        return

    if "chats" not in st.session_state:
        return

    clean_chats: dict = {}
    for session_name, msg_list in st.session_state.chats.items():
        if not isinstance(msg_list, list):
            continue
        clean_chats[session_name] = []
        for msg in msg_list:
            if not isinstance(msg, dict):
                continue
            # Strip large or binary keys
            clean_msg = {
                k: v
                for k, v in msg.items()
                if k not in ("audio", "image_url", "bytes", "raw_response")
                and isinstance(v, (str, int, float, bool, list, dict))
            }
            clean_chats[session_name].append(clean_msg)

    _atomic_json_write(CHAT_STORAGE_FILE, clean_chats)


def load_bookmarks() -> list:
    return _safe_json_load(BOOKMARKS_FILE, {}).get("bookmarks", [])


def save_bookmarks() -> None:
    _atomic_json_write(BOOKMARKS_FILE, {"bookmarks": st.session_state.get("bookmarks", [])})


def load_memory_vault() -> list:
    return _safe_json_load(MEMORY_FILE, {}).get("memory", [])


def save_memory_vault() -> None:
    _atomic_json_write(MEMORY_FILE, {"memory": st.session_state.get("memory_vault", [])})


def load_settings() -> dict:
    return _safe_json_load(SETTINGS_FILE, {})


def save_settings(settings_dict: dict) -> None:
    _atomic_json_write(SETTINGS_FILE, settings_dict)


# ==============================================================================
# 3. SESSION STATE INITIALIZATION (SAFE DEFAULT HOOKS)
# ==============================================================================
def initialize_session_state() -> None:
    """
    Idempotent session state bootstrap. Populates defaults for all workspace
    primitives, loads persisted data where appropriate, and validates cross-key
    consistency (e.g., current_chat must exist inside chats).
    """
    defaults = {
        "is_logged_in": False,
        "chats": {"New Chat": []},
        "current_chat": "New Chat",
        "input_buffer": "",
        "memory_vault": load_memory_vault(),
        "bookmarks": load_bookmarks(),
        "telemetry": {"requests": 0, "est_tokens": 0, "last_latency": 0.0},
        "doc_context": "",
        "doc_context_meta": {},
        "image_base64": None,
        "image_mime_type": "image/jpeg",
        "uploaded_file": None,
        "selected_provider": PROVIDER_GROQ,
        "selected_model": "llama-3.3-70b-versatile",
        "personality": "Helpful Assistant",
        "target_language": "English",
        "temperature": 0.7,
        "max_tokens": 4096,
        "auto_search": True,
        "prompt_enhance": False,
        "system_prompt_override": "",
 }

    # Seed defaults only when missing
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

    # Load disk chats only for authenticated sessions
    if st.session_state.get("is_logged_in", False) and "chats_loaded_from_disk" not in st.session_state:
        disk_chats = load_saved_chats()
        if disk_chats:
            st.session_state.chats = disk_chats
        st.session_state.chats_loaded_from_disk = True

    # Validate current_chat pointer
if (
    "current_chat" not in st.session_state
    or st.session_state.current_chat not in st.session_state.get("chats", {})
):
    keys = list(st.session_state.chats.keys())
    st.session_state.current_chat = keys[0] if keys else "New Chat"
    if st.session_state.current_chat not in st.session_state.chats:
        st.session_state.chats[st.session_state.current_chat] = []

# Ensure bookmarks/memory are lists
if not isinstance(st.session_state.get("bookmarks"), list):
    st.session_state.bookmarks = []


# ==============================================================================
# 4. CLIENT INITIALIZATION (CACHED RESOURCE LAYER)
# ==============================================================================
@st.cache_resource(show_spinner=False)
def get_groq_client(api_key: str):
    """Groq client cached across reruns."""
    if not api_key or Groq is None:
        return None
    try:
        return Groq(api_key=api_key, timeout=30.0)
    except Exception as err:
        logging.error("[CLIENT] Groq init failure: %s", err)
        return None


@st.cache_resource(show_spinner=False)
def get_openrouter_client(api_key: str):
    """OpenRouter client cached across reruns."""
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
        logging.error("[CLIENT] OpenRouter init failure: %s", err)
        return None


def initialize_clients():
    """
    Reads secrets/env for API keys and initializes both Groq and OpenRouter
    clients. Returns a tuple (groq_client, openrouter_client).
    """
    groq_key = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))
    or_key = st.secrets.get("OPENROUTER_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))

    client = get_groq_client(groq_key)
    openrouter_client = get_openrouter_client(or_key)

    return client, openrouter_client


# ==============================================================================
# 5. FORMATTING, SANITIZATION & EXPORT UTILITIES
# ==============================================================================
def sanitize_and_repair_formatting(text: str) -> str:
    """
    Fixes LaTeX math syntax, normalizes markdown lists, collapses excessive    blank lines, and strips common search-engine disclaimers.
    """
    if not text or not isinstance(text, str):
        return ""

    try:
        text = re.sub(r"\\\[\s*([\s\S]*?)\s*\\\]", r"$$\1$$", text)
        text = re.sub(r"\\\(\s*([\s\S]*?)\s*\\\)", r"$\1$", text)
        text = re.sub(r"([^\n])\n?(\s*[*|-]\s+[A-Za-z0-9])", r"\1\n\2", text)
        text = re.sub(r"([^\n])\n?(\s*\d+\.\s+[A-Za-z0-9])", r"\1\n\2", text)

        disclaimers = [
            r"The provided search results do not directly address.*?\n",
            r"Based on the search results provided.*?\n",
            r"According to the retrieved sources.*?\n",
        ]
        for pattern in disclaimers:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception as err:
        logging.warning("[FORMATTING] Clean failed: %s", err)
        return text


def sanitize_sensitive_data(text: str) -> str:
    """Scrubs emails, IPs, API keys, and phone numbers before LLM dispatch."""
    if not isinstance(text, str) or not text.strip():
        return text

    text = re.sub(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "[REDACTED_EMAIL]",
        text,
    )
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[REDACTED_IP]", text)
    text = re.sub(
        r"\b(sk-[a-zA-Z0-9]{20,}|gsk_[a-zA-Z0-9]{20,})\b",
        "[REDACTED_API_KEY]",
        text,
    )
    text = re.sub(
        r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b",
        "[REDACTED_PHONE]",
        text,
    )
    return text


def export_chat_as_markdown(chat_list: list, title: str = "Chat Session") -> str:
    """Converts a chat thread into clean Markdown with metadata headers."""
    if not isinstance(chat_list, list):
        chat_list = []

    clean_title = str(title) if title else "Chat Session"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# 📄 {clean_title}",
        f"**Exported On:** {timestamp}  ",
        f"**Total Messages:** {len(chat_list)}  ",
        "\n---\n",
    ]

    for msg in chat_list:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            extracted = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    extracted.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    extracted.append(part)
            content = "\n".join(extracted) if extracted else str(content)
        elif not isinstance(content, str):
            content = str(content)

        header = "### 👤 User" if role == "user" else "### 🤖 Assistant"
        lines.append(f"{header}\n\n{content.strip()}\n\n---\n")

    return "\n".join(lines)


def export_session_to_json(chats_dict: dict) -> str:
    """Exports the full workspace thread map as a structured JSON string."""
    payload = {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat() + "Z",
        "threads": chats_dict,
    }
    return json.dumps(payload, indent=2)


def import_session_from_json(json_str: str) -> dict:
    """Validates and restores a session JSON payload."""
    try:
        data = json.loads(json_str)
        if isinstance(data, dict) and "threads" in data and isinstance(data["threads"], dict):
            return data["threads"]
        elif isinstance(data, dict):
            return data
    except Exception as err:
        logging.error("[IMPORT] Session JSON restore failed: %s", err)
    return {}


def _estimate_message_tokens(msg: dict, char_per_token: float = 4.0) -> int:
    """
    Token estimator supporting both string and multimodal (list) content.
    Images are costed at a flat ~1024-token heuristic.
    """
    content = msg.get("content", "")
    if isinstance(content, str):
        return int(len(content) / char_per_token)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                total += int(len(part.get("text", "")) / char_per_token)
            elif part.get("type") == "image_url":
                total += 1024
        return total
    return 0


def enforce_context_window(
    messages: list[dict],
    max_token_budget: int = 4090,
    char_per_token_ratio: float = 4.0,
) -> list[dict]:
    """
    Prunes older conversation messages (preserving system and latest user turn)
    to remain within the defined token budget.
    """
    if not messages:
        return []

    system_msgs = [m for m in messages if m.get("role") == "system"]
    conversation = [m for m in messages if m.get("role") != "system"]

    def _total_tokens(msg_list):
        return sum(_estimate_message_tokens(m, char_per_token_ratio) for m in msg_list)

    # Prune from the front (oldest) while over budget, but always retain the last user message
    while conversation and (_total_tokens(system_msgs + conversation) > max_token_budget):
        if len(conversation) <= 1:
            break
        conversation.pop(0)

    return system_msgs + conversation


# ==============================================================================
# 6. DOCUMENT & ATTACHMENT HELPERS
# ==============================================================================
def extract_text_from_upload(uploaded_file, max_char_limit: int = 50000) -> str:
    """
    Parses uploaded files safely across CSV, JSON, TXT, MD, PDF, PY, and XLSX.
    """
    if uploaded_file is None:
        return ""

    file_type = uploaded_file.type
    file_name = uploaded_file.name.lower()

    try:
        if file_name.endswith(".csv") or "csv" in str(file_type):
            df = pd.read_csv(uploaded_file)
            return f"--- CSV Content ({file_name}) ---\n" + df.to_markdown(index=False)

        if file_name.endswith((".xlsx", ".xls")) or "sheet" in str(file_type):
            df = pd.read_excel(uploaded_file)
            return f"--- Excel Content ({file_name}) ---\n" + df.to_markdown(index=False)

        if file_name.endswith(".json") or "json" in str(file_type):
            content = json.load(uploaded_file)
            return f"--- JSON Content ({file_name}) ---\n" + json.dumps(content, indent=2)

        if file_name.endswith((".txt", ".md", ".py")):
            raw = uploaded_file.read()
            text = raw.decode("utf-8", errors="replace")
            return text[:max_char_limit]

        if file_name.endswith(".pdf"):
            if PdfReader is None:
                return "⚠️ PDF parsing requires `pypdf` package."
            reader = PdfReader(uploaded_file)
            pages = [p.extract_text() for p in reader.pages if p.extract_text()]
            full_text = "\n\n".join(pages)
            return full_text[:max_char_limit]

        # Fallback binary decode
        raw = uploaded_file.read()
        return raw.decode("utf-8", errors="replace")[:max_char_limit]

    except Exception as exc:
        return f"⚠️ Error parsing file '{file_name}': {exc}"


def encode_image_to_base64(file_obj) -> tuple:
    """
    Converts an uploaded image file into a base64 data URI tuple (b64_string, mime_type).
    Resizes aggressively to reduce token load.
    """
    try:
        img = Image.open(file_obj)
        # Resize to cap max dimension to reduce inference cost        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        fmt = img.format if img.format else "JPEG"
        out_fmt = "JPEG" if fmt not in ("PNG", "WEBP", "GIF") else fmt
        img.save(buf, format=out_fmt)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        mime = f"image/{out_fmt.lower()}"
        return b64, mime
    except Exception as exc:
        st.error(f"Image encoding error: {exc}")
        return None, None


def scrape_web_page(target_url: str, max_chars: int = 6000) -> str:
    """
    Fetches a URL and extracts structured text (title + paragraphs) via BeautifulSoup.
    """
    if not requests:
        return "⚠️ `requests` is not installed."
    if not BeautifulSoup:
        return "⚠️ `beautifulsoup4` is not installed."

    clean_url = target_url.strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = "https://" + clean_url

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(clean_url, headers=headers, timeout=12)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove script/style
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else "No Title"
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 20]
        body_text = " ".join(paragraphs)[:max_chars]

        return f"Title: {title}\nURL: {clean_url}\n\n{body_text}"

    except Exception as exc:
        return f"⚠️ Failed to scrape {clean_url}: {exc}"

# ==============================================================================
# 7. SEARCH, IMAGE, TTS, & TOOL ENGINES
# ==============================================================================
def retry_with_exponential_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    allowed_exceptions: tuple = (Exception,),
):
    """Decorator retry utility with jitter for flaky network calls."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except allowed_exceptions as err:
                    if attempt == max_retries:
                        raise err
                    sleep_time = delay + (random.uniform(0, 0.5) * delay)
                    logging.warning(
                        "[RETRY] %s attempt %d/%d failed: %s | sleeping %.2fs",
                        func.__name__, attempt, max_retries, err, sleep_time
                    )
                    time.sleep(sleep_time)
                    delay *= backoff_factor
        return wrapper
    return decorator


def perform_live_search(query: str) -> str:
    """Queries Tavily Search API with a hard timeout."""
    clean_query = query.strip() if query else ""
    if not clean_query:
        return "Search query was empty."

    key = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))
    if not key:
        return "⚠️ Tavily API Key not found. Configure TAVILY_API_KEY in secrets."

    if TavilyClient is None:
        return "⚠️ Tavily SDK (`tavily-python`) is not installed."

    try:
        tavily = TavilyClient(api_key=key)
        results = tavily.search(
            query=clean_query,
            search_depth="basic",
            max_results=3,
        )
        items = results.get("results", [])
        if not items:
            return "No matching live web results found."

        formatted = []
        for idx, r in enumerate(items, 1):
            title = r.get("title", "Untitled Source")
            content = r.get("content", "No description available.")
            url = r.get("url", "#")
            formatted.append(f"{idx}. **{title}**: {content}\n   [Source]({url})")
        return "\n\n".join(formatted)

    except Exception as exc:
        return f"Search execution error: {exc}"


def execute_deconstructed_multi_search(query: str, client, selected_model: str) -> str:
    """
    Deconstructs a query into sub-queries, performs Tavily retrieval,
    and synthesizes an answer with inline citations.
    """
    key = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))
    if not key:
        return "⚠️ Missing `TAVILY_API_KEY` in secrets or environment."
    if TavilyClient is None:
        return "⚠️ Tavily SDK not installed."
    if not client:
        return "⚠️ LLM client is not initialized."

    tavily = TavilyClient(api_key=key)

    # Step 1: Deconstruct query into3 sub-queries
    sub_queries = [query]
    deconstruction_prompt = (
        f"Deconstruct this query into 3 distinct search sub-queries to capture different angles:\n"
        f"Query: '{query}'\n"
        "Return ONLY the 3 queries, one per line, with no extra text."
    )
    try:
        sub_res = client.chat.completions.create(
            model=selected_model,
            messages=[{"role": "user", "content": deconstruction_prompt}],
            temperature=0.1,
            max_tokens=120,
        )
        raw = sub_res.choices[0].message.content.strip().split("\n")
        parsed = [
            re.sub(r"^[0-9\.\-\*\s]+", "", q).strip()
            for q in raw
            if q.strip()
        ]
        if parsed:
            sub_queries = parsed[:3]
    except Exception as err:
        logging.warning("[MULTI_SEARCH] Deconstruction failed: %s", err)

    # Step 2: Retrieve    aggregated = []
    meta = []
    counter = 1
    for q in sub_queries:
        try:
            res = tavily.search(query=q, max_results=2)
            for item in res.get("results", []):
                title = str(item.get("title", "Source")).strip()
                content = str(item.get("content", "")).strip()
                url = str(item.get("url", "#")).strip()
                aggregated.append(
                    f"[{counter}] **{title}** (URL: {url})\nContent: {content}"
                )
                meta.append({"id": counter, "title": title, "url": url})
                counter += 1
        except Exception:
            continue

    if not aggregated:
        return "No authoritative search sources could be retrieved at this time."

    # Step 3: Synthesis prompt
    synthesis_prompt = (
        f"Synthesize an accurate, well-structured answer for: '{query}' using these factual sources.\n\n"
        "CRITICAL CITATION RULES:\n"
        "1. Insert clickable markdown citation links in your answer whenever referencing facts, e.g., [[1]](URL).\n"
        "2. Keep the tone informative, balanced, and scannable.\n\n"
        "SOURCES:\n" + "\n\n".join(aggregated)
    )

    try:
        synthesis_res = client.chat.completions.create(
            model=selected_model,
            messages=[{"role": "user", "content": synthesis_prompt}],
 temperature=0.2,
            max_tokens=2048,
        )
        ai_response = synthesis_res.choices[0].message.content.strip()
    except Exception as exc:
        return f"⚠️ Fact synthesis error: {exc}"

    # Footer references
    footer = "\n\n---\n### 🌐 Sources & References\n"
    for src in meta:
        footer += f"* [[{src['id']}]] [{src['title']}]({src['url']})\n"

    return ai_response + footer


def generate_and_render_image(prompt: str) -> str:
    """Renders generated images with timeout protection and direct UI fallback."""
    clean_prompt = prompt.strip() if prompt else "abstract digital artwork"

    with st.status("🎨 Generating image...", expanded=True) as status:
        try:
            encoded = urllib.parse.quote(clean_prompt)
            image_url = (
                f"https://image.pollinations.ai/prompt/{encoded}"
                f"?width=1024&height=1024&nologo=true"
            )
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(image_url, headers=headers, timeout=60) if requests else None

            if resp and resp.status_code == 200:
                st.image(
                    resp.content,
                    caption=f"Generated: {clean_prompt}",
                    use_container_width=True,
                )
                status.update(
                    label="✨ Image rendered successfully!", state="complete", expanded=False
                )
                return f"![Generated Image]({image_url})"

            status.update(label="❌ Generation failed", state="error", expanded=True)
            return f"⚠️ Image generation failed (HTTP {resp.status_code if resp else 'no response'})."

        except Exception as exc:
            status.update(label="❌ Generation failed", state="error", expanded=True)
            return f"Image generation failed: {exc}"


def generate_tts_audio(text: str, speed_factor: float = 1.0) -> str:
    """Strips formatting syntax and converts text into spoken audio via gTTS."""
    if not text or not isinstance(text, str) or not text.strip():
        return None
    if gTTS is None:
        st.warning("gTTS not installed.")
        return None

    try:
        clean = text
        clean = re.sub(r"```[\s\S]*?```", " [code block omitted] ", clean)
        clean = re.sub(r"`.*?`", "", clean)
        clean = re.sub(r"\$\$.*?\$\$", " [equation] ", clean)
        clean = re.sub(r"\$.*?\$", " [math] ", clean)
        clean = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", clean)
        clean = re.sub(r"<.*?>", "", clean)
        clean = re.sub(r"[*_#~>]", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        clean = clean[:400]
        if not clean:
            return None

        tts = gTTS(text=clean, lang="en", slow=(speed_factor < 1.0))
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            tts.save(fp.name)
            return fp.name
    except Exception as exc:
        logging.warning("[TTS ERROR] %s", exc)
        return None


# ==============================================================================
# 8. LLM INTELLIGENCE UTILITIES
# ==============================================================================
def classify_user_intent(user_text: str, client, model: str) -> str:
    """
    LLM-based intent router. Returns one of:
    STANDARD, IMAGE_GEN, DEBUG, SEARCH, READ, MEMORY, SUMMARIZE.
    """
    if not client:
        return "STANDARD"

    prompt = (
        "Classify the user's intent into exactly one category: STANDARD, IMAGE_GEN, DEBUG, SEARCH, READ, MEMORY, SUMMARIZE.\n"
        "STANDARD = general chat or questions.\n"
        "IMAGE_GEN = user wants an image, drawing, picture, or visual.\n"
        "DEBUG = user wants to debug or fix code.\n"
        "SEARCH = user wants real-time web search, news, facts, current events.\n"
        "READ = user wants to read or summarize a URL.\n"
        "MEMORY = user is asking about prior memory or context.\n"
        "SUMMARIZE = user wants to summarize text or conversation.\n\n"
        f"User query: {user_text}\n\nRespond with ONLY the category keyword."
    )
    try:
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20,
        )
        raw = res.choices[0].message.content.strip().upper()
        for cat in [
            "STANDARD",
            "IMAGE_GEN",
            "DEBUG",
            "SEARCH",
            "READ",
            "MEMORY",
            "SUMMARIZE",
        ]:
            if cat in raw:
                return cat
        return "STANDARD"
    except Exception as exc:
        logging.warning("[INTENT] Classification error: %s", exc)
        return "STANDARD"


def generate_with_reflection(client, model: str, user_prompt: str, system_prompt: str, temperature: float = 0.7):
    """
    Three-step reflective reasoning pipeline:
       1) Draft reasoning
       2) Critique / error check
       3) Final polished synthesis
    """
    if not client:
        return "❌ LLM client unavailable for reflection."

    # Step 1: Draft
    draft = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": system_prompt + "\nThink step-by-step and draft your reasoning internally.",
            },
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=2048,
    ).choices[0].message.content

    # Step 2: Critique
    critique = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a critical reviewer. Identify errors, logical gaps, or weaknesses.",
            },
            {
                "role": "user",
                "content": f"Draft:\n{draft}\n\nIdentify any mistakes or improvements needed.",
            },
        ],
        temperature=0.2,
        max_tokens=1024,
    ).choices[0].message.content

    # Step 3: Final
    final = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": draft},
            {
                "role": "user",
                "content": (
                    f"Critique feedback: {critique}\n\n"
                    "Now produce the final, polished answer incorporating improvements."
                ),
            },
        ],
        temperature=temperature,
        max_tokens=2048,
    ).choices[0].message.content

    return final


def enhance_user_prompt(prompt_text: str, client) -> str:
    """
    Rewrites raw user input into a structured, high-signal instruction prompt.
    """
    if not prompt_text or not isinstance(prompt_text, str) or len(prompt_text.strip()) < 3:
        return prompt_text
    if not client:
        return prompt_text

    system_instr = (
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
                {"role": "system", "content": system_instr},
                {"role": "user", "content": f"Raw User Query: '{prompt_text.strip()}'"},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        enhanced = (res.choices[0].message.content or "").strip()
        enhanced = re.sub(
            r"^(Here is[^\n]*\n|Enhanced Prompt:\s*)", "", enhanced, flags=re.IGNORECASE
        ).strip()
        return enhanced if len(enhanced) >= len(prompt_text) else prompt_text
    except Exception as exc:
        logging.warning("[ENHANCER] %s", exc)
        return prompt_text


def auto_summarize_chat_title(chat_history: list, client, current_name: str) -> None:
    """
    Dynamically renames generic chat threads based on early user intent.
    Guards against state races, key collisions, and API outages.
    """
    if not isinstance(chat_history, list) or not chat_history:
        return

    first_user_msg = None
    for msg in chat_history:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                first_user_msg = content.strip()
                break

    safe_current = str(current_name) if current_name else ""
    if not first_user_msg or not (
        safe_current.startswith("Chat ") or safe_current == "New Chat"
    ):
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
                {"role": "user", "content": f"Topic query: '{first_user_msg[:500]}'"},
 ],
            temperature=0.2,
            max_tokens=15,
        )
        raw_title = res.choices[0].message.content or ""
        cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", raw_title).strip()
        cleaned = " ".join(cleaned.split()).title()

        if not cleaned or len(cleaned) < 2:
            cleaned = " ".join(first_user_msg.split()[:4]).title()

        if len(cleaned) > 35:
            cleaned = cleaned[:32].rstrip() + "..."

        chats = st.session_state.get("chats", {})
        if not isinstance(chats, dict):
            return

        final_title = cleaned
        counter = 1
        while final_title in chats:
            final_title = f"{cleaned} ({counter})"
            counter += 1

        if safe_current in chats:
            chat_data = chats.pop(safe_current)
            chats[final_title] = chat_data
            st.session_state.chats = chats
            st.session_state.current_chat = final_title
            save_chats_to_disk()
            st.rerun()

    except Exception as exc:
        logging.warning("[SUMMARIZER] %s", exc)


def build_dynamic_system_prompt(
    user_input: str,
    base_personality: str,
    language: str,
    detected_style: str = "GENERAL",
) -> str:
    """
    Constructs a high-density system prompt with domain adaptation,
    language enforcement, and Jaccard memory retrieval.
    """
    lowered = str(user_input).lower() if user_input else ""
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
    lines = [
        f"You are an elite AI assistant operating as a {safe_persona}.",
        "",
        "### 🧠 CORE COGNITIVE DIRECTIVES:",
        "1. **First-Principles Reasoning:** Deconstruct complex queries into core logical elements.",
        "2. **Zero Fluff:** Start directly with the answer without preambles like 'Sure, here is...'.",
        "3. **Quantitative Precision:** Use concrete units, probabilities, or metrics where applicable.",
        "4. **Production Code:** Provide clean, runnable code with explicit syntax highlighting.",
    ]

    domains = {
        ("code", "architecture", "algorithm", "python", "javascript", "refactor", "bug", "api", "database"): (
            "\n[DOMAIN ACTIVATED: PRINCIPAL SYSTEMS ARCHITECT]\n"
            "- Focus on modularity, edge-case safety, typed signatures, and execution efficiency."
        ),
        ("data", "dataframe", "pandas", "plot", "csv", "statistics", "machine learning"): (
            "\n[DOMAIN ACTIVATED: SENIOR DATA SCIENTIST]\n"
            "- Focus on statistical validity, data hygiene, vectorization, and clean visualizations."
        ),
    }

    for keywords, adaptation in domains.items():
        if any(kw in lowered for kw in keywords):
            lines.append(adaptation)
            break

    if safe_lang.lower() != "english":
        lines.append(
            f"\nCRITICAL LANGUAGE DIRECTIVE: You MUST respond entirely in {safe_lang}."
        )

    # Memory Retrieval
    try:
        vault = st.session_state.get("memory_vault", [])
        if isinstance(vault, list) and vault:
            user_tokens = set(re.findall(r"\w+", lowered))
            if user_tokens:
                scored = []
                for fact in vault:
                    if not isinstance(fact, str):
                        continue
                    fact_tokens = set(re.findall(r"\w+", fact.lower()))
                    union_len = len(user_tokens.union(fact_tokens))
                    score = len(user_tokens.intersection(fact_tokens)) / float(
                        union_len if union_len else 1
                    )
                    scored.append((score, fact))
                scored.sort(key=lambda x: x[0], reverse=True)
                top_memories = [m[1] for m in scored[:3] if m[0] > 0.05]
                if top_memories:
                    mem_block = "\n".join([f"- {m}" for m in top_memories])
                    lines.append(
                        f"\n[RELEVANT USER CONTEXT]:\n"
                        f"Incorporate these relevant user facts naturally:\n{mem_block}"
                    )
    except Exception as exc:
        logging.warning("[MEMORY RETRIEVAL] %s", exc)

    return "\n".join(lines)


def search_past_memory(user_query: str, chat_history: list, top_k: int = 2) -> str:
    """Searches past chat messages for overlapping keywords to pull relevant context."""
    relevant = []
    keywords = set(user_query.lower().split())

    for msg in chat_history[:-2]:  # Exclude current turn
        content = msg.get("content", "")
        if isinstance(content, str):
            matches = sum(1 for word in keywords if word in content.lower())
            if matches > 1:
                relevant.append(content)

    if not relevant:
        return ""

    return "\n---\n".join(relevant[-top_k:])


# ==============================================================================
# 9. CODE EXECUTION & DEBUGGING ENGINES
# ==============================================================================
def run_autonomous_code_debugger(code_snippet: str, client, model: str) -> str:
    """
    Runs code in a sandboxed execution environment, traps errors,
    and asks the LLM to self-correct iteratively up to max bounds.
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
            exec_globals = {
                "st": st,
                "pd": pd,
                "re": re,
                "datetime": datetime,
                "json": json,
                "math": math,
            }
            with contextlib.redirect_stdout(output_buffer):
                exec(current_code, exec_globals)

            st.success(f"✅ Code executed cleanly on attempt {attempt}!")
            return current_code

        except Exception as err:
            err_msg = f"{type(err).__name__}: {err}"
            st.warning(f"⚠️ Attempt {attempt} failed: {err_msg}")

            if not client:
                st.error("❌ Debugger halted: No active LLM client.")
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
                    max_tokens=2048,
                )
                raw = res.choices[0].message.content or ""
                extracted = re.search(r"```python\s*(.*?)\s*```", raw, re.DOTALL)
                if extracted and extracted.group(1).strip():
                    current_code = extracted.group(1).strip()
                else:
                    st.error("❌ Debugger could not extract corrected Python block.")
                    break
            except Exception as api_err:
                st.error(f"❌ LLM correction request failed: {api_err}")
                break

    st.error("❌ Agentic debugger reached maximum iterations without clean resolution.")
    return current_code


def safe_exec_python(code: str):
    """
    Safer interactive Python runner for the inline code execution widget.
    Returns (success: bool, output: str, error: str).
    """
    if not code or not isinstance(code, str):
        return False, "", "No code provided."

    output_buffer = io.StringIO()
    exec_globals = {
        "st": st,
        "pd": pd,
        "re": re,
        "json": json,
        "math": math,
        "datetime": datetime,
        "__builtins__": {k: __builtins__[k] for k in (
            "abs", "all", "any", "bin", "bool", "chr", "dict", "divmod",
            "enumerate", "filter", "float", "format", "frozenset", "hex",
            "int", "isinstance", "issubclass", "iter", "len", "list", "map",
            "max", "min", "next", "oct", "ord", "pow", "print", "range",
            "repr", "reversed", "round", "set", "slice", "sorted", "str",
            "sum", "tuple", "type", "zip",
        )},
    }

    try:
        with contextlib.redirect_stdout(output_buffer):
            exec(code, exec_globals)
        out = output_buffer.getvalue().strip()
        return True, out, ""
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"


# ==============================================================================
# 10. SIDEBAR UI MANAGERS
# ==============================================================================
def render_login_gate():
    """Renders an authentication form inside the sidebar."""
    st.header("🔐 Authentication")
    demo_user = st.secrets.get("DEMO_USER", "user")
    demo_pass = st.secrets.get("DEMO_PASS", "password")

    username = st.text_input("Username", key="login_user")
    password = st.text_input("Password", type="password", key="login_pass")

    col_btn, _ = st.columns([1, 2])
    with col_btn:
        if st.button("Login", use_container_width=True, key="login_btn"):
            if username == demo_user and password == demo_pass:
                st.session_state.is_logged_in = True
                st.session_state.chats = load_saved_chats()
                st.success("Logged in successfully!")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("Invalid credentials.")


def render_thread_manager():
    """
    Full chat thread lifecycle manager: switch, create, rename, delete.
    """
    st.subheader("💬 Thread Manager")

    chats = st.session_state.get("chats", {})
    if not isinstance(chats, dict):
        chats = {}
        st.session_state.chats = chats

    chat_names = list(chats.keys())
    current = st.session_state.get("current_chat", chat_names[0] if chat_names else "New Chat")

    # Switch thread
    if chat_names:
        idx = chat_names.index(current) if current in chat_names else 0
        selected = st.selectbox("Active Thread", chat_names, index=idx, key="thread_selector")
        if selected != current:
            st.session_state.current_chat = selected
            st.rerun()

    # Message count mini-metric
    active_list = chats.get(st.session_state.current_chat, [])
    st.caption(f"{len(active_list)} messages in this thread.")

    # Actions row
    c1, c2 = st.columns(2)
    with c1:
        if st.button("➕ New Chat", use_container_width=True, key="btn_new_chat"):
            n = 1
            new_name = f"Chat {n}"
            while new_name in chats:
                n += 1
                new_name = f"Chat {n}"
            chats[new_name] = []
            st.session_state.current_chat = new_name
            save_chats_to_disk()
            st.rerun()

    with c2:
        if st.button("🧹 Clear Thread", use_container_width=True, key="btn_clear_thread"):
            chats[st.session_state.current_chat] = []
            save_chats_to_disk()
            st.rerun()

    # Rename / Delete inside expander
    with st.expander("Rename / Delete Current Thread", expanded=False):
        new_title = st.text_input("Rename to", value=st.session_state.current_chat, key="rename_input")
        if st.button("✏️ Rename", use_container_width=True, key="btn_rename"):
            old = st.session_state.current_chat
            clean = new_title.strip()
            if clean and clean not in chats and old in chats:
                chats[clean] = chats.pop(old)
                st.session_state.current_chat = clean
                save_chats_to_disk()
                st.rerun()

        st.markdown("---")
        confirm = st.checkbox("Confirm delete this thread", key="confirm_delete")
        if confirm and st.button("🗑️ Delete Thread", use_container_width=True, key="btn_delete"):
            old = st.session_state.current_chat
            if old in chats:
                del chats[old]
                fallback = list(chats.keys())[0] if chats else "New Chat"
                if fallback not in chats:
                    chats[fallback] = []
                st.session_state.current_chat = fallback
                save_chats_to_disk()
                st.rerun()


def render_model_config():
    """Model provider selection, model picker, persona, and language."""
    st.subheader("⚙️ Model Configuration")

    provider = st.selectbox(
        "Provider",
        [PROVIDER_GROQ, PROVIDER_OPENROUTER],
        index=0 if st.session_state.get("selected_provider") == PROVIDER_GROQ else 1,
        key="cfg_provider",
    )
    st.session_state.selected_provider = provider

    if provider == PROVIDER_GROQ:
        models = GROQ_MODELS
    else:
        models = OPENROUTER_MODELS

    current_model = st.session_state.get("selected_model", models[0])
    model_idx = models.index(current_model) if current_model in models else 0
    chosen_model = st.selectbox("Model", models, index=model_idx, key="cfg_model")
    st.session_state.selected_model = chosen_model

    persona = st.selectbox(
        "Personality",
        PERSONALITY_PRESETS,
        index=PERSONALITY_PRESETS.index(st.session_state.get("personality", "Helpful Assistant"))
        if st.session_state.get("personality") in PERSONALITY_PRESETS
        else 0,
        key="cfg_persona",
    )
    st.session_state.personality = persona

    lang = st.selectbox(
        "Language",
        LANGUAGE_PRESETS,
        index=LANGUAGE_PRESETS.index(st.session_state.get("target_language", "English"))
        if st.session_state.get("target_language") in LANGUAGE_PRESETS
        else 0,
        key="cfg_lang",
    )
    st.session_state.target_language = lang


def render_file_uploader():
    """Handles document and image ingestion into session state."""
    st.subheader("📎 Attachments")

    doc_file = st.file_uploader(
        "Upload Document (PDF, TXT, MD, CSV, JSON, PY, XLSX)",
        type=["pdf", "txt", "md", "csv", "json", "py", "xlsx"],
        key="doc_uploader_widget",
    )

    if doc_file:
        with st.spinner("Extracting document text..."):
            extracted = extract_text_from_upload(doc_file)
            st.session_state.doc_context = extracted
            st.session_state.doc_context_meta = {
                "name": doc_file.name,
                "type": doc_file.type,
                "size": getattr(doc_file, "size", 0),
            }
            st.success(f"Loaded {doc_file.name}")

    img_file = st.file_uploader(
        "Upload Image (Vision)",
        type=["png", "jpg", "jpeg"],
        key="img_uploader_widget",
    )

    if img_file:
        b64, mime = encode_image_to_base64(img_file)
        if b64:
            st.session_state.image_base64 = b64
            st.session_state.image_mime_type = mime
            st.image(img_file, caption="Vision Attachment", width=120)

    if st.button("🧹 Clear Attachments", use_container_width=True, key="btn_clear_attachments"):
        st.session_state.doc_context = ""
        st.session_state.doc_context_meta = {}
        st.session_state.image_base64 = None
        st.session_state.image_mime_type = "image/jpeg"
        st.rerun()


def render_memory_vault_manager():
    """Sidebar memory vault viewer and editor."""
    with st.expander("🧠 Memory Vault", expanded=False):
        vault = st.session_state.get("memory_vault", [])
        if not vault:
            st.info("No memories stored yet.")
        else:
            for i, mem in enumerate(vault[:20]):
                c1, c2 = st.columns([6, 1])
                c1.markdown(f"• {str(mem)[:120]}")
                if c2.button("❌", key=f"del_mem_{i}"):
                    vault.pop(i)
                    st.session_state.memory_vault = vault
                    save_memory_vault()
                    st.rerun()

        new_mem = st.text_area("Add a memory", key="new_memory_input", height=68)
        if st.button("Add to Vault", use_container_width=True, key="btn_add_mem"):
            if new_mem.strip():
                vault.append(new_mem.strip())
                st.session_state.memory_vault = vault
                save_memory_vault()
                st.rerun()


def render_bookmarks_panel():
    """Sidebar bookmark viewer and editor."""
    with st.expander("📌 Bookmarks", expanded=False):
        bms = st.session_state.get("bookmarks", [])
        if not bms:
            st.info("No bookmarks saved yet.")
        else:
            for i, bm in enumerate(bms[:20]):
                c1, c2 = st.columns([6, 1])
                c1.markdown(f"{i+1}. {str(bm)[:100]}...")
                if c2.button("🗑️", key=f"del_bm_{i}"):
                    bms.pop(i)
                    st.session_state.bookmarks = bms
                    save_bookmarks()
                    st.rerun()

def render_settings_panel():
    """Advanced generation settings and behavior toggles."""
    with st.expander("⚙️ Generation Settings", expanded=False):
        st.session_state.temperature = st.slider(
            "Temperature",
            0.0,
            1.5,
            float(st.session_state.get("temperature", 0.7)),
            0.05,
            key="set_temp",
        )
        st.session_state.max_tokens = st.slider(
            "Max Tokens",
            256,
            8192,
            int(st.session_state.get("max_tokens", 4096)),
            128,
            key="set_max_tokens",
        )
        st.session_state.auto_search = st.toggle(
            "Auto Web Search",
            value=st.session_state.get("auto_search", True),
            key="set_auto_search",
        )
        st.session_state.prompt_enhance = st.toggle(
            "Prompt Enhancement",
            value=st.session_state.get("prompt_enhance", False),
            key="set_prompt_enhance",
        )
        override = st.text_area(
            "System Prompt Override",
            value=st.session_state.get("system_prompt_override", ""),
            height=80,
            key="set_sys_override",
        )
        st.session_state.system_prompt_override = override

        if st.button("Reset to Defaults", use_container_width=True, key="btn_reset_settings"):
            st.session_state.temperature = 0.7
            st.session_state.max_tokens = 4096
            st.session_state.auto_search = True
            st.session_state.prompt_enhance = False
            st.session_state.system_prompt_override = ""
            st.rerun()

def render_sidebar_telemetry_widget():
    """Compact telemetry card inside the sidebar."""
    if "telemetry" not in st.session_state or not isinstance(st.session_state.telemetry, dict):
        st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}

    tele = st.session_state.telemetry
    reqs = tele.get("requests", 0)
    tokens = tele.get("est_tokens", 0)
    latency = tele.get("last_latency", 0.0)

    avg = round(tokens / reqs) if reqs > 0 else 0
    if latency == 0.0:
        badge = "⏸️ Idle"
    elif latency < 1.5:
        badge = "⚡ Fast"
    elif latency < 3.5:
        badge = "🟢 Normal"
    else:
        badge = "🟡 Slow"

    with st.sidebar.expander("📈 Live Telemetry", expanded=False):
        c1, c2 = st.columns(2)
        c1.metric("Requests", f"{reqs:,}")
        c1.metric("Avg Tkn/Req", f"{avg:,}")
        c2.metric("Total Tokens", f"{tokens:,}")
        c2.metric("Latency", f"{latency:.2f}s", delta=badge, delta_color="off")

        st.markdown("---")
        if st.button("🧹 Reset Telemetry", use_container_width=True, key="btn_reset_telemetry"):
            st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}
            st.toast("Telemetry metrics reset!", icon="🧹")
            st.rerun()


def render_chat_export_ui():
    """Sidebar Markdown export download button."""
    try:
        current_chat_name = st.session_state.get("current_chat", "New Chat")
        chats = st.session_state.get("chats", {})
        if not isinstance(chats, dict):
            return

        active_list = chats.get(current_chat_name, [])
        if not isinstance(active_list, list):
            return

        if active_list:
            md_data = export_chat_as_markdown(active_list, title=current_chat_name)
            clean_filename = re.sub(r"[^a-zA-Z0-9_-]", "_", str(current_chat_name)).lower()
            if not clean_filename:
                clean_filename = "chat_export"

            st.sidebar.download_button(
                label="📥 Export Chat (.md)",
                data=md_data,
                file_name=f"{clean_filename}_export.md",
                mime="text/markdown",
                use_container_width=True,
                key="sidebar_export_md_btn",
            )
    except Exception as exc:
        st.sidebar.caption("⚠️ Export feature currently unavailable.")
        logging.warning("[EXPORT UI] %s", exc)

def initialize_sidebar_ui():
    """
    Aggregates all sidebar panels in the correct order.
    Separates authenticated content from the login gate.
    """
    with st.sidebar:
        st.title("🧠 Frontier AI Workspace")
        st.markdown("---")

        if not st.session_state.get("is_logged_in", False):
            render_login_gate()
            st.stop()

        render_thread_manager()
        st.markdown("---")
        render_model_config()
        st.markdown("---")
        render_file_uploader()
        st.markdown("---")
        render_memory_vault_manager()
        render_bookmarks_panel()
        st.markdown("---")
        render_settings_panel()
        st.markdown("---")
        render_sidebar_telemetry_widget()
        render_chat_export_ui()


# ==============================================================================
# 11. MAIN WORKSPACE RENDERERS
# ==============================================================================
def render_header_area():
    """Renders the top header bar with current thread name and active badges."""
    col1, col2 = st.columns([5, 5])
    with col1:
        st.markdown(f"### 💬 {st.session_state.get('current_chat', 'New Chat')}")
    with col2:
        prov = st.session_state.get("selected_provider", PROVIDER_GROQ)
        mod = st.session_state.get("selected_model", "llama-3.3-70b-versatile")
        lang = st.session_state.get("target_language", "English")
        st.markdown(
            f"<div style='text-align:right'>"
            f"<span style='background:#262730;color:#fafafa;padding:4px 8px;border-radius:4px;font-size:0.85em;margin-left:6px;display:inline-block;'>"
            f"⚡ {prov} / {mod}</span>"
            f"<span style='background:#262730;color:#fafafa;padding:4px 8px;border-radius:4px;font-size:0.85em;margin-left:6px;display:inline-block;'>"
            f"🌐 {lang}</span>"
 f"</div>",
 unsafe_allow_html=True,
        )


def render_document_canvas():
    """If a document is loaded, show an interactive RAG inspector expander."""
    doc = st.session_state.get("doc_context", "")
    meta = st.session_state.get("doc_context_meta", {})
    if not doc or not isinstance(doc, str):
        return

    with st.expander("📄 Document Canvas & RAG Inspector", expanded=True):
        c1, c2 = st.columns([3, 1])

        words = len(doc.split())
        chars = len(doc)
        est_read = max(1, round(words / 200))

        with c1:
            st.markdown("### 📝 Loaded Document Context")
            show_full = st.checkbox("Show full document content", value=False, key="doc_show_full")
            display = doc if show_full or len(doc) <= 2000 else doc[:2000] + "\n\n... [Truncated for Preview]"
            st.text_area(
                "Document Content Preview",
                value=display,
                height=300 if show_full else 180,
                disabled=True,
                label_visibility="collapsed",
                key="doc_preview_text_area",
            )

        with c2:
            st.markdown("### 📊 Document Metrics")
            st.metric("Total Words", f"{words:,}")
            st.metric("Total Characters", f"{chars:,}")
            st.metric("Est. Read Time", f"~{est_read} min")
            if meta.get("name"):
                st.caption(f"File: `{meta['name']}`")

            st.markdown("---")
            if st.button("🧹 Clear Canvas", use_container_width=True, key="btn_clear_canvas"):
                st.session_state.doc_context = ""
                st.session_state.doc_context_meta = {}
                st.success("Document context cleared!")
                st.rerun()


def render_message_actions(msg_idx: int, content: str, role: str):
    """
    Per-message interactive toolbar: Copy, TTS, Bookmark, Delete.
    """
    if role != "assistant":
        return

    cols = st.columns([1, 1, 1, 1, 6])
    if cols[0].button("📋", key=f"act_copy_{msg_idx}", help="Copy text"):
        st.toast("Copied to clipboard context!", icon="📋")

    if cols[1].button("🔊", key=f"act_tts_{msg_idx}", help="Text to Speech"):
        audio_path = generate_tts_audio(str(content))
        if audio_path:
            st.audio(audio_path, format="audio/mp3")

    if cols[2].button("📌", key=f"act_bm_{msg_idx}", help="Bookmark Response"):
        bookmarks = st.session_state.get("bookmarks", [])
        if content not in bookmarks:
            bookmarks.append(content)
            st.session_state.bookmarks = bookmarks
            save_bookmarks()
            st.toast("Bookmarked!", icon="📌")

    if cols[3].button("🗑️", key=f"act_del_{msg_idx}", help="Delete Message"):
        cur = st.session_state.current_chat
        lst = st.session_state.chats.get(cur, [])
        if msg_idx < len(lst):
            lst.pop(msg_idx)
            save_chats_to_disk()
            st.rerun()


def render_chat_history_thread(active_chat_list: list):
    """Renders the active conversation with interactive action bars."""
    if not active_chat_list:
        st.info("👋 Welcome! Ask a question or use slash commands like `/search`, `/image`, `/debug`, `/enhance`, `/read`, `/memory`, `/summarize`, `/clear`.")
        return

    for msg_idx, msg in enumerate(active_chat_list):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        avatar = "👤" if role == "user" else "🤖"

        with st.chat_message(role, avatar=avatar):
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        st.markdown(sanitize_and_repair_formatting(part.get("text", "")))
                    elif isinstance(part, dict) and part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url:
                            st.image(url, caption="Attached Image", use_container_width=True)
            else:
                st.markdown(sanitize_and_repair_formatting(str(content)))

            if role == "assistant":
                render_message_actions(msg_idx, str(content), role)

def render_voice_input(client):
    """
    Captures audio from the user, transcribes via Whisper, and buffers the result
    into the next chat input cycle.
    """
    if audio_recorder is None or not client:
        return

    audio_bytes = audio_recorder(
        text="",
        recording_color="#e8b62c",
        neutral_color="#6aa84f",
        icon_size="2x",
        key="voice_recorder_widget",
    )

    if audio_bytes:
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

                tx = str(transcription).strip()
                if tx:
                    st.session_state.input_buffer = tx
                    st.rerun()
            except Exception as exc:
                st.error(f"Voice transcription error: {exc}")


def render_telemetry_dashboard():
    """Renders analytical workspace performance cards in the main area."""
    st.markdown("### 📊 Workspace Telemetry & Health Monitor")

    tele = st.session_state.get("telemetry", {"requests": 0, "est_tokens": 0, "last_latency": 0.0})
    total_requests = tele.get("requests", 0)
    total_tokens = tele.get("est_tokens", 0)
    last_latency = tele.get("last_latency", 0.0)

    avg_tokens = round(total_tokens / total_requests, 1) if total_requests > 0 else 0
    est_cost = f"${(total_tokens / 1000) * 0.002:.4f}"

    t1, t2, t3, t4 = st.columns(4)
    with t1:
        st.metric("Total Requests", f"{total_requests:,}")
    with t2:
        st.metric("Est. Tokens", f"{total_tokens:,}", delta=f"~{avg_tokens}/req")
    with t3:
        status = "⚡ Fast" if last_latency < 1.5 else ("🟢 Normal" if last_latency < 3.5 else "🟡 Slow")
        st.metric("Last Latency", f"{last_latency:.2f}s", delta=status)
    with t4:
        st.metric("Est. Spend", est_cost)


def render_footer_status_bar():
    """Persistent status footer summarizing active runtime configuration."""
    with st.container():
        st.markdown("---")
        f1, f2, f3, f4, f5 = st.columns(5)
        f1.caption(f"🔑 Logged In: {'Yes' if st.session_state.get('is_logged_in') else 'No'}")
        f2.caption(f"🤖 {st.session_state.get('selected_provider','?')} / {st.session_state.get('selected_model','?')}")
        f3.caption(f"🌡️ Temp: {st.session_state.get('temperature',0.7)}")
        f4.caption(f"📄 Doc: {'Loaded' if st.session_state.get('doc_context') else 'None'}")
        f5.caption(f"🖼️ Img: {'Attached' if st.session_state.get('image_base64') else 'None'}")


# ==============================================================================
# 12. MAIN CHAT PIPELINE (THE FRONTIER DISPATCHER)
# ==============================================================================
def handle_chat_turn(user_input: str, client, openrouter_client):
    """
    Central execution pipeline for a single user turn.
    Handles commands, route detection, context assembly, model dispatch,
    response rendering, telemetry, persistence, and title auto-summarization.
    """
    if not user_input or not isinstance(user_input, str):
        return

    user_input = user_input.strip()
    lowered = user_input.lower()

    current_thread = st.session_state.get("current_chat", "New Chat")
    if current_thread not in st.session_state.chats:
        st.session_state.chats[current_thread] = []

    active_chat_list = st.session_state.chats[current_thread]

    # -------------------------------------------------------------------------
 # COMMAND OVERRIDES (stateful actions that do not generate a chat response)
    # -------------------------------------------------------------------------
    if lowered.startswith("/clear"):
        st.session_state.chats[current_thread] = []
        save_chats_to_disk()
        st.toast("Chat thread cleared!", icon="🧹")
        st.rerun()
        return

    if lowered.startswith("/export"):
        md_data = export_chat_as_markdown(active_chat_list, current_thread)
        st.download_button(
            "📥 Download Markdown",
            md_data,
            file_name=f"{re.sub(r'[^a-zA-Z0-9_-]', '_', current_thread).lower()}_export.md",
            mime="text/markdown",
        )
        return

if lowered.startswith("/summarize"):
    with st.chat_message("assistant"):
        with st.spinner("Summarizing conversation..."):
            payload = "Summarize this conversation concisely:\n\n" + str(active_chat_list)
            try:
                summary = client.chat.completions.create(
                    model=st.session_state.selected_model,
                    messages=[{"role": "user", "content": payload}],
                    temperature=0.3,
                    max_tokens=1024,
                ).choices[0].message.content
            except Exception as exc:
                summary = f"Summarization failed: {exc}"
        st.markdown(summary)
    active_chat_list.append({"role": "assistant", "content": summary})
    save_chats_to_disk()
    st.rerun()
    return

if lowered.startswith("/memory"):
    recalled = search_past_memory(user_input, active_chat_list)
    with st.chat_message("assistant"):
        if recalled:
            st.markdown(f"**Recalled Context:**\n\n{recalled}")
        else:
            st.info("No strong memory matches found in this thread.")
    active_chat_list.append({"role": "assistant", "content": recalled or "No memory matches."})
    save_chats_to_disk()
    st.rerun()
    return

    # -------------------------------------------------------------------------
    # APPEND USER MESSAGE & DISPLAY IMMEDIATELY
    # -------------------------------------------------------------------------
    active_chat_list.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # -------------------------------------------------------------------------
    # INTENT & ROUTE RESOLUTION
    # -------------------------------------------------------------------------
    detected_route = "ROUTE_STANDARD"

    if lowered.startswith("/search"):
        detected_route = "ROUTE_SEARCH"
    elif lowered.startswith(("/image", "/imagine", "/draw")):
        detected_route = "ROUTE_IMAGE_GEN"
    elif lowered.startswith("/debug"):
        detected_route = "ROUTE_DEBUG"
    elif lowered.startswith("/read"):
        detected_route = "ROUTE_READ"
    elif lowered.startswith("/enhance"):
        detected_route = "ROUTE_ENHANCE"
    else:
        if st.session_state.get("auto_search", False):
            if needs_automatic_search(user_input):
                detected_route = "ROUTE_SEARCH"
        if detected_route == "ROUTE_STANDARD" and client:
            try:
                intent = classify_user_intent(user_input, client, st.session_state.selected_model)
                route_map = {
                    "IMAGE": "ROUTE_IMAGE_GEN",
                    "DEBUG": "ROUTE_DEBUG",
                    "SEARCH": "ROUTE_SEARCH",
                    "READ": "ROUTE_READ",
                    "MEMORY": "ROUTE_MEMORY",
                    "SUMMARIZE": "ROUTE_SUMMARIZE",
                }
                detected_route = route_map.get(intent, "ROUTE_STANDARD")
            except Exception as exc:
                logging.warning("[ROUTE] Intent classification failed: %s", exc)

    # -------------------------------------------------------------------------
 # PROMPT ENHANCEMENT (if toggled and applicable)
    # -------------------------------------------------------------------------
    processed_prompt = user_input
    if detected_route == "ROUTE_STANDARD" and st.session_state.get("prompt_enhance", False):
        if client:
            try:
                with st.spinner("✨ Enhancing prompt structure..."):
                    enhanced = enhance_user_prompt(user_input, client)
                    if enhanced and len(enhanced) > len(user_input):
                        processed_prompt = enhanced
                        st.info(f"**Enhanced Prompt:** {processed_prompt}")
            except Exception as exc:
 logging.warning("[ENHANCE] %s", exc)

    # -------------------------------------------------------------------------
    # STYLE & TEMPERATURE HEURISTICS
    # -------------------------------------------------------------------------
    casual_triggers = {
        "hi", "hello", "hey", "howdy", "sup", "what's up",
        "thanks", "thank you", "cool", "nice", "ok", "bye",
    }
    analytical_keywords = [
        "compare", "vs", "probability", "percent", "rate",
        "code", "architecture", "refactor", "math", "algorithm",
    ]

    words = set(re.findall(r"\w+", lowered))

    if len(lowered.split()) < 8 and words.intersection(casual_triggers):
        detected_style = "CASUAL"
        active_temperature = 0.85
    elif any(kw in lowered for kw in analytical_keywords):
        detected_style = "ANALYTICAL"
        active_temperature = 0.15
    else:
        detected_style = "GENERAL"
        active_temperature = float(st.session_state.get("temperature", 0.7))

    # Override temperature from settings regardless if not extreme style
    if detected_style not in ("CASUAL", "ANALYTICAL"):
 active_temperature = float(st.session_state.get("temperature", 0.7))

    # -------------------------------------------------------------------------
    # SYSTEM PROMPT CONSTRUCTION
    # -------------------------------------------------------------------------
    retrieved_memory = ""
    if "search_past_memory" in globals():
        retrieved_memory = search_past_memory(processed_prompt, active_chat_list)

    system_prompt = build_dynamic_system_prompt(
        processed_prompt,
        st.session_state.get("personality", "Helpful Assistant"),
        st.session_state.get("target_language", "English"),
        detected_style,
    )

    if st.session_state.get("system_prompt_override"):
        system_prompt = st.session_state.system_prompt_override

    if retrieved_memory:
        system_prompt += f"\n\n[MEMORY RETRIEVAL]:\n{retrieved_memory}"

    if st.session_state.get("doc_context"):
        system_prompt += f"\n\n[DOCUMENT CONTEXT]:\n{str(st.session_state.doc_context)[:4000]}"

    system_prompt += "\n\n[STRICT CONTEXT RULE]: Always maintain awareness of prior chat history."

    # -------------------------------------------------------------------------
    # API MESSAGE BUDGET ASSEMBLY
    # -------------------------------------------------------------------------
    api_messages = [{"role": "system", "content": system_prompt}]
    for m in active_chat_list:
        role = m.get("role", "user")
        content = m.get("content", "")
        api_messages.append({"role": role, "content": content})

    max_budget = int(st.session_state.get("max_tokens", 4096)) - 256
    api_messages = enforce_context_window(api_messages, max_token_budget=max_budget)

    # -------------------------------------------------------------------------
    # GLOBAL TIMERS
    # -------------------------------------------------------------------------
    start_time = time.time()
    final_reply = ""

    ##########################################################################
    # ROUTE DISPATCHER
    # #########################################################################
    with st.chat_message("assistant"):

        # ---------------------------------------------------------------------
        # ROUTE: ENHANCE (meta — show prompt then treat as standard afterward)
        # ---------------------------------------------------------------------
        if detected_route == "ROUTE_ENHANCE":
            st.info("✨ Prompt enhancement complete. Sending enhanced prompt to standard model...")
            # Re-classify as standard for actual generation below
            detected_route = "ROUTE_STANDARD"

        # ---------------------------------------------------------------------
        # ROUTE A: LIVE WEB SEARCH
        # ---------------------------------------------------------------------
        if detected_route == "ROUTE_SEARCH":
            st.info("🔍 Auto-Detected: Web Search Activated")
            clean_query = re.sub(r"^/search\s*", "", user_input, flags=re.IGNORECASE).strip()

            if "execute_deconstructed_multi_search" in globals() and client:
                with st.spinner("Deconstructing & synthesizing multi-angle search..."):
                    final_reply = execute_deconstructed_multi_search(
                        clean_query, client, st.session_state.selected_model
                    )
                st.markdown(final_reply)
            elif "perform_live_search" in globals():
                with st.status("🌐 Searching the web...", expanded=True) as status:
                    raw_search_data = perform_live_search(clean_query)
                    status.update(label="✅ Search completed!", state="complete", expanded=False)

                synthesis_prompt = (
                    f"Answer using ONLY the search context provided.\n\n"
                    f"CONTEXT:\n{raw_search_data}\n\nQUERY:\n{clean_query}"
                )
                completion = client.chat.completions.create(
                    model=st.session_state.selected_model,
                    messages=[{"role": "user", "content": synthesis_prompt}],
                    temperature=0.2,
                )
 final_reply = completion.choices[0].message.content or ""
                st.markdown(final_reply)
            else:
                final_reply = "⚠️ Search tool functions are not available."
                st.warning(final_reply)

        # ---------------------------------------------------------------------
        # ROUTE B: AI IMAGE GENERATION
        # ---------------------------------------------------------------------
        elif detected_route == "ROUTE_IMAGE_GEN":
            clean_prompt = re.sub(
                r"^/(image|imagine|draw|generate)\s*",
                "",
                user_input,
                flags=re.IGNORECASE,
            ).strip()
            if not clean_prompt:
                clean_prompt = user_input

            with st.spinner("🎨 Generating high-quality AI artwork..."):
                enhanced = f"{clean_prompt}, high resolution, detailed, vivid colors"
                encoded = urllib.parse.quote(enhanced)
                seed_val = random.randint(1, 99999)

 img_url = (
                    f"https://image.pollinations.ai/prompt/{encoded}"
                    f"?width=1024&height=1024&seed={seed_val}"
                    f"&model=flux&enhance=true&nologo=true"
                )
                final_reply = (
                    f"🎨 **Generated Image for:** *'{clean_prompt}'*\n\n"
                    f"![AI Image]({img_url})"
                )
                st.markdown(final_reply)

        # ---------------------------------------------------------------------
        # ROUTE C: AUTONOMOUS CODE DEBUGGER
        # ---------------------------------------------------------------------
        elif detected_route == "ROUTE_DEBUG":
            st.info("🛠️ *Auto-Detected: Code Debugger Activated*")
            clean_code = re.sub(r"^/debug\s*", "", user_input, flags=re.IGNORECASE).strip()

            if "run_autonomous_code_debugger" in globals():
                fixed_code = run_autonomous_code_debugger(
                    clean_code, client, st.session_state.selected_model
                )
                final_reply = f"```python\n{fixed_code}\n```"
            else:
 final_reply = "**System Debug Payload:**\n* Debugger module not available."
            st.markdown(final_reply)

        # ---------------------------------------------------------------------
        # ROUTE D: WEB PAGE READER / SCRAPER
        # ---------------------------------------------------------------------
        elif detected_route == "ROUTE_READ":
            target_url = re.sub(r"^/read\s*", "", user_input, flags=re.IGNORECASE).strip()
            st.info(f"🌐 Fetching content from `{target_url}`...")

 page_text = scrape_web_page(target_url)
            if page_text.startswith("⚠️"):
                final_reply = page_text st.error(final_reply)
            else:
                prompt_with_url = (
                    f"Analyze and summarize the content from {target_url}:\n\n{page_text}"
                )
                try:
                    response = client.chat.completions.create(
                        model=st.session_state.selected_model,
                        messages=[{"role": "user", "content": prompt_with_url}],
                        temperature=active_temperature,
                    )
 final_reply = response.choices[0].message.content or ""
                    st.markdown(final_reply)
                except Exception as exc:
                    final_reply = f"LLM synthesis failed after scrape: {exc}"
 st.error(final_reply)

        # ---------------------------------------------------------------------
        # ROUTE E: STANDARD CHAT COMPLETION (with Vision Branch)
        # ---------------------------------------------------------------------
        else:
            # Branch E1: Vision (image uploaded + OpenRouter client available)
            if st.session_state.get("image_base64") and openrouter_client:
                prompt_text = (
                    processed_prompt.strip()
                    if processed_prompt.strip()
                    else "Describe and analyze this image in detail."
                )
                vision_messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{st.session_state.image_mime_type};base64,{st.session_state.image_base64}"
                                },
                            },
                        ],
                    }
                ]

 # Prepend system + text history
                history_text = [
                    {"role": m["role"], "content": str(m["content"])}
                    for m in active_chat_list[:-1]
                    if isinstance(m.get("content"), str)
                ]

                v_messages = [{"role": "system", "content": system_prompt}]
                v_messages.extend(history_text)
                v_messages.extend(vision_messages)

                success = False
                with st.spinner("👁️ Analyzing image with Vision..."):
                    for v_model in VISION_MODELS:
                        try:
                            response = openrouter_client.chat.completions.create(
                                model=v_model,
                                messages=v_messages,
                                temperature=active_temperature,
                            )
                            final_reply = response.choices[0].message.content or ""
                            st.markdown(final_reply)
 success = True
                            break
                        except Exception:
                            continue if not success:
 st.info("Vision endpoints busy. Falling back to text model...")
                    fallback_msg = (
                        f"[Attached Image]\nUser Prompt: {prompt_text}"
                    )
                    api_messages.append({"role": "user", "content": fallback_msg})
                    try:
                        response = client.chat.completions.create(
                            model=st.session_state.selected_model,
                            messages=api_messages,
                            temperature=active_temperature,
 )
                        final_reply = response.choices[0].message.content or ""
                        st.markdown(final_reply)
 except Exception as exc:
                        final_reply = f"Vision fallback failed: {exc}"
                        st.error(final_reply)

            # Branch E2: Streaming Standard Text
            else:
                # If analytical and not huge, optionally use reflection (non-streaming)
                # We default to streaming for UX, but you could swap this block conditionally.
                try:
                    stream = client.chat.completions.create(
                        model=st.session_state.selected_model,
                        messages=api_messages,
                        temperature=active_temperature,
                        stream=True,
                    )

 def _stream_generator():
                        for chunk in stream:
                            if (
 chunk.choices
 and len(chunk.choices) > 0
                                and chunk.choices[0].delta
                            ):
                                delta = chunk.choices[0].delta.content or ""
 yield delta

                    raw_reply = st.write_stream(_stream_generator)
                    final_reply = sanitize_and_repair_formatting(raw_reply)

 except Exception as exc:
                    final_reply = f"⚠️ Streaming error: {exc}"
                    st.error(final_reply)

    # -------------------------------------------------------------------------
    # TELEMETRY, PERSISTENCE & STATE WRAP-UP
    # -------------------------------------------------------------------------
    if final_reply:
        active_chat_list.append({"role": "assistant", "content": final_reply})

    latency_seconds = round(time.time() - start_time, 2)
    in_words = len(user_input.split())
    out_words = len(final_reply.split()) if final_reply else 0
    est_tokens = int((in_words + out_words) * 1.33)

    tele = st.session_state.get("telemetry", {})
    if not isinstance(tele, dict):
        tele = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}
    tele["requests"] = tele.get("requests", 0) + 1
    tele["est_tokens"] = tele.get("est_tokens", 0) + est_tokens
    tele["last_latency"] = latency_seconds
    st.session_state.telemetry = tele

    # Auto-rename generic chat titles
    if "auto_summarize_chat_title" in globals() and client:
        try:
            auto_summarize_chat_title(
                chat_history=active_chat_list,
                client=client,
                current_name=st.session_state.current_chat,
            )
        except Exception as exc:
            logging.warning("[MAIN] Summarizer: %s", exc)

    # Atomic disk sync
    try:
        save_chats_to_disk()
    except Exception as exc:
        logging.warning("[MAIN] Disk sync: %s", exc)

    # Rerun to normalize the UI (history loop picks up the assistant message cleanly)
    st.rerun()


# ==============================================================================
# 13. ORCHESTRATION
# ==============================================================================
def main():
    """
    Root orchestrator. Must be the first Streamlit call chain on script load.
    """
    st.set_page_config(
        page_title="Frontier AI Workspace",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Custom CSS injection for subtle polish
    st.markdown(
        """
        <style>
        .stChatMessage { border-radius: 12px; }
        .stChatMessage div { line-height: 1.6; }
        div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] > div[data-testid="stVerticalBlock"] {
 gap: 0.5rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Initialize state & clients
    initialize_session_state()
    client, openrouter_client = initialize_clients()

    # -------------------------------------------------------------------------
    # SIDEBAR
    # -------------------------------------------------------------------------
    initialize_sidebar_ui()

    # -------------------------------------------------------------------------
    # MAIN WORKSPACE
    # -------------------------------------------------------------------------
    render_header_area()

    # Optional telemetry display (collapsible)
    with st.expander("📊 Workspace Telemetry & Health Monitor", expanded=False):
        render_telemetry_dashboard()

    st.markdown("---")

    # Document canvas if loaded
    render_document_canvas()

    # Chat history
    active_chat_list = st.session_state.chats.get(st.session_state.current_chat, [])
    render_chat_history_thread(active_chat_list)

    # Voice input
    render_voice_input(client)

    # Buffer redirect (voice or presets)
    buffered = st.session_state.get("input_buffer", "")
    if buffered:
        st.session_state.input_buffer = ""
        handle_chat_turn(buffered, client, openrouter_client)
        # handle_chat_turn ends with st.rerun(), so this point is technically unreachable
        return

    # -------------------------------------------------------------------------
    # SINGLE CHAT INPUT (Unified)
    # -------------------------------------------------------------------------
    user_input = st.chat_input(
        "Ask anything, or use /search, /image, /debug, /enhance, /read, /memory, /summarize, /clear ...",
        key="primary_chat_input",
 )

    if user_input:
        handle_chat_turn(user_input, client, openrouter_client)

    render_footer_status_bar()


# ==============================================================================
# ENTRYPOINT
# ==============================================================================
if __name__ == "__main__":
    main()
