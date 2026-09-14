# El canal PC ↔ panel, medido

Panel **COUGAR CFV235** (9,16", 1920×462) · USB HID `1d6b:0126` · serie `BYZL2611WC01CM001018`
· firmware/app `V1.0.5`, SDK `V1.2.7`, hardware `V2.0`.

Todo lo de este documento está **medido sobre el panel real** con el código de esta app
(`cfv235.canal`), en `/dev/hidraw4`, el 2026-09-13. No es copia del kit: donde el kit dice una
cosa y la medición dice otra, aquí manda la medición y se señala la discrepancia.

---

## 1. El dispositivo

```
VID/PID       1d6b:0126   (VID de Linux Foundation: el panel no usa uno propio)
Fabricante    COUGAR Inc.
Producto      COUGAR USB Device
Serie         BYZL2611WC01CM001018
USB           2.00, bcdDevice 0100, 250 mA
Driver        usbhid  ->  /dev/hidrawN
Interfaz      1 sola (bInterfaceClass 03 = HID)
Endpoints     ep_01 OUT  Interrupt  wMaxPacketSize 1024  bInterval 1
              ep_81 IN   Interrupt  wMaxPacketSize 1024  bInterval 4
```

### El descriptor HID (esto es lo importante)

```
06 00 ff        Usage Page (0xFF00)
09 01           Usage (0x01)
a1 01           Collection (Application)
09 01             Usage (0x01)
15 00             Logical Minimum (0)
26 ff 00          Logical Maximum (255)
75 08             Report Size (8)
96 00 04          Report Count (0x0400 = 1024)
81 02             Input  (Data,Var,Abs)      -> informe de ENTRADA de 1024 B
09 01             Usage (0x01)
15 00             Logical Minimum (0)
26 ff 00          Logical Maximum (255)
75 08             Report Size (8)
96 00 04          Report Count (1024)
91 02             Output (Data,Var,Abs)      -> informe de SALIDA de 1024 B
c0              End Collection
```

Léelo en tu equipo con:

```bash
xxd /sys/class/hidraw/hidraw4/device/report_descriptor | head
```

**No hay ningún ítem `Report ID`** y el tamaño es **1024 bytes**, no 1025.

## 2. La incógnita del kit: 1024 o 1025

El kit `cfv-235` escribe **1025 bytes con un `0x00` delante** y documenta un `EINVAL` en Linux
como problema misterioso ("prueba `--sin-prefijo`"). El proyecto `cougarLCD.cpp` hace lo mismo
(1025) pero **tolera que hidapi escriba 1024** (`written == size - 1`), que es lo que pasa en
Linux cuando el dispositivo no usa reportes numerados.

Medido: **las seis formas de escribir funcionan**, todas con `200` y respuesta en 6-9 ms.

| Variante | Escritura | Resultado |
|---|---|---|
| A · `0x00` + trama + relleno a 1024 | 1025 B | `200` |
| **B · trama + relleno a 1024 (sin report ID)** | **1024 B** | **`200`** ← elige esta |
| C · `0x00` + trama, sin relleno | 54 B | `200` |
| D · trama, sin prefijo ni relleno | 53 B | `200` |
| E · trama + relleno a múltiplo de 64 | 53 B | `200` |
| F · `0x00` + trama + relleno a 1024 | 1025 B | `200` |

Conclusiones:

1. **No hay `EINVAL`**: hidraw acepta 1025 bytes aunque el descriptor declare 1024. El kit no
   estaba roto por esto.
2. La forma **correcta** es la que declara el descriptor: **1024 B, sin byte de report ID**.
   No es solo elegancia: escribir un byte que el dispositivo no define es depender de que el
   kernel lo tolere.
3. Da igual rellenar o no: el panel lee la trama por su `0x5A` de cierre.

Por eso `cfv235.canal` **autonegocia**: manda un `conn` con cada variante y se queda con la que
el panel contesta (en este panel, la B, en 211 ms). Si algún día otro firmware se comporta
distinto, se adapta solo.

### La lectura

`read()` devuelve **exactamente 1024 bytes que empiezan por `0x5A`**: la respuesta **no trae
byte de report ID**, y detrás van ceros de relleno. No hay que recortar por el campo `len`
(el kit ya lo advierte: hay que buscar el `0x5A`).

## 3. Framing (confirmado contra el hardware)

```
informe : 5A | len(BE16) | payload_escapado | checksum | 5A | ceros
```

- `len = payload_sin_escapar + 5`; **no** cuenta los bytes que añade el escape, así que la trama
  del cable es más larga que `len` cuando hay escapes.
- Escape: `0x5A → 5B 01`, `0x5B → 5B 02`, aplicado al payload, a los dos bytes de longitud y al
  checksum.
- `checksum = (byte_alto + byte_bajo + suma(payload)) & 0xFF` (se suman los **dos bytes** de
  longitud, no el valor `len`).
- El fin de trama se busca por el `0x5A` de cierre.

Medición de hoy: la respuesta de `conn` declara **`len = 375`** (el kit midió 371 con su
firmware/estado). El parser de esta app lo decodifica con `checksum_ok` y `len_ok` verdaderos.

## 4. Mensajes

```
petición   POST <cmd> 1\r\nSeqNumber=N\r\nDate=<ms>\r\n[ContentType/ContentLength]\r\n\r\n<body>
respuesta  1 <code>\r\nAckNumber=N\r\n[ContentType/ContentLength]\r\n\r\n<body>
```

- `AckNumber = SeqNumber + 1` en `conn` (medido con 7 secuencias seguidas).
- `transported` contesta con **cuerpo**: `{"state":"success"}`.
- Un `200` con **cuerpo vacío** significa "no aceptado", no éxito.

## 5. La subida completa, medida en el panel real

Subida de un PNG de 10 585 B a la **capa OSD**, con esta app, sin el editor de COUGAR:

```
transport  {"type":"media","fileSize":10585,"fileName":"prueba_canal.png"}
   -> 200  {"state":"success","blockMaxSize":1024}
11 bloques de 1000 B  (informes de medios, delimitador 0x5C)
   <- 1 200 AckNumber=0          <-- ACUSE DE LOS BLOQUES
transported {"md5":"todo","fileName":"prueba_canal.png"}
   -> 200  {"state":"success"}
total: 109 ms
```

Estado del panel antes y después:

| Campo | Antes | Después |
|---|---|---|
| `background` | `["panel_cfv235.png"]` | `["prueba_canal.png"]` |
| `space` | 81 128 | 81 148 (**+20 KB**) |

### Lo que aporta esto frente al kit

1. **El acuse de los bloques llega y hay que esperarlo.** El kit dice explícitamente "no se
   espera el acuse de cada bloque"; `cougarLCD.cpp` sí lo espera (`wait_for_response` justo
   después de los bloques y antes de `transported`). Medido: el panel manda
   **`1 200 AckNumber=0`** al recibir el último bloque. `cfv235.panel` lo lee y, si llega un
   **`1 400`**, avisa de que los bloques se rechazaron (sesión caducada).
