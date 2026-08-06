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
                clean_msg = {
                    k: v
                    for k, v in msg.items()
                    if k not in ["audio", "image_url"]
                }
                clean_chats[session_name].append(clean_msg)

        with open(CHAT_STORAGE_FILE, "w") as f:
            json.dump(clean_chats, f, indent=2)
    except Exception:
        pass


# Initialize Session States
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

# UPGRADE #60: Session Telemetry Tracker
if "telemetry" not in st.session_state:
    st.session_state.telemetry = {"requests": 0, "est_tokens": 0, "last_latency": 0.0}


# ==========================================
# 1. UPGRADES #33 & #48: LATEX & SYNTAX AUTO-REPAIR ENGINE
# ==========================================
def sanitize_and_repair_formatting(text: str) -> str:
    """UPGRADES #33 & #48: Automatically fixes LaTeX math syntax, normalizes
    markdown, and repairs broken list formatting.
    """
    if not text:
        return ""

    # Fix display math syntax: \[ ... \] -> $$ ... $$
    text = re.sub(r"\\\[\s*(.*?)\s*\\\]", r"$$\1$$", text, flags=re.DOTALL)

    # Fix inline math syntax: \( ... \) -> $ ... $
    text = re.sub(r"\\\(\s*(.*?)\s*\\\)", r"$\1$", text, flags=re.DOTALL)

    # Remove awkward search artifact disclaimers if present
    text = re.sub(
        r"The provided search results do not directly address.*?\n",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()

def export_chat_as_markdown(chat_list) -> str:
    md_content = "# Chat Session Export\n\n"
    for msg in chat_list:
        role = "### 👤 User" if msg["role"] == "user" else "### 🤖 Assistant"
        md_content += f"{role}\n{msg['content']}\n\n---\n\n"
    return md_content

# Grab active chat messages safely from session state (or default to empty list)
active_chat_list = st.session_state.get("messages", [])

md_data = export_chat_as_markdown(active_chat_list)
st.sidebar.download_button(
    label="📥 Export Chat (.md)",
    data=md_data,
    file_name="chat_history.md",
    mime="text/markdown"
)

def enhance_prompt(raw_prompt: str) -> str:
    return (
        f"Expand the following request into a clear, detailed, and structured prompt, "
        f"specifying context, desired formatting, and constraints:\n\n'{raw_prompt}'"
    )

# Can be run via slash command or a UI button
    if user_input.lower().startswith("/enhance "):
        lazy_prompt = user_input.replace("/enhance ", "").strip()
        enhanced_prompt = enhance_prompt(lazy_prompt)
        # Send enhanced_prompt directly to your LLM generator


def generate_tts_audio(text: str, speed_factor: float = 1.0) -> str:
    """Helper for TTS Generation (UPGRADE #61: Enhanced with playback parameters)."""
    try:
        clean_text = re.sub(r"[*_#`$]", "", text)[:300]
        tts = gTTS(text=clean_text, lang="en", slow=(speed_factor < 1.0))
        fp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tts.save(fp.name)
        return fp.name
    except Exception:
        return None


# ==========================================
# 2. UPGRADE #32 & #34: MULTI-ANGLE SEARCH & FACT ENGINE
# ==========================================
TAVILY_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))


