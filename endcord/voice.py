import json
import logging
import queue
import random
import socket
import struct
import threading
import time
import urllib
import urllib.parse

import av
import nacl.bindings
import socks
import websocket

# safely import soundcard, in case there is no sound system
try:
    import soundcard
    have_soundcard = True
except (AssertionError, RuntimeError):
    have_soundcard = False

DISCORD_HOST = "discord.com"
LOCAL_MEMBER_COUNT = 50   # members per guild, CPU-RAM intensive
VOICE_FLAGS = 3   # CLIPS_ENABLED and ALLOW_VOICE_RECORDING
UDP_TIMEOUT = 10
logger = logging.getLogger(__name__)
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

class Gateway():
    """Methods for fetching and sending data to Discord voice gateway through websocket"""

    def __init__(self, voice_gateway_data, my_id, mute, user_agent, proxy=None):
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
        self.mute = mute
        self.media_session_id = None
        self.connect()


    def create_udp_socket(self):
        """Create udp soocket to the server"""
        if self.proxy.scheme:
            self.udp = socks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.set_proxy(
                proxy_type=socks.SOCKS5 if "socks" in self.proxy.scheme.lower() else socks.HTTP,
                addr=self.proxy.hostname,
                port=self.proxy.port,
            )
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
            self.voice_handler = VoiceHandler(self, self.udp, self.secret_key, self.selected_mode)
            self.voice_handler.start()


    def stop_voice_handler(self):
        """Stoo voice handler without stopping gateway"""
        try:
            self.udp.close()
        except Exception:
            pass
        if self.voice_handler:
            self.voice_handler.stop()


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


    def send(self, request):
        """Send data to gateway"""
        try:
            self.ws.send(json.dumps(request))
        except websocket._exceptions.WebSocketException:
            self.disconnect()


    def receiver(self):
        """Receive and handle all traffic from gateway, should be run in a thread"""
        logger.info("Receiver started")
        while self.run:
            try:
                ws_opcode, data = self.ws.recv_data()
            except (
                ConnectionResetError,
                websocket._exceptions.WebSocketConnectionClosedException,
                OSError,
            ):
                break
            if ws_opcode == 8 and len(data) >= 2:
                if not data:
                    break
                code = struct.unpack("!H", data[0:2])[0]
                reason = data[2:].decode("utf-8", "replace")
                if code in (4022, 4014):   # call terminated, disconnected
                    break
                logger.warning(f"Gateway error code: {code}, reason: {reason}")
                break
            try:
                try:
                    response = json.loads(data)
                    opcode = response["op"]
                except ValueError:
                    response = None
                    opcode = None
            except Exception as e:
                logger.warning(f"Receiver error: {e}")
                break
            # debug_events
            # from endcord import debug
            # debug.save_json(response, f"event_{opcode}.json", False)

            self.sequence = max(response.get("seq", 0), self.sequence)

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
                logger.debug("Received: READY event")
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
                self.state = 2
                logger.debug("Received: SESSION DESCRIPTION event, voice gateway is ready")
                self.send_speaking(0)
                self.start_voice_handler()

            elif opcode == 14:   # SESSION UPDATE
                data = response["d"]
                self.audio_codec = data["audio_codec"]
                self.video_codec = data["video_codec"]
                self.media_session_id = data["media_session_id"]
                self.update = True
                logger.debug("Received: SESSION UPDATE event")

            elif opcode == 11:   # CLIENT CONNECT
                data = response["d"]
                for user_id in data["user_ids"]:
                    self.call_buffer.append({
                        "op": "USER_JOIN",
                        "user_id": user_id,
                    })

            elif opcode == 13:   # CLIENT DISCONNECT
                data = response["d"]
                self.call_buffer.append({
                    "op": "USER_LEAVE",
                    "user_id": data["user_id"],
                })

            elif opcode == 5:   # SPEAKING
                data = response["d"]
                self.call_buffer.append({
                    "op": "USER_SPEAKING",
                    "user_id": data["user_id"],
                    "speaking": bool(data["user_id"]),
                })

        logger.info("Receiver stopped")
        self.disconnect()


    def send_heartbeat(self):
        """Send heartbeat to voice gateway, will stop if response is not received, should be run in a thread"""
        logger.info(f"Heartbeater started, interval={self.heartbeat_interval/1000} s")
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

        logger.info("Heartbeater stopped")
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
        logger.debug("Identifying with voice gateway")


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
        logger.debug("Sent SELECT PROTOCOL event")


    def send_speaking(self, speaking_packet_delay):
        """Send SPEAKING event"""
        payload = {
            "op": 5,
            "d": {
                "speaking": 1,
                "delay": speaking_packet_delay,
                "ssrc": self.ssrc,
            },
        }
        self.send(payload)
        logger.debug("Sent SPEAKING event")


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
            logger.info("Gateway disconnected")


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


    def set_mute(self, state):
        """Set muted state, will stop recording and sending sound"""
        self.mute = state



