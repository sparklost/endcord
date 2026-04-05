# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import shutil

import qrcode

FG_WHITE = "\x1b[38;5;15m"
BG_BLACK = "\x1b[48;5;16m"
RESET = "\x1b[0m"


def gen_qr_terminal_string(text, text_above="", text_bellow=""):
    """Convert string to QR code string ready to be printed to terminal and check for terminal size"""
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()

    height = len(matrix)
    width = len(matrix[0])
    term = shutil.get_terminal_size()
    screen_height = term.lines
    screen_width = term.columns

    text_above = [x.center(screen_width) for x in text_above.split("\n")]
    text_bellow = [x.center(screen_width) for x in text_bellow.split("\n")]
    screen_height -= len(text_above) + len(text_bellow)

    # check terminal size
    height_real = (height + 1) // 2
    if screen_width < width or screen_height < height_real:
        text = ["Terminal too small:", f"Need: {width}x{height_real}.", f"Have: {screen_width}x{screen_height}."]
        return 0, "\n".join([x.center(screen_width) for x in text])

    # build string
    out_lines = []
    out_lines.append(BG_BLACK + FG_WHITE + "\n".join(text_above) + RESET)

    # calculate paddings
    padding_h = (screen_height - height // 2) // 2
    padding_w = (screen_width - width) // 2

    # top padding
    for _ in range(padding_h):
        out_lines.append(BG_BLACK + (" " * screen_width) + RESET)

    for y in range(0, height, 2):
        top_line = matrix[y]
        bottom_line = matrix[y + 1] if y + 1 < height else [False] * width
        line_parts = []

        # left padding
        if padding_w > 0:
            line_parts.append(BG_BLACK + (" " * padding_w))

        # qr code
        for x in range(width):
            top = top_line[x]
            bottom = bottom_line[x]
            if top and bottom:
                line_parts.append("█")
            elif top and not bottom:
                line_parts.append("▀")
            elif not top and bottom:
                line_parts.append("▄")
            else:
                line_parts.append(" ")

        # right padding
        visible_len = padding_w + width
        if visible_len < screen_width:
            line_parts.append(BG_BLACK + (" " * (screen_width - visible_len)))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height + 1:
        out_lines.append(BG_BLACK + (" " * screen_width) + RESET)

    out_lines.append(BG_BLACK + FG_WHITE + "\n".join(text_bellow) + RESET)

    return 1, "\n".join(out_lines)
