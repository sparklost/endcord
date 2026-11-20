# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import curses
import os
import sys
import webbrowser

from endcord import downloader, keybinding, peripherals

uses_gtkcurses = hasattr(curses, "GTKCURSES")

TOC_WIDTH = 20
MAX_TEXT_WIDTH = 100
REPO = "https://raw.githubusercontent.com/sparklost/endcord/main/"
WIKI = "https://raw.githubusercontent.com/sparklost/endcord/main/docs/"
PAGES = [
    ("README", REPO + "README.md"),
    ("Wiki", WIKI + "wiki.md"),
    (" - Basic Usage", WIKI + "usage.md"),
    (" - Keybindings", WIKI + "keybindings.md"),
    (" - Commands", WIKI + "commands.md"),
    (" - Configuration", WIKI + "configuration.md"),
    (" - FAQ", WIKI + "faq.md"),
    (" - Extensions", WIKI + "extensions.md"),
    (" - Dev notes", "https://raw.githubusercontent.com/sparklost/endcord/main/.github/CONTRIBUTING.md"),
]
DOWNLOAD_DIR = os.path.join(os.path.expanduser(peripherals.config_path), "docs")
DOC_DIRS = ("/usr/share/doc/endcord", "./documentation", DOWNLOAD_DIR)
TEXT = {}
APP_NAME = "endcord"
HEADER = {
    "User-Agent": APP_NAME + "/" + peripherals.VERSION,   # required by github
    "Accept": "application/vnd.github+json",
}
ALT_SPACE = " " if uses_gtkcurses else " "   # U+2000 - en quad


# markdown parsing

C_BOLD, C_CODE, C_H1, C_H2, C_H3, C_QUOTE, C_DIM, C_BLOCK, C_LINK = 1, 2, 3, 4, 5, 6, 7, 8, 9
BLOCK_COLOR = {"h1": C_H1, "h2": C_H2, "h3": C_H3, "q": C_QUOTE, "code": C_BLOCK}
STYLES = {
    C_BOLD: (0, curses.A_BOLD),   # color id, attribute
    C_CODE: (2, 0),
    C_H1: (3, curses.A_BOLD),
    C_H2: (3, curses.A_BOLD),
    C_H3: (4, 0),
    C_DIM: (5, 0),
    C_QUOTE: (5, 0),
    C_BLOCK: (2, 0),
}


def parse_inline(source, links):
    """Parse **bold**, `code` and []() in one line"""
    text = ""
    spans = []
    bold = -1
    i = 0
    while i < len(source):
        j = source.find("`", i + 1) if source[i] == "`" else -1
        k = source.find("](", i) if source[i] == "[" else -1
        end = source.find(")", k) if k > 0 else -1
        if source.startswith("**", i):
            if bold < 0:
                bold = len(text)
            else:
                spans.append([C_BOLD, bold, len(text)])
                bold = -1
            i += 2
        elif j > 0:
            spans.append([C_CODE, len(text), len(text) + j - i - 1])
            text += source[i + 1:j]
            i = j + 1
        elif end > 0 and source.find("]", i) == k:
            n, start = len(links), len(text)
            links.append(source[k + 2:end].strip())
            label, sub, links = parse_inline(source[i + 1:k], links)
            for c, a, b in sub:
                spans.append([c, a + start, b + start])
            spans.append([C_LINK + n, start, start + len(label)])
            text += label
            i = end + 1
        else:
            text += source[i]
            i += 1
    return text, spans, links


def wrap(lines, formats, text, spans, width, first, rest, block):
    """Wrap one block into lines with prefixes, and handle code blocks"""

    # wrap
    wrapped = []
    wrap_width = width - len(first)
    offset = 0
    for part in text.split("\n"):
        a, end = offset, offset + len(part)
        if a == end:
            wrapped.append((a, a))
        while a < end:
            b = min(a + wrap_width, end)
            if b < end:
                k = text.rfind(" ", a, b + 1)
                if k > a:
                    b = k
            wrapped.append((a, b))
            a = b
            while a < end and text[a] == " ":
                a += 1
        offset = end + 1

    # handle code blocks
    for num, (a, b) in enumerate(wrapped):
        pre = first if num == 0 else rest
        off = len(pre) - a
        line_format = []
        for color, start, end in spans:
            if start < b and end > a:
                line_format.append([color, max(start, a) + off, min(end, b) + off])
        if pre.strip():
            line_format.append([C_DIM, 0, len(pre)])
        line = pre + text[a:b]
        if block == C_BLOCK:
            line += " " * (width - len(line))
            line_format.append([block, 0, width])
        elif block and b > a:
            line_format.append([block, len(pre), len(pre) + b - a])
        lines.append(line)
        formats.append(line_format)


