"""Inline profile pictures via the Kitty graphics protocol.

Renders each message's author avatar at the chat header line using
Kitty's image transmission + placement commands. Images are written
directly to stdout (bypassing curses) and re-placed on every chat
redraw so they track scroll.

Layout: avatars are 2 columns wide and 2 rows tall, placed at the
leftmost columns of the chat window. The chat formatter prepends 3
spaces to each header / newline so the avatar doesn't cover text.
"""

import base64
import hashlib
import io
import logging
import os
import sys
import threading
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Pillow is optional - if not present, inline PFPs are disabled.
try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# Width/height in terminal cells. 5×2 keeps the avatar square at the
# typical Kitty cell ratio (~1:2.5). The chat-side pad is 6 = 5 avatar
# cells + 1 col gap between the avatar and the message text.
PFP_COLS = 5
PFP_ROWS = 2
# Inline custom emoji size. Single-row so the image never bleeds into
# the row below. 3 cols wide gives a slight horizontal stretch but
# stays roughly emoji-sized (~text height).
EMOJI_COLS = 3
EMOJI_ROWS = 1
# Inline image-attachment thumbnail. The actual display dimensions
# (cols × rows) are computed per-image to preserve the source aspect
# ratio. ATTACHMENT_MAX_COLS / ATTACHMENT_MAX_ROWS are upper bounds.
# CELL_ASPECT = approximate cell height / cell width for typical Kitty
# fonts; used to translate source pixel aspect into cell aspect.
ATTACHMENT_MAX_COLS = 40
ATTACHMENT_MAX_ROWS = 18
# Cell height / cell width. Increase to make images wider, decrease
# to make them taller (counter-intuitive: this is the multiplier
# we use to translate source aspect into cell aspect).
CELL_ASPECT = 2.8
# Max pixel dim for the on-disk thumbnail. Big enough that Kitty
# doesn't have to upscale when rendering at MAX_COLS×MAX_ROWS cells.
ATTACHMENT_THUMB_PX = 768
# HTTP timeout for synchronous attachment downloads.
ATTACHMENT_TIMEOUT_S = 4
# Pixel size to request for emoji from the CDN.
EMOJI_SIZE_PX = 48
# Pixel size to ask Discord for. Anything >= cell-pixels * dimensions works.
PFP_SIZE_PX = 64
# Kitty image IDs are 32-bit. Start at a high offset so we don't clash
# with anything else that might be using the protocol (e.g. notifications).
KITTY_ID_BASE = 0x70667000   # "pfp\0"


def kitty_supported():
    """Heuristic: is the current terminal Kitty-compatible?

    Checks $TERM and a couple of common env vars. We don't query the
    terminal here because that requires read-back on stdin which we
    can't safely do while curses owns it.
    """
    term = os.environ.get("TERM", "")
    if "kitty" in term:
        return True
    if os.environ.get("TERM_PROGRAM") == "kitty":
        return True
    # Ghostty also implements the protocol.
    if os.environ.get("TERM_PROGRAM") == "ghostty":
        return True
    if os.environ.get("KITTY_WINDOW_ID"):
        return True
    return False


def _send(payload):
    """Write a Kitty graphics escape sequence to stdout."""
    try:
        os.write(sys.stdout.fileno(), payload)
    except OSError:
        pass


def detect_cell_aspect():
    """Probe the controlling terminal for cell pixel dimensions via
    TIOCGWINSZ. Returns cell_h / cell_w as a float, or None if the
    terminal didn't report pixel dimensions (most non-Kitty TTYs).
    Kitty (and Ghostty) fill ws_xpixel/ws_ypixel so this works without
    a stdin-blocking CSI query.
    """
    try:
        import fcntl
        import termios
        for path in ("/dev/tty", None):
            try:
                fd = os.open(path, os.O_RDONLY) if path else sys.stdout.fileno()
                buf = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
                if path:
                    os.close(fd)
                import struct
                rows, cols, xpix, ypix = struct.unpack("HHHH", buf)
                if rows > 0 and cols > 0 and xpix > 0 and ypix > 0:
                    cw = xpix / cols
                    ch = ypix / rows
                    return ch / cw
            except OSError:
                continue
    except ImportError:
        pass
    return None


def best_square(rows, cell_aspect):
    """Return (cols, rows) closest to a 1:1 visual square at the given
    cell aspect. rows is fixed (callers choose layout height); cols is
    picked from {floor, ceil} of rows×cell_aspect, whichever lands
    closer to a true square."""
    ideal_c = rows * cell_aspect
    best = None
    for c in (max(1, int(ideal_c)), max(1, int(ideal_c) + 1)):
        visual = c / (rows * cell_aspect)
        err = abs(visual - 1.0) if visual >= 1.0 else abs(1.0 / visual - 1.0)
        if best is None or err < best[0]:
            best = (err, c)
    return best[1], rows


