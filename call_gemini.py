import time
from google import genai

# Models to attempt, in order of preference
MODELS = ["gemini-3.8-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

def send_message_with_retry(chat, message, max_retries=3):
    """Sends a message with automatic exponential backoff on 503 errors."""
    for attempt in range(max_retries):
        try:
            return chat.send_message(message)
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                wait_time = (attempt + 1) * 3
                print(f"\n[Server busy (503). Retrying in {wait_time}s... (Attempt {attempt + 1}/{max_retries})]")
                time.sleep(wait_time)
            else:
                raise e
    raise Exception("Model remains unavailable after multiple retries.")
