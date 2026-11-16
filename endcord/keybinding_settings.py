# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import curses
import os
import sys
from ast import literal_eval
from configparser import ConfigParser

from endcord import defaults, keybinding
from endcord.config import save_config

uses_gtkcurses = hasattr(curses, "GTKCURSES")

SHEETS = {"main": "keybindings", "macro": "command_bindings", "vim": "vim_mode_bindings"}
DESCRIPTIONS = {
    "main": ("Standard keybindings: single bining or chain.", "" if uses_gtkcurses else "Some key combinations are reserved by terminal: Ctrl+C/I/J/M/Q/S/Z."),
    "macro": ("Macros: one or two key combinations that run commands", "Add creates new macro. Reset restores default macro or deletes custom one."),
    "vim": ("Vim mode keybindings: single key, letter or capital letter.", "They are used only when vim_mode is enabled."),
}
HINT = "Enter to bind, Left/Right/Space for buttons, Esc to revert selected or discard"
BIND_HINTS = {
    "main": "Press key combination. Esc first - disable, Esc after - store",
    "macro": "Press key combination. Esc first - keep, Esc after - store",
    "vim": "Press key or Shift+Key. Esc - disable",
}
BUTTONS = ("Main", "Macro", "Vim", "Edit", "Reset", "Add", "Save", "Save and Exit", "Discard")
TEXT_HINT = "Type or paste command, Enter or Esc when done"
IGNORED_KEYS = ("RESIZE", "FOCUS_IN", "FOCUS_OUT", "ERR")   # PASTE is already ignored
COLOR_SELECTED = (233, 255)
COLOR_CURSOR = (255, 242)


def make_rows(path, sheet):
    """Create rows of one sheet from config file and defaults"""
    rows = []
    if sheet == "macro":
        default = defaults.command_bindings
        for binding, command in load_sheet(path, SHEETS[sheet], default, True).items():
            new = (binding, str(command))
            rows.append({"key": None, "new": new, "default": (binding, default[binding]) if binding in default else None, "saved": new})
        return rows

    source = defaults.keybindings if sheet == "main" else defaults.vim_mode_bindings
    default = {key: value[0] for key, value in source.items() if value[1] is not None}
    for key, raw_value in load_sheet(path, SHEETS[sheet], default, False).items():
        value = raw_value
        if isinstance(raw_value, (list, tuple)):
            value = next((item for item in raw_value if item), None)
        if value in (None, "None", ""):
            value = None
        else:
            value = value if isinstance(value, str) else default[key]
        rows.append({"key": key, "new": (value, None), "default": (default[key], None), "saved": (value, None)})
    return rows


def find_collisions(bindings):
    """Find colliding bindings and first parts of chained bindings"""
    seen = set()
    collided = set()
    first_parts = set()
    for binding in bindings:
        if binding in seen:
            collided.add(binding)
        seen.add(binding)
        if " " in binding:
            first_parts.add(binding.split(" ")[0])
    return collided | (seen & first_parts)


def load_sheet(path, section, default, merge):
    """Load bindings from config section, fallback to default value"""
    sheet = dict(default)
    parser = ConfigParser(interpolation=None)
    if merge:
        parser.optionxform = str   # case sensitive for macros
    if os.path.exists(os.path.expanduser(path)):
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            parser.read_file(f)
    if parser.has_section(section):
        for key, raw in parser.items(section):
            if key in default or merge:
                try:
                    sheet[key] = literal_eval(raw)
                except (ValueError, SyntaxError):
                    sheet[key] = raw
    return sheet


