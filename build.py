# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import distribution

PYTHON_MAX_MINOR = 14
PYTHON_FREETHREADED = 14
PYTHON_LAST_SAFE = 13
PYTHON_PATCH = 6
CURSES_TAG = "v6_6_20260627"

CUSTOM_CFLAGS = [
    "-DNDEBUG",
    "-g0",
    "-O3",
    "-mtune=generic",
    "-fno-semantic-interposition",
    "-fno-strict-overflow",
    "-fvisibility=hidden",
    # "-flto=thin",
]
CUSTOM_CXXFLAGS = CUSTOM_CFLAGS
CUSTOM_LDFLAGS = [
    "-Wl,-s",
    "-Wl,-O1",
    "-Wl,--sort-common",
    "-Wl,--as-needed",
    "-Wl,-z,pack-relative-relocs",
    "-Wl,--exclude-libs,ALL",
    # "-flto=thin",
]
UNSAFE_FLAGS = [   # unsafe to use when building some libraries
    "-fvisibility=hidden",
]
CFLAGS_OLD = os.environ.get("CFLAGS", "")
CXXFLAGS_OLD = os.environ.get("CFLAGS", "")
LDFLAGS_OLD = os.environ.get("CFLAGS", "")

RED = "\033[1;31m"
PURPLE = "\033[1;35m"

if sys.platform.startswith("android"):
    sys.platform = "linux"
if "bsd" in sys.platform:
    sys.platform = "linux"


