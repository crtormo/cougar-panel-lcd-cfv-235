# Protocolo del panel LCD COUGAR CFV235 (9.16", 1920x462)

Reconstruido íntegramente a partir del log del **COUGAR LCD Editor 1.0.14** y verificado
contra los frames reales (checksum comprobado en 336/336 muestras).

---

## 1. Capa física: USB HID

| Dato | Valor |
|---|---|
| VID / PID | `0x1D6B` / `0x0126` (VID de Linux Foundation — el panel no usa un VID propio de COUGAR) |
| Fabricante (string) | `COUGAR Inc.` |
| Producto (string) | `COUGAR USB Device` |
| Nº de serie | p. ej. `BYZL2611WC01CM001018` |
| Interfaz | 0 — una sola interfaz, dispositivo **no** compuesto |
| `usagePage` / `usage` | `0xFF00` (vendor-defined) / `1` |
| `release` | `256` |

En Linux aparece como `/dev/hidrawN`. Localízalo así:

```bash
for h in /dev/hidraw*; do echo "$h: $(grep HID_ID /sys/class/hidraw/$(basename $h)/device/uevent)"; done
# busca: HID_ID=0003:00001D6B:00000126
```

**Escritura:** cada transferencia es **un informe de 1025 bytes**: el byte de report ID
`0x00` seguido de **1024 bytes de datos**, y el resto **relleno de ceros**. Ese informe
contiene la trama entera, así que una petición corta viaja en un solo informe con muchos
ceros detrás. La implementación independiente `cougarLCD.cpp` (mismo panel, VID/PID
idénticos) escribe siempre 1025 bytes y funciona; en Windows también se ha verificado que
el panel responde igual escribiendo el tamaño exacto y rellenando a 64 o a 1024.

**Lectura:** los informes de entrada llegan también de 1024 bytes útiles y pueden contener
la trama **y ceros de relleno** detrás. Acumula y busca el `0x5A` inicial; **no** recortes
por el campo de longitud (ver §2).

---

## 2. Trama (framing)

```
informe:  00 | 5A | len (BE16) | payload_escapado | checksum | 5A | 00 00 00 ...
          ^^   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^
       report ID            la trama                                relleno
```

### 2.1 Escape (byte stuffing)

**El payload, los dos bytes de longitud y el checksum se escapan:**

| Byte original | En el cable |
|---|---|
| `0x5A` | `5B 01` |
| `0x5B` | `5B 02` |

Es la pieza que más cuesta descubrir y la que rompe los parsers: el número de serie
`BYZL2611WC01CM001018` contiene una `Z` (`0x5A`) y el JSON `background` contiene un `[`
(`0x5B`), así que **casi todas las respuestas de propiedades llevan escapes**.

### 2.2 Longitud y checksum

- **`len` = `len(payload_sin_escapar) + 5`.** No cuenta los bytes de código que inserta el
  escape, así que **la trama del cable es MÁS LARGA que `len` cuando hay escapes**:
  medido `len = 371` con **373 bytes** en el cable.
- **`checksum = (byte_alto + byte_bajo + suma(payload_sin_escapar)) & 0xFF`.**
  Se suman los **dos bytes** de longitud, **no** el valor `len`.

> ⚠️ Antes aquí decía `checksum = (sum(payload) + len) & 0xFF`. Es correcto solo para
> tramas de menos de 256 bytes (donde `byte_alto + byte_bajo == len`), y por eso cuadraba
> en 336/336 muestras del log del editor. Con `len = 371`: `1 + 115 = 116`, mientras que
> `371 mod 256 = 115` → **desfase de exactamente 1**, medido y confirmado en las tramas de
> `tramas_reales/` con `probar_tramas.py`.

### 2.3 Cómo encontrar el fin de trama

**Por el `0x5A` de cierre, nunca por `len`.** El payload no puede contener un `0x5A` crudo
porque va escapado, así que el cierre es inequívoco:

1. Busca el `0x5A` inicial.
2. Avanza desescapando (`5B 01` → `5A`, `5B 02` → `5B`) hasta encontrar otro `0x5A` crudo:
   ese es el final.
3. Comprueba que `len` declarado == tamaño ya desescapado, y el checksum.

Un parser que corte en `len` se desincroniza en cuanto aparece un escape y **descarta la
respuesta entera en silencio** (fue el error que nos tuvo horas dando vueltas: `conn`
parecía no responder y en realidad respondía siempre).

Ejemplo real, `POST brightness 1`, trama de 109 bytes sin escapes:

```
5a 006d 504f5354206272696768746e6573732031...  fc 5a
   ^^^^ len=0x6D=109
                                          ^^ checksum
```
`sum(payload) mod 256 = 143`, `0 + 109 = 109`, `143 + 109 = 252 = 0xFC` ✅

Ejemplo real con escapes (`tramas_reales/respuesta_conn_1.bin`):

```
len declarado 371 | trama en el cable 373 | escapes 2 (0x5A y 0x5B)
checksum 0xA8 = (1 + 115 + suma(366 B)) & 0xFF   ✅
```

