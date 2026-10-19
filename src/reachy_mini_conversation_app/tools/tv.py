import asyncio
import logging
from typing import Any
from urllib.parse import quote_plus

from reachy_mini_conversation_app.android_tv import (
    APPS,
    MEDIA_KEYS,
    NAVIGATION_KEYS,
    TVError,
    resolve_app,
)
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

# A TV that just reported waking needs a moment before it takes an app.
WAKE_SETTLE_S = 1.0
VOLUME_STEP_DEFAULT = 3
VOLUME_STEP_MAX = 20


class TV(Tool):
    """Control the living room TV."""

    name = "tv"
    description = (
        "Control the TV (Android / Google TV). Anyone in the household may use it, kids included. "
        "action=status says whether it is on, which app is open and the volume. "
        "action=power with on=true/false. "
        "action=volume with level 0-100, or change=up/down (optionally steps, default 3); "
        "action=mute with muted=true/false. "
        "action=media with key play, pause, play_pause, stop, next, previous, rewind or fast_forward. "
        "action=navigate with key up, down, left, right, select, back or home (optionally repeat). "
        "action=open with app (" + ", ".join(sorted({name.title() for name in APPS})) + "); "
        "it cannot pick a show inside an app. "
        "action=youtube with query searches YouTube on the TV and shows the results; the person picks "
        "with the remote or asks you to navigate and select. "
        "Turning it on or opening an app also wakes the TV. Say briefly what you did."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "power", "volume", "mute", "media", "navigate", "open", "youtube"],
            },
            "on": {"type": "boolean", "description": "For power: on (true) or off (false)."},
            "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "For volume: 0-100."},
            "change": {"type": "string", "enum": ["up", "down"], "description": "For volume: louder or quieter."},
            "steps": {"type": "integer", "minimum": 1, "maximum": VOLUME_STEP_MAX},
            "muted": {"type": "boolean", "description": "For mute."},
            "key": {
                "type": "string",
                "enum": sorted({*MEDIA_KEYS, *NAVIGATION_KEYS}),
                "description": "For media or navigate.",
            },
            "repeat": {"type": "integer", "minimum": 1, "maximum": 10, "description": "For navigate."},
            "app": {"type": "string", "description": "For open: the app's name."},
            "query": {"type": "string", "description": "For youtube: what to search for."},
        },
        "required": ["action"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> dict[str, Any]:
        """Read or control the TV."""
        tv = deps.tv
        if tv is None:
            return {"error": "No TV is set up."}
        action = kwargs.get("action")
        logger.info("Tool call: tv action=%s", action)
        try:
            return await self._run(tv, action, kwargs)
        except TVError as error:
            logger.warning("tv %s failed: %s", action, error)
            return {"error": f"The TV did not respond: {error}", "instruction": "Tell the person. Do not retry."}

    async def _run(self, tv: Any, action: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        if action == "status":
            state = await tv.state()
            return {
                "on": state.is_on,
                "app": state.app_name,
                "volume": state.volume,
                "muted": state.muted,
            }
        if action == "power":
            on = kwargs.get("on")
            if on is None:
                return {"error": "power needs on=true or on=false."}
            if await tv.power(bool(on)):
                return {"ok": True, "on": bool(on)}
            # The key was sent; this TV can take over 6 s to wake from a long standby before it reports.
            return {
                "ok": True,
                "on": bool(on),
                "confirmed": False,
                "note": "The power key was sent but the TV has not reported the change yet; it can take a few "
                "seconds. Say it is turning on (or off). Do not retry: pressing power again would undo it.",
            }
        if action == "volume":
            level, change = kwargs.get("level"), kwargs.get("change")
            if level is not None:
                await tv.set_volume(int(level))
                return {"ok": True, "volume": int(level)}
            if change in ("up", "down"):
                steps = max(1, min(VOLUME_STEP_MAX, int(kwargs.get("steps") or VOLUME_STEP_DEFAULT)))
                await tv.key("VOLUME_UP" if change == "up" else "VOLUME_DOWN", steps)
                return {"ok": True, "change": change, "steps": steps}
            return {"error": "volume needs level 0-100 or change=up/down."}
        if action == "mute":
            muted = kwargs.get("muted")
            await tv.set_muted(True if muted is None else bool(muted))
            return {"ok": True, "muted": True if muted is None else bool(muted)}
        if action == "media":
            key = MEDIA_KEYS.get(str(kwargs.get("key")))
            if key is None:
                return {"error": "media needs key: " + ", ".join(MEDIA_KEYS)}
            await tv.key(key)
            return {"ok": True, "key": kwargs.get("key")}
        if action == "navigate":
            key = NAVIGATION_KEYS.get(str(kwargs.get("key")))
            if key is None:
                return {"error": "navigate needs key: " + ", ".join(NAVIGATION_KEYS)}
            repeat = max(1, min(10, int(kwargs.get("repeat") or 1)))
            await tv.key(key, repeat)
            return {"ok": True, "key": kwargs.get("key"), "repeat": repeat}
        if action == "open":
            name = str(kwargs.get("app") or "").strip()
            package = resolve_app(name)
            if package is None:
                return {"error": f"Unknown app {name!r}.", "apps": sorted({app.title() for app in APPS})}
            await self._wake(tv)
            if await tv.open_app(package):
                return {"ok": True, "app": name}
            return {
                "ok": False,
                "error": f"{name} did not open; it is probably not installed on this TV.",
                "instruction": "Tell the person. Do not retry.",
            }
        if action == "youtube":
            query = str(kwargs.get("query") or "").strip()
            if not query:
                return {"error": "youtube needs a query."}
            await self._wake(tv)
            await tv.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
            return {"ok": True, "searched": query}
        return {"error": "Unknown action."}

    @staticmethod
    async def _wake(tv: Any) -> None:
        state = await tv.state()
        if state.is_on is False:
            await tv.power(True)
            await asyncio.sleep(WAKE_SETTLE_S)
