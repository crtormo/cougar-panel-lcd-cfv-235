# Pendiente para Windows (y qué capturar)

> **ACTUALIZACION 2026-09-14 (banco de pruebas).** Espiando al editor por el inspector
> (`cdp_parche.js`) se resolvieron tres de los pendientes: **#2** el editor no manda ningun
> comando de borrado (borrar es local, store.json + fichero); **#3** el `.osd` es un PNG de
> 1920x462 con otra extension; **#6** el video se re-codifica y se sube entero por
> `transport`, no hay comando «play» — el editor decodifica y sube cada fotograma a la capa
> OSD (~2/s). Detalle y ficheros reconstruidos en `docs/evidencia/captura_editor.md`.


Esto es lo que queda por resolver del panel y que **solo se puede atacar desde Windows**, porque
ahí corre el editor oficial de COUGAR y se pueden espiar sus bytes por el inspector de Electron.

## Ya resuelto (no hace falta repetirlo)

- El protocolo de cable (framing, escapes, checksum, bloques, acuse) está completo y verificado.
- `GET waterBlockScreen` → **400** (el 200 de antes era una respuesta vieja).
- `mediaDelete` no responde (5 variantes probadas, silencio). Ver abajo: hay que capturar el
  comando real del editor, no adivinarlo.
- `recovery` **no borra los medios**: reinicia, apaga la OSD (`osdState` 1→0) y restaura el
  fondo anterior, pero no libera espacio. Tarda 2-8 min.
- `displayInSleep` 0 y 1: el panel **no se apaga en 12 min** en ninguno de los dos. El apagado
  real es por espera de tráfico (`timeout: 60`).
- El panel no escala: repite en mosaico. La imagen debe ser 1920x462.
- Límite de tamaño real: entre 5 y 10 MB (evidencia: `respuesta_conn_1.bin`, `g_40mb.jpg`,
  `space=40008`, `bootFinish=0`).

## Lo que falta (ordenado por valor)

### 1. Qué pone `bootFinish` en 1 (la puerta de todo) — MEDIDO: arranque ~5 min

Sin `bootFinish=1` el panel no acepta `transport` (da 400) ni registra nada. Queremos el
**ciclo exacto** que lo deja en 1.

**Qué capturar:**
- Ciclo de alimentación real (fuente, no solo USB) con la sonda `conn` corriendo cada 2 s
  (`node herramientas/windows/sondas/vigilar_panel.js` o la orden `esperar` de la sonda).
- Anotar cuánto tarda `bootFinish` en pasar de 0 a 1 y qué pasó justo antes.
- Si usas el editor: su log segundo a segundo, para ver qué manda él y en qué orden.

### 2. El comando REAL de `mediaDelete` y el listado de medios

El editor tiene `waterBlockScreen.mediaDelete(f.value[u.deleteIndex].path)`. Sabemos que
`POST/DELETE mediaDelete` con `path`/`fileName`/`name`/`value` da silencio, así que **el nombre
de cable no es ese**. Hay que espiarlo.

**Qué capturar** (con `cougar_hid_node.js` o los ganchos CDP de `herramientas/windows/sondas/`):
- Los **bytes HID exactos** que envía el editor al pulsar "borrar" un medio (y al listar).
- Lo mismo para `mediaImageAdd`, `mediaVideoCropAdd` y `mediaInfoGet`: el nombre del comando y
  la forma del cuerpo.
- Si hay un `waterBlockScreen` GET que devuelva la lista de medios, el JSON que devuelve.

Esto es lo que más me falta: **el diccionario comando-de-la-interfaz ↔ comando-de-cable**.

### 3. El formato `.osd` (qué sube el editor para la superposición)

El editor sube algo con extensión `.osd` que no es PNG. Queremos saber su formato.

**Qué capturar:**
- El `transport` del editor con su `fileSize` y `fileName` (p.ej. `algo.osd`).
- Los primeros bytes del fichero que sube (está en su directorio de trabajo temporal).
- Compararlos con el PNG origen: ¿es PNG con cabecera distinta? ¿otro contenedor?

### 4. `osdState`, `mode`, `logo`, `presetThemeId`, `sleepClockId` — qué significan en pantalla

`mode` y `logo` aceptan 200 pero **no cambian nada visible por `conn`**. Solo mirando la pantalla
se sabe qué hacen.

