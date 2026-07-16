import sys
import queue
import tkinter as tk
from tkinter import ttk

IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes

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

_OV_W         = 500
_OV_ALPHA_MIN = 0.20
_OV_ALPHA_DEF = 0.93

_overlay_queue    = queue.Queue()
_overlay_win      = None
_overlay_label    = None
_overlay_after    = None
_overlay_root_ref = None
_click_through    = False
_drag_data        = {"x": 0, "y": 0}
_overlay_alpha    = _OV_ALPHA_DEF

def _apply_win32_obs_hide(hwnd: int):
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

def _show_opacity_slider(root):
    global _overlay_alpha
    slider_win = tk.Toplevel(root)
    slider_win.overrideredirect(True)
    slider_win.attributes("-topmost", True)
    slider_win.config(bg=_C_BORDER)

    outer = tk.Frame(slider_win, bg=_C_BORDER, bd=0)
    outer.pack(fill="both", expand=True, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_C_SLIDER_BG)
    inner.pack(fill="both", expand=True)

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

    body = tk.Frame(inner, bg=_C_SLIDER_BG, padx=14, pady=10)
    body.pack(fill="x")

    pct_var = tk.StringVar(value=f"{int(_overlay_alpha * 100)}%")
    pct_lbl = tk.Label(body, textvariable=pct_var,
                       font=("Consolas", 11, "bold"),
                       fg=_C_BORDER, bg=_C_SLIDER_BG)
    pct_lbl.pack(anchor="center", pady=(0, 4))

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
        if v < _OV_ALPHA_MIN * 100:
            v = _OV_ALPHA_MIN * 100
            slider_var.set(v)
        _overlay_alpha = round(v / 100, 2)
        pct_var.set(f"{int(v)}%")
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

    apply_btn = tk.Button(body, text="Застосувати",
                          font=("Segoe UI", 9),
                          bg=_C_BORDER, fg="#ffffff",
                          relief="flat", cursor="hand2",
                          activebackground="#1e7a70",
                          command=slider_win.destroy)
    apply_btn.pack(pady=(2, 0))

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

def _show_context_menu(event, win, root, stop_event):
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
        ("Закрити оверлей",    lambda: _menu_close(win, stop_event)),
    ]

    def _close_menu():
        try: menu.destroy()
        except: pass

    for label, cmd in items:
        if label == "Закрити оверлей":
            sep = tk.Frame(inner, bg=_C_BORDER, height=1)
            sep.pack(fill="x", padx=8, pady=2)

        lbl = tk.Label(inner, text=label, font=("Segoe UI", 10), fg=_C_MENU_FG, bg=_C_MENU_BG, anchor="w", padx=16, pady=5, cursor="hand2")
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
    mw, mh = menu.winfo_reqwidth(), menu.winfo_reqheight()
    sx, sy = event.x_root, event.y_root
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    if sx + mw > sw: sx = sw - mw - 4
    if sy + mh > sh: sy = sh - mh - 4
    menu.geometry(f"+{sx}+{sy}")
    menu.bind("<FocusOut>", lambda e: _close_menu())
    menu.focus_force()

    if IS_WIN:
        menu.update_idletasks()
        hwnd = _get_hwnd(menu)
        _apply_win32_obs_hide(hwnd)

def _menu_clear(win):
    if _overlay_label: _overlay_label.config(text="")
    if _overlay_after and win: win.after_cancel(_overlay_after)
    if win: win.attributes("-alpha", 0.0)

def _menu_hide5(win):
    global _overlay_after
    if win:
        win.attributes("-alpha", 0.0)
        if _overlay_after: win.after_cancel(_overlay_after)
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

def _menu_close(win, stop_event):
    if win: win.attributes("-alpha", 0.0)
    if stop_event: stop_event.set()

