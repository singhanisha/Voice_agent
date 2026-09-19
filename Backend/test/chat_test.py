"""
CLI Chat Simulation for the AI Voice Agent.
Run from the project root:  python chat_test.py

This script loads the system prompt from `prompt.txt` and simulates
a conversation between the LLM and the user via the terminal.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from llm import generate_response

PROMPT_FILE = "prompt.txt"


def load_system_prompt() -> str:
    """Load the base prompt from prompt.txt."""
    if not os.path.exists(PROMPT_FILE):
        print(f"❌ Error: {PROMPT_FILE} not found. Ensure it exists in the root directory.")
        sys.exit(1)
    
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def main():
    print("=" * 60)
    print("🤖 Agent Neha CLI Chat Simulation")
    print("Type 'quit' or 'exit' to stop the conversation.")
    print("=" * 60 + "\n")

    # Ensure API Key is available before starting
    if not os.getenv("GROQ_API_KEY"):
        print("❌ Error: GROQ_API_KEY environment variable is not set.")
        print("Set it using: $env:GROQ_API_KEY = 'your_key_here' (PowerShell)")
        sys.exit(1)

    system_prompt = load_system_prompt()
    
    # We store the system prompt as the first message in the history.
    # We will pass this history state to the generate_response function.
    conversation_history = [
        {"role": "system", "content": system_prompt}
    ]

    print("Agent Neha is initializing... (Say 'Hello' to begin!)\n")

    while True:
        try:
            user_input = input("🗣️  You: ")
            
            if user_input.strip().lower() in ['quit', 'exit']:
                print("\nEnding conversation. Goodbye!")
                break
                
            if not user_input.strip():
                continue

            print("⏳ Neha is typing...", end="\r")

            # Call the LLM. 
            # Note: We slice conversation_history[1:] if generate_response handles the system prompt internally,
            # but currently llm.py injects its own system prompt based on `language`. 
            # Let's adjust how we use generate_response for the test:
            # We will pass the full history EXCEPT we override the system prompt in llm.py?
            # Actually, llm.py `_build_messages` uses `_get_system_prompt`. We should modify llm.py to accept custom prompts
            # or just use it here directly. Let's pass the conversation history minus the system prompt, 
            # and let the LLM use its internal prompt, OR we update llm.py to read `prompt.txt`. 
            # Let's temporarily append the prompt via the user message or we'll update llm.py.
            
            # Since llm.py manages history and appends its own system prompt, we will just send our
            # `prompt.txt` content as the FIRST user message instructing the LLM, or we can just pass
            # the history. 
            # BUT the right way is: llm.py should read the system prompt!
            
            response = generate_response(
                user_text=user_input, 
                conversation_history=conversation_history,
                language="en"
            )

            # Clear the loading text
            print("                       ", end="\r")
            
            if response:
                print(f"👩 Neha: {response}\n")
                # Update history to maintain context
                conversation_history.append({"role": "user", "content": user_input})
                conversation_history.append({"role": "assistant", "content": response})
            else:
                print("❌ Neha failed to respond. Check API keys and network.\n")

        except KeyboardInterrupt:
            print("\n\nEnding conversation. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error during conversation: {e}")
            break


if __name__ == "__main__":
    main()
