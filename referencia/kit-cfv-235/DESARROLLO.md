# DESARROLLO — cómo construir tu editor

## 1. Las capas, de abajo arriba

```
cougar/protocolo.py     tramas, escape, checksum, parser y el informe de medios
        ↓
cougar/panel.py         Panel: abrir /dev/hidraw, enviar, leer, subir imágenes
        ↓
cougar/widgets.py       motor de dibujo: tema JSON → PNG 1920x462
cougar/fuentes.py       datos del PC (Linux por /proc y /sys; acepta los 35 nombres del editor)
cougar/sensores.py      lecturas en crudo
        ↓
cougar/temas.py         esquema, catálogo, validador, normalizar, renderizar
        ↓
cougar/editor.py        backend HTTP del editor visual (y cougar/editor.html, el front)
cougar/cli.py           herramienta de línea de comandos
cougar/simulador.py     panel falso para desarrollar sin hardware
cougar/patrones.py      patrones de prueba para calibrar la pantalla
```

Regla de oro: **nada de `cougar/panel.py` hacia arriba habla directamente con el descriptor**.
Si escribes tu editor, usa `Panel`; si quieres entender el protocolo, lee `protocolo.py`
(está comentado con lo que se midió) y `docs/PROTOCOLO.md`.

## 2. Hablar con el panel en seis líneas

```python
from cougar import Panel

with Panel() as panel:                     # busca /dev/hidraw con 1d6b:0126
    props = panel.propiedades()            # POST conn -> dict (versiones, espacio, capas…)
    panel.no_dormir()                      # que no se apague sin tráfico
    panel.subir("mi_fondo.png")            # capa de fondo
    panel.subir("mi_fondo.png", capa="osd")# capa OSD (encima, y reutiliza hueco)
    panel.telemetria({"cpu": {"temperature": 55}})   # STATE all: lo que muestra el panel
```

Y si dibujas tú la imagen, sin pasar por disco:

```python
from cougar import Panel, temas
png = temas.renderizar_datos(mi_tema)      # bytes PNG
with Panel() as panel:
    panel.subir_datos(png, "editor.png", capa="osd")
```

En un editor que dibuje con otra cosa (Qt, GTK, cairo, lo que sea) basta con entregar los
bytes: `subir_datos()` sólo comprueba que empiecen por PNG/JPEG/GIF.

## 3. El editor que ya funciona

```bash
python3 -m cougar.editor --abrir           # http://127.0.0.1:8777
```

El front (`cougar/editor.html`) es HTML y JavaScript sin dependencias: lienzo a escala del
panel, arrastrar y redimensionar widgets, elegir entre las 35 fuentes del editor, valores en
vivo y botones de Guardar / Aplicar / Bucle. El backend expone esto:

| Ruta | Cuerpo | Devuelve |
|---|---|---|
| `GET /` | — | el editor |
| `GET /estado` | — | `valores` en vivo, `tema` actual, `ruta`, `nombres_editor`, `bucle`, `ultimo`, `png` |
| `GET /catalogo` | — | tipos de widget con sus campos, fuentes, marcadores, tema de ejemplo |
| `POST /guardar` | `{"tema":…}` | guarda el JSON y devuelve los problemas que vea |
| `POST /validar` | `{"tema":…,"estricto":false}` | lista de problemas (vacía = perfecto) |
| `POST /aplicar` | `{"tema":…}` | dibuja y sube (capa OSD); `ok` falso si no hay panel |
| `POST /bucle` | `{"periodo":2}` o `{"parar":true}` | tema animado: redibuja y sube |
| `POST /tema` | `{"ruta":"/etc/mi-tema.json"}` | carga un tema de disco |
| `POST /plantilla` | — | tema de ejemplo |

`/catalogo` está pensado justo para lo que vas a hacer: si escribes tu propio interfaz, pídelo
al arrancar y construye el panel de propiedades a partir de él en lugar de duplicar la lista
de campos en el front. Así, cuando añadas un campo nuevo al motor, tu interfaz lo muestra sin
tocar nada.

Si no hay panel conectado, el editor arranca igual: se puede diseñar, guardar y validar; sólo
`/aplicar` y `/bucle` avisan del motivo.

