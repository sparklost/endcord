# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import json
import logging
import os
import queue
import random
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque

import dave
import nacl.bindings
import numpy as np
import websocket

from endcord import opus, peripherals, socks

# safely import soundcard, in case there is no sound system
try:
    import soundcard
except (AssertionError, RuntimeError):
    soundcard = False

DISCORD_HOST = "discord.com"
BASE_INPUT_GAIN = 1
BASE_OUTPUT_GAIN = 2.0
VOICE_FLAGS = 2   # ALLOW_VOICE_RECORDING
UDP_TIMEOUT = 10
OPUS_SILENCE = bytes([0xF8, 0xFF, 0xFE])
SILENCE_BUFFER = 5
MAX_SILENCE = 10
MAX_LATENCY_FRAMES = 5
TRANSMITTER_QUEUE_TIMEOUT = 0.06
MIXER_BUFFER = 3
RTCP_SR_SEND_DELAY = 5
RTCP_RR_SEND_DELAY = 1
RTCP_PLI_SEND_DELAY = 2
CODECS = [
    {"name": "opus", "type": "audio", "priority": 1000, "payload_type": 120},
    # {"name": "AV1", "type": "video", "priority": 1000, "payload_type": 101, "rtx_payload_type": 102, "encode": False, "decode": True},
    {"name": "H264", "type": "video", "priority": 2000, "payload_type": 103, "rtx_payload_type": 104, "encode": True, "decode": True},
    # {"name": "H265", "type": "video", "priority": 3000, "payload_type": 105, "rtx_payload_type": 106, "encode": True, "decode": True},
    # {"name": "VP8", "type": "video", "priority": 4000, "payload_type": 107, "rtx_payload_type": 108, "encode": True, "decode": True},
]
VIDEO_HEIGHT = 720   # screen share is scaled to this height
VIDEO_FPS = 30
VIDEO_BITRATE = 2500   # kbps
VIDEO_QUEUE_SIZE = 30
ANNEXB_START = b"\x00\x00\x00\x01"
ANNEXB_START_RE = re.compile(b"\x00\x00\x01")
logger = logging.getLogger(__name__)
rtp_unpacker = struct.Struct(">xxHII")

# get speaker
if soundcard:
    try:
        speaker = soundcard.default_speaker()
        have_sound = True
    except Exception:
        have_sound = False
else:
    have_sound = False


def init_microphone(custom_mic):
    """Try to initialize custom mic, else use default one"""
    os.environ["PULSE_LATENCY_MSEC"] = "20"
    if not have_sound:
        return None
    try:
        if not custom_mic:
            return soundcard.default_microphone()
        if custom_mic == "OFF":
            return False
        try:
            return soundcard.get_microphone(custom_mic)
        except IndexError:
            return soundcard.default_microphone()
    except Exception:
        logger.warn("Failed to initialize microphone!")
        return None


def volume_to_gain(volume, boost=1):
    """Convert volume value 0-200 to gain, above 100 is boost up to x5"""
    volume = max(min(volume, 200), 0)
    if volume == 0:
        return 0
    volume = volume / 100
    if volume <= 1:
        db = -30 + 30 * volume
    else:
        db = 14 * (volume - 1) ** 1.5
    return boost * (10 ** (db / 20))


def detect_silence(data, threshold=0.03):
    """RMS-based silence detection"""
    data = data - np.mean(data, axis=0, keepdims=True)   # remove dc
    rms = np.sqrt(np.mean(data**2))
    return rms < threshold


def split_annex_b(data, final=False):
    """Split H264 annex B stream into NAL units without start codes"""
    starts = [m.end() for m in ANNEXB_START_RE.finditer(data)]
    nals = [bytes(data[starts[i]:starts[i + 1] - 3]).rstrip(b"\x00") for i in range(len(starts) - 1)]
    if final and starts:
        nals.append(bytes(data[starts[-1]:]).rstrip(b"\x00"))
    return [nal for nal in nals if nal], (starts[-1] - 3 if starts else 0)


