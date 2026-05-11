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
import io
import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)

# Pillow is optional - if not present, inline PFPs are disabled.
try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# Width/height in terminal cells. 2x2 fits the header + first content row.
PFP_COLS = 2
PFP_ROWS = 2
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
        # placement ids currently on screen, so we can clear them
        self._placed = set()
        self._lock = threading.Lock()

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
        """Remove all current avatar placements from the terminal."""
        if not self.enabled or not self._placed:
            return
        for pid in self._placed:
            # a=d: delete. d=I: by image id. i=<id>. q=2: silent.
            _send(b"\x1b_Ga=d,d=I,i=" + str(pid).encode("ascii") + b",q=2\x1b\\")
        self._placed.clear()

    def place(self, user_id, avatar_id, row, col):
        """Place this user's avatar at terminal cell (row, col).

        Uses Kitty's a=p with C=1 (don't move cursor) so we can write
        the placement without disturbing curses' notion of cursor pos.
        Position is set via the standard CUP escape before the place.
        """
        if not self.enabled or not avatar_id:
            return
        kid = self._ensure_transmitted(user_id, avatar_id)
        if kid is None:
            return
        # Move cursor (1-indexed in CUP), then place. C=1 keeps the
        # cursor on the same cell after placement so curses isn't lost.
        cup = f"\x1b[{row + 1};{col + 1}H".encode("ascii")
        place = (
            f"\x1b_Ga=p,i={kid},p={kid},c={PFP_COLS},r={PFP_ROWS},C=1,q=2\x1b\\"
        ).encode("ascii")
        _send(cup + place)
        self._placed.add(kid)
