"""Plantillas de la dirección G de Vivax Nutrición (15-oct-2026): mismos colores de marca, formatos que se alternan y menos «plantilla».

Nace de las 45 capturas de la búsqueda «dieta» que pasó José el 15-oct (ver datos/propuestas/direccion_g_spec_2026-10-15.md): cambios
(«lo que evitas / lo que comes»), rankings hechos con la propia comida, «¿cuál lleva más?», una afirmación grande sobre un color liso,
menús por día. Lo que cambia respecto a la dirección F:

- Tres fondos de la marca, no uno: negro (texto blanco, marcas en oliva), oliva (texto negro, marcas en negro sobre un trazo blanco) y
  blanco (texto negro, marcas en negro sobre un trazo oliva). Los tres colores siguen siendo los de la marca: el control de paleta no cambia.
- Sin «DESLIZA», sin puntos de paginación y sin pastilla en cada hoja; la marca pequeña solo en la portada y en el cierre.
- Fotos en círculo (un plato visto desde arriba es redondo), una etiqueta ligeramente girada, una flecha o un subrayado de trazo irregular.
- Las fuentes y los créditos de las fotos van en la última hoja (g_cierre), no en el pie.

Se registran en plantillas_d.PLANTILLAS al final de ese módulo, como las de F. Los textos entre llaves ({así}) son la marca de énfasis.
"""
from __future__ import annotations

import random
import re
import sys

import plantillas_f as F

D = None          # el módulo plantillas_d, que se pasa en registrar()

FONDOS = ("negro", "oliva", "blanco")

CSS = """
.gf{--ac:var(--o)}
.gf.negro{background:#000;color:#fff;--mk:transparent;--ci:#fff;--anillo:#000}
.gf.negro mark.m,.gf.negro mark.c{color:var(--o)}
.gf.oliva{background:var(--o);color:#000;--mk:#fff;--ci:#000;--anillo:var(--o)}
.gf.oliva mark.m,.gf.oliva mark.c{color:#000}
.gf.blanco{background:#fff;color:#000;--mk:var(--o);--ci:#000;--anillo:#fff}
.gf.blanco mark.m,.gf.blanco mark.c{color:#000}
.gf .foto{position:absolute;overflow:hidden;background:#222}
.gf .foto img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:cover}
.gf .velo{position:absolute;pointer-events:none}
.gf .gt{position:absolute;font-family:var(--ft);font-weight:900;text-transform:uppercase;line-height:.94;letter-spacing:-.004em;display:flex;
        padding:0 0 .16em 0;overflow:visible}
.gf .gt > span{display:block;width:100%}
.gf .gt.fin{flex-direction:column;align-items:stretch}
.gf .gt.fin > span{margin-top:auto}
.gf .gc{position:absolute;font-family:var(--fc);font-weight:700;line-height:1.16;overflow:visible}
.gf .gc > span{display:block;width:100%}
.gf .num{position:absolute;display:flex;align-items:center;justify-content:center;font-family:var(--ft);font-weight:900;line-height:.8;
         white-space:nowrap;text-transform:none;overflow:visible}
.gf .num span.u{align-self:flex-end;margin-left:.1em;margin-bottom:.04em;text-transform:none}
.gf .gm{position:absolute;font:900 26px/1 var(--ft);text-transform:uppercase;letter-spacing:.09em}
.gf .peg{position:absolute;font-family:var(--ft);font-weight:900;text-transform:uppercase;line-height:1;white-space:nowrap;
         display:flex;align-items:center;justify-content:center;padding:0 .5em .08em;border-radius:20px;overflow:visible}
.gf .peg.neg{background:#000;color:#fff}
.gf .peg.oli{background:var(--o);color:#000}
.gf .peg.bla{background:#fff;color:#000}
.gf .peg.neg.cl{color:var(--o)}
.gf .anillo{position:absolute;border-radius:50%;pointer-events:none}
"""


def _fondo(h: dict) -> str:
    f = h.get("fondo") or "negro"
    if f not in FONDOS:
        raise F.ErrorFoto(f"fondo «{f}» no vale: {', '.join(FONDOS)}")
    return f


