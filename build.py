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


def load_build_config():
    """Load build config from pyproject.toml"""
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        return data.get("build", {})
    print("pyproject.toml file not found", file=sys.stderr)
    sys.exit(1)


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


build_config = load_build_config()

PYTHON_MAJOR = int(build_config.get("python_max", "3.14.6").split(".")[0])
PYTHON_MAX_MINOR = int(build_config.get("python_max", "3.14.6").split(".")[1])
PYTHON_PATCH = int(build_config.get("python_max", "3.14.6").split(".")[2])
PYTHON_FREETHREADED = int(build_config.get("python_freethreaded", "3.14").split(".")[1])
PYTHON_LAST_SAFE = int(build_config.get("python_last_safe", "3.13").split(".")[1])
CURSES_TAG = build_config.get("curses_tag", "v6_6_20260627")
PKGNAME = get_app_name()
PKGVER = get_version_number()
USE_COLOR = supports_color()


def fprint(text, color=PURPLE, prefix=f"[{PKGNAME.capitalize()} Build Script]: ", file=sys.stdout):
    """Print colored text prefixed with text, default is light purple"""
    if USE_COLOR and color:
        print(f"{color}{prefix}{text}\033[0m", file=file, flush=True)
    else:
        print(f"{prefix}{text}", file=file, flush=True)


def iprint(text, indent=2, color=None):
    """Print inented low importance text belonging to build step"""
    if color:
        fprint(text, prefix=(indent * " "), color=color)
    else:
        print(f"{indent * " "}{text}", flush=True)


def backup_file(file_path, backup_path, overwrite=False):
    """Backup file by creating backup_path version of it"""
    if os.path.exists(backup_path):
        if overwrite:
            os.remove(backup_path)
        else:
            return
    shutil.copy2(file_path, backup_path)


def restore_file(file_path, backup_path):
    """Restore file from backup_path if its found"""
    if not os.path.exists(backup_path):
        return
    os.replace(backup_path, file_path)


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
            fprint(f"uv error: {e}", color_code=RED, prefix="", file=sys.stderr)
            return sys.version_info.major, sys.version_info.minor, not is_gil_enabled()
        all_parts = version_result.stdout.strip().split(" ")
        version_parts = all_parts[1].split(".")
        if len(version_parts) < 2:
            return sys.version_info.major, sys.version_info.minor, not is_gil_enabled()
        return int(version_parts[0]), int(version_parts[1]), "free-threading" in all_parts[2]
    return sys.version_info.major, sys.version_info.minor, not is_gil_enabled()


def get_nice_python_version():
    """Get clean python version"""
    version = sys.version
    start = version.find("(++")
    if start < 0:
        return version
    return version[:start] + version[version.find(")", start):]


