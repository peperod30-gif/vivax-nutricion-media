"""Plantillas de la dirección C («folleto de ofertas con datos por 100 g») de Vivax Nutrición.

Cada hoja es HTML y CSS propios, dibujados en un navegador (render.py). Tres tintas: blanco #FFFFFF, negro #000000
y oliva #B0BD6B (oliva claro desde el 9-oct; antes #5E6A2E). Los defectos tienen causa de imprenta: la capa oliva sale desplazada unos píxeles bajo la negra
(registro de riso), el oliva de las bandas es trama, los sellos tienen el borde roto por la tinta y el papel lleva
grano. Nada de degradados, brillos ni sombras difusas.

La semilla de cada hoja sale de un hash (pieza + número de hoja), así que repetir el render da la misma imagen.
"""
from __future__ import annotations

import hashlib
import html
import random
from pathlib import Path

ANCHO, ALTO = 1080, 1350
OLIVA, NEGRO, BLANCO = "#B0BD6B", "#000000", "#FFFFFF"

# Rangos Unicode que cubren las fuentes locales (subconjuntos latin y latin-ext de Google Fonts).
RANGOS = [(0x0000, 0x00FF), (0x0131, 0x0131), (0x0152, 0x0153), (0x02BB, 0x02BC), (0x02C6, 0x02C6), (0x02DA, 0x02DA),
          (0x02DC, 0x02DC), (0x0304, 0x0304), (0x0308, 0x0308), (0x0329, 0x0329), (0x2000, 0x206F), (0x20AC, 0x20AC),
          (0x2122, 0x2122), (0x2191, 0x2191), (0x2193, 0x2193), (0x2212, 0x2212), (0x2215, 0x2215), (0xFEFF, 0xFEFF),
          (0xFFFD, 0xFFFD), (0x0100, 0x02BA), (0x1E00, 0x1E9F), (0x20A0, 0x20AB)]


def caracteres_sin_fuente(texto: str) -> list[str]:
    """Caracteres del texto que ninguna de las dos fuentes dibuja (se piden antes de pintar, nunca se sustituyen)."""
    malos = []
    for c in texto:
        if c in "\n\t":
            continue
        o = ord(c)
        if not any(a <= o <= b for a, b in RANGOS) and c not in malos:
            malos.append(c)
    return malos


def e(t) -> str:
    return html.escape(str(t), quote=True)


