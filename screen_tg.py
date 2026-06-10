import sys
import io
import os
import threading
import base64
import time
import queue
import tkinter as tk
from tkinter import ttk

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes
    ctypes.windll.user32.ShowWindow(
        ctypes.windll.kernel32.GetConsoleWindow(), 0
    )

from dotenv import load_dotenv
load_dotenv()

import mss
import requests
from PIL import Image
from mistralai.client import Mistral

BOT_TOKEN           = os.getenv("BOT_TOKEN")
CHAT_ID             = os.getenv("CHAT_ID")
MISTRAL_API_KEY     = os.getenv("MISTRAL_API_KEY")
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY")
QUIT_HOTKEY         = os.getenv("QUIT_HOTKEY", "ctrl+alt+shift+q")
HOTKEY              = os.getenv("HOTKEY", "f8")
SOLVER_MODEL        = os.getenv("SOLVER_MODEL", "mistral-large-latest")

PROVIDER_ORDER = ["mistral", "gemini"]
_stop = threading.Event()

# ─── Стан поточного завдання ─────────────────────────────────────────────────
_thinking_msg_id = None              # message_id повідомлення "Думаю..."
_cancel_event    = threading.Event() # встановлюється при натисканні ⏹ Скасувати
_task_queue: queue.Queue = queue.Queue()  # черга завдань
_task_lock       = threading.Lock()  # захист _thinking_msg_id
_worker_running  = False             # чи зараз виконується воркер

# ─── Клієнти ──────────────────────────────────────────────────────────────────

_mistral_client    = None
_openrouter_client = None
_gemini_model      = "google/gemini-2.5-flash"

def _get_mistral():
    global _mistral_client
    if _mistral_client is None and MISTRAL_API_KEY:
        _mistral_client = Mistral(api_key=MISTRAL_API_KEY)
    return _mistral_client

def _get_openrouter():
    global _openrouter_client
    if _openrouter_client is None and OPENROUTER_API_KEY:
        from openai import OpenAI
        _openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
    return _openrouter_client

# ─── Перевірка токенів ────────────────────────────────────────────────────────

def _check_mistral_tokens() -> bool:
    try:
        client = _get_mistral()
        if not client:
            return False
        client.chat.complete(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "ping"}],
        )
        return True
    except Exception as e:
        s = str(e).lower()
        return "401" not in s and "429" not in s and "quota" not in s

def _check_gemini_tokens() -> bool:
    try:
        client = _get_openrouter()
        if not client:
            return False
        client.chat.completions.create(
            model=_gemini_model, max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True
    except Exception as e:
        s = str(e).lower()
        return "401" not in s and "429" not in s and "quota" not in s

def check_all_tokens() -> dict:
    return {
        "mistral": _check_mistral_tokens(),
        "gemini":  _check_gemini_tokens(),
    }

# ─── Fallback ─────────────────────────────────────────────────────────────────

AI_PROVIDER = "mistral"
OUTPUT_MODE = "telegram"

def _is_quota_error(e: Exception) -> bool:
    s = str(e).lower()
    return any(k in s for k in ("429", "quota", "rate limit", "insufficient", "credit", "overloaded", "limit exceeded"))

def _next_provider(current: str):
    try:
        idx = PROVIDER_ORDER.index(current)
        for candidate in PROVIDER_ORDER[idx + 1:]:
            return candidate
    except ValueError:
        pass
    return None

# ─── Overlay ──────────────────────────────────────────────────────────────────
# Стиль: темно-синій заголовок "⚡ SCREEN SOLVER OVERLAY" + кнопка ×
# ПКМ → контекстне меню: Очистити / Сховати на 5 сек / Клік наскрізь / Прозорість / Закрити
# Повзунок прозорості: мін 20%, макс 100%, не можна зробити повністю прозорим
# OBS-невидимість: SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE=0x11)

# ── Кольорова схема ──
_C_BG        = "#1a2233"
_C_TITLEBAR  = "#151d2e"
_C_BORDER    = "#2a9d8f"
_C_TITLE_FG  = "#2ecc71"
_C_TEXT      = "#e0e8f0"
_C_CLOSE_FG  = "#e74c3c"
_C_MENU_BG   = "#1e2d40"
_C_MENU_FG   = "#c8d8e8"
_C_MENU_SEL  = "#2a9d8f"
_C_SLIDER_BG = "#0f1622"
_C_SLIDER_FG = "#2a9d8f"

_OV_W         = 500          # ширина overlay
_OV_ALPHA_MIN = 0.20         # мінімальна прозорість (20%) — не можна зробити повністю прозорим
_OV_ALPHA_DEF = 0.93         # прозорість за замовчуванням

_overlay_queue    = queue.Queue()
_overlay_win      = None
_overlay_label    = None
_overlay_after    = None
_overlay_root_ref = None
_click_through    = False
_drag_data        = {"x": 0, "y": 0}
_overlay_alpha    = _OV_ALPHA_DEF   # поточна прозорість (глобальна)

# ── Win32 утиліти ──────────────────────────────────────────────────────────────

def _apply_win32_obs_hide(hwnd: int):
    """WDA_EXCLUDEFROMCAPTURE — вікно невидиме для OBS/будь-якого screen capture."""
    GWL_EXSTYLE            = -20
    WS_EX_LAYERED          = 0x00080000
    WS_EX_TOOLWINDOW       = 0x00000080
    WDA_EXCLUDEFROMCAPTURE = 0x00000011

    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
    try:
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass

def _set_click_through_win32(hwnd: int, enable: bool):
    GWL_EXSTYLE       = -20
    WS_EX_TRANSPARENT = 0x00000020
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enable:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
    else:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT)

