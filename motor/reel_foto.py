"""Reels de Vivax Nutrición con fotos reales (dirección F, 9:16, 1080×1920): cuenta atrás de ranking y duelo de dos alimentos.

Los carruseles de la dirección F (plantillas_f.py) llevan comida real fotografiada, cifras grandes y un ganador que se ve de un vistazo.
Este módulo hace lo mismo en vídeo: foto a toda pantalla con zoom lento, la cifra que SUBE contando, la barra que crece, los cortes al
pulso de la música (compuesta por código, musica_propia.py), golpes de zoom y sacudida en cada revelación y transiciones por empuje o
cortinilla. Sin voz, sin personas, sin marcas. Cada reel sale en dos MP4: `video.mp4` (con música propia) y `video_mudo.mp4` (sin pista de
audio, para que José ponga el sonido de moda desde la app de Instagram).

Uso:
    python reel_foto.py piezas/f_reels/n1-r-01-hierro.json                 # dibuja un guion
    python reel_foto.py --desde-pieza n1-f-01-hierro --modo cuenta_atras   # guion a partir de un carrusel de piezas/f/ y dibujo
    python reel_foto.py --desde-pieza n1-f-05-azucar --modo duelo --estilo house --id n1-r-05-azucar
    python reel_foto.py guion.json --solo 0.5,3.0,9.0                      # solo unos fotogramas (fotogramas.jpg), sin codificar

El guion (JSON) lleva `escenas` con `tipo` gancho | cuenta | vs | cierre y `pulsos` (cada escena dura un número entero de pulsos de la
música: los cortes caen siempre en un golpe de bombo). Las cifras y los alimentos salen de los hechos que cita (`hechos`, banco ALI-...);
los textos pasan los controles de texto de controles.py (cifras contra el banco, líneas rojas, texto de IA, densidad, descargo).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
FUENTES = RAIZ / "recursos" / "fuentes"
CATALOGO = RAIZ / "datos" / "fotos_catalogo.json"
ANCHO, ALTO = 1080, 1920
FPS = 30
ZONA = (40, 250, 950, 1500)            # zona segura de Instagram (izquierda, arriba, derecha, abajo): 250 arriba, 420 abajo, 130 a la derecha
TIPOS = ("gancho", "cuenta", "vs", "cierre")
DUR_MIN_S, DUR_MAX_S = 12.0, 20.0
ARGS_CHROME = ["--disable-lcd-text", "--force-color-profile=srgb", "--font-render-hinting=none", "--hide-scrollbars"]


class ErrorReel(ValueError):
    pass


# ------------------------------------------------------------------ utilidades de texto
def _escapar(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def marcado(t: str) -> str:
    """Texto escapado con las {palabras} marcadas en oliva."""
    return re.sub(r"\{(.+?)\}", r'<b class="o">\1</b>', _escapar(str(t)))


def _sin_marcas(t: str) -> str:
    return str(t).replace("{", "").replace("}", "")


def valor(s: str) -> dict:
    """«12 mg» -> {num: 12.0, dec: 0, unidad: 'mg', texto: '12 mg'}; «8,8 mg» -> dec 1; «21,3 g» -> dec 1."""
    m = re.match(r"^\s*([\d.,]+)\s*(.*)$", str(s))
    if not m:
        raise ErrorReel(f"valor sin cifra: «{s}»")
    n = m.group(1)
    dec = len(n.split(",")[1]) if "," in n else 0
    num = float(n.replace(".", "").replace(",", ".")) if "," in n else float(n.replace(".", ""))
    return {"num": num, "dec": dec, "unidad": m.group(2).strip(), "texto": str(s).strip()}


def partir_titular(texto: str, max_car: int = 13) -> list[str]:
    """Reparte un titular en líneas cortas (≤ max_car letras sin contar las llaves), sin partir lo que va entre llaves."""
    fichas, marca = [], False
    texto = re.sub(r"\}([^\s{}]+)", lambda m: m.group(1) + "}", str(texto))   # la puntuación pegada a una marca entra en ella: «{azúcar}:» -> «{azúcar:}»
    texto = re.sub(r"([^\s{}]+)\{", lambda m: "{" + m.group(1), texto)
    for tok in re.findall(r"\{|\}|[^\s{}]+", texto):
        if tok == "{":
            marca = True
        elif tok == "}":
            marca = False
        else:
            fichas.append((tok, marca))
    lineas, actual, largo = [], [], 0
    for tok, m in fichas:
        if actual and largo + 1 + len(tok) > max_car:
            lineas.append(actual)
            actual, largo = [], 0
        actual.append((tok, m))
        largo += len(tok) + (1 if largo else 0)
    if actual:
        lineas.append(actual)
    sal = []
    for l in lineas:
        partes, i = [], 0
        while i < len(l):
            j = i
            while j < len(l) and l[j][1] == l[i][1]:
                j += 1
            seg = " ".join(t for t, _ in l[i:j])
            partes.append("{" + seg + "}" if l[i][1] else seg)
            i = j
        sal.append(" ".join(partes))
    return sal


def partir_titular_auto(texto: str, alto: int = 900, ancho: int = 886, max_px: int = 260, min_px: int = 90) -> list[str]:
    """La división en líneas que da las letras más grandes (estimación: ancho medio de 0,47 em por letra en versales de Sofia), con las
    líneas a la altura disponible; a igual tamaño, la que tiene menos líneas."""
    mejor, mejor_px = None, -1.0
    for mc in range(6, 19):
        ls = partir_titular(texto, mc)
        letras = max(len(_sin_marcas(l)) for l in ls)
        px = min(max_px, ancho / (0.47 * letras))
        while len(ls) * px * 0.9 > alto and px > min_px:
            px -= 4
        if px > mejor_px + 1e-6 or (abs(px - mejor_px) < 1e-6 and mejor is not None and len(ls) < len(mejor)):
            mejor, mejor_px = ls, px
    return mejor or partir_titular(texto)


# ------------------------------------------------------------------ guion -> plan
def _fotos_de(guion: dict) -> list[str]:
    sal = []
    for e in guion["escenas"]:
        for k in ("foto",):
            if e.get(k):
                sal.append(e[k])
        for lado in ("izq", "der"):
            if isinstance(e.get(lado), dict) and e[lado].get("foto"):
                sal.append(e[lado]["foto"])
    return sal


def validar(guion: dict) -> list[str]:
    err = []
    for k in ("id", "cuenta", "escenas", "hechos", "pie_instagram", "musica"):
        if k not in guion:
            err.append(f"falta «{k}»")
    if err:
        return err
    cat = json.loads(CATALOGO.read_text(encoding="utf-8")) if CATALOGO.exists() else {}
    for c in _fotos_de(guion):
        if c not in cat:
            err.append(f"la foto «{c}» no está en datos/fotos_catalogo.json")
        elif not (RAIZ / cat[c]["archivo"]).is_file():
            err.append(f"falta el fichero de la foto «{c}»")
    esc = guion["escenas"]
    if esc[0]["tipo"] != "gancho" or esc[-1]["tipo"] != "cierre":
        err.append("la primera escena tiene que ser un gancho y la última un cierre (el reel enlaza con su inicio)")
    for i, e in enumerate(esc):
        if e.get("tipo") not in TIPOS:
            err.append(f"escena {i}: tipo «{e.get('tipo')}» desconocido")
            continue
        if not isinstance(e.get("pulsos"), int) or e["pulsos"] < 2:
            err.append(f"escena {i}: «pulsos» tiene que ser un entero de 2 o más")
        if e["tipo"] == "cuenta":
            try:
                valor(e["valor"])
            except (KeyError, ErrorReel) as exc:
                err.append(f"escena {i}: {exc}")
        if e["tipo"] == "vs":
            for lado in ("izq", "der"):
                try:
                    valor(e[lado]["valor"])
                except (KeyError, TypeError, ErrorReel) as exc:
                    err.append(f"escena {i}, {lado}: {exc}")
    if guion["musica"].get("estilo") not in ("house", "lofi", "hiphop"):
        err.append("estilo de música desconocido")
    return err


def planificar(guion: dict) -> dict:
    """Tiempos de cada escena (en pulsos y segundos), roles de música por pulso, golpes de cámara y eventos de sonido."""
    import musica_propia as MP
    estilo = guion["musica"]["estilo"]
    pulso = 60.0 / MP.ESTILOS[estilo]["bpm"]
    esc = guion["escenas"]
    P = sum(e["pulsos"] for e in esc)
    roles: list[str] = []
    escenas, eventos, golpes_cam, golpes_musica = [], [], [], []
    p0 = 0
    for i, e in enumerate(esc):
        dur = e["pulsos"] * pulso
        t0 = p0 * pulso
        rol = {"gancho": "intro", "cuenta": "construccion", "vs": "groove", "cierre": "cola"}[e["tipo"]]
        if e["tipo"] == "cuenta" and e.get("ganador"):
            rol = "groove"
        roles += [rol] * e["pulsos"]
        tr = {"tipo": "corte", "dur": 0.0} if i == 0 else {"tipo": ("empuje" if i % 2 else "barrido"), "dur": 0.26}
        impactos = []                                            # instantes (s, relativos al inicio de la escena) en que la escena golpea
        if e["tipo"] == "cuenta":
            fin = dur - (2 * pulso if e.get("ganador") else pulso)
            impactos.append((fin, 1.0 if e.get("ganador") else 0.62, "sello" if e.get("ganador") else "golpe"))
        elif e["tipo"] == "vs":
            a_fin, b_fin = round((dur / 2 - pulso) / pulso) * pulso, dur - 2 * pulso
            impactos += [(a_fin, 0.55, "golpe"), (b_fin, 0.95, "sello")]
        elif e["tipo"] == "gancho":
            n = len(e.get("_lineas") or partir_titular_auto(e["titular"], 900))
            for k in range(1, n):
                eventos.append({"t": round(t0 + k * 0.25 - 0.03, 3), "tipo": "entrada", "escena": i, "intensidad": 0.4})
                golpes_cam.append({"t": round(t0 + k * 0.25 - 0.03, 3), "amp": 7})
        elif e["tipo"] == "cierre":
            eventos.append({"t": round(t0 + 0.02, 3), "tipo": "cierre", "escena": i, "intensidad": 0.7})
        if i > 0:
            eventos.append({"t": round(t0 + 0.02, 3), "tipo": "transicion", "escena": i, "intensidad": 0.5})
        for (ti, inten, tipo) in impactos:
            eventos.append({"t": round(t0 + ti, 3), "tipo": tipo, "escena": i, "intensidad": inten})
            golpes_cam.append({"t": round(t0 + ti, 3), "amp": 10 + 10 * inten})
            if inten >= 0.9:
                golpes_musica.append(int(round((t0 + ti) / pulso)))
        escenas.append({"i": i, "tipo": e["tipo"], "t0": round(t0, 6), "t1": round(t0 + dur, 6), "dur": round(dur, 6), "pulsos": e["pulsos"],
                        "transicion": tr, "impactos": [round(t0 + x[0], 3) for x in impactos]})
        p0 += e["pulsos"]
    duracion = P * pulso
    n_frames = int(round(duracion * FPS))
    eventos.sort(key=lambda x: x["t"])
    return {"estilo": estilo, "pulso_s": pulso, "pulsos": P, "duracion": duracion, "n_frames": n_frames, "roles": roles, "escenas": escenas,
            "eventos": eventos, "golpes_cam": golpes_cam, "golpes_musica": sorted(set(golpes_musica))}


# ------------------------------------------------------------------ la página (todo se coloca desde setT(t): determinista)
JS = r"""
const D = __DATOS__;
const E = {out3: t => 1 - Math.pow(1 - t, 3), out5: t => 1 - Math.pow(1 - t, 5), io3: t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2,
           back: t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
           expo: t => t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)};
