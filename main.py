import sys
import ctypes

APP_NAME = "Desktop Platformer"


def enable_dpi_awareness():
    """Windows: opt out of DPI virtualisation. This must happen before anything
    queries the screen size or creates a window; otherwise Windows reports
    scaled-down sizes while screenshots come back at full resolution."""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()    # Vista / 7 fallback
        except Exception:
            pass


enable_dpi_awareness()

from pyray import *
from pyray import ffi
import pyautogui
from time import sleep, perf_counter
from collections import deque
import cv2
import numpy as np
import threading
import os.path
import sys
import json

# GLFW key codes
KEY_RIGHT, KEY_LEFT, KEY_DOWN, KEY_UP = 262, 263, 264, 265
KEY_A, KEY_D, KEY_S, KEY_W = 65, 68, 83, 87
KEY_ESCAPE = 256
MOUSE_LEFT, MOUSE_RIGHT = 0, 1

# raylib mouse cursors
CURSOR_DEFAULT = 0
CURSOR_HAND = 4         # pointing hand
CURSOR_MOVE = 9         # four-way arrows (raylib has no "closed hand" cursor)

# raylib config flags
FLAG_TRANSPARENT = 0x10
FLAG_UNDECORATED = 0x8
FLAG_TOPMOST = 0x1000
FLAG_MOUSE_PASSTHROUGH = 0x4000

# Folder containing the script, or PyInstaller's unpack dir when frozen.
base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))

pyautogui.FAILSAFE = False   # don't raise when the cursor touches a screen corner
pyautogui.PAUSE = 0


def asset(name):
    return os.path.join(base, 'assets', name)


def user_config_dir(app=APP_NAME):
    """Per-user, writable settings folder on every OS (the app folder may be read-only)."""
    if sys.platform == 'win32':
        root = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        root = os.path.expanduser('~/Library/Application Support')
    else:
        root = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    if sys.platform.startswith('linux'):
        app = app.lower().replace(' ', '-')   # ~/.config/desktop-platformer
    return os.path.join(root, app)


GRAVITY = 10000
FOOT_INSET = 50
ANIM_FRAME_TIME = 0.1        # run/jump/fall animations
IDLE_FRAME_TIME = 0.25       # idle animations are slower
CANNY_LOW, CANNY_HIGH = 100, 200
MIN_PLATFORM_WIDTH = 10


# User-adjustable settings
# key, label, min, max, step, format
SETTING_DEFS = [
    ("horizontal_speed", "Run speed (px/s)",     100,  3000, 50,   "%d"),
    ("jump_speed",       "Jump speed (px/s)",    500,  6000, 50,   "%d"),
    ("fast_fall",        "Fast-fall (px/frame)", 0,    40,   1,    "%d"),
    ("size",             "Size",                 0.25, 4.0,  0.05, "%.2fx"),
    ("target_fps",       "Target FPS",           30,   240,  10,   "%d"),
    ("screenshot_delay", "Screenshot delay (s)", 0.01, 1.0,  0.01, "%.2f"),
]

DEFAULTS = {
    "horizontal_speed": 1000,
    "jump_speed": 2600,
    "fast_fall": 10,
    "size": 1.0,
    "target_fps": 120,
    "screenshot_delay": 0.1,
}

SETTINGS_FILE = os.path.join(user_config_dir(), 'settings.json')
S = dict(DEFAULTS)


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    for key, _, lo, hi, step, _ in SETTING_DEFS:
        v = data.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = min(max(v, lo), hi)
            S[key] = int(round(v)) if isinstance(step, int) else round(float(v), 4)


def save_settings():
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(S, f, indent=2)
    except OSError as e:
        print("could not save settings:", e)


load_settings()

screen_w, screen_h = pyautogui.size()

# Display scale: physical screenshot pixels / logical points
# (2.0 on Retina / HiDPI, 1.0 otherwise). Falls back to 1.0 if capture fails.
try:
    SCALE = pyautogui.screenshot().size[0] / screen_w
except Exception as e:
    print("could not detect display scale, assuming 1.0:", e)
    SCALE = 1.0


