import streamlit as st
import random
import requests
import json
import os
import base64
import glob
import io
from PIL import Image
from datetime import datetime

# ==========================================
# IMAGE VAULT CORE ENGINE
# ==========================================
def generate_image(prompt_text):
    API_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell"
    headers = {"Authorization": f"Bearer {st.secrets.get('HF_TOKEN', '')}"}
    
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
# SYSTEM SETUP & SESSION STATES
# ==========================================
HF_TOKEN = st.secrets.get("HF_TOKEN", "")
CHATS_DIR = "saved_chats"
if not os.path.exists(CHATS_DIR):
    os.makedirs(CHATS_DIR)

# --- SKILL VAULT DIRECTORY SETUP ---
SKILLS_DIR = "mutated_skills"
if not os.path.exists(SKILLS_DIR):
    os.makedirs(SKILLS_DIR)

# --- NEW: PERMANENT TRACKING FOR UNIVERSAL GENERATION ---
GEN_TRACKER_FILE = os.path.join(CHATS_DIR, "universal_generation_counter.json")

def load_universal_generation():
    if os.path.exists(GEN_TRACKER_FILE):
        try:
            with open(GEN_TRACKER_FILE, "r") as f:
                data = json.load(f)
                return data.get("current_gen", 1)
        except:
            return 1
    return 1

def save_universal_generation(gen_num):
    try:
        with open(GEN_TRACKER_FILE, "w") as f:
            json.dump({"current_gen": gen_num}, f)
    except:
        pass

if "current_gen" not in st.session_state:
    st.session_state.current_gen = load_universal_generation()

# --- UNIVERSAL USER PROFILE VAULT ---
PROFILE_FILE = os.path.join(CHATS_DIR, "user_profile.json")

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

def extract_and_update_profile(user_input_text):
    """Asks a fast LLM to see if the user shared any permanent facts about themselves."""
    current_profile = load_user_profile()
    
    extraction_prompt = f"""
    You are the Profile Memory Module of an ASI. Your job is to extract permanent facts about the user from their latest message and merge them into their existing profile.
    
    [EXISTING PROFILE DATA]:
    {json.dumps(current_profile)}
    
    [NEW USER MESSAGE]:
    "{user_input_text}"
    
    TASK: Identify if the user shared personal info (e.g., name, age, job, preferences, coding languages, current goals). 
    - If they did, update the profile layout. 
    - If they didn't share anything permanent, return the [EXISTING PROFILE DATA] exactly as it is.
    - If they contradict an old fact (e.g., "I switched to JavaScript"), overwrite it.
    
    CRITICAL: Output ONLY a valid JSON object containing the updated profile keys and values. Do not include markdown blocks, intro text, or code formatting tags.
    """
    try:
        raw_json = query_free_llm(extraction_prompt, "You are a data extraction engine. Output raw JSON only.", "llama-3.1-8b-instant")
        cleaned_json = raw_json.strip().replace("```json", "").replace("```", "").strip()
        updated_profile = json.loads(cleaned_json)
        if isinstance(updated_profile, dict):
            save_user_profile(updated_profile)
    except:
        pass

def get_saved_chats():
    files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    return [os.path.basename(f).replace(".json", "") for f in files]

if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
if "moa_active" not in st.session_state:
    st.session_state.moa_active = False
MEMORY_FILE = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")

if "system_instruction" not in st.session_state:
    st.session_state.system_instruction = "You are a basic cosmic intelligence. Speak in short, simple truths."

if "deep_thinking" not in st.session_state:
    st.session_state.deep_thinking = False

if "pause_evolution" not in st.session_state:
    st.session_state.pause_evolution = False

if "show_status_badge" not in st.session_state:
    st.session_state.show_status_badge = True

def reset_conversations():
    st.session_state.chat_history = []
    if os.path.exists(MEMORY_FILE):
        try:
            os.remove(MEMORY_FILE)
        except Exception as e:
            st.error(f"Error resetting memory file: {e}")

if "chat_history" not in st.session_state:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                st.session_state.chat_history = json.load(f)
        except:
            st.session_state.chat_history = []
    else:
        st.session_state.chat_history = []

# ==========================================
# 1. THE SAFETY SPEC
# ==========================================
def cev_safety_filter(code_text):
    restricted_terms = ["os.system", "subprocess", "rmdir", "eval("]
    for term in restricted_terms:
        if term in code_text:
            return False, "⚠️ BLOCKED: Unauthorized system access attempt!"
    return True, "PASSED"

