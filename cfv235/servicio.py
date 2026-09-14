"""Gestion del servicio de usuario que mantiene el dashboard en marcha.

El dashboard puede correr como servicio de systemd **de usuario** (sin root: con la regla
udev el panel lo abre tu propio usuario). Esta capa evita que el usuario tenga que ir a la
terminal para pararlo cuando quiere usar la app de escritorio: el panel admite una sola
sesion, asi que alternar entre dashboard y GUI es una operacion habitual, no un caso raro.
"""

from __future__ import annotations

import os
import subprocess

NOMBRE = "cfv235-dashboard.service"
UNIDAD = "cfv235-dashboard"


def _systemctl(*argumentos: str, timeout: float = 8.0):
    """Ejecuta `systemctl --user ...`. Devuelve (ok, salida)."""
    try:
        proceso = subprocess.run(["systemctl", "--user", *argumentos],
                                 capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "no hay systemctl en este sistema"
    except subprocess.TimeoutExpired:
        return False, "systemctl no respondio a tiempo"
    salida = (proceso.stdout or proceso.stderr or "").strip()
    return proceso.returncode == 0, salida


def disponible() -> bool:
    """True si hay systemd de usuario para gestionar el servicio."""
    ok, _ = _systemctl("is-system-running")
    if ok:
        return True
    # is-system-running devuelve != 0 en estados degradados; basta con que responda
    ok, salida = _systemctl("--version")
    return ok and "systemd" in salida


def propiedades() -> dict:
    """Campos utiles de la unidad: estado, pid y reinicios."""
    ok, salida = _systemctl("show", NOMBRE, "-p", "ActiveState", "-p", "SubState",
                            "-p", "MainPID", "-p", "NRestarts", "-p", "UnitFileState")
    datos = {}
    if ok:
        for linea in salida.splitlines():
            if "=" in linea:
                clave, valor = linea.split("=", 1)
                datos[clave] = valor
    return datos


def estado() -> dict:
    """Resumen para la interfaz: activo, pid, reinicios y una descripcion legible."""
    props = propiedades()
    activo = props.get("ActiveState") == "active"
    pid = props.get("MainPID")
    pid = int(pid) if pid and pid.isdigit() and int(pid) > 0 else None
    reinicios = props.get("NRestarts")
    return {
        "disponible": bool(props),
        "activo": activo,
        "habilitado": props.get("UnitFileState") in ("enabled", "enabled-runtime"),
        "pid": pid,
        "reinicios": int(reinicios) if reinicios and reinicios.isdigit() else 0,
        "estado": props.get("ActiveState", "desconocido"),
        "subestado": props.get("SubState", ""),
        "descripcion": ("en marcha" + (f" (pid {pid})" if pid else "")) if activo
                       else "parado",
    }


def activo() -> bool:
    return estado().get("activo", False)


def parar() -> tuple[bool, str]:
    """Para el servicio. Devuelve (ok, mensaje)."""
    ok, salida = _systemctl("stop", NOMBRE)
    return ok, salida or ("servicio parado" if ok else "no se pudo parar")


def arrancar() -> tuple[bool, str]:
    ok, salida = _systemctl("start", NOMBRE)
    return ok, salida or ("servicio arrancado" if ok else "no se pudo arrancar")


def reiniciar() -> tuple[bool, str]:
    ok, salida = _systemctl("restart", NOMBRE)
    return ok, salida or ("servicio reiniciado" if ok else "no se pudo reiniciar")


def habilitar(activar: bool = True) -> tuple[bool, str]:
    """Que arranque solo al iniciar sesion (o que deje de hacerlo)."""
    accion = "enable" if activar else "disable"
    return _systemctl(accion, NOMBRE)


def registro(lineas: int = 20) -> str:
    """Ultimas lineas del journal del servicio."""
    try:
        proceso = subprocess.run(["journalctl", "--user", "-u", UNIDAD, "--no-pager",
                                  "-n", str(lineas)],
                                 capture_output=True, text=True, timeout=8.0)
        return (proceso.stdout or proceso.stderr or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"no se pudo leer el registro: {exc}"


def pid_del_bloqueo(dispositivo: str | None = None) -> int | None:
    """Pid que tiene tomado el panel, segun el fichero de bloqueo de `canal`."""
    from .canal import _ruta_bloqueo, buscar
    ruta = dispositivo or buscar()
    if not ruta:
        return None
    try:
        with open(_ruta_bloqueo(ruta), encoding="utf-8") as fh:
            contenido = fh.read().strip()
        return int(contenido) if contenido.isdigit() else None
    except (OSError, ValueError):
        return None


def ocupado_por_el_servicio(dispositivo: str | None = None) -> bool:
    """True si quien tiene el panel es el servicio (y por tanto hay que pararlo)."""
    pid_lock = pid_del_bloqueo(dispositivo)
    if pid_lock is None:
        return False
    pid_servicio = estado().get("pid")
    return pid_servicio is not None and pid_lock == pid_servicio


def liberar_panel(dispositivo: str | None = None, espera: float = 3.0) -> tuple[bool, str]:
    """Deja el panel libre: si lo tiene el servicio, lo para y espera a que lo suelte."""
    import time
    if not ocupado_por_el_servicio(dispositivo):
        return True, "el panel ya estaba libre"
    ok, mensaje = parar()
    if not ok:
        return False, mensaje
    limite = time.time() + espera
    while time.time() < limite:
        if pid_del_bloqueo(dispositivo) is None:
            return True, "servicio parado: el panel queda libre"
        time.sleep(0.2)
    return True, "servicio parado (el bloqueo se liberara en un instante)"


def es_unidad_instalada() -> bool:
    return os.path.exists(os.path.expanduser(f"~/.config/systemd/user/{NOMBRE}"))