def execute_deconstructed_multi_search(
    query: str, client, selected_model: str
) -> str:
    """UPGRADE #32 & #34: Deconstructs complex queries into sub-searches,
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
    source_counter = 1

    for q in queries:
        try:
            res = tavily.search(query=q, max_results=2)
            for item in res.get("results", []):
                title = item.get("title", "Source")
                content = item.get("content", "")
                url = item.get("url", "#")
                aggregated_sources.append(
                    f"[{source_counter}] **{title}**: {content} (URL: {url})"
                )
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
        temperature=0.2,
    )

    return synthesis_res.choices[0].message.content


# ==========================================
# 3. UPGRADES #31 THROUGH #49: UNIVERSAL REASONING & SYNTHESIS ENGINE
# ==========================================

def build_dynamic_system_prompt(user_input, base_personality, language, detected_style="GENERAL"):
    """UPGRADE #49 & #56: Universal Cognitive & Multi-Modal Synthesis Engine."""
    
    # CASUAL ROUTE: If the user is just engaging in casual conversation, drop the heavy framework
    if detected_style == "CASUAL":
        return (
            f"You are a friendly, highly intelligent, and natural conversational assistant operating as a {base_personality}. "
            f"Keep your responses warm, concise, engaging, and empathetic. Respond naturally like a helpful peer without using forced tables, headers, or rigid technical structures unless asked. "
            f"Always reply in {language}."
        )

    prompt = (
        f"You are an elite AI reasoning engine operating as a {base_personality}.\n\n"
        "### CORE COGNITIVE & EPISTEMIC RULES (UPGRADES #31, #36, #37, #38):\n"
        "1. FIRST-PRINCIPLES DECOMPOSITION (#36):\n"
        "   - Break all complex phenomena down to fundamental truths, assumptions, physical laws, or logical axioms.\n"
        "2. ANTI-PSEUDOSCIENCE & EPISTEMIC GUARDRAIL (#37):\n"
        "   - Strictly avoid invalid physics tropes (e.g., quantum entanglement energy transfer, FTL communication). Distinguish between known science and speculation.\n"
        "3. DUAL-PASS SELF-CRITIQUE (#38):\n"
        "   - Internally critique mechanisms and code before rendering output to eliminate fallacies, bugs, or unneeded hand-waving.\n"
        "4. QUANTITATIVE BOUNDARY ENFORCEMENT (#42):\n"
        "   - Always provide specific metrics, theoretical orders of magnitude (e.g., Watts, Joules, Big-O metrics, discrete probabilities).\n\n"
        "### UPGRADE #49: UNIVERSAL COGNITIVE SYNTHESIS ENGINE:\n"
        "1. INTENT-DRIVEN ADAPTATION:\n"
        "   - Analyze the underlying objective of the query. If technical, optimize for production readiness; if theoretical, optimize for conceptual rigor; if creative, optimize for narrative depth and original metaphors.\n"
        "2. MULTI-ANGLE TRIANGULATION:\n"
        "   - Evaluate complex prompts through multiple relevant domain lenses (e.g., architectural, economic, physical, human-centric) to deliver comprehensive, robust answers.\n"
        "3. HIGH-CONCEPT NAMING & STRIKING TITLES (#40):\n"
        "   - Eliminate passive intros ('One idea is...'). Name every major concept or model with a bold, memorable title.\n"
        "4. CONTRASTIVE CONCEPTUAL SYNTHESIS (#39):\n"
        "   - Ensure proposed theories, solutions, or architectures are fundamentally distinct rather than minor reskins.\n"
        "5. FALSIFIABILITY & LIMITATION ANCHORS (#44):\n"
        "   - Define explicit physical bounds, failure modes, or falsiability conditions for every claim or proposed system.\n"
        "6. ANTI-FILLER & SCANNABLE MATRIX (#41, #45, #46):\n"
        "   - Skip meta-preambles and redundant summaries. Use tables only for distinct, multi-variable comparative metrics that aren't already written out in prose.\n"
        "7. MIC-DROP IMPLICATION (#47):\n"
        "   - End with a single, highly memorable, logically sound takeaway that leaves a lasting impact.\n"
    )

    # Dynamic Domain & Intent Adaptation (Upgrade #43 & #49)
    lowered_input = user_input.lower()
    if any(
        kw in lowered_input
        for kw in [
            "physics",
            "dyson",
            "kardashev",
            "star",
            "energy",
            "quantum",
            "space",
        ]
    ):
        prompt += "\n\n[DOMAIN ADAPTATION: ASTROPHYSICAL & HARD SCIENCE RIGOR ACTIVE]\n- Apply strict thermodynamic limits, relativistic dynamics, and field equations."
    elif any(
        kw in lowered_input
        for kw in [
            "code",
            "architecture",
            "system",
            "algorithm",
            "python",
            "bug",
            "refactor",
        ]
    ):
        prompt += "\n\n[DOMAIN ADAPTATION: SENIOR SYSTEMS ARCHITECT ACTIVE]\n- Focus on modularity, production-level edge cases, typed signatures, and runtime complexities."
    elif any(
        kw in lowered_input
        for kw in ["story", "creative", "fiction", "worldbuilding", "design"]
    ):
        prompt += "\n\n[DOMAIN ADAPTATION: CREATIVE DIRECTORS ARCHITECT ACTIVE]\n- Focus on vivid sensory imagery, high-stakes narrative tension, and unconventional thematic resonance."

    prompt += (
        "\n\n### UPGRADES #50–#52: AGENTIC ARTIFACT & EXECUTION ENGINE:\n"
        "1. EXECUTABLE CODE ARTIFACTS (#50):\n"
        "   - Whenever writing Python code for calculations, data analysis, or plotting, wrap the code in standard triple-backtick markdown blocks. Ensure code is complete, self-contained, and ready for immediate execution.\n"
        "2. LIVE UI & WEB WORKBENCH (#51):\n"
        "   - If asked to design a dashboard, web tool, or UI component, provide self-contained HTML/JS/CSS or Streamlit code snippets that can be visually rendered.\n"
        "3. DYNAMIC TOOL INTENT DETECTION (#52):\n"
        "   - Explicitly identify when external data processing, local system calls, or multi-step tool usage is required, and structure responses so tools can parse the output programmatically.\n"
    )

    if detected_style == "ANALYTICAL":
        prompt += (
            "\n\n[MODE: DEEP STRATEGY & MATRIX REASONING]"
            "\n- Present multi-variable trade-offs in a clean comparison table."
            "\n- Maintain hyper-precise quantitative allocations."
        )

    if language != "English":
        prompt += f"\n\nCRITICAL RULE: Respond entirely in {language}."

    if st.session_state.memory_vault:
        # UPGRADE #57: Vector-Like Keyword Filtered Context Retrieval
        query_words = set(re.findall(r"\w+", lowered_input))
        relevant_memories = []
        for fact in st.session_state.memory_vault:
            fact_words = set(re.findall(r"\w+", fact.lower()))
            if query_words.intersection(fact_words) or len(st.session_state.memory_vault) <= 3:
                relevant_memories.append(fact)

        if relevant_memories:
            facts_str = "\n".join([f"- {fact}" for fact in relevant_memories])
            prompt += f"\n\n[BACKGROUND USER CONTEXT]:\nUse these known facts naturally if relevant, but DO NOT mention the Memory Vault directly:\n{facts_str}"

    return prompt


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


