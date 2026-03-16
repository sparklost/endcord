import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

logger = logging.getLogger(__name__)
REPO_OWNER = "sparklost"
APP_NAME = "endcord"
VERSION = "1.3.0"
NO_NOTIFY_SOUND_DE = ("kde", "plasma")   # linux desktops without notification sound

# platform specific code
have_termux_notify = False
if sys.platform == "win32":
    import win32clipboard
    from windows_toasts import Toast, ToastDisplayImage, WindowsToaster
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
        config_path = os.path.join(path, APP_NAME)
        log_path = os.path.join(path, APP_NAME)
    else:
        config_path = f"~/.config/{APP_NAME}"
        log_path = f"~/.config/{APP_NAME}"
    path = os.environ.get("XDG_RUNTIME_DIR", "")
    if path.strip():
        temp_path = os.path.join(path, APP_NAME)
    else:
        # per-user temp dir
        temp_path = f"/run/user/{os.getuid()}/{APP_NAME}"
        # fallback to .cache
        if not os.access(f"/run/user/{os.getuid()}", os.W_OK):
            temp_path = f"~/.cache/{APP_NAME}/temp"
    os.makedirs(os.path.expanduser(temp_path), exist_ok=True)
    path = os.environ.get("XDG_CACHE_HOME", "")
    if path.strip():
        cache_path = os.path.join(path, APP_NAME)
    else:
        cache_path = f"~/.cache/{APP_NAME}"
    path = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if path.strip():
        downloads_path = os.path.join(path, APP_NAME)
    else:
        downloads_path = "~/Downloads"
elif sys.platform == "win32":
    config_path = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME)
    log_path = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME)
    temp_path = os.path.join(os.environ["LOCALAPPDATA"], "Temp", APP_NAME)
    cache_path = os.path.join(os.environ["LOCALAPPDATA"], APP_NAME, "Cache")
    downloads_path = os.path.join(os.environ["USERPROFILE"], "Downloads")
elif sys.platform == "darwin":
    config_path = f"~/Library/Application Support/{APP_NAME}"
    log_path = f"~/Library/Application Support/{APP_NAME}"
    temp_path = f"~/Library/Caches/TemporaryItems/{APP_NAME}"
    cache_path = f"~/Library/Caches/{APP_NAME}"
    downloads_path = "~/Downloads"
else:
    print(f"Unsupported platform: {sys.platform}", file=sys.stderr)
    sys.exit(1)


# ensure paths exists
for app_path in (config_path, log_path, temp_path, cache_path, downloads_path):
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


def notify_send(title, message, sound="message", image_path=None, custom_sound=None):
    """Send simple notification containing title, message and optionally image, with optional custom notification sound. Cross-platform."""
    if image_path:
        image_path = os.path.expanduser(image_path)
    image_path = make_round_image(image_path)
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
            if image_path:
                command.insert(2, "--image-path")
                command.insert(3, image_path)
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        elif have_notify_send:
            command = ["notify-send", "-p", "--app-name", APP_NAME, *include_sound, title, message]
            if image_path:
                command.insert(4, "-i")
                command.insert(5, image_path)
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
        if image_path:
            notification.AddImage(ToastDisplayImage.fromPath(image_path))
        toaster.show_toast(notification)
    elif sys.platform == "darwin":
        if custom_sound:
            threading.Thread(target=play_audio, daemon=True, args=(custom_sound, )).start()
        command = ["osascript", "-e", f"'display notification \"{message}\" with title \"{title}\"'"]
        # osascript cant display image in notification
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
    save_path = os.path.expanduser(save_path)
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


def pillow_paste_image():
    """If there is image in clipboard, save it to temp path using pillow"""
    from PIL import Image, ImageGrab
    img = ImageGrab.grabclipboard()
    if isinstance(img, Image.Image):
        save_path = os.path.join(os.path.expanduser(temp_path), f"clipboard_image_{int(time.time())}.png")
        img.save(save_path)
        return [save_path]
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
        self.aspell = None
        self.aspell_mode = aspell_mode
        self.aspell_language = aspell_language
        self.enable = False
        self.first_run = True
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
        if self.aspell:
            return
        try:
            start = time.time()
            self.aspell = subprocess.Popen(
                [self.aspell_path, "-a", "--sug-mode", self.aspell_mode, "--lang", self.aspell_language],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self.aspell.stdout.readline()
            logger.info(f"Aspell initialized in {round((time.time() - start)*1000, 3)} ms")
        except Exception as e:
            if self.aspell.poll() is not None:
                aspell_error = self.aspell.stderr.read()
            else:
                aspell_error = ""
            logger.error(f"Aspell initialization error: {e}\n  {aspell_error}")
            self.enable = False


    def check_word(self, word):
        """Spellcheck single word using aspell"""
        if not self.aspell:
            return False
        if word.isdigit():
            return False   # dont spellcheck numbers
        try:
            with self.lock:
                self.aspell.stdin.write(word + "\n")
                self.aspell.stdin.flush()
                result = self.aspell.stdout.readline().strip()
                next_line = result
                while next_line != "\n":   # read until it prints empty line
                    next_line = self.aspell.stdout.readline()
                if result.startswith("*"):
                    return False
                return True
        except Exception as e:
            if self.aspell.poll() is not None:
                aspell_error = self.aspell.stderr.read()
            else:
                aspell_error = ""
            logger.error(f"Spellchecker error: {e}\n  {aspell_error}")
            if self.first_run:   # a fuse if it fails on first word
                self.enable = False
            if self.enable:
                self.stop_aspell()
                self.start_aspell()
            return False
        self.first_run = False


    def stop_aspell(self):
        """Nicely stop aspell process"""
        if not self.aspell:
            return
        self.aspell.stdin.close()
        self.aspell.terminate()
        self.aspell.wait()
        self.aspell = None


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
                    misspelled.append(self.check_word(word))
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
            save_path = os.path.join(os.path.expanduser(temp_path), "rec-audio-message.ogg")
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


def make_round_image_pillow(input_path, output_path):
    """Create new image with circular shape using pillow"""
    from PIL import Image, ImageDraw
    img = Image.open(input_path).convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, w, h), fill=255)
    result = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    result.save(output_path, "WEBP")


def make_round_image_imagemagick(input_path, output_path):
    """Create new image with circular shape using imagemagick"""
    subprocess.run([
        "magick", input_path,
        "(",
            "+clone",
            "-alpha", "transparent",
            "-fill", "white",
            "-draw", "circle %[fx:w/2],%[fx:h/2] %[fx:w/2],0",
        ")",
        "-alpha", "set",
        "-compose", "DstIn",
        "-composite",
        output_path,
    ], check=True)


def make_round_image(image_path):
    """
    Convert image to round image and delete old one, if possible.
    Use pillow if available, fallback to imagemagick if available.
    Save image as _round and delete original, and dont re-edit same image.
    """
    if not image_path or not os.path.exists(image_path):
        return None
    if "_round" in image_path:
        return image_path
    if importlib.util.find_spec("PIL") is not None:
        base, ext = os.path.splitext(image_path)
        save_path = base + "_round" + ext
        make_round_image_pillow(image_path, save_path)
        os.remove(image_path)
        return save_path
    if shutil.which("magick"):
        try:
            base, ext = os.path.splitext(image_path)
            save_path = base + "_round" + ext
            make_round_image_imagemagick(image_path, save_path)
            os.remove(image_path)
            return save_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    return image_path
