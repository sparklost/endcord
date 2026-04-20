# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import curses
import importlib.util
import sys

from endcord import xterm256

colors = xterm256.colors


def argmin(values):
    """Return index of smallest value in a list"""
    return min(range(len(values)), key=values.__getitem__)


def closest_color(rgb):
    """
    Find closest 8bit xterm256 color to provided rgb tuple color.
    Return ANSI code and rgb color.
    """
    r, g, b = rgb
    distances = []
    for i, (cr, cg, cb) in enumerate(colors):
        dr = r - cr
        dg = g - cg
        db = b - cb
        distances.append(dr*dr + dg*dg + db*db)   # doing it like this for better performance
    index = argmin(distances)
    return index, colors[index]


def int_to_rgb(int_color):
    """Convert integer color string to rgb tuple"""
    return (
        (int_color >> 16) & 255,   # r
        (int_color >> 8) & 255,   # g
        int_color & 255,   # b
    )


def hex_to_rgb(hex_str):
    """Convert hexadecimal color strin to rgb tuple"""
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i : i + 2], 16) for i in (0, 2, 4))


def convert_role_colors(all_roles, guild_id=None, role_id=None, default=-1):
    """
    For all roles, in all guilds, convert integer color format into rgb tuple color and closest 8bit ANSI color code.
    If ANSI code is 0, then use default color.
    Optionally update only one guild and/or one role.
    """
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
                ansi = closest_color(rgb)[0]
            role["color"] = ansi
            if role_id:
                break
        if guild_id:
            break

    return all_roles


# use cython if available, ~20 times faster
if importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.color"):
    from endcord_cython.color import convert_role_colors as convert_role_colors_cython
    def convert_role_colors(all_roles, guild_id=None, role_id=None, default=-1):
        """
        For all roles, in all guilds, convert integer color format into rgb tuple color and closest 8bit ANSI color code.
        If ANSI code is 0, then use default color.
        Optionally update only one guild and/or one role.
        """
        return convert_role_colors_cython(all_roles, colors, guild_id, role_id, default)


def check_color(color):
    """Check if color format is valid and repair it"""
    color_new = color[:]
    if color_new is None:
        return [-1, -1]
    if color_new[0] is None:
        color_new[0] = -1
    elif color_new[1] is None:
        color_new[1] = -1
    return color_new


def check_color_formatted(color_format):
    """
    Check if color format is valid and repair it.
    Replace -2 values for non-default colors with default for this format.
    """
    color_format_new = [row[:] for row in color_format] if color_format is not None else None
    if color_format_new is None:
        return [[-1, -1]]
    for color in color_format_new[1:]:
        if color[0] == -2:
            color[0] = color_format_new[0][0]
    return color_format_new


def extract_colors(config):
    """Extract simple colors from config if any value is None, default is used"""
    return (   # DO NOT CHANGE ORDER
        check_color(config["color_default"]),
        check_color(config["color_chat_mention"]),
        check_color(config["color_chat_blocked"]),
        check_color(config["color_chat_deleted"]),
        check_color(config["color_chat_pending"]),
        check_color(config["color_chat_separator"]),
        check_color(config["color_chat_code"]),
        check_color(config["color_chat_standout"]),
    )


def extract_colors_formatted(config):
    """Extract complex formatted colors from config"""
    return (   # DO NOT CHANGE ORDER
        check_color_formatted(config["color_format_message"]),   # 0 9
        check_color_formatted(config["color_format_newline"]),   # 1 10
        check_color_formatted(config["color_format_reply"]),   # 2 11
        check_color_formatted(config["color_format_reactions"]),   # 3 12
        check_color_formatted(config["color_format_interaction"]),   # 4 13
        # not complex but is here so it can be initialized for alt bg color
        [check_color(config["color_chat_edited"])],   # 5 14
        [check_color(config["color_chat_url"])],   # 6 15
        [check_color(config["color_chat_spoiler"])],   # 7 16
        check_color_formatted(config["color_format_forum"]),   # 8 17
    )


def color_palette_internal(screen):
    """Show all available colors and their codes, wait for input, then exit"""
    curses.use_default_colors()
    curses.curs_set(0)
    draw_bg = False
    while True:

        # drawing
        for i in range(0, curses.COLORS):
            if draw_bg:
                curses.init_pair(i, 232, i)
            else:
                curses.init_pair(i, i, -1)
        screen.addstr(1, 1, "Press Space to toggle fg/bg, any other key to close")
        h, w = screen.getmaxyx()
        x = 1
        y = 3
        for i in range(0, curses.COLORS):
            screen.addstr(y, x, str(i) + " " * (3 - len(str(i))), curses.color_pair(i))
            x += 5
            if x + 3 > w:
                y += 1
                x = 1
            if y >= h:
                break

        # ckeck key
        key_code = screen.getch()
        if key_code == 32:   # space
            draw_bg = not draw_bg
        else:
            break


def color_palette():
    """Show all available colors and their codes, wait for input, then exit"""
    try:
        curses.wrapper(color_palette_internal)
    except curses.error as e:
        if str(e) != "endwin() returned ERR":
            sys.exit("Curses error")
