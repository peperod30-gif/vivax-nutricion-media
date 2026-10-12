"""Adaptador de las cuentas que usan el motor de Vivax Nutrición (la propia y las de cada cliente).

Cada cuenta tiene su base de datos de piezas (`<datos>/<cuenta>/estado.sqlite`) y su carpeta de imágenes. Las piezas entran con
`importar()` desde su JSON, solo si pasan los controles (sin fallos). Estados de una pieza:

    pendiente → aprobada (con hora de salida) → publicando → publicada
                                   ↘ rechazada                 ↘ error (si no se puede publicar tal cual)

Candado de aprobación (`politica`, en cuentas.json):
- `validado`: ningún hecho de la pieza puede estar «pendiente: dietista-nutricionista colegiado».
- `bajo`: lo pendiente solo vale si su riesgo de publicar es «bajo» (divulgación sin riesgo: etiquetas, mitos de peso con ensayos...).
  Enfermedad, medicación, embarazo, menores o trastornos de la alimentación siguen exigiendo validación.
- `libre` (o `ninguna`): se aprueba todo lo que pasa los controles; al aprobar se avisa de lo que toca salud sin validar. Los controles (cifras contra el banco,
  promesas de peso o de curación, pautas personales, testimonios falsos, descargo) siguen mandando: no dependen de este candado.

El publicador (router/instagram.py) se inyecta desde fuera. Sin él, o con el modo en `desactivado`, lo aprobado espera en la cola.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import controles as C
import validaciones as V

from ..bd import Conexion
from ..hub import espera_reintento_s, fecha_hora_es
from .base import Pieza

RAIZ_MOTOR = Path(__file__).resolve().parents[2]
MAX_PIE = 2200      # límite de Instagram para el pie
SEPARACION_MIN = 90
MARGEN_HUECO_MIN = 20     # un hueco libre tiene que quedar al menos 20 min por delante (para poder cambiar de idea)
TARDE_MAXIMA_H = 1        # una pieza no sale más de una hora tarde: pasada esa hora se le busca otro hueco
TARDE_MANUAL_H = 3        # una hora que José puso a mano tiene tres horas de margen; si se pasan, se avisa con botón
RETRASO_PUBLICADA = timedelta(minutes=15)    # una pieza que sale más tarde que esto dice cuánto tarde en su aviso de «Publicada»
FASES_ENVIADAS = ("publicando", "publicado")  # Instagram pudo recibir la orden de publicar: la pieza ya no se puede quitar ni rechazar
HORAS_BLOQUEO = 6
ESTADO_TEXTO = {"pendiente": "pendiente", "aprobada": "aprobada", "publicando": "publicándose", "publicada": "publicada",
                "rechazada": "rechazada", "error": "con error"}
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

ESQUEMA = """
CREATE TABLE IF NOT EXISTS piezas(id INTEGER PRIMARY KEY AUTOINCREMENT, pieza_id TEXT UNIQUE NOT NULL, ruta_json TEXT NOT NULL,
                                  titulo TEXT NOT NULL, formato TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'pendiente', pie TEXT NOT NULL,
                                  creada TEXT NOT NULL, entregada INTEGER NOT NULL DEFAULT 0, decidida_por INTEGER, decidida TEXT);
