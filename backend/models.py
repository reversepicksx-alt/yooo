from pydantic import BaseModel
from typing import Optional


class VerifyAccessRequest(BaseModel):
    email: str
    pin: Optional[str] = None  # None = native (no pin field sent); "" = web with empty pin


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


class PlayerRoleResolveRequest(BaseModel):
    playerId: Optional[int] = None
    playerName: str
    teamName: Optional[str] = ""
    genericPosition: Optional[str] = ""
    stats: Optional[dict] = None


class PredictionRequest(BaseModel):
    email: str
    token: str
    leagueId: int = 0
    playerId: int = 0
    playerName: str
    teamId: int = 0
    teamName: str = ""
    opponentId: int = 0
    opponentName: str = ""
    venue: str = "home"
    propType: str = "pass_attempts"
    line: float = 0
    positionOverride: str = ""
    roleOverride: str = ""
    sport: str = "soccer"
    odds: Optional[dict] = None


class Cs2PredictRequest(BaseModel):
    email: str
    token: str
    playerNickname: str
    playerId: Optional[int] = None
    teamName: str = ""
    teamId: Optional[int] = None
    propType: str = ""
    line: float = 0
    opponentName: str = ""
    opponentId: Optional[int] = None
    opponentRank: Optional[int] = None
    matchId: Optional[int] = None
    matchDate: str = ""
    maps: Optional[int] = None
    mapName: Optional[str] = None
    tournament: Optional[str] = None
    playerTeamRank: Optional[int] = None
    playerTeamStartsCt: Optional[bool] = None

class WtaPredictRequest(BaseModel):
    email: str
    token: str
    playerName: str
    playerId: int = 0
    opponentName: str = ""
    opponentId: Optional[int] = None
    propType: str = ""
    line: float = 0
    surface: str = ""
    round: str = ""
    tournament: str = ""
    subjectRank: Optional[int] = None
    opponentRank: Optional[int] = None

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
    sport: str = "soccer"


class ChatStartRequest(BaseModel):
    session_id: Optional[str] = None


class ChatMessageRequest(BaseModel):
    session_id: str
    message: str


class TacticalMessageRequest(BaseModel):
    session_id: str
    message: str = ""
    image_base64: Optional[str] = None


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


class AdminSettingsRequest(BaseModel):
    email: str
    token: str
    key: str
    value: str


class AdminTestKeyRequest(BaseModel):
    email: str
    token: str
    api_key: str


class AppleAuthRequest(BaseModel):
    identity_token: str
    email: Optional[str] = None
    full_name: Optional[str] = None