def get_app_name():
    """Get app name from pyproject.toml"""
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        if "project" in data and "version" in data["project"]:
            return str(data["project"]["name"])
        print("App name not specified in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    print("pyproject.toml file not found", file=sys.stderr)
    sys.exit(1)


def get_media_packages():
    """Get media packages from pyproject.toml"""
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        dependencies = data["dependency-groups"]["media"]
        names = []
        for dependency in dependencies:
            names.append(re.split(r"[<>=!~]", dependency)[0].strip())
        return names
    print("pyproject.toml file not found", file=sys.stderr)
    sys.exit(1)


def get_version_number():
    """Get version number from pyproject.toml"""
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        if "project" in data and "version" in data["project"]:
            return str(data["project"]["version"])
        print("Version not specified in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    print("pyproject.toml file not found", file=sys.stderr)
    sys.exit(1)


def is_gil_enabled():
    """Safely check if GIL is enabled"""
    try:
        return sys._is_gil_enabled()
    except AttributeError:
        return True


def get_python_version():
    """Get python major and minor versions"""
    if shutil.which("uv"):
        try:
            version_result = subprocess.run(["uv", "run", "--no-sync", "python", "-VV"], capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"uv error: {e}", file=sys.stderr)
            return sys.version_info.major, sys.version_info.minor, is_gil_enabled()
        all_parts = version_result.stdout.strip().split(" ")
        version_parts = all_parts[1].split(".")
        if len(version_parts) < 2:
            return sys.version_info.major, sys.version_info.minor, is_gil_enabled()
        return int(version_parts[0]), int(version_parts[1]), "free-threading" in all_parts[2]
    return sys.version_info.major, sys.version_info.minor, is_gil_enabled()


def get_nice_python_version():
    """Get clean python version"""
    version = sys.version
    start = version.find("(++")
    if start < 0:
        return version
    return version[:start] + version[version.find(")", start):]


def supports_color():
    """Return True if the running terminal supports ANSI colors."""
    if sys.platform == "win32":
        return (os.getenv("ANSICON") is not None or
            os.getenv("WT_SESSION") is not None or
            os.getenv("TERM_PROGRAM") == "vscode" or
            os.getenv("TERM") in ("xterm", "xterm-color", "xterm-256color")
        )
    if not sys.stdout.isatty():
        return False
    return os.getenv("TERM", "") != "dumb"


PKGNAME = get_app_name()
PKGVER = get_version_number()
USE_COLOR = supports_color()


def fprint(text, color_code=PURPLE, prefix=f"[{PKGNAME.capitalize()} Build Script]: "):
    """Print colored text prefixed with text, default is light purple"""
    if USE_COLOR:
        print(f"{color_code}{prefix}{text}\033[0m", flush=True)
    else:
        print(f"{prefix}{text}", flush=True)


def check_python():
    """Check python version and print warning, and return True if runing inside pure python (no uv)"""
    if sys.version_info.major != 3:
        print(f"Python {sys.version_info.major} is not supported. Only Python 3 is supported.", file=sys.stderr)
        sys.exit(1)

    if os.environ.get("UV", ""):
        if sys.version_info.minor < 12 or sys.version_info.minor > PYTHON_MAX_MINOR:
            fprint(f'WARNING: Python {sys.version_info.major}.{sys.version_info.minor} is not supported but build may succeed. Run "python build.py" to let uv download and setup recommended temporary python interpreter.', color_code=RED)
        else:
            try:
                version = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True)
                fprint(f"Using {version.stdout.strip()}")
            except Exception:
                pass
            fprint(f"Using Python {get_nice_python_version()}")
        if not is_gil_enabled():
            if sys.version_info.minor == PYTHON_FREETHREADED:
                fprint("WARNING: While endcord works with freethreaded python, final binary is much larger. Nutka doesnt yet support freethreaded python, so build is likely to fail.", color_code=RED)
            else:
                fprint(f'WARNING: Endcord is known to only build with freethreaded python version 3.{PYTHON_FREETHREADED}. Buil is likely to fail on other versions. Run "python build.py" to let uv download and setup recommended temporary python interpreter, optionally with flag "--freethreaded".', color_code=RED)
        return False

    try:
        version = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"uv error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("uv command not found, please ensure uv is installed and in PATH", file=sys.stderr)
        sys.exit(1)
    return True


def ensure_python(freethreaded, safe=False):
    """Check current python and download correct python if needed"""
    if safe:
        selected_version = PYTHON_LAST_SAFE
    else:
        selected_version = PYTHON_MAX_MINOR

    _, minor, have_freethreaded = get_python_version()
    if minor == selected_version and freethreaded == have_freethreaded:
        return None, have_freethreaded

    if freethreaded:
        version = f"3.{PYTHON_FREETHREADED}+freethreaded"
    else:
        version = f"3.{selected_version}"
        # ensure there is no same-name freethreaded python
        subprocess.run(["uv", "python", "uninstall", f"3.{minor}+freethreaded"], check=False)

    freethreaded_string = "freethreaded " if freethreaded else ""
    fprint(f"Setting up {freethreaded_string}python {version} for this project")
    subprocess.run(["uv", "python", "install", version], check=True)

    return version, have_freethreaded or freethreaded


def check_patchelf():
    """Patchelf is required for nuitka, so check early if its installed"""
    if sys.platform != "linux":
        return

    patchelf_path = shutil.which("patchelf")
    if not patchelf_path:
        fprint("Patchelf is required for building with nuitka. Please install it first.", color_code=RED)
        sys.exit(1)
    try:
        result = subprocess.run([patchelf_path, "--version"], capture_output=True, text=True, check=True)
        output = result.stdout.strip().lower()
        if not output.startswith("patchelf "):
            return
        if output.split(" ")[1].startswith("0.18."):
            fprint("Patchelf version 0.18.0 is a known buggy release, nuitka will likely refuse to use it! Please upgrade or downgrade it.", color_code=RED)
    except Exception:
        pass


def check_media_support():
    """Check if media is supported"""
    return (
        importlib.util.find_spec("av") is not None and
        importlib.util.find_spec("dave") is not None and
        importlib.util.find_spec("PIL") is not None and
        importlib.util.find_spec("nacl") is not None
    )


def add_media():
    """Add media support"""
    if not check_media_support():
        fprint("Adding media support dependencies")
        subprocess.run(["uv", "sync", "--all-groups"], check=True)


def remove_media():
    """Remove media support"""
    if check_media_support():
        fprint("Removing media support dependencies")
        subprocess.run(["uv", "pip", "uninstall"] + get_media_packages(), check=True)


def check_dev():
    """Check if its dev environment and set it up"""
    if importlib.util.find_spec("PyInstaller") is None or importlib.util.find_spec("nuitka") is None:
        subprocess.run(["uv", "sync", "--group", "build"], check=True)


def is_local_build(package_name):
    """Check if package is locally built"""
    try:
        dist = distribution(package_name)
        for file in dist.files or []:
            if file.name == "WHEEL":
                wheel_path = dist.locate_file(file)
                break
        else:
            return False
        with open(wheel_path, "r", encoding="utf-8") as f:
            return "manylinux" not in f.read()
    except Exception:
        pass
    return False


def force_ujson():
    """Remove orjson and force installing ujson instead. WARNING: this modifies pyproject.toml"""
    try:
        subprocess.run(["uv", "remove", "orjson"], check=True, stderr=subprocess.DEVNULL)
        fprint("Switching orjson -> ujson   !! pyproject.toml is modified !!", color_code=RED)
        subprocess.run(["uv", "add", "ujson"], check=True)
    except subprocess.CalledProcessError:
        pass


def build_third_party_licenses(exclude=[]):
    """Collect and build all lincenses found in venv into THIRD_PARTY_LICENSES.txt file"""
    fprint("Building list of third party licenses")
    subprocess.run(["uv", "pip", "install", "pip-licenses"], check=True)
    command = [
        "uv", "run", "pip-licenses",
        "--ignore-packages " + " ".join(exclude),
        "--format=plain-vertical",
        "--no-license-path",
        "--output-file=THIRD_PARTY_LICENSES.txt",
    ]
    subprocess.run(command, check=True)
    subprocess.run(["uv", "pip", "uninstall", "pip-licenses", "prettytable", "wcwidth"], check=True)


def get_cython_bins(directory="endcord_cython", startswith=None):
    """Get list of all cython built binaries"""
    files = os.listdir(directory)
    bins = []
    for file in files:
        if (not startswith or file.startswith(startswith)) and (file.endswith(".pyd") or file.endswith(".so")):
            bins.append(file)
    return bins


def find_file_in_venv(lib_name, file_name, silent=False, recurse=False, startswith=False):
    """Search for file in specified library in current venv"""
    if isinstance(file_name, list):
        file_name = os.path.join(*file_name)
    for root, dirs, files in os.walk(".venv"):
        path_parts = root.split(os.sep)
        if lib_name in path_parts:
            if not recurse and path_parts[-1] != lib_name:
                continue
            for f in files:
                if (startswith and f.startswith(file_name)) or f == file_name:
                    return os.path.join(root, f)
    if not silent:
        print(f"{lib_name}/{file_name} not found", flush=True)
    return None


def check_venv_file_size(lib_name, file_name, min_file_size):
    """Crude way to check if this is already custom compiled library or downloaded binary. Return True if it should be built."""
    path = find_file_in_venv(lib_name, file_name, silent=True, recurse=True, startswith=True)
    if not path:
        return True
    return os.stat(path).st_size > min_file_size


def patch_soundcard():
    """
    Search for soundcard/mediafoundation.py in .venv
    Prepend "if _ole32: " to "_ole32.CoUninitialize()" line while respecting indentation
    Search for soundcard/pulseaudio.py in .venv
    replace assert with proper exception
    """
    fprint("Patching soundcard")
    if not os.path.exists(".venv"):
        print(".venv dir not found", flush=True)
        return

    # patch mediafoundation.py
    path = find_file_in_venv("soundcard", "mediafoundation.py")
    if not path:
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    pattern = re.compile(r"^(\s*)_ole32\.CoUninitialize\(\)")
    changed = False
    for num, line in enumerate(lines):
        match = re.match(pattern, line)
        if match:
            indent = match.group(1)
            lines[num] = f"{indent}if _ole32: _ole32.CoUninitialize()\n"
            changed = True
            break

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"Patched file: {path}", flush=True)
    else:
        print(f"Nothing to patch in file {path}", flush=True)

    # patch pulseaudio.py
    path = find_file_in_venv("soundcard", "pulseaudio.py")
    if not path:
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    pattern = re.compile(r"^(\s*)assert self\._pa_context_get_state")
    changed = False
    for num, line in enumerate(lines):
        match = re.match(pattern, line)
        if match:
            indent = match.group(1)
            lines[num] = f"{indent}if self._pa_context_get_state(self.context) != _pa.PA_CONTEXT_READY:\n"
            lines.insert(num+1, f'{indent+"    "}raise RuntimeError("PulseAudio context not ready (no sound system?)")\n')
            changed = True
            break

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"Patched file: {path}", flush=True)
    else:
        print(f"Nothing to patch in file {path}", flush=True)


