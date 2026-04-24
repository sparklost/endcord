# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import json
import os

from endcord import peripherals


def hash_none(value):
    """Hash an integer value as a string and return it as a string, omitting None"""
    if value is None:
        return None
    return str(hash(str(value)))


def save_json(json_data, name, debug_path=True):
    """Save json to log path"""
    if debug_path:
        path = os.path.expanduser(os.path.join(peripherals.log_path, "Debug", name))
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
    else:
        path = name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)


def load_json(path):
    """Load json from any path"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
    return None


def anonymize_guilds(guilds):
    """
    Anonymize all sensitive data in guilds.
    hash: guild_id, id
    replace text: name
    remove: description, topic
    """
    anonymized = []
    for num, guild in enumerate(guilds):
        anonymized_channels = []
        for num_ch, channel in enumerate(guild["channels"]):
            if channel["type"] == 4:
                name = f"category_{num_ch}"
            else:
                name = f"channel_{num_ch}"
            if channel["parent_id"]:
                parent_id = hash_none(channel["parent_id"])
            else:
                parent_id = "NO DATA"
            anonymized_channels.append({
                "id": hash_none(channel["id"]),
                "type": channel["type"],
                "name": name,
                "topic": "",
                "parent_id": parent_id,
                "position": channel["position"],
                "message_notifications": channel.get("message_notifications", "NO DATA"),
                "muted": channel.get("muted", "NO DATA"),
                "hidden": channel.get("hidden", "NO DATA"),
                "collapsed": channel.get("collapsed", "NO DATA"),
            })
        anonymized.append({
            "guild_id": hash_none(guild["guild_id"]),
            "owned": guild["owned"],
            "name": f"guild_{num}",
            "description": "",
            "suppress_everyone": guild.get("suppress_everyone", "NO DATA"),
            "suppress_roles": guild.get("suppress_roles", "NO DATA"),
            "message_notifications": guild.get("message_notifications", "NO DATA"),
            "muted": guild.get("muted", "NO DATA"),
            "channels": anonymized_channels,
        })
    return anonymized


def anonymize_guild_folders(guild_folders):
    """
    Anonymize all sensitive data in guild_folders.
    hash: guild_id
    """
    anonymized = []
    for folder in guild_folders:
        guilds = []
        for guild in folder["guilds"]:
            guilds.append(hash_none(guild))
        anonymized.append({
            "id": folder["id"],
            "guilds": guilds,
        })
    return anonymized
