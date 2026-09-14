# Revisión: `roychowdhuryrohit-dev/cougarLCD.cpp`

Revisado el 2026-09-12 sobre el código real (descargado a `refs/cougarLCD.cpp/`), no sobre
el README.

Repo: <https://github.com/roychowdhuryrohit-dev/cougarLCD.cpp> · MIT · C++ ·
creado 2026-08-23, último push 2026-08-30 · 0 estrellas, 0 forks, 0 issues.

---

## 1. Veredicto

**Es el proyecto que buscabas: es para TU panel.** Lo dice su propio README y lo confirma
el código: *«C++ dashboard and HID transport for the COUGAR CFV235 Vision LCD»*, con
`VID_1D6B/PID_0126`, informes HID de 1025 bytes y panel de 9,16" 1920×462. Soporta CFV235
Vision, CFV235 Mesh Vision y CFV235 LCD Monitor.

Y no solo es compatible: **resuelve las dos cosas que nos faltaban**, y de paso **corrige
un error del nuestro**.

| Lo que aporta | Estado |
|---|---|
| **El protocolo de subida completo** (`transport` → bloques → `transported`) | ✅ reconstruido en bytes, ya implementado en nuestro `cougar_panel.py` |
| **La regla correcta del checksum** (suma los dos *bytes* de longitud, no el valor `len`) | ✅ corregido y verificado contra tramas reales |
| Confirmación del informe de **1025 bytes** (ID + 1024) y del escape `5A→5B01`, `5B→5B02` | ✅ aplicado |
| Confirmación de que `SeqNumber` empieza en **0** | ✅ aplicado |
| **La idea clave**: renderizar un PNG en el PC y subirlo como medio, en bucle | ✅ es la forma de mostrar contenido propio sin el editor |

---

## 2. Lo que hay que tomar de él (y ya está tomado)

### 2.1 El protocolo de subida (lo que llevábamos semanas sin poder observar)

`src/linux/hid_transport.cpp` → `upload_png()`:

1. `POST transport` con `{"type":"media","fileSize":N,"fileName":"nombre.png"}`.
   Se exige **`200` con `"blockMaxSize":1024`**.
2. El PNG se parte en trozos de **1000 bytes**. Cada trozo va en **un informe de 1025 B**
   con cabecera `[0]=0x00 (ID)`, `[1]=0x5C`, `[2..3]=longitud BE (21+trozo)`, `[4]=0x13`,
   `[5..6]=nº bloques`, `[7..8]=índice`, `[9]=0x02`, `[10..24]=ceros`, `[25..]=datos`.
3. `POST transported` con `{"md5":"todo","fileName":"nombre.png"}` → `{"state":"success"}`.

Los informes de medios **no llevan trama `5A`, ni escape, ni checksum**: son binario crudo.
Ese detalle (y el desplazamiento 25, que incluye el byte de report ID) es exactamente lo que
no se podía deducir del log del editor.

### 2.2 La corrección del checksum

Su `docs/PROTOCOL.md` dice: *«el checksum es el byte bajo de la suma de **ambos bytes de
longitud** y de cada byte del payload sin escapar»*. Nuestra regla era
`(sum(payload) + len) & 0xFF`, que **coincide solo si `len < 256`** (caso de todas las
tramas cortas, y por eso cuadraba en 336/336 muestras). Con `len = 371`: `1 + 115 = 116`
frente a `371 mod 256 = 115` → el desfase de 1 que medimos.

Verificado: `probar_tramas.py` sobre las tramas reales de `tramas_reales/` da
**checksum correcto con la fórmula nueva y fallo con la vieja** (0xA8 ✔ / 0xA7 ✘).

### 2.3 La arquitectura

- **Un PNG por fotograma.** `main.cpp` renderiza con Cairo y llama a `upload_png()` en cada
  ciclo (1 s por defecto). No hay API de dibujo: **la pantalla solo sabe mostrar imágenes
  subidas**, y lo «en vivo» se consigue re-subiendo la imagen. Eso responde de una vez a
  «¿cómo pongo yo mi contenido?».
- `packaging/systemd/cougar-lcd.service` + `scripts/linux/install.sh` + `ensure-usb.sh`:
  servicio y comprobación de USB antes de arrancar.
- `--probe` (read-only) y `--render-only PATH` (dibuja sin tocar USB). El segundo es un
  acierto de diseño: permite probar toda la parte gráfica sin el panel.
- `AGENTS.md` con **invariantes de seguridad** explícitas (no enviar comandos adivinados,
  no tocar ThermalTake `264a:22c5`, no ejecutar a la vez que el editor). Es la disciplina
  correcta y coincide con la que hemos seguido nosotros; merece copiarse.

---

## 3. Críticas y limitaciones reales

