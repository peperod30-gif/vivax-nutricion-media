"""Núcleo del router: recibe cada actualización de Telegram y decide quién puede hacer qué.

Dos tipos de cuenta cuelgan del router:
- Las que usan el motor de Nutrición (la propia y las de cada cliente): el router lleva sus piezas, sus botones y sus permisos.
- Las que se ALOJAN (Campo): el router no sabe de sus piezas. Solo comprueba que quien escribe es el administrador y le pasa la
  actualización entera a la lógica de esa cuenta, que contesta como siempre.

Regla que no se salta nunca: el dato de un botón no se cree. En cada pulsación de un botón del router se comprueba, por este orden:
  1. el chat es privado, es el del propio usuario y el usuario está en el hub (y no revocado);
  2. la cuenta del botón existe y el usuario pertenece a ella (el administrador pertenece a todas);
  3. su rol permite esa acción;
  4. la pieza existe en ESA cuenta (los números de pieza se repiten entre cuentas);
  5. el estado de la pieza admite la acción (lo comprueba el adaptador: no se aprueba lo ya decidido, ni lo no validado).
Un usuario sin acceso recibe siempre la misma respuesta, para no revelar qué cuentas o piezas existen.

Los botones del router tienen la forma `n1:ok:17` (cuenta, acción, número). Todo lo demás, que solo puede ser de una cuenta alojada,
va al administrador y a nadie más.

Las decisiones (preguntas del asistente a José con botones) usan `cuenta:d<clave>:<número>` y solo las pulsa el administrador: la
clase `Decisiones` las entrega, las cierra y guarda lo que José escribe libremente (la bandeja). Ver `ROUTER_TELEGRAM.md`.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .hub import (EJEMPLOS_HORA, ESPERA_HORA_S, LATIDO, MADRID, OPCION_CADUCA_S, PREGUNTA_AHORA_S, ErrorHora, analizar_hora, desde_iso,
                  dia_corto_es, fecha_corta_es, fecha_hora_es, hora_local, limpiar_respuesta, limpiar_texto, parece_credencial,
                  repeticion_hora)
from .panel import Panel
from .telegram_io import ErrorTelegram
from .validacion import ACCIONES_VALIDACION, Validacion

FORMATO_BOTON = re.compile(r"[a-z][0-9]{1,3}:[a-z]{2,8}:[0-9]{1,9}")      # se usa con fullmatch: sin fin de línea ni cifras de otros alfabetos
ACCIONES_APROBADOR = {"ok", "no", "pie"}
ACCIONES_MIEMBRO = {"cta"}                  # cambiar de sección: cualquier miembro, solo entre sus cuentas
# Elegir la hora de salida (ver class Programacion): Programar, Cambiar hora, Ahora, Sí, una opción del selector, escribir una hora,
# quitar de la cola y volver. Nunca empiezan por «d» (eso son las decisiones) ni coinciden con las de validación.
ACCIONES_PROGRAMAR = {"pr", "ch", "ya", "yas", "pg", "pt", "qu", "vol"}
ESTADO_ES = {"pendiente": "pendiente", "aprobada": "aprobada", "publicando": "publicándose", "publicada": "publicada",
             "rechazada": "rechazada", "error": "con error"}
PROGRAMAR_EJEMPLOS = "Prueba, por ejemplo:\n· " + "\n· ".join(EJEMPLOS_HORA)
ORDENES_DE_CUENTA = {"/estado", "/cola", "/pausa", "/seguir"}      # van a la cuenta activa
PRIVADO = "Este bot es privado. Si te han dado un enlace de invitación, ábrelo desde Telegram."
SIN_ACCESO = "No tienes acceso a eso."


def _datos_boton(cuenta: str, accion: str, id: int) -> str:
    d = f"{cuenta}:{accion}:{id}"
    assert len(d.encode("utf-8")) <= 64, "el dato de un botón no puede pasar de 64 bytes"
    return d


def _botones_de(texto_json: str | None):
    """Los botones de un aviso guardado (JSON de filas de pares «texto», «dato del botón»), o None si no lleva ninguno."""
    if not texto_json:
        return None
    return [[(str(t), str(d)) for t, d in fila] for fila in json.loads(texto_json)]


def _misma_hora(a: datetime | None, b: datetime | None) -> bool:
    """¿Son el mismo instante? Se compara en UTC: dentro de Madrid, dos horas con el mismo texto (cambio de hora) no son la misma."""
    return a is not None and b is not None and a.astimezone(timezone.utc) == b.astimezone(timezone.utc)


PATRON_DECISION = re.compile(r"[a-z][0-9]{1,3}:d[a-z]{1,7}:[0-9]{1,9}")     # forma de un botón de decisión (para auditar lo denegado)
AVISO_BANDEJA_S = 600                   # «Anotado» como mucho cada 10 minutos, para no ensuciar el chat


class Decisiones:
    """Preguntas que el asistente hace a José (decisiones) y la bandeja de lo que él escribe libremente. Solo el administrador.

    Cada vuelta del bucle: entrega las pendientes que aún no han llegado, cierra las caducadas (con su opción por defecto si la hay)
    y quita los botones de las ya cerradas. Una decisión no se vuelve a mandar aunque el router se reinicie: lo entregado tiene número
    de mensaje. Si José borra el mensaje, el router no lo nota ni lo reenvía: la decisión sigue pendiente hasta que caduque (y se ve
    con /decisiones).
    """

    def __init__(self, router):
        self.r = router

    @property
    def hub(self):
        return self.r.hub

    def _ahora(self) -> float:
        return self.r.ahora()

    # ------------------------------------------------------------------ textos
    @staticmethod
    def _texto_de(d, clave: str | None) -> str:
        return next((o["texto"] for o in json.loads(d["opciones"]) if o["clave"] == clave), "")

    def _botones(self, d):
        filas = [[(o["texto"], _datos_boton(d["cuenta"], "d" + o["clave"], d["id"]))] for o in json.loads(d["opciones"])]
        return filas or None

    def _cab(self, d) -> str:
        """La primera línea del mensaje: «Decisión n.º 7» como siempre, o «Pregunta n.º 7» con el panel (sin jerga). Se reconocen las dos."""
        return f"{'Pregunta' if self.r.panel.activo else 'Decisión'} n.º {d['id']}"

    def _texto_pregunta(self, d) -> str:
        if d["texto_libre"]:
            pide = ("Pulsa una opción o responde a este mensaje con tu texto." if json.loads(d["opciones"])
                    else "Responde a este mensaje con tu texto.")
        else:
            pide = "Pulsa una opción."
        cola = f"Caduca el {hora_local(d['caduca']):%d/%m a las %H:%M}."
        if d["defecto"]:
            cola += f" Si no contestas, se aplica «{self._texto_de(d, d['defecto'])}»."
        return f"{self._cab(d)}\n\n{d['pregunta']}\n\n{pide}\n{cola}"

    def _texto_cerrada(self, d) -> str:
        if d["estado"] == "respondida":
            if d["respuesta_via"] == "boton":
                linea = f"Elegiste: {d['respuesta_texto']}."
            elif d["respuesta_via"] == "texto":
                linea = f"Respondiste: «{d['respuesta_texto']}»."
            else:
                linea = f"Caducó sin respuesta. Se aplicó «{d['respuesta_texto']}» por defecto."
        elif d["estado"] == "caducada":
            linea = "Caducó sin respuesta. No había ninguna opción por defecto."
        else:
            linea = "Anulada: ya no hace falta contestarla."
        return f"{self._cab(d)}\n\n{d['pregunta']}\n\n{linea}"

    # ------------------------------------------------------------------ cada vuelta del bucle
    def vuelta(self) -> None:
        self._entregar()
        self.hub.cerrar_vencidas(self._ahora())
        for d in self.hub.sin_sincronizar():
            self._cerrar(d, avisar=d["respuesta_via"] == "defecto")

    def latido(self) -> None:
        """Señal de vida: el router sigue dando vueltas con esta versión. Lo miran preguntar y respuesta para avisar con precisión.
        Si la base no deja escribirla un momento, no se tumba el router: la siguiente vuelta lo vuelve a intentar."""
        try:
            self.hub.meta_escribir(LATIDO, str(self._ahora()))
        except sqlite3.Error as exc:
            self.r.log(f"latido no escrito ({type(exc).__name__})")

    def _entregar(self) -> None:
        admin = self.hub.admin_id()
        if admin is None:
            return
        for d in self.hub.sin_entregar():
            try:
                mid = self.r.tg.enviar(admin, self._texto_pregunta(d), self._botones(d))
            except ErrorTelegram as exc:
                if exc.codigo is None:                  # fallo de red: puede que Telegram ya lo haya guardado (se verá en /decisiones)
                    self.hub.marcar_dudosa(d["id"])
                self.r.log(f"decisión {d['id']}: no se pudo entregar ({exc.metodo} {exc.codigo}); lo vuelvo a intentar")
                return
            self.hub.marcar_entregada(d["id"], mid)
            self.hub.auditar_decision(None, d["id"], "entregada", str(mid))

    def _editar_cierre(self, d) -> bool:
        """Pone cada copia del mensaje sin botones y con cómo acabó. False si Telegram no lo admite en alguna (p. ej. el mensaje ya no existe)."""
        admin = self.hub.admin_id()
        copias = self.hub.copias(d["id"])
        if admin is None or not copias:
            return True
        ok = True
        for mid in copias:
            try:
                self.r.tg.editar(admin, mid, self._texto_cerrada(d))
            except ErrorTelegram as exc:
                self.r.log(f"decisión {d['id']}: no se pudo cerrar el mensaje {mid} ({exc.metodo} {exc.codigo})")
                ok = False
        return ok

    def _cerrar(self, d, avisar: bool = False) -> None:
        """Quita los botones y pone cómo acabó. Con `avisar`, manda además un mensaje nuevo (el móvil solo suena con mensajes nuevos).
        Si la decisión nunca llegó a Telegram (la cerró la CLI con el router parado), se cuenta ahora en un mensaje nuevo, con la pregunta.
        Si Telegram falla no se insiste en cada vuelta (un mensaje borrado no se arregla): la pulsación siguiente sí lo reintenta."""
        if d["cerrada_tg"]:
            return
        admin = self.hub.admin_id()
        if not self.hub.copias(d["id"]):
            if admin is not None and d["estado"] != "anulada":
                try:
                    self.r.tg.enviar(admin, self._texto_cerrada(d))
                except ErrorTelegram as exc:
                    self.r.log(f"decisión {d['id']}: no se pudo contar su cierre ({exc.metodo} {exc.codigo})")
        else:
            self._editar_cierre(d)
            if avisar and admin is not None:
                try:
                    self.r.tg.enviar(admin, f"Decisión n.º {d['id']}: caducó sin respuesta y se ha aplicado «{d['respuesta_texto']}» "
                                            "por defecto. No tienes que hacer nada.")
                except ErrorTelegram as exc:
                    self.r.log(f"decisión {d['id']}: no se pudo avisar del defecto ({exc.metodo} {exc.codigo})")
        self.hub.marcar_cerrada_tg(d["id"])

    # ------------------------------------------------------------------ botones (solo el administrador)
    def boton(self, cb_id: str, mensaje_id: int | None, texto_mensaje: str | None, uid: int, cuenta: str, accion: str, num: int) -> None:
        """Una pulsación de decisión. Lo que dice el botón no se cree: que la decisión exista, que sea de esa cuenta, que el mensaje sea
        una copia suya, que siga pendiente y sin caducar, y que la opción exista. La respuesta se guarda una sola vez."""
        hub, ahora = self.hub, self._ahora()
        clave = accion[1:]
        if not hub.es_admin(uid):
            hub.auditar_decision(uid, num, "boton_denegado", f"no admin: {cuenta}:{clave}")
            return self.r._responder(cb_id, SIN_ACCESO, True)
        d = hub.decision(num)
        if d is None:
            hub.auditar_decision(uid, num, "boton_sin_decision", clave)
            return self.r._responder(cb_id, "Esa decisión no existe.", True)
        if d["cuenta"] != cuenta or not self._copia_de(d, mensaje_id, texto_mensaje):
            hub.auditar_decision(uid, num, "boton_no_coincide", f"{cuenta}:{clave}")
            return self.r._responder(cb_id, "Ese botón no es de esa decisión.", True)
        if d["estado"] != "pendiente":                      # doble pulsación: no se cambia nada, solo se dice
            hub.auditar_decision(uid, num, "boton_tardio", d["estado"])
            if self._editar_cierre(d):                      # por si el mensaje aún tenía los botones: se reintenta aquí
                hub.marcar_cerrada_tg(num)
            if d["estado"] == "respondida":
                return self.r._responder(cb_id, self._ya_cerrada(d))
            return self.r._responder(cb_id, "Esa decisión ya está cerrada.", True)
        if d["caduca"] <= hub.iso(ahora):
            hub.auditar_decision(uid, num, "boton_tardio", "caducada")
            hub.cerrar_vencidas(ahora)
            d = hub.decision(num)
            self._cerrar(d, avisar=d["respuesta_via"] == "defecto")
            if d["estado"] == "respondida":
                return self.r._responder(cb_id, f"Ya caducó: no he cambiado nada. Se aplicó «{d['respuesta_texto']}».", True)
            return self.r._responder(cb_id, "Ya caducó: no he cambiado nada.", True)
        opcion = next((o for o in json.loads(d["opciones"]) if o["clave"] == clave), None)
        if opcion is None:
            hub.auditar_decision(uid, num, "opcion_inexistente", clave)
            return self.r._responder(cb_id, "Esa opción ya no existe.", True)
        if not hub.responder_decision(num, clave, opcion["texto"], "boton", ahora):    # atómico: si otro proceso fue primero, no pisa
            d = hub.decision(num)
            if d["estado"] == "respondida":
                return self.r._responder(cb_id, self._ya_cerrada(d))
            return self.r._responder(cb_id, "Esa decisión ya está cerrada.", True)
        hub.auditar_decision(uid, num, "respondida", f"boton:{clave}")
        self._cerrar(hub.decision(num))
        self.r._responder(cb_id, f"Elegiste: {opcion['texto']}")

    @staticmethod
    def _ya_cerrada(d) -> str:
        """Lo que se dice a quien pulsa una decisión que ya tiene respuesta: quién la dio y que no se cambia."""
        if d["respuesta_via"] == "defecto":
            return f"Caducó y se aplicó «{d['respuesta_texto']}» por defecto. No la cambio."
        if d["respuesta_via"] == "texto":
            return f"Ya estaba registrada: «{d['respuesta_texto']}». No la cambio."
        return f"Ya estaba registrada: {d['respuesta_texto']}. No la cambio."

    def _copia_de(self, d, mensaje_id: int | None, texto: str | None) -> bool:
        """¿Es este mensaje una copia de la decisión «d»? Si el mensaje ya está registrado, manda el registro. Si no (Telegram lo envió pero
        el router no llegó a guardar su número, por un fallo de red), se reconoce por su texto, que empieza por «Decisión n.º N» y tiene
        la pregunta; entonces se registra ahora, para que sus botones y sus respuestas cuenten y se cierre también."""
        if mensaje_id is None:
            return False
        otra = self.hub.decision_por_mensaje(mensaje_id)
        if otra is not None:
            return otra["id"] == d["id"]
        if str(texto or "").startswith((f"Decisión n.º {d['id']}\n", f"Pregunta n.º {d['id']}\n")) and d["pregunta"] in str(texto):
            self.hub.registrar_copia(d["id"], mensaje_id)
            return True
        return False

    def _decision_citada(self, m: dict):
        """La decisión a la que responde el mensaje: por el número del mensaje citado. Si ese mensaje no quedó registrado, por el
        «Decisión n.º N» de su texto, solo si lo escribió el propio bot (`from.is_bot`)."""
        cita = m.get("reply_to_message") or {}
        mensaje_id = cita.get("message_id")
        d = self.hub.decision_por_mensaje(mensaje_id)
        if d is not None:
            return d
        texto = str(cita.get("text") or "")
        # Cabe también el aviso de cierre («Decisión n.º 5: caducó…»); como mucho 9 cifras, como en los botones
        n = re.match(r"(?:Decisión|Pregunta) n\.º ([0-9]{1,9})[\n:]", texto)
        if n is None or not (cita.get("from") or {}).get("is_bot"):
            return None
        otra = self.hub.decision(int(n.group(1)))
        if otra is None:
            return None
        if otra["estado"] != "pendiente":
            return otra                 # ya cerrada: se dice cuál es y no hay nada que contestar; no se registra nada aquí
        return otra if self._copia_de(otra, mensaje_id, texto) else None

    def denegado(self, uid, data) -> None:
        """Un botón de decisión que ni siquiera llega a `boton` (chat ajeno, usuario sin acceso) también queda en el registro."""
        if isinstance(data, str) and PATRON_DECISION.fullmatch(data):
            cuenta, accion, num = data.split(":")
            self.hub.auditar_decision(uid, int(num), "boton_denegado", f"{cuenta}:{accion}")

    # ------------------------------------------------------------------ lo que José escribe
    def respuesta_libre(self, chat_id: int, uid: int, m: dict, texto: str, adjunto: bool) -> bool:
        """El texto de José responde a una decisión con texto libre: si lo escribe respondiendo al mensaje de la decisión, o si es suelto,
        no está en la sección de Campo y solo hay una decisión abierta con texto libre (si hay varias, no se atribuye a ninguna). True si
        lo ha tomado; si no, Campo o la bandeja siguen su camino."""
        if adjunto or not texto:
            return False
        hub, ahora, mensaje_id = self.hub, self._ahora(), m.get("message_id")
        if hub.mensaje_usado(mensaje_id):                       # el router lo recibe otra vez tras una caída: ya estaba contado
            return True
        if (m.get("reply_to_message") or {}).get("message_id") is not None:
            d = self._decision_citada(m)
            if d is None:
                if str((m.get("reply_to_message") or {}).get("text") or "").startswith(("Decisión n.º ", "Pregunta n.º ")):
                    return self._sin_enlazar(chat_id, uid, m, texto)     # responde a una decisión que no se puede enlazar: se dice
                return False                                    # respuesta a otra cosa: la de Campo, o la bandeja
        else:
            if self.r._activa_es_anfitriona(uid):
                self._aviso_campo(chat_id)                      # en Campo, lo que escribes es de Campo: sigue su camino
                return False
            abiertas = hub.abiertas_con_texto()
            if not abiertas:
                return False
            if len(abiertas) > 1:
                return self._varias_abiertas(chat_id, uid, m, texto, abiertas)
            d = abiertas[0]
        if parece_credencial(texto):
            return self._no_guardar(chat_id, uid)
        if d["estado"] == "pendiente" and d["texto_libre"] and d["caduca"] > hub.iso(ahora):
            return self._guardar_respuesta(chat_id, uid, d, texto, mensaje_id)
        hub.anotar_bandeja(texto, mensaje_id, d["id"])
        if d["estado"] == "pendiente" and d["texto_libre"]:                   # venció mientras tanto
            hub.cerrar_vencidas(ahora)
            d = hub.decision(d["id"])
            self._cerrar(d, avisar=d["respuesta_via"] == "defecto")
            motivo = f"La decisión n.º {d['id']} ya caducó: no la cambio. Lo he anotado para el asistente."
        elif d["estado"] == "respondida":
            motivo = f"La decisión n.º {d['id']} ya tiene respuesta («{d['respuesta_texto']}»). No la cambio; lo he anotado para el asistente."
        elif not d["texto_libre"]:
            motivo = f"La decisión n.º {d['id']} solo admite botones: pulsa una opción. Lo he anotado para el asistente."
        else:
            motivo = f"La decisión n.º {d['id']} ya está cerrada: no la cambio. Lo he anotado para el asistente."
        self.r._decir(chat_id, motivo)
        return True

    def _guardar_respuesta(self, chat_id: int, uid: int, d, texto: str, mensaje_id: int | None) -> bool:
        try:
            limpio = limpiar_respuesta(texto)
        except ValueError as exc:
            self.r._decir(chat_id, f"No la he guardado: {exc}. Escríbela más corta, por favor.")
            return True
        if not self.hub.responder_decision(d["id"], "texto", limpio, "texto", self._ahora(), respuesta_mensaje=mensaje_id):
            self.r._decir(chat_id, f"La decisión n.º {d['id']} ya no está pendiente: no la cambio.")
            return True
        self.hub.auditar_decision(uid, d["id"], "respondida", "texto")
        # La pregunta va en la confirmación: si se le atribuyó a la equivocada, José lo ve enseguida
        self.r._decir(chat_id, f"Guardado: esa es la respuesta a la decisión n.º {d['id']} («{limpiar_texto(d['pregunta'], 80)}»).")
        self._cerrar(self.hub.decision(d["id"]))
        return True

    def anotar(self, chat_id: int, uid: int, texto: str, mensaje_id: int | None) -> None:
        """Un mensaje libre de José que no es una orden ni una respuesta: a la bandeja. El «Anotado» va como mucho cada 10 minutos."""
        if self.hub.mensaje_usado(mensaje_id):
            return
        if parece_credencial(texto):
            self._no_guardar(chat_id, uid)
            return
        self.hub.anotar_bandeja(texto, mensaje_id)
        ahora = self._ahora()
        ultimo = self._leer_instante("bandeja_aviso")
        if ultimo is not None and ahora - ultimo < AVISO_BANDEJA_S:
            return
        self.hub.meta_escribir("bandeja_aviso", str(ahora))
        self.r._decir(chat_id, "Anotado. Lo verá el asistente.")

    def _varias_abiertas(self, chat_id: int, uid: int, m: dict, texto: str, abiertas) -> bool:
        """Hay más de una decisión abierta: el texto suelto no se atribuye a ninguna. Va a la bandeja sin número, y se dice cuáles hay."""
        if parece_credencial(texto):
            return self._no_guardar(chat_id, uid)
        self.hub.anotar_bandeja(texto, m.get("message_id"))
        lista = "\n".join(f"· n.º {d['id']} · {limpiar_texto(d['pregunta'], 80)}" for d in abiertas)
        self.r._decir(chat_id, "Tienes varias decisiones abiertas y no sé a cuál contestas:\n" + lista +
                      "\n\nResponde al mensaje de la que sea, o pulsa una opción. Lo he anotado para el asistente, sin asignarlo.")
        return True

    def _sin_enlazar(self, chat_id: int, uid: int, m: dict, texto: str) -> bool:
        """Responde a un mensaje de decisión que no se puede enlazar con ninguna (p. ej. una decisión que ya no existe). Se dice; no se
        contesta «Anotado» como si fuera un mensaje cualquiera."""
        if parece_credencial(texto):
            return self._no_guardar(chat_id, uid)
        self.hub.anotar_bandeja(texto, m.get("message_id"))
        self.r._decir(chat_id, "No la he podido enlazar con ninguna decisión: no sé a cuál contesta. Lo he anotado para el asistente, "
                               "sin asignarlo.")
        return True

    def _no_guardar(self, chat_id: int, uid: int) -> bool:
        """Lo que parece una credencial no se guarda ni se repite. El registro dice que pasó, pero sin el texto."""
        self.hub.auditar(uid, "credencial_no_guardada")
        self.r._decir(chat_id, "Parece una credencial (un token o una clave): no la he guardado. Bórrala del chat, por favor, "
                               "y dime qué necesitas.")
        return True

    def _aviso_campo(self, chat_id: int) -> None:
        """Un mensaje suelto en Campo sigue yendo a Campo, pero si hay decisiones abiertas con texto libre se le dice a José cómo
        contestarlas. Como mucho cada 10 minutos."""
        abiertas = self.hub.abiertas_con_texto()
        if not abiertas:
            return
        ahora = self._ahora()
        ultimo = self._leer_instante("aviso_campo")
        if ultimo is not None and ahora - ultimo < AVISO_BANDEJA_S:
            return
        self.hub.meta_escribir("aviso_campo", str(ahora))
        nums = ", ".join(f"n.º {d['id']}" for d in abiertas)
        self.r._decir(chat_id, f"Estás en Campo: esto es de Campo. Si es para una decisión abierta ({nums}), respóndele a su mensaje "
                               "(o cambia de sección con /cuenta).")

    def _leer_instante(self, clave: str) -> float | None:
        """Un instante guardado en la base (en segundos), o None si no hay ninguno o no se entiende."""
        try:
            v = self.hub.meta_leer(clave)
            return float(v) if v is not None else None
        except ValueError:
            return None

    # ------------------------------------------------------------------ orden /decisiones
    def orden(self, chat_id: int) -> None:
        pendientes = self.hub.decisiones(estado="pendiente", limite=20)
        lineas = [f"Pendientes: {len(pendientes)}" if pendientes else "No hay decisiones pendientes."]
        for d in pendientes:
            duda = " · entrega dudosa: puede haber dos copias en el chat" if d["dudosa"] else ""
            lineas.append(f"· n.º {d['id']} · {limpiar_texto(d['pregunta'], 60)} · caduca {hora_local(d['caduca']):%d/%m %H:%M}{duda}")
        respondidas = self.hub.ultimas_respondidas(5)
        lineas += ["", "Últimas respondidas:" if respondidas else "Todavía no hay respuestas."]
        for d in respondidas:
            lineas.append(f"· n.º {d['id']} · {limpiar_texto(d['respuesta_texto'], 60)} · {hora_local(d['respondida_en']):%d/%m %H:%M}")
        self.r._decir(chat_id, "\n".join(lineas))


PREFIJO_AVISO = {"info": "ℹ️", "atencion": "⚠️", "urgente": "🚨"}
RETRASO_AVISO_S = 15 * 60              # un aviso que sale más de 15 min después de crearse dice cuándo se creó
ERRORES_PERMANENTES = (400, 403)       # Telegram no lo admitirá nunca (chat cerrado, bot bloqueado): no se insiste
LOTE_AVISOS = 20


class Avisos:
    """Avisos a José guardados en la base del router (tabla `avisos`: los escribe la producción o la línea de órdenes
    `python -m router avisar`). Cada vuelta del bucle envía los pendientes en orden, como mensajes normales sin botones
    y solo al administrador. Un fallo de red deja el aviso pendiente y se reintenta con espera creciente; un error permanente lo da
    por fallido sin frenar a los demás. El cupo del día ya lo decidió `crear_aviso` al guardarlo: aquí no se cuenta nada.
    Los avisos de publicación no pasan por aquí: van en la tabla de cada cuenta (ver `Router._entregar_avisos_publicacion`)."""

    def __init__(self, router):
        self.r = router

    @property
    def hub(self):
        return self.r.hub

    def texto(self, a) -> str:
        """Lo que llega a José: el prefijo según el nivel, la cuenta si la hay y la hora de creación si llega tarde."""
        nombre = None
        if a["cuenta"]:
            c = self.hub.cuenta(a["cuenta"])
            nombre = c["nombre"] if c else a["cuenta"]
        cuerpo = f"{nombre}: {a['texto']}" if nombre else a["texto"]
        t = f"{PREFIJO_AVISO[a['nivel']]} {cuerpo}"
        if self.r.ahora() - desde_iso(a["creada"]) > RETRASO_AVISO_S:
            t += f" (creado el {hora_local(a['creada']):%d/%m a las %H:%M}; llega con retraso)"
        return t

    def vuelta(self) -> None:
        admin = self.hub.admin_id()
        if admin is None:
            return
        for a in self.hub.avisos_pendientes(LOTE_AVISOS):
            try:
                mid = self.r.tg.enviar(admin, self.texto(a))
            except ErrorTelegram as exc:
                permanente = exc.codigo in ERRORES_PERMANENTES
                self.hub.aviso_fallido(a["id"], f"{exc.metodo} {exc.codigo}", definitivo=permanente)
                self.r.log(f"aviso {a['id']}: no se pudo enviar ({exc.metodo} {exc.codigo})")
                if not permanente:
                    return                  # la red o Telegram no contestan: se reintenta en la siguiente vuelta, en orden
                continue
            self.hub.aviso_enviado(a["id"], mid)


class Programacion:
    """Elegir cuándo sale una pieza desde el bot (ROUTER_TELEGRAM.md, «Programar desde el bot»).

    «Aprobar» sigue saliendo en el primer hueco libre, como antes. «Programar» abre un selector en la propia tarjeta: la hora del plan (si
    la hay), los próximos huecos libres de hoy y mañana y «Escribir otra hora». «Ahora» pide confirmación y programa para dentro de un
    minuto. «Cambiar hora» y «Quitar de la cola» actúan sobre lo ya aprobado. Todo pasa por el mismo candado, la misma auditoría y los
    mismos avisos que Aprobar. Lo que dice un botón no se cree: la hora sale de una opción guardada en el servidor (con su dueño, su
    cuenta, su pieza y su caducidad) o de un texto que el servidor analiza."""

    def __init__(self, router):
        self.r = router

    @property
    def hub(self):
        return self.r.hub

    def _ahora(self) -> datetime:
        return datetime.fromtimestamp(self.r.ahora(), timezone.utc)

    def _nombre_cuenta(self, cuenta: str) -> str:
        return self.hub.cuenta(cuenta)["nombre"]

    def _quien(self, uid: int) -> str:
        u = self.hub.usuario(uid)
        return self.r._nombre(u["nombre"] or uid) if u else str(uid)

    def _editar(self, chat_id: int, msg_id: int | None, texto: str, botones=None) -> int | None:
        """Cambia el mensaje donde se pulsó. Si Telegram no lo deja (mensaje viejo o borrado), se manda uno nuevo: la acción no se pierde.
        Devuelve el número del mensaje que queda."""
        if msg_id:
            try:
                self.r.tg.editar(chat_id, msg_id, texto, botones)
                return msg_id
            except ErrorTelegram as exc:
                self.r.log(f"programar: no se pudo editar el mensaje ({exc.metodo} {exc.codigo}); lo envío de nuevo")
        return self.r._decir(chat_id, texto, botones)

    @staticmethod
    def _botones_aprobada(cuenta: str, id: int):
        return [[("⏰ Cambiar hora", _datos_boton(cuenta, "ch", id)), ("↩️ Quitar de la cola", _datos_boton(cuenta, "qu", id))]]

    @staticmethod
    def _motivo_estado(p, accion: str) -> str:
        """Por qué la pieza no admite esta acción, con su estado en castellano. No cambia nada."""
        estado = ESTADO_ES.get(p.estado, p.estado)
        if accion == "ch":
            if p.estado == "pendiente":
                return "Esa pieza todavía no está aprobada: usa «⏰ Programar»."
            if p.estado == "publicando":
                return "Esa pieza ya se está publicando: no se puede cambiar la hora."
            return f"Esa pieza ya está {estado}: no se puede cambiar la hora."
        if accion == "qu":
            if p.estado == "publicando":
                return "Esa pieza ya se está publicando: no se puede quitar de la cola."
            return f"Esa pieza ya está {estado}: no está en la cola."
        if accion in ("pr", "ya", "yas") and p.estado == "aprobada":
            return "Esa pieza ya está aprobada: para cambiar su hora, usa «⏰ Cambiar hora» en /cola."
        return f"Esa pieza ya está {estado}."

    # ------------------------------------------------------------------ tarjetas y lo que dice cada mensaje
    def tarjeta(self, p, rol: str):
        """La tarjeta de una pieza pendiente: texto y botones. Las cuentas con programación tienen «Programar» y «Ahora»; las demás, la
        tarjeta de siempre."""
        ad = self.r.adaptadores.get(p.cuenta)
        aviso = ad.aviso_de_salud(p.id) if rol == "admin" and hasattr(ad, "aviso_de_salud") else ""
        texto = f"¿Qué hacemos con la pieza {p.id}?{aviso}"

        def boton(accion: str) -> str:
            return _datos_boton(p.cuenta, accion, p.id)
        if not getattr(ad, "programable", False):
            return texto, [[("Aprobar", boton("ok")), ("Editar pie", boton("pie")), ("Rechazar", boton("no"))]]
        return texto, [[("✅ Aprobar", boton("ok")), ("⏰ Programar", boton("pr"))],
                       [("🚀 Ahora", boton("ya")), ("✏️ Editar pie", boton("pie"))],
                       [("❌ Rechazar", boton("no"))]]

    def vista(self, p, rol: str):
        """Lo que dice el mensaje de una pieza según su estado: la tarjeta si está pendiente; si está aprobada, su hora con «Cambiar hora»
        y «Quitar de la cola»; si no, cómo está (sin botones)."""
        if p.estado == "pendiente" and rol in ("admin", "aprobador"):
            return self.tarjeta(p, rol)
        ad = self.r.adaptadores[p.cuenta]
        nombre = self._nombre_cuenta(p.cuenta)
        if p.estado == "aprobada":
            fila = ad.fila(p.id)
            cuando = fecha_hora_es(datetime.fromisoformat(fila["programada"])) if fila and fila["programada"] else "sin hora"
            botones = self._botones_aprobada(p.cuenta, p.id) if rol in ("admin", "aprobador") else None
            return f"Pieza {p.id} de {nombre}: aprobada. Programada: {cuando} (hora de Madrid).", botones
        return f"Pieza {p.id} de {nombre}: {ESTADO_ES.get(p.estado, p.estado)}.", None

    def _hora_actual(self, ad, p) -> datetime | None:
        """La hora que ya tiene una pieza aprobada (None si está pendiente o no tiene hora)."""
        if p.estado != "aprobada":
            return None
        fila = ad.fila(p.id)
        return datetime.fromisoformat(fila["programada"]) if fila and fila["programada"] else None

    def selector(self, p, uid: int, rol: str, chat_id: int, msg_id: int | None, nuevo: bool = False) -> int | None:
        """El selector de hora: el plan (si lo dice), los próximos huecos libres (hasta una semana) y «Escribir otra hora» y «Volver». Cada
        hora queda guardada en el servidor (el botón solo lleva su número) y caduca a los 15 minutos. No ofrece la hora que la pieza ya
        tiene (se compara el instante, en UTC). Edita la tarjeta; con `nuevo` (p. ej. desde /cola, donde editar borraría la lista) manda
        un mensaje nuevo."""
        ad = self.r.adaptadores[p.cuenta]
        ahora = self._ahora()
        actual = self._hora_actual(ad, p)
        plan = ad.hora_del_plan(p.id, ahora)
        libres = [t for t in ad.huecos_libres(ahora, dias=7, excluir=p.id) if not _misma_hora(t, plan) and not _misma_hora(t, actual)]
        opciones = [(plan, f"📅 Como dice el plan: {fecha_hora_es(plan)}")] if plan is not None and not _misma_hora(plan, actual) else []
        opciones += [(t, f"🕗 {dia_corto_es(t, ahora)} {t.astimezone(MADRID):%H:%M}") for t in libres[:4]]
        ids = self.hub.crear_opciones(uid, p.cuenta, p.id, p.estado, [(t, etiqueta) for t, etiqueta in opciones]) if opciones else []
        de_plan, resto = [], []
        for i, ((_, etiqueta), num) in enumerate(zip(opciones, ids)):
            (de_plan if plan is not None and i == 0 else resto).append((etiqueta, _datos_boton(p.cuenta, "pg", num)))
        botones = [[b] for b in de_plan] + [resto[k:k + 2] for k in range(0, len(resto), 2)]
        botones.append([("✍️ Escribir otra hora", _datos_boton(p.cuenta, "pt", p.id))])
        botones.append([("↩️ Volver", _datos_boton(p.cuenta, "vol", p.id))])
        nombre = self._nombre_cuenta(p.cuenta)
        if p.estado == "aprobada":
            cuando_actual = fecha_hora_es(actual) if actual is not None else "sin hora"
            lineas = [f"Pieza {p.id} de {nombre}: ahora sale el {cuando_actual}. ¿A qué hora la cambio?"]
        else:
            lineas = [f"¿Cuándo sale la pieza {p.id} de {nombre}?"]
        lineas.append("Horas de Madrid. Las opciones caducan en 15 minutos.")
        if not opciones:
            lineas.append("No hay huecos libres en los próximos 7 días: escribe otra hora.")
        if p.estado == "pendiente" and rol == "admin" and hasattr(ad, "aviso_de_salud"):
            aviso = ad.aviso_de_salud(p.id).strip()                 # la línea ⚠ de salud se ve también aquí, antes de elegir
            if aviso:
                lineas.append(aviso)
        texto = "\n".join(lineas)
        if nuevo:
            return self.r._decir(chat_id, texto, botones)
        return self._editar(chat_id, msg_id, texto, botones)

    # ------------------------------------------------------------------ aplicar una hora (lo mismo que Aprobar, con hora)
    def _aplicar(self, uid: int, rol: str, p, instante: datetime, origen: str, dia_asumido: str | None = None,
                 rep: tuple[bool, bool] | None = None, ahora_mismo: bool = False,
                 noche_de: date | None = None) -> tuple[str | None, str | None]:
        """Programa la pieza: la aprueba a esa hora si está pendiente, o cambia la hora si ya está aprobada. Pasa por el mismo candado, la
        misma auditoría y los mismos avisos que Aprobar (también el de salud, para el administrador). Devuelve (motivo, texto de la
        confirmación); con motivo, no ha cambiado nada."""
        ad = self.r.adaptadores[p.cuenta]
        admin = rol == "admin"
        anterior = None
        if p.estado == "pendiente":
            motivo = ad.aprobar(p.id, uid, para_admin=admin, hora=instante, origen=origen)
        else:
            fila = ad.fila(p.id)
            anterior = fecha_hora_es(datetime.fromisoformat(fila["programada"])) if fila and fila["programada"] else None
            motivo = ad.cambiar_hora(p.id, instante, uid, origen=origen, para_admin=admin)
        if motivo:
            return motivo, None
        self.hub.auditar(uid, "pieza_programada", f"{p.cuenta}:{p.id}:{origen}")
        detalle = ad.resumen_decision(p.id) if hasattr(ad, "resumen_decision") else ""      # con el ⚠ de salud, como en Aprobar
        self.r._avisar_admins(f"{self._quien(uid)}: pieza {p.cuenta} {p.id} "
                              f"{'aprobada y programada' if anterior is None else 'reprogramada'} para {fecha_hora_es(instante)}.{detalle}",
                              excepto=uid)
        return None, self._texto_confirmacion(p, rol, instante, anterior, dia_asumido, rep, ahora_mismo, noche_de)

    def _texto_confirmacion(self, p, rol: str, instante: datetime, anterior: str | None, dia_asumido: str | None,
                            rep: tuple[bool, bool] | None, ahora_mismo: bool, noche_de: date | None = None) -> str:
        """«Programada: sábado 10 oct, 18:30 (hora de Madrid).» y lo que conviene saber: el día que se asumió, el cambio de hora, qué noche
        es (una medianoche dicha con día), los avisos «ojo» (otra pieza cerca, madrugada) y, si no va a salir sola, por qué (modo, pausa,
        cola parada, falta el token) y el aviso de salud. «Sale en uno o dos minutos» solo si no hay nada que lo impida."""
        ad = self.r.adaptadores[p.cuenta]
        nombre = self._nombre_cuenta(p.cuenta)
        cabeza = f"Pieza {p.id} de {nombre}: aprobada." if anterior is None else f"Pieza {p.id} de {nombre}: hora cambiada."
        salida = ad.aviso_salida(p.id, para_admin=(rol == "admin"))
        cuando = f"Programada: {fecha_hora_es(instante)} (hora de Madrid)" + (": sale en uno o dos minutos." if ahora_mismo and not salida else ".")
        lineas = [cabeza, cuando]
        if anterior is not None:
            lineas.append(f"Antes estaba programada para {anterior}.")
        if dia_asumido:
            lineas.append(f"Como la hora de hoy ya ha pasado, la he puesto para {dia_asumido}.")
        if noche_de is not None:
            lineas.append(f"Es la medianoche que cierra el {fecha_corta_es(noche_de)}.")
        repetida, segunda = rep if rep is not None else repeticion_hora(instante)
        if repetida:
            lineas.append("Esa hora se repite ese día por el cambio de hora: " + ("es la segunda vez, ya con horario de invierno."
                                                                              if segunda else "he tomado la primera vez, con horario de verano."))
        lineas += self._avisos_hora(ad, p.id, instante)
        if salida:
            lineas.append(salida)
        return "\n".join(lineas)

    @staticmethod
    def _avisos_hora(ad, id: int, instante: datetime) -> list[str]:
        """Dos avisos suaves, sin bloquear: otra pieza de la cola a menos de 45 minutos (antes o después), y que la hora sea de madrugada
        (de 00:00 a 07:00)."""
        out = []
        ref = instante.astimezone(timezone.utc)
        for otra, t in ad.choques(id, instante, minutos=45):
            mins = round(abs((t.astimezone(timezone.utc) - ref).total_seconds()) / 60)
            lado = "antes" if t.astimezone(timezone.utc) < ref else "después"
            out.append(f"ojo: hay otra a las {t.astimezone(MADRID):%H:%M}, a {mins} minutos {lado} (pieza {otra}).")
        if 0 <= instante.astimezone(MADRID).hour < 7:
            out.append("ojo: es de madrugada (entre las 00:00 y las 07:00).")
        return out

    # ------------------------------------------------------------------ botones
    def boton(self, cb_id: str, chat_id: int, msg_id: int | None, uid: int, rol: str, cuenta: str, accion: str, num: int,
              cancelo_hora: bool = False) -> None:
        """Una pulsación de elegir hora. El router ya comprobó chat, usuario, cuenta y rol. Aquí, la pieza sale de esa cuenta (salvo en «pg»,
        que es una opción guardada: de ella salen la pieza, su dueño y la hora, nunca del botón). `cancelo_hora`: esta pulsación cerró una
        espera de «Escribir otra hora» (se dice que no se ha cambiado la hora)."""
        r = self.r
        if accion == "pg":
            return self._elegir(cb_id, chat_id, msg_id, uid, rol, cuenta, num)
        ad = r.adaptadores[cuenta]
        p = ad.pieza(num)
        if p is None:
            return r._responder(cb_id, "Esa pieza no existe.", True)
        if accion == "pr":                                      # Programar: pieza pendiente, el selector en su tarjeta
            if p.estado != "pendiente":
                return r._responder(cb_id, self._motivo_estado(p, accion), True)
            self.selector(p, uid, rol, chat_id, msg_id)
            return r._responder(cb_id)
        if accion == "ch":                                      # Cambiar hora: pieza aprobada; el selector en mensaje nuevo (la cola sigue)
            if p.estado != "aprobada":
                return r._responder(cb_id, self._motivo_estado(p, accion), True)
            self.selector(p, uid, rol, chat_id, None, nuevo=True)
            return r._responder(cb_id)
        if accion == "ya":                                      # Ahora: confirmación en dos pasos (su «Sí» vale dos minutos, solo para esta persona)
            if p.estado != "pendiente":
                return r._responder(cb_id, self._motivo_estado(p, accion), True)
            r.hub.preguntar_ahora(uid, cuenta, p.id)
            motivo = r.adaptadores[cuenta].motivo_sin_salida()
            if motivo:                                          # no saldría sola: la pregunta lo dice, y «Sí» no promete lo que no pasará
                cabeza = f"No saldrá: {motivo}."
            else:
                cabeza = f"La pieza {p.id} de {self._nombre_cuenta(cuenta)} saldría en uno o dos minutos, si la publicación automática está en marcha."
            texto = f"¿Publicar ahora? Sí / No\n{cabeza}"
            self._editar(chat_id, msg_id, texto, [[("✅ Sí", _datos_boton(cuenta, "yas", p.id)), ("❌ No", _datos_boton(cuenta, "vol", p.id))]])
            return r._responder(cb_id)
        if accion == "yas":                                     # Sí: programada para dentro de un minuto, si la pregunta sigue viva
            if p.estado != "pendiente":
                return r._responder(cb_id, self._motivo_estado(p, accion), True)
            if not r.hub.pregunta_ahora_viva(uid, cuenta, p.id):
                return r._responder(cb_id, f"Esa pregunta ya caducó (pasaron {PREGUNTA_AHORA_S // 60} minutos): no he hecho nada. "
                                           "Pulsa 🚀 Ahora otra vez.")
            r.hub.olvidar_ahora(uid, cuenta, p.id)
            motivo, texto = self._aplicar(uid, rol, p, self._ahora() + timedelta(minutes=1), "boton:yas", ahora_mismo=True)
            if motivo:
                return r._responder(cb_id, motivo[:190], True)
            self._editar(chat_id, msg_id, texto, self._botones_aprobada(cuenta, p.id))
            return r._responder(cb_id, "Programada")
        if accion == "pt":                                      # Escribir otra hora: el siguiente texto de esta persona es la hora
            if p.estado not in ("pendiente", "aprobada"):
                return r._responder(cb_id, self._motivo_estado(p, accion), True)
            # La sección activa NO cambia (así las órdenes de Campo siguen llegando). La espera recuerda dónde estaba y el texto siguiente
            # se acepta en esa sección o en la de la pieza (ver Router._espera_aqui).
            texto = (f"Escribe la hora de la pieza {p.id} en un mensaje, por ejemplo «18:30», «mañana a las 9:30» o «en 2 horas». "
                     f"Tienes {ESPERA_HORA_S // 60} minutos; /cancelar lo deja.")
            mensaje = self._editar(chat_id, msg_id, texto, [[("↩️ Volver", _datos_boton(cuenta, "vol", p.id))]])
            r.hub.esperar(uid, cuenta, p.id, "hora", ESPERA_HORA_S, mensaje=mensaje, origen=r._activa(uid))
            return r._responder(cb_id)
        if accion == "vol":                                     # Volver (y «No» de Ahora): la tarjeta o el estado de siempre
            r.hub.olvidar_ahora(uid, cuenta, p.id)              # un «Sí» de una pregunta cerrada ya no vale
            texto, botones = self.vista(p, rol)
            self._editar(chat_id, msg_id, texto, botones)
            return r._responder(cb_id, "Hecho: no he cambiado la hora." if cancelo_hora else "")
        if accion == "qu":                                      # Quitar de la cola: vuelve a pendiente y se reenvía su tarjeta
            if p.estado != "aprobada":
                return r._responder(cb_id, self._motivo_estado(p, accion), True)
            motivo = ad.quitar_de_cola(p.id, uid, origen="boton:qu")
            if motivo:
                return r._responder(cb_id, motivo[:190], True)
            r.hub.auditar(uid, "pieza_quitada_cola", f"{cuenta}:{p.id}")
            r._responder(cb_id, "Quitada de la cola")
            nueva = ad.pieza(p.id)
            if nueva is not None:
                texto, botones = self.tarjeta(nueva, rol)
                r._decir(chat_id, f"Pieza {p.id} de {self._nombre_cuenta(cuenta)}: vuelve a pendiente. Te dejo su tarjeta otra vez.")
                r._decir(chat_id, texto, botones)
            r._avisar_admins(f"{self._quien(uid)}: pieza {cuenta} {p.id} quitada de la cola (vuelve a pendiente).", excepto=uid)
            return None
        return r._responder(cb_id, "Ese botón no es de este tipo.", True)

    def _elegir(self, cb_id: str, chat_id: int, msg_id: int | None, uid: int, rol: str, cuenta: str, num: int) -> None:
        """Una opción del selector. La fila guardada dice la pieza y la hora: se comprueba que es de quien pulsa y de esta cuenta, que no
        se usó ni caducó, que la pieza sigue en el mismo estado y que la hora aún está a dos minutos o más. Pulsar dos veces no programa
        dos veces: la primera la marca como usada; si el candado la para, se libera para que se pueda elegir otra."""
        r = self.r
        fila = self.hub.opcion(num)
        if fila is None or fila["user_id"] != uid or fila["cuenta"] != cuenta:
            return r._responder(cb_id, "Esa opción ya no vale. Vuelve a abrir «Programar».", True)
        if fila["usada"]:
            return r._responder(cb_id, "Esa opción ya se usó: no he cambiado nada.", True)
        if self._sustituida(fila):                              # no es que caducara: se abrió otro selector después (no es una alerta)
            return r._responder(cb_id, "Ese selector ya no vale: abriste otro después. Usa el último.")
        if fila["caduca"] <= self.hub.iso(r.ahora()):           # caducada de verdad: el texto dice cómo seguir según el estado de entonces
            if fila["estado_pieza"] == "aprobada":
                return r._responder(cb_id, "Esa hora ya no vale. Pulsa ⏰ Cambiar hora otra vez.")
            return r._responder(cb_id, "Esa opción ha caducado (pasaron 15 minutos). Vuelve a abrir «Programar».")
        ad = r.adaptadores[cuenta]
        p = ad.pieza(fila["pieza"])
        if p is None:
            return r._responder(cb_id, "Esa opción ya no vale. Vuelve a abrir «Programar».", True)
        if p.estado != fila["estado_pieza"]:
            return r._responder(cb_id, "La pieza ya ha cambiado de estado: no he hecho nada. Vuelve a abrir su tarjeta.", True)
        instante = datetime.fromisoformat(fila["hora"])
        if instante < self._ahora() + timedelta(minutes=2):
            return r._responder(cb_id, "Esa hora ya ha pasado o queda menos de dos minutos. Vuelve a abrir «Programar».", True)
        if not self.hub.usar_opcion(num):
            return r._responder(cb_id, "Esa opción ya se usó: no he cambiado nada.", True)
        motivo, texto = self._aplicar(uid, rol, p, instante, "boton:pg")
        if motivo:
            self.hub.liberar_opcion(num)
            return r._responder(cb_id, motivo[:190], True)
        self._editar(chat_id, msg_id, texto, self._botones_aprobada(cuenta, p.id))
        return r._responder(cb_id, "Programada")

    # ------------------------------------------------------------------ el texto que sigue a «Escribir otra hora»
    def recibir_hora(self, chat_id: int, uid: int, espera, texto: str) -> None:
        """El texto que sigue a «Escribir otra hora». Se analiza: si no se entiende, es ambigua o no vale, se dice con tres ejemplos y se
        sigue esperando (hasta 10 minutos). Si vale, se programa como cualquier aprobación. El texto escrito no se guarda."""
        r = self.r
        cuenta, id = espera["cuenta"], espera["pieza"]
        rol = self.hub.rol_en(uid, cuenta)
        ad = r.adaptadores.get(cuenta)
        if rol not in ("admin", "aprobador") or ad is None or not getattr(ad, "programable", False):
            self.hub.dejar_de_esperar(uid)
            return r._decir(chat_id, SIN_ACCESO)
        p = ad.pieza(id)
        if p is None or p.estado not in ("pendiente", "aprobada"):
            aviso = "Esa pieza ya no existe." if p is None else self._motivo_estado(p, "pt")
            self.hub.dejar_de_esperar(uid)
            return r._decir(chat_id, aviso)
        try:
            h = analizar_hora(texto, self._ahora())
        except ErrorHora as exc:                                 # sigue esperando: se dice cuánto queda, y que /cancelar lo deja
            minutos = max(1, int((espera["caduca"] - r.ahora()) // 60))
            return r._decir(chat_id, f"{exc.motivo}. Sigo esperando la hora (te quedan {minutos} min; /cancelar lo deja). {PROGRAMAR_EJEMPLOS}")
        motivo, confirmacion = self._aplicar(uid, rol, p, h.instante, "texto", dia_asumido=h.dia_asumido, rep=(h.repetida, h.segunda),
                                             noche_de=h.noche_de)
        self.hub.dejar_de_esperar(uid)
        if motivo:
            return r._decir(chat_id, motivo)
        ya = ad.pieza(id)
        if ya is not None:
            vista_texto, botones = self.vista(ya, rol)
            self._editar(chat_id, espera["mensaje"], vista_texto, botones)
        r._decir(chat_id, confirmacion)

    def tras_espera(self, chat_id: int, uid: int, texto: str) -> bool:
        """Un texto con forma de hora que llega después de «Escribir otra hora» caducada o cancelada. No cambia nada, y se dice: no va a la
        bandeja ni contesta a una decisión abierta. True si lo ha tomado. Si la sección activa es Campo, no se toca (es de Campo)."""
        fin = self.hub.fin_espera(uid)
        activa = self.r._activa(uid)
        if fin is None or activa is None or activa not in (fin["cuenta"], fin.get("origen")):   # solo donde se pidió la hora (o en la de la pieza)
            return False
        cuenta, rol = fin["cuenta"], self.hub.rol_en(uid, fin["cuenta"])
        if rol not in ("admin", "aprobador") or cuenta not in self.r.adaptadores:
            return False
        try:
            analizar_hora(texto, self._ahora())
        except ErrorHora as exc:
            if exc.motivo == "No la entiendo":                   # no es una hora: sigue su camino (bandeja o Campo)
                return False
        p = self.r.adaptadores[cuenta].pieza(fin["pieza"])
        motivo = (f"la espera caducó (pasaron {ESPERA_HORA_S // 60} minutos)" if fin["motivo"] == "caducada"
                  else "cancelaste la espera")
        if p is None:
            texto_fin = f"No he cambiado ninguna hora: {motivo}."
        else:
            como = {"aprobada": " Pulsa ⏰ Cambiar hora en /cola.", "pendiente": " Pulsa ⏰ Programar en su tarjeta."}.get(p.estado, "")
            texto_fin = f"No he cambiado la hora de la pieza {p.id} de {self._nombre_cuenta(cuenta)}: {motivo}.{como}"
        self.r._decir(chat_id, texto_fin)
        return True

    @staticmethod
    def _sustituida(fila) -> bool:
        """Una opción que cayó porque se abrió otro selector de la misma pieza: su caducidad se adelantó a ese momento (ver
        `Hub.crear_opciones`), así que caduca antes de los quince minutos que dura una opción normal."""
        return desde_iso(fila["caduca"]) < desde_iso(fila["creada"]) + OPCION_CADUCA_S

    # ------------------------------------------------------------------ /cola
    def cola(self, chat_id: int, uid: int, cuenta: str, rol: str) -> None:
        """/cola: lo aprobado con su hora, lo que se está publicando o tiene error, y cuántas piezas quedan por decidir. Quien puede decidir
        ve un botón «⏰ Cambiar hora» y otro «↩️ Quitar de la cola» por cada pieza aprobada (como mucho ocho) en un solo mensaje; quien solo
        lee, el texto."""
        r = self.r
        ad = r.adaptadores[cuenta]
        nombre = self._nombre_cuenta(cuenta).upper()
        filas = ad.filas_cola()
        pendientes = ad.contar("pendiente")
        if not filas:
            return r._decir(chat_id, f"{nombre} · cola\nNo hay piezas aprobadas ni programadas. Pendientes de decidir: {pendientes}.")
        lineas = [f"{nombre} · cola"]
        aprobadas = []
        for f in filas:
            titulo = limpiar_texto(f["titulo"], 40)
            if f["estado"] == "aprobada":
                aprobadas.append(f)
                marca = " (a mano)" if f["programada_manual"] else ""
                cuando = fecha_hora_es(datetime.fromisoformat(f["programada"])) if f["programada"] else "sin hora"
                lineas.append(f"{f['id']} · {titulo} · {cuando}{marca}")
            else:
                lineas.append(f"{f['id']} · {titulo} · {ESTADO_ES.get(f['estado'], f['estado'])}: no se puede cambiar desde aquí")
        lineas.append(f"Pendientes de decidir: {pendientes}.")
        botones = None
        if rol in ("admin", "aprobador") and aprobadas:
            botones = [[(f"⏰ Cambiar hora ({f['id']})", _datos_boton(cuenta, "ch", f["id"])),
                        (f"↩️ Quitar de la cola ({f['id']})", _datos_boton(cuenta, "qu", f["id"]))] for f in aprobadas[:8]]
            if len(aprobadas) > 8:                              # sin botones para estas: /ver las abre con sus botones
                lineas.append(f"Hay {len(aprobadas) - 8} más sin botones: para cambiarlas, /ver {cuenta} <número>.")
        return r._decir(chat_id, "\n".join(lineas), botones)


class Router:
    def __init__(self, hub, tg, adaptadores: dict, bot_usuario: str, ahora=time.time, log=None, planes=None, produccion=None, panel: bool = False):
        self.hub, self.tg, self.adaptadores, self.bot_usuario = hub, tg, adaptadores, bot_usuario
        self.ahora = ahora
        self.log = log or (lambda m: print(m, file=sys.stderr))
        self.planes = planes
        self.produccion = produccion                    # planes/n1/produccion.json: la línea de la producción en /estado (si se da)
        self._hilos: list[threading.Thread] = []
        self._ruido: dict[int, list[float]] = {}
        self.validacion = Validacion(self)              # /validar, /validados y los botones de validación de hechos
        self.decisiones = Decisiones(self)              # preguntas a José con botones, su bandeja y /decisiones
        self.avisos = Avisos(self)                      # avisos a José guardados en la base (sin botones)
        self.programacion = Programacion(self)          # elegir la hora de salida: Programar, Ahora, Cambiar hora, Quitar de la cola
        self.panel = Panel(self, activo=panel)          # el panel de aprobación (router/panel.py); apagado salvo que se pida (la nube lo pide)

    # ------------------------------------------------------------------ entrada
    def procesar(self, upd: dict) -> None:
        if "callback_query" in upd:
            self._callback(upd["callback_query"], upd)
        elif "message" in upd:
            self._mensaje(upd["message"], upd)

    def _decir(self, chat_id: int, texto: str, botones=None) -> int:
        """Envía un mensaje y devuelve su número (hace falta para reconocer la respuesta a una petición de nota)."""
        return self.tg.enviar(chat_id, texto, botones)

    @staticmethod
    def _nombre(n) -> str:
        """El nombre de Telegram es de quien lo escribe: sin saltos de línea ni caracteres de control, y corto, antes de ponerlo en un aviso."""
        return "".join(c for c in str(n) if c.isprintable())[:40].strip() or "alguien"

    def _responder(self, cb_id: str, texto: str = "", alerta: bool = False) -> None:
        """Contestar al botón no puede dejar a medias lo que ya se ha hecho: si Telegram falla, se anota y se sigue."""
        try:
            self.tg.responder_callback(cb_id, texto, alerta)
        except ErrorTelegram as exc:
            self.log(f"answerCallbackQuery no llegó ({exc.codigo})")

    def _callado(self, uid: int) -> bool:
        """Quien no tiene acceso recibe una respuesta, no mil: a partir de 8 mensajes por minuto se le ignora sin gastar auditoría."""
        ahora = self.ahora()
        marcas = [t for t in self._ruido.get(uid, []) if ahora - t < 60] + [ahora]
        self._ruido[uid] = marcas
        return len(marcas) > 8

    def _anfitriones(self) -> list:
        return [a for c, a in self.adaptadores.items() if getattr(a, "anfitrion", False) and self.hub.cuenta(c)]

    def _activa(self, uid: int) -> str | None:
        propias = self.hub.cuentas_de(uid)
        elegida = self.hub.cuenta_activa(uid)
        return elegida if elegida in propias else (propias[0] if propias else None)

    # ------------------------------------------------------------------ mensajes
    def _mensaje(self, m: dict, upd: dict) -> None:
        chat = m.get("chat") or {}
        remitente = m.get("from") or {}
        if chat.get("type") != "private" or not remitente.get("id") or chat.get("id") != remitente["id"]:
            return
        uid, chat_id = remitente["id"], chat["id"]
        texto = (m.get("text") or "").strip()
        if texto.startswith("/start"):
            partes = texto.split(maxsplit=1)
            if len(partes) == 2:
                return self._canjear(chat_id, uid, remitente.get("first_name", ""), partes[1].strip())
            if self.hub.activo(uid) and self.panel.comando(chat_id, uid, "/start"):
                return
            return self._decir(chat_id, "Hola. " + (self._ayuda(uid) if self.hub.activo(uid) else PRIVADO))
        if not self.hub.activo(uid):
            if self._callado(uid):
                return
            self.hub.auditar(uid, "mensaje_sin_acceso")
            return self._decir(chat_id, PRIVADO)
        if texto.startswith("/"):
            cancelo = self._soltar_nota(uid)                                # una orden deja sin efecto la nota pedida
            return self._comando(chat_id, uid, texto, upd, cancelo=cancelo)
        espera = self.hub.esperando(uid)
        adjunto = any(k in m for k in ("photo", "document", "video", "voice", "audio"))
        respuesta_a = (m.get("reply_to_message") or {}).get("message_id")
        if espera and texto and not adjunto and espera["tipo"] == "horapn" and self.hub.es_admin(uid) and not m.get("reply_to_message"):
            return self.panel.recibir_hora(chat_id, uid, espera, texto)              # «Escribir hora» del panel: el texto suelto es la hora
        if espera and texto and not adjunto and espera["tipo"] != "horapn" and self._espera_aqui(uid, espera):
            if espera["tipo"] == "reparos":                                 # la nota de un hecho del banco: texto suelto o respuesta a la petición
                if respuesta_a in (None, self.hub.peticion(uid)):           # otra respuesta sigue su camino (Campo)
                    return self.validacion.recibir_nota(chat_id, uid, espera, texto)
            elif espera["tipo"] == "hora":                                  # «Escribir otra hora»: un texto suelto es la hora; una respuesta, no
                if not m.get("reply_to_message"):
                    return self.programacion.recibir_hora(chat_id, uid, espera, texto)
            elif not m.get("reply_to_message"):                             # el pie: solo texto suelto de la sección en la que se pidió
                return self._recibir_pie(chat_id, uid, espera, texto)
        if espera is None and texto and not adjunto and not m.get("reply_to_message"):
            if self.programacion.tras_espera(chat_id, uid, texto):          # una hora que llega tras caducar o cancelar: no cambia nada, y se dice
                return
        es_respuesta_o_adjunto = bool(m.get("reply_to_message")) or adjunto
        if self.hub.es_admin(uid):
            if self.decisiones.respuesta_libre(chat_id, uid, m, texto, adjunto):      # la respuesta a una decisión con texto libre
                return
            if m.get("reply_to_message") or self._activa_es_anfitriona(uid):
                if self._delegar(upd):
                    return
            if self._anfitriones() and (es_respuesta_o_adjunto or texto.lower().startswith("nota:")):
                return self._decir(chat_id, "Estás en otra sección y las fotos y las notas son de Campo. Cámbiate con /cuenta y vuelve a mandarlo.")
            if texto and not adjunto:                       # lo que hoy se contesta con «no te he entendido» va a la bandeja
                return self.decisiones.anotar(chat_id, uid, texto, m.get("message_id"))
        self._decir(chat_id, "No te he entendido. /ayuda te dice qué puedo hacer.")

    def _activa_es_anfitriona(self, uid: int) -> bool:
        return getattr(self.adaptadores.get(self._activa(uid)), "anfitrion", False)

    def _espera_aqui(self, uid: int, espera) -> bool:
        """¿Vale esta espera en la sección en la que está ahora la persona? Siempre en la de su cuenta. Para «hora» y «pie», también en la
        que tenía al pedirla: así «Escribir otra hora» y «Editar pie» no cambian la sección activa (las órdenes de Campo siguen llegando)."""
        activa = self._activa(uid)
        if activa == espera["cuenta"]:
            return True
        return espera["tipo"] in ("hora", "pie") and activa is not None and activa == self.hub.origen_espera(uid)

    def _soltar_nota(self, uid: int) -> str | None:
        """Una nota pedida con ✏️, o una hora pedida con «Escribir otra hora», no sobrevive a lo que haga después el administrador: otra
        tarjeta, un botón o una orden la anulan. (El pie sigue su camino: solo lo cancela su propio flujo.) Devuelve el tipo de espera que
        ha cancelado, o None: así «Volver» y «/cancelar» pueden decir que no se ha cambiado la hora."""
        espera = self.hub.esperando(uid)
        if espera is not None and espera["tipo"] in ("reparos", "hora"):
            self.hub.dejar_de_esperar(uid, motivo="cancelada")
            return espera["tipo"]
        return None

    def _delegar(self, upd: dict) -> bool:
        """Pasa la actualización a la lógica de la cuenta alojada. Solo se llama para el administrador."""
        hosts = self._anfitriones()
        for h in hosts:
            h.procesar(upd)
        return bool(hosts)

    def _canjear(self, chat_id: int, uid: int, nombre: str, codigo: str) -> None:
        ok, dato = self.hub.canjear(codigo, uid, nombre)
        if not ok:
            return self._decir(chat_id, "Esa invitación no vale (puede haber caducado o ya estar usada). Pide otra." if dato != "bloqueado"
                               else "Demasiados intentos. Prueba más tarde.")
        c = self.hub.cuenta(dato)
        rol = self.hub.rol_en(uid, dato)
        self.hub.fijar_cuenta_activa(uid, dato)
        self._decir(chat_id, f"Listo. Ya tienes acceso a {c['nombre']} como {rol}. Solo verás las piezas de esa cuenta. /ayuda te explica el resto.")
        self._avisar_admins(f"{self._nombre(nombre or uid)} ha entrado en {c['nombre']} como {rol}.", excepto=uid)
        for p in self.adaptadores[dato].listar(3):
            if p.estado == "pendiente" and p.entregada:
                self._enviar_pieza(uid, p, rol)

    def _comando(self, chat_id: int, uid: int, texto: str, upd: dict, cancelo: str | None = None) -> None:
        orden, *args = texto.split()
        orden = orden.split("@")[0].lower()
        admin = self.hub.es_admin(uid)
        if orden in ("/panel", "/pendientes", "/calendario", "/ayuda") and self.panel.comando(chat_id, uid, orden):
            return
        if orden == "/ayuda":
            return self._decir(chat_id, self._ayuda(uid))
        if orden == "/cancelar":                                 # (la espera de una hora ya la cerró _soltar_nota, y lo dice aquí)
            self.hub.dejar_de_esperar(uid, motivo="cancelada")
            return self._decir(chat_id, "Hecho: no he cambiado la hora." if cancelo == "hora" else "Hecho, lo dejamos.")
        if orden == "/hoy":
            return self._listar(chat_id, uid)
        if orden == "/ver":
            return self._ver(chat_id, uid, args)
        if orden == "/cuenta":
            return self._cuenta(chat_id, uid, args)
        if orden == "/baja":
            if admin:
                return self._decir(chat_id, "El administrador no se da de baja.")
            self.hub.revocar(uid)
            return self._decir(chat_id, "Te he dado de baja.")
        if orden in ORDENES_DE_CUENTA:
            return self._orden_de_cuenta(chat_id, uid, orden, upd)
        if not admin:
            self.hub.auditar(uid, "orden_admin_denegada", orden)
            return self._decir(chat_id, SIN_ACCESO)
        if orden in ("/validar", "/validados"):
            return self.validacion.orden(chat_id, uid, orden, args)
        if orden == "/invitar":
            return self._invitar(chat_id, uid, args)
        if orden == "/revocar":
            return self._revocar(chat_id, uid, args)
        if orden == "/usuarios":
            return self._usuarios(chat_id)
        if orden == "/cuentas":
            return self._cuentas(chat_id)
        if orden == "/instagram":
            return self._instagram(chat_id, uid, args)
        if orden in ("/plan", "/plan_nuevo"):
            return self._plan(chat_id, uid, orden, args)
        if orden == "/decisiones":
            return self.decisiones.orden(chat_id)
        if self._activa_es_anfitriona(uid) and self._delegar(upd):      # /generar, /otra, /exportar... son de Campo
            return
        self._decir(chat_id, "No conozco esa orden. /ayuda")

    def _ayuda(self, uid: int) -> str:
        if self.hub.es_admin(uid) and self.panel.disponible():
            from .textos_panel import ayuda_comandos       # noqa: PLC0415
            return ayuda_comandos()
        base = ("/hoy: tus últimas piezas\n/ver <cuenta> <número>: te la vuelvo a enseñar\n/cuenta: en qué sección estás\n"
                "/cancelar: cancela una edición en curso\n/baja: salir")
        if self.hub.es_admin(uid):
            base += ("\n/estado, /cola, /pausa, /seguir: de la sección activa"
                     "\n\nAdministración:\n/invitar <cuenta> <lector|aprobador>: enlace de invitación (72 h, un solo uso)\n"
                     "/revocar <id>: quita el acceso al instante\n/usuarios\n/cuentas\n/instagram: estado de la conexión\n"
                     "/plan: el plan del mes\n/plan_nuevo [AAAA-MM]: prepararlo ahora\n"
                     "/validar [cuenta] [id]: revisar los hechos del banco, uno a uno\n"
                     "/validados [cuenta]: cuántos hechos están validados, con reparos o pendientes"
                     "\n/decisiones: lo que espera tu respuesta y las últimas respondidas")
        return "Esto es lo que puedes hacer:\n" + base

    def _cuenta(self, chat_id: int, uid: int, args: list[str]) -> None:
        propias = self.hub.cuentas_de(uid)
        if args and args[0].lower() in propias:
            self.hub.fijar_cuenta_activa(uid, args[0].lower())
            return self._decir(chat_id, f"Sección activa: {self.hub.cuenta(args[0].lower())['nombre']}.")
        activa = self._activa(uid)
        botones = [[(self.hub.cuenta(c)["nombre"] + (" ✓" if c == activa else ""), _datos_boton(c, "cta", 0))] for c in propias]
        self._decir(chat_id, "Estás en: " + (self.hub.cuenta(activa)["nombre"] if activa else "ninguna") + ". ¿A cuál cambio?", botones or None)

    def _orden_de_cuenta(self, chat_id: int, uid: int, orden: str, upd: dict) -> None:
        cuenta = self._activa(uid)
        if not cuenta:
            return self._decir(chat_id, SIN_ACCESO)
        ad = self.adaptadores.get(cuenta)
        if getattr(ad, "anfitrion", False):
            if not self.hub.es_admin(uid):
                return self._decir(chat_id, SIN_ACCESO)
            return ad.procesar(upd)
        rol = self.hub.rol_en(uid, cuenta)
        if orden in ("/pausa", "/seguir") and rol not in ("admin", "aprobador"):
            return self._decir(chat_id, "Tu rol no permite esto.")
        if orden == "/estado":
            texto = f"{self.hub.cuenta(cuenta)['nombre'].upper()}\n" + ad.texto_estado()
            if cuenta == "n1" and self.produccion is not None:          # la producción automática es de n1 (produccion.py)
                texto += "\n" + self._linea_produccion()
            return self._decir(chat_id, texto)
        if orden == "/cola":
            if getattr(ad, "programable", False):                            # con botones para cambiar la hora o quitarla de la cola
                return self.programacion.cola(chat_id, uid, cuenta, rol)
            return self._decir(chat_id, f"{self.hub.cuenta(cuenta)['nombre'].upper()}\n" + ad.texto_cola())
        pausar = orden == "/pausa"
        ad.pausar(pausar)
        self.hub.auditar(uid, "pausa" if pausar else "reanudado", cuenta)
        self._decir(chat_id, "Publicaciones en pausa. /seguir las reanuda." if pausar else "Publicaciones reanudadas.")

    def _linea_produccion(self) -> str:
        """La línea de la producción automática para /estado. Nunca tumba el router: si no se puede mirar, se dice."""
        try:
            import produccion_avisos                     # noqa: PLC0415 (solo para /estado; el router arranca sin él)
            return produccion_avisos.linea_estado(self.produccion, self.ahora())
        except Exception as exc:
            return f"Producción: no puedo mirarla ahora ({type(exc).__name__})."

    def _listar(self, chat_id: int, uid: int) -> None:
        lineas = []
        for cuenta in self.hub.cuentas_de(uid):
            if getattr(self.adaptadores.get(cuenta), "anfitrion", False):
                continue
            nombre = self.hub.cuenta(cuenta)["nombre"]
            for p in self.adaptadores[cuenta].listar(5):
                lineas.append(f"{nombre} · {cuenta} {p.id} · {p.titulo[:40]} · {p.estado}")
        self._decir(chat_id, "\n".join(lineas) if lineas else "Todavía no hay piezas.")

    def _ver(self, chat_id: int, uid: int, args: list[str]) -> None:
        if len(args) != 2 or not args[1].isdigit():
            return self._decir(chat_id, "Uso: /ver <cuenta> <número>")
        cuenta, id = args[0].lower(), int(args[1])
        rol = self.hub.rol_en(uid, cuenta)
        p = self.adaptadores[cuenta].pieza(id) if rol and cuenta in self.adaptadores else None
        if not p:
            self.hub.auditar(uid, "ver_denegado", f"{cuenta}:{id}")
            return self._decir(chat_id, SIN_ACCESO)
        self._enviar_pieza(uid, p, rol)

    def _invitar(self, chat_id: int, uid: int, args: list[str]) -> None:
        if len(args) < 2:
            return self._decir(chat_id, "Uso: /invitar <cuenta> <lector|aprobador> [nota]")
        cuenta, rol = args[0].lower(), args[1].lower()
        try:
            codigo = self.hub.crear_invitacion(cuenta, rol, uid, nota=" ".join(args[2:]))
        except ValueError as exc:
            return self._decir(chat_id, f"No se pudo: {exc}.")
        nombre = self.hub.cuenta(cuenta)["nombre"]
        self._decir(chat_id, f"Invitación a {nombre} como {rol}. Vale 72 horas y un solo uso:\n"
                             f"https://t.me/{self.bot_usuario}?start={codigo}\n\n"
                             "Mándasela por un canal privado. La persona solo verá las piezas de esa cuenta.")

    def _revocar(self, chat_id: int, uid: int, args: list[str]) -> None:
        if len(args) != 1 or not args[0].lstrip("-").isdigit():
            return self._decir(chat_id, "Uso: /revocar <id de usuario> (los ves con /usuarios)")
        objetivo = int(args[0])
        ok = self.hub.revocar(objetivo)
        self.hub.auditar(uid, "revocado" if ok else "revocar_fallido", str(objetivo))
        self._decir(chat_id, "Acceso retirado." if ok else "No he podido (no existe, o es el administrador).")

    def _usuarios(self, chat_id: int) -> None:
        lineas = []
        for u in self.hub.usuarios():
            cuentas = ",".join(self.hub.cuentas_de(u["user_id"])) if not u["revocado"] else "-"
            lineas.append(f'{u["user_id"]} · {u["nombre"]} · {u["rol"]}{" · REVOCADO" if u["revocado"] else ""} · {cuentas}')
        self._decir(chat_id, "\n".join(lineas) or "Sin usuarios.")

    def _cuentas(self, chat_id: int) -> None:
        lineas = []
        for c in self.hub.cuentas():
            ad = self.adaptadores.get(c["codigo"])
            pend = "alojada" if getattr(ad, "anfitrion", False) else (sum(1 for p in ad.listar(50) if p.estado == "pendiente") if ad else "?")
            lineas.append(f'{c["codigo"]} · {c["nombre"]} · {c["tipo"]} · {len(self.hub.miembros(c["codigo"]))} personas · {pend if pend == "alojada" else f"{pend} pendientes"}')
        self._decir(chat_id, "\n".join(lineas) or "Sin cuentas.")

    def _instagram(self, chat_id: int, uid: int, args: list[str]) -> None:
        cuenta = (args[0].lower() if args else self._activa(uid)) or ""
        ad = self.adaptadores.get(cuenta)
        if not self.hub.cuenta(cuenta) or ad is None or getattr(ad, "anfitrion", False):
            return self._decir(chat_id, "Esa sección no tiene conexión propia con Instagram desde aquí (Campo se conecta con su propia guía).")
        self._decir(chat_id, f"{self.hub.cuenta(cuenta)['nombre'].upper()} · Instagram\n" + ad.texto_instagram())

    def _plan(self, chat_id: int, uid: int, orden: str, args: list[str]) -> None:
        if self.planes is None:
            return self._decir(chat_id, "Los planes mensuales no están activados en este arranque.")
        cuenta = self._activa(uid)
        if args and re.fullmatch(r"[a-z]\d{1,3}", args[0].lower()):
            cuenta, args = args[0].lower(), args[1:]
        explicito = bool(args and re.fullmatch(r"\d{4}-\d{2}", args[0]))
        mes = args[0] if explicito else self.planes.mes_siguiente()
        if orden == "/plan" and not explicito and self.planes.resumen(cuenta or "", mes) is None:
            en_curso = self.planes.mes_actual()
            if self.planes.resumen(cuenta or "", en_curso):
                mes = en_curso
        if not cuenta or not self.planes.tiene_perfil(cuenta):
            return self._decir(chat_id, "Esta sección no tiene perfil de estrategia todavía.")
        if orden == "/plan":
            return self._decir(chat_id, self.planes.resumen(cuenta, mes) or f"No hay plan de {mes} para {cuenta}. Escribe /plan_nuevo {mes} y lo preparo.")
        self._decir(chat_id, f"Preparando el plan de {mes} de {cuenta}. Tarda unos minutos y te aviso al acabar. Si no hay cuota de Claude, lo reintento solo más tarde.")
        self.planes.lanzar(cuenta, mes, lambda t: self._avisar_admins(t), manual=True)

    # ------------------------------------------------------------------ edición de un pie
    def _recibir_pie(self, chat_id: int, uid: int, espera, texto: str) -> None:
        cuenta, id = espera["cuenta"], espera["pieza"]
        rol = self.hub.rol_en(uid, cuenta)
        if rol not in ("admin", "aprobador") or cuenta not in self.adaptadores:
            self.hub.dejar_de_esperar(uid)
            return self._decir(chat_id, SIN_ACCESO)
        problemas = self.adaptadores[cuenta].editar_pie(id, texto, uid)
        if problemas:
            return self._decir(chat_id, "No he guardado ese pie:\n- " + "\n- ".join(problemas) + "\n\nEscribe otro o /cancelar.")
        self.hub.dejar_de_esperar(uid)
        self.hub.auditar(uid, "pie_editado", f"{cuenta}:{id}")
        self._decir(chat_id, "Pie guardado.")
        p = self.adaptadores[cuenta].pieza(id)
        if p:
            self._enviar_decision(uid, p, rol)

    # ------------------------------------------------------------------ botones
    def _callback(self, cb: dict, upd: dict) -> None:
        cb_id = cb.get("id", "")
        uid = (cb.get("from") or {}).get("id")
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        data = cb.get("data") or ""

        def negar(motivo: str = SIN_ACCESO) -> None:
            self.hub.auditar(uid, "boton_denegado", data[:64])
            self.decisiones.denegado(uid, data)
            self._responder(cb_id, motivo, True)

        if chat.get("type") != "private" or not uid or chat.get("id") != uid:                    # 1
            return negar()
        if not self.hub.activo(uid):
            return negar()
        cancelo = self._soltar_nota(uid)                                                          # cualquier otra pulsación anula la nota pedida
        if data.startswith("pn:"):                                                                # el panel: solo el administrador
            return self.panel.boton(cb_id, chat.get("id"), msg.get("message_id"), uid, data)
        if not FORMATO_BOTON.fullmatch(data):                                                     # no es del router: solo puede ser de Campo
            if not self.hub.es_admin(uid) or not self._anfitriones():
                return negar()
            self._delegar(upd)
            return
        cuenta, accion, id = data.split(":")[0], data.split(":")[1], int(data.split(":")[2])
        if accion.startswith("d"):                                                               # decisiones de José: solo el administrador
            return self.decisiones.boton(cb_id, msg.get("message_id"), msg.get("text"), uid, cuenta, accion, id)
        rol = self.hub.rol_en(uid, cuenta)                                                       # 2
        if rol is None or cuenta not in self.adaptadores:
            return negar()
        if accion in ACCIONES_MIEMBRO:
            self.hub.fijar_cuenta_activa(uid, cuenta)
            self._responder(cb_id, "Sección cambiada")
            if msg.get("message_id"):
                self.tg.editar(chat["id"], msg["message_id"], f"Sección activa: {self.hub.cuenta(cuenta)['nombre']}.")
            return
        if accion in ACCIONES_VALIDACION:                                                         # hechos del banco: solo el administrador
            if rol != "admin" or getattr(self.adaptadores[cuenta], "anfitrion", False):
                return negar("Tu rol no permite esto.")
            return self.validacion.boton(cb_id, chat.get("id"), msg.get("message_id"), uid, cuenta, accion, id)
        if accion in ACCIONES_PROGRAMAR:                                                          # elegir la hora: admin y aprobador; no Campo
            if rol not in ("admin", "aprobador") or not getattr(self.adaptadores[cuenta], "programable", False):
                return negar("Tu rol no permite esto.")
            return self.programacion.boton(cb_id, chat.get("id"), msg.get("message_id"), uid, rol, cuenta, accion, id,
                                           cancelo_hora=cancelo == "hora")
        if accion not in ACCIONES_APROBADOR or rol not in ("admin", "aprobador"):                 # 3
            return negar("Tu rol no permite esto.")
        ad = self.adaptadores[cuenta]
        if getattr(ad, "anfitrion", False):
            return negar()
        p = ad.pieza(id)                                                                          # 4
        if p is None:
            return negar("Esa pieza no existe.")
        nombre = self._nombre((cb.get("from") or {}).get("first_name") or uid)
        if accion == "pie":                                     # la sección activa no cambia: el texto siguiente se acepta en esta o en la de la pieza
            self.hub.esperar(uid, cuenta, id, "pie", origen=self._activa(uid))
            self._responder(cb_id, "Escribe el nuevo pie")
            self.tg.enviar(chat["id"], f"Escribe en un mensaje el nuevo pie de la pieza {id}. /cancelar para dejarlo como está.")
            return
        admin = rol == "admin"
        motivo = ad.aprobar(id, uid, para_admin=admin) if accion == "ok" else ad.rechazar(id, uid)   # 5
        if motivo:
            self._responder(cb_id, motivo[:190], True)
            return
        verbo = "aprobada" if accion == "ok" else "rechazada"
        self.hub.auditar(uid, f"pieza_{verbo}", f"{cuenta}:{id}")
        self._responder(cb_id, f"Pieza {verbo}")
        detalle = detalle_admin = ""
        if accion == "ok" and hasattr(ad, "resumen_decision"):
            detalle = ad.resumen_decision(id, para_admin=admin)                  # lo que ve quien aprueba
            detalle_admin = detalle if admin else ad.resumen_decision(id)        # con el aviso de salud: para el administrador
        if msg.get("message_id"):
            # una aprobada conserva «Cambiar hora» y «Quitar de la cola» (como en /ver y /cola): sin ellos había que ir a /cola
            botones = self.programacion._botones_aprobada(cuenta, id) if accion == "ok" and getattr(ad, "programable", False) else None
            self.tg.editar(chat["id"], msg["message_id"], f"Pieza {id} de {self.hub.cuenta(cuenta)['nombre']}: {verbo}.{detalle}", botones)
        self._avisar_admins(f"{nombre}: pieza {cuenta} {id} {verbo}.{detalle_admin}", excepto=uid)

    def _avisar_admins(self, texto: str, excepto: int | None = None) -> None:
        for u in self.hub.usuarios():
            if u["rol"] == "admin" and not u["revocado"] and u["user_id"] != excepto:
                try:
                    self.tg.enviar(u["user_id"], texto)
                except ErrorTelegram as exc:
                    self.log(f"aviso al administrador no enviado: {exc.metodo} {exc.codigo}")

    # ------------------------------------------------------------------ entrega de piezas
    def _enviar_pieza(self, uid: int, p, rol: str) -> None:
        c = self.hub.cuenta(p.cuenta)
        cabecera = f"{c['nombre'].upper()} · pieza {p.id} · {p.formato}"
        self.tg.enviar_archivos(uid, p.formato, p.archivos, cabecera)
        self.tg.enviar(uid, "Pie de Instagram:\n\n" + p.pie)
        self._enviar_decision(uid, p, rol)

    def _enviar_decision(self, uid: int, p, rol: str) -> None:
        if rol in ("admin", "aprobador") and p.estado == "pendiente":
            texto, botones = self.programacion.tarjeta(p, rol)     # antes de aprobar, el administrador ve el aviso de salud en la tarjeta
            self.tg.enviar(uid, texto, botones)
        elif p.estado == "pendiente":
            self.tg.enviar(uid, "Esta pieza es solo para que la veas: tu acceso es de lectura.")
        elif p.estado == "aprobada" and getattr(self.adaptadores.get(p.cuenta), "programable", False):
            texto, botones = self.programacion.vista(p, rol)       # /ver de una aprobada: su hora, y «Cambiar hora» y «Quitar» si puede
            self.tg.enviar(uid, texto, botones)

    def entregar_pendientes(self) -> int:
        enviadas = 0
        for codigo, ad in self.adaptadores.items():
            if getattr(ad, "anfitrion", False) or not self.hub.cuenta(codigo):
                continue
            nuevas = ad.por_entregar()
            if self.panel.disponible() and self.panel.cuenta() and self.panel.cuenta()[0] == codigo and nuevas:
                # con el panel: UN aviso con el panel para el administrador (no un álbum y una tarjeta por pieza); los demás miembros, como siempre
                bien_admin = self.panel.avisar_llegada(nuevas)
                for p in nuevas:
                    bien = bien_admin
                    for uid, rol in self.hub.miembros(codigo):
                        if self.hub.es_admin(uid):
                            continue
                        try:
                            self._enviar_pieza(uid, p, rol)
                            bien += 1
                        except ErrorTelegram as exc:
                            self.log(f"pieza {codigo} {p.id}: no se pudo entregar a un destinatario ({exc.metodo} {exc.codigo})")
                    if bien:
                        ad.marcar_entregada(p.id)
                        enviadas += 1
                continue
            for p in nuevas:
                bien = 0
                for uid, rol in self.hub.miembros(codigo):
                    try:
                        self._enviar_pieza(uid, p, rol)
                        bien += 1
                    except ErrorTelegram as exc:
                        self.log(f"pieza {codigo} {p.id}: no se pudo entregar a un destinatario ({exc.metodo} {exc.codigo})")
                if bien:
                    ad.marcar_entregada(p.id)
                    enviadas += 1
        return enviadas

    # ------------------------------------------------------------------ tareas de fondo
    def vuelta_de_fondo(self) -> None:
        """Una vuelta de las tareas de fondo (cada 60 s): el publicador de cada cuenta de Nutrición, sus avisos de publicación y el
        plan mensual. Los avisos de publicación se entregan después del publicador, para que salgan en la misma vuelta."""
        for codigo, ad in self.adaptadores.items():
            if hasattr(ad, "ciclo_publicador") and not getattr(ad, "anfitrion", False):
                try:
                    for texto in ad.ciclo_publicador():
                        self._avisar_admins(texto)
                except Exception as exc:
                    self.log(f"publicador {codigo}: {type(exc).__name__}")
                try:
                    self._entregar_avisos_publicacion(ad)
                except Exception as exc:
                    self.log(f"avisos de publicación {codigo}: {type(exc).__name__}")
        if self.planes is not None:
            try:
                self.planes.revisar(lambda t: self._avisar_admins(t))
            except Exception as exc:
                self.log(f"planes: {type(exc).__name__}")
        if self.panel.activo:
            try:
                self.panel.vuelta()
            except Exception as exc:
                self.log(f"panel: {type(exc).__name__}")

    def _administradores(self) -> list[int]:
        return [u["user_id"] for u in self.hub.usuarios() if u["rol"] == "admin" and not u["revocado"]]

    def _enviar_a_admins(self, texto: str, botones=None) -> None:
        """Lo manda a cada administrador (con sus botones, si los lleva). Un fallo de Telegram sale (ErrorTelegram): quien llama decide si
        el aviso queda pendiente."""
        for uid in self._administradores():
            self.tg.enviar(uid, texto, botones)

    def _entregar_avisos_publicacion(self, ad) -> None:
        """Los avisos de publicación de una cuenta (tabla `avisos_publicacion` de su base): cada uno se marca como enviado solo cuando
        Telegram lo confirma. Un fallo que puede pasar (red, Telegram caído) deja el aviso pendiente con espera creciente y para la
        tanda, para conservar el orden; un error permanente de Telegram lo da por fallido y sigue con el siguiente. Si no hay
        administrador, nada se da por enviado: lo guardado sale cuando lo haya."""
        if not hasattr(ad, "avisos_publicacion_pendientes") or not self._administradores():
            return
        for a in ad.avisos_publicacion_pendientes():
            try:
                self._enviar_a_admins(a["texto"], _botones_de(a["botones"]))
            except ErrorTelegram as exc:
                permanente = exc.codigo in ERRORES_PERMANENTES
                ad.aviso_publicacion_fallido(a["id"], f"{exc.metodo} {exc.codigo}", definitivo=permanente)
                self.log(f"aviso de publicación {a['id']}: no se pudo enviar ({exc.metodo} {exc.codigo})")
                if not permanente:
                    return
                continue
            ad.aviso_publicacion_enviado(a["id"])

    def iniciar_fondo(self, detener: threading.Event) -> None:
        """Pone en marcha lo que las cuentas alojadas y las de Nutrición necesitan hacer solas: el publicador de Campo, el de Nutrición y el plan mensual."""
        for ad in self.adaptadores.values():
            if hasattr(ad, "iniciar"):
                ad.iniciar(detener)

        def ciclo():
            while not detener.wait(60):
                self.vuelta_de_fondo()

        h = threading.Thread(target=ciclo, name="router-fondo", daemon=True)
        h.start()
        self._hilos.append(h)

    def parar_fondo(self) -> None:
        for ad in self.adaptadores.values():
            if hasattr(ad, "parar"):
                ad.parar()

    # ------------------------------------------------------------------ bucle
    def ejecutar(self, parar=lambda: False, espera_error: float = 5.0, detener: threading.Event | None = None) -> None:
        detener = detener or threading.Event()
        self.iniciar_fondo(detener)
        try:
            while not parar() and not detener.is_set():
                try:
                    self.decisiones.latido()                # señal de vida antes del sondeo largo: lo miran preguntar y respuesta
                    for upd in self.tg.actualizaciones(self.hub.ultimo_update() + 1, 25):
                        try:
                            self.procesar(upd)
                        except Exception as exc:                      # una actualización mala no tumba el router
                            self.log(f"actualización {upd.get('update_id')}: {type(exc).__name__}")
                        self.hub.guardar_update(upd["update_id"])
                    try:
                        self.entregar_pendientes()
                    except ErrorTelegram:
                        raise
                    except Exception as exc:                          # una pieza rota no tumba el router ni a Campo, que vive dentro de él
                        self.log(f"entrega de piezas: {type(exc).__name__}")
                        time.sleep(espera_error)
                    try:
                        self.decisiones.vuelta()
                    except Exception as exc:                          # una decisión rota no tumba el router
                        self.log(f"decisiones: {type(exc).__name__}")
                        time.sleep(espera_error)
                    try:
                        self.avisos.vuelta()
                    except Exception as exc:                          # un aviso roto no tumba el router
                        self.log(f"avisos: {type(exc).__name__}")
                        time.sleep(espera_error)
                except ErrorTelegram as exc:
                    self.log(f"telegram: {exc.metodo} {exc.codigo}")
                    time.sleep(30 if exc.codigo == 409 else espera_error)
        finally:
            detener.set()
            self.parar_fondo()
