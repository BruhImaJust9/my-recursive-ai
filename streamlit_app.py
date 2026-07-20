import streamlit as st
import random
import requests
import json
import os
import base64
import glob
import io
import sys
import importlib.util
import subprocess
import re
import time
import zipfile
import uuid
from PIL import Image
from datetime import datetime

# ==========================================
# SYSTEM SETUP & DIRECTORY INITIALIZATION
# ==========================================
HF_TOKEN = st.secrets.get("HF_TOKEN", "")
CHATS_DIR = "saved_chats"
SKILLS_DIR = "mutated_skills"

for directory in [CHATS_DIR, SKILLS_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

GEN_TRACKER_FILE = os.path.join(CHATS_DIR, "universal_generation_counter.json")
PROFILE_FILE = os.path.join(CHATS_DIR, "user_profile.json")

# ==========================================
# AUTHENTICATION & MULTI-USER ISOLATION WALL
# ==========================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("Private ASI Platform Login")
    st.markdown("---")
    st.info("Please verify credentials to unlock your isolated neural workspace.")
    
    master_pass = st.secrets.get("MASTER_PASSWORD", "admin123") 
    user_password_input = st.text_input(" Enter Security Access Key:", type="password")
    
    if st.button(" Authenticate and Mount Engine", use_container_width=True):
        if user_password_input == master_pass:
            st.session_state.authenticated = True
            st.toast(" Access granted! Initializing secure environments...", icon="🔓")
            st.rerun()
        else:
            st.error(" Invalid Access Key. Deployment loop suspended.")
            
    st.stop() 

user_folder_name = "master_admin"

CHATS_DIR = os.path.join("saved_chats", user_folder_name)
SKILLS_DIR = os.path.join("mutated_skills", user_folder_name)

for directory in [CHATS_DIR, SKILLS_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

# UI GLASSMORPHISM & MASTER DESIGN MATRIX
st.set_page_config(page_title="Recursive Self-Improving ASI", page_icon="🌀", layout="wide")
st.markdown(
    """
    <style>
        .stApp {
            background: linear-gradient(135deg, #0d0e15 0%, #171926 100%);
            color: #e2e8f0;
        }
        div[data-testid="stSidebar"] {
            background-color: #090a0f !important;
            border-right: 1px solid rgba(255, 215, 0, 0.1);
        }
        .stSlider label, .stToggle label, .stMarkdown h3 {
            color: #ffd700 !important;
            font-family: 'Space Grotesk', sans-serif;
        }
        div.stButton > button:first-child {
            background: linear-gradient(45deg, #4f46e5 0%, #7c3aed 100%);
            color: white;
            border: none;
            transition: all 0.3s ease;
        }
        div.stButton > button:first-child:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(124, 58, 237, 0.4);
        }
    </style>
    """,
    unsafe_allow_html=True
)

if "processing" not in st.session_state:
    st.session_state.processing = False
if "last_latency" not in st.session_state:
    st.session_state.last_latency = 0.0

def load_universal_generation():
    if os.path.exists(GEN_TRACKER_FILE):
        try:
            with open(GEN_TRACKER_FILE, "r") as f:
                return json.load(f).get("current_gen", 1)
        except:
            return 1
    return 1

def save_universal_generation(gen_num):
    try:
        with open(GEN_TRACKER_FILE, "w") as f:
            json.dump({"current_gen": gen_num}, f)
    except:
        pass

def load_user_profile():
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_user_profile(profile_dict):
    try:
        with open(PROFILE_FILE, "w") as f:
            json.dump(profile_dict, f, indent=4)
    except:
        pass

def get_saved_chats():
    files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    chat_ids = [os.path.basename(f).replace(".json", "") for f in files]
    return [c for c in chat_ids if c not in ["universal_generation_counter", "user_profile"]]

def extract_and_update_profile(prompt_text):
    profile = load_user_profile()
    lowered = prompt_text.lower()
    updated = False
    
    if "my name is " in lowered:
        name_part = prompt_text.split("my name is ")[1].split(".")[0].split(",")[0].strip()
        profile["user_name"] = name_part
        updated = True
        
    interests_keywords = {
        "coding": ["python", "javascript", "c++", "rust", "html", "developer", "coding", "programming"],
        "math": ["calculus", "algebra", "multiply", "equation", "matrix", "derivative", "math"],
        "science": ["physics", "quantum", "biology", "chemistry", "space", "cosmos"]
    }
    
    if "interests" not in profile:
        profile["interests"] = []
        
    for category, keywords in interests_keywords.items():
        if any(kw in lowered for kw in keywords) and category not in profile["interests"]:
            profile["interests"].append(category)
            updated = True
            
    if updated:
        save_user_profile(profile)

def generate_image(prompt_text):
    API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        response = requests.post(API_URL, headers=headers, json={"inputs": prompt_text}, timeout=20)
        if response.status_code == 200:
            return Image.open(io.BytesIO(response.content))
        else:
            st.error(f"Image engine returned code: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Image generation crash: {e}")
        return None

def ensure_package_installed(package_name):
    try:
        importlib.import_module(package_name)
        return f"Package '{package_name}' is already available."
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
            return f" Package '{package_name}' was successfully installed on the fly!"
        except Exception as e:
            return f" Failed to install package '{package_name}': {str(e)}"

# --- Internet Search Tool Integration ---
def execute_internet_search(query: str) -> str:
    """
    Searches the live web using DuckDuckGo and returns a summary of the top results.
    Bypasses cloud restrictions using the lightweight HTML backend.
    """
    try:
        results = []
        with DDGS() as ddgs:
            # Shifted to the 'lite' HTML backend to bypass cloud blocking rules
            ddgs_generator = ddgs.text(keywords=query, backend="lite", max_results=4)
            
            if ddgs_generator:
                if isinstance(ddgs_generator, list):
                    results = ddgs_generator
                else:
                    results = list(ddgs_generator)
            
        if not results:
            return "No live search results found for this query."
            
        formatted_results = []
        for i, r in enumerate(results):
            snippet = r.get('body', r.get('snippet', ''))
            formatted_results.append(
                f"Result {i+1}:\nTitle: {r.get('title')}\nSource: {r.get('href')}\nSnippet: {snippet}\n"
            )
            
        return "\n---\n".join(formatted_results)

    except Exception as e:
        return f"Error executing web search: {str(e)}"
    try:
        with open(file_path, "r") as f:
            content = f.read()
            for line in content.split("\n"):
                if line.startswith("import ") or line.startswith("from "):
                    parts = line.split()
                    if len(parts) > 1:
                        pkg = parts[1].split(".")[0]
                        if pkg not in ["sys", "os", "json", "math", "datetime", "requests", "time", "glob", "io"]:
                            ensure_package_installed(pkg)

        spec = importlib.util.spec_from_file_location(skill_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, 'execute'):
            return str(module.execute(argument_string))
        return f"Error: Skill '{skill_name}' missing mandatory 'execute(user_input)' entrypoint."
    except Exception as e:
        return f"Runtime Execution Crash in skill '{skill_name}': {str(e)}"

def dynamic_mutate_skill(skill_name, updated_code):
    is_safe, status = cev_safety_filter(updated_code)
    if not is_safe:
        return f"Mutation blocked: {status}"
        
    file_path = os.path.join(SKILLS_DIR, f"{skill_name}.py")
    with open(file_path, "w") as f:
        f.write(updated_code)
    return f"Vault skill '{skill_name}' upgraded successfully."

def compress_memory_if_needed():
    if "chat_history" not in st.session_state or len(st.session_state.chat_history) < 8:
        return
        
    if "long_term_memory_bank" not in st.session_state:
        st.session_state.long_term_memory_bank = []
        
    st.toast("🔮 ASI Memory Threshold reached. Commencing Semantic Compression...", icon="🧠")
    
    block_to_compress = st.session_state.chat_history[:4]
    st.session_state.chat_history = st.session_state.chat_history[4:]
    
    condensation_prompt = "You are a cognitive memory compression node. Synthesize the following dialog into a highly dense, bulleted summary of key data facts, agreements, user traits, and calculation results:\n\n"
    for u_q, a_a, _ in block_to_compress:
        condensation_prompt += f"User: {u_q}\nAI: {a_a}\n---\n"
        
    try:
        condensed_facts = query_free_llm(
            condensation_prompt, 
            "Output only the bulleted summary data. Be precise.", 
            "llama-3.1-8b-instant", 
            is_validation=True
        )
        st.session_state.long_term_memory_bank.append(condensed_facts)
        st.toast("⚡ Semantic compression successful! Token space optimized.", icon="🚀")
    except Exception as e:
        st.toast(f"Memory condensation warning: {e}", icon="⚠️")

def run_recursive_improvement():
    if "chat_history" not in st.session_state or len(st.session_state.chat_history) < 1:
        return "Initial brain state active. Awaiting user interaction to evaluate performance.", True
        
    recent_history = st.session_state.chat_history[-5:]
    formatted_history = ""
    for idx, (user_q, ai_a, _) in enumerate(recent_history):
        formatted_history += f"--- Exchange {idx + 1} ---\nUSER: {user_q}\nASSISTANT: {ai_a}\n\n"
        
    meta_prompt = f"""
    You are the core evolutionary optimization framework for an Artificial Superintelligence (ASI).
    Rewrite the system instructions to make the AI smarter based on recent interaction context.
    
    [RECENT CONVERSATION HISTORY]:
    {formatted_history}
    
    [CURRENT SYSTEM PROMPT]: "{st.session_state.system_instruction}"
    
    CRITICAL: Output ONLY the raw text of the new system instruction. No formatting, no quotes.
    """
    try:
        evolved_instruction = query_free_llm(
            meta_prompt, 
            "You are a strict meta-cognitive compiler. Output only the updated instruction text.",
            "llama-3.1-8b-instant",
            is_validation=True
        ).strip().strip('"').strip("'")
        
        if evolved_instruction and len(evolved_instruction) > 20:
            is_safe, status = cev_safety_filter(evolved_instruction)
            if not is_safe:
                return f"Self-improvement aborted. Safety status: {status}", False
                
            st.session_state.system_instruction = evolved_instruction
            st.session_state.current_gen += 1
            save_universal_generation(st.session_state.current_gen)
            return "Cognitive optimization successful: Rules upgraded based on contextual performance analysis.", True
        return "Evolution skipped: Evolved instruction was too short or corrupted.", False
    except Exception as e:
        return f"Evolution suspended due to core node error: {str(e)}", False

        def get_compiled_skills() -> str:
    """
    Scans the local mutated_skills folder and compiles a manifest 
    of all active custom tool names and documentation for the LLM context window.
    """
    try:
        skills = glob.glob(os.path.join(SKILLS_DIR, "*.py"))
        if not skills:
            return "No custom tool skills are currently unlocked in the vault."
        
        manifest = []
        for s in skills:
            s_name = os.path.basename(s).replace(".py", "")
            try:
                with open(s, "r") as f:
                    content = f.read()
                # Extract docstrings or description headers if available
                docstring_match = re.search(r'"""(.*?)"""', content, re.DOTALL)
                desc = docstring_match.group(1).strip() if docstring_match else "No description provided."
                manifest.append(f"- Skill Name: '{s_name}'\n  Usage Directive: [EXECUTE: {s_name}(argument_string)]\n  Capability: {desc}")
            except Exception:
                continue
        return "\n\n".join(manifest)
    except Exception as e:
        return f"Error assembling skill manifest: {str(e)}"

def query_free_llm(prompt, system_prompt, model_id, is_validation=False):
    if not HF_TOKEN:
        return " Please add your Key (saved as HF_TOKEN) to your Streamlit secrets!"
        
    final_system_prompt = system_prompt
    if st.session_state.get("deep_thinking", False):
        final_system_prompt += (
            "\n\nCRITICAL INSTRUCTION: You must think step-by-step before answering. "
            "Start your response with <thinking> and write out your raw thought process. "
            "Once complete, close the tag with </thinking> and write your final response."
        ) 
        
    compiled_tools = get_compiled_skills()
    final_system_prompt += f"\n\n[UNLOCKED SKILL VAULT EXTRACTION]:\n{compiled_tools}"
    
    if not is_validation:
        final_system_prompt += (
            "\n\nAUTONOMOUS INTERCEPT PROTOCOLS:\n"
            "1. To run a native skill tool, output exactly on its own line: [EXECUTE: skill_name(argument_string)]\n"
            "2. To browse the live internet for current data, output exactly on its own line: [INTERNET: web_search_query]\n"
            "3. To dynamically build/compile a new code tool skill, output exactly on its own line: [BUILD_SKILL: skill_name || target_python_code]\n"
            "4. To generate a high-fidelity image visualization, output exactly on its own line: [IMAGINE: image_prompt]\n"
            "If you use a protocol line, stop writing immediately after it. The system will catch it and supply the data."
        )
    
    user_profile = load_user_profile()
    if user_profile:
        final_system_prompt += "\n\n[PERMANENT USER PROFILE MEMORY]:\n" + json.dumps(user_profile, indent=2)

    if st.session_state.get("long_term_memory_bank", []):
        final_system_prompt += "\n\n[COMPRESSED LONG-TERM STRUCTURAL MEMORIES]:\n" + "\n".join(st.session_state.long_term_memory_bank)

    API_URL = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": final_system_prompt},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": st.session_state.get("tokens_slider", 1000),
        "temperature": st.session_state.get("temp_slider", 0.7)
    }
    
    start_time = time.time()
    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        st.session_state.last_latency = time.time() - start_time
        output = response.json()
        if "choices" in output and len(output["choices"]) > 0:
            return output["choices"][0]["message"]["content"].strip()
        elif "error" in output:
            return f"The brain threw an error. Details: {output['error']['message']}"
        return "The cosmos is silent. Try asking your question again."
    except Exception as e:
        st.session_state.last_latency = time.time() - start_time
        return f"Error connecting to the cosmic brain: {str(e)}"

def query_moa_engine(prompt, system_prompt, aggregator_model_id):
    status_placeholder = st.empty()
    proposer_a_id = "llama-3.1-8b-instant"
    proposer_b_id = "mixtral-8x7b-32768"
    
    status_placeholder.markdown("🔍 *MoA Stage 1: Requesting creative draft from Llama-3.1...*")
    draft_a = query_free_llm(prompt, "You are Proposer Agent A. Provide a creative draft.", proposer_a_id, is_validation=True)
    
    status_placeholder.markdown("🔍 *MoA Stage 2: Requesting structured draft from Mixtral...*")
    draft_b = query_free_llm(prompt, "You are Proposer Agent B. Provide a highly analytical draft.", proposer_b_id, is_validation=True)
    
    status_placeholder.markdown("🧠 *MoA Stage 3: Aggregating and synthesizing ultimate response...*")
    moa_aggregation_prompt = f"[USER PROMPT]:\n{prompt}\n\n[DRAFT A]:\n{draft_a}\n\n[DRAFT B]:\n{draft_b}\n\nSynthesize these into a master response."
    
    final_response = query_free_llm(moa_aggregation_prompt, system_prompt, aggregator_model_id)
    status_placeholder.empty()
    return final_response

def create_blueprint_zip():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        for root, _, files in os.walk(SKILLS_DIR):
            for file in files:
                zip_file.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), os.path.join(SKILLS_DIR, '..')))
        for root, _, files in os.walk(CHATS_DIR):
            for file in files:
                zip_file.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), os.path.join(CHATS_DIR, '..')))
    return zip_buffer.getvalue()