2. **La capa OSD no acumula**: +20 KB para un fichero de 10 KB. Es la capa que hay que usar
   para lo continuo (dashboard, animación).
3. **El formato del informe de medios del kit era correcto** (cabecera de 24 B sin el report ID,
   datos en el offset 24/25, delimitador `0x5C`).

### El contador `[4]` del informe de medios

El kit capturó del editor un contador que **incrementa** (`0x16` fondo / `0x18` OSD);
`cougarLCD.cpp` usa **`0x13` fijo**. Medido en el panel real: **`0x13` y `0x16` funcionan los
dos** (acuse `200` y `transported {"state":"success"}`). El firmware **no exige un valor
concreto**, así que la sospecha del kit de que ahí estaba el problema era infundada.

## 6. Comandos: qué existe y qué no

Medido hoy con el panel sano (`bootFinish: 1`):

| Petición | Resultado |
|---|---|
| `POST conn` (sin cuerpo) | **200** + propiedades (15 campos) |
| **`GET waterBlockScreen`** | **200** + las mismas propiedades ← **el kit lo daba por inexistente**. OJO: desde otro equipo (2026-09-14) dio **400**, sin confirmar (ver `docs/VERIFICACION_INDEPENDIENTE.md`) |
| `GET waterBlockScreenId` | 400 |
| `POST waterBlockScreen` | 400 |
| `STATE waterBlockScreen` | 400 |
| `POST config` | 400 |
| `POST media` / `GET media` | 400 |
| `POST fanLCDSet {"value":50}` | 400 |
| `GET sysinfoDisplay` | 400 |
| `GET conn` / `DELETE conn` | 400 |
| `STATE all` **sin cuerpo** | **400** (exige la telemetría en el cuerpo) |
| `POST inventado` | 400 |

