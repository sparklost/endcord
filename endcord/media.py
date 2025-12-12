import curses
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from queue import Queue

import av
import filetype
from PIL import Image, ImageEnhance

# safely import soundcard, in case there is no sound system
try:
    import soundcard
    have_soundcard = True
except (AssertionError, RuntimeError):
    have_soundcard = False

from endcord import xterm256

logger = logging.getLogger(__name__)
match_youtube = re.compile(r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}")


def get_mime(path):
    """Try to get mime type of the file"""
    kind = filetype.guess(path)
    if kind:
        return kind.mime
    return "unknown/unknown"


def img_to_curses(screen, img, img_gray, start_color_id, ascii_palette, ascii_palette_len, screen_width, screen_height, width, height):
    """Draw image using curses with padding"""
    pixels = img.load()
    pixels_gray = img_gray.load()

    padding_h = (screen_height - height) // 2
    padding_w = (screen_width - width) // 2
    bg_color = curses.color_pair(start_color_id + 1)

    # top padding
    for y_fill in range(padding_h):
        screen.insstr(y_fill, 0, " " * screen_width, bg_color)

    # image rows
    for y in range(height):
        row_y = y + padding_h

        # left padding
        if padding_w > 0:
            screen.insstr(row_y, 0, " " * padding_w, bg_color)

        for x in range(width):
            gray_val = pixels_gray[x, y]
            character = ascii_palette[(gray_val * ascii_palette_len) // 255]
            color = start_color_id + pixels[x, y] + 16
            screen.insch(row_y, x + padding_w, character, curses.color_pair(color))

        # right padding
        if x + padding_w + 1 < screen_width:
            screen.insstr(row_y, x + padding_w + 1, " " * (screen_width - (x + padding_w + 1)), bg_color)

    # bottom padding
    if screen_height != height:
        for y_fill in range(padding_h + 1):
            try:
                screen.insstr(screen_height - 1 - y_fill, 0, " " * screen_width, bg_color)
            except curses.error:
                pass

    screen.noutrefresh()

# use cython if available, ~1.15 times faster
if importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.media"):
    from endcord_cython.media import img_to_curses

# get speaker
if have_soundcard:
    try:
        speaker = soundcard.default_speaker()
        have_sound = True
    except Exception:
        have_sound = False
else:
    have_sound = False

class CursesMedia():
    """Methods for showing and playing media in terminal with curses"""

    def __init__(self, screen, config, start_color_id, ui=True):
        logging.getLogger("libav").setLevel(logging.ERROR)
        self.screen = screen
        self.font_scale = config["media_font_scale"]   # 2.25
        self.ascii_palette = list(config["media_ascii_palette"])   # "  ..',;:c*loexk#O0XNW"
        self.saturation = config["media_saturation"]   # 1.2
        self.cap_fps = config["media_cap_fps"]   # 30
        self.color_media_bg = config["media_color_bg"]   # -1
        self.mute_video = config["media_mute"]   # false
        self.bar_ch = config["media_bar_ch"]
        self.default_color = config["color_default"][0]   # all 255 colors already init in order
        self.yt_dlp_path = config["yt_dlp_path"]
        self.yt_dlp_format = config["yt_dlp_format"]
        if self.default_color == -1:
            self.default_color = 0
        self.start_color_id = start_color_id
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

        self.lock = threading.RLock()
        self.need_update = threading.Event()

        self.ui = ui
        if ui:
            self.show_ui()
        # self.init_colors()   # 255_curses_bug - enable this
        self.start_color_id = 0   # 255_curses_bug


    def init_colors(self):
        """Initialize 255 colors for drawing picture, from starting color ID"""
        for i in range(1, 255):
            curses.init_pair(self.start_color_id + i, i, self.color_media_bg)


    def screen_update(self):
        """Thread that updates drawn content on physical screen"""
        while self.run:
            self.need_update.wait()
            # here must be delay, otherwise output gets messed up
            with self.lock:
                time.sleep(0.005)   # lower delay so video is not late
                curses.doupdate()
                self.need_update.clear()


    def pil_img_to_curses(self, img, remove_alpha=True):
        """Convert pillow image to ascii art and display it with curses"""
        screen_height, screen_width = self.media_screen.getmaxyx()
        height, width = self.media_screen.getmaxyx()

        # scale image
        wpercent = (width / (float(img.size[0] * self.font_scale)))
        hsize = int((float(img.size[1]) * float(wpercent)))
        if hsize > height:
            hpercent = (height / float(img.size[1]))
            wsize = int((float(img.size[0] * self.font_scale) * float(hpercent)))
            width = wsize
        else:
            height = hsize
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        img_gray = img.convert("L")

        # increase saturation
        if self.saturation:
            sat = ImageEnhance.Color(img)
            img = sat.enhance(self.saturation)

        if remove_alpha and img.mode != "RGB" and img.mode != "L":
            background = Image.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background

        # apply xterm256 palette
        img_palette = Image.new("P", (16, 16))
        img_palette.putpalette(self.xterm_256_palette)
        img = img.quantize(palette=img_palette, dither=0)

        # draw with curses
        img_to_curses(
            self.media_screen,
            img,
            img_gray,
            self.start_color_id,
            self.ascii_palette,
            self.ascii_palette_len,
            screen_width,
            screen_height,
            width,
            height,
        )
        self.need_update.set()


    def play_img(self, img_path):
        """
        Convert image to colored ascii art and draw it with curses.
        If image is animated (eg apng) send it to play_anim instead.
        """
        img = Image.open(img_path)
        if hasattr(img, "is_animated") and img.is_animated:
            self.media_type = "gif"
            self.play_anim(img_path)
            return
        self.init_colors()   # 255_curses_bug
        self.hide_ui()
        self.pil_img_to_curses(img)
        while self.playing:
            self.media_screen.noutrefresh()
            self.need_update.set()
            screen_size = self.media_screen.getmaxyx()
            if self.media_screen_size != screen_size:
                self.pil_img_to_curses(img)
                self.media_screen_size = screen_size
            time.sleep(0.1)


    def play_anim(self, gif_path):
        """Convert animated image to colored ascii art and draw it with curses"""
        self.init_colors()   # 255_curses_bug
        self.hide_ui()
        gif = Image.open(gif_path)
        frame = 0
        loop = bool(gif.info.get("loop", 1))
        while self.playing:
            try:
                start_time = time.time()
                frame_duration = gif.info["duration"] / 1000
                gif.seek(frame)
                img = Image.new("RGB", gif.size)
                img.paste(gif)
                self.pil_img_to_curses(img, remove_alpha=False)
                frame += 1
                time.sleep(max(frame_duration - (time.time() - start_time), 0))
            except EOFError:
                if loop:
                    break
                frame = 0


    def play_audio(self, path, loop=False, loop_delay=0.7, loop_max=60):
        """Play only audio"""
        if self.ui:
            self.init_colors()   # 255_curses_bug
            self.show_ui()

        self.seek = None
        if not have_sound:
            self.ended = True
            return

        container = av.open(path)
        self.ended = False
        self.video_time = 0   # using video_time to simplify controls

        # fill screen
        if self.ui:
            self.media_screen.clear()
            h, w = self.media_screen.getmaxyx()
            for y in range(h):
                self.media_screen.insstr(y, 0, " " * w, curses.color_pair(self.start_color_id+1))
            self.media_screen.noutrefresh()
            self.need_update.set()

        all_audio_streams = container.streams.audio
        if not all_audio_streams:   # no audio?
            return
        audio_stream = all_audio_streams[0]

        if audio_stream.duration:
            self.video_duration = float(audio_stream.duration * audio_stream.time_base)
        else:
            self.video_duration = audio_stream.frames / audio_stream.average_rate * audio_stream.time_base
        if self.video_duration == 0:
            self.video_duration = 1   # just in case

        frame_duration = 1 / container.streams.audio[0].codec_context.sample_rate

        with speaker.player(samplerate=audio_stream.rate, channels=audio_stream.channels, blocksize=1152) as stream:
            start = int(time.time())
            while self.playing:
                for frame in container.decode(audio=0):
                    if self.seek is not None:
                        container.seek(int(self.seek / audio_stream.time_base), stream=audio_stream)
                        self.video_time = self.seek
                        self.seek = None
                        if self.pause_after_seek:
                            self.pause_after_seek = False
                            self.pause = True
                        continue
                    if not self.playing:
                        break
                    stream.play(frame.to_ndarray().astype("float32").T)
                    self.video_time += frame.samples * frame_duration
                    if self.pause:
                        if self.ui:
                            self.draw_ui()
                        while self.pause:
                            time.sleep(0.1)
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
        self.pause = False
        self.run = False
        self.playing = False


    def audio_player(self, audio_queue, samplerate, channels, audio_ready):
        """Play audio frames from the queue"""
        with speaker.player(samplerate=samplerate, channels=channels, blocksize=1152) as stream:
            audio_ready.set()
            while True:
                frame = audio_queue.get()
                if frame is None:
                    break
                stream.play(frame.to_ndarray().astype("float32").T)
                while self.pause:
                    time.sleep(0.1)


    def video_player(self, video_queue, audio_queue, frame_duration):
        """Play video frames from the queue"""
        while True:
            frame = video_queue.get()
            if frame is None:
                break
            if audio_queue.qsize() >= 1:
                start_time = time.time()
                img = frame.to_image()
                with self.lock:
                    self.pil_img_to_curses(img, remove_alpha=False)
            if audio_queue.qsize() >= 3:
                time.sleep(max(frame_duration - (time.time() - start_time), 0))
            while self.pause:
                time.sleep(0.1)


    def play_video(self, path):
        """Decode video and audio frames and manage queues"""
        self.init_colors()   # 255_curses_bug
        self.show_ui()
        self.seek = None

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

        # prepare audio
        audio_queue = Queue(maxsize=10)
        have_audio = False
        if not self.mute_video and have_sound:
            all_audio_streams = container.streams.audio
            if all_audio_streams:   # in case of a muted video
                audio_ready = threading.Event()
                have_audio = True
                audio_stream = all_audio_streams[0]
                audio_thread = threading.Thread(target=self.audio_player, args=(audio_queue, audio_stream.rate, audio_stream.channels, audio_ready), daemon=True)
                audio_thread.start()
                audio_ready.wait()   # wait for audio to decrease desyncing
                audio_ready.clear()

        # prepare video
        video_queue = Queue(maxsize=10)
        video_thread = threading.Thread(target=self.video_player, args=(video_queue, audio_queue, frame_duration), daemon=True)
        video_thread.start()

        num = 0
        for frame in container.decode():
            if self.seek is not None:
                container.seek(int(self.seek / video_stream.time_base), stream=video_stream)
                self.video_time = self.seek
                self.seek = None
                if self.pause_after_seek:
                    self.pause_after_seek = False
                    self.pause = True
                continue
            if not self.playing:
                container.close()
                break
            if isinstance(frame, av.audio.frame.AudioFrame) and have_audio:
                audio_queue.put(frame)
            if isinstance(frame, av.video.frame.VideoFrame):
                if num == frame_index:   # limit fps
                    video_queue.put(frame)
                    num = 0
                num += 1
                self.video_time += frame_duration
            if self.pause:
                self.draw_ui()
                while self.pause:
                    time.sleep(0.1)

        audio_queue.put(None)
        video_queue.put(None)
        if have_audio:
            audio_thread.join()
        video_thread.join()
        self.ended = True


    def play(self, path):
        """Select runner based on file type"""
        if not path:
            return
        if not os.path.exists(path):
            return
        if os.path.isdir(path):
            return
        self.path = path
        self.ui = True
        self.run = True
        self.screen_update_thread = threading.Thread(target=self.screen_update, daemon=True)
        self.screen_update_thread.start()
        self.playing = True
        try:
            yt_match = re.search(match_youtube, path)
            if yt_match:
                self.play_youtube(yt_match.group())
            elif "https://" in path:
                self.media_type = "video"
                self.start_ui_thread()
                self.play_video(path)
            else:
                mime = get_mime(path).split("/")
                if mime[0] == "image":
                    if mime[1] == "gif":
                        self.media_type = "gif"
                        self.play_anim(path)
                    else:
                        self.media_type = "img"
                        self.play_img(path)
                elif mime[0] == "video":
                    self.media_type = "video"
                    self.start_ui_thread()
                    self.play_video(path)
                elif mime[0] == "audio":
                    self.media_type = "audio"
                    self.start_ui_thread()
                    self.play_audio(path)
                else:
                    logger.warning(f"Unsupported media format: {mime}")
                    self.run = False
            while self.run:   # dont exit when video ends
                time.sleep(0.2)
        except Exception as e:
            logger.error("".join(traceback.format_exception(e)))
        self.run = False
        self.playing = False
        self.need_update.set()
        self.screen_update_thread.join()


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


    def control_codes(self, code):
        """Handle controls from TUI"""
        if code == 100:   # quit media player
            self.pause = False
            self.run = False
            self.playing = False
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
            if self.pause:
                self.pause = False
                self.pause_after_seek = True
            self.seek = min(self.video_time + 5, self.video_duration)
        elif code == 104 and self.media_type in ("audio", "video") and not self.ended:   # seek backward
            self.show_ui()
            if self.pause:
                self.pause = False
                self.pause_after_seek = True
            self.seek = max(self.video_time - 5, 0)


    def start_ui_thread(self):
        """Start UI drawing thread"""
        self.ui_thread = threading.Thread(target=self.draw_ui_loop, daemon=True)
        self.ui_thread.start()


    def show_ui(self):
        """Show UI after its been hidden"""
        with self.lock:
            self.ui_timer = 0
            h, w = self.screen.getmaxyx()
            media_screen_hwyx = (h - 1, w, 0, 0)
            self.media_screen = self.screen.derwin(*media_screen_hwyx)
            ui_line_hwyx = (1, w, h - 1, 0)
            self.ui_line = self.screen.derwin(*ui_line_hwyx)
            self.media_screen_size = self.media_screen.getmaxyx()


    def hide_ui(self):
        """Hide UI"""
        with self.lock:
            h, w = self.screen.getmaxyx()
            self.media_screen = self.screen
            self.ui_line = None


    def draw_ui_loop(self):
        """Continuously draw UI line at bottom of the screen"""
        if self.media_type == "video":
            self.video_duration = 1
            self.video_time = 0
            self.ui_timer = 0
            while self.run:
                if self.ui_timer <= 25:
                    self.draw_ui()
                    if self.ui_timer == 25:
                        self.hide_ui()
                    if not (self.pause or self.ended):
                        self.ui_timer += 1
                time.sleep(0.2)
        elif self.media_type == "audio":
            self.video_duration = 1
            self.video_time = 0
            self.ui_timer = 0
            while self.run:
                self.draw_ui()
                time.sleep(0.2)


    def draw_ui(self):
        """Draw UI line at bottom of the screen"""
        if self.ui_line:
            total_time = f"{int(self.video_duration) // 60:02d}:{int(self.video_duration) % 60:02d}"
            current_time = f"{int(self.video_time) // 60:02d}:{int(self.video_time) % 60:02d}"
            bar_len = self.screen.getmaxyx()[1] - 20   # minus len of all other elements and spaces
            filled = int(bar_len * min(self.video_time / self.video_duration, 1))
            bar = self.bar_ch * filled + " " * (bar_len - filled)
            if self.pause:
                pause = "|"
            else:
                pause = ">"
            with self.lock:
                ui_line = f"   {pause} {current_time} {bar} {total_time}  "
                self.ui_line.addstr(0, 0, ui_line, curses.color_pair(self.default_color))
                self.ui_line.noutrefresh()
                self.need_update.set()


def wait_input(screen, keybindings, curses_media):
    """Handle input from user"""
    keybindings = {key: (val,) if not isinstance(val, tuple) else val for key, val in keybindings.items()}
    run = True
    while run:
        key = screen.getch()
        if key == 27:   # ESCAPE
            screen.nodelay(True)
            key = screen.getch()
            if key in (-1, 27):
                screen.nodelay(False)
                curses_media.control_codes(100)
            screen.nodelay(False)
            run = False
        elif key in keybindings["media_pause"]:
            curses_media.control_codes(101)
        elif key in keybindings["media_replay"]:
            curses_media.control_codes(102)
        elif key in keybindings["media_seek_forward"]:
            curses_media.control_codes(103)
        elif key in keybindings["media_seek_backward"]:
            curses_media.control_codes(104)
        elif key == curses.KEY_RESIZE:
            pass


def ascii_runner(screen, path, config, keybindings):
    """Main function"""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        sys.exit("Cant play media: File not found.")
    if os.path.isdir(path):
        sys.exit("Cant play media: Specified path is a directory.")
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses_media = CursesMedia(screen, config, 0)
    input_thread = threading.Thread(target=wait_input, args=(screen, keybindings, curses_media), daemon=True)
    input_thread.start()
    curses_media.play(path)
