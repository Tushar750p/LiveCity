"""LiveCity external data adapters.

Adapters are deliberately isolated from the core incident model. Public sources can
run without credentials; provider-specific sources are enabled only when configured.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

import httpx


IODA_BASE = "https://api.ioda.inetintel.cc.gatech.edu/v2"
CLOUDFLARE_BASE = "https://api.cloudflare.com/client/v4/radar"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def fetch_ioda_alerts(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch current IODA outage alerts. IODA exposes this public API without a token."""
    params = {"limit": limit}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{IODA_BASE}/outages/alerts", params=params)
        response.raise_for_status()
        payload = response.json()
    alerts = payload.get("data", payload.get("alerts", []))
    if isinstance(alerts, dict):
        alerts = alerts.get("alerts", [])
    return alerts if isinstance(alerts, list) else []


async def fetch_cloudflare_outages(days: int = 1) -> list[dict[str, Any]]:
    """Fetch Cloudflare Radar outage annotations when RADAR_API_TOKEN is configured."""
    token = os.getenv("RADAR_API_TOKEN")
    if not token:
        return []
    params = {"limit": 100, "dateRange": f"{days}d", "format": "json"}
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{CLOUDFLARE_BASE}/annotations/outages",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
    return payload.get("result", {}).get("annotations", [])


async def source_health() -> list[dict[str, Any]]:
    """Return a lightweight status page for configured external sources."""
    result: list[dict[str, Any]] = []

    try:
        alerts = await fetch_ioda_alerts(limit=1)
        result.append({
            "id": "ioda",
            "service": "internet",
            "configured": True,
            "reachable": True,
            "records": len(alerts),
            "checked_at": _utc_now().isoformat(),
        })
    except Exception as exc:
        result.append({
            "id": "ioda",
            "service": "internet",
            "configured": True,
            "reachable": False,
            "error": str(exc),
            "checked_at": _utc_now().isoformat(),
        })

    configured = bool(os.getenv("RADAR_API_TOKEN"))
    result.append({
        "id": "cloudflare-radar",
        "service": "internet",
        "configured": configured,
        "reachable": None if not configured else True,
        "checked_at": _utc_now().isoformat(),
    })
    return result
