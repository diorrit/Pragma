import sys
import os
import time
import base64
import threading
import queue
import tkinter as tk

import config
from config import HOTKEY, QUIT_HOTKEY, PROVIDER_ORDER
import bot
import overlay
import capture
import llm

# Global state
_stop = threading.Event()
_cancel_event = threading.Event()
_task_queue = queue.Queue()
_task_lock = threading.Lock()
_worker_running = False
_thinking_msg_id = None
OUTPUT_MODE = "telegram"
AI_PROVIDER = "mistral"

def _provider_emoji(p):
    return {"mistral": "⚡", "gemini": "✨", "openai": "🧠", "anthropic": "🎭"}.get(p, "🤖")

def _build_status_text(token_status=None):
    p = AI_PROVIDER
    mode_icon = "📱" if OUTPUT_MODE == "telegram" else "🪟"
    lines = [
        f"{_provider_emoji(p)} Активна модель: {p.upper()}",
        f"{mode_icon} Режим виводу: {OUTPUT_MODE.upper()}",
    ]
    if token_status:
        lines.append("\n💳 Стан токенів:")
        for name in PROVIDER_ORDER:
            ok = token_status.get(name)
            lines.append(f"  {'✅' if ok else '❌'} {name.upper()}: {'є' if ok else 'немає/вичерпані'}")
    lines.append(f"\n⌨️ Гаряча клавіша: {HOTKEY}")
    return "\n".join(lines)

def _switch_model():
    global AI_PROVIDER
    idx = PROVIDER_ORDER.index(AI_PROVIDER) if AI_PROVIDER in PROVIDER_ORDER else 0
    AI_PROVIDER = PROVIDER_ORDER[(idx + 1) % len(PROVIDER_ORDER)]

def _notify(text: str):
    global _thinking_msg_id
    msg_id = bot.send_with_cancel_button(text)
    with _task_lock:
        if "Думаю" in text:
            _thinking_msg_id = msg_id
    if OUTPUT_MODE == "overlay":
        overlay.overlay_show(text, duration=6)

def _deliver_answer(text: str, provider: str):
    global _thinking_msg_id
    emoji = _provider_emoji(provider)
    full  = f"{emoji} [{provider.upper()}]\n{text}"
    with _task_lock:
        mid = _thinking_msg_id
        _thinking_msg_id = None
    if mid:
        bot.edit_message(mid, full)
    else:
        bot.send_message(full)
    if OUTPUT_MODE == "overlay":
        overlay.overlay_show(f"{emoji} {text}", duration=12)

def _run_task_queue():
    global _worker_running, _thinking_msg_id, AI_PROVIDER
    _worker_running = True
    while True:
        try:
            image_b64, image_bytes = _task_queue.get(timeout=0.3)
        except queue.Empty:
            _worker_running = False
            return

        _cancel_event.clear()
        queue_size = _task_queue.qsize()
        label = f"📸 [{_provider_emoji(AI_PROVIDER)} {AI_PROVIDER.upper()}] Думаю..."
        if queue_size > 0:
            label += f"\n📋 Ще в черзі: {queue_size}"

        try:
            _notify(label)
            # Try providers with fallback
            answer = None
            provider = AI_PROVIDER
            tried = set()
            while provider and provider not in tried:
                tried.add(provider)
                if _cancel_event.is_set():
                    raise Exception("__cancelled__")
                try:
                    answer = llm.process_task(image_b64, image_bytes, provider)
                    AI_PROVIDER = provider
                    break
                except Exception as e:
                    if str(e) == "__cancelled__":
                        raise
                    print(f"Provider {provider} failed: {e}")
                    next_p = llm.get_next_provider(provider)
                    if next_p:
                        _notify(f"⚠️ {provider.upper()} помилка.\nПереключаюсь на {next_p.upper()}...")
                        provider = next_p
                    else:
                        raise Exception("❌ Обидва провайдери вичерпали ліміт токенів або недоступні.")
            
            _deliver_answer(answer, provider)
            
        except Exception as e:
            with _task_lock:
                mid = _thinking_msg_id
                _thinking_msg_id = None
            if str(e) == "__cancelled__":
                if mid: bot.edit_message(mid, "⏹ Скасовано.")
                else: bot.send_message("⏹ Скасовано.")
            else:
                msg = f"❗ Помилка: {e}"
                if mid: bot.edit_message(mid, msg)
                else: bot.send_message(msg)
        finally:
            _task_queue.task_done()

