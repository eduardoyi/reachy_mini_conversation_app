"""Web Push to the installed admin app: VAPID keys (RFC 8292) and aes128gcm message encryption (RFC 8291).

Messages go through the phone's push service (Apple's or Google's), encrypted so only the phone can read
them. The keys live in an owner-only file next to the subscriptions.
"""

import os
import hmac
import json
import time
import base64
import hashlib
import logging
from typing import Final
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


logger = logging.getLogger(__name__)

RECORD_SIZE: Final[int] = 4096
TOKEN_LIFETIME_S: Final[int] = 12 * 3600
PUSH_TIMEOUT_S: Final[float] = 10.0
# How long the push service keeps a message for a phone that is off.
PUSH_TTL_S: Final[int] = 24 * 3600


def b64url(raw: bytes) -> str:
    """Return ``raw`` as unpadded base64url."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    """Decode unpadded (or padded) base64url."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    server_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """Return ``payload`` encrypted for the subscription keys ``p256dh`` and ``auth`` as one aes128gcm record."""
    ua_public = unb64url(p256dh)
    auth_secret = unb64url(auth)
    salt = salt if salt is not None else os.urandom(16)
    server_key = server_key if server_key is not None else ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(server_key.public_key())
    ecdh_secret = server_key.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    )
    ikm = _hmac(_hmac(auth_secret, ecdh_secret), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    content_key = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(payload) + 1 + 16 + 86 > RECORD_SIZE:
        raise ValueError("push payload too large")
    ciphertext = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ciphertext


def load_or_create_key(path: Path) -> ec.EllipticCurvePrivateKey:
    """Return the VAPID key in ``path``, creating it (owner-only) on first use."""
    if path.is_file():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("VAPID key is not an EC key")
        return key
    key = ec.generate_private_key(ec.SECP256R1())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(pem)
    return key


def application_server_key(key: ec.EllipticCurvePrivateKey) -> str:
    """Return the public key the browser subscribes with (base64url, uncompressed point)."""
    return b64url(_public_bytes(key.public_key()))


def vapid_authorization(endpoint: str, key: ec.EllipticCurvePrivateKey, subject: str, now: float) -> str:
    """Return the ``Authorization`` header value for a push to ``endpoint``."""
    parts = urlsplit(endpoint)
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now) + TOKEN_LIFETIME_S, "sub": subject}
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{header}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{body}.{signature}, k={application_server_key(key)}"


async def send(
    client: httpx.AsyncClient,
    subscription: dict[str, object],
    message: dict[str, object],
    key: ec.EllipticCurvePrivateKey,
    subject: str,
) -> int:
    """Push ``message`` (JSON) to one browser subscription; returns the push service's HTTP status (0 when unreached)."""
    endpoint = str(subscription.get("endpoint") or "")
    keys = subscription.get("keys")
    if not endpoint.startswith("https://") or not isinstance(keys, dict):
        return 0
    body = encrypt(json.dumps(message).encode(), str(keys.get("p256dh") or ""), str(keys.get("auth") or ""))
    headers = {
        "Authorization": vapid_authorization(endpoint, key, subject, time.time()),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(PUSH_TTL_S),
        "Urgency": "high",
    }
    try:
        response = await client.post(endpoint, content=body, headers=headers, timeout=PUSH_TIMEOUT_S)
    except httpx.HTTPError as error:
        logger.warning("Push not delivered: %s", type(error).__name__)
        return 0
    if response.status_code >= 300:
        logger.warning("Push refused by the push service: HTTP %d %s", response.status_code, response.text[:200])
    return response.status_code
