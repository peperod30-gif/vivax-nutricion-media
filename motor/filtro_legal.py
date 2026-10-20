import re

# Riesgo alto: bloquea, salvo que la misma frase sea un desmentido (mito, no, ?).
ALTO = [
    r"\b(cura|curan|previene|previenen|evita|evitan|protege|protegen)\b.{0,30}\b(resfriad|gripe|c[aá]ncer|diabetes|infarto|ictus|alzh|demencia|covid|enfermedad)",
    r"\badelgaza(n|r|nte|ntes)?\b",
    r"\bquema(r|n)?\s+grasa",
    r"\bdetox\b",
    r"\bmilagr",
    r"\b(pierde|perder|baja|bajar|adelgaza|elimina)\s+(hasta\s+)?\d+([.,]\d+)?\s*(kg|kilos)",
    r"\b(revierte|revierten|revertir|revertir[aá]|revertimos)\b",
    r"(?<!no )(?<!ni )\b(deja|dejar|abandona|sustituye|olvida|olv[ií]date)\b.{0,25}(medicaci[oó]n|pastillas|insulina|metformina|estatinas|m[eé]dico)",
    r"(te\s+(hago|preparo|dise[nñ]o|monto)\s+(tu|una)\s+dieta|plan\s+personalizado|m[aá]ndame\s+tu\s+anal[ií]tica|cu[eé]ntame\s+tus\s+s[ií]ntomas)",
    r"\b(soy|somos)\s+(tu\s+)?(nutricionista|dietista)",
    r"#?\b(thinspo|thinspiration|proana|promia|bonespo)\b",
]
# Si la frase contiene alguno de estos, el ALTO baja a REVISAR (desmentido o pregunta).
DESMENTIDO = (r"(\bmito\b|\bfalso\b|\bbulo\b|\bno\b|\bni\b|¿|\?|no hay pruebas|no est[aá] demostrado"
              r"|\bestudio\b|\bensayo\b|metaan[aá]lisis|participantes|proh[ií]be|prohibid|la ley)")
# Siempre bloquea, aunque sea un desmentido.
SIEMPRE = [8, 9, 10]  # índices de ALTO: dieta a medida, «soy nutricionista», hashtags de TCA

# Riesgo medio: pide confirmación explícita de José.
MEDIO = [
    r"\b(cura|curan|previene|previenen)\b",
    r"\b\d+([.,]\d+)?\s*(kg|kilos)\b.{0,25}\b(en|al)\s+(\d+|una|un|dos|tres)\s*(d[ií]as?|semanas?|mes(es)?)\b",
    r"\bnatural(es)?\b",
    r"sin\s+qu[ií]micos",
    r"(estudios|la\s+ciencia)\s+(lo\s+)?(demuestra|demuestran|avala|avalan)",
    r"cient[ií]ficamente\s+(probado|demostrado)",
    r"recomendad[oa]s?\s+por",
    r"\bdefensas\b",
    r"\b(m[aá]s|da|dan|aporta)\s+energ[ií]a\b",
    r"\brendimiento\b",
    r"(acelera|activa|dispara)\s+(el|tu)\s+metabolismo",
    r"\b(desinflama|antiinflamatori)",
    r"\b(baja|bajan|reduce|reducen|controla|controlan|mejora|mejoran|regula|regulan|normaliza|sube|suben|dispara|disparan)\s+(el|la|los|tu|tus)?\s*(colesterol|ldl|insulina|az[uú]car\s+en\s+sangre|glucosa|gluc[eé]mia|tensi[oó]n|triglic[eé]ridos)",
    r"\b(veneno|t[oó]xico|basura|estafa|enga[nñ]a|enga[nñ]o)\b",
    r"bajo\s+en\s+az[uú]car|sin\s+az[uú]car(es)?(\s+a[nñ]adidos)?|fuente\s+de\s+fibra|alto\s+en\s+prote[ií]nas|\blight\b",
    r"gracias\s+a\s+@",
]
JUICIO = r"(veneno|t[oó]xic|basura|estafa|enga[nñ]|timo|os\s+toma)"
# Pieza de desmentido: si aparece en cualquier parte, el ALTO baja a REVISAR
# (José comprueba que el desmentido está en la MISMA diapositiva que la frase falsa).
MITO_PIEZA = r"(\bmito\b|\bfalso\b|\bbulo\b|sin pruebas|no est[aá] demostrado|lo que dicen los datos)"

def revisar(texto, marcas=()):
    """Devuelve (veredicto, motivos). Pasar solo pie y rótulos, no el campo de aviso.
    marcas: nombres de marca de la pieza (desde la cola)."""
    t = texto.lower()
    es_mito = bool(re.search(MITO_PIEZA, t))
    frases = [f for f in re.split(r"(?<=[.!?\n])", t) if f.strip()]
    bloq, rev = [], []
    for f in frases:
        for i, p in enumerate(ALTO):
            if re.search(p, f):
                motivo = f.strip()[:70]
                if i in SIEMPRE or not (es_mito or re.search(DESMENTIDO, f)):
                    bloq.append(motivo)
                else:
                    rev.append(motivo)
        if any(m.lower() in f for m in marcas) and re.search(JUICIO, f):
            bloq.append("marca+juicio: " + f.strip()[:60])
    rev += [p for p in MEDIO if re.search(p, t)]
    if bloq:
        return "BLOQUEAR", bloq
    if rev:
        return "REVISAR", rev
    return "OK", []

if __name__ == "__main__":
    pruebas = [
        "100 g de avena aportan 389 kcal de energía (USDA).",
        "Comida sana con datos: así se lee una etiqueta.",
        "¿Cuál lleva más azúcar? A 24 g · B 17 g · C 8 g por 100 g.",
        "La vitamina C evita resfriados.",
        "Mito: la vitamina C evita resfriados. Lo que dicen los datos.",
        "Pierde 5 kg en 3 semanas con este truco.",
        "El té verde quema grasa mientras duermes.",
        "La dieta low-carb revierte la diabetes tipo 2.",
        "La vitamina C contribuye al funcionamiento normal del sistema inmunitario.",
        "Deja la metformina: la comida es tu medicina.",
        "Contenido divulgativo: no es consejo personal ni sustituye a tu médico.",
        "Escríbeme y te preparo tu dieta.",
        "Cereal Kriko te engaña: es pura basura.",
        "Tóxico de verdad es no leer la etiqueta.",
        "Más energía y concentración con este desayuno.",
        "El hierro ayuda a disminuir el cansancio y la fatiga.",
        "¿Saltarse el desayuno adelgaza?",
        "Dieta sana y variada: 5 cambios de plato.",
    ]
    for p in pruebas:
        v, m = revisar(p, marcas=["Kriko"])
        print(f"{v:9} | {p}")