def _get_hwnd(win) -> int:
    hwnd = win.winfo_id()
    try:
        parent = ctypes.windll.user32.GetAncestor(hwnd, 2)
        if parent and parent != 0:
            return parent
    except Exception:
        pass
    return hwnd

# ── Вікно повзунка прозорості ──────────────────────────────────────────────────

def _show_opacity_slider(root):
    """Відкриває невелике вікно з повзунком прозорості оверлея."""
    global _overlay_alpha

    slider_win = tk.Toplevel(root)
    slider_win.overrideredirect(True)
    slider_win.attributes("-topmost", True)
    slider_win.config(bg=_C_BORDER)

    # Зовнішня рамка
    outer = tk.Frame(slider_win, bg=_C_BORDER, bd=0)
    outer.pack(fill="both", expand=True, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_C_SLIDER_BG)
    inner.pack(fill="both", expand=True)

    # Заголовок з кнопкою закрити
    header = tk.Frame(inner, bg=_C_TITLEBAR, height=24)
    header.pack(fill="x")
    header.pack_propagate(False)

    tk.Label(header, text="🔆 Прозорість оверлея",
             font=("Segoe UI", 9, "bold"), fg=_C_TITLE_FG, bg=_C_TITLEBAR,
             padx=8).pack(side="left", pady=3)

    close_lbl = tk.Label(header, text="✕",
                         font=("Consolas", 10, "bold"),
                         fg=_C_CLOSE_FG, bg=_C_TITLEBAR,
                         padx=8, cursor="hand2")
    close_lbl.pack(side="right", pady=2)
    close_lbl.bind("<Button-1>", lambda e: slider_win.destroy())
    close_lbl.bind("<Enter>",    lambda e: close_lbl.config(fg="#ff6b6b"))
    close_lbl.bind("<Leave>",    lambda e: close_lbl.config(fg=_C_CLOSE_FG))

    # Тіло
    body = tk.Frame(inner, bg=_C_SLIDER_BG, padx=14, pady=10)
    body.pack(fill="x")

    # Підпис поточного значення
    pct_var = tk.StringVar(value=f"{int(_overlay_alpha * 100)}%")
    pct_lbl = tk.Label(body, textvariable=pct_var,
                       font=("Consolas", 11, "bold"),
                       fg=_C_BORDER, bg=_C_SLIDER_BG)
    pct_lbl.pack(anchor="center", pady=(0, 4))

    # Підписи меж
    row = tk.Frame(body, bg=_C_SLIDER_BG)
    row.pack(fill="x")
    tk.Label(row, text="20%", font=("Segoe UI", 8), fg="#5a7a8a",
             bg=_C_SLIDER_BG).pack(side="left")
    tk.Label(row, text="100%", font=("Segoe UI", 8), fg="#5a7a8a",
             bg=_C_SLIDER_BG).pack(side="right")

    # Стиль повзунка — кастомний через ttk
    style = ttk.Style(slider_win)
    style.theme_use("clam")
    style.configure("Opacity.Horizontal.TScale",
                    background=_C_SLIDER_BG,
                    troughcolor="#0d1a26",
                    sliderlength=18,
                    sliderrelief="flat")

    slider_var = tk.DoubleVar(value=_overlay_alpha * 100)

    def _on_slider(val):
        global _overlay_alpha
        v = float(val)
        # Жорстке обмеження мінімуму — не нижче 20%
        if v < _OV_ALPHA_MIN * 100:
            v = _OV_ALPHA_MIN * 100
            slider_var.set(v)
        _overlay_alpha = round(v / 100, 2)
        pct_var.set(f"{int(v)}%")
        # Якщо оверлей зараз видимий — одразу оновлюємо
        if _overlay_win:
            cur = _overlay_win.attributes("-alpha")
            if cur > 0:
                _overlay_win.attributes("-alpha", _overlay_alpha)

    scale = ttk.Scale(body, from_=_OV_ALPHA_MIN * 100, to=100,
                      orient="horizontal", length=220,
                      variable=slider_var,
                      command=_on_slider,
                      style="Opacity.Horizontal.TScale")
    scale.pack(fill="x", pady=(2, 6))

    # Кнопка "Застосувати"
    apply_btn = tk.Button(body, text="Застосувати",
                          font=("Segoe UI", 9),
                          bg=_C_BORDER, fg="#ffffff",
                          relief="flat", cursor="hand2",
                          activebackground="#1e7a70",
                          command=slider_win.destroy)
    apply_btn.pack(pady=(2, 0))

    # Позиціонуємо поруч з оверлеєм або по центру
    slider_win.update_idletasks()
    sw_w = slider_win.winfo_reqwidth()
    sw_h = slider_win.winfo_reqheight()
    if _overlay_win:
        ox = _overlay_win.winfo_x()
        oy = _overlay_win.winfo_y()
        slider_win.geometry(f"{sw_w}x{sw_h}+{ox}+{oy - sw_h - 8}")
    else:
        scr_w = root.winfo_screenwidth()
        scr_h = root.winfo_screenheight()
        slider_win.geometry(f"{sw_w}x{sw_h}+{(scr_w - sw_w)//2}+{(scr_h - sw_h)//2}")

    slider_win.bind("<FocusOut>", lambda e: slider_win.destroy())
    slider_win.focus_force()

    if IS_WIN:
        slider_win.update_idletasks()
        hwnd = _get_hwnd(slider_win)
        _apply_win32_obs_hide(hwnd)

