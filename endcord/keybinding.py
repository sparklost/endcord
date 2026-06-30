# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import curses
import logging
import os
import sys

logger = logging.getLogger(__name__)


MESSAGE = """ Press key combination, its code will be printed in terminal.
 Use this code to set this key combination in config.ini keybinding section.
 Some key combinations are reserved by terminal: Ctrl+C/I/J/M/Q/S/Z.
 Ctrl+Shift+Key combinations are not supported.
 Ctrl+C to exit."""

ARROW_MAP = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}
MODIFIER_MAP = (None, None, "S", "M", "M-S", "C", None, None)


def get_key(screen, backspace_code=127):
    """
    Read raw bytes from curses
    Return strings for normal text encoded like M-a, C-M-A, C-M-LEFT...
    Return (y, x, button, clicked) for mouse events
    Return RESIZE, SPACE, BACKSPACE, FOCUS_IN, FOCUS_OUT, DEL, TAB
    """
    first = screen.getch()
    if first == -1:
        return -1

    # ascii
    if 32 <= first <= 126:
        return chr(first)

    # escape sequences
    if first == 27:
        screen.nodelay(True)
        sequence_list = [27]
        while True:
            ch = screen.getch()
            if ch == -1:
                break
            sequence_list.append(ch)
        screen.nodelay(False)

        if len(sequence_list) > 1:
            sequence = "".join(chr(b) for b in sequence_list)

            # SGR 1006 mouse parsing
            if sequence.startswith("\x1b[<") and (sequence.endswith("m") or sequence.endswith("M")):
                clicked = sequence.endswith("M")
                try:
                    button_str, x_str, y_str = sequence.split("\x1b[<")[-1][:-1].split(";")
                    return (int(y_str) - 1, int(x_str) - 1, int(button_str), clicked)
                except (ValueError, IndexError):
                    return -1

            # arrows
            try:
                if sequence[-1] in ("A", "B", "C", "D"):
                    direction = ARROW_MAP[sequence[-1]]
                    if sequence in ("\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\x1bOA", "\x1bOB", "\x1bOC", "\x1bOD"):
                        return direction
                    if sequence.startswith("\x1b[1;") and len(sequence) >= 6:
                        modifier = MODIFIER_MAP[int(sequence[4])]
                        if modifier:
                            return f"{modifier}-{direction}"
            except (ValueError, IndexError):
                pass

            # delete key
            if sequence.startswith("\x1b[3"):
                if len(sequence) >= 6:
                    modifier = MODIFIER_MAP[int(sequence[4])]
                    if modifier:
                        return f"{modifier}-DEL"
                return "DEL"

            # terminal focus and bracket pasting
            if sequence == "\x1b[I":
                return "FOCUS_IN"
            if sequence == "\x1b[O":
                return "FOCUS_OUT"
            if sequence == "\x1b[200~":
                return "PASTE_START"
            if sequence == "\x1b[201~":
                return "PASTE_END"
            if sequence == "\x1b\n":
                return "M-ENTER"

            # 2-byte escape sequences
            if len(sequence_list) == 2:
                payload = sequence_list[1]
                if 1 <= payload <= 26:
                    return f"C-M-{chr(payload + 96)}"
                if payload == 0:
                    return "C-M-SPC"
                if payload == backspace_code:
                    return "M-BACKSPACE"
                if payload == 29:
                    return "C-M-]"
                if payload == 30:
                    return "C-M-^"
                if payload == 31:
                    return "C-M-/"
                if 32 <= payload <= 126:
                    char = chr(payload)
                    if char.isupper():
                        return f"M-S-{char.lower()}"
                    return f"M-{char}"

            return repr(sequence)

        return "ESC"

    # special
    if first == 0:
        return "C-SPACE"
    if first == backspace_code:
        return "BACKSPACE"
    if first == (127 if backspace_code == 8 else 8):
        return "C-BACKSPACE"
    if first == 9:
        return "TAB"
    if first == 10 or first == 13:
        return "ENTER"
    if first == 29:
        return "C-]"
    if first == 30:
        return "C-^"
    if first == 31:
        return "C-/"
    if first == curses.KEY_RESIZE:
        return "RESIZE"

    # ctrl+key
    if 1 <= first <= 26:
        return f"C-{chr(first + 96)}"

    # 8bit meta keys encoded as UTF-8 (for xterm)
    if backspace_code == 8 and first in (194, 195):
        screen.timeout(25)
        second = screen.getch()
        screen.timeout(-1)
        if second != -1 and (0x80 <= second <= 0xBF):
            high_bit_byte = ((first & 0x1F) << 6) | (second & 0x3F)
            original_ascii = high_bit_byte - 128
            if 1 <= original_ascii <= 26:
                return f"C-M-{chr(original_ascii + 96)}"
            if 32 <= original_ascii <= 126:
                char = chr(original_ascii)
                if char.isupper():
                    return f"M-S-{char.lower()}"
                return f"M-{char}"

    # wide characters
    if 0xC0 <= first <= 0xFF:
        buf = bytes([first])
        if 0xC0 <= first <= 0xDF:
            n_bytes = 2
        elif 0xE0 <= first <= 0xEF:
            n_bytes = 3
        elif 0xF0 <= first <= 0xF7:
            n_bytes = 4
        else:
            return first
        for _ in range(n_bytes - 1):
            ch = screen.getch()
            if ch == -1 or ch < 0x80 or ch > 0xBF:
                return first
            buf += bytes([ch])
        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError:
            return first

    return repr(first)


