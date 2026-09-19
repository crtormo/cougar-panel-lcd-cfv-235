# Diario del proyecto

Aquí se van apuntando **los avances de los dos lados**: lo que se mide y se cambia desde el
equipo Linux (donde vive el panel) y lo que se mide desde el banco de pruebas de Windows. La
idea no es sustituir a los documentos —`docs/CANAL.md`, `docs/HALLAZGOS.md`,
`docs/RENDIMIENTO.md` y `docs/VERIFICACION_INDEPENDIENTE.md` son las referencias— sino dejar
constancia de **cómo y cuándo** se averiguó cada cosa, que es lo que se pierde con el tiempo.

## Cómo se mantiene

1. **Una entrada por sesión de trabajo**, la más reciente arriba, con: fecha, equipo, qué se
   hizo, qué se midió y **dónde quedó la evidencia**.
2. **La evidencia cruda viaja con el proyecto**, en `docs/evidencia/` (capturas de hidraw,
   sondeos del canal, resultados de mediciones). Un hallazgo sin evidencia guardada no cuenta.
3. **Nada se borra.** Si algo se corrige, se añade una entrada nueva que lo dice; las viejas se
   quedan como historia.
4. **Un hallazgo que contradiga a un documento se arregla en los dos sitios**: en el documento
   y con una entrada aquí.
5. Las herramientas de cada lado: en Linux, `cfv235` y `herramientas/*.py`; en Windows, la sonda
   y el gancho del inspector, en `herramientas/windows/`.

---

## 2026-09-14 — Windows (banco de pruebas)

Sesión de verificación independiente del panel `BYZL2611WC01CM001018` (firmware V1.0.5) con una
sonda propia (`herramientas/sonda-chequeos.js`), sin usar el código del proyecto para medir.
Todo el detalle en `docs/VERIFICACION_INDEPENDIENTE.md`; el resumen:

- **Velocidad de subida: 2,39 MB en 1.632 ms (1,46 MB/s)**, con el desglose por fases:
  `transport` 34 ms, bloques 659 ms, `transported` 939 ms. Confirma lo que dice
  `docs/RENDIMIENTO.md`: el coste está en el cierre y manda el número de bloques.
- **Un acuse `1 200 AckNumber=0` por transferencia** (con 28, 47 y 2 388 bloques). Existe, pero
  **no es obligatorio esperarlo**: la subida de 2,39 MB entró sin leerlo.
- **`realtimeDisplay` no cambia `osdState`** (medido: `1 → 1` con `enable: true` y `false`). El
  `cfv235/simulador.py` lo modela al revés.
- **`GET waterBlockScreen` da 400** aquí (también con POST), no 200 como dice `HALLAZGOS.md`.
- **El JPEG falló 1 de 2 veces** (un intento con `transported` agotando 8 s): conviene reintentar
  una vez.
- **El `len` de `conn` no es constante**: 371, 375, 377, 381 y 386 en el mismo panel, según el
  contenido (el nombre del fondo va dentro del JSON).
- **El espacio no baja de forma monótona**: con la capa OSD subió de 79 344 a 81 500 KB al
  reescribir el hueco.
- **No se reprodujo el apagado por espera**: 4 minutos sin tráfico con `displayInSleep: 0`
  acabaron con `brightness: 100`; con `1`, igual.
- Detalle de método que vale la pena recordar: había un **dashboard subiendo un fotograma cada
  5 s** desde el arranque automático del otro equipo y las respuestas se cruzaban. Es la mejor
  justificación del `flock` que ya tiene `cfv235.canal`.

### Hallazgo: el atasco original, identificado en la evidencia

Analizando la evidencia que ya estaba en el repositorio
(`herramientas/analizar_tramas.py docs/evidencia/tramas_reales/respuesta_conn_1.bin`) aparece el
estado del panel en el momento en que se atascó:

```
bootFinish = 0          <- el panel no ha terminado de arrancar: no atiende ordenes
space      = 40008 KB   <- la mitad de los ~80 MB habituales
background = ['g_40mb.jpg']
```

Es decir: **un JPEG llamado `g_40mb.jpg`**. El panel lo aceptó, se quedó con ~40 MB de memoria y
`bootFinish` a 0. Eso explica las dos cosas raras de las que hablaba el proyecto: por qué el
límite real está muy por debajo de los 20 MB que anuncia el fabricante —de ahí el recorte a
8 MB— y por qué `recovery` tardaba entre 2 y 8 minutos: no estaba "reiniciando", estaba
digeriendo ese fichero. **Se recupera solo, pero tarda; y desenchufar el USB no sirve** porque
el panel se alimenta de la fuente.