def compress_emoji():
    """Compress emoji dict"""
    fprint("Compressing emoji data")
    json_path_in = os.path.join("endcord", "emoji.json")
    json_path_out = os.path.join("build", "emoji.json")
    if not os.path.exists(json_path_in):
        print("emoji.json not found", flush=True)
        return None
    if not os.path.exists("build"):
        os.mkdir("build")
    with open(json_path_in, "r", encoding="utf-8") as f:
        data = json.load(f)
    with open(json_path_out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=None, separators=(",", ":"))
    return json_path_out


def toggle_experimental(check_only=False):
    """Toggle experimental mode"""
    whitelist = ("endcord" + os.sep, "endcord_cython" + os.sep)
    file_list = []
    for path, subdirs, files in os.walk(os.getcwd()):
        subdirs[:] = [d for d in subdirs if not d.startswith(".")]
        for name in files:
            file_path = os.path.join(path, name)
            file_relpath = os.path.relpath(file_path)
            if not name.startswith(".") and (file_path.endswith(".py") or file_path.endswith(".pyx")) and any(x in file_relpath for x in whitelist):
                file_list.append(file_path)
    enable = False
    for path in file_list:
        # replace imports
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        changed = False
        for num, line in enumerate(lines):
            if line.startswith("import curses"):
                lines[num] = "from endcord import pgcurses as curses\n"
                changed = True
                enable = True
                break
            elif line.startswith("from endcord import pgcurses as curses"):
                lines[num] = "import curses\n"
                changed = True
                enable = False
                break
        if changed and not check_only:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
    if check_only:
        return not enable

    # backup cython binaries
    if enable:
        bins = get_cython_bins(directory="endcord_cython")
        if bins:
            for binary in bins:
                try:
                    importlib.import_module("endcord_cython." + binary.split(".")[0])
                except ImportError:
                    pass
            if "curses" in sys.modules:
                for file in bins:
                    old_name = os.path.join("endcord_cython", file)
                    new_name = os.path.join("endcord_cython", "bkp_" + file)
                    if os.path.exists(new_name):
                        os.remove(new_name)
                    os.rename(old_name, new_name)
    else:
        bins = get_cython_bins(directory="endcord_cython", startswith="bkp_")
        if bins:
            error = False
            for binary in bins:
                try:
                    importlib.import_module("endcord_cython." + binary.split(".")[0])
                except ImportError:
                    error = True
                    break
            if "curses" in sys.modules or error:
                for file in bins:
                    old_name = os.path.join("endcord_cython", file)
                    new_name = os.path.join("endcord_cython", file[4:])
                    if os.path.exists(new_name):
                        os.remove(new_name)
                    os.rename(old_name, new_name)

    # toggle dependencies
    experimental_dependencies = ["pygame-ce", "pyperclip", "pystray"]
    if sys.platform == "linux":
        experimental_dependencies += ["pygobject"]
    if enable:
        subprocess.run(["uv", "pip", "install"] + experimental_dependencies, check=True)
        fprint("Experimental windowed mode enabled!")
    else:
        subprocess.run(["uv", "pip", "uninstall"] + experimental_dependencies, check=True)
        fprint("Experimental windowed mode disabled!")
    return not enable


