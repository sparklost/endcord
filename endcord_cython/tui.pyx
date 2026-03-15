# cython: boundscheck=False, wraparound=False, freethreading_compatible=True

import curses
import threading
cimport cython


cdef void safe_insch(object win_chat, int y, int x, unicode ch, unsigned int attr):
    """
    Writes a character safely to the curses window.
    Uses insstr for emoji/multibyte characters, insch for ASCII-safe ones.
    """
    if ord(ch) > 127:   # for some reason insch wont draw 2-byte chars is cython
        win_chat.insstr(y, x, ch, attr)
    else:
        win_chat.insch(y, x, ch, attr)


cpdef void draw_chat(
    object win_chat,
    int h, int w,
    list chat_buffer,
    list chat_format,
    int chat_index,
    int chat_selected,
    list attrib_map,
    int color_default,
):
    cdef int num, pos
    cdef int line_idx
    cdef object line, line_format, format_part
    cdef int default_color_id
    cdef unsigned int color, color_ready
    cdef object character, format_slice
    cdef int start, end
    cdef int fill_len

    cdef int y = h

    # drawing from down to up
    chat_format = chat_format[chat_index:]
    for num in range(len(chat_buffer) - chat_index):
        line_idx = chat_index + num
        if line_idx >= len(chat_buffer):
            break
        y = h - (num + 1)
        if y < 0 or y >= h:
            break

        line = chat_buffer[line_idx]
        if num == chat_selected - chat_index:
            fill_len = w - len(line)
            win_chat.insstr(y, 0, line + (" " * fill_len) + "\n", curses.color_pair(16))
        else:
            line_format = chat_format[num]
            default_color_id = line_format[0][0]
            # filled with spaces so background is drawn all the way
            default_color = curses.color_pair(default_color_id) | attrib_map[default_color_id]
            win_chat.insstr(y, 0, " " * w + "\n", curses.color_pair(default_color_id))

            for pos in range(min(len(line), w)):
                character = line[pos]
                for format_part in line_format[1:]:
                    color = format_part[0]
                    start = format_part[1]
                    end = format_part[2]
                    if start <= pos < end:
                        # assuming never to have id > 65536, if value is that large its definitely attribute
                        if color >= 0x00010000:
                            # using base color because it is in message content anyway
                            color_ready = curses.color_pair(default_color_id) | color
                        else:
                            if color > 255:   # set all colors after 255 to default color
                                color = color_default
                            color_ready = (<unsigned int>curses.color_pair(color)) | (<unsigned int>attrib_map[color])
                        safe_insch(win_chat, y, pos, character, color_ready)
                        break
                else:
                    safe_insch(win_chat, y, pos, character, default_color)

    # fill empty lines with spaces so background is drawn all the way
    y -= 1
    while y >= 0:
        win_chat.insstr(y, 0, "\n", curses.color_pair(0))
        y -= 1
