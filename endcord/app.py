import curses
import importlib.util
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

import emoji

from endcord import (
    client_properties,
    color,
    debug,
    discord,
    downloader,
    formatter,
    game_detection,
    gateway,
    log_queue,
    parser,
    peripherals,
    perms,
    rpc,
    search,
    tui,
)
from endcord.assist_data import COMMAND_ASSISTS, SEARCH_HELP_TEXT

support_media = (
    importlib.util.find_spec("PIL") is not None and
    importlib.util.find_spec("av") is not None and
    importlib.util.find_spec("nacl") is not None
)
if support_media:
    from endcord import clipboard
cythonized = importlib.util.find_spec("endcord_cython") and importlib.util.find_spec("endcord_cython.search")
uses_pgcurses = tui.uses_pgcurses

logger = logging.getLogger(__name__)
ENABLE_EXTENSIONS = True
MESSAGE_UPDATE_ELEMENTS = ("id", "content", "mentions", "mention_roles", "mention_everyone", "embeds", "edited")
MEDIA_EMBEDS = ("image", "gifv", "video", "audio", "rich")
STATUS_STRINGS = ("online", "idle", "dnd", "invisible")
ERROR_TEXT = "\nUnhandled exception occurred. Please report here: https://github.com/sparklost/endcord/issues"
MSG_MIN = 3   # minimum number of messages that must be sent in official client
SUMMARY_SAVE_INTERVAL = 300   # 5min
LIMIT_SUMMARIES = 5   # max number of summaries per channel
INTERACTION_THROTTLING = 3   # delay between sending app interactions
APP_COMMAND_AUTOCOMPLETE_DELAY = 0.3   # delay for requesting app command autocompletions after stop typing
MB = 1024 * 1024
USER_UPLOAD_LIMITS = (10*MB, 50*MB, 500*MB, 50*MB)   # premium tier 0, 1, 2, 3 (none, classic, full, basic)
GUILD_UPLOAD_LIMITS = (10*MB, 10*MB, 50*MB, 100*MB)   # premium tier 0, 1, 2, 3
FORUM_COMMANDS = (1, 2, 7, 13, 14, 15, 17, 20, 22, 25, 27, 29, 30, 31, 32, 40, 42, 49, 50, 51, 52, 53, 55, 56, 57, 58, 61, 62, 66, 67, 68, 69, 70)
COLLAPSE_ALL_EXCEPT_OPTIONS = ("current", "selected", "above", "bellow")

match_emoji = re.compile(r"<:(.*):(\d*)>")
match_youtube = re.compile(r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}")


recorder = peripherals.Recorder()


