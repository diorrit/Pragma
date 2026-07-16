import requests
import time
from config import BOT_TOKEN, CHAT_ID

_RETRY_DELAYS = [1, 2, 4]

def _tg_post(method, *, retries=3, **kwargs):
    for attempt, delay in enumerate([0] + _RETRY_DELAYS[:retries - 1]):
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", timeout=30, **kwargs)
            data = r.json()
            if data.get("ok"):
                return data
            return data
        except requests.exceptions.RequestException as e:
            if attempt + 1 < retries:
                continue
    return None

def delete_message(message_id: int):
    _tg_post("deleteMessage", data={"chat_id": CHAT_ID, "message_id": message_id})

def edit_message(message_id: int, text: str):
    _tg_post("editMessageText", json={
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
    })

def send_message(text: str) -> int | None:
    resp = _tg_post("sendMessage", json={"chat_id": CHAT_ID, "text": text})
    try:
        return resp["result"]["message_id"]
    except Exception:
        return None

def send_with_cancel_button(text: str) -> int | None:
    resp = _tg_post("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[{"text": "⏹ Скасувати", "callback_data": "cancel_task"}]]
        },
    })
    try:
        return resp["result"]["message_id"]
    except Exception:
        return None

def answer_callback(callback_query_id: str, text: str = ""):
    _tg_post("answerCallbackQuery", json={
        "callback_query_id": callback_query_id,
        "text": text,
    })

def send_reply_keyboard(text: str):
    _tg_post("sendMessage", json={
        "chat_id": CHAT_ID, "text": text,
        "reply_markup": {"keyboard": [
            [{"text": "📸 Зробити скрін"}, {"text": "🔄 Змінити модель"}],
            [{"text": "ℹ️ Статус"},        {"text": "❌ Зупинити бот"}],
        ], "resize_keyboard": True},
    })

def get_updates(offset: int = 0, timeout: int = 20):
    try:
        resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params={"offset": offset, "timeout": timeout}, timeout=timeout+5)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None
