# cfv235 — el panel COUGAR CFV235 desde Linux

Aplicación para manejar el panel LCD **COUGAR CFV235** (9,16", 1920×462, USB HID
`1d6b:0126`) desde Linux, **sin el COUGAR LCD Editor de Windows**: estado, control, subida de
imágenes, temas JSON, dashboard en vivo con las métricas del PC y app de escritorio GTK4.

Está construida sobre el kit `cfv-235-linux` que había en `~/Descargas`, pero **no es una
copia**: el canal se ha vuelto a medir sobre el panel real, se han corregido los fallos del
kit y se han resuelto varias de las incógnitas que dejaba abiertas. Todo eso está en
[`docs/CANAL.md`](docs/CANAL.md) y en [`ANALISIS.md`](../cfv235/ANALISIS.md).

---

## Puesta en marcha

```bash
# 1) permisos del panel (sudo, una sola vez) + ordenes + servicio
./herramientas/instalar.sh

# 2) comprobar todo
cfv235 doctor

# 3) un fotograma del dashboard al panel
cfv235 dashboard --una-vez

# 4) la app de escritorio
cfv235-gtk
```

Sin instalar nada, todo funciona desde la carpeta:

```bash
python3 -m cfv235 doctor
python3 -m cfv235 dashboard --periodo 2
python3 -m cfv235_gtk
```

## El dashboard: rediseñado y con perfiles

La primera versión se veía mezclada: el widget `dato` ocupa por defecto **340×300** y dibuja su
barra interna en `alto - 102`, así que las tarjetas se comían todo lo de debajo y los textos se
pisaban. El dashboard actual va sobre una **rejilla calculada** (4 columnas de 453 px, márgenes
de 24) con cada bloque en su fila:

```
y  18 .. 76    cabecera: título, reloj y fecha
y  92 .. 236   tarjeta de dato (etiqueta + valor grande)
y 252 .. 270   barra de progreso
y 282 .. 306   línea de detalle
y 316 .. 412   gráfica de CPU + red / ventiladores
y 424 .. 452   pie: arranque, procesos, carga
```

**Perfiles** (`--perfil`): `completo`, `esencial`, `graficas`, `minimo` y `presentacion`.
**Secciones activables** (`--con` / `--sin`): `titulo`, `reloj`, `cpu`, `gpu`, `ram`, `disco`,
`red`, `grafica`, `sistema`, `ventiladores`, `temperaturas`.

```bash
cfv235 perfiles                                  # ver qué hay
cfv235 dashboard --perfil esencial               # sin gráfica ni pie
cfv235 dashboard --sin red --sin sistema         # quitar secciones sueltas
cfv235 dashboard --guardar-png /tmp/dash.png     # revisarlo sin tocar el panel
```

Y una cosa que se agradece: **las secciones cuyos datos no existen en tu equipo no se pintan**.
En este PC no hay sensores de ventilador ni de placa, así que en vez de una línea de `-- rpm`
el dashboard simplemente no la muestra (con `sudo modprobe nct6775` aparecerían).

## Vídeo y animación

El panel **solo muestra PNG** (acepta JPEG y GIF como fichero, pero el GIF sale en blanco: lo
medimos). Así que «reproducir vídeo» significa **subir fotogramas PNG uno detrás de otro**, y
eso es lo que hace esta sección:

```bash
cfv235 video ver RUTA            # inspecciona la fuente sin tocar el panel
cfv235 video RUTA                # reproduce en el panel (GIF, carpeta de imágenes o vídeo)
cfv235 video RUTA --fps 4 --sin-bucle --ajuste recortar
```

| Fuente | Cómo se lee |
|---|---|
| **GIF animado** | con Pillow (`ImageSequence`), respetando la duración de cada fotograma |
| **Carpeta de imágenes** | PNG/JPG ordenados por nombre |
| **Vídeo** (mp4, mkv, webm, avi, mov) | con **GStreamer** (`decodebin` → `appsink`), que sí está instalado en este equipo |

Modos de ajuste a 1920×462: `ajustar` (letterbox, por defecto), `recortar` (centrado) y
`estirar`.

**Límite realista: 4 fotogramas por segundo** (medido: el techo físico es ~5,8 fps, porque cada
PNG de 1920×462 tarda ~160 ms en subirse y el panel solo admite una sesión). Un vídeo se ve a
saltos, sin sonido (el panel no tiene audio) y saltándose fotogramas cuando la subida no llega.
Para animaciones cortas y suaves (un logo, un reloj, un indicador) va perfecto. El GIF se lee
con Pillow y el vídeo con GStreamer (`drop=false` en el `appsink`, que si no el tubo decodifica
todo el vídeo mientras se sube un fotograma y tira el resto: se perdían 34 de 120).