def enable_extensions(enable=True, check_only=False, silent=False):
    """"Enable/disable extensions support in the code"""
    path = "./endcord/app.py"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    changed = False
    for num, line in enumerate(lines):
        if line.startswith("ENABLE_EXTENSIONS = "):
            if "True" in line and enable:
                break
            elif "False" in line and not enable:
                break
            lines[num] = f"ENABLE_EXTENSIONS = {bool(enable)}\n"
            changed = True
            break
    if changed and not check_only:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    if not check_only and not silent:
        fprint(f"Extensions are {"enabled" if enable else "disabled"}!")


def setup_compiler(clang, clear=False, overwrite=False, cflags=[], ldflags=[], cxxflags=[], safe=False):
    """Set compiler and its flags in environment variables"""
    if clang:
        os.environ["CC"] = "clang"
        os.environ["CXX"] = "clang++"
        os.environ["LD"] = "lld"
    if clear:
        os.environ["CFLAGS"] = CFLAGS_OLD
        os.environ["CXXFLAGS"] = CXXFLAGS_OLD
        os.environ["LDFLAGS"] = LDFLAGS_OLD
        return [], [], []
    custom_cflags = [item for item in CUSTOM_CFLAGS if item not in UNSAFE_FLAGS] if safe else CUSTOM_CFLAGS
    cflags = ([] if overwrite else CFLAGS_OLD.split(" ")) + custom_cflags + cflags
    cxxflags = ([] if overwrite else CXXFLAGS_OLD.split(" ")) + CUSTOM_CXXFLAGS + cxxflags
    ldflags = ([] if overwrite else LDFLAGS_OLD.split(" ")) + CUSTOM_LDFLAGS + ldflags
    if shutil.which("lld") and clang:
        ldflags.append("-fuse-ld=lld")
    os.environ["CFLAGS"] = " ".join(cflags)
    os.environ["CXXFLAGS"] = " ".join(cxxflags)
    os.environ["LDFLAGS"] = " ".join(ldflags)
    return cflags, cxxflags, ldflags


