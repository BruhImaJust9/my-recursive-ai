import streamlit as st
import urllib.parse
from PIL import Image
import os
import base64
import io
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

from tavily import TavilyClient

# Fetch Tavily API Key
TAVILY_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))

def execute_free_search(query: str) -> str:
    """Bulletproof web search using Tavily API (No IP blocks/rate limits)."""
    if not TAVILY_KEY:
        return "⚠️ Missing `TAVILY_API_KEY` in Streamlit secrets!"

    try:
        tavily = TavilyClient(api_key=TAVILY_KEY)
        # Search web with up to 5 clean results
        response = tavily.search(query=query, max_results=5)
        
        results = response.get("results", [])
        if not results:
            return "No matching search results found."
            
        sources = []
        for r in results:
            title = r.get("title", "No Title")
            content = r.get("content", "No content snippet.")
            url = r.get("url", "#")
            sources.append(f"**{title}**\nSnippet: {content}\nURL: {url}")
            
        return "\n\n".join(sources)
    except Exception as e:
        return f"Search error: {str(e)}"

def get_image_url(prompt: str) -> str:
    """Returns the direct raw image URL from Pollinations."""
    encoded_prompt = urllib.parse.quote(prompt.strip())
    # Notice 'image.pollinations.ai/prompt/' instead of 'pollinations.ai/p/'
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=800&nologo=true"

def encode_image_to_base64(image: Image.Image) -> str:
    """Helper to convert uploaded PIL image into a base64 data string for Groq Vision."""
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
        st.image(image_to_analyze, caption="Attached Image", use_container_width=True)
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
            st.image(msg["image_url"], use_container_width=True)
        elif "uploaded_img" in msg:
            st.image(msg["uploaded_img"], use_container_width=True)

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
            st.image(image_to_analyze, use_container_width=True)

    # Process Assistant Response
    with st.chat_message("assistant"):
        placeholder = st.empty()
        
        # 🎨 FEATURE 1: Image Generation
        if user_input.lower().startswith("/generate") or "generate an image" in user_input.lower():
            prompt = user_input.replace("/generate", "").strip()
            placeholder.markdown(f"🎨 *Generating image for:* **'{prompt}'**...")
            
            img_url = get_image_url(prompt)
            
            # Use st.image directly with the direct image endpoint
            placeholder.image(img_url, caption=f"Generated: {prompt}", use_container_width=True)
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
                    {
                        "role": "system", 
                        "content": (
                            "Today's date is in 2026. You are a helpful assistant summarizing live web search results. "
                            "Rely strictly on the provided search context. Summarize all key facts, standings, names, or "
                            "numbers present in the snippets clearly using bullet points. If specific numbers are missing, "
                            "summarize the main story accurately based on what was provided."
                        )
                    },
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

        # 💬 FEATURE 4: Standard Chat Response (with full history memory)
        else:
            # Build history list for Groq (filters out non-text/uploaded objects)
            formatted_history = []
            for m in st.session_state.messages:
                if "content" in m and isinstance(m["content"], str):
                    # Basic protection against vision analysis messages
                    if not isinstance(m["content"], list):
                         formatted_history.append({"role": m["role"], "content": m["content"]})

            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=formatted_history
            )
            response_text = completion.choices[0].message.content
            placeholder.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})
