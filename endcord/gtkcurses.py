# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import importlib.util
import json
import logging
import os
import queue
import sys
import threading
import time
import traceback
from functools import lru_cache

# support for gvsbuild
if sys.platform == "win32":
    if "__compiled__" in globals():   # nuitka binary
        gtk_path = f"{os.path.dirname(sys.executable)}\\gtk"
        os.add_dll_directory(f"{gtk_path}\\bin")
        os.environ["Path"] = f"{gtk_path}\\bin;" + os.environ.get("Path", "")
    else:
        gtk_path = f"{os.path.dirname(os.path.abspath(sys.argv[0]))}\\.gtk"
        if not os.path.exists(gtk_path) and os.path.exists("C:\\gtk\\bin"):
            os.add_dll_directory("C:\\gtk\\bin")
        os.add_dll_directory(f"{gtk_path}\\bin")
        if "gtk\\bin" not in os.environ.get("PATH", ""):
            os.environ["Path"] = f"{gtk_path}\\bin;" + os.environ.get("Path", "")

import cairo
import gi

from endcord.wide_ranges import WIDE_RANGES

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo   # noqa
have_tray = False
tray_error = None
if importlib.util.find_spec("pystray"):
    have_tray = True
    if sys.platform == "linux":
        if importlib.util.find_spec("gi"):
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

logger = logging.getLogger(__name__)

# default config
WINDOW_SIZE = (900, 600)
MAXIMIZED = False
FONT_SIZE = 12
FONT_NAME = "Monospace"
GTK_DARK_THEME = True
BG_ALPHA = 1.0
BG_ALPHA_COLOR = 1.0
try:
    import __main__
    APP_NAME = getattr(__main__, "APP_NAME", "endcord")
except Exception:
    APP_NAME = "endcord"

CTRL_V_PASTE = False   # use Ctrl+V instead Ctrl+Shift+V to paste
ENABLE_TRAY = True
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
        config_path = os.path.join(path, APP_NAME.lower(), "gtkcurses.json")
    else:
        config_path = f"~/.config/{APP_NAME.lower()}/gtkcurses.json"
elif sys.platform == "win32":
    config_path = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME, "gtkcurses.json")
elif sys.platform == "darwin":
    config_path = f"~/Library/Application Support/{APP_NAME.lower()}/gtkcurses.json"
# config_path = os.environ.get("GTKCURSES_CONFIG")

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
            GTK_DARK_THEME = config.get("gtk_dark_theme", GTK_DARK_THEME)
            APP_NAME = config.get("app_name", APP_NAME)
            CTRL_V_PASTE = config.get("ctrl_v_paste", CTRL_V_PASTE)
            ENABLE_TRAY = config.get("enable_tray", ENABLE_TRAY)
            TRAY_ICON_NORMAL = config.get("tray_icon_normal", TRAY_ICON_NORMAL)
            TRAY_ICON_UNREAD = config.get("tray_icon_unread", TRAY_ICON_UNREAD)
            TRAY_ICON_MENTION = config.get("tray_icon_mention", TRAY_ICON_MENTION)
            BG_ALPHA = float(config.get("bg_alpha", BG_ALPHA))
            BG_ALPHA_COLOR = float(config.get("bg_alpha_color", BG_ALPHA_COLOR))
            DEFAULT_PAIR = tuple(tuple(color) for color in config.get("default_color_pair", DEFAULT_PAIR))
            SYSTEM_COLORS = tuple(tuple(color) for color in config.get("color_palette", SYSTEM_COLORS))

    else:
        config = {
            "window_size": WINDOW_SIZE,
            "maximized": MAXIMIZED,
            "font_size": FONT_SIZE,
            "font_name": FONT_NAME,
            "gtk_dark_theme": GTK_DARK_THEME,
            "app_name": APP_NAME,
            "ctrl_v_paste": CTRL_V_PASTE,
            "enable_tray": ENABLE_TRAY,
            "tray_icon_normal": TRAY_ICON_NORMAL,
            "tray_icon_unread": TRAY_ICON_UNREAD,
            "tray_icon_mention": TRAY_ICON_MENTION,
            "bg_alpha": BG_ALPHA,
            "bg_alpha_color": BG_ALPHA_COLOR,
            "default_color_pair": DEFAULT_PAIR,
            "color_palette": SYSTEM_COLORS,
        }
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config {config_path}: {e}")
if BG_ALPHA >= 1:
    BG_ALPHA = None
