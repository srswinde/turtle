"""Claude-vision analysis of a departure — which way did Cassini leave?

Calls the Anthropic Messages API directly over httpx (no SDK, keeps the web
image lean + torch-free). A forced tool call guarantees structured output.
"""
from __future__ import annotations

import base64
import logging

import httpx

from app.settings import settings

log = logging.getLogger("swahle.naturalist.vision")

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

DIRECTIONS = ["left", "right", "up", "down",
              "up-left", "up-right", "down-left", "down-right",
              "toward-camera", "away-from-camera", "stayed", "unclear"]

SYSTEM = (
    "You are analyzing a short chronological sequence of still frames from a "
    "fixed camera pointed at a tortoise's burrow (called 'the hole'). The "
    "tortoise is Cassini. Determine which direction Cassini moved as he LEFT "
    "the frame. Directions are relative to the image as you see it: use the "
    "eight compass-style options (left, right, up, down, and the diagonals "
    "up-left, up-right, down-left, down-right) when he walks across the ground "
    "out of frame — pick the diagonal when the motion is clearly diagonal. Use "
    "'away-from-camera' ONLY when he backs directly into the burrow/hole (gets "
    "smaller, disappears into the ground rather than crossing the frame edge), "
    "and 'toward-camera' when he approaches. Use 'stayed' if he is still clearly "
    "in the last frame, and 'unclear' if you genuinely cannot tell. Base the "
    "direction on his motion across the whole sequence, not a single frame."
)

TOOL = {
    "name": "report_exit",
    "description": "Report how and in which direction the tortoise left the frame.",
    "input_schema": {
        "type": "object",
        "properties": {
            "present": {"type": "boolean",
                        "description": "Is a tortoise visible in any frame?"},
            "direction": {"type": "string", "enum": DIRECTIONS},
            "confidence": {"type": "number",
                           "description": "0..1 confidence in the direction"},
            "description": {"type": "string",
                            "description": "One short sentence describing the exit."},
        },
        "required": ["present", "direction", "confidence", "description"],
    },
}


class VisionError(RuntimeError):
    pass


def _image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.b64encode(jpeg).decode("ascii")}}


async def analyze_departure(frames: list[bytes]) -> dict:
    """Send the frames to Claude and return the structured report_exit input."""
    if not settings.anthropic_api_key:
        raise VisionError("no_api_key")
    if not frames:
        raise VisionError("no_frames")

    content: list[dict] = [{
        "type": "text",
        "text": (f"Here are {len(frames)} frames in chronological order "
                 "(earliest first). Which way did Cassini leave?"),
    }]
    for i, jpeg in enumerate(frames, 1):
        content.append({"type": "text", "text": f"Frame {i}:"})
        content.append(_image_block(jpeg))

    payload = {
        "model": settings.anthropic_model,
        "max_tokens": 400,
        "system": SYSTEM,
        "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": "report_exit"},
        "messages": [{"role": "user", "content": content}],
    }
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(API_URL, json=payload, headers=headers)
    if resp.status_code != 200:
        log.error("Anthropic API %s: %s", resp.status_code, resp.text[:400])
        raise VisionError(f"api_error_{resp.status_code}")

    data = resp.json()
    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "report_exit":
            out = block.get("input", {})
            out["direction"] = out.get("direction") or "unclear"
            return out
    raise VisionError("no_tool_use")
