"""Base de datos del router: usuarios, cuentas, pertenencias, invitaciones y estado de la conversación.

Un solo fichero SQLite (`hub.sqlite`). Las piezas de cada cuenta NO viven aquí: cada cuenta tiene su propia base de datos
dentro de su adaptador (la de Campo sigue siendo la de Vivax auditori). El hub solo decide quién puede qué.

Las invitaciones se guardan con hash (sha256 del código): quien lea la base de datos no puede canjearlas.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .bd import Conexion

ROLES_CUENTA = ("lector", "aprobador")
CODIGO_CUENTA = "[a-z][0-9]{1,3}"      # los botones del router empiezan por esto; los de Campo (ap:, rj:...) no
MAX_INTENTOS = 5          # códigos de invitación fallidos por hora y usuario
VENTANA_INTENTOS = 3600

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios(user_id INTEGER PRIMARY KEY, nombre TEXT, rol TEXT NOT NULL CHECK(rol IN ('admin','suscriptor')),
                                    revocado INTEGER NOT NULL DEFAULT 0, alta TEXT);
CREATE TABLE IF NOT EXISTS cuentas(codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL, tipo TEXT NOT NULL, adaptador TEXT NOT NULL,
                                   activa INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pertenencias(user_id INTEGER NOT NULL, cuenta TEXT NOT NULL,
                                        rol_en_cuenta TEXT NOT NULL CHECK(rol_en_cuenta IN ('lector','aprobador')),
                                        PRIMARY KEY(user_id, cuenta));
CREATE TABLE IF NOT EXISTS invitaciones(hash_codigo TEXT PRIMARY KEY, cuenta TEXT NOT NULL, rol_en_cuenta TEXT NOT NULL,
                                        caduca TEXT NOT NULL, usos_max INTEGER NOT NULL DEFAULT 1, usados INTEGER NOT NULL DEFAULT 0,
                                        creada_por INTEGER, nota TEXT);
CREATE TABLE IF NOT EXISTS intentos(user_id INTEGER NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS esperando(user_id INTEGER PRIMARY KEY, cuenta TEXT NOT NULL, pieza INTEGER NOT NULL, tipo TEXT NOT NULL,
                                     caduca REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS auditoria(ts TEXT NOT NULL, user_id INTEGER, accion TEXT NOT NULL, detalle TEXT);
CREATE TABLE IF NOT EXISTS validacion_numeros(num INTEGER PRIMARY KEY AUTOINCREMENT, cuenta TEXT NOT NULL, hecho TEXT NOT NULL,
                                              huella TEXT NOT NULL, UNIQUE(cuenta, hecho, huella));
CREATE TABLE IF NOT EXISTS decisiones(id INTEGER PRIMARY KEY, cuenta TEXT NOT NULL, pregunta TEXT NOT NULL, opciones TEXT NOT NULL,
                                      estado TEXT NOT NULL DEFAULT 'pendiente'
                                          CHECK(estado IN ('pendiente','respondida','caducada','anulada')),
                                      creada TEXT NOT NULL, caduca TEXT NOT NULL, respondida_en TEXT, respuesta_clave TEXT,
                                      respuesta_texto TEXT, respuesta_via TEXT, mensaje_id INTEGER, origen TEXT NOT NULL DEFAULT 'asistente',
                                      defecto TEXT, texto_libre INTEGER NOT NULL DEFAULT 0, cerrada_tg INTEGER NOT NULL DEFAULT 0,
                                      respuesta_mensaje INTEGER, dudosa INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS decision_mensajes(mensaje_id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bandeja(id INTEGER PRIMARY KEY AUTOINCREMENT, recibida TEXT NOT NULL, texto TEXT NOT NULL,
                                   mensaje_id INTEGER, leida INTEGER NOT NULL DEFAULT 0, decision_id INTEGER);
CREATE TABLE IF NOT EXISTS decisiones_auditoria(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user_id INTEGER,
                                                decision_id INTEGER, accion TEXT NOT NULL, detalle TEXT);
CREATE TRIGGER IF NOT EXISTS decisiones_auditoria_no_cambia BEFORE UPDATE ON decisiones_auditoria
BEGIN SELECT RAISE(ABORT, 'la auditoría de decisiones solo se añade'); END;
CREATE TRIGGER IF NOT EXISTS decisiones_auditoria_no_borra BEFORE DELETE ON decisiones_auditoria
BEGIN SELECT RAISE(ABORT, 'la auditoría de decisiones solo se añade'); END;
CREATE TABLE IF NOT EXISTS avisos(id INTEGER PRIMARY KEY AUTOINCREMENT, cuenta TEXT, texto TEXT NOT NULL,
                                  nivel TEXT NOT NULL DEFAULT 'info' CHECK(nivel IN ('info','atencion','urgente')), clave TEXT,
                                  creada TEXT NOT NULL,
                                  estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN ('pendiente','enviada','silenciada','fallida')),
                                  entregada TEXT, mensaje_id INTEGER, intentos INTEGER NOT NULL DEFAULT 0, error TEXT,
                                  reintentar_en TEXT);
CREATE INDEX IF NOT EXISTS avisos_clave ON avisos(clave, id);
CREATE INDEX IF NOT EXISTS avisos_estado ON avisos(estado, id);
CREATE TABLE IF NOT EXISTS programacion_opciones(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, cuenta TEXT NOT NULL,
                                                 pieza INTEGER NOT NULL, estado_pieza TEXT NOT NULL, hora TEXT NOT NULL, etiqueta TEXT NOT NULL,
                                                 creada TEXT NOT NULL, caduca TEXT NOT NULL, usada INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS programacion_opciones_pieza ON programacion_opciones(user_id, cuenta, pieza);
"""

# ------------------------------------------------------------------ decisiones (preguntas a José con botones) y bandeja
MAX_ID_DECISION = 999_999_999           # el número de un botón tiene 9 cifras como mucho (FORMATO_BOTON de nucleo.py)
MAX_OPCIONES = 8
CADUCA_MINIMA_S, CADUCA_MAXIMA_S, CADUCA_POR_DEFECTO_S = 60, 7 * 86400, 24 * 3600
MAX_PREGUNTA, MAX_OPCION, MAX_RESPUESTA, MAX_TEXTO_BANDEJA = 300, 40, 500, 1000
MAX_PREGUNTA_LARGA = 3800          # preguntas con varias líneas (un calendario entero); si no caben en un mensaje de Telegram se parten en dos
MAX_BANDEJA = 100                       # mensajes que se guardan: al pasar de aquí se borran los más antiguos
LATIDO = "router_latido"                # cuándo dio su última vuelta el router (lo miran preguntar y respuesta)
LATIDO_MAXIMO_S = 180                   # más de esto sin latido: el router parece parado (un sondeo largo dura unos 40 s)
ESPERA_BASE_MS = 30_000                 # el router y la CLI escriben a la vez: esperar antes de dar «database is locked»
CLAVE_OPCION = re.compile(r"[a-z]{1,7}")    # la clave va en la acción del botón («d» + clave): corta y en minúsculas
CLAVE_TEXTO = "texto"                   # reservada: es la clave de la respuesta con texto libre (no tiene botón)
MADRID = ZoneInfo("Europe/Madrid")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ETIQUETA = re.compile(r"</?(?:b|i|u|s|em|strong|code|pre|a|p|span)>|<br\s*/?>", re.IGNORECASE)     # lista cerrada, sin atributos
# Lo que parece una credencial (token de Telegram, de Meta o de Instagram, de GitHub, clave de una API): no se guarda ni se enseña
_CREDENCIAL = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}|EAA[A-Za-z0-9]{20,}|IG[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{20,}"
                         r"|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}")
_ENLACE = re.compile(r"(?i)(?:https?://|www\.|t\.me/)\S*|\b[\w-]+\.(?:es|com|org|net|io|eu|info)(?:/\S*)?")
_OCULTAR_ACCESO = re.compile(r"(access_token=|Bearer\s+)\S+", re.IGNORECASE)

