# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import curses
import math
import sys
import threading

import numpy as np
import soundcard

BAR_WIDTH = 50
MIN_DB = -60
MAX_DB = 0

CONTROLS_TEXT = """Controls:
 [Space] Cycle devices | [Arrows/Scroll] Move threshold
 [D] Toggle denoise | [Q/Esc] Quit | [Enter] Save and quit"""
HOWTO_TEXT = """How to configure:
 2. While its silence, move knob until it's just above (right) of current input level
 3. Start speaking, move knob more right, few dB bellow current input level
 4. Make threshold knob be somewhere between these 2 levels
 5. Ensure there are no SILENCE signs longer than 0.5s when you are speaking
"""


def detect_silence(data, threshold=0.03):
    """RMS-based silence detection"""
    data = data - np.mean(data, axis=0, keepdims=True)   # remove dc
    rms = np.sqrt(np.mean(data**2))
    return rms, rms < threshold


def patch_texts(n):
    """Prepend spaces to lines of constant texts texts"""
    global HOWTO_TEXT, CONTROLS_TEXT
    HOWTO_TEXT = "\n".join((" " * n) + line for line in HOWTO_TEXT.split("\n"))
    CONTROLS_TEXT = "\n".join((" " * n) + line for line in CONTROLS_TEXT.split("\n"))


def draw_border(screen, corners, color):
    """Draw border at screen borders with custom corners"""
    h, w = screen.getmaxyx()
    try:
        screen.hline(0, 1, curses.ACS_HLINE, w - 2, curses.color_pair(color))
        screen.hline(h - 1, 1, curses.ACS_HLINE, w - 2, curses.color_pair(color))
        screen.vline(1, 0, curses.ACS_VLINE, h - 2, curses.color_pair(color))
        screen.vline(1, w - 1, curses.ACS_VLINE, h - 2, curses.color_pair(color))
        screen.addstr(0, 0, corners[0], curses.color_pair(color))
        screen.addstr(0, w - 1, corners[2], curses.color_pair(color))
        screen.addstr(h - 1, 0, corners[1], curses.color_pair(color))
        screen.addstr(h - 1, w - 1, corners[3], curses.color_pair(color))
    except curses.error:   # errors randomly when resizing
        pass