const cl = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const prog = (t, t0, d) => cl((t - t0) / d);
const fmt = (v, dec) => v.toFixed(dec).replace('.', ',');
const el = (cls, html, css) => { const d = document.createElement('div'); d.className = cls; if (html != null) d.innerHTML = html; if (css) d.style.cssText = css; return d; };
const cam = document.getElementById('cam');

function foto(clave, x, y, w, h, r) {
  const f = D.fotos[clave];
  const d = el('foto', null, `left:${x}px;top:${y}px;width:${w}px;height:${h}px;border-radius:${r}px`);
  const im = new Image(); im.src = f.src; im.style.objectPosition = `${f.fx}% ${f.fy}%`; im.style.transformOrigin = `${f.fx}% ${f.fy}%`; im._z = h < 1000 ? (f.z || 1) : 1;
  d.appendChild(im); d.img = im; return d;
}
function zoom(f, s, dx = 0, dy = 0) { f.img.style.transform = `translate(${dx}px,${dy}px) scale(${s * (f.img._z || 1)})`; }   // _z: acercamiento permanente del catálogo (plato con guarnición)
function fitW(e, maxW, mn, mx) { let lo = mn, hi = mx, best = mn; while (lo <= hi) { const m = (lo + hi) >> 1; e.style.fontSize = m + 'px'; if (e.offsetWidth <= maxW) { best = m; lo = m + 1; } else hi = m - 1; } e.style.fontSize = best + 'px'; return best; }
function fitWH(e, maxW, maxH, mn, mx) { let lo = mn, hi = mx, best = mn; while (lo <= hi) { const m = (lo + hi) >> 1; e.style.fontSize = m + 'px'; if (e.offsetWidth <= maxW && e.offsetHeight <= maxH) { best = m; lo = m + 1; } else hi = m - 1; } e.style.fontSize = best + 'px'; return best; }
function fitBox(e, W, H, mn, mx) { e.style.width = W + 'px'; let lo = mn, hi = mx, best = mn; while (lo <= hi) { const m = (lo + hi) >> 1; e.style.fontSize = m + 'px'; if (e.scrollHeight <= H + 1 && e.scrollWidth <= W + 1) { best = m; lo = m + 1; } else hi = m - 1; } e.style.fontSize = best + 'px'; return best; }
function pill(txt, x, y, tam) { const p = el('pill txt', txt, `left:${x}px;top:${y}px;font-size:${tam}px`); return p; }
function burst(root, n, seed, colores) {
  const ps = []; let sd = seed; const rnd = () => { sd = (sd * 1664525 + 1013904223) >>> 0; return sd / 4294967296; };
  for (let i = 0; i < n; i++) { const a = rnd() * Math.PI * 2, v = 380 + rnd() * 560, r = 9 + rnd() * 17;
    const d = el('velo', null, `left:0;top:0;width:${r * 2}px;height:${r * 2}px;border-radius:${rnd() < .55 ? '50%' : '5px'};background:${colores[i % colores.length]};opacity:0`); root.appendChild(d); ps.push({ d, a, v, r, rot: rnd() * 360 }); }
  return { set(dt, cx, cy) { ps.forEach(p => { if (dt < 0 || dt > .7) { p.d.style.opacity = 0; return; }
    const x = cx + Math.cos(p.a) * p.v * dt, y = cy + Math.sin(p.a) * p.v * dt + 1100 * dt * dt; p.d.style.opacity = 1 - dt / .7;
    p.d.style.transform = `translate(${x - p.r}px,${y - p.r}px) rotate(${p.rot + dt * 420}deg)`; }); } };
}
function cinta(root, texto, y) {
  const c = el('velo', null, `left:0;top:${y}px;width:1080px;height:66px;background:var(--o);overflow:hidden`);
  const t = el('', null, 'position:absolute;left:0;top:0;white-space:nowrap;color:#000;font-family:var(--ft);font-weight:900;text-transform:uppercase;font-size:46px;line-height:68px;padding-bottom:0');
  const una = texto + '  /  '; t.textContent = una.repeat(8); c.appendChild(t); root.appendChild(c);
  return (lt) => { const per = t.scrollWidth / 8; t.style.transform = `translateX(${-((lt * 330) % per)}px)`; };
}
function esc() { const r = el('esc'); r.style.display = 'block'; return r; }
const velo = (css) => el('velo', null, css);
const LIN = 'linear-gradient(';

