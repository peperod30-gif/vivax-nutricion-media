"""Todo lo que José lee en el panel de Telegram, en un solo sitio para poder revisarlo de un vistazo (13-oct-2026).

Reglas de estilo: castellano de España, tuteo, frases cortas, cero jerga («decisión», «candado», «hueco», «programada a mano»), iconos de
estado fijos y ninguna raya larga. Las funciones son puras: reciben datos ya calculados y devuelven texto (y a veces botones); no tocan la
base ni Telegram, así se pueden probar y mirar sin montar nada (python -X utf8 -m nube.panel_demo).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

ICONO = {"pendiente": "⏳", "aprobada": "✅", "publicando": "✅", "publicada": "📤", "rechazada": "❌", "error": "⚠️"}
ESTADO_LLANO = {"pendiente": "Por aprobar", "aprobada": "Programada", "publicando": "Publicándose ahora", "publicada": "Publicada",
                "rechazada": "Rechazada", "error": "Con problema"}
VOLVER = ("⬅ Panel", "pn:p")


# ------------------------------------------------------------------ fechas y títulos
def _l(dt: datetime) -> datetime:
    return dt.astimezone(MADRID)


def dia(dt: datetime) -> str:
    """«mié 15-oct»."""
    d = _l(dt)
    return f"{DIAS[d.weekday()]} {d.day}-{MESES[d.month - 1]}"


def hora(dt: datetime) -> str:
    return f"{_l(dt):%H:%M}"


def cuando(dt: datetime) -> str:
    """«mié 15-oct a las 14:00»."""
    return f"{dia(dt)} a las {hora(dt)}"


def cuando_rel(dt: datetime, hoy: date) -> str:
    """«hoy a las 16:00», «mañana a las 08:00» o «mié 15-oct a las 14:00»."""
    d = _l(dt).date()
    if d == hoy:
        return f"hoy a las {hora(dt)}"
    if (d - hoy).days == 1:
        return f"mañana a las {hora(dt)}"
    return cuando(dt)


def dia_de(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day}-{MESES[d.month - 1]}"


def corto(texto, n: int = 40) -> str:
    """Un título en una sola línea y de largo acotado."""
    t = re.sub(r"\s+", " ", str(texto or "")).strip()
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def rango(a: datetime, b: datetime) -> str:
    """«mié 14-oct al dom 2-nov» (o solo el día si es el mismo)."""
    return dia(a) if dia(a) == dia(b) else f"{dia(a)} al {dia(b)}"


def _rango_semana(d0: date, d1: date) -> str:
    if d0.month == d1.month:
        return f"{d0.day} al {d1.day} {MESES[d1.month - 1]}"
    return f"{d0.day} {MESES[d0.month - 1]} al {d1.day} {MESES[d1.month - 1]}"


def plural(n: int, uno: str, varios: str) -> str:
    return f"{n} {uno if n == 1 else varios}"


def primeras_lineas(pie: str, n: int = 2, largo: int = 150) -> str:
    """Las primeras líneas con texto de un pie, sin cortar a mitad de palabra."""
    lineas = [l.strip() for l in str(pie or "").splitlines() if l.strip()]
    t = " ".join(lineas[:1]) if n == 1 else "\n".join(lineas[:n])
    return t if len(t) <= largo * n else t[:largo * n - 1].rstrip() + "…"


# ------------------------------------------------------------------ el motivo de un fallo, en llano
def motivo_llano(error) -> str:
    """Lo que ha pasado cuando una pieza no sale, sin jerga ni instrucciones de ordenador."""
    t = str(error or "").lower()
    if not t:
        return "Instagram no la dejó publicar."
    if "token" in t or "permiso" in t or "oauth" in t or "autoriz" in t:
        return "Instagram ha rechazado el acceso de la cuenta. Hay que renovarlo desde el PC."
    if "alojamiento" in t or "github" in t or "imagen" in t or "descarg" in t:
        return "No se pudieron subir las imágenes para publicarla."
    if "no usar" in t or "revisado" in t or "validar" in t or "hechos" in t:
        return "Tiene un dato sin revisar o marcado como no usable."
    if "fichero" in t or "json" in t:
        return "Falta un fichero de la pieza."
    if "límite" in t or "limite" in t or "tasa" in t or "rate" in t:
        return "Instagram ha puesto un límite de publicaciones y hay que esperar."
    if "local" in t or "disco" in t:
        return "Falló algo en el equipo que publica."
    return "Instagram no respondió o no la aceptó."


# ------------------------------------------------------------------ el panel
def panel(hoy: date, ahora: datetime, siguiente: tuple[datetime, str] | None, pendientes: int, programadas: int, hasta: datetime | None,
          dias_cola: int, en_pausa: bool, pasada_min: int | None, nota: str = "") -> tuple[str, list]:
    L = [f"📍 Vivax Nutrición · {dia(ahora)}, {hora(ahora)}", ""]
    if nota:
        L += [nota, ""]
    if siguiente:
        L.append(f"Sale a continuación: {cuando_rel(siguiente[0], hoy)} · {corto(siguiente[1], 44)}")
    else:
        L.append("No hay nada programado.")
    L.append(f"⏳ Por aprobar: {pendientes}")
    L.append(f"✅ Programadas: {programadas}" + (f" (hasta el {dia(hasta)})" if hasta else ""))
    if en_pausa:
        L.append("⏸ Las publicaciones están en pausa.")
    if programadas and dias_cola < 3:
        L.append(f"🧭 A la cola le quedan solo {plural(dias_cola, 'día', 'días')}.")
    elif not programadas and not pendientes:
        L.append("🧭 La cola está vacía.")
    if pasada_min is not None:
        L.append("☁️ Nube: conectada." if pasada_min <= 1 else f"☁️ Nube: última pasada hace {pasada_min} min." if pasada_min <= 12
                 else f"⚠️ La nube no da señales desde hace {pasada_min} min.")
    botones = [[(f"⏳ Aprobar pendientes ({pendientes})", "pn:pe")], [("📅 Calendario", "pn:cal:0"), ("📤 Publicadas", "pn:pu")], [("⚙️ Más", "pn:mas")]]
    return "\n".join(L), botones


# ------------------------------------------------------------------ aprobar pendientes
def pendientes(items: list[dict], hoy: date) -> tuple[str, list]:
    """items: {titulo, prevista (datetime|None)}, ya ordenados."""
    n = len(items)
    if not n:
        return "⏳ No hay nada por aprobar. Todo está al día.", [[VOLVER]]
    con = [i["prevista"] for i in items if i["prevista"]]
    cab = f"⏳ Por aprobar: {plural(n, 'pieza', 'piezas')}" + (f" (del {rango(min(con), max(con))})" if con else "")
    L = [cab, ""]
    semanas: dict[date, list[dict]] = {}
    sin_dia = []
    for i in items:
        if i["prevista"]:
            d = _l(i["prevista"]).date()
            lunes = date.fromordinal(d.toordinal() - d.weekday())
            semanas.setdefault(lunes, []).append(i)
        else:
            sin_dia.append(i)
    lineas = 0
    for lunes in sorted(semanas):
        L.append(f"Semana del {_rango_semana(lunes, date.fromordinal(lunes.toordinal() + 6))}")
        for i in semanas[lunes]:
            if lineas >= 45:
                break
            L.append(f"· {DIAS[_l(i['prevista']).weekday()]} {_l(i['prevista']).day} · {hora(i['prevista'])} · {corto(i['titulo'], 38)}")
            lineas += 1
    if sin_dia:
        L.append("Sin día fijo (salen en el primer hueco libre)")
        for i in sin_dia[:10]:
            L.append(f"· {corto(i['titulo'], 44)}")
    if lineas >= 45:
        L.append(f"… y {n - lineas - len(sin_dia)} más.")
    botones = [[(f"✅ Aprobar las {n}", "pn:ap")], [("🔍 Revisar una a una", "pn:rv")], [VOLVER]]
    return "\n".join(L), botones


def confirmar_lote(n: int, a: datetime | None, b: datetime | None, sin_dia: int, firma: str) -> tuple[str, list]:
    L = [f"Vas a programar {plural(n, 'pieza', 'piezas')}" + (f", del {rango(a, b)}." if a and b else ".")]
    L.append("Se publican solas aunque tu PC esté apagado.")
    if sin_dia:
        L.append(f"{plural(sin_dia, 'pieza no tiene', 'piezas no tienen')} día fijo: {'sale' if sin_dia == 1 else 'salen'} en el primer hueco libre.")
    return "\n".join(L), [[(f"✅ Sí, aprobar las {n}", f"pn:ao:{firma}")], [("Cancelar", "pn:pe")]]


def resultado_lote(ok: list[dict], fallos: list[tuple[str, str]]) -> tuple[str, list]:
    L = []
    if ok:
        horas = [i["hora"] for i in ok if i.get("hora")]
        L.append(f"✅ Hecho: {plural(len(ok), 'pieza programada', 'piezas programadas')}"
                 + (f", del {rango(min(horas), max(horas))}." if horas else "."))
        L.append("Se publican solas aunque tu PC esté apagado.")
    else:
        L.append("No se ha programado ninguna pieza.")
    if fallos:
        L += ["", f"⚠️ No se {'pudo' if len(fallos) == 1 else 'pudieron'} programar {len(fallos)}:"]
        L += [f"· {corto(t, 34)}: {motivo_aprobar(m)}" for t, m in fallos[:8]]
    return "\n".join(L), [[("📅 Calendario", "pn:cal:0")], [VOLVER]]


# ------------------------------------------------------------------ ficha de una pieza
def ficha(it: dict, nota: str = "", ctx: str = "p") -> tuple[str, list]:
    """it: id, titulo, estado, programada, publicada, permalink, error, prevista, pie."""
    e = it["estado"]
    cab = f"{ICONO.get(e, '•')} {ESTADO_LLANO.get(e, e)}"
    if e == "aprobada" and it.get("programada"):
        cab += f" · {cuando(it['programada'])}"
    elif e == "pendiente":
        cab += f" · saldría {cuando(it['prevista'])}" if it.get("prevista") else " · saldría en el primer hueco libre"
    elif e == "publicada" and it.get("publicada"):
        cab += f" · {cuando(it['publicada'])}"
    L = [cab, corto(it["titulo"], 90)]
    if e == "error":
        L.append(f"Motivo: {motivo_llano(it.get('error'))}")
    pie = primeras_lineas(it.get("pie"), 2, 140)
    if pie:
        L += ["", pie]
    if nota:
        L = [nota, ""] + L
    i = it["id"]
    fila_ver = [("🖼 Ver hojas", f"pn:vh:{i}"), ("📝 Ver pie", f"pn:vp:{i}")]
    atras = [("⬅ Volver", _volver(ctx))]
    if e == "pendiente":
        b = [[("✅ Aprobar", f"pn:ok:{i}:{ctx}"), ("🕐 Otra hora", f"pn:ch:{i}:{ctx}")], fila_ver, [("❌ Rechazar", f"pn:no:{i}:{ctx}")], atras]
    elif e == "aprobada":
        b = [[("🕐 Cambiar hora", f"pn:ch:{i}:{ctx}"), ("↩️ Quitar de la cola", f"pn:qu:{i}:{ctx}")], fila_ver, atras]
    elif e == "error":
        b = [[("🔁 Reintentar", f"pn:rt:{i}:{ctx}"), ("🕐 Mover", f"pn:ch:{i}:{ctx}")], [("❌ Rechazar", f"pn:no:{i}:{ctx}")], atras]
    elif e == "publicada":
        b = ([[("Ver en Instagram", it["permalink"])]] if it.get("permalink") else []) + [fila_ver, atras]
    else:
        b = [fila_ver, atras]
    return "\n".join(L), b


def _volver(ctx: str) -> str:
    if ctx.startswith("c"):
        return f"pn:cal:{ctx[1:] or 0}"
    if ctx == "pu":
        return "pn:pu"
    if ctx in ("p", "a", "r"):
        return "pn:pe" if ctx == "p" else "pn:p"
    return "pn:p"


# ------------------------------------------------------------------ revisar una a una
def tarjeta_revision(k: int, n: int, it: dict) -> tuple[str, list]:
    cab = f"{k}/{n} · " + (cuando(it["prevista"]) if it.get("prevista") else "primer hueco libre") + f" · {corto(it['titulo'], 60)}"
    pie = primeras_lineas(it.get("pie"), 2, 150)
    texto = cab + (f"\n\n{pie}" if pie else "")
    i = it["id"]
    botones = [[("✅ Aprobar", f"pn:ok:{i}:r"), ("🕐 Otra hora", f"pn:ch:{i}:r")],
               [("🖼 Ver hojas", f"pn:vh:{i}"), ("📝 Ver pie", f"pn:vp:{i}")],
               [("❌ Rechazar", f"pn:no:{i}:r"), ("⏭ Saltar", f"pn:sk:{i}")],
               [("⬅ Dejarlo aquí", "pn:rf")]]
    return texto, botones


def resumen_revision(ok: int, no: int, sk: int, falt: int) -> str:
    L = ["🔍 Revisión terminada.", f"✅ Aprobadas: {ok} · ❌ Rechazadas: {no} · ⏭ Saltadas: {sk}"]
    if falt:
        L.append(f"Quedan {plural(falt, 'pieza', 'piezas')} por decidir.")
    else:
        L.append("No queda nada por aprobar.")
    return "\n".join(L)


# ------------------------------------------------------------------ elegir otra hora
def selector_hora(it: dict, opciones: list[tuple[str, str]], ctx: str) -> tuple[str, list]:
    """opciones: (etiqueta, dato del botón)."""
    if it["estado"] == "aprobada" and it.get("programada"):
        cab = f"Ahora sale el {cuando(it['programada'])}. ¿A qué hora la paso?"
    else:
        cab = "¿Cuándo sale?"
    L = [cab, corto(it["titulo"], 70), "", "Horas de Madrid. Las opciones valen 15 minutos."]
    if not opciones:
        L.append("No hay huecos libres en los próximos 7 días: escribe tú la hora.")
    botones = []
    for et, dato in opciones:
        botones.append([(et, dato)])
    botones.append([("✍️ Escribir hora", f"pn:ht:{it['id']}:{ctx}")])
    botones.append([("⬅ Volver", f"pn:f:{it['id']}:{ctx}")])
    return "\n".join(L), botones


def pedir_hora(it: dict, ctx: str, minutos: int) -> tuple[str, list]:
    return (f"Escribe la hora de «{corto(it['titulo'], 50)}» en un mensaje, por ejemplo «18:30», «mañana a las 9:30» o «en 2 horas». "
            f"Tienes {minutos} minutos; /cancelar lo deja.", [[("⬅ Volver", f"pn:f:{it['id']}:{ctx}")]])


# ------------------------------------------------------------------ calendario
def calendario(desde: date, dias: list[tuple[date, list[dict]]], sin_dia: list[dict], off: int) -> tuple[str, list, list[int]]:
    """dias: (día, piezas del día ordenadas por hora); cada pieza: id, titulo, estado, hora (datetime). Devuelve texto, botones e ids numerados."""
    hasta = date.fromordinal(desde.toordinal() + 6)
    L = [f"📅 Calendario · {_rango_semana(desde, hasta)}"]
    ids: list[int] = []
    hay = False
    for d, piezas in dias:
        if not piezas:
            continue
        hay = True
        L.append("")
        L.append(dia_de(d))
        for p in piezas:
            ids.append(p["id"])
            L.append(f"{len(ids)} {ICONO.get(p['estado'], '•')} {hora(p['hora'])} · {corto(p['titulo'], 36)}")
    if not hay:
        L += ["", "Nada previsto estos días."]
    if sin_dia and off == 0:
        L += ["", "Sin día fijo:"]
        for p in sin_dia[:8]:
            ids.append(p["id"])
            L.append(f"{len(ids)} {ICONO.get(p['estado'], '•')} {corto(p['titulo'], 44)}")
    botones, fila = [], []
    for k, i in enumerate(ids, 1):
        fila.append((str(k), f"pn:f:{i}:c{off}"))
        if len(fila) == 5:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    botones.append([("◀ Semana", f"pn:cal:{off - 1}"), ("Semana ▶", f"pn:cal:{off + 1}")])
    botones.append([VOLVER])
    return "\n".join(L), botones, ids


# ------------------------------------------------------------------ publicadas, más, números, estado, ayuda
def publicadas(items: list[dict]) -> tuple[str, list]:
    if not items:
        return "📤 Todavía no hay nada publicado.", [[VOLVER]]
    L = ["📤 Últimas publicadas", ""]
    botones, fila = [], []
    for k, i in enumerate(items, 1):
        L.append(f"{k} · {dia(i['publicada'])} {hora(i['publicada'])} · {corto(i['titulo'], 38)}")
        if i.get("permalink"):
            fila.append((str(k), i["permalink"]))
        if len(fila) == 4:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    L.append("")
    L.append("Los números de abajo abren la publicación en Instagram.")
    botones.append([VOLVER])
    return "\n".join(L), botones


def mas(en_pausa: bool) -> tuple[str, list]:
    return "⚙️ Más", [[("📊 Números", "pn:num"), ("🔧 Estado técnico", "pn:te")],
                      [("▶ Reanudar publicaciones", "pn:pa") if en_pausa else ("⏸ Pausar publicaciones", "pn:pa")],
                      [("ℹ️ Ayuda", "pn:ay")], [VOLVER]]


def numeros(datos: dict | None, publicadas_n: int, ultima: datetime | None) -> tuple[str, list]:
    L = ["📊 Números", ""]
    if datos:
        pf = datos.get("perfil") or {}
        ch = datos.get("cuenta_hoy") or {}
        if pf.get("followers_count") is not None:
            L.append(f"Seguidores: {pf['followers_count']}")
        if ch:
            L.append(f"Alcance de hoy: {ch.get('reach', '?')} personas (de ellas, {ch.get('reach_non_follower', '?')} no te seguían)")
            L.append(f"Visitas al perfil: {ch.get('profile_views', '?')}")
        if datos.get("fecha"):
            L.append(f"Medido el {datos['fecha'][:10]}.")
    else:
        L.append("Todavía no tengo medidas de Instagram desde la nube.")
    L.append(f"Publicadas hasta ahora: {publicadas_n}" + (f" (la última, {cuando(ultima)})" if ultima else ""))
    return "\n".join(L), [[VOLVER]]


def estado_tecnico(pasada_min: int | None, modo: str, instagram: str, token_expira: str | None, en_pausa: bool, parada: str | None) -> tuple[str, list]:
    L = ["🔧 Estado técnico", ""]
    L.append("Nube: " + ("conectada." if pasada_min is not None and pasada_min <= 1 else f"última pasada hace {pasada_min} min." if pasada_min is not None
                                else "sin datos de la última pasada."))
    L.append(f"Instagram: {instagram}.")
    L.append("Publicación automática: " + {"real": "activada", "simulacion": "en simulación (no publica de verdad)", "desactivado": "desactivada"}.get(modo, modo) + ".")
    if en_pausa:
        L.append("⏸ Las publicaciones están en pausa.")
    if parada:
        L.append(f"⚠️ Cola parada: {parada}")
    if token_expira:
        L.append(f"El acceso a Instagram caduca el {token_expira[:10]} y se renueva solo.")
    return "\n".join(L), [[VOLVER]]


def ayuda() -> tuple[str, list]:
    return ("ℹ️ Cómo va esto\n\n"
            "Todo se publica solo: aunque tu PC esté apagado, la nube sube a Instagram lo que has aprobado, a su hora.\n\n"
            "Los iconos:\n⏳ por aprobar\n✅ aprobada y programada\n📤 publicada\n⚠️ con problema\n❌ rechazada\n\n"
            "Lo que haces tú: abrir el /panel, aprobar (de golpe o una a una) y, si quieres, cambiar la hora de una pieza.\n\n"
            "Comandos: /panel, /pendientes, /calendario, /pausa, /seguir"), [[VOLVER]]


def ayuda_comandos() -> str:
    return ayuda()[0]


def sin_acceso() -> str:
    return "No tienes acceso a eso."


# ------------------------------------------------------------------ avisos push
def aviso_llegada(n: int, desde: datetime | None, hasta: datetime | None, total: int | None = None) -> tuple[str, list]:
    """Un solo mensaje cuando entran piezas nuevas (sea 1 o 40): qué hay y tres botones. `total` = las que hay por aprobar en conjunto."""
    total = max(total or n, n)
    rg = f", del {rango(desde, hasta)}" if desde and hasta else ""
    L = [f"📥 He recibido {plural(n, 'pieza nueva', 'piezas nuevas')}{rg}."]
    if total != n:
        L.append(f"En total hay {total} por aprobar.")
    L.append("¿La apruebo con la hora que dice el plan?" if total == 1 else "¿Las apruebo con la hora que dice el plan?")
    botones = [[("✅ Aprobar según el plan" if total == 1 else f"✅ Aprobar las {total} según el plan", "pn:ap")],
               [("🔍 Revisar una a una", "pn:rv")], [("Después", "pn:p")]]
    return "\n".join(L), botones


def motivo_aprobar(motivo) -> str:
    """Por qué no se pudo aprobar una pieza, en una frase sin jerga (los avisos del adaptador son largos y hablan de «candado»)."""
    t = str(motivo or "")
    for prefijo in ("No se puede aprobar ahora: ", "No se puede aprobar: "):
        if t.startswith(prefijo):
            t = t[len(prefijo):]
    t = t.replace(" Se puede editar o rechazar.", "").strip()
    bajo = t.lower()
    if "no usar" in bajo:
        return "Cita un dato que marcaste como «no usar»."
    if "validar" in bajo or "revisar algunos hechos" in bajo:
        return "Tiene datos sin validar. Mira /validar."
    if "fichero" in bajo:
        return "Falta el fichero de la pieza."
    if "hora ya ha pasado" in bajo:
        return "Su hora ya había pasado."
    if "ya no está pendiente" in bajo or bajo.startswith("esa pieza ya está"):
        return "Ya estaba decidida."
    return corto(t, 90)


def aviso_publicada(titulo: str, enlace: str | None, retraso_min: int | None = None) -> str:
    t = f"📤 Publicada: «{corto(titulo, 70)}»"
    t += f"\n{enlace}" if enlace else "\nInstagram todavía no ha dado el enlace: lo verás en el perfil."
    if retraso_min and retraso_min > 15:
        h, m = divmod(retraso_min, 60)
        t += f"\nSalió con {f'{h} h {m} min' if h else f'{m} min'} de retraso."
    return t


def aviso_error(titulo: str, error, pieza_id: int) -> tuple[str, list]:
    t = (f"⚠️ No salió: «{corto(titulo, 70)}»\n{motivo_llano(error)}\n"
         "Puedes reintentarlo, pasarla a otra hora o rechazarla.")
    return t, [[("🔁 Reintentar", f"pn:rt:{pieza_id}:a"), ("🕐 Mover", f"pn:ch:{pieza_id}:a")], [("❌ Rechazar", f"pn:no:{pieza_id}:a")]]


def aviso_retraso(titulo: str, previsto: datetime, nuevo: datetime, pieza_id: int) -> tuple[str, list]:
    t = (f"⚠️ «{corto(titulo, 70)}» no salió el {cuando(previsto)}.\n"
         f"La he pasado al {cuando(nuevo)}. ¿Quieres otra hora?")
    return t, [[("🕐 Cambiar hora", f"pn:ch:{pieza_id}:a")]]


def aviso_resumen_hoy(salidas: list[tuple[datetime, str]]) -> tuple[str, list]:
    L = ["☀️ Hoy salen:"] + [f"· {hora(h)} · {corto(t, 46)}" for h, t in salidas]
    return "\n".join(L), [[("📅 Calendario", "pn:cal:0")]]


def resumen_noche(manana: date, salidas: list[dict]) -> tuple[str, list]:
    """El único mensaje que José recibe cada noche (20-oct-2026): qué sale mañana, con una imagen resumen. Si no toca nada, sale así.
    salidas: lista de {id, hora (datetime), titulo, reel (bool)} ordenada por hora."""
    n = len(salidas)
    L = [f"🌙 Mañana, {dia_de(manana)}: {plural(n, 'publicación', 'publicaciones')}"]
    for x in salidas:
        L.append(f"· {hora(x['hora'])} {'Reel' if x.get('reel') else 'Carrusel'} · {corto(x['titulo'], 52)}")
    L += ["", "Si no tocas nada, sale así."]
    b = [[("👍 Todo bien", f"pn:nd:ok:{manana.isoformat()}")]]
    fila = [(f"✏️ {hora(x['hora'])}", f"pn:f:{x['id']}:a") for x in salidas[:4]]
    if fila:
        b.append(fila)
    b.append([("📅 Calendario", "pn:cal:0")])
    return "\n".join(L), b


def resumen_noche_hecho(texto: str) -> str:
    """El mismo resumen, ya aceptado: se quitan los botones y la última línea dice que está visto."""
    L = texto.split("\n")
    while L and not L[-1].strip():
        L.pop()
    if L and L[-1].startswith("Si no tocas nada"):
        L.pop()
    while L and not L[-1].strip():
        L.pop()
    return "\n".join(L) + "\n\n👍 Visto: sale así."


def aviso_cola_corta(dias_cola: int, hasta: datetime | None) -> str:
    if not dias_cola:
        return "🧭 La cola de publicaciones se ha quedado vacía. Hay que preparar más piezas desde el PC."
    return (f"🧭 A la cola le {'queda' if dias_cola == 1 else 'quedan'} {plural(dias_cola, 'día', 'días')} de publicaciones"
            + (f" (hasta el {dia(hasta)})" if hasta else "") + ". Conviene preparar más piezas desde el PC.")


def aviso_lote_hecho(texto: str) -> str:
    return texto


# ------------------------------------------------------------------ respuestas cortas a una pulsación (el aviso que sale un momento)
def toast_aprobada(cuando_txt: str) -> str:
    return f"Aprobada: sale el {cuando_txt}"


def pregunta_cabecera(numero: int) -> str:
    return f"Pregunta n.º {numero}"
