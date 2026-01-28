import importlib.util
import json
import logging
import os
import queue
import sys
import threading

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame
import pygame.freetype
from pygame._sdl2 import Window as pg_Window

have_tray = False
tray_error = None
if importlib.util.find_spec("pystray"):
    have_tray = True
    if sys.platform == "linux":
        if importlib.util.find_spec("gi"):
            import gi
            gi.require_version("GioUnix", "2.0")
        else:
            have_tray = False
if have_tray:
    from PIL import Image, ImageDraw
    try:
        from pystray import Icon, Menu, MenuItem
    except Exception as e:
        have_tray = False
        tray_error = e

support_clipboard = False
if importlib.util.find_spec("pyperclip"):
    import pyperclip
    support_clipboard = True

logger = logging.getLogger(__name__)

# default config
WINDOW_SIZE = (900, 600)
MAXIMIZED = False
FONT_SIZE = 12
FONT_NAME = None
APP_NAME = "Endcord"
REPEAT_DELAY = 400
REPEAT_INTERVAL = 25
CTRL_V_PASTE = False   # use Ctrl+V instead Ctrl+Shift+V to paste
enable_tray = True
TRAY_ICON_NORMAL = None
TRAY_ICON_UNREAD = None
TRAY_ICON_MENTION = None
DEFAULT_PAIR = ((255, 255, 255), (0, 0, 0))
SYSTEM_COLORS = (
    (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
    (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
    (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
)

# custom path
if sys.platform == "linux":
    path = os.environ.get("XDG_DATA_HOME", "")
    if path.strip():
        config_path = os.path.join(path, APP_NAME.lower(), "pgcurses.json")
    else:
        config_path = f"~/.config/{APP_NAME.lower()}/pgcurses.json"
elif sys.platform == "win32":
    config_path = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME, "pgcurses.json")
elif sys.platform == "darwin":
    config_path = f"~/Library/Application Support/{APP_NAME.lower()}/pgcurses.json"
#config_path = os.environ.get("PGCURSES_CONFIG")

# load config
if config_path:
    config_path = os.path.expanduser(config_path)
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config {config_path}: {e}")
        if config:
            WINDOW_SIZE = tuple(config.get("window_size", WINDOW_SIZE))
            MAXIMIZED = config.get("maximized", MAXIMIZED)
            FONT_SIZE = config.get("font_size", FONT_SIZE)
            FONT_NAME = config.get("font_name", FONT_NAME)
            APP_NAME = config.get("app_name", APP_NAME)
            REPEAT_DELAY = config.get("repeat_delay", REPEAT_DELAY)
            REPEAT_INTERVAL = config.get("repeat_interval", REPEAT_INTERVAL)
            CTRL_V_PASTE = config.get("ctrl_v_paste", CTRL_V_PASTE)
            enable_tray = config.get("enable_tray", enable_tray)
            TRAY_ICON_NORMAL = config.get("tray_icon_normal", TRAY_ICON_NORMAL)
            TRAY_ICON_UNREAD = config.get("tray_icon_unread", TRAY_ICON_UNREAD)
            TRAY_ICON_MENTION = config.get("tray_icon_mention", TRAY_ICON_MENTION)
            DEFAULT_PAIR = tuple(tuple(color) for color in config.get("default_color_pair", DEFAULT_PAIR))
            SYSTEM_COLORS = tuple(tuple(color) for color in config.get("color_palette", SYSTEM_COLORS))

    else:
        config = {
            "window_size": WINDOW_SIZE,
            "maximized": MAXIMIZED,
            "font_size": FONT_SIZE,
            "font_name": FONT_NAME,
            "app_name": APP_NAME,
            "repeat_delay": REPEAT_DELAY,
            "repeat_interval": REPEAT_INTERVAL,
            "ctrl_v_paste": CTRL_V_PASTE,
            "enable_tray": enable_tray,
            "tray_icon_normal": TRAY_ICON_NORMAL,
            "tray_icon_unread": TRAY_ICON_UNREAD,
            "TRAY_ICON_MENTION": TRAY_ICON_MENTION,
            "default_color_pair": DEFAULT_PAIR,
            "color_palette": SYSTEM_COLORS,
        }
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config {config_path}: {e}")


# constants
PGCURSES = True
KEY_MOUSE = 409
if sys.platform == "win32":
    KEY_BACKSPACE = 8
else:
    KEY_BACKSPACE = 263
KEY_DOWN = 258
KEY_UP = 259
KEY_LEFT = 260
KEY_RIGHT = 261
KEY_RESIZE = 419
KEY_DC = 330
KEY_HOME = 262
KEY_END = 360
BUTTON1_PRESSED = 2
BUTTON2_PRESSED = 64
BUTTON3_PRESSED = 2048
BUTTON4_PRESSED = 65536
BUTTON5_PRESSED = 2097152
A_STANDOUT = 65536
A_UNDERLINE = 131072
A_BOLD = 2097152
A_ITALIC = 2147483648
ALL_MOUSE_EVENTS = 268435455
COLORS = 255
COLOR_PAIRS = 1000000

run = True
screen = None
toggle_window = False
open_window = True
mouse_event = (0, 0, 0, 0, 0)
main_thread_queue = queue.Queue()
event_queue = queue.Queue()
color_map = [DEFAULT_PAIR] * (COLORS + 1)
fake_videoresize = pygame.event.Event(pygame.VIDEORESIZE, w=1, h=1)   # used to trigger redraw
icon = None
current_icon_index = None
emoji_font = None
font_regular = None
font_bold = None
font_italic = None
font_bold_italic = None

if sys.platform == "win32":
    emoji_font_name = "Segoe UI Emoji"
elif sys.platform == "darwin":
    emoji_font_name = "Apple Color Emoji"
else:
    emoji_font_name = "Noto Color Emoji"


def find_mono():
    """Find any monospace font"""
    fonts = pygame.font.get_fonts()

    mono_keywords = (
        "code",
        "mono",
        "console",
        "terminal",
        "courier",
        "consolas",
        "menlo",
        "fira",
        "jetbrains",
        "dejavusansmono",
    )
    for keyword in mono_keywords:
        for font in fonts:
            if keyword in font:
                return font


# tray stuff
def load_tray_image(path=None, color=None):
    """Load image from path, fallback to circle drawn with pillow"""
    if path and os.path.exists(os.path.expanduser(path)):
        return Image.open(os.path.expanduser(path)).convert("RGBA")
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.ellipse((16, 16, 48, 48), fill=color)
    return image


if have_tray:
    tray_icons = [load_tray_image(path, color) for path, color in (
        (TRAY_ICON_NORMAL, (200, 200, 200, 255)),
        (TRAY_ICON_UNREAD, (255, 120, 0, 255)),
        (TRAY_ICON_MENTION, (200, 30, 40, 255)),
    )]


def tray_toggle(icon=None, item=None):   # noqa
    """Toggle window state button"""
    global toggle_window
    toggle_window = True


def quit_app(icon, item):   # noqa
    """Quit app button"""
    global run
    run = False
    event_queue.put(None)
    icon.stop()


def set_tray_icon(icon_index):
    """
    Set tray icon, available icons:
    0 - default
    1 - unreads
    2 - mention
    """
    global icon, current_icon_index
    if not have_tray or current_icon_index == icon_index:
        return
    current_icon_index = icon_index
    if icon_index == 0:
        icon.icon = tray_icons[icon_index]
    if icon_index == 1 and (TRAY_ICON_UNREAD or not TRAY_ICON_NORMAL):
        icon.icon = tray_icons[icon_index]
    if icon_index == 2 and (TRAY_ICON_MENTION or not TRAY_ICON_NORMAL):
        icon.icon = tray_icons[icon_index]
    else:
        return


def tray_thread():
    """Thread that runs tray icon handler"""
    global icon
    menu = Menu(
        MenuItem("Toggle Window", tray_toggle),
        MenuItem(f"Quit {APP_NAME}", quit_app),
    )
    icon = Icon(f"{APP_NAME.lower()}-tray", tray_icons[0], APP_NAME, menu)
    icon.run()
    icon._listener.on_clicked = tray_toggle


# curses stuff
def clear_queue(target_queue):
    """Safely clears queue"""
    with target_queue.mutex:
        target_queue.queue.clear()
        target_queue.all_tasks_done.notify_all()
        target_queue.unfinished_tasks = 0


def xterm_to_rgb(x):
    """Convert xterm256 color to RGB tuple"""
    if x < 16:
        return SYSTEM_COLORS[x]
    if 16 <= x <= 231:
        x -= 16
        r = (x // 36) % 6
        g = (x // 6) % 6
        b = x % 6
        return (r * 51, g * 51, b * 51)
    if 232 <= x <= 255:
        gray = 8 + (x - 232) * 10
        return (gray, gray, gray)
    return (0, 0, 0)


def is_emoji(ch):
    """Check if character is emoji"""
    code = ord(ch)
    return (
        0x1F300 <= code <= 0x1F9FF or
        0x2600 <= code <= 0x27BF or
        0x2300 <= code <= 0x23FF or
        0x2B00 <= code <= 0x2BFF
    )


def map_key(event):
    """Map pygame keys to curses codes"""
    key = event.key
    if event.mod & pygame.KMOD_CTRL:   # Ctrl+Key
        if key == pygame.K_DOWN:
            if sys.platform == "win32":
                return 481
            return 534
        if key == pygame.K_UP:
            if sys.platform == "win32":
                return 480
            return 575
        if key == pygame.K_LEFT:
            if sys.platform == "win32":
                return 443
            return 554
        if key == pygame.K_RIGHT:
            if sys.platform == "win32":
                return 444
            return 569
        if key == pygame.K_SPACE:
            return 0
        if key == pygame.K_SLASH:
            return 31
        if key == pygame.K_BACKSPACE:
            return 8
    elif event.mod & pygame.KMOD_SHIFT:   # Shift+Key
        if key == pygame.K_DOWN:
            return 336
        if key == pygame.K_UP:
            return 337
        if key == pygame.K_LEFT:
            return 393
        if key == pygame.K_RIGHT:
            return 402
    elif event.mod & pygame.KMOD_ALT:   # Alt+Key
        if key == pygame.K_DOWN:
            return 532
        if key == pygame.K_UP:
            return 573
        if key == pygame.K_LEFT:
            return 552
        if key == pygame.K_RIGHT:
            return 567
    if key == pygame.K_BACKSPACE:
        return KEY_BACKSPACE
    if key == pygame.K_DOWN:
        return KEY_DOWN
    if key == pygame.K_UP:
        return KEY_UP
    if key == pygame.K_LEFT:
        return KEY_LEFT
    if key == pygame.K_RIGHT:
        return KEY_RIGHT
    if key == pygame.K_DELETE:
        return KEY_DC
    if key == pygame.K_RETURN:
        return 10
    return None


def is_paste_event(event):
    """Test if pressed keys are paste event"""
    if CTRL_V_PASTE:
        return event.key == pygame.K_v and (event.mod & pygame.KMOD_CTRL)
    return event.key == pygame.K_v and (event.mod & pygame.KMOD_CTRL) and (event.mod & pygame.KMOD_SHIFT)


def insstr(buffer, nlines, ncols, dirty_lines, dirty_lock, y, x, text, attr):
    """Internal function for curses.insstr"""
    lines = text.split("\n")
    len_lines = len(lines)
    with dirty_lock:
        for i in range(len_lines):
            line = lines[i]
            row = y + i
            if row >= nlines:
                break
            line_len = ncols - x
            if line_len <= 0:
                continue
            if line_len < len(line):
                min_len = line_len
            else:
                min_len = len(line)

            row_buffer = buffer[row]
            for j in range(min_len):
                row_buffer[x + j] = (line[j], attr)

            if i < len_lines - 1 and min_len < line_len:
                for j in range(min_len, line_len):
                    row_buffer[x + j] = (" ", attr)

            dirty_lines.add(row)


def render(screen, buffer, dirty_lines, dirty_lock, ncols, char_width, char_height, pxx, pxy, font_regular, font_bold, font_italic, font_bold_italic, emoji_font, color_map):
    """Render buffer onto screen"""
    with dirty_lock:
        for y in dirty_lines:
            row = buffer[y]
            i = 0
            draw_x = 0
            while i < ncols:
                ch, attr = row[i]
                flags = attr & 0xFFFF0000

                if is_emoji(ch):
                    px_x = draw_x * char_width
                    px_y = y * char_height
                    fg, bg = color_map[attr & 0xFFFF]
                    if flags & A_STANDOUT:
                        fg, bg = bg, fg
                    screen.fill(bg, (px_x + pxx, px_y + pxy, 2 * char_width, char_height))
                    try:
                        surface = emoji_font.render(ch, True, (255, 255, 255))
                        emoji = pygame.transform.smoothscale(surface, (char_height, char_height))
                    except pygame.error:
                        emoji = None
                    if emoji:
                        offset = px_x + (2 * char_width - char_height) // 2
                        screen.blit(emoji, (offset + pxx, px_y + pxy))
                    draw_x += 2   # emoji takes two cells visually
                    i += 2   # so push buffer line one extra char right
                    continue

                # collect characters with same attributes
                span_draw_x = draw_x
                text_buffer = []
                while i < ncols:
                    ch, attr2 = row[i]
                    if attr2 != attr or is_emoji(ch):
                        break
                    text_buffer.append(ch)
                    i += 1
                    draw_x += 1
                if not text_buffer:
                    i += 1
                    draw_x += 1
                    continue

                # render collected text
                text = "".join(text_buffer)
                fg, bg = color_map[attr & 0xFFFF]
                if flags & A_STANDOUT:
                    fg, bg = bg, fg
                px_x = span_draw_x * char_width
                px_y = y * char_height
                screen.fill(bg, (px_x + pxx, px_y + pxy, len(text) * char_width, char_height))
                if flags & A_BOLD:
                    if flags & A_ITALIC:
                        font = font_bold_italic
                    else:
                        font = font_bold
                elif flags & A_ITALIC:
                    if flags & A_ITALIC:
                        font = font_bold_italic
                    else:
                        font = font_italic
                else:
                    font = font_regular
                font.underline = bool(flags & A_UNDERLINE)
                font.render_to(screen, (px_x + pxx, px_y + pxy), text, fg)

        dirty_lines.clear()

# use cython if available, ~1.5 times faster insstr and ~1.3 times faster render
if importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.pgcurses"):
    from endcord_cython.pgcurses import insstr, render


class Window:
    """Pygame-Curses window class"""

    def __init__(self, nlines, ncols, begy, begx, parent_begx, parent_begy):
        self.begy, self.begx = begy + parent_begy, begx + parent_begx
        self.bgcolor = pygame.Color((0, 0, 0))
        self.input_buffer = []
        self.clock = pygame.time.Clock()
        self.nodelay_state = False

        rect = font_regular.get_rect(" ")
        self.char_width, self.char_height = rect.width, rect.height
        if ncols and nlines:
            self.ncols = ncols
            self.nlines = nlines
        else:
            self.ncols = screen.get_width() // self.char_width
            self.nlines = screen.get_height() // self.char_height
        self.pxy, self.pxx = self.begy * self.char_height, self.begx * self.char_width

        self.buffer = [[(" ", 0) for _ in range(self.ncols)]for _ in range(self.nlines)]
        self.dirty_lock = threading.RLock()
        self.dirty_lines = set()


    def derwin(self, nlines, ncols, begy, begx):
        """curses.derwin clone using pygame"""
        return Window(nlines, ncols, begy, begx, self.begx, self.begy)


    def screen_resize(self):
        """Internal function used to update screen dimensions on VIDEORESIZE event"""
        global screen
        self.ncols = screen.get_width()  // self.char_width
        self.nlines = screen.get_height() // self.char_height
        with self.dirty_lock:
            self.buffer = [[(" ", 0) for _ in range(self.ncols)]for _ in range(self.nlines)]
            self.dirty_lines = set()


    def getmaxyx(self):
        """curses.getmaxyx clone using pygame"""
        return (self.nlines, self.ncols)


    def getbegyx(self):
        """curses.getbegyx clone using pygame"""
        return (self.begy, self.begx)


    def insstr(self, y, x, text, attr=0):
        """curses.insstr clone using pygame"""
        insstr(
            buffer=self.buffer,
            nlines=self.nlines,
            ncols=self.ncols,
            dirty_lines=self.dirty_lines,
            dirty_lock=self.dirty_lock,
            y=y,
            x=x,
            text=text,
            attr=attr,
        )


    def insch(self, y, x, ch, color_id=0):
        """curses.insch clone using pygame, takes color id"""
        self.insstr(y, x, ch, color_id)


    def addstr(self, y, x, text, color_id=0):
        """curses.addstr clone using pygame, takes color id"""
        self.insstr(y, x, text, color_id)
        self.refresh()


    def addch(self, y, x, ch, color_id=0):
        """curses.addch clone using pygame, takes color id"""
        self.insstr(y, x, ch, color_id)


    def hline(self, y, x, ch, n, attr=0):
        """curses.hline clone using pygame, takes color id"""
        self.insstr(y, x, ch * n, attr)


    def vline(self, y, x, ch, n, attr=0):
        """curses.vline clone using pygame, takes color id"""
        for i in range(n):
            self.insch(y + i, x, ch, attr)


    def render(self):
        """Render buffer onto screen"""
        global screen
        render(
            screen,
            self.buffer,
            self.dirty_lines,
            self.dirty_lock,
            self.ncols,
            self.char_width,
            self.char_height,
            self.pxx,
            self.pxy,
            font_regular,
            font_bold,
            font_italic,
            font_bold_italic,
            emoji_font,
            color_map,
        )


    def clear(self):
        """curses.clear clone using pygame"""
        global screen
        screen.fill(self.bgcolor)


    def refresh(self):
        """curses.refresh clone using pygame"""
        main_thread_queue.put(self.render)
        main_thread_queue.put(pygame.display.update)


    def redrawwin(self):
        """curses.redrawwin clone using pygame"""
        main_thread_queue.put(self.render)


    def noutrefresh(self):
        """curses.noutrefresh clone using pygame"""
        main_thread_queue.put(self.render)


    def bkgd(self, ch, color_id):
        """curses.bkgd clone using pygame"""
        global screen
        ch = str(ch)[0]
        fg_color, bg_color = color_map[color_id]
        for y in range(self.nlines):
            for x in range(self.ncols):
                px_x = x * self.char_width
                px_y = y * self.char_height
                font_regular.render_to(screen, (px_x + self.pxx, px_y + self.pxy), ch, fg_color, bg_color)


    def nodelay(self, flag):
        """curses.nodelay clone using pygame"""
        self.nodelay_state = flag


    def timeout(self, value):
        """curses.delay clone using pygame, does nothing"""


    def do_key_press(self, event):
        """Map pygame keys to curses codes"""
        key = event.key
        char = event.unicode or ""
        mods = event.mod

        if mods & pygame.KMOD_SHIFT and key == pygame.K_RETURN:
            self.input_buffer.extend(b"\n")
            return 27
        if key == pygame.K_c and (mods & pygame.KMOD_CTRL):
            main_thread_queue.put(None)
            return -1
        if key == pygame.K_ESCAPE and not char:
            return 27

        if mods & pygame.KMOD_CTRL:
            if char and char.isalpha():
                return ord(char.lower()) - ord("a") + 1
            if char:
                return ord(char)

        if mods & pygame.KMOD_ALT and char:
            self.input_buffer.extend(char.encode("utf-8"))
            return 27

        if char:
            self.input_buffer.extend(char.encode("utf-8"))
            return None

        return None


    def getch(self):
        """curses.getch clone using pygame"""
        global mouse_event

        if self.input_buffer:
            return self.input_buffer.pop(0)

        while True:
            if self.input_buffer:
                return self.input_buffer.pop(0)

            if self.nodelay_state:
                try:
                    event = event_queue.get_nowait()
                except queue.Empty:
                    event = None
            else:
                event = event_queue.get()
            if event is None:
                return -1

            if event.type == pygame.KEYDOWN:
                key = map_key(event)
                if key is not None:
                    return key
                if is_paste_event(event):
                    if support_clipboard:
                        pasted = pyperclip.paste()
                        if pasted:   # bracket pasting
                            bracketed = "\x1b[200~" + pasted + "\x1b[201~"
                            self.input_buffer.extend(bracketed.encode("utf-8"))
                        return -1
                    logger.warning("Pyperclip must be installed in order to have clipboard support")
                code = self.do_key_press(event)
                if code is not None:
                    return code

            elif event.type == pygame.VIDEORESIZE:
                main_thread_queue.put(self.clear)
                self.screen_resize()
                return KEY_RESIZE

            elif event.type == pygame.MOUSEBUTTONDOWN:
                btnstate = 0
                if event.button == 1:
                    btnstate = BUTTON1_PRESSED
                elif event.button == 2:
                    btnstate = BUTTON2_PRESSED
                elif event.button == 3:
                    btnstate = BUTTON3_PRESSED
                elif event.button == 4:
                    btnstate = BUTTON4_PRESSED
                elif event.button == 5:
                    btnstate = BUTTON5_PRESSED
                x_pixel, y_pixel = event.pos
                mouse_event = (0, x_pixel // self.char_width, y_pixel // self.char_height, 0, btnstate)
                return KEY_MOUSE

            if self.nodelay_state:
                return -1


def getmouse():
    """curses.getmouse clone using pygame"""
    return mouse_event


def initscr():
    """curses.initscr clone using pygame"""
    global screen, font_regular, font_bold, font_italic, font_bold_italic, emoji_font
    pygame.display.init()
    pygame.font.init()
    pygame.freetype.init()
    pygame.key.set_repeat(REPEAT_DELAY, REPEAT_INTERVAL)
    pygame.display.set_caption(APP_NAME)
    screen = pygame.display.set_mode(WINDOW_SIZE, pygame.RESIZABLE)
    if MAXIMIZED:
        win = pg_Window.from_display_module()
        win.maximize()
    font_name = FONT_NAME
    if not font_name:
        font_name = find_mono()
    emoji_font = pygame.font.SysFont(emoji_font_name, FONT_SIZE)
    font_regular = pygame.freetype.SysFont(font_name, FONT_SIZE)
    font_bold = pygame.freetype.SysFont(font_name, FONT_SIZE, bold=True)
    font_italic = pygame.freetype.SysFont(font_name, FONT_SIZE, italic=True)
    font_bold_italic = pygame.freetype.SysFont(font_name, FONT_SIZE, bold=True, italic=True)
    font_regular.pad = font_bold.pad = font_italic.pad = font_bold_italic.pad = True
    return Window(0, 0, 0, 0, 0, 0)


def wrapper(func, *args, **kwargs):
    """curses.wrapper clone using pygame"""
    global screen, run, toggle_window, open_window
    window = initscr()

    if have_tray:
        if enable_tray:
            threading.Thread(target=tray_thread, daemon=True).start()
    elif tray_error:
        logger.error(f"Failed to start tray: {tray_error}")
    else:
        logger.warning("Pystray must be installed to have tray icon, on Linux additionaly pygobject")

    def user_thread():
        func(window, *args, **kwargs)
        main_thread_queue.put(None)
    threading.Thread(target=user_thread, daemon=True).start()
    last_window_size = WINDOW_SIZE

    while run:
        if toggle_window:
            toggle_window = False
            if screen:
                last_window_size = screen.get_size()
                pygame.display.quit()
                screen = None
                open_window = False
            else:
                screen = pygame.display.set_mode(last_window_size, pygame.RESIZABLE)
                open_window = True
                event_queue.put(fake_videoresize)   # used to trigger redraw

        if screen:
            for event in pygame.event.get():
                if event.type in (pygame.KEYDOWN, pygame.VIDEORESIZE, pygame.MOUSEBUTTONDOWN):
                    event_queue.put(event)
                    clear_queue(main_thread_queue)
                elif event.type == pygame.QUIT:
                    if have_tray and enable_tray:
                        clear_queue(main_thread_queue)
                        toggle_window = True
                    else:
                        event_queue.put(None)
                        return
            try:
                task = main_thread_queue.get(timeout=0.02)
                if not task:
                    break
                task()
            except queue.Empty:
                pass

    pygame.quit()


def doupdate():
    """curses.doupdate clone using pygame"""
    main_thread_queue.put(pygame.display.update)


def init_pair(pair_id, fg, bg):
    """curses.init_pair clone using pygame"""
    fg_rgb = DEFAULT_PAIR[0] if fg <= 0 else xterm_to_rgb(fg)
    bg_rgb = DEFAULT_PAIR[1] if bg <= 0 else xterm_to_rgb(bg)
    if pair_id >= len(color_map):
        missing = pair_id + 1 - len(color_map)
        color_map.extend([DEFAULT_PAIR] * missing)
    color_map[pair_id] = (fg_rgb, bg_rgb)



class error(Exception):   # noqa
    """curses.error clone using pygame, only inherits Exception class"""
    pass


def color_pair(color_id):
    """curses.color_pair clone using pygame, returns color id"""
    return color_id

def start_color():
    """curses.start_color clone using pygame, does nothing"""
    pass

def use_default_colors():
    """curses.use_default_colors clone using pygame, does nothing"""
    pass

def curs_set(x):
    """curses.curs_set clone using pygame, does nothing"""
    pass

def mousemask(x):
    """curses.mousemask clone using pygame, does nothing"""
    pass

def mouseinterval(x):
    """curses.mouseinterval clone using pygame, does nothing"""
    pass

def nocbreak():
    """curses.nocbreak clone using pygame, does nothing"""
    pass

def echo():
    """curses.echo clone using pygame, does nothing"""
    pass

def endwin():
    """curses.endwin clone using pygame"""
    pass

def def_prog_mode():
    """curses.def_prog_mode clone using pygame"""
    pass

def reset_prog_mode():
    """curses.reset_prog_mode clone using pygame"""
    pass


ACS_ULCORNER = "┌"
ACS_LLCORNER = "└"
ACS_URCORNER = "┐"
ACS_LRCORNER = "┘"
ACS_LTEE = "├"
ACS_RTEE = "┤"
ACS_BTEE = "┴"
ACS_TTEE = "┬"
ACS_HLINE = "─"
ACS_VLINE = "│"
ACS_PLUS = "┼"
ACS_S1 = "⎺"
ACS_S3 = "⎻"
ACS_S7 = "⎼"
ACS_S9 = "⎽"
ACS_DIAMOND = "◆"
ACS_DEGREE = "°"
ACS_PLMINUS = "±"
ACS_BULLET = "·"
ACS_LARROW = "←"
ACS_RARROW = "→"
ACS_DARROW = "↓"
ACS_UARROW = "↑"
ACS_BOARD = "▒"
ACS_LANTERN = "␋"
ACS_BLOCK = "▮"
ACS_LEQUAL = "≤"
ACS_GEQUAL = "≥"
ACS_PI = "π"
ACS_NEQUAL = "≠"
ACS_STERLING = "£"