/* ---- gancho: foto a toda pantalla y el titular en líneas que entran al pulso */
function sGancho(d, S) {
  const root = esc(); const f = foto(d.foto, 0, 0, 1080, 1920, 0); root.appendChild(f);
  root.appendChild(velo(`inset:0;background:${LIN}180deg,rgba(0,0,0,.66) 0,rgba(0,0,0,0) 24%,rgba(0,0,0,0) 36%,rgba(0,0,0,.93) 100%)`));
  const k = pill(d.etiqueta, 64, 270, 66); root.appendChild(k);
  const lines = d.lineas.map(h => { const l = el('t txt', h, 'left:64px;top:0;text-shadow:0 5px 34px rgba(0,0,0,.55)'); root.appendChild(l); return l; });
  const sub = d.sub ? el('c txt', d.sub, 'left:64px;top:1440px;font-size:40px;font-weight:700;letter-spacing:.02em;opacity:.95') : null; if (sub) root.appendChild(sub);
  document.getElementById('mundo').appendChild(root);
  // tamaño común: el de la línea más ancha, sin pasar de la altura disponible
  let px = 260; lines.forEach(l => { l.style.fontSize = '260px'; });
  lines.forEach(l => { px = Math.min(px, fitW(l, 886, 90, 260)); });
  const alto = 1420; let lh = px * .9;
  while (lines.length * lh > 900 && px > 90) { px -= 4; lh = px * .9; }
  lines.forEach((l, i) => { l.style.fontSize = px + 'px'; l.style.top = (alto - (lines.length - i) * lh) + 'px'; });
  return { root, upd(lt, dur) {
    zoom(f, 1.2 - .12 * prog(lt, 0, dur), 0, 0);
    lines.forEach((l, i) => { const t0 = i === 0 ? -.3 : i * .25 - .05; const p = prog(lt, t0, .28); l.style.opacity = lt >= t0 ? 1 : 0;
      l.style.transform = `scale(${1 + .45 * (1 - E.back(p))}) translateY(${(1 - E.out3(p)) * 40}px)`; l.style.transformOrigin = '0 70%'; });
    k.style.transform = `scale(${.7 + .3 * E.back(prog(lt, 0, .3))})`; k.style.transformOrigin = '0 50%';
    if (sub) sub.style.opacity = prog(lt, .6, .3);
  } };
}

/* ---- cuenta: una posición del ranking (tarjeta sobre su propia foto desenfocada) o el ganador (foto a toda pantalla) */
function sCuenta(d, S) {
  const root = esc(); const v = d.v; const gan = !!d.ganador;
  let bg = null;
  if (!gan) { bg = foto(d.foto, -140, -140, 1360, 2200, 0); bg.img.style.filter = 'blur(48px) brightness(.5) saturate(1.45)'; root.appendChild(bg); }
  const f = gan ? foto(d.foto, 0, 0, 1080, 1920, 0) : foto(d.foto, 64, 330, 952, 830, 56); root.appendChild(f);
  if (gan) root.appendChild(velo(`inset:0;background:${LIN}180deg,rgba(0,0,0,.55) 0,rgba(0,0,0,0) 26%,rgba(0,0,0,.2) 44%,rgba(0,0,0,.94) 100%)`));
  else root.appendChild(velo(`left:64px;top:330px;width:952px;height:830px;border-radius:56px;background:${LIN}180deg,rgba(0,0,0,0) 46%,rgba(0,0,0,.9) 100%)`));
  const chip = gan ? pill(d.unidad_chip, 64, 640, 50) : null; if (chip) root.appendChild(chip);
  const mover = gan ? null : cinta(root, d.unidad_chip, 250);
  const bd = gan ? 300 : 200;
  const badge = el('txt', `<span style="display:block;margin-top:${gan ? -10 : -6}px">${d.pos}</span>`, `position:absolute;left:${gan ? 64 : 92}px;top:${gan ? 290 : 358}px;width:${bd}px;height:${bd}px;border-radius:50%;background:var(--o);color:#000;font-family:var(--ft);font-weight:900;font-size:${gan ? 240 : 160}px;line-height:${bd}px;text-align:center`);
  root.appendChild(badge);
  const nombre = el('t txt', d.nombre_html, `left:${gan ? 64 : 100}px;white-space:normal;line-height:.9;font-size:80px;text-shadow:0 4px 26px rgba(0,0,0,.5)`);
  root.appendChild(nombre);
  const num = el('t txt', `<span class="n"></span><span class="u"></span>`, `left:64px;top:${gan ? 1130 : 1178}px;color:#fff;white-space:nowrap;line-height:.85`);
  root.appendChild(num);
  const nn = num.querySelector('.n'), uu = num.querySelector('.u'); uu.textContent = v.unidad ? ' ' + v.unidad : ''; uu.style.fontSize = '.5em'; uu.style.textTransform = 'none';
  const btop = gan ? 1440 : 1430;
  const barra = velo(`left:64px;top:${btop}px;width:886px;height:28px;border-radius:14px;background:rgba(255,255,255,.2)`);
  const relleno = velo(`left:64px;top:${btop}px;width:${886 * d.ratio}px;height:28px;border-radius:14px;background:var(--o);transform-origin:0 50%`);
  root.appendChild(barra); root.appendChild(relleno);
  const anillo = velo('left:240px;top:1240px;width:200px;height:200px;border-radius:50%;border:14px solid var(--o);opacity:0'); root.appendChild(anillo);
  const chispas = burst(root, gan ? 26 : 12, 31 + d.pos * 17, ['#B0BD6B', '#ffffff', '#B0BD6B']);
  const flash = velo('inset:0;background:#fff;opacity:0'); root.appendChild(flash);
  document.getElementById('mundo').appendChild(root);
  nn.textContent = fmt(v.num, v.dec);
  fitWH(num, 886, gan ? 300 : 235, 90, gan ? 440 : 330);
  fitBox(nombre, gan ? 880 : 790, gan ? 250 : 160, 52, gan ? 130 : 96);
  nombre.style.top = ((gan ? 1100 : 1135) - nombre.offsetHeight) + 'px';
  return { root, upd(lt, dur) {
    const tend = d.t_fin, ini = .1;
    const p = prog(lt, ini, tend - ini); nn.textContent = fmt(v.num * E.out3(p), v.dec);
    const hecho = lt >= tend;
    zoom(f, gan ? 1.22 - .1 * prog(lt, 0, dur) : 1.0 + .08 * prog(lt, 0, dur), 0, 0);
    if (bg) zoom(bg, 1.0 + .05 * prog(lt, 0, dur), 0, 0);
    const pc = E.back(prog(lt, 0, .3)); if (!gan) { f.style.transform = `scale(${.86 + .14 * pc}) rotate(${(1 - pc) * -3}deg)`; f.style.opacity = prog(lt, 0, .12); }
    const pb = E.back(prog(lt, .04, .28)); badge.style.transform = `scale(${.2 + .8 * pb})`; badge.style.transformOrigin = '50% 50%';
    const pn = E.out3(prog(lt, .1, .3)); nombre.style.opacity = pn; nombre.style.transform = `translateX(${(1 - pn) * -80}px)`;
    const pulso = hecho ? 1 + .12 * Math.sin(prog(lt, tend, .26) * Math.PI) : 1; num.style.transform = `scale(${pulso})`; num.style.transformOrigin = '0 70%';
    num.style.color = (gan || hecho) ? 'var(--o)' : '#fff';
    relleno.style.transform = `scaleX(${E.expo(p)})`;
    const pa = prog(lt, tend, .5); anillo.style.opacity = hecho && gan ? (1 - pa) * .55 : 0; anillo.style.transform = `scale(${.5 + 3.6 * E.out3(pa)})`; anillo.style.transformOrigin = '50% 50%';
    anillo.style.left = (64 + num.offsetWidth * .4 - 100) + 'px';
    flash.style.opacity = gan && hecho ? Math.max(0, 1 - (lt - tend) / .14) * .85 : 0;
    if (chip) { chip.style.transform = `scale(${.7 + .3 * E.back(prog(lt, 0, .3))})`; chip.style.transformOrigin = '0 50%'; }
    if (mover) mover(lt);
    chispas.set(lt - tend, 64 + num.offsetWidth * .5, (gan ? 1130 : 1178) + num.offsetHeight * .5);
  } };
}

