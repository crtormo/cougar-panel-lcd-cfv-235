# Verificación independiente de los hallazgos (2026-09-14)

Medido sobre el **mismo panel** (`1d6b:0126`, S/N `BYZL2611WC01CM001018`, firmware V1.0.5) desde
otro equipo, con una sonda propia escrita para esta revisión
(`herramientas/sonda-chequeos.js`), que sólo imprime la respuesta emparejada por `AckNumber` y
mide por fases. No se ha usado el código de esta app para medir, así que los números son
independientes.

Antes de empezar se detectó y **paró** un dashboard que estaba subiendo un fotograma cada 5 s
desde el arranque automático del otro equipo: las respuestas se cruzaban y las peticiones
parecían fallar sin motivo. Es la mejor justificación del `flock` que ya tiene `cfv235` y que
otros clientes no tienen.

## Lo que sale confirmado

| Afirmación | Medición |
|---|---|
| Framing: `len = payload+5`, escapes, checksum con **los dos bytes** de longitud | Coincide byte a byte con una implementación previa independiente y con las capturas |
| Acuse de bloque `1 200 AckNumber=0` | **Exacto**: 1 acuse por transferencia (28, 47 y 2 388 bloques) |
| El coste está en el cierre y manda el número de bloques | 2,39 MB (2 388 bloques): `transport` 34 ms, bloques 659 ms, **`transported` 939 ms**, total **1 632 ms** |
| Velocidad de subida | **1,46 MB/s** (ellos midieron 1,6 MB/s con 2,66 MB) |
| `realtimeDisplay` **no** cambia `osdState` | Confirmado: `1 → 1` con `enable: true` y con `false` |
| `mode` existe y guarda el valor | `{"value":0}` → 200 y `mode = 0` |
| `fanLCDSet`, `config`, `media`, `GET conn` → 400 | Confirmado los cuatro |
| `power`: solo `resume` | `resume` → 200; `restart` → **silencio**, no 400 |
| JPEG se acepta y se muestra | 46 KB → 176 ms, `background` cambia (ver el matiz de abajo) |
| El panel es 1920×462 y `degree` normal 270 | Confirmado |
| `displayInSleep` no mantiene la imagen por sí solo | Confirmado en lo práctico: hace falta flujo periódico |

## Matices y correcciones

1. **Esperar el acuse no es obligatorio.** Existe y es buen diagnóstico, pero subí 2,39 MB sin
   esperarlo y entró en 1,63 s. La frase "hay que esperar el acuse de los bloques antes de
   cerrar la subida" es más fuerte que la realidad; el propio código ya lo tolera
   (`MS_ACUSE_BLOQUES` con aviso si no llega).
2. **`GET waterBlockScreen` responde 400** en este panel, con GET y con POST. La tabla de
   `docs/HALLAZGOS.md` y `docs/CANAL.md` dice que responde 200 con el mismo JSON que `conn`.
   No se ha podido reproducir; conviene marcarlo como no confirmado o comprobar de qué sesión
   salió ese dato (un `200` emparejado con una respuesta vieja produce exactamente este error).
3. **El JPEG falló 1 de 2 veces.** El primer intento: `transported` agotó 8 s y
   `background` no cambió; el segundo, 165 ms y aceptado. Conclusión práctica: **reintentar la
   subida una vez** antes de dar un fallo por bueno.
4. **`len` de `conn` no es una constante.** Ha dado 371, 375, 377, 381 y **386** en el mismo
   panel: depende del contenido (sobre todo del nombre del fondo, que va dentro del JSON). La
   comparación "375 frente a 371" no indica diferencia de firmware.
5. **El espacio no baja de forma monótona.** Con la capa OSD pasó de 79 344 a **81 500 KB** al
   reescribir el hueco. La tendencia es la que dice el documento, pero conviene no usarlo como
   contador exacto.
6. **El apagado por espera: la medición era INVÁLIDA, y después se vio la verdad.**
   Cuatro minutos (y luego doce) sin tráfico con `displayInSleep: 0` terminaron con
   `brightness: 100`, lo que parecía refutar la frase "se apaga en menos de dos minutos".
   **Estaba mal medido**: durante esas pruebas el **editor de COUGAR estaba abierto** en el
   equipo del banco, y mientras una aplicación usa el panel, el panel no se duerme.
   Cerrado el editor, la pantalla se apagó **~1 minuto** después del último tráfico, que es
   justo lo que dice el `timeout: 60` del perfil. O sea: `docs/CANAL.md` §7.bis tiene
   razón y **el kit cfv-235 es el que se equivocaba** al prometer una imagen fija sin
   bucle. Lección de método: una medición de inactividad con otro programa al lado no
   vale, y el editor puede estar abierto sin escribir nada.
7. **`recovery` sigue sin comprobarse**: su propio `README.md` se contradecía (lo negaba arriba
   y lo afirmaba en la última línea) y la prueba deja el panel sin responder entre 2 y 8
   minutos. Queda pendiente con el panel delante.

## Correcciones pendientes en este repositorio

- `cfv235/simulador.py` (~línea 142): modela `realtimeDisplay` como si cambiara `osdState`.
  Medido, **no lo cambia**; el simulador debería imitar al panel en esto.
- `cfv235/panel.py` (~línea 194): el docstring de `recovery` dice que "borra los medios y deja
  `osdState` en 0", y `docs/HALLAZGOS.md` dice que **no** los borra. Alinear el docstring con lo
  medido.
- `README.md`, sección *Avisos*: decía que `recovery` borra todos los medios. Corregido en este
  commit para que coincida con `docs/HALLAZGOS.md`.
- `docs/HALLAZGOS.md` §2 y `docs/CANAL.md` §6: `GET waterBlockScreen` (ver matiz 2).

## Cómo se repite

```bash
node herramientas/sonda-chequeos.js conn          # estado y campos
node herramientas/sonda-chequeos.js waterblock    # GET y POST waterBlockScreen
node herramientas/sonda-chequeos.js power-restart # resume frente a restart
node herramientas/sonda-chequeos.js realtime on   # ¿cambia osdState?
node herramientas/sonda-chequeos.js esperar 4     # 4 min sin tráfico: ¿se apaga?
node herramientas/sonda-chequeos.js upload foto.png [--osd] [--repeticiones N]
```

Necesita `COUGAR_NODE_HID` apuntando a un `node-hid`. Los tiempos y los acuses salen por
pantalla; la subida informa de `transport`, bloques, `transported` y del número de acuses con
`AckNumber=0` que han llegado.