def _hoja(interior: str, fondo: str, ctx: dict | None = None) -> str:
    return f'<div class="hoja gf {fondo}">{interior}</div>'


def _gt(texto, x, y, w, alto, fit, align="left", anclar="start", color: str = "", extra: str = "") -> str:
    """Titular en versales condensadas. `texto` puede ser cadena o lista de líneas."""
    html = "<br>".join(D.tx(t) for t in texto) if isinstance(texto, (list, tuple)) else D.tx(texto)
    col = f"color:{color};" if color else ""
    if anclar == "end":
        return (f'<div class="gt fin" data-fit="{fit[0]},{fit[1]}" style="left:{x}px;top:{y}px;width:{w}px;height:{alto}px;'
                f'text-align:{align};{col}{extra}"><span>{html}</span></div>')
    al = {"left": "flex-start", "center": "center"}.get(align, "flex-start")
    return (f'<div class="gt" data-fit="{fit[0]},{fit[1]}" style="left:{x}px;top:{y}px;width:{w}px;height:{alto}px;text-align:{align};'
            f'align-items:{al};{col}{extra}"><span>{html}</span></div>')


def _gc(texto, x, y, w, alto, fit, align="left", color: str = "", centrar_v: bool = False, peso: int = 0) -> str:
    col = f"color:{color};" if color else ""
    pw = f"font-weight:{peso};" if peso else ""
    al = "center" if centrar_v else "flex-start"
    return (f'<div class="gc" data-fit="{fit[0]},{fit[1]}" style="left:{x}px;top:{y}px;width:{w}px;height:{alto}px;display:flex;align-items:{al};'
            f'text-align:{align};{col}{pw}"><span>{D.tx(texto)}</span></div>')


def _marca(fondo: str, x: int = 56, y: int = 1286, color: str | None = None) -> str:
    c = color or ("#fff" if fondo == "negro" else "#000")
    return f'<div class="gm" style="left:{x}px;top:{y}px;color:{c}">Vivax Nutrición</div>'


def _svg(inner: str) -> str:
    return ('<svg class="fx" viewBox="0 0 1080 1350" style="position:absolute;left:0;top:0;width:1080px;height:1350px;overflow:visible;'
            f'pointer-events:none;filter:url(#aspero)">{inner}</svg>')


def _subrayado(r: random.Random, x: float, y: float, w: float, color: str, sw: float = 10) -> str:
    """Una raya hecha a mano: no es recta, se afina al final."""
    j = lambda a: (r.random() - .5) * a
    d = (f"M{x + j(6):.1f} {y + j(6):.1f} C{x + w * .3:.1f} {y - 7 + j(8):.1f} {x + w * .62:.1f} {y + 6 + j(8):.1f} {x + w:.1f} {y - 2 + j(7):.1f}")
    return _svg(f'<path d="{d}" style="fill:none;stroke:{color};stroke-width:{sw};stroke-linecap:round"/>')


def _flecha_d(r: random.Random, x0: float, y0: float, x1: float, y1: float, cabeza: float = 34) -> str:
    j = lambda a: (r.random() - .5) * a
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2 + j(16) - 8
    cuerpo = f"M{x0 + j(4):.1f} {y0 + j(4):.1f} Q{mx:.1f} {my:.1f} {x1:.1f} {y1 + j(3):.1f}"
    a = (x1 - 1.0 * cabeza, y1 - cabeza * .78 + j(5))
    b = (x1 - 1.0 * cabeza, y1 + cabeza * .78 + j(5))
    return f"{cuerpo} M{a[0]:.1f} {a[1]:.1f} L{x1:.1f} {y1:.1f} L{b[0]:.1f} {b[1]:.1f}"


def _flecha(r, x0, y0, x1, y1, color, sw=12) -> str:
    return _svg(f'<path d="{_flecha_d(r, x0, y0, x1, y1)}" style="fill:none;stroke:{color};stroke-width:{sw};stroke-linecap:round;stroke-linejoin:round"/>')


