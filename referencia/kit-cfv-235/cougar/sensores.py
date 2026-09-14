#!/usr/bin/env python3
"""sensores.py - lecturas de sensores de Linux para el dashboard del panel CFV235.

Solo biblioteca estandar. Descubre los interfaces a mano en tiempo de ejecucion, asi que
funciona en Intel, AMD y ARM sin listas de modelos:

  * carga de CPU   -> /proc/stat (delta entre muestras)
  * temperatura    -> hwmon (coretemp/k10temp/zenpower/...) y, si no, thermal_zone*
  * frecuencia     -> CPUFreq (politicas, luego por CPU) y /proc/cpuinfo
  * memoria        -> /proc/meminfo
  * disco          -> os.statvfs('/')
  * red            -> /proc/net/route + sys/class/net/<if>/statistics
  * GPU            -> hwmon de amdgpu/nouveau + gpu_busy_percent
  * ventiladores   -> hwmon fan*_input (pump/water/cpu)

Los valores que el sistema no expone se devuelven como None y el dashboard los pinta como
"--", igual que hace el cliente de referencia cougarLCD.cpp.

La tecnica (descubrimiento de hwmon/thermal/CPUFreq/powercap) sigue la del proyecto
poseidon-linux-display, adaptada a las metricas de este panel.
"""

import glob
import os
import time

CPU_HWMON = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal",
             "soc_thermal", "soc-thermal", "x86_pkg_temp", "scpi_sensors",
             "bcm2835_thermal", "imx_thermal", "rockchip_thermal")
GPU_HWMON = ("amdgpu", "nouveau", "nvidia", "radeon")
CPU_TEMP_ETIQUETAS = ("package", "physical id", "tctl", "tdie", "cpu", "core", "tsi0")
FAN_PUMP = ("pump", "water", "aio")
FAN_CPU = ("cpu", "processor")


