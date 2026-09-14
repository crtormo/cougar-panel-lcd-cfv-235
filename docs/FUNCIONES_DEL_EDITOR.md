<!-- Procede del banco de pruebas de Windows. -->

> **Que aporta este documento.** El inventario del editor oficial de COUGAR: que hace
> cada pantalla, que interfaces internas expone y los **35 nombres de fuente de datos**
> que usa. Sirve para comparar comportamiento y para saber que pide el editor cuando
> parece que el panel hace algo raro.

# Funciones del COUGAR LCD Editor — inventario y lo que falta

Extraído del bundle del renderer instalado (1.0.14), que está en `renderer-js/` y legible. El
puente entre la interfaz y el proceso principal se expone en `window.*` y el bundle lo
envuelve en una clase estática:

```js
class Bridge {
  static webview()          { return window.webview }
  static waterBlockScreen() { return window.waterBlockScreen }
  static themeCreator()     { return window.themeCreator }
  static sysInfoStatus()    { return window.sysInfoStatus }
  static sysInfoSpec()      { return window.sysInfoSpec }
  static sysInfoDetails()   { return window.sysInfoDetails }
  static screenRecoder()    { return window.screenRecoder }
  static device()           { return window.device }
  static log()              { return window.log }
  static settings()         { return window.settings }
}
```

**Todas las llamadas de la interfaz pasan por ahí.** Eso da la lista completa de funciones
que la aplicación puede invocar, y por tanto el mapa de lo que hay que reconstruir.

---

## 1. Inventario de funciones (extraído automáticamente del bundle)

### `waterBlockScreen()` — el panel
| Función | Qué hace (deducido del uso) | En el cable |
|---|---|---|
| `brightnessSet(valor, sn)` | brillo 0-100 | ✅ `POST brightness {"value":N}` |
| `rotateSet(grados, sn)` | orientación 0/90/180/270 | ✅ `POST rotate {"degree":N}` |
| `recovery(sn)` | Reset desde la pestaña Screen | ✅ `POST recovery {"enable":true}` (reinicia el panel) |
| `media()` | lista de medios del panel | ❌ **no localizado** (`GET waterBlockScreen` → 400) |
| `mediaDelete(path)` | borra un medio | ❌ **no localizado** (`DELETE mediaDelete` → 400) |
| `mediaImageAdd("PNG", arrayBuffer, ratio, ancho, alto)` | añadir imagen a la biblioteca | ❌ pendiente |
| `mediaVideoCropAdd(path, {x,y}, w, h, ratio, W, H)` | recortar vídeo | ❌ pendiente |
| `mediaInfoGet(path)` | propiedades del medio (ancho/alto) | ❌ pendiente |
| `mediaFrameGet(path)` | primer fotograma de un vídeo | ❌ pendiente |
| `getPreloadPath()` / `getWebviewSrc()` | preload y URL del webview que **renderiza el OSD** | — (local) |

### `themeCreator()` — temas
`allCustomizationThemes(dpi)` · `customizationThemeItem(id)` · `customizationThemeAdd(objeto, imagenBase64)` · `customizationThemeDelete(id)` · `canThemeBeDeleted(id)` (`"inuse"`/`"default"`/`"preset"`) · `changeTheme(id, sn)` ← **el `切换主题` del log** · `exportThemeOption(id)` · `importThemeOption()` · `getFontList()` · `sourceType("line"|"all")` · `sourceInformations(fuente)`

### `settings()`
`appVersion` · `language`/`languageSet`/`languageOptions` · `temperature`/`temperatureSet`/`temperatureOptions` · `startupWithWindows`/`startupWithWindowsSet` · `closeAction`/`closeActionSet` · `logExport` · `openDefaultBrowserUrl` · `faqs`

### `sysInfoStatus()` / `sysInfoSpec()` / `sysInfoDetails()`
`onSysInfoStatusChanged` (evento) · `cpu` · `gpu` · `details` — la telemetría que alimenta los widgets y el `STATE all`.

### `webview()` — el motor del OSD (¡la pieza clave!)
`renderGraph` · `updateData` · `setBg` · `clearGraph` · `playVideo` · `stopVideo` · `removeAllListener`

Es un webview oculto al que le pasan el fondo y los datos, dibuja los widgets y **se captura
como imagen** (`renderToBase64` en el bundle usa `getWebviewSrc()` + `getPreloadPath()` +
`toDataURL("image/png")`). **Ese PNG capturado es lo que se sube al panel** como fichero
`.osd`. Es decir: el «dashboard en vivo» del editor es *renderizar el webview y subir la
captura*, una vez por actualización.

---

## 2. Reglas del formato (sacadas del propio código)