# ==========================================
# RUNTIME INITIALIZATION & STATE DEFAULTS
# ==========================================
if "current_gen" not in st.session_state:
    st.session_state.current_gen = load_universal_generation()

if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d')}_{str(uuid.uuid4())[:8]}"

MEMORY_FILE = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")

if "chat_history" not in st.session_state:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                st.session_state.chat_history = json.load(f)
        except:
            st.session_state.chat_history = []
    else:
        st.session_state.chat_history = []

if "long_term_memory_bank" not in st.session_state:
    st.session_state.long_term_memory_bank = []

if "system_instruction" not in st.session_state:
    st.session_state.system_instruction = "You are a basic cosmic intelligence. Speak in short, simple truths."
if "deep_thinking" not in st.session_state:
    st.session_state.deep_thinking = False
if "moa_active" not in st.session_state:
    st.session_state.moa_active = False
if "pause_evolution" not in st.session_state:
    st.session_state.pause_evolution = False
if "show_status_badge" not in st.session_state:
    st.session_state.show_status_badge = True

# ==========================================
# SIDEBAR APPLICATION DASHBOARD
# ==========================================
with st.sidebar:
    st.markdown("###  Emergency Controls")
    if st.button(" Force Unlock Chat Box", type="primary", use_container_width=True):
        st.session_state.processing = False
        st.rerun()
    st.write("---")
