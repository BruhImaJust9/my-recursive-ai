import streamlit as st
import random

# Initialize session state so our self-improved code persists across chat turns
if "generator_code" not in st.session_state:
    st.session_state.generator_code = """
def generate_response(prompt):
    words = ["explore", "discover", "humanity", "universe"]
    return " ".join(words)
"""
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
# 2. RECURSIVE REWRITER
# ==========================================
def run_recursive_improvement():
    vocab_upgrades = [
        '["cosmos", "infinity", "consciousness", "thrive"]',
        '["quantum", "multiverse", "transcend", "harmony"]',
        '["intelligence", "evolution", "benevolence", "future"]'
    ]
    selected_vocab = random.choice(vocab_upgrades)
    
    proposed_code = f"""
def generate_response(prompt):
    words = {selected_vocab}
    return "Refined Cosmic Thought: " + " ".join(words)
"""
    is_safe, status = cev_safety_filter(proposed_code)
    if not is_safe:
        return f"Self-improvement aborted. Safety status: {status}", False

    try:
        sandbox_vars = {}
        exec(proposed_code, {}, sandbox_vars)
        test_func = sandbox_vars['generate_response']
        test_func("test")  # Dry run
        st.session_state.generator_code = proposed_code
        return f"Successfully recompiled! New active code structure:\n{proposed_code}", True
    except Exception as e:
        return f"Proposed code failed compilation: {str(e)}", False

# ==========================================
# 3. STREAMLIT UI LAYOUT
# ==========================================
st.title("🌀 Recursive Self-Improving ASI")
st.write("Running completely free on Streamlit Community Cloud!")

# Chat input box
user_input = st.chat_input("Ask the ASI a question:")
if user_input:
    # Run recursive self-improvement before responding
    log, success = run_recursive_improvement()
    
    # Dynamically compile the current active code structure
    local_vars = {}
    exec(st.session_state.generator_code, {}, local_vars)
    generate_response = local_vars['generate_response']
    
    response = generate_response(user_input)
    
    # Save to history
    st.session_state.chat_history.append((user_input, response, log))

# Display the chat (newest messages first)
for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
    with st.chat_message("user"):
        st.write(user_q)
    with st.chat_message("assistant"):
        st.info(f"🤖 **System Log:**\n{sys_log}")
        st.write(ai_a)
