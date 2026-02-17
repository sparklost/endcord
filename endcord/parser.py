import logging
import re
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
DISCORD_EPOCH_MS = 1420070400000
STATUS_STRINGS = ("online", "idle", "dnd", "invisible")
TIME_FORMATS = ("%Y-%m-%d", "%Y-%m-%d-%H-%M", "%H:%M:%S", "%H:%M")
TIME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
NOTIFICATION_VALUES = ("all", "mentions", "nothing", "default", "suppress_everyone", "suppress_roles")

match_from = re.compile(r"from:<@\d*>")
match_mentions = re.compile(r"mentions:<@\d*>")
match_has = re.compile(r"has:(?:link|embed|file|video|image|sound|sticker)")
match_before = re.compile(r"before:\d{4}-\d{2}-\d{2}")
match_after = re.compile(r"after:\d{4}-\d{2}-\d{2}")
match_in = re.compile(r"in:<#\d*>")
match_pinned = re.compile(r"pinned:(?:true|false)")

match_setting = re.compile(r"(\w+) ?= ?(.+)")
match_channel = re.compile(r"<#(\d*)>")
match_profile = re.compile(r"<@(\d*)>")

match_command_arguments = re.compile(r"--(\S+)=(\w+|\"[^\"]+\")?")

match_string_select = re.compile(r"string_select(?: (\d+))?\s+(.+)")
match_time_with_unit = re.compile(r"(\d+)([wdhms])")


def date_to_snowflake(date, end=False):
    """Convert date to discord snowflake, rounded to day start, if end=True then is rounded to day end"""
    try:
        time_obj = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        time_obj = datetime.now()
        time_obj = time_obj.replace(hour=0, minute=0, second=0, microsecond=0)
    # timestamp cant be larger than now
    if int(time_obj.timestamp()) > time.time():
        time_obj = datetime.now()
        time_obj = time_obj.replace(hour=0, minute=0, second=0, microsecond=0)
        time_obj = time_obj.replace(tzinfo=timezone.utc)
    if end:
        time_obj += timedelta(days=1)
    return (int(time_obj.timestamp()) * 1000 - DISCORD_EPOCH_MS) << 22


def date_to_timestamp(date):
    """Convert date to discord snowflake, rounded to day start, if end=True then is rounded to day end"""
    time_obj = None
    # try various time formats
    for time_format in TIME_FORMATS:
        try:
            time_obj = datetime.strptime(date, time_format)
        except ValueError:
            continue

    if not time_obj:
        time_obj = datetime.now()
        time_obj = time_obj.replace(hour=0, minute=0, second=0, microsecond=0)

    # set current date if its unset
    if time_obj.year == 1900:
        now = datetime.now()
        time_obj = time_obj.replace(year=now.year, month=now.month, day=now.day)

    return int(time_obj.timestamp())


def time_string_seconds(time_str):
    """Convert time string to seconds.Strings: 1w, 2d, 3h, 4m, 5s"""
    try:
        return int(time_str)
    except ValueError:
        pass
    total = 0
    for value, unit in re.findall(match_time_with_unit, time_str.lower()):
        total += int(value) * TIME_UNITS[unit]
    if not total:
        return 0
    return total


def read_value(text, idx):
    """Read quited and unquoted value from string index"""
    length = len(text)
    # quoted value
    if idx < length and text[idx] == '"':
        idx += 1
        start = idx
        while idx < length and text[idx] != '"':
            idx += 1
        value = text[start:idx]
        return value, idx + 1

    # unquoted value
    start = idx
    while idx < length and text[idx] != " ":
        idx += 1
    return text[start:idx], idx



def search_string(text):
    """
    Parse search string.
    from:[<@ID>]
    mentions:[<@ID>]
    has:[link|embed|file|video|image|sound|sticker]
    before:[2015-01-01]
    after:[2015-01-01]
    in:[<#ID>]
    pinned:[true|false]
    """
    author_id = []
    for match in re.findall(match_from, text):
        text = text.replace(match, "")
        author_id.append(match[7:-1])
    mentions = []
    for match in re.findall(match_mentions, text):
        text = text.replace(match, "")
        author_id.append(match[11:-1])
    has = []
    for match in re.findall(match_has, text):
        text = text.replace(match, "")
        has.append(match[4:])
    max_id = []
    for match in re.findall(match_before, text):
        text = text.replace(match, "")
        max_id.append(date_to_snowflake(match[7:]))
    min_id = []
    for match in re.findall(match_after, text):
        text = text.replace(match, "")
        min_id.append(date_to_snowflake(match[6:], end=True))
    channel_id = []
    for match in re.findall(match_in, text):
        text = text.replace(match, "")
        channel_id.append(match[5:-1])
    pinned = []
    for match in re.findall(match_pinned, text):
        text = text.replace(match, "")
        pinned.append(match[7:])
    text = text.strip()
    return text, channel_id, author_id, mentions, has, max_id, min_id, pinned