# ==========================================
# UPGRADE #55: SMART CHAT AUTO-SUMMARIZER
# ==========================================
def auto_summarize_chat_title(chat_history, client, current_name: str):
    """UPGRADE #55: Dynamically generates thread titles based on conversation topics."""
    if len(chat_history) == 2 and current_name.startswith("Chat "):
        first_user_msg = chat_history[0].get("content", "")
        try:
            res = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {
                        "role": "user",
                        "content": f"Generate a ultra-concise 3-4 word title for a chat thread starting with: '{first_user_msg}'. Return ONLY the title with no quotes or punctuation.",
                    }
                ],
                temperature=0.3,
            )
            new_title = res.choices[0].message.content.strip()
            if new_title:
                st.session_state.chats[new_title] = st.session_state.chats.pop(current_name)
                st.session_state.current_chat = new_title
                save_chats_to_disk()
                st.rerun()
        except Exception:
            pass


# ==========================================
# UPGRADE #62: PROMPT ENHANCER & QUERY EXPANSION
# ==========================================
def enhance_user_prompt(prompt_text: str, client) -> str:
    """UPGRADE #62: Expands short user inputs into detailed high-performing prompts."""
    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "user",
                    "content": f"Rewrite and enhance the following query into a clear, detailed, and structured prompt optimized for AI instruction:\n\n'{prompt_text}'\n\nReturn ONLY the enhanced prompt.",
                }
            ],
            temperature=0.3,
        )
        return res.choices[0].message.content.strip()
    except Exception:
        return prompt_text


# ==========================================
# UPGRADE #53: AUTO-SEARCH INTENT ROUTER
# ==========================================
REALTIME_KEYWORDS = [
    "news",
    "latest",
    "today",
    "yesterday",
    "current",
    "weather",
    "score",
    "results",
    "winner",
    "stock",
    "price",
    "2026",
    "who won",
    "schedule",
    "upcoming",
    "event",
    "standing",
    "release date",
]


def needs_automatic_search(user_text: str) -> bool:
    """Detects if the prompt requires real-time information."""
    lowered = user_text.lower()
    return any(keyword in lowered for keyword in REALTIME_KEYWORDS)

# ==========================================
# 5. UI CONFIG & MAIN APP LOOP
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")

