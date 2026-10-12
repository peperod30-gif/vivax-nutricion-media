"""Plantillas de la dirección D de Vivax Nutrición: personajes, formas redondeadas y poco texto.

Sustituye a la dirección C (cajas y tablas), que a José le pareció «muy cuadrada». Cada hoja es HTML y CSS propios,
dibujados en un navegador (render.py). Las reglas de la casa:

- Ninguna estructura es un rectángulo. Lo que sujeta el texto son manchas, globos, pastillas, estallidos y sellos
  redondos. Los rectángulos solo existen como bordes de la imagen.
- Una idea por hoja. Cada hoja tiene un titular corto y, como mucho, una frase; el detalle va a la hoja final.
- Cada hoja lleva un personaje con cara que reacciona al dato (personajes.py).
- Los defectos tienen causa de imprenta: trazo que tiembla (tinta que no cae igual), bordes de mancha rotos, marcador
  fluorescente pasado a mano, grano de papel. Nada de degradados, brillos ni sombras difusas.
- Los colores y las fuentes salen del tema de la cuenta (temas.py): la misma hoja se pinta con otro tema.

La semilla de cada hoja sale de un hash (pieza + número de hoja): repetir el render da la misma imagen.
"""
from __future__ import annotations

import html
import math
import random
import re
import sys
from pathlib import Path

import personajes as PJ
import temas as T
from plantillas import _grano, caracteres_sin_fuente, semilla  # noqa: F401  (se reexportan para render.py)

ANCHO, ALTO = 1080, 1350
NBSP = chr(160)
WJ = chr(0x2060)   # unión de palabras: invisible, impide el salto de línea
TEXTOS = ("etiqueta", "titular", "titulo", "dice", "sello", "antes", "cifra", "signo", "unidad", "texto", "frase", "dato",
          "fuente", "sigue", "aviso", "items", "fuentes", "izq", "der", "cita", "quien", "estudio",
          "respuesta", "filas", "si", "no", "ahora", "etiqueta_si", "etiqueta_no")


def e(t) -> str:
    return html.escape(str(t), quote=True)


def limpiar(t: str) -> str:
    """Texto sin las marcas de énfasis ({palabra} marcador, {{palabra}} círculo)."""
    return t.replace("{", "").replace("}", "")


def tx(t: str) -> str:
    """Texto escapado con sus marcas de énfasis convertidas en <mark>. Las unidades (10 g) van pegadas a la cifra y en minúscula."""
    s = re.sub(r"(\d) (g|kg|mg|ml|kcal|h|min)(?![\wáéíóúñ])", lambda m: m.group(1) + NBSP + '<span class="u">' + m.group(2) + "</span>", e(t))
    s = re.sub(r"^(kg|kcal|mg|ml)(?![\wáéíóúñ])", r'<span class="u">\1</span>', s)       # una unidad al empezar (kg: muy poco) también en minúscula
    s = re.sub(r"(\d) (?=%)", lambda m: m.group(1) + NBSP, s)
    s = re.sub(r"(?<=\w)-(?=\w)", "-" + WJ, s)   # que «low-carb» no se parta en dos líneas
    s = re.sub(r"\{\{(.+?)\}\}", r'<mark class="c">\1</mark>', s)
    return re.sub(r"\{(.+?)\}", r'<mark class="m">\1</mark>', s)


def _aplanar(v):
    """Los textos de un campo, sea cadena, número, lista o diccionario (filas de un ranking, lados de un antes y ahora).
    La clave «icono» (el emoji de un alimento) no es texto de la pieza."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        yield str(v)
    elif isinstance(v, dict):
        for k, x in v.items():
            if k != "icono":
                yield from _aplanar(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _aplanar(x)


def textos_de(h: dict) -> list[str]:
    """Todos los textos visibles de una hoja, sin marcas (para comprobar caracteres, cifras y reglas)."""
    sal = []
    for k in TEXTOS:
        sal.extend(limpiar(t) for t in _aplanar(h.get(k)))
    return sal


# ------------------------------------------------------------------ geometría a mano

def _suave(pts: list[tuple[float, float]]) -> str:
    """Curva cerrada que pasa por los puntos (Catmull-Rom convertida a bezier)."""
    n = len(pts)
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}"
    return d + " Z"


def mancha(sem: random.Random, cx: float, cy: float, rx: float, ry: float, n: int = 9, irr: float = .16) -> str:
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n + sem.uniform(-.12, .12)
        f = 1 + sem.uniform(-irr, irr)
        pts.append((cx + rx * f * math.cos(a), cy + ry * f * math.sin(a)))
    return _suave(pts)


def estallido(sem: random.Random, cx: float, cy: float, r1: float, r2: float, puntas: int = 13) -> str:
    pts = []
    for k in range(puntas * 2):
        a = math.pi * k / puntas + sem.uniform(-.05, .05)
        r = (r2 if k % 2 == 0 else r1) * (1 + sem.uniform(-.09, .09))
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + " Z"


def ola(sem: random.Random, y0: float, y1: float, ancho: int = ANCHO, tramos: int = 5) -> str:
    """Borde ondulado de izquierda a derecha, de la altura y0 a la y1 (para partir una hoja en dos sin una recta)."""
    pts = [(-40 + (ancho + 80) * k / tramos, y0 + (y1 - y0) * k / tramos + sem.uniform(-34, 34)) for k in range(tramos + 1)]
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        mx = (a[0] + b[0]) / 2
        d += f" C{mx:.1f} {a[1]:.1f} {mx:.1f} {b[1]:.1f} {b[0]:.1f} {b[1]:.1f}"
    return d


# ------------------------------------------------------------------ CSS

def css(carpeta_fuentes: Path, tema: dict) -> str:
    c = tema["colores"]
    f = tema["fuentes"]
    return """
