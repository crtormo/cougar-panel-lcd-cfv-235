"""Metricas del PC para el panel LCD COUGAR CFV235.

Solo Linux y solo biblioteca estandar: NO usa psutil ni ninguna dependencia externa.
Todo se lee de `/proc` y `/sys`. De donde sale cada clave de `Sensores.muestra()`:

    /proc/stat                              cpu_uso
    /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq   cpu_mhz
    /proc/cpuinfo                           cpu_modelo, cpu_nucleos
    /sys/class/hwmon/*                      cpu_temp, chipset_temp, cpu_vent, bomba_vent,
                                            gpu_temp, gpu_vent, gpu_mhz
    /sys/class/thermal/thermal_zone*        respaldo de cpu_temp y chipset_temp
    /proc/meminfo                           ram_total_gb, ram_usado_gb, ram_uso
    dmidecode (solo con root)               ram_velocidad
    /sys/class/drm/card*/device/gpu_busy_percent    gpu_uso
    /sys/class/drm/card*/device/mem_info_vram_used  gpu_vram_usado_gb
    /proc/diskstats                         disco_lectura_mb, disco_escritura_mb
    os.statvfs("/")                         disco_total_gb, disco_usado_gb, disco_uso
    /proc/net/dev                           red_subida_mb, red_bajada_mb, red_total_gb
    /proc/loadavg                           carga_1m, carga_5m, carga_15m
    /proc/uptime                            uptime_h
    /proc/<pid>                             procesos
    /sys/class/power_supply/BAT*/capacity   bateria

Convenios de unidades (para que la UI no tenga que adivinar):

* temperaturas en grados Celsius: hwmon las da en milesimas de grado, aqui se dividen /1000;
* `cpu_mhz` y `gpu_mhz` en MHz: cpufreq da kHz y amdgpu da Hz, aqui se normalizan;
* `*_gb` en GiB (1024^3) y `*_mb` en MiB/s (1024^2): se mantienen los nombres acordados
  de la interfaz aunque el calculo sea binario;
* `disco_lectura_mb`, `disco_escritura_mb`, `red_subida_mb` y `red_bajada_mb` son
  **velocidades** (MB/s) entre las dos ultimas llamadas a `muestra()`, no totales;
* `red_total_gb` si es un total acumulado desde el arranque (excluye `lo`).

Sobre los canales de hwmon: `power*_input` tambien se descubren (y salen en el inventario
de la prueba manual), pero la interfaz acordada no tiene ninguna clave de potencia, asi que
no se leen en cada muestra. En general solo se leen los valores de los canales que la
interfaz necesita, porque cada lectura de hwmon puede ser una consulta al firmware del
dispositivo (en este equipo, la temperatura del NVMe cuesta 1,6 ms y la del WiFi 4,3 ms,
frente a 0,02 ms de k10temp o amdgpu).

Reglas que cumple este modulo:

1. cada lectura esta en su propio try/except (OSError/ValueError y similares) y devuelve
   None si falla: `muestra()` NUNCA lanza, siempre devuelve un dict con las 31 claves;
2. nada de divisiones por cero y los deltas de red/disco nunca son negativos (si el
   contador baja, o aparece una interfaz nueva, el delta vale 0);
3. `cpu_uso` se calcula entre dos llamadas a `muestra()`; la primera puede dar None;
4. no hay estado mutable a nivel de modulo: todo el estado vive en la instancia, de modo
   que dos instancias de `Sensores` no se pisan entre si.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import time

# ------------------------------------------------------------------ rutas
RUTA_HWMON = "/sys/class/hwmon"
RUTA_THERMAL = "/sys/class/thermal"
RUTA_CPUFREQ = "/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"
RUTA_DRM = "/sys/class/drm"
RUTA_CPUINFO = "/proc/cpuinfo"
RUTA_MEMINFO = "/proc/meminfo"
RUTA_LOADAVG = "/proc/loadavg"
RUTA_UPTIME = "/proc/uptime"
RUTA_NETDEV = "/proc/net/dev"
RUTA_DISKSTATS = "/proc/diskstats"
RUTA_STAT = "/proc/stat"
RUTA_POWER_SUPPLY = "/sys/class/power_supply"
PUNTO_MONTAJE = "/"           # sistema de ficheros que se mide para disco_*

# ------------------------------------------------------------------ constantes de calculo
BYTES_SECTOR = 512            # /proc/diskstats cuenta sectores de 512 B
GIB = 1024.0 * 1024.0 * 1024.0
MIB = 1024.0 * 1024.0

# Catalogo unico de la interfaz: (clave, etiqueta, unidad). Es una tupla inmutable, no un
# diccionario de modulo que se pueda mutar: el estado de las medidas va en la instancia.
CATALOGO = (
    ("cpu_uso", "Uso de CPU", "%"),
    ("cpu_temp", "Temperatura de CPU", "C"),
    ("cpu_mhz", "Frecuencia de CPU", "MHz"),
    ("cpu_modelo", "Modelo de CPU", ""),
    ("cpu_nucleos", "Nucleos de CPU", ""),
    ("cpu_vent", "Ventilador de CPU", "RPM"),
    ("ram_total_gb", "RAM total", "GB"),
    ("ram_usado_gb", "RAM usada", "GB"),
    ("ram_uso", "Uso de RAM", "%"),
    ("ram_velocidad", "Velocidad de RAM", "MHz"),
    ("gpu_uso", "Uso de GPU", "%"),
    ("gpu_temp", "Temperatura de GPU", "C"),
    ("gpu_mhz", "Frecuencia de GPU", "MHz"),
    ("gpu_vram_usado_gb", "VRAM usada", "GB"),
    ("gpu_vent", "Ventilador de GPU", "RPM"),
    ("disco_total_gb", "Disco total", "GB"),
    ("disco_usado_gb", "Disco usado", "GB"),
    ("disco_uso", "Uso de disco", "%"),
    ("disco_lectura_mb", "Lectura de disco", "MB/s"),
    ("disco_escritura_mb", "Escritura de disco", "MB/s"),
    ("red_subida_mb", "Subida de red", "MB/s"),
    ("red_bajada_mb", "Bajada de red", "MB/s"),
    ("red_total_gb", "Trafico de red", "GB"),
    ("carga_1m", "Carga 1 min", ""),
    ("carga_5m", "Carga 5 min", ""),
    ("carga_15m", "Carga 15 min", ""),
    ("uptime_h", "Encendido", "h"),
    ("procesos", "Procesos", ""),
    ("bateria", "Bateria", "%"),
    ("bomba_vent", "Bomba de agua", "RPM"),
    ("chipset_temp", "Temperatura de chipset", "C"),
)

# Claves que se calculan por diferencia entre dos muestras (necesitan la muestra anterior).
CLAVES_DELTA = (
    "cpu_uso",
    "red_subida_mb",
    "red_bajada_mb",
    "disco_lectura_mb",
    "disco_escritura_mb",
)

# ------------------------------------------------------------------ identificacion de canales
# Prefijo del fichero de hwmon -> tipo de canal interno.
PREFIJOS_HWMON = (
    ("temp", "temp"),
    ("fan", "vent"),
    ("freq", "freq"),
    ("power", "potencia"),
)
TIPOS_HWMON = ("temp", "vent", "freq", "potencia")

# Nombres de chip (`name` de hwmon) que identifican cada sensor, en minusculas.
CHIPS_CPU = ("k10temp", "coretemp", "zenpower", "cpu_thermal")
CHIPS_GPU = ("amdgpu", "radeon", "nouveau", "i915", "xe")

ETIQ_CPU_TEMP = ("tctl", "tdie", "package id 0", "cpu")
ETIQ_CHIPSET = ("chipset", "pch")
ETIQ_GPU_TEMP = ("edge", "junction", "gpu", "core")
ETIQ_CPU_VENT = ("cpu fan", "cpu_fan", "cpu")
ETIQ_BOMBA_VENT = ("pump", "bomba", "water", "aio", "w_pump")

# Zonas termicas de /sys/class/thermal validas como respaldo de cada temperatura.
ZONAS_CPU = ("x86_pkg_temp", "cpu", "k10temp", "cpu_thermal", "acpitz")
ZONAS_CHIPSET = ("pch", "chipset")

# Discos virtuales que no se cuentan (evita duplicar lo que ya cuenta el disco fisico).
PREFIJOS_VIRTUALES = ("loop", "ram", "zram", "sr", "fd", "nbd", "dm-", "md")
PATRON_PARTICION = re.compile(
    r"^(?:nvme\d+n\d+p\d+|mmcblk\d+p\d+|(?:sd|hd|vd|xvd)[a-z]+\d+)$"
)


# ------------------------------------------------------------------ lecturas basicas
def _leer_texto(ruta, defecto=None):
    """Contenido de un fichero de /proc o /sys sin espacios sobrantes; `defecto` si falla."""
    try:
        with open(ruta, "r", encoding="utf-8", errors="replace") as fichero:
            return fichero.read().strip()
    except (OSError, ValueError):
        return defecto


def _leer_entero(ruta, defecto=None):
    """Primer entero del fichero, o `defecto` si no se puede abrir o no es un numero."""
    texto = _leer_texto(ruta)
    if texto is None:
        return defecto
    try:
        return int(texto, 10)
    except (TypeError, ValueError):
        return defecto


def _glob_seguro(patron):
    """glob.glob que devuelve lista vacia en vez de lanzar."""
    try:
        return sorted(glob.glob(patron))
    except (OSError, ValueError):
        return []


def _delta_positivo(nuevo, viejo):
    """Diferencia entre dos contadores que nunca puede ser negativa.

    Si el contador bajo (interfaz recreada, equipo reiniciado, contador que se resetea)
    se devuelve 0 en lugar de un valor negativo.
    """
    try:
        diferencia = nuevo - viejo
    except TypeError:
        return 0
    return diferencia if diferencia > 0 else 0


def _primero_entero(texto):
    """Primer numero entero que aparece en `texto`, o None."""
    coincidencia = re.search(r"\d+", texto or "")
    return int(coincidencia.group(0)) if coincidencia else None


def _etiqueta_de(ruta_input):
    """Lee el `*_label` hermano de un `*_input` de hwmon (cadena vacia si no existe)."""
    base = ruta_input[: -len("_input")]
    return (_leer_texto(base + "_label", "") or "").strip().lower()


def _coincide(texto, agujas):
    """True si alguna aguja aparece dentro de `texto` (ambos en minusculas)."""
    if not texto:
        return False
    return any(aguja in texto for aguja in agujas)


# ------------------------------------------------------------------ hwmon
def _entradas_seguro(directorio):
    """Entradas de un directorio, ordenadas por nombre; lista vacia si falla."""
    try:
        with os.scandir(directorio) as iterador:
            return sorted(iterador, key=lambda entrada: entrada.name)
    except (OSError, ValueError):
        return []


def _descubrir_hwmon():
    """Metadatos de los canales de /sys/class/hwmon, SIN leer todavia sus valores.

    Devuelve [(tipo, chip, etiqueta, ruta_del_input)] con el tipo en TIPOS_HWMON. El chip
    se identifica con el fichero `name` y el canal con su `*_label` (cadena vacia si el
    canal no tiene etiqueta).

    Se leen primero los metadatos, y solo despues los valores de los canales que la
    interfaz necesita, porque leer un `*_input` de hwmon puede costar milisegundos: es una
    consulta al firmware del dispositivo. Medido en este equipo: 1,6 ms por temperatura
    del NVMe y 4,3 ms por la del chip WiFi, frente a 0,02 ms de k10temp o amdgpu. Leer
    todos los canales en cada muestra costaba ~7 ms de mas y despertaba sin necesidad al
    NVMe y al WiFi.
    """
    canales = []
    for directorio in _glob_seguro(os.path.join(RUTA_HWMON, "hwmon*")):
        chip = (_leer_texto(os.path.join(directorio, "name"), "") or "").strip().lower()
        for entrada in _entradas_seguro(directorio):
            nombre = entrada.name
            if not nombre.endswith("_input"):
                continue
            for prefijo, tipo in PREFIJOS_HWMON:
                if nombre.startswith(prefijo):
                    canales.append((tipo, chip, _etiqueta_de(entrada.path), entrada.path))
                    break
    return canales


def _candidatos(canales, tipo, chips=(), etiquetas=(), prioridad="etiqueta"):
    """Canales de un tipo que encajan con el chip o la etiqueta, en orden de preferencia.

    `prioridad="etiqueta"` pone delante los que coinciden por etiqueta y despues los del
    chip conocido; con `prioridad="chip"` al reves (util cuando hay un chip dedicado,
    p.ej. k10temp o amdgpu, y no queremos que un sensor de placa con etiqueta parecida
    gane). Devuelve una lista, nunca None: el consumidor lee valores en ese orden y se
    queda con el primero que responda.
    """
    if prioridad == "chip":
        grupos = (("chip", chips), ("etiqueta", etiquetas))
    else:
        grupos = (("etiqueta", etiquetas), ("chip", chips))
    elegidos = []
    vistos = set()
    for modo, criterio in grupos:
        if not criterio:
            continue
        for canal in canales:
            if canal[0] != tipo or canal[3] in vistos:
                continue
            if (canal[1] in criterio) if modo == "chip" else _coincide(canal[2], criterio):
                elegidos.append(canal)
                vistos.add(canal[3])
    return elegidos


def _primer_valor(candidatos, escala=1.0, minimo=None):
    """Primer valor legible de la lista de candidatos, multiplicado por `escala`.

    Devuelve None si ningun canal responde. Un valor 0 es valido (p.ej. un ventilador
    parado), no se confunde con "no hay dato"; para las temperaturas se pasa `minimo=1`
    porque los drivers usan 0 para "sensor no disponible" y en ese caso se prueba el
    siguiente candidato en vez de pintar un 0 C imposible.
    """
    for _tipo, _chip, _etiqueta, ruta in candidatos:
        valor = _leer_entero(ruta)
        if valor is None or (minimo is not None and valor < minimo):
            continue
        return valor if escala == 1.0 else valor * escala
    return None


def _thermal_zone(tipos):
    """Temperatura (C) de la primera zona termica cuyo `type` encaje con `tipos`.

    Se descartan las zonas que devuelven 0 (los drivers las dejan a 0 cuando el sensor no
    esta disponible) para no pintar un 0 C imposible.
    """
    for zona in _glob_seguro(os.path.join(RUTA_THERMAL, "thermal_zone*")):
        tipo = (_leer_texto(os.path.join(zona, "type"), "") or "").strip().lower()
        if not _coincide(tipo, tipos):
            continue
        milesimas = _leer_entero(os.path.join(zona, "temp"))
        if milesimas:
            return milesimas / 1000.0
    return None


# ------------------------------------------------------------------ cpu, memoria, sistema
def _leer_cpu_stat():
    """(total, ocioso) en jiffies de la linea global `cpu` de /proc/stat.

    El total es la suma de todos los campos (user, nice, system, idle, iowait, irq,
    softirq, steal...) y el ocioso suma idle + iowait. Devuelve (None, None) si falla.
    """
    texto = _leer_texto(RUTA_STAT, "")
    for linea in (texto or "").splitlines():
        if not linea.startswith("cpu "):
            continue
        campos = linea.split()
        if len(campos) < 5:
            return (None, None)
        valores = []
        for campo in campos[1:]:
            try:
                valores.append(int(campo, 10))
            except ValueError:
                valores.append(0)
        ocioso = valores[3] + (valores[4] if len(valores) > 4 else 0)
        return (sum(valores), ocioso)
    return (None, None)


def _frecuencia_cpu_mhz():
    """Media en MHz de `scaling_cur_freq` (kHz) de todos los nucleos.

    Respaldo: el campo `cpu MHz` de /proc/cpuinfo. None si no hay cpufreq.
    """
    frecuencias = []
    for ruta in _glob_seguro(RUTA_CPUFREQ):
        khz = _leer_entero(ruta)
        if khz:
            frecuencias.append(khz / 1000.0)
    if frecuencias:
        return sum(frecuencias) / len(frecuencias)
    for linea in (_leer_texto(RUTA_CPUINFO, "") or "").splitlines():
        if linea.startswith("cpu MHz"):
            partes = linea.split(":", 1)
            if len(partes) == 2:
                try:
                    return float(partes[1].strip())
                except ValueError:
                    return None
    return None


def _datos_cpuinfo():
    """(modelo, nucleos_fisicos) leidos de /proc/cpuinfo.

    Los nucleos fisicos se cuentan como parejas (physical id, core id) distintas, que es
    lo correcto en CPU con SMT (un 8/16 devuelve 8). Respaldo: `cpu cores` y, si no,
    os.cpu_count().
    """
    texto = _leer_texto(RUTA_CPUINFO, "") or ""
    modelo = None
    nucleos_declarados = None
    parejas = set()
    actual = {}
    for linea in texto.splitlines():
        if ":" not in linea:
            # Linea en blanco: fin del bloque de un procesador.
            if "physical id" in actual and "core id" in actual:
                parejas.add((actual["physical id"], actual["core id"]))
            actual = {}
            continue
        clave, valor = linea.split(":", 1)
        clave = clave.strip()
        valor = valor.strip()
        if clave == "model name" and modelo is None:
            modelo = valor
        elif clave == "cpu cores" and nucleos_declarados is None:
            nucleos_declarados = _primero_entero(valor)
        elif clave in ("physical id", "core id"):
            actual[clave] = valor
    if "physical id" in actual and "core id" in actual:
        parejas.add((actual["physical id"], actual["core id"]))

    nucleos = len(parejas) or nucleos_declarados
    if not nucleos:
        try:
            nucleos = os.cpu_count()
        except (OSError, ValueError):
            nucleos = None
    return (modelo, nucleos)


def _leer_meminfo():
    """Diccionario campo -> valor en kB de /proc/meminfo (kg sin normalizar)."""
    campos = {}
    for linea in (_leer_texto(RUTA_MEMINFO, "") or "").splitlines():
        if ":" not in linea:
            continue
        clave, resto = linea.split(":", 1)
        partes = resto.split()
        if not partes:
            continue
        try:
            campos[clave.strip()] = int(partes[0], 10)
        except ValueError:
            continue
    return campos


def _memoria():
    """(total_gb, usado_gb, uso_%) a partir de /proc/meminfo.

    La memoria usada es MemTotal - MemAvailable (lo que hace el propio kernel y lo que
    muestra GNOME); si no hay MemAvailable se usan MemFree + Buffers + Cached.
    """
    campos = _leer_meminfo()
    total_kb = campos.get("MemTotal")
    if not total_kb or total_kb <= 0:
        return (None, None, None)
    disponible_kb = campos.get("MemAvailable")
    if disponible_kb is None:
        disponible_kb = (
            campos.get("MemFree", 0)
            + campos.get("Buffers", 0)
            + campos.get("Cached", 0)
        )
    usado_kb = total_kb - disponible_kb
    if usado_kb < 0:
        usado_kb = 0
    total_gb = total_kb / (1024.0 * 1024.0)
    usado_gb = usado_kb / (1024.0 * 1024.0)
    uso = usado_kb * 100.0 / total_kb
    return (total_gb, usado_gb, uso)


def _cargas_y_procesos():
    """(carga_1m, carga_5m, carga_15m) de /proc/loadavg."""
    partes = (_leer_texto(RUTA_LOADAVG, "") or "").split()
    cargas = []
    for parte in partes[:3]:
        try:
            cargas.append(float(parte))
        except ValueError:
            cargas.append(None)
    while len(cargas) < 3:
        cargas.append(None)
    return (cargas[0], cargas[1], cargas[2])


def _uptime_horas():
    """Horas desde el arranque segun /proc/uptime."""
    partes = (_leer_texto(RUTA_UPTIME, "") or "").split()
    if not partes:
        return None
    try:
        return float(partes[0]) / 3600.0
    except ValueError:
        return None


def _procesos():
    """Numero de procesos contando los directorios numericos de /proc (no incluye hilos)."""
    try:
        return sum(1 for entrada in os.listdir("/proc") if entrada.isdigit())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ gpu (drm)
def _gpu_uso():
    """% de ocupacion de la GPU: `gpu_busy_percent` del primer card que lo exponga (amdgpu)."""
    for ruta in _glob_seguro(os.path.join(RUTA_DRM, "card[0-9]*/device/gpu_busy_percent")):
        valor = _leer_entero(ruta)
        if valor is not None:
            return valor
    return None


def _gpu_vram_gb():
    """VRAM usada en GB: `mem_info_vram_used` (bytes) del primer card que lo exponga."""
    for ruta in _glob_seguro(os.path.join(RUTA_DRM, "card[0-9]*/device/mem_info_vram_used")):
        valor = _leer_entero(ruta)
        if valor is not None:
            return valor / GIB
    return None


def _frecuencia_gpu_mhz(canales):
    """MHz de la GPU: prioridad `sclk` de hwmon, luego el chip grafico, luego Intel.

    En amdgpu `freq1_input` es sclk (la frecuencia de reloj de shaders) y `freq2_input`
    es mclk (memoria), por eso se prefiere la etiqueta sclk antes que cualquier otra.
    """
    valor = _primer_valor(_candidatos(canales, "freq", chips=CHIPS_GPU, etiquetas=("sclk",)), 1e-6)
    if valor is not None:
        return valor
    valor = _primer_valor(_candidatos(canales, "freq", chips=CHIPS_GPU), 1e-6)
    if valor is not None:
        return valor
    for ruta in _glob_seguro(os.path.join(RUTA_DRM, "card[0-9]*/gt_cur_freq_mhz")):
        valor = _leer_entero(ruta)
        if valor is not None:
            return float(valor)
    return None


# ------------------------------------------------------------------ disco y red
def _es_disco_fisico(nombre):
    """True solo para discos completos (nvme0n1, sda...), no particiones ni virtuales."""
    if nombre.startswith(PREFIJOS_VIRTUALES):
        return False
    return PATRON_PARTICION.match(nombre) is None


def _leer_diskstats():
    """{disco: (sectores_leidos, sectores_escritos)} de los discos fisicos de /proc/diskstats."""
    discos = {}
    for linea in (_leer_texto(RUTA_DISKSTATS, "") or "").splitlines():
        campos = linea.split()
        if len(campos) < 10:
            continue
        nombre = campos[2]
        if not _es_disco_fisico(nombre):
            continue
        try:
            discos[nombre] = (int(campos[5], 10), int(campos[9], 10))
        except ValueError:
            continue
    return discos


def _leer_net_dev():
    """{interfaz: (bytes_recibidos, bytes_enviados)} de /proc/net/dev, sin loopback."""
    interfaces = {}
    for linea in (_leer_texto(RUTA_NETDEV, "") or "").splitlines():
        if ":" not in linea:
            continue
        nombre, resto = linea.split(":", 1)
        nombre = nombre.strip()
        if not nombre or nombre == "lo":
            continue
        campos = resto.split()
        if len(campos) < 9:
            continue
        try:
            interfaces[nombre] = (int(campos[0], 10), int(campos[8], 10))
        except ValueError:
            continue
    return interfaces


def _uso_disco(punto):
    """(total_gb, usado_gb, uso_%) del sistema de ficheros montado en `punto`."""
    try:
        estado = os.statvfs(punto)
    except (OSError, ValueError):
        return (None, None, None)
    try:
        bloque = estado.f_frsize or estado.f_bsize
        total = estado.f_blocks * bloque
        if total <= 0:
            return (None, None, None)
        usado = total - estado.f_bfree * bloque
        if usado < 0:
            usado = 0
        return (total / GIB, usado / GIB, usado * 100.0 / total)
    except (AttributeError, OverflowError, ZeroDivisionError):
        return (None, None, None)


# ------------------------------------------------------------------ bateria y ram (smbios)
def _bateria():
    """% de la primera bateria con `capacity` legible (None en sobremesa sin bateria)."""
    for directorio in _glob_seguro(os.path.join(RUTA_POWER_SUPPLY, "*")):
        tipo = (_leer_texto(os.path.join(directorio, "type"), "") or "").strip()
        if tipo != "Battery":
            continue
        capacidad = _leer_entero(os.path.join(directorio, "capacity"))
        if capacidad is not None and 0 <= capacidad <= 100:
            return capacidad
    return None


def _velocidad_ram_mhz():
    """Velocidad de la RAM en MHz leyendo la tabla SMBIOS con `dmidecode`.

    dmidecode necesita acceso a /dev/mem (root) y la tabla cruda
    /sys/firmware/dmi/tables/DMI tambien es solo de root, asi que sin privilegios esto
    devuelve None. Se intenta una unica vez, desde el constructor, para no penalizar
    `muestra()`. Se prefiere "Configured Memory Speed" (la velocidad real a la que corre
    el modulo) sobre "Speed" (la nominal del SPD).
    """
    try:
        es_root = os.geteuid() == 0
    except AttributeError:
        es_root = False
    if not es_root and not os.access("/dev/mem", os.R_OK):
        return None
    try:
        proceso = subprocess.run(
            ["dmidecode", "-t", "memory"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proceso.returncode != 0:
        return None
    configuradas = []
    nominales = []
    for linea in (proceso.stdout or "").splitlines():
        texto = linea.strip()
        if texto.startswith("Configured Memory Speed:"):
            valor = _primero_entero(texto.split(":", 1)[1])
            if valor:
                configuradas.append(valor)
        elif texto.startswith("Speed:"):
            valor = _primero_entero(texto.split(":", 1)[1])
            if valor:
                nominales.append(valor)
    for lista in (configuradas, nominales):
        if lista:
            # Valor mas repetido entre los modulos instalados (el mas representativo).
            return float(max(set(lista), key=lista.count))
    return None


# ------------------------------------------------------------------ clase publica
class Sensores:
    """Lee las metricas del PC. Una instancia por consumidor (el estado es de instancia).

    Parametros:
        intervalo: tiempo minimo, en segundos, entre dos medidas reales de los contadores
            (CPU, red y disco). Si se llama a `muestra()` antes de que pase ese tiempo se
            repiten los ultimos incrementos ya calculados y NO se tocan los contadores, de
            modo que la siguiente medida abarca una ventana mas larga y no salen picos
            falsos por dividir entre un delta de tiempo minusculo. Con 0.0 (por defecto) se
            recalcula en cada llamada.
    """

    def __init__(self, intervalo: float = 0.0):
        try:
            self.intervalo = max(0.0, float(intervalo))
        except (TypeError, ValueError):
            self.intervalo = 0.0

        # Estado de los contadores acumulativos (solo de esta instancia).
        self._t_contadores = None      # instante (time.monotonic) de la ultima medida real
        self._cpu_prev = None          # (total, ocioso) en jiffies
        self._red_prev = {}            # interfaz -> (rx, tx) en bytes
        self._disco_prev = {}          # disco -> (sectores leidos, escritos)

        # Ultimos valores derivados de un delta, para poder repetirlos si se llama pronto.
        self._ultimo = {clave: (None if clave == "cpu_uso" else 0.0) for clave in CLAVES_DELTA}

        # Datos que no cambian con el tiempo: se leen una vez (dmidecode es un proceso).
        self._estatico = {}
        modelo, nucleos = _datos_cpuinfo()
        self._estatico["cpu_modelo"] = modelo
        self._estatico["cpu_nucleos"] = nucleos
        self._estatico["ram_velocidad"] = _velocidad_ram_mhz()

    # -------------------------------------------------------------- API publica
    def muestra(self) -> dict:
        """Foto completa de metricas. Devuelve SIEMPRE un dict con las 31 claves.

        El valor es None cuando el dato no esta disponible en este equipo (sensor que no
        existe, fichero ilegible o sin privilegios). `cpu_uso` es None en la primera
        llamada, porque necesita dos lecturas de /proc/stat separadas en el tiempo.
        Este metodo nunca lanza una excepcion.
        """
        datos = {clave: None for clave, _etiqueta, _unidad in CATALOGO}
        try:
            ahora = time.monotonic()
            recalcular = True
            if self.intervalo > 0.0 and self._t_contadores is not None:
                if (ahora - self._t_contadores) < self.intervalo:
                    recalcular = False

            canales = _descubrir_hwmon()

            # --- CPU ---
            datos["cpu_modelo"] = self._estatico.get("cpu_modelo")
            datos["cpu_nucleos"] = self._estatico.get("cpu_nucleos")
            datos["cpu_mhz"] = _frecuencia_cpu_mhz()
            datos["cpu_temp"] = _primer_valor(
                _candidatos(canales, "temp", chips=CHIPS_CPU, etiquetas=ETIQ_CPU_TEMP, prioridad="chip"),
                0.001,   # hwmon da milesimas de grado
                minimo=1,
            )
            if datos["cpu_temp"] is None:
                datos["cpu_temp"] = _thermal_zone(ZONAS_CPU)
            datos["cpu_vent"] = _primer_valor(
                _candidatos(canales, "vent", chips=CHIPS_CPU, etiquetas=ETIQ_CPU_VENT)
            )

            # --- chipset ---
            datos["chipset_temp"] = _primer_valor(
                _candidatos(canales, "temp", etiquetas=ETIQ_CHIPSET), 0.001, minimo=1
            )
            if datos["chipset_temp"] is None:
                datos["chipset_temp"] = _thermal_zone(ZONAS_CHIPSET)

            # --- RAM ---
            total_gb, usado_gb, uso = _memoria()
            datos["ram_total_gb"] = total_gb
            datos["ram_usado_gb"] = usado_gb
            datos["ram_uso"] = uso
            datos["ram_velocidad"] = self._estatico.get("ram_velocidad")

            # --- GPU ---
            datos["gpu_uso"] = _gpu_uso()
            datos["gpu_temp"] = _primer_valor(
                _candidatos(canales, "temp", chips=CHIPS_GPU, etiquetas=ETIQ_GPU_TEMP, prioridad="chip"),
                0.001,
                minimo=1,
            )
            datos["gpu_mhz"] = _frecuencia_gpu_mhz(canales)
            datos["gpu_vram_usado_gb"] = _gpu_vram_gb()
            datos["gpu_vent"] = _primer_valor(
                _candidatos(canales, "vent", chips=CHIPS_GPU, etiquetas=("gpu",), prioridad="chip")
            )

            # --- bomba de agua (AIO) ---
            datos["bomba_vent"] = _primer_valor(
                _candidatos(canales, "vent", etiquetas=ETIQ_BOMBA_VENT)
            )

            # --- disco (capacidad ahora, velocidad por diferencia) ---
            total_gb, usado_gb, uso = _uso_disco(PUNTO_MONTAJE)
            datos["disco_total_gb"] = total_gb
            datos["disco_usado_gb"] = usado_gb
            datos["disco_uso"] = uso

            # --- red (total ahora, velocidad por diferencia) ---
            interfaces = _leer_net_dev()
            total_bytes = 0
            for recibidos, enviados in interfaces.values():
                total_bytes += recibidos + enviados
            datos["red_total_gb"] = total_bytes / GIB

            # --- sistema ---
            datos["carga_1m"], datos["carga_5m"], datos["carga_15m"] = _cargas_y_procesos()
            datos["uptime_h"] = _uptime_horas()
            datos["procesos"] = _procesos()
            datos["bateria"] = _bateria()

            # --- incrementos (CPU, red y disco) ---
            if recalcular:
                self._actualizar_deltas(ahora, interfaces)
            for clave in CLAVES_DELTA:
                datos[clave] = self._ultimo.get(clave)
        except Exception:  # red de seguridad: `muestra()` jamas propaga una excepcion
            pass
        return datos

    def resumen(self) -> list[dict]:
        """Version para pintar: [{"clave", "etiqueta", "valor", "unidad"}] en orden.

        El valor es el de `muestra()` sin redondear (o None); el consumidor decide el
        formato. Cada llamada toma una muestra nueva.
        """
        datos = self.muestra()
        return [
            {"clave": clave, "etiqueta": etiqueta, "valor": datos.get(clave), "unidad": unidad}
            for clave, etiqueta, unidad in CATALOGO
        ]

    # -------------------------------------------------------------- internos
    def _actualizar_deltas(self, ahora, interfaces):
        """Recalcula cpu_uso, red_*_mb y disco_*_mb y guarda los contadores nuevos.

        Si es la primera medida, o si el delta de tiempo no es positivo, los valores que
        dependen de una diferencia quedan en None (cpu_uso) o 0.0 (velocidades). Un
        contador que baja nunca produce un delta negativo.
        """
        anterior = self._t_contadores
        intervalo = None if anterior is None else (ahora - anterior)
        if intervalo is not None and intervalo <= 0.0:
            intervalo = None

        # --- CPU: % de tiempo no ocioso entre las dos muestras ---
        cpu_uso = None
        total, ocioso = _leer_cpu_stat()
        if total is not None and self._cpu_prev is not None and intervalo is not None:
            delta_total = _delta_positivo(total, self._cpu_prev[0])
            delta_ocioso = _delta_positivo(ocioso, self._cpu_prev[1])
            if delta_total > 0:
                ocupado = delta_total - delta_ocioso
                if ocupado < 0:
                    ocupado = 0
                elif ocupado > delta_total:
                    ocupado = delta_total
                cpu_uso = ocupado * 100.0 / delta_total
        if total is not None:
            self._cpu_prev = (total, ocioso)

        # --- red: suma de deltas por interfaz (las nuevas aportan 0) ---
        subida = 0
        bajada = 0
        if intervalo is not None:
            for nombre, (recibidos, enviados) in interfaces.items():
                previo = self._red_prev.get(nombre)
                if previo is None:
                    continue  # interfaz nueva: no hay contador anterior con el que comparar
                subida += _delta_positivo(enviados, previo[1])
                bajada += _delta_positivo(recibidos, previo[0])
        self._red_prev = dict(interfaces)

        # --- disco: igual, en sectores de 512 B ---
        discos = _leer_diskstats()
        leido = 0
        escrito = 0
        if intervalo is not None:
            for nombre, (sectores_leidos, sectores_escritos) in discos.items():
                previo = self._disco_prev.get(nombre)
                if previo is None:
                    continue
                leido += _delta_positivo(sectores_leidos, previo[0]) * BYTES_SECTOR
                escrito += _delta_positivo(sectores_escritos, previo[1]) * BYTES_SECTOR
        self._disco_prev = dict(discos)

        if intervalo is not None:
            self._ultimo["red_subida_mb"] = subida / MIB / intervalo
            self._ultimo["red_bajada_mb"] = bajada / MIB / intervalo
            self._ultimo["disco_lectura_mb"] = leido / MIB / intervalo
            self._ultimo["disco_escritura_mb"] = escrito / MIB / intervalo
        self._ultimo["cpu_uso"] = cpu_uso
        self._t_contadores = ahora


# ------------------------------------------------------------------ prueba manual
def _formato(valor):
    """Texto corto para la tabla de la prueba manual."""
    if isinstance(valor, float):
        return f"{valor:.2f}"
    return str(valor)


if __name__ == "__main__":
    sensores = Sensores()
    sensores.muestra()                      # primera muestra: fija los contadores
    time.sleep(0.3)

    inicio = time.perf_counter()
    datos = sensores.muestra()
    milisegundos = (time.perf_counter() - inicio) * 1000.0

    print(f"Sensores CFV235: {len(datos)} claves, muestra en {milisegundos:.2f} ms")
    print(f"{'clave':22s} {'valor':>16s}  unidad")
    for clave, _etiqueta, unidad in CATALOGO:
        valor = datos.get(clave)
        texto = "-" if valor is None else _formato(valor)
        print(f"{clave:22s} {texto:>16s}  {unidad}")

    # Media de varias muestras seguidas, para ver el coste real del sondeo.
    medidas = []
    for _ in range(20):
        t0 = time.perf_counter()
        sensores.muestra()
        medidas.append((time.perf_counter() - t0) * 1000.0)
    print(f"media de 20 muestras: {sum(medidas) / len(medidas):.2f} ms "
          f"(maximo {max(medidas):.2f} ms)")

    sin_dato = [clave for clave, _e, _u in CATALOGO if datos.get(clave) is None]
    print(f"claves sin dato en este equipo ({len(sin_dato)}): {', '.join(sin_dato) or 'ninguna'}")

    # Inventario de canales hwmon detectados (util cuando falta algun sensor).
    canales = _descubrir_hwmon()
    chips = sorted({chip for _tipo, chip, _etiqueta, _ruta in canales})
    print(f"chips hwmon: {', '.join(chips) or 'ninguno'}")
    for tipo in TIPOS_HWMON:
        del_tipo = [(chip, etiqueta) for t, chip, etiqueta, _ruta in canales if t == tipo]
        if del_tipo:
            detalle = ", ".join(f"{chip}:{etiqueta or '-'}" for chip, etiqueta in del_tipo)
            print(f"  {tipo:8s} {detalle}")
