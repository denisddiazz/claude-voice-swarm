"""Buzón compartido (SQLite) por el que pasan todos los mensajes.

Lo usan dos procesos a la vez: el puente (main.py) y los servidores MCP que
lanza cada sesión de Claude Code. Como está en disco, un reinicio no pierde
mensajes pendientes.

Participantes: "jefe" (tú), "subjefe" y el nombre de cada empresa.

El origen de cada mensaje lo fija quien lo escribe en código (el puente o el
servidor MCP según su rol), nunca el texto. Los mensajes del jefe los firma el
puente con una clave que solo existe en su memoria: una fila con origen 'jefe'
sin firma válida no se trata como orden del jefe.
"""
import hashlib
import hmac
import sqlite3
import time
import uuid
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

from config import Config

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL,
    destino TEXT NOT NULL,
    texto TEXT NOT NULL,
    urgente INTEGER NOT NULL DEFAULT 0,
    modo_voz INTEGER,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    creado REAL NOT NULL,
    saltos INTEGER NOT NULL DEFAULT 0,
    firma TEXT
);
CREATE INDEX IF NOT EXISTS idx_mensajes_destino ON mensajes (destino, estado);
CREATE TABLE IF NOT EXISTS sesiones (
    nombre TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    iniciada INTEGER NOT NULL DEFAULT 0,
    fallos INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gastos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    coste_usd REAL NOT NULL,
    creado REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gastos_creado ON gastos (creado);
CREATE TABLE IF NOT EXISTS sesiones_registradas (
    nombre TEXT PRIMARY KEY,
    directorio TEXT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT '',
    modelo TEXT,
    creado REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL,
    enviada INTEGER NOT NULL,
    creado REAL NOT NULL
);
"""

# Columnas añadidas después de la primera versión (bases de datos ya creadas).
_MIGRACIONES = [
    ("mensajes", "saltos", "INTEGER NOT NULL DEFAULT 0"),
    ("mensajes", "firma", "TEXT"),
    ("sesiones", "fallos", "INTEGER NOT NULL DEFAULT 0"),
    ("sesiones", "directorio", "TEXT"),        # carpeta con la que se creó la conversación
    ("sesiones", "mtime_puente", "REAL"),      # mtime de la transcripción tras el último turno del puente
    ("sesiones_registradas", "origen", "TEXT NOT NULL DEFAULT 'creada'"),  # 'creada' o 'externa'
    ("sesiones_registradas", "session_id", "TEXT"),  # conversación existente (sesiones externas)
]


def _firma(clave: bytes, origen: str, destino: str, texto: str, creado: float) -> str:
    datos = "".join([origen, destino, repr(creado), texto]).encode("utf-8")
    return hmac.new(clave, datos, hashlib.sha256).hexdigest()


def inicio_del_dia(ahora: float | None = None) -> float:
    d = datetime.fromtimestamp(time.time() if ahora is None else ahora)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class Buzon:
    def __init__(self, ruta: Path = None, clave: bytes | None = None):
        """`clave`: solo la pasa el puente; con ella firma lo que escribe y verifica al jefe."""
        self.ruta = Path(ruta or Config.BUZON_DB)
        self.clave = clave
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_ESQUEMA)
            for tabla, columna, tipo in _MIGRACIONES:
                existentes = {f[1] for f in c.execute(f"PRAGMA table_info({tabla})")}
                if columna not in existentes:
                    c.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")

    @contextmanager
    def _con(self):
        con = sqlite3.connect(self.ruta, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    # --- mensajes ---
    def enviar(self, origen: str, destino: str, texto: str, urgente: bool = False,
               modo_voz: bool | None = None, saltos: int = 0) -> int:
        creado = time.time()
        firma = _firma(self.clave, origen, destino, texto, creado) if self.clave else None
        with self._con() as c:
            cur = c.execute(
                "INSERT INTO mensajes (origen, destino, texto, urgente, modo_voz, creado, saltos, firma)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (origen, destino, texto, int(urgente), None if modo_voz is None else int(modo_voz),
                 creado, int(saltos), firma),
            )
            return cur.lastrowid

    def firmado(self, fila) -> bool:
        """True si la fila la escribió este puente (firma válida con su clave)."""
        if not self.clave or not fila["firma"]:
            return False
        esperada = _firma(self.clave, fila["origen"], fila["destino"], fila["texto"], fila["creado"])
        return hmac.compare_digest(esperada, fila["firma"])

    def es_del_jefe(self, fila) -> bool:
        """True solo si la fila dice venir del jefe y la firmó este puente."""
        return fila["origen"] == "jefe" and self.firmado(fila)

    def tomar_pendientes(self, destino: str) -> list[sqlite3.Row]:
        """Marca como 'en_curso' y devuelve todos los pendientes de un destino (atómico)."""
        with self._con() as c:
            c.execute("BEGIN IMMEDIATE")
            filas = c.execute(
                "SELECT * FROM mensajes WHERE destino = ? AND estado = 'pendiente' ORDER BY id", (destino,)
            ).fetchall()
            if filas:
                c.executemany("UPDATE mensajes SET estado = 'en_curso' WHERE id = ?", [(f["id"],) for f in filas])
            c.execute("COMMIT")
            return filas

    def marcar_hechos(self, ids: list[int]):
        with self._con() as c:
            c.executemany("UPDATE mensajes SET estado = 'hecho' WHERE id = ?", [(i,) for i in ids])

    def reiniciar_en_curso(self) -> int:
        """Al arrancar: lo que quedó a medias por un reinicio vuelve a la cola."""
        with self._con() as c:
            return c.execute("UPDATE mensajes SET estado = 'pendiente' WHERE estado = 'en_curso'").rowcount

    def destinos_pendientes(self) -> list[str]:
        with self._con() as c:
            return [f[0] for f in c.execute(
                "SELECT DISTINCT destino FROM mensajes WHERE estado = 'pendiente' AND destino != 'jefe'"
            )]

    def resumen_estado(self) -> dict[str, dict[str, int]]:
        with self._con() as c:
            filas = c.execute(
                "SELECT destino, estado, COUNT(*) FROM mensajes WHERE estado != 'hecho' GROUP BY destino, estado"
            ).fetchall()
        res: dict[str, dict[str, int]] = {}
        for destino, estado, n in filas:
            res.setdefault(destino, {})[estado] = n
        return res

    # --- sesiones de Claude Code ---
    def sesion(self, nombre: str, directorio: str | None = None) -> tuple[str, bool]:
        """Conversación propia del puente para `nombre`. Si se pasa `directorio` y la conversación
        se creó en otra carpeta, se empieza una nueva: Claude Code no la encontraría desde aquí."""
        with self._con() as c:
            fila = c.execute("SELECT session_id, iniciada, directorio FROM sesiones WHERE nombre = ?",
                             (nombre,)).fetchone()
            if fila and directorio and fila["directorio"] and fila["directorio"] != directorio:
                c.execute("DELETE FROM sesiones WHERE nombre = ?", (nombre,))
                fila = None
            if fila:
                if directorio and not fila["directorio"]:
                    c.execute("UPDATE sesiones SET directorio = ? WHERE nombre = ?", (directorio, nombre))
                return fila["session_id"], bool(fila["iniciada"])
            sid = str(uuid.uuid4())
            c.execute("INSERT INTO sesiones (nombre, session_id, directorio) VALUES (?, ?, ?)",
                      (nombre, sid, directorio))
            return sid, False

    def info_sesion(self, nombre: str):
        with self._con() as c:
            return c.execute("SELECT * FROM sesiones WHERE nombre = ?", (nombre,)).fetchone()

    def fijar_sesion(self, nombre: str, session_id: str):
        """Apunta `nombre` a una conversación que ya existe (externa o session_id de empresas.json)."""
        with self._con() as c:
            fila = c.execute("SELECT session_id FROM sesiones WHERE nombre = ?", (nombre,)).fetchone()
            if fila and fila["session_id"] == session_id:
                return
            c.execute("DELETE FROM sesiones WHERE nombre = ?", (nombre,))
            c.execute("INSERT INTO sesiones (nombre, session_id, iniciada) VALUES (?, ?, 1)", (nombre, session_id))

    def nombre_por_session_id(self, session_id: str) -> str | None:
        with self._con() as c:
            fila = c.execute("SELECT nombre FROM sesiones WHERE session_id = ?", (session_id,)).fetchone()
            return fila["nombre"] if fila else None

    def registrar_mtime_puente(self, nombre: str, mtime: float):
        with self._con() as c:
            c.execute("UPDATE sesiones SET mtime_puente = ? WHERE nombre = ?", (float(mtime), nombre))

    def marcar_sesion_iniciada(self, nombre: str):
        with self._con() as c:
            c.execute("UPDATE sesiones SET iniciada = 1 WHERE nombre = ?", (nombre,))

    def nueva_sesion(self, nombre: str):
        """Olvida la sesión actual; el próximo turno empezará una conversación nueva."""
        with self._con() as c:
            c.execute("DELETE FROM sesiones WHERE nombre = ?", (nombre,))

    def registrar_fallo_sesion(self, nombre: str) -> int:
        """Suma un fallo seguido y devuelve cuántos lleva."""
        with self._con() as c:
            c.execute("UPDATE sesiones SET fallos = fallos + 1 WHERE nombre = ?", (nombre,))
            fila = c.execute("SELECT fallos FROM sesiones WHERE nombre = ?", (nombre,)).fetchone()
            return fila["fallos"] if fila else 0

    def reiniciar_fallos_sesion(self, nombre: str):
        with self._con() as c:
            c.execute("UPDATE sesiones SET fallos = 0 WHERE nombre = ?", (nombre,))

    # --- sesiones creadas con crear_sesion o dadas de alta al informar (persisten entre reinicios) ---
    def registrar_sesion(self, nombre: str, directorio: str, descripcion: str, modelo: str | None,
                         origen: str = "creada", session_id: str | None = None):
        with self._con() as c:
            c.execute("INSERT INTO sesiones_registradas (nombre, directorio, descripcion, modelo, creado,"
                      " origen, session_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (nombre, directorio, descripcion, modelo, time.time(), origen, session_id))

    def externa_por_directorio(self, directorio: str):
        with self._con() as c:
            return c.execute("SELECT * FROM sesiones_registradas WHERE origen = 'externa' AND directorio = ?"
                             " ORDER BY creado LIMIT 1", (directorio,)).fetchone()

    def actualizar_session_id_registrada(self, nombre: str, session_id: str):
        with self._con() as c:
            c.execute("UPDATE sesiones_registradas SET session_id = ? WHERE nombre = ?", (session_id, nombre))

    def sesiones_registradas(self) -> list[sqlite3.Row]:
        with self._con() as c:
            return c.execute("SELECT * FROM sesiones_registradas ORDER BY creado").fetchall()

    def borrar_sesion_registrada(self, nombre: str) -> bool:
        """Quita la sesión del registro. No toca su carpeta. True si existía."""
        with self._con() as c:
            return c.execute("DELETE FROM sesiones_registradas WHERE nombre = ?", (nombre,)).rowcount > 0

    # --- gasto ---
    def registrar_gasto(self, nombre: str, coste_usd: float):
        with self._con() as c:
            c.execute("INSERT INTO gastos (nombre, coste_usd, creado) VALUES (?, ?, ?)",
                      (nombre, float(coste_usd), time.time()))

    def gasto_hoy(self, ahora: float | None = None) -> float:
        with self._con() as c:
            fila = c.execute("SELECT COALESCE(SUM(coste_usd), 0) FROM gastos WHERE creado >= ?",
                             (inicio_del_dia(ahora),)).fetchone()
            return float(fila[0])

    def tope_alcanzado(self, tope_usd: float) -> bool:
        """Tope diario global; tope_usd <= 0 significa sin tope."""
        return tope_usd > 0 and self.gasto_hoy() >= tope_usd

    # --- alertas al móvil ---
    def puede_alertar(self, intervalo_s: float) -> bool:
        with self._con() as c:
            fila = c.execute("SELECT MAX(creado) FROM alertas WHERE enviada = 1").fetchone()
        return fila[0] is None or time.time() - fila[0] >= intervalo_s

    def registrar_alerta(self, origen: str, enviada: bool):
        with self._con() as c:
            c.execute("INSERT INTO alertas (origen, enviada, creado) VALUES (?, ?, ?)",
                      (origen, int(enviada), time.time()))