def overlay_setup(root: tk.Tk, stop_event):
    global _overlay_win, _overlay_label, _overlay_root_ref
    _overlay_root_ref = root

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.0)
    win.config(bg=_C_BORDER)

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    wh_init = 130
    x, y = (sw - _OV_W) // 2, sh // 8
    win.geometry(f"{_OV_W}x{wh_init}+{x}+{y}")

    outer = tk.Frame(win, bg=_C_BORDER, bd=0)
    outer.pack(fill="both", expand=True, padx=1, pady=1)

    titlebar = tk.Frame(outer, bg=_C_TITLEBAR, height=26)
    titlebar.pack(fill="x", side="top")
    titlebar.pack_propagate(False)

    title_icon = tk.Label(titlebar, text="⚡ SCREEN SOLVER OVERLAY", font=("Consolas", 9, "bold"), fg=_C_TITLE_FG, bg=_C_TITLEBAR, padx=8)
    title_icon.pack(side="left", pady=3)

    opacity_btn = tk.Label(titlebar, text="🔆", font=("Consolas", 10), fg="#7ec8b8", bg=_C_TITLEBAR, padx=4, cursor="hand2")
    opacity_btn.pack(side="right", pady=2, padx=2)
    opacity_btn.bind("<Button-1>", lambda e: _show_opacity_slider(root))
    opacity_btn.bind("<Enter>",    lambda e: opacity_btn.config(fg="#ffffff"))
    opacity_btn.bind("<Leave>",    lambda e: opacity_btn.config(fg="#7ec8b8"))

    close_btn = tk.Label(titlebar, text="✕", font=("Consolas", 10, "bold"), fg=_C_CLOSE_FG, bg=_C_TITLEBAR, padx=8, cursor="hand2")
    close_btn.pack(side="right", pady=2)
    close_btn.bind("<Button-1>", lambda e: win.attributes("-alpha", 0.0))
    close_btn.bind("<Enter>",    lambda e: close_btn.config(fg="#ff6b6b"))
    close_btn.bind("<Leave>",    lambda e: close_btn.config(fg=_C_CLOSE_FG))

    body = tk.Frame(outer, bg=_C_BG)
    body.pack(fill="both", expand=True)

    lbl = tk.Label(body, text="", font=("Consolas", 13, "bold"), fg=_C_TEXT, bg=_C_BG, wraplength=_OV_W - 24, justify="left", padx=12, pady=10, anchor="nw")
    lbl.pack(fill="both", expand=True)

    def _drag_start(e):
        _drag_data["x"], _drag_data["y"] = e.x, e.y
    def _drag_motion(e):
        dx, dy = e.x - _drag_data["x"], e.y - _drag_data["y"]
        win.geometry(f"+{win.winfo_x() + dx}+{win.winfo_y() + dy}")

    for w in (titlebar, title_icon, body, lbl):
        w.bind("<ButtonPress-1>", _drag_start)
        w.bind("<B1-Motion>",     _drag_motion)

    for w in (win, outer, titlebar, title_icon, body, lbl):
        w.bind("<Button-3>", lambda e: _show_context_menu(e, win, root, stop_event))

    if IS_WIN:
        win.update_idletasks()
        _apply_win32_obs_hide(_get_hwnd(win))

    _overlay_win, _overlay_label = win, lbl

def overlay_tick(root: tk.Tk):
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
                total_lines = sum(max(1, (len(l) + char_per_line - 1) // char_per_line) for l in raw_lines)
                new_h = 26 + max(50, min(300, total_lines * 22 + 20)) + 2
                _overlay_win.geometry(f"{_OV_W}x{new_h}+{_overlay_win.winfo_x()}+{_overlay_win.winfo_y()}")
                _overlay_win.attributes("-alpha", _overlay_alpha)

                if _overlay_after:
                    _overlay_win.after_cancel(_overlay_after)
                def _hide(w=_overlay_win):
                    if w: w.attributes("-alpha", 0.0)
                _overlay_after = _overlay_win.after(duration * 1000, _hide)
    except queue.Empty:
        pass
    root.after(50, lambda: overlay_tick(root))

def overlay_show(text: str, duration: int = 9):
    _overlay_queue.put({"text": text, "duration": duration})

def choose_output_mode() -> str:
    result = ["telegram"]
    root = tk.Tk()
    root.title("Pragma — режим виводу")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    width, height = 420, 200
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")

    tk.Label(root, text="📤 Як показувати відповідь?", font=("Arial", 13, "bold")).pack(pady=12)
    frame = tk.Frame(root)
    frame.pack(pady=4)

    desc_var = tk.StringVar(value="")
    tk.Label(root, textvariable=desc_var, font=("Arial", 8), fg="gray", wraplength=390, justify="center").pack(pady=4)

    def select(choice):
        result[0] = choice
        root.destroy()

    btn_tg = tk.Button(frame, text="📱 Telegram", width=16, font=("Arial", 11), command=lambda: select("telegram"))
    btn_tg.pack(side=tk.LEFT, padx=10)
    btn_tg.bind("<Enter>", lambda e: desc_var.set("Відповідь надсилається у Telegram"))
    btn_tg.bind("<Leave>", lambda e: desc_var.set(""))

    btn_ov = tk.Button(frame, text="🪟 Overlay", width=16, font=("Arial", 11), command=lambda: select("overlay"))
    btn_ov.pack(side=tk.LEFT, padx=10)
    btn_ov.bind("<Enter>", lambda e: desc_var.set("Вікно поверх екрану + Telegram"))
    btn_ov.bind("<Leave>", lambda e: desc_var.set(""))

    root.protocol("WM_DELETE_WINDOW", lambda: select("telegram"))
    root.mainloop()
    return result[0]