---

## 3. Payload: cabecera tipo HTTP

### Petición
```
<METHOD> <cmd> 1\r\n
SeqNumber=<n>\r\n
Date=<ms epoch>\r\n
ContentType=json\r\n          ← solo si hay body
ContentLength=<bytes>\r\n     ← solo si hay body
\r\n
<json>                        ← o nada
```

### Respuesta
```
<subtipo> <code>\r\n
AckNumber=<n>\r\n
ContentType=json\r\n          ← solo si devuelve json
ContentLength=<bytes>\r\n
\r\n
<json>
```

- `<code>` observados: **`200`** (OK) y **`400`** (Bad Request).
- `SeqNumber` lo elige el cliente; normalmente la respuesta trae `AckNumber = SeqNumber + 1`.
  **Pero no siempre**: medido con el editor, `conn seq=12 → AckNumber=13` (+1) mientras que
  `transport seq=755 → AckNumber=755` y `recovery seq=754 → AckNumber=754` (**+0**).
  Un cliente robusto debe aceptar `ack == seq` **o** `ack == seq + 1`, o emparejar por orden.
  La implementación de referencia **empieza en 0**.
- El panel **retransmite respuestas idénticas**: se ha medido el mismo informe
  `1 400 AckNumber=1` llegando dos veces con 18 s de diferencia. Deduplica o te confundirás
  atribuyendo respuestas.
- Atribuye cada respuesta por `AckNumber`, no por orden de llegada: el panel contesta con
  retardos variables (de 0,4 s a más de 18 s) y hay respuestas que llegan tarde.
- Ejemplo de respuesta con propiedades (frame de 373 B):
  ```
  code: 200  AckNumber=124  ContentType=json  ContentLength=307
  body: {"OS":"Linux","version":{...},"space":40008,"brightness":100,"degree":270,
         "sn":"BYZL2611WC01CM001018","osdState":1,"mode":0,"logo":2,"timeout":60,
         "bootFinish":1,"background":["g_40mb.jpg"],"displayInSleep":1,
         "presetThemeId":0,"sleepClockId":0}
  ```

---

## 4. Comandos

### `POST conn 1` — handshake / leer propiedades
Petición **sin body**. Es lo primero que hace la app y lo repite cada ~6 s hasta que el
panel responde con `bootFinish: 1`.

```
5a0036 504F535420636f6e6e20310d0a5365714e756d6265723d32380d0a446174653d…0d0a0d0a 9b5a
```
Respuesta: `200` + las propiedades del panel (§3).

### `POST power 1` — encender / despertar
```json
{"event":"resume"}
```
Frame real (111 B): `5a006f 504F535420706f77657220310d0a…7b226576656e74223a22726573756d65227d a2 5a`
Respuesta: `200`, sin body. La app lo envía justo después de registrar el dispositivo.

### `POST brightness 1` — brillo
```json
{"value":100}          // 0–100
```
Frame real (109 B): `5a006d … 7b2276616c7565223a3130307d fc 5a` → resp. `200`.

### `POST rotate 1` — orientación
```json
{"degree":270}         // 0 / 90 / 180 / 270
```
Frame real (106 B): `5a006a … 7b22646567726565223a3237307d ab 5a` → resp. `200`.

### `STATE all 1` — datos en vivo (cada 1 s)
Ojo: el método es `STATE` y el cmd es `all`. Frame de ~737 B.
```json
{
  "network":{"upload":0,"download":1},
  "memory":{"total":32675,"used":11843,"load":36,"temperature":0,"speed":1065},
  "cpu":{"load":11,"temperature":62,"speedAverage":3775,"power":46,"voltage":1.273,"usage":8},
  "gpu":{"load":3,"temperature":29,"fan":0,"speed":76,"power":0,"voltage":0.664},
  "disk":{"total":465,"used":50,"load":10,"activity":0,"temperature":0,
          "readSpeed":0,"writeSpeed":0},
  "fans":[{"onBoard":true,"type":"Pump","name":"Fan AIO Pump","value":2566},
          {"onBoard":true,"type":"Fan","name":"Fan CPU","value":1323},
          {"onBoard":true,"type":"Chassis","name":"Fan Chassis3","value":897}],
  "motherboard":{"temperature":25},
  "timestamp":1789242061063
}
```
Respuesta: `200` sin body. Es **solo telemetría**: dibuja sobre el tema activo, no pinta nada
por sí mismo.

### `POST transport 1` + `POST transported 1` — subir una imagen (protocolo completo)

Reconstruido a partir de `cougarLCD.cpp` (`src/linux/hid_transport.cpp`), una
implementación independiente **para este mismo panel**, y verificado en su unidad. Son
**tres fases**:

**Fase 1 — anunciar el fichero:**
```json
{"type":"media","fileSize":3744,"fileName":"CFV235_fondo.png"}
```
El panel responde **`200`** y el cuerpo **debe contener `"blockMaxSize":1024`**. Si responde
`400`, se aborta y no se envía nada.

