"""
Emoji Library Compatibility Layer

Provides safe import and fallback handling for the emoji library.
If the emoji library is unavailable or outdated, this module provides
stub functions that gracefully degrade functionality.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Try to import the emoji library
try:
    import emoji as _emoji
    HAVE_EMOJI = True
    EMOJI_VERSION = getattr(_emoji, '__version__', 'unknown')
except ImportError:
    HAVE_EMOJI = False
    EMOJI_VERSION = None
    _emoji = None
    logger.warning("emoji library not installed - emoji features will be limited")
except Exception as e:
    HAVE_EMOJI = False
    EMOJI_VERSION = None
    _emoji = None
    logger.warning(f"emoji library failed to import: {e}")

# Minimum version check (emoji 2.0+ has different API)
EMOJI_V2_PLUS = False
if HAVE_EMOJI and EMOJI_VERSION:
    try:
        major = int(EMOJI_VERSION.split('.')[0])
        EMOJI_V2_PLUS = major >= 2
    except (ValueError, IndexError):
        pass


def demojize(text: str, delimiters: tuple = (":", ":")) -> str:
    """
    Convert emoji characters to text codes.

    Args:
        text: Text containing emoji
        delimiters: Tuple of (start, end) delimiters for codes

    Returns:
        Text with emoji converted to codes like :smile:
    """
    if not text:
        return text

    if not HAVE_EMOJI:
        # Fallback: return text unchanged
        return text

    try:
        if EMOJI_V2_PLUS:
            return _emoji.demojize(text, delimiters=delimiters)
        else:
            # Older API: normalize delimiters to match our API
            result = _emoji.demojize(text)

            # emoji<2.0 uses ":" as the default delimiter pair; if the caller
            # requested different delimiters, rewrite the codes so the public
            # API behaves consistently across emoji versions.
            default_delimiters = (":", ":")
            if delimiters != default_delimiters:
                start, end = delimiters

                # Prefer restricting replacements to known emoji shortcodes so that
                # we don't rewrite arbitrary :word: sequences that weren't produced
                # by emoji.demojize.
                alias_map = getattr(_emoji, "EMOJI_ALIAS_UNICODE_ENGLISH", None)

                if alias_map:
                    # EMOJI_ALIAS_UNICODE_ENGLISH keys are colon-wrapped shortcodes,
                    # e.g. ":smile:". Replace exactly those with the requested
                    # delimiters, preserving the inner shortcode text.
                    for shortcode in alias_map.keys():
                        if not (shortcode.startswith(":") and shortcode.endswith(":")):
                            continue
                        inner = shortcode[1:-1]
                        replacement = f"{start}{inner}{end}"
                        result = result.replace(shortcode, replacement)
                else:
                    # Fallback: we don't have the alias map, so broaden the allowed
                    # characters but still avoid matching nested colons/spaces.
                    # This will match any :...: without whitespace/colons inside.
                    result = re.sub(
                        r":([^:\s]+):",
                        lambda m: f"{start}{m.group(1)}{end}",
                        result,
                    )

            return result
    except Exception as e:
        logger.debug(f"demojize failed: {e}")
        return text


def emojize(text: str, language: str = "en", variant: str = None) -> str:
    """
    Convert text codes to emoji characters.

    Args:
        text: Text with codes like :smile:
        language: Language for emoji names (default "en", also supports "alias")
        variant: Emoji variant ("emoji_type" for emoji presentation)

    Returns:
        Text with codes converted to emoji
    """
    if not text:
        return text

    if not HAVE_EMOJI:
        # Fallback: return text unchanged
        return text

    try:
        if EMOJI_V2_PLUS:
            kwargs = {"language": language}
            if variant:
                kwargs["variant"] = variant
            return _emoji.emojize(text, **kwargs)
        else:
            # Older API doesn't support language/variant
            return _emoji.emojize(text)
    except Exception as e:
        logger.debug(f"emojize failed: {e}")
        return text


def is_emoji(char: str) -> bool:
    """
    Check if a character is an emoji.

    Args:
        char: Single character to check

    Returns:
        True if character is an emoji
    """
    if not char:
        return False

    if not HAVE_EMOJI:
        # Fallback: basic Unicode range check for common emoji
        code = ord(char[0]) if char else 0
        return (
            0x1F300 <= code <= 0x1F9FF or  # Misc Symbols, Emoticons, etc.
            0x2600 <= code <= 0x26FF or    # Misc Symbols
            0x2700 <= code <= 0x27BF or    # Dingbats
            0x1F600 <= code <= 0x1F64F or  # Emoticons
            0x1F680 <= code <= 0x1F6FF     # Transport/Map
        )

    try:
        if EMOJI_V2_PLUS:
            return _emoji.is_emoji(char)
        else:
            # Older API fallback
            demojized = _emoji.demojize(char)
            return demojized != char and demojized.startswith(':')
    except Exception:
        return False


def emoji_count(text: str) -> int:
    """
    Count number of emoji in text.

    Args:
        text: Text to count emoji in

    Returns:
        Number of emoji found
    """
    if not text:
        return 0

    if not HAVE_EMOJI:
        # Fallback: rough count using Unicode ranges
        count = 0
        for char in text:
            if is_emoji(char):
                count += 1
        return count

    try:
        if EMOJI_V2_PLUS:
            return _emoji.emoji_count(text)
        else:
            # Older API fallback
            demojized = _emoji.demojize(text)
            return len(re.findall(r':[a-zA-Z0-9_]+:', demojized))
    except Exception:
        return 0


def replace_emoji(text: str, replace: str = "") -> str:
    """
    Remove or replace all emoji in text.

    Args:
        text: Text containing emoji
        replace: Replacement string for each emoji

    Returns:
        Text with emoji replaced
    """
    if not text:
        return text

    if not HAVE_EMOJI:
        # Fallback: remove using Unicode ranges
        result = []
        for char in text:
            if not is_emoji(char):
                result.append(char)
            elif replace:
                result.append(replace)
        return ''.join(result)

    try:
        if EMOJI_V2_PLUS:
            return _emoji.replace_emoji(text, replace=replace)
        else:
            # Older API fallback: use demojize then strip codes
            demojized = _emoji.demojize(text)
            if replace:
                return re.sub(r':[a-zA-Z0-9_]+:', replace, demojized)
            else:
                return re.sub(r':[a-zA-Z0-9_]+:', '', demojized)
    except Exception:
        return text


def get_emoji_regexp():
    """
    Get compiled regex pattern for matching emoji.

    Returns:
        Compiled regex pattern or None if unavailable
    """
    if not HAVE_EMOJI:
        return None

    try:
        if EMOJI_V2_PLUS and hasattr(_emoji, 'get_emoji_regexp'):
            return _emoji.get_emoji_regexp()
        return None
    except Exception:
        return None


# Re-export the original emoji module for advanced usage
emoji = _emoji

# Re-export EMOJI_DATA for search functionality
# This is a dict mapping emoji characters to their metadata
if HAVE_EMOJI and hasattr(_emoji, 'EMOJI_DATA'):
    EMOJI_DATA = _emoji.EMOJI_DATA
else:
    # Empty fallback - search won't find emoji but won't crash
    EMOJI_DATA = {}


def check_emoji_support() -> dict:
    """
    Check emoji library support status.

    Returns:
        Dict with support information
    """
    return {
        "available": HAVE_EMOJI,
        "version": EMOJI_VERSION,
        "v2_api": EMOJI_V2_PLUS,
    }
