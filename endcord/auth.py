# Copyright (C) 2025-2026 SparkLost
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.

import base64
import http.client
import logging
import random
import socket
import ssl
import struct
import threading
import time
import urllib.parse

import websocket

try:
    import orjson as json
except ImportError:
    try:
        import ujson as json
    except ImportError:
        import json

import socks
from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA

DISCORD_HOST = "discord.com"
DISCORD_HOST_GATEWY = "wss://remote-auth-gateway.discord.gg/"
DISCORD_CDN_HOST = "cdn.discordapp.com"
DYN_DISCORD_CDN_HOST = "media.discordapp.net"
logger = logging.getLogger(__name__)
status_unpacker = struct.Struct("!H")


def log_api_error(response, function_name):
    """Add api response error to log"""
    text = f"{function_name}: Response code {response.status}"
    data = response.read()
    if data:
        try:
            data = json.loads(data)
            error_message = data.get("message")
            error_code = data.get("code")
            if "captcha_key" in data:
                error_message = data.get("captcha_key")
        except json.JSONDecodeError:
            error_code = "None"
            error_message = data.decode("utf-8").strip()
    else:
        error_code = error_message = None
    if error_code:
        text += f"; Error code: {error_code}"
    if error_message:
        text += f" - {error_message}"
    logger.warning(text)


class Discord():
    """Methods for fetching and sending data to Discord using REST API"""

    def __init__(self, host, client_prop, user_agent, proxy=None):
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
        self.header = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Priority": "u=1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": user_agent,
        }
        if client_prop:
            self.header["X-Super-Properties"] = client_prop
        self.user_agent = user_agent
        self.proxy = urllib.parse.urlsplit(proxy)


    def get_connection(self, host, port, timeout=10):
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
                connection = http.client.HTTPSConnection(host, port, timeout=timeout + 5)
                connection.sock = proxy_sock
            else:
                connection = http.client.HTTPSConnection(host, port)
        else:
            connection = http.client.HTTPSConnection(host, port, timeout=timeout)
        return connection


    def login(self, email, password):
        """
        Login this user using email and password, detect suspended and disabled account and mfa request.
        Codes:
        0 - success
        1 - mfa required
        2 - wrong email/password
        3 - verify phone number
        4 - verify email
        5 - account suspended
        6 - account disabled/marked for deletion
        7 - captcha required
        8 - network error
        """
        if email.startswith("+"):   # its phone number
            email = email.replace(" ", "").replace("-", "")
        message_dict = {
            "login": str(email),
            "password": str(password),
        }
        url = "/api/v9/auth/login"
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 7, None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            if "token" in data:
                return 0, data["token"]
            if data.get("mfa"):
                mfa_options = []
                if data.get("sms"):
                    mfa_options.append("SMS")
                if data.get("totp"):
                    mfa_options.append("TOTP")
                return 1, {
                    "mfa_options": mfa_options,
                    "ticket": data["ticket"],
                    "login_instance_id": data["login_instance_id"],
                    "webauthn": data["webauthn"],
                    }
            if "suspended_user_token" in data:
                return 5, None
            return 8, None
        if response.status == 400:
            data = json.loads(response.read())
            connection.close()
            error_code = data.get("code")
            log_api_error(response, "login")
            if "captcha_key" in data:
                return 7, None
            if error_code == 50035:
                return 2, None
            if error_code in (20013, 20011):
                return 6, None
            if error_code == 70007:
                return 3, None
            if error_code == 70009:
                return 4, None
        log_api_error(response, "login")
        connection.close()
        return 8, None


    def phone_verify(self, phone, code):
        """Verify phone number and receive ip authorization token"""
        phone = phone.replace(" ", "").replace("-", "")
        message_dict = {
            "phone": phone,
            "code": code,
        }
        url = "/api/v9/phone-verifications/verify"
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 2, None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return 0, data.get("token")
        log_api_error(response, "phone_verify")
        connection.close()
        return 1, None


    def send_mfa_sms(self, mfa_ticket):
        """Send mfa code over sms"""
        url = "/auth/mfa/sms/send"
        message_data = json.dumps({"ticket": mfa_ticket})
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 2, None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return 0, data.get("phone")
        log_api_error(response, "send_mfa_sms")
        connection.close()
        return 1, None


    def authorize_ip(self, auth_token):
        """Authorize this ip with token received over email or from phone_verify()"""
        url = "/api/v9/phone-verifications/verify"
        message_data = json.dumps({"token": auth_token})
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 2
        if response.status == 204:
            connection.close()
            return 0
        log_api_error(response, "authorize_ip")
        connection.close()
        return 1


    def verify_mfa(self, auth_type, code, login_instance_id, ticket):
        """Verify multi-factore authentication and get token"""
        message_dict = {
            "code": code,
            "login_instance_id": login_instance_id,
            "ticket": ticket,
        }
        url = "/api/v9/auth/mfa/" + auth_type
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 2, None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return 0, data.get("token")
        log_api_error(response, "verify_mfa")
        connection.close()
        return 1, None


    def exchange_ticket(self, ticket):
        """
        Exchange remote authentication ticket from remote authentication session.
        Return encrypted token that should be decrypted in gateway.
        """
        message_dict = {"ticket": ticket}
        url = "/api/v9/users/@me/remote-auth/login"
        message_data = json.dumps(message_dict)
        try:
            connection = self.get_connection(self.host, 443)
            connection.request("POST", url, message_data, self.header)
            response = connection.getresponse()
        except (socket.gaierror, TimeoutError):
            connection.close()
            return 3, None
        if response.status == 200:
            data = json.loads(response.read())
            connection.close()
            return 0, data.get("encrypted_token")
        if response.status == 400:
            data = json.loads(response.read())
            connection.close()
            log_api_error(response, "exchange_ticket")
            if "captcha_key" in data:
                return 2, None
        log_api_error(response, "exchange_ticket")
        connection.close()
        return 1, None


