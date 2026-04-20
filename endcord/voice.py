# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import json
import logging
import os
import queue
import random
import socket
import struct
import threading
import time
import urllib
import urllib.parse

import av
import dave
import nacl.bindings
import numpy as np
import socks
import websocket

# import davey   # using dave.py instead


# safely import soundcard, in case there is no sound system
try:
    import soundcard
    have_soundcard = True
except (AssertionError, RuntimeError):
    have_soundcard = False

DISCORD_HOST = "discord.com"
BASE_SOUND_GAIN = 2.0
VOICE_FLAGS = 2   # ALLOW_VOICE_RECORDING
UDP_TIMEOUT = 10
OPUS_MODE = os.environ.get("ENDCORD_VOICE_OPUS_MODE", "voip")
OPUS_SILENCE = bytes([0xF8, 0xFF, 0xFE])
SILENCE_BUFFER = 5
MAX_SILENCE = 10
RTCP_SEND_DELAY = 5   # set to 0 to disable RTCP SR
CODECS = [
    # pyav depends on ffmpeg, and its usually built without encode for av1 and vp9
    {"name":"opus", "type":"audio", "priority":1000, "payload_type":120},
    # video disabled for now
    # {"name":"AV1", "type":"video", "priority":1000, "payload_type":101, "rtx_payload_type":102, "encode":False, "decode":True},
    # {"name":"H264", "type":"video", "priority":2000, "payload_type":103, "rtx_payload_type":104, "encode":True, "decode":True},
    # # h265 only in discord beta
    # {"name":"VP8", "type":"video", "priority":3000, "payload_type":105, "rtx_payload_type":106, "encode":True, "decode":True},
    # {"name":"VP9", "type":"video", "priority":4000, "payload_type":107, "rtx_payload_type":108, "encode":False, "decode":True},
]
logger = logging.getLogger(__name__)
rtp_unpacker = struct.Struct(">xxHII")

# get speaker
if have_soundcard:
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


