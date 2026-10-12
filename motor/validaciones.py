"""Validación interna de los hechos del banco por el dueño de la cuenta (José).

Lo que hace: guarda, por cuenta, qué decisión ha tomado José sobre cada hecho (validado, con reparos o no usar), y superpone esa
decisión sobre el banco cada vez que se carga. No es una firma profesional pública ni sustituye la revisión que exige la ley
para la publicidad sanitaria: es el control interno del dueño, y solo se muestra dentro del bot.

Cada decisión guarda la «huella» de la versión del hecho que se validó (enunciado, veredicto, matiz, fuente, url, año, la
verificación primaria, el riesgo y las poblaciones en riesgo). Si el hecho cambia después, la decisión deja de valer y el hecho
vuelve a pendiente. La titulación de José solo se guarda en el registro (campo «titulacion»); no entra en el banco que se superpone.

Almacén: `datos/validaciones/<cuenta>.json` (se crea al primer uso; `VIVAX_VALIDACIONES` lo puede cambiar, p. ej. en pruebas).
Auditoría, solo para añadir: `datos/validaciones/<cuenta>.log.jsonl`.

Si el almacén existe pero no se puede leer, se falla cerrado: ninguna lectura lo trata como vacío (un «no usar» no puede
desaparecer en silencio) y ninguna escritura lo sobrescribe.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
NOMBRE = "José"
TITULACION = "graduado en Nutrición Humana y Dietética"      # solo en el registro de la decisión; nunca en el banco ni en un texto
ESTADOS = ("validado", "con_reparos", "no_usar")
DECIDIDOS = ("validado", "con_reparos", "no_usar")
MOTIVO_CAMBIO = "el hecho cambió desde que lo validaste"
MAX_NOTA = 500
CUENTA = re.compile(r"[a-z][0-9]{1,3}")                   # el mismo formato que el router (CODIGO_CUENTA)
RIESGO_ORDEN = {"alto": 0, "medio": 1, "bajo": 2}
BASE_PENDIENTE = "pendiente: dietista-nutricionista colegiado"

_cerrojo = threading.RLock()                              # lectura, cambio y escritura van juntos: dos pulsaciones no se pisan


class ErrorValidaciones(ValueError):
    """El almacén de una cuenta existe pero no se puede leer. Nada se da por validado."""


class HechoCambiado(Exception):
    """El hecho ha cambiado desde que se mostró: la decisión no se guarda."""


# ------------------------------------------------------------------ rutas
def carpeta() -> Path:
    return Path(os.environ.get("VIVAX_VALIDACIONES") or RAIZ / "datos" / "validaciones")


def _ruta(cuenta: str) -> Path:
    if not isinstance(cuenta, str) or not CUENTA.fullmatch(cuenta):
        raise ValueError(f"cuenta no válida para las validaciones: {cuenta!r}")
    return carpeta() / f"{cuenta}.json"


# ------------------------------------------------------------------ huella
def huella(h: dict) -> str:
    """Huella de la versión del hecho tal como se valida. Se calcula sobre el hecho ya cargado (las voces VOZ-... vienen de hecho_de_voz).
    Incluye lo que decide si el hecho puede salir (riesgo_publicar) y el aviso de salud (poblaciones_en_riesgo)."""
    campos = [h.get("enunciado", ""), h.get("veredicto", ""), h.get("matiz", ""), h.get("fuente", ""), h.get("url", ""), h.get("anio"),
              h.get("verificacion_primaria") or {}, h.get("riesgo_publicar", ""), h.get("poblaciones_en_riesgo") or []]
    return hashlib.sha256(json.dumps(campos, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def version(h: dict) -> str:
    """Versión de la tarjeta de un hecho: su huella y la decisión que enseña (estado, estado anterior, fecha, nota y «no usar»).
    Un botón solo vale si su versión sigue siendo la actual: así un mensaje viejo no cambia una decisión que ya no es la que se ve."""
    decision = [h.get("validacion"), h.get("validacion_previa"), h.get("validacion_fecha"), h.get("validacion_nota"), bool(h.get("no_usar"))]
    return hashlib.sha256(json.dumps([huella(h), decision], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ lectura y escritura
def leer(cuenta: str) -> dict:
    """Las decisiones de la cuenta ({hecho_id: registro}). Sin almacén, vacío. Con un almacén roto, lanza ErrorValidaciones."""
    ruta = _ruta(cuenta)
    if not ruta.is_file():
        return {}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ErrorValidaciones(f"no puedo leer las validaciones de {cuenta} ({ruta.name}); no uso ninguna hasta arreglarlo") from None
    if not isinstance(datos, dict):
        raise ErrorValidaciones(f"las validaciones de {cuenta} ({ruta.name}) no tienen la forma esperada; no uso ninguna hasta arreglarlo")
    return datos


def _escribir(ruta: Path, datos: dict) -> None:
    """Escritura atómica: primero un fichero temporal completo y luego el reemplazo. En Windows el reemplazo puede esperar a otro proceso."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    for intento in range(6):
        try:
            os.replace(tmp, ruta)
            return
        except PermissionError:
            if intento == 5:
                raise
            time.sleep(0.2 * (intento + 1))


