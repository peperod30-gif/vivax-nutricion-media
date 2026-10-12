"""Personajes de Vivax Nutrición: alimentos con cara, dibujados con trazos de SVG (sin imágenes de banco).

Estilo de dibujo animado de los años treinta: brazos y piernas de goma, ojos de pastel, guantes blancos. El temblor del
trazo no lo pone esta función: lo pone el filtro `tembloroso` de la página (plantillas_d.py), como una tinta que no
cae siempre en el mismo sitio.

Los colores no están en el dibujo: salen de variables CSS (--cuerpo, --claro, --brillo, --yema, --ojo, --tinta,
--mejilla), así que el mismo personaje se pinta con la paleta de cada cuenta o de cada cliente.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

ANCHO, ALTO = 400, 540
_n = itertools.count(1)

POSES = {
    # (dx mano, dy mano, dx control, dy control) respecto del hombro; el brazo izquierdo usa la x con el signo cambiado
    "reposo": ((52, 78, 56, 24), (52, 78, 56, 24)),
    "saludo": ((52, 78, 56, 24), (72, -112, 84, -6)),
    "caderas": ((10, 66, 92, 20), (10, 66, 92, 20)),
    "celebra": ((70, -118, 86, -6), (70, -118, 86, -6)),
    "encoge": ((104, 20, 74, 60), (104, 20, 74, 60)),
    "señala": ((52, 78, 56, 24), (128, -6, 66, -38)),
    "pulgar": ((52, 78, 56, 24), (70, -62, 84, 8)),
    "duda": ((52, 78, 56, 24), (36, -34, 70, 30)),
}


def _t(d, fill="none", sw=9, stroke="var(--tinta)", extra=""):
    return (f'<path d="{d}" style="fill:{fill};stroke:{stroke};stroke-width:{sw};stroke-linejoin:round;'
            f'stroke-linecap:round;{extra}"/>')


def _el(cx, cy, rx, ry, fill, sw=9, stroke="var(--tinta)"):
    return (f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx}" ry="{ry}" style="fill:{fill};stroke:{stroke};'
            f'stroke-width:{sw}"/>')


def ojos(tipo: str, cx: float, cy: float, sep: float = 66, mira=(0, 0)) -> str:
    xs = (cx - sep / 2, cx + sep / 2)
    o = []
    if tipo == "pastel":
        for x in xs:
            o.append(_el(x, cy, 25, 33, "var(--ojo)", 7))
            px, py = x + mira[0], cy + 9 + mira[1]
            o.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="14" style="fill:var(--tinta)"/>')
            o.append(f'<path d="M{px:.1f} {py:.1f} L{px + 3:.1f} {py - 14:.1f} A14 14 0 0 0 {px - 12:.1f} {py - 7:.1f} Z" '
                     f'style="fill:var(--ojo)"/>')
    elif tipo == "puntos":
        for x in xs:
            o.append(f'<ellipse cx="{x:.1f}" cy="{cy:.1f}" rx="14" ry="19" style="fill:var(--tinta)"/>')
            o.append(f'<circle cx="{x + 4:.1f}" cy="{cy - 7:.1f}" r="5.5" style="fill:var(--ojo)"/>')
    elif tipo == "feliz":
        for x in xs:
            o.append(_t(f"M{x - 22:.1f} {cy + 8:.1f} Q{x:.1f} {cy - 28:.1f} {x + 22:.1f} {cy + 8:.1f}", sw=10))
    elif tipo == "cerrados":
        for x in xs:
            o.append(_t(f"M{x - 22:.1f} {cy - 4:.1f} Q{x:.1f} {cy + 24:.1f} {x + 22:.1f} {cy - 4:.1f}", sw=10))
    elif tipo == "sorpresa":
        for x in xs:
            o.append(_el(x, cy, 31, 37, "var(--ojo)", 8))
            o.append(f'<circle cx="{x + mira[0]:.1f}" cy="{cy + mira[1]:.1f}" r="8" style="fill:var(--tinta)"/>')
    elif tipo == "sospecha":
        for x in xs:
            o.append(_el(x, cy + 6, 25, 27, "var(--ojo)", 7))
            o.append(f'<circle cx="{x + mira[0] + 6:.1f}" cy="{cy + 12:.1f}" r="12" style="fill:var(--tinta)"/>')
        o.append(_t(f"M{xs[0] - 34:.1f} {cy - 12:.1f} L{xs[0] + 30:.1f} {cy - 2:.1f}", sw=13))
        o.append(_t(f"M{xs[1] - 30:.1f} {cy - 2:.1f} L{xs[1] + 34:.1f} {cy - 12:.1f}", sw=13))
    elif tipo == "guiño":
        x = xs[0]
        o.append(_el(x, cy, 25, 33, "var(--ojo)", 7))
        o.append(f'<circle cx="{x + 2:.1f}" cy="{cy + 9:.1f}" r="14" style="fill:var(--tinta)"/>')
        o.append(_t(f"M{xs[1] - 22:.1f} {cy + 8:.1f} Q{xs[1]:.1f} {cy - 28:.1f} {xs[1] + 22:.1f} {cy + 8:.1f}", sw=10))
    return "".join(o)


def cejas(tipo: str | None, cx: float, cy: float, sep: float = 66) -> str:
    if not tipo:
        return ""
    xl, xr = cx - sep / 2, cx + sep / 2
    if tipo == "enfado":
        return (_t(f"M{xl - 30:.1f} {cy - 48:.1f} L{xl + 26:.1f} {cy - 26:.1f}", sw=12)
                + _t(f"M{xr + 30:.1f} {cy - 48:.1f} L{xr - 26:.1f} {cy - 26:.1f}", sw=12))
    if tipo == "preocupado":
        return (_t(f"M{xl - 28:.1f} {cy - 30:.1f} L{xl + 26:.1f} {cy - 50:.1f}", sw=11)
                + _t(f"M{xr + 28:.1f} {cy - 30:.1f} L{xr - 26:.1f} {cy - 50:.1f}", sw=11))
    return (_t(f"M{xl - 24:.1f} {cy - 52:.1f} Q{xl:.1f} {cy - 68:.1f} {xl + 24:.1f} {cy - 52:.1f}", sw=10)
            + _t(f"M{xr - 24:.1f} {cy - 52:.1f} Q{xr:.1f} {cy - 68:.1f} {xr + 24:.1f} {cy - 52:.1f}", sw=10))


def boca(tipo: str, cx: float, cy: float) -> str:
    if tipo == "sonrisa":
        d = f"M{cx - 44:.1f} {cy:.1f} C{cx - 34:.1f} {cy + 62:.1f} {cx + 34:.1f} {cy + 62:.1f} {cx + 44:.1f} {cy:.1f} Z"
        cid = f"b{next(_n)}"
        return (f'<clipPath id="{cid}"><path d="{d}"/></clipPath>'
                + _t(d, "var(--tinta)", 8)
                + f'<g clip-path="url(#{cid})"><ellipse cx="{cx:.1f}" cy="{cy + 46:.1f}" rx="26" ry="16" style="fill:var(--lengua)"/>'
                  f'<path d="M{cx - 46:.1f} {cy - 4:.1f} L{cx + 46:.1f} {cy - 4:.1f} L{cx + 40:.1f} {cy + 13:.1f} Q{cx:.1f} {cy + 20:.1f} {cx - 40:.1f} {cy + 13:.1f} Z" '
                  f'style="fill:var(--ojo)"/></g>')
    if tipo == "sonrisa_pequeña":
        return _t(f"M{cx - 34:.1f} {cy + 4:.1f} Q{cx:.1f} {cy + 40:.1f} {cx + 34:.1f} {cy + 4:.1f}", sw=10)
    if tipo == "o":
        return _el(cx, cy + 14, 17, 24, "var(--tinta)", 6)
    if tipo == "grita":
        d = f"M{cx - 42:.1f} {cy - 6:.1f} Q{cx:.1f} {cy - 22:.1f} {cx + 42:.1f} {cy - 6:.1f} Q{cx + 46:.1f} {cy + 60:.1f} {cx:.1f} {cy + 62:.1f} Q{cx - 46:.1f} {cy + 60:.1f} {cx - 42:.1f} {cy - 6:.1f} Z"
        return _t(d, "var(--tinta)", 8) + f'<ellipse cx="{cx:.1f}" cy="{cy + 46:.1f}" rx="24" ry="12" style="fill:var(--lengua)"/>'
    if tipo == "mueca":
        return _t(f"M{cx - 42:.1f} {cy + 14:.1f} L{cx - 21:.1f} {cy + 2:.1f} L{cx:.1f} {cy + 16:.1f} L{cx + 21:.1f} {cy + 2:.1f} L{cx + 42:.1f} {cy + 14:.1f}", sw=10)
    if tipo == "lado":
        return _t(f"M{cx - 36:.1f} {cy + 16:.1f} Q{cx + 8:.1f} {cy + 22:.1f} {cx + 40:.1f} {cy - 10:.1f}", sw=10)
    if tipo == "triste":
        return _t(f"M{cx - 34:.1f} {cy + 28:.1f} Q{cx:.1f} {cy - 8:.1f} {cx + 34:.1f} {cy + 28:.1f}", sw=10)
    if tipo == "recta":
        return _t(f"M{cx - 30:.1f} {cy + 12:.1f} L{cx + 30:.1f} {cy + 12:.1f}", sw=10)
    return ""


def mejillas(cx: float, cy: float, sep: float = 150) -> str:
    return "".join(f'<ellipse cx="{x:.1f}" cy="{cy:.1f}" rx="19" ry="11" style="fill:var(--mejilla)"/>' for x in (cx - sep / 2, cx + sep / 2))


def _brazo(hombro, brazo, lado: int, puño: str = "guante") -> str:
    """Brazo de goma: trazo negro grueso y guante blanco. lado = -1 (izquierdo) o 1 (derecho)."""
    sx, sy = hombro
    dx, dy, cx, cy = brazo
    hx, hy = sx + lado * dx, sy + dy
    kx, ky = sx + lado * cx, sy + cy
    g = [f'<path d="M{sx:.1f} {sy:.1f} Q{kx:.1f} {ky:.1f} {hx:.1f} {hy:.1f}" style="fill:none;stroke:var(--tinta);stroke-width:14;stroke-linecap:round"/>']
    g.append(_el(hx, hy, 23, 21, "var(--guante)", 7))
    # tres rayitas de dedos
    g.append(_t(f"M{hx - 8:.1f} {hy - 4:.1f} L{hx - 6:.1f} {hy + 8:.1f} M{hx + 3:.1f} {hy - 7:.1f} L{hx + 3:.1f} {hy + 8:.1f}", sw=4))
    return "".join(g)


def _pierna(arranque, fin, lado: int) -> str:
    ax, ay = arranque
    fx, fy = fin
    return (f'<path d="M{ax:.1f} {ay:.1f} Q{ax + lado * 6:.1f} {(ay + fy) / 2:.1f} {fx:.1f} {fy:.1f}" '
            f'style="fill:none;stroke:var(--tinta);stroke-width:14;stroke-linecap:round"/>'
            f'<ellipse cx="{fx + lado * 16:.1f}" cy="{fy + 10:.1f}" rx="34" ry="15" style="fill:var(--tinta)"/>')


@dataclass(frozen=True)
class Cuerpo:
    dibujo: str          # trazos del cuerpo (sin cara)
    cara: tuple          # (cx, cy) centro de la cara
    sep_ojos: float
    hombros: tuple       # ((x, y) izquierdo, (x, y) derecho)
    caderas: tuple       # ((x, y), (x, y)) arranque de las piernas
    pies: tuple          # ((x, y), (x, y)) final de las piernas
    ancho_cuerpo: float
    cejas_dy: float = 0


def _gota() -> Cuerpo:
    d = "M200 28 C214 78 334 196 334 330 C334 412 276 470 200 470 C124 470 66 412 66 330 C66 196 186 78 200 28 Z"
    dib = (_t(d, "var(--cuerpo)", 10)
           + _t("M108 296 Q100 346 126 394", "none", 15, "var(--brillo)")
           + _t("M150 226 Q142 244 138 258", "none", 12, "var(--brillo)"))
    return Cuerpo(dib, (200, 338), 70, ((78, 352), (322, 352)), ((160, 462), (240, 462)), ((150, 506), (250, 506)), 268)


def _huevo() -> Cuerpo:
    blob = ("M88 156 C38 146 12 224 56 266 C14 326 62 410 148 386 C198 438 304 418 326 366 "
            "C390 356 398 270 352 238 C392 172 330 98 270 124 C228 68 140 80 88 156 Z")
    yema = _el(205, 256, 112, 108, "var(--yema)", 9)
    dib = (_t(blob, "var(--claro)", 10) + yema
           + _t("M128 214 Q118 246 132 276", "none", 14, "var(--brillo)"))
    return Cuerpo(dib, (205, 262), 72, ((52, 262), (360, 262)), ((130, 396), (260, 410)), ((124, 462), (258, 468)), 330, 4)


def _aguacate() -> Cuerpo:
    d = ("M200 40 C262 40 288 112 302 182 C318 252 346 304 346 364 C346 442 286 492 200 492 "
         "C114 492 54 442 54 364 C54 304 82 252 98 182 C112 112 138 40 200 40 Z")
    dib = (_t(d, "var(--cuerpo)", 10)
           + f'<g transform="translate(200 300) scale(.83) translate(-200 -300)">{_t(d, "var(--claro)", 8)}</g>'
           + _el(200, 396, 66, 64, "var(--hueso)", 9)
           + _t("M162 372 Q152 392 164 416", "none", 11, "var(--brillo)"))
    return Cuerpo(dib, (200, 222), 74, ((70, 332), (330, 332)), ((156, 484), (244, 484)), ((148, 512), (252, 512)), 290)


def _rosquilla() -> Cuerpo:
    import math
    pts = []
    for k in range(72):
        a = 2 * math.pi * k / 72
        r = 142 + 10 * math.sin(7 * a + .6)
        pts.append((200 + r * math.cos(a), 262 + r * math.sin(a)))
    glaseado = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z"
    chispas = []
    for (x, y, r) in ((96, 176, 20), (150, 112, -35), (262, 122, 30), (318, 206, -22), (84, 262, 70), (322, 286, 12), (236, 150, -50), (118, 226, 50), (292, 160, 80)):
        chispas.append(f'<path d="M{x} {y} l{20 * math.cos(math.radians(r)):.1f} {20 * math.sin(math.radians(r)):.1f}" '
                       f'style="stroke:var(--tinta);stroke-width:11;stroke-linecap:round"/>')
    dib = (_el(200, 262, 176, 176, "var(--claro)", 10)
           + _t(glaseado, "var(--cuerpo)", 8)
           + "".join(chispas)
           + _el(200, 196, 56, 50, "var(--hueco)", 9))
    return Cuerpo(dib, (200, 340), 76, ((26, 268), (374, 268)), ((150, 432), (250, 432)), ((142, 496), (258, 496)), 352)


def _persona(pelo: str = "corto", gafas: bool = False) -> Cuerpo:
    """Muñeco de una persona (no un retrato): cabeza redonda, pelo de varios tipos y gafas opcionales. Pone cara a las voces reales."""
    cuerpo = "M115 262 C95 340 70 410 78 462 C80 478 100 486 200 486 C300 486 320 478 322 462 C330 410 305 340 285 262 C250 240 150 240 115 262 Z"
    cabeza = _el(200, 152, 106, 108, "var(--claro)", 10)
    corto = "M96 146 C92 70 148 40 200 40 C252 40 308 70 304 146 C282 106 242 94 200 94 C158 94 118 106 96 146 Z"
    pelos = {
        "corto": _t(corto, "var(--tinta)", 8),
        "largo": (_t(corto, "var(--tinta)", 8) + _t("M98 140 C80 200 84 252 102 276 L128 254 C116 212 116 172 120 128 Z", "var(--tinta)", 8)
                  + _t("M302 140 C320 200 316 252 298 276 L272 254 C284 212 284 172 280 128 Z", "var(--tinta)", 8)),
        "moño": _t(corto, "var(--tinta)", 8) + _el(200, 30, 36, 30, "var(--tinta)", 8),
        "gorra": (_t("M92 134 C92 62 146 34 200 34 C254 34 308 62 308 134 Z", "var(--cuerpo)", 9)
                  + _t("M302 124 C342 122 368 134 376 152 C352 152 322 148 298 144 Z", "var(--cuerpo)", 9)),
        "calvo": "",
    }
    gaf = ""
    if gafas:
        gaf = (_el(165, 160, 34, 32, "none", 8) + _el(235, 160, 34, 32, "none", 8) + _t("M199 160 L201 160", "none", 8))
    dib = _t(cuerpo, "var(--cuerpo)", 10) + cabeza + pelos.get(pelo, pelos["corto"]) + gaf
    return Cuerpo(dib, (200, 150), 70, ((98, 300), (302, 300)), ((160, 480), (240, 480)), ((150, 514), (250, 514)), 250)


CUERPOS = {"gota": _gota, "huevo": _huevo, "aguacate": _aguacate, "rosquilla": _rosquilla, "persona": _persona}


def personaje(nombre: str, ojos_: str = "pastel", boca_: str = "sonrisa", pose: str = "reposo", cejas_: str | None = None,
              mira=(0, 0), mejilla: bool = True, piernas: bool = True, brazos: bool = True, extra: str = "", rasgos: dict | None = None) -> str:
    """SVG completo (400×540) del personaje. `extra` va encima de todo (por ejemplo un cartel en la mano)."""
    c = CUERPOS[nombre](**(rasgos or {}))
    cx, cy = c.cara
    g = []
    if piernas:
        g.append(_pierna(c.caderas[0], c.pies[0], -1) + _pierna(c.caderas[1], c.pies[1], 1))
    g.append(c.dibujo)
    if mejilla:
        g.append(mejillas(cx, cy + 44, c.sep_ojos * 2.1))
    g.append(ojos(ojos_, cx, cy, c.sep_ojos, mira))
    g.append(cejas(cejas_, cx, cy + c.cejas_dy, c.sep_ojos))
    g.append(boca(boca_, cx, cy + 50))
    if brazos:
        izq, der = POSES[pose]
        g.append(_brazo(c.hombros[0], izq, -1) + _brazo(c.hombros[1], der, 1))
    g.append(extra)
    return (f'<svg class="pj" viewBox="0 0 {ANCHO} {ALTO}" xmlns="http://www.w3.org/2000/svg" overflow="visible">'
            f'{"".join(g)}</svg>')


def colores(sobre: str, nombre: str, paleta: dict) -> str:
    """Variables CSS de color del personaje según la superficie que tiene detrás (color, claro u oscuro) y la paleta del tema."""
    sobre = {"oliva": "color", "blanco": "claro", "negro": "oscuro"}.get(sobre, sobre)
    c, o, b = paleta["color"], paleta["oscuro"], paleta["claro"]
    if sobre == "color":
        cuerpo, brillo, hueco = b, c, c
    elif sobre == "claro":
        cuerpo, brillo, hueco = c, b, b
    else:
        cuerpo, brillo, hueco = (b if nombre == "gota" else c), b, o
    return (f"--cuerpo:{cuerpo};--claro:{b};--brillo:{brillo};--yema:{c};--hueso:{c};--mejilla:{c};"
            f"--ojo:{b};--tinta:{o};--guante:{b};--lengua:{c};--hueco:{hueco}")
