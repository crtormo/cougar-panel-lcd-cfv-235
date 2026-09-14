# `referencia-banco-windows/` — cómo se averiguó todo esto

Todo lo de esta carpeta sirve para hablar con el panel **desde Windows**, y es lo que se usó
para descubrir el protocolo. En Linux no hace falta: el paquete `cougar` hace lo mismo. Se
incluye porque es la evidencia y la herramienta de comparación: si algún día el editor oficial
de COUGAR cambia algo, aquí está cómo volver a mirarlo.

| Fichero | Para qué |
|---|---|
| `sondas/cougar_hid_node.js` | Sonda en Node.js: **segunda implementación** del protocolo (36 KB, con `estado`, `subir`, `bucle`, `stream`, `vigilar`, `barrido`, `efectos`…). Sirve para contrastar tramas desde otro lenguaje. Necesita `node-hid` (`npm install node-hid`, o `COUGAR_NODE_HID=<ruta a nodehid.js>`) |
| `sondas/buscar_bloque.js` | Prueba variantes de informe de bloque contra el panel |
| `sondas/cdp_parche.js`, `cdp_enganchar.js`, `cdp_eval.js`, `cdp_scripts.js` | Enganchan al **inspector de Electron** del editor oficial (`--inspect=9229`) y reescriben su `node-hid` en caliente con `Debugger.setScriptSource` |
| `sondas/vigilar_panel.js` | Vigila `bootFinish` mientras se desenchufa y se vuelve a enchufar el panel |
| `sondas/generar_png.js` | Genera el PNG de prueba que usaba el banco de pruebas |
| `instrumentar/captura-nodehid.js` | El gancho mínimo: registra **cada informe** que escribe el editor, con sus bytes y su hora |
| `dashboard/` | El primer dashboard, en PowerShell (`panel-dashboard.ps1`, `panel-bucle.ps1`, `instalar-tarea.ps1`). Sustituido por `cougar bucle` |
| `capturar-pantalla.ps1` | Captura de pantalla en Windows (el reflejo; en Linux se usa `grim`/`scrot`/`ffmpeg`) |
| `capturar_en_windows.md` | El procedimiento completo de la captura, paso a paso |

## Por qué esto importa

El gancho (`instrumentar/captura-nodehid.js`) es lo que permitió leer, byte a byte, cómo sube
el editor una imagen. De ahí salieron:

- el formato real del informe de bloque: `00 5C <21+datos BE> <contador> <n bloques BE>
  <índice BE> <capa> 00×15 <datos>`;
- los tiempos críticos: **87 ms** entre `transport` y el primer bloque, **6 ms** entre el
  último bloque y `transported`;
- el detalle de que el último bloque va **sin rellenar** (769 B para 744 de datos);
- las dos capas: fondo (`0x02`, acumula) y OSD (`0x01`, reutiliza hueco).

Sin ese gancho, la subida no habría funcionado: el handshake respondía `200` y los bloques se
aceptaban sin error, pero el panel los rechazaba con `1 400 AckNumber=0` y `transported`
devolvía `200` con el cuerpo vacío.

Las capturas crudas que se tomaron con él (11.907 escrituras) se perdieron por un borrado
accidental; el resumen anotado y el procedimiento para repetirlas están en
`capturar_en_windows.md`.
