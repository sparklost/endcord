"""
Terminal Capability Detection Module

Provides robust detection and handling of terminal capabilities across:
- TTY (Linux virtual console)
- X11 terminals (xterm, urxvt, etc.)
- Wayland terminals (foot, alacritty, kitty)
- macOS Terminal.app and iTerm2
- Windows Terminal and legacy cmd
- SSH sessions
- tmux/screen multiplexers

This module centralizes all terminal capability detection to ensure
consistent behavior across diverse terminal environments.
"""

import curses
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum terminal dimensions for usable UI
MIN_TERMINAL_WIDTH = 60
MIN_TERMINAL_HEIGHT = 15

# Terminal types that support specific features
TERMS_WITH_256_COLOR = {
    "xterm-256color", "screen-256color", "tmux-256color",
    "rxvt-unicode-256color", "gnome-256color", "konsole-256color",
    "alacritty", "kitty", "foot", "wezterm", "contour",
}

TERMS_WITH_TRUE_COLOR = {
    "xterm-direct", "alacritty", "kitty", "foot", "wezterm",
    "contour", "iterm2", "vte-direct",
}

TERMS_WITH_MOUSE = {
    "xterm", "xterm-256color", "rxvt", "rxvt-unicode", "rxvt-unicode-256color",
    "screen", "screen-256color", "tmux", "tmux-256color",
    "alacritty", "kitty", "foot", "wezterm", "konsole", "gnome-256color",
    "iterm2", "apple-terminal",
}

TERMS_LIMITED = {
    "linux", "vt100", "vt102", "vt220", "ansi", "dumb",
}

TERMS_WITH_BRACKETED_PASTE = {
    "xterm", "xterm-256color", "screen", "screen-256color",
    "tmux", "tmux-256color", "alacritty", "kitty", "foot",
    "wezterm", "iterm2", "gnome-256color", "konsole",
}


@dataclass
class TerminalCapabilities:
    """Detected terminal capabilities"""
    term: str
    colors: int
    color_pairs: int
    has_mouse: bool
    has_unicode: bool
    has_wide_chars: bool
    has_bracketed_paste: bool
    has_true_color: bool
    is_tty: bool
    is_multiplexer: bool
    is_ssh: bool
    min_size_ok: bool
    width: int
    height: int
    recommended_escdelay: int

    def __str__(self) -> str:
        return (
            f"Terminal: {self.term}\n"
            f"  Colors: {self.colors}, Pairs: {self.color_pairs}\n"
            f"  Mouse: {self.has_mouse}, Unicode: {self.has_unicode}\n"
            f"  Bracketed Paste: {self.has_bracketed_paste}\n"
            f"  TTY: {self.is_tty}, Multiplexer: {self.is_multiplexer}, SSH: {self.is_ssh}\n"
            f"  Size: {self.width}x{self.height} (min ok: {self.min_size_ok})"
        )


def get_term() -> str:
    """Get the current TERM environment variable with fallbacks"""
    term = os.environ.get("TERM", "")
    if not term:
        if sys.platform == "win32":
            return "windows"
        return "unknown"
    return term.lower()


def is_tty() -> bool:
    """Check if running in a real TTY (Linux virtual console)"""
    term = get_term()
    if term == "linux":
        return True
    # Check if running on a virtual console
    tty = os.environ.get("XDG_VTNR")
    if tty:
        return True
    # Check /dev/tty path
    try:
        tty_path = os.ttyname(sys.stdout.fileno())
        if tty_path and "/tty" in tty_path and "/pts/" not in tty_path:
            return True
    except (OSError, AttributeError):
        pass
    return False


def is_multiplexer() -> bool:
    """Check if running inside tmux or screen"""
    if os.environ.get("TMUX"):
        return True
    if os.environ.get("STY"):  # screen session
        return True
    term = get_term()
    if term.startswith("screen") or term.startswith("tmux"):
        return True
    return False


def is_ssh() -> bool:
    """Check if running over SSH"""
    if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
        return True
    if os.environ.get("SSH_CONNECTION"):
        return True
    return False


def detect_color_support() -> tuple:
    """
    Detect color support level.

    Returns:
        tuple: (colors, supports_true_color)
    """
    term = get_term()

    # Check COLORTERM for true color
    colorterm = os.environ.get("COLORTERM", "").lower()
    has_true_color = colorterm in ("truecolor", "24bit") or term in TERMS_WITH_TRUE_COLOR

    # Check for 256 color support
    if term in TERMS_WITH_256_COLOR or "256color" in term:
        return 256, has_true_color

    # Limited terminals
    if term in TERMS_LIMITED:
        return 8, False

    # Windows
    if sys.platform == "win32":
        # Windows Terminal supports 256, cmd.exe supports 16
        if os.environ.get("WT_SESSION"):
            return 256, True
        return 16, False

    # Default to 256 for modern terminals
    if term and term not in ("dumb", "unknown"):
        return 256, has_true_color

    return 8, False


