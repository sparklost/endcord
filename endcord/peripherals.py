import base64
import glob
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from ast import literal_eval
from configparser import ConfigParser

import filetype
import pexpect
import pexpect.popen_spawn

from endcord import defaults

logger = logging.getLogger(__name__)
APP_NAME = "endcord"
ASPELL_TIMEOUT = 0.1   # aspell limit for looking-up one word
NO_NOTIFY_SOUND_DE = ("kde", "plasma")   # linux desktops without notification sound

match_youtube = re.compile(r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}")


# platform specific code
have_termux_notify = False
if sys.platform == "win32":
    import win32clipboard
    from windows_toasts import Toast, WindowsToaster
    toaster = WindowsToaster(APP_NAME)
elif sys.platform == "linux":
    have_gdbus = shutil.which("gdbus")
    have_notify_send = shutil.which("notify-send")
    have_termux_notify = shutil.which("termux-notification")
    # if this DE has no notification sound, try to get fallback sound
    no_notify_sound = False
    fallback_notification_sound = None
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "")
    if desktop.lower() in NO_NOTIFY_SOUND_DE:
        no_notify_sound = True
        path = "/usr/share/sounds/freedesktop/stereo"
        for keyword in ["message", "notification", "bell"]:
            for root, dirs, files in os.walk(path):
                for file in files:
                    if keyword in file.lower():
                        fallback_notification_sound = os.path.join(root, file)
                        break
                if fallback_notification_sound:
                    break
            if fallback_notification_sound:
                break

# get platform specific paths
if sys.platform == "linux":
    path = os.environ.get("XDG_DATA_HOME", "")
    if path.strip():
        config_path = os.path.join(path, f"{APP_NAME}")
        log_path = os.path.join(path, f"{APP_NAME}")
    else:
        config_path = f"~/.config/{APP_NAME}"
        log_path = f"~/.config/{APP_NAME}"
    path = os.environ.get("XDG_RUNTIME_DIR", "")
    if path.strip():
        temp_path = os.path.join(path, f"{APP_NAME}")
    else:
        # per-user temp dir
        temp_path = f"/run/user/{os.getuid()}/{APP_NAME}"
        # fallback to .cache
        if not os.access(f"/run/user/{os.getuid()}", os.W_OK):
            temp_path = f"~/.cache/{APP_NAME}"
    os.makedirs(temp_path, exist_ok=True)

    path = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if path.strip():
        downloads_path = os.path.join(path, f"{APP_NAME}")
    else:
        downloads_path = "~/Downloads"
elif sys.platform == "win32":
    config_path = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME)
    log_path = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME)
    temp_path = os.path.join(os.environ["LOCALAPPDATA"], "Temp", APP_NAME)
    downloads_path = os.path.join(os.environ["USERPROFILE"], "Downloads")
elif sys.platform == "darwin":
    config_path = f"~/Library/Application Support/{APP_NAME}"
    log_path = f"~/Library/Application Support/{APP_NAME}"
    temp_path = f"~/Library/Caches/TemporaryItems{APP_NAME}"
    downloads_path = "~/Downloads"
else:
    print(f"Unsupported platform: {sys.platform}", file=sys.stderr)
    sys.exit(1)


# ensure paths exists
for app_path in (config_path, log_path, temp_path, downloads_path):
    if not os.path.exists(os.path.expanduser(app_path)):
        os.makedirs(os.path.expanduser(app_path), exist_ok=True)


# platform specific commands
if sys.platform == "linux":
    runner = "xdg-open"
    if shutil.which("zenity"):
        filedialog = "zenity"
    elif shutil.which("kdialog"):
        filedialog = "kdialog"
    else:
        filedialog = None
elif sys.platform == "win32":
    runner = "explorer"
    import win32con
    import win32gui
    filedialog = "windows"
elif sys.platform == "darwin":
    runner = "open"
    filedialog = "mac"


# check for audio systems
have_pipewire = (sys.platform == "linux" and shutil.which("pw-cat") and "pipewire" in subprocess.check_output(["ps", "-A"], text=True))
have_pulseaudio = (sys.platform == "linux" and shutil.which("paplay") and "pulseaudio" in subprocess.check_output(["ps", "-A"], text=True))
have_afplay = (sys.platform == "darwin" and shutil.which("afplay"))


