import curses
import importlib.util
import logging
import re
import sys
import threading
import time

from endcord import acs, peripherals

logger = logging.getLogger(__name__)
uses_pgcurses = hasattr(curses, "PGCURSES")
INPUT_LINE_JUMP = 20   # jump size when moving input line
MAX_DELTA_STORE = 50   # limit undo size
MIN_ASSIST_LETTERS = 2
ASSIST_TRIGGERS = ("#", "@", ":", ";")
APP_COMMAND_ASSIST_TRIEGGER = "/"
if sys.platform == "win32":
    BACKSPACE = 8   # i cant believe this
else:
    BACKSPACE = curses.KEY_BACKSPACE
BUTTON4_PRESSED = getattr(curses, "BUTTON4_PRESSED", 0)
BUTTON5_PRESSED = getattr(curses, "BUTTON5_PRESSED", 0)
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
    """Replace existing item or append to list if it doesnt exist"""
    try:
        input_list[index] = item
    except IndexError:
        input_list.append(item)
    return input_list


def trim_with_dash(text, dash=True):
    """Trim spaces from a line and add '─' if there were spaces prepended"""
    if dash and text and text[0] == " ":
        return "─" + text.strip()
    return text.strip()


def replace_spaces_dash(text):
    """Replace more than 3 spaces with ' ─ '"""
    return match_spaces.sub(lambda match: " " + ("─" * (len(match.group(0)))) + " ", text)


