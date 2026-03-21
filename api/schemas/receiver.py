from pydantic import BaseModel
from typing import Optional, List


class STTResult(BaseModel):
    text: str
    confidence: float
    language: str
    duration: float


class DetectionPayload(BaseModel):
    """Combined vision + STT payload sent from PI 5 on every detection event."""
    timestamp: str
    person_id: str
    is_registered: bool
    track_id: Optional[int] = None
    bbox: Optional[List[float]] = None
    stt: STTResult


class GreetingPayload(BaseModel):
    """Greeting-only payload from PI 5 — no STT, just face detection."""
    timestamp: str
    person_id: str
    is_registered: bool
    vision_confidence: float


class ActivateResponse(BaseModel):
    active: int                # 0 or 1