class VUMeter:
    """VU Meter class"""

    def __init__(self, screen, config):
        curses.use_default_colors()
        curses.curs_set(0)
        self.screen = screen
        self.screen.timeout(50)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)

        curses.init_pair(1, -1, 40)   # bar
        curses.init_pair(2, -1, 235)   # bar bg
        curses.init_pair(3, 232, 40)   # knob bar
        curses.init_pair(4, 255, 235)   # knob bg
        curses.init_pair(5, 232, 40)   # sign on
        curses.init_pair(6, -1, 235)   # sign off
        self.color_bar = curses.color_pair(2)
        self.color_knob_bar = curses.color_pair(3) | curses.A_BOLD
        self.color_knob_bg = curses.color_pair(4) | curses.A_BOLD

        self.config = config
        self.bordered = not (config["compact"])
        self.border_corners = config["border_corners"]
        if config["color_default"] != [-1, -1]:
            self.ui_color = 7
            curses.init_pair(7, config["color_default"][0], config["color_default"][1])
        else:
            self.ui_color = 0
        self.threshold_db = config["call_silence_threshold"]
        self.threshold_rms = self.silence_threshold = 10 ** (self.threshold_db / 20)
        self.do_denoise = config["call_mic_noise_supression"]
        self.screen = screen
        self.run = True
        self.rms = 0.0
        self.is_silence = True
        self.mic_index = 0
        self.mic_changed = False
        self.mics = soundcard.all_microphones()
        if self.bordered:
            patch_texts(1)

        from endcord import rnnoise
        try:
            self.denoiser = rnnoise.RNNoise()
            silence_frame = np.zeros(480, dtype=np.int16)
            self.denoiser.process_frame(silence_frame)
        except OSError:
            self.denoiser = None

        self.main()


    def audio_recorder(self):
        """Record and process audio"""
        while self.run:
            microphone = self.mics[self.mic_index]
            with microphone.recorder(samplerate=48000, channels=1, blocksize=960) as rec:
                while self.run and not self.mic_changed:
                    audio_data = rec.record(numframes=960)
                    if self.denoiser and self.do_denoise:
                        audio_data = self.denoise(audio_data)
                    self.rms, self.is_silence = detect_silence(audio_data, self.threshold_rms)
            self.mic_changed = False


    def handle_input(self):
        """Process input events"""
        key = self.screen.getch()

        if key in (ord("q"), 27, "q", "ESC", "C-c"):
            self.run = False

        if key in (10, "ENTER"):
            from endcord import config
            config.update_config(self.config, "call_silence_threshold", self.threshold_db)
            config.update_config(self.config, "call_mic_noise_supression", self.do_denoise)
            self.run = False

        elif key in (ord(" "), "SPACE") and len(self.mics) > 1:
            self.mic_index = (self.mic_index + 1) % len(self.mics)
            self.mic_changed = True

        elif key in (ord("d"), "d"):
            self.do_denoise = not self.do_denoise

        elif key in (curses.KEY_UP, curses.KEY_RIGHT, "UP", "RIGHT"):
            self.threshold_db = min(MAX_DB, self.threshold_db + 1.0)

        elif key in (curses.KEY_DOWN, curses.KEY_LEFT, "DOWN", "LEFT"):
            self.threshold_db = max(MIN_DB, self.threshold_db - 1.0)

        elif key == curses.KEY_MOUSE or isinstance(key, tuple):
            if isinstance(key, tuple):
                key_type = key[2]
                if key_type == 64:
                    self.threshold_db = min(MAX_DB, self.threshold_db + 1.0)
                elif key_type == 65:
                    self.threshold_db = max(MIN_DB, self.threshold_db - 1.0)
            else:
                try:
                    key_type = curses.getmouse()[4]
                except curses.error:
                    return False
                if key_type & curses.BUTTON4_PRESSED:
                    self.threshold_db = min(MAX_DB, self.threshold_db + 1.0)
                elif key_type & curses.BUTTON5_PRESSED:
                    self.threshold_db = max(MIN_DB, self.threshold_db - 1.0)

        elif key in (curses.KEY_RESIZE, "RESIE"):
            pass
        else:
            return False
        self.threshold_rms = 10 ** (self.threshold_db / 20)
        return True


    def draw_full_ui(self, current_db, bar_len, knob_pos):
        """Render full interface to the terminal"""
        try:
            x = 1 + self.bordered
            dev_name = self.mics[self.mic_index].name
            device_text = f"Device [{self.mic_index + 1}/{len(self.mics)}]: "
            self.screen.addstr(1, x, device_text, curses.color_pair(self.ui_color) | curses.A_BOLD)
            left_w = max(0, self.screen.getmaxyx()[1] - len(device_text))
            self.screen.addstr(1, x + len(device_text), f"{dev_name[:left_w]}{" " * (left_w - len(dev_name[:left_w]))}", curses.color_pair(self.ui_color))
            self.screen.addstr(3, x, f"Input Level: {current_db:5.1f} dB  |  Threshold: {self.threshold_db:5.1f} dB", curses.color_pair(self.ui_color))
            empty_len = max(0, BAR_WIDTH - bar_len)
            self.screen.addstr(4, x, " " * bar_len, curses.color_pair(1))
            self.screen.addstr(4, x + bar_len, " " * empty_len, curses.color_pair(2))
            if (knob_pos - 1) < bar_len:
                self.screen.addstr(4, x + knob_pos, "♦", curses.color_pair(3) | curses.A_BOLD)
            else:
                self.screen.addstr(4, x + knob_pos, "♦", curses.color_pair(4) | curses.A_BOLD)
            if self.is_silence:
                self.screen.addstr(6, x, "  SILENCE  ", curses.color_pair(5) | curses.A_BOLD)
            else:
                self.screen.addstr(6, x, "  ACTIVE   ", curses.color_pair(6))
            if self.do_denoise and self.denoiser:
                self.screen.addstr(6, x + 13, "DENOISE ON ", curses.color_pair(5) | curses.A_BOLD)
            else:
                self.screen.addstr(6, x + 13, "DENOISE OFF", curses.color_pair(6))
            self.screen.addstr(8, 1, CONTROLS_TEXT + "\n", curses.color_pair(self.ui_color))
            self.screen.addstr(11, 1, HOWTO_TEXT, curses.color_pair(self.ui_color))
            if self.bordered:
                draw_border(self.screen, self.border_corners, self.ui_color)
        except curses.error:
            pass
        self.screen.refresh()


    def draw_bar_ui(self, bar_len, knob_pos):
        """Render only bar interface to the terminal"""
        try:
            x = 1 + self.bordered
            empty_len = max(0, BAR_WIDTH - bar_len)
            self.screen.addstr(4, x, " " * bar_len, curses.color_pair(1))
            self.screen.addstr(4, x + bar_len, " " * empty_len, curses.color_pair(2))
            if (knob_pos - 1) < bar_len:
                self.screen.addstr(4, x + knob_pos, "♦", curses.color_pair(3) | curses.A_BOLD)
            else:
                self.screen.addstr(4, x + knob_pos, "♦", curses.color_pair(4) | curses.A_BOLD)
        except curses.error:
            pass
        self.screen.refresh()


    def denoise(self, audio_data):
        """Process 20ms of audio, downmix to mono if needed, run RNNoise across two 10ms subframes, and return clean stereo"""
        if audio_data.dtype == np.int16:
            audio_float = audio_data.astype("float32") / 32768.0
        else:
            audio_float = audio_data.astype("float32")

        if audio_float.ndim == 1:
            mono = audio_float
        elif audio_float.shape[0] == 2 and audio_float.shape[1] == 960:
            mono = (audio_float[0, :] + audio_float[1, :]) * 0.5
        elif audio_float.shape[1] == 1:
            mono = audio_float[:, 0]
        else:
            mono = (audio_float[:, 0] + audio_float[:, 1]) * 0.5

        out = np.empty((960, 2), dtype=np.float32)
        for frame_index in range(2):
            start = frame_index * 480
            end = start + 480
            _, cleaned_float = self.denoiser.process_frame(mono[start:end])
            out[start:end, 0] = cleaned_float
            out[start:end, 1] = cleaned_float

        return out


    def main(self):
        """Main app method"""
        if not self.mics:
            sys.exit("No audio input devices found")

        audio_thread = threading.Thread(target=self.audio_recorder, daemon=True)
        audio_thread.start()

        first = True
        while self.run:
            full_redraw = self.handle_input()
            current_db = 20 * math.log10(self.rms) if self.rms > 0 else MIN_DB
            current_db = max(MIN_DB, min(MAX_DB, current_db))
            fill_ratio = (current_db - MIN_DB) / (MAX_DB - MIN_DB)
            bar_len = int(fill_ratio * BAR_WIDTH)
            knob_ratio = (self.threshold_db - MIN_DB) / (MAX_DB - MIN_DB)
            knob_pos = max(0, min(BAR_WIDTH, int(knob_ratio * BAR_WIDTH)))
            if full_redraw or first:
                self.draw_full_ui(current_db, bar_len, knob_pos)
                first = False
            else:
                self.draw_bar_ui(bar_len, knob_pos)
        audio_thread.join(timeout=0.2)


def vumeter_runner(config):
    """Run vumeter app"""
    try:
        app = VUMeter
        curses.wrapper(app, config)
    except curses.error as e:
        if str(e) != "endwin() returned ERR":
            sys.exit("Curses error")
