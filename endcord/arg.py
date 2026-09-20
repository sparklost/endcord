# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import argparse
import sys


class VersionAction(argparse.Action):
    """Custom formatter to handle newlines"""
    def __init__(self, option_strings, version=None, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help="show program's version number and exit"):   #noqa
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)
        self.version = version

    def __call__(self, parser, namespace, values, option_string=None):   #noqa
        print(self.version)
        sys.exit(0)


def parser(app_name, version, default_config_path, log_path, level):
    """Setup argument parser for CLI"""
    parser = argparse.ArgumentParser(
        prog=app_name,
        description="Feature rich Discord client in terminal using ncurses",
        add_help=False,
    )
    parser.suggest_on_error = True
    parser._positionals.title = "arguments"
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        action="store",
        help=f"custom path to config file; If file does not exist, \
        config with defaults will be created; default config is in {default_config_path}",
    )
    parser.add_argument(
        "-e",
        "--theme",
        type=str,
        action="store",
        help="custom path to theme file; if file does not exist, theme with defaults will be created",
    )
    parser.add_argument(
        "-a",
        "--manager",
        action="store_true",
        help="launch profile manager",
    )
    parser.add_argument(
        "-k",
        "--keybinding",
        action="store_true",
        help="launch keybinding resolver",
    )
    parser.add_argument(
        "-o",
        "--colors",
        action="store_true",
        help="show all available colors and their codes",
    )
    parser.add_argument(
        "-u",
        "--vumeter",
        action="store_true",
        help="launch vumeter used to configure voice call silence detection",
    )
    parser.add_argument(
        "-i",
        "--install-extension",
        type=str,
        action="store",
        help="git url to extension to install (or just owner/repo for github)",
    )
    parser.add_argument(
        "-p",
        "--profile",
        type=str,
        action="store",
        help="Name of profile to load, profiles are managed in profile manager",
    )
    parser.add_argument(
        "-t",
        "--token",
        type=str,
        action="store",
        help="Discord user authentication token, it is recommended to provide it in profile manager",
    )
    parser.add_argument(
        "-m",
        "--media",
        type=str,
        action="store",
        help="\
        local path to media file or youtube url; \
        if provided, will play it without starting endcord discord",
    )
    parser.add_argument(
        "-s",
        "--host",
        type=str,
        action="store",
        help="custom host to connect to; overrides custom_host in config",
    )
    parser.add_argument(
        "-x",
        "--proxy",
        type=str,
        action="store",
        help="proxy URL to use, it must be this format: 'protocol://host:port'; \
        supported proxy protocols: http, socks5; using proxy might make you more suspicious to discord",
    )
    parser.add_argument(
        "-n",
        "--headless",
        action="store_true",
        help="run in headless mode, UI will not be drawn",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help=f"add extra debug entries in log file; log is always overwritten and saved to {log_path}",
    )
    parser.add_argument(
        "-h", "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="show this help message and exit",
    )
    parser.add_argument(
        "-v",
        "--version",
        action=VersionAction,
        version=(f"{app_name} ({level}) {version}\n\n"
            f"Copyright (C) 2025-2026 SparkLost. All Rights Reserved.\n"
            f"Source-available under the Endcord License. See LICENSE for terms.\n"
            f"Redistribution of modified versions is not permitted."),
        help=f"display the {app_name} version and exit",
    )
    return parser.parse_args()
