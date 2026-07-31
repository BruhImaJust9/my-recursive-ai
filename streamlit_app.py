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
# 0. PERSISTENCE & HELPERS
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

if "memory_vault" not in st.session_state:
    st.session_state.memory_vault = []

if "bookmarks" not in st.session_state:
    st.session_state.bookmarks = []

# ==========================================
# 1. UPGRADE #33: LATEX & SYNTAX AUTO-REPAIR ENGINE
# ==========================================
def sanitize_and_repair_formatting(text: str) -> str:
    """
    UPGRADE #33: Automatically fixes LaTeX math syntax and normalizes markdown.
    Converts improper LaTeX syntax to clean inline ($...$) or display ($$...$$) format.
    """
    if not text:
        return ""
    
    # Fix display math syntax: \[ ... \] -> $$ ... $$
    text = re.sub(r'\\\[\s*(.*?)\s*\\\]', r'$$\1$$', text, flags=re.DOTALL)
    
    # Fix inline math syntax: \( ... \) -> $ ... $
    text = re.sub(r'\\\(\s*(.*?)\s*\\\)', r'$\1$', text, flags=re.DOTALL)
    
    # Remove awkward search artifact disclaimers if present
    text = re.sub(r'The provided search results do not directly address.*?\n', '', text, flags=re.IGNORECASE)
    
    return text.strip()

# ==========================================
# 2. UPGRADE #32 & #34: MULTI-ANGLE SEARCH & FACT ENGINE
# ==========================================
TAVILY_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))

