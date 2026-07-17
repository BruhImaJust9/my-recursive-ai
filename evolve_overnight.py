import os
import requests
import json
import time

# Load your token
HF_TOKEN = os.environ.get("HF_TOKEN", "YOUR_HF_TOKEN_HERE") # Replace with your real token or set env var
SKILLS_DIR = "mutated_skills"

if not os.path.exists(SKILLS_DIR):
    os.makedirs(SKILLS_DIR)

# A list of tools we want the AI to try and invent overnight
ideas = [
    {"name": "math_helper", "task": "A function that calculates prime numbers.", "input": 17, "output_type": bool},
    {"name": "text_cleaner", "task": "A function that strips punctuation from strings.", "input": "Hello, World!", "output_type": str},
    {"name": "fizz_buzz", "task": "A function that returns 'Fizz', 'Buzz', or 'FizzBuzz' based on input.", "input": 15, "output_type": str}
]

def ask_ai_to_code(idea):
    prompt = f"""
    Write a raw Python file containing a function named 'execute'. 
    The function must perform this task: {idea['task']}
    It takes one argument and must return the type: {idea['output_type'].__name__}.
    
    Output ONLY the valid python code. Do not include markdown code blocks, explanations, or any other text.
    """
    
    API_URL = "https://router.huggingface.co/v1/chat/completions"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.3
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        code = response.json()["choices"][0]["message"]["content"].strip()
        # Clean markdown wrappers if AI still outputted them
        if code.startswith("```python"):
            code = code.split("```python")[1].split("```")[0].strip()
        elif code.startswith("```"):
            code = code.split("```")[1].split("```")[0].strip()
        return code
    except Exception as e:
        print(f"Error calling model: {e}")
        return None

print("🌙 Running overnight evolutionary loop...")
for idea in ideas:
    file_path = os.path.join(SKILLS_DIR, f"{idea['name']}.py")
    if os.path.exists(file_path):
        continue
        
    print(f"Generating skill: {idea['name']}...")
    generated_code = ask_ai_to_code(idea)
    
    if generated_code:
        # Save it to the vault!
        with open(file_path, "w") as f:
            f.write(generated_code)
        print(f"✅ Saved {idea['name']}.py to Vault.")
    
    time.sleep(5) # Pause to avoid hitting rate limits too fast
