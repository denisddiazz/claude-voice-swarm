"""Carpeta propia del subjefe, alta automática de sesiones externas, session_id manual,
listado con origen, baja con /borrar_sesion y protección contra escrituras concurrentes."""
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import externas
import herramientas
import sesiones
from config import Config
from conftest import REPO
from empresas import Empresa, ErrorEmpresas, cargar_todas, desde_conf


def _sid():
    return str(uuid.uuid4())


def _transcripcion(directorio, sid, texto="{}", hace_s=None):
    t = externas.transcripcion(directorio, sid)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(texto + "\n", encoding="utf-8")
    if hace_s is not None:
        m = time.time() - hace_s
        os.utime(t, (m, m))
    return t


def _informar(buzon, cwd, sid, mensaje="hola", metodo="entorno", **kw):
    return herramientas.informar_externo(buzon, cargar_todas(buzon, Config.EMPRESAS_FILE), None,
                                         cwd, sid, metodo, mensaje, **kw)


@pytest.fixture
def proyecto(tmp_path):
    d = tmp_path / "Mi Proyecto"
    d.mkdir()
    return d.resolve()


@pytest.fixture(autouse=True)
def sin_empresas_json(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "EMPRESAS_FILE", tmp_path / "empresas.json")


# --- carpeta del subjefe ---
def test_carpeta_del_subjefe_recibe_su_claude_md_sin_sobrescribir():
    d = sesiones.preparar_carpeta_subjefe()
    assert d == Config.SUBJEFE_DIR
    texto = (d / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Solo delegas" in texto and "traducir-al-jefe" in texto and "traducir-a-sesion" in texto
    (d / "CLAUDE.md").write_text("ajustado por el usuario", encoding="utf-8")
    sesiones.preparar_carpeta_subjefe()
    assert (d / "CLAUDE.md").read_text(encoding="utf-8") == "ajustado por el usuario"


def test_el_papel_no_se_duplica_en_el_prompt():
    prompt = sesiones.prompt_subjefe({})
    assert "CLAUDE.md" in prompt
    assert "Solo delegas" not in prompt and "Coordinar el trabajo" not in prompt


def test_turno_del_subjefe_en_su_carpeta_y_solo_con_skill(buzon, claude_falso):
    args = sesiones.args_subjefe({})
    asyncio.run(sesiones.ejecutar_turno(buzon, "subjefe", "hola", Config.SUBJEFE_DIR, args, 30))
    (llamada,) = claude_falso.llamadas()
    argv = llamada["argv"]
    assert os.path.samefile(llamada["cwd"], Config.SUBJEFE_DIR)
    assert argv[argv.index("--tools") + 1] == "Skill"
    assert argv[argv.index("--setting-sources") + 1] == "project"
    permitidas = argv[argv.index("--allowedTools") + 1:]
    assert permitidas[:2] == ["mcp__buzon", "Skill"]
    assert llamada["env"]["CLAUDE_VOICE_SWARM_PUENTE"] == "1"


def test_migracion_del_subjefe_empieza_conversacion_nueva(buzon):
    sid_vieja, _ = buzon.sesion("subjefe")  # conversación de una versión anterior: sin carpeta anotada
    buzon.marcar_sesion_iniciada("subjefe")
    sesiones.migrar_subjefe(buzon)
    sid_nueva, iniciada = buzon.sesion("subjefe", str(Config.SUBJEFE_DIR))
    assert sid_nueva != sid_vieja and not iniciada
    sesiones.migrar_subjefe(buzon)  # ya con carpeta: no se vuelve a tocar
    assert buzon.sesion("subjefe", str(Config.SUBJEFE_DIR))[0] == sid_nueva


def test_cambiar_de_carpeta_empieza_conversacion_nueva(buzon, tmp_path):
    sid, _ = buzon.sesion("x", str(tmp_path / "a"))
    assert buzon.sesion("x", str(tmp_path / "a"))[0] == sid
    assert buzon.sesion("x", str(tmp_path / "b"))[0] != sid


# --- alta automática de sesiones externas ---
def test_primer_informe_da_de_alta_la_sesion(buzon, proyecto):
    sid = _sid()
    r = _informar(buzon, proyecto, sid, "empiezo la migración")
    assert "dada de alta como 'mi-proyecto'" in r
    e = cargar_todas(buzon)["mi-proyecto"]
    assert (e.origen, e.session_id, e.directorio) == ("externa", sid, proyecto)
    (fila,) = buzon.tomar_pendientes("subjefe")
    assert (fila["origen"], fila["texto"]) == ("mi-proyecto", "empiezo la migración")


def test_informes_siguientes_no_duplican_el_alta(buzon, proyecto):
    sid = _sid()
    _informar(buzon, proyecto, sid)
    r = _informar(buzon, proyecto, sid, "segundo")
    assert r == "Aviso entregado al subjefe."
    assert [f["nombre"] for f in buzon.sesiones_registradas()] == ["mi-proyecto"]
    assert [f["origen"] for f in buzon.tomar_pendientes("subjefe")] == ["mi-proyecto", "mi-proyecto"]


def test_otra_conversacion_en_la_misma_carpeta_pasa_a_ser_la_principal(buzon, proyecto):
    _informar(buzon, proyecto, _sid())
    sid2 = _sid()
    r = _informar(buzon, proyecto, sid2)
    assert "pasa a ser la sesión 'mi-proyecto'" in r
    assert cargar_todas(buzon)["mi-proyecto"].session_id == sid2


def test_nombre_explicito_y_colision(buzon, proyecto, tmp_path):
    r = _informar(buzon, proyecto, _sid(), nombre="web")
    assert "'web'" in r
    otra = tmp_path / "otra" / "web"
    otra.mkdir(parents=True)
    assert "ya existe" in _informar(buzon, otra.resolve(), _sid(), nombre="web")
    assert "'web-2'" in _informar(buzon, otra.resolve(), _sid())  # por defecto: nombre de carpeta + sufijo


def test_carpetas_protegidas_no_se_dan_de_alta(buzon):
    Config.SUBJEFE_DIR.mkdir(parents=True, exist_ok=True)
    r = _informar(buzon, Config.SUBJEFE_DIR, _sid())
    assert r.startswith("Aviso no entregado") and buzon.sesiones_registradas() == []


def test_sin_session_id_se_entrega_pero_no_se_da_de_alta(buzon, proyecto):
    r = _informar(buzon, proyecto, None, metodo="desconocido")
    assert "no queda dada de alta" in r and buzon.sesiones_registradas() == []
    assert len(buzon.tomar_pendientes("subjefe")) == 1


def test_conversacion_del_propio_puente_informa_con_su_nombre(buzon, proyecto):
    Config.EMPRESAS_FILE.write_text(json.dumps({"fija": {"directorio": str(proyecto)}}), encoding="utf-8")
    sid, _ = buzon.sesion("fija", str(proyecto))
    assert _informar(buzon, proyecto, sid) == "Aviso entregado al subjefe."
    assert buzon.sesiones_registradas() == []
    sid_subjefe, _ = buzon.sesion("subjefe")
    assert "no se informa a sí mismo" in _informar(buzon, proyecto, sid_subjefe)


# --- de dónde sale el session_id ---
def test_session_id_desde_el_entorno(proyecto):
    sid = _sid()
    assert externas.identificar_sesion(proyecto, {"CLAUDE_CODE_SESSION_ID": sid.upper()}) == (sid, "entorno")
    otra = _sid()
    _transcripcion(proyecto, otra, json.dumps({"mensaje": "mi informe"}))
    # La variable apunta a una conversación que no es de esta carpeta (heredada): manda la transcripción.
    assert externas.identificar_sesion(proyecto, {"CLAUDE_CODE_SESSION_ID": sid}, "mi informe") == \
        (otra, "transcripcion")
    _transcripcion(proyecto, sid, "{}", hace_s=120)
    assert externas.identificar_sesion(proyecto, {"CLAUDE_CODE_SESSION_ID": sid}, "mi informe") == (sid, "entorno")


def test_session_id_desde_la_transcripcion(proyecto):
    a, b = _sid(), _sid()
    _transcripcion(proyecto, a, json.dumps({"input": {"mensaje": "informe único ñ"}}, ensure_ascii=False), 60)
    _transcripcion(proyecto, b, "{}")  # más reciente, pero sin el mensaje
    assert externas.identificar_sesion(proyecto, {}, "informe único ñ") == (a, "transcripcion")
    assert externas.identificar_sesion(proyecto, {}, "otro texto") == (b, "reciente")
    assert externas.identificar_sesion(proyecto / "vacia", {}) == (None, "desconocido")


def test_mcp_externo_da_de_alta_por_cwd_y_entorno(tmp_path, proyecto):
    pytest.importorskip("mcp")
    sid = _sid()
    datos = tmp_path / "datos_mcp"
    env = dict(os.environ, ROL="externa", DATA_DIR=str(datos), EMPRESAS_FILE=str(tmp_path / "no.json"),
               SUBJEFE_DIR=str(tmp_path / "subjefe"), CLAUDE_CODE_SESSION_ID=sid, PYTHONUTF8="1",
               PYTHONPATH=str(REPO))
    env.pop("CLAUDE_VOICE_SWARM_PUENTE", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    codigo = ("import asyncio, mcp_server; "
              "print(asyncio.run(mcp_server.informar_al_subjefe('hola desde la app')))")
    out = subprocess.run([sys.executable, "-c", codigo], cwd=proyecto, env=env, capture_output=True,
                         text=True, encoding="utf-8", check=True).stdout
    assert "dada de alta como 'mi-proyecto'" in out
    from buzon import Buzon
    (fila,) = Buzon(datos / "buzon.sqlite3").sesiones_registradas()
    assert (fila["origen"], fila["session_id"], fila["directorio"]) == ("externa", sid, str(proyecto))

    env["CLAUDE_VOICE_SWARM_PUENTE"] = "1"  # sesión lanzada por el puente: el MCP de usuario no ofrece nada
    out = subprocess.run([sys.executable, "-c", "import mcp_server; print(hasattr(mcp_server, 'informar_al_subjefe'))"],
                         cwd=proyecto, env=env, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"


# --- session_id manual en empresas.json ---
def test_session_id_manual_en_empresas_json(tmp_path):
    sid = _sid()
    e = desde_conf("app", {"directorio": str(tmp_path), "session_id": sid.upper()})
    assert e.session_id == sid and e.origen == "empresas.json"
    with pytest.raises(ErrorEmpresas):
        desde_conf("app", {"directorio": str(tmp_path), "session_id": "no-es-un-uuid"})


def test_encargo_a_conversacion_fija_la_reanuda_y_no_la_recrea(buzon, claude_falso, proyecto):
    sid = _sid()
    claude_falso.configurar(modos=["ok", "sin_json"], existentes=[sid])
    e = Empresa("app", "d", proyecto, session_id=sid)
    r = asyncio.run(sesiones.ejecutar_turno(buzon, "app", "haz x", proyecto, sesiones.args_empresa(e), 30,
                                            sid_fijo=sid))
    assert r == "hecho"
    argv = claude_falso.llamadas()[0]["argv"]
    assert argv[argv.index("--resume") + 1] == sid and "--session-id" not in argv
    for _ in range(Config.SESION_MAX_FALLOS + 1):
        with pytest.raises(sesiones.ErrorSesion, match="no se recrea"):
            asyncio.run(sesiones.ejecutar_turno(buzon, "app", "haz y", proyecto, [], 30, sid_fijo=sid))
    assert buzon.info_sesion("app")["session_id"] == sid


# --- concurrencia con la app ---
def test_no_reanuda_si_la_conversacion_esta_en_uso_en_la_app(buzon, claude_falso, proyecto):
    sid = _sid()
    claude_falso.configurar(existentes=[sid])
    _transcripcion(proyecto, sid, hace_s=30)  # el usuario la tocó hace 30 s
    with pytest.raises(sesiones.ErrorSesion, match="actividad fuera del puente"):
        asyncio.run(sesiones.ejecutar_turno(buzon, "app", "haz x", proyecto, [], 30, sid_fijo=sid))
    assert claude_falso.llamadas() == []


def test_reanuda_si_la_ultima_escritura_fue_del_puente_o_es_antigua(buzon, claude_falso, proyecto):
    sid = _sid()
    claude_falso.configurar(existentes=[sid])
    t = _transcripcion(proyecto, sid, hace_s=Config.EXTERNA_MARGEN_MIN * 60 + 60)
    asyncio.run(sesiones.ejecutar_turno(buzon, "app", "uno", proyecto, [], 30, sid_fijo=sid))
    os.utime(t, None)  # escritura del turno del puente (el claude falso no escribe la transcripción)
    externas.anotar_mtime_puente(buzon, "app", proyecto, sid)
    asyncio.run(sesiones.ejecutar_turno(buzon, "app", "dos", proyecto, [], 30, sid_fijo=sid))
    assert len(claude_falso.llamadas()) == 2


# --- listado con origen ---
def test_listar_sesiones_muestra_el_origen(buzon, proyecto, tmp_path):
    fija = tmp_path / "fija"
    fija.mkdir()
    Config.EMPRESAS_FILE.write_text(json.dumps({"fija": {"directorio": str(fija)}}), encoding="utf-8")
    creada = tmp_path / "creada"
    herramientas.crear_sesion(buzon, cargar_todas(buzon), True, "creada", str(creada), "Creada")
    sid = _sid()
    _informar(buzon, proyecto, sid)
    texto = herramientas.listar_sesiones(cargar_todas(buzon), None)
    lineas = {l.split(":")[0]: l for l in texto.splitlines()}
    assert "origen empresas.json" in lineas["fija"]
    assert "origen creada" in lineas["creada"]
    assert "origen externa" in lineas["mi-proyecto"] and sid[:8] in lineas["mi-proyecto"]
    assert "(externa)" in sesiones.prompt_subjefe(cargar_todas(buzon))


# --- baja ---
@pytest.fixture
def main_mod(monkeypatch):
    for nombre in ("telegram", "telegram.ext", "comunicaciones", "voice_engine", "notificaciones"):
        monkeypatch.setitem(sys.modules, nombre, MagicMock())
    monkeypatch.delitem(sys.modules, "main", raising=False)
    import main

    monkeypatch.setattr(Config, "AUTHORIZED_CHAT_ID", 111)
    monkeypatch.setattr(main, "puente", main.Puente())
    yield main
    sys.modules.pop("main", None)


def _update():
    return SimpleNamespace(effective_chat=SimpleNamespace(id=111),
                           effective_message=SimpleNamespace(reply_text=AsyncMock()))


def test_borrar_sesion_da_de_baja_una_externa_sin_tocar_su_conversacion(main_mod, proyecto):
    buzon = main_mod.puente.buzon
    sid = _sid()
    t = _transcripcion(proyecto, sid, "conversación del usuario")
    _informar(buzon, proyecto, sid)
    main_mod.puente.recargar_empresas()
    buzon.fijar_sesion("mi-proyecto", sid)
    upd = _update()
    asyncio.run(main_mod.cmd_borrar_sesion(upd, SimpleNamespace(args=["mi-proyecto"])))
    assert "dada de baja" in upd.effective_message.reply_text.await_args.args[0]
    assert "mi-proyecto" not in main_mod.puente.empresas
    assert buzon.sesiones_registradas() == [] and buzon.info_sesion("mi-proyecto") is None
    assert t.read_text(encoding="utf-8").strip() == "conversación del usuario" and proyecto.is_dir()


def test_nueva_sesion_no_cambia_una_conversacion_fija(main_mod, proyecto):
    sid = _sid()
    _informar(main_mod.puente.buzon, proyecto, sid)
    main_mod.puente.recargar_empresas()
    upd = _update()
    asyncio.run(main_mod.cmd_nueva_sesion(upd, SimpleNamespace(args=["mi-proyecto"])))
    assert "conversación existente" in upd.effective_message.reply_text.await_args.args[0]
    assert main_mod.puente.empresas["mi-proyecto"].session_id == sid


def test_puente_crea_la_carpeta_del_subjefe(main_mod):
    assert (Config.SUBJEFE_DIR / "CLAUDE.md").is_file()
