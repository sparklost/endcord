# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import base64
import json
import logging
import random
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.parse

import websocket
from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA

from endcord import peripherals
from endcord.discord import get_hosts

logger = logging.getLogger(__name__)
status_unpacker = struct.Struct("!H")


def log_api_error(status, data, function_name):
    """Add api response error to log"""
    text = f"{function_name}: Response code {status}"
    if data.get("code"):
        text += f"; Error code: {data["code"]}"
    if data.get("message"):
        text += f" - {data["message"]}"
    logger.warning(text)


class Discord():
    """Methods for fetching and sending data to Discord using REST API"""

    def __init__(self, host, client_prop, user_agent, proxy=None):
        hosts = get_hosts(host, proxy=proxy)
        if not hosts:
            raise SystemExit(f"Could not get endpoints for host: {host}")
        api, cdn = hosts[:2]
        self.hostname, self.port, self.tls = api
        self.host = self.hostname if self.port in (80, 443) else f"{self.hostname}:{self.port}"   # used for links and header
        self.cdn_host = cdn[0]
        logger.debug(f"Endpoints: API={self.hostname}:{self.port}, CDN={self.cdn_host}")
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
        self.proxy = proxy


    def request(self, method, path, body=None, headers=None):
        """Perform request on new connection"""
        header = self.header.copy()
        header.update(headers or {})
        connection = None
        try:
            connection = peripherals.get_connection(self.hostname, self.port, timeout=2, proxy=self.proxy, tls=self.tls)
            connection.request(method, path, body, header)
            response = connection.getresponse()
            status = response.status
            raw = response.read()
        except (socket.gaierror, TimeoutError, OSError):
            return None, None
        finally:
            if connection:
                connection.close()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return status, data if isinstance(data, dict) else {}


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
        message_data = json.dumps({"login": str(email), "password": str(password)})
        status, data = self.request("POST", "/api/v9/auth/login", message_data)
        if status is None:
            return 8, None
        if status == 200:
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
        if status == 400:
            error_code = data.get("code")
            log_api_error(status, data, "login")
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
        log_api_error(status, data, "login")
        return 8, None


    def phone_verify(self, phone, code):
        """Verify phone number and receive ip authorization token"""
        phone = phone.replace(" ", "").replace("-", "")
        message_data = json.dumps({"phone": phone, "code": code})
        status, data = self.request("POST", "/api/v9/phone-verifications/verify", message_data)
        if status is None:
            return 2, None
        if status == 200:
            return 0, data.get("token")
        log_api_error(status, data, "phone_verify")
        return 1, None


    def send_mfa_sms(self, mfa_ticket):
        """Send mfa code over sms"""
        status, data = self.request("POST", "/auth/mfa/sms/send", json.dumps({"ticket": mfa_ticket}))
        if status is None:
            return 2, None
        if status == 200:
            return 0, data.get("phone")
        log_api_error(status, data, "send_mfa_sms")
        return 1, None


    def authorize_ip(self, auth_token):
        """Authorize this ip with token received over email or from phone_verify()"""
        status, data = self.request("POST", "/api/v9/phone-verifications/verify", json.dumps({"token": auth_token}))
        if status is None:
            return 2
        if status == 204:
            return 0
        log_api_error(status, data, "authorize_ip")
        return 1


    def verify_mfa(self, auth_type, code, login_instance_id, ticket):
        """Verify multi-factor authentication and get token"""
        message_data = json.dumps({
            "code": code,
            "login_instance_id": login_instance_id,
            "ticket": ticket,
        })
        status, data = self.request("POST", "/api/v9/auth/mfa/" + auth_type, message_data)
        if status is None:
            return 2, None
        if status == 200:
            return 0, data.get("token")
        log_api_error(status, data, "verify_mfa")
        return 1, None


    def exchange_ticket(self, ticket):
        """
        Exchange remote authentication ticket from remote authentication session.
        Return encrypted token that should be decrypted in gateway.
        """
        status, data = self.request("POST", "/api/v9/users/@me/remote-auth/login", json.dumps({"ticket": ticket}))
        if status is None:
            return 3, None
        if status == 200:
            return 0, data.get("encrypted_token")
        if status == 400:
            log_api_error(status, data, "exchange_ticket")
            if "captcha_key" in data:
                return 2, None
        log_api_error(status, data, "exchange_ticket")
        return 1, None


