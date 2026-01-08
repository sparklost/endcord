import heapq
import importlib.util
import re

import emoji

COMMAND_OPT_TYPE = ("subcommand", "group", "string", "integer", "True/False", "user ID", "channel ID", "role ID", "mentionable ID", "number", "attachment")


def fuzzy_match_score_single(query, candidate):
    """
    Calculates score for fuzzy matching of single query word.
    Consecutive matches will have larger score.
    Matches closer to the start of the candidate string will have larger score.
    Score is not limited.
    """
    query_lower, candidate_lower = query.lower(), candidate.lower()
    qlen, clen = len(query), len(candidate)
    qpos, cpos = 0, 0
    score = 0
    last_match_pos = -1
    while qpos < qlen and cpos < clen:
        if query_lower[qpos] == candidate_lower[cpos]:
            if last_match_pos == cpos - 1:
                score += 10   # consecutive match adds more score
            else:
                score += 1   # match after some gap
            last_match_pos = cpos
            qpos += 1
        cpos += 1
    if qpos == qlen:
        # bonus for match starting early in candidate
        score += max(0, 10 - last_match_pos)
        return score
    return 0


def fuzzy_match_score(query, candidate):
    """
    Calculate score for fuzzy matching of query containing one or multiple words.
    Consecutive matches will have larger score.
    Matches closer to the start of the candidate string will have larger score.
    Score is not limited.
    """
    total_score = 0
    for word in query.split():
        score = fuzzy_match_score_single(word, candidate)
        if score == 0:
            return 0
        total_score += score
    return total_score


# use cython if available, ~6.7 times faster
if importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.search"):
    from endcord_cython.search import fuzzy_match_score