**Fase 2 — los bloques.** Se parte el PNG en trozos de **1000 bytes**, y cada trozo va en
**un informe de 1025 bytes** con esta cabecera (¡sin trama `5A`, sin escape y sin checksum!):

| Desplazamiento en el informe | Valor |
|---|---|
| `[0]` | `0x00` (report ID) |
| `[1]` | **`0x5C`** (delimitador de medios) |
| `[2..3]` | longitud BE = **21 + bytes del trozo** |
| `[4]` | `0x13` (tipo de mensaje = 19) |
| `[5..6]` | nº total de bloques (BE16) |
| `[7..8]` | índice del bloque (BE16, desde 0) |
| `[9]` | `0x02` (tipo de medio: imagen) |
| `[10..24]` | ceros |
| `[25..]` | **los datos** (hasta 1000 B) |

Cuidado con los desplazamientos: si escribes desde Python sin el byte de report ID delante,
la cabecera son **24 bytes** y los datos empiezan en el 24 del cuerpo (25 en el informe).
El total encaja justo: `1 + 3 + 21 + 1000 = 1025`.

**Fase 3 — confirmar:**
```json
{"md5":"todo","fileName":"CFV235_fondo.png"}
```
El panel responde con `{"state":"success"}`. El campo `md5` lo acepta tal cual
(`"todo"`), es decir **el firmware no valida el hash** en esta versión.

> Este es el mecanismo que usa `cougarLCD.cpp` para su dashboard «en vivo»: **renderiza un
> PNG de 1920x462 en el PC y lo sube entero, una vez por segundo**. Es la forma de mostrar
> contenido propio sin el editor de COUGAR.

#### El panel ACUSA cada subida de bloques (clave para depurar)

Capturado del log del editor en una subida **que sí funcionó** (23:30:37):

```
transport  {"type":"media","fileSize":8085,"fileName":"2026-09-12_23-30-37-227.osd"}
   -> 200  {"state":"success","blockMaxSize":1024}          (AckNumber=109+1)
   block total: 9                                           (8085 B / 1000 = 9)
   <- "1 200 AckNumber=0"      <-- ACUSE DE LOS BLOQUES: 200 = aceptados
transported {"md5":"todo","fileName":"2026-09-12_23-30-37-227.osd"}
   -> 200  {"state":"success"}                              (¡con cuerpo!)
```

En **nuestra** subida, el mismo acuse llegó como **`1 400 AckNumber=0`** y el `transported`
devolvió `200` con `ContentLength=0` (**sin cuerpo**). Es decir:

- **`1 200 AckNumber=0`** → los bloques se aceptaron.
- **`1 400 AckNumber=0`** → los bloques se rechazaron (y el `transported` vacío es el síntoma).
- Con la subida buena, `transported` devuelve **`{"state":"success"}`**; con la mala, cuerpo vacío.

Esto da un **indicador inmediato** para dar con el formato exacto de bloque (tamaño, cabecera,
índice base, prefijo del informe) probando variantes y mirando ese acuse.

#### Modelo del editor: fondo + capa OSD

Su `store.json` lo aclara:

- `devicesConfig[sn]`: `currentThemeId`, `currentThemeBg` (PNG del fondo), `transType: "osd"`.
- `customizationTheme[]`: **fondo PNG de 1920×462** (1,5 MB, se sube una vez) **+ widgets**
  (`type: "chart"`, `source: "CPU Temperature"`, `chartType: "line"`…).
- Los widgets se **renderizan en el PC a una imagen OSD** que se **re-sube continuamente**
  (ficheros `.osd` de ~8 KB, 9 bloques, 52 subidas en 5 minutos).

Es decir: el «dashboard en vivo» es **reenviar la capa OSD**, igual que concluimos con
`cougarLCD.cpp`. Los `.osd` se generan y se borran, no quedan en disco.

#### Verificado en hardware con NUESTRO cliente (2026-09-12, 23:2x)

| Paso | Resultado real |
|---|---|
| `transport {"type":"media","fileSize":3744,"fileName":"CFV235_fondo.png"}` | **`200`** + `{"state":"success","blockMaxSize":1024}` ✅ |
| 4 bloques de 1000 B (informes de 1025 con `0x5C`) | escritos sin error, pero el panel los **acusa con `1 400 AckNumber=0`** ❌ |
| `transported {"md5":"todo",...}` | **`200`** pero **`ContentLength=0`: sin cuerpo** ⚠️ (con la subida buena devuelve `{"state":"success"}`) |
| Efecto en el panel | `background` sigue en `[]` y `space` **no cambia** ❌ |

Conclusión: el handshake y la confirmación funcionan, pero **nuestro informe de bloque no lo
acepta el panel**. Falta ajustar su formato; el acuse (`200`/`400` con `AckNumber=0`) permite
iterar y saber cuándo está bien.

#### ✅ CONSEGUIDO: la subida funciona y el panel muestra nuestra imagen (2026-09-13, 00:00)