class Endcord:
    """Main app class"""

    def __init__(self, screen, config, keybindings, command_bindings, profiles, version):
        self.screen = screen
        self.config = config
        self.keybindings = keybindings
        self.init_time = time.time()
        self.profiles = profiles

        # select profile
        for profile in profiles["keyring"] + profiles["plaintext"]:
            if profile["name"] == profiles["selected"]:
                self.token = profile["token"]
                self.last_run = profile["time"]
                break
        else:
            profiles = profiles["keyring"] + profiles["plaintext"]
            self.token = profiles[0]["token"]
            self.profiles["selected"] = profiles[0]["name"]
            self.last_run = profiles[0]["time"]

        # load often used values from config
        self.enable_rpc = config["rpc"]
        self.enable_game_detection = config["game_detection"]
        self.limit_chat_buffer = max(min(config["limit_chat_buffer"], 1000), 50)
        self.limit_channel_cache = config["limit_channel_cache"]
        self.msg_num = max(min(config["download_msg"], 100), 20)
        self.limit_typing = max(config["limit_typing_string"], 25)
        self.send_my_typing = config["send_typing"]
        self.ack_throttling = max(config["ack_throttling"], 3)
        self.format_title_line_l = config["format_title_line_l"]
        self.format_title_line_r = config["format_title_line_r"]
        self.format_status_line_l = config["format_status_line_l"]
        self.format_status_line_r = config["format_status_line_r"]
        self.format_title_tree = config["format_title_tree"]
        self.format_rich = config["format_rich"]
        self.reply_mention = config["reply_mention"]
        self.cache_typed = config["cache_typed"]
        self.enable_notifications = config["desktop_notifications"]
        self.notification_sound = config["linux_notification_sound"]
        self.notification_path = config["custom_notification_sound"]
        self.hide_spam = config["hide_spam"]
        self.keep_deleted = config["keep_deleted"]
        self.limit_cache_deleted = config["limit_cache_deleted"]
        self.ping_this_channel = config["notification_in_active"]
        self.username_role_colors = config["username_role_colors"]
        self.save_summaries = config["save_summaries"]
        self.fun = config["easter_eggs"]
        self.tenor_gif_type = config["tenor_gif_type"]
        self.get_members = config["member_list"]
        self.member_list_auto_open = config["member_list_auto_open"]
        self.member_list_width = config["member_list_width"]
        self.use_nick = config["use_nick_when_available"]
        self.status_char = config["tree_dm_status"]
        self.assist_skip_app_command = config["assist_skip_app_command"]
        self.extra_line_delay = config["extra_line_delay"]
        self.assist_limit = config["assist_limit"]
        self.assist_score_cutoff = config["assist_score_cutoff"]
        self.external_editor = config["external_editor"]
        self.limit_command_history = config["limit_command_history"]
        self.remove_prev_notif = ["remove_previous_notification"]
        self.emoji_as_text = config["emoji_as_text"]
        self.show_pending_messages = config["show_pending_messages"]
        self.enable_calls = config["calls"]
        self.game_detection_download_delay = config["game_detection_download_delay"]

        if not self.external_editor or not shutil.which(self.external_editor):
            self.external_editor = os.environ.get("EDITOR", "nano")
            if not shutil.which(self.external_editor):
                self.external_editor = None
        downloads_path = config["downloads_path"]
        if not downloads_path:
            downloads_path = peripherals.downloads_path
        self.downloads_path = os.path.expanduser(downloads_path)
        if self.notification_path:
            self.notification_path = os.path.expanduser(self.notification_path)
        if not support_media:
            config["native_media_player"] = True
        self.colors = color.extract_colors(config)
        self.colors_formatted = color.extract_colors_formatted(config)
        self.default_msg_color = self.colors_formatted[0][0][:]
        self.default_msg_alt_color = self.colors[1]

        # write properties to log
        properties = [peripherals.detect_runtime()]
        if support_media:
            properties.append("ASCII media")
        if cythonized:
            properties.append("cythonized")
        if uses_pgcurses:
            properties.append("windowed")
        if shutil.which(self.config["mpv_path"]):
            properties.append("have mpv")
        if shutil.which(self.config["yt_dlp_path"]):
            properties.append("have yt-dlp")
        logger.info("Properties: " + ", ".join(properties))
        del (properties)

        # variables
        self.run = False
        self.extensions = []
        self.active_channel = {
            "guild_id": None,
            "channel_id": None,
            "guild_name": None,
            "channel_name": None,
            "pinned": False,
            "admin": False,
        }
        self.guilds = []
        self.guild_folders = []
        self.all_roles = []
        self.current_roles = []
        self.current_guild_properties = {}
        self.current_channels = []
        self.current_channel = {}
        self.slowmodes = {}
        self.slowmode_times = {}
        self.slowmode_thread = None
        self.summaries = []
        self.input_store = []
        self.running_tasks = []
        self.cached_downloads = []
        self.tabs_names = []
        self.last_summary_save = time.time() - SUMMARY_SAVE_INTERVAL - 1

        # get client properties
        if config["client_properties"].lower() == "anonymous":
            client_prop = client_properties.get_anonymous_properties()
        else:
            client_prop = client_properties.get_default_properties()
        if config["custom_user_agent"]:
            client_prop = client_properties.add_user_agent(client_prop, config["custom_user_agent"])
        client_prop_gateway = client_properties.add_for_gateway(client_prop)
        self.user_agent = client_prop["browser_user_agent"]
        client_prop = client_properties.encode_properties(client_prop)
        logger.debug(f"User-Agent: {self.user_agent}")

        self.chat = []
        self.chat.insert(0, f"Connecting to {self.config["custom_host"] or "Discord"}")
        logger.info(f"Connecting to {self.config["custom_host"] or "Discord"}")

        if not config["send_x_super_properties"]:
            client_prop = None

        # initialize stuff
        self.discord = discord.Discord(
            self.token,
            config["custom_host"],
            client_prop,
            self.user_agent,
            proxy=config["proxy"],
        )
        # preload chat for faster startup
        self.preloaded = False
        self.need_preload = True
        threading.Thread(target=self.preload_chat, daemon=True).start()
        self.gateway = gateway.Gateway(
            self.token,
            config["custom_host"],
            client_prop_gateway,
            self.user_agent,
            proxy=config["proxy"],
            capablities=config["capabilities"],
        )
        # this takes some time, so let other things init in parallel
        threading.Thread(target=self.gateway.connect, daemon=True).start()
        self.downloader = downloader.Downloader(config["proxy"])
        self.tui = tui.TUI(self.screen, self.config, keybindings, command_bindings)
        if self.fun:
            today = (time.localtime().tm_mon, time.localtime().tm_mday)
            self.fun = 2 if (10, 25) <= today <= (11, 8) else self.fun
            self.fun = 3 if today >= (12, 25) or today <= (1, 8) else self.fun
            self.fun = 4 if today == (4, 1) else self.fun
        self.colors = self.tui.init_colors(self.colors)
        self.colors_formatted = self.tui.init_colors_formatted(self.colors_formatted, self.default_msg_alt_color)
        self.tui.update_chat(self.chat, [[[self.colors[0]]]] * len(self.chat))
        self.tui.update_status_line(" CONNECTING")
        self.my_id = None   # will be taken from gateway in main()
        self.premium = None    # same
        self.my_user_data = None    # same
        self.channel_cache = []
        self.voice_gateway = None
        self.reset()
        self.chat.insert(0, f"Connecting to {self.config["custom_host"] or "Discord"}")
        self.gateway_state = self.gateway.get_state()
        self.chat_dim, self.tree_dim, _  = self.tui.get_dimensions()
        self.state = {
            "last_guild_id": None,
            "last_channel_id": None,
            "muted": False,
            "member_list": True,
            "collapsed": [],
            "folder_names": [],
            "tabbed_channels": [],
            "games_blacklist": [],
        }
        self.tree = []
        self.tree_format = []
        self.tree_metadata = []
        self.uncollapsed_threads = []
        self.my_roles = []
        self.deleted_cache = []
        self.extra_window_open = False
        self.extra_indexes = []
        self.extra_body = []
        self.viewing_user_data = {"id": None, "guild_id": None}
        self.read_state = {}
        self.hidden_channels = []
        self.current_subscribed_members = []
        self.recording = False
        self.assist_word = None
        self.assist_type = None
        self.assist_found = []
        self.restore_input_text = (None, None)
        self.extra_bkp = None
        self.checkpoint = None
        self.command_history = peripherals.load_json("command_history.json", [])
        self.command_history_index = 0
        self.command_history_stored_current = None
        self.show_blocked_messages = False
        self.gateway.set_want_member_list(self.get_members)
        self.gateway.set_want_summaries(self.save_summaries)
        self.timed_extra_line = threading.Event()
        self.log_queue_manager = None
        self.message_send_queue = queue.Queue()
        self.notifications = []
        self.terminal_media = None
        # threading.Thread(target=self.profiling_auto_exit, daemon=True).start()
        self.discord.get_voice_regions()

        # init sigint handler - replaces handler from main.py
        try:
            signal.signal(signal.SIGINT, self.sigint_handler)
        except ValueError:
            pass   # error when ran in pgcurses

        # init extensions
        if config["extensions"] and ENABLE_EXTENSIONS:
            self.load_extensions(version)
            self.tui.load_extensions(self.extensions)
            self.gateway.load_extensions(self.extensions)
        self.main()


    def sigint_handler(self, _signum, _frame):
        """Handling Ctrl-C event"""
        if self.terminal_media:
            self.terminal_media.stop_playback()
        self.gateway.disconnect_ws()
        self.run = False
        try:
            # in case curses.wrapper doesnt restore terminal
            curses.nocbreak()
            curses.echo()
            curses.endwin()
        except curses.error:
            pass
        sys.exit(0)


    def load_extensions(self, version):
        """Load extensions and initialize them"""
        # get extensions
        path = os.path.expanduser(os.path.join(peripherals.config_path, "Extensions"))
        if not os.path.exists(path):
            os.makedirs(os.path.expanduser(path), exist_ok=True)
        extensions, invalid = peripherals.get_extensions(path)

        # load extensions
        log_text = []
        log_text_invalid = []
        for ext_file, ext_name in extensions:
            ext_dir = os.path.dirname(os.path.abspath(ext_file))
            original_path = list(sys.path)
            sys.path.insert(0, ext_dir)   # add extension dir to sys.path to load their modules
            try:
                spec = importlib.util.spec_from_file_location(ext_name, ext_file)
                if not spec or not spec.loader:
                    log_text_invalid.append(f"  {ext_name} - ERROR: Cannot load spec")
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                ext_class = getattr(module, "Extension", None)
                ext_app_version = getattr(module, "EXT_ENDCORD_VERSION", None)
                ext_version = getattr(module, "EXT_VERSION", None)
                if not callable(ext_class):
                    log_text_invalid.append(f"  {ext_name} - ERROR: Extension class is invalid")
                    continue
                ext_command_assist = getattr(module, "EXT_COMMAND_ASSIST", None)
                if ext_command_assist:   # merge assist data
                    global COMMAND_ASSISTS
                    COMMAND_ASSISTS += ext_command_assist
                instance = ext_class(self)
                self.extensions.append(instance)
                if ext_app_version.split(".")[:2] != version.split(".")[:2]:
                    log_text.append(f"  {ext_name} {ext_version} - WARNING: This extension is built for different endcord version!")
                else:
                    log_text.append(f"  {ext_name} {ext_version} - OK")
            except Exception as e:
                log_text_invalid.append(f"  {ext_name} - ERROR: {e}")
            finally:   # restore old sys.path
                sys.path[:] = original_path

        # log stuff
        for ext_file, ext_name in invalid:
            log_text_invalid.append(f"  {ext_name} - ERROR: Invalid extension structure")
        if log_text:
            logger.info(f"Loaded {len(self.extensions)} extensions:\n" + "\n".join(log_text))
        if log_text_invalid:
            logger.warning("Invalid extensions:\n" + "\n".join(log_text_invalid))
        self.chat.insert(0, f"Successfully loaded {len(self.extensions)} extensions")
        self.chat.insert(0, f"Not loaded (invalid) {len(extensions) - len(self.extensions) + len(invalid)} extensions")
        self.tui.update_chat(self.chat, [[[self.colors[0]]]] * len(self.chat))
        self.extension_cache = []


    def execute_extensions_methods(self, method_name, *args, cache=False):
        """Execute specific method for each extension if extension has this method, and chain them"""
        if not self.extensions:
            return args

        # try to load from cache (improves performance with many extensions)
        if cache:
            for extension_point in self.extension_cache:
                data = args
                if extension_point[0] == method_name:
                    for method in extension_point[1]:
                        result = method(*data)
                        if result is not None:
                            if not isinstance(result, tuple):
                                result = (result, )
                            data = result
                    if data is not None:
                        return data
                    return args

        # try to load method from extensions and add to cache
        methods = []
        data = args
        for extension in self.extensions:
            method = getattr(extension, method_name, None)
            if callable(method):
                if cache:
                    methods.append(method)
                result = method(*data)
                if result is not None:
                    if not isinstance(result, tuple):
                        result = (result, )
                    data = result
        if cache:
            self.extension_cache.append((method_name, methods))
        if data is not None:
            return data
        return args


    def execute_extensions_method_first(self, method_name, *args, cache=False):
        """Execute specific method for each extension if extension has this method, and chain them, without chaining, stop on first run extension"""
        if not self.extensions:
            return None

        # try to load from cache (improves performance with many extensions)
        if cache:
            result = False
            for extension_point in self.extension_cache:
                if extension_point[0] == method_name:
                    for method in extension_point[1]:
                        result = method(*args)
                        if result:
                            return result

        # try to load method from extensions and add to cache
        result = False
        methods = []
        for extension in self.extensions:
            method = getattr(extension, method_name, None)
            if callable(method):
                if cache:
                    methods.append(method)
                result = method(*args)
                if result:
                    break
        if cache:
            self.extension_cache.append((method_name, methods))
        return result


    def profiling_auto_exit(self):
        """Thread that waits then exits cleanly, so profiler (vprof) can process data"""
        time.sleep(20)
        self.run = False


    def message_sender(self):
        """Thread that takes message create/edit/remove api requests from queue and executes them one-by-one"""
        while self.run:
            try:
                func, args, kwargs = self.message_send_queue.get()
                success = func(*args, **kwargs)
                if success is None:
                    self.gateway.set_offline()
                    self.update_extra_line("Network error.")
                    while self.run:   # drain remaining tasks
                        try:
                            self.message_send_queue.get_nowait()
                        except queue.Empty:
                            break
            except Exception:
                pass


    def put_to_message_sender(self, func, *args, **kwargs):
        """Put method to queue with its args and kwargs"""
        self.message_send_queue.put((func, args, kwargs))


    def extra_line_remover(self):
        """Thread that removes extra line after specific time"""
        while self.run:
            self.timed_extra_line.wait()
            time.sleep(self.extra_line_delay)
            self.update_extra_line()
            self.timed_extra_line.clear()


    def wait_slowmode(self):
        """Thread that times slowmode and updates extra line"""
        while self.slowmodes and self.slowmode_times:
            active_channel = self.active_channel["channel_id"]
            all_times = 0
            for key, val in list(self.slowmode_times.items()):
                all_times += val
                if val <= 0 and key != active_channel:
                    self.slowmode_times.pop(key)
                    self.slowmodes.pop(key)
                elif val > 0:
                    self.slowmode_times[key] = val - 1
            if not all_times:
                break
            status = "%slowmode" in self.format_status_line_r or "%slowmode" in self.format_status_line_l
            title = "%slowmode" in self.format_title_line_r or "%slowmode" in self.format_title_line_l
            tree = "%slowmode" in self.format_title_tree
            self.update_status_line(status=status, title=title, tree=tree)
            time.sleep(1)


    def reset(self, online=False):
        """Reset stored data from discord, should be run on startup and reconnect"""
        if not self.preloaded:
            self.messages = []
        self.session_id = None
        self.chat = []
        self.chat_format = []
        self.tab_string = ""
        self.tab_string_format = []
        self.new_unreads = False
        self.this_unread = False
        self.chat_map = []
        if self.my_user_data:
            self.update_prompt()
        self.typing = []
        self.typing_sent = int(time.time())
        self.sent_ack_time = time.time() - self.ack_throttling
        self.pending_acks = []
        self.last_message_id = 0
        self.my_activities = []
        self.chat_end = False
        self.forum_end = False
        self.forum_old = []
        self.downloader.cancel()
        self.download_threads = []
        self.upload_threads = []
        self.ready_attachments = {}
        self.selected_attachment = 0
        self.current_my_roles = []
        self.member_roles = []
        self.current_member_roles = []
        self.threads = []
        self.activities = []
        self.search_messages = []
        self.members = []
        self.subscribed_members = []
        self.member_list = []
        self.got_commands = False
        self.my_commands = []
        self.my_apps = []
        self.guild_commands = []
        self.guild_apps = []
        self.guild_commands_permitted = []
        self.pinned = []
        self.missing_members_nonce = None
        self.forum = False
        self.disable_sending = False
        self.extra_line = None
        self.permanent_extra_line = None
        self.search = False
        self.search_end = False
        self.search_gif = False
        self.command = False
        self.app_command_autocomplete = ""
        self.app_command_autocomplete_resp = []
        self.app_command_sent_time = time.time()
        self.app_command_last_keypress = time.time()
        self.allow_app_command_autocomplete = False
        self.ignore_typing = False
        self.incoming_calls = []
        self.most_recent_incoming_call = None
        self.joining_call = False
        if self.voice_gateway:
            self.voice_gateway.disconnect()
        self.voice_gateway = None
        self.in_call = None
        self.call_participants = []
        self.voice_call_list_open = False
        self.ringer = None
        if not online:
            self.my_status = {
                "status": "online",
                "custom_status": None,
                "custom_status_emoji": None,
                "activities": [],
                "afk": False,
                "client_state": "OFFLINE",
            }
        for num, _ in enumerate(self.channel_cache):
            if self.channel_cache[num][2]:
                if len(self.channel_cache[num]) < 4:
                    self.channel_cache[num].append(True)
                else:
                    self.channel_cache[num][3] = True   # mark as invalid
            else:
                _ = self.channel_cache.pop(num)


    def reconnect(self):
        """Fetch updated data from gateway and rebuild chat after reconnecting"""
        self.add_running_task("Reconnecting", 1)
        self.reset(online=True)
        self.premium = self.gateway.get_premium()
        guilds = self.gateway.get_guilds()
        if guilds:
            self.guilds = guilds
        # not initializing role colors again to avoid issues with media colors
        self.load_dms()
        new_activities = self.gateway.get_dm_activities()
        if new_activities:
            self.activities = new_activities
            self.update_tree()
        self.read_state = self.gateway.get_read_state()
        self.blocked = self.gateway.get_blocked()
        self.select_current_member_roles()
        self.my_roles = self.gateway.get_my_roles()
        self.current_my_roles = []   # user has no roles in dm
        for roles in self.my_roles:
            if roles["guild_id"] == self.active_channel["guild_id"]:
                self.current_my_roles = roles["roles"]
                break
        self.compute_permissions()
        self.select_current_channels()
        self.gateway.update_presence(
            self.my_status["status"],
            custom_status=self.my_status["custom_status"],
            custom_status_emoji=self.my_status["custom_status_emoji"],
            activities=self.my_activities,
            afk=self.my_status["afk"],
        )

        new_messages = self.get_messages_with_members()
        if new_messages is not None:
            self.messages = new_messages
            if self.messages:
                self.last_message_id = self.get_chat_last_message_id()

        self.typing = []
        self.chat_end = False
        self.forum_end = False
        self.forum_old = []
        self.gateway.subscribe(
            self.active_channel["channel_id"],
            self.active_channel["guild_id"],
        )
        self.session_id = self.gateway.session_id

        self.execute_extensions_methods("on_reconnect")

        self.update_chat(keep_selected=False)
        self.update_tree()

        self.remove_running_task("Reconnecting", 1)
        logger.info("Reconnect complete")


    def load_dms(self):
        """Load dms and remove spam"""
        self.dms, self.dms_vis_id = self.gateway.get_dms()
        if self.hide_spam:
            for dm in self.dms:
                if dm["is_spam"]:
                    self.dms_vis_id.remove(dm["id"])
                    self.dms.remove(dm)


    def switch_channel(self, channel_id, channel_name, guild_id, guild_name, parent_hint=None, preload=False, delay=False):
        """
        All that should be done when switching channel.
        If it is DM, guild_id and guild_name should be None.
        """
        # dont switch to same channel
        if channel_id == self.active_channel["channel_id"]:
            return

        # dont switch when offline
        if self.my_status["client_state"] in ("OFFLINE", "connecting"):
            self.update_extra_line("Can't switch channel when offline.", timed=False)
            return

        logger.debug(f"Switching channel, has_id: {bool(channel_id)}, has_guild: {bool(guild_id)}, has hint: {bool(parent_hint)}")

        # stop log watcher so it doesnt interfere with chat generation
        if self.log_queue_manager:
            self.log_queue_manager.stop()
            self.log_queue_manager = None

        # save deleted
        if self.keep_deleted:
            self.cache_deleted()

        # check if should open member list
        open_member_list = (
            self.member_list_auto_open and guild_id != self.active_channel["guild_id"] and
            self.screen.getmaxyx()[1] - self.config["tree_width"] - self.member_list_width - 2 >= 32
        )

        # clear member roles when switching guild so there are no issues with same members in both guilds
        if guild_id != self.active_channel["guild_id"]:
            self.current_member_roles = []

        # cache previous channel chat (if not forum)
        if not self.forum and self.messages:
            self.add_to_channel_cache(self.active_channel["channel_id"], self.messages, self.active_channel.get("pinned", False))

        # remove unread line for previous channel only if set as seen
        if self.active_channel["channel_id"] and self.active_channel["channel_id"] in self.read_state:
            channel = self.read_state.get(channel_id)
            if not channel or channel["last_message_id"] == channel["last_acked_message_id"]:
                self.read_state[self.active_channel["channel_id"]]["last_acked_unreads_line"] = None

        # update active channel
        self.active_channel["guild_id"] = guild_id
        self.active_channel["guild_name"] = guild_name
        self.active_channel["channel_id"] = channel_id
        self.active_channel["channel_name"] = channel_name
        self.active_channel["pinned"] = False
        self.add_running_task("Switching channel", 1)

        # run extensions
        self.execute_extensions_methods("on_switch_channel_start")

        this_guild = self.select_current_channels(parent_hint)

        # generate forum
        if self.current_channel.get("type") == 15:
            forum = True
            self.forum_end = False
            self.forum_old = []
            self.get_forum_chunk(force=True)

        # fetch messages or load them from cache
        else:
            forum = False

            # check cache
            from_cache = self.load_from_channel_cache(channel_id)

            # use preloaded
            if not from_cache and preload and self.preloaded:
                self.request_missing_members(guild_id, self.messages)
                self.last_message_id = self.get_chat_last_message_id()
                self.preloaded = False
                self.need_preload = False

            # download messages
            elif not from_cache:
                new_messages = self.get_messages_with_members(num=self.msg_num)
                if new_messages is not None:
                    self.messages = new_messages
                    self.last_message_id = self.get_chat_last_message_id()
                else:
                    self.remove_running_task("Switching channel", 1)
                    logger.warning("Channel switching failed")
                    return

        # if this is dm, check if user has sent minimum number of messages
        # this is to prevent triggering discords spam filter
        if not guild_id and len(self.messages) < self.msg_num:
            # if there is less than self.msg_num messages, this is the start of conversation
            # so count all messages sent from this user
            my_messages = 0
            for message in self.messages:
                if message["user_id"] == self.my_id:
                    my_messages += 1
                    if my_messages >= MSG_MIN:
                        break
            if my_messages < MSG_MIN:
                self.disable_sending = f"Can't send a message: send at least {MSG_MIN} messages with the official client."

        # if this is thread and is locked or archived, prevent sending messages
        elif self.current_channel.get("type") in (11, 12) and self.current_channel.get("locked"):
            self.disable_sending = "Can't send a message: this thread is locked."
        elif not self.current_channel.get("allow_write", True) and not forum:
            self.disable_sending = "Can't send a message: No write permissions."
        else:
            self.disable_sending = False
            self.update_extra_line()

        # find where to scroll chat to show last seen message
        if not forum:
            last_acked_msg = None
            channel = self.read_state.get(channel_id)
            if channel and channel["last_message_id"] and int(channel["last_acked_message_id"]) < int(channel["last_message_id"]):
                last_acked_msg = int(channel["last_acked_message_id"])
            if last_acked_msg:
                for num, message in enumerate(self.messages):
                    if int(message["id"]) <= last_acked_msg:
                        select_message_index = min(max(num - 1, 0), len(self.messages) - 2)
                        break
                else:
                    select_message_index = len(self.messages) - 2
            else:
                select_message_index = None
        else:
            select_message_index = None
        self.this_unread = select_message_index is not None

        # misc
        self.typing = []
        self.active_channel["admin"] = this_guild.get("admin", False)
        self.chat_end = False
        self.got_commands = False
        self.selected_attachment = 0
        self.gateway.subscribe(channel_id, guild_id)
        self.tui.reset_chat_scrolled_top()
        self.gateway.set_subscribed_channels([x[0] for x in self.channel_cache] + [channel_id])
        if self.recording:
            self.recording = False
            _ = recorder.stop()

        # check for call popups
        if self.incoming_calls and not self.in_call:
            self.most_recent_incoming_call = None
            if channel_id in self.incoming_calls:
                new_permanent_extra_line = None
                if self.most_recent_incoming_call:
                    incoming_call_ch_id = self.most_recent_incoming_call
                else:
                    incoming_call_ch_id = channel_id
                for dm in self.dms:
                    if dm["id"] == incoming_call_ch_id:
                        new_permanent_extra_line = formatter.generate_extra_line_ring(
                            dm["name"],
                            self.tui.get_dimensions()[2][1],
                        )
                        break
                if new_permanent_extra_line and new_permanent_extra_line != self.permanent_extra_line:
                    self.update_extra_line(custom_text=new_permanent_extra_line, permanent=True)
            elif not self.in_call:
                self.update_extra_line(permanent=True)

        # select guild member list and subscribed
        if guild_id:
            if self.get_members:
                for guild in self.members:
                    if guild[0] == guild_id:
                        if "everyone" in guild[1]:
                            self.member_list = guild[1]["everyone"][1]
                        elif guild[1]:
                            self.member_list = next(iter(guild[1].values()))[1]   # fix_member_list_selection
                        else:
                            self.member_list = []
                        break
                else:
                    self.member_list = []
            for guild in self.subscribed_members:
                if guild["guild_id"] == guild_id:
                    self.current_subscribed_members = guild["members"]
                    break
                else:
                    self.current_subscribed_members = []

        # manage roles
        if guild_id:   # for guilds only
            # 255_curses_bug - make it run on init only
            self.all_roles = self.tui.init_role_colors(
                self.all_roles,
                self.default_msg_color[1],
                self.default_msg_alt_color[1],
                guild_id=guild_id,
            )
        self.current_roles = []   # dm has no roles
        for roles in self.all_roles:
            if roles["guild_id"] == guild_id:
                self.current_roles = roles["roles"]
                break
        self.current_my_roles = []   # user has no roles in dm
        for roles in self.my_roles:
            if roles["guild_id"] == guild_id:
                self.current_my_roles = roles["roles"]
                break
        self.select_current_member_roles()
        self.forum = forum   # changing it here because previous code takes long time

        # run extensions
        self.execute_extensions_methods("on_switch_channel_end")

        # update UI
        if not forum:
            self.update_chat(keep_selected=False, select_message_index=select_message_index, select_unread=True)
        else:
            self.tui.update_chat(self.chat, self.chat_format)
        self.set_channel_seen(channel_id, self.get_chat_last_message_id(), force_remove_notify=True)   # right after update_chat so new_unreads is determined
        if not guild_id:   # no member list in dms
            self.tui.remove_member_list()
        elif self.state.get("member_list") or open_member_list:
            if delay:
                time.sleep(0.01)   # needed when startup to fix issues with emojis and border lines
            self.update_member_list(reset=True)
            self.gateway.set_want_member_list(True)
            self.state["member_list"] = True
        self.close_extra_window()
        if self.disable_sending:
            self.update_extra_line(self.disable_sending, timed=False)
        else:
            self.update_extra_line()
        self.update_tabs(no_redraw=True)
        self.update_prompt()
        self.update_tree()

        # save state (exclude threads)
        if self.config["remember_state"] and self.current_channel.get("type") not in (11, 12, 15):
            self.state["last_guild_id"] = guild_id
            self.state["last_channel_id"] = channel_id
            peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")

        self.remove_running_task("Switching channel", 1)
        logger.debug("Channel switching complete")


    def blank_chat(self):
        """Switch to None mode, no open channel, no chat displayed"""
        if self.keep_deleted:
            self.cache_deleted()
        self.current_member_roles = []
        if not self.forum and self.messages:
            self.add_to_channel_cache(self.active_channel["channel_id"], self.messages, self.active_channel.get("pinned", False))

        self.active_channel = {
            "guild_id": None,
            "channel_id": None,
            "guild_name": None,
            "channel_name": None,
            "pinned": False,
            "admin": False,
        }
        self.forum = False
        self.last_message_id = 0
        self.preloaded = False
        self.need_preload = False
        self.messages = []
        self.current_guild_properties = {}
        self.current_channels = []
        self.current_channel = {}
        self.disable_sending = False
        self.typing = []
        self.chat_end = False
        self.got_commands = False
        self.selected_attachment = 0
        self.tui.reset_chat_scrolled_top()

        if self.recording:
            self.recording = False
            _ = recorder.stop()

        self.member_list = []
        self.current_roles = []
        self.current_my_roles = []
        self.current_member_roles = []

        self.chat = []
        self.chat_format = []
        self.chat_map = []

        self.tui.update_chat(self.chat, self.chat_format)
        self.tui.remove_member_list()
        self.update_extra_line()
        self.close_extra_window()
        self.update_tabs(no_redraw=True)
        self.update_prompt()
        self.update_tree()


    def open_guild(self, guild_id, select=False, restore=False, open_only=False, one_open=False, dont_close_this=False, direction=0):
        """When opening guild in tree"""
        # check in tree_format if it should be un-/collapsed
        collapse = False
        for num, obj in enumerate(self.tree_metadata):
            if obj and obj["id"] == guild_id:
                collapse = bool(self.tree_format[num] % 10)   # get first digit
                break

        # dont collapse if it should stay open
        if open_only and collapse:
            collapsed = self.state["collapsed"][:]
            folder_changed = False
            for folder in self.guild_folders:
                if guild_id in folder["guilds"]:
                    if folder["id"] in collapsed:
                        collapsed.remove(folder["id"])
                        folder_changed = True
                    break
            if folder_changed:
                self.update_tree(collapsed=collapsed)
            return collapsed

        one_open |= self.config["only_one_open_server"]

        # keep dms, collapsed and all guilds except one at cursor position
        # copy dms
        if 0 in self.state["collapsed"]:
            collapsed = [0]
        else:
            collapsed = []
        guild_ids = []
        passed = False
        if one_open:
            # collapse all other guilds
            for obj in self.tree_metadata:
                if obj and obj["type"] < 0:   # guilds and folders
                    obj_id = obj["id"]
                    guild_ids.append(obj_id)
                    if obj_id == guild_id:
                        passed = True
                        if collapse and not dont_close_this:
                            collapsed.append(guild_id)
                        continue
                    if direction == 0 or (direction == -1 and not passed) or (direction == 1 and passed):
                        collapsed.append(obj_id)
            # copy already collapsed categories
            for collapsed_id in self.state["collapsed"]:
                if collapsed_id not in collapsed:
                    collapsed.append(collapsed_id)
        elif restore:
            # copy all
            collapsed = self.state["collapsed"][:]
        # toggle only this guild
        elif collapse and guild_id not in self.state["collapsed"]:
            collapsed = self.state["collapsed"][:]
            collapsed.append(guild_id)
        elif not collapse and guild_id in self.state["collapsed"]:
            collapsed = self.state["collapsed"][:]
            collapsed.remove(guild_id)

        # check for folder that should be uncollapsed
        folder_changed = False
        for folder in self.guild_folders:
            if guild_id in folder["guilds"]:
                if folder["id"] in collapsed:
                    collapsed.remove(folder["id"])
                    folder_changed = True
                break

        self.update_tree(collapsed=collapsed)

        # keep this guild selected
        if one_open and select:
            for tree_pos, obj in enumerate(self.tree_metadata):
                if obj and obj["id"] == guild_id:
                    break
            self.tui.tree_select(tree_pos)

        return folder_changed


    def select_current_channels(self, parent_hint=None, refresh=False):
        """Select current channels and current channel objects and update things related to them"""
        # update list of channels
        guild_id = self.active_channel["guild_id"]
        channel_id = self.active_channel["channel_id"]

        if refresh:
            parent_hint = self.current_channel.get("parent_id")

        for this_guild in self.guilds:
            if this_guild["guild_id"] == guild_id:
                self.current_channels = this_guild["channels"]
                break
        else:
            self.current_channels = []
            this_guild = {}

        # update channel
        self.current_channel = {}
        for channel in self.current_channels:
            if channel["id"] == channel_id:
                self.current_channel = channel
                break

        # check threads if no channel
        else:
            if parent_hint:   # thread will have parent_hint
                for guild in self.threads:
                    if guild["guild_id"] == guild_id:
                        for channel in guild["channels"]:
                            if channel["channel_id"] == parent_hint:
                                for thread in channel["threads"]:
                                    if thread["id"] == channel_id:
                                        self.current_channel = thread
                                        break
                                break
                        break

        # update current guild properties
        if this_guild:
            self.current_guild_properties = {
                "owned": this_guild["owned"],
                "community": this_guild["community"],
                "premium": this_guild["premium"],
            }
        else:
            self.current_guild_properties = {}

        # update slowmode
        slowmode = self.current_channel.get("rate_limit")
        if slowmode and not this_guild.get("admin", False) and not self.current_channel.get("bypass_slowmode"):
            self.slowmodes[channel_id] = slowmode
            if self.slowmode_times.get(channel_id) is None:
                self.slowmode_times[channel_id] = 0

        return this_guild


    def select_current_member_roles(self):
        """Select member roles for currently active guild and check for missing primary role colors"""
        if not self.active_channel["guild_id"]:
            self.current_member_roles = []
            return
        for guild in self.member_roles:
            if guild["guild_id"] == self.active_channel["guild_id"]:
                if self.username_role_colors:
                    # add my roles if missing
                    for member in guild["members"]:
                        if member["user_id"] == self.my_id:
                            break
                    else:
                        guild["members"].append({
                            "user_id": self.my_id,
                            "roles": self.current_my_roles,
                        })
                    # select colors
                    for member in guild["members"]:
                        if "primary_role_color" not in member:
                            member_roles = member["roles"]
                            for role in self.current_roles:
                                if role["id"] in member_roles:
                                    member["primary_role_color"] = role.get("color_id")
                                    member["primary_role_alt_color"] = role.get("alt_color_id")
                                    break
                self.current_member_roles = guild["members"]
                break
        else:
            self.current_member_roles = []


    def add_to_store(self, channel_id, text):
        """Adds entry to input line store"""
        if self.cache_typed:
            for num, channel in enumerate(self.input_store):
                if channel["id"] == channel_id:
                    self.input_store[num]["content"] = text
                    self.input_store[num]["index"] = self.tui.input_index
                    break
            else:
                self.input_store.append({
                    "id": channel_id,
                    "content": text,
                    "index": self.tui.input_index,
                })


    def add_to_command_history(self, command):
        """Add command to command history and limit its size"""
        if not self.command_history or self.command_history[-1] != command:
            self.command_history.append(command)
            if len(self.command_history) > self.limit_command_history:
                self.command_history.pop(0)
                if self.command_history_index:
                    self.command_history_index -= 1
            peripherals.save_json(self.command_history, "command_history.json")


    def add_to_channel_cache(self, channel_id, messages, set_pinned):
        """Add messages to channel cache"""
        # format: channel_cache = [[channel_id, messages, pinned, *invalid], ...]
        # skipping deleted because they are separately cached
        if self.limit_channel_cache:
            pinned = 0
            for channel in self.channel_cache:
                if channel[2]:
                    pinned += 1
            if pinned >= self.limit_channel_cache:   # skip if all are pinned
                return
            messages = [x for x in messages if not x.get("deleted")]
            if self.get_chat_last_message_id() == self.last_message_id:   # only keep latest messages
                messages = messages[:self.msg_num]
            else:
                messages = []
            for num, channel in enumerate(self.channel_cache):
                if channel[0] == channel_id:
                    self.channel_cache[num] = [channel_id, messages, set_pinned]
                    break
            else:
                self.channel_cache.append([channel_id, messages, set_pinned])
                if len(self.channel_cache) > self.limit_channel_cache:
                    for num, channel in enumerate(self.channel_cache):
                        if not channel[2]:   # dont remove pinned
                            self.channel_cache.pop(num)
                            break


    def load_from_channel_cache(self, channel_id):
        """Load messages from channel cache"""
        if not self.limit_channel_cache:
            return False
        for num, channel in enumerate(self.channel_cache):
            if channel[0] == channel_id and not (len(channel) > 3 and channel[3]):
                break
        else:
            return False
        if self.channel_cache[num][2]:
            cached = self.channel_cache[num]
        else:
            cached = self.channel_cache.pop(num)
        if cached[1] and len(cached[1]) >= self.msg_num - 10:
            self.messages = cached[1]
        else:
            new_messages = self.get_messages_with_members(num=self.msg_num)
            if new_messages is None:
                return True
            self.messages = new_messages
        self.active_channel["pinned"] = cached[2]

        if self.messages:
            self.last_message_id = self.get_chat_last_message_id()

        # restore deleted
        if self.keep_deleted and self.messages:
            self.messages = self.restore_deleted(self.messages)

        # find and request missing member roles
        self.select_current_member_roles()
        self.request_missing_members(
            self.active_channel["guild_id"],
            self.messages,
        )
        return True


    def remove_channel_cache(self, num=None, active=False):
        """Remove cached channel"""
        if active and num is None:
            for num_cache, channel in enumerate(self.channel_cache):
                if channel[0] == self.active_channel["channel_id"]:
                    num = num_cache
                    break
        if num is not None:
            try:
                self.channel_cache.pop(num)
            except IndexError:
                pass


    def toggle_tab(self):
        """Toggle tabbed state of currently active channel"""
        if not self.forum:
            if self.active_channel.get("pinned"):
                self.active_channel["pinned"] = False
                self.remove_channel_cache(active=True)
            else:
                pinned = 0
                for channel in self.channel_cache:
                    if channel[2]:
                        pinned += 1
                if pinned >= self.limit_channel_cache:   # if all are pinned
                    self.update_extra_line("Can't add tab: channel cache limit reached.")
                else:
                    self.active_channel["pinned"] = True
            if self.config["remember_tabs"]:
                tabbed_channels = [channel[0] for channel in self.channel_cache if channel[2]]
                if self.active_channel["pinned"]:
                    tabbed_channels.append(self.active_channel["channel_id"])
                self.state["tabbed_channels"] = tabbed_channels
                peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")
            self.update_tabs(add_current=True)


    def set_dm_first(self, dm_id, update_tree=True):
        """Set specified dm to be first it tree"""
        for i, channel in enumerate(self.dms):
            if channel.get("id") == dm_id:
                if i == 0:
                    return False
                self.dms.insert(0, self.dms.pop(i))
                if update_tree:
                    self.update_tree()
                return True
        return False


    def reset_states(self, replying=False, reacting=True):
        """Reset all states except replying"""
        if replying:
            self.replying = {
                "id": None,
                "username": None,
                "global_name": None,
                "mention": None,
            }
        self.editing = None
        self.deleting = None
        self.downloading_file = {
            "urls": None,
            "web": False,
            "open": False,
        }
        self.cancel_download = False
        self.uploading = False
        self.hiding_ch = {
            "channel_name": None,
            "channel_id": None,
            "guild_id": None,
        }
        if reacting:
            self.reacting = {
                "id": None,
                "msg_index": None,
                "username": None,
                "global_name": None,
            }
        self.view_reactions = {
            "message_id": None,
            "reactions": [],
        }
        self.going_to_ch = None
        self.ignore_typing = False
        self.tui.typing = time.time() - 5
        self.update_status_line()


    def add_running_task(self, task, priority=5):
        """Add currently running long task with priority (lower number = higher priority)"""
        self.running_tasks.append([task, priority])
        self.update_status_line()


    def remove_running_task(self, task, priority):
        """Remove currently running long task"""
        try:
            self.running_tasks.remove([task, priority])
            self.update_status_line()
        except ValueError:
            pass


    def channel_name_from_id(self, channel_id):
        """Get channel name from its id"""
        for channel in self.current_channels:
            if channel["id"] == channel_id:
                return channel["name"]
        return None


    def tree_pos_from_id(self, object_id):
        """Get object position in tree from its id"""
        for tree_pos, obj in enumerate(self.tree_metadata):
            if obj and obj["id"] == object_id:
                return tree_pos


    def wait_input(self, forced_binding=None):
        """Thread that handles: getting input, formatting, sending, replying, editing, deleting message and switching channel"""
        logger.info("Input handler loop started")
        ephemeral = False

        while self.run and not ephemeral:
            if self.reacting["id"]:
                self.restore_input_text = (self.restore_input_text[0], "react")
            if forced_binding:   # externally forced binding
                init_text = self.restore_input_text[0]
                self.stop_extra_window()
                input_text, chat_sel, tree_sel, action = self.tui.wait_input(self.prompt, init_text=init_text, reset=False, keep_cursor=True, forum=self.forum, press=forced_binding)
                ephemeral = True   # stop loop
            elif self.restore_input_text[1] == "prompt":
                self.stop_extra_window()
                self.restore_input_text = (None, "after prompt")
                input_text, chat_sel, tree_sel, action = self.tui.wait_input(self.prompt, forum=self.forum)
            elif self.restore_input_text[1] in ("standard", "standard extra", "standard insert"):
                keep_cursor = True
                if self.restore_input_text[1] == "standard" and not self.restore_input_text[0].startswith("/"):
                    self.stop_extra_window()
                elif self.restore_input_text[1] == "standard insert":
                    keep_cursor = False
                    self.stop_extra_window()
                init_text = self.restore_input_text[0]
                self.restore_input_text = (None, None)
                input_text, chat_sel, tree_sel, action = self.tui.wait_input(self.prompt, init_text=init_text, reset=False, keep_cursor=keep_cursor, forum=self.forum)
            elif self.restore_input_text[1] == "autocomplete":
                init_text = self.restore_input_text[0]
                self.restore_input_text = (None, None)
                input_text, chat_sel, tree_sel, action = self.tui.wait_input(self.custom_prompt("PATH"), init_text=init_text, autocomplete=True, forum=self.forum)
            elif self.restore_input_text[1] in ("search", "command", "react", "edit"):
                init_text = self.restore_input_text[0]
                prompt_text = self.restore_input_text[1]
                if self.search_gif and prompt_text == "search":
                    prompt_text = "gif search"
                prompt_text = prompt_text.upper()
                command = self.restore_input_text[1] == "command"
                self.restore_input_text = (None, None)
                input_text, chat_sel, tree_sel, action = self.tui.wait_input(self.custom_prompt(prompt_text), init_text=init_text, forum=self.forum, command=command)
            else:
                restore_text = None
                input_index = 0
                if self.cache_typed:
                    active_channel = self.active_channel["channel_id"]
                    for num, channel in enumerate(self.input_store):
                        if channel["id"] == active_channel:
                            restore_text = self.input_store[num]["content"]
                            input_index = self.input_store.pop(num)["index"]
                            break
                if restore_text:
                    self.tui.update_prompt(self.prompt)
                    self.tui.input_buffer = restore_text
                    self.tui.set_input_index(input_index)
                    input_text, chat_sel, tree_sel, action = self.tui.wait_input(self.prompt, init_text=restore_text, keep_cursor=True, reset=False, clear_delta=True, forum=self.forum)
                else:
                    input_text, chat_sel, tree_sel, action = self.tui.wait_input(self.prompt, clear_delta=True, forum=self.forum)
            logger.debug(f"Input code: {action}")

            # switch channel
            if action == 4:
                if input_text and input_text != "\n":
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                sel_channel = self.tree_metadata[tree_sel]
                guild_id, parent_id, guild_name = self.find_parents_from_tree(tree_sel)
                self.switch_channel(sel_channel["id"], sel_channel["name"], guild_id, guild_name, parent_hint=parent_id)
                self.reset_states(replying=True)
                self.update_status_line()

            # reply
            elif action == 1 and self.messages:
                self.reset_states()
                self.restore_input_text = (input_text, "standard")
                self.update_status_line()
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                message = self.messages[msg_index]
                if "deleted" not in message and "pending" not in message:
                    if message["user_id"] == self.my_id:
                        mention = None
                    else:
                        mention = self.reply_mention
                    self.replying = {
                        "id": message["id"],
                        "username": message["username"],
                        "global_name": message["global_name"],
                        "mention": mention,
                    }

            # edit
            elif action == 2 and self.messages:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                message = self.messages[msg_index]
                if message["user_id"] == self.my_id and "deleted" not in message and "pending" not in message:
                    self.reset_states()
                    self.editing = message["id"]
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (emoji.demojize(message["content"]), "edit")
                    self.update_status_line()

            # delete
            elif action == 3 and self.messages:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                message = self.messages[msg_index]
                if (message["user_id"] == self.my_id or self.active_channel["admin"]) and "deleted" not in message and "pending" not in message:
                    self.reset_states()
                    self.ignore_typing = True
                    self.deleting = message["id"]
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "prompt")
                    self.update_status_line()

            # toggle mention ping
            elif action == 6:
                self.restore_input_text = (input_text, "standard")
                self.replying["mention"] = None if self.replying["mention"] is None else not self.replying["mention"]
                self.update_status_line()

            # warping to chat bottom
            elif action == 7 and self.messages:
                self.restore_input_text = (input_text, "standard")
                self.go_bottom()

            # go to replied message
            elif action == 8 and self.messages:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                self.go_replied(msg_index)

            # download file
            elif action == 9:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                embeds = []
                for embed in self.messages[msg_index]["embeds"]:
                    if embed["url"]:
                        embeds.append(embed["url"])
                selected_urls = []
                urls = self.get_msg_urls_chat(msg_index)
                for num in self.get_stuff_from_selected_line(chat_sel, 0):   # get url
                    if urls[num] in embeds:
                        selected_urls.append(urls[num])
                if len(selected_urls) == 1:
                    self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(selected_urls[0], )))
                    self.download_threads[-1].start()
                else:
                    self.ignore_typing = True
                    self.downloading_file = {
                        "urls": selected_urls,
                        "web": False,
                        "open": False,
                    }
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "prompt")
                    self.update_status_line()

            # open link in browser
            elif action == 10:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                selected_urls = []
                urls = self.get_msg_urls_chat(msg_index)
                for num in self.get_stuff_from_selected_line(chat_sel, 0):   # get url
                    selected_urls.append(urls[num])
                if len(selected_urls) == 1:
                    selected_url = self.refresh_attachment_url(selected_urls[0])
                    webbrowser.open(selected_url, new=0, autoraise=True)
                elif selected_urls:
                    self.ignore_typing = True
                    self.downloading_file = {
                        "urls": selected_urls,
                        "web": True,
                        "open": False,
                    }
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "prompt")
                    self.update_status_line()
                else:
                    self.restore_input_text = (input_text, "standard")

            # view media
            elif action == 17:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                embeds = self.get_msg_embeds(msg_index)
                selected_urls = []
                urls = self.get_msg_urls_chat(msg_index)
                if urls:
                    for num in self.get_stuff_from_selected_line(chat_sel, 0):   # get url
                        if urls[num] in embeds:
                            selected_urls.append(urls[num])
                else:
                    selected_urls = embeds
                if len(selected_urls) == 1:
                    self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(selected_urls[0], False, True)))
                    self.download_threads[-1].start()
                elif selected_urls:
                    self.ignore_typing = True
                    self.downloading_file = {
                        "urls": selected_urls,
                        "web": False,
                        "open": True,
                    }
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "prompt")
                    self.update_status_line()
                else:
                    self.restore_input_text = (input_text, "standard")

            # cancel all downloads and uploads
            elif action == 11:
                self.add_to_store(self.active_channel["channel_id"], input_text)
                self.restore_input_text = (None, "prompt")
                self.reset_states()
                self.ignore_typing = True
                self.cancel_download = True
                self.update_status_line()

            # copy message to clipboard
            elif action == 12 and self.messages:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                peripherals.copy_to_clipboard(self.messages[msg_index]["content"])

            # upload file
            elif action == 13 and self.messages and not self.disable_sending:
                if self.config["native_file_dialog"]:
                    if self.upload_native_dialog():
                        self.restore_input_text = (input_text, "standard")
                        continue
                self.restore_input_text = (None, "autocomplete")
                self.add_to_store(self.active_channel["channel_id"], input_text)
                if self.current_channel.get("allow_attach", True):
                    if self.recording:   # stop recording voice message
                        self.recording = False
                        _ = recorder.stop()
                    self.uploading = True
                    self.ignore_typing = True
                    self.update_status_line()
                else:
                    self.update_extra_line("Uploading is not allowed in this channel.")

            # moving left/right through attachments
            elif action == 14:
                self.restore_input_text = (input_text, "standard")
                if self.selected_attachment > 0:
                    self.selected_attachment -= 1
                    self.update_extra_line()
            elif action == 15:
                self.restore_input_text = (input_text, "standard")
                num_attachments = 0
                attachments = self.ready_attachments.get(self.active_channel["channel_id"])
                if attachments:
                    num_attachments = len(attachments)
                if self.selected_attachment + 1 < num_attachments:
                    self.selected_attachment += 1
                    self.update_extra_line()

            # cancel selected attachment
            elif action == 16:
                self.restore_input_text = (input_text, "standard")
                self.cancel_attachment()
                self.update_extra_line()

            # reveal one-by-one spoiler in a message
            elif action == 18:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                self.spoil(msg_index)

            # open guild in tree
            elif action == 19:
                self.restore_input_text = (input_text, "standard")
                if self.tree_metadata[tree_sel]:
                    guild_id = self.tree_metadata[tree_sel]["id"]
                self.open_guild(guild_id, select=True)

            # copy/cut on input line
            elif action == 20:
                self.restore_input_text = (input_text, "standard")
                peripherals.copy_to_clipboard(self.tui.input_select_text)

            # join/leave selected thread in tree
            elif action == 21:
                self.restore_input_text = (input_text, "standard")
                if self.tree_metadata[tree_sel] and self.tree_metadata[tree_sel]["type"] in (11, 12):
                    # find threads parent channel and guild
                    thread_id = self.tree_metadata[tree_sel]["id"]
                    guild_id, channel_id, _ = self.find_parents_from_tree(tree_sel)
                    # toggle joined
                    self.thread_toggle_join(guild_id, channel_id, thread_id)
                    self.update_tree()

            # preview file to upload / selected gif from picker
            elif action == 22:
                if self.uploading:
                    self.restore_input_text = (input_text, "autocomplete")
                    self.ignore_typing = True
                    extra_index = self.tui.get_extra_selected()
                    file_path = self.assist_found[extra_index][1]
                    if isinstance(file_path, str):
                        self.media_thread = threading.Thread(target=self.open_media, daemon=True, args=(file_path, ))
                        self.media_thread.start()
                elif self.search_gif:
                    self.restore_input_text = (input_text, "search")
                    self.ignore_typing = True
                    extra_index = self.tui.get_extra_selected()
                    url = self.search_messages[extra_index]["gif"]
                    self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(url, False, True)))
                    self.download_threads[-1].start()
                else:
                    active_channel = self.active_channel["channel_id"]
                    attachments = self.ready_attachments.get(active_channel)
                    if attachments and len(attachments) >= self.selected_attachment - 1:
                        file_path = attachments[self.selected_attachment]["path"]
                        if isinstance(file_path, str):
                            self.media_thread = threading.Thread(target=self.open_media, daemon=True, args=(file_path, ))
                            self.media_thread.start()

            # view profile info
            elif action == 24:
                self.restore_input_text = (input_text, "standard extra")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                self.view_profile(self.messages[msg_index]["user_id"])

            # view channel info
            elif action == 25:
                self.restore_input_text = (input_text, "standard extra")
                self.view_selected_channel(tree_sel=tree_sel)

            # select in extra window / member list
            elif action in (27, 39):
                self.restore_input_text = (input_text, "standard")
                if self.extra_window_open and action == 27:
                    if self.extra_indexes:
                        extra_selected = self.tui.get_extra_selected()
                        if extra_selected < 0:
                            continue
                        total_len = 0
                        for num, item in enumerate(self.extra_indexes):
                            total_len += item["lines"]
                            if total_len >= extra_selected + 1:
                                message_id = item["message_id"]
                                channel_id = item.get("channel_id")
                                break
                        else:
                            continue
                        if channel_id and channel_id != self.active_channel["channel_id"]:
                            guild_id = self.active_channel["guild_id"]
                            guild_name = self.active_channel["guild_name"]
                            channel_name = self.channel_name_from_id(channel_id)
                            self.switch_channel(channel_id, channel_name, guild_id, guild_name)
                        self.tui.disable_wrap_around(False)
                        self.go_to_message(message_id)
                        self.close_extra_window()
                    elif self.search_gif:
                        extra_selected = self.tui.get_extra_selected()
                        if extra_selected < 0:
                            continue
                        gif = self.search_messages[extra_selected]["url"]
                        self.restore_input_text = (gif, "standard")
                        continue
                    elif self.assist_found:
                        new_input_text, new_input_index = self.insert_assist(
                            input_text,
                            self.tui.get_extra_selected(),
                            self.tui.assist_start,
                            self.tui.input_index,
                        )
                        self.reset_states(reacting=False)
                        self.update_status_line()
                        if new_input_text:
                            if self.search and self.extra_bkp:
                                self.restore_input_text = (new_input_text, "search")
                                self.ignore_typing = True
                            elif self.command and self.extra_bkp:
                                self.restore_input_text = (new_input_text, "command")
                                self.ignore_typing = True
                                self.tui.instant_assist = True
                            else:
                                self.restore_input_text = (new_input_text, "standard")
                            self.tui.input_buffer = new_input_text
                            self.tui.set_input_index(new_input_index)
                elif self.state.get("member_list") and self.active_channel["guild_id"]:   # controls for member list when no extra window
                    mlist_selected = self.tui.get_mlist_selected()
                    if mlist_selected == -1 or mlist_selected >= len(self.member_list):
                        continue
                    member = self.member_list[mlist_selected]
                    if "id" in member:
                        self.restore_input_text = (input_text, "standard extra")
                        self.view_profile(member["id"])

            # view summaries
            elif action == 28:
                self.restore_input_text = (input_text, "standard extra")
                self.view_summaries()

            # search
            elif action == 29:
                if not self.search:
                    self.reset_states()
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "search")
                    self.search = True
                    self.tui.disable_wrap_around(True)
                    self.ignore_typing = True
                    max_w = self.tui.get_dimensions()[2][1]
                    extra_title, extra_body = formatter.generate_extra_window_text("Search:", SEARCH_HELP_TEXT, max_w)
                    self.stop_assist(close=False)
                    self.tui.draw_extra_window(extra_title, extra_body)
                    self.extra_window_open = True
                else:
                    self.close_extra_window()
                    self.reset_states()
                    self.search = False
                    self.tui.disable_wrap_around(False)
                    self.search_end = False
                    self.search_messages = []
                    self.update_status_line()
                    self.stop_assist()

            # copy channel link
            elif action == 30:
                self.restore_input_text = (input_text, "standard")
                guild_id = self.active_channel["guild_id"]
                if guild_id and self.tree_metadata[tree_sel]:
                    channel_id = self.tree_metadata[tree_sel]["id"]
                    url = f"https://{self.discord.host}/channels/{guild_id}/{channel_id}"
                    peripherals.copy_to_clipboard(url)

            # copy message link
            elif action == 31 and self.messages:
                self.restore_input_text = (input_text, "standard")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                if "pending" not in self.messages[msg_index]:
                    self.copy_msg_url(msg_index)

            # cycle status
            elif action == 33:
                self.restore_input_text = (input_text, "standard")
                if self.my_status["client_state"] == "online":
                    for num, status in enumerate(STATUS_STRINGS):
                        if status == self.my_status["status"]:
                            if num == len(STATUS_STRINGS) - 1:
                                new_status = STATUS_STRINGS[0]
                            else:
                                new_status = STATUS_STRINGS[num+1]
                            break
                    self.set_status(new_status)

            # quit
            elif action == 34:
                self.run = False
                time.sleep(0.5)
                sys.exit(0)

            # toggle member list
            elif self.get_members and action == 35:
                self.restore_input_text = (input_text, "standard")
                self.toggle_member_list()

            # react
            elif action == 36 and self.messages:
                self.add_to_store(self.active_channel["channel_id"], input_text)
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                message = self.messages[msg_index]
                if "deleted" not in message and "pending" not in message:
                    self.restore_input_text = (None, "react")
                    self.ignore_typing = True
                    self.reacting = {
                        "id": message["id"],
                        "msg_index": msg_index,
                        "username": message["username"],
                        "global_name": message["global_name"],
                    }
                    self.update_status_line()

            # show detailed reactions
            elif action == 37 and self.messages:
                self.restore_input_text = (input_text, "standard extra")
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    continue
                multiple = self.do_view_reactions(msg_index)
                if multiple:
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "prompt")

            # command
            elif action == 38:
                if not self.command:
                    self.update_extra_line(force=True)
                    self.reset_states()
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "command")
                    self.command = True
                    self.command_history_index = max(len(self.command_history), 0)
                    self.ignore_typing = True
                    max_w = self.tui.get_dimensions()[2][1]
                    extra_title, extra_body = formatter.generate_extra_window_assist(COMMAND_ASSISTS, 5, max_w)
                    self.stop_assist(close=False)
                    self.assist_found = COMMAND_ASSISTS
                    self.assist_word = " "
                    self.assist_type = 5
                    self.tui.instant_assist = True
                    self.tui.draw_extra_window(extra_title, extra_body, select=True)
                    self.extra_window_open = True
                    self.extra_bkp = (self.tui.extra_window_title, self.tui.extra_window_body)
                else:
                    self.tui.instant_assist = False
                    self.close_extra_window()
                    self.reset_states()
                    self.command = False
                    self.update_status_line()
                    self.stop_assist()

            # toggle tab
            elif action == 41:
                self.restore_input_text = (input_text, "standard")
                self.toggle_tab()

            # switch tab
            elif action == 42:
                pressed_num_key = self.tui.pressed_num_key
                self.add_to_store(self.active_channel["channel_id"], input_text)
                if pressed_num_key:
                    self.switch_tab(pressed_num_key - 1)

            # show pinned
            elif action == 43:
                self.restore_input_text = (input_text, "standard extra")
                self.view_pinned()

            # search gif
            elif action == 44:
                if not self.search_gif:
                    self.reset_states()
                    self.add_to_store(self.active_channel["channel_id"], input_text)
                    self.restore_input_text = (None, "search")
                    self.search_gif = True
                    self.ignore_typing = True
                    self.stop_assist(close=False)
                    self.extra_window_open = True
                else:
                    self.close_extra_window()
                    self.reset_states()
                    self.search_gif = False
                    self.update_status_line()
                    self.stop_assist()

            # open external editor
            elif action == 45 and self.external_editor is not None:
                self.tui.pause_curses()
                channel = self.active_channel["channel_id"]
                timestamp = int(time.time() * 1000)
                temp_message_path = os.path.join(peripherals.temp_path, f"message-{timestamp}")
                with open(temp_message_path, "w", encoding="utf-8") as file:
                    file.write(input_text)
                subprocess.run([self.external_editor, temp_message_path], check=True)
                if os.path.exists(temp_message_path):
                    with open(temp_message_path, "r", encoding="utf-8") as f:
                        new_text = f.read().strip("\n").replace("\t", " " * self.config["tab_spaces"])
                    os.remove(temp_message_path)
                    self.tui.input_buffer = new_text
                    self.tui.set_input_index(len(new_text))
                    self.restore_input_text = (new_text, "standard")
                else:
                    self.restore_input_text = (input_text, "standard")
                self.tui.resume_curses()

            # up/down in command mode
            elif action == 46 and self.command:   # UP
                if self.command_history_index == len(self.command_history):
                    self.command_history_stored_current = input_text
                if self.command_history:
                    if self.command_history_index > 0:
                        self.command_history_index -= 1
                    self.restore_input_text = (self.command_history[self.command_history_index], "command")
                else:
                    self.restore_input_text = (input_text, "command")
            elif action == 47 and self.command:   # DOWN
                history_len = len(self.command_history) - 1
                if self.command_history and self.command_history_index <= history_len:
                    if self.command_history_index < history_len:
                        self.command_history_index += 1
                    else:
                        self.restore_input_text = (self.command_history_stored_current, "command")
                        self.command_history_stored_current = None
                        self.command_history_index += 1
                        continue
                    self.restore_input_text = (self.command_history[self.command_history_index], "command")
                else:
                    self.restore_input_text = (input_text, "command")

            # mouse double click on message
            elif action == 40:
                self.restore_input_text = (input_text, "standard")
                clicked_chat, mouse_x = self.tui.get_clicked_chat()
                if clicked_chat is not None and mouse_x is not None:
                    chat_line_map = self.chat_map[clicked_chat]
                    if self.forum:
                        msg_index = self.lines_to_msg(chat_sel)
                        if msg_index is None:
                            continue
                        messages = self.messages + self.forum_old
                        message = messages[msg_index]
                        self.switch_channel(
                            message["id"],
                            message["name"],
                            self.active_channel["guild_id"],
                            self.active_channel["guild_name"],
                            parent_hint=self.active_channel["channel_id"],
                        )
                        self.reset_states()
                        self.update_status_line()
                    elif chat_line_map:
                        msg_index = chat_line_map[0]
                        clicked_type = None
                        clicked_id = None
                        # decode
                        if chat_line_map[1] and chat_line_map[1][0] < mouse_x < chat_line_map[1][1]:
                            clicked_type = 2   # username
                        elif chat_line_map[2] == 1:
                            clicked_type = 3   # replied line
                        elif chat_line_map[3]:
                            for num, reaction in enumerate(chat_line_map[3]):
                                if reaction[0] < mouse_x < reaction[1]:
                                    clicked_type = 4   # reaction
                                    clicked_id = num
                                    break
                        elif chat_line_map[4] and chat_line_map[4][0] < mouse_x < chat_line_map[4][1]:
                            clicked_type = 1   # message timestamp
                        elif chat_line_map[5]:   # click on message content elements
                            ranges = chat_line_map[5]
                            if ranges[0]:   # url/embed
                                for num, item in enumerate(ranges[0]):
                                    if item[0] < mouse_x < item[1]:
                                        clicked_type = 5
                                        clicked_id = item[2]
                            elif ranges[2]:   # custom emoji
                                for num, item in enumerate(ranges[2]):
                                    if item[0] < mouse_x < item[1]:
                                        clicked_type = 7
                                        clicked_id = item[2]
                            elif ranges[3]:   # mention
                                for num, item in enumerate(ranges[3]):
                                    if item[0] < mouse_x < item[1]:
                                        clicked_type = 8
                                        clicked_id = item[2]
                            elif ranges[4]:   # channel
                                for num, item in enumerate(ranges[4]):
                                    if item[0] < mouse_x < item[1]:
                                        clicked_type = 9
                                        clicked_id = item[2]
                            if ranges[1]:   # spoiler (owerwries previous)
                                for num, item in enumerate(ranges[1]):
                                    if item[0] < mouse_x < item[1]:
                                        clicked_type = 6
                                        clicked_id = item[2]
                        # execute
                        if clicked_type == 1 and "deleted" not in self.messages[msg_index]:   # start reply
                            if self.messages[msg_index]["user_id"] == self.my_id:
                                mention = None
                            else:
                                mention = self.reply_mention
                            self.replying = {
                                "id": self.messages[msg_index]["id"],
                                "username": self.messages[msg_index]["username"],
                                "global_name": self.messages[msg_index]["global_name"],
                                "mention": mention,
                            }
                            self.update_status_line()
                        elif clicked_type == 2:   # show profile
                            self.restore_input_text = (input_text, "standard extra")
                            self.view_profile(self.messages[msg_index]["user_id"])
                        elif clicked_type == 3:   # go to replied
                            self.go_replied(msg_index)
                        elif clicked_type == 4 and clicked_id is not None:   # add/remove reaction
                            self.build_reaction(str(clicked_id + 1), msg_index=msg_index)
                        elif clicked_type == 5:   # url
                            urls = self.get_msg_urls_chat(msg_index)
                            content_urls = []
                            for match in re.finditer(formatter.match_url, self.messages[msg_index]["content"]):
                                content_urls.append(match.group())
                            url = urls[clicked_id]
                            embed_url = False
                            for embed in self.get_msg_embeds(msg_index, media_only=False, stickers=False):
                                if embed == url and url not in content_urls:
                                    embed_url = True
                                    break
                            match = re.search(match_youtube, url)
                            if match:
                                if support_media and shutil.which(self.config["yt_dlp_path"]) and shutil.which(self.config["mpv_path"]):
                                    self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(url, False, True)))
                                    self.download_threads[-1].start()
                                else:
                                    webbrowser.open(url, new=0, autoraise=True)
                            elif embed_url:
                                self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(url, False, False, True)))
                                self.download_threads[-1].start()
                            else:
                                url = self.refresh_attachment_url(url)
                                webbrowser.open(url, new=0, autoraise=True)
                        elif clicked_type == 6:   # spoiler
                            self.spoil(msg_index, clicked_id)
                        elif clicked_type == 7:   # custom emoji
                            self.view_emoji(clicked_id)
                        elif clicked_type == 8:   # mention
                            self.restore_input_text = (input_text, "standard extra")
                            self.view_profile(clicked_id)
                        elif clicked_type == 9:   # channel
                            channel_id, channel_name, guild_id, guild_name, parent_hint = self.find_parents_from_id(clicked_id)
                            if channel_name:
                                self.switch_channel(channel_id, channel_name, guild_id, guild_name, parent_hint=parent_hint)

            # mouse single-click on extra line
            elif action == 48:
                if self.extra_line == self.permanent_extra_line and not self.extra_window_open:
                    if self.in_call:
                        mouse_x = self.tui.get_extra_line_clicked()
                        muted = bool(self.state.get("muted")) * 2
                        if len(self.extra_line)-13 - muted <= mouse_x < len(self.extra_line)-9:   # TOGGLE MUTE
                            if muted:
                                self.state["muted"] = False
                                self.update_voice_mute_in_call()
                            else:
                                self.state["muted"] = True
                                self.update_voice_mute_in_call()
                        elif len(self.extra_line)-6 <= mouse_x < len(self.extra_line)-1:   # LEAVE
                            self.leave_call()
                    elif self.most_recent_incoming_call or self.active_channel["channel_id"] in self.incoming_calls:
                        mouse_x = self.tui.get_extra_line_clicked()
                        if len(self.extra_line)-16 <= mouse_x < len(self.extra_line)-10:   # ACCEPT
                            if not self.in_call:
                                if self.most_recent_incoming_call:
                                    incoming_call_ch_id = self.most_recent_incoming_call
                                else:
                                    incoming_call_ch_id = self.active_channel["channel_id"]
                                threading.Thread(target=self.start_call, daemon=True, args=(True, None, incoming_call_ch_id)).start()
                            else:
                                self.update_extra_line("Cant join multiple calls")
                        elif len(self.extra_line)-7 <= mouse_x < len(self.extra_line)-1:   # REJECT
                            self.stop_ringing()
                            self.most_recent_incoming_call = None
                            # remove popup if not in this channel
                            if self.active_channel["channel_id"] not in self.incoming_calls:
                                self.update_extra_line(permanent=True)
                elif self.fun == 4 and self.extra_line and self.extra_line.startswith("Personalized"):
                    self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(
                        "https://" + "archive.org/download/youtube-" + "/".join(["xvFZjo5PgG0"]*2) + ".mp4",
                        False, True,
                    )))
                    self.download_threads[-1].start()

            # escape in main UI
            elif action == 5:
                if self.recording:
                    self.stop_recording(cancel=True)
                elif self.reacting["id"]:
                    self.reset_states()
                    self.restore_input_text = (None, None)
                elif self.uploading:
                    self.reset_states()
                    self.tui.set_input_index(0)
                    self.restore_input_text = (None, None)   # load text from cache
                elif self.assist_word and not self.tui.instant_assist:
                    if self.search or self.search_gif:
                        self.restore_input_text = (input_text, "search")
                    elif self.command:
                        self.restore_input_text = (input_text, "command")
                    elif self.assist_type == 6:   # app command
                        self.reset_states()
                        self.tui.set_input_index(0)
                        self.restore_input_text = ("", "standard")
                    else:
                        self.restore_input_text = (input_text, "standard")
                elif self.extra_window_open:
                    self.stop_extra_window(update=False)
                elif self.replying["id"]:
                    self.reset_states(replying=True)
                    self.restore_input_text = (input_text, "standard")
                elif self.editing:
                    self.restore_input_text = (None, None)
                    self.reset_states()
                elif self.restore_input_text[1] == "after prompt":
                    self.reset_states()
                    self.restore_input_text = (None, None)
                else:
                    self.update_extra_line()
                    self.reset_states()
                    self.restore_input_text = (input_text, "standard")
                    self.execute_extensions_methods("on_escape_key")
                self.update_status_line()
                self.stop_assist()

            # command bindings
            elif isinstance(action, tuple):
                if action[0] == 50:
                    self.restore_input_text = (input_text, "standard")
                    for command in parser.split_command_binding(action[1]):
                        if command.startswith("sleep"):
                            try:
                                time.sleep(float(command[6:]))
                                continue
                            except ValueError:
                                pass
                        cmd_type, cmd_args = parser.command_string(command)
                        chat_sel, _ = self.tui.get_chat_selected()
                        tree_sel = self.tui.get_tree_selected()
                        binding = None
                        if cmd_type == 0:   # try keybindings
                            binding = self.keybindings.get(command)
                            if binding and binding[0]:
                                self.wait_input(forced_binding=binding[0])
                        if not binding:
                            self.execute_command(cmd_type, cmd_args, action[1], chat_sel, tree_sel)
                        self.check_tree_format()
                    self.assist_word = None

            # media controls   # handled externally in media.py as curses is fully paused
            # elif action >= 100:
            #     self.terminal_media.control_codes(action)

            # execute extensions bindings
            elif self.execute_extensions_method_first("on_wait_input", action, input_text, chat_sel, tree_sel, cache=True):
                pass

            # enter
            elif (action == 0 and input_text and input_text != "\n" and self.active_channel["channel_id"]) or self.command:
                if self.assist_word is not None and self.assist_found:
                    self.restore_input_text = (input_text, "standard")
                    new_input_text, new_input_index = self.insert_assist(
                        input_text,
                        self.tui.get_extra_selected(),
                        self.tui.assist_start,
                        self.tui.input_index,
                    )
                    # self.reset_states(reacting=False)
                    self.update_status_line()
                    # 1000000 means its command execution and should restore text from store
                    if new_input_text is not None and new_input_index != 1000000:
                        if (self.search or self.search_gif) and self.extra_bkp:
                            self.restore_input_text = (new_input_text, "search")
                            self.ignore_typing = True
                        elif self.command and self.extra_bkp:
                            self.restore_input_text = (new_input_text, "command")
                            self.ignore_typing = True
                        elif self.reacting["id"]:
                            self.restore_input_text = (new_input_text, "react")
                            self.ignore_typing = True
                        elif self.uploading:
                            self.restore_input_text = (new_input_text, "autocomplete")
                            self.ignore_typing = True
                        else:
                            self.restore_input_text = (new_input_text, "standard")
                        self.tui.input_buffer = new_input_text
                        self.tui.set_input_index(new_input_index)
                    else:
                        self.assist_word = None
                        self.assist_found = []
                        if new_input_index != 1000000:
                            self.restore_input_text = (None, None)
                    continue

                if input_text.lower() != "y" and (self.deleting or self.cancel_download or self.hiding_ch["channel_id"]):
                    # anything not "y" when asking for "[Y/n]"
                    self.reset_states()
                    self.update_status_line()
                    continue

                if self.editing:
                    text_to_send = emoji.emojize(input_text, language="alias", variant="emoji_type")
                    self.put_to_message_sender(self.discord.send_update_message,
                        channel_id=self.active_channel["channel_id"],
                        message_id=self.editing,
                        message_content=text_to_send,
                    )

                elif self.deleting and input_text.lower() == "y":
                    self.put_to_message_sender(self.discord.send_delete_message,
                        channel_id=self.active_channel["channel_id"],
                        message_id=self.deleting,
                    )

                elif self.downloading_file["urls"]:
                    urls = self.downloading_file["urls"]
                    if self.downloading_file["web"]:
                        try:
                            num = max(int(input_text) - 1, 0)
                            if num <= len(self.downloading_file["urls"]):
                                url = self.refresh_attachment_url(urls[num])
                                if self.downloading_file["web"] == 1:
                                    webbrowser.open(url, new=0, autoraise=True)
                                else:
                                    peripherals.copy_to_clipboard(url)
                        except ValueError:
                            pass
                    elif self.downloading_file["open"]:
                        try:
                            logger.debug("Trying to play attachment from selection")
                            num = max(int(input_text) - 1, 0)
                            if num <= len(self.downloading_file["urls"]):
                                self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(urls[num], False, True)))
                                self.download_threads[-1].start()
                        except ValueError:
                            pass
                    else:
                        try:
                            num = max(int(input_text) - 1, 0)
                            if num <= len(self.downloading_file["urls"]):
                                self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(urls[num], )))
                                self.download_threads[-1].start()
                        except ValueError:
                            pass

                elif self.cancel_download and input_text.lower() == "y":
                    self.downloader.cancel()
                    self.download_threads = []
                    self.cancel_upload()
                    self.upload_threads = []

                elif self.search:
                    self.do_search(input_text)
                    self.restore_input_text = (None, "search")
                    self.reset_states()
                    self.ignore_typing = True
                    self.update_status_line()
                    continue

                elif self.search_gif:
                    max_w = self.tui.get_dimensions()[2][1]
                    self.add_running_task("Searching gifs", 4)
                    self.search_messages = self.discord.search_gifs(input_text)
                    self.remove_running_task("Searching gifs", 4)
                    if self.search_messages is None:
                        self.search_messages = []
                        self.gateway.set_offline()
                        self.stop_assist()
                        self.update_extra_line("Network error.")
                        self.restore_input_text = (input_text, "standard")
                        continue
                    extra_title, extra_body = formatter.generate_extra_window_search_gif(self.search_messages, max_w)
                    self.tui.draw_extra_window(extra_title, extra_body, select=True)
                    self.restore_input_text = (None, "search")
                    self.reset_states()
                    self.ignore_typing = True
                    self.update_status_line()
                    continue

                elif self.command:
                    self.tui.instant_assist = False
                    command_type, command_args = parser.command_string(input_text)
                    self.close_extra_window()
                    self.execute_command(command_type, command_args, input_text, chat_sel, tree_sel)
                    self.assist_word = None
                    self.add_to_command_history(input_text)
                    self.command = False
                    continue

                elif self.reacting["id"]:
                    self.build_reaction(input_text)

                elif self.hiding_ch["channel_id"] and input_text.lower() == "y":
                    channel_id = self.hiding_ch["channel_id"]
                    guild_id = self.hiding_ch["guild_id"]
                    self.hide_channel(channel_id, guild_id)
                    self.hidden_channels.append({
                        "channel_name": self.hiding_ch["channel_name"],
                        "channel_id": channel_id,
                        "guild_id": guild_id,
                        })
                    peripherals.save_json(self.hidden_channels, "hidden_channels.json")
                    self.update_tree()

                elif self.going_to_ch:
                    try:
                        num = max(int(input_text) - 1, 0)
                    except ValueError:
                        self.reset_states()
                        self.update_status_line()
                        continue
                    if num <= len(self.going_to_ch):
                        guild_id = self.going_to_ch[num][0]
                        channel_id = self.going_to_ch[num][1]
                        message_id = self.going_to_ch[num][2]
                        channel_id, channel_name, guild_id, guild_name, parent_hint = self.find_parents_from_id(channel_id)
                        if channel_name:
                            self.switch_channel(channel_id, channel_name, guild_id, guild_name, parent_hint=parent_hint)
                            if message_id:
                                self.go_to_message(message_id)

                elif self.view_reactions["message_id"]:
                    reactions = self.view_reactions["reactions"]
                    try:
                        num = max(int(input_text) - 1, 0)
                        if num <= len(reactions):
                            if reactions[num]["emoji_id"]:
                                reaction = f"{reactions[num]["emoji"]}:{reactions[num]["emoji_id"]}"
                            else:
                                reaction = reactions[num]["emoji"]
                            reaction_details = self.discord.get_reactions(
                                self.active_channel["channel_id"],
                                self.view_reactions["message_id"],
                                reaction,
                                )
                            if reaction_details is None:
                                self.gateway.set_offline()
                                self.stop_assist()
                                self.update_extra_line("Network error.")
                                self.restore_input_text = (input_text, "standard")
                                continue
                            self.stop_assist(close=False)
                            max_w = self.tui.get_dimensions()[2][1]
                            extra_title, extra_body = formatter.generate_extra_window_reactions(reactions[num], reaction_details, max_w)
                            self.tui.draw_extra_window(extra_title, extra_body)
                            self.extra_window_open = True
                    except ValueError:
                        pass

                elif input_text[0] == "/" and parser.check_start_command(input_text, self.my_commands, self.guild_commands, self.guild_commands_permitted) and not self.disable_sending:
                    if self.forum:
                        self.update_extra_line("Cant run app command in forum.")
                    else:
                        self.execute_app_command(input_text)

                elif self.slowmode_times.get(self.active_channel["channel_id"]):
                    self.restore_input_text = (input_text, "standard")
                    self.update_extra_line(f"Slowmode is enabled, will be able to send message in {self.slowmode_times[self.active_channel["channel_id"]]} s")
                    # dont allow sending messagee until it expires

                elif not self.disable_sending and not self.forum:
                    # check for substituition
                    if input_text.startswith("s/"):
                        self.substitute_in_last_message(input_text)
                        continue
                    # select attachment
                    this_attachments = None
                    active_channel = self.active_channel["channel_id"]
                    attachments = self.ready_attachments.get(active_channel)
                    if attachments:
                        if all(attachment.get("state", 0) >= 1 for attachment in attachments):
                            this_attachments = self.ready_attachments.pop(active_channel)
                            self.update_extra_line()
                        else:
                            self.restore_input_text = (input_text, "standard")
                            self.update_extra_line("Attachments are still uploading.")
                    # if this thread is not joined, join it (locally only)
                    if self.current_channel.get("type") in (11, 12) and not self.current_channel.get("joined"):
                        channel_id, _, guild_id, _, parent_id = self.find_parents_from_tree(active_channel)
                        self.thread_toggle_join(
                            guild_id,
                            parent_id,
                            channel_id,
                            join=True,
                        )
                    # search for stickers
                    stickers = []
                    for match in re.finditer(formatter.match_sticker_id, input_text):
                        stickers.append(match.group()[2:-2])
                        input_text = input_text[:match.start()] + input_text[match.end():]
                    text_to_send = emoji.emojize(input_text, language="alias", variant="emoji_type")
                    if self.fun and ("xyzzy" in text_to_send or "XYZZY" in text_to_send):
                        self.update_extra_line("Nothing happens.")
                    if not text_to_send.strip():
                        continue
                    nonce = discord.generate_nonce()
                    self.put_to_message_sender(self.discord.send_message,
                        self.active_channel["channel_id"],
                        text_to_send,
                        reply_id=self.replying["id"],
                        reply_channel_id=self.active_channel["channel_id"],
                        reply_guild_id=self.active_channel["guild_id"],
                        reply_ping=self.replying["mention"],
                        attachments=this_attachments,
                        stickers=stickers,
                        nonce=nonce,
                    )
                    self.add_pending_message(
                        text_to_send,
                        nonce,
                        reply_id=self.replying["id"],
                        attachments=this_attachments,
                        stickers=stickers,
                    )
                    self.reset_states(replying=True)
                    continue

                self.reset_states()

            # enter with no text
            elif input_text == "":
                if self.forum:
                    if input_text and input_text != "\n":
                        self.add_to_store(self.active_channel["channel_id"], input_text)
                    # failsafe if messages got rewritten
                    self.switch_channel(
                        self.messages[chat_sel]["id"],
                        self.messages[chat_sel]["name"],
                        self.active_channel["guild_id"],
                        self.active_channel["guild_name"],
                        parent_hint=self.active_channel["channel_id"],
                    )
                    self.reset_states()
                    self.update_status_line()

                if self.deleting:
                    self.put_to_message_sender(self.discord.send_delete_message,
                        channel_id=self.active_channel["channel_id"],
                        message_id=self.deleting,
                    )

                elif self.cancel_download:
                    self.downloader.cancel()
                    self.download_threads = []
                    self.cancel_upload()
                    self.upload_threads = []

                elif self.ready_attachments and not self.disable_sending and not self.forum:
                    this_attachments = None
                    active_channel = self.active_channel["channel_id"]
                    attachments = self.ready_attachments.get(active_channel)
                    if attachments:
                        if all(attachment.get("state", 0) >= 1 for attachment in attachments):
                            this_attachments = self.ready_attachments.pop(active_channel)
                            self.update_extra_line()
                        else:
                            self.update_extra_line("Attachments are still uploading.")
                            continue
                    nonce = discord.generate_nonce()
                    self.put_to_message_sender(self.discord.send_message,
                        active_channel,
                        "",
                        reply_id=self.replying["id"],
                        reply_channel_id=active_channel,
                        reply_guild_id=self.active_channel["guild_id"],
                        reply_ping=self.replying["mention"],
                        attachments=this_attachments,
                        nonce=nonce,
                    )
                    self.add_pending_message(
                        "",
                        nonce,
                        reply_id=self.replying["id"],
                        attachments=this_attachments,
                    )
                    self.reset_states(replying=True)
                    continue

                elif self.search and self.extra_window_open and self.extra_indexes:
                    extra_selected = self.tui.get_extra_selected()
                    if extra_selected < 0:
                        continue
                    total_len = 0
                    for item in self.extra_indexes:
                        total_len += item["lines"]
                        if total_len >= extra_selected + 1:
                            message_id = item["message_id"]
                            channel_id = item.get("channel_id")
                            break
                    else:
                        continue
                    if channel_id and channel_id != self.active_channel["channel_id"]:
                        guild_id = self.active_channel["guild_id"]
                        guild_name = self.active_channel["guild_name"]
                        channel_name = self.channel_name_from_id(channel_id)
                        self.switch_channel(channel_id, channel_name, guild_id, guild_name)
                    self.tui.disable_wrap_around(False)
                    self.go_to_message(message_id)
                    self.close_extra_window()

                elif self.search_gif and self.extra_window_open:
                    extra_selected = self.tui.get_extra_selected()
                    if extra_selected < 0:
                        continue
                    url = self.search_messages[extra_selected]["url"]
                    self.restore_input_text = (url, "standard insert")

                elif self.hiding_ch["channel_id"]:
                    channel_id = self.hiding_ch["channel_id"]
                    guild_id = self.hiding_ch["guild_id"]
                    self.hide_channel(channel_id, guild_id)
                    self.hidden_channels.append({
                        "channel_name": self.hiding_ch["channel_name"],
                        "channel_id": channel_id,
                        "guild_id": guild_id,
                        })
                    peripherals.save_json(self.hidden_channels, "hidden_channels.json")
                    self.update_tree()

                elif self.recording:
                    self.recording = False
                    file_path = recorder.stop()
                    self.update_extra_line()
                    if not self.disable_sending:
                        self.add_running_task("Uploading file", 2)
                        success = self.discord.send_voice_message(
                            self.active_channel["channel_id"],
                            file_path,
                            reply_id=self.replying["id"],
                            reply_channel_id=self.active_channel["channel_id"],
                            reply_guild_id=self.active_channel["guild_id"],
                            reply_ping=self.replying["mention"],
                        )
                        self.remove_running_task("Uploading file", 2)
                        if success is None:
                            self.gateway.set_offline()
                            self.update_extra_line("Network error.")
                            self.restore_input_text = (input_text, "standard")

                self.reset_states()


    def can_run_command(self, cmd_type):
        """Check if this command can be run in current scope"""
        if self.forum:
            if cmd_type not in FORUM_COMMANDS:
                return False
        return True


    def execute_command(self, cmd_type, cmd_args, cmd_text, chat_sel, tree_sel):
        """Execute custom command"""
        logger.debug(f"Executing command, type: {cmd_type}, args: {cmd_args}")
        reset = True
        self.restore_input_text = (None, None)
        success = False
        if cmd_type == 0:
            if cmd_args:
                self.update_extra_line("Invalid command arguments.")
            elif self.execute_extensions_method_first("on_execute_command", cmd_text, chat_sel, tree_sel):
                pass
            else:
                self.update_extra_line("Unknown command.")
            return

        if not self.can_run_command(cmd_type):
            self.reset_states()
            self.update_status_line()
            self.update_extra_line("This command cant be executed in forum.")
            return

        if cmd_type == 1:   # SET
            key = cmd_args["key"]
            value = cmd_args["value"]
            if key in self.config:
                self.update_extra_line("Restart needed for changes to take effect.")
                self.config = peripherals.update_config(self.config, key, value)
            else:
                self.update_extra_line("Unknow settings key.")

        elif cmd_type == 2:   # BOTTOM
            self.go_bottom()

        elif cmd_type == 3:   # GOTO_REPLY
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            self.go_replied(msg_index)

        elif cmd_type == 4:   # DOWNLOAD
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            select_num = cmd_args.get("num", 0)
            embeds = []
            for embed in self.messages[msg_index]["embeds"]:
                if embed["url"]:
                    embeds.append(embed["url"])
            selected_urls = []
            urls = self.get_msg_urls_chat(msg_index)
            if urls:
                for num in self.get_stuff_from_selected_line(chat_sel, 0):   # get url
                    if urls[num] in embeds:
                        selected_urls.append(urls[num])
                if len(selected_urls) == 1 or select_num:
                    select_num = max(min(select_num-1, len(selected_urls)-1), 0)
                    self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(selected_urls[select_num], )))
                    self.download_threads[-1].start()
                else:
                    self.ignore_typing = True
                    self.downloading_file = {
                        "urls": selected_urls,
                        "web": False,
                        "open": False,
                    }
                    self.restore_input_text = (None, "prompt")
                    reset = False

        elif cmd_type in (5, 64):   # OPEN_LINK and COPY_LINK
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            select_num = cmd_args.get("num", 0)
            selected_urls = []
            urls = self.get_msg_urls_chat(msg_index)
            if urls:
                for num in self.get_stuff_from_selected_line(chat_sel, 0):   # get url
                    selected_urls.append(urls[num])
                if len(selected_urls) == 1 or select_num:
                    select_num = max(min(select_num-1, len(selected_urls)-1), 0)
                    selected_url = self.refresh_attachment_url(selected_urls[select_num])
                    if cmd_type == 5:
                        webbrowser.open(selected_url, new=0, autoraise=True)
                    else:
                        peripherals.copy_to_clipboard(selected_url)
                else:
                    self.ignore_typing = True
                    self.downloading_file = {
                        "urls": selected_urls,
                        "web": 1 if cmd_type == 5 else 2,
                        "open": False,
                    }
                    self.restore_input_text = (None, "prompt")
                    reset = False

        elif cmd_type == 6:   # PLAY
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            select_num = cmd_args.get("num", 0)
            embeds = self.get_msg_embeds(msg_index)
            selected_urls = []
            urls = self.get_msg_urls_chat(msg_index)
            if urls:
                for num in self.get_stuff_from_selected_line(chat_sel, 0):   # get url
                    if urls[num] in embeds:
                        selected_urls.append(urls[num])
            else:
                selected_urls = embeds
            if len(selected_urls) == 1 or select_num:
                select_num = max(min(select_num-1, len(selected_urls)-1), 0)
                self.download_threads.append(threading.Thread(target=self.download_file, daemon=True, args=(selected_urls[select_num], False, True)))
                self.download_threads[-1].start()
            else:
                self.ignore_typing = True
                self.downloading_file = {
                    "urls": selected_urls,
                    "web": False,
                    "open": True,
                }
                self.restore_input_text = (None, "prompt")
                reset = False

        elif cmd_type == 7:   # CANCEL
            reset = False
            self.restore_input_text = (None, "prompt")
            self.reset_states()
            self.ignore_typing = True
            self.cancel_download = True

        elif cmd_type == 8:   # COPY_MESSAGE
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            peripherals.copy_to_clipboard(self.messages[msg_index]["content"])

        elif cmd_type == 9:   # UPLOAD
            if self.current_channel.get("allow_attach", True):
                path = cmd_args.get("path", None)
                success = False
                if path:
                    self.upload_threads.append(threading.Thread(target=self.upload, daemon=True, args=(path, )))
                    self.upload_threads[-1].start()
                elif self.config["native_file_dialog"]:
                    success = self.upload_native_dialog()
                if not success:
                    self.restore_input_text = (None, "autocomplete")
                    if self.recording:   # stop recording voice message
                        self.recording = False
                        _ = recorder.stop()
                    self.uploading = True
                    self.ignore_typing = True
                    reset = False
            else:
                self.update_extra_line("Uploading is not allowed in this channel.")

        elif cmd_type == 10:   # SPOIL
            spoiler_index = cmd_args.get("num", None)
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            self.spoil(msg_index, spoiler_index)

        elif cmd_type == 11 and self.tree_metadata[tree_sel] and self.tree_metadata[tree_sel]["type"] in (11, 12):   # TOGGLE_THREAD_TREE
            thread_id = self.tree_metadata[tree_sel]["id"]
            guild_id, channel_id, _ = self.find_parents_from_tree(tree_sel)
            self.thread_toggle_join(guild_id, channel_id, thread_id)
            self.update_tree()

        elif cmd_type == 12:  # PROFILE
            user_id = cmd_args.get("user_id", None)
            if not user_id:
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    return
                user_id = self.messages[msg_index]["user_id"]
            self.view_profile(user_id)

        elif cmd_type == 13:   # CHANNEL
            channel_id = cmd_args.get("channel_id", None)
            if channel_id:
                self.view_selected_channel(channel_id=channel_id)
            else:
                self.view_selected_channel(tree_sel=tree_sel)

        elif cmd_type == 14:   # SUMMARIES
            channel_id = cmd_args.get("channel_id", None)
            self.view_summaries(channel_id)

        elif cmd_type == 15:   # HIDE
            channel_id = cmd_args.get("channel_id", None)
            channel_sel = None
            if channel_id:
                _, _, guild_id, _, _ = self.find_parents_from_id(channel_id)
            else:
                channel_sel = self.tree_metadata[tree_sel]["type"]
            if channel_sel and channel_sel["type"] not in (-1, 1, 11, 12):
                self.restore_input_text = (None, "prompt")
                reset = False
                self.reset_states()
                self.ignore_typing = True
                guild_id = self.find_parents_from_tree(tree_sel)[0]
                self.hiding_ch = {
                    "channel_name": channel_sel["name"],
                    "channel_id": channel_sel["id"],
                    "guild_id": guild_id,
                }

        elif cmd_type == 16:   # SEARCH
            search_text = cmd_args.get("search_text", None)
            if search_text:
                reset = False
                self.do_search(search_text)
                self.restore_input_text = (None, "search")
                self.reset_states()
                self.extra_window_open = True
                self.search = True
                self.tui.disable_wrap_around(True)
                self.ignore_typing = True
            elif not self.search:
                reset = False
                self.reset_states()
                self.restore_input_text = (None, "search")
                self.search = True
                self.tui.disable_wrap_around(True)
                self.ignore_typing = True
                max_w = self.tui.get_dimensions()[2][1]
                extra_title, extra_body = formatter.generate_extra_window_text("Search:", SEARCH_HELP_TEXT, max_w)
                self.stop_assist(close=False)
                self.tui.draw_extra_window(extra_title, extra_body)
                self.extra_window_open = True

        elif cmd_type == 17:   # LINK_CHANNEL
            channel_id = cmd_args.get("channel_id")
            guild_id = None
            if channel_id:
                _, _, guild_id, _, _ = self.find_parents_from_id(channel_id)
            else:
                channel_id = self.tree_metadata[tree_sel]["id"]
                guild_id = self.find_parents_from_tree(tree_sel)[0]
            if guild_id:
                url = f"https://{self.discord.host}/channels/{guild_id}/{channel_id}"
                peripherals.copy_to_clipboard(url)

        elif cmd_type == 18:   # LINK_MESSAGE
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            if "pending" not in self.messages[msg_index]:
                self.copy_msg_url(msg_index)

        elif cmd_type == 19:   # GOTO_MENTION
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            select_num = max(cmd_args.get("num", 0), 0)
            channels = []
            for match in re.finditer(formatter.match_discord_channel_combined, self.messages[msg_index]["content"]):
                # groups: 1 - channel_id for <#id>, 2 - guild_id for url, 3 - channel_id for url, 4 - msg_id for url
                if match.group(1):
                    guild_id = self.active_channel["guild_id"]
                    channel_id = match.group(1)
                    message_id = None
                else:
                    guild_id = match.group(2)
                    channel_id = match.group(3)
                    message_id = match.group(4)
                channels.append((guild_id, channel_id, message_id))
            if len(channels) == 1 or (channels and select_num is not None):
                if select_num is None:
                    select_num = 0
                select_num = max(min(select_num-1, len(channels)-1), 0)
                channel_id, channel_name, guild_id, guild_name, parent_hint = self.find_parents_from_id(channels[select_num][1])
                if channel_name:
                    self.switch_channel(channel_id, channel_name, guild_id, guild_name, parent_hint=parent_hint)
                    if message_id:
                        self.go_to_message(message_id)
            elif channels:
                self.ignore_typing = True
                self.going_to_ch = channels
                self.update_status_line()
                self.restore_input_text = (None, "prompt")
                reset = False

        elif cmd_type == 20:   # STATUS
            new_status = cmd_args.get("status")
            if self.my_status["client_state"] == "online":
                if not new_status:
                    for num, status in enumerate(STATUS_STRINGS):
                        if status == self.my_status["status"]:
                            if num == len(STATUS_STRINGS) - 1:
                                new_status = STATUS_STRINGS[0]
                            else:
                                new_status = STATUS_STRINGS[num+1]
                            break
                self.set_status(new_status)

        elif cmd_type == 21:   # RECORD
            cancel = cmd_args.get("cancel")
            if self.recording:
                self.stop_recording(cancel=cancel)
            else:
                self.start_recording()

        elif cmd_type == 22:   # MEMBER_LIST
            self.toggle_member_list()

        elif cmd_type == 23:   # REACT
            react_text = cmd_args.get("react_text")
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            if not react_text:
                reset = False
                if "deleted" not in self.messages[msg_index] and "pending" not in self.messages[msg_index]:
                    self.restore_input_text = (None, "react")
                    self.ignore_typing = True
                    self.reacting = {
                        "id": self.messages[msg_index]["id"],
                        "msg_index": msg_index,
                        "username": self.messages[msg_index]["username"],
                        "global_name": self.messages[msg_index]["global_name"],
                    }
                    self.update_status_line()
            else:
                self.build_reaction(react_text, msg_index=msg_index)

        elif cmd_type == 24:   # SHOW_REACTIONS
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            multiple = self.do_view_reactions(msg_index)
            if multiple:
                self.restore_input_text = (None, "prompt")

        elif cmd_type == 25:   # GOTO
            object_id = cmd_args["channel_id"]
            tp = False
            if object_id == "special" and self.fun:
                if not self.checkpoint:
                    self.checkpoint = self.active_channel["channel_id"]
                    self.update_extra_line("Teleportation point set.")
                else:
                    object_id = self.checkpoint
                    tp = True
            channel_id, channel_name, guild_id, guild_name, parent_hint = self.find_parents_from_id(object_id)
            if channel_id == guild_id:
                channel_id = None
            # guild
            if not channel_id:
                self.tui.tree_select(self.tree_pos_from_id(object_id))
                folder_changed = self.open_guild(object_id, select=True, open_only=True)
                if folder_changed:
                    self.tui.tree_select(self.tree_pos_from_id(object_id))
            # category
            elif not parent_hint and guild_id:
                self.open_guild(guild_id, select=True, open_only=True)
                tree_pos = self.tree_pos_from_id(object_id)
                if tree_pos is not None:
                    self.tui.tree_select(tree_pos)
                    self.tui.toggle_category(tree_pos, only_open=True)
                else:
                    self.tui.tree_select(self.tree_pos_from_id(guild_id))
            # channel/dm
            else:
                if guild_id is not None:   # channel
                    self.open_guild(guild_id, select=True, open_only=True)
                    category_tree_pos = self.tree_pos_from_id(parent_hint)
                    if category_tree_pos:
                        self.tui.toggle_category(category_tree_pos, only_open=True)
                        channel_tree_pos = self.tree_pos_from_id(channel_id)
                        if channel_tree_pos:
                            self.tui.tree_select(channel_tree_pos)
                        else:
                            self.tui.tree_select(category_tree_pos)
                    else:
                        self.tui.tree_select(self.tree_pos_from_id(guild_id))
                else:   # dm
                    self.open_guild(0, select=True, open_only=True)
                    self.tui.tree_select(self.tree_pos_from_id(object_id))
                    time.sleep(0.1)   # sometimes dms list gets collapsed if no delay
                self.switch_channel(channel_id, channel_name, guild_id, guild_name, parent_hint=parent_hint)
                if tp:
                    self.update_extra_line("You're inside building. There is food here.")

        elif cmd_type == 26:   # VIEW_PFP
            user_id = cmd_args.get("user_id", None)
            if not user_id:
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is None:
                    return
                user_id = self.messages[msg_index]["user_id"]
            avatar_id = None
            if user_id == self.my_id:
                avatar_id = self.my_user_data["extra"]["avatar"]
            if not avatar_id:
                for dm in self.dms:
                    if dm["id"] == user_id:
                        avatar_id = dm["avatar"]
                        break
            if not avatar_id:
                avatar_id = self.discord.get_user(user_id, extra=True)["extra"]["avatar"]
            if avatar_id:
                if self.config["native_media_player"]:
                    size = 160
                else:
                    size = None
                pfp_path = self.discord.get_pfp(user_id, avatar_id, size)
                if pfp_path is None:
                    self.gateway.set_offline()
                    self.update_extra_line("Network error.")
                elif pfp_path:
                    self.media_thread = threading.Thread(target=self.open_media, daemon=True, args=(pfp_path, ))
                    self.media_thread.start()

        elif cmd_type == 27:   # CHECK_STANDING
            standing = self.discord.get_my_standing()
            self.update_extra_line(f"Account standing: {standing}/100")

        elif cmd_type == 28:   # PASTE
            paths = []
            if shutil.which("xclip") or shutil.which("wl-paste"):
                paths = peripherals.paste_clipboard_files(peripherals.temp_path)
            elif support_media:
                paths = clipboard.save_image()
            else:
                self.update_extra_line("No media support.")
            if isinstance(paths, str):
                active_channel = self.active_channel["channel_id"]
                for num, channel in enumerate(self.input_store):
                    if channel["id"] == active_channel:
                        input_text = self.input_store[num]["content"]
                        input_index = self.input_store[num]["index"]
                        self.input_store[num]["content"] = input_text[:input_index] + paths + input_text[input_index:]
                        self.input_store[num]["index"] = input_index + len(paths)
                        break
            else:
                for path in paths:
                    self.upload_threads.append(threading.Thread(target=self.upload, daemon=True, args=(path, )))
                    self.upload_threads[-1].start()
                if not paths:
                    self.update_extra_line("Image not found in clipboard.")

        elif cmd_type == 29:   # TOGGLE_MUTE
            channel_id = cmd_args.get("channel_id")
            guild_id = None
            if channel_id:
                _, _, guild_id, _, _ = self.find_parents_from_id(channel_id)
            else:
                channel_id = self.tree_metadata[tree_sel]["id"]
                guild_id = self.find_parents_from_tree(tree_sel)[0]
            if guild_id:   # mute channel/category
                mute = self.toggle_mute(channel_id, guild_id=guild_id)
                if mute is not None:
                    success = self.discord.send_mute_channel(mute, channel_id, guild_id)

            else:
                is_dm = False
                for dm in self.dms:
                    if dm["id"] == channel_id:
                        is_dm = True
                        break
                if is_dm:   # mute DM
                    mute = self.toggle_mute(channel_id, is_dm=True)
                    if mute is not None:
                        success = self.discord.send_mute_dm(mute, channel_id)
                else:   # mute guild
                    mute = self.toggle_mute(channel_id)
                    if mute is not None:
                        success = self.discord.send_mute_guild(mute, channel_id)

        elif cmd_type == 30:   # TOGGLE_TAB
            if not self.forum:
                self.toggle_tab()

        elif cmd_type == 31:   # SWITCH_TAB
            select_num = max(cmd_args.get("num", 1) - 1, 0)
            self.switch_tab(select_num)

        elif cmd_type == 32:   # MARK_AS_READ:
            target_id = cmd_args.get("channel_id")
            if target_id == "*":
                target_id = self.active_channel["guild_id"]
            if not target_id:
                target_id = self.tree_metadata[tree_sel]["id"]
            self.set_mix_seen(target_id)

        elif cmd_type == 33:   # INSERT_TIMESTAMP
            timestamp = cmd_args["timestamp"]
            timestamp = f"<t:{timestamp}>"
            for num, channel in enumerate(self.input_store):
                if channel["id"] == self.active_channel["channel_id"]:
                    input_text = self.input_store[num]["content"]
                    input_index = self.input_store[num]["index"]
                    self.input_store[num]["content"] = input_text[:input_index] + timestamp + input_text[input_index:]
                    self.input_store[num]["index"] = len(input_text[:input_index] + timestamp)
                    break

        elif cmd_type == 34:   # VOTE
            select_num = max(cmd_args.get("num", 0), 0)
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            poll = self.messages[msg_index].get("poll")
            if poll:
                if poll["expires"] > time.time():
                    if select_num > 0 and select_num <= len(poll["options"]):
                        select_num -= 1
                        selected_id = poll["options"][select_num]["id"]
                        me_voted = False
                        for option in poll["options"]:
                            if option["me_voted"]:
                                me_voted = True
                                break
                        if poll["options"][select_num]["me_voted"]:
                            # clear voted
                            success = self.discord.send_vote(self.active_channel["channel_id"], self.messages[msg_index]["id"], [], clear=True)
                        elif not me_voted or poll["multi"]:
                            # send vote
                            success = self.discord.send_vote(self.active_channel["channel_id"], self.messages[msg_index]["id"], [selected_id])
                        else:
                            self.update_extra_line("Already voted.")
                    else:
                        self.update_extra_line("Can't vote - selected answer doesn't exist.")
                else:
                    self.update_extra_line("Can't vote - poll has ended.")
            else:
                self.update_extra_line("Can't vote - selected message is not a poll.")

        elif cmd_type == 35:   # SHOW_PINNED
            self.view_pinned()

        elif cmd_type == 36:   # PIN_MESSAGE
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            if "deleted" not in self.messages[msg_index] and "pending" not in self.messages[msg_index]:
                if not self.active_channel["guild_id"] or (self.active_channel["admin"] or self.active_channel.get("allow_manage")):
                    success = self.discord.send_pin(
                        self.active_channel["channel_id"],
                        self.messages[msg_index]["id"],
                    )
                else:
                    self.update_extra_line("Can't pin a message - not permitted.")

        elif cmd_type == 37:   # PUSH_BUTTON
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            message = self.messages[msg_index]
            if "component_info" in message and message["component_info"]["buttons"]:
                disabled = False
                if "num" in cmd_args:
                    try:
                        button = message["component_info"]["buttons"][int(cmd_args["num"])-1]
                        custom_id = button["id"]
                        disabled = button["disabled"]
                    except IndexError:
                        custom_id = None
                else:
                    name = cmd_args["name"].lower()
                    for button in message["component_info"]["buttons"]:
                        if button["text"].lower() == name:
                            custom_id = button["id"]
                            disabled = button["disabled"]
                            break
                    else:
                        custom_id = None
                if disabled:
                    self.update_extra_line("Can't push button, its disabled.")
                elif custom_id:
                    command_data = {
                        "component_type": 2,
                        "custom_id": custom_id,
                    }
                    success = self.discord.send_interaction(
                        self.active_channel["guild_id"],
                        self.active_channel["channel_id"],
                        self.session_id,
                        message["user_id"],   # user_id is app_id
                        3,
                        command_data,
                        None,
                        message_id=message["id"],
                    )
                else:
                    self.update_extra_line("Button not found.")

        elif cmd_type == 38:   # STRING_SELECT
            chat_sel, _ = self.tui.get_chat_selected()
            msg_index = self.lines_to_msg(chat_sel)
            if msg_index is None:
                return
            message = self.messages[msg_index]
            if "component_info" in message and message["component_info"]["buttons"]:
                if cmd_args["num"]:
                    num = max(int(cmd_args["num"])-1, 0)
                else:
                    num = 0
                custom_id = None
                try:
                    string_select = message["component_info"]["string_selects"][num]
                    custom_id = string_select["id"]
                    disabled = string_select["disabled"]
                except IndexError:
                    custom_id = None
                if disabled:
                    self.update_extra_line("Can't select string, its disabled.")
                elif custom_id:
                    # check if label is valid and select value
                    label = cmd_args["text"].lower().strip()
                    for option in string_select["options"]:
                        if label == option["label"].lower():
                            value = option["value"]
                            break
                    else:
                        value = None
                        self.update_extra_line("Specified value is invalid")
                    if value:
                        command_data = {
                            "component_type": 3,
                            "custom_id": custom_id,
                            "values": [value],
                        }
                        success = self.discord.send_interaction(
                            self.active_channel["guild_id"],
                            self.active_channel["channel_id"],
                            self.session_id,
                            message["user_id"],   # user_id is app_id
                            3,
                            command_data,
                            None,
                            message_id=message["id"],
                        )
                else:
                    self.update_extra_line("String selection not found.")

        elif cmd_type == 39:   # DUMP_CHAT
            if self.forum:
                self.update_extra_line("Cant dump chat, this is forum.")
            else:
                unique_name = f"chat_dump_{time.strftime("%Y-%m-%d-%H-%M-%S")}.json"
                debug.save_json({
                    "channel": self.current_channel,
                    "chat": self.messages,
                }, unique_name)
                self.update_extra_line(f"Chat saved to: {os.path.join(peripherals.log_path, "Debug")}")

        elif cmd_type == 40:   # SET_NOTIFICATIONS
            channel_id = cmd_args["id"]
            if channel_id:
                _, _, guild_id, _, _ = self.find_parents_from_id(channel_id)
            else:
                channel_id = self.tree_metadata[tree_sel]["id"]
                guild_id = self.find_parents_from_tree(tree_sel)[0]
            if guild_id:   # set channel/category
                if cmd_args["setting"].startswith("suppress"):
                    self.update_extra_line("Cant set that option for channel.")
                else:
                    self.discord.send_notification_setting_channel(cmd_args["setting"], channel_id, guild_id)
            else:
                for dm in self.dms:
                    if dm["id"] == channel_id:
                        self.update_extra_line("DM has no notification settings.")
                        break
                else:   # set guild
                    for guild in self.guilds:
                        if guild["guild_id"] == channel_id:
                            break
                    else:
                        guild = None
                    if guild:
                        if cmd_args["setting"] == "suppress_everyone":
                            value = not guild.get("suppress_everyone")
                        elif cmd_args["setting"] == "suppress_roles":
                            value = not guild.get("suppress_roles")
                        else:
                            value = None
                        self.discord.send_notification_setting_guild(cmd_args["setting"], channel_id, value)
                    else:
                        self.update_extra_line("Guild not found.")

        elif cmd_type == 41:   # GIF
            search_text = cmd_args.get("search_text", None)
            if search_text:
                reset = False
                max_w = self.tui.get_dimensions()[2][1]
                self.add_running_task("Searching gifs", 4)
                self.search_messages = self.discord.search_gifs(search_text)
                self.remove_running_task("Searching gifs", 4)
                if self.search_messages is None:
                    self.search_messages = []
                    self.gateway.set_offline()
                    self.stop_assist()
                    self.update_extra_line("Network error.")
                    self.restore_input_text = (input_text, "standard")
                    return
                extra_title, extra_body = formatter.generate_extra_window_search_gif(self.search_messages, max_w)
                self.tui.draw_extra_window(extra_title, extra_body, select=True)
                self.restore_input_text = (None, "search")
                self.reset_states()
                self.search = True
                self.ignore_typing = True
                self.update_status_line()
                self.extra_window_open = True
            elif not self.search:
                reset = False
                self.reset_states()
                self.restore_input_text = (None, "search")
                self.search_gif = True
                self.ignore_typing = True
                self.stop_assist(close=False)
                self.tui.remove_extra_window()
                self.extra_window_open = True

        elif cmd_type == 42:   # REDRAW
            self.tui.force_redraw()

        elif cmd_type == 43 and self.external_editor is not None:   # EXTERNAL_EDIT
            self.tui.pause_curses()
            channel = self.active_channel["channel_id"]
            timestamp = int(time.time() * 1000)
            temp_message_path = os.path.join(peripherals.temp_path, f"message-{timestamp}")
            for num, channel in enumerate(self.input_store):
                if channel["id"] == self.active_channel["channel_id"]:
                    input_text = self.input_store[num]["content"]
                    break
            else:
                input_text = ""
            with open(temp_message_path, "w", encoding="utf-8") as file:
                file.write(input_text)
            subprocess.run([self.external_editor, temp_message_path], check=True)
            if os.path.exists(temp_message_path):
                with open(temp_message_path, "r", encoding="utf-8") as f:
                    new_text = f.read().strip("\n")
                os.remove(temp_message_path)
                self.tui.input_buffer = new_text
                self.tui.set_input_index(len(new_text))
                self.add_to_store(self.active_channel["channel_id"], new_text)
            self.tui.resume_curses()

        elif cmd_type == 44:   # CUSTOM_STATUS/EMOJI/REMOVE
            if "text" in cmd_args:
                self.my_status["custom_status"] = cmd_args["text"][:128]
                if len(cmd_args["text"]) > 128:
                    self.update_extra_line("Text has been trimmed to 128 characters.")
            elif "emoji" in cmd_args:
                match = re.search(formatter.match_d_emoji, cmd_args["emoji"])
                if match:
                    if self.premium:
                        self.my_status["custom_status_emoji"] = {
                            "id": match.group(3),
                            "name": match.group(2),
                            "animated": match.group(1) == "a",
                        }
                    else:
                        self.update_extra_line("Must have nitro to set custom emoji.")
                else:
                    self.my_status["custom_status_emoji"] = {
                        "id": None,
                        "name": emoji.emojize(cmd_args["emoji"], language="alias", variant="emoji_type"),
                        "animated": False,
                    }
            else:
                self.my_status["custom_status"] = None
                self.my_status["custom_status_emoji"] = None

            settings = {
                "status": {
                    "status": self.my_status["status"],
                    "showCurrentGame": True,
                },
            }
            if self.my_status["custom_status"] or self.my_status["custom_status_emoji"]:
                settings["status"]["customStatus"] = {}
            if self.my_status["custom_status"]:
                settings["status"]["customStatus"]["text"] = self.my_status["custom_status"]
            if self.my_status["custom_status_emoji"]:
                settings["status"]["customStatus"]["emojiName"] = self.my_status["custom_status_emoji"]["name"]
            if not self.gateway.legacy:
                success = self.discord.patch_settings_proto(1, settings)
            else:
                success = self.discord.patch_settings_old("custom_status", {"text": self.my_status["custom_status"]})
            if success:
                self.gateway.update_presence(
                    self.my_status["status"],
                    custom_status=self.my_status["custom_status"],
                    custom_status_emoji=self.my_status["custom_status_emoji"],
                    activities=self.my_activities,
                    afk=self.my_status["afk"],
                )

        elif cmd_type == 45:   # BLOCK
            user_id = cmd_args["user_id"]
            ignore = cmd_args["ignore"]
            if user_id != self.my_id:
                success = self.discord.block_user(user_id, ignore)
                if success:
                    self.blocked.append(user_id)
                    self.update_chat()
                    self.update_extra_line("User has been blocked successfully.")
                elif success is False:   # network error is handled at the end
                    self.update_extra_line("Failed to block user.")

        elif cmd_type == 46:   # UNBLOCK
            user_id = cmd_args["user_id"]
            ignore = cmd_args["ignore"]
            if user_id in self.blocked and user_id != self.my_id:
                success = self.discord.unblock_user(user_id, ignore)
                if success:
                    self.blocked.remove(user_id)
                    self.update_chat()
                    self.update_extra_line("User has been unblocked successfully.")
                elif success is False:
                    self.update_extra_line("Failed to unblock user.")
            else:
                self.update_extra_line("User is not blocked.")

        elif cmd_type == 47:   # TOGGLE_BLOCKED_MESSAGES
            self.show_blocked_messages = not self.show_blocked_messages
            self.update_chat()

        elif cmd_type == 48 and self.enable_calls:   # VOICE_START_CALL
            if not self.in_call:
                if not self.active_channel["guild_id"]:
                    threading.Thread(target=self.start_call, daemon=True, args=(False, None, self.active_channel["channel_id"])).start()
                else:
                    self.update_extra_line("Can only start call in DM")
            else:
                self.update_extra_line("Cant join multiple calls")

        elif cmd_type == 49:   # VOICE_ACCEPT_CALL
            if not self.in_call:
                if self.incoming_calls:
                    if self.most_recent_incoming_call:
                        incoming_call_ch_id = self.most_recent_incoming_call
                    else:
                        incoming_call_ch_id = self.active_channel["channel_id"]
                    threading.Thread(target=self.start_call, daemon=True, args=(True, None, incoming_call_ch_id)).start()
            else:
                self.update_extra_line("Cant join multiple calls")

        elif cmd_type == 50:   # VOICE_LEAVE_CALL
            self.leave_call()

        elif cmd_type == 51:   # VOICE_REJECT_CALL
            if self.incoming_calls:
                self.stop_ringing()
                self.most_recent_incoming_call = None
                # remove popup if not in this channel
                if self.active_channel["channel_id"] not in self.incoming_calls:
                    self.update_extra_line(permanent=True)

        elif cmd_type == 52:   # VOICE_TOGGLE_MUTE
            if self.state.get("muted"):
                self.state["muted"] = False
                if self.in_call:
                    self.update_voice_mute_in_call()
                else:
                    self.update_extra_line("Client voice has been UNMUTED.")
            else:
                self.state["muted"] = True
                if self.in_call:
                    self.update_voice_mute_in_call()
                else:
                    self.update_extra_line("Client voice has been MUTED.")
            peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")

        elif cmd_type == 53:   # VOICE_LIST_CALL
            if self.in_call:
                if self.voice_call_list_open:
                    self.close_extra_window()
                else:
                    self.view_voice_call_list(reset=True)

        elif cmd_type == 54:   # GENERATE_INVITE
            if self.active_channel["guild_id"]:
                invite_url = self.discord.get_invite_url(
                    self.active_channel["channel_id"],
                    cmd_args["max_age"],
                    cmd_args["max_uses"],
                )
                if invite_url:
                    peripherals.copy_to_clipboard(invite_url)
                    self.update_extra_line("Servr invite copied to clipboard")
                elif invite_url is None:
                    self.gateway.set_offline()
                    self.update_extra_line("Network error.")
                else:
                    self.update_extra_line("Failed to generate invite, see log for more info")

        elif cmd_type == 55:   # SHOW_LOG
            self.blank_chat()
            self.view_log()

        elif cmd_type == 56:   # RENAME_FOLDER
            folder_id = self.tree_metadata[tree_sel].get("id")
            guild_folders_ids = [x["id"] for x in self.guild_folders]
            if folder_id and folder_id in guild_folders_ids:
                for folder in self.state["folder_names"]:
                    if folder["id"] == folder_id:
                        folder["name"] = cmd_args["name"]
                        break
                else:
                    self.state["folder_names"].append({
                        "id": folder_id,
                        "name": cmd_args["name"],
                    })
                for num, folder in enumerate(self.state["folder_names"]):
                    if folder["id"] not in guild_folders_ids:
                        self.state["folder_names"].pop(num)
                peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")
                self.update_tree()

        elif cmd_type == 57:   # VIEW_EMOJI
            if cmd_args.get("name"):
                match = re.search(formatter.match_d_emoji, cmd_args["name"])
                if match:
                    self.view_emoji(match.group(3))
                else:
                    self.update_extra_line("Invalid emoji. Should be: <:EmojiName:emoji_id>")
            else:
                select_num = cmd_args.get("num", 0)
                if not select_num:
                    select_num = 0
                emojis = self.get_stuff_from_selected_line(chat_sel, 2)   # get emoji
                select_num = max(min(select_num-1, len(emojis)-1), 0)
                self.view_emoji(emojis[select_num])

        elif cmd_type == 58:   # QUIT
            self.gateway.disconnect_ws()
            self.run = False

        elif cmd_type == 59:   # MARK_AS_UNREAD
            msg_index = self.lines_to_msg(self.tui.get_chat_selected()[0], space=True)+1
            msg_index = min(msg_index, len(self.messages)-1)
            message_id = self.messages[msg_index]["id"]
            if message_id:
                self.send_ack(self.active_channel["channel_id"], message_id, manual=True)
                self.set_channel_unseen(self.active_channel["channel_id"], self.get_chat_last_message_id(), False, False, last_acked_message_id=message_id)
                self.update_tree()
                self.update_chat(scroll=False)

        elif cmd_type == 60 and self.current_channel.get("type") in (11, 12):   # TOGGLE_THREAD
            thread_id = self.active_channel["channel_id"]
            guild_id, channel_id, _ = self.find_parents_from_id(self.active_channel)
            self.thread_toggle_join(guild_id, channel_id, thread_id)
            self.update_tree()

        elif cmd_type == 61:   # GAME_DETECTION_BLACKLIST
            if self.enable_game_detection and self.game_detection.run:
                games = self.game_detection.get_detected()
                name = cmd_args["name"].lower()
                app_id = None
                for game in games:
                    if game[1].lower() == name:
                        app_id = game[0]
                        break
                if app_id:
                    if app_id in self.state["games_blacklist"]:
                        self.state["games_blacklist"].remove(app_id)
                    else:
                        self.state["games_blacklist"].append(app_id)
                    self.game_detection.set_blacklist(self.state["games_blacklist"])
                    peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")
            else:
                self.update_extra_line("Game detection service is disabled or not running.")

        elif cmd_type == 62:   # OPEN_CINFIG_DIR
            peripherals.native_open(os.path.expanduser(peripherals.config_path))

        elif cmd_type == 63:   # SEND_MESSAGE
            threading.Thread(target=self.send_message_command, daemon=True, args=(
                cmd_args["content"],
                cmd_args["channel_id"],
                cmd_args["reply_id"],
                cmd_args["ping"],
                cmd_args["attachments"],
            )).start()

        # 64 - COPY_LINK is handled together with 5 - OPEN_LINK

        elif cmd_type == 65:   # TOGGLE_AFK
            self.my_status["afk"] = not self.my_status["afk"]
            self.gateway.update_presence(
                self.my_status["status"],
                custom_status=self.my_status["custom_status"],
                custom_status_emoji=self.my_status["custom_status_emoji"],
                activities=self.my_activities,
                afk=self.my_status["afk"],
            )
            self.update_status_line()

        elif cmd_type == 66 and self.fun:   # 666
            self.fun = 1 if self.fun == 2 else 2
            self.tui.set_fun(self.fun)

        elif cmd_type == 67 and self.fun:   # TOGGLE_SNOW
            self.fun = 1 if self.fun == 3 else 3
            self.tui.set_fun(self.fun)
            self.state["snow"] = self.fun == 3
            peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")

        elif cmd_type == 68:   # REMOVE_ALL_TABS
            for channel in reversed(self.channel_cache):
                if channel[2] is True:
                    self.channel_cache.remove(channel)
            if self.config["remember_tabs"]:
                tabbed_channels = [channel[0] for channel in self.channel_cache if channel[2]]
                if self.active_channel["pinned"]:
                    tabbed_channels.append(self.active_channel["channel_id"])
                self.state["tabbed_channels"] = tabbed_channels
                peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")
            self.update_tabs()

        elif cmd_type == 69:   # COLLAPSE_ALL_EXCEPT
            if "value" not in cmd_args or cmd_args["value"] == 2:
                self.open_guild(self.active_channel["guild_id"], select=True, one_open=True, dont_close_this=True)
            elif cmd_args["value"] in (-1, 0, 1):
                if self.tree_metadata[tree_sel]:
                    if self.tree_metadata[tree_sel]["type"] == -1:
                        guild_id = self.tree_metadata[tree_sel]["id"]
                    else:
                        guild_id, _, _ = self.find_parents_from_tree(tree_sel)
                    if guild_id:
                        self.open_guild(guild_id, select=True, one_open=True, dont_close_this=True, direction=cmd_args["value"])
            else:
                self.update_extra_line("Invalid command arguments.")
                return

        elif cmd_type == 70:   # TREE_SELECT
            if cmd_args["type"]:   # server
                current_target_id = self.tree_metadata[tree_sel]["id"]
                target_types = (-1, )   # server
            else:   # channel
                current_target_id = self.active_channel["channel_id"]
                target_types = (0, 1, 3, 15)   # channel, dm, group, forum
            if not current_target_id:
                current_target_id = 0
            if cmd_args["value"]:   # previous
                tree_metadata = reversed(self.tree_metadata)
            else:
                tree_metadata = self.tree_metadata
            target_id = None
            found = False
            for obj in tree_metadata:
                if obj and obj["id"] == current_target_id:
                    found = True
                elif found and obj and obj["type"] in target_types:
                    target_id = obj["id"]
                    break
            if target_id is not None:
                self.execute_command(25, {"channel_id": target_id}, "", chat_sel, tree_sel)   # goto command
                return

        if success is None:
            self.gateway.set_offline()
            self.update_extra_line("Network error.")
        if reset:
            self.reset_states()
            self.restore_input_text = (None, None)
        self.update_status_line()


    def execute_app_command(self, input_text, autocomplete=False):
        """Parse and execute app command/autocomplete"""
        command_data, app_id, need_attachment = parser.app_command_string(
            input_text,
            self.my_commands,
            self.guild_commands,
            self.guild_commands_permitted,
            self.current_roles,
            self.current_channels,
            not self.active_channel["guild_id"],   # dm
            autocomplete,
        )

        logger.debug(f"App command string: {input_text}, autocomplete: {autocomplete}")
        if logger.getEffectiveLevel() == logging.DEBUG:
            debug.save_json(self.my_commands, "commands_my.json")
            debug.save_json(self.guild_commands, "commands_guild.json")
            debug.save_json(self.guild_commands_permitted, "commands_guild_permitted.json")

        # select attachment
        this_attachments = None
        if need_attachment and not autocomplete:
            attachments = self.ready_attachments.get(self.active_channel["channel_id"])
            if attachments:
                this_attachments = self.ready_attachments.pop(self.active_channel["channel_id"])
                self.update_extra_line()
            if not this_attachments:
                self.update_extra_line("Attachment not provided.")
                self.stop_assist()
                return

        if command_data:
            success = self.discord.send_interaction(
                self.active_channel["guild_id"],
                self.active_channel["channel_id"],
                self.session_id,
                app_id,
                4 if autocomplete else 2,
                command_data,
                this_attachments,
            )
            if success is None:
                self.gateway.set_offline()
                self.stop_assist()
                self.update_extra_line("Network error.")
                return
        else:
            self.update_extra_line("Invalid app command.")
        self.stop_assist()


    def send_message_command(self, content, channel_id, reply_id, ping, attachments_paths):
        """Execute send_message command as a thread"""
        if not channel_id:
            channel_id = self.active_channel["channel_id"]
        reply_guild_id = None
        if reply_id:
            reply_guild_id = self.find_parents_from_id(channel_id)[2]
            if not reply_guild_id:
                reply_id = None
        for attachment in attachments_paths:
            self.upload(attachment, channel_id=channel_id)
        this_attachments = []
        attachments = self.ready_attachments.get(channel_id)
        if attachments:
            if all(attachment.get("state", 0) >= 1 for attachment in attachments):
                this_attachments = self.ready_attachments.pop(channel_id)
                self.update_extra_line()
            else:
                self.update_extra_line("Attachments are still uploading.")
        self.discord.send_message(
            channel_id,
            content,
            reply_id=reply_id,
            reply_channel_id=channel_id,
            reply_guild_id=reply_guild_id,
            reply_ping=ping,
            attachments=this_attachments,
            nonce=None,
        )
        self.update_extra_line("Message sent.")


    def refresh_attachment_url(self, url):
        """Check if provided url is discord attachment url, and refresh it if its expired"""
        queries = self.discord.check_expired_attachment_url(url)
        if queries:
            expiration = queries.get("ex")
            if expiration:
                try:
                    expiration = int(expiration, 16)
                    if len(str(expiration)) > 12:
                        expiration //= 1000
                    if expiration <= int(time.time()):
                        new_url = self.discord.refresh_attachment_url(url)
                        if new_url:
                            return new_url
                        return url
                except ValueError:
                    return url
        elif queries == {}:
            new_url = self.discord.refresh_attachment_url(url)
            if new_url:
                return new_url
            return url
        return url


    def find_parents_from_tree(self, tree_sel):
        """Find object parents from its tree index"""
        guild_id = None
        guild_name = None
        parent_id = None
        parent_index = self.tree_metadata[tree_sel]["parent_index"]
        for i in range(3):   # avoid infinite loops, there can be max 3 nest levels
            if parent_index is None:
                break
            guild_id = self.tree_metadata[parent_index]["id"]
            guild_name = self.tree_metadata[parent_index]["name"]
            parent_index = self.tree_metadata[parent_index]["parent_index"]
            if i == 0 and self.tree_metadata[tree_sel]["type"] in (11, 12):
                parent_id = guild_id
        return guild_id, parent_id, guild_name


    def find_parents_from_id(self, channel_id):
        """Find channel parents from its id"""
        for guild in self.guilds:
            for channel in guild["channels"]:
                if channel["id"] == channel_id:
                    return channel_id, channel["name"], guild["guild_id"], guild["name"], channel["parent_id"]
        # check dms
        for dm in self.dms:
            if dm["id"] == channel_id:
                name = dm["name"]
                return channel_id, name, None, None, None
        return None, None, None, None, None


    def go_bottom(self):
        """Go to chat bottom"""
        self.tui.scroll_bot()
        if self.get_chat_last_message_id() != self.last_message_id:

            # load from cache
            from_cache = self.load_from_channel_cache(self.active_channel["channel_id"])
            if from_cache:
                self.update_chat()

            # download messages
            else:
                self.add_running_task("Downloading chat", 4)
                new_messages = self.get_messages_with_members(num=self.msg_num)
                if new_messages is not None:
                    self.messages = new_messages
                    if self.messages:
                        self.last_message_id = self.get_chat_last_message_id()
                    self.messages = self.get_messages_with_members()
                    self.update_chat()
                    self.tui.allow_chat_selected_hide(self.get_chat_last_message_id() == self.last_message_id)
                    self.remove_running_task("Downloading chat", 4)
                else:
                    self.remove_running_task("Switching channel", 1)
                self.remove_running_task("Downloading chat", 4)


    def go_replied(self, msg_index):
        """Go to replied message from selected message in chat"""
        if self.messages[msg_index]["referenced_message"]:
            reference_id = self.messages[msg_index]["referenced_message"]["id"]
            if reference_id:
                self.go_to_message(reference_id)


    def switch_tab(self, select_num):
        """Switch to specified tab number if it is available"""
        num = 0
        channel_id = None
        for channel in self.channel_cache:
            if channel[2]:
                if num == select_num:
                    channel_id = channel[0]
                    break
                num += 1
        if channel_id:
            channel_id, channel_name, guild_id, guild_name, parent_hint = self.find_parents_from_id(channel_id)
            self.switch_channel(channel_id, channel_name, guild_id, guild_name, parent_hint=parent_hint)


    def get_chat_last_message_id(self):
        """Safely get last message id in currently loaded messages"""
        if len(self.messages):
            if self.messages[0].get("deleted"):
                # skip deleted messages
                for message in self.messages:
                    if not message.get("deleted"):
                        return message["id"]
                else:
                    return 0
            return self.messages[0]["id"]
        return 0


    def get_msg_urls(self, msg_index, embeds=True):
        """Get all urls from message"""
        urls = []
        code_snippets = []
        code_blocks = []
        message_text = self.messages[msg_index]["content"]
        for match in re.finditer(formatter.match_md_code_snippet, message_text):
            code_snippets.append([match.start(), match.end()])
        for match in re.finditer(formatter.match_md_code_block, message_text):
            code_blocks.append([match.start(), match.end()])
        except_ranges = code_snippets + code_blocks
        for match in re.finditer(formatter.match_url, message_text):
            start = match.start()
            end = match.end()
            skip = False
            for except_range in except_ranges:
                start_r = except_range[0]
                end_r = except_range[1]
                if start > start_r and start < end_r and end > start_r and end <= end_r:
                    skip = True
                    break
            if not skip:
                urls.append(match.group())
        if embeds:
            for embed in self.messages[msg_index]["embeds"]:
                if embed["url"]:
                    urls.append(embed["url"])
        return urls


    def get_stuff_from_selected_line(self, chat_sel, thing):
        """
        Get selected stuff indexes in message from selected line in chat.
        Stuff can be: 0 - url, 1 - spoiler, 2 - custom emoji, 3 - mention, 4 - channel
        """
        chat_line_map = self.chat_map[chat_sel]
        if not chat_line_map or not chat_line_map[5]:
            return []
        line_stuff = []
        if chat_line_map[5]:
            for item in chat_line_map[5][thing]:
                line_stuff.append(item[2])
        return line_stuff


    def get_msg_urls_chat(self, msg_index):
        """Get all urls from message, as visible in chat"""
        urls = self.get_msg_urls(msg_index, embeds=False)
        message = self.messages[msg_index]
        content = ""

        if "poll" in message:
            content = formatter.format_poll(message["poll"])

        for embed in message["embeds"]:
            if embed["url"] and not embed.get("hidden") and embed["url"] not in content:
                if content:
                    content += "\n"
                content += f"[{formatter.clean_type(embed["type"])} embed]:\n{embed["url"]}"

        for sticker in message["stickers"]:
            sticker_type = sticker["format_type"]
            if content:
                content += "\n"
            if sticker_type == 1:
                content += f"[png sticker] (can be opened): {sticker["name"]}"
            elif sticker_type == 2:
                content += f"[apng sticker] (can be opened): {sticker["name"]}"
            elif sticker_type == 3:
                content += f"[lottie sticker] (cannot be opened): {sticker["name"]}"
            else:
                content += f"[gif sticker] (can be opened): {sticker["name"]}"

        for match in re.finditer(formatter.match_url, content):
            urls.append(match.group())
        return urls


    def copy_msg_url(self, msg_index):
        """Copy message url to clipboard"""
        guild_id = self.active_channel["guild_id"]
        channel_id = self.active_channel["channel_id"]
        msg_id = self.messages[msg_index]["id"]
        url = f"https://{self.discord.host}/channels/{guild_id}/{channel_id}/{msg_id}"
        peripherals.copy_to_clipboard(url)


    def get_msg_embeds(self, msg_index, media_only=True, stickers=True):
        """Get all palyable media embeds and stickers from message in chat"""
        urls = []
        for embed in self.messages[msg_index]["embeds"]:
            media_type = embed["type"].split("/")[0]
            if embed["url"] and (not media_only or media_type in MEDIA_EMBEDS):
                if media_type == "rich":
                    for num, match in enumerate(re.finditer(formatter.match_url, embed["url"])):
                        if "media" in embed and len(embed["media"]) >= num and embed["media"][num]:
                            urls.append(match.group())
                elif embed.get("main_url"):
                    urls.append(embed["main_url"])
                else:
                    urls.append(embed["url"])

        if stickers:
            for sticker in self.messages[msg_index]["stickers"]:
                sticker_url = discord.get_sticker_url(sticker)
                if sticker_url:
                    urls.append(sticker_url)
        return urls


    def spoil(self, msg_index, spoiler_index=None):
        """Reveal specific or one-by-one spoiler in selected message in chat"""
        if "spoiled" in self.messages[msg_index]:
            if not spoiler_index:
                nums = sorted(self.messages[msg_index]["spoiled"])
                spoiler_index = 0
                for num in nums:
                    if num == spoiler_index:
                        spoiler_index += 1
                    elif num > spoiler_index:
                        break
            self.messages[msg_index]["spoiled"].append(spoiler_index)
        else:
            if not spoiler_index:
                spoiler_index = 0
            self.messages[msg_index]["spoiled"] = [spoiler_index]
        self.update_chat(keep_selected=True, scroll=False)


    def add_pending_message(self, content, nonce, reply_id=None, attachments=None, stickers=None):
        """Add message to chat as pending"""
        if not self.show_pending_messages:
            return
        if self.get_chat_last_message_id() != self.last_message_id:
            return

        # build embeds/attachments
        embeds = []
        if attachments:
            for attachment in attachments:
                embeds.append({
                    "type": "unknown",
                    "name": attachment["name"],
                    "url": attachment["path"],
                })

        # build reply
        referenced_message = None
        if reply_id:
            for message in self.messages:
                if message["id"] == reply_id:
                    referenced_message = message
                    break
            else:
                referenced_message = {
                    "id": reply_id,
                    "timestamp": formatter.discord_timestamp(time.time()),
                    "content": "Unknown content",
                    "mentions": [],
                    "user_id": "-1000",
                    "username": "unknown",
                    "global_name": None,
                    "nick": None,
                    "embeds": [],
                    "stickers": [],
                }

        # build stickers
        if stickers:
            stickers = [{"name": "sticker", "id": s, "format_type": -1} for s in stickers]
        else:
            stickers = []

        # build message
        if not content and not embeds and not stickers:
            return
        message = {
            "pending": True,
            "id": nonce,
            "channel_id": self.active_channel["channel_id"],
            "guild_id": self.active_channel["guild_id"],
            "timestamp": formatter.discord_timestamp(time.time()),
            "edited": False,
            "content": content,
            "mentions": [],
            "mention_roles": [],
            "mention_everyone": None,
            "user_id": self.my_id,
            "username": self.my_user_data["username"],
            "global_name": self.my_user_data["global_name"],   # spacebar_fix - get
            "nick": self.my_user_data["nick"],
            "referenced_message": referenced_message,
            "reactions": [],
            "embeds": embeds,
            "stickers": stickers,   # {name, id, format_type}
            "interaction": None,
        }

        if self.emoji_as_text:
            message = formatter.demojize_message(message)
        self.messages.insert(0, message)
        self.last_message_id = message["id"]
        self.update_chat(change_amount=1, scroll=False)


    def substitute_in_last_message(self, input_text):
        """Try to perform s/ substitutiion in last sent message of this user, if the message is in current buffer"""
        for message in self.messages:
            if message["user_id"] == self.my_id:
                message_id = message["id"]
                content = message["content"]
                break
        else:
            return
        if not content:
            return
        new_content = formatter.substitute(content, input_text)
        if new_content and content != new_content:
            self.put_to_message_sender(self.discord.send_update_message,
                channel_id=self.active_channel["channel_id"],
                message_id=message_id,
                message_content=new_content,
            )


    def upload_native_dialog(self):
        """Thread that waits for native dialog to return list of files to upload"""
        native_file_dialog = self.config["native_file_dialog"]
        if isinstance(native_file_dialog, str):
            native_file_dialog = native_file_dialog.lower()
        if native_file_dialog == "auto" and shutil.which("yazi"):
            self.tui.pause_curses()
            files = peripherals.native_select_files(auto=True)
            self.tui.resume_curses()
        else:
            files = peripherals.native_select_files()
        if files == "ERROR":
            return False
        for file in files:
            self.upload_threads.append(threading.Thread(target=self.upload, daemon=True, args=(file, )))
            self.upload_threads[-1].start()
        return True


    def download_file(self, url, move=True, open_media=False, open_move=False):
        """Thread that downloads and moves file to downloads dir"""
        if url.startswith("https://media.tenor.com/"):
            url = downloader.convert_tenor_gif_type(url, self.tenor_gif_type)
        destination = None
        from_cache = False
        match = re.search(match_youtube, url)
        if match:
            url = match.group()
            if open_media:
                self.add_running_task("Loading video", 2)
                self.media_thread = threading.Thread(target=self.open_media, daemon=True, args=(url, ))
                self.media_thread.start()
                self.remove_running_task("Loading video", 2)
            else:
                self.update_extra_line("Can only play YouTube video.")
            return

        # check if file is already downloaded
        if open_media or open_move:
            for file in self.cached_downloads:
                if url == file[0] and os.path.exists(file[1]):
                    destination = file[1]
                    from_cache = True
                    if open_move and peripherals.get_can_play(destination):
                        open_media = True
                    break

        # refresh discord attachment url if needed
        orig_url = url
        if not open_media or not destination:
            url = self.refresh_attachment_url(url)

        # download
        if not open_media or not destination:
            self.add_running_task("Downloading file", 2)
            self.update_extra_line("File download started.")
            try:
                file_id = self.discord.get_file_id(url)
                path, filename = self.downloader.download(url, file_id)
                if path:
                    if open_move:
                        if peripherals.get_can_play(path):
                            open_media = True
                        else:
                            move = True
                    if move:
                        if not os.path.exists(self.downloads_path):
                            os.makedirs(os.path.dirname(self.downloads_path), exist_ok=True)
                        destination = os.path.join(self.downloads_path, filename)
                        shutil.move(path, destination)
                    else:
                        destination = path
                else:
                    return
            except Exception as e:
                logger.error(f"Failed downloading file: {e}")

            self.remove_running_task("Downloading file", 2)
            if move:
                self.update_extra_line(f"File saved to {peripherals.collapseuser(self.downloads_path)}")

        # open media
        if open_media:
            self.media_thread = threading.Thread(target=self.open_media, daemon=True, args=(destination, ))
            self.media_thread.start()
            if not from_cache and destination:
                self.cached_downloads.append([orig_url, destination])


    def upload(self, path, channel_id=None):
        """Thread that uploads file to currently open channel"""
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            self.update_extra_line("Cant upload file: file does not exist")
            return
        if os.path.isdir(path):
            self.update_extra_line("Cant upload directory")
            return

        if not channel_id:
            channel_id = self.active_channel["channel_id"]

        size = peripherals.get_file_size(path)
        limit = max(USER_UPLOAD_LIMITS[self.premium], GUILD_UPLOAD_LIMITS[self.premium])
        if size > limit:
            self.update_extra_line(f"File is larger than current upload limit: {int(limit/MB)}MB")
            return
        if size > 200*MB:
            self.update_extra_line("Cant upload over cloudflare. File is larger than 200MB.")
            return

        # add attachment to list
        if channel_id not in self.ready_attachments:
            self.ready_attachments[channel_id] = []
        self.ready_attachments[channel_id].append({
            "path": path,
            "name": os.path.basename(path),
            "upload_url": None,
            "upload_filename": None,
            "state": 0,   # 0 - uploading, 1 - done, 2 - too large, 3 - restricted, 4 - failed
        })
        at_index = len(self.ready_attachments[channel_id]) - 1

        self.add_running_task("Uploading file", 2)
        time.sleep(0.01)   # time for tui to clear extra window before drawing extra line
        self.update_extra_line()
        upload_data, code = self.discord.request_attachment_url(channel_id, path)
        if code == 3:
            self.gateway.set_offline()
            self.update_extra_line("Network error.")
        if channel_id not in self.ready_attachments:
            self.update_extra_line()
            self.remove_running_task("Uploading file", 2)
            return
        try:
            if upload_data:
                uploaded = self.discord.upload_attachment(upload_data["upload_url"], path)
                if uploaded is None:
                    self.update_extra_line()
                    self.remove_running_task("Uploading file", 2)
                    return
                if uploaded:
                    self.ready_attachments[channel_id][at_index]["upload_url"] = upload_data["upload_url"]
                    self.ready_attachments[channel_id][at_index]["upload_filename"] = upload_data["upload_filename"]
                    self.ready_attachments[channel_id][at_index]["state"] = 1
                else:
                    self.ready_attachments[channel_id][at_index]["path"] = None
                    self.ready_attachments[channel_id][at_index]["state"] = 4
            else:
                self.ready_attachments[channel_id][at_index]["path"] = None
                if code == 1:
                    self.ready_attachments[channel_id][at_index]["state"] = 4
                elif code == 2:
                    self.ready_attachments[channel_id][at_index]["state"] = 2
        except IndexError:
            self.update_extra_line("Failed uploading attachment.")
        self.update_extra_line()
        self.remove_running_task("Uploading file", 2)


    def cancel_upload(self):
        """Cancel and remove all uploaded attachments for current channel"""
        attachments = self.ready_attachments.get(self.active_channel["channel_id"])
        if attachments:
            for attachment in attachments:
                self.discord.cancel_uploading(url=attachment["upload_url"])
                if attachment["upload_filename"]:
                    self.discord.cancel_attachment(attachment["upload_filename"])
                self.selected_attachment = 0
            self.ready_attachments.pop(self.active_channel["channel_id"])
            self.update_extra_line()


    def cancel_attachment(self):
        """Cancel currently selected attachment"""
        attachments = self.ready_attachments.get(self.active_channel["channel_id"])
        if attachments:
            attachment = attachments.pop(self.selected_attachment)["upload_filename"]
            if not len(attachments):
                self.ready_attachments.pop(self.active_channel["channel_id"])
            if attachment:
                self.discord.cancel_attachment(attachment)
            if self.selected_attachment >= 1:
                self.selected_attachment -= 1
            self.update_extra_line()


    def start_recording(self):
        """Start recording audio message"""
        recorder.start()
        self.recording = True
        self.update_extra_line("RECORDING, Esc to cancel, Enter to send.", timed=False)


    def stop_recording(self, cancel=False):
        """Stop recording audio message and send it"""
        if self.recording:
            self.recording = False
            file_path = recorder.stop()
            self.update_extra_line()
            if not cancel:
                self.add_running_task("Uploading file", 2)
                success = self.discord.send_voice_message(
                    self.active_channel["channel_id"],
                    file_path,
                    reply_id=self.replying["id"],
                    reply_channel_id=self.active_channel["channel_id"],
                    reply_guild_id=self.active_channel["guild_id"],
                    reply_ping=self.replying["mention"],
                )
                if success is None:
                    self.gateway.set_offline()
                    self.update_extra_line("Network error.")
                self.remove_running_task("Uploading file", 2)


    def get_messages_with_members(self, num=50, before=None, after=None, around=None):
        """Get messages, check for missing members, request and wait for member chunk, and update local member list"""
        channel_id = self.active_channel["channel_id"]
        messages = self.discord.get_messages(channel_id, num, before, after, around)
        if messages is None:   # network error
            self.gateway.set_offline()
            self.update_extra_line("Network error.")
            return None

        # restore deleted
        if self.keep_deleted and messages:
            messages = self.restore_deleted(messages)

        # emoji safe
        if self.emoji_as_text:
            for num_msg, message in enumerate(messages):
                messages[num_msg] = formatter.demojize_message(message)

        current_guild = self.active_channel["guild_id"]
        if not current_guild:
            # skipping dms
            return messages

        self.request_missing_members(current_guild, messages)
        return messages


    def request_missing_members(self, current_guild, messages):
        """Loop through all messages and download missing members"""
        if not current_guild:
            # skipping dms
            return

        # find missing members
        missing_members = []
        for message in messages:
            message_user_id = message["user_id"]
            if message_user_id in missing_members:
                continue
            for member in self.current_member_roles:
                if member["user_id"] == message_user_id:
                    break
            else:
                missing_members.append(message_user_id)

        # request missing members
        if missing_members:
            self.missing_members_nonce = discord.generate_nonce()
            self.gateway.request_members(current_guild, missing_members, nonce=self.missing_members_nonce)


    def get_chat_chunk(self, past=True, scroll=False):
        """Get chunk of chat in specified direction and add it to existing chat, trim chat to limited size and trigger update_chat"""
        if self.my_status["client_state"] != "online":
            return
        self.add_running_task("Downloading chat", 4)
        start_id = self.messages[-int(past)]["id"]

        if past:   # when scrolling UP
            logger.debug(f"Requesting chat chunk before {start_id}")
            new_chunk = self.get_messages_with_members(before=start_id)
            if new_chunk is None:   # network error
                self.remove_running_task("Downloading chat", 4)
                return
            if new_chunk and self.get_chat_last_message_id() == self.last_message_id and new_chunk[0]["id"] != self.last_message_id:
                self.add_to_channel_cache(self.active_channel["channel_id"], self.messages, self.active_channel.get("pinned", False))
            selected_msg, remainder = self.lines_to_msg_with_remainder(self.tui.get_chat_selected()[0], space=True)
            selected_id = self.messages[selected_msg]["id"]
            self.messages = self.messages + new_chunk
            all_msg = len(self.messages)
            old_chat_len = len(self.chat)
            selected_line = old_chat_len - 1
            selected_msg = self.lines_to_msg(selected_line, space=True)
            self.messages = self.messages[-self.limit_chat_buffer:]
            if new_chunk:
                self.update_chat(keep_selected=None)
                # when messages are trimmed, keep same selected position
                if len(self.messages) != all_msg:
                    selected_msg_new = selected_msg - (all_msg - len(self.messages))
                    selected_line = self.msg_to_lines(selected_msg_new) - remainder
                self.tui.allow_chat_selected_hide(self.get_chat_last_message_id() == self.last_message_id)
                if scroll:
                    scroll_diff = old_chat_len - 1 - selected_line
                    self.tui.set_chat_index(self.tui.chat_index + 2 - scroll_diff)
                    if selected_id and selected_id > self.messages[0]["id"]:
                        self.tui.set_selected(3, scroll=False)
                    elif len(self.messages) != all_msg:
                        for num, message in enumerate(self.messages):
                            if message["id"] == selected_id:
                                selected_msg_index = self.msg_to_lines(num, smart=True) - remainder
                                break
                        else:
                            selected_msg_index = 3
                        self.tui.set_selected(selected_msg_index, scroll=False)
                else:
                    self.tui.set_selected(selected_line)
            else:
                self.chat_end = True
            self.tui.reset_chat_scrolled_top()

        else:   # when scrolling DOWN
            logger.debug(f"Requesting chat chunk after {start_id}")
            new_chunk = self.get_messages_with_members(after=start_id)
            if new_chunk is None:   # network error
                self.remove_running_task("Downloading chat", 4)
                return
            selected_line = 0
            old_chat_len = len(self.chat)
            selected_msg, remainder = self.lines_to_msg_with_remainder(self.tui.get_chat_selected()[0], space=True)
            selected_id = self.messages[selected_msg]["id"]
            selected_msg = self.lines_to_msg(selected_line, space=True)
            if new_chunk and self.get_chat_last_message_id() == self.last_message_id and new_chunk[0]["id"] != self.last_message_id:
                self.add_to_channel_cache(self.active_channel["channel_id"], self.messages, self.active_channel.get("pinned", False))
            self.messages = new_chunk + self.messages
            all_msg = len(self.messages)
            self.messages = self.messages[:self.limit_chat_buffer]
            self.update_chat(keep_selected=True)
            # keep same selected position
            selected_msg_new = selected_msg + len(new_chunk)
            selected_line = self.msg_to_lines(selected_msg_new) - remainder
            self.tui.allow_chat_selected_hide(self.get_chat_last_message_id() == self.last_message_id)
            if scroll:
                scroll_diff = len(self.chat) - old_chat_len
                self.tui.set_chat_index(selected_line - 4)
                if selected_id < self.messages[-1]["id"]:
                    if self.get_chat_last_message_id() == self.last_message_id:
                        self.tui.set_selected(-1, scroll=False)
                    else:
                        self.tui.set_selected(len(self.chat) - 3, scroll=False)
                elif len(self.messages) != all_msg:
                    for num, message in enumerate(self.messages):
                        if message["id"] == selected_id:
                            selected_msg_index = self.msg_to_lines(num, smart=True) - remainder
                            break
                    else:
                        selected_msg_index = len(self.chat) - 3
                    self.tui.set_selected(selected_msg_index, scroll=False)
            else:
                self.tui.set_selected(selected_line)
        self.remove_running_task("Downloading chat", 4)


    def go_to_message(self, message_id):
        """Check if message is in current chat buffer, if not: load chunk around specified message id and select message"""
        for num, message in enumerate(self.messages):
            if message["id"] == message_id:
                self.tui.set_selected(self.msg_to_lines(num, smart=True))
                break

        else:
            logger.debug(f"Requesting chat chunk around {message_id}")
            self.add_running_task("Downloading chat", 4)
            new_messages = self.get_messages_with_members(around=message_id)
            if new_messages:
                if self.get_chat_last_message_id() == self.last_message_id and new_messages[0]["id"] != self.last_message_id:
                    self.add_to_channel_cache(self.active_channel["channel_id"], self.messages, self.active_channel.get("pinned", False))
                self.messages = new_messages
            self.update_chat(keep_selected=False)
            self.remove_running_task("Downloading chat", 4)

            for num, message in enumerate(self.messages):
                if message["id"] == message_id:
                    self.tui.allow_chat_selected_hide(self.get_chat_last_message_id() == self.last_message_id)
                    self.tui.set_selected(self.msg_to_lines(num, smart=True))
                    break


    def get_forum_chunk(self, force=False):
        """Get chunk of forum and add it to existing chat, no trimming, entries are cached for each forum"""
        self.add_running_task("Downloading forum", 4)
        logger.debug(f"Requesting forum chunk with offset {len(self.forum_old)}")
        num_threads, new_chunk = self.discord.get_threads(
            self.active_channel["channel_id"],
            number=25,
            offset=len(self.forum_old),
            archived=True,
        )
        if self.forum or force:
            self.forum_old += new_chunk
            if num_threads:
                self.update_forum(self.active_channel["guild_id"], self.active_channel["channel_id"])
                self.tui.update_chat(self.chat, self.chat_format)
            else:
                self.chat_end = True
        self.remove_running_task("Downloading forum", 4)


    def preload_chat(self):
        """Download chat before switching channel to allow faster switching, used for initial chat when starting up"""
        self.state = peripherals.load_json(f"state_{self.profiles["selected"]}.json")
        if self.state and self.state["last_channel_id"]:
            messages = self.discord.get_messages(self.state["last_channel_id"], self.msg_num)
            if messages is None:   # network error
                return
            # emoji safe
            if self.emoji_as_text:
                for num, message in enumerate(messages):
                    messages[num] = formatter.demojize_message(message)
            if self.need_preload and messages:
                self.messages = messages
                self.preloaded = True


    def toggle_member_list(self):
        """Toggle member list if there is enough space"""
        if not self.get_members or not self.active_channel["guild_id"]:
            return

        if self.state.get("member_list"):
            self.tui.remove_member_list()
            self.state["member_list"] = False

        elif self.screen.getmaxyx()[1] - self.config["tree_width"] - self.member_list_width - 2 < 32:
            self.update_extra_line("Not enough space to draw member list.")
        else:
            self.update_member_list()
            self.state["member_list"] = True

        self.gateway.set_want_member_list(self.state.get("member_list", True))
        peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")


    def set_status(self, status):
        """Set my status: online, idle, dnd, invisible"""
        if status in STATUS_STRINGS:
            self.my_status["status"] = status
            self.gateway.update_presence(
                status,
                custom_status=self.my_status["custom_status"],
                custom_status_emoji=self.my_status["custom_status_emoji"],
                activities=self.my_activities,
                afk=self.my_status["afk"],
            )
            if self.my_status["custom_status_emoji"]:
                custom_status_emoji_name = self.my_status["custom_status_emoji"]["name"]
            else:
                custom_status_emoji_name = None
            settings = {
                "status":{
                    "status": status,
                    "custom_status": {
                        "text": self.my_status["custom_status"],
                        "emoji_name": custom_status_emoji_name,
                    },
                    "show_current_game": True,
                },
            }
            if not self.gateway.legacy:
                self.discord.patch_settings_proto(1, settings)
            else:   # spacebar_fix - using old user_settings
                self.discord.patch_settings_old("status", status)
            self.update_status_line()


    def view_profile(self, user_id=None, guild_id=None, user_data=None, reset=True):
        """Format and show extra window with profile information"""
        # get and cache user data
        if not user_id and not user_data:
            return
        if not guild_id:
            guild_id = self.active_channel["guild_id"]
        self.stop_assist(close=False)
        if not user_data:
            if self.viewing_user_data["id"] != user_id or self.viewing_user_data["guild_id"] != guild_id:
                if guild_id:
                    user_data = self.discord.get_user_guild(user_id, guild_id)
                else:
                    user_data = self.discord.get_user(user_id)
            if user_data is None:
                self.gateway.set_offline()
                self.update_extra_line("Network error.")
                return
            if not user_data:
                self.update_extra_line("No profile information found.")
                self.viewing_user_data = None
                return
        self.viewing_user_data = user_data

        # collect other data
        max_w = self.tui.get_dimensions()[2][1]
        roles = []
        if user_data["roles"]:
            for role_id in user_data["roles"]:
                for role in self.current_roles:
                    if role["id"] == role_id:
                        roles.append(role["name"])
                        break
        user_id = user_data["id"]
        selected_presence = None
        guild_id = user_data["guild_id"]
        if user_id == self.my_id:
            selected_presence = self.my_status
        elif guild_id:
            for presence in self.current_subscribed_members:
                if presence["id"] == user_id:
                    selected_presence = presence
                    break
            else:   # if none, then subscribe
                self.gateway.subscribe_member(user_id, guild_id)
        else:   # dms
            for presence in self.activities:
                if "id" in presence and presence["id"] == user_id:
                    selected_presence = presence
                    break

        # generate and draw extra window
        extra_title, extra_body = formatter.generate_extra_window_profile(user_data, roles, selected_presence, max_w)
        if self.emoji_as_text:
            extra_title = emoji.demojize(extra_title)
            extra_body = [emoji.demojize(x) for x in extra_body]
        self.tui.draw_extra_window(extra_title, extra_body, reset_scroll=reset)
        self.extra_window_open = True


    def view_selected_channel(self, tree_sel=None, channel_id=None):
        """View selected channel from tree or by its id"""
        channel_sel = None
        if tree_sel:
            ch_type = self.tree_metadata[tree_sel]["type"]
            if ch_type == -1:
                guild_id = self.tree_metadata[tree_sel]["id"]
                for guild in self.guilds:
                    if guild["guild_id"] == guild_id:
                        self.stop_assist(close=False)
                        self.view_channel(guild, True)
                        break
            elif ch_type not in (1, 3, 4, 11, 12):
                channel_id = self.tree_metadata[tree_sel]["id"]
                guild_id = self.find_parents_from_tree(tree_sel)[0]
                for guild in self.guilds:
                    if guild["guild_id"] == guild_id:
                        for channel in guild["channels"]:
                            if channel["id"] == channel_id:
                                channel_sel = channel
                                break
                        break
        else:
            found = False
            for guild in self.guilds:
                for channel in guild["channels"]:
                    if channel["id"] == channel_id:
                        if channel["type"] not in (1, 3, 4, 11, 12):
                            channel_sel = channel
                        found = True
                        break
                if found:
                    break
        if channel_sel:
            self.stop_assist(close=False)
            self.view_channel(channel_sel)


    def view_channel(self, channel, guild=False):
        """Format and show extra window with channel/guild information"""
        max_w = self.tui.get_dimensions()[2][1]
        if guild:
            extra_title, extra_body = formatter.generate_extra_window_guild(channel, max_w)
        else:
            extra_title, extra_body = formatter.generate_extra_window_channel(channel, max_w)
        self.tui.draw_extra_window(extra_title, extra_body)
        self.extra_window_open = True


    def view_summaries(self, channel_id=None):
        """Format and show extra window with this or specified channel summaries"""
        summaries = []
        if not channel_id:
            for guild in self.summaries:
                if guild["guild_id"] == self.active_channel["guild_id"]:
                    for channel in guild["channels"]:
                        if channel["channel_id"] == self.active_channel["channel_id"]:
                            summaries = channel["summaries"]
                            break
                    break
        else:
            found = False
            for guild in self.summaries:
                for channel in guild["channels"]:
                    if channel["channel_id"] == channel_id:
                        summaries = channel["summaries"]
                        found = True
                        break
                if found:
                    break
        self.stop_assist(close=False)
        max_w = self.tui.get_dimensions()[2][1]
        extra_title, extra_body, self.extra_indexes = formatter.generate_extra_window_summaries(summaries, max_w)
        self.tui.draw_extra_window(extra_title, extra_body, select=True)
        self.extra_window_open = True


    def view_pinned(self):
        """Get, format and show pinned messages in this channel"""
        # cache
        for channel in self.pinned:
            if channel["id"] == self.active_channel["channel_id"]:
                pinned = channel["messages"]
                break
        else:
            pinned = self.discord.get_pinned(self.active_channel["channel_id"])
            if pinned is None:
                self.gateway.set_offline()
                self.update_extra_line("Network error.")
                return
            self.pinned.append({
                "channel_id": self.active_channel["channel_id"],
                "messages": pinned,
            })
        self.stop_assist(close=False)
        extra_title, extra_body, self.extra_indexes = formatter.generate_extra_window_search(
            pinned,
            self.current_roles,
            self.current_channels,
            self.blocked,
            len(pinned),
            self.config,
            self.tui.get_dimensions()[2][1],
            pinned=True,
        )
        self.tui.draw_extra_window(extra_title, extra_body, select=True)
        self.extra_window_open = True


    def do_view_reactions(self, msg_index):
        """Format and show extra window with this or specified message reactions details"""
        reactions = self.messages[msg_index]["reactions"]
        if reactions:
            if len(self.messages[msg_index]["reactions"]) == 1:
                if reactions[0]["emoji_id"]:
                    reaction = f"{reactions[0]["emoji"]}:{reactions[0]["emoji_id"]}"
                else:
                    reaction = reactions[0]["emoji"]
                reaction_details = self.discord.get_reactions(
                    self.active_channel["channel_id"],
                    self.messages[msg_index]["id"],
                    reaction,
                    )
                if reaction_details is None:
                    self.gateway.set_offline()
                    self.update_extra_line("Network error.")
                    self.stop_assist()
                    return
                self.stop_assist(close=False)
                max_w = self.tui.get_dimensions()[2][1]
                extra_title, extra_body = formatter.generate_extra_window_reactions(reactions[0], reaction_details, max_w)
                self.tui.draw_extra_window(extra_title, extra_body)
                self.extra_window_open = True
            else:
                self.ignore_typing = True
                self.view_reactions = {
                    "message_id": self.messages[msg_index]["id"],
                    "reactions": reactions,
                }
                self.update_status_line()
                return True


    def view_voice_call_list(self, reset=False):
        """Show voice call participants and their states in extra window"""
        self.stop_assist(close=False)
        extra_title, extra_body = formatter.generate_extra_window_call(
            self.call_participants,
            self.state.get("muted"),
            self.tui.get_dimensions()[2][1],
        )
        self.tui.draw_extra_window(extra_title, extra_body, reset_scroll=reset)
        self.extra_window_open = True
        self.voice_call_list_open = True


    def view_log(self):
        """Show live log in chat area"""
        time.sleep(0.1)   # time to finish self.update_chat in main loop when resize
        self.messages = []
        log = log_queue.read_log_file(os.path.expanduser(os.path.join(peripherals.log_path, peripherals.APP_NAME) + ".log"))
        self.chat, self.chat_format, self.chat_map = formatter.generate_log(
            log,
            self.colors,
            self.tui.get_dimensions()[2][1],
        )
        self.tui.set_selected(-1)
        self.tui.update_chat(self.chat, self.chat_format)

        # start log watche thread
        if not self.log_queue_manager:
            threading.Thread(target=self.log_watcher, daemon=True, args=(log, )).start()


    def log_watcher(self, log=[]):
        """Thread that looks for log changes and updates it in chat area"""
        self.log_queue_manager = log_queue.LogQueueManager(max_size=self.limit_chat_buffer*2)
        self.log_queue_manager.start()
        while self.run:
            new_log_entry = self.log_queue_manager.get_log_entry()
            log.append(new_log_entry)
            if len(log) > 100:
                log.pop(0)
            selected_line, chat_index = self.tui.get_chat_selected()
            old_chat_len = len(self.chat)
            self.chat, self.chat_format, self.chat_map = formatter.generate_log(
                log,
                self.colors,
                self.tui.get_dimensions()[2][1],
            )
            chat_len_diff = old_chat_len - len(self.chat)
            if selected_line != -1:
                self.tui.set_selected(selected_line - chat_len_diff, scroll=False)
            if chat_index:
                self.tui.set_chat_index(chat_index - chat_len_diff)
            self.tui.update_chat(self.chat, self.chat_format)
            if self.active_channel["channel_id"]:   # failsafe
                break
        if self.log_queue_manager:
            self.log_queue_manager.stop()
            self.log_queue_manager = None


    def view_emoji(self, emoji_id):
        """Download and view custom emoji in media player"""
        emoji_path = self.discord.get_emoji(emoji_id)
        if emoji_path:
            self.media_thread = threading.Thread(target=self.open_media, daemon=True, args=(emoji_path, ))
            self.media_thread.start()
        elif emoji_path is None:
            self.gateway.set_offline()
            self.update_extra_line("Network error.")


    def build_reaction(self, text, msg_index=None):
        """Build and send reaction from provided text"""
        first = text.split(" ")[0]
        if msg_index is None:
            msg_index = self.reacting["msg_index"]
        if msg_index is None or msg_index < 0:
            return
        all_reactions = self.messages[msg_index]["reactions"]

        my_present_emojis = []
        my_present_ids = []
        success = False
        for reaction in all_reactions:
            if reaction["me"]:
                if reaction["emoji_id"]:
                    my_present_ids.append(reaction["emoji_id"])
                else:
                    my_present_emojis.append(reaction["emoji"])
        add_to_existing = False

        try:  # existing emoji index
            num = max(int(first) - 1, 0)
            if num < len(all_reactions) and num >= 0:
                # get reaction from existing emoji
                selected_reaction = all_reactions[num]
                if selected_reaction["emoji_id"]:
                    emoji_string = f"<:{selected_reaction["emoji"]}:{selected_reaction["emoji_id"]}>"
                else:
                    emoji_string = selected_reaction["emoji"]
                add_to_existing = True
        except ValueError:   # new emoji
            emoji_string = emoji.emojize(first, language="alias")

        if emoji.is_emoji(emoji_string):   # standard emoji
            if emoji_string not in my_present_emojis:
                if len(all_reactions) < 20 or add_to_existing:
                    success = self.discord.send_reaction(
                        self.active_channel["channel_id"],
                        self.messages[msg_index]["id"],
                        emoji_string,
                    )
                else:
                    self.update_extra_line("Maximum number of reactions reached.")
            else:
                success = self.discord.remove_reaction(
                    self.active_channel["channel_id"],
                    self.messages[msg_index]["id"],
                    emoji_string,
                )

        else:   # discord emoji
            match = re.match(match_emoji, emoji_string)
            if match:
                emoji_name = match.group(1)
                emoji_id = match.group(2)
                if emoji_id not in my_present_ids:
                    if len(all_reactions) < 20 or add_to_existing:
                        # validate discord emoji before adding it
                        valid = False
                        guild_emojis = []
                        if self.premium:
                            for guild in self.gateway.get_emojis():
                                guild_emojis += guild["emojis"]
                        else:
                            for guild in self.gateway.get_emojis():
                                if guild["guild_id"] == self.active_channel["guild_id"]:
                                    guild_emojis += guild["emojis"]
                                    break
                        for guild_emoji in guild_emojis:
                            if guild_emoji["id"] == emoji_id:
                                valid = True
                                break
                        if valid:
                            success = self.discord.send_reaction(
                                self.active_channel["channel_id"],
                                self.messages[msg_index]["id"],
                                f"{emoji_name}:{emoji_id}",
                            )
                    else:
                        self.update_extra_line("Maximum number of reactions reached.")
                else:
                    success = self.discord.remove_reaction(
                        self.active_channel["channel_id"],
                        self.messages[msg_index]["id"],
                        f"{emoji_name}:{emoji_id}",
                    )
        if success is None:
            self.gateway.set_offline()
            self.update_extra_line("Network error.")
        self.restore_input_text = (None, None)


    def close_extra_window(self):
        """Close extra window and toggle its state"""
        self.tui.remove_extra_window()
        self.extra_window_open = False
        self.voice_call_list_open = False
        self.viewing_user_data = {"id": None, "guild_id": None}
        if self.permanent_extra_line:
            self.extra_line = self.permanent_extra_line
            self.tui.draw_extra_line(self.extra_line)


    def do_search(self, text):
        """Perform message search"""
        self.add_running_task("Searching", 4)
        content, channel_id, author_id, mentions, has, max_id, min_id, pinned = parser.search_string(text)
        self.search = (content, channel_id, author_id, mentions, has, max_id, min_id, pinned)
        logger.debug(f"Starting search with params: {self.search}")
        is_dm = not(self.active_channel["guild_id"])
        total_search_messages, self.search_messages = self.discord.search(
            self.active_channel["channel_id"] if is_dm else self.active_channel["guild_id"],
            channel=is_dm,
            content=content,
            channel_id=channel_id,
            author_id=author_id,
            mentions=mentions,
            has=has,
            max_id=max_id,
            min_id=min_id,
            pinned=pinned,
        )
        if total_search_messages is None:
            self.gateway.set_offline()
            self.update_extra_line("Network error.")
            self.remove_running_task("Searching", 4)
            return
        if len(self.search_messages) >= total_search_messages:
            self.search_end = True
        extra_title, self.extra_body, self.extra_indexes = formatter.generate_extra_window_search(
            self.search_messages,
            self.current_roles,
            self.current_channels,
            self.blocked,
            total_search_messages,
            self.config,
            self.tui.get_dimensions()[2][1],
        )
        self.tui.draw_extra_window(extra_title, self.extra_body, select=True)
        self.remove_running_task("Searching", 4)


    def extend_search(self):
        """Repeat search and add more messages"""
        self.add_running_task("Searching", 4)
        logger.debug(f"Extending search with params: {self.search}")
        is_dm = not(self.active_channel["guild_id"])
        total_search_messages, search_chunk = self.discord.search(
            self.active_channel["channel_id"] if is_dm else self.active_channel["guild_id"],
            channel=is_dm,
            content=self.search[0],
            channel_id=self.search[1],
            author_id=self.search[2],
            mentions=self.search[3],
            has=self.search[4],
            max_id=self.search[5],
            min_id=self.search[6],
            pinned=self.search[7],
            offset=len(self.search_messages),
        )
        if total_search_messages is None:
            self.gateway.set_offline()
            self.update_extra_line("Network error.")
            self.remove_running_task("Searching", 4)
            return
        if search_chunk:
            self.search_messages += search_chunk
            if len(self.search_messages) >= total_search_messages:
                self.search_end = True
            extra_title, extra_body_chunk, indexes_chunk = formatter.generate_extra_window_search(
                search_chunk,
                self.current_roles,
                self.current_channels,
                self.blocked,
                total_search_messages,
                self.config,
                self.tui.get_dimensions()[2][1],
            )
            self.extra_body += extra_body_chunk
            self.extra_indexes += indexes_chunk
            self.tui.draw_extra_window(extra_title, self.extra_body, select=len(self.extra_body))
        self.remove_running_task("Searching", 4)


    def assist(self, assist_word, assist_type, query_results=None):
        """Assist when typing: channel, username, role, emoji and sticker"""
        self.assist_type = assist_type
        self.assist_found = []

        if assist_type == 1:   # channels
            if not self.command:   # current guild channels
                self.assist_found = search.search_channels_guild(
                    self.current_channels,
                    assist_word,
                    limit=self.assist_limit,
                    score_cutoff=self.assist_score_cutoff,
                )
            else:   # all guilds, channels, and dms
                self.assist_found = search.search_channels_all(
                    self.guilds,
                    self.dms,
                    assist_word,
                    self.tui.input_buffer,
                    limit=self.assist_limit,
                    score_cutoff=self.assist_score_cutoff,
                )

        elif assist_type == 2:   # username/role
            self.assist_found = search.search_usernames_roles(
                self.current_roles,
                query_results,
                self.active_channel["guild_id"],
                self.gateway,
                assist_word,
                presences=self.current_subscribed_members,
                limit=self.assist_limit,
                score_cutoff=self.assist_score_cutoff,
            )

        elif assist_type == 3:   # emoji
            self.assist_found = search.search_emojis(
                self.gateway.get_emojis(),
                self.premium,
                self.active_channel["guild_id"],
                assist_word,
                safe_emoji=self.emoji_as_text,
                limit=self.assist_limit,
                score_cutoff=self.assist_score_cutoff,
            )

        elif assist_type == 4:   # sticker
            if self.config["default_stickers"]:
                default_stickers = self.discord.get_stickers()
            else:
                default_stickers = []
            self.assist_found = search.search_stickers(
                self.gateway.get_stickers(),
                default_stickers,
                self.premium,
                self.active_channel["guild_id"],
                assist_word,
                limit=self.assist_limit,
                score_cutoff=self.assist_score_cutoff,
            )

        elif assist_type == 5:   # client command
            if assist_word.lower().startswith("set "):
                if assist_word[4:]:
                    self.assist_found = search.search_settings(
                        self.config,
                        assist_word[4:],
                        limit=self.assist_limit,
                        score_cutoff=self.assist_score_cutoff,
                    )
                else:
                    for key, value in self.config.items():
                        self.assist_found.append((f"{key} = {value}", f"set {key} = {value}"))

            elif assist_word.lower().startswith("string_select "):
                chat_sel, _ = self.tui.get_chat_selected()
                msg_index = self.lines_to_msg(chat_sel)
                if msg_index is not None:
                    message = self.messages[msg_index]
                    if "component_info" in message and message["component_info"]["buttons"]:
                        self.assist_found = search.search_string_selects(
                            message,
                            assist_word[14:],
                            limit=self.assist_limit,
                            score_cutoff=self.assist_score_cutoff,
                        )

            elif assist_word.lower().startswith("set_notifications "):
                query_words = assist_word.split(" ")
                channel_id = None
                if len(query_words) > 1:
                    match = re.search(parser.match_channel, query_words[1])
                    if match:
                        channel_id = match.group(1)
                if channel_id:
                    _, _, guild_id, _, _ = self.find_parents_from_id(channel_id)
                else:
                    tree_sel = self.tui.get_tree_selected()
                    channel_id = self.tree_metadata[tree_sel]["id"]
                    guild_id = self.find_parents_from_tree(tree_sel)[0]
                if assist_word.endswith(" ") and not all(not x for x in query_words[1:]):
                    guild_id = None   # skip all
                    channel_id = None
                else:
                    self.assist_found = search.search_set_notifications(
                        self.guilds,
                        self.dms,
                        guild_id,
                        channel_id,
                        discord.PING_OPTIONS,
                        assist_word,
                        score_cutoff=self.assist_score_cutoff,
                    )

            elif assist_word.lower().startswith("game_detection_blacklist ") and self.enable_game_detection and self.game_detection.run:
                self.assist_found = search.search_games(
                    self.game_detection.get_detected(),
                    self.state["games_blacklist"],
                    assist_word[25:],
                    limit=self.assist_limit,
                    score_cutoff=self.assist_score_cutoff,
                )

            elif assist_word.lower().startswith("switch_tab "):
                self.assist_found = search.search_tabs(
                    self.tabs_names,
                    assist_word[11:],
                    limit=self.assist_limit,
                    score_cutoff=self.assist_score_cutoff,
                )

            elif assist_word.lower().startswith("collapse_all_except "):
                self.assist_found = search.search_options(
                    COLLAPSE_ALL_EXCEPT_OPTIONS,
                    assist_word[20:],
                    assist_word[:19],
                    limit=self.assist_limit,
                    score_cutoff=self.assist_score_cutoff,
                )

            elif assist_word:
                self.assist_found = search.search_client_commands(
                    COMMAND_ASSISTS,
                    assist_word,
                    limit=self.assist_limit,
                    score_cutoff=self.assist_score_cutoff,
                )
            else:
                self.assist_found = COMMAND_ASSISTS

        elif assist_type == 6:   # app commands
            assist_words = assist_word[1:].split(" ")
            depth = len(assist_words)
            self.assist_found, autocomplete = search.search_app_commands(
                self.guild_apps,
                self.guild_commands,
                self.my_apps,
                self.my_commands,
                depth,
                self.guild_commands_permitted,
                not self.active_channel["guild_id"],
                self.assist_skip_app_command,
                parser.match_command_arguments,
                assist_word[1:],
                self.assist_limit,
                self.assist_score_cutoff,
            )
            if autocomplete:
                query = assist_word.lower()
                if self.allow_app_command_autocomplete and self.app_command_autocomplete != query and time.time() - self.app_command_sent_time > INTERACTION_THROTTLING:
                    self.app_command_autocomplete = assist_word.lower()
                    self.execute_app_command(query, autocomplete=True)
                    self.app_command_sent_time = time.time()
                elif self.app_command_autocomplete_resp:
                    for choice in self.app_command_autocomplete_resp:
                        self.assist_found.append((choice["name"], choice["value"]))

        elif assist_type == 7:   # upload file select
            if query_results:
                for path in query_results:
                    name = os.path.basename(os.path.normpath(path))
                    if os.path.isdir(path):
                        name += "/"
                    self.assist_found.append((name, path))
            elif os.path.exists(assist_word):
                if os.path.isdir(assist_word):
                    self.assist_found.append(("Provided path is a directory", False))
                else:
                    self.assist_found.append(("Press enter to upload this file", True))
            else:
                self.assist_found.append(("Provided path is invalid", True))

        max_w = self.tui.get_dimensions()[2][1]
        extra_title, extra_body = formatter.generate_extra_window_assist(self.assist_found, assist_type, max_w)
        self.extra_window_open = True
        if (self.search or self.search_gif or self.command) and not (self.assist_word or self.assist_word == " "):
            self.extra_bkp = (self.tui.extra_window_title, self.tui.extra_window_body)
        self.assist_word = assist_word
        self.tui.draw_extra_window(extra_title, extra_body, select=True)


    def stop_assist(self, close=True):
        """Stop assisting and hide assist UI"""
        self.tui.instant_assist = False
        if self.assist_word:
            if close:
                self.close_extra_window()
            self.assist_word = None
            self.assist_type = None
            self.assist_found = []
            self.tui.assist_start = -1
            # if search was open, restore it
            if (self.search or self.search_gif or self.command) and self.extra_bkp:
                self.extra_window_open = True
                self.tui.draw_extra_window(self.extra_bkp[0], self.extra_bkp[1], select=True)


    def stop_extra_window(self, update=True):
        """Properly close extra window, no matter on content"""
        self.tui.instant_assist = False
        self.close_extra_window()
        if self.search or self.search_gif or self.command:
            self.ignore_typing = False
            self.tui.typing = time.time() - 5
        self.search = False
        self.search_gif = False
        self.tui.disable_wrap_around(False)
        self.search_end = False
        self.search_messages = []
        self.command = False
        self.uploading = False
        if update:
            self.update_status_line()
            self.stop_assist()
        self.assist_word = None


    def insert_assist(self, input_text, index, start, end):
        """Insert specified assist to specified position in the text"""
        if index >= len(self.assist_found) or index < 0:
            return None, None
        if self.assist_type == 1:   # channel
            insert_string = f"<#{self.assist_found[index][1]}>"   # format: "<#ID>"
        elif self.assist_type == 2:   # username/role
            # username format: "<@ID>"
            # role format: "<@&ID>" - already has "&" in ID
            insert_string = f"<@{self.assist_found[index][1]}>"
        elif self.assist_type == 3:   # emoji
            # default emoji - :emoji_name:
            # discord emoji format: "<:name:ID>"
            insert_string = self.assist_found[index][1]
        elif self.assist_type == 4:   # sticker
            insert_string = f"<;{self.assist_found[index][1]};>"   # format: "<;ID;>"
        elif self.assist_type == 5:   # command
            if self.assist_found[index][1]:
                if input_text.endswith(" ") and input_text not in ("set ", "string_select ", "set_notifications ", "game_detection_blacklist ", "switch_tab "):
                    self.tui.instant_assist = False
                    command_type, command_args = parser.command_string(input_text)
                    self.close_extra_window()
                    self.execute_command(
                        command_type,
                        command_args,
                        input_text,
                        self.tui.get_chat_selected()[0],
                        self.tui.get_tree_selected(),
                    )
                    self.add_to_command_history(input_text)
                    self.command = False
                    self.assist_word = None
                    return "", 1000000   # means its command execution and should restore text from store
                new_text = self.assist_found[index][1] + " "
                new_pos = len(new_text)
                return new_text, new_pos
            return input_text, len(input_text)
        elif self.assist_type == 6:   # app command
            if self.assist_found[index][1] is None:   # execute app command
                if self.forum:
                    self.update_extra_line("Cant run app command in forum")
                else:
                    self.execute_app_command(input_text)
                return "", 0
            # check if this is option choice
            match = re.search(parser.match_command_arguments, input_text.split(" ")[-1])
            if match:
                # replace word after "="
                if match.group(2):
                    new_text = input_text[:-len(match.group(2))] + str(self.assist_found[index][1])
                else:
                    new_text = input_text + str(self.assist_found[index][1])
            else:
                # replace last word
                words = input_text.split(" ")
                if words:
                    words[-1] = self.assist_found[index][1]
                    new_text = " ".join(words)
                else:
                    new_text = ""
            if not new_text.endswith("="):   # dont add space if its option
                new_text = new_text + " "
            new_pos = len(new_text)
            return new_text, new_pos
        elif self.assist_type == 7:   # upload file select
            if self.assist_found[index][1] is True or self.assist_found[index][1] == input_text:
                if self.uploading:
                    self.upload_threads.append(threading.Thread(target=self.upload, daemon=True, args=(input_text, )))
                    self.upload_threads[-1].start()
                self.uploading = False
                self.stop_assist()
                return None, 0
            if self.assist_found[index][1] is False:
                return input_text, len(input_text)
            new_text = self.assist_found[index][1]
            new_pos = len(new_text)
            return new_text, new_pos
        if not end:
            end = len(input_text)
        new_text = input_text[:start-1] + insert_string + input_text[end:]
        new_pos = len(input_text[:start-1] + insert_string)
        self.stop_assist()
        return new_text, new_pos


    def cache_deleted(self):
        """Cache all deleted messages from current channel"""
        if not self.active_channel["channel_id"]:
            return
        for channel in self.deleted_cache:
            if channel["channel_id"] == self.active_channel["channel_id"]:
                this_channel_cache = channel["messages"]
                break
        else:
            self.deleted_cache.append({
                "channel_id": self.active_channel["channel_id"],
                "messages": [],
            })
            this_channel_cache = self.deleted_cache[-1]["messages"]
        for message in self.messages:
            if message.get("deleted"):
                for message_c in this_channel_cache:
                    if message_c["id"] == message["id"]:
                        break
                else:
                    this_channel_cache.append(message)
                    if len(this_channel_cache) > self.limit_cache_deleted:
                        this_channel_cache.pop(0)


    def restore_deleted(self, messages):
        """Restore all cached deleted messages for this channels in the correct position"""
        for channel in self.deleted_cache:
            if channel["channel_id"] == self.active_channel["channel_id"]:
                this_channel_cache = channel["messages"]
                break
        else:
            return messages
        for message_c in this_channel_cache:
            message_c_id = int(message_c["id"])
            # ids are discord snowflakes containing unix time so it can be used as message sent time
            if message_c_id < int(messages[-1]["id"]):
                # if message_c date is before last message date
                continue
            if message_c_id > int(messages[0]["id"]):
                # if message_c date is after first message date
                if int(messages[0]["id"]) >= int(self.last_message_id):
                    # if it is not scrolled up
                    messages.insert(0, message_c)
                continue
            for num, message in enumerate(messages):
                try:
                    if int(message["id"]) > message_c_id > int(messages[num+1]["id"]):
                        # if message_c date is between this and next message dates
                        messages.insert(num+1, message_c)
                        break
                except IndexError:
                    break
        return messages


    def open_media(self, path):
        """
        If TUI mode: prevent other UI updates, draw media and wait for input, after quitting - update UI
        If native mode: just open the file/url
        """
        if support_media and not self.config["native_media_player"] and not uses_pgcurses:
            if not self.terminal_media:
                from endcord import media
                if support_media:
                    self.terminal_media = media.TerminalMedia(self.config, self.keybindings)
            self.update_extra_line()
            self.tui.pause_curses()
            self.terminal_media.play(path)
            time.sleep(0.1)
            self.tui.resume_curses()
        else:
            if shutil.which(self.config["yt_dlp_path"]) and shutil.which(self.config["mpv_path"]):
                mpv_path = self.config["mpv_path"]
            else:
                mpv_path = ""
            self.update_extra_line("Media will be opened in native app")
            peripherals.native_open(path, mpv_path, yt_in_mpv=self.config["yt_in_mpv"])


    def update_chat(self, keep_selected=True, change_amount=0, select_message_index=None, scroll=True, select_unread=False):
        """Generate chat and update it in TUI"""
        if self.messages is None:
            return

        if keep_selected:
            selected_line, text_index = self.tui.get_chat_selected()
            if selected_line == -1:
                keep_selected = False
            selected_msg, remainder = self.lines_to_msg_with_remainder(selected_line, space=True)

        # spacebar_fix - message/referenced_message is always null, instead only for deleted message
        if self.gateway.legacy:
            for message in self.messages:
                if message["referenced_message"] and not message["referenced_message"]["id"]:
                    message["referenced_message"] = None

        # find message id for last acked line
        last_seen_msg = None
        channel_id = self.active_channel["channel_id"]
        channel = self.read_state.get(channel_id)
        if channel:
            last_acked_unreads_line = channel.get("last_acked_unreads_line")
            last_message_id = channel["last_message_id"]
            if last_acked_unreads_line and (not last_message_id or int(last_acked_unreads_line) < int(last_message_id)):
                last_seen_msg = channel["last_acked_unreads_line"]

        self.chat, self.chat_format, self.chat_map, wide_map = formatter.generate_chat(
            self.messages,
            self.current_roles,
            self.current_channels,
            self.chat_dim[1],
            self.my_id,
            self.current_my_roles,
            self.current_member_roles,
            self.colors,
            self.colors_formatted,
            self.blocked,
            last_seen_msg,
            self.show_blocked_messages,
            self.config,
        )
        self.tui.set_wide_map(wide_map)

        if keep_selected:
            selected_msg = selected_msg + change_amount
            selected_line_new = self.msg_to_lines(selected_msg) - remainder
            change_amount_lines = selected_line_new - selected_line
            self.tui.set_selected(selected_line_new, change_amount=change_amount_lines, scroll=scroll, draw=False)
        elif select_message_index is not None:
            full_message = (self.config["message_spacing"] and select_unread)
            selected_line_new = abs(self.msg_to_lines(select_message_index, full=select_unread)) + 1 + full_message
            self.tui.set_selected(selected_line_new, scroll=scroll, draw=False)
        elif keep_selected is not None:
            self.tui.set_selected(-1, scroll=scroll, draw=False)   # return to bottom

        self.tui.update_chat(self.chat, self.chat_format)


    def update_forum(self, guild_id, channel_id):
        """Generate forum instead chat and update it in TUI"""
        # using self.messages as forum entries, should not be overwritten while in forum
        self.messages = []
        for guild in self.threads:
            if guild["guild_id"] == guild_id:
                for channel in guild["channels"]:
                    if channel["channel_id"] == channel_id:
                        self.messages = channel["threads"]
                        break
                break
        messages = self.messages + self.forum_old
        self.chat, self.chat_format = formatter.generate_forum(
            messages,
            self.blocked,
            self.chat_dim[1],
            self.colors,
            self.colors_formatted,
            self.config,
        )
        self.chat_map = [None] * len(messages)
        self.tui.set_wide_map([])

    def update_member_list(self, last_index=None, reset=False):
        """Generate member list and update it in TUI"""
        if last_index is not None and not self.tui.mlist_index-1 < last_index < self.tui.mlist_index-1 + self.screen.getmaxyx()[0]:
            return   # dont regenerate for changes that are not visible
        member_list, member_list_format = formatter.generate_member_list(
            self.member_list,
            self.current_roles,
            self.member_list_width + 1 - self.tui.bordered,
            self.use_nick,
            self.status_char,
        )
        self.tui.draw_member_list(member_list, member_list_format, reset=reset)


    def update_tabs(self, no_redraw=False, add_current=False):
        """Generate tab string and update status line"""
        tabs = []
        for channel in self.channel_cache:
            if channel[2]:
                tabs.append(channel[0])

        # add active channel if needed
        active_channel_id = self.active_channel["channel_id"]
        if add_current and self.active_channel["pinned"] and active_channel_id not in tabs:
            tabs.append(active_channel_id)

        # get index of active tab
        active_tab_index = None
        if active_channel_id in tabs:
            active_tab_index = tabs.index(active_channel_id)

        # get tab channel names
        tabs_names_unsorted = []
        for guild in self.guilds:
            guild_name = guild["name"]
            for channel in guild["channels"]:
                if channel["id"] in tabs:
                    tabs_names_unsorted.append({
                        "channel_id": channel["id"],
                        "channel_name": channel["name"],
                        "guild_name": guild_name,
                    })
                    if len(tabs_names_unsorted) == len(tabs):
                        break
        for dm in self.dms:
            if dm["id"] in tabs:
                tabs_names_unsorted.append({
                    "channel_id": dm["id"],
                    "channel_name": dm["name"],
                    "guild_name": "DM",
                })
        # sort names
        self.tabs_names = []
        for tab in tabs:
            for named_tab in tabs_names_unsorted:
                if named_tab["channel_id"] == tab:
                    self.tabs_names.append(named_tab)

        # build tab string
        self.tab_string, self.tab_string_format = formatter.generate_tab_string(
            self.tabs_names,
            active_tab_index,
            self.read_state,
            self.config["format_tabs"],
            self.config["tabs_separator"],
            self.config["limit_channel_name"],
            self.config["limit_tabs_string"],
        )
        if not no_redraw:
            self.update_status_line()


    def update_status_line(self, status=True, title=True, tree=True):
        """Generate status and title lines and update them in TUI"""
        action = {
            "type": None,
            "username": None,
            "global_name": None,
            "mention": None,
        }
        if self.replying["id"]:
            action = {
                "type": 1,
                "username": self.replying["username"],
                "global_name": self.replying["global_name"],
                "mention": self.replying["mention"],
            }
        elif self.editing:
            action["type"] = 2
        elif self.deleting:
            action["type"] = 3
        elif self.downloading_file["urls"]:
            if self.downloading_file["web"]:
                action["type"] = 4
            elif self.downloading_file["open"]:
                action["type"] = 6
            else:
                action["type"] = 5
        elif self.cancel_download:
            action["type"] = 7
        elif self.uploading:
            action["type"] = 8
        elif self.hiding_ch["channel_id"]:
            action["type"] = 9
        elif self.going_to_ch:
            action["type"] = 10
        elif self.reacting["id"]:
            action = {
                "type": 11,
                "username": self.reacting["username"],
                "global_name": self.reacting["global_name"],
            }
        elif self.view_reactions["message_id"]:
            action["type"] = 12
        else:
            action["type"] = 0

        if status:
            if self.format_status_line_r:
                status_line_r, status_line_r_format = formatter.generate_status_line(
                    self.my_user_data,
                    self.my_status,
                    self.new_unreads,
                    self.typing,
                    self.active_channel,
                    action,
                    self.running_tasks,
                    self.tab_string,
                    self.tab_string_format,
                    self.format_status_line_r,
                    self.format_rich,
                    slowmode=self.slowmode_times.get(self.active_channel["channel_id"]),
                    limit_typing=self.limit_typing,
                    fun=self.fun,
                )
            else:
                status_line_r = None
                status_line_r_format = []
            status_line_l, status_line_l_format = formatter.generate_status_line(
                self.my_user_data,
                self.my_status,
                self.new_unreads,
                self.typing,
                self.active_channel,
                action,
                self.running_tasks,
                self.tab_string,
                self.tab_string_format,
                self.format_status_line_l,
                self.format_rich,
                slowmode=self.slowmode_times.get(self.active_channel["channel_id"]),
                limit_typing=self.limit_typing,
                fun=self.fun,
            )
            self.tui.update_status_line(status_line_l, status_line_r, status_line_l_format, status_line_r_format)

        if title:
            if self.format_title_line_r:
                title_line_r, title_line_r_format = formatter.generate_status_line(
                    self.my_user_data,
                    self.my_status,
                    self.new_unreads,
                    self.typing,
                    self.active_channel,
                    action,
                    self.running_tasks,
                    self.tab_string,
                    self.tab_string_format,
                    self.format_title_line_r,
                    self.format_rich,
                    slowmode=self.slowmode_times.get(self.active_channel["channel_id"]),
                    limit_typing=self.limit_typing,
                    fun=self.fun,
                )
            else:
                title_line_r = None
                title_line_r_format = []
            if self.format_title_line_l:
                title_line_l, title_line_l_format = formatter.generate_status_line(
                    self.my_user_data,
                    self.my_status,
                    self.new_unreads,
                    self.typing,
                    self.active_channel,
                    action,
                    self.running_tasks,
                    self.tab_string,
                    self.tab_string_format,
                    self.format_title_line_l,
                    self.format_rich,
                    slowmode=self.slowmode_times.get(self.active_channel["channel_id"]),
                    limit_typing=self.limit_typing,
                    fun=self.fun,
                )
                self.tui.update_title_line(title_line_l, title_line_r, title_line_l_format, title_line_r_format)

        if tree:
            if self.format_title_tree:
                title_tree, _ = formatter.generate_status_line(
                    self.my_user_data,
                    self.my_status,
                    self.new_unreads,
                    self.typing,
                    self.active_channel,
                    action,
                    self.running_tasks,
                    self.tab_string,
                    self.tab_string_format,
                    self.format_title_tree,
                    self.format_rich,
                    slowmode=self.slowmode_times.get(self.active_channel["channel_id"]),
                    limit_typing=self.limit_typing,
                    fun=self.fun,
                )
            else:
                title_tree = None
            self.tui.update_title_tree(title_tree)


    def update_prompt(self):
        """Generate prompt for input line"""
        self.prompt = formatter.generate_prompt(
            self.my_user_data,
            self.active_channel,
            self.config["format_prompt"],
            limit_prompt=self.config["limit_prompt"],
        )


    def custom_prompt(self, text):
        """Generate prompt for input line with custom text"""
        return formatter.generate_custom_prompt(
            text,
            self.config["format_prompt"],
            limit_prompt=self.config["limit_prompt"],
        )


    def update_extra_line(self, custom_text=None, update_only=False, timed=True, permanent=False, force=False):
        """Generate extra line and update it in TUI"""
        update_only |= self.extra_window_open
        if permanent:
            if update_only:
                self.permanent_extra_line = custom_text
                return
            if custom_text:
                self.permanent_extra_line = custom_text
                self.extra_line = custom_text
                self.tui.draw_extra_line(self.extra_line)
            else:
                self.permanent_extra_line = None
                self.extra_line = None
                self.tui.remove_extra_line()
            return
        if update_only:
            self.extra_line = custom_text
        elif custom_text:
            if custom_text == self.extra_line:
                if self.permanent_extra_line:
                    self.extra_line = self.permanent_extra_line
                    self.tui.draw_extra_line(self.extra_line)
                else:
                    self.extra_line = None
                    self.tui.remove_extra_line()
            else:
                self.extra_line = custom_text
                self.tui.draw_extra_line(self.extra_line)
            if timed:
                self.timed_extra_line.set()
        else:
            attachments = self.ready_attachments.get(self.active_channel["channel_id"])
            if attachments:
                self.extra_line = formatter.generate_extra_line(
                    attachments,
                    self.selected_attachment,
                    self.tui.get_dimensions()[2][1],
                )
                self.tui.draw_extra_line(self.extra_line)
            elif self.permanent_extra_line and not force:
                self.extra_line = self.permanent_extra_line
                self.tui.draw_extra_line(self.extra_line)
            else:
                self.extra_line = None
                self.tui.remove_extra_line()


    def update_tree(self, collapsed=None):
        """Generate channel tree"""
        if collapsed is None:
            collapsed = self.state["collapsed"]
        self.tree, self.tree_format, self.tree_metadata = formatter.generate_tree(
            self.dms,
            self.guilds,
            self.threads,
            self.read_state,
            self.guild_folders,
            self.activities,
            collapsed,
            self.uncollapsed_threads,
            self.active_channel["channel_id"],
            self.config,
            folder_names=self.state.get("folder_names", []),
            safe_emoji=self.emoji_as_text,
            max_w=self.tui.get_dimensions()[1][1],
        )
        # debug_guilds_tree
        # debug.save_json(self.tree, "tree.json", False)
        # debug.save_json(self.tree_format, "tree_format.json", False)
        # debug.save_json(self.tree_metadata, "tree_metadata.json", False)
        self.tui.update_tree(self.tree, self.tree_format)

        # check for unreads/mentions for tray icon
        if uses_pgcurses:
            if not self.tui.is_window_open and not bool(self.tui.get_chat_selected()[1]):
                # tree update for current channel is triggered from process_msg_events_other_channels
                self.tui.set_chat_index(1)
                self.update_chat(scroll=False)
            tray_state = 0   # standard
            if self.new_unreads:
                tray_state = 1   # unreads
            for num, code in enumerate(self.tree_format):
                if 100 <= code < 200:
                    second_digit = int((code % 100) // 10)
                    if second_digit in (2, 5):
                        tray_state = 2   # mention
                        break
                    elif second_digit == 3:
                        tray_state = 1   # unread
            self.tui.set_tray_icon(tray_state)


    def lines_to_msg(self, line_index, space=False):
        """Convert line index from formatted chat to message index"""
        line_map = self.chat_map[line_index]
        if line_map and line_map[0] is not None:   # has timestamp range
            return line_map[0]
        if space:
            i = 0
            while i < 5:
                line_map = self.chat_map[line_index - i]
                if line_map and line_map[0]:
                    return line_map[0]
                i += 1
            return 0
        return None


    def lines_to_msg_with_remainder(self, line_index, space=False):
        """Convert line index from formatted chat to message index and remainder"""
        i = 0
        if line_index >= len(self.chat_map):
            line_index = len(self.chat_map) - 1
        while i < 5:
            line_map = self.chat_map[line_index - i]
            if line_map and line_map[0] is not None:
                if line_map[4]:   # when it reaches message base line
                    return line_map[0], 0 - (i * space)
                remainder = 0
                while remainder < len(self.chat_map) - (line_index + remainder + 1):
                    remainder_line_map = self.chat_map[line_index + remainder + 1]
                    if not remainder_line_map or (remainder_line_map[0] is None or remainder_line_map[0] != line_map[0] or remainder_line_map[2]):
                        break
                    remainder += 1
                if line_map[2]:   # when selected reply line
                    i += 1
                return line_map[0], remainder - (i * space)
            i += 1
        return 0, 0


    def msg_to_lines(self, msg_index, smart=False, full=False):
        """Convert message index to line index from formatted chat"""
        in_msg_start_index = 0
        if smart:
            for line_map in reversed(self.chat_map):
                if line_map and line_map[0] == msg_index and line_map[4]:
                    if line_map[1]:
                        break
                    in_msg_start_index += 1
            else:
                in_msg_start_index = 0
        for line_index, line_map in enumerate(self.chat_map):
            if line_map and line_map[0] == msg_index and line_map[4]:      # message root has timestamp range
                if full:
                    next_line_map = self.chat_map[line_index + 1]
                    if next_line_map and next_line_map[0] == line_map[0] and next_line_map[2]:
                        return line_index - in_msg_start_index - 1
                return line_index - in_msg_start_index
        return 0


    def set_mix_seen(self, target_id):
        """
        Set channel/category/guild as seen if it is not already seen.
        Force will set even if its not marked as unseen, used for active channel.
        """
        # check chanels
        channel = self.read_state.get(target_id)
        if channel and channel["last_message_id"] and int(channel["last_acked_message_id"]) < int(channel["last_message_id"]):
            self.set_channel_seen(target_id)
            if target_id in self.read_state and "last_acked_unreads_line" in self.read_state[target_id]:
                self.read_state[target_id]["last_acked_unreads_line"] = None
        else:
            channels = []

            # check guilds
            for guild in self.guilds:
                if guild["guild_id"] == target_id:
                    for channel in guild["channels"]:
                        channel_r = self.read_state.get(channel["id"])
                        if channel_r and channel_r["last_message_id"] and int(channel_r["last_acked_message_id"]) < int(channel_r["last_message_id"]):
                            channels.append({
                                "channel_id": channel["id"],
                                "message_id": channel_r["last_message_id"],
                            })
                    break

            # check categories
            _, _, guild_id, _, parent_id = self.find_parents_from_id(target_id)
            guild = None
            if guild_id and not parent_id:   # category has no parent_id
                for guild in self.guilds:
                    if guild["guild_id"] == guild_id:
                        break
            if guild:
                for channel in guild["channels"]:
                    if channel["parent_id"] == target_id:   # category
                        channel_r = self.read_state.get(channel["id"])
                        if channel_r and channel_r["last_message_id"] and int(channel_r["last_acked_message_id"]) < int(channel_r["last_message_id"]):
                            channels.append({
                                "channel_id": channel["id"],
                                "message_id": channel_r["last_message_id"],
                            })

            if channels:
                success = self.discord.send_ack_bulk(channels)
                if success is None:
                    self.gateway.set_offline()
                    self.update_extra_line("Network error.")
                    return
                for channel in channels:
                    channel_id = channel["channel_id"]
                    self.set_channel_seen(channel_id, ack=False, update_tree=False)
                    if channel_id in self.read_state and "last_acked_unreads_line" in self.read_state[channel_id]:
                        self.read_state[channel_id]["last_acked_unreads_line"] = None
                self.update_tree()


    def set_channel_seen(self, channel_id, message_id=None, ack=True, force=False, update_tree=True, force_remove_notify=False):
        """Set one channel as seen"""
        channel = self.read_state.get(channel_id)
        if channel:
            remove_notification = force_remove_notify
            this_channel = channel_id == self.active_channel["channel_id"]
            last_message_id = channel["last_message_id"]
            unseen = not last_message_id or int(channel["last_acked_message_id"]) < int(last_message_id)
            if unseen and (not this_channel or (not bool(self.tui.get_chat_selected()[1]) and this_channel) or force):
                if not message_id or message_id < channel["last_message_id"]:
                    message_id = channel["last_message_id"]
                if message_id:
                    self.read_state[channel_id]["last_message_id"] = message_id
                    if ack:
                        self.send_ack(channel_id, message_id)
                    if not this_channel:
                        remove_notification = True
                    self.read_state[channel_id]["last_acked_message_id"] = message_id
                    self.read_state[channel_id]["mentions"] = []
                    if update_tree:
                        self.update_tree()

            if self.enable_notifications and remove_notification:
                for num, notification in enumerate(self.notifications):
                    if notification["channel_id"] == channel_id:
                        notification_id = self.notifications.pop(num)["id"]
                        peripherals.notify_remove(notification_id)
                        break


    def set_channel_unseen(self, channel_id, message_id, ping, skip_unread, last_acked_message_id=1, set_line=True, set_line_now=False):
        """Set one channel as unseen"""
        update_tree = False
        channel = self.read_state.get(channel_id)
        if channel:
            last_message_id = channel["last_message_id"]
            if last_message_id and int(channel["last_acked_message_id"]) >= int(last_message_id):
                update_tree = True   # only update tree if previous state is "read"
            self.read_state[channel_id]["last_message_id"] = message_id
            if last_acked_message_id != 1 or not channel["last_acked_message_id"]:
                self.read_state[channel_id]["last_acked_message_id"] = last_acked_message_id
            if ping:
                self.read_state[channel_id]["mentions"].append(message_id)
            if channel.get("last_acked_unreads_line") is None and set_line:
                # last_acked_unreads_line is used to persist unreads line even after channel is acked
                if set_line_now:
                    self.read_state[channel_id]["last_acked_unreads_line"] = message_id
                else:
                    self.read_state[channel_id]["last_acked_unreads_line"] = self.read_state[channel_id]["last_acked_message_id"]
        else:
            self.read_state[channel_id] = {
                "last_acked_message_id": last_acked_message_id,
                "last_message_id": message_id,
                "mentions": [message_id] if ping else [],
                "last_acked_unreads_line": last_acked_message_id,
            }
            update_tree = True

        if channel_id == self.active_channel["channel_id"] and not bool(self.tui.get_chat_selected()[1]):
            self.set_channel_seen(self.active_channel["channel_id"], message_id)
        if (update_tree or ping) and not skip_unread:
            self.update_tree()


    def set_channel_me_seen(self, channel_id, message_id):
        """Set one channel as seen because this client sent message in it"""
        if channel_id in self.read_state:
            self.read_state[channel_id]["last_acked_message_id"] = message_id
            self.read_state[channel_id]["last_message_id"] = message_id
            self.read_state[channel_id]["last_acked_unreads_line"] = None
        else:
            self.read_state[channel_id] = {
                "last_acked_message_id": message_id,
                "last_message_id": message_id,
                "mentions": [],
            }
        self.update_tree()



    def send_ack(self, channel_id=None, message_id=None, manual=False):
        """Send ack, if throttled - add to queue, if queue is larger than 1, then send bulk ack"""
        # add to queue
        if channel_id:
            for ack in self.pending_acks:
                if ack == channel_id:
                    break
            else:
                self.pending_acks.append((channel_id, message_id))

        # try to send
        if self.pending_acks and time.time() - self.sent_ack_time > self.ack_throttling:
            success = self.discord.send_ack(*self.pending_acks.pop(0), manual=manual)
            if success is None:
                self.gateway.set_offline()
                self.update_extra_line("Network error.")
            self.sent_ack_time = time.time()


    def compute_permissions(self):
        """Compute permissions for all guilds. Run after roles have been obtained"""
        for guild in self.guilds:
            guild_id = guild["guild_id"]
            # get my roles
            my_roles = None
            for roles in self.my_roles:
                if roles["guild_id"] == guild_id:
                    my_roles = roles["roles"]
                    break
            if my_roles is None:
                continue
            # get guild roles
            this_guild_roles = []
            for roles in self.all_roles:
                if roles["guild_id"] == guild_id:
                    this_guild_roles = roles["roles"]
                    break
            # get permissions
            self.guilds = perms.compute_permissions(
                self.guilds,
                this_guild_roles,
                guild_id,
                my_roles,
                self.my_id,
            )


    def clean_permissions(self, guild_id):
        """Remove all computed permissions for specified guild"""
        for guild in self.guilds:
            if guild["guild_id"] == guild_id:
                break
        for num, channel in enumerate(guild["channels"]):
            guild["channels"][num].pop("perms_computed", None)
            guild["channels"][num].pop("allow_manage", None)
            guild["channels"][num].pop("permitted", None)
            guild["channels"][num].pop("allow_write", None)
            guild["channels"][num].pop("allow_attach", None)


    def hide_channel(self, channel_id, guild_id):
        """Locally hide this channel, for this session"""
        for guild in self.guilds:
            if guild["guild_id"] == guild_id:
                for channel in guild["channels"]:
                    if channel["id"] == channel_id:
                        channel["hidden"] = True
                        break
                break


    def load_threads(self, event):
        """
        Add new threads to sorted list of threads by guild and channel.
        new_threads is list of threads that belong to same guild.
        Threads are sorted by creation date.
        """
        to_sort = False
        guild_id = event["guild_id"]
        for num, guild in enumerate(self.threads):
            if guild["guild_id"] == guild_id:
                break
        else:
            num = len(self.threads)
            self.threads.append({
                "guild_id": guild_id,
                "channels": [],
            })
        for new_thread in event["threads"]:
            parent_id = new_thread["parent_id"]
            for channel in self.threads[num]["channels"]:
                if channel["channel_id"] == parent_id:
                    for thread in channel["threads"]:
                        if thread["id"] == new_thread["id"]:
                            muted = thread["muted"]   # dont overwrite muted and joined
                            joined = thread.get("joined", False)
                            thread.update(new_thread)
                            thread["muted"] = muted
                            thread["joined"] = joined
                            # no need to sort if its only update
                            break
                    else:
                        to_sort = True
                        channel["threads"].append(new_thread)
                    break
            else:
                new_thread.pop("parent_id")
                self.threads[num]["channels"].append({
                    "channel_id": parent_id,
                    "threads": [new_thread],
                })
        if to_sort:
            for guild in self.threads:
                if guild["guild_id"] == guild_id or guild_id is None:
                    for channel in guild["channels"]:
                        channel["threads"] = sorted(channel["threads"], key=lambda x: x["id"], reverse=True)
                break
        self.update_tree()
        if self.forum:
            self.update_forum(self.active_channel["guild_id"], self.active_channel["channel_id"])
            self.tui.update_chat(self.chat, self.chat_format)


    def remove_thread(self, event):
        """Delete threads for specified guild and channel"""
        guild_id = event["guild_id"]
        for num, guild in enumerate(self.threads):
            if guild["guild_id"] == guild_id:
                break
        else:
            return
        for new_thread in event["threads"]:
            parent_id = new_thread["parent_id"]
            for channel in self.threads[num]["channels"]:
                if channel["channel_id"] == parent_id:
                    for tnum, thread in enumerate(channel["threads"]):
                        if thread["id"] == new_thread["id"]:
                            channel["threads"].pop(tnum)
                    break
        self.update_tree()
        if self.forum:
            self.update_forum(self.active_channel["guild_id"], self.active_channel["channel_id"])
            self.tui.update_chat(self.chat, self.chat_format)


    def thread_toggle_join(self, guild_id, channel_id, thread_id, join=None):
        """Toggle, or set a custom value for 'joined' state of a thread and return new state"""
        for guild in self.threads:
            if guild["guild_id"] == guild_id:
                for channel in guild["channels"]:
                    if channel["channel_id"] == channel_id:
                        for thread in channel["threads"]:
                            if thread["id"] == thread_id:
                                if join is None:
                                    if thread["joined"]:
                                        thread["joined"] = False
                                        discord.leave_thread(thread_id)
                                    else:
                                        thread["joined"] = True
                                        discord.join_thread(thread_id)
                                if join != thread["joined"]:
                                    thread["joined"] = join
                                    discord.join_thread(thread_id)
                        break
                break


    def toggle_mute(self, channel_id, guild_id=None, is_dm=False):
        """Toggle mute setting of channel, category, guild or DM"""
        if is_dm:   # dm
            for dm in self.dms:
                if dm["id"] == channel_id:
                    if dm.get("muted"):
                        dm["muted"] = False
                        self.dms_vis_id.remove(channel_id)
                    else:
                        dm["muted"] = True
                        self.dms_vis_id.append(channel_id)
                    self.update_tree()
                    return dm.get("muted")
        elif guild_id:   # channel/category
            for guild in self.guilds:
                if guild["guild_id"] == guild_id:
                    for channel in guild["channels"]:
                        if channel["id"] == channel_id:
                            channel["muted"] = not channel.get("muted")
                            self.update_tree()
                            return channel["muted"]
                    break
        else:   # guild
            for guild in self.guilds:
                if guild["guild_id"] == channel_id:
                    guild["muted"] = not guild.get("muted")
                    self.update_tree()
                    return guild["muted"]


    def check_tree_format(self):
        """Check tree format for collapsed guilds, categories, channels (with threads) and forums and save it"""
        new_tree_format = self.tui.get_tree_format()
        if new_tree_format:
            self.tree_format = new_tree_format
            # get all collapsed guilds/categories/channels/forums and save them
            collapsed = []
            self.uncollapsed_threads = []
            for num, code in enumerate(self.tree_format):
                if code < 300 and (code % 10) == 0:   # guild/category
                    collapsed.append(self.tree_metadata[num]["id"])
                elif 499 < code < 700 and (code % 10) == 1:   # channel (with threads) and forum
                    self.uncollapsed_threads.append(self.tree_metadata[num]["id"])
            if self.state["collapsed"] != collapsed:
                self.state["collapsed"] = collapsed
                peripherals.save_json(self.state, f"state_{self.profiles["selected"]}.json")


    def process_msg_events_active_channel(self, new_message, selected_line, latest_chat=True):
        """Process message events for currently active channel"""
        data = new_message["d"]
        op = new_message["op"]
        my_message = data.get("user_id") == self.my_id
        channel_id = self.active_channel["channel_id"]
        if op == "MESSAGE_CREATE" and latest_chat:
            change_amount = 1
            if my_message:
                nonce = data.pop("nonce", None)
            if self.emoji_as_text:
                data = formatter.demojize_message(data)
            self.messages.insert(0, data)
            self.last_message_id = data["id"]
            # remove pending message
            if my_message and self.show_pending_messages:
                for num, message in enumerate(self.messages):
                    if "pending" in message and message["id"] == nonce:
                        self.messages.pop(num)
                        change_amount -=1
                        break
            # limit chat size
            if len(self.messages) > self.limit_chat_buffer:
                self.messages.pop(-1)
            # set unreads sign in status line
            update_status_line = False
            if bool(self.tui.get_chat_selected()[1]):
                if not self.new_unreads:
                    update_status_line = True
                self.new_unreads = True
            # remove user from typing
            for num, user in enumerate(self.typing):
                if user["user_id"] == data["user_id"]:
                    self.typing.pop(num)
                    update_status_line = True
                    break
            if my_message:
                # handle slowmode
                if self.slowmodes and self.slowmodes.get(channel_id):
                    if not self.slowmode_times.get(channel_id):
                        self.slowmode_times[channel_id] = self.slowmodes.get(channel_id, 0)
                    if not self.slowmode_thread or not self.slowmode_thread.is_alive():
                        self.slowmode_thread = threading.Thread(target=self.wait_slowmode, daemon=True, args=())
                        self.slowmode_thread.start()
                # remove unreads line
                if self.read_state.get(channel_id):
                    self.read_state[channel_id]["last_acked_unreads_line"] = None
            self.update_chat(change_amount=change_amount, scroll=False)
            if update_status_line:
                self.update_status_line()
        else:
            for num, loaded_message in enumerate(self.messages):
                if data["id"] == loaded_message["id"]:
                    if op == "MESSAGE_UPDATE":
                        if self.emoji_as_text:
                            data = formatter.demojize_message(data)
                        for element in MESSAGE_UPDATE_ELEMENTS:
                            loaded_message[element] = data[element]
                            loaded_message["spoiled"] = []
                        # check if this message has emoji and clear it before redraw
                        msg_line_index = self.msg_to_lines(num, smart=True)
                        if msg_line_index in self.tui.wide_map:
                            self.tui.wide_map = [msg_line_index]
                            self.tui.clear_chat_wide()
                        self.update_chat(scroll=False)
                    elif op == "MESSAGE_DELETE":
                        if self.keep_deleted:
                            self.messages[num]["deleted"] = True
                        else:
                            self.messages.pop(num)
                        self.last_message_id = self.get_chat_last_message_id()
                        if num < selected_line and not self.keep_deleted:
                            self.update_chat(change_amount=-1, scroll=False)
                        else:
                            self.update_chat(scroll=False)
                    elif op == "MESSAGE_REACTION_ADD":
                        for num2, reaction in enumerate(loaded_message["reactions"]):
                            if data["emoji_id"] == reaction["emoji_id"] and data["emoji"] == reaction["emoji"]:
                                loaded_message["reactions"][num2]["count"] += 1
                                if my_message:
                                    loaded_message["reactions"][num2]["me"] = True
                                break
                        else:
                            loaded_message["reactions"].append({
                                "emoji": data["emoji"],
                                "emoji_id": data["emoji_id"],
                                "count": 1,
                                "me": my_message,
                            })
                        self.update_chat(scroll=False)
                    elif op == "MESSAGE_REACTION_REMOVE":
                        for num2, reaction in enumerate(loaded_message["reactions"]):
                            if data["emoji_id"] == reaction["emoji_id"] and data["emoji"] == reaction["emoji"]:
                                if reaction["count"] <= 1:
                                    loaded_message["reactions"].pop(num2)
                                else:
                                    loaded_message["reactions"][num2]["count"] -= 1
                                    if my_message:
                                        loaded_message["reactions"][num2]["me"] = False
                                break
                        self.update_chat(scroll=False)
                    elif op in ("MESSAGE_POLL_VOTE_ADD", "MESSAGE_POLL_VOTE_REMOVE") and "poll" in loaded_message:
                        if "poll" in loaded_message:
                            add = op == "MESSAGE_POLL_VOTE_ADD"
                            for num2, option in enumerate(loaded_message["poll"]["options"]):
                                if option["id"] == data["answer_id"]:
                                    loaded_message["poll"]["options"][num2]["count"] += (1 if add else -1)
                                    if my_message:
                                        loaded_message["poll"]["options"][num2]["me_voted"] = add
                                    break
                        self.update_chat(scroll=False)


    def process_msg_events_cached_channel(self, new_message, ch_num):
        """Process message events for currently active channel"""
        data = new_message["d"]
        op = new_message["op"]
        if op == "MESSAGE_CREATE":
            if data["channel_id"] == self.active_channel["channel_id"]:
                self.last_message_id = data["id"]
                self.new_unreads = True
                self.update_status_line()
            if self.emoji_as_text:
                data = formatter.demojize_message(data)
            if data.get("user_id") == self.my_id and self.show_pending_messages:
                for num, message in enumerate(self.messages):
                    if "pending" in message and message["id"] == new_message.pop("nonce", None):
                        self.messages.pop(num)
                        break
            self.channel_cache[ch_num][1].insert(0, data)
            if len(self.channel_cache[ch_num][1]) > self.msg_num:
                self.channel_cache[ch_num][1].pop(-1)
        else:
            my_message = data.get("user_id") == self.my_id
            for num, loaded_message in enumerate(self.channel_cache[ch_num][1]):
                if data["id"] == loaded_message["id"]:
                    if op == "MESSAGE_UPDATE":
                        if self.emoji_as_text:
                            data = formatter.demojize_message(data)
                        for element in MESSAGE_UPDATE_ELEMENTS:
                            loaded_message[element] = data[element]
                        loaded_message["edited"] = True
                    elif op == "MESSAGE_DELETE":
                        if self.keep_deleted:
                            self.channel_cache[ch_num][1][num]["deleted"] = True
                        else:
                            self.channel_cache[ch_num][1].pop(num)
                    elif op == "MESSAGE_REACTION_ADD":
                        for num2, reaction in enumerate(loaded_message["reactions"]):
                            if data["emoji_id"] == reaction["emoji_id"] and data["emoji"] == reaction["emoji"]:
                                loaded_message["reactions"][num2]["count"] += 1
                                if my_message:
                                    loaded_message["reactions"][num2]["me"] = True
                                break
                        else:
                            loaded_message["reactions"].append({
                                "emoji": data["emoji"],
                                "emoji_id": data["emoji_id"],
                                "count": 1,
                                "me": my_message,
                            })
                    elif op == "MESSAGE_REACTION_REMOVE":
                        for num2, reaction in enumerate(loaded_message["reactions"]):
                            if data["emoji_id"] == reaction["emoji_id"] and data["emoji"] == reaction["emoji"]:
                                if reaction["count"] <= 1:
                                    loaded_message["reactions"].pop(num2)
                                else:
                                    loaded_message["reactions"][num2]["count"] -= 1
                                    if my_message:
                                        loaded_message["reactions"][num2]["me"] = False
                                break
                    elif op in ("MESSAGE_POLL_VOTE_ADD", "MESSAGE_POLL_VOTE_REMOVE") and "poll" in loaded_message:
                        if "poll" in loaded_message:
                            add = op == "MESSAGE_POLL_VOTE_ADD"
                            for num2, option in enumerate(loaded_message["poll"]["options"]):
                                if option["id"] == data["answer_id"]:
                                    loaded_message["poll"]["options"][num2]["count"] + (1 if add else -1)
                                    if my_message:
                                        loaded_message["poll"]["options"][num2]["me_voted"] = add
                                    break


    def process_msg_events_other_channels(self, new_message):
        """Process message events that should ping and send notification"""
        data = new_message["d"]
        op = new_message["op"]
        new_message_channel_id = data["channel_id"]
        this_channel = self.active_channel["channel_id"] == new_message_channel_id
        if op == "MESSAGE_CREATE":
            if data["user_id"] == self.my_id:
                self.set_channel_me_seen(new_message_channel_id, data["id"])
            elif data["user_id"] not in self.blocked:
                # skip muted channels
                muted = False
                message_notifications = 2   # 0 - all messages, 1 - only mentions, 2 - nothing
                for guild in self.guilds:
                    if guild["guild_id"] == data["guild_id"]:
                        muted = guild.get("muted")
                        suppress_everyone = guild.get("suppress_everyone")
                        suppress_roles = guild.get("suppress_roles")
                        for channel in guild["channels"]:
                            if new_message_channel_id == channel["id"]:
                                if channel.get("muted") or channel.get("hidden"):
                                    muted = True
                                else:
                                    message_notifications = channel.get("message_notifications", 2)
                                    if message_notifications >= 10:
                                        message_notifications -= 10
                                break
                        break
                else:
                    suppress_everyone = False
                    suppress_roles = False
                for dm in self.dms:
                    if dm["id"] == new_message_channel_id:
                        is_dm = True
                        muted = dm.get("muted")
                        message_notifications = 0
                        break
                else:
                    is_dm = False
                if not muted:
                    ping = False

                    # check if this message should ping
                    mentions = data["mentions"]
                    # select my roles from same guild as message
                    my_roles = []
                    for guild in self.my_roles:
                        if guild["guild_id"] == data["guild_id"]:
                            my_roles = guild["roles"]
                    if (
                        (data["mention_everyone"] and not suppress_everyone) or
                        (bool([i for i in my_roles if i in data["mention_roles"]]) and not suppress_roles) or
                        (self.my_id in [x["id"] for x in mentions]) or
                        (is_dm and new_message_channel_id in self.dms_vis_id)
                    ):
                        if not this_channel or self.new_unreads:   # new_unreads already set in process events for active channel
                            ping = True
                        if message_notifications != 2:
                            self.send_desktop_notification(new_message)
                    elif message_notifications == 0:
                        self.send_desktop_notification(new_message)

                    # set unseen
                    if this_channel and self.new_unreads:
                        last_acked_message_id = self.messages[1]["id"]
                    else:
                        last_acked_message_id = 1
                    self.set_channel_unseen(
                        new_message_channel_id,
                        data["id"],
                        ping,
                        this_channel and not self.new_unreads,
                        last_acked_message_id,
                        set_line=not(self.new_unreads and this_channel),
                        set_line_now=this_channel and not self.new_unreads,
                    )
                    if this_channel and self.new_unreads:
                        self.update_chat(scroll=False)


    def process_msg_events_ghost_ping(self, new_message):
        """Check message events for deleted message and remove ghost pings/notifications"""
        if new_message["op"] == "MESSAGE_DELETE" and not self.keep_deleted:
            channel_id = new_message["d"]["channel_id"]
            if channel_id in self.read_state and new_message["d"]["id"] in self.read_state.get(channel_id, {}).get("mentions", []):
                # if channel is from ready event - message is unknown
                self.read_state[channel_id]["mentions"].remove(new_message["d"]["id"])
                self.update_tree()
                if self.enable_notifications:
                    for num_1, notification in enumerate(self.notifications):
                        if notification["channel_id"] == channel_id:
                            notification_id = self.notifications.pop(num_1)["id"]
                            peripherals.notify_remove(notification_id)
                            break


    def process_new_user_data(self, new_user_data):
        """Process new user data"""
        # all these changes happen rarely so there is no need to buffer them
        guild_roles_changed = new_user_data[2]
        changed_guild = new_user_data[1]
        new_user_data = new_user_data[0]

        if new_user_data:   # gateway will send this only when this user data changes
            if not new_user_data.get("nick"):
                new_user_data["nick"] = self.my_user_data["nick"]
            if "name" in new_user_data:   # its my user_update
                self.my_user_data = new_user_data
                self.premium = self.gateway.get_premium()
                if self.rpc.run:
                    self.rpc.generate_dispatch(new_user_data)
            else:   # its guild_member_update
                self.my_user_data["nick"] = new_user_data["nick"]
            if changed_guild:   # its my roles update from guild_member_update
                self.my_roles = self.gateway.get_my_roles()
                self.clean_permissions(changed_guild)
                self.compute_permissions()
                for roles in self.my_roles:
                    if roles["guild_id"] == changed_guild:
                        self.current_my_roles = roles["roles"]
                        break
                for guild in self.member_roles:
                    if guild["guild_id"] == self.active_channel["guild_id"]:
                        for member in guild["members"]:
                            if member["user_id"] == self.my_id:
                                member["roles"] = self.current_my_roles
                                member.pop("primary_role_color", None)
                                break
                        break
                self.select_current_member_roles()
                self.update_tree()
                self.update_chat()
            self.update_status_line()
            self.update_prompt()

        if guild_roles_changed:
            guild_id = guild_roles_changed[0]
            role_id = guild_roles_changed[1]
            for num, roles in enumerate(self.my_roles):
                if roles["guild_id"] == guild_id:
                    break
            else:
                num = None
            if num is None:
                return

            self.all_roles = color.convert_role_colors(self.all_roles, guild_id, role_id, default=self.config["color_default"][0])

            if guild_id != self.active_channel["guild_id"]:
                return

            # 255_curses_bug - update only portion of roles color ids
            self.all_roles = self.tui.init_role_colors(
                self.all_roles,
                self.default_msg_color[1],
                self.default_msg_alt_color[1],
                guild_id=guild_id,
            )
            for roles in self.all_roles:
                if roles["guild_id"] == guild_id:
                    self.current_roles = roles["roles"]
                    break
            for guild in self.member_roles:
                if guild["guild_id"] == guild_id:
                    for member in guild["members"]:
                        member.pop("primary_role_color", None)
                    break
            self.select_current_member_roles()

            # update perms and redraw
            if role_id in roles["roles"]:
                self.clean_permissions(guild_id)
                self.compute_permissions()
                self.update_tree()
            self.update_chat(scroll=False)


    def process_call_gateway_events(self, event):
        """Process call related event from gateway"""
        if not self.enable_calls:
            return
        for dm in self.dms:
            if dm["id"] == event["channel_id"]:
                break
        else:
            return
        if dm["is_spam"] or dm["muted"]:
            return

        if self.in_call and event["channel_id"] != self.in_call["channel_id"] and event["op"] != "CALL_DELETE":
            return

        event = self.execute_extensions_methods("on_call_gateway_event", event, cache=True)[0]

        if event["op"] == "CALL_CREATE" and not (self.in_call or self.joining_call):
            if dm["id"] not in self.incoming_calls:
                self.incoming_calls.append(dm["id"])
                self.most_recent_incoming_call = dm["id"]
                if self.recording:   # stop recording voice message
                    self.recording = False
                    _ = recorder.stop()
                self.permanent_extra_line = formatter.generate_extra_line_ring(
                    dm["name"],
                    self.tui.get_dimensions()[2][1],
                )
                self.update_extra_line(custom_text=self.permanent_extra_line, permanent=True)
                if event["ringing"]:
                    custom_ringtone = self.config["custom_ringtone_incoming"]
                    linux_ringtone = peripherals.find_linux_sound(self.config["linux_ringtone_incoming"])
                    if custom_ringtone and os.path.exists(custom_ringtone):
                        self.start_ringing(custom_ringtone)
                    elif linux_ringtone and os.path.exists(linux_ringtone):
                        self.start_ringing(linux_ringtone)
                    else:
                        logger.warning(f"Specified ringtone paths are invalid: {custom_ringtone}, {linux_ringtone}")

        elif event["op"] == "CALL_UPDATE" and not (self.in_call or self.joining_call):
            if event["ringing"]:
                custom_ringtone = self.config["custom_ringtone_incoming"]
                linux_ringtone = peripherals.find_linux_sound(self.config["linux_ringtone_incoming"])
                if custom_ringtone and os.path.exists(custom_ringtone):
                    self.start_ringing(custom_ringtone)
                elif linux_ringtone and os.path.exists(linux_ringtone):
                    self.start_ringing(linux_ringtone)
                else:
                    logger.warning(f"Specified ringtone paths are invalid: {custom_ringtone}, {linux_ringtone}")

        elif event["op"] == "CALL_DELETE" and event["channel_id"] in self.incoming_calls:
            self.incoming_calls.remove(event["channel_id"])
            if self.most_recent_incoming_call == event["channel_id"]:
                self.most_recent_incoming_call = None
            if not (self.in_call or self.joining_call):
                self.update_extra_line(permanent=True)
                self.stop_ringing()

        elif event["op"] == "STATE_UPDATE" and event["channel_id"] in self.incoming_calls:
            for num, participant in enumerate(self.call_participants):
                if participant["user_id"] == event["user_id"]:
                    if participant["name"]:
                        self.call_participants[num]["muted"] = event["muted"]
                    else:
                        # if user is added by USER_JOIN, show popup
                        self.update_extra_line(f"{event["name"]} joined the call")
                        if event["name"]:
                            self.call_participants[num]["name"] = event["name"]
                            self.update_call_extra_line()
                        self.call_participants[num]["muted"] = event["muted"]
                    if self.voice_call_list_open:
                        self.view_voice_call_list()
                    break
            else:
                # USER_JOIN will show popup
                self.call_participants.append({
                    "user_id": event["user_id"],
                    "name": event["name"],
                    "muted": event["muted"],
                    "speaking": False,
                })
                self.update_call_extra_line()


    def process_call_voice_gateway_events(self, event):
        """Process events from voice gateway"""
        event = self.execute_extensions_methods("on_call_voice_gateway_event", event, cache=True)[0]

        if event["op"] == "USER_SPEAK":
            for num, participant in enumerate(self.call_participants):
                if participant["user_id"] == event["user_id"] and participant["name"]:
                    self.call_participants[num]["speaking"] = event["speaking"]

        elif event["op"] == "USER_JOIN":
            self.stop_ringing()
            if self.in_call and not self.in_call["guild_id"]:
                for dm in self.dms:
                    if dm["id"] == self.in_call["channel_id"]:
                        for recipient in dm["recipients"]:
                            if recipient["id"] == event["user_id"]:
                                name = recipient["global_name"] or recipient["username"]
                                self.update_extra_line(f"{name} joined the call")
                                # add call participant
                                for num, participant in enumerate(self.call_participants):
                                    if participant["user_id"] == event["user_id"]:
                                        if not participant["name"]:
                                            self.call_participants[num]["name"] = name
                                            self.update_call_extra_line()
                                        break
                                else:
                                    self.call_participants.append({
                                        "user_id": recipient["id"],
                                        "name": name,
                                        "muted": False,
                                        "speaking": False,
                                    })
                                    self.update_call_extra_line()
                                if self.voice_call_list_open:
                                    self.view_voice_call_list()
                                break
                        break
            elif self.in_call:
                for participant in self.call_participants:
                    if participant["user_id"] == event["user_id"] and participant["name"]:
                        # if user is already added by STATE_UPDATE, just show popup
                        self.update_extra_line(f"{participant["name"]} joined the call")
                        break
                else:
                    # this adds only user_id, STATE_UPDATE has to fill user data
                    self.call_participants.append({
                        "user_id": recipient["id"],
                        "name": None,
                        "muted": False,
                        "speaking": False,
                    })
                    self.update_call_extra_line()
                if self.voice_call_list_open:
                    self.view_voice_call_list()

        elif event["op"] == "USER_LEAVE":
            if self.in_call:
                for num, participant in enumerate(self.call_participants):
                    if participant["user_id"] == event["user_id"]:
                        self.update_extra_line(f"{participant["name"]} left the call")
                        self.call_participants.pop(num)
                        self.update_call_extra_line()
                        if self.voice_call_list_open:
                            self.view_voice_call_list()
                        break


    def start_ringing(self, path, loop_delay=1, loop_max=60):
        """Start ringing with specified audio file"""
        if support_media:
            if not self.terminal_media:
                from endcord import media
            self.ringer = media.TerminalMedia(self.config, self.keybindings, ui=False)
            self.ringer.play_audio_noui(path, loop=True, loop_delay=loop_delay, loop_max=loop_max)
        else:
            self.ringer = peripherals.Player()
            self.ringer.start(path, loop=True, loop_delay=loop_delay, loop_max=loop_max)


    def stop_ringing(self):
        """Stop ringing"""
        if self.ringer:
            self.ringer.stop_playback()
            del self.ringer
            self.ringer = None


    def start_call(self, incoming=False, guild_id=None, channel_id=None):
        """Start voice call"""
        if not support_media:
            self.update_extra_line("Failed to start call: No media support.")
            return

        self.joining_call = True
        self.call_participants = []
        self.update_extra_line(custom_text="Connecting to voice server.", permanent=True)
        self.gateway.request_voice_gateway(
            guild_id,
            channel_id,
            self.state.get("muted", False),
            video=False,
            preferred_regions=self.discord.get_best_voice_region(),
        )
        for _ in range(100):   # wait for 10s
            voice_gateway_data = self.gateway.get_voice_gateway()
            if voice_gateway_data:
                break
            time.sleep(0.1)
        else:
            self.update_extra_line(permanent=True)
            self.update_extra_line("Failed to start call: gateway timeout.")
            logger.warning("Failed to start call: gateway timeout.")
            self.joining_call = False
            return

        from endcord import voice
        self.voice_gateway = voice.Gateway(
            voice_gateway_data,
            self.my_id,
            self.state.get("muted"),
            self.user_agent,
            proxy=self.config["proxy"],
        )
        self.in_call = {"guild_id": guild_id, "channel_id": channel_id}
        for _ in range(100):   # wait for 10s
            if self.voice_gateway.get_state() == 2:
                break
            time.sleep(0.1)
        else:
            self.update_extra_line(permanent=True)
            self.update_extra_line("Failed to start call: voice gateway timeout.")
            logger.warning("Failed to start call: voice gateway timeout.")
            del self.voice_gateway
            self.voice_gateway = None
            self.joining_call = False
            self.in_call = None
            return

        if not guild_id:
            recipients = []
            for dm in self.dms:
                if dm["id"] == channel_id:
                    for recipient in dm["recipients"]:
                        if recipient["id"] != self.my_id:
                            recipients.append(recipient["id"])
                    break
            if recipients:
                self.discord.send_ring(channel_id, recipients)

        # call started successfully
        if self.most_recent_incoming_call == channel_id:
            self.most_recent_incoming_call = False
        if channel_id in self.incoming_calls:
            self.incoming_calls.remove(channel_id)
        self.update_call_extra_line()

        self.stop_ringing()
        if not incoming:
            custom_ringtone = self.config["custom_ringtone_outgoing"]
            linux_ringtone = peripherals.find_linux_sound(self.config["linux_ringtone_outgoing"])
            if custom_ringtone and os.path.exists(custom_ringtone):
                self.start_ringing(custom_ringtone, loop_delay=1.5, loop_max=30)
            elif linux_ringtone and os.path.exists(linux_ringtone):
                self.start_ringing(linux_ringtone, loop_delay=1.5, loop_max=30)
            else:
                logger.warning(f"Specified ringtone paths are invalid: {custom_ringtone}, {linux_ringtone}")
        self.joining_call = False

        self.execute_extensions_methods("on_start_call")


    def leave_call(self):
        """Leave voice call"""
        if self.in_call:
            call_channel_id = self.in_call["channel_id"]
            if call_channel_id not in self.incoming_calls:
                self.incoming_calls.append(call_channel_id)

        if self.voice_gateway:
            self.voice_gateway.stop_voice_handler()

        if self.voice_call_list_open:
            self.close_extra_window()

        self.gateway.request_voice_disconnect()

        # keep popup (will be removed on CALL_DELETE event)
        if self.in_call and call_channel_id == self.active_channel["channel_id"]:
            for dm in self.dms:
                if dm["id"] == call_channel_id:
                    new_permanent_extra_line = formatter.generate_extra_line_ring(
                        dm["name"],
                        self.tui.get_dimensions()[2][1],
                    )
                    self.update_extra_line(custom_text=new_permanent_extra_line, permanent=True)
                    break
        else:
            self.update_extra_line(permanent=True)
        self.in_call = None

        # wait for host respond then terminate voice gateway
        time.sleep(0.5)
        if self.voice_gateway:
            self.voice_gateway.disconnect()
        self.stop_ringing()
        self.voice_gateway = None

        self.execute_extensions_methods("on_leave_call")


    def update_call_extra_line(self):
        """Update extra line shown when in call, eg on call participants change"""
        self.permanent_extra_line = formatter.generate_extra_line_call(
            self.call_participants,
            self.state.get("muted"),
            self.tui.get_dimensions()[2][1],
        )
        self.update_extra_line(custom_text=self.permanent_extra_line, permanent=True)


    def update_voice_mute_in_call(self):
        """Update this client mute state while in voice call"""
        if self.in_call and self.voice_gateway:
            self.voice_gateway.set_mute(self.state.get("muted", False))
            self.gateway.update_mute_in_call(
                self.in_call["guild_id"],
                self.in_call["channel_id"],
                self.state.get("muted", False),
                video=False,
                preferred_regions=self.discord.get_best_voice_region(),
            )
            self.update_call_extra_line()


    def update_summary(self, new_summary):
        """Add new summary to list, then save it, avoiding often disk writes"""
        summary = {
            "message_id": new_summary["message_id"],
            "topic": new_summary["topic"],
            "description": new_summary["description"],
        }
        for num, guild in enumerate(self.summaries):   # select guild
            if guild["guild_id"] == new_summary["guild_id"]:
                selected_guild = num
                break
        else:
            self.summaries.append({
                "guild_id": new_summary["guild_id"],
                "channels": [],
            })
            selected_guild = -1
        for channel in self.summaries[selected_guild]["channels"]:
            if channel["channel_id"] == new_summary["channel_id"]:
                channel["summaries"].append(summary)
                if len(channel["summaries"]) > LIMIT_SUMMARIES:
                    del channel["summaries"][0]
                break
        else:
            self.summaries[selected_guild]["channels"].append({
                "channel_id": new_summary["channel_id"],
                "summaries": [summary],
            })
        if time.time() - self.last_summary_save > SUMMARY_SAVE_INTERVAL:
            peripherals.save_json(self.summaries, "summaries.json")
            self.last_summary_save = time.time()


    def update_presence_from_proto(self):
        """Update presence from protos locally and redraw status line"""
        custom_status_emoji = None
        custom_status = None
        if "status" in self.discord_settings and "status" in self.discord_settings["status"]:
            status = self.discord_settings["status"]["status"]
            if "customStatus" in self.discord_settings["status"]:
                custom_status_emoji = {
                    "id": self.discord_settings["status"]["customStatus"].get("emojiID"),
                    "name": self.discord_settings["status"]["customStatus"].get("emojiName"),
                    "animated": self.discord_settings["status"]["customStatus"].get("animated", False),
                }
                custom_status = self.discord_settings["status"]["customStatus"].get("text")
            if custom_status_emoji and not (custom_status_emoji["name"] or custom_status_emoji["id"]):
                custom_status_emoji = None
        else:   # just in case
            status = "online"
            custom_status = None
            custom_status_emoji = None
        self.my_status.update({
            "status": status,
            "custom_status": custom_status,
            "custom_status_emoji": custom_status_emoji,
        })
        self.update_status_line()


    def get_media_session_id(self):
        """Return current media session id"""
        if self.voice_gateway:
            return self.voice_gateway.get_media_session_id()


    def get_voice_channel_id(self):
        """Return current voice channel id"""
        if self.in_call:
            return self.in_call["channel_id"]


    def send_desktop_notification(self, new_message):
        """
        Send desktop notification, and keep its ID so it can be removed.
        """
        if self.enable_notifications and self.my_status["status"] != "dnd":
            data = new_message["d"]
            channel_id = data["channel_id"]

            # remove previous notification
            if self.remove_prev_notif:
                for num, notification in enumerate(self.notifications):
                    if notification["channel_id"] == channel_id:
                        peripherals.notify_remove(notification["id"])
                        self.notifications.pop(num)
                        break

            # collect data
            guild_id = data["guild_id"]
            for guild in self.guilds:
                if guild["guild_id"] == guild_id:
                    guild_name = guild["name"]
                    channels = guild["channels"]
                    break
            else:
                guild_name = None
                channels = []
            for guild in self.all_roles:
                if guild["guild_id"] == guild_id:
                    guild_roles = guild["roles"]
                    break
            else:
                guild_roles = []

            # build and send notification
            title, body = formatter.generate_message_notification(
                data,
                channels,
                guild_roles,
                guild_name,
                self.config["convert_timezone"],
                use_global_name=("%global_name" in self.config["format_message"]),
                use_nick=self.config["use_nick_when_available"],
            )
            notification_id = peripherals.notify_send(
                title,
                body,
                sound=self.notification_sound,
                custom_sound=self.notification_path,
            )

            # save notification id
            self.notifications.append({
                "id": notification_id,
                "channel_id": channel_id,
            })


    def main(self):
        """Main app method"""
        logger.info("Init sequence started")
        logger.info("Waiting for ready signal from gateway")
        self.my_status["client_state"] = "connecting"

        # wait for gateway and load data from it
        while not self.gateway.get_ready():
            if self.gateway.error:
                logger.fatal(f"Gateway error: \n {self.gateway.error}")
                sys.exit(self.gateway.error + ERROR_TEXT)
            time.sleep(0.2)
        self.my_id = self.gateway.get_my_id()
        self.premium = self.gateway.get_premium()
        self.my_user_data = self.gateway.get_my_user_data()
        self.update_prompt()
        self.reset_states(replying=True)
        self.update_status_line()
        self.session_id = self.gateway.session_id
        self.my_status["client_state"] = "online"
        self.update_status_line()
        self.discord_settings = self.gateway.get_settings_proto()
        logger.debug(f"Premium tier: {self.premium}")

        # perform token update if needed
        new_token = self.gateway.get_token_update()
        if new_token:
            from endcord import profile_manager
            profile_manager.refresh_token(
                new_token,
                self.profiles["selected"],
                os.path.join(peripherals.config_path, "profiles.json"),
            )
            del sys.modules["endcord.profile_manager"]   # save resources
            del profile_manager
        del new_token

        # download proto if its not in gateway
        if "status" not in self.discord_settings:
            self.discord_settings = self.discord.get_settings_proto(1)

        # guild position
        self.guild_folders = []
        found = []
        if "guildFolders" in self.discord_settings:
            for folder in self.discord_settings["guildFolders"].get("folders", []):
                self.guild_folders.append({
                    "id": folder.get("id"),
                    "guilds": folder["guildIds"],
                })
                found += folder["guildIds"]
            # if some folders are missing use default positions
            missing_guilds = []
            for guild in self.discord_settings["guildFolders"].get("guildPositions", []):
                if guild not in found:   # deduplicate
                    missing_guilds.append(guild)
            self.guild_folders.append({
                "id": "MISSING",
                "guilds": missing_guilds,
            })
        if logger.getEffectiveLevel() == logging.DEBUG:
            debug.save_json(debug.anonymize_guild_folders(self.guild_folders), "guild_folders.json")
        del found

        # custom status
        self.update_presence_from_proto()

        self.gateway_state = 1
        logger.info("Gateway is ready")
        self.chat.insert(0, "Loading channels")
        self.tui.update_chat(self.chat, [[[self.colors[0]]]] * len(self.chat))

        # get data from gateway
        guilds = self.gateway.get_guilds()
        if guilds:
            self.guilds = guilds
        self.all_roles = self.gateway.get_roles()
        self.all_roles = color.convert_role_colors(self.all_roles, default=self.config["color_default"][0])

        # get my roles and compute perms
        self.my_roles = self.gateway.get_my_roles()
        self.compute_permissions()

        # load locally hidden channels
        self.hidden_channels = peripherals.load_json("hidden_channels.json")
        if not self.hidden_channels:
            self.hidden_channels = []
        for hidden in self.hidden_channels:
            self.hide_channel(hidden["channel_id"], hidden["guild_id"])

        # some checks
        if "~/.cache" in peripherals.temp_path:
            logger.warning(f"Temp files will be stored in {peripherals.temp_path}")
        if self.config["proxy"]:
            logger.info(f"Using proxy: {self.config["proxy"]}")

        # load dms
        self.load_dms()
        new_activities = self.gateway.get_dm_activities()
        if new_activities:
            self.activities = new_activities

        # load pings, unseen and blocked
        self.read_state = self.gateway.get_read_state()
        self.blocked = self.gateway.get_blocked()
        self.run = True

        # restore last state
        if self.config["remember_state"]:
            self.state = peripherals.load_json(f"state_{self.profiles["selected"]}.json", self.state)
            if self.fun == 3 and "snow" in self.state and not self.state["snow"]:
                self.fun = 1
        if self.state["last_guild_id"] in self.state["collapsed"]:
            self.state["collapsed"].remove(self.state["last_guild_id"])
        for folder in self.guild_folders:
            if folder["id"] and folder["id"] != "MISSING" and folder["id"] not in self.state["collapsed"]:
                self.state["collapsed"].append(folder["id"])
        if self.config["remember_tabs"]:
            for channel_id in self.state["tabbed_channels"]:
                self.add_to_channel_cache(channel_id, [], True)
        self.gateway.set_want_member_list(self.get_members and self.state.get("member_list", True))
        self.tui.set_fun(self.fun)

        # load summaries
        if self.save_summaries:
            self.summaries = peripherals.load_json("summaries.json", [])

        # load messages
        if self.state["last_channel_id"]:
            self.chat.insert(0, "Loading messages")
            self.tui.update_chat(self.chat, [[[self.colors[0]]]] * len(self.chat))
            guild_id = self.state["last_guild_id"]
            channel_id = self.state["last_channel_id"]
            channel_name = None
            guild_name = None
            if guild_id:
                for guild in self.guilds:
                    if guild["guild_id"] == guild_id:
                        guild_name = guild["name"]
                        break
                else:
                    guild = {"channels": []}
                for channel in guild["channels"]:
                    if channel["id"] == channel_id and channel.get("permitted"):
                        channel_name = channel["name"]
                        break
            else:
                for channel in self.dms:
                    if channel["id"] == channel_id:
                        channel_name = channel["name"]
                        break
            if channel_name:
                self.switch_channel(channel_id, channel_name, guild_id, guild_name, preload=True, delay=True)
                self.tui.tree_select_active()
            else:
                self.chat.insert(0, "Select channel to load messages")
                self.tui.update_chat(self.chat, [[[self.colors[0]]]] * len(self.chat))
        else:
            self.chat.insert(0, "Select channel to load messages")
            self.tui.update_chat(self.chat, [[[self.colors[0]]]] * len(self.chat))
        self.preloaded = False
        self.need_preload = False

        # collapse all guilds in tree on first run or profile change
        if not self.active_channel["channel_id"]:
            for guild in self.guilds:
                self.state["collapsed"].append(0)
                self.state["collapsed"].append(guild["guild_id"])

        # open uncollapsed guilds, generate and draw tree
        self.open_guild(self.active_channel["guild_id"], restore=True)

        # send new presence
        self.gateway.update_presence(
            self.my_status["status"],
            custom_status=self.my_status["custom_status"],
            custom_status_emoji=self.my_status["custom_status_emoji"],
            activities=self.my_activities,
            afk=self.my_status["afk"],
        )

        # start input and sender threads
        threading.Thread(target=self.wait_input, daemon=True, args=()).start()
        threading.Thread(target=self.message_sender, daemon=True).start()

        # start RPC server
        if self.enable_rpc:
            self.rpc = rpc.RPC(self.discord, self.my_user_data, self.config)

        # start game detection service
        if self.enable_game_detection:
            self.game_detection = game_detection.GameDetection(
                self,
                self.discord,
                self.state["games_blacklist"],
                self.game_detection_download_delay,
            )

        # start extra line remover thread
        threading.Thread(target=self.extra_line_remover, daemon=True).start()

        # startup popups
        if self.fun in (3, 4):
            if self.last_run:
                last_run = datetime.utcfromtimestamp(self.last_run)
                last_run = (last_run.month, last_run.day)
            else:
                last_run = (6, 1)
            if self.fun == 3:
                self.update_extra_line("Snow easter egg can be CPU-heavy. Run 'toggle_snow' command to stop it.", timed=False)
            if self.fun == 4 and last_run != (4, 1):
                self.update_extra_line("Personalized ADs will be shown here. Click this to opt-out.", timed=False)

        self.execute_extensions_methods("on_main_start")

        logger.info(f"Main loop started after {round((time.time() - self.init_time) * 1000, 3)} ms")
        del self.init_time

        while self.run:
            selected_line, text_index = self.tui.get_chat_selected()

            self.execute_extensions_methods("on_main_loop", cache=True)

            # get new messages
            while self.run:
                new_message = self.gateway.get_messages()
                if new_message:
                    new_message = self.execute_extensions_methods("on_message_event", new_message, cache=True)[0]
                    new_message_channel_id = new_message["d"]["channel_id"]
                    this_channel = (new_message_channel_id == self.active_channel["channel_id"])
                    if this_channel and self.get_chat_last_message_id() == self.last_message_id:   # if its scrolled far up, this channel bot is cached
                        self.process_msg_events_active_channel(new_message, selected_line)
                    # handle cached channels
                    elif self.limit_channel_cache:
                        in_cache = False
                        for ch_num, channel in enumerate(self.channel_cache):
                            if channel[0] == new_message_channel_id:
                                in_cache = True
                                break
                        if in_cache:
                            self.process_msg_events_cached_channel(new_message, ch_num)
                        if this_channel:
                            # still have to do this when scrolled far up, only to handle message delete/edit/react/poll
                            self.process_msg_events_active_channel(new_message, selected_line, latest_chat=False)
                    # handle unseen and mentions
                    if not this_channel or (this_channel and (self.new_unreads or self.ping_this_channel or self.tui.disable_drawing or self.tui.is_window_open())):
                        self.process_msg_events_other_channels(new_message)
                    # remove ghost pings
                    self.process_msg_events_ghost_ping(new_message)
                    # rearrange dms
                    if not new_message["d"]["guild_id"]:
                        self.set_dm_first(new_message_channel_id)
                else:
                    break

            # get new typing
            while self.run:
                new_typing = self.gateway.get_typing()
                if new_typing:
                    if (
                        new_typing["channel_id"] == self.active_channel["channel_id"] and
                        new_typing["user_id"] not in self.blocked and
                        new_typing["user_id"] != self.my_id
                    ):
                        if not new_typing["username"]:   # its DM
                            for dm in self.dms:
                                if dm["id"] == new_typing["channel_id"]:
                                    new_typing["username"] = dm["recipients"][0]["username"]
                                    new_typing["global_name"] = dm["recipients"][0]["global_name"]
                                    break
                        for num, user in enumerate(self.typing):
                            if user["user_id"] == new_typing["user_id"]:
                                self.typing[num]["timestamp"] = new_typing["timestamp"]
                                break
                        else:
                            self.typing.append(new_typing)
                        self.update_status_line()
                else:
                    break

            # get new summaries
            if self.save_summaries:
                while self.run:
                    new_summary = self.gateway.get_summaries()
                    if new_summary:
                        self.update_summary(new_summary)
                    else:
                        break

            # get new message_ack
            while self.run:
                new_message_ack = self.gateway.get_message_ack()
                if new_message_ack:
                    self.set_channel_seen(new_message_ack["channel_id"], new_message_ack["message_id"], ack=False)
                else:
                    break

            # get thread updates
            while self.run:
                thread_event = self.gateway.get_threads()
                if thread_event:
                    if thread_event["op"] == "THREAD_UPDATE":
                        self.load_threads(thread_event)   # add or update thread
                    elif thread_event["op"] == "THREAD_DELETE":
                        self.remove_thread()
                else:
                    break

            # get new call events
            while self.run:
                new_call_event = self.gateway.get_call_events()
                if new_call_event:
                    self.process_call_gateway_events(new_call_event)
                else:
                    break

            # voice gateway stuff
            if self.voice_gateway:
                # check if voice call disconnected with errror
                if not self.voice_gateway.run:
                    # if it errored there will still be self.voice_gateway but it wont be running
                    self.leave_call()
                if self.voice_gateway:
                    # get new call events
                    while self.run:
                        new_call_event = self.voice_gateway.get_call_events()
                        if new_call_event:
                            self.process_call_voice_gateway_events(new_call_event)
                        else:
                            break

                    # check voice gateway state
                    if self.in_call:
                        if not self.voice_gateway.get_state():
                            self.leave_call()

            # send new rpc activities
            if self.enable_rpc:
                rpc_activities, changed = self.rpc.get_activities()
                if changed and self.gateway_state == 1:
                    if self.enable_game_detection:
                        game_activities, _ = self.game_detection.get_activities()
                        rpc_apps_ids = [d["application_id"] for d in rpc_activities]
                        self.my_activities = rpc_activities + [d for d in game_activities if d["application_id"] not in rpc_apps_ids]
                    else:
                        self.my_activities = rpc_activities
                    self.gateway.update_presence(
                        self.my_status["status"],
                        custom_status=self.my_status["custom_status"],
                        custom_status_emoji=self.my_status["custom_status_emoji"],
                        activities=self.my_activities,
                        afk=self.my_status["afk"],
                    )

            # send new detectable games activities
            if self.enable_game_detection:
                game_activities, changed = self.game_detection.get_activities()
                if changed and self.gateway_state == 1:
                    if self.enable_rpc:
                        rpc_activities, _ = self.rpc.get_activities()
                        rpc_apps_ids = [d["application_id"] for d in rpc_activities]
                        self.my_activities = rpc_activities + [d for d in game_activities if d["application_id"] not in rpc_apps_ids]
                    else:
                        self.my_activities = game_activities
                    self.gateway.update_presence(
                        self.my_status["status"],
                        custom_status=self.my_status["custom_status"],
                        custom_status_emoji=self.my_status["custom_status_emoji"],
                        activities=self.my_activities,
                        afk=self.my_status["afk"],
                    )

            # remove expired typing
            if self.typing:
                for num, user in enumerate(self.typing):
                    if round(time.time()) - user["timestamp"] > 10:
                        self.typing.pop(num)
                        self.update_status_line()

            # send typing event
            if self.send_my_typing and not self.disable_sending:
                my_typing = self.tui.get_my_typing()
                # typing indicator on server expires in 10s, so lest stay safe with 7s
                if not self.ignore_typing and my_typing and time.time() >= self.typing_sent + 7:
                    slowmode_time = self.discord.send_typing(self.active_channel["channel_id"])
                    self.typing_sent = int(time.time())
                    # check for slowmode
                    if slowmode_time and slowmode_time != 1 and self.active_channel["channel_id"] not in self.slowmode_times:
                        self.slowmode_times[self.active_channel["channel_id"]] = slowmode_time
                        self.update_extra_line(f"Slowmode is enabled, will be able to send message in {slowmode_time} s")
                        if not self.slowmode_thread or not self.slowmode_thread.is_alive():
                            self.slowmode_thread = threading.Thread(target=self.wait_slowmode, daemon=True, args=())
                            self.slowmode_thread.start()

            # remove unseen after scrolled to bottom on unseen channel
            if self.new_unreads or self.this_unread:
                if text_index == 0:
                    self.new_unreads = False
                    self.this_unread = False
                    self.update_status_line()
                    self.set_channel_seen(self.active_channel["channel_id"], self.get_chat_last_message_id())

            # send pending ack
            if self.pending_acks and time.time() - self.sent_ack_time > self.ack_throttling:
                self.send_ack()

            # check gateway state
            gateway_state = self.gateway.get_state()
            if gateway_state != self.gateway_state:
                self.gateway_state = gateway_state
                if self.gateway_state == 1:
                    self.my_status["client_state"] = "online"
                    self.reconnect()
                elif self.gateway_state == 2:
                    self.my_status["client_state"] = "connecting"
                else:
                    self.my_status["client_state"] = "OFFLINE"
                self.update_status_line()

            # check for changes in guilds
            guilds = self.gateway.get_guilds()
            if guilds:
                self.guilds = guilds
                self.load_dms()
                self.compute_permissions()
                self.select_current_channels(refresh=True)
                self.update_tree()
                self.update_status_line()

            # check for changes in dimensions
            new_chat_dim = self.tui.get_dimensions()[0]
            if new_chat_dim != self.chat_dim:
                if self.chat_dim[1] != new_chat_dim[1]:
                    self.execute_extensions_methods("on_resize")
                    if self.forum:
                        self.update_forum(self.active_channel["guild_id"], self.active_channel["channel_id"])
                        self.tui.update_chat(self.chat, self.chat_format)
                    else:
                        self.chat_dim = new_chat_dim
                        self.update_chat(scroll=False)
                if self.most_recent_incoming_call or self.active_channel["channel_id"] in self.incoming_calls:
                    new_permanent_extra_line = None
                    if self.most_recent_incoming_call:
                        incoming_call_ch_id = self.most_recent_incoming_call
                    else:
                        incoming_call_ch_id = self.active_channel["channel_id"]
                    for dm in self.dms:
                        if dm["id"] == incoming_call_ch_id:
                            new_permanent_extra_line = formatter.generate_extra_line_ring(
                                dm["name"],
                                self.tui.get_dimensions()[2][1],
                            )
                            break
                    if self.in_call:
                        new_permanent_extra_line = formatter.generate_extra_line_call(
                            self.call_participants,
                            self.state.get("muted"),
                            self.tui.get_dimensions()[2][1],
                        )
                    if new_permanent_extra_line and new_permanent_extra_line != self.permanent_extra_line:
                        self.update_extra_line(custom_text=new_permanent_extra_line, permanent=True)
                if self.tui.get_dimensions()[1] != self.tree_dim:
                    self.update_tree()
                    self.tree_dim = self.tui.get_dimensions()[1]
                self.chat_dim = new_chat_dim

            # check and update my status
            new_status = self.gateway.get_my_status()
            if new_status:
                self.my_status.update(new_status)
                self.my_status["activities"] = new_status["activities"]
                self.update_status_line()
            new_proto = self.gateway.get_settings_proto()
            if new_proto:
                self.discord_settings = new_proto
                self.update_presence_from_proto()
                self.gateway.update_presence(
                    self.my_status["status"],
                    custom_status=self.my_status["custom_status"],
                    custom_status_emoji=self.my_status["custom_status_emoji"],
                    activities=self.my_activities,
                    afk=self.my_status["afk"],
                )

            # check changes in presences and update tree
            new_activities = self.gateway.get_dm_activities()
            if new_activities:
                self.activities = new_activities
                self.update_tree()

            # check for user data updates
            new_user_data = self.gateway.get_user_update()
            if new_user_data:
                self.process_new_user_data(new_user_data)

            # check for new member presences
            if self.get_members:
                new_members, changed_guilds = self.gateway.get_activities()
                if changed_guilds:
                    self.members = new_members
                    last_index = 99
                    for guild in new_members:   # select guild
                        if guild[0] == self.active_channel["guild_id"]:
                            if "everyone" in guild[1]:
                                self.member_list = guild[1]["everyone"][1]
                                last_index = guild[1]["everyone"][0]
                            elif guild[1]:
                                self.member_list = next(iter(guild[1].values()))[1]   # fix_member_list_selection
                                last_index = next(iter(guild[1].values()))[0]
                            else:
                                self.member_list = []
                                last_index = None

                            break
                    if self.active_channel["guild_id"] in changed_guilds and self.state.get("member_list"):
                        self.update_member_list(last_index)

            # check for subscribed member presences
            new_members, changed_guilds = self.gateway.get_subscribed_activities()
            if changed_guilds:
                self.subscribed_members = new_members
                for guild in new_members:   # select guild
                    if guild["guild_id"] == self.active_channel["guild_id"]:
                        self.current_subscribed_members = guild["members"]
                        break
                if self.active_channel["guild_id"] in changed_guilds:
                    if self.viewing_user_data["id"]:
                        self.view_profile(user_data=self.viewing_user_data, reset=False)

            # check for new member roles
            new_member_roles, nonce = self.gateway.get_member_roles()
            if new_member_roles:
                self.member_roles = new_member_roles
                if nonce is not True and (nonce == self.missing_members_nonce or nonce == self.active_channel["channel_id"]):
                    self.select_current_member_roles()
                    self.update_chat(scroll=False)
                    self.missing_members_nonce = False

            # check for tree format changes
            self.check_tree_format()

            # check if new chat chunks needs to be downloaded in any direction
            if not self.forum and self.messages:
                if (selected_line == 0 or text_index == 0) and self.get_chat_last_message_id() != self.last_message_id:
                    self.get_chat_chunk(past=False, scroll=not(text_index == 0 and selected_line <= 2))
                elif (selected_line >= len(self.chat) - 1 or self.tui.get_chat_scrolled_top()) and not self.chat_end:
                    self.get_chat_chunk(past=True, scroll=self.tui.get_chat_scrolled_top())
            elif self.forum and not self.forum_end:
                len_forum = len(self.messages) + len(self.forum_old)
                if len_forum <= 1 or selected_line >= len_forum - 2:
                    self.get_forum_chunk()

            # check for message search chunks
            if self.search and self.extra_indexes:
                extra_selected = self.tui.get_extra_selected()
                if extra_selected >= len(self.extra_body) - 2 and not self.search_end:
                    self.extend_search()

            # check if assist is needed
            assist_word, assist_type = self.tui.get_assist()
            if assist_type and not self.uploading:
                if assist_type == 100:   # esc or (" " in assist_word and assist_type not in (5, 6)):
                    self.stop_assist()
                elif assist_type == 6:   # app commands
                    if assist_word != self.assist_word and not (self.disable_sending or self.forum):
                        self.ignore_typing = True
                        if not self.got_commands:
                            # this will be allowed to run when channel changes
                            self.got_commands = True
                            self.my_commands, self.my_apps = self.discord.get_my_commands()
                            if self.active_channel["guild_id"]:
                                self.guild_commands, self.guild_apps = self.discord.get_guild_commands(self.active_channel["guild_id"])
                                # permissions depend on channel so they myt be computed each time
                                self.guild_commands_permitted = perms.compute_command_permissions(
                                    self.guild_commands,
                                    self.guild_apps,
                                    self.active_channel["channel_id"],
                                    self.active_channel["guild_id"],
                                    self.current_my_roles,
                                    self.my_id,
                                    self.active_channel["admin"],
                                    self.current_channel.get("perms_computed", 0),
                                )
                        self.assist(assist_word, assist_type)
                    elif not self.allow_app_command_autocomplete and time.time() - self.app_command_last_keypress >= APP_COMMAND_AUTOCOMPLETE_DELAY:
                        self.allow_app_command_autocomplete = True
                    app_command_autocomplete_resp = self.gateway.get_app_command_autocomplete_resp()
                    if app_command_autocomplete_resp:
                        self.app_command_autocomplete_resp = app_command_autocomplete_resp
                        self.assist(assist_word, assist_type)
                elif assist_word != self.assist_word:
                    self.assist(assist_word, assist_type)
            elif assist_type == 7 and assist_word != self.assist_word:   # path
                paths = peripherals.complete_path(assist_word, separator=True)
                self.assist(assist_word, assist_type, query_results=paths)

            # check member assist query results
            if self.assist_type == 2:
                query_results = self.gateway.get_member_query_results()
                if query_results:
                    self.assist(self.assist_word, self.assist_type, query_results=query_results)

            # check gateway for errors
            if self.gateway.error:
                logger.fatal(f"Gateway error: \n {self.gateway.error}")
                sys.exit(self.gateway.error + ERROR_TEXT)

            time.sleep(0.1)   # some reasonable delay
