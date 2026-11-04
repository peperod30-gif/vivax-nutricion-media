# Historias de @vivax_nutri (formato «historia», 4-nov-2026)

José pidió «que publiques historias y hagas historias destacadas». La cola de la nube ya publica historias con la API oficial
(la misma que los Reels y los carruseles: `router/instagram.py`). Las **destacadas no se pueden crear por la API**: se hacen a mano
en la app (al final de esta guía está cómo).

Este cambio es solo local y viene con pruebas. No se ha subido nada a la nube: entra con el próximo despliegue del código
(`nube_enviar.py --codigo`, que copia la lista de `nube/sincronizar_codigo.py`).

## Qué es una historia para la cola

- **Una** imagen JPEG de **1080x1920** (9:16) o **un** vídeo MP4 de **1080x1920**: H.264, de **3 a 60 s** y con pista de audio
  **AAC** (aunque sea en silencio). Dura 24 horas en el perfil.
- Se publica con `media_type=STORIES`, usando `image_url` o `video_url`.
- **No lleva** pie, hashtags, texto alternativo ni `share_to_feed`, porque la API no los admite en historias. El publicador
  descarta el pie aunque se lo pasen.
- Tras publicarla, el perfil **no** deja el primer comentario con las fuentes, porque una historia no admite comentarios.
- El resultado se guarda igual que el de las demás piezas: estado `publicada`, `ig_media_id` e `ig_permalink`. También sale el
  aviso «✅ Publicada» por Telegram.
- Si Meta rechaza una historia por **permiso** (código 10 o 200-299), la historia pasa a error con su aviso y el feed **sigue**:
  no se para la cola 6 horas como con una pieza del feed, porque puede ser un permiso solo de las historias. Si el permiso falta
  de verdad, la siguiente pieza del feed falla igual y para la cola ella misma. Token, cuenta y alojamiento sí paran la cola.

## El JSON de una historia

Va en `piezas/nube/<id>.json`, como las demás piezas:

```json
{
  "id": "n1-h-01-pasta",
  "cuenta": "n1",
  "formato": "historia",
  "direccion": "g",
  "estilo": "viral",
  "hechos": ["ALI-024", "ALI-025"],
  "titulo": "Pasta o calabacín",
  "pie_instagram": "Descripción INTERNA (no se publica): historia del 5-nov que lleva al carrusel de las 10:30.",
  "alt": "Pasta cocida, 30,9 g de hidratos por 100 g, frente a calabacín crudo, 3,1 g.",
  "produccion": {"autoaprobar": true, "fecha": "2026-11-05"},
  "pilar_plan": "datos",
  "serie": "historias",
  "hojas": [
    {"plantilla": "historia", "titulo": "Pasta cocida: 30,9 g de hidratos", "texto": "Calabacín crudo: 3,1 g. Por 100 g."}
  ]
}
```

- `hojas` contiene **los textos que se dibujan** en la historia. Una imagen lleva una sola hoja; un vídeo lleva una hoja por
  escena (8 como mucho). Valen los mismos campos de texto que en las demás piezas (`titulo`, `texto`, `cifra`, `dato`...). Si
  falta `plantilla`, se toma «historia», que tiene un tope de 20 palabras.
- `pie_instagram` es obligatorio, pero es una **descripción interna**: la ven José en Telegram y el panel, y no se publica ni se
  controla.
- `alt` no se publica, aunque pasa las líneas rojas igual que el resto.
- `ia: true` solo si la historia lleva voz sintética (ver «Límites de la API»).
- **Nunca** lleva `produccion.paleta_libre`. En una historia no sirve: la paleta se mide siempre.
- Lo del autopiloto es igual que en las demás piezas: `autoaprobar` y `fecha` (el primer día en que puede salir). Con
  `revision_jose` o `muestra`, el autopiloto no la programa.

## Ficheros (en `salida/<id>/`)

| Historia | Ficheros | Qué sube `nube/pc.py` a la nube |
|---|---|---|
| Imagen | `01.jpg`, y si lleva foto, `_sinfoto/01.jpg` (igual que las piezas F, G y Luxury) | `01.jpg` y `_sinfoto/01.jpg` |
| Vídeo | `video.mp4` | solo `video.mp4` |

Una carpeta con `01.jpg` y `video.mp4` a la vez es un error, tanto en los controles como en `pc.imagenes_de`, porque no se sabe
cuál de los dos es la historia.

## Controles (`controles.py`, apartado 8: `c_historia`)

- **Textos dibujados** (`hojas`): pasan cifras contra el banco, líneas rojas, filtro legal, texto de IA, voces y densidad, igual
  que cualquier pieza. Hace falta citar al menos un hecho del banco.
- **No se controlan** el pie (no se publica) ni el descargo «no es consejo personal…», porque no hay pie donde ponerlo. Si una
  historia da una cifra de salud, conviene que remita a la publicación del feed que sí lleva el descargo. Una historia tampoco
  puede citar voces de estudios (VOZ-...), porque eso exige decirlo en el pie.
- **Imagen**: un JPEG de exactamente 1080x1920 y una sola hoja de textos. La paleta se comprueba con `c_paleta`: negro, blanco y
  oliva #B0BD6B con sus mezclas; aviso a partir del 3 % fuera y fallo a partir del 8 %. Se mide sobre `_sinfoto/01.jpg` si existe
  y, si no, sobre la propia imagen.
- **Vídeo**: MP4 H.264 de 1080x1920, de 3 a 60 s, con audio AAC, 20 MB como mucho (es el límite de jsDelivr, que sirve los vídeos
  de GitHub) y sin listas de edición. Las listas de edición solo se miran en el PC, porque la nube no lleva `video_medidas`. La
  paleta se mide con `c_paleta` en 8 fotogramas repartidos por el vídeo.
