# Captura del editor: qué manda por HID (pendientes 2, 3 y 6)

Capturado el 2026-09-14 espiando al editor de COUGAR por su inspector (`cdp_parche.js`: parche en
caliente de `node-hid` con `Debugger.setScriptSource`), en el equipo del banco de pruebas. La
captura completa queda en local (`docs/evidencia/captura_editor.txt`, 5,7 MB, 2.890 escrituras).

## Qué manda el editor, y nada más

Los **únicos** comandos que usa el editor son: `conn`, `power resume`, `STATE all`, `transport`
y `transported`. Todo lo demás (temas, widgets, vídeo) lo dibuja el PC y lo sube como PNG. El
protocolo del editor es deliberadamente mínimo: el panel solo pinta lo que le llega.

## Pendiente 2 — el comando de borrar NO existe

En toda la captura no hay ni un solo comando de borrado (ningún `DELETE`/`mediaDelete`). Borrar
un medio en el editor es **local**: quita la entrada de `store.json` y borra el fichero. El panel
no tiene comando de borrado por cable. Un barrido aparte ya había dado silencio con 5 variantes;
esto lo confirma desde el editor real.

## Pendiente 3 — el `.osd` es un PNG

Todos los `.osd` que sube el editor tienen firma PNG (`89 50 4E 47 0D 0A 1A 0A`) y son de
**1920×462** (reconstruido en `docs/evidencia/reconstruidos/primer_osd.bin`). No hay formato
propio: es un PNG con extensión `.osd`, subido a la capa OSD ~1 vez por segundo (de ~3,5 KB a
~48 KB según lo que se muestre).

## Pendiente 6 — el vídeo se sube entero y se reproduce por OSD

Al cargar un vídeo, el editor:

1. Lo **re-codifica** (mi mp4 de 33.558 B se subió como `2026-09-14_20-23-43-260.mp4` de
   63.381 B, `ftyp` mp4 válido).
2. Lo sube **entero por `transport`** (64 bloques), igual que una imagen.
3. **No hay ningún comando "play"**: el panel no reproduce el mp4 por sí solo. El editor
   decodifica el vídeo y sube cada fotograma como PNG a la capa OSD (~47 KB por fotograma,
   ~2 por segundo).

Por eso el "vídeo" en el panel va a ~3 fps aunque el panel refresque a 60 Hz: **el panel solo
muestra la última capa OSD, no reproduce nada internamente**. La reproducción a 60 Hz que sugiere
la app no existe como tal; lo que hay es el editor haciendo de reproductor y subiendo fotogramas.

## Pendiente 1 (parcial) — el saludo al reconectar

Al abrir el panel, el editor manda en este orden: `conn` → `power {"event":"resume"}` →
`STATE all {telemetría del PC}` → `transport` del fondo guardado (el último medio personalizado:
`2026-09-12_20-57-20-896.png`, 1,58 MB). El contador `[4]` de los informes de medios va
incrementando entre transferencias (0x2c, 0x2d, …), como ya se sospechaba.

## Ficheros de esta evidencia

- `captura_editor.txt` — captura completa (local, 5,7 MB).
- `captura_editor.muestra.txt` — primeras escrituras, para ver el formato de línea.
- `reconstruidos/primer_osd.bin` — un `.osd` reconstruido (PNG 1920×462).
- `reconstruidos/video_subido.mp4` — el mp4 que el editor subió (re-codificado).
