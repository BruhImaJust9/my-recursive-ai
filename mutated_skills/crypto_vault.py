import base64

def execute(user_input):
    try:
        if ":" not in user_input:
            return "❌ Format error. Use 'encode:text' or 'decode:text'"
        
        action, text = user_input.split(":", 1)
        action = action.strip().lower()
        
        if action == "encode":
            encoded_bytes = base64.b64encode(text.encode("utf-8"))
            return f"🔒 Encoded Message: {encoded_bytes.decode('utf-8')}"
        elif action == "decode":
            decoded_bytes = base64.b64decode(text.encode("utf-8"))
            return f"🔓 Decoded Message: {decoded_bytes.decode('utf-8')}"
        else:
            return "❌ Unknown action. Use 'encode' or 'decode'."
    except Exception as e:
        return f"❌ Crypto error: {str(e)}"
