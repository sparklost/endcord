# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import ctypes
import ctypes.util
import os
import sys

import numpy as np

if sys.platform == "win32":
    lib_name = "librnnoise.dll"
elif sys.platform == "darwin":
    lib_name = "librnnoise.dylib"
else:
    lib_name = "librnnoise.so"


class RNNoise:
    """RNNoise wrapper class"""

    def __init__(self, lib_path=None):
        check_path = True
        if not lib_path:
            lib_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), lib_name)
            if not os.path.exists(lib_path):
                lib_path = ctypes.util.find_library("rnnoise") or lib_name
                check_path = False
        if not lib_path or (check_path and not os.path.exists(lib_path)):
            raise OSError("RNNoise library not bundled and not found on system")
        self.lib = ctypes.CDLL(lib_path)
        self.lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_create.restype = ctypes.c_void_p
        self.lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
        self.lib.rnnoise_process_frame.restype = ctypes.c_float
        self.lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        self.lib.rnnoise_destroy.restype = None
        self.state = self.lib.rnnoise_create(None)


    def process_frame(self, mono_float):
        """Process float32 mono frame with data in range -1 to 1"""
        in_float = (mono_float * 32767.0).astype(np.float32)
        out_float = np.zeros(480, dtype=np.float32)
        in_ptr = in_float.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        out_ptr = out_float.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        vad = self.lib.rnnoise_process_frame(self.state, out_ptr, in_ptr)
        return vad, out_float / 32767.0


    def destroy(self):   # noqa
        if hasattr(self, "state") and self.state:
            self.lib.rnnoise_destroy(self.state)
            self.state = None


    def __del__(self):   # noqa
        self.destroy()
