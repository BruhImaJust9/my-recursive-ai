import streamlit as st
import urllib.parse
from PIL import Image
import google.generativeai as genai
from duckduckgo_search import DDGS

# ==========================================
# 1. PAGE SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")
st.title("🤖 Intelligent AI Workspace")
st.caption("Free Web Search, Image Vision, & AI Image Generation")

# Gemini Key Setup (Stored in Secrets or User Input)
GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hey there! Ask me anything, upload an image for me to analyze, try `/search <topic>`, or type `/generate <prompt>` to make an image!"}
    ]

# ==========================================
# 2. FREE HELPER FUNCTIONS
# ==========================================

def execute_free_search(query: str) -> str:
    """Free web search — No API Key required!"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "No matching search results found."
            
        sources = [f"**{r['title']}**\nSnippet: {r['body']}\nURL: {r['href']}" for r in results]
        return "\n\n".join(sources)
    except Exception as e:
        return f"Search error: {str(e)}"

def get_generated_image_url(prompt: str) -> str:
    """Free image generation via Pollinations URL — No API Key required!"""
    encoded_prompt = urllib.parse.quote(prompt.strip())
    return f"https://pollinations.ai/p/{encoded_prompt}?width=800&height=800&seed=42"

# ==========================================
# 3. SIDEBAR & IMAGE ATTACHMENT
# ==========================================
with st.sidebar:
    st.header("⚙️ Workspace Controls")
    
    # Image upload for Vision analysis
    uploaded_file = st.file_uploader("Upload Image to Analyze", type=["png", "jpg", "jpeg", "webp"])
    image_to_analyze = Image.open(uploaded_file) if uploaded_file else None
    
    if image_to_analyze:
        st.image(image_to_analyze, caption="Attached Image", use_column_width=True)
        st.success("Image attached! Ask a question in chat about it.")

    st.markdown("---")
    if st.button("🗑️ Clear Chat"):
        st.session_state.messages = []
        st.rerun()

# ==========================================
# 4. CHAT HISTORY DISPLAY
# ==========================================
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "image_url" in msg:
            st.image(msg["image_url"], use_column_width=True)
        elif "uploaded_img" in msg:
            st.image(msg["uploaded_img"], use_column_width=True)

# ==========================================
# 5. INPUT LOGIC & ROUTING
# ==========================================
user_input = st.chat_input("Type a question, /search <query>, or /generate <prompt>...")

if user_input:
    # Append User Input
    user_data = {"role": "user", "content": user_input}
    if image_to_analyze:
        user_data["uploaded_img"] = image_to_analyze
    st.session_state.messages.append(user_data)
    
    with st.chat_message("user"):
        st.markdown(user_input)
        if image_to_analyze:
            st.image(image_to_analyze, use_column_width=True)

    # Generate Assistant Response
    with st.chat_message("assistant"):
        placeholder = st.empty()
        
        # 🎨 FEATURE 1: Image Generation Command
        if user_input.lower().startswith("/generate") or "generate an image" in user_input.lower():
            prompt = user_input.replace("/generate", "").strip()
            placeholder.markdown(f"🎨 *Generating image for:* **'{prompt}'**...")
            img_url = get_generated_image_url(prompt)
            
            placeholder.image(img_url, caption=f"Generated: {prompt}", use_column_width=True)
            st.session_state.messages.append({
                "role": "assistant", 
                "content": f"Here is your generated image for: **'{prompt}'**",
                "image_url": img_url
            })

        # 🔍 FEATURE 2: Keyless Live Web Search
        elif user_input.lower().startswith("/search"):
            query = user_input.replace("/search", "").strip()
            placeholder.markdown(f"🔍 *Searching live web for:* **'{query}'**...")
            
            search_text = execute_free_search(query)
            
            # Format and summarize
            summary_prompt = f"User search: '{query}'\n\nSearch Results:\n{search_text}\n\nSummarize clearly:"
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(summary_prompt)
            
            placeholder.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})

        # 👀 FEATURE 3: Image Vision / Multimodal Analysis
        elif image_to_analyze is not None:
            placeholder.markdown("👀 *Analyzing uploaded image...*")
            model = genai.GenerativeModel("gemini-1.5-flash")
            
            response = model.generate_content([user_input, image_to_analyze])
            placeholder.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})

        # 💬 FEATURE 4: Standard Chat Response
        else:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(user_input)
            placeholder.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})