def _auditar(cuenta: str, por: int, accion: str, hecho: str, previo, nuevo, huella_: str, nota: str) -> None:
    linea = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "por": por, "accion": accion, "hecho": hecho,
             "estado_previo": previo, "estado_nuevo": nuevo, "huella": huella_, "nota": nota}
    ruta = carpeta() / f"{cuenta}.log.jsonl"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "a", encoding="utf-8", newline="\n") as f:          # solo se añade; nunca se reescribe
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def limpiar_nota(texto: str) -> str:
    """La nota de reparos: una sola línea, sin caracteres de control y de 500 caracteres como mucho."""
    t = " ".join("".join(c if c.isprintable() else " " for c in str(texto)).split())
    if not t:
        raise ValueError("la nota está vacía")
    if len(t) > MAX_NOTA:
        raise ValueError(f"la nota tiene {len(t)} caracteres y el máximo es {MAX_NOTA}")
    return t


def registrar(cuenta: str, hecho: dict, estado: str, por: int, hoy: date, nota: str = "", esperada: str | None = None) -> dict:
    """Guarda la decisión sobre la versión actual del hecho. Repetir la misma decisión no escribe nada. `esperada` es la huella
    que vio quien decide: si el hecho ya no es esa versión, no se guarda (HechoCambiado)."""
    if estado not in ESTADOS:
        raise ValueError(f"estado no válido: {estado!r}")
    nota = limpiar_nota(nota) if estado == "con_reparos" else ""
    hid, h = hecho["id"], huella(hecho)
    with _cerrojo:
        datos = leer(cuenta)
        if esperada is not None and esperada != h:
            raise HechoCambiado(hid)
        previo = datos.get(hid) if isinstance(datos.get(hid), dict) else None
        if previo and previo.get("estado") == estado and previo.get("huella") == h and previo.get("nota", "") == nota:
            return previo                                       # doble pulsación: no se repite ni se audita
        reg = {"estado": estado, "por": por, "titulacion": TITULACION, "fecha": hoy.isoformat(), "nota": nota, "huella": h}
        datos[hid] = reg
        _escribir(_ruta(cuenta), datos)
        _auditar(cuenta, por, "decision", hid, previo.get("estado") if previo else None, estado, h, nota)
    return reg


def retirar(cuenta: str, hecho_id: str, por: int) -> bool:
    """Quita la decisión de un hecho: vuelve a pendiente. Queda en la auditoría."""
    with _cerrojo:
        datos = leer(cuenta)
        previo = datos.pop(hecho_id, None)
        if not isinstance(previo, dict):
            return False
        _escribir(_ruta(cuenta), datos)
        _auditar(cuenta, por, "retirada", hecho_id, previo.get("estado"), None, previo.get("huella", ""), "")
    return True