## Generar contenido con otra IA

Hay cuatro prompts autocontenidos, listos para pegar en otra IA. Se generan desde el código
real, así que no pueden quedar desfasados:

| Documento | Para qué |
|---|---|
| `docs/PROMPT_FOTOS.md` | **Fotos** (imágenes fijas) de 1920×462 para dejar puestas |
| `docs/PROMPT_GIF.md` | **GIF animados**: qué fotogramas, bucle perfecto, colores planos |
| `docs/PROMPT_VIDEOS.md` | **Vídeos** (mp4 y demás) que la app convierte en fotogramas |
| `docs/PROMPT_TEMAS.md` | **Temas JSON** (los widgets del dashboard) |

Los tres primeros llevan las especificaciones del panel (resolución, aspecto, fps reales,
límites de tamaño, las dos capas y cómo aprovecharlas), un prompt para copiar, los errores
típicos y cómo subir el resultado. Se regeneran con:

```bash
python3 herramientas/generar_prompts.py        # fotos, GIF y vídeos
python3 herramientas/generar_prompt_temas.py   # temas JSON
```

## Revisar la interfaz sin mirarla

La app puede contar por texto todo lo que muestra, sin necesidad de capturas:

```bash
python3 -m cfv235_gtk --informe /tmp/ui.txt    # o `--informe -` para la salida estándar
```

Escribe cada página con sus grupos, filas, textos y **estados** (`revelado=True/False`,
`(seleccion=… )`, `(deshabilitada)`, `[imagen]`…). Es como leer la pantalla, pero en texto.

## Si un botón de «elegir fichero» no hace nada

Síntoma: se pulsa **Elegir imagen…**, **Elegir fichero…** o **Elegir carpeta…** y no se abre
ningún diálogo, sin ningún error.

La causa está en cómo GTK4 dibuja los diálogos de fichero: **no los dibuja la app, se los pide
al portal del escritorio** (`org.freedesktop.portal.FileChooser`). Para colocarlo como hijo de
la ventana, el portal necesita el *token* de activación que el escritorio le pasa a la app al
arrancarla (`XDG_ACTIVATION_TOKEN`). Si la app se lanzó desde una terminal, un servicio de
systemd o `systemd-run`, ese token no existe, el portal falla en silencio —

```
xdg-desktop-portal-gnome: Failed to associate portal window with parent window ''
```

— y el botón parece muerto.

En GTK 4.22 **no hay forma de desactivar el portal** para las API nuevas: `Gtk.FileDialog` lo
usa siempre, y `Gtk.FileChooserNative` también (probado: `GTK_USE_PORTAL=0` ya no lo evita).
Por eso la app usa **`Gtk.FileChooserDialog`**, que GTK dibuja en el propio proceso y no pasa
por ningún portal. Está marcado como obsoleto desde GTK 4.10, pero es el único que garantiza
que el diálogo se vea.

Los ficheros se filtran también por extensión, sufijo y formatos de GdkPixbuf, y el filtro por
defecto es **Todos los ficheros**, para que nunca haya uno que no se pueda elegir: la app
valida lo que se elija.

Hay dos pruebas de regresión que vigilan esto (`tests/test_regresiones.py`), porque el fallo es
silencioso y fácil de reintroducir.

## Lo que corrigió la revisión de la app

Después de tenerla funcionando se hizo una **auditoría a fondo** (código, hilos, robustez,
usabilidad y producto) y se arreglaron los fallos que encontró. Los más importantes:

**Del núcleo:**

1. **`reabrir()` olvidaba la ruta que le habías dado.** Si el panel se caía, la app se iba a
   buscar *cualquier* hidraw con el VID/PID del panel: usando el simulador (`--device
   /dev/pts/3`) acababa abriendo **el panel real**. Ahora respeta la ruta pedida y solo busca
   por VID/PID si lo que le diste era un `/dev/hidraw*` (que puede cambiar de número).
2. **Un `Panel` cerrado volvía a abrir el hidraw por detrás de su dueño**, con dos sesiones
   sobre el mismo panel (justo lo que el bloqueo existe para evitar). Ahora cerrar es cerrar:
   `Canal.asegurar_abierto()` no resucita un canal cerrado.