## 2026-09-14 — Linux (equipo del panel)

Los ocho commits de esta jornada, tal como quedaron:

| Commit | Asunto |
|---|---|
| `380be53c` | Resumen de hallazgos y correcciones en el documento del canal |
| `8b058ed3` | Corregir `no_dormir`: enviaba el valor invertido |
| `629dc50c` | Documentar que `recovery` no borra los medios y tarda hasta 8 minutos |
| `ede80273` | Bajar el límite de imagen a 8 MB: el panel se atasca con 10 MB |
| `52bf9831` | Documentar las nueve páginas de la app |
| `6e552eb5` | Ajustar la imagen al panel y arreglar el diálogo de ficheros |
| `6f7ff212` | Registrar al arrancar qué diálogo de ficheros se usa |
| `93643561` | Versión inicial: app de escritorio y CLI para el panel COUGAR |

## 2026-09-14 — Windows (aportes al repositorio)

- `docs/VERIFICACION_INDEPENDIENTE.md`: las mediciones de arriba, con los matices y las
  correcciones pendientes.
- `docs/evidencia/tramas_reales/`: **la evidencia cruda pasa a viajar con el proyecto** (las dos
  capturas de `conn` y el fichero de tramas documentadas). Con eso, `tests/test_protocolo.py`
  deja de saltarse las pruebas contra tramas reales cuando no encuentra el kit cfv-235.
- `herramientas/analizar_tramas.py`: analiza capturas (`.bin` o texto con hex) contra
  `cfv235.protocolo`. Fue lo que destapó lo de `g_40mb.jpg`.
- `herramientas/sonda-chequeos.js`: la sonda con la que se midió (Windows/node-hid).
- `herramientas/windows/`: el gancho que espía al editor de COUGAR por su inspector de Electron
  y el procedimiento completo (`capturar_en_windows.md`). Es de donde salió el formato real del
  informe de bloque y los tiempos de 87 ms / 6 ms.

### El Reset (`recovery`), medido

Un `recovery {"enable":true}` contestó **200** y el panel dejó de responder a `conn`. Antes y
después:

| | Antes | Después |
|---|---|---|
| `space` | 81 080 KB | **80 036 KB** |
| `background` | `panel_cfv235.png` | **`2026-09-13_02-53-10-086.png`** |
| `osdState` | 1 | **0** |
| `displayInSleep` | 1 | 0 |
| `bootFinish` | 1 | 1 |

Conclusiones, que zanjan la contradicción entre `HALLAZGOS.md` y el docstring de `panel.py`:

1. **El Reset no borra ni deja el fondo igual: vuelve a poner el medio que el panel tenía
   configurado antes.** Ese `2026-09-13_02-53-10-086.png` con marca de tiempo es del editor
   oficial: es el fondo que había de antes, no el que habíamos subido. Por eso los dos
   documentos tenían razón a medias: quien miraba el fondo justo después veía "lo mismo" (el
   configurado) y quien esperaba verlo vacío, no.
2. **Sí apaga la capa OSD**: `osdState` pasó de 1 a 0. Es la forma de dejar la pantalla limpia
   cuando hay restos de una capa encima, y es la secuencia que el propio README describe
   ("Reset y después subir").
3. **El espacio baja** (~1 MB en este caso): no libera memoria.

**Aviso de método.** La *duración* del reinicio **no quedó bien medida**: la sonda mantiene
abierto el descriptor del dispositivo desde que arranca, y al reiniciarse el panel ese
descriptor queda muerto, así que seguía diciendo "sin respuesta" aunque el panel ya hubiera
vuelto. Es exactamente el caso que `cfv235.canal` resuelve con `reabrir()` y que la sonda todavía
no hace. Las cifras de arriba sí valen, porque se leyeron con un proceso nuevo después.

### El mosaico, confirmado en pantalla

Con la capa OSD apagada (`osdState: 0`, después del Reset) se subió un JPEG de **1024×240** con
cuatro cuadrantes numerados y **se ve repetido en cuadrícula 2×2, con la última copia cortada**.
Confirma `HALLAZGOS.md` §3.2: el panel **no escala**, repite. La app hace lo correcto ajustando
la imagen a 1920×462 antes de subir (`ajustar_imagen` en `cfv235/config.py`).

### Documentos que se han traído del banco de pruebas

