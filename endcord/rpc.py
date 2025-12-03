import json
import logging
import os
import socket
import struct
import sys
import threading
import time

if sys.platform == "win32":
    import pywintypes
    import win32file
    import win32pipe


GATEWAY_RATE_LIMIT = 5   # delay between each event that rpc server will send to discord
GATEWAY_RATE_LIMIT_SAME = 60   # delay between each same activity that rpc server will send to discord
REQUEST_DELAY = 1.5   # delay to decrease error 429 - too many requests
logger = logging.getLogger(__name__)
if sys.platform == "linux":
    DISCORD_SOCKET = f"/run/user/{os.getuid()}/discord-ipc-0"
else:
    DISCORD_SOCKET = ""
DISCORD_WIN_PIPE = r"\\?\pipe\discord-ipc-0"
DISCORD_ASSETS_WHITELIST = (   # assets passed from RPC app to discord as text
    "large_text",
    "small_text",
    "large_image",   # external images are text
    "small_image",
)


def receive_data_linux(connection):
    """Receive and decode nicely packed json data"""
    try:
        header = connection.recv(8)
        op, length = struct.unpack("<II", header)
        data = connection.recv(length)
        final_data = json.loads(data)
        return op, final_data
    except struct.error as e:
        logger.error(e)
        return None, None


def send_data_linux(connection, op, data):
    """
    Nicely encode and send json data
    op codes:
    0 - handshake
    1 - payload
    """
    payload = json.dumps(data, separators=(",", ":"))
    package = struct.pack("<ii", op, len(payload)) + payload.encode("utf-8")
    connection.sendall(package)


def receive_data_win(pipe):
    """Receive and decode nicely packed json data from windows named pipe"""
    try:
        header = win32file.ReadFile(pipe, 8)[1]
        op, length = struct.unpack("<II", header)
        data = win32file.ReadFile(pipe, length)[1]
        final_data = json.loads(data.decode("utf-8"))
        return op, final_data
    except (struct.error, pywintypes.error) as e:
        logger.error(e)
        return None, None


def send_data_win(pipe, op, data):
    """
    Nicely encode and send json data to windows named pipe
    op codes:
    0 - handshake
    1 - payload
    """
    try:
        payload = json.dumps(data, separators=(",", ":"))
        package = struct.pack("<ii", op, len(payload)) + payload.encode("utf-8")
        win32file.WriteFile(pipe, package)
    except pywintypes.error as e:
        logger.error(e)


if sys.platform == "win32":
    receive_data = receive_data_win
    send_data = send_data_win
else:
    receive_data = receive_data_linux
    send_data = send_data_linux