1. **No es un cliente Linux de verdad: es un cliente WSL + Windows.** Necesita WSL2,
   `usbipd-win` (y **permisos de administrador** para `usbipd bind`), y la telemetría de
   CPU depende de un CSV que genera el **AMD Ryzen Master SDK en Windows**
   (`C:\ProgramData\CougarLCD`), mientras la GPU se lee por NVML dentro de WSL. En Linux
   nativo no funciona, y sin ese puente la temperatura de CPU sale `-- °C`. Para tu caso
   (Windows sin privilegios de admin) el `install.sh`/`Install.ps1` no es viable tal cual.
2. **El dashboard «en vivo» re-sube la imagen completa cada segundo.** Para 1920×462 son
   ~30–80 bloques de 1000 B por fotograma, cada segundo. Funciona, pero es un caudal de
   escritura notable sobre el almacenamiento interno del panel — y este panel **ya se quedó
   atascado una vez por una subida demasiado grande** (`space: 40008`, `g_40mb.jpg`). Recomiendo
   subir una imagen y dejarla fija, no animar a 1 fps sin medir antes.
3. **No empareja respuestas por `AckNumber`.** Incrementa `SeqNumber` pero `wait_for_response()`
   acepta cualquier `1 200`; para `brightness`/`resume` no exige ninguna subcadena, así que una
   respuesta atrasada de un comando anterior satisfaría la espera. Medimos retardos de 0,4 s
   a más de 18 s, así que no es teórico.
4. **No hay acuse por bloque.** `upload_png()` escribe **todos** los bloques y luego espera
   una sola vez (10 s), pese a que su propio `docs/PROTOCOL.md` dice «espera el acuse del
   bloque». Un fallo a mitad se detecta al final, sin reintento ni reanudación.
5. **`blockMaxSize` se pide pero no se usa**: el tamaño de trozo está fijo en 1000 B.
6. **La telemetría se descarta si está obsoleta** (bien) pero el cliente **no expone ningún
   dato del propio panel** (`conn`, `bootFinish`, espacio libre). No puede avisarte de que el
   panel está sin arrancar: si `transport` devuelve `400` solo dice «fallo de transporte».
   Ahí nuestro `cougar_panel.py conn` es más informativo.
7. **Sin pruebas del transporte.** Hay CI y `ctest`, pero lo `hid_transport.cpp` (que es
   donde vive todo el riesgo) no tiene un vector de prueba; lo que existe requiere hardware.
8. **Riesgo de exclusividad no resuelto**: avisan de no usar el editor a la vez
   (correcto y honesto), pero no comprueban si hay otra instancia abierta.

---

## 4. Comparación con nuestro trabajo

| | cougarLCD.cpp | Nuestra carpeta CFV235-Linux |
|---|---|---|
| Protocolo de subida | ✅ implementado y funcionando | ✅ ahora también (copiado de ellos y verificado) |
| Framing | ✅ correcto | ✅ correcto (corregido gracias a ellos) |
| Panel atascado / `bootFinish` | ❌ no lo contempla | ✅ diagnóstico completo + vectores de prueba reales |
| Secretos del editor (temas, `STATE all`, `mediaDelete`, `recovery`) | ❌ fuera de alcance a propósito | ✅ documentados en `API_DEL_EDITOR.md` |
| Sensores | Windows/WSL (AMD SDK + NVML) | ❌ pendiente (aquí sirve `poseidon-linux-display`) |
| Gráficos | ✅ renderiza con Cairo | ❌ no |
| Empaquetado (systemd/udev) | ✅ | ❌ pendiente |
| Licencia | MIT | sin licencia explícita |

**Conclusión práctica:** los dos proyectos son **complementarios**. De este hay que tomar el
transporte, la corrección del checksum y el empaquetado; de nuestro trabajo, el diagnóstico
del panel atascado y el conocimiento del editor. Nada de lo suyo contradice lo nuestro: lo
confirma y lo completa.

---

## 5. Qué hacer con esto, en orden

1. **Ya hecho**: transporte implementado en `cougar_panel.py` (`subir <png>`), checksum
   corregido, informe de 1025 B, escapes en longitud y checksum, y `probar_tramas.py` con
   tramas reales como regresión.
2. **Siguiente**: resolver el `bootFinish: 0` del panel (es lo que bloquea la subida:
   `transport` → `400` mientras no arranque). Ahí su proyecto no ayuda: hay que usar
   `recovery` / `mediaDelete` desde nuestro cliente.
3. **Después**: subir un PNG de prueba pequeño (el `CFV235_1920x462_ligero.jpg` convertido a
   PNG) y comprobar que `state: success`; luego decidir si merece la pena el bucle a 1 fps.
4. **Opcional**: portar su idea de `--render-only` y su unidad systemd, con la telemetría de
   `poseidon-linux-display` (hwmon), para tener un servicio Linux sin WSL ni usbipd.
