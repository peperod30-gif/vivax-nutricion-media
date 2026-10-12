"""Cliente mínimo de la API de Telegram para el router. Un solo sondeo largo (getUpdates) por bot.

Los errores nunca llevan la URL (lleva el token). El token se pasa al construir y no se guarda en ningún otro sitio.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{metodo}"


class ErrorTelegram(Exception):
    def __init__(self, metodo: str, codigo: int | None, descripcion: str):
        super().__init__(f"{metodo}: {codigo} {descripcion}")
        self.metodo, self.codigo, self.descripcion = metodo, codigo, descripcion


class TelegramReal:
    def __init__(self, token: str):
        self._url = API.replace("{token}", token)

    def _llamar(self, metodo: str, timeout: int = 60, **kw) -> dict | list | bool:
        for intento in range(1, 4):
            try:
                r = requests.post(self._url.replace("{metodo}", metodo), timeout=timeout, **kw)
            except requests.RequestException as exc:
                raise ErrorTelegram(metodo, None, f"fallo de red ({type(exc).__name__})") from None
            try:
                d = r.json()
            except ValueError:
                raise ErrorTelegram(metodo, r.status_code, "respuesta no válida") from None
            if d.get("ok"):
                return d["result"]
            if r.status_code == 429 and intento < 3:
                time.sleep(int((d.get("parameters") or {}).get("retry_after", 5)) + 1)
                continue
            raise ErrorTelegram(metodo, d.get("error_code"), str(d.get("description")))
        raise ErrorTelegram(metodo, 429, "demasiados reintentos")

    @staticmethod
    def _boton(texto: str, dato: str) -> dict:
        """Un botón de callback; si el dato es un enlace (http o https), un botón que abre ese enlace."""
        if str(dato).startswith(("http://", "https://")):
            return {"text": texto, "url": dato}
        return {"text": texto, "callback_data": dato}

    @classmethod
    def _teclado(cls, botones) -> str | None:
        if not botones:
            return None
        return json.dumps({"inline_keyboard": [[cls._boton(t, d) for t, d in fila] for fila in botones]})

    def actualizaciones(self, offset: int, timeout: int = 25) -> list[dict]:
        return self._llamar("getUpdates", timeout=timeout + 15, data={"offset": offset, "timeout": timeout,
                                                                        "allowed_updates": json.dumps(["message", "callback_query"])})

    def poner_menu(self, comandos: list[tuple[str, str]]) -> None:
        self._llamar("setMyCommands", timeout=20, data={"commands": json.dumps([{"command": c, "description": d} for c, d in comandos])})

    def yo(self) -> dict:
        return self._llamar("getMe", timeout=20)

    def enviar(self, chat_id: int, texto: str, botones=None) -> int:
        datos = {"chat_id": chat_id, "text": texto[:4000], "disable_web_page_preview": "true"}
        t = self._teclado(botones)
        if t:
            datos["reply_markup"] = t
        return self._llamar("sendMessage", data=datos)["message_id"]

    def editar(self, chat_id: int, message_id: int, texto: str, botones=None) -> None:
        datos = {"chat_id": chat_id, "message_id": message_id, "text": texto[:4000]}
        t = self._teclado(botones)
        if t:
            datos["reply_markup"] = t
        try:
            self._llamar("editMessageText", data=datos)
        except ErrorTelegram as exc:
            if "not modified" not in exc.descripcion:
                raise

    # ------------------------------------------------------------------ fotos (el panel enseña la portada de cada pieza)
    def enviar_foto(self, chat_id: int, ruta, caption: str, botones=None) -> int:
        """Una foto con su texto (hasta 1024 caracteres) y sus botones. Devuelve el número del mensaje."""
        datos = {"chat_id": chat_id, "caption": caption[:1000]}
        t = self._teclado(botones)
        if t:
            datos["reply_markup"] = t
        with open(ruta, "rb") as f:
            return self._llamar("sendPhoto", data=datos, files={"photo": (Path(ruta).name, f, "image/jpeg")})["message_id"]

    def editar_foto(self, chat_id: int, message_id: int, ruta, caption: str, botones=None) -> None:
        """Cambia la foto, el texto y los botones de un mensaje que ya es una foto (editMessageMedia con el fichero adjunto)."""
        media = {"type": "photo", "media": "attach://foto", "caption": caption[:1000]}
        datos = {"chat_id": chat_id, "message_id": message_id, "media": json.dumps(media)}
        t = self._teclado(botones)
        if t:
            datos["reply_markup"] = t
        with open(ruta, "rb") as f:
            self._llamar("editMessageMedia", data=datos, files={"foto": (Path(ruta).name, f, "image/jpeg")})

    def editar_leyenda(self, chat_id: int, message_id: int, caption: str, botones=None) -> None:
        """Cambia solo el texto y los botones de un mensaje que es una foto (la foto se queda)."""
        datos = {"chat_id": chat_id, "message_id": message_id, "caption": caption[:1000]}
        t = self._teclado(botones)
        datos["reply_markup"] = t if t else json.dumps({"inline_keyboard": []})
        try:
            self._llamar("editMessageCaption", data=datos)
        except ErrorTelegram as exc:
            if "not modified" not in exc.descripcion:
                raise

    def borrar(self, chat_id: int, message_id: int) -> None:
        """Borra un mensaje (Telegram solo deja borrar los de menos de 48 horas; si no se puede, se ignora)."""
        try:
            self._llamar("deleteMessage", timeout=20, data={"chat_id": chat_id, "message_id": message_id})
        except ErrorTelegram as exc:
            if exc.codigo not in (400, 403):
                raise

    def responder_callback(self, cb_id: str, texto: str = "", alerta: bool = False) -> None:
        self._llamar("answerCallbackQuery", timeout=20, data={"callback_query_id": cb_id, "text": texto[:190], "show_alert": "true" if alerta else "false"})

    def enviar_archivos(self, chat_id: int, formato: str, rutas, caption: str) -> None:
        if not rutas:
            raise ErrorTelegram("enviar_archivos", None, "la pieza no tiene ficheros que enviar")
        abiertos = []
        try:
            if formato == "video":
                f = open(rutas[0], "rb")
                abiertos.append(f)
                self._llamar("sendVideo", data={"chat_id": chat_id, "caption": caption, "supports_streaming": "true"}, files={"video": (rutas[0].name, f, "video/mp4")})
            elif len(rutas) == 1:
                f = open(rutas[0], "rb")
                abiertos.append(f)
                self._llamar("sendPhoto", data={"chat_id": chat_id, "caption": caption}, files={"photo": (rutas[0].name, f, "image/jpeg")})
            else:
                media, ficheros = [], {}
                for i, r in enumerate(rutas[:10]):
                    f = open(r, "rb")
                    abiertos.append(f)
                    ficheros[f"h{i}"] = (r.name, f, "image/jpeg")
                    m = {"type": "photo", "media": f"attach://h{i}"}
                    if i == 0:
                        m["caption"] = caption
                    media.append(m)
                self._llamar("sendMediaGroup", data={"chat_id": chat_id, "media": json.dumps(media)}, files=ficheros)
        finally:
            for f in abiertos:
                f.close()
