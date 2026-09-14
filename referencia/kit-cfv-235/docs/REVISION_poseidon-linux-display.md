# Revisión: `VillainRU/poseidon-linux-display`

Revisado el 2026-09-12 a partir del código real (clonado por API a `refs/poseidon-linux-display/`),
no del README.

Repo: <https://github.com/VillainRU/poseidon-linux-display> · MIT · Python · v2.0.0 ·
creado 2026-07-25, último push 2026-07-28 · 0 estrellas, 0 forks, 0 issues.

---

## 1. Veredicto en una línea

**Buen proyecto, pero es para OTRO dispositivo: no sirve para el panel del CFV235.**
Lo que sí vale —y vale mucho— es su **arquitectura** y su capa de sensores, que es justo lo
que le falta a `cougar_panel.py`.

---

## 2. Por qué no sirve para el CFV235

| | Poseidon (este repo) | Tu CFV235 |
|---|---|---|
| USB ID | `2c65:1000` (HWCX USB Display) | `1d6b:0126` (COUGAR USB Device) |
| Dispositivo | pantalla de la bomba (2,7") | panel LCD 9,16" 1920x462 |
| Escritura | **un informe binario fijo de 65 B**, primer byte `0x02` (report ID) | trama `5A len payload checksum 5A` con cabecera tipo HTTP + JSON |
| Contenido | **solo telemetría**: temp, carga, frecuencia, voltaje, potencia, RPM | comandos (`conn`, `power`, `brightness`, `transport`, `STATE all`…) |
| Quién dibuja | **la pantalla se dibuja sola** los campos | **el PC sube las imágenes/temas**; el panel solo los pinta |
| Subida de ficheros | no existe | `transport` + bloques (`FileBlockId`, `ContentRange`…) |
| Checksum | no hay | `(sum(payload) + len) & 0xFF` |

No comparten ni una capa del protocolo. Su servicio envía 65 bytes a `2c65:1000`; si se
ejecutara aquí simplemente diría que el dispositivo no está conectado. **El único punto en
común es «escribir a un nodo hidraw»**, que ya tienes resuelto.

⚠️ **No ejecutes su `install.sh` en este PC**: instalaría una unidad systemd y una regla udev
para `2c65:1000`. No dañaría el panel (la regla no coincide), pero el servicio quedaría en
bucle de reinicio escribiendo en el log cada 3 s.

---

## 3. Lo que sí hay que copiar (y por qué importa)

### 3.1 La capa de sensores: no hay que escribirla desde cero

`poseidon-display.py` es, en esencia, un **lector de sensores de Linux** bien hecho. Y los
campos del `STATE all` que espera tu panel son **exactamente los mismos**:

| Campo del `STATE all` (tu panel) | Función a portar del repo |
|---|---|
| `cpu.temperature` | `cpu_temperature()` — hwmon (coretemp/k10temp/zenpower…), con puntuación por etiqueta y reserva en `thermal_zone*` |
| `cpu.load`, `cpu.usage` | `LoadTracker.cpu_percent()` — deltas de `/proc/stat` |
| `cpu.speedAverage` | `cpu_frequency_mhz()` — CPUFreq por política, luego por CPU, luego `/proc/cpuinfo` |
| `cpu.power` | `LoadTracker.cpu_power_tenths_w()` — hwmon o **powercap/RAPL por delta de energía** |
| `cpu.voltage` | `cpu_voltage_cents()` — busca canales `in*_input` entre 0,5 y 2,0 V |
| `memory.total/used/load` | `memory_percent()` + `/proc/meminfo` |
| `disk` | `root_disk_percent()` (`os.statvfs`) |
| `network.upload/download` | `LoadTracker.network_speed()` (`/proc/net/route` + `statistics/tx_bytes`) |
| `fans[]` | `fan_speeds()` — etiquetas `pump`/`water`/`cpu`, con el mismo criterio de reserva que usaba el servicio del fabricante |
| `gpu` | `gpu_stats()` — `amdgpu`/`nouveau`/`nvidia`, `gpu_busy_percent`, `freq1_input`, `power1_average` |

Ojo con las **unidades**: su informe binario viaja en décimas de vatio, centésimas de voltio
y KiB/s (`// 100_000`, `// 10`, `// 1024`). El JSON del CFV235 usa vatios, voltios y valores
directos. Hay que **quitar esas conversiones**, no copiarlas tal cual.

### 3.2 El envoltorio de servicio, que es lo que convierte un script en herramienta

- `poseidon-display.service` — unidad systemd mínima, `Restart=always`.
- `90-poseidon-display.rules` — regla udev: `MODE="0660", GROUP="users", TAG+="uaccess"`.
  Para ti sería `ATTRS{idVendor}=="1d6b", ATTRS{idProduct}=="0126"`.
- `install.sh` — `install -Dm755` a `/usr/local/bin`, `udevadm control --reload-rules`,
  `systemctl enable --now`. Limpio y corto.
- `--diagnose` — volcar los sensores detectados **sin necesidad del dispositivo**. Idea muy
  buena para depurar en Linux: tu `cougar_panel.py` debería tenerla.

### 3.3 Decisiones de diseño acertadas que conviene imitar

- **Cero dependencias**: solo biblioteca estándar. Nada de `pyhidapi` ni `hidapi` que compilar.
- Escribe con `open(device, "wb", buffering=0)` + `hidraw.write(report)` y **comprueba que la
  escritura fue completa** (`written != REPORT_SIZE` → error). Detalle que muchos omiten.
- Reabre el dispositivo en cada iteración → se recupera solo tras un reenchufe.
- Relee `bootFinish`-equivalente cada ciclo: si falla, no se cae, informa y sigue.
- `tests/` con sysfs simulado (mock de `glob`/`read_text`/`read_int`): permite probar la
  lógica de sensores **sin hardware**. Ese mismo patrón vale para probar tu parser de tramas.

---

## 4. Defectos y cosas a mejorar (revisión de código)

1. **Bug real de precedencia** en `fan_speeds()` (línea 316):
   ```python
   cpu_fan = cpu_fan or speeds[-2] if len(speeds) > 1 else speeds[-1]
   ```
   En Python la expresión condicional tiene la precedencia más baja, así que se evalúa como
   `(cpu_fan or speeds[-2]) if len(speeds) > 1 else speeds[-1]`. Cuando la placa expone **un
   solo** ventilador sin etiqueta, `cpu_fan` se **sobrescribe** con esa lectura aunque ya se
   hubiera detectado un ventilador de CPU etiquetado en otro chip. Lo correcto sería:
   ```python
   if len(speeds) > 1:
       cpu_fan = cpu_fan or speeds[-2]
   ```
   (y en el caso de uno solo, `cpu_fan = cpu_fan or speeds[-1]`, pero sin pisar el valor ya
   encontrado).

2. **Bucle de reinicio ruidoso**: si el dispositivo no está, `main()` hace `return 1`
   (línea 526) y la unidad tiene `Restart=always` + `RestartSec=3` → systemd reintenta
   indefinidamente cada 3 s y llena el journal con «is not connected». Mejor salir con `0`
   o usar `Restart=on-failure` con un `StartLimitIntervalSec`.

3. **VID/PID en constante, sin opción ni autodetección**: no se puede reutilizar el script
   para otro panel sin editarlo. Añadir `--vid/--pid` es trivial.

4. **GPU infrarrepresentada**: solo mira hwmon; en NVIDIA propietario no hay `gpu_busy_percent`
   ni hwmon útil, así que la mitad de los campos quedan a 0 en la mayoría de equipos con NVIDIA.

5. **Sin `O_RDWR` ni lectura**: si el dispositivo necesitara leer (como el tuyo), este diseño
   de solo escritura no valdría. No es un fallo para su hardware, pero limita la reutilización.

6. **Sin fichero de configuración**: intervalo, dispositivo y módulos van por argumentos o por
   detección. Aceptable, pero un `poseidon-display.conf` sería más cómodo para el usuario final.

7. **Documentación**: el README está impecable y bilingüe (EN/RU); el CHANGELOG explica el
   porqué de cada versión. Nada que objetar aquí — es el nivel de documentación que debería
   tener tu carpeta del CFV235.

8. **Atribución**: es MIT. Si portas su capa de sensores, hay que **conservar el aviso de
   copyright y la licencia** e indicar de dónde viene.

---

## 5. Plan concreto de reutilización para el CFV235

1. Copiar de allí las funciones de sensores (sección 3.1) a un nuevo módulo, por ejemplo
   `sensores.py`, **quitando las conversiones de unidades**.
2. Mapear su salida al JSON del `STATE all` que ya está documentado en `PROTOCOLO.md` §4
   (incluido el array `fans[]` con `onBoard`/`type`/`name`/`value` y `timestamp`).
3. Añadir a `cougar_panel.py` un subcomando `state --real` que:
   - abra el hidraw del panel (`1d6b:0126`),
   - envíe `POST conn` una vez y espere `bootFinish: 1`,
   - emita `STATE all` cada 1 s (la cadencia que usa el editor),
   - reenvíe `conn` cada ~6 s si se queda sin respuesta.
4. Empaquetar como servicio: unidad systemd + regla udev `1d6b:0126` + `install.sh`, calcados
   de este repo.
5. Añadir `--diagnose` (sensores sin panel) y un test con sysfs simulado.
6. **Lo que este repo NO aporta**: la subida de temas/imágenes (`transport` + bloques). Eso
   sigue pendiente y hay que sacarlo del panel, no de GitHub.

---

## 6. Comparación honesta de madurez

| | poseidon-linux-display | tu carpeta CFV235-Linux |
|---|---|---|
| Conoce *su* dispositivo | sí, completo | sí, protocolo reconstruido y verificado |
| Código listo para ejecutar | sí (`--once`, `--diagnose`, servicio) | cliente de sondeo (`cougar_panel.py`) |
| Sensores reales de Linux | **sí, muy completo** | **no** (modo `--demo`/`--file`) |
| systemd + udev + instalador | sí | no |
| Pruebas automatizadas | sí | no |
| Documentación | README + CHANGELOG bilingües | `PROTOCOLO.md` + `API_DEL_EDITOR.md` |
| Licencia | MIT | sin licencia explícita |

La conclusión práctica: **no cambies de proyecto, roba su capa de sensores y su empaquetado.**
Con eso, tu kit deja de ser un cliente de sondeo y pasa a ser un servicio de pantalla real.
