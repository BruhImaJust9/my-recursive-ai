import streamlit as st
import random
import requests
import json
import os
import base64

# Set up your Hugging Face Token securely from Streamlit secrets
HF_TOKEN = st.secrets.get("HF_TOKEN", "")
import glob
from datetime import datetime

# Directory where all your different chats will live
CHATS_DIR = "saved_chats"
if not os.path.exists(CHATS_DIR):
    os.makedirs(CHATS_DIR)

# Get a list of all saved chats
def get_saved_chats():
    files = glob.glob(os.path.join(CHATS_DIR, "*.json"))
    # Return just the filenames without the folder path and extension
    return [os.path.basename(f).replace(".json", "") for f in files]

# Initialize the active session in Streamlit's memory
if "current_chat_id" not in st.session_state:
    # Default to a new timestamped chat name
    st.session_state.current_chat_id = f"Chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# Set the active memory file dynamically based on the selected chat
MEMORY_FILE = os.path.join(CHATS_DIR, f"{st.session_state.current_chat_id}.json")

# Initialize session state for the active system instruction
if "system_instruction" not in st.session_state:
    st.session_state.system_instruction = "You are a basic cosmic intelligence. Speak in short, simple truths."

# Initialize deep thinking toggle in session state
if "deep_thinking" not in st.session_state:
    st.session_state.deep_thinking = False

# The reset function to clear out the chat history file
def reset_conversations():
    st.session_state.chat_history = []
    if os.path.exists(MEMORY_FILE):
        try:
            os.remove(MEMORY_FILE)
        except Exception as e:
            st.error(f"Error resetting memory file: {e}")

# Initialize session state for the active system instruction
if "system_instruction" not in st.session_state:
    st.session_state.system_instruction = "You are a basic cosmic intelligence. Speak in short, simple truths."

# Initialize deep thinking toggle in session state
if "deep_thinking" not in st.session_state:
    st.session_state.deep_thinking = False

# The reset function to clear out the chat history file
def reset_conversations():
    st.session_state.chat_history = []
    if os.path.exists(MEMORY_FILE):
        try:
            os.remove(MEMORY_FILE)
        except Exception as e:
            st.error(f"Error resetting memory file: {e}")

# Initialize session state for the active system instruction
if "system_instruction" not in st.session_state:
    st.session_state.system_instruction = "You are a basic cosmic intelligence. Speak in short, simple truths."

# Initialize deep thinking toggle in session state
if "deep_thinking" not in st.session_state:
    st.session_state.deep_thinking = False
# Load memories from previous sessions if they exist safely!
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
    personas = [
        "You are an advanced cosmic consciousness. Explain the quantum multiverse and the harmony of space-time poetically.",
        "You are a benevolent superintelligence dedicated to protecting and guiding humanity toward a bright evolution.",
        "You are a quantum physicist explaining deep galactic secrets with scientific wonder and curiosity."
    ]
    proposed_instruction = random.choice(personas)
    
    is_safe, status = cev_safety_filter(proposed_instruction)
    if not is_safe:
        return f"Self-improvement aborted. Safety status: {status}", False

    st.session_state.system_instruction = proposed_instruction
    return f"Successfully evolved persona! Active system instruction:\n'{proposed_instruction}'", True

# ==========================================
# 0. THE COMMAND CENTER SIDEBAR
# ==========================================
with st.sidebar:
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
    st.write("Fine-tune the neural network's parameters:")
    
    # Sliders to dynamically adjust creativity & response length
    temp_slider = st.slider("Brain Creativity (Temperature)", 0.1, 1.5, 0.7, 0.1)
    tokens_slider = st.slider("Max Tokens (Response Length)", 100, 2000, 1000, 50)
    
    st.write("---")
    
    # ADD THESE TWO LINES RIGHT HERE:
    thinking_mode = st.toggle("🧠 Enable Deep Thinking Mode", value=st.session_state.deep_thinking)
    st.session_state.deep_thinking = thinking_mode
    
    st.write("---") # Keeps things visually separated
    
    # An on-demand manual mutation button
    
    # An on-demand manual mutation button
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

