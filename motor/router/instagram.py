"""Publicación en Instagram con la API oficial de Meta («API con inicio de sesión de Instagram», graph.instagram.com).

Lo usa el router para publicar en la cuenta principal de Vivax Nutrición. Parte del diseño de Vivax auditori
(social/vivax_social/publicador.py), con dos añadidos: vídeo (Reels) y un modo de simulación. Lo que se copia
es el orden de las operaciones, que es lo que evita publicar dos veces tras una caída.

Modos (`ConfigIG.modo`):
- `desactivado`: `publicar` lanza `ErrorInstagram("desactivado")` sin tocar nada.
- `simulacion`: valida ficheros, pie y límites en local y devuelve `Resultado("simulado")`. Sin red ni subidas.
- `real`: publica de verdad.
`comprobar` y `renovar_token` hablan con Meta en cualquier modo: son lecturas o mantenimiento, no publican.

Orden de `publicar` en modo real:
1. Validación local (ficheros, pie, límites). Ningún paso de red antes de esto.
2. Subida al alojamiento público (Meta descarga las imágenes él) y `guardar` con las direcciones.
3. Contenedores (hijos y padre en un carrusel) y `guardar` con su id ANTES de esperar a que estén listos.
4. `guardar` con la fase «publicando». Solo entonces se llama a `media_publish`, que nunca se repite a ciegas.
5. `guardar` con la fase «publicado» y el id del medio. El enlace permanente es opcional.

`estado` es un dict que guarda quien llama. Tiene que ser JSON (texto, números, listas y dicts) y tiene que
volver tal cual tras cualquier error: así la siguiente llamada retoma donde se quedó y no publica dos veces.

El token de acceso no sale en logs, mensajes, excepciones ni URLs. La única excepción es `renovar_token`: Meta
documenta el token en la query de `refresh_access_token`, así que va ahí, y `FiltroSecretos` lo quita de los logs
de urllib3. Los errores de red se registran solo por el nombre de su tipo, sin el texto (que trae la URL).

Alojamiento de las imágenes en GitHub: con `token` (GITHUB_TOKEN en router/.env) o con `auth: gh`, que usa la sesión
que ya tiene guardada la CLI `gh` del equipo y no guarda ningún token (ver «GitHub con la sesión de la CLI gh»).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

GRAPH = "https://graph.instagram.com"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
# raw.githubusercontent.com sirve el vídeo como application/octet-stream y Meta lo rechaza; jsDelivr lo sirve como video/mp4
# (comprobado el 9-oct-2026) y, fijado a la confirmación, nunca cambia de contenido.
JSDELIVR = "https://cdn.jsdelivr.net/gh"
AGENTE = "vivax-instagram"
MODOS = ("desactivado", "simulacion", "real")
FORMATOS = {"unica": "jpeg", "carrusel": "jpeg", "video": "mp4"}
MIB = 1024 * 1024
MAX_JPEG = 8 * MIB                 # Meta rechaza JPEG de más de 8 MiB
MAX_VIDEO = 100 * MIB
MAX_VIDEO_GITHUB = 20_000_000             # jsDelivr no sirve por defecto ficheros de GitHub de más de 20 MB (README de jsdelivr/jsdelivr)
MAX_PIE, MAX_HASHTAGS, MAX_MENCIONES = 2200, 5, 20          # Instagram limita a 5 los hashtags por pie (dic-2025); las piezas llevan 3 o 4
MAX_HOJAS, MAX_ALT = 10, 1000
VIDEO_ANCHO, VIDEO_ALTO = 1080, 1920      # 9:16 con 1080 de ancho
VIDEO_DURACION = (3, 90)                  # segundos
ESPERAS_S = (2, 10, 30)                   # tres reintentos con esperas crecientes
ESPERA_DUDA_S = 20                        # antes de volver a mirar un envío que no ha contestado
VENTANA_REENVIO_S = 300                   # un envío reciente no se repite hasta pasado este tiempo
TIMEOUT = (10, 60)
TIMEOUT_SUBIDA = (10, 300)
BLOQUEANTES = frozenset({"desactivado", "token", "permiso", "cuenta", "alojamiento"})   # para la cola hasta que el dueño actúe
REINTENTABLES = frozenset({"red", "tasa", "limite", "servidor", "alojamiento_red", "imagen_descarga",
                           "contenedor_no_listo", "contenedor_caducado", "ambiguo", "renovacion_pronto",
                           "local", "rechazo_github"})
TRANSITORIAS = frozenset({"red", "servidor", "tasa"})     # se reintentan dentro de la misma llamada
_EXT = {"jpeg": ".jpg", "mp4": ".mp4"}
_CONTENT_TYPE = {"jpeg": "image/jpeg", "mp4": "video/mp4"}
_SEGMENTO = re.compile(r"[A-Za-z0-9._-]+")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_RAMA = re.compile(r"[A-Za-z0-9._/-]+")
_VERSION = re.compile(r"v\d+\.\d+")

_TEXTOS = {
    "desactivado": "La publicación en Instagram está desactivada (instagram.modo = «desactivado»). Pásala a "
                   "«simulacion» para probar o a «real» cuando todo esté listo.",
    "token": "Instagram no acepta el token de acceso: ha caducado, lo han revocado o está mal copiado. Genera uno "
             "nuevo en el panel de Meta y vuelve a conectarlo con «python -m router instagram n1» (sin pegarlo en ningún chat) y reinicia el router.",
    "permiso": "Al token de acceso le falta un permiso. Hacen falta instagram_business_basic e "
               "instagram_business_content_publish: genera el token de nuevo con los dos marcados.",
    "cuenta": "Instagram ha restringido la cuenta o parte de su actividad. Mira si hay un aviso en la aplicación "
              "de Instagram. Mientras tanto no se publica.",
    "limite": "Se ha llegado al máximo de publicaciones que Instagram deja hacer por la API en 24 horas. Se "
              "volverá a intentar más tarde.",
    "tasa": "Instagram pide ir más despacio (demasiadas peticiones seguidas). Se volverá a intentar.",
    "red": "No hay conexión con Instagram. Comprueba la conexión del equipo; se volverá a intentar.",
    "servidor": "Instagram ha dado un error interno pasajero. Se volverá a intentar.",
    "imagen_descarga": "Instagram no ha podido descargar la imagen de su dirección pública. Si es un fallo pasajero "
                       "se volverá a intentar; si se repite, revisa que el alojamiento sea público.",
    "imagen_invalida": "Instagram rechaza la imagen: solo admite JPEG normal de hasta 8 MiB, con proporción de 4:5 a "
                       "1,91:1.",
    "video_invalido": "Instagram rechaza el vídeo: hace falta MP4 H.264 de 1080 de ancho, 9:16, de 3 a 90 segundos y "
                      "menos de 100 MiB.",
    "pie": "El pie no vale para Instagram: máximo 2.200 caracteres, 30 hashtags y 20 menciones.",
    "carrusel": "Un carrusel de Instagram lleva de 2 a 10 imágenes.",
    "formato": "Los parámetros de la publicación no valen para Instagram.",
    "archivo": "No encuentro un fichero de la publicación.",
    "contenedor_no_listo": "Instagram tarda más de lo normal en procesar la publicación. Se volverá a intentar.",
    "contenedor_caducado": "La publicación preparada en Instagram ha caducado o ha fallado; se prepara de nuevo.",
    "ambiguo": "No se sabe si Instagram llegó a publicarla. No se ha vuelto a enviar para no duplicarla: al "
               "reintentar se comprobará primero en Instagram.",
    "alojamiento": "No se puede subir la imagen al alojamiento público. Revisa su configuración y que sea accesible "
                   "desde internet.",
    "alojamiento_red": "El alojamiento público no responde bien ahora mismo. Se volverá a intentar. Si el alojamiento "
                       "es GitHub, los vídeos se sirven por jsDelivr y un fichero recién subido puede tardar unos segundos; "
                       "si no se arregla, usa el tipo «carpeta».",
    "renovacion_pronto": "Meta no renueva un token de menos de 24 horas. Se puede volver a intentar más tarde.",
    "desconocido": "Instagram ha devuelto un error que no reconozco.",
}

# Códigos de error de Meta, tomados de Vivax auditori. El subcódigo manda sobre el código.
_SUBCODIGOS = {
    2207003: "imagen_descarga", 2207052: "imagen_descarga",
    2207004: "imagen_invalida", 2207005: "imagen_invalida", 2207009: "imagen_invalida",
    2207010: "pie", 2207028: "carrusel",
    2207027: "contenedor_no_listo", 2207020: "contenedor_caducado",
    2207042: "limite", 2207050: "cuenta", 2207051: "cuenta",
    2207001: "servidor", 2207032: "servidor", 2207008: "servidor",
}
_CODIGOS = {
    190: "token", 102: "token", 10: "permiso", 25: "cuenta", 9: "limite",
    9004: "imagen_descarga", 36000: "imagen_invalida", 36001: "imagen_invalida", 36003: "imagen_invalida",
    36004: "pie", 9007: "contenedor_no_listo",
    1: "servidor", 2: "servidor", 4: "tasa", 17: "tasa", 32: "tasa", 341: "tasa", 613: "tasa",
}


# ---------------------------------------------------------------------------------------------
# Secretos y logs
# ---------------------------------------------------------------------------------------------

_SECRETOS: set[str] = set()


def _registrar(valor: str) -> None:
    """Añade un token a la lista de lo que no puede salir en ningún texto. Los valores muy cortos no se registran."""
    if valor and len(valor) >= 8:
        _SECRETOS.add(valor)


def limpiar(texto) -> str:
    """Quita de un texto los tokens conocidos, cualquier `access_token=...` y cualquier `Bearer ...`."""
    t = str(texto)
    for secreto in sorted(_SECRETOS, key=len, reverse=True):
        t = t.replace(secreto, "***")
    t = re.sub(r"(access_token=)[^&\s\"']+", r"\1***", t)
    t = re.sub(r"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{8,}", "***", t)          # tokens de GitHub (clásicos y de grano fino)
    return re.sub(r"(Bearer\s+)[^\s\"',}]+", r"\1***", t, flags=re.IGNORECASE)


class FiltroSecretos(logging.Filter):
    """Limpia el mensaje de cada registro. Se instala una sola vez, en el logger de este módulo y en urllib3."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            texto = record.getMessage()
        except Exception:
            return True
        limpio = limpiar(texto)
        if limpio != texto:
            record.msg, record.args = limpio, ()
        return True


