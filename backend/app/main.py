from datetime import datetime, timezone, timedelta
from typing import Literal, Any
from uuid import uuid4
import asyncio, json, math, os, sqlite3

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .sources import (
    fetch_ioda_alerts, fetch_cloudflare_outages, source_health,
    fetch_water_outages, fetch_power_outages, fetch_mobile_cells,
    source_registry, normalize_source_features,
)

Service = Literal["electricity", "water", "internet", "mobile"]
Status = Literal["down", "partial", "restored"]

DB_PATH = os.getenv("DB_PATH", "/tmp/livecity.db")
SOURCE_POLL_SECONDS = max(60, int(os.getenv("SOURCE_POLL_SECONDS", "300")))
INCIDENT_TTL_MINUTES = max(15, int(os.getenv("INCIDENT_TTL_MINUTES", "360")))
lock = asyncio.Lock()
clients: set[WebSocket] = set()

class ReportIn(BaseModel):
    service: Service
    status: Status = "down"
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    description: str = Field(default="", max_length=500)

class Report(ReportIn):
    id: str
    created_at: datetime
    confirmations: int = 0
    confidence: int = 50

class Incident(BaseModel):
    id: str
    service: Service
    status: Status
    source: str
    source_url: str | None = None
    location: str | None = None
    country: str | None = None
    lat: float | None = None
    lng: float | None = None
    confidence: int = 50
    observed_at: str | None = None
    last_seen_at: datetime
    description: str = ""
    active: bool = True