def save_sheet(path, sheet, rows, deleted):
    """Write changed bindings of one sheet to config file section"""
    section = SHEETS[sheet]
    data = {}
    for row in rows:
        if row["saved"] == row["new"]:
            continue
        binding, command = row["new"]
        if sheet == "macro":
            if row["saved"] and row["saved"][0] != binding:
                deleted.append(row["saved"][0])
            if binding:
                data[binding] = command
        else:
            data[row["key"]] = binding
        row["saved"] = row["new"]
    path = os.path.expanduser(path)
    if deleted and os.path.exists(path):
        parser = ConfigParser(interpolation=None)
        parser.optionxform = str
        with open(path, "r", encoding="utf-8") as f:
            parser.read_file(f)
        if parser.has_section(section):
            for binding in deleted:
                parser.remove_option(section, binding)
            with open(path, "w", encoding="utf-8") as f:
                parser.write(f)
    if data:
        save_config(path, data, section, lower=False)
    return bool(data or deleted)


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


def main_tui(screen, config):
    """Main keybinding editor tui, returns True if any binding was saved"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    curses.init_pair(1, config["color_default"][0], config["color_default"][1])
    curses.init_pair(2, COLOR_SELECTED[0], COLOR_SELECTED[1])
    curses.init_pair(3, COLOR_CURSOR[0], COLOR_CURSOR[1])
    curses.init_pair(4, 196, COLOR_SELECTED[1])   # colliding binding and selected
    curses.init_pair(5, 196, config["color_default"][1])   # colliding binding
    curses.init_pair(6, 208, config["color_default"][1])   # no binding
    screen.bkgd(" ", curses.color_pair(1))

    path = config["config_path"]
    bordered = not config["compact"]
    border_corners = config["border_corners"]

    # select parser
    try:
        backspace_code = 8 if (curses.erasechar() == 8 or "XTERM_VERSION" in os.environ or sys.platform == "win32") else 127
    except Exception:
        backspace_code = 127
    fallback_keybinding_parser = (config["fallback_keybinding_parser"] or sys.platform == "win32") and not uses_gtkcurses
    if fallback_keybinding_parser:
        screen.keypad(True)
        get_key = keybinding.get_key_fallback
        backspace_code = 263 if backspace_code == 127 else 8
    elif uses_gtkcurses:
        curses.set_nice_exit(True)
        get_key = keybinding.get_key_passthrough
    else:
        screen.keypad(False)
        get_key = keybinding.get_key

    # initial values
    sheets = {name: make_rows(path, name) for name in SHEETS}
    deleted_macros = []   # removed macros
    changed = False
    x0 = int(bordered)
    sheet = "main"
    selected = 0
    top = 0
    selected_button = 0
    mode = None   # None, "bind" or "text"
    chain = []
    text = ""
    cursor = 0

    while True:
        h, w = screen.getmaxyx()
        row_w = (w - 2 * x0) - 1
        list_y = x0 + 3
        hint_y = h - 2 - x0
        button_y = h - 1 - x0
        list_h = max(hint_y - list_y - 1, 1)
        rows = sheets[sheet]
        selected = min(max(selected, 0), len(rows) - 1)
        top = min(top, selected)
        if selected >= top + list_h:
            top = selected - list_h + 1
        visible_buttons = [btn for btn in BUTTONS if btn != "Add" or sheet == "macro"]
        selected_button = min(selected_button, len(visible_buttons) - 1)
        labels = [item["key"] or item["new"][0] or "" for item in rows]
        label_w = len(max(labels, key=len))

        # check for collisions
        active_keys = []
        for sheet_name in (["vim"] if sheet == "vim" else ["main", "macro"]):
            for item in sheets[sheet_name]:
                current_key = item["new"][0]
                if current_key:
                    active_keys.append(current_key)
        collisions = find_collisions(active_keys)

        screen.erase()
        for num, line in enumerate(DESCRIPTIONS[sheet]):
            screen.addstr(x0 + num, x0, f" {line}"[:row_w + 1].ljust(row_w + 1), curses.color_pair(1))

        # draw bindings
        for row in range(list_h):
            num = top + row
            if num >= len(rows):
                break
            y = list_y + row
            item = rows[num]
            binding, command = item["new"]
            if num == selected and mode == "bind":
                binding_text = " ".join(chain) + " _"
            else:
                binding_text = "None" if not binding else binding
            label = binding_text if sheet == "macro" else item["key"]   # swapped for macros
            value = command if sheet == "macro" else binding_text
            prefix = f" {"*" if item["new"] != item["default"] else " "} {label.ljust(label_w)} "
            if num == selected and mode:   # mark line being edited
                prefix = ">" + prefix[1:]
            if num == selected:
                attr = curses.color_pair(4 if binding in collisions else 2)
            else:   # unsaved lines are italic
                attr = curses.color_pair(5 if binding in collisions else 6 if item["new"][0] is None else 1)
                attr |= curses.A_ITALIC if item["new"] != item["saved"] else 0
            if num == selected and mode == "text":
                max_len = max(0, row_w - len(prefix) - 1)
                left = text[:cursor][-max_len:] if max_len > 0 else ""
                right = text[cursor:]
                screen.addstr(y, x0, (prefix + left + right)[:row_w].ljust(row_w), attr)
                cursor_x = x0 + len(prefix) + len(left)
                if cursor_x < x0 + row_w:
                    screen.addstr(y, cursor_x, right[:1] or " ", curses.color_pair(3))
                continue
            screen.addstr(y, x0, (prefix + value)[:row_w].ljust(row_w), attr)

        # draw scrollbar
        if len(rows) > list_h:
            thumb_size = max(list_h * list_h // len(rows), 1)
            thumb_pos = top * (list_h - thumb_size) // (len(rows) - list_h)
            for row in range(thumb_pos, thumb_pos + thumb_size):
                screen.addstr(list_y + row, x0 + row_w, "┃", curses.color_pair(1))

        # draw hint
        hint = BIND_HINTS[sheet] if mode == "bind" else TEXT_HINT if mode == "text" else HINT
        screen.addstr(hint_y, x0, f"{hint}"[:row_w + 1].center(row_w + 1), curses.color_pair(1))

        # draw buttons
        wide_buttons = [f"[{name.center(8)}]" for name in visible_buttons]
        narrow_buttons = [f"[{name}]" for name in visible_buttons]
        separator = "  "
        if len(separator.join(wide_buttons)) <= row_w:
            buttons = wide_buttons
        else:
            buttons = narrow_buttons
            separator = " "
        x = max((row_w - len(separator.join(buttons))) // 2, 0)
        button_spans = []
        for num, button in enumerate(buttons):
            attr = curses.color_pair(1)
            if num == selected_button:
                attr |= curses.A_STANDOUT
            if visible_buttons[num].lower() == sheet:   # active sheet
                attr |= curses.A_BOLD | curses.A_UNDERLINE
            screen.addstr(button_y, x, button, attr)
            button_spans.append((x, x + len(button)))
            x += len(button) + len(separator)

        if bordered:
            draw_border(screen, border_corners, 1)
        screen.refresh()

        # input
        action = None
        target = selected
        current_row = rows[selected] if rows else None
        key = get_key(screen, backspace_code)
        if key == -1:
            continue

        # mouse
        if isinstance(key, tuple):   # mouse
            my, mx, key_type, pressed = key
            if mode is None and pressed:
                if key_type == 64:   # SCROLL UP
                    target = max(selected - 3, 0)
                elif key_type == 65:   # SCROLL DOWN
                    target = min(selected + 3, len(rows) - 1)
                elif key_type == 0:   # LEFT CLICK
                    if list_y <= my < list_y + list_h and top + my - list_y < len(rows):
                        target = top + my - list_y
                        action = "Edit"
                    elif my == button_y:
                        for num, (start, end) in enumerate(button_spans):
                            if start <= mx < end:
                                selected_button = num
                                action = visible_buttons[num]

        # keyboard in bind mode
        elif mode == "bind":
            stop = False
            binding = None
            if key == "ESC":
                stop = True
                if chain:   # store typed combination
                    binding = " ".join(chain)
                elif sheet == "macro":  # edit command only
                    binding = current_row["new"][0]
            elif not chain and len(key) == 1 and sheet != "vim":
                continue
            elif isinstance(key, str) and key not in IGNORED_KEYS and not key.startswith("PASTE "):
                chain.append(key)
                if len(chain) == 2 or sheet == "vim":
                    stop = True
                    binding = " ".join(chain)
            if stop:
                if sheet == "macro" and not binding:   # canceled new macro
                    rows.pop(selected)
                    mode = None
                else:
                    current_row["new"] = (binding, current_row["new"][1])
                    mode = None
                    if sheet == "macro":   # next to command edit
                        mode = "text"
                        text = current_row["new"][1]
                        cursor = len(text)

        # keyboard in text mode
        elif mode == "text":
            new_text = ""
            if key in ("ESC", "ENTER"):
                mode = None
            elif key == "LEFT":
                cursor = max(cursor - 1, 0)
            elif key == "RIGHT":
                cursor = min(cursor + 1, len(text))
            elif key == "BACKSPACE":
                if cursor > 0:
                    text = text[:cursor - 1] + text[cursor:]
                    cursor -= 1
            elif key == "DEL":
                text = text[:cursor] + text[cursor + 1:]
            elif isinstance(key, str) and key.startswith("PASTE "):
                new_text = key[6:].replace("\n", " ")
            elif isinstance(key, str) and len(key) == 1:
                new_text = key
            if new_text:
                text = text[:cursor] + new_text + text[cursor:]
                cursor += len(new_text)
            if text != current_row["new"][1]:
                current_row["new"] = (current_row["new"][0], text)

        # keyboard common
        elif key == "ESC":
            if current_row and current_row["new"] != current_row["default"] and (current_row["default"] or sheet != "macro"):   # revert setting
                current_row["new"] = current_row["default"]
            else:
                action = "Discard"
        elif key == "C-c":
            action = "Discard"
        elif key == "ENTER":
            action = "Edit"
        elif key == "UP":
            target = max(selected - 1, 0)
        elif key == "DOWN":
            target = min(selected + 1, len(rows) - 1)
        elif key == "LEFT":
            selected_button = max(selected_button - 1, 0)
        elif key == "RIGHT":
            selected_button = min(selected_button + 1, len(visible_buttons) - 1)
        elif key == " ":
            action = visible_buttons[selected_button]

        # update selected
        selected = max(min(target, len(rows) - 1), 0)
        current_row = rows[selected] if rows else None

        # execute actions
        if action in ("Main", "Macro", "Vim"):
            sheet = action.lower()
            selected = 0
            top = 0
        elif action == "Edit" and current_row:
            mode = "bind"
            chain = []
        elif action == "Reset" and current_row:
            if current_row["default"] or sheet != "macro":
                current_row["new"] = current_row["default"]
            else:   # delete custom macro
                if current_row["saved"]:
                    deleted_macros.append(current_row["saved"][0])
                rows.pop(selected)
        elif action == "Add":
            rows.append({"key": None, "new": (None, ""), "default": None, "saved": None})
            selected = len(rows) - 1
            mode = "bind"
            chain = []
        elif action in ("Save", "Save and Exit"):
            for name in SHEETS:
                if save_sheet(path, name, sheets[name], deleted_macros if name == "macro" else []):
                    changed = True
            deleted_macros.clear()
        if action in ("Save and Exit", "Discard"):
            break

    if not uses_gtkcurses:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
    screen.clear()
    screen.refresh()
    return changed


def bindings(config):
    """Show keybindings editor"""
    try:
        changed = curses.wrapper(main_tui, config)
    except curses.error as e:
        if str(e) == "addwstr() returned ERR":
            sys.exit("There was no enough space to render the TUI, please increase the terminal size")
        return config, False
    return config, changed
