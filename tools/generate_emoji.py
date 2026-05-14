# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import http.client
import json
import os.path
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

UTC_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d, %H:%M:%S") + " UTC"
HEADER = f"""# {UTC_DATE}
# Generated from: https://www.unicode.org/Public/emoji/latest/emoji-test.txt
"""
SKIP_VARIATIONS = False
APP_NAME = "endcord"
VERSION = "1.5.0"
HTTP_HEADER = {
    "User-Agent": APP_NAME + "/" + VERSION,   # required by github
    "Accept": "application/vnd.github+json",
}
REMOVE_CHARS_GH = re.compile("\u200d|\ufe0f|\ufe0e")


def http_get_with_redirect(host, path, header=None):
    """Download data while handling up to 2 redirects"""
    redirects = 0

    while redirects < 2:
        try:
            conn = http.client.HTTPSConnection(host, timeout=30)
            if header:
                conn.request("GET", path, headers=header)
            else:
                conn.request("GET", path)
            response = conn.getresponse()
        except (socket.gaierror, TimeoutError, ConnectionResetError) as e:
            print(f"Error: {e}")
            return ""

        if response.status == 200:
            data = response.read().decode("utf-8", errors="replace")
            conn.close()
            return data

        # redirects
        if response.status in (301, 302, 303, 307, 308):
            location = response.getheader("Location")
            if not location:
                print("Error: Redirect without lcation")
                return ""
            redirects += 1
            conn.close()
            parsed = urlparse(location)
            if parsed.netloc:
                host = parsed.netloc
            if parsed.path:
                path = parsed.path
            continue
        print(f"HTTP error {response.status} for {host}{path}")
        return ""
    return ""


def parse_line(line):
    """Parse line according to unicode convention"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None, None, None, None, None
    if ";" not in line:
        return None, None, None, None, None
    unicode_val = line.split(";")[0].strip()
    qualified = line.split(";")[1].split(" # ")[0].strip() == "fully-qualified"
    info = line.split(" # ")[1].strip().split(" ")
    emoji = info[0]
    version = info[1]
    name = " ".join(info[2:])
    return unicode_val, emoji, name, qualified, version


def generate_emoji():
    """Download latest unicode data and build list of ranges of wide characters as python file"""
    # download list
    print("Downloading unicode list")
    list_raw = http_get_with_redirect("www.unicode.org", "/Public/emoji/latest/emoji-test.txt").splitlines()
    if not list_raw:
        print("Failed downloading unicode list")
        return

    # read lines into usable data
    skipped_variations = 0
    list_parsed = []
    for line in list_raw:
        unicode_val, emoji, name, qualified, _ = parse_line(line)
        if not qualified:
            continue
        if SKIP_VARIATIONS and any((0x1F3FB <= ord(ch) <= 0x1F3FF) for ch in emoji):
            skipped_variations += 1
            continue
        if emoji[-1] == 0xFE0E or emoji[-1] == 0xFE0F:
            return emoji[0:-1]   # strip variation selectors for text/emoji
        name = (name
            .replace("flag: ", "")
            .replace(" ", "_")
            .replace(":", "")
            .replace(",", "")
            .replace(".", "")
            .replace("&", "")
            .replace("-", "_")
            .replace("__", "_")
            .replace("__", "_")
        )
        list_parsed.append([emoji, name])
    print(f"{"NOT " if SKIP_VARIATIONS else ""}Skipped {skipped_variations} emoji variations")
    print(f"Total {len(list_parsed)} emojis")
    print()

    # download and prepare github aliases
    print("Downloading alias list from github")
    alias_gh_raw = http_get_with_redirect("api.github.com", "/emojis", HTTP_HEADER)
    if not alias_gh_raw:
        print("Failed downloading github alias list")
        return
    alias_gh_raw = json.loads(alias_gh_raw)
    alias_gh = {}
    for name, value in alias_gh_raw.items():
        if "unicode" not in value:
            continue
        unicode_str = value.split("unicode/")[1].split(".png")[0]
        emoji = "".join(chr(int(h, 16)) for h in unicode_str.split("-"))
        alias_gh[emoji] = name
    print(f"Found {len(alias_gh)} aliases from github emoji list ({len(alias_gh_raw)} emojis)")
    print()

    # download and prepare youtube aliases
    print("Downloading alias list from youtube")
    alias_yt_raw = http_get_with_redirect("www.gstatic.com", "/youtube/img/emojis/emojis-png-7.json")
    if not alias_yt_raw:
        print("Failed downloading youtube alias list")
        return
    alias_yt_raw = json.loads(alias_yt_raw)
    alias_yt = {}
    for emoji in alias_yt_raw:
        if "shortcuts" not in emoji:
            continue
        if len(emoji["shortcuts"]) < 2:
            continue
        short_name = emoji["shortcuts"][1]
        if not (short_name.startswith(":") and short_name.endswith(":")):
            continue
        alias_yt[emoji["emojiId"]] = short_name
    print(f"Found {len(alias_yt)} aliases from youtube emoji list ({len(alias_yt_raw)} emojis)")
    print()

    # add aliases
    print("Merging aliases")
    count_gh = 0
    count_gh_same = 0
    count_yt = 0
    count_yt_same = 0
    for emoji in list_parsed:
        emoji_char = emoji[0]
        emoji_char_clean = REMOVE_CHARS_GH.sub("", emoji_char)
        if emoji_char_clean in alias_gh:
            if alias_gh[emoji_char_clean].lower() != emoji[1].lower():
                emoji.append(alias_gh[emoji_char_clean])
                count_gh += 1
            else:
                count_gh_same += 1
        if emoji_char in alias_yt:
            if alias_gh[emoji_char].lower() != emoji[1].lower() and (len(emoji) < 3 or alias_gh[emoji_char].lower() != emoji[2].lower()):
                emoji.append(alias_yt[emoji_char][1:-1])
                count_yt += 1
            else:
                count_yt_same += 1
    print(f"Added {count_gh} aliases from github ({count_gh_same} duplicates; {len(alias_gh) - count_gh - count_gh_same} skipped)")
    print(f"Added {count_yt} aliases from youtube ({count_yt_same} duplicates; {len(alias_yt) - count_yt - count_yt_same} skipped)")
    print()

    # add : around names and aliases
    for emoji in list_parsed:
        i = 1
        while i < len(emoji):
            emoji[i] = ":" + emoji[i] + ":"
            i += 1

    # sort alphabetically by name
    list_parsed.sort(key=lambda x: x[1])

    # build json file
    print("Building json file")
    path = os.path.expanduser("./endcord/emoji.json")
    with open(path, "w") as f:
        f.write("{\n")
        count = len(list_parsed)
        for emoji in list_parsed:
            names = '"' + '", "'.join(emoji[1:]) + '"'
            f.write(f'"{emoji[0]}": [{names}]{"" if count == 1 else ","}\n')
            count -= 1
        f.write("}\n")

    print(f"Emoji data saved to {path}")


if __name__ == "__main__":
    generate_emoji()