- La nube solo necesita PIL, numpy e imageio-ffmpeg, que ya están en `nube/requirements.txt`. No hay módulos nuevos que subir.

## Huecos: aparte de los del feed

- Las historias tienen su propia lista, `pauta_historias`, en `nube/cuentas.nube.json`:
  `[["09:30","historia"], ["14:45","historia"], ["20:00","historia"]]`. Cada hora queda a 60 minutos o más de las seis del feed
  (08:30, 10:30, 13:00, 16:30, 19:00 y 21:00). Para cambiar las horas, se edita esa lista. La `pauta` del feed no se toca: también
  aceptaría un hueco «historia», pero así queda más claro.
- **Autopiloto** (`nube/autopiloto.py`): una historia marcada con `autoaprobar` va al primer hueco libre de tipo «historia».
  Respeta lo mismo que el resto: la fecha mínima del plan, 30 minutos de margen y 21 días de horizonte.
- **Las dos familias no se ven.** Una historia no ocupa un hueco del feed, no separa sus 45 o 90 minutos y no da el aviso «ojo:
  hay otra a menos de 45 minutos». Tampoco al revés. Entre dos historias basta con que no caigan en el mismo hueco (5 minutos).
- **Aprobar sin hora**, ya sea con el botón ✅ Aceptar de Telegram (`fp:ok`), con ✅ Aprobar o con «Reintentar», lleva la historia
  al siguiente hueco de historias, nunca a uno del feed. «Cambiar hora» y «Programar» ofrecen las horas de historias. Si una
  historia no sale a su hora porque el router no estaba en marcha, pasa al siguiente hueco de historias. Las horas salen de
  `pauta_historias`; si la cuenta no tiene esa lista (el router del PC), se usan las mismas tres de
  `router/adaptadores/nutricion.py: HORAS_HISTORIAS`.
- El panel no alarga «días de cola» con historias: el aviso de cola corta sigue contando solo el feed.

## Telegram

- Las historias llegan a José con los mismos botones que las publicaciones reales: ✅ Aceptar · ❌ Rechazar · ✏️ Modificar con IA
  (`fp:`, de `enviar_publicaciones.py` y `router/formatos_serie.py`). El mensaje dice «· historia» en lugar de
  «Reel/carrusel/imagen», y el pie aparece marcado como «Descripción interna».
- Una historia en vídeo se envía como vídeo, en la entrega de la pieza y en «Ver hojas», no como foto.
- El resumen de la noche escribe «Historia», y la imagen resumen pone «HISTORIA» en la franja.

## Límites de la API: lo que NO se puede hacer

- **Destacadas**: no hay ningún endpoint para crearlas ni para añadirles historias. Se hacen a mano en la app de Instagram. Desde
  el perfil: «+» → «Destacada» → se eligen historias del archivo → nombre y portada. Desde una historia publicada: «Destacar» →
  elegir destacada. Para que el perfil quede ordenado, conviene una destacada por serie (por ejemplo «Hidratos», «Vitaminas»,
  «Etiquetas», «Para nutris»), con la portada en negro, blanco y oliva.
- **Stickers interactivos**: encuesta, pregunta, cuestionario, deslizador, cuenta atrás, enlace, ubicación, música, hashtag y
  mención con sticker no se pueden publicar por la API («Publishing stickers (i.e., link, poll, location) is not supported»).
  Si una historia necesita encuesta o enlace, se sube a mano. El texto dibujado puede decir «mira el último post».
- **Pie y texto alternativo**: la API no los admite en historias.
- **Etiqueta de IA** (`is_ai_generated`): Meta la documenta para las publicaciones (solo excluye los hijos de un carrusel) y no
  dice nada de las historias. La cola la envía solo si la pieza trae `ia: true`, igual que en los demás formatos. Si Meta la
  rechazara, la historia pasa a error y no sale; nunca sale una historia con voz sintética sin la marca.
- **Borrar, editar o programar dentro de Instagram**: la API no lo hace. La hora la pone la cola, y una historia publicada se
  borra a mano.
- **Números**: las historias no salen en `/media`, así que no entran en el parte diario ni en el informe semanal
  (`metricas.py`). Sus estadísticas solo existen durante las 24 horas que dura la historia (y unas semanas en el archivo de la
  app). Hoy la cola no las recoge.
- **Recuperación tras una caída**: como no tiene pie, una historia se busca en `/{usuario}/stories` por la hora. Si hay más de
  una posible (por ejemplo, otra subida a mano a la vez), queda con id «desconocido» y sin enlace, para no apuntar otra.

## Pruebas

Desde `motor/`:

```
python -X utf8 -m unittest router.tests.test_historias -v
python -X utf8 -m unittest nube.tests.test_nube.AutopilotoHistorias -v
```

`router/tests/test_historias.py` cubre:

- el publicador: el contenedor STORIES con imagen y con vídeo, sin caption, la validación del fichero, la simulación, el límite
  de 20 MB, la espera de vídeo y la recuperación en `/stories`;
- los controles: una historia válida y las que fallan (tamaño, paleta, cifra fuera del banco, sin audio, dos ficheros...);
- el adaptador: importar, aprobar, los huecos aparte, el retraso y la publicación de punta a punta con el publicador real contra
  el HttpFalso;
- Telegram: los botones `fp:`, `sendVideo`, la portada y el resumen de la noche.

`nube/tests/test_nube.py: AutopilotoHistorias` cubre el tipo de hueco y la pauta aparte, `montar`, el autopiloto y lo que sube
`pc.py`.
