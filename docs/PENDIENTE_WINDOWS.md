# Pendiente para Windows (y qué capturar)

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

### 1. Qué pone `bootFinish` en 1 (la puerta de todo)

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
