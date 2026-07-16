# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import curses
import logging
import os
import re
import sys
import threading
import time

from endcord import acs, keybinding, peripherals, utils

logger = logging.getLogger(__name__)
uses_pgcurses = hasattr(curses, "PGCURSES")
INPUT_LINE_JUMP = 20   # jump size when moving input line
MAX_DELTA_STORE = 50   # limit undo size
MIN_ASSIST_LETTERS = 2
ASSIST_TRIGGERS = ("#", "@", ":", ";")
ALLOWED_BEFORE_ASSIST = (" ", "\n", "+")
ALLOWED_BEFORE_ASSIST_EXTRA = (" ", "\n", "+", ":", "=")
APP_COMMAND_ASSIST_TRIEGGER = "/"
BUTTON4_PRESSED = getattr(curses, "BUTTON4_PRESSED", 0)
BUTTON5_PRESSED = getattr(curses, "BUTTON5_PRESSED", 0)
ALT_SPACE = "⠀"   # U+2800 - braille pattern blank
match_word = re.compile(r"\w")
match_split = re.compile(r"[^\w']")
match_spaces = re.compile(r" {3,}")


def ctrl(x):
    """Convert character code to ctrl-modified"""
    return x - 96


def resplit(text, pattern=r"[^\w']", diff=False):
    """Splits string to list of words using regex"""
    if not diff:
        return re.split(pattern, text)
    res = re.split(pattern, text)
    if res[0] == text:
        return [""]


def rersplit_0(text, pattern=match_split):
    """text.rsplit(pattern, 1)[0] equivalent in regex to split words"""
    for i in range(len(text) - 1, -1, -1):
        if re.match(pattern, text[i]):
            return text[:i]
    return text


def split_char_in(text):
    """Check if split character is in text"""
    return bool(re.search(match_split, text))


def set_list_item(input_list, item, index):
    """Replace existing item or build list if it doesnt exist"""
    if index >= len(input_list):
        input_list.extend([0] * (index - len(input_list) + 1))
    input_list[index] = item
    return input_list


def trim_with_dash(text, dash=True):
    """Trim spaces from a line and add '─' if there were spaces prefixed"""
    if dash and text and text[0] == " ":
        return "─" + text.strip()
    return text.strip()


def replace_spaces_dash(text):
    """Replace more than 3 spaces with ' ─ '"""
    return match_spaces.sub(lambda match: " " + ("─" * (len(match.group(0)) - 2)) + " ", text)


def safe_insch(screen, y, x, character, color):
    """
    Safely insert character into line.
    This is because curses.insch will throw exception for weird characters.
    curses.insstr will not, but is slower.
    """
    try:
        # cant insch weird characters, but this is faster than always calling insstr
        screen.insch(y, x, character, color)
    except (OverflowError, UnicodeEncodeError):
        screen.insstr(y, x, character, color)


def select_word(text, index):
    """Select word at index position"""
    if index < 0 or index >= len(text):
        return None, None
    start = index
    while start > 0 and re.match(match_word, text[start - 1]):
        start -= 1
    end = index
    while end < len(text) - 1 and re.match(match_word, text[end + 1]):
        end += 1
    return start, end


def draw_formatted_line(window, y, x, text, text_format, default_color, attrib_map, lock):
    """
    Draw single attributed line on a window, line is expected to have spaces to be filled to screen edge.
    text_format = [[color_id, attribute, start, end], ...]
    """
    with lock:
        if not text_format:
            window.insstr(y, x, text, curses.color_pair(default_color) | attrib_map[default_color])
            return
        try:
            window.insstr(y, x, text, curses.color_pair(default_color) | attrib_map[default_color])
            for format_part in text_format:
                if format_part[1] == 1:
                    attrib = curses.A_BOLD
                elif format_part[1] == 2:
                    attrib = curses.A_ITALIC
                elif format_part[1] == 3:
                    attrib = curses.A_UNDERLINE
                elif format_part[1] == 4:
                    attrib = curses.A_UNDERLINE | curses.A_ITALIC
                elif format_part[1] == 5:
                    attrib = curses.A_BOLD | curses.A_ITALIC
                else:
                    attrib = attrib_map[default_color]
                color = format_part[0]
                if color is None:
                    color = default_color
                window.chgat(y, x + format_part[2], format_part[3] - format_part[2], curses.color_pair(color) | attrib)
        except curses.error:
            # exception will happen when window is resized to smaller w dimensions
            pass   # some other draw function will call self.resize()


def draw_chat(win_chat, h, w, chat_buffer, chat_format, chat_index, chat_selected, attrib_map, color_default, exclude_selection):
    """Draw chat with applied color formatting"""
    y = h
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
        if num == chat_selected - chat_index and not any(a <= chat_selected <= b for a, b in exclude_selection):
            fill_len = w - len(line)
            win_chat.insstr(y, 0, line + (" " * fill_len) + "\n", curses.color_pair(16))
        else:
            line_format = chat_format[num]
            default_color_id = line_format[0][0]
            # filled with spaces so background is drawn all the way
            default_color = curses.color_pair(default_color_id) | attrib_map[default_color_id]
            win_chat.insstr(y, 0, (line[:w]).ljust(w) + "\n", default_color)
            for format_part in line_format[1:]:
                color = format_part[0]
                start = format_part[1]
                end = min(format_part[2], w)
                if start >= end:
                    continue
                # assuming never to have id > 65536, if value is that large its definitely attribute
                if color >= 0x00010000:
                    # using base color because it is in message content anyway
                    color_ready = curses.color_pair(default_color_id) | color
                else:
                    if color > 255:   # set all colors after 255 to default color
                        color = color_default
                    color_ready = curses.color_pair(color) | attrib_map[color]
                win_chat.chgat(y, start, end - start, color_ready)

    # fill empty lines with spaces so background is drawn all the way
    y -= 1
    while y >= 0:
        win_chat.insstr(y, 0, "\n", curses.color_pair(0))
        y -= 1


# use cython if available, ~1.5 times faster
try:
    # windows curses returns negative value from curses.color_pair() causing crash in draw_chat cython function
    from endcord_cython.tui import draw_chat
except ImportError:
    pass