# ------------------------------------------------------------------ superposición sobre el banco
def superponer(banco: dict, cuenta: str) -> dict:
    """Copia del banco con la decisión de José de la cuenta aplicada. Campos nuevos por hecho:
    validacion (None, validado, con_reparos, no_usar u obsoleta), vigente, no_usar (bloquea citarlo), validacion_fecha,
    validacion_nota y, si cambió, validacion_motivo. `estado_validacion` solo deja de decir «pendiente» con un validado completo."""
    regs = leer(cuenta)
    sal = {}
    for hid, h in banco.items():
        h2 = dict(h, validacion=None, vigente=False, no_usar=False)
        r = regs.get(hid)
        if isinstance(r, dict) and r.get("estado") in ESTADOS:
            base = str(h.get("estado_validacion", ""))
            fecha = str(r.get("fecha", ""))
            if r.get("huella") == huella(h):
                st = r["estado"]
                h2.update(validacion=st, vigente=True, validacion_fecha=fecha, validacion_nota=str(r.get("nota", "")), no_usar=(st == "no_usar"))
                if "pendiente" in base.lower():
                    if st == "validado":
                        if base.strip().lower() == BASE_PENDIENTE:
                            h2["estado_validacion"] = f"validado: {NOMBRE}, {fecha}"
                        else:                                    # p. ej. «y abogado»: lo de José no basta, sigue pendiente
                            h2["estado_validacion"] = f"{base} (validado por {NOMBRE}, {fecha}; falta lo demás)"
                    elif st == "con_reparos":
                        h2["estado_validacion"] = f"{base} (con reparos de {NOMBRE}, {fecha}: {r.get('nota', '')})"
                    else:
                        h2["estado_validacion"] = f"{base} (no usar: decisión de {NOMBRE}, {fecha})"
            else:
                h2.update(validacion="obsoleta", validacion_previa=r.get("estado"), validacion_motivo=MOTIVO_CAMBIO)
                if r.get("estado") == "no_usar":                 # un «no usar» no se cae con una errata: sigue bloqueado hasta revisarlo
                    h2["no_usar"] = True
                    h2["validacion_motivo"] = MOTIVO_CAMBIO + "; sigue sin usarse hasta que lo revises"
        sal[hid] = h2
    return sal


def en_cola(h: dict) -> bool:
    """Un hecho espera decisión si su texto sigue pendiente y no tiene una decisión vigente de José."""
    return "pendiente" in str(h.get("estado_validacion", "")).lower() and not (h.get("vigente") and h.get("validacion") in DECIDIDOS)


def cola(sup: dict, en_uso=frozenset()) -> list[str]:
    """Ids que esperan decisión, en el orden de revisión: primero los que usan piezas en cola o aprobadas, luego riesgo alto, medio y bajo."""
    orden = {i: k for k, i in enumerate(sup)}
    ids = [i for i, h in sup.items() if en_cola(h)]
    ids.sort(key=lambda i: (0 if i in en_uso else 1, RIESGO_ORDEN.get(sup[i].get("riesgo_publicar"), 1), orden[i]))
    return ids


def resumen(sup: dict, en_uso=frozenset()) -> dict:
    c = {"validados": 0, "con_reparos": 0, "no_usar": 0, "obsoletos": 0, "pendientes": len(cola(sup, en_uso)), "con_mas_requisitos": 0}
    for h in sup.values():
        v = h.get("validacion")
        if v == "obsoleta":
            c["obsoletos"] += 1
        elif h.get("vigente") and v == "validado":
            c["validados"] += 1
            if "pendiente" in str(h.get("estado_validacion", "")).lower():
                c["con_mas_requisitos"] += 1
        elif h.get("vigente") and v == "con_reparos":
            c["con_reparos"] += 1
        elif h.get("vigente") and v == "no_usar":
            c["no_usar"] += 1
    return c
