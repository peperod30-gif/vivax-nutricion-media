"""Plantillas de la dirección F de Vivax Nutrición: comida real fotografiada, un titular enorme y una sola idea por hoja (12-oct-2026).

Salen de los tres zip de publicaciones de calidad que recogió José (120 capturas): la foto de la comida ocupa la hoja, el titular tiene
pocas palabras y muy grandes, los rankings llevan la cifra al lado de la comida y el que gana se ve de un vistazo. Las fotos son reales
(Wikimedia Commons, con su licencia, ver fotos.py y datos/fotos_catalogo.json): nada se genera con IA.

La paleta sigue siendo la de la marca (negro, blanco y oliva claro): lo único que no está en ella son las fotos. Cada foto lleva la
clase `foto`; el dibujo saca una segunda versión de cada hoja sin las fotos (`_sinfoto/`) y es esa la que mide el control de paleta.

Se registran en plantillas_d.PLANTILLAS al final de ese módulo, igual que las virales. Todas son de fondo negro con texto blanco y el oliva
como acento; las palabras entre llaves ({así}) salen en oliva.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

D = None          # el módulo plantillas_d, que se pasa en registrar()
RAIZ = Path(__file__).resolve().parent
CATALOGO = RAIZ / "datos" / "fotos_catalogo.json"
_CAT: dict | None = None


class ErrorFoto(Exception):
    pass


CSS = """
.ff{background:#000;color:#fff;--mk:transparent;--ci:#fff;--sh:transparent}
.ff mark.m,.ff mark.c{color:var(--o)}
.ff .foto{position:absolute;overflow:hidden;background:#111}
.ff .foto img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:cover}
.ff .velo{position:absolute;pointer-events:none}
.ff .ft{position:absolute;font-family:var(--ft);font-weight:900;text-transform:uppercase;line-height:.88;letter-spacing:-.004em;display:flex;padding:0 0 .2em 0;text-shadow:0 4px 26px rgba(0,0,0,.45)}
.ff .ft > span{display:block}
.ff .ft.fin{flex-direction:column;align-items:stretch}
.ff .ft.fin > span{margin-top:auto}
.ff .cuerpo{position:absolute;font-family:var(--fc);font-weight:700;line-height:1.16}
.ff .pastilla{position:absolute;font-family:var(--ft);font-weight:900;text-transform:uppercase;letter-spacing:.02em;background:var(--o);color:#000;
              border-radius:999px;padding:6px 30px 10px;white-space:nowrap;line-height:1}
.ff .pastilla.bl{background:#fff}
.ff .marcaf{position:absolute;left:64px;bottom:40px;font:900 34px/1 var(--ft);text-transform:uppercase;letter-spacing:.09em;color:#fff;opacity:.92}
.ff .puntosf{position:absolute;right:60px;top:70px;display:flex;gap:12px}
.ff .puntosf i{display:block;width:20px;height:20px;border-radius:50%;border:4px solid #fff}
.ff .puntosf i.on{background:#fff}
.ff .sigf{position:absolute;right:64px;bottom:34px;display:flex;align-items:center;gap:14px;font:900 40px/1 var(--ft);text-transform:uppercase;
          letter-spacing:.06em;color:var(--o)}
.ff .sigf svg{width:76px;height:34px;overflow:visible}
.ff .num{position:absolute;display:flex;align-items:center;justify-content:center;font-family:var(--ft);font-weight:900;line-height:.8;white-space:nowrap;
         text-transform:none}
.ff .num span.u{font-size:.52em;margin-left:.12em;align-self:flex-end;margin-bottom:.05em;text-transform:none;color:#fff}
.ff .fuentes{position:absolute;font:500 28px/1.3 var(--fc);color:#fff}
.ff .fuentes b{font-weight:800}
.ff .aviso{position:absolute;font:600 27px/1.3 var(--fc);color:#fff;opacity:.85}
.ff .fm{color:#fff}
.ff .letra{position:absolute;font-family:var(--ft);font-weight:900;text-transform:uppercase;line-height:.8;white-space:nowrap;display:flex;align-items:center;justify-content:flex-start;
           color:var(--o);text-shadow:0 0 0 transparent}
/* piezas angulares (pieza["angular"] = true): ninguna forma redonda; etiquetas en paralelogramo, cantos vivos y una cuña oliva en la esquina */
.ff.ang *{border-radius:0 !important}
.ff.ang .pastilla{clip-path:polygon(0 0,100% 0,calc(100% - 22px) 100%,0 100%);padding-right:52px}
.ff.ang .puntosf i{width:20px;height:10px;border-width:3px}
.ff.ang .cuna{position:absolute;right:0;top:0;width:210px;height:210px;background:var(--o);clip-path:polygon(0 0,100% 0,100% 100%)}
"""


def _catalogo() -> dict:
    global _CAT
    if _CAT is None:
        _CAT = json.loads(CATALOGO.read_text(encoding="utf-8")) if CATALOGO.exists() else {}
    return _CAT


def _foto(clave: str, x: float, y: float, w: float, h: float, radio: float = 0, foco=None, extra: str = "", zoom: float | None = None) -> str:
    """Una foto del catálogo recortada a la caja x, y, w, h (con `object-fit: cover`). `zoom` (>1) acerca la foto al punto de foco."""
    f = _catalogo().get(clave)
    if not f:
        raise ErrorFoto(f"La foto «{clave}» no está en datos/fotos_catalogo.json")
    ruta = RAIZ / f["archivo"]
    if not ruta.is_file():
        raise ErrorFoto(f"Falta el fichero de la foto «{clave}»: {ruta}")
    fx, fy = foco or f.get("foco") or (50, 50)
    z = f";transform:scale({zoom});transform-origin:{fx}% {fy}%" if zoom and zoom > 1 else ""
    return (f'<div class="foto" style="left:{x:.0f}px;top:{y:.0f}px;width:{w:.0f}px;height:{h:.0f}px;border-radius:{radio:.0f}px;{extra}">'
            f'<img alt="" src="{ruta.as_uri()}" style="object-position:{fx}% {fy}%{z}"></div>')


def _titulo(texto: str, x: int, y: int, w: int, alto: int, fit: tuple[int, int], align: str = "left", anclar: str = "start", color: str = "#fff") -> str:
    if anclar == "end":
        return (f'<div class="ft fin" data-fit="{fit[0]},{fit[1]}" style="left:{x}px;top:{y}px;width:{w}px;height:{alto}px;text-align:{align};'
                f'color:{color}"><span>{D.tx(texto)}</span></div>')
    return (f'<div class="ft" data-fit="{fit[0]},{fit[1]}" style="left:{x}px;top:{y}px;width:{w}px;height:{alto}px;text-align:{align};'
            f'align-items:flex-start;color:{color}"><span>{D.tx(texto)}</span></div>')


def _pastilla(texto: str, x: int, y: int, tam: int = 46, blanca: bool = False, versales: bool = True) -> str:
    estilo = "" if versales else "text-transform:none;letter-spacing:0;"
    t = re.sub(r"(\d) (g|mg|kg|ml|kcal)(?![\wáéíóúñ])", lambda m: m.group(1) + " " + f'<span style="text-transform:none">{m.group(2)}</span>', D.e(texto))
    return f'<div class="pastilla{" bl" if blanca else ""}" style="left:{x}px;top:{y}px;font-size:{tam}px;{estilo}">{t}</div>'


def _puntos(ctx: dict) -> str:
    return '<div class="puntosf">' + "".join(f'<i class="{"on" if k == ctx["n"] else ""}"></i>' for k in range(1, ctx["total"] + 1)) + "</div>"


def _marca(ctx: dict) -> str:
    return f'<div class="marcaf">{D.e(ctx["tema"]["nombre"])}</div>'


def _sigue(h: dict) -> str:
    if not h.get("sigue"):
        return ""
    flecha = ('<svg viewBox="0 0 96 44"><path d="M4 22 L84 22 M62 5 L86 22 L62 39" style="fill:none;stroke:currentColor;stroke-width:8;'
              'stroke-linecap:round;stroke-linejoin:round"/></svg>')
    return f'<div class="sigf"><span>{D.e(h["sigue"])}</span>{flecha}</div>'


def _hoja(interior: str, ctx: dict | None = None) -> str:
    return f'<div class="hoja ff{" ang" if (ctx or {}).get("angular") else ""}">{interior}</div>'


def _num(v) -> float:
    t = re.sub(r"[^\d,.]", "", str(v))
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)


def _valor(v: str, tam_unidad: float = .52) -> str:
    """«12 mg» -> la cifra grande y la unidad pequeña a su lado."""
    m = re.match(r"^\s*([\d.,×x]+)\s*(.*)$", str(v))
    if not m or not m.group(2):
        return D.e(v)
    return f'{D.e(m.group(1))}<span class="u" style="font-size:{tam_unidad}em">{D.e(m.group(2))}</span>'


# ------------------------------------------------------------------ f_portada: la foto a toda la hoja y el titular abajo
def f_portada(h: dict, ctx: dict) -> str:
    p = [_foto(h["foto"], 0, 0, 1080, 1350, 0, h.get("foco"))]
    p.append('<div class="velo" style="inset:0;background:linear-gradient(180deg,rgba(0,0,0,.66) 0%,rgba(0,0,0,.1) 24%,rgba(0,0,0,.28) 40%,rgba(0,0,0,.93) 100%)"></div>')
    if h.get("etiqueta"):
        p.append(_pastilla(h["etiqueta"], 64, 60, 48))
    p.append(_titulo(h["titular"], 64, 400, 952, 760, (96, 230), anclar="end"))
    p.append(_marca(ctx) + _puntos(ctx) + _sigue(h))
    return _hoja("".join(p), ctx)


# ------------------------------------------------------------------ f_letra: portada de serie, la letra de la vitamina enorme sobre la foto
def f_letra(h: dict, ctx: dict) -> str:
    p = [_foto(h["foto"], 0, 0, 1080, 1350, 0, h.get("foco"))]
    p.append('<div class="velo" style="inset:0;background:linear-gradient(180deg,rgba(0,0,0,.82) 0%,rgba(0,0,0,.6) 34%,rgba(0,0,0,.72) 58%,rgba(0,0,0,.96) 100%)"></div>')
    if ctx.get("angular"):
        p.append('<div class="cuna"></div>')
    if h.get("etiqueta"):
        p.append(_pastilla(h["etiqueta"], 64, 60, 48))
    p.append(f'<div class="letra" data-fit="260,980" style="left:48px;top:100px;width:980px;height:640px"><span>{D.e(h["letra"])}</span></div>')
    p.append(_titulo(h["titular"], 64, 740, 952, 440, (70, 140), anclar="end"))
    p.append(_marca(ctx) + _puntos(ctx) + _sigue(h))
    return _hoja("".join(p), ctx)


# ------------------------------------------------------------------ f_ranking: el que gana, grande; el resto, en fila
def f_ranking(h: dict, ctx: dict) -> str:
    filas = h["filas"]
    n = len(filas)
    valores = [_num(f["valor"]) for f in filas]
    vmax = max(valores) or 1.0
    p = [_titulo(h["titulo"], 64, 56, 1080 - 64 - 60 - ctx["total"] * 32 - 40, 210, (62, 112))]
    if h.get("unidad"):
        p.append(_pastilla(h["unidad"], 64, 276, 44, versales=False))
    # el primero: tarjeta grande con la foto
    g = filas[0]
    y0, alto = 344, {2: 560, 3: 520, 4: 460, 5: 400}.get(n, 350)
    p.append(_foto(g["foto"], 64, y0, 952, alto, 52, g.get("foco")))
    p.append(f'<div class="velo" style="left:64px;top:{y0}px;width:952px;height:{alto}px;border-radius:52px;'
             f'background:linear-gradient(180deg,rgba(0,0,0,.12) 18%,rgba(0,0,0,.92) 100%)"></div>')
    p.append(f'<div class="num" style="left:88px;top:{y0 + 24}px;width:104px;height:104px;border-radius:50%;background:var(--o);color:#000;font-size:84px;line-height:1">'
             f'<span style="display:block;margin-top:-6px">1</span></div>')
    p.append(f'<div class="ft" data-fit="34,74" style="left:92px;top:{y0 + alto - 176}px;width:520px;height:150px;align-items:flex-end"><span>{D.tx(g["nombre"])}</span></div>')
    p.append(f'<div class="num" data-fitw="70,170" style="left:620px;top:{y0 + alto - 196}px;width:372px;height:160px;justify-content:flex-end;color:var(--o)">'
             f'<span style="display:flex;align-items:flex-end;white-space:nowrap">{_valor(g["valor"])}</span></div>')
    resto = filas[1:]
    ini = y0 + alto + 28
    fin = 1196 if h.get("sigue") else 1262
    rh = min(112, (fin - ini) // max(len(resto), 1))
    d = min(84, rh - 14)
    for k, (f, v) in enumerate(zip(resto, valores[1:]), 2):
        y = ini + (k - 2) * rh
        cy = y + rh / 2
        p.append(f'<div class="ft" style="left:64px;top:{cy - 26:.0f}px;width:54px;height:52px;font-size:54px;color:var(--o);align-items:center;justify-content:center"><span>{k}</span></div>')
        p.append(_foto(f["foto"], 128, cy - d / 2, d, d, d / 2, f.get("foco")))
        p.append(f'<div class="cuerpo" data-fit="24,38" style="left:232px;top:{cy - rh * .46:.0f}px;width:420px;height:{rh * .5:.0f}px;display:flex;align-items:center"><span>{D.tx(f["nombre"])}</span></div>')
        largo = max(24.0, 330.0 * v / vmax)
        p.append(f'<div class="velo" style="left:232px;top:{cy + rh * .13:.0f}px;width:{largo:.0f}px;height:16px;border-radius:8px;background:var(--o)"></div>')
        p.append(f'<div class="num" data-fitw="40,76" style="left:700px;top:{cy - rh * .42:.0f}px;width:316px;height:{rh * .84:.0f}px;justify-content:flex-end;color:#fff">'
                 f'<span style="display:flex;align-items:flex-end;white-space:nowrap">{_valor(f["valor"])}</span></div>')
    p.append(_marca(ctx) + _puntos(ctx) + _sigue(h))
    return _hoja("".join(p), ctx)


# ------------------------------------------------------------------ f_vs: dos comidas, dos cifras
def f_vs(h: dict, ctx: dict) -> str:
    p = [_titulo(h["titulo"], 64, 56, 1080 - 64 - 60 - ctx["total"] * 32 - 40, 230, (62, 120))]
    ganador = h.get("ganador")
    for lado, x in (("izq", 64), ("der", 552)):
        d = h[lado]
        gana = ganador == lado
        borde = "box-shadow:0 0 0 10px var(--o);" if gana else ""
        p.append(_foto(d["foto"], x, 318, 464, 560, 52, d.get("foco"), borde, d.get("zoom")))
        p.append(f'<div class="ft" data-fit="36,66" style="left:{x}px;top:910px;width:464px;height:76px;align-items:center;justify-content:center;text-align:center"><span>{D.tx(d["nombre"])}</span></div>')
        col = "var(--o)" if gana or not ganador else "#fff"
        p.append(f'<div class="num" data-fitw="60,150" style="left:{x}px;top:984px;width:464px;height:140px;color:{col}"><span style="display:flex;align-items:flex-end;white-space:nowrap">{_valor(d["valor"])}</span></div>')
    p.append('<div class="num" style="left:482px;top:560px;width:116px;height:116px;border-radius:50%;background:#000;border:8px solid var(--o);color:#fff;font-size:50px;line-height:1">'
             '<span style="display:block;margin-top:-4px;text-transform:uppercase">vs</span></div>')
    p.append(f'<div class="cuerpo" data-fit="28,44" style="left:64px;top:1140px;width:952px;height:104px;display:flex;align-items:center;justify-content:center;text-align:center"><span>{D.tx(h["texto"])}</span></div>')
    p.append(_marca(ctx) + _puntos(ctx) + _sigue(h))
    return _hoja("".join(p), ctx)


# ------------------------------------------------------------------ f_dato: una cifra enorme y una frase
def f_dato(h: dict, ctx: dict) -> str:
    p = []
    ang = bool(ctx.get("angular"))
    if h.get("foto"):
        if ang:        # angular: un bloque que sangra por la derecha, sin pisar el texto ni el «Desliza»
            p.append(_foto(h["foto"], 700, 760, 380, 440, 0, h.get("foco")))
        else:
            p.append(_foto(h["foto"], 560, 790, 640, 640, 320, h.get("foco")))
    if h.get("etiqueta"):
        p.append(_pastilla(h["etiqueta"], 64, 60, 46))
    p.append(_titulo(h["titulo"], 64, 150, 952, 330, (64, 150)))
    cifra = D.e(h["cifra"])
    unidad = f'<span class="u">{D.e(h["unidad"])}</span>' if h.get("unidad") else ""
    p.append(f'<div class="num" data-fitw="170,560" style="left:64px;top:470px;width:952px;height:430px;justify-content:flex-start;color:var(--o)">'
             f'<span style="display:flex;align-items:flex-end;white-space:nowrap">{cifra}{unidad}</span></div>')
    ancho = (580 if ang else 600) if h.get("foto") else 952
    p.append(f'<div class="cuerpo" data-fit="32,50" style="left:64px;top:940px;width:{ancho}px;height:250px;display:flex;align-items:flex-start"><span>{D.tx(h["texto"])}</span></div>')
    p.append(_marca(ctx) + _puntos(ctx) + _sigue(h))
    return _hoja("".join(p), ctx)


# ------------------------------------------------------------------ f_foto_texto: foto arriba y una explicación corta debajo
def f_foto_texto(h: dict, ctx: dict) -> str:
    p = [_foto(h["foto"], 0, 0, 1080, 760, 0, h.get("foco"))]
    p.append('<div class="velo" style="left:0;top:0;width:1080px;height:760px;background:linear-gradient(180deg,rgba(0,0,0,.62) 0%,rgba(0,0,0,.05) 26%,rgba(0,0,0,.2) 52%,#000 100%)"></div>')
    if h.get("etiqueta"):
        p.append(_pastilla(h["etiqueta"], 64, 60, 48))
    p.append(_titulo(h["titulo"], 64, 520, 952, 330, (60, 130), anclar="end"))
    p.append(f'<div class="cuerpo" data-fit="30,46" style="left:64px;top:884px;width:952px;height:300px;display:flex;align-items:flex-start"><span>{D.tx(h["texto"])}</span></div>')
    p.append(_marca(ctx) + _puntos(ctx) + _sigue(h))
    return _hoja("".join(p), ctx)


# ------------------------------------------------------------------ f_frase: tres palabras enormes y una explicación
def f_frase(h: dict, ctx: dict) -> str:
    """El titular puede ser una lista: cada elemento va en su línea, grande y apilado (por ejemplo «Fabrica.» «Capta.» «Exporta.»)."""
    p = []
    if h.get("etiqueta"):
        p.append(_pastilla(h["etiqueta"], 64, 60, 48))
    tit = h["titular"]
    html_tit = "<br>".join(D.tx(x) for x in tit) if isinstance(tit, list) else D.tx(tit)
    p.append(f'<div class="ft" data-fit="110,300" style="left:64px;top:170px;width:952px;height:740px;align-items:center"><span>{html_tit}</span></div>')
    p.append('<div class="velo" style="left:64px;top:950px;width:952px;height:6px;border-radius:3px;background:var(--o)"></div>')
    ancho = 640 if h.get("foto") else 952
    p.append(f'<div class="cuerpo" data-fit="30,48" style="left:64px;top:990px;width:{ancho}px;height:210px;display:flex;align-items:flex-start"><span>{D.tx(h["texto"])}</span></div>')
    if h.get("foto"):
        p.append(_foto(h["foto"], 740, 990, 276, 276, 138, h.get("foco")))
    p.append(_marca(ctx) + _puntos(ctx) + _sigue(h))
    return _hoja("".join(p), ctx)


# ------------------------------------------------------------------ f_cierre: guardar, fuentes y aviso
def f_cierre(h: dict, ctx: dict) -> str:
    """`fotos` admite nombres de foto o diccionarios {foto, texto}: el texto es el rótulo bajo el círculo («Kiwi · 93 mg»)."""
    p = []
    if h.get("etiqueta"):
        p.append(_pastilla(h["etiqueta"], 64, 60, 52))
    p.append(_titulo(h["frase"], 64, 160, 952, 400, (64, 180)))
    fotos = [x if isinstance(x, dict) else {"foto": x} for x in (h.get("fotos") or [])][:3]
    if fotos:
        d = 270
        hueco = (952 - d * len(fotos)) // max(len(fotos) - 1, 1) if len(fotos) > 1 else 0
        for k, x in enumerate(fotos):
            px = 64 + k * (d + hueco)
            p.append(_foto(x["foto"], px, 590, d, d, d / 2, x.get("foco")))
            if x.get("texto"):
                p.append(f'<div class="cuerpo" data-fit="24,34" style="left:{px - 40}px;top:{590 + d + 14}px;width:{d + 80}px;height:90px;display:flex;align-items:flex-start;justify-content:center;text-align:center"><span>{D.tx(x["texto"])}</span></div>')
    fu = "<br>".join(D.e(x) for x in h["fuentes"])
    p.append('<div class="velo" style="left:64px;top:985px;width:952px;height:6px;border-radius:3px;background:var(--o)"></div>')
    p.append(f'<div class="fuentes" data-fit="24,32" style="left:64px;top:1010px;width:952px;height:120px;overflow:hidden"><span style="display:block"><b>{"Fuente." if len(h["fuentes"]) == 1 else "Fuentes."}</b> {fu}</span></div>')
    p.append(f'<div class="aviso" style="left:64px;top:1140px;width:952px;font-size:29px;opacity:1">{D.e(h["aviso"])}</div>')
    p.append(_puntos(ctx))
    p.append(f'<div class="marcaf" style="bottom:26px;font-size:30px">{D.e(ctx["tema"]["nombre"])}</div>')
    return _hoja("".join(p), ctx)


PLANTILLAS = {"f_frase": f_frase, "f_portada": f_portada, "f_letra": f_letra, "f_ranking": f_ranking, "f_vs": f_vs, "f_dato": f_dato, "f_foto_texto": f_foto_texto, "f_cierre": f_cierre}


def registrar(modulo_d) -> dict:
    global D
    D = modulo_d
    return PLANTILLAS
