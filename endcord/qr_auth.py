"""
Discord QR Code Remote Authentication

This module implements Discord's Remote Auth protocol, allowing users to log in
by scanning a QR code with their Discord mobile app instead of manually extracting tokens.

Protocol flow:
1. Connect to wss://remote-auth-gateway.discord.gg/?v=2
2. Receive HELLO with timeout and heartbeat interval
3. Generate RSA 2048-bit keypair
4. Send INIT with public key
5. Receive NONCE_PROOF challenge, solve it
6. Receive PENDING_REMOTE_INIT with fingerprint
7. Generate QR code: https://discord.com/ra/{fingerprint}
8. User scans QR with Discord mobile app
9. Receive PENDING_TICKET with encrypted user data
10. User confirms on mobile
11. Receive PENDING_LOGIN with encrypted token
12. Decrypt and return token

References:
- https://github.com/RuslanUC/RemoteAuthClient
- https://docs.discord.food/remote-authentication/desktop
"""

import base64
import hashlib
import http.client
import json
import logging
import ssl
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.backends import default_backend
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

import websocket

logger = logging.getLogger(__name__)

REMOTE_AUTH_GATEWAY = "wss://remote-auth-gateway.discord.gg/?v=2"
QR_CODE_URL_TEMPLATE = "https://discord.com/ra/{fingerprint}"

# Opcodes
OP_HELLO = "hello"
OP_INIT = "init"
OP_NONCE_PROOF = "nonce_proof"
OP_PENDING_REMOTE_INIT = "pending_remote_init"
OP_PENDING_TICKET = "pending_ticket"
OP_PENDING_LOGIN = "pending_login"
OP_CANCEL = "cancel"
OP_HEARTBEAT = "heartbeat"
OP_HEARTBEAT_ACK = "heartbeat_ack"


class QRAuthError(Exception):
    """Base exception for QR auth errors"""
    pass


class QRAuthTimeout(QRAuthError):
    """QR code expired or auth timed out"""
    pass


class QRAuthCancelled(QRAuthError):
    """User cancelled the auth on mobile"""
    pass


class QRAuthCaptcha(QRAuthError):
    """Captcha required (rare, usually on suspicious IPs)"""
    def __init__(self, captcha_data: dict):
        self.captcha_data = captcha_data
        super().__init__("Captcha required for authentication")


class UserData:
    """Parsed user data from remote auth"""
    def __init__(self, encrypted_data: str, private_key):
        decrypted = _decrypt_payload(encrypted_data, private_key)
        if not decrypted:
            raise QRAuthError("Failed to decrypt user data: empty payload")

        parts = decrypted.split(":")
        if len(parts) < 3:
            raise QRAuthError(f"Invalid user data format: expected at least 3 fields, got {len(parts)}")

        self.id = parts[0]
        self.discriminator = parts[1]
        self.avatar_hash = parts[2]
        self.username = parts[3] if len(parts) > 3 else "Unknown"

    def get_display_name(self) -> str:
        if self.discriminator and self.discriminator != "0":
            return f"{self.username}#{self.discriminator}"
        return self.username

    def get_avatar_url(self) -> str:
        if self.avatar_hash:
            return f"https://cdn.discordapp.com/avatars/{self.id}/{self.avatar_hash}.png"
        # Default avatar based on discriminator or user id
        default_index = (int(self.id) >> 22) % 6 if self.discriminator == "0" else int(self.discriminator) % 5
        return f"https://cdn.discordapp.com/embed/avatars/{default_index}.png"


def _generate_keypair():
    """Generate RSA 2048-bit keypair for remote auth"""
    if not HAVE_CRYPTO:
        raise QRAuthError("cryptography library not installed. Run: pip install cryptography")

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    public_key = private_key.public_key()
    public_key_spki = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_key_b64 = base64.b64encode(public_key_spki).decode()

    return private_key, public_key_b64


def _decrypt_payload(encrypted_b64: str, private_key) -> str:
    """Decrypt OAEP-encrypted payload from Discord"""
    encrypted = base64.b64decode(encrypted_b64)
    decrypted = private_key.decrypt(
        encrypted,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )
    return decrypted.decode()


def _compute_nonce_proof(nonce_b64: str, private_key) -> str:
    """Decrypt nonce and compute SHA256 proof"""
    decrypted_nonce = _decrypt_payload(nonce_b64, private_key)
    if not decrypted_nonce:
        raise QRAuthError("Failed to decrypt nonce: empty payload")
    nonce_hash = hashlib.sha256(decrypted_nonce.encode()).digest()
    # URL-safe base64 without padding
    proof = base64.urlsafe_b64encode(nonce_hash).decode().rstrip("=")
    return proof


