# cython: boundscheck=False, wraparound=False

cdef inline int binary_search(int ucs, tuple ranges):
    cdef Py_ssize_t low = 0
    cdef Py_ssize_t high = len(ranges) - 1
    cdef Py_ssize_t mid
    cdef int start, end

    if ucs < ranges[0][0] or ucs > ranges[high][1]:
        return 0

    while low <= high:
        mid = (low + high) >> 1
        start, end = ranges[mid]

        if ucs > end:
            low = mid + 1
        elif ucs < start:
            high = mid - 1
        else:
            return 1

    return 0


cpdef limit_width_wch(str text, int max_width, tuple ranges):
    cdef int total_width = 0
    cdef int character, char_width
    cdef Py_ssize_t i, n = len(text)

    for i in range(n):
        character = ord(text[i])
        if 32 <= character < 0x7f:
            char_width = 1
        else:
            char_width = 1 + binary_search(character, ranges)
        if total_width + char_width > max_width:
            return text[:i], total_width
        total_width += char_width

    return text, total_width


cpdef len_wch(str text, tuple ranges):
    cdef int total_width = 0
    cdef int character
    cdef Py_ssize_t i, n = len(text)

    for i in range(n):
        character = ord(text[i])
        if 32 <= character < 0x7f:
            total_width += 1
        else:
            total_width += 1 + binary_search(character, ranges)

    return total_width
