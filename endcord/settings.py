# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import curses
import sys
from ast import literal_eval

from endcord.config import update_config

if sys.platform == "win32":
    BACKSPACE = 8
else:
    BACKSPACE = curses.KEY_BACKSPACE

BUTTONS = ("Edit", "Reset", "Save", "Save and Exit", "Discard")
HINT = "Type or Enter to edit, Left/Right/Space for buttons, Esc to revert selected or exit"
COLOR_SELECTED = (233, 255)
COLOR_CURSOR = (255, 242)


def get_kind(ref):
    """Get setting kind from its reference value"""
    if isinstance(ref, bool):
        return "bool"
    if isinstance(ref, int):
        return "int"
    if isinstance(ref, (list, tuple, float, dict)):
        return "literal"
    return "text"


def to_text(value):
    """Convert value to single line text"""
    if value is None:
        return "None"
    if isinstance(value, str):
        return value.replace("\n", "\\n")
    return repr(value)


def get_edit_text(value, kind):
    """Get text to start editing from, none is empty and integer settings may hold bool or float"""
    if value is None:
        return ""
    if kind == "int" and isinstance(value, (bool, float)):
        value = int(value)
    return to_text(value)


def check_literal(text, ref):
    """Parse text as python literal, returns if it is valid and parsed value"""
    try:
        value = literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False, None
    if isinstance(ref, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool), value
    if isinstance(ref, (list, tuple)):
        return isinstance(value, (list, tuple)), value
    return isinstance(value, type(ref)), value


def valid_setting(text, ref, kind):
    """Check if typed text is valid for this kind of setting"""
    if kind == "int":
        try:
            int(text)
        except ValueError:
            return False
    elif kind == "literal":
        return check_literal(text, ref)[0]
    return True


def draw_border(screen, corners, color):
    """Draw border at screen borders with custom corners"""
    h, w = screen.getmaxyx()
    try:
        screen.hline(0, 1, curses.ACS_HLINE, w - 2, curses.color_pair(color))
        screen.hline(h - 1, 1, curses.ACS_HLINE, w - 2, curses.color_pair(color))
        screen.vline(1, 0, curses.ACS_VLINE, h - 2, curses.color_pair(color))
        screen.vline(1, w - 1, curses.ACS_VLINE, h - 2, curses.color_pair(color))
        screen.addstr(0, 0, corners[0], curses.color_pair(color))
        screen.addstr(0, w - 1, corners[2], curses.color_pair(color))
        screen.addstr(h - 1, 0, corners[1], curses.color_pair(color))
        screen.addstr(h - 1, w - 1, corners[3], curses.color_pair(color))
    except curses.error:   # errors randomly when resizing
        pass