class Gateway():
    """Methods for fetching and sending data to Discord gateway through websocket"""
    def __init__(self, user_agent, host=None, proxy=None):
        if host:
            host_obj = urllib.parse.urlsplit(host)
            if host_obj.netloc:
                self.gateway_url = host_obj.netloc
            else:
                self.gateway_url = host_obj.path
        else:
            self.gateway_url = DISCORD_HOST_GATEWY
        self.header = [
            "Connection: keep-alive, Upgrade",
            "Sec-WebSocket-Extensions: permessage-deflate",
            "Origin: https://discord.com",
            f"User-Agent: {user_agent}",
        ]
        self.proxy = urllib.parse.urlsplit(proxy)
        self.run = True
        self.state = 0
        self.heartbeat_received = True
        self.heartbeat_interval = 30
        self.timeout = 100
        self.remaining_til_timeout = int(self.timeout)
        self.fingerprint = None
        self.user_id = ""
        self.username = ""
        self.ticket = None


    def connect_ws(self):
        """Connect to websocket"""
        self.ws = websocket.WebSocket()
        if self.proxy.scheme:
            self.ws.connect(
                self.gateway_url + "/?v=2",
                header=self.header,
                proxy_type=self.proxy.scheme,
                http_proxy_host=self.proxy.hostname,
                http_proxy_port=self.proxy.port,
            )
        else:
            self.ws.connect(self.gateway_url + "/?v=2", header=self.header)


    def disconnect_ws(self, timeout=2, status=1000):
        """Close websocket with timeout"""
        if self.ws:
            try:
                self.ws.settimeout(timeout)
                self.ws.close(status=status)
                logger.debug(f"Disconnected with status code {status}")
            except Exception as e:
                logger.warning("Error closing websocket:", e)
            finally:
                self.ws = None


    def connect(self):
        """Create initial connection to Discord gateway"""
        try:
            self.connect_ws()
        except websocket._exceptions.WebSocketAddressException:
            return False
        self.state = 0
        self.receiver_thread = threading.Thread(target=self.receiver, daemon=True)
        self.receiver_thread.start()
        self.heartbeat_thread = threading.Thread(target=self.send_heartbeat, daemon=True)
        self.heartbeat_thread.start()
        self.reconnect_thread = threading.Thread()
        self.init_auth()
        return True


    def send(self, request):
        """Send data to gateway"""
        try:
            self.ws.send(json.dumps(request))
        except websocket._exceptions.WebSocketException:
            self.state = 4
            self.heartbeat_running = False
            self.disconnect_ws(timeout=0)


    def init_rsa_keypair(self):
        """Initialize rsa keypair"""
        self.private_key = RSA.generate(2048)
        public_key = self.private_key.publickey()

        spki_der = public_key.export_key(format="DER")
        return base64.b64encode(spki_der).decode("utf-8")


    def decrypt_nonce(self, encrypted_nonce):
        """Decrypt nonce proof received from server"""
        cipher = PKCS1_OAEP.new(self.private_key, hashAlgo=SHA256)
        nonce = cipher.decrypt(base64.b64decode(encrypted_nonce))

        return base64.urlsafe_b64encode(nonce).decode("utf-8").rstrip("=")


    def decrypt_user_payload(self, user_payload):
        """Decrypt user playload received when remote authentication session starts"""
        cipher = PKCS1_OAEP.new(self.private_key, hashAlgo=SHA256)
        user_payload_bytes = cipher.decrypt(base64.b64decode(user_payload))

        user_id, _, _, username = user_payload_bytes.decode("utf-8").split(":")
        return user_id, username


    def decrypt_token(self, encrypted_token):
        """Decrypt token received from ticket-token exchange"""
        cipher = PKCS1_OAEP.new(self.private_key, hashAlgo=SHA256)
        decrypted = cipher.decrypt(base64.b64decode(encrypted_token))
        return decrypted.decode("utf-8")


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
            if ws_opcode == 8 and len(data) >= 2:
                if not data:
                    break
                status = status_unpacker.unpack(data[0:2])[0]
                reason = data[2:].decode("utf-8", "replace")
                if status not in (1000, 1001):
                    logger.warning(f"Gateway status code: {status}, reason: {reason}")
                break
            try:
                response = json.loads(data)
                opcode = response["op"]
            except ValueError:
                response = None
                opcode = None
            del data
            logger.debug(f"Received: opcode={opcode}")
            # debug_events
            # from endcord import debug
            # debug.save_json(response, f"{opcode}.json", False)

            if opcode == "heartbeat_ack":
                self.heartbeat_received = True

            elif opcode == "hello":
                self.heartbeat_interval = int(response["heartbeat_interval"])
                self.timeout = int(response["timeout_ms"]) / 1000
                self.remaining_til_timeout = int(self.timeout)

            elif opcode == "nonce_proof":
                encrypted_nonce = response["encrypted_nonce"]
                nonce = self.decrypt_nonce(encrypted_nonce)
                self.send({
                    "op": "nonce_proof",
                    "nonce": nonce,
                })

            elif opcode == "pending_remote_init":
                self.fingerprint = response["fingerprint"]
                self.state = 1

            elif opcode == "pending_ticket":
                encrypted_user_payload = response["encrypted_user_payload"]
                self.user_id, self.username = self.decrypt_user_payload(encrypted_user_payload)
                self.state = 2

            elif opcode == "pending_login":
                self.ticket = response["ticket"]
                self.state = 3

            elif opcode == "cancel":
                self.state = 6
                break

        logger.debug("Receiver stopped")
        self.heartbeat_running = False


    def send_heartbeat(self):
        """Send heartbeat to gateway, if response is not received, triggers reconnect, should be run in a thread"""
        logger.debug(f"Heartbeater started, interval={self.heartbeat_interval/1000} s")
        self.heartbeat_running = True
        self.heartbeat_received = True
        start_time = int(time.time())
        heartbeat_interval_rand = int(self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000)
        heartbeat_sent_time = int(time.time())
        while self.run and self.heartbeat_running:
            if time.time() - heartbeat_sent_time >= heartbeat_interval_rand:
                self.send({"op": "heartbeat"})
                heartbeat_sent_time = int(time.time())
                logger.debug("Sent heartbeat")
                if not self.heartbeat_received:
                    logger.warning("Heartbeat reply not received")
                    break
                self.heartbeat_received = False
                heartbeat_interval_rand = int(self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000)
            self.remaining_til_timeout = int(max(self.timeout - (time.time() - start_time), 0))
            if heartbeat_sent_time - time.time() >= self.timeout:
                self.state = 5
                self.disconnect_ws(timeout=0)
                logger.warn("Auth gateway timeout")
                return
            # sleep(heartbeat_interval * jitter), but jitter is limited to (0.1 - 0.9)
            # in this time heartbeat ack should be received from discord
            time.sleep(1)
        self.state = 4
        self.disconnect_ws(timeout=0)
        logger.debug("Heartbeater stopped")


    def init_auth(self):
        """Initialize remote authentication session"""
        encoded_public_key = self.init_rsa_keypair()
        payload = {
            "op": "init",
            "encoded_public_key": encoded_public_key,
        }
        self.send(payload)
        logger.debug("Initialized aut session")


    def get_state(self):
        """Get current gateway state"""
        return self.state


    def get_fingerprint(self):
        """Get fingerprint"""
        return self.fingerprint


    def get_user(self):
        """Get user information: user_id and username from remote authentication session"""
        return self.user_id, self.username


    def get_ticket(self):
        """Get authentication ticket that can be exchanged for authenticatin token"""
        return self.ticket


    def get_remaining_time(self):
        """Get remaining time in seconds before gateway timeout"""
        return self.remaining_til_timeout
