"""Una pasada (o dos) del router de Vivax Nutrición en la nube.

Hace lo que hace el router local en cada vuelta, pero sin quedarse esperando:
    1. importa las piezas nuevas de `motor/piezas/nube/` (con los mismos controles de calidad que en el PC),
    2. ejecuta las órdenes de `motor/ordenes/` (avisos y preguntas con acciones),
    3. lee lo que José ha pulsado o escrito en Telegram (`getUpdates` con tiempo 0) y lo procesa con el mismo código de siempre,
    4. entrega las tarjetas pendientes, cierra decisiones, hace las acciones de las decisiones que José ha respondido y manda avisos,
    5. publica en Instagram lo aprobado cuya hora ha llegado (el publicador de siempre, con sus reintentos y sus protecciones).
Si en la primera pasada pasó algo (José pulsó algo, se aprobó algo), hace una segunda 20 s después para que el publicador salga ya.

NO monta Campo, ni el plan mensual, ni la producción: siguen siendo del PC. No lee main.py (arrastra planes).

Seguridad: si Telegram contesta 409 (otro proceso está leyendo el mismo bot, p. ej. el router del PC), la ejecución se detiene SIN
publicar nada: dos routers con dos colas distintas publicarían dos veces lo mismo.

Uso (desde motor/):
    python -X utf8 -m nube.ejecutar_nube --datos ../router_datos [--simulado] [--sin-telegram] [--pasadas 2]
Variables: TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, IG_N1_TOKEN, IG_N1_USER_ID, GITHUB_TOKEN (nada de esto se imprime).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

MOTOR = Path(__file__).resolve().parents[1]
if str(MOTOR) not in sys.path:
    sys.path.insert(0, str(MOTOR))

from nube import estado as E            # noqa: E402
from nube import ordenes as O           # noqa: E402
from router.adaptadores.nutricion import AdaptadorNutricion, ErrorImportar   # noqa: E402
from router.hub import Hub              # noqa: E402
from router.nucleo import Router        # noqa: E402
from router.telegram_io import ErrorTelegram, TelegramReal   # noqa: E402

CUENTAS = MOTOR / "nube" / "cuentas.nube.json"
CODIGO_CONFLICTO = 3


class ConflictoTelegram(Exception):
    """Otro proceso lee el mismo bot de Telegram."""


class TelegramMudo:
    """Telegram que no habla con nadie: para las ejecuciones de prueba (nada sale a la red)."""

    def __init__(self):
        self._n = 0
        self.enviados: list[str] = []

    def enviar(self, chat_id, texto, botones=None):
        self._n += 1
        self.enviados.append(texto)
        return 9_000_000 + self._n

    def editar(self, chat_id, message_id, texto, botones=None):
        pass

    def responder_callback(self, cb_id, texto="", alerta=False):
        pass

    def enviar_archivos(self, chat_id, formato, rutas, caption):
        self.enviados.append(f"[{formato}: {len(list(rutas))} archivos] {caption}")

    def actualizaciones(self, offset, timeout=25):
        return []

    def poner_menu(self, comandos):
        pass

    def yo(self):
        return {"username": "telegram_mudo"}


class ResultadoSimulado:
    def __init__(self, id_, enlace):
        self.id, self.enlace = id_, enlace


class PublicadorSimulado:
    """Publicador de Instagram que no sale a la red: acepta todo y devuelve un resultado de mentira (ejecuciones de prueba)."""

    prefijo = "simulado"          # el adaptador trata como simulación todo id que empiece así (la pieza sigue aprobada); las pruebas lo cambian

    def __init__(self):
        self.llamadas = 0

    def publicar(self, formato, archivos, pie, estado, guardar, ia=False, alt=None):
        from router.instagram import Resultado       # noqa: PLC0415
        self.llamadas += 1
        guardar({"fase": "contenedor", "contenedor": "simulado"})
        return Resultado(f"{self.prefijo}-{self.llamadas:04d}", "https://www.instagram.com/p/SIMULADO/")

    def renovar_token(self):
        class _Pronto(Exception):
            clase = "renovacion_pronto"
        raise _Pronto("simulado")


def log(texto: str) -> None:
    print(texto, flush=True)


def cargar_cuentas() -> dict:
    return json.loads(Path(os.environ.get("VIVAX_CUENTAS_NUBE") or CUENTAS).read_text(encoding="utf-8"))


def _info_ig(datos: Path, cuenta: str) -> dict:
    r = datos / "cuentas" / cuenta / "instagram.json"
    return json.loads(r.read_text(encoding="utf-8")) if r.is_file() else {}


def construir_publicador(c: dict, env: dict, datos: Path, simulado: bool):
    if simulado:
        return PublicadorSimulado()
    ig = c.get("instagram") or {}
    ficha_token = datos / "token_ig.txt"
    token = ficha_token.read_text(encoding="utf-8").strip() if ficha_token.is_file() else env.get("IG_N1_TOKEN", "")
    uid = env.get("IG_N1_USER_ID", "") or _info_ig(datos, c["codigo"]).get("user_id", "")
    if not token or not uid:
        return None
    from router.instagram import ConfigIG, Credenciales, Publicador       # noqa: PLC0415
    cfg = ConfigIG(modo=ig.get("modo", "desactivado"), api=ig.get("api", "instagram_login"), version=ig.get("version") or "v25.0",
                   alojamiento=ig.get("alojamiento") or {})
    return Publicador(Credenciales(user_id=str(uid), token=token, github_token=env.get("GITHUB_TOKEN", "")), cfg)


def montar(datos: Path, env: dict, simulado: bool, motor: Path = MOTOR, modo_ig: str | None = None):
    """Hub y adaptador de Nutrición. Las demás cuentas del archivo se registran apagadas (Campo no vive en la nube)."""
    cfg = cargar_cuentas()
    Path(datos).mkdir(parents=True, exist_ok=True)
    hub = Hub(datos / "hub.sqlite")
    adaptadores = {}
    for c in cfg["cuentas"]:
        hub.registrar_cuenta(c["codigo"], c["nombre"], c["tipo"], c["adaptador"], activa=c.get("habilitada", True))
        if not c.get("habilitada", True) or c["adaptador"] != "nutricion":
            continue
        ig = dict(c.get("instagram") or {})
        if simulado:
            ig["modo"] = "simulacion"
        if modo_ig:                                                     # solo las pruebas: «real» con el publicador simulado
            ig["modo"] = modo_ig
        ig["info"] = _info_ig(datos, c["codigo"])
        ig["info_ruta"] = str(datos / "cuentas" / c["codigo"] / "instagram.json")
        ad = AdaptadorNutricion(c["codigo"], datos / "cuentas", salida=Path(motor) / "salida", politica=c.get("politica_aprobacion", "validado"),
                                horas=c.get("horas"), ig=ig, publicador=construir_publicador({**c, "instagram": ig}, env, datos, simulado))
        ad.guardar_token = lambda tok, datos=datos: _guardar_token(datos, tok)
        adaptadores[c["codigo"]] = ad
    return hub, adaptadores


def _guardar_token(datos: Path, token: str) -> None:
    """El token renovado se guarda en el estado cifrado (los secretos de Actions no se pueden escribir desde aquí)."""
    f = datos / "token_ig.txt"
    f.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def importar_nuevas(ad, hub, carpeta: Path, lg=log) -> int:
    """Importa los JSON de `carpeta` que aún no están en la cola. Un fallo no se calla: avisa a José (una vez cada 6 h por pieza)."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return 0
    conocidas = {r["pieza_id"] for r in ad.db.execute("SELECT pieza_id FROM piezas")}
    n = 0
    for ruta in sorted(carpeta.glob("*.json")):
        try:
            pid = json.loads(ruta.read_text(encoding="utf-8"))["id"]
        except (OSError, ValueError, KeyError):
            continue
        if pid in conocidas:
            continue
        try:
            ad.importar(ruta)
            n += 1
            lg(f"importada {pid}")
        except Exception as exc:                                  # noqa: BLE001 (se cuenta, no se esconde)
            lg(f"no se pudo importar {pid}: {type(exc).__name__}")
            clave = "nube-import-" + "".join(ch if ch.isalnum() else "-" for ch in pid.lower())[:60]
            hub.crear_aviso(f"La pieza {pid} no se ha podido importar en la nube: {str(exc)[:300] or type(exc).__name__}", "atencion", clave, "n1", 6 * 3600)
    return n


