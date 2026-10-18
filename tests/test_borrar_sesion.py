"""/borrar_sesion: quita del registro una sesión creada con crear_sesion; nunca borra su carpeta."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import Config


@pytest.fixture
def main_mod(monkeypatch, tmp_path):
    # main.py importa telegram, audio y red en el arranque; el venv de pruebas no los tiene.
    # Se sustituyen solo para cargar el módulo y se quitan al terminar.
    for nombre in ("telegram", "telegram.ext", "comunicaciones", "voice_engine", "notificaciones"):
        monkeypatch.setitem(sys.modules, nombre, MagicMock())
    monkeypatch.delitem(sys.modules, "main", raising=False)
    import main

    monkeypatch.setattr(Config, "AUTHORIZED_CHAT_ID", 111)
    monkeypatch.setattr(Config, "EMPRESAS_FILE", tmp_path / "empresas.json")  # no existe: sin sesiones fijas
    monkeypatch.setattr(main, "puente", main.Puente())
    yield main
    sys.modules.pop("main", None)


def _update(chat_id=111):
    mensaje = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), effective_message=mensaje)


def _ctx(*args):
    return SimpleNamespace(args=list(args))


def _respuesta(update) -> str:
    return update.effective_message.reply_text.await_args.args[0]


def _crear_registrada(main, tmp_path, nombre="nueva"):
    carpeta = tmp_path / "trabajos" / nombre
    carpeta.mkdir(parents=True, exist_ok=True)
    main.puente.buzon.registrar_sesion(nombre, str(carpeta), "Pruebas", None)
    main.puente.recargar_empresas()
    return carpeta


def test_borra_sesion_creada_pero_no_su_carpeta(main_mod, tmp_path):
    carpeta = _crear_registrada(main_mod, tmp_path)
    upd = _update()
    asyncio.run(main_mod.cmd_borrar_sesion(upd, _ctx("nueva")))
    assert "borrada" in _respuesta(upd) and str(carpeta) in _respuesta(upd)
    assert "nueva" not in main_mod.puente.empresas
    assert main_mod.puente.buzon.sesiones_registradas() == []
    assert carpeta.is_dir()


def test_recrear_con_el_mismo_nombre_empieza_de_cero(main_mod, tmp_path):
    _crear_registrada(main_mod, tmp_path)
    buzon = main_mod.puente.buzon
    sid_antigua, _ = buzon.sesion("nueva")  # conversación de la sesión borrada
    asyncio.run(main_mod.cmd_borrar_sesion(_update(), _ctx("nueva")))
    _crear_registrada(main_mod, tmp_path)
    sid_nueva, iniciada = buzon.sesion("nueva")
    assert sid_nueva != sid_antigua and not iniciada


def test_nombre_inexistente_responde_error(main_mod, tmp_path):
    _crear_registrada(main_mod, tmp_path)
    upd = _update()
    asyncio.run(main_mod.cmd_borrar_sesion(upd, _ctx("fantasma")))
    assert _respuesta(upd).startswith("No existe la sesión 'fantasma'")
    assert [r["nombre"] for r in main_mod.puente.buzon.sesiones_registradas()] == ["nueva"]


def test_sin_nombre_muestra_uso(main_mod):
    upd = _update()
    asyncio.run(main_mod.cmd_borrar_sesion(upd, _ctx()))
    assert _respuesta(upd).startswith("Uso: /borrar_sesion")


def test_sesion_de_empresas_json_no_se_borra_desde_aqui(main_mod, tmp_path):
    carpeta = tmp_path / "fija"
    carpeta.mkdir()
    Config.EMPRESAS_FILE.write_text(json.dumps({"fija": {"directorio": str(carpeta), "descripcion": "Fija"}}),
                                    encoding="utf-8")
    main_mod.puente.recargar_empresas()
    upd = _update()
    asyncio.run(main_mod.cmd_borrar_sesion(upd, _ctx("fija")))
    assert "empresas.json" in _respuesta(upd)
    assert "fija" in main_mod.puente.empresas and carpeta.is_dir()


def test_chat_no_autorizado_no_borra_nada(main_mod, tmp_path):
    _crear_registrada(main_mod, tmp_path)
    upd = _update(chat_id=999)
    asyncio.run(main_mod.cmd_borrar_sesion(upd, _ctx("nueva")))
    upd.effective_message.reply_text.assert_not_awaited()
    assert [r["nombre"] for r in main_mod.puente.buzon.sesiones_registradas()] == ["nueva"]
    assert "nueva" in main_mod.puente.empresas
