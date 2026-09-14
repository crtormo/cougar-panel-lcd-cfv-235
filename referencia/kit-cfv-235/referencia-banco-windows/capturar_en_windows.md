# Qué hacer en Windows para adelantar el trabajo

> ## ✅ Estado actual (2026-09-13)
>
> Este documento se escribió cuando el panel estaba atascado. **Ya está todo resuelto**:
> el panel se desatascó con un corte de energía real, la subida por bloques se reconstruyó
> (y se corrigió el tiempo, que era lo que fallaba), y hoy tenemos dashboard propio, motor de
> widgets, editor visual y reflejo de pantalla.
>
> Lo que sigue teniendo valor de este documento:
> - Las **técnicas de captura** (secciones 2 y 3) y la lista de lo que se puede recuperar.
> - La **técnica estrella**: parchear el `node-hid` del editor en caliente con su inspector
>   (`sondas/cdp_parche.js`) — así se obtuvieron los bytes exactos de la subida.
>
> Lo que ya **no** aplica: el contexto del panel atascado y la idea de que la subida por
> bloques estaba pendiente.

Objetivo original: conseguir el protocolo completo (sobre todo la **subida por bloques**) y
dejar el panel desatascado para poder seguir en Linux.

---

## 0. Contexto: por qué el panel está así

Subiste un fichero mayor que la memoria del panel. La firma medida:

| Señal | Valor |
|---|---|
| `space` | bajó de **40128** a **40008** KB |
| `bootFinish` | clavado en **0** (su arranque no se completa) |
| `transport` | **400** en 977 de 977 intentos |
| `logo` | **2** (pantalla de respaldo) |
| `background` | solo `["g_40mb.jpg"]` |

Consecuencia importante: **el editor se niega a registrar un panel con `bootFinish: 0`**
(verificado en 5 registros: los 5 ocurrieron con `bootFinish: 1`). Y sin registro, la
pestaña **Screen** aparece vacía, así que **el botón `Reset` del editor no es alcanzable**.

> Por eso la vía Linux puede llegar donde el editor no: tu cliente HID envía `recovery`
> directamente, sin depender de ese registro. Empieza por ahí.

---

## 1. Desatascar el panel

**Opción A — desde Linux (recomendada, no depende del editor):**

```bash
sudo python3 cougar_panel.py conn                 # confirma bootFinish=0
sudo python3 cougar_panel.py raw POST recovery    # el "Reset" del editor
sudo python3 cougar_panel.py conn                 # ¿ya bootFinish=1?
```

Si `recovery` no basta, prueba en este orden:
`raw POST config`, `raw DELETE mediaDelete --header FileName=g_40mb.jpg`,
y por último `raw GET waterBlockScreen` para ver qué ficheros quedan dentro.

**Opción B — desde Windows, con el editor:**

1. Cierra el editor **del todo**: icono de bandeja → salir (la X solo lo minimiza).
   **No lo vuelvas a lanzar mientras esté abierto**: su lógica de instancia única hace que
   salgan las dos instancias (comprobado: 17 arranques, 9 duplicados, todas las sesiones
   murieron). Si necesitas la ventana, tráela desde la barra de tareas.
2. Arranca el editor **una sola vez** y déjalo 2–5 minutos sin tocar. El handshake con el
   panel tarda eso.
3. Cuando `bootFinish` sea 1, el editor registra el panel y la pestaña **Screen** se llena.
   Ahí: **`Reset`** y luego **`Update`** (que reenvía el tema).

**Opción C — ciclo de alimentación del panel.** Corta su corriente ~30 s (o apaga el PC) con
el editor cerrado, y vuelve a arrancar. Un arranque limpio es lo que mejor le sienta.

---

## 2. Capturar el protocolo completo (la vía buena, sin descompilar)

El editor **ya escribe en su log cada trama que envía y recibe**, en hexadecimal completo:

```
	 data hex: 5a00aa504f5354207472616e73706f727420310d0a5365714e756d6265723d...
```

El único motivo de que no tengamos los bloques de subida es que **nunca consiguió un 200**.
Así que:

1. Desatasca el panel (paso 1).
2. En el editor: **Screen → Update** (o aplica un tema). Eso lanza la subida.
3. Copia el log y pásalo por el analizador:

```bash
python3 analizar_log.py --log 2026-9-12.log --jsonl protocolo.jsonl
```

El script valida longitud y checksum de cada trama, empareja peticiones con respuestas y
**avisa en cuanto aparezcan las cabeceras `FileName` / `FileBlockId` / `FileSize` /
`ContentRange`** — que es exactamente el protocolo de bloques.

Log del editor en Windows: `%APPDATA%\cougar_lcd_editor\logs\*.log`

---

## 3. Si quieres capturar *todo*, byte a byte (requiere administrador)