class VoiceHandler:
    """Voice call sound receiver and transmitter, player and recorder"""

    def __init__(self, gateway, udp, secret_key, encryption_mode):
        self.gateway = gateway
        self.udp = udp
        self.secret_key = bytes(secret_key)
        self.mode = encryption_mode
        self.audio_queue = queue.Queue(maxsize=10)
        self.opus_decoder = av.codec.CodecContext.create("opus", "r")
        # self.opus_decoder.sample_rate = 48000
        # self.opus_decoder.channels = 2
        # self.opus_decoder.open()


    def start(self):
        """Staart receiver and transmitter loops in threads"""
        if self.mode not in ("aead_aes256_gcm_rtpsize", "aead_xchacha20_poly1305_rtpsize"):
            logger.error(f"Unsupported encryption mode {self.mode}")
            self.gateway.disconnect()
            return
        self.run = True

        if have_sound:
            # start player
            self.audio_thread = threading.Thread(target=self.audio_player, args=(48000, 2), daemon=True)
            self.audio_thread.start()

            # start receiver
            self.receiver_thread = threading.Thread(target=self.receiver_loop, daemon=True)
            self.receiver_thread.start()
        else:
            logger.warning("Could not find any speakers or sound system is not running")


    def stop(self):
        """Stop voice handler"""
        self.run = False
        self.audio_queue.put(None)
        try:
            self.udp.close()
        except Exception:
            pass


    def receiver_loop(self):
        """Receive, unpack, decrypt, decode received data, and put it to queue"""
        logger.debug("Voice receiver started")
        while self.run:
            # receive
            try:
                data = self.udp.recv(4096)
            except OSError as e:
                logger.info(f"UDP socket closed or error: {e}")
                break
            except Exception as e:
                logger.error(f"UDP receive error: {e}")
                break
            if not data:
                continue

            # unpack for different rtpsizes
            data = bytearray(data)
            sequence, timestamp, ssrc = rtp_unpacker.unpack_from(data[:12])

            cutoff = 12 + (data[0] & 0b00001111) * 4
            if data[0] & 0b00010000:
                cutoff += 4

            header = data[:cutoff]
            counter = data[-4:]
            ciphertext = data[cutoff:-4]


            if 200 <= data[1] <= 204:   # RTCP
                pass

            else:   # RTP
                # decrypt
                try:
                    if self.mode == "aead_aes256_gcm_rtpsize":
                        nonce = bytearray(12)
                        nonce[:4] = counter
                        payload = nacl.bindings.crypto_aead_aes256gcm_decrypt(bytes(ciphertext), bytes(header), bytes(nonce), self.secret_key)[8:]
                    elif self.mode == "aead_xchacha20_poly1305_rtpsize":
                        nonce = bytearray(24)
                        nonce[:4] = counter
                        payload = nacl.bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(bytes(ciphertext), bytes(header), bytes(nonce), self.secret_key)[8:]
                except Exception as e:
                    logger.error(f"Decryption failed for mode: {self.mode}. Error: {e}")
                    continue

                # decode opus and add to audio queue
                try:
                    av_packet = av.packet.Packet(payload)
                    frames = self.opus_decoder.decode(av_packet)
                    for frame in frames:
                        self.audio_queue.put(frame)
                except Exception as e:
                    logger.error(f"PyAV opus decoding failed. Error: {e}")
        self.gateway.disconnect()


    def audio_player(self, samplerate, channels):
        """Play audio frames from the queue"""
        with speaker.player(samplerate=samplerate, channels=channels, blocksize=1152) as stream:
            while self.run:
                frame = self.audio_queue.get()
                if frame is None:
                    break
                stream.play(frame.to_ndarray().astype("float32").T)
