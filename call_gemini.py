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

def main():
    api_key = "AQ.Ab8RN6KE6UJgp8-o97gMkfOVpwEc8tOSYWMi6ukGCr47KpqWhg"
    client = genai.Client(api_key=api_key)
    
    chat = None
    active_model = ""

    # Attempt to initialize with the best available model
    for model_name in MODELS:
        try:
            print(f"Connecting to {model_name}...")
            test_chat = client.chats.create(model=model_name)
            # Test connection with a lightweight check
            test_chat.send_message("hi")
            chat = test_chat
            active_model = model_name
            print(f"Successfully connected to {active_model}!\n")
            break
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                print(f"Model {model_name} is currently busy. Trying alternative model...")
                continue
            elif "404" in str(e):
                continue
            else:
                print(f"Error on {model_name}: {e}")

    if not chat:
        print("\nAll model endpoints are currently experiencing high demand. Please wait 2-3 minutes and try again.")
        return

    print("=== Gemini in VS Code ===")
    print("Type 'quit' or 'exit' to end the conversation.\n")

    while True:
        user_input = input("You: ")
        
        if user_input.lower() in ['quit', 'exit']:
            print("Ending chat. Goodbye!")
            break
            
        if not user_input.strip():
            continue

        try:
            response = send_message_with_retry(chat, user_input)
            print(f"\nGemini ({active_model}): {response.text}\n")
            print("-" * 40)
        except Exception as e:
            print(f"\nCould not complete request: {e}\n")

if __name__ == "__main__":
    main()