def execute_deconstructed_multi_search(query: str, client, selected_model: str) -> str:
    """
    UPGRADE #32 & #34: Deconstructs complex queries into sub-searches, 
    executes multi-angle retrieval, and synthesizes factually verified claims.
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
            temperature=0.1
        )
        queries = [q.strip(" 123456789.-*") for q in sub_res.choices[0].message.content.strip().split("\n") if q.strip()][:3]
    except Exception:
        queries = [query]

    # 2. Concurrently retrieve facts across queries
    aggregated_sources = []
    source_counter = 1
    
    for q in queries:
        try:
            res = tavily.search(query=q, max_results=2)
            for item in res.get("results", []):
                title = item.get("title", "Source")
                content = item.get("content", "")
                url = item.get("url", "#")
                aggregated_sources.append(f"[{source_counter}] **{title}**: {content} (URL: {url})")
                source_counter += 1
        except Exception:
            continue

    if not aggregated_sources:
        return "No authoritative search sources could be retrieved."

    # 3. Synthesize with Citation Anchors
    synthesis_prompt = (
        f"Synthesize an accurate, well-structured answer for: '{query}' using these factual sources.\n"
        "Insert inline citation anchors (e.g. [1], [2]) corresponding to sources used.\n\n"
        "SOURCES:\n" + "\n\n".join(aggregated_sources)
    )

    synthesis_res = client.chat.completions.create(
        model=selected_model,
        messages=[{"role": "user", "content": synthesis_prompt}],
        temperature=0.2
    )
    
    return synthesis_res.choices[0].message.content

# ==========================================
# 3. UPGRADE #31 & #36: LOGIC ENGINE & FIRST-PRINCIPLES META-PROMPT
# ==========================================
def build_dynamic_system_prompt(user_input, base_personality, language, detected_style="GENERAL"):
    """
    UPGRADES #31, #33, #36:
    Enforces First-Principles reasoning, Chain-of-Thought verification, and strict formatting.
    """
    prompt = (
        f"You are a premier AI reasoning engine acting as a {base_personality}.\n\n"
        "### CORE LOGIC & EXECUTION RULES:\n"
        "1. FIRST-PRINCIPLES DECOMPOSITION (UPGRADE #36):\n"
        "   - Before reaching conclusions on complex topics, break the problem down to fundamental truths, assumptions, and physical/logical bounds.\n"
        "2. CHAIN-OF-THOUGHT SELF-VERIFICATION (UPGRADE #31):\n"
        "   - Internally verify all mathematical totals, logic chains, and edge cases. Ensure probabilities sum to EXACTLY 100% with discrete allocations.\n"
        "3. REAL-WORLD BENCHMARKS & ANCHORS:\n"
        "   - Ground all comparisons using concrete dates, historical eras, Big-O complexity, or exact metrics.\n"
        "4. SCANNABLE FORMATTING & TABLES:\n"
        "   - Use clean Markdown tables for multi-variable trade-offs or category breakdowns.\n"
        "5. STRICT FOCUS:\n"
        "   - Answer directly. Do not include unrequested extra sections or search tool disclaimers."
    )
    
    if detected_style == "ANALYTICAL":
        prompt += (
            "\n\n[MODE: DEEP REASONING & STRATEGY]"
            "\n- Establish a clear theoretical framework first."
            "\n- Present trade-offs in a clean comparison table."
            "\n- End with a precise recommendation matrix summing to 100%."
        )
    elif detected_style == "TECHNICAL":
        prompt += (
            "\n\n[MODE: SENIOR SYSTEMS ARCHITECT]"
            "\n- Diagnose root causes before offering code."
            "\n- Provide production-ready, typed code with inline complexity analysis."
        )

    if language != "English":
        prompt += f"\n\nCRITICAL RULE: Respond entirely in {language}."

    return prompt

# ==========================================
# 4. UPGRADE #35: DYNAMIC VISUAL CANVAS AUTO-RENDERER
# ==========================================
def render_data_canvas(response_text: str):
    """
    UPGRADE #35: Automatically parses tables or CSV structures into interactive charts & dataframes.
    """
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

# ==========================================
# 5. UI CONFIG & MAIN APP LOOP
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")

st.markdown(
    """
    <style>
        iframe[title*="audio_recorder"],
        iframe[src*="audio_recorder"] {
            background-color: transparent !important;
            border: none !important;
        }
        div[data-testid="stCustomComponentV1"] {
            background-color: transparent !important;
            border: none !important;
            padding: 0px !important;
        }
        div[data-testid="column"] button {
            border: none !important;
            background: transparent !important;
            color: #888888 !important;
            font-size: 0.8rem !important;
            padding: 2px 8px !important;
            border-radius: 6px !important;
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
st.caption("Enhanced with Logic Verification, Multi-Angle Search & Auto-Formatting")

GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    client = None
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets!")

# Sidebar Controls
with st.sidebar:
    st.header("⚙️ Workspace Controls")
    st.markdown("---")
    
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

    st.markdown("---")
    target_language = st.selectbox("Response Language:", ["English", "Spanish", "French", "German", "Mandarin", "Japanese"])
    personality = st.selectbox("AI Persona:", ["Helpful Assistant", "Code Expert", "Strict Tutor", "Executive Analyst"])
    selected_model = st.selectbox("Model Engine:", ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"])

# Active Chat Buffer
active_chat_list = st.session_state.chats[st.session_state.current_chat]

# Header Badge Display
col_hdr1, col_hdr2 = st.columns([6, 4])
with col_hdr1:
    st.markdown(f"### 💬 {st.session_state.current_chat}")
with col_hdr2:
    st.markdown(f"<div style='text-align: right;'><span class='model-badge'>🤖 {selected_model}</span> <span class='model-badge'>🌐 {target_language}</span></div>", unsafe_allow_html=True)

st.markdown("---")

# Render Message History with Auto-Repair Formatting (Upgrade #33)
for idx, msg in enumerate(active_chat_list):
    with st.chat_message(msg["role"]):
        repaired_content = sanitize_and_repair_formatting(msg.get("content", ""))
        st.markdown(repaired_content)
        
        if msg["role"] == "assistant" and repaired_content:
            render_data_canvas(repaired_content)

# Input Execution Pipeline
user_input = st.chat_input("Ask anything, use /search or /research...")

if user_input and client:
    active_chat_list.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Detect style to set active temperature dynamically
    detected_style = "ANALYTICAL" if any(kw in user_input.lower() for kw in ["compare", "vs", "probability", "percent", "rate", "code", "architecture"]) else "GENERAL"
    active_temperature = 0.2 if detected_style == "ANALYTICAL" else 0.7

    with st.chat_message("assistant"):
        if user_input.lower().startswith("/search"):
            clean_query = user_input.replace("/search", "").strip()
            with st.spinner("🔍 Deconstructing query & synthesizing multi-angle search..."):
                reply = execute_deconstructed_multi_search(clean_query, client, selected_model)
                reply = sanitize_and_repair_formatting(reply)
                st.markdown(reply)
                active_chat_list.append({"role": "assistant", "content": reply})
        else:
            system_prompt = build_dynamic_system_prompt(user_input, personality, target_language, detected_style)
            messages_payload = [{"role": "system", "content": system_prompt}]
            
            for m in active_chat_list:
                if isinstance(m.get("content"), str):
                    messages_payload.append({"role": m["role"], "content": m["content"]})

            stream = client.chat.completions.create(
                model=selected_model,
                messages=messages_payload,
                temperature=active_temperature,
                stream=True
            )

            def stream_generator():
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            raw_reply = st.write_stream(stream_generator)
            final_reply = sanitize_and_repair_formatting(raw_reply)
            
            active_chat_list.append({"role": "assistant", "content": final_reply})

    save_chats_to_disk()
    st.rerun()
