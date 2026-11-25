# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request

LIB_NAMES = ("libopus.so", "libopus.dylib", "libopus-0.dll", "libopus.dll", "opus.dll")


def download(url, dest_path):
    """Download a file if it doesnt exist"""
    if not os.path.exists(dest_path):
        try:
            urllib.request.urlretrieve(url, dest_path)
        except Exception as e:
            raise OSError(f"Error downloading {url}: {e}")
    return dest_path


def get_env():
    """Environment for build commands, without foreign flag variables on windows"""
    env = os.environ.copy()
    if sys.platform == "win32":
        for name in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
            env.pop(name, None)
    return env


def run(cmd, cwd, silent=False):
    """Run command and show its output on failure"""
    result = subprocess.run(cmd, cwd=cwd, env=get_env(), capture_output=True, text=True, check=False)
    if result.returncode != 0 and not silent:
        print(result.stdout)
        print(result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd)


def find_library(root):
    """Search directory tree for matching library file"""
    for folder, _, files in os.walk(root):
        for filename in files:
            if filename in LIB_NAMES:
                return os.path.abspath(os.path.join(folder, filename))
    return None


def build_libopus(build_dir="", opus_version="1.5.2"):
    """Build libopus"""
    print("  Downloading libopus release")
    opus_url = f"https://downloads.xiph.org/releases/opus/opus-{opus_version}.tar.gz"
    working_dir = os.path.join(build_dir, f"opus-{opus_version}")
    save_path = download(opus_url, os.path.join(build_dir, "opus.tar.gz"))
    with tarfile.open(save_path, "r:gz") as tar:
        tar.extractall(path=build_dir, filter="data")

    print("  Compiling libopus library")
    jobs = str(os.cpu_count() or 1)
    if shutil.which("cmake"):
        try:
            run([
                "cmake", "-S", ".", "-B", "cmake_build", "-G", "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=ON",
                "-DOPUS_BUILD_PROGRAMS=OFF",
                "-DOPUS_BUILD_TESTING=OFF",
            ], working_dir, silent=True)
            run(["cmake", "--build", "cmake_build", "--config", "Release", "-j", jobs], working_dir)
            return find_library(os.path.join(working_dir, "cmake_build"))
        except (subprocess.CalledProcessError, OSError):
            print("  Cmake build failed, trying configure/make")
    run(["sh", "./configure", "--disable-doc", "--disable-extra-programs"], working_dir)
    run(["make", f"-j{jobs}"], working_dir)
    return find_library(os.path.join(working_dir, ".libs"))


if __name__ == "__main__":
    print(build_libopus())
