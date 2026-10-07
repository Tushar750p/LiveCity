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


async def fetch_arcgis_layer(service_url: str, where: str = "1=1", out_fields: str = "*", out_sr: int = 4326) -> list[dict[str, Any]]:
    """Generic public ArcGIS FeatureServer query adapter."""
    url = service_url.rstrip("/") + "/query"
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true",
        "outSR": out_sr,
        "f": "json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))
    return payload.get("features", [])


async def fetch_water_outages(service_url: str | None = None) -> list[dict[str, Any]]:
    """Fetch public water interruption features from a configured ArcGIS layer."""
    url = service_url or os.getenv("WATER_ARCGIS_LAYER_URL")
    if not url:
        return []
    return await fetch_arcgis_layer(url)


async def fetch_power_outages(service_url: str | None = None) -> list[dict[str, Any]]:
    """Fetch public power outage features from a configured ArcGIS layer."""
    url = service_url or os.getenv("POWER_ARCGIS_LAYER_URL")
    if not url:
        return []
    return await fetch_arcgis_layer(url)


async def fetch_mobile_cells(
    mcc: int, mnc: int, lac: int, cellid: int, radio: str | None = None
) -> dict[str, Any]:
    """Look up a mobile cell using OpenCelliD when OPENCELLID_API_KEY is configured."""
    key = os.getenv("OPENCELLID_API_KEY")
    if not key:
        return {"configured": False, "cell": None}
    params: dict[str, Any] = {
        "key": key, "mcc": mcc, "mnc": mnc, "lac": lac,
        "cellid": cellid, "format": "json",
    }
    if radio:
        params["radio"] = radio
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get("https://opencellid.org/cell/get", params=params)
        response.raise_for_status()
        return {"configured": True, "cell": response.json()}
