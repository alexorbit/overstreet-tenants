"""Transcrição de áudio.

Prioridade:
  1. Groq API (whisper-large-v3-turbo) — instantâneo, grátis até 7200 req/dia
     Configure GROQ_API_KEY em .env (console.groq.com)
  2. Local faster-whisper (tiny, CPU) — fallback sem API key, mais lento
"""
import asyncio
import io
import logging

from aiogram import Bot
from aiogram.types import Message

from overstreet.config import GROQ_API_KEY, GROQ_BASE_URL, GROQ_WHISPER_MODEL

log = logging.getLogger("overstreet.ai.whisper")

# ── Groq (preferred) ──────────────────────────────────────────────────────

_groq_client = None


def _get_groq():
    global _groq_client
    if _groq_client is None:
        from openai import OpenAI
        _groq_client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)
    return _groq_client


def _transcribe_groq_sync(audio_bytes: bytes, filename: str) -> str:
    client = _get_groq()
    buf = io.BytesIO(audio_bytes)
    buf.name = filename
    result = client.audio.transcriptions.create(
        model=GROQ_WHISPER_MODEL,
        file=buf,
        language="pt",
    )
    return result.text.strip()


# ── Local faster-whisper (fallback) ───────────────────────────────────────

_local_model = None


def _get_local_whisper():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel
        log.info("Carregando Whisper local (tiny)...")
        _local_model = WhisperModel(
            "tiny", device="cpu", compute_type="int8",
            download_root="whisper_cache",
        )
        log.info("Whisper local pronto")
    return _local_model


def _transcribe_local_sync(audio_bytes: bytes, suffix: str) -> str:
    import os, tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        model = _get_local_whisper()
        segments, _ = model.transcribe(tmp_path, language="pt", beam_size=1)
        return " ".join(s.text for s in segments).strip()
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ── Public API ────────────────────────────────────────────────────────────

async def transcribe_audio(bot: Bot, message: Message) -> str | None:
    """Transcreve voz ou arquivo de áudio. Retorna texto ou None."""
    try:
        if message.voice:
            file_id = message.voice.file_id
            suffix = ".ogg"
            filename = "audio.ogg"
        elif message.audio:
            file_id = message.audio.file_id
            suffix = ".mp3"
            filename = "audio.mp3"
        else:
            return None

        # Download audio into memory (no disk write)
        file_info = await bot.get_file(file_id)
        buf = io.BytesIO()
        await bot.download_file(file_info.file_path, destination=buf)
        audio_bytes = buf.getvalue()

        if not audio_bytes:
            log.warning("Audio vazio recebido")
            return None

        # 1. Try Groq (fast)
        if GROQ_API_KEY:
            try:
                text = await asyncio.to_thread(_transcribe_groq_sync, audio_bytes, filename)
                log.info("Groq transcricao OK: %d chars", len(text))
                return text or None
            except Exception as e:
                log.warning("Groq falhou (%s), tentando local...", e)

        # 2. Fallback: local faster-whisper
        log.info("Usando whisper local (Groq key nao configurada)...")
        text = await asyncio.to_thread(_transcribe_local_sync, audio_bytes, suffix)
        return text or None

    except Exception as e:
        log.error("Erro transcricao: %s", e)
        return None