Verificado en hardware, subiendo **desde nuestro cliente y sin el editor**: el panel pasó a
mostrar la imagen de prueba (magenta con franjas azules) y su estado lo confirma:

```
transported -> 200 {"state":"success"}
"background": ["2026-09-13_00-00-18-837.png"]     <- nuestro fichero, subido por nosotros
"space": 81756 -> 81752                            <- el fichero se guardó
```

**Lo que faltaba no era el formato del bloque, sino el TIEMPO.** El panel abre una *sesión de
transferencia* al contestar al `transport` y **caduca en menos de un segundo**:

| Paso | Editor (funciona) | Nosotros (fallaba) | Nosotros (ahora) |
|---|---|---|---|
| `transport` → primer bloque | **87 ms** | **8 s** ❌ | ~90 ms ✅ |
| último bloque → `transported` | **6 ms** | **3 s** ❌ | ~10 ms ✅ |

Con la sesión caducada, el panel acusa los bloques con **`1 400 AckNumber=0`** y el
`transported` devuelve **200 con `ContentLength=0`** (sin cuerpo). Es decir: el `200` sin
cuerpo NO significa éxito, significa «no he aceptado nada». Con la subida buena devuelve
`{"state":"success"}`.

#### Dos capas: fondo y OSD (por qué «queda encima lo anterior»)

El panel **compone dos capas**, y de ahí sale un efecto que confunde mucho:

| Capa | Tipo en el informe (`[9]`) | Quién la usa |
|---|---|---|
| **Fondo** | `0x02` | el tema del editor la sube **una vez** |
| **OSD** (encima) | `0x01` | el editor la **re-sube sin parar** (52 veces en 5 min): ahí van los widgets |

El campo `osdState` de las propiedades dice si esa capa superior está **activa**:

- `osdState: 1` → hay una capa OSD dibujándose **por encima**. Si subes una imagen de pantalla
  completa como fondo (`0x02`), **se verá con la capa OSD anterior encima** (medido: subimos
  varias imágenes y quedaban restos de la anterior).
- `osdState: 0` → no hay capa superior y la imagen se ve sola.

**Cómo dejar el panel limpio para tu imagen o dashboard:**

1. `POST recovery {"enable":true}` → el Reset: borra los medios, deja `background: []` y pone
   **`osdState: 0`** (medido: `space` sube de 81564 a 81756).
2. Sube tu imagen (`subir`), que queda como fondo.
3. **No apliques después un tema del editor**: lo volvería a poner en `osdState: 1` y su capa
   OSD se dibujaría encima.

Si el panel ya está en `osdState: 1` y no quieres reiniciarlo, la alternativa es subir la
imagen **como capa OSD** (`[9]=0x01`) para sustituir lo que hay en esa capa.

#### Comandos que SÍ funcionan con cuerpo (y el apagado por espera)

Barrido hecho el 2026-09-13 probando los comandos que antes solo habíamos tanteado **sin**
cuerpo (sin cuerpo dan `400` y eso no informa de nada: en este protocolo casi todo exige cuerpo):

| Comando | Resultado | Efecto medido |
|---|---|---|
| **`POST displayInSleep {"enable":true\|false}`** | **200** | **cambia `displayInSleep` (1 ↔ 0)** |
| `POST realtimeDisplay {"enable":true}` | **200** (sin cuerpo: `400`) | no cambia ninguna propiedad: **efecto desconocido** |
| `POST conn {"enable":true}` | 200 | devuelve las propiedades, igual que sin cuerpo |
| `POST config` ({} o `{"enable":true}`) | 400 | no existe en este firmware |
| `POST sysinfoDisplay {"enable":true}` | 400 | no existe |
| `POST fanLCDSet {...}` | **silencio** | o no existe, o pide otro cuerpo |
| `POST waterBlockScreen`, `waterBlockScreenId`, `mediaDelete`, `media`, `GET waterBlockScreen`, `STATE waterBlockScreen`, `DELETE conn` | **silencio** | no hay forma de listar ni borrar medios |

##### `displayInSleep` y el apagado de la pantalla

**`POST displayInSleep {"enable":false}` es la forma de que el panel no se apague por espera.**

Medido: con `displayInSleep: 0`, tras **4 minutos sin ningún tráfico de host**, el panel seguía
en `brightness: 100` y con su `background` intacto. Antes (con el valor por defecto `1`) la
pantalla se apagaba y el panel reportaba **`brightness: 0`** — de ahí venía la sensación de que
«se apagó solo».

Consecuencia práctica: **para dejar una imagen o un tema fijo no hace falta ningún bucle de
mantenimiento**; basta con poner `displayInSleep` en 0 una vez y subir la imagen. El flujo
`STATE all` sigue siendo necesario si quieres que la pantalla se actualice (tema animado), pero
no para que se quede encendida.

#### Formatos de imagen: qué dibuja el panel (probado)