__FUENTES__
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;height:1350px}
:root{--o:__O__;--n:__N__;--b:__B__;--ft:"__FT__";--fc:"__FC__";--fm:"__FM__"}
body{font-family:var(--fc),sans-serif;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;background:var(--b)}
.hoja{position:relative;width:1080px;height:1350px;overflow:hidden}
.f-color{background:var(--o);color:var(--n);--fg:var(--n);--sh:var(--b);--mk:var(--b);--ci:var(--n);--tgb:var(--n);--tgf:var(--b)}
.f-claro{background:var(--b);color:var(--n);--fg:var(--n);--sh:var(--o);--mk:var(--o);--ci:var(--n);--tgb:var(--o);--tgf:var(--n)}
.f-oscuro{background:var(--n);color:var(--b);--fg:var(--b);--sh:var(--o);--mk:var(--o);--ci:var(--b);--tgb:var(--b);--tgf:var(--n)}
.abs{position:absolute}
[data-fit],[data-fitw]{overflow:hidden}
mark{background:none;color:inherit;text-shadow:none}
mark.m{color:var(--n)}
.u{text-transform:none}
.grano{position:absolute;inset:0;pointer-events:none;mix-blend-mode:multiply;opacity:.45;background-size:540px 540px}
.fx{position:absolute;left:0;top:0;width:1080px;height:1350px;overflow:visible;pointer-events:none}
/* titulares: tinta gruesa con la sombra dura corrida, como un cliché mal registrado */
.tit{position:absolute;font-family:var(--ft);font-weight:900;text-transform:uppercase;line-height:.9;letter-spacing:-.004em;
     text-shadow:8px 8px 0 var(--sh);padding:0 14px 14px 0;display:flex;align-items:center;text-wrap:balance}
.tit > span{display:block}
/* pastilla */
.tag{position:absolute;font-family:var(--fm);font-size:56px;line-height:1;padding:8px 40px 14px;border-radius:999px;background:var(--tgb);
     color:var(--tgf);filter:drop-shadow(7px 7px 0 var(--n));white-space:nowrap}
