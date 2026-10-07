"""Background source polling for LiveCity.

Only public/configured sources are polled. Provider-specific integrations can be
added as independent adapters without changing the API contract.
"""
from __future__ import annotations

import asyncio
import logging

from .sources import fetch_cloudflare_outages, fetch_ioda_alerts

log = logging.getLogger("livecity.sources")


async def poll_sources() -> None:
    interval = 300
    try:
        import os
        interval = max(60, int(os.getenv("SOURCE_POLL_SECONDS", "300")))
    except ValueError:
        pass

    while True:
        try:
            # Fetching is intentionally non-destructive for now. The next ingestion
            # layer will normalize source-specific events into LiveCity incidents.
            ioda = await fetch_ioda_alerts(limit=25)
            cloudflare = await fetch_cloudflare_outages(days=1)
            log.info("source poll: ioda=%s cloudflare=%s", len(ioda), len(cloudflare))
        except Exception:
            log.exception("external source polling failed")
        await asyncio.sleep(interval)
