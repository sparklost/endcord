# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

# cython: boundscheck=False, wraparound=False, freethreading_compatible=True

import curses
import threading
cimport cython


cpdef inline bint in_any_range(int x, list ranges):
    """Check if x is in any of given ranges"""
    cdef tuple r
    cdef int a, b
    for r in ranges:
        a = r[0]
        b = r[1]
        if a <= x <= b:
            return True
    return False


cpdef void draw_chat(
    object win_chat,
    int h, int w,
    list chat_buffer,
    list chat_format,
    int chat_index,
    int chat_selected,
    list attrib_map,
    int color_default,
    list exclude_selection,
):
    cdef int num, pos
    cdef int line_idx
    cdef object line, line_format, format_part
    cdef int default_color_id
    cdef unsigned int color, color_ready
    cdef object character, format_slice
    cdef int start, end
    cdef int fill_len
    cdef bint selected

    cdef int y = h

    for num in range(len(chat_buffer) - chat_index):
        line_idx = chat_index + num
        if line_idx >= len(chat_buffer):
            break
        y = h - (num + 1)
        if y < 0 or y >= h:
            break

        line = chat_buffer[line_idx]
        selected = num == chat_selected - chat_index and not in_any_range(chat_selected, exclude_selection)
        line_format = chat_format[line_idx]
        default_color_id = line_format[0][0]

        default_color = curses.color_pair(default_color_id if not selected else 16) | attrib_map[default_color_id]
        win_chat.insstr(y, 0, (line[:w]).ljust(w), default_color)

        for format_part in line_format[1:]:
            color = format_part[0]
            start = format_part[1]
            end = min(format_part[2], w)
            if start >= end:
                continue
            if color >= 0x00010000:
                color_ready = (<unsigned int>curses.color_pair(default_color_id if not selected else 16)) | color
            else:
                if color > 255:
                    color = color_default
                color_ready = (<unsigned int>curses.color_pair(color if not selected else 16)) | (<unsigned int>attrib_map[color])
            win_chat.chgat(y, start, end - start, color_ready)

    y -= 1
    while y >= 0:
        win_chat.insstr(y, 0, "\n", curses.color_pair(0))
        y -= 1