def parse(text, width):
    """
    Parse markdown text into lines, formats and urls.
    lines = [one_message_line, ...]
    lines_format = [[[color_id, start, end], ...], ...]
    Supports **bold**, `code`, [](), # ## ###, >, - and 1. lists, ``` and ---.
    """
    lines = []
    lines_format = []
    links = []

    # strip html from md
    i = 0
    depth = 0
    raw_lines = text.replace("\r", "").split("\n")
    while i < len(raw_lines):
        line = raw_lines[i].strip()
        if line and not line.startswith("<") and depth <= 0:
            break
        for tag in ("<div", "<p>", "<p ", "<details", "<center", "<table", "<!--"):
            depth += line.count(tag)
        for tag in ("</div", "</p>", "</details", "</center", "</table", "-->"):
            depth -= line.count(tag)
        i += 1
    clean_text = raw_lines[i:]

    items = []
    code = False
    for raw in clean_text:
        raw_strip = raw.strip()
        body = raw.lstrip()
        ind = " " * (len(raw) - len(body))
        dot = body.find(". ")
        if raw_strip.startswith("```"):
            code = not code
        elif code:
            items.append(["code", "  ", "  ", raw])
        elif raw_strip.startswith("<") and raw_strip.endswith(">") and "://" not in raw_strip:
            continue
        elif not raw_strip:
            items.append(None)
        elif raw_strip in ("---", "***", "___"):
            items.append(["hr", "", "", ""])
        elif raw_strip.startswith("#") and raw_strip.lstrip("#")[:1] == " ":
            n = len(raw_strip) - len(raw_strip.lstrip("#"))
            items.append([f"h{min(n, 3)}", "» " if n > 2 else "", "  " if n > 2 else "", raw_strip[n:].strip()])
        elif raw_strip.startswith(">"):
            items.append(["q", "| ", "| ", raw_strip[1:].strip()])
        elif body[:2] in ("- ", "* ", "+ "):
            items.append(["li", ind + "• ", ind + "  ", body[2:].strip() + ("\n" if raw.endswith("  ") else " ")])
        elif 0 < dot <= 3 and body[:dot].isdigit():
            items.append(["li", ind + body[:dot + 2], ind + " " * (dot + 2), body[dot + 2:].strip() + ("\n" if raw.endswith("  ") else " ")])
        elif items and items[-1] and items[-1][0] in ("p", "li"):
            items[-1][3] += raw_strip + ("\n" if raw.endswith("  ") else " ")
        else:
            items.append(["p", "", "", raw_strip + ("\n" if raw.endswith("  ") else " ")])

    prev = ""
    for item in items:
        if not item:
            continue
        kind, first, rest, source = item
        if kind in ("p", "li"):
            source = source.rstrip(" \n")
        if lines and not (kind == prev and kind in ("li", "q", "code")):
            lines.append("")
            lines_format.append([])
            if kind in ("h1", "h2"):   # extra newline for big headings
                lines.append("")
                lines_format.append([])
        prev = kind
        if kind == "hr":
            lines.append("-" * width)
            lines_format.append([[C_DIM, 0, width]])
            continue
        spans = []
        if kind != "code":
            source, spans, links = parse_inline(source, links)
        wrap(lines, lines_format, source, spans, width, first, rest, BLOCK_COLOR.get(kind, 0))
    return lines, lines_format, links


# links

def resolve(base, href):
    """Turn link into a full url relative to the base"""
    root = WIKI if base.startswith(WIKI) else REPO
    parts = root.rstrip("/").split("/") if href.startswith("/") else base.split("/")[:-1]
    for p in href.split("#")[0].split("/"):
        if p == "..":
            parts.pop()
        elif p and p != ".":
            parts.append(p)
    return "/".join(parts)


def heading_anchors(lines, formats):
    """Find all headings, returns [(index of the first line, anchor), ...]"""
    found = []
    title = None
    start = 0
    for i in range(len(lines) + 1):
        heading = None
        if i < len(lines):
            for color, start_f, end_f in formats[i]:
                if color in (C_H1, C_H2, C_H3):
                    heading = lines[i][start_f:end_f]
                    break
        if heading is not None:
            if title is None:
                start, title = i, heading
            else:
                title += " " + heading
        elif title is not None:
            found.append((start, title.lower().replace(" ", "-")))
            title = None
    return found


def find_heading(lines, formats, anchor):
    """Find index of the first line of anchor heading"""
    for start, heading in heading_anchors(lines, formats):
        if heading == anchor.lower():
            return start
    return -1


