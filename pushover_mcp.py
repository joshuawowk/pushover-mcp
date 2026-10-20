#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.2.0,<2.0",
#     "httpx>=0.27",
# ]
# ///
"""
pushover_mcp — MCP server for the Pushover notification API (https://pushover.net/api).

Configuration (environment variables):
    PUSHOVER_TOKEN      (required) Application API token.
    PUSHOVER_USER_KEY   (optional) Default user/group key. Tools accept a `user`
                        override; if neither is set, sending fails with a clear error.
    PUSHOVER_DEVICE     (optional) Default device name(s), comma-separated.

Transports:
    stdio (default):        uv run pushover_mcp.py
    streamable HTTP:        uv run pushover_mcp.py --http [--host 0.0.0.0] [--port 8735]

All calls honor Pushover's "be friendly" guidance: 4xx errors are surfaced with the
API's `errors` array and never retried; 5xx errors are retried once after 5 seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

API_BASE = "https://api.pushover.net/1"
VALID_SOUNDS_HINT = "Use pushover_list_sounds to see valid sound names."

mcp = FastMCP("pushover_mcp")

# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


def _token() -> str:
    token = os.environ.get("PUSHOVER_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "PUSHOVER_TOKEN environment variable is not set. "
            "Set it to your Pushover application API token."
        )
    return token


def _default_user() -> str:
    return os.environ.get("PUSHOVER_USER_KEY", "").strip()


async def _request(
    method: str,
    path: str,
    data: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Call the Pushover API. Returns parsed JSON plus quota headers when present.

    5xx / network errors are retried once after 5 s (per Pushover's guidance);
    4xx errors are returned immediately with the API's error details.
    """
    client = _get_client()
    url = f"{API_BASE}{path}"
    last_exc: Optional[Exception] = None

    for attempt in (1, 2):
        try:
            resp = await client.request(method, url, data=data, params=params)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == 1:
                await asyncio.sleep(5)
                continue
            raise RuntimeError(f"Could not reach Pushover API: {exc}") from exc

        if resp.status_code >= 500:
            if attempt == 1:
                await asyncio.sleep(5)
                continue
            raise RuntimeError(
                f"Pushover API server error (HTTP {resp.status_code}). "
                "Temporary problem on Pushover's side; try again later."
            )

        try:
            body: dict[str, Any] = resp.json()
        except ValueError:
            body = {"status": 0, "raw": resp.text[:500]}

        # Attach quota headers when Pushover provides them (messages endpoint).
        limits = {}
        for header, key in (
            ("X-Limit-App-Limit", "monthly_limit"),
            ("X-Limit-App-Remaining", "monthly_remaining"),
            ("X-Limit-App-Reset", "limit_reset_unix"),
        ):
            if header in resp.headers:
                limits[key] = resp.headers[header]
        if limits:
            body["quota"] = limits

        body["_http_status"] = resp.status_code
        return body

    raise RuntimeError(f"Could not reach Pushover API: {last_exc}")


def _format_result(body: dict[str, Any], success_note: str = "") -> str:
    """Render an API response for the LLM, with actionable errors."""
    http_status = body.pop("_http_status", None)
    if body.get("status") == 1:
        out = dict(body)
        if success_note:
            out["note"] = success_note
        return json.dumps(out, indent=2)

    errors = body.get("errors") or []
    hints = []
    joined = " ".join(errors).lower()
    if "token" in joined:
        hints.append("Check the PUSHOVER_TOKEN environment variable.")
    if "user" in joined:
        hints.append(
            "Check the user key (PUSHOVER_USER_KEY or the `user` parameter). "
            "It must be a 30-character key from the Pushover dashboard, not an email."
        )
    if "sound" in joined:
        hints.append(VALID_SOUNDS_HINT)
    if http_status == 429:
        hints.append(
            "Monthly message limit reached. Limits reset at 00:00 Central Time "
            "on the 1st of each month; see pushover_get_limits."
        )
    return json.dumps(
        {
            "status": "error",
            "http_status": http_status,
            "errors": errors or [body],
            "hints": hints,
        },
        indent=2,
    )


