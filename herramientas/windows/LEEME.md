# `herramientas/windows/` — el banco de pruebas

Aquí está lo que sirvió para **descubrir el protocolo** mirando al editor oficial de COUGAR por
dentro, desde Windows. No hace falta para usar el panel (eso lo hace `cfv235`), pero sí para
volver a comprobar algo si algún día el editor cambia: es la única forma de ver **lo que manda
el editor de verdad**, byte a byte, incluidos los bloques de subida que su propio log no
registra.

| Fichero | Qué hace |
|---|---|
| `cdp_enganchar.js` | Se conecta al **inspector de Electron** del editor (`--inspect=9229`) y le reescribe `node-hid` en caliente para grabar cada informe HID con sus bytes |
| `captura-nodehid.js` | La otra vía: se inyecta con `NODE_OPTIONS=--require` y vuelca todo lo que se lee y se escribe por HID |
| `capturar_en_windows.md` | El procedimiento completo, paso a paso, con lo que costó cada intento |

## El que se usó de verdad

```powershell
# 1) consola de ADMINISTRADOR: abrir el editor con el inspector
cd 'C:\Program Files\COUGAR LCD Editor'
& '.\COUGAR LCD Editor.exe' --inspect=9229

# 2) conectar el gancho (deja el editor grabando todo lo que manda al panel)
cd <ruta del proyecto>\herramientas\windows
node cdp_enganchar.js 9229 captura_informes.txt

# 3) en la interfaz del editor: cambiar el fondo o aplicar un tema
# 4) cortar la muestra
Get-Content .\captura_informes.txt -TotalCount 12 | Set-Content .\muestra_subida_fondo.txt
```

Cada línea queda como `2026-09-13T02:5x:xx.xxxZ len=<bytes> <hex>`, que es exactamente lo que
hizo falta para deducir:

- el formato real del informe de bloque: `00 5C <21+datos BE> <contador> <n bloques BE>
  <índice BE> <capa> 00×15 <datos>`;
- los tiempos críticos: **87 ms** entre `transport` y el primer bloque, **6 ms** entre el último
  bloque y `transported`;
- que el último bloque va **sin rellenar** (769 B para 744 de datos);
- las dos capas: fondo (`0x02`, acumula) y OSD (`0x01`, reutiliza hueco).

Sin esto, la subida no habría funcionado: el handshake respondía `200` y los bloques se
aceptaban sin error, pero el panel los rechazaba con `1 400 AckNumber=0` y `transported`
devolvía un `200` **sin cuerpo**.

## Aviso

- Estas herramientas son **solo para Windows** (Electron y la API HID de Windows).
- El editor hay que lanzarlo **como administrador** y **solo un programa** puede hablar con el
  panel a la vez: cierra `cfv235` y sus servicios antes de usarlas.
- Los binarios y ficheros del editor de COUGAR **no** se copian aquí: son suyos.