def detect_ui_scale():
    """OS display-scaling factor (1.0 = 100%, 1.5 = 150%, ...).

    Windows and Linux report screen sizes in physical pixels once the process
    is DPI aware, so UI and sprite sizes have to be scaled up by this factor.
    macOS coordinates are already in points, so it needs no extra scaling."""
    try:
        if sys.platform == 'win32':
            try:
                dpi = ctypes.windll.user32.GetDpiForSystem()
            except AttributeError:   # Windows older than 10 (1607)
                hdc = ctypes.windll.user32.GetDC(0)
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
            return dpi / 96.0
        if sys.platform != 'darwin':
            for var in ('GDK_SCALE', 'QT_SCALE_FACTOR'):
                if os.environ.get(var):
                    return float(os.environ[var])
    except Exception as e:
        print("could not detect UI scale, assuming 1.0:", e)
    return 1.0


UI_C = min(max(detect_ui_scale(), 1.0), 4.0)
PX = UI_C / SCALE


def sprite_scale(): return S["size"] * PX
def hspeed():       return S["horizontal_speed"] * PX
def vspeed():       return S["jump_speed"] * PX
def gravity():      return -GRAVITY * PX
def fast_fall():    return S["fast_fall"] * PX
def foot_inset():   return FOOT_INSET * sprite_scale()


# App icon
if sys.platform == 'win32':
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DesktopPlatformer")
    except Exception:
        pass

set_config_flags(FLAG_TRANSPARENT | FLAG_UNDECORATED | FLAG_TOPMOST | FLAG_MOUSE_PASSTHROUGH)
init_window(screen_w, screen_h, APP_NAME)
set_window_position(0, 0)
set_target_fps(S["target_fps"])
set_exit_key(0)


def set_app_icon(path):
    if not os.path.exists(path):
        print("icon not found at", path)
        return
    if sys.platform == 'darwin':
        try:
            from AppKit import NSApplication, NSImage
            from Foundation import NSBundle
            info = NSBundle.mainBundle().infoDictionary()
            if info is not None:
                info['CFBundleName'] = APP_NAME   # name shown in the menu bar
            img = NSImage.alloc().initWithContentsOfFile_(path)
            if img is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(img)
        except ImportError:
            print("install PyObjC for the Dock icon: pip install pyobjc-framework-Cocoa")
    else:
        set_window_icon(load_image(path))


set_app_icon(asset('icon.png'))


class Anim:
    def __init__(self, path, idle=False):
        count = ffi.new('int *')
        self.img = load_image_anim(path, count)
        self.frames = count[0]
        self.texture = load_texture_from_image(self.img)
        self.idle = idle
        # raw pixel size (needed for frame offsets in upload())
        self.width = self.img.width
        self.height = self.img.height
        self.frame = 0
        self.timer = 0
        self.upload()

    @property
    def draw_width(self):
        return self.width * sprite_scale()

    @property
    def draw_height(self):
        return self.height * sprite_scale()

    @property
    def frame_time(self):
        return IDLE_FRAME_TIME if self.idle else ANIM_FRAME_TIME

    def upload(self):
        offset = self.frame * self.width * self.height * 4
        update_texture(self.texture, ffi.cast('char *', self.img.data) + offset)

    def reset(self):
        self.frame = 0
        self.timer = 0
        self.upload()

    def advance(self, dt):
        if self.frames <= 1:
            return
        self.timer += dt
        if self.timer >= self.frame_time:
            self.timer = 0
            self.frame = (self.frame + 1) % self.frames
            self.upload()

    def alpha_at(self, px, py):
        """Alpha (0-255) of the current frame at raw pixel (px, py)."""
        if not (0 <= px < self.width and 0 <= py < self.height):
            return 0
        offset = self.frame * self.width * self.height * 4 + (py * self.width + px) * 4 + 3
        return ffi.cast('unsigned char *', self.img.data)[offset]


class Sprite:
    def __init__(self, img, x=0, y=0):
        self.x = x
        self.y = y
        self.image = img

    @property
    def image(self):
        return self._image

    @image.setter
    def image(self, img):
        self._image = img
        img.reset()

    @property
    def width(self):
        return self._image.draw_width

    @property
    def height(self):
        return self._image.draw_height

    def draw(self):
        # sprite coords are screen coords; convert to window-local coords.
        win = get_window_position()
        draw_texture_ex(
            self.image.texture,
            Vector2(int(self.x - win.x), int(screen_h - self.y - self.height - win.y)),
            0.0,
            sprite_scale(),
            WHITE,
        )


