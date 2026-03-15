import os
import sys
import tomllib

extensions_white = [".py", ".ini"]
extensions_black = [".pyc"]


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


def get_file_list():
    """Get list of all files with extensions from extensions_white"""
    file_list = []
    for path, subdirs, files in os.walk(os.getcwd()):
        subdirs[:] = [d for d in subdirs if not d.startswith(".")]   # skip hidden dirs
        for name in files:
            file_path = os.path.join(path, name)
            if any(name.endswith(x) for x in extensions_white) and not name.startswith("."):
                if not any(name.endswith(x) for x in extensions_black):
                    file_list.append(file_path)
    return file_list


def main():
    """Update versions in all files"""
    print("Running version update script")
    version = get_version_number()
    file_list = get_file_list()
    any_updated = False

    for path in file_list:
        if "update_version.py" not in path:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            changed = False
            for num, line in enumerate(lines):
                if line.startswith("VERSION = ") and line.split("VERSION = ")[-1] != f'"{version}"\n':
                    lines[num] = f'VERSION = "{version}"\n'
                    changed = True
                    break

            if changed:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                print(f"Version number updated in: {path}")
                any_updated = True

    if any_updated:
        print(f"New version: {version}")
    else:
        print("Version in all files is already the latest")
    sys.exit(0)

if __name__ == "__main__":
    main()