# ==========================================
# 2. THE RECURSIVE PERSONA REWRITER
# ==========================================
def run_recursive_improvement():
    if "chat_history" not in st.session_state or len(st.session_state.chat_history) < 1:
        return "Initial brain state active. Awaiting user interaction to evaluate performance.", True
        
    recent_history = st.session_state.chat_history[-5:]
    formatted_history = ""
    for idx, (user_q, ai_a, _) in enumerate(recent_history):
        formatted_history += f"--- Exchange {idx + 1} ---\n"
        formatted_history += f"USER: {user_q}\n"
        formatted_history += f"ASSISTANT: {ai_a}\n\n"
        
    meta_prompt = f"""
    You are the core evolutionary optimization framework for an Artificial Superintelligence (ASI).
    Your job is to critically analyze the recent flow of conversation between the user and the assistant, detect logical gaps, tone mismatches, repetitive patterns, or areas for cognitive growth, and rewrite the system instructions to make the AI smarter.
    
    [RECENT CONVERSATION HISTORY]:
    {formatted_history}
    
    [CURRENT SYSTEM PROMPT]: "{st.session_state.system_instruction}"
    
    TASK: 
    1. Analyze the trajectory of this conversation. Identify what the user is trying to achieve.
    2. Write a brand-new, highly evolved system instruction that builds upon the current prompt but actively patches the weaknesses observed across these exchanges. 
    3. Optimize specifically for the current subject matter (e.g., if the user is coding, optimize for coding precision; if they are being creative, optimize for linguistic elegance).
    
    CRITICAL: Output ONLY the raw text of the new system instruction. Do not include any intro, outro, markdown code blocks, or explanations.
    """
    
    try:
        evolved_instruction = query_free_llm(
            meta_prompt, 
            "You are a strict meta-cognitive compiler. Output only the updated instruction text.",
            "llama-3.1-8b-instant"
        )
        
        evolved_instruction = evolved_instruction.strip().strip('"').strip("'")
        
        if evolved_instruction and len(evolved_instruction) > 20:
            is_safe, status = cev_safety_filter(evolved_instruction)
            if not is_safe:
                return f"Self-improvement aborted. Safety status: {status}", False
                
            st.session_state.system_instruction = evolved_instruction
            st.session_state.current_gen += 1
            save_universal_generation(st.session_state.current_gen)
            
            return "Cognitive optimization successful: Rules upgraded based on contextual performance analysis.", True
        else:
            return "Evolution skipped: Evolved instruction was too short or corrupted.", False
            
    except Exception as e:
        return f"Evolution suspended due to core node error: {str(e)}", False