**`GET waterBlockScreen` merece atención**: responde `200` con el JSON de propiedades, igual
que `conn`. No lista medios (eso lo pedía el kit), pero desmiente que el comando no exista.
Sigue sin haber forma conocida de **listar ni borrar** medios: la única limpieza es
`POST recovery {"enable":true}` (Reset), que borra todo y reinicia el panel.

## 6.bis Comando NO documentado: `mode`

Barrido de los nombres que expone la API interna del editor (`waterBlockScreen()`:
`displayInformations`, `customScreenMode`, `customScreenSplitting`, `displayInSleepMode`,
`fan`, `list`, `id`, `enable`, `defaultMedia`, `mediaInfoGet`, `mediaFrameGet`,
`presetTheme`, `sleepClock`, `mode`, `theme`, `sysinfo`), medido hoy:

| Comando | Respuesta |
|---|---|
| **`POST mode {"value":N}`** | **200** y `mode` toma el valor **0, 1, 2 o 3** |
| `POST displayInformations {"enable":true}` | 400 (no existe) |
| `POST customScreenMode`, `customScreenSplitting`, `displayInSleepMode`, `fan`, `list`, `id`, `enable`, `defaultMedia`, `mediaInfoGet`, `mediaFrameGet`, `presetTheme`, `sleepClock`, `theme`, `sysinfo` | **silencio** (ni 200 ni 400: el panel los ignora) |
| `POST displayInSleep`, `POST mode` | 200 (existen) |

**`mode` es un comando real que el kit no documentaba.** No cambia `logo`, `osdState`,
`background` ni `brightness`, así que su efecto visible no está determinado; queda como
incógnita abierta, pero ya se puede fijar y leer.

### ⚠️ Bug del firmware: `mode` se corrompe con un cuerpo que no lleve `value`

Medido, y es fácil de provocar por accidente:

| Cuerpo enviado | Respuesta | `mode` resultante |
|---|---|---|
| `{"value": 2}` | 200 | `2` (correcto) |
| `{"mode": 1}` | 200 | **2147483647** |
| `{"enable": true}` | 200 | **2147483647** |
| `{"value": "1"}` | 200 | **2147483647** |

El firmware contesta 200 igualmente, pero lee un campo sin inicializar y deja `mode` en
`0x7FFFFFFF`. Por eso `cfv235 modo N` (y `Panel.modo()`) **exigen un entero 0..3** y no dejan
mandar otra cosa. Si te pasa, se recupera con `POST mode {"value":0}`.

### Los tres comportamientos del panel

Con el barrido queda claro que un comando puede acabar de tres formas distintas, y eso
importa al depurar:

1. **200** — existe y lo ha atendido.
2. **400** — existe el comando pero la petición no le vale (o no está implementado).
3. **silencio** — lo ignora sin contestar. Un cliente con timeout corto (1,5 s) lo confunde
   con "no existe": al repetir el barrido con 3,5 s aparecieron los `400` que antes se habían
   perdido. El panel contesta con retardos muy variables (el kit midió de 0,4 s a más de 18 s).

