#!/usr/bin/env python3
"""fuentes.py - datos para los widgets del panel CFV235, en Windows y en Linux.

Expone `Fuentes`, que entrega un diccionario de metricas normalizadas y mantiene el historial
que necesitan las graficas. El backend se elige solo:

  * Linux   -> /proc, /sys (hwmon, thermal, CPUFreq, powercap), statvfs
  * Windows -> CIM/WMI a traves de PowerShell (Get-CimInstance), una sola llamada por muestra

Ademas traduce los **nombres de fuente del editor de COUGAR** (`CPU Temperature`,
`GPU Usage`...) a nuestras claves, para que un tema escrito con sus nombres funcione tal cual.

Uso rapido:
    from fuentes import Fuentes
    f = Fuentes()
    f.muestra()                 # primera muestra (prepara deltas)
    time.sleep(1)
    datos = f.muestra()         # metricas + historial
    datos["valores"]["cpu_uso"]
"""

import json
import os
import platform
import subprocess
import sys
import time

ES_WINDOWS = platform.system() == "Windows"

# --- nombres del editor -> nuestras claves -------------------------------------------
# Extraidos del bundle del editor 1.0.14 (ver FUNCIONES_DEL_EDITOR.md)
MAPA_EDITOR = {
    "CPU Temperature": "cpu_temp",
    "CPU Usage": "cpu_uso",
    "CPU Hz": "cpu_mhz",
    "CPU Power": "cpu_w",
    "CPU Fan": "cpu_vent",
    "CPU Fan Speed": "cpu_vent",
    "CPU Fans Speed": "cpu_vent",
    "CPU Brand": "cpu_modelo",
    "CPU Vendor": "cpu_modelo",
    "CPU Platform": "cpu_plataforma",
    "CPU ID": "cpu_id",
    "GPU Temperature": "gpu_temp",
    "GPU Usage": "gpu_uso",
    "GPU Hz": "gpu_mhz",
    "GPU Power": "gpu_w",
    "GPU Fan Speed": "gpu_vent",
    "GPU Fans Speed": "gpu_vent",
    "Memory Usage": "ram_uso",
    "Memory Size": "ram_total_gb",
    "Memory Type": "ram_tipo",
    "Memory Speed": "ram_velocidad",
    "Memory Channels Support": "ram_canales",
    "Disk Space": "disco_uso",
    "Motherboard Name": "placa",
    "Motherboard Chipset": "chipset",
    "Network Card": "red_tarjeta",
    "Network Interface Card": "red_interfaz",
    "Network Interface Card Model": "red_tarjeta",
    "System Information": "sistema",
    "Fan Control": "vent_control",
    # nuestras (no estan en el editor, pero son utiles)
    "Hora": "hora",
    "Fecha": "fecha",
    "Encendido": "uptime",
    "Red": "red_mb",
    "Disco libre": "disco_libre_gb",
}


def nombre_a_clave(nombre):
    """Acepta el nombre del editor, nuestra clave, o cualquiera de nuestros alias."""
    if nombre in MAPA_EDITOR:
        return MAPA_EDITOR[nombre]
    return nombre.strip().lower().replace(" ", "_")


