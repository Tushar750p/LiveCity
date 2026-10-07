# LiveCity

Worldwide live map for essential-service disruptions: electricity, water, internet, and mobile network.

## MVP
- Interactive OpenStreetMap map
- Live outage markers from API
- Report outage / restored status
- Service filters
- Confidence scoring
- Nearby report summary
- FastAPI backend + persistent SQLite MVP (PostgreSQL/PostGIS planned for production scale)
- WebSocket-ready live update channel

## Run locally
See `backend/README.md`. Run `docker compose up --build` for the full demo.

> MVP reports are community reports. They are not official utility data unless an official source is explicitly integrated.