def _compute_fingerprint(public_key_b64: str) -> str:
    """Compute fingerprint from public key for verification"""
    public_key_der = base64.b64decode(public_key_b64)
    fingerprint_hash = hashlib.sha256(public_key_der).digest()
    return base64.urlsafe_b64encode(fingerprint_hash).decode().rstrip("=")


def _parse_proxy(proxy: str) -> tuple:
    """
    Parse proxy URL robustly, supporting multiple formats.

    Supported formats:
    - host:port
    - http://host:port
    - http://user:pass@host:port

    Returns:
        tuple: (host, port, auth) where auth is (user, pass) or None
    """
    if not proxy:
        return None, None, None

    try:
        # Add scheme if missing for urlparse to work correctly
        if "://" not in proxy:
            proxy = f"http://{proxy}"

        parsed = urlparse(proxy)
        host = parsed.hostname
        port = parsed.port or 8080
        auth = None

        if parsed.username:
            auth = (parsed.username, parsed.password or "")

        if host:
            return host, port, auth
    except Exception as e:
        logger.warning(f"Failed to parse proxy URL '{proxy}': {e}")

    # Fallback: basic host:port parsing
    try:
        if ":" in proxy and "://" not in proxy:
            parts = proxy.rsplit(":", 1)
            return parts[0], int(parts[1]), None
        return proxy, 8080, None
    except (ValueError, IndexError):
        return proxy, 8080, None


def _exchange_ticket_for_token(ticket: str, proxy: Optional[str] = None) -> str:
    """
    Exchange the decrypted ticket for an authentication token via Discord API.

    Args:
        ticket: Decrypted ticket from PENDING_LOGIN
        proxy: Optional proxy URL

    Returns:
        Authentication token

    Raises:
        QRAuthError: If the exchange fails
    """
    host, port, auth = _parse_proxy(proxy)
    connection = None  # Initialize before try block to avoid UnboundLocalError

    try:
        if host:
            # Use proxy with CONNECT tunnel
            connection = http.client.HTTPSConnection(host, port, timeout=10)
            connection.set_tunnel("discord.com", 443)
        else:
            connection = http.client.HTTPSConnection("discord.com", 443, timeout=10)

        body = json.dumps({"ticket": ticket})
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        connection.request("POST", "/api/v9/users/@me/remote-auth/login", body, headers)
        response = connection.getresponse()
        response_data = response.read()

        if response.status != 200:
            error_msg = f"Token exchange failed (status {response.status})"
            try:
                error_data = json.loads(response_data.decode('utf-8'))
                error_msg += f": {error_data}"
            except (json.JSONDecodeError, UnicodeDecodeError):
                preview = response_data.decode('utf-8', errors='replace')[:200]
                error_msg += f": {preview}"
            raise QRAuthError(error_msg)

        result = json.loads(response_data.decode('utf-8'))
        token = result.get("encrypted_token") or result.get("token")

        if not token:
            raise QRAuthError("No token in exchange response")

        logger.info("Successfully exchanged ticket for authentication token")
        return token

    except QRAuthError:
        raise
    except Exception as e:
        raise QRAuthError(f"Token exchange failed: {e}")
    finally:
        if connection:
            try:
                connection.close()
            except Exception:
                pass


# Import QR display functions from dedicated module
# This module provides terminal-aware QR code rendering with automatic
# capability detection and fallback strategies
try:
    from endcord.qr_display import (
        generate_qr_code_ascii,
        generate_qr_code_simple,
        QRDisplay,
        RenderMode,
        check_qr_support,
    )
except ImportError:
    # Minimal fallback if qr_display module not available
    def generate_qr_code_ascii(data: str, border: int = 2) -> str:
        """Fallback QR generation when qr_display module unavailable."""
        try:
            import segno
            import io
            qr = segno.make(data)
            f = io.StringIO()
            qr.terminal(out=f, compact=True, border=border)
            f.seek(0)
            return f.read()
        except ImportError:
            pass

        try:
            import qrcode
            qr = qrcode.QRCode(version=1, border=border)
            qr.add_data(data)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            lines = []
            for y in range(0, len(matrix), 2):
                line = ""
                for x in range(len(matrix[y])):
                    top = matrix[y][x]
                    bottom = matrix[y + 1][x] if y + 1 < len(matrix) else False
                    if top and bottom:
                        line += "\u2588"
                    elif top:
                        line += "\u2580"
                    elif bottom:
                        line += "\u2584"
                    else:
                        line += " "
                lines.append(line)
            return "\n".join(lines)
        except ImportError:
            pass

        return f"[QR Code - scan this URL with Discord mobile app]\n{data}"

    def generate_qr_code_simple(data: str) -> str:
        """Fallback simple QR generation."""
        return f"[Install 'segno' or 'qrcode' for QR display]\nURL: {data}"

    QRDisplay = None
    RenderMode = None
    check_qr_support = None


