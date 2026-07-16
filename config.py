import os
import json
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# API Keys
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Settings
HOTKEY = os.getenv("HOTKEY", "f8")
QUIT_HOTKEY = os.getenv("QUIT_HOTKEY", "ctrl+alt+shift+q")
SOLVER_MODEL = os.getenv("SOLVER_MODEL", "mistral-large-latest")
ENABLE_LOGGING = os.getenv("ENABLE_LOGGING", "false").lower() == "true"

# Fallback Provider Order
PROVIDER_ORDER = ["mistral", "gemini", "openai", "anthropic"]

# Load Prompts
PROMPTS = {}
try:
    with open("prompts.json", "r", encoding="utf-8") as f:
        PROMPTS = json.load(f)
except Exception as e:
    print(f"Warning: Could not load prompts.json: {e}")
    # Fallback default prompts
    PROMPTS = {
        "EXTRACT_PROMPT": "Витягни текст з екрану дослівно.",
        "SOLVE_PROMPT_MISTRAL": "Дай правильну відповідь.",
        "SOLVE_PROMPT_GEMINI": "Розв'яжи задачу докладно.",
        "SOLVE_PROMPT_DEFAULT": "Дай відповідь.",
        "COMPRESS_PROMPT": "Видай ТІЛЬКИ фінальну відповідь: {solution}"
    }

def get_prompt(key: str, **kwargs) -> str:
    text = PROMPTS.get(key, "")
    if kwargs:
        text = text.format(**kwargs)
    return text
