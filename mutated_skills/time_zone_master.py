from datetime import datetime

def execute(user_input):
    # Returns the exact real-time current date and time from the server host
    now = datetime.now()
    current_time = now.strftime("%I:%M %p")
    current_date = now.strftime("%A, %B %d, %Y")
    return f"📅 Real-Time Anchor: It is currently {current_time} on {current_date}."
