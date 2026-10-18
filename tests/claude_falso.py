"""Sustituto de `claude` para las pruebas: no llama a ninguna API.

Uso: python claude_falso.py <control.json> [argumentos de claude...]

control.json: {"modos": ["ok", "sin_json", ...], "resultado": "...", "coste": 0.12}
Cada llamada consume un modo (el último se repite). Modos:
- ok: JSON de éxito con `resultado` y `coste`.
- sin_json: escribe en stderr y sale con código 1 (cuelgue, cierre inesperado).
- error: JSON con is_error=true (p. ej. sin sesión iniciada).
- error_max_budget: JSON con subtype error_max_budget_usd.
- dormir: espera 30 s (para probar timeouts).
- cerrar_tema: como ok, pero antes llama a herramientas.cerrar_tema con "cierre" del control
  ({"resumen", "pendientes", "nuevo_asunto"}) en el buzón de su --mcp-config.
Simula las sesiones: --session-id con un UUID ya usado o --resume de uno inexistente fallan sin JSON.
"existentes": lista de UUID que se dan por creados fuera (sesiones externas).
Registra en <control>.log una línea JSON por llamada (argv, env, stdin, cwd).
"""
import json
import os
import sys
import time
from pathlib import Path

control = Path(sys.argv[1])
args = sys.argv[2:]
conf = json.loads(control.read_text(encoding="utf-8"))
estado_f = control.with_suffix(".estado.json")
estado = json.loads(estado_f.read_text(encoding="utf-8")) if estado_f.exists() else {"n": 0, "sesiones": []}
modos = conf.get("modos", ["ok"])
modo = modos[min(estado["n"], len(modos) - 1)]
estado["n"] += 1

entrada = sys.stdin.read()
with open(str(control) + ".log", "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": args, "env": dict(os.environ), "stdin": entrada, "cwd": os.getcwd(),
                        "modo": modo}) + "\n")


def guardar():
    estado_f.write_text(json.dumps(estado), encoding="utf-8")


def valor(flag):
    return args[args.index(flag) + 1] if flag in args else None


sid_nueva, sid_resume = valor("--session-id"), valor("--resume")
if sid_nueva and sid_nueva in estado["sesiones"]:
    guardar()
    print(f"Error: Session ID {sid_nueva} is already in use.", file=sys.stderr)
    sys.exit(1)
if sid_resume and sid_resume not in estado["sesiones"] + conf.get("existentes", []):
    guardar()
    print(f"No conversation found with session ID: {sid_resume}", file=sys.stderr)
    sys.exit(1)

if modo == "dormir":
    guardar()
    time.sleep(30)
    sys.exit(0)
if modo == "sin_json":
    if sid_nueva:  # la sesión llega a crearse en disco aunque no haya respuesta
        estado["sesiones"].append(sid_nueva)
    guardar()
    print("fallo simulado", file=sys.stderr)
    sys.exit(1)
if modo == "error":
    guardar()
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                      "result": "Not logged in", "total_cost_usd": 0}))
    sys.exit(1)

if modo == "cerrar_tema":
    # Como si el subjefe llamara a la herramienta MCP cerrar_tema: mismo buzón que su --mcp-config.
    mcp_env = json.loads(Path(valor("--mcp-config")).read_text(encoding="utf-8"))["mcpServers"]["buzon"]["env"]
    os.environ.update(mcp_env)
    os.environ["ROL"] = "externa"  # que config no cargue el .env real; el buzón sale de DATA_DIR
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import herramientas  # noqa: E402
    from buzon import Buzon  # noqa: E402
    cierre = conf.get("cierre", {})
    herramientas.cerrar_tema(Buzon(), cierre.get("resumen", "resumen del tema"), cierre.get("pendientes", ""),
                             cierre.get("nuevo_asunto", False))

if sid_nueva:
    estado["sesiones"].append(sid_nueva)
guardar()
if modo == "error_max_budget":
    print(json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True,
                      "total_cost_usd": conf.get("coste", 0.0)}))
    sys.exit(1)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": conf.get("resultado", "hecho"), "total_cost_usd": conf.get("coste", 0.0),
                  "session_id": sid_nueva or sid_resume}))
