"""Botones de la serie de formatos (3-nov-2026): «✅ Mandar», «✏️ Modificar con IA» y «❌ Rechazar» justo debajo de cada pieza.

José pidió los botones pegados a cada publicación («porque así es un lío»): ya no hay una pregunta aparte que llega minutos después.
Los botones los pone el PC al enviar la pieza (`catalogo_formatos.py`); aquí solo se atienden las pulsaciones. Los datos de cada botón
son `fx:<ok|ia|no>:<CÓDIGO>` (el código del formato, dos o tres letras: AA, BC…).

Lo que se guarda (meta del hub, un valor por formato): `f100:<CÓDIGO>` = {"estado": "mandar"|"modificar"|"rechazar", "ts": …, "notas": […]}.
El asistente lo lee con `enviar_f100.py --votos`. Este módulo NO publica nada ni toca la cola: «Mandar» solo deja constancia; el asistente
prepara la pieza publicable y sigue el camino de siempre (controles y aprobación). «Modificar con IA» pide el cambio por escrito y lo guarda
como nota: la nube no puede renderizar, así que lo rehace el asistente en el PC y manda la versión nueva con sus botones.

Solo el administrador pulsa. Una segunda pulsación del mismo botón no cambia nada; pulsar otro botón cambia de opinión.
"""
from __future__ import annotations

import json
import re

from .hub import parece_credencial
from .telegram_io import ErrorTelegram

DATO = re.compile(r"fx:(ok|ia|no):([A-Z]{2,3})")        # se usa con fullmatch
CLAVE = "f100:"                                          # meta: f100:<CÓDIGO>
ESPERA = "f100_espera:"                                  # meta: f100_espera:<user_id> = {"cod", "ts", "pedido"}
ESPERA_S = 30 * 60
MAX_NOTAS = 20
MAX_NOTA = 1500
ESTADO_DE = {"ok": "mandar", "ia": "modificar", "no": "rechazar"}
ACCION_DE = {v: k for k, v in ESTADO_DE.items()}


def botones_de(cod: str, estado: str | None = None) -> list[list[tuple[str, str]]]:
    """El teclado de un formato; con `estado`, la opción elegida lleva ✓ (se puede cambiar de opinión pulsando otra)."""
    def t(texto: str, accion: str) -> tuple[str, str]:
        marca = " ✓" if estado and ESTADO_DE[accion] == estado else ""
        return (texto + marca, f"fx:{accion}:{cod}")
    return [[t("✅ Mandar", "ok"), t("❌ Rechazar", "no")],
            [t("✏️ Modificar con IA", "ia")]]