| Formato | ¿Lo acepta? | ¿Lo muestra? |
|---|---|---|
| **PNG** | ✅ `{"state":"success"}` | ✅ **sí** (es el que usamos siempre) |
| **GIF** (incluso animado) | ✅ `{"state":"success"}`, 34 bloques | ❌ **pantalla blanca** |
| JPEG | no probado | — |

Medido el 2026-09-13: se generó un GIF animado de 8 cuadros (1920×462, 34 KB, con Pillow) y el
panel **lo aceptó como fondo** (`background: ["animada_025446.gif"]`, `space` bajó, `transported`
→ `success`) pero **mostró la pantalla en blanco** ✗. Conclusión: **para mostrar algo, PNG**; el
GIF no sirve para animar.

Dato relacionado: el selector de ficheros del editor usa `accept="image/*"`, así que **no deja
elegir un `.mp4`** (por eso su interfaz solo ofrece «background»), pero un `.gif` **sí** lo deja
elegir (es `image/gif`). Aun así, por lo medido arriba, no esperes que el panel lo anime.

**Para animar de verdad** la única vía que funciona es **reenviar fotogramas PNG**: los modos
`bucle` (tema animado) y `stream` (reflejo de pantalla) suben una imagen nueva cada 1-2 s.

#### `realtimeDisplay`

`POST realtimeDisplay {"enable":true}` → `200`. Efecto observado: **deja la capa OSD activa**
(`osdState: 1`), que es la capa que se dibuja **encima** del fondo. Con `{"enable":false}`
responde `200` pero en las pruebas **`osdState` no volvió a 0**; para dejarlo limpio lo fiable es
un `recovery` (Reset). No se le ha encontrado otra utilidad.

#### Cuidado con las lecturas: el panel a veces contesta de más

Dos avisos que nos han confundido más de una vez:

- A veces responde **`200` con cuerpo vacío** (sin el JSON de propiedades). Si `conn` no trae
  campos, **repite la lectura** antes de sacar conclusiones.
- Emite **frames extra** (retransmisiones, acuses de sesión, `AckNumber=0`). Si emparejas por
  orden de llegada en vez de por `AckNumber`, atribuirás respuestas a comandos equivocados —
  nos pasó con `realtimeDisplay`, que parecía devolver un `blockMaxSize` que no era suyo.

#### Dónde va cada imagen: fondo (`0x02`) o capa OSD (`0x01`)

Medido con el panel real (6 subidas de ~30 KB en cada caso, contando el `space`):

| Tipo | Nombres | `space` | Conclusión |
|---|---|---|---|
| `0x02` fondo | mismo nombre | **-12 KB** | reutiliza el fichero |
| `0x02` fondo | nombres distintos | **+1004 KB** | acumula (el panel libera después, con retraso) |
| **`0x01` capa OSD** | nombres distintos | **-16 KB** | ✅ **reutiliza su hueco: no ocupa** |

Por eso el editor puede subir la capa OSD **2 veces por segundo** sin llenar el panel, y por eso
**los modos continuos (bucle y stream) deben ir como capa OSD** (`[9]=0x01`). Medición de
referencia con 4 fotogramas seguidos del bucle ya como OSD: **-76 KB (≈ 2-19 KB por fotograma)**,
frente a los **~170 KB por fotograma** de acumular fondos. La contabilidad del panel es perezosa
(libera con retraso), así que el número baila, pero el orden de magnitud es claro. Los clientes ya
lo hacen así por defecto en esos modos (`usar_capa_osd()` en Python, `COUGAR_TIPO=1` en el Node);
para una imagen fija se sigue usando el fondo (`0x02`).

#### Formato REAL del informe de bloque, capturado del editor (2026-09-13, 02:5x UTC)

Obtenido instrumentando el propio editor: se parcheó **en caliente** su módulo `node-hid`
(con el inspector de Electron, `Debugger.setScriptSource`) para que registre cada informe que
escribe al panel. 1865 escrituras capturadas, 1786 de ellas informes de medios.

```
00 5C <len BE = 21 + trozo> <[4]> <n bloques BE> <indice BE> <[9]> 00 00 ... 00 <datos>
 ^^ ^^                       ^^^^  ^^^^^^^^^^^   ^^^^^^^^^^  ^^^^^  ^^^^^^^^^^  ^^^^^^^
 ID delim                      |                                |    15 bytes   el PNG
                               |                                +-- 0x02 imagen de fondo
                               |                                    0x01 capa OSD
                               +-- contador que INCREMENTA por informe
```

Ejemplo real (primer bloque de un fondo de 3380 B, 4 bloques):

```
005c03fd 16 0004 0000 02 00×15 89504e470d0a1a   <- firma PNG en los datos
  ^^^^   ^  ^^^^ ^^^^ ^^        ^^^^^^^^^^^^
  1021   0x16  4    idx0 tipo2   los datos son el PNG tal cual
```

| Diferencia con lo que teníamos | Editor | Nosotros |
|---|---|---|
| `[4]` | **0x16** (fondo) / 0x18 (OSD) — y **va incrementando** | 0x13 fijo |
| `[9]` | 0x02 fondo / 0x01 OSD | 0x02 |
| **Último bloque** | **SIN relleno** (769 B para 744 de datos) | rellenado a 1025 |
| resto (delimitador, longitud, contador, índice, datos en el 25) | — | igual ✅ |