if BG_ALPHA is None or BG_ALPHA_COLOR >= 1:
    BG_ALPHA_COLOR = None

# constants
GTKCURSES = True
KEY_BACKSPACE = None
KEY_DOWN = None
KEY_UP = None
KEY_LEFT = None
KEY_RIGHT = None
KEY_RESIZE = None
KEY_MOUSE = None
A_STANDOUT = 0x00010000
A_UNDERLINE = 0x00020000
A_BOLD = 0x00200000
A_ITALIC = 0x80000000
A_EMOJI = 0x40000000   # bit 30 should be free
ALL_MOUSE_EVENTS = 0
REPORT_MOUSE_POSITION = 1
COLORS = 255
COLOR_PAIRS = 1000000

QUIT_TIMEOUT = 2000
ARROW_KEYS = {
    Gdk.KEY_Up: "UP",
    Gdk.KEY_Down: "DOWN",
    Gdk.KEY_Left: "LEFT",
    Gdk.KEY_Right: "RIGHT",
}

run = True
toggle_window = False
focused = True
cursor_type = 1
mouse_pos_reporting = 0
event_queue = queue.Queue()
color_map = [DEFAULT_PAIR] * (COLORS + 1)
icon = None
current_icon_index = None
nice_exit = False
is_quitting = False
gtk_window = None
use_tray = False


@lru_cache(maxsize=64)
def rgb_to_cairo(c):
    """Convert rgb 0-255 to cairo 0.0-1.0"""
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)


@lru_cache(maxsize=64)
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


def binary_search(codepoint, ranges):
    """Binary-search a sorted tuple of (start, end) ranges and return 1 if codepoint is inside any range, else 0"""
    low = 0
    high = len(ranges) - 1

    if codepoint < ranges[0][0] or codepoint > ranges[high][1]:
        return 0

    while low <= high:
        mid = (low + high) >> 1
        if codepoint < ranges[mid][0]:
            high = mid - 1
        else:
            low = mid + 1

    return high >= 0 and codepoint <= ranges[high][1]


def is_wch(ch):
    """Check if given character is wide"""
    codepoint = ord(ch)
    if 0x20 <= codepoint < 0x7f:
        return False
    return binary_search(codepoint, WIDE_RANGES)


# use cython if available, ~? times faster
try:
    from endcord_cython.formatter import is_wch
    # using same cached wide ranges from formatter, so no need to call init_wide_ranges() here
except ImportError:
    pass


def glib_log_bridge(domain, level, message, user_data=None):   # noqa
    """Logger bridge for gobject"""
    if level & GLib.LogLevelFlags.LEVEL_CRITICAL:
        logger.critical(f"[{domain}] {message}")
    if level & GLib.LogLevelFlags.LEVEL_ERROR:
        logger.error(f"[{domain}] {message}")
    elif level & GLib.LogLevelFlags.LEVEL_WARNING:
        logger.warning(f"[{domain}] {message}")
    else:
        logger.info(f"[{domain}] {message}")


def no_log(domain, level, message, user_data=None):   # noqa
    pass


for domain in ("GLib", "GLib-GIO", "Gtk", "Gdk"):
    GLib.log_set_handler(domain, GLib.LogLevelFlags.LEVEL_MASK | GLib.LogLevelFlags.FLAG_FATAL, glib_log_bridge, None)
GLib.log_set_handler(
    "libayatana-appindicator",
    GLib.LogLevelFlags.LEVEL_MASK | GLib.LogLevelFlags.FLAG_FATAL,
    glib_log_bridge if logger.getEffectiveLevel() == logging.DEBUG else no_log,
    None,
)


# tray stuff

def enable_tray():
    """Enable tray icon setup"""
    global use_tray
    use_tray = True
    if not icon:   # tray not yet initialized
        threading.Thread(target=tray_thread, daemon=True).start()


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


def set_tray_icon(icon_index):
    """
    Set tray icon, available icons:
    0 - default
    1 - unreads
    2 - mention
    """
    global icon, current_icon_index
    if not have_tray or icon is None or current_icon_index == icon_index:
        return

    current_icon_index = icon_index
    if icon_index == 0:
        icon.icon = tray_icons[0]
    elif icon_index == 1 and (TRAY_ICON_UNREAD or not TRAY_ICON_NORMAL):
        icon.icon = tray_icons[1]
    elif icon_index == 2 and (TRAY_ICON_MENTION or not TRAY_ICON_NORMAL):
        icon.icon = tray_icons[2]


