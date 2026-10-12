"""Validación de los hechos del banco desde Telegram. Solo para el administrador, en chat privado y en cuentas de Nutrición.

    /validar [cuenta] [id]    el siguiente hecho pendiente, o el hecho que se pide (MIT-007, mit-007...)
    /validados [cuenta]       resumen: validados, con reparos, no usar, pendientes y obsoletos por cambio

Botones de cada hecho: ✅ Validar · ✏️ Con reparos (pide una nota) · ⛔ No usar · ⏭ Luego, y ↩️ Retirar si ya hay una decisión.

El dato de cada botón es «cuenta:acción:número». El número identifica una versión de la tarjeta (validaciones.version: el hecho y
la decisión que enseña; tabla validacion_numeros del hub). Cada pulsación vuelve a comprobarlo todo: el número es de esa cuenta,
la versión sigue siendo la actual y quien pulsa es el administrador. Si la tarjeta es vieja, no se decide nada: se enseña la actual.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import controles as C
import validaciones as V
from .telegram_io import ErrorTelegram

ACCIONES_VALIDACION = {"validar", "reparos", "nousar", "luego", "retirar"}
MADRID = ZoneInfo("Europe/Madrid")
NO_ACCESO = "No tienes acceso a eso."
VEREDICTO = {"respaldado": "respaldado", "matizado": "matizado", "no_respaldado": "no respaldado", "falso": "falso",
             "cita": "cita de un participante", "ejemplo": "ejemplo"}
PREVIA = {"validado": "validado", "con_reparos": "con reparos", "no_usar": "no usar"}
DECIDIDO = {"validar": ("validado", "Ya estaba validado."), "nousar": ("no_usar", "Ya estaba marcado como no usar.")}   # doble pulsación
LIMITE_MENSAJE = 3900          # Telegram admite 4096; el margen evita que el envío recorte algo


def _plano(valor) -> str:
    """Un valor anidado de la ficha (dict o lista) como texto legible: «fecha: …; vía: …»."""
    if isinstance(valor, dict):
        return "; ".join(f"{k}: {_plano(x)}" for k, x in valor.items() if x)
    if isinstance(valor, (list, tuple)):
        return "; ".join(_plano(x) for x in valor if x)
    return str(valor)


def _partir(texto: str, limite: int = LIMITE_MENSAJE) -> list[str]:
    """Trozos de como mucho `limite` caracteres, cortados entre líneas. Una tarjeta larga se envía entera, en varios mensajes."""
    trozos: list[str] = []
    actual: list[str] = []
    largo = 0
    for linea in texto.split("\n"):
        for fragmento in [linea[i:i + limite] for i in range(0, max(len(linea), 1), limite)]:
            if actual and largo + len(fragmento) + 1 > limite:
                trozos.append("\n".join(actual))
                actual, largo = [], 0
            actual.append(fragmento)
            largo += len(fragmento) + 1
    trozos.append("\n".join(actual))
    return trozos


class Validacion:
    def __init__(self, router):
        self.r = router

    # ------------------------------------------------------------------ cuentas, banco y textos
    def _nutricion(self) -> list[str]:
        return [c for c, a in self.r.adaptadores.items() if not getattr(a, "anfitrion", False) and self.r.hub.cuenta(c)]

    def _elegir(self, uid: int, pedida: str | None) -> str | None:
        nut = self._nutricion()
        if pedida:
            return pedida if pedida in nut else None
        activa = self.r._activa(uid)
        if activa in nut:
            return activa
        return nut[0] if len(nut) == 1 else None

    def _en_uso(self, cuenta: str) -> set[str]:
        fn = getattr(self.r.adaptadores.get(cuenta), "hechos_en_uso", None)
        return fn() if fn else set()

    def _hoy(self) -> date:
        return datetime.fromtimestamp(self.r.ahora(), MADRID).date()

    def _nombre(self, cuenta: str) -> str:
        return self.r.hub.cuenta(cuenta)["nombre"]

    def _estado(self, h: dict) -> str:
        v, fecha = h.get("validacion"), h.get("validacion_fecha", "")
        if v == "obsoleta":
            previa = PREVIA.get(h.get("validacion_previa"), "")
            motivo = h.get("validacion_motivo", "")
            return ("Estado: pendiente. " + motivo[:1].upper() + motivo[1:] + "."
                    + (f" Antes estaba {previa}." if previa else ""))
        if v == "validado" and h.get("vigente"):
            pendiente = "pendiente" in str(h.get("estado_validacion", "")).lower()
            return f"Estado: validado el {fecha}." + (" Falta lo que exige otro profesional: sigue pendiente." if pendiente else "")
        if v == "con_reparos" and h.get("vigente"):
            return f"Estado: con reparos ({fecha}): «{h.get('validacion_nota', '')}». Sigue pendiente."
        if v == "no_usar" and h.get("vigente"):
            return f"Estado: NO USAR ({fecha}). Ninguna pieza puede citarlo."
        if h.get("no_usar"):
            return "Estado: NO USAR, hasta que lo revises de nuevo."
        return "Estado: pendiente de revisar."

    def _texto(self, cuenta: str, sup: dict, hid: str, en_uso: set[str], cabecera: str | None) -> str:
        """La tarjeta entera: todo lo que forma la huella del hecho (y la decisión) se enseña, sin cortes."""
        h = sup[hid]
        pendientes = len(V.cola(sup, en_uso))
        poblaciones = h.get("poblaciones_en_riesgo") or []
        lineas = [cabecera, "", f"{self._nombre(cuenta)} · {pendientes} pendientes", "", f"[{hid}] {h.get('tema', '')}"]
        if h.get("enunciado"):
            lineas.append(f"«{h['enunciado']}»")
        lineas += [f"Veredicto: {VEREDICTO.get(h.get('veredicto'), h.get('veredicto') or '—')}",
                   f"Límite: {h.get('matiz') or '—'}",
                   f"Tipo de evidencia: {h.get('nivel_evidencia') or '—'}",
                   f"Riesgo: {h.get('riesgo_publicar') or '?'}" + (" · lo usan piezas en cola o aprobadas" if hid in en_uso else ""),
                   "Poblaciones en riesgo: " + ("; ".join(poblaciones) if poblaciones else "ninguna anotada"),
                   f"Fuente: {h.get('fuente') or '—'}",
                   f"Año: {h.get('anio') or '—'}",
                   f"Enlace: {h.get('url') or '—'}"]
        v = h.get("verificacion_primaria") or {}
        lineas += [f"Verificación: {v.get('detalle') or '—'}",
                   f"Fecha de la verificación: {v.get('fecha') or '—'} · Vía: {v.get('via') or '—'}"]
        for clave, valor in v.items():                              # todo lo que forma la huella se ve: p. ej. el verificador independiente
            if clave not in ("detalle", "fecha", "via") and valor:
                lineas.append(f"{clave.replace('_', ' ').capitalize()}: {_plano(valor)}")
        lineas += ["", self._estado(h)]
        return "\n".join(x for x in lineas if x is not None).strip()

    def _botones(self, cuenta: str, hid: str, h: dict) -> list:
        num = self.r.hub.numero_validacion(cuenta, hid, V.version(h))
        d = lambda accion: f"{cuenta}:{accion}:{num}"           # noqa: E731
        filas = [[("✅ Validar", d("validar")), ("✏️ Con reparos", d("reparos"))],
                 [("⛔ No usar", d("nousar")), ("⏭ Luego", d("luego"))]]
        if h.get("validacion") is not None:
            filas.append([("↩️ Retirar validación", d("retirar"))])
        return filas

    def _mostrar(self, chat_id: int, mensaje_id: int | None, cuenta: str, sup: dict, hid: str, cabecera: str | None = None) -> None:
        h = sup[hid]
        texto = self._texto(cuenta, sup, hid, self._en_uso(cuenta), cabecera)
        botones = self._botones(cuenta, hid, h)
        trozos = _partir(texto)
        if len(trozos) == 1 and mensaje_id is not None:
            self._editar(chat_id, mensaje_id, texto, botones)
            return
        for trozo in trozos[:-1]:                                  # si la tarjeta no cabe en un mensaje, los botones van al final
            self.r._decir(chat_id, trozo)
        self.r._decir(chat_id, trozos[-1], botones)

    def _editar(self, chat_id: int, mensaje_id: int, texto: str, botones=None) -> None:
        try:
            self.r.tg.editar(chat_id, mensaje_id, texto, botones)
        except ErrorTelegram as exc:
            self.r.log(f"validación: no se pudo editar el mensaje ({exc.metodo} {exc.codigo})")

    def _resumen(self, cuenta: str, sup: dict) -> str:
        c = V.resumen(sup, self._en_uso(cuenta))
        return "\n".join([f"{self._nombre(cuenta)}: validación de hechos",
                          f"Validados: {c['validados']}",
                          f"Con reparos: {c['con_reparos']}",
                          f"No usar: {c['no_usar']}",
                          f"Pendientes: {c['pendientes']}",
                          f"Obsoletos (cambiaron desde que los validaste): {c['obsoletos']}",
                          f"Validados, pero con otra exigencia además del dietista (p. ej. abogado): {c['con_mas_requisitos']}",
                          "",
                          "Para quitar una validación: /validar <id> y pulsa ↩️ Retirar validación."])

    def _pintar(self, chat_id: int, mensaje_id: int | None, cuenta: str, cabecera: str | None = None) -> None:
        """Enseña el siguiente hecho pendiente (editando el mensaje si se puede) o el resumen si no queda ninguno."""
        sup = C.cargar_banco(cuenta)
        cola = V.cola(sup, self._en_uso(cuenta))
        if cola:
            return self._mostrar(chat_id, mensaje_id, cuenta, sup, cola[0], cabecera)
        texto = (cabecera + "\n\n" if cabecera else "") + "No quedan hechos pendientes.\n\n" + self._resumen(cuenta, sup)
        if mensaje_id is not None:
            self._editar(chat_id, mensaje_id, texto, None)
        else:
            self.r._decir(chat_id, texto)

    def _guardar(self, cuenta: str, h: dict, estado: str, uid: int, nota: str = "") -> bool:
        """Guarda la decisión. False si ya estaba tal cual (doble pulsación): entonces no se audita ni se cambia nada."""
        previo = V.leer(cuenta).get(h["id"])
        if isinstance(previo, dict) and previo.get("estado") == estado and previo.get("huella") == V.huella(h) \
                and previo.get("nota", "") == nota:
            return False
        V.registrar(cuenta, h, estado, uid, self._hoy(), nota=nota, esperada=V.huella(h))
        self.r.hub.auditar(uid, "validacion", f"{cuenta}:{h['id']}:{estado}")
        return True

    # ------------------------------------------------------------------ órdenes /validar y /validados
    def orden(self, chat_id: int, uid: int, orden: str, args: list[str]) -> None:
        resto = list(args)
        pedida = None
        if resto and resto[0].lower() in self._nutricion():
            pedida = resto.pop(0).lower()
        cuenta = self._elegir(uid, pedida)
        if cuenta is None:
            nut = self._nutricion()
            if pedida:
                return self.r._decir(chat_id, "Esa cuenta no es de Nutrición.")
            if not nut:
                return self.r._decir(chat_id, "No hay ninguna cuenta de Nutrición con hechos que validar.")
            return self.r._decir(chat_id, "Esta sección no usa el banco de hechos. Dime de qué cuenta: "
                                 + " o ".join(f"{orden} {c}" for c in nut))
        try:
            sup = C.cargar_banco(cuenta)
        except V.ErrorValidaciones as exc:
            return self.r._decir(chat_id, f"{exc}. No he cambiado nada.")
        if orden == "/validados":
            return self.r._decir(chat_id, self._resumen(cuenta, sup))
        if resto:
            hid = resto[0].upper()
            if hid not in sup:
                return self.r._decir(chat_id, f"No conozco el hecho {hid} en {self._nombre(cuenta)}.")
            return self._mostrar(chat_id, None, cuenta, sup, hid)
        cola = V.cola(sup, self._en_uso(cuenta))
        if cola:
            return self._mostrar(chat_id, None, cuenta, sup, cola[0])
        self.r._decir(chat_id, "No quedan hechos pendientes.\n\n" + self._resumen(cuenta, sup))

    # ------------------------------------------------------------------ botones
    def boton(self, cb_id: str, chat_id: int, mensaje_id: int | None, uid: int, cuenta_boton: str, accion: str, num: int) -> None:
        """Llega solo con el rol de administrador ya comprobado por el router. Aquí se comprueba lo que el botón dice: el número
        es de esa cuenta y es la versión de la tarjeta que sigue vigente (el hecho y la decisión que enseña)."""
        hub = self.r.hub
        fila = hub.validacion_numero(num)
        if fila is None or fila["cuenta"] != cuenta_boton or cuenta_boton not in self._nutricion():
            return self.r._responder(cb_id, "Ese botón ya no vale. Vuelve a /validar.", True)
        cuenta, hid, version_vista = fila["cuenta"], fila["hecho"], fila["huella"]
        try:
            sup = C.cargar_banco(cuenta)
            h = sup.get(hid)
            if h is None:
                return self.r._responder(cb_id, "Ese hecho ya no está en el banco.", True)
            if accion == "retirar" and h.get("validacion") is None:
                return self.r._responder(cb_id, "No había ninguna decisión que quitar.")
            if accion in DECIDIDO and h.get("validacion") == DECIDIDO[accion][0]:       # doble pulsación: ya está como pedía el botón
                return self.r._responder(cb_id, DECIDIDO[accion][1])
            if V.version(h) != version_vista:              # tarjeta vieja: el hecho o la decisión han cambiado; no se decide sobre eso
                self._mostrar(chat_id, mensaje_id, cuenta, sup, hid,
                              "⚠ Esta tarjeta ya no vale: el hecho o tu decisión cambió. Esta es la versión actual; no he guardado nada.")
                return self.r._responder(cb_id, "Esa tarjeta ya no vale: el hecho o la decisión cambió. Te enseño la actual; no he guardado nada.", True)
            if accion == "reparos":
                hub.esperar(uid, cuenta, num, "reparos")               # el texto siguiente es la nota; `num` lleva la versión
                hub.fijar_cuenta_activa(uid, cuenta)
                self.r._responder(cb_id, "Escribe la nota")
                peticion = self.r._decir(chat_id, f"Escribe en un mensaje la nota de reparos para {hid}: qué no cuadra o qué falta "
                                                  "(500 caracteres como máximo). /cancelar para dejarlo.")
                hub.fijar_peticion(uid, peticion)                     # solo una respuesta a este mensaje es la nota
                return None
            if accion == "luego":
                cola = V.cola(sup, self._en_uso(cuenta))
                if hid not in cola:
                    return self.r._responder(cb_id, "Ya no está pendiente.")
                siguiente = cola[(cola.index(hid) + 1) % len(cola)]
                self._mostrar(chat_id, mensaje_id, cuenta, sup, siguiente)
                return self.r._responder(cb_id, "Lo dejo para luego")
            if accion == "retirar":
                if not V.retirar(cuenta, hid, uid):
                    return self.r._responder(cb_id, "No había ninguna decisión que quitar.")
                hub.auditar(uid, "validacion_retirada", f"{cuenta}:{hid}")
                self._mostrar(chat_id, mensaje_id, cuenta, C.cargar_banco(cuenta), hid, f"↩️ Validación de {hid} quitada.")
                return self.r._responder(cb_id, "Validación quitada")
            if accion == "validar":
                if not self._guardar(cuenta, h, "validado", uid):
                    return self.r._responder(cb_id, "Ya estaba validado.")
                self._pintar(chat_id, mensaje_id, cuenta, f"✅ {hid} validado.")
                return self.r._responder(cb_id, f"{hid} validado")
            if accion == "nousar":
                if not self._guardar(cuenta, h, "no_usar", uid):
                    return self.r._responder(cb_id, "Ya estaba marcado como no usar.")
                self._pintar(chat_id, mensaje_id, cuenta, f"⛔ {hid}: no usar. Ninguna pieza lo puede citar.")
                return self.r._responder(cb_id, f"{hid}: no usar")
        except V.HechoCambiado:
            return self.r._responder(cb_id, "El hecho cambió mientras lo revisabas. No he guardado nada; vuelve a /validar.", True)
        except V.ErrorValidaciones as exc:
            return self.r._responder(cb_id, str(exc)[:190], True)
        return self.r._responder(cb_id, "Ese botón no hace nada.", True)

    # ------------------------------------------------------------------ nota de reparos (texto suelto tras pulsar ✏️)
    def recibir_nota(self, chat_id: int, uid: int, espera, texto: str) -> None:
        hub = self.r.hub
        cuenta, num = espera["cuenta"], int(espera["pieza"])
        fila = hub.validacion_numero(num)
        if fila is None or fila["cuenta"] != cuenta or cuenta not in self._nutricion():
            hub.dejar_de_esperar(uid)
            return self.r._decir(chat_id, "Esa decisión ya no vale. Vuelve a /validar.")
        try:
            sup = C.cargar_banco(cuenta)
        except V.ErrorValidaciones as exc:
            hub.dejar_de_esperar(uid)
            return self.r._decir(chat_id, f"{exc}. No he guardado la nota.")
        hid = fila["hecho"]
        h = sup.get(hid)
        if h is None or V.version(h) != fila["huella"]:                # el hecho o su decisión han cambiado mientras se escribía
            hub.dejar_de_esperar(uid)
            return self.r._decir(chat_id, f"{hid} ha cambiado mientras escribías la nota. No he guardado nada: vuelve a /validar.")
        try:
            nota = V.limpiar_nota(texto)
        except ValueError as exc:
            return self.r._decir(chat_id, f"No he guardado la nota: {exc}. Escríbela más corta o pulsa /cancelar.")
        try:
            V.registrar(cuenta, h, "con_reparos", uid, self._hoy(), nota=nota, esperada=V.huella(h))
        except (V.HechoCambiado, V.ErrorValidaciones, ValueError) as exc:
            hub.dejar_de_esperar(uid)
            return self.r._decir(chat_id, f"No he guardado la nota: {exc}.")
        hub.dejar_de_esperar(uid)
        hub.auditar(uid, "validacion", f"{cuenta}:{hid}:con_reparos")
        self.r._decir(chat_id, f"Guardado: {hid} con reparos.")
        try:
            self._pintar(chat_id, None, cuenta)
        except V.ErrorValidaciones as exc:                      # la nota ya está guardada; solo falla enseñar el siguiente
            self.r._decir(chat_id, f"{exc}. Lo demás está guardado; vuelve a /validar cuando lo arregles.")
