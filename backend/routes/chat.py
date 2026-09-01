"""Compatibility endpoints for the retired natural-language chat features."""
import uuid
from fastapi import APIRouter, HTTPException

from config import chat_sessions
from models import ChatStartRequest, ChatMessageRequest, NaturalQueryRequest

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat/start")
async def chat_start(req: ChatStartRequest):
    sid = req.session_id or str(uuid.uuid4())
    chat_sessions[sid] = {"session_id": sid}
    return {
        "session_id": sid,
        "available": False,
        "message": "Tactical chat generation is unavailable. Use the model explanation attached to an analyzed pick.",
    }


@router.post("/chat/message")
async def chat_message(req: ChatMessageRequest):
    return {
        "response": "Tactical chat generation is unavailable. Analyze a pick to receive a deterministic model explanation.",
        "session_id": req.session_id,
        "available": False,
    }


@router.post("/parse-query")
async def parse_natural_query(_req: NaturalQueryRequest):
    raise HTTPException(
        status_code=503,
        detail="Natural-language query parsing is unavailable. Enter the player, prop, and line manually.",
    )