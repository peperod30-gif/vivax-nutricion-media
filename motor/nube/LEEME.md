# Router de Vivax Nutrición en la nube

Qué es: la misma lógica del router de Telegram y de Instagram que corre en el PC, ejecutada por GitHub Actions cada pocos minutos, para que
**aprobar una pieza desde Telegram y que salga en Instagram a su hora no necesite ni el PC ni Claude Code encendidos**.

Estado de esta entrega: construido y probado, **sin activar**. El workflow solo se puede lanzar a mano (`workflow_dispatch`); el disparador
automático (`schedule`) está comentado en `.github/workflows/router.yml`.

## Cómo funciona

Cada ejecución (una pasada, o dos con 20 s de pausa si en la primera pasó algo):

1. baja la rama `estado` del repositorio `peperod30-gif/vivax-nutricion-media` y descifra la base (AES-256 con `VIVAX_CLAVE_ESTADO`);
2. importa las piezas nuevas de `motor/piezas/nube/` con los mismos controles de calidad que en el PC;
3. ejecuta las órdenes de `motor/ordenes/` (avisos y preguntas a José con acciones);
4. lee lo que José ha pulsado o escrito en Telegram (`getUpdates` con tiempo 0) y lo procesa con el código de siempre: tarjetas, Aprobar,
   Programar, Cambiar hora, Quitar, /cola, /estado…;
5. manda las tarjetas pendientes, cierra decisiones, hace las acciones de las decisiones que José ha respondido y entrega los avisos;
6. publica en Instagram lo aprobado cuya hora ha llegado (el publicador de siempre, con sus reintentos y sus protecciones);
7. si algo cambió de verdad, cifra otra vez y **sobrescribe** la rama `estado` con un solo commit (el repositorio no crece).

Dónde vive cada cosa:

| Qué | Dónde |
|---|---|
| Código | `motor/nube/` (fuente en el PC) y la copia mínima en `nube_repo/motor/` (se sincroniza con `nube_enviar.py --codigo`) |
| Estado (bases, token de Instagram renovado, validaciones de José) | rama `estado` del repo, cifrado en `router_datos.tar.enc` |
| Piezas e imágenes | `motor/piezas/nube/<id>.json` y `motor/salida/<id>/NN.jpg` en `main` (el repo es público: Instagram descarga de ahí) |
| Órdenes | `motor/ordenes/<nombre>.json` |
| Secretos | secretos de Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `IG_N1_TOKEN`, `IG_N1_USER_ID`, `VIVAX_CLAVE_ESTADO` |
| Clave del estado en el PC | `motor/router/.env.nube` (una línea `CLAVE=...`) |

## Herramientas del PC (`motor/`)

- `nube_enviar.py <ids…>` copia piezas e imágenes al clon, comprueba que no sube nada indebido y sube a `main`. `--codigo` sincroniza el código.
- `nube_orden.py avisar|preguntar …` deja una orden. Con `preguntar` se puede decir qué hace la nube según la opción que pulse José
  (`--aprobar todas=ID@2026-10-13T13:00`, `--rechazar`, `--cambiar-hora`). **La acción solo se ejecuta si la respuesta es de José** (botón o
  texto); si la decisión caduca y se cierra por su opción por defecto, no se hace nada. Sustituye a `esperar_y_aprobar.py`, que necesitaba el
  PC encendido.
- `nube_estado.py ver [--rama estado|estado-prueba]` lista piezas, decisiones y avisos del estado en la nube (no deja nada descifrado en disco).
- `migrar_a_nube.py [--definitivo]` copia el estado del PC a la nube (sin `--definitivo`: a `estado-prueba`, sin tocar nada más).
- `nube/configurar_secretos.py` pone los secretos desde los `.env` del PC sin mostrar ningún valor (`--ver` dice cuáles existen).

## Qué NO hace respecto al router local

- Es solo la cuenta **n1 (Vivax Nutrición)**. Las tarjetas, los botones y los comandos son los mismos.
- **Vivax Campo (c1)** no vive en la nube: su bot y su publicador dependen del proyecto «Vivax auditori» del PC. Al pasar a la nube, el router
  local deja de alojarlo.
