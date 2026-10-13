# Telegram de Vivax Nutrición: el panel (UX/UI)

José no se aclara con botones sueltos por pieza, así que todo gira alrededor de **un solo mensaje, el Panel**, que se edita en el sitio.
Código: `router/panel.py` (qué hace cada botón) y `router/textos_panel.py` (todo lo que se dice, en un solo sitio). Se activa con
`Router(..., panel=True)`: la nube lo activa siempre; el router del PC sigue como antes mientras no se pida.

## Principios
- Castellano de España, tuteo, frases cortas. Sin jerga: nada de «decisión», «candado», «hueco» o «programada a mano».
- Cada pantalla tiene «⬅ Panel» o «⬅ Volver». Cada acción dice qué va a pasar y cuándo («Sale el mié 15-oct a las 14:00»).
- Iconos fijos: ⏳ por aprobar, ✅ aprobada y programada, 📤 publicada, ⚠️ con problema, ❌ rechazada.
- Pulsar dos veces no hace nada dos veces: se contesta «Ya estaba: programada».
- Solo el administrador (José) toca el panel. Cualquier otra persona recibe un aviso corto y no cambia nada.
- Las tarjetas antiguas (`n1:ok:7`, `n1:pr:7`…) y las preguntas siguen funcionando; un botón que ya no vale contesta algo amable, nunca una traza.

## Pantallas
| Pantalla | Cómo se llega | Qué hace |
|---|---|---|
| Panel | `/panel`, `/start`, «⬅ Panel» | Qué sale a continuación, cuántas hay por aprobar, cuántas programadas y hasta qué día, aviso si la cola tiene menos de 3 días, si está en pausa, estado de la nube |
| Aprobar pendientes | «⏳ Aprobar pendientes (N)», `/pendientes` | Lista por semanas con día y hora previstos. «✅ Aprobar las N» y «🔍 Revisar una a una» |
| Confirmación | «✅ Aprobar las N» | «Vas a programar N piezas, del … al …». **Un toque** («✅ Sí, aprobar las N») programa todas, cada una a su hora del plan (`produccion.fecha` + `produccion.hora`, o la hora de su hueco); si esa hora ya pasó o falta menos de 2 minutos, al primer hueco libre. Cuenta los fallos uno a uno, con el motivo en una frase («Cita un dato que marcaste como «no usar»»). Primero se programan las de hora exacta y después las que van al primer hueco; dos piezas nunca quedan a la misma hora (si el plan pide una hora ya ocupada, la segunda va al primer hueco libre) |
| Revisar una a una | «🔍 Revisar una a una» | Un solo mensaje con la portada como foto que se edita: «3/14 · mié 15-oct a las 14:00 · título» y dos líneas del pie. Aprobar, Otra hora, Ver hojas (álbum aparte), Ver pie (texto completo), Rechazar, Saltar. Al final, resumen en el panel |
| Otra hora | «🕐 Otra hora» | El plan, cuatro huecos libres y «✍️ Escribir hora» (vale «18:30», «mañana 18:30», «en 2 horas»; 10 minutos para escribirla) |
| Calendario | «📅 Calendario», `/calendario` | 7 días por página, «◀ Semana» y «Semana ▶» (hasta 8 semanas). Cada pieza tiene un número que abre su ficha |
| Ficha | un número del calendario o de la lista | Según el estado: pendiente (aprobar, otra hora, hojas, pie, rechazar), programada (cambiar hora, quitar de la cola), publicada (enlace a Instagram), con problema (reintentar, mover, rechazar) |
| Publicadas | «📤 Publicadas» | Las últimas, con botón al enlace de cada una |
| Más | «⚙️ Más» | Números (si hay medidas), pausar o reanudar, ayuda, estado técnico. `/pausa` y `/seguir` hacen lo mismo |

## Avisos que llegan solos (una sola vez cada uno)
- 📥 **He recibido N piezas nuevas** (sean 1 o 40): un solo mensaje, sin tarjeta por pieza, con tres botones: «✅ Aprobar las N según el plan» (lleva a la confirmación), «🔍 Revisar una a una» y «Después». Las piezas quedan por aprobar y se ven en el Panel, el Calendario y «Aprobar pendientes». Los demás miembros del equipo, si los hubiera, siguen recibiendo la tarjeta de siempre.
- 📤 **Publicada**, con el enlace.
- ⚠️ **No salió**, con el motivo en llano y los botones Reintentar, Mover y Rechazar.
- ⚠️ **Retrasada**: la pieza se ha pasado a otra hora, con botón para cambiarla.
- ☀️ **Hoy salen**: a partir de las 08:00, solo si hay algo.
- 🧭 **Cola corta**: a partir de las 10:00, solo si quedan menos de 3 días y no hay nada pendiente que ya se avise por otro lado.
- Las preguntas de las órdenes se llaman «Pregunta n.º N»; el aviso de lo hecho, «Hecho (pregunta n.º N)». Una pregunta puede llevar un texto largo con varias líneas (un calendario de 40 piezas, hasta 3800 caracteres: `python -X utf8 nube_orden.py preguntar --texto-archivo calendario.txt --opcion si="Sí" …`); si no cabe en un mensaje de Telegram se manda en dos, con los botones en el último, y al cerrarla no se vuelve a mandar el calendario entero. Si responde «Sí» a un lote de más de 5 acciones, el aviso de lo hecho es una frase («40 de 40 acciones … del jue 15-oct al lun 2-nov») y solo detalla los fallos.

