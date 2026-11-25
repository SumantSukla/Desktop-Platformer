import pyglet
from pyglet.window import key
import pyautogui
from time import sleep
import cv2
import numpy as np
import threading
import os.path

update_delay = 0.1

display = pyglet.display.get_display()
screen = display.get_default_screen()

window = pyglet.window.Window(screen.width, screen.height, style=pyglet.window.Window.WINDOW_STYLE_OVERLAY)
base = os.path.dirname(os.path.abspath(__file__))
pyglet.resource.path = [os.path.join(base, 'assets')]
pyglet.resource.reindex()
window.activate()

idleR = pyglet.resource.animation('idle-r.gif')
idleL = pyglet.resource.animation('idle-l.gif')
runR = pyglet.resource.animation('run-r.gif')
runL = pyglet.resource.animation('run-l.gif')
downR = pyglet.resource.animation('down-r.gif')
downL = pyglet.resource.animation('down-l.gif')
upR = pyglet.resource.image('up-r.png')
upL = pyglet.resource.image('up-l.png')

icon = pyglet.image.load(os.path.join(base, 'assets', 'icon.png'))
window.set_icon(icon)

sprite = pyglet.sprite.Sprite(img = idleR, x=0, y=0)

# Track held-down keys
keys = key.KeyStateHandler()
window.push_handlers(keys)

vx = 0 
vy = 0  
HORIZONTAL_SPEED = 1000
VERTICAL_SPEED = 2600
DIR = 1  # Direction: -1 for left, 1 for right
GRAVITY = -10000

binary_array = np.zeros((screen.height, screen.width), np.uint8)
H, W = binary_array.shape          # rows = screen height, cols = screen width
FOOT_INSET = 50                    # ignore this many px at each side of the sprite
SPRITE_PAD = -1  

def get_platforms():
    global binary_array, update_delay, H, W

    while True:
        try:
            # Snapshot the sprite rect at the moment of the screenshot
            sx, sy, sw, sh = sprite.x, sprite.y, sprite.width, sprite.height

            screenshot = pyautogui.screenshot()
            np_img = np.array(screenshot)
            grayscale = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)

            blurred = cv2.GaussianBlur(grayscale, (3, 3), 1.4)
            edges = cv2.Canny(blurred, threshold1=100, threshold2=200)

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (10, 5))
            bridged_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

            open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (10, 1))
            clean_edges = cv2.morphologyEx(bridged_edges, cv2.MORPH_OPEN, open_kernel)

            # --- remove the sprite's area ---
            img_h, img_w = clean_edges.shape
            scale_x = img_w / window.width     # 2.0 on a Retina display, 1.0 otherwise
            scale_y = img_h / window.height

            # pyglet: origin bottom-left. Image/array: origin top-left, so flip y.
            x0 = int((sx - SPRITE_PAD) * scale_x)
            x1 = int((sx + sw + SPRITE_PAD) * scale_x)
            y0 = int((window.height - (sy + sh + SPRITE_PAD)) * scale_y)   # top edge
            y1 = int((window.height - (sy - SPRITE_PAD)) * scale_y)        # bottom edge

            x0, x1 = max(0, x0), min(img_w, x1)
            y0, y1 = max(0, y0), min(img_h, y1)
            clean_edges[y0:y1, x0:x1] = 0

            binary_array = (clean_edges > 0).astype(np.uint8)
            H, W = binary_array.shape          # rows = screen height, cols = screen width

        except Exception as e:
            print("platform capture failed:", e)

        sleep(update_delay)



def collision(prev_y, new_y):
    """
    Returns the y (pyglet coords, bottom-left origin) the sprite should land on
    if its feet hit a platform while moving from prev_y down to new_y,
    otherwise None.
    """

    global binary_array

    # horizontal span of the feet
    left  = int(max(0, sprite.x + FOOT_INSET))
    right = int(min(W - 1, sprite.x + sprite.width - FOOT_INSET))
    if right < left:
        return None

    # pixel rows just below the feet, from where we were down to where we are now
    hi = int(prev_y) - 1
    lo = int(new_y) - 1
    ys = np.arange(hi, lo - 1, -1)
    ys = ys[(ys >= 0) & (ys < H)]
    if ys.size == 0:
        return None

    # binary_array row 0 is the TOP of the screen, pyglet's y=0 is the BOTTOM
    rows = (H - 1) - ys
    hits = binary_array[rows, left:right + 1].any(axis=1)

    if hits.any():
        platform_top = ys[np.argmax(hits)]   # first solid row going downward
        return platform_top + 1              # stand on top of it
    return None


thread = threading.Thread(target=get_platforms, daemon=True)
thread.start()

@window.event
def on_draw():
    window.clear()
    sprite.draw()

def update(dt):

    # Arrow keys and WASD controls
    global vx, vy, DIR
    prev_y = sprite.y
    sprite.x += vx * dt
    sprite.y += vy * dt
    vy += GRAVITY * dt

    if vy <= 0:
        land_y = collision(prev_y, sprite.y)
        if land_y is not None:
            sprite.y = land_y
            vy = 0

    if sprite.y <= 0:
        sprite.y = 0
        vy = 0

    if sprite.x < -FOOT_INSET:
        sprite.x = -FOOT_INSET
        vx = 0

    if sprite.x > W - sprite.width + FOOT_INSET:
        sprite.x = W - sprite.width + FOOT_INSET
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
        
    if (keys[key.LEFT] or keys[key.A]):
        vx -= HORIZONTAL_SPEED
        DIR = -1

    if (keys[key.RIGHT] or keys[key.D]):
        vx += HORIZONTAL_SPEED
        DIR = 1

    if (keys[key.UP] or keys[key.W]) and vy == 0:
        vy = VERTICAL_SPEED

    if keys[key.DOWN] or keys[key.S]:
        sprite.y -= 10



# Schedule 120 FPS update loop
pyglet.clock.schedule_interval(update, 1 / 120.0)

pyglet.app.run()