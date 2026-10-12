"""Plantillas virales de la dirección D (11-oct-2026): respuesta, ranking, sino y antes_ahora.

Salen del análisis de las publicaciones virales que recogió José (datos/propuestas/virales_analisis_2026-10-11.md): el resultado
va primero (una palabra grande, una cifra grande), la comida o el personaje ocupan la hoja y el estudio no sale en pantalla (va en el pie).
Las comparaciones de corrientes usan `versus`, que ya existía (izq y der con su cabecera y su tesis).

Se registran en plantillas_d.PLANTILLAS al final de ese módulo. Los alimentos se dibujan como pegatinas (emoji con contorno blanco y
sombra dura): el campo `icono` no es texto de la pieza y no pasa por los controles de texto.
"""
from __future__ import annotations

import re

D = None          # el módulo plantillas_d, que se pasa en registrar() (así no hay importación circular)

CSS = """
.ico{position:absolute;line-height:1;text-align:center;font-family:"Segoe UI Emoji","Noto Color Emoji","Apple Color Emoji",sans-serif;
     filter:drop-shadow(4px 0 0 var(--b)) drop-shadow(-4px 0 0 var(--b)) drop-shadow(0 4px 0 var(--b)) drop-shadow(0 -4px 0 var(--b)) drop-shadow(8px 8px 0 var(--n))}
.val{position:absolute;display:flex;align-items:center;justify-content:flex-end;font-family:var(--ft);font-weight:900;line-height:.9;
     white-space:nowrap;text-shadow:5px 5px 0 var(--sh)}
.fila-n{position:absolute;display:flex;align-items:center;font-family:var(--fc);font-weight:800;line-height:1.05}
.centro{justify-content:center;text-align:center}
"""


def _num(v) -> float:
    """El valor de una fila como número: «31», «9,6», «1.634,9» o «28 mg»."""
    t = re.sub(r"[^\d,.]", "", str(v))
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def _ico(icono: str, cx: float, cy: float, tam: int, rot: float = 0.0) -> str:
    """Un alimento como pegatina centrada en (cx, cy)."""
    w = tam * 1.4
    return (f'<div class="ico" style="left:{cx - w / 2:.0f}px;top:{cy - tam * .55:.0f}px;width:{w:.0f}px;font-size:{tam}px;'
            f'transform:rotate({rot:.1f}deg)">{icono}</div>')


def _insignia(sem, cx: float, cy: float, r: float, interior: str, relleno: str = "var(--o)") -> str:
    return (f'<svg class="fx" viewBox="0 0 {D.ANCHO} {D.ALTO}"><path d="{D.mancha(sem, cx, cy, r, r, 8, .06)}" '
            f'style="fill:{relleno};stroke:var(--n);stroke-width:8" filter="url(#aspero)"/>{interior}</svg>')


