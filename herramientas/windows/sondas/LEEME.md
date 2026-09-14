# `sondas/` — herramientas para Windows (Node)

El cliente **Python** (`cougar_panel.py`, en la carpeta de arriba) es para **Linux**: abre
`/dev/hidrawN`. En Windows no existe ese nodo, así que ahí se usa un cliente **Node** con
`node-hid`, que es lo que se ha empleado para todo el trabajo contra el panel desde este PC.

## Requisito

`node-hid`. Instálalo con `npm install node-hid`, o apunta a una copia ya extraída:

```powershell
$env:COUGAR_NODE_HID = "C:\ruta\a\nodehid.js"
```

## `cougar_hid_node.js`

Mismo modelo de trama verificado que la versión Python: informe de 1025 B (ID `0x00` +
1024), escape `5A→5B01` / `5B→5B02` (también en los bytes de longitud y el checksum),
`len = payload+5`, `checksum = (byte_alto + byte_bajo + suma(payload)) & 0xFF`, y el fin de
trama se detecta por el `0x5A` de cierre.

```powershell
node cougar_hid_node.js estado                 # POST conn: propiedades y bootFinish
node cougar_hid_node.js power                  # despertar
node cougar_hid_node.js recovery [serial]      # intento de recuperacion
node cougar_hid_node.js raw POST transport '{"type":"media","fileSize":3744,"fileName":"x.png"}'
node cougar_hid_node.js listen 30              # escuchar 30 s
```

Variables: `COUGAR_ESPERA` (ms de espera por respuesta, por defecto 6000) y `COUGAR_DEBUG=1`
(muestra los bytes escritos y recibidos).

## `vigilar_panel.js`

Manda `POST conn` cada 3 s, detecta el corte y reaparición del USB y **avisa en cuanto el
panel arranca** (`bootFinish: 1`), que es el requisito para que el editor lo registre y para
que acepte subidas.

```powershell
node vigilar_panel.js 6      # vigila 6 minutos
```

## Errores que ya nos costaron tiempo (para no repetirlos)

1. **Los delimitadores `0x5A` van crudos**, nunca escapados. Si se pasan por la función de
   escape se convierten en `5B 01`, el panel no reconoce ninguna trama y **no contesta nada**
   (sin ningún error visible). Nos pasó exactamente eso.
2. **No cortes la trama por el campo de longitud**: la respuesta de propiedades mide 373
   bytes en el cable pero declara 371, porque los escapes insertan bytes que `len` no cuenta.
   Un parser que corte en `len` descarta la respuesta entera **en silencio**.
3. **No vacíes el manejador de errores** de node-hid (`dev.on('error', () => {})`): ahí es
   donde aparecen las escrituras rechazadas.
4. El panel **retransmite** respuestas idénticas: deduplica o atribuirás mal las respuestas.
