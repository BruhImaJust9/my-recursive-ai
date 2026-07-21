import streamlit as st
import urllib.parse
from PIL import Image
import os
import base64
from groq import Groq
from duckduckgo_search import DDGS

# ==========================================
# 1. PAGE SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")
st.title("🤖 Intelligent AI Workspace")
st.caption("Powered by Groq Llama 3, Free Web Search, & Image Generation")

# Initialize Groq Client
GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets! Please add it to continue.")
    client = None

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hey there! I'm powered by Groq. Ask me anything, upload an image to analyze, try `/search <topic>`, or `/generate <prompt>`!"}
    ]

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def execute_free_search(query: str) -> str:
    """Free web search using DuckDuckGo — No API Key required!"""
    try:
        with DDGS(timeout=10) as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "No matching search results found."
            
        sources = [f"**{r['title']}**\nSnippet: {r['body']}\nURL: {r['href']}" for r in results]
        return "\n\n".join(sources)
    except Exception as e:
        return f"Search error or timeout: {str(e)}"

def get_generated_image_url(prompt: str) -> str:
    """Free image generation via Pollinations URL — No API Key required!"""
    encoded_prompt = urllib.parse.quote(prompt.strip())
    return f"https://pollinations.ai/p/{encoded_prompt}?width=800&height=800&seed=42"

def encode_image_to_base64(image: Image.Image) -> str:
    """Helper to convert uploaded PIL image into a base64 data string for Groq Vision."""
    import io
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

# ==========================================
# 3. SIDEBAR & IMAGE UPLOAD
# ==========================================
with st.sidebar:
    st.header("⚙️ Workspace Controls")
    
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

if user_input and client:
    # Append User Input
    user_data = {"role": "user", "content": user_input}
    if image_to_analyze:
        user_data["uploaded_img"] = image_to_analyze
    st.session_state.messages.append(user_data)
    
    with st.chat_message("user"):
        st.markdown(user_input)
        if image_to_analyze:
            st.image(image_to_analyze, use_column_width=True)

    # Process Assistant Response
    with st.chat_message("assistant"):
        placeholder = st.empty()
        
        # 🎨 FEATURE 1: Image Generation
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

        # 🔍 FEATURE 2: Free Live Web Search
        elif user_input.lower().startswith("/search"):
            query = user_input.replace("/search", "").strip()
            placeholder.markdown(f"🔍 *Searching live web for:* **'{query}'**...")
            
            search_text = execute_free_search(query)
            
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant. Summarize the user's web search results clearly and accurately."},
                    {"role": "user", "content": f"Query: '{query}'\n\nSearch Results:\n{search_text}"}
                ]
            )
            response_text = completion.choices[0].message.content
            placeholder.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})

        # 👀 FEATURE 3: Image Vision Analysis (via Llama 3.2 Vision)
        elif image_to_analyze is not None:
            placeholder.markdown("👀 *Analyzing uploaded image...*")
            base64_img = encode_image_to_base64(image_to_analyze)
            
            completion = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_input},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}
                            }
                        ]
                    }
                ]
            )
            response_text = completion.choices[0].message.content
            placeholder.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})

        # 💬 FEATURE 4: Standard Chat Response
        else:
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": user_input}]
            )
            response_text = completion.choices[0].message.content
            placeholder.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})
