# Instrumentar el editor para capturar el protocolo COMPLETO

Captura **todo** lo que el editor lee y escribe por USB HID — incluidos los **bloques de
fichero** de la subida, que son lo único que falta del protocolo.

**Ventaja:** no hay que desempacar ni reempaquetar el `app.asar`. Se engancha `node-hid`
desde fuera con `NODE_OPTIONS`.

---

## Requisitos

- Una consola **de administrador**. El `.exe` del editor pide elevación, así que sin admin
  no arranca. Compruébalo con:

  ```powershell
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')
  # tiene que decir True
  ```

  Si dice `False`: cierra esa ventana, abre **PowerShell con botón derecho → Ejecutar como
  administrador** y vuelve a empezar. (Ese es el motivo del `Acceso denegado` al escribir en
  `C:\`: sin admin no se puede ni escribir en la raíz del disco.)

- El editor **NO debe estar corriendo**. Su lógica de instancia única hace que, si lanzas una
  segunda, salgan las dos. Ciérralo del todo:

  ```powershell
  Stop-ScheduledTask -TaskName 'StartCLE' -ErrorAction SilentlyContinue
  Get-Process 'COUGAR LCD Editor' -ErrorAction SilentlyContinue   # tiene que salir vacio
  ```

---

## Pasos

```powershell
# 1) (consola de ADMINISTRADOR) apunta el enganche
$env:NODE_OPTIONS = "--require $env:USERPROFILE\Desktop\CFV235-Linux\instrumentar\captura-nodehid.js"

# 2) lanza el editor TU, para que herede esa variable
& 'C:\Program Files\COUGAR LCD Editor\COUGAR LCD Editor.exe'
```

3. Deja que arranque y **espera 2–5 minutos** a que registre el panel (la pestaña *Screen* se
   llena cuando lo consigue; hasta entonces está vacía).

4. Haz las operaciones que quieras capturar:
   - **Screen → `Reset`** (manda `recovery`: puede desatascar el panel)
   - **Screen → `Update`** o aplica un tema (esto lanza la **subida**, que es lo que buscamos)
   - cambiar brillo y rotación

5. Cierra el editor desde el **icono de bandeja → salir** (la X solo minimiza).

6. El volcado queda en:

   ```
   C:\Users\Maximo\Desktop\CFV235-Linux\hid-captura.log
   ```

   Cada línea es:
   ```
   W <ms> <hex>   -> lo que la app ESCRIBE al panel
   R <ms> <hex>   -> lo que la app LEE del panel
   ```

---

## Cómo saber que el enganche funcionó

El fichero debe empezar así:

```
# ===== captura iniciada 2026-...T... pid=...
# enganche de require instalado
# node-hid interceptado correctamente
```

Si **no** aparecen esas tres líneas, o el fichero no crece, prueba estas dos cosas:

- Arranca el editor con la variable puesta **en la misma consola** (paso 1 y 2 juntos).
- Si Electron ignorase `NODE_OPTIONS`, usa la vía del `app.asar`
  (ver `../capturar_en_windows.md`, apartado 3a).

Deberías ver líneas `W 5a00...` desde el primer momento (el handshake `conn`) — sirve para
validar que captura de verdad.

---

## Qué haré yo con el volcado

Con ese fichero reconstruyo el **algoritmo de bloques** (tamaño de bloque, `FileBlockId`,
`ContentRange`, `Counter`, `Option`) y lo integro en `cougar_panel.py`, de modo que puedas
subir fondos y OSD desde Linux sin el editor.

---

## Apéndice: desensamblar el bytecode (opcional, y con reservas)

El proceso principal es un **code cache de V8 15.3** y el único host que puede cargarlo es el
Electron del propio editor (por eso hace falta admin). La orden sería:

```powershell
# consola de ADMINISTRADOR
$env:ELECTRON_RUN_AS_NODE = '1'
cd 'C:\Program Files\COUGAR LCD Editor'
& '.\COUGAR LCD Editor.exe' --no-lazy --print-bytecode `
    -e "require('./resources/app.asar/out/main/index.js')" *>
    "$env:USERPROFILE\Desktop\bytecode-main.txt"
```

**Aviso honesto:** puede salir **vacío**. `--print-bytecode` engancha el *compilador* de V8,
pero un code cache se carga por *deserialización*, no compilando — así que es posible que no
imprima nada. Pruébalo (es barato) y, si sale algo, pásamelo y te reconstruyo la lógica.
Si sale vacío, la vía de la instrumentación de arriba es la buena.