# ------------------------------------------------------------------ avisos a José sin botones (producción desatendida, publicación)
NIVELES_AVISO = ("info", "atencion", "urgente")
MAX_AVISOS_DIA = 20                     # avisos admitidos por día (hora de Madrid), urgentes incluidos
MAX_AVISOS_NORMALES_DIA = 15            # info y atención: los cinco restantes quedan para los urgentes
CADA_AVISO_POR_DEFECTO_S = 24 * 3600    # con clave y sin --cada: no se repite en el día
CADA_AVISO_MINIMO_S, CADA_AVISO_MAXIMO_S = 60, 7 * 86400
MAX_TEXTO_AVISO = 600
PREFIJO_SIN_CUPO = "produccion-resumen-"   # el resumen «Producción al día»: sale siempre y no cuenta en el cupo de las alarmas
CLAVE_AVISO = re.compile(r"[a-z0-9][a-z0-9:_.-]{0,79}")
AVISO_SILENCIO = {
    "normal": f"Hoy ya van {MAX_AVISOS_NORMALES_DIA} avisos normales: a partir de ahora solo te envío los urgentes y el resumen de "
              f"las 20:30, hasta {MAX_AVISOS_DIA} en total. Lo demás queda guardado en el router.",
    "total": f"Hoy ya van {MAX_AVISOS_DIA} avisos: no te envío más hasta mañana, salvo el resumen de las 20:30. Lo demás queda "
             "guardado en el router.",
}


def _hash(codigo: str) -> str:
    return hashlib.sha256(codigo.encode("utf-8")).hexdigest()


def limpiar_texto(texto, maximo: int, sin_enlaces: bool = False, sin_etiquetas: bool = False) -> str:
    """Quita caracteres de control, junta los espacios y corta a `maximo` con «…». Con `sin_etiquetas`, quita las etiquetas de formato
    de la lista (no cualquier «<…>»: «Peso < 70 kg» es texto). Con `sin_enlaces`, cambia los enlaces por «(enlace quitado)». Lo que
    escribe José no pasa por esas dos cosas: «a<b y c>d» llega tal cual."""
    t = _CONTROL.sub(" ", str(texto))
    if sin_etiquetas:
        t = _ETIQUETA.sub(" ", t)
    if sin_enlaces:
        t = _ENLACE.sub("(enlace quitado)", t)
    t = " ".join(t.split())
    return t if len(t) <= maximo else t[:maximo - 1].rstrip() + "…"


def limpiar_pregunta(texto, larga: bool = False) -> str:
    """Con `larga`, la pregunta conserva sus líneas (hasta MAX_PREGUNTA_LARGA caracteres); si no, una sola línea de hasta MAX_PREGUNTA."""
    if larga:
        lineas, hueco = [], False
        for linea in str(texto).splitlines():
            t = limpiar_texto(linea, MAX_PREGUNTA_LARGA, sin_enlaces=True, sin_etiquetas=True)
            if not t:
                hueco = bool(lineas)                      # como mucho una línea en blanco seguida, y nunca al principio
                continue
            if hueco:
                lineas.append("")
            hueco = False
            lineas.append(t)
        t = "\n".join(lineas)
        if len(t) > MAX_PREGUNTA_LARGA:
            corte = t.rfind("\n", 0, MAX_PREGUNTA_LARGA - 2)
            t = t[:corte if corte > 0 else MAX_PREGUNTA_LARGA - 2].rstrip() + "\n…"
    else:
        t = limpiar_texto(texto, MAX_PREGUNTA, sin_enlaces=True, sin_etiquetas=True)
    if not t:
        raise ValueError("la pregunta está vacía")
    return t


def limpiar_opcion(texto) -> str:
    t = limpiar_texto(texto, MAX_OPCION, sin_enlaces=True, sin_etiquetas=True)
    if not t:
        raise ValueError("una opción está vacía")
    return t


def limpiar_respuesta(texto) -> str:
    """La respuesta escrita por José: si pasa del máximo se rechaza entera; no se guarda cortada a medias."""
    t = limpiar_texto(texto, 10 ** 6)
    if not t:
        raise ValueError("la respuesta está vacía")
    if len(t) > MAX_RESPUESTA:
        raise ValueError(f"la respuesta pasa de {MAX_RESPUESTA} caracteres")
    return t


def parece_credencial(texto) -> bool:
    """Un token o una clave pegados por error en el chat: no se guardan ni se enseñan (ver ROUTER_TELEGRAM.md)."""
    return bool(_CREDENCIAL.search(str(texto)))


def hora_local(iso: str) -> datetime:
    """Una fecha guardada en UTC («AAAA-MM-DDTHH:MM:SSZ») como hora de Madrid."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MADRID)


def desde_iso(iso: str) -> float:
    """Una fecha guardada en UTC («AAAA-MM-DDTHH:MM:SSZ») como segundos desde 1970."""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def inicio_del_dia(t: float) -> str:
    """La medianoche de Madrid del día de ese instante, en UTC y con el formato de las fechas de la base."""
    medianoche = datetime.fromtimestamp(t, MADRID).replace(hour=0, minute=0, second=0, microsecond=0)
    return medianoche.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dia_madrid(t: float) -> str:
    return datetime.fromtimestamp(t, MADRID).strftime("%Y-%m-%d")


def ocultar_credenciales(texto) -> str:
    """Lo que parece un token o una clave se cambia por «[credencial oculta]»: un aviso nunca lleva secretos (como en las decisiones)."""
    t = _OCULTAR_ACCESO.sub(r"\1[credencial oculta]", str(texto))
    return _CREDENCIAL.sub("[credencial oculta]", t)


def limpiar_aviso(texto) -> str:
    """El texto de un aviso: sin credenciales, sin enlaces, sin caracteres de control y cortado a MAX_TEXTO_AVISO."""
    t = limpiar_texto(ocultar_credenciales(texto), MAX_TEXTO_AVISO, sin_enlaces=True, sin_etiquetas=True)
    if not t:
        raise ValueError("el aviso está vacío")
    return t


def espera_reintento_s(intentos: int) -> int:
    """Lo que se espera antes de volver a intentar un aviso que no ha salido: 30 s, luego el doble cada vez, hasta 15 min."""
    return min(30 * 2 ** max(intentos - 1, 0), 15 * 60)


# ------------------------------------------------------------------ horas de salida que escribe José (programar desde el bot)
# Todo se compara y se suma en UTC: dentro de Europe/Madrid, Python ignora «fold» al restar o comparar, y el cambio de hora de octubre
# (25 de octubre de 2026, de las 03:00 a las 02:00) haría mal las cuentas. Madrid solo sirve para construir la hora de pared y mostrarla.
UTC = timezone.utc
MINIMO_PROGRAMAR = timedelta(minutes=2)          # una hora programada queda al menos dos minutos en el futuro
MAXIMO_PROGRAMAR = timedelta(days=30)            # y como mucho dentro de treinta días
OPCION_CADUCA_S = 15 * 60                        # las opciones del selector caducan a los 15 minutos
ESPERA_HORA_S = 10 * 60                          # «Escribir otra hora»: el siguiente texto cuenta durante 10 minutos
FIN_ESPERA_S = 60 * 60                           # una espera de hora caducada o cancelada se recuerda una hora (para decir que no se cambió nada)
PREGUNTA_AHORA_S = 2 * 60                        # el «Sí» de «¿Publicar ahora?» vale dos minutos (y solo para quien lo pidió)
EJEMPLOS_HORA = ("18:30", "mañana a las 9:30", "dentro de 45 minutos")
DIAS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES_ABREV_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_DIAS_SEMANA = {"lunes": 0, "lun": 0, "martes": 1, "mar": 1, "miercoles": 2, "mie": 2, "jueves": 3, "jue": 3, "viernes": 4, "vie": 4,
                "sabado": 5, "sab": 5, "domingo": 6, "dom": 6}
_MESES = {"enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3, "abril": 4, "abr": 4, "mayo": 5, "may": 5,
          "junio": 6, "jun": 6, "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9,
          "octubre": 10, "oct": 10, "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12}
_NUMEROS = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "diez": 10, "quince": 15, "veinte": 20, "treinta": 30}
_MESES_RE = "|".join(sorted(_MESES, key=len, reverse=True))
_DIAS_RE = "pasado manana|manana|hoy|lunes|martes|miercoles|jueves|viernes|sabado|domingo|lun|mar|mie|jue|vie|sab|dom"
_R_MEDIA_HORA = re.compile(r"(?:en|dentro de) media hora")
_R_RELATIVA = re.compile(r"(?:en|dentro de) (?:(?P<n>[0-9]{1,3})|(?P<w>un|una|dos|tres|cuatro|cinco|diez|quince|veinte|treinta)) "
                         r"(?P<u>minutos?|min|horas?|h)")
_R_FRANJA = re.compile(r"(?:de la|por la|de|por|esta|este) (?P<f>manana|tarde|noche|madrugada)(?= |$)")
_R_CORTESIA = re.compile(r"\b(?:por favor|porfa)\b")
_R_FECHA_NUM = re.compile(r"(?:el )?(?P<d>[0-9]{1,2})/(?P<m>[0-9]{1,2})(?:/(?P<y>[0-9]{4}|[0-9]{2}))?(?= |$)")
_R_FECHA_TXT = re.compile(rf"(?:el )?(?P<d>[0-9]{{1,2}}) (?:de )?(?P<m>{_MESES_RE})(?= |$)(?: (?:de )?(?P<y>[0-9]{{4}}))?")
_R_DIA_INI = re.compile(rf"(?:el |este )?(?P<dia>{_DIAS_RE})(?= |$)")
_R_DIA_FIN = re.compile(rf"(?:^| )(?:el )?(?P<dia>{_DIAS_RE})$")
_R_PREFIJO_HORA = re.compile(r"^(?:sobre las|hacia las|a las|a la|las|a) ")
_R_HORA_24 = re.compile(r"(?P<h>[0-9]{1,2})[:.](?P<m>[0-9]{2})h?")
_R_HORA_H = re.compile(r"(?P<h>[0-9]{1,2})h(?P<m>[0-9]{2})?")
_R_HORA_SOLA = re.compile(r"(?P<h>[0-9]{1,2})(?: y (?P<med>media|cuarto))?h?")


class ErrorHora(ValueError):
    """Una hora que no se puede programar. `motivo` dice por qué, en castellano, sin el texto que escribió José (puede ser una
    credencial pegada por error). Quien lo recibe añade los ejemplos."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class HoraAnalizada:
    instante: datetime                  # en UTC, con zona
    dia_asumido: str | None             # «mañana» si la hora sin día ya había pasado (se dice en la confirmación)
    repetida: bool                      # su hora de pared se repite ese día (cambio de hora de octubre)
    segunda: bool = False               # y este instante es la segunda vez (ya con horario de invierno); la primera es la de verano
    noche_de: date | None = None        # «12 de la noche» con día: la medianoche que cierra ese día (sale a las 00:00 del día siguiente)


