# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import base64
import glob
import importlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time

import filetype

from endcord import peripherals

if sys.platform.startswith("android"):
    sys.platform = "linux"
if "bsd" in sys.platform:
    sys.platform = "linux"


logger = logging.getLogger(__name__)
match_youtube = re.compile(r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}")
match_emoji = re.compile(r"(?<!\\):[^:\s]+:")
EMOJI_DATA = {}   # delayed load for faster startup


def get_executable():
    """Get executable path and arguments for current run"""
    if "__compiled__" in globals() or getattr(sys, "frozen", False):   # built with nuitka or pyinstaller
        return [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def ensure_terminal():
    """
    Ensure that app is running inside a terminal emulator, launch inside terminal if not.
    Prefer $TERMINAL env var, fallback to few common terminal emulators.
    """
    if sys.stdout.isatty():
        return

    terminals = []
    if "TERMINAL" in os.environ:
        terminals.append(os.environ["TERMINAL"])
    terminals += [
        "gnome-terminal",
        "kgx",
        "konsole",
        "xfce4-terminal",
        "lxterminal",
        "alacritty",
        "ghostty",
        "kitty",
        "urxvt",
        "x-terminal-emulator",
        "xterm",
    ]

    for t in terminals:
        if shutil.which(t):
            terminal = t
            break
    else:
        terminal = None

    if not terminal:
        print("No terminal emulator found.", file=sys.stderr)
        sys.exit(1)

    if terminal in ("gnome-terminal", "kgx"):
        subprocess.Popen([terminal, "--"] + get_executable())
    else:
        subprocess.Popen([terminal, "-e"] + get_executable())
    sys.exit(0)


def ensure_ssl_certificates():
    """Ensure that there are ssl certificates available to http.client module"""
    if not ("__compiled__" in globals() or getattr(sys, "frozen", False)):   # skip if running from source
        return
    if sys.platform == "linux":
        cert_path = "/etc/ssl/certs/ca-certificates.crt"
        if os.path.exists(cert_path):
            os.environ["SSL_CERT_FILE"] = cert_path
        elif importlib.util.find_spec("certifi") is not None:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
    elif sys.platform == "darwin" and importlib.util.find_spec("certifi") is not None:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()


def collapseuser(path):
    """Opposite of os.path.expanduser()"""
    home = os.path.expanduser("~")
    abs_path = os.path.abspath(path)
    home = os.path.abspath(home)
    if abs_path.startswith(home):
        return "~" + abs_path[len(home):]
    return path


def detect_runtime():
    """Detect if code is running from source, pyinstaller or nuitka binary"""
    if hasattr(sys, "_MEIPASS"):
        return "pyinstaller"
    if "__compiled__" in globals():
        return "nuitka"
    if getattr(sys, "frozen", False):
        return "unknown"
    return "source"


def get_build_info(cythonized, uses_pgcurses, support_media, support_call):
    """Write build info string"""
    import curses
    build_info = [detect_runtime()]
    if cythonized:
        build_info.append("cythonized")
    if uses_pgcurses:
        build_info.append("windowed")
    if support_media:
        build_info.append("media support")
    if support_call:
        build_info.append("call support")
    custom_build = " (CUSTOM BUILD)" if importlib.util.find_spec("_bz2") is None else ""
    version = sys.version
    start = version.find("(++")
    if start >= 0:
        version = version[:start] + version[version.find(")", start):]
    curses_version = f"{curses.ncurses_version.major}.{curses.ncurses_version.minor}.{curses.ncurses_version.patch}"
    return f"Python {version}{custom_build} on {sys.platform}\n  ncurses {curses_version}\n  Features: {", ".join(build_info)}"


def remove_args(cmd, *args):
    """Remove args and their values from command list"""
    result = []
    skip = False
    for i, arg in enumerate(cmd):
        if skip:
            skip = False
            continue
        if arg in args:
            if i + 1 < len(cmd) and not cmd[i + 1].startswith("-"):
                skip = True
            continue
        result.append(arg)
    return result


def get_extensions(path):
    """Get list of valid extensions from specified path"""
    extensions = []
    invalid = []

    # get list of valid extensions (dir name and py file name)
    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        if os.path.isdir(full_path):
            main_file = os.path.join(full_path, entry + ".py")
            if os.path.isfile(main_file):
                extensions.append((main_file, entry))
            else:
                invalid.append((main_file, entry))
    if not extensions:
        return extensions, invalid

    # safely check py file contents
    match_class = re.compile(r"^class\s+Extension(\s*\(|\s*:)")
    match_constant = re.compile(r"^(\w+)\s*=\s*(.+)$")
    has_extension_class = False
    constants = ["EXT_NAME", "EXT_VERSION", "EXT_ENDCORD_VERSION", "EXT_DESCRIPTION", "EXT_SOURCE"]
    for ext_file, ext_name in extensions:
        with open(ext_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            if not line or line.startswith(("#", "'", '"')):
                continue
            if re.match(match_class, line):
                has_extension_class = True
                continue
            match = match_constant.match(line)
            if match:
                name, value = match.groups()
                if name in constants and value:
                    constants.remove(name)
        if not has_extension_class or constants:
            extensions.remove((ext_file, ext_name))
            invalid.append((ext_file, ext_name))

    extensions = sorted(extensions, key=lambda x: x[1])
    return extensions, invalid


def find_linux_sound(name):
    """Return path of sound file from its name, if it exists"""
    if not name:
        return None
    if sys.platform == "linux":
        path = os.path.join("/usr/share/sounds/freedesktop/stereo/", name + ".oga")
        if os.path.exists(path):
            return path


def load_json(file, default=None, dir_path=peripherals.config_path, create=False):
    """Load saved json from same location where default config is saved"""
    path = os.path.expanduser(os.path.join(dir_path, file))
    if not os.path.exists(path):
        if create:
            save_json(default, file, dir_path=dir_path)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if default:
                for key, value in default.items():
                    data.setdefault(key, value)
            return data
    except Exception:
        return default


def save_json(data, file, compact=False, dir_path=peripherals.config_path):
    """Save json to same location where default config is saved"""
    if not os.path.exists(dir_path):
        os.makedirs(os.path.expanduser(dir_path), exist_ok=True)
    path = os.path.expanduser(os.path.join(dir_path, file))
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, indent=None, separators=(",", ":"))
        else:
            json.dump(data, f, indent=2)


def get_file_size(path, mb=False):
    """Get file size in B or MB"""
    if mb:
        return round(os.stat(path).st_size / (1024 ** 2), 3)
    return os.stat(path).st_size


def get_dir_size(path, mb=False):
    """Get dir size in B or MB and file count"""
    count = 0
    total_size = 0
    for root, dirs, files in os.walk(path):
        for file in files:
            try:
                total_size += os.stat(os.path.join(root, file)).st_size
                count += 1
            except FileNotFoundError:
                pass
    if mb:
        return count, round(total_size / (1024 ** 2), 3)
    return count, total_size


def get_is_clip(path):
    """Get whether file is video or not"""
    kind = filetype.guess(path)
    if kind and kind.mime:
        return kind.mime.split("/")[0] == "video"


def get_can_play(path):
    """Get whether file can be played as media"""
    kind = filetype.guess(path)
    if kind and kind.mime:
        return kind.mime.split("/")[0] in ("image", "video", "audio")


def get_mime(path):
    """Try to get mime type of the file"""
    kind = filetype.guess(path)
    if kind:
        return kind.mime
    return "unknown/unknown"


def get_media_type(path, hint=None):
    """Try to get media type"""
    if re.search(match_youtube, path):
        return "YT"
    if "https://" in path:
        return "URL"
    mime = get_mime(path).split("/")
    if hint:
        mime = [hint, None]
    if mime[0] == "image":
        if mime[1] == "gif":
            return "gif"
        return "img"
    if mime[0] == "video":
        return "video"
    if mime[0] == "audio":
        return "audio"
    logger.warning(f"Unsupported media format: {mime}")


def get_audio_waveform(path):
    """Get audio file waveform and length"""
    import numpy as np
    if not os.path.exists(path):
        return None, None
    import soundfile
    with soundfile.SoundFile(path) as audio_file:
        data = audio_file.read()
        if data.ndim > 1:
            data = data[:, 0]   # select only one stream
        duration = len(data) / audio_file.samplerate
        chunk_num = min(max(int(duration * 10), 32), 256)
        chunk_size = int(len(data) / chunk_num)
        reshaped = data[:len(data) - len(data) % chunk_size].reshape(-1, chunk_size)
        rms_samples = np.sqrt(np.mean(reshaped**2, axis=1))
        normalized = (rms_samples / rms_samples.max()) * 255
        waveform = base64.b64encode(normalized.astype(np.uint8)).decode("utf-8")
    return waveform, duration


def complete_path(path, separator=True):
    """Get possible completions for path"""
    if not path:
        return []
    path = os.path.expanduser(path)
    completions = []
    for path in glob.glob(path + "*"):
        if separator and path and os.path.isdir(path) and path[-1] != "/":
            completions.append(path + "/")
        else:
            completions.append(path)
    return sorted(completions)


def json_array_objects(stream):
    """Stream a json array from a file like object. Yield one parsed object at a time without loading full json into memory"""
    # replaces ijson.items(data, "item")
    decoder = json.JSONDecoder()
    buf = ""
    in_array = False
    for chunk in iter(lambda: stream.read(65536).decode("utf-8"), ""):
        buf += chunk
        i = 0
        length = len(buf)
        while i < length:
            ch = buf[i]
            if not in_array:
                if ch == "[":   # skip to [
                    in_array = True
                i += 1
                continue
            if ch == "]":
                return
            if ch.isspace() or ch == ",":   # skip space and comma
                i += 1
                continue
            try:   # try to get object
                obj, consumed = decoder.raw_decode(buf[i:])
            except json.JSONDecodeError:
                break
            yield obj
            i += consumed
        buf = buf[i:]   # keep incomplete json only


def delete_old_files(directory, days, accessed=False):
    """Delete files accessed/created older than N days"""
    directory = os.path.expanduser(directory)
    if not os.path.exists(directory):
        return
    cutoff = time.time() - (days * 86400)
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        try:
            file_time = os.path.getatime(path) if accessed else os.path.getctime(path)
            if file_time < cutoff:
                os.remove(path)
        except OSError:
            pass


def get_base_path():
    """Get resource path"""
    if hasattr(sys, "_MEIPASS"):   # pyinstaller onefile
        return sys._MEIPASS
    if "__compiled__" in globals():
        return os.path.dirname(sys.executable)   # nuitka
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)   # onedir
    return os.path.dirname(os.path.abspath(__file__))


