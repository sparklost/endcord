# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import asyncio

from pyscript import document, fetch, window
from pyscript.ffi import create_proxy

SIDEBAR_WIDTH = 20
MAX_TEXT_WIDTH = 100
CHAR_HEIGHT = 20
BUNDLE = "repo/"
REPO = "https://raw.githubusercontent.com/sparklost/endcord/main/"
WIKI = "https://raw.githubusercontent.com/sparklost/endcord/main/docs/"
PAGES = [
    ("Home", "home"),
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
REPO_URL = "https://github.com/sparklost/endcord"
RELEASES_URL = "https://github.com/sparklost/endcord/releases"
INSTALL_CMD = 'bash -c "$(curl -fsSL https://raw.githubusercontent.com/sparklost/endcord/main/tools/install.sh)"'
DISCORD_URL = "https://discord.gg/judQSxw5K2"
BUG_URL = "https://github.com/sparklost/endcord/issues"
RPC_URL = "https://github.com/sparklost/endcord-rpc"
HOME_MD = f"""# Endcord

Hi, I dont know how to make websites, I only know how to make TUIs, so you get this instead.

- [Read the README]({PAGES[1][1]})
- [Browse the wiki]({PAGES[2][1]})
- [Browse endcord extensions]({PAGES[3][1]})
- [Pick the binary you want on GitHub releases]({RELEASES_URL})
- [Copy the linux/macos endcord-installer.sh command](copy:install)
- [Join the official discord server]({DISCORD_URL})
- [View the repo on GitHub]({REPO_URL})
- [Report a bug on GitHub]({BUG_URL})
- [endcord-rpc, the miniature rpc-only client]({RPC_URL})

Made with my own 2 hands - by SparkLost
And you may not edit this website on GitHub
"""


# markdown parsing

C_BOLD, C_CODE, C_H1, C_H2, C_H3, C_QUOTE, C_DIM, C_BLOCK, C_LINK = 1, 2, 3, 4, 5, 6, 7, 8, 9
BLOCK_COLOR = {"h1": C_H1, "h2": C_H2, "h3": C_H3, "q": C_QUOTE, "code": C_BLOCK}


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
            items.append(["li", ind + "• ", ind + "  ", body[2:]])
        elif 0 < dot <= 3 and body[:dot].isdigit():
            items.append(["li", ind + body[:dot + 2], ind + " " * (dot + 2), body[dot + 2:]])
        elif items and items[-1] and items[-1][0] == "p":
            items[-1][3] += raw_strip + ("\n" if raw.endswith("  ") else " ")
        else:
            items.append(["p", "", "", raw_strip + ("\n" if raw.endswith("  ") else " ")])

    prev = ""
    for item in items:
        if not item:
            continue
        kind, first, rest, source = item
        if kind == "p":
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


# rendering to html

NAMES = ["", "b", "c", "h1", "h2", "h3", "q", "dim", "cb", "l"]   # css class for each color id
TEXT = {"home": HOME_MD}
DOC = [[], []]
current_page = "home"
char_width = 9.6
frame = document.getElementById("frame")
main = document.getElementById("main")
side = document.getElementById("side")
page = document.getElementById("page")


def escape(t):
    """Escape text for html"""
    return t.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


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


def find_heading(lines, formats, anchor):
    """Find index of the first line of anchor heading"""
    title = None
    start = 0
    for i in range(len(lines) + 1):
        if i < len(lines):
            for color, start_f, end_f in formats[i]:
                if color in (C_H1, C_H2, C_H3):
                    heading = lines[i][start_f:end_f]
                    break
            else:
                heading = None
        else:
            heading = None
        if heading is not None:
            if title is None:
                start, title = i, heading
            else:
                title += " " + heading
        elif title is not None:
            if title.lower().replace(" ", "-") == anchor.lower():
                return start
            title = None
    return -1


def hash_for(key, anchor=""):
    """Build url hash that opens page key at heading anchor"""
    for title, page_key in PAGES:
        if page_key == key:
            name = title.strip(" -").lower().replace(" ", "-")
            break
    else:   # not in sidebar
        name = key[len(REPO):] if key.startswith(REPO) else ""
    return name + "/" + anchor if name and anchor else name


def page_from_hash():
    """Get page key and heading from the url hash"""
    hash_text = window.location.hash[1:]
    for title, key in PAGES:
        name = title.strip(" -").lower().replace(" ", "-")
        if hash_text.lower() == name or hash_text.lower().startswith(name + "/"):
            return key, hash_text[len(name) + 1:]
    i = hash_text.find(".md")
    if i > 0:
        return REPO + hash_text[:i + 3], hash_text[i + 4:]
    return None, ""


def set_hash(key, anchor=""):
    """Set page key and heading anchor in the url hash"""
    hash_text = hash_for(key, anchor)
    if hash_text and window.location.hash[1:] != hash_text:
        if window.location.hash:
            window.location.hash = hash_text
        else:   # first page
            window.history.replaceState(None, "", "#" + hash_text)


def show(text):
    """Parse markdown text and display it"""
    width = max(10, min(int(main.clientWidth / char_width), MAX_TEXT_WIDTH))
    lines, formats, links = parse(text, width)
    DOC[0], DOC[1] = lines, formats
    attrs = []

    for href in links:
        raw_anchor = href.split("#", 1)[1] if "#" in href else ""
        anchor = escape(raw_anchor)
        url = (href if "://" in href else resolve(current_page, href)).split("#")[0]
        if url.startswith(WIKI) and "." not in url.split("/")[-1]:
            url += ".md"
        dest = url if url.endswith(".md") and (url.startswith(WIKI) or url.startswith(REPO)) else None

        if href.startswith("#"):
            attrs.append(f'href="#{escape(hash_for(current_page, raw_anchor))}" data-anchor="{anchor}"')
        elif href == "copy:install":
            attrs.append('href="#" data-copy="1"')
        elif dest:
            attrs.append(f'href="#{escape(hash_for(dest, raw_anchor))}" data-go="{escape(dest)}" data-anchor="{anchor}"')
        else:
            resolved = escape(url if "://" in href else resolve(current_page, href))
            attrs.append(f'href="{resolved}" target="_blank" rel="noopener"')
    ready_lines = []
    for line_text, line_format in zip(lines, formats):
        marks = [()] * len(line_text)
        for cid, start, end in line_format:
            for i in range(start, end):
                marks[i] = marks[i] + (cid,)
        out, i = [], 0
        while i < len(line_text):
            j = i + 1
            while j < len(line_text) and marks[j] == marks[i]:
                j += 1
            if not marks[i]:
                out.append(escape(line_text[i:j]))
                i = j
                continue
            cls = " ".join([NAMES[min(c, C_LINK)] for c in marks[i]])
            for c in marks[i]:
                if c >= C_LINK:
                    out.append(f'<a class="{cls}" data-n="{c - C_LINK}" {attrs[c - C_LINK]}>{escape(line_text[i:j])}</a>')
                    break
            else:
                out.append(f'<span class="{cls}">{escape(line_text[i:j])}</span>')
            i = j
        ready_lines.append("".join(out))
    main.innerHTML = "\n".join(ready_lines)


async def load(key):
    """Return the markdown of page key, download it the first time"""
    if key in TEXT:
        return TEXT[key]
    try:
        response = None
        if key.startswith(REPO):   # check for bundled markdown
            path = key[len(REPO):]
            if path.startswith(".github/"):
                path = path[1:]   # remove the dot
            try:
                response = await fetch(BUNDLE + path)
            except Exception:   # for example when opened as a local file
                response = None
        if response is None or not response.ok:   # fallback to download
            response = await fetch(key)
        if not response.ok:
            raise Exception("HTTP " + str(response.status))
        TEXT[key] = await response.text()
        return TEXT[key]
    except Exception as ex:
        return "# Could not load this page\n\n" + key + "\n\n" + str(ex)


async def go(key, anchor=""):
    """Open page key and scrol to heading anchor"""
    global current_page
    current_page = key
    set_hash(key, anchor)

    for title, page_key in PAGES:
        if page_key == key:
            page.textContent = title.strip(" - ")
            break
    else:
        page.textContent = key.split("/")[-1].replace(".md", "")

    side_html = []
    for title, page_key in PAGES:
        side_html.append(f'<a href="#{hash_for(page_key)}" data-go="{escape(page_key)}"{' class="cur"' if page_key == current_page else ""}>{escape(title)}</a>')
    side.innerHTML = "".join(side_html)

    show(TEXT.get(key, "Loading..."))
    text = await load(key)
    if current_page == key:
        show(text)
        main.scrollTop = 0
        if anchor:
            i = find_heading(DOC[0], DOC[1], anchor)
            if i >= 0:
                main.scrollTop = i * CHAR_HEIGHT
        main.focus()


# handle input

def on_click(e):
    """Handle clicks on links"""
    a = e.target.closest("a")
    if not a:
        return
    key, anchor = a.getAttribute("data-go"), a.getAttribute("data-anchor")

    if key:   # go to local file
        e.preventDefault()
        asyncio.create_task(go(key, anchor))

    elif a.getAttribute("data-copy"):   # copy to clipboard
        e.preventDefault()
        window.navigator.clipboard.writeText(INSTALL_CMD)

    elif a.getAttribute("href").startswith("#"):   # go to this file anchor
        e.preventDefault()
        if anchor:
            i = find_heading(DOC[0], DOC[1], anchor)
            if i >= 0:
                main.scrollTop = i * CHAR_HEIGHT
                set_hash(current_page, anchor)


def on_key(e):
    """Handle keyboard keys"""
    k = e.key
    if k in ("Escape", "Backspace"):   # go home
        asyncio.create_task(go("home"))

    elif current_page == "home" and k in ("ArrowUp", "ArrowDown"):   # navigate urls in home
        count = main.querySelectorAll("a").length
        i = document.activeElement.getAttribute("data-n")
        i = int(i) + (1 if k == "ArrowDown" else -1) if i else (0 if k == "ArrowDown" else count - 1)
        i = max(min(count - 1, i), 0)
        main.querySelector(f'a[data-n="{i}"]').focus()
    else:
        return
    e.preventDefault()


def on_resize(_):
    """Resize frame and redraw page"""
    root = document.documentElement
    frame.style.width = f"{int(int(root.clientWidth / char_width) * char_width)}px"
    frame.style.height = f"{int(root.clientHeight / CHAR_HEIGHT) * CHAR_HEIGHT}px"
    show(TEXT.get(current_page, "Loading..."))


def on_hash_change(_):
    """Open the page and heading from the url hash"""
    key, anchor = page_from_hash()
    key = key or "home"
    if key != current_page:
        asyncio.create_task(go(key, anchor))
    elif anchor:
        i = find_heading(DOC[0], DOC[1], anchor)
        if i >= 0:
            main.scrollTop = i * CHAR_HEIGHT


try:
    await document.fonts.load("16px 'Source Code Pro'")   # noqa
    await document.fonts.load("700 16px 'Source Code Pro'")   # noqa
except Exception:
    pass

measure = document.getElementById("measure")
measure.textContent = "M" * 100
char_width = measure.getBoundingClientRect().width / 100
document.documentElement.style.setProperty("--hx", f"{int(char_width / 2)}px")
root = document.documentElement
frame.style.width = f"{int(int(root.clientWidth / char_width) * char_width)}px"
frame.style.height = f"{int(root.clientHeight / CHAR_HEIGHT) * CHAR_HEIGHT}px"
side.style.width = f"{SIDEBAR_WIDTH}ch"

body = document.body
body.addEventListener("click", create_proxy(on_click))
body.addEventListener("keydown", create_proxy(on_key))
document.defaultView.addEventListener("resize", create_proxy(on_resize))
document.defaultView.addEventListener("hashchange", create_proxy(on_hash_change))

start_key, start_anchor = page_from_hash()
await go(start_key or "home", start_anchor)   # noqa