| Dato | Valor |
|---|---|
| Formatos aceptados al añadir medio | **`.png`, `.jpg`, `.mp4`, `.gif`** |
| Tamaño máximo | **< 20 MB** (rechaza `>= 20`) |
| Dimensiones | ≤ 1920×1080 (o ≤ 1080×1920) |
| `ratio` / `dpi` | `"320:77"` ↔ `1920X462` (tu panel) y `"1:1"` ↔ `720X720` / `480X480` |
| Orientación según ratio | con `320:77` el paso de rotación es 180 y solo admite 90/270 |
| Id de tema | `Date.now()` (por eso `1789257523661` es una marca de tiempo) |
| Guardar tema | `customizationThemeAdd({id,title,url,widget,background,antvX6,dpi,ratio}, imagenBase64)` |
| Temas por defecto | ids `1722413507022` y `1723630367584` (no exportables) |

---

## 3. Qué falta para la ingeniería inversa COMPLETA

| Subsistema | Estado | Qué falta |
|---|---|---|
| Protocolo con el panel | ✅ ~90% | el formato de bloque del **fondo** (la capa OSD ya funciona) |
| `waterBlockScreen` (6 de 11 funciones) | ✅ parcial | `media()`, `mediaDelete()`, `mediaImageAdd()`, `mediaVideoCropAdd()`, `mediaInfoGet()`, `mediaFrameGet()` → **su comando de cable** |
| `themeCreator` (11 funciones) | ✅ nombres y argumentos; ❌ el cable | `changeTheme` se sabe por el log (`切换主题` + subidas + `recovery` + `rotate`); el resto, no |
| `settings` / `sysInfo*` | ✅ nombres | son locales, no van al panel (poco valor) |
| **Proceso principal** (`out/main/index.jsc`) | ❌ **opaco** | es **caché de bytecode de V8**, no fuente: no se descompila. **Aquí vive el mapeo función → comando**, y es el 100% de lo que falta |
| Preload (`out/preload/index.jsc`) | ❌ opaco | igual; pero su superficie ya la tenemos del renderer |
| Formato `.osd` | ⚠️ | son PNG capturados del webview (muy probable); falta confirmarlo |
| Biblioteca de medios del panel | ❌ | cómo se listan y borran medios (el panel rechaza las formas probadas) |
| `screenRecoder()` | ❌ | grabación de pantalla para temas de vídeo |

**Resumen honesto:** de la **interfaz** ya tenemos el inventario completo de funciones (esto
mismo); lo que falta es **el proceso principal**, que está compilado a caché de bytecode y no
se puede leer. Por eso el camino no es descompilar, sino **observar en ejecución**.

---

## 4. Cómo cerrarlo: técnicas que aún no hemos usado

| Técnica | Qué daría | Coste |
|---|---|---|
| **USBPcap + Wireshark** | **los bytes exactos** de cada informe HID del editor (incluidos los bloques de medios, que su log NO escribe) | instalar (admin), capturar, analizar |
| **`--inspect` en el proceso principal** (`& '.\COUGAR LCD Editor.exe' --inspect=9229`, en admin) | lo más parecido al código: inspeccionar el proceso Node en vivo, **listar sus funciones**, ver variables y hasta llamarlas | bajo; hay que probar si el flag está permitido en la app empaquetada |
| **CDP en el renderer** (`--remote-debugging-port=9222`) | ejecutar `window.waterBlockScreen().media()` a mano y **ver qué manda** al panel (combinado con la captura USB) | bajo |
| **Process Monitor** (filtrado a la app) | cada fichero que lee/escribe: los `.osd` temporales, los temas, la caché | bajo |
| **Análisis estático de lo que ya tenemos** | hecho para la API; queda por mapear los canales IPC y los textos de la interfaz | ya en marcha |

La combinación **CDP + USBPcap** es la que cierra el mapa completo: se llama a cada función
desde la consola del renderer y se graba lo que sale por USB. Sin descompilar nada.

---

## 5. Estado del conjunto (2026-09-12)

> **Actualización 2026-09-13:** las funciones de interfaz que faltaban **ya están
> reproducidas** con código propio en `widgets/`: motor de widgets con los mismos tipos
> (`texto`, `dato`/tarjeta, `barra`, `gráfica`), **las 35 fuentes del editor por su nombre**, y
> un **editor visual local** (arrastrar widgets, elegir fuente, «Aplicar al panel», bucle
> animado). Verificado en el panel. No se copió nada del editor: sólo el conocimiento del
> formato y de su comportamiento.

- ✅ **Panel**: protocolo verificado en hardware (trama, escape, checksum, comandos,
  telemetría, subida) — ver `PROTOCOLO.md`.
- ✅ **Interfaz**: inventario completo de funciones y reglas de formato — este documento.
- ⚠️ **Adaptador** (proceso principal): mapeo función → comando del panel. Falta.
- ⚠️ **Mostrar una imagen propia**: el panel acepta nuestras subidas pero no las dibuja; falta
  el formato de bloque del fondo.
