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

---

## Pendiente

- **`recovery`**: los documentos del proyecto dicen que no borra los medios (medido dos veces) y
  el docstring de `cfv235/panel.py` dice lo contrario. Falta comprobarlo con el panel delante y
  unificar.
- `cfv235/simulador.py`: que `realtimeDisplay` no toque `osdState`, como el panel real.
- `docs/HALLAZGOS.md` §2 y `docs/CANAL.md` §6: `GET waterBlockScreen` da 400 en este panel.
- Comprobar en pantalla el comportamiento con imágenes que no son 1920×462 (si el panel repite
  en mosaico, como dice `docs/HALLAZGOS.md`, o si escala).