class Gateway():
    """Methods for fetching and sending data to Discord voice gateway through websocket"""

    def __init__(self, voice_gateway_data, my_id, volume_input, volume_output, user_agent, proxy=None, custom_mic=None, silence=-30, opus_mode="voip", fast_mixer=False, denoise=True, mpv="mpv"):
        self.voice_gateway_data = voice_gateway_data
        self.guild_id = voice_gateway_data["guild_id"]
        self.channel_id = voice_gateway_data["channel_id"]
        self.my_id = my_id
        self.header = [
            "Connection: Upgrade",
            "Sec-WebSocket-Extensions: permessage-deflate",
            f"User-Agent: {user_agent}",
        ]
        self.proxy = proxy
        self.run = True
        self.state = 0
        self.heartbeat_received = True
        self.sequence = 1
        self.resumable = False
        self.call_handler = None
        self.call_handler_thread = None
        self.call_buffer = []
        self.media_session_id = None
        self.udp = None

        self.dave_session = dave.Session()
        self.dave_session.init(
            version=dave.get_max_supported_protocol_version(),
            group_id=int(self.channel_id),
            self_user_id=str(self.my_id),
        )
        self.dave_protocol_version = 0   # no dave yet
        self.pending_transition_id = None
        self.known_user_ids = set()
        self.known_user_ids.add(str(my_id))
        self.ssrc_cache = set()
        self.video_sources = {}

        self.enable_input = volume_input >= 0
        self.volume_input = volume_input if self.enable_input else 0
        self.volume_output = volume_output
        self.custom_mic = custom_mic
        self.silence_threshold = silence
        self.opus_mode = opus_mode
        self.fast_mixer = fast_mixer
        self.denoise = denoise
        self.mpv = mpv

        self.connect()


    def create_udp_socket(self):
        """Create udp soocket to the server"""
        if self.proxy:
            if self.proxy.startswith("socks"):
                self.udp = socks.Socks5UDPSocket(self.proxy)
            else:
                self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                logger.warning("Only SOCKS5 proxy can be used for voice calls. This call will be made without proxy!")
        else:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.connect((self.server_ip, self.server_port))
        self.udp.settimeout(UDP_TIMEOUT)
        logger.debug("Created udp socket")


    def send_ip_discovery(self):
        """Send ip discovery packet to the server"""
        packet = bytearray(74)
        struct.pack_into(">H", packet, 0, 1)   # type = 1 (request)
        struct.pack_into(">H", packet, 2, 70)   # length = 70
        struct.pack_into(">I", packet, 4, self.ssrc)   # big-endian ssrc
        self.udp.send(packet)
        logger.debug("Sent IP discovery packet")


    def receive_ip_discovery(self):
        """Receive this client ip and port from the server"""
        try:
            data = self.udp.recv(74)
            if len(data) < 74:
                logger.error("Invalid IP discovery response")
                self.disconnect()
                return
            typ, length = struct.unpack_from(">HH", data, 0)
            if typ != 2 or length != 70:
                logger.error("Invalid IP discovery response")
                self.disconnect()
                return
            # ssrc = struct.unpack_from(">I", data, 4)[0]
            self.client_ip = data[8:72].split(b"\x00", 1)[0].decode("ascii")
            self.client_port = struct.unpack_from(">H", data, 72)[0]
            logger.debug("Received IP discovery packet")
        except socket.timeout:
            logger.error(f"Failed to receive IP discovery: timeout after {UDP_TIMEOUT} s")
            self.disconnect()


    def start_call_handler(self):
        """Initialize and start voice handler"""
        if not self.call_handler:
            self.call_handler = CallHandler(
                self,
                self.my_id,
                self.ssrc,
                self.udp,
                self.secret_key,
                self.selected_mode,
                self.volume_input,
                self.volume_output,
                custom_mic=(self.custom_mic if self.enable_input else "OFF"),
                silence=self.silence_threshold,
                opus_mode=self.opus_mode,
                fast_mixer=self.fast_mixer,
                denoise=self.denoise,
                mpv=self.mpv,
            )
            self.call_handler.start()

        while self.ssrc_cache:   # load cached ssrc-id pairs received before voice handler was initialized
            ssrc, user_id = self.ssrc_cache.pop()
            self.call_handler.add_ssrc_mapping(ssrc, user_id)

        for user_id, ssrcs in self.video_sources.items():
            self.call_handler.update_video_source(user_id, ssrcs)


    def stop_call_handler(self):
        """Stop voice handler without stopping gateway"""
        try:
            self.udp.close()
        except Exception:
            pass
        if self.call_handler:
            self.call_handler.stop()
            self.call_handler = None


    def connect_ws(self):
        """Connect to websocket"""
        gateway_url = "wss://" + self.voice_gateway_data["endpoint"]
        if sys.platform == "darwin":
            import certifi
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        else:
            ssl_context = ssl.create_default_context()
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ws = websocket.WebSocket(sslopt={"context": ssl_context})
        if self.proxy:
            proxy = urllib.parse.urlsplit(self.proxy)
            scheme = proxy.scheme
            self.ws.connect(
                gateway_url + "/?v=9",
                header=self.header,
                proxy_type="socks5h" if scheme == "socks5" else "http" if scheme == "https" else scheme,
                http_proxy_host=proxy.hostname,
                http_proxy_port=proxy.port,
                http_proxy_auth=(proxy.username, proxy.password) if proxy.username else None,
            )
        else:
            self.ws.connect(gateway_url + "/?v=8", header=self.header)


    def connect(self):
        """Create initial connection to Discord gateway and start identification"""
        self.connect_ws()
        self.state = 1
        self.heartbeat_interval = int(json.loads(self.ws.recv())["d"]["heartbeat_interval"])
        self.receiver_thread = threading.Thread(target=self.receiver, daemon=True)
        self.receiver_thread.start()
        self.heartbeat_thread = threading.Thread(target=self.send_heartbeat, daemon=True)
        self.heartbeat_thread.start()
        self.identify()


    def send_binary(self, opcode, payload):
        """Send a binary websocket message to the gateway"""
        logger.debug(f"BINARY SEND: op: {opcode}, data: {payload}")
        # logger.info(("BINARY SEND", opcode, payload))
        try:
            self.ws.send_binary(struct.pack(">B", opcode) + payload)
        except websocket._exceptions.WebSocketException:
            self.disconnect()


    def send(self, request):
        """Send data to gateway"""
        logger.debug(f"SEND: op: {request["op"]}, data: {request}")
        # logger.info(("SEND", request))
        try:
            self.ws.send(json.dumps(request))
        except websocket._exceptions.WebSocketException:
            self.disconnect()


    def receiver(self):
        """Receive and handle all traffic from gateway, should be run in a thread"""
        logger.debug("Receiver started")
        while self.run:
            try:
                ws_opcode, data = self.ws.recv_data()
            except (
                ConnectionResetError,
                websocket._exceptions.WebSocketConnectionClosedException,
                OSError,
            ):
                break
            if ws_opcode == 8 and len(data) >= 2:   # error
                if not data:
                    break
                code = struct.unpack("!H", data[0:2])[0]
                reason = data[2:].decode("utf-8", "replace")
                if code in (4022, 4014):   # call terminated, disconnected
                    break
                logger.warning(f"Gateway error code: {code}, reason: {reason}")
                break
            elif ws_opcode == 2:   # binary data
                sequence, opcode = struct.unpack(">HB", data[:3])
                response = data[3:]
                self.sequence = max(sequence, self.sequence)
            elif ws_opcode == 1:
                try:
                    try:
                        response = json.loads(data)
                        opcode = response["op"]
                    except ValueError:
                        response = None
                        opcode = None
                        continue
                except Exception as e:
                    logger.warning(f"Receiver error: {e}")
                    break
                self.sequence = max(response.get("seq", 0), self.sequence)

            logger.debug(f"RECEIVE: op: {opcode}, data: {response}")
            # logger.info(("RECEIVE", opcode, response))

            if opcode == 6:
                self.heartbeat_received = True

            elif opcode == 8:
                self.heartbeat_interval = int(response["d"]["heartbeat_interval"])

            elif opcode == 3:   # requested heartbeat
                self.send({
                    "op": 3,
                    "d": {
                        "t": int(time.time()) * 1000,
                        "seq_ack": self.sequence,
                    },
                })

            elif opcode == 2:   # READY
                data = response["d"]
                self.ssrc = data["ssrc"]
                self.server_ip = data["ip"]
                self.server_port = int(data["port"])
                self.enc_modes = data["modes"]
                self.streams = data["streams"]
                self.create_udp_socket()
                self.send_ip_discovery()
                self.receive_ip_discovery()
                self.select_protocol()

            elif opcode == 4:   # SESSION DESCRIPTION
                data = response["d"]
                self.audio_codec = data["audio_codec"]
                self.video_codec = data["video_codec"]
                self.media_session_id = data["media_session_id"]
                self.selected_mode = data["mode"]
                self.secret_key = data["secret_key"]
                self.dave_protocol_version = data.get("dave_protocol_version", 0)
                self.state = 2
                self.send_speaking()
                self.start_call_handler()

            elif opcode == 14:   # SESSION UPDATE
                data = response["d"]
                self.audio_codec = data["audio_codec"]
                self.video_codec = data["video_codec"]
                self.media_session_id = data["media_session_id"]
                self.update = True

            elif opcode == 11:   # CLIENT CONNECT
                data = response["d"]
                for user_id in data["user_ids"]:
                    self.call_buffer.append({
                        "op": "USER_JOIN",
                        "user_id": user_id,
                    })
                    self.known_user_ids.add(str(user_id))

            elif opcode == 12:   # VIDEO SOURCES
                data = response["d"]
                user_id = int(data["user_id"])
                streams = data.get("streams") or []
                ssrcs = {int(s["ssrc"]) for s in streams if s.get("ssrc") and s.get("active", True)}
                if not streams and data.get("video_ssrc"):
                    ssrcs = {int(data["video_ssrc"])}
                if ssrcs:
                    self.video_sources[user_id] = ssrcs
                else:
                    self.video_sources.pop(user_id, None)
                if self.call_handler:
                    self.call_handler.update_video_source(user_id, ssrcs)

            elif opcode == 13:   # CLIENT DISCONNECT
                data = response["d"]
                self.call_buffer.append({
                    "op": "USER_LEAVE",
                    "user_id": data["user_id"],
                })
                self.known_user_ids.discard(str(data["user_id"]))
                self.video_sources.pop(int(data["user_id"]), None)
                if self.call_handler:
                    self.call_handler.update_video_source(int(data["user_id"]), set())

            elif opcode == 5:   # SPEAKING
                data = response["d"]
                self.call_buffer.append({
                    "op": "USER_SPEAKING",
                    "user_id": data["user_id"],
                    "speaking": bool(data["user_id"]),
                })
                if "ssrc" in data:
                    if self.call_handler:
                        self.call_handler.add_ssrc_mapping(int(data["ssrc"]), int(data["user_id"]))
                    else:   # add to cache until call_handler object is created
                        self.ssrc_cache.add((int(data["ssrc"]), int(data["user_id"])))

            # DAVE OPCODES
            elif opcode == 21:  # DAVE_PROTOCOL_PREPARE_TRANSITION
                data = response["d"]
                transition_id = data["transition_id"]
                self.pending_transition_id = transition_id
                self.send_dave_ready_for_transition(transition_id)

            elif opcode == 22:  # DAVE_PROTOCOL_EXECUTE_TRANSITION
                if self.call_handler:
                    self.call_handler.update_ratchets(self.dave_session)

            elif opcode == 24:   # DAVE_PROTOCOL_PREPARE_EPOCH
                data = response["d"]
                if data["epoch"] == 1:
                    # re-init session for new group creation
                    self.dave_session.reset()
                    self.dave_session.init(
                        version=data.get("dave_protocol_version", 1),
                        group_id=int(self.channel_id),
                        self_user_id=str(self.my_id),
                    )
                    self.send_dave_mls_key_package()

            elif opcode == 25:  # DAVE_MLS_EXTERNAL_SENDER_PACKAGE
                self.dave_session.set_external_sender(response)
                self.send_dave_mls_key_package()

            elif opcode == 27:  # DAVE_MLS_PROPOSALS
                result = self.dave_session.process_proposals(response, self.known_user_ids)
                if result is not None:
                    self.send_dave_mls_commit_welcome(result)

            elif opcode == 29:  # DAVE_MLS_ANNOUNCE_COMMIT_TRANSITION
                transition_id = struct.unpack_from(">H", response, 0)[0]
                commit = response[2:]
                result = self.dave_session.process_commit(commit)
                if isinstance(result, dave.RejectType):
                    logger.warning(f"Invalid commit: {result}")
                    self.send_dave_mls_invalid_commit_welcome(transition_id)
                    self.send_dave_mls_key_package()
                else:
                    if self.call_handler:
                        self.call_handler.update_ratchets(self.dave_session)
                    if transition_id != 0:
                        self.send_dave_ready_for_transition(transition_id)

            elif opcode == 30:  # DAVE_MLS_WELCOME
                transition_id = struct.unpack_from(">H", response, 0)[0]
                result = self.dave_session.process_welcome(response[2:], self.known_user_ids)
                if result is None:
                    logger.warning("Invalid welcome")
                    self.send_dave_mls_invalid_commit_welcome(transition_id)
                    self.send_dave_mls_key_package()
                else:
                    if self.call_handler:
                        self.call_handler.update_ratchets(self.dave_session)
                    self.send_dave_ready_for_transition(transition_id)

        logger.debug("Receiver stopped")
        self.disconnect()


    def send_heartbeat(self):
        """Send heartbeat to voice gateway, will stop if response is not received, should be run in a thread"""
        logger.debug(f"Heartbeater started, interval={self.heartbeat_interval/1000} s")
        self.heartbeat_received = True
        # wait for ready event for some time
        sleep_time = 0
        while self.state < 2 and self.run:
            if sleep_time >= self.heartbeat_interval / 100:
                logger.error("Voice gateway setup timeout. Stopping connection.")
                break
            time.sleep(0.5)
            sleep_time += 5
        heartbeat_interval_rand = self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000
        heartbeat_sent_time = time.monotonic()
        while self.run:
            if time.monotonic() - heartbeat_sent_time >= heartbeat_interval_rand:
                self.send({
                    "op": 3,
                    "d": {
                        "t": int(time.time()) * 1000,
                        "seq_ack": self.sequence,
                    },
                })
                heartbeat_sent_time = time.monotonic()
                logger.debug("Heartbeat sent")
                if not self.heartbeat_received:
                    logger.warning("Heartbeat reply not received")
                    break
                self.heartbeat_received = False
                heartbeat_interval_rand = self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000
            # sleep(heartbeat_interval * jitter), but jitter is limited to (0.1 - 0.9)
            # in this time heartbeat ack should be received from discord
            time.sleep(0.5)

        logger.debug("Heartbeater stopped")
        self.disconnect()


    def identify(self):
        """Identify client with discord voice gateway"""
        payload = {
            "op": 0,
            "d": {
                "server_id": self.guild_id,
                "channel_id": self.channel_id,
                "user_id": self.my_id,
                "session_id": self.voice_gateway_data["session_id"],
                "token": self.voice_gateway_data["token"],
                "max_dave_protocol_version": dave.get_max_supported_protocol_version(),
                "video": True,
                "streams": [{
                    "type": "video",
                    "rid": "100",
                    "quality": 100,
                }],
            },
        }
        self.send(payload)


    def select_protocol(self):
        """Send SELECT PROTOCOL event with details of the connection"""
        protocol_data = {
            "address": self.client_ip,
            "port": self.client_port,
            "mode": "aead_xchacha20_poly1305_rtpsize",   # not using AES-GCM because it has different nonce layout
        }
        payload = {
            "op": 1,
            "d": {
                "protocol": "udp",
                "data": protocol_data,
                "codecs": CODECS,
            },
        }
        self.send(payload)


    def send_dave_ready_for_transition(self, transition_id):
        """Send DAVE_READY_FOR_TRANSITION event"""
        self.send({
            "op": 23,
            "d": {"transition_id": transition_id},
        })


    def send_dave_mls_key_package(self):
        """Send DAVE_MLS_KEY_PACKAGE event"""
        key_package = self.dave_session.get_marshalled_key_package()
        self.send_binary(26, key_package)


    def send_dave_mls_commit_welcome(self, commit, welcome=None):
        """Send DAVE_MLS_COMMIT_WELCOME"""
        payload = commit
        if welcome is not None:
            payload += welcome
        self.send_binary(28, payload)


    def send_dave_mls_invalid_commit_welcome(self, transition_id):
        """Send DAVE_MLS_INVALID_COMMIT_WELCOME"""
        self.send({
            "op": 31,
            "d": {"transition_id": transition_id},
        })


    def send_speaking(self, speaking=True):
        """Send SPEAKING event"""
        payload = {
            "op": 5,
            "d": {
                "speaking": 1 if speaking else 0,
                "delay": 0,
                "ssrc": self.ssrc,
            },
        }
        self.send(payload)


    def send_video_sources(self, active, video_ssrc, rtx_ssrc):
        """Send VIDEO event to start/stop outgoing video stream"""
        self.send({
            "op": 12,
            "d": {
                "audio_ssrc": self.ssrc,
                "video_ssrc": video_ssrc if active else 0,
                "rtx_ssrc": rtx_ssrc if active else 0,
                "streams": [{
                    "type": "video",
                    "rid": "100",
                    "ssrc": video_ssrc,
                    "active": active,
                    "quality": 100,
                    "rtx_ssrc": rtx_ssrc,
                    "max_bitrate": VIDEO_BITRATE * 1000,
                    "max_framerate": VIDEO_FPS,
                    "max_resolution": {"type": "fixed", "width": VIDEO_HEIGHT * 16 // 9, "height": VIDEO_HEIGHT},
                }],
            },
        })


    def get_state(self):
        """
        Return current state of gateway:
        0 - disconnected
        1 - connecting
        2 - ready
        """
        return self.state


    def disconnect(self):
        """Disconnect and stop voice gateway"""
        if self.run:
            self.run = False
            self.state = 0
            try:
                self.udp.close()
            except Exception:
                pass
            if self.call_handler:
                self.call_handler.stop()
            self.ws.close(timeout=0)   # this will stop receiver
            time.sleep(0.6)   # time for heartbeater to stop
            logger.debug("Gateway disconnected")


    def get_call_events(self):
        """
        Get call events.
        Returns 1 by 1 call event.
        """
        if len(self.call_buffer) == 0:
            return None
        return self.call_buffer.pop(0)


    def get_media_session_id(self):
        """Get media session id"""
        return self.media_session_id


    def get_rtt(self):
        """Get rtt value in ms"""
        if self.call_handler:
            return self.call_handler.get_rtt()
        return 0.0


    def set_volumes(self, volume_input, volume_output):
        """Set volumes levels"""
        self.volume_input = volume_input if self.enable_input else 0
        self.volume_output = volume_output
        if self.call_handler:
            self.call_handler.set_volumes(self.volume_input, self.volume_output)


    def live_mic_switch(self, new_mic):
        """Change microphone while in the stream"""
        self.call_handler.live_mic_switch(new_mic)


    def play_audio_file(self, path, block=False, mix=False):
        """Play audio file from specified path. If mic is ON then mix sounds"""
        if not self.enable_input:
            return
        if block:
            self.call_handler.audio_file_player(path, mix)
        else:
            threading.Thread(target=self.call_handler.audio_file_player, args=(path, mix), daemon=True).start()


    def stop_file_playback(self):
        """Stop playback of all audio files"""
        self.call_handler.stop_file_playback()


    def set_user_volume(self, user_id, volume):
        """Set volume per user_id"""
        self.call_handler.set_user_volume(user_id, volume)


    def get_user_volumes(self):
        """Get user_id:volume dict"""
        return self.call_handler.user_to_volume


    def get_video_users(self):
        """Get list of user_ids that are currently sending video"""
        return [str(k) for k in self.video_sources]


    def start_video_player(self, user_id=None):
        """Start mpv and show video of user_id, or of first user, return False if there are none"""
        return self.call_handler.start_video_player(user_id)


    def stop_video_player(self):
        """Close mpv and stop receiving video"""
        self.call_handler.stop_video_player()


    def start_screen_sharing(self, source=None):
        """Capture screen with ffmpeg and send it as video"""
        return self.call_handler.start_screen_sharing(source)


    def stop_screen_sharing(self):
        """Stop screen sharing"""
        self.call_handler.stop_screen_sharing()



class CallHandler:
    """Voice call sound receiver, transmitter, player and recorder"""

    def __init__(self, gateway, my_id, my_ssrc, udp, secret_key, encryption_mode, volume_input, volume_output, custom_mic=None, silence=-30, opus_mode="voip", fast_mixer=False, denoise=True, mpv="mpv"):
        self.gateway = gateway
        self.my_id = my_id
        self.my_ssrc = my_ssrc
        self.udp = udp

        self.secret_key = bytes(secret_key)
        self.mode = encryption_mode
        self.dave_session = gateway.dave_session
        self.ssrc_to_decryptor = {}
        self.ssrc_to_userid = {}
        self.user_to_volume = {}
        self.ssrc_to_gain = {}
        self.encryptor = dave.Encryptor()

        self.audio_queue_out = {}
        self.audio_queue_out_lock = threading.Lock()
        self.audio_queue_in = queue.Queue(maxsize=25)   # 0.5s buffer
        self.audio_thread_rec = None

        if opus_mode not in ("voip", "audio", "lowdelay"):
            opus_mode = "voip"
        self.opus_encoder = opus.OpusEncoder(
            application=opus_mode,
            bitrate=None if opus_mode == "audio" else 96000,
            vbr=opus_mode != "audio",
        )
        self.ssrc_to_opus_decoder = {}

        self.microphone = init_microphone(custom_mic)
        self.gain_input = volume_to_gain(volume_input, boost=BASE_INPUT_GAIN)
        self.gain_output = volume_to_gain(volume_output, boost=BASE_OUTPUT_GAIN)
        self.silence_threshold = 10 ** (silence / 20)
        self.fast_mixer = fast_mixer
        if denoise:
            from endcord import rnnoise
            try:
                self.denoiser = rnnoise.RNNoise()
                silence_frame = np.zeros(480, dtype=np.int16)
                self.denoiser.process_frame(silence_frame)
            except OSError as e:
                self.denoiser = None
                logger.warn(e)
        else:
            self.denoiser = None

        self.run = False
        self.recording = False
        self.mix_mic = False
        self.playing = False
        self.active_file_queues = []
        self.rtp_sequence = random.randint(0, 0xFFFF)   # must be random per rfc3550
        self.rtp_timestamp = random.randint(0, 0xFFFFFFFF)
        self.transport_counter = 0
        self.packets_sent = 0
        self.bytes_sent = 0
        self.counter_lock = threading.Lock()

        # video stuff
        self.video_ssrc_to_userid = {}
        self.watch_user = None
        self.watch_ssrc = None
        self.mpv_path = mpv
        self.mpv_proc = None
        self.mpv_ipc = None
        self.twcc_epoch = time.monotonic()
        self.video_rotation = 0
        self.mpv_rotation = 0
        self.video_queue = None
        self.last_pli = 0
        self.reset_video_receiver()
        self.ffmpeg_proc = None
        self.screen_ssrc = 0
        self.screen_rtx_ssrc = 0
        self.video_sequence = random.randint(0, 0xFFFF)
        self.video_timestamp_base = random.randint(0, 0xFFFFFFFF)


    def start(self):
        """Start receiver and transmitter loops in threads"""
        if self.mode not in ("aead_aes256_gcm_rtpsize", "aead_xchacha20_poly1305_rtpsize"):
            logger.error(f"Unsupported encryption mode {self.mode}")
            self.gateway.disconnect()
            return
        self.run = True
        self.recording = True
        self.rtp_sequence = random.randint(0, 0xFFFF)
        self.rtp_timestamp = random.randint(0, 0xFFFFFFFF)
        self.transport_counter = 0
        self.rtt_ms = 0.0

        if have_sound:
            # start receiver and player
            self.receiver_thread = threading.Thread(target=self.receiver_loop, daemon=True)
            self.receiver_thread.start()
            self.audio_thread_play = threading.Thread(target=self.audio_player, args=(48000, 2), daemon=True)
            self.audio_thread_play.start()

            # start transmitter and recorder
            if self.gateway.enable_input:
                self.transmitter_thread = threading.Thread(target=self.transmitter_loop, daemon=True)
                self.transmitter_thread.start()
            if self.microphone:
                self.audio_thread_rec = threading.Thread(target=self.audio_recorder, args=(48000, 2), daemon=True)
                self.audio_thread_rec.start()

            # start rtcp sender
            if RTCP_SR_SEND_DELAY:
                threading.Thread(target=self.rtcp_sender_report_loop, daemon=True).start()

        else:
            logger.warning("Could not find any speakers or sound system is not running")


    def stop(self):
        """Stop voice handler"""
        if not self.run:
            return
        self.run = False
        self.stop_video_player(notify=False)
        self.stop_screen_sharing(notify=False)
        try:
            self.udp.close()
        except Exception:
            pass


    def set_volumes(self, volume_input, volume_output):
        """Set volumes levels"""
        self.gain_input = volume_to_gain(volume_input, boost=BASE_INPUT_GAIN)
        self.gain_output = volume_to_gain(volume_output, boost=BASE_OUTPUT_GAIN)


    def set_user_volume(self, user_id, volume):
        """Set volume per user_id"""
        self.user_to_volume[user_id] = volume
        ssrc = next((k for k, v in self.ssrc_to_userid.items() if v == user_id), None)
        if not ssrc:
            return
        self.ssrc_to_gain[ssrc] = volume_to_gain(volume)


    def live_mic_switch(self, new_mic):
        """Change microphone while in the stream"""
        new_mic_obj = init_microphone(new_mic)
        if new_mic_obj is None:
            return
        self.recording = False
        if self.audio_thread_rec:
            self.audio_thread_rec.join()
        self.recording = True
        self.microphone = new_mic_obj
        if new_mic_obj:
            self.audio_thread_rec = threading.Thread(target=self.audio_recorder, args=(48000, 2), daemon=True)
            self.audio_thread_rec.start()


    def next_counter(self):
        """Get next transport counter as bytes"""
        with self.counter_lock:
            self.transport_counter = (self.transport_counter + 1) & 0xFFFFFFFF
            return struct.pack(">I", self.transport_counter)


    def update_ratchets(self, session):
        """Update DAVE ratchets"""
        # update for other users
        for ssrc, user_id in list(self.ssrc_to_userid.items()) + list(self.video_ssrc_to_userid.items()):
            ratchet = session.get_key_ratchet(str(user_id))
            if ratchet is not None:
                if ssrc not in self.ssrc_to_decryptor:
                    self.ssrc_to_decryptor[ssrc] = dave.Decryptor()
                self.ssrc_to_decryptor[ssrc].transition_to_key_ratchet(ratchet, transition_expiry=10.0)

        # update my ratchet
        my_ratchet = session.get_key_ratchet(str(self.my_id))
        if my_ratchet is not None:
            self.encryptor.set_key_ratchet(my_ratchet)


    def add_ssrc_mapping(self, ssrc, user_id):
        """Add user to ssrc map and transition ratchet"""
        self.ssrc_to_userid[ssrc] = user_id
        ratchet = self.gateway.dave_session.get_key_ratchet(str(user_id))
        if ratchet is not None:
            if ssrc not in self.ssrc_to_decryptor:
                self.ssrc_to_decryptor[ssrc] = dave.Decryptor()
            self.ssrc_to_decryptor[ssrc].transition_to_key_ratchet(ratchet, transition_expiry=10.0)


    def transport_encrypt(self, header, payload):
        """Transport encrypt payload, return header + ciphertext + counter"""
        counter = self.next_counter()
        if self.mode == "aead_aes256_gcm_rtpsize":
            nonce = bytearray(12)
            nonce[:4] = counter
            ciphertext = nacl.bindings.crypto_aead_aes256gcm_encrypt(bytes(payload), bytes(header), bytes(nonce), self.secret_key)
        elif self.mode == "aead_xchacha20_poly1305_rtpsize":
            nonce = bytearray(24)
            nonce[:4] = counter
            ciphertext = nacl.bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(bytes(payload), bytes(header), bytes(nonce), self.secret_key)
        else:
            logger.error(f"Unknown mode: {self.mode}")
            return None
        return bytes(header) + ciphertext + counter


    def receiver_loop(self):
        """Receive data, unpack, transport decrypt, DAVE decrypt, opus decode, and put it to queue; send video to handle_video_packet"""
        while self.run:
            # receive
            try:
                data = self.udp.recv(4096)
            except OSError as e:
                logger.error(f"UDP socket closed or error: {e}")
                break
            except Exception as e:
                logger.error(f"UDP receive error: {e}")
                break
            if not data:
                continue
            arrival = time.monotonic()

            # RTP
            # logger.info(("RECEIVED", data))
            if (data[0] >> 6) != 2:   # rtp version
                continue
            data = bytearray(data)
            sequence, timestamp, ssrc = rtp_unpacker.unpack_from(data[:12])
            cutoff = 12 + (data[0] & 0b00001111) * 4
            has_extension = data[0] & 0b00010000
            if has_extension:
                cutoff += 4
            header = data[:cutoff]
            counter = data[-4:]
            ciphertext = data[cutoff:-4]

            # skip non-opus and non-video payloads and parse RTCP
            payload_type = data[1] & 0x7F
            is_video = payload_type == 0x67
            if payload_type != 0x78 and not is_video:
                self.handle_rtcp(data)
                continue
            if is_video and self.watch_user is None:
                continue   # not watching video

            # transport
            try:
                if self.mode == "aead_aes256_gcm_rtpsize":
                    nonce = bytearray(12)
                    nonce[:4] = counter
                    payload = nacl.bindings.crypto_aead_aes256gcm_decrypt(bytes(ciphertext), bytes(header), bytes(nonce), self.secret_key)
                elif self.mode == "aead_xchacha20_poly1305_rtpsize":
                    nonce = bytearray(24)
                    nonce[:4] = counter
                    payload = nacl.bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(bytes(ciphertext), bytes(header), bytes(nonce), self.secret_key)
                else:
                    logger.error(f"Unknown mode: {self.mode}")
                    continue
            except Exception as e:
                logger.error(f"Decryption failed; mode: {self.mode}; error: {e}")
                continue

            # strip rfc3550 padding
            if data[0] & 0b00100000:
                padding_amount = payload[-1]
                if 0 < padding_amount < len(payload):
                    payload = payload[:-padding_amount]

            # strip RTP extension body
            if has_extension:
                ext_body_len = int.from_bytes(data[cutoff - 2:cutoff], "big") * 4
                self.parse_rtp_extensions(bytes(data[cutoff - 4:cutoff - 2]), payload[:ext_body_len], is_video, arrival)
                payload = payload[ext_body_len:]

            # video
            if is_video:
                if self.video_ssrc_to_userid.get(ssrc) != self.watch_user or self.watch_ssrc not in (None, ssrc):
                    continue   # not watching this video
                self.watch_ssrc = ssrc
                self.handle_video_packet(sequence, timestamp, bool(data[1] & 0x80), ssrc, payload)
                continue

            # DAVE
            if self.gateway.dave_protocol_version > 0:
                if not self.gateway.dave_session.has_established_group():
                    continue
                decryptor = self.ssrc_to_decryptor.get(ssrc)
                if decryptor is None:
                    logger.debug(f"Unknown ssrc {ssrc}")
                    continue
                if len(payload) < 12 or not payload.endswith(b"\xfa\xfa"):   # skip empty or non-opus packets
                    continue
                decrypted = decryptor.decrypt(dave.MediaType.audio, bytes(payload))
                if decrypted is None:
                    continue
                payload = decrypted

            # opus
            try:
                opus_decoder = self.ssrc_to_opus_decoder.get(ssrc)
                if opus_decoder is None:
                    opus_decoder = opus.OpusDecoder()
                    self.ssrc_to_opus_decoder[ssrc] = opus_decoder
                pcm = opus_decoder.decode(bytes(payload))
                with self.audio_queue_out_lock:
                    buf = self.audio_queue_out.setdefault(ssrc, deque(maxlen=20))
                    for i in range(0, len(pcm) - 959, 960):
                        buf.append(pcm[i:i + 960])
            except Exception as e:
                logger.error(f"Opus decoding failed. Error: {e}")

        self.gateway.disconnect()


    def transmitter_loop(self):
        """Take data from queue, opus encode, DAVE encrypt, transport encrypt, pack and send it; video is sent with send_video_frame"""
        skipped_samples = 0
        silence_counter = 0
        max_silence_packets = MAX_SILENCE + SILENCE_BUFFER
        is_speaking = False

        while self.run:
            qsize = self.audio_queue_in.qsize()
            if qsize > MAX_LATENCY_FRAMES:  # drain acumulated latency
                dropped = 0
                while self.audio_queue_in.qsize() > 1:
                    try:
                        self.audio_queue_in.get_nowait()
                        dropped += 1
                    except queue.Empty:
                        break
                skipped_samples += dropped * 960
            payload = None
            try:
                audio_data = self.audio_queue_in.get(timeout=TRANSMITTER_QUEUE_TIMEOUT)
                if audio_data is None:
                    break
            except queue.Empty:
                payload = OPUS_SILENCE
            if not self.gain_input:
                payload = OPUS_SILENCE

            if (not payload and self.silence_threshold and detect_silence(audio_data, threshold=self.silence_threshold)) or payload == OPUS_SILENCE:
                if silence_counter > max_silence_packets:
                    if is_speaking:
                        is_speaking = False
                        self.gateway.send_speaking(False)
                    skipped_samples += 960
                    continue   # drop packet to save bandwidth
                silence_counter += 1
                if silence_counter > SILENCE_BUFFER:   # 3 silence frames in a row = confident silence
                    payload = OPUS_SILENCE
            else:
                silence_counter = 0
                if not is_speaking:
                    is_speaking = True
                    self.gateway.send_speaking(True)
                if skipped_samples > 0:   # catch up rtp timeline
                    self.rtp_timestamp += skipped_samples
                    skipped_samples = 0

            # opus
            if payload is None:
                if audio_data.dtype == np.int16:
                    audio_float = audio_data.astype("float32") / 32768.0
                else:
                    audio_float = audio_data.astype("float32")
                audio_float *= self.gain_input
                np.clip(audio_float, -1.0, 1.0, out=audio_float)
                payload = self.opus_encoder.encode(audio_float.reshape(-1), 960)

            opus_payload_size = len(bytes(payload))

            # DAVE
            if self.gateway.dave_protocol_version > 0:
                if not self.gateway.dave_session.has_established_group():
                    skipped_samples += 960
                    continue
                encrypted = self.encryptor.encrypt(dave.MediaType.audio, self.my_ssrc, bytes(payload))
                if encrypted is None:
                    skipped_samples += 960
                    continue
                payload = encrypted

            # build RTP header
            self.rtp_sequence = (self.rtp_sequence + 1) & 0xFFFF
            self.rtp_timestamp = (self.rtp_timestamp + 960) & 0xFFFFFFFF
            header = bytearray(12)
            header[0] = 0x80   # v2, no padding, no extension, cc=0
            header[1] = 0x78   # opus with default config
            struct.pack_into(">H", header, 2, self.rtp_sequence)
            struct.pack_into(">I", header, 4, self.rtp_timestamp)
            struct.pack_into(">I", header, 8, self.my_ssrc)

            # transport encryption
            try:
                payload = self.transport_encrypt(header, payload)
                if not payload:
                    skipped_samples += 960
                    continue
            except Exception as e:
                logger.error(f"Encryption failed; mode: {self.mode}; error: {e}")
                skipped_samples += 960
                continue

            # send
            try:
                self.udp.send(payload)
            except BlockingIOError:
                pass
            except Exception:
                break
            self.packets_sent += 1
            self.bytes_sent += opus_payload_size

        self.gateway.disconnect()
        if is_speaking:
            self.gateway.send_speaking(False)


    def rtcp_sender_report_loop(self):
        """Send RTCP sender report"""
        while self.run:
            now = time.time()
            ntp_seconds = int(now) + 2208988800   # ntp timestamp is seconds since 01.01.1900
            ntp_fraction = int((now % 1) * (1 << 32))
            ntp_timestamp = (ntp_seconds << 32) | ntp_fraction

            payload = bytearray(28)
            payload[0] = 0x80   # v2, no padding, RC=0
            payload[1] = 200   # PT=SR
            struct.pack_into(">H", payload, 2, 6)   # length in 32bit words
            struct.pack_into(">I", payload, 4, self.my_ssrc)
            struct.pack_into(">Q", payload, 8, ntp_timestamp)
            struct.pack_into(">I", payload, 16, self.rtp_timestamp)
            struct.pack_into(">I", payload, 20, self.packets_sent)
            struct.pack_into(">I", payload, 24, self.bytes_sent)

            payload = self.transport_encrypt(payload[:8], payload[8:])
            if not payload:
                time.sleep(RTCP_SR_SEND_DELAY)
                continue
            try:
                self.udp.send(payload)
            except Exception:
                break
            time.sleep(RTCP_SR_SEND_DELAY)


    def handle_rtcp(self, data):
        """Decrypt and parse compounded rtcp packets"""
        if data[1] != 201:   # only taking receiver report
            return

        # decrypt
        header = data[:8]
        try:
            if self.mode == "aead_aes256_gcm_rtpsize":
                nonce = bytearray(12)
                nonce[:4] = data[-4:]
                payload = nacl.bindings.crypto_aead_aes256gcm_decrypt(bytes(data[8:-4]), bytes(header), bytes(nonce), self.secret_key)
            elif self.mode == "aead_xchacha20_poly1305_rtpsize":
                nonce = bytearray(24)
                nonce[:4] = data[-4:]
                payload = nacl.bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(bytes(data[8:-4]), bytes(header), bytes(nonce), self.secret_key)
        except Exception as e:
            logger.error(f"RTCP decryption failed: {e}")
            return
        payload = header + payload

        # parse compounded
        offset = 0
        while offset < len(payload):
            if offset + 4 > len(payload):
                break
            length = (struct.unpack_from(">H", payload, offset + 2)[0] + 1) * 4
            self.parse_rtcp_single(payload[offset : offset + length])
            offset += length


    def parse_rtcp_single(self, payload):
        """Parse one rtcp packet"""
        # if payload[1] == 200:   # RTCP sender report   # UNUSED
        #     pass

        if payload[1] == 201:   # RTCP receiver report
            block_offset = 8   # skip header and sender ssrc
            report_count = payload[0] & 0x1F
            for _ in range(report_count):
                if block_offset + 24 > len(payload):
                    break
                lsr = struct.unpack_from(">I", payload, block_offset + 16)[0]
                dlsr = struct.unpack_from(">I", payload, block_offset + 20)[0]
                if lsr != 0:
                    now_unix = time.time()
                    ntp_sec = int(now_unix) + 2208988800
                    ntp_frac = int((now_unix % 1) * (1 << 32))
                    now_mid = ((ntp_sec & 0xFFFF) << 16) | (ntp_frac >> 16)
                    rtt_units = (now_mid - lsr - dlsr) & 0xFFFFFFFF  # handle 32-bit wraparound
                    self.rtt_ms = (rtt_units / 65536) * 1000
                if logger.getEffectiveLevel() == logging.DEBUG:
                    source_ssrc = struct.unpack_from(">I", payload, block_offset)[0]
                    loss_percentage = (payload[block_offset + 4] / 256) * 100
                    cum_lost_bytes = payload[block_offset + 5:block_offset + 8]
                    cum_lost = int.from_bytes(cum_lost_bytes, "big", signed=False)
                    if cum_lost & 0x800000:
                        cum_lost -= 0x1000000
                    jitter = struct.unpack_from(">I", payload, block_offset + 12)[0]
                    jitter_ms = (jitter / 48000) * 1000   # convert from rtp timestamp unit to ms
                    logger.debug(
                        f"RTCP Receiver Report: ssrc={source_ssrc}:\n"
                        f"  loss={loss_percentage:.1f}% cumulative={cum_lost}\n"
                        f"  jitter={jitter_ms:.1f}ms\n"
                        f"  rtt={self.rtt_ms:.1f}ms\n",
                    )
                block_offset += 24


    ### ------- AUDIO ------- ###


    def audio_player(self, samplerate, channels):
        """Play audio frames from the queue with jitter buffering and soft limiting"""
        peer_buffer = {}
        with speaker.player(samplerate=samplerate, channels=channels, blocksize=960) as stream:
            mixed = np.zeros((960, channels), dtype="float32")
            while self.run:
                frames_to_mix = []

                # do small buffering to smooth out network jitter
                with self.audio_queue_out_lock:
                    for ssrc, buf in self.audio_queue_out.items():
                        if not buf:
                            peer_buffer[ssrc] = True
                            continue
                        if peer_buffer.get(ssrc, True):   # jitter buffer
                            if len(buf) >= MIXER_BUFFER:
                                peer_buffer[ssrc] = False
                            else:
                                continue  # keep buffering
                        gain = self.ssrc_to_gain.get(ssrc, 1)
                        frames_to_mix.append((buf.popleft(), gain))

                # mixing
                if frames_to_mix:
                    mixed.fill(0)
                    for frame, gain in frames_to_mix:
                        mixed += frame * (gain * self.gain_output)

                    # normalization
                    num_speakers = len(frames_to_mix)
                    if num_speakers > 1:
                        if self.fast_mixer:
                            np.tanh(mixed, out=mixed)
                        else:
                            mixed /= np.sqrt(num_speakers)
                            mixed *= 3.0
                            np.tanh(mixed, out=mixed)
                            mixed /= 0.99505   # tanh(3)
                    else:
                        np.clip(mixed, -1.0, 1.0, out=mixed)
                else:
                    mixed.fill(0)  # silence

                stream.play(mixed)


    def audio_recorder(self, samplerate, channels):
        """Record audio, denoise, and add frames to the queue"""
        with self.microphone.recorder(samplerate=samplerate, channels=channels) as rec:
            while self.run and self.recording:
                audio_data = rec.record(numframes=960)
                if self.denoiser:
                    audio_data = self.denoise(audio_data)
                if self.active_file_queues:
                    if self.mix_mic:
                        try:
                            self.raw_mic_queue.put_nowait(audio_data)
                        except queue.Full:
                            pass
                    continue
                try:
                    self.audio_queue_in.put_nowait(audio_data)
                except queue.Full:
                    try:   # drop old data and always keep newest
                        self.audio_queue_in.get_nowait()
                    except queue.Empty:
                        pass
                    self.audio_queue_in.put_nowait(audio_data)
        if self.recording:
            self.audio_queue_in.put(None)


    def send_mixer(self):
        """Mixer loop for file playback and mic integration"""
        next_time = time.perf_counter()
        while self.run:
            if not self.active_file_queues:
                self.send_mixer_thread = None
                self.mix_mic = False
                break
            queues = list(self.active_file_queues)
            frames_to_mix = []

            # collect from players
            for q in queues:
                try:
                    frame = q.get(timeout=0.005)
                    if frame is not None:
                        frames_to_mix.append(frame)
                except queue.Empty:
                    pass

            # collect from mic
            if self.mix_mic:
                try:
                    mic_data = self.raw_mic_queue.get_nowait()
                    if mic_data.dtype == np.int16:
                        mic_data = mic_data.astype("float32") / 32768.0
                    else:
                        mic_data = mic_data.astype("float32")
                    if mic_data.shape == (2, 960):
                        mic_data = mic_data.T
                    elif mic_data.shape == (1, 1920):
                        mic_data = mic_data.reshape(960, 2)
                    frames_to_mix.append(mic_data)
                except queue.Empty:
                    pass

            # mixing
            if frames_to_mix:
                mixed = np.zeros((960, 2), dtype="float32")
                for frame in frames_to_mix:
                    mixed += frame
                num_signals = len(frames_to_mix)
                if num_signals > 1:
                    if self.fast_mixer:
                        np.tanh(mixed, out=mixed)
                    else:
                        mixed /= np.sqrt(num_signals)
                        mixed *= 3.0
                        np.tanh(mixed, out=mixed)
                        mixed /= 0.99505   # tanh(3)
                else:
                    np.clip(mixed, -1.0, 1.0, out=mixed)
                self.audio_queue_in.put(mixed)

            next_time += 0.02
            sleep_time = next_time - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_time = time.perf_counter()


    def audio_file_player(self, path, mix=False):
        """Play audio file from path. Multi-thread safe with automatic mic handling"""

        def av_frames(path):
            import av
            container = av.open(path)
            try:
                if not container.streams.audio:
                    raise ValueError("No audio stream")
                resampler = av.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=48000)
                fifo = av.audio.fifo.AudioFifo()
                for frame in container.decode(container.streams.audio[0]):
                    for new_frame in resampler.resample(frame):
                        new_frame.pts = None
                        fifo.write(new_frame)
                        while fifo.samples >= 960:
                            arr = fifo.read(960).to_ndarray()
                            yield arr.astype(np.float32).T
            finally:
                container.close()

        def ffmpeg_frames(path):
            import subprocess
            proc = subprocess.Popen(
                ["ffmpeg", "-v", "quiet", "-i", path, "-f", "f32le", "-ac", "2", "-ar", "48000", "-"],
                stdout=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            try:
                chunk_size = 960 * 2 * 4
                first = True
                while True:
                    data = proc.stdout.read(chunk_size)
                    if len(data) < chunk_size:
                        if first:
                            raise ValueError("ffmpeg produced no audio")
                        break
                    first = False
                    yield np.frombuffer(data, dtype=np.float32).reshape(960, 2)
            finally:
                proc.kill()

        # init
        if not hasattr(self, "file_players_lock"):
            self.file_players_lock = threading.Lock()
            self.send_mixer_thread = None
            self.raw_mic_queue = queue.Queue(maxsize=10)
        self.mix_mic = self.mix_mic or mix
        player_queue = queue.Queue(maxsize=5)
        with self.file_players_lock:
            self.active_file_queues.append(player_queue)
            if self.send_mixer_thread is None:
                self.send_mixer_thread = threading.Thread(target=self.send_mixer, daemon=True)
                self.send_mixer_thread.start()

        # player
        try:
            self.playing = True
            for source in (av_frames, ffmpeg_frames):
                started = False
                frames = source(path)
                try:
                    for arr in frames:
                        started = True
                        if not self.playing:
                            break
                        player_queue.put(arr)
                    break
                except Exception as e:
                    if started:
                        logger.error(f"Error while playing audio file {path}: {e}")
                        break
                finally:
                    frames.close()
            else:
                logger.error(f"No available decoder for audio file {path}")
        except Exception as e:
            logger.error(f"Error while playing audio file {path}: {e}")

        # cleanup
        finally:
            with self.file_players_lock:
                if player_queue in self.active_file_queues:
                    self.active_file_queues.remove(player_queue)


    def stop_file_playback(self):
        """Stop playback of all audio file player threads"""
        if self.playing:
            self.playing = False
            try:   # empty queue
                while self.run:
                    self.audio_queue_in.get_nowait()
            except queue.Empty:
                pass


    def get_rtt(self):
        """Get rtt value in ms"""
        return self.rtt_ms


    def denoise(self, audio_data):
        """Process 20ms of audio, downmix to mono if needed, run RNNoise across two 10ms subframes, and return clean stereo"""
        if audio_data.dtype == np.int16:
            audio_float = audio_data.astype("float32") / 32768.0
        else:
            audio_float = audio_data.astype("float32")

        if audio_float.ndim == 1:
            mono = audio_float
        elif audio_float.shape[0] == 2 and audio_float.shape[1] == 960:
            mono = (audio_float[0, :] + audio_float[1, :]) * 0.5
        elif audio_float.shape[1] == 1:
            mono = audio_float[:, 0]
        else:
            mono = (audio_float[:, 0] + audio_float[:, 1]) * 0.5

        out = np.empty((960, 2), dtype=np.float32)
        for frame_index in range(2):
            start = frame_index * 480
            end = start + 480
            _, cleaned_float = self.denoiser.process_frame(mono[start:end])
            out[start:end, 0] = cleaned_float
            out[start:end, 1] = cleaned_float

        return out


    ### ------- VIDEO ------- ###


    def reset_video_receiver(self):
        """Reset video receiver state"""
        self.video_ts = None
        self.video_seq = None
        self.video_frame = bytearray()
        self.video_broken = False
        self.video_synced = False
        self.video_ext_seq = None   # extended highest sequence number
        self.video_prev_seq = 0
        self.video_received = 0
        self.video_cum_lost = 0
        self.twcc_packets = {}   # twcc arival times
        self.twcc_max = None
        self.twcc_next = None
        self.twcc_count = 0
        self.twcc_sent = 0


    def update_video_source(self, user_id, ssrcs):
        """Update video ssrcs of user and update ratchets, close mpv if video stopped"""
        for ssrc in [s for s, u in self.video_ssrc_to_userid.items() if u == user_id]:
            del self.video_ssrc_to_userid[ssrc]

        for ssrc in ssrcs:
            self.video_ssrc_to_userid[ssrc] = user_id
            ratchet = self.gateway.dave_session.get_key_ratchet(str(user_id))
            if ratchet is not None:
                if ssrc not in self.ssrc_to_decryptor:
                    self.ssrc_to_decryptor[ssrc] = dave.Decryptor()
                self.ssrc_to_decryptor[ssrc].transition_to_key_ratchet(ratchet, transition_expiry=10.0)

        if user_id == self.watch_user:
            if not ssrcs:
                self.stop_video_player()
            elif self.watch_ssrc not in ssrcs:   # stream restarted with different ssrc
                self.watch_ssrc = None
                self.reset_video_receiver()


    def send_rtcp_pli(self, media_ssrc):
        """Send RTCP picture loss indicator and ask sender for a keyframe"""
        payload = bytearray(12)
        payload[0] = 0x81   # v2, no padding, FMT=1
        payload[1] = 206   # PT=PSFB
        struct.pack_into(">H", payload, 2, 2)   # length in 32bit words
        struct.pack_into(">I", payload, 4, self.my_ssrc)
        struct.pack_into(">I", payload, 8, media_ssrc)
        payload = self.transport_encrypt(payload[:8], payload[8:])
        if not payload:
            logger.error(f"Unknown mode: {self.mode}")
            return
        try:
            self.udp.send(payload)
        except Exception:
            pass


    def parse_rtp_extensions(self, profile, body, is_video, arrival):
        """Get video orientation and transport-wide sequence number from RTP header extensions"""
        if profile != b"\xbe\xde":
            return
        offset = 0
        while offset < len(body):
            if body[offset] == 0:   # padding
                offset += 1
                continue
            ext_id = body[offset] >> 4
            length = (body[offset] & 0x0F) + 1
            if ext_id == 15:
                break
            ext_data = body[offset + 1:offset + 1 + length]

            if is_video and ext_id == 4 and length == 1:   # video orientation packet
                self.video_rotation = (ext_data[0] & 0x03) * 90

            elif ext_id == 5 and length == 2 and self.watch_user is not None:   # twcc packet
                sequence = int.from_bytes(ext_data, "big")
                if self.twcc_max is None:
                    unwrapped = sequence
                    self.twcc_max = sequence
                else:
                    diff = (sequence - self.twcc_max) & 0xFFFF
                    unwrapped = self.twcc_max + diff if diff < 0x8000 else self.twcc_max - (0x10000 - diff)
                    self.twcc_max = max(self.twcc_max, unwrapped)
                if self.twcc_next is None:
                    self.twcc_next = unwrapped
                if unwrapped >= self.twcc_next:
                    self.twcc_packets[unwrapped] = arrival
                if arrival - self.twcc_sent >= 0.1:   # send feedback every 100ms
                    self.twcc_sent = arrival
                    self.send_twcc()

            offset += 1 + length


    def rtcp_receiver_report_loop(self):
        """Send RTCP receiver report for watched video"""
        while self.run:
            ssrc = self.watch_ssrc
            if not ssrc or self.video_ext_seq is None:
                return
            expected = self.video_ext_seq - self.video_prev_seq
            lost = max(expected - self.video_received, 0)
            fraction = min(lost * 256 // expected, 255) if expected > 0 else 0
            self.video_cum_lost += lost
            self.video_prev_seq = self.video_ext_seq
            self.video_received = 0

            report = bytearray(32)
            report[0] = 0x81   # v2, no padding, RC=1
            report[1] = 201   # PT=RR
            struct.pack_into(">H", report, 2, 7)   # length in 32bit words
            struct.pack_into(">I", report, 4, self.my_ssrc)
            struct.pack_into(">I", report, 8, ssrc)
            report[12] = fraction
            report[13:16] = (self.video_cum_lost & 0xFFFFFF).to_bytes(3, "big")
            struct.pack_into(">I", report, 16, self.video_ext_seq & 0xFFFFFFFF)   # jitter, lsr and dlsr = 0

            payload = self.transport_encrypt(report[:8], report[8:])
            if not payload:
                time.sleep(RTCP_RR_SEND_DELAY)
                continue
            try:
                self.udp.send(payload)
            except Exception:
                pass
            time.sleep(RTCP_RR_SEND_DELAY)


    def send_twcc(self):
        """Send RTCP transport-wide congestion control feedback, server needs this to calculate bandwidth and pick video quality"""
        if not self.twcc_packets:
            return
        last = max(self.twcc_packets)
        base = max(self.twcc_next, last - 499)   # limit packet size
        reference = int((self.twcc_packets[min(s for s in self.twcc_packets if s >= base)] - self.twcc_epoch) / 0.064)   # 64ms units
        previous = reference * 0.064
        symbols = []   # 0 = not received, 1 = received with 1 byte delta, 2 = received with 2 byte delta
        deltas = bytearray()
        for sequence in range(base, last + 1):
            arrival = self.twcc_packets.get(sequence)
            if arrival is None:
                symbols.append(0)
                continue
            units = round((arrival - self.twcc_epoch - previous) / 0.00025)   # 250us units
            if 0 <= units <= 255:
                symbols.append(1)
                deltas.append(units)
            else:
                units = max(-32768, min(32767, units))
                symbols.append(2)
                deltas += units.to_bytes(2, "big", signed=True)
            previous += units * 0.00025

        chunks = bytearray()
        for i in range(0, len(symbols), 7):
            value = 0xC000
            for j, symbol in enumerate(symbols[i:i + 7]):
                value |= symbol << (12 - 2 * j)
            chunks += value.to_bytes(2, "big")

        packet = bytearray(12) + struct.pack(">HHI", base & 0xFFFF, len(symbols), ((reference & 0xFFFFFF) << 8) | self.twcc_count) + chunks + deltas
        packet += bytes(-len(packet) % 4)
        packet[0] = 0x8F   # v2, no padding, FMT=15
        packet[1] = 205   # PT=RTPFB
        struct.pack_into(">H", packet, 2, len(packet) // 4 - 1)   # length in 32bit words
        struct.pack_into(">I", packet, 4, self.my_ssrc)
        struct.pack_into(">I", packet, 8, self.watch_ssrc or 0)
        self.twcc_next = last + 1
        self.twcc_packets.clear()
        self.twcc_count = (self.twcc_count + 1) & 0xFF
        packet = self.transport_encrypt(packet[:8], packet[8:])
        if not packet:
            return
        try:
            self.udp.send(packet)
        except Exception:
            pass


    def set_mpv_rotation(self, degrees):
        """Rotate mpv video through ipc"""
        command = (json.dumps({"command": ["set_property", "video-rotate", degrees]}) + "\n").encode()
        try:
            if sys.platform == "win32":
                with open(self.mpv_ipc, "wb", buffering=0) as pipe:
                    pipe.write(command)
            else:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1)
                    sock.connect(self.mpv_ipc)
                    sock.sendall(command)
        except OSError:
            return False
        return True


    def request_keyframe(self):
        """Request keyframe from the watched user, max 1 per second"""
        while self.run:
            now = time.monotonic()
            if now - self.last_pli < 1:
                return
            self.last_pli = now
            if self.watch_ssrc:
                self.send_rtcp_pli(self.watch_ssrc)
            else:
                for ssrc, user_id in self.video_ssrc_to_userid.items():
                    if user_id == self.watch_user:
                        self.send_rtcp_pli(ssrc)
            time.sleep(RTCP_PLI_SEND_DELAY)


    def handle_video_packet(self, sequence, timestamp, marker, ssrc, payload):
        """Unpack H264 RTP payload, DAVE decrypt, and add to queue for mpv"""
        if not payload:
            return
        gap = self.video_seq is not None and sequence != (self.video_seq + 1) & 0xFFFF
        self.video_seq = sequence
        if self.video_ext_seq is None:
            self.video_ext_seq = sequence
            self.video_prev_seq = sequence - 1
        else:
            diff = (sequence - self.video_ext_seq) & 0xFFFF
            if diff < 0x8000:   # wraparound
                self.video_ext_seq += diff
        self.video_received += 1
        if timestamp != self.video_ts:   # new frame
            self.video_ts = timestamp
            self.video_frame = bytearray()
            self.video_broken = gap
        elif gap:
            self.video_broken = True

        # unpack NALs
        nal_type = payload[0] & 0x1F
        if 1 <= nal_type <= 23:   # single NAL
            self.video_frame += ANNEXB_START + payload
        elif nal_type == 24:   # STAP-A
            offset = 1
            while offset + 2 <= len(payload):
                size = int.from_bytes(payload[offset:offset + 2], "big")
                offset += 2
                self.video_frame += ANNEXB_START + payload[offset:offset + size]
                offset += size
        elif nal_type == 28 and len(payload) > 2:   # FU-A
            if payload[1] & 0x80:   # start of fragmented NAL
                self.video_frame += ANNEXB_START + bytes([(payload[0] & 0xE0) | (payload[1] & 0x1F)])
            elif not self.video_frame:   # joined in the middle of the frame
                self.video_broken = True
            self.video_frame += payload[2:]

        if not marker:
            return
        frame = bytes(self.video_frame)
        self.video_frame = bytearray()
        if self.video_broken:   # lost packet, wait for keyframe
            self.video_synced = False
            self.request_keyframe()
            return

        # DAVE
        if self.gateway.dave_protocol_version > 0:
            if not self.gateway.dave_session.has_established_group():
                return
            decryptor = self.ssrc_to_decryptor.get(ssrc)
            if decryptor is None:
                return
            frame = decryptor.decrypt(dave.MediaType.video, frame)
            if frame is None:
                return
            frame = bytes(frame)

        # start only from keyframe
        if not self.video_synced:
            if not any(nal[0] & 0x1F == 5 for nal in split_annex_b(frame, final=True)[0]):
                self.request_keyframe()
                return
            self.video_synced = True
        video_queue = self.video_queue
        if video_queue is None:
            return
        try:
            video_queue.put_nowait(frame)
        except queue.Full:   # wait for next keyframe
            self.video_synced = False


    def send_video_frame(self, ssrc, nals, timestamp):
        """DAVE encrypt frame, transport encrypt pack and send"""
        if self.gateway.dave_protocol_version > 0:
            if not self.gateway.dave_session.has_established_group():
                return True
            encrypted = self.encryptor.encrypt(
                dave.MediaType.video,
                ssrc,
                b"".join(ANNEXB_START + nal for nal in nals),
            )
            if encrypted is None:
                return True
            nals = split_annex_b(bytes(encrypted), final=True)[0]

        # pack NALs
        chunk_size = 1100 - 2
        packets = []
        for nal in nals:
            if len(nal) <= 1100:   # single NAL
                packets.append(nal)
                continue
            indicator = (nal[0] & 0xE0) | 28   # FU-A
            body = nal[1:]
            for i in range(0, len(body), chunk_size):
                fu_header = nal[0] & 0x1F
                if i == 0:
                    fu_header |= 0x80
                if i + chunk_size >= len(body):
                    fu_header |= 0x40
                packets.append(bytes([indicator, fu_header]) + body[i:i + chunk_size])

        # send packets
        for i, packet in enumerate(packets):
            self.video_sequence = (self.video_sequence + 1) & 0xFFFF
            header = bytearray(12)
            header[0] = 0x80   # v2, no padding, no extension, cc=0
            header[1] = 0x67 | (0x80 if i == len(packets) - 1 else 0)   # marker on last packet of frame
            struct.pack_into(">H", header, 2, self.video_sequence)
            struct.pack_into(">I", header, 4, timestamp)
            struct.pack_into(">I", header, 8, ssrc)
            payload = self.transport_encrypt(header, packet)
            if not payload:
                logger.error(f"Unknown mode: {self.mode}")
                continue
            try:
                self.udp.send(payload)
            except BlockingIOError:
                pass
            except Exception:
                return False
        return True


    def start_video_player(self, user_id=None):
        """Start video player for user_id or first user that sends video, currently only piped to mpv"""
        self.stop_video_player()
        if user_id is None:
            user_id = next(iter(self.video_ssrc_to_userid.values()), None)
        if user_id is None or int(user_id) not in self.video_ssrc_to_userid.values():
            return False
        if sys.platform == "win32":
            self.mpv_ipc = f"\\\\.\\pipe\\endcord-mpv-{os.getpid()}"
        else:
            self.mpv_ipc = os.path.join(peripherals.runtime_path, f"endcord-mpv-{os.getpid()}.sock")
        self.video_rotation = 0
        self.mpv_rotation = 0
        try:
            command = [
                self.mpv_path, "--no-terminal", "--no-audio", "--force-window=immediate",
                "--profile=low-latency", "--untimed", "--title=endcord video call",
                "--demuxer=lavf", "--demuxer-lavf-format=h264", f"--input-ipc-server={self.mpv_ipc}", "-",
            ]
            mpv_proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            logger.error(f"Failed to start mpv: {e}")
            return False

        self.reset_video_receiver()
        self.watch_ssrc = None
        self.video_queue = queue.Queue(maxsize=VIDEO_QUEUE_SIZE)
        self.mpv_proc = mpv_proc
        self.watch_user = int(user_id)

        threading.Thread(target=self.mpv_pipe, args=(mpv_proc, self.video_queue), daemon=True).start()
        threading.Thread(target=self.rtcp_receiver_report_loop, daemon=True).start()
        threading.Thread(target=self.request_keyframe, daemon=True).start()

        self.gateway.send({"op": 15, "d": {"any": 100}})   # tell server to send video
        self.request_keyframe()
        return True


    def stop_video_player(self, notify=True):
        """Close mpv, stop receiving video and notify server"""
        self.watch_user = None
        self.watch_ssrc = None

        mpv_proc = self.mpv_proc
        self.mpv_proc = None
        if not mpv_proc:
            return
        try:
            mpv_proc.stdin.close()
        except Exception:
            pass
        if mpv_proc.poll() is None:
            mpv_proc.terminate()

        if notify:
            self.gateway.send({"op": 15, "d": {"any": 0}})   # tell server to stop sending video


    def mpv_pipe(self, mpv_proc, frames):
        """Thread that writes frames from queue to mpv stdin"""
        while self.run and self.mpv_proc is mpv_proc and mpv_proc.poll() is None:
            try:
                frame = frames.get(timeout=0.5)
            except queue.Empty:
                frame = None
            if frame is not None:
                try:
                    mpv_proc.stdin.write(frame)
                    mpv_proc.stdin.flush()
                except (OSError, ValueError):
                    break
            if self.mpv_rotation != self.video_rotation and self.set_mpv_rotation(self.video_rotation):
                self.mpv_rotation = self.video_rotation

        if self.mpv_proc is mpv_proc:   # mpv closed by user
            self.stop_video_player(notify=self.run)


    def start_screen_sharing(self, source=None):
        """Capture screen with ffmpeg, encode to H264 and send it"""
        if self.ffmpeg_proc:
            return True
        stream = next((s for s in self.gateway.streams if s.get("rid") == "100"), None)
        if not stream:
            logger.error("Server did not provide video stream info")
            return False

        if source is None:
            if sys.platform == "win32":
                source = ["-f", "gdigrab", "-framerate", str(VIDEO_FPS), "-i", "desktop"]
            elif sys.platform == "darwin":
                source = ["-f", "avfoundation", "-capture_cursor", "1", "-framerate", str(VIDEO_FPS), "-i", "Capture screen 0:none"]
            else:
                source = ["-f", "x11grab", "-framerate", str(VIDEO_FPS), "-i", os.environ.get("DISPLAY", ":0")]

        command = [
            "ffmpeg", "-v", "error", *source,
            "-vf", f"scale=-2:{VIDEO_HEIGHT},format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline",
            "-g", str(VIDEO_FPS * 2),   # keyframe every 2s
            "-b:v", f"{VIDEO_BITRATE}k", "-maxrate", f"{VIDEO_BITRATE}k", "-bufsize", f"{VIDEO_BITRATE // 2}k",
            "-x264-params", "aud=1:repeat-headers=1",   # use access unit delimiters as frame boundaries
            "-f", "h264", "-",
        ]
        try:
            ffmpeg_proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as e:
            logger.error(f"Failed to start ffmpeg: {e}")
            return False

        self.screen_ssrc = int(stream["ssrc"])
        self.screen_rtx_ssrc = int(stream.get("rtx_ssrc", 0))
        if self.gateway.dave_protocol_version > 0:
            self.encryptor.assign_ssrc_to_codec(self.screen_ssrc, dave.Codec.h264)
        self.ffmpeg_proc = ffmpeg_proc

        self.gateway.send_video_sources(True, self.screen_ssrc, self.screen_rtx_ssrc)
        threading.Thread(target=self.screen_share_loop, args=(ffmpeg_proc, self.screen_ssrc), daemon=True).start()
        return True


    def stop_screen_sharing(self, notify=True):
        """Stop screen capturing and notify server"""
        if not self.ffmpeg_proc:
            return
        self.ffmpeg_proc.kill()
        self.ffmpeg_proc = None
        if notify:
            self.gateway.send_video_sources(False, self.screen_ssrc, self.screen_rtx_ssrc)


    def screen_share_loop(self, ffmpeg_proc, ssrc):
        """Thread that reads H264 stream from ffmpeg, split it into frames and send them"""
        buffer = bytearray()
        access_unit = []
        start_time = time.monotonic()
        ok = True

        while ok and self.run and self.ffmpeg_proc is ffmpeg_proc:
            data = ffmpeg_proc.stdout.read1(65536)
            if not data:
                break
            buffer += data
            nals, cut = split_annex_b(buffer)
            del buffer[:cut]
            for nal in nals:
                if nal[0] & 0x1F == 9 and access_unit:   # frame is complete
                    timestamp = (self.video_timestamp_base + int((time.monotonic() - start_time) * 90000)) & 0xFFFFFFFF   # 90khz
                    ok = self.send_video_frame(ssrc, access_unit, timestamp)
                    access_unit = []
                    if not ok:
                        break
                access_unit.append(nal)

        if self.ffmpeg_proc is ffmpeg_proc:   # ffmpeg died or sending failed
            self.stop_screen_sharing(notify=self.run)
