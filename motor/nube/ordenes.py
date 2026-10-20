"""Órdenes que el PC deja en `motor/ordenes/*.json` y la nube ejecuta una sola vez, y las acciones que siguen a una decisión de José.

Sirve para que una pulsación de José en Telegram («Sí, aprobar las tres») haga lo que tiene que hacer sin que el PC ni Claude Code estén
encendidos: la orden `preguntar` lleva dentro lo que se hará según la opción elegida (`si_responde`), y la nube lo hace cuando la decisión
queda respondida POR JOSÉ (botón o texto). Una decisión que caduca y se cierra por su opción por defecto NO ejecuta nada.

Tipos de orden:
    {"tipo": "avisar", "texto": "...", "nivel": "info", "clave": "mi-aviso", "cada": "6h", "cuenta": "n1"}
    {"tipo": "preguntar", "pregunta": "...", "opciones": [["todas", "Sí, las tres"], ["yo", "Las miro yo"]], "caduca": "24h",
     "defecto": null, "texto_libre": false, "cuenta": "n1",
     "si_responde": {"todas": [{"accion": "aprobar", "pieza_id": "n1-f-11-x", "hora": "2026-10-13T13:00:00+02:00"}]}}

Acciones (cada una se ejecuta una vez, con el mismo candado y la misma auditoría que el botón):
    aprobar       {pieza_id, hora?}  Con hora ISO (y futura, a más de 2 minutos) sale a esa hora; si no, en el primer hueco libre.
    rechazar      {pieza_id}
    cambiar_hora  {pieza_id, hora}
    quitar        {pieza_id}         Una aprobada vuelve a «pendiente» (no se pierde): deja libre su hueco para otra pieza.

Orden que se ejecuta sola, sin pregunta (solo cambia texto de piezas que aún no han salido; no aprueba ni programa nada):
    {"tipo": "pies", "cuenta": "n1", "pies": {"n1-f-11-fibra": "texto del pie…"}, "texto": "nota opcional para el aviso a José"}
    {"tipo": "reentregar", "cuenta": "n1", "piezas": ["n1-g-01-cambios"]}           # pendientes que vuelven a mandarse enteras a Telegram
    {"tipo": "reintentar", "cuenta": "n1", "piezas": {"n1-r-04-k": "2026-10-15T19:50"}}     # aprobadas que fallaron por un problema técnico (estado «error»)
    {"tipo": "anular", "cuenta": "n1", "decisiones": [10]}                       # preguntas que ya no valen: se cierran sin ejecutar nada
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from router.hub import fecha_hora_es
from router.textos_panel import corto, plural, rango

MADRID = ZoneInfo("Europe/Madrid")
CLAVE_ORDENES = "nube_ordenes"          # meta del hub: {nombre_de_orden: {"hecha": iso, "decision": n}}
CLAVE_ACCIONES = "nube_acciones"        # meta del hub: {numero_de_decision: {"orden": nombre, "si_responde": {...}, "hecha": bool}}
MINIMO_PROGRAMAR = timedelta(minutes=2)
ACCIONES = ("aprobar", "rechazar", "cambiar_hora", "quitar")
HUMANAS = ("boton", "texto")           # «via» de una respuesta de José; «defecto» es una caducidad, no una respuesta


def _duracion(texto) -> int:
    m = re.fullmatch(r"(\d{1,4})([hm])", str(texto or "24h").strip().lower())
    if not m:
        raise ValueError(f"la duración {texto!r} no vale: pon, por ejemplo, 12h o 90m")
    return int(m.group(1)) * (3600 if m.group(2) == "h" else 60)


def _leer(hub, clave: str) -> dict:
    t = hub.meta_leer(clave)
    return json.loads(t) if t else {}


def _guardar(hub, clave: str, valor: dict) -> None:
    hub.meta_escribir(clave, json.dumps(valor, ensure_ascii=False, sort_keys=True))


def _hora(texto) -> datetime | None:
    if not texto:
        return None
    t = datetime.fromisoformat(str(texto))
    return t.replace(tzinfo=MADRID) if t.tzinfo is None else t


def validar_orden(o: dict) -> None:
    """Falla con ValueError si la orden no se puede ejecutar: así una orden rota no se queda a medias."""
    tipo = o.get("tipo")
    if tipo == "avisar":
        if not str(o.get("texto") or "").strip():
            raise ValueError("avisar sin texto")
        return
    if tipo == "pies":
        pies = o.get("pies")
        if not isinstance(pies, dict) or not pies:
            raise ValueError("pies sin ningún pie")
        for pid, texto in pies.items():
            if not str(pid).strip() or not isinstance(texto, str) or not texto.strip():
                raise ValueError(f"el pie de «{pid}» está vacío")
            if len(texto) > 2200:
                raise ValueError(f"el pie de «{pid}» tiene {len(texto)} caracteres (el máximo de Instagram es 2200)")
            if len(re.findall(r"#\w+", texto)) > 5:
                raise ValueError(f"el pie de «{pid}» lleva más de 5 hashtags")
        return
    if tipo == "acciones":
        lista = o.get("lista")
        if not isinstance(lista, list) or not lista:
            raise ValueError("acciones sin ninguna acción")
        for a in lista:
            if a.get("accion") not in ACCIONES:
                raise ValueError(f"acción desconocida: {a.get('accion')!r}")
            if not a.get("pieza_id"):
                raise ValueError("una acción no dice la pieza")
            if a["accion"] == "cambiar_hora" and not a.get("hora"):
                raise ValueError("cambiar_hora necesita hora")
            _hora(a.get("hora"))
        return
    if tipo == "reentregar":
        if not isinstance(o.get("piezas"), list) or not o["piezas"]:
            raise ValueError("reentregar sin ninguna pieza")
        return
    if tipo == "anular":
        if not isinstance(o.get("decisiones"), list) or not o["decisiones"] or not all(isinstance(n, int) for n in o["decisiones"]):
            raise ValueError("anular necesita la lista de números de decisión")
        return
    if tipo == "reintentar":
        piezas = o.get("piezas")
        if not isinstance(piezas, dict) or not piezas:
            raise ValueError("reintentar sin ninguna pieza")
        for pid, hora in piezas.items():
            if not str(pid).strip():
                raise ValueError("reintentar con una pieza sin id")
            _hora(hora)
        return
    if tipo != "preguntar":
        raise ValueError(f"tipo de orden desconocido: {tipo!r}")
    if not str(o.get("pregunta") or "").strip():
        raise ValueError("preguntar sin pregunta")
    claves = {str(c).strip().lower() for c, _ in o.get("opciones", [])}
    for clave, acciones in (o.get("si_responde") or {}).items():
        if str(clave).lower() not in claves:
            raise ValueError(f"si_responde habla de la opción «{clave}», que no existe")
        for a in acciones:
            if a.get("accion") not in ACCIONES:
                raise ValueError(f"acción desconocida: {a.get('accion')!r}")
            if not a.get("pieza_id"):
                raise ValueError("una acción no dice la pieza")
            if a["accion"] == "cambiar_hora":
                if not a.get("hora"):
                    raise ValueError("cambiar_hora necesita hora")
            _hora(a.get("hora"))


def _cambiar_pies(hub, ad, nombre: str, o: dict, log) -> dict:
    """La orden «pies»: cambia el pie de piezas que aún no han salido y cuenta a José qué se hizo. Devuelve lo que se anota como hecho."""
    cambiados, fallos = [], {}
    for pid, texto in o["pies"].items():
        motivo = ad.cambiar_pie_en_cola(str(pid), texto, origen=f"nube:orden-{nombre}")
        if motivo is None:
            cambiados.append(str(pid))
        else:
            fallos[str(pid)] = motivo
    L = [f"✍️ He renovado el pie de {plural(len(cambiados), 'publicación', 'publicaciones')} (gancho, pregunta, hashtags; sin la línea de fuentes)."]
    if o.get("texto"):
        L.append(str(o["texto"]))
    if fallos:
        L.append(f"⚠️ No se pudo en {len(fallos)}: " + "; ".join(f"{p} ({corto(m, 70)})" for p, m in list(fallos.items())[:6]))
    hub.crear_aviso("\n".join(L), "info", f"pies-{nombre}"[:60], o.get("cuenta") or "n1", None)
    log(f"orden {nombre}: pies cambiados {len(cambiados)}, fallos {len(fallos)}")
    return {"cambiados": cambiados, "fallos": fallos}


def _reintentar(hub, ad, nombre: str, o: dict, log) -> dict:
    """La orden «reintentar»: una pieza que José ya aprobó y que falló por un problema técnico (estado «error») vuelve a la cola a la hora que
    se diga. No aprueba nada nuevo: solo repite lo aprobado. Cuenta a José qué se hizo."""
    admin = hub.admin_id()
    hechas, fallos = [], {}
    for pid, hora in o["piezas"].items():
        r = _fila(ad, str(pid))
        if r is None:
            fallos[str(pid)] = "no está en la cola"
            continue
        h = _hora(hora)
        if h is not None and h.astimezone(timezone.utc) <= ad.ahora().astimezone(timezone.utc) + MINIMO_PROGRAMAR:
            h = None                                               # la hora ya pasó: primer hueco libre
        motivo = ad.reintentar(r["id"], admin, h, origen=f"nube:orden-{nombre}") if admin is not None else "sin administrador"
        if motivo:
            fallos[str(pid)] = motivo
        else:
            hechas.append(str(pid))
    L = []
    if hechas:
        L.append("🔁 " + plural(len(hechas), "publicación aprobada vuelve", "publicaciones aprobadas vuelven") + " a la cola tras un fallo técnico (faltaba ffmpeg en la nube): "
                 + ", ".join(f"{p} → {ad.resumen_decision(_fila(ad, p)['id']).strip()}" for p in hechas))
    if fallos:
        L.append("⚠️ No se pudo reintentar: " + "; ".join(f"{p} ({corto(m, 80)})" for p, m in fallos.items()))
    hub.crear_aviso("\n".join(L) or "Nada que reintentar.", "info", f"reintentar-{nombre}"[:60], o.get("cuenta") or "n1", None)
    log(f"orden {nombre}: reintentadas {len(hechas)}, fallos {len(fallos)}")
    return {"reintentadas": hechas, "fallos": fallos}


def _acciones(hub, ad, nombre: str, o: dict, log) -> dict:
    """La orden «acciones»: las mismas acciones que una decisión (aprobar, quitar, cambiar_hora, rechazar) pero que se hacen sin pregunta.
    Solo existe porque José lo autorizó por escrito (15-oct-2026, «aprueba en automático los cambios que consideres precisos»); la orden
    lleva `autoriza` con quién y cuándo. Cuenta a José qué se hizo."""
    admin = hub.admin_id()
    lista = o["lista"]
    if admin is None:
        return {"fallos": ["sin administrador"]}
    lineas = [_hacer(ad, a, admin, f"nube:acciones-{nombre}") for a in lista]
    texto = _resumen(nombre, ad, lista, lineas)
    nota = str(o.get("texto") or "").strip()
    malas = [l for l in lineas if l.startswith("⚠️")]
    if not o.get("silencio") or malas:          # una recolocación rutinaria (`silencio: true`) no avisa a José salvo que algo haya fallado (20-oct)
        hub.crear_aviso((nota + "\n" if nota else "") + texto, "info", f"acciones-{nombre}"[:60], o.get("cuenta") or "n1", None)
    log(f"orden {nombre}: {len(lista)} acciones, {len(malas)} sin hacer")
    return {"acciones": len(lista), "fallos": [l[:160] for l in malas]}


def _reentregar(ad, o: dict, log) -> dict:
    """La orden «reentregar»: las piezas pendientes vuelven a la lista de por entregar, y el router las manda otra vez a Telegram entera
    (álbum, pie y botones). Solo toca pendientes."""
    hechas = []
    for pid in o["piezas"]:
        cur = ad.db.execute("UPDATE piezas SET entregada=0 WHERE pieza_id=? AND estado='pendiente'", (str(pid),))
        if cur.rowcount:
            hechas.append(str(pid))
    ad.db.commit()
    log(f"orden reentregar: {len(hechas)} piezas")
    return {"reentregadas": hechas}


def procesar_ordenes(hub, carpeta: Path, log=print, ad=None) -> int:
    """Ejecuta las órdenes de `carpeta` que aún no se han hecho. Devuelve cuántas ejecutó. `ad` es el adaptador de la cuenta: sin él, las
    órdenes que tocan piezas (`pies`) se dejan para otra pasada."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return 0
    hechas = _leer(hub, CLAVE_ORDENES)
    acciones = _leer(hub, CLAVE_ACCIONES)
    n = 0
    for ruta in sorted(carpeta.glob("*.json")):
        nombre = ruta.stem
        if nombre in hechas:
            continue
        try:
            o = json.loads(ruta.read_text(encoding="utf-8"))
            validar_orden(o)
        except (OSError, ValueError, TypeError) as exc:
            log(f"orden {nombre}: no vale ({type(exc).__name__}: {exc}); se marca como hecha para no repetirla")
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": str(exc)[:200]}
            _guardar(hub, CLAVE_ORDENES, hechas)
            continue
        cuenta = o.get("cuenta") or "n1"
        if o["tipo"] == "avisar":
            cada = _duracion(o["cada"]) if o.get("cada") else None
            hub.crear_aviso(o["texto"], o.get("nivel", "info"), o.get("clave"), cuenta, cada)
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        elif o["tipo"] == "acciones":
            if ad is None:
                continue
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), **_acciones(hub, ad, nombre, o, log)}
        elif o["tipo"] == "anular":
            anuladas = []
            for n in o["decisiones"]:
                if hub.anular_decision(int(n)):
                    anuladas.append(int(n))
                    if str(n) in acciones:
                        acciones[str(n)]["hecha"] = True           # una pregunta anulada no ejecuta nada si alguien pulsa un botón viejo
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "anuladas": anuladas}
        elif o["tipo"] == "reentregar":
            if ad is None:
                continue
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), **_reentregar(ad, o, log)}
        elif o["tipo"] == "reintentar":
            if ad is None:
                continue
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), **_reintentar(hub, ad, nombre, o, log)}
        elif o["tipo"] == "pies":
            if ad is None:
                continue                                           # sin adaptador no se puede: se queda para la siguiente pasada
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), **_cambiar_pies(hub, ad, nombre, o, log)}
        else:
            try:
                numero = hub.crear_decision(o["pregunta"], [(c, t) for c, t in o["opciones"]], cuenta, _duracion(o.get("caduca", "24h")),
                                            defecto=o.get("defecto"), texto_libre=bool(o.get("texto_libre")), origen="nube", larga=True)
            except ValueError as exc:
                log(f"orden {nombre}: la decisión no se pudo crear ({exc}); se marca como hecha para no repetirla")
                hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": str(exc)[:200]}
                _guardar(hub, CLAVE_ORDENES, hechas)
                continue
            hechas[nombre] = {"hecha": datetime.now(timezone.utc).isoformat(timespec="seconds"), "decision": numero}
            if o.get("si_responde"):
                acciones[str(numero)] = {"orden": nombre, "si_responde": {str(k).lower(): v for k, v in o["si_responde"].items()}, "hecha": False}
        _guardar(hub, CLAVE_ORDENES, hechas)
        _guardar(hub, CLAVE_ACCIONES, acciones)
        log(f"orden {nombre}: ejecutada ({o['tipo']})")
        n += 1
    return n


