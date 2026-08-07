# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import hashlib
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


def build_rnnoise(build_dir="", rnnoise_tag=None, avx2=False):
    """Build RNNoise"""

    # download repo
    print("  Downloading RNNoise repository")
    if not rnnoise_tag or rnnoise_tag == "main":
        rnnoise_tag = "main"
        rnnoise_url = "https://github.com/xiph/rnnoise/archive/main.tar.gz"
    else:
        rnnoise_url = f"https://github.com/xiph/rnnoise/archive/refs/tags/{rnnoise_tag}.tar.gz"
    working_dir = os.path.join(build_dir, f"rnnoise-{rnnoise_tag.removeprefix("v")}")
    save_path = download(rnnoise_url, os.path.join(build_dir, "rnnoise.tar.gz"))
    with tarfile.open(save_path, "r:gz") as tar:
        tar.extractall(path=build_dir, filter="data")
    model_version_path = os.path.join(working_dir, "model_version")
    with open(model_version_path, "r", encoding="utf-8") as f:
        model_hash = f.read().strip()

    # download model
    print("  Downloading RNNoise model data")
    model_path = os.path.join(working_dir, "rnnoise_data.tar.gz")
    model_url = f"https://media.xiph.org/rnnoise/models/rnnoise_data-{model_hash}.tar.gz"
    download(model_url, model_path)
    sha256_hash = hashlib.sha256()
    with open(model_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    calculated_checksum = sha256_hash.hexdigest()
    if model_hash != calculated_checksum:
        raise OSError("Mismatching checksums for RNNoise model")
    with tarfile.open(model_path, "r:gz") as tar:
        tar.extractall(path=working_dir, filter="data")

    # build
    print(f"  Compiling RNNoise library{" with AVX2 support" if avx2 else ""}")
    os.environ["LD"] = ""   # rnnoise doesnt like lld
    subprocess.run(["autoreconf", "-isf"], cwd=working_dir, check=True, capture_output=True)
    cmd = ["sh", "./configure", "--disable-doc", "--disable-examples"]
    cmd += ["--enable-x86-rtcd"] if avx2 else []
    subprocess.run(cmd, cwd=working_dir, check=True, capture_output=True)
    subprocess.run(["make"], cwd=working_dir, check=True, capture_output=True)

    for filename in ("librnnoise.so", "librnnoise.dll", "librnnoise.dylib"):
        path = os.path.abspath(os.path.join(working_dir, ".libs", filename))
        if os.path.exists(path):
            return path
    return None


if __name__ == "__main__":
    print(build_rnnoise())