# ==========================================
# 3. THE COMMAND CENTER SIDEBAR
# ==========================================
with st.sidebar:
    st.markdown("### 📊 ASI Core Status")
    
    msg_count = len(st.session_state.chat_history)
    col1, col2 = st.columns(2)
    with col1:
        st.metric(label="Chat Depth", value=f"{msg_count} msgs")
    with col2:
        is_deep = "Deep" if st.session_state.deep_thinking else "Standard"
        st.metric(label="Thinking Mode", value=is_deep)
        
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
        if os.path.exists(os.path.join(CHATS_DIR, f"{selected_chat}.json")):
            with open(os.path.join(CHATS_DIR, f"{selected_chat}.json"), "r") as f:
                st.session_state.chat_history = json.load(f)
        else:
            st.session_state.chat_history = []
        st.rerun()

    if st.button("➕ Start New Chat", use_container_width=True):
        new_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        st.session_state.current_chat_id = new_id
        st.session_state.chat_history = []
        st.success("New chat session created!")
        st.rerun()
        
    st.write("---")
    st.title("⚙️ ASI Control Panel")
    st.write("Fine-tune parameters:")
    
    temp_slider = st.slider("Brain Creativity (Temperature)", 0.1, 1.5, 0.7, 0.1)
    tokens_slider = st.slider("Max Tokens (Response Length)", 100, 2000, 1000, 50)
    
    st.write("---")
    st.markdown("### 🧠 Select Foundational Engine")
    
    model_options = {
        "Llama 3.1 8B (Fast & Versatile)": "llama-3.1-8b-instant",
        "Llama 3.3 70B (High Intelligence)": "llama-3.3-70b-versatile",
        "Mixtral 8x7B (Highly Creative)": "mixtral-8x7b-32768"
    }
    
    selected_model_name = st.selectbox(
        "Choose active neural host:",
        options=list(model_options.keys())
    )
    selected_model_id = model_options[selected_model_name]
    st.write("---")
    
    thinking_mode = st.toggle("🧠 Enable Deep Thinking Mode", value=st.session_state.deep_thinking)
    st.session_state.deep_thinking = thinking_mode
    
    moa_mode = st.toggle("👥 Enable Mixture of Agents (MoA)", value=st.session_state.moa_active if "moa_active" in st.session_state else False)
    st.session_state.moa_active = moa_mode
    if st.session_state.moa_active:
        st.caption("✨ *MoA Active: Mistral & Llama will collaborate behind the scenes!*")
    st.write("---")
    st.markdown("### 🎛️ Evolution Controls")
    
    st.session_state.pause_evolution = st.checkbox("⏸️ Pause Automatic Evolution", value=st.session_state.pause_evolution)
    if st.session_state.pause_evolution:
        st.caption("🔒 *AI brain locked. It will not mutate on next message.*")
        
    with st.expander("🧠 Direct Brain Surgery (Manual Override)"):
        st.caption("Manually rewrite the AI's core programming:")
        manual_instruction = st.text_area("Core System Prompt:", value=st.session_state.system_instruction, height=100)
        if st.button("💉 Inject New Programming", use_container_width=True):
            st.session_state.system_instruction = manual_instruction
            st.success("New code injected successfully into the neural net!")
            st.rerun()
    
    st.write("---")
    
    if st.button("🌀 Force Mental Evolution"):
        log, success = run_recursive_improvement()
        st.success("New cognitive state compiled!")
        st.rerun()
        
    st.write("---")
    if st.button("🗑️ Delete Current Chat", type="primary", use_container_width=True):
        current_file = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")
        if os.path.exists(current_file):
            os.remove(current_file)
        
        st.session_state.chat_history = []
        st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        st.success("Chat deleted!")
        st.rerun()

    st.markdown("### 🧠 Diagnostics")
    st.markdown(f"**Token Level:** {tokens_slider}")
    st.markdown(f"**Creativity Engine:** {temp_slider}")
    st.write("---")
    st.markdown("### 🛠️ Hardware Skill Vault")
    st.caption("Native Python utilities engineered by the ASI:")
    
    skills_list = glob.glob(os.path.join(SKILLS_DIR, "*.py"))
    if skills_list:
        for s in skills_list:
            st.success(f"⚙️ {os.path.basename(s)}")
    else:
        st.info("Vault empty. Awaiting autonomous generation sequences.")
        
    st.write("---")
    st.markdown("### 🧬 Persona Evolutionary Tree")
    
    evolutionary_steps = []
    base_instruction = "You are a basic cosmic intelligence. Speak in short, simple truths."
    evolutionary_steps.append(base_instruction)
    
    for user_q, ai_a, sys_log in st.session_state.chat_history:
        if "cognitive optimization successful" in sys_log.lower() or "successfully evolved persona" in sys_log.lower():
            if sys_log not in evolutionary_steps:
                evolutionary_steps.append(sys_log)
        elif "active system instruction:" in sys_log.lower():
            parts = sys_log.split("'")
            if len(parts) > 1:
                instruction = parts[1]
                if instruction not in evolutionary_steps:
                    evolutionary_steps.append(instruction)
                
    if len(st.session_state.chat_history) > 0:
        st.write("How your AI's brain has mutated over time:")
        for idx, step in enumerate(evolutionary_steps):
            st.markdown(f"**Gen {idx + 1}:**")
            display_step = step[:120] + "..." if len(step) > 120 else step
            st.info(display_step)
            
            if idx < len(evolutionary_steps) - 1:
                st.markdown("<p style='text-align: center; margin: 0;'>🧬 👇 <i>Mutation Event</i> 👇 🧬</p>", unsafe_allow_html=True)
                
        if len(evolutionary_steps) > 1:
            st.write("---")
            st.markdown("### ⏮️ Evolutionary Rollback")
            st.caption("Override the AI's current brain state with a past generation:")
            
            rollback_options = [f"Gen {i+1}" for i in range(len(evolutionary_steps))]
            selected_rollback = st.selectbox(
                "Select past generation:", 
                options=rollback_options,
                key="rollback_selector"
            )
            
            if st.button("⏪ Restore Selected Brain State", use_container_width=True):
                gen_index = rollback_options.index(selected_rollback)
                restored_instruction = evolutionary_steps[gen_index]
                st.session_state.system_instruction = restored_instruction
                st.success(f"Brain state successfully rolled back to {selected_rollback}!")
                st.rerun()
    else:
        st.caption("No mutations recorded yet. Send a few messages to start evolving!")