def semilla(pieza_id: str, n: int) -> random.Random:
    h = hashlib.sha256(f"{pieza_id}:{n}".encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def _num(t: str) -> str:
    """Cifra al estilo de un precio de folleto: la parte entera grande y los decimales pequeños arriba."""
    t = str(t)
    if "," in t:
        ent, dec = t.split(",", 1)
        return f'<span class="ent">{e(ent)}</span><sup class="dec">,{e(dec)}</sup>'
    return f'<span class="ent">{e(t)}</span>'


def css(carpeta_fuentes: Path) -> str:
    f = carpeta_fuentes.resolve().as_uri()
    return """
@font-face{font-family:"Sofia";font-weight:300 900;src:url("__F__/sofia-sans-extra-condensed-latin.woff2") format("woff2");unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}
@font-face{font-family:"Sofia";font-weight:300 900;src:url("__F__/sofia-sans-extra-condensed-latin-ext.woff2") format("woff2");unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF}
@font-face{font-family:"Hanken";font-weight:400 800;src:url("__F__/hanken-grotesk-latin.woff2") format("woff2");unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}
@font-face{font-family:"Hanken";font-weight:400 800;src:url("__F__/hanken-grotesk-latin-ext.woff2") format("woff2");unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C4,U+2113,U+2C60-2C7F,U+A720-A7FF}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;height:1350px;background:#fff}
body{font-family:"Hanken",sans-serif;font-feature-settings:"tnum" 1,"lnum" 1;-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
:root{--o:#B0BD6B;--n:#000;--b:#fff}
.hoja{position:relative;width:1080px;height:1350px;overflow:hidden}
.claro{background:var(--b);color:var(--n)}
.oliva{background:var(--o);color:var(--n)}
.negro{background:var(--n);color:var(--b)}
.sofia{font-family:"Sofia",sans-serif;font-weight:900;text-transform:uppercase;letter-spacing:-.005em}
/* grano de papel y registro de tinta */
.grano{position:absolute;inset:0;pointer-events:none;mix-blend-mode:multiply;opacity:.5;background-size:540px 540px}
.trama{position:absolute;pointer-events:none;background-image:radial-gradient(circle,var(--n) 30%,transparent 32%);background-size:15px 15px}
.trama.o{background-image:radial-gradient(circle,var(--o) 30%,transparent 32%)}
.trama.b{background-image:radial-gradient(circle,var(--b) 30%,transparent 32%)}
/* pie común */
.pie{position:absolute;left:72px;right:72px;bottom:56px;height:48px;display:flex;align-items:center;justify-content:space-between}
.pie .marca{font:800 38px/1 "Sofia",sans-serif;text-transform:uppercase;letter-spacing:.05em}
.pie .pag{font:700 30px/1 "Hanken",sans-serif;background:var(--n);color:var(--b);padding:9px 18px 7px}
.oliva .pie .pag,.negro .pie .pag{background:var(--b);color:var(--n)}
.fuente{position:absolute;left:72px;right:72px;bottom:126px;font:500 28px/1.25 "Hanken",sans-serif;opacity:.9}
/* titulares: caja negra con la capa oliva corrida detrás */
.cab{position:absolute;left:0;top:78px;width:1008px}
.cab .capa{position:absolute;inset:0;filter:url(#corte)}
.cab .oli{background:var(--o);transform:translate(15px,13px)}
.cab .neg{background:var(--n)}
.cab .ct{position:absolute;inset:0;padding:24px 52px 20px 72px;color:var(--b);font-family:"Sofia",sans-serif;font-weight:900;text-transform:uppercase;line-height:.9}
.cab .ct span{display:flex;align-items:center;width:100%;height:100%}
/* fit */
[data-fit],[data-fitw]{overflow:hidden}
.portada .tit,.cab .ct span,.cierre .envia,.cifra .texto span{text-wrap:balance}
/* sello de goma */
.sello{position:absolute;font-family:"Sofia",sans-serif;font-weight:900;text-transform:uppercase;line-height:.88;text-align:center;border:10px solid currentColor;padding:14px 30px 10px;filter:url(#aspero)}
/* portada */
.portada .tit{display:flex;align-items:center;position:absolute;left:72px;top:232px;width:936px;height:560px;padding:0 16px 14px 0;font-family:"Sofia",sans-serif;font-weight:900;text-transform:uppercase;line-height:.86;color:var(--n);text-shadow:9px 9px 0 var(--b)}
.portada .franja{position:absolute;left:-30px;right:-30px;top:1008px;background:var(--n);color:var(--b);padding:34px 102px 34px 102px;font:700 54px/1.12 "Hanken",sans-serif}
.portada .etiq{position:absolute;left:72px;top:72px;background:var(--n);color:var(--b);font:900 76px/1 "Sofia",sans-serif;text-transform:uppercase;padding:10px 30px 6px;box-shadow:12px 12px 0 var(--b)}
.portada .esq{position:absolute;right:0;bottom:0;width:150px;height:150px}
/* ficha */
.ficha .tabla{position:absolute;left:72px;right:72px;top:396px;height:712px;border-top:10px solid var(--n);display:flex;flex-direction:column;font-size:48px}
.ficha .fila{flex:1;display:flex;align-items:stretch;border-bottom:10px solid var(--n)}
.ficha .etq{width:310px;flex:none;padding:0 24px 0 0;display:flex;align-items:center;font:900 1.3em/.92 "Sofia",sans-serif;text-transform:uppercase;color:var(--n)} .ficha .etq span{text-decoration:underline;text-decoration-color:var(--o);text-decoration-thickness:.2em;text-underline-offset:.08em}
.ficha .val{flex:1;padding:14px 0 14px 28px;display:flex;align-items:center;font:700 1em/1.18 "Hanken",sans-serif;border-left:10px solid var(--n)}
/* cifra */
.cifra .precio{position:absolute;left:60px;top:300px;width:960px;height:520px;padding:30px 0 0 12px;z-index:2;font-family:"Sofia",sans-serif;font-weight:900;line-height:.74;display:flex;align-items:flex-start;white-space:nowrap}
.cifra .precio .cap{position:relative;display:flex;align-items:flex-start}
.cifra .precio .cap > .copia{position:absolute;left:0;top:0;color:var(--o);transform:translate(16px,14px);display:flex;align-items:flex-start}
.cifra .precio .cap > .real{position:relative;color:var(--n);display:flex;align-items:flex-start}
.cifra .signo{font-size:.62em;margin-right:.04em;margin-top:.02em}
.cifra .dec{font-size:.46em;vertical-align:top;position:relative;top:.06em;line-height:1}
.cifra .pun{position:absolute;left:0;top:330px;width:560px;height:470px;z-index:1;-webkit-mask-image:linear-gradient(100deg,#000 0%,transparent 78%);mask-image:linear-gradient(100deg,#000 0%,transparent 78%)}
.cifra .uni{position:absolute;right:72px;top:822px;font:900 118px/.8 "Sofia",sans-serif;text-transform:uppercase;color:var(--n);background:var(--o);padding:0 .12em}
.cifra .texto{position:absolute;left:72px;right:72px;top:956px;height:214px;font:700 48px/1.2 "Hanken",sans-serif}
/* texto */
.texto-hoja .cuerpo{position:absolute;left:72px;right:72px;top:390px;height:700px;font:600 47px/1.28 "Hanken",sans-serif}
.texto-hoja .cuerpo p+p{margin-top:.7em}
.texto-hoja .cuerpo .res{display:inline-block;background:var(--o);color:var(--n);padding:2px 14px 4px}
/* contraste */
.contraste .blk{position:absolute;left:72px;right:72px}
.contraste .b1{top:372px;height:300px}
.contraste .b2{top:728px;height:420px}
.contraste .tag{display:inline-block;font:900 66px/1 "Sofia",sans-serif;text-transform:uppercase;color:var(--b);padding:12px 26px 8px}
.contraste .b1 .tag{background:var(--n)}
.contraste .b2 .tag{background:var(--o);color:var(--n)}
.contraste .txt{margin-top:20px;font:700 48px/1.22 "Hanken",sans-serif;height:210px}
.contraste .b2 .txt{height:320px}
.contraste .b1 .txt{text-decoration:line-through;text-decoration-thickness:5px;text-decoration-color:var(--n)}
/* lista */
.lista .items{position:absolute;left:72px;right:72px;top:380px;height:740px;font-size:44px}
.lista .it{display:flex;align-items:flex-start;gap:34px;padding:22px 0 20px;border-top:7px solid var(--n)}
.lista .it:last-child{border-bottom:7px solid var(--n)}
.lista .caja{flex:none;width:66px;height:66px;border:8px solid var(--n);position:relative;margin-top:6px}
.lista .caja svg{position:absolute;inset:0;width:100%;height:100%}
.lista .it span.t{font:700 1em/1.18 "Hanken",sans-serif}
/* cierre */
.cierre .borde{position:absolute;left:54px;right:54px;top:54px;bottom:54px;border:7px dashed var(--b)}
.cierre .envia{position:absolute;left:112px;right:112px;top:206px;height:610px;font-family:"Sofia",sans-serif;font-weight:900;text-transform:uppercase;line-height:.9;color:var(--b)}
.cierre .envia-etq{position:absolute;left:112px;top:98px;background:var(--b);color:var(--n);font:900 50px/1 "Sofia",sans-serif;text-transform:uppercase;padding:8px 22px 4px}
.cierre .fuentes{position:absolute;left:112px;right:112px;top:860px;font:500 29px/1.3 "Hanken",sans-serif;color:var(--b)}
.cierre .fuentes b{font-weight:800}
.cierre .aviso{position:absolute;left:112px;right:112px;bottom:156px;font:600 29px/1.3 "Hanken",sans-serif;border-top:5px solid var(--b);padding-top:20px}
.cierre .pie{left:112px;right:112px}
""".replace("__F__", f)


def _defs(sem: random.Random) -> str:
    s1, s2 = sem.randint(1, 9999), sem.randint(1, 9999)
    return f"""<svg width="0" height="0" style="position:absolute"><defs>
<filter id="aspero" x="-6%" y="-6%" width="112%" height="112%"><feTurbulence type="fractalNoise" baseFrequency="0.045" numOctaves="2" seed="{s1}" result="r"/><feDisplacementMap in="SourceGraphic" in2="r" scale="9" xChannelSelector="R" yChannelSelector="G"/></filter>
<filter id="corte" x="-4%" y="-8%" width="108%" height="116%"><feTurbulence type="fractalNoise" baseFrequency="0.014 0.03" numOctaves="2" seed="{s2}" result="a"/><feDisplacementMap in="SourceGraphic" in2="a" scale="9" xChannelSelector="R" yChannelSelector="G" result="b"/><feTurbulence type="fractalNoise" baseFrequency="0.22" numOctaves="2" seed="{s1}" result="c"/><feDisplacementMap in="b" in2="c" scale="3.5" xChannelSelector="G" yChannelSelector="R"/></filter>
</defs></svg>"""


def _grano(sem: random.Random) -> str:
    s = sem.randint(1, 9999)
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='540' height='540'><filter id='f' x='0' y='0' width='100%' height='100%'>"
           f"<feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' seed='{s}'/>"
           f"<feColorMatrix type='matrix' values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 3.4 -2.15'/></filter>"
           f"<rect width='100%' height='100%' filter='url(#f)'/></svg>")
    return "data:image/svg+xml;utf8," + svg.replace("<", "%3C").replace(">", "%3E").replace("#", "%23").replace("'", "%27")