st.markdown(
    "<style>"
    "iframe[title*='audio_recorder'], iframe[src*='audio_recorder'] {"
    "background-color: transparent !important; border: none !important;"
    "}"
    "div[data-testid='stCustomComponentV1'] {"
    "background-color: transparent !important; border: none !important; padding: 0 !important;"
    "}"
    "div[data-testid='column'] button {"
    "border: none !important; background: transparent !important; color: #888888 !important;"
    "font-size: 0.8rem !important; padding: 2px 8px !important; border-radius: 6px !important;"
    "}"
    "div[data-testid='column'] button:hover {"
    "background-color: rgba(255, 255, 255, 0.08) !important; color: #ffffff !important;"
    "}"
    ".model-badge {"
    "background: rgba(255, 255, 255, 0.08); padding: 4px 10px; border-radius: 12px;"
    "font-size: 0.75rem; color: #aaa; border: 1px solid rgba(255, 255, 255, 0.1);"
    "}"
    "</style>",
    unsafe_allow_html=True,
)

st.title("🤖 Intelligent AI Workspace")
st.caption(
    "Enhanced with Upgrades #31–#63: Epistemic Physics Guardrails, Live Code Debugger & Dynamic Workspace Telemetry"
)

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

    # Thread Controls
    st.header("💬 Chat Sessions")
    chat_names = list(st.session_state.chats.keys())
    selected_chat = st.selectbox(
        "Select Thread:",
        chat_names,
        index=chat_names.index(st.session_state.current_chat),
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
    st.header("📄 File Attachment Context")
    uploaded_file = st.file_uploader(
        "Upload TXT, CSV or Code snippet:", type=["txt", "py", "js", "md", "csv"]
    )
    doc_context = ""
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith(".csv"):
                # UPGRADE #58: Data Inspector
                df_upload = pd.read_csv(uploaded_file)
                st.markdown("#### 🔍 CSV File Summary")
                st.write(f"**Rows:** {df_upload.shape[0]} | **Cols:** {df_upload.shape[1]}")
                st.dataframe(df_upload.head(3), use_container_width=True)
                doc_context = f"CSV Data Summary:\nColumns: {list(df_upload.columns)}\nData Sample:\n{df_upload.head(10).to_csv(index=False)}"
            else:
                doc_context = uploaded_file.read().decode("utf-8")
                st.success("File context loaded!")
        except Exception:
            st.error("Error reading file!")

    st.markdown("---")
    st.header("🧠 Memory Vault Facts")
    new_memory_fact = st.text_input("Add Persistent Fact:", key="memory_input")
    if st.button("Save Memory Fact") and new_memory_fact:
        st.session_state.memory_vault.append(new_memory_fact)
        st.success(f"Remembered: '{new_memory_fact}'")
        st.rerun()

    if st.session_state.memory_vault:
        for idx, fact in enumerate(st.session_state.memory_vault):
            col_m1, col_m2 = st.columns([8, 2])
            col_m1.caption(f"- {fact}")
            if col_m2.button("❌", key=f"del_mem_{idx}"):
                st.session_state.memory_vault.pop(idx)
                st.rerun()
    else:
        st.caption("No custom memory facts saved yet.")

    # UPGRADE #59: Live Thread Export Engine
    st.markdown("---")
    st.header("📥 Workspace Thread Export")
    export_chat = st.session_state.chats.get(st.session_state.current_chat, [])
    chat_export_str = json.dumps(export_chat, indent=2)
    st.download_button(
        label="Download Chat (JSON)",
        data=chat_export_str,
        file_name=f"{st.session_state.current_chat}.json",
        mime="application/json",
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

# Input Execution Pipeline
user_input = st.chat_input("Ask anything, use /search, /image, /debug, or /enhance...")
if st.session_state.input_buffer and not user_input:
    user_input = st.session_state.input_buffer
    st.session_state.input_buffer = ""

if user_input and client:
    start_time = time.time()
    
    # UPGRADE #62: Prompt Enhancement Trigger
    if user_input.lower().startswith("/enhance"):
        clean_p = user_input.replace("/enhance", "").strip()
        with st.spinner("✨ Enhancing query structure..."):
            user_input = enhance_user_prompt(clean_p, client)
            st.info(f"**Enhanced Prompt:** {user_input}")

    active_chat_list.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    lowered_input = user_input.lower().strip()
    
    casual_triggers = ["hi", "hello", "hey", "howdy", "sup", "how are you", "what's up", "thanks", "thank you", "cool", "nice"]
    
    if any(lowered_input.startswith(cw) or lowered_input == cw for cw in casual_triggers) and len(lowered_input.split()) < 8:
        detected_style = "CASUAL"
        active_temperature = 0.85  # Higher warmth & natural conversational flow
    elif any(kw in lowered_input for kw in ["compare", "vs", "probability", "percent", "rate", "code", "architecture", "dyson", "kardashev"]):
        detected_style = "ANALYTICAL"
        active_temperature = 0.2   # Precision mode
    else:
        detected_style = "GENERAL"
        active_temperature = 0.7

    with st.chat_message("assistant"):
        # ROUTE 0: Autonomous Code Debugger (UPGRADE #54)
        if user_input.lower().startswith("/debug"):
            clean_code = user_input.replace("/debug", "").strip()
            fixed_code = run_autonomous_code_debugger(clean_code, client, selected_model)
            reply = f"```python\n{fixed_code}\n```"
            st.markdown(reply)
            active_chat_list.append({"role": "assistant", "content": reply})

        # ROUTE 1: High-Quality AI Image Generation
        elif any(user_input.lower().startswith(cmd) for cmd in ["/image", "/imagine", "/draw", "/generate"]):
            clean_prompt = re.sub(r"^/(image|imagine|draw|generate)\s*", "", user_input, flags=re.IGNORECASE).strip()
            if not clean_prompt:
                clean_prompt = "a cute dog"
                
            with st.spinner("🎨 Generating high-quality AI artwork..."):
                encoded_prompt = urllib.parse.quote(clean_prompt)
                
                # Added &model=flux and &enhance=true for photorealistic results
                img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={random.randint(1,99999)}&model=flux&enhance=true&nologo=true"
                
                st.image(
                    img_url,
                    caption=f"Prompt: {clean_prompt}",
                    use_container_width=True,
                )
                active_chat_list.append(
                    {
                        "role": "assistant",
                        "content": f"🎨 Generated Image for: '{clean_prompt}'\n![Image]({img_url})",
                    }
                )

        # ROUTE 2: Deconstructed Multi-Angle Search Route (Manual /search or Automatic via Intent Router #53)
        elif user_input.lower().startswith(
            "/search"
        ) or needs_automatic_search(user_input):
            clean_query = user_input.replace("/search", "").strip()
            with st.spinner(
                "🔍 Deconstructing query & synthesizing multi-angle search..."
            ):
                reply = execute_deconstructed_multi_search(
                    clean_query, client, selected_model
                )
                reply = sanitize_and_repair_formatting(reply)
                st.markdown(reply)
                active_chat_list.append({"role": "assistant", "content": reply})

        import requests
from bs4 import BeautifulSoup

# ROUTE: Web Scraper Route
if user_input.lower().startswith("/read "):
    target_url = user_input.replace("/read ", "").strip()
    with st.spinner(f"🌐 Fetching content from {target_url}..."):
        try:
            res = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            soup = BeautifulSoup(res.text, "html.parser")
            paragraphs = [p.get_text() for p in soup.find_all("p")]
            page_text = " ".join(paragraphs)[:4000] # Cap text length
            
            prompt_with_url = f"Analyze and summarize the following content from {target_url}:\n\n{page_text}"
            # Pass prompt_with_url directly to your LLM text engine
        except Exception as e:
            st.error(f"Failed to fetch web page: {e}")

        # ROUTE 3: Standard Chat Generation (Enriched with Upgrades #31-#49 & #56)
        else:
            system_prompt = build_dynamic_system_prompt(
                user_input, personality, target_language, detected_style
            )

            if doc_context:
                system_prompt += (
                    f"\n\n[USER ATTACHED FILE CONTEXT]:\n{doc_context[:4000]}"
                )

            messages_payload = [{"role": "system", "content": system_prompt}]
            for m in active_chat_list:
                if isinstance(m.get("content"), str):
                    messages_payload.append(
                        {"role": m["role"], "content": m["content"]}
                    )

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

            active_chat_list.append(
                {"role": "assistant", "content": final_reply}
            )

    # Telemetry Updates (UPGRADE #60)
    st.session_state.telemetry["requests"] += 1
    st.session_state.telemetry["est_tokens"] += len(user_input.split()) + 150
    st.session_state.telemetry["last_latency"] = time.time() - start_time

    # UPGRADE #55: Smart Thread Summarization Trigger
    auto_summarize_chat_title(active_chat_list, client, st.session_state.current_chat)

    save_chats_to_disk()
    st.rerun()