def load(url, file_downloader):
    """Return the markdown of page url from local documentation or download it the first time"""
    if url not in TEXT:
        name = url.split("/")[-1]
        try:
            path = None
            for folder in DOC_DIRS:
                if os.path.exists(folder):
                    for root, _, files in os.walk(folder):
                        if name in files:
                            path = os.path.join(root, name)
                            break
                if path:
                    break
            if not path:
                path, error = file_downloader.download(url)
                if not path:
                    return f"# Could not load this page\n\n{url}\n\n{error}"
            with open(path, "r", encoding="utf-8") as f:
                TEXT[url] = f.read()
        except Exception as e:
            return f"# Could not load this page\n\n{url}\n\n{e}"
    return TEXT[url]


# tui

def draw_border(screen, corners):
    """Draw border at screen borders with custom corners"""
    h, w = screen.getmaxyx()
    try:
        screen.hline(0, 1, curses.ACS_HLINE, w - 2, curses.color_pair(1))
        screen.hline(h - 1, 1, curses.ACS_HLINE, w - 2, curses.color_pair(1))
        screen.vline(1, 0, curses.ACS_VLINE, h - 2, curses.color_pair(1))
        screen.vline(1, w - 1, curses.ACS_VLINE, h - 2, curses.color_pair(1))
        screen.addstr(0, 0, corners[0], curses.color_pair(1))
        screen.addstr(0, w - 1, corners[2], curses.color_pair(1))
        screen.addstr(h - 1, 0, corners[1], curses.color_pair(1))
        screen.addstr(h - 1, w - 1, corners[3], curses.color_pair(1))
    except curses.error:   # errors randomly when resizing
        pass


