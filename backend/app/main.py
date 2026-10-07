from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
import os, sqlite3, asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .sources import fetch_ioda_alerts, fetch_cloudflare_outages, source_health

Service = Literal["electricity", "water", "internet", "mobile"]
Status = Literal["down", "partial", "restored"]
DB_PATH = os.getenv("DB_PATH", "/tmp/livecity.db")
lock = asyncio.Lock()

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

clients: set[WebSocket] = set()
app = FastAPI(title="LiveCity API", version="0.2.0")

origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY, service TEXT NOT NULL, status TEXT NOT NULL,
        lat REAL NOT NULL, lng REAL NOT NULL, description TEXT NOT NULL,
        created_at TEXT NOT NULL, confirmations INTEGER NOT NULL DEFAULT 0,
        confidence INTEGER NOT NULL DEFAULT 50)""")
    con.commit()
    return con

def row_to_report(row):
    return Report(id=row["id"], service=row["service"], status=row["status"],
                  lat=row["lat"], lng=row["lng"], description=row["description"],
                  created_at=datetime.fromisoformat(row["created_at"]),
                  confirmations=row["confirmations"], confidence=row["confidence"])

def get_all(service=None, status=None):
    con=db()
    q="SELECT * FROM reports WHERE 1=1"; args=[]
    if service: q+=" AND service=?"; args.append(service)
    if status: q+=" AND status=?"; args.append(status)
    q+=" ORDER BY created_at DESC"
    rows=[row_to_report(x) for x in con.execute(q,args).fetchall()]
    con.close(); return rows

def confidence_for(service, lat, lng):
    nearby=[r for r in get_all(service=service) if r.status!="restored" and abs(r.lat-lat)<0.01 and abs(r.lng-lng)<0.01]
    return min(98, 50 + max(0, len(nearby)-1)*12)

async def broadcast(payload: dict):
    dead=[]
    for ws in list(clients):
        try: await ws.send_json(payload)
        except Exception: dead.append(ws)
    for ws in dead: clients.discard(ws)

@app.on_event("startup")
def startup(): db().close()

@app.get("/health")
def health(): return {"status":"ok","service":"livecity-api","reports":len(get_all())}

@app.get("/api/sources/health")
async def get_source_health():
    return {"sources": await source_health()}

@app.get("/api/sources/internet/ioda")
async def get_ioda_alerts(limit: int = 100):
    return {"source": "ioda", "alerts": await fetch_ioda_alerts(limit=max(1, min(limit, 500)))}

@app.get("/api/sources/internet/cloudflare")
async def get_cloudflare_outages(days: int = 1):
    return {"source": "cloudflare-radar", "configured": bool(os.getenv("RADAR_API_TOKEN")), "outages": await fetch_cloudflare_outages(days=max(1, min(days, 7)))}

@app.get("/api/reports")
def get_reports(service: Service | None=None, status: Status | None=None):
    return get_all(service,status)

@app.post("/api/reports", response_model=Report)
async def create_report(payload: ReportIn):
    async with lock:
        now=datetime.now(timezone.utc); report=Report(id=str(uuid4()),created_at=now,**payload.model_dump())
        report.confidence=confidence_for(payload.service,payload.lat,payload.lng)
        con=db(); con.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?)",
          (report.id,report.service,report.status,report.lat,report.lng,report.description,
           report.created_at.isoformat(),report.confirmations,report.confidence)); con.commit(); con.close()
    await broadcast({"type":"report.created","report":report.model_dump(mode="json")})
    return report

@app.post("/api/reports/{report_id}/confirm", response_model=Report)
async def confirm_report(report_id: str):
    async with lock:
        con=db(); row=con.execute("SELECT * FROM reports WHERE id=?",(report_id,)).fetchone()
        if not row: con.close(); raise HTTPException(404,"Report not found")
        confirmations=row["confirmations"]+1; confidence=min(99,max(row["confidence"],50+confirmations*10))
        con.execute("UPDATE reports SET confirmations=?,confidence=? WHERE id=?",(confirmations,confidence,report_id)); con.commit()
        report=row_to_report(con.execute("SELECT * FROM reports WHERE id=?",(report_id,)).fetchone()); con.close()
    await broadcast({"type":"report.updated","report":report.model_dump(mode="json")}); return report

@app.post("/api/reports/{report_id}/restore", response_model=Report)
async def restore_report(report_id: str):
    async with lock:
        con=db(); row=con.execute("SELECT * FROM reports WHERE id=?",(report_id,)).fetchone()
        if not row: con.close(); raise HTTPException(404,"Report not found")
        con.execute("UPDATE reports SET status='restored' WHERE id=?",(report_id,)); con.commit()
        report=row_to_report(con.execute("SELECT * FROM reports WHERE id=?",(report_id,)).fetchone()); con.close()
    await broadcast({"type":"report.updated","report":report.model_dump(mode="json")}); return report

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        await ws.send_json({"type":"snapshot","reports":[r.model_dump(mode="json") for r in get_all()]})
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)
