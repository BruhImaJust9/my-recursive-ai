import streamlit as st
import requests
from PIL import Image
import io
import os
import google.generativeai as genai

# ==========================================
# 1. PAGE CONFIGURATION & INITIALIZATION
# ==========================================
st.set_page_config(
    page_title="AI Workspace",
    page_icon="🤖",
    layout="wide"
)

st.title("🤖 Intelligent AI Workspace")
st.caption("Powered by Gemini Vision, Live Web Search, & Image Generation")

# Retrieve API Keys securely from Streamlit secrets or environment
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY", ""))
TAVILY_API_KEY = st.secrets.get("TAVILY_API_KEY", os.getenv("TAVILY_API_KEY", ""))

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Initialize Session State for Chat History
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Hello! How can I help you today? You can ask me questions, request live web searches, upload images for me to analyze, or ask me to generate images!"}
    ]

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

def execute_tavily_search(query: str) -> str:
    """Performs a clean, structured web search using Tavily API."""
    if not TAVILY_API_KEY:
        return "⚠️ Search skipped: Missing `TAVILY_API_KEY` in Streamlit secrets."
    
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": 3,
        "search_depth": "basic"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        results = data.get("results", [])
        
        if not results:
            return "No matching search results found."
            
        formatted_sources = []
        for i, r in enumerate(results, 1):
            formatted_sources.append(
                f"**Result {i}: {r.get('title', 'Untitled')}**\n"
                f"Snippet: {r.get('content', '')}\n"
                f"URL: {r.get('url', '')}\n"
            )
        return "\n".join(formatted_sources)
    except Exception as e:
        return f"Search error encountered: {str(e)}"


def generate_image_from_prompt(prompt: str):
    """
    Generates an image using an image generation model or API endpoint.
    Falls back to Imagen if configured on Google GenAI.
    """
    try:
        # Utilizing Imagen 3 via Google Generative AI SDK
        imagen_model = genai.ImageGenerationModel("imagen-3.0-generate-002")
        result = imagen_model.generate_images(
            prompt=prompt,
            number_of_images=1,
            aspect_ratio="1:1"
        )
        for img in result.images:
            return img._pil_image
    except Exception as e:
        st.error(f"Image generation error: {str(e)}")
        return None

# ==========================================
# 3. SIDEBAR CONTROLS & IMAGE UPLOAD
# ==========================================
with st.sidebar:
    st.header("⚙️ Controls & Uploads")
    
    # Image upload for Vision Analysis
    uploaded_file = st.file_uploader(
        "Upload an Image for Analysis", 
        type=["png", "jpg", "jpeg", "webp"]
    )
    
    image_to_analyze = None
    if uploaded_file:
        image_to_analyze = Image.open(uploaded_file)
        st.image(image_to_analyze, caption="Uploaded Image", use_column_width=True)
        st.success("Image attached! Type a prompt in the chat box to ask about this image.")

    st.markdown("---")
    if st.button("🗑️ Clear Chat History"):
        st.session_state.messages = []
        st.rerun()

# ==========================================
# 4. CHAT HISTORY DISPLAY
# ==========================================
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "image" in msg and msg["image"] is not None:
            st.image(msg["image"], use_column_width=True)

# ==========================================
# 5. USER INPUT HANDLING & LOGIC
# ==========================================
user_input = st.chat_input("Ask a question, type /search <query>, or ask to generate an image...")

if user_input:
    # Append User Message
    user_msg_data = {"role": "user", "content": user_input}
    if image_to_analyze:
        user_msg_data["image"] = image_to_analyze
    st.session_state.messages.append(user_msg_data)
    
    with st.chat_message("user"):
        st.markdown(user_input)
        if image_to_analyze:
            st.image(image_to_analyze, use_column_width=True)

    # Process AI Response
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        
        # Scenario 1: Image Generation Command
        if user_input.lower().startswith("/generate") or "generate an image" in user_input.lower():
            response_placeholder.markdown("🎨 *Generating image, please wait...*")
            clean_prompt = user_input.replace("/generate", "").strip()
            generated_img = generate_image_from_prompt(clean_prompt)
            
            if generated_img:
                response_placeholder.markdown(f"Here is your generated image for: **'{clean_prompt}'**")
                st.image(generated_img, use_column_width=True)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"Generated image for: '{clean_prompt}'",
                    "image": generated_img
                })
            else:
                response_placeholder.markdown("Failed to generate image. Please check API settings.")

        # Scenario 2: Web Search Request
        elif user_input.lower().startswith("/search"):
            search_query = user_input.replace("/search", "").strip()
            response_placeholder.markdown(f"🔍 *Searching live web for: '{search_query}'...*")
            
            search_data = execute_tavily_search(search_query)
            
            # Feed search results to Gemini for a clean summary
            prompt = (
                f"The user searched for: '{search_query}'\n\n"
                f"Live Web Results:\n{search_data}\n\n"
                f"Summarize these findings accurately and clearly for the user."
            )
            
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)
            
            response_placeholder.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})

        # Scenario 3: Image Vision / Multimodal Input
        elif image_to_analyze is not None:
            response_placeholder.markdown("👀 *Analyzing image...*")
            model = genai.GenerativeModel("gemini-1.5-flash")
            
            response = model.generate_content([user_input, image_to_analyze])
            response_placeholder.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})

        # Scenario 4: Standard Chat
        else:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(user_input)
            response_placeholder.markdown(response.text)
            st.session_state.messages.append({"role": "assistant", "content": response.text})