class RPC:
    """Main RPC class"""

    def __init__(self, discord, user, config):
        self.discord = discord
        self.changed = False
        self.external = config["rpc_external"]
        self.activities = []
        if user["bot"]:
            logger.warning("RPC server cannot be started for bot accounts")
            return
        self.run = True

        self.generate_dispatch(user)

        # start server thread
        if sys.platform == "win32":
            self.rpc_thread = threading.Thread(target=self.server_thread_win, daemon=True, args=())
            self.rpc_thread.start()
        elif sys.platform == "linux":
            self.rpc_thread = threading.Thread(target=self.server_thread_linux, daemon=True, args=())
            self.rpc_thread.start()
        else:
            logger.warning(f"RPC server cannot be started on this platform: {sys.platform}")
            return


    def generate_dispatch(self, user):
        """Generate dispatch ready event from user data"""
        self.dispatch = {
            "cmd": "DISPATCH",
            "data": {
                "v": 1,
                "config": {
                    "cdn_host": "cdn.discordapp.com",
                    "api_endpoint": "//discord.com/api",
                    "environment": "production",
                },
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "discriminator": user["extra"]["discriminator"],
                    "global_name": user["global_name"],
                    "avatar": user["extra"]["avatar"],
                    "avatar_decoration_data": user["extra"]["avatar_decoration_data"],
                    "bot": False,
                    "flags": 32,
                    "premium_type": user["extra"]["premium_type"],
                },
            },
            "evt": "READY",
            "nonce": None,
        }


    def build_response(self, data):
        """Build response to RPC client, which is usually just echo"""
        if data["cmd"] == "SET_ACTIVITY":
            response = {
                "cmd": data["cmd"],
                "data": data["args"]["activity"],
                "evt": None,
                "nonce": data["nonce"],
            }
        else:
            response = {
                "cmd": data["cmd"],
                "data": {
                    "evt": data["evt"],
                },
                "evt": None,
                "nonce": data["nonce"],
            }
        return response


    def client_thread(self, connection):
        """Thread that handles receiving and sending data from one client"""
        app_id = None

        try:   # lets keep server running even if there is error in one thread
            op, init_data = receive_data(connection)
            if op is None or init_data is None:
                return
            if isinstance(init_data, str):   # discord client sends a number string for unknown reason
                if sys.platform == "win32":
                    win32file.CloseHandle(connection)
                else:
                    connection.close()
                return
            app_id = init_data["client_id"]
            logger.debug(f"RPC app id: {app_id}")
            rpc_data = self.discord.get_rpc_app(app_id)
            rpc_assets = self.discord.get_rpc_app_assets(app_id)
            logger.info(f"RPC client connected: {rpc_data["name"]}")
            if rpc_data and rpc_assets:
                send_data(connection, 1, self.dispatch)
                sent_time = time.time() - (GATEWAY_RATE_LIMIT + 1)
                prev_activity = None
                while self.run:
                    op, data = receive_data(connection)
                    if not data:
                        break
                    logger.debug(f"Received: {json.dumps(data, indent=2)}")

                    if data["cmd"] == "SET_ACTIVITY":
                        # prevent sending same activity too often
                        if data["args"]["activity"] == prev_activity and time.time() - sent_time < GATEWAY_RATE_LIMIT_SAME:
                            response = self.build_response(data)
                            send_data(connection, op, response)
                            return
                        prev_activity = data["args"]["activity"]

                        # prevent sending presences too often
                        while time.time() - sent_time < GATEWAY_RATE_LIMIT:
                            response = self.build_response(data)
                            send_data(connection, op, response)
                            continue
                        sent_time = time.time()

                        activity = data["args"]["activity"]
                        if not activity:
                            continue
                        activity_type = activity.get("type", 0)

                        # add everything thats missing
                        activity["application_id"] = app_id
                        activity["name"] = rpc_data["name"]
                        assets = {}
                        for asset_client in activity["assets"]:

                            # check if asset is external link
                            if activity["assets"][asset_client][:8] == "https://":
                                if self.external:
                                    for _ in range(5):
                                        external_asset = self.discord.get_rpc_app_external(app_id, activity["assets"][asset_client])
                                        if isinstance(external_asset, float):   # rate limited
                                            time.sleep(external_asset + 0.2)
                                        elif not external_asset:
                                            break
                                        else:
                                            assets[asset_client] = f"mp:{external_asset[0]["external_asset_path"]}"
                                            break
                                if len(activity["assets"]) > 1:
                                    time.sleep(REQUEST_DELAY)
                                else:
                                    external_asset = activity["assets"][asset_client]
                                continue

                            # check if asset is an image
                            elif "image" in asset_client:
                                for asset_app in rpc_assets:
                                    if activity["assets"][asset_client] == asset_app["name"]:
                                        assets[asset_client] = asset_app["id"]
                                        break
                            elif asset_client in DISCORD_ASSETS_WHITELIST:
                                assets[asset_client] = activity["assets"][asset_client]
                                continue

                        # prepare other data
                        if "timestamps" in activity:
                            if "start" in activity["timestamps"]:
                                activity["timestamps"]["start"] *= 1000
                            if "end" in activity["timestamps"]:
                                activity["timestamps"]["end"] *= 1000
                        if "buttons" in activity:
                            buttons = activity.pop("buttons")
                            activity["buttons"] = []
                            activity["metadata"] = {"button_urls": []}
                            for button in buttons:
                                activity["buttons"].append(button["label"])
                                activity["metadata"]["button_urls"].append(button["url"])

                        activity["assets"] = assets
                        if activity_type == 2:
                            activity.pop("flags", None)
                        activity["flags"] = 1
                        activity["type"] = activity_type
                        activity.pop("instance", None)

                        # self.changed will be true only when presence data has been updated
                        for num, app in enumerate(self.activities):
                            if app["application_id"] == app_id:
                                if activity != self.activities[num]:
                                    self.activities[num] = activity
                                    self.changed = True
                                break
                        else:
                            self.activities.append(activity)
                            self.changed = True

                        response = {
                            "cmd": data["cmd"],
                            "data": data["args"]["activity"],
                            "evt": None,
                            "nonce": data["nonce"],
                        }
                        send_data(connection, op, response)
                    else:
                        # all other commands are currently unimplemented
                        # returning them to client so it can keep running with rich presence only
                        # this will probably create some errors with edge-case clients
                        response = {
                            "cmd": data["cmd"],
                            "data": {
                                "evt": data["evt"],
                            },
                            "evt": None,
                            "nonce": data["nonce"],
                        }
                        send_data(connection, op, response)

            else:
                logger.warning("Failed retrieving RPC app data from discord")
        except Exception as e:
            logger.error(e)

        # remove presence from list
        if app_id:
            for num, app in enumerate(self.activities):
                if app["application_id"] == app_id:
                    self.activities.pop(num)
                    self.changed = True
                    break
        if sys.platform == "win32":
            win32file.CloseHandle(connection)
        else:
            connection.close()
        logger.info("RPC client disconnected")


    def server_thread_win(self):
        """Thread that listens for new connections on the named pipe and starts new client_thread for each connection"""
        logger.info("RPC server started")
        while self.run:
            try:
                pipe = win32pipe.CreateNamedPipe(
                    DISCORD_WIN_PIPE,   # pipeName
                    win32pipe.PIPE_ACCESS_DUPLEX,   # openMode
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_WAIT,   # pipeMode
                    win32pipe.PIPE_UNLIMITED_INSTANCES,   # nMaxInstances
                    65536,   # nOutBufferSize in bytes   # = 64KiB
                    65536,   # nInBufferSize
                    0,   # nDefaultTimeOut
                    None,   # lpSecurityAttributes
                )
                win32pipe.ConnectNamedPipe(pipe, None)
                threading.Thread(target=self.client_thread, daemon=True, args=(pipe,)).start()
            except pywintypes.error as e:
                logger.error(f"Named pipe error: {e}")


    def server_thread_linux(self):
        """Thread that listens for new connections on socket and starts new client_thread for each connection"""
        if sys.platform in ("linux", "darwin"):
            if not os.path.isdir(os.path.dirname(DISCORD_SOCKET)):
                logger.warning("Error starting RPC server: could not create socket")
                return
            if os.path.exists(DISCORD_SOCKET):
                os.unlink(DISCORD_SOCKET)
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(DISCORD_SOCKET)
        logger.info("RPC server started")
        while self.run:
            self.server.listen(1)
            client, address = self.server.accept()
            threading.Thread(target=self.client_thread, daemon=True, args=(client, )).start()


    def get_activities(self, force=False):
        """Get activities for all connected apps, only when they changed."""
        if self.changed or force:
            self.changed = False
            logger.debug(f"Sending: {json.dumps(self.activities, indent=2)}")
            return self.activities
        return None
