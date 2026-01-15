import logging
import os
import urllib.parse

import urllib3
from urllib3.contrib.socks import SOCKSProxyManager

from endcord import peripherals

CHUNK_SIZE = 1024 * 1024   # load max 1MB data in RAM when downloading
logger = logging.getLogger(__name__)


def convert_tenor_gif_type(url, content_type):
    """
    Convert tenor video link between types:
    0 - gif HD
    1 - gif UHD
    2 - mp4 Video
    """
    if content_type == 1:
        return url.replace("AAAPo/", "AAAAC/")[:-3] + "gif"
    if content_type == 2:
        return url
    return url.replace("AAAPo/", "AAAAd/")[:-3] + "gif"


class Downloader:
    """Downloader class"""

    def __init__(self, proxy=None):
        self.downloading = True
        self.active = 0
        self.proxy = proxy


    def download(self, url, file_id=None):
        """Thread that downloads file and stores it in temp folder"""
        if not os.path.exists(os.path.expanduser(peripherals.temp_path)):
            os.makedirs(os.path.dirname(os.path.expanduser(peripherals.temp_path)), exist_ok=True)
        url_object = urllib.parse.urlsplit(url)
        filename = os.path.basename(url_object.path)
        proxy = urllib.parse.urlsplit(self.proxy)
        if proxy.scheme.lower() == "http":
            http = urllib3.ProxyManager(self.proxy)
        elif proxy.scheme and "socks" in proxy.scheme.lower():
            # socket is replaced with PySocks globally in app.py
            http = SOCKSProxyManager(self.proxy)
        else:
            http = urllib3.PoolManager()
            if proxy.scheme:
                logger.warning("Invalid proxy, continuing without proxy")
        response = http.request("GET", url, preload_content=False)
        extension = response.headers.get("Content-Type", None).split("/")[-1].replace("jpeg", "jpg")
        if file_id:
            unique_filename = file_id + "_" + filename
        else:
            unique_filename = filename
        destination = os.path.join(os.path.expanduser(peripherals.temp_path), unique_filename)
        if os.path.splitext(destination)[-1] == "" and extension:
            destination = destination + "." + extension
        self.active += 1
        self.downloading = True
        complete = False
        with open(destination, "wb") as out:
            while self.downloading:
                data = response.read(CHUNK_SIZE)
                if not data:
                    complete = True
                    break
                out.write(data)
        response.release_conn()
        self.active -= 1
        if self.active == 0:
            self.downloading = True
        if complete:
            return destination, filename
        logger.erorr("Error downloading file")
        return None, None

    def cancel(self):
        """Stops all active downloads"""
        self.downloading = False