def check_python():
    """Check python version and print warning, and return True if running inside pure python (no uv)"""
    if sys.version_info.major != 3:
        fprint(f"Python {sys.version_info.major} is not supported. Only Python 3 is supported.", color=RED, prefix="", file=sys.stderr)
        sys.exit(1)

    if os.environ.get("UV", ""):
        if sys.version_info.minor < 12 or sys.version_info.minor > PYTHON_MAX_MINOR:
            fprint(f'WARNING: Python {sys.version_info.major}.{sys.version_info.minor} is not supported but build may succeed. Run "python build.py" to let uv download and setup recommended temporary python interpreter.', color=RED)
        else:
            try:
                version = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True)
                fprint(f"Using {version.stdout.strip()}")
            except Exception:
                pass
            fprint(f"Using Python {get_nice_python_version()}")
        if not is_gil_enabled():
            if sys.version_info.minor == PYTHON_FREETHREADED:
                fprint("WARNING: While endcord works with freethreaded python, final binary is much larger. Nuitka doesnt yet support freethreaded python, so build is likely to fail.", color=RED)
            else:
                fprint(f'WARNING: Endcord is known to only build with freethreaded python version 3.{PYTHON_FREETHREADED}. Build is likely to fail on other versions. Run "python build.py" to let uv download and setup recommended temporary python interpreter, optionally with flag "--freethreaded".', color=RED)
        return False

    try:
        version = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        fprint(f"uv error: {e}", color=RED, prefix="", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        fprint("uv command not found, please ensure uv is installed and in PATH", color=RED, prefix="", file=sys.stderr)
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
        version = f"{PYTHON_MAJOR}.{PYTHON_FREETHREADED}+freethreaded"
    else:
        version = f"{PYTHON_MAJOR}.{selected_version}"
        # ensure there is no same-name freethreaded python
        subprocess.run(["uv", "python", "uninstall", f"{PYTHON_MAJOR}.{minor}+freethreaded"], check=False)

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
        fprint("Patchelf is required for building with nuitka. Please install it first.", color=RED)
        sys.exit(1)
    try:
        result = subprocess.run([patchelf_path, "--version"], capture_output=True, text=True, check=True)
        output = result.stdout.strip().lower()
        if not output.startswith("patchelf "):
            return
        if output.split(" ")[1].startswith("0.18."):
            fprint("Patchelf version 0.18.0 is a known buggy release, nuitka will likely refuse to use it! Please upgrade or downgrade it.", color=RED)
    except Exception:
        pass


def check_deps(*deps):
    """Check if specified dependencies are installed"""
    for dep in deps:
        if importlib.util.find_spec(dep) is None:
            return False
    return True


def setup_dependencies(level, set_dev):
    """Setup first stage of dependencies based on provided level"""
    restore_file("pyproject.toml", ".pyproject.toml.bak")

    if level == "FULL" and (not check_deps("av", "dave", "PIL", "nacl") or (set_dev and not check_deps("nuitka"))):
        subprocess.run(["uv", "sync", "--group=media"] + (["--group=build"] if set_dev else []), check=True)

    elif level == "MEDIUM" and (not check_deps("PIL") or check_deps("av") or (set_dev and not check_deps("nuitka"))):
        subprocess.run(["uv", "sync"] + (["--group=build"] if set_dev else []), check=True)
        with open("pyproject.toml", "rb") as f:
            media_deps = tomllib.load(f).get("dependency-groups", {}).get("media", {})
        medium_deps = load_build_config().get("medium_deps", [])
        for media_dep in media_deps:
            if any(x in media_dep for x in medium_deps):
                subprocess.run(["uv", "pip", "install", media_dep], check=True)

    elif level == "LITE" and (check_deps("PIL") or not check_deps("numpy") or (set_dev and not check_deps("nuitka"))):
        subprocess.run(["uv", "sync"] + (["--group=build"] if set_dev else []), check=True)

    elif level == "MINI":
        backup_file("pyproject.toml", ".pyproject.toml.bak")
        subprocess.run(["uv", "remove", *build_config.get("mini_exclude_deps")], check=True)
        if set_dev:
            subprocess.run(["uv", "sync", "--group=build"], check=True)
        fprint("WARNING: pyproject.toml is modified! Backup is '.pyproject.toml.bak'", color=RED)

    elif level == "MICRO":
        backup_file("pyproject.toml", ".pyproject.toml.bak")
        subprocess.run(["uv", "remove", *build_config.get("micro_exclude_deps")], check=True)
        if set_dev:
            subprocess.run(["uv", "sync", "--group=build"], check=True)
        fprint("WARNING: pyproject.toml is modified! Backup is '.pyproject.toml.bak'", color=RED)

    fprint(f"Environment configured to endcord-{level} with{"" if set_dev else "out"} build dependencies")


def force_ujson():
    """Remove orjson and force installing ujson instead"""
    subprocess.run(["uv", "-q", "pip", "uninstall", "orjson"], check=False, capture_output=True)
    subprocess.run(["uv", "-q", "pip", "install", "ujson"], check=True)
    fprint("Switched orjson -> ujson")


def build_third_party_licenses(exclude=[]):
    """Collect and build all licenses found in venv into THIRD_PARTY_LICENSES.txt file"""
    fprint("Building list of third party licenses")
    subprocess.run(["uv", "pip", "install", "pip-licenses"], check=True)
    command = [
        "uv", "run", "pip-licenses",
        "--ignore-packages", *exclude,
        "--format=plain-vertical",
        "--no-license-path",
        "--with-license-file",
        "--output-file=THIRD_PARTY_LICENSES.txt",
    ]
    subprocess.run(command, check=True)
    subprocess.run(["uv", "pip", "uninstall", "pip-licenses", "prettytable", "wcwidth"], check=True)
    shutil.rmtree("build")
    sys.exit(0)


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
        iprint(f"{lib_name}/{file_name} not found")
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
        iprint(".venv dir not found")
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
        iprint(f"Patched file: {path}")
    else:
        iprint(f"Nothing to patch in file {path}")

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
        iprint(f"Patched file: {path}")
    else:
        iprint(f"Nothing to patch in file {path}")


def compress_emoji():
    """Compress emoji dict"""
    fprint("Compressing emoji data")
    json_path_in = os.path.join("endcord", "emoji.json")
    json_path_out = os.path.join("build", "emoji.json")
    if not os.path.exists(json_path_in):
        iprint("emoji.json not found")
        return None
    if not os.path.exists("build"):
        os.makedirs("build", exist_ok=True)
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
    version = f"{PYTHON_MAJOR}.{minor}.{PYTHON_PATCH}"
    if not check_deps("_bz2"):
        return
    if os.path.exists(".cpython") and os.path.exists(f".cpython/bin/python{PYTHON_MAJOR}.{version.split(".")[1]}"):
        if os.environ.get("UV", ""):
            if os.environ.get("_CUSTOM_PYTHON_CHECKED"):
                fprint("Failed starting custom python build, delete .cpython dir and try again")
                sys.exit(1)
            os.environ["_CUSTOM_PYTHON_CHECKED"] = "1"
            subprocess.run(["uv", "venv", "--clear", "--python", f".cpython/bin/python{PYTHON_MAJOR}.{minor}"], check=True)
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
    downloading = True
    for line in process.stdout:
        # print(line.strip("\n"))
        if len(line) > 100:
            continue
        if not built_curses:
            if line.startswith("Building ncurses"):
                curses = line.strip().split(" ")[-1].replace("_", ".")
            elif downloading and "Length" in line and "[application/" in line:
                curses_size = line.split("(")[1].split(")")[0] if "(" in line else "unknown size"
                downloading = False
                iprint(f"Downloading ncurses-{curses}.tar.gz ({curses_size})")
            elif "checking build system type" in line:
                iprint("Compiling ncurses shared library")
            elif line.startswith("Building Python"):
                built_curses = True
                downloading = True
            continue
        elif downloading and "Length" in line and "[application/" in line:
            python_size = line.split("(")[1].split(")")[0] if "(" in line else "unknown size"
            downloading = False
            iprint(f"Downloading Python-{version}.tgz ({python_size})")
        elif "checking build system type" in line:
            iprint("Configuring build system")
        elif "Building with support for profile generation" in line:
            iprint("Compiling instrumented binaries")
        elif "run the profile task to generate the profile information" in line:
            iprint("Running tests to generate profile data")
        elif "Rebuilding with profile guided optimizations:" in line and first:
            first = False
            iprint("Recompiling with profile guided optimizations")
    process.wait()
    if process.returncode != 0:
        if line:
            iprint(line.strip())
        raise subprocess.CalledProcessError(process.returncode, cmd)


def run_pip_build_command(command):
    """Run pip command to build sdist package with selected printed lines"""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    line = None
    package_name = "package"
    for line in process.stdout:
        # print(line.strip("\n"))
        if "Downloading" in line:
            package_name = line.strip().split(" ")[1].split(".tar")[0]
            iprint(line.strip())
        elif "Preparing metadata" in line and "started" in line.lower():
            iprint(f"Building {package_name}")
        elif "Successfully installed" in line:
            iprint(line.strip())
    process.wait()
    if process.returncode != 0:
        if line:
            iprint(line.strip())
        raise subprocess.CalledProcessError(process.returncode, command)


def build_generic_package(package, clang, safe=False):
    """Build any python C compiled package with custom compiler args to reduce final binary size"""
    if sys.platform != "linux":
        return
    fprint(f"Building {package} with custom compiler args")
    setup_compiler(clang, safe=safe)
    subprocess.run(["uv", "-q", "pip", "install", "pip"], check=True)   # because uv wont work with --config-settings as it should
    try:
        python = ".venv/bin/python" if sys.platform != "win32" else r".venv\Scripts\python.exe"
        subprocess.run([python, "-m", "pip", "uninstall", "--yes", package], check=False, capture_output=True)
        run_pip_build_command([python, "-m", "pip", "install", "--no-cache-dir", "--no-binary=:all:", package])
    except subprocess.CalledProcessError as e:   # fallback
        iprint(e, flush=True)
        fprint(f"Failed building {package}, falling back to default prebuilt version", color=RED, prefix="")
        subprocess.run(["uv", "-q", "pip", "install", package], check=True)
    subprocess.run(["uv", "-q", "pip", "uninstall", "pip"], check=True)


def build_numpy_lite(clang):
    """Build numpy without openblass to reduce final binary size"""
    if sys.platform != "linux":
        fprint("Skipping numpy-lite (no openblas) building on non-linux platforms")
        return
    fprint("Building numpy-lite (no openblas) with custom compiler args")
    check_openblas_cmd = [
        "uv", "run", "python", "-c",
        "import numpy; print(int(numpy.__config__.show_config('dicts')['Build Dependencies']['blas'].get('found', False)))",
    ]   # check if numpy without blas is not already installed
    value = subprocess.run(check_openblas_cmd, capture_output=True, text=True, check=False).stdout.strip()
    if not value or not int(value):
        iprint("Numpy-lite (no openblas) is already built locally")
        return
    setup_compiler(clang)
    subprocess.run(["uv", "-q", "pip", "install", "pip"], check=True)   # because uv wont work with --config-settings as it should
    try:
        python = ".venv/bin/python" if sys.platform != "win32" else r".venv\Scripts\python.exe"
        subprocess.run([python, "-m", "pip", "uninstall", "--yes", "numpy"], check=False, capture_output=True)
        run_pip_build_command([
            python, "-m", "pip", "install", "numpy",
            "--no-cache-dir",
            "--no-binary=:all:",
            "--config-settings=setup-args=-Dblas=none",
            "--config-settings=setup-args=-Dlapack=none",
            "--config-settings=setup-args=-Dallow-noblas=true",
        ])
    except subprocess.CalledProcessError as e:   # fallback
        print(e, flush=True)
        fprint("Failed building numpy-lite, faling back to default numpy", color=RED, prefix="")
        subprocess.run(["uv", "-q", "pip", "install", "numpy"], check=True)
    value = subprocess.run(check_openblas_cmd, capture_output=True, text=True, check=False).stdout.strip()
    if value and int(value):
        iprint("Verification failed: numpy after building is still linked to openblas!", color=RED)
    subprocess.run(["uv", "-q", "pip", "uninstall", "pip"], check=True)


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
        if len(line_clean) < 100 and not any(s in line_clean for s in ("Cythonizing", "Compiling", "creating", "  warn(", "build_ext")):
            fprint(line_clean.capitalize(), flush=True)
    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)

    files = [f for f in os.listdir("endcord_cython") if f.endswith(".c")]
    for f in files:
        os.remove(os.path.join("endcord_cython", f))
    shutil.rmtree("build")