def pasada(router: Router, hub: Hub, ad, tg, sondear: bool, motor: Path, lg=log) -> dict:
    cuenta = {"importadas": 0, "ordenes": 0, "updates": 0, "decisiones": 0}
    cuenta["importadas"] = importar_nuevas(ad, hub, motor / "piezas" / "nube", lg)
    cuenta["ordenes"] = O.procesar_ordenes(hub, motor / "ordenes", lg)
    if sondear:
        try:
            for upd in tg.actualizaciones(hub.ultimo_update() + 1, 0):
                try:
                    router.procesar(upd)
                except Exception as exc:                          # noqa: BLE001 (una actualización mala no tumba la pasada)
                    lg(f"actualización {upd.get('update_id')}: {type(exc).__name__}")
                hub.guardar_update(upd["update_id"])
                cuenta["updates"] += 1
        except ErrorTelegram as exc:
            if exc.codigo == 409:
                raise ConflictoTelegram() from None
            lg(f"telegram: {exc.metodo} {exc.codigo}")
    for etapa, fn in (("entrega de piezas", router.entregar_pendientes), ("decisiones", router.decisiones.vuelta)):
        try:
            fn()
        except ErrorTelegram as exc:
            lg(f"{etapa}: telegram {exc.metodo} {exc.codigo}")
        except Exception as exc:                                  # noqa: BLE001
            lg(f"{etapa}: {type(exc).__name__}")
    try:
        cuenta["decisiones"] = O.ejecutar_acciones(hub, ad, router, lg)
    except Exception as exc:                                      # noqa: BLE001
        lg(f"acciones de decisiones: {type(exc).__name__}: {exc}")
    for etapa, fn in (("decisiones (cierre)", router.decisiones.vuelta), ("avisos", router.avisos.vuelta), ("publicador", router.vuelta_de_fondo)):
        try:
            fn()
        except ErrorTelegram as exc:
            lg(f"{etapa}: telegram {exc.metodo} {exc.codigo}")
        except Exception as exc:                                  # noqa: BLE001
            lg(f"{etapa}: {type(exc).__name__}")
    return cuenta