idleR = Anim(asset('idle-r.gif'), idle=True)
idleL = Anim(asset('idle-l.gif'), idle=True)
runR = Anim(asset('run-r.gif'))
runL = Anim(asset('run-l.gif'))
downR = Anim(asset('down-r.gif'))
downL = Anim(asset('down-l.gif'))
upR = Anim(asset('up-r.png'))
upL = Anim(asset('up-l.png'))

sprite = Sprite(img=idleR, x=0, y=0)

vx = 0
vy = 0
DIR = 1  # Direction: -1 for left, 1 for right

# History of where the sprite was drawn, so the capture thread can mask it out
# at the moment the screenshot was actually taken (not when it started).
sprite_history = deque(maxlen=240)
history_lock = threading.Lock()
SCREENSHOT_LAG = 0.05   # seconds of slack for compositor / capture latency


def record_sprite():
    with history_lock:
        sprite_history.append((perf_counter(), sprite.x, sprite.y, sprite.width, sprite.height))


binary_array = np.zeros((screen_h, screen_w), np.uint8)
H, W = binary_array.shape          # rows = screen height, cols = screen width (logical)
SPRITE_PAD = -1

ui_state = None          # None | 'menu' | 'settings'


def get_platforms():
    global binary_array, H, W

    while True:
        # Don't scan while our own menu/settings panel is on screen, otherwise
        # its edges would turn into platforms. The last good map is kept.
        if ui_state is not None:
            sleep(0.05)
            continue

        try:
            t0 = perf_counter()
            screenshot = pyautogui.screenshot()
            t1 = perf_counter()
            np_img = np.array(screenshot)
            grayscale = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)

            blurred = cv2.GaussianBlur(grayscale, (3, 3), 1.4)
            edges = cv2.Canny(blurred, threshold1=CANNY_LOW, threshold2=CANNY_HIGH)

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (10, 5))
            bridged_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

            open_kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT, (MIN_PLATFORM_WIDTH, 1))
            clean_edges = cv2.morphologyEx(bridged_edges, cv2.MORPH_OPEN, open_kernel)

            # --- remove the sprite's area ---
            img_h, img_w = clean_edges.shape
            scale_x = img_w / screen_w     # 2.0 on a Retina display, 1.0 otherwise
            scale_y = img_h / screen_h

            # Every place the sprite was drawn while the screenshot could have been taken
            with history_lock:
                recent = list(sprite_history)
            rects = [r for r in recent if t0 - SCREENSHOT_LAG <= r[0] <= t1]
            earlier = [r for r in recent if r[0] < t0 - SCREENSHOT_LAG]
            if earlier:
                rects.append(earlier[-1])
            if not rects and recent:
                rects.append(recent[-1])

            for _, sx, sy, sw, sh in rects:
                # Sprite coords: origin bottom-left. Image/array: origin top-left, so flip y.
                x0 = int((sx - SPRITE_PAD) * scale_x)
                x1 = int((sx + sw + SPRITE_PAD) * scale_x)
                y0 = int((screen_h - (sy + sh + SPRITE_PAD)) * scale_y)   # top edge
                y1 = int((screen_h - (sy - SPRITE_PAD)) * scale_y)        # bottom edge

                x0, x1 = max(0, x0), min(img_w, x1)
                y0, y1 = max(0, y0), min(img_h, y1)
                clean_edges[y0:y1, x0:x1] = 0

            # shrink to logical resolution; INTER_AREA keeps thin lines (any coverage -> nonzero)
            small = cv2.resize(clean_edges, (screen_w, screen_h), interpolation=cv2.INTER_AREA)
            if ui_state is None:   # UI may have opened while we were working
                binary_array = (small > 0).astype(np.uint8)
                H, W = binary_array.shape   # now screen_h, screen_w

        except Exception as e:
            print("platform capture failed:", e)

        sleep(S["screenshot_delay"])


