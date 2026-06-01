# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import curses
import glob
import importlib
import logging
import os
import queue
import threading
import time

from PIL import Image, ImageEnhance

from endcord import peripherals, terminal_utils, utils, xterm256

logger = logging.getLogger(__name__)
ESC = "\x1b"
RESET = f"{ESC}[0m"


def freed_vertical_segments(old_rects, new_rects_y):
    """Find freed vertical segments"""
    to_clear = []
    occupied = []
    for new_y, new_h in new_rects_y:
        occupied.append((new_y, new_y + new_h))
    occupied.sort()

    # for each old rect subtract occupied vertical ranges
    for oy, ox, oh, ow in old_rects:
        old_end = oy + oh
        cursor = oy
        for occ_start, occ_end in occupied:
            if occ_end <= cursor:
                continue
            if occ_start >= old_end:
                break
            if occ_start > cursor:   # start segnemt
                to_clear.append((ox, cursor, ow, occ_start - cursor))
            cursor = max(cursor, occ_end)
        if cursor < old_end:   # end segment
            to_clear.append((ox, cursor, ow, old_end - cursor))

    return to_clear


class InlineMedia:
    """Main extension class"""

    def __init__(self, discord, tui, config):
        self.discord = discord
        self.tui = tui
        self.use_blocks = config["media_use_blocks"]
        self.inline_media_quality = config["inline_media_quality"]
        if self.inline_media_quality not in ("lossless", "low", "high"):
            self.inline_media_quality = "low"
        self.truecolor = config["media_truecolor"]
        self.saturation = config["media_saturation"]
        self.run = True
        self.prev_chat_index = None
        self.prev_win_hw = self.tui.screen_hw
        self.force_draw = False
        self.image_cache_path = os.path.expanduser(os.path.join(peripherals.cache_path, "images"))
        self.image_cache = {}
        self.drawn_areas = []
        self.prev_chat_hw = None
        self.image_cache_lock = threading.Lock()
        self.download_queue = queue.Queue()
        if not self.use_blocks:
            self.ascii_palette = list(config["media_ascii_palette"])
        if self.truecolor:   # select drawing algorithm
            self.img_to_term_block = img_to_term_block_truecolor
        else:
            self.img_to_term_block = img_to_term_block

        threading.Thread(target=utils.delete_old_files, daemon=True, args=(   # might delay startup
            os.path.join(peripherals.cache_path, "images"),
            config["max_thumb_cache_age"],
            True,
        )).start()
        threading.Thread(target=self.downloader, daemon=True).start()


    def stop(self):
        """Stop all threads"""
        self.run = False
        self.download_queue.put([None] * 8)


    def force_redraw(self):
        """When curses screen.clear(), images are cleared too so redraw them"""
        self.force_draw = True
        self.draw_images()


    def update(self, chat_map, messages):
        """Get new data, queue downloads, update caches and trigger forced redraw if needed"""
        images = []

        # check chat_map for images
        for rel_y, line_map in enumerate(chat_map):
            if not line_map:
                continue
            if not line_map[5]:
                continue
            img_pos = line_map[5][5]
            if not img_pos or len(img_pos) < 4:
                continue

            # collect data
            rel_x, w, embed_idx, h, = img_pos
            try:
                message = messages[line_map[0]]
                message_id = message["id"]
                image_id = f"{message_id}_{embed_idx}"
                embed_name = message["embeds"][embed_idx]["name"]
                draw = not (embed_name and embed_name.startswith("SPOILER_"))
                if not draw:
                    draw = 1000 + embed_idx in message.get("spoiled", [])
            except IndexError:
                continue

            # update cache
            with self.image_cache_lock:
                if image_id not in self.image_cache:
                    self.force_draw = True
                    self.image_cache[image_id] = [None, rel_y, rel_x, h, w, draw]
                elif self.image_cache[image_id][1:] != [rel_y, rel_x, h, w, draw]:
                    if self.image_cache[image_id][3] != h or self.image_cache[image_id][4] != w:
                        # make this one redownload because changed dimensions
                        self.image_cache[image_id][0] = None
                    self.force_draw = True
                    self.image_cache[image_id][1:] = [rel_y, rel_x, h, w, draw]

            # download image if needed
            if image_id not in self.image_cache or self.image_cache[image_id][0] is not None or h != self.image_cache[image_id][2] or w != self.image_cache[image_id][3]:
                self.download_queue.put((message, embed_idx, image_id, rel_y, rel_x, h, w, draw))
            images.append(image_id)

        # clear cache
        with self.image_cache_lock:
            to_delete = []
            for image_id, image in self.image_cache.items():
                if image_id not in images:
                    to_delete.append(image_id)
                    self.force_draw = True
            for image_id in to_delete:
                del self.image_cache[image_id]


    def draw_images(self):
        """Re-calculate image positions and draw them"""
        if not self.force_draw and self.prev_chat_index == self.tui.chat_index and self.prev_chat_hw[0] == self.tui.chat_hw[0]:
            return
        if self.prev_win_hw != self.tui.screen_hw:
            self.prev_win_hw = self.tui.screen_hw
            self.force_redraw()
        drawn_areas = []
        time.sleep(0.0001)   # delay for clear_images to flush
        with self.tui.lock:
            chat_y, chat_x = self.tui.win_chat.getbegyx()
            chat_h = self.tui.chat_hw[0]
            with self.image_cache_lock:
                for data, rel_y, rel_x, h, w, draw in self.image_cache.values():
                    if not data or not draw:
                        continue
                    abs_y = chat_h - (rel_y - self.tui.chat_index - self.tui.have_title + 1)
                    if abs_y - chat_y <= -h or abs_y > chat_h:
                        continue
                    abs_x = chat_x + rel_x
                    cut_y = 0
                    cut_h = h
                    if abs_y > chat_h - h + 1:
                        cut_h = min(h, chat_h - abs_y + 1)
                    if abs_y <= 0:
                        cut_h += abs_y - chat_y
                        cut_y = -abs_y + 1
                        abs_y = chat_y
                    # logger.info(("DRAW", (h, w), abs_y, rel_y, cut_h, cut_y))
                    terminal_utils.draw_over_curses("\n".join(data.split("\n")[cut_y:cut_y + cut_h]), abs_y, abs_x)
                    drawn_areas.append((abs_y, abs_x, cut_h, w))
            self.draw_selection(self.tui.chat_selected)
        self.drawn_areas = drawn_areas
        self.prev_chat_index = self.tui.chat_index
        self.prev_chat_hw = self.tui.chat_hw
        self.force_draw = False


    def clear_images(self, force=False):
        """Clear all areas that are no longer used by images"""
        if not self.force_draw and not force and self.prev_chat_index == self.tui.chat_index and self.prev_chat_hw[0] == self.tui.chat_hw[0]:
            return

        # get occupied vertical ranges
        occupied = []
        chat_y, chat_x = self.tui.win_chat.getbegyx()
        chat_h = self.tui.chat_hw[0]
        for _, rel_y, _, h, w, draw in self.image_cache.values():
            if not draw:
                continue
            abs_y = chat_h - (rel_y - self.tui.chat_index - self.tui.have_title + 1)
            cut_h = h
            if abs_y - chat_y <= -h or abs_y >= chat_h:
                continue
            if abs_y > chat_h - h + 1:
                cut_h = min(h, chat_h - abs_y + 1)
            if abs_y <= 0:
                cut_h += abs_y - chat_y
                abs_y = chat_y
            occupied.append((abs_y, abs_y + cut_h, w))
        occupied.sort()

        # for each old rect subtract occupied vertical ranges
        to_clear = []
        for y, x, h, w in self.drawn_areas:
            old_end = y + h
            new_y = y
            if force:
                to_clear.append((new_y, old_end, x, w))
                continue
            for occupied_start, occupied_end, occupied_w in occupied:
                if occupied_w < w:   # right of new image that is smaller than old image
                    to_clear.append((occupied_start, occupied_end, x + occupied_w, w - occupied_w))
                if occupied_end <= new_y:
                    continue
                if occupied_start >= old_end:
                    break
                if occupied_start > new_y:   # start segnemt
                    to_clear.append((new_y, occupied_start, x, w))
                new_y = max(new_y, occupied_end)
            if new_y < old_end:   # end segment
                to_clear.append((new_y, old_end, x, w))
        # logger.info(f"DATA:\n  {self.drawn_areas}\n  {occupied}\n  {to_clear}")

        # clear segments, if any
        if not to_clear:
            return
        with self.tui.lock:
            for y_start, y_end, x, w in to_clear:
                for row in range(y_start, y_end):
                    terminal_utils.draw_over_curses(" " * w, row, x)
        self.drawn_areas = []


    def get_images(self):
        """Get image y ranges in chat"""
        images = []
        for data, rel_y, rel_x, h, w, draw in self.image_cache.values():
            if not data or not draw:
                continue
            images.append((rel_y - h + 1, rel_y))
        return images


    def draw_selection(self, pos):
        """Draaw selection line around images"""
        chat_y, chat_x = self.tui.win_chat.getbegyx()
        chat_h, chat_w = self.tui.chat_hw
        for data, rel_y, rel_x, h, w, draw in self.image_cache.values():
            if not data or not draw:
                continue
            if pos < rel_y - h + 1 or pos > rel_y:
                continue
            line_y = chat_h - (pos - self.tui.chat_index + 1)
            if line_y >= chat_h or line_y < 0:
                continue
            line = self.tui.chat_buffer[pos]
            with self.tui.lock:
                self.tui.win_chat.insstr(line_y, 0, line[:rel_x] + "\n", curses.color_pair(16))
                self.tui.win_chat.insstr(line_y, rel_x + w, (" " * (chat_w - rel_x - w)) + "\n", curses.color_pair(16))
                self.tui.win_chat.noutrefresh()
                self.tui.need_update.set()
            break


    def downloader(self):
        """Downloader for inline media"""
        while self.run:
            message, embed_idx, image_id, rel_y, rel_x, h, w, draw = self.download_queue.get()
            if not message:
                break

            # get message and image info
            embed = message["embeds"][embed_idx]
            img_url = embed["proxy_url"]
            img_h, img_w = embed["hw"]
            if img_h == 0 or img_w == 0:
                continue
            scale = min(h * (1 + self.use_blocks) * 2 / img_h, w * 2 / img_w, 1)
            img_w = int(img_w * scale)
            img_h = int(img_h * scale)

            img_quality = "lossless" if "//media." in img_url else self.inline_media_quality
            if img_url.endswith("&"):
                img_url += "="
            if "?" not in img_url:
                img_url += "?"

            # reuse larger cached image or delete smaller
            for path in glob.glob(os.path.join(self.image_cache_path, f"{image_id}_*")):
                try:
                    filename = os.path.splitext(os.path.basename(path))[0]
                    _, _, new_w, new_h = filename.split(".")[0].split("_")
                    if int(new_w) >= img_w:
                        img_w = int(new_w)
                        img_h = int(new_h)
                        break
                    else:
                        os.remove(path)
                        break   # assuming only one can exist
                except Exception:
                    pass

            # download
            img_url = f"{img_url}&format=webp&quality={img_quality}&width={img_w}&height={img_h}"
            img_name = f"{image_id}_{img_w}_{img_h}.webp"
            image_path = self.discord.get_file(img_url, self.image_cache_path, file_name=img_name, cache=True, keepalive=True)
            if not image_path:
                continue
            data = self.load_image(image_path, h, w)
            if not data:
                continue
            with self.image_cache_lock:
                if image_id not in self.image_cache:
                    continue
                self.image_cache[image_id][0] = data
            if not draw:
                continue

            # draw
            chat_y, chat_x = self.tui.win_chat.getbegyx()
            chat_h = self.tui.chat_hw[0]
            with self.tui.lock:
                with self.image_cache_lock:
                    abs_y = chat_h - (rel_y - self.tui.chat_index - self.tui.have_title + 1)
                    if abs_y - chat_y <= -h or abs_y > chat_h:
                        continue
                    abs_x = chat_x + rel_x
                    cut_y = 0
                    cut_h = h
                    if abs_y > chat_h - h + 1:
                        cut_h = min(h, chat_h - abs_y + 1)
                    if abs_y <= 0:
                        cut_h += abs_y - chat_y
                        cut_y = -abs_y + 1
                        abs_y = chat_y
                    # logger.info(("INIT", (h, w), abs_y, rel_y, cut_h, cut_y))
                    terminal_utils.draw_over_curses("\n".join(data.split("\n")[cut_y:cut_y + cut_h]), abs_y, abs_x)
                    self.drawn_areas.append((abs_y, abs_x, cut_h, w))
                self.draw_selection(self.tui.chat_selected)


    def load_image(self, path, h, w):
        """Load image and convert it to colored characters string of given size and type (ascii/blocks)"""
        img = Image.open(path)
        img = img.resize((w, h*(1+self.use_blocks)), Image.Resampling.LANCZOS)

        if img.mode != "RGB" and img.mode != "L":
            background = Image.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background

        if self.use_blocks:
            if self.truecolor:
                img = img.convert("RGB")
            else:
                img_palette = Image.new("P", (16, 16))
                img_palette.putpalette(xterm256.palette_short)
                img = img.quantize(palette=img_palette, dither=0)
            return self.img_to_term_block(img, -1, w, h, w, h*2)

        img = img.resize((w, h), Image.Resampling.LANCZOS)
        img_gray = img.convert("L")
        if self.saturation:
            sat = ImageEnhance.Color(img)
            img = sat.enhance(self.saturation)
        img_palette = Image.new("P", (16, 16))
        img_palette.putpalette(xterm256.palette_short)
        img = img.quantize(palette=img_palette, dither=0)
        return img_to_term(img, img_gray, -1, self.ascii_palette, len(self.ascii_palette), w, h, w, h)


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


