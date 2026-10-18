"""Hook UserPromptSubmit de SUBJEFE_DIR (lo registra el puente en .claude/settings.json).

Uso: python hook_subjefe_pc.py <DATA_DIR> <EMPRESAS_FILE> <SUBJEFE_DIR>  (JSON del hook por stdin)

En los turnos headless del puente (CLAUDE_VOICE_SWARM_PUENTE=1) no hace nada. En la sesión de la
app marca el canal PC y pone al día al subjefe (ver contexto_pc.py). Nunca bloquea al jefe por un
fallo propio: ante cualquier error sale con 0 y sin salida.
"""
import json
import os
import sys


def main():
    if os.environ.get("CLAUDE_VOICE_SWARM_PUENTE") == "1":
        return  # turno del subjefe headless: el canal no cambia
    try:
        if len(sys.argv) >= 4:
            os.environ["DATA_DIR"], os.environ["EMPRESAS_FILE"], os.environ["SUBJEFE_DIR"] = sys.argv[1:4]
        os.environ["ROL"] = "hook_pc"  # config no carga .env: este proceso no necesita secretos
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        entrada = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        import contexto_pc
        from buzon import Buzon

        salida = contexto_pc.al_escribir(Buzon(), entrada)
        if salida:
            sys.stdout.write(json.dumps(salida, ensure_ascii=True))
    except Exception as e:
        sys.stderr.write(f"hook del canal PC: {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
    sys.exit(0)
