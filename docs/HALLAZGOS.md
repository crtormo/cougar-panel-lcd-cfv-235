# Lo que sabemos del panel que no se ve desde Windows

Resumen de lo medido en Linux contra el panel real. El editor de COUGAR en Windows hace lo
suyo, pero **no expone el protocolo, no publica los limites reales y no avisa de las trampas**.
Todo lo de aqui esta comprobado con el panel delante; lo que no se pudo comprobar se dice.

---

## 1. El protocolo, descifrado

### La trama

```
informe : 5A | len(BE16) | payload_escapado | checksum | 5A | ceros de relleno
```

- `len = payload_sin_escapar + 5` — **no** cuenta los bytes que anaden los escapes.
- Escapes: `0x5A → 5B 01`, `0x5B → 5B 02`, aplicados al payload, a los dos bytes de longitud
  y al checksum.
- `checksum = (byte_alto + byte_bajo + suma(payload)) & 0xFF` — se suman los **dos bytes** de
  longitud, no el valor `len`.
- El fin de trama se localiza por el `0x5A` de cierre.
- La respuesta de `conn` mide hoy **`len = 375`** (el kit midio 371 con su firmware).

### El detalle que el kit no tenia claro: 1024 o 1025

`read()` devuelve **exactamente 1024 bytes que empiezan por `0x5A`**: **sin byte de report ID**.
Windows espera 1025 (con un `0x00` delante) y por eso el kit dudaba; en hidraw crudo son
**1024**, y el descriptor HID (36 B, `usage_page 0xFF00`, sin Report ID) lo confirma.

### Los mensajes

```
peticion   POST <cmd> 1\r\nSeqNumber=N\r\nDate=<ms>\r\n[ContentType/ContentLength]\r\n\r\n<body>
respuesta  1 <code>\r\nAckNumber=N\r\n[ContentType/ContentLength]\r\n\r\n<body>
```

- `AckNumber = SeqNumber + 1` en `conn`.
- **Un `200` con cuerpo vacio significa "no lo he aceptado"**, no exito.
- Hay **tres** comportamientos posibles, no dos: `200`, `400` y **silencio** (el panel ignora el
  comando sin contestar). Con un timeout corto el silencio se confunde con "no existe": al
  repetir el barrido con 3,5 s en vez de 1,5 s aparecieron `400` que antes se perdian.

### La subida, paso a paso

```
transport  {"type":"media","fileSize":N,"fileName":"..."}
   -> 200  {"state":"success","blockMaxSize":1024}
bloques de 1000 B  (informe de medios: cabecera de 24 B, delimitador 0x5C)
   <- 1 200 AckNumber=0        <-- ACUSE DE LOS BLOQUES
transported {"md5":"todo","fileName":"..."}
   -> 200  {"state":"success"}
```

**El acuse de los bloques hay que esperarlo.** El kit dice literalmente "no se espera el acuse";
`cougarLCD.cpp` si lo espera, y medido: el panel manda `1 200 AckNumber=0` al recibir el ultimo
bloque. Si llega un **`1 400`**, es que la sesion ha caducado y hay que repetir.

El contador `[4]` de la cabecera (el kit capturo `0x16`/`0x18` del editor y `cougarLCD.cpp` usa
`0x13` fijo): medido, **`0x13` y `0x16` funcionan los dos**. El firmware no exige un valor
concreto, asi que la sospecha del kit era infundada.

---

## 2. Comandos que existen y el editor no usa

| Comando | Que hace |
|---|---|
| `POST conn` | estado completo (15+ campos) |
| **`GET waterBlockScreen`** | **`400` o silencio** — no responde 200 (el `200` que se midio al principio era una respuesta vieja mal emparejada) |
| **`POST mode {"value":0..3}`** | **comando no documentado**; el panel lo guarda |
| `POST realtimeDisplay {"enable":bool}` | 200, pero **no cambia `osdState`** |
| `POST displayInSleep {"enable":bool}` | 200; **`true` deja `displayInSleep=1`** |
| `POST power {"event":"resume"}` | **solo `resume`**; restart/reboot/reset/reload dan 400 |
| `POST brightness {"value":0..100}` | brillo |
| `POST rotate {"degree":0|90|180|270}` | orientacion |
| `POST recovery {"enable":true}` | Reset (ver §3: **no borra los medios**) |
| `POST fanLCDSet`, `config`, `media`, `GET conn`, `DELETE conn` | **400**: no existen |

Los nombres que expone la API interna del editor pero que el panel **ignora en silencio**:
`displayInformations`, `customScreenMode`, `customScreenSplitting`, `displayInSleepMode`, `fan`,
`list`, `id`, `enable`, `defaultMedia`, `mediaInfoGet`, `mediaFrameGet`, `presetTheme`,
`sleepClock`, `theme`, `sysinfo`.

### ⚠️ Bug del firmware: `mode` se corrompe

| Cuerpo enviado | Respuesta | `mode` resultante |
|---|---|---|
| `{"value": 2}` | 200 | `2` (correcto) |
| `{"mode": 1}` | 200 | **2147483647** |
| `{"enable": true}` | 200 | **2147483647** |
| `{"value": "1"}` | 200 | **2147483647** |

El firmware contesta **200 igualmente**, pero lee un campo sin inicializar y deja `mode` en
`0x7FFFFFFF`. Se recupera con `POST mode {"value":0}`. Es facil de provocar por accidente desde
cualquier cliente propio.

---

## 3. Las trampas que cuestan el panel (esto es lo importante)