# ── Контекстне меню (ПКМ) ──────────────────────────────────────────────────────

def _show_context_menu(event, win, root):
    global _click_through, _overlay_after

    menu = tk.Toplevel(root)
    menu.overrideredirect(True)
    menu.attributes("-topmost", True)
    menu.config(bg=_C_MENU_BG)

    outer = tk.Frame(menu, bg=_C_BORDER, bd=1)
    outer.pack(fill="both", expand=True)
    inner = tk.Frame(outer, bg=_C_MENU_BG)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    items = [
        ("Очистити",           lambda: _menu_clear(win)),
        ("Сховати на 5 сек",   lambda: _menu_hide5(win)),
        ("Клік наскрізь",      lambda: _menu_toggle_clickthrough(win)),
        ("🔆 Прозорість...",   lambda: _show_opacity_slider(root)),
        ("Закрити оверлей",    lambda: _menu_close(win)),
    ]

    def _close_menu():
        try:
            menu.destroy()
        except Exception:
            pass

    for label, cmd in items:
        # Роздільник перед "Закрити"
        if label == "Закрити оверлей":
            sep = tk.Frame(inner, bg=_C_BORDER, height=1)
            sep.pack(fill="x", padx=8, pady=2)

        lbl = tk.Label(
            inner, text=label,
            font=("Segoe UI", 10), fg=_C_MENU_FG, bg=_C_MENU_BG,
            anchor="w", padx=16, pady=5, cursor="hand2",
        )
        lbl.pack(fill="x")

        def _make_cmd(c):
            def _do(e=None):
                _close_menu()
                c()
            return _do

        lbl.bind("<Enter>",    lambda e, l=lbl: l.config(bg=_C_MENU_SEL, fg="#ffffff"))
        lbl.bind("<Leave>",    lambda e, l=lbl: l.config(bg=_C_MENU_BG, fg=_C_MENU_FG))
        lbl.bind("<Button-1>", _make_cmd(cmd))

    menu.update_idletasks()
    mw = menu.winfo_reqwidth()
    mh = menu.winfo_reqheight()
    sx, sy = event.x_root, event.y_root
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    if sx + mw > sw:
        sx = sw - mw - 4
    if sy + mh > sh:
        sy = sh - mh - 4
    menu.geometry(f"+{sx}+{sy}")

    menu.bind("<FocusOut>", lambda e: _close_menu())
    menu.focus_force()

    if IS_WIN:
        menu.update_idletasks()
        hwnd = _get_hwnd(menu)
        _apply_win32_obs_hide(hwnd)

def _menu_clear(win):
    if _overlay_label:
        _overlay_label.config(text="")
    if _overlay_after and win:
        win.after_cancel(_overlay_after)
    if win:
        win.attributes("-alpha", 0.0)