def tray_toggle(icon=None, item=None):   # noqa
    """Toggle window button in tray"""
    global toggle_window
    toggle_window = True
    if gtk_window:
        GLib.idle_add(gtk_window.toggle_visibility)


def quit_app(icon=None, item=None):   # noqa
    """Exit button in tray"""
    global is_quitting, run

    def do_exit():
        global run
        run = False
        if have_tray and icon:
            try:
                icon.stop()
            except Exception:
                pass
        Gtk.main_quit()

    if is_quitting or not nice_exit:   # second click
        do_exit()
        return

    is_quitting = True   # first click
    event_queue.put("QUIT")
    event_queue.put("QUIT")   # to be sure

    elapsed = 0

    def poll_exit():
        # polling for 2s until main loop finishes
        nonlocal elapsed
        elapsed += 50
        if not run or elapsed >= QUIT_TIMEOUT:
            do_exit()
            return False
        return True

    GLib.timeout_add(50, poll_exit)


def tray_thread():
    """Thread that runs tray icon handler"""
    global icon
    time.sleep(1)   # delay for window to init
    menu = Menu(
        MenuItem("Toggle Window", tray_toggle),
        MenuItem(f"Quit {APP_NAME}", quit_app),
    )
    icon = Icon(f"{APP_NAME.lower()}-tray", tray_icons[0], APP_NAME, menu)
    icon.run()


def set_nice_exit(value):
    """Set fast exit to true/false"""
    global nice_exit
    nice_exit = value


# gtk stuff