### 3.1 El tamano: entre 5 y 10 MB esta el limite

Medido con guardas (techo duro, comprobacion de espacio y parada automatica):

| Fichero | Resultado | Tiempo |
|---|---|---|
| 1,0 MB | correcto | 1,2 s |
| 5,1 MB | correcto | 2,8 s |
| **10,2 MB** | **`bootFinish=0`: el panel se atasca** | 12,2 s |
| 15 y 19 MB | **no se enviaron**: se paro al ver el atasco | - |

El fabricante habla de **20 MB**; medido, a 10 MB ya se atasca. En este caso **se recupero
solo** (unos minutos despues volvio a responder), pero el kit documenta un caso en el que hizo
falta **cortar la alimentacion de verdad** (desenchufar el USB no basta: el panel se alimenta
de la fuente).

**El espacio se gasta de verdad**: cada subida descuenta aproximadamente su tamano, aunque se
repita el mismo nombre de fichero. Lo de "la capa OSD reutiliza su hueco" solo se cumple para
fotogramas pequenos y repetidos.

### 3.2 El panel NO escala las imagenes: las repite

Si le mandas una imagen que no es de 1920x462, **no la escala**: la dibuja a su tamano y
**repite lo que falta en mosaico**, cortando la ultima. Una foto de 1024x240 en una pantalla de
1920x462 sale **4 veces (2x2)**. Hay que ajustarla antes de subirla.

### 3.3 `recovery` no borra los medios (y tarda hasta 8 minutos)

Probado dos veces:

| Intento | Que paso |
|---|---|
| desde el panel | dejo de responder **~107 s**; al volver, el espacio y el fondo **iguales** |
| desde `cfv235` | contesto **200**; dejo de responder **~8 minutos**; al volver, espacio y fondo **exactamente iguales** |

El kit dice que `recovery` "borra los medios y deja `osdState` en 0". Medido: **no los borra**.
Y el reinicio tarda **entre 2 y 8 minutos**, no los 60-80 s que se suponen.

Mientras no responde, **sigue en el USB** (`lsusb` lo ve como `1d6b:0126`) y `/dev/hidrawN`
existe, pero no contesta a `conn`. **Hay que esperar, no desconectarlo.**

### 3.4 `brightness: 0` no es una averia: es el panel en reposo

El panel estaba **con `brightness: 0`**, `background` correcto y `bootFinish: 1`: se veia negro.
No estaba roto, estaba a brillo cero. Se recupera con `power resume` + `brightness 100`.

### 3.5 Se apaga solo en menos de dos minutos (el kit se equivocaba)

El kit destaca como hallazgo que "con `displayInSleep` en 0 el panel aguanta mas de 6 minutos
sin trafico". **Medido: no es asi.**

```
inicio : brightness=100  displayInSleep=0
min  2 : brightness=0    <- ya se ha apagado
min  8 : brightness=0
```

Lo que **si** lo mantiene: `displayInSleep=1` (`enable: true`); y, sobre todo, **trafico
periodico del host**. Un tema o una imagen fija **no** se quedan puestos solos.

### ⚠️ Como se comprueba esto (y por que `brightness` no vale)

**La unica prueba valida es mirar la pantalla.** Cualquier consulta por protocolo **es
trafico** y puede despertar al panel: medido, justo despues de verlo apagado un `conn` tardo
**324 ms** (los siguientes, 25 ms) y devolvio **`brightness: 100`** — lo habia despertado la
propia consulta. Y mientras haya un programa usandolo (el editor de COUGAR abierto, por
ejemplo) el panel **no se duerme**, asi que una prueba de inactividad con otro programa al lado
no sirve.

Con la pantalla delante, con **el editor cerrado**: se apago **~1 minuto** despues del ultimo
trafico, que encaja con `timeout: 60`.

### 3.6 Otras

- **Solo un programa puede hablar con el panel a la vez**: el HID es exclusivo (el editor de
  COUGAR ocupa el dispositivo entero).
- **El GIF sale en blanco.** PNG y **JPEG se ven** los dos (el JPEG lo hemos subido y mostrado).
- **La capa de FONDO acumula; la OSD reutiliza** su hueco para fotogramas repetidos.
- **No hay forma de listar ni borrar medios**: ningun comando los enumera.
- **El panel es 1920x460** segun el fabricante, pero **reporta 1920x462** al preguntarle.
- **El `degree` normal en esta caja es 270**.

---

## 4. La tasa de fotogramas

Medido (docs/RENDIMIENTO.md):

| Concepto | Medido |
|---|---|
| Techo real sostenido | **~3,1 fps** |
| Recomendado | **2 fps** |
| Coste de dibujar el PNG | ~51 ms |
| Coste de subir un fotograma | **100-440 ms** (bimodal: ~70 ms o ~400 ms) |

El panel anuncia **60 Hz** de refresco, pero eso es la pantalla: lo que limita es que **cada
fotograma se sube entero por USB**. Un video a 30 fps se queda en 3.

---

## 5. Como comprobarlo desde Linux

```bash
python3 -m cfv235 doctor            # estado + descriptor + negociacion
python3 -m cfv235 estado            # las propiedades del panel (JSON)
python3 -m cfv235 sondear           # descriptor HID y las 6 formas de escribir
python3 herramientas/medir_fps.py   # tasa de fotogramas
python3 herramientas/medir_tamano.py --mb 1 5   # limite de tamano (con guardas)
```

Los detalles completos, con las mediciones crudas, estan en `docs/CANAL.md` y
`docs/RENDIMIENTO.md`.