# create notification channel on termux
if have_termux_notify:
    proc = subprocess.Popen(
        ["termux-notification-channel", "1000", APP_NAME],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def import_soundcard():
    """Safely import soundcard"""
    try:
        if importlib.util.find_spec("soundcard") is not None:
            import soundcard
            return soundcard
        return None
    except (AssertionError, RuntimeError):
        logger.warning("Soundcard failed connecting to sound system")
        return None


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

    cmd = [sys.executable] + sys.argv
    if terminal in ("gnome-terminal", "kgx"):
        subprocess.Popen([terminal, "--"] + cmd)
    else:
        subprocess.Popen([terminal, "-e"] + cmd)
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


def save_config(path, data, section):
    """Save config section"""
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    config = ConfigParser(interpolation=None)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config.read_file(f)
    if not config.has_section(section):
        config.add_section(section)
    for key in data:
        if data[key] in (True, False, None) or isinstance(data[key], (list, tuple, int, float)):
            config.set(section, key, str(data[key]))
        else:
            config.set(section, key, f'"{str(data[key]).replace("\\", "\\\\")}"')
    with open(path, "w", encoding="utf-8") as f:
        config.write(f)


def load_config(path, default, section="main", gen_config=False, merge=False):
    """
    Load settings and theme from config
    If some value is missing, it is replaced with default value
    """
    if not path:
        path = os.path.join(config_path, "config.ini")
    path = os.path.expanduser(path)

    if not os.path.exists(path) or gen_config:
        save_config(path, default, section)
        if not gen_config:
            print(f"Default config generated at: {path}")
        config_data = default
    else:
        config = ConfigParser(interpolation=None)
        with open(path, "r", encoding="utf-8") as f:
            config.read_file(f)
        if not config.has_section(section):
            return default
        config_data_raw = config._sections[section]
        config_data = dict.fromkeys(default)
        for key in default:
            if key in list(config[section].keys()):
                try:
                    eval_value = literal_eval(config_data_raw[key].replace("\\", "\\\\"))
                    config_data[key] = eval_value
                except ValueError:
                    config_data[key] = config_data_raw[key]
            else:
                config_data[key] = default[key]
        for key, value in config_data_raw.items():
            if key.startswith("ext_") or merge:
                try:
                    eval_value = literal_eval(value.replace("\\", "\\\\"))
                    config_data[key] = eval_value
                except ValueError:
                    config_data[key] = value
    return config_data


def get_themes():
    """Return list of all themes found in Themes directory"""
    themes_path = os.path.expanduser(os.path.join(config_path, "Themes"))
    if not os.path.exists(themes_path):
        os.makedirs(themes_path, exist_ok=True)
    themes = []
    for file in os.listdir(themes_path):
        if file.endswith(".ini"):
            themes.append(os.path.join(themes_path, file))
    return themes


def merge_configs(custom_config_path, theme_path):
    """Merge config and themes, from various locations"""
    gen_config = False
    error = None
    if not custom_config_path:
        default_config_path = os.path.expanduser(os.path.join(config_path, "config.ini"))
        if not os.path.exists(default_config_path):
            logger.info("Using default config")
            gen_config = True
        custom_config_path = default_config_path
    elif not os.path.exists(os.path.expanduser(custom_config_path)):
        gen_config = True
    config = load_config(custom_config_path, defaults.settings)
    config["config_path"] = custom_config_path
    if not theme_path and config["theme"]:
        theme_path = os.path.expanduser(config["theme"])
    saved_themes = get_themes()
    theme = load_config(custom_config_path, defaults.theme, section="theme", gen_config=gen_config)
    theme["theme_path"] = None
    if theme_path:
        # if path is only file name without extension
        if os.path.splitext(os.path.basename(theme_path))[0] == theme_path:
            for saved_theme in saved_themes:
                if os.path.splitext(os.path.basename(saved_theme))[0] == theme_path:
                    theme_path = saved_theme
                    break
            else:
                error = f"Theme {theme_path} not found in themes directory."
        if not error:
            theme_path = os.path.expanduser(theme_path)
            theme = load_config(theme_path, theme, section="theme")
            theme["theme_path"] = theme_path
    config.update(theme)
    return config, gen_config, error


def alt_shift(value, shift):
    """Try to change "ALT+Key into integer key value"""
    val = re.sub(r"ALT\+(\d+)", lambda m: str(int(m.group(1)) + shift), value)
    try:
        return int(val)
    except ValueError:
        return val



def convert_keybindings(keybindings):
    """Convert keybinding codes to os-specific codes"""
    if sys.platform == "win32":   # windows has different codes for Alt+Key
        shift = 320
        swap_backspace = True   # config - 8 (ctrl+backspace) with real - 263 (backspace)
    elif os.environ.get("TERM", "") == "xterm":   # xterm has different codes for Alt+Key
        shift = 64
        swap_backspace = True
        # for ALT+Key it actually sends 195 then Key+64
        # but this is simpler since key should already be uniquely shifted
    else:
        return keybindings

    for key, value in keybindings.items():
        if isinstance(value, str):
            keybindings[key] = alt_shift(value, shift)
        elif swap_backspace and value == 8:
            keybindings[key] = 263

    return keybindings


def convert_keybindings_cmd(keybindings):
    """Convert keybinding codes to os-specific codes, for command bindings"""
    if sys.platform == "win32":
        shift = 320
    elif os.environ.get("TERM", "") == "xterm":
        shift = 64
    else:
        shift = 0

    new_keybindings = {}
    for key, value in keybindings.items():
        new_key = key.replace('"', "")
        if isinstance(new_key, str) and "alt" in new_key:
            new_key = new_key.replace("alt", "ALT")
        if isinstance(new_key, str) and shift:
            new_key = alt_shift(new_key, shift)
        else:
            try:
                new_key = int(new_key)
            except ValueError:
                pass
        new_keybindings[new_key] = value

    return new_keybindings


def normalize_keybindings(keybindings):
    """Ensure all keybindings are tuples"""
    for key in keybindings:
        if not isinstance(keybindings[key], tuple):
            keybindings[key] = (keybindings[key], )
    return keybindings


def deduplicate_keybindings(keybindings_a, keybindings_b, command=False):
    """Deduplicate 2 keubinding dicts that can have strings and tuples of strings as values, keeping keybindings_b"""
    def deduplicate_value(dedupe_value, keybindings):
        for key, values in keybindings.items():
            if isinstance(values, tuple):
                for num, value in enumerate(values):
                    if value == dedupe_value:
                        fresh_values = keybindings[key]
                        keybindings[key] = fresh_values[:num] + (None,) + fresh_values[num + 1:]
            elif values == dedupe_value:
                keybindings[key] = None

    def dedupe_value_command(dedupe_value, keybindings):
        for key, value in keybindings.items():
            if key == str(dedupe_value):
                del keybindings[key]

    if command:
        deduplicate_value = dedupe_value_command

    for key, values in keybindings_b.items():
        if isinstance(values, tuple):
            for value in values:
                deduplicate_value(value, keybindings_a)
        else:
            deduplicate_value(values, keybindings_a)


def merge_keybindings(keybindings, vim_keybindings, command_bindings):
    """Merge standard and vim mode keybindings and remove keybinding collisions favoring vim keybindings"""
    deduplicate_keybindings(keybindings, vim_keybindings)
    deduplicate_keybindings(command_bindings, vim_keybindings, command=True)

    for key, value_2_old in vim_keybindings.items():
        if isinstance(value_2_old, str) and len(value_2_old) == 1:
            try:
                value_2 = ord(value_2_old)
            except TypeError:
                value_2 = value_2_old
        else:
            value_2 = value_2_old

        if key not in keybindings:
            keybindings[key] = value_2
        else:
            value_1 = keybindings[key]
            if not isinstance(value_1, tuple):
                value_1 = (value_1, )
            if not isinstance(value_2, tuple):
                value_2 = (value_2, )
            if value_1[0] is None:
                value_1 = ()
            keybindings[key] = value_1 + value_2

    return keybindings


def update_config(config, key, value):
    """Update and save config"""
    if not value:
        value = ""
    else:
        try:
            value = literal_eval(value)
        except ValueError:
            pass
    config[key] = value
    config_path = config["config_path"]
    saved_config = ConfigParser(interpolation=None)
    if os.path.exists(config_path):
        with open(os.path.expanduser(config_path), "r", encoding="utf-8") as f:
            saved_config.read_file(f)
    new_config = {}
    new_theme = {}
    # split config and theme
    for key_all, value_all in config.items():
        if key_all in defaults.settings:
            new_config[key_all] = value_all
        elif key_all in defaults.theme:
            new_theme[key_all] = value_all
    save_config(config_path, new_config, "main")
    save_config(config_path, new_theme, "theme")
    return config


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


def install_extension(url):
    """Install extension from specified git repo url"""
    if shutil.which("git"):
        ext_path = os.path.expanduser(os.path.join(config_path, "Extensions"))
        if not os.path.exists(ext_path):
            os.makedirs(os.path.expanduser(path), exist_ok=True)
        print("Installing extension to: {ext_path}")
        result = subprocess.run(["git", "clone", url], cwd=ext_path, capture_output=True, text=True, check=False)
        print(result.stdout + result.stderr)
    else:
        print("git is needed to install extension")


def find_linux_sound(name):
    """Return path of sound file from its name, if it exists"""
    if sys.platform == "linux":
        path = os.path.join("/usr/share/sounds/freedesktop/stereo/", name + ".oga")
        if os.path.exists(path):
            return path


def notify_send(title, message, sound="message", custom_sound=None):
    """Send simple notification containing title and message. Cross-platform."""
    if sys.platform == "linux":
        if custom_sound:
            threading.Thread(target=play_audio, daemon=True, args=(custom_sound, )).start()
            include_sound = []
        elif no_notify_sound and fallback_notification_sound and have_notify_send:
            threading.Thread(target=play_audio, daemon=True, args=(fallback_notification_sound, )).start()
            include_sound = []
        else:
            include_sound = ["-h", f"string:sound-name:{sound}"]
        if have_termux_notify:
            command = ["termux-notification", "--icon=chat", "--sound", "--channel=1000", "-t", title, "-c", message]
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        elif have_notify_send:
            command = ["notify-send", "-p", "--app-name", APP_NAME, *include_sound, title, message]
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            try:
                return int(proc.communicate()[0].decode().strip("\n"))   # return notification id
            except ValueError:
                return None
        return None
    if sys.platform == "win32":
        if custom_sound:
            threading.Thread(target=play_audio, daemon=True, args=(custom_sound, )).start()
        notification = Toast()
        notification.text_fields = [message]
        toaster.show_toast(notification)
    elif sys.platform == "darwin":
        if custom_sound:
            threading.Thread(target=play_audio, daemon=True, args=(custom_sound, )).start()
        command = ["osascript", "-e", f"'display notification \"{message}\" with title \"{title}\"'"]
        _ = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return None


def notify_remove(notification_id):
    """Removes notification by its id. Linux only."""
    if sys.platform == "linux" and have_gdbus:
        command = ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications", "--object-path", "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.CloseNotification", str(notification_id)]
        _ = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def load_json(file, default=None, dir_path=config_path):
    """Load saved json from same location where default config is saved"""
    path = os.path.expanduser(os.path.join(dir_path, file))
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
            if default:
                for key, value in default.items():
                    _ = data.setdefault(key, value)
            return data
    except Exception:
        return default


def save_json(data, file, compact=False, dir_path=config_path):
    """Save json to same location where default config is saved"""
    if not os.path.exists(dir_path):
        os.makedirs(os.path.expanduser(dir_path), exist_ok=True)
    path = os.path.expanduser(os.path.join(dir_path, file))
    with open(path, "w") as f:
        if compact:
            json.dump(data, f, indent=None, separators=(",", ":"))
        else:
            json.dump(data, f, indent=2)


def copy_to_clipboard(text):
    """Copy text to clipboard. Cross-platform."""
    text = str(text)
    if sys.platform == "linux":
        if os.getenv("WAYLAND_DISPLAY"):
            try:
                proc = subprocess.Popen(
                    ["wl-copy"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.communicate(input=text.encode("utf-8"))
            except FileNotFoundError:
                logger.warning("Cant copy: wl-clipboard not found on system")
        else:
            try:
                proc = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.communicate(input=text.encode("utf-8"))
            except FileNotFoundError:
                logger.warning("Cant copy: xclip not found on system")
    elif sys.platform == "win32":
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text)
        win32clipboard.CloseClipboard()
    elif sys.platform == "darwin":
        proc = subprocess.Popen(
            ["pbcopy", "w"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(input=text.encode("utf-8"))


def paste_clipboard_files(save_path=None):
    """Get files paths from clipboard, linux only, needs xclip or wl-clipboard"""
    if sys.platform == "linux":

        if os.getenv("WAYLAND_DISPLAY"):
            list_command = ["wl-paste", "-l"]
            query_command = ["wl-paste", "-t"]
            list_types = ""
            suffix = ""
        else:
            list_command = ["xclip", "-selection", "clipboard", "-t"]
            query_command = list_command
            list_types = "TARGETS"
            suffix = "-o"

        try:
            # get types
            proc = subprocess.Popen(
                list_command[:] + ([list_types, suffix] if suffix else []),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            types_list = proc.communicate()[0].decode().split("\n")

            # binary image
            for num, data_type in enumerate(types_list):
                if data_type.startswith("image/"):
                    if not save_path:
                        return []
                    file_path = os.path.join(save_path, f"clipboard_image_{int(time.time())}." + types_list[num].split("/")[1])
                    with open(file_path, "wb") as f:
                        proc = subprocess.run(
                            query_command[:] + [types_list[num]] + ([suffix] if suffix else []),
                            stdout=f,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    return [file_path]

            # file path
            if "text/uri-list" in types_list:
                proc = subprocess.Popen(
                    query_command[:] + ["text/uri-list"] + ([suffix] if suffix else []),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                data = proc.communicate()[0].decode().strip("\n")
                return [line[7:] for line in data.splitlines() if line.startswith("file://")]

            # plain text or nothing
            if "text/plain" in types_list:
                proc = subprocess.Popen(
                    query_command[:] + ["text/plain"] + ([suffix] if suffix else []),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                return proc.communicate()[0].decode().strip("\n")

        except FileNotFoundError:
            logger.warning("Cant paste: wl-clipboard or xclip not found on system")
    return []


def native_select_files(file_filter=None, multiple=True, auto=False):
    """Get one or more file paths with native dialog"""
    if filedialog == "windows":
        init_dir = os.path.join(os.environ["USERPROFILE"], "Desktop")
    else:
        init_dir = os.path.expanduser("~") + "/"

    if sys.platform == "linux" and auto:
        cmd = ["yazi", "--chooser-file=/dev/stdout", "~"]
        data = subprocess.run(cmd, capture_output=True, text=True, check=False)
        paths = []
        if data.returncode == 0 and data.stdout.strip():
            paths = [line.strip() for line in data.stdout.strip().split("\n") if line.strip()]
        return paths

    if filedialog == "zenity":
        command = [
            "zenity", "--file-selection",
            "--title", "Import File",
            "--filename", init_dir,
        ]
        if multiple:
            command.append("--multiple")
        if file_filter:
            for one_filter in file_filter:
                command.append("--file-filter")
                command.append(one_filter)
        data = subprocess.run(command, capture_output=True, text=True, check=False)
        result = data.stdout.strip()
        if not result:
            return []
        if multiple:
            return result.split("|")
        return [result]

    if filedialog == "kdialog":
        command = [
            "kdialog", "--getopenfilename",
            init_dir,
            "--title", "Import File",
        ]
        if multiple:
            command.append("--multiple")
            command.append("--separate-output")
        if file_filter:
            command.append('"' + "|".join(file_filter) + '"')
        data = subprocess.run(command, capture_output=True, text=True, check=False)
        result = data.stdout.strip()
        if not result:
            return []
        if multiple:
            return result.splitlines()
        return [result]

    if filedialog == "windows":
        flags = win32con.OFN_FILEMUSTEXIST | win32con.OFN_EXPLORER
        if multiple:
            flags |= win32con.OFN_ALLOWMULTISELECT
        try:
            foreground = win32gui.GetForegroundWindow()
            result = win32gui.GetOpenFileNameW(
                hwndOwner=foreground,
                InitialDir=init_dir,
                Title="Upload Files",
                Flags=flags,
            )[0].split("\x00")
            if not result:
                return []
            if len(result) == 1:
                return result
            dir_path, *files = result   # first is directory rest are file names
            return [f"{dir_path}\\{f}" for f in files]
        except Exception:
            return []

    elif filedialog == "mac":
        command += f'choose file default location "{init_dir}"  with prompt "Import File"'
        data = subprocess.run(["osascript", "-"], input=command, text=True, capture_output=True, check=False)
        data = data.stdout.strip().split(",")
        return data[data.find(":"):].replace(":", "/")

    else:
        return "ERROR"

    return []


def get_file_size(path):
    """Get file size in bytes"""
    return os.stat(path).st_size


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


def play_audio(path):
    """Play audio file with simpleaudio or with pw-cat on pipewire"""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        logger.warn(f"Audio file not found at path: {path}")
        return

    if sys.platform == "linux":
        if have_pipewire:
            try:
                result = subprocess.run(["pw-cat", "-p", path], capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    return
                logger.error("pw-cat error:", result.stderr.strip() or result.stdout.strip())
            except Exception as e:
                logger.error(f"Failed to run pw-cat: {e}")
        elif have_pulseaudio:
            try:
                result = subprocess.run(["paplay", path], capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    return
                logger.error("paplay error:", result.stderr.strip() or result.stdout.strip())
            except Exception as e:
                logger.error(f"Failed to run paplay: {e}")
    elif sys.platform == "darwin" and have_afplay:
        try:
            result = subprocess.run(["afplay", path], capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return
            logger.error("afplay error:", result.stderr.strip() or result.stdout.strip())
        except Exception as e:
            logger.error(f"Failed to run afplay: {e}")
        return
    elif sys.platform == "win32":
        # no simple windows implementation
        pass

    # fallback to soundcard
    import soundfile
    try:
        data, samplerate = soundfile.read(path, dtype="float32")
    except Exception as e:
        logger.error(f"Error loading sound file: {e}")
    if not data:
        return
    soundcard = import_soundcard()
    if soundcard:
        speaker = soundcard.default_speaker()
        speaker.play(data, samplerate=samplerate)


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


def native_open(path, mpv_path="", yt_in_mpv=True):
    """Open media file in native application, cross-system"""
    if not path:
        return
    if path.startswith("https://") and "youtu" in path:
        if mpv_path and yt_in_mpv:
            current_runner = mpv_path
        else:
            webbrowser.open(path, new=0, autoraise=True)
            return
    else:
        current_runner = runner
    _ = subprocess.Popen(
        [current_runner, path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def find_aspell():
    """Find aspell exe path on windows system"""
    if sys.platform == "linux":
        if shutil.which("aspell"):
            return "aspell"
        return None
    if sys.platform == "win32":
        aspell_path = None
        for name in os.listdir(os.environ.get("ProgramFiles(x86)")):
            if "Aspell" in name:
                aspell_path = os.path.join(os.environ.get("ProgramFiles(x86)"), name, "bin\\aspell.exe")
                if not os.path.exists(aspell_path):
                    aspell_path = None
                break
        return aspell_path
    logger.info("Spellchecking not supported on this platform")


class SpellCheck():
    """Sentence and word spellchecker"""

    def __init__(self, aspell_mode, aspell_language):
        self.aspell_mode = aspell_mode
        self.aspell_language = aspell_language
        self.enable = False
        self.lock = threading.Lock()
        if aspell_mode:
            aspell_path = find_aspell()
            if aspell_path:
                self.aspell_path = aspell_path
                self.enable = True
                self.start_aspell()
            else:
                logger.info("Spellchecking disabled: Aspell not found")
        else:
            logger.info("Spellchecking disabled in config")


    def start_aspell(self):
        """Start aspell with selected mode and language"""
        # cross-platform replacement for pexpect.spawn() because aspell works with it
        self.proc = pexpect.popen_spawn.PopenSpawn(f"{self.aspell_path} -a --sug-mode={self.aspell_mode} --lang={self.aspell_language}", encoding="utf-8", codec_errors="replace")
        self.proc.delaybeforesend = None
        try:
            start = time.time()
            self.proc.expect(r"\r?\n", timeout=1)
            logger.info(f"Aspell initialized in {round((time.time() - start)*1000, 3)} ms")
        except pexpect.exceptions.EOF:
            logger.info("Aspell initialization %s", "error")
            self.enable = False
        except pexpect.exceptions.TIMEOUT:
            logger.info("Aspell initialization %s", "timed out")
            self.enable = False


    def check_word_pexpect(self, word):
        """Spellcheck single word with aspell"""
        if word.isdigit():
            return False   # dont spellcheck numbers
        with self.lock:
            try:
                self.proc.sendline(word)
                status = self.proc.expect([r"^[&\#\*].*\r?\n", r"\r?\n", pexpect.TIMEOUT], timeout=ASPELL_TIMEOUT)
                if status == 0:
                    line = self.proc.after.strip()
                    return line[0] != "*"
                return False
            except pexpect.exceptions.TIMEOUT:
                return False   # if timed-out return it as correct
            except pexpect.exceptions.EOF:
                if self.enable:
                    self.start_aspell()
                return False


    # def check_word_subprocess(self, word):
    #     """Spellcheck single word with aspell"""
    #     try:
    #         proc = subprocess.Popen(
    #             [self.aspell_path, "-a", f"--sug-mode={self.aspell_mode}", f"--lang={self.aspell_language}"],
    #             stdin=subprocess.PIPE,
    #             stdout=subprocess.PIPE,
    #             stderr=subprocess.DEVNULL,
    #         )
    #         output, error = proc.communicate(word.encode())
    #         check = output.decode().split("\n")[1]
    #         if check == "*":
    #             return False
    #         return True
    #     except FileNotFoundError:   # aspell not installed
    #         return False


    def check_list(self, words):
        """
        Spellcheck a list of words with aspell.
        Return list of bools representing whether each word is misspelled or not.
        """
        misspelled = []
        if self.enable:
            for word in words:
                if word == "":
                    misspelled.append(False)
                else:
                    misspelled.append(self.check_word_pexpect(word))
        else:
            return [False] * len(words)
        return misspelled


class Recorder():
    """Sound recorder"""

    def __init__(self):
        self.recording = False
        self.audio_data = []


    def record(self):
        """Continuously record audio"""
        timer = 0
        soundcard = import_soundcard()
        if soundcard:
            try:
                mic = soundcard.default_microphone()
            except Exception as e:
                logger.warning(f"No microphone found. Error: {e}")
                self.recording = False
                return
        else:
            logger.warning("Failed connecting to sound system")
            self.recording = False
            return
        with mic.recorder(samplerate=48000, channels=1) as rec:
            while self.recording:
                if timer >= 600:   # 10min limit
                    del self.audio_data
                    self.recording = False
                    break
                data = rec.record(numframes=48000)
                self.audio_data.append(data)
                timer += 1


    def start(self):
        """Start continuously recording audio"""
        if not self.recording:
            self.recording = True
            self.audio_data = []
            self.record_thread = threading.Thread(target=self.record, daemon=True)
            self.record_thread.start()


    def stop(self):
        """Stop recording audio and return file path"""
        import numpy as np
        import soundfile
        if self.recording:
            self.recording = False
            self.record_thread.join()
            self.record_thread = None
            self.audio_data = np.concatenate(self.audio_data, axis=0)
            save_path = os.path.join(temp_path, "rec-audio-message.ogg")
            soundfile.write(save_path, self.audio_data, 48000, format="OGG", subtype="OPUS")
            del self.audio_data
            return save_path


class Player():
    """Sound loop player"""

    def __init__(self):
        self.playing = False
        self.play_thread = None


    def play_loop(self, file_path, loop, loop_delay, loop_max):
        """Play sound file in a loop"""
        try:
            soundcard = import_soundcard()
            if soundcard:
                import soundfile
                with soundfile.SoundFile(file_path) as sound:
                    sample_rate = sound.samplerate
                    channels = sound.channels
                    block_size = sample_rate // 20
                    speaker = soundcard.default_speaker()
                    start = time.time()
                    sleep_time = block_size / sample_rate
                    with speaker.player(samplerate=sample_rate, channels=channels) as stream:
                        sound.seek(0)
                        while self.playing:
                            read_start = time.perf_counter()
                            if time.time() - start >= loop_max:
                                self.playing = False
                                break
                            if sound.tell() >= len(sound):
                                if loop:
                                    sound.seek(0)
                                    time.sleep(loop_delay)
                                    read_start = time.perf_counter()
                                else:
                                    break
                            frames = sound.read(block_size, dtype="float32")
                            read_time = time.perf_counter() - read_start
                            stream.play(frames)
                            time.sleep(sleep_time - read_time)
        except Exception as e:
            logger.error(f"Playback error: {e}")
            self.playing = False


    def start(self, file_path, loop=False, loop_delay=1, loop_max=60):
        """Start playing from beginning on loop"""
        if not self.playing:
            self.playing = True
            self.play_thread = threading.Thread(target=self.play_loop, daemon=True, args=(file_path, loop, loop_delay, loop_max))
            self.play_thread.start()


    def stop_playback(self):
        """Stop playback immediately"""
        if self.playing:
            self.playing = False
        if self.play_thread:
            self.play_thread.join()


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