## 7. Estados del panel (`POST conn`)

| Campo | Valor medido hoy | Interpretación |
|---|---|---|
| `OS` | `Linux` | sistema que reporta el host |
| `version` | app/firmware `V1.0.5`, sdk `V1.2.7`, hardware `V2.0` | |
| `space` | 81 128 → 81 140 KB | espacio libre |
| `brightness` | **0** → **100** | **estaba en 0: la pantalla se veía negra**; esta app lo subió a 100 |
| `degree` | 270 | orientación |
| `osdState` | 1 | hay capa OSD activa |
| `mode` | 0 | sin cambio en ningún experimento |
| `logo` | 2 | pantalla de arranque/respaldo |
| `timeout` | 60 | |
| `bootFinish` | **1** | arrancado y operativo |
| `background` | `["prueba_canal.png"]` | último medio adoptado |
| `displayInSleep` | 0 | **NO basta para que no se apague** (ver §7.bis) |
| `presetThemeId` / `sleepClockId` | 0 | |

### `brightness: 0` — la trampa que deja el panel negro

El kit lo menciona de pasada ("puede quedarse con brillo 0: si lo ves negro, mira
`brightness`"). Medido: **este panel estaba en `brightness: 0`**, con `background` correcto y
`bootFinish: 1`. Es decir: el panel no estaba averiado, estaba a brillo cero. Esta app lo
detecta y lo puede restaurar con `POST brightness {"value":100}`.

### `realtimeDisplay` y `osdState`

`POST realtimeDisplay {"enable":false}` y `{"enable":true}` devuelven **200** y **no cambian
`osdState`** (sigue en 1). Confirma lo que el kit observó: no sirve para desactivar la capa
OSD. La única forma medida de dejar `osdState` en 0 es `recovery` (Reset).

### 7.bis El apagado por espera: el kit se equivocaba

> **Cómo se comprueba esto (2026-09-14).** La única prueba válida es **mirar la
> pantalla**: cualquier consulta por protocolo es tráfico y puede despertar al panel.
> Medido: justo después de verlo apagado, un `conn` tardó **324 ms** (los siguientes,
> 25 ms) y devolvió `brightness: 100` — lo había despertado la propia consulta. Además,
> mientras haya una aplicación usándolo (por ejemplo el editor de COUGAR abierto) el
> panel **no se duerme**, así que una prueba de inactividad con otro programa al lado no
> vale para nada.

El kit afirma, y lo destaca como uno de sus hallazgos que "simplifican mucho todo":

> «el apagado por espera **se puede desactivar**. Con `displayInSleep` en 0 el panel aguanta
> encendido más de 6 minutos sin ningún tráfico (medido y confirmado en pantalla), así que una
> imagen o un tema fijo se quedan puestos **sin ningún bucle de mantenimiento**.»

**Medido hoy, y no es así.** Experimento: `recovery` ya hecho, `brightness: 100`,
`displayInSleep: 0`, y a partir de ahí **ni una sola escritura** al panel durante 8 minutos:

```
inicio : brightness=100  displayInSleep=0  bootFinish=1  osdState=0
min  2 : brightness=0    displayInSleep=0  bootFinish=1  osdState=0
min  4 : brightness=0    displayInSleep=0  bootFinish=1  osdState=0
min  6 : brightness=0    displayInSleep=0  bootFinish=1  osdState=0
min  8 : brightness=0    displayInSleep=0  bootFinish=1  osdState=0
```

**El panel se apaga igual** (`brightness` cae a 0, la pantalla se ve negra) en **menos de dos
minutos**, con `displayInSleep` en 0 y con `bootFinish: 1`. Lo único que lo mantiene encendido
es **tráfico periódico del host**.

Consecuencias prácticas, que son las que importan:

1. **Hace falta flujo.** Un tema o una imagen fija **no** se quedan puestos solos: hay que
   mandar algo cada pocos segundos. Para eso está `cfv235 mantener` (y el dashboard, que manda
   telemetría entre fotograma y fotograma).
2. **`no-dormir` no sustituye al flujo.** Cambia `displayInSleep` a 0, y eso está bien, pero no
   evita el apagado.
3. **`brightness: 0` no es una avería ni un fallo de la subida**: es el panel en reposo. Se
   recupera con `power resume` + `brightness 100` (lo que hace `cfv235 doctor` al avisarlo).
4. Si ves el panel negro después de un rato, es esto: comprueba `brightness` y arranca
   `cfv235 mantener` o el servicio del dashboard.

## 8. Formatos de imagen

| Formato | ¿Lo acepta el panel? | ¿Lo muestra? |
|---|---|---|
| **PNG** | ✅ `{"state":"success"}`, `background` cambia | ✅ (subida verificada; el kit lo confirmó en pantalla) |
| **JPEG** | ✅ **medido**: 15 325 B, 16 bloques, acuse `200`, `background` → `["prueba.jpg"]` | ✅ **confirmado**: se subio un .jpg de 1024x240 y **se vio en pantalla** |
| GIF | ✅ (según el kit) | ❌ pantalla blanca |

El JPEG es una novedad frente al kit: **se acepta y se ve** (confirmado en pantalla).

### El panel NO escala la imagen: la repite

Confirmado en pantalla (2026-09-14, con el editor cerrado y `osdState: 0`): se subio un JPEG de
**1024x240** con **cuatro cuadrantes numerados** y se vio **repetido 2x2**, con la ultima copia
cortada. El panel **no escala**: dibuja la imagen a su tamano y repite lo que falta en mosaico.

Es decir: **la imagen tiene que ser exactamente 1920x462**. Si no, hay que ajustarla antes de
subirla (`video.ajustar_imagen`: ajustar con bandas, recortar o estirar; es lo que hace la app).

## 9. Cómo reproducir todo esto

```bash
# 1. permisos (una sola vez)
sudo /home/maximo/cfv235-app/herramientas/instalar_udev.sh

# 2. sondeo del canal: descriptor + las 6 variantes de escritura
python3 /home/maximo/cfv235-app/herramientas/sondear_canal.py --json sondeo.json

# 3. estado del panel y diagnóstico completo
python3 -m cfv235 doctor

# 4. subir una imagen (la capa OSD no acumula espacio)
python3 -m cfv235 subir mi_imagen.png --osd
```

## 10. Riesgos (leer antes de subir cosas)

- **Un fichero mayor que la memoria del panel lo deja atascado** (`bootFinish: 0`, `transport`
  → 400). Medido: con **10,2 MB** se atasca (12,2 s de espera). Puede **recuperarse solo** unos
  minutos despues, pero el kit documenta un caso en el que hizo falta **cortarle la alimentacion
  de verdad** (~30 s; desenchufar el USB no basta porque se alimenta de la fuente). El limite
  real esta **entre 5 y 10 MB**, no en los 20 MB que se le suponen (ver `docs/RENDIMIENTO.md`). El README del kit lo documenta
  como algo que le pasó. `cfv235.panel` **comprueba el espacio libre antes de subir** y aborta
  con un mensaje claro si no cabe; es la única defensa y el kit no la tiene.
- **Solo un programa puede hablar con el panel a la vez.** El editor de COUGAR ocupa el HID en
  exclusiva.
- `recovery` **reinicia el panel y NO borra los medios.** Medido dos veces (una desde el panel y
  otra desde `cfv235 recovery`): el comando se acepta (200), el panel deja de responder entre
  **~107 s y ~8 minutos**, y al volver el espacio y el `background` estan **exactamente iguales**.
  El kit dice que borra los medios: no lo hace con este firmware (V1.0.5).
- Mientras no responde, **el panel sigue en el USB** (`lsusb` lo ve como `1d6b:0126`) y
  `/dev/hidrawN` existe, pero no contesta a `conn`. **Hay que esperar, no desconectarlo.**
