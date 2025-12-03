import base64
import http.client
import logging
import os
import re
import socket
import ssl
import time
import urllib.parse
import uuid

try:
    import orjson as json
except ImportError:
    try:
        import ujson as json
    except ImportError:
        import json

import socks
from discord_protos import FrecencyUserSettings, PreloadedUserSettings
from google.protobuf.json_format import MessageToDict, ParseDict

from endcord import peripherals
from endcord.message import prepare_messages

DISCORD_HOST = "discord.com"
DISCORD_CDN_HOST = "cdn.discordapp.com"
DISCORD_EPOCH = 1420070400
SEARCH_PARAMS = ("content", "channel_id", "author_id", "mentions", "has", "max_id", "min_id", "pinned", "offset")
SEARCH_HAS_OPTS = ("link", "embed", "poll", "file", "video", "image", "sound", "sticker", "forward")
PING_OPTIONS = ("all", "mention", "nothing")
SUPPRESS_OPTIONS = ("suppress_everyone", "suppress_roles")
logger = logging.getLogger(__name__)


def ceil(x):
    """
    Return the ceiling of x as an integral.
    Equivalent to math.ceil().
    """
    # lets not import whole math just for this
    return -int(-1 * x // 1)


def get_sticker_url(sticker):
    """Generate sticker download url from its type and id, lottie stickers will return None"""
    sticker_type = sticker["format_type"]
    if sticker_type == 1:   # png - downloaded as webp
        return f"https://media.discordapp.net/stickers/{sticker["id"]}.webp"
    if sticker_type == 2:   # apng
        return f"https://media.discordapp.net/stickers/{sticker["id"]}.png"
    if sticker_type == 4:   # gif
        return f"https://media.discordapp.net/stickers/{sticker["id"]}.gif"
    return None   # lottie


def generate_nonce():
    """Generate nonce string - current UTC time as discord snowflake"""
    return str((int(time.time() * 1000) - DISCORD_EPOCH * 1000) << 22)


def build_multipart_body(data):
    """
    Build multipart/form-data body for http.client.
    Header is needed:
    multipart_header = self.header.copy()
    multipart_header.update({"Content-Type": content_type"Content-Length": content_len})
    """
    boundary = "------geckoformboundary" + uuid.uuid4().hex
    body_lines = [
        f"--{boundary}",
        'Content-Disposition: form-data; name="payload_json"',
        "",
        json.dumps(data).decode("utf-8"),
        f"--{boundary}--",
        "",
    ]
    body = "\r\n".join(body_lines).encode("utf-8")
    content_type = f"multipart/form-data; boundary={boundary}"
    content_len = str(len(body))
    return body, content_type, content_len


class Discord():
    """Methods for fetching and sending data to Discord using REST API"""

    def __init__(self, token, host, client_prop, user_agent, proxy=None):
        if host:
            host_obj = urllib.parse.urlsplit(host)
            if host_obj.netloc:
                self.host = host_obj.netloc
            else:
                self.host = host_obj.path
            host_netloc = self.host.lstrip("api.")
            self.cdn_host = f"cdn.{host_netloc}"
        else:
            self.host = DISCORD_HOST
            self.cdn_host = DISCORD_CDN_HOST
        logger.debug(f"Endpoints: API={self.host}, CDN={self.cdn_host}")
        self.token = token
        self.header = {
            "Accept": "*/*",
            "Authorization": self.token,
            "Content-Type": "application/json",
            "Priority": "u=1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": user_agent,
            "X-Super-Properties": client_prop,
        }
        self.user_agent = user_agent
        self.proxy = urllib.parse.urlsplit(proxy)
        self.my_id = self.get_my_id(exit_on_error=True)
        self.activity_token = None
        self.protos = [[], []]
        self.stickers = []
        self.my_commands = []
        self.my_apps = []
        self.guild_commands = []
        self.threads = []
        self.uploading = []
        self.voice_regions = []
        self.ranked_voice_regions = []
        self.attachment_id = 1


    def check_expired_attachment_url(self, url):
        """Check if provided url is attachment and return its querys"""
        parsed_url = urllib.parse.urlsplit(url)
        if self.cdn_host in parsed_url.netloc:
            return dict(urllib.parse.parse_qsl(parsed_url.query))


    def get_connection(self, host, port):
        """Get connection object and handle proxying"""
        if self.proxy.scheme:
            if self.proxy.scheme.lower() == "http":
                connection = http.client.HTTPSConnection(self.proxy.hostname, self.proxy.port)
                connection.set_tunnel(host, port=port)
            elif "socks" in self.proxy.scheme.lower():
                proxy_sock = socks.socksocket()
                proxy_sock.set_proxy(socks.SOCKS5, self.proxy.hostname, self.proxy.port)
                proxy_sock.settimeout(10)
                proxy_sock.connect((host, port))
                ssl_context = ssl.create_default_context()
                ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
                proxy_sock = ssl_context.wrap_socket(proxy_sock, server_hostname=host)
                # proxy_sock.do_handshake()   # seems like its not needed
                connection = http.client.HTTPSConnection(host, port, timeout=10)
                connection.sock = proxy_sock
            else:
                connection = http.client.HTTPSConnection(host, port)
        else:
            connection = http.client.HTTPSConnection(host, port, timeout=5)
        return connection


    def get_my_id(self, exit_on_error=False):
        """Get my discord user ID"""
        message_data = None
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", "/api/v9/users/@me", message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            if exit_on_error:
                logger.warning("No internet connection. Exiting...")
                raise SystemExit("No internet connection. Exiting...")
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return data["id"]
        if response.status in (400, 401):   # bad request or unauthorized
            logger.error("unauthorized access. Probably invalid token. Exiting...")
            raise SystemExit("unauthorized access. Probably invalid token. Exiting...")
        logger.error(f"Failed to get my id. Response code: {response.status}")
        connection.close()
        return False


    def get_user(self, user_id, extra=False):
        """Get relevant information about specified user"""
        message_data = None
        url = f"/api/v9/users/{user_id}/profile"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            if extra:   # extra data for rpc
                extra_data = {
                    "avatar": data["user"]["avatar"],
                    "avatar_decoration_data": data["user"].get("avatar_decoration_data"),   # spacebar_fix - get
                    "discriminator": data["user"]["discriminator"],
                    "flags": data["user"].get("flags"),   # spacebar_fix - get
                    "premium_type": data["premium_type"],
                }
            else:
                extra_data = None
            bio = data["user"].get("bio")
            pronouns = data["user"].get("pronouns")
            if data["user_profile"]:
                if data["user_profile"].get("bio"):
                    bio = data["user_profile"].get("bio")
                if data["user_profile"].get("pronouns"):
                    pronouns = data["user_profile"].get("pronouns")
            tag = None
            if data["user"].get("primary_guild") and "tag" in data["user"]["primary_guild"]:   # spacebar_fix - get
                tag = data["user"]["primary_guild"]["tag"]
            return {
                "id": data["user"]["id"],
                "guild_id": None,
                "username": data["user"]["username"],
                "global_name": data["user"].get("global_name"),   # spacebar_fix - get
                "nick": None,
                "bio": bio,
                "pronouns": pronouns,
                "joined_at": None,
                "tag": tag,
                "bot": data["user"].get("bot"),
                "extra": extra_data,
                "roles": None,
            }
        logger.error(f"Failed to fetch user data. Response code: {response.status}")
        connection.close()
        return False


    def get_user_guild(self, user_id, guild_id):
        """Get relevant information about specified user in a guild"""
        message_data = None
        url = f"/api/v9/users/{user_id}/profile?with_mutual_guilds=true&with_mutual_friends=true&guild_id={guild_id}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            if "guild_member" in data:
                nick = data["guild_member"]["nick"]
                roles = data["guild_member"]["roles"]
                joined_at = data["guild_member"]["joined_at"][:10]
            else:   # just in case
                nick = None
                roles = None
                joined_at = None
            bio = data["user"].get("bio")
            pronouns = data["user"].get("pronouns")
            if data["user_profile"]:
                if data["user_profile"].get("bio"):
                    bio = data["user_profile"].get("bio")
                if data["user_profile"].get("pronouns"):
                    pronouns = data["user_profile"].get("pronouns")
            if "guild_member_profile" in data and data["guild_member_profile"]:
                guild_profile = data["guild_member_profile"]
                if "pronouns" in guild_profile and guild_profile["pronouns"]:
                    pronouns = data["guild_member_profile"]["pronouns"]
                if "bio" in guild_profile and guild_profile["bio"]:
                    bio = data["guild_member_profile"]["bio"]
            tag = None
            if data["user"].get("primary_guild") and "tag" in data["user"]["primary_guild"]:   # spacebar_fix - get
                tag = data["user"]["primary_guild"]["tag"]
            return {
                "id": data["user"]["id"],
                "guild_id": guild_id,
                "username": data["user"]["username"],
                "global_name": data["user"].get("global_name"),   # spacebar_fix - get
                "nick": nick,
                "bio": bio,
                "pronouns": pronouns,
                "joined_at": joined_at,
                "tag": tag,
                "bot": data["user"].get("bot"),
                "roles": roles,
            }
        logger.error(f"Failed to fetch user data. Response code: {response.status}")
        connection.close()
        return False


    def get_dms(self):
        """
        Get list of open DMs with their recipient.
        Same as gateway.get_dms()
        DM types:
        1 - single person text
        3 - group DM (name is not None)
        """
        message_data = None
        url = f"/api/v9/users/{self.my_id}/channels"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None, None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            dms = []
            dms_id = []
            for dm in data:
                recipients = []
                for recipient in dm["recipients"]:
                    recipients.append({
                        "id": recipient["id"],
                        "username": recipient["username"],
                        "global_name": recipient.get("global_name"),   # spacebar_fix - get
                    })
                if "name" in dm:
                    name = dm["name"]
                else:
                    name = recipients[0].get("global_name")   # spacebar_fix - get
                dms.append({
                    "id": dm["id"],
                    "type": dm["type"],
                    "recipients": recipients,
                    "name": name,
                })
                dms_id.append(dm["id"])
            return dms, dms_id
        logger.error(f"Failed to fetch dm list. Response code: {response.status}")
        connection.close()
        return [], []


    def get_channels(self, guild_id):
        """
        Get channels belonging to specified guild
        Channel types:
        0 - text
        2 - voice
        4 - category
        5 - announcement
        11/12 - thread
        15 - forum (contains only threads)
        """
        message_data = None
        url = f"/api/v9/guilds/{guild_id}/channels"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            channels = []
            for channel in data:
                channels.append({
                    "id": channel["id"],
                    "type": channel["type"],
                    "name": channel["name"],
                    "topic": channel.get("topic"),
                    "parent_id": channel.get("parent_id"),
                    "position": channel["position"],
                })
            return channels
        logger.error(f"Failed to fetch guild channels. Response code: {response.status}")
        connection.close()
        return []


    def get_messages(self, channel_id, num=50, before=None, after=None, around=None):
        """Get specified number of messages, optionally number before and after message ID"""
        message_data = None
        url = f"/api/v9/channels/{channel_id}/messages?limit={num}"
        if before:
            url += f"&before={before}"
        if after:
            url += f"&after={after}"
        if around:
            url += f"&around={around}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            # debug_chat
            # from endcord import debug
            # debug.save_json(data, "messages.json", False)
            return prepare_messages(data)
        logger.error(f"Failed to fetch messages. Response code: {response.status}")
        connection.close()
        return []


    def get_reactions(self, channel_id, message_id, reaction):
        """Get reaction for specified message"""
        encoded_reaction = urllib.parse.quote(reaction)
        message_data = None
        url = f"/api/v9/channels/{channel_id}/messages/{message_id}/reactions/{encoded_reaction}?limit=50&type=0"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            reaction = []
            for user in data:
                reaction.append({
                    "id": user["id"],
                    "username": user["username"],
                })
            return reaction
        logger.error(f"Failed to fetch reaction details: {reaction}. Response code: {response.status}")
        connection.close()
        return []


    def get_mentions(self, num=25, roles=True, everyone=True):
        """Get specified number of mentions, optionally including role and everyone mentions"""
        url = f"/api/v9/users/@me/mentions?limit={num}"
        if roles:
            url += "&roles=true"
        if everyone:
            url += "&everyone=true"
        message_data = None
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            mentions = []
            for mention in data:
                mentions.append({
                    "id": mention["id"],
                    "channel_id": mention["channel_id"],
                    "timestamp": mention["timestamp"],
                    "content": mention["content"],
                    "user_id": mention["author"]["id"],
                    "username": mention["author"]["username"],
                    "global_name": mention["author"].get("global_name"),   # spacebar_fix - get
                })
            return mentions
        logger.error(f"Failed to fetch mentions. Response code: {response.status}")
        connection.close()
        return []


    def get_stickers(self):
        """Get default discord stickers and cache them"""
        if self.stickers:
            return self.stickers
        url = "/api/v9/sticker-packs?locale=en-US"
        message_data = None
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return []
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            for pack in data["sticker_packs"]:
                pack_stickers = []
                for sticker in pack["stickers"]:
                    pack_stickers.append({
                        "id": sticker["id"],
                        "name": sticker["name"],
                    })
                self.stickers.append({
                    "pack_id": pack["id"],
                    "pack_name": pack["name"],
                    "stickers": pack_stickers,
                })
            del (data, pack_stickers)
            return self.stickers
        logger.error(f"Failed to fetch stickers. Response code: {response.status}")
        connection.close()
        return []


    def get_settings_proto(self, num):
        """
        Get account settings:
        num=1 - General user settings
        num=2 - Frecency and favorites storage for various things
        """
        if self.protos[num-1]:
            return self.protos[num-1]
        message_data = None
        url = f"/api/v9/users/@me/settings-proto/{num}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())["settings"]
            connection.close()
            if num == 1:
                decoded = PreloadedUserSettings.FromString(base64.b64decode(data))
            elif num == 2:
                decoded = FrecencyUserSettings.FromString(base64.b64decode(data))
            else:
                return {}
            self.protos[num-1] = MessageToDict(decoded)
            return self.protos[num-1]
        logger.error(f"Failed to fetch settings. Response code: {response.status}")
        connection.close()
        return False


    def patch_settings_proto(self, num, data):
        """
        Patch account settings
        num=1 - General user settings
        num=2 - Frecency and favorites storage for various things"""
        if not self.protos[num-1]:
            self.get_settings_proto(num)
        self.protos[num-1].update(data)
        if num == 1:
            encoded = base64.b64encode(ParseDict(data, PreloadedUserSettings()).SerializeToString()).decode("utf-8")
        elif num == 2:
            encoded = base64.b64encode(ParseDict(data, FrecencyUserSettings()).SerializeToString()).decode("utf-8")
        else:
            return False

        message_data = json.dumps({"settings": encoded})
        url = f"/api/v9/users/@me/settings-proto/{num}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PATCH", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            connection.close()
            return True
        logger.error(f"Failed to patch protobuf {num}. Response code: {response.status}")
        connection.close()
        return False


    def patch_settings_old(self, setting, value):   # spacebar_fix - using old user_settings
        """Patch account settings, used only for spacebar compatibility"""
        url = "/api/v9/users/@me/settings"
        message_data = json.dumps({str(setting): value})
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PATCH", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            connection.close()
            return True
        logger.error(f"Failed to patch user setting {setting}. Response code: {response.status}")
        connection.close()
        return False


    def get_rpc_app(self, app_id):
        """Get data about Discord RPC application"""
        message_data = None
        url = f"/api/v9/oauth2/applications/{app_id}/rpc"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return {
                "id": data["id"],
                "name": data["name"],
                "description": data["description"],
            }
        logger.error(f"Failed to fetch application rpc data. Response code: {response.status}")
        connection.close()
        return False


    def get_rpc_app_assets(self, app_id):
        """Get Discord application assets list"""
        message_data = None
        url = f"/api/v9/oauth2/applications/{app_id}/assets"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            assets = []
            for asset in data:
                assets.append({
                    "id": asset["id"],
                    "name": asset["name"],
                })
            return assets
        logger.error(f"Failed to fetch application assets. Response code: {response.status}")
        connection.close()
        return False


    def get_rpc_app_external(self, app_id, asset_url):
        """Get Discord application external assets"""
        message_data = json.dumps({"urls": [asset_url]})
        url = f"/api/v9/applications/{app_id}/external-assets"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return data
        if response.status == 429:
            data = json.loads(response.read())
            connection.close()
            retry_after = float(data["retry_after"])
            logger.error(f"Failed to fetch application external assets. Response code: 429 - Retry after: {retry_after}")
            return retry_after
        logger.error(f"Failed to fetch application external assets. Response code: {response.status}")
        connection.close()
        return False


    def get_file(self, url, save_path):
        """Download file from discord with proper header"""
        message_data = None
        url_object = urllib.parse.urlsplit(url)
        filename = os.path.basename(url_object.path)
        connection = self.get_connection(url_object.netloc, 443)
        connection.request("GET", url_object.path + "?" + url_object.query, message_data, self.header)
        response = connection.getresponse()
        extension = response.getheader("Content-Type").split("/")[-1].replace("jpeg", "jpg")
        destination = os.path.join(save_path, filename)
        if os.path.splitext(destination)[-1] == "":
            destination = destination + "." + extension
        with open(destination, mode="wb") as file:
            file.write(response.read())


    def send_message(self, channel_id, message_content, reply_id=None, reply_channel_id=None, reply_guild_id=None, reply_ping=True, attachments=None, stickers=None):
        """Send a message in the channel with reply with or without ping"""
        message_dict = {
            "content": message_content,
            "tts": "false",
            "flags": 0,
            "nonce": generate_nonce(),
        }
        if reply_id and reply_channel_id:
            message_dict["message_reference"] = {
                "message_id": reply_id,
                "channel_id": reply_channel_id,
            }
            if reply_guild_id:
                message_dict["message_reference"]["guild_id"] = reply_guild_id
            if not reply_ping:
                if reply_guild_id:
                    message_dict["allowed_mentions"] = {
                        "parse": ["users", "roles", "everyone"],
                    }
                else:
                    message_dict["allowed_mentions"] = {
                        "parse": ["users", "roles", "everyone"],
                        "replied_user": False,
                    }
        if attachments:
            for attachment in attachments:
                if attachment["upload_url"]:
                    if "attachments" not in message_dict:
                        message_dict["attachments"] = []
                        message_dict["type"] = 0
                        message_dict["sticker_ids"] = []
                        message_dict["channel_id"] = channel_id
                        message_dict.pop("tts")
                        message_dict.pop("flags")
                    message_dict["attachments"].append({
                        "id": len(message_dict["attachments"]),
                        "filename": attachment["name"],
                        "uploaded_filename": attachment["upload_filename"],
                    })
        if stickers:
            message_dict["sticker_ids"] = stickers
        message_data = json.dumps(message_dict)
        url = f"/api/v9/channels/{channel_id}/messages"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            if "referenced_message" in data:
                reference = {
                    "id": data["referenced_message"]["id"],
                    "timestamp": data["referenced_message"]["timestamp"],
                    "content": data["referenced_message"]["content"],
                    "user_id": data["referenced_message"]["author"]["id"],
                    "username": data["referenced_message"]["author"]["username"],
                    "global_name": data["referenced_message"]["author"].get("global_name"),   # spacebar_fix - get
                }
            else:
                reference = None
            return {
                "id": data["id"],
                "channel_id": data["channel_id"],
                "guild_id": data.get("guild_id"),
                "timestamp": data["timestamp"],
                "edited": False,
                "content": data["content"],
                "mentions": data["mentions"],
                "mention_roles": data["mention_roles"],
                "mention_everyone": data["mention_everyone"],
                "user_id": data["author"]["id"],
                "username": data["author"]["username"],
                "global_name": data["author"].get("global_name"),   # spacebar_fix - get
                "referenced_message": reference,
                "reactions": [],
                "stickers": data.get("sticker_items", []),
            }
        logger.error(f"Failed to send message. Response code: {response.status}")
        connection.close()
        return False


    def send_update_message(self, channel_id, message_id, message_content):
        """Update the message in the channel"""
        message_data = json.dumps({"content": message_content})
        url = f"/api/v9/channels/{channel_id}/messages/{message_id}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PATCH", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            mentions = []
            if data["mentions"]:
                for mention in data["mentions"]:
                    mentions.append({
                        "username": mention["username"],
                        "id": mention["id"],
                    })
            return {
                "id": data["id"],
                "channel_id": data["channel_id"],
                "guild_id": data.get("guild_id"),
                "edited": True,
                "content": data["content"],
                "mentions": mentions,
                "mention_roles": data["mention_roles"],
                "mention_everyone": data["mention_everyone"],
                "stickers": data.get("sticker_items", []),
            }

        logger.error(f"Failed to edit the message. Response code: {response.status}")
        connection.close()
        return False


    def send_delete_message(self, channel_id, message_id):
        """Delete the message from the channel"""
        message_data = None
        url = f"/api/v9/channels/{channel_id}/messages/{message_id}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("DELETE", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to delete the message. Response code: {response.status}")
        connection.close()
        return False



    def send_ack(self, channel_id, message_id, manual=False):
        """Send information that this channel has been seen up to this message"""
        last_viewed = ceil((time.time() - DISCORD_EPOCH) / 86400)   # days since first second of 2015 (discord epoch)
        if manual:
            message_data = json.dumps({"manual": True})
        else:
            message_data = json.dumps({
                "last_viewed": last_viewed,
                "token": None,
            })
        url = f"/api/v9/channels/{channel_id}/messages/{message_id}/ack"
        logger.debug("Sending message ack")
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            connection.close()
            return True
        logger.error(f"Failed to set the message as seen. Response code: {response.status}")
        connection.close()
        return False


    def send_ack_bulk(self, channels):
        """
        Send information that this channel has been seen up to this message
        channels is a list of dicts: [{channel_id, message_id}, ...]
        """
        for channel in channels:
            channel["read_state_type"] = 0
        message_data = json.dumps({"read_states": channels})
        url = "/api/v9/read-states/ack-bulk"
        logger.debug("Sending bulk message ack")
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to send bulk message ack. Response code: {response.status}")
        connection.close()
        return False


    def send_typing(self, channel_id):
        """Set '[username] is typing...' status on specified channel"""
        message_data = None
        url = f"/api/v9/channels/{channel_id}/typing"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return int(data["message_send_cooldown_ms"] / 1000)
        logger.error(f"Failed to set typing. Response code: {response.status}")
        connection.close()
        return False


    def send_reaction(self, channel_id, message_id, reaction):
        """Send reaction to specified message"""
        encoded_reaction = urllib.parse.quote(reaction)
        message_data = None
        url = f"/api/v9/channels/{channel_id}/messages/{message_id}/reactions/{encoded_reaction}/%40me?location=Message%20Reaction%20Picker&type=0"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PUT", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to send reaction: {reaction}. Response code: {response.status}")
        connection.close()
        return False


    def remove_reaction(self, channel_id, message_id, reaction):
        """Remove reaction from specified message"""
        encoded_reaction = urllib.parse.quote(reaction)
        message_data = None
        url = f"/api/v9/channels/{channel_id}/messages/{message_id}/reactions/{encoded_reaction}/0/%40me?location=Message%20Inline%20Button&burst=false"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("DELETE", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to delete reaction: {reaction}. Response code: {response.status}")
        connection.close()
        return False


    def send_mute_guild(self, mute, guild_id):
        """Mute/unmute guild"""
        guild_id = str(guild_id)

        message_dict = {
            "guilds": {
                guild_id: {
                    "muted": bool(mute),
                },
            },
        }
        if mute:
            message_dict["guilds"][guild_id]["mute_config"] = {
                "end_time": None,
                "selected_time_window": -1,
            }

        url = "/api/v9/users/@me/guilds/settings"
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PATCH", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            connection.close()
            return True
        logger.error(f"Failed to set guild mute config. Response code: {response.status}")
        connection.close()
        return False


    def send_mute_channel(self, mute, channel_id, guild_id):
        """Mute/unmute channel or category"""
        channel_id = str(channel_id)
        guild_id = str(guild_id)

        channel_overrides = {
            channel_id: {
                "muted": mute,
            },
        }
        if mute:
            channel_overrides[channel_id]["mute_config"] = {
                "end_time": None,
                "selected_time_window": -1,
            }
        message_dict = {
            "guilds": {
                guild_id: {
                    "channel_overrides": channel_overrides,
                },
            },
        }

        url = "/api/v9/users/@me/guilds/settings"
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PATCH", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            connection.close()
            return True
        logger.error(f"Failed to set guild mute config. Response code: {response.status}")
        connection.close()
        return False


    def send_mute_dm(self, mute, dm_id):
        """Mute/unmute DM"""
        dm_id = str(dm_id)

        message_dict = {
            "channel_overrides": {
                dm_id: {
                    "muted": bool(mute),
                },
            },
        }
        if mute:
            message_dict["channel_overrides"][dm_id]["mute_config"] = {
                "end_time": None,
                "selected_time_window": -1,
            }

        url = "/api/v9/users/@me/guilds/%40me/settings"
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PATCH", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            connection.close()
            return True
        logger.error(f"Failed to set DM mute config. Response code: {response.status}")
        connection.close()
        return False


    def send_notification_setting_guild(self, setting, guild_id, value=None):
        """Send notification settings for guild"""
        guild_id = str(guild_id)
        option = None
        for i, ping_option in enumerate(PING_OPTIONS):
            if setting == ping_option:
                option = "message_notifications"
                value = i
                break
        if setting in SUPPRESS_OPTIONS:
            option = setting

        if option:
            message_dict = {
                "guilds": {
                    guild_id: {
                        option: value,
                    },
                },
            }

            url = "/api/v9/users/@me/guilds/settings"
            message_data = json.dumps(message_dict)
            try:
                connection = self.get_connection(self.host, 443)
                connection.request("PATCH", url, message_data, self.header)
                response = connection.getresponse()
            except (socket.gaierror, TimeoutError):
                connection.close()
                return None
            if response.status == 200:
                connection.close()
                return True
            logger.error(f"Failed to set guild mute config. Response code: {response.status}")
            connection.close()
        return False


    def send_notification_setting_channel(self, setting, channel_id, guild_id):
        """Send notification settings for channel or category"""
        channel_id = str(channel_id)
        guild_id = str(guild_id)
        value = None
        for i, ping_option in enumerate(PING_OPTIONS):
            if setting == ping_option:
                value = i
                break

        if value is not None:
            channel_overrides = {
                channel_id: {
                    "message_notifications": value,
                },
            }
            message_dict = {
                "guilds": {
                    guild_id: {
                        "channel_overrides": channel_overrides,
                    },
                },
            }

            url = "/api/v9/users/@me/guilds/settings"
            message_data = json.dumps(message_dict)
            try:
                connection = self.get_connection(self.host, 443)
                connection.request("PATCH", url, message_data, self.header)
                response = connection.getresponse()
            except (socket.gaierror, TimeoutError):
                connection.close()
                return None
            if response.status == 200:
                connection.close()
                return True
            logger.error(f"Failed to set guild mute config. Response code: {response.status}")
            connection.close()
        return False


    def get_threads(self, channel_id, number=25, offset=0, archived=True):
        """Get specified number of threads with offset for one forum"""
        message_data = None
        url = f"/api/v9/channels/{channel_id}/threads/search?archived={archived}&sort_by=last_message_time&sort_order=desc&limit={number}&tag_setting=match_some&offset={offset}"
        if offset == 0:   # check in cache
            for channel in self.threads:
                if channel["channel_id"] == channel_id:
                    return len(channel["threads"]), channel["threads"]
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 0, []
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            threads = []
            total = data["total_results"]
            for thread in data["threads"]:
                threads.append({
                    "id": thread["id"],
                    "type": thread["type"],
                    "owner_id": thread["owner_id"],
                    "name": thread["name"],
                    "locked": thread["thread_metadata"]["locked"],
                    "message_count": thread["message_count"],
                    "timestamp": thread["thread_metadata"]["create_timestamp"],
                    "parent_id": thread["parent_id"],
                    "suppress_everyone": False,   # no config for threads
                    "suppress_roles": False,
                    "message_notifications": None,
                    "muted": False,   # muted and joined are in READY event
                    "joined": False,
                })
            if offset == 0:   # save to cache
                for channel in self.threads:
                    if channel["channel_id"] == channel_id:
                        channel["threads"] = threads
                        break
                else:
                    self.threads.append({
                        "channel_id": channel_id,
                        "threads": threads,
                    })
            return total, threads
        logger.error(f"Failed to perform a thread search. Response code: {response.status}")
        connection.close()
        return 0, []


    def join_thread(self, thread_id):
        """Join a thread"""
        message_data = None
        # location is not necessarily "Sidebar Overflow"
        url = f"/api/v9/channels/{thread_id}/thread-members/@me?location=Sidebar%20Overflow"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to join a thread. Response code: {response.status}")
        connection.close()
        return False


    def leave_thread(self, thread_id):
        """Leave a thread"""
        message_data = None
        # location is not necessarily "Sidebar Overflow"
        url = f"/api/v9/channels/{thread_id}/thread-members/@me?location=Sidebar%20Overflow"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("DELETE", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to leave a thread. Response code: {response.status}")
        connection.close()
        return False


    def search(self, object_id, channel=False, content=None, channel_id=None, author_id=None, mentions=None, has=None, max_id=None, min_id=None, pinned=None, offset=None):
        """
        Search in specified guild/channel (dm)
        author_id   - (from) user_id
        mentions    - user_id
        has         - link, embed, poll, file, video, image, sound, sticker, forward
        max_id      - (before) convert date to (floor) snowflake
        min_id      - (after) convert date to (ceil) snowflake
        channel_id  - (in) channel_id
        pinned      - true, false
        content     - search query
        offset      - starting number
        """
        message_data = None
        url = f"/api/v9/{"channels" if channel else "guilds"}/{object_id}/messages/search?"
        if "true" in pinned:
            pinned = "true"
        elif "false" in pinned:
            pinned = "false"
        for one_has in has:
            if one_has not in SEARCH_HAS_OPTS:
                return 0, []
        content = [content]
        if offset:
            offset = str(offset)
        offset = [offset]
        for num, items in enumerate([content, channel_id, author_id, mentions, has, max_id, min_id, pinned, offset]):
            for item in items:
                if item:
                    url += f"{SEARCH_PARAMS[num]}={urllib.parse.quote(item)}&"
        url = url.rstrip("&")
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None, []
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            messages = []
            total = data["total_results"]
            for message in data["messages"]:
                messages.append(message[0])
            return total, prepare_messages(messages, have_channel_id=True)
        logger.error(f"Failed to perform a message search. Response code: {response.status}")
        connection.close()
        return 0, []


    def get_my_commands(self):
        """Get my app commands"""
        if self.my_commands:
            return self.my_commands, self.my_apps

        message_data = None
        url = "/api/v9/users/@me/application-command-index"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return [], []
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()

            applications = data["applications"]
            commands = []
            apps = []
            for app in applications:
                apps.append({
                    "app_id": app["id"],
                    "name": app["name"],
                })

            for command in data["application_commands"]:
                if command["type"] == 1:   # only slash commands
                    for app in applications:
                        if app["id"] == command["application_id"]:
                            app_name = app["name"]
                            break
                    else:
                        continue
                    ready_command = {
                        "id": command["id"],
                        "app_id": command["application_id"],
                        "app_name": app_name,
                        "name": command["name"],
                        "description": command["description"],
                        "version": command["version"],
                        "options": command.get("options", []),
                    }
                    if command.get("dm_permission"):
                        ready_command["dm"] = True
                    commands.append(ready_command)

            self.my_commands = commands
            self.my_apps = apps
            return commands, apps
        logger.error(f"Failed to fetch my application commands. Response code: {response.status}")
        connection.close()
        return [], []


    def get_guild_commands(self, guild_id):
        """Get guild app commands"""
        for guild in self.guild_commands:
            if guild["guild_id"] == guild_id:
                return guild["commands"], guild["apps"]

        message_data = None
        url = f"/api/v9/guilds/{guild_id}/application-command-index"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return [], []
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()

            applications = data["applications"]
            commands = []
            apps = []
            for app in applications:
                apps.append({
                    "app_id": app["id"],
                    "name": app["name"],
                    "perms": app.get("permissions", {}),
                })

            for command in data["application_commands"]:
                if command["type"] == 1:   # only slash commands
                    for app in applications:
                        if app["id"] == command["application_id"]:
                            app_name = app["name"]
                            break
                    ready_command = {
                        "id": command["id"],
                        "app_id": command["application_id"],
                        "app_name": app_name,
                        "name": command["name"],
                        "description": command["description"],
                        "version": command["version"],
                        "options": command.get("options", []),
                    }
                    if command.get("permissions"):
                        ready_command["permissions"] = command.get("permissions")
                    if command.get("default_member_permissions"):
                        ready_command["default_member_permissions"] = command.get("default_member_permissions")
                    commands.append(ready_command)

            self.guild_commands.append({
                "guild_id": guild_id,
                "commands": commands,
                "apps": apps,
            })
            return commands, apps
        logger.error(f"Failed to fetch guild application commands. Response code: {response.status}")
        connection.close()
        return [], []


    def send_interaction(self, guild_id, channel_id, session_id, app_id, interaction_type, interaction_data, attachments, message_id=None):
        """
        Send app interaction
        Known types:
        2 - application command
        3 - component interaction
        4 - command option autocomplete
        """
        if attachments:
            for attachment in attachments:
                if attachment["upload_url"]:
                    interaction_data["attachments"].append({
                        "id": len(interaction_data["attachments"]),
                        "filename": attachment["name"],
                        "uploaded_filename": attachment["upload_filename"],
                    })
        message_dict = {
            "type": interaction_type,
            "application_id": app_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "session_id": session_id,
            "data": interaction_data,
            "nonce": generate_nonce(),
        }
        if interaction_type == 3:
            message_dict["message_id"] = message_id
        else:
            message_dict["analytics_location"] = "slash_ui"
        message_data = json.dumps(message_dict)
        url = "/api/v9/interactions"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to send app interaction. Response code: {response.status}")
        connection.close()
        return False


    def send_vote(self, channel_id, message_id, vote_ids, clear=False):
        """Send poll vote or clear my existing votes"""
        if clear:
            message_data = json.dumps({"answer_ids": []})
        else:
            message_data = json.dumps({"answer_ids": [str(x) for x in vote_ids]})
        url = f"/api/v9/channels/{channel_id}/polls/{message_id}/answers/@me"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PUT", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to send poll vote. Response code: {response.status}")
        connection.close()
        return False


    def block_user(self, user_id, ignore=False):
        """Block/ignore specified user"""
        if ignore:
            message_data = None
        else:
            message_data = json.dumps({"type": 2})
        url = f"/api/v9/users/@me/relationships/{user_id}"
        if ignore:
            url = url + "/ignore"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PUT", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed block/ignore user. Response code: {response.status}")
        connection.close()
        return False


    def unblock_user(self, user_id, ignore=False):
        """Unblock specified user (and unignore)"""
        message_data = None
        url = f"/api/v9/users/@me/relationships/{user_id}"
        if ignore:
            url = url + "/ignore"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("DELETE", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to unblock user. Response code: {response.status}")
        connection.close()
        return False


    def get_pinned(self, channel_id):
        """Get pinned messages for specified channel"""
        message_data = None
        url = f"/api/v9/channels/{channel_id}/pins"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return prepare_messages(data, have_channel_id=True)
        logger.error(f"Failed to get pinned messages. Response code: {response.status}")
        connection.close()
        return False


    def send_pin(self, channel_id, message_id):
        """Send what message should be pinned in specified channel"""
        message_data = None
        url = f"/api/v9/channels/{channel_id}/pins/{message_id}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("PUT", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to pin a message: Response code: {response.status}")
        connection.close()
        return False


    def search_gifs(self, query):
        """Search gifs from query and return their links with preview"""
        message_data = None
        query = urllib.parse.quote(query)
        url = f"/api/v9/gifs/search?q={query}&media_format=webm&provider=tenor&locale=en-US"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return []
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            gifs = []
            for gif in data:
                gifs.append({
                    "url": gif["url"],
                    "webm": gif["src"],
                    "gif": gif["gif_src"],
                })
            return gifs
        logger.error(f"Failed to perform a gif search. Response code: {response.status}")
        connection.close()
        return []


    def request_attachment_url(self, channel_id, path, custom_name=None):
        """
        Request attachment upload link.
        If file is too large - will return None.
        Return codes:
        0 - OK
        1 - Failed
        2 - File too large
        """
        if custom_name:
            filename = custom_name
        else:
            filename = os.path.basename(path)
        message_data = json.dumps({
            "files": [{
                "file_size": peripherals.get_file_size(path),
                "filename": filename,
                "id": self.attachment_id,
                "is_clip": peripherals.get_is_clip(path),
            }],
        })
        url = f"/api/v9/channels/{channel_id}/attachments"
        self.attachment_id += 1
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None, 3   # network error
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return data["attachments"][0], 0
        if response.status == 413:
            logger.warning("Failed to get attachment upload link: 413 - File too large.")
            connection.close()
            return None, 2   # file too large
        logger.error(f"Failed to get attachment upload link. Response code: {response.status}")
        connection.close()
        return None, 1


    def upload_attachment(self, upload_url, path):
        """
        Upload a file to provided url
        """
        # will load whole file into RAM, but discord limits upload size anyways
        # and this function wont be run if request_attachment_url() is not successful
        header = {
            "Content-Type": "application/octet-stream",
            "Origin": f"https://{self.host}",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": self.user_agent,
        }
        url = urllib.parse.urlsplit(upload_url)
        upload_url_path = f"{url.path}?{url.query}"
        with open(path, "rb") as f:
            try:
                connection = self.get_connection(url.netloc, 443)
                self.uploading.append((upload_url, connection))
                connection.request("PUT", upload_url_path, f, header)
                response = connection.getresponse()
                self.uploading.remove((upload_url, connection))
            except (socket.gaierror, TimeoutError):
                connection.close()
                return None
            if response.status == 200:
                connection.close()
                return True
            # discord client is also performing OPTIONS request, idk why, not needed here
            logger.error(f"Failed to upload attachment. Response code: {response.status}")
            connection.close()
            return False


    def cancel_uploading(self, url=None):
        """Stop specified upload, or all running uploads"""
        if url:
            for upload in self.uploading:
                upload_url, connection = upload
                if upload_url == url:
                    self.uploading.remove(upload)
        else:
            for upload in self.uploading:
                upload_url, connection = upload
                try:
                    connection.sock.shutdown()
                    connection.sock.close()
                except Exception:
                    logger.debug("Cancel upload: upload socket already closed.")
                self.uploading.remove(upload)


    def cancel_attachment(self, attachment_name):
        """Cancel uploaded attachments"""
        attachment_name = urllib.parse.quote(attachment_name, safe="")
        message_data = None
        url = f"/api/v9/attachments/{attachment_name}"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("DELETE", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        if response.status == 429:
            # discord usually returns 429 for this request, but original client does not retry after some time
            # so this wont retry either, file wont be sent in the message anyway
            logger.debug("Failed to delete attachment. Response code: 429 - Too Many Requests")
            connection.close()
            return True
        logger.error(f"Failed to delete attachment. Response code: {response.status}")
        connection.close()
        return False


    def refresh_attachment_url(self, url):
        """Request refreshed attachment url"""
        message_data = json.dumps({"attachment_urls": [url]})
        url = "/api/v9/attachments/refresh-urls"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            if data["refreshed_urls"]:
                return data["refreshed_urls"][0]["refreshed"]
        logger.error(f"Failed to refresh attachment URL. Response code: {response.status}")
        connection.close()
        return False


    def send_voice_message(self, channel_id, path, reply_id=None, reply_channel_id=None, reply_guild_id=None, reply_ping=None):
        """Send voice message from file path, file must be ogg"""
        waveform, duration = peripherals.get_audio_waveform(path)
        if not duration:
            logger.warning(f"Couldn't read voice message file: {path}")
        upload_data, status = self.request_attachment_url(channel_id, path, custom_name="voice-message.ogg")
        if status != 0:
            logger.warning("Cant send voice message, attachment error")
        uploaded = self.upload_attachment(upload_data["upload_url"], path)
        if not uploaded:
            logger.warning("Cant upload voice message, upload error")
        message_dict = {
            "channel_id": channel_id,
            "content": "",
            "attachments": [{
                "id": "0",
                "filename": "voice-message.ogg",
                "uploaded_filename": upload_data["upload_filename"],
                "duration_secs": duration,
                "waveform": waveform,
            }],
            "message_reference": None,
            "flags": 8192,
            "type": 0,
            "sticker_ids": [],
            "nonce": generate_nonce(),
        }
        if reply_id and reply_channel_id:
            message_dict["message_reference"] = {
                "message_id": reply_id,
                "channel_id": reply_channel_id,
            }
            if reply_guild_id:
                message_dict["message_reference"]["guild_id"] = reply_guild_id
            if not reply_ping:
                if reply_guild_id:
                    message_dict["allowed_mentions"] = {
                        "parse": ["users", "roles", "everyone"],
                    }
                else:
                    message_dict["allowed_mentions"] = {
                        "parse": ["users", "roles", "everyone"],
                        "replied_user": False,
                    }
        message_data = json.dumps(message_dict)
        url = f"/api/v9/channels/{channel_id}/messages"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            connection.close()
            return True
        logger.error(f"Failed to send voice message. Response code: {response.status}")
        connection.close()
        return False


    def check_ring(self, channel_id):
        """Check if user can ring call in DM"""
        message_data = None
        url = f"/channels/{channel_id}/call"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return bool(data["ringable"])
        connection.close()
        return False


    def send_ring(self, channel_id, recipients):
        """Ring private channel recipients if there is an active call"""
        if not self.check_ring(channel_id):
            logger.warning("Cant ring a call in this private channel recipients")
            return

        message_data = json.dumps({
            "recipients": recipients,
        })
        url = f"/api/v9/channels/{channel_id}/call/ring"
        logger.debug("Ringing provate channel recipients")
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 204:
            connection.close()
            return True
        logger.error(f"Failed to ring private channel recipients. Response code: {response.status}")
        connection.close()
        return False


    def get_pfp(self, user_id, pfp_id, size=80):
        """Download pfp for specified user"""
        destination = os.path.join(os.path.expanduser(peripherals.temp_path), f"{pfp_id}.webp")
        if os.path.exists(destination):
            return destination

        message_data = None
        url = f"/avatars/{user_id}/{pfp_id}.webp?size={size}"
        header = {
            "Origin": f"https://{self.host}",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": self.user_agent,
        }
        try:
            connection = self.get_connection(self.cdn_host, 443)
            connection.request("GET", url, message_data, header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            with open(destination, "wb") as f:
                f.write(response.read())
            connection.close()
            return destination
        logger.error(f"Failed to download pfp. Response code: {response.status}")
        connection.close()
        return False


    def get_emoji(self, emoji_id, size=None):
        """Download image for specified custom emoji"""
        destination = os.path.join(os.path.expanduser(peripherals.temp_path), f"{emoji_id}.webp")
        if os.path.exists(destination):
            return destination

        message_data = None
        url = f"/emojis/{emoji_id}.webp"
        if size:
            url = url + f"?size={size}"
        header = {
            "Origin": f"https://{self.host}",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": self.user_agent,
        }
        try:
            connection = self.get_connection(self.cdn_host, 443)
            connection.request("GET", url, message_data, header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            with open(destination, "wb") as f:
                f.write(response.read())
            connection.close()
            return destination
        logger.error(f"Failed to download emoji. Response code: {response.status}")
        connection.close()
        return False


    def get_invite_url(self, channel_id, max_age, max_uses):
        """Get invite url for specified guild channel"""
        message_dict = {
            "flags": 0,
            "max_age": max_age,
            "max_uses": max_uses,
            "target_type": None,
            "target_user_id": None,
            "temporary": False,
            "validate": None,
        }
        url = f"/api/v9/channels/{channel_id}/invites"
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            if data["code"]:
                return f"https://{self.host}/invite/{data["code"]}"
            return None
        logger.error(f"Failed to generate invite url. Response code: {response.status}")
        connection.close()
        return False


    def get_my_standing(self):
        """Get my account standing"""
        message_data = None
        url = "/api/v9/safety-hub/@me"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return data["account_standing"]["state"]
        logger.error(f"Failed to fetch account standing. Response code: {response.status}")
        connection.close()
        return False


    def send_update_activity_session(self, app_id, exe_path, closed, session_id, media_session_id=None, voice_channel_id=None):
        """Send update for currently running activity session"""
        message_data = json.dumps({
            "token": self.activity_token,
            "application_id": app_id,
            "share_activity": True,
            "exePath": exe_path,
            "voice_channel_id": voice_channel_id,
            "session_id": session_id,
            "media_session_id": media_session_id,
            "closed": closed,
        })
        url = "/api/v9/activities"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            self.activity_token = json.loads(response.read())["token"]
            connection.close()
            return self.activity_token
        logger.error(f"Failed to update activity session. Response code: {response.status}")
        connection.close()
        return False


    def get_voice_regions(self):
        """Get voice regions list"""
        if self.voice_regions:
            return self.voice_regions
        message_data = None
        url = "/api/v9/voice/regions"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            regions = []
            optimal = None
            for num, region in enumerate(data):
                if region["deprecated"]:
                    continue
                if region["optimal"]:
                    optimal = num
                regions.append(region["id"])
            if optimal is not None:
                optimal = regions.pop(optimal)
                regions.insert(0, optimal)
            self.voice_regions = regions
            return regions
        logger.error(f"Failed to fetch voice regions. Response code: {response.status}")
        connection.close()
        return False


    def get_best_voice_region(self):
        """Get voice regions ranked by latency"""
        # TOKEN IS NOT USED
        if self.ranked_voice_regions:
            return self.ranked_voice_regions
        message_data = None
        url = "/rtc"
        try:
            media_host = re.sub(r"(?<=\.)[^./]+(?=/|$)", "media", self.host)
            connection = self.get_connection(f"latency.{media_host}", 443)
            connection.request("GET", url, message_data, {"User-Agent": self.header["User-Agent"]})
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return self.ranked_voice_regions
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            regions = []
            for region in data:
                regions.append(region["region"])
            self.ranked_voice_regions = regions
            return self.ranked_voice_regions
        logger.error(f"Failed to fetch ranked voice regions. Response code: {response.status}")
        connection.close()
        return self.ranked_voice_regions


    def check_detectable_apps_version(self):
        """Get last-modified value for list of detectable applications"""
        # TOKEN IS NOT USED
        message_data = None
        url = "/api/v9/applications/detectable"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("HEAD", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        if response.status == 200:
            last_modified = response.getheader("Last-Modified")
            connection.close()
            return last_modified
        connection.close()
        return False


    def get_detectable_apps(self, save_path):
        """Get and save list (as ndjson) of detectable applications, containing all detectable games"""
        message_data = None
        url = "/api/v9/applications/detectable"
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("GET", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return None
        json_array_objects = peripherals.json_array_objects   # to skip name lookup
        if response.status == 200:
            using_orjson = json.__name__ == "orjson"
            if using_orjson:
                nl = b"\n"
            else:
                nl = "\n"
            with open(save_path, "w" + ("b" if using_orjson else "")) as f:
                try:
                    for app in json_array_objects(response):
                        executables = []
                        for exe in app["executables"]:
                            os = exe["os"]
                            os = 0 if os == "linux" else 1 if os == "win32" else 2 if os == "darwin" else None
                            if os is not None:
                                path_piece = exe["name"].lower()
                                if not path_piece.startswith("/"):
                                    path_piece = "/" + path_piece
                                executables.append((os, path_piece))
                        if not executables:
                            continue
                        ready_app = (app["id"], app["name"], executables)
                        f.write(json.dumps(ready_app) + nl)
                except Exception as e:
                    logger.error(f"Error decoding detectable apps json: {e}")
                    return False
                return True
        connection.close()
        return False