def ensure_custom_python(safe, clang, curses):
    """Check if current python is custom built, setup env or build it if not"""
    minor = PYTHON_LAST_SAFE if safe else PYTHON_MAX_MINOR
    version = f"3.{minor}.{PYTHON_PATCH}"
    if importlib.util.find_spec("_bz2") is None:
        return
    if os.path.exists(".cpython") and os.path.exists(f".cpython/bin/python3.{version.split(".")[1]}"):
        if os.environ.get("UV", ""):
            if os.environ.get("_CUSTOM_PYTHON_CHECKED"):
                fprint("Failed starting custom python build, delete .cpython dir and try again")
                sys.exit(1)
            os.environ["_CUSTOM_PYTHON_CHECKED"] = "1"
            subprocess.run(["uv", "venv", "--clear", "--python", f".cpython/bin/python3.{minor}"], check=True)
        os.execvp("uv", ["uv", "run", *sys.argv])
        sys.exit(0)
    else:
        build_custom_python(version, clang, curses)


def build_custom_python(version, clang, curses):
    """Build custom Pyhon in .cpython dir"""
    fprint("Building custom Python")
    cmd = ["/bin/bash", "tools/build_python.sh", version, "clang" if clang else "None", "curses" if curses else "None"]
    if CURSES_TAG:
        cmd.append(CURSES_TAG)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    line = None
    first = True
    built_curses = not curses
    for line in process.stdout:
        if len(line) > 100:
            continue
        if line.startswith("Building curses"):
            print(line.strip().replace("_", "."))
        if line.startswith("Building Python"):
            built_curses = True
            print(line.strip())
        if not built_curses:
            continue
        elif "Resolving www.python.org" in line:
            print("Downloading Python source", flush=True)
        elif "checking build system type" in line:
            print("Configuring build system", flush=True)
        elif "Building with support for profile generation" in line:
            print("Compiling instrumented binaries", flush=True)
        elif "run the profile task to generate the profile information" in line:
            print("Running tests to generate profile data", flush=True)
        elif "Rebuilding with profile guided optimizations:" in line and first:
            first = False
            print("Rebuilding with profile guided optimizations", flush=True)
    process.wait()
    if process.returncode != 0:
        if line:
            print(line.strip(), flush=True)
        raise subprocess.CalledProcessError(process.returncode, cmd)


def build_numpy_lite(clang):
    """Build numpy without openblass to reduce final binary size"""
    if sys.platform != "linux":
        fprint("Skipping numpy lite (no openblas) building on non-linux platforms")
        return
    fprint("Building numpy-lite (no openblas)")
    check_openblas_cmd = [
        "uv", "run", "python", "-c",
        "import numpy; print(int(numpy.__config__.show_config('dicts')['Build Dependencies']['blas'].get('found', False)))",
    ]   # check if numpy without blas is not already installed
    value = subprocess.run(check_openblas_cmd, capture_output=True, text=True, check=False).stdout.strip()
    if not value or not int(value):
        print("Numpy-lite (no openblas) is already built", flush=True)
        return
    setup_compiler(clang)
    subprocess.run(["uv", "pip", "install", "pip"], check=True)   # because uv wont work with --config-settings as it should
    try:
        if sys.platform == "win32":
            python_interpreter = r".venv\Scripts\python.exe"
        else:
            python_interpreter = ".venv/bin/python"
        subprocess.run([python_interpreter, "-m", "pip", "uninstall", "--yes", "numpy"], check=True)
        subprocess.run([
            python_interpreter, "-m", "pip", "install", "numpy",
            "--no-cache-dir",
            "--no-binary=:all:",
            "--config-settings=setup-args=-Dblas=none",
            "--config-settings=setup-args=-Dlapack=none",
            "--config-settings=setup-args=-Dallow-noblas=true",
        ], check=True)
    except subprocess.CalledProcessError as e:   # fallback
        print(e, flush=True)
        print("Failed building numpy-lite (no openblas), faling back to default numpy", flush=True)
        subprocess.run(["uv", "pip", "install", "numpy"], check=True)
    value = subprocess.run(check_openblas_cmd, capture_output=True, text=True, check=False).stdout.strip()
    if value and int(value):
        print("Verification failed: numpy after building is still linked to openblas!", flush=True)
    subprocess.run(["uv", "pip", "uninstall", "pip"], check=True)