def get_key_code(screen):
    """Reads a key from curses and returns int for ascii, special and ctrl+key, str for utf-8 wide chars"""
    first = screen.getch()
    if first == -1:
        return -1

    # ascii, control, special keys
    if first < 0x80 or first > 0xFF:
        return first

    # utf-8 lead byte
    buf = bytes([first])
    if 0xC0 <= first <= 0xDF:
        n_bytes = 2
    elif 0xE0 <= first <= 0xEF:
        n_bytes = 3
    elif 0xF0 <= first <= 0xF7:
        n_bytes = 4
    else:
        return first

    # rest of utf-8 sequence
    for _ in range(n_bytes - 1):
        ch = screen.getch()
        if ch == -1 or ch < 0x80 or ch > 0xBF:
            return first
        buf += bytes([ch])

    # decode sequence
    try:
        return buf.decode("utf-8")
    except UnicodeDecodeError:
        return first


def decode_bstate_flag(flag_value):
    """Decode mouse bstate flag"""
    if flag_value <= 0:
        return (0, False)
    total_shift = flag_value.bit_length() - 1
    key_type = total_shift // 5
    pressed = (total_shift % 5) == 1
    if key_type >= 3:
        key_type += 61
    return (key_type, pressed)


def get_key_fallback(screen, backspace_code=127):
    """
    Read already parsed key from curses and return same output as get_key()
    Return strings for normal text encoded like M-a, C-M-A, C-M-LEFT...
    Return (y, x, button, clicked) for mouse events
    Return RESIZE, SPACE, BACKSPACE, FOCUS_IN, FOCUS_OUT, DEL, TAB
    """
    key = get_key_code(screen)
    if key == -1:
        return -1

    # ascii
    if 32 <= key <= 126:
        return chr(key)

    if key == curses.KEY_MOUSE:
        try:
            _, x, y, _, bstate = curses.getmouse()
            return (y, x, *decode_bstate_flag(bstate))
        except curses.error:
            return "ERR"

    # escape sequences
    if key == 27:
        screen.nodelay(True)   # terminal waits when esc is pressed, but not when sending escape sequence
        ch = get_key_code(screen)
        if ch == -1:
            screen.nodelay(False)
            return "ESC"
        sequence_list = [27, ch]
        while ch != -1:   # -1 means no key is pressed, 126 is end of escape sequence
            ch = get_key_code(screen)
            if ch == -1:
                break
            sequence_list.append(ch)
            if ch == 126 or ch == 27:
                break
        screen.nodelay(False)
        if len(sequence_list) == 2:
            payload = sequence_list[1]
            if payload == 10:
                return "M-ENTER"
            if 1 <= payload <= 26:
                return f"C-M-{chr(payload + 96)}"
            if payload == 0:
                return "C-M-SPC"
            if payload == (backspace_code):
                return "M-BACKSPACE"
            if payload == 29:
                return "C-M-]"
            if payload == 30:
                return "C-M-^"
            if payload == 31:
                return "C-M-/"
            if 32 <= payload <= 126:
                char = chr(payload)
                if char.isupper():
                    return f"M-S-{char.lower()}"
                return f"M-{char}"
        if sequence_list == [27, 91, 50, 48, 48, 126]:   # bracket paste start
            return "PASTE_START"
        if sequence_list == [27, 91, 50, 48, 49, 126]:   # bracket paste end
            return "PASTE_END"
        if sequence_list[-1] == 27:   # holding escape key
            return "ESC"
        return "ESC"

    if sys.platform == "win32":
        # alt+number
        if 407 <= key <= 416:
            return f"M-{key - 407}"
        # alt+letters
        if 352 <= key <= 442:
            return f"M-{chr(key - 320)}"
        arrow_map_fallback = {
            480: "C-UP", 481: "C-DOWN", 443: "C-LEFT", 444: "C-RIGHT",
            490: "M-UP", 491: "M-DOWN", 493: "M-LEFT", 492: "M-RIGHT",
        }
        if key == 504:
            return "M-BACKSPACE"
        if key == 527:
            return "C-DEL"
        if key == 478:
            return "M-DEL"
        if key == 0:
            return "C-/"
    else:
        arrow_map_fallback = {
            575: "C-UP", 534: "C-DOWN", 554: "C-LEFT", 569: "C-RIGHT",
            573: "M-UP", 532: "M-DOWN", 552: "M-LEFT", 567: "M-RIGHT",
            337: "S-UP", 336: "S-DOWN", 393: "S-LEFT", 402: "S-RIGHT",
        }

    # arrows
    if key == 259:
        return "UP"
    if key == 258:
        return "DOWN"
    if key == 260:
        return "LEFT"
    if key == 261:
        return "RIGHT"
    arrow_key = arrow_map_fallback.get(key)
    if arrow_key:
        return arrow_key

    # special
    if key == backspace_code:
        return "BACKSPACE"
    if key == ((127 if sys.platform == "win32" else 263) if backspace_code == 8 else 8):
        return "C-BACKSPACE"
    if key == 0:
        return "C-SPACE"
    if key == 330:
        return "DEL"
    if key == 528:
        return "C-DEL"
    if key == 526:
        return "M-DEL"
    if key == curses.KEY_RESIZE:
        return "RESIZE"
    if key == 590:
        return "FOCUS_IN"
    if key == 591:
        return "FOCUS_OUT"
    if key == 31:
        return "C-/"

    # ctrl+key
    if 1 <= key <= 26:
        if key == 9:
            return "TAB"
        if key == 10:
            return "ENTER"
        return f"C-{chr(key + 96)}"

    return repr(key)