def _menu_hide5(win):
    global _overlay_after
    if win:
        win.attributes("-alpha", 0.0)
        if _overlay_after:
            win.after_cancel(_overlay_after)
        def _show_back():
            if win and _overlay_label and _overlay_label.cget("text"):
                win.attributes("-alpha", _overlay_alpha)
        _overlay_after = win.after(5000, _show_back)

def _menu_toggle_clickthrough(win):
    global _click_through
    _click_through = not _click_through
    if IS_WIN and win:
        hwnd = _get_hwnd(win)
        _set_click_through_win32(hwnd, _click_through)

def _menu_close(win):
    if win:
        win.attributes("-alpha", 0.0)
    _stop.set()

# ── Головна функція побудови overlay ──────────────────────────────────────────

def _overlay_setup(root: tk.Tk):
    global _overlay_win, _overlay_label, _overlay_root_ref
    _overlay_root_ref = root

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.0)
    win.config(bg=_C_BORDER)

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    wh_init = 130
    x = (sw - _OV_W) // 2
    y = sh // 8
    win.geometry(f"{_OV_W}x{wh_init}+{x}+{y}")

    outer = tk.Frame(win, bg=_C_BORDER, bd=0)
    outer.pack(fill="both", expand=True, padx=1, pady=1)

    # ── Заголовок ──
    titlebar = tk.Frame(outer, bg=_C_TITLEBAR, height=26)
    titlebar.pack(fill="x", side="top")
    titlebar.pack_propagate(False)

    title_icon = tk.Label(titlebar, text="⚡ SCREEN SOLVER OVERLAY",
                          font=("Consolas", 9, "bold"),
                          fg=_C_TITLE_FG, bg=_C_TITLEBAR,
                          padx=8)
    title_icon.pack(side="left", pady=3)

    # Кнопка прозорості в заголовку
    opacity_btn = tk.Label(titlebar, text="🔆",
                           font=("Consolas", 10),
                           fg="#7ec8b8", bg=_C_TITLEBAR,
                           padx=4, cursor="hand2")
    opacity_btn.pack(side="right", pady=2, padx=2)
    opacity_btn.bind("<Button-1>", lambda e: _show_opacity_slider(root))
    opacity_btn.bind("<Enter>",    lambda e: opacity_btn.config(fg="#ffffff"))
    opacity_btn.bind("<Leave>",    lambda e: opacity_btn.config(fg="#7ec8b8"))

    close_btn = tk.Label(titlebar, text="✕",
                         font=("Consolas", 10, "bold"),
                         fg=_C_CLOSE_FG, bg=_C_TITLEBAR,
                         padx=8, cursor="hand2")
    close_btn.pack(side="right", pady=2)
    close_btn.bind("<Button-1>", lambda e: win.attributes("-alpha", 0.0))
    close_btn.bind("<Enter>",    lambda e: close_btn.config(fg="#ff6b6b"))
    close_btn.bind("<Leave>",    lambda e: close_btn.config(fg=_C_CLOSE_FG))

    # ── Тіло ──
    body = tk.Frame(outer, bg=_C_BG)
    body.pack(fill="both", expand=True)

    lbl = tk.Label(
        body, text="", font=("Consolas", 13, "bold"),
        fg=_C_TEXT, bg=_C_BG,
        wraplength=_OV_W - 24, justify="left",
        padx=12, pady=10, anchor="nw",
    )
    lbl.pack(fill="both", expand=True)

    # ── Drag ──
    def _drag_start(e):
        _drag_data["x"] = e.x
        _drag_data["y"] = e.y

    def _drag_motion(e):
        dx = e.x - _drag_data["x"]
        dy = e.y - _drag_data["y"]
        win.geometry(f"+{win.winfo_x() + dx}+{win.winfo_y() + dy}")

    for widget in (titlebar, title_icon, body, lbl):
        widget.bind("<ButtonPress-1>", _drag_start)
        widget.bind("<B1-Motion>",     _drag_motion)

    # ── ПКМ → контекстне меню ──
    def _on_rclick(e):
        _show_context_menu(e, win, root)

    for widget in (win, outer, titlebar, title_icon, body, lbl):
        widget.bind("<Button-3>", _on_rclick)

    # ── Win32: OBS-невидимість ──
    if IS_WIN:
        win.update_idletasks()
        hwnd = _get_hwnd(win)
        _apply_win32_obs_hide(hwnd)

    _overlay_win   = win
    _overlay_label = lbl

# ── Тік: обробка черги ────────────────────────────────────────────────────────

