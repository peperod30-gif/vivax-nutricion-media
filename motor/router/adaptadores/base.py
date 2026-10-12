"""Contrato que cumple cada cuenta (Nutrición, Campo, un cliente) para colgarse del router.

El router no sabe nada de imágenes, de bases de datos de piezas ni de cómo se publica: solo habla con esto. Las piezas se
numeran por cuenta, así que la pieza 17 de Nutrición y la 17 de Campo son distintas; el router siempre las pide con la cuenta.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

ESTADOS = ("pendiente", "aprobada", "rechazada")


@dataclass
class Pieza:
    id: int
    cuenta: str
    titulo: str
    formato: str                    # carrusel | unica | video
    estado: str                     # pendiente | aprobada | rechazada
    pie: str
    archivos: list[Path] = field(default_factory=list)
    creada: str = ""
    entregada: bool = False


class Adaptador(Protocol):
    cuenta: str

    def por_entregar(self) -> list[Pieza]: ...
    def marcar_entregada(self, id: int) -> None: ...
    def pieza(self, id: int) -> Pieza | None: ...
    def listar(self, limite: int = 10) -> list[Pieza]: ...
    def aprobar(self, id: int, por: int) -> str | None: ...                  # None si ha ido bien; si no, el motivo
    def rechazar(self, id: int, por: int) -> str | None: ...
    def editar_pie(self, id: int, texto: str, por: int) -> list[str]: ...    # lista de problemas; vacía si se aceptó
