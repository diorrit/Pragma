import io
import sys
import mss
from PIL import Image

IS_WIN = sys.platform == "win32"

def get_mouse_position():
    if IS_WIN:
        import ctypes
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    return 0, 0

def make_screenshot() -> io.BytesIO:
    with mss.MSS() as sct:
        monitor_to_capture = sct.monitors[0] # Fallback to all
        
        if IS_WIN:
            mx, my = get_mouse_position()
            # Find which monitor contains the mouse cursor
            # sct.monitors[0] is all monitors combined
            # sct.monitors[1:] are individual monitors
            for m in sct.monitors[1:]:
                if m["left"] <= mx < m["left"] + m["width"] and m["top"] <= my < m["top"] + m["height"]:
                    monitor_to_capture = m
                    break
        else:
            # For non-Windows, capture primary monitor
            if len(sct.monitors) > 1:
                monitor_to_capture = sct.monitors[1]

        shot = sct.grab(monitor_to_capture)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90, optimize=True)
        buf.seek(0)
        return buf
