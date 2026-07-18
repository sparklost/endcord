# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import logging
import os
import re
import socket
import urllib.parse

from endcord import peripherals

CHUNK_SIZE = 1024 * 1024   # load max 1MB data in RAM when downloading
logger = logging.getLogger(__name__)


def get_tenor_gif(url, header=None, proxy=None):
    """Request tenor gif page and extract direct gif url"""
    if "tenor.com/" not in url:
        return
    parsed = urllib.parse.urlsplit(url)
    try:
        connection = peripherals.get_connection(parsed.netloc, proxy=proxy)
        if header:
            connection.request("GET", parsed.path, headers=header)
        else:
            connection.request("GET", parsed.path)
        response = connection.getresponse()
    except (socket.gaierror, TimeoutError):
        connection.close()
        return None
    if response.status == 200:
        match = re.search(r'<link\s+rel="image_src"\s+href="([^"]+\.gif)"', response.read().decode())
        if not match:
            return None
        return match.group(1)
    connection.close()
    logger.error(f"Failed fetching tenor gif page, http error code: {response.status}")
    return None


def extract_file_name(headers, url_path):
    """Extract file name from Content-Disposition header, fallback to extracting from url + extension from Content-Type"""
    content_disposition = headers.get("Content-Disposition")
    if content_disposition:
        parts = content_disposition.split(";")
        for part in parts:
            if part.strip().startswith("filename*="):
                value = part.split("=", 1)[1]
                if "''" in value:
                    _, encoded_filename = value.split("''", 1)
                    return urllib.parse.unquote(encoded_filename)
        for part in parts:
            if part.strip().startswith("filename="):
                return part.strip().split("=", 1)[1].strip('"\'')

    filename = os.path.basename(url_path) or "downloaded_file"
    extension = headers.get("Content-Type", None).split("/")[-1].replace("jpeg", "jpg")
    if extension:
        if os.path.splitext(filename)[-1] != "":
            return f"{"".join(os.path.splitext(filename)[:-1])}.{extension}"
        return f"{filename}.{extension}"
    return "file"


class Downloader:
    """Streaming downloader using http.client"""

    def __init__(self, proxy=None, save_dir=None, headers=None, user_agent=None):
        self.downloading = True
        self.active = 0
        self.proxy = proxy
        if save_dir:
            self.save_dir = save_dir
        else:
            self.save_dir = os.getcwd()
        if headers:
            self.headers = headers
        else:
            self.headers = {"Accept": "*/*", "User-Agent": user_agent}


    def download(self, url, file_id=None, headers=None):
        """Thread that downloads file and stores it in temp folder"""
        if url.lower().split("/")[0] == "http":
            logger.warning("REJECTING HTTP-ONLY DOWNLOAD - HIGH SECURITY RISK")
            return None, None
        os.makedirs(self.save_dir, exist_ok=True)
        self.active += 1
        self.downloading = True
        complete = False
        connection = None
        current_url = url
        redirects = 0
        if not headers:
            headers = self.headers

        try:
            while redirects < 5:
                url_object = urllib.parse.urlsplit(current_url)
                host = url_object.hostname
                port = url_object.port or (443 if url_object.scheme.lower() == "https" else 80)
                path_with_query = url_object.path + (f"?{url_object.query}" if url_object.query else "")
                if not path_with_query:
                    path_with_query = "/"
                connection = peripherals.get_connection(host, port, proxy=self.proxy)
                connection.request("GET", path_with_query, headers=headers)
                response = connection.getresponse()

                if response.status in (301, 302, 303, 307, 308):
                    redirect_url = response.getheader("Location")
                    if not redirect_url:
                        logger.error("Redirect without location")
                        break
                    current_url = urllib.parse.urljoin(current_url, redirect_url)
                    redirects += 1
                    connection.close()
                    continue
                else:
                    break

            filename = extract_file_name(response.headers, url_object.path)
            unique_filename = f"{file_id}_{filename}" if file_id else filename
            destination = os.path.join(self.save_dir, unique_filename)

            with open(destination, "wb") as out:
                while self.downloading:
                    data = response.read(CHUNK_SIZE)
                    if not data:
                        complete = True
                        break
                    out.write(data)

        except Exception:
            logger.exception("Error downloading file:")
        finally:
            if connection:
                connection.close()
            self.active -= 1
            if self.active == 0:
                self.downloading = True
        if complete:
            return destination, filename
        logger.error(f"Error downloading file. Requested url: '{url}'")
        return None, None


    def cancel(self):
        """Stop all active downloads"""
        self.downloading = False


# # old implementation using urllib3
#
# import urllib3
# from urllib3.contrib.socks import SOCKSProxyManager
#
#
# class Downloader:
#     """Streaming downloader using urllib3"""
#
#     def __init__(self, proxy=None, save_dir=None):
#         self.downloading = True
#         self.active = 0
#         self.proxy = proxy
#         if save_dir:
#             self.save_dir = save_dir
#         else:
#             self.save_dir = os.getcwd()
#
#
#     def download(self, url, file_id=None):
#         """Thread that downloads file and stores it in configured save_dir"""
#         os.makedirs(self.save_dir, exist_ok=True)
#         url_object = urllib.parse.urlsplit(url)
#         filename = os.path.basename(url_object.path)
#         proxy = urllib.parse.urlsplit(self.proxy)
#         if proxy.scheme.lower() == "http":
#             http = urllib3.ProxyManager(self.proxy)
#         elif proxy.scheme and "socks" in proxy.scheme.lower():
#             # socket is replaced with PySocks globally in app.py
#             http = SOCKSProxyManager(self.proxy)
#         else:
#             http = urllib3.PoolManager()
#             if proxy.scheme:
#                 logger.warning("Invalid proxy, continuing without proxy")
#         response = http.request("GET", url, preload_content=False)
#         extension = response.headers.get("Content-Type", None).split("/")[-1].replace("jpeg", "jpg")
#         unique_filename = f"{file_id}_{filename}" if file_id else filename
#         destination = os.path.join(self.save_dir, unique_filename)
#         if os.path.splitext(destination)[-1] == "" and extension:
#             destination = destination + "." + extension
#         self.active += 1
#         self.downloading = True
#         complete = False
#         with open(destination, "wb") as out:
#             while self.downloading:
#                 data = response.read(CHUNK_SIZE)
#                 if not data:
#                     complete = True
#                     break
#                 out.write(data)
#         response.release_conn()
#         self.active -= 1
#         if self.active == 0:
#             self.downloading = True
#         if complete:
#             return destination, filename
#         logger.error("Error downloading file")
#         return None, None
#
#     def cancel(self):
#         """Stop all active downloads"""
#         self.downloading = False