def detect_mouse_support() -> bool:
    """Detect if terminal supports mouse input"""
    term = get_term()

    # TTY never supports mouse
    if is_tty():
        return False

    # Check known terminals
    for supported_term in TERMS_WITH_MOUSE:
        if term.startswith(supported_term):
            return True

    # Windows Terminal supports mouse
    if sys.platform == "win32" and os.environ.get("WT_SESSION"):
        return True

    # SSH without X forwarding typically doesn't support mouse well
    if is_ssh() and not os.environ.get("DISPLAY"):
        return False

    # Default to True for unknown modern terminals
    return term not in TERMS_LIMITED


def detect_unicode_support() -> tuple:
    """
    Detect unicode and wide character support.

    Returns:
        tuple: (has_unicode, has_wide_chars)
    """
    term = get_term()

    # Check locale with standard precedence:
    # LC_ALL > LC_CTYPE > LANG
    lang = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_CTYPE")
        or os.environ.get("LANG")
        or ""
    )
    has_utf8_locale = "utf-8" in lang.lower() or "utf8" in lang.lower()

    # TTY has limited unicode
    if is_tty():
        return False, False

    # Limited terminals
    if term in TERMS_LIMITED:
        return False, False

    # Windows cmd.exe has limited unicode
    if sys.platform == "win32" and not os.environ.get("WT_SESSION"):
        return has_utf8_locale, False

    # Modern terminals with UTF-8 locale
    return has_utf8_locale, has_utf8_locale


def detect_bracketed_paste_support() -> bool:
    """Detect if terminal supports bracketed paste mode"""
    term = get_term()

    # TTY doesn't support bracketed paste
    if is_tty():
        return False

    # Limited terminals
    if term in TERMS_LIMITED:
        return False

    # Check known terminals
    for supported_term in TERMS_WITH_BRACKETED_PASTE:
        if term.startswith(supported_term):
            return True

    # Multiplexers support it if the outer terminal does
    if is_multiplexer():
        return True

    # Windows Terminal supports it
    if sys.platform == "win32" and os.environ.get("WT_SESSION"):
        return True

    return False


def get_recommended_escdelay() -> int:
    """
    Get recommended ESCDELAY value for current environment.

    Lower values = faster escape key response but may break escape sequences.
    Higher values = more reliable but slower escape key.
    """
    # SSH needs higher delay due to network latency
    if is_ssh():
        return 100

    # Multiplexers add processing overhead
    if is_multiplexer():
        return 50

    # TTY can be slow
    if is_tty():
        return 100

    # Local modern terminals can be fast
    return 25


def get_optimal_term() -> str:
    """
    Get the optimal TERM value for current environment.

    Upgrades basic TERM values to 256-color variants when appropriate.
    """
    term = get_term()
    colors, _ = detect_color_support()

    # Don't modify if already 256-color
    if "256color" in term:
        return term

    # Don't upgrade limited terminals
    if term in TERMS_LIMITED:
        return term

    # Upgrade mappings
    upgrades = {
        "xterm": "xterm-256color",
        "screen": "screen-256color",
        "tmux": "tmux-256color",
        "rxvt-unicode": "rxvt-unicode-256color",
        "gnome": "gnome-256color",
    }

    if term in upgrades and colors >= 256:
        return upgrades[term]

    return term


def get_terminal_size() -> tuple:
    """Get terminal dimensions with fallbacks"""
    try:
        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines
    except (ValueError, OSError):
        return 80, 24


def detect_capabilities() -> TerminalCapabilities:
    """
    Detect all terminal capabilities.

    This is the main entry point for capability detection.
    Should be called early in application startup.
    """
    term = get_term()
    colors, has_true_color = detect_color_support()
    has_unicode, has_wide_chars = detect_unicode_support()
    width, height = get_terminal_size()

    # Calculate color pairs (curses limit)
    # Most terminals support at least 256 pairs
    color_pairs = min(colors * colors, 32767)

    return TerminalCapabilities(
        term=term,
        colors=colors,
        color_pairs=color_pairs,
        has_mouse=detect_mouse_support(),
        has_unicode=has_unicode,
        has_wide_chars=has_wide_chars,
        has_bracketed_paste=detect_bracketed_paste_support(),
        has_true_color=has_true_color,
        is_tty=is_tty(),
        is_multiplexer=is_multiplexer(),
        is_ssh=is_ssh(),
        min_size_ok=(width >= MIN_TERMINAL_WIDTH and height >= MIN_TERMINAL_HEIGHT),
        width=width,
        height=height,
        recommended_escdelay=get_recommended_escdelay(),
    )