class Gateway():
    """Methods for fetching and sending data to Discord gateway through websocket"""
    def __init__(self, user_agent, host=None, proxy=None):
        hosts = get_hosts(host, proxy=proxy)
        if not hosts:
            raise SystemExit(f"Could not get endpoints for host: {host}")
        auth_gateway = hosts[4]
        if auth_gateway:
            hostname, port, tls = auth_gateway
            port_suffix = "" if port in (80, 443) else f":{port}"
            self.gateway_url = f"{"wss" if tls else "ws"}://{hostname}{port_suffix}"
        else:
            self.gateway_url = None   # remote auth is not supported on this host
        self.enabled = bool(self.gateway_url)
        self.header = [
            "Connection: keep-alive, Upgrade",
            "Sec-WebSocket-Extensions: permessage-deflate",
            "Origin: https://discord.com",
            f"User-Agent: {user_agent}",
        ]
        self.ws = None
        self.proxy = proxy
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
        sslopt = {}
        if not self.gateway_url.startswith("ws://"):   # plain ws:// is used for local instances
            if sys.platform == "darwin":
                import certifi
                ssl_context = ssl.create_default_context(cafile=certifi.where())
            else:
                ssl_context = ssl.create_default_context()
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            sslopt = {"context": ssl_context}
        self.ws = websocket.WebSocket(sslopt=sslopt)
        if self.proxy:
            proxy = urllib.parse.urlsplit(self.proxy)
            scheme = proxy.scheme
            self.ws.connect(
                self.gateway_url + "/?v=2",
                header=self.header,
                proxy_type="socks5h" if scheme == "socks5" else "http" if scheme == "https" else scheme,
                http_proxy_host=proxy.hostname,
                http_proxy_port=proxy.port,
                http_proxy_auth=(proxy.username, proxy.password) if proxy.username else None,
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
        if not self.enabled:
            return False
        try:
            self.connect_ws()
        except (websocket._exceptions.WebSocketException, OSError):
            return False
        self.state = 0
        self.receiver_thread = threading.Thread(target=self.receiver, daemon=True)
        self.receiver_thread.start()
        self.heartbeat_thread = threading.Thread(target=self.send_heartbeat, daemon=True)
        self.heartbeat_thread.start()
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
        """Decrypt user payload received when remote authentication session starts"""
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
        start_time = int(time.monotonic())
        heartbeat_interval_rand = int(self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000)
        heartbeat_sent_time = int(time.monotonic())
        while self.run and self.heartbeat_running:
            if time.monotonic() - heartbeat_sent_time >= heartbeat_interval_rand:
                self.send({"op": "heartbeat"})
                heartbeat_sent_time = int(time.monotonic())
                logger.debug("Sent heartbeat")
                if not self.heartbeat_received:
                    logger.warning("Heartbeat reply not received")
                    break
                self.heartbeat_received = False
                heartbeat_interval_rand = int(self.heartbeat_interval * (0.8 - 0.6 * random.random()) / 1000)
            self.remaining_til_timeout = int(max(self.timeout - (time.monotonic() - start_time), 0))
            if heartbeat_sent_time - time.monotonic() >= self.timeout:
                self.state = 5
                self.disconnect_ws(timeout=0)
                logger.warning("Auth gateway timeout")
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
        logger.debug("Initialized auth session")


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
        """Get authentication ticket that can be exchanged for authentication token"""
        return self.ticket


    def get_remaining_time(self):
        """Get remaining time in seconds before gateway timeout"""
        return self.remaining_til_timeout