def main_tui(screen, config, defaults, title):
    """Main settings editor tui"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    curses.mouseinterval(0)
    curses.init_pair(1, config["color_default"][0], config["color_default"][1])
    curses.init_pair(2, COLOR_SELECTED[0], COLOR_SELECTED[1])
    curses.init_pair(3, COLOR_CURSOR[0], COLOR_CURSOR[1])
    curses.init_pair(4, 196, COLOR_SELECTED[1])   # invalid text on selected row
    screen.bkgd(" ", curses.color_pair(1))

    values = {key: config.get(key, defaults[key]) for key in defaults}
    original = dict(values)
    changed = False
    bordered = not config["compact"]
    border_corners = config["border_corners"]
    x0 = int(bordered)
    names = list(defaults)
    key_w = len(max(names, key=len))
    selected = 0
    cursor = 0
    top = 0
    selected_button = 0
    editing = False
    text = ""

    while True:
        h, w = screen.getmaxyx()
        row_w = (w - 2 * x0) - 1
        list_y = x0 + 2
        button_y = h - 1 - x0
        list_h = max(button_y - list_y - 1, 1)
        top = min(top, selected)
        if selected >= top + list_h:
            top = selected - list_h + 1
        name = names[selected]
        ref = defaults[name] if defaults[name] is not None else values[name]
        kind = get_kind(ref)
        valid = not editing or valid_setting(text, ref, kind)

        screen.erase()
        screen.addstr(x0, x0, f" {title}: {HINT}"[:row_w+1].ljust(row_w+1), curses.color_pair(1))

        # draw settings
        for row in range(list_h):
            num = top + row
            if num >= len(names):
                break
            y = list_y + row
            row_name = names[num]
            prefix = f"  {"*" if values[row_name] != defaults[row_name] else " "} {row_name.ljust(key_w)} "
            if num == selected and editing:
                row_attr = curses.color_pair(2) if valid else curses.color_pair(4)
                prefix = ">" + prefix[1:]   # mark line being edited
                max_len = max(0, row_w - len(prefix) - 1)
                left = text[:cursor][-max_len:] if max_len > 0 else ""
                right = text[cursor:]
                line_str = (prefix + left + right)[:row_w].ljust(row_w)
                screen.addstr(y, x0, line_str, row_attr | curses.A_BOLD)
            else:
                if num == selected:
                    attr = curses.color_pair(2)
                else:   # unsaved lines are italic
                    attr = curses.color_pair(1) | (curses.A_ITALIC if values[row_name] != original[row_name] else 0)
                screen.addstr(y, x0, (prefix + to_text(values[row_name]))[:row_w].ljust(row_w), attr)

        # draw scrollbar
        if len(names) > list_h:
            thumb_size = max(list_h * list_h // len(names), 1)
            thumb_pos = top * (list_h - thumb_size) // (len(names) - list_h)
            for row in range(thumb_pos, thumb_pos + thumb_size):
                screen.addstr(list_y + row, x0 + row_w, "┃", curses.color_pair(1))

        # draw buttons
        buttons = [f"[{name.center(8)}]" for name in BUTTONS]
        left_space = w - len("  ".join(buttons))
        x = max(left_space // 2, 0)
        button_spans = []
        for num, button in enumerate(buttons):
            attr = curses.color_pair(1) | (curses.A_STANDOUT if num == selected_button else 0)
            screen.addstr(button_y, x, button, attr)
            button_spans.append((x, x + len(button)))
            x += len(button) + 2

        if bordered:
            draw_border(screen, border_corners, 1)
        screen.refresh()

        # input
        action = None
        target = selected
        key = screen.getch()
        printable = ""
        edited = False
        if key in (32, " "):
            printable = " " if editing else ""
        elif isinstance(key, int) and 33 <= key <= 126:
            printable = chr(key)
        elif isinstance(key, str) and key.startswith("PASTE_TEXT"):
            printable = key[11:].replace("\n", " ")
        elif isinstance(key, str) and len(key) == 1:
            printable = key

        # mouse
        if key == curses.KEY_MOUSE or isinstance(key, tuple):
            if isinstance(key, tuple):   # from gtkcurses
                my, mx, key_type, pressed = key
            else:
                try:
                    _, mx, my, _, state = curses.getmouse()
                except curses.error:
                    continue
                pressed = True
                if state & curses.BUTTON4_PRESSED:
                    key_type = 64
                elif state & getattr(curses, "BUTTON5_PRESSED", 0):   # just in case
                    key_type = 65
                elif state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
                    key_type = 0
                else:
                    key_type = None
            if pressed and key_type == 64:   # SCROLL UP
                target = max(selected - 3, 0)
            elif pressed and key_type == 65:   # SCROLL DOWN
                target = min(selected + 3, len(names) - 1)
            elif pressed and key_type == 0:   # LEFT CLICK
                if list_y <= my < list_y + list_h and top + my - list_y < len(names):
                    target = top + my - list_y
                    action = 0   # EDIT
                elif my == button_y:
                    for num, (start, end) in enumerate(button_spans):
                        if start <= mx < end:
                            selected_button = num
                            action = num

        # keyboard
        elif key in (27, "ESC"):
            if editing:
                if valid:
                    editing = False
                else:   # revert typing
                    text = get_edit_text(values[name], kind)
                    cursor = len(text)
            elif values[name] != defaults[name]:   # revert setting
                values[name] = defaults[name]
            else:
                action = 4   # DISCARD
        elif key == "C-c":
            action = 4   # DISCARD
        elif key in (10, "ENTER"):
            action = 0   # EDIT
        elif key in (curses.KEY_UP, "UP"):
            target = max(selected - 1, 0)
        elif key in (curses.KEY_DOWN, "DOWN"):
            target = min(selected + 1, len(names) - 1)
        elif key in (curses.KEY_LEFT, "LEFT"):
            if editing:
                cursor = max(cursor - 1, 0)
            else:
                selected_button = max(selected_button - 1, 0)
        elif key in (curses.KEY_RIGHT, "RIGHT"):
            if editing:
                cursor = min(cursor + 1, len(text))
            else:
                selected_button = min(selected_button + 1, len(BUTTONS) - 1)
        elif key in (32, " ") and not editing:
            action = selected_button
        elif editing and key in (BACKSPACE, 127, "BACKSPACE"):
            if cursor > 0:
                text = text[:cursor - 1] + text[cursor:]
                cursor -= 1
                edited = True
        elif editing and key in (curses.KEY_DC, "DEL"):
            text = text[:cursor] + text[cursor + 1:]
            edited = True
        elif printable and kind != "bool":
            if editing:
                base = text
                pos = cursor
            else:
                base = get_edit_text(values[name], kind)
                pos = len(base)
            add = printable
            if kind == "int":
                add = "".join(ch for ch in add if ch.isdigit() or ch in ("-", "."))
            if add:
                text = base[:pos] + add + base[pos:]
                cursor = pos + len(add)
                editing = True   # typing starts editing
                edited = True

        # store settings
        if edited and valid_setting(text, ref, kind):   # dont store invalid setting
            if kind == "int":
                values[name] = int(text)
            elif kind == "literal":
                values[name] = check_literal(text, ref)[1]
            elif text == "None" or (not text and defaults[name] is None):
                values[name] = None
            else:
                values[name] = text.replace("\\n", "\n")

        # update states when moved
        if target != selected:
            if valid:
                selected = target
                name = names[selected]
                ref = defaults[name] if defaults[name] is not None else values[name]
                kind = get_kind(ref)
                editing = False
                valid = True
            else:   # dont leave invalid values
                action = None

        # execute actions
        if action == 0:   # EDIT
            if kind == "bool":
                values[name] = not values[name]
            elif editing:
                if valid:
                    editing = False
            else:
                editing = True
                text = get_edit_text(values[name], kind)
                cursor = len(text)
        elif action == 1:   # RESET
            values[name] = defaults[name]
            editing = False
        elif action in (2, 3):   # SAVE, SAVE+EXIT
            for key in defaults:
                if values[key] != original[key]:
                    update_config(config, key, repr(values[key]))   # "None" string is parsed back to None
                    original[key] = values[key]
                    changed = True
        if action in (3, 4):   # SAVE+EXIT, DISCARD
            break

    screen.clear()
    screen.refresh()
    return changed


def settings(config, defaults, title="Settings"):
    """Show settings editor"""
    try:
        changed = curses.wrapper(main_tui, config, defaults, title)
    except curses.error as e:
        if str(e) == "addwstr() returned ERR":
            sys.exit("There was no enough space to render the TUI, please increase the terminal size")
        return config, False
    return config, changed