def _overlay_tick(root: tk.Tk):
    global _overlay_after
    try:
        while True:
            item     = _overlay_queue.get_nowait()
            text     = item["text"]
            duration = item.get("duration", 9)

            if _overlay_win and _overlay_label:
                _overlay_label.config(text=text)

                char_per_line = max(1, (_OV_W - 24) // 8)
                raw_lines = text.split("\n")
                total_lines = sum(
                    max(1, (len(l) + char_per_line - 1) // char_per_line)
                    for l in raw_lines
                )
                new_body_h = max(50, min(300, total_lines * 22 + 20))
                new_h = 26 + new_body_h + 2
                wx, wy = _overlay_win.winfo_x(), _overlay_win.winfo_y()
                _overlay_win.geometry(f"{_OV_W}x{new_h}+{wx}+{wy}")

                # Використовуємо поточну глобальну прозорість
                _overlay_win.attributes("-alpha", _overlay_alpha)

                if _overlay_after:
                    _overlay_win.after_cancel(_overlay_after)

                def _hide(w=_overlay_win):
                    if w:
                        w.attributes("-alpha", 0.0)

                _overlay_after = _overlay_win.after(duration * 1000, _hide)

    except queue.Empty:
        pass

    root.after(50, lambda: _overlay_tick(root))

def overlay_show(text: str, duration: int = 9):
    """Безпечно з будь-якого потоку."""
    _overlay_queue.put({"text": text, "duration": duration})

# ─── Скриншот ─────────────────────────────────────────────────────────────────

def make_screenshot() -> io.BytesIO:
    with mss.MSS() as sct:
        shot = sct.grab(sct.monitors[0])
        img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf  = io.BytesIO()
        img.save(buf, "JPEG", quality=90, optimize=True)
        buf.seek(0)
        return buf

# ─── Промпти ──────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = (
    "Витягни з цього скриншоту текст питання або завдання дослівно. "
    "Якщо є варіанти відповідей — перерахуй їх теж. "
    "Не розв'язуй сам, лише точно перепиши текст з екрану."
)
_SOLVE_PROMPT_MISTRAL = (
    "Дай правильну відповідь і поясни її двома реченнями. "
    "Якщо є варіанти — вкажи який правильний і чому. "
    "Якщо правильних варіантів кілька — назви всі вірні. "
    "ВАЖЛИВО: не використовуй LaTeX-розмітку (\\frac, \\cdot, \\[ тощо). "
    "Пиши математику звичайним текстом: дроби як 11/3, степені як 3^10, множення як ×, корінь як √."
)
_SOLVE_PROMPT_GEMINI = (
    "Розв'яжи задачу докладно, перевір відповідь крок за кроком. "
    "Переконайся, що обрано правильний варіант. "
    "Не використовуй LaTeX: дроби як 11/3, степені як 3^10, множення як ×."
)
_COMPRESS_PROMPT = (
    "Ось розв'язок задачі:\n\n{solution}\n\n"
    "Якщо правильних варіантів кілька — назви всі вірні. "
    "ВАЖЛИВО: не використовуй LaTeX-розмітку (\\frac, \\cdot, \\[ тощо). "
    "Пиши математику звичайним текстом: дроби як 11/3, степені як 3^10, множення як ×, корінь як √. "
    "Видай ТІЛЬКИ фінальну відповідь одним рядком. "
    "Якщо є буква варіанту — напиши її і значення. Приклад: 'Б — 180 см' або 'Д — 42'. "
    "Якщо варіантів немає — одним реченням. Жодних пояснень, жодних обчислень."
)

# ─── AI-виклики ───────────────────────────────────────────────────────────────

def _extract_mistral(image_b64):
    c = _get_mistral()
    r = c.chat.complete(model="pixtral-12b-2409", messages=[{"role":"user","content":[
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
        {"type":"text","text":_EXTRACT_PROMPT}]}])
    return r.choices[0].message.content

def _extract_gemini(image_b64):
    c = _get_openrouter()
    r = c.chat.completions.create(model=_gemini_model, max_tokens=1000, messages=[{"role":"user","content":[
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{image_b64}"}},
        {"type":"text","text":_EXTRACT_PROMPT}]}])
    return r.choices[0].message.content

def _solve_mistral(question):
    c = _get_mistral()
    r = c.chat.complete(model=SOLVER_MODEL, messages=[{"role":"user","content":f"{question}\n\n{_SOLVE_PROMPT_MISTRAL}"}])
    return r.choices[0].message.content

def _solve_gemini(question):
    c = _get_openrouter()
    r = c.chat.completions.create(model=_gemini_model, max_tokens=3000, messages=[{"role":"user","content":f"{question}\n\n{_SOLVE_PROMPT_GEMINI}"}])
    return r.choices[0].message.content

def _compress_mistral(solution):
    c = _get_mistral()
    r = c.chat.complete(model=SOLVER_MODEL, messages=[{"role":"user","content":_COMPRESS_PROMPT.format(solution=solution)}])
    return r.choices[0].message.content

def _compress_gemini(solution):
    try:
        if _get_mistral(): return _compress_mistral(solution)
    except Exception: pass
    c = _get_openrouter()
    r = c.chat.completions.create(model=_gemini_model, max_tokens=200, messages=[{"role":"user","content":_COMPRESS_PROMPT.format(solution=solution)}])
    return r.choices[0].message.content

_EXTRACT_FNS  = {"mistral": _extract_mistral,  "gemini": _extract_gemini}
_SOLVE_FNS    = {"mistral": _solve_mistral,    "gemini": _solve_gemini}
_COMPRESS_FNS = {"mistral": _compress_mistral, "gemini": _compress_gemini}

def _call_with_fallback(fn_map, *args):
    global AI_PROVIDER
    provider = AI_PROVIDER
    tried = set()
    while provider and provider not in tried:
        tried.add(provider)
        fn = fn_map.get(provider)
        if fn is None:
            provider = _next_provider(provider)
            continue
        # Перевіряємо скасування перед кожним викликом AI
        if _cancel_event.is_set():
            raise Exception("__cancelled__")
        try:
            result = fn(*args)
            AI_PROVIDER = provider
            return result, provider
        except Exception as e:
            if str(e) == "__cancelled__":
                raise
            if _is_quota_error(e):
                next_p = _next_provider(provider)
                if next_p:
                    _notify(f"⚠️ {provider.upper()}: токени вичерпані.\nПереключаюсь на {next_p.upper()}...")
                    AI_PROVIDER = next_p
                    provider = next_p
                else:
                    raise Exception("❌ Обидва провайдери вичерпали ліміт токенів.")
            else:
                raise
    raise Exception("❌ Не вдалося викликати жодного провайдера.")

# ─── Вивід ───────────────────────────────────────────────────────────────────

def _notify(text: str):
    """Надсилає "Думаю..." з кнопкою ⏹, зберігає його ID."""
    global _thinking_msg_id
    msg_id = send_with_cancel_button(text)
    with _task_lock:
        if "Думаю" in text:
            _thinking_msg_id = msg_id
    if OUTPUT_MODE == "overlay":
        overlay_show(text, duration=6)

def _deliver_answer(text: str, provider: str):
    """Редагує повідомлення "Думаю..." → готова відповідь (без кнопки скасування)."""
    global _thinking_msg_id
    emoji = {"mistral": "⚡", "gemini": "✨"}.get(provider, "🤖")
    full  = f"{emoji} [{provider.upper()}]\n{text}"
    with _task_lock:
        mid = _thinking_msg_id
        _thinking_msg_id = None
    if mid:
        edit_message(mid, full)
    else:
        send_message(full)
    if OUTPUT_MODE == "overlay":
        overlay_show(f"{emoji} {text}", duration=12)

# ─── Telegram API ────────────────────────────────────────────────────────────

_RETRY_DELAYS = [1, 2, 4]  # секунди між спробами (backoff)

def _tg_post(method, *, retries=3, **kwargs):
    """POST до Telegram API з retry+backoff при мережевих помилках."""
    last_err = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS[:retries - 1]):
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                timeout=30, **kwargs
            )
            data = r.json()
            if data.get("ok"):
                return data
            # Telegram повернув ok=false — не мережева помилка, не повторювати
            return data
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt + 1 < retries:
                continue
    return None