class TUI():
    """Methods used to draw terminal user interface"""

    def __init__(self, screen, config, keybindings, command_bindings, draw_tree, draw_member_list):
        self.spellchecker = peripherals.SpellCheck(config["aspell_mode"], config["aspell_lang"])
        acs_map = acs.get_map()
        curses.use_default_colors()
        curses.curs_set(0)   # using custom cursor
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)
        sys.stdout.write("\x1b[?2004h")   # enable bracketed paste mode
        sys.stdout.write("\x1b[?1004h")   # enable terminal focus change reporting
        sys.stdout.flush()
        try:
            self.backspace_code = 8 if (curses.erasechar() == 8 or "XTERM_VERSION" in os.environ or sys.platform == "win32") else 127
        except Exception:
            self.backspace_code = 127
        self.fallback_keybinding_parser = config["fallback_keybinding_parser"] or sys.platform == "win32"
        if self.fallback_keybinding_parser:
            screen.keypad(True)
            self.get_key = keybinding.get_key_fallback
            self.backspace_code = 263 if self.backspace_code == 127 else 8
        else:
            screen.keypad(False)
            self.get_key = keybinding.get_key
        screen.clear()
        self.last_free_id = 1   # last free color pair id
        self.color_pairs = {}
        self.attrib_map = [0]   # has 0 so its index starts from 1 to be matched with color pairs
        self.protected_colors = 26   # first N colors that must not be reused
        self.init_pair((255, -1))   # white on default
        self.init_pair((233, 255))   # black on white
        self.init_pair(config["color_tree_default"])   # 3
        self.init_pair(config["color_tree_selected"])
        self.init_pair(config["color_tree_muted"])
        self.init_pair(config["color_tree_active"])   # 6
        self.init_pair(config["color_tree_unseen"])
        self.init_pair(config["color_tree_mentioned"])
        self.init_pair(config["color_tree_active_mentioned"])   # 9
        self.init_pair(config["color_misspelled"])
        self.init_pair(config["color_extra_line"])
        self.init_pair(config["color_title_line"])   # 12
        self.init_pair(config["color_prompt"])
        self.init_pair(config["color_input_line"])
        self.init_pair(config["color_cursor"])   # 15
        self.init_pair(config["color_chat_selected"])
        self.init_pair(config["color_status_line"])
        self.init_pair((config["color_green"][0], -1))   # 18
        self.init_pair((config["color_orange"][0], -1))
        self.init_pair((config["color_red"][0], -1))
        self.init_pair(config["color_extra_window"])   # 21
        self.init_pair(config["color_tree_selected_mentioned"])
        self.init_pair(config["color_subtitle_line"])
        self.init_pair(config["color_tree_category"])   # 24
        self.init_pair(config["color_tree_server"])
        self.init_pair(config["color_scrollbar"])
        curses.init_pair(255, config["color_default"][0], config["color_default"][1])   # temporary
        self.default_color = 255
        self.tree_unread_inherit_color = config["color_tree_unseen"][0] == -2 and config["color_tree_unseen"][1] == -2
        self.role_color_start_id = self.last_free_id   # starting id for role colors
        self.keybindings = keybindings
        self.key_map = keybinding.build_key_map(keybindings)
        self.switch_tab_modifier = keybindings["switch_tab_modifier"][0][:-3]
        self.command_bindings = command_bindings
        self.screen = screen
        self.extensions = []

        # load config
        self.bordered = not (config["compact"])
        self.have_title = bool(config["format_title_line_l"])
        self.have_title_tree = bool(config["format_title_tree"])
        vline = config["tree_drop_down_vline"][0]
        self.vline = acs_map.get(vline, vline)
        self.tree_width_conf = max(config["tree_width"], 10)
        self.tree_width = 1 if not draw_tree else max(config["tree_width"], 10)
        self.member_list_width_conf = config["member_list_width"]
        self.member_list_width = 2 if not draw_member_list else max(config["member_list_width"], 5)
        self.extra_window_h = config["extra_window_height"]   # load initial value
        self.blink_cursor_on = config["cursor_on_time"]
        self.blink_cursor_off = config["cursor_off_time"]
        self.enable_blink_cursor = bool(self.blink_cursor_on) and bool(self.blink_cursor_off)
        self.tree_dm_status = config["tree_dm_status"]
        self.assist = config["assist"]
        self.wrap_around = config["wrap_around"]
        self.mouse = config["mouse"]
        self.screen_update_delay = min(config["screen_update_delay"], 0.01)
        self.mouse_scroll_sensitivity = min(max(1, config["mouse_scroll_sensitivity"]), 10)
        self.corner_ul = config["border_corners"][0]
        self.corner_ur = config["border_corners"][2]
        self.corner_dl = config["border_corners"][1]
        self.corner_dr = config["border_corners"][3]
        self.have_scrollbar = config["draw_scrollbar"] and self.bordered
        self.scrollbar_char = config["scrollbar_character"]
        self.fine_scrollbar = self.scrollbar_char == "┃" and config["color_scrollbar"] == [-1, -1]   # can be drawn at double resolution with this character
        self.hline = config["tree_drop_down_hline"]
        self.tab_spaces = int(config["tab_spaces"])
        self.vim_mode = config["vim_mode"]
        self.swap_assist = config["assist_swap_binding"]

        # select bordered method
        if self.bordered:
            self.resize = self.resize_bordered

        # select mouse scroll method
        if not config["mouse_scroll_selection"]:
            self.mouse_scroll = self.mouse_scroll_content

        # find all keybinding first-chain-parts
        self.chainable = keybinding.find_chainable(keybindings, self.command_bindings)

        # initial values
        self.disable_drawing = False
        self.prompt = "> "
        self.input_buffer = ""
        self.status_txt_l = ""
        self.status_txt_r = ""
        self.status_txt_l_format = []
        self.status_txt_r_format = []
        self.title_txt_l = ""
        self.title_txt_r = ""
        self.title_txt_l_format = []
        self.title_txt_r_format = []
        self.subtitle_txt = ""
        self.subtitle_format = []
        self.title_tree_txt = ""
        self.chat_buffer = []
        self.chat_format = []
        self.tree = []
        self.tree_format = []
        self.tree_clean_len = 0
        self.chat_selected = -1   # hidden selection by default
        self.tree_selected = -1
        self.dont_hide_chat_selection = False
        self.tree_selected_abs = -1
        self.chat_index = 0   # chat scroll index
        self.tree_index = 0
        self.chat_scrolled_top = False
        self.tree_format_changed = False
        self.input_index = 0   # index of input cursor
        self.input_line_index = 0   # index of input line, when moving it to left
        self.cursor_pos = 0   # on-screen position of cursor
        self.cursor_on = True
        self.enable_autocomplete = False
        self.spelling_range = [0, 0]
        self.misspelled = []
        self.delta_store = []
        self.last_action = None
        self.delta_cache = ""
        self.delta_index = 0
        self.undo_index = None
        self.input_select_start = None
        self.input_select_end = None
        self.input_select_text = ""
        self.typing = time.time()
        self.extra_line_text = ""
        self.extra_line_format = []
        self.extra_window_title = ""
        self.extra_window_body = []
        self.extra_window_format = []
        self.member_list = []
        self.member_list_format = []
        self.member_list_title_text = ""
        self.red_list = []
        self.extra_selected = -1
        self.extra_index = 0
        self.extra_select = False
        self.mlist_selected = -1
        self.mlist_index = 0
        self.fun = 0
        self.fun_thread = None
        self.run = True
        self.win_subtitle_line = None
        self.win_extra_line = None
        self.win_extra_window = None
        self.win_member_list = None
        self.win_prompt = None
        self.keybinding_chain = None
        self.assist_start = -1
        self.instant_assist = False
        self.first_click = (0, 0, 0)
        self.mouse_rel_x = None
        self.wrap_around_disable = False
        self.focused = True
        self.pressed_num_key = None
        self.insert_mode = not self.vim_mode   # leave it true to enable input
        self.inline_media = None

        # lock for thread-safe drawing with curses
        self.lock = threading.RLock()

        # start drawing
        self.need_update = threading.Event()
        self.screen_update_thread = threading.Thread(target=self.screen_update, daemon=True)
        self.screen_update_thread.start()

        self.resize()

        if self.enable_blink_cursor:
            self.blink_cursor_thread = threading.Thread(target=self.blink_cursor, daemon=True)
            self.blink_cursor_thread.start()
        self.need_update.set()


    def load_inline_media(self, inline_media):
        """Pass inline media drawer reference from app to tui"""
        self.inline_media = inline_media


    def load_extensions(self, extensions):
        """Load already initialized extensions from app class and load chain parts for chainable bindings"""
        self.extensions = extensions
        self.extension_cache = []
        for extension in self.extensions:
            method = getattr(extension, "init_bindings", None)
            if not callable(method):
                continue
            new_bindings = method(self.keybindings)
            if not isinstance(new_bindings, dict):
                continue
            for binding in new_bindings.values():
                if not isinstance(binding, str):
                    continue
                split_binding = binding.split(" ")
                if len(split_binding) == 1:
                    continue
                elif len(split_binding) > 2:
                    logger.warn(f"Invalid keybinding: {binding}")
                if split_binding[0] not in self.chainable:
                    self.chainable.append(split_binding[0])
        del self.keybindings   # not needed anymore


    def execute_extensions_methods(self, method_name, *args, cache=False):
        """Execute specific method for each extension if extension has this method, and chain them"""
        if not self.extensions:
            return args

        # try to load from cache (improves performance with many extensions)
        if cache:
            for extension_point in self.extension_cache:
                data = args
                if extension_point[0] == method_name:
                    for method in extension_point[1]:
                        result = method(*data)
                        if result is not None:
                            if not isinstance(result, tuple):
                                result = (result, )
                            data = result
                    if data is not None:
                        return data
                    return args

        # try to load method from extensions and add to cache
        methods = []
        data = args
        for extension in self.extensions:
            method = getattr(extension, method_name, None)
            if callable(method):
                if cache:
                    methods.append(method)
                result = method(*data)
                if result is not None:
                    if not isinstance(result, tuple):
                        result = (result, )
                    data = result
        if cache:
            self.extension_cache.append((method_name, methods))
        if data is not None:
            return data
        return args


    def execute_extensions_method_first(self, method_name, *args, cache=False):
        """Execute specific method for each extension if extension has this method, without chaining, stop on first run extension"""
        if not self.extensions:
            return args

        # try to load from cache (improves performance with many extensions)
        if cache:
            result = False
            for extension_point in self.extension_cache:
                if extension_point[0] == method_name:
                    for method in extension_point[1]:
                        result = method(*args)
                        if result:
                            return result

        # try to load method from extensions and add to cache
        result = False
        methods = []
        for extension in self.extensions:
            method = getattr(extension, method_name, None)
            if callable(method):
                if cache:
                    methods.append(method)
                result = method(*args)
                if result:
                    break
        if cache:
            self.extension_cache.append((method_name, methods))
        return result


    def stop(self):
        """Stop all threads and restore terminal"""
        sys.stdout.write("\x1b[?2004l")   # disable bracketed paste mode
        sys.stdout.write("\x1b[?1004l")   # disable terminal focus change reporting
        self.run = False
        self.need_update.set()


    def screen_update(self):
        """Thread that updates drawn content on physical screen"""
        while self.run:
            self.need_update.wait()
            # here must be delay, otherwise output gets messed up
            with self.lock:
                time.sleep(self.screen_update_delay)
                curses.doupdate()
                self.need_update.clear()


    def resize(self, redraw_only=False):
        """Resize screen area and redraw ui"""
        if self.disable_drawing:
            return

        # re-init areas
        if not redraw_only:
            h, w = self.screen.getmaxyx()
            chat_hwyx = (
                h - 2 - self.have_title - bool(self.win_subtitle_line),
                w - (self.tree_width + 1),
                self.have_title + bool(self.win_subtitle_line),
                self.tree_width + 1,
            )
            prompt_hwyx = (1, len(self.prompt), h - 1, self.tree_width + 1)
            input_line_hwyx = (1, w - (self.tree_width + 1) - len(self.prompt), h - 1, self.tree_width + len(self.prompt) + 1)
            status_line_hwyx = (1, w - (self.tree_width + 1), h - 2, self.tree_width + 1)
            tree_hwyx = (h - self.have_title_tree, self.tree_width, self.have_title, 0)
            self.win_chat = self.screen.derwin(*chat_hwyx)
            self.win_prompt = self.screen.derwin(*prompt_hwyx)
            self.win_input_line = self.screen.derwin(*input_line_hwyx)
            self.win_status_line = self.screen.derwin(*status_line_hwyx)
            self.win_tree = self.screen.derwin(*tree_hwyx)
            if self.have_title:
                title_line_hwyx = (1, w - (self.tree_width + 1), 0, self.tree_width + 1)
                self.win_title_line = self.screen.derwin(*title_line_hwyx)
                self.title_hw = self.win_title_line.getmaxyx()
            if self.have_title_tree:
                tree_title_line_hwyx = (1, self.tree_width, 0, 0)
                self.win_title_tree = self.screen.derwin(*tree_title_line_hwyx)
                self.tree_title_hw = self.win_title_tree.getmaxyx()
            self.screen_hw = self.screen.getmaxyx()
            self.chat_hw = self.win_chat.getmaxyx()
            self.prompt_hw = self.win_prompt.getmaxyx()
            self.input_hw = self.win_input_line.getmaxyx()
            self.status_hw = self.win_status_line.getmaxyx()
            self.tree_hw = self.win_tree.getmaxyx()
            self.win_extra_line = None
            self.win_extra_window = None
            self.win_member_list = None

        # redraw
        with self.lock:
            self.screen.vline(0, self.tree_hw[1], self.vline, self.screen_hw[0], curses.color_pair(self.default_color))
            if self.have_title and self.have_title_tree:
                # fill gap between titles
                self.screen.addch(0, self.tree_hw[1], self.vline, curses.color_pair(12))
            self.screen.noutrefresh()
            self.need_update.set()
        self.draw_status_line()
        self.update_prompt(self.prompt)
        self.spellcheck()
        self.draw_input_line()
        self.draw_tree()
        if self.have_title:
            self.draw_title_line()
        self.draw_subtitle_line()
        if self.have_title_tree:
            self.draw_title_tree()
        self.draw_extra_line(self.extra_line_text, self.extra_line_format)
        self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
        self.draw_member_list(self.member_list, self.member_list_format, force=True)
        self.draw_chat()


    def resize_bordered(self, redraw_only=False):
        """Resize screen area and redraw ui in bordered mode"""
        if self.disable_drawing:
            return

        h, w = self.screen.getmaxyx()
        chat_hwyx = (
            h - 4 - self.have_title - bool(self.win_subtitle_line),
            w - (self.tree_width + 4) - bool(self.member_list),
            self.have_title + bool(self.win_subtitle_line),
            self.tree_width + 3,
        )
        win_prompt_input_line = (1, w - self.tree_width - 4, h - 2, self.tree_width + 3)
        tree_hwyx = (h - self.have_title_tree - 1, self.tree_width, self.have_title, 1)

        # re-init areas
        if not redraw_only:
            prompt_hwyx = (1, len(self.prompt), h - 2, self.tree_width + 2)
            input_line_hwyx = (
                1,
                w - (self.tree_width + 1) - len(self.prompt) - 2,
                h - 2,
                self.tree_width + len(self.prompt) + 2,
            )
            status_line_hwyx = (1, w - (self.tree_width + 2), h - 3, self.tree_width + 2)
            self.win_chat = self.screen.derwin(*chat_hwyx)
            self.win_prompt = self.screen.derwin(*prompt_hwyx)
            self.win_input_line = self.screen.derwin(*input_line_hwyx)
            self.win_status_line = self.screen.derwin(*status_line_hwyx)
            self.win_tree = self.screen.derwin(*tree_hwyx)
            if self.have_title:
                title_line_hwyx = (1, w - (self.tree_width + 2) - bool(self.win_member_list) * (self.member_list_width + 1), 0, self.tree_width + 2)
                self.win_title_line = self.screen.derwin(*title_line_hwyx)
                self.title_hw = self.win_title_line.getmaxyx()
            if self.have_title_tree:
                tree_title_line_hwyx = (1, self.tree_width + 2, 0, 0)
                self.win_title_tree = self.screen.derwin(*tree_title_line_hwyx)
                self.tree_title_hw = self.win_title_tree.getmaxyx()
            self.screen_hw = self.screen.getmaxyx()
            self.chat_hw = self.win_chat.getmaxyx()
            self.prompt_hw = self.win_prompt.getmaxyx()
            self.input_hw = self.win_input_line.getmaxyx()
            self.status_hw = self.win_status_line.getmaxyx()
            self.tree_hw = self.win_tree.getmaxyx()
            self.win_extra_line = None
            self.win_extra_window = None
            self.win_member_list = None

        # redraw
        self.draw_border(win_prompt_input_line, top=False)
        self.draw_status_line()
        self.draw_border(chat_hwyx, top=not (self.have_title), right=not (self.have_scrollbar))
        self.update_prompt(self.prompt)
        self.spellcheck()
        self.draw_input_line()
        self.draw_border(tree_hwyx, top=not (self.have_title_tree) or self.tree_width < 10)
        self.draw_tree()
        if self.have_title:
            self.draw_title_line()
        self.draw_subtitle_line()
        if self.have_title_tree:
            self.draw_title_tree()
        self.draw_extra_line(self.extra_line_text, self.extra_line_format)
        self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
        if self.win_extra_window:   # redraw borders for extra window
            extra_window_hwyx = self.win_extra_window.getmaxyx() + self.win_extra_window.getbegyx()
            self.draw_border(extra_window_hwyx, top=False, bot=False)
            y, x = extra_window_hwyx[2], extra_window_hwyx[3]
            self.screen.addstr(y, x - 1, self.corner_ul, curses.color_pair(self.default_color))
            self.screen.addstr(y, x + extra_window_hwyx[1], self.corner_ur, curses.color_pair(self.default_color))
        self.draw_member_list(self.member_list, self.member_list_format, force=True)
        self.draw_chat()
        self.screen.noutrefresh()
        self.need_update.set()


    def force_redraw(self):
        """Forcibly redraw entire screen"""
        self.screen.clear()
        self.screen.redrawwin()
        if sys.platform == "win32":
            self.screen.noutrefresh()   # ??? needed only with windows-curses
            self.need_update.set()
        self.resize()
        if self.inline_media:
            self.inline_media.force_redraw()
        self.execute_extensions_methods("on_force_redraw")


    def init_chat(self):
        """Initialize chat window"""
        h, w = self.screen.getmaxyx()
        if self.win_extra_window:
            common_h = h - 3 - self.have_title - 2*self.bordered - self.extra_window_h
        elif self.win_extra_line:
            common_h = h - 3 - self.have_title - 2*self.bordered
        else:
            common_h = h - 2 - self.have_title - 2*self.bordered
        chat_hwyx = (
            common_h - bool(self.win_subtitle_line),
            w - (self.tree_width + 3 * self.bordered + 1) - bool(self.member_list) * (self.member_list_width + 1),
            self.have_title + bool(self.win_subtitle_line),
            self.tree_width + 2 * self.bordered + 1,
        )
        self.win_chat = self.screen.derwin(*chat_hwyx)
        self.chat_hw = self.win_chat.getmaxyx()
        if self.bordered:
            self.draw_border(chat_hwyx, top=not (self.have_title), right=not (self.have_scrollbar))
            self.screen.noutrefresh()
        return common_h


    def pause_curses(self):
        """Pause curses and disable drawing, releasing terminal"""
        if self.win_member_list:
            with self.lock:
                h, w = self.win_member_list.getmaxyx()
                for y in range(h):
                    self.win_member_list.insstr(y, 0, " " * w, curses.color_pair(1))
                self.win_member_list.noutrefresh()
                self.need_update.set()
        self.disable_drawing = True
        curses.ungetch(27)
        self.hibernate_cursor = 10
        time.sleep(0.2)   # be sure everything is stopped before pausing
        with self.lock:
            curses.def_prog_mode()
            curses.endwin()
            sys.stdout.write("\x1b[?2004l")   # disable bracketed paste mode
            sys.stdout.write("\x1b[?1004l")   # disable terminal focus change reporting
            sys.stdout.flush()


    def resume_curses(self):
        """Resume curses and enable drawing, capturing terminal"""
        with self.lock:
            curses.reset_prog_mode()
            curses.curs_set(0)
            curses.flushinp()
            sys.stdout.write("\x1b[?2004h")   # disable bracketed paste mode
            sys.stdout.write("\x1b[?1004h")   # enable terminal focus change reporting
            sys.stdout.flush()
            self.screen.refresh()
            self.disable_drawing = False
            self.resize(redraw_only=True)
            if self.inline_media:
                self.inline_media.force_redraw()
            self.execute_extensions_methods("on_force_redraw")


    def get_dimensions(self):
        """Return current dimensions for screen objects"""
        status_line = self.win_status_line.getmaxyx()
        return (
            tuple(self.win_chat.getmaxyx()),
            tuple(self.win_tree.getmaxyx()),
            (status_line[0], status_line[1] - 2 * self.bordered),
        )

    def get_chat_selected(self):
        """Return index of currently selected line and how much text has been scrolled"""
        return self.chat_selected, self.chat_index


    def get_tree_selected(self):
        """Return index of currently selected tree line"""
        return self.tree_selected_abs


    def get_extra_selected(self):
        """Return index of selected line in extra window"""
        return self.extra_selected


    def get_mlist_selected(self):
        """Return index of selected line in member list"""
        return self.mlist_selected


    def get_my_typing(self):
        """Return whether it has been typed in past 3s"""
        if time.time() - self.typing > 3:
            return None
        return True


    def get_tree_format(self):
        """Return tree format if it has been changed"""
        if self.tree_format_changed:
            self.tree_format_changed = False
            return self.tree_format
        return None


    def get_clicked_chat(self):
        """Get index of clicked line in chat buffer and x coordinate"""
        return self.chat_selected, self.mouse_rel_x


    def get_x_line_clicked(self):
        """Get clicked x coordinate of some line"""
        return self.mouse_rel_x


    def get_chat_scrolled_top(self):
        """Check whether chat scrolling hit the top end"""
        return self.chat_scrolled_top


    def reset_chat_scrolled_top(self):
        """Force reset state of chat scrolling hit the top end"""
        self.chat_scrolled_top = False


    def get_assist(self, extended=False):
        """
        Return word to be assisted with completing and type of assist needed
        Assist types:
        1 - channel
        2 - username/role
        3 - emoji
        4 - sticker
        5 - client command
        6 - app command
        7 - upload file select
        100 - stop assist
        """
        if self.assist_start >= 0:
            if self.assist_start < self.input_index - (MIN_ASSIST_LETTERS - 1):
                if (
                    self.assist_start != -1 and
                    self.assist_start < len(self.input_buffer) and
                    self.input_buffer[self.assist_start-1] in ASSIST_TRIGGERS
                ):
                    assist_type = ASSIST_TRIGGERS.index(self.input_buffer[self.assist_start-1]) + 1
                    before_assist = ALLOWED_BEFORE_ASSIST_EXTRA if extended else ALLOWED_BEFORE_ASSIST
                    if self.assist_start != 1 and (self.input_buffer[self.assist_start-2] not in before_assist or self.input_buffer[self.assist_start] in (" ", "\n")):
                        # skip trigger if no space before it
                        if self.instant_assist:
                            return self.input_buffer, 5
                        return None, None
                    assist_word = self.input_buffer[self.assist_start : self.input_index]
                    return assist_word, assist_type
                self.assist_start = -1
                return None, 100
            if self.assist_start > self.input_index:
                self.assist_start = -1
                return None, 100
        if self.enable_autocomplete and self.input_buffer:
            return self.input_buffer, 7
        if self.input_buffer and self.input_buffer[0] == APP_COMMAND_ASSIST_TRIEGGER:
            return self.input_buffer, 6
        if self.instant_assist:
            return self.input_buffer, 5
        return None, None


    def get_last_free_color_id(self):
        """Return last free color id. Should be run at the end of all color initialization in endcord.tui."""
        return self.last_free_id


    def get_focused(self):
        """Get whether chat is focused or not"""
        if uses_pgcurses:
            return curses.focused
        return self.focused and not self.disable_drawing


    def set_selected(self, selected, change_amount=0, scroll=True, draw=True):
        """Set selected line and text scrolling"""
        up = self.chat_selected >= selected
        self.chat_selected = selected
        if scroll:
            if self.chat_selected == -1:
                self.chat_index = 0
            elif change_amount and self.chat_index:
                self.chat_index += change_amount
            on_screen_h = selected - self.chat_index
            if on_screen_h > self.chat_hw[0] - 3 or on_screen_h < 3:
                if up:
                    self.chat_index = max(selected - self.chat_hw[0] + 3, 0)
                else:
                    self.chat_index = max(selected - 3, 0)
        elif change_amount and self.chat_index:
            self.chat_index += change_amount
        if not self.disable_drawing and draw:
            self.draw_chat()
            if self.inline_media:
                self.inline_media.draw_selection(self.chat_selected)


    def set_chat_index(self, index):
        """Set chat index, used to trigger unseen scrolled"""
        self.chat_index = index
        if not self.disable_drawing:
            self.draw_chat()


    def set_input_index(self, index):
        """Set cursor position on input line"""
        self.input_index = index
        _, w = self.input_hw
        self.cursor_pos = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
        self.cursor_pos = max(self.cursor_pos, 0)
        self.cursor_pos = min(w - 1, self.cursor_pos)
        self.input_select_start = None
        self.show_cursor()


    def paste_text(self, text):
        """Paste text at current index in input line"""
        if self.disable_drawing:
            return
        if self.input_select_start is not None:
            self.delete_selection()
            self.input_select_start = None
        self.input_buffer = self.input_buffer[:self.input_index] + text + self.input_buffer[self.input_index:]
        self.input_index += len(text)
        self.typing = int(time.time())
        for key in text:
            self.add_to_delta_store(key)
        self.show_cursor()
        self.spellcheck()
        self.draw_input_line()


    def set_tray_icon(self, icon=0):
        """
        Set tray icon, used only for non-terminal UI mode
        available icons:
        0 - default
        1 - unreads
        2 - mention
        """
        if uses_pgcurses:
            curses.set_tray_icon(icon)


    def set_extra_height(self, value):
        """Set extra window height to number or +1/-1"""
        h, _ = self.screen_hw
        if value == 1:
            self.extra_window_h += 1
        elif value == -1:
            self.extra_window_h -= 1
        else:
            self.extra_window_h = value
        self.extra_window_h = max(min(self.extra_window_h, int(2*h/3)), 3)
        del self.win_extra_window
        self.win_extra_window = None
        self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)


    def set_tree_width(self, value):
        """Change tree width, toggle state if value is negative"""
        if value < 0:
            if self.tree_width == 1:
                self.tree_width = self.tree_width_conf
            else:
                self.tree_width = 1
        else:
            self.tree_width = value
        if self.inline_media:
            self.inline_media.clear_images(force=True)
        self.resize()


    def set_member_list_width(self, value):
        """Change member list width, toggle state if value is negative"""
        if value < 0:
            if self.member_list_width == 2:
                self.member_list_width = self.member_list_width_conf
            else:
                self.member_list_width = 2
        else:
            self.member_list_width = value
        if self.inline_media:
            self.inline_media.clear_images(force=True)
        if not self.win_member_list:
            self.draw_member_list([" "], [None])
        self.resize()


    def set_vim_insert(self, value):
        """Set insert mode for vim mode"""
        if self.vim_mode:
            self.insert_mode = value


    def set_fun(self, fun_lvl):
        """Set fun level"""
        if fun_lvl != self.fun and self.fun_thread:
            self.fun = fun_lvl
            self.fun_thread.join(timeout=0.35)
        self.fun = fun_lvl
        if self.fun == 2:
            self.fun_lock = threading.Lock()
        if self.fun in (2, 3) and not (self.fun_thread and self.fun_thread.is_alive()):
            self.fun_thread = threading.Thread(target=self.fun_loop, daemon=True)
            self.fun_thread.start()


    def fun_loop(self):
        """Thread for fun features"""
        import random
        if self.fun == 2:
            bkp_fg, bkp_bg = curses.pair_content(15)
            curses.init_pair(15, -1, 196)
            while self.run and self.fun == 2:
                if self.disable_drawing:
                    time.sleep(0.5)
                    continue
                if self.red_list:
                    with self.fun_lock:
                        self.red_list.pop(random.randrange(len(self.red_list)))
                        self.draw_input_line()
                    sleep_time = 0.3 / max(len(self.red_list), 1)
                    time.sleep(random.uniform(sleep_time, sleep_time*5))
                else:
                    time.sleep(0.2)
            curses.init_pair(15, bkp_fg, bkp_bg)
            self.red_list = []

        elif self.fun == 3 and not uses_pgcurses:
            h, w = self.screen.getmaxyx()
            flakes = []
            while self.run and self.fun == 3:
                if self.disable_drawing:
                    time.sleep(0.5)
                    continue
                self.resize(redraw_only=True)
                with self.lock:
                    h, w = self.screen.getmaxyx()
                    flakes = [flake for flake in flakes if flake[0] <= h]   # despawn
                    if random.random() < w / 600 and len(flakes) < 30:   # spawn
                        flakes.append([self.have_title, random.randint(self.have_title, w - 1)])
                    for flake in flakes:   # move and draw
                        flake[0] += 1
                        flake[1] += random.choice((-1, 0, 1))
                        y, x = flake
                        if 0 <= y < h and 0 <= x < w:
                            try:
                                self.screen.addch(y, x, "*")
                            except curses.error:
                                pass
                    self.screen.noutrefresh()
                    self.need_update.set()
                time.sleep(0.3)


    def disable_wrap_around(self, disable):
        """Explicitly disable wrap around in extra window"""
        self.wrap_around_disable = disable


    def scroll_bot(self):
        """Scroll to chat bottom"""
        self.chat_index = 0
        self.set_selected(-1, scroll=False)


    def store_input_selected(self):
        """Get selected text from input line"""
        input_select_start = self.input_select_start
        input_select_end = self.input_select_end
        if input_select_start > input_select_end:
            # swap so start is always left side
            input_select_start, input_select_end = input_select_end, input_select_start
        self.input_select_start = None   # stop selection
        self.input_select_text = self.input_buffer[input_select_start:input_select_end]
        return input_select_start, input_select_end


    def allow_chat_selected_hide(self, allow):
        """Allow selected line in chat to be none, position -1"""
        self.dont_hide_chat_selection = not (allow)


    def get_tree_index(self, position):
        """
        Get indexes of various tree positions:
        0 - tree end
        1 - active channel
        """
        num = 0
        skipped = 0
        drop_down_skip_folder = False
        drop_down_skip_guild = False
        drop_down_skip_category = False
        drop_down_skip_channel = False
        for num, code in enumerate(self.tree_format):
            if code == 1000:
                skipped += 1
                drop_down_skip_folder = False
                continue
            elif code == 1100:
                skipped += 1
                drop_down_skip_guild = False
                continue
            elif code == 1200:
                skipped += 1
                drop_down_skip_category = False
                continue
            elif code == 1300:
                skipped += 1
                drop_down_skip_channel = False
                continue
            elif drop_down_skip_folder or drop_down_skip_guild or drop_down_skip_category or drop_down_skip_channel:
                skipped += 1
                continue
            first_digit = code % 10
            if first_digit == 0 and code < 100:
                drop_down_skip_folder = True
            elif first_digit == 0 and code < 200:
                drop_down_skip_guild = True
            elif first_digit == 0 and code < 300:
                drop_down_skip_category = True
            elif first_digit == 0 and 500 <= code <= 599:
                drop_down_skip_channel = True
            if position and (code % 100) // 10 in (4, 5):   # active channels
                return num - skipped
        return num - skipped


    def tree_select_active(self):
        """Move tree selection to active channel"""
        active_channel_index = self.get_tree_index(1)
        self.tree_selected = active_channel_index
        self.tree_index = max(self.tree_selected - self.tree_hw[0] + 3, 0)
        self.draw_tree()


    def tree_select(self, tree_pos):
        """Select specific item in tree by its index"""
        if tree_pos is None:
            return
        skipped = 0
        drop_down_skip_folder = False
        drop_down_skip_guild = False
        drop_down_skip_category = False
        drop_down_skip_channel = False
        for num, code in enumerate(self.tree_format):
            if code == 1000:
                skipped += 1
                drop_down_skip_folder = False
                continue
            elif code == 1100:
                skipped += 1
                drop_down_skip_guild = False
                continue
            elif code == 1200:
                skipped += 1
                drop_down_skip_category = False
                continue
            elif code == 1300:
                skipped += 1
                drop_down_skip_channel = False
                continue
            elif drop_down_skip_folder or drop_down_skip_guild or drop_down_skip_category or drop_down_skip_channel:
                skipped += 1
                continue
            first_digit = code % 10
            if first_digit == 0 and code < 100:
                drop_down_skip_folder = True
            elif first_digit == 0 and code < 200:
                drop_down_skip_guild = True
            elif first_digit == 0 and code < 300:
                drop_down_skip_category = True
            elif first_digit == 0 and 500 <= code <= 599:
                drop_down_skip_channel = True
            if num == tree_pos:
                self.tree_selected = num - skipped
                self.tree_index = max(self.tree_selected - self.tree_hw[0] + 3, 0)
                break
        if not self.disable_drawing:
            self.draw_tree()


    def toggle_category(self, tree_pos, only_open=False):
        """Toggle category drop-down state in tree"""
        if tree_pos >= 0:
            if (self.tree_format[tree_pos] % 10):
                if not only_open:
                    self.tree_format[tree_pos] -= 1
            else:
                self.tree_format[tree_pos] += 1
            if not self.disable_drawing:
                self.draw_tree()
            self.tree_format_changed = True


    def draw_border(self, hwyx, top=True, bot=True, left=True, right=True):
        """Draw border around area on the screen with custom corners"""
        h, w, y, x = hwyx
        h += 2
        w += 2
        y -= 1
        x -= 1

        try:
            # lines
            if top and w > 0:
                self.screen.hline(y, x + 1, curses.ACS_HLINE, w - 2, curses.color_pair(self.default_color))
            if bot and w > 0:
                self.screen.hline(y + h - 1, x + 1, curses.ACS_HLINE, w - 2, curses.color_pair(self.default_color))
            if left and h > 0:
                self.screen.vline(y + 1, x, curses.ACS_VLINE, h - 2, curses.color_pair(self.default_color))
            if right and h > 0:
                self.screen.vline(y + 1, x + w - 1, curses.ACS_VLINE, h - 2, curses.color_pair(self.default_color))

            # corners
            if top and left:
                self.screen.addstr(y, x, self.corner_ul, curses.color_pair(self.default_color))
            if bot and left:
                self.screen.addstr(y + h - 1, x, self.corner_dl, curses.color_pair(self.default_color))
            if top and right:
                self.screen.addstr(y, x + w - 1, self.corner_ur, curses.color_pair(self.default_color))
            if bot and right:
                # it errors when drawing in bottom-right cell, but still draws it
                self.screen.addstr(y + h - 1, x + w - 1, self.corner_dr, curses.color_pair(self.default_color))
        except curses.error:   # errors randomly when resizing
            pass


    def draw_status_line(self):
        """Draw status line"""
        with self.lock:
            h, w = self.status_hw
            status_txt_r = self.status_txt_r
            if status_txt_r:
                status_txt_r = status_txt_r[:w - 1 - 2*self.bordered]   # limit status text size
            # if there is enough space for right text, add spaces and right text
            if self.status_txt_r:
                status_format = []
                if self.bordered:
                    if self.win_extra_line or self.win_extra_window:
                        status_txt_r = replace_spaces_dash(trim_with_dash(status_txt_r)) + "─" + "┤"
                        status_txt_l = "├" + replace_spaces_dash(trim_with_dash(self.status_txt_l))
                    else:
                        status_txt_r = replace_spaces_dash(trim_with_dash(status_txt_r)) + "─" + self.corner_ur
                        status_txt_l = self.corner_ul + replace_spaces_dash(trim_with_dash(self.status_txt_l))
                    status_txt_l = status_txt_l[: max(w - (len(status_txt_r) + 2), 0)]
                    status_txt_l = status_txt_l + "─" * (w - len(status_txt_l) - len(status_txt_r))
                    for item in self.status_txt_l_format:
                        status_format.append((item[0], item[1], item[2] + 1, min(item[3] + 1, w - 1)))
                else:
                    status_txt_r += " "
                    status_txt_l = self.status_txt_l[: max(w - (len(status_txt_r) + 2), 0)]
                    status_txt_l = status_txt_l + " " * (w - len(status_txt_l) - len(status_txt_r))
                    status_format = []   # no format for now in compact mode
                status_line = status_txt_l + status_txt_r
                text_l_len = len(status_txt_l)
                for tab in self.status_txt_r_format:
                    status_format.append((tab[0], tab[1], tab[2] + text_l_len, min(tab[3] + text_l_len, w - 1)))
            elif self.bordered:
                status_txt_l = replace_spaces_dash(trim_with_dash(self.status_txt_l[:w - 1 - 2*self.bordered]))
                if self.win_extra_line or self.win_extra_window:
                    status_line = "├" + status_txt_l + "─" * (w - len(status_txt_l) - 2) + "┤"
                else:
                    status_line = self.corner_ul + status_txt_l + "─" * (w - len(status_txt_l) - 2) + self.corner_ur
                status_format = []
                for item in self.status_txt_l_format:
                    status_format.append((item[0], item[1], item[2] + 1, min(item[3] + 1, w - 1)))
            else:
                # add spaces to end of line
                status_txt_l = self.status_txt_l[:w - 1 - 2*self.bordered]
                status_line = status_txt_l + " " * (w - len(status_txt_l))
                status_format = []   # no format for now in compact mode

            if status_format:
                draw_formatted_line(self.win_status_line, 0, 0, status_line, status_format, self.default_color if self.bordered else 17, self.attrib_map, self.lock)
            elif self.bordered:
                self.win_status_line.insstr(0, 0, status_line + "\n", curses.color_pair(self.default_color))
            else:
                self.win_status_line.insstr(0, 0, status_line + "\n", curses.color_pair(17) | self.attrib_map[17])
            self.win_status_line.noutrefresh()
            self.need_update.set()


    def draw_title_line(self):
        """Draw title line, works same as status line"""
        with self.lock:
            _, w = self.title_hw
            title_txt_r = self.title_txt_r
            if title_txt_r:
                title_txt_r = title_txt_r[:w - 2*self.bordered]
            title_txt_l = self.title_txt_l
            title_txt_l_format = self.title_txt_l_format

            if self.tree_width < 10:   # merge title tree with left title
                if self.bordered:
                    title_tree_txt = replace_spaces_dash(trim_with_dash(self.title_tree_txt[:self.tree_width_conf]))
                    title_tree_txt = title_tree_txt + "─" * (self.tree_width_conf - len(title_tree_txt) - 2) + "─"
                    title_txt_l = title_tree_txt + trim_with_dash(title_txt_l)
                    new_format_l = []
                    shift = len(title_tree_txt)
                    for item in title_txt_l_format:
                        new_format_l.append((item[0], item[1], item[2] + shift, item[3] + shift))
                    title_txt_l_format = new_format_l
                else:
                    title_tree_txt = self.title_tree_txt[:self.tree_width_conf-2]
                    title_tree_txt = title_tree_txt + " " * (self.tree_width_conf-2 - len(title_tree_txt)) + " "
                    title_txt_l = title_tree_txt + title_txt_l

            if self.title_txt_r and len(title_txt_l) < w - 2:
                title_txt_r = replace_spaces_dash(trim_with_dash(title_txt_r, dash=self.bordered))
                title_txt_l = replace_spaces_dash(trim_with_dash(title_txt_l, dash=self.bordered))
                if self.bordered:
                    title_txt_l = self.corner_ul + title_txt_l
                    title_txt_r = title_txt_r[: max(w - (len(title_txt_l) + 5), 0)] + "─" + self.corner_ur
                    title_txt_l = title_txt_l + "─" * (w - len(title_txt_l) - len(title_txt_r))
                    new_format_l = []
                    for item in title_txt_l_format:
                        new_format_l.append((item[0], item[1], item[2] + 1, min(item[3] + 1, w - 1)))
                    title_txt_l_format = new_format_l
                else:
                    title_txt_r = title_txt_r[: max(w - (len(title_txt_r) + 2), 0)] + " "
                    title_txt_l = title_txt_l + " " * (w - len(title_txt_l) - len(title_txt_r))
                    title_txt_l_format = []   # no format for now in compact mode
                title_line = title_txt_l + title_txt_r
                text_l_len = len(title_txt_l)
                title_format = title_txt_l_format
                for part in self.title_txt_r_format:
                    title_format.append((part[0], part[1], part[2] + text_l_len, min(part[3] + text_l_len, w - 1)))

            elif self.bordered:
                title_txt_l = replace_spaces_dash(trim_with_dash(title_txt_l[:w - 1 - 2*self.bordered]))
                title_line = self.corner_ul + title_txt_l + "─" * (w - len(title_txt_l) - 2) + self.corner_ur
                title_format = []
                for item in title_txt_l_format:
                    title_format.append((item[0], item[1], item[2] + 1, min(item[3] + 1, w - 1)))

            else:
                title_txt_l = title_txt_l[:w - 1 - 2*self.bordered]
                title_line = title_txt_l + " " * (w - len(title_txt_l))
                title_format = []   # no format for now in compact mode

            if title_format:
                draw_formatted_line(self.win_title_line, 0, 0, title_line, title_format, self.default_color if self.bordered else 12, self.attrib_map, self.lock)
            elif self.bordered:
                self.win_title_line.insstr(0, 0, title_line + "\n", curses.color_pair(self.default_color))
            else:
                self.win_title_line.insstr(0, 0, title_line + "\n", curses.color_pair(12) | self.attrib_map[12])
            self.win_title_line.noutrefresh()
            self.need_update.set()


    def draw_subtitle_line(self):
        """Draw subtitle line, of no text then remove it"""
        if self.disable_drawing:
            return
        if not self.subtitle_txt.strip(" ").strip("─"):
            if self.win_subtitle_line:
                del self.win_subtitle_line
                self.win_subtitle_line = None
                self.init_chat()
                self.draw_chat(refresh=False)
            return

        chat_w = self.chat_hw[1]
        if not self.win_subtitle_line or chat_w != self.subtitle_line_hw[1] + self.bordered * 2:
            subtitle_line_hwyx = (1,
                self.chat_hw[1] + self.bordered * 2,
                1,
                self.tree_width + self.bordered + 1,
            )
            try:
                self.win_subtitle_line = self.screen.derwin(*subtitle_line_hwyx)
            except curses.error:   # during resize
                return
            self.subtitle_line_hw = self.win_subtitle_line.getmaxyx()
            del self.win_chat
            self.init_chat()
            self.draw_chat(refresh=False)

        subtitle_format = []
        if self.bordered:
            for tab in self.subtitle_format:
                subtitle_format.append((tab[0], tab[1], tab[2] + 1, tab[3] + 1))
            subtitle_format.append((self.default_color, None, 0, 1))
            subtitle_format.append((self.default_color, None, self.subtitle_line_hw[1] - 1, self.subtitle_line_hw[1]))
        else:
            subtitle_format = self.subtitle_format

        with self.lock:
            _, w = self.subtitle_line_hw
            if self.bordered:
                subtitle_txt = replace_spaces_dash(trim_with_dash(self.subtitle_txt[:w-2]))
                subtitle_line = "├" + subtitle_txt + "─" * (w - len(subtitle_txt) - 2) + "┤"
            else:
                subtitle_txt = self.subtitle_txt[:w]
                subtitle_line = subtitle_txt + " " * (w - len(subtitle_txt))
            if subtitle_format:
                draw_formatted_line(self.win_subtitle_line, 0, 0, subtitle_line, subtitle_format, 23, self.attrib_map, self.lock)
            else:
                self.win_subtitle_line.insstr(0, 0, subtitle_line + "\n", curses.color_pair(23) | self.attrib_map[23])
            self.win_title_tree.noutrefresh()
            self.need_update.set()


    def draw_title_tree(self):
        """Draw tree title line, works same as status line, but without right text"""
        if self.tree_width < 10:
            return
        with self.lock:
            _, w = self.tree_title_hw
            title_txt = self.title_tree_txt[:w]
            if self.bordered:
                title_txt = replace_spaces_dash(trim_with_dash(title_txt))
                title_line = self.corner_ul + title_txt + "─" * (w - len(title_txt) - 2) + self.corner_ur
                self.win_title_tree.insstr(0, 0, title_line + "\n", curses.color_pair(self.default_color))
            else:
                title_line = title_txt + " " * (w - len(title_txt))
                self.win_title_tree.insstr(0, 0, title_line + "\n", curses.color_pair(12) | self.attrib_map[12])
            self.win_title_tree.noutrefresh()
            self.need_update.set()


    def draw_member_list_title(self, title_line, color=None, force=False):
        """Draw title above member list"""
        if self.disable_drawing:
            return
        if not self.win_member_list or self.member_list_width == 2 or not title_line or (not force and title_line == self.member_list_title_text):
            return
        self.member_list_title_text = title_line
        _, w = self.win_member_list.getmaxyx()
        y, x = self.win_member_list.getbegyx()
        y -= 1
        x += 1
        if self.bordered:
            parts = title_line.split(" ")
            self.screen.addstr(y, x, parts[0], curses.color_pair(color if color else self.default_color))
            second_text = (" " + parts[1]) if len(parts) >= 2 else ""
            second_text += "─" * (w - len(title_line) - 2)
            self.screen.addstr(y, x + len(parts[0]), second_text, curses.color_pair(self.default_color))
        else:   # no format for now in compact mode
            title_line = title_line + " " * (w - len(title_line) - 2)
            self.screen.addstr(y, x, title_line, curses.color_pair(12) | self.attrib_map[12])
        self.screen.noutrefresh()
        self.need_update.set()


    def draw_input_line(self):
        """Draw text input line"""
        with self.lock:
            w = self.input_hw[1]
            # show only part of line when longer than screen
            start = max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
            end = start + w - 1
            line_text = self.input_buffer[start:end].replace("\n", "␤")

            # prepare selected range
            if self.input_select_start is not None:
                selected_start_screen = self.input_select_start - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                selected_end_screen = self.input_select_end - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                if selected_start_screen > selected_end_screen:
                    # swap so start is always left side
                    selected_start_screen, selected_end_screen = selected_end_screen, selected_start_screen

            # if not line_text:   # only needed if cursor drawing is disabled
            #     line_text = " "

            # draw
            character = " "
            pos = 0
            cursor_drawn = False
            for pos, character in enumerate(line_text):
                # cursor in the string
                if not cursor_drawn and self.cursor_pos == pos:
                    safe_insch(self.win_input_line, 0, self.cursor_pos, character, curses.color_pair(15) | self.attrib_map[15])
                    cursor_drawn = True
                # selected part of string
                elif self.input_select_start is not None and selected_start_screen <= pos < selected_end_screen:
                    safe_insch(self.win_input_line, 0, pos, character, curses.color_pair(15) | self.attrib_map[15])
                elif pos in self.red_list:
                    safe_insch(self.win_input_line, 0, pos, character, curses.color_pair(20))
                else:
                    for bad_range in self.misspelled:
                        if bad_range[0] <= pos < sum(bad_range) and (bad_range[0] > self.cursor_pos or self.cursor_pos >= sum(bad_range)+1):
                            safe_insch(self.win_input_line, 0, pos, character, curses.color_pair(10) | self.attrib_map[10])
                            break
                    else:
                        safe_insch(self.win_input_line, 0, pos, character, curses.color_pair(14) | self.attrib_map[14])
            self.win_input_line.insch(0, pos + 1, "\n", curses.color_pair(0))
            # cursor at the end of string
            if not cursor_drawn and self.cursor_pos >= len(line_text):
                self.show_cursor()
            self.win_input_line.noutrefresh()
            self.need_update.set()


    def draw_chat(self, refresh=True, inline=True):
        """Draw chat with applied color formatting"""
        if self.inline_media:
            self.inline_media.clear_images()
            exclude_selection = self.inline_media.get_images()
        else:
            exclude_selection = []
        with self.lock:
            try:
                draw_chat(
                    self.win_chat,
                    self.chat_hw[0],
                    self.chat_hw[1],
                    self.chat_buffer,
                    self.chat_format,
                    self.chat_index,
                    self.chat_selected,
                    self.attrib_map,
                    self.default_color,
                    exclude_selection,
                )
                self.win_chat.noutrefresh()
                if refresh:
                    self.need_update.set()
            except curses.error:
                # exception will happen when window is resized to smaller w dimensions
                self.resize()
        if self.have_scrollbar:
            self.draw_scrollbar_chat()
        if self.inline_media and refresh and inline:
            self.inline_media.draw_images()
        self.execute_extensions_methods("on_chat_draw", cache=True)


    def draw_scrollbar_chat(self):
        """Draw scrollbar at the right side of the chat"""
        if not self.have_scrollbar:
            return
        with self.lock:
            h, w = self.chat_hw
            y, x = self.win_chat.getbegyx()
            abs_x = x + w
            total_lines = len(self.chat_buffer)

            # calculate thumb size and pos
            if total_lines <= h:
                thumb_size = 0
                thumb_pos = 0
                thumb_pos_half = 0
            else:
                thumb_size = max(2, h * h // total_lines)
                max_pos = h - thumb_size
                max_index = total_lines - h
                if self.scrollbar_char == "┃":
                    max_pos_half = max_pos * 2   # double resolution
                    thumb_pos_half = max(0, min(max_pos_half, max_pos_half - int(self.chat_index * max_pos_half / max_index)))
                else:
                    thumb_pos = max(0, min(max_pos, max_pos - int(self.chat_index * max_pos / max_index)))

            try:
                # draw thumb and border
                self.screen.vline(y, abs_x, curses.ACS_VLINE, h, curses.color_pair(self.default_color))
                if thumb_size > 0:
                    if self.fine_scrollbar:
                        start_row = thumb_pos_half // 2
                        is_half_step = (thumb_pos_half % 2 != 0)
                        loop_range = thumb_size + 1 if is_half_step else thumb_size
                        for rel_y in range(loop_range):
                            char = ("╽" if rel_y == 0 else "╿" if rel_y == loop_range - 1 else "┃") if is_half_step else "┃"
                            self.screen.addch(y + start_row + rel_y, abs_x, char, curses.color_pair(26))
                    else:
                        for rel_y in range(thumb_size):
                            self.screen.addch(y + rel_y + thumb_pos, abs_x, self.scrollbar_char, curses.color_pair(self.default_color))

                # draw corners
                if self.win_subtitle_line:
                    self.screen.addstr(y - 1, abs_x, "┤", curses.color_pair(self.default_color))
                else:
                    self.screen.addstr(y - 1, abs_x, self.corner_ur, curses.color_pair(self.default_color))

                # it errors when drawing in bottom-right cell, but still draws it
                self.screen.addstr(y + h, abs_x, self.corner_dr, curses.color_pair(self.default_color))

            except curses.error:
                pass

            self.screen.noutrefresh()
            self.need_update.set()


    def draw_scrollbar_extra(self):
        """Draw scrollbar at the right side of extra window"""
        if not self.have_scrollbar or not self.win_extra_window:
            return
        with self.lock:
            h, w = self.win_extra_window.getmaxyx()
            y, x = self.win_extra_window.getbegyx()
            h -= 1
            y += 1
            abs_x = x + w
            total_lines = len(self.extra_window_body)
            if total_lines <= h:
                thumb_size = 0
                thumb_pos = 0
                thumb_pos_half = 0
            else:
                thumb_size = max(1, h * h // total_lines)
                max_pos = h - thumb_size
                max_index = total_lines - h
                if self.scrollbar_char == "┃":
                    max_pos_half = max_pos * 2
                    thumb_pos_half = max(0, min(max_pos_half, int(self.extra_index * max_pos_half / max_index)))
                else:
                    thumb_pos = max(0, min(max_pos, int(self.extra_index * max_pos / max_index)))
            try:
                self.screen.vline(y, abs_x, curses.ACS_VLINE, h, curses.color_pair(self.default_color))
                if thumb_size > 0:
                    if self.fine_scrollbar:
                        start_row = thumb_pos_half // 2
                        is_half_step = (thumb_pos_half % 2 != 0)
                        loop_range = thumb_size + 1 if is_half_step else thumb_size
                        for rel_y in range(loop_range):
                            char = ("╽" if rel_y == 0 else "╿" if rel_y == loop_range - 1 else "┃") if is_half_step else "┃"
                            self.screen.addch(y + start_row + rel_y, abs_x, char, curses.color_pair(26))
                    else:
                        for rel_y in range(thumb_size):
                            self.screen.addch(y + rel_y + thumb_pos, abs_x, self.scrollbar_char, curses.color_pair(self.default_color))
                self.screen.addstr(y - 1, abs_x, self.corner_ur, curses.color_pair(self.default_color))
                self.screen.addstr(y + h, abs_x, "┤", curses.color_pair(self.default_color))
            except curses.error:
                pass
            self.screen.noutrefresh()
            self.need_update.set()


    def draw_tree(self):
        """Draw channel tree"""
        if self.tree_width < 10:
            self.draw_tree_mini()
            return
        with self.lock:
            h, w = self.tree_hw
            # drawing from top to down
            skipped = 0   # skipping drop-down ends (code 1XXX)
            drop_down_skip_folder = False
            drop_down_skip_guild = False
            drop_down_skip_category = False
            drop_down_skip_channel = False
            drop_down_level = 0
            above_mention = False
            self.tree_clean_len = 0
            y = 0
            num = 0
            try:
                for num, line in enumerate(self.tree):
                    code = self.tree_format[num]
                    first_digit = (code % 10)
                    if code == 1000:
                        skipped += 1
                        drop_down_skip_folder = False
                        continue
                    elif code == 1100:
                        skipped += 1
                        drop_down_level -= 1
                        drop_down_skip_guild = False
                        continue
                    elif code == 1200:
                        skipped += 1
                        drop_down_level -= 1
                        drop_down_skip_category = False
                        continue
                    elif code == 1300:
                        skipped += 1
                        drop_down_level -= 1
                        drop_down_skip_channel = False
                        continue
                    text_start = drop_down_level * 3 + 1
                    if 99 < code < 300 or 500 <= code <= 599:
                        drop_down_level += 1
                    if drop_down_skip_folder or drop_down_skip_guild or drop_down_skip_category or drop_down_skip_channel:
                        skipped += 1
                        continue
                    self.tree_clean_len += 1
                    if first_digit == 0 and code < 100:
                        drop_down_skip_folder = True
                    elif first_digit == 0 and code < 200:
                        drop_down_skip_guild = True
                    elif first_digit == 0 and code < 300:
                        drop_down_skip_category = True
                    elif first_digit == 0 and 500 <= code <= 599:
                        drop_down_skip_channel = True
                    if num < self.tree_index:   # above visible area
                        # if mentioned and (its not collapsed or its forum/channel_with_threads)
                        if not above_mention and (code % 100) // 10 == 2 and (first_digit != 1 or 500 < code < 599):
                            above_mention = True
                        continue
                    y = max(num - skipped - self.tree_index, 0)
                    if y >= h:   # bellow visible area
                        break
                    second_digit = (code % 100) // 10
                    color = curses.color_pair(3)
                    color_line = curses.color_pair(3)
                    selected = False
                    if second_digit == 1:   # muted
                        color = curses.color_pair(5) | self.attrib_map[5]
                    elif second_digit == 2:   # mentioned
                        color = curses.color_pair(8) | self.attrib_map[8]
                    elif second_digit == 3:   # unread
                        if self.tree_unread_inherit_color and code < 200:
                            color = curses.color_pair(25) | self.attrib_map[7]
                        elif self.tree_unread_inherit_color and code < 300:
                            color = curses.color_pair(24) | self.attrib_map[7]
                        else:
                            color = curses.color_pair(7) | self.attrib_map[7]
                    elif second_digit == 4:   # active
                        color = curses.color_pair(6) | self.attrib_map[6]
                        color_line = curses.color_pair(6)
                    elif second_digit == 5:   # active mentioned
                        color = curses.color_pair(9) | self.attrib_map[9]
                        color_line = curses.color_pair(6)
                    elif code < 200:   # guild/dms
                        color = curses.color_pair(25) | self.attrib_map[25]
                    elif code < 300:   # category
                        color = curses.color_pair(24) | self.attrib_map[24]
                    if y == self.tree_selected - self.tree_index:   # selected
                        if second_digit == 2:   # selected mentioned
                            color = curses.color_pair(22) | self.attrib_map[22]
                        else:
                            color = curses.color_pair(4) | self.attrib_map[4]
                        color_line = curses.color_pair(4)
                        self.tree_selected_abs = self.tree_selected + skipped
                        selected = True
                    # filled with spaces so background is drawn all the way
                    self.win_tree.insstr(y, 0, " " * w + "\n", color_line)
                    self.win_tree.insstr(y, 0, line[:text_start], color_line)
                    self.win_tree.insstr(y, text_start, line[text_start:], color)
                    # if this is dm, set color for status sign
                    # drawing it only for "normal" DMs, just to save some color pairs until python curses fixes the bug
                    if 300 <= code < 399 and second_digit == 0 and not selected:
                        if first_digit == 2:   # online
                            # this character is always at position 4 (set in formatter)
                            self.win_tree.addch(y, 4, self.tree_dm_status, curses.color_pair(18))
                        elif first_digit == 3:   # idle
                            self.win_tree.addch(y, 4, self.tree_dm_status, curses.color_pair(19))
                        elif first_digit == 4:   # dnd
                            self.win_tree.addch(y, 4, self.tree_dm_status, curses.color_pair(20))
                y += 1
                while y < h:
                    # curses optimizes scrolling, so large empty space will cause flickering when scrolling member list
                    # this is prevented by vertically alternating space and alt_space character (U+2800 - braille pattern blank)
                    self.win_tree.insstr(y, 0, f"{" " if y % 2 else ALT_SPACE}\n", curses.color_pair(1))
                    y += 1
                while num < len(self.tree_format):   # continue loop to detect mentions bellow visible area
                    if (self.tree_format[num] % 100) // 10 == 2:
                        self.win_tree.insstr(h-1, 0, " Mentions down ".center(w, self.hline), curses.color_pair(8) | self.attrib_map[8])
                        break
                    num += 1
                if above_mention:
                    self.win_tree.insstr(0, 0, " Mentions up ".center(w, self.hline), curses.color_pair(8) | self.attrib_map[8])
                self.win_tree.noutrefresh()
                self.need_update.set()
            except curses.error:
                # this exception will happen when window is resized to smaller h dimensions
                self.resize()


    def draw_tree_mini(self):
        """Draw minimized channel tree"""
        mentions = 0
        for code in self.tree_format:
            if 300 <= code < 499 and (code % 100) // 10 == 2:
                mentions += 1

        h, _ = self.tree_hw
        y = 0
        if mentions:
            text = f">>> {mentions} MENTIONS >>>".center(h)
            color = curses.color_pair(8)
        else:
            text = ">>> TREE >>>".center(h)
            color = curses.color_pair(3)

        try:
            while y < h:
                self.win_tree.insch(y, 0, text[y], color)
                y += 1
                self.win_tree.noutrefresh()
                self.need_update.set()
        except curses.error:
            self.resize()


    def draw_prompt(self):
        """Draw prompt line, resize input line but dont redraw it"""
        with self.lock:
            h, w = self.screen.getmaxyx()
            del (self.win_prompt, self.win_input_line)
            input_line_hwyx = (
                1,
                w - (self.tree_width + self.bordered + 1) - len(self.prompt) - 2*self.bordered,
                h - 1 - self.bordered,
                self.tree_width + len(self.prompt) + 2*self.bordered + 1)
            self.win_input_line = self.screen.derwin(*input_line_hwyx)
            self.input_hw = self.win_input_line.getmaxyx()
            prompt_hwyx = (1, len(self.prompt), h - 1 - self.bordered, self.tree_width + 2*self.bordered + 1)
            self.win_prompt = self.screen.derwin(*prompt_hwyx)
            self.win_prompt.insstr(0, 0, self.prompt, curses.color_pair(13) | self.attrib_map[13])
            self.win_prompt.noutrefresh()
            self.need_update.set()


    def draw_extra_line(self, text=None, extra_line_format=None, toggle=False):
        """
        Draw extra line above status line and resize chat.
        If toggle and same text is repeated then remove extra line.
        """
        if self.disable_drawing:
            return
        with self.lock:
            if toggle and text == self.extra_line_text:
                self.remove_extra_line()
                return
            self.extra_line_text = text
            self.extra_line_format = extra_line_format
            if text:
                h, w = self.screen.getmaxyx()
                if not self.win_extra_line:
                    extra_line_hwyx = (1, w - (self.tree_width + self.bordered + 1), h - self.bordered - 3, self.tree_width + self.bordered + 1)
                    self.win_extra_line = self.screen.derwin(*extra_line_hwyx)
                    del self.win_chat
                    self.init_chat()
                    self.draw_chat(refresh=False)
                    if self.member_list and self.bordered:   # have to redraw member list borders
                        h, w = self.screen.getmaxyx()
                        member_list_hwyx = (
                            h - (2 + bool(self.win_extra_line)) - self.have_title - 2*self.bordered,
                            self.member_list_width - self.bordered,
                             self.bordered or self.have_title,
                            w - self.member_list_width,
                        )
                        self.draw_border(member_list_hwyx, top=not (self.have_title))
                    self.draw_member_list(self.member_list, self.member_list_format, force=True)
                    if self.bordered:
                        self.draw_status_line()
                w = w - (self.tree_width + self.bordered + 1)
                if self.bordered:
                    text = "─" + trim_with_dash(text, dash=False)
                    line_text = self.corner_ul + text + "─" * (w - len(text) - 2) + self.corner_ur
                    if extra_line_format:
                        line_format = []
                        for item in extra_line_format:
                            line_format.append((item[0], item[1], item[2] + 2, min(item[3] + 2, w - 1)))
                        draw_formatted_line(self.win_extra_line, 0, 0, line_text, line_format, self.default_color, self.attrib_map, self.lock)
                    else:
                        self.win_extra_line.insstr(0, 0, line_text, curses.color_pair(self.default_color))
                else:
                    self.win_extra_line.insstr(0, 0, text + " " * (w - len(text)) + "\n", curses.color_pair(11) | self.attrib_map[11])
                self.win_extra_line.noutrefresh()
                self.need_update.set()
                self.draw_chat(inline=False)
        if self.inline_media and text:   # gotta be outside lock
            self.inline_media.draw_images()


    def remove_extra_line(self):
        """Disable drawing of extra line above status line, and resize chat"""
        if self.disable_drawing:
            return
        if self.win_extra_line:
            with self.lock:
                del self.win_chat
                self.extra_line_text = ""
                self.extra_line_format = []
                self.win_extra_line = None
                self.init_chat()
                self.chat_hw = self.win_chat.getmaxyx()
                self.draw_chat(inline=False)
                if self.member_list and self.bordered:   # have to redraw member list borders
                    h, w = self.screen.getmaxyx()
                    member_list_hwyx = (
                        h - (2 + bool(self.win_extra_line)) - self.have_title - 2*self.bordered,
                        self.member_list_width - self.bordered,
                         self.bordered or self.have_title,
                        w - self.member_list_width,
                    )
                    self.draw_border(member_list_hwyx, top=not (self.have_title))
                if self.bordered:
                    self.draw_status_line()
                self.draw_member_list(self.member_list, self.member_list_format, force=True)
                self.draw_chat(inline=False)
            if self.have_scrollbar:
                self.draw_scrollbar_chat()
            if self.inline_media:   # gotta be outside lock
                self.inline_media.draw_images()


    def draw_extra_window(self, title_txt, body_text, body_format=None, select=False, reset_scroll=True):
        """
        Draw extra window above status line and resize chat.
        title_txt is string, body_text is list.
        """
        if self.disable_drawing:
            return
        with self.lock:
            self.extra_select = select
            self.extra_window_title = title_txt
            self.extra_window_body = body_text
            if body_format is not None:   # only if body format is [] then there is no format
                self.extra_window_format = body_format
            if reset_scroll:
                self.extra_index = 0
                self.extra_selected = 0 if self.extra_select else -1

            if title_txt and not self.disable_drawing:
                h, w = self.screen.getmaxyx()
                if not self.win_extra_window:
                    del self.win_chat
                    self.win_extra_line = None
                    extra_window_hwyx = (
                        self.extra_window_h + 1,
                        w - (self.tree_width + 3*self.bordered + 1),
                        h - 3 - self.extra_window_h - self.bordered,
                        self.tree_width + 2*self.bordered + 1,
                    )
                    self.win_extra_window = self.screen.derwin(*extra_window_hwyx)
                    self.init_chat()
                    if not self.member_list:
                        self.draw_chat(refresh=False)
                    self.draw_member_list(self.member_list, self.member_list_format, force=True)
                    if self.bordered:
                        self.draw_border(extra_window_hwyx, top=False, bot=False)
                        y, x = extra_window_hwyx[2], extra_window_hwyx[3]
                        self.screen.addstr(y, x - 1, self.corner_ul, curses.color_pair(self.default_color))
                        self.screen.addstr(y, x + extra_window_hwyx[1], self.corner_ur, curses.color_pair(self.default_color))
                        self.screen.noutrefresh()
                        self.draw_status_line()

                if self.bordered:
                    title_txt = "─" + trim_with_dash(title_txt, dash=False) + "─" * (w - len(title_txt))
                    self.win_extra_window.insstr(0, 0, title_txt, curses.color_pair(self.default_color))
                else:
                    self.win_extra_window.insstr(0, 0, title_txt + " " * (w - len(title_txt)) + "\n", curses.color_pair(11) | self.attrib_map[11])
                h = self.win_extra_window.getmaxyx()[0]
                y = 0
                for num, line in enumerate(body_text):
                    y = num - self.extra_index
                    if y + 1 >= h:
                        break
                    if y < 0:
                        continue
                    line_format = self.extra_window_format[num] if self.extra_window_format else []
                    try:
                        if num == self.extra_selected:
                            self.win_extra_window.insstr(y + 1, 0, line + " " * (w - len(line)) + "\n", curses.color_pair(11) | self.attrib_map[11])
                        else:
                            draw_formatted_line(self.win_extra_window, y + 1, 0, line + " " * (w - len(line)) + "\n", line_format, 21, self.attrib_map, self.lock)
                    except curses.error:   # some error with emojis
                        pass

                y += 2
                while y < h:
                    self.win_extra_window.insstr(y, 0, "\n", curses.color_pair(1))
                    y += 1
                self.draw_scrollbar_extra()
                self.draw_chat(refresh=False)
                self.win_extra_window.noutrefresh()
                self.need_update.set()
        if self.inline_media and title_txt:   # do here because of refresh=False
            self.inline_media.draw_images()
        self.execute_extensions_methods("on_extra_window_draw", cache=False)


    def remove_extra_window(self):
        """Disable drawing of extra window above status line, and resize chat"""
        if self.disable_drawing:
            return
        if self.win_extra_window:
            with self.lock:
                del (self.win_extra_window, self.win_chat)
                self.extra_window_title = ""
                self.extra_window_body = []
                self.extra_window_format = []
                self.win_extra_window = None
                self.extra_selected = -1
                self.init_chat()
                self.chat_hw = self.win_chat.getmaxyx()
                if self.member_list and self.bordered:   # have to redraw member list borders
                    h, w = self.screen.getmaxyx()
                    member_list_hwyx = (
                        h - (2 + bool(self.win_extra_line)) - self.have_title - 2*self.bordered,
                        self.member_list_width - self.bordered,
                         self.bordered or self.have_title,
                        w - self.member_list_width,
                    )
                    self.draw_border(member_list_hwyx, top=not (self.have_title))
                if self.bordered:
                    self.draw_status_line()
                self.draw_extra_line(self.extra_line_text, self.extra_line_format)
                self.draw_member_list(self.member_list, self.member_list_format, force=True)
                self.draw_chat(inline=False)
            if self.have_scrollbar:
                self.draw_scrollbar_chat()
            if self.inline_media:   # gotta be outside lock
                self.inline_media.draw_images()
            self.execute_extensions_methods("on_extra_window_remove")


    def draw_member_list(self, member_list, member_list_format, force=False, reset=False):
        """Draw member list and resize chat"""
        if self.disable_drawing:
            return

        if self.inline_media and (not self.win_member_list or force):
            self.inline_media.clear_images(force=True)

        with self.lock:
            h, w = self.screen.getmaxyx()
            if self.bordered:   # restore left border because chat probably messed it
                common_h = h - 4 - self.have_title - (bool(self.win_extra_window) * self.extra_window_h + 1) - bool(self.win_extra_line)
                self.screen.vline(2, w - self.member_list_width, curses.ACS_VLINE, common_h - 2, curses.color_pair(self.default_color))
            self.member_list = member_list
            self.member_list_format = member_list_format
            if member_list and not self.disable_drawing:
                if reset:
                    self.mlist_selected = -1
                    self.mlist_index = 0
                if not self.win_member_list or force:
                    if not force and self.win_member_list:
                        self.mlist_selected = -1
                        self.mlist_index = 0
                    common_h = self.init_chat()
                    if not force:
                        self.draw_chat()

                    # init member list
                    member_list_hwyx = (
                        common_h,
                        self.member_list_width - self.bordered,
                        self.bordered or self.have_title,
                        w - self.member_list_width,
                    )
                    self.win_member_list = self.screen.derwin(*member_list_hwyx)
                    if self.bordered:
                        self.draw_border(member_list_hwyx)
                        self.draw_member_list_title(self.member_list_title_text, force=True)
                        if self.have_title:
                            title_line_hwyx = (1, w - (self.tree_width + 2) - bool(self.win_member_list) * (self.member_list_width + 1), 0, self.tree_width + 2)
                            self.win_title_line = self.screen.derwin(*title_line_hwyx)
                            self.title_hw = self.win_title_line.getmaxyx()
                            self.draw_title_line()
                        self.draw_subtitle_line()
                    else:
                        self.screen.vline(self.have_title, w - self.member_list_width-1, self.vline, common_h, curses.color_pair(self.default_color))

                # draw member list
                h, w = self.win_member_list.getmaxyx()
                w -= 1
                y = 0
                if self.member_list_width == 2:
                    member_list = "<<< MEMBERS <<<".center(h)
                    member_list_format = [None] * len(member_list)
                for num, line in enumerate(member_list):
                    y = max(num - self.mlist_index, 0)
                    if y >= h:
                        break
                    line_format = member_list_format[num]
                    if num == self.mlist_selected and self.member_list_width != 2:
                        self.win_member_list.insstr(y, 0, line, curses.color_pair(4) | self.attrib_map[4])
                    else:
                        draw_formatted_line(self.win_member_list, y, 0, line, line_format, self.default_color, self.attrib_map, self.lock)
                y += 1
                while y < h:
                    # curses optimizes scrolling, so large empty space will cause flickering when scrolling tree
                    # this is prevented by vertically alternating space and alt_space character (U+2800 - braille pattern blank)
                    self.win_member_list.insstr(y, 0, f"{" " if y % 2 else ALT_SPACE}\n", curses.color_pair(1))
                    y += 1
                self.win_member_list.noutrefresh()
                self.need_update.set()

        if uses_pgcurses:   # quick fix but not ideal, find why is tree cleared on derwin
            self.draw_tree()
            self.draw_title_tree()


    def remove_member_list(self, pause=True):
        """Remove member list and resize chat"""
        if self.disable_drawing:
            return
        if self.win_member_list:
            # safely clean emojis
            with self.lock:
                if not self.win_member_list:
                    return
                h, w = self.win_member_list.getmaxyx()
                for y in range(h):
                    self.win_member_list.insstr(y, 0, " " * w, curses.color_pair(1))
                self.win_member_list.noutrefresh()
                self.need_update.set()
            if pause:
                time.sleep(self.screen_update_delay/2)

            # remove member list and redraw chat
            with self.lock:
                del (self.win_member_list, self.win_chat)
                self.member_list = []
                self.member_list_format = []
                self.win_member_list = None
                h, w = self.screen.getmaxyx()
                self.init_chat()
                if self.bordered and self.have_title:
                    title_line_hwyx = (1, w - (self.tree_width + 2) - bool(self.win_member_list) * (self.member_list_width + 1), 0, self.tree_width + 2)
                    self.win_title_line = self.screen.derwin(*title_line_hwyx)
                    self.title_hw = self.win_title_line.getmaxyx()
                    self.draw_title_line()
                self.draw_subtitle_line()
                # self.draw_chat()   # chat will be regenerated and resized in app main loop

        if uses_pgcurses:   # quick fix but not ideal, find why is tree cleared on derwin
            self.draw_tree()
            self.draw_title_tree()


    def set_cursor_color(self, color_id):
        """Changes cursor color"""
        with self.lock:
            w = self.input_hw[1]
            start = max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
            end = start + w - 1
            line_text = self.input_buffer[start:end].replace("\n", "␤")
            character = " "
            if self.cursor_pos < w:
                if self.cursor_pos < len(line_text):
                    character = line_text[self.cursor_pos]
                if self.cursor_pos == w - 1:
                    self.win_input_line.insch(0, self.cursor_pos, character, curses.color_pair(color_id) | self.attrib_map[color_id])
                else:
                    self.win_input_line.addch(0, self.cursor_pos, character, curses.color_pair(color_id) | self.attrib_map[color_id])
                self.win_input_line.noutrefresh()
                self.need_update.set()


    def blink_cursor(self):
        """Thread that makes cursor blink, hibernates after some time"""
        self.hibernate_cursor = 0
        while self.run:
            while self.run and self.hibernate_cursor >= 10:
                time.sleep(self.blink_cursor_on)
            if self.cursor_on:
                color_id = 14
                sleep_time = self.blink_cursor_on
            else:
                color_id = 15
                sleep_time = self.blink_cursor_off
            self.set_cursor_color(color_id)
            time.sleep(sleep_time)
            self.hibernate_cursor += 1
            self.cursor_on = not self.cursor_on


    def show_cursor(self):
        """Force cursor to be shown on screen and reset blinking"""
        if not self.disable_drawing:
            self.set_cursor_color(15)
            self.cursor_on = True
            self.hibernate_cursor = 0


    def update_status_line(self, text_l, text_r=None, text_l_format=[], text_r_format=[]):
        """Update status line text and format"""
        redraw = False
        if text_l != self.status_txt_l or text_l_format != self.status_txt_l_format:
            self.status_txt_l = text_l
            self.status_txt_l_format = text_l_format
            redraw = True
        if text_r != self.status_txt_r or text_r_format != self.status_txt_r_format:
            self.status_txt_r = text_r
            self.status_txt_r_format = text_r_format
            redraw = True
        if redraw and not self.disable_drawing:
            self.draw_status_line()


    def update_title_line(self, text_l, text_r=None, text_l_format=[], text_r_format=[]):
        """Update title line text and format"""
        if self.have_title:
            redraw = False
            if text_l != self.title_txt_l or text_l_format != self.title_txt_l_format:
                self.title_txt_l = text_l
                self.title_txt_l_format = text_l_format
                redraw = True
            if text_r != self.title_txt_r or text_r_format != self.title_txt_r_format:
                self.title_txt_r = text_r
                self.title_txt_r_format = text_r_format
                redraw = True
            if redraw and not self.disable_drawing:
                self.draw_title_line()


    def update_subtitle_line(self, subtitle, subtitle_format):
        """Update subtitle line text and format"""
        if subtitle != self.subtitle_txt or subtitle_format != self.subtitle_format:
            self.subtitle_txt = subtitle
            self.subtitle_format = subtitle_format
            if not self.disable_drawing:
                self.draw_subtitle_line()


    def update_title_tree(self, text):
        """Update tree title line text"""
        if self.have_title_tree and text != self.title_tree_txt:
            self.title_tree_txt = text
            if not self.disable_drawing:
                self.draw_title_tree()


    def update_chat(self, chat_text, chat_format):
        """Update text buffer"""
        self.chat_buffer = chat_text
        self.chat_format = chat_format
        if not self.disable_drawing:
            self.draw_chat()


    def update_tree(self, tree_text, tree_format):
        """Update channel tree"""
        self.tree = tree_text
        self.tree_format = tree_format
        if not self.disable_drawing:
            self.draw_tree()
        self.tree_format_changed = True


    def update_prompt(self, prompt):
        """Update and draw prompt line, resize input line but dont redraw it"""
        self.prompt = prompt
        if not self.disable_drawing:
            self.draw_prompt()


    def init_pair(self, color, force_id=-1):
        """Initialize color pair while keeping track of last unused id, and store its attribute in attr_map"""
        if len(color) == 2:
            fg, bg = color
            attribute = 0
        else:
            fg, bg, attribute = color
            attribute = str(attribute).lower()
            if attribute in ("b", "bold"):
                attribute = curses.A_BOLD
            elif attribute in ("u", "underline"):
                attribute = curses.A_UNDERLINE
            elif attribute in ("i", "italic"):
                attribute = curses.A_ITALIC
            else:
                attribute = 0

        if fg > curses.COLORS or fg < 0:
            fg = -1
        if bg > curses.COLORS or bg < 0:
            bg = -1
        if force_id >= curses.COLOR_PAIRS:
            return 0
        if self.last_free_id >= curses.COLOR_PAIRS:
            return 0
        if force_id > 0:
            curses.init_pair(force_id, fg, bg)
            self.attrib_map = set_list_item(self.attrib_map, attribute, force_id)
            return force_id

        # 255_curses_bug - reusing same pairs to save ids
        key = (fg & 0x1FF) | ((bg & 0x1FF) << 9) | (attribute << 18)
        if key in self.color_pairs and self.last_free_id > self.protected_colors:
            return self.color_pairs[key]
        self.color_pairs[key] = self.last_free_id

        curses.init_pair(self.last_free_id, fg, bg)
        self.attrib_map.append(attribute)
        self.last_free_id += 1
        return self.last_free_id - 1


    def init_colors(self, colors):
        """Initialize multiple color pairs"""
        color_codes = []
        for color in colors:
            pair_id = self.init_pair(color)
            color_codes.append(pair_id)
        self.default_color = color_codes[0]
        self.role_color_start_id = self.last_free_id
        self.resize(redraw_only=True)
        return color_codes


    def init_colors_formatted(self, colors, alt_color):
        """Initialize multiple color pairs in double nested lists twice, one with normal color and one bg from with alt_color"""
        color_codes = []
        for format_colors in colors:
            format_codes = []
            for color in format_colors:
                new_color = color.copy()
                if new_color[1] == -2:
                    new_color[1] = format_colors[0][1]
                pair_id = self.init_pair(new_color[:3])
                format_codes.append([pair_id, *color[3:]])
            color_codes.append(format_codes)
        # using bg from alt_color
        for num_f, format_colors in enumerate(colors):
            format_codes = []
            for num, color in enumerate(format_colors):
                if num == 0:
                    if num_f in (0, 1, 2) and alt_color[0] != -2:   # update fg onlt for message, message_grouped and newline
                        color[0] = alt_color[0]
                    color[1] = alt_color[1]
                if color[1] == -2:
                    color[1] = format_colors[0][1]
                pair_id = self.init_pair(color[:3])
                format_codes.append([pair_id, *color[3:]])
            color_codes.append(format_codes)
        self.role_color_start_id = self.last_free_id
        return color_codes


    def init_role_colors(self, all_roles, bg, alt_bg, guild_id=None):
        """Initialize 2 pairs of role colors for different backgrounds, for all or specific guild"""
        if guild_id:
            selected_id = self.role_color_start_id
        else:
            selected_id = None
        for guild in all_roles:
            if guild_id:
                if guild["guild_id"] != guild_id:
                    continue
            num = self.last_free_id
            for role in guild["roles"]:
                color = role["color"]
                found = False
                if not guild_id:
                    num = 0
                    # all guilds roles init at once
                    for guild_i in all_roles:
                        for role_i in guild_i["roles"]:
                            if "color_id" not in role_i:
                                break
                            if role_i["color"] == color:
                                role["color_id"] = role_i["color_id"]
                                role["alt_color_id"] = role_i["alt_color_id"]
                                found = True
                                break
                        if found:
                            break
                else:   # replacing colors from previous guild
                    num += 2
                if not found:
                    role["color_id"] = self.init_pair((color, bg, selected_id), force_id=num-1)
                    role["alt_color_id"] = self.init_pair((color, alt_bg, selected_id), force_id=num)
                    if guild_id:
                        selected_id += 1
            if guild_id:
                break
        return all_roles


    def spellcheck(self):
        """Spellcheck words visible on screen"""
        if not self.input_buffer or self.enable_autocomplete:
            return
        w = self.input_hw[1]
        line_start = max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
        # first space before line_start in self.input_buffer
        if split_char_in(self.input_buffer[:line_start]):
            range_word_start = len(rersplit_0(self.input_buffer[:line_start])) + bool(line_start)
        else:
            range_word_start = 0
        # when input buffer cant fit on screen
        if len(self.input_buffer) > w:
            # first space after line_start + input_line_w in self.input_buffer
            range_word_end = line_start + w + len(resplit(self.input_buffer[line_start+w:])[0])
        else:
            # first space before last word
            range_word_end = len(self.input_buffer) - len(resplit(self.input_buffer)[-1]) - split_char_in(self.input_buffer)
        # indexes of words visible on screen
        spelling_range = [range_word_start, range_word_end]
        if spelling_range != self.spelling_range:
            words_on_screen = resplit(self.input_buffer[range_word_start:range_word_end])
            misspelled_words_on_screen = self.spellchecker.check_list(words_on_screen)
            misspelled_words_on_screen.append(False)
            # loop over all words visible on screen
            self.misspelled = []
            index = 0
            for num, word in enumerate(resplit(self.input_buffer[line_start:line_start+w])):
                word_len = len(word)
                if misspelled_words_on_screen[num]:
                    self.misspelled.append([index, word_len])
                index += word_len + 1
            # self.misspelled format: [start_index_on_screen, word_len] for all misspelled words on screen
        self.spelling_range = spelling_range


    def add_to_delta_store(self, key, character=None):
        """Add input line delta to delta_store"""
        if key not in ("BACKSPACE", "DELETE", " ", "UNDO", "REDO"):
            action = "ADD"
        elif key == " ":
            action = "SPACE"
        else:
            action = key

        # clear future history when undo/redo then edit
        if self.last_action != action and ((self.last_action == "UNDO" and action != "REDO") or (self.last_action == "REDO" and action != "UNDO")):
            self.delta_store = self.delta_store[:self.undo_index]

        # add delta_cache to delta_store
        if self.last_action != action or abs(self.input_index - self.delta_index) >= 2:
            # checking index change for case when cursor is moved
            if self.delta_cache and (self.last_action != "SPACE" or (action not in ("ADD", "BACKSPACE", "DELETE"))):
                if self.last_action == "SPACE":
                    # space is still adding text
                    self.delta_store.append([self.delta_index - 1, self.delta_cache, "ADD"])
                else:
                    self.delta_store.append([self.delta_index - 1, self.delta_cache, self.last_action])
                if len(self.delta_store) > MAX_DELTA_STORE:
                    # limit history size
                    del self.delta_store[0]
                self.delta_cache = ""
            self.last_action = action

        # add to delta_cache
        if action == "BACKSPACE" and character:
            self.delta_cache = character + self.delta_cache
            self.delta_index = self.input_index
            self.undo_index = None
        elif action == "DELETE" and character:
            self.delta_cache += character
            self.delta_index = self.input_index
            self.undo_index = None
        elif action in ("ADD", "SPACE"):
            self.delta_cache += key
            self.delta_index = self.input_index
            self.undo_index = None


    def delete_selection(self):
        """Delete selected text in input line and add it to undo history"""
        input_select_start, input_select_end = self.store_input_selected()
        # delete selection
        self.input_buffer = self.input_buffer[:input_select_start] + self.input_buffer[input_select_end:]
        # add selection to undo history as backspace
        self.input_index = input_select_end
        _, w = self.input_hw
        self.input_line_index -= input_select_end - input_select_start
        self.input_line_index = min(max(0, self.input_line_index), max(0, len(self.input_buffer) - w))
        for letter in self.input_select_text[::-1]:
            self.input_index -= 1
            self.add_to_delta_store("BACKSPACE", letter)


    def common_keybindings(self, key, mouse=False, switch=False, command=False, forum=False, alt=False):
        """Handle keybinding events that can be executed by mouse events"""
        # chat_up / extra_up
        if key == 521 or (self.swap_assist and self.win_extra_window and key == 523 and not alt and not command):
            if command and key != 523:
                return 46
            if self.swap_assist and self.win_extra_window and not mouse and key == 521:
                self.common_keybindings(523, alt=True)
                return None
            if self.chat_selected + 1 < len(self.chat_buffer):
                top_line = self.chat_index + self.chat_hw[0] - 3
                if top_line + 3 < len(self.chat_buffer) and self.chat_selected >= top_line:
                    self.chat_index += 1   # move history down
                self.set_selected(self.chat_selected + 1, scroll=False)   # move selection up

        # chat_down / extra_down
        elif key == 522 or (self.swap_assist and self.win_extra_window and key == 524 and not alt and not command):
            if command and key != 524:
                return 47
            if self.swap_assist and self.win_extra_window and not mouse and key == 522:
                self.common_keybindings(524, alt=True)
                return None
            if self.chat_selected >= self.dont_hide_chat_selection:   # if it is -1, selection is hidden
                if self.chat_index and self.chat_selected <= self.chat_index + 2:   # +2 from status and input lines
                    self.chat_index -= 1   # move history up
                self.set_selected(self.chat_selected - 1, scroll=False)   # move selection down

        # tree_up
        elif key == 500:
            if self.tree_selected >= 0:
                if self.tree_index and self.tree_selected <= self.tree_index + 2:
                    self.tree_index -= 1
                self.tree_selected -= 1
                self.draw_tree()
            elif self.wrap_around:
                tree_end_index = self.get_tree_index(0)
                self.tree_selected = tree_end_index
                self.tree_index = max(self.tree_selected - (self.tree_hw[0] - 1), 0)
                self.draw_tree()

        elif key == 501:
            if self.tree_selected + 1 < self.tree_clean_len:
                top_line = self.tree_index + self.tree_hw[0]
                if top_line < self.tree_clean_len and self.tree_selected >= top_line - 3:
                    self.tree_index += 1
                self.tree_selected += 1
                self.draw_tree()
            elif self.wrap_around:
                self.tree_selected = 0
                self.tree_index = 0
                self.draw_tree()

        # tree_select
        elif key == 502:
            # if selected tree entry is channel
            if 300 <= self.tree_format[self.tree_selected_abs] <= 399 and not mouse:
                # stop wait_input and return so new prompt can be loaded
                return 4
            # if selected tree entry is dms drop down
            if self.tree_selected_abs == 0 and not switch:   # for dms
                if (self.tree_format[self.tree_selected_abs] % 10):
                    self.tree_format[self.tree_selected_abs] -= 1
                else:
                    self.tree_format[self.tree_selected_abs] += 1
                self.draw_tree()
            # if selected tree entry is guild drop-down
            elif 100 <= self.tree_format[self.tree_selected_abs] <= 199 and not switch:
                # this will trigger open_guild() in app.py that will update and expand tree
                return 19
            # if selected tree entry is threads drop-down
            elif 400 <= self.tree_format[self.tree_selected_abs] <= 599 and not mouse:
                # stop wait_input and return so new prompt can be loaded
                return 4
            # if selected tree entry is category drop-down
            elif self.tree_selected_abs >= 0 and not switch:
                if (self.tree_format[self.tree_selected_abs] % 10):
                    self.tree_format[self.tree_selected_abs] -= 1
                else:
                    self.tree_format[self.tree_selected_abs] += 1
                self.draw_tree()
            self.tree_format_changed = True

        # extra_up
        elif key == 523:
            if self.extra_window_body and not mouse:
                if self.extra_select:
                    if self.extra_selected > 0:
                        if self.extra_index and self.extra_selected <= self.extra_index:
                            self.extra_index -= 1
                        self.extra_selected -= 1
                        self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
                    elif self.wrap_around and not self.wrap_around_disable:
                        self.extra_selected = len(self.extra_window_body) - 1
                        self.extra_index = max(len(self.extra_window_body) - (self.win_extra_window.getmaxyx()[0] - 1), 0)
                        self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
                elif self.extra_index > 0:
                    self.extra_index -= 1
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
            elif self.win_member_list:
                if self.mlist_selected >= 0:
                    if self.mlist_index and self.mlist_selected <= self.mlist_index:
                        self.mlist_index -= 1
                    self.mlist_selected -= 1
                    self.draw_member_list(self.member_list, self.member_list_format)
                elif self.mlist_index > 0:
                    self.mlist_index -= 1
                    self.draw_member_list(self.member_list, self.member_list_format)

        # extra_down
        elif key == 524:
            if self.extra_window_body and not mouse:
                if self.extra_select:
                    if self.extra_selected + 1 < len(self.extra_window_body):
                        top_line = self.extra_index + self.win_extra_window.getmaxyx()[0] - 1
                        if top_line < len(self.extra_window_body) and self.extra_selected >= top_line - 1:
                            self.extra_index += 1
                        self.extra_selected += 1
                        self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
                    elif self.wrap_around and not self.wrap_around_disable:
                        self.extra_selected = 0
                        self.extra_index = 0
                        self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
                elif self.extra_index + 1 < len(self.extra_window_body):
                    self.extra_index += 1
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
            elif self.win_member_list:
                if self.mlist_selected + 1 < len(self.member_list):
                    top_line = self.mlist_index + self.win_member_list.getmaxyx()[0] - 1
                    if top_line < len(self.member_list) and self.mlist_selected >= top_line - 1:
                        self.mlist_index += 1
                    self.mlist_selected += 1
                    self.draw_member_list(self.member_list, self.member_list_format)

        # check extensions bindings
        else:
            ext_ret = self.execute_extensions_method_first("on_binding", key, command, forum, cache=True)
            if isinstance(ext_ret, int):
                return ext_ret

        return None


    def return_input_code(self, code):
        """Clean input line and return input code wit other data"""
        tmp = self.input_buffer
        self.input_buffer = ""
        return tmp, self.chat_selected, self.tree_selected_abs, code


    def wait_input(self, prompt="", init_text=None, reset=True, keep_cursor=False, autocomplete=False, clear_delta=False, forum=False, command=False, press=None):
        """
        Take input from user, and show it on screen.
        Return typed text, absolute_tree_position and whether channel is changed.
        """
        _, w = self.input_hw
        self.enable_autocomplete = autocomplete
        if reset:
            self.input_buffer = ""
            self.input_index = 0
            self.input_line_index = 0
            self.cursor_pos = 0
            self.assist_start = -1
        if init_text:
            self.input_buffer = init_text
            if not keep_cursor:
                w += len(self.prompt) - len(prompt)
                self.input_index = len(self.input_buffer)
                self.cursor_pos = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                self.cursor_pos = max(self.cursor_pos, 0)
                self.cursor_pos = min(w - 1, self.cursor_pos)
        if not self.disable_drawing:
            self.update_prompt(prompt)
            self.spellcheck()
            self.draw_input_line()
        if clear_delta:
            self.delta_store = []
            self.last_key = None
            self.delta_cache = ""
            self.undo_index = None
        selected_completion = 0
        self.keybinding_chain = None
        key = -1
        self.screen.timeout(250)
        while self.run:
            while self.disable_drawing:
                time.sleep(0.2)
            if press:
                key = press
            else:
                key = self.get_key(self.screen, self.backspace_code)
            if key == -1:
                continue

            if isinstance(key, tuple):
                code = self.mouse_events(key)
                if code:
                    return self.return_input_code(code)
                continue
            w = self.input_hw[1]

            # regular characters
            if len(key) == 1 and self.insert_mode:
                if self.input_select_start is not None:
                    self.delete_selection()
                    self.input_select_start = None
                self.input_buffer = self.input_buffer[:self.input_index] + key + self.input_buffer[self.input_index:]
                if self.fun == 2:
                    with self.fun_lock:
                        self.red_list.append(self.input_index)
                self.input_index += 1
                self.typing = int(time.time())
                if self.enable_autocomplete:
                    selected_completion = 0
                self.add_to_delta_store(key)
                self.show_cursor()
                if self.assist:
                    if key in ASSIST_TRIGGERS:
                        self.assist_start = self.input_index
                self.spellcheck()
                self.cursor_pos = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                self.cursor_pos = max(self.cursor_pos, 0)
                self.cursor_pos = min(w - 1, self.cursor_pos)
                self.draw_input_line()
                continue

            # switching tab with Binding+Num
            if key.startswith(self.switch_tab_modifier):
                try:
                    self.pressed_num_key = int(key[-1:])
                    return self.return_input_code(42)
                except ValueError:
                    pass

            # switch tab key in normal mode in vim mode
            elif not self.insert_mode and not self.switch_tab_modifier and key.isdigit():
                self.pressed_num_key = key - 48
                return self.return_input_code(42)

            if key in self.chainable and not self.keybinding_chain:
                self.keybinding_chain = key
                continue
            if self.keybinding_chain:
                key = f"{self.keybinding_chain} {"SPC" if key == " " else key}"
                self.keybinding_chain = None

            if key.startswith("PASTE"):
                self.paste_text(key[6:])

            key = self.key_map.get(key, key)

            # special keys
            if key == keybinding.KEY_ESCAPE:
                if self.assist_start:
                    self.assist_start = -1
                if self.vim_mode and self.insert_mode:
                    self.insert_mode = False
                    return self.return_input_code(26)
                return self.return_input_code(5)

            if key == keybinding.KEY_RESIZE:
                self.resize()
                _, w = self.input_hw
            elif key == keybinding.KEY_FOCUS_IN:
                self.focused = True
                return self.return_input_code(2000)
            elif key == keybinding.KEY_FOCUS_OUT:
                self.focused = False
                return self.return_input_code(2001)

            elif key == keybinding.KEY_BACKSPACE:
                if self.input_select_start is not None:
                    self.delete_selection()
                    self.input_select_start = None
                elif self.input_index > 0:
                    removed_char = self.input_buffer[self.input_index-1]
                    self.input_buffer = self.input_buffer[:self.input_index-1] + self.input_buffer[self.input_index:]
                    self.input_index -= 1
                    if self.enable_autocomplete:
                        selected_completion = 0
                    self.add_to_delta_store("BACKSPACE", removed_char)
                    self.show_cursor()
                self.spellcheck()

            elif key == keybinding.KEY_DEL:   # DEL
                if self.input_select_start is not None:
                    self.delete_selection()
                    self.input_select_start = None
                elif self.input_index < len(self.input_buffer):
                    removed_char = self.input_buffer[self.input_index]
                    self.input_buffer = self.input_buffer[:self.input_index] + self.input_buffer[self.input_index+1:]
                    self.add_to_delta_store("DELETE", removed_char)
                    self.show_cursor()
                self.spellcheck()

            elif key == keybinding.KEY_HOME:
                self.input_index = 0
                self.input_line_index = 0
                self.input_select_start = None
                self.spellcheck()

            elif key == keybinding.KEY_END:
                self.input_index = len(self.input_buffer)
                self.input_select_start = None
                self.spellcheck()

            elif key == keybinding.KEY_TAB:
                if self.enable_autocomplete:
                    if self.input_buffer and self.input_index == len(self.input_buffer):
                        completions = utils.complete_path(self.input_buffer, separator=False)
                        if completions:
                            path = completions[selected_completion]
                            self.input_buffer = path
                            self.input_index = len(path)
                            self.show_cursor()
                            selected_completion += 1
                            if selected_completion > len(completions) - 1:
                                selected_completion = 0
                else:   # regular text
                    if self.input_select_start is not None:
                        self.delete_selection()
                        self.input_select_start = None
                    tab_str = " " * self.tab_spaces
                    self.input_buffer = self.input_buffer[:self.input_index] + tab_str + self.input_buffer[self.input_index:]
                    self.input_index += self.tab_spaces
                    self.typing = int(time.time())
                    self.add_to_delta_store(tab_str)
                    self.show_cursor()
                    self.spellcheck()

            elif (code := self.common_keybindings(key, command=command, forum=forum)):
                return self.return_input_code(code)

            # keys from config with custom actions

            elif key == 520:   # send_message
                if forum:
                    self.input_index = 0
                    self.input_line_index = 0
                    self.cursor_pos = 0
                    self.draw_input_line()
                self.cursor_on = True
                self.input_select_start = None
                return self.return_input_code(0)

            elif key == 504:   # input_left
                if self.input_index > 0:
                    # if index hits left screen edge, but there is more text to left, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index) == 0:
                        self.input_line_index += min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index -= 1
                    self.show_cursor()
                self.input_select_start = None
                self.spellcheck()

            elif key == 505:   # input_right
                if self.input_index < len(self.input_buffer):
                    # if index hits right screen edge, but there is more text to right, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w - self.input_line_index) == w:
                        self.input_line_index -= min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index += 1
                    self.show_cursor()
                self.input_select_start = None
                self.spellcheck()

            elif key == 518:   # delete_word
                word = ""
                for word_part in resplit(self.input_buffer[:self.input_index])[::-1]:
                    if word_part == "":
                        word += " "
                    else:
                        word += word_part
                        break
                if word:
                    self.input_buffer = self.input_buffer[:self.input_index-len(word)] + self.input_buffer[self.input_index:]
                    self.input_index -= len(word)
                    self.input_index = max(self.input_index, 0)
                    input_line_index_diff = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                    if input_line_index_diff <= 0:
                        self.input_line_index -= input_line_index_diff - 4   # diff is negative
                        self.input_line_index = min(max(0, self.input_line_index), max(0, len(self.input_buffer) - w))
                    self.input_select_start = None
                    if self.enable_autocomplete:
                        selected_completion = 0
                    for char in word:
                        self.add_to_delta_store("BACKSPACE", char)
                    self.spellcheck()

            elif key == 519:   # delete_word_forward
                word = ""
                for word_part in resplit(self.input_buffer[self.input_index:]):
                    if word_part == "":
                        word += " "
                    else:
                        word += word_part
                        break
                if word:
                    self.input_buffer = self.input_buffer[:self.input_index] + self.input_buffer[self.input_index+len(word):]
                    input_line_index_diff = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                    if input_line_index_diff <= 0:
                        self.input_line_index -= input_line_index_diff - 4   # diff is negative
                        self.input_line_index = min(max(0, self.input_line_index), max(0, len(self.input_buffer) - w))
                    self.input_select_start = None
                    if self.enable_autocomplete:
                        selected_completion = 0
                    for char in word:
                        self.add_to_delta_store("DELETE", char)
                    self.spellcheck()

            elif key == 506:   # word_left
                left_len = 0
                for word in resplit(self.input_buffer[:self.input_index])[::-1]:
                    if word == "":
                        left_len += 1
                    else:
                        left_len += len(word)
                        break
                self.input_index -= left_len
                self.input_index = max(self.input_index, 0)
                input_line_index_diff = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                if input_line_index_diff <= 0:
                    self.input_line_index -= input_line_index_diff - 4   # diff is negative
                    self.input_line_index = min(max(0, self.input_line_index), max(0, len(self.input_buffer) - w))
                self.input_select_start = None
                self.spellcheck()

            elif key == 507:   # word_right
                left_len = 0
                for word in resplit(self.input_buffer[self.input_index:]):
                    if word == "":
                        left_len += 1
                    else:
                        left_len += len(word)
                        break
                self.input_index += left_len
                self.input_index = min(self.input_index, len(self.input_buffer))
                input_line_index_diff = self.input_index - max(0, len(self.input_buffer) - w - self.input_line_index) - w
                if input_line_index_diff >= 0:
                    self.input_line_index -= input_line_index_diff + 4   # diff is negative
                    self.input_line_index = min(max(0, self.input_line_index), max(0, len(self.input_buffer) - w))
                self.input_select_start = None
                self.spellcheck()

            elif key == 510:   # select_word_left
                if self.input_select_start is None:
                    self.input_select_end = self.input_select_start = self.input_index
                left_len = 0
                for word in resplit(self.input_buffer[:self.input_index])[::-1]:
                    if word == "":
                        left_len += 1
                    else:
                        left_len += len(word)
                        break
                self.input_index -= left_len
                self.input_index = max(self.input_index, 0)
                input_line_index_diff = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                if input_line_index_diff <= 0:
                    self.input_line_index -= input_line_index_diff - 4   # diff is negative
                    self.input_line_index = min(max(0, self.input_line_index), max(0, len(self.input_buffer) - w))
                if self.input_select_start is not None:
                    self.input_select_end -= left_len
                    self.input_select_end = min(max(0, self.input_select_end), len(self.input_buffer))
                self.spellcheck()

            elif key == 511:   # select_word_right
                if self.input_select_start is None:
                    self.input_select_end = self.input_select_start = self.input_index
                left_len = 0
                for word in resplit(self.input_buffer[self.input_index:]):
                    if word == "":
                        left_len += 1
                    else:
                        left_len += len(word)
                        break
                self.input_index += left_len
                self.input_index = min(self.input_index, len(self.input_buffer))
                input_line_index_diff = self.input_index - max(0, len(self.input_buffer) - w - self.input_line_index) - w
                if input_line_index_diff >= 0:
                    self.input_line_index -= input_line_index_diff + 4   # diff is negative
                    self.input_line_index = min(max(0, self.input_line_index), max(0, len(self.input_buffer) - w))
                if self.input_select_start is not None:
                    self.input_select_end += left_len
                    self.input_select_end = min(max(0, self.input_select_end), len(self.input_buffer))
                self.spellcheck()

            elif key == 513:   # undo
                self.add_to_delta_store("UNDO")
                if self.undo_index is None:
                    self.undo_index = len(self.delta_store) - 1
                    undo = True
                elif self.undo_index > 0:
                    self.undo_index -= 1
                    undo = True   # dont undo if hit history end
                if undo and self.undo_index >= 0:
                    # get delta
                    delta_index, delta_text, delta_code = self.delta_store[self.undo_index]
                    if delta_code == "ADD":
                        # remove len(delta_text) before index_pos
                        self.input_buffer = self.input_buffer[:delta_index - len(delta_text) + 1] + self.input_buffer[delta_index + 1:]
                        self.input_index = delta_index - len(delta_text) + 1
                    elif delta_code == "BACKSPACE":
                        # add text at index pos
                        self.input_buffer = self.input_buffer[:delta_index+1] + delta_text + self.input_buffer[delta_index+1:]
                        self.input_index = delta_index + len(delta_text) + 1
                    elif delta_code == "DELETE":
                        # add text at index pos
                        self.input_buffer = self.input_buffer[:delta_index+1] + delta_text + self.input_buffer[delta_index+1:]
                        self.input_index = delta_index + 1
                self.input_select_start = None
                self.spellcheck()

            elif key == 514:   # redo
                self.add_to_delta_store("REDO")
                if self.undo_index is not None and self.undo_index < len(self.delta_store):
                    self.undo_index += 1
                    # get delta
                    delta_index, delta_text, delta_code = self.delta_store[self.undo_index - 1]
                    if delta_code == "ADD":
                        # add text at index_pos - len(text)
                        delta_index = delta_index - len(delta_text) + 1
                        self.input_buffer = self.input_buffer[:delta_index] + delta_text + self.input_buffer[delta_index:]
                        self.input_index = delta_index + len(delta_text)
                    elif delta_code in ("BACKSPACE", "DELETE"):
                        # remove len(text) after index pos
                        self.input_buffer = self.input_buffer[:delta_index + 1] + self.input_buffer[delta_index + len(delta_text) + 1:]
                        self.input_index = delta_index + 1
                self.input_select_start = None
                self.spellcheck()

            elif key == 508:   # select_left
                if self.input_select_start is None:
                    self.input_select_start = self.input_index
                if self.input_index > 0:
                    # if index hits left screen edge, but there is more text to left, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index) == 0:
                        self.input_line_index += min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index -= 1
                self.input_select_end = self.input_index

            elif key == 509:   # select_right
                if self.input_select_start is None:
                    self.input_select_start = self.input_index
                if self.input_index < len(self.input_buffer):
                    # if index hits right screen edge, but there is more text to right, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w - self.input_line_index) == w:
                        self.input_line_index -= min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index += 1
                self.input_select_end = self.input_index

            elif key == 515:   # select_all
                self.input_select_start = 0
                self.input_select_end = len(self.input_buffer)

            elif key == 516:   # copy
                if self.input_select_start is not None:
                    self.store_input_selected()
                    return self.return_input_code(20)
                return self.return_input_code(12)

            elif key == 517 and self.input_select_start is not None:   # cut
                self.delete_selection()
                self.input_select_start = None
                self.cursor_pos = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                self.cursor_pos = max(self.cursor_pos, 0)
                self.cursor_pos = min(w - 1, self.cursor_pos)
                self.draw_input_line()
                return self.return_input_code(20)

            elif key == 512:   # insert_newline
                self.input_buffer = self.input_buffer[:self.input_index] + "\n" + self.input_buffer[self.input_index:]
                self.input_index += 1
                self.show_cursor()
                self.spellcheck()

            elif key == 503:   # tree_collapse_threads
                if (self.tree_format[self.tree_selected_abs] % 10):
                    self.tree_format[self.tree_selected_abs] -= 1
                else:
                    self.tree_format[self.tree_selected_abs] += 1
                self.draw_tree()
                self.tree_format_changed = True

            elif key == 13 and not forum:   # upload
                self.enable_autocomplete = True
                self.misspelled = []
                return self.return_input_code(13)

            elif key == 24 and self.chat_selected != -1 and not forum:   # profile_info
                self.extra_index = 0
                self.extra_selected = -1
                return self.return_input_code(24)

            elif key == 25 and self.tree_selected > 0:   # channel_info
                self.extra_index = 0
                self.extra_selected = -1
                return self.return_input_code(25)

            elif key == 29 and not forum:   # search
                self.extra_index = 0
                self.extra_selected = -1
                return self.return_input_code(29)

            elif self.vim_mode and key == 525:   # insert_mode for vim mode
                self.insert_mode = True
                return self.return_input_code(28)

            # keys that only return a value to app.py
            elif isinstance(key, int):
                if forum and key in (1, 2, 3, 6, 8, 18, 30, 31, 36, 37, 43, 44, 45):
                    pass
                elif self.tree_selected == -1 and key in (1, 2, 3, 8, 9, 10, 17, 18, 30, 31):
                    pass
                elif key < 500:
                    return self.return_input_code(key)

            if key in self.command_bindings:
                return self.return_input_code((50, self.command_bindings.get(key)))

            # terminal reserved keys: CTRL+ C, I, J, M, Q, S, Z

            # keep index inside screen
            self.cursor_pos = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
            self.cursor_pos = max(self.cursor_pos, 0)
            self.cursor_pos = min(w - 1, self.cursor_pos)
            self.draw_input_line()

            if press:
                break
        return self.return_input_code(None)


    def mouse_events(self, key):
        """Handle mouse events on terminal screen"""
        y, x, key_type, pressed = key
        if self.fallback_keybinding_parser:
            while self.run and sys.platform != "win32":   # drain internal curses buffer for mouse events
                # windows-curses doesnt error here, so this cant be used
                try:
                    _, x, y, _, bstate = curses.getmouse()
                    key_type, pressed = keybinding.decode_bstate_flag(bstate)
                except curses.error:
                    break
        if pressed and key_type == 0:   # LEFT CLICK
            chat_y, chat_x = self.win_chat.getbegyx()
            if self.have_scrollbar and x == chat_x + self.chat_hw[1] and y > chat_y and y < chat_y + self.chat_hw[0]:
                self.drag_scrollbar()
                return
            new_click = (time.time(), x, y)
            if new_click[0] - self.first_click[0] < 0.5 and new_click[1:] == self.first_click[1:]:
                self.first_click = (0, 0, 0)
                return self.mouse_double_click(x, y)
            self.first_click = new_click
            return self.mouse_single_click(x, y)
        if pressed and key_type == 64:   # SCROLL UP
            self.mouse_scroll(x, y, True)
        elif pressed and key_type == 65:   # SCROLL DOWN
            self.mouse_scroll(x, y, False)
        elif pressed and key_type == 1:   # MIDDLE CLICK
            return self.mouse_middle_click(x, y)
        return None


    def mouse_in_window(self, x, y, window, around=False):
        """Check if mouse is inside specified window"""
        win_y, win_x = window.getbegyx()
        win_h, win_w = window.getmaxyx()
        if around:
            win_y -= 1
            win_x -= 1
            win_h += 2
            win_w += 2
        return (win_x <= x < win_x + win_w) and (win_y <= y < win_y + win_h)


    def mouse_rel_pos(self, x, y, window):
        """Get mouse position relative to specified window"""
        win_y, win_x = window.getbegyx()
        return (x - win_x, y - win_y)


    def mouse_single_click(self, x, y):
        """Handle mouse single click events"""
        if self.mouse_in_window(x, y, self.win_tree):
            if self.tree_width < 10:
                return 32   # app will toggle
            x, y = self.mouse_rel_pos(x, y, self.win_tree)
            self.tree_selected = self.tree_index + y
            self.draw_tree()
            return self.common_keybindings(502, mouse=True)

        if self.mouse_in_window(x, y, self.win_chat):
            x, y = self.mouse_rel_pos(x, y, self.win_chat)
            new_pos = self.chat_index + self.win_chat.getmaxyx()[0] - y - 1
            self.set_selected(new_pos, scroll=False)

        elif self.win_member_list and self.mouse_in_window(x, y, self.win_member_list):
            if self.member_list_width < 5:
                return 35   # app will toggle
            x, y = self.mouse_rel_pos(x, y, self.win_member_list)
            self.mlist_selected = self.mlist_index + y
            self.draw_member_list(self.member_list, self.member_list_format)

        elif self.mouse_in_window(x, y, self.win_input_line):
            x, y = self.mouse_rel_pos(x, y, self.win_input_line)
            input_index = min(x + max(0, len(self.input_buffer) - self.input_hw[1] + 1 - self.input_line_index), len(self.input_buffer))
            self.set_input_index(input_index)
            self.draw_input_line()

        elif self.win_extra_window and self.mouse_in_window(x, y, self.win_extra_window) and self.extra_select:
            x, y = self.mouse_rel_pos(x, y, self.win_extra_window)
            if y == 0:
                self.drag_extra_window()
                return None
            self.extra_selected = self.extra_index + y - 1
            self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)

        elif self.win_extra_line and self.mouse_in_window(x, y, self.win_extra_line):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_extra_line)[0]
            return 48   # special handling

        elif self.win_title_line and self.mouse_in_window(x, y, self.win_title_line):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_title_line)[0]
            return 16   # special handling

        elif self.win_subtitle_line and self.mouse_in_window(x, y, self.win_subtitle_line):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_subtitle_line)[0]
            return 51   # special handling

        elif self.mouse_in_window(x, y, self.win_tree, around=True):
            return 32   # app will toggle

        elif self.win_member_list and self.mouse_in_window(x, y, self.win_member_list, around=True):
            return 35   # app will toggle


    def mouse_double_click(self, x, y):
        """Handle mouse double click events"""
        if self.tree_width >= 10 and self.mouse_in_window(x, y, self.win_tree):
            return self.common_keybindings(502, switch=True)

        if self.mouse_in_window(x, y, self.win_chat):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_chat)[0]
            return 40   # special handling

        if self.win_extra_window and self.mouse_in_window(x, y, self.win_extra_window):
            return 27   # select in extra window

        if self.win_member_list and self.member_list_width >= 5 and self.mouse_in_window(x, y, self.win_member_list):
            return 39   # select in member list

        if self.mouse_in_window(x, y, self.win_input_line):
            start, end = select_word(self.input_buffer, self.input_index)
            if not end:
                return None
            self.input_select_start = start
            self.input_select_end = end + 1
            self.draw_input_line()
            self.set_input_index(self.input_select_end)
            self.input_select_start = start

        elif self.win_subtitle_line and self.mouse_in_window(x, y, self.win_subtitle_line):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_subtitle_line)[0]
            return 52   # special handling


    def mouse_middle_click(self, x, y):
        """Handle mouse middle click events"""
        if self.tree_width >= 10 and self.mouse_in_window(x, y, self.win_tree):
            x, y = self.mouse_rel_pos(x, y, self.win_tree)
            self.tree_selected = self.tree_index + y
            self.draw_tree()
            return 49

        if self.win_subtitle_line and self.mouse_in_window(x, y, self.win_subtitle_line):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_subtitle_line)[0]
            return 53   # special handling


    def mouse_scroll(self, x, y, up):
        """Handle mouse scroll events, by scrolling selection"""
        if self.mouse_in_window(x, y, self.win_tree):
            if up:
                self.common_keybindings(500)
            else:
                self.common_keybindings(501)

        elif self.mouse_in_window(x, y, self.win_chat):
            if up:
                self.common_keybindings(521)
            else:
                self.common_keybindings(522)

        elif self.win_extra_window and self.mouse_in_window(x, y, self.win_extra_window):
            if up:
                self.common_keybindings(523)
            else:
                self.common_keybindings(524)

        elif self.win_member_list and self.mouse_in_window(x, y, self.win_member_list):
            if up:
                self.common_keybindings(523, mouse=True)
            else:
                self.common_keybindings(524, mouse=True)


    def mouse_scroll_content(self, x, y, up):
        """Handle mouse scroll events, by scrolling content"""
        if self.mouse_in_window(x, y, self.win_tree):
            if up:
                if self.tree_index:
                    self.tree_index -= min(self.mouse_scroll_sensitivity, self.tree_index)
                    self.draw_tree()
            elif self.tree_index + self.tree_hw[0] < self.tree_clean_len:
                self.tree_index += self.mouse_scroll_sensitivity
                self.draw_tree()

        elif self.mouse_in_window(x, y, self.win_chat):
            if up:
                if self.chat_index + self.chat_hw[0] - 3 + 3 < len(self.chat_buffer):
                    self.chat_index += self.mouse_scroll_sensitivity
                    self.draw_chat()
                else:
                    self.chat_scrolled_top = True
            elif self.chat_index:
                self.chat_index -= min(self.mouse_scroll_sensitivity, self.chat_index)
                self.draw_chat()
                self.chat_scrolled_top = False

        elif self.win_extra_window and self.mouse_in_window(x, y, self.win_extra_window):
            if up:
                if self.extra_index:
                    self.extra_index -= min(self.mouse_scroll_sensitivity, self.extra_index)
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)
            elif self.extra_index + self.win_extra_window.getmaxyx()[0] - 1 < len(self.extra_window_body):
                self.extra_index += self.mouse_scroll_sensitivity
                self.draw_extra_window(self.extra_window_title, self.extra_window_body, select=self.extra_select, reset_scroll=False)

        elif self.win_member_list and self.mouse_in_window(x, y, self.win_member_list):
            if up:
                if self.mlist_index:
                    self.mlist_index -= min(self.mouse_scroll_sensitivity, self.mlist_index)
                    self.draw_member_list(self.member_list, self.member_list_format)
            elif self.mlist_index + self.win_member_list.getmaxyx()[0] - 1 < len(self.member_list):
                self.mlist_index += self.mouse_scroll_sensitivity
                self.draw_member_list(self.member_list, self.member_list_format)


    def drag_extra_window(self):
        """Handle extra window resizing with mouse dragging until mouse is released"""
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        sys.stdout.write("\x1b[?1003h")   # enable mouse movement reporting
        sys.stdout.flush()
        prev_y = None
        first = True
        try:
            while self.run:
                key = self.get_key(self.screen)
                if not isinstance(key, tuple):
                    continue
                y, x, key_type, pressed = key
                if y != prev_y:
                    h = self.screen_hw[0] - 2 - 2*self.bordered - y
                    self.set_extra_height(h)
                    prev_y = y
                if (not pressed and key_type == 0) or (pressed and key_type == 0 and not first):
                    break
                first = False
        except curses.error:
            return
        finally:   # restore old state
            sys.stdout.write("\x1b[?1003l")
            sys.stdout.flush()
            curses.mousemask(curses.ALL_MOUSE_EVENTS)


    def drag_scrollbar(self):
        """Handle dragging scrollbar with mouse until mouse is released"""
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        sys.stdout.write("\x1b[?1003h")   # enable mouse movement reporting
        sys.stdout.flush()
        prev_y = None
        first_y = None
        first = True
        on_thumb = False
        y = None
        y_in_thumb = None
        try:
            while self.run:
                key = self.get_key(self.screen)
                if not isinstance(key, tuple):
                    continue
                y, x, key_type, pressed = key
                if first:
                    rel_y = y - self.win_chat.getbegyx()[0]
                    y_in_thumb, on_thumb = self.calculate_y_in_thumb(rel_y)
                    first_y = y
                    if not on_thumb:
                        break
                if y != prev_y:
                    if not first:
                        rel_y = y - self.win_chat.getbegyx()[0]
                        self.move_scrollbar(rel_y, y_in_thumb)
                    prev_y = y
                if (not pressed and key_type == 0) or (pressed and key_type == 0 and not first):
                    break
                first = False
            if y is not None and first_y == y and not on_thumb:
                rel_y = y - self.win_chat.getbegyx()[0]
                self.move_scrollbar(rel_y, y_in_thumb)
        except curses.error:
            return
        finally:   # restore old state
            sys.stdout.write("\x1b[?1003l")
            sys.stdout.flush()
            curses.mousemask(curses.ALL_MOUSE_EVENTS)


    def calculate_y_in_thumb(self, rel_y):
        """Calculate where on thumb is clicked"""
        total_lines = len(self.chat_buffer)
        chat_h = self.chat_hw[0]
        if total_lines < chat_h:
            return None
        thumb_size = max(2, chat_h * chat_h // total_lines)
        max_pos = chat_h - thumb_size
        max_index = total_lines - chat_h
        thumb_pos = max(0, min(max_pos, max_pos - int(self.chat_index * max_pos / max_index)))
        if thumb_pos <= rel_y < thumb_pos + thumb_size:
            return rel_y - thumb_pos, True
        return thumb_size // 2, False


    def move_scrollbar(self, rel_y, y_in_thumb):
        """Move scrollbar to specified position"""
        total_lines = len(self.chat_buffer)
        chat_h = self.chat_hw[0]
        if total_lines < chat_h:
            return
        thumb_size = max(2, chat_h * chat_h // total_lines)
        max_pos = chat_h - thumb_size
        max_index = total_lines - chat_h
        new_thumb_pos = max(0, min(max_pos, rel_y - y_in_thumb))
        self.chat_index = int((max_pos - new_thumb_pos) * max_index / max_pos)
        self.draw_chat()
