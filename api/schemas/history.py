from pydantic import BaseModel
from typing import Optional


class HistoryPayload(BaseModel):
    session_id: str
    student_id: Optional[str] = None
    user_text: str
    bot_reply: str
    intent: str
    timestamp: str


class SummaryRequest(BaseModel):
    session_id: str
    student_id: str
    query: str


class SummaryResponse(BaseModel):
    summary: str