def _pie(h: dict, n: int, total: int) -> str:
    return (f'<div class="pie"><span class="marca">Vivax Nutrición</span><span class="pag">{n}/{total}</span></div>')


def _fuente(h: dict) -> str:
    return f'<div class="fuente">{e(h["fuente"])}</div>' if h.get("fuente") else ""


def _cab(titulo: str, sem: random.Random, fit=(70, 128)) -> str:
    """Titular: caja negra de bordes cortados con la capa oliva corrida. Cambia de lado, ángulo y corrimiento por hoja."""
    rot = sem.uniform(-1.2, 1.2)
    dx = sem.randint(0, 40)
    ox, oy = sem.choice([(15, 13), (-14, 13), (15, -12), (-13, -12)])
    if sem.random() < 0.5:
        lado = f"left:0;width:{1008 - dx}px"
        relleno = "24px 52px 20px 72px"
    else:
        lado = f"left:auto;right:0;width:{1008 - dx}px"
        relleno = "24px 72px 20px 52px"
    return (f'<div class="cab" style="transform:rotate({rot:.2f}deg);{lado};height:230px">'
            f'<div class="capa oli" style="transform:translate({ox}px,{oy}px)"></div><div class="capa neg"></div>'
            f'<div class="ct" style="padding:{relleno}"><span data-fit="{fit[0]},{fit[1]}">{e(titulo)}</span></div></div>')


