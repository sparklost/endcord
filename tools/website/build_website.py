# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import argparse
import os
import shutil

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WEBSITE_DIR = os.path.join(ROOT, "tools", "website")
ICON = os.path.join(ROOT, "tools", "icons", "endcord.png")
SCRIPT_TAG = '<script type="mpy" src="website.py"></script>'


def build(version, output, inline, bundle):
    """Build the website into output folder and bundle files"""
    with open(os.path.join(WEBSITE_DIR, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(WEBSITE_DIR, "website.py"), "r", encoding="utf-8") as f:
        script = f.read()

    # build website
    os.makedirs(output, exist_ok=True)
    html = html.replace("{%VERSION}", version.strip("v"))
    if inline:
        html = html.replace(SCRIPT_TAG, f'<script type="mpy">\n{script.rstrip()}\n</script>')
    else:
        shutil.copyfile(os.path.join(WEBSITE_DIR, "website.py"), os.path.join(output, "website.py"))
    with open(os.path.join(output, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    shutil.copyfile(ICON, os.path.join(output, "endcord.png"))

    # bundle markdown
    bundle_files = [
        (os.path.join(ROOT, "README.md"), "README.md"),
        (os.path.join(ROOT, ".github", "CONTRIBUTING.md"), "github/CONTRIBUTING.md"),
    ]
    for root, dirs, files in os.walk(os.path.join(ROOT, "docs")):
        for file in files:
            if file.endswith(".md"):
                path = os.path.join(root, file)
                bundle_files.append((path, os.path.relpath(path, ROOT)))
    bundle = os.path.join(output, "repo")
    if os.path.exists(bundle):
        shutil.rmtree(bundle)
    for src, name in bundle_files:
        destination = os.path.join(bundle, name)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(src, destination)

    print(f"Built endcord website {version} in {output} (inline: {inline}, bundled: {len(bundle_files)})")


def parser():
    """Setup argument parser for CLI"""
    parser = argparse.ArgumentParser(
        prog="build_website.py",
        description="build script for endcord website",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False,
    )
    parser.suggest_on_error = True
    parser.add_argument(
        "version",
        type=str,
        help="app version shown on the website",
    )
    parser._positionals.title = "arguments"
    parser.add_argument(
        "--output",
        type=str,
        default="build/website",
        help="output directory",
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="merge website.py into index.html",
    )
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="bundle README and docs markdown files with the website",
    )
    parser.add_argument(
        "-h", "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="show this help message and exit",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parser()
    build(args.version, args.output, args.inline, args.bundle)
