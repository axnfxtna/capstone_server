from pydantic import BaseModel
from typing import Optional


class ChatbotResponse(BaseModel):
    reply_text: str
    intent: str                  # "chat" | "info" | "navigate" | "farewell"
    destination: Optional[str] = None
    confidence: float = 0.5


class GrammarRequest(BaseModel):
    raw_text: str
    session_id: str


class GrammarResponse(BaseModel):
    corrected_text: str


class TTSRequest(BaseModel):
    text: str
    session_id: str


class TTSResponse(BaseModel):
    phoneme_text: str
