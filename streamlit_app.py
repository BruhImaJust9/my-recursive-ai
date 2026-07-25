import streamlit as st
import urllib.parse
from PIL import Image
import os
import base64
import io
import re
import tempfile
from groq import Groq
from tavily import TavilyClient
from gtts import gTTS
from audio_recorder_streamlit import audio_recorder

# ==========================================
# 1. PAGE SETUP & CONFIG
# ==========================================
st.set_page_config(page_title="AI Workspace", page_icon="🤖", layout="wide")
st.title("🤖 Intelligent AI Workspace")
st.caption("Powered by Groq Llama 3, Free Web Search, Image Generation & Voice")

# Initialize Groq Client
GROQ_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))

if GROQ_KEY:
    client = Groq(api_key=GROQ_KEY)
else:
    st.warning("⚠️ Missing `GROQ_API_KEY` in Streamlit secrets! Please add it to continue.")
    client = None

if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hey there! I'm powered by Groq. Ask me anything, upload an image to analyze, try `/search <topic>`, `/generate <prompt>`, or speak using the ➕ menu!"}
    ]

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

# Fetch Tavily API Key
TAVILY_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))

def execute_free_search(query: str) -> str:
    """Bulletproof web search using Tavily API (No IP blocks/rate limits)."""
    if not TAVILY_KEY:
        return "⚠️ Missing `TAVILY_API_KEY` in Streamlit secrets!"

    try:
        tavily = TavilyClient(api_key=TAVILY_KEY)
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
    cleaned = user_prompt.lower().replace("search", "").replace("what are", "").strip()

    if "world cup" in cleaned or "super bowl" in cleaned:
        return f'"{cleaned}" final score champions match recap -betting -odds'
    
    if "movie" in cleaned or "grossing" in cleaned or "box office" in cleaned:
        return f'"{cleaned}" box office worldwide stats'
    elif "standings" in cleaned:
        return f'"{cleaned}" scores standings results'
        
    return cleaned

def clean_search_query(user_query: str) -> str:
    """Strips conversational fluff and leading stop words."""
    query = user_query.lower().replace("/search", "").strip().strip("!? ")
    
    stop_phrases = ["the ", "a ", "an ", "who won ", "what is ", "tell me about "]
    for phrase in stop_phrases:
        if query.startswith(phrase):
            query = query[len(phrase):].strip()
            
    if " " in query and not ('"' in query or "'" in query):
        return f'"{query}"'
        
    return query

def get_image_url(prompt: str) -> str:
    """Returns the direct raw image URL from Pollinations."""
    encoded_prompt = urllib.parse.quote(prompt.strip())
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=800&height=800&nologo=true"

def encode_image_to_base64(image: Image.Image) -> str:
    """Helper to convert uploaded PIL image into a base64 data string for Groq Vision."""
    buffered = io.BytesIO()
    
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")
        
    image.thumbnail((1024, 1024))
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def strip_thinking_process(text: str) -> str:
    """Removes internal <think>...</think> blocks from model outputs."""
    if not text:
        return ""
    cleaned_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return cleaned_text.strip()

def transcribe_audio_groq(audio_bytes: bytes, client) -> str:
    """Sends recorded audio bytes to Groq Whisper for instant Speech-to-Text."""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
            temp_audio.write(audio_bytes)
            temp_audio_path = temp_audio.name

        with open(temp_audio_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(temp_audio_path, file.read()),
                model="whisper-large-v3",
                response_format="json",
                language="en",
            )
        os.remove(temp_audio_path)
        return transcription.text
    except Exception as e:
        return f"Speech-to-Text Error: {str(e)}"

def generate_speech_audio(text: str) -> bytes:
    """Converts response text into speech audio bytes using gTTS."""
    clean_text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    clean_text = re.sub(r'[*_#~`]', '', clean_text).strip()
    
    if not clean_text:
        clean_text = "Here is your response."

    tts = gTTS(text=clean_text[:500], lang='en')
    fp = io.BytesIO()
    tts.write_to_fp(fp)
    fp.seek(0)
    return fp.read()

# ==========================================
# 3. SIDEBAR & IMAGE UPLOAD
# ==========================================
image_to_analyze = None