def build_with_pyinstaller(level, onedir, print_cmd=False):
    """Build with pyinstaller"""
    pkgname = PKGNAME if level == "FULL" else f"{PKGNAME}-{level.lower()}"
    emoji_path = compress_emoji() if not print_cmd else "endcord/emoji.json"
    mode = "--onedir" if onedir else "--onefile"
    hidden_imports = ["--hidden-import=uuid"]
    exclude_imports = [
        "--exclude-module=cython",
        "--exclude-module=zstandard",
    ]
    package_data = []
    if level not in ("MINI", "MICRO"):
        package_data += ["--collect-data=soundcard"]

    # platform-specific
    if sys.platform == "linux":
        options = []
        if level not in ("MINI", "MICRO"):
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
        fprint(f"Build failed: {e}", color=RED, prefix="", file=sys.stderr)
        sys.exit(e.returncode)

    # cleanup
    fprint("Cleaning up")
    try:
        os.remove(f"{pkgname}.spec")
        shutil.rmtree("build")
    except FileNotFoundError:
        pass
    fprint(f"Finished building {pkgname}")


def build_with_nuitka(level, onedir, clang, mingw, compile_deps, print_cmd=False, experimental=False):
    """Build with nuitka"""
    clang = clang or os.environ.get("CC") == "clang"
    pkgname = PKGNAME if level == "FULL" else f"{PKGNAME}-{level.lower()}"
    emoji_path = compress_emoji() if not print_cmd else "endcord/emoji.json"
    if not print_cmd:
        if compile_deps and level not in ("MINI", "MICRO"):
            build_numpy_lite(clang)
            if check_venv_file_size("Crypto", "_chacha", 10000):
                build_generic_package("pycryptodome", clang, safe=True)
            else:
                fprint("Building pycryptodome with custom compiler args")
                iprint("Pycryptodome is already built locally")
            if level == "FULL":
                if check_venv_file_size("pynacl", "_sodium.", 1000000):
                    fprint("Building pycryptodome with custom compiler args")
                    build_generic_package("pynacl", clang)
                else:
                    iprint("PyNaCl is already built locally")
        patch_soundcard()
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
    package_data = []
    add_data = [f"--include-data-files={emoji_path}=emoji.json"]

    setup_compiler(clang)

    # options
    if level == "FULL":
        hidden_imports += [
            "--include-module=av.sidedata.encparams",
            "--include-module=av.utils",
        ]
    if level not in ("MINI", "MICRO"):
        package_data += ["--include-package-data=soundcard"]

    # platform-specific
    if sys.platform == "linux":
        options = []
        if experimental:
            options += ["--include-package=gi._enum"]
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
        fprint(f"Build failed: {e}", color=RED, prefix="", file=sys.stderr)
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
        formatter_class=argparse.RawTextHelpFormatter,
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
        "--level",
        type=str,
        default="FULL",
        choices=["FULL", "MEDIUM", "LITE", "MINI", "MICRO"],
        help=(
            'Change environment to build a specified level of endcord.\n'
            'Options:\n'
            '  "FULL"   - Has media and voice call support.\n'
            '  "MEDIUM" - No media and voice call support, but can display images.\n'
            '  "LITE"   - No image, media, or voice call support.\n'
            '  "MINI"   - Like LITE minus sound unless paplay/pw-cat commands are available and no voice recording\n'
            '  "MICRO"  - Max compatibility on legacy/weird systems. Like MICRO minus QR code and email login.'
        ),
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
        help=f"Use python 3.{PYTHON_LAST_SAFE} which is known to build endcord without any issues (disables --custom-python)",
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
            build_with_nuitka(args.level, args.onedir, clang, args.mingw, print_cmd=True)
        else:
            build_with_pyinstaller(args.level, args.onedir, print_cmd=True)
        sys.exit(0)

    if os.path.exists("build"):   # ensure clean build env
        shutil.rmtree("build")

    if args.custom_python:
        ensure_custom_python(args.safe, clang, compile_deps)

    if check_python():
        version, freethreaded = ensure_python(args.freethreaded, args.safe)
        if version:
            os.execvp("uv", ["uv", "run", "-p", "--no-sync", version, *sys.argv] + (["--freethreaded"] if freethreaded else []))
        else:
            os.execvp("uv", ["uv", "run", "--no-sync", *sys.argv])
        sys.exit(0)

    if args.freethreaded:
        force_ujson()

    if args.toggle_experimental:
        toggle_experimental()
        sys.exit(0)
    setup_dependencies(args.level, not args.nobuild)

    experimental = toggle_experimental(check_only=True)
    if experimental:
        experimental_dependencies = ["pygame-ce", "pyperclip", "pystray"]
        if sys.platform == "linux":
            experimental_dependencies += ["pygobject"]
        subprocess.run(["uv", "pip", "install"] + experimental_dependencies, check=True)
        fprint("Experimental windowed mode enabled!")

    enable_extensions(enable=(not args.disable_extensions))

    if sys.platform not in ("linux", "win32", "darwin"):
        fprint(f"This platform is not supported: {sys.platform}", color=RED, prefix="", file=sys.stderr)
        sys.exit(1)

    if args.nocython:
        bins = get_cython_bins(directory="endcord_cython")
        for file in bins:
            os.remove(os.path.join("endcord_cython", file))
        fprint("Deleted compiled cython extensions")
    elif not args.nobuild:
        try:
            build_cython(clang, args.mingw)
        except Exception as e:
            fprint(f"Failed building cython extensions, error: {e}")

    if args.build_licenses:
        exclude = ["cython", "altgraph", "packaging", "pyinstaller", "pyinstaller-hooks-contrib", "packaging", "setuptools"]
        build_third_party_licenses(exclude)

    if not args.nobuild:
        if args.nuitka:
            build_with_nuitka(args.level, args.onedir, clang, args.mingw, compile_deps, experimental=experimental)
        else:
            build_with_pyinstaller(args.level, args.onedir)

    enable_extensions(enable=True, silent=True)

    sys.exit(0)
