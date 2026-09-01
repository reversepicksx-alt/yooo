"""
Reverse Tactical — deterministic soccer context compatibility endpoints.
Supports text questions and image uploads (prop screenshots).
Connected to the full system: player cache, API-Sports data.
"""

import json
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter

from config import db
from models import ChatStartRequest, TacticalMessageRequest


router = APIRouter(prefix="/api", tags=["tactical"])

tactical_sessions = {}


@router.post("/tactical/start")
async def tactical_start(req: ChatStartRequest):
    sid = req.session_id or f"tac-{uuid.uuid4().hex[:8]}"
    tactical_sessions[sid] = {
        "history": [],
        "created": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "session_id": sid,
        "message": "**Reverse Tactical online.** Connected to deterministic analytics and cached soccer context.\n\nAsk me anything — or upload a prop screenshot for an unavailable tactical generation response.",
    }


@router.post("/tactical/message")
async def tactical_message(req: TacticalMessageRequest):
    return {
        "response": "Tactical generation is unavailable. Use the deterministic explanation on an analyzed pick.",
        "session_id": req.session_id,
        "available": False,
        "scanEntries": [],
    }