_FILTRO = FiltroSecretos()
log.addFilter(_FILTRO)
logging.getLogger("urllib3.connectionpool").addFilter(_FILTRO)


# ---------------------------------------------------------------------------------------------
# Piezas públicas
# ---------------------------------------------------------------------------------------------

class ErrorInstagram(Exception):
    """Fallo de Instagram ya clasificado. `clase` dice qué hacer; `bloquea` y `reintentable` se derivan de ella.

    El mensaje va al dueño: en español, sin secretos y con lo que hay que hacer.
    """

    def __init__(self, clase: str, mensaje: str):
        super().__init__(limpiar(mensaje))
        self.clase = clase

    @property
    def bloquea(self) -> bool:
        return self.clase in BLOQUEANTES

    @property
    def reintentable(self) -> bool:
        return self.clase in REINTENTABLES


@dataclass
class Credenciales:
    """Id de la cuenta y tokens. Los tokens no aparecen en `repr` ni en `str`."""

    user_id: str
    token: str = field(repr=False)
    github_token: str = field(default="", repr=False)

    def __setattr__(self, nombre: str, valor) -> None:
        """Cada token que se asigne, también en la construcción y al pegar uno nuevo, queda como secreto."""
        if nombre in ("token", "github_token"):
            _registrar(valor)
        object.__setattr__(self, nombre, valor)

    def __repr__(self) -> str:
        gh = "'***'" if self.github_token else "''"
        return f"Credenciales(user_id={self.user_id!r}, token='***', github_token={gh})"


@dataclass
class ConfigIG:
    modo: str = "desactivado"
    api: str = "instagram_login"
    version: str = "v25.0"
    espera_contenedor_s: float = 120
    espera_video_s: float = 600
    alojamiento: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.modo not in MODOS:
            raise ValueError(f"modo tiene que ser uno de {MODOS}; ahora pone {self.modo!r}.")
        if self.api != "instagram_login":
            raise ValueError("Solo se admite la API con inicio de sesión de Instagram («instagram_login»).")
        if not _VERSION.fullmatch(self.version):
            raise ValueError(f"version tiene que ser como «v25.0»; ahora pone {self.version!r}.")
        self.alojamiento = dict(self.alojamiento or {})


@dataclass(frozen=True)
class Resultado:
    """`recuperada` es True si la publicación ya estaba en Instagram (la encontró publicada) o si se terminó un
    contenedor que dejó una ejecución anterior. Un reinicio desde cero, aunque reutilice direcciones, es False."""

    media_id: str
    permalink: str | None = None
    recuperada: bool = False


# ---------------------------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------------------------

def _entero(valor) -> int | None:
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _iso(fecha: datetime) -> str:
    return fecha.isoformat(timespec="seconds")


