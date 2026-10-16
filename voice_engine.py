"""Voz local: Kokoro-82M (TTS) y Faster-Whisper (STT).

Los modelos se cargan la primera vez que se necesitan y después se quedan en
memoria. Cada uno va protegido por un lock porque se llaman desde hilos.
"""
import io
import os
import subprocess
import tempfile
import threading

import numpy as np
import soundfile as sf

from config import Config

_tts_pipeline = None
_stt_model = None
_tts_lock = threading.Lock()
_stt_lock = threading.Lock()


def _get_tts():
    global _tts_pipeline
    if _tts_pipeline is None:
        from kokoro import KPipeline
        _tts_pipeline = KPipeline(lang_code="e")
    return _tts_pipeline


def _get_stt():
    global _stt_model
    if _stt_model is None:
        from faster_whisper import WhisperModel
        _stt_model = WhisperModel(Config.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _stt_model


def _sintetizar(texto: str) -> np.ndarray:
    with _tts_lock:
        pipeline = _get_tts()
        trozos = []
        for _, _, audio in pipeline(
            texto, voice=Config.KOKORO_VOICE, speed=Config.KOKORO_SPEED, split_pattern=r"\n+"
        ):
            if audio is None:
                continue
            trozos.append(audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio))
    if not trozos:
        raise RuntimeError("Kokoro no generó audio")
    return np.concatenate(trozos)


def texto_a_ogg_telegram(texto: str) -> io.BytesIO:
    """Texto -> nota de voz OGG/Opus en memoria, lista para Telegram (convierte con ffmpeg)."""
    audio = _sintetizar(texto)
    wav = io.BytesIO()
    sf.write(wav, audio, 24000, format="WAV")
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
         "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
        input=wav.getvalue(), capture_output=True, check=True,
    )
    return io.BytesIO(proc.stdout)


def transcribir_bytes(datos: bytes, sufijo: str = ".ogg") -> str:
    """Transcribe audio en español. Usa un archivo temporal único por llamada."""
    fd, ruta = tempfile.mkstemp(suffix=sufijo)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(datos)
        with _stt_lock:
            segments, _ = _get_stt().transcribe(ruta, language="es")
            return " ".join(seg.text for seg in segments).strip()
    finally:
        os.remove(ruta)