app = FastAPI(title="LiveCity API", version="0.3.0")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY, service TEXT NOT NULL, status TEXT NOT NULL,
        lat REAL NOT NULL, lng REAL NOT NULL, description TEXT NOT NULL,
        created_at TEXT NOT NULL, confirmations INTEGER NOT NULL DEFAULT 0,
        confidence INTEGER NOT NULL DEFAULT 50)""")
    con.execute("""CREATE TABLE IF NOT EXISTS source_incidents (
        id TEXT PRIMARY KEY, service TEXT NOT NULL, status TEXT NOT NULL,
        source TEXT NOT NULL, source_url TEXT, location TEXT, country TEXT,
        lat REAL, lng REAL, confidence INTEGER NOT NULL DEFAULT 50,
        observed_at TEXT, last_seen_at TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
        raw_json TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_source_active ON source_incidents(active, service)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_source_seen ON source_incidents(last_seen_at)")
    con.commit()
    return con

def row_to_report(row):
    return Report(
        id=row["id"], service=row["service"], status=row["status"],
        lat=row["lat"], lng=row["lng"], description=row["description"],
        created_at=datetime.fromisoformat(row["created_at"]),
        confirmations=row["confirmations"], confidence=row["confidence"],
    )

def row_to_incident(row):
    return Incident(
        id=row["id"], service=row["service"], status=row["status"], source=row["source"],
        source_url=row["source_url"], location=row["location"], country=row["country"],
        lat=row["lat"], lng=row["lng"], confidence=row["confidence"],
        observed_at=row["observed_at"], last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        description=row["description"], active=bool(row["active"]),
    )

def get_all(service=None, status=None):
    con = db()
    q = "SELECT * FROM reports WHERE 1=1"
    args = []
    if service: q += " AND service=?"; args.append(service)
    if status: q += " AND status=?"; args.append(status)
    q += " ORDER BY created_at DESC"
    rows = [row_to_report(x) for x in con.execute(q, args).fetchall()]
    con.close()
    return rows

def get_incidents(service=None, active=True):
    con = db()
    q = "SELECT * FROM source_incidents WHERE 1=1"
    args = []
    if active: q += " AND active=1"
    if service: q += " AND service=?"; args.append(service)
    q += " ORDER BY last_seen_at DESC"
    rows = [row_to_incident(x) for x in con.execute(q, args).fetchall()]
    con.close()
    return rows

def confidence_for(service, lat, lng):
    nearby = [
        r for r in get_all(service=service)
        if r.status != "restored" and abs(r.lat-lat) < 0.01 and abs(r.lng-lng) < 0.01
    ]
    return min(98, 50 + max(0, len(nearby)-1) * 12)

def verification_score(base: int, service: str, lat: float | None, lng: float | None):
    score = max(0, min(100, int(base)))
    if lat is not None and lng is not None:
        score += 5
    nearby = 0
    if lat is not None and lng is not None:
        for r in get_all(service=service):
            if r.status != "restored" and abs(r.lat-lat) < 0.02 and abs(r.lng-lng) < 0.02:
                nearby += 1
    return min(99, score + min(15, nearby * 5))

async def broadcast(payload: dict):
    dead = []
    for ws in list(clients):
        try: await ws.send_json(payload)
        except Exception: dead.append(ws)
    for ws in dead: clients.discard(ws)

def normalize_ioda(item: dict[str, Any], index: int):
    entity = item.get("entity", {}) if isinstance(item, dict) else {}
    return {
        "id": "ioda:" + str(item.get("id", item.get("entityCode", index))),
        "service": "internet", "status": "down", "source": "IODA",
        "source_url": "https://ioda.inetintel.cc.gatech.edu/",
        "location": entity.get("name") or item.get("entityName") or item.get("entityCode"),
        "country": entity.get("code") or item.get("entityCode"),
        "lat": item.get("lat"), "lng": item.get("lng"),
        "confidence": 90,
        "observed_at": item.get("time") or item.get("createdAt"),
        "description": item.get("description") or "Internet connectivity disruption detected by IODA.",
        "raw": item,
    }

def normalize_cloudflare(item: dict[str, Any], index: int):
    locations = item.get("locations") or []
    level = item.get("level") or 85
    cause = (item.get("outage") or {}).get("outageCause")
    status = "partial" if str((item.get("outage") or {}).get("outageType", "")).upper() == "REGIONAL" else "down"
    return {
        "id": "cloudflare:" + str(item.get("id", index)),
        "service": "internet", "status": status, "source": "Cloudflare Radar",
        "source_url": item.get("linkedUrl") or "https://radar.cloudflare.com/",
        "location": ", ".join(locations) if locations else item.get("scope"),
        "country": locations[0] if locations else None, "lat": None, "lng": None,
        "confidence": verification_score(level, "internet", None, None),
        "observed_at": item.get("startDate"),
        "description": item.get("description") or cause or "Internet outage detected by Cloudflare Radar.",
        "raw": item,
    }

def persist_incidents(items: list[dict[str, Any]]):
    now = datetime.now(timezone.utc)
    con = db()
    for item in items:
        incident_id = str(item["id"])
        con.execute("""INSERT INTO source_incidents
            (id,service,status,source,source_url,location,country,lat,lng,confidence,observed_at,last_seen_at,description,active,raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              status=excluded.status, source_url=excluded.source_url, location=excluded.location,
              country=excluded.country, lat=excluded.lat, lng=excluded.lng, confidence=excluded.confidence,
              observed_at=excluded.observed_at, last_seen_at=excluded.last_seen_at,
              description=excluded.description, active=1, raw_json=excluded.raw_json""",
            (incident_id, item["service"], item["status"], item["source"], item.get("source_url"),
             item.get("location"), item.get("country"), item.get("lat"), item.get("lng"),
             int(item.get("confidence", 50)), item.get("observed_at"), now.isoformat(),
             str(item.get("description") or ""), 1, json.dumps(item.get("raw", {}), default=str)))
    cutoff = (now - timedelta(minutes=INCIDENT_TTL_MINUTES)).isoformat()
    con.execute("UPDATE source_incidents SET active=0 WHERE last_seen_at < ? AND active=1", (cutoff,))
    con.commit()
    con.close()

async def sync_external_incidents():
    items = []
    try:
        items.extend(normalize_ioda(x, i) for i, x in enumerate(await fetch_ioda_alerts(limit=100)))
    except Exception:
        pass
    try:
        items.extend(normalize_cloudflare(x, i) for i, x in enumerate(await fetch_cloudflare_outages(days=1)))
    except Exception:
        pass
    # Provider-specific electricity/water feeds are optional and are normalized when configured.
    try:
        items.extend(normalize_source_features(await fetch_power_outages(), "electricity", "arcgis-power"))
    except Exception:
        pass
    try:
        items.extend(normalize_source_features(await fetch_water_outages(), "water", "arcgis-water"))
    except Exception:
        pass
    if items:
        persist_incidents(items)
    else:
        # Still expire old records when upstreams return no active events.
        persist_incidents([])
    return get_incidents()

async def source_loop():
    while True:
        try: await sync_external_incidents()
        except Exception: pass
        await asyncio.sleep(SOURCE_POLL_SECONDS)

@app.mount("/", StaticFiles(directory="/app/web", html=True), name="web")

@app.on_event("startup")
async def startup():
    db().close()
    app.state.source_task = asyncio.create_task(source_loop())

@app.on_event("shutdown")
async def shutdown():
    task = getattr(app.state, "source_task", None)
    if task: task.cancel()

@app.get("/health")
def health():
    return {"status": "ok", "service": "livecity-api", "reports": len(get_all()), "active_incidents": len(get_incidents())}

@app.get("/api/sources/registry")
def get_source_registry():
    return {"sources": source_registry()}

@app.get("/api/sources/health")
async def get_source_health():
    return {"sources": await source_health()}

@app.get("/api/sources/internet/ioda")
async def get_ioda_alerts(limit: int = Query(100, ge=1, le=500)):
    return {"source": "ioda", "alerts": await fetch_ioda_alerts(limit=limit)}

@app.get("/api/sources/internet/cloudflare")
async def get_cloudflare_outages(days: int = Query(1, ge=1, le=7)):
    return {"source": "cloudflare-radar", "configured": bool(os.getenv("RADAR_API_TOKEN")), "outages": await fetch_cloudflare_outages(days=days)}

@app.get("/api/incidents/live")
async def get_live_incidents(service: Service | None = None, refresh: bool = False):
    if refresh:
        await sync_external_incidents()
    incidents = get_incidents(service=service)
    return {"count": len(incidents), "incidents": [x.model_dump(mode="json") for x in incidents]}

@app.get("/api/incidents/history")
def incident_history(service: Service | None = None, limit: int = Query(100, ge=1, le=1000)):
    con = db()
    q = "SELECT * FROM source_incidents WHERE 1=1"
    args = []
    if service: q += " AND service=?"; args.append(service)
    q += " ORDER BY last_seen_at DESC LIMIT ?"; args.append(limit)
    rows = [row_to_incident(x) for x in con.execute(q, args).fetchall()]
    con.close()
    return {"count": len(rows), "incidents": [x.model_dump(mode="json") for x in rows]}

@app.get("/api/sources/electricity")
async def get_power_source():
    features = await fetch_power_outages()
    return {"source": "ArcGIS", "configured": bool(os.getenv("POWER_ARCGIS_LAYER_URL")), "features": normalize_source_features(features, "electricity", "arcgis-power")}

@app.get("/api/sources/water")
async def get_water_source():
    features = await fetch_water_outages()
    return {"source": "ArcGIS", "configured": bool(os.getenv("WATER_ARCGIS_LAYER_URL")), "features": normalize_source_features(features, "water", "arcgis-water")}

@app.get("/api/sources/mobile/cell")
async def get_mobile_cell(mcc: int, mnc: int, lac: int, cellid: int, radio: str | None = None):
    return await fetch_mobile_cells(mcc, mnc, lac, cellid, radio)

@app.get("/api/reports")
def get_reports(service: Service | None=None, status: Status | None=None):
    return get_all(service, status)

@app.post("/api/reports", response_model=Report)
async def create_report(payload: ReportIn):
    async with lock:
        now = datetime.now(timezone.utc)
        report = Report(id=str(uuid4()), created_at=now, **payload.model_dump())
        report.confidence = confidence_for(payload.service, payload.lat, payload.lng)
        con = db()
        con.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?)",
            (report.id, report.service, report.status, report.lat, report.lng, report.description,
             report.created_at.isoformat(), report.confirmations, report.confidence))
        con.commit(); con.close()
    await broadcast({"type": "report.created", "report": report.model_dump(mode="json")})
    return report

