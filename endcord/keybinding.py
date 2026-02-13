import curses
import sys

message = """ Press key combination, its code will be printed in terminal.
 Use this code to set this key combination in config.ini keybinding section.
 Some key combinations are reserved by terminal: Ctrl+ C/I/J/M/Q/S/Z.
 Ctrl+Shift+Key combinations are not supported, but Alt+Shift+Key are.
 Ctrl+C to exit."""


def get_key(screen):
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


def picker_internal(screen, keybindings, command_bindings):
    """Keybinding picker, prints last pressed key combination"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.init_pair(1, -1, -1)
    screen.bkgd(" ", curses.color_pair(1))
    screen.addstr(1, 0, message)
    command_bindings = [(val, key) for key, val in command_bindings.items()]
    while True:
        key_code = get_key(screen)
        if key_code == 27:   # escape sequence, when ALT+KEY is pressed
            screen.nodelay(True)
            key_code_2 = get_key(screen)   # key pressed with ALT
            screen.nodelay(False)
            if key_code_2 != -1:
                key_code = "ALT+" + str(key_code_2)
        elif key_code == curses.KEY_RESIZE:
            screen.addstr(1, 0, message)

        text = f"Keybinding code: {str(key_code)}"
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


def picker(keybindings, command_bindings):
    """Keybinding picker, prints last pressed key combination"""
    try:
        curses.wrapper(picker_internal, keybindings, command_bindings)
    except curses.error as e:
        if str(e) != "endwin() returned ERR":
            sys.exit("Curses error")
