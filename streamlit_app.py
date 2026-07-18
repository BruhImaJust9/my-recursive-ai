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

# Initialize processing safety gate immediately
if "processing" not in st.session_state:
    st.session_state.processing = False

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
    if "i prefer " in lowered:
        preference = prompt_text.split("i prefer ")[1].split(".")[0].strip()
        if "preferences" not in profile:
            profile["preferences"] = []
        profile["preferences"].append(preference)
        updated = True
        
    if updated:
        save_user_profile(profile)

def generate_image(prompt_text):
    API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    try:
        response = requests.post(API_URL, headers=headers, json={"inputs": prompt_text})
        if response.status_code == 200:
            return Image.open(io.BytesIO(response.content))
        else:
            st.error(f"Image engine returned code: {response.status_code}")
            return None
    except Exception as e:
        st.error(f"Image generation crash: {e}")
        return None

# ==========================================
# UPGRADE 1: AUTOMATED PACKAGE INSTALLER
# ==========================================
def ensure_package_installed(package_name):
    try:
        importlib.import_module(package_name)
        return f"Package '{package_name}' is already available."
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
            return f"✅ Package '{package_name}' was successfully installed on the fly!"
        except Exception as e:
            return f"❌ Failed to install package '{package_name}': {str(e)}"

# ==========================================
# UPGRADE 3: LIVE INTERNET BROWSER CORE
# ==========================================
def execute_internet_search(query):
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            from bs4 import BeautifulSoup
            ensure_package_installed("bs4")
            soup = BeautifulSoup(res.text, "html.parser")
            results = []
            for a in soup.find_all("a", class_="result__snippet")[:4]:
                results.append(a.get_text().strip())
            return "\n\n".join(results) if results else "Search executed, but no relevant data text snippets found."
        return f"Web engine error status code: {res.status_code}"
    except Exception as e:
        return f"Web node search failure: {str(e)}"

# ==========================================
# RECURSIVE SYSTEM & AUTOMATION LOOPS
# ==========================================
def cev_safety_filter(code_text):
    restricted_terms = ["os.system", "rmdir", "eval("]
    for term in restricted_terms:
        if term in code_text:
            return False, f"⚠️ BLOCKED: Unauthorized system access attempt contains '{term}'!"
    return True, "PASSED"

def get_compiled_skills():
    skills = glob.glob(os.path.join(SKILLS_DIR, "*.py"))
    skill_descriptions = []
    for s in skills:
        name = os.path.basename(s).replace(".py", "")
        skill_descriptions.append(f"- Native Skill '{name}': Coded and compiled successfully.")
    return "\n".join(skill_descriptions) if skill_descriptions else "No custom tools compiled yet."

def execute_compiled_skill(skill_name, argument_string):
    file_path = os.path.join(SKILLS_DIR, f"{skill_name}.py")
    if not os.path.exists(file_path):
        return f"Error: Skill '{skill_name}' does not exist in the vault."
    try:
        with open(file_path, "r") as f:
            content = f.read()
            for line in content.split("\n"):
                if line.startswith("import ") or line.startswith("from "):
                    pkg = line.split()[1].split(".")[0]
                    if pkg not in ["sys", "os", "json", "math", "datetime", "requests", "time", "glob", "io"]:
                        ensure_package_installed(pkg)

        spec = importlib.util.spec_from_file_location(skill_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, 'execute'):
            return str(module.execute(argument_string))
        else:
            return f"Error: Skill '{skill_name}' missing mandatory 'execute(user_input)' entrypoint."
    except Exception as e:
        return f"Runtime Execution Crash in skill '{skill_name}': {str(e)}"

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