def collision(prev_y, new_y):
    """
    Returns the y (bottom-left origin) the sprite should land on
    if its feet hit a platform while moving from prev_y down to new_y,
    otherwise None.
    """

    global binary_array

    inset = foot_inset()
    # horizontal span of the feet
    left = int(max(0, sprite.x + inset))
    right = int(min(W - 1, sprite.x + sprite.width - inset))
    if right < left:
        return None

    # pixel rows just below the feet, from where we were down to where we are now
    hi = int(prev_y) - 1
    lo = int(new_y) - 1
    ys = np.arange(hi, lo - 1, -1)
    ys = ys[(ys >= 0) & (ys < H)]
    if ys.size == 0:
        return None

    # binary_array row 0 is the TOP of the screen, y=0 is the BOTTOM
    rows = (H - 1) - ys
    hits = binary_array[rows, left:right + 1].any(axis=1)

    if hits.any():
        platform_top = ys[np.argmax(hits)]   # first solid row going downward
        return platform_top + 1              # stand on top of it
    return None


thread = threading.Thread(target=get_platforms, daemon=True)
thread.start()


# ---------------------------------------------------------------------------
# Mouse: the window is click-through everywhere except over the sprite's
# opaque pixels (and while a menu is open), where passthrough is turned off so
# it can receive clicks. While passthrough is on the window gets no mouse
# events, so the cursor is read from the OS (pyautogui.position(), logical
# screen coords, top-left origin).
# ---------------------------------------------------------------------------
passthrough = True
dragging = False
drag_dx = 0
drag_dy = 0

cur_mx = 0
cur_my = 0
cursor_want = CURSOR_DEFAULT
cursor_now = CURSOR_DEFAULT


def set_passthrough(enable):
    global passthrough
    if enable == passthrough:
        return
    passthrough = enable
    if enable:
        set_window_state(FLAG_MOUSE_PASSTHROUGH)
    else:
        clear_window_state(FLAG_MOUSE_PASSTHROUGH)


def apply_cursor():
    global cursor_now
    if cursor_want != cursor_now:
        set_mouse_cursor(cursor_want)
        cursor_now = cursor_want


def sprite_hit(mx, my):
    # True if screen point (mx, my) is over an opaque pixel of the sprite
    lx = mx - sprite.x
    ly = my - (screen_h - sprite.y - sprite.height)
    if lx < 0 or ly < 0 or lx >= sprite.width or ly >= sprite.height:
        return False
    scale = sprite_scale()
    return sprite.image.alpha_at(int(lx / scale), int(ly / scale)) > 20


def handle_mouse():
    global dragging, drag_dx, drag_dy, ui_state, ui_just_opened
    global menu_x, menu_y, cur_mx, cur_my, cursor_want

    mx, my = pyautogui.position()
    cur_mx, cur_my = mx / UI_C, my / UI_C   # the UI works in scaled "logical" units
    cursor_want = CURSOR_DEFAULT

    # A menu/settings panel is open: the whole overlay is modal and the UI
    # code (ui_frame, called while drawing) handles everything
    if ui_state is not None:
        dragging = False
        set_passthrough(False)
        return

    if dragging:
        set_passthrough(False)
        cursor_want = CURSOR_MOVE
        if is_mouse_button_down(MOUSE_LEFT):
            inset = foot_inset()
            x = mx - drag_dx
            y = (screen_h - my) - drag_dy
            sprite.x = min(max(x, -inset), W - sprite.width + inset)
            sprite.y = min(max(y, 0), screen_h - sprite.height)
        else:
            dragging = False
        return

    hover = sprite_hit(mx, my)
    set_passthrough(not hover)
    if hover:
        cursor_want = CURSOR_HAND
        if is_mouse_button_pressed(MOUSE_LEFT):
            dragging = True
            cursor_want = CURSOR_MOVE
            drag_dx = mx - sprite.x
            drag_dy = (screen_h - my) - sprite.y
        elif is_mouse_button_pressed(MOUSE_RIGHT):
            ui_state = 'menu'
            ui_just_opened = True
            menu_x, menu_y = mx / UI_C, my / UI_C


# right click menu and settings ui

ui_just_opened = False
menu_x = 0
menu_y = 0
active_slider = None
settings_dirty = False
quit_requested = False

C_DIM = Color(0, 0, 0, 110)
C_PANEL = Color(34, 36, 42, 245)
C_BORDER = Color(90, 94, 105, 255)
C_TEXT = Color(235, 237, 242, 255)
C_MUTED = Color(160, 165, 178, 255)
C_BTN = Color(58, 61, 70, 255)
C_BTN_HOVER = Color(82, 112, 190, 255)
C_TRACK = Color(70, 74, 86, 255)
C_FILL = Color(104, 140, 230, 255)
C_KNOB = Color(240, 244, 255, 255)

