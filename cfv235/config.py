"""Preferencias persistentes de la aplicacion de escritorio.

Se guardan en un unico JSON (`~/.config/cfv235/config.json`) porque el nucleo no instala
ningun esquema de GSettings: usar `Gio.Settings` obligaria a instalar un `.gschema.xml` en
el sistema, y la aplicacion tiene que funcionar copiada de cualquier carpeta.

Reglas de la casa:

* **Nunca lanza.** Si el fichero no existe, esta corrupto, no se puede leer o no se puede
  escribir, se siguen usando los valores por defecto y la aplicacion arranca igual.
* **Solo claves conocidas.** Lo que no este en `DEFECTOS` se ignora al leer y al escribir,
  de modo que un fichero viejo o manipulado a mano no puede meter basura en la aplicacion.
* **Guardado atomico.** Se escribe en un temporal de la misma carpeta y se hace
  `os.replace`, asi que un corte a mitad no deja un JSON a medias.

    from cfv235 import config
    config.leer()                       # dict con todas las claves
    config.obtener("perfil", "completo")
    config.escribir(perfil="minimo", pagina="dashboard")
"""

from __future__ import annotations

import json
import os
import tempfile

# Nombre del fichero dentro de la carpeta de configuracion.
NOMBRE_FICHERO = "config.json"

# Version del formato: sirve para poder cambiar el significado de una clave mas adelante.
VERSION = 1

# Valores por defecto de todo lo que la aplicacion recuerda entre arranques.
DEFECTOS = {
    "version": VERSION,
    # --- dashboard
    "perfil": "completo",
    "secciones": {},          # ajustes sueltos sobre el perfil: {clave: bool}
    "periodo": 2,             # segundos entre fotogramas (1, 2 o 5)
    # --- imagen
    "capa": "fondo",          # "fondo" (acumula) u "osd" (reutiliza el hueco)
    "ajustar_imagen": True,   # escalar a 1920x462 antes de subir (si no, el panel la repite)
    "ajuste_imagen": "ajustar",  # "ajustar" (bandas) | "recortar" | "estirar"
    # --- video
    "fps": 2,                 # medido: el panel sostiene ~3 fps (ver docs/RENDIMIENTO.md)
    "bucle": True,
    "ajuste": "ajustar",      # "ajustar" | "recortar" | "estirar"
    # --- galeria de patrones
    "patron_lado": 8,         # tamano del cuadro de la rejilla
    "patron_etiquetas": False,  # numerar los cruces
    # --- carpetas recordadas
    "carpeta_imagen": "",
    "carpeta_video": "",
    # --- keepalive
    "keepalive": False,        # trafico periodico para que el panel no se apague
    # --- ventana
    "ancho": 1060,
    "alto": 800,
    "pagina": "estado",
}

# Claves cuyo tipo se comprueba al leer: lo que no encaje se queda en el valor por defecto.
_TIPOS = {
    "perfil": str,
    "secciones": dict,
    "periodo": int,
    "capa": str,
    "fps": int,
    "bucle": bool,
    "ajuste": str,
    "keepalive": bool,
    "carpeta_imagen": str,
    "carpeta_video": str,
    "ancho": int,
    "alto": int,
    "pagina": str,
}


def directorio() -> str:
    """Carpeta donde vive el config.json.

    `CFV235_CONFIG_DIR` manda (lo usan las pruebas para no tocar la configuracion del
    usuario); si no, el `XDG_CONFIG_HOME` de siempre o `~/.config`.
    """
    propio = os.environ.get("CFV235_CONFIG_DIR")
    if propio:
        return os.path.abspath(os.path.expanduser(propio))
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "cfv235")


def ruta() -> str:
    """Ruta completa del fichero de preferencias."""
    return os.path.join(directorio(), NOMBRE_FICHERO)


def defectos() -> dict:
    """Copia de los valores por defecto (para poder tocarla sin miedo)."""
    return json.loads(json.dumps(DEFECTOS))


def _normalizar(bruto) -> dict:
    """Mezcla lo leido con los defectos, tirando lo que no encaje."""
    datos = defectos()
    if not isinstance(bruto, dict):
        return datos
    for clave, valor in bruto.items():
        if clave not in DEFECTOS:
            continue
        if clave == "version":
            continue
        esperado = _TIPOS.get(clave)
        if esperado is bool:
            # bool es subclase de int: hay que mirarlo antes.
            if not isinstance(valor, bool):
                continue
        elif esperado is int:
            if isinstance(valor, bool) or not isinstance(valor, int):
                continue
        elif esperado is not None and not isinstance(valor, esperado):
            continue
        datos[clave] = valor
    # Las secciones son un mapa de clave -> bool: se limpia lo que no sea asi.
    datos["secciones"] = {str(k): bool(v) for k, v in (datos.get("secciones") or {}).items()}
    return datos


def leer() -> dict:
    """Las preferencias guardadas, completas. Nunca lanza: si algo falla, defectos."""
    try:
        with open(ruta(), encoding="utf-8") as fh:
            bruto = json.load(fh)
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return defectos()
    except Exception:                             # noqa: BLE001  (nada escapa)
        return defectos()
    try:
        return _normalizar(bruto)
    except Exception:                             # noqa: BLE001
        return defectos()


def obtener(clave: str, defecto=None):
    """Valor de una clave, o `defecto` (y si no, el de fabrica) si no esta."""
    datos = leer()
    if clave in datos:
        return datos[clave]
    if clave in DEFECTOS:
        return DEFECTOS[clave]
    return defecto


def escribir(**cambios) -> bool:
    """Mezcla `cambios` con lo guardado y escribe el fichero. Devuelve si se pudo.

    Nunca lanza: un fallo al guardar (disco lleno, permisos, carpeta que no se puede
    crear) no puede tumbar la aplicacion. Las claves que no existan en `DEFECTOS` se
    ignoran.
    """
    try:
        datos = leer()
    except Exception:                             # noqa: BLE001
        datos = defectos()
    for clave, valor in cambios.items():
        if clave in DEFECTOS and clave != "version":
            datos[clave] = valor
    return _guardar(_normalizar(datos))


def escribir_todo(datos: dict) -> bool:
    """Como `escribir`, pero con un diccionario completo (se normaliza igual)."""
    try:
        return _guardar(_normalizar(datos))
    except Exception:                             # noqa: BLE001
        return False


def _guardar(datos: dict) -> bool:
    """Escritura atomica del JSON. Devuelve False (sin lanzar) si no se pudo."""
    temporal = None
    try:
        carpeta = directorio()
        os.makedirs(carpeta, exist_ok=True)
        fd, temporal = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=carpeta)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(temporal, ruta())
        return True
    except Exception:                             # noqa: BLE001
        if temporal:
            try:
                os.unlink(temporal)
            except OSError:
                pass
        return False
