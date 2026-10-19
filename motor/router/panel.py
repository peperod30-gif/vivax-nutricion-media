"""El panel de Telegram de Vivax Nutrición (13-oct-2026): lo que José ve y toca para aprobar todo desde el móvil.

Un mensaje «Panel» que se edita (no se inunda el chat), con cuatro cosas: aprobar lo pendiente (de golpe o una a una), el calendario, lo ya
publicado y un menú «Más». Los botones llevan datos `pn:<acción>:<argumentos>` (hasta 64 bytes) y solo los pulsa el administrador. Todo pasa
por el mismo adaptador de siempre (`aprobar`, `cambiar_hora`, `quitar_de_cola`, `rechazar`), con su candado y su auditoría: el panel no
decide nada, solo lo hace más fácil. Los textos viven en `router/textos_panel.py`.

Se activa con `Router(..., panel=True)` (la nube lo hace; el router del PC sigue como estaba). Sin una cuenta de Nutrición con programación,
el panel no está disponible y todo sigue como siempre.

Estado en el hub (tabla `meta`): `panel:rev:<uid>` guarda la sesión de «Revisar una a una» (qué piezas, por cuál va, cuántas aprobadas,
rechazadas y saltadas, y el número de sus mensajes), `panel:htctx:<uid>` el contexto de «Escribir hora» y `panel:push:<clave>` lo que ya se ha
avisado (cada aviso del día sale una sola vez).
"""
from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import textos_panel as T
from .hub import ESPERA_HORA_S, LATIDO, MADRID, OPCION_CADUCA_S, ErrorHora, analizar_hora, desde_iso
from .telegram_io import ErrorTelegram

SIN_ACCESO = "No tienes acceso a eso."
METRICAS = Path(__file__).resolve().parents[1] / "datos" / "metricas"
HORA_RESUMEN = 8                # el resumen de «hoy salen» no sale antes de las 08:00
HORA_COLA = 10                  # ni el aviso de cola corta antes de las 10:00
HORA_NOCHE = (21, 30)           # el resumen de «mañana sale esto» (20-oct): un único mensaje al día, con imagen
DIAS_COLA_CORTA = 3
MINIMO = timedelta(minutes=2)   # una hora programada queda al menos dos minutos por delante


@dataclass
class Item:
    id: int
    pieza_id: str
    titulo: str
    estado: str
    programada: datetime | None
    publicada: datetime | None
    permalink: str | None
    error: str | None
    intentos: int
    formato: str
    pie: str
    prevista: datetime | None

    def d(self) -> dict:
        return {"id": self.id, "titulo": self.titulo, "estado": self.estado, "programada": self.programada, "publicada": self.publicada,
                "permalink": self.permalink, "error": self.error, "prevista": self.prevista, "pie": self.pie}

    @property
    def cuando(self) -> datetime | None:
        """Cuándo cuenta esta pieza en el calendario: su hora programada, la de publicación o la prevista por el plan."""
        return self.programada or self.publicada or self.prevista


def _iso(t: str | None) -> datetime | None:
    if not t:
        return None
    try:
        return datetime.fromisoformat(t).astimezone(MADRID)
    except ValueError:
        return None


def firma(ids) -> str:
    """Un resumen corto de qué piezas se iban a aprobar: si la lista cambia entre que se pide y que se confirma, no se aprueba a ciegas."""
    return format(zlib.crc32(",".join(str(i) for i in sorted(ids)).encode()), "08x")