def _peg(texto: str, x: int, y: int, ancho: int, alto: int, fit: tuple[int, int], tono: str, rot: float = -3, cl: bool = False) -> str:
    """Una etiqueta girada (tono: neg, oli, bla)."""
    return (f'<div class="peg {tono}{" cl" if cl else ""}" data-fit="{fit[0]},{fit[1]}" style="left:{x}px;top:{y}px;width:{ancho}px;height:{alto}px;'
            f'transform:rotate({rot}deg)">{D.tx(texto)}</div>')


def _circulo(clave: str, cx: float, cy: float, d: float, foco=None, anillo: str = "", zoom=None) -> str:
    extra = f"box-shadow:0 0 0 {anillo};" if anillo else ""
    return F._foto(clave, cx - d / 2, cy - d / 2, d, d, d / 2, foco, extra, zoom)


def _l_marca(t: str) -> str:
    """El texto sin llaves: la respuesta de una palabra siempre va marcada."""
    return re.sub(r"[{}]", "", str(t))


def _ancho_peg(texto: str, tam: int) -> int:
    return int(min(940, 70 + len(re.sub(r"[{}]", "", texto)) * tam * .5))


# ------------------------------------------------------------------ g_cambio: una sustitución por hoja
def g_cambio(h: dict, ctx: dict) -> str:
    r = random.Random(ctx["sem"].random())
    p = ['<div class="velo" style="left:540px;top:0;width:540px;height:1350px;background:var(--o)"></div>']
    gana = h.get("ganador")
    for lado, x0, claro in (("izq", 0, True), ("der", 540, False)):
        d = h[lado]
        c_txt = "#fff" if claro else "#000"
        cx = x0 + 270
        p.append(_gt(d["etiqueta"], x0 + 50, 62, 440, 160, (40, 68), color=c_txt))
        sub = "var(--o)" if claro else "#000"
        p.append(_subrayado(r, x0 + 52, 236, 190, sub, 9))
        anillo = "10px " + (("var(--o)" if claro else "#fff") if gana == lado else ("#fff" if claro else "#000"))
        p.append(_circulo(d["foto"], cx, 490 + (6 if claro else -10), 430, d.get("foco"), anillo, d.get("zoom")))
        p.append(_gt(d["nombre"], x0 + 40, 745, 460, 130, (34, 62), align="center", color=c_txt, extra="justify-content:center;"))
        val = F._valor(d["valor"], .46)
        col = ("var(--o)" if claro else "#000") if gana == lado else c_txt
        if gana == lado:        # sobre el oliva la marca no puede ser oliva: texto negro y círculo blanco
            val = f'<mark class="c" style="color:{col};--ci:#fff">{val}</mark>'
        p.append(f'<div class="num" data-fitw="110,214" style="left:{x0 + 20}px;top:888px;width:500px;height:200px;color:{col}">'
                 f'<span style="display:flex;align-items:flex-end;white-space:nowrap">{val}</span></div>')
        p.append(_gc(d["texto"], x0 + 40, 1114, 460, 110, (26, 36), align="center", color=c_txt, peso=700))
    # el círculo de la flecha: oliva sobre el negro, con un aro negro que se ve también sobre el oliva
    p.append('<div class="velo" style="left:465px;top:395px;width:150px;height:150px;border-radius:50%;background:var(--o);border:9px solid #000"></div>')
    p.append(_svg(f'<path d="{_flecha_d(r, 497, 472, 585, 466, 30)}" style="fill:none;stroke:#000;stroke-width:13;stroke-linecap:round;stroke-linejoin:round"/>'))
    p.append(_marca("negro", 48, 1290))
    p.append('<div class="gm" style="right:50px;top:1290px;color:#000;text-transform:none;letter-spacing:.04em">@vivax_nutri</div>')
    return _hoja("".join(p), "negro", ctx)


# ------------------------------------------------------------------ g_pictograma: un ranking hecho de comida
_PASOS = (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000)


def _paso_auto(vmax: float, k_max: int = 7) -> float:
    for p in _PASOS:
        if round(vmax / p) <= k_max:
            return p
    return _PASOS[-1]


def _num_es(v: float) -> str:
    return (f"{v:.1f}" if v != int(v) else str(int(v))).replace(".", ",")