**3a. Instrumentar `node-hid`.** El módulo está en
`C:\Program Files\COUGAR LCD Editor\resources\app.asar.unpacked\node_modules\node-hid\`,
pero **en disco solo está el `.node` nativo**; el wrapper JS (`nodehid.js`) vive dentro del
`app.asar`. Para interceptarlo hay que desempacar y reempaquetar:

```powershell
# PowerShell como ADMINISTRADOR
cd 'C:\Program Files\COUGAR LCD Editor\resources'
copy app.asar app.asar.orig
npx asar extract app.asar app_unpacked
# edita app_unpacked\node_modules\node-hid\nodehid.js y envuelve write()/read()
npx asar pack app_unpacked app.asar
```

En el wrapper, un volcado mínimo:

```js
const origWrite = HID.prototype.write
HID.prototype.write = function (data) {
  require('fs').appendFileSync('C:\\hid.log', Buffer.from(data).toString('hex') + '\n')
  return origWrite.call(this, data)
}
```

Así capturas cada mensaje, del tamaño que sea, incluidos los bloques de fichero.

**3b. Captura pasiva con USBPcap + Wireshark.** No toca la app: USBPcap captura el tráfico
USB (incluidos los informes HID) y Wireshark lo muestra. Requiere instalar un filtro de
captura (admin). Útil si no quieres modificar nada; luego hay que reensamblar las tramas a
partir de los informes.

---

## 4. Levantar el bytecode del proceso principal (última opción)

`out/main/index.jsc` **no es bytecode suelto: es un V8 code cache** (lo carga
`bytecode-loader.js` con `new vm.Script(dummy, { cachedData })`). Datos de tu fichero:

```
uint32[0] = 0xC0DE05DF    magic 0xC0DE + offset 0x05DF (1503 -> V8 15.3)
uint32[1] = 0x767EEED3    hash de version de V8
source hash en el byte 8, flag hash en el byte 12
```

**El código fuente original NO está dentro** (solo su hash). Para levantarlo a pseudo-código
necesitas la versión de V8 exacta, que sacas así (en un PowerShell **de administrador** —
el `.exe` pide elevación, por eso desde una consola normal falla):

```powershell
$env:ELECTRON_RUN_AS_NODE=1
& "C:\Program Files\COUGAR LCD Editor\COUGAR LCD Editor.exe" -p "process.versions"
```

Con eso, herramientas tipo `view8` (bytecode de V8 -> pseudo-JS) pueden trabajar sobre el
`.jsc`. Espera pseudo-código imperfecto; con las cadenas reales —que ya están extraídas en
`PROTOCOLO.md`— se sigue bien.

**No existe** forma de recuperar el código fuente original ni una reconstrucción limpia de
los 600 KB: no hay source maps ni fuentes TypeScript en el paquete.

---

## 5. Lo que sí tienes ya, gratis

| Parte | Formato | Recuperable |
|---|---|---|
| Renderer (`main-10c86d44.js`, `index.vue…js`, chunks) | JS plano minificado | **100 %** con un beautifier |
| `node_modules` (node-hid, appium-adb, sharp…) | JS plano | **100 %** |
| Protocolo con el panel | — | **100 %** capturando el log (paso 2) |
| `out/main/index.jsc` | V8 code cache | solo por instrumentación o lifting |

Los ficheros del renderer se sacan así:

```powershell
npx asar extract "C:\Program Files\COUGAR LCD Editor\resources\app.asar" salida
# salida\out\renderer\assets\  -> ahi tienes todo el JS de la interfaz
npx js-beautify salida\out\renderer\assets\main-10c86d44.js > main.lindo.js
```

---

## 6. La técnica que funcionó de verdad: parchear el editor en caliente

El log del editor **no registra los informes de medios** (los bloques de la subida), que era
justo lo que faltaba. Pero el editor es Electron, así que se puede:

1. **Abrirlo con el inspector** (consola de administrador, porque la app pide elevación):

   ```powershell
   cd 'C:\Program Files\COUGAR LCD Editor'
   & '.\COUGAR LCD Editor.exe' --inspect=9229
   ```

2. **Parchear su `node-hid`** para que registre cada informe con sus bytes:

   ```powershell
   cd C:\Users\Maximo\Desktop\CFV235-Linux\sondas
   node cdp_parche.js
   ```

   (El parche vive en memoria: si cierras el editor, hay que repetirlo.)

3. **Forzar que reabra el dispositivo**: desenchufar y volver a enchufar el USB del panel
   (el parche se aplica a instancias nuevas de `node-hid`).

4. **Hacer la acción** en la interfaz y leer `captura_informes.txt`: ahí están **todos** los
   informes, byte a byte.

Así se obtuvieron el formato exacto del bloque y su sincronización (ver `PROTOCOLO.md` §4).

### Pendiente: capturar la subida de un VÍDEO

Es la última vía de comunicación sin explorar. El editor acepta `.mp4` y `.gif`
(`mediaImageAdd`, `mediaVideoCropAdd`, `mediaInfoGet`, `mediaFrameGet`), y su biblioteca
distingue tipos `PNG`/`MP4` — pero **nunca hemos visto cómo se sube un vídeo al panel**.

Receta (misma técnica, unos 10 minutos):

1. En el editor, crear o elegir un tema con **fondo de vídeo** (`mp4` corto, de unos MB).
2. Abrir el editor con `--inspect=9229` y lanzar `node cdp_parche.js`.
3. Desenchufar/enchufar el panel para forzar la reapertura del dispositivo.
4. **Aplicar el tema de vídeo** y esperar unos segundos.
5. Analizar `captura_informes.txt`: hay que buscar informes **cuyos datos no empiecen por la
   firma PNG** (`89504e470d0a1a`) y ver qué llevan el byte `[4]` y el tipo `[9]`.

Con eso sabremos si el vídeo viaja por el mismo camino de bloques con otro tipo, o si usa un
protocolo aparte — y si se puede reproducir desde nuestro cliente.