También se ve que el editor **está subiendo la capa OSD sin parar** (un `transport` +
`transported` cada ~0,5 s) mientras está abierto: es su «dashboard en vivo».


| Prueba | Resultado |
|---|---|
| 8 variantes del informe de bloque con nombres **`.osd`** | `transported` → **`{"state":"success"}`** en las 8 (¡el formato base era correcto!) |
| El **mismo PNG** con nombre `.osd` | **aceptado** ✅ |
| El mismo PNG con nombre `.png` (y con marca de tiempo) | **rechazado** (cuerpo vacío) ❌, con las 8 variantes de bloque |
| El editor sube a las 23:29:45 **exactamente el mismo fichero** (`CFV235_fondo.png`, 3744 B, mismo cuerpo JSON) | **aceptado** ✅ |
| Imagen magenta subida como `.osd` y luego mantener el flujo 90 s | el panel **se enciende y muestra el tema del editor**, pero **no nuestra imagen** ❌ |

Dos hechos que cambian el planteamiento:

1. **El panel se apaga si no recibe flujo.** Con `STATE all` cada segundo (90 tramas, todas
   `200`) se mantiene encendido y mostrando su tema: por eso el editor no para de enviar
   telemetría. Nuestras órdenes sueltas no bastan.
2. **El estado de tema es transitorio y lo mantiene el host**: al cerrar el editor,
   `osdState` volvió a `0` y `background` a `[]`.

**La conclusión incómoda pero clara:** con una petición idéntica y el mismo fichero, el
editor consigue que el panel lo acepte y nosotros no. Como el cuerpo de la petición es el
mismo, la diferencia está **en los bytes de los bloques** — y esos **el editor no los escribe
en su log** (solo registra las peticiones, no los informes de medios). El repositorio de
GitHub tampoco los aclara para este caso: su formato (que es el nuestro) sirve para la capa
OSD, y aquí el panel lo rechaza para el fondo.

**Cómo cerrarlo:** ~~capturar los informes con USBPcap~~ **RESUELTO**: se capturaron
instrumentando el propio editor (ver arriba). Se necesitaba capturar los informes que escribe
a nivel de aplicación, no de USB. En Windows se hace con **USBPcap + Wireshark** (USBPcap
instala un filtro de
controlador de USB y requiere administrador; el equipo lo tiene). Con eso se leen los
informes de 1025 B byte a byte y se copia el formato exacto.

Es decir: el **transporte funciona** (extremo a extremo, sin errores), pero con esto solo
**no se consigue que el panel muestre la imagen**. Falta el paso con el que el editor
«selecciona» el medio (probablemente la secuencia de tema: sus ficheros + `recovery` +
`rotate`). Ojo: en los dos logs del editor **no llegó a enviarse ni un solo bloque**, así que
tampoco tenemos su versión del camino completo.

#### Comandos que este firmware NO implementa

Probados con el panel **sano** (`bootFinish: 1`): `GET waterBlockScreen`, `POST
waterBlockScreen`, `POST waterBlockScreenId`, `DELETE mediaDelete` (con `{"path":...}` y
`{"fileName":...}`) → **`400`** todos. La gestión de medios del panel se reduce, por lo que
hemos visto, a **subir** y a **`recovery`** (que es el Reset).

#### `recovery {"enable":true}` es el Reset **y reinicia el panel**

Medido: responde `200` y a continuación **el dispositivo desaparece del USB unos segundos**
(y entonces cualquier escritura falla con «Cannot write to hid device»). Al volver, el panel
queda limpio:

| Campo | Antes | Después |
|---|---|---|
| `background` | `["g_40mb.jpg"]` | **`[]`** |
| `space` | 40108 | **81760** |
| `osdState` | 1 | 0 |
| `displayInSleep` | 1 | 0 |
| `bootFinish` | 1 | **1** |

Esto es lo que **limpió definitivamente** el fichero que había atascado el panel.

#### Frame de error espontáneo

El panel envía de vez en cuando `1 400` con **`AckNumber=0`**, sin que corresponda a ninguna
petición. Es el origen de los `400` con `Ack=0/1` que aparecían «sueltos» al sondear.


---

## 5. Estados y avisos observados

- **`bootFinish: 1` es imprescindible.** Mientras el panel reporta `bootFinish: 0`, el editor
  repite `conn` indefinidamente y **no registra** el dispositivo. En las 5 ocasiones en que
  sí lo registró, `bootFinish` era `1`.
- **`logo: 2`** = el panel está mostrando su pantalla de logo (reposo). No es una avería: es
  lo que muestra cuando no ha recibido ningún tema válido.
