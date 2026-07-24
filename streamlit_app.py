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

def optimize_search_query(user_prompt: str, category: str = "general") -> str:
    # 1. Strip conversational fluff
    cleaned = user_prompt.lower().replace("search", "").replace("what are", "").strip()

    # 2. Strip betting terms and force match recap keywords
    if "world cup" in cleaned or "super bowl" in cleaned:
        return f'"{cleaned}" final score champions match recap -betting -odds'
    
    # 3. Append domain anchors based on keywords
    if "movie" in cleaned or "grossing" in cleaned or "box office" in cleaned:
        return f'"{cleaned}" box office worldwide stats'
    elif "standings" in cleaned:
        return f'"{cleaned}" scores standings results'
        
    return cleaned

import re

def clean_search_query(user_query: str) -> str:
    """Strips conversational fluff and leading stop words to prevent dictionary results."""
    # Convert to lowercase and remove leading /search command
    query = user_query.lower().replace("/search", "").strip()
    
    # Strip trailing punctuation
    query = query.strip("!? ")
    
    # Remove leading common stop words/phrases that cause dictionary matches
    stop_phrases = ["the ", "a ", "an ", "who won ", "what is ", "tell me about "]
    for phrase in stop_phrases:
        if query.startswith(phrase):
            query = query[len(phrase):].strip()
            
    # Add quotes around multi-word titles if no quotes exist
    if " " in query and not ('"' in query or "'" in query):
        # Keeps phrases together so it searches for the exact title
        return f'"{query}"'
        
    return query

def get_image_url(prompt: str) -> str:
    """Returns the direct raw image URL from Pollinations."""
    encoded_prompt = urllib.parse.quote(prompt.strip())
    # Notice 'image.pollinations.ai/prompt/' instead of 'pollinations.ai/p/'
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=800&nologo=true"

def encode_image_to_base64(image: Image.Image) -> str:
    """Helper to convert uploaded PIL image into a base64 data string for Groq Vision."""
    buffered = io.BytesIO()
    
    # Convert RGBA, P, or LA image modes to standard RGB
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")
        
    # Resize high-res images so payload doesn't exceed API size limits
    image.thumbnail((1024, 1024))
        
    image.save(buffered, format="JPEG", quality=85)
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

# Create a two-column row at the bottom for the '+' menu and chat bar
col_popover, col_input = st.columns([1, 12])

# Variable to hold the quick-uploaded image from the menu
popover_image = None

with col_popover:
    # ➕ Gemini-style Floating Action Menu
    with st.popover("➕", help="Quick Actions & Attachments"):
        st.markdown("### 🛠️ Actions & Uploads")
        
        # 📸 Direct Image Uploader inside the Popover Menu
        popover_file = st.file_uploader(
            "📷 Attach Image for Vision AI", 
            type=["png", "jpg", "jpeg", "webp"],
            key="popover_file_uploader"
        )
        
        if popover_file:
            popover_image = Image.open(popover_file)
            st.image(popover_image, caption="Attached Image", use_container_width=True)
            st.success("Image attached! Type a prompt below.")

        st.markdown("---")
        
        # Action Shortcuts & Tips
        st.caption("🔍 **Live Search:** Type `/search <topic>`")
        st.caption("🎨 **Generate Image:** Type `/generate <prompt>`")
        
        st.markdown("---")
        if st.button("🗑️ Clear History", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

with col_input:
    user_input = st.chat_input("Type a question, ask about an image, /search, or /generate...")

# Combine sidebar upload AND popover upload into one target
active_image = popover_image if popover_image else image_to_analyze

if user_input and client:
    # Append User Input
    user_data = {"role": "user", "content": user_input}
    if active_image:
        user_data["uploaded_img"] = active_image
    st.session_state.messages.append(user_data)
    
    with st.chat_message("user"):
        st.markdown(user_input)
        if active_image:
            st.image(active_image, use_container_width=True)

    # Process Assistant Response
    with st.chat_message("assistant"):
        placeholder = st.empty()
        
        # 🎨 FEATURE 1: Image Generation
        if user_input.lower().startswith("/generate") or "generate an image" in user_input.lower():
            prompt = user_input.replace("/generate", "").strip()
            placeholder.markdown(f"🎨 *Generating image for:* **'{prompt}'**...")
            
            img_url = get_image_url(prompt)
            
            placeholder.image(img_url, caption=f"Generated: {prompt}", use_container_width=True)
            st.session_state.messages.append({
                "role": "assistant", 
                "content": f"Here is your generated image for: **'{prompt}'**",
                "image_url": img_url
            })

        # 🔍 FEATURE 2: Free Live Web Search
        elif user_input.lower().startswith("/search"):
            cleaned_query = clean_search_query(user_input)
            optimized_query = optimize_search_query(cleaned_query)
            
            placeholder.markdown(f"🔍 *Searching live web for:* **{optimized_query}**...")
            search_text = execute_free_search(optimized_query)
            
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system", 
                        "content": (
                            "Today's date is in 2026. You are a helpful assistant summarizing live web search results. "
                            "Structure the summary into clear categories using Markdown tables or concise bullet points. "
                            "Always separate 'Key Findings' from 'Sources & Metadata' for maximum scannability."
                        )
                    },
                    {"role": "user", "content": f"Query: '{optimized_query}'\n\nSearch Results:\n{search_text}"}
                ]
            )
            response_text = completion.choices[0].message.content
            placeholder.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})

        # 👀 FEATURE 3: Image Vision Analysis (via Llama 3.2 Vision)
        elif active_image is not None:
            placeholder.markdown("👀 *Analyzing attached image...*")
            base64_img = encode_image_to_base64(active_image)
            
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
            formatted_history = []
            for m in st.session_state.messages:
                if "content" in m and isinstance(m["content"], str):
                    if not isinstance(m["content"], list):
                        formatted_history.append({"role": m["role"], "content": m["content"]})

            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=formatted_history
            )
            response_text = completion.choices[0].message.content
            placeholder.markdown(response_text)
            st.session_state.messages.append({"role": "assistant", "content": response_text})