class FormatosSerie:
    def __init__(self, router):
        self.r = router

    @property
    def hub(self):
        return self.r.hub

    # ------------------------------------------------------------------ guardado
    def leer(self, cod: str) -> dict | None:
        valor = self.hub.meta_leer(CLAVE + cod)
        if not valor:
            return None
        try:
            d = json.loads(valor)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def _guardar(self, cod: str, d: dict) -> None:
        self.hub.meta_escribir(CLAVE + cod, json.dumps(d, ensure_ascii=False, sort_keys=True))

    def _espera(self, uid: int) -> dict | None:
        valor = self.hub.meta_leer(ESPERA + str(uid))
        if not valor:
            return None
        try:
            e = json.loads(valor)
            vivo = self.r.ahora() - float(e["ts"]) <= ESPERA_S
        except (ValueError, KeyError, TypeError):
            return None
        return e if vivo else None

    def _soltar_espera(self, uid: int) -> None:
        self.hub.db.execute("DELETE FROM meta WHERE k=?", (ESPERA + str(uid),))
        self.hub.db.commit()

    def cancelar(self, uid: int) -> bool:
        """«/cancelar»: si había un cambio pedido a medias, se deja como está. True si había algo que cancelar."""
        hay = self._espera(uid) is not None
        self._soltar_espera(uid)
        return hay

    # ------------------------------------------------------------------ botones
    def boton(self, cb_id: str, chat_id: int, mensaje: dict, uid: int, data: str) -> None:
        r, hub = self.r, self.hub
        if not hub.es_admin(uid):
            hub.auditar(uid, "boton_denegado", data[:64])
            return r._responder(cb_id, "No tienes acceso a eso.", True)
        m = DATO.fullmatch(data)
        if m is None:
            return r._responder(cb_id, "Ese botón ya no vale.", True)
        accion, cod = m.groups()
        estado = ESTADO_DE[accion]
        mensaje_id = mensaje.get("message_id")
        actual = self.leer(cod) or {}
        pidiendo = accion == "ia" and (self._espera(uid) or {}).get("cod") == cod
        if actual.get("estado") == estado and (accion != "ia" or pidiendo):
            self._marcar(chat_id, mensaje_id, cod, estado)         # por si la pulsación anterior no llegó a cambiar el teclado
            return r._responder(cb_id, "Ya estaba así: " + self._dicho(estado) + ".")
        nuevo = {"estado": estado, "ts": hub.iso(r.ahora()), "notas": actual.get("notas", [])}
        self._guardar(cod, nuevo)
        hub.auditar(uid, f"formato_{estado}", cod)
        if estado != "modificar":
            self._soltar_espera(uid)                               # cambiar de opinión anula un cambio pedido a medias
        self._marcar(chat_id, mensaje_id, cod, estado)
        if estado == "mandar":
            return r._responder(cb_id, "Apuntado para publicar. La preparo y te digo la hora.")
        if estado == "rechazar":
            return r._responder(cb_id, "Rechazada. Queda anotado.")
        r._responder(cb_id, "Escríbeme qué cambio quieres.")
        pedido = r._decir(chat_id, f"✏️ Formato {cod}: dime qué cambio quieres (el guion, el texto, el gancho, los dibujos…). "
                                   "Escríbelo ahora en un mensaje y lo rehago con IA; te lo mando otra vez con sus botones. "
                                   "Tarda unos minutos. /cancelar lo deja como está.")
        hub.meta_escribir(ESPERA + str(uid), json.dumps({"cod": cod, "ts": r.ahora(), "pedido": pedido}))

    @staticmethod
    def _dicho(estado: str) -> str:
        return {"mandar": "apuntada para publicar", "rechazar": "rechazada", "modificar": "pidiendo el cambio"}[estado]

    def _marcar(self, chat_id: int, mensaje_id: int | None, cod: str, estado: str) -> None:
        """Pone el ✓ en el botón elegido. Un fallo de Telegram aquí no deshace lo ya guardado."""
        if mensaje_id is None:
            return
        try:
            self.r.tg.editar_botones(chat_id, mensaje_id, botones_de(cod, estado))
        except ErrorTelegram as exc:
            self.r.log(f"formato {cod}: no pude marcar el botón ({exc.metodo} {exc.codigo})")

    # ------------------------------------------------------------------ el cambio escrito
    def recibir_texto(self, chat_id: int, uid: int, m: dict, texto: str) -> bool:
        """El texto que sigue a «✏️ Modificar con IA» es el cambio pedido. True si lo ha consumido. No consume nada si hay otra conversación
        en curso (un pie, una hora, un reparo), si es una orden o si es la respuesta a otro mensaje que no es la petición."""
        hub = self.hub
        e = self._espera(uid)
        if e is None or not texto or texto.startswith("/") or any(k in m for k in ("photo", "document", "video", "voice", "audio")):
            return False
        cita = (m.get("reply_to_message") or {}).get("message_id")
        if cita is not None and cita != e.get("pedido"):
            return False
        if hub.esperando(uid) is not None or hub.mensaje_usado(m.get("message_id")):
            return False
        cod = e["cod"]
        if parece_credencial(texto):
            self.r.decisiones._no_guardar(chat_id, uid)
            return True
        d = self.leer(cod) or {"estado": "modificar", "notas": []}
        mid = m.get("message_id")
        if not any(n.get("mid") == mid for n in d.get("notas", []) if mid is not None):
            d["notas"] = (d.get("notas") or [])[-(MAX_NOTAS - 1):] + [{"t": hub.iso(self.r.ahora()), "texto": texto[:MAX_NOTA], "mid": mid}]
        d["estado"] = "modificar"
        d["ts"] = hub.iso(self.r.ahora())
        self._guardar(cod, d)
        self._soltar_espera(uid)
        self.r._decir(chat_id, f"Anotado para {cod}. Lo rehago con IA y te lo mando otra vez con sus botones. "
                               "Lo hago desde el PC: tarda unos minutos y, si no estoy conectado, en cuanto lo esté.")
        return True