# ------------------------------------------------------------------ una función por plantilla

def portada(h, n, total, sem):
    rot = sem.uniform(-4.5, -2.0)
    rot2 = sem.uniform(-2.2, -0.8)
    sello = ""
    if h.get("sello"):
        sello = (f'<div class="sello" style="right:54px;top:{sem.randint(826, 846)}px;font-size:74px;color:var(--n);transform:rotate({sem.uniform(6, 10):.1f}deg);'
                 f'background:transparent;z-index:3">{e(h["sello"])}</div>')
    return (f'<div class="hoja oliva portada">'
            f'<div class="trama b" style="left:0;top:0;width:1080px;height:200px;opacity:.16;transform:rotate({sem.uniform(8, 16):.1f}deg) scale(1.3)"></div>'
            f'<div class="etiq" style="transform:rotate({rot:.1f}deg)">{e(h["etiqueta"])}</div>'
            f'<div class="tit" data-fit="110,300">{e(h["titular"])}</div>'
            f'<div class="franja" style="transform:rotate({rot2:.2f}deg)"><span data-fit="38,54" style="display:block;height:122px">{e(h["franja"])}</span></div>'
            f'{sello}'
            f'<div class="pie"><span class="marca">Vivax Nutrición</span><span class="pag">{n}/{total}</span></div>'
            f'</div>')


def ficha(h, n, total, sem):
    filas = "".join(f'<div class="fila"><div class="etq"><span>{e(a)}</span></div><div class="val">{e(b)}</div></div>' for a, b in h["filas"])
    return (f'<div class="hoja claro ficha">{_cab(h["titulo"], sem)}'
            f'<div class="tabla" data-fit="34,58" style="transform:rotate({sem.uniform(-0.6, 0.6):.2f}deg)">{filas}</div>'
            f'{_fuente(h)}{_pie(h, n, total)}</div>')