KEY_ESCAPE = 1000
KEY_PASTE_START = 1001
KEY_PASTE_END = 1002
KEY_ENTER = 1003
KEY_RESIZE = 1004
KEY_FOCUS_IN = 1005
KEY_FOCUS_OUT = 1006
KEY_BACKSPACE = 1007
KEY_DEL = 1008
KEY_HOME = 1009
KEY_END = 1010
KEY_TAB = 1010


def build_key_map(keybindings):
    """Build fast lookup key map from keybindings dict"""
    key_map = {}
    for key_name, value in keybindings.items():
        if not value:
            continue
        triggers, action_id = value
        if key_name.startswith("media_"):
            continue
        if not triggers:
            continue
        if not isinstance(triggers, list) and not isinstance(triggers, tuple):
            triggers = (triggers, )
        for trigger in triggers:
            parts = []
            for part in trigger.split(" "):
                if len(part.split("-")[-1]) == 1:
                    parts.append(part[:-1] + part[-1].lower())
            new_trigger = " ".join(parts)
            if new_trigger:
                key_map[trigger] = action_id
            else:
                key_map[trigger] = action_id
    key_map["ESC"] = KEY_ESCAPE
    key_map["PASTE_START"] = KEY_PASTE_START
    key_map["PASTE_END"] = KEY_PASTE_END
    key_map["RESIZE"] = KEY_RESIZE
    key_map["FOCUS_IN"] = KEY_FOCUS_IN
    key_map["FOCUS_OUT"] = KEY_FOCUS_OUT
    key_map["BACKSPACE"] = KEY_BACKSPACE
    key_map["DEL"] = KEY_DEL
    key_map["TAB"] = KEY_TAB
    if "ENTER" in key_map:
        global KEY_ENTER
        KEY_ENTER = key_map["ENTER"]
    return key_map