- **`space`** ≈ 40 000 (KB libres) y `background: ["g_40mb.jpg"]` con solo el fichero de fábrica.
- **Rechazo `400` en `transport`**: en el caso medido, 977 rechazos frente a ~0 aceptaciones
  reales; el mismo fichero de 3518 B se aceptó y se rechazó sin patrón de tamaño. Los pocos
  aceptados llegaban tras **huecos de inactividad** (media 11,5 s) frente a los rechazados
  (media 2,9 s) → apunta a un problema de *flow control* del firmware del panel, no del
  cliente.
- El panel **nunca** apareció como dispositivo ADB: `adb devices` solo listó un móvil aparte.

---

## 6. Códigos de respuesta del panel (medidos)

| Comando | 200 | 400 |
|---|---|---|
| `STATE all` | 349 | 0 |
| `conn` | 40+ | 0 |
| `power` | 2 | 0 |
| `brightness` / `rotate` | 1 / 1 | 0 |
| **`transport`** | 0 fiables | **292+** |

---

## 7. Vocabulario completo extraído del binario del editor

Extraído del bytecode de `out/main/index.jsc` (cadenas del *request factory*). No son
deducciones: son los literales que usa la app.

### Métodos
```
GET | POST | STATE | DELETE
```

### Cabeceras de mensaje
```
SeqNumber | AckNumber | ContentLength | ContentType
FileName | FileBlockId | FileSize | ContentRange | Counter | Option
```
El bloque `FileName / FileBlockId / FileSize / ContentRange` es el **protocolo de subida por
bloques**: cada bloque lleva su identificador y su rango dentro del fichero. `Counter` y
`Option` aparecen junto a ellos.

### Tipos de contenido
```
text | json | xml | png | jpg | gif | mp4 | avi | pdf
```

### Comandos (`cmd`)
```
conn              waterBlockScreen     waterBlockScreenId
brightness        rotate               recovery
sysinfoDisplay    displayInSleep       fanLCDSet
mediaDelete       config               power
realtimeDisplay
```
Si llega un comando desconocido, el editor registra `unknown cmd:`.

### API interna del editor (equivalencias útiles)
`waterBlockScreen()` expone en la interfaz: `brightness`, `rotate`, `recovery`, `media`,
`defaultMedia`, `mediaSave`, `mediaFrameGet`, `mediaInfoGet`, `mediaImageAdd`,
`mediaVideoAdd`, `mediaDelete`, `id`, `list`, `enable`, `custom`, `customScreenMode`,
`customScreenSplitting`, `displayInformations`, `displayInSleepMode`, `fan`.

**Borrado de medios** (código real de la interfaz):
```js
const e  = await waterBlockScreen().media()      // lista: cada item tiene .path y .dpi
await waterBlockScreen().mediaDelete(item.path)  // borra por PATH
```

**Recuperación** (botón `Reset` de la pestaña Screen):
```js
await waterBlockScreen().recovery(currentDevice)
```

> Nota: el emparejamiento exacto de **método + cmd + body** de cada operación vive en la
> lógica compilada (bytecode V8), no en cadenas legibles, así que no se puede extraer
> estáticamente. Se descubre probando: usa el modo `raw` del cliente Python.

---

## 8. Panel atascado tras subir un fichero mayor que su memoria

Síntoma y firma medidos en este caso:

| Señal | Valor |
|---|---|
| `space` | bajó de **40128** a **40008** KB tras la subida |
| `bootFinish` | clavado en **0** — el arranque no se completa |
| `transport` | **400** en 977 de 977 intentos |
| `logo` | **2** (pantalla de respaldo) |
| `background` | solo `["g_40mb.jpg"]` |

El panel queda con su servicio de ficheros colgado y **rechaza todo lo que implique
escritura**, pero sigue atendiendo `power`, `conn` y `STATE` con `200`.

**Estado medido en este equipo (2026-09-12, 22:2x) con el editor cerrado:**

| Señal | Valor |
|---|---|
| `conn` | **200** con el JSON completo (¡el panel sí responde!) |
| `bootFinish` | **0** — sigue sin completar el arranque |
| `space` | `40008` |
| `background` | `["g_40mb.jpg"]` |
| `logo` | **2** (pantalla de logo: es lo que se ve, fondo negro con el símbolo COUGAR) |
| `brightness` / `degree` | `100` / `270` |
| `POST power {"event":"resume"}` | **200** en 0,4 s (no cambia `bootFinish`) |
| `POST transport` | **400** |
| `DELETE mediaDelete` | **400** |
| `GET *`, `POST sysinfoDisplay`, `POST realtimeDisplay` | **400** |

Con `bootFinish: 0` el editor **no registra** el dispositivo → la pestaña Screen sale vacía
y los controles en gris, y el panel se queda mostrando solo su logo. Es exactamente el
cuadro que se describe arriba.

Secuencia de rescate sugerida:

```
1. POST   conn                                          (confirmar bootFinish)
2. POST   recovery  {"enable":true}                     (equivale al Reset del editor)
3. POST   conn                                          (¿bootFinish = 1?)
4. GET    waterBlockScreen                              (listar medios del panel)
5. DELETE mediaDelete                                   (borrar el fichero culpable)
6. POST   power  {"event":"resume"}                     (despertar)
7. POST   config                                        (si hace falta, volver a valores de fabrica)
```

