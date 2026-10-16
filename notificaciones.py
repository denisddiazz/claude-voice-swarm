"""Alertas críticas al móvil vía ntfy (https://ntfy.sh)."""
import logging

import httpx

from config import Config

logger = logging.getLogger(__name__)


async def enviar_alerta_ntfy(mensaje: str, titulo: str = "Alerta crítica", prioridad: int = 5) -> bool:
    """Publica en el tema de ntfy. Prioridad 5 = máxima (sonido largo, se salta No molestar en Android)."""
    if not Config.NTFY_TOPIC:
        logger.warning("NTFY_TOPIC no configurado: no se envía la alerta")
        return False
    headers = {"Authorization": f"Bearer {Config.NTFY_TOKEN}"} if Config.NTFY_TOKEN else {}
    # Publicación en JSON: evita problemas con tildes en cabeceras HTTP.
    payload = {
        "topic": Config.NTFY_TOPIC,
        "title": titulo,
        "message": mensaje[:4000],
        "priority": prioridad,
        "tags": ["rotating_light"] if prioridad >= 5 else ["robot"],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(Config.NTFY_SERVER, json=payload, headers=headers)
            r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error(f"Fallo al enviar a ntfy: {e}")
        return False
