# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import os
import re
import shutil
import subprocess
import sys
import tomllib

VENV_DIR = ".venv"
if sys.platform == "win32":
    VENV_BIN = os.path.join(VENV_DIR, "Scripts")
    VENV_PYTHON = os.path.join(VENV_BIN, "python.exe")
else:
    VENV_BIN = os.path.join(VENV_DIR, "bin")
    VENV_PYTHON = os.path.join(VENV_BIN, "python")
HAS_UV = shutil.which("uv") is not None


def in_env():
    """Check if running inside env that build should use"""
    if HAS_UV:
        return bool(os.environ.get("UV"))
    return os.path.exists(VENV_DIR) and os.path.realpath(sys.prefix) == os.path.realpath(VENV_DIR)


def ensure_venv():
    """Create venv in project dir if it doesn't exist"""
    if not os.path.exists(VENV_PYTHON):
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)


def dep_name(dependency):
    """Normalize package name from requirement string"""
    return re.split(r"[=<>!~;@\[ ]", dependency, maxsplit=1)[0].lower().strip().replace("_", "-")


def group_deps(groups, name):
    """Get requirements of dependency group"""
    dependencies = []
    for item in groups.get(name, []):
        dependencies += [item] if isinstance(item, str) else group_deps(groups, item["include-group"])
    return dependencies


def sync(args, **kwargs):
    """Install dependencies from groups, uninstall oteher groups"""
    ensure_venv()
    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
        groups = data.get("dependency-groups", {})
        dependencies = list(data["project"].get("dependencies", []))

    selected = ["dev"] + [arg.removeprefix("--group=") for arg in args if arg.startswith("--group=")]
    for name in selected:
        dependencies += group_deps(groups, name)

    to_keep = [dep_name(dep) for dep in dependencies]
    to_remove = []
    for name in groups:
        if name not in selected:
            to_remove += [dep_name(dep) for dep in group_deps(groups, name) if dep_name(dep) not in to_keep]
    if to_remove:
        subprocess.run([VENV_PYTHON, "-m", "pip", "uninstall", "--yes", *to_remove], capture_output=True, check=False)

    return subprocess.run([VENV_PYTHON, "-m", "pip", "install", *dependencies], **kwargs)   # noqa


def remove(packages):
    """Remove packages from pyproject.toml and uninstall them"""
    ensure_venv()
    packages = [dep_name(name) for name in packages]

    with open("pyproject.toml", "r", encoding="utf-8") as f:
        text = f.read()
    dependencies = tomllib.loads(text).get("project", {}).get("dependencies", [])
    to_remove = tuple(f'"{x}"' for x in dependencies if dep_name(x) in packages)

    # may cause trouble with duplicate deps across groups
    remaining = [line for line in text.splitlines(keepends=True) if not line.strip().startswith(to_remove)]
    with open("pyproject.toml", "w", encoding="utf-8") as f:
        f.writelines(remaining)
    return subprocess.run([VENV_PYTHON, "-m", "pip", "uninstall", "--yes", *packages], capture_output=True, check=False)


def translate(cmd):
    """Translate single uv command to equivalent command that doesn't need uv"""
    args = cmd[1:]
    quiet = ["-q"] if args[0] == "-q" else []
    args = args[len(quiet):]

    if args[:1] == ["pip"]:
        ensure_venv()
        action, packages = args[1], args[2:]
        if packages == ["pip"]:   # skip pip
            return [sys.executable, "-c", ""]
        yes = ["--yes"] if action == "uninstall" else []
        return [VENV_PYTHON, "-m", "pip", action, *quiet, *yes, *packages]

    if args[:1] == ["run"]:
        ensure_venv()
        args = args[1:]
        args = args[1:] if args[0] == "--no-sync" else args
        if args[0].endswith(".py"):
            return [VENV_PYTHON, *args]
        return [shutil.which(args[0], path=VENV_BIN) or args[0], *args[1:]]

    if args[:1] == ["venv"]:
        python = args[args.index("--python") + 1] if "--python" in args else sys.executable
        clear = ["--clear"] if "--clear" in args else []
        return [python, "-m", "venv", *clear, VENV_DIR]

    raise FileNotFoundError(f"uv is not installed and cannot be replaced for: {" ".join(cmd)}")


def run(cmd, **kwargs):
    """Same as subprocess.run but translates uv commands if uv is missing"""
    if cmd[0] != "uv" or HAS_UV:
        return subprocess.run(cmd, **kwargs)   # noqa
    if cmd[1] == "sync":
        return sync(cmd[2:], **kwargs)
    if cmd[1] == "remove":
        return remove(cmd[2:])
    return subprocess.run(translate(cmd), **kwargs)   # noqa


def popen(cmd, **kwargs):
    """Same as subprocess.Popen but translates uv commands if uv is missing"""
    if cmd[0] == "uv" and not HAS_UV:
        cmd = translate(cmd)
    return subprocess.Popen(cmd, **kwargs)


def execvp(file, args):
    """Same as os.execvp but translates uv commands if uv is missing"""
    if file == "uv" and not HAS_UV:
        args = translate(args)
        file = args[0]
    os.execvp(file, args)