def _fila(ad, pieza_id: str):
    return ad.db.execute("SELECT * FROM piezas WHERE pieza_id=?", (pieza_id,)).fetchone()


def _hacer(ad, accion: dict, admin: int, origen: str) -> str:
    """Ejecuta una acción y devuelve una línea que cuenta qué pasó (con el motivo si no se pudo)."""
    pid = accion["pieza_id"]
    r = _fila(ad, pid)
    if r is None:
        return f"⚠️ {pid}: no está en la cola."
    nombre = corto(r["titulo"], 40)                                # con el título se entiende; el id de la pieza no dice nada a José
    ahora = ad.ahora()
    hora = _hora(accion.get("hora"))
    if accion["accion"] == "aprobar":
        if r["estado"] in ("aprobada", "publicando", "publicada"):          # José ya la aprobó por otro camino (el panel): no es un fallo
            quedan = "publicada" if r["estado"] == "publicada" else "aprobada"
            return f"✅ {nombre}: ya estaba {quedan}.{ad.resumen_decision(r['id']) if quedan == 'aprobada' else ''}"
        if hora is not None and hora.astimezone(timezone.utc) <= ahora.astimezone(timezone.utc) + MINIMO_PROGRAMAR:
            hora = None                                            # la hora ya pasó (o falta nada): primer hueco libre
        motivo = ad.aprobar(r["id"], admin, para_admin=True, hora=hora, origen=origen)
        if motivo:
            return f"⚠️ {nombre}: no se aprobó. {motivo}"
        return f"✅ {nombre}: aprobada.{ad.resumen_decision(r['id'])}"
    if accion["accion"] == "quitar":
        if r["estado"] == "pendiente":
            return f"↩️ {nombre}: ya estaba fuera de la cola."
        motivo = ad.quitar_de_cola(r["id"], admin, origen=origen)
        return f"⚠️ {nombre}: no se quitó de la cola. {motivo}" if motivo else f"↩️ {nombre}: fuera de la cola (queda pendiente, no se pierde)."
    if accion["accion"] == "rechazar":
        if r["estado"] == "rechazada":
            return f"❌ {nombre}: ya estaba rechazada."
        motivo = ad.rechazar(r["id"], admin)
        return f"⚠️ {nombre}: no se rechazó. {motivo}" if motivo else f"❌ {nombre}: rechazada."
    motivo = ad.cambiar_hora(r["id"], hora, admin, origen=origen)
    return f"⚠️ {nombre}: la hora no cambió. {motivo}" if motivo else f"🕐 {nombre}: nueva hora.{ad.resumen_decision(r['id'])}"