3. **Una trama completa que ya estaba en el buffer no se devolvía nunca**: solo se decodificaba
   tras leer bytes nuevos. Ahora se mira el buffer primero, y un escape roto **resincroniza**
   en vez de dejar el parser atascado para siempre.
4. **El emparejado por `AckNumber` aceptaba `ack=0`**, así que un acuse duplicado se leía como
   la respuesta de la petición siguiente (con un panel que retransmite, **toda subida fallaba**).
5. **Faltaba un candado de hilo**: el bloqueo del panel es entre procesos, así que dos hilos
   del mismo programa se adjudicaban respuestas cruzadas. Ahora hay un `RLock` y la subida
   completa (transporte → bloques → acuse → cierre) va bajo una sola operación.
6. **Fuga de descriptores**: si la apertura fallaba, el panel se quedaba abierto (con
   `--esperar-panel` acababa en `EMFILE`). Ahora se cierra siempre.
7. **La subida ya no lanza**: un panel que se cae a mitad devolvía una traza de Python en vez
   de un motivo; ahora `subir_datos()` cumple lo que promete su docstring.
8. **`subir_archivo()` leía el fichero entero antes de validarlo**: con uno de 400 MB eran
   400 MB de memoria para rechazarlo. Ahora comprueba tamaño y firma **antes** de leer.
9. **El control de espacio fallaba en abierto**: si no se podía leer `space`, la subida seguía
   sin comprobar nada. Ahora avisa y, si el fichero no es pequeño, aborta.
10. **La telemetría inventaba ventiladores**: sin sensores de ventilador mandaba los de
    ejemplo (`Fan AIO Pump 2566 rpm`). Ahora manda una lista vacía.
11. **`cfv235 patron` estaba roto** (`ImportError`): faltaba el módulo. Ya está, con seis
    patrones de calibración.

**De la interfaz:** la app no se recuperaba de una desconexión —ahora invalida el panel, tiene
**Reconectar** y un banner para **Esperar a que arranque**—, las órdenes decían "Brillo
aplicado" aunque el panel contestara 400, el interruptor *No dormir* no reflejaba el estado
real (el panel manda un entero), el bucle del dashboard no vigilaba el espacio, una imagen de
6000×6000 subía el consumo 106 MB (ahora se lee la cabecera y se previsualiza en miniatura),
`PanelPrestado` guardaba una referencia al panel que podía quedar obsoleta, y el bucle de
`journalctl` se disparaba en cada refresco (3-4 procesos por vuelta; ahora 1 `systemctl` y el
journal solo en Diagnóstico). Además:

- **Preferencias persistentes** (`~/.config/cfv235/config.json`): perfil y secciones del
  dashboard, periodo, capa, fps, bucle, ajuste, últimas carpetas, tamaño de ventana y última
  página. Se restauran al abrir.
- **Menú y atajos**: `Ctrl+Q`, `Ctrl+W`, `Ctrl+R`/F5, `Escape` (parar bucle/vídeo) y `Ctrl+1..6`
  para las páginas; menú con **Atajos de teclado** y **Acerca de**.
- **Mantenimiento del panel**: `recovery` (Reset) con confirmación que avisa de que **borra
  todos los medios** y de que el panel desaparece 60-80 s.
- **Notificaciones de escritorio** cuando el panel se desconecta, falla el bucle o se cae el
  servicio, con acción para reconectar.
- **Accesibilidad**: nombre accesible en los botones de solo icono (GTK4 usa
  `update_property()`, no el `set_accessible_label` de GTK3).

**Y 14 pruebas de regresión** (`tests/test_regresiones.py`) que fijan cada uno de esos fallos
para que no vuelvan. En total **92 pruebas**.

## Qué hace

