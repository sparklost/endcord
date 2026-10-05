# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import ctypes
import ctypes.util
import os
import sys

import numpy as np

if sys.platform == "win32":
    lib_name = "libopus.dll"
elif sys.platform == "darwin":
    lib_name = "libopus.dylib"
else:
    lib_name = "libopus.so"

OPUS_APPS = {"voip": 2048, "audio": 2049, "lowdelay": 2051}


def load_libopus(lib_path=None):
    """Load libopus"""
    check_path = True
    if not lib_path:
        lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), lib_name)
        if not os.path.exists(lib_path):
            lib_path = ctypes.util.find_library("opus") or lib_name
            check_path = False
    if not lib_path or (check_path and not os.path.exists(lib_path)):
        raise OSError("Opus library not bundled and not found on system")
    return ctypes.CDLL(lib_path)


class OpusEncoder:
    """libopus encoder wrapper class"""

    def __init__(self, application="voip", bitrate=None, vbr=True, samplerate=48000, channels=2, lib_path=None):
        self.channels = channels
        self.lib = load_libopus(lib_path)
        self.lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.opus_encoder_create.restype = ctypes.c_void_p
        self.lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int32]
        self.lib.opus_encoder_ctl.restype = ctypes.c_int
        self.lib.opus_encode_float.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_void_p, ctypes.c_int32]
        self.lib.opus_encode_float.restype = ctypes.c_int32
        self.lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        self.lib.opus_encoder_destroy.restype = None

        err = ctypes.c_int(0)
        self.state = self.lib.opus_encoder_create(samplerate, channels, OPUS_APPS.get(application, 2048), ctypes.byref(err))
        if err.value != 0 or not self.state:
            raise OSError(f"Failed to create opus encoder: {err.value}")

        self.lib.opus_encoder_ctl(self.state, 4006, 1 if vbr else 0)
        self.lib.opus_encoder_ctl(self.state, 4010, 10)
        if bitrate:
            self.lib.opus_encoder_ctl(self.state, 4002, bitrate)


    def encode(self, pcm_float, frame_size=960):
        """Encode interleaved float32 pcm to opus packet"""
        pcm = np.ascontiguousarray(pcm_float, dtype=np.float32)
        out = ctypes.create_string_buffer(4000)
        n = self.lib.opus_encode_float(self.state, pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), frame_size, out, 4000)
        if n < 0:
            raise RuntimeError(f"Opus encode error: {n}")
        return out.raw[:n]


    def destroy(self):   # noqa
        if hasattr(self, "state") and self.state:
            self.lib.opus_encoder_destroy(self.state)
            self.state = None


    def __del__(self):   # noqa
        self.destroy()


class OpusDecoder:
    """libopus decoder wrapper class"""

    def __init__(self, samplerate=48000, channels=2, lib_path=None):
        self.channels = channels
        self.lib = load_libopus(lib_path)
        self.lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.opus_decoder_create.restype = ctypes.c_void_p
        self.lib.opus_decode_float.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int]
        self.lib.opus_decode_float.restype = ctypes.c_int
        self.lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self.lib.opus_decoder_destroy.restype = None

        err = ctypes.c_int(0)
        self.state = self.lib.opus_decoder_create(samplerate, channels, ctypes.byref(err))
        if err.value != 0 or not self.state:
            raise OSError(f"Failed to create opus decoder: {err.value}")

        self.pcm = np.empty(5760 * channels, dtype=np.float32)   # 120ms at 48khz
        self.pcm_ptr = self.pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_float))


    def decode(self, data):
        """Decode opus packet to float32 array"""
        n = self.lib.opus_decode_float(self.state, data, len(data), self.pcm_ptr, 5760, 0)   # 120ms at 48khz
        if n < 0:
            raise RuntimeError(f"Opus decode error: {n}")
        return self.pcm[:n * self.channels].reshape(n, self.channels).copy()


    def destroy(self):   # noqa
        if hasattr(self, "state") and self.state:
            self.lib.opus_decoder_destroy(self.state)
            self.state = None


    def __del__(self):   # noqa
        self.destroy()