def _texto(ruta):
    try:
        with open(ruta, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _entero(ruta, por_defecto=None):
    t = _texto(ruta)
    try:
        return int(t)
    except ValueError:
        return por_defecto


def _hwmon():
    """Genera (ruta, nombre_del_chip) de cada hwmon."""
    for raiz in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        yield raiz, _texto(os.path.join(raiz, "name")).lower()


def _es_gpu(raiz, chip):
    if any(p in chip for p in GPU_HWMON):
        return True
    destino = os.path.realpath(os.path.join(raiz, "device")).lower()
    return "drm" in destino or "gpu" in destino


def temperatura_cpu():
    """Temperatura de CPU en grados, o None."""
    candidatos = []
    for raiz, chip in _hwmon():
        if _es_gpu(raiz, chip):
            continue
        es_cpu = any(p in chip for p in CPU_HWMON)
        for entrada in glob.glob(os.path.join(raiz, "temp*_input")):
            etiqueta = _texto(entrada.replace("_input", "_label")).lower()
            valor = _entero(entrada)
            if valor is None:
                continue
            grados = valor // 1000
            if not 0 < grados < 130:
                continue
            puntos = 0
            if es_cpu:
                puntos += 50
            if any(p in etiqueta for p in CPU_TEMP_ETIQUETAS):
                puntos += 40
            if any(p in etiqueta for p in ("package", "tctl", "tdie")):
                puntos += 20
            if puntos:
                candidatos.append((puntos, grados))
    if candidatos:
        return max(candidatos)[1]

    candidatos = []
    for zona in glob.glob("/sys/class/thermal/thermal_zone*"):
        tipo = _texto(os.path.join(zona, "type")).lower()
        if not any(p in tipo for p in ("cpu", "x86_pkg", "soc")):
            continue
        valor = _entero(os.path.join(zona, "temp"))
        if valor is None:
            continue
        grados = valor // 1000
        if 0 < grados < 130:
            candidatos.append((10 + (10 if "cpu" in tipo else 0), grados))
    return max(candidatos)[1] if candidatos else None


def frecuencia_cpu_mhz():
    """Frecuencia actual en MHz (la mayor de las politicas), o None."""
    for patron in ("/sys/devices/system/cpu/cpufreq/policy*/cpuinfo_cur_freq",
                   "/sys/devices/system/cpu/cpufreq/policy*/scaling_cur_freq",
                   "/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"):
        valores = [v for v in (_entero(p) for p in glob.glob(patron)) if v]
        if valores:
            return max(valores) // 1000
    for linea in _texto("/proc/cpuinfo").splitlines():
        if linea.lower().startswith("cpu mhz") and ":" in linea:
            try:
                return int(float(linea.split(":", 1)[1]))
            except ValueError:
                pass
    return None


def memoria():
    """(porcentaje_usado, usado_gb, total_gb) o (None, None, None)."""
    valores = {}
    for linea in _texto("/proc/meminfo").splitlines():
        partes = linea.split()
        if len(partes) >= 2:
            try:
                valores[partes[0].rstrip(":")] = int(partes[1])
            except ValueError:
                pass
    total = valores.get("MemTotal")
    if not total:
        return None, None, None
    disponible = valores.get("MemAvailable", valores.get("MemFree", 0))
    usado = total - disponible
    return (round(100 * usado / total), round(usado / 1048576, 1),
            round(total / 1048576, 0))


def disco_porcentaje(ruta="/"):
    if not hasattr(os, "statvfs"):          # statvfs es de POSIX (permite probarlo en Windows)
        return None
    try:
        st = os.statvfs(ruta)
        if not st.f_blocks:
            return None
        return round(100 * (st.f_blocks - st.f_bfree) / st.f_blocks)
    except OSError:
        return None


def gpu():
    """(porcentaje_uso, grados) de la GPU, con None en lo que no se pueda leer."""
    uso = None
    grados = None
    for raiz, chip in _hwmon():
        if not _es_gpu(raiz, chip):
            continue
        for entrada in glob.glob(os.path.join(raiz, "temp*_input")):
            valor = _entero(entrada)
            if valor is not None and 0 < valor // 1000 < 130:
                grados = grados or valor // 1000
        for ruta in (os.path.join(raiz, "device", "gpu_busy_percent"),):
            valor = _entero(ruta)
            if valor is not None:
                uso = valor
    for ruta in glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"):
        valor = _entero(ruta)
        if valor is not None:
            uso = valor if uso is None else max(uso, valor)
    return uso, grados


def ventiladores():
    """(rpm_cpu, rpm_pump) leyendo etiquetas; None si no hay."""
    cpu = None
    pump = None
    for raiz, chip in _hwmon():
        for entrada in glob.glob(os.path.join(raiz, "fan*_input")):
            etiqueta = _texto(entrada.replace("_input", "_label")).lower()
            valor = _entero(entrada)
            if not valor:
                continue
            if any(p in etiqueta for p in FAN_PUMP):
                pump = max(pump or 0, valor)
            elif any(p in etiqueta for p in FAN_CPU):
                cpu = max(cpu or 0, valor)
    return cpu, pump


_POWERCAP_PREVIO = {}


def potencia_cpu_w():
    """Potencia de CPU en vatios por powercap/RAPL, o None.

    RAPL solo da energia acumulada (microjulios), asi que hace falta la diferencia entre dos
    muestras: la primera llamada prepara el dato y devuelve None.
    """
    ahora = time.monotonic()
    total = 0.0
    medidos = 0
    for energia_uj in glob.glob("/sys/class/powercap/*/energy_uj"):
        zona = os.path.dirname(energia_uj)
        if not any(p in _texto(os.path.join(zona, "name")).lower()
                   for p in ("package", "socket")):
            continue
        energia = _entero(energia_uj)
        if energia is None:
            continue
        previo = _POWERCAP_PREVIO.get(zona)
        _POWERCAP_PREVIO[zona] = (energia, ahora)
        if previo and ahora > previo[1] and energia >= previo[0]:
            total += (energia - previo[0]) / (ahora - previo[1]) / 1_000_000
            medidos += 1
    return round(total, 1) if medidos else None


class CargaCPU:
    """Carga de CPU por diferencia de /proc/stat (0-100)."""

    def __init__(self):
        self.previo = None

    @staticmethod
    def _tiempos():
        lineas = _texto("/proc/stat").splitlines()
        if not lineas:
            return None                      # no hay /proc (p. ej. al probarlo en Windows)
        valores = [int(v) for v in lineas[0].split()[1:] if v.isdigit()]
        if len(valores) < 4:
            return None
        total = sum(valores)
        inactivo = valores[3] + (valores[4] if len(valores) > 4 else 0)
        return total, inactivo

    def muestra(self):
        actual = self._tiempos()
        if actual is None:
            return None
        previo, self.previo = self.previo, actual
        if previo is None:
            return None
        total = actual[0] - previo[0]
        inactivo = actual[1] - previo[1]
        if total <= 0:
            return None
        return round(100 * (total - inactivo) / total)


class Red:
    """Velocidad de red en MB/s por diferencia de contadores."""

    def __init__(self):
        self.previo = None

    @staticmethod
    def _interfaz():
        for linea in _texto("/proc/net/route").splitlines()[1:]:
            campos = linea.split()
            if len(campos) >= 2 and campos[1] == "00000000":
                return campos[0]
        return None

    def muestra(self):
        iface = self._interfaz()
        if not iface:
            return None
        base = f"/sys/class/net/{iface}/statistics"
        actual = (_entero(f"{base}/tx_bytes", 0) + _entero(f"{base}/rx_bytes", 0),
                  time.monotonic())
        previo, self.previo = self.previo, actual
        if previo is None or actual[1] <= previo[1]:
            return None
        return round((actual[0] - previo[0]) / (actual[1] - previo[1]) / 1_048_576, 2)


def modelo_cpu():
    for linea in _texto("/proc/cpuinfo").splitlines():
        clave, sep, valor = linea.partition(":")
        if sep and clave.strip().lower() in ("model name", "hardware"):
            valor = valor.strip()
            if valor and not valor.isdigit():
                return valor
    return "CPU"


def resumen():
    """Todas las metricas de golpe (para el dashboard)."""
    uso_gpu, temp_gpu = gpu()
    ram_pct, ram_usada, ram_total = memoria()
    fan_cpu, fan_pump = ventiladores()
    return {
        "cpu": None, "cpu_temp": temperatura_cpu(), "cpu_mhz": frecuencia_cpu_mhz(),
        "cpu_modelo": modelo_cpu(), "cpu_w": potencia_cpu_w(),
        "ram": ram_pct, "ram_usada": ram_usada, "ram_total": ram_total,
        "gpu": uso_gpu, "gpu_temp": temp_gpu,
        "disco": disco_porcentaje(),
        "fan_cpu": fan_cpu, "fan_pump": fan_pump,
    }


if __name__ == "__main__":
    carga = CargaCPU()
    red = Red()
    carga.muestra()
    red.muestra()
    time.sleep(1.0)
    datos = resumen()
    datos["cpu"] = carga.muestra()
    datos["red"] = red.muestra()
    for clave, valor in datos.items():
        print(f"  {clave:<10} {valor}")