## 4. Desarrollar sin la pantalla delante

```bash
python3 -m cougar.simulador --traza --salida /tmp/lo-que-subi.png
#   panel simulado en: /dev/pts/3
./herramientas/cougar --device /dev/pts/3 conn
./herramientas/cougar --device /dev/pts/3 subir fondo.png
```

El simulador contesta como el panel y, sobre todo, **falla como el panel**:

| Opción | Qué reproduce |
|---|---|
| `--caduca 500` | sesión de transferencia más corta de lo normal |
| `--tarde-ms 1200` | el fallo clásico: bloques tarde → `1 400 AckNumber=0` y `transported` con cuerpo vacío |
| `--retransmitir` | respuestas repetidas, como hace el panel de vez en cuando |
| `--salida f.png` | reconstruye lo que le has subido, para compararlo byte a byte |

Con eso se depura el camino de subida completo (bloques, orden, tamaños, tiempos) sin tocar el
panel. Después, siempre, pruébalo contra el panel de verdad: el simulador lo escribí leyendo el
protocolo medido, y el firmware tiene sus manías.

## 5. Las trampas del panel (esto es lo que cuesta horas)

1. **La sesión de subida caduca en menos de un segundo.** Al contestar al `transport` el panel
   abre una sesión; el editor manda el primer bloque **87 ms** después y el `transported`
   **6 ms** después del último. Por eso `subir_datos()` no mete ninguna pausa y **no** espera el
   acuse de cada bloque. Un `transported` con **200 y cuerpo vacío significa “no aceptado”**;
   con éxito devuelve `{"state":"success"}`.
2. **Hay dos capas.** Fondo (`[9]=0x02`) y OSD (`[9]=0x01`, encima). El fondo **acumula**
   memoria (~170 KB por imagen grande); la OSD **reutiliza su hueco** (~2-19 KB), así que es la
   que hay que usar para lo continuo (`bucle`, editor en vivo). Si hay capa OSD activa
   (`osdState: 1`, la deja el editor oficial al aplicar un tema) se dibuja **encima** de lo que
   subas: para dejarlo limpio, `panel.recovery()`.
3. **El panel se apaga si no recibe nada, y eso no se puede desactivar con un campo.**
   Durante un tiempo creímos lo contrario (que `displayInSleep: 0` lo mantenía encendido
   más de 6 minutos), pero aquella medida se tomó **con el editor de COUGAR abierto**, y
   mientras hay una aplicación usándolo el panel no se duerme. Cerrado el editor, la
   pantalla se apaga **~1 minuto** después del último tráfico (el perfil del panel trae
   `timeout: 60`). Consecuencia práctica: **una imagen fija necesita tráfico periódico**
   o se apaga; lo que mantiene despierto al panel es el flujo, no `displayInSleep`.
4. **Sólo un programa puede hablar con el panel a la vez.** Si tienes el editor de COUGAR
   abierto, ciérralo (y ojo: ese programa se ejecuta elevado, así que puede que haya que
   cerrarlo desde el administrador de tareas).
5. **Si el panel se queda atascado** (`conn` dice `bootFinish: 0`, `transport` contesta `400`),
   hay que **cortarle la alimentación de verdad**: apagar el PC o la caja ~30 s. Desenchufar el
   USB **no** lo apaga (se alimenta de la fuente). Después arranca solo y acepta todo.
6. **El parser tiene que cortar por el `0x5A` de cierre, nunca por el campo `len`.** `len` no
   cuenta los bytes que inserta el escape, así que la trama del cable es más larga que `len`
   cuando hay escapes (medido: `len=371`, cable 373 B). Un parser que corte en `len` se
   desincroniza y pierde la respuesta entera en silencio. Y el checksum suma **los dos bytes**
   de longitud, no el valor `len`.
7. **El `AckNumber` no siempre es `seq + 1`.** Casi siempre, pero a veces el panel contesta con
   el mismo número, y de vez en cuando manda avisos no pedidos con `AckNumber=0`
   (`1 400 AckNumber=0`). Empareja respuestas aceptando los tres casos, y no te fíes de un
   `200` con cuerpo vacío: puede ser una respuesta vieja.
