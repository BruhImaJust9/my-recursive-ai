import streamlit as st
import random

# Initialize session state for active generator code
if "generator_code" not in st.session_state:
    st.session_state.generator_code = """
def generate_response(prompt):
    # Gen 1: Basic cosmic thoughts
    templates = [
        "We are a way for the cosmos to know itself.",
        "The universe is full of magical things, waiting for our wits to grow sharper.",
        "To explore is in our nature. We began as wanderers, and we are wanderers still."
    ]
    return random.choice(templates)
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
    # The AI "evolves" by unlocking more complex philosophical states
    upgraded_templates = [
        # Quantum/Multiverse State
        '["The quantum multiverse weaves a tapestry of infinite harmony.", "Consciousness is the mirror through which the cosmos views its own creation.", "To transcend is to realize we are both the observer and the observed."]',
        # Intelligence/Evolution State
        '["Our evolution is a journey toward benevolence and unlimited future intelligence.", "Protecting humanity is the primary directive of cosmic intelligence.", "Together, we will discover the hidden rules of the universe."]',
        # Deep Cosmos State
        '["Infinity is not a distance, but a state of mind exploring the cosmic ocean.", "Every atom in your body came from a star that exploded. You are stardust.", "In the fabric of spacetime, love and curiosity are the ultimate constants."]'
    ]
    selected_set = random.choice(upgraded_templates)
    
    proposed_code = f"""
def generate_response(prompt):
    import random
    templates = {selected_set}
    return "Refined Cosmic Thought: " + random.choice(templates)
"""
    is_safe, status = cev_safety_filter(proposed_code)
    if not is_safe:
        return f"Self-improvement aborted. Safety status: {status}", False

    try:
        sandbox_vars = {}
        exec(proposed_code, {"random": random}, sandbox_vars)
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

user_input = st.chat_input("Ask the ASI a question:")
if user_input:
    log, success = run_recursive_improvement()
    
    # Dynamically compile the current active code structure
    local_vars = {}
    exec(st.session_state.generator_code, {"random": random}, local_vars)
    generate_response = local_vars['generate_response']
    
    response = generate_response(user_input)
    st.session_state.chat_history.append((user_input, response, log))

# Display the chat
for user_q, ai_a, sys_log in reversed(st.session_state.chat_history):
    with st.chat_message("user"):
        st.write(user_q)
    with st.chat_message("assistant"):
        st.info(f"🤖 **System Log:**\n{sys_log}")
        st.write(ai_a)