def cifra(h, n, total, sem):
    signo = f'<span class="signo">{e(h["signo"])}</span>' if h.get("signo") else ""
    cuerpo = f'{signo}{_num(h["cifra"])}'
    return (f'<div class="hoja claro cifra">{_cab(h["titulo"], sem, (60, 100))}'
            f'<div class="trama o pun" style="opacity:.55;transform:rotate({sem.uniform(-8, 8):.1f}deg)"></div>'
            f'<div class="precio" data-fitw="200,720"><div class="cap"><div class="copia">{cuerpo}</div><div class="real">{cuerpo}</div></div></div>'
            f'<div class="uni" style="transform:rotate({sem.uniform(-3, 2):.1f}deg)">{e(h["unidad"])}</div>'
            f'<div class="texto"><span data-fit="34,48" style="display:block;height:100%">{e(h["texto"])}</span></div>'
            f'{_fuente(h)}{_pie(h, n, total)}</div>')


def texto(h, n, total, sem):
    ps = "".join(f"<p>{e(p)}</p>" for p in h["parrafos"])
    res = f'<p><span class="res">{e(h["resalte"])}</span></p>' if h.get("resalte") else ""
    return (f'<div class="hoja claro texto-hoja">{_cab(h["titulo"], sem)}'
            f'<div class="cuerpo" data-fit="34,62">{ps}{res}</div>'
            f'{_fuente(h)}{_pie(h, n, total)}</div>')


def contraste(h, n, total, sem):
    r1, r2 = sem.uniform(-1.2, -0.3), sem.uniform(0.3, 1.2)
    return (f'<div class="hoja claro contraste">{_cab(h["titulo"], sem)}'
            f'<div class="blk b1" style="transform:rotate({r1:.2f}deg)"><span class="tag">{e(h["izq"]["cab"])}</span>'
            f'<div class="txt" data-fit="34,50">{e(h["izq"]["texto"])}</div></div>'
            f'<div class="blk b2" style="transform:rotate({r2:.2f}deg)"><span class="tag">{e(h["der"]["cab"])}</span>'
            f'<div class="txt" data-fit="34,50">{e(h["der"]["texto"])}</div></div>'
            f'{_fuente(h)}{_pie(h, n, total)}</div>')


def lista(h, n, total, sem):
    aspa = ('<svg viewBox="0 0 50 50"><path d="M8 10 L42 41 M41 8 L10 42" stroke="#000" stroke-width="7" stroke-linecap="butt" fill="none"/></svg>'
            if h.get("marca", "aspa") == "aspa" else "")
    its = "".join(f'<div class="it"><div class="caja">{aspa}</div><span class="t">{e(t)}</span></div>' for t in h["items"])
    return (f'<div class="hoja claro lista">{_cab(h["titulo"], sem)}'
            f'<div class="items" data-fit="30,54" style="display:block">{its}</div>'
            f'{_fuente(h)}{_pie(h, n, total)}</div>')


def cierre(h, n, total, sem):
    fu = "<br>".join(e(f) for f in h["fuentes"])
    return (f'<div class="hoja negro cierre"><div class="borde"></div>'
            f'<div class="envia-etq" style="transform:rotate({sem.uniform(-3, -1):.1f}deg)">{e(h["etiqueta"])}</div>'
            f'<div class="envia" data-fit="70,170">{e(h["frase"])}</div>'
            f'<div class="fuentes"><b>Fuentes.</b> {fu}</div>'
            f'<div class="aviso">{e(h["aviso"])}</div>'
            f'{_pie(h, n, total)}</div>')


PLANTILLAS = {"portada": portada, "ficha": ficha, "cifra": cifra, "texto": texto, "contraste": contraste, "columnas": contraste,
              "lista": lista, "cierre": cierre}


def textos_de(h: dict) -> list[str]:
    """Todos los textos visibles de una hoja (para comprobar caracteres y reglas)."""
    sal = []
    for k, v in h.items():
        if k == "plantilla":
            continue
        if isinstance(v, str):
            sal.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    sal.append(x)
                elif isinstance(x, (list, tuple)):
                    sal.extend(str(y) for y in x)
        elif isinstance(v, dict):
            sal.extend(str(y) for y in v.values())
    return sal


def pagina(pieza: dict, i: int, carpeta_fuentes: Path) -> str:
    hojas = pieza["hojas"]
    h = hojas[i]
    sem = semilla(pieza["id"], i + 1)
    cuerpo = PLANTILLAS[h["plantilla"]](h, i + 1, len(hojas), sem)
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><style>{css(carpeta_fuentes)}</style></head><body>'
            f'{_defs(sem)}{cuerpo}'
            f'<div class="grano" style="position:absolute;inset:0;background-image:url(\'{_grano(sem)}\')"></div>'
            f'</body></html>')
