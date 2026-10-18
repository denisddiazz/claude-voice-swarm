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
CREATE TABLE IF NOT EXISTS temas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    abierto REAL NOT NULL,
    ultimo_jefe REAL NOT NULL,            -- último mensaje del jefe (cierre por inactividad)
    encargo TEXT NOT NULL DEFAULT '',     -- primer mensaje del jefe del tema, abreviado
    cerrado REAL,                         -- NULL = tema abierto (solo uno a la vez)
    motivo TEXT,
    resumen TEXT,
    pendientes TEXT,
    nuevo_asunto INTEGER NOT NULL DEFAULT 0,  -- el subjefe cerró porque el jefe cambió de asunto
    cierre_pedido TEXT                    -- motivo de un cierre forzado en curso o pendiente
);
CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origen TEXT NOT NULL,
    enviada INTEGER NOT NULL,
    creado REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS canal (            -- por dónde se entrega al jefe (una sola fila)
    id INTEGER PRIMARY KEY CHECK (id = 1),
    activo TEXT NOT NULL DEFAULT 'movil',     -- 'movil' (Telegram) o 'pc' (sesión de la app en SUBJEFE_DIR)
    motivo TEXT NOT NULL DEFAULT 'inicio',
    cambiado REAL NOT NULL,
    ultimo_movil REAL NOT NULL,               -- último mensaje o comando del jefe por Telegram
    ultimo_pc REAL NOT NULL DEFAULT 0,        -- último mensaje del jefe por el PC
    actividad_pc REAL NOT NULL DEFAULT 0,     -- última actividad conocida del jefe en el PC
    al_dia_de TEXT,                           -- canal anterior con entregas que el jefe no ha visto
    al_dia_desde REAL                         -- desde cuándo (lo anterior se da por visto)
);
CREATE TABLE IF NOT EXISTS salidas (          -- lo entregado al jefe, por canal
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canal TEXT NOT NULL,
    texto TEXT NOT NULL,
    creado REAL NOT NULL,
    recopilacion INTEGER NOT NULL DEFAULT 0,  -- entrega de lo no visto: no se vuelve a recopilar
    coste_usd REAL                            -- coste del relé (solo PC)
);
CREATE INDEX IF NOT EXISTS idx_salidas_canal ON salidas (canal, creado);
"""

CANALES = ("movil", "pc")

# Columnas añadidas después de la primera versión (bases de datos ya creadas).
_MIGRACIONES = [
    ("mensajes", "saltos", "INTEGER NOT NULL DEFAULT 0"),
    ("mensajes", "firma", "TEXT"),
    ("sesiones", "fallos", "INTEGER NOT NULL DEFAULT 0"),
    ("sesiones", "directorio", "TEXT"),        # carpeta con la que se creó la conversación
    ("sesiones", "mtime_puente", "REAL"),      # mtime de la transcripción tras el último turno del puente
    ("sesiones_registradas", "origen", "TEXT NOT NULL DEFAULT 'creada'"),  # 'creada' o 'externa'
    ("sesiones_registradas", "session_id", "TEXT"),  # conversación existente (sesiones externas)
    ("mensajes", "canal", "TEXT"),             # por dónde llegó un mensaje del jefe ('movil' o 'pc')
    ("alertas", "mensaje", "TEXT"),            # texto de la alerta (se copia al PC si es el canal activo)
    ("alertas", "titulo", "TEXT"),
    ("mensajes", "via", "TEXT"),    # 'pc': lo vio el subjefe del PC (relé o hook), no el headless
    ("sesiones", "fork_de", "TEXT"),           # conversación del PC que el próximo turno continúa con --fork-session
    ("canal", "pc_session_id", "TEXT"),        # conversación de la sesión del subjefe en la app (la da el hook)
    ("canal", "pc_transcript", "TEXT"),
    ("canal", "pc_visto", "REAL"),             # última vez que el hook puso al día al subjefe del PC
    ("canal", "pc_msg_id", "INTEGER NOT NULL DEFAULT 0"),     # mensajes al subjefe ya contados al PC
    ("canal", "pc_salida_id", "INTEGER NOT NULL DEFAULT 0"),  # salidas por Telegram ya contadas al PC
    ("canal", "saltos_pc", "INTEGER NOT NULL DEFAULT 0"),     # encargos encadenados sin el jefe, en el PC
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
               modo_voz: bool | None = None, saltos: int = 0, canal: str | None = None) -> int:
        creado = time.time()
        firma = _firma(self.clave, origen, destino, texto, creado) if self.clave else None
        with self._con() as c:
            cur = c.execute(
                "INSERT INTO mensajes (origen, destino, texto, urgente, modo_voz, creado, saltos, firma, canal)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (origen, destino, texto, int(urgente), None if modo_voz is None else int(modo_voz),
                 creado, int(saltos), firma, canal),
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

    # --- temas del subjefe (cada tema, una conversación; ver temas.py) ---
    def tema_abierto(self):
        with self._con() as c:
            return c.execute("SELECT * FROM temas WHERE cerrado IS NULL ORDER BY id DESC LIMIT 1").fetchone()

    def tema(self, tema_id: int):
        with self._con() as c:
            return c.execute("SELECT * FROM temas WHERE id = ?", (tema_id,)).fetchone()

    def hay_temas(self) -> bool:
        with self._con() as c:
            return c.execute("SELECT 1 FROM temas LIMIT 1").fetchone() is not None

    def abrir_tema(self, encargo: str = "", ahora: float | None = None) -> int:
        ahora = time.time() if ahora is None else ahora
        with self._con() as c:
            return c.execute("INSERT INTO temas (abierto, ultimo_jefe, encargo) VALUES (?, ?, ?)",
                             (ahora, ahora, encargo)).lastrowid

    def anotar_jefe_tema(self, tema_id: int, creado: float, texto: str):
        """Último mensaje del jefe del tema; el primero queda como encargo."""
        with self._con() as c:
            c.execute("UPDATE temas SET ultimo_jefe = MAX(ultimo_jefe, ?),"
                      " encargo = CASE WHEN encargo = '' THEN ? ELSE encargo END WHERE id = ?",
                      (float(creado), texto, tema_id))

    def pedir_cierre_tema(self, motivo: str) -> bool:
        """Marca el tema abierto para cerrarlo con `motivo` antes del próximo turno. True si había tema."""
        with self._con() as c:
            return c.execute("UPDATE temas SET cierre_pedido = ? WHERE cerrado IS NULL",
                             (motivo,)).rowcount > 0

    def cerrar_tema(self, resumen: str, pendientes: str, motivo: str, nuevo_asunto: bool = False) -> int | None:
        """Cierra el tema abierto. Si el puente pidió el cierre, el motivo es el suyo. Devuelve su id."""
        with self._con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = c.execute("SELECT id FROM temas WHERE cerrado IS NULL ORDER BY id DESC LIMIT 1").fetchone()
            if fila is None:
                c.execute("COMMIT")
                return None
            c.execute("UPDATE temas SET cerrado = ?, resumen = ?, pendientes = ?, nuevo_asunto = ?,"
                      " motivo = COALESCE(cierre_pedido, ?) WHERE id = ?",
                      (time.time(), resumen, pendientes, int(nuevo_asunto), motivo, fila["id"]))
            c.execute("COMMIT")
            return fila["id"]

    def temas_cerrados(self, limite: int = 8) -> list[sqlite3.Row]:
        """Del más reciente al más antiguo."""
        with self._con() as c:
            return c.execute("SELECT * FROM temas WHERE cerrado IS NOT NULL ORDER BY cerrado DESC, id DESC"
                             " LIMIT ?", (limite,)).fetchall()

    def encargados_entre(self, desde: float, hasta: float) -> list[str]:
        """Sesiones a las que el subjefe encargó algo en ese intervalo."""
        with self._con() as c:
            return [f[0] for f in c.execute(
                "SELECT destino FROM mensajes WHERE origen = 'subjefe' AND creado BETWEEN ? AND ?"
                " GROUP BY destino ORDER BY MIN(id)", (desde, hasta))]

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

    def registrar_alerta(self, origen: str, enviada: bool, mensaje: str | None = None, titulo: str | None = None):
        with self._con() as c:
            c.execute("INSERT INTO alertas (origen, enviada, creado, mensaje, titulo) VALUES (?, ?, ?, ?, ?)",
                      (origen, int(enviada), time.time(), mensaje, titulo))

    def alertas_enviadas_desde(self, desde: float) -> list[sqlite3.Row]:
        with self._con() as c:
            return c.execute("SELECT * FROM alertas WHERE enviada = 1 AND creado >= ? ORDER BY id",
                             (float(desde),)).fetchall()

    # --- canal del jefe: móvil (Telegram) o PC (ver README, "Canal PC") ---
    def _fila_canal(self, c, ahora: float):
        # Al crearse, lo enviado antes por Telegram se da por visto.
        c.execute("INSERT OR IGNORE INTO canal (id, cambiado, ultimo_movil) VALUES (1, ?, ?)", (ahora, ahora))
        return c.execute("SELECT * FROM canal WHERE id = 1").fetchone()

    def estado_canal(self, ahora: float | None = None) -> sqlite3.Row:
        with self._con() as c:
            return self._fila_canal(c, time.time() if ahora is None else ahora)

    def anotar_jefe(self, canal: str, ahora: float | None = None):
        """El jefe acaba de escribir por `canal`: lo enviado ahí hasta ahora lo ha visto."""
        if canal not in CANALES:
            raise ValueError(f"canal desconocido: {canal!r}")
        ahora = time.time() if ahora is None else ahora
        with self._con() as c:
            self._fila_canal(c, ahora)
            if canal == "movil":
                c.execute("UPDATE canal SET ultimo_movil = MAX(ultimo_movil, ?) WHERE id = 1", (ahora,))
            else:
                c.execute("UPDATE canal SET ultimo_pc = MAX(ultimo_pc, ?), actividad_pc = MAX(actividad_pc, ?)"
                          " WHERE id = 1", (ahora, ahora))

    def anotar_actividad_pc(self, cuando: float):
        """Actividad del jefe en el PC (teclado o ratón): lo entregado al PC antes, lo ha podido ver."""
        with self._con() as c:
            self._fila_canal(c, time.time())
            c.execute("UPDATE canal SET actividad_pc = MAX(actividad_pc, ?) WHERE id = 1", (float(cuando),))

    def cambiar_canal(self, canal: str, motivo: str, ahora: float | None = None) -> bool:
        """Pone `canal` como activo. True si ha cambiado.

        Del PC al móvil deja dos cosas pendientes: entregar por Telegram lo que el subjefe dijo en el
        PC desde la última actividad del jefe allí (de su transcripción), y que el próximo turno del
        subjefe headless continúe la conversación del PC con --fork-session. Del móvil al PC no deja
        nada: lo pone al día el hook UserPromptSubmit de la sesión del PC."""
        if canal not in CANALES:
            raise ValueError(f"canal desconocido: {canal!r}")
        ahora = time.time() if ahora is None else ahora
        with self._con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = self._fila_canal(c, ahora)
            if fila["activo"] == canal:
                c.execute("COMMIT")
                return False
            de_pc = fila["activo"] == "pc"
            # Al pasar al PC cuenta como actividad: el plazo de inactividad empieza ahora.
            c.execute("UPDATE canal SET activo = ?, motivo = ?, cambiado = ?, al_dia_de = ?, al_dia_desde = ?,"
                      " actividad_pc = CASE WHEN ? = 'pc' THEN MAX(actividad_pc, ?) ELSE actividad_pc END"
                      " WHERE id = 1", (canal, motivo, ahora, "pc" if de_pc else None,
                                        fila["actividad_pc"] if de_pc else None, canal, ahora))
            if de_pc and fila["pc_session_id"]:
                if not c.execute("UPDATE sesiones SET fork_de = ? WHERE nombre = 'subjefe'",
                                 (fila["pc_session_id"],)).rowcount:
                    c.execute("INSERT INTO sesiones (nombre, session_id, fork_de) VALUES ('subjefe', ?, ?)",
                              (str(uuid.uuid4()), fila["pc_session_id"]))
            c.execute("COMMIT")
            return True

    def tomar_no_visto(self) -> tuple[float, str | None] | None:
        """Si al pasar del PC al móvil quedó pendiente contar lo que el jefe no vio en el PC, devuelve
        (desde, transcripción de la sesión del PC) y lo da por entregado."""
        with self._con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = self._fila_canal(c, time.time())
            if fila["al_dia_de"] != "pc":
                c.execute("COMMIT")
                return None
            c.execute("UPDATE canal SET al_dia_de = NULL, al_dia_desde = NULL WHERE id = 1")
            c.execute("COMMIT")
        return fila["al_dia_desde"] or 0.0, fila["pc_transcript"]

    # --- subjefe en el PC (sesión de la app en SUBJEFE_DIR) ---
    def registrar_pc(self, session_id: str | None, transcript: str | None) -> bool:
        """Anota la conversación de la sesión del PC. True si es otra que la anterior."""
        with self._con() as c:
            fila = self._fila_canal(c, time.time())
            if not session_id:
                return False
            c.execute("UPDATE canal SET pc_session_id = ?, pc_transcript = ? WHERE id = 1",
                      (session_id, transcript))
            return fila["pc_session_id"] != session_id

    def fijar_saltos_pc(self, saltos: int, maximo: bool = False):
        """saltos_pc = saltos (el jefe ha escrito en el PC: 0) o el mayor de los dos (informes)."""
        with self._con() as c:
            self._fila_canal(c, time.time())
            c.execute("UPDATE canal SET saltos_pc = " + ("MAX(saltos_pc, ?)" if maximo else "?") + " WHERE id = 1",
                      (int(saltos),))

    def marcar_via_pc(self, ids: list[int]):
        with self._con() as c:
            c.executemany("UPDATE mensajes SET via = 'pc' WHERE id = ?", [(i,) for i in ids])

    def tomar_para_pc(self) -> list[sqlite3.Row]:
        """Pendientes del subjefe que se le dan al del PC (hook): quedan hechos y vistos en el PC."""
        with self._con() as c:
            c.execute("BEGIN IMMEDIATE")
            filas = c.execute("SELECT * FROM mensajes WHERE destino = 'subjefe' AND estado = 'pendiente'"
                              " ORDER BY id").fetchall()
            c.executemany("UPDATE mensajes SET estado = 'hecho', via = 'pc' WHERE id = ?", [(f["id"],) for f in filas])
            c.execute("COMMIT")
            return filas

    def novedades_movil(self, limite: int = 200) -> tuple[list[sqlite3.Row], list[sqlite3.Row], int, int]:
        """Lo que pasó por el subjefe headless desde la última puesta al día del PC: mensajes que trató
        (hechos, no vistos en el PC) y lo que dijo por Telegram. Devuelve también hasta qué id de cada
        tabla se ha contado (lo que está en curso se deja para la próxima)."""
        with self._con() as c:
            fila = self._fila_canal(c, time.time())
            en_curso = c.execute("SELECT MIN(id) FROM mensajes WHERE destino = 'subjefe' AND estado != 'hecho'"
                                 " AND id > ?", (fila["pc_msg_id"],)).fetchone()[0]
            tope_msg = (en_curso - 1) if en_curso else (c.execute("SELECT MAX(id) FROM mensajes").fetchone()[0] or 0)
            mensajes = c.execute("SELECT * FROM mensajes WHERE destino = 'subjefe' AND estado = 'hecho'"
                                 " AND via IS NULL AND id > ? AND id <= ? ORDER BY id DESC LIMIT ?",
                                 (fila["pc_msg_id"], tope_msg, limite)).fetchall()[::-1]
            tope_salida = c.execute("SELECT MAX(id) FROM salidas").fetchone()[0] or 0
            salidas = c.execute("SELECT * FROM salidas WHERE canal = 'movil' AND recopilacion = 0 AND id > ?"
                                " ORDER BY id DESC LIMIT ?", (fila["pc_salida_id"], limite)).fetchall()[::-1]
        return mensajes, salidas, max(tope_msg, fila["pc_msg_id"]), tope_salida

    def marcar_puesto_al_dia(self, msg_id: int, salida_id: int, ahora: float | None = None):
        with self._con() as c:
            c.execute("UPDATE canal SET pc_msg_id = MAX(pc_msg_id, ?), pc_salida_id = MAX(pc_salida_id, ?),"
                      " pc_visto = ? WHERE id = 1", (int(msg_id), int(salida_id), time.time() if ahora is None else ahora))

    def temas_cerrados_desde(self, desde: float | None, limite: int = 5) -> list[sqlite3.Row]:
        with self._con() as c:
            return c.execute("SELECT * FROM temas WHERE cerrado IS NOT NULL AND cerrado > ? ORDER BY cerrado DESC"
                             " LIMIT ?", (float(desde or 0), limite)).fetchall()[::-1]

    def anotar_jefe_tema_abierto(self, cuando: float, encargo: str = ""):
        """El jefe ha escrito en el PC: el tema abierto no se cierra por inactividad. Si no hay
        ninguno, abre uno: la conversación del PC forma parte de él, y así el primer turno headless
        la continúa (con --fork-session) en vez de empezar un tema y una conversación nuevos."""
        with self._con() as c:
            if not c.execute("UPDATE temas SET ultimo_jefe = MAX(ultimo_jefe, ?) WHERE cerrado IS NULL",
                             (float(cuando),)).rowcount:
                c.execute("INSERT INTO temas (abierto, ultimo_jefe, encargo) VALUES (?, ?, ?)",
                          (float(cuando), float(cuando), encargo[:300]))

    # --- continuidad PC -> móvil: el subjefe headless continúa la conversación del PC ---
    def tomar_fork(self, nombre: str) -> str | None:
        """Conversación del PC pendiente de continuar con --fork-session (y la da por tomada)."""
        with self._con() as c:
            fila = c.execute("SELECT fork_de FROM sesiones WHERE nombre = ?", (nombre,)).fetchone()
            if fila is None or not fila["fork_de"]:
                return None
            c.execute("UPDATE sesiones SET fork_de = NULL WHERE nombre = ?", (nombre,))
            return fila["fork_de"]

    def adoptar_sesion(self, nombre: str, session_id: str, directorio: str):
        """La conversación de `nombre` pasa a ser `session_id` (la bifurcada del PC), ya iniciada."""
        with self._con() as c:
            c.execute("DELETE FROM sesiones WHERE nombre = ?", (nombre,))
            c.execute("INSERT INTO sesiones (nombre, session_id, iniciada, directorio) VALUES (?, ?, 1, ?)",
                      (nombre, session_id, directorio))

    def hay_no_visto(self) -> bool:
        with self._con() as c:
            return bool(self._fila_canal(c, time.time())["al_dia_de"])

    def registrar_salida(self, canal: str, texto: str, recopilacion: bool = False,
                         coste_usd: float | None = None, ahora: float | None = None) -> int:
        with self._con() as c:
            return c.execute("INSERT INTO salidas (canal, texto, creado, recopilacion, coste_usd)"
                             " VALUES (?, ?, ?, ?, ?)",
                             (canal, texto, time.time() if ahora is None else ahora, int(recopilacion),
                              coste_usd)).lastrowid

    def salidas(self, canal: str | None = None) -> list[sqlite3.Row]:
        with self._con() as c:
            if canal is None:
                return c.execute("SELECT * FROM salidas ORDER BY id").fetchall()
            return c.execute("SELECT * FROM salidas WHERE canal = ? ORDER BY id", (canal,)).fetchall()