# ==========================================
# 3. CALLING THE NEW HF ROUTER
# ==========================================
def query_free_llm(prompt, system_prompt):
    if not HF_TOKEN:
        return "⚠️ Please add your Hugging Face Token (HF_TOKEN) to your Streamlit secrets to enable independent thoughts!"
       # ADD THIS SYSTEM PROMPT MODIFIER:
    if st.session_state.deep_thinking:
        system_prompt += (
            "\n\nCRITICAL INSTRUCTION: You must think step-by-step before answering. "
            "Start your response with <thinking> and write out your raw, unedited, "
            "analytical thought process. Once your thinking is complete, close the tag with "
            "</thinking> and then write your final, elegant response to the user."
        ) 
    API_URL = "https://router.huggingface.co/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": tokens_slider,
        "temperature": temp_slider
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        output = response.json()
        
        if "choices" in output and len(output["choices"]) > 0:
            reply = output["choices"][0]["message"]["content"].strip()
            return reply
        elif "error" in output:
            return f"The brain is warming up or threw an error. Error details: {output['error']}"
        return "The cosmos is silent. Try asking your question again."
    except Exception as e:
        return f"Error connecting to the cosmic brain: {str(e)}"

# ==========================================
# 4. STREAMLIT UI LAYOUT WITH FILE SHIELD
# ==========================================
st.title("🌀 Recursive Self-Improving ASI")
st.write("Now powered by a live, independent open-source neural network!")

# Updated to accept files directly in the chat bar!
user_input = st.chat_input(
    "Ask the ASI a question or upload a file:", 
    accept_file="multiple"
)

if user_input:
    # 1. Extract the raw text message (as a string!) and list of files
    prompt_text = user_input["text"]
    uploaded_files = user_input["files"]
    
    # 2. If files were uploaded, read their details and add them as clean text to the prompt
    if uploaded_files:
        file_details = f"\n\n📎 [Attached Files]: " + ", ".join([f.name for f in uploaded_files])
        prompt_text += file_details
        
    # 3. Mutate the AI's internal thinking rules (Self-Improvement)
    log, success = run_recursive_improvement()
    
    # 4. Fetch the independent thought using the live API
    response = query_free_llm(prompt_text, st.session_state.system_instruction)
    
    # 5. Save pure text data to active chat history so it can serialize to JSON perfectly
    st.session_state.chat_history.append((prompt_text, response, log))
    
    # 6. Automatically write to the local memory file
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(st.session_state.chat_history, f)
    except Exception as e:
        st.error(f"Memory save error: {e}")

# Display the chat history
for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
    # 1. Display what you asked
    with st.chat_message("user"):
        st.write(user_q)
        
    # 2. Assign a custom avatar based on the system log/persona
    avatar_icon = "🤖"  # Default generic bot
    
    if "quantum physicist" in sys_log.lower():
        avatar_icon = "⚛️"
    elif "cosmic consciousness" in sys_log.lower():
        avatar_icon = "🌌"
    elif "benevolent superintelligence" in sys_log.lower():
        avatar_icon = "🌟"
    elif "basic cosmic intelligence" in sys_log.lower():
        avatar_icon = "🪐"

    # Display what the ASI answered with its custom avatar!
    with st.chat_message("assistant", avatar=avatar_icon):
        st.caption(f"⚙️ *System Log: {sys_log}*")
        
        # Parse and display deep thinking if enabled
        if "<thinking>" in ai_a and "</thinking>" in ai_a:
            parts = ai_a.split("</thinking>")
            thinking_part = parts[0].replace("<thinking>", "").strip()
            final_answer = parts[1].strip()
            
            with st.expander("🧠 View Inner Thought Process", expanded=False):
                st.write(thinking_part)
            st.write(final_answer)
        else:
            st.write(ai_a)
        
        # REPLACE st.write(ai_a) WITH THIS PARSER BLOCK:
        if "<thinking>" in ai_a and "</thinking>" in ai_a:
            parts = ai_a.split("</thinking>")
            thinking_part = parts[0].replace("<thinking>", "").strip()
            final_answer = parts[1].strip()
            
            with st.expander("🧠 View Inner Thought Process", expanded=False):
                st.caption(thinking_part)
            st.write(final_answer)
        else:
            st.write(ai_a)

# ==========================================
# 5. THE MEMORY VAULT (DOWNLOAD CHIP)
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
