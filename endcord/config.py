# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import logging
import os
import re
import sys
from ast import literal_eval
from configparser import ConfigParser

from endcord import color, defaults, peripherals

logger = logging.getLogger(__name__)

XTERM_LIKE_BINDINGS_TERMS = ("xterm", "foot")


def save_config(path, data, section):
    """Save config section"""
    path = os.path.expanduser(path)
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    config = ConfigParser(interpolation=None)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config.read_file(f)
    if not config.has_section(section):
        config.add_section(section)
    for key in data:
        if data[key] in (True, False, None) or isinstance(data[key], (list, tuple, int, float)):
            config.set(section, key, str(data[key]))
        else:
            config.set(section, key, f'"{str(data[key]).replace("\\", "\\\\").replace("\n", "\\n")}"')
    with open(path, "w", encoding="utf-8") as f:
        config.write(f)


def load_config(path, default, section="main", gen_config=False, merge=False):
    """
    Load settings and theme from config
    If some value is missing, it is replaced with default value
    """
    if not path:
        path = os.path.join(peripherals.config_path, "config.ini")
    path = os.path.expanduser(path)

    if not os.path.exists(path) or gen_config:
        save_config(path, default, section)
        if not gen_config:
            print(f"Default config generated at: {path}")
        config_data = default
    else:
        config = ConfigParser(interpolation=None)
        with open(path, "r", encoding="utf-8") as f:
            config.read_file(f)
        if not config.has_section(section):
            return default
        config_data_raw = config._sections[section]
        config_data = dict.fromkeys(default)
        for key in default:
            if key in list(config[section].keys()):
                try:
                    eval_value = literal_eval(config_data_raw[key])
                    if section == "theme":
                        eval_value = parse_color(eval_value)
                    config_data[key] = eval_value
                except ValueError:
                    config_data[key] = config_data_raw[key]
            else:
                config_data[key] = default[key]
        for key, value in config_data_raw.items():
            if key.startswith("ext_") or merge:
                try:
                    eval_value = literal_eval(value)
                    if section == "theme":
                        eval_value = parse_color(eval_value)
                    config_data[key] = eval_value
                except ValueError:
                    config_data[key] = value
    return config_data


def get_themes():
    """Return list of all themes found in Themes directory"""
    themes_path = os.path.expanduser(os.path.join(peripherals.config_path, "Themes"))
    if not os.path.exists(themes_path):
        os.makedirs(themes_path, exist_ok=True)
    themes = []
    for file in os.listdir(themes_path):
        if file.endswith(".ini"):
            themes.append(os.path.join(themes_path, file))
    return themes


def merge_configs(custom_config_path, theme_path):
    """Merge config and themes, from various locations"""
    gen_config = False
    error = None
    if not custom_config_path:
        default_config_path = os.path.expanduser(os.path.join(peripherals.config_path, "config.ini"))
        if not os.path.exists(default_config_path):
            logger.info("Using default config")
            gen_config = True
        custom_config_path = default_config_path
    elif not os.path.exists(os.path.expanduser(custom_config_path)):
        gen_config = True
    config = load_config(custom_config_path, defaults.settings)
    config["config_path"] = custom_config_path
    if not theme_path and config["theme"]:
        theme_path = os.path.expanduser(config["theme"])
    saved_themes = get_themes()
    theme = load_config(custom_config_path, defaults.theme, section="theme", gen_config=gen_config)
    theme["theme_path"] = None
    if theme_path:
        # if path is only file name without extension
        if os.path.splitext(os.path.basename(theme_path))[0] == theme_path:
            for saved_theme in saved_themes:
                if os.path.splitext(os.path.basename(saved_theme))[0] == theme_path:
                    theme_path = saved_theme
                    break
            else:
                error = f'Theme "{theme_path}" not found in themes directory.'
        if not error:
            theme_path = os.path.expanduser(theme_path)
            theme = load_config(theme_path, theme, section="theme")
            theme["theme_path"] = theme_path
    config.update(theme)
    return config, gen_config, error


def parse_color(data):
    """Automatically parse (r, g, b) and '#123abc' color formats and convert to 8-bit ansi"""
    if isinstance(data, list):
        for i, value in enumerate(data):
            data[i] = parse_color(value)
    if isinstance(data, int):   # already ansi
        return data
    if isinstance(data, tuple) and len(data) == 3:   # rgb tuple
        return color.closest_color(data)[0]
    if isinstance(data, str) and data.startswith("#"):   # hex string
        return color.closest_color(color.hex_to_rgb(data))[0]
    return data