@app.post("/api/reports/{report_id}/confirm", response_model=Report)
async def confirm_report(report_id: str):
    async with lock:
        con = db(); row = con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not row: con.close(); raise HTTPException(404, "Report not found")
        confirmations = row["confirmations"] + 1
        confidence = min(99, max(row["confidence"], 50 + confirmations * 10))
        con.execute("UPDATE reports SET confirmations=?,confidence=? WHERE id=?", (confirmations, confidence, report_id))
        con.commit()
        report = row_to_report(con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()); con.close()
    await broadcast({"type": "report.updated", "report": report.model_dump(mode="json")})
    return report

@app.post("/api/reports/{report_id}/restore", response_model=Report)
async def restore_report(report_id: str):
    async with lock:
        con = db(); row = con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if not row: con.close(); raise HTTPException(404, "Report not found")
        con.execute("UPDATE reports SET status='restored' WHERE id=?", (report_id,))
        con.commit()
        report = row_to_report(con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()); con.close()
    await broadcast({"type": "report.updated", "report": report.model_dump(mode="json")})
    return report

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        await ws.send_json({
            "type": "snapshot",
            "reports": [r.model_dump(mode="json") for r in get_all()],
            "incidents": [i.model_dump(mode="json") for i in get_incidents()],
        })
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)
