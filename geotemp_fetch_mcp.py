#!/usr/bin/env python3
"""
Fetch weather and attractions from GeoTemp Travel MCP Server.
https://mcp-travel-data.onrender.com/sse — get_weather, get_attractions.

Requires: pip install mcp (sse_client used for GeoTemp).
"""

import json
from typing import Any

# MCP client
from mcp import ClientSession
from mcp.client.sse import sse_client

GEOTEMP_MCP_URL = "https://mcp-travel-data.onrender.com/sse"


def _get_block_text(block: Any) -> str | None:
    if block is None:
        return None
    if isinstance(block, dict):
        return block.get("text")
    if hasattr(block, "text"):
        return getattr(block, "text")
    if hasattr(block, "model_dump"):
        return block.model_dump().get("text")
    return None


def _parse_tool_result(result: Any) -> dict | list | None:
    """Extract JSON-serializable data from MCP CallToolResult (content[0].text pattern)."""
    if result is None:
        return None
    # content: list of blocks with .text (GeoTemp / standard MCP)
    content = getattr(result, "content", None)
    if content and len(content) > 0:
        text = _get_block_text(content[0])
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text[:500]}
    # structuredContent fallback
    if getattr(result, "structuredContent", None) is not None:
        sc = result.structuredContent
        if isinstance(sc, (dict, list)):
            return sc
        if isinstance(sc, str):
            try:
                return json.loads(sc)
            except json.JSONDecodeError:
                return {"raw": sc}
    # content with multiple blocks
    if content:
        parts = [_get_block_text(b) for b in content if _get_block_text(b)]
        if parts:
            for p in parts:
                try:
                    return json.loads(p)
                except json.JSONDecodeError:
                    continue
    return None


async def get_weather(
    session: ClientSession,
    city_name: str,
    start_date: str,
    end_date: str,
    *,
    month: int | None = None,
) -> list[dict] | dict | None:
    """Get weather for a city. Uses month (1–12) if given, else start_date/end_date (YYYY-MM-DD). Returns list of daily data or dict."""
    if month is not None:
        params = {"city_name": city_name, "month": month}
    else:
        params = {"city_name": city_name, "start_date": start_date, "end_date": end_date}
    result = await session.call_tool("get_weather", params)
    data = _parse_tool_result(result)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "days" in data:
        return data.get("days", [])
    if isinstance(data, dict) and "weather" in data:
        return data["weather"]
    return data if isinstance(data, (list, dict)) else None


async def get_attractions(
    session: ClientSession,
    city_name: str,
    limit: int = 10,
) -> list[dict] | None:
    """Get attractions for a city. Returns list of attraction dicts."""
    result = await session.call_tool(
        "get_attractions",
        {"city_name": city_name, "limit": limit},
    )
    data = _parse_tool_result(result)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "attractions" in data:
        return data["attractions"]
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data if isinstance(data, list) else None