- `docs/INGENIERIA_INVERSA.md`, `docs/FUNCIONES_DEL_EDITOR.md`, `docs/API_DEL_EDITOR.md` y las
  dos revisiones de otras implementaciones (`cougarLCD.cpp` y `poseidon-linux-display`).
- `docs/LEEME.md`: índice de la documentación, diciendo qué contesta cada documento y de qué
  lado viene.

### Reintento único en la subida

La subida falla de vez en cuando sin motivo aparente: medido, un JPEG que agotó los 8 s de
`transported` sin cambiar el fondo entró a la **segunda** intentona en 165 ms. `Panel.subir_datos`
reintenta ahora **una vez**, con el mismo nombre y los mismos bytes, y lo cuenta en
`ResultadoSubida.reintentos` (con un aviso si hizo falta). No se reintenta lo que no puede
mejorar —no cabe, fichero vacío, no es una imagen, nombre inválido, no se puede leer—, porque
eso sería perder el tiempo dos veces. `reintentos=0` deja el comportamiento de antes.

Tres pruebas nuevas en `tests/test_simulador.py` (clase `TestReintento`): con la sesión siempre
caducada se intenta dos veces, con un fichero que no es imagen se intenta una, y con
`reintentos=0` también una. **Estas tres necesitan Linux** (el simulador va por PTY), así que en
el banco de pruebas sólo se ha podido comprobar que el módulo compila y que la lista de motivos
filtra como debe.

### La prueba de inactividad (12 minutos sin tráfico)

