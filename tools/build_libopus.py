# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import os
import subprocess
import tarfile
import urllib.request


def download(url, dest_path):
    """Download a file if it doesnt exist"""
    if not os.path.exists(dest_path):
        try:
            urllib.request.urlretrieve(url, dest_path)
        except Exception as e:
            raise OSError(f"Error downloading {url}: {e}")
    return dest_path


def build_libopus(build_dir="", opus_version="1.5.2"):
    """Build libopus"""
    print("  Downloading libopus release")
    opus_url = f"https://downloads.xiph.org/releases/opus/opus-{opus_version}.tar.gz"
    working_dir = os.path.join(build_dir, f"opus-{opus_version}")
    save_path = download(opus_url, os.path.join(build_dir, "opus.tar.gz"))
    with tarfile.open(save_path, "r:gz") as tar:
        tar.extractall(path=build_dir, filter="data")

    print("  Compiling libopus library")
    cmd = ["sh", "./configure", "--disable-doc", "--disable-extra-programs"]
    subprocess.run(cmd, cwd=working_dir, check=True, capture_output=True)
    subprocess.run(["make", f"-j{os.cpu_count() or 1}"], cwd=working_dir, check=True, capture_output=True)

    for filename in ("libopus.so", "libopus.dll", "libopus.dylib"):
        path = os.path.abspath(os.path.join(working_dir, ".libs", filename))
        if os.path.exists(path):
            return path
    return None


if __name__ == "__main__":
    print(build_libopus())
