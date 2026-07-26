# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

# cython: boundscheck=False, wraparound=False, freethreading_compatible=True

from libc.stdlib cimport malloc

cdef Py_ssize_t wide_count
cdef int* wide_starts
cdef int* wide_ends

cpdef void init_wide_ranges(tuple ranges):
    cdef Py_ssize_t i
    global wide_count, wide_starts, wide_ends
    wide_count = len(ranges)
    wide_starts = <int*>malloc(wide_count * sizeof(int))
    wide_ends   = <int*>malloc(wide_count * sizeof(int))
    for i in range(wide_count):
        wide_starts[i] = ranges[i][0]
        wide_ends[i] = ranges[i][1]


cdef inline bint binary_search(int codepoint) noexcept:
    cdef Py_ssize_t low = 0
    cdef Py_ssize_t high = wide_count - 1
    cdef Py_ssize_t mid

    if codepoint < wide_starts[0]:
        return False
    if codepoint > wide_ends[high]:
        return False

    while low <= high:
        mid = (low + high) >> 1
        if codepoint < wide_starts[mid]:
            high = mid - 1
        else:
            low = mid + 1

    return high >= 0 and codepoint <= wide_ends[high]


cpdef limit_width_wch(str text, int max_width):
    cdef int total_width = 0
    cdef int codepoint, char_width
    cdef Py_ssize_t i, n = len(text)

    for i in range(n):
        codepoint = ord(text[i])
        if 0x20 <= codepoint < 0x7f:
            char_width = 1
        else:
            char_width = 1 + binary_search(codepoint)
        if total_width + char_width > max_width:
            return text[:i], total_width
        total_width += char_width

    return text, total_width


cpdef len_wch(str text):
    cdef int total_width = 0
    cdef int codepoint
    cdef Py_ssize_t i, n = len(text)

    for i in range(n):
        codepoint = ord(text[i])
        if 0x20 <= codepoint < 0x7f:
            total_width += 1
        else:
            total_width += 1 + binary_search(codepoint)

    return total_width


cpdef is_wch(str ch):
    cdef int codepoint = ord(ch)
    if 0x20 <= codepoint < 0x7f:
        return False
    return binary_search(codepoint)


cpdef Py_ssize_t split_index_wch(str text, int max_width):
    cdef int width = 0
    cdef int codepoint
    cdef Py_ssize_t i, n = len(text)
    cdef int w

    for i in range(n):
        codepoint = ord(text[i])
        if 0x20 <= codepoint < 0x7f:
            w = 1
        else:
            w = 1 + binary_search(codepoint)
        if width + w > max_width:
            return i
        width += w

    return n


cdef inline Py_ssize_t bisect_left_c(list arr, Py_ssize_t x):
    # custom bisect_left to avoid python binding call
    cdef Py_ssize_t low = 0
    cdef Py_ssize_t high = len(arr)
    cdef Py_ssize_t mid

    while low < high:
        mid = (low + high) >> 1
        if <Py_ssize_t>arr[mid] < x:
            low = mid + 1
        else:
            high = mid

    return low


cpdef list fix_line_format(list line_format, str text):
    cdef list wide_positions, corrected
    cdef Py_ssize_t i, pos
    cdef Py_ssize_t start, end, start_shift, end_shift
    cdef int color, codepoint
    cdef object ch

    if len(line_format) <= 1:
        return line_format

    wide_positions = []
    for i, ch in enumerate(text):
        codepoint = ord(ch)
        if (codepoint < 0x20 or codepoint >= 0x7f) and binary_search(codepoint):
            wide_positions.append(i)
    if not wide_positions:
        return line_format

    corrected = [line_format[0]]
    for color, start, end in line_format[1:]:
        start_shift = bisect_left_c(wide_positions, start)
        end_shift = bisect_left_c(wide_positions, end)
        corrected.append([color, start + start_shift, end + end_shift])

    return corrected


cpdef list fix_map_ranges(list map_ranges, str text):
    cdef list wide_positions, corrected
    cdef Py_ssize_t i, pos
    cdef Py_ssize_t start, end, start_shift, end_shift
    cdef int codepoint
    cdef object ch
    cdef object data

    if not map_ranges:
        return map_ranges

    wide_positions = []
    for i, ch in enumerate(text):
        codepoint = ord(ch)
        if (codepoint < 0x20 or codepoint >= 0x7f) and binary_search(codepoint):
            wide_positions.append(i)
    if not wide_positions:
        return map_ranges

    corrected = []
    for start, end, data in map_ranges:
        start_shift = bisect_left_c(wide_positions, start)
        end_shift = bisect_left_c(wide_positions, end)
        corrected.append([start + start_shift, end + end_shift, data])

    return corrected


from cpython.unicode cimport PyUnicode_New, PyUnicode_WriteChar

def replace_wide(str text, str replacement):
    if not text:
        return ""

    cdef Py_ssize_t length = len(text)
    cdef Py_ssize_t i
    cdef int codepoint
    cdef int rep_codepoint = 0
    cdef str result = PyUnicode_New(length, 1114111)
    if replacement:
        rep_codepoint = ord(replacement)

    for i in range(length):
        codepoint = text[i]
        if codepoint < 0x20 or codepoint >= 0x7f:
            PyUnicode_WriteChar(result, i, codepoint)
        if binary_search(codepoint) and rep_codepoint:
            PyUnicode_WriteChar(result, i, rep_codepoint)
        else:
            PyUnicode_WriteChar(result, i, codepoint)
    return result