def load_emoji(path="emoji.json"):
    """Load global emoji data"""
    global EMOJI_DATA
    path = os.path.join(get_base_path(), *path.split("/"))
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        EMOJI_DATA = json.load(f)


def emojize(text):
    """Convert all emoji shortcodes in given string to actual emoji"""
    if not text:
        return text

    def replace_emoji(match):
        name = match.group()
        for emoji, data in EMOJI_DATA.items():
            if name in data:
                break
        else:
            return name
        # remove existing variation selector and force emoji
        if emoji[-1] == 0xFE0E or emoji[-1] == 0xFE0F:
            return emoji[0:-1] + 0xFE0F
        return emoji

    return re.sub(match_emoji, replace_emoji, text)


def is_potential_emoji(cluster):
    """Check if any character in the cluster is emoji"""
    for character in cluster:
        ch = ord(character)
        return (
            (0x1F300 <= ch <= 0x1F9FF) or
            (0x2600 <= ch <= 0x27BF) or
            (0x2300 <= ch <= 0x23FF) or
            (0x2B00 <= ch <= 0x2BFF)
        )
    return False


SKIN_TONES = {0x1F3FB, 0x1F3FC, 0x1F3FD, 0x1F3FE, 0x1F3FF}


def next_emoji_cluster(text, i):
    """Get emoji cluster after specific index"""
    ch = text[i]
    cluster = [ch]
    i += 1

    if 0x1F1E6 <= ord(ch) <= 0x1F1FF:   # flags
        if i < len(text) and 0x1F1E6 <= ord(text[i]) <= 0x1F1FF:
            cluster.append(text[i])
            i += 1
        return "".join(cluster), i

    while i < len(text):
        ch = text[i]
        if ord(ch) == 0xFE0E or ord(ch) == 0xFE0F:   # variation selectors for for text/emoji
            cluster.append(ch)
            i += 1
            continue
        if ord(ch) in SKIN_TONES:   # skin tone
            cluster.append(ch)
            i += 1
            continue
        if ch == "\u20E3":   # keycap sequence
            cluster.append(ch)
            i += 1
            continue
        if ch == "\u200D" and i + 1 < len(text):  # zwj sequence
            cluster.append(ch)
            cluster.append(text[i + 1])
            i += 2
            continue
        break

    return "".join(cluster), i


def demojize(text, safe=False):
    """Convert all emojis in given string to their shortcodes"""
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if 0x20 <= ord(ch) < 0x7F:
            result.append(ch)
            i += 1
            continue
        cluster, i = next_emoji_cluster(text, i)
        if ord(cluster[-1]) == 0xFE0E or ord(cluster[-1]) == 0xFE0F:
            cluster = cluster[0:-1]   # remove variation selector for for text/emoji
        emoji = EMOJI_DATA.get(cluster)
        if emoji:
            result.append(min(emoji, key=len))
        elif safe and is_potential_emoji(cluster):
            result.append("▒")
        else:
            result.append(cluster)
    return "".join(result)


def is_emoji(character):
    """Check if given character is emoji"""
    if not character:
        return False
    return character in EMOJI_DATA


def split_emoji(text):
    """Split text on each character, taking care of emoji variation sequences"""
    result = []
    for char in text:
        if result and "\ufe00" <= char <= "\ufe0f":
            result[-1] += char
        else:
            result.append(char)
    return result
