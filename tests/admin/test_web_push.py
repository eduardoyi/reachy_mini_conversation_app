"""Web Push encryption (RFC 8291), VAPID authorization (RFC 8292) and delivery to the push service."""

import sys
import json
import stat
import time
from pathlib import Path

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from reachy_mini_conversation_app.admin.web_push import (
    PUSH_TTL_S,
    RECORD_SIZE,
    TOKEN_LIFETIME_S,
    send,
    b64url,
    encrypt,
    unb64url,
    load_or_create_key,
    vapid_authorization,
    application_server_key,
)


# RFC 8291 Appendix A.
PLAINTEXT = b"When I grow up, I want to be a watermelon"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
EXPECTED_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl"
    "7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)
ENDPOINT = "https://fcm.googleapis.com:443/wp/abc123?x=1"
SUBJECT = "https://reachy.example.ts.net"


def _rfc_server_key() -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64url(AS_PRIVATE), "big"), ec.SECP256R1())


def _subscription(endpoint: str = "https://push.example.com/send/1") -> dict[str, object]:
    return {"endpoint": endpoint, "keys": {"p256dh": UA_PUBLIC, "auth": AUTH}}


def test_base64url_round_trips_without_padding() -> None:
    """Encoding drops padding; decoding accepts it either way."""
    for raw in (b"", b"a", b"ab", b"abc", bytes(range(256))):
        text = b64url(raw)
        assert "=" not in text and unb64url(text) == raw
    assert unb64url("YQ==") == b"a"


def test_encrypt_reproduces_rfc8291_appendix_a() -> None:
    """Given the RFC's salt and server key, the encrypted body is the RFC's, byte for byte."""
    body = encrypt(PLAINTEXT, UA_PUBLIC, AUTH, salt=unb64url(SALT), server_key=_rfc_server_key())
    assert b64url(body) == EXPECTED_BODY


def test_encrypt_without_fixed_inputs_uses_fresh_salt_and_key() -> None:
    """Two messages never share salt or server key, and the header carries the record size."""
    first = encrypt(PLAINTEXT, UA_PUBLIC, AUTH)
    second = encrypt(PLAINTEXT, UA_PUBLIC, AUTH)
    assert first[:16] != second[:16]
    assert first[21:86] != second[21:86]
    assert int.from_bytes(first[16:20], "big") == RECORD_SIZE and first[20] == 65


def test_encrypt_refuses_a_payload_larger_than_one_record() -> None:
    """The largest payload fits one 4096-byte record; one byte more is refused."""
    largest = RECORD_SIZE - 86 - 16 - 1
    assert len(encrypt(b"x" * largest, UA_PUBLIC, AUTH)) == RECORD_SIZE
    with pytest.raises(ValueError):
        encrypt(b"x" * (largest + 1), UA_PUBLIC, AUTH)


