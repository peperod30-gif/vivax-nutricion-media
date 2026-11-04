"""Informe semanal de @vivax_nutri con propuestas de mejora (4-nov-2026): cada domingo a partir de las 20:00 (Madrid), desde la nube.

José pidió «ir haciendo una lectura de los números diarios y engagement que tienen mi Instagram y, a partir de ahí, ir mejorándolo progresivamente y
proponerme mejoras al final de cada semana». El parte diario (nube/informe.py) cuenta lo de cada día; este informe cierra la semana: números por tipo
(Reel, carrusel, imagen), por serie y por hora de salida, y hasta cinco propuestas concretas con reglas simples. Con tan poco alcance se usan medianas y se
avisa de que una semana no es una tendencia. Nunca rompe la pasada: cualquier fallo se calla y se reintenta en la siguiente.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")


def _mediana(xs: list) -> float:
    xs = sorted(xs)
    n = len(xs)
    return 0.0 if n == 0 else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def _puntos(x: dict) -> float:
    """Una sola nota por publicación para ordenar: alcance + 5 por envío + 3 por guardado + 2 por comentario (envíos y guardados valen más que el alcance)."""
    ins = x.get("insights") or {}
    return (ins.get("reach") or 0) + 5 * (ins.get("shares") or 0) + 3 * (ins.get("saved") or 0) + 2 * (x.get("comentarios") or 0)


def _tipo_ig(x: dict) -> str:
    """reel, carrusel o imagen según lo que dice la API (media_product_type y, si hace falta, el id de la pieza)."""
    t = str(x.get("tipo") or "")
    if t == "REELS":
        return "reel"
    if "CAROUSEL" in t:
        return "carrusel"
    return "imagen" if t in ("IMAGE", "FEED") else "carrusel"


def _series_por_media(ad) -> dict:
    """id de Instagram → {serie, pilar} leído del JSON de la pieza (las piezas nuevas llevan `serie`; las viejas solo `molde`)."""
    out: dict = {}
    if ad is None:
        return out
    try:
        for r in ad.db.execute("SELECT ig_media_id, ruta_json, formato FROM piezas WHERE ig_media_id IS NOT NULL"):
            try:
                with open(r["ruta_json"], encoding="utf-8") as f:
                    j = json.load(f)
            except (OSError, ValueError):
                j = {}
            out[str(r["ig_media_id"])] = {"serie": j.get("serie") or j.get("molde") or r["formato"], "pilar": j.get("pilar_plan") or j.get("pilar") or ""}
    except Exception:                                             # noqa: BLE001
        return {}
    return out


def _resumen(grupo: list) -> dict:
    n = len(grupo)
    alc = sum(g["alcance"] for g in grupo) or 1
    return {"n": n, "alcance_mediano": _mediana([g["alcance"] for g in grupo]), "envios_por_mil": 1000 * sum(g["envios"] for g in grupo) / alc,
            "guardados_por_mil": 1000 * sum(g["guardados"] for g in grupo) / alc, "comentarios_por_mil": 1000 * sum(g["comentarios"] for g in grupo) / alc,
            "interaccion_por_mil": 1000 * sum(g["envios"] + g["guardados"] + g["comentarios"] + g["me_gusta"] for g in grupo) / alc,
            "puntos_medios": sum(g["puntos"] for g in grupo) / n}


def estadisticas_semana(foto: dict, ad=None, dias: int = 7, ahora_ts: float | None = None) -> dict:
    """Números de la semana por tipo, por serie y por hora (solo piezas con más de 24 h de vida: antes aún no han dado su alcance)."""
    ahora = ahora_ts if ahora_ts is not None else datetime.now(ZoneInfo("UTC")).timestamp()
    series = _series_por_media(ad)
    filas = []
    for x in foto.get("publicaciones", []):
        try:
            ts = datetime.fromisoformat(x["fecha"].replace("+0000", "+00:00"))
        except (KeyError, ValueError):
            continue
        if not 24 * 3600 <= ahora - ts.timestamp() <= dias * 86400:
            continue
        ins = x.get("insights") or {}
        meta = series.get(str(x.get("id")), {})
        filas.append({"tipo": _tipo_ig(x), "serie": meta.get("serie") or "(sin serie)", "pilar": meta.get("pilar", ""), "hora": ts.astimezone(MADRID).strftime("%H"),
                      "alcance": ins.get("reach") or 0, "guardados": ins.get("saved") or 0, "envios": ins.get("shares") or 0, "comentarios": x.get("comentarios") or 0,
                      "me_gusta": x.get("me_gusta") or 0, "puntos": _puntos(x), "salto3s": ins.get("reels_skip_rate")})
    por: dict = {}
    for clave in ("tipo", "serie", "hora"):
        g: dict = {}
        for f in filas:
            g.setdefault(str(f[clave]), []).append(f)
        por[clave] = {k: _resumen(v) for k, v in g.items()}
    saltos = [f["salto3s"] for f in filas if f["tipo"] == "reel" and f["salto3s"] is not None]
    return {"n": len(filas), "por_tipo": por["tipo"], "por_serie": por["serie"], "por_hora": por["hora"],
            "salto3s_mediano": _mediana(saltos) if saltos else None, "global": _resumen(filas) if filas else {}}


def propuestas_semana(est: dict, previa: dict | None) -> list:
    """Hasta cinco propuestas concretas con reglas simples."""
    P: list = []
    series = {k: v for k, v in est.get("por_serie", {}).items() if v["n"] >= 2 and k != "(sin serie)"}
    if len(series) >= 2:
        orden = sorted(series.items(), key=lambda kv: kv[1]["puntos_medios"], reverse=True)
        mejor, peor = orden[0], orden[-1]
        P.append(f"Más de «{mejor[0]}»: es lo que mejor puntúa (alcance mediano {mejor[1]['alcance_mediano']:.0f}, {mejor[1]['envios_por_mil']:.1f} envíos por mil). "
                 "Le doy un hueco más a la semana.")
        P.append(f"Cambio de «{peor[0]}»: es lo que menos puntúa ({peor[1]['alcance_mediano']:.0f} de alcance mediano). Pruebo otro gancho y otra mecánica antes de repetirla.")
    horas = {k: v for k, v in est.get("por_hora", {}).items() if v["n"] >= 2}
    if len(horas) >= 3:
        oh = sorted(horas.items(), key=lambda kv: kv[1]["alcance_mediano"], reverse=True)
        P.append(f"Hora: las {oh[0][0]}:00 es la que más alcanza ({oh[0][1]['alcance_mediano']:.0f}) y las {oh[-1][0]}:00 la que menos ({oh[-1][1]['alcance_mediano']:.0f}). "
                 "Muevo un hueco de la peor a la mejor.")
    pt = est.get("por_tipo", {})
    if previa and previa.get("por_tipo") and "carrusel" in pt and "carrusel" in previa["por_tipo"]:
        antes, ahora = previa["por_tipo"]["carrusel"]["alcance_mediano"], pt["carrusel"]["alcance_mediano"]
        if antes and ahora < 0.7 * antes:
            P.append(f"Alerta de saturación: el alcance mediano de los carruseles bajó de {antes:.0f} a {ahora:.0f}. Paso de 2 carruseles al día a 1 y refuerzo los Reels.")
    if est.get("salto3s_mediano") is not None and est["salto3s_mediano"] > 65:
        P.append(f"Los Reels se abandonan en 3 segundos ({est['salto3s_mediano']:.0f} % de salto): el primer fotograma tiene que preguntar o sorprender más.")
    elif est.get("global") and est["global"].get("envios_por_mil", 0) < 2:
        P.append("Casi nadie los envía: más remates con un destinatario concreto («mándaselo a quien…») y más comparaciones que se puedan discutir.")
    return P[:5]


def texto_semana(foto: dict, ad, previa: dict | None, seg_previo: int | None, ahora_ts: float | None = None) -> tuple:
    """El informe del domingo por la noche. Devuelve (texto, estadísticas para comparar la semana siguiente)."""
    p = foto["perfil"]
    est = estadisticas_semana(foto, ad, ahora_ts=ahora_ts)
    seg = p.get("followers_count")
    delta = f" ({seg - seg_previo:+d} en la semana)" if seg_previo is not None and seg is not None else ""
    L = [f"📈 La semana de @{p['username']}: {seg} seguidores{delta}. Piezas con más de 24 h: {est['n']}."]
    pt = est["por_tipo"]
    for tipo in ("reel", "carrusel", "imagen"):
        if tipo in pt:
            v = pt[tipo]
            L.append(f"{tipo.capitalize()}s ({v['n']}): alcance mediano {v['alcance_mediano']:.0f}, {v['envios_por_mil']:.1f} envíos y {v['guardados_por_mil']:.1f} guardados por mil.")
    if est.get("salto3s_mediano") is not None:
        L.append(f"Reels: el {est['salto3s_mediano']:.0f} % se va en los 3 primeros segundos.")
    top = sorted(((k, v) for k, v in est["por_serie"].items()), key=lambda kv: kv[1]["puntos_medios"], reverse=True)[:3]
    if top:
        L.append("Lo mejor: " + "; ".join(f"{k} ({v['alcance_mediano']:.0f})" for k, v in top) + ".")
    prop = propuestas_semana(est, previa)
    if prop:
        L += ["", "Propuestas para la semana que viene:"] + [f"{i}. {x}" for i, x in enumerate(prop, 1)]
        L.append("Una semana no es una tendencia: con pocos datos, cambio poco cada vez.")
    else:
        L.append("Aún hay pocos datos por serie para proponer cambios: sigo midiendo.")
    return "\n".join(L), est


def informe_semanal(hub, ahora: datetime | None = None, log=print, ad=None) -> bool:
    """El domingo a partir de las 20:00 (Madrid), una vez por semana."""
    ahora = (ahora or datetime.now(MADRID)).astimezone(MADRID)
    if ahora.weekday() != 6 or ahora.hour < 20:
        return False
    iso = ahora.isocalendar()
    clave = f"informe_sem:{iso[0]}-{iso[1]:02d}"
    if hub.meta_leer(clave):
        return False
    try:
        import metricas as M
        foto = M.foto_de_hoy(limite=60)
    except Exception as exc:                                      # noqa: BLE001 (sin red o sin token: se reintenta en otra pasada)
        log(f"informe semanal: no se pudo ({type(exc).__name__})")
        return False
    try:
        previa = json.loads(hub.meta_leer("informe_semana_estadisticas") or "null")
    except ValueError:
        previa = None
    try:
        seg_previo = json.loads(hub.meta_leer("informe_semana") or "null")
    except ValueError:
        seg_previo = None
    texto, est = texto_semana(foto, ad, previa, (seg_previo or {}).get("seguidores"))
    hub.crear_aviso(texto, "info", f"informe-semana-{iso[0]}-{iso[1]:02d}", "n1", None)
    hub.meta_escribir("informe_semana_estadisticas", json.dumps(est, ensure_ascii=False))
    hub.meta_escribir("informe_semana", json.dumps({"seguidores": foto["perfil"].get("followers_count"), "fecha": ahora.isoformat()}))
    hub.meta_escribir(f"informe_sem_estadisticas:{iso[0]}-{iso[1]:02d}", json.dumps(est, ensure_ascii=False))
    hub.meta_escribir(clave, "hecho")
    log("informe semanal enviado")
    return True
