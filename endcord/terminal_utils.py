# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

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
    import termios
    import tty
    STDIN_FD = sys.stdin.fileno()
    OLD_TERM = termios.tcgetattr(STDIN_FD)


KEY_CODES = {   # from curses for consistency
    b"\x1b[A": 259,  # UP
    b"\x1b[B": 258,  # DOWN
    b"\x1b[D": 260,  # LEFT
    b"\x1b[C": 261,  # RIGHT
    b"\x1b[H": 262,  # HOME
    b"\x1b[F": 360,  # END
    b"\x1b[5~": 339, # PG_UP
    b"\x1b[6~": 338, # PG_DOWN
    b"\x1b[3~": 330, # DELETE
    b"\x1b[2~": 331, # INSERT
}

KEY_CODES_WIN = {
    b"H": 259,  # UP
    b"P": 258,  # DOWN
    b"M": 261,  # RIGHT
    b"K": 260,  # LEFT
    b"S": 330,  # DELETE
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
    sys.stdout.write("\x1b[H")   # cursor home
    sys.stdout.write(lines)
    sys.stdout.flush()


def read_key():
    """Blocking read key, return key code like curses.getch(), alt sequences are not handled"""
    fd = sys.stdin.fileno()
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        # wait for first byte
        first = os.read(fd, 1)

        # using O_NONBLOCK instead select() for windows compatibility
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)

        # backspace
        if first == b"\x7f":
            return 263

        # single code
        if first != b"\x1b":
            return KEY_CODES.get(first, ord(first))

        # escape sequences
        seq = first
        start = time.time()
        while time.time() - start < 0.01:   # 10ms timeout
            try:
                byte = os.read(fd, 1)
                if not byte:
                    time.sleep(0.001)
                    continue
                seq += byte
                if seq in KEY_CODES:
                    return KEY_CODES[seq]
                if len(seq) > 6:
                    break
            except BlockingIOError:
                time.sleep(0.001)

        return 27

    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)


def read_key_win():
    """Blocking key reader for Windows console"""
    while True:
        ch = msvcrt.getch()

        # backspace
        if ch == b"\x08":
            return 8

        # escape
        if ch == b"\x1b":
            return 27

        # escape sequences
        if ch in (b"\x00", b"\xe0"):
            ch2 = msvcrt.getch()
            return KEY_CODES_WIN.get(ch2, ord(ch2))

        return ord(ch)


def esc_detector():
    """A function to be ran in a thread that waits for esc key then exits"""
    global run_esc_detector
    run_esc_detector = True
    fd = sys.stdin.fileno()
    old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)
        while run_esc_detector:
            try:
                byte = os.read(fd, 1)
                if not byte:
                    time.sleep(0.01)
                elif byte == b"\x1b":
                    run_esc_detector = False
                else:
                    time.sleep(0.01)
            except BlockingIOError:
                time.sleep(0.01)
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)


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
    finally:
        return


def stop_esc_detector():
    """Stop esc detector thread"""
    global run_esc_detector
    run_esc_detector = False


if sys.platform == "win32":
    enter_tui = enter_tui_win
    leave_tui = leave_tui_win
    read_key = read_key_win
    esc_detector = esc_detector_win