# ------------------------------------------------------------------ respuesta: la pregunta y un sí o un no enorme
def respuesta(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = D._fondo(h, "color")
    col_burst = "var(--b)" if fondo == "color" else "var(--o)"
    palabra = D.e(str(h["respuesta"]).upper())
    partes = [D._tit(h["titulo"], 64, 100, 952, 270, (56, 118), sem.uniform(-1.2, .8))]
    partes.append(D._mancha_svg(D.estallido(sem, 540, 668, 262, 360), col_burst, "var(--n)", 10))
    ancho = 700 if len(str(h["respuesta"])) <= 3 else 440          # «Sí» y «No» llenan el estallido; «Depende» o «No siempre» tienen que caber dentro
    partes.append(f'<div class="num" data-fitw="90,560" style="left:{540 - ancho // 2}px;top:440px;width:{ancho}px;height:460px;color:var(--n);--sh:var(--o)">'
                  f'<div class="cap"><div class="copia">{palabra}</div><div class="real">{palabra}</div></div></div>')
    partes.append(f'<div class="cuerpo" data-fit="28,50" style="left:64px;top:1062px;width:590px;height:180px;display:flex;align-items:center">'
                  f'<span>{D.tx(h["texto"])}</span></div>')
    partes.append(D._pj(h["personaje"], 724, 900, 310, fondo, ctx))
    partes.append(D._puntos(ctx["n"], ctx["total"]) + D._marca(ctx) + D._sigue(h, "izq"))
    return D._hoja(f"f-{fondo}", "".join(partes))


# ------------------------------------------------------------------ ranking: los alimentos con su cifra, de mayor a menor
def ranking(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = D._fondo(h, "claro")
    filas = h["filas"]
    n = len(filas)
    valores = [_num(f["valor"]) for f in filas]
    vmax = max(valores) or 1.0
    arriba, abajo = 440, (1160 if h.get("sigue") else 1240)
    rh = min(168, (abajo - arriba) // n)
    partes = [D._tit(h["titulo"], 64, 96, 952, 230, (60, 124), sem.uniform(-1.2, .8))]
    if h.get("unidad") and "100 g" not in str(h["titulo"]):       # si el título ya dice «por cada 100 g», la etiqueta de la unidad sobra
        partes.append(D._tag(h["unidad"], 64, 336, sem.uniform(-3, -1), 48))
    for k, (f, v) in enumerate(zip(filas, valores)):
        y = arriba + k * rh
        cy = y + rh * .42
        largo = max(70.0, 500.0 * v / vmax)
        tinta = "var(--n)" if k == 0 else "var(--o)"
        partes.append(_insignia(sem, 100, cy, 40, f'<text x="100" y="{cy + 20:.0f}" text-anchor="middle" style="font:900 56px var(--ft);fill:var(--n)">{k + 1}</text>',
                                "var(--b)" if k else "var(--o)"))
        partes.append(_ico(f.get("icono", ""), 218, cy - 2, 94, sem.uniform(-6, 6)))
        partes.append(f'<div class="fila-n" data-fit="26,46" style="left:290px;top:{y:.0f}px;width:470px;height:{rh * .42:.0f}px"><span>{D.tx(f["nombre"])}</span></div>')
        partes.append(f'<svg class="fx" viewBox="0 0 {D.ANCHO} {D.ALTO}"><rect x="290" y="{y + rh * .47:.0f}" width="{largo:.0f}" height="{rh * .27:.0f}" rx="{rh * .135:.0f}" '
                      f'style="fill:{tinta};stroke:var(--n);stroke-width:6" filter="url(#aspero)"/></svg>')
        partes.append(f'<div class="val" data-fitw="48,104" style="left:770px;top:{y:.0f}px;width:246px;height:{rh * .8:.0f}px"><span>{D.tx(str(f["valor"]))}</span></div>')
    partes.append(D._puntos(ctx["n"], ctx["total"]) + D._marca(ctx) + D._sigue(h, "izq"))
    return D._hoja(f"f-{fondo}", "".join(partes))


# ------------------------------------------------------------------ sino: dos columnas con tic y aspa
def sino(h: dict, ctx: dict) -> str:
    sem = ctx["sem"]
    fondo = D._fondo(h, "claro")
    partes = [D._tit(h["titulo"], 64, 96, 952, 230, (60, 124), sem.uniform(-1.2, .8))]
    partes.append(f'<div class="tag" style="left:64px;top:360px;transform:rotate({sem.uniform(-3, -1):.1f}deg);--tgb:var(--o);--tgf:var(--n);color:var(--n)">{D.e(h.get("etiqueta_si", "Sí"))}</div>')
    partes.append(f'<div class="tag" style="left:572px;top:360px;transform:rotate({sem.uniform(1, 3):.1f}deg);--tgb:var(--n);--tgf:var(--b)">{D.e(h.get("etiqueta_no", "No"))}</div>')
    corte = f"M540 350 C 515 520 565 700 538 880 C 520 980 556 1040 540 1090"
    partes.append(f'<svg class="fx" viewBox="0 0 {D.ANCHO} {D.ALTO}"><path d="{corte}" style="fill:none;stroke:var(--n);stroke-width:7;stroke-linecap:round;stroke-dasharray:2 22" filter="url(#borde)"/></svg>')
    tic = '<path d="M26 54 L44 72 L76 30" style="fill:none;stroke:var(--n);stroke-width:13;stroke-linecap:round;stroke-linejoin:round"/>'
    aspa = '<path d="M30 30 L70 70 M70 30 L30 70" style="fill:none;stroke:var(--b);stroke-width:13;stroke-linecap:round"/>'
    for col, (x0, items, signo, relleno) in enumerate(((64, h["si"], tic, "var(--o)"), (572, h["no"], aspa, "var(--n)"))):
        m = len(items)
        rh = min(150, 600 // max(m, 1))
        for k, t in enumerate(items):
            y = 480 + k * rh
            r = sem.uniform(-1.5, 1.5)
            partes.append(f'<svg style="position:absolute;left:{x0}px;top:{y}px;width:96px;height:96px;overflow:visible;transform:rotate({r:.1f}deg)" viewBox="0 0 100 100">'
                          f'<path d="{D.mancha(sem, 50, 50, 46, 46, 8, .06)}" style="fill:{relleno};stroke:var(--n);stroke-width:8" filter="url(#aspero)"/>{signo}</svg>')
            partes.append(f'<div class="fila-n" data-fit="26,46" style="left:{x0 + 114}px;top:{y - 8}px;width:340px;height:{rh - 14}px"><span>{D.tx(t)}</span></div>')
    ancho = 700 if h.get("personaje") else 952
    partes.append(f'<div class="cuerpo" data-fit="30,54" style="left:64px;top:1112px;width:{ancho}px;height:140px;display:flex;align-items:center"><span>{D.tx(h["texto"])}</span></div>')
    if h.get("personaje"):
        partes.append(D._pj(h["personaje"], 800, 1000, 230, fondo, ctx))
    partes.append(D._puntos(ctx["n"], ctx["total"]) + D._marca(ctx) + D._sigue(h, "izq"))
    return D._hoja(f"f-{fondo}", "".join(partes))


# ------------------------------------------------------------------ antes_ahora: dos alimentos (o un cambio de A por B) con un dato a cada lado
def antes_ahora(h: dict, ctx: dict) -> str:
    """Sin `etiqueta_antes` ni `etiqueta_ahora` es una comparación de dos cosas: un «vs» en medio y nada encima. Con ellas («Antes» y «Ahora»,
    por ejemplo) es un cambio y lleva flecha."""
    sem = ctx["sem"]
    fondo = D._fondo(h, "oscuro")
    partes = [D._tit(h["titulo"], 64, 96, 952, 220, (56, 118), sem.uniform(-1.2, .8))]
    cambio = bool(h.get("etiqueta_antes") or h.get("etiqueta_ahora"))
    lados = ((h["antes"], 285, h.get("etiqueta_antes", ""), -3), (h["ahora"], 795, h.get("etiqueta_ahora", ""), 3))
    partes.append(D._mancha_svg(D.mancha(sem, 285, 745, 235, 320), "var(--b)", "var(--n)", 10))
    partes.append(D._mancha_svg(D.mancha(sem, 795, 745, 235, 320), "var(--o)", "var(--n)", 10))
    for d, cx, etiqueta, rot in lados:
        if etiqueta:
            partes.append(f'<div class="tag" style="left:{cx - 90}px;top:372px;transform:rotate({rot}deg);--tgb:var(--n);--tgf:var(--b);'
                          f'{"" if fondo != "oscuro" else "--tgb:var(--b);--tgf:var(--n);"}font-size:46px">{D.e(etiqueta)}</div>')
        partes.append(_ico(d.get("icono", ""), cx, 555, 180, sem.uniform(-5, 5)))
        partes.append(f'<div class="cuerpo centro" data-fit="26,44" style="left:{cx - 205}px;top:662px;width:410px;height:84px;display:flex;color:var(--n)"><span>{D.tx(d["nombre"])}</span></div>')
        valor = D.tx(f'{d["valor"]} {d["unidad"]}' if d.get("unidad") else str(d["valor"]))
        partes.append(f'<div class="val centro" data-fitw="60,140" style="left:{cx - 205}px;top:752px;width:410px;height:150px;justify-content:center;color:var(--n);--sh:var(--o)"><span>{valor}</span></div>')
        if d.get("detalle"):
            partes.append(f'<div class="cuerpo centro" data-fit="22,34" style="left:{cx - 150}px;top:908px;width:300px;height:84px;display:flex;color:var(--n)"><span>{D.tx(d["detalle"])}</span></div>')
    if cambio:
        partes.append(f'<svg class="fx" viewBox="0 0 {D.ANCHO} {D.ALTO}"><path d="M470 745 L610 745 M565 700 L612 745 L565 790" style="fill:none;stroke:var(--b);stroke-width:16;stroke-linecap:round;stroke-linejoin:round" filter="url(#borde)"/></svg>')
    else:
        partes.append(f'<div class="tag" style="left:{D.ANCHO // 2 - 56}px;top:690px;transform:rotate({sem.uniform(-6, 6):.1f}deg);--tgb:var(--b);--tgf:var(--n);font-size:56px">vs</div>')
    partes.append(f'<div class="cuerpo centro" data-fit="30,54" style="left:64px;top:1112px;width:952px;height:140px;display:flex;align-items:center;color:var(--b)"><span>{D.tx(h["texto"])}</span></div>')
    partes.append(D._puntos(ctx["n"], ctx["total"]) + D._marca(ctx) + D._sigue(h, "izq"))
    return D._hoja(f"f-{fondo}", "".join(partes))


PLANTILLAS = {"respuesta": respuesta, "ranking": ranking, "sino": sino, "antes_ahora": antes_ahora}


def registrar(modulo_d) -> dict:
    """plantillas_d se pasa a sí mismo al terminar de cargarse; devuelve las plantillas para añadirlas a su registro."""
    global D
    D = modulo_d
    return PLANTILLAS