def ejecutar(datos: Path, env: dict, simulado: bool, sin_telegram: bool, pasadas: int = 2, espera_s: float = 20.0, motor: Path = MOTOR,
             tg=None, modo_ig: str | None = None) -> int:
    datos = Path(datos)
    datos.mkdir(parents=True, exist_ok=True)
    os.environ["VIVAX_VALIDACIONES"] = str(datos / "validaciones")        # las validaciones de José viajan con el estado cifrado
    cambiadas = E.reescribir_rutas(datos / "cuentas" / "n1" / "estado.sqlite", motor / "piezas" / "nube")
    if cambiadas:
        log(f"rutas de piezas reescritas: {cambiadas}")
    admin = env.get("TELEGRAM_USER_ID", "")
    if not admin.isdigit():
        print("Falta TELEGRAM_USER_ID.", file=sys.stderr)
        return 2
    hub, adaptadores = montar(datos, env, simulado, motor, modo_ig)
    hub.establecer_admin(int(admin), "administrador")
    if tg is not None:                                                  # las pruebas inyectan su Telegram
        pass
    elif sin_telegram:
        tg = TelegramMudo()
    else:
        if not env.get("TELEGRAM_BOT_TOKEN"):
            print("Falta TELEGRAM_BOT_TOKEN.", file=sys.stderr)
            return 2
        tg = TelegramReal(env["TELEGRAM_BOT_TOKEN"])
    router = Router(hub, tg, adaptadores, "nube", planes=None, produccion=None, log=lambda m: log(f"[router] {m}"))
    ad = adaptadores["n1"]
    modo = ad.ig.get("modo", "desactivado")
    log(f"Pasada en la nube: modo {modo}{' (publicador simulado)' if simulado else ''}{', sin Telegram' if sin_telegram else ''}.")
    try:
        for i in range(1, pasadas + 1):
            try:
                c = pasada(router, hub, ad, tg, not sin_telegram, motor)
            except ConflictoTelegram:
                print("Telegram da 409: otro proceso está leyendo este bot (¿el router del PC sigue en marcha?). "
                      "No publico nada para no duplicar. Para el router local y vuelve a ejecutar.", file=sys.stderr)
                return CODIGO_CONFLICTO
            log(f"pasada {i}: {c}")
            if i < pasadas and (c["updates"] or c["decisiones"] or c["importadas"]):
                time.sleep(espera_s)
            else:
                break
        return 0
    finally:
        for base in (hub.db, ad.db):                                    # que nada quede abierto antes de cifrar el estado
            try:
                base.cerrar()
            except Exception:                                           # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nube.ejecutar_nube")
    ap.add_argument("--datos", required=True, type=Path, help="carpeta con hub.sqlite y cuentas/n1/estado.sqlite (el estado descifrado)")
    ap.add_argument("--simulado", action="store_true", help="publicador de Instagram simulado (no sale a la red)")
    ap.add_argument("--sin-telegram", action="store_true", help="no leer ni escribir en Telegram")
    ap.add_argument("--pasadas", type=int, default=2)
    a = ap.parse_args(argv)
    env = {k: os.environ.get(k, "") for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID", "IG_N1_TOKEN", "IG_N1_USER_ID", "GITHUB_TOKEN")}
    return ejecutar(a.datos, env, a.simulado, a.sin_telegram, a.pasadas)


if __name__ == "__main__":
    raise SystemExit(main())