def _verify_jwt(token: str, key: ec.EllipticCurvePrivateKey) -> tuple[dict[str, object], dict[str, object]]:
    header_b64, claims_b64, signature_b64 = token.split(".")
    signature = unb64url(signature_b64)
    assert len(signature) == 64
    der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
    key.public_key().verify(der, f"{header_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(unb64url(header_b64)), json.loads(unb64url(claims_b64))


def test_vapid_authorization_is_a_signed_token_for_the_push_origin() -> None:
    """The header is ``vapid t=<ES256 JWT>, k=<public key>`` with the endpoint's origin as audience."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = time.time()
    value = vapid_authorization(ENDPOINT, key, SUBJECT, now)
    assert value.startswith("vapid t=")
    token_part, key_part = value.split(", ")
    token = token_part.removeprefix("vapid t=")
    assert key_part == f"k={application_server_key(key)}"

    header, claims = _verify_jwt(token, key)
    assert header == {"typ": "JWT", "alg": "ES256"}
    assert claims["aud"] == "https://fcm.googleapis.com:443"
    assert claims["sub"] == SUBJECT
    assert claims["exp"] == int(now) + TOKEN_LIFETIME_S
    assert isinstance(claims["exp"], int) and claims["exp"] > now


def test_vapid_signature_does_not_verify_with_another_key() -> None:
    """The token is bound to this server's key."""
    key = ec.generate_private_key(ec.SECP256R1())
    token = vapid_authorization(ENDPOINT, key, SUBJECT, time.time()).split(", ")[0].removeprefix("vapid t=")
    with pytest.raises(InvalidSignature):
        _verify_jwt(token, ec.generate_private_key(ec.SECP256R1()))


def test_application_server_key_is_the_uncompressed_public_point() -> None:
    """The browser subscribes with 65 bytes starting 0x04."""
    raw = unb64url(application_server_key(ec.generate_private_key(ec.SECP256R1())))
    assert len(raw) == 65 and raw[0] == 4


def test_load_or_create_key_creates_once_and_reloads_the_same_key(tmp_path: Path) -> None:
    """The first call writes an owner-only key file; later calls return the same key."""
    path = tmp_path / "admin" / "vapid.pem"
    created = load_or_create_key(path)
    assert path.is_file()
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    reloaded = load_or_create_key(path)
    assert application_server_key(reloaded) == application_server_key(created)
    assert path.read_bytes() == before


def test_load_or_create_key_refuses_a_non_ec_key(tmp_path: Path) -> None:
    """A key file holding another kind of key is an error, not silently replaced."""
    path = tmp_path / "vapid.pem"
    path.write_bytes(
        ed25519.Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    with pytest.raises(ValueError):
        load_or_create_key(path)


@pytest.mark.asyncio
async def test_send_posts_an_encrypted_message_with_push_headers() -> None:
    """The push service gets an aes128gcm body with TTL, urgency and a VAPID authorization."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    key = ec.generate_private_key(ec.SECP256R1())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        status = await send(client, _subscription(), {"title": "Reachy", "body": "Hi"}, key, SUBJECT)

    assert status == 201
    (request,) = seen
    assert str(request.url) == "https://push.example.com/send/1"
    assert request.method == "POST"
    assert request.headers["content-encoding"] == "aes128gcm"
    assert request.headers["content-type"] == "application/octet-stream"
    assert request.headers["ttl"] == str(PUSH_TTL_S)
    assert request.headers["urgency"] == "high"
    assert request.headers["authorization"].startswith("vapid t=")
    _, claims = _verify_jwt(request.headers["authorization"].split(", ")[0].removeprefix("vapid t="), key)
    assert claims["aud"] == "https://push.example.com"
    body = request.content
    assert b"Reachy" not in body and int.from_bytes(body[16:20], "big") == RECORD_SIZE


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410, 500])
async def test_send_returns_the_push_service_status(status: int) -> None:
    """Refusals are passed back so the caller can forget dropped subscriptions."""
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    async with httpx.AsyncClient(transport=transport) as client:
        key = ec.generate_private_key(ec.SECP256R1())
        assert await send(client, _subscription(), {"title": "t", "body": "b"}, key, SUBJECT) == status


@pytest.mark.asyncio
async def test_send_returns_zero_when_the_push_service_is_unreachable() -> None:
    """A transport error is not raised; it reports 0."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        key = ec.generate_private_key(ec.SECP256R1())
        assert await send(client, _subscription(), {"title": "t", "body": "b"}, key, SUBJECT) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subscription",
    [
        _subscription("http://push.example.com/send/1"),
        _subscription("file:///etc/passwd"),
        {"endpoint": "https://push.example.com/send/1"},
        {"endpoint": "https://push.example.com/send/1", "keys": "nope"},
        {},
    ],
)
async def test_send_refuses_non_https_or_keyless_subscriptions_without_a_request(
    subscription: dict[str, object],
) -> None:
    """Only https push endpoints with keys are contacted."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        key = ec.generate_private_key(ec.SECP256R1())
        assert await send(client, subscription, {"title": "t", "body": "b"}, key, SUBJECT) == 0
    assert seen == []
