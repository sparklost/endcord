"""
QR Code Terminal Display Module

Provides robust, terminal-aware QR code rendering with automatic
capability detection and fallback strategies.

Features:
- Automatic terminal capability detection via termcap
- Multiple rendering modes: ANSI color, Unicode half-blocks, ASCII
- Auto-sizing based on terminal dimensions
- Curses integration for TUI display
- Raw terminal output for standalone use

Usage:
    from endcord.qr_display import QRDisplay, render_qr_code

    # Quick render (auto-detects best method)
    qr_string = render_qr_code("https://example.com")
    print(qr_string)

    # Full control
    display = QRDisplay()
    qr_string = display.render("https://example.com", mode="auto")
"""

import io
import logging
import shutil
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class RenderMode(Enum):
    """QR code rendering modes in order of preference."""
    ANSI_HALF_BLOCK = auto()   # Best: ANSI colors + Unicode half-blocks (segno)
    UNICODE_HALF_BLOCK = auto() # Good: Unicode half-blocks without ANSI
    UNICODE_FULL_BLOCK = auto() # OK: Full blocks, double height
    ASCII_BLOCK = auto()        # Fallback: ## for black, spaces for white
    ASCII_CHAR = auto()         # Legacy: X for black, space for white
    TEXT_ONLY = auto()          # Last resort: just the URL


@dataclass
class QRConfig:
    """Configuration for QR code rendering."""
    border: int = 2
    error_correction: str = "L"  # L, M, Q, H
    min_version: int = 1
    max_version: int = 40
    compact: bool = True  # Use half-blocks for vertical compactness
    invert: bool = False  # Invert colors (white on black)


class QRLibrary(Enum):
    """Available QR code libraries."""
    SEGNO = "segno"
    QRCODE = "qrcode"
    PYQRCODE = "pyqrcode"
    NONE = None


def detect_available_libraries() -> dict:
    """Detect which QR libraries are available."""
    available = {}

    try:
        import segno
        available[QRLibrary.SEGNO] = segno
        logger.debug("segno library available")
    except ImportError:
        pass

    try:
        import qrcode
        available[QRLibrary.QRCODE] = qrcode
        logger.debug("qrcode library available")
    except ImportError:
        pass

    try:
        import pyqrcode
        available[QRLibrary.PYQRCODE] = pyqrcode
        logger.debug("pyqrcode library available")
    except ImportError:
        pass

    if not available:
        logger.warning("No QR code libraries available - install segno or qrcode")

    return available


def get_terminal_size() -> Tuple[int, int]:
    """Get terminal dimensions with fallback."""
    try:
        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines
    except (ValueError, OSError):
        return 80, 24


