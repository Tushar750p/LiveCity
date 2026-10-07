from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

Service = Literal["electricity", "water", "internet", "mobile"]
Status = Literal["down", "partial", "restored"]

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

reports: list[Report] = []
clients: set[WebSocket] = set()

app = FastAPI(title="LiveCity API", version="0.1.0")

origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def confidence_for(service: str, lat: float, lng: float) -> int:
    # MVP heuristic: multiple recent reports within ~1km increase confidence.
    nearby = [r for r in reports if r.service == service and r.status != "restored"
              and abs(r.lat-lat) < 0.01 and abs(r.lng-lng) < 0.01]
    return min(98, 50 + max(0, len(nearby)-1) * 12)

async def broadcast(payload: dict):
    dead=[]
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)

@app.get("/health")
def health():
    return {"status":"ok","service":"livecity-api","reports":len(reports)}

@app.get("/api/reports")
def get_reports(service: Service | None = None, status: Status | None = None):
    rows = reports
    if service:
        rows = [r for r in rows if r.service == service]
    if status:
        rows = [r for r in rows if r.status == status]
    return rows

@app.post("/api/reports", response_model=Report)
async def create_report(payload: ReportIn):
    now = datetime.now(timezone.utc)
    report = Report(id=str(uuid4()), created_at=now, **payload.model_dump())
    report.confidence = confidence_for(payload.service, payload.lat, payload.lng)
    reports.append(report)
    await broadcast({"type":"report.created","report":report.model_dump(mode="json")})
    return report

@app.post("/api/reports/{report_id}/confirm", response_model=Report)
async def confirm_report(report_id: str):
    report = next((r for r in reports if r.id == report_id), None)
    if not report:
        raise HTTPException(404, "Report not found")
    report.confirmations += 1
    report.confidence = min(99, max(report.confidence, 50 + report.confirmations * 10))
    await broadcast({"type":"report.updated","report":report.model_dump(mode="json")})
    return report

@app.post("/api/reports/{report_id}/restore", response_model=Report)
async def restore_report(report_id: str):
    report = next((r for r in reports if r.id == report_id), None)
    if not report:
        raise HTTPException(404, "Report not found")
    report.status = "restored"
    await broadcast({"type":"report.updated","report":report.model_dump(mode="json")})
    return report

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json({"type":"snapshot","reports":[r.model_dump(mode="json") for r in reports]})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)
