"""A4: las sesiones no reciben secretos del .env (lista blanca de variables)."""
import asyncio

import sesiones
from config import entorno_sesion
from empresas import Empresa


def test_lista_blanca(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_TOKEN=x\nOTRO_SECRETO=y\nSUBJEFE_MODEL=haiku\n", encoding="utf-8")
    origen = {
        "PATH": "/usr/bin", "HOME": "/home/u", "LANG": "es_ES.UTF-8", "LC_ALL": "C.UTF-8",
        "TELEGRAM_TOKEN": "123:abc", "NTFY_TOPIC": "t", "NTFY_TOKEN": "tk",
        "OTRO_SECRETO": "y", "AWS_SECRET_ACCESS_KEY": "z", "SSH_AUTH_SOCK": "/tmp/s",
    }
    env = entorno_sesion(origen, extra=[], env_file=env_file)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/u", "LANG": "es_ES.UTF-8", "LC_ALL": "C.UTF-8"}


def test_extra_no_puede_reabrir_secretos(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OTRO_SECRETO=y\n", encoding="utf-8")
    origen = {"SSH_AUTH_SOCK": "/tmp/s", "TELEGRAM_TOKEN": "1", "OTRO_SECRETO": "y", "NTFY_TOKEN": "t"}
    env = entorno_sesion(origen, extra=["SSH_AUTH_SOCK", "TELEGRAM_TOKEN", "OTRO_SECRETO", "NTFY_TOKEN"],
                         env_file=env_file)
    assert env == {"SSH_AUTH_SOCK": "/tmp/s"}


def test_proceso_claude_no_hereda_secretos(tmp_path, buzon, claude_falso, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:secreto")
    monkeypatch.setenv("NTFY_TOPIC", "tema-secreto")
    monkeypatch.setenv("NTFY_TOKEN", "tk")
    e = Empresa("x", "d", tmp_path / "trabajo", max_budget_usd=1)
    asyncio.run(sesiones.ejecutar_turno(buzon, "x", "hola", e.directorio, sesiones.args_empresa(e), 30))
    (llamada,) = claude_falso.llamadas()
    claves = {k.upper() for k in llamada["env"]}
    assert not {"TELEGRAM_TOKEN", "NTFY_TOPIC", "NTFY_TOKEN"} & claves
    assert "PATH" in claves


def test_mcp_de_empresa_no_lleva_secretos(tmp_path):
    import json
    e = Empresa("x", "d", tmp_path / "trabajo", max_budget_usd=1)
    args = sesiones.args_empresa(e)
    conf = json.loads(open(args[args.index("--mcp-config") + 1], encoding="utf-8").read())
    env = conf["mcpServers"]["buzon"]["env"]
    assert env["ROL"] == "empresa"
    assert not any(k.startswith(("TELEGRAM", "NTFY")) for k in env)