def find_chainable(keybindings, command_bindings):
    """Find all first-parts of chained keybindings"""
    chainable = []
    for binding_value in keybindings.values():
        if not binding_value or not binding_value[0]:
            continue
        binding_group = binding_value[0]
        if isinstance(binding_group, str):
            split_binding = binding_group.split(" ")
            if len(split_binding) == 1:
                continue
            elif len(split_binding) > 2:
                logger.warn(f"Invalid keybinding: {binding_group}")
            chainable.append(split_binding[0])
        else:
            for binding in binding_group[0]:
                split_binding = binding.split(" ")
                if len(split_binding) == 1:
                    continue
                elif len(split_binding) > 2:
                    logger.warn(f"Invalid keybinding: {binding}")
                if split_binding[0] not in chainable:
                    chainable.append(split_binding[0])
    for binding in command_bindings:   # here read key instead value
        if isinstance(binding, str):
            split_binding = binding.split(" ")
            if len(split_binding) == 1:
                continue
            elif len(split_binding) > 2:
                logger.warn(f"Invalid keybinding: {binding}")
            if split_binding[0] not in chainable:
                chainable.append(split_binding[0])
    return chainable


def picker_internal(screen, keybindings, command_bindings, fallback):
    """Keybinding picker, prints last pressed key combination"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.init_pair(1, -1, -1)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    if sys.platform == "win32":
        fallback = True
    if not fallback:
        screen.keypad(False)
    screen.bkgd(" ", curses.color_pair(1))
    screen.addstr(1, 0, MESSAGE)
    command_bindings = [(val, key) for key, val in command_bindings.items()]
    try:
        backspace_code = 8 if (curses.erasechar() == 8 or "XTERM_VERSION" in os.environ or sys.platform == "win32") else 127
    except Exception:
        backspace_code = 127
    if fallback:
        backspace_code = 263 if backspace_code == 127 else backspace_code
    while True:
        if fallback:
            key_code = get_key_fallback(screen, backspace_code)
        else:
            key_code = get_key(screen, backspace_code)
        if key_code == curses.KEY_RESIZE:
            screen.addstr(1, 0, MESSAGE)
        text = f"Keybinding code: {key_code}"
        warning = ""
        for key, value in keybindings.items():
            if key_code in value:
                warning = f'Warning: same keybinding as "{key}"'
                break
        for key, value in command_bindings:
            if key_code == value and not warning:
                warning = f'Warning: same keybinding as for command "{key}"'
                break
        _, w = screen.getmaxyx()
        screen.addstr(7, 1, text + " " * (w - len(text)))
        screen.addstr(8, 1, warning + " " * (w - len(warning)))
        screen.refresh()


def picker(keybindings, command_bindings, fallback=False):
    """Keybinding picker, prints last pressed key combination"""
    try:
        curses.wrapper(picker_internal, keybindings, command_bindings, fallback)
    except curses.error as e:
        if str(e) != "endwin() returned ERR":
            sys.exit("Curses error")