## Botones (`pn:`)
`pn:p` panel · `pn:pe` pendientes · `pn:ap` / `pn:ao:<firma>` aprobar todo (la firma es el crc32 de los ids que se vieron: si la lista cambió entre mirar y
confirmar no se aprueba nada y se enseña la lista nueva) · `pn:rv` / `pn:rf` empezar y terminar la revisión · `pn:sk:<id>` saltar · `pn:f:<id>:<contexto>` ficha ·
`pn:ok|no|qu|rt|ch|ht|hv|vh|vp:<id>[:<contexto>]` aprobar, rechazar, quitar, reintentar, otra hora, escribir hora, elegir hora, hojas, pie ·
`pn:cal:<semana>` · `pn:pu` · `pn:mas` · `pn:num` · `pn:te` · `pn:pa` · `pn:ay`. Contextos: `p` pendientes, `c<n>` calendario, `pu`, `a` aviso, `r` revisión.
Las horas que se ofrecen se validan en el servidor: de uso único, de quien las abrió, 15 minutos de vida, solo mientras la pieza esté en el estado esperado.

## La nube responde rápido (`ejecutar_nube.py`)
El cron de GitHub no arrancó nunca, así que lo que manda es la cadena del workflow (`router.yml`): cada ejecución lanza la siguiente. Encima de eso:
- **Modo activo** (`--activo`): si José pulsa o escribe, la ejecución no termina. Sigue con sondeo largo de Telegram (15 s) y contesta al momento hasta que pasan 4 minutos sin actividad suya o 9 minutos en total. Sigue publicando mientras tanto.
- **Vigilia** (`--vigilia 235`): la espera entre pasadas (antes un `sleep 235`) se hace escuchando Telegram, así el primer toque de José se contesta en segundos y no en hasta 4 minutos.
- **Encadenado inmediato**: si José tocó algo durante la ejecución o llegó a los 9 minutos con él todavía activo, la ejecución lanza la siguiente ya (`workflow_dispatch` con el token del workflow, permiso `actions: write`), sin dormir. El workflow lo sabe porque la ejecución deja `encadenada=si` (o `vigilia=si` si solo esperó escuchando) en `$GITHUB_OUTPUT`; sin señal (la ejecución falló antes) hace lo de siempre: dormir 235 s y lanzar. La ventana de actividad se guarda en el estado (`nube_activa_hasta`) para que la siguiente ejecución la continúe.
- **Límites**: como mucho 6 encadenadas inmediatas seguidas sin un toque nuevo de José y 40 al día; pasado eso sigue la cadena lenta del workflow.
- **Publicación puntual**: si hay una pieza aprobada que vence en menos de 6 minutos, la ejecución espera y la publica en punto, atendiendo Telegram mientras tanto.
- **Apagarlo**: variable del repositorio `ENCADENAR=no` (apaga encadenado y vigilia) o `VIVAX_MODO_ACTIVO=0` (apaga el modo activo).
- Sin red, un sondeo que falla no da vueltas a toda velocidad: espera hasta 5 s entre intentos. Un 409 de Telegram (otro lector del mismo bot) detiene todo sin publicar.
- Menú de comandos del bot (`/panel`, `/pendientes`, `/calendario`, `/pausa`, `/seguir`, `/ayuda`): se pone una vez y solo se repite si cambia la lista.

## Pruebas
- `python -X utf8 -m unittest router.tests.test_panel` (pantallas, lotes, revisión una a una, calendario, fichas, avisos, idempotencia, no administrador).
- `python -X utf8 -m unittest nube.tests.test_nube` (incluye el modo activo con reloj falso, la vigilia, el encadenado, la publicación puntual y la importación de un reel).
- `python -X utf8 -m nube.panel_demo [--piezas 40]`: 22 piezas de ejemplo (o 42, con 40 del plan) y cada pantalla impresa con sus botones. No toca el bot real ni ninguna base real.

## Lo que no se puede probar sin el bot real
Subir y editar fotos de verdad (`sendPhoto`, `editMessageMedia`), el menú de comandos en el cliente de Telegram, que GitHub acepte el `workflow_dispatch` con el token del workflow y la latencia real del sondeo largo desde un runner. Todo lo demás está cubierto con Telegram, reloj y publicador de mentira.
