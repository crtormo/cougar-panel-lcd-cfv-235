# `capturas/` — evidencia de la ingeniería inversa

> **Aviso (2026-09-13).** Los dos ficheros de captura (`muestra_subida_fondo.txt` y
> `captura_informes.zip`) **se perdieron**: los borró por error un comando mío mientras
> reorganizaba la carpeta, y no estaban en la Papelera ni en ninguna copia. Lo que sigue
> conserva **lo que enseñaban** —que es lo que importa— y más abajo está cómo volver a
> tomarlos. Nada del protocolo depende de ellos: los formatos medidos están en `PROTOCOLO.md`.

## `muestra_subida_fondo.txt` (perdido)

Las primeras 12 escrituras HID del editor, tal cual las registró el parche que instalamos en
su `node-hid`. Contiene **la subida de un fondo de principio a fin**:

```
...Z write len= 111 005a006e 504f535420706f776572...        POST power {"event":"resume"}
...Z write len= 740 005a02e2 535441544520616c6c...          STATE all  (telemetría)
...Z write len= 159 005a009e 504f5354207472616e73706f7274.. POST transport {"fileSize":3744,...}
...Z write len=1025 005c03fd 16 0004 0000 02 00...           BLOQUE idx=0
...Z write len=1025 005c03fd 16 0004 0001 02 00...           BLOQUE idx=1
...Z write len=1025 005c03fd 16 0004 0002 02 00...           BLOQUE idx=2
...Z write len= 769 005c02fd 16 0004 0003 02 00...           BLOQUE idx=3 (sin relleno)
...Z write len= 143 005a008f 504f5354207472616e73706f7274.. POST transported {"md5":"todo",...}
```

Fíjate en los tiempos: entre el `transport` y el primer bloque pasan **87 ms**, y entre el
último bloque y el `transported`, **6 ms**. Eso es lo que hacía fallar nuestras subidas.

## `captura_informes.zip` (perdido)

La captura completa: **11.907 escrituras** del editor al panel (11.435 informes de medios y 472
tramas de control), ~1.860 de ellas de la primera sesión.

Se obtuvo **sin USBPcap ni controladores**: el editor es Electron, así que nos conectamos a su
inspector (`--inspect=9229`) y **reescribimos en caliente su `node-hid`** con
`Debugger.setScriptSource` para que registre cada informe con sus bytes
(`_solo-windows/sondas/cdp_parche.js`).

Este fichero es la prueba de todos los datos del protocolo: formato de bloque, delimitadores,
contadores y sincronización. Si algún día hay dudas sobre el protocolo, se resuelven aquí.

## Cómo volver a tomarlas

El procedimiento está en `capturar_en_windows.md` y las piezas están todas recuperadas. En
resumen, en Windows y con el editor COUGAR cerrado:

```powershell
# 1) abrir el editor con el inspector
& "C:\Program Files\COUGAR LCD Editor\COUGAR LCD Editor.exe" --inspect=9229
# 2) enganchar el gancho (deja el editor escribiendo todo lo que manda al panel)
cd _solo-windows\instrumentar
$env:COUGAR_NODE_HID = "C:\ruta\a\nodehid.js"
node captura-nodehid.js 9229 .\captura_informes.txt
# 3) en la interfaz del editor: cambiar el fondo o aplicar un tema
# 4) cortar la muestra y comprimirla
Get-Content .\captura_informes.txt -TotalCount 12 | Set-Content .\muestra_subida_fondo.txt -Encoding UTF8
Compress-Archive .\captura_informes.txt -DestinationPath .\captura_informes.zip -Force
```

Cada línea queda como `2026-09-13T02:5x:xx.xxxZ len=<bytes> <hex>`, que es exactamente lo que
se ve arriba. (El gancho se puede repetir tantas veces como haga falta: no toca el panel.)