| Comando | Para qué |
|---|---|
| `cfv235 doctor` | diagnóstico: descriptor HID, variante de escritura negociada, estado del panel, sensores y avisos |
| `cfv235 estado` | propiedades del panel (JSON) |
| `cfv235 dashboard` | dashboard en vivo: `--perfil completo\|esencial\|graficas\|minimo\|presentacion` y `--sin/--con SECCION` |
| `cfv235 perfiles` | lista los perfiles y las secciones que se pueden activar |
| `cfv235 video RUTA` | reproduce en el panel un GIF, una carpeta de imágenes o un vídeo (ver abajo) |
| `cfv235 subir f.png --osd` | sube un PNG/JPEG/GIF |
| `cfv235 tema t.json --subir` | dibuja un tema JSON y lo sube |
| `cfv235 patron esquinas` | patrón de calibración |
| `cfv235 brillo 100` · `girar 270` · `no-dormir` · `power` | control del panel |
| `cfv235 modo 0..3` | `mode`: comando **no documentado** que hemos encontrado (ver aviso abajo) |
| `cfv235 servicio` | estado / parar / arrancar / reiniciar el servicio del dashboard |
| `cfv235 mantener` | mantiene el panel despierto (vuelve solo a brillo 0 sin tráfico) |
| `cfv235 sondear` | sondeo del canal: descriptor + las 6 formas de escribir |
| `cfv235 simular` | panel falso en `/dev/pts/N`, para trabajar sin hardware |
| `cfv235 recovery --si` | Reset: reinicia el panel y borra los medios |

La app de escritorio (`cfv235-gtk`) tiene **nueve páginas**:

| Página | Para qué |
|---|---|
| **Estado** | el panel de un vistazo: avisos de brillo 0, `bootFinish` y capa OSD; **lo que está mostrando el panel** (vista previa); la ficha del dispositivo; las propiedades con el valor destacado; el control de brillo, rotación y «no dormir»; y **Mantenimiento** (recovery) |
| **Imagen** | sube un PNG/JPEG/GIF. **Ajusta la imagen a 1920×462** antes de subirla (si no, el panel la repite en mosaico) y avisa de cuántas veces se repetiría |
| **Temas** | los temas JSON que tengas en el disco |
| **Patrones** | los seis patrones de calibración con su miniatura, para ver o subir |
| **Editor** | escribe un tema JSON, **valida**, **previsualiza** y lo aplica; el dibujo se hace en memoria |
| **Paletas** | seis paletas de color que se aplican **al dashboard** con un clic |
| **Dashboard** | el dashboard en vivo: perfil, secciones, periodo y previsualización |
| **Vídeo** | reproduce un GIF, una carpeta de imágenes o un vídeo |
| **Diagnóstico** | descriptor HID, negociación de escritura y comando raw |

El instalador además la deja **en el menú de aplicaciones** como «Panel COUGAR CFV235», con su
icono, y como lanzador en el escritorio.

## Lo que este proyecto corrige del kit original

Medido sobre el panel real, no deducido:

1. **El informe es de 1024 bytes y no lleva byte de report ID.** El descriptor HID del panel
   declara `Report Count = 0x400` sin ningún ítem `Report ID`. El kit escribe 1025 con un
   `0x00` delante (obligatorio en la API HID de Windows, innecesario en Linux) y documenta un
   `EINVAL` como misterio. Medido: **las seis variantes funcionan**, y la correcta es la de
   1024. La app **autonegocia** y se queda con la que el panel contesta.
2. **Hay que esperar el acuse de los bloques antes de cerrar la subida.** El kit dice
   explícitamente que no lo espera; `cougarLCD.cpp` sí. Medido: el panel manda
   **`1 200 AckNumber=0`** al recibir el último bloque, y **`1 400`** si la sesión caducó.
3. **`GET waterBlockScreen` responde 200** (el kit lo daba por inexistente).
4. **El contador `[4]` del informe de medios es indiferente**: `0x13` y `0x16` funcionan los
   dos, así que la sospecha del kit de que ahí estaba el problema era infundada.
5. **`logo: 2` no es el "modo pantalla"**: no cambia ni con el Reset. El kit lo interpretaba
   como "está mostrando su logo"; es un campo fijo.
6. **El panel se apaga por espera** devolviendo `brightness: 0` (se ve negro aunque todo lo
   demás funcione). La app lo detecta, lo avisa y lo restaura.
7. **Lo que saca al panel de su estado de reposo es el Reset** (`POST recovery
   {"enable":true}`) seguido de la subida: es la secuencia que hace que se vea la imagen.
8. **La validación de espacio antes de subir**: el kit documenta que un fichero mayor que la
   memoria del panel **lo deja atascado** y que recuperarlo exige cortarle la corriente. Esta
   app comprueba `space` y aborta con un mensaje claro.
9. **Los fallos del motor de dibujo ya no se silencian**: el kit se tragaba las excepciones
   de cada widget, así que un widget podía desaparecer y las pruebas seguían en verde.
10. **El servicio del dashboard corre como tu usuario**, no como root, y no escribe en rutas
    predecibles de `/tmp` (el kit lo hacía como root en `/tmp/cougar-dashboard.png`).