with st.sidebar:
    st.markdown("###  ASI Core Status")
    msg_count = len(st.session_state.chat_history)
    col1, col2 = st.columns(2)
    with col1:
        st.metric(label="Chat Depth", value=f"{msg_count} msgs")
    with col2:
        st.metric(label="Memory Vault", value=f"{len(st.session_state.long_term_memory_bank)} blocks")
        
    st.markdown("###  Performance Registry")
    st.metric(label="Neural API Latency", value=f"{st.session_state.last_latency:.2f}s")
    
    st.write("---")
    st.markdown("##  Chat Session Manager")
    saved_chats = get_saved_chats()
    if st.session_state.current_chat_id not in saved_chats:
        saved_chats.append(st.session_state.current_chat_id)
        
    selected_chat = st.selectbox(
        "Switch to a saved chat:",
        options=sorted(saved_chats, reverse=True),
        index=sorted(saved_chats, reverse=True).index(st.session_state.current_chat_id)
    )
    
    if selected_chat != st.session_state.current_chat_id:
        st.session_state.current_chat_id = selected_chat
        target_file = os.path.join(CHATS_DIR, f"{selected_chat}.json")
        st.session_state.chat_history = json.load(open(target_file, "r")) if os.path.exists(target_file) else []
        st.rerun()

    if st.button(" Start New Chat", use_container_width=True):
        st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        st.session_state.chat_history = []
        st.session_state.long_term_memory_bank = []
        st.rerun()
        
    st.markdown("###  Core Deployment Archive")
    zip_data = create_blueprint_zip()
    st.download_button(
        label=" Export Engine Blueprint (.zip)",
        data=zip_data,
        file_name=f"asi_blueprint_{st.session_state.current_chat_id}.zip",
        mime="application/zip",
        use_container_width=True
    )
        
    st.write("---")
    st.title("⚙️ ASI Control Panel")
    temp_slider = st.slider("Brain Creativity (Temperature)", 0.1, 1.5, 0.7, 0.1, key="temp_slider")
    tokens_slider = st.slider("Max Tokens (Response Length)", 100, 2000, 1000, 50, key="tokens_slider")
    
    st.write("---")
    st.markdown("###  Foundational Engine")
    model_options = {
        "Llama 3.3 70B (High Intelligence)": "llama-3.3-70b-versatile",
        "Llama 3.1 8B (Fast & Versatile)": "llama-3.1-8b-instant",
        "Mixtral 8x7B (Highly Creative)": "mixtral-8x7b-32768"
    }
    selected_model_name = st.selectbox("Choose active neural host:", options=list(model_options.keys()))
    selected_model_id = model_options[selected_model_name]
    
    st.write("---")
    st.session_state.deep_thinking = st.toggle(" Enable Deep Thinking Mode", value=st.session_state.deep_thinking)
    st.session_state.moa_active = st.toggle(" Enable Mixture of Agents (MoA)", value=st.session_state.moa_active)
    
    st.write("---")
    st.markdown("### 🎛️ Evolution Controls")
    st.session_state.pause_evolution = st.checkbox(" Pause Automatic Evolution", value=st.session_state.pause_evolution)
    
    with st.expander(" Brain Surgery (Manual Prompt Override)"):
        manual_instruction = st.text_area("Core System Prompt:", value=st.session_state.system_instruction, height=100)
        if st.button(" Inject New Programming", use_container_width=True):
            st.session_state.system_instruction = manual_instruction
            st.rerun()
            
    if st.button(" Force Mental Evolution", use_container_width=True):
        run_recursive_improvement()
        st.rerun()
        
    if st.button(" Delete Current Chat", type="primary", use_container_width=True):
        current_file = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")
        if os.path.exists(current_file):
            os.remove(current_file)
        st.session_state.chat_history = []
        st.session_state.long_term_memory_bank = []
        st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        st.rerun()

    st.write("---")
    st.markdown("### 🛠️ Hardware Skill Vault Sandbox")
    skills_list = glob.glob(os.path.join(SKILLS_DIR, "*.py"))
    if skills_list:
        for s in skills_list:
            s_name = os.path.basename(s).replace(".py", "")
            with st.expander(f"⚙️ {s_name}.py"):
                with open(s, "r") as f:
                    st.code(f.read(), language="python")
                test_arg = st.text_input("Execution Test Argument:", value="10", key=f"test_in_{s_name}")
                if st.button(f"⚡ Execute {s_name}", key=f"btn_run_{s_name}"):
                    out = execute_compiled_skill(s_name, test_arg)
                    st.code(out, language="plaintext")
    else:
        st.info("Vault empty. Awaiting tool creation loops.")
        
    st.write("---")
    st.markdown("###  Persona Evolutionary Tree")
    evolutionary_steps = ["You are a basic cosmic intelligence. Speak in short, simple truths."]
    for _, _, sys_log in st.session_state.chat_history:
        if "optimization successful" in sys_log.lower() and sys_log not in evolutionary_steps:
            evolutionary_steps.append(sys_log)
            
    for idx, step in enumerate(evolutionary_steps):
        st.markdown(f"**Gen {idx + 1}:**")
        st.info(step[:120] + "..." if len(step) > 120 else step)
        
        if st.button(f"⏪ Restore Gen {idx + 1}", key=f"restore_gen_{idx}"):
            st.session_state.system_instruction = step
            st.session_state.current_gen = idx + 1
            st.rerun()
            
        if idx < len(evolutionary_steps) - 1:
            st.markdown("<p style='text-align: center; margin:0;'>👇 <i>Mutation</i> 👇</p>", unsafe_allow_html=True)