def main_tui(screen, config):
    """Main documentation viewer tui"""
    curses.use_default_colors()
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    curses.init_pair(1, -1, -1)   # default
    curses.init_pair(2, 215, 234)   # code
    curses.init_pair(3, 117, -1)   # heading 1/2 and url
    curses.init_pair(4, 119, -1)   # heading 3
    curses.init_pair(5, 245, -1)   # dim and quote
    curses.init_pair(6, config["color_tree_selected"][0], config["color_tree_selected"][1])   # tree selected line
    screen.bkgd(" ", curses.color_pair(1))
    file_downloader = downloader.Downloader(proxy=config["proxy"], save_dir=DOWNLOAD_DIR, headers=HEADER)

    # select parser
    try:
        backspace_code = 8 if (curses.erasechar() == 8 or "XTERM_VERSION" in os.environ or sys.platform == "win32") else 127
    except Exception:
        backspace_code = 127
    fallback_keybinding_parser = (config["fallback_keybinding_parser"] or sys.platform == "win32") and not uses_gtkcurses
    if fallback_keybinding_parser:
        screen.keypad(True)
        get_key = keybinding.get_key_fallback
        backspace_code = 263 if backspace_code == 127 else 8
    elif uses_gtkcurses:
        curses.set_nice_exit(True)
        get_key = keybinding.get_key_passthrough
    else:
        screen.keypad(False)
        get_key = keybinding.get_key

    bordered = not config["compact"]
    corners = config["border_corners"]
    x0 = int(bordered)
    current = PAGES[0][1]
    lines = []
    formats = []
    links = []
    old_w = None
    scroll_idx = 0
    selected_link = None
    toc_selected = 0

    while True:
        h, w = screen.getmaxyx()
        screen_h = h - 2 * x0
        screen_w = w - 2 * x0 - TOC_WIDTH - 1
        text_w = min(screen_w, MAX_TEXT_WIDTH)
        screen.erase()
        win_toc = screen.derwin(screen_h, TOC_WIDTH, x0, x0)
        win_viewer = screen.derwin(screen_h, screen_w, x0, x0 + TOC_WIDTH + 1)

        # parse
        if old_w != text_w:   # when changed simensions
            lines, formats, links = parse(load(current, file_downloader), text_w)
            old_w = text_w
        scroll_idx = max(min(scroll_idx, len(lines) - screen_h), 0)
        urls = []   # links on screen as (line, start, end, link idx)
        for num in range(scroll_idx, min(scroll_idx + screen_h, len(lines))):
            for color, start, end in formats[num]:
                if color >= C_LINK:
                    urls.append((num, start, end, color - C_LINK))
        urls.sort()

        # draw toc
        for num, (title, page) in enumerate(PAGES):
            attr = curses.color_pair(6 if num == toc_selected else 1)
            attr |= curses.A_BOLD if page == current else 0
            win_toc.addstr(num, 0, title.ljust(TOC_WIDTH)[:TOC_WIDTH], attr)
        num += 1
        while num < screen_h:
            win_toc.insstr(num, 0, f"{" " if num % 2 else ALT_SPACE}\n", curses.color_pair(1))
            num += 1
        screen.vline(x0, x0 + TOC_WIDTH, curses.ACS_VLINE, screen_h, curses.color_pair(1))

        # draw page
        for row in range(min(screen_h, len(lines) - scroll_idx)):
            text = lines[scroll_idx + row]
            styles = [[1, 0] for _ in text]
            for color, start, end in formats[scroll_idx + row]:
                if color >= C_LINK:
                    style = (4 if color - C_LINK == selected_link else 3, curses.A_UNDERLINE)
                else:
                    style = STYLES[color]
                for i in range(start, min(end, len(text))):
                    if style[0]:
                        styles[i][0] = style[0]
                    styles[i][1] |= style[1]
            i = 0
            while i < len(text):
                j = i + 1
                while j < len(text) and styles[j] == styles[i]:
                    j += 1
                try:
                    win_viewer.addstr(row, i, text[i:j], curses.color_pair(styles[i][0]) | styles[i][1])
                except curses.error:
                    pass
                i = j

        if bordered:
            draw_border(screen, corners)
        screen.refresh()

        # input
        old_scroll_idx = scroll_idx
        open_href = None
        target_page = None
        target_anchor = ""
        key = get_key(screen, backspace_code)
        if key == -1:
            continue
        if isinstance(key, tuple):   # mouse
            my, mx, key_type, pressed = key
            if pressed and key_type == 64:   # SCROLL UP
                scroll_idx -= 3
            elif pressed and key_type == 65:   # SCROLL DOWN
                scroll_idx += 3
            elif pressed and key_type == 0:   # LEFT CLICK
                if x0 <= mx < x0 + TOC_WIDTH and 0 <= my - x0 < len(PAGES):
                    toc_selected = my - x0
                    target_page = PAGES[toc_selected][1]
                else:
                    for num, start, end, n in urls:
                        if num - scroll_idx == my - x0 and x0 + TOC_WIDTH + 1 + start <= mx < x0 + TOC_WIDTH + 1 + end:
                            open_href = links[n]
                            break
        elif key in ("ESC", "C-c"):
            break
        elif key in ("UP", "DOWN"):
            scroll_idx = scroll_idx + (1 if key == "DOWN" else -1)
        elif key in ("PGUP", "PGDN"):
            scroll_idx = scroll_idx + (screen_h if key == "PGDN" else -screen_h)
        elif key == "HOME":
            scroll_idx = 0
        elif key == "END":
            scroll_idx = len(lines)
        elif key in ("C-LEFT", "C-RIGHT"):   # select previous or next visible link
            order = list(dict.fromkeys(segment[3] for segment in urls))
            if order and selected_link in order:
                num = order.index(selected_link) + (1 if key == "C-RIGHT" else -1)
                selected_link = order[max(min(num, len(order) - 1), 0)]
            elif order:
                selected_link = order[0] if key == "C-RIGHT" else order[-1]
        elif key == "ENTER" and selected_link is not None:
            open_href = links[selected_link]
        elif key == "C-UP":
            toc_selected = max(toc_selected - 1, 0)
        elif key == "C-DOWN":
            toc_selected = min(toc_selected + 1, len(PAGES) - 1)
        elif key in ("C-SPACE"):
            target_page = PAGES[toc_selected][1]
        scroll_idx = max(min(scroll_idx, len(lines) - screen_h), 0)
        if scroll_idx != old_scroll_idx:   # deselect link when scrolled
            selected_link = None

        # open link
        if open_href is not None:
            target_page = None
            target_anchor = None
            url = None
            anchor = open_href.split("#", 1)[1] if "#" in open_href else ""
            if open_href.startswith("#"):
                target_page = current
                target_anchor = anchor
            else:
                url = (open_href if "://" in open_href else resolve(current, open_href)).split("#")[0]
                if url.startswith(WIKI) and "." not in url.split("/")[-1]:
                    url += ".md"
                if url.endswith(".md") and (url.startswith(WIKI) or url.startswith(REPO)):
                    target_page = url
                    target_anchor = anchor
                else:
                    webbrowser.open(url)
        if target_page:
            if target_page != current:
                win_viewer.clear()
                win_viewer.addstr(0, 0, "Loading...")
                win_viewer.refresh()
                current = target_page
                lines, formats, links = parse(load(current, file_downloader), text_w)
                toc_selected = next((num for num, (_, page) in enumerate(PAGES) if page == current), toc_selected)
                scroll_idx = 0
            if target_anchor:
                num = find_heading(lines, formats, target_anchor)
                if num >= 0:
                    scroll_idx = num
            selected_link = None

    screen.clear()
    screen.refresh()


def documentation(config):
    """Show documentation viewer"""
    try:
        curses.wrapper(main_tui, config)
    except curses.error as e:
        if str(e) == "addwstr() returned ERR":
            sys.exit("There was no enough space to render the TUI, please increase the terminal size")