8. **`STATE all` es telemetría del PC**, no del panel: es lo que el panel enseña en su propia
   capa de sistema. Si no mandas nada, el panel cree que el PC está parado.
9. **Los informes de medios no llevan checksum ni escapes** y van sin relleno: el último bloque
   se escribe corto (769 B para 744 de datos, medido), no rellenado a 1025.
10. **GIF: aceptado pero pantalla blanca.** El panel dice `{"state":"success"}` y guarda el
    fichero, pero no lo muestra. El JPEG no se ha probado. Si quieres animación, sube PNG
    fotograma a fotograma (eso es lo que hacen `bucle` y el reflejo de pantalla).

## 6. Añadir un tipo de widget

1. Escribe `def dibujar_lo_tuyo(dib, w, fuentes)` en `cougar/widgets.py` (usa `w.get(..., por_defecto)`).
2. Añádelo a `TIPOS` (acepta alias, mira cómo `clock` apunta a `reloj`).
3. Descríbelo en `CATALOGO` de `cougar/temas.py` con sus campos `(tipo, por_defecto, ayuda)`:
   así lo ven el validador, el catálogo `/catalogo` y cualquier interfaz que lo consulte.
4. Añade un caso al `casos` de `pruebas/test_temas.py` y comprueba que se dibuja.

## 7. Añadir una fuente de datos

En `cougar/fuentes.py`:

- el nombre que usa el editor oficial va en `MAPA_EDITOR` (así un tema hecho con COUGAR
  funciona tal cual);
- la lectura de Linux, en `_BackendLinux.muestra()` (por `/proc` y `/sys`, sin `sudo`);
- en Windows, `_BackendWindows` (CIM). Si un dato no existe, devuelve `None`: el motor dibuja
  `--` y el dashboard sigue funcionando.

Los marcadores `{clave}` de los widgets de texto usan esas mismas claves
(`{cpu_temp}`, `{ram_uso}`…), y también se acepta `{fuente:CPU Usage}`.

## 8. Lo que tiene el editor oficial y aquí no está

Para que no busques lo que no hay:

- **temas de vídeo (`mp4`) y la grabación de pantalla** del editor. El **reflejo de pantalla**
  sí se puede montar aquí: captura con `grim`/`scrot`/`ffmpeg`, ajusta a 1920×462 y sube con
  `subir_datos(bytes, capa="osd")`; el proyecto original trae ese módulo ya escrito
  (`widgets/stream.py`, en la carpeta `CFV235-Linux`) como referencia;
- **campos de estilo** suyos que este motor ignora: `showPrefix`, `addZero`, `rotate`,
  `maxTickCount`, `cornerRadius` (el validador los avisa);
- **importar/exportar temas en el formato propietario** del editor (nuestro tema es JSON propio);
- **paneles de 480×480 y 720×720** (este kit es para el de 1920×462).

Nada de eso está cerrado: el protocolo de subida es el mismo, sólo cambia lo que se dibuja.

## 9. Si algo no funciona

```bash
./herramientas/probar.sh              # de menos a más, sin cambiar nada
./pruebas/ejecutar.sh                 # pruebas sin panel
./herramientas/cougar --list          # ¿está el panel? (1d6b:0126)
./herramientas/cougar conn            # ¿contesta? mira bootFinish y osdState
python3 herramientas/probar_tramas.py # ¿el analizador cuadra con las tramas guardadas?
```

- **No abre el dispositivo**: permisos. `sudo`, o instala la regla udev
  (`sudo ./herramientas/instalar.sh --sin-servicio`) y añade tu usuario al grupo `plugdev`.
- **`EINVAL` al escribir**: el kernel no traga el tamaño de escritura. Prueba
  `--sin-prefijo` (sin el byte de report ID) o `--exacto` (una sola escritura, sin rellenar a
  1025). En el banco de pruebas funcionaban las tres variantes; la de por defecto es la buena.
- **Sube pero no se ve nada**: mira `osdState` y `brightness` en `conn`, y si hay restos,
  `cougar recovery` antes de subir.
- **`transported` con cuerpo vacío**: los bloques llegaron tarde. No metas pausas.
- **El panel no acepta órdenes** (`bootFinish: 0`): corta la alimentación de verdad, 30 s.
