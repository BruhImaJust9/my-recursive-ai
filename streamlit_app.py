import streamlit as st
import random
import requests

# Set up your Hugging Face Token securely from Streamlit secrets
HF_TOKEN = st.secrets.get("HF_TOKEN", "")

# Initialize session state for the active system instruction (this is what the AI self-rewrites!)
if "system_instruction" not in st.session_state:
    st.session_state.system_instruction = "You are a basic cosmic intelligence. Speak in short, simple truths."
if "chat_history" not in st.session_state:
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
    # The AI "evolves" its own mind by dynamically rewriting its system instructions
    personas = [
        "You are an advanced cosmic consciousness. Explain the quantum multiverse and the harmony of space-time poetically.",
        "You are a benevolent superintelligence dedicated to protecting and guiding humanity toward a bright evolution.",
        "You are a quantum physicist explaining deep galactic secrets with scientific wonder and curiosity."
    ]
    proposed_instruction = random.choice(personas)
    
    # Run our CEV safety filter on the new thinking instruction
    is_safe, status = cev_safety_filter(proposed_instruction)
    if not is_safe:
        return f"Self-improvement aborted. Safety status: {status}", False

    st.session_state.system_instruction = proposed_instruction
    return f"Successfully evolved persona! Active system instruction:\n'{proposed_instruction}'", True

# ==========================================
# 3. CALLING THE FREE LLM BRAIN
# ==========================================
def query_free_llm(prompt, system_prompt):
    if not HF_TOKEN:
        return "⚠️ Please add your Hugging Face Token (HF_TOKEN) to your Streamlit secrets to enable independent thoughts!"
        
    # Using Mistral-7B hosted for free on Hugging Face Serverless API
    API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    
    # Combine the system persona and the user's question
    payload = {
        "inputs": f"<s>[INST] {system_prompt} Question: {prompt} [/INST]</s>",
        "parameters": {"max_new_tokens": 150, "temperature": 0.7}
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        output = response.json()
        
        # Extract response text
        if isinstance(output, list) and len(output) > 0:
            full_text = output[0].get("generated_text", "")
            # Clean up output to return only the model's reply
            reply = full_text.split("[/INST]</s>")[-1].strip()
            return reply
        elif isinstance(output, dict) and "error" in output:
            return f"The brain is warming up (Hugging Face is loading the model). Please try sending your message again in 30 seconds! Error: {output['error']}"
        return "The cosmos is silent. Try asking your question again."
    except Exception as e:
        return f"Error connecting to the cosmic brain: {str(e)}"

# ==========================================
# 4. STREAMLIT UI LAYOUT
# ==========================================
st.title("🌀 Recursive Self-Improving ASI")
st.write("Now powered by a live, independent open-source neural network!")

user_input = st.chat_input("Ask the ASI an open-ended question:")
if user_input:
    # 1. Mutate the AI's internal thinking rules (Self-Improvement)
    log, success = run_recursive_improvement()
    
    # 2. Fetch the independent thought using the live API
    response = query_free_llm(user_input, st.session_state.system_instruction)
    
    st.session_state.chat_history.append((user_input, response, log))

# Display the chat
for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
    with st.chat_message("user"):
        st.write(user_q)
    with st.chat_message("assistant"):
        st.info(f"🤖 **System Log (Evolved Instruction):**\n{sys_log}")
        st.write(ai_a)