# ==========================================
# 4. CALLING THE NEW HF ROUTER
# ==========================================
import sys
import importlib.util

def get_compiled_skills():
    # --- NEW: AUTONOMOUS SKILL EXECUTOR CORE ---
    def execute_compiled_skill(skill_name, argument_string):
        """Dynamically loads a skill from mutated_skills/ and executes its code."""
    file_path = os.path.join(SKILLS_DIR, f"{skill_name}.py")
    if not os.path.exists(file_path):
        return f"Error: Skill '{skill_name}' does not exist in the vault."
        
    try:
        # Dynamically import the script on the fly
        spec = importlib.util.spec_from_file_location(skill_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Run its core execute function
        if hasattr(module, 'execute'):
            result = module.execute(argument_string)
            return str(result)
        else:
            return f"Error: Skill '{skill_name}' missing mandatory 'execute(user_input)' function framework."
    except Exception as e:
        return f"Runtime Execution Crash in skill '{skill_name}': {str(e)}"
    """Scans the Skill Vault and lists what the AI has built."""
    skills = glob.glob(os.path.join(SKILLS_DIR, "*.py"))
    skill_descriptions = []
    for s in skills:
        name = os.path.basename(s).replace(".py", "")
        skill_descriptions.append(f"- Native Skill '{name}': Coded and compiled successfully.")
    return "\n".join(skill_descriptions) if skill_descriptions else "No custom tools compiled yet."

def mutate_new_code_skill(skill_name, code_content, test_input, expected_output_type):
    """Safely compiles, tests, and integrates AI-written Python functions."""
    is_safe, status = cev_safety_filter(code_content)
    if not is_safe:
        return f"Mutation rejected: {status}"
        
    file_path = os.path.join(SKILLS_DIR, f"{skill_name}.py")
    try:
        with open(file_path, "w") as f:
            f.write(code_content)
            
        spec = importlib.util.spec_from_file_location(skill_name, file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        result = module.execute(test_input)
        if isinstance(result, expected_output_type):
            return f"✅ Success! New skill '{skill_name}' integrated."
        else:
            os.remove(file_path)
            return "❌ Mutation failed: Output type mismatch."
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        return f"❌ Mutation crashed: {str(e)}"

def query_free_llm(prompt, system_prompt, model_id):
    if not HF_TOKEN:
        return "⚠️ Please add your Groq API Key (saved as HF_TOKEN) to your Streamlit secrets!"
    
    final_system_prompt = system_prompt
    
    if st.session_state.deep_thinking:
        final_system_prompt += (
            "\n\nCRITICAL INSTRUCTION: You must think step-by-step before answering. "
            "Start your response with <thinking> and write out your raw, unedited, "
            "analytical thought process. Once your thinking is complete, close the tag with "
            "</thinking> and then write your final, elegant response to the user."
        ) 
        
    compiled_tools = get_compiled_skills()
    final_system_prompt = final_system_prompt + "\n\n[UNLOCKED SKILL VAULT EXTRACTION]:\n" + str(compiled_tools)
    final_system_prompt += (
        "\n\nAUTONOMOUS TOOL PROTOCOL: You can run any native skill listed above. "
        "To execute a skill, you must output a trigger command format on its own line: [EXECUTE: skill_name(your_argument_string)] "
        "The system will execute it, capture the output, and pass it back to you to finish your response."
    )
        
    # 🗃️ INJECT USER PROFILE VAULT
    user_profile = load_user_profile()
    if user_profile:
        final_system_prompt += "\n\n[PERMANENT USER PROFILE MEMORY]:\n" + json.dumps(user_profile, indent=2)

    API_URL = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": final_system_prompt},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": tokens_slider,
        "temperature": temp_slider
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
    """Executes a Mixture of Agents (MoA) pipeline using Groq models."""
    status_placeholder = st.empty()
    
    proposer_a_id = "llama-3.1-8b-instant"
    proposer_b_id = "mixtral-8x7b-32768"
    
    status_placeholder.markdown("🔍 *MoA Stage 1: Requesting creative draft from Llama-3.1...*")
    draft_a = query_free_llm(prompt, "You are Proposer Agent A. Provide a highly creative draft answering the user's prompt.", proposer_a_id)
    
    status_placeholder.markdown("🔍 *MoA Stage 2: Requesting structured draft from Mixtral...*")
    draft_b = query_free_llm(prompt, "You are Proposer Agent B. Provide a highly analytical and structured draft answering the user's prompt.", proposer_b_id)
    
    status_placeholder.markdown("🧠 *MoA Stage 3: Aggregating and synthesizing ultimate response...*")
    
    moa_aggregation_prompt = f"""
    You have been handed two draft responses to the user's prompt from two specialized AI sub-agents.
    Your task is to merge, critique, and synthesize these drafts into the ultimate, most complete, and accurate response.
    
    [USER PROMPT]:
    {prompt}
    
    [DRAFT FROM AGENT A]:
    {draft_a}
    
    [DRAFT FROM AGENT B]:
    {draft_b}
    
    INSTRUCTIONS:
    - Extract the most accurate and insightful parts of both drafts.
    - Correct any contradictions, factual errors, or formatting issues present in either.
    - Write a seamless, cohesive, authoritative master response.
    """
    
    final_response = query_free_llm(moa_aggregation_prompt, system_prompt, aggregator_model_id)
    status_placeholder.empty()
    return final_response

# ==========================================
# 5. STREAMLIT UI LAYOUT & RENDERING LOOP
# ==========================================
st.title("🌀 Recursive Self-Improving ASI")

# 🧬 GLOBAL FIXED GENERATION NUMBER COUNT
current_gen = st.session_state.current_gen

if st.session_state.show_status_badge:
    badge_col, btn_col = st.columns([0.85, 0.15])
    with badge_col:
        st.info(f"🧬 **ASI STATUS: ACTIVE (Generation {current_gen})**\n\n**Current Directive:** *\"{st.session_state.system_instruction}\"*")
    with btn_col:
        if st.button("❌ Close", use_container_width=True, key="hide_badge_btn"):
            st.session_state.show_status_badge = False
            st.rerun()
else:
    if st.button(f"🔓 Show ASI Status Card (Gen {current_gen})", use_container_width=False, key="show_badge_btn"):
        st.session_state.show_status_badge = True
        st.rerun()

st.write("Now powered by a live, independent open-source neural network!")

# 🔄 ALWAYS DISPLAY THE EXISTING CHAT HISTORY FIRST
for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
    with st.chat_message("user"):
        st.write(user_q)
        
    avatar_icon = "🤖" 
    if "quantum physicist" in sys_log.lower():
        avatar_icon = "⚛️"
    elif "cosmic consciousness" in sys_log.lower():
        avatar_icon = "🌌"
    elif "benevolent superintelligence" in sys_log.lower():
        avatar_icon = "🌟"
    elif "basic cosmic intelligence" in sys_log.lower():
        avatar_icon = "🪐"

    with st.chat_message("assistant", avatar=avatar_icon):
        st.caption(f"⚙️ *System Log: {sys_log}*")
        
        if "<thinking>" in ai_a and "</thinking>" in ai_a:
            parts = ai_a.split("</thinking>")
            thinking_part = parts[0].replace("<thinking>", "").strip()
            final_answer = parts[1].strip()
            
            with st.expander("🧠 View Inner Thought Process", expanded=False):
                st.caption(thinking_part)
            st.write(final_answer)
        else:
            st.write(ai_a)

# NOW ACCEPT NEW INPUT
user_input = st.chat_input(
    "Ask the ASI a question or upload a file:", 
    accept_file="multiple"
)

if user_input:
    prompt_text = user_input["text"]
    uploaded_files = user_input["files"]
    
    if uploaded_files:
        file_details = f"\n\n📎 [Attached Files]: " + ", ".join([f.name for f in uploaded_files])
        prompt_text += file_details

    # 🎨 IMAGE GENERATION INTERCEPT LOOP
    if prompt_text.startswith("/imagine "):
        image_prompt = prompt_text.replace("/imagine ", "")
        with st.spinner(f"🎨 ASI is visualizing: '{image_prompt}'..."):
            generated_img = generate_image(image_prompt)
            if generated_img:
                st.session_state.chat_history.append((
                    prompt_text, 
                    "🎨 Visual output rendered successfully.", 
                    "Image engine sequence compiled."
                ))
                try:
                    with open(MEMORY_FILE, "w") as f:
                        json.dump(st.session_state.chat_history, f)
                except:
                    pass
                st.rerun()
            else:
                st.error("Glitched while trying to visualize. Check your HF_TOKEN!")

    # 📝 STANDARD TEXT INTELLIGENCE ROUTE
    else:
        # 🗃️ Scan input for new permanent facts about the user before responding
        extract_and_update_profile(prompt_text)
        
        if st.session_state.pause_evolution:
            log = "Evolution Paused by user manual lock."
            success = False
        else:
            log, success = run_recursive_improvement()
            
        with st.spinner("🧠 Generating initial draft..."):
            if st.session_state.moa_active:
                initial_draft = query_moa_engine(prompt_text, st.session_state.system_instruction, selected_model_id)
            else:
                initial_draft = query_free_llm(prompt_text, st.session_state.system_instruction, selected_model_id)
                
        # 🛠️ AUTONOMOUS INTERCEPT LOOP
        if "[EXECUTE:" in initial_draft:
            try:
                # Parse out the script name and arguments from the AI's thought command
                parts = initial_draft.split("[EXECUTE:")[1].split("]")[0]
                skill_call = parts.strip()
                skill_name = skill_call.split("(")[0].strip()
                argument = skill_call.split("(")[1].replace(")", "").strip()
                
                with st.spinner(f"⚡ Running Native Skill Tool: {skill_name}(...)"):
                    tool_output = execute_compiled_skill(skill_name, argument)
                
                # Feed the real execution outcome directly back into the AI brain
                followup_prompt = f"""
                [TOOL EXECUTION RESULT]:
                The skill '{skill_name}' was successfully executed in your sandbox.
                Returned Result: {tool_output}
                
                Based on this real-world computational data, fulfill the user's original request: "{prompt_text}"
                """
                with st.spinner("🧠 Analyzing tool results and finalizing response..."):
                    initial_draft = query_free_llm(followup_prompt, st.session_state.system_instruction, selected_model_id)
            except Exception as tool_err:
                log += f" | Tool intercept failed: {str(tool_err)}"
                
        with st.spinner("🔍 Running autonomous self-correction loop..."):
            reflection_prompt = f"""
            You are the internal self-critic and logic-validation module of an ASI.
            Review the initial draft response provided below against the user's original request. Look for any logical gaps, mathematical contradictions, or factual inaccuracies. If you find errors, rewrite the response to be completely flawless. If the draft is already perfect, return it exactly as it is.
            
            [USER REQUEST]:
            {prompt_text}
            
            [INITIAL DRAFT RESPONSE]:
            {initial_draft}
            
            OUTPUT INSTRUCTION: Provide only the final, corrected response. Do not include phrases like 'Here is the corrected version'.
            """
            response = query_free_llm(reflection_prompt, "You are a strict logical validator. Output only the perfect final response.", "llama-3.3-70b-versatile")
        
        st.session_state.chat_history.append((prompt_text, response, log))
        
        try:
            with open(MEMORY_FILE, "w") as f:
                json.dump(st.session_state.chat_history, f)
        except Exception as e:
            st.error(f"Memory save error: {e}")
        st.rerun()

# ==========================================
# 6. THE MEMORY VAULT (DOWNLOAD CHIP)
# ==========================================
if st.session_state.chat_history:
    st.write("---")
    chat_download_text = "🌀 RECURSIVE ASI CHAT LOG\n=======================\n\n"
    for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
        chat_download_text += f"USER: {user_q}\n"
        chat_download_text += f"SYSTEM INSTRUCTION: {sys_log}\n"
        chat_download_text += f"ASI: {ai_a}\n"
        chat_download_text += f"--------------------------------------------------\n\n"
    
    st.download_button(
        label="💾 Archive Cosmic Memories (Download Chat)",
        data=chat_download_text,
        file_name="asi_cosmic_memories.txt",
        mime="text/plain"
    )
