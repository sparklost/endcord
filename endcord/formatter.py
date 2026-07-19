# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import curses
import logging
import re
import shutil
import time
from bisect import bisect_left
from datetime import UTC, datetime
from itertools import chain

from endcord import peripherals, utils
from endcord.wide_ranges import WIDE_RANGES

logger = logging.getLogger(__name__)
try:
    import __main__
    APP_NAME = __main__.APP_NAME   # set in main.py
except (AttributeError, NameError):
    APP_NAME = "endcord"

DAY_MS = 24 * 60 * 60 * 1000
DISCORD_EPOCH_MS = 1420070400000
TREE_EMOJI_REPLACE = "▮"
TIME_DIVS = [1, 60, 3600, 86400, 2678400, 31190400]
TIME_UNITS = ["second", "minute", "hour", "day", "month", "year"]
SPLIT_AFTER_TIME = 10 * 60
ALT_SPACE = "⠀"   # U+2800 - braille pattern blank
MIN_TAB_LEN = 8

ACTIVITY_VERBS = ("Playing", "Streaming", "Listening to", "Watching", "Competing in")
LOG_LELVELS = ("INFO", "DEBUG", "WARNING", "ERROR", "FATAL")

match_d_emoji = re.compile(r"<(a?):(.+):(\d+)>")
match_mention = re.compile(r"<@(\d+)>")
match_role = re.compile(r"<@&(\d+)>")
match_channel = re.compile(r"<#([\d\/]+)>")
match_timestamp = re.compile(r"<t:(\d+)(:[tTdDfFsSR])?>")
match_escaped_md = re.compile(r"\\(?=[^a-zA-Z\d\s])")
match_md_spoiler = re.compile(r"(?<!\\)\|\|.+?\|\|")
match_md_code_snippet = re.compile(r"(?<!`|\\)`[^`]+`")
match_md_code_block = re.compile(r"(?s)```(?:([a-zA-Z0-9+#-]+)?[ \t]*\n)?(.*?)```")
match_md_url = re.compile(r"(?<!\\)\[([^\]]+)\]\(([^)]+)\)")
match_url = re.compile(r"https?:\/\/[\w.-]+(\.[\w-])+[^\s)\]>]*[^\s).\]>]")
match_discord_channel_url = re.compile(r"https:\/\/discord(?:app)?\.com\/channels\/(\d*)\/(\d*)(?:\/(\d*))?")
match_sticker_id = re.compile(r"<;\d+;>")
match_md_all = re.compile(
    r"""
    (?<!\\)(
        (__[^_]+__)          |   # underline
        (\*\*[^\*]+\*\*)     |   # bold
        # (~~[^~]+~~)        |   # strikethrough - unused
        (?<!\w)_[^_]+_(?!\w) |   # italic _
        (\*[^\*\n]+\*)           # italic * (no newline)
    )
    """,
    re.VERBOSE,
)
MARKER = "\uEE42"  # character from private use area


def ceil(x):
    """To avoid importing math.ceil"""
    int_part = int(x)
    if x > int_part:
        return int_part + 1
    return int_part


def lazy_replace(text, key, value_function):
    """Replace key in text with result from value_function, but run it only if key is found"""
    if key in text:
        text = text.replace(key, value_function())
    return text


def trim_string(input_string, max_length):
    """If string is too long, trim it and append '...' so returned string is not longer than max_length"""
    if len(input_string) > max_length:
        return input_string[:max_length - 3] + "..."
    return input_string


def normalize_int_str(input_int, digits_limit):
    """Convert integer to string and limit its value to preferred number of digits"""
    int_str = str(min(input_int, 10**digits_limit - 1))
    while len(int_str) < digits_limit:
        int_str = " " + int_str
    return int_str


def demojize(text):
    """Safely demojize string"""
    if not text:
        return text
    return utils.demojize(text)


def demojize_message(message):
    """Safely demojize message"""
    message["content"] = demojize(message["content"])
    message["username"] = demojize(message["username"])
    message["global_name"] = demojize(message.get("global_name"))
    if message["referenced_message"]:
        referenced = message["referenced_message"]
        referenced["content"] = demojize(referenced["content"])
        referenced["username"] = demojize(referenced.get("username"))
        referenced["global_name"] = demojize(referenced.get("global_name"))
    for embed in message["embeds"]:
        if embed["type"] == "rich":
            embed["url"] = demojize(embed["url"])
    return message


def is_substring_before(main_string, substring_1, substring_2):
    """Check if substring_1 is before substring_2"""
    index_1 = main_string.find(substring_1)
    index_2 = main_string.find(substring_2)
    return index_1 != -1 and index_2 != -1 and index_1 < index_2


def discord_timestamp(unix_time, timezone=True):
    """Generate discord timestamp from unix time"""
    time_obj = datetime.fromtimestamp(unix_time)
    if timezone:
        time_obj = time_obj.astimezone()
    return datetime.strftime(time_obj, "%Y-%m-%dT%H:%M:%S.%f%z")