def build_package(package, clang, safe=False):
    """Build any python C compiled package with custom compiler args to reduce final binary size"""
    if sys.platform != "linux":
        return
    fprint(f"Building {package} with custom compiler args")
    setup_compiler(clang, safe=safe)
    subprocess.run(["uv", "pip", "install", "pip"], check=True)   # because uv wont work with --config-settings as it should
    try:
        if sys.platform == "win32":
            python_interpreter = r".venv\Scripts\python.exe"
        else:
            python_interpreter = ".venv/bin/python"
        subprocess.run([python_interpreter, "-m", "pip", "uninstall", "--yes", package], check=True)
        subprocess.run([python_interpreter, "-m", "pip", "install", "--no-cache-dir", "--no-binary=:all:", package], check=True)
    except subprocess.CalledProcessError as e:   # fallback
        print(e, flush=True)
        print(f"Failed building {package}, faling back to default prebuilt version", flush=True)
        subprocess.run(["uv", "pip", "install", package], check=True)
    subprocess.run(["uv", "pip", "uninstall", "pip"], check=True)


def build_cython(clang, mingw):
    """Build cython extensions"""
    clang = clang or os.environ.get("CC") == "clang"
    fprint(f"Compiling cython code with {"clang" if clang else "gcc"}{("mingw") if mingw else ""}")
    setup_compiler(clang)
    cmd = ["uv", "run", "python", "setup.py", "build_ext", "--inplace"]
    if mingw and sys.platform == "win32":
        cmd.append("--compiler=mingw32")   # covers mingw 32 and 64

    # run process with control of stdout
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in process.stdout:
        line_clean = line.rstrip("\n")
        if len(line_clean) < 100 and not any(s in line_clean for s in ("Cythonizing", "Compiling", "creating", "  warn(")):
            print(line_clean, flush=True)
    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)

    files = [f for f in os.listdir("endcord_cython") if f.endswith(".c")]
    for f in files:
        os.remove(os.path.join("endcord_cython", f))
    shutil.rmtree("build")


def build_with_pyinstaller(onedir, nosoundcard, print_cmd=False):
    """Build with pyinstaller"""
    if not print_cmd:
        if check_media_support():
            pkgname = PKGNAME
            fprint("Media support is enabled")
        else:
            pkgname = f"{PKGNAME}-lite"
            fprint("Media support is disabled")
        emoji_path = compress_emoji()
    else:
        pkgname = PKGNAME
        emoji_path = "endcord/emoji.json"

    mode = "--onedir" if onedir else "--onefile"
    hidden_imports = ["--hidden-import=uuid"]
    exclude_imports = [
        "--exclude-module=cython",
        "--exclude-module=zstandard",
    ]
    package_data = ["--collect-data=soundcard"]

    # options
    if nosoundcard:
        exclude_imports.append("--exclude-module=soundcard")
        package_data.remove("--collect-data=soundcard")

    # platform-specific
    if sys.platform == "linux":
        options = []
        hidden_imports += ["--hidden-import=soundcard.pulseaudio"]
        add_data = [f"--add-data={emoji_path}:."]
    elif sys.platform == "win32":
        options = ["--console"]
        hidden_imports += ["--hidden-import=win32timezone"]
        add_data = [f"--add-data={emoji_path};."]
    elif sys.platform == "darwin":
        options = []
        package_data += ["--collect-data=certifi"]
        add_data = [f"--add-data={emoji_path}:."]

    # prepare command and run it
    cmd = [
        "uv", "run", "python", "-m", "PyInstaller",
        mode,
        *hidden_imports,
        *exclude_imports,
        *package_data,
        *add_data,
        *options,
        "--noconfirm",
        "--clean",
        f"--name={pkgname}",
        "main.py",
    ]
    cmd = [arg for arg in cmd if arg != ""]
    if print_cmd:
        print(" ".join(cmd))
        sys.exit(0)
    fprint("Starting pyinstaller")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}", file=sys.stderr)
        sys.exit(e.returncode)

    # cleanup
    fprint("Cleaning up")
    try:
        os.remove(f"{pkgname}.spec")
        shutil.rmtree("build")
    except FileNotFoundError:
        pass
    fprint(f"Finished building {pkgname}")