def search_channels_guild(channels, query, limit=50, score_cutoff=15):
    """Search for channels in one guild"""
    results = []
    worst_score = score_cutoff

    for channel in channels:
        # skip categories (type 4)
        if channel["permitted"] and channel["type"] != 4:
            if channel["type"] == 2:
                formatted = f"{channel["name"]} - voice"
            elif channel["type"] in (11, 12):
                formatted = f"{channel["name"]} - thread"
            elif channel["type"] == 15:
                formatted = f"{channel["name"]} - forum"
            else:
                formatted = channel["name"]

            score = fuzzy_match_score(query, formatted)
            if score < worst_score:
                continue
            heapq.heappush(results, (formatted, channel["id"], score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_channels_all(guilds, dms, query, full_input, limit=50, score_cutoff=15):
    """Search for guilds/categories/channels/DMs"""
    results = []
    worst_score = score_cutoff

    for dm in dms:
        formatted = f"{dm["name"]} (DM)"
        score = fuzzy_match_score(query, formatted) * 4   # dms get more score so they are on top
        if score < worst_score:
            continue
        heapq.heappush(results, (formatted, dm["id"], score))
        if len(results) > limit:
            heapq.heappop(results)
            worst_score = results[0][2]

    if full_input.startswith("toggle_mute") or full_input.startswith("mark_as_read") or full_input.startswith("goto"):
        full = True   # include guilds and categories
    else:
        full = False
    for guild in guilds:
        if full:
            formatted = f"{guild["name"]} - server"
            score = fuzzy_match_score(query, formatted) * 2   # guilds get more score so they are on top
            if score >= worst_score:
                heapq.heappush(results, (formatted, guild["guild_id"], score))
                if len(results) > limit:
                    heapq.heappop(results)
                    worst_score = results[0][2]

        for channel in guild["channels"]:
            if channel["permitted"]:
                if channel["type"] == 2:
                    formatted = f"{channel["name"]} - voice ({guild["name"]})"
                elif full and channel["type"] == 4:
                    formatted = f"{channel["name"]} - category ({guild["name"]})"
                elif channel["type"] in (11, 12):
                    formatted = f"{channel["name"]} - thread ({guild["name"]})"
                elif channel["type"] == 15:
                    formatted = f"{channel["name"]} - forum ({guild["name"]})"
                else:
                    formatted = f"{channel["name"]} ({guild["name"]})"
                score = fuzzy_match_score(query, formatted)
                if score < worst_score:
                    continue
                heapq.heappush(results, (formatted, channel["id"], score))
                if len(results) > limit:
                    heapq.heappop(results)
                    worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_usernames_roles(roles, query_results, guild_id, gateway, query, limit=50, score_cutoff=15):
    """Search for usernames and roles"""
    results = []
    worst_score = score_cutoff

    # roles first
    for role in roles:
        formatted = f"{role["name"]} - role"
        score = fuzzy_match_score(query, formatted)
        if score < worst_score:
            continue
        heapq.heappush(results, (formatted, f"&{role["id"]}", score))
        if len(results) > limit:
            heapq.heappop(results)
            worst_score = results[0][2]

    if query_results:
        for member in query_results:
            formatted = f"{member["username"]} {member["name"]}"
            score = fuzzy_match_score(query, formatted)
            if score < worst_score:
                continue
            if member["name"]:
                member_name = f" ({member["name"]})"
            else:
                member_name = ""
            heapq.heappush(results, (f"{member["username"]}{member_name}", member["id"], score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]
    else:
        gateway.request_members(
            guild_id,
            None,
            query=query,
            limit=10,
        )

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_emojis(all_emojis, premium, guild_id, query, safe_emoji=False, limit=50, score_cutoff=15):
    """Search for emoji"""
    results = []
    worst_score = score_cutoff

    # guild emoji
    if premium:
        emojis = all_emojis
    else:
        for guild in all_emojis:
            if guild["guild_id"] == guild_id:
                emojis = [guild]
                break
        else:
            emojis = []

    for guild in emojis:
        guild_name = guild["guild_name"]
        for guild_emoji in guild["emojis"]:
            formatted = f"{guild_emoji["name"]} ({guild_name})"
            score = fuzzy_match_score(query, formatted)
            if score < worst_score:
                continue
            heapq.heappush(results, (formatted, f"<:{guild_emoji["name"]}:{guild_emoji["id"]}>", score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]

    # standard emoji
    if len(results) < limit:
        for key, item in emoji.EMOJI_DATA.items():
            if item["status"] > 2:   # skip unqualified and minimally qualified emoji
                continue
            # emoji.EMOJI_DATA = {emoji: {"en": ":emoji_name:", "status": 2, "E": 3}...}
            # using only qualified emojis (status: 2)
            if safe_emoji:
                formatted = item["en"]
            else:
                formatted = f"{item["en"]} - {key}"
            score = fuzzy_match_score(query, formatted)
            if score < worst_score:
                continue
            heapq.heappush(results, (formatted, item["en"], score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_stickers(all_stickers, default_stickers, premium, guild_id, query, limit=50, score_cutoff=15):
    """Search for stickers"""
    results = []
    worst_score = score_cutoff

    if premium:
        stickers = all_stickers
    else:
        for pack in all_stickers:
            if pack["pack_id"] == guild_id:
                stickers = [pack]
                break
        else:
            stickers = []
        
    for pack in stickers + default_stickers:
        pack_name = pack["pack_name"]
        for sticker in pack["stickers"]:
            formatted = f"{sticker["name"]} ({pack_name})"
            score = fuzzy_match_score(query, formatted)
            if score < worst_score:
                continue
            heapq.heappush(results, (formatted, sticker["id"], score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_settings(config, query, limit=50, score_cutoff=15):
    """Search for settings"""
    results = []
    worst_score = score_cutoff

    for key, value in config.items():
        formatted = f"{key} = {value}"
        score = fuzzy_match_score(query, formatted)
        if score < worst_score:
            continue
        heapq.heappush(results, (formatted, f"set {key} = {value}", score))
        if len(results) > limit:
            heapq.heappop(results)
            worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_string_selects(message, query_in, limit=50, score_cutoff=15):
    """Search for string selects"""
    results = []
    worst_score = score_cutoff
    query = query_in.lower()
    num = query.split(" ")[1]

    try:
        num = max(int(num)-1, 0)
        query_words = query.split(" ")[2:]
    except (ValueError, IndexError):
        num = 0
        query_words = query.split(" ")[1:]
    try:
        string_select = message["component_info"]["string_selects"][num]
    except IndexError:
        string_select = None
    # allow executing command if space is at the end
    if string_select and not (query.endswith(" ") and not all(not x for x in query_words)):
        for option in string_select["options"]:
            score = fuzzy_match_score(query, option["label"])
            if score < worst_score:
                continue
            description = option.get("description", "")
            if description:
                formatted = f"{option["label"]}{description}"
            else:
                formatted = option["label"]
            heapq.heappush(results, (formatted, f"string_select {num+1} {option["value"]}", score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_set_notifications(guilds, dms, guild_id, channel_id, ping_options, query_in, score_cutoff=15):
    """Search for notification settings"""
    options = []
    query = query_in.lower()
    query_words = query.split(" ")

    if guild_id:   # channel/category
        channel = None
        for guild in guilds:
            if guild["guild_id"] == guild_id:
                for channel in guild["channels"]:
                    if channel["id"] == channel_id:
                        break
                break
        if channel:
            message_notifications = channel.get("message_notifications", 0)
            if channel["type"] == 4:
                default_val = guild["message_notifications"]
            else:
                for category in guild["channels"]:
                    if category["type"] == 4 and category["id"] == channel["parent_id"]:
                        default_val = category["message_notifications"]
                        default_val = default_val - 10 if default_val >= 10 else default_val
                        break
                else:
                    default_val = 2
            default = "default" + f" ({"server" if channel["type"] == 4 else "category"}: {ping_options[default_val]})"
            message_notifications = 3 if message_notifications >= 10 else message_notifications
            for num, option in enumerate(ping_options[:-1] + [default]):
                if num == message_notifications:
                    options.append((f"* {option}", query_words[0] + " " + option.split(" ")[0]))
                else:
                    options.append((option, query_words[0] + " " + option.split(" ")[0]))
    else:
        for dm in dms:
            if dm["id"] == channel_id:
                options.append(("No notification settings for DM", None))
                return options
        else:   # guild
            guild = None
            for guild in guilds:
                if guild["guild_id"] == channel_id:
                    break
            if guild:
                message_notifications = guild.get("message_notifications", 0)
                for num, option in enumerate(ping_options):
                    if num == message_notifications:
                        options.append((f"* {option}", f"{" ".join(query_words[:2])}{option}"))
                    else:
                        options.append((option, f"{" ".join(query_words[:2])}{option}"))
                options.append((f"suppress_everyone = {guild.get("suppress_everyone", False)}", f"{" ".join(query_words[:2])}suppress_everyone"))
                options.append((f"suppress_roles = {guild.get("suppress_roles", False)}", f"{" ".join(query_words[:2])}suppress_roles"))

    results = []
    for option in options:
        score = fuzzy_match_score(query_words[1], option[0].replace("*", " ").strip())
        if query_words[1] and score < score_cutoff:
            continue
        results.append((option[0], option[1], score))

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_client_commands(commands, query, limit=50, score_cutoff=15):
    """Search for client commands"""
    results = []
    worst_score = score_cutoff

    for command in commands:
        score = fuzzy_match_score(query, command[1])
        if score < worst_score:
            continue
        heapq.heappush(results, (*command, score))
        if len(results) > limit:
            heapq.heappop(results)
            worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_games(games, blacklist, query, limit=50, score_cutoff=15):
    """Search for settings"""
    results = []
    worst_score = score_cutoff

    for game_id, game_name in games:
        if game_id in blacklist:
            formatted = game_name + " (blacklisted)"
        else:
            formatted = game_name
        score = fuzzy_match_score(query, formatted)
        if score < worst_score and query:
            continue
        heapq.heappush(results, (formatted, f"game_detection_blacklist {game_name}", score))
        if len(results) > limit:
            heapq.heappop(results)
            worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_tabs(tabs, query, limit=50, score_cutoff=15):
    """Search for tabs"""
    results = []
    worst_score = score_cutoff

    for num, tab in enumerate(tabs):
        formatted = f"{num + 1} - {tab["channel_name"]} ({tab["guild_name"]})"
        score = fuzzy_match_score(query, formatted)
        if score < worst_score and query:
            continue
        heapq.heappush(results, (formatted, f"switch_tab {num}", score))
        if len(results) > limit:
            heapq.heappop(results)
            worst_score = results[0][2]

    return sorted(results, key=lambda x: x[2], reverse=True)


def search_app_commands(guild_apps, guild_commands, my_apps, my_commands, depth, guild_commands_permitted, dm, assist_skip_app_command, match_command_arguments, query, limit=50, score_cutoff=15):
    """Search for app commands"""
    results = []
    worst_score = score_cutoff
    query_words = query.split(" ")
    autocomplete = False
    if depth == 1 and assist_skip_app_command:
        depth = 2   # skip app assist on depth 1

    if depth == 1:   # app
        assist_app = query_words[0].replace("_", " ")
        # list apps
        for app in guild_apps:
            score = fuzzy_match_score(assist_app, app["name"])
            if score < worst_score and assist_app:   # show all if no text is typed
                continue
            clean_name = app["name"].lower().replace(" ", "_")
            heapq.heappush(results, (f"{clean_name} - guild app", f"/{clean_name}", score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]
        for app in my_apps:
            score = fuzzy_match_score(assist_app, app["name"])
            if score < worst_score and assist_app:
                continue
            clean_name = app["name"].lower().replace(" ", "_")
            heapq.heappush(results, (f"{clean_name} - user app", f"/{clean_name}", score))
            if len(results) > limit:
                heapq.heappop(results)
                worst_score = results[0][2]

    elif depth == 2:   # command
        if assist_skip_app_command:
            assist_app_name = None
            assist_command = query_words[0].replace("_", " ")
        else:
            assist_app_name = query_words[0].lower()
            assist_command = query_words[1].replace("_", " ")
        # list commands
        found = False
        for num, command in enumerate(guild_commands):
            if (command["app_name"].lower().replace(" ", "_") == assist_app_name or assist_skip_app_command) and guild_commands_permitted[num]:
                command_name = command["name"].lower()
                score = fuzzy_match_score(assist_command, command_name)
                if score < worst_score and assist_command:
                    continue
                if assist_skip_app_command:
                    name = f"{command_name.replace(" ", "_")} ({command["app_name"]})"
                    value = f"{command["app_name"].lower().replace(" ", "_")} {command_name.replace(" ", "_")}"
                else:
                    name = command_name.replace(" ", "_")
                    value = command_name.replace(" ", "_")
                if command.get("description"):
                    name += f" - {command["description"]}"
                heapq.heappush(results, (name, value, score))
                if len(results) > limit:
                    heapq.heappop(results)
                    worst_score = results[0][2]
                found = True
        if not found:    # skip my commands if found in guild commands
            for command in my_commands:
                if (command["app_name"].lower().replace(" ", "_") == assist_app_name or assist_skip_app_command) and ((not dm) or command.get("dm")):
                    command_name = command["name"].lower()
                    score = fuzzy_match_score(assist_command, command_name)
                    if score < worst_score and assist_command:
                        continue
                    if assist_skip_app_command:
                        name = f"{command_name.replace(" ", "_")} ({command["app_name"]})"
                        value = f"{command["app_name"].lower().replace(" ", "_")} {command_name.replace(" ", "_")}"
                    else:
                        name = command_name.replace(" ", "_")
                        value = command_name.replace(" ", "_")
                    if command.get("description"):
                        name += f" - {command["description"]}"
                    heapq.heappush(results, (name, value, score))
                    if len(results) > limit:
                        heapq.heappop(results)
                        worst_score = results[0][2]

    elif depth == 3:   # group/subcommand/option
        results.append(("EXECUTE", None, 10000))
        assist_app_name = query_words[0].lower()
        assist_command = query_words[1].lower()
        assist_subcommand = query_words[2].replace("_", " ")
        # find command
        for num, command in enumerate(guild_commands):
            if command["app_name"].lower().replace(" ", "_") == assist_app_name and guild_commands_permitted[num] and assist_command == command["name"].lower().replace(" ", "_"):
                break
        else:
            for command in my_commands:
                if command["app_name"].lower().replace(" ", "_") == assist_app_name and assist_command == command["name"].lower().replace(" ", "_") and ((not dm) or command.get("dm")):
                    break
            else:
                command = None
        if command:
            # list groups/subcommands/options
            for subcommand in command.get("options", []):
                subcommand_name = subcommand["name"].lower()
                score = fuzzy_match_score(assist_subcommand, subcommand_name)
                if score < worst_score and assist_subcommand:
                    continue
                if subcommand["type"] == 1:
                    name = f"{subcommand_name.replace(" ", "_")} - subcommand"
                    value = subcommand_name.replace(" ", "_")
                elif subcommand["type"] == 2:
                    name = f"{subcommand_name.replace(" ", "_")} - group"
                    value = subcommand_name.replace(" ", "_")
                else:
                    name = f"{subcommand_name.replace(" ", "_")} - option: {COMMAND_OPT_TYPE[int(subcommand["type"])-1]}"
                    value = f"--{subcommand_name.replace(" ", "_")}="
                if subcommand.get("required"):
                    name += " (required)"
                if subcommand.get("description"):
                    name += f" - {subcommand["description"]}"
                heapq.heappush(results, (name, value, score))
                if len(results) > limit:
                    heapq.heappop(results)
                    worst_score = results[0][2]
            # list option choices
            else:
                match = re.search(match_command_arguments, query_words[2].lower())
                if match:
                    for option in command.get("options", []):
                        if option["name"].lower() == match.group(1):
                            break
                    else:
                        option = None
                    if option and "choices" in option:
                        value = match.group(2).replace("_", " ") if match.group(2) else ""
                        for choice in option["choices"]:
                            score = fuzzy_match_score(value, choice["name"])
                            if score < worst_score and value:
                                continue
                            heapq.heappush(results, (choice["name"], choice["value"], score))
                            if len(results) > limit:
                                heapq.heappop(results)
                                worst_score = results[0][2]
                    elif option and option.get("autocomplete"):
                        autocomplete = True

    elif depth == 4:   # groups subcommand and options
        results.append(("EXECUTE", None, 10000))
        assist_app_name = query_words[0].lower()
        assist_command = query_words[1].lower()
        assist_subcommand = query_words[2].lower()
        assist_group_subcommand = query_words[3].replace("_", " ")
        options_only = False
        # find command
        for num, command in enumerate(guild_commands):
            if command["app_name"].lower().replace(" ", "_") == assist_app_name and guild_commands_permitted[num] and assist_command == command["name"].lower().replace(" ", "_"):
                break
        else:
            for command in my_commands:
                if command["app_name"].lower().replace(" ", "_") == assist_app_name and assist_command == command["name"].lower().replace(" ", "_") and ((not dm) or command.get("dm")):
                    break
            else:
                command = None
        if command:
            # find subcommand
            for subcommand in command.get("options", []):
                if subcommand["name"].lower().replace(" ", "_") == assist_subcommand:
                    break
            else:
                if re.search(match_command_arguments, assist_subcommand):
                    subcommand = command   # when adding multiple options
                    options_only = True
                else:
                    subcommand = None
            if subcommand:
                # list group_subcommands/options
                for group_subcommand in subcommand.get("options", []):
                    group_subcommand_name = group_subcommand["name"].lower()
                    score = fuzzy_match_score(assist_group_subcommand, group_subcommand_name)
                    if score < worst_score and assist_group_subcommand:
                        continue
                    if options_only and group_subcommand["type"] in (1, 2):
                        continue   # skip non-options
                    if group_subcommand["type"] == 1:
                        name = f"{group_subcommand_name.replace(" ", "_")} - subcommand"
                        value = group_subcommand_name.replace(" ", "_")
                    else:
                        name = f"{group_subcommand_name.replace(" ", "_")} - option: {COMMAND_OPT_TYPE[int(group_subcommand["type"])-1]}"
                        value = f"--{group_subcommand_name.replace(" ", "_")}="
                    if group_subcommand.get("required"):
                        name += " (required)"
                    if group_subcommand.get("description"):
                        name += f" - {group_subcommand["description"]}"
                    heapq.heappush(results, (name, value, score))
                    if len(results) > limit:
                        heapq.heappop(results)
                        worst_score = results[0][2]
                # list option choices
                else:
                    match = re.search(match_command_arguments, query_words[3].lower())
                    if match:
                        for option in subcommand.get("options", []):
                            if option["name"].lower() == match.group(1):
                                break
                        else:
                            option = None
                        if option and "choices" in option:
                            value = match.group(2).replace("_", " ") if match.group(2) else ""
                            for choice in option["choices"]:
                                score = fuzzy_match_score(value, choice["name"])
                                if score < worst_score and value:
                                    continue
                                heapq.heappush(results, (choice["name"], choice["value"], score))
                                if len(results) > limit:
                                    heapq.heappop(results)
                                    worst_score = results[0][2]
                        elif option and option.get("autocomplete"):
                            autocomplete = True

    elif depth >= 5:   # options
        results.append(("EXECUTE", None, 10000))
        assist_app_name = query_words[0].lower()
        assist_command = query_words[1].lower()
        assist_subcommand = query_words[2].lower()
        assist_group_subcommand = query_words[3].lower()
        assist_option = query_words[4].replace("_", " ")
        options_only = False
        # find command
        for num, command in enumerate(guild_commands):
            if command["app_name"].lower().replace(" ", "_") == assist_app_name and guild_commands_permitted[num] and assist_command == command["name"].lower().replace(" ", "_"):
                break
        else:
            for command in my_commands:
                if command["app_name"].lower().replace(" ", "_") == assist_app_name and assist_command == command["name"].lower().replace(" ", "_") and ((not dm) or command.get("dm")):
                    break
            else:
                command = None
        if command:
            # find subcommand
            for subcommand in command.get("options", []):
                if subcommand["name"].lower().replace(" ", "_") == assist_subcommand:
                    break
            else:
                if re.search(match_command_arguments, assist_subcommand):
                    subcommand = command   # when adding multiple options
                    options_only = True
                else:
                    subcommand = None
            if subcommand:
                # find group subcommand
                for group_subcommand in subcommand.get("options", []):
                    if group_subcommand["name"].lower().replace(" ", "_") == assist_group_subcommand:
                        break
                else:
                    if re.search(match_command_arguments, assist_group_subcommand):
                        group_subcommand = subcommand   # when adding multiple options
                        options_only = True
                    else:
                        group_subcommand = None
                if group_subcommand:
                    # list options
                    for option in group_subcommand.get("options", []):
                        option_name = option["name"].lower()
                        score = fuzzy_match_score(assist_option, option_name)
                        if score < worst_score and assist_option:
                            continue
                        if options_only and option["type"] in (1, 2):
                            continue   # skip non-options
                        name = f"{option_name.replace(" ", "_")} - option: {COMMAND_OPT_TYPE[int(option["type"])-1]}"
                        value = f"--{option_name.replace(" ", "_")}="
                        if option.get("required"):
                            name += " (required)"
                        if option.get("description"):
                            name += f" - {option["description"]}"
                        heapq.heappush(results, (name, value, score))
                        if len(results) > limit:
                            heapq.heappop(results)
                            worst_score = results[0][2]
                    # list option choices
                    else:
                        match = re.search(match_command_arguments, query_words[4].lower())
                        if match:
                            for option in group_subcommand.get("options", []):
                                if option["name"].lower() == match.group(1):
                                    break
                            else:
                                option = None
                            if option and "choices" in option:
                                value = match.group(2).replace("_", " ") if match.group(2) else ""
                                for choice in option["choices"]:
                                    score = fuzzy_match_score(value, choice["name"])
                                    if score < worst_score and value:
                                        continue
                                    heapq.heappush(results, (choice["name"], choice["value"], score))
                                    if len(results) > limit:
                                        heapq.heappop(results)
                                        worst_score = results[0][2]
                            elif option and option.get("autocomplete"):
                                autocomplete = True

    return sorted(results, key=lambda x: x[2], reverse=True), autocomplete