def img_to_term_block(img, bg_color, screen_width, screen_height, img_width, img_height):
    """Convert image to ANSI-colored string made of half-blocks, ready to be printed in terminal"""
    pixels = img.load()

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
            top_color = pixels[x, y] + 16
            bot_color = pixels[x, y + 1] + 16
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


def img_to_term_block_truecolor(img, bg_color, screen_width, screen_height, img_width, img_height):
    """Convert image to ANSI true-color string made of half-blocks"""
    pixels = img.load()
    padding_h = (screen_height - img_height // 2) // 2
    padding_w = (screen_width - img_width) // 2

    bg = f"{ESC}[48;5;{bg_color}m"   # bg color is not in r;g;b
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
            top_color = pixels[x, y]
            bot_color = pixels[x, y + 1]
            if top_color != current_fg:
                line_parts.append(f"{ESC}[38;2;{top_color[0]};{top_color[1]};{top_color[2]}m")
                current_fg = top_color
            if bot_color != current_bg:
                line_parts.append(f"{ESC}[48;2;{bot_color[0]};{bot_color[1]};{bot_color[2]}m")
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


# use cython if available, ~1.7 times faster
if importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.media"):
    from endcord_cython.media import (
        img_to_term,
        img_to_term_block,
        img_to_term_block_truecolor,
    )
