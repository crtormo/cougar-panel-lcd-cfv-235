# PENDIENTE — mejoras que salen de comparar con la otra implementación

Comparando este kit con `cfv235` (la app de Linux del mismo panel) y midiendo en el panel real
(2026-09-14) salen ocho cosas que este kit debería hacer y no hace. Las dos implementaciones
coinciden en el protocolo byte a byte, así que todo lo de aquí es de la capa de transporte.

## 1. Autonegociar la forma de escribir (el `EINVAL` del kit viejo)

El descriptor del panel declara informes de **1024 B sin byte de report ID**
(`Report Count 0x400`, sin ítem `Report ID`), pero la API HID de Windows **exige** anteponerlo
(1025). Este kit escribe 1025 con `0x00` y ofrece `--sin-prefijo` / `--exacto` como remedio
manual. Lo correcto es **probar las variantes al abrir y quedarse con la que el panel contesta**
(como hace `cfv235.canal.autonegociar`): así desaparece el misterio del `EINVAL` de Linux.

## 2. Comprobar el espacio libre antes de subir

`space` está en `conn`. Hoy el kit sube sin mirar: si el fichero no cabe, el panel se atasca
(`bootFinish: 0`, `transport` → 400) y el único desatascador conocido es **cortar la
alimentación ~30 s**. Medido: entre 5 y 10 MB se atasca; a 10,2 MB quedó inservible un rato.

**El caso real está en la evidencia del kit**: la captura `tramas_reales/respuesta_conn_1.bin`
se tomó **durante** el atasco y dice exactamente qué lo provocó:

```
bootFinish = 0
space      = 40008 KB           <- la mitad de los ~80 MB de siempre
background = ['g_40mb.jpg']     <- un JPEG de ~40 MB
```

O sea: el panel **aceptó** el fichero, se quedó con ~40 MB y no volvió a atender órdenes. Eso
explica por qué `recovery` tardaba entre 2 y 8 minutos (no reiniciaba: digería ese fichero) y
por qué el límite real está muy por debajo de los 20 MB que anuncia el fabricante. Con un margen
del 5 % y un aviso claro se evita el único fallo irreversible que tiene esto.
La capa **fondo** acumula (~57 KB por fotograma grande) y la **OSD reutiliza** su hueco
(~4 KB por fotograma): por eso el vídeo o el bucle continuo van a OSD.

## 3. Bloqueo exclusivo por dispositivo

El HID es exclusivo pero **el panel no lo impide a la ligera**: si dos procesos escriben, las
respuestas se cruzan y todo parece "rechazar el handshake" sin motivo. Medido en directo: un
dashboard subiendo cada 5 s hacía fallar peticiones ajenas. Un `flock` sobre `/dev/hidrawN`
con el pid del que lo tiene convierte eso en un mensaje claro.

## 4. Leer el acuse de los bloques

El panel manda **`1 200 AckNumber=0`** al recibir el último bloque (medido: **uno por
transferencia**, con 28, 47 y 2 388 bloques) y `1 400` si la sesión caducó. No es obligatorio
esperarlo —subí 2,39 MB sin esperarlo y entró en 1,63 s—, pero leerlo 1,5 s con margen da un
diagnóstico inmediato en vez de un `transported` vacío.

## 5. Escrituras cortas, `EAGAIN` y desconexión

`os.write` puede escribir menos de lo pedido y, con el descriptor en `O_NONBLOCK`, lanzar
`EAGAIN`. Y `ENODEV`/`EIO`/`ESHUTDOWN` significan "el panel se está reiniciando", no un bug del
programa. Hoy el kit escribe a secas y no comprueba nada de eso.

## 6. Reintentar una vez la subida

Medido: un JPEG falló en el primer intento (`transported` agotó 8 s, el fondo no cambió) y
funcionó a la segunda (165 ms). Un reintento único, con el mismo nombre, convierte ese fallo
esporádico en invisible.

## 7. Usar el `blockMaxSize` que anuncia el panel

El kit usa 1000 fijo (que es lo que el panel anuncia hoy, así que en la práctica da igual),
pero el handshake devuelve `blockMaxSize` y quien lo ignore se romperá el día que cambie.

## 8. Esperar a que el panel arranque

Tras un Reset el panel reinicia y no contesta (`bootFinish: 0`) durante **entre ~2 y 8 minutos**,
y sigue en el USB mientras tanto. Esperar con `conn` cada pocos segundos, en vez de fallar,
evita que `bucle` y el servicio den el panel por perdido. Ojo: `recovery` **no borra** los
medios: medido con el panel real (2026-09-14), al volver **restaura el fondo que tenía
configurado de antes** y apaga la capa OSD (`osdState` 1 → 0).

## 9. Reabrir el dispositivo cuando el panel se reinicia

El descriptor se abre una vez y **muere con el reinicio**. Medido a costa de un falso negativo:
tras un `recovery`, la sonda seguía diciendo "sin respuesta" durante 10 minutos aunque el panel
ya había vuelto; lo que fallaba era el descriptor viejo. `Panel.peticion` debería, si lleva
varios intentos sin respuesta, volver a buscar el dispositivo y reabrir (una sola vez cada pocos
segundos, y avisando). Sin eso, `bucle` se queda mudo hasta que el servicio lo reinicie, y quien
mire el registro creerá que el panel no volvió.

---

## Lo que este kit hace mejor (para no perderlo al tocar lo de arriba)

- El motor de temas con **validación** (`temas.validar`) y catálogo JSON para que un editor
  construya su interfaz sin duplicar campos.
- Las **pruebas contra capturas reales** del panel y la comprobación de coherencia byte a byte
  con `referencia/cougar_panel.py`.
- El **simulador** reproduce el fallo real de la sesión caducada (`1 400 AckNumber=0` y
  `transported` con 200 y cuerpo vacío), que es lo que hace falta para depurar sin hardware.

## Correcciones ya sabidas de la documentación de este kit

- **El apagado por espera.** `cougar/panel.py` y `DESARROLLO.md` decían que con
  `displayInSleep` a 0 el panel aguanta más de 6 minutos sin tráfico. **La medida era
  falsa**: se tomó con el editor de COUGAR abierto, que mantiene el panel despierto. Con
  el editor cerrado, la pantalla se apaga **~1 minuto** después del último tráfico (el
  perfil trae `timeout: 60`). Una imagen fija necesita tráfico periódico, punto.

- `realtimeDisplay` **no** cambia `osdState` (medido: `1 → 1` con `enable: true` y `false`).
  El kit decía lo contrario en `cougar/panel.py`, en `PROTOCOLO.md` y en el LEEME.
- El valor de `len` en la respuesta de `conn` **no** es fijo: 371, 375, 377, 381 y 386 en el
  mismo panel según el contenido. Comparar ese número entre sesiones no indica nada.
- El espacio libre **no** baja de forma monótona: con la capa OSD subió de 79 344 a 81 500 KB al
  reescribir el hueco.
