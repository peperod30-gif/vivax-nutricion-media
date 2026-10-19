"""Informe diario de números de @vivax_nutri para José, desde la nube (15-oct-2026): una vez al día, a partir de las 10:00 (Madrid).

Lee la API oficial de Instagram con los secretos del workflow (solo lectura) y deja un aviso por Telegram con seguidores, alcance, vistas,
guardados y envíos de las últimas publicaciones. Nunca rompe la pasada: cualquier fallo se calla y se reintenta en la siguiente.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
HORA_MIN = 10


def _cuentas(n: float) -> str:
    return f"{n:.0f} {'cuenta' if round(n) == 1 else 'cuentas'}"


def _texto(foto: dict, previa: dict | None) -> str:
    """El parte de la mañana (20-oct): 5 líneas que se leen en 10 segundos, con lo que importa para crecer y una sola conclusión."""
    p = foto["perfil"]
    seg = p.get("followers_count")
    delta = f" ({seg - previa['seguidores']:+d})" if previa and previa.get("seguidores") is not None and seg is not None else ""
    c = foto.get("cuenta_hoy", {}) or {}
    pubs = sorted(foto["publicaciones"], key=lambda x: x["fecha"], reverse=True)
    desde = datetime.now(ZoneInfo("UTC")).timestamp() - 36 * 3600
    recientes = [x for x in pubs if datetime.fromisoformat(x["fecha"].replace("+0000", "+00:00")).timestamp() >= desde] or pubs[:4]

    def val(x, k):
        return x["insights"].get(k) or 0

    def media(xs, k):
        return sum(val(x, k) for x in xs) / len(xs) if xs else 0
    reels = [x for x in recientes if x["tipo"] == "REELS"]
    carr = [x for x in recientes if x["tipo"] != "REELS"]
    L = [f"📊 @{p['username']}: {seg} seguidores{delta}"
         + (f" · {c['profile_views']} visitas al perfil" if c.get("profile_views") is not None else "")]
    vistas = []
    for x in reels:
        ms = x["insights"].get("ig_reels_avg_watch_time")
        if ms:
            vistas.append(ms / 1000)
    if reels:
        L.append(f"Reels ({len(reels)}): llegan a {_cuentas(media(reels, 'reach'))} de media" + (f" y se ven {sum(vistas) / len(vistas):.1f} s" if vistas else "") + ".")
    if carr:
        L.append(f"Carruseles ({len(carr)}): llegan a {_cuentas(media(carr, 'reach'))} de media.")
    L.append(f"Interacción: {sum((x.get('comentarios') or 0) for x in recientes)} comentarios, {sum(val(x, 'saved') for x in recientes)} guardados, "
             f"{sum(val(x, 'shares') for x in recientes)} envíos.")
    if recientes:
        mejor = max(recientes, key=lambda x: val(x, "reach") + 5 * val(x, "shares") + 3 * val(x, "saved"))
        L.append(f"La que mejor va: «{mejor['pie'][:48].rstrip()}…» ({'Reel' if mejor['tipo'] == 'REELS' else 'carrusel'}, {_cuentas(val(mejor, 'reach'))}).")
    if vistas and sum(vistas) / len(vistas) < 5:
        L.append("👉 Los Reels se abandonan en los primeros segundos: sigo afinando el gancho.")
    elif reels and media(reels, "reach") >= 100 and sum(val(x, "shares") for x in reels) == 0:
        L.append("👉 Llegan, pero nadie los envía: toca mejorar la razón para compartirlos.")
    return "\n".join(L)


def _por_formato(foto: dict, ad) -> str:
    """Media de alcance, guardados y envíos por molde (cambio, pictograma, debate…), uniendo cada publicación con su pieza por el id de Instagram."""
    if ad is None:
        return ""
    por_media = {}
    try:
        for r in ad.db.execute("SELECT ig_media_id, ruta_json, formato FROM piezas WHERE ig_media_id IS NOT NULL"):
            try:
                molde = json.loads(open(r["ruta_json"], encoding="utf-8").read()).get("molde") or r["formato"]
            except (OSError, ValueError):
                molde = r["formato"]
            por_media[str(r["ig_media_id"])] = molde
    except Exception:                                             # noqa: BLE001
        return ""
    grupos: dict[str, list] = {}
    for x in foto["publicaciones"]:
        m = por_media.get(str(x["id"]))
        if m:
            grupos.setdefault(m, []).append(x)
    if len(grupos) < 2:
        return ""
    filas = []
    for m, xs in grupos.items():
        n = len(xs)
        filas.append((sum((x["insights"].get("reach") or 0) for x in xs) / n, m, n,
                      sum((x["insights"].get("saved") or 0) for x in xs), sum((x["insights"].get("shares") or 0) for x in xs)))
    filas.sort(reverse=True)
    return "Por formato (alcance medio · guardados · envíos): " + "; ".join(f"{m} {a:.0f} · {g} · {e} ({n})" for a, m, n, g, e in filas[:6])


def informe_diario(hub, ahora: datetime | None = None, log=print, ad=None) -> bool:
    ahora = (ahora or datetime.now(MADRID)).astimezone(MADRID)
    if ahora.hour < HORA_MIN:
        return False
    clave = f"informe:{ahora.date().isoformat()}"
    if hub.meta_leer(clave):
        return False
    try:
        import metricas as M
        foto = M.foto_de_hoy(limite=24)
    except Exception as exc:                                      # noqa: BLE001 (sin red o sin token: se reintenta en otra pasada)
        log(f"informe diario: no se pudo ({type(exc).__name__})")
        return False
    try:
        previa = json.loads(hub.meta_leer("informe_ultimo") or "null")
    except ValueError:
        previa = None
    texto = _texto(foto, previa)
    try:
        extra = _por_formato(foto, ad)
    except Exception:                                             # noqa: BLE001
        extra = ""
    if extra:
        texto += "\n" + extra
    hub.crear_aviso(texto, "info", f"informe-{ahora.date().isoformat()}", "n1", None)
    hub.meta_escribir("informe_ultimo", json.dumps({"seguidores": foto["perfil"].get("followers_count"), "fecha": ahora.isoformat()}))
    hub.meta_escribir(clave, "hecho")
    log("informe diario enviado")
    return True
