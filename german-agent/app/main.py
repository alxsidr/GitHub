from fastapi import FastAPI
from datetime import datetime

app = FastAPI(title="German A1 Learning Agents")


@app.get("/api/health")
def health_check():
    return {
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
        "agents_active": 0,
        "phase": "setup",
        "message": "Container is running. Ready for Phase 1 development."
    }
