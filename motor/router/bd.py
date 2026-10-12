"""SQLite compartida entre los hilos del router (el que lee Telegram, el publicador, el plan mensual).

Una sola conexión y un cerrojo en cada operación: sqlite3 no deja usar una conexión desde otro hilo y, aunque se le deje, dos hilos
a la vez pueden pisarse a mitad de una consulta. El cerrojo es reentrante.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

# Si otro hilo tiene una escritura a medias (ejecutada y sin confirmar), la transacción espera a que la confirme: unos 6 s en total.
ESPERAS_TRANSACCION = (0.005, 0.01, 0.02, 0.04) + (0.1,) * 60


class Conexion:
    def __init__(self, ruta: Path | str):
        self._c = sqlite3.connect(str(ruta), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._cerrojo = threading.RLock()

    def execute(self, *args):
        with self._cerrojo:
            return self._c.execute(*args)

    def executescript(self, script: str):
        with self._cerrojo:
            return self._c.executescript(script)

    def commit(self) -> None:
        with self._cerrojo:
            self._c.commit()

    def rollback(self) -> None:
        with self._cerrojo:
            self._c.rollback()

    @contextmanager
    def transaccion(self):
        """Varias escrituras que se hacen todas o ninguna. BEGIN IMMEDIATE toma el cerrojo de escritura de SQLite desde el principio:
        así dos procesos (el router y la línea de órdenes, o la producción) no pueden leer lo mismo y escribir los dos.
        Si otro hilo tiene una escritura sin confirmar (`execute` hecho y `commit` pendiente, porque el cerrojo se suelta entre
        ambos), se espera a que la confirme: no se confirma su escritura ni se abre una transacción dentro de la suya."""
        for espera in ESPERAS_TRANSACCION:
            with self._cerrojo:
                if not self._c.in_transaction:
                    self._c.execute("BEGIN IMMEDIATE")
                    try:
                        yield self._c
                    except BaseException:
                        self._c.rollback()
                        raise
                    else:
                        self._c.commit()
                    return
            time.sleep(espera)                  # el cerrojo se suelta aquí para que el otro hilo pueda confirmar
        raise sqlite3.OperationalError("la base tiene una escritura sin confirmar de otro hilo; se vuelve a intentar")

    def cerrar(self) -> None:
        with self._cerrojo:
            self._c.close()