def build_with_nuitka(onedir, clang, mingw, nosoundcard, compile_deps, print_cmd=False, experimental=False):
    """Build with nuitka"""
    clang = clang or os.environ.get("CC") == "clang"
    if not print_cmd:
        full = check_media_support()
        if full:
            pkgname = PKGNAME
            fprint("ASCII media support is enabled")
        else:
            pkgname = f"{PKGNAME}-lite"
            fprint("ASCII media support is disabled")

        if compile_deps:
            build_numpy_lite(clang)
            if check_venv_file_size("Crypto", "_chacha", 10000):
                build_package("pycryptodome", clang, safe=True)
            else:
                print("Pycryptodome is already compiled locally", flush=True)
            if full:
                if check_venv_file_size("pynacl", "_sodium.", 1000000):
                    build_package("pynacl", clang)
                else:
                    print("PyNaCl is already compiled locally", flush=True)
        patch_soundcard()
        emoji_path = compress_emoji()
    else:
        pkgname = PKGNAME
        emoji_path = "endcord/emoji.json"
    full = pkgname == PKGNAME
    static_python = False   # might be useful with custom python build

    mode = "standalone" if onedir else "onefile"
    compiler = ""
    if clang:
        compiler = "--clang"
    elif mingw:
        compiler = "--mingw64"
    python_flags = ["--python-flag=-OO"]
    hidden_imports = ["--include-module=uuid"]
    exclude_imports = [
        "--nofollow-import-to=cython",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=zstandard",
    ]
    package_data = ["--include-package-data=soundcard"]
    add_data = [f"--include-data-files={emoji_path}=emoji.json"]

    setup_compiler(clang)

    # options
    if nosoundcard:
        exclude_imports.append("--nofollow-import-to=soundcard")
        package_data.remove("--include-package-data=soundcard")
    if full:
        hidden_imports += [
            "--include-module=av.sidedata.encparams",
            "--include-module=av.utils",
        ]

    # platform-specific
    if sys.platform == "linux":
        options = []
        if experimental:
            options.append("--include-package=gi._enum")
            hidden_imports += ["--include-package=ctypes.util"]
    elif sys.platform == "win32":
        options = ["--assume-yes-for-downloads"]
        hidden_imports += [
            "--include-package=winrt.windows.foundation",
            "--include-package=winrt.windows.ui.notifications",
            "--include-package=winrt.windows.data.xml.dom",
            "--include-package=win32timezone",
        ]
        package_data += ["--include-package-data=winrt"]
    elif sys.platform == "darwin":
        options = [
            f"--macos-app-name={PKGNAME}",
            f"--macos-app-version={get_version_number()}",
            "--macos-app-protected-resource=NSMicrophoneUsageDescription:Microphone access for recording voice message.",
        ]
        package_data += ["--include-package-data=certifi:cacerts.pem"]

    # prepare command and run it
    cmd = [
        "uv", "run", "python", "-m", "nuitka",
        f"--mode={mode}",
        compiler,
        *python_flags,
        *hidden_imports,
        *exclude_imports,
        *package_data,
        *add_data,
        *options,
        "--static-libpython=yes" if static_python else "",
        "--no-deployment-flag=self-execution",   # -c and -m flags are safely handled by argparser
        "--no-prefer-source-code",
        "--remove-output",
        "--output-dir=dist",
        f"--output-filename={pkgname}",
        "main.py",
    ]
    cmd = [arg for arg in cmd if arg != ""]
    if print_cmd:
        print(" ".join(cmd))
        sys.exit(0)
    fprint("Starting nuitka")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}", file=sys.stderr)
        sys.exit(e.returncode)

    # cleanup
    fprint("Cleaning up")
    try:
        shutil.rmtree("build")
    except FileNotFoundError:
        pass
    fprint(f"Finished building {pkgname}")