class Gateway():
    """Methods for fetching and sending data to Discord voice gateway through websocket"""

    def __init__(self, voice_gateway_data, my_id, volume_input, volume_output, user_agent, proxy=None, custom_mic=None, silence=-30):
        self.voice_gateway_data = voice_gateway_data
        self.guild_id = voice_gateway_data["guild_id"]
        self.channel_id = voice_gateway_data["channel_id"]
        self.my_id = my_id
        self.header = [
            "Connection: Upgrade",
            "Sec-WebSocket-Extensions: permessage-deflate",
            f"User-Agent: {user_agent}",
        ]
        self.proxy = urllib.parse.urlsplit(proxy)
        self.run = True
        self.state = 0
        self.heartbeat_received = True
        self.sequence = 1
        self.resumable = False
        self.voice_handler = None
        self.voice_handler_thread = None
        self.call_buffer = []
        self.media_session_id = None
        self.udp = None

        self.dave_session = dave.Session()
        self.dave_session.init(
            version=dave.get_max_supported_protocol_version(),
            group_id=int(self.channel_id),
            self_user_id=str(self.my_id),
        )
        # self.dave_session = davey.DaveSession(   # fix_davey
        #     protocol_version=davey.DAVE_PROTOCOL_VERSION,
        #     user_id=int(self.my_id),
        #     channel_id=int(self.channel_id),
        # )
        self.dave_protocol_version = 0   # no dave yet
        self.pending_transition_id = None
        self.known_user_ids = set()
        self.known_user_ids.add(str(my_id))   # fix_davey - uses int ids everywhere
        self.ssrc_cache = set()

        self.volume_input = volume_input
        self.volume_output = volume_output
        self.custom_mic = custom_mic
        self.silence_threshold = silence

        self.connect()


    def create_udp_socket(self):
        """Create udp soocket to the server"""
        if self.proxy.scheme:
            if "socks" in self.proxy.scheme.lower():
                self.udp = socks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp.set_proxy(
                    proxy_type=socks.SOCKS5,
                    addr=self.proxy.hostname,
                    port=self.proxy.port,
                )
            else:
                self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                logger.warning("Only SOCKS5 proxy can be used for voice calls. This call will be made without proxy!")
        else:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.connect((self.server_ip, self.server_port))
        self.udp.settimeout(UDP_TIMEOUT)
        logger.debug("Created udp socket")


    def send_ip_discovery(self):
        """Send ip discorvery packet to the server"""
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
            logger.debug("Rceived IP discovery packet")
        except socket.timeout:
            logger.error(f"Failed to receive IP discovery: timeout after {UDP_TIMEOUT} s")
            self.disconnect()


    def start_voice_handler(self):
        """Initialize and start voice handler"""
        if not self.voice_handler:
            self.voice_handler = VoiceHandler(
                self,
                self.my_id,
                self.ssrc,
                self.udp,
                self.secret_key,
                self.selected_mode,
                self.volume_input,
                self.volume_output,
                custom_mic=self.custom_mic,
                silence=self.silence_threshold,
            )
            self.voice_handler.start()

        while self.ssrc_cache:   # load cached ssrc-id pairs received before voice handler was initialised
            ssrc, user_id = self.ssrc_cache.pop()
            self.voice_handler.add_ssrc_mapping(ssrc, user_id)


    def stop_voice_handler(self):
        """Stop voice handler without stopping gateway"""
        try:
            self.udp.close()
        except Exception:
            pass
        if self.voice_handler:
            self.voice_handler.stop()
            self.voice_handler = None


    def connect(self):
        """Create initial connection to Discord gateway"""
        gateway_url = "wss://" + self.voice_gateway_data["endpoint"]
        self.ws = websocket.WebSocket()
        if self.proxy.scheme:
            self.ws.connect(
                gateway_url + "/?v=8",
                header=self.header,
                proxy_type=self.proxy.scheme,
                http_proxy_host=self.proxy.hostname,
                http_proxy_port=self.proxy.port,
            )
        else:
            self.ws.connect(gateway_url + "/?v=8", header=self.header)
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
                self.start_voice_handler()

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

            elif opcode == 13:   # CLIENT DISCONNECT
                data = response["d"]
                self.call_buffer.append({
                    "op": "USER_LEAVE",
                    "user_id": data["user_id"],
                })
                self.known_user_ids.discard(str(data["user_id"]))

            elif opcode == 5:   # SPEAKING
                data = response["d"]
                self.call_buffer.append({
                    "op": "USER_SPEAKING",
                    "user_id": data["user_id"],
                    "speaking": bool(data["user_id"]),
                })
                if "ssrc" in data:
                    if self.voice_handler:
                        self.voice_handler.add_ssrc_mapping(int(data["ssrc"]), int(data["user_id"]))
                        # self.voice_handler.ssrc_to_userid[ssrc] = int(data["user_id"])   # fix_davey
                    else:   # add to cache until voice_handler object is created
                        self.ssrc_cache.add((int(data["ssrc"]), int(data["user_id"])))

            # DAVE OPCODES
            elif opcode == 21:  # DAVE_PROTOCOL_PREPARE_TRANSITION
                data = response["d"]
                transition_id = data["transition_id"]
                self.pending_transition_id = transition_id
                self.send_dave_ready_for_transition(transition_id)

            elif opcode == 22:  # DAVE_PROTOCOL_EXECUTE_TRANSITION
                if self.voice_handler:
                    self.voice_handler.update_ratchets(self.dave_session)

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
                    # self.dave_session.reinit(   # fix_davey
                    #     protocol_version=data.get("dave_protocol_version", 1),
                    #     user_id=int(self.my_id),
                    #     channel_id=int(self.channel_id),
                    # )
                    self.send_dave_mls_key_package()

            elif opcode == 25:  # DAVE_MLS_EXTERNAL_SENDER_PACKAGE
                self.dave_session.set_external_sender(response)
                self.send_dave_mls_key_package()

            elif opcode == 27:  # DAVE_MLS_PROPOSALS
                result = self.dave_session.process_proposals(response, self.known_user_ids)
                if result is not None:
                    self.send_dave_mls_commit_welcome(result)
                # op_type_int = struct.unpack_from(">B", response, 0)[0]   # fix_davey
                # op_type = davey.ProposalsOperationType.append if op_type_int == 0 else davey.ProposalsOperationType.revoke
                # result = self.dave_session.process_proposals(op_type, response[1:])
                # if result is not None:
                #     self.send_dave_mls_commit_welcome(result.commit, result.welcome)

            elif opcode == 29:  # DAVE_MLS_ANNOUNCE_COMMIT_TRANSITION
                transition_id = struct.unpack_from(">H", response, 0)[0]
                commit = response[2:]
                result = self.dave_session.process_commit(commit)
                if isinstance(result, dave.RejectType):
                    logger.warning(f"Invalid commit: {result}")
                    self.send_dave_mls_invalid_commit_welcome(transition_id)
                    self.send_dave_mls_key_package()
                else:
                    if self.voice_handler:
                        self.voice_handler.update_ratchets(self.dave_session)
                    if transition_id != 0:
                        self.send_dave_ready_for_transition(transition_id)
                # try:   # fix_davey
                #     self.dave_session.process_commit(commit)
                #     self.send_dave_ready_for_transition(transition_id)
                # except Exception as e:
                #     logger.warning(f"Invalid commit: {e}")
                #     self.send_dave_mls_invalid_commit_welcome(transition_id)
                #     self.send_key_package()

            elif opcode == 30:  # DAVE_MLS_WELCOME
                transition_id = struct.unpack_from(">H", response, 0)[0]
                result = self.dave_session.process_welcome(response[2:], self.known_user_ids)
                if result is None:
                    logger.warning("Invalid welcome")
                    self.send_dave_mls_invalid_commit_welcome(transition_id)
                    self.send_dave_mls_key_package()
                else:
                    if self.voice_handler:
                        self.voice_handler.update_ratchets(self.dave_session)
                    self.send_dave_ready_for_transition(transition_id)
                # try:   # fix_davey
                #     self.dave_session.process_welcome(response[2:])
                # except Exception as e:
                #     logger.warning(f"Invalid welcome: {e}")
                #     self.send_dave_mls_invalid_commit_welcome(transition_id)
                #     self.send_key_package()

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
        heartbeat_sent_time = time.time()
        while self.run:
            if time.time() - heartbeat_sent_time >= heartbeat_interval_rand:
                self.send({
                    "op": 3,
                    "d": {
                        "t": int(time.time()) * 1000,
                        "seq_ack": self.sequence,
                    },
                })
                heartbeat_sent_time = time.time()
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
                "video": True,
                "max_dave_protocol_version": dave.get_max_supported_protocol_version(),
                # "max_dave_protocol_version": davey.DAVE_PROTOCOL_VERSION,   # fix_davey
                "streams": [{
                    "type": "video",
                    "rid": "100",
                    "quality": 100,
                },{
                    "type": "video",
                    "rid": "50",
                    "quality":50,
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
        # key_package = self.dave_session.get_serialized_key_package()   # fix_davey
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


    def send_speaking(self):
        """Send SPEAKING event"""
        payload = {
            "op": 5,
            "d": {
                "speaking": 1,
                "delay": 0,
                "ssrc": self.ssrc,
            },
        }
        self.send(payload)


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
            if self.voice_handler:
                self.voice_handler.stop()
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
        if self.voice_handler:
            return self.voice_handler.get_rtt()
        return 0.0


    def set_volumes(self, volume_input, volume_output):
        """Set volumes levels"""
        self.volume_input = volume_input
        self.volume_output = volume_output
        if self.voice_handler:
            self.voice_handler.set_volumes(volume_input, volume_output)


    def live_mic_switch(self, new_mic):
        """Change microphone while in the stream"""
        self.voice_handler.live_mic_switch(new_mic)


class VoiceHandler:
    """Voice call sound receiver, transmitter, player and recorder"""

    def __init__(self, gateway, my_id, my_ssrc, udp, secret_key, encryption_mode, volume_input, volume_output, custom_mic=None, silence=-30):
        self.gateway = gateway
        self.my_id = my_id
        self.my_ssrc = my_ssrc
        self.udp = udp

        self.secret_key = bytes(secret_key)
        self.mode = encryption_mode
        self.dave_session = gateway.dave_session
        self.ssrc_to_decryptor = {}
        self.ssrc_to_userid = {}
        self.encryptor = dave.Encryptor()

        self.audio_queue_out = queue.Queue(maxsize=10)   # 0.2s buffer
        self.audio_queue_in = queue.Queue(maxsize=25)   # 0.5s buffer

        self.opus_decoder = av.codec.CodecContext.create("opus", "r")
        self.opus_encoder = av.codec.CodecContext.create("opus", "w")
        self.opus_encoder.sample_rate = 48000
        self.opus_encoder.layout = "stereo"
        self.opus_encoder.format = "flt"
        self.opus_encoder.options = {
            "application": OPUS_MODE,
            "vbr": "off" if OPUS_MODE == "audio" else "on",
        }
        if OPUS_MODE != "audio":
            self.opus_encoder.bit_rate = 96000

        self.microphone = init_microphone(custom_mic)
        self.gain_input = volume_to_gain(volume_input, boost=1)
        self.gain_output = volume_to_gain(volume_output, boost=BASE_SOUND_GAIN)
        self.silence_threshold = 10 ** (silence / 20)

        self.run = False
        self.recording = False
        self.rtp_sequence = random.randint(0, 0xFFFF)   # must be random per rfc3550
        self.rtp_timestamp = random.randint(0, 0xFFFFFFFF)
        self.transport_counter = 0
        self.packets_sent = 0
        self.bytes_sent = 0


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
            if self.microphone:
                self.transmitter_thread = threading.Thread(target=self.transmitter_loop, daemon=True)
                self.transmitter_thread.start()
                self.audio_thread_rec = threading.Thread(target=self.audio_recorder, args=(48000, 2), daemon=True)
                self.audio_thread_rec.start()

            # start rtcp sender
            if RTCP_SEND_DELAY:
                self.rtcp_sender_thread = threading.Thread(target=self.rtcp_sender_report_loop, daemon=True)
                self.rtcp_sender_thread.start()

        else:
            logger.warning("Could not find any speakers or sound system is not running")


    def stop(self):
        """Stop voice handler"""
        if not self.run:
            return
        self.run = False
        self.audio_queue_out.put(None)
        self.audio_queue_in.put(None)
        try:
            self.udp.close()
        except Exception:
            pass


    def set_volumes(self, volume_input, volume_output):
        """Set volumes levels"""
        self.gain_input = volume_to_gain(volume_input, boost=1)
        self.gain_output = volume_to_gain(volume_output, boost=BASE_SOUND_GAIN)


    def live_mic_switch(self, new_mic):
        """Change microphone while in the stream"""
        if not self.microphone:
            return
        if not self.audio_thread_rec:
            return
        new_mic_obj = init_microphone(new_mic)
        if not new_mic_obj:
            return
        self.recording = False
        self.audio_thread_rec.join()
        self.recording = True
        self.microphone = new_mic_obj
        self.audio_thread_rec = threading.Thread(target=self.audio_recorder, args=(48000, 2), daemon=True)
        self.audio_thread_rec.start()


    def update_ratchets(self, session):
        """Update DAVE ratchets"""
        # update for other users
        for ssrc, user_id in self.ssrc_to_userid.items():
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


    def receiver_loop(self):
        """Receive data, unpack, transport decrypt, DAVE decrypt, opus decoee, and put it to queue"""
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

            # skip non-opus payloads and parse RTCP
            if data[1] & 0x7F != 0x78:
                self.handle_rtcp(data)
                continue

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
                payload = payload[ext_body_len:]

            # DAVE
            if self.gateway.dave_protocol_version > 0:
                if not self.gateway.dave_session.has_established_group():
                    continue
                decryptor = self.ssrc_to_decryptor.get(ssrc)
                if decryptor is None:
                    logger.debug(f"Unknown ssrc {ssrc}")
                    continue
                # logger.info(("BEFORE DAVE", bytes(payload)))
                decrypted = decryptor.decrypt(dave.MediaType.audio, bytes(payload))
                if decrypted is None:
                    continue
                payload = decrypted

            # is_dave_encrypted = len(payload) >= 2 and payload[-2] == 0xFA and payload[-1] == 0xFA   # fix_davey
            # if self.gateway.dave_protocol_version > 0 and self.dave_session.ready and is_dave_encrypted:
            #     user_id = self.ssrc_to_userid.get(ssrc)
            #     if user_id is not None:
            #         try:
            #             payload = self.dave_session.decrypt(user_id, davey.MediaType.audio, payload)
            #         except Exception as e:
            #             logger.error(f"DAVE decryption failed: {e}")
            #             continue
            #     else:
            #         logger.debug(f"Unknown ssrc {ssrc}")
            #         continue

            # opus
            try:
                av_packet = av.packet.Packet(payload)
                frames = self.opus_decoder.decode(av_packet)
                for frame in frames:
                    self.audio_queue_out.put(frame)
            except Exception as e:
                logger.error(f"PyAV opus decoding failed. Error: {e}")

        self.gateway.disconnect()


    def transmitter_loop(self):
        """Take data from queue, opus encode, DAVE encrypt, transport encrypt, pack and send it"""
        # speaking = False
        skipped_samples = 0
        silence_counter = 0
        max_silence_packets = MAX_SILENCE + SILENCE_BUFFER
        next_send_time = time.perf_counter()
        while self.run:
            payload = None

            # wait to send or drop if late, then get data
            now = time.perf_counter()
            if now > next_send_time + 0.02:   # drop if late more than one frame
                next_send_time = now   # resync
                try:
                    self.audio_queue_in.get_nowait()
                except queue.Empty:
                    pass
                continue
            if now < next_send_time:
                time.sleep(next_send_time - now)
            try:
                audio_data = self.audio_queue_in.get_nowait()
                if audio_data is None:
                    break
            except queue.Empty:
                payload = OPUS_SILENCE
            if not self.gain_input:
                payload = OPUS_SILENCE
            next_send_time += 0.02   # 20ms = 960 samples

            # silence detection
            if not payload and self.silence_threshold and detect_silence(audio_data, threshold=self.silence_threshold):
                if silence_counter > max_silence_packets:
                    # if speaking:   # seems theres no need to send
                    #     self.gateway.send_speaking()
                    skipped_samples += 960
                    continue
                silence_counter += 1
                if silence_counter > SILENCE_BUFFER:   # 3 silence frames in a row = confident silence
                    payload = OPUS_SILENCE
            else:
                silence_counter = 0

            # opus
            if payload is None:
                audio_data *= self.gain_input
                self.rtp_timestamp += skipped_samples
                # if not speaking:   # seems theres no need to send
                #     self.gateway.send_speaking()
                frame = av.AudioFrame.from_ndarray(
                    np.expand_dims(audio_data.astype("float32").reshape(-1), axis=0),
                    format="flt",
                    layout="stereo",
                )
                frame.sample_rate = 48000
                payload = self.opus_encoder.encode(frame)[0]   # always returns 1 packet
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

            # RTP + transport
            self.transport_counter = (self.transport_counter + 1) & 0xFFFFFFFF
            counter = struct.pack(">I", self.transport_counter)
            try:
                if self.mode == "aead_aes256_gcm_rtpsize":
                    nonce = bytearray(12)
                    nonce[:4] = counter
                    payload = nacl.bindings.crypto_aead_aes256gcm_encrypt(bytes(payload), bytes(header), bytes(nonce), self.secret_key)
                elif self.mode == "aead_xchacha20_poly1305_rtpsize":
                    nonce = bytearray(24)
                    nonce[:4] = counter
                    payload = nacl.bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(bytes(payload), bytes(header), bytes(nonce), self.secret_key)
                else:
                    logger.error(f"Unknown mode: {self.mode}")
                    skipped_samples += 960
                    continue
            except Exception as e:
                logger.error(f"Encryption failed; mode: {self.mode}; error: {e}")
                skipped_samples += 960
                continue
            payload = bytes(header) + payload + counter

            # send
            try:
                self.udp.send(payload)
            except Exception:
                break
            self.packets_sent += 1
            self.bytes_sent += opus_payload_size

        self.gateway.disconnect()


    def audio_player(self, samplerate, channels):
        """Play audio frames from the queue"""
        with speaker.player(samplerate=samplerate, channels=channels, blocksize=1152) as stream:
            while self.run:
                frame = self.audio_queue_out.get()
                if frame is None:
                    break
                audio_data = frame.to_ndarray().astype("float32").T
                audio_data *= self.gain_output
                stream.play(audio_data)


    def audio_recorder(self, samplerate, channels):
        """Record audio and add frames to the queue"""
        with self.microphone.recorder(samplerate=samplerate, channels=channels) as rec:
            while self.run and self.recording:
                audio_data = rec.record(numframes=960)
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
            # logger.info(("SR", payload))

            header = payload[:8]
            self.transport_counter = (self.transport_counter + 1) & 0xFFFFFFFF
            counter = struct.pack(">I", self.transport_counter)
            if self.mode == "aead_aes256_gcm_rtpsize":
                nonce = bytearray(12)
                nonce[:4] = counter
                payload = nacl.bindings.crypto_aead_aes256gcm_encrypt(bytes(payload), bytes(header), bytes(nonce), self.secret_key)
            elif self.mode == "aead_xchacha20_poly1305_rtpsize":
                nonce = bytearray(24)
                nonce[:4] = counter
                payload = nacl.bindings.crypto_aead_xchacha20poly1305_ietf_encrypt(bytes(payload), bytes(header), bytes(nonce), self.secret_key)
            else:
                logger.error(f"Unknown mode: {self.mode}")
                time.sleep(RTCP_SEND_DELAY)
                continue
            payload = bytes(header) + payload + counter

            try:
                self.udp.send(payload)
            except Exception:
                break
            time.sleep(RTCP_SEND_DELAY)


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
            # logger.info(("RR", payload))
            block_offset = 8   # skip header and sender ssrc
            report_count = payload[0] & 0x1F
            for _ in range(report_count):
                if block_offset + 24 > len(payload):
                    break
                lsr = struct.unpack_from(">I", payload, block_offset + 16)[0]
                dlsr = struct.unpack_from(">I", payload, block_offset + 20)[0]
                if lsr != 0:
                    # now_ntp = time.time() + 2208988800
                    # now_mid = int((now_ntp * 65536)) & 0xFFFFFFFF   # lsr and dlsr are in 1/65536
                    # self.rtt_ms = ((now_mid - lsr - dlsr) / 65536) * 1000   # RTT = NOW - LSR - DLSR
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
                    # logger.info(f"lsr={lsr} dlsr={dlsr} now_mid={now_mid} rtt_units={rtt_units}")
                    logger.debug(
                        f"RTCP Receiver Report: ssrc={source_ssrc}: "
                        f"loss={loss_percentage:.1f}% cumulative={cum_lost} "
                        f"jitter={jitter_ms:.1f}ms "
                        f"rtt={self.rtt_ms:.1f}ms ",
                    )
                block_offset += 24


    def get_rtt(self):
        """Get rtt value in ms"""
        return self.rtt_ms
