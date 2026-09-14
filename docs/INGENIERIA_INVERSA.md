<!-- Procede del banco de pruebas de Windows (equipo donde se hizo la ingenieria
     inversa del editor de COUGAR). Las rutas que menciona son de aquel equipo. -->

> **Que aporta este documento.** El *metodo* y la *historia*: de donde salio cada dato
> del protocolo (el log del editor, las capturas del inspector, los experimentos que
> fallaron y por que). `docs/CANAL.md` dice que se midio; este dice como se llego a
> medirlo, que es lo que hace falta para repetirlo o para entender una duda vieja.

# Ingeniería inversa del panel CFV235 — estado

Panel: **COUGAR CFV235 LCD** (9,16", 1920×462) · USB HID `1D6B:0126` · firmware V1.0.5,
SDK V1.2.7, hardware V2.0 · serie `BYZL2611WC01CM001018`.

Fecha del estado: **2026-09-12**. Todo lo «verificado» está comprobado contra bytes reales
del panel o contra el log del editor 1.0.14; lo «inferido» se marca como tal.

---

## 1. Resumen

| Área | Estado |
|---|---|
| Trama (framing), escape y checksum | ✅ **verificado byte a byte** |
| Informe HID (1025 B) y tamaños | ✅ verificado |
| Comandos y cuerpos (`conn`, `power`, `brightness`, `rotate`, `recovery`) | ✅ verificado |
| Telemetría `STATE all` | ✅ formato completo capturado |
| **Subida de imágenes (`transport` → bloques → `transported`)** | ✅ **COMPLETA y verificada en hardware**: formato exacto capturado del editor (instrumentando su `node-hid` con el inspector de Electron) **y** la clave del tiempo: la sesión caduca, así que los bloques van 87 ms después del `transport` y el cierre 6 ms después |
| Flujo del editor al arrancar y registrar | ✅ reconstruido del log |
| **`bootFinish`: qué lo pone a 1** | ✅ **RESUELTO**: un **corte de energía real** del panel (apagar el PC/caja >30 s) con un host sondeando `conn`. Confirmado el 2026-09-12 a las 23:08:53 (`bootFinish: 1` a los 40 s y panel registrado). El reenchufe del USB **no** sirve: no le quita la alimentación |
| Cómo se limpia el panel | ✅ **RESUELTO**: `POST recovery {"enable":true}` es el Reset; reinicia el panel y deja `background: []` con el `space` al máximo |
| `mediaDelete` y listado de medios | ❌ **no existen en este firmware**: `waterBlockScreen`, `waterBlockScreenId`, `mediaDelete` → `400` con el panel sano |
| **Que el panel MUESTRE una imagen subida** | ✅ **CONSEGUIDO** el 2026-09-13 a las 00:00: el panel muestra la imagen de prueba subida **desde nuestro cliente, sin el editor** (`background: ["2026-09-13_00-00-18-837.png"]`, `transported` → `{"state":"success"}`) |
| Formato del fichero de subida | ✅ son **PNG tal cual** (se ve la firma `89504e470d0a1a` desde el byte 25 del informe) |

**Estado a 2026-09-13: OBJETIVO CUMPLIDO Y SUPERADO.** El panel arranca (tras un corte de
energía real), se limpia con `recovery`, acepta imágenes nuestras y las muestra, y **se le
puede poner un dashboard propio con métricas reales del PC** (`dashboard/`), todo desde
nuestro código y **sin el editor de COUGAR**. Detalle del protocolo en `PROTOCOLO.md` §4 y
evidencia cruda en `capturas/`.

---

## 2. Lo verificado (referencia rápida)

### 2.1 Trama

```
informe : 00 | 5A | len(BE16) | payload_escapado | checksum | 5A | ceros hasta 1024
          ^^   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^
     report ID              la trama                              relleno
```

- **Escape**: `0x5A → 5B 01`, `0x5B → 5B 02`, aplicado **al payload, a los dos bytes de
  longitud y al checksum**.
- **`len` = payload_sin_escapar + 5**. No cuenta los bytes de código del escape: medido
  `len = 371` con **373 bytes** en el cable (`tramas_reales/respuesta_conn_1.bin`).
- **`checksum` = `(byte_alto + byte_bajo + suma(payload)) & 0xFF`** — se suman los **bytes**
  de longitud, no el valor `len`. Verificado: `probar_tramas.py` da OK con esta fórmula y
  **falla** con la anterior.
- **Fin de trama por el `0x5A` de cierre, nunca por `len`** (el payload no puede contener
  `0x5A` crudo). Cortar por `len` descarta la respuesta entera y en silencio.
- `SeqNumber` empieza en **0**; la respuesta trae `AckNumber = SeqNumber + 1`.
- El panel **retransmite respuestas idénticas** (medido: la misma trama repetida 18 s después).

### 2.2 Comandos confirmados

| Comando | Cuerpo | Respuesta |
|---|---|---|
| `POST conn` | *(sin cuerpo)* | `200` + JSON de propiedades (307 B de contenido) |
| `POST power` | `{"event":"resume"}` | `200` (única variante válida: `restart`/`reboot`/`reset`/`reload` → `400`) |
| `POST brightness` | `{"value":0..100}` | `200` |
| `POST rotate` | `{"degree":0/90/180/270}` | `200` |
| `POST recovery` | `{"enable":true}` | `200` |
| `STATE all` | telemetría (JSON, §2.4) | `200`, cada ~1 s |
| `POST transport` | `{"type":"media","fileSize":N,"fileName":"x"}` | `200` con `"blockMaxSize":1024` |
| *(bloques de medios)* | informe de 1025 B con `0x5C` | — |
| `POST transported` | `{"md5":"todo","fileName":"x"}` | `{"state":"success"}` |

### 2.3 Subida de imágenes (3 fases)

1. `transport` con `type`/`fileSize`/`fileName`; se exige `200` con `blockMaxSize`.
2. El fichero en trozos de **1000 B**, cada uno en un informe de 1025 B:
   `[0]=0x00` `[1]=0x5C` `[2..3]=21+trozo (BE)` `[4]=0x13` `[5..6]=nº bloques`
   `[7..8]=índice` `[9]=0x02` `[10..24]=ceros` `[25..]=datos`.
   Sin trama, sin escape y sin checksum: es binario crudo.
3. `transported` con `{"md5":"todo","fileName":"x"}` → `{"state":"success"}`.

Implementado en `cougar_panel.py subir` y en `sondas/cougar_hid_node.js subir`.

### 2.4 Telemetría `STATE all`

```json
{"network":{"upload":0,"download":3},
 "memory":{"total":32675,"used":8131,"load":24,"temperature":0,"speed":1065},
 "cpu":{"load":4,"temperature":43,"speedAverage":3700,"power":53,"voltage":0.982,"usage":3},
 "gpu":{"load":6,"temperature":34,"fan":0,"speed":654,"power":0,"voltage":0.61},
 "disk":{"total":465,"used":60,"load":13,"activity":0,"temperature":0,"readSpeed":0,"writeSpeed":0},
 "fans":[{"onBoard":true,"type":"Pump","name":"Fan AIO Pump","value":2571},
         {"onBoard":true,"type":"Fan","name":"Fan CPU","value":786},
         {"onBoard":true,"type":"Chassis","name":"Fan Chassis3","value":584}],
 "motherboard":{"temperature":25},
 "timestamp":1789250082699}
```
**Dibuja el panel**, no el PC: la telemetría es solo datos.

### 2.5 Propiedades (`conn`)

```json
{"OS":"Linux","version":{"app":"V1.0.5","firmware":"V1.0.5","sdk":"V1.2.7","hardware":"V2.0"},
 "space":40128,"brightness":100,"degree":270,"sn":"BYZL2611WC01CM001018","osdState":1,
 "mode":0,"logo":2,"timeout":60,"bootFinish":0,"background":["g_40mb.jpg"],
 "displayInSleep":1,"presetThemeId":0,"sleepClockId":0}
```

---

## 3. Flujo del editor (lo que hace la app, medido en su log)

```
arranque → ApiInit (sysinfo, hwinfo ~2,5 s, serialPort, store) → tray
   ↓
POST conn cada ~6 s  ─── hasta que la respuesta trae bootFinish: 1   (12 sondeos = ~75 s)
   ↓
POST power {"event":"resume"}
   ↓
STATE all cada ~1 s   (la pestaña «Screen» ya está viva)
   ↓
al aplicar un tema:  «切换主题 <id>»
   ├─ transport de ficheros .osd (≈45 KB, nombre con marca de tiempo)
   ├─ POST recovery {"enable":true}
   └─ POST rotate {"degree":270}
```

`space` bajó de **40128** a **40008** tras una subida (≈120 KB), y el panel pasó a
`bootFinish: 0`: ése es el rastro del fichero que lo atascó.

---

## 4. Lo que no se sabe (y cómo atacarlo)

| Incógnita | Por qué importa | Cómo resolverla |
|---|---|---|
| **Qué pone `bootFinish` a 1** | es la puerta de todo: sin eso el editor no registra y `transport` da `400` | ciclo de energía real + sondeo `conn`; si se reproduce, comparar el log del editor segundo a segundo |
| `mediaDelete` / listado de medios | borrar el fichero culpable sin formatear | probar cuando el panel arranque: `GET waterBlockScreen`, `DELETE mediaDelete`, `POST mediaDelete` con `{"path":...}`, `{"fileName":...}`, cabecera `FileName=` |
| Formato del `.osd` | entender lo que sube el editor | capturar los bytes de un `transport` del editor (fichero temporal en su directorio de trabajo) y compararlos con el PNG origen |
| Nombres de los medios (`g_40mb.jpg`) | saber qué borrar | `GET waterBlockScreen` cuando responda |
| Significado de `osdState`, `mode`, `logo`, `presetThemeId`, `sleepClockId` | control fino (OSD, modo, reloj de reposo) | barrido de comandos con cuerpo + lectura de propiedades antes/después |
| Índice del tema (`切换主题 <id>`) | reproducir el tema sin el editor | está en `store.json` del editor; el envío real es el `transport` de sus ficheros |

---

## 4.bis Medido el 2026-09-12 por la noche (analizador corregido)

Al arreglar `analizar_log.py` (validaba con la regla **vieja** del checksum y exigía
`len == longitud`, así que descartaba en silencio todas las tramas con escapes) aparecieron
**5183 mensajes válidos** en el log grande, frente a los 336 que se habían validado antes:

| Dato | Valor |
|---|---|
| `POST transport` enviados | **1284** |
| `transport` → **400** | 1209 |
| **`transport` → 200** | **16** ← no eran cero, como creíamos |
| `STATE all` → 200 | 823 |
| `POST conn` → 200 | 349 |
| peticiones sin respuesta | `transport` 59, `STATE all` 276, `conn` 14 |
| **bloques de medios enviados por el editor** | **0** (ni una escritura con el marcador `00 5C` en ninguno de los dos logs) |

Conclusiones que cambian el diagnóstico:

1. **El handshake de subida no está roto, es una carrera del firmware**: 16 de 1284 se
   aceptaron. La hipótesis de «flow control por tiempos» queda **descartada**: el silencio
   previo de los aceptados (0,21-0,97 s) es igual que el de los rechazados (~0,65 s).
2. **El editor nunca completó una subida**, ni siquiera cuando el panel aceptó el handshake:
   no hay ni un bloque en el log. Por eso el formato de bloques sólo lo tenemos de la
   implementación de referencia y **no está verificado contra este panel**.
3. **`AckNumber` no es siempre `SeqNumber + 1`**: medido +1 en `conn` y **+0** en `transport`
   y `recovery`. Los clientes ya aceptan las dos convenciones.

---

## 5. Métodos: qué funcionó y qué no

**Funcionó**

1. **Minar el log del editor** (13 MB, `%APPDATA%\cougar_lcd_editor\logs\`): es la fuente más
   rica — trae la trama completa en hex, la respuesta, y el estado interno de la app.
   De ahí salieron el framing, los cuerpos de los comandos, `recovery {"enable":true}` y el
   flujo de arranque.
2. **HID directo desde Windows con `node-hid`** (módulo extraído del propio editor) — único
   modo de hablar con el panel sin la app.
3. **Contrastar con una implementación independiente del mismo panel** (`cougarLCD.cpp`):
   corrigió nuestra fórmula del checksum y nos dio el protocolo de subida.
4. **Pruebas de regresión con tramas reales** (`probar_tramas.py` + `tramas_reales/`).
5. **Comparar estados** (sano vs atascado) para aislar la puerta `bootFinish`.

**No funcionó (no repetir)**

1. **`NODE_OPTIONS` para enganchar `node-hid` dentro del editor**: Electron empaquetado lo
   ignora; el fichero de captura nunca se creó.
2. **Volcar el bytecode** (`--print-bytecode` con `ELECTRON_RUN_AS_NODE=1`): el `main` va como
   **caché de bytecode de V8**, no como fuente, así que la salida es **vacía** (0 bytes,
   comprobado). El código del main no es recuperable por esta vía.
3. **ADB**: el panel no es un dispositivo ADB.
4. **Adivinar comandos sin cuerpo**: dan `400` y llevan a conclusiones falsas (el cuerpo es
   obligatorio en casi todos).
5. **Concluir «no responde» a partir de un timeout**: el panel contesta con retardos muy
   variables (0,4 s a >18 s) y retransmite; hay que emparejar por `AckNumber`.

---

## 6. Aportación de cada fuente

| Hallazgo | Fuente |
|---|---|
| Framing, escape, checksum (regla simple), comandos, telemetría, propiedades, flujo del editor, `recovery {"enable":true}` | log del editor 1.0.14 |
| Protocolo de subida (3 fases + bloques `0x5C`), **corrección del checksum**, informe 1025 B, escape también en longitud/checksum, `SeqNumber` desde 0 | **`cougarLCD.cpp`** (`docs/PROTOCOL.md`, `src/linux/hid_transport.cpp`) |
| Que `len` ≠ bytes del cable y el fallo del parser que ocultaba las respuestas | medición propia (`probe4`-`probe7`) + contraste con la fuente anterior |
| Retransmisiones, latencias, mapa acepta/rechaza, comportamiento de `bootFinish` | medición propia contra el panel |
| Capa de sensores (hwmon, thermal, CPUFreq, powercap/RAPL) y empaquetado systemd/udev | `poseidon-linux-display` (otro dispositivo; se reutiliza la técnica, no el protocolo) |

---

## 7. Próximos pasos, en orden

1. **Ciclo de energía real del panel** (apagar el PC o la fuente, ~30 s). Es lo único que ha
   funcionado alguna vez. Después, `conn` cada 6 s hasta `bootFinish: 1`.
2. Con el panel arrancado: **subir un PNG pequeño** (3.744 B) y confirmar `state: success`.
3. **Descubrir `mediaDelete`/listado** (tabla §4) para dejar el panel limpio sin formatearlo.
4. **Portar el dashboard**: `renderer.cpp` de `cougarLCD.cpp` (Cairo) + sensores de
   `poseidon-linux-display` → servicio systemd propio, sin WSL ni usbipd, subiendo un PNG
   por fotograma. Es el objetivo final: el panel sin el editor de COUGAR.
5. Documentar el `.osd` si se consigue capturar uno.