- El **plan mensual** (`/plan`) y la **producción automática** siguen en el PC. Las piezas se fabrican en el PC y se mandan con `nube_enviar.py`.
- Un reel (vídeo) se manda con su `video.mp4`; importarlo en la nube necesita los controles de vídeo (`reel_foto`), que no están allí: los
  reels ya importados se publican, pero uno nuevo hay que importarlo con la copia del estado desde el PC (migración) o añadir ese módulo.
- Si José valida hechos desde Telegram, quedan en la nube (viajan en el estado cifrado); el PC no los ve.

## Latencia y límites (honestidad)

- El `cron` de GitHub no es instantáneo ni exacto: lo normal son 5 a 15 minutos entre que José pulsa y que se aplica, y una pieza programada a
  las 13:00 puede salir entre las 13:00 y las 13:15. El adaptador ya la mueve al siguiente hueco si pasa más de una hora.
- Mientras José no pulse, nada se aprueba. La nube nunca aprueba por su cuenta.
- Si Telegram contesta 409 (otro proceso lee el mismo bot, por ejemplo el router del PC), la nube se detiene **sin publicar**: dos routers con
  dos colas publicarían dos veces. Hay que parar uno de los dos.
- **Zona gris de los términos de GitHub:** usar Actions como programador de tareas permanente (servicio sin relación con construir software) no
  es el uso previsto de Actions; muchos proyectos lo hacen, pero GitHub podría limitarlo. La alternativa limpia es un Worker de Cloudflare con
  su cron y un webhook de Telegram (respuesta instantánea y hora exacta); requiere una cuenta gratuita de Cloudflare que tendría que crear José.
- El repositorio es público: lo que se manda a `nube/` (piezas e imágenes) es visible antes de salir. El estado va cifrado.

## Activarlo (cambio definitivo) — en este orden

1. Parar el router del PC y su vigilante (si no, el vigilante lo relanza cada 5 minutos y habría dos lectores de Telegram):
   `Stop-ScheduledTask VivaxRouter; Disable-ScheduledTask VivaxRouter; Disable-ScheduledTask VivaxVigilante`
   y cerrar los procesos `python -m router`.
2. `python -X utf8 migrar_a_nube.py --definitivo` (se niega si el router local sigue vivo).
3. Descomentar `schedule:` y su `cron` en `nube_repo/.github/workflows/router.yml` (copia en `motor/nube/router.yml`) y subirlo con
   `python -X utf8 nube_enviar.py --codigo`.
4. Comprobar con `gh run list --repo peperod30-gif/vivax-nutricion-media` y `python -X utf8 nube_estado.py ver`.

## Volver al local

`Enable-ScheduledTask VivaxRouter, VivaxVigilante; Start-ScheduledTask VivaxRouter`, y comentar otra vez el `schedule` (si no, la nube recibe 409
y se detiene, que es lo seguro). Lo decidido en la nube desde la migración (aprobaciones, publicaciones) está solo en el estado cifrado:
`nube_estado.py ver --guardar <carpeta>` lo descifra para copiar la base de vuelta si hace falta.

## Rotar el token de Instagram

El router renueva solo el token cuando toca (cada 20 días como mínimo) y guarda el nuevo en el estado cifrado (`token_ig.txt`). El secreto
`IG_N1_TOKEN` solo se usa si ese fichero no existe. Si hay que cambiarlo a mano: `gh secret set IG_N1_TOKEN --repo peperod30-gif/vivax-nutricion-media`
y borrar `token_ig.txt` del estado (o volver a migrar). El actual caduca el 2026-12-08.

## Pruebas

`python -X utf8 -m unittest nube.tests.test_nube -v` (desde `motor/`): cifrado de ida y vuelta, cambio lógico, reescritura de rutas, importar con
controles, órdenes y respuesta de José (con la hora del plan), caducidad por defecto sin aprobar, respuesta de otra persona, 409, publicación y
no repetirla tras cifrar y descifrar. Y el workflow en modo prueba: `gh workflow run router.yml -f prueba=true`.