def check_start_command(text, my_commands, guild_commands, permitted_guild_commands):
    """Check if string is valid start of command"""
    app_name = text.split(" ")[0][1:].lower()
    if not app_name:
        return False
    for command in my_commands:
        if command["app_name"].lower().replace(" ", "_") == app_name:
            return True
    for num, command in enumerate(guild_commands):
        if permitted_guild_commands[num] and command["app_name"].lower().replace(" ", "_") == app_name:
            return True
    return False


def verify_option_type(option_value, option_type, roles, channels):
    """Check if option value is of correct type"""
    if option_type in (1, 2):   # SUB_COMMAND and SUB_COMMAND_GROUP
        if not option_value:
            return False   # skip subcommands
    if option_type == 3:   # STRING
        return not (
            bool(re.search(match_profile, option_value)) or
            bool(re.search(match_channel, option_value))
        )
    if option_type == 4:   # INTEGER
        try:
            int(option_value)
            return True
        except ValueError:
            pass
    elif option_type == 5:   # BOOLEAN
        try:
            bool(option_value)
            return True
        except ValueError:
            pass
    elif option_type == 6:   # USER
        return bool(re.search(match_profile, option_value))
    elif option_type == 7:   # CHANNEL
        match = re.search(match_channel, option_value)
        if match:
            channel_id = match.group(1)
            for channel in channels:
                if channel["id"] == channel_id:
                    return True
    elif option_type == 8:   # ROLE
        match = re.search(match_profile, option_value)
        if match:
            role_id = match.group(1)
            for role in roles:
                if role["id"] == role_id:
                    return True
    elif option_type == 9:   # MENTIONABLE
        return bool(re.search(match_profile, option_value))
    elif option_type == 10:   # NUMBER
        try:
            float(option_value)
            return True
        except ValueError:
            pass
    elif option_type == 11:   # ATTACHMENT
        if option_value == 0:
            return True
    return False


def app_command_string(text, my_commands, guild_commands, permitted_guild_commands, roles, channels, dm, autocomplete):
    """Parse app command string and prepare data payload"""
    app_name = text.split(" ")[0][1:].lower()
    if not app_name:
        return None, None, None

    #  verify command
    command_name = text.split(" ")[1]
    if command_name.startswith("--"):
        return None, None, None
    for num, command in enumerate(guild_commands):
        if permitted_guild_commands[num] and command["name"] == command_name and command["app_name"].lower().replace(" ", "_") == app_name:
            app_id = command["app_id"]
            break
    else:
        for command in my_commands:
            if command["name"] == command_name and command["app_name"].lower().replace(" ", "_") == app_name:
                if dm and not command.get("dm"):
                    return None, None, None   # command not allowed in dm
                app_id = command["app_id"]
                break
        else:
            return None, None, None

    # get subcommands
    try:
        subcommand_group_name = text.split(" ")[2]
        if subcommand_group_name.startswith("--"):
            subcommand_group_name = None
    except IndexError:
        subcommand_group_name = None
    if subcommand_group_name:
        try:
            subcommand_name = text.split(" ")[3]
            if subcommand_name.startswith("--"):
                subcommand_name = None
        except IndexError:
            subcommand_name = None
    else:
        subcommand_name = None

    command_options = []
    for match in re.finditer(match_command_arguments, text):
        if len(match.groups()) == 2:
            value = match.group(2)
        else:
            value = 0
        command_options.append((match.group(1), value))   # (name, value)
    context_options = command.get("options", [])

    # verify subcommands and groups
    subcommand = None
    subcommand_group = None
    if subcommand_group_name:
        for subcmd in context_options:
            if subcmd["type"] == 1 and subcmd["name"] == subcommand_group_name:   # subcommand
                subcommand = subcmd
                context_options = subcmd.get("options", [])
                break
            elif subcmd["type"] == 2 and subcmd["name"] == subcommand_group_name:   # group
                subcommand_group = subcmd
                break
    if subcommand_group and subcommand_name:   # subcommand after group
        for subcmd in subcommand_group.get("options", []):
            if subcmd["type"] == 1 and subcmd["name"] == subcommand_name:
                subcommand = subcmd
                context_options = subcmd.get("options", [])
                break

    # add and verify options
    need_attachment = False
    options = []
    required = True
    for num, (option_name, option_value) in enumerate(command_options):
        for option in context_options:
            if option["name"] == option_name:
                break
        option_value_clean = option_value
        if option["type"] == 11:
            need_attachment = True
            option_value_clean = 0
        if not autocomplete and not (option_value_clean and verify_option_type(option_value_clean, option["type"], roles, channels)):
            return None, None, None
        option_dict = {
            "type": option["type"],
            "name": option["name"],
            "value": option_value_clean,
        }
        if autocomplete and num == len(command_options) - 1:   # if its last option
            option_dict["focused"] = True   # what "focused" means ?
        options.append(option_dict)

    # check for required
    for option in context_options:
        if option.get("required"):
            for option_name, _ in command_options:
                if option["name"] == option_name:
                    break
            else:
                return None, None, None   # missing required option

    # dont allow command with options but none is set
    if not options and not subcommand_group_name and context_options and required:
        return None, None, None

    # add subcommands and groups
    if subcommand:
        options = [{
            "type": subcommand["type"],
            "name": subcommand["name"],
            "options": options,
        }]
        if not options[0]["options"]:
            options[0].pop("options")
    if subcommand_group:
        options = [{
            "type": subcommand_group["type"],
            "name": subcommand_group["name"],
            "options": options,
        }]
        if not options[0]["options"]:
            options[0].pop("options")
            return None, None, None   # cant have group without subcommand

    command_data = {
        "version": command["version"],
        "id": command["id"],
        "name": command["name"],
        "type": 1,   # only slash commands
        "options": options,
        "attachments": [],
    }
    return command_data, app_id, need_attachment


