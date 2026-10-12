"""Una pasada (o dos) del router de Vivax Nutrición en la nube, y, si José está usando Telegram, una sesión activa.

Hace lo que hace el router local en cada vuelta, pero sin quedarse esperando:
    1. importa las piezas nuevas de `motor/piezas/nube/` (con los mismos controles de calidad que en el PC),
    2. ejecuta las órdenes de `motor/ordenes/` (avisos y preguntas con acciones),
    3. lee lo que José ha pulsado o escrito en Telegram (`getUpdates`) y lo procesa con el mismo código de siempre (el panel incluido),
    4. entrega las piezas nuevas, cierra preguntas, hace las acciones de las preguntas que José ha respondido y manda avisos,
    5. publica en Instagram lo aprobado cuya hora ha llegado (el publicador de siempre, con sus reintentos y sus protecciones).
Si en la primera pasada pasó algo (José pulsó algo, se aprobó algo), hace una segunda 20 s después para que el publicador salga ya.

MODO ACTIVO (`--activo`, lo pide el workflow): con el cron cada 5 minutos, un panel que tardase 5 minutos en contestar a cada toque no
serviría. Por eso, si José pulsa o escribe algo durante la ejecución, la ejecución NO termina: sigue con sondeo largo de Telegram (15 s) y
contesta al momento hasta que pasen 4 minutos sin actividad suya o 9 minutos en total. La ventana de actividad se guarda en el estado
(`nube_activa_hasta`): si la ejecución llega al tope de 9 minutos con José todavía activo, lanza la siguiente de inmediato
(`workflow_dispatch` con el token del workflow) y la nueva continúa la misma ventana; con límites (6 encadenadas seguidas sin un toque
nuevo y 40 al día) para no abusar de Actions. El cron queda como red de seguridad.

PUBLICACIÓN PUNTUAL: si hay una pieza aprobada que vence en menos de 6 minutos, la ejecución espera hasta su hora y la publica en punto
(en vez de salir hasta 10 minutos tarde), atendiendo Telegram mientras espera.

NO monta Campo, ni el plan mensual, ni la producción: siguen siendo del PC. No lee main.py (arrastra planes).

Seguridad: si Telegram contesta 409 (otro proceso está leyendo el mismo bot, p. ej. el router del PC), la ejecución se detiene SIN
publicar nada: dos routers con dos colas distintas publicarían dos veces lo mismo.

Uso (desde motor/):
    python -X utf8 -m nube.ejecutar_nube --datos ../router_datos [--simulado] [--sin-telegram] [--pasadas 2] [--activo]
Variables: TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, IG_N1_TOKEN, IG_N1_USER_ID, GITHUB_TOKEN (nada de esto se imprime);
VIVAX_MODO_ACTIVO=0 apaga el modo activo y VIVAX_ENCADENAR=0 apaga el encadenado, aunque se pase --activo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
MADRID = ZoneInfo("Europe/Madrid")
SESION_MAX_S = 9 * 60            # una ejecución activa no pasa de 9 minutos (el job tiene 12 de margen)
SIN_ACTIVIDAD_S = 4 * 60         # y se corta tras 4 minutos sin que José toque nada
SALIDA_PUNTUAL_S = 6 * 60        # una pieza que vence en menos de esto se espera para publicarla en punto
SONDEO_S = 15                    # sondeo largo de Telegram en modo activo
ENCADENADAS_SEGUIDAS = 6         # encadenadas sin un toque nuevo de José
ENCADENADAS_AL_DIA = 40
ACTIVA_HASTA = "nube_activa_hasta"
CADENA = "nube_cadena"
COMANDOS = [("panel", "Abrir el panel"), ("pendientes", "Aprobar lo pendiente"), ("calendario", "Ver el calendario"),
            ("pausa", "Parar las publicaciones"), ("seguir", "Reanudar las publicaciones"), ("ayuda", "Qué significa cada cosa")]


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

    def enviar_foto(self, chat_id, ruta, caption, botones=None):
        self._n += 1
        self.enviados.append(f"[foto] {caption}")
        return 9_000_000 + self._n

    def editar_foto(self, chat_id, message_id, ruta, caption, botones=None):
        pass

    def editar_leyenda(self, chat_id, message_id, caption, botones=None):
        pass

    def borrar(self, chat_id, message_id):
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


def montar(datos: Path, env: dict, simulado: bool, motor: Path = MOTOR, modo_ig: str | None = None, reloj=None):
    """Hub y adaptador de Nutrición. Las demás cuentas del archivo se registran apagadas (Campo no vive en la nube).
    `reloj` (segundos desde 1970) solo lo cambian las pruebas: el mismo reloj para el hub, el adaptador y el router."""
    cfg = cargar_cuentas()
    Path(datos).mkdir(parents=True, exist_ok=True)
    hub = Hub(datos / "hub.sqlite", ahora=reloj) if reloj else Hub(datos / "hub.sqlite")
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
        extra = {"ahora": (lambda: datetime.fromtimestamp(reloj(), MADRID))} if reloj else {}
        ad = AdaptadorNutricion(c["codigo"], datos / "cuentas", salida=Path(motor) / "salida", politica=c.get("politica_aprobacion", "validado"),
                                horas=c.get("horas"), ig=ig, publicador=construir_publicador({**c, "instagram": ig}, env, datos, simulado), **extra)
        ad.guardar_token = lambda tok, datos=datos: _guardar_token(datos, tok)
        ad.estilo_panel = True                                          # avisos de publicación en llano y con los botones del panel
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


def pasada(router: Router, hub: Hub, ad, tg, sondear: bool, motor: Path, lg=log, espera_sondeo: int = 0, dormir=time.sleep) -> dict:
    """Una pasada completa. `espera_sondeo` > 0 (modo activo): el sondeo de Telegram espera hasta ese número de segundos a que José haga algo
    (sin Telegram, simplemente se duerme ese rato). Devuelve cuánto hizo: `updates` cuenta lo que José ha pulsado o escrito."""
    cuenta = {"importadas": 0, "ordenes": 0, "updates": 0, "decisiones": 0}
    try:
        router.decisiones.latido()                                # «última pasada» del panel (el latido no cuenta como cambio del estado)
    except Exception:                                             # noqa: BLE001
        pass
    cuenta["importadas"] = importar_nuevas(ad, hub, motor / "piezas" / "nube", lg)
    cuenta["ordenes"] = O.procesar_ordenes(hub, motor / "ordenes", lg)
    if sondear:
        try:
            for upd in tg.actualizaciones(hub.ultimo_update() + 1, espera_sondeo):
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
    elif espera_sondeo:
        dormir(espera_sondeo)
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


# ------------------------------------------------------------------ modo activo y publicación puntual
def proxima_salida_s(ad, ahora_s: float) -> float | None:
    """Segundos hasta la próxima pieza aprobada que puede salir sola (negativo si ya venció y aún no ha salido), o None si no hay ninguna
    o la publicación no puede salir ahora (sin publicador, en pausa, parada por Instagram)."""
    if ad.publicador is None or ad.ig.get("modo", "desactivado") == "desactivado" or ad.pausada():
        return None
    try:
        if ad._bloqueada(datetime.fromtimestamp(ahora_s, MADRID)):
            return None
    except Exception:                                             # noqa: BLE001
        return None
    mejor = None
    for r in ad.db.execute("SELECT programada, proximo_intento, simulada FROM piezas WHERE estado='aprobada' AND programada IS NOT NULL").fetchall():
        if ad.ig.get("modo") == "simulacion" and r["simulada"]:
            continue
        t = datetime.fromisoformat(r["programada"]).timestamp()
        if r["proximo_intento"]:
            t = max(t, datetime.fromisoformat(r["proximo_intento"]).timestamp())
        if mejor is None or t < mejor:
            mejor = t
    return None if mejor is None else mejor - ahora_s


def sesion_activa(router, hub, ad, tg, sondear: bool, motor: Path, lg, reloj, dormir, t0: float, primera: dict, max_s: float = SESION_MAX_S,
                  idle_s: float = SIN_ACTIVIDAD_S, puntual_s: float = SALIDA_PUNTUAL_S, sondeo_s: int = SONDEO_S) -> dict:
    """Sigue atendiendo mientras José esté activo (hasta `idle_s` sin que toque nada, o `max_s` en total) o mientras haya una pieza a punto de
    salir (en menos de `puntual_s`). Devuelve {activa, tope, pasadas, fin_actividad}: `tope` es que llegó al máximo con José aún activo."""
    previa = hub.meta_leer(ACTIVA_HASTA)
    try:
        fin_act = float(previa) if previa else 0.0
    except ValueError:
        fin_act = 0.0
    if fin_act <= reloj():
        fin_act = 0.0
    nuevo_toque = bool(primera["updates"])
    if nuevo_toque:
        fin_act = reloj() + idle_s
    res = {"activa": fin_act > reloj(), "tope": False, "pasadas": 0, "fin_actividad": fin_act, "nuevo_toque": nuevo_toque}
    while True:
        ahora = reloj()
        restante = max_s - (ahora - t0)
        activo = ahora < fin_act
        if restante <= 1:
            res["tope"] = activo
            break
        prox = proxima_salida_s(ad, ahora)
        esperando = prox is not None and prox <= puntual_s
        if not (activo or esperando):
            break
        espera = sondeo_s if (activo or prox is None or prox < 0) else max(1, min(sondeo_s, int(prox) + 1))
        espera = int(max(1, min(espera, restante)))
        c = pasada(router, hub, ad, tg, sondear, motor, lg, espera_sondeo=espera, dormir=dormir)
        res["pasadas"] += 1
        if c["updates"]:
            res["nuevo_toque"] = True
            fin_act = reloj() + idle_s
            res["activa"] = True
        res["fin_actividad"] = fin_act
        if prox is not None and prox < 0 and not c["updates"] and not activo:
            break                                                 # vencida y no sale (en reintento, en pausa...): no se queda esperando
    return res


def _presupuesto_cadena(hub, ahora_s: float, nuevo_toque: bool) -> tuple[bool, dict]:
    """¿Se puede encadenar otra ejecución? Como mucho 6 seguidas sin un toque nuevo de José y 40 al día. Devuelve (sí o no, estado actualizado)."""
    try:
        e = json.loads(hub.meta_leer(CADENA) or "{}")
    except ValueError:
        e = {}
    hoy = datetime.fromtimestamp(ahora_s, MADRID).date().isoformat()
    if e.get("dia") != hoy:
        e = {"dia": hoy, "dia_n": 0, "seguidas": 0}
    if nuevo_toque:
        e["seguidas"] = 0
    if e.get("seguidas", 0) >= ENCADENADAS_SEGUIDAS or e.get("dia_n", 0) >= ENCADENADAS_AL_DIA:
        return False, e
    e["seguidas"] = e.get("seguidas", 0) + 1
    e["dia_n"] = e.get("dia_n", 0) + 1
    return True, e


def lanzar_siguiente(env: dict, lg=log) -> bool:
    """Pide a GitHub otra ejecución del workflow (workflow_dispatch con el token del propio workflow, permiso `actions: write`). Queda en
    cola detrás de la actual (grupo de concurrencia). False si no se pudo; nunca lanza."""
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", "")
    if not repo or not token:
        lg("encadenar: faltan GITHUB_REPOSITORY o el token del workflow; no se lanza otra ejecución")
        return False
    try:
        import requests                                           # noqa: PLC0415
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/router.yml/dispatches", timeout=20,
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                          json={"ref": os.environ.get("GITHUB_REF_NAME") or "main", "inputs": {"prueba": "false"}})
    except Exception as exc:                                      # noqa: BLE001 (la red de GitHub puede fallar; el cron sigue ahí)
        lg(f"encadenar: no se pudo lanzar otra ejecución ({type(exc).__name__})")
        return False
    if r.status_code != 204:
        lg(f"encadenar: GitHub contestó {r.status_code}; no se lanza otra ejecución")
        return False
    return True


def ejecutar(datos: Path, env: dict, simulado: bool, sin_telegram: bool, pasadas: int = 2, espera_s: float = 20.0, motor: Path = MOTOR,
             tg=None, modo_ig: str | None = None, exigir_estado: bool = False, activo: bool = False, encadenar: bool = False, reloj=None,
             dormir=time.sleep, lanzar=None, max_s: float = SESION_MAX_S) -> int:
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
    reloj = reloj or time.time
    hub, adaptadores = montar(datos, env, simulado, motor, modo_ig, reloj=None if reloj is time.time else reloj)
    hub.establecer_admin(int(admin), "administrador")
    if exigir_estado and not sin_telegram and hub.ultimo_update() == 0:
        print("El estado no trae el último mensaje de Telegram procesado (¿base vacía o sin migrar?). No leo Telegram: volvería a procesar "
              "pulsaciones viejas. Migra el estado con migrar_a_nube.py --definitivo.", file=sys.stderr)
        return 4
    if tg is not None:                                                  # las pruebas inyectan su Telegram
        pass
    elif sin_telegram:
        tg = TelegramMudo()
    else:
        if not env.get("TELEGRAM_BOT_TOKEN"):
            print("Falta TELEGRAM_BOT_TOKEN.", file=sys.stderr)
            return 2
        tg = TelegramReal(env["TELEGRAM_BOT_TOKEN"])
    router = Router(hub, tg, adaptadores, "nube", ahora=reloj, planes=None, produccion=None, log=lambda m: log(f"[router] {m}"), panel=True)
    ad = adaptadores["n1"]
    modo = ad.ig.get("modo", "desactivado")
    activo = activo and not sin_telegram                                # sin Telegram no hay a quién atender
    log(f"Pasada en la nube: modo {modo}{' (publicador simulado)' if simulado else ''}{', sin Telegram' if sin_telegram else ''}"
        f"{', modo activo' if activo else ''}.")
    t0 = reloj()
    try:
        _poner_menu(hub, tg, sin_telegram)
        c = {"updates": 0}
        for i in range(1, pasadas + 1):
            try:
                c = pasada(router, hub, ad, tg, not sin_telegram, motor, dormir=dormir)
            except ConflictoTelegram:
                print("Telegram da 409: otro proceso está leyendo este bot (¿el router del PC sigue en marcha?). "
                      "No publico nada para no duplicar. Para el router local y vuelve a ejecutar.", file=sys.stderr)
                return CODIGO_CONFLICTO
            log(f"pasada {i}: {c}")
            if i < pasadas and (c["updates"] or c["decisiones"] or c["importadas"]):
                dormir(espera_s)
            else:
                break
        if activo:
            try:
                s = sesion_activa(router, hub, ad, tg, True, motor, log, reloj, dormir, t0, c, max_s=max_s)
            except ConflictoTelegram:
                print("Telegram da 409: otro proceso está leyendo este bot. Me detengo sin publicar más.", file=sys.stderr)
                return CODIGO_CONFLICTO
            if s["pasadas"]:
                log(f"sesión: {s['pasadas']} pasadas más, activa={s['activa']}, tope={s['tope']}")
            nueva = str(int(s["fin_actividad"])) if s["fin_actividad"] > reloj() else ""
            if (hub.meta_leer(ACTIVA_HASTA) or "") != nueva:             # solo se escribe si cambia (un cambio del estado es una subida del estado)
                hub.meta_escribir(ACTIVA_HASTA, nueva)
            if encadenar and s["tope"]:
                ok, e = _presupuesto_cadena(hub, reloj(), s["nuevo_toque"])
                hub.meta_escribir(CADENA, json.dumps(e))
                if ok:
                    lanzado = (lanzar or lanzar_siguiente)(env, log)
                    log("encadenada la siguiente ejecución." if lanzado else "no se pudo encadenar la siguiente ejecución.")
                else:
                    log("no se encadena otra ejecución: se llegó al límite de encadenadas.")
        return 0
    finally:
        for base in (hub.db, ad.db):                                    # que nada quede abierto antes de cifrar el estado
            try:
                base.cerrar()
            except Exception:                                           # noqa: BLE001
                pass


def _poner_menu(hub, tg, sin_telegram: bool) -> None:
    """El menú de comandos del bot (/panel, /pendientes…), una sola vez: se vuelve a poner solo si cambia la lista."""
    if sin_telegram:
        return
    huella = json.dumps(COMANDOS, ensure_ascii=False)
    if hub.meta_leer("panel:menu") == huella:
        return
    try:
        tg.poner_menu(COMANDOS)
        hub.meta_escribir("panel:menu", huella)
    except ErrorTelegram as exc:
        log(f"menú de comandos: telegram {exc.metodo} {exc.codigo}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nube.ejecutar_nube")
    ap.add_argument("--datos", required=True, type=Path, help="carpeta con hub.sqlite y cuentas/n1/estado.sqlite (el estado descifrado)")
    ap.add_argument("--simulado", action="store_true", help="publicador de Instagram simulado (no sale a la red)")
    ap.add_argument("--sin-telegram", action="store_true", help="no leer ni escribir en Telegram")
    ap.add_argument("--pasadas", type=int, default=2)
    ap.add_argument("--activo", action="store_true", help="modo activo: sigue atendiendo a José mientras use Telegram y espera a las piezas a punto de salir")
    a = ap.parse_args(argv)
    env = {k: os.environ.get(k, "") for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID", "IG_N1_TOKEN", "IG_N1_USER_ID", "GITHUB_TOKEN")}
    activo = a.activo and os.environ.get("VIVAX_MODO_ACTIVO", "1") != "0"
    encadenar = activo and os.environ.get("VIVAX_ENCADENAR", "1") != "0"
    return ejecutar(a.datos, env, a.simulado, a.sin_telegram, a.pasadas, exigir_estado=not a.simulado, activo=activo, encadenar=encadenar)


if __name__ == "__main__":
    raise SystemExit(main())