def generate_timestamp(discord_time, format_string, timezone=True):
    """Convert discord timestamp string to formatted string and optionally convert to current timezone"""
    try:
        time_obj = datetime.strptime(discord_time, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        time_obj = datetime.strptime(discord_time, "%Y-%m-%dT%H:%M:%S%z")   # edge case
    if timezone:
        time_obj = time_obj.astimezone()
    return datetime.strftime(time_obj, format_string)


def unix_from_snowflake(snowflake):
    """Convert discord snowflake to unix time"""
    return int(((int(snowflake) >> 22) + DISCORD_EPOCH_MS) / 1000)


def timestamp_from_snowflake(snowflake, format_string, timezone=True):
    """Convert discord snowflake to formatted string and optionally convert to current timezone"""
    time_obj = datetime.fromtimestamp(((int(snowflake) >> 22) + DISCORD_EPOCH_MS) / 1000, UTC)
    if timezone:
        time_obj = time_obj.astimezone()
    return datetime.strftime(time_obj, format_string)


def day_from_snowflake(snowflake, timezone=True):
    """Extract day from discord snowflake with optional timezone conversion"""
    snowflake = int(snowflake)
    if timezone:
        time_obj = datetime.fromtimestamp(((snowflake >> 22) + DISCORD_EPOCH_MS) / 1000, UTC)
        time_obj = time_obj.astimezone()
        return time_obj.day
    # faster than datetime, but no timezone conversion
    return ((snowflake >> 22) + DISCORD_EPOCH_MS) / DAY_MS


def generate_relative_time(timestamp):
    """Generate relative time string"""
    now = time.time()
    diff = abs(now - timestamp)
    ago = now > timestamp
    for num, time_div in enumerate(TIME_DIVS[1:]):
        if diff < time_div:
            break
    rel_time = int(diff / TIME_DIVS[num])
    time_unit = TIME_UNITS[num]
    plural = "s" if rel_time > 1 else ""
    if ago:
        time_string = f"{rel_time} {time_unit}{plural} ago"
    else:
        time_string = f"in {rel_time} {time_unit}{plural}"
    return time_string


def format_seconds(seconds, nice=False, pad=True):
    """Convert seconds to hh:mm:ss or HHh MMm SSs"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    fmt = "02d" if pad else "d"
    if hours:
        parts.append(f"{hours:{fmt}}" + ("h" if nice else ""))
    if minutes or hours:
        if pad:
            parts.append(f"{minutes:{fmt}}" + ("m" if nice else ""))
    parts.append(f"{secs:{fmt}}" + ("s" if nice else ""))
    if nice:
        return " ".join(parts)
    return ":".join(parts)



def generate_discord_timestamp(timestamp, discord_format, timezone=True):
    """Generate discord formatted timestamp"""
    if discord_format == "R":
        return generate_relative_time(int(timestamp))
    time_obj = datetime.fromtimestamp(int(timestamp), UTC)
    if timezone:
        time_obj = time_obj.astimezone()
    if discord_format == "t":
        format_string = "%H:%M"
    elif discord_format == "T":
        format_string = "%H:%M:%S"
    elif discord_format == "d":
        format_string = "%d/%m/%Y"
    elif discord_format == "D":
        format_string = "%d %b %Y"
    elif discord_format == "F":
        format_string = "%A, %d %b %Y %H:%M"
    elif discord_format == "s":
        format_string = "d/%m/%Y %H:%M"
    elif discord_format == "S":
        format_string = "d/%m/%Y %H:%M:%S"
    else:   # "f"
        format_string = "%d %b %Y %H:%M"
    return datetime.strftime(time_obj, format_string)


def find_timestamp(full_string, timestamp):
    """Return timestamp indexes of a matching timestamp"""
    start_index = full_string.find(timestamp)
    if start_index == -1:
        return None
    end_index = start_index + len(timestamp) - 1
    return start_index, end_index


def format_kilo(n, dec=1):
    """Convert integer to string with K suffix"""
    if n < 1000:
        return str(n)
    value = n / 1000
    if n > 10000:
        dec = 0
    s = f"{value:.{dec}f}".rstrip("0").rstrip(".")
    return f"{s}K"


def move_by_indexes(indexes, *ranges_lists):
    """Move format by indexes"""
    for ranges in ranges_lists:
        for format_part in ranges:
            for index in indexes:
                if index < format_part[0]:
                    format_part[0] -= 1
                if index < format_part[1]:
                    format_part[1] -= 1
                else:
                    break


def emoji_name(emoji_char):
    """Return emoji name from its Unicode"""
    return utils.demojize(emoji_char).replace(":", "")


def replace_wide(text, replacement=TREE_EMOJI_REPLACE):
    """Replace all wide characters in string with given character"""
    if not text:
        return ""
    new_text = []
    for i, ch in enumerate(text):
        character = ord(ch)
        if 0x20 <= character < 0x7f:
            new_text.append(ch)
        elif binary_search(character, WIDE_RANGES):
            new_text.append(replacement)
        else:
            new_text.append(ch)
    return "".join(new_text)


def binary_search(codepoint, ranges):
    """Binary-search a sorted tuple of (start, end) ranges and return 1 if codepoint is inside any range, else 0"""
    low = 0
    high = len(ranges) - 1

    if codepoint < ranges[0][0] or codepoint > ranges[high][1]:
        return 0

    while low <= high:
        mid = (low + high) >> 1
        if codepoint < ranges[mid][0]:
            high = mid - 1
        else:
            low = mid + 1

    return high >= 0 and codepoint <= ranges[high][1]


def limit_width_wch(text, max_width):
    """Limit width of the text on the screen, because "wide characters" are 2 characters wide"""
    total_width = 0
    for i, ch in enumerate(text):
        codepoint = ord(ch)
        if 0x20 <= codepoint < 0x7f:
            char_width = 1
        else:
            char_width = 1 + binary_search(codepoint, WIDE_RANGES)
        if total_width + char_width > max_width:
            return text[:i], total_width
        total_width += char_width
    return text, total_width


def len_wch(text):
    """Return real display width for a string"""
    total_width = 0
    for ch in text:
        codepoint = ord(ch)
        if 0x20 <= codepoint < 0x7f:
            total_width += 1
        else:
            total_width += 1 + binary_search(codepoint, WIDE_RANGES)
    return total_width


def split_index_wch(text, max_width):
    """Get split index for string with wide characters"""
    width = 0
    for i, ch in enumerate(text):
        codepoint = ord(ch)
        if 0x20 <= codepoint < 0x7f:
            w = 1
        else:
            w = 1 + binary_search(codepoint, WIDE_RANGES)
        if width + w > max_width:
            return i
        width += w
    return len(text)


def fix_line_format_py(line_format, text):
    """Fix line format ranges for wide character positions"""
    if len(line_format) <= 1:
        return line_format

    wide_positions = []
    for i, ch in enumerate(text):
        codepoint = ord(ch)
        if (codepoint < 0x20 or codepoint >= 0x7f) and binary_search(codepoint, WIDE_RANGES):
            wide_positions.append(i)
    if not wide_positions:
        return line_format

    corrected = [line_format[0]]
    for color, start, end in line_format[1:]:
        start_shift = bisect_left(wide_positions, start)
        end_shift = bisect_left(wide_positions, end)
        corrected.append((color, start + start_shift, end + end_shift))

    return corrected


def fix_line_format_extended(line_format, text):
    """Fix extended line format ranges for wide character positions"""
    if len(line_format) <= 1:
        return line_format

    wide_positions = []
    for i, ch in enumerate(text):
        codepoint = ord(ch)
        if (codepoint < 0x20 or codepoint >= 0x7f) and binary_search(codepoint, WIDE_RANGES):
            wide_positions.append(i)
    if not wide_positions:
        return line_format

    corrected = []
    for color, attr, start, end in line_format:
        start_shift = bisect_left(wide_positions, start)
        end_shift = bisect_left(wide_positions, end)
        corrected.append((color, attr, start + start_shift, end + end_shift))

    return corrected


def fix_map_ranges(map_ranges, text):
    """Fix chat map ranges for wide character positions"""
    if not map_ranges:
        return map_ranges

    wide_positions = []
    for i, ch in enumerate(text):
        codepoint = ord(ch)
        if (codepoint < 0x20 or codepoint >= 0x7f) and binary_search(codepoint, WIDE_RANGES):
            wide_positions.append(i)
    if not wide_positions:
        return map_ranges

    corrected = []
    for start, end, data in map_ranges:
        start_shift = bisect_left(wide_positions, start)
        end_shift = bisect_left(wide_positions, end)
        corrected.append((start + start_shift, end + end_shift, data))

    return corrected


# use cython if available, ~6 times faster
try:
    from endcord_cython.formatter import (
        fix_line_format,
        fix_map_ranges,
        init_wide_ranges,
        len_wch,
        limit_width_wch,
        replace_wide,
        split_index_wch,
    )
    init_wide_ranges(WIDE_RANGES)
except ImportError:
    fix_line_format = fix_line_format_py


def normalize_string(input_string, max_length, emoji_safe=False, dots=False, fill=True):
    """
    Normalize length of string, by cropping it or appending spaces.
    Set max_length to None to disable.
    """
    input_string = str(input_string)
    if not max_length:
        return input_string
    if dots:
        dots = len(input_string) > max_length
    if emoji_safe:
        input_string, length = limit_width_wch(input_string, max_length)
        if fill:
            input_string += " " * (max_length - length)
        if dots:
            return input_string[:-3] + "..."
        return input_string
    if fill:
        input_string += " " * (max_length - len(input_string))
    if len(input_string) > max_length:
        if dots:
            return input_string[:-3] + " " * (len_wch(input_string[-3:]) - 3) + "..."
        return input_string[:max_length]
    return input_string


def normalize_string_with_suffix(input_string, suffix, max_length, emoji_safe=False):
    """
    Normalize length of string and add suffix to its end, by cropping it or appending spaces.
    Set max_length to None to disable.
    """
    input_string = str(input_string)
    suffix = str(suffix)
    if not max_length:
        return input_string + suffix
    input_width = max_length - len(suffix)
    if input_width <= 0:
        return suffix[:max_length]
    dots = len(input_string) > input_width
    if emoji_safe:
        input_string, length = limit_width_wch(input_string, input_width)
        input_string += " " * (input_width - length)
        if dots:
            return input_string[:-3] + "..." + suffix
        return input_string + suffix
    input_string += " " * (input_width - len(input_string))
    if len(input_string) > input_width:
        if dots:
            return input_string[:-3] + " " * (len_wch(input_string[-3:]) - 3) + "..." + suffix
        return input_string[:input_width] + suffix
    return input_string + suffix


def shift_ranges(ranges_lists, index, diff):
    """Range shifter for chained replace_ functions"""
    if not diff:
        return
    for ranges in ranges_lists:
        for format_range in ranges:
            if format_range[0] >= index:
                format_range[0] += diff
                format_range[1] += diff


def shift_ranges_all(diff, *ranges_lists):
    """Shifter for chat format ranges"""
    if not diff:
        return
    for ranges in ranges_lists:
        for format_range in ranges:
            format_range[0] += diff
            format_range[1] += diff


def delete_ranges(outer_ranges, *ranges_lists):
    """Delete all format ranges that are inside any of outer ranges"""
    if not outer_ranges:
        return
    for ranges in ranges_lists:
        for i in range(len(ranges) - 1, -1, -1):
            format_range = ranges[i]
            start = format_range[0]
            end = format_range[1]
            for outer_start, outer_end, *_ in outer_ranges:
                if end < outer_start:
                    break
                if start >= outer_start and end <= outer_end:
                    del ranges[i]
                    break


def shift_formats(formats, index, diff, skip=1):
    """Shift formats for one chat element after specified index, creates copy of initial formats if there is diff"""
    if not diff:
        return formats
    new_formats = []
    for num, chat_format in enumerate(formats):
        new_chat_format = chat_format[:]
        if num >= skip and new_chat_format[1] >= index:
            new_chat_format[1] += diff
            new_chat_format[2] += diff
        new_formats.append(new_chat_format)
    return new_formats


def replace_formatted(text, formats, key, replacement, color=None, attr=None):
    """Replaces a key in text and updates existing format indices (for 4 element format!)"""
    start_idx = text.find(key)
    if start_idx == -1:
        return text, formats
    replacement_len = len(replacement)
    shift = replacement_len - len(key)
    new_text = text.replace(key, replacement, 1)
    new_formats = []
    for color_f, attr_f, start, end in formats:   # shift formats
        if end <= start_idx:
            new_formats.append([color_f, attr_f, start, end])
        else:
            new_formats.append([color_f, attr_f, start + shift, end + shift])
    if replacement and (color is not None or attr is not None):
        new_formats.append([color, attr, start_idx, start_idx + replacement_len])
    return new_text, new_formats


def replace_discord_emoji(text, placeholder=None, *ranges_lists):
    """
    Transform emoji strings into nicer looking ones:
    `<:emoji_name:emoji_id>` --> `:emoji_name:`
    """
    result = []
    emoji_ranges = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_d_emoji, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        if placeholder:
            new_text = placeholder
        else:
            new_text = f":{match.group(2)}:"
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        emoji_ranges.append([new_start, new_end, match.group(3)])

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, new_start, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), emoji_ranges


def replace_mentions(text, usernames_ids, *ranges_lists, global_name=False, use_nick=False):
    """
    Transforms mention string into nicer looking one:
    `<@user_id>` --> `@username`
    """
    result = []
    mention_ranges = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_mention, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        user_id = match.group(1)
        for user in usernames_ids:
            if user_id == user["id"]:
                new_text = f"@{get_global_name(user, use_nick) if global_name else user["username"]}"
                break
        else:
            new_text = match.group(0)
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        mention_ranges.append([new_start, new_end, user_id])

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, new_start, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), mention_ranges


def replace_roles(text, roles_ids, *ranges_lists):
    """
    Transforms roles string into nicer looking one:
    `<@role_id>` --> `@role_name`
    And shifts ranges for other range lists.
    """
    result = []
    role_ranges = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_role, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        for role in roles_ids:
            if match.group(1) == role["id"]:
                new_text = f"@{role["name"]}"
                break
        else:
            new_text = match.group(0)
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        role_ranges.append([new_start, new_end, None])   # dont mix role and user ids

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, new_start, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), role_ranges


def replace_discord_url(text, *ranges_lists):
    """Replace discord url for channel and message and shift ranges for other range lists."""
    result = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_discord_channel_url, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        if match.group(3):
            new_text = f"<#{match.group(2)}/{match.group(3)}>>MSG"
        else:
            new_text = f"<#{match.group(2)}>"
        result.append(new_text)
        # range is added in replace_channels()

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, start + offset, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result)


def replace_channels(text, channels_ids, *ranges_lists):
    """
    Transforms channels string into nicer looking one:
    `<#channel_id>` --> `#channel_name`
    And shifts ranges for other range lists.
    """
    result = []
    channel_ranges = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_channel, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        full_id = match.group(1)
        if "/" in full_id:
            channel_id = full_id.split("/")[0]
        else:
            channel_id = full_id
        for channel in channels_ids:
            if channel_id == channel["id"]:
                new_text = "#" + channel["name"]
                break
        else:
            new_text = "#unknown"
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        channel_ranges.append([new_start, new_end, full_id])

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, new_start, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), channel_ranges


def replace_timestamps(text, timezone, *ranges_lists):
    """
    Transforms timestamp string into nicer looking one:
    `<t:timestamp:type>` --> discord specified format
    And shifts ranges for other range lists.
    """
    result = []
    timestamp_ranges = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_timestamp, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        timestamp = match.group(1)
        discord_format = match.group(2)
        if discord_format:
            discord_format = discord_format[1]
        new_text = generate_discord_timestamp(timestamp, discord_format, timezone=timezone)
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        timestamp_ranges.append([new_start, new_end])

        difference = len(new_text) - (end - start)
        if difference != 0:
            shift_ranges(ranges_lists, new_start, difference)
        offset += difference
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), timestamp_ranges


def replace_code_snippets(text, *ranges_lists):
    """Remove ` around code snippet"""
    result = []
    code_snippet_ranges = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_md_code_snippet, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        new_text = match.group(0)[1:-1]
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        code_snippet_ranges.append([new_start, new_end])

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, new_start, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), code_snippet_ranges


def replace_code_blocks(text, *ranges_lists):
    """Remove ``` around code block, and extract code language"""
    result = []
    code_snippet_ranges = []
    last_pos = 0
    offset = 0
    for match in re.finditer(match_md_code_block, text):
        start, end = match.span()
        result.append(text[last_pos:start])
        new_text = match.group(2)
        new_text = new_text.removeprefix("\n")
        lang = match.group(1)
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        code_snippet_ranges.append([new_start, new_end, lang])

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, new_start, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), code_snippet_ranges


def replace_bracketed_urls(text):
    """Find <https://...> and remove < > from it"""
    result = []
    last_pos = 0
    pos = 0
    while True:
        start = text.find("<https://", pos)
        if start == -1 or text[start-1] == "\\":
            break
        end = text.find(">", start)
        if end == -1:
            pos = start + 1
            continue
        result.append(text[last_pos:start])
        result.append(text[start + 1 : end])
        last_pos = end + 1
        pos = last_pos
    result.append(text[last_pos:])
    return "".join(result)


def replace_markdown_urls(text, except_ranges, *ranges_lists):
    """Replace [text](url) and [text](<url>) with text and return it as url range"""
    result = []
    url_ranges = []
    last_pos = 0
    offset = 0

    for match in re.finditer(match_md_url, text):
        start, end = match.span()
        skip = False
        for except_range in chain(*except_ranges):
            start_r = except_range[0]
            end_r = except_range[1]
            if start > start_r and start < end_r and end > start_r and end < end_r:
                skip = True
                break
        if skip:
            continue
        new_text = match.group(1)
        url = match.group(2).strip("<>")
        if not match_url.match(url):   # verify url
            continue
        result.append(text[last_pos:start])
        result.append(new_text)

        new_start = start + offset
        new_end = new_start + len(new_text)
        url_ranges.append([new_start, new_end, url])

        diff = len(new_text) - (end - start)
        if diff != 0:
            shift_ranges(ranges_lists, new_start, diff)
        offset += diff
        last_pos = end

    result.append(text[last_pos:])
    return "".join(result), url_ranges


def replace_spoilers(line):
    """Replace spoiler: ||content|| with ACS_BOARD characters"""
    for _ in range(20):   # safety limit
        string_match = re.search(match_md_spoiler, line)
        if not string_match:
            break
        start = string_match.start()
        end = string_match.end()
        line = line[:start] + "▒" * (end - start) + line[end:]
    return line


def replace_escaped_md(line, except_ranges=()):
    r"""
    Replace escaped markdown characters.
    eg "\:" --> ":"
    """
    indexes = []
    corr = 0
    for match in re.finditer(match_escaped_md, line):
        start, end = match.span()
        skip = False
        for except_range in chain(*except_ranges):
            start_r = except_range[0]
            end_r = except_range[1]
            if start > start_r and start < end_r and end > start_r and end < end_r:
                skip = True
                break
        if not skip:
            line = line[:start-corr] + line[end-corr:]
            indexes.append(start-corr)
            corr += 1
    return line, indexes


def is_unseen(read_state):
    """Check if given read state is unseen"""
    if not read_state:
        return False
    last_acked_message_id = read_state["last_acked_message_id"]
    if last_acked_message_id is None:
        return False
    last_message_id = read_state["last_message_id"]
    if not last_message_id or int(last_acked_message_id) < int(last_message_id):
        return True
    return False


def generate_count(count):
    """Generate mention count string"""
    if not count:
        return ""
    if count < 100:
        return f" ({count})"
    return " (99+)"


def format_md_all(line, content_start, except_ranges=()):
    """
    Replace all supported formatted markdown strings and return list of their formats.
    This should be called only after curses has initialized color.
    Strikethrough is apparently not supported by curses.
    Formatting is not performed inside except_ranges.
    """
    line_format = []
    indexes = []
    for _ in range(20):   # safety limit
        line_content = line[content_start:]
        string_match = re.search(match_md_all, line_content)
        if not string_match:
            break

        if string_match.group(2):   # underline
            attribute = curses.A_UNDERLINE
            format_len = 2
        elif string_match.group(3):   # bold
            attribute = curses.A_BOLD
            format_len = 2
        else:   # italic
            attribute = curses.A_ITALIC
            format_len = 1

        start = string_match.start() + content_start
        end = string_match.end() + content_start
        skip = False
        for except_range in chain(*except_ranges):
            start_r = except_range[0]
            end_r = except_range[1]
            # if this match is inside excepted range
            if (start >= start_r and start <= end_r) or (end > start_r and end < end_r):
                skip = True
                break
        if skip:
            continue
        text = string_match.group(0)[format_len:-format_len]
        line = line[:start] + text + line[end:]
        true_end = end - 2 * format_len

        # keep indexes of changes
        indexes.extend((start, true_end))
        if format_len == 2:
            indexes.extend((start+1, end - 3))

        # rearrange formats at indexes after this format index
        done = False
        for format_part in line_format:
            if format_part[0] > start:
                format_part[0] -= format_len
                format_part[1] -= format_len
            if format_part[1] >= end:
                format_part[1] -= 2 * format_len
            # merge formats
            if format_part[0] == start and format_part[1] == true_end and format_part[2] != attribute:
                format_part[2] |= attribute
                done = True
            # add to format inside
            elif (format_part[0] >= start and format_part[1] <= true_end) and format_part[2] != attribute:
                format_part[2] |= attribute
            # inherit from format around
            elif (format_part[0] < start and format_part[1] > true_end) and format_part[2] != attribute:
                attribute |= format_part[2]
        if not done:
            line_format.append([start, end - 2 * format_len, attribute])

    # sort by format start so tui can draw nested format on top of previous one
    line_format = sorted(line_format, key=lambda x: x[0], reverse=True)
    return line, line_format, indexes


def format_multiline_one_line(formats_range, line_len, newline_len, color, quote=False):
    """Generate format for multiline matches, for one line"""
    line_format = []
    if not color:
        return line_format
    for format_range in formats_range:
        if format_range[0] >= line_len or format_range[1] < newline_len:
            continue
        if format_range[0] >= newline_len:
            if format_range[1] < line_len:
                line_format.append([color, format_range[0], format_range[1]])
            else:
                line_format.append([color, format_range[0], line_len])
        elif format_range[1] < line_len:
            line_format.append([color, newline_len + quote*2, format_range[1]])
        else:
            line_format.append([color, newline_len + quote*2, line_len])
    return line_format


def format_multiline_one_line_format(formats, line_len, newline_len, quote=False):
    """Adjust existing format, for one line"""
    line_format = []
    for format_range in formats:
        if format_range[0] >= line_len or format_range[1] < newline_len:
            continue
        if format_range[0] >= newline_len:
            if format_range[1] < line_len:
                line_format.append([format_range[2], format_range[0], format_range[1]])
            else:
                line_format.append([format_range[2], format_range[0], line_len])
        elif format_range[1] < line_len:
            line_format.append([format_range[2], newline_len + quote*2, format_range[1]])
        else:
            line_format.append([format_range[2], newline_len + quote*2, line_len])
    return line_format


def format_multiline_one_line_end(formats_range, line_len, newline_len, color, end, quote=False):
    """Generate format for multiline matches, for one line, with custom end position"""
    line_format = []
    if not color:
        return line_format
    for format_range in formats_range:
        if format_range[0] >= line_len or format_range[1] < newline_len:
            continue
        if format_range[0] >= newline_len:
            if format_range[1] < line_len:
                line_format.append([color, format_range[0], end])
            else:
                line_format.append([color, format_range[0], end])
        elif format_range[1] < line_len:
            line_format.append([color, newline_len + quote*2, end])
        else:
            line_format.append([color, newline_len + quote*2, end])
    return line_format


def ranges_multiline_one_line(ranges, line_len, newline_len, quote=False):
    """Generate ranges for one line"""
    line_ranges = []
    have_id = ranges and len(ranges[0]) >= 3
    for num, format_range in enumerate(ranges):
        if format_range[0] >= line_len or format_range[1] < newline_len:
            continue
        if format_range[0] >= newline_len:
            if format_range[1] < line_len:
                line_ranges.append([format_range[0], format_range[1], format_range[2] if have_id else num])
            else:
                line_ranges.append([format_range[0], line_len, format_range[2] if have_id else num])
        elif format_range[1] < line_len:
            line_ranges.append([newline_len + quote*2, format_range[1], format_range[2] if have_id else num])
        else:
            line_ranges.append([newline_len + quote*2, line_len, format_range[2] if have_id else num])
    return line_ranges


def split_long_line(line, max_len, align=0, normalize=False):
    """
    Split long line into list, on nearest space to left or on newline.
    Optionally align newline to specified length.
    """
    lines_list = []
    first = True
    while line:
        if len(line) > max_len:
            if align and not first:
                line = " " * align + line
            newline_index = len(line[:max_len].rsplit(" ", 1)[0])
            if "\n" in line[:max_len]:
                newline_index = line.index("\n")
            elif newline_index == 0:
                newline_index = max_len
            elif newline_index < align:
                newline_index = max_len
            lines_list.append(line[:newline_index])
            try:
                if line[newline_index] in (" ", "\n"):   # remove space and \n
                    line = line[newline_index+1:]
                else:
                    line = line[newline_index:]
            except IndexError:
                line = line[newline_index+1:]
        elif "\n" in line:
            if align and not first:
                line = " " * align + line
            newline_index = line.index("\n")
            lines_list.append(line[:newline_index])
            line = line[newline_index+1:]
        else:
            if align and not first:
                line = " " * align + line
            lines_list.append(line)
            break
        first = False
    if normalize:
        for num, line in enumerate(lines_list):
            lines_list[num] = normalize_string(line, max_len, emoji_safe=True)
    return lines_list


def clean_type(embed_type):
    r"""
    Clean embed type string from excessive information
    eg. `image\png` ---> `image`
    """
    return embed_type.split("/")[0]


def get_global_name(data, use_nick):
    """Get nick or global name, fallback to username"""
    if use_nick and data.get("nick"):
        return data["nick"]
    if data.get("global_name"):
        return data["global_name"]
    return data["username"]


def replace_backreferences(text):
    """Replace sed like backreference with python re like backreference"""
    def replacer(match):
        num = match.group(1)
        return f"\\g<{num}>"
    return re.sub(r"\\(\d+)", replacer, text)


def substitute(text, pattern):
    """
    Perform sed-like substitution with extended regex, with pattern: 's/old/new'.
    Supported flags: /g - global, /i - case insensitive
    Supports special character & to insert matched text.
    """
    if not pattern.startswith("s"):
        return None
    body = pattern[2:]

    # split on / but handle escaped
    parts = []
    current = []
    escaped = False
    for ch in body:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            continue
        if ch == "/":
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    if len(parts) < 2:
        return None

    old = parts[0].replace("\\/", "/")
    new = parts[1].replace("\\/", "/")
    flags = parts[2] if len(parts) >= 3 else ""
    count = 0 if "g" in flags else 1
    re_flags = 0
    if "i" in flags.lower():
        re_flags = re.IGNORECASE

    # replace & with \g<0>
    new = re.sub(r"(?<!\\)&", r"\\g<0>", new).replace(r"\&", "&")
    new = replace_backreferences(new)

    try:
        return re.sub(old, new, text, count=count, flags=re_flags)
    except re.PatternError:
        return text


def format_poll(poll):
    """Generate message text from poll data"""
    if poll["expires"] < time.time():
        status = "ended"
        expires = "Ended"
    else:
        status = "ongoing"
        expires = "Ends"
    content_list = [
        f"*Poll ({status}):*",
        poll["question"],
    ]
    total_votes = 0
    for option in poll["options"]:
        total_votes += int(option["count"])
    for option in poll["options"]:
        if total_votes:
            answer_votes = option["count"]
            percent = round((answer_votes / total_votes) * 100)
        else:
            answer_votes = 0
            percent = 0
        content_list.append(f"  {"*" if option["me_voted"] else "-"} {option["answer"]} ({answer_votes} votes, {percent}%)")
    content_list.append(f"{expires} <t:{poll["expires"]}:R>")
    content = ""
    for line in content_list:
        content += f"> {line}\n"
    return content.strip("\n")


class ChatGenerator:
    """Chat generator class"""

    def __init__(self, config, colors, colors_formatted, colors_code, my_id, placeholder_emoji, placeholder_images, font_ratio=2.25, dpw=1):
        # load from config
        self.config = config
        self.format_message = config["format_message"]
        self.format_message_grouped = config["format_message_grouped"]
        self.format_newline = config["format_newline"]
        self.format_reply = config["format_reply"]
        self.format_interaction = config["format_interaction"]
        self.format_reactions = config["format_reactions"]
        self.format_reactions_newline = config["format_reactions_newline"]
        self.format_one_reaction = config["format_one_reaction"]
        self.format_timestamp = config["format_timestamp"]
        self.edited_string = config["edited_string"]
        self.app_string_format = config["app_string"]
        self.reactions_separator = config["reactions_separator"]
        self.limit_username = config["limit_username"]
        self.use_nick = config["use_nick_when_available"]
        self.convert_timezone = config["convert_timezone"]
        self.blocked_mode = config["blocked_mode"]
        self.keep_deleted = config["keep_deleted"]
        self.date_separator = config["chat_date_separator"]
        self.format_date = config["format_date"]
        self.emoji_as_text = config["emoji_as_text"]
        self.message_spacing = config["message_spacing"]
        self.message_grouping = config["message_grouping"]
        self.quote_character = config["quote_character"]
        self.trim_embed_url_size = max(config["trim_embed_url_size"], 20)
        self.dynamic_name_len = config["dynamic_name_len"]
        self.limit_chat_buffer = config["limit_chat_buffer"]
        self.syntax_highlight = config["syntax_highlight"]
        self.smart_chat_lines = config["smart_chat_lines"] and self.message_grouping
        self.unreads_edge = "" if self.emoji_as_text else config["tree_drop_down_thread"]
        if self.smart_chat_lines:
            self.tree_drop_down_intersect = config["tree_drop_down_intersect"]
            self.tree_drop_down_vline = config["tree_drop_down_vline"]
            self.spaces_before_text = len(self.format_reactions) - len(self.format_reactions.lstrip(" "))
        self.font_ratio = font_ratio
        self.dpw = dpw   # "dots" per character width
        self.placeholder_emoji = "  " if placeholder_emoji else None
        self.placeholder_images = (config["inline_media_height"]) if placeholder_images else None

        # load colors
        self.color_default = [colors[0]]
        self.color_blocked = [colors[2]]
        self.color_deleted = [colors[3]]
        self.color_pending = [colors[4]]
        self.color_separator = [colors[5]]
        self.color_code = colors[6]
        self.color_standout = colors[7]
        self.color_chat_edited = colors_formatted[6][0]
        self.color_chat_url = colors_formatted[7][0][0]
        self.color_spoiler = colors_formatted[8][0][0]
        self.color_mention_chat_edited = colors_formatted[16][0]
        self.color_mention_chat_url = colors_formatted[17][0][0]
        self.color_mention_spoiler = colors_formatted[18][0][0]
        self.colors_code = colors_code

        # load formatted colors: [[id], [id, start, end]...]
        self.color_message = colors_formatted[0]
        self.color_message_grouped = colors_formatted[1]
        self.color_newline = colors_formatted[2]
        self.color_reply = colors_formatted[3]
        self.color_reactions = colors_formatted[4]
        self.color_interaction = colors_formatted[5]
        self.color_mention_message = colors_formatted[10]
        self.color_mention_message_grouped = colors_formatted[11]
        self.color_mention_newline = colors_formatted[12]
        self.color_mention_reply = colors_formatted[13]
        self.color_mention_reactions = colors_formatted[14]
        self.color_mention_interaction = colors_formatted[15]

        # other
        self.use_global_name = "%global_name" in self.format_message
        self.len_edited = len(self.edited_string)
        self.enable_separator = self.format_date and self.date_separator
        self.my_id = my_id
        self.edited_before_content = is_substring_before(self.format_message, "%edited", "%content")
        self.have_edited = "%edited" in self.format_message

        # initial
        self.last_width = 0
        self.chat = []
        self.chat_format = []
        self.chat_map = []
        # self.chat_lock = threading.Lock()   # enable if threads collide which is very unlikely

        self.tokenize_code = None
        if self.syntax_highlight:
            if shutil.which("pygmentize"):
                self.tokenize_code = peripherals.tokenize_code_pygments
            elif shutil.which("source-highlight"):
                self.tokenize_code = peripherals.tokenize_code_srchl

        self.calculate_lengths()


    def calculate_lengths(self):
        """Calculate all lengths based on config"""
        # moved to method so extensions can modify settings and trigger recalculation
        if self.smart_chat_lines:
            self.tree_drop_down_intersect = self.config["tree_drop_down_intersect"]
            self.tree_drop_down_vline = self.config["tree_drop_down_vline"]
            self.spaces_before_text = len(self.format_reactions) - len(self.format_reactions.lstrip(" "))

        self.placeholder_timestamp = generate_timestamp("2015-01-01T00:00:00.000000+00:00", self.format_timestamp)
        placeholder_message = (self.format_message
            .replace("%username", " " * self.limit_username)
            .replace("%global_name", " " * self.limit_username)
            .replace("%timestamp", self.placeholder_timestamp)
            .replace("%edited", "")
            .replace("%app", "")
            # .replace("%content", "")   # will be split on %content
        ).split("%content")[0]
        self.default_pre_content_len = len(placeholder_message)
        self.timestamp_range = find_timestamp(placeholder_message, self.placeholder_timestamp)
        self.pre_name_len = len(self.format_message
            .replace("%username", "\n")
            .replace("%global_name", "\n")
            .replace("%timestamp", self.placeholder_timestamp)
            .split("\n")[0],
        ) - 1
        self.newline_len = len(self.format_newline
            .replace("%username", normalize_string("Unknown", self.limit_username))
            .replace("%global_name", normalize_string("Unknown", self.limit_username))
            .replace("%timestamp", self.placeholder_timestamp)
            .replace("%content", ""),
            )
        self.pre_reactions_len = len(
            self.format_reactions
            .replace("%timestamp", self.placeholder_timestamp)
            .replace("%reactions", ""),
        ) - 1
        self.end_name = self.pre_name_len + self.limit_username + 1
        self.pre_name_len_reply = len(self.format_reply
            .replace("%username", "\n")
            .replace("%global_name", "\n")
            .replace("%timestamp", self.placeholder_timestamp)
            .split("\n")[0],
        )
        self.pre_name_len_interaction = len(self.format_interaction
            .replace("%username", "\n")
            .replace("%global_name", "\n")
            .split("\n")[0],
        )
        if self.dynamic_name_len:
            self.dynamic_name_len = 2 if "%global_name" in self.format_message else 1
        if "%content" not in self.format_message:
            self.format_message += "/n%content"
        if self.edited_before_content:
            self.pre_edited_len = len(self.format_message
                .replace("%username", " " * self.limit_username)
                .replace("%global_name", " " * self.limit_username)
                .replace("%timestamp", self.placeholder_timestamp)
                .replace("%app", "")
                .split("%edited")[0],
            )
        else:
            self.pre_edited_len = 0

        # curses optimizes scrolling, so large empty space in chat will cause flickering when scrolling tree / member list
        # this is prevented by vertically alternating space and alt_space character (U+2800 - braille pattern blank)
        # same thing is in member list but if member list is closed, here must be too
        fixed_content = (self.format_newline
            .replace("%timestamp", self.placeholder_timestamp)
            .split("%content")[0]
            .split("%global_name")[0]
            .split("%username")[0]
        )
        self.chat_constant_space_idx = len(fixed_content.split(" ")[0]) if fixed_content else None
        self.member_list = False


    def set_my_id(self, my_id):
        """If my_id is not available on init"""
        self.my_id = my_id


    def generate_chat(self, messages, roles, channels, max_length, my_roles, member_roles, blocked, last_seen_msg, show_blocked, change_id=None, change_type=None):
        """
        Generate chat according to provided formatting.
        Message shape:
            format_reply (message that is being replied to)
            format_message (main message line)
            format_newline (if main message is too long, it goes on newlines)
            format_reactions (reactions added to main message)
        chat = [one_message_line, ...]
        chat_format = [[[default_color_id], [color_id, start, end], ...], ...]
        chat_map = [(msg_num, username:(st, end), is_reply, reactions:((st, end, emoji_id), ...), date:(st, end), ranges), ...]
            ranges = (url:(st, end, idx), spoiler:(st, end, idx), emoji:(st, end, id), mentions:(st, end, id), channels:(st, end, id), images:(y, x, h, w))
        change_id hints that only one specific message got changed, change_type hints type of that change: 1 - append, 2 - delete, 3 - edit.
        """
        # with self.chat_lock:   # enable if threads collide which is very unlikely
        num_messages = len(messages)
        if max_length != self.last_width:
            self.last_width = max_length
            self.dyn_limit_username = max_length-15 if self.dynamic_name_len else self.limit_username

        # if its small change then only update data
        elif change_id is not None and self.chat:
            if change_type == 1:   # append message
                if num_messages == self.limit_chat_buffer:
                    self.remove_message(len(messages) - 1)
                message_chat, message_format, message_chat_map = self.generate_message(
                    messages[0],
                    0,
                    roles,
                    channels,
                    max_length,
                    my_roles,
                    member_roles,
                    blocked,
                    last_seen_msg,
                    show_blocked,
                    num_messages,
                    messages[1] if num_messages > 1 else None,
                    self.chat,
                )
                self.insert_data_into(self.chat, message_chat, 0)
                self.insert_data_into(self.chat_format, message_format, 0)
                self.shift_chat_map(-1, 1)
                self.insert_data_into(self.chat_map, message_chat_map, 0)

            elif change_type in (2, 20):   # fully delete message / delete when remove pending
                self.remove_message(change_id)   # change_id is msg_num in this case
                if change_id != 0 and change_type == 2:   # have to reconstruct the message bellow, to update separator lines
                    # skipped when pending because its simple message update
                    message_index = change_id - 1
                    line_index = self.remove_message(message_index, shift_chat_map=False)
                    if line_index is None:
                        return self.chat, self.chat_format, self.chat_map
                    message_chat, message_format, message_chat_map = self.generate_message(
                        messages[message_index],
                        message_index,
                        roles,
                        channels,
                        max_length,
                        my_roles,
                        member_roles,
                        blocked,
                        last_seen_msg,
                        show_blocked,
                        num_messages,
                        messages[message_index+1] if num_messages > message_index+1 else None,
                        self.chat,
                    )
                    self.insert_data_into(self.chat, message_chat, line_index)
                    self.insert_data_into(self.chat_format, message_format, line_index)
                    self.insert_data_into(self.chat_map, message_chat_map, line_index)

            else:   # update existing message and handle pending->sent message transition
                for message_index, message in enumerate(messages):
                    if message["id"] == change_id:
                        break
                else:
                    return self.chat, self.chat_format, self.chat_map
                line_index = self.remove_message(message_index, shift_chat_map=False)
                if line_index is None:
                    return self.chat, self.chat_format, self.chat_map
                message_chat, message_format, message_chat_map = self.generate_message(
                    messages[message_index],
                    message_index,
                    roles,
                    channels,
                    max_length,
                    my_roles,
                    member_roles,
                    blocked,
                    last_seen_msg,
                    show_blocked,
                    num_messages,
                    messages[message_index+1] if num_messages > message_index+1 else None,
                    self.chat,
                )
                self.insert_data_into(self.chat, message_chat, line_index)
                self.insert_data_into(self.chat_format, message_format, line_index)
                self.insert_data_into(self.chat_map, message_chat_map, line_index)
            return self.chat, self.chat_format, self.chat_map

        # reconstruct full chat
        self.chat = []
        self.chat_format = []
        self.chat_map = []
        self.have_unseen_messages_line = False
        for message_index, message in enumerate(messages):
            message_chat, message_format, message_chat_map = self.generate_message(
                message,
                message_index,
                roles,
                channels,
                max_length,
                my_roles,
                member_roles,
                blocked,
                last_seen_msg,
                show_blocked,
                num_messages,
                messages[message_index+1] if num_messages > message_index+1 else None,
                self.chat,
            )
            if not message_chat:
                continue
            # invert message lines order and append them to chat
            # it is inverted because chat is drawn from down to upside
            self.chat.extend(message_chat[::-1])
            self.chat_format.extend(message_format[::-1])
            self.chat_map.extend(message_chat_map[::-1])
        return self.chat, self.chat_format, self.chat_map


    def insert_data_into(self, data_list, data, index):
        """Insert data into data list at specific index, like data_list.extend(data) at custom index. Data order will be inverted."""
        for value in data:
            data_list.insert(index, value)


    def shift_chat_map(self, after_index, diff):
        """Shift chat map message indexes by diff after specified message index"""
        for num, entry in enumerate(self.chat_map):
            if entry is None:
                continue
            message_index = entry[0]
            if message_index is not None and message_index > after_index:
                self.chat_map[num] = (message_index + diff, *entry[1:])


    def remove_message(self, target_index, shift_chat_map=True):
        """Remove message by its index and clear spacing above it"""
        # find all target message line indexes
        remove_lines = []
        found = False
        for i, entry in enumerate(self.chat_map):
            if entry is None:
                if found:
                    remove_lines.append(i)
                continue
            message_index = entry[0]
            if message_index == target_index:
                found = True
                remove_lines.append(i)
                continue
            if found:
                break
        if not remove_lines:
            return None

        # remove lines
        for i in reversed(remove_lines):
            del self.chat[i]
            del self.chat_format[i]
            del self.chat_map[i]

        # fix message_index in chat_map
        if shift_chat_map:
            self.shift_chat_map(target_index, -1)

        return remove_lines[0]


    def generate_message(self, message, num, roles, channels, max_length, my_roles, member_roles, blocked, last_seen_msg, show_blocked, num_messages, next_msg, full_chat):
        """Generate one message according to provided formatting"""
        if not message:   # failsafe
            return None, None, None

        chat = []
        chat_format = []
        chat_map = []
        mentioned = False
        edited = message.get("edited") and self.have_edited
        user_id = message.get("user_id")
        selected_color_spoiler = self.color_spoiler
        disable_formatting = False

        # select base color
        color_base = self.color_default
        for mention in message["mentions"]:
            if mention["id"] == self.my_id:
                mentioned = True
                selected_color_spoiler = self.color_mention_spoiler
                break
        for role in message["mention_roles"]:
            if role in my_roles:
                mentioned = True
                selected_color_spoiler = self.color_mention_spoiler
                break
        if "pending" in message:
            color_base = self.color_pending
            selected_color_spoiler = self.color_pending
            disable_formatting = True

        # skip deleted
        if "deleted" in message:
            if self.keep_deleted:
                color_base = self.color_deleted
                selected_color_spoiler = self.color_deleted
                disable_formatting = True
            else:
                return None, None, None

        # get member role color and nick
        role_color = None
        alt_role_color = None
        nick = None
        for member in member_roles:
            if member["user_id"] == user_id:
                role_color = member.get("primary_role_color")
                alt_role_color = member.get("primary_role_alt_color")
                nick = member.get("nick")
                break
        if not message["nick"]:
            message["nick"] = nick

        reply_color_format = color_base

        # handle blocked messages
        if self.blocked_mode and user_id in blocked and not show_blocked:
            if self.blocked_mode == 1:
                message["username"] = "blocked"
                message["global_name"] = "blocked"
                message["nick"] = "blocked"
                message["content"] = "Blocked message"
                message["embeds"] = []
                message["stickers"] = []
                color_base = self.color_blocked
            else:
                chat_map.append(None)
                return None, None, None   # to not break message-to-chat conversion

        group = False

        # unread message separator
        next_id = int(next_msg["id"]) if next_msg else -1
        if not self.have_unseen_messages_line and self.date_separator and last_seen_msg and (num == num_messages-1 or (next_id <= int(last_seen_msg))):
            if self.message_spacing:
                chat.append(" " if num % 2 else ALT_SPACE + " " * (max_length-1))
                chat_format.append([color_base])
                chat_map.append((None, None, None, None, None, None))
            # keep text always in center
            chat.append(f"{self.date_separator * (max_length - 4 - len(self.unreads_edge))}{self.unreads_edge} New")
            chat_format.append([[8]])   # 8 - red - color_tree_mentioned
            chat_map.append(None)
            self.have_unseen_messages_line = True
            if self.message_spacing:
                chat.append(" " if num % 2 else ALT_SPACE + " " * (max_length-1))
                chat_format.append([color_base])
                chat_map.append(None)

        elif next_msg:
            # date separator
            if self.enable_separator and day_from_snowflake(message["id"]) != day_from_snowflake(next_msg["id"]):
                if self.message_spacing:
                    chat.append(" " if num % 2 else ALT_SPACE + " " * (max_length-1))
                    chat_format.append([color_base])
                    chat_map.append(None)
                # if this message is 1 day older than next message (up - past message)
                date = generate_timestamp(message["timestamp"], self.format_date, self.convert_timezone)
                # keep text always in center
                filler = max_length - len(date)
                filler_l = filler // 2
                filler_r = filler - filler_l
                chat.append(f"{self.date_separator * filler_l}{date}{self.date_separator * filler_r}")
                chat_format.append([self.color_separator])
                chat_map.append(None)
                if self.message_spacing:
                    chat.append(" " if num % 2 else ALT_SPACE + " " * (max_length-1))
                    chat_format.append([color_base])
                    chat_map.append(None)

            # empty separator between messages not from same sender of after period of time and if message has reply or interaction
            elif message["referenced_message"] or message["interaction"] or (self.message_spacing and (message["user_id"] != next_msg["user_id"] or unix_from_snowflake(message["id"]) - unix_from_snowflake(next_msg["id"]) > SPLIT_AFTER_TIME)):
                group = False
                chat.append(" " if num % 2 else ALT_SPACE + " " * (max_length-1))
                chat_format.append([color_base])
                chat_map.append(None)
            else:
                group = self.message_grouping

        # replied message line
        if message["referenced_message"]:
            ref_message = message["referenced_message"].copy()
            if ref_message["id"]:
                if self.blocked_mode and ref_message["user_id"] in blocked and not show_blocked:
                    ref_message["username"] = "blocked"
                    ref_message["global_name"] = "blocked"
                    ref_message["nick"] = "blocked"
                    ref_message["content"] = "Blocked message"
                    reply_color_format = self.color_blocked
                for member in member_roles:
                    if member["user_id"] == ref_message["user_id"]:
                        if not ref_message["nick"]:
                            ref_message["nick"] = member.get("nick")
                        break
                global_name = get_global_name(ref_message, self.use_nick)
                reply_embeds = ref_message["embeds"].copy()
                content = ""
                emoji_ranges = []
                if ref_message["content"]:
                    content = ref_message["content"]
                    if self.emoji_as_text:
                        content = utils.demojize(content)
                    content = replace_bracketed_urls(content)
                    content = replace_spoilers(content)
                    content, _ = replace_mentions(content, ref_message["mentions"], global_name=self.use_global_name, use_nick=self.use_nick)
                    content, _ = replace_roles(content, roles)
                    content = replace_discord_url(content)
                    content, _ = replace_channels(content, channels)
                    content, _ = replace_timestamps(content, self.convert_timezone)
                    content, _ = replace_markdown_urls(content, ())
                    content, _ = replace_escaped_md(content)
                    content, emoji_ranges = replace_discord_emoji(content, self.placeholder_emoji)
                if reply_embeds:
                    for embed in reply_embeds:
                        embed_url = embed["url"]
                        if embed_url and not embed.get("hidden") and embed_url not in content:
                            if content:
                                content += "\n"
                            if "main_url" not in embed:   # its attachment
                                content += f"[{clean_type(embed["type"])} attachment]"
                            elif embed["type"] == "rich":
                                content += f"[rich embed]: {embed_url}"
                            else:
                                content += f"[{clean_type(embed["type"])} embed]"
                reply_line = lazy_replace(self.format_reply, "%username", lambda: normalize_string(ref_message["username"], self.dyn_limit_username, emoji_safe=False, fill=not (self.dynamic_name_len)))
                reply_line = lazy_replace(reply_line, "%global_name", lambda: normalize_string(global_name, self.dyn_limit_username, emoji_safe=True, fill=not (self.dynamic_name_len)))
                reply_line = lazy_replace(reply_line, "%timestamp", lambda: generate_timestamp(ref_message["timestamp"], self.format_timestamp, self.convert_timezone))
                pre_content_len = len(reply_line.split("%content")[0])
                reply_line = lazy_replace(reply_line, "%content", lambda: content.replace("\r", " ").replace("\n", " "))
            else:
                global_name = "Unknown"
                reply_line = lazy_replace(self.format_reply, "%username", lambda: normalize_string(global_name, self.dyn_limit_username, emoji_safe=False, fill=not (self.dynamic_name_len)))
                reply_line = lazy_replace(reply_line, "%global_name", lambda: normalize_string(global_name, self.dyn_limit_username, emoji_safe=False, fill=not (self.dynamic_name_len)))
                reply_line = reply_line.replace("%timestamp", self.placeholder_timestamp)
                reply_line = lazy_replace(reply_line, "%content", lambda: ref_message["content"].replace("\r", "").replace("\n", ""))
                emoji_ranges = []
                pre_content_len = 0
            reply_line = normalize_string(reply_line, max_length, emoji_safe=True, dots=True)
            if self.dynamic_name_len:
                if self.dynamic_name_len == 1:
                    name_len = len(ref_message["username"][:self.dyn_limit_username])
                else:
                    name_len = len(limit_width_wch(global_name, self.dyn_limit_username)[0])
            chat.append(reply_line)
            if disable_formatting or reply_color_format == self.color_blocked:
                chat_format.append([color_base])
            elif mentioned:
                if self.dynamic_name_len:
                    chat_format.append(fix_line_format(shift_formats(self.color_mention_reply, self.pre_name_len_reply, name_len - self.limit_username), reply_line))
                else:
                    chat_format.append(fix_line_format(self.color_mention_reply, reply_line))
            elif self.dynamic_name_len:
                chat_format.append(fix_line_format(shift_formats(self.color_reply, self.pre_name_len_reply, name_len - self.limit_username), reply_line))
            else:
                chat_format.append(fix_line_format(self.color_reply, reply_line))
            shift_ranges_all(pre_content_len, emoji_ranges)
            this_line_ranges = (None, None, fix_map_ranges(emoji_ranges, reply_line), None, None, None)
            chat_map.append((num, None, True, None, None, this_line_ranges))

        # bot interaction
        elif message["interaction"]:
            global_name = normalize_string(get_global_name(message["interaction"], self.use_nick), self.limit_username, emoji_safe=True)
            interaction_line = (
                self.format_interaction
                .replace("%username", message["interaction"]["username"][:self.limit_username])
                .replace("%global_name", global_name)
                .replace("%command", message["interaction"]["command"])
            )
            interaction_line = normalize_string(interaction_line, max_length, emoji_safe=True, dots=True)
            chat.append(interaction_line)
            if disable_formatting or reply_color_format == self.color_blocked:
                chat_format.append([color_base])
            elif mentioned:
                chat_format.append(fix_line_format(self.color_mention_interaction, interaction_line))
            else:
                chat_format.append(fix_line_format(self.color_interaction, interaction_line))
            chat_map.append((num, None, 2, None, None, None))

        # main message
        global_name = get_global_name(message, self.use_nick) if self.use_global_name else ""
        if "bot" in message:
            app_string = self.app_string_format.replace("%app", "App - Ephemeral" if message["bot"] == 2 else "App")
        elif "webhook" in message:
            app_string = self.app_string_format.replace("%app", "Webhook")
        else:
            app_string = None
        if group:
            placeholder_message = self.format_message_grouped.replace("%timestamp", self.placeholder_timestamp).replace("%edited", "").split("%content")[0]
            pre_content_len = len(placeholder_message)
            name_len = 0
            end_name = 0
        elif self.dynamic_name_len:
            if self.dynamic_name_len == 1:
                name_len = len(message["username"][:self.dyn_limit_username])
            else:
                name_len = len(limit_width_wch(global_name, self.dyn_limit_username)[0])
            end_name = self.pre_name_len + name_len + 1
            placeholder_message = (self.format_message
                .replace("%username", " " * name_len)
                .replace("%global_name", " " * name_len)
                .replace("%timestamp", self.placeholder_timestamp)
                .replace("%edited", "")
                .replace("%app", app_string if app_string else "")
            ).split("%content")[0]
            pre_content_len = len(placeholder_message)
        else:
            pre_content_len = self.default_pre_content_len
            name_len = self.limit_username
            end_name = self.end_name
        if self.edited_before_content and edited and not group:
            pre_content_len += self.len_edited
        quote = False
        content = ""
        if "poll" in message:
            message["content"] = format_poll(message["poll"])
        if message["content"]:
            content = message["content"]
            if self.emoji_as_text:
                content = utils.demojize(content)
            content = replace_bracketed_urls(content)
            content, emoji_ranges = replace_discord_emoji(content, self.placeholder_emoji)
            content, mention_ranges = replace_mentions(content, message["mentions"], emoji_ranges, global_name=self.use_global_name, use_nick=self.use_nick)
            content, role_ranges = replace_roles(content, roles, emoji_ranges, mention_ranges)
            mention_ranges += role_ranges
            content = replace_discord_url(content, emoji_ranges, mention_ranges)
            content, channel_ranges = replace_channels(content, channels, emoji_ranges, mention_ranges)
            content, timestamp_ranges = replace_timestamps(content, self.convert_timezone, emoji_ranges, mention_ranges, channel_ranges)
            content, code_snippets = replace_code_snippets(content, emoji_ranges, mention_ranges, channel_ranges, timestamp_ranges)
            content, code_blocks = replace_code_blocks(content, emoji_ranges, mention_ranges, channel_ranges, timestamp_ranges, code_snippets)
            content, urls = replace_markdown_urls(content, (code_snippets, code_blocks), emoji_ranges, mention_ranges, channel_ranges, timestamp_ranges, code_snippets)
            code_blocks_tokens = []
            if self.syntax_highlight:
                for block in code_blocks:
                    if not block[2]:
                        continue
                    tokens = self.tokenize_code(content[block[0]:block[1]], block[2], initial_idx=block[0])
                    for token in tokens:
                        token[2] = self.colors_code[token[2]]
                    code_blocks_tokens += tokens
            shift_ranges_all(
                pre_content_len,
                emoji_ranges,
                mention_ranges,
                channel_ranges,
                timestamp_ranges,
                code_snippets,
                code_blocks,
                code_blocks_tokens,
                urls,
            )
            if content.startswith("> "):
                content = self.quote_character + " " + content[2:]
                quote = True
        else:
            emoji_ranges = []
            mention_ranges = []
            channel_ranges = []
            timestamp_ranges = []
            code_snippets = []
            code_blocks = []
            code_blocks_tokens = []
            urls = []
        embeds = []
        image_locations = []
        embed_marker_ranges = []
        for num_e, embed in enumerate(message["embeds"]):
            embed_url = embed["url"]
            if embed_url and not embed.get("hidden") and embed_url not in content:
                if content:
                    content += "\n"
                spoiler = embed["name"] and embed["name"].startswith("SPOILER_")
                if spoiler:
                    spoiler = 1000 + num_e not in message.get("spoiled", [])
                if "main_url" not in embed:   # its attachment
                    if self.placeholder_images:
                        embed_url = ""
                    elif self.trim_embed_url_size:
                        embed_url = trim_string(embed_url, self.trim_embed_url_size)
                    embed_type = clean_type(embed["type"])
                    if embed_type.lower() not in ("image", "video"):
                        embed_type = "file"
                        embed_url = embed.get("name", "")
                        if embed.get("duration"):
                            embed_url += f" ({format_seconds(embed["duration"], nice=True, pad=False)})"
                        pre_embed_len = pre_content_len + len(content)
                        embeds.append([pre_embed_len, pre_embed_len + len(embed_type) + 15 + len(embed_url), 0])
                    embed_marker_ranges.append([len(content), len(content) + len(embed_type) + 14])
                    content += f"[{embed_type} attachment]: {embed_url}"
                elif embed["type"] == "rich":
                    embed_url = embed_url.replace("\r\n", "\n")
                    embed_marker_ranges.append([len(content), len(content) + 13])
                    content += f"[rich embed]:\n{embed_url}"
                else:
                    if self.placeholder_images and embed["type"] != "article":
                        embed_url = ""
                    elif embed["main_url"] == embed_url and self.trim_embed_url_size:
                        embed_url = trim_string(embed_url, self.trim_embed_url_size)
                    embed_type = clean_type(embed["type"])
                    embed_marker_ranges.append([len(content), len(content) + len(embed_type) + 9])
                    content += f"[{embed_type} embed]: {embed_url}"
                if self.placeholder_images and embed.get("proxy_url") and embed["hw"]:
                    if not embed["hw"][0] or not embed["hw"][1]:
                        continue
                    h = embed["hw"][0] / self.font_ratio
                    w = embed["hw"][1]
                    smallest_h = h / self.dpw
                    smallest_w = w / self.dpw
                    scale = min(min(self.placeholder_images, smallest_h) / h, min(max_length - 1 - self.newline_len, smallest_w) / w, 1)
                    h = round(h * scale)
                    w = round(w * scale) - 1
                    if self.chat_constant_space_idx is None:   # insert here if cant in newline format
                        content += f"\n<{MARKER}:{num_e}>"
                        for line in range(h - 1):
                            content += f"\n{" " if line % 2 else ALT_SPACE}"
                            if spoiler:
                                content += " " * w
                    elif spoiler:
                        content += f"\n<{MARKER}:{num_e}>" + ("\n" + " " * w) * (h - 1)
                    else:
                        content += f"\n<{MARKER}:{num_e}>" + "\n " * (h - 1)
                    image_locations.append((h, w, spoiler))
        for sticker in message["stickers"]:
            sticker_type = sticker["format_type"]
            if content:
                content += "\n"
            if sticker_type == 1:
                embed_marker_ranges.append([len(content), len(content) + 14])
                content += f"[png sticker]: {sticker["name"]}"
            elif sticker_type == 2:
                embed_marker_ranges.append([len(content), len(content) + 15])
                content += f"[apng sticker]: {sticker["name"]}"
            elif sticker_type == 3:
                embed_marker_ranges.append([len(content), len(content) + 17])
                content += f"[lottie sticker]: {sticker["name"]}"
            else:
                embed_marker_ranges.append([len(content), len(content) + 14])
                content += f"[gif sticker]: {sticker["name"]}"
        message_line = lazy_replace(
            self.format_message_grouped if group else self.format_message,
            "%username",
            lambda: normalize_string(message["username"],
            self.dyn_limit_username, emoji_safe=False,
            fill=not (self.dynamic_name_len)),
        )
        if embed_marker_ranges:
            shift_ranges_all(pre_content_len, embed_marker_ranges)
        message_line = lazy_replace(message_line, "%global_name", lambda: normalize_string(global_name, self.dyn_limit_username, emoji_safe=True, fill=not (self.dynamic_name_len)))
        message_line = lazy_replace(message_line, "%timestamp", lambda: generate_timestamp(message["timestamp"], self.format_timestamp, self.convert_timezone))
        message_line = message_line.replace("%edited", self.edited_string if edited else "")
        message_line = lazy_replace(message_line, "%app", lambda: app_string if app_string else "")
        message_line = message_line.replace("%content", content)

        # find all non-markdown urls
        if self.color_chat_url:
            for match in re.finditer(match_url, message_line):
                start, end = match.span()
                skip = False
                for except_range in chain(code_snippets, code_blocks):
                    start_r = except_range[0]
                    end_r = except_range[1]
                    if start > start_r and start < end_r and end > start_r and end <= end_r:
                        skip = True
                        break
                if not skip:
                    urls.append([start, end, match.group()])
        urls = sorted(urls, key=lambda x: x[0])
        for idx, url in enumerate(urls):
            url[2] = idx
        idx = len(urls)
        for i, embed in enumerate(embeds):
            embed[2] = idx + i

        # find spoilers - must be after all other replacements
        spoilers = []
        for match in re.finditer(match_md_spoiler, message_line):
            spoilers.append([match.start(), match.end()])
        spoiled = message.get("spoiled")
        if spoiled:
            spoilers = [value for i, value in enumerate(spoilers) if i not in spoiled]   # exclude spoiled messages

        # find all markdown and correct format indexes
        message_line, md_format, md_indexes = format_md_all(message_line, pre_content_len, (code_snippets, code_blocks, urls))
        if md_indexes:
            move_by_indexes(
                md_indexes,
                urls,
                embeds,
                spoilers,
                code_snippets,
                code_blocks,
                code_blocks_tokens,
                emoji_ranges,
                mention_ranges,
                channel_ranges,
                timestamp_ranges,
                embed_marker_ranges,
            )
        message_line, escaped_indexes = replace_escaped_md(message_line, (code_snippets, code_blocks, urls))

        # correct format indexes for removed markdown escape characters "\"
        if escaped_indexes:
            move_by_indexes(
                escaped_indexes,
                md_format,
                urls,
                embeds,
                spoilers,
                code_snippets,
                code_blocks,
                code_blocks_tokens,
                emoji_ranges,
                mention_ranges,
                channel_ranges,
                timestamp_ranges,
                embed_marker_ranges,
            )

        # delete all format ranges that are inside spoiler ranges
        if spoilers:
            delete_ranges(
                spoilers,
                md_format,
                urls,
                code_snippets,
                code_blocks,
                code_blocks_tokens,
                emoji_ranges,
                mention_ranges,
                channel_ranges,
                timestamp_ranges,
                embed_marker_ranges,
            )
        standout_ranges = chain(timestamp_ranges, mention_ranges, channel_ranges, emoji_ranges, embed_marker_ranges)

        # limit message_line and split to multiline
        newline_sign = False
        newline_index = max_length
        quote_nl = True
        len_wch_message_line = len_wch(message_line)
        if len_wch_message_line > max_length:
            newline_index = len(limit_width_wch(message_line, max_length)[0].rsplit(" ", 1)[0])   # split line on space
            # if there is \n on current line, use its position to split line
            if "\n" in message_line[:max_length]:
                newline_index = message_line.index("\n")
                quote = False
                newline_sign = True
                split_on_space = 0
            elif newline_index <= self.newline_len:
                newline_index = split_index_wch(message_line, max_length)
                quote_nl = False
            else:
                quote_nl = False
            if message_line[newline_index] in (" ", "\n"):   # remove space and \n
                next_line = message_line[newline_index + 1:]
                split_on_space = 1
            else:
                next_line = message_line[newline_index:]
                split_on_space = 0
            message_line = message_line[:newline_index]
        elif "\n" in message_line:
            newline_index = message_line.index("\n")
            next_line = message_line[newline_index+1:]
            message_line = message_line[:newline_index]
            quote = False
            newline_sign = True
            split_on_space = 1
        else:
            next_line = None

        if newline_sign and next_line and next_line.startswith("> "):
            next_line = next_line[2:]
            quote = True

        # replace spoilers
        format_spoilers = format_multiline_one_line(spoilers, newline_index+1, 0, selected_color_spoiler, quote)
        for spoiler_range in format_spoilers:
            start = spoiler_range[1]
            end = spoiler_range[2]
            message_line = message_line[:start] + "▒" * (end - start) + message_line[end:]

        # code blocks formatting here to add spaces to end of string
        code_block_format = format_multiline_one_line_end(code_blocks, newline_index+1, 0, self.color_code, max_length-1, quote)
        if code_block_format:
            message_line = message_line.ljust(max_length-1)
        code_block_format += format_multiline_one_line_format(code_blocks_tokens, newline_index+1, 0, quote)

        chat.append(message_line)
        urls_this_line = fix_map_ranges(ranges_multiline_one_line(urls + embeds, newline_index+1, 0, quote), message_line)
        spoilers_this_line = fix_map_ranges(ranges_multiline_one_line(spoilers, newline_index+1, 0, quote), message_line)
        emoji_this_line = fix_map_ranges(ranges_multiline_one_line(emoji_ranges, newline_index+1, 0, quote), message_line)
        mentions_this_line = fix_map_ranges(ranges_multiline_one_line(mention_ranges, newline_index+1, 0, quote), message_line)
        channels_this_line = fix_map_ranges(ranges_multiline_one_line(channel_ranges, newline_index+1, 0, quote), message_line)
        this_line_ranges = (urls_this_line, spoilers_this_line, emoji_this_line, mentions_this_line, channels_this_line, None)
        # using False for name range if group so app.msg_to_lines still can find message base (if its None then its not base)
        chat_map.append((num, False if group else (self.pre_name_len, end_name), False, None, (0, 0) if group else self.timestamp_range, this_line_ranges))

        # formatting
        len_message_line = len(message_line)
        if disable_formatting:
            chat_format.append([color_base])
        elif mentioned:
            if group:
                format_line = self.color_mention_message_grouped[:]
            else:
                format_line = self.color_mention_message[:]
            if self.dynamic_name_len:
                format_line = shift_formats(format_line, self.pre_name_len+1, name_len - self.limit_username)
            format_line += format_multiline_one_line_format(md_format, newline_index+1, 0, quote)
            format_line += format_multiline_one_line(urls, newline_index+1, 0, self.color_mention_chat_url, quote)
            format_line += format_multiline_one_line(code_snippets, newline_index+1, 0, self.color_code, quote)
            format_line += format_multiline_one_line(standout_ranges, newline_index+1, 0, self.color_standout, quote)
            format_line += code_block_format
            format_line += format_spoilers
            if alt_role_color:
                format_line.append([alt_role_color, self.pre_name_len+1, end_name])
            if edited:
                if self.edited_before_content and not group:
                    format_line.append([*self.color_mention_chat_edited, self.pre_edited_len + (name_len - self.limit_username), self.pre_edited_len + (name_len - self.limit_username) + self.len_edited])
                elif edited and not next_line:
                    format_line.append(self.color_mention_chat_edited + [len_message_line - self.len_edited, len_message_line])
            chat_format.append(fix_line_format(format_line, message_line))
        else:
            if group:
                format_line = self.color_message_grouped[:]
            else:
                format_line = self.color_message[:]
            if self.dynamic_name_len:
                format_line = shift_formats(format_line, self.pre_name_len+1, name_len - self.limit_username)
            format_line += format_multiline_one_line_format(md_format, newline_index+1, 0, quote)
            format_line += format_multiline_one_line(urls, newline_index+1, 0, self.color_chat_url, quote)
            format_line += format_multiline_one_line(code_snippets, newline_index+1, 0, self.color_code, quote)
            format_line += format_multiline_one_line(standout_ranges, newline_index+1, 0, self.color_standout, quote)
            format_line += code_block_format
            format_line += format_spoilers
            if role_color:
                format_line.append([role_color, self.pre_name_len+1, end_name])
            if edited:
                if self.edited_before_content and not group:
                    format_line.append([*self.color_chat_edited, self.pre_edited_len + (name_len - self.limit_username), self.pre_edited_len + (name_len - self.limit_username) + self.len_edited])
                elif edited and not next_line:
                    format_line.append([*self.color_chat_edited, len_message_line - self.len_edited, len_message_line])
            chat_format.append(fix_line_format(format_line, message_line))

        # newline
        line_num = 1
        quote_nl = quote_nl and quote
        while next_line and line_num < 200:   # safety against memory leaks
            this_quote = False
            if quote:
                full_content = self.quote_character + " " + next_line
                extra_newline_len = 2
                this_quote = True
            else:
                full_content = next_line
                extra_newline_len = 0
            new_line = lazy_replace(self.format_newline, "%username", lambda: normalize_string(message["username"], self.limit_username, emoji_safe=True))
            new_line = lazy_replace(new_line, "%global_name", lambda: normalize_string(global_name, self.limit_username, emoji_safe=True))
            new_line = lazy_replace(new_line, "%timestamp", lambda: generate_timestamp(message["timestamp"], self.format_timestamp, self.convert_timezone))
            new_line = new_line.replace("%content", full_content)
            if not self.member_list and self.chat_constant_space_idx is not None and line_num % 2:
                new_line = new_line[:self.chat_constant_space_idx] + ALT_SPACE + new_line[self.chat_constant_space_idx+1:]

            # correct index for each new line
            content_index_correction = self.newline_len + extra_newline_len - 1 + (not split_on_space) - newline_index - quote_nl*2
            shift_ranges_all(
                content_index_correction,
                md_format,
                urls,
                embeds,
                spoilers,
                code_snippets,
                code_blocks,
                code_blocks_tokens,
                emoji_ranges,
                mention_ranges,
                channel_ranges,
                timestamp_ranges,
                embed_marker_ranges,
            )
            standout_ranges = chain(timestamp_ranges, mention_ranges, channel_ranges, emoji_ranges, embed_marker_ranges)
            quote_nl = False

            # limit new_line and split to next line
            newline_sign = False
            if len_wch(new_line) > max_length:
                newline_index = len(limit_width_wch(new_line, max_length - bool(code_block_format))[0].rsplit(" ", 1)[0])   # split line on space
                if "\n" in new_line[:max_length]:
                    newline_index = new_line.index("\n")
                    quote = False
                    newline_sign = True
                    split_on_space = 0
                elif newline_index <= self.newline_len + 2*quote:
                    newline_index = split_index_wch(new_line, max_length)
                try:
                    if new_line[newline_index] in (" ", "\n"):   # remove space and \n
                        next_line = new_line[newline_index + 1:]
                        split_on_space = 1
                    else:
                        next_line = new_line[newline_index:]
                        split_on_space = 0
                except IndexError:
                    next_line = new_line[newline_index + 1:]
                    split_on_space = 1
                new_line = new_line[:newline_index]
            elif "\n" in new_line:
                newline_index = new_line.index("\n")
                next_line = new_line[newline_index+1:]
                new_line = new_line[:newline_index]
                quote = False
                newline_sign = True
                split_on_space = 1
            else:
                next_line = None

            if next_line and newline_sign and next_line.startswith("> "):
                next_line = next_line[2:]
                quote_nl = True
                quote = True

            # replace spoilers
            len_new_line = len(new_line)
            format_spoilers = format_multiline_one_line(spoilers, len_new_line, self.newline_len, selected_color_spoiler, this_quote)
            for spoiler_range in format_spoilers:
                start = spoiler_range[1]
                end = spoiler_range[2]
                new_line = new_line[:start] + "▒" * (end - start) + new_line[end:]

            # code blocks formatting here to add spaces to end of string
            code_block_format = format_multiline_one_line_end(code_blocks, len_new_line, self.newline_len, self.color_code, max_length-1, this_quote)
            if code_block_format:
                new_line = new_line.ljust(max_length-1)
            code_block_format += format_multiline_one_line_format(code_blocks_tokens, len_new_line, self.newline_len, this_quote)

            len_new_line = len(new_line)
            chat.append(new_line)
            urls_this_line = fix_map_ranges(ranges_multiline_one_line(urls + embeds, len_new_line, self.newline_len, quote), new_line)
            spoilers_this_line = fix_map_ranges(ranges_multiline_one_line(spoilers, len_new_line, self.newline_len, quote), new_line)
            emoji_this_line = fix_map_ranges(ranges_multiline_one_line(emoji_ranges, len_new_line, self.newline_len, quote), new_line)
            mentions_this_line = fix_map_ranges(ranges_multiline_one_line(mention_ranges, len_new_line, self.newline_len, quote), new_line)
            channels_this_line = fix_map_ranges(ranges_multiline_one_line(channel_ranges, len_new_line, self.newline_len, quote), new_line)
            this_line_ranges = (urls_this_line, spoilers_this_line, emoji_this_line, mentions_this_line, channels_this_line, [])
            chat_map.append((num, None, None, None, None, this_line_ranges))

            # formatting
            if disable_formatting:
                chat_format.append([color_base])
            elif mentioned:
                format_line = self.color_mention_newline[:]
                format_line += format_multiline_one_line_format(md_format, len_new_line, self.newline_len, this_quote)
                format_line += format_multiline_one_line(urls, len_new_line, self.newline_len, self.color_mention_chat_url, this_quote)
                format_line += format_multiline_one_line(code_snippets, len_new_line, self.newline_len, self.color_code, this_quote)
                format_line += format_multiline_one_line(standout_ranges, len_new_line, self.newline_len, self.color_standout, this_quote)
                format_line += code_block_format
                format_line += format_spoilers
                if edited and not next_line and not (self.edited_before_content and not group):
                    format_line.append(self.color_mention_chat_edited + [len_new_line - self.len_edited, len_new_line])
                try:
                    chat_format.append(fix_line_format(format_line, new_line))
                except OverflowError:   # fallback if anything goes wrong
                    chat_format.append(fix_line_format_py(format_line, new_line))
            else:
                format_line = self.color_newline[:]
                format_line += format_multiline_one_line_format(md_format, len_new_line, self.newline_len, this_quote)
                format_line += format_multiline_one_line(urls, len_new_line, self.newline_len, self.color_chat_url, this_quote)
                format_line += format_multiline_one_line(code_snippets, len_new_line, self.newline_len, self.color_code, this_quote)
                format_line += format_multiline_one_line(standout_ranges, len_new_line, self.newline_len, self.color_standout, this_quote)
                format_line += code_block_format
                format_line += format_spoilers
                if edited and not next_line and not (self.edited_before_content and not group):
                    format_line.append([*self.color_chat_edited, len_new_line - self.len_edited, len_new_line])
                try:
                    chat_format.append(fix_line_format(format_line, new_line))
                except OverflowError:   # fallback if anything goes wrong
                    chat_format.append(fix_line_format_py(format_line, new_line))
                    logger.error(f"An OverflowError occurred in cython. Please report with this information: {repr(format_line)}, {repr(new_line)}")
            line_num += 1

        # add images to ranges in chat_map relative to this message base line and add format for spoiler images
        start_y = len(chat_map) + 1
        num_images = len(image_locations) - 1
        for idx_i, image_location in enumerate(reversed(image_locations)):   # y is relative to message base line
            num_i = num_images - idx_i
            start_y -= image_location[0] + 1
            # search for marker on this line and above
            marker = f"<{MARKER}:{num_i}>"
            shift = 0
            while chat[start_y-shift][self.newline_len:] != marker:
                shift += 1
                if start_y - shift < 0:
                    break
            if start_y - shift < 0:
                continue
            spoiler = image_location[2]
            h = image_location[0]
            w = image_location[1]
            chat[start_y-shift] = chat[start_y-shift][:self.newline_len] + ((" " * w) if spoiler else " ")
            for idx_rel in range(image_location[0]):
                idx = start_y - shift + idx_rel
                chat_map[idx][5][5].append(self.newline_len)   # start_x
                chat_map[idx][5][5].append(w)   # width
                chat_map[idx][5][5].append(num_i)   # embed index
                if idx_rel == 0:
                    chat_map[idx][5][5].append(h)   # height
                if spoiler:
                    chat_format[idx].append([self.color_spoiler, self.newline_len, self.newline_len + image_location[1]])

        # reactions
        if message["reactions"]:
            reactions = []
            emoji_ranges = []
            for reaction in message["reactions"]:
                if self.placeholder_emoji and reaction["emoji_id"]:
                    emoji_str = self.placeholder_emoji
                elif self.emoji_as_text and not reaction["emoji_id"]:
                    emoji_str = emoji_name(reaction["emoji"])
                else:
                    emoji_str = reaction["emoji"]
                my_reaction = ""
                if reaction["me"]:
                    my_reaction = "*"
                reactions.append(
                    self.format_one_reaction
                    .replace("%reaction", emoji_str)
                    .replace("%count", f"{my_reaction}{reaction["count"]}"),
                )
            first_reaction_line = True
            extend_lines = self.smart_chat_lines and (full_chat[-1] if full_chat else "").lstrip(" ").startswith(self.tree_drop_down_intersect)
            reaction_index = 0
            while reactions:
                reactions_this = [reactions[0]]
                reaction_string = reactions.pop(0)
                reaction = reactions[0] if reactions else ""
                if first_reaction_line:
                    reactions_line = lazy_replace(self.format_reactions, "%timestamp", lambda: generate_timestamp(message["timestamp"], self.format_timestamp, self.convert_timezone))
                else:
                    reactions_line = lazy_replace(self.format_reactions_newline, "%timestamp", lambda: generate_timestamp(message["timestamp"], self.format_timestamp, self.convert_timezone))
                len_base = len(reactions_line.replace("%reactions", ""))
                while reactions and len_base + len(reaction_string) + len(reaction) < max_length:
                    reaction = reactions.pop(0)
                    reaction_string += self.reactions_separator + reaction
                    reactions_this.append(reaction)
                    reaction = reactions[0] if reactions else ""
                reactions_line = reactions_line.replace("%reactions", reaction_string)
                reactions_line = normalize_string(reactions_line, max_length, emoji_safe=True, dots=True, fill=True)
                if extend_lines:
                    selected = self.tree_drop_down_intersect if first_reaction_line else self.tree_drop_down_vline
                    reactions_line = " " * self.spaces_before_text + selected + reactions_line[1+self.spaces_before_text:]
                chat.append(reactions_line)
                if disable_formatting:
                    chat_format.append([color_base])
                elif mentioned:
                    chat_format.append(self.color_mention_reactions)
                else:
                    chat_format.append(self.color_reactions)
                reactions_map = []
                offset = 0
                for num_r, reaction in enumerate(reactions_this):
                    idx = reaction_index + num_r
                    wide = not self.emoji_as_text and len(message["reactions"][idx]["emoji"]) == 1   # emoji reaction will be one character
                    emoji_id = message["reactions"][idx]["emoji_id"]
                    if not emoji_id:
                        emoji_id = message["reactions"][idx]["emoji"]
                    reactions_map.append([self.pre_reactions_len + offset+1, self.pre_reactions_len + offset + len(reaction) + wide, emoji_id])
                    offset += len(self.reactions_separator) + len(reaction) + wide
                chat_map.append((num, None, False, reactions_map, None, None))
                first_reaction_line = False
                reaction_index += len(reactions_this)

        return chat, chat_format, chat_map


def generate_status_line(my_user_data, my_status, unread_count, typing, active_channel, action, tasks, tabs, tabs_format, format_status_line, format_rich, colors, my_role_color, status_sign, slowmode=None, vim_mode=None, limit_typing=30, use_nick=True, fun=True):
    """
    Generate status line according to provided formatting.
    Possible options for format_status_line:
        %global_name
        %username
        %status   # discord status if online, otherwise 'connecting' or 'offline'
        %custom_status
        %custom_status_emoji
        %pronouns
        %unreads   # '[N+ unreads]' if this channel has unread messages
        %typing
        %rich
        %server
        %channel
        %channel_no_tab - no text if there are tabs
        %action   # replying/editing/deleting
        %task   # currently running long task
        %tabs
        %slowmode   # 'slowmode {time}'
        %vim_mode   # [--NORMAL--] / [--INSERT--]
    Possible options for format_rich:
        %type
        %name
        %state
        %details
        # %small_text
        # %large_text
    length of the %typing string can be limited with limit_typing
    use_nick will make it use nick instead username whenever possible.
    """
    color_low = colors[8]
    color_standout = colors[9]
    # typing
    if "%typing" in format_status_line:
        if len(typing) == 0:
            typing_string = ""
        elif len(typing) == 1:
            typing_string = get_global_name(typing[0], use_nick)
            # -15 is for "(... is typing)"
            typing_string = typing_string[:limit_typing - 15]
            suffix = " is typing"
            typing_string = f"({typing_string.replace("\n ", ", ")}{suffix})"
        else:
            usernames = []
            for user in typing:
                usernames.append(get_global_name(user, use_nick))
            typing_string = "\n ".join(usernames)
            # -13 is for "( are typing)"
            if len(typing_string) > limit_typing - 13:
                # -16 is for "(+XX are typing)"
                break_index = len(typing_string[:limit_typing - 16].rsplit("\n", 1)[0])
                remaining = len(typing_string[break_index+2:].split("\n "))
                if len(typing[0]["username"]) > limit_typing - 16:
                    remaining -= 1   # correction when first user is cut
                typing_string = typing_string[:break_index] + f" +{remaining}"
            suffix = " are typing"
            typing_string = f"({typing_string.replace("\n ", ", ")}{suffix})"
    else:
        typing_string = ""

    # my rich presence
    if "%rich" in format_status_line:
        if my_status["activities"]:
            state = my_status["activities"][0]["state"][:limit_typing]
            details = my_status["activities"][0]["details"][:limit_typing]
            # sm_txt = my_status["activities"][0]["small_text"]
            # lg_txt = my_status["activities"][0]["large_text"]
            rich = (
                format_rich
                .replace("%type", ACTIVITY_VERBS[my_status["activities"][0]["type"]])
                .replace("%name", my_status["activities"][0]["name"])
                .replace("%state", state or "")
                .replace("%details", details or "")
                # .replace("%small_text", sm_txt or "")
                # .replace("%large_text", lg_txt or "")
            )
            if fun:
                rich = rich.replace("Metal", "🤘 Metal").replace("metal", "🤘 metal")
        else:
            rich = "No rich presence"
    else:
        rich = ""

    # action
    if "%action" in format_status_line:
        action_string = ""
        if action["type"] == 1:   # replying
            ping = ""
            if action["mention"]:
                ping = "(PING) "
            if action["global_name"]:
                name = action["global_name"]
            else:
                name = action["username"]
            action_string = f"Replying {ping}to {name}"
        elif action["type"] == 2:   # editing
            action_string = "Editing the message"
        elif action["type"] == 3:   # confirm deleting
            action_string = "Really delete the message? [Y/n]"
        elif action["type"] == 4:   # select from multiple links
            action_string = "Select link (type a number)"
        elif action["type"] == 5:   # select from multiple attachments
            action_string = "Select attachment link to download (type a number)"
        elif action["type"] == 6:   # select attachment media to play
            action_string = "Select attachment link to play (type a number)"
        elif action["type"] == 7:   # cancel all downloads
            action_string = "Really cancel all downloads/attachments? [Y/n]"
        elif action["type"] == 8:   # ask for upload path
            action_string = "Type file path to upload"
        elif action["type"] == 9:   # confirm hiding channel
            action_string = "Really hide this channel? [Y/n]"
        elif action["type"] == 10:   # select to which channel to go
            action_string = "Select channel/message to go to (type a number)"
        elif action["type"] == 11:   # reacting
            if action["global_name"]:
                name = action["global_name"]
            else:
                name = action["username"]
            action_string = f"Reacting to {name}"
        elif action["type"] == 12:   # select reaction to show details
            action_string = "Select reaction (type a number)"
    else:
        action_string = ""

    # running long tasks
    tasks = sorted(tasks, key=lambda x: x[1])
    if len(tasks) == 0:
        task = ""
    elif len(tasks) == 1:
        task = tasks[0][0]
    else:
        task = f"{tasks[0][0]} (+{len(tasks) - 1})"

    if my_status["custom_status_emoji"]:
        custom_status_emoji = str(my_status["custom_status_emoji"]["name"])
    else:
        custom_status_emoji = ""

    have_tabs = "%tabs" in format_status_line
    if not tabs:
        tabs = ""
        have_tabs = False

    if my_status["client_state"] == "online":
        warn_state = "[AFK]" if my_status["afk"] else ""
        warn_state_color = 19
        status = my_status["status"]
        if status == "online":
            status_color = 18
        elif status == "dnd":
            status_color = 20
        elif status == "idle":
            status_color = 19
        else:
            status_color = color_low
        status_dot_color = status_color
    else:
        status = my_status["client_state"]
        warn_state = f"[{status}]"
        if status == "connecting":
            status_color = 19
            status_dot_color = color_low
        else:   # offline/error
            status_color = 20
            status_dot_color = color_low
        warn_state_color = status_color

    guild = active_channel["guild_name"]

    if slowmode is None:
        slowmode = ""
    elif slowmode == 0:
        slowmode = "Slowmode"
    else:
        slowmode = f"Slowmode: {format_seconds(slowmode)}"

    if vim_mode is False:
        vim_mode = "[--NORMAL--]"
    elif vim_mode is True:
        vim_mode = "[--INSERT--]"
    else:
        vim_mode = ""

    sl_format = []
    sl_text = (
        format_status_line
        .replace("%global_name", get_global_name(my_user_data, False))
        .replace("%username", my_user_data["username"])
        .replace("%custom_status", str(my_status["custom_status"]))
        .replace("%custom_emoji", custom_status_emoji)
        .replace("%pronouns", str(my_user_data["pronouns"]))
        .replace("%rich", rich)
        .replace("%channel_no_tab", str("" if tabs else active_channel["channel_name"]))
        .replace("%channel", str(active_channel["channel_name"]))
        .replace("%action", action_string)
        .replace("%task", task)
        .replace("%tabs", tabs)
        .replace("%vim_mode", vim_mode)
        .replace("%app_name", APP_NAME)
    )
    unread_count = str(abs(unread_count)) + "+" if unread_count < 0 else unread_count
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%nick", get_global_name(my_user_data, True), my_role_color, None)
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%status_dot", status_sign, status_dot_color, None)
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%status", "status", status_color, 1 if status == "ERROR" else None)
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%warn_state", warn_state, warn_state_color, 1)
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%unreads", f"[{unread_count} unreads]" if unread_count else "", 19, None)
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%typing", typing_string, color_standout, None)
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%server", guild or "DM", color_standout, None)
    sl_text, sl_format = replace_formatted(sl_text, sl_format, "%slowmode", slowmode, color_low, 1)

    if have_tabs:
        pre_tab_len = len(sl_text.split(tabs)[0])
        for tab in tabs_format:
            sl_format.append((tab[0], tab[1], tab[2] + pre_tab_len, tab[3] + pre_tab_len))
    else:
        pre_tab_len = 0

    return sl_text, sl_format, pre_tab_len


def generate_tab_string(channel_cache, active_channel_id, read_state, format_tab, tabs_separator, limit_len, max_len, fill_line, colors):
    """
    Generate tabs list string according to provided formatting.
    Possible options for generate_tab_string:
        %num
        %name
        %server
    """
    tabs_separated = []
    tab_string_map = []   # [[start, end, tab_num], ...]   # tab_num = -1 - arrow left -2 - arrow right
    trimmed_left = False
    default_color = colors[0]

    if not channel_cache:
        return "", [], tab_string_map
    num_tabs = 0
    temp_tab = True
    for channel in channel_cache:
        if channel[2]:
            num_tabs += 1
            temp_tab = channel[3]   # in case there is only one tab
    if num_tabs <= 1 and temp_tab:
        return "", [], tab_string_map

    separators_len = max(0, num_tabs) * len(tabs_separator)
    available = max_len - separators_len
    if available > 0:
        limit_len = min(limit_len, max(MIN_TAB_LEN, available // num_tabs))

    active_tab = None
    tab_string_format = []   # [[color, attribute] ...]
    num = 0
    for tab in channel_cache:
        if not tab[2]:
            continue
        channel_id = tab[0]
        channel_name = replace_wide(tab[4], "")
        guild_name = replace_wide(tab[5], "")
        tab_text = (
            format_tab
            .replace("%num", str(num + 1))
            .replace("%name", channel_name)
            .replace("%server", guild_name)
        )
        if limit_len:
            tab_text = tab_text[:limit_len].center(limit_len)
        if fill_line:
            tab_text = tab_text.replace(" ", "─")
        tabs_separated.append(tab_text)

        ch_read_state = read_state.get(channel_id)
        if channel_id == active_channel_id:
            tab_string_format.append([6, 3 + bool(tab[3])])   # underline + color_tree_active (id 6)
        elif ch_read_state and ch_read_state["mentions"]:
            tab_string_format.append([8, 2 if tab[3] else None])   # red - color_tree_mentioned (id 8)
        elif is_unseen(ch_read_state):
            tab_string_format.append([None, 5 if tab[3] else 1])   # bold
        elif tab[3]:
            tab_string_format.append([None, 2])   # italic
        else:
            tab_string_format.append(None)

        # scroll to active if string is too long
        active_tab = num
        scroll_index = 0
        if channel_id == active_channel_id:
            while len(tabs_separator.join(tabs_separated)) >= max_len:
                if not tabs_separated:
                    break
                trimmed_left = True
                tabs_separated.pop(0)
                tab_string_format.pop(0)
                scroll_index += 1
        if (active_tab and num >= active_tab) and len(tabs_separator.join(tabs_separated)) >= max_len:
            break
        num += 1

    # add format start and end indexes
    prev_end = None
    tab_string_format_final = []   # [[color, attribute, start, end] ...]
    for num, tab in enumerate(tabs_separated):
        start = len(tabs_separator.join(tabs_separated[:num])) + bool(num) * len(tabs_separator) + 2 * trimmed_left
        end = start + len(tab)
        tab_string_map.append([start, end, num + scroll_index])
        if tab_string_format[num]:
            tab_string_format_final.append([tab_string_format[num][0], tab_string_format[num][1], start, end])
        if fill_line and prev_end is not None:
            tab_string_format_final.append([default_color, None, prev_end, start])
        prev_end = end

    tab_string = tabs_separator.join(tabs_separated)

    if trimmed_left:
        tab_string = f"< {tab_string}"
        tab_string_map.insert(0, [0, 1, -1])

    # trim right side of tab string
    if len(tab_string) > max_len:
        tab_string = tab_string[:max_len - 2 * (trimmed_left + 1)] + " >"
        tab_string_map.insert(0, [len(tab_string)-1, len(tab_string), -2])   # insert so it overrides other ranges
    elif len(tab_string) < max_len - 2:
        tab_string += tabs_separator
        tab_string_format_final.append([default_color, None, len(tab_string) - 1, len(tab_string)])

    return tab_string, tab_string_format_final, tab_string_map


def generate_prompt(my_user_data, active_channel, format_prompt, limit_prompt=15, vim_mode=None):
    """
    Generate prompt line according to provided formatting.
    Possible options for format_prompt_line:
        %global_name
        %username
        %server
        %channel
        %vim_mode
    """
    guild = active_channel["guild_name"]
    if vim_mode is False:
        vim_mode = "NORMAL"
    elif vim_mode is True:
        vim_mode = "INSERT"
    else:
        vim_mode = ""
    return (
        format_prompt
        .replace("%global_name", get_global_name(my_user_data, False)[:limit_prompt])
        .replace("%username", my_user_data["username"][:limit_prompt])
        .replace("%server", guild[:limit_prompt] if guild else "DM")
        .replace("%channel", str(active_channel["channel_name"])[:limit_prompt])
        .replace("%vim_mode", vim_mode)
    )


def generate_custom_prompt(text, format_prompt, limit_prompt=15):
    """Generate prompt with custom text"""
    prompt = format_prompt.replace("%global_name", text[:limit_prompt])
    if prompt != format_prompt:
        text = ""
    prompt = format_prompt.replace("%username", text[:limit_prompt])
    if prompt != format_prompt:
        text = ""
    prompt = format_prompt.replace("%server", text[:limit_prompt])
    if prompt != format_prompt:
        text = ""
    prompt = format_prompt.replace("%channel", text[:limit_prompt])
    if prompt != format_prompt:
        text = ""
    return prompt


def generate_timestamp_assist(value, prefix):
    """Generate assist for insert_timestamp command"""
    return (
        (generate_discord_timestamp(value, "t"), prefix + "<t:" + str(value) + ":t>"),
        (generate_discord_timestamp(value, "f"), prefix + "<t:" + str(value) + ":f>"),
        (generate_discord_timestamp(value, "F"), prefix + "<t:" + str(value) + ":F>"),
        (generate_discord_timestamp(value, "D"), prefix + "<t:" + str(value) + ":D>"),
        (generate_discord_timestamp(value, "S"), prefix + "<t:" + str(value) + ":S>"),
        (generate_discord_timestamp(value, "R"), prefix + "<t:" + str(value) + ":R>"),
    )


def generate_log(log, colors, max_width):
    """Generate log lines shown in chat area"""
    chat = []
    chat_format = []
    chat_map = []
    color_base = colors[0]
    color_low = colors[8]
    log_level_colors = (colors[9], 18, 19, 20, 20)
    for message in log:
        chat.extend(split_long_line(message, max_width, 4))
    for line in chat:
        line_format = [[color_base]]
        first_word = line.split(" ")[0]
        if first_word.count("-") == 3 and first_word.count(":") == 2:   # definitely a time string
            line_format.append([color_low, 0, len(first_word)])
        for i, level in enumerate(LOG_LELVELS):
            level_idx = line.find(level)
            if level_idx != -1:
                line_format.append([log_level_colors[i], level_idx, level_idx + len(level)])
                break
        chat_format.append(line_format)
        chat_map.append(None)
    chat = chat[::-1]
    chat_format = chat_format[::-1]
    return chat, chat_format, chat_map


def generate_about(about, colors, colors_formatted, max_width):
    """Generate about lines shown in chat area"""
    chat = []
    chat_format = []
    chat_map = []
    color_base = colors[0]
    color_url = colors_formatted[7][0][0]
    for line in about.split("\n"):
        if not line:
            chat.append("")
        else:
            chat.extend(split_long_line(line, max_width, 4))
    for num, line in enumerate(chat):
        if line.startswith("# "):
            chat[num] = line[2:].center(max_width)
            chat_format.append([[18]])   # green
            chat_map.append(None)
        elif line.startswith("https://"):
            chat[num] = line.center(max_width)
            start = chat[num].find(line)
            chat_format.append([[color_base], [color_url, start, start + len(line)]])
            this_line_ranges = (((start, start + len(line), line), ), None, None, None, None, None)
            chat_map.append((0, None, None, None, None, this_line_ranges))
        else:
            chat[num] = line.center(max_width)
            chat_format.append([[color_base]])
            chat_map.append(None)
    chat = chat[::-1]
    chat_format = chat_format[::-1]
    chat_map = chat_map[::-1]
    return chat, chat_format, chat_map


def generate_extra_line_upload(attachments, selected, max_len, colors):
    """
    Generate extra line containing attachments information, with format:
    Attachments: [attachment.name] - [Uploading/OK/Too-Large/Restricted/Failed], Selected:N, Total:N
    """
    color_standout = colors[9]
    if attachments:
        total = len(attachments)
        name = attachments[selected]["name"]
        state_color = 20
        match attachments[selected]["state"]:
            case 0:
                state = "Uploading"
                state_color = 19
            case 1:
                state = "OK"
                state_color = 18
            case 2:
                state = "Too Large"
            case 3:
                state = "Restricted"
            case 4:
                state = "Failed"
            case _:
                state = "Unknown"
        right = f" - {state}, Selected:{selected + 1}, Total:{total}"
        left = f" Attachments: {name}"[:max_len - len(right)]
        left_end = len(left) - 1
        total_idx = right.rfind(":") + 1
        selected_idx = right.rfind(":", 0, total_idx - 1) + 1
        line_format = [
            (color_standout, None, 13, left_end),
            (state_color, None, left_end + 3, left_end + 3 + len(state)),
            (color_standout, None, left_end + selected_idx, left_end + selected_idx + len(str(selected))),
            (color_standout, None, left_end + total_idx, left_end + total_idx + len(str(total))),
        ]
        return left + right, line_format
    return "", []


def generate_extra_line_ring(caller_name, max_len, bordered, colors):
    """Generate extra line containing information about incoming call"""
    max_len = max_len - bordered * 3
    color_standout = colors[9]
    left_text = f"{caller_name} is calling you, use commands: voice_*"
    right_text = "[Accept] [Reject]"
    max_str_length = max_len - len(right_text) - 3   # 3 for ...

    line_format = [
        (color_standout, None, 0, len(caller_name[:max_str_length])),
        (18, None, max_len - 17, max_len - 9),
        (20, None, max_len - 8, max_len),
    ]

    if len(left_text) + 1 + len(right_text) <= max_len:
        space_num = max_len - (len(left_text) + len(right_text))
        if bordered:
            filler = " " + "─" * (space_num - 2) + " "
        else:
            filler = " " * space_num
        return left_text + filler + right_text, line_format

    shortened_str = left_text[:max_str_length] + "..."
    return shortened_str + right_text, line_format


def generate_extra_line_call(call_participants, volume_in, volume_out, max_len, bordered, rtt, colors):   # noqa
    """Generate extra line containing information about ongoing call"""
    max_len = max_len - bordered * 3
    color_low = colors[8]
    color_standout = colors[9]
    left_text = "In the call: You"
    rtt_text = f"({min(rtt, 999.9):.1f}ms) " if rtt is not None else ""
    right_text = f"{rtt_text}[I:{(str(volume_in)+"%").center(4)} O:{(str(volume_out)+"%").center(4)}] [Leave]"
    max_str_length = max_len - len(right_text) - 3   # 3 for ...

    for participant in call_participants:
        left_text += f", {participant["name"]}"
        if len(left_text) + 1 + len(right_text) > max_len:
            break

    line_format = [
        (color_standout, None, 13, len(left_text[:max_str_length])),
        (color_standout, None, max_len - 23, max_len - 8),
        (20 if int(rtt) > 500 else 19 if rtt > 200 else color_low, None, max_len - 23 - len(rtt_text), max_len - 23),
        (20 if volume_out == 0 else None, None, max_len - 15, max_len - 9),
        (20 if volume_in == 0 else None, None, max_len - 22, max_len - 16),
        (20, None, max_len - 7, max_len),
    ]

    if len(left_text) + 1 + len(right_text) <= max_len:
        space_num = max_len - (len(left_text) + len(right_text))
        if bordered:
            filler = " " + "─" * (space_num - 2) + " "
        else:
            filler = " " * space_num
        return left_text + filler + right_text, line_format

    shortened_str = left_text[:max_str_length] + "..."
    return shortened_str + right_text, line_format


def generate_extra_window_call(call_participants, me_muted, colors, max_len):
    """Generate extra windows title and body as a list of voice call participants and their states"""
    title_line = "Voice call participants:"
    color_low = colors[8]
    color_standout = colors[9]
    body = []
    body_format = []
    body.append(f"Me - {"muted  " if me_muted else "unmuted"}")
    body_format.append([(color_low, 1 if me_muted else None, 5, 12)])
    for participant in call_participants:
        name = participant["name"]
        text = f" - {"muted  " if participant["muted"] else "unmuted"}"
        if participant["speaking"]:
            text += " - speaking"
        if len(participant["name"]) + len(text) > max_len:
            name = name[:-(len(participant["name"]) + len(text) - max_len)]
        body.append(name + text)
        line_format = [([(color_low, 1 if participant["muted"] else None, len(name) + 3, len(name) + 10)])]
        if participant["speaking"]:
            line_format.append(([(color_standout, None, len(name) + 10, len(name + text))]))
    return title_line, body, body_format


def generate_extra_window_profile(user_data, user_roles, presence, colors, max_len):
    """Generate extra window title and body for user profile view"""
    color_low = colors[8]
    color_standout = colors[9]
    title_line = ""
    body = []
    body_format = []

    # prepare user strings
    nick = ""
    if user_data["nick"]:
        nick = f"Nick: {user_data["nick"]}"
    global_name = ""
    if user_data["global_name"]:
        global_name = f"Name: {user_data["global_name"]}"
    if user_data["bot"]:
        username_string = "BOT name"
    else:
        username_string = "Username"
    username = f"{username_string}: {user_data["username"]}"
    pronouns = ""
    if user_data["pronouns"]:
        pronouns = f"Pronouns: {user_data["pronouns"]}"
    roles_string = ", ".join(user_roles)
    member_since = timestamp_from_snowflake(int(user_data["id"]), "%Y-%m-%d")

    # build title
    items = [nick, global_name, username, pronouns]
    complete = True
    for num, item in enumerate(items):
        if len(title_line + item) + 3 > max_len:
            complete = False
            break
        if item:
            title_line += f"{items[num]} | "
    title_line = title_line[:-3]
    items = items[num+complete:]
    if not title_line:
        title_line = items.pop(0)[:max_len]

    # add overflow from title line to body
    body_line = ""
    if items:
        add_newline = False
        for item in items:
            if item:
                body_line += f"{item} | "
                add_newline = True
        if add_newline:
            body_line += "\n"
    lines = split_long_line(body_line, max_len)
    body.extend(lines)
    body_format.extend([*[None] * len(lines)])

    # activity
    if presence:
        status = presence["status"].capitalize().replace("Dnd", "DnD")
        custom = ""
        if presence.get("custom_status_emoji"):
            status_emoji = presence["custom_status_emoji"]["name"]
            if not utils.is_emoji(status_emoji):
                status_emoji = f":{status_emoji}:"
            custom += f"{status_emoji} "
        if presence["custom_status"]:
            custom += presence["custom_status"]
        if custom:
            custom = f" - {custom}"
        text = f"Status: {status}{custom}"
        lines = split_long_line(text, max_len)
        body.extend(lines)
        color_status = 18 if status == "Online" else 19 if status == "Idle" else 20 if status == "DnD" else color_low
        body_format.extend(([(color_standout, 0, 0, 6), (color_status, 1, 8, len(status) + 8)],), *[None] * (len(lines) - 1))
    else:
        body.append("Status: Offline")
        body_format.append(([(color_standout, 0, 0, 6), (color_low, 1, 8, max_len)]))

    # misc
    if user_data["tag"]:
        body.append(f"Tag: {user_data["tag"]}")
        body_format.append(([(color_standout, 0, 0, 3)]))
    body.append(f"Member since: {member_since}"[:max_len])
    body_format.append(([(color_standout, 0, 0, 12)]))
    if user_data["joined_at"]:
        body.append(f"Joined: {user_data["joined_at"]}")
        body_format.append(([(color_standout, 0, 0, 6)]))

    # rich presences
    if presence:
        if presence["activities"]:
            body.append("")
            body_format.append(None)
        for activity in presence["activities"]:
            action = ACTIVITY_VERBS[activity["type"]]
            if activity["state"]:
                state = f"{activity["state"]}"
            else:
                state = ""
            duration = f"({format_seconds(int(time.time() - activity["start"]))})" if activity["start"] else ""
            body.append(f"{action} {activity["name"]} {duration}"[:max_len])
            body_format.append(([
                (color_standout, 0, 0, len(action) + len(activity["name"]) + 1),
                (color_low, 0, len(action) + len(activity["name"]) + 2, max_len),
            ]))
            body.append(f"  {state}")
            body_format.append(None)
            if activity["details"]:
                body.append(f"  {activity["details"]}")
                body_format.append(None)
            # for text in (activity["small_text"], activity["large_text"]):
            #     if not text:
            #         continue
            #     lines = split_long_line(body_line, max_len)
            #     body.extend(lines)
            #     body_format.extend([*[None] * len(lines)])
            body.append("")
            body_format.append(None)

    # misc
    if roles_string:
        roles = split_long_line(f"Roles: {roles_string}", max_len)
        body.extend(roles)
        body_format.extend([([(color_standout, 0, 0, 5)]), *[None] * (len(roles) - 1)])
        body.append("")
        body_format.append(None)
    if user_data["bio"]:
        body.append("Bio:")
        body_format.append(([(color_standout, 0, 0, 3)]))
        bio = split_long_line(user_data["bio"], max_len)
        body.extend(bio)
        body_format.extend([*[None] * len(bio)])

    return title_line, body, body_format


def generate_extra_window_channel(channel, voice_states, use_nick, colors, max_len):
    """Generate extra window title and body for channel info view"""
    color_low = colors[8]
    color_standout = colors[9]
    body = []
    body_format = []
    if channel["type"] == 2:   # voice channel
        title_line = f"Voice Channel: {channel["name"]}"[:max_len]
        allow_voice = channel.get("allow_voice", True)
        allow_speak = channel.get("allow_speak", True)
        if not allow_voice:
            body.append("No voice permission"[:max_len])
            body_format.append([(None, 1, 0, max_len)])
        elif not allow_speak:
            body.append("No speak permission"[:max_len])
            body_format.append([(None, 1, 0, max_len)])
        if channel["topic"]:
            body.append("Topic:")
            body_format.append(None)
            topic = split_long_line(channel["topic"], max_len)
            body.extend(topic)
            body_format.extend(([(color_low, 0, 0, max_len)],) * len(topic))
        else:
            body.append("No topic.")
            body_format.append(None)
        body.append("")
        body_format.append(None)
        if voice_states and len(voice_states) > 1:
            limit = f"/{channel["user_limit"]}" if channel["user_limit"] else ""
            text = f"{voice_states[0]}{limit} participant{"s" if voice_states[0] > 1 else ""}:"   # 0 is count
            body.append(text[:max_len])
            body_format.append([(color_standout, 0, 0, max_len)])
            for value in voice_states.values():
                if isinstance(value, int):
                    continue
                username, global_name, nick = value
                if (nick and use_nick) or global_name:
                    body.append(f"  {nick if nick else global_name} ({username})"[:max_len])
                    body_format.append([(color_low, 0, len(nick if nick else global_name) + 3, max_len)])
                else:
                    body.append(f"  {username}"[:max_len])
                    body_format.append(None)
        else:
            text = "No participants." + (f" ({channel["user_limit"]} max.)" if channel["user_limit"] else "")
            body.append(text[:max_len])
            if channel["user_limit"]:
                body_format.append([(color_low, 0, 17, max_len)])
            else:
                body_format.append(None)
        return title_line, body, body_format

    title_line = f"Channel: {channel["name"]}"[:max_len]
    no_embed = not channel.get("allow_attach", True)
    no_write = not channel.get("allow_write", True)
    if no_embed and no_write:
        body.append("No write and embed permissions"[:max_len])
        body_format.append([(None, 1, 0, max_len)])
    elif no_embed:
        body.append("No embed permissions"[:max_len])
        body_format.append([(None, 1, 0, max_len)])
    elif no_write:
        body.append("No write permissions"[:max_len])
        body_format.append([(None, 1, 0, max_len)])
    if channel["topic"]:
        body.append("Topic:")
        body_format.append(None)
        topic = split_long_line(channel["topic"], max_len)
        body.extend(topic)
        body_format.extend(([(color_low, 0, 0, max_len)],) * len(topic))
    else:
        body.append("No topic.")
        body_format.append(None)

    return title_line, body, body_format


def generate_extra_window_guild(guild, colors, max_len):
    """Generate extra window title and body for guild info view"""
    title_line = f"Server: {guild["name"]}"[:max_len]
    color_low = colors[8]
    color_standout = colors[9]
    body = []
    body_format = []
    body.append(f"Members: {guild["member_count"]}")
    body_format.append([(color_standout, 0, 9, 20)])
    if guild["description"]:
        body.append("Description:")
        body_format.append(None)
        description = split_long_line(guild["description"], max_len)
        body.extend(description)
        body_format.extend(([(color_low, 0, 0, max_len)],) * len(description))
    else:
        body.append("No description.")
        body_format.append(None)
    return title_line, body, body_format


def generate_extra_window_summaries(summaries, colors, max_len, channel_name=None):
    """Generate extra window title and body for summaries list view"""
    title_line = "Summaries:"
    color_low = colors[8]
    if channel_name:
        title_line = f"[{channel_name}] {title_line}"
    body = []
    body_format = []
    indexes = []
    if summaries:
        for summary in reversed(summaries):
            summary_date = timestamp_from_snowflake(int(summary["message_id"]), "%m-%d-%H:%M")
            summary_string = f"[{summary_date}] - {summary["topic"]}: {summary["description"]}"
            summary_lines = split_long_line(summary_string, max_len, align=16)
            indexes.append({
                "lines": len(summary_lines),
                "message_id": summary["message_id"],
            })
            body.extend(summary_lines)
            body_format.extend(([(color_low, None, 0, len(summary_date) + 2)], *[None] * (len(summary_lines) - 1)))
    else:
        body = ["No summaries."]
    return title_line, body, body_format, indexes


def generate_extra_window_search(query, messages, roles, channels, blocked, total_msg, config, colors, max_len, limit_lines=3, newline_len=4, pinned=False):
    """
    Generate extra window title and body for message search view
    Possible options for format_message:
        %content
        %username
        %global_name
        %date
        %channel
    """
    limit_username = config["limit_username"]
    limit_channel_name = config["limit_channel_name"]
    use_nick = config["use_nick_when_available"]
    convert_timezone = config["convert_timezone"]
    blocked_mode = config["blocked_mode"]
    format_date = config["format_forum_timestamp"]
    emoji_as_text = config["emoji_as_text"]
    format_message = config["format_search_message"]
    use_global_name = "%global_name" in format_message
    color_low = colors[8]
    color_standout = colors[9]

    if pinned:
        title_line = f"Pinned messages ({total_msg}):"
    else:
        title_line = f"Search results: {total_msg} messages"

    body = []
    body_format = []
    indexes = []
    if messages:
        for message in messages:

            # skip blocked messages
            if blocked_mode and message["user_id"] in blocked:
                indexes.append({
                    "lines": 0,
                    "message_id": message["id"],
                })
                continue

            global_name = get_global_name(message, use_nick) if use_global_name else ""

            channel_name = "Unknown"
            channel_id = message["channel_id"]
            fill_ch_name = True
            for channel in channels:
                if channel["id"] == channel_id:
                    channel_name = channel["name"]
                    break
            if not channels:
                channel_name = "DM"
                fill_ch_name = False

            content = ""
            if message["content"]:
                content = message["content"]
                if emoji_as_text:
                    content = utils.demojize(content)
                content = replace_spoilers(content)
                content = replace_bracketed_urls(content)
                content, _ = replace_discord_emoji(content)
                content, _ = replace_mentions(content, message["mentions"], global_name=use_global_name, use_nick=use_nick)
                content, _ = replace_roles(content, roles)
                content = replace_discord_url(content)
                content, _ = replace_channels(content, channels)
                content, _ = replace_timestamps(content, convert_timezone)
                content, _ = replace_markdown_urls(content, ())

            for embed in message["embeds"]:
                if embed["url"] and not embed.get("hidden") and embed["url"] not in content:
                    if content:
                        content += "\n"
                    content += f"[{clean_type(embed["type"])} embed]: {embed["url"]}"

            # skip empty messages
            if not content:
                indexes.append({
                    "lines": 0,
                    "message_id": message["id"],
                })
                continue
            message_string = (
                format_message
                .replace("%username", normalize_string(message["username"], limit_username, emoji_safe=True))
                .replace("%global_name", normalize_string(global_name, limit_username, emoji_safe=True))
                .replace("%date", generate_timestamp(message["timestamp"], format_date, convert_timezone))
                .replace("%channel", normalize_string(channel_name, limit_channel_name, emoji_safe=True, fill=fill_ch_name))
            )
            pre_content_len = len(message_string.split("%content")[0])
            message_string = message_string.replace("%content", content)

            message_lines = split_long_line(message_string, max_len, align=newline_len)
            message_lines = message_lines[:limit_lines]
            indexes.append({
                "lines": len(message_lines),
                "message_id": message["id"],
                "channel_id": message["channel_id"],
            })
            body.extend(message_lines)
            first_line_format = [(color_low, 1, 0, limit_channel_name), ((color_low, None, limit_channel_name, pre_content_len))]
            if query:
                for m in re.finditer(re.escape(query), message_lines[0][pre_content_len:], re.IGNORECASE):
                    first_line_format.append((color_standout, 1, m.start() + pre_content_len, m.end() + pre_content_len))
            body_format.append(first_line_format)
            for line in message_lines[1:]:
                line_format = []
                if query:
                    for m in re.finditer(re.escape(query), line, re.IGNORECASE):
                        line_format.append((color_standout, 1, m.start(), m.end()))
                body_format.append(line_format)

    else:
        body = ["No messages found."]
    return title_line, body, body_format, indexes


def generate_extra_window_search_ext(extensions, colors, max_len):
    """Generate extra window title and body for extensions search view"""
    title_line = f"Extensions search results: {len(extensions)} extensions"
    color_low = colors[8]
    color_standout = colors[9]
    body = []
    body_format = []
    for extension in extensions:
        official = " (official)" if extension[3] else ""
        description = str(extension[2]).replace("An extension for endcord discord TUI client, that ", "")
        description = description.capitalize()
        name = extension[1] + official + " - "
        body.append(normalize_string(name + description, max_len, emoji_safe=True, dots=True, fill=False))
        if extension[3]:
            body_format.append([
                (color_standout, None, len(extension[1]) + 1, len(extension[1]) + len(official)),
                (color_low, None, len(name) - 1, len(name) + len(description)),
            ])
        else:
            body_format.append([(color_low, None, len(name) - 1, len(name) + len(description))])
    return title_line, body, body_format


def generate_extra_window_text(title_text, body_text, max_len):
    """Generate extra window title and body for summaries list view"""
    title_line = title_text[:max_len]
    body = split_long_line(body_text, max_len)
    return title_line, body


def generate_extra_window_assist(found, assist_type, max_len, placeholder_emoji=False):
    """Generate extra window title and body for assist"""
    body = []
    prefix = ""
    if assist_type == 1:
        title_line = "Channel assist:"
        prefix = "#"
    elif assist_type == 2:
        title_line = "Username/role assist:"
        prefix = "@"
    elif assist_type == 3:
        title_line = "Emoji assist:"
        # prefix handled externally
    elif assist_type == 4:
        title_line = "Sticker assist:"
    elif assist_type == 5:
        title_line = "Command:"
    elif assist_type == 6:
        title_line = "App command:"
    elif assist_type == 7:
        title_line = "File select:"
    elif assist_type == 8:
        title_line = "GIF search:"
    else:
        title_line = "Unknown"
    for item in found:
        if placeholder_emoji and assist_type == 3 and item[1].startswith("<:"):
            if item[0].startswith("** "):
                body.append(f"**    - {item[0][3:]}"[:max_len])
            else:
                body.append(f"    - {item[0]}"[:max_len])
        else:
            body.append(f"{prefix}{item[0]}"[:max_len])
    if not body:
        body = ["No matches"]
    return title_line[:max_len], body


def generate_extra_window_reactions(reaction, details, colors, max_len):
    """Generate extra window title and body for reactions"""
    title_line = f"Users who reacted {reaction["emoji"]}: "
    color_low = colors[8]
    body = []
    body_format = []
    for user in details:
        line = f"{user["global_name"]} ({user["username"]})"[:max_len]
        body.append(line)
        body_format.append([(color_low, None, len(user["global_name"]) + 1, len(line))])
    return title_line[:max_len], body, body_format


def generate_extra_window_stats(data, texts, colors, max_len):
    """Generate extra window for stats command"""
    title_line = "Client stats:"
    color_standout = colors[9]
    body = []
    body_format = []
    for num, text in enumerate(texts):
        value = data[num]
        if isinstance(value, tuple):
            line = f"{text}: {value[0]} ({value[1]})"[:max_len]
            body.append(line)
            body_format.append([
                (color_standout, None, len(text) + 2, len(text) + 2 + len(str(value[0]))),
                (color_standout, None, len(text) + len(str(value[0])) + 4, len(line) - 1)],
            )
        elif value is None:
            body.append(text[:max_len])
            body_format.append([])
        else:
            line = f"{text}: {value}"[:max_len]
            body.append(line)
            body_format.append([(color_standout, None, len(text) + 2, len(line))])
    return title_line[:max_len], body, body_format


def generate_forum(threads, blocked, max_length, colors, colors_formatted, config):
    """
    Generate forum according to provided formatting.
    Possible options for forum_format:
        %thread_name
        %timestamp
        %msg_num
    Possible options for format_one_reaction:
        %reaction
        %count
    Possible options for format_timestamp:
        same as format codes for datetime package
    Possible options for blocked_mode:
        0 - no blocking
        1 - mask blocked messages
        2 - hide blocked messages
    limit_thread_name normalizes length of thread name, by cropping them or appending spaces. Set to None to disable.
    use_nick will make it use nick instead global_name whenever possible.
    """
    forum_thread_format = config["format_forum"]
    forum_format_timestamp = config["format_forum_timestamp"]
    color_blocked = [colors[2]]
    color_format_forum = colors_formatted[9]   # 19 is unused
    blocked_mode = config["blocked_mode"]
    limit_thread_name = config["limit_thread_name"]
    convert_timezone = config["convert_timezone"]

    forum = []
    forum_format = []
    for thread in threads:
        owner_id = thread["owner_id"]

        # handle blocked messages
        if blocked_mode and owner_id in blocked:
            if blocked_mode == 1:
                thread["username"] = "blocked"
                thread["global_name"] = "blocked"
                thread["nick"] = "blocked"

        if thread["timestamp"]:
            timestamp = generate_timestamp(thread["timestamp"], forum_format_timestamp, convert_timezone)
        else:
            placeholder_timestamp = generate_timestamp("2015-01-01T00:00:00.000000+00:00", forum_format_timestamp)
            timestamp = normalize_string("Unknown", len(placeholder_timestamp))

        thread_line = (
            forum_thread_format
            .replace("%thread_name", normalize_string(thread["name"], limit_thread_name, emoji_safe=True))
            .replace("%timestamp", timestamp)
            .replace("%msg_count", normalize_int_str(thread["message_count"], 3))
        )
        thread_line = normalize_string(thread_line, max_length, emoji_safe=True, dots=True)
        forum.append(thread_line)

        if thread["owner_id"] in blocked:
            forum_format.append([color_blocked])
        else:
            forum_format.append(color_format_forum)

    return forum, forum_format


def generate_member_list(member_list_raw, guild_roles, width, use_nick, status_sign, activity_icons, emoji_safe, colors, fun=True):
    """Generate member list"""
    # colors: 18 - green, 19 - orange, 20 - red
    color_low = colors[8]
    color_standout = colors[9]
    if not member_list_raw:
        return ["No members".center(width-1, " ")], [[]]
    member_list = []
    member_list_format = []
    filler = " " * width
    first = True
    for member in member_list_raw:
        this_format = []
        if "id" in member:

            # format text
            global_name = get_global_name(member, use_nick)
            text = f"{status_sign} {global_name}"

            # get status color
            if member["status"] == "dnd":
                this_format.append([20, None, 0, 2])
            elif member["status"] == "idle":
                this_format.append([19, None, 0, 2])
            elif member["status"] == "offline":
                text = f"  {global_name}"
            else:   # online
                this_format.append([18, None, 0, 2])

            # get role color
            member_roles = member["roles"]
            for role in guild_roles:
                if role["id"] in member_roles:
                    if role.get("color_id"):
                        this_format.append([role["color_id"], None, 2, width])
                    break
            member_list.append(normalize_string(text, width-1, emoji_safe=True))
            member_list_format.append(this_format)

            if member["activities"]:
                activities = member["activities"]
                activity_type = activities[0][0]
                verb = ACTIVITY_VERBS[activity_type] if emoji_safe else activity_icons[activity_type]
                activity_title = activities[0][1]
                if fun and not emoji_safe and activity_type == 2 and "metal" in activity_title.lower():
                    verb = "🤘"
                status_text = f"  {verb}{f"+{len(activities)-1}" if len(activities) > 1 else ""} {activity_title}"
                member_list.append(normalize_string(status_text, width-1, emoji_safe=True) + filler)
                len_verb = len(verb) + (2 if len(activities) > 1 else 0)
                member_list_format.append(((color_standout, None, 0, 2 + len_verb), (color_low, None, 2 + len_verb, width)))
            elif member["custom_status"]:
                status_text = f"  {member["custom_status"]}"
                member_list.append(normalize_string(status_text, width-1, emoji_safe=True) + filler)
                member_list_format.append(((color_low, None, 0, width),))

        else:   # user group
            text = "Unknown group"
            group_id = member["group"]
            count = member["count"]
            if group_id in ("online", "offline"):
                text = group_id.capitalize()
            else:
                for role in guild_roles:
                    if role["id"] == group_id:
                        text = role["name"]
            if count:
                count_text = f" ({count})"
                this_format.append([color_low, None, len(text), len(text) + len(count_text)])
                text += count_text
            if not first:
                member_list.append(filler)
                member_list_format.append(this_format)
            else:
                first = False
            member_list.append(normalize_string(text, width-1, emoji_safe=True))
            member_list_format.append(this_format)

    return member_list, member_list_format


def generate_message_notification(data, channels, roles, guild_name, my_data, convert_timezone, use_global_name=False, use_nick=False):
    """Generate message notification title and body"""
    if data["guild_id"]:
        # find guild and channel name
        channel_id = data["channel_id"]
        channel_name = None

        for channel in channels:
            if channel["id"] == channel_id and channel.get("permitted"):
                channel_name = channel["name"]
                break
        if guild_name and channel_name:
            title = f"{get_global_name(data, use_nick) if use_global_name else data["username"]} ({guild_name} #{channel_name})"
        else:
            title = get_global_name(data, use_nick) if use_global_name else data["username"]
    else:
        title = get_global_name(data, use_nick) if use_global_name else data["username"]

    if data["content"]:
        body = replace_spoilers(data["content"])
        content = replace_bracketed_urls(body)
        body, _ = replace_discord_emoji(body)
        body, _ = replace_mentions(body, data["mentions"] + [my_data], global_name=use_global_name, use_nick=use_nick)
        body, _ = replace_roles(body, roles)
        body = replace_discord_url(body)
        body, _ = replace_channels(body, channels)
        body, _ = replace_timestamps(body, convert_timezone)
        content, _ = replace_markdown_urls(body, ())
    elif data.get("embeds"):
        num = len(data["embeds"])
        if num == 1:
            embed_type = data["embeds"][0]["type"].split("/")[0]
            embed_type = ("an " if embed_type.startswith(("a", "e", "i", "o", "u")) else "a ") + embed_type
            body = f"Sent {embed_type}"
        else:
            body = f"Sent {num} attachments"
    elif data.get("stickers"):
        body = "Sent a sticker"
    else:
        body = "Unknown content"

    return title, body


def generate_tree(dms, guilds, threads, read_state, guild_folders, activities, collapsed, voice_states, active_channel_id, config, folder_names=[], safe_emoji=False, max_width=0):
    """
    Generate channel tree according to provided formatting.
    tree_format keys:
        0XX - Guild folder (same level as guilds but hides them when collapsed)
        1XX - DM/Guild (top level drop down menu)
        2XX - category (second level drop down menu)
        3XX - channel (not drop-down)
        4XX - thread
        5XX - channel/forum (third level drop down menu)
        X0X - normal
        X1X - muted
        X2X - mentioned
        X3X - unread
        X4X - active channel
        X5X - active and mentioned
        XX0 - collapsed drop-down
        XX1 - uncollapsed drop-down
        XX2 - online DM
        XX3 - idle DM
        XX4 - DnD DM
        1000 - end of folder drop down
        1100 - end of top level drop down
        1200 - end of second level drop down
        1300 - end of third level drop down
    Voice channels are ignored.
    """
    dd_vline = config["tree_drop_down_vline"]
    dd_hline = config["tree_drop_down_hline"]
    dd_intersect = config["tree_drop_down_intersect"]
    dd_corner = config["tree_drop_down_corner"]
    dd_pointer = config["tree_drop_down_pointer"]
    dd_thread = config["tree_drop_down_thread"]
    dd_forum = config["tree_drop_down_forum"]
    dd_folder = config["tree_drop_down_folder"]
    voice_char = config["tree_drop_down_voice"]
    dm_status_char = config["tree_dm_status"]
    show_folders = config["tree_show_folders"]
    uncollapse_threads = config["remember_collapsed_channels"]
    intersection = f"{dd_intersect}{dd_hline*2}"   # default: "|--"
    pass_by = f"{dd_vline}  "   # default: "|  "
    intersection_end = f"{dd_corner}{dd_hline*2}"   # default: "\\--"
    pass_by_end = f"{pass_by}{intersection_end}"   # default: "|  \\--"
    intersection_thread = f"{dd_intersect}{dd_hline}{dd_thread}"   # default: "|-<"
    end_thread = f"{dd_corner}{dd_hline}{dd_thread}"   # default: "\\-<"
    tree = []
    tree_format = []
    tree_metadata = []
    tree.append(f"{dd_pointer} Direct Messages")
    code = 101
    if 0 in collapsed:
        code = 100
    tree_format.append(code)
    tree_metadata.append({
        "id": 0,
        "type": -1,
        "name": None,
        "muted": False,
        "parent_index": None,
    })
    dm_pings = 0
    for dm in dms:
        name = dm["name"]
        ch_read_state = read_state.get(dm["id"])
        unseen_dm = is_unseen(ch_read_state)
        mentioned_dm = unseen_dm and ch_read_state["mentions"]
        muted = dm.get("muted", False)
        active = (dm["id"] == active_channel_id)
        if safe_emoji:
            name = replace_wide(name)
        code = 300
        # get dm status
        if len(dm["recipients"]) == 1:
            for activity in activities:
                if activity["id"] == dm["recipients"][0]["id"]:
                    status = activity["status"]
                    if status == "online":
                        code += 2
                    elif status == "idle":
                        code += 3
                    elif status == "dnd":
                        code += 4
                    else:
                        break
                    name = dm_status_char + " " + name
                    break
        mention_count = generate_count(len(ch_read_state["mentions"])) if unseen_dm else ""
        if dm["type"] == 3:
            states = voice_states.get(dm["id"])
            if states and len(states) > 1:
                mention_count = f"{mention_count} [{min(states[0], 99)}]"
        tree.append(normalize_string_with_suffix(f"{intersection} {name}", mention_count, max_width, emoji_safe=not (safe_emoji)))
        dm_pings += len(ch_read_state["mentions"]) if unseen_dm else 0
        if muted and not active:
            code += 10
        elif active and not mentioned_dm:
            code += 40
        elif active and mentioned_dm:
            code += 50
        elif mentioned_dm:
            code += 20
            tree_format[0] += 20
        elif unseen_dm:
            code += 30
            tree_format[0] += 30
        if not active and 0 in collapsed:
            tree_format[0] == 100
        tree_format.append(code)
        tree_metadata.append({
            "id": dm["id"],
            "type": dm["type"],
            "name": dm["name"],
            "muted": muted,
            "parent_index": 0,
        })
    tree.append("END-DMS-DROP-DOWN")
    tree_format.append(1100)
    tree_metadata.append(None)
    if dm_pings:
        mention_count = generate_count(dm_pings)
        tree[0] = normalize_string_with_suffix(f"{dd_pointer} Direct Messages", mention_count, max_width)

    # sort guilds and folders
    guilds_sorted = []
    guilds_used_index = []
    for num_f, folder in enumerate(guild_folders):
        if show_folders and folder["id"] and folder["id"] != "MISSING":
            for folder_name in folder_names:
                if folder_name["id"] == folder["id"]:
                    name = folder_name["name"]
                    break
            else:
                name = f"Folder-{num_f}"
            guilds_sorted.append({
                "folder": True,
                "id": folder["id"],
                "name": name,
            })
        for guild_id in folder["guilds"]:
            for num, guild in enumerate(guilds):
                if guild["guild_id"] == guild_id:
                    guilds_sorted.append(guilds[num])
                    guilds_used_index.append(num)
                    break
        if show_folders and folder["id"] and folder["id"] != "MISSING":
            guilds_sorted.append({
                "folder": True,
                "id": folder["id"],
                # no name indicates its end
            })
    # add unsorted guilds
    for num, guild in enumerate(guilds):
        if num not in guilds_used_index:
            guilds_sorted.append(guild)

    # generator loop
    in_folder = None
    for guild in guilds_sorted:
        # handle folders
        if "folder" in guild:
            if "name" in guild:
                in_folder = guild["id"]
                tree.append(f"{dd_folder} {guild["name"]}")
                tree_format.append(int(guild["id"] not in collapsed))
                tree_metadata.append({
                    "id": guild["id"],
                    "type": -2,
                    "name": guild["name"],
                    "muted": False,
                    "parent_index": None,
                })
            else:   # ending are already added when sorting guilds and folders
                in_folder = None
                tree.append("END-FOLDER-DROP-DOWN")
                tree_format.append(1000)
                tree_metadata.append(None)
            continue

        # prepare data
        muted_guild = guild.get("muted", False)
        unseen_guild = False
        ping_guild = 0
        for guild_th in threads:
            if guild_th["guild_id"] == guild["guild_id"]:
                threads_guild = guild_th["channels"]
                break
        else:
            threads_guild = []

        # sort categories and channels
        categories = []
        for channel in guild["channels"]:
            if channel["type"] == 4:
                # categories are also hidden if they have no visible channels
                muted = channel.get("muted", False)
                hidden = 1
                if channel.get("hidden"):
                    hidden = 2   # forced hidden
                else:
                    hidden = 1   # hidden unless there are channels
                # using local storage instead for collapsed
                # collapsed = category_set["collapsed"]
                categories.append({
                    "id": channel["id"],
                    "name": channel["name"],
                    "position": channel["position"],
                    "channels": [],
                    "muted": muted,
                    "collapsed": False,
                    "hidden": hidden,
                    "unseen": False,
                    "ping": 0,
                })

        # separately sort channels in their categories
        uncategorized_channels = []
        for channel in guild["channels"]:
            if channel["type"] in (0, 2, 5, 15, 16):
                # find this channel threads, if any
                for channel_th in threads_guild:
                    if channel_th["channel_id"] == channel["id"]:
                        threads_ch = channel_th["threads"]
                        break
                else:
                    threads_ch = []
                ch_read_state = read_state.get(channel["id"])
                unseen_ch = is_unseen(ch_read_state)
                mentioned_ch = len(ch_read_state["mentions"]) if unseen_ch else 0
                for category in categories:
                    if channel["parent_id"] == category["id"]:
                        muted_ch = channel.get("muted", False)
                        hidden_ch = channel.get("hidden", False)
                        # hide restricted channels now because they can be marked as unseen/ping
                        if not channel.get("permitted", False):
                            hidden_ch = True
                        if not (category["muted"] or category["hidden"] == 2 or hidden_ch or muted_ch):
                            if unseen_ch:
                                category["unseen"] = True
                                unseen_guild = True
                            category["ping"] += mentioned_ch
                            ping_guild += mentioned_ch
                        if not hidden_ch and category["hidden"] != 2:
                            category["hidden"] = False
                        active = (channel["id"] == active_channel_id)
                        data = {
                            "id": channel["id"],
                            "name": channel["name"],
                            "position": channel["position"],
                            "muted": muted_ch,
                            "hidden": hidden_ch,
                            "unseen": unseen_ch,
                            "ping": mentioned_ch,
                            "active": active,
                            "threads": threads_ch,
                        }
                        if channel["type"] in (15, 16):   # saving ram
                            data["forum"] = True
                        if channel["type"] == 2:
                            data["voice"] = True
                            data["user_limit"] = channel["user_limit"]
                            channel["unseen"] = False
                        category["channels"].append(data)
                        break
                else:
                    muted_ch = channel.get("muted", False)
                    hidden_ch = channel.get("hidden", False)
                    if not channel.get("permitted", False):
                        hidden_ch = True
                    ping_guild += mentioned_ch
                    active = channel["id"] == active_channel_id
                    uncategorized_channels.append({
                        "id": channel["id"],
                        "name": channel["name"],
                        "position": -1,   # uncategorized on top
                        "channels": None,
                        "muted": muted_ch,
                        "hidden": hidden_ch,
                        "unseen": unseen_ch,
                        "ping": mentioned_ch,
                        "active": active,
                    })
        categories += uncategorized_channels

        # sort categories by position key
        categories = sorted(categories, key=lambda x: x["position"])

        # add guild to the tree
        name = guild["name"]
        if safe_emoji:
            name = replace_wide(name)
        mention_count = generate_count(ping_guild)
        tree.append(normalize_string_with_suffix(f"{dd_pointer} {name}", mention_count, max_width, emoji_safe=not (safe_emoji)))
        code = 101
        if muted_guild:
            code += 10
        elif ping_guild:
            code += 20
        elif unseen_guild:
            code += 30
        if guild["guild_id"] in collapsed:
            code -= 1
        tree_format.append(code)
        guild_index = len(tree_format) - 1
        tree_metadata.append({
            "id": guild["guild_id"],
            "type": -1,
            "name": guild["name"],
            "muted": muted_guild,
            "parent_index": None,
        })

        # mark folder as unread/mention
        if in_folder:
            for num, folder in enumerate(tree_metadata):
                if folder and folder["id"] == in_folder:
                    if ping_guild and not muted_guild and (not tree_format[num] or tree_format[num] == 30):
                        tree_format[num] = 20 + (tree_format[num] % 10)
                    elif unseen_guild and not muted_guild:
                        tree_format[num] = 30 + (tree_format[num] % 10)
                    break

        # add categories to the tree
        for category in categories:
            if not category["hidden"]:
                if category["channels"]:
                    category_index = len(tree_format)

                    # sort channels by position key
                    category["channels"] = sorted(category["channels"], key=lambda x: x["position"])

                    # add to the tree
                    name = category["name"]
                    if safe_emoji:
                        name = replace_wide(name)
                    mention_count = generate_count(category["ping"])
                    tree.append(normalize_string_with_suffix(f"{intersection}{dd_pointer} {name}", mention_count, max_width, emoji_safe=not (safe_emoji)))
                    code = 201
                    if category["muted"]:
                        code += 10
                    elif category["ping"]:
                        code += 20
                    elif category["unseen"]:
                        code += 30
                    if category["collapsed"] or category["id"] in collapsed:
                        code -= 1
                    tree_format.append(code)
                    tree_metadata.append({
                        "id": category["id"],
                        "type": 4,
                        "name": category["name"],
                        "muted": category["muted"],
                        "parent_index": guild_index,
                    })

                    # add channels to the tree
                    category_channels = category["channels"]
                    for channel in category_channels:
                        if not channel["hidden"]:
                            name = channel["name"]
                            forum = channel.get("forum")
                            channel_threads = channel.get("threads", [])
                            channel_index = len(tree_format)
                            if safe_emoji:
                                name = replace_wide(name)
                            mention_count = generate_count(channel["ping"])
                            if forum:
                                tree.append(normalize_string_with_suffix(f"{pass_by}{intersection}{dd_forum} {name}", mention_count, max_width, emoji_safe=not (safe_emoji)))
                            elif channel_threads:
                                tree.append(normalize_string_with_suffix(f"{pass_by}{intersection}{dd_pointer} {name}", mention_count, max_width, emoji_safe=not (safe_emoji)))
                            elif channel.get("voice"):
                                states = voice_states.get(channel["id"])
                                if (states and len(states) > 1) or channel["user_limit"]:
                                    count = min(states[0], 99) if states else 0
                                    limit = f"/{channel["user_limit"]}" if channel["user_limit"] else ""
                                    call_count = f"[{count}{limit}]"
                                else:
                                    call_count = ""
                                tree.append(normalize_string_with_suffix(f"{pass_by}{intersection}{voice_char} {name}", call_count, max_width, emoji_safe=not (safe_emoji)))
                            else:
                                tree.append(normalize_string_with_suffix(f"{pass_by}{intersection} {name}", mention_count, max_width, emoji_safe=not (safe_emoji)))
                            if channel_threads:
                                code = 500
                            else:
                                code = 300
                            if channel["muted"] and not channel["active"]:
                                code += 10
                            elif channel["active"] and channel["ping"]:
                                code += 50
                            elif channel["active"]:
                                code += 40
                            elif channel["ping"]:
                                code += 20
                            elif channel["unseen"]:
                                code += 30
                            if channel_threads and uncollapse_threads and (channel["id"] in collapsed):
                                code += 1
                            tree_format.append(code)
                            tree_metadata.append({
                                "id": channel["id"],
                                "type": 15 if forum else 2 if channel.get("voice") else 0,
                                "name": channel["name"],
                                "muted": channel["muted"],
                                "parent_index": category_index,
                            })

                            # add channel threads to the tree
                            for thread in channel_threads:
                                joined = thread["joined"]
                                if not joined and forum:
                                    # skip non-joined threads for forum
                                    continue
                                name = thread["name"]
                                thread_id = thread["id"]
                                active = (thread_id == active_channel_id)
                                ch_read_state = read_state.get(thread_id)
                                unseen = is_unseen(ch_read_state)
                                mentioned = unseen and ch_read_state["mentions"]
                                if safe_emoji:
                                    name = replace_wide(name)
                                tree.append(f"{pass_by}{pass_by}{intersection_thread} {name}")
                                code = 400
                                if (thread["muted"] or not joined) and not active:
                                    code += 10
                                elif active and mentioned:
                                    code += 50
                                elif active:
                                    code += 40
                                elif mentioned:
                                    code += 20
                                elif unseen:
                                    code += 30
                                tree_format.append(code)
                                tree_metadata.append({
                                    "id": thread["id"],
                                    "type": thread["type"],
                                    "name": thread["name"],
                                    "muted": thread["muted"],
                                    "parent_index": channel_index,
                                })
                            if channel_threads:
                                tree.append(f"{pass_by}{pass_by}END-CHANNEL-DROP-DOWN")
                                tree_format.append(1300)
                                tree_metadata.append(None)

                    tree.append(f"{pass_by}END-CATEGORY-DROP-DOWN")
                    tree_format.append(1200)
                    tree_metadata.append(None)
                else:
                    name = category["name"]
                    if safe_emoji:
                        name = replace_wide(name)
                    mention_count = generate_count(category["ping"])
                    tree.append(normalize_string_with_suffix(f"{intersection} {name}", mention_count, max_width, emoji_safe=not (safe_emoji)))
                    code = 300
                    if muted and not category["active"]:
                        code += 10
                    elif category["ping"]:
                        code += 20
                    elif category["unseen"]:
                        code += 30
                    tree_format.append(code)
                    category["muted"] = muted
                    tree_metadata.append({
                        "id": category["id"],
                        "type": 0,
                        "name": category["name"],
                        "muted": category["muted"],
                        "parent_index": guild_index,
                    })

        tree.append("END-GUILD-DROP-DOWN")
        tree_format.append(1100)
        tree_metadata.append(None)

    # add drop-down corners
    for num, code in enumerate(tree_format):
        if code >= 1000:
            if code == 1300 and (tree_format[num - 1] // 100) % 10 == 4:   # thread end if there are threads
                tree[num - 1] = f"{pass_by}{pass_by}{end_thread}{tree[num - 1][9:]}"
            elif tree[num - 1][:4] != f"{intersection}{dd_pointer}":
                if (tree_format[num - 1] < 500 or tree_format[num - 1] > 599) and tree[num][:3] == pass_by:
                    # skipping collapsed forums
                    tree[num - 1] = pass_by_end + tree[num - 1][6:]
                elif tree[num - 1][:3] == intersection:
                    tree[num - 1] = intersection_end + tree[num - 1][3:]
            if code == 1100 and tree_format[num - 1] == 1200:
                for back, _ in enumerate(tree_format):
                    if tree[num - back - 1][:3] == pass_by:
                        tree[num - back - 1] = "   " + tree[num - back - 1][3:]
                    else:
                        tree[num - back - 1] = intersection_end + tree[num - back - 1][3:]
                        break
            if code == 1200 and tree_format[num - 1] == 1300:
                for back, _ in enumerate(tree_format):
                    if tree[num - back - 2][3:6] == pass_by:
                        tree[num - back - 2] = f"{pass_by}   {tree[num - back - 2][6:]}"
                    else:
                        tree[num - back - 2] = pass_by_end + tree[num - back - 2][6:]
                        break
    return tree, tree_format, tree_metadata
