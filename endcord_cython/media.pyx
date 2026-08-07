# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

# cython: boundscheck=False, wraparound=False, freethreading_compatible=True

cdef str ESC = "\x1b"
cdef str RESET = ESC + "[0m"
cdef str FG_PREFIX = ESC + "[38;2;"
cdef str BG_PREFIX = ESC + "[48;2;"
cdef str FG_PREFIX_ANSI = ESC + "[38;5;"
cdef str BG_PREFIX_ANSI = ESC + "[48;5;"
cdef const char* RESET_NL = "\x1b[0m\n"   # need it as bytes


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

    cdef str BGR = ESC + "[48;5;" + str(bg_color) + "m"
    cdef str blank_line = BGR + (" " * screen_width) + RESET
    cdef list out_lines = []

    # top padding
    for y in range(padding_h):
        out_lines.append(blank_line)

    # image rows
    for y in range(img_height):
        line_parts = []
        current_fg = -1

        # left padding
        if padding_w > 0:
            line_parts.append(BGR)
            line_parts.append(" " * padding_w)

        for x in range(img_width):
            gray_val = pixels_gray[x, y]
            color = pixels[x, y] + 16
            if color != current_fg:
                line_parts.append(FG_PREFIX_ANSI)   # avoiding string concatenations for speed
                line_parts.append(str(color))
                line_parts.append("m")
                current_fg = color
            line_parts.append(ascii_palette[(gray_val * ascii_palette_len) // 255])

        # right padding
        visible_len = padding_w + img_width
        if visible_len < screen_width:
            line_parts.append(BGR)
            line_parts.append(" " * (screen_width - visible_len))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height:
        out_lines.append(blank_line)

    return "\n".join(out_lines)


cpdef img_to_term_block(
    bytes buf,
    int bg_color,
    int screen_width,
    int screen_height,
    int img_width,
    int img_height
):
    cdef const unsigned char* data = <const unsigned char*>buf
    cdef int padding_h = (screen_height - img_height // 2) // 2
    cdef int padding_w = (screen_width - img_width) // 2
    cdef int x, y, top_color, bot_color, current_fg, current_bg, visible_len

    cdef str BGR = ESC + "[48;5;" + str(bg_color) + "m"
    cdef str blank_line = BGR + (" " * screen_width) + RESET
    cdef list line_parts
    cdef list out_lines = []

    # top padding
    for y in range(padding_h):
        out_lines.append(blank_line)

    # image rows
    for y in range(0, img_height - 1, 2):
        line_parts = []
        current_fg = -1
        current_bg = -1

        # left padding
        if padding_w > 0:
            line_parts.append(BGR)
            line_parts.append(" " * padding_w)

        for x in range(img_width):
            top_color = data[y * img_width + x] + 16
            bot_color = data[(y + 1) * img_width + x] + 16
            if top_color != current_fg:
                line_parts.append(FG_PREFIX_ANSI)   # avoiding string concatenations for speed
                line_parts.append(str(top_color))
                line_parts.append("m")
                current_fg = top_color
            if bot_color != current_bg:
                line_parts.append(BG_PREFIX_ANSI)
                line_parts.append(str(bot_color))
                line_parts.append("m")
                current_bg = bot_color
            line_parts.append("▀")

        # right padding
        visible_len = padding_w + img_width
        if visible_len < screen_width:
            line_parts.append(BGR)
            line_parts.append(" " * (screen_width - visible_len))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height:
        out_lines.append(blank_line)

    return "\n".join(out_lines)


# ~3x faster than below commented function
# this is using only C, removing all python overhead

from libc.stdlib cimport malloc, free
from libc.string cimport memcpy
from libc.stdio cimport sprintf

# prepare int to char lookup table for 0-255
cdef struct ColorStr:
    char s[4]
    int length
cdef ColorStr num_lookup[256]
cdef int _i
for _i in range(256):
    num_lookup[_i].length = sprintf(num_lookup[_i].s, "%d", _i)


cdef inline void append_color_channel(char** cursor_ptr, unsigned char channel_val, char separator) noexcept nogil:
    cdef char* cursor = cursor_ptr[0]   # dereferenced
    memcpy(cursor, num_lookup[channel_val].s, num_lookup[channel_val].length)
    cursor += num_lookup[channel_val].length
    cursor[0] = separator
    cursor += 1
    cursor_ptr[0] = cursor   # back to pointer ref


cpdef str img_to_term_block_truecolor(
    bytes buf,
    int bg_color,
    int screen_width,
    int screen_height,
    int img_width,
    int img_height
):
    cdef const unsigned char* data = <const unsigned char*>buf
    cdef int padding_h = (screen_height - img_height // 2) // 2
    cdef int padding_w = (screen_width - img_width) // 2
    cdef int right_pad_len = screen_width - padding_w - img_width
    cdef int x, y, row_top
    cdef Py_ssize_t idx
    cdef unsigned char fr, fg, fb, br, bg, bb
    cdef int cfr = -1, cfg = -1, cfb = -1
    cdef int cbr = -1, cbg = -1, cbb = -1
    cdef int current_lines
    cdef bytes raw_bytes
    cdef str result

    # pre allocate c buffer
    cdef size_t max_buf_size = (img_width * img_height * 30) + (screen_width * screen_height * 2) + 8192
    cdef char* c_buf = <char*>malloc(max_buf_size)
    cdef char* cursor = c_buf

    # prepare terminal background sequence
    cdef char bgr_seq[32]
    cdef int bgr_len = sprintf(bgr_seq, "\x1b[48;5;%dm", bg_color)

    with nogil:
        # top padding
        for y in range(padding_h):
            memcpy(cursor, bgr_seq, bgr_len)
            cursor += bgr_len
            for x in range(screen_width):
                cursor[0] = b" "
                cursor += 1
            memcpy(cursor, RESET_NL, 5)
            cursor += 5

        # image rows
        for y in range(0, img_height - 1, 2):
            cfr, cfg, cfb = -1, -1, -1
            cbr, cbg, cbb = -1, -1, -1

            # Left padding
            if padding_w > 0:
                memcpy(cursor, bgr_seq, bgr_len)
                cursor += bgr_len
                for x in range(padding_w):
                    cursor[0] = b" "
                    cursor += 1

            row_top = y * img_width * 3
            for x in range(img_width):
                idx = row_top + x * 3
                fr = data[idx]
                fg = data[idx + 1]
                fb = data[idx + 2]
                idx += img_width * 3
                br = data[idx]
                bg = data[idx + 1]
                bb = data[idx + 2]
                if fr != cfr or fg != cfg or fb != cfb:
                    memcpy(cursor, "\x1b[38;2;", 7)
                    cursor += 7
                    append_color_channel(&cursor, fr, ";")
                    append_color_channel(&cursor, fg, ";")
                    append_color_channel(&cursor, fb, "m")
                    cfr, cfg, cfb = fr, fg, fb
                if br != cbr or bg != cbg or bb != cbb:
                    memcpy(cursor, "\x1b[48;2;", 7)
                    cursor += 7
                    append_color_channel(&cursor, br, ";")
                    append_color_channel(&cursor, bg, ";")
                    append_color_channel(&cursor, bb, "m")
                    cbr, cbg, cbb = br, bg, bb
                # block character (▀)
                cursor[0] = <char>0xe2
                cursor[1] = <char>0x96
                cursor[2] = <char>0x80
                cursor += 3

            # right padding
            if right_pad_len > 0:
                memcpy(cursor, bgr_seq, bgr_len)
                cursor += bgr_len
                for x in range(right_pad_len):
                    cursor[0] = b" "
                    cursor += 1

            memcpy(cursor, RESET_NL, 5)
            cursor += 5

        # bottom padding
        current_lines = padding_h + (img_height // 2)
        while current_lines < screen_height:
            memcpy(cursor, bgr_seq, bgr_len)
            cursor += bgr_len
            for x in range(screen_width):
                cursor[0] = b" "
                cursor += 1
            memcpy(cursor, RESET_NL, 5)
            cursor += 5
            current_lines += 1

    # convert to python string
    if cursor > c_buf and (cursor - 1)[0] == b"\n":
        cursor -= 1
    raw_bytes = c_buf[:cursor - c_buf]
    result = raw_bytes.decode("utf-8", errors="replace")
    free(c_buf)
    return result


# the old way, ~3x slower than above

# cdef list numstr = [str(i) for i in range(256)]
#
# cpdef img_to_term_block_truecolor(
#     bytes buf,
#     int bg_color,
#     int screen_width,
#     int screen_height,
#     int img_width,
#     int img_height
# ):
#     cdef const unsigned char* data = <const unsigned char*>buf
#     cdef int padding_h = (screen_height - img_height // 2) // 2
#     cdef int padding_w = (screen_width - img_width) // 2
#     cdef int x, y, row_top, visible_len
#     cdef Py_ssize_t idx
#     cdef unsigned char fr, fg, fb, br, bg, bb
#     cdef int cfr = -1, cfg = -1, cfb = -1
#     cdef int cbr = -1, cbg = -1, cbb = -1
#
#     cdef str BGR = ESC + "[48;5;" + str(bg_color) + "m"   # bg color is not in r;g;b
#     cdef str blank_line = BGR + (" " * screen_width) + RESET
#     cdef str left_padding = " " * padding_w
#     cdef str right_padding = " " * (screen_width - padding_w - img_width)
#     cdef list line_parts
#     cdef list out_lines = []
#
#     # top padding
#     for y in range(padding_h):
#         out_lines.append(blank_line)
#
#     # image rows
#     for y in range(0, img_height - 1, 2):
#         line_parts = []
#         cfr, cfg, cfb = -1, -1, -1
#         cbr, cbg, cbb = -1, -1, -1
#
#         # left padding
#         if padding_w > 0:
#             line_parts.append(BGR)
#             line_parts.append(left_padding)
#
#         row_top = y * img_width * 3
#         for x in range(img_width):
#             idx = row_top + x * 3
#             fr = data[idx]
#             fg = data[idx + 1]
#             fb = data[idx + 2]
#             idx += img_width * 3
#             br = data[idx]
#             bg = data[idx + 1]
#             bb = data[idx + 2]
#             if fr != cfr or fg != cfg or fb != cfb:   # top pixel
#                 line_parts.append(FG_PREFIX)
#                 line_parts.append(numstr[fr])
#                 line_parts.append(";")
#                 line_parts.append(numstr[fg])
#                 line_parts.append(";")
#                 line_parts.append(numstr[fb])
#                 line_parts.append("m")
#                 cfr, cfg, cfb = fr, fg, fb
#             if br != cbr or bg != cbg or bb != cbb:   # bot pixel
#                 line_parts.append(BG_PREFIX)
#                 line_parts.append(numstr[br])
#                 line_parts.append(";")
#                 line_parts.append(numstr[bg])
#                 line_parts.append(";")
#                 line_parts.append(numstr[bb])
#                 line_parts.append("m")
#                 cbr, cbg, cbb = br, bg, bb
#             line_parts.append("▀")
#
#         # right padding
#         if padding_w + img_width < screen_width:
#             line_parts.append(BGR)
#             line_parts.append(right_padding)
#
#         line_parts.append(RESET)
#         out_lines.append("".join(line_parts))
#
#     # bottom padding
#     while len(out_lines) < screen_height:
#         out_lines.append(blank_line)
#
#     return "\n".join(out_lines)