def g_pictograma(h: dict, ctx: dict) -> str:
    r = random.Random(ctx["sem"].random())
    fondo = _fondo(h)
    filas = h["filas"][:6]
    n = len(filas)
    vals = [F._num(f["valor"]) for f in filas]
    vmax = max(vals) or 1.0
    paso = float(h.get("paso") or _paso_auto(vmax))
    cuenta = [0 if v <= 0 else max(1, int(v / paso + .5)) for v in vals]       # mitades hacia arriba (round() de Python las lleva al par: 2,5 daba 2); un 0 no dibuja círculos
    kmax = max(cuenta) or 1
    um = re.match(r"^\s*[\d.,]+\s*(\S+)", filas[0]["valor"])
    unidad = um.group(1) if um else ""
    c_txt = "#fff" if fondo == "negro" else "#000"
    c_ac = "var(--o)" if fondo == "negro" else "#000"
    anillo_ganador = "var(--o)" if fondo == "negro" else "#000"
    aro = "#000" if fondo == "negro" else ("var(--o)" if fondo == "oliva" else "#fff")
    p = [_gt(h["titulo"], 64, 52, 952, 250, (70, 132), color=c_txt)]
    if h.get("unidad"):
        p.append(_gc(h["unidad"], 64, 318, 952, 54, (28, 40), color=c_txt, centrar_v=True, peso=700))
        p.append(_subrayado(r, 66, 378, 230, c_ac, 8))
    base = 1062
    col_w = 952 / n
    area = 560
    fd = 1.45 if n <= 4 else 1.25                # cuánto mide el círculo respecto al paso: con pocas columnas, círculos más grandes que se solapan
    pitch = min(124.0, area / (kmax - 1 + fd))
    for k, (f, v, c) in enumerate(zip(filas, vals, cuenta)):
        cx = 64 + col_w * (k + .5)
        d = min(col_w - 16, round(pitch * fd))
        pc = pitch
        gana = k == 0
        for j in range(c):                      # de abajo arriba: el de arriba tapa un poco al de abajo
            cy = base - d / 2 - j * pc
            anillo = f"5px {anillo_ganador}" if gana else f"5px {aro}"
            p.append(_circulo(f["foto"], cx, cy, d, f.get("foco"), anillo, f.get("zoom")))
        alto_pila = (c - 1) * pc + d if c else 0
        val = F._valor(f["valor"], .46)
        if gana and n <= 5:                      # con 6 columnas el trazo a mano de la marca invade la cifra del vecino
            val = f'<mark class="c">{val}</mark>'
        ytop = base - alto_pila - 118
        p.append(f'<div class="num" data-fitw="34,84" style="left:{cx - col_w / 2 - 2:.0f}px;top:{ytop:.0f}px;width:{col_w + 4:.0f}px;height:96px;'
                 f'color:{c_ac if gana else c_txt}"><span style="display:flex;align-items:flex-end;white-space:nowrap">{val}</span></div>')
        p.append(_gt(f["nombre"], cx - col_w / 2 + 10, base + 26, col_w - 20, 120, (20, 30), align="center", color=c_txt, extra="justify-content:center;"))
    # línea base de trazo irregular
    p.append(_subrayado(r, 56, base + 8, 970, c_ac, 7))
    ley = h.get("leyenda") or f"cada círculo = {_num_es(paso)} {unidad}, redondeado"
    p.append(f'<div class="foto" style="left:64px;top:1214px;width:34px;height:34px;border-radius:50%;background:{c_ac}"></div>')
    p.append(_gc(ley, 112, 1204, 800, 54, (24, 32), color=c_txt, centrar_v=True, peso=700))
    p.append(_marca(fondo, 56, 1298))
    return _hoja("".join(p), fondo, ctx)


