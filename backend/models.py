from pydantic import BaseModel
from typing import Optional


class VerifyWhopRequest(BaseModel):
    email: str


class LoginRequest(BaseModel):
    email: str
    password: str


class SetPasswordRequest(BaseModel):
    email: str
    password: str


class ResetPasswordRequest(BaseModel):
    email: str
    new_password: str


class VerifySessionRequest(BaseModel):
    email: str
    session_token: str


class PlayerSearchRequest(BaseModel):
    query: str
    league_id: Optional[int] = None
    season: Optional[int] = None


class PredictionRequest(BaseModel):
    leagueId: int
    playerId: int
    playerName: str
    teamId: int
    teamName: str = ""
    opponentId: int
    opponentName: str
    venue: str = "home"
    propType: str = "pass_attempts"
    line: float = 0


class ComboRequest(BaseModel):
    leagueId: int
    player1Id: int
    player1Name: str
    player1TeamId: int
    player2Id: int
    player2Name: str
    player2TeamId: int
    opponentId: int
    opponentName: str
    venue: str = "home"
    propType: str = "pass_attempts"
    combinedLine: float = 0


class ScanPropRequest(BaseModel):
    image_base64: str


class ChatStartRequest(BaseModel):
    session_id: Optional[str] = None


class ChatMessageRequest(BaseModel):
    session_id: str
    message: str


class NaturalQueryRequest(BaseModel):
    query: str


class SettlePicksRequest(BaseModel):
    picks: list


class SavePickRequest(BaseModel):
    email: str
    token: str
    pick: dict


class GetPicksRequest(BaseModel):
    email: str
    token: str


class DeletePickRequest(BaseModel):
    email: str
    token: str
    pickId: str


class CorrectPickRequest(BaseModel):
    email: str
    token: str
    pickId: str
    actualValue: float


class LiveUpdateRequest(BaseModel):
    email: str
    token: str