def delete_message(message_id: int):
    _tg_post("deleteMessage", data={"chat_id": CHAT_ID, "message_id": message_id})

def edit_message(message_id: int, text: str):
    """Редагує існуюче повідомлення (без кнопок)."""
    _tg_post("editMessageText", json={
        "chat_id": CHAT_ID,
        "message_id": message_id,
        "text": text,
    })

def send_message(text: str) -> int | None:
    """Надсилає повідомлення і повертає message_id (або None)."""
    resp = _tg_post("sendMessage", json={"chat_id": CHAT_ID, "text": text})
    try:
        return resp["result"]["message_id"]
    except Exception:
        return None

def send_with_cancel_button(text: str) -> int | None:
    """Надсилає повідомлення з кнопкою ⏹ Скасувати (inline keyboard)."""
    resp = _tg_post("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "⏹ Скасувати", "callback_data": "cancel_task"}
            ]]
        },
    })
    try:
        return resp["result"]["message_id"]
    except Exception:
        return None

def answer_callback(callback_query_id: str, text: str = ""):
    """Відповідає на callback щоб прибрати годинник у Telegram."""
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

def _provider_emoji(p): return {"mistral": "⚡", "gemini": "✨"}.get(p, "🤖")

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
            lines.append(f"  {'✅' if ok else '❌'} {name.upper()}: {'є' if ok else 'вичерпані / не налаштовані'}")
    lines.append(f"\n⌨️ Гаряча клавіша: {HOTKEY}")
    lines.append("🔗 Fallback: Mistral → Gemini")
    return "\n".join(lines)

