# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import os
import shutil
import sys
import time

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    H_STDIN = kernel32.GetStdHandle(-10)
    H_STDOUT = kernel32.GetStdHandle(-11)
    OLD_IN_MODE = wintypes.DWORD()
    OLD_OUT_MODE = wintypes.DWORD()
    kernel32.GetConsoleMode(H_STDIN, ctypes.byref(OLD_IN_MODE))
    kernel32.GetConsoleMode(H_STDOUT, ctypes.byref(OLD_OUT_MODE))
else:
    import fcntl
    import select
    import termios
    import tty
    STDIN_FD = sys.stdin.fileno()
    OLD_TERM = termios.tcgetattr(STDIN_FD)


KEY_CODES = {
    b"\x1b[A": "UP",
    b"\x1b[B": "DOWN",
    b"\x1b[D": "LEFT",
    b"\x1b[C": "RIGHT",
    b"\x1b[H": "HOME",
    b"\x1b[F": "END",
    b"\x1b[3~": "DELETE",
    b" ": "SPC",
    b"\t": "TAB",
    b"\r": "ENTER",
    b"\n": "ENTER",
}

KEY_CODES_WIN = {
    b"H": "UP",
    b"P": "DOWN",
    b"M": "RIGHT",
    b"K": "LEFT",
    b"S": "DELETE",
}

width = 0
height = 0
run_esc_detector = False


def enter_tui():
    """Enter tui terminal mode"""
    tty.setcbreak(STDIN_FD)
    sys.stdout.write(
        "\x1b[?1049h"   # alternate screen
        "\x1b[?7l"      # disable line wrap
        "\x1b[2J"       # clear screen
        "\x1b[?25l"     # hide cursor
        "\x1b[H",       # cursor home
    )
    sys.stdout.flush()


def leave_tui():
    """Leave tui terminal mode"""
    sys.stdout.write(
        "\x1b[?1049l"   # leave alternate screen
        "\x1b[?7h"      # enable line wrap
        "\x1b[?25h"     # show cursor
        "\x1b[0m",      # reset attrs
    )
    sys.stdout.flush()
    termios.tcsetattr(STDIN_FD, termios.TCSADRAIN, OLD_TERM)



def enter_tui_win():
    """Enter tui mode on Windows console"""
    # enable virtual terminal input and processed input
    kernel32.SetConsoleMode(H_STDIN, OLD_IN_MODE.value | 0x0200 | 0x0001)
    # enable ansi escape processing
    kernel32.SetConsoleMode(H_STDOUT, OLD_OUT_MODE.value | 0x0004)
    sys.stdout.write(
    "\x1b[?1049h"   # alternate screen
    "\x1b[?7l"      # disable line wrap
    "\x1b[2J"       # clear screen
    "\x1b[?25l"     # hide cursor
    "\x1b[H",       # cursor home
    )
    sys.stdout.flush()


def leave_tui_win():
    """Leave tui terminal mode"""
    sys.stdout.write(
    "\x1b[?1049l"   # leave alternate screen
    "\x1b[?7h"      # enable wrap
    "\x1b[?25h"     # show cursor
    "\x1b[0m",      # reset attrs
    )
    sys.stdout.flush()
    kernel32.SetConsoleMode(H_STDIN, OLD_IN_MODE)
    kernel32.SetConsoleMode(H_STDOUT, OLD_OUT_MODE)


def get_size():
    """Get size of terminal in characters (h, w)"""
    size = shutil.get_terminal_size()
    return size.lines, size.columns


def draw(lines):
    """Draw lines on screen"""
    try:
        sys.stdout.write("\x1b[H")   # cursor home
        sys.stdout.write(lines)
        sys.stdout.flush()
    except BlockingIOError:
        pass


def draw_over_curses(text, y, x):
    """Draw lines on screen already used by curses, and restore cursor position"""
    sys.stdout.write("\x1b[s")  # save cursor
    for i, line in enumerate(text.split("\n")):
        sys.stdout.write(f"\x1b[{y + i + 1};{x + 1}H")
        sys.stdout.write(line)
    sys.stdout.write("\x1b[u")  # restore cursor
    sys.stdout.flush()