def parser():
    """Setup argument parser for CLI"""
    parser = argparse.ArgumentParser(
        prog="build.py",
        description=f"build script for {PKGNAME}",
    )
    parser._positionals.title = "arguments"
    parser.add_argument(
        "--nuitka",
        action="store_true",
        help="build with nuitka, takes a long time, but more optimized executable",
    )
    parser.add_argument(
        "--noclang",
        action="store_true",
        help="script prefers clang if its installed, set this to not use it, or change CC and LD env vars",
    )
    parser.add_argument(
        "--lite",
        action="store_true",
        help="change environment to build or run endcord-lite, by deleting voice call and media support depenencies",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="build into directory instead single executable",
    )
    parser.add_argument(
        "--custom-python",
        action="store_true",
        help="build and use python with custom settings, will reduce final binary size, only for linux",
    )
    parser.add_argument(
        "--nocython",
        action="store_true",
        help="build without compiling cython code",
    )
    parser.add_argument(
        "--nocompile-deps",
        action="store_true",
        help="do not compile dependencies with custom compiler flags (compiled only in nuitka mode)",
    )
    parser.add_argument(
        "--nosoundcard",
        action="store_true",
        help="build without soundcard dependency, for super lightewight build, will enable lite mode, and notifications sound wont work unless pw-cat (pipewire) or paplay (pulseaudio) is installed on linux, and not at all on windows",
    )
    parser.add_argument(
        "--mingw",
        action="store_true",
        help="use mingw instead msvc on windows, has no effect on Linux and macOS or with --clang flag",
    )
    parser.add_argument(
        "--toggle-experimental",
        action="store_true",
        help="toggle experimental mode and exit",
    )
    parser.add_argument(
        "--freethreaded",
        action="store_true",
        help="build with freethreaded python, will noticeably improve terminal media player performance at the cost of much larger binary",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        help=f"Use python 3.{PYTHON_LAST_SAFE} which is known to build endcord without any issues",
    )
    parser.add_argument(
        "--nobuild",
        action="store_true",
        help="only configure environment, but dont build endcord",
    )
    parser.add_argument(
        "--disable-extensions",
        action="store_true",
        help="disable extensions support in the code, overriding option in the config",
    )
    parser.add_argument(
        "--print-cmd",
        action="store_true",
        help="print build command for nuitka or pyinstaller and exit, without configuring any environment",
    )
    parser.add_argument(
        "--build-licenses",
        action="store_true",
        help="build file containing licenses from all used third party libraries",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parser()
    clang = not (args.noclang or args.mingw)
    compile_deps = not args.nocompile_deps

    if args.nuitka:
        check_patchelf()

    if args.print_cmd:
        if args.nuitka:
            build_with_nuitka(args.onedir, clang, args.mingw, args.nosoundcard, print_cmd=True)
        else:
            build_with_pyinstaller(args.onedir, args.nosoundcard, print_cmd=True)
        sys.exit(0)

    if os.path.exists("build"):   # ensure clean build env
        shutil.rmtree("build")

    if args.custom_python:
        ensure_custom_python(args.safe, clang, compile_deps)

    if check_python():
        version, freethreaded = ensure_python(args.freethreaded, args.safe)
        if version:
            if freethreaded:
                force_ujson()
            os.execvp("uv", ["uv", "run", "-p", version, *sys.argv])
        else:
            os.execvp("uv", ["uv", "run", *sys.argv])
        sys.exit(0)

    if args.freethreaded:
        force_ujson()

    if args.toggle_experimental:
        toggle_experimental()
        sys.exit(0)
    if args.lite or args.nosoundcard:
        remove_media()
    else:
        add_media()

    if not args.nobuild:
        check_dev()

    experimental = toggle_experimental(check_only=True)
    if experimental:
        experimental_dependencies = ["pygame-ce", "pyperclip", "pystray"]
        if sys.platform == "linux":
            experimental_dependencies += ["pygobject"]
        subprocess.run(["uv", "pip", "install"] + experimental_dependencies, check=True)
        fprint("Experimental windowed mode enabled!")

    enable_extensions(enable=(not args.disable_extensions))

    if sys.platform not in ("linux", "win32", "darwin"):
        print(f"This platform is not supported: {sys.platform}", file=sys.stderr)
        sys.exit(1)

    if args.nocython:
        bins = get_cython_bins(directory="endcord_cython")
        for file in bins:
            os.remove(os.path.join("endcord_cython", file))
        fprint("Deleted compiled cython extensions")
    else:
        try:
            build_cython(clang, args.mingw)
        except Exception as e:
            fprint(f"Failed building cython extensions, error: {e}")

    if args.build_licenses:
        exclude = ["ordered-set", "zstandard", "altgraph", "packaging", "pyinstaller-hooks-contrib", "packaging", "setuptools"]
        build_third_party_licenses(exclude)

    if not args.nobuild:
        if args.nuitka:
            build_with_nuitka(args.onedir, clang, args.mingw, args.nosoundcard, compile_deps, experimental=experimental)
        else:
            build_with_pyinstaller(args.onedir, args.nosoundcard)

    enable_extensions(enable=True, silent=True)

    sys.exit(0)