/* ---- vs: dos fotos, dos cifras que suben, y el ganador marcado */
function sVs(d, S) {
  const root = esc();
  const fa = foto(d.izq.foto, 0, 0, 1080, 960, 0), fb = foto(d.der.foto, 0, 960, 1080, 960, 0); root.appendChild(fa); root.appendChild(fb);
  root.appendChild(velo(`left:0;top:0;width:1080px;height:960px;background:${LIN}180deg,rgba(0,0,0,.5) 0,rgba(0,0,0,0) 28%,rgba(0,0,0,.2) 50%,rgba(0,0,0,.88) 100%)`));
  root.appendChild(velo(`left:0;top:960px;width:1080px;height:960px;background:${LIN}180deg,rgba(0,0,0,.55) 0,rgba(0,0,0,.1) 30%,rgba(0,0,0,.25) 55%,rgba(0,0,0,.92) 100%)`));
  const ra = velo('left:0;top:0;width:1080px;height:960px;box-shadow:inset 0 0 0 0 var(--o);opacity:0'), rb = velo('left:0;top:960px;width:1080px;height:960px;opacity:0');
  ra.style.boxShadow = 'inset 0 0 0 18px #B0BD6B'; rb.style.boxShadow = 'inset 0 0 0 18px #B0BD6B'; root.appendChild(ra); root.appendChild(rb);
  const lados = [{ d: d.izq, y: 560, f: fa, r: ra, v: d.vi }, { d: d.der, y: 1010, f: fb, r: rb, v: d.vd }];
  lados.forEach(L => {
    L.n = el('t txt', L.d.nombre_html, `left:64px;top:${L.y}px;white-space:normal;line-height:.9;font-size:70px;text-shadow:0 4px 26px rgba(0,0,0,.5)`); root.appendChild(L.n);
    L.num = el('t txt', `<span class="n"></span><span class="u"></span>`, `left:64px;top:${L.y + 110}px;white-space:nowrap;line-height:.85;color:#fff`); root.appendChild(L.num);
    L.nn = L.num.querySelector('.n'); L.uu = L.num.querySelector('.u'); L.uu.textContent = L.v.unidad ? ' ' + L.v.unidad : ''; L.uu.style.fontSize = '.5em'; L.uu.style.textTransform = 'none';
  });
  const disco = el('txt', '<span style="display:block;margin-top:-4px">vs</span>', 'position:absolute;left:790px;top:890px;width:140px;height:140px;border-radius:50%;background:#000;border:10px solid var(--o);color:#fff;font-family:var(--ft);font-weight:900;font-size:62px;line-height:120px;text-align:center;text-transform:uppercase');
  root.appendChild(disco);
  const cap = d.texto ? el('pill txt', d.texto_html, 'left:64px;top:1350px;font-size:54px;white-space:nowrap;transform-origin:0 50%') : null; if (cap) root.appendChild(cap);
  const nota = d.nota ? el('c txt', d.nota, 'left:64px;top:1456px;font-size:34px;font-weight:700;opacity:.95') : null; if (nota) root.appendChild(nota);
  const flash = velo('inset:0;background:#fff;opacity:0'); root.appendChild(flash);
  const chA = burst(root, 12, 77, ['#B0BD6B', '#ffffff']), chB = burst(root, 26, 91, ['#B0BD6B', '#ffffff', '#B0BD6B']);
  document.getElementById('mundo').appendChild(root);
  lados.forEach(L => { L.nn.textContent = fmt(L.v.num, L.v.dec); fitWH(L.num, 560, 215, 90, 260); fitBox(L.n, 800, 130, 44, 90); L.n.style.top = (L.y + 100 - L.n.offsetHeight) + 'px'; });
  if (cap) { fitW(cap, 800, 36, 84); cap.style.top = (nota ? 1352 : 1380) + 'px'; }
  return { root, upd(lt, dur) {
    const a_fin = d.t_a, b_fin = d.t_b;
    zoom(fa, 1.02 + .08 * prog(lt, 0, dur), 0, 0); zoom(fb, 1.02 + .08 * prog(lt, 0, dur), 0, 0);
    const pa = E.out3(prog(lt, 0, .26)), pb = E.out3(prog(lt, .1, .26));
    fa.style.transform = `translateX(${(1 - pa) * -1080}px)`; fb.style.transform = `translateX(${(1 - pb) * 1080}px)`;
    lados[0].r.style.transform = fa.style.transform; lados[1].r.style.transform = fb.style.transform;
    const sa = prog(lt, .3, a_fin - .3), sb = prog(lt, a_fin + .1, b_fin - a_fin - .1);
    lados[0].nn.textContent = fmt(lados[0].v.num * E.out3(sa), lados[0].v.dec); lados[1].nn.textContent = fmt(lados[1].v.num * E.out3(sb), lados[1].v.dec);
    lados[0].n.style.opacity = prog(lt, .25, .2); lados[1].n.style.opacity = prog(lt, .35, .2);
    lados[0].num.style.opacity = prog(lt, .25, .2); lados[1].num.style.opacity = lt >= a_fin ? 1 : 0;
    lados[1].n.style.opacity = lt >= a_fin ? 1 : 0;
    const pa2 = lt >= a_fin ? 1 + .1 * Math.sin(prog(lt, a_fin, .24) * Math.PI) : 1, pb2 = lt >= b_fin ? 1 + .12 * Math.sin(prog(lt, b_fin, .26) * Math.PI) : 1;
    lados[0].num.style.transform = `scale(${pa2})`; lados[1].num.style.transform = `scale(${pb2})`; lados[0].num.style.transformOrigin = lados[1].num.style.transformOrigin = '0 70%';
    const g = d.ganador, hecho = lt >= b_fin;
    ['izq', 'der'].forEach((k, j) => { const gana = hecho && g === k; lados[j].num.style.color = hecho ? (gana || !g ? 'var(--o)' : '#fff') : '#fff'; lados[j].r.style.opacity = gana ? 1 : 0; });
    disco.style.transform = `scale(${E.back(prog(lt, .3, .3))}) rotate(${(1 - E.out3(prog(lt, .3, .4))) * -90}deg)`;
    if (cap) { const pc = E.back(prog(lt, b_fin + .05, .3)); cap.style.opacity = lt >= b_fin + .05 ? 1 : 0; cap.style.transform = `rotate(-3deg) scale(${.3 + .7 * pc})`; }
    if (nota) nota.style.opacity = prog(lt, b_fin + .3, .3);
    flash.style.opacity = lt >= b_fin ? Math.max(0, 1 - (lt - b_fin) / .14) * .7 : 0;
    chA.set(lt - a_fin, 64 + lados[0].num.offsetWidth * .5, lados[0].y + 110 + lados[0].num.offsetHeight * .5);
    chB.set(lt - b_fin, 64 + lados[1].num.offsetWidth * .5, lados[1].y + 110 + lados[1].num.offsetHeight * .5);
  } };
}

