import os
import time

from PIL import Image, ImageGrab

from endcord import peripherals


def save_image():
    """If there is image in clipboard, save it to temp path"""
    img = ImageGrab.grabclipboard()
    if isinstance(img, Image.Image):
        save_path = os.path.join(peripherals.temp_path, f"clipboard_image_{int(time.time())}.png")
        img.save(save_path)
        return [save_path]
    return []