FONT = 18
UW = int(screen_w / UI_C) + 1   # screen size in UI (logical) units
UH = int(screen_h / UI_C) + 1


def inside(mx, my, x, y, w, h):
    return x <= mx < x + w and y <= my < y + h


def ui_button(x, y, w, h, label):
    # Draw a button; returns True on the frame it is clicked.
    global cursor_want
    hover = inside(cur_mx, cur_my, x, y, w, h)
    draw_rectangle(int(x), int(y), int(w), int(h), C_BTN_HOVER if hover else C_BTN)
    tw = measure_text(label, FONT)
    draw_text(label, int(x + (w - tw) / 2), int(y + (h - FONT) / 2), FONT, C_TEXT)
    if hover:
        cursor_want = CURSOR_HAND
    return hover and is_mouse_button_pressed(MOUSE_LEFT)


def draw_context_menu(fresh):
    global ui_state, quit_requested
    items = ["Settings", "Quit"]
    w, ih, pad = 170, 34, 6
    h = ih * len(items) + pad * 2
    x = min(menu_x, UW - w - 4)
    y = min(menu_y, UH - h - 4)

    draw_rectangle(int(x), int(y), int(w), int(h), C_PANEL)
    draw_rectangle_lines(int(x), int(y), int(w), int(h), C_BORDER)

    chosen = None
    for i, label in enumerate(items):
        iy = y + pad + i * ih
        if ui_button(x + pad, iy, w - pad * 2, ih, label) and not fresh:
            chosen = label

    if chosen == "Settings":
        ui_state = 'settings'
    elif chosen == "Quit":
        quit_requested = True
    elif not fresh:
        clicked_outside = (is_mouse_button_pressed(MOUSE_LEFT) or is_mouse_button_pressed(MOUSE_RIGHT)) \
            and not inside(cur_mx, cur_my, x, y, w, h)
        if clicked_outside or is_key_pressed(KEY_ESCAPE):
            ui_state = None


def close_settings():
    global ui_state, active_slider, settings_dirty
    ui_state = None
    active_slider = None
    if settings_dirty:
        save_settings()
        settings_dirty = False


def apply_settings():
    set_target_fps(int(S["target_fps"]))
    inset = foot_inset()
    sprite.x = min(max(sprite.x, -inset), W - sprite.width + inset)
    sprite.y = min(max(sprite.y, 0), screen_h - sprite.height)


def draw_slider(x, y, w, key, label, lo, hi, step, fmt):
    global active_slider, settings_dirty, cursor_want

    value = S[key]
    draw_text(label, int(x), int(y), FONT, C_TEXT)
    txt = fmt % value
    draw_text(txt, int(x + w - measure_text(txt, FONT)), int(y), FONT, C_MUTED)

    track_y = y + 32
    t = (value - lo) / (hi - lo)
    draw_rectangle(int(x), int(track_y - 3), int(w), 6, C_TRACK)
    draw_rectangle(int(x), int(track_y - 3), int(w * t), 6, C_FILL)
    draw_circle(int(x + w * t), int(track_y), 9.0, C_KNOB)

    hit = inside(cur_mx, cur_my, x - 10, y + 16, w + 20, 32)
    if hit:
        cursor_want = CURSOR_HAND
    if hit and is_mouse_button_pressed(MOUSE_LEFT):
        active_slider = key

    if active_slider == key:
        cursor_want = CURSOR_HAND
        if is_mouse_button_down(MOUSE_LEFT):
            f = min(max((cur_mx - x) / w, 0.0), 1.0)
            v = lo + f * (hi - lo)
            v = round(v / step) * step
            v = min(max(v, lo), hi)
            v = int(round(v)) if isinstance(step, int) else round(float(v), 4)
            if v != S[key]:
                S[key] = v
                settings_dirty = True
                apply_settings()
        else:
            active_slider = None
            if settings_dirty:
                save_settings()
                settings_dirty = False