class RemoteAuthClient:
    """
    Discord Remote Authentication client.

    Handles the WebSocket connection to Discord's remote auth gateway
    and manages the QR code login flow.
    """

    def __init__(self, proxy: Optional[str] = None):
        self.proxy = proxy
        self.ws: Optional[websocket.WebSocket] = None
        self.private_key = None
        self.public_key_b64 = None
        self.fingerprint = None
        self.heartbeat_interval = None
        self.timeout_ms = None
        self.user_data: Optional[UserData] = None
        self.token: Optional[str] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()
        self._last_heartbeat_ack = True
        self._heartbeat_lock = threading.Lock()  # Thread-safe heartbeat state

        # Callbacks
        self.on_qr_code: Optional[Callable[[str, str], None]] = None  # (url, ascii_qr)
        self.on_user_data: Optional[Callable[[UserData], None]] = None
        self.on_token: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        self.on_waiting: Optional[Callable[[], None]] = None

    def _start_heartbeat(self):
        """Start heartbeat thread"""
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        """Send heartbeats at the specified interval"""
        while not self._stop_heartbeat.wait(self.heartbeat_interval / 1000):
            with self._heartbeat_lock:
                if not self._last_heartbeat_ack:
                    logger.warning("Missed heartbeat ACK")
                try:
                    self._send({"op": OP_HEARTBEAT})
                    self._last_heartbeat_ack = False
                except Exception as e:
                    logger.debug(f"Heartbeat error: {e}")
                    break

    def _stop_heartbeat_thread(self):
        """Stop the heartbeat thread"""
        self._stop_heartbeat.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)

    def _send(self, data: dict):
        """Send JSON message to WebSocket"""
        if self.ws:
            self.ws.send(json.dumps(data))

    def _recv(self) -> dict:
        """Receive JSON message from WebSocket"""
        if self.ws:
            data = self.ws.recv()
            return json.loads(data)
        return {}

    def _handle_hello(self, data: dict):
        """Handle HELLO opcode"""
        self.timeout_ms = data.get("timeout_ms", 120000)
        self.heartbeat_interval = data.get("heartbeat_interval", 41250)
        logger.debug(f"Remote auth hello: timeout={self.timeout_ms}ms, heartbeat={self.heartbeat_interval}ms")

        # Generate keypair
        self.private_key, self.public_key_b64 = _generate_keypair()

        # Start heartbeat
        self._start_heartbeat()

        # Send INIT
        self._send({
            "op": OP_INIT,
            "encoded_public_key": self.public_key_b64
        })

    def _handle_nonce_proof(self, data: dict):
        """Handle NONCE_PROOF opcode"""
        encrypted_nonce = data.get("encrypted_nonce")
        if not encrypted_nonce:
            raise QRAuthError("No nonce in nonce_proof")

        proof = _compute_nonce_proof(encrypted_nonce, self.private_key)
        self._send({
            "op": OP_NONCE_PROOF,
            "proof": proof
        })

    def _handle_pending_remote_init(self, data: dict):
        """Handle PENDING_REMOTE_INIT - QR code is ready"""
        self.fingerprint = data.get("fingerprint")
        if not self.fingerprint:
            raise QRAuthError("No fingerprint in pending_remote_init")

        # Verify fingerprint matches our public key
        expected_fingerprint = _compute_fingerprint(self.public_key_b64)
        if self.fingerprint != expected_fingerprint:
            logger.warning("Fingerprint mismatch - possible MITM attack")
            raise QRAuthError("Fingerprint verification failed")

        # Generate QR code URL
        qr_url = QR_CODE_URL_TEMPLATE.format(fingerprint=self.fingerprint)
        qr_ascii = generate_qr_code_ascii(qr_url)

        logger.info(f"QR code ready: {qr_url}")

        if self.on_qr_code:
            self.on_qr_code(qr_url, qr_ascii)

    def _handle_pending_ticket(self, data: dict):
        """Handle PENDING_TICKET - user scanned QR, waiting for confirmation"""
        encrypted_user = data.get("encrypted_user_payload")
        if encrypted_user:
            self.user_data = UserData(encrypted_user, self.private_key)
            logger.info(f"User scanned QR: {self.user_data.get_display_name()}")

            if self.on_user_data:
                self.on_user_data(self.user_data)

        if self.on_waiting:
            self.on_waiting()

    def _handle_pending_login(self, data: dict):
        """Handle PENDING_LOGIN - auth complete, exchange ticket for token"""
        encrypted_ticket = data.get("ticket")
        if not encrypted_ticket:
            raise QRAuthError("No ticket in pending_login")

        # Decrypt the ticket
        decrypted_ticket = _decrypt_payload(encrypted_ticket, self.private_key)
        logger.debug("Ticket decrypted, exchanging for authentication token")

        # Exchange ticket for actual token via API
        self.token = _exchange_ticket_for_token(decrypted_ticket, self.proxy)
        logger.info("Remote auth successful, token received")

        if self.on_token:
            self.on_token(self.token)

    def connect_and_wait(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Connect to remote auth gateway and wait for authentication.

        Args:
            timeout: Maximum time to wait in seconds (default: use Discord's timeout)

        Returns:
            Discord token if successful, None if cancelled/timed out

        Raises:
            QRAuthError: On protocol errors
            QRAuthCaptcha: If captcha is required
        """
        if not HAVE_CRYPTO:
            raise QRAuthError("cryptography library required. Run: pip install cryptography")

        try:
            # Connect to gateway with robust proxy parsing
            ws_opts = {}
            if self.proxy:
                host, port, auth = _parse_proxy(self.proxy)
                if host:
                    ws_opts["http_proxy_host"] = host
                    ws_opts["http_proxy_port"] = port
                    if auth:
                        ws_opts["http_proxy_auth"] = auth

            self.ws = websocket.create_connection(
                REMOTE_AUTH_GATEWAY,
                sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                **ws_opts
            )

            start_time = time.time()
            effective_timeout = timeout

            while True:
                # Check timeout
                if effective_timeout and (time.time() - start_time) > effective_timeout:
                    raise QRAuthTimeout("Authentication timed out")

                try:
                    # Adaptive timeout: longer when waiting, respects remaining time
                    if effective_timeout:
                        elapsed = time.time() - start_time
                        remaining = effective_timeout - elapsed
                        ws_timeout = min(5.0, max(0.5, remaining))
                    else:
                        ws_timeout = 5.0
                    self.ws.settimeout(ws_timeout)
                    data = self._recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except websocket.WebSocketConnectionClosedException:
                    if self.token:
                        return self.token
                    raise QRAuthTimeout("Connection closed")

                op = data.get("op")

                if op == OP_HELLO:
                    self._handle_hello(data)
                    if not effective_timeout:
                        effective_timeout = self.timeout_ms / 1000

                elif op == OP_NONCE_PROOF:
                    self._handle_nonce_proof(data)

                elif op == OP_PENDING_REMOTE_INIT:
                    self._handle_pending_remote_init(data)

                elif op == OP_PENDING_TICKET:
                    self._handle_pending_ticket(data)

                elif op == OP_PENDING_LOGIN:
                    self._handle_pending_login(data)
                    return self.token

                elif op == OP_CANCEL:
                    raise QRAuthCancelled("Authentication cancelled by user")

                elif op == OP_HEARTBEAT_ACK:
                    with self._heartbeat_lock:
                        self._last_heartbeat_ack = True

                elif op == "captcha":
                    # Captcha required (rare)
                    raise QRAuthCaptcha({
                        "captcha_sitekey": data.get("captcha_sitekey"),
                        "captcha_service": data.get("captcha_service", "hcaptcha"),
                        "captcha_rqdata": data.get("captcha_rqdata"),
                        "captcha_rqtoken": data.get("captcha_rqtoken"),
                    })

                else:
                    logger.debug(f"Unknown opcode: {op}")

        except Exception as e:
            if self.on_error:
                self.on_error(e)
            raise

        finally:
            self._stop_heartbeat_thread()
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None

    def close(self):
        """Close the connection"""
        self._stop_heartbeat_thread()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None


def authenticate_with_qr(
    on_qr_ready: Callable[[str, str], None],
    on_user_scanned: Optional[Callable[[UserData], None]] = None,
    on_waiting: Optional[Callable[[], None]] = None,
    proxy: Optional[str] = None,
    timeout: Optional[float] = None
) -> Optional[str]:
    """
    Convenience function for QR code authentication.

    Args:
        on_qr_ready: Callback when QR code is ready. Receives (url, ascii_art).
        on_user_scanned: Optional callback when user scans QR (before confirmation).
        on_waiting: Optional callback while waiting for user to confirm.
        proxy: Optional proxy URL (host:port format).
        timeout: Optional timeout in seconds.

    Returns:
        Discord token if successful, None on failure.

    Example:
        def show_qr(url, ascii_art):
            print(ascii_art)
            print(f"Or open: {url}")

        token = authenticate_with_qr(show_qr)
        if token:
            print(f"Got token: {token[:20]}...")
    """
    client = RemoteAuthClient(proxy=proxy)
    client.on_qr_code = on_qr_ready
    client.on_user_data = on_user_scanned
    client.on_waiting = on_waiting

    try:
        return client.connect_and_wait(timeout=timeout)
    except QRAuthCancelled:
        return None
    except QRAuthTimeout:
        return None
    except QRAuthError as e:
        logger.error(f"QR auth error: {e}")
        return None
