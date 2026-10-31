"""The admin app's line to the running conversation app's control API (loopback, shared token)."""

import logging
from typing import Any, Final
from pathlib import Path

import httpx


logger = logging.getLogger(__name__)

STATUS_TIMEOUT_S: Final[float] = 3.0
ACTION_TIMEOUT_S: Final[float] = 20.0


class AppUnavailable(RuntimeError):
    """The conversation app is not running, or refused the admin app."""


class AppLink:
    """Call the control API at ``url`` with the token in ``token_file`` (read on each call: the app may renew it)."""

    def __init__(self, url: str, token_file: Path, client: httpx.AsyncClient | None = None) -> None:
        """Use ``client`` (tests) or a new one."""
        self._url = url.rstrip("/")
        self._token_file = token_file
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(STATUS_TIMEOUT_S, read=None))

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_file.read_text().strip()
        except OSError as error:
            raise AppUnavailable("no control token") from error
        return {"Authorization": f"Bearer {token}"}

    async def request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """Send one request; raises AppUnavailable when the app cannot be reached or refuses the token."""
        timeout = STATUS_TIMEOUT_S if method == "GET" else ACTION_TIMEOUT_S
        try:
            response = await self._client.request(
                method, self._url + path, json=json, params=params, headers=self._headers(), timeout=timeout
            )
        except httpx.HTTPError as error:
            raise AppUnavailable(type(error).__name__) from error
        if response.status_code == 403:
            raise AppUnavailable("control token refused")
        return response

    async def status(self) -> dict[str, Any] | None:
        """Return the app's status, or None when it is not running."""
        try:
            response = await self.request("GET", "/status")
        except AppUnavailable:
            return None
        payload = response.json() if response.status_code == 200 else None
        return payload if isinstance(payload, dict) else None

    async def open_stream(self, path: str) -> httpx.Response:
        """Start a long response (the camera); the caller reads it and closes it."""
        request = self._client.build_request("GET", self._url + path, headers=self._headers())
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as error:
            raise AppUnavailable(type(error).__name__) from error
        if response.status_code == 403:
            await response.aclose()
            raise AppUnavailable("control token refused")
        return response

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