def draw_settings():
    global settings_dirty
    col_w, gap, rows_per_col, row_h = 320, 24, len(SETTING_DEFS), 56
    pw = col_w + gap * 2
    ph = 64 + rows_per_col * row_h + 70
    px = (UW - pw) // 2
    py = (UH - ph) // 2

    draw_rectangle(0, 0, UW, UH, C_DIM)
    draw_rectangle(px, py, pw, ph, C_PANEL)
    draw_rectangle_lines(px, py, pw, ph, C_BORDER)
    draw_text("Settings", px + gap, py + 20, 26, C_TEXT)

    for i, (key, label, lo, hi, step, fmt) in enumerate(SETTING_DEFS):
        col, row = divmod(i, rows_per_col)
        x = px + gap + col * (col_w + gap)
        y = py + 64 + row * row_h
        draw_slider(x, y, col_w, key, label, lo, hi, step, fmt)

    by = py + ph - 54
    if ui_button(px + gap, by, 180, 36, "Reset to defaults"):
        S.update(DEFAULTS)
        apply_settings()
        save_settings()
        settings_dirty = False
    if ui_button(px + pw - gap - 120, by, 120, 36, "Close") or is_key_pressed(KEY_ESCAPE):
        close_settings()


def ui_frame():
    global ui_just_opened
    if ui_state is None:
        return
    fresh = ui_just_opened
    ui_just_opened = False
    if ui_state == 'menu':
        draw_context_menu(fresh)
    elif ui_state == 'settings':
        draw_settings()


def on_draw():
    record_sprite()
    begin_drawing()
    clear_background(BLANK)
    sprite.draw()

    # UI is laid out in logical units; zoom by the OS scale factor and shift by
    # the real window position so it lines up with the (physical-pixel) screen.
    win = get_window_position()
    begin_mode_2d(Camera2D(Vector2(-win.x, -win.y), Vector2(0, 0), 0.0, UI_C))
    ui_frame()
    end_mode_2d()

    end_drawing()
    apply_cursor()


def update(dt):

    # Arrow keys and WASD controls
    global vx, vy, DIR

    if dragging:
        # held by the mouse: no physics
        vx = 0
        vy = 0
        held = upR if DIR == 1 else upL
        if sprite.image != held:
            sprite.image = held
        return

    prev_y = sprite.y
    inset = foot_inset()
    sprite.x += vx * dt
    sprite.y += vy * dt
    vy += gravity() * dt

    if vy <= 0:
        land_y = collision(prev_y, sprite.y)
        if land_y is not None:
            sprite.y = land_y
            vy = 0

    if sprite.y <= 0:
        sprite.y = 0
        vy = 0

    if sprite.x < -inset:
        sprite.x = -inset
        vx = 0

    if sprite.x > W - sprite.width + inset:
        sprite.x = W - sprite.width + inset
        vx = 0

    if vx == 0 and vy == 0:
        if sprite.image != idleR and DIR == 1:
            sprite.image = idleR
        if sprite.image != idleL and DIR == -1:
            sprite.image = idleL
    elif vy > 0:
        if DIR == 1:
            if sprite.image != upR:
                sprite.image = upR
        elif DIR == -1:
            if sprite.image != upL:
                sprite.image = upL
    elif vy < 0:
        if DIR == 1:
            if sprite.image != downR:
                sprite.image = downR
        elif DIR == -1:
            if sprite.image != downL:
                sprite.image = downL
    elif vx > 0:
        if sprite.image != runR:
            sprite.image = runR
    elif vx < 0:
        if sprite.image != runL:
            sprite.image = runL

    vx = 0  # Reset vx each frame

    if ui_state is not None:
        return   # ignore keyboard while a menu is open

    if is_key_down(KEY_LEFT) or is_key_down(KEY_A):
        vx -= hspeed()
        DIR = -1

    if is_key_down(KEY_RIGHT) or is_key_down(KEY_D):
        vx += hspeed()
        DIR = 1

    if (is_key_down(KEY_UP) or is_key_down(KEY_W)) and vy == 0:
        vy = vspeed()

    if is_key_down(KEY_DOWN) or is_key_down(KEY_S):
        sprite.y -= fast_fall()


# Main update loop
while not window_should_close() and not quit_requested:
    dt = get_frame_time()
    handle_mouse()
    update(dt)
    sprite.image.advance(dt)
    on_draw()

if settings_dirty:
    save_settings()
close_window()