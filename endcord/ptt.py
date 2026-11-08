# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import ctypes
import ctypes.util
import logging
import os
import re
import sys
import threading
import time

from endcord import tui

MODIFIERS = {"C": "ctrl", "M": "alt", "S": "shift", "s": "super"}
KEY_NAMES = {"SPACE": "space", "ENTER": "Return", "RETURN": "Return", "ESC": "Escape", "ESCAPE": "Escape", "TAB": "Tab",
    "BACKSPACE": "BackSpace", "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right", "HOME": "Home", "END": "End",
    "PAGEUP": "Page_Up", "PGUP": "Page_Up", "PAGEDOWN": "Page_Down", "PGDN": "Page_Down", "INSERT": "Insert", "DELETE": "Delete"}
WIN_KEYS = {"space": 0x20, "Return": 0x0D, "Escape": 0x1B, "Tab": 0x09, "BackSpace": 0x08, "Up": 0x26, "Down": 0x28,
    "Left": 0x25, "Right": 0x27, "Home": 0x24, "End": 0x23, "Page_Up": 0x21, "Page_Down": 0x22, "Insert": 0x2D, "Delete": 0x2E}
WIN_MODIFIERS = {"ctrl": (0x11,), "shift": (0x10,), "alt": (0x12,), "super": (0x5B, 0x5C)}
X11_MODIFIERS = {"ctrl": "Control", "shift": "Shift", "alt": "Alt", "super": "Super"}
PORTAL_MODIFIERS = (("ctrl", "CTRL"), ("shift", "SHIFT"), ("alt", "ALT"), ("super", "LOGO"))
PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.GlobalShortcuts"
SHORTCUT_ID = "push-to-talk"
logger = logging.getLogger(__name__)


