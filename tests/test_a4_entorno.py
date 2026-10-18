"""A4: el token de Telegram, ntfy y lo demás del .env no pasan al entorno de las sesiones."""
import asyncio
import json

import sesiones
from config import entorno_sesion
from empresas import Empresa


def test_quita_secretos_y_conserva_el_resto(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_TOKEN=x\nOTRO_SECRETO=y\nSUBJEFE_MODEL=haiku\n", encoding="utf-8")
    origen = {
        "PATH": "/usr/bin", "HOME": "/home/u", "GITHUB_TOKEN": "de-la-sesion", "SSH_AUTH_SOCK": "/tmp/s",
        "TELEGRAM_TOKEN": "123:abc", "telegram_chat": "1", "NTFY_TOPIC": "t", "NTFY_TOKEN": "tk",
        "OTRO_SECRETO": "y", "SUBJEFE_MODEL": "haiku",
    }
    env = entorno_sesion(origen, env_file=env_file)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/u", "GITHUB_TOKEN": "de-la-sesion",
                   "SSH_AUTH_SOCK": "/tmp/s"}


def test_proceso_claude_no_hereda_secretos(tmp_path, buzon, claude_falso, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:secreto")
    monkeypatch.setenv("NTFY_TOPIC", "tema-secreto")
    monkeypatch.setenv("NTFY_TOKEN", "tk")
    e = Empresa("x", "d", tmp_path / "trabajo")
    asyncio.run(sesiones.ejecutar_turno(buzon, "x", "hola", e.directorio, sesiones.args_empresa(e), 30))
    (llamada,) = claude_falso.llamadas()
    claves = {k.upper() for k in llamada["env"]}
    assert not {"TELEGRAM_TOKEN", "NTFY_TOPIC", "NTFY_TOKEN"} & claves
    assert "PATH" in claves


def test_mcp_de_empresa_no_lleva_secretos(tmp_path):
    e = Empresa("x", "d", tmp_path / "trabajo")
    args = sesiones.args_empresa(e)
    conf = json.loads(open(args[args.index("--mcp-config") + 1], encoding="utf-8").read())
    env = conf["mcpServers"]["buzon"]["env"]
    assert env["ROL"] == "empresa"
    assert not any(k.startswith(("TELEGRAM", "NTFY")) for k in env)