class QRDisplay:
    """
    Terminal-aware QR code display system.

    Automatically detects terminal capabilities and chooses the best
    rendering method. Integrates with termcap for capability detection.
    """

    def __init__(self, config: Optional[QRConfig] = None):
        self.config = config or QRConfig()
        self._libraries = detect_available_libraries()
        self._term_caps = None
        self._cached_mode = None

    @property
    def term_caps(self):
        """Lazy-load terminal capabilities."""
        if self._term_caps is None:
            try:
                from endcord import termcap
                self._term_caps = termcap.detect_capabilities()
            except ImportError:
                self._term_caps = None
        return self._term_caps

    @property
    def has_unicode(self) -> bool:
        """Check if terminal supports Unicode."""
        if self.term_caps:
            return self.term_caps.has_unicode
        # Fallback: check locale
        import os
        lang = (
            os.environ.get("LC_ALL")
            or os.environ.get("LC_CTYPE")
            or os.environ.get("LANG")
            or ""
        )
        return "utf-8" in lang.lower() or "utf8" in lang.lower()

    @property
    def has_colors(self) -> bool:
        """Check if terminal supports colors."""
        if self.term_caps:
            return self.term_caps.colors >= 8
        # Fallback: check TERM
        import os
        term = os.environ.get("TERM", "").lower()
        return "color" in term or term in ("xterm", "screen", "tmux")

    @property
    def is_tty(self) -> bool:
        """Check if running in raw TTY (limited capabilities)."""
        if self.term_caps:
            return self.term_caps.is_tty
        import os
        return os.environ.get("TERM", "").lower() == "linux"

    def detect_best_mode(self) -> RenderMode:
        """Detect the best rendering mode for current terminal."""
        if self._cached_mode:
            return self._cached_mode

        # TTY has very limited capabilities
        if self.is_tty:
            self._cached_mode = RenderMode.ASCII_BLOCK
            return self._cached_mode

        # Check for segno (best output)
        if QRLibrary.SEGNO in self._libraries:
            if self.has_unicode and self.has_colors:
                self._cached_mode = RenderMode.ANSI_HALF_BLOCK
                return self._cached_mode
            elif self.has_unicode:
                self._cached_mode = RenderMode.UNICODE_HALF_BLOCK
                return self._cached_mode

        # Check for qrcode
        if QRLibrary.QRCODE in self._libraries:
            if self.has_unicode:
                self._cached_mode = RenderMode.UNICODE_HALF_BLOCK
                return self._cached_mode
            else:
                self._cached_mode = RenderMode.ASCII_BLOCK
                return self._cached_mode

        # Check for pyqrcode
        if QRLibrary.PYQRCODE in self._libraries:
            if self.has_colors:
                self._cached_mode = RenderMode.ANSI_HALF_BLOCK
                return self._cached_mode
            else:
                self._cached_mode = RenderMode.ASCII_CHAR
                return self._cached_mode

        # No libraries available
        self._cached_mode = RenderMode.TEXT_ONLY
        return self._cached_mode

    def _render_segno(self, data: str, mode: RenderMode) -> str:
        """Render using segno library."""
        segno = self._libraries[QRLibrary.SEGNO]
        qr = segno.make(data)

        f = io.StringIO()

        if mode == RenderMode.ANSI_HALF_BLOCK:
            # Full ANSI color support with half-blocks
            qr.terminal(out=f, compact=self.config.compact, border=self.config.border)
        elif mode == RenderMode.UNICODE_HALF_BLOCK:
            # Unicode half-blocks without ANSI colors
            # segno's terminal() always uses ANSI, so we build our own
            matrix = []
            for row in qr.matrix:
                matrix.append([bool(cell) for cell in row])
            return self._render_unicode_halfblock(matrix)
        else:
            # Fallback to terminal output
            qr.terminal(out=f, compact=self.config.compact, border=self.config.border)

        f.seek(0)
        return f.read()

    def _render_qrcode(self, data: str, mode: RenderMode) -> str:
        """Render using qrcode library."""
        qrcode = self._libraries[QRLibrary.QRCODE]

        qr = qrcode.QRCode(
            version=self.config.min_version,
            error_correction=getattr(qrcode.constants, f"ERROR_CORRECT_{self.config.error_correction}"),
            box_size=1,
            border=self.config.border,
        )
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()

        if mode in (RenderMode.ANSI_HALF_BLOCK, RenderMode.UNICODE_HALF_BLOCK):
            return self._render_unicode_halfblock(matrix)
        elif mode == RenderMode.UNICODE_FULL_BLOCK:
            return self._render_unicode_fullblock(matrix)
        elif mode == RenderMode.ASCII_BLOCK:
            return self._render_ascii_block(matrix)
        else:
            return self._render_ascii_char(matrix)

    def _render_pyqrcode(self, data: str, mode: RenderMode) -> str:
        """Render using pyqrcode library."""
        pyqrcode = self._libraries[QRLibrary.PYQRCODE]
        qr = pyqrcode.create(data)

        if mode in (RenderMode.ANSI_HALF_BLOCK, RenderMode.UNICODE_HALF_BLOCK):
            # pyqrcode terminal() uses ANSI
            return qr.terminal(quiet_zone=self.config.border)
        else:
            # Build matrix manually for other modes
            # pyqrcode doesn't expose matrix directly in a simple way
            # Fall back to terminal output
            return qr.terminal(quiet_zone=self.config.border)

    def _render_unicode_halfblock(self, matrix: list) -> str:
        """
        Render QR using Unicode half-block characters.
        Combines two vertical pixels into one character for compact display.

        Characters used:
        - ' ' (space): both pixels white
        - '▀' (U+2580): top black, bottom white
        - '▄' (U+2584): top white, bottom black
        - '█' (U+2588): both pixels black
        """
        lines = []
        height = len(matrix)
        width = len(matrix[0]) if matrix else 0

        # Add top border
        border_line = "█" * (width + self.config.border * 2)
        for _ in range(self.config.border):
            lines.append(border_line)

        # Process two rows at a time
        for y in range(0, height, 2):
            line = "█" * self.config.border  # Left border

            for x in range(width):
                top = matrix[y][x] if y < height else False
                bottom = matrix[y + 1][x] if y + 1 < height else False

                if self.config.invert:
                    top, bottom = not top, not bottom

                if top and bottom:
                    line += "█"  # Full block
                elif top and not bottom:
                    line += "▀"  # Upper half
                elif not top and bottom:
                    line += "▄"  # Lower half
                else:
                    line += " "  # Empty

            line += "█" * self.config.border  # Right border
            lines.append(line)

        # Add bottom border
        for _ in range(self.config.border):
            lines.append(border_line)

        return "\n".join(lines)

    def _render_unicode_fullblock(self, matrix: list) -> str:
        """Render using full Unicode blocks (larger but more compatible)."""
        lines = []

        for row in matrix:
            line = ""
            for cell in row:
                if self.config.invert:
                    cell = not cell
                line += "██" if cell else "  "
            lines.append(line)

        return "\n".join(lines)

    def _render_ascii_block(self, matrix: list) -> str:
        """Render using ASCII ## blocks (TTY compatible)."""
        lines = []

        for row in matrix:
            line = ""
            for cell in row:
                if self.config.invert:
                    cell = not cell
                line += "##" if cell else "  "
            lines.append(line)

        return "\n".join(lines)

    def _render_ascii_char(self, matrix: list) -> str:
        """Render using simple ASCII characters."""
        lines = []

        for row in matrix:
            line = ""
            for cell in row:
                if self.config.invert:
                    cell = not cell
                line += "XX" if cell else "  "
            lines.append(line)

        return "\n".join(lines)

    def render(self, data: str, mode: Optional[RenderMode] = None) -> str:
        """
        Render a QR code for terminal display.

        Args:
            data: The data to encode (URL, text, etc.)
            mode: Rendering mode. If None, auto-detects best mode.

        Returns:
            String representation of the QR code for terminal display.
        """
        if mode is None:
            mode = self.detect_best_mode()

        if mode == RenderMode.TEXT_ONLY or not self._libraries:
            return f"[QR Code - scan this URL]\n{data}"

        try:
            # Try libraries in order of preference
            if QRLibrary.SEGNO in self._libraries:
                return self._render_segno(data, mode)
            elif QRLibrary.QRCODE in self._libraries:
                return self._render_qrcode(data, mode)
            elif QRLibrary.PYQRCODE in self._libraries:
                return self._render_pyqrcode(data, mode)
        except Exception as e:
            logger.error(f"QR rendering failed: {e}")

        return f"[QR Code - scan this URL]\n{data}"

    def render_for_curses(self, data: str, max_width: int = 0, max_height: int = 0) -> Tuple[list, int, int]:
        """
        Render QR code for curses display.

        Args:
            data: Data to encode
            max_width: Maximum width (0 = no limit)
            max_height: Maximum height (0 = no limit)

        Returns:
            Tuple of (lines, width, height) where lines is list of strings
        """
        qr_string = self.render(data)
        lines = qr_string.split("\n")

        # Filter out empty lines
        lines = [l for l in lines if l.strip()]

        width = max(len(l) for l in lines) if lines else 0
        height = len(lines)

        # Truncate if needed
        if max_width > 0 and width > max_width:
            lines = [l[:max_width] for l in lines]
            width = max_width

        if max_height > 0 and height > max_height:
            lines = lines[:max_height]
            height = max_height

        return lines, width, height

    def get_dimensions(self, data: str) -> Tuple[int, int]:
        """Get the dimensions of the rendered QR code."""
        qr_string = self.render(data)
        lines = [l for l in qr_string.split("\n") if l.strip()]
        width = max(len(l) for l in lines) if lines else 0
        height = len(lines)
        return width, height

    def fits_terminal(self, data: str, margin: int = 4) -> bool:
        """Check if QR code fits in current terminal."""
        term_width, term_height = get_terminal_size()
        qr_width, qr_height = self.get_dimensions(data)
        return qr_width <= term_width - margin and qr_height <= term_height - margin

    def draw_curses(self, screen, data: str, start_y: int = 0, center: bool = True,
                    color_pair: int = 0, max_height: int = 0) -> int:
        """
        Draw QR code directly to a curses screen.

        Args:
            screen: curses window object
            data: Data to encode
            start_y: Starting Y position
            center: Whether to center horizontally
            color_pair: curses color pair to use
            max_height: Maximum height (0 = use screen height)

        Returns:
            Number of lines drawn
        """
        import curses

        try:
            h, w = screen.getmaxyx()
        except Exception:
            return 0

        if max_height <= 0:
            max_height = h - start_y - 2

        lines, qr_width, qr_height = self.render_for_curses(
            data, max_width=w - 2, max_height=max_height
        )

        drawn = 0
        for num, line in enumerate(lines):
            y = start_y + num
            if y >= h - 1:
                break

            if center:
                x = max(0, (w - len(line)) // 2)
            else:
                x = 0

            try:
                attr = curses.color_pair(color_pair) if color_pair else 0
                screen.addstr(y, x, line[:w-1], attr)
                drawn += 1
            except curses.error:
                pass

        return drawn


# Convenience functions

_default_display: Optional[QRDisplay] = None

def get_display() -> QRDisplay:
    """Get the default QRDisplay instance."""
    global _default_display
    if _default_display is None:
        _default_display = QRDisplay()
    return _default_display


def render_qr_code(data: str, mode: Optional[RenderMode] = None) -> str:
    """
    Convenience function to render a QR code.

    Args:
        data: Data to encode
        mode: Optional rendering mode (auto-detects if not specified)

    Returns:
        String representation of QR code
    """
    return get_display().render(data, mode)


def render_qr_for_curses(data: str, max_width: int = 0, max_height: int = 0) -> Tuple[list, int, int]:
    """
    Convenience function to render QR code for curses.

    Returns:
        Tuple of (lines, width, height)
    """
    return get_display().render_for_curses(data, max_width, max_height)


def draw_qr_curses(screen, data: str, start_y: int = 0, center: bool = True,
                   color_pair: int = 0, max_height: int = 0) -> int:
    """
    Draw QR code directly to a curses screen.

    Args:
        screen: curses window object
        data: Data to encode
        start_y: Starting Y position
        center: Whether to center horizontally
        color_pair: curses color pair to use
        max_height: Maximum height (0 = auto)

    Returns:
        Number of lines drawn
    """
    return get_display().draw_curses(screen, data, start_y, center, color_pair, max_height)


def check_qr_support() -> dict:
    """
    Check QR code support status.

    Returns:
        Dict with support information
    """
    display = get_display()
    libs = detect_available_libraries()

    return {
        "libraries": [lib.value for lib in libs.keys()],
        "best_mode": display.detect_best_mode().name,
        "has_unicode": display.has_unicode,
        "has_colors": display.has_colors,
        "is_tty": display.is_tty,
        "term_caps": str(display.term_caps) if display.term_caps else None,
    }


# For backwards compatibility with qr_auth.py
def generate_qr_code_ascii(data: str, border: int = 2) -> str:
    """
    Generate ASCII art QR code for terminal display.
    Backwards-compatible wrapper around QRDisplay.
    """
    config = QRConfig(border=border)
    display = QRDisplay(config)
    return display.render(data)


def generate_qr_code_simple(data: str) -> str:
    """
    Generate simple ASCII QR code for limited terminals.
    Backwards-compatible wrapper.
    """
    config = QRConfig(border=2, compact=False)
    display = QRDisplay(config)
    return display.render(data, mode=RenderMode.ASCII_BLOCK)