11. **Bloqueo exclusivo del panel.** El panel admite una sola sesión: si dos procesos le
    escriben a la vez, las respuestas se cruzan y todo parece "rechazar el handshake" sin
    motivo (es la trampa que el kit documenta). La app toma un `flock` por dispositivo, así
    que el segundo proceso recibe un mensaje claro —`otro proceso está usando /dev/hidraw1
    (pid N)`— en vez de fallar de forma misteriosa.
12. **Un comando que el kit no conocía: `mode`.** Barriendo los nombres de la API interna del
    editor apareció `POST mode {"value":N}`: responde **200** y el panel reporta el valor
    (0..3). No cambia `logo`, `osdState`, `background` ni `brightness`, así que su efecto
    visible sigue sin determinar, pero ya se puede fijar y leer.
13. **Y un bug del firmware que conviene conocer**: con un cuerpo que no lleve `value` entero
    (`{"mode":1}`, `{"enable":true}`, `{"value":"1"}`) el panel contesta 200 pero deja `mode`
    en **2147483647** — un campo sin inicializar. `cfv235 modo` solo acepta enteros 0..3
    precisamente para que no se provoque por accidente.
14. **El kit se equivocaba con el apagado por espera.** Afirma que con `displayInSleep: 0` el
    panel aguanta más de 6 minutos sin tráfico y que por eso una imagen fija no necesita bucle
    de mantenimiento. Medido (8 minutos sin escribirle nada, con `displayInSleep: 0` y
    `bootFinish: 1`): **el panel se apaga en menos de 2 minutos**. Hace falta **flujo
    periódico**; por eso existen `cfv235 mantener` y la telemetría entre fotogramas del
    dashboard. Detalle en [`docs/CANAL.md` §7.bis](docs/CANAL.md).

### Datos medidos del dashboard en vivo

Con el dashboard por defecto (54 KB de PNG, 55 bloques, capa OSD):

| Medida | Valor |
|---|---|
| Tiempo por fotograma (dibujar + subir) | ~160 ms (el primero ~440 ms) |
| Consumo de memoria del panel | **~4 KB por fotograma** (la capa OSD reutiliza su hueco) |
| CPU del servicio | **~1,9 % de un núcleo** |
| Memoria del servicio | ~19 MB |
| Espacio libre del panel | 81 700 KB de partida |
| Sensores por muestra | 2-4 ms |

A ~4 KB por fotograma cada 2 s, el panel tardaría decenas de horas en llenarse, y el bucle
además se detiene solo si el espacio baja de 20 MB. Si se usara la **capa de fondo** en lugar
de la OSD, cada fotograma ocuparía su tamaño completo (54 KB), que es lo que hacía el kit.

### El servicio de dashboard

```bash
systemctl --user status cfv235-dashboard     # ¿está vivo?
journalctl --user -u cfv235-dashboard -f     # ver los fotogramas
systemctl --user stop cfv235-dashboard       # pararlo (hace falta para usar la GUI o el CLI)
```

**Ojo**: mientras el servicio está en marcha, tiene el panel tomado. Para usar la app de
escritorio o el CLI, páralo primero (`systemctl --user stop cfv235-dashboard`), o usa el botón
que la propia app ofrece cuando detecta que el panel está ocupado.

### El bucle de reinicios (encontrado en la prueba real, y arreglado por los dos lados)

Con `Restart=always`, si el dashboard no consigue abrir el panel **muere y systemd lo reinicia
cada pocos segundos**, en bucle (`status=4/NOPERMISSION`). Pasó de verdad: al pulsar
*Reiniciar* en la app, la app le robaba el bloqueo del panel en la ventana de arranque del
servicio. Arreglado en dos sitios, porque son dos causas distintas:

1. **En la app** (`cfv235_gtk`): antes de arrancar o reiniciar el servicio, **cierra su propio
   panel** (traspaso en los dos sentidos) y después no reintenta abrirlo; además, si ya tiene
   el panel, no arranca el bucle de dashboard en vivo.
2. **En el servicio** (`--esperar-panel 3600`): si otro programa tiene el panel, el dashboard
   **espera** a que lo suelte en vez de morir. Así, cuando cierras la app o el editor de
   COUGAR, el dashboard vuelve solo y no hay bucle.


Y un fallo que era del propio kit y está corregido aquí: `esperar-y-subir.sh` buscaba
`"bootFinish":1`, que **nunca** coincide con la salida real `"bootFinish": 1`, así que ese
servicio no subía nada jamás.

