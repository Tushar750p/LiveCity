# LiveCity

**See what's working. See what's not.**

LiveCity is a worldwide live map for essential-service disruptions: electricity, water, internet, and mobile network.

## What is implemented

- Interactive worldwide OpenStreetMap map
- Electricity / water / internet / mobile service layers
- Community outage reporting and confirmations
- Live WebSocket updates
- External-source ingestion pipeline
- IODA Internet outage signals
- Cloudflare Radar outage adapter
- Optional ArcGIS power and water FeatureServer adapters
- Optional OpenCelliD mobile-cell lookup
- Source registry and source health endpoints
- Persistent external incident history
- Automatic incident refresh and stale-event expiry
- Confidence / verification scoring
- Duplicate-safe source incident upserts
- Incident freshness (last_seen_at)
- Service filtering and current-location reporting
- Responsive map UI with source/freshness labels
- Docker Compose setup
- GitHub Actions CI for backend compilation and frontend production build

## Data trust model

LiveCity separates **community reports** from external measurement/provider signals. It does not label a third-party signal as an official utility confirmation unless the provider source is explicitly configured and identified.

IODA is a measurement-based Internet disruption system using BGP, active probing, and network-telescope signals. Cloudflare Radar exposes outage annotations including location, ASN, outage type/scope, cause, and start/end times. These are useful external signals, but they are not substitutes for a utility's official customer outage system.

## Optional provider configuration

Provider-specific feeds are intentionally configured through environment variables rather than hard-coded credentials:

- RADAR_API_TOKEN
- POWER_ARCGIS_LAYER_URL
- WATER_ARCGIS_LAYER_URL
- OPENCELLID_API_KEY

OpenCelliD is used for **cell location lookup**, not as a standalone mobile-outage detector.

## Run locally

    docker compose up --build

- Frontend: http://localhost:4173
- API: http://localhost:8000
- API health: http://localhost:8000/health
- Live incidents: http://localhost:8000/api/incidents/live
- Source registry: http://localhost:8000/api/sources/registry

See `backend/README.md` for backend details.

## Production roadmap

For a public worldwide deployment, the remaining environment-specific work is operational rather than core product logic: configure approved utility/provider feeds, add production secrets, deploy managed PostgreSQL/PostGIS/Redis, configure a domain and HTTPS, and add authentication/notification infrastructure as required.

LiveCity never fabricates outage data when a provider feed is unavailable.