def _sin_tildes(texto) -> str:
    """Minúsculas, sin tildes ni signos de pregunta, con los espacios juntos: «¿Mañana a las 9:30?» → «manana a las 9:30»."""
    t = unicodedata.normalize("NFKD", str(texto).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[¿¡?!,;]+", " ", t)
    return " ".join(t.split()).strip(" .")


def _validar_rango(inst: datetime, ahora_utc: datetime, dia: date | None = None, hoy: date | None = None) -> None:
    """Dos minutos como mínimo. Para «en 2 horas», treinta días de reloj; para una fecha de calendario (dia y hoy), hasta el mismo día
    del mes siguiente en el calendario, así «09/11 10:00» no falla por la hora de más del cambio de octubre."""
    if inst <= ahora_utc:
        raise ErrorHora("Esa hora ya ha pasado")
    if inst < ahora_utc + MINIMO_PROGRAMAR:
        raise ErrorHora("Eso queda a menos de dos minutos")
    if dia is None:
        if inst > ahora_utc + MAXIMO_PROGRAMAR:
            raise ErrorHora("Como mucho se puede programar dentro de 30 días")
    elif dia > hoy + timedelta(days=30):
        raise ErrorHora("Como mucho se puede programar dentro de 30 días")


def _repeticion(inst: datetime) -> tuple[bool, bool]:
    """(la hora de pared de este instante se repite ese día, y es la segunda vez). Solo mira la zona de Madrid."""
    loc = inst.astimezone(MADRID)
    repetida = loc.replace(fold=0).utcoffset() != loc.replace(fold=1).utcoffset()
    return repetida, bool(loc.fold) and repetida


repeticion_hora = _repeticion          # la usa el router para decir «primera vez» o «segunda vez» en la confirmación


def _instante(d: date, h: int, m: int) -> tuple[datetime, bool]:
    """Un día y una hora de pared de Madrid, en UTC. Devuelve (instante, repetida). La hora que se repite (cambio de octubre) toma la
    primera vez, con horario de verano (fold=0); una hora que no existe ese día (cambio de marzo) es un error."""
    local = datetime(d.year, d.month, d.day, h, m, tzinfo=MADRID)
    inst = local.astimezone(UTC)
    vuelta = inst.astimezone(MADRID)
    if (vuelta.year, vuelta.month, vuelta.day, vuelta.hour, vuelta.minute) != (d.year, d.month, d.day, h, m):
        raise ErrorHora("Esa hora no existe ese día porque el reloj cambia de hora")
    return inst, local.utcoffset() != local.replace(fold=1).utcoffset()


def _hora_escrita(t: str) -> tuple[int, int, bool]:
    """(hora, minuto, escrita_en_24_horas). Lo que está en 24 horas: con «0» delante, 13 o más, o 0."""
    if t == "mediodia":
        return 12, 0, True
    if t == "medianoche":
        return 0, 0, True
    m = _R_HORA_24.fullmatch(t) or _R_HORA_H.fullmatch(t)
    if m is not None:
        cifras, mi = m.group("h"), int(m.group("m") or 0)
    else:
        m = _R_HORA_SOLA.fullmatch(t)
        if m is None:
            raise ErrorHora("No la entiendo")
        cifras = m.group("h")
        mi = {"media": 30, "cuarto": 15}.get(m.group("med") or "", 0)       # «a las 9 y media», «a las 9 y cuarto»
    h = int(cifras)
    if h > 23 or mi > 59:
        raise ErrorHora("Esa hora no existe")
    return h, mi, (len(cifras) == 2 and cifras.startswith("0")) or h >= 13 or h == 0


def _hora_del_dia(h: int, mi: int, franja: str | None, en_24: bool) -> tuple[int, int]:
    """La hora de pared. Sin franja: 24 horas tal cual; 8 a 11 es de la mañana; 12, mediodía; de 1 a 7 es ambigua (ver ErrorHora)."""
    if franja is None:
        if en_24 or h == 12 or 8 <= h <= 11:
            return h, mi
        raise ErrorHora(f"No sé si {h}:{mi:02d} es de madrugada o de la tarde")
    if franja == "manana":
        return h, mi
    if franja == "tarde":
        return (h + 12 if 1 <= h <= 11 else h), mi
    if franja == "noche":
        if h in (0, 12):
            return 0, mi
        return (h + 12 if 6 <= h <= 11 else h), mi
    return (0 if h == 12 else h), mi                           # madrugada


def _relativa(ahora_utc: datetime, delta: timedelta) -> HoraAnalizada:
    inst = ahora_utc + delta
    if inst.second or inst.microsecond:                          # se redondea hacia arriba al minuto: nunca queda antes de lo dicho
        inst = inst.replace(second=0, microsecond=0) + timedelta(minutes=1)
    _validar_rango(inst, ahora_utc)
    rep, segunda = _repeticion(inst)
    return HoraAnalizada(inst, None, rep, segunda)


def _absoluta(t: str, ahora_utc: datetime) -> HoraAnalizada:
    hoy = ahora_utc.astimezone(MADRID).date()
    franja = None
    m = _R_FRANJA.search(t)                                      # antes del día: «mañana por la mañana» no es el día «mañana» dos veces
    if m:
        franja = m.group("f")
        t = " ".join((t[:m.start()] + " " + t[m.end():]).split())
    fecha = dia = None
    m = _R_FECHA_NUM.match(t) or _R_FECHA_TXT.match(t)
    if m:
        anio = m.group("y")
        anio = (2000 + int(anio) if len(anio) == 2 else int(anio)) if anio else None
        mes = int(m.group("m")) if m.group("m").isdigit() else _MESES[m.group("m")]
        fecha = (int(m.group("d")), mes, anio)
        t = t[m.end():].strip()
    else:
        m = _R_DIA_INI.match(t)
        if m:
            dia = m.group("dia")
            t = t[m.end():].strip()
        else:
            m = _R_DIA_FIN.search(t)
            if m:
                dia = m.group("dia")
                t = t[:m.start()].strip()
    t = _R_PREFIJO_HORA.sub("", t).strip()
    if not t:
        raise ErrorHora("Falta la hora")
    h, mi, en_24 = _hora_escrita(t)
    # «12 de la noche» y «medianoche» son la medianoche que CIERRA el día: con un día dicho, sale a las 00:00 del día siguiente
    # («el sábado a las 12 de la noche» = domingo 00:00). Sin día, la regla de siempre: la siguiente medianoche que aún esté por delante.
    cierra_dia = t == "medianoche" or (franja == "noche" and h == 12)
    h, mi = _hora_del_dia(h, mi, franja, en_24)
    if fecha is None and dia is None:                            # hora sin día: hoy si aún queda margen, si no, mañana
        inst, rep = _instante(hoy, h, mi)
        dia_asumido = None
        if inst < ahora_utc + MINIMO_PROGRAMAR:
            inst, rep = _instante(hoy + timedelta(days=1), h, mi)
            dia_asumido = "mañana"
        _validar_rango(inst, ahora_utc)
        return HoraAnalizada(inst, dia_asumido, rep, _repeticion(inst)[1])
    desplaza = timedelta(days=1 if cierra_dia else 0)
    if fecha is not None:
        dia_n, mes, anio = fecha
        try:
            dia_fecha = date(anio or hoy.year, mes, dia_n)
        except ValueError:
            raise ErrorHora("Esa fecha no existe") from None
        if dia_fecha < hoy:
            raise ErrorHora("Ese día ya ha pasado")
    elif dia in ("hoy", "manana", "pasado manana"):
        dia_fecha = hoy + timedelta(days={"hoy": 0, "manana": 1, "pasado manana": 2}[dia])
    else:                                                        # un día de la semana: el próximo que aún esté por delante
        dia_fecha = hoy + timedelta(days=(_DIAS_SEMANA[dia] - hoy.weekday()) % 7)
        if _instante(dia_fecha + desplaza, h, mi)[0] < ahora_utc + MINIMO_PROGRAMAR:
            dia_fecha += timedelta(days=7)
    inst, rep = _instante(dia_fecha + desplaza, h, mi)
    _validar_rango(inst, ahora_utc, dia=dia_fecha + desplaza, hoy=hoy)
    return HoraAnalizada(inst, None, rep, _repeticion(inst)[1], noche_de=dia_fecha if desplaza else None)


def analizar_hora(texto, ahora: datetime) -> HoraAnalizada:
    """Lo que escribe José como hora de salida: «18:30», «18.30», «18h», «a las 9», «a las 9 de la noche», «hoy 20:00», «mañana 9»,
    «mañana a las 9:30», «pasado mañana 12:00», «el sábado 12», «sábado a las 10:15», «15/10 10:00», «15 de octubre a las 10»,
    «en 2 horas», «dentro de 45 minutos». Lanza ErrorHora con el motivo si no se entiende, es ambigua o no se puede programar.
    Reglas: hora sin día = hoy si quedan dos minutos o más, si no mañana; un «a las N» de 1 a 7 sin «0» delante ni franja es
    ambiguo; «el sábado 12» es sábado a las 12:00; el día de la semana es el próximo que esté por delante; mínimo dos minutos y
    máximo treinta días. Ver ROUTER_TELEGRAM.md, «Programar desde el bot»."""
    if ahora.tzinfo is None:
        raise ValueError("la hora actual tiene que llevar zona horaria")
    ahora_utc = ahora.astimezone(UTC)
    t = _sin_tildes(texto)
    t = " ".join(_R_CORTESIA.sub(" ", t).split())            # «por favor» y «porfa» no cambian la hora
    if not t or len(t) > 60:
        raise ErrorHora("No la entiendo")
    if _R_MEDIA_HORA.fullmatch(t):
        return _relativa(ahora_utc, timedelta(minutes=30))
    m = _R_RELATIVA.fullmatch(t)
    if m:
        cantidad = int(m.group("n")) if m.group("n") else _NUMEROS[m.group("w")]
        unidad = timedelta(hours=1) if m.group("u").startswith("h") else timedelta(minutes=1)
        return _relativa(ahora_utc, cantidad * unidad)
    return _absoluta(t, ahora_utc)


def fecha_hora_es(instante: datetime) -> str:
    """«sábado 10 oct, 18:30»: una hora en Madrid, para leerla en el móvil."""
    loc = instante.astimezone(MADRID)
    return f"{DIAS_ES[loc.weekday()]} {loc.day} {MESES_ABREV_ES[loc.month - 1]}, {loc:%H:%M}"


def fecha_corta_es(dia: date) -> str:
    """«sábado 10 oct»: un día de calendario en castellano (para decir qué noche es)."""
    return f"{DIAS_ES[dia.weekday()]} {dia.day} {MESES_ABREV_ES[dia.month - 1]}"


def dia_corto_es(instante: datetime, ahora: datetime) -> str:
    """«Hoy», «Mañana» o el día de la semana con su número, según la fecha de Madrid."""
    dif = (instante.astimezone(MADRID).date() - ahora.astimezone(MADRID).date()).days
    if dif == 0:
        return "Hoy"
    if dif == 1:
        return "Mañana"
    loc = instante.astimezone(MADRID)
    return f"{DIAS_ES[loc.weekday()].capitalize()} {loc.day}"


class Hub:
    def __init__(self, ruta: Path | str, ahora=time.time):
        self.ahora = ahora
        self.db = Conexion(ruta)
        self.db.execute(f"PRAGMA busy_timeout = {ESPERA_BASE_MS}")       # antes del esquema: también escribe
        self.db.executescript(ESQUEMA)
        self._migrar()
        self.db.commit()

    def _migrar(self) -> None:
        """Las columnas nuevas de «decisiones» se añaden a las bases creadas antes, sin tocar lo que hay (las tablas nuevas ya las crea
        ESQUEMA). No hay índices únicos sobre la bandeja: una base con repetidos no debe impedir arrancar."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(decisiones)").fetchall()}
        for nombre, tipo in (("respuesta_mensaje", "INTEGER"), ("dudosa", "INTEGER NOT NULL DEFAULT 0")):
            if nombre not in cols:
                self.db.execute(f"ALTER TABLE decisiones ADD COLUMN {nombre} {tipo}")
        cols_avisos = {r["name"] for r in self.db.execute("PRAGMA table_info(avisos)").fetchall()}
        if "reintentar_en" not in cols_avisos:              # una base con avisos de antes: la columna nueva, sin tocar lo que hay
            self.db.execute("ALTER TABLE avisos ADD COLUMN reintentar_en TEXT")
        cols_espera = {r["name"] for r in self.db.execute("PRAGMA table_info(esperando)").fetchall()}
        if "mensaje" not in cols_espera:                    # el mensaje que pide la hora (para editarlo al recibirla)
            self.db.execute("ALTER TABLE esperando ADD COLUMN mensaje INTEGER")

    # ------------------------------------------------------------------ tiempo y auditoría
    def _iso(self, t: float | None = None) -> str:
        return datetime.fromtimestamp(self.ahora() if t is None else t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def auditar(self, user_id: int | None, accion: str, detalle: str = "") -> None:
        self.db.execute("INSERT INTO auditoria(ts, user_id, accion, detalle) VALUES(?,?,?,?)", (self._iso(), user_id, accion, detalle[:300]))
        self.db.commit()

    # ------------------------------------------------------------------ cuentas y usuarios
    def registrar_cuenta(self, codigo: str, nombre: str, tipo: str, adaptador: str, activa: bool = True) -> None:
        if not re.fullmatch(CODIGO_CUENTA, codigo):
            raise ValueError(f"código de cuenta no válido: {codigo!r} (una letra y de una a tres cifras, como n1)")
        self.db.execute("INSERT INTO cuentas(codigo, nombre, tipo, adaptador, activa) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(codigo) DO UPDATE SET nombre=excluded.nombre, tipo=excluded.tipo, adaptador=excluded.adaptador, activa=excluded.activa",
                        (codigo, nombre, tipo, adaptador, 1 if activa else 0))
        self.db.commit()

    def cuenta(self, codigo: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM cuentas WHERE codigo=? AND activa=1", (codigo,)).fetchone()

    def cuentas(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM cuentas WHERE activa=1 ORDER BY codigo").fetchall()

    def crear_admin(self, user_id: int, nombre: str = "admin") -> None:
        self.db.execute("INSERT INTO usuarios(user_id, nombre, rol, alta) VALUES(?,?,'admin',?) "
                        "ON CONFLICT(user_id) DO UPDATE SET rol='admin', revocado=0", (user_id, nombre, self._iso()))
        self.db.commit()

    def establecer_admin(self, user_id: int, nombre: str = "administrador") -> None:
        """El administrador es el del .env y solo uno: si cambia, el anterior pierde el acceso (hay que volver a invitarlo)."""
        self.db.execute("UPDATE usuarios SET revocado=1 WHERE rol='admin' AND user_id<>?", (user_id,))
        self.db.commit()
        self.crear_admin(user_id, nombre)

    def usuario(self, user_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM usuarios WHERE user_id=?", (user_id,)).fetchone()

    def activo(self, user_id: int) -> bool:
        u = self.usuario(user_id)
        return bool(u) and not u["revocado"]

    def es_admin(self, user_id: int) -> bool:
        u = self.usuario(user_id)
        return bool(u) and u["rol"] == "admin" and not u["revocado"]

    def usuarios(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM usuarios ORDER BY alta").fetchall()

    def rol_en(self, user_id: int, cuenta: str) -> str | None:
        """'admin' si es el administrador (pertenece a todas las cuentas), 'aprobador' o 'lector'; None si no pertenece."""
        if not self.activo(user_id) or not self.cuenta(cuenta):
            return None
        if self.es_admin(user_id):
            return "admin"
        r = self.db.execute("SELECT rol_en_cuenta FROM pertenencias WHERE user_id=? AND cuenta=?", (user_id, cuenta)).fetchone()
        return r["rol_en_cuenta"] if r else None

    def cuentas_de(self, user_id: int) -> list[str]:
        return [c["codigo"] for c in self.cuentas() if self.rol_en(user_id, c["codigo"])]

    def miembros(self, cuenta: str) -> list[tuple[int, str]]:
        """(user_id, rol) de quienes reciben las piezas de la cuenta: sus miembros activos y el administrador."""
        sal = {u["user_id"]: "admin" for u in self.usuarios() if u["rol"] == "admin" and not u["revocado"]}
        for r in self.db.execute("SELECT p.user_id, p.rol_en_cuenta FROM pertenencias p JOIN usuarios u USING(user_id) "
                                 "WHERE p.cuenta=? AND u.revocado=0", (cuenta,)).fetchall():
            sal.setdefault(r["user_id"], r["rol_en_cuenta"])
        return list(sal.items())

    def revocar(self, user_id: int) -> bool:
        if self.es_admin(user_id):
            return False                      # al administrador no se le revoca desde el chat
        c = self.db.execute("UPDATE usuarios SET revocado=1 WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM esperando WHERE user_id=?", (user_id,))
        self.db.commit()
        return c.rowcount > 0

    # ------------------------------------------------------------------ invitaciones
    def crear_invitacion(self, cuenta: str, rol_en_cuenta: str, creada_por: int, horas: int = 72, usos_max: int = 1, nota: str = "") -> str:
        if rol_en_cuenta not in ROLES_CUENTA:
            raise ValueError("rol de cuenta no válido")
        c = self.cuenta(cuenta)
        if not c:
            raise ValueError("la cuenta no existe")
        if c["adaptador"] == "campo":
            raise ValueError("la sección de Campo no admite invitados: es solo del administrador")
        codigo = secrets.token_urlsafe(16)    # 128 bits; 22 caracteres válidos en un enlace t.me/bot?start=
        caduca = self._iso(self.ahora() + horas * 3600)
        self.db.execute("INSERT INTO invitaciones(hash_codigo, cuenta, rol_en_cuenta, caduca, usos_max, creada_por, nota) VALUES(?,?,?,?,?,?,?)",
                        (_hash(codigo), cuenta, rol_en_cuenta, caduca, usos_max, creada_por, nota[:80]))
        self.db.commit()
        self.auditar(creada_por, "invitacion_creada", f"{cuenta}:{rol_en_cuenta}")
        return codigo

    def bloqueado(self, user_id: int) -> bool:
        limite = self.ahora() - VENTANA_INTENTOS
        n = self.db.execute("SELECT COUNT(*) FROM intentos WHERE user_id=? AND ts>?", (user_id, limite)).fetchone()[0]
        return n >= MAX_INTENTOS

    def canjear(self, codigo: str, user_id: int, nombre: str) -> tuple[bool, str]:
        """Canjea una invitación. Devuelve (True, cuenta) o (False, motivo). El motivo es el mismo para código malo, usado o caducado."""
        if self.bloqueado(user_id):
            self.auditar(user_id, "canje_bloqueado")
            return False, "bloqueado"
        inv = self.db.execute("SELECT * FROM invitaciones WHERE hash_codigo=?", (_hash(codigo),)).fetchone()
        valida = bool(inv) and inv["usados"] < inv["usos_max"] and inv["caduca"] > self._iso() and self.cuenta(inv["cuenta"]) is not None
        if not valida:
            self.db.execute("INSERT INTO intentos(user_id, ts) VALUES(?,?)", (user_id, self.ahora()))
            self.db.commit()
            self.auditar(user_id, "canje_fallido")
            return False, "invalida"
        u = self.usuario(user_id)
        if u and u["revocado"]:
            self.auditar(user_id, "canje_revocado")
            return False, "invalida"
        if not u:
            self.db.execute("INSERT INTO usuarios(user_id, nombre, rol, alta) VALUES(?,?,'suscriptor',?)", (user_id, nombre[:60], self._iso()))
        self.db.execute("INSERT INTO pertenencias(user_id, cuenta, rol_en_cuenta) VALUES(?,?,?) "
                        "ON CONFLICT(user_id, cuenta) DO UPDATE SET rol_en_cuenta=excluded.rol_en_cuenta", (user_id, inv["cuenta"], inv["rol_en_cuenta"]))
        self.db.execute("UPDATE invitaciones SET usados=usados+1 WHERE hash_codigo=?", (inv["hash_codigo"],))
        self.db.commit()
        self.auditar(user_id, "canje_ok", f'{inv["cuenta"]}:{inv["rol_en_cuenta"]}')
        return True, inv["cuenta"]

    # ------------------------------------------------------------------ conversación en curso (editar un pie)
    def esperar(self, user_id: int, cuenta: str, pieza: int, tipo: str, segundos: int = 600, mensaje: int | None = None,
                origen: str | None = None) -> None:
        """Pide un texto (o una pieza, para el pie) a esta persona. `origen` es la sección en la que estaba al pedirlo: el texto siguiente
        se acepta en esa sección o en la de la pieza, sin cambiar la sección activa (así las órdenes de Campo siguen llegando)."""
        self.db.execute("INSERT INTO esperando(user_id, cuenta, pieza, tipo, caduca, mensaje) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(user_id) DO UPDATE SET cuenta=excluded.cuenta, pieza=excluded.pieza, tipo=excluded.tipo, "
                        "caduca=excluded.caduca, mensaje=excluded.mensaje",
                        (user_id, cuenta, pieza, tipo, self.ahora() + segundos, mensaje))
        if origen is None:
            self.db.execute("DELETE FROM meta WHERE k=?", (f"origen_espera:{user_id}",))
        else:
            self.db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (f"origen_espera:{user_id}", origen))
        self.db.execute("DELETE FROM meta WHERE k=?", (f"fin_espera:{user_id}",))     # una espera nueva borra el recuerdo de la anterior
        self.db.commit()

    def esperando(self, user_id: int) -> sqlite3.Row | None:
        r = self.db.execute("SELECT * FROM esperando WHERE user_id=?", (user_id,)).fetchone()
        if r and r["caduca"] < self.ahora():
            origen = self.origen_espera(user_id)
            self.db.execute("DELETE FROM esperando WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM meta WHERE k=?", (f"origen_espera:{user_id}",))
            if r["tipo"] == "hora":                                      # se recuerda: el texto siguiente dice que no se cambió nada
                self._recordar_fin(user_id, r["cuenta"], r["pieza"], "caducada", origen)
            self.db.commit()
            return None
        return r

    def dejar_de_esperar(self, user_id: int, motivo: str | None = None) -> None:
        """Cierra la espera. Con `motivo` («cancelada»): si era de una hora, queda anotada para decir después que no se cambió nada.
        Sin motivo (la hora se aplicó), se borra cualquier recuerdo de una espera anterior."""
        r = self.db.execute("SELECT * FROM esperando WHERE user_id=?", (user_id,)).fetchone()
        if motivo is None:
            self.db.execute("DELETE FROM meta WHERE k=?", (f"fin_espera:{user_id}",))
        elif r is not None and r["tipo"] == "hora":
            self._recordar_fin(user_id, r["cuenta"], r["pieza"], motivo, self.origen_espera(user_id))
        self.db.execute("DELETE FROM esperando WHERE user_id=?", (user_id,))
        self.db.execute("DELETE FROM meta WHERE k=?", (f"peticion:{user_id}",))
        self.db.execute("DELETE FROM meta WHERE k=?", (f"origen_espera:{user_id}",))
        self.db.commit()

    def _recordar_fin(self, user_id: int, cuenta: str, pieza: int, motivo: str, origen: str | None) -> None:
        valor = json.dumps({"cuenta": cuenta, "pieza": pieza, "motivo": motivo, "origen": origen, "ts": self.ahora()}, ensure_ascii=False)
        self.db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (f"fin_espera:{user_id}", valor))

    def fin_espera(self, user_id: int) -> dict | None:
        """La última espera de hora que caducó o se canceló (si fue hace menos de una hora): {cuenta, pieza, motivo, origen}. None si no hay."""
        valor = self.meta_leer(f"fin_espera:{user_id}")
        if not valor:
            return None
        try:
            d = json.loads(valor)
            ts = float(d["ts"])
        except (ValueError, KeyError, TypeError):
            return None
        return d if self.ahora() - ts <= FIN_ESPERA_S else None

    def origen_espera(self, user_id: int) -> str | None:
        """La sección en la que estaba la persona al pedir la espera (ver `esperar`)."""
        return self.meta_leer(f"origen_espera:{user_id}")

    def preguntar_ahora(self, user_id: int, cuenta: str, pieza: int) -> None:
        """«🚀 Ahora» pregunta «¿Publicar ahora?»: su «Sí» vale dos minutos, y solo para quien lo pidió."""
        self.meta_escribir(f"ahora:{user_id}:{cuenta}:{pieza}", str(self.ahora() + PREGUNTA_AHORA_S))

    def pregunta_ahora_viva(self, user_id: int, cuenta: str, pieza: int) -> bool:
        valor = self.meta_leer(f"ahora:{user_id}:{cuenta}:{pieza}")
        try:
            return valor is not None and float(valor) > self.ahora()
        except ValueError:
            return False

    def olvidar_ahora(self, user_id: int, cuenta: str, pieza: int) -> None:
        """Quita la pregunta «¿Publicar ahora?» (al usarla o al pulsar «No»): su «Sí» ya no vale."""
        self.db.execute("DELETE FROM meta WHERE k=?", (f"ahora:{user_id}:{cuenta}:{pieza}",))
        self.db.commit()

    def fijar_peticion(self, user_id: int, mensaje_id: int) -> None:
        """Guarda el mensaje del bot que pide la nota: solo sus respuestas cuentan como nota (las demás son de Campo)."""
        self.db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (f"peticion:{user_id}", str(mensaje_id)))
        self.db.commit()

    def peticion(self, user_id: int) -> int | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (f"peticion:{user_id}",)).fetchone()
        return int(r["v"]) if r and r["v"] and str(r["v"]).isdigit() else None

    # ------------------------------------------------------------------ sección activa de cada persona
    def cuenta_activa(self, user_id: int) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (f"activa:{user_id}",)).fetchone()
        return r["v"] if r else None

    def fijar_cuenta_activa(self, user_id: int, cuenta: str) -> None:
        self.db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (f"activa:{user_id}", cuenta))
        self.db.commit()

    # ------------------------------------------------------------------ desplazamiento de getUpdates
    def ultimo_update(self) -> int:
        r = self.db.execute("SELECT v FROM meta WHERE k='ultimo_update_id'").fetchone()
        return int(r["v"]) if r else 0

    def guardar_update(self, update_id: int) -> None:
        self.db.execute("INSERT INTO meta(k, v) VALUES('ultimo_update_id', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(update_id),))
        self.db.commit()

    # ------------------------------------------------------------------ números de los botones de validación
    def numero_validacion(self, cuenta: str, hecho: str, version: str) -> int:
        """Número estable de una versión de la tarjeta de un hecho (validaciones.version: el hecho y la decisión que muestra).
        Va en el botón: si el hecho o su decisión cambian, la tarjeta vieja tiene otro número. La columna se llama «huella» por
        compatibilidad con la tabla que ya existe; guarda la versión."""
        self.db.execute("INSERT OR IGNORE INTO validacion_numeros(cuenta, hecho, huella) VALUES(?,?,?)", (cuenta, hecho, version))
        self.db.commit()
        return self.db.execute("SELECT num FROM validacion_numeros WHERE cuenta=? AND hecho=? AND huella=?", (cuenta, hecho, version)).fetchone()["num"]

    def validacion_numero(self, num: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM validacion_numeros WHERE num=?", (num,)).fetchone()

    # ------------------------------------------------------------------ decisiones: preguntas a José con botones
    def iso(self, t: float | None = None) -> str:
        return self._iso(t)

    def admin_id(self) -> int | None:
        r = self.db.execute("SELECT user_id FROM usuarios WHERE rol='admin' AND revocado=0 ORDER BY alta LIMIT 1").fetchone()
        return int(r["user_id"]) if r else None

    def crear_decision(self, pregunta: str, opciones: list[tuple[str, str]], cuenta: str, caduca_s: int = CADUCA_POR_DEFECTO_S,
                       defecto: str | None = None, texto_libre: bool = False, origen: str = "asistente", larga: bool = False) -> int:
        """Crea una pregunta para José. Valida y limpia todo lo que irá a Telegram. Devuelve su número (el que va en los botones)."""
        if not re.fullmatch(CODIGO_CUENTA, str(cuenta or "")):
            raise ValueError(f"la cuenta {cuenta!r} no es un código válido")
        pregunta = limpiar_pregunta(pregunta, larga)
        limpias: list[dict] = []
        claves: set[str] = set()
        for clave, texto in opciones:
            k = str(clave).strip().lower()
            if not CLAVE_OPCION.fullmatch(k) or k == CLAVE_TEXTO:
                raise ValueError(f"la clave «{clave}» no vale: de 1 a 7 letras minúsculas, y no «{CLAVE_TEXTO}»")
            if k in claves:
                raise ValueError(f"la clave «{k}» está repetida")
            claves.add(k)
            limpias.append({"clave": k, "texto": limpiar_opcion(texto)})
        if len(limpias) > MAX_OPCIONES:
            raise ValueError(f"como mucho {MAX_OPCIONES} opciones")
        if len(limpias) < 2 and not texto_libre:
            raise ValueError("hacen falta al menos dos opciones (o --texto-libre)")
        if defecto is not None:
            defecto = str(defecto).strip().lower()
            if defecto not in claves:
                raise ValueError(f"la opción por defecto «{defecto}» no existe")
        if not CADUCA_MINIMA_S <= caduca_s <= CADUCA_MAXIMA_S:
            raise ValueError("la caducidad tiene que estar entre 1 minuto y 7 días")
        ahora = self.ahora()
        cur = self.db.execute(                       # el número y la inserción en una sola sentencia
            "INSERT INTO decisiones(id, cuenta, pregunta, opciones, estado, creada, caduca, defecto, texto_libre, origen) "
            "SELECT COALESCE(MAX(id), 0) + 1, ?, ?, ?, 'pendiente', ?, ?, ?, ?, ? FROM decisiones",
            (cuenta, pregunta, json.dumps(limpias, ensure_ascii=False), self.iso(ahora), self.iso(ahora + caduca_s), defecto,
             1 if texto_libre else 0, str(origen)[:30]))
        numero = cur.lastrowid
        if numero > MAX_ID_DECISION:
            self.db.execute("DELETE FROM decisiones WHERE id=?", (numero,))
            self.db.commit()
            raise ValueError("no quedan números de decisión libres")
        self.db.commit()
        self.auditar_decision(None, numero, "creada", f"{cuenta}; {origen}")
        return numero

    def decision(self, id: int):
        return self.db.execute("SELECT * FROM decisiones WHERE id=?", (id,)).fetchone()

    def decision_por_mensaje(self, mensaje_id: int):
        """La decisión a la que pertenece un mensaje del bot: el primero con el que se envió o cualquiera de sus copias."""
        return self.db.execute("SELECT d.* FROM decisiones d WHERE d.mensaje_id=? OR d.id IN "
                               "(SELECT decision_id FROM decision_mensajes WHERE mensaje_id=?) LIMIT 1", (mensaje_id, mensaje_id)).fetchone()

    def copias(self, id: int) -> list[int]:
        """Todos los mensajes con los que se ha enviado la decisión (tras un fallo de red puede haber más de uno)."""
        return [r[0] for r in self.db.execute("SELECT mensaje_id FROM decision_mensajes WHERE decision_id=? UNION "
                                              "SELECT mensaje_id FROM decisiones WHERE id=? AND mensaje_id IS NOT NULL ORDER BY 1",
                                              (id, id)).fetchall()]

    def registrar_copia(self, id: int, mensaje_id: int) -> None:
        """Apunta un mensaje con el que se envió la decisión. El primero queda también como su mensaje principal."""
        self.db.execute("INSERT OR IGNORE INTO decision_mensajes(mensaje_id, decision_id) VALUES(?,?)", (mensaje_id, id))
        self.db.execute("UPDATE decisiones SET mensaje_id=COALESCE(mensaje_id, ?) WHERE id=?", (mensaje_id, id))
        self.db.commit()

    def decisiones(self, estado: str | None = None, limite: int = 30) -> list:
        if estado:
            return self.db.execute("SELECT * FROM decisiones WHERE estado=? ORDER BY id DESC LIMIT ?", (estado, limite)).fetchall()
        return self.db.execute("SELECT * FROM decisiones ORDER BY id DESC LIMIT ?", (limite,)).fetchall()

    def ultimas_respondidas(self, limite: int = 5) -> list:
        return self.db.execute("SELECT * FROM decisiones WHERE estado='respondida' ORDER BY respondida_en DESC, id DESC LIMIT ?",
                               (limite,)).fetchall()

    def sin_entregar(self) -> list:
        """Pendientes que nunca llegaron a Telegram (sin número de mensaje). Lo ya entregado tiene número y no se vuelve a mandar."""
        return self.db.execute("SELECT * FROM decisiones WHERE estado='pendiente' AND mensaje_id IS NULL ORDER BY id").fetchall()

    def marcar_entregada(self, id: int, mensaje_id: int) -> None:
        self.registrar_copia(id, mensaje_id)

    def marcar_dudosa(self, id: int) -> None:
        """El último envío falló por red: puede que Telegram ya tenga el mensaje. Se enseña en /decisiones."""
        self.db.execute("UPDATE decisiones SET dudosa=1 WHERE id=?", (id,))
        self.db.commit()

    def abiertas_con_texto(self) -> list:
        """Las decisiones con texto libre que siguen pendientes, ya están en Telegram y no han caducado (de la más antigua a la última)."""
        return self.db.execute("SELECT * FROM decisiones WHERE estado='pendiente' AND texto_libre=1 AND mensaje_id IS NOT NULL "
                               "AND caduca>? ORDER BY id", (self.iso(),)).fetchall()

    def responder_decision(self, id: int, clave: str, texto: str, via: str, ahora: float | None = None,
                           respuesta_mensaje: int | None = None) -> bool:
        """Guarda la respuesta una sola vez: solo si sigue pendiente y sin caducar. True si la guardó esta llamada.
        `respuesta_mensaje` es el mensaje de José que la contiene (para no contarlo dos veces si el router lo recibe otra vez)."""
        t = self.iso(self.ahora() if ahora is None else ahora)
        c = self.db.execute("UPDATE decisiones SET estado='respondida', respuesta_clave=?, respuesta_texto=?, respuesta_via=?, respondida_en=?, "
                            "respuesta_mensaje=? WHERE id=? AND estado='pendiente' AND caduca>?", (clave, texto, via, t, respuesta_mensaje, id, t))
        self.db.commit()
        return c.rowcount == 1

    def mensaje_usado(self, mensaje_id: int | None) -> bool:
        """Un mensaje de José que ya se contó (como respuesta o en la bandeja). Si el router lo recibe otra vez tras una caída, no se cuenta
        dos veces."""
        if mensaje_id is None:
            return False
        return self.db.execute("SELECT 1 FROM decisiones WHERE respuesta_mensaje=? UNION ALL SELECT 1 FROM bandeja WHERE mensaje_id=? LIMIT 1",
                               (mensaje_id, mensaje_id)).fetchone() is not None

    def anular_decision(self, id: int) -> bool:
        c = self.db.execute("UPDATE decisiones SET estado='anulada' WHERE id=? AND estado='pendiente'", (id,))
        self.db.commit()
        if c.rowcount == 1:
            self.auditar_decision(None, id, "anulada", "cli")
        return c.rowcount == 1

    def cerrar_vencidas(self, ahora: float | None = None) -> list[int]:
        """Cierra las pendientes que ya caducaron: con su opción por defecto si la tienen, o como caducadas. Devuelve sus números."""
        t = self.iso(self.ahora() if ahora is None else ahora)
        cerradas: list[int] = []
        for d in self.db.execute("SELECT id, opciones, defecto FROM decisiones WHERE estado='pendiente' AND caduca<=? ORDER BY id",
                                 (t,)).fetchall():
            if d["defecto"]:
                texto = next((o["texto"] for o in json.loads(d["opciones"]) if o["clave"] == d["defecto"]), "")
                c = self.db.execute("UPDATE decisiones SET estado='respondida', respuesta_clave=?, respuesta_texto=?, respuesta_via='defecto', "
                                    "respondida_en=? WHERE id=? AND estado='pendiente'", (d["defecto"], texto, t, d["id"]))
                accion = "cerrada_por_defecto"
            else:
                c = self.db.execute("UPDATE decisiones SET estado='caducada' WHERE id=? AND estado='pendiente'", (d["id"],))
                accion = "caducada"
            self.db.commit()
            if c.rowcount == 1:
                cerradas.append(d["id"])
                self.auditar_decision(None, d["id"], accion, d["defecto"] or "")
        return cerradas

    def sin_sincronizar(self) -> list:
        """Decisiones ya cerradas cuyo mensaje de Telegram aún no se ha puesto al día (sin botones y con cómo acabó)."""
        return self.db.execute("SELECT * FROM decisiones WHERE cerrada_tg=0 AND estado<>'pendiente' ORDER BY id").fetchall()

    def marcar_cerrada_tg(self, id: int) -> None:
        self.db.execute("UPDATE decisiones SET cerrada_tg=1 WHERE id=?", (id,))
        self.db.commit()

    def auditar_decision(self, user_id: int | None, decision_id: int | None, accion: str, detalle: str = "") -> None:
        """Registro de decisiones: solo se añade (los disparadores de la tabla impiden cambiarlo o borrarlo)."""
        self.db.execute("INSERT INTO decisiones_auditoria(ts, user_id, decision_id, accion, detalle) VALUES(?,?,?,?,?)",
                        (self._iso(), user_id, decision_id, accion, str(detalle)[:300]))
        self.db.commit()

    # ------------------------------------------------------------------ bandeja: lo que José escribe y no es una orden
    def anotar_bandeja(self, texto: str, mensaje_id: int | None, decision_id: int | None = None) -> int:
        cur = self.db.execute("INSERT INTO bandeja(recibida, texto, mensaje_id, leida, decision_id) VALUES(?,?,?,0,?)",
                              (self._iso(), limpiar_texto(texto, MAX_TEXTO_BANDEJA), mensaje_id, decision_id))
        numero = cur.lastrowid
        self.db.execute("DELETE FROM bandeja WHERE id IN (SELECT id FROM bandeja ORDER BY id DESC LIMIT -1 OFFSET ?)", (MAX_BANDEJA,))
        self.db.commit()
        return numero

    def bandeja(self, desde: int | None = None, limite: int = 50) -> list:
        """Sin `desde`: los mensajes no leídos. Con `desde`: los de ese número en adelante, lean o no."""
        if desde is None:
            return self.db.execute("SELECT * FROM bandeja WHERE leida=0 ORDER BY id LIMIT ?", (limite,)).fetchall()
        return self.db.execute("SELECT * FROM bandeja WHERE id>=? ORDER BY id LIMIT ?", (desde, limite)).fetchall()

    def marcar_leidas(self, ids: list[int]) -> None:
        for i in ids:
            self.db.execute("UPDATE bandeja SET leida=1 WHERE id=?", (i,))
        self.db.commit()

    # ------------------------------------------------------------------ avisos: lo que el router envía a José sin botones
    def crear_aviso(self, texto, nivel: str = "info", clave: str | None = None, cuenta: str | None = None,
                    cada_s: int | None = None) -> tuple[str, int | None]:
        """Guarda un aviso para José. Devuelve («creada», número), («repetida», None) o («silenciada», número):
        - repetida: su clave ya se avisó hace menos de `cada_s` (24 h por defecto); no se guarda nada. Solo cuentan los avisos
          pendientes o enviados: uno silenciado o fallido no bloquea que se vuelva a avisar.
        - silenciada: el día ya tiene su cupo (15 avisos normales o 20 en total). Se guarda para el registro y no se envía; la primera
          vez de cada grupo y día se guarda además un aviso que lo dice. El resumen (clave con PREFIJO_SIN_CUPO) no cuenta en el
          cupo y siempre se admite.
        Todo va en una transacción con el cerrojo de escritura: el router, la línea de órdenes y la producción no se pisan."""
        if nivel not in NIVELES_AVISO:
            raise ValueError(f"el nivel {nivel!r} no vale: info, atencion o urgente")
        if cuenta is not None and not re.fullmatch(CODIGO_CUENTA, str(cuenta)):
            raise ValueError(f"la cuenta {cuenta!r} no es un código válido")
        if clave is not None:
            clave = str(clave).strip().lower()
            if not CLAVE_AVISO.fullmatch(clave):
                raise ValueError("la clave va en minúsculas, con letras, cifras, «-», «_», «.» o «:» (80 caracteres como máximo)")
        cada_s = CADA_AVISO_POR_DEFECTO_S if cada_s is None else int(cada_s)
        if not CADA_AVISO_MINIMO_S <= cada_s <= CADA_AVISO_MAXIMO_S:
            raise ValueError("el intervalo tiene que estar entre 1 minuto y 7 días")
        t = limpiar_aviso(texto)
        ahora = self.ahora()
        creada, inicio = self._iso(ahora), inicio_del_dia(ahora)
        admitidos = ("estado<>'silenciada' AND creada>=? AND (clave IS NULL OR (clave NOT LIKE 'silencio:%' AND clave NOT LIKE ?))")
        sin_cupo = f"{PREFIJO_SIN_CUPO}%"
        with self.db.transaccion() as c:
            if clave:
                ultimo = c.execute("SELECT creada FROM avisos WHERE clave=? AND estado IN ('pendiente','enviada') ORDER BY id DESC LIMIT 1",
                                   (clave,)).fetchone()
                if ultimo is not None and ahora - desde_iso(ultimo["creada"]) < cada_s:
                    return "repetida", None
            if clave and clave.startswith(PREFIJO_SIN_CUPO):             # el resumen: sale siempre, y no quita plazas a las alarmas
                cur = c.execute("INSERT INTO avisos(cuenta, texto, nivel, clave, creada) VALUES(?,?,?,?,?)", (cuenta, t, nivel, clave, creada))
                return "creada", cur.lastrowid
            total = c.execute(f"SELECT COUNT(*) FROM avisos WHERE {admitidos}", (inicio, sin_cupo)).fetchone()[0]
            normales = c.execute(f"SELECT COUNT(*) FROM avisos WHERE {admitidos} AND nivel<>'urgente'", (inicio, sin_cupo)).fetchone()[0]
            if total >= MAX_AVISOS_DIA or (nivel != "urgente" and normales >= MAX_AVISOS_NORMALES_DIA):
                grupo = "total" if total >= MAX_AVISOS_DIA else "normal"
                nota = f"silencio:{dia_madrid(ahora)}:{grupo}"
                if c.execute("SELECT 1 FROM avisos WHERE clave=? LIMIT 1", (nota,)).fetchone() is None:
                    c.execute("INSERT INTO avisos(cuenta, texto, nivel, clave, creada) VALUES(NULL,?,'info',?,?)",
                              (AVISO_SILENCIO[grupo], nota, creada))
                cur = c.execute("INSERT INTO avisos(cuenta, texto, nivel, clave, creada, estado) VALUES(?,?,?,?,?,'silenciada')",
                                (cuenta, t, nivel, clave, creada))
                return "silenciada", cur.lastrowid
            cur = c.execute("INSERT INTO avisos(cuenta, texto, nivel, clave, creada) VALUES(?,?,?,?,?)", (cuenta, t, nivel, clave, creada))
            return "creada", cur.lastrowid

    def aviso(self, id: int):
        return self.db.execute("SELECT * FROM avisos WHERE id=?", (id,)).fetchone()

    def avisos_pendientes(self, limite: int = 20) -> list:
        """Lo que hay que enviar ya: pendientes cuya espera (si fallaron antes) ya ha pasado."""
        return self.db.execute("SELECT * FROM avisos WHERE estado='pendiente' AND (reintentar_en IS NULL OR reintentar_en<=?) "
                               "ORDER BY id LIMIT ?", (self._iso(), limite)).fetchall()

    def aviso_enviado(self, id: int, mensaje_id: int) -> None:
        self.db.execute("UPDATE avisos SET estado='enviada', entregada=?, mensaje_id=? WHERE id=?", (self.iso(), mensaje_id, id))
        self.db.commit()

    def aviso_fallido(self, id: int, motivo: str, definitivo: bool = False) -> None:
        """Un envío que no sale. Un error permanente (Telegram no admite el chat) lo da por fallido en el acto. Cualquier otro fallo
        (red, Telegram caído, límite de envíos) deja el aviso pendiente y lo reintenta con espera creciente: no se pierde."""
        with self.db.transaccion() as c:
            fila = c.execute("SELECT intentos FROM avisos WHERE id=?", (id,)).fetchone()
            n = (fila["intentos"] if fila else 0) + 1
            if definitivo:
                c.execute("UPDATE avisos SET intentos=?, error=?, estado='fallida' WHERE id=?", (n, str(motivo)[:200], id))
            else:
                siguiente = self._iso(self.ahora() + espera_reintento_s(n))
                c.execute("UPDATE avisos SET intentos=?, error=?, reintentar_en=? WHERE id=?", (n, str(motivo)[:200], siguiente, id))

    # ------------------------------------------------------------------ opciones del selector «Programar» (cada una caduca a los 15 min)
    def crear_opciones(self, user_id: int, cuenta: str, pieza: int, estado_pieza: str, opciones: list[tuple[datetime, str]]) -> list[int]:
        """Guarda las horas que ofrece el selector de una pieza, una fila por hora. Las de la misma pieza y persona que aún valían dejan
        de valer: solo cuenta el último selector abierto. Devuelve los números (los que van en los botones). Lo caducado hace más de un
        día se borra aquí, para que la tabla no crezca."""
        ahora = self.ahora()
        ahora_s, caduca_s = self._iso(ahora), self._iso(ahora + OPCION_CADUCA_S)
        ids: list[int] = []
        with self.db.transaccion() as c:
            c.execute("UPDATE programacion_opciones SET caduca=? WHERE user_id=? AND cuenta=? AND pieza=? AND caduca>?",
                      (ahora_s, user_id, cuenta, pieza, ahora_s))
            for hora, etiqueta in opciones:
                cur = c.execute("INSERT INTO programacion_opciones(user_id, cuenta, pieza, estado_pieza, hora, etiqueta, creada, caduca) "
                                "VALUES(?,?,?,?,?,?,?,?)", (user_id, cuenta, pieza, estado_pieza, hora.astimezone(UTC).isoformat(timespec="seconds"),
                                                            etiqueta[:40], ahora_s, caduca_s))
                if cur.lastrowid > MAX_ID_DECISION:                 # el número va en el botón: tiene que caber en 9 cifras
                    raise ValueError("no quedan números de opción libres")
                ids.append(cur.lastrowid)
            c.execute("DELETE FROM programacion_opciones WHERE caduca<?", (self._iso(ahora - 86400),))
        return ids

    def opcion(self, id: int):
        return self.db.execute("SELECT * FROM programacion_opciones WHERE id=?", (id,)).fetchone()

    def usar_opcion(self, id: int) -> bool:
        """Marca una opción como usada. Solo lo hace si aún no se usó y no ha caducado; True si lo hizo esta llamada (una doble pulsación
        no programa dos veces)."""
        c = self.db.execute("UPDATE programacion_opciones SET usada=1 WHERE id=? AND usada=0 AND caduca>?", (id, self.iso()))
        self.db.commit()
        return c.rowcount == 1

    def liberar_opcion(self, id: int) -> None:
        """Deshace «usar_opcion» cuando la hora no se pudo aplicar (p. ej. el candado): así puede volver a elegirla."""
        self.db.execute("UPDATE programacion_opciones SET usada=0 WHERE id=?", (id,))
        self.db.commit()

    # ------------------------------------------------------------------ valores sueltos (p. ej. cuándo se avisó por última vez)
    def meta_leer(self, k: str) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None

    def meta_escribir(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        self.db.commit()