class Panel:
    def __init__(self, router, activo: bool = False, metricas: Path | None = None):
        self.r = router
        self.activo = activo
        self.metricas = Path(metricas) if metricas else METRICAS
        self._manejadores = {
            "p": self._b_p, "pe": self._b_pe, "ap": self._b_ap, "ao": self._b_ao, "rv": self._b_rv, "rf": self._b_rf, "sk": self._b_sk,
            "f": self._b_f, "ok": self._b_ok, "no": self._b_no, "ch": self._b_ch, "hv": self._b_hv, "ht": self._b_ht, "qu": self._b_qu,
            "rt": self._b_rt, "vh": self._b_vh, "vp": self._b_vp, "cal": self._b_cal, "pu": self._b_pu, "mas": self._b_mas,
            "num": self._b_num, "te": self._b_te, "pa": self._b_pa, "ay": self._b_ay, "nd": self._b_nd,
        }

    # ------------------------------------------------------------------ base
    @property
    def hub(self):
        return self.r.hub

    def _ahora(self) -> datetime:
        return datetime.fromtimestamp(self.r.ahora(), timezone.utc).astimezone(MADRID)

    def cuenta(self):
        """(código, adaptador) de la cuenta de Nutrición con programación que maneja el panel, o None."""
        for codigo, ad in self.r.adaptadores.items():
            if getattr(ad, "programable", False) and not getattr(ad, "anfitrion", False) and hasattr(ad, "filas_panel") and self.hub.cuenta(codigo):
                return codigo, ad
        return None

    def disponible(self) -> bool:
        return self.activo and self.cuenta() is not None

    def _log(self, m: str) -> None:
        self.r.log(f"panel: {m}")

    def _items(self, estados: tuple[str, ...] | None = None) -> list[Item]:
        codigo, ad = self.cuenta()
        sal = []
        for f in ad.filas_panel(estados):
            prevista = ad.hora_prevista(f["id"]) if f["estado"] == "pendiente" else None
            sal.append(Item(f["id"], f["pieza_id"], f["titulo"], f["estado"], _iso(f["programada"]), _iso(f["publicada"]), f["ig_permalink"],
                            f["error"], f["intentos"] or 0, f["formato"], f["pie"] or "", prevista))
        return sal

    def _item(self, id: int) -> Item | None:
        codigo, ad = self.cuenta()
        f = ad.fila(id)
        if f is None:
            return None
        return Item(f["id"], f["pieza_id"], f["titulo"], f["estado"], _iso(f["programada"]), _iso(f["publicada"]), f["ig_permalink"], f["error"],
                    f["intentos"] or 0, f["formato"], f["pie"] or "", ad.hora_prevista(id) if f["estado"] == "pendiente" else None)

    @staticmethod
    def _orden_pendientes(items: list[Item]) -> list[Item]:
        grandes = datetime.max.replace(tzinfo=timezone.utc)
        return sorted(items, key=lambda i: (i.prevista is None, i.prevista or grandes, i.id))

    def _pendientes(self) -> list[Item]:
        return self._orden_pendientes(self._items(("pendiente",)))

    # ------------------------------------------------------------------ enviar y editar
    def _decir(self, chat_id: int, texto: str, botones=None) -> int:
        return self.r._decir(chat_id, texto, botones)

    def _editar(self, chat_id: int, mid: int | None, texto: str, botones=None, foto: bool = False) -> int | None:
        """Cambia el mensaje donde se pulsó. Si Telegram no deja (mensaje viejo o borrado), manda uno nuevo: la acción no se pierde."""
        if mid:
            try:
                if foto:
                    self.r.tg.editar_leyenda(chat_id, mid, texto, botones)
                else:
                    self.r.tg.editar(chat_id, mid, texto, botones)
                return mid
            except ErrorTelegram as exc:
                self._log(f"no se pudo editar el mensaje ({exc.metodo} {exc.codigo}); lo envío de nuevo")
        return self._decir(chat_id, texto, botones)

    def _toast(self, cb_id: str, texto: str = "", alerta: bool = False) -> None:
        self.r._responder(cb_id, texto, alerta)

    # ------------------------------------------------------------------ entradas desde el router
    def comando(self, chat_id: int, uid: int, orden: str) -> bool:
        """/start, /panel, /pendientes, /calendario y /ayuda del administrador. True si los ha tomado el panel."""
        if not self.disponible() or not self.hub.es_admin(uid):
            return False
        self._fin_espera_hora(uid)
        if orden in ("/start", "/panel"):
            self._pantalla_panel(chat_id, None, nuevo=True)
        elif orden == "/pendientes":
            self._pantalla_pendientes(chat_id, None)
        elif orden == "/calendario":
            self._pantalla_calendario(chat_id, None, 0)
        elif orden == "/ayuda":
            texto, botones = T.ayuda()
            self._decir(chat_id, texto, botones)
        else:
            return False
        return True

    def boton(self, cb_id: str, chat_id: int, msg_id: int | None, uid: int, data: str) -> None:
        """Una pulsación de un botón `pn:`. El router ya comprobó el chat y que la persona está dada de alta."""
        if not self.disponible() or not self.hub.es_admin(uid):
            self.hub.auditar(uid, "boton_denegado", data[:64])
            return self._toast(cb_id, SIN_ACCESO, True)
        partes = data.split(":")
        accion, args = (partes[1] if len(partes) > 1 else ""), partes[2:]
        fn = self._manejadores.get(accion)
        if fn is None:
            return self._toast(cb_id, "Ese botón ya no vale. Abre el /panel.", True)
        if accion != "ht":
            self._fin_espera_hora(uid)                   # cualquier otro toque deja sin efecto «Escribir hora»
        try:
            fn(cb_id, chat_id, msg_id, uid, args)
        except ErrorTelegram as exc:
            self._log(f"telegram: {exc.metodo} {exc.codigo}")
            self._toast(cb_id, "Telegram no ha contestado. Vuelve a pulsar.", True)
        except Exception as exc:                          # noqa: BLE001 (un botón roto no tumba el router)
            self._log(f"botón {data[:40]}: {type(exc).__name__}: {str(exc)[:120]}")
            self._toast(cb_id, "Algo ha fallado. Abre el /panel y prueba otra vez.", True)

    def _fin_espera_hora(self, uid: int) -> None:
        e = self.hub.esperando(uid)
        if e is not None and e["tipo"] == "horapn":
            self.hub.dejar_de_esperar(uid)

    # ------------------------------------------------------------------ pantalla: panel
    def _datos_panel(self) -> dict:
        codigo, ad = self.cuenta()
        ahora = self._ahora()
        items = self._items()
        prog = sorted([i for i in items if i.estado in ("aprobada", "publicando") and i.programada], key=lambda i: i.programada)
        pend = [i for i in items if i.estado == "pendiente"]
        hasta = prog[-1].programada if prog else None
        dias_cola = max(0, (hasta.date() - ahora.date()).days + 1) if hasta else 0
        v = self.hub.meta_leer(LATIDO)
        try:
            pasada = int(max(0, (self.r.ahora() - float(v)) // 60)) if v else None
        except ValueError:
            pasada = None
        return {"ahora": ahora, "siguiente": (prog[0].programada, prog[0].titulo) if prog else None, "pendientes": len(pend),
                "programadas": len(prog), "hasta": hasta, "dias_cola": dias_cola, "en_pausa": ad.pausada(), "pasada": pasada}

    def _pantalla_panel(self, chat_id: int, msg_id: int | None, nota: str = "", nuevo: bool = False) -> int | None:
        d = self._datos_panel()
        texto, botones = T.panel(d["ahora"].date(), d["ahora"], d["siguiente"], d["pendientes"], d["programadas"], d["hasta"], d["dias_cola"],
                                 d["en_pausa"], d["pasada"], nota)
        if nuevo or not msg_id:
            return self._decir(chat_id, texto, botones)
        return self._editar(chat_id, msg_id, texto, botones)

    def _b_p(self, cb_id, chat_id, msg_id, uid, args):
        self._pantalla_panel(chat_id, msg_id)
        self._toast(cb_id)

    # ------------------------------------------------------------------ pantalla: pendientes y aprobar de golpe
    def _pantalla_pendientes(self, chat_id: int, msg_id: int | None, nota: str = "") -> None:
        pend = self._pendientes()
        texto, botones = T.pendientes([{"titulo": i.titulo, "prevista": i.prevista} for i in pend], self._ahora().date())
        if nota:
            texto = nota + "\n\n" + texto
        if msg_id:
            self._editar(chat_id, msg_id, texto, botones)
        else:
            self._decir(chat_id, texto, botones)

    def _b_pe(self, cb_id, chat_id, msg_id, uid, args):
        self._pantalla_pendientes(chat_id, msg_id)
        self._toast(cb_id)

    def _b_ap(self, cb_id, chat_id, msg_id, uid, args):
        """«Aprobar las N»: antes de hacer nada, un resumen y un toque de confirmación."""
        pend = self._pendientes()
        if not pend:
            self._pantalla_pendientes(chat_id, msg_id)
            return self._toast(cb_id, "No hay nada por aprobar.")
        ahora = self._ahora()
        con = [i.prevista for i in pend if i.prevista and i.prevista > ahora + MINIMO]
        texto, botones = T.confirmar_lote(len(pend), min(con) if con else None, max(con) if con else None, len(pend) - len(con), firma(i.id for i in pend))
        self._editar(chat_id, msg_id, texto, botones)
        self._toast(cb_id)

    def _b_ao(self, cb_id, chat_id, msg_id, uid, args):
        """Confirmado: programa cada pieza en su hora prevista. Si la lista ya no es la que se confirmó, no aprueba nada."""
        pend = self._pendientes()
        if not pend:
            self._pantalla_pendientes(chat_id, msg_id)
            return self._toast(cb_id, "Ya estaba hecho: no queda nada por aprobar.")
        if not args or args[0] != firma(i.id for i in pend):
            self._pantalla_pendientes(chat_id, msg_id, nota="La lista ha cambiado desde que la miraste. Revísala otra vez.")
            return self._toast(cb_id, "La lista ha cambiado: no he aprobado nada.", True)
        ok, fallos = [], []
        codigo, ad = self.cuenta()
        ahora = self._ahora()
        previstas = {it.id: ad.hora_prevista(it.id) for it in pend}
        # primero las que llevan hora exacta del plan (por orden de hora) y después las que van al primer hueco libre: así un hueco no se
        # lo come una pieza que no tenía hora justo antes de que otra la pida
        con_hora = lambda it: previstas[it.id] if previstas[it.id] is not None and previstas[it.id] > ahora + MINIMO else None   # noqa: E731
        for it in sorted(pend, key=lambda it: (con_hora(it) is None, con_hora(it) or ahora)):
            motivo, dt = self._aprobar(uid, it.id, origen="panel:lote")
            if motivo:
                fallos.append((it.titulo, motivo))
            else:
                ok.append({"titulo": it.titulo, "hora": dt})
        self.hub.auditar(uid, "lote_aprobado", f"{len(ok)} de {len(pend)}")
        texto, botones = T.resultado_lote(ok, fallos)
        self._editar(chat_id, msg_id, texto, botones)
        self._toast(cb_id, f"Aprobadas: {len(ok)}")

    def _aprobar(self, uid: int, id: int, origen: str, hora: datetime | None = None, plan: bool = True) -> tuple[str | None, datetime | None]:
        """Aprueba una pieza pendiente: en `hora` si se da; si no, en su hora prevista por el plan (si queda a más de dos minutos); y si no hay, en
        el primer hueco libre. Devuelve (motivo del fallo, hora en que queda)."""
        codigo, ad = self.cuenta()
        ahora = self._ahora()
        if hora is None and plan:
            prev = ad.hora_prevista(id)
            hora = prev if prev is not None and prev > ahora + MINIMO else None
            if hora is not None and ad.hora_ocupada(hora):
                hora = None                              # otra pieza ya sale a esa hora: esta, al primer hueco libre
        motivo = ad.aprobar(id, uid, para_admin=True, hora=hora, origen=origen)
        if motivo:
            return motivo, None
        self.hub.auditar(uid, "pieza_aprobada", f"{codigo}:{id}")
        return None, _iso(ad.fila(id)["programada"])

    # ------------------------------------------------------------------ revisar una a una
    def _cargar(self, uid: int) -> dict | None:
        v = self.hub.meta_leer(f"panel:rev:{uid}")
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def _guardar(self, uid: int, ses: dict) -> None:
        self.hub.meta_escribir(f"panel:rev:{uid}", json.dumps(ses, ensure_ascii=False))

    def _olvidar(self, uid: int) -> None:
        self.hub.db.execute("DELETE FROM meta WHERE k=?", (f"panel:rev:{uid}",))
        self.hub.db.commit()

    def _portada(self, id: int) -> Path | None:
        codigo, ad = self.cuenta()
        p = ad.pieza(id)
        if p is None or p.formato == "video":
            return None
        for f in p.archivos:
            if Path(f).is_file():
                return Path(f)
        return None

    def _b_rv(self, cb_id, chat_id, msg_id, uid, args):
        pend = self._pendientes()
        if not pend:
            self._pantalla_pendientes(chat_id, msg_id)
            return self._toast(cb_id, "No hay nada por aprobar.")
        vieja = self._cargar(uid)
        if vieja and vieja.get("msg"):                         # una revisión anterior sin acabar: se retira su ficha
            self._borrar(chat_id, vieja["msg"])
        ses = {"ids": [i.id for i in pend], "i": 0, "ok": 0, "no": 0, "sk": 0, "panel": msg_id, "msg": None, "modo": "texto"}
        self._toast(cb_id)
        self._mostrar_revision(chat_id, uid, ses)

    def _borrar(self, chat_id: int, mid: int | None) -> None:
        if not mid:
            return
        try:
            self.r.tg.borrar(chat_id, mid)
        except (ErrorTelegram, AttributeError) as exc:
            self._log(f"no se pudo borrar el mensaje {mid} ({type(exc).__name__})")

    def _mostrar_revision(self, chat_id: int, uid: int, ses: dict, nota: str = "") -> None:
        while ses["i"] < len(ses["ids"]):
            it = self._item(ses["ids"][ses["i"]])
            if it is not None and it.estado == "pendiente":
                break
            ses["i"] += 1                                      # ya no está pendiente (la decidió otra cosa): se pasa de largo
        else:
            return self._fin_revision(chat_id, uid, ses)
        texto, botones = T.tarjeta_revision(ses["i"] + 1, len(ses["ids"]), it.d())
        if nota:
            texto = nota + "\n\n" + texto
        self._poner_tarjeta(chat_id, ses, texto, botones, self._portada(it.id))
        self._guardar(uid, ses)

    def _poner_tarjeta(self, chat_id: int, ses: dict, texto: str, botones, ruta: Path | None) -> None:
        """Un solo mensaje que se edita pieza a pieza: foto con su texto si hay portada; si no, solo texto."""
        tg = self.r.tg
        mid, modo = ses.get("msg"), ses.get("modo")
        if ruta is not None:
            if mid and modo == "foto":
                try:
                    tg.editar_foto(chat_id, mid, ruta, texto, botones)
                    return
                except ErrorTelegram as exc:
                    self._log(f"no se pudo cambiar la foto ({exc.metodo} {exc.codigo}); la envío de nuevo")
                    mid = None
            elif mid:
                self._borrar(chat_id, mid)
                mid = None
            ses["msg"], ses["modo"] = tg.enviar_foto(chat_id, ruta, texto, botones), "foto"
            return
        if mid and modo == "foto":
            self._borrar(chat_id, mid)
            mid = None
        if mid:
            try:
                tg.editar(chat_id, mid, texto, botones)
                return
            except ErrorTelegram as exc:
                self._log(f"no se pudo editar la ficha ({exc.metodo} {exc.codigo}); la envío de nuevo")
        ses["msg"], ses["modo"] = self._decir(chat_id, texto, botones), "texto"

    def _fin_revision(self, chat_id: int, uid: int, ses: dict) -> None:
        self._borrar(chat_id, ses.get("msg"))
        falt = len(self._pendientes())
        resumen = T.resumen_revision(ses["ok"], ses["no"], ses["sk"], falt)
        self._olvidar(uid)
        self._pantalla_panel(chat_id, ses.get("panel"), nota=resumen)

    def _b_rf(self, cb_id, chat_id, msg_id, uid, args):
        ses = self._cargar(uid)
        self._toast(cb_id)
        if ses is None:
            return self._pantalla_panel(chat_id, msg_id)
        self._fin_revision(chat_id, uid, ses)

    def _b_sk(self, cb_id, chat_id, msg_id, uid, args):
        ses = self._cargar(uid)
        if ses is None or not args or ses["i"] >= len(ses["ids"]) or ses["ids"][ses["i"]] != int(args[0]):
            return self._toast(cb_id, "Ya estaba hecho.")
        ses["sk"] += 1
        ses["i"] += 1
        self._toast(cb_id, "Saltada")
        self._mostrar_revision(chat_id, uid, ses)

    def _paso_revision(self, cb_id, chat_id, uid, id: int, clave: str, nota: str, toast: str) -> bool:
        """Tras decidir una pieza dentro de la revisión: cuenta, avanza y enseña la siguiente. False si no había revisión o ya se había decidido."""
        ses = self._cargar(uid)
        if ses is None:
            return False
        ses[clave] += 1
        if ses["i"] < len(ses["ids"]) and ses["ids"][ses["i"]] == id:
            ses["i"] += 1
        self._toast(cb_id, toast)
        self._mostrar_revision(chat_id, uid, ses, nota=nota)
        return True

    def _en_revision(self, uid: int, id: int) -> bool:
        """¿Es esta la pieza que la revisión tiene a la vista ahora mismo? (Una pulsación repetida sobre una ya decidida no vale.)"""
        ses = self._cargar(uid)
        return ses is not None and ses["i"] < len(ses["ids"]) and ses["ids"][ses["i"]] == id

    # ------------------------------------------------------------------ pantalla: ficha de una pieza y sus acciones
    def _ver_ficha(self, chat_id: int, msg_id: int | None, it: Item, ctx: str, nota: str = "") -> None:
        texto, botones = T.ficha(it.d(), nota, ctx)
        self._editar(chat_id, msg_id, texto, botones)

    def _hay_pieza(self, cb_id, args) -> Item | None:
        if not args or not args[0].isdigit():
            self._toast(cb_id, "Ese botón ya no vale. Abre el /panel.", True)
            return None
        it = self._item(int(args[0]))
        if it is None:
            self._toast(cb_id, "Esa pieza ya no existe.", True)
        return it

    @staticmethod
    def _ctx(args) -> str:
        return args[1] if len(args) > 1 else "p"

    def _b_f(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        self._ver_ficha(chat_id, msg_id, it, self._ctx(args))
        self._toast(cb_id)

    def _b_ok(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        ctx = self._ctx(args)
        if ctx == "r" and not self._en_revision(uid, it.id):
            return self._toast(cb_id, "Ya estaba hecho.")
        if it.estado != "pendiente":
            self._ver_ficha(chat_id, msg_id, it, "p" if ctx == "r" else ctx, nota=f"Esa pieza ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
            return self._toast(cb_id, f"Ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
        motivo, dt = self._aprobar(uid, it.id, origen="panel:ok")
        if motivo:
            return self._toast(cb_id, motivo[:190], True)
        nota = f"✅ Aprobada: sale el {T.cuando(dt)}."
        if ctx == "r":
            self._paso_revision(cb_id, chat_id, uid, it.id, "ok", nota, T.toast_aprobada(T.cuando(dt)))
            return
        self._ver_ficha(chat_id, msg_id, self._item(it.id), ctx, nota=nota)
        self._toast(cb_id, T.toast_aprobada(T.cuando(dt)))

    def _b_no(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        ctx = self._ctx(args)
        if ctx == "r" and not self._en_revision(uid, it.id):
            return self._toast(cb_id, "Ya estaba hecho.")
        codigo, ad = self.cuenta()
        if it.estado == "pendiente":
            motivo = ad.rechazar(it.id, uid)
        elif it.estado == "error":
            motivo = ad.descartar(it.id, uid)
        else:
            self._ver_ficha(chat_id, msg_id, it, "p" if ctx == "r" else ctx, nota=f"Esa pieza ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
            return self._toast(cb_id, f"Ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
        if motivo:
            return self._toast(cb_id, motivo[:190], True)
        self.hub.auditar(uid, "pieza_rechazada", f"{codigo}:{it.id}")
        if ctx == "r":
            self._paso_revision(cb_id, chat_id, uid, it.id, "no", "❌ Rechazada.", "Rechazada")
            return
        self._ver_ficha(chat_id, msg_id, self._item(it.id), ctx, nota="❌ Rechazada.")
        self._toast(cb_id, "Rechazada")

    def _b_qu(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        ctx = self._ctx(args)
        codigo, ad = self.cuenta()
        if it.estado != "aprobada":
            self._ver_ficha(chat_id, msg_id, it, ctx, nota=f"Esa pieza ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
            return self._toast(cb_id, f"Ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
        motivo = ad.quitar_de_cola(it.id, uid, origen="panel:qu")
        if motivo:
            return self._toast(cb_id, motivo[:190], True)
        self.hub.auditar(uid, "pieza_quitada_cola", f"{codigo}:{it.id}")
        self._ver_ficha(chat_id, msg_id, self._item(it.id), ctx, nota="↩️ Quitada de la cola: vuelve a «Por aprobar».")
        self._toast(cb_id, "Quitada de la cola")

    def _b_rt(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        ctx = self._ctx(args)
        codigo, ad = self.cuenta()
        if it.estado != "error":
            self._ver_ficha(chat_id, msg_id, it, ctx, nota=f"Esa pieza ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
            return self._toast(cb_id, f"Ya estaba: {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.")
        motivo = ad.reintentar(it.id, uid)
        if motivo:
            return self._toast(cb_id, motivo[:190], True)
        self.hub.auditar(uid, "pieza_reintentada", f"{codigo}:{it.id}")
        nuevo = self._item(it.id)
        self._ver_ficha(chat_id, msg_id, nuevo, ctx, nota=f"🔁 Otra vez en la cola: sale el {T.cuando(nuevo.programada)}." if nuevo.programada else "🔁 Otra vez en la cola.")
        self._toast(cb_id, "Reintentada")

    # ------------------------------------------------------------------ ver las hojas y el pie completo
    def _b_vh(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        codigo, ad = self.cuenta()
        p = ad.pieza(it.id)
        if p is None or not p.archivos:
            return self._toast(cb_id, "Esta pieza no tiene hojas que enseñar.", True)
        self._toast(cb_id, "Te las mando")
        self.r.tg.enviar_archivos(chat_id, p.formato, p.archivos, f"Hojas de «{T.corto(it.titulo, 80)}»")

    def _b_vp(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        self._toast(cb_id)
        self._decir(chat_id, f"Pie de «{T.corto(it.titulo, 60)}»:\n\n{it.pie}")

    # ------------------------------------------------------------------ elegir otra hora
    def _editar_ctx(self, chat_id: int, uid: int, msg_id: int | None, ctx: str, texto: str, botones) -> None:
        """Edita el mensaje donde está la ficha: en la revisión es una foto (se cambia su texto); en lo demás, un mensaje de texto."""
        if ctx == "r":
            ses = self._cargar(uid)
            self._editar(chat_id, msg_id, texto, botones, foto=bool(ses and ses.get("modo") == "foto"))
        else:
            self._editar(chat_id, msg_id, texto, botones)

    def _b_ch(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        ctx = self._ctx(args)
        if it.estado not in ("pendiente", "aprobada", "error"):
            return self._toast(cb_id, f"Esa pieza ya está {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}: no se puede cambiar la hora.", True)
        codigo, ad = self.cuenta()
        ahora = self._ahora()
        actual = it.programada if it.estado == "aprobada" else None
        plan = it.prevista if it.estado == "pendiente" else None
        if plan is not None and plan <= ahora + MINIMO:
            plan = None
        libres = [t for t in ad.huecos_libres(ahora, dias=7, excluir=it.id)
                  if (plan is None or t.astimezone(timezone.utc) != plan.astimezone(timezone.utc))
                  and (actual is None or t.astimezone(timezone.utc) != actual.astimezone(timezone.utc))]
        horas = ([(plan, f"📅 Como dice el plan: {T.cuando(plan)}")] if plan is not None else [])
        horas += [(t, f"🕗 {T.cuando_rel(t, ahora.date())}") for t in libres[:4]]
        nums = self.hub.crear_opciones(uid, codigo, it.id, it.estado, horas) if horas else []
        texto, botones = T.selector_hora(it.d(), [(et, f"pn:hv:{n}:{ctx}") for (t, et), n in zip(horas, nums)], ctx)
        self._editar_ctx(chat_id, uid, msg_id, ctx, texto, botones)
        self._toast(cb_id)

    def _aplicar_hora(self, uid: int, it: Item, instante: datetime, origen: str) -> tuple[str | None, str]:
        """Pone la pieza a esa hora según su estado: la aprueba (pendiente), cambia su hora (aprobada) o la reintenta (con problema).
        Devuelve (motivo del fallo, frase de lo hecho)."""
        codigo, ad = self.cuenta()
        if it.estado == "pendiente":
            motivo = ad.aprobar(it.id, uid, para_admin=True, hora=instante, origen=origen)
            hecho = f"✅ Aprobada: sale el {T.cuando(instante)}."
        elif it.estado == "aprobada":
            motivo = ad.cambiar_hora(it.id, instante, uid, origen=origen)
            hecho = f"🕐 Hora cambiada: sale el {T.cuando(instante)}."
        else:
            motivo = ad.reintentar(it.id, uid, hora=instante, origen=origen)
            hecho = f"🔁 Otra vez en la cola: sale el {T.cuando(instante)}."
        if not motivo:
            self.hub.auditar(uid, "pieza_programada", f"{codigo}:{it.id}:{origen}")
        return motivo, hecho

    def _b_hv(self, cb_id, chat_id, msg_id, uid, args):
        """Una opción del selector de hora. La hora sale de la opción guardada en el servidor (con su dueño, su pieza y su caducidad), nunca del botón."""
        if not args or not args[0].isdigit():
            return self._toast(cb_id, "Esa opción ya no vale.", True)
        ctx = self._ctx(args)
        codigo, ad = self.cuenta()
        fila = self.hub.opcion(int(args[0]))
        if fila is None or fila["user_id"] != uid or fila["cuenta"] != codigo:
            return self._toast(cb_id, "Esa opción ya no vale. Vuelve a abrir «Otra hora».", True)
        if fila["usada"]:
            return self._toast(cb_id, "Esa opción ya se usó: no he cambiado nada.", True)
        if desde_iso(fila["caduca"]) < desde_iso(fila["creada"]) + OPCION_CADUCA_S:
            return self._toast(cb_id, "Ese selector ya no vale: abriste otro después. Usa el último.")
        if fila["caduca"] <= self.hub.iso(self.r.ahora()):
            return self._toast(cb_id, "Esa opción ha caducado (pasaron 15 minutos). Vuelve a abrir «Otra hora».")
        it = self._item(fila["pieza"])
        if it is None or it.estado != fila["estado_pieza"]:
            return self._toast(cb_id, "La pieza ha cambiado de estado: no he hecho nada. Ábrela otra vez.", True)
        instante = datetime.fromisoformat(fila["hora"])
        if instante < self._ahora() + MINIMO:
            return self._toast(cb_id, "Esa hora ya ha pasado o queda menos de dos minutos. Vuelve a abrir «Otra hora».", True)
        if ctx == "r" and not self._en_revision(uid, it.id):
            return self._toast(cb_id, "Ya estaba hecho.")
        if not self.hub.usar_opcion(int(args[0])):
            return self._toast(cb_id, "Esa opción ya se usó: no he cambiado nada.", True)
        motivo, hecho = self._aplicar_hora(uid, it, instante, "panel:hora")
        if motivo:
            self.hub.liberar_opcion(int(args[0]))
            return self._toast(cb_id, motivo[:190], True)
        if ctx == "r" and it.estado == "pendiente":
            self._paso_revision(cb_id, chat_id, uid, it.id, "ok", hecho, "Programada")
            return
        self._ver_ficha(chat_id, msg_id, self._item(it.id), ctx, nota=hecho)
        self._toast(cb_id, "Programada")

    def _b_ht(self, cb_id, chat_id, msg_id, uid, args):
        it = self._hay_pieza(cb_id, args)
        if it is None:
            return
        ctx = self._ctx(args)
        codigo, ad = self.cuenta()
        if it.estado not in ("pendiente", "aprobada", "error"):
            return self._toast(cb_id, f"Esa pieza ya está {T.ESTADO_LLANO.get(it.estado, it.estado).lower()}.", True)
        self.hub.esperar(uid, codigo, it.id, "horapn", ESPERA_HORA_S, mensaje=msg_id, origen=self.r._activa(uid))
        self.hub.meta_escribir(f"panel:htctx:{uid}", ctx)
        texto, botones = T.pedir_hora(it.d(), ctx, ESPERA_HORA_S // 60)
        self._editar_ctx(chat_id, uid, msg_id, ctx, texto, botones)
        self._toast(cb_id)

    def recibir_hora(self, chat_id: int, uid: int, espera, texto: str) -> None:
        """El texto que sigue a «Escribir hora». Si no se entiende o no vale, se dice con ejemplos y se sigue esperando; si vale, se programa."""
        ctx = self.hub.meta_leer(f"panel:htctx:{uid}") or "p"
        it = self._item(espera["pieza"])
        if it is None or it.estado not in ("pendiente", "aprobada", "error"):
            self.hub.dejar_de_esperar(uid)
            return self._decir(chat_id, "Esa pieza ya no admite cambiar la hora. Abre el /panel.")
        try:
            h = analizar_hora(texto, datetime.fromtimestamp(self.r.ahora(), timezone.utc))
        except ErrorHora as exc:
            minutos = max(1, int((espera["caduca"] - self.r.ahora()) // 60))
            return self._decir(chat_id, f"{exc.motivo}. Sigo esperando la hora (te quedan {minutos} min; /cancelar lo deja). "
                                        "Prueba, por ejemplo: «18:30», «mañana a las 9:30» o «en 2 horas».")
        if ctx == "r" and not self._en_revision(uid, it.id):
            ctx = "p"
        motivo, hecho = self._aplicar_hora(uid, it, h.instante, "panel:texto")
        self.hub.dejar_de_esperar(uid)
        if motivo:
            return self._decir(chat_id, motivo)
        mid = espera["mensaje"]
        if ctx == "r" and it.estado == "pendiente":
            ses = self._cargar(uid)
            if ses is not None:
                ses["ok"] += 1
                if ses["i"] < len(ses["ids"]) and ses["ids"][ses["i"]] == it.id:
                    ses["i"] += 1
                return self._mostrar_revision(chat_id, uid, ses, nota=hecho)
        nuevo = self._item(it.id)
        texto_f, botones = T.ficha(nuevo.d(), hecho, ctx if ctx != "r" else "p")
        self._editar_ctx(chat_id, uid, mid, ctx, texto_f, botones)

    # ------------------------------------------------------------------ pantalla: calendario
    def _pantalla_calendario(self, chat_id: int, msg_id: int | None, off: int) -> None:
        off = max(-8, min(8, off))
        ahora = self._ahora()
        desde = ahora.date() + timedelta(days=7 * off)
        hasta = desde + timedelta(days=6)
        por_dia: dict[date, list[dict]] = {}
        sin_dia: list[dict] = []
        for it in self._items():
            if it.estado == "rechazada":
                continue
            c = it.cuando
            if c is None:
                if it.estado in ("pendiente", "error"):
                    sin_dia.append({"id": it.id, "titulo": it.titulo, "estado": it.estado})
                continue
            d = c.date()
            if desde <= d <= hasta:
                por_dia.setdefault(d, []).append({"id": it.id, "titulo": it.titulo, "estado": it.estado, "hora": c})
        dias = []
        for k in range(7):
            d = desde + timedelta(days=k)
            dias.append((d, sorted(por_dia.get(d, []), key=lambda p: p["hora"])))
        texto, botones, _ = T.calendario(desde, dias, sin_dia, off)
        if msg_id:
            self._editar(chat_id, msg_id, texto, botones)
        else:
            self._decir(chat_id, texto, botones)

    def _b_cal(self, cb_id, chat_id, msg_id, uid, args):
        try:
            off = int(args[0]) if args else 0
        except ValueError:
            off = 0
        self._pantalla_calendario(chat_id, msg_id, off)
        self._toast(cb_id)

    # ------------------------------------------------------------------ pantallas: publicadas, más, números, estado, ayuda
    def _b_pu(self, cb_id, chat_id, msg_id, uid, args):
        pub = sorted([i for i in self._items(("publicada",)) if i.publicada], key=lambda i: i.publicada, reverse=True)[:8]
        texto, botones = T.publicadas([{"titulo": i.titulo, "publicada": i.publicada, "permalink": i.permalink} for i in pub])
        self._editar(chat_id, msg_id, texto, botones)
        self._toast(cb_id)

    def _b_mas(self, cb_id, chat_id, msg_id, uid, args):
        codigo, ad = self.cuenta()
        texto, botones = T.mas(ad.pausada())
        self._editar(chat_id, msg_id, texto, botones)
        self._toast(cb_id)

    def _ultimas_metricas(self) -> dict | None:
        try:
            ficheros = sorted(self.metricas.glob("*.json"))
            return json.loads(ficheros[-1].read_text(encoding="utf-8")) if ficheros else None
        except (OSError, ValueError):
            return None

    def _b_num(self, cb_id, chat_id, msg_id, uid, args):
        pub = [i for i in self._items(("publicada",)) if i.publicada]
        texto, botones = T.numeros(self._ultimas_metricas(), len(pub), max((i.publicada for i in pub), default=None))
        self._editar(chat_id, msg_id, texto, botones)
        self._toast(cb_id)

    def _b_te(self, cb_id, chat_id, msg_id, uid, args):
        codigo, ad = self.cuenta()
        v = self.hub.meta_leer(LATIDO)
        try:
            pasada = int(max(0, (self.r.ahora() - float(v)) // 60)) if v else None
        except ValueError:
            pasada = None
        info = ad.ig.get("info") or {}
        instagram = f"conectado como @{info['username']}" if info.get("conectado") and info.get("username") else "sin conexión confirmada"
        parada = None
        try:
            parada = ad._bloqueada(self._ahora())
        except Exception:                                  # noqa: BLE001
            parada = None
        texto, botones = T.estado_tecnico(pasada, ad.ig.get("modo", "desactivado"), instagram, info.get("token_expira"), ad.pausada(), parada)
        self._editar(chat_id, msg_id, texto, botones)
        self._toast(cb_id)

    def _b_pa(self, cb_id, chat_id, msg_id, uid, args):
        codigo, ad = self.cuenta()
        ad.pausar(not ad.pausada())
        self.hub.auditar(uid, "pausa" if ad.pausada() else "reanudado", codigo)
        texto, botones = T.mas(ad.pausada())
        nota = "⏸ Publicaciones en pausa: no sale nada hasta que las reanudes." if ad.pausada() else "▶ Publicaciones reanudadas."
        self._editar(chat_id, msg_id, nota + "\n\n" + texto, botones)
        self._toast(cb_id, "En pausa" if ad.pausada() else "Reanudadas")

    def _b_ay(self, cb_id, chat_id, msg_id, uid, args):
        texto, botones = T.ayuda()
        self._editar(chat_id, msg_id, texto, botones)
        self._toast(cb_id)

    # ------------------------------------------------------------------ avisos que salen solos (cada uno, una sola vez)
    def _una_vez(self, clave: str) -> bool:
        """True la primera vez que se pide esta clave; las siguientes, False."""
        k = f"panel:push:{clave}"
        if self.hub.meta_leer(k):
            return False
        self.hub.meta_escribir(k, str(self.r.ahora()))
        return True

    def avisar_llegada(self, nuevas) -> int:
        """Piezas nuevas por revisar: UN mensaje con el panel (suena el móvil), en vez de un álbum y una tarjeta por pieza. Devuelve a cuántos
        administradores ha llegado."""
        codigo, ad = self.cuenta()
        prev = [p for p in (ad.hora_prevista(x.id) for x in nuevas) if p]
        texto, botones = T.aviso_llegada(len(nuevas), min(prev) if prev else None, max(prev) if prev else None, len(self._pendientes()))
        bien = 0
        for uid in self.r._administradores():
            try:
                self._decir(uid, texto, botones)
                bien += 1
            except ErrorTelegram as exc:
                self._log(f"aviso de llegada no enviado ({exc.metodo} {exc.codigo})")
        return bien

    def vuelta(self) -> None:
        """Lo que sale solo: el resumen de «hoy salen» a partir de las 08:00 y el aviso de cola corta a partir de las 10:00."""
        if not self.disponible():
            return
        ahora = self._ahora()
        hoy = ahora.date().isoformat()
        items = self._items()
        ayer = (ahora.date() - timedelta(days=1)).isoformat()
        if (ahora.hour, ahora.minute) >= HORA_NOCHE and self.hub.meta_leer(f"panel:push:noche:{hoy}") is None:
            self._resumen_noche(ahora, items, hoy)
        if self.hub.meta_leer(f"panel:push:noche:{ayer}") is not None:
            self._una_vez(f"resumen:{hoy}")                  # ya se contó anoche qué sale hoy: no se repite por la mañana
        if ahora.hour >= HORA_RESUMEN and ahora.hour < 20 and self.hub.meta_leer(f"panel:push:resumen:{hoy}") is None:
            salidas = sorted([(i.programada, i.titulo) for i in items if i.estado == "aprobada" and i.programada and i.programada.date() == ahora.date()
                              and i.programada > ahora])
            if salidas:
                texto, botones = T.aviso_resumen_hoy(salidas)
                if self._enviar_admins(texto, botones):
                    self._una_vez(f"resumen:{hoy}")
            elif ahora.hour >= HORA_RESUMEN + 1:
                self._una_vez(f"resumen:{hoy}")                 # nada que contar hoy: no se vuelve a mirar
        if ahora.hour >= HORA_COLA and self.hub.meta_leer(f"panel:push:cola:{hoy}") is None:
            prog = sorted([i.programada for i in items if i.estado in ("aprobada", "publicando") and i.programada])
            pend = [i for i in items if i.estado == "pendiente"]
            hasta = prog[-1] if prog else None
            dias = max(0, (hasta.date() - ahora.date()).days + 1) if hasta else 0
            if dias < DIAS_COLA_CORTA and not pend:
                if self._enviar_admins(T.aviso_cola_corta(dias, hasta)):
                    self._una_vez(f"cola:{hoy}")
            else:
                self._una_vez(f"cola:{hoy}")

    # ------------------------------------------------------------------ el resumen de la noche (20-oct-2026)
    def pieza_sensible(self, id: int) -> bool:
        """True si la pieza pide que la vea José antes de salir (`produccion.revision_jose`): es lo único que se le manda pieza a pieza."""
        try:
            codigo, ad = self.cuenta()
            r = ad.fila(id)
            if r is None:
                return False
            return bool((ad._json_de(r).get("produccion") or {}).get("revision_jose"))
        except Exception:                                      # noqa: BLE001 (ante la duda, no se esconde: se manda como siempre)
            return True

    def _portada_de(self, id: int) -> Path | None:
        """Una imagen que represente la pieza: su primera hoja o, si es un Reel, un fotograma del vídeo (cerca del segundo 0,6)."""
        codigo, ad = self.cuenta()
        p = ad.pieza(id)
        if p is None:
            return None
        for f in p.archivos:
            f = Path(f)
            if f.suffix.lower() in (".jpg", ".jpeg", ".png") and f.is_file():
                return f
        for f in p.archivos:
            f = Path(f)
            if f.suffix.lower() == ".mp4" and f.is_file():
                try:
                    import subprocess
                    import tempfile
                    import imageio_ffmpeg
                    out = Path(tempfile.gettempdir()) / f"vivax_noche_{f.parent.name}.jpg"
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-ss", "0.6", "-i", str(f), "-frames:v", "1", "-vf", "scale=540:-2", str(out)],
                                   capture_output=True, timeout=60)
                    if out.is_file():
                        return out
                except Exception as exc:                       # noqa: BLE001 (sin fotograma, el resumen sale con un recuadro)
                    self._log(f"no pude sacar un fotograma de {f.name}: {type(exc).__name__}")
        return None

    def _montaje(self, salidas: list[dict], destino: Path) -> Path | None:
        """Una sola imagen con las portadas de lo que sale mañana (hasta 4), cada una con su hora. None si no se puede dibujar."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None
        n = len(salidas[:4])
        if n == 0:
            return None
        w, h, hueco = 360, 450, 18
        lienzo = Image.new("RGB", (hueco + n * (w + hueco), h + 2 * hueco), (12, 12, 12))
        d = ImageDraw.Draw(lienzo)
        try:
            letra = ImageFont.load_default(size=34)
        except TypeError:
            letra = ImageFont.load_default()
        for k, x in enumerate(salidas[:4]):
            x0 = hueco + k * (w + hueco)
            ruta = self._portada_de(x["id"])
            if ruta is not None:
                try:
                    im = Image.open(ruta).convert("RGB")
                    escala = max(w / im.width, h / im.height)
                    im = im.resize((int(im.width * escala) + 1, int(im.height * escala) + 1))
                    izq, arr = (im.width - w) // 2, (im.height - h) // 2
                    lienzo.paste(im.crop((izq, arr, izq + w, arr + h)), (x0, hueco))
                except Exception:                              # noqa: BLE001
                    d.rectangle((x0, hueco, x0 + w, hueco + h), fill=(40, 40, 40))
            else:
                d.rectangle((x0, hueco, x0 + w, hueco + h), fill=(40, 40, 40))
            etiqueta = f"{T.hora(x['hora'])}" + ("  REEL" if x.get("reel") else "")
            d.rectangle((x0, hueco, x0 + 12 * len(etiqueta) + 36, hueco + 52), fill=(176, 189, 107))
            d.text((x0 + 14, hueco + 8), etiqueta, fill=(0, 0, 0), font=letra)
        lienzo.save(destino, "JPEG", quality=88)
        return destino

    def _resumen_noche(self, ahora: datetime, items: list, hoy: str) -> None:
        """A partir de las 21:30, UNA vez al día: qué sale mañana, con una imagen y botones. Sin nada previsto no manda nada."""
        manana = ahora.date() + timedelta(days=1)
        sale = sorted([i for i in items if i.estado == "aprobada" and i.programada and i.programada.date() == manana], key=lambda i: i.programada)
        if not sale:
            self._una_vez(f"noche:{hoy}")
            return
        salidas = [{"id": i.id, "hora": i.programada, "titulo": i.titulo, "reel": i.formato == "video"} for i in sale]
        texto, botones = T.resumen_noche(manana, salidas)
        import tempfile
        imagen = self._montaje(salidas, Path(tempfile.gettempdir()) / f"vivax_noche_{manana.isoformat()}.jpg")
        bien = False
        for uid in self.r._administradores():
            try:
                if imagen is not None:
                    self.r.tg.enviar_foto(uid, imagen, texto, botones)
                else:
                    self._decir(uid, texto, botones)
                bien = True
            except ErrorTelegram as exc:
                self._log(f"resumen de la noche no enviado ({exc.metodo} {exc.codigo})")
        if bien:
            self._una_vez(f"noche:{hoy}")
            self.hub.meta_escribir(f"panel:noche:{manana.isoformat()}", texto)

    def _b_nd(self, cb_id, chat_id, msg_id, uid, args):
        """«👍 Todo bien» del resumen de la noche: se queda el resumen sin botones y se dice que está visto."""
        fecha = args[1] if len(args) > 1 else ""
        texto = self.hub.meta_leer(f"panel:noche:{fecha}") or "🌙 Resumen de mañana"
        self._editar(chat_id, msg_id, T.resumen_noche_hecho(texto), [[("📅 Calendario", "pn:cal:0")]], foto=True)
        self._toast(cb_id, "Perfecto, sale así")

    def _enviar_admins(self, texto: str, botones=None) -> bool:
        """Lo manda a los administradores; True si llegó a alguno. Un fallo de Telegram no tumba nada: se reintenta en la siguiente vuelta."""
        bien = False
        for uid in self.r._administradores():
            try:
                self._decir(uid, texto, botones)
                bien = True
            except ErrorTelegram as exc:
                self._log(f"aviso no enviado ({exc.metodo} {exc.codigo})")
        return bien
