"""Copia al clon del repositorio (nube_repo) el código mínimo que necesita el router en la nube, con la misma disposición de carpetas que
el motor local, y no sube nada: solo copia. Quien sube es `nube_enviar.py --codigo`.

El conjunto sale de una prueba de importación en un venv limpio (Python 3.11 con requests, pillow, numpy y tzdata): el router
(`router/`), los módulos que importan los controles de calidad (controles, plantillas*, temas, validaciones, personajes), los JSON de datos que
leen esos controles y este paquete (`nube/`). No lleva: main.py (arrastra el plan mensual), Campo, la producción, el render, las fuentes, las
fotos, la base real, el .env ni nada de router_datos.

Uso:  python -X utf8 nube/sincronizar_codigo.py [--clon <carpeta>]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

MOTOR = Path(__file__).resolve().parents[1]
CLON = MOTOR.parent / "nube_repo"
MODULOS = ("controles.py", "plantillas.py", "plantillas_d.py", "plantillas_v.py", "plantillas_f.py", "plantillas_g.py", "temas.py", "validaciones.py", "personajes.py",
           "reel_foto.py")      # reel_foto: los controles de los reels de fotos (solo la biblioteca estándar al cargarse; lo pesado está dentro de las funciones de dibujo)
ROUTER = ("__init__.py", "bd.py", "hub.py", "instagram.py", "nucleo.py", "panel.py", "textos_panel.py", "telegram_io.py", "validacion.py",
          "adaptadores/__init__.py", "adaptadores/base.py", "adaptadores/nutricion.py")
NUBE = ("__init__.py", "estado.py", "ordenes.py", "ejecutar_nube.py", "sincronizar_codigo.py", "requirements.txt", "cuentas.nube.json", "LEEME.md", "UX.md")
PROHIBIDOS = (".env", ".sqlite", ".pem", "router_datos")


def datos_de_controles() -> list[str]:
    """Los JSON de datos que lee controles.py (se sacan de su propio texto, para no quedarse atrás cuando se añade un banco)."""
    t = (MOTOR / "controles.py").read_text(encoding="utf-8")
    return sorted(set(re.findall(r'RAIZ / "datos" / "([^"]+\.json)"', t)))


def lista_de_ficheros() -> list[str]:
    f = [m for m in MODULOS]
    f += [f"router/{r}" for r in ROUTER]
    f += [f"nube/{n}" for n in NUBE]
    f += [f"datos/{d}" for d in datos_de_controles()]
    return f


def sincronizar(clon: Path) -> list[str]:
    clon = Path(clon)
    if not (clon / ".git").exists():
        raise SystemExit(f"{clon} no es un clon de git.")
    copiados = []
    for rel in lista_de_ficheros():
        origen = MOTOR / rel
        if any(p in rel for p in PROHIBIDOS):
            raise SystemExit(f"Lista de ficheros con algo prohibido: {rel}")
        if not origen.is_file():
            if rel.endswith(("LEEME.md", "UX.md")):
                continue
            raise SystemExit(f"Falta {origen}")
        destino = clon / "motor" / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        if not destino.is_file() or destino.read_bytes() != origen.read_bytes():
            shutil.copyfile(origen, destino)
            copiados.append(rel)
    for carpeta in ("piezas/nube", "salida", "ordenes"):
        (clon / "motor" / carpeta).mkdir(parents=True, exist_ok=True)
        marca = clon / "motor" / carpeta / ".gitkeep"
        if not marca.exists():
            marca.write_text("", encoding="utf-8")
    flujo = MOTOR / "nube" / "router.yml"
    if flujo.is_file():
        d = clon / ".github" / "workflows" / "router.yml"
        d.parent.mkdir(parents=True, exist_ok=True)
        if not d.is_file() or d.read_bytes() != flujo.read_bytes():
            shutil.copyfile(flujo, d)
            copiados.append(".github/workflows/router.yml")
    return copiados


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clon", type=Path, default=CLON)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    c = sincronizar(a.clon)
    print(f"{len(c)} ficheros copiados al clon." + ("".join(f"\n  {x}" for x in c) if c else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
