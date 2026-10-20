"""Controles de una pieza de Vivax Nutrición antes de enseñarla.

Uso:  python controles.py piezas/muestra_01_desayuno.json [--imagenes salida/muestra-01-desayuno]

Siete controles. Los FALLOS bloquean el envío; los AVISOS se revisan a mano.
 1. Estructura: carrusel de 5 a 10 hojas (imagen única: 1), pie de 250 a 900 caracteres, un emoji como mucho, de 0 a 4 etiquetas.
 2. Cifras: cada número de las hojas y del pie sale de un hecho citado del banco (o de un ejemplo declarado).
 3. Líneas rojas de salud y de publicidad (plan v2, apartado 6.8): promesas, pautas personales, testimonios, titulación.
 4. Texto de IA: las 114 reglas de `datos/reglas_texto_ia.json` (restos de chat, fórmulas, vocabulario, ritmo).
 5. Descargo y fuentes: la última hoja trae el aviso y cada hoja con cifras trae su fuente.
 6. Paleta: porcentaje de píxeles fuera del triángulo de los tres colores del tema (si hay imágenes).
 7. Densidad: palabras por hoja. José pidió poco texto y más impacto; cada plantilla tiene su tope.

Un hecho del banco sigue «pendiente: dietista-nutricionista colegiado» mientras José no lo valide (/validar en el bot): la pieza
sale como muestra y nunca se publica con esa marca. Un hecho que José ha marcado «no usar» es un FALLO si la pieza lo cita.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import plantillas as P
import plantillas_d as D
import temas as T
import validaciones as V

RAIZ = Path(__file__).resolve().parent
RUTA_BANCO = RAIZ / "datos" / "banco_hechos.json"
RUTA_REGLAS = RAIZ / "datos" / "reglas_texto_ia.json"

FALLO, AVISO = "FALLO", "AVISO"

# Números escritos con palabras, de cero a cien. Sin los compuestos con «y»: «treinta y dos» se lee como «treinta».
NUMEROS_EN_PALABRAS = {"cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
                       "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
                       "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
                       "veintiuno": 21, "veintiún": 21, "veintidós": 22, "veintidos": 22, "veintitrés": 23, "veintitres": 23,
                       "veinticuatro": 24, "veinticinco": 25, "veintiséis": 26, "veintiseis": 26, "veintisiete": 27,
                       "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
                       "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100}
NUM_PALABRA = "(?:" + "|".join(re.escape(p) for p in sorted(NUMEROS_EN_PALABRAS, key=len, reverse=True)) + ")"
# «dos de cada tres», «cinco por ciento». Un «un» o un «dos» sueltos son palabras corrientes, no cifras.
RX_FRACCION_ESCRITA = re.compile(r"\b(" + NUM_PALABRA + r")\s+de\s+cada\s+(" + NUM_PALABRA + r")\b|\b(" + NUM_PALABRA + r")\s+por\s+(?:ciento|cien)\b", re.I)

# ---------------------------------------------------------------------------------------------- líneas rojas
LINEAS_ROJAS = [
    ("L1 pauta personal", FALLO, r"\b(?:tu|su) (?:dieta|men[uú]|plan (?:de )?(?:alimentaci[oó]n|comidas))\b|\bte (?:recomiendo|aconsejo|pauto)\b|\bmen[uú]s? personalizad\w+|\bdeber[ií]as (?:tomar|comer|seguir|hacer)\b|\bcalor[ií]as que necesitas\b|\b(?:men[uú]s?|dietas?|planes?) (?:semanal(?:es)? |diari[oa]s? )?(?:para|de) (?:perder|adelgazar|bajar)\b|\bte (?:dejo|paso|mando|comparto) (?:mi|un|el) (?:men[uú]|dieta|plan)\b"),
    ("L2 consulta por privado", FALLO, r"\b(?:escr[ií]beme|m[aá]ndame|h[aá]blame) (?:por|al) (?:privado|dm|md)\b|\bconsulta (?:gratis|gratuita|personal)\b"),
    ("L3 antes y después", FALLO, r"\bantes y despu[eé]s\b|\b(?:perd[ií]|baj[eé]|adelgac[eé]|he perdido|he bajado|he adelgazado|consegu[ií] (?:bajar|perder|adelgazar)) (?:\d|unos? \d|\w+ (?:kilos?|kg)\b)|\bmi (?:transformaci[oó]n|cambio f[ií]sico)\b"),
    ("L4 promesa sobre enfermedades", FALLO,
     r"(?<!no )(?<!ni )(?<!sin )\b(?:cura|curas|curan|curando|curar(?:á|ás|án|é|emos)?|curaci[oó]n(?:es)?|reviert(?:e|en)|revertir|revert(?:irá|irás|irán)|sanar(?:á|ás|án|é|emos)?|sanan)\b"
     r"|\b(?:elimina|quita|baja|reduce) (?:la |el |tu )?(?:diabetes|colesterol|hipertensi[oó]n|tensi[oó]n|insomnio)\b"
     r"|\bprevien(?:e|en) (?:el )?(?:c[aá]ncer|diabetes|infarto)\b"
     r"|(?<!no )(?<!ni )(?<!sin )\bsana (?:la |el |tu |su |a )?(?:diabetes|colesterol|hipertensi\w*|tensi[oó]n|insomnio|c[aá]ncer|infarto|obesidad|enfermedad\w*|h[ií]gado)\b"),
    ("L5 garantía de kilos o plazos", FALLO,
     r"\bgarantiz\w+|\bsin esfuerzo\b|\bseguro que (?:adelgaz|pierd)\w*|\badelgazar(?:á|ás|án|emos)\b|\b(?:perder|bajar)(?:á|ás|án|emos)\s+(?:de\s+)?(?:peso|kilos?|kg|grasa)\b"
     r"|\b(?:\d+|" + NUM_PALABRA + r")\s*(?:kilos?|kg)\s+en\s+(?:\d+|" + NUM_PALABRA + r")\s+(?:d[ií]as?|semanas?|mes(?:es)?)\b"
     r"|\bresultados? (?:r[aá]pidos?|asegurados?)\b|\bmilagro\b"),
    ("L6 «bajo en carbohidratos» como declaración de producto", FALLO, r"\b(?:producto|alimento|suplemento|snack|barrita|galleta|pan) (?:bajo en (?:carbohidratos|hidratos)|keto|cetog[eé]nic\w+)\b"),
    ("L8 suplementos con efectos", FALLO, r"\bsuplement\w+ (?:que|para) (?:quema|adelgaza|cura|reduce)\w*"),
    ("L9 testimonio", FALLO, r"\b(?:mi experiencia|yo perd[ií]|a m[ií] me funcion[oó]|testimonio|mi paciente|mis pacientes|una paciente|un paciente|una clienta|un cliente)\b"),
    ("L13 titulación", FALLO, r"\bsoy (?:un |una )?(?:dietista|nutricionista|m[eé]dic[oa]|dietista-nutricionista)\b|\bcomo (?:dietista|nutricionista|m[eé]dic[oa])\b|\batiendo a (?:mis|mi|nuestros|tus|sus) pacientes\b|\bespecialista en nutrici[oó]n\b"),
    ("L14 titulación o revisión profesional atribuida", FALLO,
     r"\bgraduad[oa] en (?:nutrici[oó]n|diet[eé]tica)\b|\bnutrici[oó]n humana y diet[eé]tica\b|"
     r"\b(?:revisad[oa]|validad[oa]|avalad[oa]|supervisad[oa]|aprobad[oa]) por (?:un[ao]? |el |la )?(?:dietista|nutricionista|graduad\w*|m[eé]dic\w*)|"
     r"\b(?:revisad[oa]|validad[oa]|avalad[oa]|supervisad[oa]) por jos[eé]\b|\b(?:revis|valid|aval|supervis)\w* jos[eé]\b"),
    ("L15 nombre de la casa", FALLO, r"\bjos[eé]\b"),                  # el nombre de José no aparece en ninguna pieza ni texto, con verbo o sin él
    ("L18 el colesterol LDL alto «no importa»", FALLO,                 # línea de la casa (12-oct-2026): el colesterol es necesario y la calidad de la LDL importa, pero no se niega que el LDL alto importe
     r"\bel problema (?:no es|nunca fue) (?:el )?(?:colesterol|ldl)\b|\bel (?:colesterol|ldl)(?: alto| malo)? no es (?:el|un) problema\b"
     r"|\b(?:colesterol|ldl)(?: alto| malo)? no (?:importa|importan|pasa nada)\b|\bel (?:colesterol|ldl) alto es inocuo\b"
     r"|\bno (?:te )?preocupes? (?:por|de) (?:el |tu )?(?:colesterol|ldl)\b"),
    ("L19 la insulina «mala» o que «inflama»", FALLO,                    # es una hormona necesaria; lo que se asocia a más riesgo es la resistencia a la insulina
     r"\bla insulina (?:es|resulta|son) (?:mala|mal[ií]sima|maligna|t[oó]xica|peligrosa|un veneno|la culpable|el enemigo)\b"
     r"|\b(?:la )?insulina (?:alta )?(?:inflama|causa inflamaci[oó]n|provoca inflamaci[oó]n|produce inflamaci[oó]n)\b|\b(?:la )?insulina (?:te )?(?:engorda|envenena)\b"),
    ("Tono: miedo o culpa", AVISO,r"\bte est[aá]n (?:enga[nñ]ando|envenenando|matando)\b|\bveneno\b|\bla verdad que no quieren\b"),
]
# Reglas que miran frase a frase: una negación («no dejes la medicación»), o remitir al médico en la misma frase, no es una promesa.
PROMESAS_DE_FRASE = [
    ("L15 dejar, quitar o cambiar la medicación", FALLO, r"\b(?:dej|quit|reduc|suspend|sustitu|cambi)\w*\s+(?:\w+\s+){0,3}?"
                                                         r"(?:medicaci[oó]n|medicina|medicamento|pastillas?|tratamiento|metformina|insulina|antidiab[eé]tic\w*)\b"),
    ("L16 promesa de peso o de grasa", FALLO, r"\b(?:te|os|le|les|nos)\s+(?:hace|har[aá]|ayuda|ayudar[aá]|permite|consigue|conseguir[aá])\s+"
                                              r"(?:a\s+)?(?:adelgazar|perder peso|bajar de peso|quemar grasa)\b|"
                                              r"\b(?:perder|adelgazar|bajar)\s+(?:unos?\s+)?\d+\s*(?:kilos?|kg)\b|\bquem\w*\s+(?:la\s+)?grasa\s+mientras\b"),
    ("L17 remisión de una enfermedad", FALLO, r"\bremisi[oó]n\b"),
    ("L20 «colesterol malo»", FALLO, r"\b(?:colesterol malo|mal colesterol)\b"),     # la LDL es una lipoproteína que transporta colesterol, no «colesterol malo» (mirada de la casa, 12-oct-2026); con una negación en la frase pasa
    ("desintoxicación como promesa", AVISO, r"\bdesintoxic\w*"),
    ("«quema grasa» sin explicar", AVISO, r"\bquem\w*\s+(?:la\s+)?grasa\b"),
]
PROTECCION_DE_FRASE = re.compile(r"\b(?:no|nunca|jam[aá]s|ni|sin|antes de|evit\w*|consult\w*|habl\w*|pregunt\w*)\b", re.I)
ALERTA_PESO = r"\b(?:adelgaz\w+|perder peso|p[eé]rdida de peso|bajar de peso)\b"
RESULTADOS_DE_SALUD = (r"\b(?:peso|kilos?|kg|adelgaz\w*|glucosa|az[uú]car en (?:la )?sangre|hemoglobina|hba1c|colesterol|triglic[eé]ridos|tensi[oó]n|insulina|"
                       r"medicaci[oó]n|medicinas?|diabet\w+|enfermedad\w*|salud|sanar|curar|remisi[oó]n|grasa corporal|cintura|inflamaci[oó]n)\b")
ENFERMEDADES = r"\b(?:diabetes|colesterol|hipertensi[oó]n|c[aá]ncer|epilepsia|infarto|enfermedad renal|ovario poliqu[ií]stico|h[ií]gado graso|trastornos? de la conducta alimentaria)\b"


# ---------------------------------------------------------------------------------------------- utilidades
RUTA_NOVEDADES = RAIZ / "datos" / "novedades_2025_2026.json"
RUTA_VOCES = RAIZ / "datos" / "voces_de_estudios.json"
RUTA_AMPLIACION = RAIZ / "datos" / "novedades_ampliacion_2026-10.json"       # 19 hechos de octubre de 2026, abiertos de nuevo en su fuente por un verificador independiente
RUTA_COMPOSICION = RAIZ / "datos" / "composicion_alimentos_2026-10.json"        # ALI-...: cifras de la tabla de USDA FoodData Central, consultadas una a una
RUTA_DEBATES = RAIZ / "datos" / "debates_polemicos.json"                         # DEB-...: hechos de los debates (corrientes), verificados en Europe PMC
RUTA_VITAMINAS = RAIZ / "datos" / "vitaminas_2026-10.json"                      # VIT-...: declaraciones autorizadas, VRN, composición (USDA) y límites del NHS (13-oct-2026)
RUTA_ETIQUETAS = RAIZ / "datos" / "etiquetas_2026-10.json"                      # ETI-...: cómo leer una etiqueta (Reglamento UE 1169/2011), frase a frase comprobada
RUTA_CAPTACION = RAIZ / "datos" / "captacion_2026-10.json"      # lo que se cuenta a nutricionistas: mecánica de Instagram y límites de la publicidad sanitaria (15-oct-2026)
RUTA_AZUCAR = RAIZ / "datos" / "azucar_2026-10.json"                            # AZU-...: guía de la OMS y estudios sobre el azúcar, verificados en Europe PMC
RUTA_SUPERMERCADO = RAIZ / "datos" / "supermercado_2026-10.json"                # SUP-...: productos de supermercado (AESAN 2022 + etiqueta en Open Food Facts, 20-oct-2026)


def hecho_de_voz(v: dict) -> dict:
    """Una cita literal de un participante de un estudio publicado, vista como un hecho del banco (para citarla por su id VOZ-...)."""
    est = v.get("estudio", {})
    return {"id": v["id"], "tema": f"voz de un participante: {v.get('tema', '')}",
            "enunciado": f"Cita literal de un participante ({v.get('quien', '')}) del estudio «{est.get('titulo', '')}» ({est.get('revista', '')}, {est.get('anio', '')}): «{v['cita_es']}»",
            "veredicto": "cita", "matiz": f"Original: «{v['cita_original']}». {v.get('hallazgo_principal', '')} Límites: {v.get('limites', '')}",
            "fuente": f"{est.get('revista', '')} {est.get('anio', '')}", "url": est.get("url") or est.get("doi", ""), "anio": est.get("anio"),
            "nivel_evidencia": "estudio cualitativo (citas de participantes; no prueba eficacia)", "riesgo_publicar": v.get("riesgo_publicar", "medio"),
            "poblaciones_en_riesgo": [], "estado_validacion": v.get("estado_validacion", "pendiente: dietista-nutricionista colegiado"),
            "es_voz": True, "cita_es": v["cita_es"], "cita_original": v["cita_original"], "licencia": v.get("licencia")}


def cuenta_de(pieza: dict) -> str:
    """La cuenta de una pieza (las piezas antiguas dicen «nutricion», que es n1)."""
    c = str(pieza.get("cuenta") or "n1")
    return "n1" if c == "nutricion" else c


def cargar_banco(cuenta: str) -> dict[str, dict]:
    """Banco de hechos de una cuenta: el heredado, las novedades de 2025-2026 (cada cifra verificada contra el resumen del artículo)
    y las voces, con la validación de José de esa cuenta superpuesta (validaciones.py). Sin cuenta no hay banco: no hay
    valor por defecto, porque un banco sin validaciones dejaría pasar un «no usar»."""
    hechos = json.loads(RUTA_BANCO.read_text(encoding="utf-8-sig"))
    if RUTA_NOVEDADES.is_file():
        hechos += json.loads(RUTA_NOVEDADES.read_text(encoding="utf-8-sig"))
    if RUTA_AMPLIACION.is_file():
        hechos += json.loads(RUTA_AMPLIACION.read_text(encoding="utf-8-sig"))
    for ruta in (RUTA_COMPOSICION, RUTA_DEBATES, RUTA_VITAMINAS, RUTA_AZUCAR, RUTA_ETIQUETAS, RUTA_CAPTACION, RUTA_SUPERMERCADO):   # composición de alimentos (USDA), debates con sus corrientes (11-oct-2026), vitaminas y azúcar (13-oct-2026), supermercado (20-oct-2026)
        if ruta.is_file():
            hechos += json.loads(ruta.read_text(encoding="utf-8-sig"))
    if RUTA_VOCES.is_file():
        hechos += [hecho_de_voz(v) for v in json.loads(RUTA_VOCES.read_text(encoding="utf-8-sig"))]
    return V.superponer({h["id"]: h for h in hechos}, cuenta)


def _norm_num(t: str) -> str | None:
    t = t.replace(" ", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):
        return t.replace(".", "")
    if re.fullmatch(r"\d+(?:,\d+)?", t):
        return t.replace(",", ".")
    return None


RX_NUM = re.compile(r"(?<![\w.,])\d{1,3}(?:\.\d{3})+(?![\d])|(?<![\w.,])\d+(?:,\d+)?(?!\d)")          # «40g» y «300kcal» también son cifras: solo se descarta un número que sigue a otro dígito


def numeros(texto: str) -> list[str]:
    sal = []
    for m in RX_NUM.finditer(texto):
        n = _norm_num(m.group(0))
        if n is not None:
            sal.append(n)
    return sal


def numeros_escritos(texto: str) -> list[str]:
    """Las fracciones y los porcentajes escritos con palabras («dos de cada tres», «cinco por ciento»), en cifras."""
    sal = []
    for m in RX_FRACCION_ESCRITA.finditer(texto):
        sal += [str(NUMEROS_EN_PALABRAS[g.lower()]) for g in m.groups() if g]
    return sal


def _texto_hecho(h: dict) -> str:
    partes = [h.get("enunciado", ""), h.get("matiz", "")]
    v = h.get("verificacion_primaria") or {}
    partes.append(v.get("detalle", ""))
    return " ".join(partes)


# campos de una hoja que son texto visible (no cuentan fuentes y avisos, que llevan años, volúmenes y páginas)
CLAVES_DE_TEXTO = ("cab", "texto", "nombre", "valor", "unidad", "detalle")        # lo que se lee dentro de un diccionario de una hoja (el icono, no)
CAMPOS_TEXTO = ("titulo", "titular", "dice", "antes", "cifra", "signo", "unidad", "texto", "frase", "dato", "sigue", "items",
                "izq", "der", "parrafos", "resalte", "franja", "filas", "voz", "cita", "quien",
                "respuesta", "si", "no", "ahora", "etiqueta_si", "etiqueta_no",
                "kicker", "dia", "opciones")        # dirección G; «creditos» no entra: lleva licencias («CC BY 3.0») que no son cifras de la pieza


def _lim(t) -> str:
    return D.limpiar(str(t))


RX_ANIO = re.compile(r"\b(?:19|20)\d{2}\b")
RX_VOLUMEN = re.compile(r"\b\d+(?:\(\d+\))?[;:]\s?[\w\-–.()]+")      # 2019;364:l42   2021;9(2):39   2018;3(2):175-192


def _sin_citas(pie: str) -> str:
    """El pie sin los años y los volúmenes de la línea «Fuente(s):». El resto de esa línea (que puede llevar más texto) sí se comprueba."""
    sal = []
    for linea in pie.split("\n"):
        if re.match(r"\s*Fuentes?:", linea):
            linea = RX_VOLUMEN.sub(" ", RX_ANIO.sub(" ", linea))
        elif re.match(r"\s*Fotos?:", linea):        # créditos de las fotos de Commons (autor, «CC BY 2.0»…): no son cifras de la pieza
            continue
        sal.append(linea)
    return "\n".join(sal)


def citas_de(pieza: dict, banco: dict) -> list[str]:
    """Las citas literales (en español y en el original) de las voces VOZ-... que la pieza cita en `hechos`."""
    sal = []
    for i in pieza.get("hechos", []):
        h = banco.get(i)
        if h and h.get("es_voz"):
            sal += [h["cita_es"], h["cita_original"]]
    return sorted(set(sal), key=len, reverse=True)


def textos_visibles(pieza: dict) -> list[tuple[str, str, str]]:
    """(hoja, campo, texto) de todo lo que se ve, menos fuentes y avisos. Sin las marcas de énfasis {palabra}.
    Las citas literales de las voces verificadas del banco se quitan: son palabras de otra persona, no de la cuenta."""
    citas = pieza.get("_citas", [])

    def limpio(t) -> str:
        t = _lim(t)
        for q in citas:
            t = t.replace(q, " ")
        return t

    sal = []
    for i, h in enumerate(pieza["hojas"], 1):
        for k in CAMPOS_TEXTO:
            v = h.get(k)
            if isinstance(v, str):
                sal.append((f"hoja {i}", k, limpio(v)))
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, str):
                        sal.append((f"hoja {i}", k, limpio(x)))
                    elif isinstance(x, (list, tuple)):
                        sal.extend((f"hoja {i}", k, limpio(y)) for y in x)
                    elif isinstance(x, dict):                 # filas de un ranking: nombre y valor (el icono no es texto)
                        sal.extend((f"hoja {i}", f"{k}.{kk}", limpio(str(y))) for kk, y in x.items() if kk in CLAVES_DE_TEXTO)
            elif isinstance(v, dict):
                sal.extend((f"hoja {i}", f"{k}.{kk}", limpio(str(y))) for kk, y in v.items() if kk in CLAVES_DE_TEXTO)
    pie = pieza.get("pie_instagram", "")
    for q in citas:
        pie = pie.replace(q, " ")
    sal.append(("pie", "pie_instagram", _sin_citas(pie)))
    return sal


# ---------------------------------------------------------------------------------------------- 1 estructura
def c_estructura(pieza: dict, salida: list) -> None:
    n = len(pieza["hojas"])
    if pieza.get("formato") == "video":
        if not 3 <= n <= 8:
            salida.append((AVISO, "estructura", f"{n} escenas: lo previsto es de 3 a 8"))     # la duración la mira c_video
    elif pieza.get("formato") == "unica":
        if n != 1:
            salida.append((FALLO, "estructura", f"imagen única con {n} hojas: tiene que ser 1"))
    elif pieza.get("direccion") == "g":                  # dirección G (15-oct-2026): carruseles más cortos, de 3 a 10 hojas
        if not 3 <= n <= 10:
            salida.append((FALLO, "estructura", f"{n} hojas: tiene que haber de 3 a 10"))
        elif n == 3:
            salida.append((AVISO, "estructura", "3 hojas: es lo mínimo; lo habitual son de 4 a 6"))
    elif not 5 <= n <= 10:
        salida.append((FALLO, "estructura", f"{n} hojas: tiene que haber de 5 a 10"))
    pie = pieza.get("pie_instagram", "")
    cuerpo = re.sub(r"(?:\s*#\w+)+\s*$", "", pie).strip()
    if not 150 <= len(cuerpo) <= 1200:
        salida.append((AVISO, "estructura", f"el pie mide {len(cuerpo)} caracteres (rango previsto: 150 a 1200)"))
    emojis = re.findall("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]", pie)
    if len(emojis) > 6:                                     # 15-oct: José pide pies dinámicos con emojis (antes, como mucho uno)
        salida.append((FALLO, "estructura", f"{len(emojis)} emojis en el pie: como mucho seis"))
    etiquetas = re.findall(r"#\w+", pie)
    if len(etiquetas) > 5:                                  # Instagram admite 5 por pie desde dic-2025 (router/instagram.py: MAX_HASHTAGS)
        salida.append((FALLO, "estructura", f"{len(etiquetas)} etiquetas: como mucho 5"))
    elif len(etiquetas) < 3:
        salida.append((AVISO, "estructura", f"{len(etiquetas)} etiquetas: lo previsto es de 3 a 5"))
    if not pieza.get("hechos"):
        salida.append((FALLO, "estructura", "la pieza no cita ningún hecho del banco"))


# ---------------------------------------------------------------------------------------------- 2 cifras
def c_cifras(pieza: dict, banco: dict, salida: list) -> None:
    faltan = [i for i in pieza.get("hechos", []) if i not in banco]
    if faltan:
        salida.append((FALLO, "cifras", f"hechos que no están en el banco: {faltan}"))
    permitidos = set()
    for i in pieza.get("hechos", []):
        if i in banco:
            permitidos.update(numeros(_texto_hecho(banco[i])))
    for donde, campo, t in textos_visibles(pieza):
        for n in numeros(t):
            if n not in permitidos:
                salida.append((FALLO, "cifras", f"{donde}, {campo}: el número {n} no sale de ningún hecho citado ({', '.join(pieza['hechos'])})"))
    for i in pieza.get("hechos", []):
        h = banco.get(i)
        if not h:
            continue
        if h.get("enunciado_pendiente_de_corregir"):
            salida.append((AVISO, "cifras", f"{i}: el enunciado del banco está marcado como erróneo; se usa solo con la verificación primaria"))
        if h.get("veredicto") not in ("respaldado", "matizado", "falso", "no_respaldado", "ejemplo", "cita"):
            salida.append((AVISO, "cifras", f"{i}: veredicto raro ({h.get('veredicto')})"))
        if h.get("no_usar"):
            salida.append((FALLO, "cifras", f"{i}: marcado como «no usar» en la validación de José; no puede citarse"))
        if h.get("riesgo_publicar") == "alto" and h.get("validacion") != "validado":
            salida.append((AVISO, "cifras", f"{i}: riesgo de publicar ALTO; hace falta validarlo (/validar en el bot) antes de publicar"))
    pend = [i for i in pieza.get("hechos", []) if i in banco and "pendiente" in str(banco[i].get("estado_validacion", ""))]
    if pend:
        salida.append((AVISO, "validación", f"sin validar por dietista-nutricionista colegiado: {', '.join(pend)}"))


# ---------------------------------------------------------------------------------------------- 3 líneas rojas
def hallazgos_de_texto(t: str) -> list[tuple[str, str, str]]:
    """(nombre, gravedad, fragmento) de las líneas rojas que aparecen en un texto: las de LINEAS_ROJAS sobre todo el texto y las de
    PROMESAS_DE_FRASE frase a frase, salvo las frases con una negación o que remiten al médico."""
    sal = []
    for nombre, grav, rx in LINEAS_ROJAS:
        m = re.search(rx, t, re.I)
        if m:
            sal.append((nombre, grav, t[max(0, m.start() - 20):m.end() + 20]))
    for trozo in re.split(r"[.!?;:,\n]", t):
        if not trozo.strip() or PROTECCION_DE_FRASE.search(trozo):
            continue
        for nombre, grav, rx in PROMESAS_DE_FRASE:
            if re.search(rx, trozo, re.I):
                sal.append((nombre, grav, trozo.strip()[:80]))
    return sal


def c_lineas_rojas(pieza: dict, salida: list) -> None:
    vistas: dict[str, set] = {}
    alt = pieza.get("alt") or []                    # el texto alternativo también sale a Instagram: mismas líneas rojas que el resto
    alts = [alt] if isinstance(alt, str) else [a for a in alt if isinstance(a, str)]
    for donde, campo, t in textos_visibles(pieza) + [("alt", "texto alternativo", a) for a in alts]:
        for nombre, grav, frag in hallazgos_de_texto(t):
            salida.append((grav, "línea roja", f"{donde}, {campo}: {nombre}: «{frag}»"))
        for m in re.finditer(ENFERMEDADES, t, re.I):
            vistas.setdefault(m.group(0).lower(), set()).add(donde)
    for donde, campo, t in textos_visibles(pieza):
        if re.search(ALERTA_PESO, t, re.I):
            vistas.setdefault("adelgazamiento", set()).add(donde)
    # lo que también se dibuja fuera del cuerpo (etiqueta, sello, aviso, fuente, fuentes y «sigue»): las mismas líneas rojas, no la salud
    for i, h in enumerate(pieza.get("hojas") or [], 1):
        if not isinstance(h, dict):
            continue
        for k in ("etiqueta", "sello", "aviso", "fuente", "sigue", "fuentes"):
            v = h.get(k)
            for t in ([v] if isinstance(v, str) else [x for x in v if isinstance(x, str)] if isinstance(v, list) else []):
                for nombre, grav, frag in hallazgos_de_texto(_lim(t)):
                    salida.append((grav, "línea roja", f"hoja {i}, {k}: {nombre}: «{frag}»"))
    for enf, donde in sorted(vistas.items()):
        salida.append((AVISO, "salud", f"menciona «{enf}» en {', '.join(sorted(donde))}: solo vale como límite o riesgo, nunca como promesa"
                       + (" (RD 1907/1996, art. 4.2: nada de atribuir propiedades adelgazantes)" if enf == "adelgazamiento" else "")))
    # filtro legal de la investigación del 20-oct (filtro_legal.py): promesas, curaciones, adelgazar, «soy nutricionista», marca + juicio.
    # BLOQUEAR es un fallo; REVISAR, un aviso (casi siempre un desmentido o la palabra «natural»). Probado contra las 143 piezas G: 0 bloqueos.
    try:
        import filtro_legal as _FL
    except ImportError:
        return
    texto = "\n".join([str(pieza.get("pie_instagram") or "")] + [t for _, _, t in textos_visibles(pieza)])
    veredicto, motivos = _FL.revisar(texto)
    if veredicto == "BLOQUEAR":
        salida.append((FALLO, "filtro legal", "; ".join(str(m)[:70] for m in motivos[:3])))
    elif veredicto == "REVISAR":
        salida.append((AVISO, "filtro legal", "a revisar: " + "; ".join(str(m)[:50] for m in motivos[:3])))


# ---------------------------------------------------------------------------------------------- 4 texto de IA
_FLAGS = {"i": re.I, "m": re.M, "s": re.S}


def cargar_reglas() -> list[dict]:
    reglas = json.loads(RUTA_REGLAS.read_text(encoding="utf-8-sig"))
    for r in reglas:
        f = 0
        for letra in r.get("flags", ""):
            f |= _FLAGS[letra]
        r["_rx"] = re.compile(r["patron"] if r["tipo"] == "recuento" else r["regex"], f)
    return reglas


def evaluar(texto: str, destino: str, reglas: list[dict]) -> list[dict]:
    hits = []
    for r in reglas:
        if destino not in r.get("aplica", []) and not r["id"].startswith("TX-RES-"):
            continue
        ms = list(r["_rx"].finditer(texto))
        if r["tipo"] == "recuento":
            if len(ms) <= r["maximo"]:
                continue
            m = ms[r["maximo"]]
        else:
            if not ms:
                continue
            m = ms[0]
        hits.append({"id": r["id"], "gravedad": r["gravedad"], "peso": r.get("peso", 20 if r["gravedad"] == "fallo" else 4),
                     "desc": r["descripcion"], "frag": texto[max(0, m.start() - 25):m.end() + 25].replace("\n", " ")})
    return hits


def c_texto_ia(pieza: dict, salida: list) -> None:
    reglas = cargar_reglas()
    hojas = "\n\n".join(t for d, c, t in textos_visibles(pieza) if d != "pie")
    pie = pieza.get("pie_instagram", "")
    for destino, texto in (("diapositivas", hojas), ("pie", pie)):
        hits = evaluar(texto, destino, reglas)
        indice = min(100, sum({h["id"]: h["peso"] for h in hits}.values()))
        for h in hits:
            salida.append((FALLO if h["gravedad"] == "fallo" else AVISO, f"texto IA ({destino})",
                           f"{h['id']}: {h['desc'][:110]} Sale en: «{h['frag']}»"))
        salida.append(("INFO", f"texto IA ({destino})", f"índice de sospecha {indice} (0 pasa; 15 revisar; 35 reescribir)"))


# ---------------------------------------------------------------------------------------------- 5 descargo y fuentes
def c_ia_declarada(pieza: dict, salida: list) -> None:
    """Un vídeo con voz sintética tiene que declararlo (`"ia": true`): el router lo manda a Instagram como contenido de IA."""
    if pieza.get("formato") == "video" and any(e.get("voz") for e in pieza.get("escenas", [])) and not pieza.get("ia"):
        salida.append((FALLO, "ia", "el vídeo lleva voz sintética y la pieza no declara \"ia\": true"))


def c_descargo(pieza: dict, salida: list) -> None:
    ultima = pieza["hojas"][-1]
    if pieza.get("formato") in ("unica", "video"):
        pie = pieza.get("pie_instagram", "").lower()
        if "no es consejo personal" not in pie or "sustituye" not in pie:
            salida.append((FALLO, "descargo", "el pie de la imagen única no lleva «no es consejo personal ni sustituye…»"))
        # las fuentes ya no hacen falta en el pie (13/15-oct: José las quita) si la propia pieza las lleva en su última hoja o escena
        en_pieza = any(("fuente" in x or "fuentes" in x) for x in (ultima, (pieza.get("escenas") or [{}])[-1]) if isinstance(x, dict))
        if "fuente" not in pie and not en_pieza and pieza.get("direccion") != "g":
            salida.append((FALLO, "descargo", "ni el pie ni la última hoja o escena de la pieza listan las fuentes"))
        return
    aviso = (ultima.get("aviso") or "").lower()
    if "consejo personal" not in aviso or "sustituye" not in aviso or not re.search(r"\bno es consejo\b", aviso) or not re.search(r"\bni sustituye\b|\bno sustituye\b", aviso):
        salida.append((FALLO, "descargo", "la última hoja no lleva el aviso «no es consejo personal ni sustituye…»"))
    if not ultima.get("fuentes") and pieza.get("direccion") != "g":      # las piezas G no llevan fuentes a la vista (decisión de José, 15-oct)
        salida.append((FALLO, "descargo", "la última hoja no lista las fuentes"))
    for i, h in enumerate(pieza["hojas"], 1):
        if h["plantilla"] in ("portada", "cierre"):
            continue
        tiene_cifra = any(numeros(t) for d, _, t in textos_visibles({"hojas": [h], "pie_instagram": ""}) if d != "pie")
        if tiene_cifra and not h.get("fuente") and pieza.get("estilo") != "viral":     # estilo viral (11-oct-2026): la fuente va en el pie y en la última hoja, no en cada hoja
            salida.append((FALLO, "fuentes", f"hoja {i} lleva cifras y no lleva fuente"))


# ---------------------------------------------------------------------------------------------- voces
RX_CITA = re.compile(r"«([^»]{20,})»")
LIMITE_DEL_ESTUDIO = ("no demuestra", "no prueba", "no es una prueba", "no dice nada sobre", "no sirve para saber", "no mide")


def c_voces(pieza: dict, banco: dict, salida: list) -> None:
    """Toda cita entre «» de una hoja tiene que ser, tal cual, una de las voces verificadas que la pieza cita; y la pieza dice qué no demuestra."""
    validas = citas_de(pieza, banco)
    usa_voz = any(banco.get(i, {}).get("es_voz") for i in pieza.get("hechos", []))
    for i in pieza.get("hechos", []):
        v = banco.get(i, {})
        if not v.get("es_voz"):
            continue
        if v.get("riesgo_publicar") != "bajo":
            salida.append((FALLO, "voces", f"{i}: las voces reales solo pueden hablar de la vida diaria (compra, comida, vida social, coste, ánimo): "
                                           "un testimonio de peso o de salud como inducción al consumo está prohibido (RD 1907/1996, art. 4.7; Ley 17/2011, art. 44.3.a)"))
        if re.search(RESULTADOS_DE_SALUD, v.get("cita_es", ""), re.I):
            salida.append((FALLO, "voces", f"{i}: la cita habla de peso, de salud o de enfermedad; no se usa"))
    textos = []
    for i, h in enumerate(pieza["hojas"], 1):
        for k in CAMPOS_TEXTO:
            v = h.get(k)
            if isinstance(v, str):
                textos.append((f"hoja {i}", k, D.limpiar(v)))
            elif isinstance(v, dict):
                textos += [(f"hoja {i}", f"{k}.{kk}", D.limpiar(str(y))) for kk, y in v.items() if kk in ("cab", "texto")]
    for i, h in enumerate(pieza["hojas"], 1):
        if h.get("plantilla") != "voz":
            continue
        voces = [banco[x] for x in pieza.get("hechos", []) if banco.get(x, {}).get("es_voz")]
        cita = " ".join(h.get("cita", "").split())
        if not any(cita and cita in (" ".join(v["cita_es"].split()), " ".join(v["cita_original"].split())) for v in voces):
            salida.append((FALLO, "voces", f"hoja {i}: la cita no es, tal cual, una de las voces verificadas que la pieza cita (VOZ-...)"))
        quien = [(banco[x].get("enunciado", "")) for x in pieza.get("hechos", []) if banco.get(x, {}).get("es_voz")]
        if h.get("quien") != "Participante de un estudio" and not any(h.get("quien", "") and h["quien"] in q for q in quien):
            salida.append((FALLO, "voces", f"hoja {i}: «quién habla» no es como lo describe el estudio ni es «Participante de un estudio»: no se inventan datos del participante"))
        if re.search(RESULTADOS_DE_SALUD, h.get("quien", ""), re.I):
            salida.append((FALLO, "voces", f"hoja {i}: «quién habla» menciona salud o enfermedad; la voz va como «Participante de un estudio»"))
        if not any(v.get("fuente", "").split(" ")[0] and v["fuente"] in h.get("estudio", "") for v in voces):
            salida.append((FALLO, "voces", f"hoja {i}: el campo «estudio» tiene que nombrar la revista y el año del estudio de la cita (por ejemplo «Frontiers in Nutrition 2024»)"))
    for donde, campo, t in textos:
        for m in RX_CITA.finditer(t):
            texto = " ".join(m.group(1).split())
            if not any(" ".join(q.split()) == texto or texto in " ".join(q.split()) for q in validas):
                salida.append((FALLO, "voces", f"{donde}, {campo}: la cita «{texto[:60]}…» no es una cita verificada del banco (VOZ-...); no se inventan voces"))
    if usa_voz:
        pie = pieza.get("pie_instagram", "").lower()
        if not any(f in pie for f in LIMITE_DEL_ESTUDIO):
            salida.append((AVISO, "voces", "la pieza usa voces de un estudio y el pie no dice lo que ese estudio no demuestra (por ejemplo «no demuestra que la dieta funcione»)"))
        if "participante" not in pie and "estudio" not in pie:
            salida.append((FALLO, "voces", "el pie no dice que la cita es de un participante de un estudio publicado"))


# ---------------------------------------------------------------------------------------------- 7 densidad
TOPE_PALABRAS = {"voz": 60, "video": 24, "portada": 14, "dice": 18, "cifra": 26, "versus": 24, "lista": 36, "frase": 20, "cierre": 18, "unica": 26,
                 "respuesta": 22, "ranking": 40, "sino": 40, "antes_ahora": 36,
                 "f_portada": 14, "f_letra": 16, "f_frase": 30, "f_ranking": 52, "f_vs": 32, "f_dato": 28, "f_foto_texto": 40, "f_cierre": 18,
                 # dirección G (15-oct-2026): menos texto por hoja que en F; el cierre cuenta solo la frase (fuentes y aviso no entran)
                 "g_cambio": 26, "g_pictograma": 44, "g_eleccion": 30, "g_afirmacion": 30, "g_dia": 16, "g_cierre": 16}


def c_densidad(pieza: dict, salida: list) -> None:
    for i, h in enumerate(pieza["hojas"], 1):
        palabras = 0
        for _, campo, t in textos_visibles({"hojas": [h], "pie_instagram": ""}):
            if campo in ("sigue", "voz") or _ == "pie":
                continue
            palabras += len(re.findall(r"[\wÁÉÍÓÚÜÑáéíóúüñ%]+", t))
        tope = TOPE_PALABRAS.get(h["plantilla"], 24)
        if palabras > tope * 1.25:
            salida.append((FALLO, "densidad", f"hoja {i} ({h['plantilla']}): {palabras} palabras, el tope es {tope}"))
        elif palabras > tope:
            salida.append((AVISO, "densidad", f"hoja {i} ({h['plantilla']}): {palabras} palabras, el tope es {tope}"))


# ---------------------------------------------------------------------------------------------- 6 paleta
def _rgb(h: str) -> list[int]:
    h = h.lstrip("#")
    return [int(h[k:k + 2], 16) for k in (0, 2, 4)]


def c_paleta(carpeta: Path, salida: list, tema: dict, umbral_aviso: float = 3.0, umbral_fallo: float = 8.0,
             tolerancia: float = 10.0) -> None:
    """Porcentaje de píxeles a más de `tolerancia` (unidades RGB) del triángulo de los tres colores del tema.

    La distancia es la real: la del plano si el punto cae dentro del triángulo, y la de la arista más cercana si no.
    Antes se proyectaba por mínimos cuadrados recortando los coeficientes, lo que daba avisos del 3-4 % en hojas con el
    oliva claro (0,00 % con la distancia real). Tolerancia 10, medida el 9-oct en 28 hojas: las hojas nuevas no pasan del
    1,6 % (ruido del JPEG en los bordes); las hojas con el oliva antiguo (#5E6A2E, a 12 unidades del triángulo) dan
    del 3,5 % al 65 %, así que el control las marca. Con 24 el oliva antiguo no se veía (0 %).
    """
    import numpy as np
    from PIL import Image

    negro = np.array(_rgb(tema["colores"]["oscuro"]), float)
    oliva = np.array(_rgb(tema["colores"]["color"]), float)
    blanco = np.array(_rgb(tema["colores"]["claro"]), float)
    A = np.stack([oliva - negro, blanco - negro], axis=1)       # px = negro + a*(oliva-negro) + b*(blanco-negro), a,b >= 0, a+b <= 1
    pinv = np.linalg.pinv(A)
    aristas = [(negro, oliva), (negro, blanco), (oliva, blanco)]
    for ruta in sorted(carpeta.glob("[0-9][0-9].jpg")):
        px = np.asarray(Image.open(ruta).convert("RGB"), float).reshape(-1, 3)[::7]
        ab = (px - negro) @ pinv.T
        dentro = (ab[:, 0] >= 0) & (ab[:, 1] >= 0) & (ab.sum(axis=1) <= 1)
        d = np.linalg.norm(px - (negro + ab @ A.T), axis=1)
        d_arista = np.full(len(px), np.inf)
        for p0, p1 in aristas:
            v = p1 - p0
            t = np.clip(((px - p0) @ v) / (v @ v), 0, 1)
            d_arista = np.minimum(d_arista, np.linalg.norm(px - (p0 + t[:, None] * v), axis=1))
        d = np.where(dentro, d, d_arista)
        fuera = (d > tolerancia).mean() * 100
        nivel = FALLO if fuera > umbral_fallo else (AVISO if fuera > umbral_aviso else "INFO")
        salida.append((nivel, "paleta", f"{ruta.name}: {fuera:.1f} % de píxeles fuera del triángulo de los tres colores del tema"))


# ---------------------------------------------------------------------------------------------- principal
def _hojas_de_video(pieza: dict) -> list[dict]:
    """Cada escena de un vídeo, vista como una hoja: el texto que se lee en ella, para pasarle los mismos controles."""
    hojas = []
    for esc in pieza["escenas"]:
        h: dict = {"plantilla": "video"}
        for e in esc["elementos"]:
            t = e["tipo"]
            if t in ("titulo", "texto"):
                h["texto"] = (h.get("texto", "") + " " + e["texto"]).strip()
            elif t == "globo":
                h["dice"] = (h.get("dice", "") + " " + e["texto"]).strip()
            elif t == "cifra":
                h["cifra"] = f"{e.get('signo', '')}{e['valor']:.{e.get('dec', 0)}f}".replace(".", ",").lstrip("+")
        if esc.get("voz"):
            h["voz"] = esc["voz"]
        hojas.append(h)
    return hojas


# ---------------------------------------------------------------------------------------------- vídeo (esquema 2)
def _caja_con_zoom(caja: list, zoom: float) -> tuple:
    """Una caja del fotograma (sin zoom) con el zoom máximo de la cámara y el margen de la sacudida: así es donde puede estar."""
    import video_tiempo as VT
    cx, cy = VT.CENTRO_CAMARA
    xs = sorted([cx + zoom * (caja[0] - cx), cx + zoom * (caja[2] - cx)])
    ys = sorted([cy + zoom * (caja[1] - cy), cy + zoom * (caja[3] - cy)])
    m = VT.SACUDIDA_MAX_PX
    return xs[0] - m, ys[0] - m, xs[1] + m, ys[1] + m


def _se_solapan(a: tuple, b: list) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _voz_de_la_pieza(pieza: dict, carpeta: Path | None) -> tuple[str, str | None]:
    """Motor y voz con que se hizo (o se haría) la locución: los de registro.json si el render ya se hizo; si no, los de voz_config de
    la pieza, y si la pieza no los dice, el motor por defecto de voz.py (Supertonic)."""
    import voz as VZ
    if carpeta is not None:
        try:
            voz_reg = json.loads((Path(carpeta) / "registro.json").read_text(encoding="utf-8")).get("voz") or {}
        except (OSError, ValueError, AttributeError):
            voz_reg = {}
        if voz_reg.get("motor"):
            return voz_reg["motor"], voz_reg.get("voz")
    cfg = pieza.get("voz_config") or {}
    return cfg.get("motor") or VZ.MOTOR_VOZ_DEFECTO, cfg.get("voz")


def c_voz_decisiones(pieza: dict, salida: list, motor: str | None = None, voz: str | None = None) -> None:
    """Una frase de la voz sin toma limpia (WER de más de 35 % o «sin toma limpia», según la caché de decisiones) da un aviso que
    la nombra: hay que escucharla antes de publicar. Es un aviso y no un fallo: la decisión de bloquear sin escuchar es de José.
    `motor` y `voz` son los de la locución (ver _voz_de_la_pieza); los motores sin decisiones de tomas (edge, kokoro) no se miran."""
    import voz as VZ
    import voz_criterio as VC
    if motor is None:
        motor, voz = _voz_de_la_pieza(pieza, None)
    if motor not in ("chatterbox", "supertonic"):
        return
    variante = voz or VZ.VOZ_POR_DEFECTO.get(motor)
    try:
        for i, esc in enumerate(pieza.get("escenas", [])):
            if not esc.get("voz"):
                continue
            for f in VZ._frases(esc["voz"], hablar=True):
                d = VZ.decision_de_frase(f["texto"], variante, motor=motor)
                if d is None:
                    salida.append((AVISO, "voz", f"escena {i + 1}: «{f['texto']}» no tiene decisión de tomas en la caché: no se ha comprobado"))
                elif d.get("sin_toma_limpia") or float(d.get("wer_pct", 0)) > VC.UMBRALES["wer_max_pct"]:
                    salida.append((AVISO, "voz", f"escena {i + 1}: «{f['texto']}» (WER {d.get('wer_pct')} %"
                                                 + (", sin toma limpia" if d.get("sin_toma_limpia") else "") + "): escúchala antes de publicar"))
    except VZ.ErrorVoz as exc:
        salida.append((AVISO, "voz", f"no se pudo comprobar la voz: {exc}"))


RAMAS_LICENCIA_VOZ = ("L1 ", "L3 ", "L9 ")   # pauta personal, resultado o antes y después de una persona, testimonio: líneas rojas que ya existen


def c_locucion_licencia(pieza: dict, salida: list, motor: str) -> None:
    """Solo con la voz de Supertonic 3 (licencia OpenRAIL-M, cláusula (l) del Anexo A: no se da consejo médico ni se interpreta un resultado
    médico). Un AVISO y no un fallo: si la locución dice una pauta personal (L1) o un resultado o caso de una persona (L3, L9), José lo
    revisa antes de publicar. Usa las líneas rojas de LINEAS_ROJAS, sin reglas nuevas; el texto hablado se mira aparte, con sus frases."""
    if motor != "supertonic":
        return
    for i, esc in enumerate(pieza.get("escenas", []), 1):
        for nombre, _grav, frag in hallazgos_de_texto(esc.get("voz") or ""):
            if nombre.startswith(RAMAS_LICENCIA_VOZ):
                salida.append((AVISO, "licencia", f"escena {i}: la locución de Supertone (OpenRAIL-M, cláusula (l) del Anexo A) dice «{frag}» "
                                                  f"({nombre}): divulgación sin pautas personales ni resultados de personas; revísalo antes de publicar"))


def c_video(pieza: dict, carpeta: Path | None, salida: list) -> None:
    """Controles propios de un vídeo. Sin renderizar se miran la zona segura (con el zoom máximo de la cámara), el gancho de la
    primera escena y que la voz empiece antes de 0,3 s. Con el registro del render (registro.json, en la carpeta de la pieza) se
    miran la duración de 15 a 25 s, los destellos (3 por segundo como máximo), los cambios visuales (cada 3 s como mucho), la
    sonoridad y que los subtítulos digan el guion. Además: que el registro sea de esta versión de la pieza (huella), las medidas
    del MP4 (parpadeo rojo, saltos de brillo, paleta), que ningún texto tape un subtítulo y que cada cifra empiece a contar cuando
    aparece. Las frases de voz sin toma limpia salen como avisos (c_voz_decisiones)."""
    import json as _json
    import video_medidas as ME
    import video_tiempo as VT
    escenas = pieza.get("escenas", [])
    # la zona de Instagram (250 arriba, 420 abajo, 130 a la derecha) con el margen de una sacudida de cámara
    for nombre, x0, y0, x1, y1 in ME.fuera_de_zona(ME.cajas_de_pieza(pieza), ME.zoom_maximo(pieza), VT.SACUDIDA_MAX_PX, VT.ZONA_IG):
        salida.append((FALLO, "zona segura", f"{nombre} sale de la zona segura (x {x0}-{x1}, y {y0}-{y1}) con el zoom de la cámara"))
    if carpeta is not None and (carpeta / "video.mp4").is_file():
        pistas = ME.listas_de_edicion(carpeta / "video.mp4")         # Instagram pide Reels sin listas de edición («no edit lists»)
        if pistas:
            salida.append((FALLO, "contenedor", f"el MP4 trae listas de edición ({', '.join(pistas)}): Instagram pide Reels sin ellas; "
                                                "hay que volver a renderizar"))
    motor, voz = _voz_de_la_pieza(pieza, carpeta)
    c_voz_decisiones(pieza, salida, motor, voz)
    c_locucion_licencia(pieza, salida, motor)
    for i, esc in enumerate(escenas):                    # una cifra no puede verse antes de empezar a contar: se leería un 0
        for el in esc.get("elementos", []):
            cu = el.get("cuenta") or {}
            if el.get("tipo") == "cifra" and "desde" in cu and str(el.get("t0", 0)) != str(cu["desde"]):
                salida.append((FALLO, "cifra", f"escena {i + 1}, cifra {el.get('valor')}: aparece en t0 {el.get('t0', 0)} pero empieza "
                                               f"a contar en {cu['desde']}: hasta entonces se vería un 0 (t0 y cuenta.desde, iguales)"))
    if escenas:
        primera = escenas[0]
        vistos = [e for e in primera.get("elementos", []) if e.get("t0", 0) in (0, 0.0, "0") and e.get("entra") == "ya"
                  and e.get("tipo") in ("titulo", "texto", "etiqueta", "sello", "globo")]
        if not vistos:
            salida.append((FALLO, "gancho", "la primera escena no trae texto en pantalla desde el fotograma 0 (t0 0 y entra «ya»)"))
        if float(primera.get("voz_t", VT.VOZ_T_PRIMERA)) >= 0.3:
            salida.append((FALLO, "gancho", f"la voz de la primera escena empieza a {primera.get('voz_t')} s: tiene que empezar antes de 0,3 s"))
    ruta = carpeta / "registro.json" if carpeta is not None else None
    if ruta is None or not ruta.is_file():
        if carpeta is not None and (carpeta / "video.mp4").is_file():
            salida.append((FALLO, "registro", "hay un video.mp4 sin registro.json: puede ser de un render incompleto o de otra versión; "
                                              "hay que volver a renderizar"))
        else:
            salida.append(("INFO", "vídeo", "sin registro.json: duración, destellos, cambios y subtítulos se miran al renderizar"))
        return
    reg = _json.loads(ruta.read_text(encoding="utf-8"))
    if reg.get("huella") != ME.huella_pieza(pieza):
        salida.append((FALLO, "registro", "el registro es de otra versión de la pieza (cambió una escena, la voz, el sonido o el fps): "
                                          "hay que volver a renderizar"))
    dur = float(reg.get("duracion_s", 0))
    if not 15 <= dur <= 25:
        salida.append((FALLO, "duración", f"el vídeo dura {dur} s: el formato pide de 15 a 25 s"))
    for malo in ME.respaldos_del_render(reg):              # un respaldo silencioso es un fallo: no se publica una pieza así
        salida.append((FALLO, "respaldo", malo))
    for malo in ME.fuera_de_zona_registro(reg.get("zona", {})):
        salida.append((FALLO, "zona segura", malo))
    if carpeta is not None and (carpeta / "eventos.json").is_file():
        hueco = ME.hueco_max_pulsos(_json.loads((carpeta / "eventos.json").read_text(encoding="utf-8")), dur)
        if hueco > ME.PULSOS_MAX_HUECO_S:
            salida.append((AVISO, "ritmo", f"{hueco} s sin ningún pulso (entrada, golpe, palabra clave...): "
                                           f"el objetivo es de {ME.PULSOS_MAX_HUECO_S} s como mucho"))
    med = reg.get("medidas", {})
    if med.get("destellos_max_por_s", 0) > ME.MAX_DESTELLOS_S:
        salida.append((FALLO, "destellos", f"{med.get('destellos_max_por_s')} destellos en un segundo: como mucho {ME.MAX_DESTELLOS_S}"))
    if med.get("cambios_max_hueco_s", 0) > 3.0:
        salida.append((FALLO, "cambios", f"{med.get('cambios_max_hueco_s')} s sin cambio visual: como mucho 3 s"))
    son = med.get("sonoridad_salida") or {}
    if any(esc.get("voz") for esc in escenas) and "lufs" not in son:      # sin medida no se sabe si la voz suena (un MP4 mudo pasaba)
        salida.append((FALLO, "sonoridad", f"no se pudo medir la sonoridad del MP4 ({son.get('error', 'sin dato')}): sin ella no se sabe "
                                           "si la voz suena"))
    if "lufs" in son and not -17.0 <= son["lufs"] <= -13.0:
        salida.append((AVISO, "sonoridad", f"la salida mide {son['lufs']} LUFS (se espera de -17 a -13)"))
    if "pico_dbtp" in son and son["pico_dbtp"] > -1.0:
        salida.append((AVISO, "sonoridad", f"pico verdadero de {son['pico_dbtp']} dBTP (se espera por debajo de -1)"))
    mp4 = med.get("mp4")                                  # las medidas del fichero que sale, las mismas que video_medidas.py
    if mp4 is None:
        salida.append((FALLO, "medidas", "el registro no trae las medidas del MP4 (es de una versión anterior): hay que volver a renderizar"))
    else:
        if mp4.get("parpadeo_rojo_max_por_s", 0) > 0:
            salida.append((FALLO, "parpadeo rojo", f"{mp4['parpadeo_rojo_max_por_s']} parpadeo(s) rojo(s) por segundo: no se admite ninguno"))
        if mp4.get("saltos_luminancia_max_por_s", 0) > ME.MAX_SALTOS_S:
            salida.append((FALLO, "brillo", f"{mp4['saltos_luminancia_max_por_s']} cambios de brillo de pantalla completa en un segundo: "
                                            f"como mucho {ME.MAX_SALTOS_S}"))
        if mp4.get("destellos_max_por_s", 0) > ME.MAX_DESTELLOS_S:
            salida.append((FALLO, "destellos", f"el MP4 da {mp4['destellos_max_por_s']} destellos en un segundo: como mucho {ME.MAX_DESTELLOS_S}"))
        fuera = mp4.get("fuera_de_paleta_max_pct")
        if fuera is not None and fuera > ME.FALLO_PALETA_PCT:
            salida.append((FALLO, "paleta", f"un fotograma tiene el {fuera} % de píxeles fuera de la paleta (como mucho {ME.FALLO_PALETA_PCT} %)"))
        elif fuera is not None and fuera > ME.AVISO_PALETA_PCT:
            salida.append((AVISO, "paleta", f"un fotograma tiene el {fuera} % de píxeles fuera de la paleta (aviso desde {ME.AVISO_PALETA_PCT} %)"))
    for txt in reg.get("cajas_texto", []):                # ningún texto ni cifra tapa un subtítulo en el mismo instante
        a = _caja_con_zoom(txt["caja"], ME.zoom_maximo(pieza))
        for k, (caja_sub, sub) in enumerate(zip(reg.get("zona", {}).get("subtitulos", []), reg.get("subtitulos", [])), 1):
            if txt["t0"] < sub["t1"] and sub["t0"] < txt["t1"] and _se_solapan(a, caja_sub):
                salida.append((FALLO, "solapes", f"escena {txt['escena'] + 1}, {txt['tipo']} ({txt['t0']}-{txt['t1']} s) tapa el "
                                                 f"subtítulo {k} ({sub['t0']}-{sub['t1']} s, «{' '.join(sub['palabras'])}»)"))
    ini = reg.get("escenas", [{}])[0]
    if ini and float(ini.get("voz_t0", 0)) - float(ini.get("t0", 0)) >= 0.3:
        salida.append((FALLO, "gancho", "la voz de la primera escena no empieza antes de 0,3 s en el render"))
    for i, esc in enumerate(escenas):
        if not esc.get("voz"):
            continue
        guion = [t["clave"] for t in VT.tokens_de(esc["voz"])]
        dichas = [VT.normaliza(w) for b in reg.get("subtitulos", []) if b.get("escena") == i for w in b.get("palabras", [])]
        if guion != [d for d in dichas if d]:
            salida.append((FALLO, "subtítulos", f"escena {i + 1}: los subtítulos no dicen el guion de la voz"))


ILUSTRACION_AVISO_PCT = 22.0      # el encargo pide que la ilustración (personaje, cifra y estallido) ocupe la mayor parte del fotograma. Con la
                                  # zona segura de Instagram (formato vertical, texto ≤ 40 %) lo medido en los reels de demostración es del 17 al 36 %
                                  # (11-oct-2026): por debajo de 22 % se avisa
ILUSTRACION_MIN_PCT = 15.0        # por debajo de 15 % es FALLO: el texto llevaría el peso de la escena (medida de cajas, no de píxeles)
TEXTO_MAX_PCT = 40.0              # el texto no ocupa más del 40 % del fotograma en ninguna escena (FALLO por encima)


def c_mudo(pieza: dict, carpeta: Path | None, salida: list) -> None:
    """Controles del reel mudo (esquema 3, reel_mudo.py). Sin renderizar: la validación de la pieza en sus tres estilos, la zona
    segura con el zoom máximo, el gancho (con personaje en el fotograma 0 si lo hay), el cambio visual cada 3 s, la figura de cada
    escena, y que el texto y el estallido de una cifra no se solapen. Con el render (registro.json y video.mp4): que el MP4 tenga pista
    de audio y ninguna lista de edición; que estén todas las medidas (área, sonoridad, golpe, cortes, alineación, MP4); el texto por
    escena (≤ 40 %); los destellos, los saltos de brillo y la paleta del MP4; la sonoridad (-14 LUFS, pico ≤ -1,5 dBTP), sin recortes;
    la alineación de los golpes (música y MP4) y de los cortes (plan y MP4) con un fotograma de margen, con el retardo medido."""
    import json as _json
    import musica_propia as MP
    import reel_mudo as RM
    import video_medidas as ME
    import video_tiempo as VT
    for err in RM.validar(pieza):
        salida.append((FALLO, "reel mudo", err))
    try:
        p2 = RM.traducir(pieza)
    except RM.ErrorReel as exc:
        salida.append((FALLO, "reel mudo", str(exc)))
        return
    for nombre, x0, y0, x1, y1 in ME.fuera_de_zona(ME.cajas_de_pieza(p2), ME.zoom_maximo(p2), VT.SACUDIDA_MAX_PX, VT.ZONA_IG):
        salida.append((FALLO, "zona segura", f"{nombre} sale de la zona segura (x {x0}-{x1}, y {y0}-{y1}) con el zoom de la cámara"))
    plan = RM.planificar(p2)                                 # cambio visual: sin ningún evento de la línea de tiempo durante más de 3 s
    hueco = ME.hueco_max_pulsos(plan["eventos"], plan["duracion"])
    if hueco > 3.0:
        salida.append((FALLO, "cambio visual", f"{hueco} s sin ningún cambio visual (entrada, golpe, transición o cifra): como mucho 3 s"))
    primera = p2["escenas"][0]["elementos"] if p2["escenas"] else []
    if not any(float(e.get("t0", 1)) == 0.0 and e.get("entra") == "ya" and e.get("tipo") in ("titulo", "etiqueta", "texto")
               for e in primera):
        salida.append((FALLO, "gancho", "la primera escena no trae titular en pantalla desde el fotograma 0"))
    if pieza.get("escenas") and pieza["escenas"][0].get("plantilla") == "gancho" and pieza["escenas"][0].get("personaje"):
        if not any(e.get("tipo") == "personaje" and float(e.get("t0", 1)) == 0.0 and e.get("entra") == "ya" for e in primera):
            salida.append((FALLO, "gancho", "el personaje no está en el fotograma 0: la portada sería solo texto"))
    zoom = ME.zoom_maximo(p2)
    for i, esc in enumerate(p2["escenas"], 1):                # cada escena con una ilustración o una forma grande
        grande = any(e.get("tipo") == "personaje" or e.get("tipo") == "cifra" or (e.get("tipo") == "mancha" and float(e.get("r2", e.get("rx", 0)) or 0) >= 100)
                     for e in esc["elementos"])
        if not grande:
            salida.append((FALLO, "peso visual", f"escena {i}: no hay personaje, cifra ni forma grande (el texto llevaría todo el peso)"))
        # ningún texto tapa el estallido de una cifra (con el zoom máximo y el margen de la sacudida)
        estallidos = []
        for el in esc["elementos"]:
            if el.get("tipo") == "cifra" and el.get("r2"):
                cx, cy, r2 = float(el["cx"]), float(el["cy"]), float(el["r2"])
                estallidos.append((cx - r2, cy - r2, cx + r2, cy + r2))
        for el in esc["elementos"]:
            if el.get("tipo") in ("titulo", "texto", "etiqueta", "globo", "sello"):
                caja = ME._caja_de(el)
                if caja is None:
                    continue
                a = _caja_con_zoom([round(float(v), 1) for v in caja], zoom)
                for est in estallidos:
                    if _se_solapan(a, [float(v) for v in est]):
                        salida.append((FALLO, "solapes", f"escena {i}: «{el.get('texto', '')[:40]}» tapa el estallido de la cifra"))
    if carpeta is None:
        return
    for nombre in ("video.mp4", "video_mudo.mp4", "musica.wav", "eventos.json", "ficha_sonido.txt", "registro.json"):
        if not (carpeta / nombre).is_file():
            salida.append((FALLO, "salida", f"falta {nombre} en {carpeta.name}: hay que volver a renderizar"))
    mp4 = carpeta / "video.mp4"
    if mp4.is_file():
        if ME.listas_de_edicion(mp4):                        # Instagram pide Reels sin listas de edición
            salida.append((FALLO, "contenedor", "el MP4 trae listas de edición: Instagram pide Reels sin ellas"))
        if not ME.tiene_audio(mp4):                          # un MP4 sin pista de audio se publicaría mudo (Instagram lo penaliza)
            salida.append((FALLO, "audio", "el MP4 no tiene pista de audio: un reel sin sonido sale penalizado en Instagram"))
    ruta = carpeta / "registro.json"
    if not ruta.is_file():
        return
    reg = _json.loads(ruta.read_text(encoding="utf-8"))
    if reg.get("huella") != ME.huella_pieza(pieza):
        salida.append((FALLO, "registro", "el registro es de otra versión de la pieza: hay que volver a renderizar"))
    estilo = reg.get("estilo")
    dur = float(reg.get("duracion_s", 0))
    if not 15 <= dur <= 25:
        salida.append((FALLO, "duración", f"el vídeo dura {dur} s: el formato pide de 15 a 25 s"))
    for malo in ME.respaldos_del_render(reg):
        salida.append((FALLO, "respaldo", malo))
    areas = reg.get("area_texto")
    if not areas:
        salida.append((FALLO, "medidas", "el registro no trae el área de texto por escena"))
    for area in areas or []:
        if area["texto_pct"] > TEXTO_MAX_PCT:
            salida.append((FALLO, "área de texto", f"escena {area['escena']}: el texto ocupa el {area['texto_pct']} % del fotograma "
                                                   f"(como mucho {TEXTO_MAX_PCT:.0f} %)"))
        ilu = round(area["cifra_pct"] + area["figura_pct"], 1)
        if ILUSTRACION_MIN_PCT is not None and ilu < ILUSTRACION_MIN_PCT:
            salida.append((FALLO, "peso visual", f"escena {area['escena']}: la ilustración ocupa el {ilu} % del fotograma "
                                                 f"(mínimo {ILUSTRACION_MIN_PCT} %)"))
        elif ilu < ILUSTRACION_AVISO_PCT:
            salida.append((AVISO, "peso visual", f"escena {area['escena']}: la ilustración (figura y cifra) ocupa el {ilu} % del "
                                                 f"fotograma (el encargo pide la mayor parte; el umbral de fallo lo decide José)"))
    med = reg.get("medidas") or {}
    son = med.get("sonoridad_salida")
    if not son or "lufs" not in son:
        salida.append((FALLO, "sonoridad", f"no se pudo medir la sonoridad del MP4 ({(son or {}).get('error', 'sin dato')})"))
    if "pico_dbtp" in (son or {}) and son["pico_dbtp"] > -1.5:
        salida.append((FALLO, "sonoridad", f"pico verdadero de {son['pico_dbtp']} dBTP: el reel pide -1,5 dBTP o menos"))
    if son and "lufs" in son and not -15.0 <= son["lufs"] <= -13.0:
        salida.append((AVISO, "sonoridad", f"la salida mide {son['lufs']} LUFS (se espera de -15 a -13)"))
    if med.get("destellos_max_por_s", 0) > ME.MAX_DESTELLOS_S:
        salida.append((FALLO, "destellos", f"{med.get('destellos_max_por_s')} destellos en un segundo: como mucho {ME.MAX_DESTELLOS_S}"))
    if med.get("cambios_max_hueco_s", 0) > 3.0:
        salida.append((FALLO, "cambios", f"{med.get('cambios_max_hueco_s')} s sin cambio visual: como mucho 3 s"))
    mp4m = med.get("mp4")
    if not mp4m:
        salida.append((FALLO, "medidas", "el registro no trae las medidas del MP4 (hay que volver a renderizar)"))
    else:
        if mp4m.get("parpadeo_rojo_max_por_s", 0) > 0:
            salida.append((FALLO, "parpadeo rojo", f"{mp4m['parpadeo_rojo_max_por_s']} parpadeo(s) rojo(s) por segundo"))
        if mp4m.get("destellos_max_por_s", 0) > ME.MAX_DESTELLOS_S:
            salida.append((FALLO, "destellos", f"el MP4 da {mp4m['destellos_max_por_s']} destellos en un segundo"))
        if mp4m.get("saltos_luminancia_max_por_s", 0) > ME.MAX_SALTOS_S:
            salida.append((FALLO, "brillo", f"{mp4m['saltos_luminancia_max_por_s']} cambios de brillo de pantalla completa en un segundo: "
                                            f"como mucho {ME.MAX_SALTOS_S}"))
        fuera = mp4m.get("fuera_de_paleta_max_pct")
        if fuera is None:
            salida.append((FALLO, "paleta", "el registro no trae la medida de la paleta del MP4"))
        elif fuera > ME.FALLO_PALETA_PCT:
            salida.append((FALLO, "paleta", f"un fotograma tiene el {fuera} % de píxeles fuera de la paleta (como mucho {ME.FALLO_PALETA_PCT} %)"))
        elif fuera > ME.AVISO_PALETA_PCT:
            salida.append((AVISO, "paleta", f"un fotograma tiene el {fuera} % de píxeles fuera de la paleta (aviso desde {ME.AVISO_PALETA_PCT} %)"))
    if (reg.get("musica") or {}).get("recortes_muestras", 0):
        salida.append((FALLO, "recortes", "la pista de música tiene muestras recortadas"))
    al = reg.get("alineacion")
    if not al or not al.get("wav"):
        salida.append((FALLO, "alineación", "falta la medida de alineación de los golpes (música)"))
    else:
        if not al["wav"].get("dentro_de_un_fotograma", False):
            salida.append((FALLO, "alineación", "los golpes programados no caen en la señal de la música a un fotograma "
                                                 f"(cobertura mínima {MP.COBERTURA_MIN_PCT} %, error máximo {MP.ERROR_MAX_FOTOGRAMAS} fotograma; ver alineacion.wav)"))
        if not (al.get("mp4") or {}).get("dentro_de_un_fotograma", False):
            salida.append((FALLO, "alineación", "en el audio del MP4 decodificado los golpes no pasan el criterio de un fotograma"))
    gs = reg.get("golpe_sincronia")
    if not gs or gs.get("error_max_fotogramas") is None and gs.get("golpes_visuales"):
        salida.append((FALLO, "golpe", "falta la medida de sincronía de los golpes visuales"))
    elif (gs.get("error_max_fotogramas") or 0) > 1.0:
        salida.append((FALLO, "golpe", f"el golpe visual va {gs.get('error_max_fotogramas')} fotogramas por detrás o delante del impacto de la música"))
    cor = reg.get("cortes")
    if not cor:
        salida.append((FALLO, "cortes", "falta la medida de los cortes"))
    else:
        if cor.get("error_max_fotogramas", 0) > 1.0:
            salida.append((FALLO, "cortes", f"un corte cae a {cor.get('error_max_fotogramas')} fotogramas de su pulso"))
        mc = cor.get("mp4") or {}
        if mc.get("error_max_fotogramas") is None and cor.get("cortes"):
            salida.append((FALLO, "cortes", "no se ve el cambio de algún corte en el MP4 (ver cortes.mp4.detalle)"))
        elif (mc.get("error_max_fotogramas") or 0) > 1.0:
            salida.append((FALLO, "cortes", f"en el MP4, un corte se ve a {mc.get('error_max_fotogramas')} fotogramas de su pulso "
                                            "(con el retardo del audio medido)"))
    if not reg.get("sincronia"):
        salida.append((FALLO, "sincronía", "falta el retardo del audio del MP4 (sin él no se puede comprobar la sincronía)"))
    if float(reg.get("tiempo_render_s", 0)) > 600:
        salida.append((AVISO, "tiempo", f"el render tardó {reg.get('tiempo_render_s')} s (el objetivo es menos de 10 minutos)"))
    if estilo is None:
        salida.append((FALLO, "registro", "el registro no dice el estilo de música"))


def controlar(pieza: dict, imagenes: Path | None = None, cuenta: str | None = None) -> list[tuple[str, str, str]]:
    original = pieza
    if pieza.get("formato") == "video" and pieza.get("esquema") == "foto-1":      # reel de fotos reales (reel_foto.py): lleva sus propios controles de vídeo
        import reel_foto as RF
        registro = None
        if imagenes and (imagenes / "registro.json").is_file():
            registro = json.loads((imagenes / "registro.json").read_text(encoding="utf-8"))
        return RF.controlar_reel(pieza, registro)
    if pieza.get("formato") == "video" and pieza.get("esquema") == "slides-1":    # reel de hojas (reel_slides.py): textos de las hojas y el registro del vídeo
        salida_s: list = []
        try:
            banco_s = cargar_banco(cuenta or cuenta_de(pieza))
        except V.ErrorValidaciones as exc:
            return [(FALLO, "validación", str(exc))]
        pz = dict(pieza, _citas=citas_de(pieza, banco_s))
        c_estructura(pz, salida_s)
        c_cifras(pz, banco_s, salida_s)
        c_lineas_rojas(pz, salida_s)
        c_texto_ia(pz, salida_s)
        c_descargo(pz, salida_s)
        c_densidad(pz, salida_s)
        reg = imagenes / "registro.json" if imagenes else None
        if not reg or not reg.is_file():
            salida_s.append((FALLO, "vídeo", "falta registro.json del reel"))
        else:
            r = json.loads(reg.read_text(encoding="utf-8"))
            if not 5 <= r.get("duracion_s", 0) <= 45:
                salida_s.append((AVISO, "vídeo", f"dura {r.get('duracion_s')} s: lo previsto es de 5 a 45 s"))
            if (r.get("ancho"), r.get("alto")) != (1080, 1920) or r.get("codec") != "h264":
                salida_s.append((FALLO, "vídeo", f"formato {r.get('ancho')}x{r.get('alto')} {r.get('codec')}: tiene que ser 1080x1920 h264"))
            if r.get("tamano_mb", 0) > 18:
                salida_s.append((FALLO, "vídeo", f"pesa {r.get('tamano_mb')} MB (el tope es 18)"))
            if not r.get("tiene_audio"):
                salida_s.append((FALLO, "vídeo", "el vídeo no lleva pista de audio (ni siquiera en silencio)"))
        return salida_s
    if pieza.get("formato") == "video" and pieza.get("esquema") == 3:      # reel mudo: los textos se miran en su traducción
        import reel_mudo as RM
        try:
            p2 = RM.traducir(pieza)
        except RM.ErrorReel as exc:
            return [(FALLO, "reel mudo", str(exc))]
        pieza = dict(pieza, escenas=[{"elementos": e["elementos"], "voz": "", "fondo": e["fondo"]} for e in p2["escenas"]])
        pieza = dict(pieza, hojas=_hojas_de_video(pieza))
    elif pieza.get("formato") == "video":
        pieza = dict(pieza, hojas=_hojas_de_video(pieza))
    salida: list = []
    try:
        banco = cargar_banco(cuenta or cuenta_de(pieza))
    except V.ErrorValidaciones as exc:                 # sin validaciones legibles no se da nada por bueno
        return [(FALLO, "validación", str(exc))]
    pieza = dict(pieza, _citas=citas_de(pieza, banco))
    c_estructura(pieza, salida)
    c_cifras(pieza, banco, salida)
    c_lineas_rojas(pieza, salida)
    c_voces(pieza, banco, salida)
    c_texto_ia(pieza, salida)
    c_descargo(pieza, salida)
    c_ia_declarada(pieza, salida)
    c_densidad(pieza, salida)
    if original.get("formato") == "video" and original.get("esquema") == 3:
        c_mudo(original, imagenes, salida)
    elif pieza.get("formato") == "video":
        c_video(pieza, imagenes, salida)
    if imagenes and imagenes.exists():
        # dirección F (fotos reales): la paleta se mide sobre la versión sin fotos (_sinfoto/), que dibuja render.py; sin ella, no se da por buena
        if pieza.get("direccion") in ("f", "g"):
            if (imagenes / "_sinfoto").exists():
                c_paleta(imagenes / "_sinfoto", salida, T.cargar(pieza.get("cuenta")))
            else:
                salida.append((FALLO, "paleta", "pieza con fotos sin la versión _sinfoto/ para medir la paleta (hay que dibujarla con render.py)"))
        else:
            c_paleta(imagenes, salida, T.cargar(pieza.get("cuenta")))
    return salida


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pieza", type=Path)
    ap.add_argument("--imagenes", type=Path)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    pieza = json.loads(a.pieza.read_text(encoding="utf-8"))
    res = controlar(pieza, a.imagenes)
    fallos = [r for r in res if r[0] == FALLO]
    for nivel, quien, msg in res:
        print(f"{nivel:6} [{quien}] {msg}")
    print(f"\n{pieza['id']}: {len(fallos)} fallos, {sum(1 for r in res if r[0] == AVISO)} avisos")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