def alt_shift(value, shift):
    """Try to change "ALT+Key into integer key value"""
    val = re.sub(r"ALT\+(\d+)", lambda m: str(int(m.group(1)) + shift), value)
    try:
        return int(val)
    except ValueError:
        return val


def convert_keybindings(keybindings):
    """Convert keybinding codes to os-specific codes"""
    if sys.platform == "win32":   # windows has different codes for Alt+Key
        shift = 320
        swap_backspace = True   # config - 8 (ctrl+backspace) with real - 263 (backspace)
    elif os.environ.get("TERM", "") == "xterm":   # xterm has different codes for Alt+Key
        shift = 64
        swap_backspace = True
        # for ALT+Key it actually sends 195 then Key+64
        # but this is simpler since key should already be uniquely shifted
    else:
        return keybindings

    for key, value in keybindings.items():
        if isinstance(value, str):
            keybindings[key] = alt_shift(value, shift)
        elif swap_backspace and value == 8:
            keybindings[key] = 263

    return keybindings


def convert_keybindings_cmd(keybindings):
    """Convert keybinding codes to os-specific codes, for command bindings"""
    if sys.platform == "win32":
        shift = 320
    elif os.environ.get("TERM", "") in XTERM_LIKE_BINDINGS_TERMS:
        shift = 64
    else:
        shift = 0

    new_keybindings = {}
    for key, value in keybindings.items():
        new_key = key.replace('"', "")
        if isinstance(new_key, str) and "alt" in new_key:
            new_key = new_key.replace("alt", "ALT")
        if isinstance(new_key, str) and shift:
            new_key = alt_shift(new_key, shift)
        else:
            try:
                new_key = int(new_key)
            except ValueError:
                pass
        new_keybindings[new_key] = value

    return new_keybindings


def normalize_keybindings(keybindings):
    """Ensure all keybindings are tuples"""
    for key in keybindings:
        if not isinstance(keybindings[key], tuple):
            keybindings[key] = (keybindings[key], )
    return keybindings


def deduplicate_keybindings(keybindings_a, keybindings_b, command=False):
    """Deduplicate 2 keubinding dicts that can have strings and tuples of strings as values, keeping keybindings_b"""
    def deduplicate_value(dedupe_value, keybindings):
        for key, values in keybindings.items():
            if isinstance(values, tuple):
                for num, value in enumerate(values):
                    if value == dedupe_value:
                        fresh_values = keybindings[key]
                        keybindings[key] = fresh_values[:num] + (None,) + fresh_values[num + 1:]
            elif values == dedupe_value:
                keybindings[key] = None

    def dedupe_value_command(dedupe_value, keybindings):
        for key, value in keybindings.items():
            if key == str(dedupe_value):
                del keybindings[key]

    if command:
        deduplicate_value = dedupe_value_command

    for key, values in keybindings_b.items():
        if isinstance(values, tuple):
            for value in values:
                deduplicate_value(value, keybindings_a)
        else:
            deduplicate_value(values, keybindings_a)


def merge_keybindings(keybindings, vim_keybindings, command_bindings):
    """Merge standard and vim mode keybindings and remove keybinding collisions favoring vim keybindings"""
    deduplicate_keybindings(keybindings, vim_keybindings)
    deduplicate_keybindings(command_bindings, vim_keybindings, command=True)

    for key, value_2_old in vim_keybindings.items():
        if isinstance(value_2_old, str) and len(value_2_old) == 1:
            try:
                value_2 = ord(value_2_old)
            except TypeError:
                value_2 = value_2_old
        else:
            value_2 = value_2_old

        if key not in keybindings:
            keybindings[key] = value_2
        else:
            value_1 = keybindings[key]
            if not isinstance(value_1, tuple):
                value_1 = (value_1, )
            if not isinstance(value_2, tuple):
                value_2 = (value_2, )
            if value_1[0] is None:
                value_1 = ()
            keybindings[key] = value_1 + value_2

    return keybindings


def update_config(config, key, value):
    """Update and save config"""
    if not value:
        value = ""
    else:
        try:
            value = literal_eval(value)
        except ValueError:
            pass
    config[key] = value
    config_path = config["config_path"]
    saved_config = ConfigParser(interpolation=None)
    if os.path.exists(config_path):
        with open(os.path.expanduser(config_path), "r", encoding="utf-8") as f:
            saved_config.read_file(f)
    new_config = {}
    new_theme = {}
    # split config and theme
    for key_all, value_all in config.items():
        if key_all in defaults.settings:
            new_config[key_all] = value_all
        elif key_all in defaults.theme:
            new_theme[key_all] = value_all
    save_config(config_path, new_config, "main")
    save_config(config_path, new_theme, "theme")
    return config
