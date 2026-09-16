"""Compatibility checks for the pinned Google Gen AI Live SDK."""

import json

import pytest
from google import genai
from google.genai import live, types


class FakeWebSocket:
    """Return synthetic setup and server frames to the SDK."""

    def __init__(self, frames: list[dict[str, object]]) -> None:
        """Initialize a websocket with deterministic JSON frames."""
        self._frames = list(frames)
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        """Record one SDK outbound payload."""
        self.sent.append(payload)

    async def recv(self, *_args: object, **_kwargs: object) -> bytes:
        """Return the next synthetic server payload as bytes."""
        if not self._frames:
            raise AssertionError("synthetic websocket ran out of frames")
        return json.dumps(self._frames.pop(0)).encode("utf-8")

    async def close(self) -> None:
        """Record SDK connection closure."""
        self.closed = True


class FakeWebSocketContext:
    """Async context manager matching the websocket connector boundary."""

    def __init__(self, websocket: FakeWebSocket, uri: str, options: dict[str, object]) -> None:
        """Initialize the context around one fake websocket."""
        self.websocket = websocket
        self.uri = uri
        self.options = options

    async def __aenter__(self) -> FakeWebSocket:
        """Return the fake websocket to the SDK."""
        return self.websocket

    async def __aexit__(self, *_args: object) -> None:
        """Close the fake websocket when the SDK context exits."""
        await self.websocket.close()


def install_fake_websocket(
    monkeypatch: pytest.MonkeyPatch, frames: list[dict[str, object]]
) -> tuple[FakeWebSocket, list[FakeWebSocketContext]]:
    """Patch only the SDK's websocket connector and retain call details."""
    websocket = FakeWebSocket(frames)
    connections: list[FakeWebSocketContext] = []

    def connect(uri: str, **options: object) -> FakeWebSocketContext:
        context = FakeWebSocketContext(websocket, uri, options)
        connections.append(context)
        return context

    monkeypatch.setattr(live, "ws_connect", connect)
    return websocket, connections


def build_client() -> genai.Client:
    """Build a client with an explicit synthetic v1alpha transport."""
    return genai.Client(
        api_key="synthetic-key",
        http_options=types.HttpOptions(api_version="v1alpha"),
    )


async def receive_messages(client: genai.Client) -> list[types.LiveServerMessage]:
    """Decode the queued server frames through the SDK's public Live iterator."""
    try:
        async with client.aio.live.connect(model="gemini-3.8-live-extended-thinking") as session:
            return [message async for message in session.receive()]
    finally:
        await client.aio.aclose()


@pytest.mark.asyncio
async def test_nested_status_and_transcript_fields_decode_from_live_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested status and finished transcript markers survive SDK decoding."""
    install_fake_websocket(
        monkeypatch,
        [
            {"setupComplete": {"sessionId": "synthetic"}},
            {
                "serverContent": {
                    "interimInputTranscription": {"text": "hel", "finished": False},
                }
            },
            {
                "serverContent": {
                    "inputTranscription": {"text": "hello", "finished": True},
                    "outputTranscription": {"text": "Hi", "finished": True},
                    "turnComplete": True,
                    "interactionStatus": "IDLE",
                }
            },
        ],
    )

    messages = await receive_messages(build_client())

    assert len(messages) == 2
    first_content = messages[0].server_content
    final_content = messages[1].server_content
    assert first_content is not None
    assert first_content.interim_input_transcription is not None
    assert first_content.interim_input_transcription.text == "hel"
    assert first_content.interim_input_transcription.finished is False
    assert final_content is not None
    assert final_content.input_transcription is not None
    assert final_content.input_transcription.text == "hello"
    assert final_content.input_transcription.finished is True
    assert final_content.output_transcription is not None
    assert final_content.output_transcription.finished is True
    assert final_content.interaction_status == types.InteractionStatus.IDLE


@pytest.mark.asyncio
async def test_top_level_status_is_not_exposed_by_pinned_live_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top-level status is discarded while canonical nested status remains."""
    websocket, _ = install_fake_websocket(
        monkeypatch,
        [
            {"setupComplete": {"sessionId": "synthetic"}},
            {
                "interactionStatus": "IDLE",
                "toolCall": {"functionCalls": [{"id": "call-1", "name": "search", "args": {}}]},
                "serverContent": {
                    "turnComplete": True,
                    "interactionStatus": "IN_PROGRESS",
                },
            },
        ],
    )

    client = build_client()
    try:
        async with client.aio.live.connect(model="gemini-3.8-live-extended-thinking") as session:
            messages = [message async for message in session.receive()]
    finally:
        await client.aio.aclose()

    assert websocket.closed is True
    assert len(messages) == 1
    message = messages[0]
    assert "interaction_status" not in message.model_dump(exclude_none=True)
    assert message.server_content is not None
    assert message.server_content.interaction_status == types.InteractionStatus.IN_PROGRESS
    assert message.tool_call is not None
    assert message.tool_call.function_calls is not None
    assert message.tool_call.function_calls[0].id == "call-1"


@pytest.mark.asyncio
async def test_top_level_only_status_is_silently_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A top-level-only status cannot be recovered from the decoded message."""
    install_fake_websocket(
        monkeypatch,
        [
            {"setupComplete": {"sessionId": "synthetic"}},
            {
                "interactionStatus": "IDLE",
                "serverContent": {"turnComplete": True},
            },
        ],
    )

    messages = await receive_messages(build_client())

    assert len(messages) == 1
    assert "interaction_status" not in messages[0].model_dump(exclude_none=True)
    assert messages[0].server_content is not None
    assert messages[0].server_content.interaction_status is None


@pytest.mark.asyncio
async def test_unknown_nested_status_is_preserved_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK accepts a future status, so the adapter must validate it."""
    install_fake_websocket(
        monkeypatch,
        [
            {"setupComplete": {"sessionId": "synthetic"}},
            {
                "serverContent": {
                    "turnComplete": True,
                    "interactionStatus": "FUTURE_STATE",
                }
            },
        ],
    )

    client = build_client()
    try:
        async with client.aio.live.connect(model="gemini-3.8-live-extended-thinking") as session:
            with pytest.warns(UserWarning, match="FUTURE_STATE"):
                messages = [message async for message in session.receive()]
    finally:
        await client.aio.aclose()

    assert len(messages) == 1
    message = messages[0]
    assert message.server_content is not None
    assert message.server_content.interaction_status is not None
    assert message.server_content.interaction_status.value == "FUTURE_STATE"


@pytest.mark.asyncio
async def test_v1alpha_connects_to_live_endpoint_and_normalizes_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public client option selects v1alpha and sends the models resource name."""
    websocket, connections = install_fake_websocket(
        monkeypatch,
        [{"setupComplete": {"sessionId": "synthetic"}}],
    )

    client = build_client()
    try:
        async with client.aio.live.connect(model="gemini-3.8-live-extended-thinking"):
            pass
    finally:
        await client.aio.aclose()

    assert len(connections) == 1
    assert ".v1alpha.GenerativeService.BidiGenerateContent" in connections[0].uri
    setup = json.loads(websocket.sent[0])
    assert setup["setup"]["model"] == "models/gemini-3.8-live-extended-thinking"
