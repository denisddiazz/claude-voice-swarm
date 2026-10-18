"""Hook PreToolUse del relé del canal PC (lo pasa rele_pc.py con --settings).

El relé corre en bypassPermissions para que la sesión de la app, que también corre así, no
retenga sus mensajes. Este hook es la barrera: solo deja pasar un SendMessage al destinatario
de RELE_PC_DESTINO con el texto exacto de RELE_PC_ESPERADO. Cualquier otra cosa, o un fallo
del propio hook, se deniega (exit 2).
"""
import json
import os
import sys


def _normalizar(texto) -> str:
    lineas = str(texto or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(x.rstrip() for x in lineas).strip()


def _denegar(motivo: str):
    sys.stderr.write(f"Denegado por el puente: {motivo}\n")
    sys.exit(2)


def decidir(entrada: dict, destino: str, esperado: str) -> str | None:
    """None si la llamada se permite; si no, el motivo."""
    if entrada.get("tool_name") != "SendMessage":
        return None if entrada.get("tool_name") == "ListAgents" else "herramienta no permitida"
    datos = entrada.get("tool_input") or {}
    to = str(datos.get("to") or "")
    if not destino or (to != destino and not (to.startswith(destino + " [") and to.endswith("]"))):
        return f"solo se puede enviar a {destino!r}"
    if _normalizar(datos.get("message")) != _normalizar(esperado):
        return "el mensaje tiene que ser el texto exacto, sin cambios"
    if datos.get("notify_when_idle"):
        return "sin notify_when_idle"
    return None


def main():
    try:
        entrada = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        with open(os.environ["RELE_PC_ESPERADO"], encoding="utf-8") as f:
            esperado = f.read()
        motivo = decidir(entrada, os.environ.get("RELE_PC_DESTINO", ""), esperado)
    except Exception as e:  # el hook falla cerrado
        motivo = f"error en la guarda del relé ({type(e).__name__})"
    if motivo:
        _denegar(motivo)
    sys.exit(0)


if __name__ == "__main__":
    main()
