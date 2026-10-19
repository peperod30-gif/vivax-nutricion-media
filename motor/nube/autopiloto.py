"""Autopiloto de la nube (20-oct-2026): programa solo las piezas nuevas, sin que José (ni Claude Code) tengan que aprobar una a una.

Por qué. José delegó: «aprueba en automático lo que consideres preciso; lo importante, por Telegram». Y los datos mostraron que aprobar
pieza a pieza no sirve: en 4 minutos aprobó más de 60 tarjetas sin mirarlas. Ahora una pieza que el productor deja en `piezas/nube/` con
`produccion.autoaprobar: true` se aprueba sola al importarse (ya pasó los controles de texto, cifras y líneas rojas) y recibe el primer
hueco libre de la PAUTA de la cuenta (`pauta` en nube/cuentas.nube.json: lista de [«HH:MM», «reel»|«carrusel»]), dentro de un horizonte de
días. Lo que NO programa nunca:
  · piezas sin la marca `autoaprobar` (las que ya estaban pendientes y se guardaron a propósito siguen igual);
  · piezas con `produccion.revision_jose` o `produccion.muestra`: llegan a José una a una, y las aprueba él;
  · piezas cuyos hechos de riesgo alto no están validados (`aprobar` ya lo comprueba con su candado y devuelve el motivo).
Qué deja: una línea de auditoría `aprobada_hora` con origen `nube:autopiloto`, y nada más (el resumen de la noche cuenta a José qué sale mañana
y puede parar cualquier pieza hasta 30 minutos antes con «Quitar de la cola»).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
HORIZONTE_DIAS = 21              # no se programa más allá: así la cola nunca se queda con piezas viejas por delante de otras nuevas
MARGEN_MIN = 30                  # una hora tiene que quedar al menos 30 minutos por delante (José puede parar la pieza hasta entonces)
SEPARACION_MIN = 45              # dos piezas no salen a menos de 45 minutos
TOPE_POR_PASADA = 80


def _tipo(formato: str) -> str:
    return "reel" if formato == "video" else "carrusel"


def _pauta(valor) -> list[tuple[str, str]]:
    sal = []
    for x in valor or []:
        try:
            hhmm, tipo = x
            hh, mm = (int(p) for p in str(hhmm).split(":"))
            if 0 <= hh < 24 and 0 <= mm < 60 and tipo in ("reel", "carrusel"):
                sal.append((f"{hh:02d}:{mm:02d}", tipo))
        except (TypeError, ValueError):
            continue
    return sorted(sal)


def programar(ad, hub, pauta, log=print, ahora: datetime | None = None, horizonte_dias: int = HORIZONTE_DIAS) -> list[str]:
    """Aprueba las piezas pendientes marcadas con `produccion.autoaprobar`, cada una en el primer hueco libre de la pauta que sea de su tipo.
    Devuelve los ids de las piezas programadas. Nunca lanza: un fallo se cuenta en el log y se reintenta en la pasada siguiente."""
    huecos = _pauta(pauta)
    if not huecos:
        return []
    admin = hub.admin_id()
    if admin is None:
        return []
    ahora = (ahora or ad.ahora()).astimezone(timezone.utc)
    candidatas = []
    for r in ad.db.execute("SELECT * FROM piezas WHERE estado='pendiente' AND decidida_por IS NULL ORDER BY id").fetchall():
        try:
            prod = (ad._json_de(r).get("produccion") or {})
        except Exception:                                          # noqa: BLE001 (un JSON ilegible no se programa)
            continue
        if not prod.get("autoaprobar") or prod.get("revision_jose") or prod.get("muestra"):
            continue
        candidatas.append((str(prod.get("fecha") or "9999"), r["id"], r["pieza_id"], _tipo(r["formato"])))
    if not candidatas:
        return []
    candidatas.sort()
    ocupadas = [datetime.fromisoformat(r["programada"]).astimezone(timezone.utc) for r in ad.db.execute(
        "SELECT programada FROM piezas WHERE programada IS NOT NULL AND estado IN ('aprobada','publicando')")]
    desde = ahora + timedelta(minutes=MARGEN_MIN)
    dia0 = ahora.astimezone(MADRID).date()
    hechas: list[str] = []
    for _, id_, pid, tipo in candidatas[:TOPE_POR_PASADA]:
        colocada = False
        for d in range(0, horizonte_dias + 1):
            dia = dia0 + timedelta(days=d)
            for hhmm, t_hueco in huecos:
                if t_hueco != tipo:
                    continue
                h, m = (int(x) for x in hhmm.split(":"))
                t = datetime(dia.year, dia.month, dia.day, h, m, tzinfo=MADRID)
                tu = t.astimezone(timezone.utc)
                if tu < desde or any(abs((tu - o).total_seconds()) < SEPARACION_MIN * 60 for o in ocupadas):
                    continue
                try:
                    motivo = ad.aprobar(id_, hub.admin_id(), para_admin=True, hora=t, origen="nube:autopiloto")
                except Exception as exc:                           # noqa: BLE001
                    log(f"autopiloto: {pid}: {type(exc).__name__}")
                    motivo = "error"
                if motivo:
                    log(f"autopiloto: {pid} no se programa: {str(motivo)[:100]}")
                else:
                    ocupadas.append(tu)
                    hechas.append(pid)
                    log(f"autopiloto: {pid} → {t:%a %d-%m %H:%M}")
                colocada = True
                break
            if colocada:
                break
    return hechas