def _resolve_user(user: Optional[str]) -> str:
    resolved = (user or "").strip() or _default_user()
    if not resolved:
        raise RuntimeError(
            "No user key available. Pass the `user` parameter or set the "
            "PUSHOVER_USER_KEY environment variable (find your key at "
            "https://pushover.net/dashboard)."
        )
    return resolved


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="pushover_send_message",
    annotations={
        "title": "Send Pushover Notification",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def pushover_send_message(
    message: str = Field(..., description="Message body (max 1024 chars). URLs in the body become clickable links.", max_length=1024),
    title: Optional[str] = Field(None, description="Message title (max 250 chars). Defaults to the app name.", max_length=250),
    priority: int = Field(0, description="-2 silent, -1 quiet, 0 normal, 1 high (bypasses quiet hours), 2 emergency (repeats until acknowledged; requires retry+expire).", ge=-2, le=2),
    retry: Optional[int] = Field(None, description="Emergency only: seconds between re-notifications (min 30).", ge=30),
    expire: Optional[int] = Field(None, description="Emergency only: seconds until retries stop (max 10800 = 3h).", ge=30, le=10800),
    sound: Optional[str] = Field(None, description="Notification sound name (e.g. 'siren', 'magic', 'none'). Omit for the user's default. " + VALID_SOUNDS_HINT),
    url: Optional[str] = Field(None, description="Supplementary URL shown below the message (max 512 chars).", max_length=512),
    url_title: Optional[str] = Field(None, description="Display title for the supplementary URL (max 100 chars).", max_length=100),
    device: Optional[str] = Field(None, description="Target specific device(s), comma-separated (e.g. 'iphone,desktop'). Omit for all devices."),
    html: bool = Field(False, description="Set true to render <b>, <i>, <u>, <font>, <a> tags in the message."),
    monospace: bool = Field(False, description="Set true for monospace formatting. Mutually exclusive with html."),
    timestamp: Optional[int] = Field(None, description="Unix timestamp to display instead of receipt time.", ge=0),
    ttl: Optional[int] = Field(None, description="Seconds until the message auto-deletes from devices. Ignored for priority 2.", ge=1),
    tags: Optional[str] = Field(None, description="Emergency only: comma-separated tags stored on the receipt, usable with pushover_cancel_retries."),
    user: Optional[str] = Field(None, description="User/group key override, or comma-separated list of up to 50 keys. Defaults to PUSHOVER_USER_KEY."),
) -> str:
    """Send a push notification via Pushover.

    Emergency priority (2) requires `retry` and `expire` and returns a `receipt`
    for use with pushover_check_receipt / pushover_cancel_retries. The response
    includes remaining monthly message quota.
    """
    if html and monospace:
        raise RuntimeError("html and monospace are mutually exclusive; set at most one.")
    if priority == 2:
        if retry is None or expire is None:
            raise RuntimeError(
                "Emergency priority (2) requires both `retry` (>=30s) and `expire` (<=10800s)."
            )
    data: dict[str, Any] = {
        "token": _token(),
        "user": _resolve_user(user),
        "message": message,
        "priority": priority,
    }
    for key, value in (
        ("title", title), ("retry", retry), ("expire", expire), ("sound", sound),
        ("url", url), ("url_title", url_title), ("device", device or os.environ.get("PUSHOVER_DEVICE") or None),
        ("timestamp", timestamp), ("ttl", ttl), ("tags", tags),
    ):
        if value is not None and value != "":
            data[key] = value
    if html:
        data["html"] = 1
    if monospace:
        data["monospace"] = 1

    body = await _request("POST", "/messages.json", data=data)
    note = "Message queued for delivery."
    if "receipt" in body:
        note += " Emergency message: track acknowledgement with pushover_check_receipt."
    return _format_result(body, note)


@mcp.tool(
    name="pushover_validate_user",
    annotations={
        "title": "Validate Pushover User/Group Key",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def pushover_validate_user(
    user: Optional[str] = Field(None, description="User or group key to validate. Defaults to PUSHOVER_USER_KEY."),
    device: Optional[str] = Field(None, description="Optionally validate a specific device name on the account."),
) -> str:
    """Check that a user/group key is valid and has at least one active device.

    Returns the account's active device names and licensed platforms.
    """
    data = {"token": _token(), "user": _resolve_user(user)}
    if device:
        data["device"] = device
    body = await _request("POST", "/users/validate.json", data=data)
    return _format_result(body, "User key is valid.")


@mcp.tool(
    name="pushover_list_sounds",
    annotations={
        "title": "List Pushover Notification Sounds",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def pushover_list_sounds() -> str:
    """List valid notification sound names (built-in plus any custom sounds
    uploaded to the account that owns the API token)."""
    body = await _request("GET", "/sounds.json", params={"token": _token()})
    return _format_result(body)


@mcp.tool(
    name="pushover_get_limits",
    annotations={
        "title": "Get Pushover Message Quota",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def pushover_get_limits() -> str:
    """Get the monthly message quota: limit, messages remaining, and the Unix
    timestamp when the counter resets (00:00 Central Time, 1st of each month)."""
    body = await _request("GET", "/apps/limits.json", params={"token": _token()})
    if body.get("status") == 1 and "reset" in body:
        body["reset_human_utc"] = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(body["reset"]))
        )
    return _format_result(body)


@mcp.tool(
    name="pushover_check_receipt",
    annotations={
        "title": "Check Emergency Notification Receipt",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def pushover_check_receipt(
    receipt: str = Field(..., description="Receipt ID returned by an emergency (priority 2) pushover_send_message call.", min_length=1),
) -> str:
    """Get the status of an emergency-priority notification: whether it was
    acknowledged (and by whom/when), last delivery time, and whether it expired."""
    body = await _request("GET", f"/receipts/{receipt}.json", params={"token": _token()})
    if body.get("status") == 1:
        for field in ("acknowledged_at", "last_delivered_at", "expires_at", "called_back_at"):
            value = body.get(field)
            if value:
                body[f"{field}_human_utc"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(value))
                )
    return _format_result(body)


@mcp.tool(
    name="pushover_cancel_retries",
    annotations={
        "title": "Cancel Emergency Notification Retries",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def pushover_cancel_retries(
    receipt: Optional[str] = Field(None, description="Receipt ID of the emergency notification to stop retrying."),
    tag: Optional[str] = Field(None, description="Cancel all emergency retries whose receipts carry this tag (set via `tags` when sending). Provide either receipt or tag."),
) -> str:
    """Stop the retry cycle of un-acknowledged emergency (priority 2)
    notifications, by receipt ID or by tag."""
    if bool(receipt) == bool(tag):
        raise RuntimeError("Provide exactly one of `receipt` or `tag`.")
    path = f"/receipts/{receipt}/cancel.json" if receipt else f"/receipts/cancel_by_tag/{tag}.json"
    body = await _request("POST", path, data={"token": _token()})
    return _format_result(body, "Retries cancelled.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Pushover MCP server")
    parser.add_argument("--http", action="store_true", help="Serve streamable HTTP instead of stdio")
    parser.add_argument("--stdio", action="store_true", help="Serve over stdio (the default when --http is absent; explicit form for container use)")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8735, help="HTTP port (default 8735)")
    parser.add_argument(
        "--allowed-hosts",
        default=os.environ.get("PUSHOVER_MCP_ALLOWED_HOSTS", ""),
        help=(
            "Comma-separated Host values accepted by the DNS-rebinding guard "
            "(e.g. '192.0.2.10:8735,pushover.example.com'). Use '*' to disable the "
            "check. Defaults to localhost only, which rejects LAN clients. "
            "Also settable via PUSHOVER_MCP_ALLOWED_HOSTS."
        ),
    )
    args = parser.parse_args()
    if args.http and args.stdio:
        parser.error("--http and --stdio are mutually exclusive")

    try:
        _token()  # fail fast with a clear message
    except RuntimeError as exc:
        print(f"pushover_mcp: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.stateless_http = True

        entries = [h.strip() for h in args.allowed_hosts.split(",") if h.strip()]
        if "*" in entries:
            # Explicit opt-out: the proxy or network boundary is doing the filtering.
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
        elif entries:
            origins = [f"http://{h}" for h in entries] + [f"https://{h}" for h in entries]
            mcp.settings.transport_security = TransportSecuritySettings(
                allowed_hosts=entries + ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"],
                allowed_origins=origins + ["http://localhost:*", "http://127.0.0.1:*"],
            )
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
