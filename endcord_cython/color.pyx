# cython: boundscheck=False, wraparound=False

from libc.stdint cimport int16_t, int32_t


cpdef inline tuple closest_color(tuple colors, tuple rgb):
    cdef int r = rgb[0]
    cdef int g = rgb[1]
    cdef int b = rgb[2]
    cdef Py_ssize_t n = len(colors)
    cdef Py_ssize_t i
    cdef tuple c
    cdef int32_t dr, dg, db, dist
    cdef int32_t best_dist = 2147483647
    cdef Py_ssize_t best_idx = 0

    for i in range(n):
        c = colors[i]
        dr = r - <int> c[0]
        dg = g - <int> c[1]
        db = b - <int> c[2]
        dist = dr * dr + dg * dg + db * db

        if dist < best_dist:
            best_dist = dist
            best_idx = i

    return best_idx, colors[best_idx]


cpdef inline tuple int_to_rgb(int int_color):
    return (
        (int_color >> 16) & 255,
        (int_color >> 8) & 255,
        int_color & 255,
    )


cpdef list convert_role_colors(list all_roles, tuple colors, object guild_id, object role_id, int default):
    cdef dict guild
    cdef dict role
    cdef int color
    cdef tuple rgb
    cdef int ansi

    for guild in all_roles:
        if guild_id and guild["guild_id"] != guild_id:
            continue
        for role in guild["roles"]:
            if role_id and role["id"] != role_id:
                continue
            color = role["color"]
            if color == 0:
                ansi = default
            else:
                rgb = int_to_rgb(color)
                ansi = closest_color(colors, rgb)[0]
            role["color"] = ansi
            if role_id:
                break
        if guild_id:
            break

    return all_roles