# ------------------------------------------------------------------ g_eleccion: «¿cuál lleva más…?» con tres opciones
def g_eleccion(h: dict, ctx: dict) -> str:
    r = random.Random(ctx["sem"].random())
    fondo = _fondo(h)
    ops = h["opciones"][:3]
    c_txt = "#fff" if fondo == "negro" else "#000"
    c_ac = "var(--o)" if fondo == "negro" else "#000"
    oculto = bool(h.get("oculto"))
    gana = h.get("ganador")
    p = [_gt(h["titulo"], 64, 60, 952, 330, (76, 210), color=c_txt)]
    d, y0 = 296, 470
    for k, o in enumerate(ops):
        x = 64 + k * (d + 32)
        cx, cy = x + d / 2, y0 + d / 2 + (-14 if k == 1 else (10 if k == 2 else 0))
        es_ganador = gana == k and not oculto
        anillo = f"9px {c_ac}" if es_ganador else f"9px {'#fff' if fondo == 'negro' else '#000'}"
        p.append(_circulo(o["foto"], cx, cy, d, o.get("foco"), anillo, o.get("zoom")))
        # número de la opción: se contesta «la 2» en los comentarios
        nb = "var(--o)" if fondo == "negro" else "#000"
        nt = "#000" if fondo == "negro" else ("#fff" if fondo == "blanco" else "var(--o)")
        p.append(f'<div class="num" style="left:{cx - d / 2 - 6:.0f}px;top:{cy - d / 2 - 14:.0f}px;width:84px;height:84px;border-radius:50%;'
                 f'background:{nb};color:{nt};font-size:62px;line-height:1"><span style="display:block;margin-top:-6px">{k + 1}</span></div>')
        p.append(_gt(o["nombre"], x - 6, 820, d + 12, 128, (30, 58), align="center", color=c_txt, extra="justify-content:center;"))
        if oculto:
            fondo_i = "var(--o)" if fondo == "negro" else "#000"
            col_i = "#000" if fondo == "negro" else ("#fff" if fondo == "blanco" else "var(--o)")
            p.append(f'<div class="num" style="left:{cx - 62:.0f}px;top:970px;width:124px;height:124px;border-radius:50%;background:{fondo_i};'
                     f'color:{col_i};font-size:100px;line-height:1"><span style="display:block;margin-top:-8px">?</span></div>')
        else:
            val = F._valor(o["valor"], .46)
            if es_ganador:
                val = f'<mark class="c">{val}</mark>'
            p.append(f'<div class="num" data-fitw="60,130" style="left:{x - 6}px;top:960px;width:{d + 12}px;height:140px;color:{c_ac if es_ganador else c_txt}">'
                     f'<span style="display:flex;align-items:flex-end;white-space:nowrap">{val}</span></div>')
    if h.get("texto"):
        p.append(_gc(h["texto"], 64, 1130, 952, 120, (28, 42), align="center", color=c_txt, centrar_v=True, peso=700))
    p.append(_subrayado(r, 64, 396, 260, c_ac, 9))
    p.append(_marca(fondo, 56, 1296))
    return _hoja("".join(p), fondo, ctx)


# ------------------------------------------------------------------ g_afirmacion: una frase grande sobre un color liso
def g_afirmacion(h: dict, ctx: dict) -> str:
    r = random.Random(ctx["sem"].random())
    fondo = _fondo(h)
    c_txt = "#fff" if fondo == "negro" else "#000"
    c_ac = "var(--o)" if fondo == "negro" else "#000"
    p = []
    top = 80
    if h.get("kicker"):
        tono = "oli" if fondo == "negro" else "neg"
        tam = 56
        p.append(_peg(h["kicker"], 64, 74, _ancho_peg(h["kicker"], tam), 96, (36, tam), tono, -3, cl=(fondo == "blanco")))
        top = 230
    hay_foto = bool(h.get("foto"))
    ancho = 600 if hay_foto and h.get("texto") else 952
    base_texto = 1002
    if h.get("respuesta"):
        bajar = 60 if h.get("kicker") else 0          # con etiqueta, la mancha de la respuesta no tiene que tocar la píldora (revisión del 15-oct)
        p.append(_gt("{" + _l_marca(h["respuesta"]) + "}", 56, top + 20 + bajar, 968, 620, (200, 470), color=c_txt, extra="align-items:center;"))
        yt = top + 700
    else:
        alto_t = (base_texto - 40 - top) if h.get("texto") else (1230 - top)
        p.append(_gt(h["titular"], 64, top, 952, alto_t, (84, 270), color=c_txt, extra="align-items:center;"))
        yt = base_texto
    if h.get("texto"):
        p.append(_subrayado(r, 66, yt - 24, 210, c_ac, 9))
        p.append(_gc(h["texto"], 64, yt + 6, ancho, 250, (30, 46), color=c_txt, peso=700))
    if hay_foto:
        cx, cy = 846, 1086
        p.append(_circulo(h["foto"], cx, cy, 290, h.get("foco"), f"10px {'#fff' if fondo == 'negro' else '#000'}", h.get("zoom")))
    p.append(_marca(fondo, 56, 1296))
    return _hoja("".join(p), fondo, ctx)


