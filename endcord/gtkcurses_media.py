# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import logging
import os
import queue
import threading

import gi

from endcord import formatter, peripherals, utils

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib   # noqa

logger = logging.getLogger(__name__)

IMAGE_SIZES = [16, 20, 22, 24, 28, 32, 40, 44, 48, 56, 60, 64, 80, 96, 100, 128, 160, 240, 254]


class GtkCursesMedia:
    """Draws media over gtkcurses text buffer"""

    def __init__(self, app, gtk_window, pfp_round=True, pfp_aa=True):
        self.app = app
        self.gtk_window = gtk_window
        self.run = True
        self.chat_map = []
        self.messages = []
        self.assist_data = []
        self.update_event = threading.Event()
        self.prev_chat_index = None
        self.prev_chat_hw = None
        self.prev_assist_type = None
        self.prev_extra_index = None

        # emoji
        self.download_queue = queue.Queue()
        self.images = {}   # emoji_id: surface
        self.images_lock = threading.Lock()
        self.emoji_pos_cache = []   # (rel_y, rel_x, emoji_id)
        self.emoji_assist_cache = []   # (line number, emoji_id, extra_w)
        self.chat_draw_list = []   # (abs_y, abs_x, emoji_id)
        self.extra_draw_list = []
        self.emoji_cache_path = os.path.join(peripherals.cache_path, "emoji")
        self.post_one_reaction_len = len(self.app.config["format_one_reaction"].split("%reaction")[-1])
        self.size = round(self.gtk_window.char_height)
        self.x_offset = max(0, (2 * self.gtk_window.char_width - self.size) / 2)
        self.app.placeholder_emoji = True
        self.app.formatter.placeholder_emoji = "  "

        # pfp
        self.round = pfp_round
        self.antialias = pfp_aa
        self.pfp_queue = queue.Queue()
        self.pfp_images = {}   # avatar_id: surface
        self.pfp_lock = threading.Lock()
        self.pfp_pos_cache = []   # (rel_y, key)
        self.pfp_draw_list = []   # (abs_y, abs_x, key)
        self.pfp_clip = (0, 0)   # visible rows, top and bottom
        self.pfp_cache_path = os.path.expanduser(os.path.join(peripherals.cache_path, "pfp-small"))
        os.makedirs(self.pfp_cache_path, exist_ok=True)

        # shift formats for pfp
        message_shift = app.config.get("ext_image_pfp_format_message_shift", (0, 5))
        format_message = app.config["format_message"]
        app.formatter.format_message = format_message[:message_shift[0]] + (" " * message_shift[1]) + format_message[message_shift[0]:]
        app.formatter.color_message = formatter.shift_formats(app.formatter.color_message, message_shift[0], message_shift[1])
        app.formatter.color_mention_message = formatter.shift_formats(app.formatter.color_mention_message, message_shift[0], message_shift[1])
        if "\n" in app.formatter.format_message:
            grouped_shift = app.config.get("ext_image_pfp_format_message_grouped_shift", (2, 1))
            format_message_grouped = app.config["format_message_grouped"]
            app.formatter.format_message_grouped = format_message_grouped[:grouped_shift[0]] + (" " * grouped_shift[1]) + format_message_grouped[grouped_shift[0]:]
            app.formatter.color_message_grouped = formatter.shift_formats(app.formatter.color_message_grouped, grouped_shift[0], grouped_shift[1])
            app.formatter.color_mention_message_grouped = formatter.shift_formats(app.formatter.color_mention_message_grouped, grouped_shift[0], grouped_shift[1])
            newline_shift = app.config.get("ext_image_pfp_format_newline_shift", (2, 1))
            format_newline = app.config["format_newline"]
            app.formatter.format_newline = format_newline[:newline_shift[0]] + (" " * newline_shift[1]) + format_newline[newline_shift[0]:]
            app.formatter.color_newline = formatter.shift_formats(app.formatter.color_newline, newline_shift[0], newline_shift[1])
            app.formatter.color_mention_newline = formatter.shift_formats(app.formatter.color_mention_newline, newline_shift[0], newline_shift[1])
            self.pfp_size = 2
        else:
            self.pfp_size = 1
        app.formatter.calculate_lengths()
        app.keep_avatars = True

        self.pfp_side = round(min(self.pfp_size * self.gtk_window.char_height, self.pfp_size * 2 * self.gtk_window.char_width))
        self.pfp_x_offset = (self.pfp_size * 2 * self.gtk_window.char_width - self.pfp_side) / 2
        self.pfp_y_offset = (self.pfp_size * self.gtk_window.char_height - self.pfp_side) / 2

        self.gtk_window.media = self
        threading.Thread(target=self.downloader, daemon=True).start()
        threading.Thread(target=self.pfp_downloader, daemon=True).start()
        threading.Thread(target=self.worker, daemon=True).start()


    def redraw(self):
        """Tell GTK to redraw the window"""
        GLib.idle_add(self.gtk_window.drawing_area.queue_draw)


    def draw(self, cr):
        """Draw images over text buffer, called from GTK on_draw"""
        if self.app.tui.disable_drawing:
            return
        char_width = self.gtk_window.char_width
        char_height = self.gtk_window.char_height

        # pfp
        cr.save()
        top, bottom = self.pfp_clip
        cr.rectangle(0, top * char_height, self.gtk_window.curses_window.ncols * char_width, (bottom - top) * char_height)
        cr.clip()
        for abs_y, abs_x, key in self.pfp_draw_list:
            surface = self.pfp_images.get(key)
            if not surface:
                continue
            cr.set_source_surface(surface, abs_x * char_width + self.pfp_x_offset, abs_y * char_height + self.pfp_y_offset)
            cr.paint()
        cr.restore()

        # emoji
        for abs_y, abs_x, emoji_id in self.chat_draw_list + self.extra_draw_list:
            surface = self.images.get(emoji_id)
            if not surface:
                continue
            cr.set_source_surface(surface, abs_x * char_width + self.x_offset, abs_y * char_height)
            cr.paint()


    def stop(self):
        """Stop all threads"""
        self.run = False
        self.gtk_window.media = None
        self.update_event.set()
        self.download_queue.put(None)
        self.pfp_queue.put(None)


    def clear_queue(self):
        """Clear download queues and drawn images (when switching channel)"""
        for download_queue in (self.download_queue, self.pfp_queue):
            while self.run:
                try:
                    download_queue.get_nowait()
                except queue.Empty:
                    break
        with self.images_lock:
            self.images = {emoji_id: surface for emoji_id, surface in self.images.items() if surface}
        with self.pfp_lock:
            self.pfp_images = {key: surface for key, surface in self.pfp_images.items() if surface}
        self.emoji_pos_cache = []
        self.chat_draw_list = []
        self.pfp_pos_cache = []
        self.pfp_draw_list = []
        self.redraw()


    def update(self, chat_format, chat_map, messages):   # noqa
        """Get new chat data, queue downloads, update caches and redraw"""
        self.chat_map = chat_map
        self.messages = messages
        self.update_event.set()


    def update_assist(self, assist_data, assist_type):
        """Get new assist data"""
        if assist_type == 3:
            self.assist_data = assist_data
            self.update_event.set()
        else:
            self.assist_data = []
            if self.prev_assist_type != assist_type:
                self.prev_assist_type = assist_type
                self.update_event.set()


    def draw_images(self):
        """Recalculate image positions if chat was scrolled or resized"""
        tui = self.app.tui
        if self.prev_chat_index == tui.chat_index and self.prev_chat_hw == tui.chat_hw:
            return
        self.calculate_chat()


    def draw_extra_window_images(self):
        """Recalculate emoji positions in extra window"""
        tui = self.app.tui
        if not tui.win_extra_window:
            return
        if tui.extra_index != self.prev_extra_index:
            self.prev_extra_index = tui.extra_index
            self.update_event.set()
        self.calculate_extra()


    def remove_extra_window_images(self):
        """Remove emoji from extra window when its removed"""
        self.assist_data = []
        self.prev_assist_type = None
        self.prev_extra_index = None
        self.emoji_assist_cache = []
        self.extra_draw_list = []
        self.update_event.set()
        self.redraw()


    def calculate_chat(self):
        """Calculate absolute image positions from cached chat positions"""
        tui = self.app.tui
        with tui.lock:
            chat_y, chat_x = tui.win_chat.getbegyx()
            chat_h = tui.chat_hw[0]
            subtitle_line = bool(tui.win_subtitle_line)

            # emoji
            draw_list = []
            for rel_y, rel_x, emoji_id in self.emoji_pos_cache:
                abs_y = chat_h - (rel_y - tui.chat_index - tui.have_title - subtitle_line + 1)
                if abs_y <= subtitle_line or abs_y > chat_h + tui.have_title + subtitle_line - 1:
                    continue
                draw_list.append((abs_y, chat_x + rel_x, emoji_id))
            self.chat_draw_list = draw_list

            # pfp
            top = 1 + subtitle_line
            bottom = chat_h + 1 + subtitle_line
            draw_list = []
            for rel_y, key in self.pfp_pos_cache:
                abs_y = chat_h - (rel_y - tui.chat_index - tui.have_title - subtitle_line + 1)
                if abs_y + self.pfp_size <= top or abs_y >= bottom:
                    continue
                draw_list.append((abs_y, chat_x, key))
            self.pfp_draw_list = draw_list
            self.pfp_clip = (top, bottom)

            self.prev_chat_index = tui.chat_index
            self.prev_chat_hw = tui.chat_hw
        self.redraw()


    def calculate_extra(self):
        """Convert cached extra window emoji positions to absolute cell positions"""
        tui = self.app.tui
        with tui.lock:
            if not tui.win_extra_window:
                self.extra_draw_list = []
                return
            win_y, win_x = tui.win_extra_window.getbegyx()
            win_h = tui.win_extra_window.getmaxyx()[0]
            draw_list = []
            for num, emoji_id, extra_w in self.emoji_assist_cache:
                rel_y = num - tui.extra_index
                if rel_y + 1 >= win_h or rel_y < 0:
                    continue
                draw_list.append((win_y + rel_y + 1, win_x + 1 + 2 * extra_w, emoji_id))
            self.extra_draw_list = draw_list
        self.redraw()


    def worker(self):
        """Thread that updates position caches and queues missing downloads"""
        while self.run:
            self.update_event.wait()
            self.update_event.clear()
            if not self.run:
                break
            tui = self.app.tui
            visible = set()
            visible_pfp = set()
            new_pos_cache = []
            new_pfp_cache = []
            new_assist_cache = []

            # emoji and profile pictures in chat
            chat_w = tui.chat_hw[1]
            for rel_y, line_map in enumerate(self.chat_map):
                if not line_map:
                    continue

                # pfp
                if line_map[1]:
                    try:
                        message = self.messages[line_map[0]]
                        user_id = message["user_id"]
                    except (IndexError, KeyError):
                        user_id = None
                    if user_id:
                        avatar_id = message.get("avatar")
                        key = avatar_id or user_id
                        with self.pfp_lock:
                            if key not in self.pfp_images:
                                self.pfp_images[key] = None
                                self.pfp_queue.put((key, user_id, avatar_id))
                        visible_pfp.add(key)
                        new_pfp_cache.append((rel_y, key))

                if line_map[3]:
                    iterable = line_map[3]
                elif line_map[5] and line_map[5][2]:
                    iterable = line_map[5][2]
                else:
                    iterable = ()
                for emoji in iterable:
                    if line_map[3]:
                        _, rel_x, emoji_id = emoji
                        rel_x -= 1 + self.post_one_reaction_len
                    else:
                        rel_x, _, emoji_id = emoji
                    if not emoji_id or rel_x >= chat_w - 1 or len(emoji_id) < 5:
                        continue
                    with self.images_lock:
                        if emoji_id not in self.images:
                            self.images[emoji_id] = None
                            self.download_queue.put(emoji_id)
                    visible.add(emoji_id)
                    new_pos_cache.append((rel_y, rel_x, emoji_id))

            # emoji in extra window with emoji assist
            if tui.win_extra_window:
                win_h = tui.win_extra_window.getmaxyx()[0]
                for num, line in enumerate(self.assist_data):
                    rel_y = num - tui.extra_index
                    if rel_y < 0 or not line[1].startswith("<:"):
                        continue
                    if rel_y + 1 >= win_h:
                        break
                    emoji_id = line[1].split(":")[-1][:-1]
                    extra_w = line[0].startswith("** ")
                    with self.images_lock:
                        if emoji_id not in self.images:
                            self.images[emoji_id] = None
                            self.download_queue.put(emoji_id)
                    visible.add(emoji_id)
                    new_assist_cache.append((num, emoji_id, extra_w))

            # update changed positions
            chat_changed = False
            if new_pos_cache != self.emoji_pos_cache:
                self.emoji_pos_cache = new_pos_cache
                chat_changed = True
            if new_pfp_cache != self.pfp_pos_cache:
                self.pfp_pos_cache = new_pfp_cache
                chat_changed = True
            if chat_changed:
                self.calculate_chat()
            if new_assist_cache != self.emoji_assist_cache:
                self.emoji_assist_cache = new_assist_cache
                self.calculate_extra()

            # delete unused images
            with self.images_lock:
                for emoji_id in [emoji_id for emoji_id in self.images if emoji_id not in visible]:
                    del self.images[emoji_id]
            with self.pfp_lock:
                for key in [key for key in self.pfp_images if key not in visible_pfp]:
                    del self.pfp_images[key]


    def downloader(self):
        """Thread that downloads emoji, loads it and triggers redraw"""
        while self.run:
            emoji_id = self.download_queue.get()
            if emoji_id is None:
                break
            image_path = self.app.discord.get_emoji(emoji_id, img_type="png", cache=self.emoji_cache_path, keepalive=True)
            if not image_path:
                continue
            scale = self.gtk_window.get_scale_factor()
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(image_path, self.size * scale, self.size * scale, True)
            except GLib.Error:
                logger.warning(f"Failed loading emoji: {emoji_id}")
                continue
            surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None)
            with self.images_lock:
                if emoji_id not in self.images:
                    continue
                self.images[emoji_id] = surface
            self.redraw()


    def pfp_downloader(self):
        """Thread that downloads profile pictures, rounds them, loads them and triggers redraw"""
        while self.run:
            item = self.pfp_queue.get()
            if item is None:
                break
            key, user_id, avatar_id = item
            scale = self.gtk_window.get_scale_factor()
            pixel_size = self.pfp_side * scale
            image_size = min((x for x in IMAGE_SIZES if x >= pixel_size), default=None)

            # check if cached
            image_path = utils.search_pfp_cache(self.pfp_cache_path, f"{avatar_id}*", avatar_id)
            if not image_path:
                image_path = self.app.discord.get_pfp(user_id, avatar_id, size=image_size, img_type="png", save_path=self.pfp_cache_path, keepalive=True)
                if image_path and not avatar_id:
                    image_path = peripherals.resize_image(image_path, h=image_size, w=image_size)
            if not image_path:
                continue

            if self.round:
                try:
                    image_path = peripherals.make_round_image(image_path, antialias=self.antialias)
                except Exception as e:
                    logger.error(f"Error converting image: {e}")

            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(image_path, pixel_size, pixel_size, True)
            except GLib.Error:
                logger.warning(f"Failed loading profile picture: {key}")
                continue
            surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None)
            with self.pfp_lock:
                if key not in self.pfp_images:
                    continue
                self.pfp_images[key] = surface
            self.redraw()