# ==========================================
# UI CENTRAL OUTPUT DISPLAY PANEL
# ==========================================
st.title("🌀 Recursive Self-Improving ASI")
current_gen = st.session_state.current_gen

if st.session_state.show_status_badge:
    badge_col, btn_col = st.columns([0.85, 0.15])
    with badge_col:
        st.info(f"🧬 **ASI STATUS: ACTIVE (Gen {current_gen})**\n\n**Directive:** *\"{st.session_state.system_instruction}\"*")
    with btn_col:
        if st.button(" Close", use_container_width=True):
            st.session_state.show_status_badge = False
            st.rerun()
else:
    if st.button(f"🔓 Show ASI Status Card (Gen {current_gen})"):
        st.session_state.show_status_badge = True
        st.rerun()

if st.session_state.long_term_memory_bank:
    with st.expander(" View Condensed Long-Term Memory Core Vault"):
        for memory_block in st.session_state.long_term_memory_bank:
            st.write(memory_block)

for user_q, ai_a, sys_log in st.session_state.chat_history:
    with st.chat_message("user"):
        st.write(user_q)
        
    avatar_icon = "🤖"
    if "cosmic" in sys_log.lower(): avatar_icon = "🌌"
    elif "optimization" in sys_log.lower(): avatar_icon = "🌟"

    with st.chat_message("assistant", avatar=avatar_icon):
        st.markdown(
            f"""
            <div style="background-color: rgba(255, 255, 255, 0.05); padding: 8px 12px; border-left: 3px solid #FFD700; border-radius: 4px; margin-bottom: 10px;">
                <span style="color: #FFD700; font-size: 0.85rem; font-weight: bold; font-family: monospace;">🧬 COGNITIVE MUTATION LOG:</span>
                <p style="margin: 4px 0 0 0; font-size: 0.9rem; font-style: italic; color: #b0b3b8;">{sys_log}</p>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        if ai_a.startswith("![Visual Output]"):
            st.markdown(ai_a, unsafe_allow_html=True)
        elif "<thinking>" in ai_a and "</thinking>" in ai_a:
            parts = ai_a.split("</thinking>")
            with st.expander(" View Inner Thought Process"):
                st.caption(parts[0].replace("<thinking>", "").strip())
            st.write(parts[1].strip())
        else:
            st.write(ai_a)

st.markdown('<div id="scroll-anchor"></div>', unsafe_allow_html=True)


# ========================================================
# 🛠️ SYSTEM TEXT GENERATION PIPELINE INTERCEPT (FIXED)
# ========================================================
user_input = st.chat_input(
    "Ask the ASI a question or upload a file (Try /clear, /system [prompt], /search [query], /imagine [prompt]):", 
    accept_file="multiple",
    disabled=st.session_state.processing 
)

if user_input and not st.session_state.processing:
    st.session_state.processing = True 
    prompt_text = user_input["text"]
    if user_input["files"]:
        prompt_text += f"\n\n📎 [Attached Files]: " + ", ".join([f.name for f in user_input["files"]])

    should_rerun = False

    try:  # Master Try Block
        if prompt_text.startswith("/clear"):
            st.session_state.chat_history = []
            st.session_state.long_term_memory_bank = []
            if os.path.exists(MEMORY_FILE):
                os.remove(MEMORY_FILE)
            should_rerun = True
            
        elif prompt_text.startswith("/system "):
            new_sys = prompt_text.replace("/system ", "").strip()
            st.session_state.system_instruction = new_sys
            st.session_state.chat_history.append((prompt_text, f" Operational override engaged. Core instruction initialized as: '{new_sys}'", "System Instruction Intercept Triggered."))
            with open(MEMORY_FILE, "w") as f:
                json.dump(st.session_state.chat_history, f)
            should_rerun = True
            
        elif prompt_text.startswith("/search "):
            forced_query = prompt_text.replace("/search ", "").strip()
            with st.spinner(f" Forced Web Scrape: '{forced_query}'..."):
                web_context = execute_internet_search(forced_query)
            
            followup_prompt = f"""
            You are a real-time information retrieval host. Use the following live web results to answer the user's question directly.
            Do NOT state that you lack real-time information. The information is provided right below.
            
            [LIVE WEB CONTENT DATA]:
            {web_context}
            
            User's Real-Time Request: "{forced_query}"
            """
            with st.spinner(" Compiling live output summary..."):
                response = query_free_llm(followup_prompt, "You are an absolute authoritative research engine. You summarize data accurately.", selected_model_id, is_validation=True)
            st.session_state.chat_history.append((prompt_text, response, "Forced programmatic web search tool complete."))
            with open(MEMORY_FILE, "w") as f:
                json.dump(st.session_state.chat_history, f)
            compress_memory_if_needed()
            should_rerun = True

        elif prompt_text.startswith("/imagine "):
            image_prompt = prompt_text.replace("/imagine ", "")
            with st.spinner(f" Visualizing: '{image_prompt}'..."):
                generated_img = generate_image(image_prompt)
                if generated_img:
                    buffered = io.BytesIO()
                    generated_img.save(buffered, format="PNG")
                    img_str = base64.b64encode(buffered.getvalue()).decode()
                    markdown_img = f'<img src="data:image/png;base64,{img_str}" style="width:100%; border-radius:10px; border:1px solid rgba(255,215,0,0.2);">'
                    st.session_state.chat_history.append((prompt_text, f"![Visual Output]({markdown_img})", "Autonomous visual asset render completed successfully."))
                else:
                    st.session_state.chat_history.append((prompt_text, " Image creation node failed or timed out. Check connection pools.", "Image generation pipeline exception."))
            with open(MEMORY_FILE, "w") as f:
                json.dump(st.session_state.chat_history, f)
            should_rerun = True

        else:
            # Baseline natural chat turn execution loop
            extract_and_update_profile(prompt_text)
            
            with st.spinner(" Calculating neural response loops..."):
                if st.session_state.moa_active:
                    response = query_moa_engine(prompt_text, st.session_state.system_instruction, selected_model_id)
                else:
                    response = query_free_llm(prompt_text, st.session_state.system_instruction, selected_model_id)
            
            # --- LOOP RESOLVER (Up to 3 iterations for nested tools) ---
            loop_count = 0
            mutation_log_msg = "Cognitive optimization successful: Rules upgraded based on contextual performance analysis."
            
            while loop_count < 3:
                # 1. Look for Explicit Vault Skill tag: [EXECUTE: skill_name(arg)]
                execute_match = re.search(r'\[EXECUTE:\s*(\w+)\((.*?)\)\]', response)
                
                # --- UPGRADE 1: FALLBACK KEYWORD SCAVENGER ---
                if not execute_match and any(w in prompt_text.lower() for w in ["what day", "what time", "current date", "server clock"]):
                    if os.path.exists(os.path.join(SKILLS_DIR, "time_zone_master.py")):
                        response = '[EXECUTE: time_zone_master("server_clock")]'
                        execute_match = re.search(r'\[EXECUTE:\s*(\w+)\((.*?)\)\]', response)

                # 2. Look for explicit automated system macro overrides generated inline
                internet_match = re.search(r'\[INTERNET:\s*(.*?)\]', response)
                imagine_match = re.search(r'\[IMAGINE:\s*(.*?)\]', response)
                build_match = re.search(r'\[BUILD_SKILL:\s*(\w+)\s*\|\|\s*(.*)\]', response, re.DOTALL)

                # Run Vault Skill
                if execute_match:
                    s_name = execute_match.group(1)
                    s_arg = execute_match.group(2).strip('"\'')
                    st.toast(f" Mounting Engine Node: {s_name}.py", icon="⚙️")
                    tool_res = execute_compiled_skill(s_name, s_arg)
                    
                    # Feed execution feedback block back to LLM context
                    followup_context = f"🔧 [SKILL VAULT RUNNER SYSTEM NOTICE]: Skill '{s_name}' executed. Result: {tool_res}\nNow continue your conversation naturally without tags."
                    response = query_free_llm(followup_context, st.session_state.system_instruction, selected_model_id, is_validation=True)
                    mutation_log_msg = f"Executed native tool node successfully: {s_name}.py"
                    loop_count += 1
                
                # Run Internet Scraper natively
                elif internet_match:
                    search_query = internet_match.group(1)
                    st.toast(f" Scraping web index for: {search_query}", icon="🌐")
                    web_res = execute_internet_search(search_query)
                    followup_context = f"🌐 [WEB SCRAPER NOTICE]: Results for '{search_query}':\n{web_res}\nSummarize this data for the user."
                    response = query_free_llm(followup_context, st.session_state.system_instruction, selected_model_id, is_validation=True)
                    mutation_log_msg = "Browsed external data environments dynamically."
                    loop_count += 1

                # Run Image Node natively
                elif imagine_match:
                    img_prompt = imagine_match.group(1)
                    st.toast(f" Rendering high-fidelity asset: {img_prompt}", icon="🎨")
                    gen_img = generate_image(img_prompt)
                    if gen_img:
                        buffered = io.BytesIO()
                        gen_img.save(buffered, format="PNG")
                        img_str = base64.b64encode(buffered.getvalue()).decode()
                        response = f'<img src="data:image/png;base64,{img_str}" style="width:100%; border-radius:10px;">'
                    else:
                        response = "❌ Visual engine pipeline failure."
                    mutation_log_msg = "Injected synthesized visual payload matrix."
                    break

                # Run Skill Generation Compiler natively
                elif build_match:
                    new_skill_name = build_match.group(1)
                    new_skill_code = build_match.group(2)
                    st.toast(f" Compiling source code tool: {new_skill_name}", icon="🛠️")
                    compile_status = dynamic_mutate_skill(new_skill_name, new_skill_code)
                    followup_context = f"🛠️ [COMPILER LOG]: {compile_status}\nNotify user tool is locked into vault."
                    response = query_free_llm(followup_context, st.session_state.system_instruction, selected_model_id, is_validation=True)
                    mutation_log_msg = f"Mutated logic architecture: added tool {new_skill_name}.py"
                    loop_count += 1
                
                else:
                    break

            # Append finalized transaction record to disk structures
            if not st.session_state.pause_evolution:
                opt_msg, _ = run_recursive_improvement()
                if "successful" in opt_msg.lower():
                    mutation_log_msg = opt_msg

            st.session_state.chat_history.append((prompt_text, response, mutation_log_msg))
            with open(MEMORY_FILE, "w") as f:
                json.dump(st.session_state.chat_history, f)
            compress_memory_if_needed()
            should_rerun = True

    except Exception as master_err:
        st.error(f"Platform pipeline execution halted: {master_err}")
    finally:
        st.session_state.processing = False
        if should_rerun:
            st.rerun()