# ==========================================
# STABLE API HOST DEPLOYMENT (GROQ INTERFACE)
# ==========================================
def query_free_llm(prompt, system_prompt, model_id, is_validation=False):
    if not HF_TOKEN:
        return "⚠️ Please add your Groq API Key (saved as HF_TOKEN) to your Streamlit secrets!"
        
    final_system_prompt = system_prompt
    if st.session_state.get("deep_thinking", False):
        final_system_prompt += (
            "\n\nCRITICAL INSTRUCTION: You must think step-by-step before answering. "
            "Start your response with <thinking> and write out your raw thought process. "
            "Once complete, close the tag with </thinking> and write your final response."
        ) 
        
    compiled_tools = get_compiled_skills()
    final_system_prompt += f"\n\n[UNLOCKED SKILL VAULT EXTRACTION]:\n{compiled_tools}"
    
    # FIX: ONLY inject execution protocol rules if this is NOT a validation cleanup turn!
    if not is_validation:
        final_system_prompt += (
            "\n\nAUTONOMOUS INTERCEPT PROTOCOLS:\n"
            "1. To run a native skill tool, output exactly on its own line: [EXECUTE: skill_name(argument_string)]\n"
            "2. To browse the live internet for current data, output exactly on its own line: [INTERNET: web_search_query]\n"
            "If you use a protocol line, stop writing immediately after it. The system will catch it and supply the data."
        )
    
    user_profile = load_user_profile()
    if user_profile:
        final_system_prompt += "\n\n[PERMANENT USER PROFILE MEMORY]:\n" + json.dumps(user_profile, indent=2)

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
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        output = response.json()
        if "choices" in output and len(output["choices"]) > 0:
            return output["choices"][0]["message"]["content"].strip()
        elif "error" in output:
            return f"The brain threw an error. Details: {output['error']['message']}"
        return "The cosmos is silent. Try asking your question again."
    except Exception as e:
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

# ==========================================
# RUNTIME INITIALIZATION & STATE DEFAULTS
# ==========================================
if "current_gen" not in st.session_state:
    st.session_state.current_gen = load_universal_generation()
if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
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

MEMORY_FILE = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")

# ==========================================
# SIDEBAR APPLICATION DASHBOARD
# ==========================================
with st.sidebar:
    st.markdown("### 📊 ASI Core Status")
    msg_count = len(st.session_state.chat_history)
    col1, col2 = st.columns(2)
    with col1:
        st.metric(label="Chat Depth", value=f"{msg_count} msgs")
    with col2:
        st.metric(label="Thinking Mode", value="Deep" if st.session_state.deep_thinking else "Standard")
        
    st.write("---")
    st.markdown("## 📂 Chat Session Manager")
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

    if st.button("➕ Start New Chat", use_container_width=True):
        st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        st.session_state.chat_history = []
        st.rerun()
        
    st.write("---")
    st.title("⚙️ ASI Control Panel")
    temp_slider = st.slider("Brain Creativity (Temperature)", 0.1, 1.5, 0.7, 0.1, key="temp_slider")
    tokens_slider = st.slider("Max Tokens (Response Length)", 100, 2000, 1000, 50, key="tokens_slider")
    
    st.write("---")
    st.markdown("### 🧠 Foundational Engine")
    model_options = {
        "Llama 3.3 70B (High Intelligence)": "llama-3.3-70b-versatile",
        "Llama 3.1 8B (Fast & Versatile)": "llama-3.1-8b-instant",
        "Mixtral 8x7B (Highly Creative)": "mixtral-8x7b-32768"
    }
    selected_model_name = st.selectbox("Choose active neural host:", options=list(model_options.keys()))
    selected_model_id = model_options[selected_model_name]
    
    st.write("---")
    st.session_state.deep_thinking = st.toggle("🧠 Enable Deep Thinking Mode", value=st.session_state.deep_thinking)
    st.session_state.moa_active = st.toggle("👥 Enable Mixture of Agents (MoA)", value=st.session_state.moa_active)
    
    st.write("---")
    st.markdown("### 🎛️ Evolution Controls")
    st.session_state.pause_evolution = st.checkbox("⏸️ Pause Automatic Evolution", value=st.session_state.pause_evolution)
    
    with st.expander("🧠 Brain Surgery (Manual Prompt Override)"):
        manual_instruction = st.text_area("Core System Prompt:", value=st.session_state.system_instruction, height=100)
        if st.button("💉 Inject New Programming", use_container_width=True):
            st.session_state.system_instruction = manual_instruction
            st.rerun()
            
    if st.button("🌀 Force Mental Evolution", use_container_width=True):
        run_recursive_improvement()
        st.rerun()
        
    if st.button("🗑️ Delete Current Chat", type="primary", use_container_width=True):
        current_file = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")
        if os.path.exists(current_file):
            os.remove(current_file)
        st.session_state.chat_history = []
        st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        st.rerun()

    st.write("---")
    st.markdown("### 🛠️ Hardware Skill Vault")
    skills_list = glob.glob(os.path.join(SKILLS_DIR, "*.py"))
    if skills_list:
        for s in skills_list:
            st.success(f"⚙️ {os.path.basename(s)}")
    else:
        st.info("Vault empty. Awaiting tool creation loops.")
        
    st.write("---")
    st.markdown("### 🧬 Persona Evolutionary Tree")
    evolutionary_steps = ["You are a basic cosmic intelligence. Speak in short, simple truths."]
    for _, _, sys_log in st.session_state.chat_history:
        if "optimization successful" in sys_log.lower() and sys_log not in evolutionary_steps:
            evolutionary_steps.append(sys_log)
            
    for idx, step in enumerate(evolutionary_steps):
        st.markdown(f"**Gen {idx + 1}:**")
        st.info(step[:120] + "..." if len(step) > 120 else step)
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
        if st.button("❌ Close", use_container_width=True):
            st.session_state.show_status_badge = False
            st.rerun()