def _chunk_payload(data, controls):
    """Build the Kitty protocol APC sequence(s).

    Long payloads are split into chunks of 4096 base64 chars with m=1
    on all but the last chunk.
    """
    b64 = base64.standard_b64encode(data)
    chunks = []
    chunk_size = 4096
    pos = 0
    first = True
    while pos < len(b64):
        chunk = b64[pos:pos + chunk_size]
        pos += chunk_size
        more = pos < len(b64)
        if first:
            ctrl = controls + (",m=1" if more else "")
        else:
            ctrl = ("m=1" if more else "m=0")
        chunks.append(b"\x1b_G" + ctrl.encode("ascii") + b";" + chunk + b"\x1b\\")
        first = False
    return b"".join(chunks)


class PfpRenderer:
    """Owns the avatar cache and pushes images into the terminal."""

    def __init__(self, cache_path, discord, enabled=True):
        self.discord = discord
        self.cache_path = cache_path
        self.enabled = enabled and HAVE_PIL and kitty_supported()
        # avatar_id -> kitty image id (already transmitted)
        self._transmitted = {}
        # avatar_id -> True while a background download is in flight
        self._fetching = set()
        # next free Kitty image id
        self._next_id = KITTY_ID_BASE
        # image ids that have at least one placement currently on screen.
        # We delete by image id (kills all placements for that image)
        # so we don't need to track individual placement ids.
        self._placed = set()
        # Monotonic placement-id counter — every place() gets a unique
        # placement_id so two avatars from the same author don't collide.
        self._next_placement_id = 1
        self._lock = threading.Lock()
        # Auto-tune (cols, rows) to render a visually square avatar at
        # the terminal's actual cell pixel aspect. Falls back to the
        # hardcoded 5×2 if the terminal doesn't report pixel dims.
        detected = detect_cell_aspect()
        self.cell_aspect = detected or CELL_ASPECT
        self.pfp_cols, self.pfp_rows = best_square(2, self.cell_aspect)
        self.emoji_cols, self.emoji_rows = best_square(1, self.cell_aspect)
        # url -> (source_width_px, source_height_px), populated by
        # measure_attachment. Used for source-region crops when an image
        # is only partially on-screen.
        self._attach_px = {}
        logger.info(
            f"pfp cell_aspect={self.cell_aspect:.3f} "
            f"(detected={detected is not None}) "
            f"pfp={self.pfp_cols}x{self.pfp_rows} "
            f"emoji={self.emoji_cols}x{self.emoji_rows}"
        )

    def _alloc_id(self, avatar_id):
        with self._lock:
            if avatar_id in self._transmitted:
                return self._transmitted[avatar_id]
            kid = self._next_id
            self._next_id += 1
            self._transmitted[avatar_id] = kid
            return kid

    def _avatar_path(self, user_id, avatar_id):
        """Return a local path to a square 64px PNG of the avatar.

        Downloads + converts if needed. Returns None on failure.
        """
        png_name = f"pfp_{avatar_id}.png"
        png_path = os.path.join(os.path.expanduser(self.cache_path), png_name)
        if os.path.isfile(png_path):
            return png_path
        # Pull the webp via endcord's existing helper, then convert.
        webp_path = self.discord.get_pfp(
            user_id, avatar_id, size=PFP_SIZE_PX, save_path=self.cache_path,
        )
        if not webp_path or not os.path.isfile(webp_path):
            return None
        try:
            with Image.open(webp_path) as im:
                im = im.convert("RGBA").resize(
                    (PFP_SIZE_PX, PFP_SIZE_PX), Image.LANCZOS,
                )
                im.save(png_path, format="PNG")
        except Exception as e:
            logger.debug(f"pfp convert failed for {avatar_id}: {e}")
            return None
        return png_path

    def _ensure_transmitted(self, user_id, avatar_id):
        """If the image isn't on the terminal yet, transmit it.

        Returns the Kitty image id, or None if not yet ready.
        """
        if avatar_id in self._transmitted:
            return self._transmitted[avatar_id]
        if avatar_id in self._fetching:
            return None
        # Fetch synchronously here — caller is the draw thread; the cache
        # hit path is fast and the cold path happens at most once per user.
        # Background-thread this if it becomes a bottleneck.
        self._fetching.add(avatar_id)
        try:
            path = self._avatar_path(user_id, avatar_id)
            if not path:
                return None
            kid = self._alloc_id(avatar_id)
            with open(path, "rb") as f:
                data = f.read()
            # a=t: transmit only. f=100: PNG (auto-detect). i: image id.
            # q=2: suppress all responses. C=1: don't move cursor.
            ctrl = f"a=t,f=100,i={kid},q=2"
            _send(_chunk_payload(data, ctrl))
            return kid
        finally:
            self._fetching.discard(avatar_id)

    def clear_placements(self):
        """Remove all current avatar placements from the terminal.

        Uses lowercase d=i which deletes placements but preserves the
        image data in Kitty's storage, so we don't have to re-transmit
        the bytes on the next placement.
        """
        if not self.enabled or not self._placed:
            return
        for kid in self._placed:
            _send(b"\x1b_Ga=d,d=i,i=" + str(kid).encode("ascii") + b",q=2\x1b\\")
        self._placed.clear()


    def invalidate_transmissions(self):
        """Forget which images have been sent to the terminal.

        Call this after anything that drops Kitty's image storage (e.g.
        the full-screen clear we issue on tree toggle). Forces the next
        place() to re-transmit the image bytes.
        """
        with self._lock:
            self._transmitted.clear()
            self._placed.clear()
            self._next_id = KITTY_ID_BASE

    def _emoji_path(self, emoji_id):
        """Download + convert a custom emoji to a square PNG. Returns
        local path or None on failure.
        """
        png_name = f"emoji_{emoji_id}.png"
        png_path = os.path.join(os.path.expanduser(self.cache_path), png_name)
        if os.path.isfile(png_path):
            return png_path
        webp_path = self.discord.get_emoji(emoji_id, size=EMOJI_SIZE_PX)
        if not webp_path or not os.path.isfile(webp_path):
            return None
        try:
            with Image.open(webp_path) as im:
                im = im.convert("RGBA").resize(
                    (EMOJI_SIZE_PX, EMOJI_SIZE_PX), Image.LANCZOS,
                )
                im.save(png_path, format="PNG")
        except Exception as e:
            logger.debug(f"emoji convert failed for {emoji_id}: {e}")
            return None
        return png_path

    def _ensure_emoji_transmitted(self, emoji_id):
        """Transmit a custom-emoji image to Kitty if not already there.

        Cache key is the emoji_id (a numeric string) — distinct from
        avatar hashes so the two namespaces don't collide.
        """
        key = f"emoji:{emoji_id}"
        if key in self._transmitted:
            return self._transmitted[key]
        if key in self._fetching:
            return None
        self._fetching.add(key)
        try:
            path = self._emoji_path(emoji_id)
            if not path:
                return None
            with self._lock:
                kid = self._next_id
                self._next_id += 1
                self._transmitted[key] = kid
            with open(path, "rb") as f:
                data = f.read()
            ctrl = f"a=t,f=100,i={kid},q=2"
            _send(_chunk_payload(data, ctrl))
            return kid
        finally:
            self._fetching.discard(key)

    def _attachment_path(self, url):
        """Download and PNG-thumbnail an arbitrary image URL.

        Cached by URL hash; returns local path or None on failure.
        Synchronous — caller is the draw thread, so this blocks. The
        cache-hit path is fast; first load of each image incurs the
        download cost once.
        """
        if not HAVE_PIL:
            return None
        key = hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:16]
        png_path = os.path.join(os.path.expanduser(self.cache_path), f"attach_{key}.png")
        if os.path.isfile(png_path):
            return png_path
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "endcord"})
            with urllib.request.urlopen(req, timeout=ATTACHMENT_TIMEOUT_S) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug(f"attachment fetch failed {url}: {e}")
            return None
        try:
            with Image.open(io.BytesIO(raw)) as im:
                im = im.convert("RGB")
                im.thumbnail((ATTACHMENT_THUMB_PX, ATTACHMENT_THUMB_PX), Image.LANCZOS)
                im.save(png_path, format="PNG")
        except Exception as e:
            logger.debug(f"attachment convert failed {url}: {e}")
            return None
        return png_path

    def measure_attachment(
        self,
        url,
        max_cols=ATTACHMENT_MAX_COLS,
        max_rows=ATTACHMENT_MAX_ROWS,
        cell_aspect=None,
    ):
        """Download the image if needed and return (cols, rows) for an
        aspect-preserving placement.

        Returns None if the image can't be loaded. The returned cell
        dimensions fit within (max_cols, max_rows) and approximate the
        source pixel aspect ratio given the terminal's cell_aspect
        (cell_height / cell_width).
        """
        if not HAVE_PIL:
            return None
        if cell_aspect is None:
            cell_aspect = self.cell_aspect
        path = self._attachment_path(url)
        if not path:
            return None
        try:
            with Image.open(path) as im:
                sw, sh = im.size
        except Exception as e:
            logger.debug(f"attachment measure failed {url}: {e}")
            return None
        if sw <= 0 or sh <= 0:
            return None
        # Cell aspect = cell_h / cell_w. For a cell area c×r to display
        # an image with source aspect sw:sh undistorted:
        #   (c * cell_w) / (r * cell_h) = sw / sh
        #   c / r = (sw / sh) * cell_aspect
        ratio = (sw / sh) * cell_aspect
        # Fit within (max_cols, max_rows) preserving ratio.
        if ratio >= max_cols / max_rows:
            cols = max_cols
            rows = max(1, round(cols / ratio))
        else:
            rows = max_rows
            cols = max(1, round(rows * ratio))
        # Stash source-pixel dims for partial-render crops below.
        self._attach_px[url] = (sw, sh)
        return cols, rows

    def _ensure_attachment_transmitted(self, url):
        """Transmit attachment PNG to Kitty if not already; return image id."""
        key = f"attach:{url}"
        if key in self._transmitted:
            return self._transmitted[key]
        if key in self._fetching:
            return None
        self._fetching.add(key)
        try:
            path = self._attachment_path(url)
            if not path:
                return None
            with self._lock:
                kid = self._next_id
                self._next_id += 1
                self._transmitted[key] = kid
            with open(path, "rb") as f:
                data = f.read()
            ctrl = f"a=t,f=100,i={kid},q=2"
            _send(_chunk_payload(data, ctrl))
            return kid
        finally:
            self._fetching.discard(key)

    def place_attachment(self, url, row, col, cols=ATTACHMENT_MAX_COLS, rows=ATTACHMENT_MAX_ROWS,
                         crop_top_cells=0, full_rows=None):
        """Place an attachment thumbnail at (row, col).

        If `crop_top_cells > 0`, render only the bottom slice of the image
        (the top `crop_top_cells × cell_h` source pixels are skipped). The
        slice is displayed in `rows` cells starting at `row`, so callers
        should pass the visible-only row count as `rows` and the image's
        full row count as `full_rows`. Used when the image header is
        off-screen above the chat region but its reserved row band is
        still visible — without it, the reserve cells show as empty
        whitespace at the top of the chat.
        """
        if not self.enabled or not url:
            return
        kid = self._ensure_attachment_transmitted(url)
        if kid is None:
            return
        with self._lock:
            pid = self._next_placement_id
            self._next_placement_id += 1
        cup = f"\x1b[{row + 1};{col + 1}H".encode("ascii")
        if crop_top_cells > 0 and full_rows and url in self._attach_px:
            sw, sh = self._attach_px[url]
            # Crop the top of the source image proportionally. Y/h are in
            # source pixels; r is the cell count we're rendering into.
            y_off = max(0, min(sh - 1, int(crop_top_cells * sh / full_rows)))
            h_src = max(1, sh - y_off)
            controls = (
                f"a=p,i={kid},p={pid},c={cols},r={rows},Y={y_off},H={h_src},"
                f"W={sw},X=0,C=1,q=2"
            )
        else:
            controls = f"a=p,i={kid},p={pid},c={cols},r={rows},C=1,q=2"
        place = (f"\x1b_G{controls}\x1b\\").encode("ascii")
        _send(cup + place)
        self._placed.add(kid)

    def place_emoji(self, emoji_id, row, col, cols=None, rows=None):
        """Place a custom emoji at terminal cell (row, col)."""
        if not self.enabled or not emoji_id:
            return
        if cols is None:
            cols = self.emoji_cols
        if rows is None:
            rows = self.emoji_rows
        kid = self._ensure_emoji_transmitted(emoji_id)
        if kid is None:
            return
        with self._lock:
            pid = self._next_placement_id
            self._next_placement_id += 1
        cup = f"\x1b[{row + 1};{col + 1}H".encode("ascii")
        place = (
            f"\x1b_Ga=p,i={kid},p={pid},c={cols},r={rows},C=1,q=2\x1b\\"
        ).encode("ascii")
        _send(cup + place)
        self._placed.add(kid)

    def place(self, user_id, avatar_id, row, col):
        """Place this user's avatar at terminal cell (row, col).

        Uses Kitty's a=p with C=1 (don't move cursor) so we can write
        the placement without disturbing curses' notion of cursor pos.
        Position is set via the standard CUP escape before the place.

        Each call gets a fresh placement_id so multiple messages from
        the same author render side-by-side instead of the later one
        moving the earlier placement.
        """
        if not self.enabled or not avatar_id:
            return
        kid = self._ensure_transmitted(user_id, avatar_id)
        if kid is None:
            return
        with self._lock:
            pid = self._next_placement_id
            self._next_placement_id += 1
        cup = f"\x1b[{row + 1};{col + 1}H".encode("ascii")
        place = (
            f"\x1b_Ga=p,i={kid},p={pid},c={self.pfp_cols},r={self.pfp_rows},C=1,q=2\x1b\\"
        ).encode("ascii")
        _send(cup + place)
        self._placed.add(kid)