def split_command_binding(text):
    """Split string into multiple strings on ";" and ignore whitespace and newline"""
    # Using null chaacter as placeholder
    parts = text.replace("\\;", "\x00").split(";")
    return [p.replace("\x00", ";").strip() for p in parts]


def command_string(text):
    """Parse command string"""

    # 0 - UNKNOWN
    cmd_type = 0
    cmd_args = {}
    text_lower = text.lower()

    # 1 - SET
    if text_lower.startswith("set "):
        # "set [key] = [value]" / "set [key]=[value]"
        cmd_type = 1
        match = re.search(match_setting, text)
        if match:
            key = match.group(1)
            value = match.group(2)
            if not (key and value):
                cmd_type = 0
        else:
            cmd_type = 0
        cmd_args = {
            "key": key,
            "value": value,
        }

    # 2 - BOTTOM
    elif text_lower.startswith("bottom"):
        cmd_type = 2

    # 3 - GOTO_REPLY
    elif text_lower.startswith("goto_reply"):
        cmd_type = 3

    # 4 - DOWNLOAD
    elif text_lower.startswith("download"):
        cmd_type = 4
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            pass

    # 5 - OPEN_LINK
    elif text_lower.startswith("open_link"):
        cmd_type = 5
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            pass

    # 6 - PLAY
    elif text_lower.startswith("play"):
        cmd_type = 6
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            pass

    # 7 - CANCEL
    elif text_lower.startswith("cancel"):
        cmd_type = 7
        value_part = text_lower[7:]
        cmd_args = {"type": 0}
        if "download" in value_part or "1" in value_part:
            cmd_args = {"type": 1}
        if "upload" in value_part or "2" in value_part:
            cmd_args = {"type": 2}
        if "attachment" in value_part or "3" in value_part:
            cmd_args = {"type": 3}

    # 8 - COPY_MESSAGE
    elif text_lower.startswith("copy_message"):
        cmd_type = 8

    # 9 - UPLOAD
    elif text_lower.startswith("upload"):
        cmd_type = 9
        cmd_args = {"path": text[7:]}

    # 10 - SPOIL
    elif text_lower.startswith("spoil"):
        cmd_type = 10
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            pass

    # 11 - TOGGLE_THREAD_TREE
    elif text_lower.startswith("toggle_thread_tree"):
        cmd_type = 11

    # 12 - PROFILE
    elif text_lower.startswith("profile"):
        cmd_type = 12
        match = re.search(match_profile, text)
        if match:
            cmd_args = {"user_id": match.group(1)}

    # 13 - CHANNEL
    elif text_lower.startswith("channel"):
        cmd_type = 13
        match = re.search(match_channel, text)
        if match:
            cmd_args = {"channel_id": match.group(1)}

    # 14 - SUMMARIES
    elif text_lower.startswith("summaries"):
        cmd_type = 14
        match = re.search(match_channel, text)
        if match:
            cmd_args = {"channel_id": match.group(1)}

    # 15 - HIDE
    elif text_lower.startswith("hide"):
        cmd_type = 15
        match = re.search(match_channel, text)
        if match:
            cmd_args = {"channel_id": match.group(1)}

    # 16 - SEARCH
    elif text_lower.startswith("search"):
        cmd_type = 16
        search_text = text[7:].strip(" ")
        cmd_args = {"search_text": search_text}

    # 17 - LINK_CHANNEL
    elif text_lower.startswith("link_channel"):
        cmd_type = 17
        match = re.search(match_channel, text)
        if match:
            cmd_args = {"channel_id": match.group(1)}

    # 18 - LINK_MESSAGE
    elif text_lower.startswith("link_message"):
        cmd_type = 18

    # 19 - GOTO_MENTION
    elif text_lower.startswith("goto_mention"):
        cmd_type = 19
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            pass

    # 20 - STATUS
    elif text_lower.startswith("status"):
        cmd_type = 20
        text += " "
        if text.split(" ")[1].lower() in STATUS_STRINGS:
            cmd_args = {"status": text.split(" ")[1].lower()}
        else:
            try:
                num = int(text.split(" ")[1].lower()) - 1
                if num < len(STATUS_STRINGS):
                    cmd_args = {"status": STATUS_STRINGS[num]}
            except ValueError:
                pass

    # 21 - RECORD
    elif text_lower.startswith("record"):
        cmd_type = 21
        text += " "
        cmd_args = {"cancel": text.split(" ")[1].lower() == "cancel"}

    # 22 - MEMBER_LIST
    elif text_lower.startswith("member_list"):
        cmd_type = 22

    # 23 - REACT
    elif text_lower.startswith("react"):
        cmd_type = 23
        react_text = text[6:].strip(" ")
        cmd_args = {"react_text": react_text}

    # 24 - SHOW_REACTIONS
    elif text_lower.startswith("show_reactions"):
        cmd_type = 24

    # 25 - GOTO
    elif text_lower.startswith(("goto", "xyzzy")):
        cmd_type = 25
        match = re.search(match_channel, text)
        if match:
            cmd_args = {"channel_id": match.group(1)}
        elif text_lower.startswith("xyzzy"):
            cmd_args = {"channel_id": "special"}
        else:
            cmd_type = 0

    # 26 - VIEW_PFP
    elif text_lower.startswith("view_pfp"):
        cmd_type = 26
        match = re.search(match_profile, text)
        if match:
            cmd_args = {"user_id": match.group(1)}

    # 27 - CHECK_STANDING
    elif text_lower.startswith("check_standing"):
        cmd_type = 27

    # 28 - PASTE
    elif text_lower.startswith("paste"):
        cmd_type = 28

    # 29 - TOGGLE_MUTE
    elif text_lower.startswith("toggle_mute"):
        cmd_type = 29
        match = re.search(match_channel, text)
        if match:
            cmd_args = {"channel_id": match.group(1)}

    # 30 - TOGGLE_TAB
    elif text_lower.startswith("toggle_tab"):
        cmd_type = 30

    # 31 - SWITCH_TAB
    elif text_lower.startswith("switch_tab"):
        cmd_type = 31
        value_part = text_lower[11:]
        if "next" in value_part:
            cmd_args = {"num": "next"}
        elif "prev" in value_part:
            cmd_args = {"num": "prev"}
        else:
            try:
                num = int(text.split(" ")[1]) - 1
                cmd_args = {"num": num}
            except (IndexError, ValueError):
                cmd_type = 0

    # 32 - MARK_AS_READ
    elif text_lower.startswith("mark_as_read"):
        cmd_type = 32
        match = re.search(match_channel, text)
        if match:
            cmd_args = {"channel_id": match.group(1)}
        elif text.split(" ")[1] == "*":
            cmd_args = {"channel_id": "*"}

    # 33 - INSERT_TIMESTAMP
    elif text_lower.startswith("insert_timestamp"):
        cmd_type = 33
        try:
            date_string = text.split(" ")[1]
            timestamp = date_to_timestamp(date_string)
            cmd_args = {"timestamp": timestamp}
        except (IndexError, ValueError):
            cmd_type = 0

    # 34 - VOTE
    elif text_lower.startswith("vote"):
        cmd_type = 34
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            cmd_type = 0

    # 35 - SHOW_PINNED
    elif text_lower.startswith("show_pinned"):
        cmd_type = 35

    # 36 - PIN_MESSAGE
    elif text_lower.startswith("pin_message"):
        cmd_type = 36

    # 37 - PUSH_BUTTON
    elif text_lower.startswith("push_button"):
        cmd_type = 37
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except ValueError:
            name = text.split(" ")[1]
            cmd_args = {"name": name}
        except IndexError:
            cmd_type = 0

    # 38 - STRING_SELECT
    elif text_lower.startswith("string_select"):
        cmd_type = 38
        match = re.search(match_string_select, text_lower)
        if match:
            num = match.group(1)
            string = match.group(2)
            cmd_args = {"num": num, "text": string}
        else:
            cmd_type = 0

    # 39 - DUMP_CHAT
    elif text_lower.startswith("dump_chat"):
        cmd_type = 39

    # 40 - SET_NOTIFICATIONS
    elif text_lower.startswith("set_notifications"):
        cmd_type = 40
        cmd_split = text.split(" ")
        have_id = False
        cmd_args = {"id": None, "setting": ""}
        if len(cmd_split) > 1:
            match = re.search(match_channel, cmd_split[1])
            if match:
                cmd_args["channel_id"] = match.group(1)
                have_id = True
        if len(cmd_split) > 1 + have_id:
            if cmd_split[1 + have_id].lower() in NOTIFICATION_VALUES:
                cmd_args["setting"] = cmd_split[1 + have_id].lower()
            else:
                cmd_type = 0

    # 41 - GIF
    elif text_lower.startswith("gif"):
        cmd_type = 41
        search_text = text[4:].strip(" ")
        cmd_args = {"search_text": search_text}

    # 42 - REDRAW
    elif text_lower.startswith("redraw"):
        cmd_type = 42

    # 43 - EXTERNAL_EDIT
    elif text_lower.startswith("external_edit"):
        cmd_type = 43

    # 44 - CUSTOM_STATUS/EMOJI/REMOVE
    elif text_lower.startswith("custom_status"):
        cmd_type = 44
        if text_lower.startswith("custom_status_emoji"):
            cmd_args = {"emoji": text[20:]}
            if len(text) <= 20:
                cmd_type = 0
        elif text_lower.startswith("custom_status_remove"):
            pass
        else:
            cmd_args = {"text": text[14:]}
            if len(text) <= 14:
                cmd_type = 0

    # 45 - BLOCK
    elif text_lower.startswith("block"):
        cmd_type = 45
        match = re.search(match_profile, text)
        if match:
            cmd_args = {
                "user_id": match.group(1),
                "ignore": "ignore" in text_lower,
            }
        else:
            cmd_type = 0

    # 46 - UNBLOCK
    elif text_lower.startswith("unblock"):
        cmd_type = 46
        match = re.search(match_profile, text)
        if match:
            cmd_args = {
                "user_id": match.group(1),
                "ignore": "ignore" in text_lower,
            }
        else:
            cmd_type = 0

    # 47 - TOGGLE_BLOCKED_MESSGAES
    elif text_lower.startswith("toggle_blocked_messages"):
        cmd_type = 47

    # 48 - VOICE_START_CALL
    elif text_lower.startswith("voice_start_call"):
        cmd_type = 48

    # 49 - VOICE_ACCEPT_CALL
    elif text_lower.startswith("voice_accept_call"):
        cmd_type = 49

    # 50 - VOICE_LEAVE_VALL
    elif text_lower.startswith("voice_leave_call"):
        cmd_type = 50

    # 51 - VOICE_REJECT_CALL
    elif text_lower.startswith("voice_reject_call"):
        cmd_type = 51

    # 52 - VOICE_TOGGLE_MUTE
    elif text_lower.startswith("voice_toggle_mute"):
        cmd_type = 52

    # 53 - VOICE_LIST_CALL
    elif text_lower.startswith("voice_list_call"):
        cmd_type = 53

    # 54 - GENERATE_INVITE
    elif text_lower.startswith("generate_invite"):
        cmd_type = 54
        max_age = 604800
        max_uses = 0
        text_split = text_lower.split(" ")
        text_split = list(filter(None, text_split))
        if len(text_split) >= 2:
            new_max_age = time_string_seconds(text_split[1])
            if new_max_age is not None:
                max_age = new_max_age
        if len(text_split) >= 3:
            try:
                max_uses = int(text_split[2])
            except ValueError:
                pass

        cmd_args = {
            "max_age": max_age,
            "max_uses": max_uses,
        }

    # 55 - SHOW_LOG
    elif text_lower.startswith("show_log"):
        cmd_type = 55

    # 56 - RENAME_FOLDER
    elif text_lower.startswith("rename_folder"):
        cmd_type = 56
        name = text[14:].strip(" ")
        if name:
            cmd_args = {"name": name}
        else:
            cmd_type = 0

    # 57 - VIEW_EMOJI
    elif text_lower.startswith("view_emoji"):
        cmd_type = 57
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            name = text[11:].strip(" ")
            cmd_args = {"name": name}

    # 58 - QUIT
    elif text_lower.split(" ")[0] == "quit":
        cmd_type = 58

    # 59 - MARK_AS_UNREAD
    elif text_lower.split(" ")[0] == "mark_as_unread":
        cmd_type = 59

    # 60 - TOGGLE_THREAD
    elif text_lower.split(" ")[0] == "toggle_thread":
        cmd_type = 60

    # 61 - GAME_DETECTION_BLACKLIST
    elif text_lower.split(" ")[0] == "game_detection_blacklist":
        cmd_type = 61
        name = text[25:].strip(" ")
        if name:
            cmd_args = {"name": name}
        else:
            cmd_type = 0

    # 62 - OPEN_CONFIG_DIR
    elif text_lower.split(" ")[0] == "open_config_dir":
        cmd_type = 62

    # 63 - SEND_MESSAGE
    elif text_lower.split(" ")[0] == "send_message":
        cmd_type = 63
        channel_id = None
        reply_id = None
        ping = True
        attachments = []
        length = len(text_lower)
        i = 13
        while i < length and text[i] == " ":   # skip spaces
            i += 1
        while i < length:
            if text.startswith("--channel_id=", i):
                i += len("--channel_id=")
                value, i = read_value(text, i)
                try:
                    int(value)
                    channel_id = value
                except ValueError:
                    match = re.search(match_channel, value)
                    if match:
                        channel_id = match.group(1)
            elif text.startswith("--reply_id=", i):
                i += len("--reply_id=")
                reply_id, i = read_value(text, i)
            elif text.startswith("--ping=", i):
                i += len("--ping=")
                value, i = read_value(text, i)
                ping = value.lower() == "true"
            elif text.startswith("--attachment=", i):
                i += len("--attachment=")
                value, i = read_value(text, i)
                attachments.append(value)
            else:
                break
            while i < length and text[i] == " ":   # skip spaces
                i += 1
        content = text[i:].lstrip()
        cmd_args = {
            "content": content,
            "channel_id": channel_id,
            "reply_id": reply_id,
            "ping": ping,
            "attachments": attachments,
        }

    # 64 - COPY_LINK
    elif text_lower.startswith("copy_link"):
        cmd_type = 64
        try:
            num = int(text.split(" ")[1])
            cmd_args = {"num": num}
        except (IndexError, ValueError):
            pass

    # 65 - TOGGLE_AFK
    elif text_lower.startswith("toggle_afk"):
        cmd_type = 65

    # 66 - 666
    elif text_lower.startswith("666"):
        cmd_type = 66

    # 67 - TOGGLE_SNOW
    elif text_lower.startswith("toggle_snow"):
        cmd_type = 67

    # 68 - REMOVE_ALL_TABS
    elif text_lower.startswith("remove_all_tabs"):
        cmd_type = 68

    # 69 - COLLAPSE_ALL_EXCEPT
    elif text_lower.startswith("collapse_all_except"):
        cmd_type = 69
        value_part = text_lower[20:].strip(" ")
        if value_part == "current":
            cmd_args = {"value": 2}
        elif value_part == "selected":
            cmd_args = {"value": 0}
        elif value_part == "above":
            cmd_args = {"value": 1}
        elif value_part == "bellow":
            cmd_args = {"value": -1}

    # 70 - TREE_SELECT
    elif text_lower.startswith("tree_select"):
        cmd_type = 70
        all_args = text_lower[12:].strip(" ").split(" ")
        cmd_args = {
            "type": "server" in all_args,
            "value": "prev" in all_args,
        }

    return cmd_type, cmd_args
