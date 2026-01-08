import curses
import importlib.util
import logging
import re
import time
from datetime import UTC, datetime

import emoji

from endcord.wide_ranges import WIDE_RANGES

logger = logging.getLogger(__name__)
DAY_MS = 24*60*60*1000
DISCORD_EPOCH_MS = 1420070400000
TREE_EMOJI_REPLACE = "▮"


TIME_DIVS = [1, 60, 3600, 86400, 2678400, 31190400]
TIME_UNITS = ["second", "minute", "hour", "day", "month", "year"]

match_emoji = re.compile(r"(?<!\\):.+:")
match_d_emoji = re.compile(r"<(.?):(.*?):(\d*?)>")
match_mention = re.compile(r"<@(\d*?)>")
match_role = re.compile(r"<@&(\d*?)>")
match_channel = re.compile(r"<#(\d*?)>")
match_timestamp = re.compile(r"<t:(\d+)(:[tTdDfFR])?>")
match_channel_id = re.compile(r"(?<=<#)\d*?(?=>)")
match_escaped_md = re.compile(r"\\(?=[^a-zA-Z\d\s])")
match_md_spoiler = re.compile(r"(?<!\\)((?<=\|))?\|\|[^_]+?\|\|")
match_md_code_snippet = re.compile(r"(?<!`|\\)`[^`]+`")
match_md_code_block = re.compile(r"(?s)```.*?```")
match_url = re.compile(r"https?:\/\/\w+(\.\w+)+[^\s)\]>]*")
match_discord_channel_url = re.compile(r"https:\/\/discord(?:app)?\.com\/channels\/(\d*)\/(\d*)(?:\/(\d*))?")
match_discord_channel_combined = re.compile(r"<#(\d*?)>|https:\/\/discord(?:app)?\.com\/channels\/(\d*)\/(\d*)(?:\/(\d*))?")
match_sticker_id = re.compile(r"<;\d*?;>")
match_md_all = re.compile(
    r"""
    (?<!\\)(
        (__[^_]+__)          |   # underline
        (\*\*[^\*]+\*\*)     |   # bold
        # (~~[^~]+~~)        |   # strikethrough - unused
        (?<!\w)_[^_]+_(?!\w) |   # italic _
        (\*[^\*]+\*)             # italic *
    )
    """,
    re.VERBOSE,
)


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
    return emoji.demojize(text)


def demojize_message(message):
    """Safely demojize message"""
    message["content"] = demojize(message["content"])
    message["username"] = demojize(message["username"])
    message["global_name"] = demojize(message.get("global_name"))
    if message["referenced_message"]:
        referenced = message["referenced_message"]
        referenced["content"] = demojize(referenced["content"])
        referenced["username"] = demojize(referenced["username"])
        referenced["global_name"] = demojize(referenced.get("global_name"))
    for embed in message["embeds"]:
        if embed["type"] == "rich":
            embed["url"] = demojize(embed["url"])
    return message


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


