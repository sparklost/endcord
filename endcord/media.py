# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from queue import Queue

import filetype
from PIL import Image, ImageEnhance

try:
    import av
    video_support = True
except ImportError:
    video_support = False


# safely import soundcard, in case there is no sound system
try:
    import soundcard
    have_soundcard = True
except (AssertionError, RuntimeError):
    have_soundcard = False

from endcord import terminal_utils, xterm256

BASE_SOUND_GAIN = 1.0
ESC = "\x1b"
RESET = f"{ESC}[0m"

logger = logging.getLogger(__name__)
match_youtube = re.compile(r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}")


def get_mime(path):
    """Try to get mime type of the file"""
    kind = filetype.guess(path)
    if kind:
        return kind.mime
    return "unknown/unknown"


def volume_to_gain(volume):
    """Convert volume value 0-200 to gain, above 100 is boost up to x5"""
    volume = max(min(volume, 200), 0)
    if volume == 0:
        return 0.0
    volume = volume / 100
    if volume <= 1.0:
        db = -30.0 + 30.0 * volume
    else:
        db = 14 * (volume - 1.0) ** 1.5
    return BASE_SOUND_GAIN * (10 ** (db / 20.0))


def img_to_term(img, img_gray, bg_color, ascii_palette, ascii_palette_len, screen_width, screen_height, img_width, img_height):
    """Convert image to ANSI-colored string made of ascii_palette, ready be printed in terminal"""
    pixels = img.load()
    pixels_gray = img_gray.load()

    padding_h = (screen_height - img_height) // 2
    padding_w = (screen_width - img_width) // 2

    bg = f"{ESC}[48;5;{bg_color}m"
    out_lines = []

    # top padding
    for _ in range(padding_h):
        out_lines.append(bg + (" " * screen_width) + RESET)

    # image rows
    for y in range(img_height):
        line_parts = []
        current_fg = None

        # left padding
        if padding_w > 0:
            line_parts.append(bg + (" " * padding_w))

        # image columns
        for x in range(img_width):
            gray_val = pixels_gray[x, y]
            color = pixels[x, y] + 16
            if color != current_fg:
                line_parts.append(f"{ESC}[38;5;{color}m")
                current_fg = color
            line_parts.append(ascii_palette[(gray_val * ascii_palette_len) // 255])

        # right padding
        visible_len = padding_w + img_width
        if visible_len < screen_width:
            line_parts.append(bg + (" " * (screen_width - visible_len)))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height:
        out_lines.append(bg + (" " * screen_width) + RESET)

    return "\n".join(out_lines)


def img_to_term_block(data, bg_color, screen_width, screen_height, img_width, img_height):
    """Convert image to ANSI-colored string made of half-blocks, ready to be printed in terminal"""
    padding_h = (screen_height - img_height // 2) // 2
    padding_w = (screen_width - img_width) // 2

    bg = f"{ESC}[48;5;{bg_color}m"
    out_lines = []

    # top padding
    for _ in range(padding_h):
        out_lines.append(bg + (" " * screen_width) + RESET)

    # image rows
    for y in range(0, img_height - 1, 2):
        line_parts = []
        current_fg = None
        current_bg = None

        # left padding
        if padding_w > 0:
            line_parts.append(bg + (" " * padding_w))

        # image columns
        for x in range(img_width):
            top_color = data[y * img_width + x] + 16
            bot_color = data[(y + 1) * img_width + x] + 16
            if top_color != current_fg:
                line_parts.append(f"{ESC}[38;5;{top_color}m")
                current_fg = top_color
            if bot_color != current_bg:
                line_parts.append(f"{ESC}[48;5;{bot_color}m")
                current_bg = bot_color
            line_parts.append("▀")

        # right padding
        visible_len = padding_w + img_width
        if visible_len < screen_width:
            line_parts.append(bg + (" " * (screen_width - visible_len)))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height:
        out_lines.append(bg + (" " * screen_width) + RESET)
    return "\n".join(out_lines)


def img_to_term_block_truecolor(data, bg_color, screen_width, screen_height, img_width, img_height):
    """Convert image to ANSI true-color string made of half-blocks"""
    padding_h = (screen_height - img_height // 2) // 2
    padding_w = (screen_width - img_width) // 2

    bgr = f"{ESC}[48;5;{bg_color}m"   # bg color is not in r;g;b
    out_lines = []

    # top padding
    for _ in range(padding_h):
        out_lines.append(bgr + (" " * screen_width) + RESET)

    # image rows
    for y in range(0, img_height - 1, 2):
        line_parts = []
        cfr, cfg, cfb = None, None, None
        cbr, cbg, cbb = None, None, None

        # left padding
        if padding_w > 0:
            line_parts.append(bgr + (" " * padding_w))

        # image columns
        row_top = y * img_width * 3
        for x in range(img_width):
            idx = row_top + x * 3
            fr = data[idx]
            fg = data[idx + 1]
            fb = data[idx + 2]
            idx += img_width * 3
            br = data[idx]
            bg = data[idx + 1]
            bb = data[idx + 2]
            if fr != cfr or fg != cfg or fb != cfb:
                line_parts.append(f"{ESC}[38;2;{fr};{fg};{fb}m")
                cfr, cfg, cfb = fr, fg, fb
            if br != cbr or bg != cbg or bb != cbb:
                line_parts.append(f"{ESC}[48;2;{br};{bg};{bb}m")
                cbr, cbg, cbb = br, bg, bb
            line_parts.append("▀")

        # right padding
        visible_len = padding_w + img_width
        if visible_len < screen_width:
            line_parts.append(bgr + (" " * (screen_width - visible_len)))

        line_parts.append(RESET)
        out_lines.append("".join(line_parts))

    # bottom padding
    while len(out_lines) < screen_height:
        out_lines.append(bgr + (" " * screen_width) + RESET)

    return "\n".join(out_lines)


# use cython if available, ~5 times faster
try:
    from endcord_cython.media import (
        img_to_term,
        img_to_term_block,
        img_to_term_block_truecolor,
    )
except ImportError:
    pass

# get speaker
if have_soundcard:
    try:
        speaker = soundcard.default_speaker()
        have_sound = True
    except Exception:
        have_sound = False
else:
    have_sound = False


class TerminalMedia():
    """Methods for showing and playing media in terminal"""

    def __init__(self, config, keybindings, ui=True, external=False, volume=100, font_ratio=2.25):
        logging.getLogger("libav").setLevel(logging.ERROR)
        self.truecolor = config["media_truecolor"]
        self.use_blocks = config["media_use_blocks"]
        self.font_ratio = font_ratio   # 2.25
        self.font_ratio_block = self.font_ratio / 2
        self.ascii_palette = list(config["media_ascii_palette"])   # "  ..',;:c*loexk#O0XNW"
        self.saturation = config["media_saturation"]   # 1.2
        self.cap_fps = config["media_cap_fps"]   # 30
        self.bg_color = config["media_color_bg"]   # -1
        self.mute_video = config["media_mute"]   # false
        self.bar_ch = config["media_bar_ch"]
        self.default_color = config["color_default"][0]   # all 255 colors already init in order
        self.yt_dlp_path = config["yt_dlp_path"]
        self.yt_dlp_format = config["yt_dlp_format"]
        self.keybindings = {}
        for key, value in keybindings.items():
            if not key.startswith("media_"):
                continue
            self.keybindings[key] = value[0] if isinstance(value[0], str) else value[0][0]
        if self.default_color == -1:
            self.default_color = 0
        self.external = external
        if external:
            signal.signal(signal.SIGINT, self.sigint_handler)
        self.ascii_palette_len = len(self.ascii_palette) - 1
        self.xterm_256_palette = xterm256.palette_short
        self.run = False
        self.playing = False
        self.ended = False
        self.pause = False
        self.pause_after_seek = False
        self.path = None
        self.media_type = None
        self.seek = None
        self.video_duration = 0
        self.video_time = 0
        self.video_h, self.video_w = None, None
        self.frame_h, self.frame_w = None, None
        self.screen_height, self.screen_width = 0, 0
        self.img = None
        self.ui = ui
        self.ui_line = None
        if ui:
            self.ui_timer = 0
        else:
            self.ui_timer = 30
        if self.use_blocks:
            self.pil_img_to_term = self.pil_img_to_term_block
        if self.truecolor:   # select drawing algorithm
            self.img_to_term_block = img_to_term_block_truecolor
        else:
            self.img_to_term_block = img_to_term_block
        self.volume = volume
        self.gain = volume_to_gain(volume)
        self.audio_queue = Queue(maxsize=10)
        self.video_queue = Queue(maxsize=10)
        self.times = []


    def sigint_handler(self, _signum, _frame):
        """Handling Ctrl-C event"""
        self.stop_playback()
        time.sleep(0.2)
        terminal_utils.leave_tui()
        sys.exit(0)   # failsafe


    def calculate_image_size(self):
        """Calculate image size for video player so it fits the screen area (image that will be used in img_to_term methods)"""
        if not self.video_h:
            return
        screen_height, screen_width = terminal_utils.get_size()
        height = screen_height * (2 if self.use_blocks else 1)
        width = screen_width
        wpercent = width / (self.video_w * (self.font_ratio_block if self.use_blocks else self.font_ratio))
        hsize = int(self.video_h * wpercent)
        if hsize > height:
            hpercent = height / self.video_h
            wsize = int(self.video_w * hpercent * (self.font_ratio_block if self.use_blocks else self.font_ratio))
            width = wsize
        else:
            height = hsize
        if self.use_blocks:
            height &= ~1   # must be even height
        self.screen_height, self.screen_width = screen_height, screen_width
        self.frame_h, self.frame_w = height, width


    def calculate_image_size_anim(self, img_h, img_w):
        """Calculate image size for video player so it fits the screen area (image that will be used in img_to_term methods)"""
        screen_height, screen_width = terminal_utils.get_size()
        height = screen_height * (2 if self.use_blocks else 1)
        width = screen_width
        wpercent = width / (img_w * (self.font_ratio_block if self.use_blocks else self.font_ratio))
        hsize = int(img_h * wpercent)
        if hsize > height:
            hpercent = height / img_h
            wsize = int(img_w * hpercent * (self.font_ratio_block if self.use_blocks else self.font_ratio))
            width = wsize
        else:
            height = hsize
        if self.use_blocks:
            height &= ~1   # must be even height
        self.screen_height, self.screen_width = screen_height, screen_width
        self.frame_h, self.frame_w = height, width


    def pil_img_to_term(self, img, remove_alpha=True):
        """Convert pillow image to ascii art and display it in terminal with media controls if needed"""
        # scale image preserving aspect ratio
        if self.frame_h:
            screen_height, screen_width = self.screen_height, self.screen_width
            height, width = self.frame_h, self.frame_w
        else:
            screen_height, screen_width = terminal_utils.get_size()
            height, width = terminal_utils.get_size()
            wpercent = width / (img.size[0] * self.font_ratio)
            hsize = int(img.size[1] * wpercent)
            if hsize > height:
                hpercent = height / img.size[1]
                wsize = int(img.size[0] * hpercent * self.font_ratio)
                width = wsize
            else:
                height = hsize

            img = img.resize((width, height), Image.Resampling.LANCZOS)
        img_gray = img.convert("L")

        # increase saturation
        if self.saturation:
            sat = ImageEnhance.Color(img)
            img = sat.enhance(self.saturation)

        # remove alpha
        if remove_alpha and img.mode != "RGB" and img.mode != "L":
            background = Image.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background

        # apply xterm256 palette
        img_palette = Image.new("P", (16, 16))
        img_palette.putpalette(self.xterm_256_palette)
        img = img.quantize(palette=img_palette, dither=0)

        # draw
        string = img_to_term(
            img,
            img_gray,
            self.bg_color,
            self.ascii_palette,
            self.ascii_palette_len,
            screen_width,
            screen_height,
            width,
            height,
        )
        if self.ui_line:
            string = string[:string.rfind("\n") + 1] + f"\n{ESC}[48;5;{self.bg_color}m{self.ui_line}{RESET}"
        terminal_utils.draw(string)


    def pil_img_to_term_block(self, img, remove_alpha=True):
        """Convert pillow image to half-block terminal output"""
        # scale image preserving aspect ratio
        if self.frame_h:
            screen_height, screen_width = self.screen_height, self.screen_width
            height, width = self.frame_h, self.frame_w
        else:
            screen_height, screen_width = terminal_utils.get_size()
            height = screen_height * 2
            width = screen_width
            wpercent = width / float(img.size[0] * self.font_ratio_block)
            hsize = int(img.size[1] * wpercent)
            if hsize > height:
                hpercent = height / float(img.size[1])
                wsize = int(img.size[0] * hpercent * self.font_ratio_block)
                width = wsize
            else:
                height = hsize
            height &= ~1   # must be even height
            img = img.resize((width, height), Image.Resampling.LANCZOS)

        # remove alpha
        if remove_alpha and img.mode == "RGBA":
            background = Image.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background

        if self.truecolor:
            # ensure its rgb
            img = img.convert("RGB")
        else:
            # apply xterm256 palette
            img_palette = Image.new("P", (16, 16))
            img_palette.putpalette(self.xterm_256_palette)
            img = img.quantize(palette=img_palette, dither=0)

        # draw
        string = self.img_to_term_block(   # truecolor is selected at init
            img.tobytes(),
            self.bg_color,
            screen_width,
            screen_height,
            width,
            height,
        )
        if self.ui_line:
            string = string[:string.rfind("\n") + 1] + f"{ESC}[48;5;{self.bg_color}m{self.ui_line}{RESET}"
        terminal_utils.draw(string)


    def draw_blank(self):
        """Fill screen with bg_color"""
        screen_size = terminal_utils.get_size()
        bg = f"{ESC}[48;5;{self.bg_color}m"
        line = bg + (" " * screen_size[1]) + RESET
        string = "\n".join(line for _ in range(screen_size[0]))
        if self.ui_line:
            string += f"\n{bg}{self.ui_line}{RESET}"
        terminal_utils.draw(string)


    def play_img(self, img_path):
        """Draw any image in terminal. If image is animated (eg apng) send it to play_animated instead. """
        img = Image.open(img_path)
        if hasattr(img, "is_animated") and img.is_animated:
            self.media_type = "gif"
            self.play_animated(img_path)
            return
        self.hide_ui()
        self.pil_img_to_term(img)
        while self.run:
            h, w = terminal_utils.get_size()
            if self.screen_height != h or self.screen_height != w:
                self.screen_height, self.screen_width = h, w
                self.pil_img_to_term(img)
            time.sleep(0.1)
        self.stop_playback()


    def play_animated(self, gif_path):
        """Draw animated image in terminal"""
        self.hide_ui()
        self.screen_height, self.screen_width = 0, 0
        gif = Image.open(gif_path)
        img_w, img_h = gif.size

        frame_duration = gif.info.get("duration", 100) / 1000
        loop = bool(gif.info.get("loop", 1))
        frame = 0
        while self.playing:
            start_time = time.time()

            h, w = terminal_utils.get_size()
            if self.screen_height != h or self.screen_width != w:
                self.calculate_image_size_anim(img_h, img_w)
                frames = []
                try:
                    gif.seek(0)
                    while True:
                        frame_canvas = Image.new("RGB", gif.size)
                        frame_canvas.paste(gif)
                        frames.append(frame_canvas.resize((self.frame_w, self.frame_h), Image.Resampling.LANCZOS))
                        gif.seek(gif.tell() + 1)
                except EOFError:
                    pass
                if not frames:
                    break

            img = frames[frame]
            try:
                self.pil_img_to_term(img, remove_alpha=False)
            except IndexError:
                pass

            frame += 1
            if frame >= len(frames):
                if loop:
                    break
                frame = 0
            time.sleep(max(frame_duration - (time.time() - start_time), 0))


    def play_audio(self, path, loop=False, loop_delay=0.7, loop_max=60):
        """Play only audio"""
        if self.ui:
            self.show_ui()
        if not have_sound:
            self.ended = True
            return
        if self.ui:
            self.draw_blank()
        container = av.open(path)
        self.ended = False
        all_audio_streams = container.streams.audio
        if not all_audio_streams:   # no audio?
            return
        audio_stream = all_audio_streams[0]

        with speaker.player(samplerate=audio_stream.rate, channels=audio_stream.channels, blocksize=1152) as stream:
            start = int(time.time())
            while self.playing:
                for frame in container.decode(audio=0):
                    if not self.playing:
                        break
                    while self.pause:
                        time.sleep(0.1)
                    audio = frame.to_ndarray().astype("float32").T
                    audio *= self.gain
                    stream.play(audio)
                if loop:
                    if int(time.time()) - start > loop_max:
                        break
                    time.sleep(loop_delay)
                    container.seek(0)
                else:
                    break
        self.ended = True


    def play_audio_noui(self, path, loop=False, loop_delay=1, loop_max=60):
        """Play audio without UI"""
        self.ui = False
        self.playing = True
        self.run = True
        self.pause = False
        self.player_thread = threading.Thread(target=self.play_audio, daemon=True, args=(path, loop, loop_delay, loop_max))
        self.player_thread.start()


    def stop_playback(self):
        """Stop all playbacks immediately"""
        self.clear_queues()
        self.screen_height, self.screen_width = 0, 0
        self.pause = False
        self.run = False
        self.playing = False


    def clear_queues(self):
        """Clear audio and video queues"""
        while not self.audio_queue.empty():
            self.audio_queue.get()
        while not self.video_queue.empty():
            self.video_queue.get()


    def audio_player(self, audio_queue, samplerate, channels, audio_ready):
        """Play audio frames from the queue"""
        with speaker.player(samplerate=samplerate, channels=channels, blocksize=1152) as stream:
            audio_ready.set()
            while self.run:
                frame = audio_queue.get()
                if frame is None:
                    break
                audio = frame.to_ndarray().astype("float32").T
                audio *= self.gain
                stream.play(audio)
                while self.pause:
                    time.sleep(0.1)


    def video_player(self, video_queue, audio_queue, frame_duration, no_audio=False):
        """Play video frames from the queue"""
        while self.run:
            frame = video_queue.get()
            if frame is None:
                break
            if audio_queue.qsize() >= 1 or no_audio:
                start_time = time.time()
                self.img = frame.to_image()
                try:
                    self.pil_img_to_term(self.img, remove_alpha=False)
                except IndexError:
                    pass
                h, w = terminal_utils.get_size()
                if self.screen_height != h or self.screen_width != w:
                    self.calculate_image_size()
            if audio_queue.qsize() >= 3 or no_audio:
                time.sleep(max(frame_duration - (time.time() - start_time), 0))
            while self.pause:
                time.sleep(0.1)


    def play_video(self, path, loop=False):
        """Decode video and audio frames and manage queues"""
        if loop:
            self.ui = False
        if self.ui:
            self.show_ui()
        self.seek = None
        self.screen_height, self.screen_width = 0, 0
        self.clear_queues()

        container = av.open(path)
        self.ended = False
        self.video_time = 0

        video_stream = container.streams.video[0]
        if video_stream.duration:
            self.video_duration = float(video_stream.duration * video_stream.time_base)
        else:
            self.video_duration = video_stream.frames / video_stream.average_rate * video_stream.time_base
        if self.video_duration == 0:
            self.video_duration = 1   # just in case
        video_fps = video_stream.guessed_rate
        frame_duration = 1 / video_fps
        frame_index = max(int(video_fps / self.cap_fps), 1)
        self.video_h, self.video_w = video_stream.height, video_stream.width
        self.calculate_image_size()

        # prepare audio
        have_audio = False
        if not self.mute_video and have_sound:
            all_audio_streams = container.streams.audio
            if all_audio_streams:   # in case of a muted video
                audio_ready = threading.Event()
                have_audio = True
                audio_stream = all_audio_streams[0]
                audio_thread = threading.Thread(target=self.audio_player, args=(self.audio_queue, audio_stream.rate, audio_stream.channels, audio_ready), daemon=True)
                audio_thread.start()
                audio_ready.wait()   # wait for audio to decrease desyncing
                audio_ready.clear()

        # prepare video
        video_thread = threading.Thread(target=self.video_player, args=(self.video_queue, self.audio_queue, frame_duration, not (have_audio)), daemon=True)
        video_thread.start()

        while self.playing:
            num = 0
            for frame in container.decode():
                if self.seek is not None:
                    container.seek(int(self.seek / video_stream.time_base), stream=video_stream)
                    self.video_time = self.seek
                    self.seek = None
                    if self.pause_after_seek:
                        self.pause_after_seek = False
                        self.pause = True
                        self.ui_line = self.build_ui_string()
                        self.show_ui()
                    continue
                if not self.playing:
                    container.close()
                    break
                while self.pause:
                    time.sleep(0.1)
                if self.pause_after_seek:
                    continue
                if isinstance(frame, av.audio.frame.AudioFrame) and have_audio:
                    self.audio_queue.put(frame)
                if isinstance(frame, av.video.frame.VideoFrame):
                    if num == frame_index:   # limit fps
                        self.video_queue.put(frame.reformat(height=self.frame_h, width=self.frame_w))
                        num = 0
                    num += 1
                    self.video_time += frame_duration
            if loop and self.playing:
                container.seek(0)
                self.video_time = 0
            else:
                break

        self.audio_queue.put(None)
        self.video_queue.put(None)
        if have_audio:
            audio_thread.join()
        video_thread.join()
        self.ended = True


    def play_youtube(self, url):
        """Get youtube video stream and play video"""
        if shutil.which(self.yt_dlp_path):
            command = [self.yt_dlp_path, "-f", str(self.yt_dlp_format), "-g", url]
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stream_url = proc.communicate()[0].decode().strip("\n")
            self.path = stream_url
            self.media_type = "video"
            self.start_ui_thread()
            self.play_video(stream_url)
        else:
            logger.warning("Cant play youtube link, yt-dlp path is invalid")


    def play(self, path, hint=None, loop=False):
        """Select runner based on file type"""
        if not path:
            return
        if not path.startswith("http") and not os.path.exists(path):
            return
        if os.path.isdir(path):
            return

        self.path = path
        self.ui = True
        self.run = True
        self.playing = True

        terminal_utils.enter_tui()
        input_thread = threading.Thread(target=self.wait_input, daemon=True)
        input_thread.start()
        try:
            yt_match = re.search(match_youtube, path)
            if yt_match:
                self.play_youtube(yt_match.group())
            elif "https://" in path:
                if not video_support:
                    self.run = False
                    self.playing = False
                    return True
                self.media_type = "video"
                self.start_ui_thread(loop)
                self.play_video(path)
            else:
                mime = get_mime(path).split("/")
                if hint:
                    mime = [hint, None]
                if mime[0] == "image":
                    if mime[1] == "gif":
                        self.media_type = "gif"
                        self.play_animated(path)
                    else:
                        self.media_type = "img"
                        self.play_img(path)
                elif mime[0] == "video":
                    if not video_support:
                        self.run = False
                        self.playing = False
                        return True
                    self.media_type = "video"
                    self.start_ui_thread(loop)
                    self.play_video(path, loop)
                elif mime[0] == "audio":
                    if not video_support:
                        self.run = False
                        self.playing = False
                        return True
                    self.media_type = "audio"
                    self.start_ui_thread(loop)
                    self.play_audio(path, loop=loop)
                else:
                    logger.warning(f"Unsupported media format: {mime}")
                    self.run = False
                    if self.external:
                        print(f"Unsupported media format: {mime}", file=sys.stderr)
                        sys.exit(1)
            while self.run:   # dont exit when video ends
                time.sleep(0.2)
        except Exception as e:
            logger.exception("Error playing media:")
            if self.external:
                print("".join(traceback.format_exception(e)))
        terminal_utils.leave_tui()
        self.run = False
        self.playing = False


    def control_codes(self, code):
        """Handle controls from TUI"""
        if code == 100:   # quit media player
            self.stop_playback()
        elif code == 101 and self.media_type in ("audio", "video"):   # pause
            self.show_ui()
            self.pause = not self.pause
        elif code == 102 and self.media_type in ("audio", "video"):   # replay
            self.show_ui()
            self.pause = False
            self.seek = 0
            if self.ended:
                self.show_ui()
                self.playing = True
                if self.media_type == "video":
                    self.player_thread = threading.Thread(target=self.play_video, daemon=True, args=(self.path, ))
                    self.player_thread.start()
                elif self.media_type == "audio":
                    self.player_thread = threading.Thread(target=self.play_audio, daemon=True, args=(self.path, ))
                    self.player_thread.start()
        elif code == 103 and self.media_type in ("audio", "video") and not self.ended:   # seek forward
            self.show_ui()
            self.clear_queues()
            if self.pause:
                self.pause = False
                self.pause_after_seek = True
            self.seek = min(self.video_time + 5, self.video_duration)
        elif code == 104 and self.media_type in ("audio", "video") and not self.ended:   # seek backward
            self.show_ui()
            self.clear_queues()
            if self.pause:
                self.pause = False
                self.pause_after_seek = True
            self.seek = max(self.video_time - 5, 0)
        elif code == 105 and self.media_type in ("audio", "video"):   # volume up
            self.volume += 10
            self.volume = min(self.volume, 200)
            self.gain = volume_to_gain(self.volume)
            self.show_ui()
        elif code == 106 and self.media_type in ("audio", "video"):   # volume Down
            self.volume -= 10
            self.volume = max(self.volume, 0)
            self.gain = volume_to_gain(self.volume)
            self.show_ui()


    def start_ui_thread(self, init_hidden=True):
        """Start UI drawing thread"""
        self.ui_thread = threading.Thread(target=self.draw_ui_loop, daemon=True, args=(init_hidden, ))
        self.ui_thread.start()


    def show_ui(self):
        """Show UI after its been hidden"""
        self.ui_timer = 0
        self.ui_line = self.build_ui_string()
        if self.img:
            self.pil_img_to_term(self.img, remove_alpha=False)


    def hide_ui(self):
        """Hide UI"""
        self.ui_timer = 30


    def draw_ui_loop(self, init_hidden):
        """Continuously draw UI line at bottom of the screen"""
        if self.media_type == "video":
            self.video_duration = 1
            self.video_time = 0
            self.ui_timer = 30 if init_hidden else 0
            while self.run:
                if self.ui_timer <= 25:
                    self.ui_line = self.build_ui_string()
                    if self.ui_timer == 25:
                        self.ui_line = ""
                    if not (self.pause or self.ended):
                        self.ui_timer += 1
                time.sleep(0.2)
        elif self.media_type == "audio":
            self.video_duration = 1
            self.video_time = 0
            self.ui_timer = 0
            while self.run:
                self.ui_line = self.build_ui_string()
                self.draw_blank()
                time.sleep(0.2)


    def build_ui_string(self):
        """Draw UI line at bottom of the screen"""
        total_time = f"{int(self.video_duration) // 60:02d}:{int(self.video_duration) % 60:02d}"
        current_time = f"{int(self.video_time) // 60:02d}:{int(self.video_time) % 60:02d}"
        volume = f"VOL:{int(self.volume):03d}"
        bar_len = terminal_utils.get_size()[1] - 28   # minus len of all other elements and spaces
        filled = int(bar_len * min(self.video_time / self.video_duration, 1))
        bar = self.bar_ch * filled + " " * (bar_len - filled)
        if self.pause:
            pause = "|"
        else:
            pause = ">"
        return f"   {pause} {current_time} {bar} {total_time} {volume}   "


    def wait_input(self):
        """Handle input from user"""
        while self.run:
            key = terminal_utils.read_key()
            if key == "ESC":
                self.control_codes(100)
                self.run = False
            elif key in self.keybindings["media_pause"]:
                self.control_codes(101)
            elif key in self.keybindings["media_replay"]:
                self.control_codes(102)
            elif key in self.keybindings["media_seek_forward"]:
                self.control_codes(103)
            elif key in self.keybindings["media_seek_backward"]:
                self.control_codes(104)
            elif key in self.keybindings["media_volume_up"]:
                self.control_codes(105)
            elif key in self.keybindings["media_volume_down"]:
                self.control_codes(106)


def runner(path, config, keybindings):
    """Main function"""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print("Cant play media: File not found.", file=sys.stderr)
        sys.exit(1)
    if os.path.isdir(path):
        print("Cant play media: Specified path is a directory.", file=sys.stderr)
        sys.exit(1)
    terminal_media = TerminalMedia(config, keybindings, external=True)
    terminal_media.play(path)
