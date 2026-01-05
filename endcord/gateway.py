import base64
import gc
import http.client
import logging
import random
import socket
import ssl
import struct
import threading
import time
import traceback
import urllib
import urllib.parse
import zlib

try:
    import orjson as json
except ImportError:
    try:
        import ujson as json
    except ImportError:
        import json

import socks
import websocket
from discord_protos import PreloadedUserSettings
from google.protobuf.json_format import MessageToDict

from endcord import debug, perms
from endcord.message import prepare_message

DISCORD_HOST = "discord.com"
LOCAL_MEMBER_COUNT = 50   # members per guild, CPU-RAM intensive
ZLIB_SUFFIX = b"\x00\x00\xff\xff"
VOICE_FLAGS = 3   # CLIPS_ENABLED and ALLOW_VOICE_RECORDING
DEFAULT_CAPABILITIES = 30717
DEFAULT_INTENTS = 50364033
QOS_HEARTBEAT = True
QOS_PAYLOAD = {"ver": 26, "active": True, "reason": "foregrounded"}
inflator = zlib.decompressobj()
logger = logging.getLogger(__name__)
status_unpacker = struct.Struct("!H")


def zlib_decompress(data):
    """Decompress zlib data, if it is not zlib compressed, return data instead"""
    if len(data) < 4 or data[-4:] != ZLIB_SUFFIX:
        return data
    try:
        return inflator.decompress(data)
    except zlib.error as e:
        logger.error(f"zlib error: {e}")
        return None


def reset_inflator():
    """Resets inflator object"""
    global inflator
    del inflator
    inflator = zlib.decompressobj()   # noqa


def double_get(data, key1, key2, default=None):
    """Get value from 2 nested dicts"""
    if key1 in data:
        return data[key1].get(key2, default)
    return default