def _fecha(valor) -> datetime | None:
    """Fecha con zona: la de nuestro estado (+00:00) o la de Meta (+0000). None si no se entiende."""
    try:
        return datetime.strptime(str(valor or ""), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _normalizar(texto) -> str:
    return " ".join(str(texto or "").split())


def _json_de(r) -> dict:
    try:
        datos = r.json()
    except ValueError:
        return {}
    return datos if isinstance(datos, dict) else {}


def _clasificar(http: int | None, codigo, subcodigo) -> str:
    """Código de Meta a clase de acción. Orden: subcódigo, código, 200–299 (permiso), 429 (tasa), 5xx, 401."""
    sub, cod = _entero(subcodigo), _entero(codigo)
    if sub in _SUBCODIGOS:
        return _SUBCODIGOS[sub]
    if cod in _CODIGOS:
        return _CODIGOS[cod]
    if cod is not None and 200 <= cod <= 299:
        return "permiso"
    if http == 429:
        return "tasa"
    if http is not None and http >= 500:
        return "servidor"
    if http == 401:
        return "token"
    return "desconocido"


def _error_de_respuesta(http: int, datos: dict) -> ErrorInstagram:
    err = datos.get("error") if isinstance(datos.get("error"), dict) else {}
    codigo, sub = err.get("code"), err.get("error_subcode")
    clase = _clasificar(http, codigo, sub)
    detalle = str(err.get("error_user_msg") or err.get("message") or "")[:200]
    tecnico = f" (Meta: «{detalle}»)" if detalle else ""
    if codigo is not None:
        tecnico += f" [código {codigo}" + (f"/{sub}" if sub else "") + "]"
    return ErrorInstagram(clase, _TEXTOS.get(clase, _TEXTOS["desconocido"]) + tecnico)


def _error_de_contenedor(texto: str) -> ErrorInstagram:
    """Un contenedor en ERROR: el texto de Meta suele traer el código del motivo."""
    clase = "desconocido"
    for numero in re.findall(r"\d{4,7}", texto):
        if int(numero) in _SUBCODIGOS:
            clase = _SUBCODIGOS[int(numero)]
            break
        if int(numero) in _CODIGOS:
            clase = _CODIGOS[int(numero)]
            break
    extra = f" (Meta: «{texto[:200]}»)" if texto else ""
    return ErrorInstagram(clase, _TEXTOS.get(clase, _TEXTOS["desconocido"]) + extra)


def _id_de(datos: dict) -> str:
    if not datos.get("id"):
        raise ErrorInstagram("servidor", _TEXTOS["servidor"] + " (Meta no devolvió el id del contenedor).")
    return str(datos["id"])


def _prefijo_seguro(prefijo) -> str:
    """Ruta relativa segura para alojar: solo letras, cifras, punto, guion y guion bajo; sin «..»."""
    partes = [p for p in str(prefijo or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if not partes or any(p == ".." or not _SEGMENTO.fullmatch(p) for p in partes):
        raise ErrorInstagram("formato", f"Prefijo de alojamiento no válido: {str(prefijo)!r}.")
    return "/".join(partes)


def _tamano(ruta: Path) -> int:
    """Tamaño de un fichero. Si no se puede leer, un ErrorInstagram de «archivo», nunca un OSError suelto."""
    try:
        return ruta.stat().st_size
    except OSError as e:
        raise ErrorInstagram("archivo", f"No puedo leer {ruta.name} ({type(e).__name__}).") from None


# ---------------------------------------------------------------------------------------------
# Ficheros: JPEG y vídeo
# ---------------------------------------------------------------------------------------------

def _tipo_de(ruta: Path) -> str | None:
    """«jpeg», «mp4» o None, mirando la cabecera del fichero y no su extensión."""
    with open(ruta, "rb") as f:
        cabecera = f.read(12)
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if cabecera[4:8] == b"ftyp":
        return "mp4"
    return None


def comprobar_archivo(ruta, esperado: str | None = None, sondear=None) -> str:
    """Comprueba un fichero antes de subirlo: que existe, que es JPEG o MP4 y que no pasa de su peso.

    Con `esperado` («jpeg» o «mp4») exige ese tipo y además las reglas de publicación: JPEG normal (sin MPO) con
    proporción de 4:5 a 1,91:1 si hay Pillow; vídeo según `validar_video`. Devuelve «jpeg» o «mp4».
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ErrorInstagram("archivo", f"No encuentro el fichero {ruta.name}.")
    try:
        real = _tipo_de(ruta)
        peso = ruta.stat().st_size
    except OSError as e:
        raise ErrorInstagram("archivo", f"No puedo leer {ruta.name} ({type(e).__name__}).") from None
    if real is None:
        clase = {"jpeg": "imagen_invalida", "mp4": "video_invalido"}.get(esperado, "archivo")
        raise ErrorInstagram(clase, f"{ruta.name} no es ni JPEG ni MP4.")
    if esperado and real != esperado:
        clase = "imagen_invalida" if esperado == "jpeg" else "video_invalido"
        raise ErrorInstagram(clase, f"{ruta.name} es {real.upper()} y aquí hace falta {esperado.upper()}.")
    if real == "jpeg":
        if peso > MAX_JPEG:
            raise ErrorInstagram("imagen_invalida", f"{ruta.name} pesa {peso / MIB:.1f} MiB y Instagram admite 8 MiB "
                                                    "como máximo. Recomprímela.")
        if esperado:
            _comprobar_jpeg(ruta)
    else:
        if peso > MAX_VIDEO:
            raise ErrorInstagram("video_invalido", f"{ruta.name} pesa {peso / MIB:.1f} MiB y el máximo es 100 MiB.")
        if esperado:
            validar_video(ruta, sondear or _sondear_video)
    return real


def _comprobar_jpeg(ruta: Path) -> None:
    with open(ruta, "rb") as f:
        cabecera = f.read(4096)
    if b"MPF\x00" in cabecera:
        raise ErrorInstagram("imagen_invalida", f"{ruta.name} es un JPEG extendido (MPO). Instagram solo admite JPEG "
                                                "normal: guárdala de nuevo como JPEG.")
    try:
        from PIL import Image
    except ImportError:
        return                      # sin Pillow no se puede mirar la proporción; Meta la rechazará si no vale
    try:
        with Image.open(ruta) as im:
            ancho, alto = im.size
            formato = im.format
    except Exception:
        raise ErrorInstagram("imagen_invalida", f"No puedo leer {ruta.name}: el fichero está dañado.") from None
    if formato != "JPEG":
        raise ErrorInstagram("imagen_invalida", f"{ruta.name} es {formato}, no JPEG.")
    if not 0.8 - 1e-3 <= ancho / alto <= 1.91 + 1e-3:
        raise ErrorInstagram("imagen_invalida", f"{ruta.name} mide {ancho}x{alto}: Instagram admite de 4:5 (vertical) "
                                                "a 1,91:1 (horizontal).")


def validar_video(ruta: Path, sondear) -> None:
    """Reglas de Reels que se pueden mirar en local: MP4 H.264, 1080x1920 y de 3 a 90 segundos.

    `sondear(ruta)` devuelve `{"contenedor", "codec", "ancho", "alto", "duracion"}`. Se inyecta para las pruebas.
    """
    datos = sondear(ruta)
    if datos["codec"] != "h264":
        raise ErrorInstagram("video_invalido", f"{ruta.name} usa {datos['codec']}; Instagram exige H.264.")
    if (datos["ancho"], datos["alto"]) != (VIDEO_ANCHO, VIDEO_ALTO):
        raise ErrorInstagram("video_invalido", f"{ruta.name} mide {datos['ancho']}x{datos['alto']}; hace falta "
                                               f"{VIDEO_ANCHO}x{VIDEO_ALTO} (1080 de ancho y 9:16).")
    minimo, maximo = VIDEO_DURACION
    if not minimo <= datos["duracion"] <= maximo:
        raise ErrorInstagram("video_invalido", f"{ruta.name} dura {datos['duracion']:.1f} s; hacen falta entre "
                                               f"{minimo} y {maximo} s.")
    if "mp4" not in datos["contenedor"]:
        raise ErrorInstagram("video_invalido", f"{ruta.name} no es un contenedor MP4.")


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _sondear_video(ruta: Path) -> dict:
    """Lee códec, tamaño y duración con ffmpeg. imageio-ffmpeg trae solo ffmpeg, sin ffprobe, así que se lee el
    texto de `ffmpeg -i`. Ese comando termina con código 1 a propósito: lo que importa es lo que escribe."""
    exe = _ffmpeg()
    if exe is None:
        raise ErrorInstagram("video_invalido", "No puedo comprobar el vídeo porque no encuentro ffmpeg. Instala "
                                               "imageio-ffmpeg (pip install imageio-ffmpeg).")
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", str(ruta)], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise ErrorInstagram("video_invalido", f"No puedo leer {ruta.name} con ffmpeg.") from None
    texto = (r.stderr or b"").decode("utf-8", errors="replace")
    video = re.search(r"Stream #\d+:\d+[^\n]*?Video: (\w+)[^\n]*?, (\d{2,5})x(\d{2,5})", texto)
    duracion = re.search(r"Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)", texto)
    contenedor = re.search(r"Input #0, ([^\n]+?) from ", texto)
    if not (video and duracion and contenedor):
        raise ErrorInstagram("video_invalido", f"No consigo leer {ruta.name}: ¿está completo el fichero?")
    horas, minutos, segundos = duracion.groups()
    return {"contenedor": contenedor.group(1), "codec": video.group(1).lower(),
            "ancho": int(video.group(2)), "alto": int(video.group(3)),
            "duracion": int(horas) * 3600 + int(minutos) * 60 + float(segundos)}


def _comprobar_pie(pie: str) -> None:
    if len(pie) > MAX_PIE:
        raise ErrorInstagram("pie", f"{_TEXTOS['pie']} El pie tiene {len(pie)} caracteres.")
    if len(re.findall(r"#\w+", pie)) > MAX_HASHTAGS:
        raise ErrorInstagram("pie", f"{_TEXTOS['pie']} Lleva más de {MAX_HASHTAGS} hashtags.")
    if len(re.findall(r"@\w+", pie)) > MAX_MENCIONES:
        raise ErrorInstagram("pie", f"{_TEXTOS['pie']} Lleva más de {MAX_MENCIONES} menciones.")


def _alts(alt, n: int) -> list[str | None]:
    """Un texto alternativo por imagen. Con una sola imagen acepta un str; con un carrusel, una lista del mismo tamaño."""
    if alt is None:
        lista = [None] * n
    elif isinstance(alt, str):
        if n != 1:
            raise ErrorInstagram("formato", "Para un carrusel pasa una lista con un texto alternativo por imagen.")
        lista = [alt]
    else:
        lista = list(alt)
        if len(lista) != n:
            raise ErrorInstagram("formato", f"Hay {n} imágenes y {len(lista)} textos alternativos.")
    salida: list[str | None] = []
    for i, texto in enumerate(lista, start=1):
        texto = (texto or "").strip()
        if len(texto) > MAX_ALT:
            raise ErrorInstagram("formato", f"El texto alternativo de la imagen {i} pasa de {MAX_ALT} caracteres.")
        salida.append(texto or None)
    return salida


# ---------------------------------------------------------------------------------------------
# Alojamiento: direcciones públicas que Meta descargará
# ---------------------------------------------------------------------------------------------

class Alojamiento:
    """Sube los ficheros a un sitio público y devuelve sus direcciones.

    `config` es `ConfigIG.alojamiento`: `{"tipo": "carpeta", "directorio", "base_url"}` o
    `{"tipo": "github", "repo", "rama", "carpeta"}`. Cada dirección se comprueba antes de devolverla: tiene que
    responder 200 con el tipo de contenido correcto y el tamaño subido. Lo que ya se subió no se borra si falla
    una parte: son ficheros con nombre aleatorio que nadie conoce.
    """

    def __init__(self, config: dict | None, github_token: str = "", *, http=requests, dormir=time.sleep,
                 buscar_gh=None, correr_gh=None):
        self._cfg = dict(config or {})
        self._github_token = github_token or ""
        self._http = http
        self._dormir = dormir
        self._buscar_gh = buscar_gh or localizar_gh          # inyectables para las pruebas; por defecto, la CLI gh real
        self._correr_gh = correr_gh or ejecutar_gh

    def subir(self, archivos, prefijo: str, ya=None, al_subir=None) -> list[str]:
        """Devuelve, en el mismo orden, las direcciones públicas. `prefijo` agrupa las de una publicación.

        `ya` son las direcciones de los primeros ficheros, subidos en un intento anterior: no se vuelven a subir, pero sí
        se comprueban otra vez. `al_subir(direcciones)` se llama tras cada subida correcta y antes de comprobarla, con
        todas las direcciones hasta ahora: si la comprobación falla, el siguiente intento no vuelve a subir ese fichero.
        """
        rutas = [Path(a) for a in archivos]
        if not rutas:
            raise ErrorInstagram("formato", "No hay ficheros que subir.")
        prefijo = _prefijo_seguro(prefijo)
        tipos = [comprobar_archivo(r) for r in rutas]      # antes de tocar ningún alojamiento
        tamanos = [_tamano(r) for r in rutas]              # los tamaños también se miran antes de subir nada
        tipo = str(self._cfg.get("tipo") or "").strip().lower()
        if tipo == "carpeta":
            directorio, base = self._config_carpeta()
        elif tipo == "github":
            g = self._config_github()
        else:
            raise ErrorInstagram("alojamiento", "alojamiento.tipo tiene que ser «carpeta» o «github» (config.json).")
        hechas = [str(u) for u in (ya or [])][:len(rutas)]
        for i, (ruta, real, tamano) in enumerate(zip(rutas, tipos, tamanos), start=1):
            if i > len(hechas):
                if tipo == "github":
                    if i > 1:
                        self._dormir(1.0)                   # GitHub pide un segundo entre escrituras seguidas
                    url = self._subir_github(g, prefijo, ruta, i, real)
                else:
                    url = self._subir_carpeta(directorio, base, prefijo, ruta, i, real)
                hechas.append(url)
                if al_subir is not None:
                    al_subir(list(hechas))
            self._verificar(hechas[i - 1], real, tamano)
        return hechas

    def _config_carpeta(self) -> tuple[Path, str]:
        directorio = str(self._cfg.get("directorio") or "").strip()
        base = str(self._cfg.get("base_url") or "").strip().rstrip("/")
        if not directorio or not base:
            raise ErrorInstagram("alojamiento", "Con alojamiento «carpeta» hacen falta «directorio» (la carpeta que sirve "
                                                "tu web) y «base_url» (su dirección pública).")
        if not base.lower().startswith(("https://", "http://")):
            raise ErrorInstagram("alojamiento", "alojamiento.base_url tiene que empezar por https://.")
        ruta = Path(directorio)
        if not ruta.is_dir():
            raise ErrorInstagram("alojamiento", f"No existe la carpeta {ruta}. Tiene que ser la que sirve tu servidor web.")
        return ruta, base

    def _config_github(self) -> dict:
        auth = str(self._cfg.get("auth") or "token").strip().lower()
        if auth not in ("token", "gh"):
            raise ErrorInstagram("alojamiento", "alojamiento.auth tiene que ser «token» o «gh» (cuentas.json).")
        repo = str(self._cfg.get("repo") or "").strip().strip("/")
        if not _REPO.fullmatch(repo):
            raise ErrorInstagram("alojamiento", "alojamiento.repo tiene que ser «usuario/repositorio» (config.json).")
        if auth == "token" and not self._github_token:
            raise ErrorInstagram("alojamiento", "Falta el token de GitHub (GITHUB_TOKEN en .env).")
        if auth == "gh" and any(p in (".", "..") for p in repo.split("/")):
            raise ErrorInstagram("alojamiento", "alojamiento.repo no puede tener «.» ni «..» (cuentas.json).")
        rama = str(self._cfg.get("rama") or "main").strip()
        if not _RAMA.fullmatch(rama) or (auth == "gh" and ".." in rama):
            raise ErrorInstagram("alojamiento", f"alojamiento.rama no vale: {rama!r}.")
        g = {"repo": repo, "rama": rama, "carpeta": _prefijo_seguro(self._cfg.get("carpeta") or "publicadas")}
        if auth == "gh":
            g["auth"] = "gh"                               # ningún token: gh usa la sesión guardada en el equipo
        else:
            g["token"] = self._github_token
        return g

    @staticmethod
    def _subir_carpeta(directorio: Path, base: str, prefijo: str, ruta: Path, i: int, real: str) -> str:
        nombre = f"{secrets.token_hex(8)}-{i:02d}{_EXT[real]}"     # 16 hex: la dirección no se puede adivinar
        destino = directorio / prefijo
        try:
            destino.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ruta, destino / nombre)
        except OSError as e:
            raise ErrorInstagram("alojamiento", f"No puedo copiar el fichero a {destino} ({type(e).__name__}). Revisa "
                                                "los permisos y el espacio de esa carpeta.") from None
        return f"{base}/{prefijo}/{nombre}"

    def _subir_github(self, g: dict, prefijo: str, ruta: Path, i: int, real: str) -> str:
        """Sube un fichero con la API de contenidos y devuelve su dirección fija a la confirmación que lo creó."""
        if g.get("auth") == "gh":                  # con «auth»: «gh», la misma subida pero con la CLI y sin token
            return self._subir_gh(g, prefijo, ruta, i, real)
        try:
            datos = ruta.read_bytes()
        except OSError as e:
            raise ErrorInstagram("archivo", f"No puedo leer {ruta.name} ({type(e).__name__}).") from None
        cabeceras = {"Authorization": f"Bearer {g['token']}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": AGENTE}
        ultimo: ErrorInstagram | None = None
        for n in range(len(ESPERAS_S) + 1):
            # Nombre nuevo en cada intento: si una respuesta se perdió, el reintento no choca con el fichero anterior.
            ruta_repo = f"{g['carpeta']}/{prefijo}/{secrets.token_hex(8)}-{i:02d}{_EXT[real]}"
            cuerpo = {"message": f"Instagram {prefijo} ({i:02d})", "content": base64.b64encode(datos).decode("ascii"),
                      "branch": g["rama"]}
            try:
                r = self._http.request("PUT", f"{GITHUB_API}/repos/{g['repo']}/contents/{ruta_repo}", headers=cabeceras,
                                       json=cuerpo, timeout=TIMEOUT_SUBIDA)
            except (requests.RequestException, OSError) as e:
                ultimo = ErrorInstagram("alojamiento_red", f"No hay conexión con GitHub ({type(e).__name__}). Se volverá a intentar.")
            else:
                if r.status_code in (200, 201):
                    sha = _sha_de(r)
                    if sha:
                        if real == "mp4":
                            return f"{JSDELIVR}/{g['repo']}@{sha}/{ruta_repo}"
                        return f"{GITHUB_RAW}/{g['repo']}/{sha}/{ruta_repo}"
                    ultimo = ErrorInstagram("alojamiento_red", "GitHub aceptó la imagen pero no devolvió su confirmación.")
                else:
                    error = _error_github(r)
                    if not error.reintentable:
                        raise error                 # un rechazo que no se arregla repitiendo: no se vuelve a enviar
                    ultimo = error
            if n < len(ESPERAS_S):
                self._dormir(ESPERAS_S[n])
        raise ultimo

    def _subir_gh(self, g: dict, prefijo: str, ruta: Path, i: int, real: str) -> str:
        """Sube un fichero con la CLI gh y devuelve su dirección fija a la confirmación que lo creó. Mismos reintentos
        que `_subir_github`. Ningún token pasa por aquí: gh usa la sesión que ya tiene guardada el equipo."""
        try:
            datos = ruta.read_bytes()
        except OSError as e:
            raise ErrorInstagram("archivo", f"No puedo leer {ruta.name} ({type(e).__name__}).") from None
        contenido = base64.b64encode(datos).decode("ascii")
        ultimo: ErrorInstagram | None = None
        for n in range(len(ESPERAS_S) + 1):
            # Nombre nuevo en cada intento, como en la subida con token: un intento perdido no choca con el siguiente.
            ruta_repo = f"{g['carpeta']}/{prefijo}/{secrets.token_hex(8)}-{i:02d}{_EXT[real]}"
            cuerpo = {"message": f"Instagram {prefijo} ({i:02d})", "content": contenido, "branch": g["rama"]}
            try:
                sha = self._put_gh(g, ruta_repo, cuerpo)
            except ErrorInstagram as error:
                if error.clase != "alojamiento_red":
                    raise                       # sin sesión, sin permiso o ruta mal formada: lo arregla el dueño
                ultimo = error
            else:
                if real == "mp4":
                    return f"{JSDELIVR}/{g['repo']}@{sha}/{ruta_repo}"
                return f"{GITHUB_RAW}/{g['repo']}/{sha}/{ruta_repo}"
            if n < len(ESPERAS_S):
                self._dormir(ESPERAS_S[n])
        raise ultimo

    def _put_gh(self, g: dict, ruta_repo: str, cuerpo: dict) -> str:
        """Una escritura: `gh api --hostname github.com -X PUT repos/<repo>/contents/<ruta> --input <temporal>`.

        Solo se permite una ruta dentro de la carpeta configurada, con segmentos validados. El cuerpo JSON (puede pesar
        decenas de MB) va a un fichero temporal, nunca a la línea de comandos, y ese fichero se borra siempre.
        Devuelve la confirmación (sha) que da GitHub.
        """
        endpoint = f"repos/{g['repo']}/contents/{ruta_repo}"
        if not endpoint.startswith(f"repos/{g['repo']}/contents/{g['carpeta']}/") or \
                any(p in ("", ".", "..") or not _SEGMENTO.fullmatch(p) for p in endpoint.split("/")):
            raise ErrorInstagram("formato", _TEXTOS_GH["destino"])
        exe = self._buscar_gh()
        if not exe:
            raise error_sin_cli()
        try:
            fd, camino = tempfile.mkstemp(prefix="vivax-gh-", suffix=".json")
        except OSError as e:
            raise ErrorInstagram("alojamiento", f"No puedo crear un fichero temporal para subir la imagen ({type(e).__name__}). "
                                                "Revisa el espacio y los permisos de la carpeta temporal.") from None
        try:
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(cuerpo, f)
            except OSError as e:                  # disco lleno o antivirus: un fallo local que puede arreglarse solo
                raise ErrorInstagram("local", _TEXTOS_GH["temporal"].format(tipo=type(e).__name__)) from None
            try:
                r = self._correr_gh(exe, ["api", "--hostname", GH_HOST, "-X", "PUT", endpoint, "--input", camino],
                                    timeout=GH_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                raise ErrorInstagram("alojamiento_red", _TEXTOS_GH["tiempo"]) from None
            except OSError as e:
                raise ErrorInstagram("alojamiento", _TEXTOS_GH["ejecutar"].format(tipo=type(e).__name__)) from None
        finally:
            _borrar_temporal(camino)
        if r.returncode != 0:
            raise fallo_gh(r.returncode, r.stdout, r.stderr, g["repo"])
        try:
            datos = json.loads(texto_salida(r.stdout))
        except ValueError:
            raise ErrorInstagram("alojamiento_red", _TEXTOS_GH["respuesta"]) from None
        sha = _sha_de_datos(datos)
        if not sha:
            raise ErrorInstagram("alojamiento_red", _TEXTOS_GH["confirmacion"])
        return sha

    def _verificar(self, url: str, real: str, tamano: int) -> None:
        """Pide la dirección como lo haría Meta (sin credenciales) y exige 200, el tipo y el tamaño subidos."""
        esperado = _CONTENT_TYPE[real]
        motivo = "no responde"
        for n in range(len(ESPERAS_S) + 1):           # un fichero recién subido puede tardar unos segundos en servirse
            try:
                r = self._http.request("GET", url, headers={"User-Agent": AGENTE}, timeout=TIMEOUT, stream=True)
            except (requests.RequestException, OSError) as e:
                motivo = f"no responde ({type(e).__name__})"
            else:
                try:
                    cab = {str(k).lower(): v for k, v in r.headers.items()}
                    tipo_srv = str(cab.get("content-type", "")).split(";")[0].strip().lower()
                    longitud = _entero(cab.get("content-length"))
                    if r.status_code == 200 and tipo_srv == esperado and longitud in (None, tamano):
                        return
                    if r.status_code != 200:
                        motivo = f"responde {r.status_code}"
                    elif tipo_srv != esperado:
                        motivo = f"sirve {tipo_srv or 'sin tipo'} en lugar de {esperado}"
                    else:
                        motivo = f"sirve {longitud} bytes en lugar de {tamano}"
                finally:
                    r.close()
            if n < len(ESPERAS_S):
                self._dormir(ESPERAS_S[n])
        raise ErrorInstagram("alojamiento_red", f"{_TEXTOS['alojamiento_red']} La dirección {url} no sirve el fichero "
                                                f"({motivo}).")


def _sha_de(r) -> str:
    try:
        sha = str(r.json()["commit"]["sha"])
    except (ValueError, KeyError, TypeError):
        return ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else ""


def _error_github(r) -> ErrorInstagram:
    """Error de la API de GitHub. «alojamiento» pide que el dueño lo arregle; «alojamiento_red» se reintenta."""
    try:
        mensaje = str(r.json().get("message") or "")
    except (ValueError, AttributeError):
        mensaje = ""
    cab = {str(k).lower(): v for k, v in r.headers.items()}
    limitado = bool(cab.get("retry-after")) or cab.get("x-ratelimit-remaining") == "0" or \
        any(p in mensaje.lower() for p in ("rate limit", "abuse"))
    if r.status_code == 403 and limitado:
        return ErrorInstagram("alojamiento_red", "GitHub limita las peticiones ahora mismo. Se volverá a intentar.")
    if r.status_code == 401:
        return ErrorInstagram("alojamiento", "GitHub no acepta el token (GITHUB_TOKEN): está mal copiado, ha caducado o "
                                             "se ha borrado. Crea uno nuevo.")
    if r.status_code == 403:
        return ErrorInstagram("alojamiento", "El token de GitHub no puede escribir en el repositorio. Necesita «Contents: "
                                             "Read and write» sobre ese repositorio.")
    if r.status_code == 404:
        return ErrorInstagram("alojamiento", "GitHub no encuentra el repositorio o la rama, o el token no tiene acceso a "
                                             "ellos. Revisa alojamiento.repo y alojamiento.rama.")
    if r.status_code == 413:                       # el fichero es demasiado grande para GitHub: es de esta pieza, no de la cuenta
        return ErrorInstagram("pieza_rechazada", _TEXTOS_GH["tamano"])
    if r.status_code == 422:                       # validación, o un límite de uso de GitHub que puede ser pasajero
        return ErrorInstagram("rechazo_github", _TEXTOS_GH["validacion"].format(detalle=mensaje[:200] or "sin detalle"))
    if r.status_code in (409, 429) or r.status_code >= 500:
        return ErrorInstagram("alojamiento_red", f"GitHub no responde bien ahora mismo (HTTP {r.status_code}). Se volverá "
                                                 "a intentar.")
    return ErrorInstagram("alojamiento", f"GitHub ha rechazado la imagen (HTTP {r.status_code}): {mensaje[:200] or 'sin detalle'}.")


# ---------------------------------------------------------------------------------------------
# GitHub con la sesión de la CLI gh (alojamiento con «auth»: «gh»)
# ---------------------------------------------------------------------------------------------
# Con «auth»: «gh» el router no lleva ningún token. Cada subida ejecuta `gh api` con la sesión que la CLI ya tiene
# guardada en este equipo. El router fija el servidor (github.com), quita de su entorno las variables que cambian la
# cuenta o el destino y no lee ni pide ninguna credencial. Las pruebas inyectan `buscar_gh` y `correr_gh`.

GH_HOST = "github.com"
GH_TIMEOUT_S = 300                       # subir un fichero (un vídeo de 100 MiB pesa unos 133 MB en base64)
GH_CONSULTA_S = 30                       # `gh api repos/<repo>` al conectar la cuenta
# Variables que cambian la cuenta, el servidor o la salida de gh. No se pasan a gh.
GH_VARIABLES_QUITADAS = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                                   "GH_HOST", "GH_REPO", "GH_CONFIG_DIR", "GH_DEBUG", "GH_FORCE_TTY"})
_NOMBRE_GH = "gh.exe" if os.name == "nt" else "gh"
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # Windows: que gh no abra una consola
_HTTP_GH = re.compile(r"HTTP (\d{3})\b")
_PISTAS_SIN_SESION = ("gh auth login", "not logged", "to get started with github cli", "authentication required")
_PISTAS_RED = ("dial tcp", "no such host", "lookup", "timeout", "timed out", "connection", "network", "unexpected eof",
               "tls", "eof", "goaway", "deadline")
_PISTAS_USO = ("unknown flag", "unknown shorthand flag", "unknown command", "usage:")

_TEXTOS_GH = {
    "sin_cli": "No encuentro la CLI de GitHub (gh) en este equipo. Hace falta para subir las imágenes con la sesión que ya "
               "tiene guardada: ponla en el PATH o en %LOCALAPPDATA%\\ghcli\\bin\\gh.exe y reinicia el router.",
    "sin_sesion": "La CLI gh de este equipo no tiene sesión iniciada o la sesión ha caducado. En una ventana de PowerShell "
                  "del equipo ejecuta «gh auth login» y entra con la cuenta que puede escribir en el repositorio. Cuando "
                  "termine, ejecuta «python -m router github n1 {repo} --con-gh»: comprueba la sesión y levanta el bloqueo "
                  "de la cola. Después, reinicia el router. No pegues ningún token en Telegram.",
    "permiso": "La cuenta de gh de este equipo no puede escribir en el repositorio (GitHub: «{detalle}»). Entra con una cuenta "
               "que tenga permiso de escritura («gh auth login») y repite «--con-gh».",
    "no_encontrado": "GitHub no encuentra el repositorio o la rama con la sesión de gh de este equipo. Revisa "
                     "alojamiento.repo y alojamiento.rama en cuentas.json.",
    "limite": "GitHub limita las peticiones ahora mismo. Se volverá a intentar.",
    "tiempo": "gh ha tardado demasiado en subir el fichero. Se volverá a intentar.",
    "red": "No hay conexión con GitHub desde gh. Se volverá a intentar.",
    "servidor": "GitHub no responde bien ahora mismo (HTTP {http}). Se volverá a intentar.",
    "respuesta": "GitHub ha devuelto algo que no entiendo al subir el fichero. Se volverá a intentar.",
    "confirmacion": "GitHub aceptó el fichero pero no devolvió su confirmación. Se volverá a intentar.",
    "ejecutar": "No puedo ejecutar gh ({tipo}). Revisa que la CLI funcione en este equipo.",
    "rechazo": "GitHub ha rechazado el fichero (HTTP {http}): {detalle}.",
    "tamano": "GitHub no acepta el fichero por su tamaño (HTTP 413). La pieza no puede salir con este alojamiento: "
              "edítala o recházala.",
    "validacion": "GitHub ha rechazado el fichero (HTTP 422): {detalle}. Puede ser un límite de uso pasajero: se volverá "
                  "a intentar y, si se repite, la pieza pasará a error.",
    "temporal": "No puedo escribir la imagen en un fichero temporal ({tipo}). Puede ser el disco lleno o el antivirus: se "
                "volverá a intentar; si se repite, revisa el espacio del disco y el antivirus.",
    "transitorio": "gh no ha podido subir el fichero (código {codigo}): {detalle}. Se volverá a intentar.",
    "uso": "gh no entiende la orden de subida (código {codigo}): {detalle}. Comprueba la CLI: «gh api --help» tiene que "
           "mostrar --hostname e --input. Hasta que se arregle, la cola se para.",
    "destino": "La ruta de subida no es la del repositorio y la carpeta configurados. No se sube nada.",
}


def error_sin_cli() -> ErrorInstagram:
    return ErrorInstagram("alojamiento", _TEXTOS_GH["sin_cli"])


def localizar_gh(entorno=None) -> str | None:
    """Ruta de la CLI gh, o None. Mira el PATH de este proceso y, si no está, %LOCALAPPDATA%\\ghcli\\bin\\gh.exe: el router
    arranca desde la carpeta de Inicio, con un PATH que puede no ser el de una terminal.

    No se usa shutil.which: en Python 3.11 y Windows busca primero en la carpeta actual, y un gh.exe que hubiera allí se
    ejecutaría. Solo vale un fichero con ese nombre exacto y en una carpeta absoluta del PATH (un gh.cmd pasaría por cmd.exe).
    """
    env = os.environ if entorno is None else entorno
    for carpeta in (env.get("PATH") or "").split(os.pathsep):
        carpeta = carpeta.strip().strip('"')
        if not carpeta or not Path(carpeta).is_absolute():
            continue
        candidato = Path(carpeta) / _NOMBRE_GH
        if candidato.is_file():
            return str(candidato)
    local = env.get("LOCALAPPDATA") or ""
    if local:
        candidato = Path(local) / "ghcli" / "bin" / _NOMBRE_GH
        if candidato.is_file():
            return str(candidato)
    return None


def ejecutar_gh(exe: str, argumentos: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Ejecuta gh con la lista de argumentos (sin shell), con tiempo máximo y sin ventana. El entorno no lleva las
    variables de GH_VARIABLES_QUITADAS: gh usa solo la sesión guardada en el equipo. Puede lanzar TimeoutExpired y OSError."""
    entorno = {k: v for k, v in os.environ.items() if k.upper() not in GH_VARIABLES_QUITADAS}
    entorno.update(GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1")
    return subprocess.run([exe, *argumentos], env=entorno, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=timeout, check=False, shell=False, creationflags=_SIN_VENTANA)


def texto_salida(salida) -> str:
    """La salida de gh (bytes) como texto. Lo que no sea UTF-8 se sustituye; no falla."""
    if isinstance(salida, bytes):
        return salida.decode("utf-8", errors="replace")
    return str(salida or "")


def _sha_de_datos(datos) -> str:
    try:
        sha = str(datos["commit"]["sha"])
    except (KeyError, TypeError):
        return ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else ""


def _borrar_temporal(ruta: str) -> None:
    """Borra el JSON temporal de una subida. Si no se puede, se avisa sin decir más."""
    try:
        os.remove(ruta)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("No pude borrar un fichero temporal de la subida a GitHub (%s).", type(e).__name__)


def _detalle_gh(texto: str) -> str:
    m = re.search(r'"message"\s*:\s*"([^"\n]{1,200})"', texto)
    if m:
        return m.group(1)
    for linea in texto.splitlines():
        linea = linea.strip()
        if linea.lower().startswith("gh:"):
            return re.sub(r"\s*\(HTTP \d{3}\)\s*$", "", linea[3:].strip())[:200]
    return ""


def fallo_gh(codigo: int, salida, error, repo: str = "") -> ErrorInstagram:
    """Traduce un `gh api` que ha fallado a un ErrorInstagram con la clase que pide el dueño o el reintento.

    Código 4 de gh, un 401 o el aviso de «gh auth login»: sin sesión (lo arregla el dueño). Sin código HTTP: si gh no
    entiende la orden (una versión antigua), bloqueo; cualquier otro fallo sin HTTP (red, un gh que se cae) se reintenta,
    como la subida con token. Con HTTP: 403 con límite, 409, 429 y 5xx se reintentan; lo demás, según GitHub.
    """
    texto = f"{texto_salida(error)}\n{texto_salida(salida)}"
    minus = texto.lower()
    m = _HTTP_GH.search(texto)
    http = int(m.group(1)) if m else None
    detalle = _detalle_gh(texto) or "sin detalle"
    if codigo == 4 or http == 401 or any(p in minus for p in _PISTAS_SIN_SESION):
        return ErrorInstagram("alojamiento", _TEXTOS_GH["sin_sesion"].format(repo=repo or "usuario/repositorio"))
    if http is None:
        if any(p in minus for p in _PISTAS_USO):           # gh no entiende la orden (una versión sin --hostname, por ejemplo)
            return ErrorInstagram("alojamiento", _TEXTOS_GH["uso"].format(codigo=codigo, detalle=detalle))
        if any(p in minus for p in _PISTAS_RED):
            return ErrorInstagram("alojamiento_red", _TEXTOS_GH["red"])
        return ErrorInstagram("alojamiento_red", _TEXTOS_GH["transitorio"].format(codigo=codigo, detalle=detalle))
    if http == 403:
        if any(p in minus for p in ("rate limit", "abuse")):
            return ErrorInstagram("alojamiento_red", _TEXTOS_GH["limite"])
        return ErrorInstagram("alojamiento", _TEXTOS_GH["permiso"].format(detalle=detalle))
    if http == 404:
        return ErrorInstagram("alojamiento", _TEXTOS_GH["no_encontrado"])
    if http == 413:
        return ErrorInstagram("pieza_rechazada", _TEXTOS_GH["tamano"])
    if http == 422:
        return ErrorInstagram("rechazo_github", _TEXTOS_GH["validacion"].format(detalle=detalle))
    if http in (409, 429) or http >= 500:
        return ErrorInstagram("alojamiento_red", _TEXTOS_GH["servidor"].format(http=http))
    return ErrorInstagram("alojamiento", _TEXTOS_GH["rechazo"].format(http=http, detalle=detalle))


def describir_alojamiento(a: dict) -> str:
    """Una frase para el dueño sobre dónde se suben las imágenes. Con alojamiento real empieza por «repositorio» o
    «carpeta», que es lo que exige el modo «real»."""
    if a.get("tipo") == "github" and a.get("repo"):
        if str(a.get("auth") or "token").strip().lower() == "gh":
            return f"repositorio de GitHub {a['repo']} (con la sesión de la CLI gh de este equipo, sin token)"
        return f"repositorio de GitHub {a['repo']}"
    if a.get("tipo") == "carpeta" and a.get("base_url"):
        return f"carpeta servida en {a['base_url']}"
    return "sin alojamiento configurado (hace falta uno antes de publicar de verdad)"


# ---------------------------------------------------------------------------------------------
# Publicador
# ---------------------------------------------------------------------------------------------

class Publicador:
    """Publica en Instagram. `http` debe tener `request(metodo, url, **kwargs)` como `requests`; `dormir` y `ahora`
    se inyectan para las pruebas. `ahora()` devuelve una fecha con zona horaria."""

    def __init__(self, cred: Credenciales, cfg: ConfigIG, http=requests, dormir=time.sleep, ahora=None,
                 sondear_video=None, buscar_gh=None, correr_gh=None):
        self.cred = cred
        self.cfg = cfg
        self._http = http
        self._dormir = dormir
        self._ahora = ahora or (lambda: datetime.now(timezone.utc))
        self._sondear = sondear_video or _sondear_video
        self._buscar_gh, self._correr_gh = buscar_gh, correr_gh      # None: la CLI gh real (solo se usa con «auth»: «gh»)

    # --- lecturas y mantenimiento (en cualquier modo) ----------------------------------------

    def comprobar(self) -> dict:
        """Perfil de la cuenta (GET /me). Lanza `ErrorInstagram('token')` si el token no vale."""
        self._exigir_token()
        datos = self._llamar("GET", "me", params={"fields": "user_id,username,account_type"})
        return {"user_id": str(datos.get("user_id") or ""), "username": datos.get("username"),
                "account_type": datos.get("account_type"),
                "coincide_user_id": bool(self.cred.user_id) and str(datos.get("user_id")) == str(self.cred.user_id)}

    def renovar_token(self) -> dict:
        """Renueva un token de larga duración (de más de 24 horas y menos de 60 días). Devuelve `{"token", "caduca"}`.

        El token nuevo queda en `self.cred.token`. No se imprime ni se escribe en ningún log.
        """
        self._exigir_token()
        try:
            datos = self._llamar("GET", "refresh_access_token", version=False, bearer=False,
                                 params={"grant_type": "ig_refresh_token", "access_token": self.cred.token})
        except ErrorInstagram as e:
            if "24 hours" in str(e):
                raise ErrorInstagram("renovacion_pronto", _TEXTOS["renovacion_pronto"]) from None
            raise
        nuevo = str(datos.get("access_token") or "")
        if not nuevo:
            raise ErrorInstagram("servidor", _TEXTOS["servidor"] + " (Meta no devolvió un token nuevo).")
        self.cred.token = nuevo
        _registrar(nuevo)
        caduca = self._ahora() + timedelta(seconds=_entero(datos.get("expires_in")) or 60 * 86400)
        return {"token": nuevo, "caduca": _iso(caduca)}

    # --- publicar -----------------------------------------------------------------------------

    def publicar(self, formato: str, archivos, pie: str, estado: dict, guardar, ia: bool = False,
                 alt=None) -> Resultado:
        """Publica una pieza y devuelve su `Resultado`. Lanza `ErrorInstagram` si no sale.

        - `formato`: «unica» (1 JPEG), «carrusel» (de 2 a 10 JPEG) o «video» (1 MP4 de Reels).
        - `estado`: dict de quien llama, que lo guarda en su base de datos. Se modifica in situ. Tras un error,
          pásalo tal cual en la siguiente llamada para retomar. Para una publicación nueva, `{}`.
        - `guardar(estado)`: se llama con cada avance. Si lanza, `publicar` se detiene antes de publicar.
        - `ia`: marca el contenido como generado por IA (`is_ai_generated`).
        - `alt`: texto alternativo. Para una imagen, un str; para un carrusel, una lista (uno por hoja). Se ignora
          en vídeo, porque la API no lo admite allí.

        En modo `simulacion` valida todo y devuelve `Resultado("simulado")` sin tocar la red ni `guardar`. En modo
        `desactivado` lanza sin validar nada.
        """
        modo = self.cfg.modo
        if modo == "desactivado":
            raise ErrorInstagram("desactivado", _TEXTOS["desactivado"])
        if modo == "real" and estado.get("fase") == "publicado" and estado.get("media_id"):
            return Resultado(str(estado["media_id"]), estado.get("permalink"), recuperada=True)
        pie = pie or ""
        rutas, alts = self._validar(formato, archivos, pie, alt)
        if modo == "simulacion":
            return Resultado("simulado", None, False)
        self._exigir_token()
        self._exigir_cuenta()
        if estado.get("contenedor"):
            resultado = self._retomar(formato, estado, guardar, pie)
            if resultado is not None:
                return resultado
            _vaciar_contenedor(estado)                 # caducado o fallido: se empieza de cero
            guardar(estado)
        else:
            self._controlar_cuota()
        urls = self._alojar(rutas, formato, estado, guardar)
        cid = self._crear_contenedor(formato, urls, pie, alts, bool(ia), estado, guardar)
        return self._publicar_y_cerrar(cid, formato, pie, estado, guardar, recuperada=False)

    def _validar(self, formato, archivos, pie: str, alt):
        if formato not in FORMATOS:
            raise ErrorInstagram("formato", f"Formato {formato!r} no válido. Usa «unica», «carrusel» o «video».")
        rutas = [Path(a) for a in archivos]
        if formato == "carrusel":
            if not 2 <= len(rutas) <= MAX_HOJAS:
                raise ErrorInstagram("carrusel", f"{_TEXTOS['carrusel']} Hay {len(rutas)}.")
        elif len(rutas) != 1:
            raise ErrorInstagram("formato", f"«{formato}» lleva exactamente un fichero; hay {len(rutas)}. Para varias "
                                            "imágenes usa «carrusel».")
        _comprobar_pie(pie)
        esperado = FORMATOS[formato]
        for ruta in rutas:
            comprobar_archivo(ruta, esperado, self._sondear)
        if formato == "video" and str(self.cfg.alojamiento.get("tipo") or "").strip().lower() == "github":
            tamano = _tamano(rutas[0])                 # aquí, y no en la subida, para que la simulación también lo vea
            if tamano > MAX_VIDEO_GITHUB:
                peso, limite = (f"{n:,}".replace(",", ".") for n in (tamano, MAX_VIDEO_GITHUB))     # en bytes, en español
                raise ErrorInstagram("pieza_rechazada", f"{rutas[0].name} pesa {peso} bytes. jsDelivr, que sirve los vídeos de "
                                                        f"GitHub, no sirve por defecto los ficheros de más de {limite} bytes "
                                                        "(20 MB): con este alojamiento el vídeo no puede salir. Usa un alojamiento "
                                                        "de tipo «carpeta» (un servidor propio) o un vídeo más ligero.")
        alts = [] if formato == "video" else _alts(alt, len(rutas))
        return rutas, alts

    def _retomar(self, formato: str, estado: dict, guardar, pie: str) -> Resultado | None:
        """Retoma un contenedor de un intento anterior. None si está caducado o fallido: hay que empezar de cero."""
        cid = str(estado["contenedor"])
        codigo = self._codigo(cid)
        if codigo == "PUBLISHED":
            return self._cerrar(None, pie, estado, guardar, recuperada=True)
        if codigo in ("EXPIRED", "ERROR"):
            log.warning("El contenedor anterior está en %s: se prepara de nuevo.", codigo)
            return None
        enviado = _fecha(estado.get("enviado_en"))
        if estado.get("fase") == "publicando" and enviado is not None:
            # La orden de publicar pudo llegar a Instagram antes de la caída: se espera a que se asiente y se mira otra vez.
            restante = VENTANA_REENVIO_S - (self._ahora() - enviado).total_seconds()
            if restante > 0:
                self._dormir(restante)
            codigo = self._codigo(cid)
            if codigo == "PUBLISHED":
                return self._cerrar(None, pie, estado, guardar, recuperada=True)
            if codigo in ("EXPIRED", "ERROR"):
                return None
        if codigo != "FINISHED" and self._esperar(cid, formato) == "PUBLISHED":
            return self._cerrar(None, pie, estado, guardar, recuperada=True)
        return self._publicar_y_cerrar(cid, formato, pie, estado, guardar, recuperada=True)

    def _controlar_cuota(self) -> None:
        """Cuenta las publicaciones de las últimas 24 horas. Si Meta no dice nada útil, sigue sin frenar."""
        try:
            datos = self._llamar("GET", f"{self.cred.user_id}/content_publishing_limit",
                                 params={"fields": "quota_usage,config"})
        except ErrorInstagram as e:
            if e.bloquea or e.reintentable:
                raise
            log.warning("No pude leer el límite de publicaciones (sigo): %s", e.clase)
            return
        items = datos.get("data") or []
        if not items or not isinstance(items[0], dict):
            return
        usado = _entero(items[0].get("quota_usage")) or 0
        config = items[0].get("config") if isinstance(items[0].get("config"), dict) else {}
        total = _entero(config.get("quota_total")) or 0
        if total and usado >= total:
            raise ErrorInstagram("limite", f"{_TEXTOS['limite']} Van {usado} de {total}.")

    def _alojar(self, rutas: list[Path], formato: str, estado: dict, guardar) -> list[str]:
        tamanos = [_tamano(r) for r in rutas]
        if estado.get("urls") and estado.get("formato") == formato and estado.get("tamanos") == tamanos:
            return [str(u) for u in estado["urls"]]         # subidas y comprobadas en un intento anterior: no se vuelven a subir
        if estado.get("formato") != formato or estado.get("tamanos") != tamanos:
            estado.pop("alojadas", None)                   # otros ficheros: lo subido antes no vale para esta pieza
            estado.pop("prefijo", None)
        prefijo = str(estado.get("prefijo") or f"{self._ahora():%Y-%m-%d}/{secrets.token_hex(4)}")
        estado.update(formato=formato, tamanos=tamanos, prefijo=prefijo)
        ya = [str(u) for u in estado.get("alojadas") or []]    # subidas en un intento anterior, aún sin comprobar del todo

        def al_subir(hechas: list[str]) -> None:
            # Se guarda justo después de cada subida, antes de comprobarla: si la comprobación falla, no se vuelve a subir.
            estado.update(alojadas=list(hechas))
            guardar(estado)

        # Se construye en cada llamada: así un token de GitHub pegado de nuevo por el dueño se usa en la siguiente subida.
        alojamiento = Alojamiento(self.cfg.alojamiento, self.cred.github_token, http=self._http, dormir=self._dormir,
                                  buscar_gh=self._buscar_gh, correr_gh=self._correr_gh)
        urls = alojamiento.subir(rutas, prefijo, ya=ya, al_subir=al_subir)
        estado.pop("alojadas", None)
        estado.update(urls=urls, fase="alojado")
        guardar(estado)
        return urls

    def _crear_contenedor(self, formato: str, urls: list[str], pie: str, alts: list, ia: bool, estado: dict,
                          guardar) -> str:
        uid = self.cred.user_id
        if formato == "carrusel":
            hijos = []
            for url, alt in zip(urls, alts):
                cuerpo = {"image_url": url, "is_carousel_item": True}
                if alt:
                    cuerpo["alt_text"] = alt
                hijos.append(_id_de(self._llamar("POST", f"{uid}/media", cuerpo=cuerpo)))
            estado["hijos"] = hijos
            guardar(estado)
            for hijo in hijos:
                self._esperar(hijo, "carrusel")
            cuerpo = {"media_type": "CAROUSEL", "children": ",".join(hijos), "caption": pie}
        elif formato == "video":
            cuerpo = {"media_type": "REELS", "video_url": urls[0], "caption": pie, "share_to_feed": True}
        else:
            cuerpo = {"image_url": urls[0], "caption": pie}
            if alts[0]:
                cuerpo["alt_text"] = alts[0]
        if ia:
            cuerpo["is_ai_generated"] = True
        cid = _id_de(self._llamar("POST", f"{uid}/media", cuerpo=cuerpo))
        estado.update(contenedor=cid, fase="contenedor", creada_en=_iso(self._ahora()))
        guardar(estado)                               # el id del contenedor se guarda antes de cualquier publicación
        self._esperar(cid, formato)
        return cid

    def _publicar_y_cerrar(self, cid: str, formato: str, pie: str, estado: dict, guardar, recuperada: bool) -> Resultado:
        estado.update(fase="publicando", enviado_en=_iso(self._ahora()))
        guardar(estado)                               # si esto falla, no se llega a media_publish
        media_id = self._media_publish(cid, formato)
        return self._cerrar(media_id, pie, estado, guardar, recuperada)

    def _media_publish(self, cid: str, formato: str) -> str | None:
        """Publica el contenedor. Devuelve el id del medio, o None si Instagram ya lo tenía publicado.

        Nunca repite `media_publish` a ciegas. Ante un fallo de red, un 5xx o una respuesta sin id, consulta primero
        el estado del contenedor. Si sigue FINISHED tras esperar, lo reenvía una sola vez. Si no puede leer el
        estado, sale `ambiguo` y el `estado` del llamador conserva el contenedor para la siguiente llamada.
        """
        reenviado = False
        no_listo = 0
        while True:
            try:
                datos = self._llamar("POST", f"{self.cred.user_id}/media_publish", cuerpo={"creation_id": cid},
                                     reintentar=False)
                if datos.get("id"):
                    return str(datos["id"])
            except ErrorInstagram as e:
                if e.clase == "contenedor_no_listo" and no_listo < 2:
                    no_listo += 1                     # rechazada porque no estaba lista: no ha publicado nada
                    if self._esperar(cid, formato) == "PUBLISHED":
                        return None
                    continue
                if e.clase not in ("red", "servidor"):
                    raise                             # rechazada de forma clara: no ha publicado nada
            codigo = self._codigo_o_ambiguo(cid)
            if codigo == "PUBLISHED":
                return None
            if codigo in ("EXPIRED", "ERROR"):
                raise ErrorInstagram("contenedor_caducado", _TEXTOS["contenedor_caducado"])
            if codigo != "FINISHED":
                codigo = self._esperar(cid, formato)  # sigue procesándose: se espera a que termine o se publique
            if codigo == "PUBLISHED":
                return None
            self._dormir(ESPERA_DUDA_S)
            if self._codigo_o_ambiguo(cid) == "PUBLISHED":
                return None
            if reenviado:
                raise ErrorInstagram("ambiguo", _TEXTOS["ambiguo"])
            reenviado = True

    def _cerrar(self, media_id: str | None, pie: str, estado: dict, guardar, recuperada: bool) -> Resultado:
        permalink = None
        if media_id is None:
            media = self._buscar_media(pie, _fecha(estado.get("creada_en")))
            media_id = str(media.get("id") or "desconocido")
            permalink = media.get("permalink") or None
            recuperada = True
            if media_id == "desconocido":
                log.warning("Instagram confirma que la publicación ya estaba publicada, pero no encuentro su id.")
        estado.update(fase="publicado", media_id=media_id, permalink=permalink)
        try:
            guardar(estado)
        except Exception as e:      # ya está publicada: un fallo al guardar no puede contar como fallo de publicación
            log.error("Publicada en Instagram, pero no pude guardar el estado (%s).", type(e).__name__)
        if permalink is None and media_id != "desconocido":
            permalink = self._permalink(media_id)
        log.info("Publicación en Instagram: %s", media_id)
        return Resultado(media_id, permalink, recuperada)

    def _buscar_media(self, pie: str, desde: datetime | None) -> dict:
        """Busca entre las últimas publicaciones la que lleva este pie y es posterior a `desde`."""
        try:
            datos = self._llamar("GET", f"{self.cred.user_id}/media",
                                 params={"fields": "id,caption,permalink,timestamp", "limit": 25})
        except ErrorInstagram as e:
            log.warning("No pude buscar la publicación recuperada (%s).", e.clase)
            return {}
        objetivo = _normalizar(pie)
        for item in datos.get("data") or []:
            if not isinstance(item, dict) or _normalizar(item.get("caption")) != objetivo:
                continue
            marca = _fecha(item.get("timestamp"))
            if desde is not None and marca is not None and marca < desde - timedelta(minutes=10):
                continue
            return item
        return {}

    def _permalink(self, media_id: str) -> str | None:
        try:
            enlace = self._llamar("GET", media_id, params={"fields": "permalink"}).get("permalink")
        except ErrorInstagram as e:
            log.warning("No pude pedir el enlace de la publicación (%s); está en el perfil.", e.clase)
            return None
        return str(enlace) if enlace else None

    # --- estado del contenedor ---------------------------------------------------------------

    def _estado_contenedor(self, cid: str) -> tuple[str, str]:
        datos = self._llamar("GET", cid, params={"fields": "status_code,status"})
        return str(datos.get("status_code") or "").upper(), str(datos.get("status") or "")

    def _codigo(self, cid: str) -> str:
        return self._estado_contenedor(cid)[0]

    def _codigo_o_ambiguo(self, cid: str) -> str:
        try:
            return self._codigo(cid)
        except ErrorInstagram:
            raise ErrorInstagram("ambiguo", _TEXTOS["ambiguo"]) from None

    def _esperar(self, cid: str, formato: str) -> str:
        """Sondea el contenedor hasta FINISHED o PUBLISHED. El límite se mide sumando lo que se duerme, no con el reloj:
        así el bucle termina también con un `dormir` que no espera."""
        limite = self.cfg.espera_video_s if formato == "video" else self.cfg.espera_contenedor_s
        paso, paso_max = (5.0, 30.0) if formato == "video" else (2.0, 15.0)
        dormido = 0.0
        while True:
            codigo, texto = self._estado_contenedor(cid)
            if codigo in ("FINISHED", "PUBLISHED"):
                return codigo
            if codigo == "EXPIRED":
                raise ErrorInstagram("contenedor_caducado", _TEXTOS["contenedor_caducado"])
            if codigo == "ERROR":
                raise _error_de_contenedor(texto)
            if dormido >= limite:
                raise ErrorInstagram("contenedor_no_listo", f"{_TEXTOS['contenedor_no_listo']} Sigue en "
                                                            f"«{codigo or 'sin estado'}» tras {limite:g} s.")
            espera = min(paso, limite - dormido)
            self._dormir(espera)
            dormido += espera
            paso = min(paso * 1.5, paso_max)

    # --- llamadas a Meta ------------------------------------------------------------------------

    def _llamar(self, metodo: str, ruta: str, *, params=None, cuerpo=None, reintentar: bool = True,
                version: bool = True, bearer: bool = True) -> dict:
        """Una llamada a la Graph API. Reintenta red, 5xx y tasa (tres veces, con esperas crecientes).

        El token va en la cabecera Authorization, nunca en la URL. Los errores de red no conservan el texto de la
        excepción, porque trae la URL completa.
        """
        url = f"{GRAPH}/{self.cfg.version}/{ruta}" if version else f"{GRAPH}/{ruta}"
        cabeceras = {"User-Agent": AGENTE}
        if bearer:
            cabeceras["Authorization"] = f"Bearer {self.cred.token}"
        intentos = len(ESPERAS_S) + 1 if reintentar else 1
        for n in range(intentos):
            try:
                r = self._http.request(metodo, url, headers=cabeceras, params=params, json=cuerpo, timeout=TIMEOUT)
            except (requests.RequestException, OSError) as e:
                fallo = ErrorInstagram("red", f"No hay conexión con Instagram ({type(e).__name__}).")
            else:
                datos = _json_de(r)
                if r.status_code < 400 and not datos.get("error"):
                    return datos
                fallo = _error_de_respuesta(r.status_code, datos)
            if fallo.clase not in TRANSITORIAS or n == intentos - 1:
                raise fallo
            log.warning("Instagram: %s. Reintento %d de %d.", fallo.clase, n + 1, len(ESPERAS_S))
            self._dormir(ESPERAS_S[n])
        raise AssertionError("un bucle de reintentos siempre devuelve o lanza")   # pragma: no cover

    def _exigir_token(self) -> None:
        if not self.cred.token:
            raise ErrorInstagram("token", "Falta el token de Instagram. Genera uno en el panel de Meta y conéctalo con "
                                          "«python -m router instagram n1»; después reinicia el router.")

    def _exigir_cuenta(self) -> None:
        if not self.cred.user_id:
            raise ErrorInstagram("cuenta", "Falta el id de la cuenta de Instagram. «python -m router instagram n1» lo "
                                           "busca solo al conectar la cuenta.")


def _vaciar_contenedor(estado: dict) -> None:
    """Quita lo que depende del contenedor. Las direcciones subidas se conservan: no publican nada."""
    for clave in ("contenedor", "hijos", "creada_en", "enviado_en", "fase"):
        estado.pop(clave, None)
