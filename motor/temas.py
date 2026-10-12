"""Temas: la parte de la imagen que cambia de una cuenta a otra (colores, tipografías, personajes).

Vivax Nutrición tiene el suyo. Cada cliente puede traer el suyo: una carpeta `clientes/<id>/` con un `tema.json` que
sobrescribe lo que quiera de aquí, y opcionalmente `clientes/<id>/fuentes/` con sus tipografías (un `fuentes.css`
con las reglas @font-face y los .woff2 al lado). Las plantillas no tienen colores ni fuentes escritos: leen el tema.

Los tres colores se llaman por su función, no por su tono, para que un cliente pueda tener otro verde, un azul o un
rojo sin que el código diga «oliva» donde ya no lo es:
  color  = la tinta de color (en Vivax, el oliva)
  oscuro = la tinta oscura (negro)
  claro  = el papel (blanco)
"""
from __future__ import annotations

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CLIENTES = RAIZ / "clientes"

_LATIN = ("U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,"
          "U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD")
_EXT = ("U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,"
        "U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF")

# familia CSS -> (peso, ficheros base). Cada fichero se declara en sus dos subconjuntos, latin y latin-ext.
FUENTES = {
    "Sofia": ("300 900", "sofia-sans-extra-condensed"),
    "Hanken": ("400 800", "hanken-grotesk"),
    "Caveat": ("400", "caveat-brush"),
}

NUTRICION = {
    "id": "nutricion",
    "cuenta": "n1",
    "nombre": "Vivax Nutrición",
    "colores": {"color": "#B0BD6B", "oscuro": "#000000", "claro": "#FFFFFF"},   # oliva claro (9-oct: José lo quiere «mucho más claro»; antes #5E6A2E)
    "fuentes": {"titular": "Sofia", "cuerpo": "Hanken", "mano": "Caveat"},
    "personajes": ["gota", "huevo", "aguacate", "rosquilla"],
}


def cargar(cuenta: str | None = None) -> dict:
    """Tema de una cuenta: el de Vivax Nutrición con lo que diga clientes/<id>/tema.json encima."""
    tema = json.loads(json.dumps(NUTRICION))
    if not cuenta or cuenta in ("n1", "nutricion"):
        return tema
    ruta = CLIENTES / cuenta / "tema.json"
    if not ruta.is_file():
        raise FileNotFoundError(f"No hay tema para la cuenta {cuenta!r}: falta {ruta}")
    extra = json.loads(ruta.read_text(encoding="utf-8"))
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(tema.get(k), dict):
            tema[k].update(v)
        else:
            tema[k] = v
    tema["_carpeta"] = str(ruta.parent)
    return tema


def css_fuentes(carpeta_fuentes: Path, tema: dict) -> str:
    """Reglas @font-face de las fuentes del tema (las propias de Vivax) y, si el cliente trae las suyas, las de su carpeta."""
    f = carpeta_fuentes.resolve().as_uri()
    usadas = {tema["fuentes"][k] for k in ("titular", "cuerpo", "mano")}
    reglas = []
    for fam in sorted(usadas & set(FUENTES)):
        peso, base = FUENTES[fam]
        for sub, rango in (("latin", _LATIN), ("latin-ext", _EXT)):
            reglas.append(f'@font-face{{font-family:"{fam}";font-weight:{peso};src:url("{f}/{base}-{sub}.woff2") format("woff2");'
                          f'unicode-range:{rango}}}')
    propio = Path(tema.get("_carpeta", "")) / "fuentes" / "fuentes.css" if tema.get("_carpeta") else None
    if propio and propio.is_file():
        reglas.append(propio.read_text(encoding="utf-8").replace("__F__", propio.parent.resolve().as_uri()))
    return "\n".join(reglas)


def familias_a_cargar(tema: dict) -> list[tuple[str, str]]:
    """Pares (peso, familia) que el dibujo debe comprobar que han cargado antes de pintar."""
    pesos = {"titular": "900", "cuerpo": "700", "mano": "400"}
    return [(pesos[k], tema["fuentes"][k]) for k in ("titular", "cuerpo", "mano")]