class Gateway():
    """Methods for fetching and sending data to Discord gateway through websocket"""

    def __init__(self, token, host, client_prop, user_agent, proxy=None, capablities=None):
        if host:
            host_obj = urllib.parse.urlsplit(host)
            if host_obj.netloc:
                self.host = host_obj.netloc
            else:
                self.host = host_obj.path
        else:
            self.host = DISCORD_HOST

        self.header = [
            "Connection: keep-alive, Upgrade",
            "Sec-WebSocket-Extensions: permessage-deflate",
            f"User-Agent: {user_agent}",
        ]
        self.capabilities = None
        if capablities is not None:
            try:
                self.capabilities = int(capablities)
            except ValueError:
                pass

        self.extensions = []
        self.client_prop = client_prop
        self.init_time = time.time() * 1000
        self.token = token
        self.proxy = urllib.parse.urlsplit(proxy)
        self.run = True
        self.wait = False
        self.state = 0
        self.heartbeat_received = True
        self.sequence = None
        self.resume_gateway_url = ""
        self.session_id = ""
        self.clear_ready_vars()
        self.want_member_list = False
        self.want_summaries = True
        self.messages_buffer = []
        self.typing_buffer = []
        self.summaries_buffer = []
        self.msg_ack_buffer = []
        self.threads_buffer = []
        self.call_buffer = []
        self.reconnect_requested = False
        self.status_changed = False
        self.dm_activities_changed = False
        self.roles_changed = False
        self.user_settings_proto = None
        self.proto_changed = False
        self.legacy = "spacebar" in self.host
        self.activities = []
        self.activities_changed = []
        self.subscribed_activities = []
        self.subscribed_activities_changed = []
        self.subscribed_channels = []
        self.emojis = []
        self.stickers = []
        self.token_update = None
        self.user_update = None
        self.guild_roles_changed = None
        self.premium = False
        self.error = None
        self.querying_members = False
        self.member_query_results = []
        self.resumable = False
        threading.Thread(target=self.thread_guard, daemon=True, args=()).start()


    def clear_ready_vars(self):
        """Clear local variables when new READY event is received"""
        self.ready = False
        self.my_status = {}
        self.dm_activities = []
        self.guilds_changed = True
        self.guilds = []
        self.roles = []
        self.member_roles = []
        self.read_state = {}
        self.subscribed = []
        self.dms = []
        self.dms_id = []
        self.blocked = []
        self.my_roles = []
        self.guilds_changed = False
        self.app_command_autocomplete_resp = []
        self.voice_gateway_data = {}
        self.voice_gateway_data_ready = 0


    def load_extensions(self, extensions):
        """Load already initialized extensions from app class"""
        self.extensions = extensions
        self.extension_cache = []


    def execute_extensions_method_nochain(self, method_name, *args, cache=False):
        """Execute specific method for each extension if extension has this method, without chaining"""
        if not self.extensions:
            return args

        # try to load from cache (improves performance with many extensions)
        if cache:
            for extension_point in self.extension_cache:
                if extension_point[0] == method_name:
                    for method in extension_point[1]:
                        _ = method(*args)
            return

        # try to load method from extensions and add to cache
        methods = []
        for extension in self.extensions:
            method = getattr(extension, method_name, None)
            if callable(method):
                if cache:
                    methods.append(method)
                _ = method(*args)
        if cache:
            self.extension_cache.append((method_name, methods))


    def thread_guard(self):
        """Check if reconnect is requested and run reconnect thread if its not running"""
        while self.run:
            if self.reconnect_requested:
                self.reconnect_requested = False
                if not self.reconnect_thread.is_alive():
                    self.reconnect_thread = threading.Thread(target=self.reconnect, daemon=True, args=())
                    self.reconnect_thread.start()
            time.sleep(0.5)


    def connect_ws(self, resume=False):
        """Connect to websocket"""
        if resume and self.resume_gateway_url:
            gateway_url = self.resume_gateway_url
        else:
            gateway_url = self.gateway_url
        self.ws = websocket.WebSocket()
        if self.proxy.scheme:
            self.ws.connect(
                gateway_url + "/?v=9&encoding=json&compress=zlib-stream",
                header=self.header,
                proxy_type=self.proxy.scheme,
                http_proxy_host=self.proxy.hostname,
                http_proxy_port=self.proxy.port,
            )
        else:
            self.ws.connect(gateway_url + "/?v=9&encoding=json&compress=zlib-stream", header=self.header)


    def disconnect_ws(self, timeout=2, status=1000):
        """Close websocket with timeout"""
        if self.ws:
            try:
                self.ws.settimeout(timeout)
                self.ws.close(status=status)
                logger.info(f"Disconnected with status code {status}")
            except Exception as e:
                logger.warning("Error closing websocket:", e)
            finally:
                self.ws = None


    def connect(self):
        """Create initial connection to Discord gateway"""
        # get proxy
        if self.proxy.scheme:
            if self.proxy.scheme.lower() == "http":
                connection = http.client.HTTPSConnection(self.proxy.hostname, self.proxy.port)
                connection.set_tunnel(self.host, port=443)
            elif "socks" in self.proxy.scheme.lower():
                proxy_sock = socks.socksocket()
                proxy_sock.set_proxy(socks.SOCKS5, self.proxy.hostname, self.proxy.port)
                proxy_sock.connect((self.host, 443))
                ssl_context = ssl.create_default_context()
                ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
                proxy_sock = ssl_context.wrap_socket(proxy_sock, server_hostname=self.host)
                proxy_sock.do_handshake()   # seems like its not needed
                connection = http.client.HTTPSConnection(self.host, 443)
                connection.sock = proxy_sock
            else:
                logger.warning("Invalid proxy, continuing without proxy")
                connection = http.client.HTTPSConnection(self.host, 443)
        else:
            connection = http.client.HTTPSConnection(self.host, 443)

        # get gateway url
        try:
            # subscribe works differently in v10
            connection.request("GET", "/api/v9/gateway")
        except (socket.gaierror, TimeoutError):
            connection.close()
            logger.warning("No internet connection. Exiting...")
            raise SystemExit("No internet connection. Exiting...")
        response = connection.getresponse()
        if response.status == 200:
            data = response.read()
            connection.close()
            self.gateway_url = json.loads(data)["url"]
        else:
            connection.close()
            logger.error(f"Failed to get gateway url. Response code: {response.status}. Exiting...")
            raise SystemExit(f"Failed to get gateway url. Response code: {response.status}. Exiting...")

        self.connect_ws()
        self.state = 1
        self.heartbeat_interval = int(json.loads(zlib_decompress(self.ws.recv()))["d"]["heartbeat_interval"])
        self.receiver_thread = threading.Thread(target=self.safe_function_wrapper, daemon=True, args=(self.receiver, ))
        self.receiver_thread.start()
        self.heartbeat_thread = threading.Thread(target=self.send_heartbeat, daemon=True)
        self.heartbeat_thread.start()
        self.reconnect_thread = threading.Thread()
        self.authenticate()


    def safe_function_wrapper(self, function, args=()):
        """
        Wrapper for a function running in a thread that captures error and stores it for later use.
        Error can be accessed from main loop and handled there.
        """
        try:
            function(*args)
        except BaseException as e:
            self.error = "".join(traceback.format_exception(e))


    def send(self, request):
        """Send data to gateway"""
        try:
            self.ws.send(json.dumps(request))
        except websocket._exceptions.WebSocketException:
            self.reconnect_requested = True


    def add_member_roles(self, guild_id, user_id, roles, nick=None, nonce=None):
        """Add member-role pair to corresponding guild, number of users per guild is limited"""
        num = -1
        for num, guild in enumerate(self.member_roles):
            if guild["guild_id"] == guild_id:
                break
        else:
            self.member_roles.append({
                "guild_id": guild_id,
                "members": [],
            })
            num += 1
        for member in self.member_roles[num]["members"]:
            if member["user_id"] == user_id:
                return
        self.member_roles[num]["members"].insert(0, {
            "user_id": user_id,
            "roles": roles,
            "nick": nick,
        })
        if len(self.member_roles[num]) > LOCAL_MEMBER_COUNT:
            self.member_roles[num].pop(-1)
        if not self.roles_changed:
            self.roles_changed = nonce or True


    def process_hidden_channels(self):
        """Search for unprocessed guilds and handle channel/category hiding logic"""
        for guild_num, guild in enumerate(self.guilds):
            if "opt_in_channels" in guild:
                owned = guild["owned"]
                community = guild["community"]
                opt_in_channels = guild.pop("opt_in_channels", False)
                for category_num, category in enumerate(guild["channels"]):
                    if owned or not community or opt_in_channels:   # cant hide channels in owned and non-community guild
                        self.guilds[guild_num]["channels"][category_num]["hidden"] = False
                        continue
                    if category["type"] == 4:
                        category_id = category["id"]
                        if not category["hidden"]:
                            # if category is not hidden - show its channels
                            for channel in guild["channels"]:
                                if channel["parent_id"] == category_id:
                                    channel["hidden"] = False
                        else:
                            # if category is hidden - hide its channels
                            for channel in guild["channels"]:
                                if channel["parent_id"] == category_id:
                                    if not channel["hidden"]:
                                        self.guilds[guild_num]["channels"][category_num]["hidden"] = False
                                        break


    def process_one_channel_overrides(self, channel_overrides, guild_num, guild_message_notifications):
        """Process channel_overrides for one guild"""

        # first pass to get values and process message_notifications for categories
        for channel in channel_overrides:
            for channel_num, channel_g in enumerate(self.guilds[guild_num]["channels"]):
                if channel_g["id"] == channel["channel_id"]:
                    break
            else:
                continue
            if channel_g["type"] in (0, 2, 4, 5, 15):
                flags = int(channel.get("flags", 0))
                hidden = not perms.decode_flag(flags, 12)   # manually hidden
            else:
                hidden = False
            self.guilds[guild_num]["channels"][channel_num].update({
                "message_notifications": channel["message_notifications"],
                "muted": channel["muted"],
                "hidden": hidden,
                "collapsed": channel.get("collapsed", False),   # spacebar_fix - get
            })

        # second pass to process message_notifications for categories
        for channel in self.guilds[guild_num]["channels"]:
            if channel["type"] == 4 and ("message_notifications" not in channel or channel["message_notifications"] == 3):    # category with server defaults notifications
                channel["message_notifications"] = 10 + guild_message_notifications

        # third pass to process message_notifications for channels
        for channel in self.guilds[guild_num]["channels"]:
            if channel["type"] != 4 and ("message_notifications" not in channel or channel["message_notifications"] == 3):   # channel with category defaults notifications
                category_id = channel["parent_id"]
                for category in self.guilds[guild_num]["channels"]:
                    if category["id"] == category_id:
                        category_message_notifications = category["message_notifications"]
                        if category_message_notifications >= 10:
                            category_message_notifications -= 10
                        break
                else:
                    category_message_notifications = guild_message_notifications
                channel["message_notifications"] = 10 + category_message_notifications


    def add_guild(self, guild):
        """Process received guild object and add guild to the guilds channels, roles, threads, emojis and stickers lists"""
        if guild.get("unavailable"):
            return
        guild_id = guild["id"]
        if "properties" in guild:
            properties = guild["properties"]
        else:
            properties = guild
        guild_channels = []

        # channels
        bot = self.my_user_data["bot"]
        for channel in guild["channels"]:
            if channel["type"] in (0, 2, 4, 5, 15) and not bot:
                hidden = True   # hidden by default
            else:
                hidden = False
            data = {
                "id": channel["id"],
                "type": channel["type"],
                "name": channel["name"],
                "topic": channel.get("topic"),
                "parent_id": channel.get("parent_id"),
                "position": channel["position"],
                "permission_overwrites": channel["permission_overwrites"],
                "hidden": hidden,
            }
            if channel.get("rate_limit_per_user"):
                data["rate_limit"] = channel["rate_limit_per_user"]
            guild_channels.append(data)
        guild_roles = []
        base_permissions = 0

        # roles
        for role in guild["roles"]:
            if role["id"] == guild_id:
                base_permissions = role["permissions"]
            guild_roles.append({
                "id": role["id"],
                "name": role["name"],
                "color": role["color"],
                "position": role["position"],   # for sorting
                "hoist": role["hoist"],   # separated from online members
                "permissions": role["permissions"],
                # "flags": role["flags"],   # flags=1 - self-assign
                # "managed": role["managed"],   # for bots
            })
        # sort roles
        guild_roles = sorted(guild_roles, key=lambda x: x.get("position"), reverse=True)
        guild_roles = sorted(guild_roles, key=lambda x: not bool(x.get("color")))
        self.roles.append({
            "guild_id": guild_id,
            "roles": guild_roles,
        })

        # guild
        community = False
        for feature in properties["features"]:
            if feature in ("COMMUNITY", "COMMUNITY_CANARY"):
                community = True
                break
        self.guilds.append({
            "guild_id": guild_id,
            "owned": self.my_id == properties["owner_id"],
            "name": properties["name"],
            "description": properties["description"],
            "member_count": guild["member_count"],
            "channels": guild_channels,
            "base_permissions": base_permissions,
            "community": community,
            "premium": properties["premium_tier"],
            "opt_in_channels": True,   # will be overwritten later if opted-out
        })

        # threads
        threads = []
        for thread in guild.get("threads", []):
            if thread["member"]["flags"] == 3:
                message_notifications = 0
            elif thread["member"]["flags"] == 5:
                message_notifications = 1
            else:
                message_notifications = 2
            threads.append({
                "id": thread["id"],
                "type": thread["type"],
                "owner_id": thread["owner_id"],
                "name": thread["name"],
                "locked": thread["thread_metadata"]["locked"],
                "message_count": thread["message_count"],
                "timestamp": thread["thread_metadata"].get("create_timestamp"),
                "parent_id": thread["parent_id"],
                "suppress_everyone": False,   # no config for threads
                "suppress_roles": False,
                "message_notifications": message_notifications,
                "muted": thread["member"]["muted"],
                "joined": True,
            })
        self.threads_buffer.append({
            "op": "THREAD_UPDATE",
            "guild_id": guild_id,
            "threads": threads,
        })

        # emojis
        guild_emojis = []
        for emojis in guild["emojis"]:
            if emojis["available"]:
                guild_emojis.append({
                    "id": emojis["id"],
                    "name": emojis["name"],
                })
        self.emojis.append({
            "guild_id": guild["id"],
            "guild_name": properties["name"],
            "emojis": guild_emojis,
        })

        # stickers
        guild_stickers = []
        for sticker in guild["stickers"]:
            if sticker["available"]:
                guild_stickers.append({
                    "id": sticker["id"],
                    "name": sticker["name"],
                })
        self.stickers.append({
            "pack_id": guild["id"],
            "pack_name": properties["name"],
            "stickers": guild_stickers,
        })


    def add_dm(self, dm, data=[]):
        """Process received dm channel object and add it to dms list"""
        channel_id = dm["id"]

        recipients = []
        add_me = dm["type"] == 3   # group dm
        if "recipients" in dm:
            for recipient in dm["recipients"]:
                recipients.append({
                    "id": recipient["id"],
                    "username": recipient["username"],
                    "global_name": recipient.get("global_name"),   # spacebar_fix - get
                })
            else:   # spacebar_fix - can open dm with self
                add_me = True
        elif data:
            for recipient_id in dm["recipient_ids"]:
                for user in data["users"]:
                    if user["id"] == recipient_id:
                        recipients.append({
                            "id": recipient_id,
                            "username": user["username"],
                            "global_name": user.get("global_name"),   # spacebar_fix - get
                        })
                    elif recipient_id == self.my_id:   # spacebar_fix - can open dm with self
                        recipients.append(self.my_user_data)
        if add_me:
            recipients.append(self.my_user_data)

        name = None
        if "name" in dm and dm["name"]:   # for group dm
            name = dm["name"]
        elif "owner_id" in dm:   # unnamed group DM
            # first isolate owner
            owner_name = "Unknown"
            for recipient in recipients:
                if recipient["id"] == dm["owner_id"]:
                    if recipient["global_name"]:
                        owner_name = recipient["global_name"]
                    else:
                        owner_name = recipient["username"]
            # then build list of members names
            names = ""
            for recipient in recipients:
                if recipient["id"] != dm["owner_id"]:
                    if recipient["global_name"]:
                        names += f", {recipient["global_name"]}"
                    else:
                        names += f"{recipient["username"]}"
            if names:
                name = f"{owner_name}; {names.strip(", ")}"
            else:
                name = f"{owner_name}'s Group"
        elif recipients:   # regular DM
            if recipients[0]["global_name"]:
                name = recipients[0]["global_name"]
            else:
                name = recipients[0]["username"]
        if not name:
            name = "Unknown DM"

        last_message_id = dm.get("last_message_id", 0)
        if last_message_id is None:
            last_message_id = 0

        new_dm = {
            "id": dm["id"],
            "type": dm["type"],
            "recipients": recipients,
            "name": name,
            "is_spam": dm.get("is_spam"),
            "is_request": dm.get("is_message_request"),
            "muted": False,
            "last_message_id": int(last_message_id),
            "avatar": dm.get("avatar"),
        }
        for num, dm_old in enumerate(self.dms):
            if dm_old["id"] == channel_id:
                self.dms[num] = new_dm
                break
        else:
            self.dms.append(new_dm)


    def set_my_user_data(self, data):
        """Set my user data from user object"""
        tag = None
        if data.get("primary_guild") and "tag" in data["primary_guild"]:   # spacebar_fix - get
            tag = data["primary_guild"]["tag"]
        if data.get("bot"):
            extra_data = None
        else:
            extra_data = {
                "avatar": data["avatar"],
                "avatar_decoration_data": data.get("avatar_decoration_data"),   # spacebar_fix - get
                "discriminator": data["discriminator"],
                "flags": data.get("flags"),   # spacebar_fix - get
                "premium_type": data["premium_type"],
            }
        self.my_user_data = {
            "id": data["id"],
            "guild_id": None,
            "username": data["username"],
            "global_name": data.get("global_name"),   # spacebar_fix - get
            "nick": None,
            "bio": data.get("bio"),
            "pronouns":  data.get("pronouns"),
            "joined_at": None,
            "tag": tag,
            "bot": data.get("bot"),
            "extra": extra_data,
            "roles": None,
        }


    def receiver(self):
        """Receive and handle all traffic from gateway, should be run in a thread"""
        logger.debug("Receiver started")
        self.resumable = False
        while self.run and not self.wait:
            try:
                ws_opcode, data = self.ws.recv_data()
            except (
                ConnectionResetError,
                websocket._exceptions.WebSocketConnectionClosedException,
                OSError,
            ):
                self.resumable = True
                break
            if ws_opcode == 8 and len(data) >= 2:
                if not data:
                    self.resumable = True
                    break
                status = status_unpacker.unpack(data[0:2])[0]
                reason = data[2:].decode("utf-8", "replace")
                if status not in (1000, 1001):
                    logger.warning(f"Gateway status code: {status}, reason: {reason}")
                self.resumable = status in (4000, 4009)
                break
            try:
                data = zlib_decompress(data)
                if data:
                    try:
                        response = json.loads(data)
                        opcode = response["op"]
                    except ValueError:
                        response = None
                        opcode = None
                    del data
                else:
                    response = None
                    opcode = None
            except Exception as e:
                logger.warning(f"Receiver error: {e}")
                self.resumable = True
                break
            logger.debug(f"Received: opcode={opcode}, optext={response["t"] if (response and "t" in response and response["t"] and "LIST" not in response["t"]) else 'None'}")
            # debug_events
            # if response.get("t"):
            #     debug.save_json(response, f"{response["t"]}.json", False)

            if opcode == 11:
                self.heartbeat_received = True

            elif opcode == 10:
                self.heartbeat_interval = int(response["d"]["heartbeat_interval"])

            elif opcode == 1:
                self.send({"op": 1, "d": self.sequence})

            elif opcode == 0:
                self.sequence = int(response["s"])
                optext = response["t"]
                data = response["d"]
                guild = None
                guild_channels = None
                role = None
                guild_roles = None
                if optext == "READY":
                    ready_time_start = time.time()
                    self.resume_gateway_url = data["resume_gateway_url"]
                    self.session_id = data["session_id"]
                    self.clear_ready_vars()
                    time_log_string = "READY event time profile:\n"
                    last_messages = []
                    # get my user data
                    self.set_my_user_data(data["user"])
                    self.my_id = data["user"]["id"]
                    self.premium = data["user"].get("premium_type")   # 0 - none, 1 - classic, 2 - full, 3 - basic
                    if data.get("auth_token"):
                        self.token_update = data["auth_token"]
                    # guilds and channels
                    if ("guilds" not in data) and ("user_guild_settings" in data):
                        logger.warning("Abnormal READY event received, if its always happening, report this")
                        self.resumable = True
                        break
                    for guild in data["guilds"]:
                        self.add_guild(guild)
                        if not guild.get("unavailable"):
                            # build list of last messages from each channel
                            for channel in guild["channels"]:
                                if channel["type"] != 15:   # skip forums
                                    last_messages.append({
                                        "message_id": channel.get("last_message_id", 0),   # really last message id
                                        "channel_id": channel["id"],
                                    })
                            # add threads to list of last messages from channels
                            for thread in guild["threads"]:
                                last_messages.append({
                                    "message_id": thread.get("last_message_id", 0),   # really last message id
                                    "channel_id": thread["id"],
                                })
                    time_log_string += f"    guilds - {round((time.time() - ready_time_start) * 1000, 3)}ms\n"
                    ready_time_mid = time.time()
                    # DM channels
                    for dm in data["private_channels"]:
                        self.add_dm(dm, data)
                        if "last_message_id" in dm:
                            last_messages.append({
                                "message_id": dm["last_message_id"],   # really last message id
                                "channel_id": dm["id"],
                            })
                    self.dms = sorted(self.dms, key=lambda x: x["last_message_id"], reverse=True)
                    self.dms = sorted(self.dms, key=lambda x: x["last_message_id"] == 0)
                    for dm in self.dms:   # dont need it anymore
                        dm.pop("last_message_id")
                    for dm in self.dms:
                        self.dms_id.append(dm["id"])
                    time_log_string += f"    DMs - {round((time.time() - ready_time_mid) * 1000, 3)}ms\n"
                    ready_time_mid = time.time()
                    # unread messages and pings
                    read_state = []
                    msg_ping = []
                    for channel in double_get(data, "read_state", "entries", default=[]):
                        # last_message_id in unread_state is actually last_ACKED_message_id
                        if "last_message_id" in channel and "mention_count" in channel:
                            read_state.append((channel["id"], channel["last_message_id"]))
                            if channel["mention_count"]:
                                msg_ping.append(channel["id"])
                    for channel_id, last_acked in read_state:   # add relevant data
                        for last_message in last_messages:
                            if last_message["channel_id"] == channel_id:
                                last_message_id = last_message["message_id"]
                                break
                        else:
                            continue
                        unseen_channel = {
                            "last_message_id": last_message_id,
                            "last_acked_message_id": last_acked or 0,   # dont allow it to be None
                            "mentions": ["True"] if channel_id in msg_ping else [],   # message_id is unknown
                        }
                        if not last_message_id or int(unseen_channel["last_acked_message_id"]) < int(last_message_id):
                            unseen_channel["last_acked_unreads_line"] = unseen_channel["last_acked_message_id"]
                        self.read_state[channel_id] = unseen_channel
                    time_log_string += f"    read state ({len(self.read_state)} channels) - {round((time.time() - ready_time_mid) * 1000, 3)}ms\n"
                    ready_time_mid = time.time()
                    # guild and dm settings
                    for guild in double_get(data, "user_guild_settings", "entries", default=[]):
                        if guild["guild_id"]:
                            # find this guild in self.guilds
                            for guild_num, guild_g in enumerate(self.guilds):
                                if guild_g["guild_id"] == guild["guild_id"]:
                                    break
                            else:
                                continue
                            self.guilds[guild_num].update({
                                "suppress_everyone": guild["suppress_everyone"],
                                "suppress_roles": guild["suppress_roles"],
                                "message_notifications": guild["message_notifications"],
                                "muted": guild["muted"],
                            })
                            guild_flags = int(guild.get("flags", 0))
                            # opt_in_channels means: show all guild channels - when guild is joined
                            opt_in_channels = not perms.decode_flag(guild_flags, 14) or perms.decode_flag(guild_flags, 13)
                            self.guilds[guild_num]["opt_in_channels"] = opt_in_channels
                            self.process_one_channel_overrides(guild["channel_overrides"], guild_num, guild["message_notifications"])
                        else:
                            for dm in guild["channel_overrides"]:
                                for dm_num, dm_g in enumerate(self.dms):
                                    if dm_g["id"] == dm["channel_id"]:
                                        break
                                self.dms[dm_num].update({
                                    "message_notifications": dm["message_notifications"],
                                    "muted": dm["muted"],
                                })
                    self.process_hidden_channels()
                    self.guilds_changed = True
                    time_log_string += f"    channel settings - {round((time.time() - ready_time_mid) * 1000, 3)}ms\n"
                    ready_time_mid = time.time()
                    for user in data["relationships"]:
                        if user["type"] == 2 or user.get("user_ignored"):
                            self.blocked.append(user["id"])
                    time_log_string += f"    blocked users - {round((time.time() - ready_time_mid) * 1000, 3)}ms\n"
                    ready_time_mid = time.time()
                    # get user settings
                    if "user_settings_proto" in data and not self.legacy:
                        decoded = PreloadedUserSettings.FromString(base64.b64decode(data["user_settings_proto"]))
                        self.user_settings_proto = MessageToDict(decoded)
                    else:
                        self.legacy = True
                        old_user_settings = data["user_settings"]
                        old_user_settings.update({
                            "status": {
                                "status": old_user_settings.get("status", "online"),
                                "guildFolders": {
                                    "guildPositions": old_user_settings.get("guild_positions"),
                                },
                            },
                        })
                        self.user_settings_proto = old_user_settings
                        if old_user_settings.get("custom_status"):
                            self.user_settings_proto["status"]["customStatus"] = old_user_settings["custom_status"]
                    self.proto_changed = True
                    time_log_string += f"    protobuf - {round((time.time() - ready_time_mid) * 1000, 3)}ms\n"
                    ready_time_mid = time.time()
                    # get my roles
                    if self.guilds:
                        for num, guild in enumerate(data["merged_members"]):
                            guild_id = self.guilds[num]["guild_id"]
                            roles = []
                            for member in guild:
                                if member.get("user_id") == self.my_id or member.get("id") == self.my_id:   # spacebar_fix - user_id -> id
                                    roles = member["roles"]
                            self.my_roles.append({
                                "guild_id": guild_id,
                                "roles": roles,
                            })
                    time_log_string += f"    roles - {round((time.time() - ready_time_mid) * 1000, 3)}ms\n"
                    ready_time_mid = time.time()
                    # write debug data
                    if logger.getEffectiveLevel() == logging.DEBUG:
                        debug.save_json(debug.anonymize_guilds(self.guilds), "guilds.json")
                    # blocked users
                    time_log_string += f"    debug data - {round((time.time() - ready_time_mid) * 1000, 3)}ms\n"
                    self.ready = True
                    time_log_string += f"    total - {round((time.time() - ready_time_start) * 1000, 3)}ms"
                    logger.debug(time_log_string)
                    # READY is huge so lets save some memory
                    del (response, data, guild, guild_channels, role, guild_roles, last_messages, time_log_string)
                    data = None
                    gc.collect()

                elif optext == "READY_SUPPLEMENTAL":
                    for guild in data["merged_presences"]["guilds"]:
                        for user in guild:
                            custom_status = None
                            activities = []
                            for activity in user["activities"]:
                                if activity["type"] == 4:
                                    custom_status = activity.get("state", "")
                                elif activity["type"] in (0, 2):
                                    assets = activity.get("assets", {})
                                    activities.append({
                                        "type": activity["type"],
                                        "name": activity["name"],
                                        "state": activity.get("state"),
                                        "details": activity.get("details"),
                                        "small_text": assets.get("small_text"),
                                        "large_text": assets.get("large_text"),
                                    })
                            self.dm_activities.append({
                                "id": user["user_id"],
                                "status": user["status"],
                                "custom_status": custom_status,
                                "activities": activities,
                            })
                    else:
                        guild = {}
                    for user in data["merged_presences"]["friends"]:
                        custom_status = None
                        activities = []
                        for activity in user["activities"]:
                            if activity["type"] == 4:
                                custom_status = activity.get("state")
                            elif activity["type"] in (0, 2):
                                assets = activity.get("assets", {})
                                activities.append({
                                    "type": activity["type"],
                                    "name": activity["name"],
                                    "state": activity.get("state"),
                                    "details": activity.get("details"),
                                    "small_text": assets.get("small_text"),
                                    "large_text": assets.get("large_text"),
                                })
                        self.dm_activities.append({
                            "id": user["user_id"],
                            "status": user["status"],
                            "custom_status": custom_status,
                            "activities": activities,
                        })
                    self.dm_activities_changed = True
                    del (guild)   # this is large dict so lets save some memory
                    gc.collect()

                elif optext == "SESSIONS_REPLACE":
                    # received when new client is connected
                    activities = []
                    for activity in data[0]["activities"]:
                        if activity["type"] in (0, 2):
                            if "assets" in activity:
                                small_text = activity["assets"].get("small_text")
                                large_text = activity["assets"].get("large_text")
                            else:
                                small_text = None
                                large_text = None
                            activities.append({
                                "type": activity["type"],
                                "name": activity["name"],
                                "state": activity.get("state", ""),
                                "details": activity.get("details", ""),
                                "small_text": small_text,
                                "large_text": large_text,
                            })
                    self.my_status = {
                        "activities": activities,
                    }
                    self.status_changed = True

                elif optext == "PRESENCE_UPDATE":
                    # received when friend/DM user changes presence state (online/rich/custom)
                    user_id = data["user"]["id"]
                    custom_status = None
                    activities = []
                    for activity in data.get("activities", []):
                        if activity["type"] == 4:
                            custom_status = activity.get("state")
                        elif activity["type"] in (0, 2):
                            if "assets" in activity:
                                small_text =  activity["assets"].get("small_text")
                                large_text =  activity["assets"].get("large_text")
                            else:
                                small_text = None
                                large_text = None
                            activities.append({
                                "type": activity["type"],
                                "name": activity["name"],
                                "state": activity.get( "state"),
                                "details": activity.get("details"),
                                "small_text": small_text,
                                "large_text": large_text,
                            })
                    # select what list of activities to update
                    if "guild_id" in data:
                        guild_id = data["guild_id"]
                        for guild_activities in self.subscribed_activities:
                            if guild_activities["guild_id"] == guild_id:
                                selected_activities = guild_activities["members"]
                                break
                        else:
                            self.subscribed_activities.append({
                                "guild_id": guild_id,
                                "members": [],
                            })
                            selected_activities = self.subscribed_activities[-1]["members"]
                        self.subscribed_activities_changed.append(guild_id)
                    else:
                        selected_activities = self.dm_activities
                    for num, user in enumerate(selected_activities):
                        if user["id"] == user_id:
                            selected_activities[num] = {
                                "id": user_id,
                                "status": data.get("status"),   # spacebar_fix - status
                                "custom_status": custom_status,
                                "activities": activities,
                            }
                            break
                    else:
                        selected_activities.append({
                            "id": data["user"]["id"],
                            "status": data.get("status"),   # spacebar_fix - get
                            "custom_status": custom_status,
                            "activities": activities,
                        })
                    self.dm_activities_changed = True

                elif optext == "TYPING_START":
                    # received when user in currently subscribed guild channel starts typing
                    if "member" in data:
                        username = data["member"]["user"]["username"]
                        global_name = data["member"]["user"].get("global_name")   # spacebar_fix - get
                        nick = data["member"]["user"].get("nick")
                    else:
                        username = None
                        global_name = None
                        nick = None
                    self.typing_buffer.append({
                        "user_id": data["user_id"],
                        "timestamp": data["timestamp"],
                        "channel_id": data["channel_id"],
                        "username": username,
                        "global_name": global_name,
                        "nick": nick,
                    })

                elif optext == "MESSAGE_CREATE" and "content" in response["d"]:
                    message = response["d"]
                    # saving roles to cache
                    if  message["channel_id"] in self.subscribed_channels and "member" in message and "roles" in message["member"]:
                        self.add_member_roles(
                            message.get("guild_id"),
                            message["author"]["id"],
                            message["member"]["roles"],
                            nick=message["member"].get("nick"),
                            nonce=message["channel_id"],
                        )
                    message_done = prepare_message(message)
                    message_done.update({
                        "channel_id": message["channel_id"],
                        "guild_id": message.get("guild_id"),
                    })
                    if message_done["user_id"] == self.my_id:
                        message_done.update({
                            "nonce": message.get("nonce"),
                        })
                    self.messages_buffer.append({
                        "op": "MESSAGE_CREATE",
                        "d": message_done,
                    })

                elif optext == "MESSAGE_UPDATE":
                    message = response["d"]
                    message_done = prepare_message(message)
                    message_done.update({
                        "channel_id": message["channel_id"],
                        "guild_id": message.get("guild_id"),
                    })
                    self.messages_buffer.append({
                        "op": "MESSAGE_UPDATE",
                        "d": message_done,
                    })

                elif optext == "MESSAGE_DELETE":
                    ready_data = {
                        "id": data["id"],
                        "channel_id": data["channel_id"],
                        "guild_id": data.get("guild_id"),
                    }
                    self.messages_buffer.append({
                        "op": "MESSAGE_DELETE",
                        "d": ready_data,
                    })

                elif optext == "MESSAGE_REACTION_ADD":
                    if "member" in data and "user" in data["member"]:   # spacebar_fix - "user" is mising
                        user_id = data["member"]["user"]["id"]
                        username = data["member"]["user"]["username"]
                        global_name = data["member"]["user"].get("global_name")   # spacebar_fix - get
                        nick = data["member"]["user"].get("nick")
                    else:
                        user_id = data["user_id"]
                        username = None
                        global_name = None
                        nick = None
                    ready_data = {
                        "id": data["message_id"],
                        "channel_id": data["channel_id"],
                        "guild_id": data.get("guild_id"),
                        "emoji": data["emoji"]["name"],
                        "emoji_id": data["emoji"].get("id"),   # spacebar_fix - get
                        "user_id": user_id,
                        "username": username,
                        "global_name": global_name,
                        "nick": nick,
                    }
                    self.messages_buffer.append({
                        "op": "MESSAGE_REACTION_ADD",
                        "d": ready_data,
                    })

                elif optext == "MESSAGE_REACTION_ADD_MANY":
                    channel_id = data["channel_id"]
                    guild_id = data.get("guild_id")
                    message_id = data["message_id"]
                    for reaction in data["reactions"]:
                        for user_id in reaction["users"]:
                            ready_data = {
                                "id": message_id,
                                "channel_id": channel_id,
                                "guild_id": guild_id,
                                "emoji": reaction["emoji"]["name"],
                                "emoji_id": reaction["emoji"]["id"],
                                "user_id": user_id,
                                "username": None,
                                "global_name": None,
                                "nick": None,
                            }
                            self.messages_buffer.append({
                                "op": "MESSAGE_REACTION_ADD",
                                "d": ready_data,
                            })

                elif optext == "MESSAGE_REACTION_REMOVE":
                    ready_data = {
                        "id": data["message_id"],
                        "channel_id": data["channel_id"],
                        "guild_id": data.get("guild_id"),
                        "emoji": data["emoji"]["name"],
                        "emoji_id": data["emoji"].get("id"),   # spacebar_fix - get
                        "user_id": data["user_id"],
                    }
                    self.messages_buffer.append({
                        "op": "MESSAGE_REACTION_REMOVE",
                        "d": ready_data,
                    })

                elif self.want_summaries and optext == "CONVERSATION_SUMMARY_UPDATE":
                    # received when new conversation summary is generated
                    for summary in data["summaries"]:
                        if summary["type"] == 3:
                            self.summaries_buffer.append({
                                "message_id": summary["start_id"],
                                "channel_id": data["channel_id"],
                                "guild_id": data.get("guild_id"),
                                "topic": summary["topic"],
                                "description": summary["summ_short"],
                            })
                        else:
                            logger.warning(f"Unhandled summary type\n{json.dumps(summary)}")

                elif optext == "MESSAGE_ACK":
                    # received when other client ACKs messages

                    self.msg_ack_buffer.append({
                        "message_id": data["message_id"],
                        "channel_id": data["channel_id"],
                    })

                elif optext == "GUILD_MEMBERS_CHUNK":
                    # received when requesting members (op 8)
                    if self.querying_members:
                        self.querying_members = False
                        self.member_query_results = []
                        for member in data["members"]:
                            name = member.get("nick")
                            if not name:
                                name = member["user"].get("global_name", member["user"]["username"])   # spacebar_fix - get
                            self.member_query_results.append({
                                "id": member["user"]["id"],
                                "username": member["user"]["username"],
                                "name": name,
                            })
                    else:
                        guild_id = data["guild_id"]
                        for member in data["members"]:
                            if "roles" in member and "roles" in member:
                                self.add_member_roles(
                                    guild_id,
                                    member["user"]["id"],
                                    member["roles"],
                                    nick=member.get("nick"),
                                )
                                if data.get("nonce"):
                                    self.roles_changed = data["nonce"]

                elif optext == "THREAD_LIST_SYNC":
                    threads = []
                    guild_id = None
                    for thread in response["d"]["threads"]:
                        if not guild_id:
                            guild_id = thread["guild_id"]   # assuming its one event per thread
                        threads.append({
                            "id": thread["id"],
                            "type": thread["type"],
                            "owner_id": thread["owner_id"],
                            "name": thread["name"],
                            "locked": thread["thread_metadata"]["locked"],
                            "message_count": thread["message_count"],
                            "timestamp": thread["thread_metadata"].get("create_timestamp", None),
                            "parent_id": thread["parent_id"],
                            "suppress_everyone": False,   # no config for threads
                            "suppress_roles": False,
                            "message_notifications": None,
                            "muted": False,   # muted and joined are in READY event
                            "joined": False,
                        })
                    self.threads_buffer.append({
                        "op": "THREAD_UPDATE",
                        "guild_id": guild_id,
                        "threads": threads,
                    })

                elif self.want_member_list and optext == "GUILD_MEMBER_LIST_UPDATE":
                    guild_id = data["guild_id"]
                    list_id = data["id"]
                    for guild_index, guild in enumerate(self.activities):
                        if guild[0] == guild_id:
                            break
                    else:
                        self.activities.append([guild_id, {}])   # [guild_id, member_lists]
                        guild_index = -1
                    for memlist in data["ops"]:
                        # keeping only necessary data, because the rest can be fetched with discord.get_user_guild()
                        if memlist["op"] == "SYNC":
                            if memlist["range"][0] != 0:
                                # keeping only first chunk (first 99)
                                continue
                            members_sync = []
                            for item in memlist["items"]:
                                if "group" in item:
                                    members_sync.append({"group": item["group"]["id"]})
                                else:
                                    member_data = item["member"]
                                    ## unused for now
                                    # custom_status = None
                                    # activities = []
                                    # for activity in member_data["presence"]["activities"]:
                                    #     if activity["type"] == 4:
                                    #         custom_status = activity.get("state", "")
                                    #     elif activity["type"] in (0, 2):
                                    #         assets = activity.get("assets", {})
                                    #         activities.append({
                                    #             "type": activity["type"],
                                    #             "name": activity["name"],
                                    #             "state": activity.get("state"),
                                    #             "details": activity.get("details"),
                                    #             "small_text": assets.get("small_text"),
                                    #             "large_text": assets.get("large_text"),
                                    #         })
                                    members_sync.append({
                                        "id": member_data["user"]["id"],
                                        "username": member_data["user"]["username"],
                                        "global_name": member_data["user"].get("global_name"),   # spacebar_fix - get
                                        "nick": member_data["nick"],
                                        "roles": member_data["roles"],
                                        "status": member_data["presence"]["status"],
                                        # "custom_status": custom_status,
                                        # "activities": activities,
                                    })
                            self.activities[guild_index][1][list_id] = [0, members_sync]
                            self.activities_changed.append(guild_id)
                        elif memlist["op"] == "DELETE":
                            try:
                                del self.activities[guild_index][1][list_id][1][memlist["index"]]
                            except (IndexError, NameError):
                                pass
                        elif memlist["op"] in ("UPDATE", "INSERT"):
                            custom_status = None
                            if list_id not in self.activities[guild_index][1]:
                                self.activities[guild_index][1][list_id] = [0, []]   # [last_index, members]
                            if "group" in memlist["item"]:
                                # group can only be inserted
                                self.activities[guild_index][1][list_id][1].insert(memlist["index"], {"group": memlist["item"]["group"]["id"]})
                                if len(self.activities[guild_index][1][list_id][1]) > 100:
                                    self.activities[guild_index][1][list_id][1].pop(-1)
                                self.activities_changed.append(guild_id)
                                self.activities[guild_index][1][list_id][0] = int(memlist["index"])
                                continue
                            member_data = memlist["item"]["member"]
                            activities = []
                            for activity in member_data["presence"]["activities"]:
                                if activity["type"] == 4:
                                    custom_status = activity.get("state", "")
                                elif activity["type"] in (0, 2):
                                    assets = activity.get("assets", {})
                                    activities.append({
                                        "type": activity["type"],
                                        "name": activity["name"],
                                        "state": activity.get("state"),
                                        "details": activity.get("details"),
                                        "small_text": assets.get("small_text"),
                                        "large_text": assets.get("large_text"),
                                    })
                            member_id = member_data["user"]["id"]
                            ready_data = {
                                "id": member_id,
                                "username": member_data["user"]["username"],
                                "global_name": member_data["user"].get("global_name"),   # spacebar_fix - get
                                "nick": member_data["nick"],
                                "roles": member_data["roles"],
                                "status": member_data["presence"]["status"],
                                # "custom_status": custom_status,
                                # "activities": activities,
                            }
                            if memlist["op"] == "UPDATE":
                                try:
                                    if self.activities[guild_index][1][list_id][1][memlist["index"]].get("id") == member_id:
                                        self.activities[guild_index][1][list_id][1][memlist["index"]].update(ready_data)
                                    else:   # failsafe
                                        for num, member in enumerate(self.activities[guild_index][1][list_id]["members"]):
                                            if member.get("id") == member_id:
                                                self.activities[guild_index][1][list_id][1][num].update(ready_data)
                                except IndexError:
                                    pass
                            else:   # INSERT
                                self.activities[guild_index][1][list_id][1].insert(memlist["index"], ready_data)
                                if len(self.activities[guild_index][1][list_id][1]) > 100:   # lets have some limits
                                    self.activities[guild_index][1][list_id][1].pop(-1)
                            self.activities[guild_index][1][list_id][0] = int(memlist["index"])
                        self.activities_changed.append(guild_id)

                elif optext == "USER_SETTINGS_PROTO_UPDATE":
                    if data["partial"] or data["settings"]["type"] != 1:
                        continue
                    decoded = PreloadedUserSettings.FromString(base64.b64decode(data["settings"]["proto"]))
                    self.user_settings_proto = MessageToDict(decoded)
                    self.proto_changed = True

                elif optext == "USER_GUILD_SETTINGS_UPDATE":
                    if data["guild_id"]:   # guild and channel
                        for guild_num_search, guild_g in enumerate(self.guilds):
                            if guild_g["guild_id"] == data["guild_id"]:
                                guild_g.pop("suppress_everyone", None)   # reset to default
                                guild_g.pop("suppress_roles", None)
                                guild_g.pop("message_notifications", None)
                                guild_g.pop("muted", None)
                                guild_num = guild_num_search
                                break
                        else:
                            continue
                        guild_flags = int(data.get("flags", 0))
                        # opt_in_channels means: show all guild channels - when guild is joined
                        opt_in_channels = not perms.decode_flag(guild_flags, 14) or perms.decode_flag(guild_flags, 13)
                        self.guilds[guild_num].update({
                            "suppress_everyone": data["suppress_everyone"],
                            "suppress_roles": data["suppress_roles"],
                            "message_notifications": data["message_notifications"],
                            "muted": data["muted"],
                            "opt_in_channels": opt_in_channels,
                        })
                        # reset all to defaults
                        for channel_num, channel in enumerate(self.guilds[guild_num]["channels"]):
                            if channel["type"] in (0, 2, 4, 5, 15):
                                flags = int(channel.get("flags", 0))
                                hidden = not perms.decode_flag(flags, 12)   # manually hidden
                            else:
                                hidden = False
                            self.guilds[guild_num]["channels"][channel_num]["hidden"] = hidden
                            self.guilds[guild_num]["channels"][channel_num]["muted"] = False
                            self.guilds[guild_num]["channels"][channel_num]["message_notifications"] = 3
                        self.process_one_channel_overrides(data["channel_overrides"], guild_num, data["message_notifications"])
                        self.process_hidden_channels()
                    else:   # dm
                        for dm_g in self.dms:
                            dm_g.pop("message_notifications", None)   # reset to default
                            dm_g.pop("muted", None)
                        for dm in data["channel_overrides"]:
                            for dm_num, dm_g in enumerate(self.dms):
                                if dm_g["id"] == dm["channel_id"]:
                                    break
                            else:
                                continue
                            self.dms[dm_num].update({
                                "message_notifications": dm["message_notifications"],
                                "muted": dm["muted"],
                            })
                    self.guilds_changed = True

                elif optext == "USER_UPDATE":
                    self.set_my_user_data(data)
                    self.my_id = data["id"]
                    self.premium = data.get("premium_type")
                    self.user_update = (self.my_user_data, None)

                elif optext == "GUILD_MEMBER_UPDATE":
                    if data["user"]["id"] == self.my_id:
                        nick = data.get("nick")
                        roles_changed = None
                        for num, guild in enumerate(self.my_roles):
                            if guild["guild_id"] == data["guild_id"]:
                                self.my_roles[num]["roles"] = data["roles"]
                                roles_changed = data["guild_id"]
                                break
                        self.user_update = ({
                            "id": data["user"]["id"],
                            "nick": nick,
                        }, roles_changed)

                elif optext == "APPLICATION_COMMAND_AUTOCOMPLETE_RESPONSE":
                    self.app_command_autocomplete_resp = response["d"]["choices"]

                elif optext in ("MESSAGE_POLL_VOTE_ADD", "MESSAGE_POLL_VOTE_REMOVE"):
                    data["id"] = data.pop("message_id")
                    self.messages_buffer.append({
                        "op": optext,
                        "d": data,
                    })

                elif optext == "VOICE_STATE_UPDATE":
                    if "session_id" not in self.voice_gateway_data and self.voice_gateway_data_ready >= 1:
                        self.voice_gateway_data["session_id"] = data["session_id"]
                        self.voice_gateway_data["guild_id"] = data.get("guild_id")
                        if not self.voice_gateway_data["guild_id"]:
                            self.voice_gateway_data["guild_id"] = data["channel_id"]   # must be channel_id in DM
                        self.voice_gateway_data["channel_id"] = data["channel_id"]
                        self.voice_gateway_data_ready += 1
                    elif data["user_id"] != self.my_id:
                        name = None
                        if "member" in data:
                            name = data["member"].get("nick")
                            if not name:
                                name = data["member"]["user"].get("global_name", data["member"]["user"]["username"])   # spacebar_fix - get
                        self.call_buffer.append({
                            "op": "STATE_UPDATE",
                            "channel_id": data["channel_id"],
                            "user_id": data["user_id"],
                            "name": name,
                            "muted": data["self_mute"] or data["mute"],
                        })
                        # this is just to get mute states, enter/leave call and speaking are sent in voice gateway

                elif optext == "VOICE_SERVER_UPDATE":
                    if "endpoint" not in self.voice_gateway_data and self.voice_gateway_data_ready >= 1:
                        self.voice_gateway_data["token"] = data["token"]
                        self.voice_gateway_data["endpoint"] = data["endpoint"]
                        self.voice_gateway_data_ready += 1

                elif optext == "CALL_CREATE":
                    # event is received even when this client creates call
                    if not data["voice_states"] or data["voice_states"][0]["user_id"] != self.my_id:
                        self.call_buffer.append({
                            "op": "CALL_CREATE",
                            "channel_id": data["channel_id"],
                            "ringing": self.my_id in data["ringing"],
                        })

                elif optext == "CALL_UPDATE":
                    self.call_buffer.append({
                        "op": "CALL_UPDATE",
                        "channel_id": data["channel_id"],
                        "ringing": self.my_id in data["ringing"],
                    })

                elif optext == "CALL_DELETE":
                    self.call_buffer.append({
                        "op": "CALL_DELETE",
                        "channel_id": data["channel_id"],
                    })

                elif optext in ("THREAD_UPDATE", "THREAD_CREATE"):
                    self.threads_buffer.append({
                        "op": "THREAD_UPDATE",
                        "guild_id": data["guild_id"],
                        "threads": [{
                            "id": data["id"],
                            "type": data["type"],
                            "owner_id": data["owner_id"],
                            "name": data["name"],
                            "locked": data["thread_metadata"]["locked"],
                            "message_count": data["message_count"],
                            "timestamp": data["thread_metadata"]["create_timestamp"],
                            "parent_id": data["parent_id"],
                            "suppress_everyone": False,   # no config for threads
                            "suppress_roles": False,
                            "message_notifications": None,
                            "muted": False,
                            "joined": False,
                        }],
                    })

                elif optext == "THREAD_DELETE":
                    self.threads_buffer.append({
                        "op": "THRRAD_DELETE",
                        "guild_id": data["guild_id"],
                        "threads": [{
                            "id": data["id"],
                            "parent_id": data["parent_id"],
                        }],
                    })

                elif optext in ("CHANNEL_CREATE", "CHANNEL_UPDATE", "CHANNEL_DELETE"):
                    new_channel = response["d"]
                    channel_id = new_channel["id"]
                    guild_id = new_channel.get("guild_id")
                    if not guild_id:   # DMs
                        channel_id = new_channel["id"]
                        if optext == "CHANNEL_DELETE":
                            for num, dm in enumerate(self.dms):
                                if dm["id"] == channel_id:
                                    self.dms.pop(num)
                                    break
                        else:
                            self.add_dm(new_channel)
                        self.dms_id = []
                        for dm in self.dms:
                            self.dms_id.append(dm["id"])
                        self.guilds_changed = True
                        continue

                    if optext == "CHANNEL_DELETE":
                        for num, guild in enumerate(self.guilds):
                            if guild["guild_id"] == guild_id:
                                for num_ch, channel in enumerate(guild["channels"]):
                                    if channel["id"] == channel_id:
                                        self.guilds[num]["channels"].pop(num_ch)
                                        break
                                break
                    else:
                        for num, guild in enumerate(self.guilds):
                            if guild["guild_id"] == guild_id:
                                for num_ch, channel in enumerate(guild["channels"]):
                                    if channel["id"] == channel_id:
                                        break
                                else:
                                    self.guilds[num]["channels"].append({})
                                    num_ch += 1
                                break
                        else:
                            continue
                        ready_data = {
                            "id": new_channel["id"],
                            "type": new_channel["type"],
                            "name": new_channel["name"],
                            "topic": new_channel.get("topic"),
                            "parent_id": new_channel.get("parent_id"),
                            "position": new_channel["position"],
                            "permission_overwrites": new_channel["permission_overwrites"],
                            "hidden": False,
                        }
                        if new_channel.get("rate_limit_per_user"):
                            ready_data["rate_limit"] = new_channel["rate_limit_per_user"]
                        self.guilds[num]["channels"][num_ch] = ready_data
                    self.guilds_changed = True

                elif optext in ("GUILD_CREATE", "GUILD_UPDATE", "GUILD_DELETE"):
                    guild_id = data["id"]
                    if optext == "GUILD_CREATE":
                        have_it = False
                        for guild in self.guilds:
                            if guild["guild_id"] == guild_id:
                                have_it = True
                                break
                        if have_it:
                            continue
                        self.add_guild(data)
                        # add my roles
                        for member in data.get("members", []):
                            if member.get("user_id") == self.my_id or member.get("id") or member["user"]["id"] == self.my_id:
                                self.my_roles.append({
                                    "guild_id": guild_id,
                                    "roles": member["roles"],
                                })
                                break
                        self.guilds_changed = True
                    elif optext == "GUILD_UPDATE":
                        for num, guild in enumerate(self.guilds):
                            community = False
                            for feature in data["features"]:
                                if feature in ("COMMUNITY", "COMMUNITY_CANARY"):
                                    community = True
                                    break
                            if guild["guild_id"] == guild_id:
                                self.guilds[num]["owned"] = self.my_id == data["owner_id"]
                                self.guilds[num]["name"] = data["name"]
                                self.guilds[num]["description"] = data["description"]
                                self.guilds[num]["community"] = community
                                self.guilds[num]["premium"] = data["premium_tier"]
                                self.guilds_changed = True
                    elif optext == "GUILD_DELETE":
                        for num, guild in enumerate(self.guilds):
                            if guild["guild_id"] == guild_id:
                                self.guilds.pop(num)
                                self.guilds_changed = True
                                break

                elif optext in ("GUILD_ROLE_CREATE", "GUILD_ROLE_UPDATE", "GUILD_ROLE_DELETE"):
                    guild_id = data["guild_id"]
                    for num_guild, guild in enumerate(self.roles):
                        if guild["guild_id"] == guild_id:
                            break
                    else:
                        continue
                    if optext == "GUILD_ROLE_CREATE":
                        role = data["role"]
                        self.roles[num_guild]["roles"].append({
                            "id": role["id"],
                            "name": role["name"],
                            "color": role["color"],
                            "position": role["position"],
                            "hoist": role["hoist"],
                            "permissions": role["permissions"],
                        })
                        # sort roles
                        self.roles[num_guild]["roles"] = sorted(self.roles[num_guild]["roles"], key=lambda x: x.get("position"), reverse=True)
                        self.roles[num_guild]["roles"] = sorted(self.roles[num_guild]["roles"], key=lambda x: not bool(x.get("color")))
                        if not self.user_update:
                            self.user_update = (None, None)
                        self.guild_roles_changed = (guild_id, role["id"])
                    elif optext == "GUILD_ROLE_UPDATE":
                        role = data["role"]
                        for num, role_old in enumerate(self.roles[num_guild]["roles"]):
                            if role["id"] == role_old["id"]:
                                self.roles[num_guild]["roles"][num] = {
                                    "id": role["id"],
                                    "name": role["name"],
                                    "color": role["color"],
                                    "position": role["position"],
                                    "hoist": role["hoist"],
                                    "permissions": role["permissions"],
                                }
                                # sort roles
                                self.roles[num_guild]["roles"] = sorted(self.roles[num_guild]["roles"], key=lambda x: x.get("position"), reverse=True)
                                self.roles[num_guild]["roles"] = sorted(self.roles[num_guild]["roles"], key=lambda x: not bool(x.get("color")))
                                # update default role
                                if role["id"] == guild_id:
                                    for num_g, guild in enumerate(self.guilds):
                                        if guild["guild_id"] == guild_id:
                                            self.guilds[num_g]["permissions"] = role["permissions"]
                                            break
                                if not self.user_update:
                                    self.user_update = (None, None)
                                self.guild_roles_changed = (guild_id, role["id"])
                                break
                    elif optext == "GUILD_ROLE_DELETE":
                        for num, role in enumerate(self.roles[num_guild]["roles"]):
                            if role["id"] == data["role_id"]:
                                self.roles[num_guild]["roles"].pop(num)
                                if not self.user_update:
                                    self.user_update = (None, None)
                                self.guild_roles_changed = (guild_id, role["id"])
                                break

                self.execute_extensions_method_nochain("on_gateway_event", data, cache=True)

            elif opcode == 7:
                logger.info("Host requested reconnect")
                self.resumable = True
                break

            elif opcode == 9:
                if response["d"]:
                    logger.info("Session invalidated, reconnecting")
                    break

        self.state = 0
        logger.debug("Receiver stopped")
        self.reconnect_requested = True
        self.heartbeat_running = False


    def send_heartbeat(self):
        """Send heartbeat to gateway, if response is not received, triggers reconnect, should be run in a thread"""
        logger.debug(f"Heartbeater started, interval={self.heartbeat_interval/1000}s")
        self.heartbeat_running = True
        self.heartbeat_received = True
        # wait for ready event for some time
        sleep_time = 0
        while not self.ready:
            if sleep_time >= self.heartbeat_interval / 100:
                logger.error("Ready event could not be processed in time, probably because of too many servers. Exiting...")
                raise SystemExit("Ready event could not be processed in time, probably because of too many servers. Exiting...")
            time.sleep(0.5)
            sleep_time += 5
        heartbeat_interval_rand = int(self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000)
        heartbeat_sent_time = int(time.time())
        time_spent_event_time = int(time.time()) - 1990   # send it 10s after start, then every 30min
        while self.run and not self.wait and self.heartbeat_running:
            send_time_spent_event = not self.legacy and int(time.time()) - time_spent_event_time >= 1800
            if send_time_spent_event:
                self.send({
                    "op": 41,
                    "d": {
                        "initialization_timestamp": self.init_time,
                        "session_id": self.client_prop["client_heartbeat_session_id"],
                        "client_launch_id": self.client_prop["client_launch_id"],
                    },
                })
                logger.debug("Sent Time Spent event")
                time_spent_event_time = int(time.time())
            if time.time() - heartbeat_sent_time >= heartbeat_interval_rand or send_time_spent_event:
                if QOS_HEARTBEAT and not self.legacy:
                    self.send({
                        "op": 1,
                        "d": {
                            "seq": self.sequence,
                            "qos": QOS_PAYLOAD,
                        },
                    })
                else:
                    self.send({"op": 1, "d": self.sequence})
                heartbeat_sent_time = int(time.time())
                logger.debug("Sent heartbeat")
                if not self.heartbeat_received:
                    logger.warning("Heartbeat reply not received")
                    self.resumable = True
                    break
                self.heartbeat_received = False
                heartbeat_interval_rand = int(self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000)
            # sleep(heartbeat_interval * jitter), but jitter is limited to (0.1 - 0.9)
            # in this time heartbeat ack should be received from discord
            time.sleep(1)
        self.state = 0
        logger.debug("Heartbeater stopped")
        self.reconnect_requested = True


    def authenticate(self):
        """Authenticate client with discord gateway"""
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "capabilities": self.capabilities or DEFAULT_CAPABILITIES,
                "properties": self.client_prop,
                "presence": {
                    "activities": [],
                    "status": "online",
                    "since": None,
                    "afk": False,
                },
            },
        }
        if self.token.startswith("Bot"):
            payload["d"].pop("capabilities")
            payload["d"]["intents"] = self.capabilities or DEFAULT_INTENTS
        self.send(payload)


    def resume(self):
        """
        Try to resume discord gateway session on url provided by Discord in READY event.
        Return gateway response code, 9 means resumming has failed
        """
        self.ws.close(timeout=0)   # this will stop receiver
        time.sleep(1)   # so receiver ends before opening new socket
        reset_inflator()   # otherwise decompression wont work
        self.ws = websocket.WebSocket()
        try:
            self.connect_ws(resume=True)
        except websocket._exceptions.WebSocketBadStatusException:
            logger.info("Failed to resume connection")
            return 9
        _ = zlib_decompress(self.ws.recv())
        payload = {"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}
        self.send(payload)
        try:
            op = json.loads(zlib_decompress(self.ws.recv()))["op"]
            logger.debug(f"Connection resumed with code {op}")
            return op or True
        except json.JSONDecodeError:
            logger.info("Failed to resume connection")
            return 9


    def reconnect(self):
        """Try to resume session, if cant, create new one"""
        if not self.wait:
            self.state = 2
            logger.info("Trying to reconnect")
        try:
            code = None
            if self.resumable:
                self.resumable = False
                code = self.resume()
            if code == 9 or code is None:
                logger.debug("Restarting connection")
                self.ws.close(timeout=0)   # this will stop receiver
                time.sleep(1)   # so receiver ends before opening new socket
                reset_inflator()   # otherwise decompression wont work
                self.ready = False   # will receive new ready event
                self.ws = websocket.WebSocket()
                self.connect_ws()
                self.authenticate()
            self.wait = False
            # restarting threads
            if not self.receiver_thread.is_alive():
                self.receiver_thread = threading.Thread(target=self.safe_function_wrapper, daemon=True, args=(self.receiver, ))
                self.receiver_thread.start()
            if not self.heartbeat_thread.is_alive():
                self.heartbeat_thread = threading.Thread(target=self.send_heartbeat, daemon=True)
                self.heartbeat_thread.start()
            self.state = 1
            logger.info("Connection established")
        except websocket._exceptions.WebSocketAddressException:
            if not self.wait:   # if not running from wait_oline
                logger.warning("No internet connection")
                self.ws.close()
                threading.Thread(target=self.wait_online, daemon=True, args=()).start()


    def wait_online(self):
        """Wait for network, try to reconnect every 5s"""
        self.wait = True
        while self.run and self.wait:
            self.reconnect_requested = True
            time.sleep(5)


    def get_state(self):
        """
        Return current state of gateway:
        0 - gateway is disconnected
        1 - gateway is connected
        2 - gateway is reconnecting
        """
        return self.state


    def update_presence(self, status, custom_status=None, custom_status_emoji=None, activities=None):
        """Update client status. Statuses: 'online', 'idle', 'dnd', 'invisible', 'offline'"""
        if self.legacy:
            return   # spacebar_fix - gateway returns error if this event is sent

        all_activities = []
        if custom_status:
            all_activities.append({
                "name": "Custom Status",
                "type": 4,
                "state": custom_status,
            })
            if custom_status_emoji:
                all_activities[0]["emoji"] = custom_status_emoji
        if activities:
            for activity in activities:
                all_activities.append(activity)

        payload = {
            "op": 3,
            "d": {
                "status": status,
                "afk": "false",
                "since": 0,
                "activities": all_activities,
            },
        }
        self.send(payload)
        logger.debug("Updated presence")


    def subscribe(self, channel_id, guild_id):
        """
        Subscribe to the channel to receive "typing" events from gateway for specified channel,
        and threads updates, and member presence updates for this guild.
        """
        if self.my_user_data["bot"]:
            return
        if guild_id:
            # when subscribing, add channel to list of subscribed channels
            # then send whole list
            # if channel is already in list send nothing
            # when subscribing to guild for the first time, send extra config
            for num, guild in enumerate(self.subscribed):
                if guild["guild_id"] == guild_id:
                    if channel_id in guild["channels"]:
                        logger.debug("Already subscribed to the channel")
                    else:
                        logger.debug("Adding channel to subscribed")
                        guild["channels"].append(channel_id)
                        channels = {}
                        for channel in guild["channels"]:
                            channels[channel] = [[0, 99]]   # what is [[0, 99]]?
                        payload = {
                            "op": 37,   # changed in gateway v10
                            "d": {
                                "subscriptions": {
                                    guild_id: {
                                        "channels": channels,
                                    },
                                },
                            },
                        }
                        self.send(payload)
                    break
            else:
                logger.debug("Adding guild to subscribed")
                self.subscribed.append({
                    "guild_id": guild_id,
                    "channels": [channel_id],
                    "members": [],
                })
                payload = {
                    "op": 37,   # changed in gateway v10
                    "d": {
                        "subscriptions": {
                            guild_id: {
                                "typing": True,
                                "activities": self.want_member_list,
                                "threads": True,
                                "channels": {
                                    channel_id: [[0, 99]],
                                },
                            },
                        },
                    },
                }
                self.send(payload)
        else:   # for DMs
            payload = {
                "op": 13,
                "d": {
                    "channel_id": channel_id,
                },
            }
            self.send(payload)
            logger.debug("Subscribed to a DM")


    def subscribe_member(self, member_id, guild_id):
        """Subscribe to the member account to receive presence updates from gateway"""
        # same as subscribe() just with members instead channels
        # no need to handle subscribing to the guild for first time
        # because it will always be subscribed with subscribe()
        for num, guild in enumerate(self.subscribed):
            if guild["guild_id"] == guild_id:
                if member_id in guild["members"]:
                    logger.debug("Already subscribed to the member")
                else:
                    logger.debug("Adding member to subscribed")
                    guild["members"].append(member_id)
                    payload = {
                        "op": 37,   # changed in gateway v10
                        "d": {
                            "subscriptions": {
                                guild_id: {
                                    "members": guild["members"],
                                },
                            },
                        },
                    }
                    self.send(payload)
                break


    def request_members(self, guild_id, members, query=None, limit=None, nonce=None):
        """
        Request update chunk for specified members in this guild.
        GUILD_MEMBERS_CHUNK event will be received after this.
        """
        if query:
            self.querying_members = True
        if members or query:
            payload = {
                "op": 8,
                "d": {
                    "guild_id": [guild_id],
                    "query": query,
                    "limit": limit,
                    "presences": False,
                    "user_ids": members,
                },
            }
            if nonce and members:
                payload["d"]["nonce"] = nonce
            self.send(payload)
            logger.debug("Requesting guild members chunk")


    def request_voice_gateway(self, guild_id, channel_id, mute, video, preferred_regions):
        """Request voice gateway, VOICE_STATE_UPDATE and VOICE_SERVER_UPDATE events are expected"""
        payload = {
            "op": 4,
            "d": {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "self_mute": mute,
                "self_deaf": False,
                "self_video": video,
                "preferred_regions": preferred_regions,
                "preferred_region": preferred_regions[0],
                "flags": VOICE_FLAGS,
            },
        }
        self.send(payload)
        self.voice_gateway_data_ready = 1
        logger.debug("Requesting voice gateway")


    def request_voice_disconnect(self):
        """Request disconnect from voice gateway and leave call"""
        payload = {
            "op": 4,
            "d": {
                "guild_id": None,
                "channel_id": None,
                "self_mute": False,
                "self_deaf": False,
                "self_video": False,
                "flags": VOICE_FLAGS,
            },
        }
        self.send(payload)
        self.voice_gateway_data_ready = 0
        logger.debug("Requesting voice disconnect")


    def update_mute_in_call(self, guild_id, channel_id, mute, video, preferred_regions):
        """Update this client mute state while in voice call"""
        payload = {
            "op": 4,
            "d": {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "self_mute": mute,
                "self_deaf": False,
                "self_video": video,
                "preferred_regions": preferred_regions,
                "preferred_region": preferred_regions[0],
                "flags": VOICE_FLAGS,
            },
        }
        self.send(payload)
        self.voice_gateway_data_ready = 0
        logger.debug("Sending voice mute update")


    def set_subscribed_channels(self, subscribed_channels):
        """Set currently subscribed channels and so MESSAGE_ events can be faster processed for other channels"""
        self.subscribed_channels = subscribed_channels


    def set_want_member_list(self, want):
        """Set if client wants to receive member list updates"""
        self.want_member_list = want


    def set_want_summaries(self, want):
        """Set if client wants to receive summaries"""
        self.want_summaries = want


    def set_offline(self):
        """Set offline client status"""
        # this will trigger reconnect from thread guard
        self.reconnect_requested = True


    def get_ready(self):
        """Return wether gateway processed entire READY event"""
        return self.ready


    def get_read_state(self):
        """Get all channels read state after connecting, channels are in a dict keyed with their id for more efficient lookup"""
        return self.read_state


    def get_dms(self):
        """
        Get list of open DMs with their recipient
        DM types:
        1 - single person DM
        3 - group DM (name is not None)
        """
        return self.dms, self.dms_id


    def get_guilds(self):
        """
        Get list of guilds and channels with their metadata, updated only when reconnecting or guild/channels changed
        Channel types:
        0 - text
        2 - voice
        4 - category
        5 - announcements
        11/12 - thread
        15 - forum (contains only threads)
        message_notifications:
        0 - all messages
        1 - only mentions
        2 - nothing
        """
        if self.guilds_changed:
            self.guilds_changed = False
            return self.guilds
        return None


    def get_roles(self):
        """Get list of roles for all guilds with their metadata, updated only when reconnecting"""
        return self.roles


    def get_blocked(self):
        """Get list of blocked user ids"""
        return self.blocked


    def get_settings_proto(self):
        """Get account settings, only proto 1"""
        if self.proto_changed:
            self.proto_changed = False
            return self.user_settings_proto
        return None


    def get_premium(self):
        """Get premium state of my account"""
        return self.premium


    def get_my_roles(self):
        """Get list of my roles for all servers"""
        return self.my_roles


    def get_my_id(self):
        """Get my discord user ID"""
        return self.my_id


    def get_my_user_data(self):
        """Get my user data"""
        return self.my_user_data


    def get_my_status(self):
        """Get my activity status, including rich presence, updated regularly"""
        if self.status_changed:
            self.status_changed = False
            return self.my_status
        return None


    def get_dm_activities(self):
        """
        Get list of friends with their activity status, including rich presence, updated regularly
        Activity types:
        0 - playing
        2 - listening
        """
        if self.dm_activities_changed:
            self.dm_activities_changed = False
            return self.dm_activities
        return None


    def get_activities(self):
        """
        Get member activities, updated regularly.
        Activity types:
        0 - playing
        2 - listening
        """
        if self.activities_changed:
            cache = self.activities_changed
            self.activities_changed = []
            return self.activities, cache
        return [], []


    def get_subscribed_activities(self):
        """
        Get subscribed member activities, updated regularly.
        Activity types:
        0 - playing
        2 - listening
        """
        if self.subscribed_activities_changed:
            cache = self.subscribed_activities_changed
            self.subscribed_activities_changed = []
            return self.subscribed_activities, cache
        return [], []


    def get_member_roles(self):
        """Get member roles, updated regularly."""
        if self.roles_changed:
            temp = self.roles_changed
            self.roles_changed = False
            return self.member_roles, temp
        return None, None


    def get_app_command_autocomplete_resp(self):
        """Get app command autocomplete response, received after discord.send_interaction with type 4"""
        if self.app_command_autocomplete_resp:
            cache = self.app_command_autocomplete_resp
            self.app_command_autocomplete_resp = []
            return cache
        return []


    def get_emojis(self):
        """Get all guilds emojis"""
        return self.emojis


    def get_stickers(self):
        """Get all guilds stickers"""
        return self.stickers


    def get_member_query_results(self):
        """Get member query results, updated after request_members() with query is called"""
        if self.member_query_results:
            cache = self.member_query_results
            self.member_query_results = []
            return cache
        return None


    def get_voice_gateway(self):
        """Get voice gateway data when it is ready"""
        if self.voice_gateway_data_ready >= 3:
            cache = self.voice_gateway_data
            self.voice_gateway_data = {}
            self.voice_gateway_data_ready = 0
            return cache


    def get_token_update(self):
        """Get new refreshed token"""
        cache = self.token_update
        self.token_update = None
        return cache


    def get_user_update(self):
        """
        Get a tuple of:
        user update (self) - dict,
        wether roles have changed - guild_id
        wether guild roles have changed - (guild_id, role_id)
        If user_update has only user_id and nick, then its guild_mmember_update event.
        """
        if self.user_update:
            cache = (*self.user_update, self.guild_roles_changed)
            self.user_update = None
            self.guild_roles_changed = None
            return cache
        return None


    # all following "get_*" work like this:
    # internally:
    #    get events and append them to list
    #    when get_messages() is called, remove event from list and return it
    # externally:
    #    in main get initial data
    #    main loop in app runs get_*() functions:
    #    if returned data:
    #       if data is for current channel:
    #           update it in existing data from init
    #       repeat
    #    else:
    #       continue to other code in main


    def get_messages(self):
        """
        Get message CREATE, EDIT, DELETE and ACK events for every guild and channel.
        Returns 1 by 1 event as an update for list of messages.
        """
        if len(self.messages_buffer) == 0:
            return None
        return self.messages_buffer.pop(0)


    def get_typing(self):
        """
        Get typing across guilds.
        Returns 1 by 1 event as an update for list of typing.
        """
        if len(self.typing_buffer) == 0:
            return None
        return self.typing_buffer.pop(0)


    def get_summaries(self):
        """
        Get summaries.
        Returns 1 by 1 event as an update for list of summaries.
        """
        if len(self.summaries_buffer) == 0:
            return None
        return self.summaries_buffer.pop(0)


    def get_message_ack(self):
        """
        Get messages seen by other clients.
        Returns 1 by 1 ack event.
        """
        if len(self.msg_ack_buffer) == 0:
            return None
        return self.msg_ack_buffer.pop(0)


    def get_threads(self):
        """
        Get thread update related events: update, crate and delete.
        Returns 1 by 1 update event with opcode.
        """
        if len(self.threads_buffer) == 0:
            return None
        return self.threads_buffer.pop(0)


    def get_call_events(self):
        """
        Get call events.
        Returns 1 by 1 call event.
        """
        if len(self.call_buffer) == 0:
            return None
        return self.call_buffer.pop(0)