def safe_insch(screen, y, x, character, color):
    """
    Safely insert character into line.
    This is because curses.insch will throw exception for weird charcters.
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


def get_key(screen):
    """Reads a key from curses and returns int for ascii, special and ctrl+key, str for utf-8 wide chars"""
    first = screen.getch()
    if first == -1:
        return -1

    # ascii, control, special keys
    if first < 0x80 or first > 0xFF:
        return first

    # utf-8 lead byte
    buf = bytes([first])
    if 0xC0 <= first <= 0xDF:
        n_bytes = 2
    elif 0xE0 <= first <= 0xEF:
        n_bytes = 3
    elif 0xF0 <= first <= 0xF7:
        n_bytes = 4
    else:
        return first

    # rest of utf-8 sequence
    for _ in range(n_bytes - 1):
        ch = screen.getch()
        if ch == -1 or ch < 0x80 or ch > 0xBF:
            return first
        buf += bytes([ch])

    # decode sequence
    try:
        return buf.decode("utf-8")
    except UnicodeDecodeError:
        return first


def draw_chat(win_chat, h, w, chat_buffer, chat_format, chat_index, chat_selected, attrib_map, color_default):
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
                            color_ready = curses.color_pair(color) | attrib_map[color]
                        safe_insch(win_chat, y, pos, character, color_ready)
                        break
                else:
                    safe_insch(win_chat, y, pos, character, default_color)

    # fill empty lines with spaces so background is drawn all the way
    y -= 1
    while y >= 0:
        win_chat.insstr(y, 0, "\n", curses.color_pair(0))
        y -= 1


# use cython if available, ~1.5 times faster
if importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.tui"):
    from endcord_cython.tui import draw_chat


class TUI():
    """Methods used to draw terminal user interface"""

    def __init__(self, screen, config, keybindings, command_bindings):
        self.spellchecker = peripherals.SpellCheck(config["aspell_mode"], config["aspell_lang"])
        acs_map = acs.get_map()
        curses.use_default_colors()
        curses.curs_set(0)   # using custom cursor
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)
        print("\x1b[?2004h")   # enable bracketed paste mode
        self.last_free_id = 1   # last free color pair id
        self.color_cache = []   # for restoring colors   # 255_curses_bug
        self.attrib_map = [0]   # has 0 so its index starts from 1 to be matched with color pairs
        tree_bg = config["color_tree_default"][1]
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
        self.init_pair((46, tree_bg))    # green   # 18
        self.init_pair((208, tree_bg))   # orange
        self.init_pair((196, tree_bg))   # red
        self.init_pair(config["color_extra_window"])   # 21
        curses.init_pair(255, config["color_default"][0], config["color_default"][1])   # temporary
        self.default_color = 255
        self.role_color_start_id = self.last_free_id   # starting id for role colors
        self.keybindings = {key: (val,) if not isinstance(val, tuple) else val for key, val in keybindings.items()}
        self.switch_tab_modifier = self.keybindings["switch_tab_modifier"][0][:-4]
        self.command_bindings = command_bindings
        self.screen = screen
        self.extensions = []

        # load config
        self.bordered = not(config["compact"])
        self.have_title = bool(config["format_title_line_l"])
        self.have_title_tree = bool(config["format_title_tree"])
        vert_line = config["tree_vert_line"][0]
        self.vert_line = acs_map.get(vert_line, vert_line)
        self.tree_width = config["tree_width"]
        self.extra_window_h = config["extra_window_height"]
        self.blink_cursor_on = config["cursor_on_time"]
        self.blink_cursor_off = config["cursor_off_time"]
        self.tree_dm_status = config["tree_dm_status"]
        self.member_list_width = config["member_list_width"]
        self.assist = config["assist"]
        self.wrap_around = config["wrap_around"]
        self.mouse = config["mouse"]
        self.screen_update_delay = min(config["screen_update_delay"], 0.01)
        self.mouse_scroll_sensitivity = min(max(1, config["mouse_scroll_sensitivity"]), 10)
        self.corner_ul = config["border_corners"][0]
        self.corner_ur = config["border_corners"][2]
        self.corner_dl = config["border_corners"][1]
        self.corner_dr = config["border_corners"][3]
        self.tab_spaces = int(config["tab_spaces"])

        # select bordered method
        if self.bordered:
            self.resize = self.resize_bordered

        # select mouse scroll method
        if not config["mouse_scroll_selection"]:
            self.mouse_scroll = self.mouse_scroll_content

        # find all keybinding first-chain-parts
        self.init_chainable()

        # initial values
        if not (self.blink_cursor_on and self.blink_cursor_off):
            self.enable_blink_cursor = False
        else:
            self.enable_blink_cursor = True
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
        self.title_tree_txt = ""
        self.chat_buffer = []
        self.chat_format = []
        self.wide_map = []
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
        self.bracket_paste = False
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
        self.extra_window_title = ""
        self.extra_window_body = ""
        self.member_list = []
        self.member_list_format = []
        self.red_list = []
        self.extra_selected = -1
        self.extra_index = 0
        self.extra_select = False
        self.mlist_selected = -1
        self.mlist_index = 0
        self.fun = 0
        self.fun_thread = None
        self.run = True
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
        self.pressed_num_key = None

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


    def init_chainable(self):
        """Find all first-parts of chained keybindings"""
        self.chainable = []
        for binding_group in self.keybindings.values():
            for binding in binding_group:
                if isinstance(binding, str):
                    split_binding = binding.split("-")
                    if len(split_binding) == 1:
                        continue
                    elif len(split_binding) > 2:
                        logger.warn(f"Invalid keybinding: {binding}")
                    self.chainable.append(split_binding[0])
        for binding in self.command_bindings:   # here read key instead value
            if isinstance(binding, str):
                split_binding = binding.split("-")
                if len(split_binding) == 1:
                    continue
                elif len(split_binding) > 2:
                    logger.warn(f"Invalid keybinding: {binding}")
                self.chainable.append(split_binding[0])


    def load_extensions(self, extensions):
        """Load already initialized extensions from app class"""
        self.extensions = extensions
        self.extension_cache = []

        # init bindings
        for extension in self.extensions:
            method = getattr(extension, "init_bindings", None)
            if callable(method):
                new_bindings = method(self.keybindings)
                if isinstance(new_bindings, dict):
                    self.keybindings.update(new_bindings)
        self.init_chainable()


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


    def screen_update(self):
        """Thread that updates drawn content on physical screen"""
        while True:
            self.need_update.wait()
            # here must be delay, otherwise output gets messed up
            with self.lock:
                time.sleep(self.screen_update_delay)
                curses.doupdate()
                self.need_update.clear()


    def resize(self, redraw_only=False):
        """Resize screen area and redraw ui"""
        # re-init areas
        if not redraw_only:
            h, w = self.screen.getmaxyx()
            chat_hwyx = (
                h - 2 - self.have_title,
                w - (self.tree_width + 1),
                self.have_title,
                self.tree_width + 1,
            )
            prompt_hwyx = (1, len(self.prompt), h - 1, self.tree_width + 1)
            input_line_hwyx = (
                1,
                w - (self.tree_width + 1) - len(self.prompt),
                h - 1,
                self.tree_width + len(self.prompt) + 1,
            )
            status_line_hwyx = (1, w - (self.tree_width + 1), h - 2, self.tree_width + 1)
            tree_hwyx = (
                h - self.have_title_tree,
                self.tree_width,
                self.have_title,
                0,
            )
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
            self.screen.vline(0, self.tree_hw[1], self.vert_line, self.screen_hw[0], curses.color_pair(self.default_color))
            if self.have_title and self.have_title_tree:
                # fill gap between titles
                self.screen.addch(0, self.tree_hw[1], self.vert_line, curses.color_pair(12))
            self.screen.noutrefresh()
            self.need_update.set()
        self.draw_status_line()
        self.draw_chat()
        self.update_prompt(self.prompt)
        self.spellcheck()
        self.draw_input_line()
        self.draw_tree()
        if self.have_title:
            self.draw_title_line()
        if self.have_title_tree:
            self.draw_title_tree()
        self.draw_extra_line(self.extra_line_text)
        self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
        self.draw_member_list(self.member_list, self.member_list_format, force=True, clean=not(redraw_only))


    def resize_bordered(self, redraw_only=False):
        """Resize screen area and redraw ui in bordered mode"""
        h, w = self.screen.getmaxyx()
        chat_hwyx = (
            h - 4 - self.have_title,
            w - (self.tree_width + 4) - bool(self.member_list),
            self.have_title,
            self.tree_width + 3,
        )
        win_prompt_input_line = (1, w - self.tree_width - 4, h - 2, self.tree_width + 3)
        tree_hwyx = (
            h - self.have_title_tree - 1,
            self.tree_width,
            self.have_title,
            1,
        )

        # re-init areas
        if not redraw_only:
            prompt_hwyx = (1, len(self.prompt), h - 2, self.tree_width + 3)
            input_line_hwyx = (
                1,
                w - (self.tree_width + 2) - len(self.prompt) - 2,
                h - 2,
                self.tree_width + len(self.prompt) + 3,
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
        self.draw_border(chat_hwyx, top=not(self.have_title))
        self.draw_chat()
        self.update_prompt(self.prompt)
        self.spellcheck()
        self.draw_input_line()
        self.draw_border(tree_hwyx, top=not(self.have_title_tree))
        self.draw_tree()
        if self.have_title:
            self.draw_title_line()
        if self.have_title_tree:
            self.draw_title_tree()
        self.draw_extra_line(self.extra_line_text)
        self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
        if self.win_extra_window:   # redraw borders for extra window
            extra_window_hwyx = self.win_extra_window.getmaxyx() + self.win_extra_window.getbegyx()
            self.draw_border(extra_window_hwyx, top=False, bot=False)
            y, x = extra_window_hwyx[2], extra_window_hwyx[3]
            self.screen.addstr(y, x - 1, self.corner_ul, curses.color_pair(self.default_color))
            self.screen.addstr(y, x + extra_window_hwyx[1], self.corner_ur, curses.color_pair(self.default_color))
        self.draw_member_list(self.member_list, self.member_list_format, force=True, clean=not(redraw_only))
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
            common_h,
            w - (self.tree_width + 3 * self.bordered + 1) - bool(self.member_list) * (self.member_list_width + 1),
            self.have_title,
            self.tree_width + 2 * self.bordered + 1,
        )
        self.win_chat = self.screen.derwin(*chat_hwyx)
        self.chat_hw = self.win_chat.getmaxyx()
        if self.bordered:
            self.draw_border(chat_hwyx, top=not(self.have_title))
            self.screen.noutrefresh()
        return common_h


    def pause_curses(self):
        """Pause curses and disable drawing, releasing terminal"""
        with self.lock:
            h, w = self.win_member_list.getmaxyx()
            for y in range(h):
                self.win_member_list.insstr(y, 0, " " * w, curses.color_pair(1))
            self.win_member_list.noutrefresh()
            self.need_update.set()
        time.sleep(0.1)   # be sure everything is stopped before pausing
        with self.lock:
            self.lock_ui(True)
            curses.def_prog_mode()
            curses.endwin()


    def resume_curses(self):
        """Resume curses and enable drawing, capturing terminal"""
        with self.lock:
            curses.reset_prog_mode()
            self.screen.refresh()
            self.lock_ui(False)


    def lock_ui(self, lock):
        """Turn ON/OFF main TUI drawing"""
        self.disable_drawing = lock
        if lock:
            self.hibernate_cursor = 10
        else:
            self.resize(redraw_only=True)


    def is_window_open(self):
        """Return True if window is openm, used only for non-terminal UI mode"""
        if uses_pgcurses:
            return curses.open_window
        return True


    def get_dimensions(self):
        """Return current dimensions for screen objects"""
        return (
            tuple(self.win_chat.getmaxyx()),
            tuple(self.win_tree.getmaxyx()),
            tuple(self.win_status_line.getmaxyx()),
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


    def get_extra_line_clicked(self):
        """Get clicked x coordinate of extra line"""
        return self.mouse_rel_x


    def get_chat_scrolled_top(self):
        """Check wether chat scrolling hit the top end"""
        return self.chat_scrolled_top


    def reset_chat_scrolled_top(self):
        """Force reset state of chat scrolling hit the top end"""
        self.chat_scrolled_top = False


    def get_assist(self):
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
                    if assist_type == 3 and self.assist_start != 1 and self.input_buffer[self.assist_start-2] not in (" ", "\n"):
                        # skip :emoji trigger if no space before it
                        return None, None
                    assist_word = self.input_buffer[self.assist_start : self.input_index]
                    return assist_word, assist_type
                self.assist_start = -1
                return None, 100
            if self.assist_start > self.input_index:
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


    def set_selected(self, selected, change_amount=0, scroll=True, draw=True):
        """Set selected line and text scrolling"""
        if self.chat_selected >= selected:
            up = True
        else:
            up = False
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
        self.chat_selected = -1
        self.chat_index = 0
        if not self.disable_drawing:
            self.draw_chat()


    def store_input_selected(self):
        """Get selected text from imput line"""
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
        self.dont_hide_chat_selection = not(allow)


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
        self.draw_tree()


    def toggle_category(self, tree_pos, only_open=False):
        """Toggle category drop-down state in tree"""
        if tree_pos >= 0:
            if (self.tree_format[tree_pos] % 10):
                if not only_open:
                    self.tree_format[tree_pos] -= 1
            else:
                self.tree_format[tree_pos] += 1
            self.draw_tree()
            self.tree_format_changed = True


    def draw_border(self, hwyx, top=True, bot=True, left=True, right=True):
        """Draw border around area on the screen with custom corners"""
        h, w, y, x = hwyx
        h += 2
        w += 2
        y -= 1
        x -= 1

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
            try:
                self.screen.addstr(y + h - 1, x + w - 1, self.corner_dr, curses.color_pair(self.default_color))
            except curses.error:
                pass   # it errors when drawing in bottom-right cell, but still draws it


    def draw_status_line(self):
        """Draw status line"""
        with self.lock:
            h, w = self.status_hw
            status_txt_l = self.status_txt_l[:w-1]   # limit status text size
            # if there is enough space for right text, add spaces and right text
            if self.status_txt_r:
                if self.bordered:
                    if self.win_extra_line or self.win_extra_window:
                        status_txt_r = self.status_txt_r[: max(w - (len(status_txt_l) + 4), 0)] + "─" + "┤"
                        status_txt_r = replace_spaces_dash(trim_with_dash(status_txt_r))
                        status_txt_l = replace_spaces_dash(trim_with_dash(status_txt_l))
                        status_txt_l = "├" + status_txt_l + "─" * (w - len(status_txt_l) - len(status_txt_r) - 1)
                    else:
                        status_txt_r = self.status_txt_r[: max(w - (len(status_txt_l) + 4), 0)] + "─" + self.corner_ur
                        status_txt_r = replace_spaces_dash(trim_with_dash(status_txt_r))
                        status_txt_l = replace_spaces_dash(trim_with_dash(status_txt_l))
                        status_txt_l = self.corner_ul + status_txt_l + "─" * (w - len(status_txt_l) - len(status_txt_r) - 1)
                    new_format_l = []
                    for item in self.status_txt_l_format:
                        new_format_l.append((item[0], item[1] + 1, min(item[2] + 1, w-1)))
                    self.status_txt_l_format = new_format_l
                else:
                    status_txt_r = self.status_txt_r[: max(w - (len(status_txt_l) + 4), 0)] + " "
                    status_txt_l = status_txt_l + " " * (w - len(status_txt_l) - len(status_txt_r))
                status_line = status_txt_l + status_txt_r
                text_l_len = len(status_txt_l)
                status_format = self.status_txt_l_format
                for tab in self.status_txt_r_format:
                    status_format.append((tab[0], tab[1] + text_l_len, min(tab[2] + text_l_len, w-1)))
            elif self.bordered:
                status_txt_l = replace_spaces_dash(trim_with_dash(status_txt_l))
                if self.win_extra_line or self.win_extra_window:
                    status_line = "├" + status_txt_l + "─" * (w - len(status_txt_l) - 2) + "┤"
                else:
                    status_line = self.corner_ul + status_txt_l + "─" * (w - len(status_txt_l) - 2) + self.corner_ur
                status_format = []
                for item in self.status_txt_l_format:
                    status_format.append((item[0], item[1] + 1, min(item[2] + 1, w-1)))
            else:
                # add spaces to end of line
                status_line = status_txt_l + " " * (w - len(status_txt_l))
                status_format = self.status_txt_l_format

            if status_format:
                self.draw_formatted_line(self.win_status_line, status_line, status_format, self.default_color if self.bordered else 17)
            elif self.bordered:
                self.win_status_line.insstr(0, 0, status_line + "\n", curses.color_pair(self.default_color))
            else:
                self.win_status_line.insstr(0, 0, status_line + "\n", curses.color_pair(17) | self.attrib_map[17])
            self.win_status_line.noutrefresh()
            self.need_update.set()


    def draw_title_line(self):
        """Draw title line, works same as status line"""
        with self.lock:
            h, w = self.title_hw
            title_txt_l = self.title_txt_l[:w-1]
            if self.title_txt_r:
                if self.bordered:
                    title_txt_r = self.title_txt_r[: max(w - (len(title_txt_l) + 4), 0)] + "─" + self.corner_ur
                    title_txt_r = replace_spaces_dash(trim_with_dash(title_txt_r))
                    title_txt_l = replace_spaces_dash(trim_with_dash(title_txt_l))
                    title_txt_l = self.corner_ul + title_txt_l + "─" * (w - len(title_txt_l) - len(title_txt_r) - 1)
                    new_format_l = []
                    for item in self.title_txt_l_format:
                        new_format_l.append((item[0], item[1] + 1, min(item[2] + 1, w-1)))
                    self.title_txt_l_format = new_format_l
                else:
                    title_txt_r = self.title_txt_r[: max(w - (len(title_txt_l) + 4), 0)] + " "
                    title_txt_l = title_txt_l + " " * (w - len(title_txt_l) - len(title_txt_r))
                title_line = title_txt_l + title_txt_r
                text_l_len = len(title_txt_l)
                title_format = self.title_txt_l_format
                for tab in self.title_txt_r_format:
                    title_format.append((tab[0], tab[1] + text_l_len, min(tab[2] + text_l_len, w-1)))
            elif self.bordered:
                title_txt_l = replace_spaces_dash(trim_with_dash(title_txt_l))
                title_line = self.corner_ul + title_txt_l + "─" * (w - len(title_txt_l) - 2) + self.corner_ur
                title_format = []
                for item in self.title_txt_l_format:
                    title_format.append((item[0], item[1] + 1, min(item[2] + 1, w-1)))
            else:
                title_line = title_txt_l + " " * (w - len(title_txt_l))
                title_format = self.title_txt_l_format

            if title_format:
                self.draw_formatted_line(self.win_title_line, title_line, title_format, self.default_color if self.bordered else 12)
            elif self.bordered:
                self.win_title_line.insstr(0, 0, title_line + "\n", curses.color_pair(self.default_color))
            else:
                self.win_title_line.insstr(0, 0, title_line + "\n", curses.color_pair(12) | self.attrib_map[12])
            self.win_title_line.noutrefresh()
            self.need_update.set()


    def draw_formatted_line(self, window, text, text_format, default_color):
        """Draw single formatted line on a (line) window, line is expected to have spaces to be filled to screen edge"""
        with self.lock:
            pos = 0
            try:
                for pos, character in enumerate(text):
                    for format_part in text_format:
                        if format_part[1] <= pos < format_part[2]:
                            if format_part[0] == 1:
                                attrib = curses.A_BOLD
                            elif format_part[0] == 2:
                                attrib = curses.A_ITALIC
                            elif format_part[0] == 3:
                                attrib = curses.A_UNDERLINE
                            elif default_color == self.default_color:
                                attrib = 0
                            else:
                                attrib = self.attrib_map[default_color]
                            safe_insch(window, 0, pos, character, curses.color_pair(default_color) | attrib)
                            break
                    else:
                        safe_insch(window, 0, pos, character, curses.color_pair(default_color) | self.attrib_map[14])
            except curses.error:
                # exception will happen when window is resized to smaller w dimensions
                if not self.disable_drawing:
                    self.resize()


    def draw_title_tree(self):
        """Draw tree title line, works same as status line, but without right text"""
        with self.lock:
            h, w = self.tree_title_hw
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


    def draw_chat(self, norefresh=False):
        """Draw chat with applied color formatting"""
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
                )
                self.win_chat.noutrefresh()
                if not norefresh:
                    self.need_update.set()
            except curses.error:
                # exception will happen when window is resized to smaller w dimensions
                if not self.disable_drawing:
                    self.resize()


    def set_wide_map(self, wide_map):
        """Update wide characters map"""
        self.wide_map = wide_map


    def clear_chat_wide(self):
        """Clear specific chat lines that are containing emoji"""
        h, w = self.win_chat.getmaxyx()
        for y in self.wide_map:
            chat_y = y - self.chat_index
            if chat_y <= 0:
                continue
            if chat_y > h:
                break
            self.win_chat.insstr(h - chat_y, 0, " " * w, curses.color_pair(1))
        self.win_chat.noutrefresh()
        self.need_update.set()
        time.sleep(self.screen_update_delay/2)


    def draw_tree(self):
        """Draw channel tree"""
        with self.lock:
            try:
                h, w = self.tree_hw
                # drawing from top to down
                skipped = 0   # skipping drop-down ends (code 1XXX)
                drop_down_skip_folder = False
                drop_down_skip_guild = False
                drop_down_skip_category = False
                drop_down_skip_channel = False
                drop_down_level = 0
                self.tree_clean_len = 0
                y = 0
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
                    y = max(num - skipped - self.tree_index, 0)
                    if y >= h:
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
                        color = curses.color_pair(7) | self.attrib_map[7]
                    elif second_digit == 4:   # active
                        color = curses.color_pair(6) | self.attrib_map[6]
                        color_line = curses.color_pair(6)
                    elif second_digit == 5:   # active mentioned
                        color = curses.color_pair(9) | self.attrib_map[9]
                        color_line = curses.color_pair(6)
                    if y == self.tree_selected - self.tree_index:   # selected
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
                    self.win_tree.insstr(y, 0, "\n", curses.color_pair(1))
                    y += 1
                self.win_tree.noutrefresh()
                self.need_update.set()
            except curses.error:
                # this exception will happen when window is resized to smaller h dimensions
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


    def draw_extra_line(self, text=None, toggle=False):
        """
        Draw extra line above status line and resize chat.
        If toggle and same text is repeated then remove extra line.
        """
        with self.lock:
            if toggle and text == self.extra_line_text:
                self.remove_extra_line()
                return
            self.extra_line_text = text
            if text and not self.disable_drawing:
                h, w = self.screen.getmaxyx()
                if not self.win_extra_line:
                    extra_line_hwyx = (1, w - (self.tree_width + self.bordered + 1), h - self.bordered - 3, self.tree_width + self.bordered + 1)
                    self.win_extra_line = self.screen.derwin(*extra_line_hwyx)
                    del self.win_chat
                    self.init_chat()
                    self.draw_chat(norefresh=True)
                    if self.member_list and self.bordered:   # have to redraw member list borders
                        h, w = self.screen.getmaxyx()
                        member_list_hwyx = (
                            h - (2 + bool(self.win_extra_line)) - self.have_title - 2*self.bordered,
                            self.member_list_width - self.bordered,
                             self.bordered or self.have_title,
                            w - self.member_list_width,
                        )
                        self.draw_border(member_list_hwyx, top=not(self.have_title))
                    self.draw_member_list(self.member_list, self.member_list_format, force=True)
                    if self.bordered:
                        self.draw_status_line()
                w = w - (self.tree_width + self.bordered + 1)
                if self.bordered:
                    text = "─" + trim_with_dash(text, dash=False)
                    line_text = self.corner_ul + text + "─" * (w - len(text) - 2) + self.corner_ur
                    self.win_extra_line.insstr(0, 0, line_text, curses.color_pair(self.default_color))
                else:
                    self.win_extra_line.insstr(0, 0, text + " " * (w - len(text)) + "\n", curses.color_pair(11) | self.attrib_map[11])
                self.win_extra_line.noutrefresh()
                self.need_update.set()
            self.draw_chat()


    def remove_extra_line(self):
        """Disable drawing of extra line above status line, and resize chat"""
        if self.win_extra_line:
            with self.lock:
                del self.win_chat
                self.extra_line_text = ""
                self.win_extra_line = None
                self.init_chat()
                self.chat_hw = self.win_chat.getmaxyx()
                self.draw_chat()
                if self.member_list and self.bordered:   # have to redraw member list borders
                    h, w = self.screen.getmaxyx()
                    member_list_hwyx = (
                        h - (2 + bool(self.win_extra_line)) - self.have_title - 2*self.bordered,
                        self.member_list_width - self.bordered,
                         self.bordered or self.have_title,
                        w - self.member_list_width,
                    )
                    self.draw_border(member_list_hwyx, top=not(self.have_title))
                if self.bordered:
                    self.draw_status_line()
                self.draw_member_list(self.member_list, self.member_list_format, force=True)
            self.draw_chat()


    def draw_extra_window(self, title_txt, body_text, select=False, start_zero=False):
        """
        Draw extra window above status line and resize chat.
        title_txt is string, body_text is list.
        """
        with self.lock:
            self.extra_select = select
            self.extra_window_title = title_txt
            self.extra_window_body = body_text
            if start_zero:
                self.extra_index = 0
                self.extra_selected = 0

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
                        self.draw_chat(norefresh=True)
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
                    y = max(num - self.extra_index, 0)
                    if y + 1 >= h:
                        break
                    if y >= 0:
                        try:
                            if num == self.extra_selected:
                                self.win_extra_window.insstr(y + 1, 0, line + " " * (w - len(line)) + "\n", curses.color_pair(11) | self.attrib_map[11])
                            else:
                                self.win_extra_window.insstr(y + 1, 0, line + " " * (w - len(line)) + "\n", curses.color_pair(21) | self.attrib_map[21])
                        except curses.error:   # some error with emojis
                            pass

                y += 2
                while y < h:
                    self.win_extra_window.insstr(y, 0, "\n", curses.color_pair(1))
                    y += 1
                self.draw_chat(norefresh=True)
                self.win_extra_window.noutrefresh()
                self.need_update.set()


    def remove_extra_window(self):
        """Disable drawing of extra window above status line, and resize chat"""
        if self.win_extra_window:
            with self.lock:
                del (self.win_extra_window, self.win_chat)
                self.extra_window_title = ""
                self.extra_window_body = ""
                self.win_extra_window = None
                self.extra_selected = -1
                self.init_chat()
                self.chat_hw = self.win_chat.getmaxyx()
                if not self.member_list:
                    self.draw_chat()
                elif self.bordered:   # have to redraw member list borders
                    h, w = self.screen.getmaxyx()
                    member_list_hwyx = (
                        h - (2 + bool(self.win_extra_line)) - self.have_title - 2*self.bordered,
                        self.member_list_width - self.bordered,
                         self.bordered or self.have_title,
                        w - self.member_list_width,
                    )
                    self.draw_border(member_list_hwyx, top=not(self.have_title))
                if self.bordered:
                    self.draw_status_line()
                self.draw_extra_line(self.extra_line_text)
                self.draw_member_list(self.member_list, self.member_list_format, force=True)
                self.draw_chat()


    def draw_member_list(self, member_list, member_list_format, force=False, reset=False, clean=True):
        """Draw member list and resize chat"""
        with self.lock:
            self.member_list = member_list
            self.member_list_format = member_list_format
            if member_list and not self.disable_drawing:
                h, w = self.screen.getmaxyx()
                if reset:
                    self.mlist_selected = -1
                    self.mlist_index = 0
                if not self.win_member_list or force:
                    if not force and self.win_member_list:
                        self.mlist_selected = -1
                        self.mlist_index = 0
                    if clean:
                        self.clear_chat_wide()
                    common_h = self.init_chat()
                    # chat will be regenerated and resized in app main loop

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
                        if self.have_title:
                            title_line_hwyx = (1, w - (self.tree_width + 2) - bool(self.win_member_list) * (self.member_list_width + 1), 0, self.tree_width + 2)
                            self.win_title_line = self.screen.derwin(*title_line_hwyx)
                            self.title_hw = self.win_title_line.getmaxyx()
                            self.draw_title_line()
                    else:
                        self.screen.vline(self.have_title, w - self.member_list_width-1, self.vert_line, common_h, curses.color_pair(self.default_color))

                # draw member list
                h, w = self.win_member_list.getmaxyx()
                w -= 1
                y = 0
                for num, line in enumerate(member_list):
                    y =  max(num - self.mlist_index, 0)
                    if y >= h:
                        break
                    line_format = member_list_format[num]
                    if num == self.mlist_selected:
                        self.win_member_list.insstr(y, 0, line, curses.color_pair(4) | self.attrib_map[4])
                    else:
                        for pos, character in enumerate(line):
                            if pos > w:
                                break
                            for format_part in line_format:
                                if format_part[1] <= pos < format_part[2]:
                                    color = format_part[0]
                                    if color > 255:   # set all colors after 255 to default color
                                        color = self.default_color
                                    color_ready = curses.color_pair(color) | self.attrib_map[color]
                                    safe_insch(self.win_member_list, y, pos, character, color_ready)
                                    break
                            else:
                                safe_insch(self.win_member_list, y, pos, character, curses.color_pair(self.default_color) | self.attrib_map[self.default_color])

                y += 1
                while y < h:
                    self.win_member_list.insstr(y, 0, "\n", curses.color_pair(1))
                    y += 1
                self.win_member_list.noutrefresh()
                self.need_update.set()

        if uses_pgcurses:   # quick fix but not ideal, find why is tree cleared on derwin
            self.draw_tree()
            self.draw_title_tree()


    def remove_member_list(self, pause=True):
        """Remove member list and resize chat"""
        if self.win_member_list:
            # safely clean emojis
            with self.lock:
                h, w = self.win_member_list.getmaxyx()
                for y in range(h):
                    self.win_member_list.insstr(y, 0, " " * w, curses.color_pair(1))
                self.win_member_list.noutrefresh()
                self.need_update.set()
            if pause:
                time.sleep(self.screen_update_delay/2)

            # remove member list and redraw chat
            with self.lock:
                self.clear_chat_wide()
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
        if self.enable_blink_cursor:
            self.set_cursor_color(15)
            self.cursor_on = True
            self.hibernate_cursor = 0


    def update_status_line(self, text_l, text_r=None, text_l_format=[], text_r_format=[]):
        """Update status text"""
        redraw = False
        if text_l != self.status_txt_l:
            self.status_txt_l = text_l
            redraw = True
        if text_r != self.status_txt_r:
            self.status_txt_r = text_r
            redraw = True
        if text_l_format != self.status_txt_l_format:
            self.status_txt_l_format = text_l_format
            redraw = True
        if text_r_format != self.status_txt_r_format:
            self.status_txt_r_format = text_r_format
            redraw = True
        if redraw and not self.disable_drawing:
            self.draw_status_line()


    def update_title_line(self, text_l, text_r=None, text_l_format=[], text_r_format=[]):
        """Update status text"""
        if self.have_title:
            redraw = False
            if text_l != self.title_txt_l:
                self.title_txt_l = text_l
                redraw = True
            if text_r != self.title_txt_r:
                self.title_txt_r = text_r
                redraw = True
            if text_l_format != self.title_txt_l_format:
                self.title_txt_l_format = text_l_format
                redraw = True
            if text_r_format != self.title_txt_r_format:
                self.title_txt_r_format = text_r_format
                redraw = True
            if redraw and not self.disable_drawing:
                self.draw_title_line()


    def update_title_tree(self, text):
        """Update status text"""
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
        if fg > curses.COLORS:
            fg = -1
        if bg > curses.COLORS:
            bg = -1
        if force_id >= curses.COLOR_PAIRS:
            return 0
        if self.last_free_id >= curses.COLOR_PAIRS:
            return 0
        if force_id > 0:
            curses.init_pair(force_id, fg, bg)
            # self.color_cache = set_list_item(self.color_cache, (fg, bg), force_id)   # role colors are already saved in app.roles
            self.attrib_map = set_list_item(self.attrib_map, attribute, force_id)
            return force_id
        curses.init_pair(self.last_free_id, fg, bg)
        self.color_cache.append((fg, bg))   # 255_curses_bug
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
        """Initialize multiple color pairs in double nested lists twice, one wih normal color and one bg from with alt_color"""
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
        for format_colors in colors:
            format_codes = []
            for num, color in enumerate(format_colors):
                if num == 0:
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


    def restore_colors(self):   # 255_curses_bug
        """Re-initialize cached colors"""
        for num, color in enumerate(self.color_cache):
            curses.init_pair(num + 1, *color)


    def spellcheck(self):
        """Spellcheck words visible on screen"""
        if not self.input_buffer or self.enable_autocomplete or self.bracket_paste:
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


    def common_keybindings(self, key, mouse=False, switch=False, command=False, forum=False):
        """Handle keybinding events that are common for all buffers"""
        if key == curses.KEY_UP:   # UP
            if command:
                return 46
            if self.chat_selected + 1 < len(self.chat_buffer):
                top_line = self.chat_index + self.chat_hw[0] - 3
                if top_line + 3 < len(self.chat_buffer) and self.chat_selected >= top_line:
                    self.chat_index += 1   # move history down
                self.chat_selected += 1   # move selection up
                self.draw_chat()

        elif key == curses.KEY_DOWN:   # DOWN
            if command:
                return 47
            if self.chat_selected >= self.dont_hide_chat_selection:   # if it is -1, selection is hidden
                if self.chat_index and self.chat_selected <= self.chat_index + 2:   # +2 from status and input lines
                    self.chat_index -= 1   # move history up
                self.chat_selected -= 1   # move selection down
                self.draw_chat()

        elif key in self.keybindings["tree_up"]:
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

        elif key in self.keybindings["tree_down"]:
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

        elif key in self.keybindings["tree_select"]:
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

        elif key in self.keybindings["extra_up"]:
            if self.extra_window_body and not mouse:
                if self.extra_select and self.extra_selected >= 0:
                    if self.extra_index and self.extra_selected <= self.extra_index:
                        self.extra_index -= 1
                    self.extra_selected -= 1
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
                elif self.extra_index > 0:
                    self.extra_index -= 1
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
                elif self.wrap_around and not self.wrap_around_disable:
                    self.extra_selected = len(self.extra_window_body) - 1
                    self.extra_index = max(len(self.extra_window_body) - (self.win_extra_window.getmaxyx()[0] - 1), 0)
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
            elif self.win_member_list:
                if self.mlist_selected >= 0:
                    if self.mlist_index and self.mlist_selected <= self.mlist_index:
                        self.mlist_index -= 1
                    self.mlist_selected -= 1
                    self.draw_member_list(self.member_list, self.member_list_format)
                elif self.mlist_index > 0:
                    self.mlist_index -= 1
                    self.draw_member_list(self.member_list, self.member_list_format)

        elif key in self.keybindings["extra_down"]:
            if self.extra_window_body and not mouse:
                if self.extra_select:
                    if self.extra_selected + 1 < len(self.extra_window_body):
                        top_line = self.extra_index + self.win_extra_window.getmaxyx()[0] - 1
                        if top_line < len(self.extra_window_body) and self.extra_selected >= top_line - 1:
                            self.extra_index += 1
                        self.extra_selected += 1
                        self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
                    elif self.wrap_around and not self.wrap_around_disable:
                        self.extra_selected = 0
                        self.extra_index = 0
                        self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
                elif self.extra_index + 1 < len(self.extra_window_body):
                    self.extra_index += 1
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
            elif self.win_member_list:
                if self.mlist_selected + 1 < len(self.member_list):
                    top_line = self.mlist_index + self.win_member_list.getmaxyx()[0] - 1
                    if top_line < len(self.member_list) and self.mlist_selected >= top_line - 1:
                        self.mlist_index += 1
                    self.mlist_selected += 1
                    self.draw_member_list(self.member_list, self.member_list_format)

        elif key in self.keybindings["quit"]:
            return 49

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


    def wait_input(self, prompt="", init_text=None, reset=True, keep_cursor=False, autocomplete=False, clear_delta=False, forum=False, command=False):
        """
        Take input from user, and show it on screen
        Return typed text, absolute_tree_position and whether channel is changed
        """
        _, w = self.input_hw
        self.enable_autocomplete = autocomplete
        if reset:
            self.input_buffer = ""
            self.input_index = 0
            self.input_line_index = 0
            self.cursor_pos = 0
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
        self.bracket_paste = False
        selected_completion = 0
        self.keybinding_chain = None
        key = -1
        while self.run:
            key = get_key(self.screen)

            if self.mouse and key == curses.KEY_MOUSE:
                code = self.mouse_events(key)
                if code:
                    return self.return_input_code(code)
                continue

            w = self.input_hw[1]
            if self.disable_drawing:
                if key == 27:   # ESCAPE
                    self.screen.nodelay(True)
                    key = get_key(self.screen)
                    if key in (-1, 27):
                        self.input_buffer = ""
                        self.screen.nodelay(False)
                        return None, 0, 0, 100
                    self.screen.nodelay(False)
                elif key in self.keybindings["media_pause"]:
                    return None, 0, 0, 101
                elif key in self.keybindings["media_replay"]:
                    return None, 0, 0, 102
                elif key in self.keybindings["media_seek_forward"]:
                    return None, 0, 0, 103
                elif key in self.keybindings["media_seek_backward"]:
                    return None, 0, 0, 104
                elif key == curses.KEY_RESIZE:
                    pass
                continue   # disable all inputs from main UI

            if key == 27:   # ESCAPE
                # terminal waits when Esc is pressed, but not when sending escape sequence
                self.screen.nodelay(True)
                key = get_key(self.screen)
                if key == -1:
                    # escape key
                    self.screen.nodelay(False)
                    if self.assist_start:
                        self.assist_start = -1
                    return self.return_input_code(5)
                # sequence (bracketed paste or ALT+KEY)
                sequence = [27, key]
                # -1 means no key is pressed, 126 is end of escape sequence
                while key != -1:
                    key = get_key(self.screen)
                    sequence.append(key)
                    if key == 126:
                        break
                    if key == 27:   # holding escape key
                        sequence.append(-1)
                        break
                self.screen.nodelay(False)
                # match sequences
                if len(sequence) == 3 and sequence[2] == -1:   # ALT+KEY
                    key = f"ALT+{sequence[1]}"
                elif sequence == [27, 91, 50, 48, 48, 126]:
                    self.bracket_paste = True
                    continue
                elif sequence == [27, 91, 50, 48, 49, 126]:
                    self.bracket_paste = False
                    continue
                elif sequence[-1] == -1 and sequence[-2] == 27:
                    # holding escape key
                    if self.assist_start:
                        self.assist_start = -1
                    return self.return_input_code(5)

            # handle chained keybindings
            if key in self.chainable and not self.keybinding_chain:
                self.keybinding_chain = key
                continue
            if self.keybinding_chain:
                key = f"{self.keybinding_chain}-{key}"
                self.keybinding_chain = None

            if key == 10:   # ENTER
                # when pasting, dont return, but insert newline character
                if self.bracket_paste:
                    self.input_buffer = self.input_buffer[:self.input_index] + "\n" + self.input_buffer[self.input_index:]
                    self.input_index += 1
                    self.add_to_delta_store("\n")
                    pass
                else:
                    if forum:
                        self.input_index = 0
                        self.input_line_index = 0
                        self.cursor_pos = 0
                        self.draw_input_line()
                    self.cursor_on = True
                    self.input_select_start = None
                    return self.return_input_code(0)

            if isinstance(key, str):
                if len(key) > 1 and key.startswith("ALT+"):
                    try:
                        modifier = key[:-3]   # skipping +/- sign
                        num = int(key[-2:])
                        if 49 <= num <= 57 and modifier == self.switch_tab_modifier:
                            self.pressed_num_key = num - 48
                            return self.return_input_code(42)
                    except ValueError:
                        pass
                else:   # unicode letters
                    if self.input_select_start is not None:
                        self.delete_selection()
                        self.input_select_start = None
                    self.input_buffer = self.input_buffer[:self.input_index] + key + self.input_buffer[self.input_index:]
                    self.input_index += 1
                    self.typing = int(time.time())
                    if self.enable_autocomplete:
                        selected_completion = 0
                    self.add_to_delta_store(key)
                    self.show_cursor()
                    if self.assist and not self.bracket_paste:
                        if key in ASSIST_TRIGGERS:
                            self.assist_start = self.input_index

            if isinstance(key, int) and 32 <= key <= 126:   # all regular characters
                if self.input_select_start is not None:
                    self.delete_selection()
                    self.input_select_start = None
                self.input_buffer = self.input_buffer[:self.input_index] + chr(key) + self.input_buffer[self.input_index:]
                if self.fun == 2:
                    with self.fun_lock:
                        self.red_list.append(self.input_index)
                self.input_index += 1
                self.typing = int(time.time())
                if self.enable_autocomplete:
                    selected_completion = 0
                self.add_to_delta_store(chr(key))
                self.show_cursor()
                if self.assist and not self.bracket_paste:
                    if chr(key) in ASSIST_TRIGGERS:
                        self.assist_start = self.input_index
                if not self.bracket_paste:
                    self.spellcheck()

            elif (code := self.common_keybindings(key, command=command, forum=forum)):
                return self.return_input_code(code)

            elif key == BACKSPACE:
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

            elif key == curses.KEY_DC:   # DEL
                if self.input_select_start is not None:
                    self.delete_selection()
                    self.input_select_start = None
                elif self.input_index < len(self.input_buffer):
                    removed_char = self.input_buffer[self.input_index]
                    self.input_buffer = self.input_buffer[:self.input_index] + self.input_buffer[self.input_index+1:]
                    self.add_to_delta_store("DELETE", removed_char)
                    self.show_cursor()
                self.spellcheck()

            elif key == curses.KEY_LEFT:
                if self.input_index > 0:
                    # if index hits left screen edge, but there is more text to left, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index) == 0:
                        self.input_line_index += min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index -= 1
                    self.show_cursor()
                self.input_select_start = None
                self.spellcheck()

            elif key == curses.KEY_RIGHT:
                if self.input_index < len(self.input_buffer):
                    # if index hits right screen edge, but there is more text to right, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w - self.input_line_index) == w:
                        self.input_line_index -= min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index += 1
                    self.show_cursor()
                self.input_select_start = None
                self.spellcheck()

            elif key == curses.KEY_HOME:
                self.input_index = 0
                self.input_line_index = 0
                self.input_select_start = None
                self.spellcheck()

            elif key == curses.KEY_END:
                self.input_index = len(self.input_buffer)
                self.input_select_start = None
                self.spellcheck()

            elif key in self.keybindings["word_left"]:
                left_len = 0
                for word in self.input_buffer[:self.input_index].split(" ")[::-1]:
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

            elif key in self.keybindings["word_right"]:
                left_len = 0
                for word in self.input_buffer[self.input_index:].split(" "):
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

            elif key in self.keybindings["select_word_left"]:
                if self.input_select_start is None:
                    self.input_select_end = self.input_select_start = self.input_index
                left_len = 0
                for word in self.input_buffer[:self.input_index].split(" ")[::-1]:
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

            elif key in self.keybindings["select_word_right"]:
                if self.input_select_start is None:
                    self.input_select_end = self.input_select_start = self.input_index
                left_len = 0
                for word in self.input_buffer[self.input_index:].split(" "):
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

            elif key in self.keybindings["undo"]:
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

            elif key in self.keybindings["redo"]:
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

            elif key in self.keybindings["select_left"]:
                if self.input_select_start is None:
                    self.input_select_start = self.input_index
                if self.input_index > 0:
                    # if index hits left screen edge, but there is more text to left, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index) == 0:
                        self.input_line_index += min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index -= 1
                self.input_select_end = self.input_index

            elif key in self.keybindings["select_right"]:
                if self.input_select_start is None:
                    self.input_select_start = self.input_index
                if self.input_index < len(self.input_buffer):
                    # if index hits right screen edge, but there is more text to right, move line right
                    if self.input_index - max(0, len(self.input_buffer) - w - self.input_line_index) == w:
                        self.input_line_index -= min(INPUT_LINE_JUMP, w - 3)
                    else:
                        self.input_index += 1
                self.input_select_end = self.input_index

            elif key in self.keybindings["select_all"]:
                self.input_select_start = 0
                self.input_select_end = len(self.input_buffer)

            elif self.input_select_start is not None and key in self.keybindings["copy_sel"]:
                self.store_input_selected()
                return self.return_input_code(20)

            elif self.input_select_start is not None and key in self.keybindings["cut_sel"]:
                self.delete_selection()
                self.input_select_start = None
                self.cursor_pos = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
                self.cursor_pos = max(self.cursor_pos, 0)
                self.cursor_pos = min(w - 1, self.cursor_pos)
                if not self.disable_drawing:
                    self.draw_input_line()
                return self.return_input_code(20)

            elif key == 9:   # TAB - same as CTRL+I
                if self.enable_autocomplete:
                    if self.input_buffer and self.input_index == len(self.input_buffer):
                        completions = peripherals.complete_path(self.input_buffer, separator=False)
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

            elif key in self.keybindings["tree_collapse_threads"]:
                if (self.tree_format[self.tree_selected_abs] % 10):
                    self.tree_format[self.tree_selected_abs] -= 1
                else:
                    self.tree_format[self.tree_selected_abs] += 1
                self.draw_tree()
                self.tree_format_changed = True

            elif key in self.keybindings["attach_prev"]:
                return self.return_input_code(14)

            elif key in self.keybindings["attach_next"]:
                return self.return_input_code(15)

            elif key in self.keybindings["insert_newline"]:
                self.input_buffer = self.input_buffer[:self.input_index] + "\n" + self.input_buffer[self.input_index:]
                self.input_index += 1
                self.show_cursor()
                self.spellcheck()

            elif key in self.keybindings["reply"] and self.chat_selected != -1 and not forum:
                return self.return_input_code(1)

            elif key in self.keybindings["edit"] and self.chat_selected != -1 and not forum:
                return self.return_input_code(2)

            elif key in self.keybindings["delete"] and self.chat_selected != -1 and not forum:
                return self.return_input_code(3)

            elif key in self.keybindings["toggle_ping"] and not forum:
                return self.return_input_code(6)

            elif key in self.keybindings["scroll_bottom"]:
                return self.return_input_code(7)

            elif key in self.keybindings["go_replied"] and self.chat_selected != -1 and not forum:
                return self.return_input_code(8)

            elif key in self.keybindings["download"] and self.chat_selected != -1:
                return self.return_input_code(9)

            elif key in self.keybindings["browser"] and self.chat_selected != -1:
                return self.return_input_code(10)

            elif key in self.keybindings["cancel"]:
                return self.return_input_code(11)

            elif key in self.keybindings["copy_msg"] and not forum:
                return self.return_input_code(12)

            elif key in self.keybindings["upload"] and not forum:
                self.enable_autocomplete = True
                self.misspelled = []
                return self.return_input_code(13)

            elif key in self.keybindings["attach_cancel"]:
                return self.return_input_code(16)

            elif key in self.keybindings["view_media"] and self.chat_selected != -1:
                return self.return_input_code(17)

            elif key in self.keybindings["spoil"] and self.chat_selected != -1 and not forum:
                return self.return_input_code(18)

            elif key in self.keybindings["tree_join_thread"]:
                return self.return_input_code(21)

            elif key in self.keybindings["preview_upload"]:
                return self.return_input_code(22)

            elif key in self.keybindings["profile_info"] and self.chat_selected != -1 and not forum:
                self.extra_index = 0
                self.extra_selected = -1
                return self.return_input_code(24)

            elif key in self.keybindings["channel_info"] and self.tree_selected > 0:
                self.extra_index = 0
                self.extra_selected = -1
                return self.return_input_code(25)

            elif key in self.keybindings["extra_select"]:
                return self.return_input_code(27)

            elif key in self.keybindings["show_summaries"]:
                self.extra_index = 0
                self.extra_selected = -1
                return self.return_input_code(28)

            elif key in self.keybindings["search"] and not forum:
                self.extra_index = 0
                self.extra_selected = -1
                return self.return_input_code(29)

            elif key in self.keybindings["copy_channel_link"] and self.tree_selected > 0 and not forum:
                return self.return_input_code(30)

            elif key in self.keybindings["copy_message_link"] and self.chat_selected != -1 and not forum:
                return self.return_input_code(31)

            elif key in self.keybindings["go_channel"] and self.chat_selected != -1 and not forum:
                return self.return_input_code(32)

            elif key in self.keybindings["cycle_status"]:
                return self.return_input_code(33)

            elif key in self.keybindings["record_audio"] and not forum:
                return self.return_input_code(34)

            elif key in self.keybindings["toggle_member_list"]:
                return self.return_input_code(35)

            elif key in self.keybindings["add_reaction"] and not forum:
                return self.return_input_code(36)

            elif key in self.keybindings["command_palette"]:
                return self.return_input_code(38)

            elif key in self.keybindings["show_reactions"] and not forum:
                return self.return_input_code(37)

            elif key in self.keybindings["toggle_tab"]:
                return self.return_input_code(41)

            elif key in self.keybindings["show_pinned"] and not forum:
                return self.return_input_code(43)

            elif key in self.keybindings["search_gif"] and not forum:
                return self.return_input_code(44)

            elif key in self.keybindings["open_external_editor"] and not forum:
                return self.return_input_code(45)

            elif key == curses.KEY_RESIZE:
                self.resize()
                _, w = self.input_hw

            # terminal reserved keys: CTRL+ C, I, J, M, Q, S, Z

            elif key in self.command_bindings:
                return self.return_input_code((50, self.command_bindings.get(key)))

            # keep index inside screen
            self.cursor_pos = self.input_index - max(0, len(self.input_buffer) - w + 1 - self.input_line_index)
            self.cursor_pos = max(self.cursor_pos, 0)
            self.cursor_pos = min(w - 1, self.cursor_pos)
            if not self.disable_drawing:
                self.draw_input_line()
        return None, None, None, None


    def mouse_events(self, key):
        """Handle mouse events on terminal screen"""
        if key == curses.KEY_MOUSE:
            try:
                _, x, y, _, bstate = curses.getmouse()
            except curses.error:
                return None
            if bstate & curses.BUTTON1_PRESSED:
                new_click = (time.time(), x, y)
                if new_click[0] - self.first_click[0] < 0.5 and new_click[1:] == self.first_click[1:]:
                    self.first_click = (0, 0, 0)
                    return self.mouse_double_click(x, y)
                self.first_click = new_click
                return self.mouse_single_click(x, y)
            if bstate & BUTTON4_PRESSED:
                self.mouse_scroll(x, y, True)
            elif bstate & BUTTON5_PRESSED:
                self.mouse_scroll(x, y, False)
            return None


    def mouse_in_window(self, x, y, win):
        """Check if mouse is inside specified window"""
        win_y, win_x = win.getbegyx()
        win_h, win_w = win.getmaxyx()
        return (win_x <= x < win_x + win_w) and (win_y <= y < win_y + win_h)


    def mouse_rel_pos(self, x, y, win):
        """Get mouse position relative to specified window"""
        win_y, win_x = win.getbegyx()
        return (x - win_x, y - win_y)

    def mouse_single_click(self, x, y):
        """Handle mouse single click events"""
        if self.mouse_in_window(x, y, self.win_tree):
            x, y = self.mouse_rel_pos(x, y, self.win_tree)
            self.tree_selected = self.tree_index + y
            self.draw_tree()
            return self.common_keybindings(self.keybindings["tree_select"][0], mouse=True)

        if self.mouse_in_window(x, y, self.win_chat):
            x, y = self.mouse_rel_pos(x, y, self.win_chat)
            self.chat_selected = self.chat_index + self.win_chat.getmaxyx()[0] - y - 1
            self.draw_chat()

        elif self.win_member_list and self.mouse_in_window(x, y, self.win_member_list):
            x, y = self.mouse_rel_pos(x, y, self.win_member_list)
            self.mlist_selected = self.mlist_index + y
            self.draw_member_list(self.member_list, self.member_list_format)

        elif self.mouse_in_window(x, y, self.win_input_line):
            x, y = self.mouse_rel_pos(x, y, self.win_input_line)
            input_index = min(x + max(0, len(self.input_buffer) - self.input_hw[1] + 1 - self.input_line_index), len(self.input_buffer))
            self.set_input_index(input_index)
            self.draw_input_line()

        elif self.win_extra_window and self.mouse_in_window(x, y, self.win_extra_window):
            x, y = self.mouse_rel_pos(x, y, self.win_extra_window)
            self.extra_selected = self.extra_index + y - 1
            self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)

        elif self.win_extra_line and self.mouse_in_window(x, y, self.win_extra_line):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_extra_line)[0]
            return 48   # special handling


    def mouse_double_click(self, x, y):
        """Handle mouse double click events"""
        if self.mouse_in_window(x, y, self.win_tree):
            return self.common_keybindings(self.keybindings["tree_select"][0], switch=True)

        if self.mouse_in_window(x, y, self.win_chat):
            self.mouse_rel_x = self.mouse_rel_pos(x, y, self.win_chat)[0]
            return 40   # special handling

        if self.win_extra_window and self.mouse_in_window(x, y, self.win_extra_window):
            return 27   # select in extra window

        if self.win_member_list and self.mouse_in_window(x, y, self.win_member_list):
            return 39   # select in member list

        if self.mouse_in_window(x, y, self.win_input_line):
            start, end = select_word(self.input_buffer, self.input_index)
            if not end:
                return
            self.input_select_start = start
            self.input_select_end = end + 1
            self.draw_input_line()
            self.set_input_index(self.input_select_end)
            self.input_select_start = start


    def mouse_scroll(self, x, y, up):
        """Handle mouse scroll events, by scrolling selection"""
        if self.mouse_in_window(x, y, self.win_tree):
            if up:
                self.common_keybindings(self.keybindings["tree_up"][0])
            else:
                self.common_keybindings(self.keybindings["tree_down"][0])

        elif self.mouse_in_window(x, y, self.win_chat):
            if up:
                self.common_keybindings(curses.KEY_UP)
            else:
                self.common_keybindings(curses.KEY_DOWN)

        elif self.win_extra_window and self.mouse_in_window(x, y, self.win_extra_window):
            if up:
                self.common_keybindings(self.keybindings["extra_up"][0])
            else:
                self.common_keybindings(self.keybindings["extra_down"][0])

        elif self.win_member_list and self.mouse_in_window(x, y, self.win_member_list):
            if up:
                self.common_keybindings(self.keybindings["extra_up"][0], mouse=True)
            else:
                self.common_keybindings(self.keybindings["extra_down"][0], mouse=True)


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
                    self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)
            elif self.extra_index + self.win_extra_window.getmaxyx()[0] - 1 < len(self.extra_window_body):
                self.extra_index += self.mouse_scroll_sensitivity
                self.draw_extra_window(self.extra_window_title, self.extra_window_body, self.extra_select)

        elif self.win_member_list and self.mouse_in_window(x, y, self.win_member_list):
            if up:
                if self.mlist_index:
                    self.mlist_index -= min(self.mouse_scroll_sensitivity, self.mlist_index)
                    self.draw_member_list(self.member_list, self.member_list_format)
            elif self.mlist_index + self.win_member_list.getmaxyx()[0] - 1 < len(self.member_list):
                self.mlist_index += self.mouse_scroll_sensitivity
                self.draw_member_list(self.member_list, self.member_list_format)