def _switch_model():
    global AI_PROVIDER
    idx = PROVIDER_ORDER.index(AI_PROVIDER) if AI_PROVIDER in PROVIDER_ORDER else 0
    AI_PROVIDER = PROVIDER_ORDER[(idx + 1) % len(PROVIDER_ORDER)]

# ─── Черга завдань ───────────────────────────────────────────────────────────

def _run_task_queue():
    """Єдиний фоновий потік що обробляє чергу завдань по одному."""
    global _worker_running, _thinking_msg_id
    _worker_running = True
    while True:
        try:
            image_b64 = _task_queue.get(timeout=0.3)
        except queue.Empty:
            # Якщо черга порожня — потік завершується, наступний тригер запустить новий
            _worker_running = False
            return

        _cancel_event.clear()
        queue_size = _task_queue.qsize()
        label = f"📸 [{_provider_emoji(AI_PROVIDER)} {AI_PROVIDER.upper()}] Думаю..."
        if queue_size > 0:
            label += f"\n📋 Ще в черзі: {queue_size}"

        try:
            _notify(label)
            question, _ = _call_with_fallback(_EXTRACT_FNS, image_b64)
            answer, p_solve = _call_with_fallback(_SOLVE_FNS, question)
            if p_solve == "gemini" or len(answer) > 400:
                answer, _ = _call_with_fallback(_COMPRESS_FNS, answer)
            _deliver_answer(answer, p_solve)
        except Exception as e:
            with _task_lock:
                mid = _thinking_msg_id
                _thinking_msg_id = None
            if str(e) == "__cancelled__":
                if mid:
                    edit_message(mid, "⏹ Скасовано.")
                else:
                    send_message("⏹ Скасовано.")
            else:
                msg = f"❗ Помилка: {e}"
                if mid:
                    edit_message(mid, msg)
                else:
                    send_message(msg)
        finally:
            _task_queue.task_done()


def on_trigger_action():
    """Робить скриншот і кладе його в чергу завдань."""
    global _worker_running
    try:
        buf = make_screenshot()
        image_b64 = base64.b64encode(buf.read()).decode()
        _task_queue.put(image_b64)
        # Запускаємо воркер тільки якщо він не запущений
        with _task_lock:
            if not _worker_running:
                threading.Thread(target=_run_task_queue, daemon=True).start()
    except Exception as e:
        send_message(f"❗ Не вдалося зробити скриншот: {e}")

# ─── Telegram polling ─────────────────────────────────────────────────────────

def _get_start_offset():
    try:
        resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                            params={"offset": -1}, timeout=10)
        results = resp.json().get("result", [])
        if results:
            return results[-1]["update_id"] + 1
    except Exception:
        pass
    return 0

def telegram_polling_loop():
    offset = _get_start_offset()
    token_status = {}
    def _check_bg():
        nonlocal token_status
        token_status = check_all_tokens()
    t = threading.Thread(target=_check_bg, daemon=True)
    t.start()
    t.join(timeout=15)

    mode_icon = "📱" if OUTPUT_MODE == "telegram" else "🪟"
    _tg_post("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": f"🤖 Wrata — активна!\n{mode_icon} Режим: {OUTPUT_MODE.upper()}\n\n{_build_status_text(token_status or None)}",
        "reply_markup": {"keyboard": [
            [{"text": "📸 Зробити скрін"}, {"text": "🔄 Змінити модель"}],
            [{"text": "ℹ️ Статус"},        {"text": "❌ Зупинити бот"}],
        ], "resize_keyboard": True},
    })

    while not _stop.is_set():
        try:
            resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                                params={"offset": offset, "timeout": 20}, timeout=25)
            if resp.status_code != 200:
                time.sleep(1); continue
            data = resp.json()
            if not (data.get("ok") and data.get("result")):
                time.sleep(0.2); continue
            for update in data["result"]:
                offset = update["update_id"] + 1

                # ── Inline-кнопка (⏹ Скасувати) ──
                cb = update.get("callback_query")
                if cb and str(cb.get("from", {}).get("id")) == str(CHAT_ID) or \
                   cb and str(cb.get("message", {}).get("chat", {}).get("id")) == str(CHAT_ID):
                    if cb.get("data") == "cancel_task":
                        _cancel_event.set()
                        # Очищаємо чергу
                        while not _task_queue.empty():
                            try:
                                _task_queue.get_nowait()
                                _task_queue.task_done()
                            except queue.Empty:
                                break
                        answer_callback(cb["id"], "⏹ Скасовано")
                    continue

                # ── Звичайне повідомлення ──
                message = update.get("message")
                if message and str(message.get("chat", {}).get("id")) == str(CHAT_ID):
                    text = message.get("text", "")
                    if text == "📸 Зробити скрін":
                        on_trigger_action()
                    elif text == "🔄 Змінити модель":
                        _switch_model()
                        send_reply_keyboard(f"Модель змінено на {_provider_emoji(AI_PROVIDER)} {AI_PROVIDER.upper()} ✅")
                    elif text == "ℹ️ Статус":
                        send_message("🔍 Перевіряю токени...")
                        send_reply_keyboard(_build_status_text(check_all_tokens()))
                    elif text == "❌ Зупинити бот":
                        send_message("🛑 Бот зупиняється...")
                        _stop.set()
                    elif text in ("/start", "/menu"):
                        send_reply_keyboard("Панель керування 👇")
        except Exception:
            time.sleep(1)
        time.sleep(0.2)