def _resumen(numero, ad, lista: list, lineas: list) -> str:
    """Lo que se cuenta a José de una pregunta contestada. Con pocas acciones, una línea por pieza; con un lote (20 o 40 piezas) no cabría en
    un mensaje de Telegram, así que se cuenta en una frase y solo se detallan los fallos."""
    if len(lineas) <= 5:
        return f"Hecho (pregunta n.º {numero}):\n" + "\n".join(lineas)
    malas = [l for l in lineas if l.startswith("⚠️")]
    horas = []
    for a, l in zip(lista, lineas):
        if not l.startswith("⚠️") and a["accion"] in ("aprobar", "cambiar_hora"):
            r = _fila(ad, a["pieza_id"])
            if r is not None and r["programada"]:
                horas.append(datetime.fromisoformat(r["programada"]).astimezone(ad.zona))
    L = [f"Hecho (pregunta n.º {numero}): {len(lineas) - len(malas)} de {len(lineas)} acciones."]
    if horas:
        L.append(f"✅ {plural(len(horas), 'pieza programada', 'piezas programadas')}, del {rango(min(horas), max(horas))}. "
                 "Se publican solas aunque tu PC esté apagado.")
    if malas:
        L += ["", f"⚠️ No se {'pudo' if len(malas) == 1 else 'pudieron'} hacer {len(malas)}:"] + [f"· {corto(l[2:].strip(), 130)}" for l in malas[:8]]
        if len(malas) > 8:
            L.append(f"… y {len(malas) - 8} más.")
    return "\n".join(L)