# ------------------------------------------------------------------ g_dia: el plato a pantalla completa con su día
def g_dia(h: dict, ctx: dict) -> str:
    p = [F._foto(h["foto"], 0, 0, 1080, 1350, 0, h.get("foco"), "", h.get("zoom"))]
    p.append('<div class="velo" style="inset:0;background:linear-gradient(180deg,rgba(0,0,0,.28) 0%,rgba(0,0,0,0) 26%,rgba(0,0,0,0) 52%,rgba(0,0,0,.9) 100%)"></div>')
    ancho = int(min(940, 100 + len(h["dia"]) * 74))
    p.append(_peg(h["dia"], 56, 74, ancho, 190, (84, 150), "bla", -3))
    p.append(_gt(h["titulo"], 64, 930, 952, 220, (52, 108), anclar="end", color="#fff", extra="text-shadow:0 3px 20px rgba(0,0,0,.4);"))
    if h.get("texto"):
        p.append(_gc(h["texto"], 64, 1178, 952, 80, (28, 40), color="#fff", peso=700))
    return _hoja("".join(p), "negro", ctx)


# ------------------------------------------------------------------ g_cierre: el cierre es la acción
def g_cierre(h: dict, ctx: dict) -> str:
    r = random.Random(ctx["sem"].random())
    fondo = _fondo(h)
    c_txt = "#fff" if fondo == "negro" else "#000"
    c_ac = "var(--o)" if fondo == "negro" else "#000"
    p = [_gt(h["frase"], 64, 80, 952, 820, (84, 250), color=c_txt, extra="align-items:center;")]
    fotos = [x if isinstance(x, dict) else {"foto": x} for x in (h.get("fotos") or [])][:3]
    if fotos:
        d = 190
        for k, x in enumerate(fotos):
            cx = 64 + d / 2 + k * (d + 34)
            p.append(_circulo(x["foto"], cx, 800, d, x.get("foco"), f"6px {'#fff' if fondo == 'negro' else '#000'}", x.get("zoom")))
    p.append(_subrayado(r, 66, 940, 300, c_ac, 9))
    fuentes = [x for x in (h.get("fuentes") or []) if str(x).strip()]      # 15-oct: José pide no poner las fuentes en las publicaciones; son opcionales
    if fuentes:
        fu = "<br>".join(D.e(x) for x in fuentes)
        et = "Fuente." if len(fuentes) == 1 else "Fuentes."
        p.append(f'<div class="gc" data-fit="18,26" style="left:64px;top:966px;width:952px;height:66px;color:{c_txt};font-weight:500"><span><b style="font-weight:800">{et}</b> {fu}</span></div>')
    if h.get("creditos"):
        p.append(f'<div class="gc" data-fit="15,22" style="left:64px;top:1040px;width:952px;height:150px;color:{c_txt};font-weight:500;opacity:.9"><span>{D.e(h["creditos"])}</span></div>')
    p.append(f'<div class="gc" data-fit="20,27" style="left:64px;top:1200px;width:952px;height:66px;color:{c_txt};font-weight:700"><span>{D.e(h["aviso"])}</span></div>')
    p.append(_marca(fondo, 56, 1298))
    return _hoja("".join(p), fondo, ctx)


PLANTILLAS = {"g_cambio": g_cambio, "g_pictograma": g_pictograma, "g_eleccion": g_eleccion, "g_afirmacion": g_afirmacion, "g_dia": g_dia,
              "g_cierre": g_cierre}


def registrar(modulo_d) -> dict:
    global D
    D = modulo_d
    return PLANTILLAS