/* ---- cierre: la foto del gancho otra vez y el guardar; al final se apaga el texto para enlazar con el inicio */
function sCierre(d, S) {
  const root = esc(); const f = foto(d.foto, 0, 0, 1080, 1920, 0); root.appendChild(f);
  const v1 = velo(`inset:0;background:${LIN}180deg,rgba(0,0,0,.66) 0,rgba(0,0,0,.4) 30%,rgba(0,0,0,.6) 55%,rgba(0,0,0,.93) 100%)`); root.appendChild(v1);
  const v2 = velo(`inset:0;background:${LIN}180deg,rgba(0,0,0,.66) 0,rgba(0,0,0,0) 24%,rgba(0,0,0,0) 36%,rgba(0,0,0,.93) 100%);opacity:0`); root.appendChild(v2);
  const k = pill(d.etiqueta, 64, 270, 60); root.appendChild(k);
  const lines = d.lineas.map(h => { const l = el('t txt', h, 'left:64px;top:0;text-shadow:0 5px 34px rgba(0,0,0,.55)'); root.appendChild(l); return l; });
  const fu = el('c txt', d.fuente, 'left:64px;top:1410px;font-size:34px;font-weight:700;opacity:.92'); root.appendChild(fu);
  const yo = el('t txt', '@vivax_nutri', 'left:64px;top:1452px;font-size:40px;color:var(--o);letter-spacing:.04em;text-transform:none'); root.appendChild(yo);
  document.getElementById('mundo').appendChild(root);
  fitW(fu, 886, 22, 34);
  let px = 240; lines.forEach(l => { px = Math.min(px, fitW(l, 860, 80, 240)); });
  let lh = px * .9; while (lines.length * lh > 760 && px > 80) { px -= 4; lh = px * .9; }
  lines.forEach((l, i) => { l.style.fontSize = px + 'px'; l.style.top = (1380 - (lines.length - i) * lh) + 'px'; });
  return { root, upd(lt, dur) {
    zoom(f, 1.08 + .12 * prog(lt, 0, dur), 0, 0);
    const sal = 1 - prog(lt, dur - .45, .4);
    lines.forEach((l, i) => { const p = prog(lt, i * .22, .28); l.style.opacity = (lt >= i * .22 ? 1 : 0) * sal; l.style.transform = `scale(${1 + .4 * (1 - E.back(p))})`; l.style.transformOrigin = '0 70%'; });
    v1.style.opacity = sal; v2.style.opacity = 1 - sal; k.style.opacity = sal; fu.style.opacity = prog(lt, .5, .3) * sal * .92; yo.style.opacity = prog(lt, .5, .3) * sal;
  } };
}

/* ---- montaje: se hace con las fuentes ya cargadas (window.__init), porque los textos se miden al construirlos */
let scenes = [], banda = null, grano = null;
window.__init = () => {
  scenes = D.escenas.map(e => { const m = { gancho: sGancho, cuenta: sCuenta, vs: sVs, cierre: sCierre }[e.tipo](e.datos, e); m.t0 = e.t0; m.t1 = e.t1; m.dur = e.dur; m.trans = e.transicion; return m; });
  banda = el('velo', null, 'left:0;top:-300px;width:1500px;height:2500px;background:#B0BD6B;display:none;transform-origin:50% 50%');
  grano = el('velo', null, `inset:-300px;background-image:url(${D.grano});background-size:256px 256px;opacity:.14;mix-blend-mode:normal`);
  document.getElementById('mundo').appendChild(banda); cam.appendChild(grano);
  return scenes.length;
};

window.__setT = (t) => {
  let i = scenes.findIndex(s => t >= s.t0 - 1e-6 && t < s.t1 - 1e-6); if (i < 0) i = scenes.length - 1;
  const s = scenes[i], lt = t - s.t0;
  scenes.forEach(x => { x.root.style.display = 'none'; x.root.style.transform = ''; x.root.style.filter = ''; });
  banda.style.display = 'none';
  const tr = s.trans; let mostrar = [s];
  if (i > 0 && lt < tr.dur) {
    const prev = scenes[i - 1], p = prog(lt, 0, tr.dur);
    if (tr.tipo === 'empuje') {
      prev.upd(prev.dur - 1e-3, prev.dur); prev.root.style.display = 'block';
      prev.root.style.transform = `translateY(${-90 * E.io3(p)}px) scale(${1 - .05 * E.io3(p)})`; prev.root.style.filter = `brightness(${1 - .55 * p})`;
      s.root.style.transform = `translateY(${(1 - E.out3(p)) * 1920}px)`;
    } else {
      banda.style.display = 'block'; const q = E.io3(p); const cx = -800 + 2140 * q; banda.style.transform = `translateX(${cx - 750}px) rotate(11deg)`;
      if (p < .5) { prev.upd(prev.dur - 1e-3, prev.dur); mostrar = [prev]; }
    }
    if (tr.tipo === 'empuje') mostrar = [prev, s];
  }
  mostrar.forEach(x => { x.root.style.display = 'block'; });
  s.upd(lt, s.dur);
  // cámara: golpe de zoom y sacudida que se apagan
  let zc = 1, sx = 0, sy = 0;
  for (const g of D.golpes) { const dt = t - g.t; if (dt >= 0 && dt < .4) { const k = Math.exp(-dt * 9); zc += .034 * k * (g.amp / 20); sx += Math.sin(dt * 120 + g.t * 13) * g.amp * k * .6; sy += Math.cos(dt * 97 + g.t * 7) * g.amp * k * .6; } }
  cam.style.transform = `translate(${sx}px,${sy}px) scale(${zc})`;
  const f = Math.round(t * 30); grano.style.backgroundPosition = `${(f * 97) % 256}px ${(f * 61) % 256}px`;
  return i;
};

window.__zona = (t, zona) => {
  window.__setT(t);
  const bad = [];
  document.querySelectorAll('.txt').forEach(e => {
    let o = e, vis = true; while (o && o !== document.body) { const cs = getComputedStyle(o); if (cs.display === 'none' || parseFloat(cs.opacity) < .9) { vis = false; break; } o = o.parentElement; }
    if (!vis) return;
    const r = e.getBoundingClientRect(); if (r.width < 2) return;
    if (r.left < zona[0] || r.top < zona[1] || r.right > zona[2] || r.bottom > zona[3]) bad.push({ texto: (e.textContent || '').trim().slice(0, 40), r: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)] });
  });
  return bad;
};
"""

HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8"><style>
__FUENTES__
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;height:1920px;overflow:hidden;background:#000}
:root{--o:#B0BD6B;--ft:"Sofia";--fc:"Hanken"}
#cam{position:absolute;left:0;top:0;width:1080px;height:1920px;transform-origin:540px 960px;overflow:hidden}
#mundo{position:absolute;left:0;top:0;width:1080px;height:1920px}
.esc{position:absolute;left:0;top:0;width:1080px;height:1920px;overflow:hidden;background:#000;display:none}
.foto{position:absolute;overflow:hidden;background:#111}
.foto img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:cover}
.velo{position:absolute;pointer-events:none}
.t{position:absolute;font-family:var(--ft);font-weight:900;text-transform:uppercase;line-height:.88;color:#fff;white-space:nowrap;padding-bottom:.12em}
.t b.o{font-weight:900;color:var(--o)}
.c{position:absolute;font-family:var(--fc);font-weight:800;line-height:1.14;color:#fff;text-shadow:0 3px 18px rgba(0,0,0,.55)}
.c b.o{color:var(--o)}
.pill{position:absolute;background:var(--o);color:#000;font-family:var(--ft);font-weight:900;text-transform:uppercase;border-radius:999px;padding:6px 34px 12px;line-height:1;white-space:nowrap}
</style></head><body><div id="cam"><div id="mundo"></div></div><script>__JS__</script></body></html>"""