## Cómo está hecho

```
cfv235/
  protocolo.py   tramas, escape, checksum, informes de medios (sin E/S)
  canal.py       /dev/hidraw: autonegociación del tamaño de informe, envío y lectura
  panel.py       el panel como objeto: estado, órdenes y subida con acuse y control de espacio
  sensores.py    31 métricas del PC por /proc y /sys (sin dependencias)
  widgets.py     motor de dibujo: 5 tipos de widget y sus alias del editor
  temas.py       esquema, catálogo, validador y render de los temas JSON
  dashboard.py   el dashboard en vivo (es un tema JSON: se puede editar)
  simulador.py   panel falso por PTY, con los mismos modos de fallo
  cli.py         la herramienta de línea de órdenes
cfv235_gtk/      aplicación de escritorio (GTK4 + libadwaita)
udev/            regla de permisos (ATTRS + uaccess, sin MODE 0666)
systemd/         unidad de usuario para el dashboard
tests/           pruebas de protocolo, canal, subida y temas
docs/CANAL.md    el canal medido: descriptor, variantes, acuses, comandos, estados
```

## Pruebas

```bash
./tests/ejecutar.sh              # todo (77 pruebas)
./tests/ejecutar.sh video        # un conjunto: protocolo | simulador | temas | video
```

- **Protocolo** (22): ida y vuelta del framing, escapes, las dos reglas de checksum, el parser
  que no se desincroniza con escapes, los informes de medios, la validación de imágenes y
  nombres… y el contraste con **las 8 tramas documentadas y las 2 capturas reales del kit**.
- **Canal y subida** (12, contra el simulador): autonegociación, comandos, acuse `200`,
  reconstrucción byte a byte del fichero subido, fallo por caducidad con acuse `400` y
  `transported` con cuerpo vacío, que no se sube si no cabe en el panel, **subidas grandes**
  (varios cientos de KB, mucho más que el buffer del PTY) sin perder escrituras y el bloqueo
  exclusivo del panel (reentrante dentro de un proceso, exclusivo entre procesos).
- **Temas** (13): validador y dibujo, que un color inválido no aborte el render, que los fallos
  de widget queden registrados, que `renderizar_datos` no deje temporales y funcione desde
  varios hilos, y que los temas del kit se sigan dibujando.
- **Vídeo** (30): carga de GIF, secuencia y vídeo, ajustes a 1920×462, el bucle con
  `threading.Event`, saltar fotogramas sin colgarse, y una subida completa contra el simulador
  con el fotograma reconstruido.

> Nota técnica: el descriptor se abre en `O_NONBLOCK`, así que `os.write` puede devolver
> `EAGAIN` o una escritura corta. `canal._escribir()` lo reintenta esperando con `select` a
> que el descriptor acepte datos; sin eso, con el PTY del simulador (buffer pequeño) las
> subidas grandes fallaban de forma intermitente. Hay una prueba específica para eso.

## Lo que no está

- **Vídeo (`mp4`) y grabación de pantalla** del editor oficial: fuera de alcance.
- **Listar o borrar medios sueltos**: este firmware no tiene `mediaDelete` ni un listado (lo
  hemos vuelto a comprobar). La única limpieza es `recovery`, que borra todo.
- **Reproducir el aspecto exacto del editor oficial**: él dibuja en un `webview` de Electron
  con sus tipografías; aquí se dibuja con Pillow y DejaVu.
- **Ventiladores y temperatura de placa**: en este equipo el módulo `nct6775` no está cargado,
  así que el kernel no expone esos sensores. Con `sudo modprobe nct6775` aparecerían
  (`cpu_vent`, `bomba_vent`, `chipset_temp`); el código ya los lee si existen.

## Avisos

- **Solo un programa puede hablar con el panel a la vez.** Si abres el editor de COUGAR, para
  el dashboard (`systemctl --user stop cfv235-dashboard`), o los dos se romperán el handshake.
- **No subas ficheros que no quepan**: la app lo comprueba, pero si fuerzas con `--forzar` y
  el panel se atasca (`bootFinish: 0`, `transport` → 400), el único desatascador conocido es
  **cortarle la alimentación de verdad** (~30 s); desenchufar el USB no basta.
- `cfv235 recovery` **reinicia el panel y NO borra los medios** (medido dos veces: ver `docs/HALLAZGOS.md`). El unico desatascador de verdad sigue siendo cortar la alimentacion.
