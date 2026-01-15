# cython: boundscheck=False, wraparound=False

cpdef img_to_term(
    object img,
    object img_gray,
    int bg_color,
    list ascii_palette,
    int ascii_palette_len,
    int screen_width,
    int screen_height,
    int img_width,
    int img_height
):
    cdef object pixels = img.load()
    cdef object pixels_gray = img_gray.load()

    cdef int padding_h = (screen_height - img_height) // 2
    cdef int padding_w = (screen_width - img_width) // 2
    cdef int x, y, gray_val, color, current_fg, visible_len

    ESC = "\x1b"
    RESET = ESC + "[0m"
    bg = ESC + "[48;5;" + str(bg_color) + "m"

    cdef list out_lines = []

    # top padding
    for y in range(padding_h):
        out_lines.append(bg + " " * screen_width + RESET)

    # image rows
    for y in range(img_height):
        line_parts = []
        current_fg = -1

        # left padding
        if padding_w > 0:
            line_parts.append(bg)
            line_parts.append(" " * padding_w)

        for x in range(img_width):
            gray_val = pixels_gray[x, y]
            color = pixels[x, y] + 16
            if color != current_fg:
                line_parts.append(ESC)   # avoiding string concatenations for speed
                line_parts.append("[38;5;")
                line_parts.append(str(color))
                line_parts.append("m")
                current_fg = color
            line_parts.append(ascii_palette[(gray_val * ascii_palette_len) // 255])

        # right padding
        visible_len = padding_w + img_width
        if visible_len < screen_width:
            line_parts.append(bg)
            line_parts.append(" " * (screen_width - visible_len))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height:
        out_lines.append(bg + " " * screen_width + RESET)

    return "\n".join(out_lines)


cpdef img_to_term_block(
    object img,
    int bg_color,
    int screen_width,
    int screen_height,
    int img_width,
    int img_height
):
    cdef object pixels = img.load()

    cdef int padding_h = (screen_height - img_height // 2) // 2
    cdef int padding_w = (screen_width - img_width) // 2
    cdef int x, y, top_color, bot_color, current_fg, current_bg, visible_len

    ESC = "\x1b"
    RESET = ESC + "[0m"
    bg = ESC + "[48;5;" + str(bg_color) + "m"

    cdef list out_lines = []

    # top padding
    for y in range(padding_h):
        out_lines.append(bg + " " * screen_width + RESET)

    # image rows
    for y in range(0, img_height - 1, 2):
        line_parts = []
        current_fg = -1
        current_bg = -1

        # left padding
        if padding_w > 0:
            line_parts.append(bg)
            line_parts.append(" " * padding_w)

        for x in range(img_width):
            top_color = pixels[x, y] + 16
            bot_color = pixels[x, y + 1] + 16
            if top_color != current_fg:
                line_parts.append(ESC)   # avoiding string concatenations for speed
                line_parts.append("[38;5;")
                line_parts.append(str(top_color))
                line_parts.append("m")
                current_fg = top_color
            if bot_color != current_bg:
                line_parts.append(ESC)
                line_parts.append("[48;5;")
                line_parts.append(str(bot_color))
                line_parts.append("m")
                current_bg = bot_color
            line_parts.append("▀")

        # right padding
        visible_len = padding_w + img_width
        if visible_len < screen_width:
            line_parts.append(bg)
            line_parts.append(" " * (screen_width - visible_len))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height:
        out_lines.append(bg + " " * screen_width + RESET)

    return "\n".join(out_lines)