def _grano_uri() -> str:
    import base64
    import io
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(7)
    a = rng.integers(0, 256, (256, 256), dtype=np.uint8)
    rgba = np.zeros((256, 256, 4), np.uint8)
    claro = a > 150
    rgba[..., :3] = np.where(claro[..., None], 255, 0)
    rgba[..., 3] = np.where(claro, (a - 150) * 2, np.where(a < 90, (90 - a) * 2, 0)).clip(0, 255).astype(np.uint8)
    b = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(b, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def datos_pagina(guion: dict, plan: dict) -> dict:
    cat = json.loads(CATALOGO.read_text(encoding="utf-8"))
    fotos = {}
    for c in set(_fotos_de(guion)):
        f = cat[c]
        fx, fy = f.get("foco", [50, 50])
        fotos[c] = {"src": (RAIZ / f["archivo"]).resolve().as_uri(), "fx": fx, "fy": fy, "z": f.get("zoom") or 1}
    esc = []
    vmax = max([valor(e["valor"])["num"] for e in guion["escenas"] if e["tipo"] == "cuenta"] or [1.0])
    hook = guion["escenas"][0]
    for e, p in zip(guion["escenas"], plan["escenas"]):
        dur, pulso = p["dur"], plan["pulso_s"]
        if e["tipo"] == "gancho":
            lineas = e.get("_lineas") or partir_titular_auto(e["titular"], 900)
            datos = {"foto": e["foto"], "etiqueta": _escapar(e["etiqueta"]), "lineas": [marcado(x) for x in lineas], "sub": _escapar(e.get("sub", ""))}
        elif e["tipo"] == "cuenta":
            v = valor(e["valor"])
            datos = {"foto": e["foto"], "pos": e["pos"], "nombre_html": marcado(e["nombre"]), "v": v, "ratio": max(.06, min(1.0, v["num"] / vmax)),
                     "unidad_chip": _escapar(e.get("unidad_chip", "")), "ganador": bool(e.get("ganador")),
                     "t_fin": dur - (2 * pulso if e.get("ganador") else pulso)}
        elif e["tipo"] == "vs":
            a_fin = round((dur / 2 - pulso) / pulso) * pulso
            datos = {"izq": dict(e["izq"], nombre_html=marcado(e["izq"]["nombre"])), "der": dict(e["der"], nombre_html=marcado(e["der"]["nombre"])),
                     "vi": valor(e["izq"]["valor"]), "vd": valor(e["der"]["valor"]), "ganador": e.get("ganador"), "texto": e.get("texto", ""),
                     "texto_html": marcado(e.get("texto", "")), "nota": _escapar(e.get("nota", "")), "t_a": a_fin, "t_b": dur - 2 * pulso}
        else:
            datos = {"foto": e["foto"], "etiqueta": _escapar(e.get("etiqueta", "Guárdalo")),
                     "lineas": [marcado(x) for x in (e.get("_lineas") or partir_titular_auto(e["frase"], 760, 886, 240, 80))], "fuente": _escapar(e.get("fuente", ""))}
        esc.append({"tipo": e["tipo"], "datos": datos, "t0": p["t0"], "t1": p["t1"], "dur": p["dur"], "transicion": p["transicion"]})
    return {"fotos": fotos, "escenas": esc, "golpes": plan["golpes_cam"], "grano": _grano_uri()}


def construir_html(guion: dict, plan: dict) -> str:
    import temas as T
    tema = T.cargar(guion.get("cuenta"))
    js = JS.replace("__DATOS__", json.dumps(datos_pagina(guion, plan), ensure_ascii=False))
    html = HTML.replace("__FUENTES__", T.css_fuentes(FUENTES, tema)).replace("__JS__", js)
    if guion.get("angular"):          # reel angular (como los carruseles de la serie): ninguna forma redonda
        html = html.replace("</style>", "*{border-radius:0 !important}.pill{clip-path:polygon(0 0,100% 0,calc(100% - 22px) 100%,0 100%);padding-right:56px}</style>", 1)
    return html


# ------------------------------------------------------------------ dibujo y montaje
def _lanzar(pw):
    try:
        return pw.chromium.launch(args=ARGS_CHROME)
    except Exception:
        return pw.chromium.launch(channel="chrome", args=ARGS_CHROME)


def _encoder(salida: Path) -> subprocess.Popen:
    import video_medidas as ME
    cmd = [ME._ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ANCHO}x{ALTO}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-maxrate", "6M", "-bufsize", "12M", "-bf", "0", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-use_editlist", "0", "-an", str(salida)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def mezclar(video: Path, pista_wav: Path, salida: Path) -> Path:
    """Vídeo mudo + música en el MP4 final, sin lista de edición (como reel_mudo.mezclar) y con el AAC a 256 kb/s: a 192 kb/s los golpes
    fuertes subían el pico real más de 2 dB sobre el del WAV y obligaban a bajar toda la pista (medido en el reel de azúcar)."""
    import video_medidas as ME
    subprocess.run([ME._ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(pista_wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart", "-use_editlist", "0", str(salida)], check=True)
    return salida


def ajustar_pico(mudo: Path, musica_wav: Path, final: Path, carpeta: Path, registro: dict) -> Path:
    """Si el pico real del MP4 pasa de -1,6 dBTP se baja el WAV hasta -2,0 y se vuelve a mezclar (dos veces como mucho)."""
    import soundfile as sf
    import video_medidas as ME
    ajustes = []
    for _ in range(2):
        pico = ME.sonoridad(final).get("pico_dbtp")
        if pico is None or pico <= -1.6:
            break
        g = -(pico + 2.0)
        x, sr = sf.read(str(musica_wav), dtype="float64")
        sf.write(str(musica_wav), x * 10 ** (g / 20), sr, subtype="PCM_16")
        ajustes.append(round(g, 2))
        final = mezclar(mudo, musica_wav, carpeta / "video.mp4")
    registro["ajustes_pico_db"] = ajustes
    return final


def _tira(imgs: list, destino: Path, columnas: int = 5, ancho: int = 216) -> None:
    from PIL import Image
    alto = int(ancho * ALTO / ANCHO)
    filas = (len(imgs) + columnas - 1) // columnas
    hoja = Image.new("RGB", (columnas * (ancho + 8) + 8, filas * (alto + 8) + 8), (60, 60, 60))
    for k, im in enumerate(imgs):
        hoja.paste(im.resize((ancho, alto), Image.LANCZOS), (8 + (k % columnas) * (ancho + 8), 8 + (k // columnas) * (alto + 8)))
    hoja.save(destino, "JPEG", quality=88)


def tiempos_de_zona(plan: dict) -> list[float]:
    """Instantes en que se mide la zona segura: cada escena, cuando todo ha entrado y no hay golpe de cámara cerca."""
    sal = []
    golpes = [g["t"] for g in plan["golpes_cam"]]
    for e in plan["escenas"]:
        for fr in (0.55, 0.8, 0.97):
            t = e["t0"] + e["dur"] * fr
            if e["tipo"] == "cierre":
                t = e["t0"] + e["dur"] * fr * 0.9            # antes de que se apague el texto
            if all(not (0 <= t - g < .45) for g in golpes) and t < plan["duracion"] - 0.04:
                sal.append(round(t, 3))
    return sal


def render(guion: dict, carpeta: Path, solo: list[float] | None = None) -> dict:
    """Dibuja y compone el reel en `carpeta`: video_mudo.mp4 (sin audio), video.mp4 (con música), musica.wav, eventos.json, registro.json,
    portada.jpg y fotogramas.jpg."""
    import io
    import musica_propia as MP
    import video_medidas as ME
    from PIL import Image
    from playwright.sync_api import sync_playwright
    inicio = time.perf_counter()
    err = validar(guion)
    if err:
        raise ErrorReel("el guion no es válido: " + " | ".join(err))
    plan = planificar(guion)
    carpeta.mkdir(parents=True, exist_ok=True)
    registro: dict = {"id": guion["id"], "esquema": "foto-1", "estilo": plan["estilo"], "pulso_s": plan["pulso_s"], "pulsos": plan["pulsos"],
                      "duracion_s": round(plan["duracion"], 3), "fps": FPS, "avisos": [], "escenas": [
                          {k: e[k] for k in ("i", "tipo", "t0", "t1", "pulsos")} for e in plan["escenas"]]}
    if not DUR_MIN_S <= plan["duracion"] <= DUR_MAX_S:
        registro["avisos"].append(f"el reel dura {plan['duracion']:.1f} s: lo previsto es de {DUR_MIN_S:.0f} a {DUR_MAX_S:.0f} s")
    (carpeta / "eventos.json").write_text(json.dumps(plan["eventos"], ensure_ascii=False, indent=1), encoding="utf-8")
    musica_wav = carpeta / "musica.wav"
    if not solo:
        r_m = MP.componer(plan["roles"], plan["estilo"], int(guion["musica"].get("semilla", 7)), plan["golpes_musica"], salida=musica_wav,
                          eventos_fx=plan["eventos"])
        registro["musica"] = {k: v for k, v in r_m["informe"].items() if k in ("estilo", "bpm", "semilla", "pulsos", "duracion_s", "lufs", "pico_dbtp", "recortes_muestras", "efectos")}
    html = construir_html(guion, plan)
    with tempfile.TemporaryDirectory(prefix="vivax_reelfoto_") as tmp, sync_playwright() as pw:
        ruta = Path(tmp) / "reel.html"
        ruta.write_text(html, encoding="utf-8")
        nav = _lanzar(pw)
        try:
            pag = nav.new_context(viewport={"width": ANCHO, "height": ALTO}, device_scale_factor=1).new_page()
            consola: list[str] = []
            pag.on("console", lambda m: consola.append(m.text) if m.type in ("error", "warning") else None)
            pag.on("pageerror", lambda e: consola.append(str(e)))
            pag.goto(ruta.as_uri())
            pag.evaluate("document.fonts.ready.then(() => true)")
            ok = pag.evaluate("() => Promise.all([['900','Sofia'],['800','Hanken'],['700','Hanken']].map(([w,f]) => document.fonts.load(`${w} 100px ${f}`))).then(r => r.map(x => x.length))")
            if not all(ok):
                raise ErrorReel("No cargan las fuentes locales")
            pag.evaluate("window.__init()")
            pag.evaluate("() => Promise.all(Array.from(document.images).map(i => i.decode().catch(() => null)))")
            clip = {"x": 0, "y": 0, "width": ANCHO, "height": ALTO}

            def fotograma(t: float):
                pag.evaluate("(t) => window.__setT(t)", t)
                return Image.open(io.BytesIO(pag.screenshot(type="jpeg", quality=93, clip=clip))).convert("RGB")

            if solo:
                _tira([fotograma(t) for t in solo], carpeta / "fotogramas.jpg", columnas=min(5, len(solo)))
                return {"solo": True}
            # zona segura, con las cajas reales del DOM (cámara incluida)
            fuera = []
            for t in tiempos_de_zona(plan):
                for m in pag.evaluate("([t, z]) => window.__zona(t, z)", [t, list(ZONA)]):
                    fuera.append({"t": t, **m})
            registro["zona_segura"] = {"zona": list(ZONA), "muestras": len(tiempos_de_zona(plan)), "fuera": fuera}
            mudo = carpeta / "video_mudo.mp4"
            ff = _encoder(mudo)
            n = plan["n_frames"]
            primero = ultimo = None
            k_portada = int(round((plan["escenas"][0]["t1"] - 0.2) * FPS))      # la portada: el gancho con todas sus líneas ya puestas
            contacto_t = sorted({round(plan["duracion"] * (k + .5) / 15, 3) for k in range(15)})
            contacto, contacto_k = [], {int(round(t * FPS)) for t in contacto_t}
            try:
                for k in range(n):
                    img = fotograma(k / FPS)
                    ff.stdin.write(img.tobytes())
                    if k == 0:
                        primero = img.copy()
                    if k == k_portada:
                        img.save(carpeta / "portada.jpg", "JPEG", quality=92)
                    if k in contacto_k:
                        contacto.append(img.copy())
                    ultimo = img
                ff.stdin.close()
                if ff.wait() != 0:
                    raise ErrorReel("ffmpeg falló al codificar")
            finally:
                if ff.poll() is None:
                    ff.kill()
            _tira(contacto, carpeta / "fotogramas.jpg")
            import numpy as np
            a, b = np.asarray(primero.resize((108, 192)), float), np.asarray(ultimo.resize((108, 192)), float)
            registro["bucle"] = {"dif_ultimo_primero": round(float(np.abs(a - b).mean()), 2)}
            registro["consola_navegador"] = consola[:10]
        finally:
            nav.close()
    final = mezclar(mudo, musica_wav, carpeta / "video.mp4")
    final = ajustar_pico(mudo, musica_wav, final, carpeta, registro)
    registro["tiempo_render_s"] = round(time.perf_counter() - inicio, 1)
    registro["salida"] = {"con_musica": ME.info_mp4(final), "sonoridad": ME.sonoridad(final), "listas_de_edicion": ME.listas_de_edicion(final),
                          "mudo_listas_de_edicion": ME.listas_de_edicion(mudo), "mudo_tiene_audio": ME.tiene_audio(mudo),
                          "musica_tiene_audio": ME.tiene_audio(final), "tamano_mb": round(final.stat().st_size / 1e6, 2)}
    (carpeta / "registro.json").write_text(json.dumps(registro, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return registro


# ------------------------------------------------------------------ controles
def hojas_de_texto(guion: dict) -> list[dict]:
    """Cada escena como una hoja con los campos de texto que leen los controles de controles.py (sin la posición ni la animación)."""
    hojas = []
    for e in guion["escenas"]:
        h: dict = {"plantilla": "reel_" + e["tipo"]}
        if e["tipo"] == "gancho":
            h.update(titular=_sin_marcas(e["titular"]), texto=e.get("sub", ""))
        elif e["tipo"] == "cuenta":
            h.update(unidad=e.get("unidad_chip", ""), filas=[{"nombre": _sin_marcas(e["nombre"]), "valor": e["valor"]}])
        elif e["tipo"] == "vs":
            h.update(izq={"nombre": e["izq"]["nombre"], "valor": e["izq"]["valor"]},
                     der={"nombre": e["der"]["nombre"], "valor": e["der"]["valor"]}, texto=" ".join(x for x in (_sin_marcas(e.get("texto", "")), e.get("nota", "")) if x))
        else:
            h.update(frase=_sin_marcas(e["frase"]), fuentes=[e.get("fuente", "")])
        hojas.append(h)
    return hojas


def controlar_reel(guion: dict, registro: dict | None = None) -> list[tuple[str, str, str]]:
    """Controles de texto de controles.py (cifras contra el banco, líneas rojas, texto de IA, densidad, pie) y los de vídeo que valen para
    un reel mudo de fotos: duración, zona segura, sonoridad, pico real, listas de edición y que la versión muda no tenga audio."""
    import controles as C
    salida: list = []
    pieza = {"id": guion["id"], "cuenta": guion["cuenta"], "formato": "video", "hechos": guion["hechos"], "pie_instagram": guion["pie_instagram"],
             "hojas": hojas_de_texto(guion), "estilo": "viral", "direccion": guion.get("direccion")}
    try:
        banco = C.cargar_banco(guion["cuenta"])
    except Exception as exc:                                                    # sin banco legible no se da nada por bueno
        return [(C.FALLO, "banco", str(exc))]
    pieza["_citas"] = C.citas_de(pieza, banco)
    C.c_estructura(pieza, salida)
    C.c_cifras(pieza, banco, salida)
    C.c_lineas_rojas(pieza, salida)
    C.c_texto_ia(pieza, salida)
    C.c_descargo(pieza, salida)
    C.c_densidad(pieza, salida)
    for e in guion["escenas"]:
        if e["tipo"] == "vs" and (e.get("texto") or e.get("nota")):
            salida.append((C.AVISO, "texto a mano", "la frase del duelo está escrita a mano: comprueba que dice lo mismo que las cifras de los dos lados "
                           f"(«{e.get('texto', '')}» / «{e.get('nota', '')}»)"))
    if registro:
        d = registro.get("duracion_s", 0)
        if not DUR_MIN_S <= d <= DUR_MAX_S:
            salida.append((C.AVISO, "duración", f"{d} s: lo previsto es de {DUR_MIN_S:.0f} a {DUR_MAX_S:.0f} s"))
        fuera = registro.get("zona_segura", {}).get("fuera", [])
        for f in fuera[:6]:
            salida.append((C.FALLO, "zona segura", f"a {f['t']} s el texto «{f['texto']}» sale de la zona segura {f['r']}"))
        s = registro.get("salida", {})
        son = s.get("sonoridad", {})
        if "lufs" in son and not -15.5 <= son["lufs"] <= -12.5:
            salida.append((C.AVISO, "sonoridad", f"{son['lufs']} LUFS (objetivo -14)"))
        if "pico_dbtp" in son and son["pico_dbtp"] > -1.0:
            salida.append((C.FALLO, "pico", f"pico real {son['pico_dbtp']} dBTP (tiene que quedar por debajo de -1)"))
        if s.get("listas_de_edicion") or s.get("mudo_listas_de_edicion"):
            salida.append((C.FALLO, "listas de edición", "el MP4 trae listas de edición (Instagram las rechaza en Reels)"))
        if s.get("mudo_tiene_audio"):
            salida.append((C.FALLO, "mudo", "video_mudo.mp4 trae pista de audio"))
        if not s.get("musica_tiene_audio"):
            salida.append((C.FALLO, "audio", "video.mp4 no trae pista de audio"))
        if s.get("tamano_mb", 0) > 18:
            salida.append((C.FALLO, "tamaño", f"{s['tamano_mb']} MB (jsDelivr sirve hasta 20 MB; el tope del motor es 18)"))
        if registro.get("consola_navegador"):
            salida.append((C.AVISO, "navegador", "; ".join(registro["consola_navegador"])[:200]))
    return salida


# ------------------------------------------------------------------ guion a partir de un carrusel de la dirección F
def reel_desde_pieza(pieza: dict, modo: str = "cuenta_atras", estilo: str = "house", semilla: int = 7, id_reel: str | None = None,
                     texto_vs: str = "", nota_vs: str = "", frase_cierre: str | None = None) -> dict:
    """Convierte un carrusel de piezas/f/ en un guion de reel.

    modo «cuenta_atras»: gancho (la portada), el ranking del último al primero (3 pulsos cada uno), el ganador a toda pantalla, el primer
    duelo `f_vs` si lo hay y el cierre. modo «duelo»: gancho, el primer `f_vs` (8 pulsos), el resto del ranking en cuenta atrás rápida
    (3 pulsos cada uno) y el cierre."""
    hojas = pieza["hojas"]
    portada = next(h for h in hojas if h["plantilla"] in ("f_portada", "f_letra"))
    ranking = next((h for h in hojas if h["plantilla"] == "f_ranking"), None)
    vs = next((h for h in hojas if h["plantilla"] == "f_vs"), None)
    cierre = next((h for h in hojas if h["plantilla"] == "f_cierre"), {})
    if modo not in ("cuenta_atras", "duelo"):
        raise ErrorReel(f"modo desconocido: {modo}")
    if modo == "duelo" and not vs:
        raise ErrorReel("el modo duelo necesita una hoja f_vs")
    if modo == "cuenta_atras" and not ranking:
        raise ErrorReel("el modo cuenta_atras necesita una hoja f_ranking")
    esc: list[dict] = []
    n_lineas = len(partir_titular_auto(portada["titular"], 900))
    esc.append({"tipo": "gancho", "pulsos": 4 + max(0, n_lineas - 4 + 1) // 2, "foto": portada["foto"], "etiqueta": portada.get("etiqueta", ""), "titular": portada["titular"],
                "sub": "Cuenta atrás por 100 g" if modo == "cuenta_atras" else "Duelo por 100 g"})
    chip = (ranking or {}).get("unidad", "").replace("µg", "mcg")          # la cinta va en versales y «µ» en versales es una eme griega: se leería «mg»
    if len(chip) > 32 and "por 100 g" in chip:                            # la cinta cabe en una línea: se queda con «unidad por 100 g» (sin «, en el estado indicado»)
        chip = chip[:chip.index("por 100 g") + len("por 100 g")]

    def duelo(pulsos: int) -> dict:
        d = {"tipo": "vs", "pulsos": pulsos, "titulo": vs.get("titulo", ""), "izq": dict(vs["izq"]), "der": dict(vs["der"]), "texto": texto_vs}
        if vs.get("ganador"):
            d["ganador"] = vs["ganador"]
            if d["ganador"] == "izq":                      # la cifra que gana se revela la última (abajo), como el remate del duelo
                d["izq"], d["der"], d["ganador"] = d["der"], d["izq"], "der"
        if nota_vs:
            d["nota"] = nota_vs
        return d

    if modo == "cuenta_atras":
        filas = ranking["filas"]
        for pos in range(len(filas), 0, -1):
            f = filas[pos - 1]
            e = {"tipo": "cuenta", "pulsos": 4 if pos == 1 else 3, "pos": pos, "nombre": f["nombre"], "valor": f["valor"], "foto": f["foto"], "unidad_chip": chip}
            if pos == 1:
                e["ganador"] = True
            esc.append(e)
        if vs:
            esc.append(duelo(6))
    else:
        esc.append(duelo(8))
        filas = ranking["filas"] if ranking else []
        for pos in range(len(filas), 2, -1):
            f = filas[pos - 1]
            esc.append({"tipo": "cuenta", "pulsos": 3, "pos": pos, "nombre": f["nombre"], "valor": f["valor"], "foto": f["foto"], "unidad_chip": chip})
    fuente = (cierre.get("fuentes") or ["USDA FoodData Central"])[0]
    esc.append({"tipo": "cierre", "pulsos": 5 if modo == "cuenta_atras" else 4, "foto": portada["foto"], "etiqueta": "Guárdalo",
                "frase": frase_cierre or cierre.get("frase") or "Guárdalo para {releerlo}", "fuente": "Fuente: " + fuente})
    return {"id": id_reel or pieza["id"].replace("-f-", "-r-"), "cuenta": pieza.get("cuenta", "n1"), "formato": "video", "esquema": "foto-1", "estilo": "viral",
            "diseno": "foto", "origen": pieza["id"], "modo": modo, "hechos": list(pieza["hechos"]), "pie_instagram": pieza["pie_instagram"],
            "musica": {"estilo": estilo, "semilla": semilla}, "fps": FPS, "escenas": esc, **({"angular": True} if pieza.get("angular") else {})}


# ------------------------------------------------------------------ línea de órdenes
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("guion", nargs="?", type=Path)
    ap.add_argument("--desde-pieza", metavar="ID", help="id de un carrusel de piezas/f/ (p. ej. n1-f-01-hierro)")
    ap.add_argument("--modo", default="cuenta_atras", choices=("cuenta_atras", "duelo"))
    ap.add_argument("--estilo", default="house", choices=("house", "lofi", "hiphop"))
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--id", dest="id_reel")
    ap.add_argument("--texto-vs", default="", help="frase corta del duelo (sin cifras que no estén en los hechos)")
    ap.add_argument("--nota-vs", default="", help="nota pequeña bajo el duelo")
    ap.add_argument("--frase-cierre", default=None, help="por defecto, la frase del cierre del carrusel (nunca una recomendación de compra)")
    ap.add_argument("--solo", help="instantes (s) separados por comas: solo dibuja esos fotogramas")
    ap.add_argument("--sin-dibujar", action="store_true", help="solo escribe el guion")
    a = ap.parse_args()
    if a.desde_pieza:
        pieza = json.loads((RAIZ / "piezas" / "f" / f"{a.desde_pieza}.json").read_text(encoding="utf-8"))
        guion = reel_desde_pieza(pieza, a.modo, a.estilo, a.semilla, a.id_reel, a.texto_vs, a.nota_vs, a.frase_cierre)
        ruta = RAIZ / "piezas" / "f_reels" / f"{guion['id']}.json"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(guion, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"guion: {ruta}")
    elif a.guion:
        guion = json.loads(a.guion.read_text(encoding="utf-8"))
    else:
        ap.error("falta el guion o --desde-pieza")
    if a.sin_dibujar:
        return 0
    carpeta = RAIZ / "salida" / guion["id"]
    solo = [float(x) for x in a.solo.split(",")] if a.solo else None
    try:
        reg = render(guion, carpeta, solo)
    except ErrorReel as exc:
        print(f"ERROR: {exc}")
        return 2
    if solo:
        print(f"fotogramas en {carpeta / 'fotogramas.jpg'}")
        return 0
    res = controlar_reel(guion, reg)
    import controles as C
    for nivel, quien, msg in res:
        if nivel != "INFO":
            print(f"{nivel:6} [{quien}] {msg}")
    print(f"{guion['id']}: {reg['duracion_s']} s, {reg['salida']['tamano_mb']} MB, render {reg['tiempo_render_s']} s, "
          f"{sum(1 for r in res if r[0] == C.FALLO)} fallos, {sum(1 for r in res if r[0] == C.AVISO)} avisos")
    return 1 if any(r[0] == C.FALLO for r in res) else 0


if __name__ == "__main__":
    raise SystemExit(main())
