"""
api/routes/grammar.py
======================
POST /grammar — Thai grammar correction endpoint.
Wraps mcp_grammar (GrammarCorrector).
"""

from fastapi import APIRouter, Request
from api.schemas.chatbot import GrammarRequest, GrammarResponse

router = APIRouter()


@router.post("/grammar", response_model=GrammarResponse)
async def grammar_correct(payload: GrammarRequest, request: Request) -> GrammarResponse:
    """Correct raw STT Thai text using LLM grammar correction."""
    app_state = request.app.state
    corrected = app_state.grammar_corrector.correct(payload.raw_text)
    return GrammarResponse(corrected_text=corrected)