# --------------------------------------------------------------------------- Linux
class _BackendLinux:
    def __init__(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import sensores as s
        self.s = s
        self.carga = s.CargaCPU()
        self.red = s.Red()
        self._previo = None

    def muestra(self):
        s = self.s
        ahora = time.time()
        carga = self.carga.muestra()
        velocidad = self.red.muestra()
        uso_gpu, temp_gpu = s.gpu()
        ram, usada, total = s.memoria()
        fan_cpu, fan_bomba = s.ventiladores()
        datos = {
            "cpu_uso": carga,
            "cpu_temp": s.temperatura_cpu(),
            "cpu_mhz": s.frecuencia_cpu_mhz(),
            "cpu_w": s.potencia_cpu_w(),
            "cpu_vent": fan_cpu,
            "bomba_vent": fan_bomba,
            "cpu_modelo": s.modelo_cpu(),
            "gpu_uso": uso_gpu,
            "gpu_temp": temp_gpu,
            "gpu_mhz": self._gpu_mhz(),
            "ram_uso": ram,
            "ram_usado_gb": usada,
            "ram_total_gb": total,
            "disco_uso": s.disco_porcentaje("/"),
            "disco_libre_gb": self._disco_libre(),
            "red_mb": velocidad,
            "red_interfaz": self._interfaz(),
            "placa": self._dmi("board_name"),
            "chipset": self._dmi("board_vendor"),
            "sistema": self._dmi("product_name"),
            "hora": time.strftime("%H:%M"),
            "fecha": time.strftime("%A %d de %B de %Y"),
            "uptime": self._uptime(),
            "vent_control": fan_cpu,
        }
        return datos

    def _gpu_mhz(self):
        import glob
        for raiz in glob.glob("/sys/class/drm/card*/device/pp_dpm_sclk"):
            try:
                with open(raiz, encoding="utf-8") as fh:
                    for linea in fh:
                        if "*" in linea:
                            return int(linea.split(":")[1].strip().split()[0])
            except (OSError, ValueError, IndexError):
                pass
        return None

    def _disco_libre(self):
        try:
            st = os.statvfs("/")
            return round(st.f_bavail * st.f_frsize / 1_073_741_824, 1)
        except (AttributeError, OSError):
            return None

    def _interfaz(self):
        try:
            with open("/proc/net/route", encoding="utf-8") as fh:
                for linea in fh.readlines()[1:]:
                    campos = linea.split()
                    if len(campos) >= 2 and campos[1] == "00000000":
                        return campos[0]
        except OSError:
            pass
        return None

    @staticmethod
    def _dmi(campo):
        try:
            with open(f"/sys/devices/virtual/dmi/id/{campo}", encoding="utf-8") as fh:
                valor = fh.read().strip()
            return valor or None
        except OSError:
            return None

    @staticmethod
    def _uptime():
        try:
            with open("/proc/uptime", encoding="utf-8") as fh:
                segundos = int(float(fh.read().split()[0]))
            horas, resto = divmod(segundos, 3600)
            return f"{horas} h {resto // 60:02d} min"
        except (OSError, ValueError):
            return None


# --------------------------------------------------------------------------- Windows
_PS_WINDOWS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$salida = [ordered]@{}
try {
    $p = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor |
         Where-Object { $_.Name -eq '_Total' }
    if ($p) { $salida.cpu_uso = [int]$p.PercentProcessorTime }
} catch {}
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $usada = $os.TotalVisibleMemorySize - $os.FreePhysicalMemory
    $salida.ram_uso = [int][Math]::Round(100 * $usada / $os.TotalVisibleMemorySize)
    $salida.ram_usado_gb = [Math]::Round($usada / 1MB, 1)
    $salida.ram_total_gb = [Math]::Round($os.TotalVisibleMemorySize / 1MB, 0)
    $salida.uptime = "$([int]((Get-Date) - $os.LastBootUpTime).TotalHours) h"
} catch {}
try {
    $d = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk |
         Where-Object { $_.Name -eq '_Total' }
    if ($d) { $salida.disco_uso = [Math]::Min(100, [int]$d.PercentDiskTime) }
    $vol = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    if ($vol) { $salida.disco_libre_gb = [Math]::Round($vol.FreeSpace / 1GB, 1) }
} catch {}
try {
    $n = Get-CimInstance Win32_PerfFormattedData_Tcpip_NetworkInterface
    $salida.red_mb = [Math]::Round((($n | Measure-Object BytesTotalPersec -Sum).Sum) / 1MB, 2)
} catch {}
try {
    $g = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine |
         Where-Object { $_.Name -like '*engtype_3D*' }
    if ($g) { $salida.gpu_uso = [Math]::Min(100, [int](($g | Measure-Object UtilizationPercentage -Sum).Sum)) }
} catch {}
try {
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $salida.cpu_modelo = $cpu.Name
    $salida.cpu_mhz = [int]$cpu.CurrentClockSpeed
    $salida.cpu_temp = $null   # Windows no la expone sin drivers
} catch {}
try {
    $b = Get-CimInstance Win32_BaseBoard
    $salida.placa = "$($b.Manufacturer) $($b.Product)"
} catch {}
try {
    $cs = Get-CimInstance Win32_ComputerSystem
    $salida.sistema = "$($cs.Manufacturer) $($cs.Model)"
} catch {}
try {
    $gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1
    $salida.gpu_nombre = $gpu.Name
} catch {}
$salida | ConvertTo-Json -Compress
"""


class _BackendWindows:
    def __init__(self):
        self._estatico = None
        self._previo = None

    def muestra(self):
        crudo = {}
        try:
            salida = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                 _PS_WINDOWS],
                capture_output=True, text=True, timeout=25,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if salida.stdout.strip():
                crudo = json.loads(salida.stdout.strip().splitlines()[-1])
        except (OSError, ValueError, subprocess.SubprocessError):
            crudo = {}

        datos = {
            "cpu_uso": crudo.get("cpu_uso"),
            "cpu_mhz": crudo.get("cpu_mhz"),
            "cpu_temp": None,          # Windows no la da sin HWiNFO/LibreHardwareMonitor
            "cpu_modelo": crudo.get("cpu_modelo"),
            "gpu_uso": crudo.get("gpu_uso"),
            "gpu_temp": None,
            "ram_uso": crudo.get("ram_uso"),
            "ram_usado_gb": crudo.get("ram_usado_gb"),
            "ram_total_gb": crudo.get("ram_total_gb"),
            "disco_uso": crudo.get("disco_uso"),
            "disco_libre_gb": crudo.get("disco_libre_gb"),
            "red_mb": crudo.get("red_mb"),
            "placa": crudo.get("placa"),
            "sistema": crudo.get("sistema"),
            "gpu_nombre": crudo.get("gpu_nombre"),
            "uptime": crudo.get("uptime"),
            "hora": time.strftime("%H:%M"),
            "fecha": time.strftime("%A %d de %B de %Y"),
        }
        # la temperatura de CPU, si el editor de COUGAR dejo su volcado de sensores
        datos["cpu_temp"] = self._temperatura_editor()
        return datos

    @staticmethod
    def _temperatura_editor():
        ruta = os.path.join(os.environ.get("APPDATA", ""), "cougar_lcd_editor", "sysinfo.json")
        if not os.path.exists(ruta):
            return None
        try:
            with open(ruta, encoding="utf-8", errors="replace") as fh:
                import re
                m = re.search(r'"CPU Package"\s*:\s*([\d\.]+)', fh.read())
            return int(float(m.group(1))) if m else None
        except OSError:
            return None


# --------------------------------------------------------------------------- fachada
class Fuentes:
    """Entrega metricas normalizadas y guarda el historial para las graficas."""

    def __init__(self, historial=300):
        self.backend = _BackendWindows() if ES_WINDOWS else _BackendLinux()
        self.historial = historial
        self.hist = {}
        self.ultimo = {}

    def muestra(self):
        """Toma una muestra. Devuelve {'valores': {...}, 'hist': {clave: [..]}}."""
        datos = {k: v for k, v in self.backend.muestra().items() if v is not None}
        self.ultimo = {**self.ultimo, **datos}
        for clave, valor in datos.items():
            if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                serie = self.hist.setdefault(clave, [])
                serie.append(float(valor))
                if len(serie) > self.historial:
                    del serie[:len(serie) - self.historial]
        return {"valores": dict(self.ultimo), "hist": self.hist}

    def valor(self, nombre):
        return self.ultimo.get(nombre_a_clave(nombre))

    def serie(self, nombre):
        return self.hist.get(nombre_a_clave(nombre), [])

    def disponibles(self):
        return sorted(self.ultimo.keys())


if __name__ == "__main__":
    f = Fuentes()
    f.muestra()
    time.sleep(1.0)
    datos = f.muestra()["valores"]
    print(f"backend: {'Windows' if ES_WINDOWS else 'Linux'}")
    for clave in sorted(datos):
        print(f"   {clave:<18} {datos[clave]}")
