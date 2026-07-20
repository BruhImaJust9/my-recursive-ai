def execute(user_input):
    if not user_input.strip():
        return "⚠️ Text payload is empty."
        
    char_count = len(user_input)
    word_count = len(user_input.split())
    line_count = len(user_input.splitlines())
    
    reading_time = round(word_count / 200, 1) if word_count > 0 else 0
    
    return f"📊 **Text Analytics completed:**\n- Lines: {line_count}\n- Words: {word_count}\n- Characters: {char_count}\n- Est. Reading Time: {reading_time} minutes"