def read_key():
    """Blocking read key, return key code like curses.getch(), alt sequences are not handled"""
    fd = sys.stdin.fileno()

    # wait for first byte
    first = os.read(fd, 1)
    if not first:
        return None

    # backspace
    if first == b"\x7f":
        return "BACKSPACE"

    # standard characters
    if first != b"\x1b":
        key = KEY_CODES.get(first)
        if key:
            return key
        try:
            return first.decode("utf-8")
        except UnicodeDecodeError:
            return first

    # escape sequences
    seq = first
    start = time.time()
    while True:
        time_left = 0.01 - (time.time() - start)
        if time_left <= 0:
            break
        ready, _, _ = select.select([fd], [], [], time_left)
        if ready:
            try:
                byte = os.read(fd, 1)
                if not byte:
                    continue
                seq += byte
                if seq in KEY_CODES:
                    return KEY_CODES[seq]
                if len(seq) > 6:
                    break
            except (IOError, OSError):
                break
        else:
            break

    if seq == b"\x1b":
        return "ESC"

    return repr(seq)


def read_key_win():
    """Blocking key reader for Windows console"""
    while True:
        ch = msvcrt.getch()

        # backspace
        if ch == b"\x08":
            return "BACKSPACE"

        # escape
        if ch == b"\x1b":
            return "ESC"

        # escape sequences
        if ch in (b"\x00", b"\xe0"):
            ch2 = msvcrt.getch()
            return KEY_CODES_WIN.get(ch2, ch2.decode())

        return ord(ch)


def esc_detector():
    """A function to be ran in a thread that waits for esc key then exits"""
    global run_esc_detector
    run_esc_detector = True
    fd = sys.stdin.fileno()
    while run_esc_detector:
        ready_to_read, _, _ = select.select([fd], [], [], 0.01)
        if ready_to_read:
            try:
                byte = os.read(fd, 1)
                if byte and byte == b"\x1b":
                    run_esc_detector = False
            except (IOError, OSError):
                pass


def esc_detector_win():
    """A function to be ran in a thread that waits for esc key then exits"""
    global run_esc_detector
    run_esc_detector = True
    try:
        while run_esc_detector:
            try:
                ch = msvcrt.getch()
                if not ch:
                    time.sleep(0.01)
                elif ch == b"\x1b":
                    run_esc_detector = False
                else:
                    time.sleep(0.01)
            except BlockingIOError:
                time.sleep(0.01)
    except Exception:
        pass


def stop_esc_detector():
    """Stop esc detector thread"""
    global run_esc_detector
    run_esc_detector = False


if sys.platform == "win32":
    enter_tui = enter_tui_win
    leave_tui = leave_tui_win
    read_key = read_key_win
    esc_detector = esc_detector_win


def query_terminal(query, timeout=0.1, read_bytes=1024):
    """Query terminal with specific sequence, wait for response and return decoded response bytes"""
    if sys.platform == "win32":
        return None
    stdin_fd = sys.stdin.fileno()
    old_term = termios.tcgetattr(stdin_fd)
    old_flags = fcntl.fcntl(stdin_fd, fcntl.F_GETFL)
    response = b""
    try:
        tty.setraw(stdin_fd)
        fcntl.fcntl(stdin_fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)
        os.write(stdin_fd, query)
        while timeout > 0:
            try:
                response = os.read(stdin_fd, read_bytes)
                if response:
                    break
            except BlockingIOError:
                pass
            timeout -= 0.01
            time.sleep(0.01)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
        fcntl.fcntl(stdin_fd, fcntl.F_SETFL, old_flags)
    return response.decode()


def get_font_size():
    """Query font size from terminal"""
    response = query_terminal(b"\033[14t")
    if not response:
        return None, None
    parts = response.lstrip("\033[").rstrip("t").split(";")
    if len(parts) != 3:
        return None, None
    cols, rows = os.get_terminal_size()
    height = int(parts[1]) // rows
    width = int(parts[2]) // cols
    return width, height
