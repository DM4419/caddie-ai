"""Detect Voice / conversational-AI roles — companies and products in speech,
voice agents, conversational AI, TTS/ASR, IVR/contact-centre AI. Keyword-based
(title + description) so it runs at import and backfills cheaply. Returns the
flag "voice_ai" when matched (kept separate from the founder-fit flags).
"""
from __future__ import annotations

import re

VOICE_RE = re.compile(
    # voice / speech
    r"\bvoice[\s-]?(ai|agent|assistant|bot|interface|tech|first|cloning|synthesis|product|platform|model)\b|"
    r"\b(speech|voice)[\s-]?(recognition|synthesis|analytics)\b|"
    r"\btext[\s-]?to[\s-]?speech\b|\bspeech[\s-]?to[\s-]?text\b|"
    r"\b(tts|asr|stt)\b|\bivr\b|\bvoicebot\b|\bspeech\s+ai\b|\baudio\s+ai\b|"
    r"\bspoken[\s-]language\b|\bvoice\s+technolog|"
    # conversational / chat
    r"\bconversational\s+(ai|platform|agent|interface|experienc|product|design|intelligence)\b|"
    r"\bconversation\s+(design|intelligence)\b|\bchat\s?bots?\b|\bchat\s+(agent|assistant)\b|"
    r"\bvirtual\s+(agent|assistant)\b|\bdialog(ue)?\s+(system|management|model)\b|"
    r"\bcontact[\s-]?cent(?:er|re)\s+ai\b|"
    # natural language
    r"\bnatural[\s-]language\s+(processing|understanding|generation)\b|\b(nlp|nlu|nlg)\b", re.I)


def detect(title: str = "", description: str = "") -> bool:
    return bool(VOICE_RE.search(f"{title or ''}\n{description or ''}"))