CREATE TABLE IF NOT EXISTS ajustes(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS avisos_publicacion(id INTEGER PRIMARY KEY AUTOINCREMENT, texto TEXT NOT NULL, creada TEXT NOT NULL,
                                              estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN ('pendiente','enviada','fallida')),
                                              intentos INTEGER NOT NULL DEFAULT 0, reintentar_en TEXT, enviada TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS programacion_auditoria(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, pieza INTEGER NOT NULL,
                                                  quien INTEGER, accion TEXT NOT NULL, desde TEXT, hasta TEXT, origen TEXT NOT NULL,
                                                  detalle TEXT);
CREATE TRIGGER IF NOT EXISTS programacion_auditoria_no_cambia BEFORE UPDATE ON programacion_auditoria
BEGIN SELECT RAISE(ABORT, 'la auditoría de programación solo se añade'); END;
CREATE TRIGGER IF NOT EXISTS programacion_auditoria_no_borra BEFORE DELETE ON programacion_auditoria
BEGIN SELECT RAISE(ABORT, 'la auditoría de programación solo se añade'); END;
"""
COLUMNAS_NUEVAS = {"simulada": "TEXT", "programada": "TEXT", "publicada": "TEXT", "ig_media_id": "TEXT", "ig_permalink": "TEXT", "ig_estado": "TEXT",
                   "intentos": "INTEGER NOT NULL DEFAULT 0", "error": "TEXT", "proximo_intento": "TEXT", "hechos": "TEXT",
                   "programada_manual": "INTEGER NOT NULL DEFAULT 0"}


def leer_bloqueo(valor: str | None) -> tuple[str, str, str] | None:
    """(hasta, clase, texto) del bloqueo de la cola, o None. Desde esta versión se guarda «hasta|clase|texto». Las filas de
    antes, «hasta|texto», devuelven clase vacía: no se sabe qué lo causó. Solo cuenta como clase una de las bloqueantes."""
    if not valor:
        return None
    from ..instagram import BLOQUEANTES          # noqa: PLC0415 (solo hace falta si hay un bloqueo)
    partes = valor.split("|", 2)
    if len(partes) == 3 and partes[1] in BLOQUEANTES:
        return partes[0], partes[1], partes[2]
    hasta, _, texto = valor.partition("|")
    return hasta, "", texto


class ErrorImportar(Exception):
    pass


def _titulo(pieza: dict) -> str:
    """Nombre corto de la pieza para las listas: el titular de la primera hoja o de la escena, sin marcas."""
    h = (pieza.get("hojas") or [{}])[0]
    for k in ("titular", "titulo", "frase", "dato"):
        if h.get(k):
            return C._lim(h[k])
    for esc in pieza.get("escenas") or []:
        for e in esc.get("elementos", []):
            if e.get("tipo") == "titulo":
                return C._lim(e["texto"])
    return pieza["id"]


# Una pieza en «error» no sale, y no se puede reenviar desde el bot: `_pendiente` solo admite piezas pendientes (no se aprueban, editan
# ni rechazan) y `importar` no vuelve a meter una pieza que ya está en la cola (su id es único). Por eso no se promete eso.
SIN_REENVIO = ("No se puede reenviar desde el bot: si quieres ese contenido, hay que escribirla a mano como pieza nueva, "
               "con otro id (la producción no vuelve a hacer ese hueco).")


def que_hacer(clase: str, cuenta: str) -> str:
    """Lo que puede hacer José ante un fallo de Instagram o de la pieza, según su clase. Lo de la cola (token, permiso, cuenta,
    alojamiento) se arregla con una orden y la pieza se vuelve a intentar; lo de la pieza no se arregla desde el bot."""
    if clase == "token":
        return (f"genera un token nuevo en el panel de Meta y conéctalo con «python -m router instagram {cuenta}» (sin pegarlo en "
                "ningún chat); después reinicia el router.")
    if clase == "permiso":
        return ("genera el token otra vez con los permisos instagram_business_basic e instagram_business_content_publish y "
                f"conéctalo con «python -m router instagram {cuenta}».")
    if clase == "cuenta":
        return "mira en la app de Instagram si hay un aviso sobre la cuenta. Mientras tanto no sale nada."
    if clase == "alojamiento":
        return (f"revisa dónde se suben las imágenes (/instagram te lo dice). Si es GitHub con la CLI gh, ejecuta «gh auth login» y "
                f"después «python -m router github {cuenta} <usuario/repo> --con-gh».")
    if clase == "fichero":
        return f"el fichero de la pieza falta o está roto. {SIN_REENVIO}"
    if clase == "no_usar":
        return f"la pieza cita hechos que has marcado «no usar». {SIN_REENVIO}"
    if clase == "revision":
        return f"la pieza cita hechos que todavía no has revisado (puedes hacerlo con /validar). {SIN_REENVIO}"
    if clase in ("imagen_invalida", "video_invalido", "pie", "carrusel", "formato", "archivo", "pieza_rechazada"):
        return f"la pieza no cumple lo que pide Instagram. {SIN_REENVIO}"
    if clase in ("limite", "tasa", "red", "servidor", "alojamiento_red", "imagen_descarga", "contenedor_no_listo",
                 "contenedor_caducado", "ambiguo", "renovacion_pronto", "local", "rechazo_github"):
        return ("Instagram o el alojamiento no respondieron tras varios intentos. Mira la conexión y el alojamiento de las "
                f"imágenes. {SIN_REENVIO}")
    return f"mira el registro del día (router_datos/logs) para ver el detalle. {SIN_REENVIO}"


def texto_publicada(pieza_id, cuenta: str, titulo: str, enlace: str | None, retraso: timedelta | None = None) -> str:
    """El aviso de una publicación real: la pieza y su enlace de Instagram. Si salió más de RETRASO_PUBLICADA tarde (una hora que José
    puso a mano, con el router parado), lo dice al final."""
    donde = enlace or "Instagram todavía no ha devuelto el enlace; lo verás en el perfil."
    texto = f"✅ Publicada la pieza {pieza_id} de {cuenta} («{titulo[:50]}»): {donde}"
    if retraso is not None and retraso > RETRASO_PUBLICADA:
        h, m = divmod(int(retraso.total_seconds() // 60), 60)
        texto += f" Salió con {f'{h} h {m} min' if h else f'{m} min'} de retraso."
    return texto


def _estado_ig(r: sqlite3.Row) -> dict:
    """El estado de Instagram de una pieza (la fase y el contenedor, ver router/instagram.py), o {} si no hay ninguno."""
    return json.loads(r["ig_estado"]) if r["ig_estado"] else {}


def _descartar_contenedor_no_enviado(ig: dict) -> None:
    """Quita el contenedor preparado en la fase «contenedor»: nunca se envió a Instagram, así que descartarlo no puede duplicar nada, y el
    siguiente intento crea uno nuevo con el pie que esté entonces. Las imágenes ya subidas («urls») se conservan."""
    from ..instagram import _vaciar_contenedor      # noqa: PLC0415 (el módulo solo hace falta al publicar)
    if ig.get("fase") == "contenedor":
        _vaciar_contenedor(ig)


def texto_no_publicada(pieza_id, cuenta: str, titulo: str, motivo, clase: str = "") -> str:
    """El aviso de una pieza que no sale (queda en error): el motivo en español y qué hacer, sin prometer nada que no se pueda hacer."""
    return (f"🚨 No se pudo publicar la pieza {pieza_id} de {cuenta} («{titulo[:50]}»). Motivo: {motivo} "
            f"Qué hacer: {que_hacer(clase, cuenta)} La pieza queda en error y no sale.")


def texto_parada(cuenta: str, exc) -> str:
    """El aviso de una cola de publicación parada por algo que arregla el dueño."""
    return (f"🚨 Instagram de {cuenta} parado: {exc} Qué hacer: {que_hacer(getattr(exc, 'clase', ''), cuenta)} "
            f"Las piezas aprobadas esperan; se vuelve a intentar sola en {HORAS_BLOQUEO} horas.")


def texto_validaciones(cuenta: str) -> str:
    """El aviso de un almacén de validaciones que no se puede leer: nada sale hasta que se arregle. Texto fijo, sin el mensaje de la
    excepción (puede traer rutas del usuario). No se aconseja borrar el almacén: ahí están las decisiones de José."""
    return (f"🚨 No sale ninguna pieza de {cuenta}: el almacén de validaciones no se puede leer, y sin él nada se da por validado. "
            "Las piezas aprobadas esperan: salen a su hora o, si ya pasó más de una hora, en el siguiente hueco. "
            f"Qué hacer: revisa datos/validaciones/{cuenta}.json (si está roto, arréglalo; no lo borres, ahí están tus decisiones). "
            "No hace falta reiniciar: se vuelve a mirar cada minuto.")


class AdaptadorNutricion:
    anfitrion = False
    estilo_panel = False        # True en la nube: los avisos de publicación salen en llano y con los botones del panel (router/textos_panel.py)
    programable = True          # el router ofrece «Programar», «Ahora», «Cambiar hora» y «Quitar de la cola» (ver router/nucleo.py)

    def __init__(self, cuenta: str, datos: Path, salida: Path | None = None, exigir_validacion: bool = True, politica: str | None = None,
                 horas: list[str] | None = None, zona: str = "Europe/Madrid", ig: dict | None = None, publicador=None, ahora=None):
        self.cuenta = cuenta
        self.salida = salida or RAIZ_MOTOR / "salida"
        self.politica = politica or ("bajo" if exigir_validacion else "ninguna")
        self.horas = horas or ["08:00", "14:00", "21:00"]
        self.zona = ZoneInfo(zona)
        self.ig = ig or {}
        self.publicador = publicador
        self.ahora = ahora or (lambda: datetime.now(self.zona))
        self.carpeta = Path(datos) / cuenta
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.db = Conexion(self.carpeta / "estado.sqlite")
        self.db.executescript(ESQUEMA)
        existentes = {r["name"] for r in self.db.execute("PRAGMA table_info(piezas)")}
        for col, tipo in COLUMNAS_NUEVAS.items():
            if col not in existentes:
                self.db.execute(f"ALTER TABLE piezas ADD COLUMN {col} {tipo}")
        avisos = {r["name"] for r in self.db.execute("PRAGMA table_info(avisos_publicacion)")}
        if "botones" not in avisos:                  # los avisos con botón (la hora de una pieza que no salió): columna nueva
            self.db.execute("ALTER TABLE avisos_publicacion ADD COLUMN botones TEXT")
        self.db.commit()

    # ------------------------------------------------------------------ ajustes
    def _ajuste(self, k: str, defecto=None):
        r = self.db.execute("SELECT v FROM ajustes WHERE k=?", (k,)).fetchone()
        return r["v"] if r else defecto

    def _fijar(self, k: str, v) -> None:
        self.db.execute("INSERT INTO ajustes(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
        self.db.commit()

    def pausada(self) -> bool:
        return self._ajuste("pausa", "0") == "1"

    def pausar(self, si: bool) -> None:
        self._fijar("pausa", "1" if si else "0")

    # ------------------------------------------------------------------ entrada de piezas
    def importar(self, ruta_json: Path) -> int:
        pieza = json.loads(Path(ruta_json).read_text(encoding="utf-8"))
        cuenta_pieza = pieza.get("cuenta", "n1")
        if cuenta_pieza == "nutricion":
            cuenta_pieza = "n1"
        if cuenta_pieza != self.cuenta:
            raise ErrorImportar(f"La pieza {pieza['id']} es de la cuenta {cuenta_pieza!r}, no de {self.cuenta!r}.")
        formato = pieza.get("formato", "carrusel")
        carpeta = self.salida / pieza["id"]
        resultado = C.controlar(pieza, carpeta, cuenta=self.cuenta)
        fallos = [r for r in resultado if r[0] == C.FALLO]
        if fallos:
            raise ErrorImportar(f"La pieza {pieza['id']} tiene {len(fallos)} fallos en los controles; no se importa.")
        imagenes = self._archivos(formato, carpeta)
        esperadas = len(pieza.get("hojas") or []) if formato == "carrusel" else 1
        if not imagenes or (formato != "video" and len(imagenes) != esperadas):          # una imagen por hoja, no solo «alguna»
            raise ErrorImportar(f"La pieza {pieza['id']} tiene {len(imagenes)} imágenes dibujadas en {carpeta} y hacen falta {esperadas}.")
        try:
            cur = self.db.execute("INSERT INTO piezas(pieza_id, ruta_json, titulo, formato, pie, creada, hechos) VALUES(?,?,?,?,?,?,?)",
                                  (pieza["id"], str(Path(ruta_json).resolve()), pieza.get("titulo") or _titulo(pieza), formato, pieza["pie_instagram"],
                                   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   json.dumps(pieza.get("hechos", []), ensure_ascii=False)))
            self.db.commit()
        except Exception:                                    # sin esto, una transacción a medias queda abierta y bloquea a los demás
            self.db.rollback()
            raise
        return cur.lastrowid

    # ------------------------------------------------------------------ lectura
    @staticmethod
    def _archivos(formato: str, carpeta: Path) -> list[Path]:
        if formato == "video":
            v = carpeta / "video.mp4"
            return [v] if v.is_file() else []
        return sorted(carpeta.glob("[0-9][0-9].jpg"))

    def _json_de(self, r: sqlite3.Row) -> dict:
        return json.loads(Path(r["ruta_json"]).read_text(encoding="utf-8"))

    def _a_pieza(self, r: sqlite3.Row | None) -> Pieza | None:
        if r is None:
            return None
        return Pieza(r["id"], self.cuenta, r["titulo"], r["formato"], r["estado"], r["pie"],
                     self._archivos(r["formato"], self.salida / r["pieza_id"]), r["creada"], bool(r["entregada"]))

    def por_entregar(self) -> list[Pieza]:
        return [self._a_pieza(r) for r in self.db.execute("SELECT * FROM piezas WHERE entregada=0 AND estado='pendiente' ORDER BY id")]

    def marcar_entregada(self, id: int) -> None:
        self.db.execute("UPDATE piezas SET entregada=1 WHERE id=?", (id,))
        self.db.commit()

    def pieza(self, id: int) -> Pieza | None:
        return self._a_pieza(self.db.execute("SELECT * FROM piezas WHERE id=?", (id,)).fetchone())

    def listar(self, limite: int = 10) -> list[Pieza]:
        return [self._a_pieza(r) for r in self.db.execute("SELECT * FROM piezas ORDER BY id DESC LIMIT ?", (limite,))]

    # ------------------------------------------------------------------ decisiones
    def _hechos(self, r: sqlite3.Row) -> list[str]:
        """Los hechos que cita la pieza: los guardados al importarla (no dependen del fichero) o, si la pieza es de antes, los de su JSON."""
        if r["hechos"] is not None:
            return json.loads(r["hechos"])
        return json.loads(Path(r["ruta_json"]).read_text(encoding="utf-8")).get("hechos", [])

    def _revision(self, r: sqlite3.Row) -> tuple[list[str], list[str]]:
        """(hechos que José ha marcado «no usar», hechos pendientes que impiden aprobar con este candado). Con `libre` y `ninguna`
        solo cuenta lo de «no usar», con cualquier candado. Puede lanzar OSError, ValueError o ErrorValidaciones."""
        banco = C.cargar_banco(self.cuenta)
        con_candado = self.politica in ("validado", "bajo")
        hechos = json.loads(Path(r["ruta_json"]).read_text(encoding="utf-8")).get("hechos", []) if con_candado else self._hechos(r)
        prohibidos = [i for i in hechos if banco.get(i, {}).get("no_usar")]
        malos = []
        if con_candado:
            for i in hechos:
                h = banco.get(i)
                if not h or "pendiente" not in str(h.get("estado_validacion", "")):
                    continue
                texto = f"{h.get('tema', '')} {h.get('enunciado', '')}"
                toca_salud = bool(h.get("poblaciones_en_riesgo")) or bool(re.search(C.ENFERMEDADES + r"|medicaci|insulina|embaraz|lactancia", texto, re.I))
                if self.politica == "validado" or h.get("riesgo_publicar") != "bajo" or toca_salud:
                    malos.append(i)
        return prohibidos, malos

    def hechos_en_uso(self) -> set[str]:
        """Hechos que citan las piezas pendientes, aprobadas o publicándose (sirve para ordenar /validar)."""
        usados: set[str] = set()
        for r in self.db.execute("SELECT * FROM piezas WHERE estado IN ('pendiente','aprobada','publicando')").fetchall():
            try:
                usados.update(self._hechos(r))
            except (OSError, ValueError):
                pass
        return usados

    def _pendiente(self, id: int) -> sqlite3.Row | str:
        r = self.db.execute("SELECT * FROM piezas WHERE id=?", (id,)).fetchone()
        if r is None:
            return "Esa pieza no existe."
        if r["estado"] != "pendiente":
            return f"Esa pieza ya está {r['estado']}."
        return r

    def _candado(self, r: sqlite3.Row, para_admin: bool) -> str | None:
        """El motivo por el que el candado no deja aprobar esta pieza, o None si la deja. Quien no es el administrador recibe un motivo
        neutro: qué hechos revisa José es asunto suyo, y el aprobador no puede arreglarlo editando el pie."""
        neutro = "No se puede aprobar ahora: José tiene que revisar algunos hechos de esta pieza. Se puede editar o rechazar."
        try:
            prohibidos, sin = self._revision(r)
        except V.ErrorValidaciones as exc:
            return f"No se puede aprobar: {exc}." if para_admin else neutro
        except (OSError, ValueError):
            return ("No se puede aprobar: falta el fichero de la pieza o está roto. Recházala; si quieres ese contenido, hay que "
                    "escribirlo a mano como pieza nueva, con otro id.")
        if prohibidos:
            return ("No se puede aprobar: cita hechos que has marcado como «no usar» "
                    f"({', '.join(prohibidos)}). Se puede editar o rechazar.") if para_admin else neutro
        if sin:
            return ("No se puede aprobar: usa hechos que exigen validar antes de salir y José todavía no los ha validado "
                    f"({', '.join(sin)}). Se puede editar o rechazar.") if para_admin else neutro
        return None

    def aprobar(self, id: int, por: int, para_admin: bool = True, hora: datetime | None = None, origen: str = "boton:ok") -> str | None:
        """None si queda aprobada; si no, el motivo. Sin `hora`, sale en el primer hueco libre (como siempre). Con `hora`, sale a esa hora
        exacta, que José ha elegido (programada a mano). El candado y la línea de auditoría son los mismos en los dos casos."""
        r = self._pendiente(id)
        if isinstance(r, str):
            return r
        motivo = self._candado(r, para_admin)
        if motivo:
            return motivo
        if hora is not None and hora.astimezone(timezone.utc) <= self.ahora().astimezone(timezone.utc):
            return "Esa hora ya ha pasado."
        iso = self.siguiente_hueco() if hora is None else hora.astimezone(self.zona).isoformat(timespec="seconds")
        ig = _estado_ig(r)
        _descartar_contenedor_no_enviado(ig)        # una pieza que vuelve a aprobarse tras quitarla: su contenedor (de antes) no vale
        with self.db.transaccion() as c:
            cur = c.execute("UPDATE piezas SET estado='aprobada', decidida_por=?, decidida=?, programada=?, programada_manual=?, ig_estado=? "
                            "WHERE id=? AND estado='pendiente' AND ig_estado IS ?",
                            (por, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), iso, 0 if hora is None else 1,
                             json.dumps(ig) if ig else None, id, r["ig_estado"]))
            if cur.rowcount != 1:
                return "Esa pieza ya no está pendiente."
            self._auditar(c, id, por, "aprobada_auto" if hora is None else "aprobada_hora", None, iso, origen)
        return None

    def cambiar_hora(self, id: int, hora: datetime, por: int, origen: str = "boton:ch", para_admin: bool = True) -> str | None:
        """Cambia la hora de una pieza ya aprobada (a mano). Solo mientras espera: si ya se está publicando o está publicada, no se toca.
        Vuelve a pasar el candado, como al aprobar. Quita los reintentos y la simulación previas: la hora nueva empieza de cero."""
        r = self.fila(id)
        if r is None:
            return "Esa pieza no existe."
        if r["estado"] != "aprobada":
            return f"Esa pieza ya está {ESTADO_TEXTO.get(r['estado'], r['estado'])}: no se puede cambiar la hora."
        motivo = self._candado(r, para_admin)
        if motivo:
            return motivo
        if hora.astimezone(timezone.utc) <= self.ahora().astimezone(timezone.utc):
            return "Esa hora ya ha pasado."
        if r["programada"] and hora.astimezone(timezone.utc) == datetime.fromisoformat(r["programada"]).astimezone(timezone.utc):
            return f"Sigue programada para {fecha_hora_es(hora)}: no he cambiado nada."     # se compara el instante, no el texto
        iso = hora.astimezone(self.zona).isoformat(timespec="seconds")
        with self.db.transaccion() as c:
            cur = c.execute("UPDATE piezas SET programada=?, programada_manual=1, proximo_intento=NULL, simulada=NULL, intentos=0, error=NULL "
                            "WHERE id=? AND estado='aprobada'", (iso, id))
            if cur.rowcount != 1:
                return "La pieza ya no está aprobada: no he cambiado su hora."
            self._auditar(c, id, por, "hora_cambiada", r["programada"], iso, origen)
        return None

    def quitar_de_cola(self, id: int, por: int, origen: str = "boton:qu") -> str | None:
        """Devuelve una pieza aprobada a pendiente: vuelve a su tarjeta y se decide otra vez. No se rechaza. Quita los reintentos y la
        simulación. Si Instagram ya empezó a publicarla (fase «publicando» o «publicado», ver router/instagram.py), no se quita: el envío
        pudo llegar y no se sabe si salió. Si solo había un contenedor sin enviar (fase «contenedor»), se descarta: así el pie que se apruebe
        otra vez es el que sale. Las imágenes subidas se conservan."""
        r = self.fila(id)
        if r is None:
            return "Esa pieza no existe."
        if r["estado"] != "aprobada":
            return f"Esa pieza ya está {ESTADO_TEXTO.get(r['estado'], r['estado'])}: no se puede quitar de la cola."
        ig = _estado_ig(r)
        if ig.get("fase") in FASES_ENVIADAS:
            return "Instagram ya ha empezado a publicarla: no se puede quitar de la cola. Mira en la cuenta si ha salido."
        _descartar_contenedor_no_enviado(ig)
        with self.db.transaccion() as c:
            # el estado de Instagram se condiciona al valor que se leyó: si el publicador lo cambió mientras tanto, no se pisa
            cur = c.execute("UPDATE piezas SET estado='pendiente', programada=NULL, programada_manual=0, decidida_por=NULL, decidida=NULL, "
                            "simulada=NULL, proximo_intento=NULL, intentos=0, error=NULL, ig_estado=? "
                            "WHERE id=? AND estado='aprobada' AND ig_estado IS ?",
                            (json.dumps(ig) if ig else None, id, r["ig_estado"]))
            if cur.rowcount != 1:
                return "La pieza ya no está aprobada, o Instagram acaba de cambiar su estado: no la he tocado."
            self._auditar(c, id, por, "quitada_cola", r["programada"], None, origen)
        return None

    def _auditar(self, c, pieza: int, quien: int | None, accion: str, desde: str | None, hasta: str | None, origen: str,
                 detalle: str = "") -> None:
        """Una línea de la auditoría de programación (solo se añade). Va dentro de la transacción del cambio (`c`): o queda el cambio y
        su línea, o no queda nada. `quien` es None cuando lo hace el propio router (la publicación)."""
        c.execute("INSERT INTO programacion_auditoria(ts, pieza, quien, accion, desde, hasta, origen, detalle) VALUES(?,?,?,?,?,?,?,?)",
                  (self._instante(), pieza, quien, accion, desde, hasta, origen, detalle[:200]))

    def _aviso_de_salud(self, r: sqlite3.Row) -> str:
        """Con el candado `libre` se aprueba todo lo que pasa los controles, pero se dice qué toca salud sin validar. Si todos los
        hechos de riesgo medio o alto están validados, no hay aviso."""
        banco = C.cargar_banco(self.cuenta)
        sin_validar, falta_otro, sin_verificar = [], [], []
        for i in self._hechos(r):
            h = banco.get(i)
            if h and h.get("riesgo_publicar") == "bajo" and not h.get("verificacion_primaria") and h.get("validacion") != "validado" \
                    and h.get("veredicto") != "ejemplo":
                sin_verificar.append(i)                      # riesgo bajo sin fuente primaria: pasa, pero José lo ve en la tarjeta
            if not h or "pendiente" not in str(h.get("estado_validacion", "")) or h.get("riesgo_publicar") == "bajo":
                continue
            if h.get("validacion") == "validado" and h.get("vigente"):      # lo de José está hecho; falta otro profesional (p. ej. abogado)
                falta_otro.append(i)
                continue
            nota = str(h.get("validacion_nota") or "")
            sin_validar.append(i + (f" (con reparos: «{nota[:160]}»)" if h.get("validacion") == "con_reparos" and nota else ""))
        aviso = ""
        if sin_validar:
            aviso += " ⚠ Toca salud (riesgo medio o alto) y todavía no lo ha validado José: " + ", ".join(sin_validar) + ". Revísalo con /validar."
        if falta_otro:
            aviso += " ⚠ Validado por José, pero exige además la revisión de otro profesional: " + ", ".join(falta_otro) + "."
        if sin_verificar:
            aviso += (" ⚠ Riesgo bajo, pero sin fuente primaria verificada ni tu validación: " + ", ".join(sin_verificar)
                      + ". Pasa los controles; revísalo con /validar.")
        return aviso

    def resumen_decision(self, id: int, para_admin: bool = True) -> str:
        """Cuándo sale la pieza y, si toca salud sin validar, el aviso. El aviso con ids, notas de reparos y órdenes es del
        administrador; a quien aprueba se le dice solo que hay hechos de salud pendientes de revisión por José."""
        r = self.db.execute("SELECT * FROM piezas WHERE id=?", (id,)).fetchone()
        if not r or not r["programada"]:
            return ""
        try:
            aviso = self._aviso_de_salud(r) if self.politica in ("ninguna", "libre") else ""
        except (OSError, ValueError):
            aviso = ""
        if aviso and not para_admin:
            aviso = " Hay hechos de salud pendientes de revisión por José."
        t = datetime.fromisoformat(r["programada"]).astimezone(self.zona)
        motivo = self.motivo_sin_salida()
        if not motivo:
            return f" Saldrá el {DIAS[t.weekday()]} {t.day}/{t.month} a las {t:%H:%M}.{aviso}"
        return f" Queda en cola para el {DIAS[t.weekday()]} {t.day}/{t.month} a las {t:%H:%M}, pero {motivo}: no saldrá sola.{aviso}"

    def motivo_sin_salida(self) -> str:
        """Por qué una pieza aprobada no saldría sola (falta el token, modo distinto de «real», pausa o cola parada por Instagram), o «» si
        saldría. Es lo que dice «¿Publicar ahora?» y las confirmaciones; `aviso_salida` lo añade a la parte de salud."""
        if self.publicador is None:
            return "falta el token de Instagram"
        modo = self.ig.get("modo", "desactivado")
        if modo != "real":
            return f"la publicación automática está en modo «{modo}»"
        if self.pausada():
            return "las publicaciones están en pausa (/seguir)"
        if self._bloqueada(self.ahora()):
            return "la cola está parada por un problema de Instagram"
        return ""

    def aviso_de_salud(self, id: int) -> str:
        """El aviso de salud de una pieza pendiente (hechos de riesgo medio o alto sin validar), para la tarjeta del administrador:
        se ve antes de aprobar. Con los candados que bloquean la aprobación no hace falta."""
        r = self.db.execute("SELECT * FROM piezas WHERE id=?", (id,)).fetchone()
        if not r or r["estado"] != "pendiente" or self.politica not in ("ninguna", "libre"):
            return ""
        voz = self._aviso_de_voz(r)
        try:
            return voz + self._aviso_de_salud(r)
        except (OSError, ValueError):
            return voz

    def _aviso_de_voz(self, r) -> str:
        """Un vídeo con voz sintética se publica con la etiqueta de IA (is_ai_generated, ver router/instagram.py): la tarjeta lo dice
        antes de aprobar. Solo mira la pieza (no el pie, que sale en Instagram)."""
        if r["formato"] != "video":
            return ""
        try:
            pieza = self._json_de(r)
        except (OSError, ValueError, TypeError):
            return ""
        sal = " Voz sintética: se publica con la etiqueta de IA." if pieza.get("ia") is True else ""
        frases = (pieza.get("produccion") or {}).get("frases_sin_toma_limpia") or []
        if frases:                       # las que hay que escuchar antes de aprobar: cinco como mucho, cada una recortada (es un mensaje)
            dichas = "; ".join(f"«{str(f.get('texto', ''))[:80]}» ({str(f.get('motivo', ''))[:40]})" for f in frases[:5])
            mas = f" y {len(frases) - 5} más" if len(frases) > 5 else ""
            sal += f" Frases de la voz sin toma limpia, escúchalas antes de aprobar: {dichas}{mas}."
        avisos = (pieza.get("produccion") or {}).get("avisos") or []
        if avisos:                       # los avisos de los controles (ritmo, validación...): no paran la pieza; cuatro como mucho, recortados
            mas = f"; y {len(avisos) - 4} más" if len(avisos) > 4 else ""
            sal += " Avisos de los controles: " + "; ".join(str(a)[:120] for a in avisos[:4]) + mas + "."
        return sal

    def rechazar(self, id: int, por: int) -> str | None:
        r = self._pendiente(id)
        if isinstance(r, str):
            return r
        if _estado_ig(r).get("fase") in FASES_ENVIADAS:         # una pieza que Instagram pudo publicar no se puede dar por rechazada
            return "Instagram ya ha empezado a publicarla: no se puede rechazar."
        self._cerrar(id, "rechazada", por)
        return None

    def _cerrar(self, id: int, estado: str, por: int) -> None:
        self.db.execute("UPDATE piezas SET estado=?, decidida_por=?, decidida=? WHERE id=?",
                        (estado, por, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), id))
        self.db.commit()

    def editar_pie(self, id: int, texto: str, por: int) -> list[str]:
        r = self._pendiente(id)
        if isinstance(r, str):
            return [r]
        texto = texto.strip()
        if not texto or len(texto) > MAX_PIE:
            return [f"El pie tiene que tener entre 1 y {MAX_PIE} caracteres."]
        pieza = self._json_de(r)
        pieza["pie_instagram"] = texto
        problemas = [f"{q}: {m}" for n, q, m in C.controlar(pieza, cuenta=self.cuenta) if n == C.FALLO]
        if problemas:
            return problemas[:4]
        self.db.execute("UPDATE piezas SET pie=? WHERE id=?", (texto, id))
        self.db.commit()
        return []

    # ------------------------------------------------------------------ horas de salida
    def siguiente_hueco(self, desde: datetime | None = None) -> str:
        """La primera hora de salida libre: uno de los huecos del día, separada de las demás y con margen para poder cambiar de idea.
        Las comparaciones se hacen en UTC: dentro de Madrid, Python ignora el desfase al comparar (cambio de hora de octubre)."""
        desde_utc = (desde or self.ahora()).astimezone(timezone.utc) + timedelta(minutes=MARGEN_HUECO_MIN)
        ocupadas = [datetime.fromisoformat(r["programada"]) for r in self.db.execute(
            "SELECT programada FROM piezas WHERE programada IS NOT NULL AND estado IN ('aprobada','publicando')")]
        dia0 = desde_utc.astimezone(self.zona).date()
        for d in range(0, 120):
            dia = dia0 + timedelta(days=d)
            for hhmm in self.horas:
                h, m = (int(x) for x in hhmm.split(":"))
                t = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=self.zona)
                if t.astimezone(timezone.utc) < desde_utc:
                    continue
                if any(abs((t.astimezone(timezone.utc) - o.astimezone(timezone.utc)).total_seconds()) < SEPARACION_MIN * 60 for o in ocupadas):
                    continue
                return t.isoformat(timespec="seconds")
        raise RuntimeError("No hay hueco libre en los próximos 120 días")

    def huecos_libres(self, ahora: datetime, dias: int = 2, excluir: int | None = None) -> list[datetime]:
        """Los huecos de la cuenta (08:00, 14:00, 21:00) de hoy y mañana que están libres: a 90 min o más de cualquier otra pieza aprobada
        o publicándose (salvo `excluir`, la propia pieza), y con el margen de 20 min. Son horas de Madrid con zona."""
        desde_utc = ahora.astimezone(timezone.utc) + timedelta(minutes=MARGEN_HUECO_MIN)
        ocupadas = [datetime.fromisoformat(r["programada"]) for r in self.db.execute(
            "SELECT programada FROM piezas WHERE programada IS NOT NULL AND estado IN ('aprobada','publicando') AND id<>?", (excluir or -1,))]
        hoy = ahora.astimezone(self.zona).date()
        libres: list[datetime] = []
        for d in range(dias):
            dia = hoy + timedelta(days=d)
            for hhmm in self.horas:
                h, m = (int(x) for x in hhmm.split(":"))
                t = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=self.zona)
                if t.astimezone(timezone.utc) < desde_utc:
                    continue
                if any(abs((t.astimezone(timezone.utc) - o.astimezone(timezone.utc)).total_seconds()) < SEPARACION_MIN * 60 for o in ocupadas):
                    continue
                libres.append(t)
        return libres

    def hora_del_plan(self, id: int, ahora: datetime) -> datetime | None:
        """La hora que dice el plan para esta pieza: su fecha y su hueco (`produccion.fecha` y `produccion.hueco`, que escribe produccion.py)
        con la hora de ese hueco en la cuenta. None si la pieza no lleva plan, si no se lee o si esa hora ya no está a dos minutos vista."""
        r = self.fila(id)
        if r is None:
            return None
        try:
            prod = (self._json_de(r).get("produccion") or {})
            dia = date.fromisoformat(str(prod.get("fecha", ""))[:10])
            hueco = int(prod.get("hueco"))
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        if not 1 <= hueco <= len(self.horas):
            return None
        h, m = (int(x) for x in self.horas[hueco - 1].split(":"))
        t = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=self.zona)
        return t if t.astimezone(timezone.utc) > ahora.astimezone(timezone.utc) + timedelta(minutes=2) else None

    def hora_prevista(self, id: int) -> datetime | None:
        """La hora que dice el plan para esta pieza, sin mirar si ya pasó: `produccion.fecha` con `produccion.hora` (HH:MM) o, si no hay hora,
        con la del hueco (`produccion.hueco`). None si la pieza no lleva plan o no se puede leer. Es lo que usa el panel para programar de golpe."""
        r = self.fila(id)
        if r is None:
            return None
        try:
            prod = (self._json_de(r).get("produccion") or {})
            dia = date.fromisoformat(str(prod.get("fecha", ""))[:10])
            hh = str(prod.get("hora") or "")
            if re.fullmatch(r"\d{1,2}:\d{2}", hh):
                h, m = (int(x) for x in hh.split(":"))
            else:
                hueco = int(prod.get("hueco"))
                if not 1 <= hueco <= len(self.horas):
                    return None
                h, m = (int(x) for x in self.horas[hueco - 1].split(":"))
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=self.zona)

    def filas_panel(self, estados: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        """Las piezas con lo que necesita el panel para dibujarlas (estado, horas, enlace, error, pie). Sin filtro, todas."""
        sql = "SELECT id, pieza_id, titulo, estado, programada, publicada, ig_permalink, error, intentos, formato, pie, ig_estado FROM piezas"
        if estados:
            sql += " WHERE estado IN (" + ",".join("?" * len(estados)) + ")"
            return self.db.execute(sql + " ORDER BY id", estados).fetchall()
        return self.db.execute(sql + " ORDER BY id").fetchall()

    def reintentar(self, id: int, por: int, hora: datetime | None = None, origen: str = "panel:reintentar") -> str | None:
        """Una pieza «con problema» vuelve a la cola: aprobada otra vez, en la hora que se diga o en el primer hueco libre. Si Instagram ya
        pudo recibir la orden de publicarla, no se reintenta (podría salir dos veces): se dice que mire en la cuenta."""
        r = self.fila(id)
        if r is None:
            return "Esa pieza no existe."
        if r["estado"] != "error":
            return f"Esa pieza ya está {ESTADO_TEXTO.get(r['estado'], r['estado'])}: no hay nada que reintentar."
        ig = _estado_ig(r)
        if ig.get("fase") in FASES_ENVIADAS:
            return "Instagram ya ha empezado a publicarla: mira en la cuenta si ha salido antes de reintentar."
        motivo = self._candado(r, True)
        if motivo:
            return motivo
        if hora is not None and hora.astimezone(timezone.utc) <= self.ahora().astimezone(timezone.utc):
            return "Esa hora ya ha pasado."
        iso = self.siguiente_hueco() if hora is None else hora.astimezone(self.zona).isoformat(timespec="seconds")
        _descartar_contenedor_no_enviado(ig)
        with self.db.transaccion() as c:
            cur = c.execute("UPDATE piezas SET estado='aprobada', programada=?, programada_manual=?, proximo_intento=NULL, simulada=NULL, intentos=0, "
                            "error=NULL, ig_estado=? WHERE id=? AND estado='error'", (iso, 0 if hora is None else 1, json.dumps(ig) if ig else None, id))
            if cur.rowcount != 1:
                return "La pieza ya no está con problema: no la he tocado."
            self._auditar(c, id, por, "reintentada", None, iso, origen)
        return None

    def descartar(self, id: int, por: int) -> str | None:
        """Una pieza «con problema» que ya no se quiere: pasa a rechazada. Con la misma precaución que rechazar."""
        r = self.fila(id)
        if r is None:
            return "Esa pieza no existe."
        if r["estado"] != "error":
            return f"Esa pieza ya está {ESTADO_TEXTO.get(r['estado'], r['estado'])}."
        if _estado_ig(r).get("fase") in FASES_ENVIADAS:
            return "Instagram ya ha empezado a publicarla: mira en la cuenta si ha salido."
        self._cerrar(id, "rechazada", por)
        return None

    def choques(self, id: int, instante: datetime, minutos: int = 45) -> list[tuple[int, datetime]]:
        """Otras piezas de la cola (aprobadas o publicándose) a menos de `minutos` de esa hora, antes o después. Para el aviso «ojo»."""
        ref = instante.astimezone(timezone.utc)
        out = []
        for r in self.db.execute("SELECT id, programada FROM piezas WHERE programada IS NOT NULL AND estado IN ('aprobada','publicando') "
                                 "AND id<>?", (id,)).fetchall():
            t = datetime.fromisoformat(r["programada"])
            if abs((t.astimezone(timezone.utc) - ref).total_seconds()) < minutos * 60:
                out.append((r["id"], t))
        return sorted(out, key=lambda x: x[1].astimezone(timezone.utc))

    def aviso_salida(self, id: int, para_admin: bool = True) -> str:
        """Lo que impide que la pieza salga a su hora aunque la tenga (modo de publicación, pausa, cola parada por Instagram), y el aviso
        de salud si toca salud sin validar (a quien aprueba solo se le dice que hay hechos pendientes). Vacío si no hay nada que decir."""
        partes: list[str] = []
        modo = self.ig.get("modo", "desactivado")
        if self.publicador is None:                 # sin token no hay publicador: no se dice «modo real», que sería absurdo
            partes.append("Ojo: no hay conexión con Instagram (falta el token): no saldrá sola.")
        elif modo != "real":
            partes.append(f"Ojo: la publicación automática está en modo «{modo}»: no saldrá sola.")
        if self.pausada():
            partes.append("Ojo: las publicaciones están en pausa: no saldrá hasta que las reanudes con /seguir.")
        if self._bloqueada(self.ahora()):
            partes.append("Ojo: la cola está parada por un problema de Instagram: no saldrá hasta que se arregle.")
        r = self.fila(id)
        if r is not None and self.politica in ("ninguna", "libre"):
            try:
                aviso = self._aviso_de_salud(r).strip()
            except (OSError, ValueError):
                aviso = ""
            if aviso:
                partes.append(aviso if para_admin else "Hay hechos de salud pendientes de revisión por José.")
        return " ".join(partes)

    def fila(self, id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM piezas WHERE id=?", (id,)).fetchone()

    def contar(self, estado: str) -> int:
        return self.db.execute("SELECT COUNT(*) n FROM piezas WHERE estado=?", (estado,)).fetchone()["n"]

    def filas_cola(self) -> list[sqlite3.Row]:
        """Las aprobadas (con su hora), y las publicándose o con error, en orden de salida (las horas se comparan en UTC)."""
        filas = self.db.execute("SELECT id, titulo, estado, programada, programada_manual FROM piezas "
                                "WHERE estado IN ('aprobada','publicando','error')").fetchall()
        return sorted(filas, key=lambda f: (f["estado"] != "aprobada", datetime.fromisoformat(f["programada"]).astimezone(timezone.utc)
                                            if f["programada"] else datetime.max.replace(tzinfo=timezone.utc), f["id"]))

    def _mover_retrasada(self, r: sqlite3.Row, ahora: datetime) -> None:
        """Una pieza aprobada que pasó su margen sin salir se pasa al siguiente hueco libre. Con su línea de auditoría y su aviso con
        «⏰ Cambiar hora», en la misma transacción que el cambio: o quedan los dos o ninguno, y el aviso lo entrega el router con reintentos
        (igual para una hora automática que para una que José puso a mano). Una hora de José conserva su marca («a mano»), así que su
        segundo retraso tiene el mismo margen de tres horas. Solo se mueve si la hora sigue siendo la que se leyó."""
        nuevo = self.siguiente_hueco(ahora)
        texto = self._texto_retraso(r, nuevo)
        botones = [[("⏰ Cambiar hora", f"{self.cuenta}:ch:{r['id']}")]]
        if self.estilo_panel:
            from ..textos_panel import aviso_retraso        # noqa: PLC0415
            texto, botones = aviso_retraso(r["titulo"], datetime.fromisoformat(r["programada"]), datetime.fromisoformat(nuevo), r["id"])
        with self.db.transaccion() as c:
            cur = c.execute("UPDATE piezas SET programada=? WHERE id=? AND estado='aprobada' AND programada IS ?",
                            (nuevo, r["id"], r["programada"]))
            if cur.rowcount != 1:
                return
            self._auditar(c, r["id"], None, "movida_retraso", r["programada"], nuevo, "sistema:retraso")
            self._encolar(c, texto, botones)

    def _texto_retraso(self, r: sqlite3.Row, nuevo_iso: str) -> str:
        """El aviso de una pieza que José programó a mano y no salió a su hora. Dice la causa que se sabe: si falló al publicar, cuántas veces;
        si no, que el router no estaba en marcha o no pudo publicar a tiempo (no se afirma que el equipo estuviera apagado)."""
        previsto = datetime.fromisoformat(r["programada"]).astimezone(self.zona)
        nuevo = datetime.fromisoformat(nuevo_iso).astimezone(self.zona)
        causa = (f" Falló {r['intentos']} veces al intentar publicarla." if r["intentos"]
                 else " El router no estaba en marcha, o no pudo publicarla a tiempo.")
        return (f"⚠️ La pieza {r['id']} de {self.cuenta} («{r['titulo'][:50]}») no salió el {fecha_hora_es(previsto)}.{causa} "
                f"La he movido al hueco {fecha_hora_es(nuevo)}: ¿quieres otra hora?")

    # ------------------------------------------------------------------ publicación
    def _bloqueada(self, ahora: datetime) -> str | None:
        b = leer_bloqueo(self._ajuste("bloqueo"))
        if b:
            hasta, _clase, texto = b
            if datetime.fromisoformat(hasta) > ahora:
                return texto
            self.db.execute("DELETE FROM ajustes WHERE k='bloqueo'")
            self.db.commit()
        return None

    def ciclo_publicador(self) -> list[str]:
        """Una vuelta: publica la pieza aprobada cuya hora ha llegado. Devuelve los avisos para el administrador."""
        avisos: list[str] = []
        modo = self.ig.get("modo", "desactivado")
        if self.publicador is None:
            return avisos
        ahora = self.ahora()
        self._vigilar_validaciones()                        # si el almacén de validaciones vuelve a leerse, un incidente nuevo volverá a avisar
        avisos += self._mantenimiento(ahora)                # el token se renueva aunque la publicación esté parada
        if modo == "desactivado" or self.pausada() or self._bloqueada(ahora):
            return avisos
        # una pieza que no salió a su hora (el router no estaba en marcha) no sale tarde: se le busca otro hueco. Las que José puso a mano
        # tienen tres horas de margen. En los dos casos el aviso va a la tabla de avisos, con un botón para cambiar la hora (ver _mover_retrasada)
        for r in self.db.execute("SELECT * FROM piezas WHERE estado='aprobada' AND programada IS NOT NULL").fetchall():
            tope = TARDE_MANUAL_H if r["programada_manual"] else TARDE_MAXIMA_H
            if ahora - datetime.fromisoformat(r["programada"]) > timedelta(hours=tope):
                self._mover_retrasada(r, ahora)
        rows = self.db.execute("SELECT * FROM piezas WHERE estado IN ('publicando','aprobada') AND programada IS NOT NULL ORDER BY (estado='publicando') DESC, programada").fetchall()
        for r in rows:
            if datetime.fromisoformat(r["programada"]) > ahora:
                continue
            if modo == "simulacion" and r["simulada"]:
                continue                                    # ya simulada: no se repite cada minuto; en modo real saldrá
            if r["proximo_intento"] and datetime.fromisoformat(r["proximo_intento"]) > ahora:
                continue
            avisos += self._publicar(r, ahora)
            break                                           # una por vuelta
        return avisos

    guardar_token = None            # lo pone main: escribe el token nuevo en el .env sin pasar por ningún otro sitio

    def _mantenimiento(self, ahora: datetime) -> list[str]:
        """Una vez al día mira si toca renovar el token (los de 60 días se renuevan pasado el primer día y antes de que caduquen)."""
        if self._ajuste("mantenimiento") == ahora.date().isoformat():
            return []
        self._fijar("mantenimiento", ahora.date().isoformat())
        ultima = self._ajuste("token_renovado")
        if ultima and (ahora - datetime.fromisoformat(ultima)).days < 20:
            return []
        try:
            r = self.publicador.renovar_token()
        except Exception as exc:                              # el mensaje de ErrorInstagram no lleva secretos
            if getattr(exc, "clase", "") == "renovacion_pronto":
                return []
            return [f"No he podido renovar el token de Instagram de {self.cuenta}: {exc}"]
        if self.guardar_token:
            self.guardar_token(r["token"])
        self._fijar("token_renovado", ahora.isoformat(timespec="seconds"))
        self.ig.setdefault("info", {})["token_expira"] = r["caduca"]
        ruta = self.ig.get("info_ruta")
        if ruta and Path(ruta).is_file():
            info = json.loads(Path(ruta).read_text(encoding="utf-8"))
            info["token_expira"] = r["caduca"]
            Path(ruta).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return []

    def _publicar(self, r: sqlite3.Row, ahora: datetime) -> list[str]:
        from ..instagram import ErrorInstagram      # noqa: PLC0415 (el módulo solo hace falta si se publica)
        try:
            pieza = self._json_de(r)
        except (OSError, ValueError):
            self._pasar_a_error(r, "falta el fichero JSON de la pieza", texto_no_publicada(r["id"], self.cuenta, r["titulo"], "falta su fichero JSON.", "fichero"))
            return []
        try:
            banco = C.cargar_banco(self.cuenta)
        except V.ErrorValidaciones:
            return self._parar_por_validaciones()           # sin validaciones legibles no sale nada: un aviso urgente por incidente
        prohibidos = [i for i in self._hechos(r) if banco.get(i, {}).get("no_usar")]
        if prohibidos:                                      # aprobada antes de que José marcara un hecho «no usar»: no sale
            self._pasar_a_error(r, f"cita hechos marcados «no usar»: {', '.join(prohibidos)}",
                                texto_no_publicada(r["id"], self.cuenta, r["titulo"], f"cita hechos que has marcado «no usar»: {', '.join(prohibidos)}.",
                                                   "no_usar"))
            return []
        try:                                                # el candado se vuelve a mirar: un hecho puede haber cambiado después de aprobar
            _, pendientes = self._revision(r)
        except V.ErrorValidaciones:
            return self._parar_por_validaciones()
        except (OSError, ValueError):
            return []
        if pendientes:
            motivo = f"cita hechos que José todavía no ha revisado: {', '.join(pendientes)}"
            self._pasar_a_error(r, motivo, texto_no_publicada(r["id"], self.cuenta, r["titulo"],
                                                             f"cita hechos que todavía no has revisado: {', '.join(pendientes)}.", "revision"))
            return []
        archivos = self._archivos(r["formato"], self.salida / r["pieza_id"])
        estado = json.loads(r["ig_estado"]) if r["ig_estado"] else {}
        # el reclamo solo vale si la hora sigue siendo la que se leyó: si José la cambió mientras tanto, no sale a la hora vieja
        cur = self.db.execute("UPDATE piezas SET estado='publicando' WHERE id=? AND estado IN ('aprobada','publicando') AND programada IS ?",
                              (r["id"], r["programada"]))
        self.db.commit()
        if cur.rowcount == 0:
            return []

        def guardar(e: dict) -> None:
            self.db.execute("UPDATE piezas SET ig_estado=? WHERE id=?", (json.dumps(e), r["id"]))
            self.db.commit()

        try:
            res = self.publicador.publicar(r["formato"], archivos, r["pie"], estado, guardar, ia=bool(pieza.get("ia")), alt=pieza.get("alt"))
        except ErrorInstagram as exc:
            return self._fallo(r, exc, ahora)
        except Exception as exc:
            # Un fallo que no es de Instagram (disco, antivirus, un error nuestro). Sin esta rama la pieza se quedaba en
            # «publicando» y, como va primero en la cola, impedía salir a todas las demás. Solo se dice el tipo de error:
            # el texto de un OSError trae rutas del usuario.
            return self._fallo(r, ErrorInstagram("local", f"Fallo local al publicar ({type(exc).__name__}). Se volverá a "
                                                          "intentar; si se repite, revisa el disco y el antivirus."), ahora)
        if str(res.media_id).startswith("simulado"):
            self.db.execute("UPDATE piezas SET estado='aprobada', simulada=?, ig_estado=NULL, error=NULL WHERE id=?",
                            (ahora.isoformat(timespec="seconds"), r["id"]))
            self.db.commit()
            return [f"Simulación: habría publicado la pieza {r['id']} de {self.cuenta} («{r['titulo'][:50]}»). No ha salido nada y la pieza sigue aprobada, lista para el modo real."]
        with self.db.transaccion() as c:                    # la pieza publicada y su aviso con el enlace: o quedan los dos o ninguno
            c.execute("UPDATE piezas SET estado='publicada', publicada=?, ig_media_id=?, ig_permalink=?, error=NULL WHERE id=?",
                      (ahora.isoformat(timespec="seconds"), res.media_id, res.permalink, r["id"]))
            retraso = ahora - datetime.fromisoformat(r["programada"])        # cuánto tarde salió (una hora de José con el router parado)
            if self.estilo_panel:
                from ..textos_panel import aviso_publicada      # noqa: PLC0415 (solo en el estilo del panel)
                self._encolar(c, aviso_publicada(r["titulo"], res.permalink, int(retraso.total_seconds() // 60)))
            else:
                self._encolar(c, texto_publicada(r["id"], self.cuenta, r["titulo"], res.permalink, retraso))
        return []

    def _fallo(self, r: sqlite3.Row, exc, ahora: datetime) -> list[str]:
        n = r["intentos"] + 1
        if getattr(exc, "bloquea", False):
            hasta = (ahora.astimezone(timezone.utc) + timedelta(hours=HORAS_BLOQUEO)).isoformat(timespec="seconds")
            with self.db.transaccion() as c:                # el bloqueo, la pieza y su aviso: todo o nada
                c.execute("INSERT INTO ajustes(k, v) VALUES('bloqueo', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                          (f"{hasta}|{exc.clase}|{exc}",))  # la clase manda: levantar_bloqueo solo levanta «alojamiento»
                c.execute("UPDATE piezas SET estado='aprobada', intentos=?, error=? WHERE id=?", (n, str(exc)[:300], r["id"]))
                self._encolar(c, texto_parada(self.cuenta, exc))
            return []
        if getattr(exc, "reintentable", False) and n < 9:
            espera = (2, 10, 30, 60, 120, 240, 360, 360)[n - 1]
            self.db.execute("UPDATE piezas SET estado='aprobada', intentos=?, error=?, proximo_intento=? WHERE id=?",
                            (n, str(exc)[:300], (ahora.astimezone(timezone.utc) + timedelta(minutes=espera)).isoformat(timespec="seconds"), r["id"]))
            self.db.commit()
            if n == 3:                       # varios fallos seguidos: el dueño se entera, aunque la pieza siga reintentándose
                return [f"La pieza {r['id']} de {self.cuenta} lleva {n} intentos sin salir: {exc} Se sigue intentando; si no "
                        "sale, pasará a error."]
            return []
        self._pasar_a_error(r, str(exc)[:300], texto_no_publicada(r["id"], self.cuenta, r["titulo"], exc, getattr(exc, "clase", "")), intentos=n)
        return []

    # ------------------------------------------------------------------ avisos de publicación (tabla propia, misma transacción)
    def _instante(self) -> str:
        return self.ahora().astimezone(timezone.utc).isoformat(timespec="seconds")

    def _encolar(self, c, texto: str, botones: list | None = None) -> None:
        """Un aviso de publicación para el administrador. Va dentro de la misma transacción que el cambio de estado de la pieza
        (`c` es la conexión de `self.db.transaccion()`): o quedan los dos o ninguno. Lo entrega el router, con reintentos. Con `botones`
        (filas de pares «texto», «dato del botón»), el aviso sale con esos botones."""
        c.execute("INSERT INTO avisos_publicacion(texto, creada, botones) VALUES(?, ?, ?)",
                  (texto, self._instante(), json.dumps(botones, ensure_ascii=False) if botones else None))

    def _pasar_a_error(self, r: sqlite3.Row, error: str, texto: str, intentos: int | None = None) -> None:
        botones = None
        if self.estilo_panel:
            from ..textos_panel import aviso_error          # noqa: PLC0415
            texto, botones = aviso_error(r["titulo"], error, r["id"])
        with self.db.transaccion() as c:
            if intentos is None:
                c.execute("UPDATE piezas SET estado='error', error=? WHERE id=?", (error, r["id"]))
            else:
                c.execute("UPDATE piezas SET estado='error', intentos=?, error=? WHERE id=?", (intentos, error, r["id"]))
            self._encolar(c, texto, botones)

    def _parar_por_validaciones(self) -> list[str]:
        """El almacén de validaciones no se puede leer: nada se da por validado, así que no sale nada. Un aviso urgente por incidente:
        la bandera `aviso_validaciones` no vuelve a avisar hasta que el almacén se lea otra vez (ver `_vigilar_validaciones`)."""
        if self._ajuste("aviso_validaciones") != "1":
            with self.db.transaccion() as c:
                c.execute("INSERT INTO ajustes(k, v) VALUES('aviso_validaciones', '1') ON CONFLICT(k) DO UPDATE SET v='1'")
                self._encolar(c, texto_validaciones(self.cuenta))
        return []

    def _vigilar_validaciones(self) -> None:
        """Cuando el almacén vuelve a leerse se quita la bandera: un incidente nuevo volverá a avisar."""
        if self._ajuste("aviso_validaciones") != "1":
            return
        try:
            C.cargar_banco(self.cuenta)
        except (V.ErrorValidaciones, OSError, ValueError):
            return
        self.db.execute("DELETE FROM ajustes WHERE k='aviso_validaciones'")
        self.db.commit()

    def avisos_publicacion_pendientes(self, limite: int = 20) -> list:
        """Los avisos de publicación que Telegram aún no ha confirmado y que toca enviar ya (los entrega el router)."""
        return self.db.execute("SELECT id, texto, botones FROM avisos_publicacion WHERE estado='pendiente' AND (reintentar_en IS NULL OR reintentar_en<=?) "
                               "ORDER BY id LIMIT ?", (self._instante(), limite)).fetchall()

    def aviso_publicacion_enviado(self, id: int) -> None:
        """Solo se llama cuando Telegram ha confirmado el envío."""
        self.db.execute("UPDATE avisos_publicacion SET estado='enviada', enviada=?, error=NULL WHERE id=?", (self._instante(), id))
        self.db.commit()

    def aviso_publicacion_fallido(self, id: int, motivo: str, definitivo: bool = False) -> None:
        """Un envío que no sale. Un error permanente de Telegram lo da por fallido; cualquier otro se reintenta con espera creciente."""
        with self.db.transaccion() as c:
            fila = c.execute("SELECT intentos FROM avisos_publicacion WHERE id=?", (id,)).fetchone()
            n = (fila["intentos"] if fila else 0) + 1
            if definitivo:
                c.execute("UPDATE avisos_publicacion SET estado='fallida', intentos=?, error=? WHERE id=?", (n, str(motivo)[:200], id))
            else:
                siguiente = (self.ahora().astimezone(timezone.utc) + timedelta(seconds=espera_reintento_s(n))).isoformat(timespec="seconds")
                c.execute("UPDATE avisos_publicacion SET intentos=?, error=?, reintentar_en=? WHERE id=?", (n, str(motivo)[:200], siguiente, id))

    # ------------------------------------------------------------------ textos de las órdenes
    def _contar(self) -> dict[str, int]:
        return {r["estado"]: r["n"] for r in self.db.execute("SELECT estado, COUNT(*) n FROM piezas GROUP BY estado")}

    def texto_estado(self) -> str:
        c = self._contar()
        simuladas = self.db.execute("SELECT COUNT(*) n FROM piezas WHERE simulada IS NOT NULL AND estado='aprobada'").fetchone()["n"]
        partes = [f"{c.get(e, 0)} {n}" for e, n in (("pendiente", "pendientes de revisar"), ("aprobada", "aprobadas en cola"), ("publicada", "publicadas"),
                                                        ("rechazada", "rechazadas"), ("error", "con error"))]
        if simuladas:
            partes.insert(2, f"{simuladas} de ellas ya simuladas")
        if c.get("publicando"):              # una pieza a medio publicar (p. ej. tras una caída): que no parezca que no hay nada
            partes.append(f"{c['publicando']} publicándose")
        sig = self.db.execute("SELECT id, programada FROM piezas WHERE estado='aprobada' AND programada IS NOT NULL ORDER BY programada LIMIT 1").fetchone()
        l = ["Piezas: " + ", ".join(partes) + "."]
        if sig:
            t = datetime.fromisoformat(sig["programada"]).astimezone(self.zona)
            l.append(f"Siguiente salida: {DIAS[t.weekday()]} {t.day}/{t.month} {t:%H:%M} (pieza {sig['id']}).")
        l.append(f"Publicación: modo {self.ig.get('modo', 'desactivado')}{' · EN PAUSA' if self.pausada() else ''}. Candado de aprobación: {self.politica}.")
        b = self._bloqueada(self.ahora())
        if b:
            l.append(f"Parada por un problema con Instagram: {b}")
        return "\n".join(l)

    def texto_cola(self) -> str:
        filas = self.db.execute("SELECT id, titulo, estado, programada FROM piezas WHERE estado IN ('pendiente','aprobada','publicando','error') ORDER BY (programada IS NULL), programada, id LIMIT 15").fetchall()
        if not filas:
            return "No hay nada en la cola."
        l = []
        for f in filas:
            cuando = ""
            if f["programada"]:
                t = datetime.fromisoformat(f["programada"]).astimezone(self.zona)
                cuando = f" · {DIAS[t.weekday()]} {t.day}/{t.month} {t:%H:%M}"
            l.append(f"{f['id']} · {f['titulo'][:40]} · {f['estado']}{cuando}")
        return "\n".join(l)

    def texto_instagram(self) -> str:
        from ..instagram import describir_alojamiento          # noqa: PLC0415  (instagram.py importa requests; solo aquí hace falta)
        info = self.ig.get("info") or {}
        alojamiento = self.ig.get("alojamiento") or {}         # lo que lee el router al arrancar, no la copia de al conectar
        linea_aloj = f"Imágenes y vídeos: {describir_alojamiento(alojamiento)}."
        if str(alojamiento.get("auth") or "").lower() == "gh":
            repo = alojamiento.get("repo") or "usuario/repo"
            linea_aloj += (" Se suben con la sesión de la CLI gh de este equipo, sin token. Si la sesión ha caducado o GitHub no "
                           "deja escribir, la cola se para y te avisa: «gh auth login» en el equipo y después «python -m router github "
                           f"{self.cuenta} {repo} --con-gh», que levanta el bloqueo si lo causó eso; luego reinicia el router.")
        if not info.get("conectado"):
            return ("Sin conectar. Para conectarlo, una sola vez, en el servidor:\n"
                    f"  python -m router instagram {self.cuenta}\n"
                    "Te pide el token sin que se vea en pantalla y comprueba la cuenta. Los pasos para sacar el token están en motor/GUIA_INSTAGRAM.md.\n"
                    f"Mientras tanto, el modo es «{self.ig.get('modo', 'desactivado')}» y lo aprobado espera en la cola.\n" + linea_aloj)
        l = [f"Conectado como @{info.get('username', '?')} ({info.get('tipo_cuenta', '?')}).", f"Modo: {self.ig.get('modo', 'desactivado')}."]
        if info.get("token_expira"):
            l.append(f"El token caduca el {info['token_expira'][:10]}; se renueva solo.")
        l.append(linea_aloj)
        return "\n".join(l)