### 8.1 Lo que se ha medido al intentarlo (2026-09-12, 22:2x-22:5x)

> ### ✅ RESUELTO — 2026-09-12, 23:08: corte de energía real
>
> | Hora | Hecho |
> |---|---|
> | 23:08:12 | arranca el PC tras apagarlo **más de 30 s** |
> | 23:08:52.676 | el editor (autoarrancado por su tarea `StartCLE`) manda el primer `conn` |
> | **23:08:53.415** | **`bootFinish: 1`** — el panel arrancó a la primera |
> | desde entonces | el editor emite `STATE all` a 1/s sin interrupción → **panel registrado** |
>
> Queda demostrado que **el único desatascador es el corte de energía**: el desenchufe del
> USB no sirve porque **no le quita la alimentación** (el panel se alimenta de la fuente de
> la caja). Una vez arrancado, se registra solo con que haya un host sondeando.

| Intento | Resultado |
|---|---|
| **Corte de energía real (apagar el PC >30 s)** | ✅ **`bootFinish: 1` en 40 s** y panel registrado |
| `POST recovery` (sin cuerpo) | **400** |
| `POST recovery {"sn":"BYZL…"}` | **400** |
| **`POST recovery {"enable":true}`** | **200** ✅ — **este es el formato correcto**, extraído del log del editor (`ContentLength=15`). Aun así **no cambia `bootFinish`** |
| `POST power {"event":"restart"}` / `reboot` / `reset` / `reload` | **400** — el único evento válido es `resume` |
| `DELETE mediaDelete`, `GET *`, `sysinfoDisplay`, `realtimeDisplay`, `transport` | **400** |
| `brightness`, `rotate`, `power resume`, `conn` | **200** |
| 20 ciclos de «abrir dispositivo + conn» (imitando al editor) | `bootFinish` se queda en **0** |
| Reenchufe del USB (33 s desconectado) | `space` vuelve de 40008 a **40128**, pero `bootFinish` sigue en **0** |

**Cómo se sabe que el panel arranca con un ciclo de energía real** (log del editor,
`2026-9-12.log`):

- 21:54:43-45 — el panel está **registrado y sano**: `STATE all` cada segundo, secuencias
  1529-1531, todo `200`.
- 21:54:45 — «water block screen status changed: **disconnected**» (el panel se desconecta).
- 21:55:30 a 21:56:39 — 12 `conn` seguidos, **todos con `bootFinish: 0`**.
- **21:56:45 — `bootFinish: 1`**, y justo después `power` + el torrente de `STATE all`.

O sea: tras un corte de energía **real**, el panel tarda del orden de **60-80 s** en
completar el arranque, y mientras tanto contesta `conn` con `bootFinish: 0`. Insistir con
`conn` es necesario, pero **no basta si el panel no ha perdido la alimentación**: el
desenchufe del USB no lo apaga (se alimenta de la caja/fuente), y por eso `bootFinish` se
quedó en 0 durante más de 10 minutos y 20 ciclos de sondeo.

**Recomendación:** apagar el PC por completo (o cortar la alimentación de la caja) unos
30 s y volver a encender. Después, sondear `conn` cada 6 s hasta ver `bootFinish: 1`.

### 8.2 Hipótesis descartadas (no gastes tiempo en ellas)

| Hipótesis | Prueba | Resultado |
|---|---|---|
| «Hay que reabrir el dispositivo en cada ciclo, como el editor» | 20 ciclos de abrir+Cerrar+`conn` | `bootFinish` sigue en 0 |
| «El tamaño del informe importa» | escritura exacta, 64 B y 1024 B (variantes A/B/C/D) | **3/3 respuestas con los tres tamaños**: no importa |
| «Basta con insistir con `conn`» | 25 sondeos del editor + 16 del cliente | ningún cambio |
| «`power` acepta un evento de reinicio» | `restart`, `reboot`, `reset`, `reload` | **400** en los cuatro |
| «Lanzar el proceso principal de la app en modo Node arranca el panel» | `ELECTRON_RUN_AS_NODE=1` + `--print-bytecode -e require(...)` (el comando que se había atribuido el `bootFinish: 1` de las 21:56:45) | a los 50 s y a los 130 s, **`bootFinish` sigue en 0**; además no volcó nada (0 bytes) |

**Atribución del `bootFinish: 1` de las 21:56:45:** coincidió en el tiempo con el lanzamiento
del editor (21:55:25), pero el editor *normal* tampoco lo consigue (25 `conn` seguidos con
`bootFinish: 0` a las 22:50-22:52). Lo único distinto en aquella ventana fue que el panel
**se había desenchufado y vuelto a enchufar** justo antes (21:54:45 «disconnected» → presente
a las 21:55:30). Es decir: el panel arrancó por el ciclo de energía, no por la sesión del
editor, y tardó del orden de 75 s en completarlo.