class PushToTalk:
    """Global push-to-talk hotkey"""

    def __init__(self, binding, on_change, app_id, dbus):
        self.on_change = on_change
        self.app_id = app_id
        self.dbus = dbus
        self.pressed = False
        self.trigger = binding   # portal can change this
        self.stop_event = threading.Event()
        self.cleanup = None
        self.thread = None

        # parse binding
        if not binding.strip():
            return
        *mods, key = binding.split("-")
        if not all(mod in MODIFIERS for mod in mods):
            return
        self.mods = {MODIFIERS[mod] for mod in mods}
        if key in KEY_NAMES:
            self.key = KEY_NAMES[key]
        elif re.fullmatch(r"F([1-9]|1\d|2[0-4])", key):   # Fn keys
            self.key = key
        elif len(key) == 1 and key.isascii() and key.isalnum():   # letters and digits
            self.key = key.lower()
        else:
            return

        # select backend
        if sys.platform == "win32":
            self.backend = "windows"
            setup = self.setup_windows
        elif sys.platform.startswith("linux"):
            if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
                if not tui.uses_gtkcurses:
                    self.backend = "disabled"
                    logger.warn("Wayland session detected but endcord is running in TUI mode, use GUI mode instead")
                    return
                self.backend = "portal"
                setup = self.setup_portal
            else:
                self.backend = "x11"
                setup = self.setup_x11
        else:
            logger.warn(f"unsupported platform: {sys.platform}")
        try:
            poll = setup()
        except Exception as e:
            logger.error(f"{self.backend} backend error: {e}")
        if not poll:
            return

        self.thread = threading.Thread(target=self.run, args=(poll,), daemon=True)
        self.thread.start()


    def stop(self):
        """Stop the polling thread and release the backend"""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)


    def run(self, poll):
        """Call poll function until stopped"""
        try:
            while not self.stop_event.is_set():
                poll()
        finally:
            if self.pressed:
                self.pressed = False
                self.on_change(False)
            if self.cleanup:
                self.cleanup()


    def setup_windows(self):
        """Prepare GetAsyncKeyState polling"""
        get_state = ctypes.windll.user32.GetAsyncKeyState
        get_state.restype = ctypes.c_short
        if self.key in WIN_KEYS:
            vk = WIN_KEYS[self.key]
        elif re.fullmatch(r"F\d+", self.key):
            vk = 0x6F + int(self.key[1:])
        else:
            vk = ord(self.key.upper())

        def poll():
            mods = {mod for mod, codes in WIN_MODIFIERS.items() if any(get_state(code) < 0 for code in codes)}
            key_down = get_state(vk) < 0
            if key_down and not self.pressed and mods == self.mods:
                self.pressed = True
                self.on_change(True)
            elif not key_down and self.pressed:
                self.pressed = False
                self.on_change(False)
            self.stop_event.wait(0.01)

        return poll


    def setup_x11(self):
        """Prepare XQueryKeymap polling through libX11"""
        lib = ctypes.util.find_library("X11")
        if not lib:
            logger.error("libX11 not found")
        xlib = ctypes.CDLL(lib)
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xlib.XStringToKeysym.restype = ctypes.c_ulong
        xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
        xlib.XKeysymToKeycode.restype = ctypes.c_ubyte
        xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xlib.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        display = xlib.XOpenDisplay(None)
        if not display:
            logger.error("cannot open X display. Is DISPLAY set?")

        key = xlib.XKeysymToKeycode(display, xlib.XStringToKeysym(self.key.encode()))
        if not key:
            xlib.XCloseDisplay(display)
            logger.errorr(f"key {self.key!r} not found in the keyboard layout")
            return
        mod_codes = {
            mod: (
                xlib.XKeysymToKeycode(display, xlib.XStringToKeysym(f"{name}_L".encode())),
                xlib.XKeysymToKeycode(display, xlib.XStringToKeysym(f"{name}_R".encode())),
            ) for mod, name in X11_MODIFIERS.items()
        }
        buffer = ctypes.create_string_buffer(32)
        self.cleanup = lambda: xlib.XCloseDisplay(display)

        def poll():
            """Query the keymap"""
            xlib.XQueryKeymap(display, buffer)
            mods = {mod for mod, codes in mod_codes.items() if any(bool(code and buffer.raw[code >> 3] & (1 << (code & 7))) for code in codes)}
            key_down = bool(key and buffer.raw[key >> 3] & (1 << (key & 7)))
            if key_down and not self.pressed and mods == self.mods:
                self.pressed = True
                self.on_change(True)
            elif not key_down and self.pressed:
                self.pressed = False
                self.on_change(False)
            self.stop_event.wait(0.01)
        return poll


    def setup_portal(self):
        """Bind the shortcut through the GlobalShortcuts portal and prepare polling"""
        from gi.repository import Gio, GLib
        variant = GLib.Variant
        tokens = 0
        context = GLib.MainContext()
        context.push_thread_default()
        connection = self.dbus if self.dbus else Gio.bus_get_sync(Gio.BusType.SESSION, None)
        subscriptions = []
        responses = {}
        session = None
        ticker = GLib.timeout_source_new(100)
        ticker.set_callback(lambda *args: True)   # noqa
        ticker.attach(context)

        def call(obj, interface, method, params, timeout=10):
            try:
                reply = connection.call_sync(PORTAL_BUS, obj, interface, method, params, None, Gio.DBusCallFlags.NONE, int(timeout * 1000), None)
            except GLib.Error as e:
                logger.error(f"{method} failed: {e}")
            return reply.unpack()

        def request(method, signature, args, options, timeout):
            nonlocal tokens
            token = variant("s", f"ptt_{os.getpid()}_{tokens}")
            tokens += 1
            body = variant(signature, (*args, {"handle_token": token, **options}))
            handle = call(PORTAL_PATH, PORTAL_IFACE, method, body)[0]
            deadline = time.monotonic() + timeout
            while handle not in responses:
                if time.monotonic() > deadline:
                    logger.error(f"{method}: no response from the portal after {timeout}s")
                    return
                context.iteration(True)
            code, response = responses.pop(handle)
            if code:
                logger.error(f"{method} refused or cancelled (code {code})")
            return response

        def on_response(conn, sender, obj, interface, member, params):   # noqa
            responses[obj] = params.unpack()

        def on_shortcut(conn, sender, obj, interface, member, params):   # noqa
            signal_session, shortcut_id = params.unpack()[:2]
            if signal_session == session and shortcut_id == SHORTCUT_ID:
                new_state = member == "Activated"
                if self.pressed != new_state:
                    self.pressed = new_state
                    self.on_change(new_state)

        try:
            subscriptions.append(connection.signal_subscribe(PORTAL_BUS, "org.freedesktop.portal.Request", "Response", None, None, Gio.DBusSignalFlags.NONE, on_response))
            if not self.dbus:
                try:
                    call(PORTAL_PATH, "org.freedesktop.host.portal.Registry", "Register", variant("(sa{sv})", (self.app_id, {})))
                except RuntimeError:
                    pass
            options = {"session_handle_token": variant("s", f"ptt{os.getpid()}")}
            response = request("CreateSession", "(a{sv})", (), options, 10)
            if response is None:
                return
            session = str(response["session_handle"])
            trigger = "+".join([name for mod, name in PORTAL_MODIFIERS if mod in self.mods] + [self.key])
            shortcut = (SHORTCUT_ID, {"description": variant("s", "Push to talk"), "preferred_trigger": variant("s", trigger)})
            response = request("BindShortcuts", "(oa(sa{sv})sa{sv})", (session, [shortcut], ""), {}, 120)
            for shortcut_id, props in response.get("shortcuts", []):
                if shortcut_id == SHORTCUT_ID and "trigger_description" in props:
                    self.trigger = props["trigger_description"]
            subscriptions.append(connection.signal_subscribe(PORTAL_BUS, PORTAL_IFACE, "Activated", None, None, Gio.DBusSignalFlags.NONE, on_shortcut))
            subscriptions.append(connection.signal_subscribe(PORTAL_BUS, PORTAL_IFACE, "Deactivated", None, None, Gio.DBusSignalFlags.NONE, on_shortcut))
        except Exception:
            for subscription in subscriptions:
                connection.signal_unsubscribe(subscription)
            ticker.destroy()
        finally:
            context.pop_thread_default()

        def close():
            if session:
                call(session, "org.freedesktop.portal.Session", "Close", None, timeout=1)
            for subscription in subscriptions:
                connection.signal_unsubscribe(subscription)
            ticker.destroy()
        self.cleanup = close

        def poll():
            context.iteration(True)
        return poll
