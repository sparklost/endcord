# cython: boundscheck=False, wraparound=False, cdivision=True, freethreading_compatible=True

from cpython.tuple cimport PyTuple_GET_ITEM
import pygame

cdef unsigned int A_STANDOUT   = 0x00010000
cdef unsigned int A_UNDERLINE  = 0x00020000
cdef unsigned int A_BOLD       = 0x00200000
cdef unsigned int A_ITALIC     = 0x80000000


cdef inline bint is_emoji(Py_UCS4 ch):
    return (
        (0x1F300 <= ch <= 0x1F9FF) or
        (0x2600 <= ch <= 0x27BF) or
        (0x2300 <= ch <= 0x23FF) or
        (0x2B00 <= ch <= 0x2BFF)
    )


cpdef void insstr(
    list buffer,
    int nlines,
    int ncols,
    set dirty_lines,
    object dirty_lock,
    int y,
    int x,
    unicode text,
    unsigned int attr=0
):
    cdef Py_ssize_t i, j, row, line_len, min_len
    cdef Py_ssize_t len_lines
    cdef unicode line
    cdef object row_buffer

    cdef list lines = text.split("\n")
    len_lines = len(lines)

    with dirty_lock:
        for i in range(len_lines):
            line = lines[i]
            row = y + i
            if row >= nlines:
                break
            row_buffer = buffer[row]
            line_len = ncols - x
            if line_len <= 0:
                continue
            if line_len < len(line):
                min_len = line_len
            else:
                min_len = len(line)

            for j in range(min_len):
                row_buffer[x + j] = (line[j], attr)

            if i < len_lines - 1 and min_len < line_len:
                for j in range(min_len, line_len):
                    row_buffer[x + j] = (" ", attr)

            dirty_lines.add(row)


cpdef void render(
    object screen,
    list buffer,
    set dirty_lines,
    object dirty_lock,
    int ncols,
    int char_width,
    int char_height,
    int pxx,
    int pxy,
    object font_regular,
    object font_bold,
    object font_italic,
    object font_bold_italic,
    object emoji_font,
    list color_map,
):
    cdef int y
    cdef Py_ssize_t i, span_draw_x
    cdef int draw_x, px_x, px_y
    cdef object row, ch_obj, attr_obj
    cdef unsigned int attr, attr2, flags
    cdef tuple fg, bg
    cdef list text_buffer
    cdef unicode ch
    cdef object surf, emoji
    cdef int text_len
    cdef tuple color_pair

    with dirty_lock:
        for y in dirty_lines:
            row = buffer[y]
            i = 0
            draw_x = 0
            while i < ncols:
                ch = <object>PyTuple_GET_ITEM(row[i], 0)
                attr_obj = <object>PyTuple_GET_ITEM(row[i], 1)
                attr = <unsigned int>int(attr_obj)
                flags = attr & 0xFFFF0000

                if is_emoji(ch):
                    px_x = draw_x * char_width
                    px_y = y * char_height
                    color_pair = color_map[attr & 0xFFFF]
                    fg, bg = color_pair
                    if flags & A_STANDOUT:
                        fg, bg = bg, fg
                    screen.fill(bg, (px_x + pxx, px_y + pxy, 2 * char_width, char_height))

                    try:
                        surf = emoji_font.render(ch, True, (255, 255, 255))
                        emoji = pygame.transform.smoothscale(surf, (char_height, char_height))
                    except Exception:
                        emoji = None

                    if emoji:
                        offset = px_x + (2 * char_width - char_height) // 2
                        screen.blit(emoji, (offset + pxx, px_y + pxy))
                    draw_x += 2   # emoji takes two cells visually
                    i += 2   # so push buffer line one extra char right
                    continue

                # collect characters with same attributes
                span_draw_x = draw_x
                text_buffer = []
                while i < ncols:
                    ch = <object>PyTuple_GET_ITEM(row[i], 0)
                    attr_obj = <object>PyTuple_GET_ITEM(row[i], 1)
                    attr2 = <unsigned int>int(attr_obj)
                    if attr2 != attr or is_emoji(ch):
                        break
                    text_buffer.append(ch)
                    i += 1
                    draw_x += 1
                if not text_buffer:
                    i += 1
                    draw_x += 1
                    continue

                # render collected text
                text = "".join(text_buffer)
                fg, bg = color_map[attr & 0xFFFF]
                if flags & A_STANDOUT:
                    fg, bg = bg, fg
                px_x = span_draw_x * char_width
                px_y = y * char_height
                screen.fill(bg, (px_x + pxx, px_y + pxy, len(text) * char_width, char_height))
                if flags & A_BOLD:
                    if flags & A_ITALIC:
                        font = font_bold_italic
                    else:
                        font = font_bold
                elif flags & A_ITALIC:
                    if flags & A_ITALIC:
                        font = font_bold_italic
                    else:
                        font = font_italic
                else:
                    font = font_regular
                font.underline = bool(flags & A_UNDERLINE)
                font.render_to(screen, (px_x + pxx, px_y + pxy), text, fg)

        dirty_lines.clear()