def format_seconds(seconds):
    """Convert seconds to hh:mm:ss"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours:
        parts.append(f"{hours:02d}")
    if minutes or hours:
        parts.append(f"{minutes:02d}")
    parts.append(f"{secs:02d}")
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
    else:
        format_string = "%d %b %Y %H:%M"
    return datetime.strftime(time_obj, format_string)


def find_timestamp(full_string, timestamp):
    """Return timestamp indexes of a matching timestamp"""
    start_index = full_string.find(timestamp)
    if start_index == -1:
        return None
    end_index = start_index + len(timestamp) - 1
    return start_index, end_index


def move_by_indexes(data, indexes, start=0):
    """Move format by indexes"""
    for format_part in data:
        for index in indexes:
            if index < format_part[start]:
                format_part[start] -= 1
            if index < format_part[start+1]:
                format_part[start+1] -= 1
            else:
                break
    return data


def emoji_name(emoji_char):
    """Return emoji name from its Unicode"""
    return emoji.demojize(emoji_char).replace(":", "")


def replace_emoji_string(line):
    """Replace emoji string (:emoji:) with single character"""
    return re.sub(match_emoji, TREE_EMOJI_REPLACE, line)


def binary_search(codepoint, ranges):
    """Binary-search a tuple of (start, end) ranges and return 1 if codepoint is inside any range, else 0"""
    low = 0
    high = len(ranges) - 1

    if codepoint < ranges[0][0] or codepoint > ranges[high][1]:
        return 0

    while low <= high:
        mid = (low + high) // 2
        start, end = ranges[mid]

        if codepoint > end:
            low = mid + 1
        elif codepoint < start:
            high = mid - 1
        else:
            return 1

    return 0


def limit_width_wch(text, max_width):
    """Limit width of the text on the screen, because "wide characters" are 2 characters wide"""
    total_width = 0
    for i, ch in enumerate(text):
        character = ord(ch)
        if 32 <= character < 0x7f:
            char_width = 1
        else:
            char_width = 1 + binary_search(character, WIDE_RANGES)
        if total_width + char_width > max_width:
            return text[:i], total_width
        total_width += char_width
    return text, total_width


def len_wch(text):
    """Return real display width for a string"""
    total_width = 0
    for ch in text:
        character = ord(ch)
        if 32 <= character < 0x7f:
            total_width += 1
        else:
            total_width += 1 + binary_search(character, WIDE_RANGES)
    return total_width


# use cython if available, ~5 times faster
if importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.formatter"):
    from endcord_cython.formatter import len_wch as len_wch_cython
    from endcord_cython.formatter import limit_width_wch as limit_width_wch_cython
    def limit_width_wch(text, max_width):
        """Limit width of the text on the screen, because "wide characters" are 2 characters wide"""
        return limit_width_wch_cython(text, max_width, WIDE_RANGES)
    def len_wch(text):
        """Calculate lenght of each character and store it in a bool list"""
        return len_wch_cython(text, WIDE_RANGES)


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


def normalize_string_count(input_string, max_length, dots=False, fill=True):
    """
    Normalize length of string, by cropping it or appending spaces.
    Set max_length to None to disable.
    Also return count of wide characters.
    """
    input_string = str(input_string)
    if not max_length:
        return input_string
    if dots:
        dots = len(input_string) > max_length
    input_string, length = limit_width_wch(input_string, max_length)
    diff = length - len(input_string[:max_length])
    if fill:
        input_string += " " * (max_length - length)
    if dots:
        return input_string[:-3] + " " * (len_wch(input_string[-3:]) - 3) + "...", diff
    return input_string, diff


def replace_discord_emoji(text):
    """
    Transform emoji strings into nicer looking ones:
    `some text <:emoji_name:emoji_id> more text` --> `some text :emoji_name: more text`
    """
    result = []
    last_pos = 0
    for match in re.finditer(match_d_emoji, text):
        result.append(text[last_pos:match.start()])
        result.append(f":{match.group(2)}:")
        last_pos = match.end()
    result.append(text[last_pos:])
    return "".join(result)


def replace_mentions(text, usernames_ids, global_name=False, use_nick=False):
    """
    Transforms mention string into nicer looking one:
    `some text <@user_id> more text` --> `some text @username more text`
    """
    result = []
    last_pos = 0
    for match in re.finditer(match_mention, text):
        result.append(text[last_pos:match.start()])
        for user in usernames_ids:
            if match.group(1) == user["id"]:
                result.append(f"@{get_global_name(user, use_nick) if global_name else user["username"]}")
                break
        last_pos = match.end()
    result.append(text[last_pos:])
    return "".join(result)


def replace_roles(text, roles_ids):
    """
    Transforms roles string into nicer looking one:
    `some text <@role_id> more text` --> `some text @role_name more text`
    """
    result = []
    last_pos = 0
    for match in re.finditer(match_role, text):
        result.append(text[last_pos:match.start()])
        for role in roles_ids:
            if match.group(1) == role["id"]:
                result.append(f"@{role["name"]}")
                break
        last_pos = match.end()
    result.append(text[last_pos:])
    return "".join(result)


def replace_discord_url(text):
    """Replace discord url for channel and message"""
    result = []
    last_pos = 0
    for match in re.finditer(match_discord_channel_url, text):
        result.append(text[last_pos:match.start()])
        if match.group(3):
            result.append(f"<#{match.group(2)}>>MSG")
        else:
            result.append(f"<#{match.group(2)}>")
        last_pos = match.end()
    result.append(text[last_pos:])
    return "".join(result)


def replace_channels(text, channels_ids):
    """
    Transforms channels string into nicer looking one:
    `some text <#channel_id> more text` --> `some text #channel_name more text`
    """
    result = []
    last_pos = 0
    for match in re.finditer(match_channel, text):
        result.append(text[last_pos:match.start()])
        for channel in channels_ids:
            if match.group(1) == channel["id"]:
                result.append(f"#{channel["name"]}")
                break
        else:
            result.append("*#unknown*")
        last_pos = match.end()
    result.append(text[last_pos:])
    return "".join(result)


def replace_timestamps(text, timezone):
    """
    Transforms timestamp string into nicer looking one:
    `some text <t:timestamp:type> more text` --> discord specified format
    """
    result = []
    last_pos = 0
    for match in re.finditer(match_timestamp, text):
        result.append(text[last_pos:match.start()])
        timestamp = match.group(1)
        discord_format = match.group(2)
        if discord_format:
            discord_format = discord_format[1]
        formatted_time = generate_discord_timestamp(timestamp, discord_format, timezone=timezone)
        result.append(f"`{formatted_time}`")
        last_pos = match.end()
    result.append(text[last_pos:])
    return "".join(result)


def replace_escaped_md(line, except_ranges=[]):
    r"""
    Replace escaped markdown characters.
    eg "\:" --> ":"
    """
    indexes = []
    corr = 0
    for match in re.finditer(match_escaped_md, line):
        start = match.start()
        end = match.end()
        skip = False
        for except_range in except_ranges:
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


def replace_spoilers_oneline(line):
    """Replace spoiler: ||content|| with ACS_BOARD characters"""
    for _ in range(10):   # lets have some limits
        string_match = re.search(match_md_spoiler, line)
        if not string_match:
            break
        start = string_match.start()
        end = string_match.end()
        line = line[:start] + "▒" * (end - start) + line[end:]
    return line


def format_md_all(line, content_start, except_ranges):
    """
    Replace all supported formatted markdown strings and return list of their formats.
    This should be called only after curses has initialized color.
    Strikethrough is apparently not supported by curses.
    Formatting is not performed inside except_ranges.
    """
    line_format = []
    indexes = []
    for _ in range(10):   # lets have some limits
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
        for except_range in except_ranges:
            start_r = except_range[0]
            end_r = except_range[1]
            # if this match is inside excepted range
            if (start > start_r and start < end_r) or (end > start_r and end < end_r):
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
            if format_part[1] > start:
                format_part[1] -= format_len
                format_part[2] -= format_len
            if format_part[2] >= end:
                format_part[2] -= 2 * format_len
            # merge formats
            if format_part[1] == start and format_part[2] == true_end and format_part[1] != attribute:
                format_part[0] |= attribute
                done = True
            # add to format inside
            elif (format_part[1] >= start and format_part[2] <= true_end) and format_part[0] != attribute:
                format_part[0] |= attribute
            # inherit from format around
            elif (format_part[1] < start and format_part[2] > true_end) and format_part[0] != attribute:
                attribute |= format_part[0]
        if not done:
            line_format.append([attribute, start, end - 2 * format_len])

    # sort by format start so tui can draw nested format on top of previous one
    line_format = sorted(line_format, key=lambda x: x[1], reverse=True)
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
        if format_range[1] >= line_len or format_range[2] < newline_len:
            continue
        if format_range[1] >= newline_len:
            if format_range[2] < line_len:
                line_format.append([format_range[0], format_range[1], format_range[2]])
            else:
                line_format.append([format_range[0], format_range[1], line_len])
        elif format_range[2] < line_len:
            line_format.append([format_range[0], newline_len + quote*2, format_range[2]])
        else:
            line_format.append([format_range[0], newline_len + quote*2, line_len])
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


def urls_multiline_one_line(urls_range, line_len, newline_len, quote=False):
    """Generate ranges of urls for one line"""
    line_ranges = []
    for num, url_range in enumerate(urls_range):
        if url_range[0] >= line_len or url_range[1] < newline_len:
            continue
        if url_range[0] >= newline_len:
            if url_range[1] < line_len:
                line_ranges.append([url_range[0], url_range[1], num])
            else:
                line_ranges.append([url_range[0], line_len, num])
        elif url_range[1] < line_len:
            line_ranges.append([newline_len + quote*2, url_range[1], num])
        else:
            line_ranges.append([newline_len + quote*2, line_len, num])
    return line_ranges


def split_long_line(line, max_len, align=0):
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
    """Repace sed like backreference with python re like brckreference """
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


def generate_chat(messages, roles, channels, max_length, my_id, my_roles, member_roles, colors, colors_formatted, blocked, last_seen_msg, show_blocked, config):
    """
    Generate chat according to provided formatting.
    Message shape:
        format_reply (message that is being replied to)
        format_message (main message line)
        format_newline (if main message is too long, it goes on newlines)
        format_reactions (reactions added to main message)
    Possible options for format_message:
        %content
        %username
        %global_name
        %timestamp
        %edited
    Possible options for format_newline:
        %content   # remainder from previous line
        %timestamp
    Possible options for format_reply:
        %content
        %username
        %global_name
        %timestamp   # of replied message
    Possible options for format_reactions:
        %timestamp   # of message
        %reactions   # all reactions after they pass through format_one_reaction
    Possible options for format_one_reaction:
        %reaction
        %count
    Possible options for format_timestamp:
        same as format codes for datetime package
    Possible options for blocked_mode:
        0 - no blocking
        1 - mask blocked messages
        2 - hide blocked messages
    limit_username normalizes length of username and global_name, by cropping them or appending spaces. Set to None to disable.
    Returned indexes correspond to each message as how many lines it is covering.
    use_nick will make it use nick instead global_name whenever possible.
    """

    # load from config
    format_message = config["format_message"]
    format_newline = config["format_newline"]
    format_reply = config["format_reply"]
    format_interaction = config["format_interaction"]
    format_reactions = config["format_reactions"]
    format_one_reaction = config["format_one_reaction"]
    format_timestamp = config["format_timestamp"]
    edited_string = config["edited_string"]
    reactions_separator = config["reactions_separator"]
    limit_username = config["limit_username"]
    use_nick = config["use_nick_when_available"]
    convert_timezone = config["convert_timezone"]
    blocked_mode = config["blocked_mode"]
    keep_deleted = config["keep_deleted"]
    date_separator = config["chat_date_separator"]
    format_date = config["format_date"]
    emoji_as_text = config["emoji_as_text"]
    quote_character = config["quote_character"]
    trim_embed_url_size = max(config["trim_embed_url_size"], 20)
    use_global_name = "%global_name" in format_message

    chat = []
    chat_format = []
    indexes = []
    chat_map = []   # ((num, username:(st, end), is_reply, reactions:((st, end), ...), date:(st, end), url:(st, end, index)), ...)
    wide_map = []
    len_edited = len(edited_string)
    enable_separator = format_date and date_separator
    have_unseen_messages_line = False
    # load colors
    color_default = [colors[0]]
    color_blocked = [colors[2]]
    color_deleted = [colors[3]]
    color_pending = [colors[4]]
    color_separator = [colors[5]]
    color_code = colors[6]
    color_chat_edited = colors_formatted[4][0]
    color_mention_chat_edited = colors_formatted[12][0]
    color_chat_url = colors_formatted[5][0][0]
    color_mention_chat_url = colors_formatted[13][0][0]
    color_spoiler = colors_formatted[6][0][0]
    color_mention_spoiler = colors_formatted[14][0][0]
    # load formatted colors: [[id], [id, start, end]...]
    color_message = colors_formatted[0]
    color_newline = colors_formatted[1]
    color_reply = colors_formatted[2]
    color_reactions = colors_formatted[3]
    color_mention_message = colors_formatted[8]
    color_mention_newline = colors_formatted[9]
    color_mention_reply = colors_formatted[10]
    color_mention_reactions = colors_formatted[11]

    placeholder_timestamp = generate_timestamp("2015-01-01T00:00:00.000000+00:00", format_timestamp)
    placeholder_message = (format_message
        .replace("%username", " " * limit_username)
        .replace("%global_name", " " * limit_username)
        .replace("%timestamp", placeholder_timestamp)
        .replace("%edited", "")
        .replace("%content", "")
    )
    pre_content_len = len(placeholder_message) - 1
    timestamp_range = find_timestamp(placeholder_message, placeholder_timestamp)
    pre_name_len = len(format_message
        .replace("%username", "\n")
        .replace("%global_name", "\n")
        .replace("%timestamp", placeholder_timestamp)
        .split("\n")[0],
    ) - 1
    newline_len = len(format_newline
        .replace("%username", normalize_string("Unknown", limit_username))
        .replace("%global_name", normalize_string("Unknown", limit_username))
        .replace("%timestamp", placeholder_timestamp)
        .replace("%content", ""),
        )
    pre_reaction_len = len(
        format_reactions
        .replace("%timestamp", placeholder_timestamp)
        .replace("%reactions", ""),
    ) - 1
    end_name = pre_name_len + limit_username + 1
    len_messages = len(messages)

    for num, message in enumerate(messages):
        if not message:   # failsafe
            continue
        temp_chat = []   # stores only one multiline message
        temp_format = []
        temp_chat_map = []
        temp_wide_map = []
        mentioned = False
        edited = message.get("edited")   # failsafe
        user_id = message.get("user_id")
        selected_color_spoiler = color_spoiler
        disable_formatting = False

        # select base color
        color_base = color_default
        for mention in message["mentions"]:
            if mention["id"] == my_id:
                mentioned = True
                selected_color_spoiler = color_mention_spoiler
                break
        for role in message["mention_roles"]:
            if role in my_roles:
                mentioned = True
                selected_color_spoiler = color_mention_spoiler
                break
        if "pending" in message:
            color_base = color_pending
            selected_color_spoiler = color_pending
            disable_formatting = True

        # skip deleted
        if "deleted" in message:
            if keep_deleted:
                color_base = color_deleted
                selected_color_spoiler = color_deleted
                disable_formatting = True
            else:
                continue

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
        if blocked_mode and user_id in blocked and not show_blocked:
            if blocked_mode == 1:
                message["username"] = "blocked"
                message["global_name"] = "blocked"
                message["nick"] = "blocked"
                message["content"] = "Blocked message"
                message["embeds"] = []
                message["stickers"] = []
                color_base = color_blocked
            else:
                indexes.append(0)
                temp_chat_map.append(None)
                continue   # to not break message-to-chat conversion

        # date separator
        try:
            if enable_separator and day_from_snowflake(message["id"]) != day_from_snowflake(messages[num+1]["id"]):
                # if this message is 1 day older than next message (up - past message)
                date = generate_timestamp(message["timestamp"], format_date, convert_timezone)
                # keep text always in center
                filler = max_length - len(date)
                filler_l = filler // 2
                filler_r = filler - filler_l
                temp_chat.append(f"{date_separator * filler_l}{date}{date_separator * filler_r}")
                temp_format.append([color_separator])
                temp_chat_map.append(None)
        except IndexError:
            pass

        # unread message separator
        try:
            if not have_unseen_messages_line and date_separator and last_seen_msg and (num == len_messages-1 or (int(messages[num+1]["id"]) <= int(last_seen_msg))):
                # keep text always in center
                filler = max_length - 3
                filler_l = filler // 2
                filler_r = filler - filler_l
                temp_chat.append(f"{date_separator * filler_l}New{date_separator * filler_r}")
                temp_format.append([color_deleted])
                temp_chat_map.append(None)
                have_unseen_messages_line = True
        except IndexError:
            pass

        # replied message line
        if message["referenced_message"]:
            ref_message = message["referenced_message"].copy()
            if ref_message["id"]:
                if blocked_mode and ref_message["user_id"] in blocked and not show_blocked:
                    ref_message["username"] = "blocked"
                    ref_message["global_name"] = "blocked"
                    ref_message["nick"] = "blocked"
                    ref_message["content"] = "Blocked message"
                    reply_color_format = color_blocked
                for member in member_roles:
                    if member["user_id"] == ref_message["user_id"]:
                        if not ref_message["nick"]:
                            ref_message["nick"] = member.get("nick")
                        break
                global_name = get_global_name(ref_message, use_nick)
                reply_embeds = ref_message["embeds"].copy()
                content = ""
                if ref_message["content"]:
                    content, _ = replace_escaped_md(ref_message["content"])
                    content = replace_spoilers_oneline(content)
                    content = replace_discord_emoji(content)
                    content = replace_mentions(content, ref_message["mentions"], global_name=use_global_name, use_nick=use_nick)
                    content = replace_roles(content, roles)
                    content = replace_discord_url(content)
                    content = replace_channels(content, channels)
                    content = replace_timestamps(content, convert_timezone)
                    if emoji_as_text:
                        content = emoji.demojize(content)
                if reply_embeds:
                    for embed in reply_embeds:
                        embed_url = embed["url"]
                        if embed_url and not embed.get("hidden") and embed_url not in content:
                            if content:
                                content += "\n"
                            if "main_url" not in embed:   # its attachment
                                if trim_embed_url_size:
                                    embed_url = trim_string(embed_url, trim_embed_url_size)
                                content += f"[{clean_type(embed["type"])} attachment]: {embed_url}"
                            elif embed["type"] == "rich":
                                content += f"[rich embed]: {embed_url}"
                            else:
                                if trim_embed_url_size:
                                    embed_url = trim_string(embed_url, trim_embed_url_size)
                                content += f"[{clean_type(embed["type"])} embed]: {embed_url}"
                reply_line = lazy_replace(format_reply, "%username", lambda: normalize_string(ref_message["username"], limit_username, emoji_safe=True))
                reply_line = lazy_replace(reply_line, "%global_name", lambda: normalize_string(global_name, limit_username, emoji_safe=True))
                reply_line = lazy_replace(reply_line, "%timestamp", lambda: generate_timestamp(ref_message["timestamp"], format_timestamp, convert_timezone))
                reply_line = lazy_replace(reply_line, "%content", lambda: content.replace("\r", " ").replace("\n", " "))
            else:
                reply_line = lazy_replace(format_reply, "%username", lambda: normalize_string("Unknown", limit_username))
                reply_line = lazy_replace(reply_line, "%global_name", lambda: normalize_string("Unknown", limit_username))
                reply_line = reply_line.replace("%timestamp", placeholder_timestamp)
                reply_line = lazy_replace(reply_line, "%content", lambda: ref_message["content"].replace("\r", "").replace("\n", ""))
            reply_line, wide = normalize_string_count(reply_line, max_length, dots=True)
            if wide:
                temp_wide_map.append(len(temp_chat))
            temp_chat.append(reply_line)
            if disable_formatting or reply_color_format == color_blocked:
                temp_format.append([color_base])
            elif mentioned:
                temp_format.append(color_mention_reply)
            else:
                temp_format.append(color_reply)
            temp_chat_map.append((num, None, True, None, None, None, None))

        # bot interaction
        elif message["interaction"]:
            interaction_line = (
                format_interaction
                .replace("%username", message["interaction"]["username"][:limit_username])
                .replace("%global_name", get_global_name(message["interaction"], use_nick)[:limit_username])
                .replace("%command", message["interaction"]["command"])
            )
            interaction_line, wide = normalize_string_count(interaction_line, max_length, dots=True)
            if wide:
                temp_wide_map.append(len(temp_chat))
            temp_chat.append(interaction_line)
            if disable_formatting or reply_color_format == color_blocked:
                temp_format.append([color_base])
            elif mentioned:
                temp_format.append(color_mention_reply)
            else:
                temp_format.append(color_reply)
            temp_chat_map.append((num, None, False, None, None, None, None))

        # main message
        quote = False
        global_name = get_global_name(message, use_nick) if use_global_name else ""
        content = ""
        if "poll" in message:
            message["content"] = format_poll(message["poll"])
        if message["content"]:
            content = replace_discord_emoji(message["content"])
            content = replace_mentions(content, message["mentions"], global_name=use_global_name, use_nick=use_nick)
            content = replace_roles(content, roles)
            content = replace_discord_url(content)
            content = replace_channels(content, channels)
            content = replace_timestamps(content, convert_timezone)
            if emoji_as_text:
                content = emoji.demojize(content)
            if content.startswith("> "):
                content = quote_character + " " + content[2:]
                quote = True
        for embed in message["embeds"]:
            embed_url = embed["url"]
            if embed_url and not embed.get("hidden") and embed_url not in content:
                if content:
                    content += "\n"
                if "main_url" not in embed:   # its attachment
                    if trim_embed_url_size:
                        embed_url = trim_string(embed_url, trim_embed_url_size)
                    content += f"[{clean_type(embed["type"])} attachment]: {embed_url}"
                elif embed["type"] == "rich":
                    content += f"[rich embed]:\n{embed_url}"
                else:
                    if trim_embed_url_size:
                        embed_url = trim_string(embed_url, trim_embed_url_size)
                    content += f"[{clean_type(embed["type"])} embed]: {embed_url}"
        for sticker in message["stickers"]:
            sticker_type = sticker["format_type"]
            if content:
                content += "\n"
            if sticker_type == 1:
                content += f"[png sticker] (can be opened): {sticker["name"]}"
            elif sticker_type == 2:
                content += f"[apng sticker] (can be opened): {sticker["name"]}"
            elif sticker_type == 3:
                content += f"[lottie sticker] (cannot be opened): {sticker["name"]}"
            else:
                content += f"[gif sticker] (can be opened): {sticker["name"]}"

        message_line = lazy_replace(format_message, "%username", lambda: normalize_string(message["username"], limit_username, emoji_safe=True))
        message_line = lazy_replace(message_line, "%global_name", lambda: normalize_string(global_name, limit_username, emoji_safe=True))
        message_line = lazy_replace(message_line, "%timestamp", lambda: generate_timestamp(message["timestamp"], format_timestamp, convert_timezone))
        message_line = message_line.replace("%edited", edited_string if edited else "")
        message_line = message_line.replace("%content", content)

        # find all code snippets and blocks
        code_snippets = []
        code_blocks = []
        for match in re.finditer(match_md_code_snippet, message_line):
            code_snippets.append([match.start(), match.end()])
        for match in re.finditer(match_md_code_block, message_line):
            code_blocks.append([match.start(), match.end()])
        except_ranges = code_snippets + code_blocks

        # find all urls
        urls = []
        if color_chat_url:
            for match in re.finditer(match_url, message_line):
                start = match.start()
                end = match.end()
                skip = False
                for except_range in except_ranges:
                    start_r = except_range[0]
                    end_r = except_range[1]
                    if start > start_r and start < end_r and end > start_r and end <= end_r:
                        skip = True
                        break
                if not skip:
                    urls.append([start, end])

        # find all spoilers
        spoilers = []
        for match in re.finditer(match_md_spoiler, message_line):
            spoilers.append([match.start(), match.end()])
        spoiled = message.get("spoiled")
        if spoiled:
            spoilers = [value for i, value in enumerate(spoilers) if i not in spoiled]   # exclude spoiled messages

        # find all markdown and correct format indexes
        message_line, md_format, md_indexes = format_md_all(message_line, pre_content_len, except_ranges + urls)
        if md_indexes:
            code_snippets = move_by_indexes(code_snippets, md_indexes)
            code_blocks = move_by_indexes(code_blocks, md_indexes)
            urls = move_by_indexes(urls, md_indexes)
            spoilers = move_by_indexes(spoilers, md_indexes)
        message_line, escaped_indexes = replace_escaped_md(message_line, except_ranges + urls)
        if escaped_indexes:
            code_snippets = move_by_indexes(code_snippets, escaped_indexes)
            code_blocks = move_by_indexes(code_blocks, escaped_indexes)
            urls = move_by_indexes(urls, escaped_indexes)
            spoilers = move_by_indexes(spoilers, escaped_indexes)
            md_format = move_by_indexes(md_format, escaped_indexes, start=1)

        # limit message_line and split to multiline
        newline_sign = False
        newline_index = max_length
        quote_nl = True
        len_wch_message_line = len_wch(message_line)
        wide = len_wch_message_line != len(message_line)   # whole message could be different
        if len_wch_message_line > max_length:
            newline_index = len(limit_width_wch(message_line, max_length)[0].rsplit(" ", 1)[0])   # split line on space
            # if there is \n on current line, use its position to split line
            if "\n" in message_line[:max_length]:
                newline_index = message_line.index("\n")
                quote = False
                newline_sign = True
                split_on_space = 0
            else:
                newline_text = lazy_replace(format_newline, "%timestamp", lambda: generate_timestamp(message["timestamp"], format_timestamp, convert_timezone))
                newline_text = newline_text.replace("%content", "")
                if newline_index <= len(newline_text):
                    newline_index = max_length - (len_wch(message_line[:max_length]) - len(message_line[:max_length]))
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
        code_block_format = format_multiline_one_line_end(code_blocks, newline_index+1, 0, color_code, max_length-1, quote)
        if code_block_format:
            message_line = message_line.ljust(max_length-1)

        if wide:
            temp_wide_map.append(len(temp_chat))
        temp_chat.append(message_line)
        urls_this_line = urls_multiline_one_line(urls, newline_index+1, 0, quote)
        spoilers_this_line = urls_multiline_one_line(spoilers, newline_index+1, 0, quote)
        temp_chat_map.append((num, (pre_name_len, end_name), False, None, timestamp_range, urls_this_line, spoilers_this_line))

        # formatting
        len_message_line = len(message_line)
        if disable_formatting:
            temp_format.append([color_base])
        elif mentioned:
            format_line = color_mention_message[:]
            format_line += format_multiline_one_line_format(md_format, newline_index+1, 0, quote)
            format_line += format_multiline_one_line(urls, newline_index+1, 0, color_mention_chat_url, quote)
            format_line += format_multiline_one_line(code_snippets, newline_index+1, 0, color_code, quote)
            format_line += code_block_format
            format_line += format_spoilers
            if alt_role_color:
                format_line.append([alt_role_color, pre_name_len, end_name])
            if edited and not next_line:
                format_line.append(color_mention_chat_edited + [len_message_line - len_edited, len_message_line])
            temp_format.append(format_line)
        else:
            format_line = color_message[:]
            format_line += format_multiline_one_line_format(md_format, newline_index+1, 0, quote)
            format_line += format_multiline_one_line(urls, newline_index+1, 0, color_chat_url, quote)
            format_line += format_multiline_one_line(code_snippets, newline_index+1, 0, color_code, quote)
            format_line += code_block_format
            format_line += format_spoilers
            if role_color:
                format_line.append([role_color, pre_name_len, end_name])
            if edited and not next_line:
                format_line.append([*color_chat_edited, len_message_line - len_edited, len_message_line])
            temp_format.append(format_line)

        # newline
        line_num = 1
        quote_nl = quote_nl and quote
        while next_line and line_num < 200:   # safety against memory leaks
            this_quote = False
            if quote:
                full_content = quote_character + " " + next_line
                extra_newline_len = 2
                this_quote = True
            else:
                full_content = next_line
                extra_newline_len = 0
            new_line = lazy_replace(format_newline, "%username", lambda: normalize_string(message["username"], limit_username, emoji_safe=True))
            new_line = lazy_replace(new_line, "%global_name", lambda: normalize_string(global_name, limit_username, emoji_safe=True))
            new_line = lazy_replace(new_line, "%timestamp", lambda: generate_timestamp(message["timestamp"], format_timestamp, convert_timezone))
            new_line = new_line.replace("%content", full_content)

            # correct index for each new line
            content_index_correction = newline_len + extra_newline_len - 1 + (not split_on_space) - newline_index - quote_nl*2
            for url in urls:
                url[0] += content_index_correction
                url[1] += content_index_correction
            for spoiler in spoilers:
                spoiler[0] += content_index_correction
                spoiler[1] += content_index_correction
            for code_snippet in code_snippets:
                code_snippet[0] += content_index_correction
                code_snippet[1] += content_index_correction
            for code_block in code_blocks:
                code_block[0] += content_index_correction
                code_block[1] += content_index_correction
            for md in md_format:
                md[1] += content_index_correction
                md[2] += content_index_correction
            quote_nl = False

            # limit new_line and split to next line
            newline_sign = False
            if len_wch(new_line) > max_length - bool(code_block_format):
                newline_index = len(limit_width_wch(new_line, max_length - bool(code_block_format))[0].rsplit(" ", 1)[0])   # split line on space
                if "\n" in new_line[:max_length]:
                    newline_index = new_line.index("\n")
                    quote = False
                    newline_sign = True
                    split_on_space = 0
                elif newline_index <= newline_len + 2*quote:
                    newline_index = max_length - bool(code_block_format) - (len_wch(message_line[:max_length]) - len(message_line[:max_length]))
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
            format_spoilers = format_multiline_one_line(spoilers, len_new_line, newline_len, selected_color_spoiler, this_quote)
            for spoiler_range in format_spoilers:
                start = spoiler_range[1]
                end = spoiler_range[2]
                new_line = new_line[:start] + "▒" * (end - start) + new_line[end:]

            # code blocks formatting here to add spaces to end of string
            code_block_format = format_multiline_one_line_end(code_blocks, len_new_line, newline_len, color_code, max_length-1, this_quote)
            if code_block_format:
                new_line = new_line.ljust(max_length-1)
            len_new_line = len(new_line)

            if wide:
                temp_wide_map.append(len(temp_chat))
            temp_chat.append(new_line)
            urls_this_line = urls_multiline_one_line(urls, len_new_line, newline_len, quote)
            spoilers_this_line = urls_multiline_one_line(spoilers, len_new_line, newline_len, quote)
            temp_chat_map.append((num, None, None, None, None, urls_this_line, spoilers_this_line))

            # formatting
            if disable_formatting:
                temp_format.append([color_base])
            elif mentioned:
                format_line = color_mention_newline[:]
                format_line += format_multiline_one_line_format(md_format, len_new_line, newline_len, this_quote)
                format_line += format_multiline_one_line(urls, len_new_line, newline_len, color_mention_chat_url, this_quote)
                format_line += format_multiline_one_line(code_snippets, len_new_line, newline_len, color_code, this_quote)
                format_line += code_block_format
                format_line += format_spoilers
                if edited and not next_line:
                    format_line.append(color_mention_chat_edited + [len_new_line - len_edited, len_new_line])
                temp_format.append(format_line)
            else:
                format_line = color_newline[:]
                format_line += format_multiline_one_line_format(md_format, len_new_line, newline_len, this_quote)
                format_line += format_multiline_one_line(urls, len_new_line, newline_len, color_chat_url, this_quote)
                format_line += format_multiline_one_line(code_snippets, len_new_line, newline_len, color_code, this_quote)
                format_line += code_block_format
                format_line += format_spoilers
                if edited and not next_line:
                    format_line.append([*color_chat_edited, len_new_line - len_edited, len_new_line])
                temp_format.append(format_line)
            line_num += 1

        # reactions
        if message["reactions"]:
            reactions = []
            for reaction in message["reactions"]:
                emoji_str = reaction["emoji"]
                if emoji_as_text:
                    emoji_str = emoji_name(emoji_str)
                my_reaction = ""
                if reaction["me"]:
                    my_reaction = "*"
                reactions.append(
                    format_one_reaction
                    .replace("%reaction", emoji_str)
                    .replace("%count", f"{my_reaction}{reaction["count"]}"),
                )
            reactions_line = lazy_replace(format_reactions, "%timestamp", lambda: generate_timestamp(message["timestamp"], format_timestamp, convert_timezone))
            reactions_line = reactions_line.replace("%reactions", reactions_separator.join(reactions))
            reactions_line, wide = normalize_string_count(reactions_line, max_length, dots=True, fill=True)
            if wide:
                temp_wide_map.append(len(temp_chat))
            temp_chat.append(reactions_line)
            if disable_formatting:
                temp_format.append([color_base])
            elif mentioned:
                temp_format.append(color_mention_reactions)
            else:
                temp_format.append(color_reactions)
            reactions_map = []
            offset = 0
            for reaction in reactions:
                reactions_map.append([pre_reaction_len + offset, pre_reaction_len + len(reaction) + offset])
                offset += len(reactions_separator) + len(reaction)
            temp_chat_map.append((num, None, False, reactions_map, None, None, None))
        indexes.append(len(temp_chat))

        # invert message lines order and append them to chat
        # it is inverted because chat is drawn from down to upside
        wide_map.extend([len(chat) + len(temp_chat) - x for x in temp_wide_map])
        chat.extend(temp_chat[::-1])
        chat_format.extend(temp_format[::-1])
        chat_map.extend(temp_chat_map[::-1])
    return chat, chat_format, indexes, chat_map, wide_map


def generate_status_line(my_user_data, my_status, unseen, typing, active_channel, action, tasks, tabs, tabs_format, format_status_line, format_rich, slowmode=None, limit_typing=30, use_nick=True, fun=True):
    """
    Generate status line according to provided formatting.
    Possible options for format_status_line:
        %global_name
        %username
        %status   # discord status if online, otherwise 'connecting' or 'offline'
        %custom_status
        %custom_status_emoji
        %pronouns
        %unreads   # '[New unreads]' if this channel has unread messages
        %typing
        %rich
        %server
        %channel
        %action   # replying/editig/deleting
        %task   # currently running long task
        %tabs
        %slowmode   # 'slowmode {time}'
    Possible options for format_rich:
        %type
        %name
        %state
        %details
        %small_text
        %large_text
    length of the %typing string can be limited with limit_typing
    use_nick will make it use nick instead username whenever possible.
    """
    # typing
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

    # my rich presence
    if my_status["activities"]:
        state = my_status["activities"][0]["state"][:limit_typing]
        details = my_status["activities"][0]["details"][:limit_typing]
        sm_txt = my_status["activities"][0]["small_text"]
        lg_txt = my_status["activities"][0]["large_text"]
        activiy_type = my_status["activities"][0]["type"]
        if activiy_type == 0:
            verb = "Playing"
        elif activiy_type == 1:
            verb = "Streaming"
        elif activiy_type == 2:
            verb = "Listening to"
        elif activiy_type == 3:
            verb = "Watching"
        elif activiy_type == 5:
            verb = "Competing in"
        rich = (
            format_rich
            .replace("%type", verb)
            .replace("%name", my_status["activities"][0]["name"])
            .replace("%state", state or "")
            .replace("%details", details or "")
            .replace("%small_text", sm_txt or "")
            .replace("%large_text", lg_txt or "")
        )
        if fun:
            rich = rich.replace("Metal", "🤘 Metal").replace("metal", "🤘 metal")
    else:
        rich = "No rich presence"
    if my_status["client_state"] == "online":
        status = my_status["status"]
    else:
        status = my_status["client_state"]
    guild = active_channel["guild_name"]

    # action
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
        action_string = "Select link to open in browser (type a number)"
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

    if my_status["custom_status_emoji"]:
        custom_status_emoji = str(my_status["custom_status_emoji"]["name"])
    else:
        custom_status_emoji = ""

    # running long tasks
    tasks = sorted(tasks, key=lambda x:x[1])
    if len(tasks) == 0:
        task = ""
    elif len(tasks) == 1:
        task = tasks[0][0]
    else:
        task = f"{tasks[0][0]} (+{len(tasks) - 1})"

    have_tabs = "%tabs" in format_status_line
    if not tabs:
        tabs = ""
        have_tabs = False

    if slowmode is None:
        slowmode = ""
    elif slowmode == 0:
        slowmode = "Slowmode"
    else:
        slowmode = f"Slowmode: {format_seconds(slowmode)}"

    status_line = (
        format_status_line
        .replace("%global_name", get_global_name(my_user_data, use_nick))
        .replace("%username", my_user_data["username"])
        .replace("%status", status)
        .replace("%custom_status", str(my_status["custom_status"]))
        .replace("%custom_emoji", custom_status_emoji)
        .replace("%pronouns", str(my_user_data["pronouns"]))
        .replace("%unreads", "[New unreads]" if unseen else "")
        .replace("%typing", typing_string)
        .replace("%rich", rich)
        .replace("%server", guild or "DM")
        .replace("%channel", str(active_channel["channel_name"]))
        .replace("%action", action_string)
        .replace("%task", task)
        .replace("%tabs", tabs)
        .replace("%slowmode", slowmode)
    )

    status_line_format = []
    if have_tabs:
        pre_tab_len = len(status_line.split(tabs)[0])
        for tab in tabs_format:
            status_line_format.append((tab[0], tab[1] + pre_tab_len, tab[2] + pre_tab_len))

    return status_line, status_line_format


def generate_tab_string(tabs, active_tab, unseen, format_tabs, tabs_separator, limit_len, max_len):
    """
    Generate tabs list string according to provided formatting.
    Possible options for generate_tab_string:
        %num
        %name
        %server
    """
    tabs_separated = []
    tab_string_format = []   # [[attribute, start, end] ...]
    trimmed_left = False
    for num, tab in enumerate(tabs):
        tab_text = (
            format_tabs
            .replace("%num", str(num + 1))
            .replace("%name", tab["channel_name"][:limit_len])
            .replace("%server", tab["guild_name"][:limit_len])
        )
        tabs_separated.append(tab_text)

        if num == active_tab:
            tab_string_format.append([3])   # underline
        elif tab["channel_id"] in unseen:
            tab_string_format.append([1])   # bold
        else:
            tab_string_format.append(None)

        # scroll to active if string is too long
        if num == active_tab:
            while len(tabs_separator.join(tabs_separated)) >= max_len:
                if not tabs_separated:
                    break
                trimmed_left = True
                tabs_separated.pop(0)
                tab_string_format.pop(0)
        if (active_tab and num >= active_tab) and len(tabs_separator.join(tabs_separated)) >= max_len:
            break

    # add format start and end indexes
    for num, tab in enumerate(tabs_separated):
        if tab_string_format[num]:
            start = len(tabs_separator.join(tabs_separated[:num])) + bool(num) * len(tabs_separator) + 2 * trimmed_left
            end = start + len(tab)
            tab_string_format[num] = [tab_string_format[num][0], start, end]
    tab_string_format = [x for x in tab_string_format if x is not None and len(x) == 3]

    tab_string = tabs_separator.join(tabs_separated)

    if trimmed_left:
        tab_string = f"< {tab_string}"

    # trim right side of tab string
    if len(tab_string) > max_len:
        tab_string = tab_string[:max_len - 2 * (trimmed_left + 1)] + " >"

    return tab_string, tab_string_format


def generate_prompt(my_user_data, active_channel, format_prompt, limit_prompt=15):
    """
    Generate prompt line according to provided formatting.
    Possible options for format_prompt_line:
        %global_name
        %username
        %server
        %channel
    """
    guild = active_channel["guild_name"]
    return (
        format_prompt
        .replace("%global_name", get_global_name(my_user_data, False)[:limit_prompt])
        .replace("%username", my_user_data["username"][:limit_prompt])
        .replace("%server", guild[:limit_prompt] if guild else "DM")
        .replace("%channel", str(active_channel["channel_name"])[:limit_prompt])
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


def generate_log(log, colors, max_width):
    """Generate log lines shown in chat area"""
    chat = []
    chat_format = []
    indexes = []
    chat_map = []
    for message in log:
        temp_chat = split_long_line(message, max_width, 4)
        chat.extend(temp_chat)
        chat_format.extend([[[colors[0]]]] * len(temp_chat))
        indexes.append(len(temp_chat))
        chat_map.extend([None] * len(temp_chat))
    chat = chat[::-1]
    return chat, chat_format, indexes, chat_map


def generate_extra_line(attachments, selected, max_len):
    """
    Generate extra line containing attachments information, with format:
    Attachments: [attachment.name] - [Uploading/OK/Too-Large/Restricted/Failed], Selected:N, Total:N
    """
    if attachments:
        total = len(attachments)
        name = attachments[selected]["name"]
        match attachments[selected]["state"]:
            case 0:
                state = "Uploading"
            case 1:
                state = "OK"
            case 2:
                state = "Too Large"
            case 3:
                state = "Restricted"
            case 4:
                state = "Failed"
            case _:
                state = "Unknown"
        end = f" - {state}, Selected:{selected + 1}, Total:{total}"
        return f" Attachments: {name}"[:max_len - len(end)] + end
    return ""


def generate_extra_line_ring(caller_name, max_len):
    """Generate extra line containing iformation about incoming call"""
    left_text = f"{caller_name} is calling you, use commands: voice_*"
    right_text = "[Accept] [Reject]"

    if len(left_text) + 1 + len(right_text) <= max_len:
        space_num = max_len - (len(left_text) + len(right_text))
        return left_text + " " * space_num + right_text

    max_str1_length = max_len - len(right_text) - 3   # 3 for ...
    shortened_str1 = left_text[:max_str1_length] + "..."
    return shortened_str1 + right_text


def generate_extra_line_call(call_participants, muted, max_len):
    """Generate extra line containing iformation about ongoing call"""
    left_text = "In the call: You"
    right_text = f"[{"Unmute" if muted else "Mute"}] [Leave]"

    for participant in call_participants:
        left_text += f", {participant["name"]}"
        if len(left_text) + 1 + len(right_text) > max_len:
            break

    if len(left_text) + 1 + len(right_text) <= max_len:
        space_num = max_len - (len(left_text) + len(right_text))
        return left_text + " " * space_num + right_text

    max_str1_length = max_len - len(right_text) - 3   # 3 for ...
    shortened_str1 = left_text[:max_str1_length] + "..."
    return shortened_str1 + right_text


def generate_extra_window_call(call_participants, me_muted, max_len):
    """Generate extra windows title and body as a list of voice call participants and their states"""
    title_line = "Voice call participants:"
    body = []
    body.append(f"Me - {"muted  " if me_muted else "unmuted"}")
    for participant in call_participants:
        name = participant["name"]
        text = f" - {"muted  " if participant["muted"] else "unmuted"}"
        if participant["speaking"]:
            text += " - speaking"
        if len(participant["name"]) + len(text) > max_len:
            name = name[:-(len(participant["name"]) + len(text) - max_len)]
        body.append(name + text)
    return title_line, body


def generate_extra_window_profile(user_data, user_roles, presence, max_len):
    """Generate extra window title and body for user profile view"""
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
    title_line = ""
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

    # activity
    if presence:
        status = presence["status"].capitalize().replace("Dnd", "DnD")
        custom = ""
        if presence.get("custom_status_emoji"):
            status_emoji = presence["custom_status_emoji"]["name"]
            if not emoji.is_emoji(status_emoji):
                status_emoji = f":{status_emoji}:"
            custom += f"{status_emoji} "
        if presence["custom_status"]:
            custom += presence["custom_status"]
        if custom:
            custom = f" - {custom}"
        body_line += f"Status: {status}{custom}\n"
    else:
        body_line += "Could not fetch status\n"

    # build body
    if user_data["tag"]:
        body_line += f"Tag: {user_data["tag"]}\n"
    body_line += f"Member since: {member_since}\n"
    if user_data["joined_at"]:
        body_line += f"Joined: {user_data["joined_at"]}\n"

    # rich presences
    if presence:
        if presence["activities"]:
            body_line += "\n"
        for activity in presence["activities"]:
            activity_type = activity["type"]
            if activity_type == 0:
                action = "Playing"
            elif activity_type == 1:
                action = "Streaming"
            elif activity_type == 2:
                action = "Listening to"
            elif activity_type == 3:
                action = "Watching"
            elif activity_type == 5:
                action = "Competing in"
            if activity["state"]:
                state = f" - {activity["state"]}"
            else:
                state = ""
            body_line += f"{action} {activity["name"]}{state}\n"
            if activity["details"]:
                body_line += f"{activity["details"]}\n"
            if activity["small_text"]:
                body_line += f"{activity["small_text"]}\n"
            if activity["large_text"]:
                body_line += f"{activity["large_text"]}\n"
            body_line += "\n"

    if roles_string:
        body_line += f"Roles: {roles_string}\n"
    if user_data["bio"]:
        body_line += f"Bio:\n{user_data["bio"]}"

    body = split_long_line(body_line, max_len)
    return title_line, body


def generate_extra_window_channel(channel, max_len):
    """Generate extra window title and body for channel info view"""
    title_line = f"Channel: {channel["name"]}"[:max_len]
    body_line = ""
    no_embed = not channel.get("allow_attach", True)
    no_write = not channel.get("allow_write", True)
    if no_embed and no_write:
        body_line += "No write and embed permissions\n"
    elif no_embed:
        body_line += "No embed permissions\n"
    elif no_write:
        body_line += "No write permissions\n"
    if channel["topic"]:
        body_line += f"Topic:\n{channel["topic"]}"
    else:
        body_line += "No topic."

    body = split_long_line(body_line, max_len)
    return title_line, body


def generate_extra_window_guild(guild, max_len):
    """Generate extra window title and body for guild info view"""
    title_line = f"Server: {guild["name"]}"[:max_len]

    # build body
    body_line = f"Members: {guild["member_count"]}\n"
    if guild["description"]:
        body_line += f"Description:\n{guild["description"]}"
    else:
        body_line += "No description."

    body = split_long_line(body_line, max_len)
    return title_line, body


def generate_extra_window_summaries(summaries, max_len, channel_name=None):
    """Generate extra window title and body for summaries list view"""
    if channel_name:
        title_line = f"[{channel_name}] Summaries:"
    else:
        title_line = "Summaries:"
    body = []
    indexes = []
    if summaries:
        for summary in summaries:
            summary_date = timestamp_from_snowflake(int(summary["message_id"]), "%m-%d-%H:%M")
            summary_string = f"[{summary_date}] - {summary["topic"]}: {summary["description"]}"
            summary_lines = split_long_line(summary_string, max_len, align=16)
            indexes.append({
                "lines": len(summary_lines),
                "message_id": summary["message_id"],
            })
            body.extend(summary_lines)
    else:
        body = ["No summaries."]
    return title_line, body, indexes


def generate_extra_window_search(messages, roles, channels, blocked, total_msg, config, max_len, limit_lines=3, newline_len=4, pinned=False):
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

    if pinned:
        title_line = f"Pinned_messages ({total_msg}):"
    else:
        title_line = f"Search results: {total_msg} messages"

    body = []
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
            for channel in channels:
                if channel["id"] == channel_id:
                    channel_name = channel["name"]
                    break

            content = ""
            if message["content"]:
                content = replace_discord_emoji(message["content"])
                content = replace_mentions(content, message["mentions"], global_name=use_global_name, use_nick=use_nick)
                content = replace_roles(content, roles)
                content = replace_discord_url(content)
                content = replace_channels(content, channels)
                content = replace_timestamps(content, convert_timezone)
                content = replace_spoilers_oneline(content)
                if emoji_as_text:
                    content = emoji.demojize(content)

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
                .replace("%channel", normalize_string(channel_name, limit_channel_name, emoji_safe=True))
                .replace("%content", content)
            )

            message_lines = split_long_line(message_string, max_len, align=newline_len)
            message_lines = message_lines[:limit_lines]
            indexes.append({
                "lines": len(message_lines),
                "message_id": message["id"],
                "channel_id": message["channel_id"],
            })
            body.extend(message_lines)

    else:
        body = ["No messages found."]
    return title_line, body, indexes


def generate_extra_window_search_gif(gifs, max_len):
    """Generate extra window title and body for gif search view"""
    title_line = f"Gif search results: {len(gifs)} gifs"
    body = []

    for gif in gifs:
        url = gif["url"]
        if url.startswith("https://tenor.com/view/"):
            # remove prefix url
            gif_title = url[len("https://tenor.com/view/"):]
            # remove trailing numbers
            last_dash_index = gif_title.rfind("-")
            if last_dash_index != -1:
                gif_title = gif_title[:last_dash_index]
            # custom format
            gif_title = f"Tenor: {gif_title}"
        else:
            gif_title = url
        body.append(gif_title[:max_len])

    return title_line, body


def generate_extra_window_text(title_text, body_text, max_len):
    """Generate extra window title and body for summaries list view"""
    title_line = title_text[:max_len]
    body = split_long_line(body_text, max_len)
    return title_line, body


def generate_extra_window_assist(found, assist_type, max_len):
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
    for item in found:
        body.append(f"{prefix}{item[0]}"[:max_len])
    if not body:
        body = ["No matches"]
    return title_line[:max_len], body


def generate_extra_window_reactions(reaction, details, max_len):
    """Generate extra window title and body for reactions"""
    title_line = f"Users who reacted {reaction["emoji"]}: "
    body = []
    for user in details:
        body.append(user["username"][:max_len])
    return title_line[:max_len], body


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
    color_format_forum = colors_formatted[7]   # 15 is unused
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


def generate_member_list(member_list_raw, guild_roles, width, use_nick, status_sign):
    """Generate member list"""
    # colors: 18 - green, 19 - orange, 20 - red
    member_list = []
    member_list_format = []
    if not member_list_raw:
        return ["No members".center(width-1, " ")], [[]]
    for member in member_list_raw:
        this_format = []
        if "id" in member:

            # format text
            global_name = get_global_name(member, use_nick)
            text = f"{status_sign} {global_name}"

            # get status color
            if member["status"] == "dnd":
                this_format.append([20, 0, 2])
            elif member["status"] == "idle":
                this_format.append([19, 0, 2])
            elif member["status"] == "offline":
                text = f"  {global_name}"
                #this_format.append([])
            else:   # online
                this_format.append([18, 0, 2])

            # get role color
            member_roles = member["roles"]
            for role in guild_roles:
                if role["id"] in member_roles:
                    if role.get("color_id"):
                        this_format.append([role["color_id"], 2, width])
                    break

        else:   # user group
            text = "Unknown group"
            if member["group"] == "online":
                text = "Online"
            elif member["group"] == "offline":
                text = "Offline"
            group_id = member["group"]
            for role in guild_roles:
                if role["id"] == group_id:
                    text = role["name"]
            this_format = []
        member_list.append(normalize_string(text, width-1, emoji_safe=True))
        member_list_format.append(this_format)

    return member_list, member_list_format


def generate_message_notification(data, channels, roles, guild_name, convert_timezone, use_global_name=False, use_nick=False):
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
        body = replace_spoilers_oneline(data["content"])
        body = replace_discord_emoji(body)
        body = replace_mentions(body, data["mentions"], global_name=use_global_name, use_nick=use_nick)
        body = replace_roles(body, roles)
        body = replace_discord_url(body)
        body = replace_channels(body, channels)
        body = replace_timestamps(body, convert_timezone)
    elif data.get("embeds"):
        num = len(data["embeds"])
        if num == 1:
            embed_type = data["embeds"][0]["type"].split("/")[0]
            embed_type = ("an " if embed_type.startswith(("a", "e", "i", "o", "u")) else "a ") + embed_type
            body = f"Sent {embed_type}"
        else:
            body = f"Sent {num} attachments"
    else:
        body = "Unknown content"

    return title, body


def generate_tree(dms, guilds, threads, unseen, mentioned, guild_folders, activities, collapsed, uncollapsed_threads, active_channel_id, config, folder_names=[], safe_emoji=False, max_w=0):
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
    dm_status_char = config["tree_dm_status"]
    show_folders = config["tree_show_folders"]
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
    for dm in dms:
        name = dm["name"]
        unseen_dm = False
        mentioned_dm = False
        if dm["id"] in unseen:
            unseen_dm = True
        if dm["id"] in mentioned:
            mentioned_dm = True
        muted = dm.get("muted", False)
        active = (dm["id"] == active_channel_id)
        if safe_emoji:
            name = replace_emoji_string(emoji.demojize(name))
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
                    name = dm_status_char + name
                    break
        tree.append(normalize_string(f"{intersection} {name}", max_w, emoji_safe=not(safe_emoji)))
        if muted:
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
        ping_guild = False
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
                    "ping": False,
                })

        # separately sort channels in their categories
        bare_channels = []
        for channel in guild["channels"]:
            if channel["type"] in (0, 5, 15):
                # find this channel threads, if any
                for channel_th in threads_guild:
                    if channel_th["channel_id"] == channel["id"]:
                        threads_ch = channel_th["threads"]
                        break
                else:
                    threads_ch = []
                unseen_ch = False
                mentioned_ch = False
                if channel["id"] in unseen:
                    unseen_ch = True
                if channel["id"] in mentioned:
                    mentioned_ch = True
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
                            if mentioned_ch:
                                category["ping"] = True
                                ping_guild = True
                        if not hidden_ch and category["hidden"] != 2:
                            category["hidden"] = False
                        active = (channel["id"] == active_channel_id)
                        category["channels"].append({
                            "id": channel["id"],
                            "name": channel["name"],
                            "position": channel["position"],
                            "muted": muted_ch,
                            "hidden": hidden_ch,
                            "unseen": unseen_ch,
                            "ping": mentioned_ch,
                            "active": active,
                            "threads": threads_ch,
                            "forum": channel["type"] == 15,
                        })
                        break
                else:
                    # top level channels can be inaccessible
                    muted_ch = channel.get("muted", False)
                    hidden_ch = channel.get("hidden", False)
                    if not channel.get("permitted", False):
                        hidden_ch = True
                    active = channel["id"] == active_channel_id
                    bare_channels.append({
                        "id": channel["id"],
                        "name": channel["name"],
                        "position": channel["position"],
                        "channels": None,
                        "muted": muted_ch,
                        "hidden": hidden_ch,
                        "unseen": unseen_ch,
                        "ping": mentioned_ch,
                        "active": active,
                    })
        categories += bare_channels

        # sort categories by position key
        categories = sorted(categories, key=lambda x: x["position"])

        # add guild to the tree
        name = guild["name"]
        if safe_emoji:
            name = replace_emoji_string(emoji.demojize(name))
        tree.append(normalize_string(f"{dd_pointer} {name}", max_w, emoji_safe=not(safe_emoji)))
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
                        name = replace_emoji_string(emoji.demojize(name))
                    tree.append(normalize_string(f"{intersection}{dd_pointer} {name}", max_w, emoji_safe=not(safe_emoji)))
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
                            forum = channel["forum"]
                            channel_threads = channel.get("threads", [])
                            channel_index = len(tree_format)
                            if safe_emoji:
                                name = replace_emoji_string(emoji.demojize(name))
                            if forum:
                                tree.append(normalize_string(f"{pass_by}{intersection}{dd_forum} {name}", max_w, emoji_safe=not(safe_emoji)))
                            elif channel_threads:
                                tree.append(normalize_string(f"{pass_by}{intersection}{dd_pointer} {name}", max_w, emoji_safe=not(safe_emoji)))
                            else:
                                tree.append(normalize_string(f"{pass_by}{intersection} {name}", max_w, emoji_safe=not(safe_emoji)))
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
                            if channel_threads and (channel["id"] in uncollapsed_threads):
                                code += 1
                            tree_format.append(code)
                            tree_metadata.append({
                                "id": channel["id"],
                                "type": 15 if forum else 0,
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
                                if safe_emoji:
                                    name = replace_emoji_string(emoji.demojize(name))
                                tree.append(f"{pass_by}{pass_by}{intersection_thread} {name}")
                                code = 400
                                if (thread["muted"] or not joined) and not active:
                                    code += 10
                                elif thread_id == active_channel_id and thread_id in mentioned:
                                    code += 50
                                elif active:
                                    code += 40
                                elif thread_id in mentioned:
                                    code += 20
                                elif thread_id in unseen:
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
                        name = replace_emoji_string(emoji.demojize(name))
                    tree.append(normalize_string(f"{intersection} {name}", max_w, emoji_safe=not(safe_emoji)))
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
