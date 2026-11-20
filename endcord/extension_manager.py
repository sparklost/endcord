# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import ast
import curses
import os
import shutil
import sys
import time

from endcord import git, keybinding, peripherals, utils

BUTTONS = ("Install/Update", "Uninstall", "Update All", "Quit")
EXT_PATH = os.path.expanduser(os.path.join(peripherals.config_path, "Extensions"))
uses_gtkcurses = hasattr(curses, "GTKCURSES")


def read_extension(ext_file):
    """Read extension info without running it"""
    try:
        with open(ext_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except Exception:
        return {}
    info = {"description": (ast.get_docstring(tree) or "").split("\n")[0]}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith("EXT_"):
            try:
                info[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return info


def check_updates(config):
    """Check installed extensions for updates once per day"""
    last_check = utils.load_json("version.json", {
        "time": int(time.time()),
        "current": peripherals.VERSION,
        "version": peripherals.VERSION,
        "extensions": {},
    }, create=True)
    just_updated = git.ver_to_tuple(peripherals.VERSION) > git.ver_to_tuple(last_check["current"])
    if not just_updated and (not config["check_for_updates"] or int(time.time()) - max(last_check["time"], last_check.get("ext_time", 0)) < 86400):
        return
    extensions, _ = utils.get_extensions(EXT_PATH)
    for ext_file, _ in extensions:
        info = read_extension(ext_file)
        source = info.get("EXT_SOURCE", "")
        parts = source.strip("/").split("/")
        if "github.com" not in source or len(parts) < 2:
            continue
        repo_owner = parts[-2]
        repo_name = parts[-1]
        local = info.get("EXT_VERSION", "0.0.0")
        ext_last_check = str(last_check["extensions"].get(repo_name, local)).rsplit(" ", 1)
        ext_last_check = (local, None) if len(ext_last_check) < 2 else ext_last_check
        ext_version, etag = ext_last_check
        if git.ver_to_tuple(local) > git.ver_to_tuple(ext_version):
            ext_version = local
        try:
            new_version, new_etag = git.check_for_update(ext_version, repo_owner, repo_name, etag, proxy=config["proxy"])
        except Exception:
            continue
        if new_etag and new_etag == etag:
            continue
        last_check["extensions"][repo_name] = f"{new_version if new_version else ext_version} {new_etag}"
        time.sleep(0.5)
    last_check["ext_time"] = int(time.time())
    utils.save_json(last_check, "version.json")


def get_rows(online):
    """Collect installed extensions and merge with online extensions"""
    os.makedirs(EXT_PATH, exist_ok=True)
    found, invalid = utils.get_extensions(EXT_PATH)
    cache = (utils.load_json("version.json") or {}).get("extensions", {})   # latest versions from last update check
    rows = []
    for bad, items in ((False, found), (True, invalid)):
        for ext_file, ext_name in items:
            info = read_extension(ext_file)
            source = info.get("EXT_SOURCE") or ""
            parts = source.strip("/").split("/")
            local = str(info.get("EXT_VERSION", "?"))
            latest = str(cache.get(parts[-1], local)).split(" ")[0]
            description = info.get("EXT_DESCRIPTION") or info["description"] if info else ""
            rows.append({
                "installed": True,
                "name": ext_name,
                "official": len(parts) > 1 and parts[-2] == peripherals.REPO_OWNER,
                "description": description.replace("An extension that ", "").capitalize(),
                "source": source,
                "path": ext_file,
                "local": local,
                "latest": latest,
                "outdated": git.ver_to_tuple(latest) > git.ver_to_tuple(local),
                "invalid": bad or not info,
            })
    rows.sort(key=lambda row: row["name"])
    installed = [row["name"] for row in rows]
    for item in online:
        if item["name"] not in installed:
            rows.append({**item, "installed": False, "local": "-", "latest": "-", "invalid": False, "outdated": False})
    return rows


def install(config, row):
    """Install or update extension"""
    if not row["source"]:
        return "Extension has no source"
    try:
        if row["installed"]:
            return git.install_extension(row["source"], proxy=config["proxy"], prefer_tag=row["latest"], update=True)[1]
        return git.install_extension(row["source"], proxy=config["proxy"])[1]
    except Exception as e:
        return f"Failed: {e}"


def uninstall(row):
    """Delete extension folder"""
    ext_dir = os.path.dirname(os.path.abspath(row["path"]))
    if ext_dir != os.path.abspath(EXT_PATH):
        shutil.rmtree(ext_dir)


def draw_row(screen, y, x, w, cells, description, attr, description_attr):
    """Draw one row in table"""
    line = " " + " ".join(cells) + " "
    screen.addstr(y, x, line[:w].ljust(w), attr)
    if len(line) < w:   # description
        screen.addstr(y, x + len(line), description.replace("\n", " ")[:w - len(line)].ljust(w - len(line)), description_attr)


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
    """Main extension manager tui, returns True if installed extensions changed"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    curses.init_pair(1, -1, -1)
    curses.init_pair(2, 46, -1)   # greem
    curses.init_pair(3, 208, -1)   # orange
    curses.init_pair(4, 196, -1)   # red
    curses.init_pair(5, 245, -1)   # description
    screen.bkgd(" ", curses.color_pair(1))
    screen.bkgd(" ", curses.color_pair(1))

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

    bordered = not config["compact"]
    border_corners = config["border_corners"]
    x0 = int(bordered)
    if bordered:
        draw_border(screen, border_corners, 1)
    screen.addstr(x0, x0, " Loading extensions...", curses.color_pair(1))
    screen.refresh()

    check_updates(config)
    online = git.search_gh_repos("endcord-extension", proxy=config["proxy"])
    online = [{"name": x1, "official": x3, "description": x2, "source": x4} for _, x1, x2, x3, x4 in online]
    rows = get_rows(online)
    start_state = [(row["name"], row["local"]) for row in rows if row["installed"]]
    selected = 0
    top = 0
    selected_button = 0
    confirm = None
    message = ""

    while True:
        h, w = screen.getmaxyx()
        row_w = (w - 2 * x0) - 1
        list_y = x0 + 1
        hint_y = h - 2 - x0
        button_y = h - 1 - x0
        list_h = max(hint_y - list_y, 1)
        selected = max(min(selected, len(rows) - 1), 0)
        top = min(top, selected)
        if selected >= top + list_h:
            top = selected - list_h + 1
        name_w = max([len(row["name"]) for row in rows] + [4])
        ver_w = max([len(row[key]) for row in rows for key in ("local", "latest")] + [6])

        screen.erase()
        header = ("Inst", "Name".ljust(name_w), "Official", "Local".ljust(ver_w), "Latest".ljust(ver_w))
        draw_row(screen, x0, x0, row_w, header, "Description", curses.color_pair(1) | curses.A_BOLD, curses.color_pair(1) | curses.A_BOLD)
        if message:
            screen.addstr(hint_y, x0, message[:row_w + 1].center(row_w + 1), curses.color_pair(1))

        # draw extensions
        for row in range(list_h):
            num = top + row
            if num >= len(rows):
                break
            ext = rows[num]
            color = 4 if ext["invalid"] else 3 if ext["outdated"] else 2 if ext["installed"] else 1
            standout = curses.A_STANDOUT if num == selected else 0
            desc_color = color if num == selected else 5
            cells = (
                ("✔" if ext["installed"] else "").ljust(4),
                ext["name"].ljust(name_w),
                ("YES" if ext["official"] else "no").ljust(8),
                ext["local"].ljust(ver_w),
                ext["latest"].ljust(ver_w),
            )
            draw_row(screen, list_y + row, x0, row_w, cells, ext["description"], curses.color_pair(color) | standout, curses.color_pair(desc_color) | standout)

        # draw scrollbar
        if len(rows) > list_h:
            thumb_size = max(list_h * list_h // len(rows), 1)
            thumb_pos = top * (list_h - thumb_size) // (len(rows) - list_h)
            for row in range(thumb_pos, thumb_pos + thumb_size):
                screen.addstr(list_y + row, x0 + row_w, "┃", curses.color_pair(1))

        # draw buttons
        buttons = [f"[{name.center(8)}]" for name in BUTTONS]
        x = max((w - len("  ".join(buttons))) // 2, 0)
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
        key = get_key(screen)   # screen.getch()
        message = ""

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
                target = min(selected + 3, len(rows) - 1)
            elif pressed and key_type == 0:   # LEFT CLICK
                if list_y <= my < list_y + list_h and top + my - list_y < len(rows):
                    target = top + my - list_y
                elif my == button_y:
                    for num, (start, end) in enumerate(button_spans):
                        if start <= mx < end:
                            selected_button = num
                            action = num

        # keyboard
        elif key in (27, "ESC", "C-c"):
            action = 3   # QUIT
        elif key in (curses.KEY_UP, "UP"):
            target = max(selected - 1, 0)
        elif key in (curses.KEY_DOWN, "DOWN"):
            target = min(selected + 1, len(rows) - 1)
        elif key in (curses.KEY_LEFT, "LEFT"):
            selected_button = max(selected_button - 1, 0)
        elif key in (curses.KEY_RIGHT, "RIGHT"):
            selected_button = min(selected_button + 1, len(BUTTONS) - 1)
        elif key in (32, " ", "ENTER"):
            action = selected_button

        # update states when moved
        if target != selected or (action is not None and action != 1):
            confirm = None
        selected = max(min(target, len(rows) - 1), 0)
        selected_row = rows[selected] if rows else None

        # execute actions
        if action in (0, 2):
            screen.addstr(hint_y, x0, "Working...".center(row_w + 1), curses.color_pair(1))
            screen.refresh()
        if action == 0 and selected_row:   # INSTALL/UPDATE
            if not selected_row["installed"] or selected_row["outdated"]:
                message = install(config, selected_row)
            else:
                message = "Already up to date"
        elif action == 1 and selected_row and selected_row["installed"]:   # UNINSTALL
            if confirm != selected_row["name"]:
                confirm = selected_row["name"]
                message = f"Press Uninstall again to uninstall {selected_row["name"]}"
            else:
                uninstall(selected_row)
                confirm = None
                message = f"Uninstalled {selected_row["name"]}"
        elif action == 2:   # UPDATE ALL
            updated = 0
            for item in rows:
                if item["installed"] and item["outdated"]:
                    install(config, item)
                    updated += 1
            message = f"Updated {updated} extension{"s" if updated != 1 else ""}"
        elif action == 3:   # QUIT
            break
        if action in (0, 1, 2):
            rows = get_rows(online)

    screen.clear()
    screen.refresh()
    return [(row["name"], row["local"]) for row in rows if row["installed"]] != start_state


def manage(config):
    """Show extension manager"""
    try:
        return curses.wrapper(main_tui, config)
    except curses.error as e:
        if str(e) == "addwstr() returned ERR":
            sys.exit("There was no enough space to render the TUI, please increase the terminal size")
        return False
