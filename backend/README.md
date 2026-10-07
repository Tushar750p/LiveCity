# LiveCity Backend

FastAPI service for outage reports.

## Run
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

Set DATABASE_URL for PostgreSQL/PostGIS. For a quick demo without PostgreSQL, the app falls back to an in-memory store.
