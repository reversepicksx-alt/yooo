import uuid
import asyncio as aio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from config import db
from models import PredictionRequest, ComboRequest
from routes.predict import predict

router = APIRouter(prefix="/api", tags=["combo"])

@router.post("/predict-combo")
async def predict_combo(req: ComboRequest):
    """Start combo prediction — returns job_id immediately, frontend polls for result."""
    job_id = uuid.uuid4().hex[:12]
    
    # Store job status
    await db.combo_jobs.insert_one({
        "jobId": job_id,
        "status": "running",
        "request": req.model_dump(),
        "created": datetime.now(timezone.utc).isoformat(),
    })

    # Launch background task
    async def run_combo():
        try:
            p1_venue = req.venue
            p2_venue = req.venue
            p1_opponent_id = req.opponentId
            p1_opponent_name = req.opponentName
            p2_opponent_id = req.opponentId
            p2_opponent_name = req.opponentName

            if req.player2TeamId == req.opponentId:
                p2_venue = "away" if req.venue == "home" else "home"
                p2_opponent_id = req.player1TeamId
                p2_opponent_name = req.player1Name.split()[0] + "'s Team"

            pred_req1 = PredictionRequest(
                leagueId=req.leagueId, playerId=req.player1Id, playerName=req.player1Name,
                teamId=req.player1TeamId, opponentId=p1_opponent_id, opponentName=p1_opponent_name,
                venue=p1_venue, propType=req.propType, line=req.combinedLine / 2,
            )
            pred_req2 = PredictionRequest(
                leagueId=req.leagueId, playerId=req.player2Id, playerName=req.player2Name,
                teamId=req.player2TeamId, opponentId=p2_opponent_id, opponentName=p2_opponent_name,
                venue=p2_venue, propType=req.propType, line=req.combinedLine / 2,
            )

            result1, result2 = await aio.gather(predict(pred_req1), predict(pred_req2))

            combined_value = round((result1.get("projectedValue", 0) + result2.get("projectedValue", 0)) * 10) / 10
            avg_confidence = round((result1.get("confidenceScore", 50) + result2.get("confidenceScore", 50)) / 2)

            await db.combo_jobs.update_one({"jobId": job_id}, {"$set": {
                "status": "done",
                "result": {
                    "player1": result1,
                    "player2": result2,
                    "combined": {
                        "projectedValue": combined_value,
                        "line": req.combinedLine,
                        "recommendation": "over" if combined_value > req.combinedLine else "under" if combined_value < req.combinedLine else "push",
                        "confidenceScore": avg_confidence,
                        "confidenceLevel": "High" if avg_confidence >= 75 else "Medium" if avg_confidence >= 55 else "Low",
                    }
                }
            }})
        except Exception as e:
            await db.combo_jobs.update_one({"jobId": job_id}, {"$set": {
                "status": "error",
                "error": str(e),
            }})

    aio.create_task(run_combo())
    return {"jobId": job_id, "status": "running"}


@router.get("/predict-combo/{job_id}")
async def get_combo_result(job_id: str):
    """Poll for combo prediction result."""
    job = await db.combo_jobs.find_one({"jobId": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "done":
        return {"status": "done", "result": job["result"]}
    elif job["status"] == "error":
        raise HTTPException(status_code=500, detail=job.get("error", "Combo prediction failed"))
    return {"status": "running"}