with st.sidebar:
    st.header("⚙️ Workspace Controls")
    
    uploaded_file = st.file_uploader(
        "Upload Image to Analyze", 
        type=["png", "jpg", "jpeg", "webp"],
        key="sidebar_file_uploader"
    )
    
    if uploaded_file:
        image_to_analyze = Image.open(uploaded_file)
        st.image(image_to_analyze, caption="Sidebar Attached Image", use_container_width=True)
        st.success("Image attached! Ask a question in chat about it.")

    st.markdown("---")
    if st.button("🗑️ Clear Chat"):
        st.session_state.messages = []
        st.rerun()

# ==========================================
# 4. CHAT HISTORY DISPLAY
# ==========================================
# Render all previous chat history first
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "image_url" in msg:
            st.image(msg["image_url"], use_container_width=True)
        elif "uploaded_img" in msg:
            st.image(msg["uploaded_img"], use_container_width=True)
        
        # 🎙️ Play audio if present
        if "audio" in msg and msg["audio"]:
            st.audio(msg["audio"], format="audio/mp3")

# ==========================================
# 5. INPUT LOGIC & ROUTING (ALWAYS AT BOTTOM)
# ==========================================

col_popover, col_input = st.columns([1, 12])

popover_image = None
audio_bytes = None

with col_popover:
    with st.popover("➕", help="Quick Actions, Attachments & Voice"):
        st.markdown("### 🎙️ Voice Input")
        
        audio_bytes = audio_recorder(
            text="Click to Record Voice",
            recording_color="#e84c3d",
            neutral_color="#6aa84f",
            icon_name="microphone",
            icon_size="2x",
            key="voice_recorder"
        )
        
        st.markdown("---")
        st.markdown("### 📷 Attach Image")
        
        popover_file = st.file_uploader(
            "Upload Vision Image", 
            type=["png", "jpg", "jpeg", "webp"],
            key="popover_file_uploader"
        )
        
        if popover_file:
            popover_image = Image.open(popover_file)
            st.image(popover_image, caption="Popover Attached Image", use_container_width=True)
            st.success("Image attached! Type a prompt below.")

        st.markdown("---")
        st.caption("🔍 **Live Search:** `/search <topic>`")
        st.caption("🎨 **Generate Image:** `/generate <prompt>`")
        
        st.markdown("---")
        if st.button("🗑️ Clear History", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

with col_input:
    user_input = st.chat_input("Type a question, ask about an image, /search, or /generate...")

# Process Speech-to-Text if audio recorded
recorded_text = ""
if audio_bytes and client:
    with st.spinner("🎙️ Transcribing voice..."):
        recorded_text = transcribe_audio_groq(audio_bytes, client)

# Combine text input or recorded voice
final_input = user_input if user_input else recorded_text
active_image = popover_image if popover_image else image_to_analyze

if final_input and client:
    user_data = {"role": "user", "content": final_input}
    if active_image:
        user_data["uploaded_img"] = active_image
        
    # Append to state and rerun immediately so it renders inside the history loop!
    st.session_state.messages.append(user_data)
    
    # Process Assistant Response
    # (We save the assistant response to state and rerun so the bar stays at the bottom)
    if final_input.lower().startswith("/generate") or "generate an image" in final_input.lower():
        prompt = final_input.replace("/generate", "").strip()
        img_url = get_image_url(prompt)
        st.session_state.messages.append({
            "role": "assistant", 
            "content": f"Here is your generated image for: **'{prompt}'**",
            "image_url": img_url
        })

    elif final_input.lower().startswith("/search"):
        cleaned_query = clean_search_query(final_input)
        optimized_query = optimize_search_query(cleaned_query)
        search_text = execute_free_search(optimized_query)
        
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system", 
                    "content": "Today's date is in 2026. You are a helpful assistant summarizing live web search results."
                },
                {"role": "user", "content": f"Query: '{optimized_query}'\n\nSearch Results:\n{search_text}"}
            ]
        )
        response_text = completion.choices[0].message.content
        clean_response = strip_thinking_process(response_text)
        st.session_state.messages.append({"role": "assistant", "content": clean_response})

    elif active_image is not None:
        base64_img = encode_image_to_base64(active_image)
        completion = client.chat.completions.create(
            model="qwen/qwen3.6-27b",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": final_input},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                    ]
                }
            ]
        )
        response_text = completion.choices[0].message.content
        clean_response = strip_thinking_process(response_text)
        st.session_state.messages.append({"role": "assistant", "content": clean_response})

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
        clean_response = strip_thinking_process(response_text)
        st.session_state.messages.append({"role": "assistant", "content": clean_response})

    # Trigger a rerun so new messages render ABOVE the input bar
    st.rerun()