def ejecutar_acciones(hub, ad, router, log=print) -> int:
    """Ejecuta las acciones de las decisiones ya respondidas por José. Cada decisión se ejecuta una sola vez. Devuelve cuántas decisiones tocó."""
    acciones = _leer(hub, CLAVE_ACCIONES)
    admin = hub.admin_id()
    tocadas = 0
    for numero, spec in sorted(acciones.items(), key=lambda kv: int(kv[0])):
        if spec.get("hecha"):
            continue
        d = hub.decision(int(numero))
        if d is None or d["estado"] == "pendiente":
            continue
        spec["hecha"] = True
        tocadas += 1
        if d["estado"] != "respondida" or d["respuesta_via"] not in HUMANAS or admin is None:
            log(f"decisión {numero}: cerrada sin respuesta de José ({d['estado']}/{d['respuesta_via']}): no se hace nada")
            continue
        lista = spec["si_responde"].get(str(d["respuesta_clave"]).lower(), [])
        if not lista:
            continue
        lineas = [_hacer(ad, a, admin, f"nube:decision-{numero}") for a in lista]
        router._avisar_admins(_resumen(numero, ad, lista, lineas))
        log(f"decisión {numero}: {len(lista)} acciones ejecutadas")
    _guardar(hub, CLAVE_ACCIONES, acciones)
    return tocadas