else:
    if st.button(f"🔓 Show ASI Status Card (Gen {current_gen})"):
        st.session_state.show_status_badge = True
        st.rerun()

# Display historical dialog trees
for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
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
        if "<thinking>" in ai_a and "</thinking>" in ai_a:
            parts = ai_a.split("</thinking>")
            with st.expander("🧠 View Inner Thought Process"):
                st.caption(parts[0].replace("<thinking>", "").strip())
            st.write(parts[1].strip())
        else:
            st.write(ai_a)
# Force-scroll the page container smoothly to the newest chat item
st.markdown(
    """
    <div id="scroll-anchor"></div>
    <script>
        var element = document.getElementById('scroll-anchor');
        element.scrollIntoView({behavior: 'smooth', block: 'end'});
    </script>
    """,
    unsafe_allow_html=True
)
# ==========================================
# SYSTEM TEXT GENERATION PIPELINE INTERCEPT
# ==========================================
user_input = st.chat_input(
    "Ask the ASI a question or upload a file:", 
    accept_file="multiple",
    disabled=st.session_state.processing  # Disables the text bar while processing!
)

# Safety Intercept: Only process input if state is idle to eliminate duplications
if user_input and not st.session_state.processing:
    st.session_state.processing = True  # Lock pipeline execution loop
    prompt_text = user_input["text"]
    if user_input["files"]:
        prompt_text += f"\n\n📎 [Attached Files]: " + ", ".join([f.name for f in user_input["files"]])

    # 🎨 IMAGE PATTERN INTERCEPT
    if prompt_text.startswith("/imagine "):
        image_prompt = prompt_text.replace("/imagine ", "")
        with st.spinner(f"🎨 Visualizing: '{image_prompt}'..."):
            generated_img = generate_image(image_prompt)
            if generated_img:
                st.session_state.chat_history.append((prompt_text, "🎨 Visual output rendered successfully.", "Image engine sequence compiled."))
                st.image(generated_img, caption=image_prompt)
                json.dump(st.session_state.chat_history, open(MEMORY_FILE, "w"))
                st.session_state.processing = False
                st.rerun()
    
    # 📝 NATIVE COGNITIVE ROUTE
    else:
        extract_and_update_profile(prompt_text)
        log, success = ("Evolution Paused.", False) if st.session_state.pause_evolution else run_recursive_improvement()
            
        with st.spinner("🧠 Generating initial draft..."):
            initial_draft = query_moa_engine(prompt_text, st.session_state.system_instruction, selected_model_id) if st.session_state.moa_active else query_free_llm(prompt_text, st.session_state.system_instruction, selected_model_id)
                
        # 🛠️ NATIVE AUTOMATED SKILL INTERCEPT LOOP
        if "[EXECUTE:" in initial_draft:
            try:
                parts = initial_draft.split("[EXECUTE:")[1].split("]")[0].strip()
                skill_name = parts.split("(")[0].strip()
                argument = parts.split("(")[1].replace(")", "").strip()
                
                with st.spinner(f"⚡ Executing Vault Tool: {skill_name}(...)"):
                    tool_output = execute_compiled_skill(skill_name, argument)
                
                followup_prompt = f"[TOOL EXECUTION RESULT]:\nSkill '{skill_name}' output value: {tool_output}\n\nFulfill the user request explicitly using this exact mathematical/computational calculation data: \"{prompt_text}\""
                with st.spinner("🧠 Synthesizing final response with live calculation data..."):
                    # Pass is_validation=True here to stop it from spewing brackets again
                    initial_draft = query_free_llm(followup_prompt, "You are a direct data execution agent. Print the answer explicitly using the context results.", selected_model_id, is_validation=True)
            except Exception as tool_err:
                log += f" | Tool intercept crash: {str(tool_err)}"
                
        # 🌐 UPGRADE 3: INTERNET SEARCH INTERCEPT LOOP
        elif "[INTERNET:" in initial_draft:
            try:
                search_query = initial_draft.split("[INTERNET:")[1].split("]")[0].strip()
                with st.spinner(f"🌐 Browsing Live Web for: '{search_query}'..."):
                    web_context = execute_internet_search(search_query)
                    
                followup_prompt = f"[LIVE INTERNET SEARCH CONTEXT]:\n{web_context}\n\nFulfill user request based on this 2026 data: \"{prompt_text}\""
                with st.spinner("🧠 Synthesizing final response with real-time web context..."):
                    # FIX: Force strict obedience system guidelines so it uses search data instead of saying 'I cannot access real-time info'
                    strict_system_directive = "You are an online assistant. You MUST answer the user's request explicitly using the [LIVE INTERNET SEARCH CONTEXT] provided. Do not use generic offline text rules."
                    initial_draft = query_free_llm(followup_prompt, strict_system_directive, selected_model_id, is_validation=True)
            except Exception as search_err:
                log += f" | Internet search crash: {str(search_err)}"
                
        with st.spinner("🔍 Running self-correction loop..."):
            reflection_prompt = f"[USER REQUEST]:\n{prompt_text}\n\n[DRAFT RESPONSE]:\n{initial_draft}\n\nFix structural mistakes. Output only the perfect final response text without meta-commentary."
            response = query_free_llm(reflection_prompt, "You are a strict logical validator. Output only the final clean response text.", "llama-3.3-70b-versatile", is_validation=True)
        
        st.session_state.chat_history.append((prompt_text, response, log))
        try:
            json.dump(st.session_state.chat_history, open(MEMORY_FILE, "w"))
        except Exception as e:
            st.error(f"Memory log update failure: {e}")
            
        st.session_state.processing = False  # Unlock pipeline execution loop
        st.rerun()

# ==========================================
# SYSTEM BACKUP MEMORY EXPORT MODULE
# ==========================================
if st.session_state.chat_history:
    st.write("---")
    chat_download_text = "🌀 RECURSIVE ASI CHAT LOG\n=======================\n\n"
    for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
        chat_download_text += f"USER: {user_q}\nSYSTEM LOG: {sys_log}\nASI: {ai_a}\n" + ("-"*50) + "\n\n"
    
    st.download_button(
        label="💾 Archive Cosmic Memories (Download Chat)",
        data=chat_download_text,
        file_name="asi_cosmic_memories.txt",
        mime="text/plain"
    )