/* globo de diálogo */
.glob{position:absolute}
.glob svg{position:absolute;left:0;top:0;overflow:visible;filter:drop-shadow(8px 8px 0 var(--n))}
.glob .bt{position:absolute;display:flex;align-items:center;justify-content:center;text-align:center;font-family:var(--fm);
          line-height:1.02;color:#000;overflow:hidden}
/* sello redondo */
.sello{position:absolute;border:11px solid var(--n);border-radius:50%;display:flex;align-items:center;justify-content:center;
       text-align:center;font-family:var(--ft);font-weight:900;text-transform:uppercase;line-height:.88;color:var(--n);
       background:transparent;filter:url(#aspero)}
/* personaje */
.pjw{position:absolute}
.pjw .pj{width:100%;height:100%;overflow:visible;
         filter:url(#tembloroso) drop-shadow(5px 0 0 var(--b)) drop-shadow(-5px 0 0 var(--b)) drop-shadow(0 5px 0 var(--b))
                drop-shadow(0 -5px 0 var(--b)) drop-shadow(10px 10px 0 var(--n))}
/* pie */
.marca{position:absolute;left:64px;bottom:36px;font:900 40px/1 var(--ft);text-transform:uppercase;letter-spacing:.05em}
.puntos{position:absolute;right:60px;top:70px;display:flex;gap:14px}
.puntos i{display:block;width:24px;height:24px;border-radius:50%;border:5px solid currentColor}
.puntos i.on{background:currentColor}
.sigue{position:absolute;bottom:96px;font:400 48px/1 var(--fm);display:flex;align-items:center;gap:12px;padding:8px 26px 12px 34px;border-radius:999px;background:var(--tgb);color:var(--tgf);filter:drop-shadow(6px 6px 0 var(--n));transform:rotate(-3deg)}
.sigue svg{width:80px;height:38px;overflow:visible}
/* cifra */
.num{position:absolute;display:flex;align-items:center;font-family:var(--ft);font-weight:900;line-height:.8;white-space:nowrap}
.num .cap{position:relative;display:flex;align-items:flex-start;margin:0 auto}
.num .cap > .copia{position:absolute;left:0;top:0;transform:translate(14px,12px);color:var(--sh);display:flex;align-items:flex-start}
.num .cap > .real{position:relative;display:flex;align-items:flex-start}
.num .signo{font-size:.62em;margin-right:.04em;margin-top:.02em}
.num .dec{font-size:.46em;vertical-align:top;position:relative;top:.06em;line-height:1}
/* texto corrido */
.cuerpo{position:absolute;font-family:var(--fc);font-weight:800;line-height:1.14}
.mano{font-family:var(--fm);font-weight:400}
.fuentes{position:absolute;font:500 28px/1.3 var(--fc)}
.fuentes b{font-weight:800}
.aviso{position:absolute;font:600 27px/1.3 var(--fc)}
.fm{position:absolute;left:450px;bottom:42px;font:700 26px/1 var(--fc);opacity:.92;max-width:260px;white-space:nowrap}
""".replace("__FUENTES__", T.css_fuentes(carpeta_fuentes, tema)).replace("__O__", c["color"]).replace("__N__", c["oscuro"]) \
        .replace("__B__", c["claro"]).replace("__FT__", f["titular"]).replace("__FC__", f["cuerpo"]).replace("__FM__", f["mano"]) + _css_virales()


def _defs(sem: random.Random) -> str:
    s1, s2 = sem.randint(1, 9999), sem.randint(1, 9999)
    return f"""<svg width="0" height="0" style="position:absolute"><defs>
<filter id="aspero" x="-6%" y="-6%" width="112%" height="112%"><feTurbulence type="fractalNoise" baseFrequency="0.045" numOctaves="2" seed="{s1}" result="r"/><feDisplacementMap in="SourceGraphic" in2="r" scale="9" xChannelSelector="R" yChannelSelector="G"/></filter>
<filter id="tembloroso" x="-10%" y="-10%" width="120%" height="120%"><feTurbulence type="fractalNoise" baseFrequency="0.02" numOctaves="2" seed="{s2}" result="r"/><feDisplacementMap in="SourceGraphic" in2="r" scale="7" xChannelSelector="R" yChannelSelector="G"/></filter>
<filter id="borde" x="-5%" y="-5%" width="110%" height="110%"><feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="3" seed="{s1}" result="a"/><feDisplacementMap in="SourceGraphic" in2="a" scale="11" xChannelSelector="R" yChannelSelector="G"/></filter>
</defs></svg>"""


# ------------------------------------------------------------------ piezas sueltas de dibujo

def _fondo(h: dict, defecto: str) -> str:
    f = h.get("fondo", defecto)
    return {"oliva": "color", "blanco": "claro", "negro": "oscuro"}.get(f, f)


def _sobre_blob(fondo: str) -> str:
    """Superficie de una mancha grande que se pinta sobre el fondo de la hoja, siempre en contraste con él."""
    return {"color": "claro", "claro": "color", "oscuro": "color"}[fondo]


def _mancha_svg(d: str, relleno: str, trazo: str = "var(--n)", sw: int = 10, extra: str = "") -> str:
    return (f'<svg class="fx" viewBox="0 0 {ANCHO} {ALTO}"><path d="{d}" style="fill:{relleno};stroke:{trazo};stroke-width:{sw};'
            f'stroke-linejoin:round;{extra}" filter="url(#borde)"/></svg>')


def _pj(spec: dict, x: float, y: float, w: float, sobre: str, ctx: dict, rot: float | None = None, voltear: bool = False) -> str:
    """Personaje colocado: x, y, ancho; `sobre` es la superficie que tiene detrás para elegir sus colores."""
    quien = spec["quien"]
    svg = PJ.personaje(quien, spec.get("ojos", "pastel"), spec.get("boca", "sonrisa"), spec.get("pose", "reposo"),
                       spec.get("cejas"), tuple(spec.get("mira", (0, 0))), rasgos=spec.get("rasgos"))
    r = spec.get("rot", rot if rot is not None else ctx["sem"].uniform(-5, 5))
    esc = "scaleX(-1)" if (voltear or spec.get("voltear")) else ""
    alto = w * PJ.ALTO / PJ.ANCHO
    return (f'<div class="pjw" style="left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{alto:.0f}px;'
            f'transform:rotate({r:.1f}deg) {esc};{PJ.colores(sobre, quien, ctx["tema"]["colores"])}">{svg}</div>')


def _globo(sem: random.Random, x: float, y: float, w: float, h: float, texto: str, punta: tuple[float, float], fit=(40, 90)) -> str:
    """Globo de diálogo con rabito hacia `punta` (en coordenadas de la hoja). Texto a mano, que se ajusta al globo."""
    pts = []
    n = 14
    for k in range(n):
        a = 2 * math.pi * k / n
        ca, sa = math.cos(a), math.sin(a)
        px = (w / 2) * math.copysign(abs(ca) ** .5, ca) * (1 + sem.uniform(-.025, .025))
        py = (h / 2) * math.copysign(abs(sa) ** .5, sa) * (1 + sem.uniform(-.04, .04))
        pts.append((w / 2 + px, h / 2 + py))
    cuerpo = _suave(pts)
    tx_, ty_ = punta[0] - x, punta[1] - y
    cx, cy = w / 2, h / 2
    dx, dy = tx_ - cx, ty_ - cy
    ln = math.hypot(dx, dy) or 1
    ux, uy = dx / ln, dy / ln
    bx, by = cx + ux * min(w, h) * .36, cy + uy * min(w, h) * .36
    px_, py_ = -uy * 54, ux * 54
    cola = f"M{bx + px_:.1f} {by + py_:.1f} L{tx_:.1f} {ty_:.1f} L{bx - px_:.1f} {by - py_:.1f} Z"
    estilo = "fill:var(--b);stroke:#000;stroke-width:8;stroke-linejoin:round"
    ancho_txt, alto_txt = w * .78, h * .66
    return (f'<div class="glob" style="left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{h:.0f}px">'
            f'<svg width="{w:.0f}" height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}"><path d="{cola}" style="{estilo}"/>'
            f'<path d="{cuerpo}" style="{estilo}"/></svg>'
            f'<div class="bt" style="left:{(w - ancho_txt) / 2:.0f}px;top:{(h - alto_txt) / 2:.0f}px;width:{ancho_txt:.0f}px;height:{alto_txt:.0f}px" '
            f'data-fit="{fit[0]},{fit[1]}"><span>{tx(texto)}</span></div></div>')


def _tit(texto: str, x: int, y: int, w: int, alto: int, fit: tuple[int, int], rot: float = 0, align: str = "left", color: str = "") -> str:
    col = f"color:{color};" if color else ""
    return (f'<div class="tit" style="left:{x}px;top:{y}px;width:{w}px;height:{alto}px;transform:rotate({rot:.2f}deg);text-align:{align};'
            f'{col}justify-content:{"center" if align == "center" else "flex-start"}" data-fit="{fit[0]},{fit[1]}"><span>{tx(texto)}</span></div>')


def _tag(texto: str, x: int, y: int, rot: float = -4, tam: int = 56) -> str:
    return f'<div class="tag" style="left:{x}px;top:{y}px;transform:rotate({rot:.1f}deg);font-size:{tam}px">{e(texto)}</div>'


def _sello(texto: str, x: int, y: int, d: int, rot: float, fit=(40, 76)) -> str:
    return (f'<div class="sello" style="left:{x}px;top:{y}px;width:{d}px;height:{d}px;transform:rotate({rot:.1f}deg);padding:{d * .13:.0f}px" '
            f'data-fit="{fit[0]},{fit[1]}"><span>{tx(texto)}</span></div>')


def _puntos(n: int, total: int) -> str:
    return '<div class="puntos">' + "".join(f'<i class="{"on" if k == n else ""}"></i>' for k in range(1, total + 1)) + "</div>"


def _sigue(h: dict, lado: str) -> str:
    if not h.get("sigue"):
        return ""
    flecha = ('<svg viewBox="0 0 96 44"><path d="M4 26 C30 6 58 6 86 24 M70 8 L88 24 L68 36" style="fill:none;stroke:currentColor;'
              'stroke-width:7;stroke-linecap:round;stroke-linejoin:round"/></svg>')
    pos = "right:64px" if lado == "der" else "left:64px"
    return f'<div class="sigue" style="{pos}"><span>{tx(h["sigue"])}</span>{flecha}</div>'


def _marca(ctx: dict) -> str:
    return f'<div class="marca">{e(ctx["tema"]["nombre"])}</div>'


def _hoja(clase: str, interior: str) -> str:
    return f'<div class="hoja {clase}">{interior}</div>'


# ------------------------------------------------------------------ una función por plantilla

def portada(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = _fondo(h, "color")
    blob = _mancha_svg(mancha(sem, 640, 1030, 480, 340), f"var(--{'b' if fondo == 'color' else 'o'})")
    sobre = _sobre_blob(fondo)
    partes = [blob]
    if h.get("etiqueta"):
        partes.append(_tag(h["etiqueta"], 64, 64, sem.uniform(-5, -2)))
    partes.append(_tit(h["titular"], 64, 170, 952, 520, (96, 250), sem.uniform(-1.6, -.4)))
    partes.append(_pj(h["personaje"], 560, 676, 460, sobre, ctx))
    if h.get("dice"):
        partes.append(_globo(sem, 50, 770, 470, 250, h["dice"], (590, 930), (40, 84)))
    if h.get("sello"):
        partes.append(_sello(h["sello"], 70, 1050, 230, sem.uniform(-14, -6)))
    partes.append(_puntos(ctx["n"], ctx["total"]) if ctx["total"] > 1 else "")
    partes.append(_marca(ctx))
    partes.append(_sigue(h, "izq"))
    return _hoja(f"f-{fondo}", "".join(partes))


def dice(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = _fondo(h, "claro")
    blob = _mancha_svg(mancha(sem, 540, 1000, 370, 250), f"var(--{'b' if fondo == 'color' else 'o'})")
    sobre = _sobre_blob(fondo)
    partes = [blob, _tit(h["titulo"], 64, 124, 952, 290, (84, 190), sem.uniform(-1.4, 1.0))]
    partes.append(_pj(h["personaje"], 330, 706, 420, sobre, ctx))
    partes.append(_globo(sem, 90, 450, 900, 260, h["dice"], (520, 790), (46, 96)))
    partes.append(_puntos(ctx["n"], ctx["total"]) + _marca(ctx) + _sigue(h, "der"))
    return _hoja(f"f-{fondo}", "".join(partes))


def cifra(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = _fondo(h, "oscuro")
    col_burst = "var(--b)" if fondo == "color" else "var(--o)"
    num_col = "var(--n)"   # negro también sobre la mancha oliva clara (el blanco no se lee sobre #B0BD6B: 2,0:1)
    sombra = "var(--o)"   # copia en oliva también sobre negro: marca el borde de la cifra donde la mancha no llega (como los títulos de f-oscuro)
    signo = f'<span class="signo">{e(h["signo"])}</span>' if h.get("signo") else ""
    t = str(h["cifra"])
    if h.get("precio") and "," in t:
        ent, dec = t.split(",", 1)
        cuerpo = f'{signo}<span class="ent">{e(ent)}</span><sup class="dec">,{e(dec)}</sup>'
    else:
        cuerpo = f'{signo}<span class="ent">{e(t)}</span>'
    partes = [_mancha_svg(estallido(sem, 540, 560, 250, 340), col_burst, "var(--n)", 10)]
    if h.get("antes"):
        partes.append(f'<div class="cuerpo mano" data-fit="48,88" style="left:64px;top:84px;width:700px;height:170px;font-size:80px;'
                      f'transform:rotate({sem.uniform(-2.5, -.6):.1f}deg)"><span>{tx(h["antes"])}</span></div>')
    partes.append(f'<div class="num" data-fitw="170,520" style="left:230px;top:380px;width:620px;height:400px;color:{num_col};--sh:{sombra}">'
                  f'<div class="cap"><div class="copia">{cuerpo}</div><div class="real">{cuerpo}</div></div></div>')
    if h.get("unidad"):
        partes.append(_tit(h["unidad"], 64, 904, 640, 110, (46, 96), sem.uniform(-1.8, 1.2)))
    partes.append(f'<div class="cuerpo" data-fit="30,48" style="left:64px;top:1030px;width:630px;height:150px"><span>{tx(h["texto"])}</span></div>')
    partes.append(_pj(h["personaje"], 724, 880, 310, fondo, ctx))
    partes.append(_puntos(ctx["n"], ctx["total"]) + _marca(ctx) + _sigue(h, "izq"))
    return _hoja(f"f-{fondo}", "".join(partes))


def versus(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    corte = ola(sem, 640, 700)
    arriba = f"{corte} L{ANCHO + 40} -40 L-40 -40 Z"
    abajo = f"{corte} L{ANCHO + 40} {ALTO + 40} L-40 {ALTO + 40} Z"
    tach = "text-decoration:line-through;text-decoration-thickness:7px;" if h["izq"].get("tachado", False) else ""   # por defecto no se tacha: tachado se lee como «falso»
    partes = [f'<svg class="fx" viewBox="0 0 {ANCHO} {ALTO}"><path d="{abajo}" style="fill:var(--b)" filter="url(#borde)"/>'
              f'<path d="{arriba}" style="fill:var(--n)" filter="url(#borde)"/>'
              f'<path d="{corte}" style="fill:none;stroke:var(--o);stroke-width:18;stroke-linecap:round" filter="url(#borde)"/></svg>']
    partes.append(f'<div class="tag" style="left:64px;top:84px;transform:rotate(-3deg);--tgb:var(--b);--tgf:var(--n);color:var(--n)">{e(h["izq"]["cab"])}</div>')
    partes.append(f'<div class="cuerpo" data-fit="44,112" style="left:64px;top:220px;width:952px;height:350px;color:var(--b);{tach}"><span>{tx(h["izq"]["texto"])}</span></div>')
    partes.append(f'<div class="tag" style="left:64px;top:770px;transform:rotate(2deg);--tgb:var(--o);--tgf:var(--n)">{e(h["der"]["cab"])}</div>')
    partes.append(f'<div class="cuerpo" data-fit="40,84" style="left:64px;top:890px;width:620px;height:340px;color:var(--n)"><span>{tx(h["der"]["texto"])}</span></div>')
    partes.append(_pj(h["personaje"], 706, 790, 340, "claro", ctx))
    partes.append(f'<div class="puntos" style="color:var(--b)">' + "".join(f'<i class="{"on" if k == ctx["n"] else ""}"></i>' for k in range(1, ctx["total"] + 1)) + "</div>")
    partes.append(f'<div class="marca" style="color:var(--n)">{e(ctx["tema"]["nombre"])}</div>')
    return _hoja("f-claro", "".join(partes))


def lista(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = _fondo(h, "claro")
    marca = h.get("marca", "num")
    n = len(h["items"])
    alto_fila = min(760 // n, 215)
    filas = []
    for k, t in enumerate(h["items"]):
        y = 400 + k * alto_fila
        if marca == "aspa":
            signo = '<path d="M30 30 L70 70 M70 30 L30 70" style="fill:none;stroke:var(--n);stroke-width:13;stroke-linecap:round"/>'
        elif marca == "exclama":
            signo = ('<path d="M50 24 L50 56" style="fill:none;stroke:var(--n);stroke-width:13;stroke-linecap:round"/>'
                     '<circle cx="50" cy="73" r="7.5" style="fill:var(--n)"/>')
        elif marca == "visto":
            signo = '<path d="M26 54 L44 72 L76 30" style="fill:none;stroke:var(--n);stroke-width:13;stroke-linecap:round;stroke-linejoin:round"/>'
        elif marca == "baja":
            signo = '<path d="M50 26 L50 72 M31 54 L50 74 L69 54" style="fill:none;stroke:var(--n);stroke-width:13;stroke-linecap:round;stroke-linejoin:round"/>'
        elif marca == "sube":
            signo = '<path d="M50 74 L50 28 M31 46 L50 26 L69 46" style="fill:none;stroke:var(--n);stroke-width:13;stroke-linecap:round;stroke-linejoin:round"/>'
        else:
            signo = f'<text x="50" y="72" text-anchor="middle" style="font:900 62px var(--ft);fill:var(--n)">{k + 1}</text>'
        r = sem.uniform(-1.2, 1.2)
        insignia = (f'<svg width="100" height="100" viewBox="0 0 100 100" style="flex:none;overflow:visible;transform:rotate({r:.1f}deg)">'
                    f'<path d="{mancha(sem, 50, 50, 46, 46, 8, .06)}" style="fill:var(--o);stroke:var(--n);stroke-width:8" filter="url(#aspero)"/>{signo}</svg>')
        filas.append(f'<div style="position:absolute;left:64px;top:{y}px;width:610px;height:{alto_fila - 24}px;display:flex;align-items:center;gap:34px">'
                     f'{insignia}<div class="cuerpo" data-fit="34,60" style="position:static;flex:1;height:100%;display:flex;align-items:center">'
                     f'<span>{tx(t)}</span></div></div>')
    partes = [_tit(h["titulo"], 64, 124, 952, 220, (70, 150), sem.uniform(-1.4, 1.0))] + filas
    partes.append(_pj(h["personaje"], 700, 780, 340, fondo, ctx))
    partes.append(_puntos(ctx["n"], ctx["total"]) + _marca(ctx) + _sigue(h, "izq"))
    return _hoja(f"f-{fondo}", "".join(partes))


def frase(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = _fondo(h, "color")
    partes = []
    if h.get("titulo"):
        partes.append(_tag(h["titulo"], 64, 70, sem.uniform(-4, -1)))
    partes.append(_tit(h["frase"], 64, 190, 952, 640, (64, 190), sem.uniform(-1.2, .6)))
    lado = h.get("lado", "der")
    x = 640 if lado == "der" else 60
    sobre = {"color": "claro", "claro": "color", "oscuro": "color"}[fondo]
    partes.append(_mancha_svg(mancha(sem, x + 190, 1060, 330, 250), f"var(--{'b' if fondo == 'color' else 'o'})"))
    partes.append(_pj(h["personaje"], x, 800, 380, sobre, ctx))
    partes.append(_puntos(ctx["n"], ctx["total"]) + _marca(ctx) + _sigue(h, "izq" if lado == "der" else "der"))
    return _hoja(f"f-{fondo}", "".join(partes))


def cierre(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fu = "<br>".join(e(f) for f in h["fuentes"])
    separador = ola(sem, 996, 1004)
    partes = []
    if h.get("etiqueta"):
        partes.append(_tag(h["etiqueta"], 64, 64, sem.uniform(-4, -1)))
    partes.append(_tit(h["frase"], 64, 170, 610, 560, (60, 170), sem.uniform(-1.2, .6), color="var(--b)"))
    partes.append(_mancha_svg(mancha(sem, 870, 780, 200, 215), "var(--o)", "var(--b)", 8))
    partes.append(_pj(h["personaje"], 724, 580, 300, "color", ctx))
    partes.append(f'<svg class="fx" viewBox="0 0 {ANCHO} {ALTO}"><path d="{separador}" style="fill:none;stroke:var(--b);stroke-width:7;stroke-linecap:round" filter="url(#borde)"/></svg>')
    partes.append(f'<div class="fuentes" data-fit="19,28" style="left:64px;right:64px;top:1026px;height:136px;overflow:hidden"><span style="display:block"><b>Fuentes.</b> {fu}</span></div>')
    partes.append(f'<div class="aviso" style="left:64px;right:64px;top:1172px">{e(h["aviso"])}</div>')
    partes.append(_puntos(ctx["n"], ctx["total"]))
    partes.append(f'<div class="marca" style="bottom:28px;font-size:34px">{e(ctx["tema"]["nombre"])}</div>')
    return _hoja("f-oscuro", "".join(partes))


def unica(h: dict, ctx: dict) -> str:
    """Imagen suelta: se tiene que entender sola, sin deslizar. Titular, personaje que lo dice, un dato y la fuente."""
    sem = ctx["sem"]
    fondo = _fondo(h, "color")
    sobre = _sobre_blob(fondo)
    partes = [_mancha_svg(mancha(sem, 620, 860, 470, 250), f"var(--{'b' if fondo == 'color' else 'o'})")]
    if h.get("etiqueta"):
        partes.append(_tag(h["etiqueta"], 64, 64, sem.uniform(-5, -2)))
    partes.append(_tit(h["titular"], 64, 160, 952, 480, (90, 240), sem.uniform(-1.6, -.4)))
    partes.append(_pj(h["personaje"], 600, 600, 400, sobre, ctx))
    if h.get("dice"):
        partes.append(_globo(sem, 50, 690, 500, 250, h["dice"], (610, 840), (40, 84)))
    if h.get("sello"):
        partes.append(_sello(h["sello"], 60, 910, 200, sem.uniform(-14, -6), (34, 66)))
    partes.append(f'<div class="cuerpo" data-fit="30,48" style="left:64px;top:1148px;width:952px;height:96px;'
                  f'display:flex;align-items:center"><span>{tx(h["dato"])}</span></div>')
    if h.get("fuente"):
        partes.append(f'<div class="fuentes" style="left:64px;right:64px;top:1252px;font-size:24px;opacity:.9">{e(h["fuente"])}</div>')
    partes.append(f'<div class="marca" style="left:auto;right:64px;top:70px;bottom:auto;font-size:36px">{e(ctx["tema"]["nombre"])}</div>')
    return _hoja(f"f-{fondo}", "".join(partes))


def voz(h: dict, ctx: dict) -> str:
    """Una voz real: la cita literal de una persona que participó en un estudio publicado, con un muñeco en lugar de cara.
    Dice quién habla (tal como lo describe el estudio) y de qué estudio sale; la cita no se toca, va entre comillas y traducida."""
    sem = ctx["sem"]
    fondo = _fondo(h, "claro")
    sobre = _sobre_blob(fondo)
    partes = [_mancha_svg(mancha(sem, 540, 1010, 400, 250), f"var(--{'b' if fondo == 'color' else 'o'})")]
    partes.append(_tag(h.get("etiqueta", "Voz real"), 64, 64, sem.uniform(-4, -1)))
    partes.append(_globo(sem, 50, 190, 980, 640, "«" + h["cita"] + "»", (540, 930), (34, 72)))
    partes.append(_pj(h["personaje"], 370, 866, 340, sobre, ctx))
    partes.append(f'<div class="tag" style="left:64px;top:1060px;transform:rotate({sem.uniform(-3, -1):.1f}deg);font-size:44px;white-space:normal;max-width:300px;line-height:1.05;'
                  f'padding:12px 30px 18px">{e(h["quien"])}</div>')
    partes.append(f'<div class="tag" style="left:735px;top:1060px;transform:rotate({sem.uniform(1, 3):.1f}deg);font-size:38px;white-space:normal;max-width:300px;line-height:1.05;'
                  f'padding:12px 28px 16px">{e(h["estudio"])}<br><span style="font-size:28px">cita literal, traducida</span></div>')
    partes.append(_puntos(ctx["n"], ctx["total"]) + _marca(ctx) + _sigue(h, "izq"))
    return _hoja(f"f-{fondo}", "".join(partes))


PLANTILLAS = {"voz": voz, "portada": portada, "dice": dice, "cifra": cifra, "versus": versus, "lista": lista, "frase": frase,
              "cierre": cierre, "unica": unica}


def _css_virales() -> str:
    import plantillas_f
    import plantillas_v
    return plantillas_v.CSS + plantillas_f.CSS


import plantillas_v as _pv  # noqa: E402  (plantillas virales: respuesta, ranking, sino, antes_ahora; usan los ayudantes de arriba)
PLANTILLAS.update(_pv.registrar(sys.modules[__name__]))

import plantillas_f as _pf  # noqa: E402  (dirección F, 12-oct-2026: fotos reales de comida; mismas reglas de registro que las virales)
PLANTILLAS.update(_pf.registrar(sys.modules[__name__]))


# ------------------------------------------------------------------ marcador y círculo a mano (después de ajustar el texto)

JS_DECORA = r"""
() => {
  const ns = 'http://www.w3.org/2000/svg';
  const prng = (s) => () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  const color = (el, v) => getComputedStyle(el).getPropertyValue(v).trim();
  let k = 0;
  const poner = (mark, svg) => {
    const cont = mark.closest('[data-fit],[data-fitw],.tit,.cuerpo') || mark.parentElement;
    (cont.parentElement).insertBefore(svg, cont);
  };
  document.querySelectorAll('mark.m').forEach(m => {
    const r = prng(777 + 31 * (++k));
    const cont = m.closest('[data-fit],[data-fitw],.tit,.cuerpo') || m.parentElement;
    const pr = cont.parentElement.getBoundingClientRect();
    const sv = document.createElementNS(ns, 'svg');
    sv.setAttribute('class', 'fx'); sv.setAttribute('viewBox', `0 0 ${pr.width} ${pr.height}`);
    sv.style.cssText = `position:absolute;left:0;top:0;width:${pr.width}px;height:${pr.height}px;overflow:visible;pointer-events:none;filter:url(#aspero)`;
    const col = color(m, '--mk');
    for (const q of m.getClientRects()) {
      const x = q.left - pr.left, y = q.top - pr.top, w = q.width, h = q.height, g = h * 1.08;
      const j = () => (r() - .5) * h * .1;
      const p = document.createElementNS(ns, 'path');
      const ent = Math.max(0, g / 2 - 14);      // los extremos del trazo son redondos: se mete su radio para que no sobren
      p.setAttribute('d', `M${x - 6 + ent} ${y + h * .46 + j()} Q${x + w * .5} ${y + h * .4 + j()} ${x + w + 8 - ent} ${y + h * .44 + j()}`);
      p.setAttribute('style', `fill:none;stroke:${col};stroke-width:${g};stroke-linecap:round`);
      sv.appendChild(p);
    }
    poner(m, sv);
  });
  document.querySelectorAll('mark.c').forEach(m => {
    const r = prng(4242 + 17 * (++k));
    const cont = m.closest('[data-fit],[data-fitw],.tit,.cuerpo') || m.parentElement;
    const pr = cont.parentElement.getBoundingClientRect();
    const rects = Array.from(m.getClientRects());
    const x0 = Math.min(...rects.map(q => q.left)) - pr.left, x1 = Math.max(...rects.map(q => q.right)) - pr.left;
    const y0 = Math.min(...rects.map(q => q.top)) - pr.top, y1 = Math.max(...rects.map(q => q.bottom)) - pr.top;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, rx = (x1 - x0) / 2 + 30, ry = (y1 - y0) / 2 + 18;
    const pts = [];
    const n = 40, a0 = -Math.PI * .6 + (r() - .5) * .4;
    for (let i = 0; i <= n; i++) {
      const t = a0 + (i / n) * Math.PI * 2.16;
      const e = 1 + (r() - .5) * .05 + (i / n - .5) * .09;
      pts.push([cx + rx * e * Math.cos(t), cy + ry * e * Math.sin(t)]);
    }
    const sv = document.createElementNS(ns, 'svg');
    sv.setAttribute('class', 'fx'); sv.setAttribute('viewBox', `0 0 ${pr.width} ${pr.height}`);
    sv.style.cssText = `position:absolute;left:0;top:0;width:${pr.width}px;height:${pr.height}px;overflow:visible;pointer-events:none;filter:url(#aspero)`;
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', 'M' + pts.map(q => q[0].toFixed(1) + ' ' + q[1].toFixed(1)).join(' L'));
    p.setAttribute('style', `fill:none;stroke:${color(m, '--ci')};stroke-width:9;stroke-linecap:round;stroke-linejoin:round`);
    sv.appendChild(p);
    poner(m, sv);
  });
}
"""


def pagina(pieza: dict, i: int, carpeta_fuentes: Path, tema: dict | None = None) -> str:
    tema = tema or T.cargar(pieza.get("cuenta"))
    hojas = pieza["hojas"]
    h = hojas[i]
    ctx = {"n": i + 1, "total": len(hojas), "sem": semilla(pieza["id"], i + 1), "tema": tema, "angular": bool(pieza.get("angular"))}
    cuerpo = PLANTILLAS[h["plantilla"]](h, ctx)
    if h.get("fuente") and h["plantilla"] not in ("unica", "cierre"):
        cuerpo = cuerpo[:-len("</div>")] + f'<div class="fm">{e(h["fuente"])}</div></div>'
    # las hojas de la dirección F (fotos reales) no llevan el grano de papel: lo pone el dibujo a mano, no la fotografía
    grano = "" if h["plantilla"].startswith("f_") else (
        f'<div class="grano" style="position:absolute;inset:0;background-image:url(\'{_grano(ctx["sem"])}\')"></div>')
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><style>{css(carpeta_fuentes, tema)}</style></head><body>'
            f'{_defs(ctx["sem"])}{cuerpo}{grano}'
            f'</body></html>')