Con `displayInSleep: 0` y doce minutos sin escribirle nada al panel: **`brightness: 100`, el panel
siguió encendido**. Refuta lo que dice `docs/CANAL.md` §7.bis ("con 0 se apaga en menos de dos
minutos") y pone en duda el arreglo de `no_dormir` del commit `8b058ed3`, que manda
`{"enable": true}` para "no dormir" (campo a 1). En marcha la prueba complementaria con el campo a
1, otros doce minutos: si tampoco se apaga, `displayInSleep` no es lo que controla el apagado en
este panel y habrá que buscar el mecanismo de verdad (¿el medio que está en pantalla? ¿`timeout`?).

### El editor abierto invalidaba las pruebas de inactividad

Detalle que llegó después y que hay que dejar por escrito: durante las pruebas de
inactividad (la de 4 minutos y la de 12) **el editor de COUGAR estaba abierto** en el
equipo del banco. Cerrado el editor, la pantalla del panel se apagó **~1 minuto** después
del último tráfico, que coincide con el `timeout: 60` del perfil. Es decir:

- lo que mantiene despierto al panel es **el tráfico** (o tener una aplicación usándolo);
- `displayInSleep` no lo mantiene encendido, y su efecto real sigue sin determinar;
- la frase de `docs/CANAL.md` §7.bis ("se apaga en menos de dos minutos") es la correcta, y
  **el kit cfv-235 es el que se equivocaba** al prometer una imagen fija sin bucle;
- y de paso, la lección de método: **una medición de inactividad con otro programa al
  lado no vale**. Es la misma trampa que el `flock` resuelve al escribir, pero se cuela
  por la puerta de atrás, porque el editor puede estar abierto sin escribir nada.

### El apagado, resuelto mirando la pantalla (18:2x)

Con el editor de COUGAR cerrado, la pantalla del panel se apagó **~1 minuto** después
del último tráfico. Es la observación directa del dueño del panel, y encaja con el
`timeout: 60` del perfil. Y trae una segunda lección de método:

**`brightness` no sirve para saber si el panel está apagado.** Justo después, una
consulta a `conn` tardó **324 ms** (las siguientes, 25 ms) y devolvió `brightness: 100`:
es decir, **la propia consulta lo despertó**. Como cualquier comprobación por protocolo
es tráfico, el campo siempre llega tarde; la única prueba buena del apagado es **mirar
la pantalla**.

Estado final de esta duda: el panel se apaga solo en ~1 minuto sin tráfico, y mientras
una aplicación lo esté usando no se duerme. `displayInSleep` no lo gobierna (su efecto
real sigue sin determinar, y ya no importa para nada práctico).

### Todo el material del banco de pruebas, en el repositorio

- `referencia/kit-cfv-235/`: el kit completo (60 ficheros), como segunda implementación del
  protocolo, con su motor de temas, su simulador y sus patrones de calibración.
- `herramientas/windows/sondas/`: la sonda en Node (36 KB) y los scripts del inspector.
- `herramientas/windows/dashboard/`: el primer dashboard, en PowerShell.
- `herramientas/windows/analizar_log.py` y el `LEEME.md` de la carpeta, reescrito.
- `docs/evidencia/capturas_LEEME.md`: la historia de las capturas que se perdieron.
- `referencia/LEEME.md`: qué es cada cosa y por qué no se debe mezclar con `cfv235/`.

El proyecto pasa de 115 a **190 ficheros**: todo lo que se sabe del panel está ya en el
repositorio, con su evidencia y su historia.

### Correcciones aplicadas en esta sesión

- `cfv235/simulador.py`: `realtimeDisplay` ya **no** cambia `osdState`, como el panel real. Lo
  que lo enciende es subir un medio a la capa OSD.
- `tests/test_simulador.py`: la prueba comprobaba lo contrario; actualizada con la medición.
- `cfv235/panel.py`: el docstring de `recovery` decía que borra los medios, en contra de
  `docs/HALLAZGOS.md`. Ahora dice lo medido y lo que sigue pendiente de comprobar.
- `docs/HALLAZGOS.md` §2 y `docs/CANAL.md` §6: `GET waterBlockScreen` queda marcado como **sin
  confirmar** (desde otro equipo dio 400 las dos veces, con GET y con POST).
- `herramientas/sonda-chequeos.js`: se le añade la orden `recovery`, que manda el Reset y sondea
  `conn` cada 10 s diciendo cuánto tarda y si cambia el espacio, el fondo o `osdState`, y ahora
  **reabre el dispositivo** cuando el panel se reinicia (con el reinicio muere el descriptor, y
  sin reabrir parecía que el panel no volvía).

---

## 2026-09-14 — Windows: captura del editor (segunda ronda)

Espiando al editor por el inspector (`cdp_parche.js`), con el panel delante:

- Los únicos comandos del editor son `conn`, `power resume`, `STATE all`, `transport` y
  `transported`: el PC lo dibuja todo y lo sube como PNG.
- El `.osd` es un **PNG 1920×462** con otra extensión (reconstruido y verificado). Pendiente 3.
- El vídeo se **re-codifica** y se sube entero por `transport`; no hay comando «play»: el
  editor decodifica y sube cada fotograma a la capa OSD (~47 KB, ~2/s). El panel NO reproduce
  nada internamente. Pendiente 6.
- Borrar un medio es local (`store.json` + fichero): no se manda nada al panel. Pendiente 2.
- Saludo al reconectar: `conn` → `power resume` → `STATE all` → sube el fondo guardado.
  Pendiente 1 (parcial).

Evidencia en `docs/evidencia/` (`captura_editor.md` + `reconstruidos/`); captura completa
local (5,7 MB).

## 2026-09-14 — Windows: ciclo de alimentación (MEDICIÓN ANULADA)

⚠️ Esta medición es inválida: se desconectó el **USB**, no la **fuente**. El panel
(alimentado por la fuente) siguió encendido y reproduciendo el vídeo todo el rato, así
que no hubo arranque. El «sin respuesta» de la sonda era el USB desenchufado, no el panel
arrancando. La observación del dueño es concluyente: al reconectar el USB el panel
respondió al momento y siguió con el vídeo.

Lección (que ya estaba en la documentación y se volvió a aprender): **desenchufar el USB
NO apaga el panel**. El pendiente #1 sigue abierto: para medir el arranque real hay que
cortar la FUENTE de alimentación ~30 s.


Corte de corriente real (~30 s) con la sonda vigilando `conn` cada 2 s:

- Durante el arranque el panel **NO responde a `conn`** (ni 200 con `bootFinish: 0`, ni
  nada): el sondeo estuvo ~4,5 min en «sin respuesta» y el panel apareció **ya con
  `bootFinish: 1`**. Es decir, el estado «responde con 0» no es del arranque, es del
  **atasco por memoria** (`g_40mb.jpg`); son dos cosas distintas.
- Tiempo de arranque: **≥ 4,5 min**, coherente con los 2-8 min ya apuntados. (La medida
  exacta quedó cortada porque se interrumpió el sondeo antes del 1; repetible.)
- Al volver, el panel restauró como fondo el último medio que adoptó el editor:
  `2026-09-14_20-23-43-260.mp4` (el vídeo), con `osdState: 1` y `space` ~81,7 MB.

Conclusión práctica: tras un corte hay que **esperar ~5 min sin tocar nada**; el panel no
dice «estoy arrancando», simplemente no contesta hasta que está listo.

### Observación del dueño: desenchufar el USB SÍ apaga este panel

Corrección importante, con observación directa: al desenchufar el USB, este panel **se
apagó**, y al reconectarlo **se encendió y siguió con el vídeo**. O sea, en este panel el
USB aporta la alimentación (o al menos su corte produce un reinicio), contra la nota antigua
de «se alimenta de la fuente». Hay que revisar esa nota en CANAL.md/HALLAZGOS.md.

Y separa dos fases del arranque que no son lo mismo:
- la **pantalla/medio** vuelve rápido (segundos), que es lo que se ve a simple vista;
- el **control (`conn`/`bootFinish`)** puede tardar más, que es lo que la sonda mide.

Para no volver a confundirlas, `herramientas/windows/sondas/vigilar_boot.js` distingue los
tres estados: USB ausente / presente pero mudo / bootFinish=0 o 1.

## 2026-09-14 — Windows: ciclo de alimentación (medida real)

Medido con `vigilar_boot.js` corriendo en la consola del dueño (mi entorno no mantiene
procesos entre turnos). Resultado del ciclo completo:

```
t=14s  USB ausente (corte detectado)
t=14s -> t=50s  USB ausente (~36 s desenchufado)
t=52s  USB reaparecido -> conn 200, bootFinish=1  (0 s de retraso)
```

**Conclusión del pendiente #1:** en un corte/reconexión normal **no existe el estado
`bootFinish=0`** — el panel reaparece en el USB ya listo. Por tanto `bootFinish=0` es
síntoma de **atasco** (memoria llena u otro), no del arranque. La pantalla/medio puede
tardar un poco más en mostrarse, pero el control responde al instante.

### Pendiente 4 resuelto: mode y logo no tienen efecto visible

Con un patrón de colores 1920×462 visible en el fondo (y una OSD encima) se recorrió
`mode` 0→1→2→3 y `logo` 0→4: todos responden 200 y el panel los acepta (`mode` cambia el
campo en `conn`, `logo` ni eso), pero **la pantalla no cambió en ningún caso** (confirmado
por el dueño). Son campos sin efecto visible en este panel: probablemente reservados o de
otras variantes del firmware (p. ej. los paneles 480×480). Pendiente cerrado.

### Stream: reflejo de pantalla en vivo (fuente nueva)

Se añade `cfv235 stream` (y `video pantalla`): `FuentePantalla` captura el escritorio con
el portal XDG y lo sirve fotograma a fotograma (nunca se acaba); el `Reproductor` lo sube
a la capa OSD con nombre fijo, igual que el resto. `herramientas/capturar_pantalla.py` gana
`capturar_bytes()` (bytes en memoria, sin mensajes por fotograma). 3 pruebas nuevas con
capturador inyectado (corren sin portal).

Pendiente de probar en Linux: la captura REAL por el portal y el ritmo real del stream. En
el banco solo se compila y se prueba el motor con captura simulada.

### Pendiente 9 resuelto: la capa la decide el tipo de medio (tres valores)

Releyendo la captura del editor, el byte [9] de cada bloque de medios codifica la capa, y
hay TRES valores, no dos:

| byte [9] | capa | ejemplo |
|---|---|---|
| `0x02` | fondo | el PNG de 1,58 MB |
| `0x01` | OSD | todos los `.osd` |
| `0x00` | **video** | el `.mp4` que sube el editor |

El `transport` no lleva un campo de capa: el tipo va en cada bloque. El contador [4]
tambien varia por tipo (0x13 fondo, 0x16 OSD, 0x0f video).

**NUEVO por investigar:** que hace el `0x00`. Si el panel reproduce el video internamente,
se podria subir un mp4 como `0x00` y olvidarse del stream OSD a 3 fps: el panel lo
reproduciria el solo a 60 Hz.

## Pendiente

- ~~La semántica de `displayInSleep`~~ **cerrado (parcialmente)**: con `1` el panel **tampoco**
  se apaga en doce minutos de silencio (medido por la latencia del primer `conn`: 15 ms, igual que
  en ráfaga). Ninguno de los dos valores provoca el apagado en estas pruebas. Queda por resolver
  **qué** gobierna el apagado: la observación directa del dueño fue ~1 minuto con el editor
  cerrado, que encaja con `timeout: 60` — así que el apagado es por **espera de tráfico**, no por
  `displayInSleep`. Mientras no se cierre, la regla práctica sigue siendo: **hay que mandar tráfico
  periódico** para tener la imagen puesta.
- **Mirar la pantalla** cuando toque decidir algo visual: el mosaico se confirmó así, y el brillo
  aparente es la única prueba de si está apagado de verdad.
