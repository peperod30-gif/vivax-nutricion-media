"""Métricas de la cuenta de Instagram de Vivax Nutrición (12-oct-2026): lectura de la API oficial, serie histórica y resumen para Telegram.

Solo lee: nunca publica, borra ni cambia nada. El token sale de router/.env y no se imprime ni se guarda (los errores de red se
escriben sin la URL, que lleva el token).

Uso:
    python metricas.py                 # guarda la foto de hoy en datos/metricas/ y escribe el resumen
    python metricas.py --avisar        # además manda el resumen a José por Telegram (python -m router avisar)
    python metricas.py --solo-resumen  # resumen de la última foto guardada, sin llamar a Instagram

Qué mide por publicación (los que devuelve la API con inicio de sesión de Instagram): alcance (cuentas únicas), vistas, guardados,
envíos, me gusta, comentarios e interacciones totales; en los Reels, además, el tiempo medio de visualización. De la cuenta:
seguidores, seguidos, publicaciones, alcance del día y visitas al perfil si la API las da.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent
ENV = RAIZ / "router" / ".env"
CARPETA = RAIZ / "datos" / "metricas"
GRAPH = "https://graph.instagram.com"
METRICAS_MEDIA = ["reach", "views", "saved", "shares", "likes", "comments", "total_interactions"]
METRICAS_REEL = METRICAS_MEDIA + ["ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]


class ErrorMetricas(Exception):
    pass


def _env() -> dict:
    import os
    env = {k: os.environ[k] for k in ("IG_N1_TOKEN", "IG_N1_USER_ID") if os.environ.get(k)}      # en la nube salen de los secretos del workflow
    if len(env) == 2 or not ENV.is_file():
        return env
    for linea in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.lstrip().startswith("#"):
            k, v = linea.strip().split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Cliente:
    def __init__(self) -> None:
        env = _env()
        try:
            self._tok, self.uid = env["IG_N1_TOKEN"], env["IG_N1_USER_ID"]
        except KeyError as exc:
            raise ErrorMetricas("faltan IG_N1_TOKEN o IG_N1_USER_ID en router/.env") from exc

    def get(self, ruta: str, **params) -> tuple[int, dict]:
        params["access_token"] = self._tok
        ultimo = "sin red"
        for k in range(6):                                  # la red del equipo falla a ratos (DNS): se reintenta
            try:
                r = requests.get(f"{GRAPH}/{ruta}", params=params, timeout=30)
                try:
                    return r.status_code, r.json()
                except ValueError:
                    return r.status_code, {"raw": r.text[:200]}
            except requests.exceptions.RequestException as exc:
                ultimo = type(exc).__name__                 # solo el tipo: el texto trae la URL con el token
                time.sleep(3 + 2 * k)
        raise ErrorMetricas(f"Instagram no contesta ({ultimo})")


def _valores(d: dict) -> dict:
    sal = {}
    for x in d.get("data", []):
        v = x.get("values", [{}])[0].get("value") if x.get("values") else (x.get("total_value") or {}).get("value")
        sal[x["name"]] = v
    return sal


def foto_de_hoy(limite: int = 50) -> dict:
    c = Cliente()
    s, perfil = c.get("me", fields="username,name,followers_count,follows_count,media_count")
    if s != 200:
        raise ErrorMetricas(f"el perfil no se pudo leer ({s}): {str(perfil)[:120]}")
    s, med = c.get(f"{c.uid}/media", fields="id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count", limit=limite)
    if s != 200:
        raise ErrorMetricas(f"las publicaciones no se pudieron leer ({s})")
    pubs = []
    for m in med.get("data", []):
        lista = METRICAS_REEL if m.get("media_product_type") == "REELS" else METRICAS_MEDIA
        s2, ins = c.get(f"{m['id']}/insights", metric=",".join(lista))
        if s2 != 200:                                       # si una métrica no existe para ese tipo, se piden las básicas
            s2, ins = c.get(f"{m['id']}/insights", metric="reach,saved,shares,total_interactions")
        pubs.append({"id": m["id"], "fecha": m["timestamp"], "tipo": m.get("media_product_type") or m.get("media_type"),
                     "permalink": m["permalink"], "pie": (m.get("caption") or "").replace("\n", " ")[:80],
                     "me_gusta": m.get("like_count"), "comentarios": m.get("comments_count"),
                     "insights": _valores(ins) if s2 == 200 else {}})
    cuenta = {}
    for periodo_metricas in ("reach", "views", "profile_views", "accounts_engaged", "follows_and_unfollows"):
        s3, d = c.get(f"{c.uid}/insights", metric=periodo_metricas, period="day", metric_type="total_value")
        if s3 == 200:
            cuenta.update(_valores(d))
    s4, d4 = c.get(f"{c.uid}/insights", metric="reach", period="day", metric_type="total_value", breakdown="follow_type")
    if s4 == 200:                                           # alcance de seguidores y no seguidores (estimado; solo por cuenta y por día)
        try:
            for r in d4["data"][0]["total_value"]["breakdowns"][0]["results"]:
                cuenta["reach_" + str(r["dimension_values"][0]).lower()] = r["value"]
        except (KeyError, IndexError, TypeError):
            cuenta["reach_por_tipo"] = "respuesta no entendida"
    return {"fecha": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "perfil": {k: perfil.get(k) for k in ("username", "name", "followers_count", "follows_count", "media_count")},
            "cuenta_hoy": cuenta, "publicaciones": pubs}


def guardar(foto: dict) -> Path:
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / f"{foto['fecha'][:10]}.json"
    ruta.write_text(json.dumps(foto, ensure_ascii=False, indent=1), encoding="utf-8")
    return ruta


def ultimas(n: int = 2) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CARPETA.glob("20??-??-??.json"))[-n:]]


def resumen(foto: dict, anterior: dict | None = None) -> str:
    p = foto["perfil"]
    lineas = [f"Instagram @{p['username']}: {p['followers_count']} seguidores"
              + (f" ({p['followers_count'] - anterior['perfil']['followers_count']:+d} desde el último registro)" if anterior else "")
              + f", {p['media_count']} publicaciones."]
    pubs = sorted(foto["publicaciones"], key=lambda x: x["fecha"], reverse=True)
    if pubs:
        def alc(x): return x["insights"].get("reach") or 0
        medio = sum(alc(x) for x in pubs) / len(pubs)
        lineas.append(f"Alcance medio por publicación: {medio:.0f} cuentas (últimas {len(pubs)}).")
        mejor = max(pubs, key=alc)
        lineas.append(f"La que más llegó: {alc(mejor)} cuentas, {mejor['insights'].get('saved', 0)} guardados, {mejor['insights'].get('shares', 0)} envíos: {mejor['pie']}")
        for x in pubs[:3]:
            i = x["insights"]
            lineas.append(f"· {x['fecha'][5:10]} {x['tipo']}: alcance {i.get('reach', '—')}, vistas {i.get('views', '—')}, guardados {i.get('saved', '—')}, envíos {i.get('shares', '—')}.")
    return "\n".join(lineas)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--avisar", action="store_true")
    ap.add_argument("--solo-resumen", action="store_true")
    a = ap.parse_args()
    try:
        if a.solo_resumen:
            fotos = ultimas(2)
            if not fotos:
                print("Todavía no hay ninguna foto guardada.")
                return 1
            foto, anterior = fotos[-1], (fotos[-2] if len(fotos) > 1 else None)
        else:
            previas = ultimas(1)
            foto = foto_de_hoy()
            guardar(foto)
            anterior = previas[-1] if previas and previas[-1]["fecha"][:10] != foto["fecha"][:10] else (ultimas(2)[0] if len(ultimas(2)) > 1 else None)
    except ErrorMetricas as exc:
        print(f"No se pudo: {exc}")
        return 2
    texto = resumen(foto, anterior)
    print(texto)
    if a.avisar:
        subprocess.run([sys.executable, "-X", "utf8", "-m", "router", "avisar", texto, "--nivel", "info", "--clave", "metricas-" + foto["fecha"][:10], "--cuenta", "n1"],
                       cwd=RAIZ, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
