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
    """Extract filename from Content-Disposition, falling back to URL and Content-Type."""
    content_disposition = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)", content_disposition, re.IGNORECASE)
    if match:
        filename = urllib.parse.unquote(match.group(1).strip().strip("\"'"))
        if filename:
            return os.path.basename(filename)
    match = re.search(r'filename\s*=\s*("(?:[^"\\]|\\.)*"|[^;]+)', content_disposition, re.IGNORECASE)
    if match:
        filename = match.group(1).strip().strip("\"'")
        if filename:
            return os.path.basename(filename)

    filename = os.path.basename(urllib.parse.unquote(url_path.rstrip("/"))) or "downloaded_file"
    content_type = headers.get("Content-Type", "")
    mime_type = content_type.split(";", 1)[0].strip().lower()
    extension = mime_type.split("/", 1)[-1].strip() if "/" in mime_type else ""
    ignored_extensions = {"plain", "octet-stream", "unknown"}

    if extension and extension not in ignored_extensions:
        extension = {
            "jpeg": "jpg",
            "svg+xml": "svg",
            "x-msdownload": "exe",
        }.get(extension, extension)
        filename = os.path.splitext(filename)[0] + "." + extension

    return filename



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
        """Thread that downloads file and stores it in save_dir"""
        if url.lower().split("/")[0] == "http":
            error = "Rejecting http download"
            logger.warning(f"{error} - HIGH SECURITY RISK")
            return None, error
        os.makedirs(self.save_dir, exist_ok=True)
        self.active += 1
        self.downloading = True
        complete = False
        connection = None
        current_url = url
        redirects = 0
        destination = None
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
                        return None, "Redirect without location"
                    current_url = urllib.parse.urljoin(current_url, redirect_url)
                    redirects += 1
                    connection.close()
                    continue
                elif response.status in (200, 206):
                    break
                else:
                    error = f"HTTP Error {response.status}"
                    logger.error(f"{error} for URL: '{current_url}'")
                    return None, error
            else:
                error = "Exceeded maximum redirect limit"
                logger.error(f"{error} (5) for URL: '{url}'")
                return None, error

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
            if not complete and destination and os.path.exists(destination):
                try:
                    os.remove(destination)
                except OSError:
                    pass
            self.active -= 1
            if self.active == 0:
                self.downloading = True
        if complete:
            return destination, filename
        error = "Download incomplete or failed"
        logger.error(f"{error} for URL: '{url}'")
        return None, error


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
