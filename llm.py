import os
import io
import datetime
from PIL import Image
import pytesseract
from config import MISTRAL_API_KEY, OPENROUTER_API_KEY, SOLVER_MODEL, ENABLE_LOGGING, get_prompt, PROVIDER_ORDER
from mistralai.client import Mistral
from openai import OpenAI

_mistral_client = None
_openrouter_client = None

def get_mistral():
    global _mistral_client
    if _mistral_client is None and MISTRAL_API_KEY:
        _mistral_client = Mistral(api_key=MISTRAL_API_KEY)
    return _mistral_client

def get_openrouter():
    global _openrouter_client
    if _openrouter_client is None and OPENROUTER_API_KEY:
        _openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
    return _openrouter_client

# Log to history
def log_history(question: str, answer: str, provider: str):
    if not ENABLE_LOGGING:
        return
    try:
        with open("history.log", "a", encoding="utf-8") as f:
            f.write(f"=== {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{provider.upper()}] ===\n")
            f.write(f"Question: {question}\n")
            f.write(f"Answer: {answer}\n\n")
    except Exception as e:
        print(f"Error logging to history: {e}")

# OCR Fallback
def extract_text_local(image_bytes: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang="ukr+rus+eng")
        return text.strip()
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""

def _extract_mistral(image_b64):
    c = get_mistral()
    r = c.chat.complete(model="pixtral-12b-2409", messages=[{"role":"user","content":[
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
        {"type":"text","text":get_prompt("EXTRACT_PROMPT")}]}])
    return r.choices[0].message.content

def _extract_openrouter(image_b64, model):
    c = get_openrouter()
    r = c.chat.completions.create(model=model, max_tokens=1000, messages=[{"role":"user","content":[
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
        {"type":"text","text":get_prompt("EXTRACT_PROMPT")}]}])
    return r.choices[0].message.content

def _solve_mistral(question):
    c = get_mistral()
    r = c.chat.complete(model=SOLVER_MODEL, messages=[{"role":"user","content":f"{question}\n\n{get_prompt('SOLVE_PROMPT_MISTRAL')}"}])
    return r.choices[0].message.content

def _solve_openrouter(question, model, prompt_key):
    c = get_openrouter()
    r = c.chat.completions.create(model=model, max_tokens=3000, messages=[{"role":"user","content":f"{question}\n\n{get_prompt(prompt_key)}"}])
    return r.choices[0].message.content

def _compress_mistral(solution):
    c = get_mistral()
    r = c.chat.complete(model=SOLVER_MODEL, messages=[{"role":"user","content":get_prompt("COMPRESS_PROMPT", solution=solution)}])
    return r.choices[0].message.content

def _compress_openrouter(solution, model):
    c = get_openrouter()
    r = c.chat.completions.create(model=model, max_tokens=200, messages=[{"role":"user","content":get_prompt("COMPRESS_PROMPT", solution=solution)}])
    return r.choices[0].message.content

def process_task(image_b64: str, image_bytes: bytes, provider: str):
    # Try Local OCR first
    question = extract_text_local(image_bytes)
    if not question or len(question) < 10:
        # Fallback to Vision AI
        if provider == "mistral":
            question = _extract_mistral(image_b64)
        elif provider == "gemini":
            question = _extract_openrouter(image_b64, "google/gemini-2.5-flash")
        elif provider == "openai":
            question = _extract_openrouter(image_b64, "openai/gpt-4o-mini")
        elif provider == "anthropic":
            question = _extract_openrouter(image_b64, "anthropic/claude-3.5-sonnet")
        else:
            raise Exception("Unknown provider")

    # Solve
    if provider == "mistral":
        answer = _solve_mistral(question)
        if len(answer) > 400:
            answer = _compress_mistral(answer)
    elif provider == "gemini":
        answer = _solve_openrouter(question, "google/gemini-2.5-flash", "SOLVE_PROMPT_GEMINI")
        answer = _compress_openrouter(answer, "google/gemini-2.5-flash")
    elif provider == "openai":
        answer = _solve_openrouter(question, "openai/gpt-4o-mini", "SOLVE_PROMPT_DEFAULT")
        answer = _compress_openrouter(answer, "openai/gpt-4o-mini")
    elif provider == "anthropic":
        answer = _solve_openrouter(question, "anthropic/claude-3.5-sonnet", "SOLVE_PROMPT_DEFAULT")
        if len(answer) > 400:
            answer = _compress_openrouter(answer, "anthropic/claude-3.5-sonnet")

    log_history(question, answer, provider)
    return answer

def check_tokens():
    status = {}
    try:
        c = get_mistral()
        if c:
            c.chat.complete(model="mistral-small-latest", messages=[{"role": "user", "content": "ping"}])
            status["mistral"] = True
    except:
        status["mistral"] = False

    try:
        c = get_openrouter()
        if c:
            c.chat.completions.create(model="google/gemini-2.5-flash", max_tokens=10, messages=[{"role": "user", "content": "ping"}])
            status["gemini"] = True
            status["openai"] = True
            status["anthropic"] = True
    except:
        status["gemini"] = False
        status["openai"] = False
        status["anthropic"] = False
        
    return status

def get_next_provider(current: str):
    try:
        idx = PROVIDER_ORDER.index(current)
        for candidate in PROVIDER_ORDER[idx + 1:]:
            return candidate
    except ValueError:
        pass
    return None