def on_trigger_action():
    global _worker_running
    try:
        buf = capture.make_screenshot()
        image_bytes = buf.read()
        image_b64 = base64.b64encode(image_bytes).decode()
        _task_queue.put((image_b64, image_bytes))
        with _task_lock:
            if not _worker_running:
                threading.Thread(target=_run_task_queue, daemon=True).start()
    except Exception as e:
        bot.send_message(f"❗ Не вдалося зробити скриншот: {e}")

def telegram_polling_loop():
    offset = 0
    init_data = bot.get_updates(offset=-1, timeout=5)
    if init_data and init_data.get("result"):
        offset = init_data["result"][-1]["update_id"] + 1

    token_status = {}
    def _check_bg():
        nonlocal token_status
        token_status = llm.check_tokens()
    t = threading.Thread(target=_check_bg, daemon=True)
    t.start()
    t.join(timeout=15)

    mode_icon = "📱" if OUTPUT_MODE == "telegram" else "🪟"
    bot.send_reply_keyboard(f"🤖 Pragma — активна!\n{mode_icon} Режим: {OUTPUT_MODE.upper()}\n\n{_build_status_text(token_status or None)}")

    while not _stop.is_set():
        data = bot.get_updates(offset=offset)
        if not data or not data.get("result"):
            time.sleep(0.5)
            continue
        
        for update in data["result"]:
            offset = update["update_id"] + 1

            cb = update.get("callback_query")
            if cb:
                if cb.get("data") == "cancel_task":
                    _cancel_event.set()
                    while not _task_queue.empty():
                        try:
                            _task_queue.get_nowait()
                            _task_queue.task_done()
                        except queue.Empty: break
                    bot.answer_callback(cb["id"], "⏹ Скасовано")
                continue

            message = update.get("message")
            if message:
                text = message.get("text", "")
                if text == "📸 Зробити скрін":
                    on_trigger_action()
                elif text == "🔄 Змінити модель":
                    _switch_model()
                    bot.send_reply_keyboard(f"Модель змінено на {_provider_emoji(AI_PROVIDER)} {AI_PROVIDER.upper()} ✅")
                elif text == "ℹ️ Статус":
                    bot.send_message("🔍 Перевіряю токени...")
                    bot.send_reply_keyboard(_build_status_text(llm.check_tokens()))
                elif text == "❌ Зупинити бот":
                    bot.send_message("🛑 Бот зупиняється...")
                    _stop.set()
                elif text in ("/start", "/menu"):
                    bot.send_reply_keyboard("Панель керування 👇")
        time.sleep(0.2)

def _quit_gracefully():
    bot.send_message("🛑 Бот зупинено хоткеєм.")
    _stop.set()

def setup_hotkeys_thread():
    if sys.platform == "win32":
        import keyboard
        keyboard.add_hotkey(HOTKEY, on_trigger_action, suppress=True)
        keyboard.add_hotkey(QUIT_HOTKEY, _quit_gracefully, suppress=True)
        _stop.wait()
        keyboard.unhook_all()
    else:
        from pynput import keyboard as kb
        def _to_pynput(h):
            parts = h.split("+")
            return "+".join(p if len(p) == 1 else f"<{p}>" for p in parts)
        h_trigger = kb.HotKey(kb.HotKey.parse(_to_pynput(HOTKEY)), on_trigger_action)
        h_quit    = kb.HotKey(kb.HotKey.parse(_to_pynput(QUIT_HOTKEY)), _quit_gracefully)
        def on_press(key):
            try: h_trigger.press(listener.canonical(key))
            except: pass
            try: h_quit.press(listener.canonical(key))
            except: pass
        def on_release(key):
            try: h_trigger.release(listener.canonical(key))
            except: pass
            try: h_quit.release(listener.canonical(key))
            except: pass
        with kb.Listener(on_press=on_press, on_release=on_release) as listener:
            _stop.wait()

def main():
    global OUTPUT_MODE
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

    OUTPUT_MODE = overlay.choose_output_mode()

    threading.Thread(target=telegram_polling_loop, daemon=True).start()
    threading.Thread(target=setup_hotkeys_thread, daemon=True).start()

    if OUTPUT_MODE == "overlay":
        root = tk.Tk()
        root.withdraw()
        root.attributes("-alpha", 0.0)
        root.overrideredirect(True)
        overlay.overlay_setup(root, _stop)
        root.after(50, lambda: overlay.overlay_tick(root))
        def _check_stop():
            if _stop.is_set(): root.quit()
            else: root.after(200, _check_stop)
        root.after(200, _check_stop)
        root.mainloop()
    else:
        _stop.wait()

    sys.exit(0)

if __name__ == "__main__":
    main()