class GtkTerminalWindow(Gtk.Window):
    """GTK window interface"""

    def __init__(self, curses_window):
        super().__init__()
        self.set_title(APP_NAME)

        # enable transparency
        if BG_ALPHA is not None:
            self.set_app_paintable(True)
            screen = self.get_screen()
            visual = screen.get_rgba_visual()
            if visual and screen.is_composited():
                self.set_visual(visual)

        # init gtk stuff
        self.curses_window = curses_window
        self.set_default_size(*WINDOW_SIZE)
        self.set_resizable(True)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", GTK_DARK_THEME)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.connect("draw", self.on_draw)
        self.drawing_area.connect("configure-event", self.on_configure)
        self.drawing_area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK,
        )
        self.drawing_area.connect("scroll-event", self.on_scroll)
        self.drawing_area.connect("button-press-event", self.on_button_press)
        self.drawing_area.connect("button-release-event", self.on_button_release)
        self.drawing_area.connect("motion-notify-event", self.on_motion_notify)
        self.add(self.drawing_area)
        self.connect("key-press-event", self.on_key_press)
        self.connect("delete-event", self.on_delete_event)
        self.connect("destroy", self.on_destroy)
        self.connect("focus-in-event", lambda *_: event_queue.put("FOCUS_IN"))
        self.connect("focus-out-event", lambda *_: event_queue.put("FOCUS_OUT"))
        self.font_desc = Pango.FontDescription.from_string(f"{FONT_NAME} {FONT_SIZE}")
        self.last_mouse_cell = (None, None)
        self.scroll_buffer = 0.0   # for touchpad

        # calculate font height and width and set it in curses class
        layout = self.drawing_area.create_pango_layout("▒")
        PangoCairo.context_set_resolution(layout.get_context(), 96)
        layout.set_font_description(self.font_desc)
        _, rect = layout.get_extents()
        self.char_width = rect.width / Pango.SCALE
        self.char_height = rect.height / Pango.SCALE
        self.curses_window.char_width = self.char_width
        self.curses_window.char_height = self.char_height
        self.layout = self.drawing_area.create_pango_layout("")
        PangoCairo.context_set_resolution(self.layout.get_context(), 96)


    def on_configure(self, widget, event):   # noqa
        """Window configure event"""
        if self.char_width <= 0 or self.char_height <= 0:
            return False
        ncols = int(event.width // self.char_width)
        nlines = int(event.height // self.char_height)
        # send resize event
        if ncols != self.curses_window.ncols or nlines != self.curses_window.nlines:
            self.curses_window.screen_resize(nlines, ncols)
            event_queue.put("RESIZE")

        return False


    def on_draw(self, widget, cr):   # noqa
        """Window draw event"""
        bg = color_map[0][1]

        if BG_ALPHA is not None:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(*rgb_to_cairo(bg), BG_ALPHA)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
        else:
            cr.set_source_rgb(*rgb_to_cairo(bg))
            cr.paint()

        layout = self.layout
        PangoCairo.update_layout(cr, layout)

        with self.curses_window.buffer_lock:
            for y in range(self.curses_window.nlines):
                row = self.curses_window.buffer[y]
                i = 0
                draw_x = 0
                while i < self.curses_window.ncols:
                    ch, attr = row[i]
                    flags = attr & 0xFFFF0000

                    # group consecutive characters with same attributes
                    span_text = []
                    span_attr = attr
                    span_start_x = draw_x
                    while i < self.curses_window.ncols and row[i][1] == span_attr:
                        ch = row[i][0]
                        span_text.append(ch)
                        i += 1
                        draw_x += 1
                        if flags & A_EMOJI:
                            if i < self.curses_window.ncols:
                                i += 1
                                draw_x += 1
                            break   # not grouping emoji because they have slightly wider font

                    text = "".join(span_text)
                    fg_idx = span_attr & 0xFFFF
                    if fg_idx >= len(color_map):
                        fg_idx = 0
                    fg_color, bg_color = color_map[fg_idx]
                    if flags & A_STANDOUT:
                        fg_color, bg_color = bg_color, fg_color
                    bg_px_width = (draw_x - span_start_x) * self.char_width
                    px_x = span_start_x * self.char_width
                    px_y = y * self.char_height

                    # draw bg
                    if bg_color != bg and text:
                        if BG_ALPHA_COLOR is not None:
                            cr.set_source_rgba(*rgb_to_cairo(bg_color), BG_ALPHA_COLOR)
                        else:
                            cr.set_source_rgb(*rgb_to_cairo(bg_color))
                        cr.rectangle(px_x, px_y, bg_px_width, self.char_height)
                        cr.fill()

                    # draw text
                    current_desc = self.font_desc.copy()
                    if flags & A_BOLD:
                        current_desc.set_weight(Pango.Weight.BOLD)
                    if flags & A_ITALIC:
                        current_desc.set_style(Pango.Style.ITALIC)
                    layout.set_font_description(current_desc)
                    layout.set_text(text, -1)
                    cr.set_source_rgb(*rgb_to_cairo(fg_color))
                    cr.move_to(px_x, px_y)
                    PangoCairo.show_layout(cr, layout)
                    if flags & A_UNDERLINE:
                        cr.set_line_width(1)
                        cr.move_to(px_x, px_y + self.char_height - 2)
                        cr.line_to(px_x + bg_px_width, px_y + self.char_height - 2)
                        cr.stroke()

            # draw cursor
            if cursor_type:
                cursor_y = self.curses_window.cursor_y
                cursor_x = self.curses_window.cursor_x
                if 0 <= cursor_y < self.curses_window.nlines and 0 <= cursor_x < self.curses_window.ncols:
                    cursor_px_y = cursor_y * self.char_height
                    cursor_px_x = cursor_x * self.char_width

                    # extract color and deal with emoji
                    ch, attr = self.curses_window.buffer[cursor_y][cursor_x]
                    flags = attr & 0xFFFF0000
                    fg_idx = attr & 0xFFFF
                    if fg_idx >= len(color_map):
                        fg_idx = 0
                    fg_color, bg_color = color_map[fg_idx]
                    if flags & A_STANDOUT:
                        fg_color, bg_color = bg_color, fg_color
                    cursor_width = self.char_width * (2 if (attr & A_EMOJI) else 1)

                    if cursor_type == 1:   # block with inverted collors
                        cursor_bg = fg_color  # Solid block gets text color
                        cursor_fg = bg_color  # Text gets background color
                        # 1. Draw solid block
                        cr.set_source_rgb(*rgb_to_cairo(cursor_bg))
                        cr.rectangle(cursor_px_x, cursor_px_y, cursor_width, self.char_height)
                        cr.fill()
                        if ch:
                            current_desc = self.font_desc.copy()
                            if flags & A_BOLD:
                                current_desc.set_weight(Pango.Weight.BOLD)
                            if flags & A_ITALIC:
                                current_desc.set_style(Pango.Style.ITALIC)
                            layout.set_font_description(current_desc)
                            layout.set_text(ch, -1)
                            cr.set_source_rgb(*rgb_to_cairo(cursor_fg))
                            cr.move_to(cursor_px_x, cursor_px_y)
                            PangoCairo.show_layout(cr, layout)
                    elif cursor_type == 2:   # bar
                        bar_w = max(2.0, self.char_width * 0.15)
                        cr.set_source_rgb(*rgb_to_cairo(fg_color))
                        cr.rectangle(cursor_px_x, cursor_px_y, bar_w, self.char_height)
                        cr.fill()

        return True


    def on_key_press(self, widget, event):   # noqa
        """Keypress events"""
        keyval = event.keyval
        state = event.state
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(state & Gdk.ModifierType.MOD1_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # clipboard paste
        if (ctrl and keyval == Gdk.KEY_v) if CTRL_V_PASTE else (ctrl and shift and keyval == Gdk.KEY_V):
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.request_text(self.on_clipboard_received, None)
            return True

        # modifier prefix
        modifiers = []
        if ctrl:
            modifiers.append("C")
        if alt:
            modifiers.append("M")
        if shift:
            modifiers.append("S")
        mod_prefix = "-".join(modifiers) + "-" if modifiers else ""

        # special
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            event_queue.put(mod_prefix + "ENTER")
            return True
        if keyval == Gdk.KEY_BackSpace:
            event_queue.put(mod_prefix + "BACKSPACE")
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            event_queue.put(mod_prefix + "TAB")
            return True
        if keyval == Gdk.KEY_Escape:
            event_queue.put(mod_prefix + "ESC")
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            event_queue.put(mod_prefix + "DEL")
            return True
        if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            event_queue.put(mod_prefix + "HOME")
            return True
        if keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            event_queue.put(mod_prefix + "END")
            return True
        if keyval in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
            event_queue.put(mod_prefix + "PGUP")
            return True
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down):
            event_queue.put(mod_prefix + "PGDN")
            return True
        if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            modifiers.append("C")
            modifiers = ["CTRL" if mod == "C" else "ALT" if mod == "M" else "SHIFT" for mod in ["C", "M", "S"] if mod in modifiers]
            event_queue.put("-".join(modifiers))
            return True
        if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
            modifiers.append("M")
            modifiers = ["CTRL" if mod == "C" else "ALT" if mod == "M" else "SHIFT" for mod in ["C", "M", "S"] if mod in modifiers]
            event_queue.put("-".join(modifiers))
            return True
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            modifiers.append("S")
            modifiers = ["CTRL" if mod == "C" else "ALT" if mod == "M" else "SHIFT" for mod in ["C", "M", "S"] if mod in modifiers]
            event_queue.put("-".join(modifiers))
            return True

        # arrows
        if keyval in ARROW_KEYS:
            direction = ARROW_KEYS[keyval]
            event_queue.put(mod_prefix + direction)
            return True

        # characters
        unicode_code = Gdk.keyval_to_unicode(keyval)
        if unicode_code != 0:
            ch = chr(unicode_code)
            if not ctrl and not alt:
                event_queue.put(ch)
                return True
            if ch == " ":
                event_queue.put(mod_prefix + "SPACE")
                return True
            char_mods = []
            if ctrl:
                char_mods.append("C")
            if alt:
                char_mods.append("M")
            if ch.isalpha() and (shift or ch.isupper()):
                char_mods.append("S")
                ch = ch.lower()
            prefix = "-".join(char_mods)
            event_queue.put(f"{prefix}-{ch}")
            return True

        return False


    def on_clipboard_received(self, clipboard, text, data):   # noqa
        """Clipboard paste event"""
        if text:
            event_queue.put(f"PASTE {text}")


    def on_button_press(self, widget, event):   # noqa
        """Mouse button press events"""
        x = int(event.x // self.char_width)
        y = int(event.y // self.char_height)
        btn = 0
        if event.button == 1:
            btn = 0
        elif event.button == 2:
            btn = 1
        elif event.button == 3:
            btn = 2
        event_queue.put((y, x, btn, True))
        return False


    def on_button_release(self, widget, event):   # noqa
        """Mouse button release events"""
        x = int(event.x // self.char_width)
        y = int(event.y // self.char_height)
        btn = 0
        if event.button == 1:
            btn = 0
        elif event.button == 2:
            btn = 1
        elif event.button == 3:
            btn = 2
        event_queue.put((y, x, btn, False))
        return False


    def on_scroll(self, widget, event):   # noqa
        """Mouse and touchpad scroll events"""
        x = int(event.x // self.char_width)
        y = int(event.y // self.char_height)

        # mouse
        if event.direction == Gdk.ScrollDirection.UP:
            event_queue.put((y, x, 64, True))   # up
            return True
        if event.direction == Gdk.ScrollDirection.DOWN:
            event_queue.put((y, x, 65, True))   # down
            return True

        # touchpad
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            success, _, dy = event.get_scroll_deltas()
            if success and dy != 0:
                self.scroll_buffer += dy
                while self.scroll_buffer <= -1.0:
                    event_queue.put((y, x, 64, True))   # up
                    self.scroll_buffer += 1.0
                while self.scroll_buffer >= 1.0:
                    event_queue.put((y, x, 65, True))   # down
                    self.scroll_buffer -= 1.0
            return True

        return False


    def on_motion_notify(self, widget, event):   # noqa
        """Mouse movement events"""
        if not mouse_pos_reporting:
            return False
        x = int(event.x // self.char_width)
        y = int(event.y // self.char_height)
        if 0 <= y < self.curses_window.nlines and 0 <= x < self.curses_window.ncols:
            current_cell = (y, x)
            if current_cell != self.last_mouse_cell:   # send only when mouse changes cell
                self.last_mouse_cell = current_cell
                event_queue.put((y, x, 32, True))
        return False


    def on_destroy(self, widget):   # noqa
        """Close window"""
        quit_app()


    def on_delete_event(self, widget, event):   # noqa
        """X button click"""
        global is_quitting, run

        if have_tray and use_tray and ENABLE_TRAY:
            self.hide()
            return True

        if is_quitting:   # second click
            run = False
            self.destroy()
            return False

        if not nice_exit:   # instant exit
            run = False
            self.destroy()
            return False

        is_quitting = True   # first click
        event_queue.put("QUIT")

        def delayed_destroy():
            global run
            run = False
            self.destroy()
            return False

        GLib.timeout_add(QUIT_TIMEOUT, delayed_destroy)
        return True


    def force_destroy(self):
        """Called by GLib timeout after 2 seconds"""
        self.destroy()
        return False


    def toggle_visibility(self):
        """Change window visibility"""
        if self.is_visible():
            self.hide()
        else:
            self.show()
            self.present()


def error_handler(message, unblock_event, report=False):
    """Spawn GTK window with the error and unblock the thread when closed"""
    if report:
        report = "\n\nYou can report this here:\nhttps://github.com/sparklost/endcord/issues"
    def build_and_show():   # noqa
        win = Gtk.Window()
        win.set_title(f"{APP_NAME} Error Report")
        win.set_default_size(800, 500)
        win.set_position(Gtk.WindowPosition.CENTER)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_cursor_visible(False)
        textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        font_desc = Pango.FontDescription("Monospace 11")
        textview.modify_font(font_desc)
        buf = textview.get_buffer()
        buf.set_text(f"{message}{report if report else ""}\n\n[Press any key to exit]")
        scroll.add(textview)
        win.add(scroll)
        def on_key_press(widget, event):   # noqa
            win.destroy()
            return True
        def on_destroy(widget):   # noqa
            unblock_event.set()
        win.connect("key-press-event", on_key_press)
        win.connect("destroy", on_destroy)
        win.show_all()
        return False
    GLib.idle_add(build_and_show)


# curses stuff

class Window:
    """GTK-Curses window class"""

    def __init__(self, nlines, ncols, begy, begx, parent=None):
        self.parent = parent
        self.begy = parent.begy + begy if parent else begy
        self.begx = parent.begx + begx if parent else begx
        self.ncols = ncols
        self.nlines = nlines
        self.nodelay_state = False

        if parent is None:   # root window
            self.buffer = [[(" ", 0) for _ in range(self.ncols)] for _ in range(self.nlines)]
            self.buffer_lock = threading.RLock()
            self.cursor_y, self.cursor_x = -1, -1
            self.root = self
        else:   # derwins
            self.buffer = parent.buffer
            self.buffer_lock = parent.buffer_lock
            self.root = parent.root


    def derwin(self, nlines, ncols, begy, begx): return Window(nlines, ncols, begy, begx, parent=self)   # noqa
    def getmaxyx(self): return (self.nlines, self.ncols)   # noqa
    def getbegyx(self): return (self.begy, self.begx)   # noqa
    def getmouse(self): return (0, 0, 0, 0, 0)   # noqa
    def doupdate(self): pass   # noqa
    def leaveok(self, x): pass   # noqa


    def screen_resize(self, nlines, ncols):
        """Internal function used to update screen dimensions"""
        self.ncols = ncols
        self.nlines = nlines
        with self.buffer_lock:
            self.buffer.clear()
            self.buffer.extend([[(" ", 0) for _ in range(self.ncols)] for _ in range(self.nlines)])


    def getch(self):   # noqa
        if self.nodelay_state:
            try:
                return event_queue.get_nowait()
            except queue.Empty:
                return -1
        else:
            return event_queue.get()


    def insstr(self, y, x, text, attr=0):   # noqa
        lines = text.split("\n")
        with self.buffer_lock:
            for i, line in enumerate(lines):
                if y + i >= self.nlines:
                    break
                line_len = self.ncols - x
                if line_len <= 0:
                    continue
                abs_y = self.begy + y + i
                abs_x = self.begx + x
                if abs_y >= len(self.buffer):
                    break
                row_buffer = self.buffer[abs_y]
                max_col = min(abs_x + line_len, len(row_buffer))
                src_idx = 0
                col_idx = abs_x
                source_line_len = len(line)
                while src_idx < source_line_len and col_idx < max_col:
                    ch = line[src_idx]
                    src_idx += 1
                    if is_wch(ch):
                        if src_idx < source_line_len and "\ufe00" <= line[src_idx] <= "\ufe0f":
                            ch += line[src_idx]
                            src_idx += 1
                        row_buffer[col_idx] = (ch, attr | A_EMOJI)
                        col_idx += 1
                        if col_idx < max_col:
                            row_buffer[col_idx] = (" ", attr)
                            col_idx += 1
                    else:
                        row_buffer[col_idx] = (ch, attr)
                        col_idx += 1
                if i < len(lines) - 1 and col_idx < max_col:
                    while col_idx < max_col:
                        row_buffer[col_idx] = (" ", attr)
                        col_idx += 1


    def chgat(self, y, x, num, attr=0):   # noqa
        if y < 0 or y >= self.nlines or x >= self.ncols:
            return
        x = max(x, 0)
        end = self.ncols if num < 0 else min(x + num, self.ncols)
        abs_y = self.begy + y
        abs_x = self.begx + x
        abs_end = self.begx + end
        with self.buffer_lock:
            if abs_y < len(self.buffer):
                row_buffer = self.buffer[abs_y]
                abs_end = min(abs_end, len(row_buffer))
                for i in range(abs_x, abs_end):
                    ch, _ = row_buffer[i]
                    row_buffer[i] = (ch, attr)


    def clear(self):   # noqa
        with self.buffer_lock:
            if self.parent is None:   # clear root
                self.buffer.clear()
                self.buffer.extend([[(" ", 0) for _ in range(self.ncols)] for _ in range(self.nlines)])
            else:   # clear derwin
                for y in range(self.nlines):
                    abs_y = self.begy + y
                    if abs_y < len(self.buffer):
                        row = self.buffer[abs_y]
                        for x in range(self.ncols):
                            abs_x = self.begx + x
                            if abs_x < len(row):
                                row[abs_x] = (" ", 0)
        self.refresh()


    def move(self, y, x):
        """Set relative cursor position (y, x) in this window"""
        self.root.cursor_y = self.begy + y
        self.root.cursor_x = self.begx + x
        self.refresh()


    def insch(self, y, x, ch, color_id=0): self.insstr(y, x, ch, color_id)   # noqa
    def addstr(self, y, x, text, color_id=0): self.insstr(y, x, text, color_id); self.refresh()   # noqa
    def addch(self, y, x, ch, color_id=0): self.insstr(y, x, ch, color_id)   # noqa
    def hline(self, y, x, ch, n, attr=0): self.insstr(y, x, ch * n, attr)   # noqa
    def vline(self, y, x, ch, n, attr=0):   # noqa
        for i in range(n):
            self.insch(y + i, x, ch, attr)
    def erase(self): self.clear()   # noqa
    def render(self): self.refresh()   # noqa
    def redrawwin(self): self.refresh()   # noqa
    def noutrefresh(self): self.refresh()   # noqa
    def refresh(self):    # noqa
        if gtk_window:
            GLib.idle_add(gtk_window.drawing_area.queue_draw)
    def nodelay(self, flag): self.nodelay_state = flag   # noqa
    def timeout(self, value): pass   # noqa
    def keypad(self, x): pass   # noqa
    def bkgd(self, ch, color_id): pass   # noqa


def init_pair(pair_id, fg, bg):
    """curses.init_pair clone"""
    fg_rgb = DEFAULT_PAIR[0] if fg <= 0 else xterm_to_rgb(fg)
    bg_rgb = DEFAULT_PAIR[1] if bg <= 0 else xterm_to_rgb(bg)
    if pair_id >= len(color_map):
        missing = pair_id + 1 - len(color_map)
        color_map.extend([DEFAULT_PAIR] * missing)
    color_map[pair_id] = (fg_rgb, bg_rgb)


def initscr():   # noqa
    global gtk_window
    window = Window(0, 0, 0, 0, parent=None)
    gtk_window = GtkTerminalWindow(window)
    return window


def wrapper(func, *args, **kwargs):   # noqa
    global gtk_window
    window = initscr()

    if have_tray and use_tray and ENABLE_TRAY:
        threading.Thread(target=tray_thread, daemon=True).start()
    elif tray_error:
        logger.error(f"Failed to start tray: {tray_error}")
    else:
        logger.warning("Pystray not installed")

    def user_thread():
        error_event = threading.Event()
        try:
            func(window, *args, **kwargs)
        except SystemExit as e:
            if e.code:
                exit_message = str(e.code)
                logger.warning(f"Exit with message: {exit_message}")
                error_handler(exit_message, error_event)
                error_event.wait()
        except Exception as e:
            error_traceback = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            logger.error(f"Exit with error:\n{error_traceback}")
            error_handler(error_traceback, error_event, report=True)
            error_event.wait()
        finally:
            def clean_quit():
                if gtk_window:
                    gtk_window.destroy()
                Gtk.main_quit()
                return False
            GLib.idle_add(clean_quit, priority=GLib.PRIORITY_HIGH)
            time.sleep(0.2)
            os._exit(0)   # failsafe if gtk.main fails to stop for any reason

    threading.Thread(target=user_thread, daemon=True).start()

    if MAXIMIZED:
        gtk_window.maximize()
    gtk_window.show_all()
    try:
        Gtk.main()
    finally:
        os._exit(0)   # forced exit in case there are some threads still running


def ungetch(ch):
    """Push character to input event queue, will try to convert number to character"""
    if not isinstance(ch, int):
        event_queue.put(ch)
    elif ch == 27:
        event_queue.put("ESC")
    else:
        try:
            event_queue.put(chr(ch))
        except (ValueError, OverflowError):
            event_queue.put(ch)


class error(Exception):   # noqa
    """Only inherits Exception class"""
    pass


def color_pair(color_id):
    """Only return color id"""
    return color_id


def curs_set(value):
    """Extra options: 0 - disable, 2 - block shape, 6 - bar shape"""
    global cursor_type
    if value == 2:
        cursor_type = 1
    elif value == 6:
        cursor_type = 2
    else:
        cursor_type = 0


def mousemask(value):
    """Only toggle report movement reporting"""
    global mouse_pos_reporting
    mouse_pos_reporting = value


def start_color(): pass   # noqa
def use_default_colors(): time.sleep(0.2)   # noqa
def mouseinterval(x): pass   # noqa
def nocbreak(): pass   # noqa
def echo(): pass   # noqa
def endwin(): pass   # noqa
def def_prog_mode(): pass   # noqa
def reset_prog_mode(): pass   # noqa
def doupdate(): pass   # noqa


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



# for terminal_utils.py

def query_terminal(query):   # noqa
    """Assuming it is kitty protocol check"""
    return "FAILED"   # set to OK to enable image drawing


def get_font_size():
    """Get font size in px"""
    return gtk_window.char_width, gtk_window.char_height


def get_size():
    """Get window size in character rows/columns"""
    return gtk_window.curses_window.nlines, gtk_window.curses_window.ncols


def read_key():
    """Blocking get key event"""
    return event_queue.get()


def draw_over_curses(text, y, x):
    """Currently unimplemented"""
    pass


def draw(text):
    """Currently unimplemented"""
    pass


def leave_tui(): pass   # noqa
def enter_tui(): pass   # noqa