def apply_environment_fixes(caps: Optional[TerminalCapabilities] = None) -> TerminalCapabilities:
    """
    Apply environment variable fixes based on detected capabilities.

    This modifies os.environ to optimize terminal behavior.
    Returns updated capabilities that reflect any environment changes made.
    """
    if caps is None:
        caps = detect_capabilities()

    # Set optimal TERM first - this may change capabilities
    optimal_term = get_optimal_term()
    if optimal_term != caps.term:
        os.environ["TERM"] = optimal_term
        logger.info(f"Upgraded TERM: {caps.term} -> {optimal_term}")
        # Re-detect capabilities immediately after TERM change
        # so subsequent env fixes use the updated capabilities
        caps = detect_capabilities()

    # Set ESCDELAY based on (potentially updated) capabilities
    os.environ["ESCDELAY"] = str(caps.recommended_escdelay)

    # Ensure UTF-8 locale for unicode support based on (potentially updated) capabilities
    if caps.has_unicode and not os.environ.get("LANG"):
        os.environ["LANG"] = "en_US.UTF-8"

    return caps


def check_minimum_requirements(caps: TerminalCapabilities) -> tuple:
    """
    Check if terminal meets minimum requirements.

    Returns:
        tuple: (ok, list of warnings)
    """
    warnings = []
    ok = True

    if not caps.min_size_ok:
        warnings.append(
            f"Terminal too small: {caps.width}x{caps.height} "
            f"(minimum: {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT})"
        )
        ok = False

    if caps.is_tty:
        warnings.append(
            "Running in TTY mode - limited colors, no mouse, no emoji. "
            "Consider setting emoji_as_text=True in config."
        )

    if caps.colors < 16:
        warnings.append(f"Limited color support ({caps.colors} colors)")

    if not caps.has_unicode:
        warnings.append("Unicode not supported - some characters may not display correctly")

    return ok, warnings


def get_safe_config_overrides(caps: TerminalCapabilities) -> dict:
    """
    Get configuration overrides for safe operation on current terminal.

    Returns dict of config keys to override for safety.
    """
    overrides = {}

    if caps.is_tty or not caps.has_unicode:
        overrides["emoji_as_text"] = True

    if not caps.has_mouse:
        overrides["mouse"] = False

    if caps.colors <= 8:
        # Force simple theme for limited colors
        overrides["use_simple_theme"] = True

    return overrides


# Curses initialization helpers

def safe_curs_set(visibility: int, caps: Optional[TerminalCapabilities] = None) -> bool:
    """
    Safely set cursor visibility, returns True if successful.

    Args:
        visibility: 0=invisible, 1=normal, 2=very visible
        caps: Optional pre-detected capabilities (for consistency, not currently used)
    """
    try:
        curses.curs_set(visibility)
        return True
    except curses.error:
        return False


def safe_mousemask(mask: int, caps: Optional[TerminalCapabilities] = None) -> int:
    """
    Safely enable mouse with capability checking.

    Args:
        mask: Mouse event mask (e.g. curses.ALL_MOUSE_EVENTS)
        caps: Optional pre-detected capabilities to check mouse support

    Returns the actual mask that was set.
    """
    if caps and not caps.has_mouse:
        return 0

    try:
        old_mask = curses.mousemask(mask)
        return old_mask[0] if isinstance(old_mask, tuple) else old_mask
    except curses.error:
        return 0


def safe_start_color(caps: Optional[TerminalCapabilities] = None) -> bool:
    """
    Safely initialize color support.

    Args:
        caps: Optional pre-detected capabilities to check color support

    Returns True if color was initialized successfully.
    """
    # Skip if capabilities indicate no color support
    if caps and caps.colors <= 0:
        return False

    try:
        curses.start_color()
        curses.use_default_colors()
        return True
    except curses.error:
        return False


def enable_bracketed_paste(caps: Optional[TerminalCapabilities] = None) -> bool:
    """
    Enable bracketed paste mode if supported.

    Returns True if enabled.
    """
    if caps and not caps.has_bracketed_paste:
        return False

    try:
        # Send escape sequence to enable bracketed paste
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
        return True
    except (IOError, OSError):
        return False


def disable_bracketed_paste() -> None:
    """Disable bracketed paste mode"""
    try:
        sys.stdout.write("\x1b[?2004l")
        sys.stdout.flush()
    except (IOError, OSError):
        pass


# Debug utilities

def print_capabilities(caps: Optional[TerminalCapabilities] = None) -> None:
    """Print detected capabilities for debugging"""
    if caps is None:
        caps = detect_capabilities()

    print("=" * 50)
    print("TERMINAL CAPABILITY DETECTION")
    print("=" * 50)
    print(caps)
    print()

    ok, warnings = check_minimum_requirements(caps)
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("No warnings - terminal fully supported")

    print()
    print("Recommended config overrides:")
    overrides = get_safe_config_overrides(caps)
    if overrides:
        for k, v in overrides.items():
            print(f"  {k} = {v}")
    else:
        print("  (none needed)")
    print("=" * 50)


if __name__ == "__main__":
    # Run capability detection when executed directly
    caps = detect_capabilities()
    print_capabilities(caps)