# ─── Хоткей ───────────────────────────────────────────────────────────────────

def _quit_gracefully():
    send_message("🛑 Бот зупинено хоткеєм.")
    _stop.set()

def setup_hotkeys_thread():
    """Реєструє хоткеї і чекає _stop (блокує свій потік)."""
    if IS_WIN:
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
            except Exception: pass
            try: h_quit.press(listener.canonical(key))
            except Exception: pass
        def on_release(key):
            try: h_trigger.release(listener.canonical(key))
            except Exception: pass
            try: h_quit.release(listener.canonical(key))
            except Exception: pass
        with kb.Listener(on_press=on_press, on_release=on_release) as listener:
            _stop.wait()

# ─── Головний потік: tkinter mainloop ────────────────────────────────────────

def run_tk_main(output_mode: str):
    """
    Головний потік — єдиний хто торкається tkinter.
    Якщо overlay — тримає root живим і крутить чергу.
    """
    if output_mode != "overlay":
        _stop.wait()
        return

    root = tk.Tk()
    root.withdraw()
    root.attributes("-alpha", 0.0)
    root.overrideredirect(True)

    _overlay_setup(root)
    root.after(50, lambda: _overlay_tick(root))

    def _check_stop():
        if _stop.is_set():
            root.quit()
        else:
            root.after(200, _check_stop)
    root.after(200, _check_stop)

    root.mainloop()

# ─── Вибір режиму (до запуску tk mainloop) ───────────────────────────────────

def choose_output_mode() -> str:
    global OUTPUT_MODE
    result = ["telegram"]

    root = tk.Tk()
    root.title("Wrata — режим виводу")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    width, height = 420, 200
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")

    tk.Label(root, text="📤 Як показувати відповідь?",
             font=("Arial", 13, "bold")).pack(pady=12)

    frame = tk.Frame(root)
    frame.pack(pady=4)

    desc_var = tk.StringVar(value="")
    tk.Label(root, textvariable=desc_var, font=("Arial", 8),
             fg="gray", wraplength=390, justify="center").pack(pady=4)

    def select(choice):
        result[0] = choice
        root.destroy()

    btn_tg = tk.Button(frame, text="📱 Telegram", width=16, font=("Arial", 11),
                       command=lambda: select("telegram"))
    btn_tg.pack(side=tk.LEFT, padx=10)
    btn_tg.bind("<Enter>", lambda e: desc_var.set("Відповідь надсилається у Telegram"))
    btn_tg.bind("<Leave>", lambda e: desc_var.set(""))

    btn_ov = tk.Button(frame, text="🪟 Overlay", width=16, font=("Arial", 11),
                       command=lambda: select("overlay"))
    btn_ov.pack(side=tk.LEFT, padx=10)
    btn_ov.bind("<Enter>", lambda e: desc_var.set("Вікно поверх екрану + Telegram"))
    btn_ov.bind("<Leave>", lambda e: desc_var.set(""))

    root.protocol("WM_DELETE_WINDOW", lambda: select("telegram"))
    root.mainloop()

    OUTPUT_MODE = result[0]
    return result[0]

# ─── Точка входу ─────────────────────────────────────────────────────────────

def main():
    output_mode = choose_output_mode()

    threading.Thread(target=telegram_polling_loop, daemon=True).start()
    threading.Thread(target=setup_hotkeys_thread,  daemon=True).start()

    run_tk_main(output_mode)
    sys.exit(0)

if __name__ == "__main__":
    main()
