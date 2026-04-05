import curses


def get_map():
    """
    Return map for conversion from unicode to ACS character code.
    Must be run after cusres.initscr()
    """
    return {
        "┌": curses.ACS_ULCORNER,
        "└": curses.ACS_LLCORNER,
        "┐": curses.ACS_URCORNER,
        "┘": curses.ACS_LRCORNER,
        "├": curses.ACS_LTEE,
        "┤": curses.ACS_RTEE,
        "┴": curses.ACS_BTEE,
        "┬": curses.ACS_TTEE,
        "─": curses.ACS_HLINE,
        "│": curses.ACS_VLINE,
        "┼": curses.ACS_PLUS,
        "⎺": curses.ACS_S1,
        "⎻": curses.ACS_S3,
        "⎼": curses.ACS_S7,
        "⎽": curses.ACS_S9,
        "◆": curses.ACS_DIAMOND,
        "°": curses.ACS_DEGREE,
        "±": curses.ACS_PLMINUS,
        "·": curses.ACS_BULLET,
        "←": curses.ACS_LARROW,
        "→": curses.ACS_RARROW,
        "↓": curses.ACS_DARROW,
        "↑": curses.ACS_UARROW,
        "▒": curses.ACS_BOARD,
        "␋": curses.ACS_LANTERN,
        "▮": curses.ACS_BLOCK,
        "≤": curses.ACS_LEQUAL,
        "≥": curses.ACS_GEQUAL,
        "π": curses.ACS_PI,
        "≠": curses.ACS_NEQUAL,
        "£": curses.ACS_STERLING,
    }
