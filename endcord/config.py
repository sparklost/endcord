# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import logging
import os
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
    Load settings and theme from config.
    If some value is missing, it is replaced with default value.
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
                        eval_value = color.parse_color(eval_value)
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
                        eval_value = color.parse_color(eval_value)
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


def load_keybindings(path, default, section="keybindings", gen_config=False, merge=False):
    """
    Load keybindings from config with special handling.
    If some value is missing, it is replaced with default value.
    """
    if not path:
        path = os.path.join(peripherals.config_path, "config.ini")
    path = os.path.expanduser(path)

    if not os.path.exists(path) or gen_config:
        default_ready = {}
        for key, value in default.items():
            if value and isinstance(value[0], tuple):
                default_ready[key] = tuple(item[0] for item in value)
            else:
                default_ready[key] = value[0]
        save_config(path, default_ready, section)
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
                    if isinstance(default[key], tuple) or isinstance(default[key], list):
                        check_id = default[key][1]
                        binding_id = check_id if (not check_id or isinstance(check_id, int)) else default[key][0][1]
                        if isinstance(eval_value, list) or isinstance(eval_value, list):
                            config_data[key] = tuple((x, binding_id) for x in eval_value)
                        elif isinstance(eval_value, int):
                            config_data[key] = default[key]
                        else:
                            config_data[key] = (str(eval_value), binding_id)
                    elif isinstance(eval_value, int):
                        config_data[key] = default[key]
                    else:
                        config_data[key] = eval_value
                except ValueError:
                    config_data[key] = config_data_raw[key]
            else:
                config_data[key] = default[key]
        if merge:
            for key, value in config_data_raw.items():
                try:
                    eval_value = literal_eval(value)
                    config_data[key] = eval_value
                except ValueError:
                    config_data[key] = value
    return config_data


def normalize_keybindings(keybindings):
    """Ensure all keybindings are tuples"""
    for key in keybindings:
        if not isinstance(keybindings[key], tuple):
            keybindings[key] = (keybindings[key], )
    return keybindings


def deduplicate_keybindings(keybindings_a, keybindings_b, command=False):
    """Deduplicate 2 keybinding dicts that can have strings and tuples of strings inside a first element of tuple as values, keeping keybindings_b"""
    def deduplicate_value(dedupe_value, keybindings):
        for key, outer_tuple in keybindings.items():
            if not isinstance(outer_tuple, tuple) or not outer_tuple:
                continue
            values = outer_tuple[0]
            if isinstance(values, tuple):
                for num, value in enumerate(values):
                    if value == dedupe_value:
                        fresh_values = values[:num] + (None,) + values[num + 1:]
                        keybindings[key] = (fresh_values,) + outer_tuple[1:]
            elif values == dedupe_value:
                keybindings[key] = (None,) + outer_tuple[1:]

    def dedupe_value_command(dedupe_value, keybindings):
        for key in list(keybindings.keys()):
            if key == str(dedupe_value):
                del keybindings[key]

    if command:
        deduplicate_value = dedupe_value_command

    for key, outer_tuple in keybindings_b.items():
        item_to_check = key if command else (outer_tuple[0] if isinstance(outer_tuple, tuple) and outer_tuple else None)
        if item_to_check is None:
            continue
        if isinstance(item_to_check, tuple):
            for value in item_to_check:
                deduplicate_value(value, keybindings_a)
        else:
            deduplicate_value(item_to_check, keybindings_a)


def merge_keybindings(keybindings, vim_keybindings, command_bindings):
    """Merge standard and vim mode keybindings and remove keybinding collisions favoring vim keybindings"""
    deduplicate_keybindings(keybindings, vim_keybindings)
    deduplicate_keybindings(command_bindings, vim_keybindings, command=True)

    for key, value_2 in vim_keybindings.items():
        if key not in keybindings:
            keybindings[key] = value_2
        else:
            value_1 = keybindings[key]
            if not isinstance(value_1, tuple) or not value_1:
                value_1 = ((),)
            if not isinstance(value_2, tuple) or not value_2:
                value_2 = ((),)   # noqa
            inner_1 = value_1[0] if isinstance(value_1[0], tuple) else ((value_1[0],) if value_1[0] is not None else ())
            inner_2 = value_2[0] if isinstance(value_2[0], tuple) else ((value_2[0],) if value_2[0] is not None else ())
            merged_inner = inner_1 + inner_2
            extra_data = value_2[1:] if len(value_2) > 1 else (value_1[1:] if len(value_1) > 1 else ())
            keybindings[key] = (merged_inner,) + extra_data

    return keybindings