**Qué capturar:**
- Para `mode` 0, 1, 2 y 3 (y `logo` 0-4): qué cambia en la pantalla (¿escala? ¿mosaico? ¿capa?
  ¿reloj?). Una foto de cada uno vale oro.
- Un barrido de comandos (`presetTheme`, `sleepClock`, `customScreenMode`, ...) con lectura de
  `conn` antes/después, anotando cuál responde 200/400/silencio.

### 5. El índice del tema (`切换主题 <id>`)

El editor cambia de tema con un id que está en su `store.json`.

**Qué capturar:**
- El `store.json` del editor (lista de temas y sus ids).
- Los bytes del `transport` cuando cambias de tema con el editor.

## La función de stream y los límites de medios (preguntas del dueño)

Esto es lo que más importa a nivel de uso, y la clave es una: **el panel refresca a 60 Hz, pero
nuestro "stream" va a ~3 fps**. Eso sugiere que el panel **reproduce medios internamente** (los
guarda y los reproduce él a 60 Hz) y que lo nuestro es solo el *livestream* por USB, que va
limitado por el transporte. Hay que confirmarlo desde el editor.

### 6. ¿Por qué el stream va a 3-5 fps si el panel es 60 Hz?

Sabido: nuestro método sube **un PNG entero por fotograma** y por eso no pasa de ~3 fps
(`docs/RENDIMIENTO.md`). 60 Hz es el refresco de la pantalla, no del envío.

**Qué capturar (esto lo resuelve todo):**
- Cuando cargas un **vídeo (mp4) o un GIF** en el editor: ¿qué manda por HID? ¿un `transport`
  con el fichero entero y luego un comando de "reproducir"? ¿o sigue mandando fotogramas?
- El nombre del comando y su cuerpo (espiar con `cdp_*.js`). El editor tiene
  `waterBlockScreen.mediaVideoCropAdd(...)` y `mediaImageAdd(...)`: capturar sus bytes de cable.
- Si hay un comando tipo "play" / "mediaPlay" / "show", capturarlo.

### 7. Tamaño máximo de vídeo que soporta

Sabido: un **fichero** de ~40 MB atascó el panel (evidencia `g_40mb.jpg`); entre 5 y 10 MB es
el límite seguro. El fabricante dice 20 MB.

**Qué capturar:**
- Qué límite muestra/impone el editor para vídeo (¿20 MB? ¿otro?). Probar un vídeo justo por
  debajo y por encima, anotando el resultado.

### 8. Qué tamaño de GIF o JPG acepta

Sabido: PNG y JPEG se ven; el **GIF sale en blanco**. Un JPEG de 1024x240 se vio (repetido, por
no ser 1920x462). El límite de tamaño de fichero es el mismo: 5-10 MB.

**Qué capturar:**
- Dimensiones máximas por formato (¿rechaza algo mayor que cierta resolución?).
- Si el GIF tiene algún tamaño/duración en que **sí** se anime (hoy nos sale blanco).

### 9. ¿Solo fondo, solo OSD, o ambos?

Sabido: el panel compone **dos capas** — fondo (acumula) y OSD (superposición, reutiliza hueco).
Nosotros subimos a una u otra con `capa`.

**Qué capturar:**
- En el editor, ¿el vídeo va a fondo, a OSD, o hay que elegir? ¿`mediaImageAdd` (imagen) es
  fondo y `mediaVideoCropAdd` (vídeo) es OSD, o al revés?
- Qué campos del cuerpo (`type`, `capa`, `screenRatio`...) deciden la capa.

## Cómo me lo pasas

- Commits en el repo (como hiciste: un zip con `.git`), o los ficheros de captura.
- Las tramas crudas: **sin desescapar** (como `respuesta_conn_1.bin`), que ya sé parsearlas.
- Para lo visual: fotos de la pantalla con el número de `mode`/`logo` anotado.

## Herramientas ya listas en `herramientas/windows/`

- `sondas/cougar_hid_node.js` — hablar con el panel desde Node (la sonda).
- `sondas/cdp_*.js` — espiar al editor por el inspector de Electron (esto es lo que captura el
  punto 2).
- `dashboard/` — el primer dashboard en PowerShell.
- `analizar_log.py` — para interpretar los logs.
- El `LEEME.md` de esa carpeta explica cada pieza.
