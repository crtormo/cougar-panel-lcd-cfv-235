"""widgets.py - motor de dibujo del panel COUGAR CFV235 (1920 x 462).

Convierte un tema (dict) en el PNG que se sube al panel. Es la adaptacion limpia del
motor del kit antiguo (`cougar/widgets.py`) al paquete `cfv235`, con los defectos ya
conocidos corregidos:

* `color()` NUNCA lanza: un color mal escrito devuelve el color por defecto y queda
  registrado en `PROBLEMAS` (antes un fondo invalido abortaba el render entero).
* `validar()` (en `temas.py`) y este motor comparten los mismos criterios.
* Cada widget se dibuja en su propio `try/except`, pero el fallo NO se traga: se
  acumula y queda accesible con `ultimos_fallos()` / `ULTIMOS_FALLOS`.
* `renderizar_datos()` usa `tempfile.mkstemp` (nombre unico, seguro entre hilos) y borra
  el temporal en un `finally` (antes usaba una ruta fija y dejaba basura).
* `_encajar()` tiene cota superior de cuerpo (`TAMANO_MAXIMO`) y calcula el cuerpo por
  proporcion en vez de decrementar de uno en uno.

Requisitos
----------
Solo biblioteca estandar y **Pillow >= 8.2** (por `ImageDraw.rounded_rectangle`, que
llego en esa version). Con Pillow mas antiguo los widgets con esquinas redondeadas
fallan y el fallo queda registrado, pero el PNG se genera igual.

Tipografia
----------
Se usa DejaVu del sistema (`/usr/share/fonts/truetype/dejavu/...`) con alternativas
(Liberation, FreeFont) y, si no hay ninguna, `ImageFont.load_default()`. La tipografia
NO reproduce exactamente la del editor oficial de COUGAR, que dibuja en un webview
Electron con sus propias fuentes y su propio interlineado: el tamano y los saltos de
linea aqui son una aproximacion.

Datos
-----
El objeto `fuentes` que se pasa puede ser:

* un objeto con `muestra() -> dict` (es lo que devuelve `cfv235.sensores.Sensores`),
* el dict devuelto por `muestra()` tal cual,
* un objeto con `valor(nombre)` / `serie(nombre)` (como el `Fuentes` del kit antiguo),
* `None`, en cuyo caso se intenta crear `cfv235.sensores.Sensores` de forma perezosa y,
  si no esta disponible, se dibuja con valores vacios ("--").

Tipos de widget
---------------
  reloj (clock)    formato strftime, por defecto "%H:%M"
  texto (text)     texto fijo con marcadores {clave}, {clave:.1f} y {fuente:Nombre}
  dato (data)      tarjeta con etiqueta, valor grande, detalle y barra opcional
  barra (bar)      etiqueta + barra horizontal + valor
  grafica (chart)  grafica de linea con el historial de una fuente
"""

import logging
import os
import re
import threading
import time

# --------------------------------------------------------------------------- constantes
ANCHO = 1920
ALTO = 462

FONDO = "#0e1117"
TARJETA = "#161b24"
BORDE = "#2c3442"
TEXTO = "#ebf0f8"
GRIS = "#788291"
ACENTO = "#00d0ff"
ACENTO2 = "#ff00c8"
PISTA = "#262d3a"
REJILLA = "#232a36"

# Cota de cuerpo de letra. El panel mide 462 px de alto: por encima de esto el texto no
# cabe y ademas FreeType se vuelve lento, asi que se recorta y se registra un aviso.
TAMANO_MAXIMO = 512
TAMANO_MINIMO = 8
MAX_FUENTES_EN_CACHE = 256
MAX_PROBLEMAS = 64
MAX_HISTORIAL = 300

_log = logging.getLogger("cfv235.widgets")
_log.addHandler(logging.NullHandler())

# Fallos del ultimo render. `FALLOS` es un alias del mismo objeto: como se reemplaza el
# CONTENIDO (no la lista), cualquier referencia importada sigue viendo los fallos nuevos.
ULTIMOS_FALLOS = []
FALLOS = ULTIMOS_FALLOS
_fallos_lock = threading.Lock()
_local = threading.local()

# Problemas no fatales (colores invalidos, radios negativos recortados...).
PROBLEMAS = []
_problemas_lock = threading.Lock()

_fuentes_cache = {}
_fuentes_lock = threading.Lock()


class ErrorWidget(Exception):
    """Un widget no se puede dibujar con los datos que trae el tema."""


# --------------------------------------------------------------------------- nombres
# Nombres de fuente del editor oficial de COUGAR -> claves internas de `cfv235.sensores`.
# Los que no existen en `sensores` se dejan mapeados igual: el widget dibuja "--" en vez
# de romper el tema.
MAPA_EDITOR = {
    "CPU Temperature": "cpu_temp",
    "CPU Usage": "cpu_uso",
    "CPU Hz": "cpu_mhz",
    "CPU Frequency": "cpu_mhz",
    "CPU Power": "cpu_w",
    "CPU Fan": "cpu_vent",
    "CPU Fan Speed": "cpu_vent",
    "CPU Fans Speed": "cpu_vent",
    "CPU Brand": "cpu_modelo",
    "CPU Vendor": "cpu_modelo",
    "CPU Platform": "cpu_plataforma",
    "CPU ID": "cpu_id",
    "CPU Cores": "cpu_nucleos",
    "GPU Temperature": "gpu_temp",
    "GPU Usage": "gpu_uso",
    "GPU Hz": "gpu_mhz",
    "GPU Frequency": "gpu_mhz",
    "GPU Power": "gpu_w",
    "GPU Fan Speed": "gpu_vent",
    "GPU Fans Speed": "gpu_vent",
    "GPU Memory Usage": "gpu_vram_usado_gb",
    "Memory Usage": "ram_uso",
    "Memory Size": "ram_total_gb",
    "Memory Type": "ram_tipo",
    "Memory Speed": "ram_velocidad",
    "Memory Channels Support": "ram_canales",
    "Disk Space": "disco_uso",
    "Disk Usage": "disco_uso",
    "Disk Read": "disco_lectura_mb",
    "Disk Write": "disco_escritura_mb",
    "Motherboard Name": "placa",
    "Motherboard Chipset": "chipset",
    "Chipset Temperature": "chipset_temp",
    "Network Card": "red_tarjeta",
    "Network Interface Card": "red_interfaz",
    "Network Interface Card Model": "red_tarjeta",
    "Download": "red_bajada_mb",
    "Upload": "red_subida_mb",
    "System Information": "sistema",
    "Fan Control": "vent_control",
    "Pump Fan Speed": "bomba_vent",
    "Water Pump": "bomba_vent",
    "Battery": "bateria",
    "Processes": "procesos",
    "Load": "carga_1m",
    "Uptime": "uptime_h",
    # nuestras (no estan en el editor, pero son utiles)
    "Hora": "hora",
    "Fecha": "fecha",
    "Encendido": "uptime",
    "Red": "red_bajada_mb",
    "Disco libre": "disco_libre_gb",
}

# Claves antiguas del kit que ahora se llaman de otra forma.
ALIAS_CLAVES = {
    "red_mb": "red_bajada_mb",
    "chipset": "chipset_temp",
    "encendido": "uptime",
}

# Claves internas conocidas. Se usa como respaldo cuando `cfv235.sensores` no se puede
# importar (el paquete debe funcionar y validarse sin el), y se completa con el CATALOGO
# real de `sensores` cuando si esta.
CLAVES_CONOCIDAS = (
    "cpu_uso", "cpu_temp", "cpu_mhz", "cpu_modelo", "cpu_nucleos", "cpu_vent",
    "ram_total_gb", "ram_usado_gb", "ram_uso", "ram_velocidad",
    "gpu_uso", "gpu_temp", "gpu_mhz", "gpu_vram_usado_gb", "gpu_vent",
    "disco_total_gb", "disco_usado_gb", "disco_uso", "disco_lectura_mb",
    "disco_escritura_mb",
    "red_subida_mb", "red_bajada_mb", "red_total_gb",
    "carga_1m", "carga_5m", "carga_15m", "uptime_h", "procesos", "bateria",
    "bomba_vent", "chipset_temp",
    # sinteticas y de otros backends
    "hora", "fecha", "uptime", "disco_libre_gb", "cpu_w", "gpu_w", "placa",
    "sistema", "red_interfaz", "red_tarjeta", "chipset", "vent_control",
)

_PATRON_MARCADOR = re.compile(r"\{([^{}]*)\}")


def nombre_a_clave(nombre):
    """Traduce el nombre del editor, nuestra clave o un alias a la clave interna."""
    if nombre is None:
        return ""
    if not isinstance(nombre, str):
        nombre = str(nombre)
    if nombre in MAPA_EDITOR:
        return MAPA_EDITOR[nombre]
    clave = nombre.strip().lower().replace(" ", "_").replace("-", "_")
    return ALIAS_CLAVES.get(clave, clave)


# --------------------------------------------------------------------------- color
def _registrar_problema(mensaje, valor=None):
    """Apunta un problema no fatal (y lo manda al log). Nunca lanza."""
    entrada = {"mensaje": mensaje, "valor": valor}
    try:
        with _problemas_lock:
            PROBLEMAS.append(entrada)
            if len(PROBLEMAS) > MAX_PROBLEMAS:
                del PROBLEMAS[:len(PROBLEMAS) - MAX_PROBLEMAS]
    except Exception:                                     # pragma: no cover (defensivo)
        pass
    _log.warning("%s", mensaje)
    return entrada


def ultimos_problemas():
    """Copia de los ultimos problemas no fatales (colores invalidos, etc.)."""
    with _problemas_lock:
        return list(PROBLEMAS)


def _color_o_nada(valor):
    """Devuelve (r, g, b) o None si el valor no es un color. NUNCA lanza."""
    try:
        if isinstance(valor, bool):
            return None
        if isinstance(valor, int):
            if 0 <= valor <= 0xFFFFFF:
                return ((valor >> 16) & 0xFF, (valor >> 8) & 0xFF, valor & 0xFF)
            return None
        if isinstance(valor, (tuple, list)):
            if len(valor) != 3:
                return None
            componentes = []
            for componente in valor:
                if isinstance(componente, bool) or not isinstance(componente, (int, float)):
                    return None
                entero = int(componente)
                if not 0 <= entero <= 255:
                    return None
                componentes.append(entero)
            return tuple(componentes)
        if not isinstance(valor, str):
            return None
        texto = valor.strip()
        if texto.startswith("#"):
            texto = texto[1:]
        if len(texto) == 3:
            texto = "".join(caracter * 2 for caracter in texto)
        if len(texto) != 6:
            return None
        return tuple(int(texto[i:i + 2], 16) for i in (0, 2, 4))
    except (TypeError, ValueError, IndexError):
        return None


def es_color(valor):
    """True si `valor` es un color interpretable (None y "" devuelven False)."""
    if valor is None or valor == "":
        return False
    return _color_o_nada(valor) is not None


def color(valor, por_defecto=(255, 255, 255)):
    """Color (r, g, b) seguro. NUNCA lanza.

    Si `valor` no es un color valido devuelve `por_defecto` y registra el problema en
    `PROBLEMAS` (visible con `ultimos_problemas()`). Un valor vacio o None se considera
    "sin color": devuelve el defecto sin registrarlo como problema.
    """
    defecto = _color_o_nada(por_defecto) or (255, 255, 255)
    try:
        if valor is None or valor == "":
            return defecto
        interpretado = _color_o_nada(valor)
        if interpretado is not None:
            return interpretado
        _registrar_problema("color invalido %r: se usa %r" % (valor, defecto), valor)
        return defecto
    except Exception as exc:                              # red de seguridad
        _registrar_problema("color invalido %r (%s): se usa %r" % (valor, exc, defecto),
                            valor)
        return defecto


# --------------------------------------------------------------------------- fuentes
def _rutas_de_fuente(negrita):
    """Rutas candidatas, en orden. Solo Linux: la app es de escritorio Linux."""
    nombres = (("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf")
               if negrita else
               ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf"))
    raices = (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/dejavu",
        "/usr/share/fonts/TTF",
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/truetype/liberation2",
        "/usr/share/fonts/liberation",
        "/usr/share/fonts/truetype/freefont",
        "/usr/share/fonts/gnu-free",
        "/usr/local/share/fonts",
    )
    for raiz in raices:
        for nombre in nombres:
            yield os.path.join(raiz, nombre)


def _fuente(tamano, negrita=False):
    """Fuente del sistema para ese cuerpo, con cache. Nunca lanza."""
    try:
        entero = int(tamano)
    except (TypeError, ValueError):
        entero = 12
    entero = max(1, min(entero, TAMANO_MAXIMO))
    clave = (entero, bool(negrita))
    with _fuentes_lock:
        guardada = _fuentes_cache.get(clave)
    if guardada is not None:
        return guardada

    from PIL import ImageFont
    fuente = None
    for ruta in _rutas_de_fuente(negrita):
        try:
            if os.path.exists(ruta):
                fuente = ImageFont.truetype(ruta, entero)
                break
        except (OSError, ValueError):
            continue
    if fuente is None:
        # Ultimo intento: dejar que Pillow busque la fuente por su nombre.
        for nombre in (("DejaVuSans-Bold.ttf",) if negrita else ("DejaVuSans.ttf",)):
            try:
                fuente = ImageFont.truetype(nombre, entero)
                break
            except (OSError, ValueError):
                continue
    if fuente is None:
        _log.info("no hay fuentes TrueType: se usa ImageFont.load_default()")
        fuente = ImageFont.load_default()
    with _fuentes_lock:
        if len(_fuentes_cache) >= MAX_FUENTES_EN_CACHE:
            _fuentes_cache.clear()
        _fuentes_cache[clave] = fuente
    return fuente


# --------------------------------------------------------------------------- utilidades
def _es_numero(valor):
    """Numero de verdad: `bool` no cuenta (True/False no son coordenadas)."""
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _numero(w, campo, defecto):
    valor = w.get(campo, defecto)
    if not _es_numero(valor):
        raise ErrorWidget("'%s' tiene que ser un numero, no %r" % (campo, valor))
    return float(valor)


def _entero(w, campo, defecto):
    return int(_numero(w, campo, defecto))


def _tamano(w, campo, defecto):
    """Cuerpo de letra valido. Un cuerpo <= 0 es un error, no se recorta en silencio."""
    valor = _numero(w, campo, defecto)
    if valor <= 0:
        raise ErrorWidget("'%s' tiene que ser positivo, no %r" % (campo, w.get(campo)))
    if valor > TAMANO_MAXIMO:
        _registrar_problema("'%s'=%r es enorme: se recorta a %d"
                            % (campo, w.get(campo), TAMANO_MAXIMO), w.get(campo))
        return TAMANO_MAXIMO
    return int(valor)


def _radio(w, defecto, ancho, alto):
    """Radio valido para rounded_rectangle (0 <= radio <= mitad del lado menor)."""
    valor = w.get("radio", defecto)
    if not _es_numero(valor):
        raise ErrorWidget("'radio' tiene que ser un numero, no %r" % (valor,))
    entero = int(valor)
    if entero < 0:
        _registrar_problema("'radio' negativo (%r): se usa 0" % (valor,), valor)
        entero = 0
    return max(0, min(entero, min(ancho, alto) // 2))


def formatear(valor, unidad=None):
    """Presenta el valor como lo haria el editor: entero para %, un decimal para el resto."""
    if valor is None:
        return "--"
    if isinstance(valor, bool):
        return "si" if valor else "no"
    if isinstance(valor, str):
        return valor
    if isinstance(valor, (int, float)):
        if unidad == "%":
            return "%.0f" % valor
        if isinstance(valor, float) and not float(valor).is_integer():
            return "%.1f" % valor
        return "%d" % int(valor)
    return str(valor)


def _valor_sintetico(clave, valores):
    """Valores que no vienen de `sensores` pero los temas del kit usan."""
    if clave == "hora":
        return time.strftime("%H:%M")
    if clave == "fecha":
        return time.strftime("%d/%m/%Y")
    if clave in ("uptime", "encendido"):
        horas = valores.get("uptime_h")
        if _es_numero(horas):
            enteras = int(horas)
            minutos = int(round((float(horas) - enteras) * 60))
            if minutos >= 60:
                enteras += 1
                minutos -= 60
            return "%d h %02d min" % (enteras, minutos)
        return valores.get("uptime")
    return None


def texto_con_marcadores(plantilla, fuentes):
    """Sustituye {clave}, {clave:.1f} y {fuente:Nombre del editor} por los valores actuales.

    Un marcador desconocido o sin dato se dibuja como "--" (nunca lanza).
    """
    texto = "" if plantilla is None else str(plantilla)

    def sustituir(coincidencia):
        dentro = coincidencia.group(1).strip()
        if not dentro:
            return ""
        if dentro.startswith("fuente:"):
            return formatear(fuentes.valor(dentro[len("fuente:"):].strip()))
        clave, especificacion = dentro, ""
        if ":" in dentro:
            clave, especificacion = dentro.split(":", 1)
            clave = clave.strip()
            especificacion = especificacion.strip()
        valor = fuentes.valor(clave)
        if especificacion:
            if valor is None:
                return "--"
            try:
                return format(valor, especificacion)
            except (ValueError, TypeError):
                return formatear(valor)
        return formatear(valor)

    return _PATRON_MARCADOR.sub(sustituir, texto)


# --------------------------------------------------------------------------- historial
_HISTORIAL = {}
_HISTORIAL_LOCK = threading.Lock()


def _anotar_historial(valores):
    """Guarda un punto por clave numerica. Es lo que alimenta las graficas cuando la
    fuente de datos solo entrega una foto (`muestra()`) y no un historial propio."""
    if not isinstance(valores, dict):
        return
    with _HISTORIAL_LOCK:
        for clave, valor in valores.items():
            if not _es_numero(valor):
                continue
            serie = _HISTORIAL.setdefault(clave, [])
            serie.append(float(valor))
            if len(serie) > MAX_HISTORIAL:
                del serie[:len(serie) - MAX_HISTORIAL]


def _aplanar_muestra(crudo):
    """De {'valores': {...}, 'hist': {...}} saca la parte de valores."""
    if not isinstance(crudo, dict):
        return {}, None
    valores = crudo.get("valores")
    if not isinstance(valores, dict):
        valores = crudo
    historial = crudo.get("hist")
    if not isinstance(historial, dict):
        historial = None
    return dict(valores), historial


def crear_fuentes():
    """Intenta crear `cfv235.sensores.Sensores` de forma perezosa.

    Si el modulo no existe todavia, o falla al arrancar, devuelve `{}`: los temas se
    dibujan igual, con "--" en los datos.
    """
    try:
        from .sensores import Sensores
        sensores = Sensores()
        sensores.muestra()
        return sensores
    except Exception as exc:                              # modulo ausente o sin /proc
        _log.info("sin cfv235.sensores (%s): se dibujara con valores vacios", exc)
        return {}


class _Lector:
    """Adaptador unico para las distintas formas de `fuentes`.

    Acepta un dict, un objeto con `muestra()` (Sensores), un objeto con `valor()`/`serie()`
    (el Fuentes del kit) o None. Mantiene el historial global para las graficas.
    """

    def __init__(self, fuentes=None):
        self._objeto = crear_fuentes() if fuentes is None else fuentes
        self._valores = {}
        self._hist_externo = None
        self._valor_delegado = callable(getattr(self._objeto, "valor", None))
        self._serie_delegada = callable(getattr(self._objeto, "serie", None))
        try:
            if isinstance(self._objeto, dict):
                crudo = self._objeto
            else:
                muestra = getattr(self._objeto, "muestra", None)
                crudo = muestra() if callable(muestra) else None
            self._valores, self._hist_externo = _aplanar_muestra(crudo)
        except Exception as exc:
            _log.warning("no se pudieron leer las fuentes (%s): se dibujara '--'", exc)
            self._valores = {}
        _anotar_historial(self._valores)

    def valor(self, nombre):
        if nombre is None:
            return None
        if self._valor_delegado:
            try:
                valor = self._objeto.valor(nombre)
            except Exception:                             # una fuente rota no tumba el tema
                valor = None
            if valor is not None:
                return valor
        clave = nombre_a_clave(nombre)
        if clave in self._valores:
            return self._valores[clave]
        return _valor_sintetico(clave, self._valores)

    def serie(self, nombre):
        if self._serie_delegada:
            try:
                serie = self._objeto.serie(nombre)
            except Exception:
                serie = None
            if serie:
                return list(serie)
        clave = nombre_a_clave(nombre)
        if self._hist_externo is not None:
            propia = self._hist_externo.get(clave)
            if isinstance(propia, (list, tuple)) and propia:
                return list(propia)
        with _HISTORIAL_LOCK:
            return list(_HISTORIAL.get(clave, ()))

    def muestra(self):
        return dict(self._valores)


# --------------------------------------------------------------------------- encaje
def _encajar(dib, contenido, tamano, limite, maximo=None, negrita=False):
    """Fuente cuyo texto quepa en `limite` (reduce el cuerpo si hace falta).

    El cuerpo de partida nunca supera TAMANO_MAXIMO y la reduccion se calcula por
    proporcion (el ancho de un texto crece casi linealmente con el cuerpo), con unos
    pocos ajustes finos. Asi un `tamano` disparatado no cuesta 1,7 s por widget.
    """
    if maximo is not None:
        tamano = maximo
    try:
        cuerpo = int(min(max(1, int(tamano)), TAMANO_MAXIMO))
    except (TypeError, ValueError):
        cuerpo = 12
    fuente = _fuente(cuerpo, negrita)
    if limite is None or limite <= 0:
        return fuente
    ancho = dib.textlength(contenido, font=fuente)
    if ancho <= limite or ancho <= 0:
        return fuente

    objetivo = int(cuerpo * float(limite) / float(ancho))
    objetivo = max(TAMANO_MINIMO, min(cuerpo, objetivo))
    fuente = _fuente(objetivo, negrita)
    intentos = 0
    while (objetivo > TAMANO_MINIMO and intentos < 24
           and dib.textlength(contenido, font=fuente) > limite):
        objetivo -= 1
        fuente = _fuente(objetivo, negrita)
        intentos += 1
    return fuente


# --------------------------------------------------------------------------- widgets
def dibujar_dato(dib, w, fuentes):
    """Tarjeta con etiqueta, valor grande, linea de detalle y barra de progreso."""
    x = _entero(w, "x", 0)
    y = _entero(w, "y", 0)
    ancho = _entero(w, "ancho", 340)
    alto = _entero(w, "alto", 300)
    if ancho <= 0 or alto <= 0:
        raise ErrorWidget("la tarjeta necesita 'ancho' y 'alto' positivos")
    radio = _radio(w, 18, ancho, alto)

    dib.rounded_rectangle([x, y, x + ancho, y + alto], radius=radio,
                          fill=color(w.get("fondo", TARJETA)),
                          outline=color(w.get("borde", BORDE)), width=2)
    dib.text((x + 28, y + 24), str(w.get("etiqueta", "")), font=_fuente(26),
             fill=color(w.get("color_etiqueta", GRIS)))

    # `sufijo` es el nombre que usa el dashboard nuevo; `unidad` es el del editor.
    unidad = w.get("unidad")
    if not unidad:
        unidad = w.get("sufijo", "")
    unidad = str(unidad)
    valor = formatear(fuentes.valor(w.get("fuente", "")), unidad)
    tamano = _tamano(w, "tamano", 74)
    fuente_valor = _encajar(dib, valor, tamano, ancho - 90)
    dib.text((x + 24, y + 74), valor, font=fuente_valor, fill=color(w.get("color", TEXTO)))
    if unidad:
        ancho_valor = dib.textlength(valor, font=fuente_valor)
        dib.text((x + 34 + ancho_valor, y + 74 + tamano * 0.42), unidad, font=_fuente(28),
                 fill=color(w.get("color_etiqueta", GRIS)))

    # Segunda linea con otro dato. Si al resolver los marcadores no queda ningun digito
    # (el equipo no expone esos sensores) NO se dibuja: "-- C  -- MHz" queda peor.
    if w.get("detalle"):
        texto_detalle = texto_con_marcadores(str(w["detalle"]), fuentes)
        if any(caracter.isdigit() for caracter in texto_detalle):
            fuente_detalle = _encajar(dib, texto_detalle, _tamano(w, "tamano_detalle", 30),
                                      ancho - 56)
            dib.text((x + 28, y + 74 + tamano + 26), texto_detalle, font=fuente_detalle,
                     fill=color(w.get("color_detalle", "#9aa6b8")))

    if w.get("barra", True):
        numero = fuentes.valor(w.get("fuente", ""))
        bx, by, bw, bh = x + 28, y + alto - 102, ancho - 56, 26
        if bw > 0:
            dib.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8,
                                  fill=color(PISTA))
            if _es_numero(numero):
                minimo = _numero(w, "min", 0)
                maximo = _numero(w, "max", 100)
                fraccion = 0.0 if maximo == minimo else (numero - minimo) / (maximo - minimo)
                relleno = max(0.0, min(1.0, fraccion)) * bw
                if relleno > 2:
                    dib.rounded_rectangle([bx, by, bx + relleno, by + bh], radius=8,
                                          fill=color(w.get("color_relleno", ACENTO)))
        if w.get("mostrar_porcentaje", False):
            dib.text((bx, by + 36), ("%s %s" % (valor, unidad)).strip(), font=_fuente(20),
                     fill=color(w.get("color_etiqueta", GRIS)))


def dibujar_barra(dib, w, fuentes):
    """Etiqueta encima y barra horizontal con el valor a la derecha."""
    x = _entero(w, "x", 0)
    y = _entero(w, "y", 0)
    ancho = _entero(w, "ancho", 500)
    alto = _entero(w, "alto", 34)
    if ancho <= 0 or alto <= 0:
        raise ErrorWidget("la barra necesita 'ancho' y 'alto' positivos")
    unidad = w.get("unidad")
    if not unidad:
        unidad = w.get("sufijo", "%")
    unidad = str(unidad)
    valor = fuentes.valor(w.get("fuente", ""))

    dib.text((x, y - 30), str(w.get("etiqueta", "")), font=_fuente(22),
             fill=color(w.get("color_etiqueta", GRIS)))
    dib.rounded_rectangle([x, y, x + ancho, y + alto], radius=max(0, alto // 2),
                          fill=color(w.get("fondo", PISTA)))
    if _es_numero(valor):
        minimo = _numero(w, "min", 0)
        maximo = _numero(w, "max", 100)
        fraccion = 0.0 if maximo == minimo else (valor - minimo) / (maximo - minimo)
        relleno = max(0.0, min(1.0, fraccion)) * ancho
        if relleno > 2:
            dib.rounded_rectangle([x, y, x + relleno, y + alto], radius=max(0, alto // 2),
                                  fill=color(w.get("color_relleno", ACENTO)))
    dib.text((x + ancho + 16, y + 4), ("%s %s" % (formatear(valor, unidad), unidad)).strip(),
             font=_fuente(22), fill=color(w.get("color", TEXTO)))


def dibujar_grafica(dib, w, fuentes):
    """Grafica de linea con el historial de una fuente."""
    x = _entero(w, "x", 0)
    y = _entero(w, "y", 0)
    ancho = _entero(w, "ancho", 560)
    alto = _entero(w, "alto", 120)
    if ancho <= 0 or alto <= 0:
        raise ErrorWidget("la grafica necesita 'ancho' y 'alto' positivos")
    puntos = _entero(w, "puntos", 120)
    if puntos < 1:
        raise ErrorWidget("'puntos' tiene que ser >= 1, no %r" % (w.get("puntos"),))
    grosor = w.get("grosor", 3)
    if not _es_numero(grosor):
        raise ErrorWidget("'grosor' tiene que ser un numero, no %r" % (grosor,))
    grosor = max(1, int(grosor))

    serie = [float(valor) for valor in fuentes.serie(w.get("fuente", ""))
             if _es_numero(valor)][-puntos:]

    dib.rounded_rectangle([x, y, x + ancho, y + alto], radius=_radio(w, 14, ancho, alto),
                          fill=color(w.get("fondo", TARJETA)),
                          outline=color(w.get("borde", BORDE)), width=2)
    if w.get("etiqueta"):
        dib.text((x + 18, y + 10), str(w["etiqueta"]), font=_fuente(20),
                 fill=color(w.get("color_etiqueta", GRIS)))

    if "min" in w:
        minimo = _numero(w, "min", 0)
    else:
        minimo = min(serie) if serie else 0.0
    if "max" in w:
        maximo = _numero(w, "max", 100)
    else:
        maximo = (max(serie) if serie else 100.0) or 1.0
    if w.get("auto") and len(serie) > 1:
        # Autoescala: si el rango real es mucho menor que 0-100 (una CPU al 8 %) la linea
        # se aplastaba abajo; asi se ven las variaciones.
        bajo, alto_serie = min(serie), max(serie)
        margen = max(1.0, (alto_serie - bajo) * 0.25)
        minimo = max(0.0, bajo - margen)
        maximo = alto_serie + margen
    rango = (maximo - minimo) or 1.0

    izq, der = x + 18, x + ancho - 18
    arr, aba = y + 34, y + alto - 16
    if der <= izq:
        der = izq + 1
    if aba <= arr:
        aba = arr + 1
    unidad = str(w.get("unidad", "") or w.get("sufijo", "") or "")

    def escala(valor):
        return max(0.0, min(1.0, (valor - minimo) / rango))

    if len(serie) < 2:
        # Aun no hay historial: linea plana con el valor actual, mejor que un hueco.
        actual = fuentes.valor(w.get("fuente", ""))
        if _es_numero(actual):
            yy = aba - (aba - arr) * escala(actual)
            dib.line([izq, yy, der, yy], fill=color(w.get("color_relleno", ACENTO)),
                     width=grosor)
            dib.text((der - 90, y + 10),
                     ("%s %s" % (formatear(actual, unidad), unidad)).strip(),
                     font=_fuente(24, True), fill=color(w.get("color", TEXTO)))
        dib.text((izq, aba - 6), "recogiendo historial...", font=_fuente(16),
                 fill=color(w.get("color_etiqueta", GRIS)))
        return

    for i in range(1, 4):
        yy = arr + (aba - arr) * i / 4
        dib.line([izq, yy, der, yy], fill=color(REJILLA), width=1)

    puntos_xy = []
    for i, valor in enumerate(serie):
        px = izq + (der - izq) * i / max(1, len(serie) - 1)
        py = aba - (aba - arr) * escala(valor)
        puntos_xy.append((px, py))
    relleno = color(w.get("color_relleno", ACENTO))
    dib.polygon(puntos_xy + [(der, aba), (izq, aba)],
                fill=tuple(int(componente * 0.22) for componente in relleno))
    dib.line(puntos_xy, fill=relleno, width=grosor, joint="curve")
    dib.text((der - 90, y + 10),
             ("%s %s" % (formatear(serie[-1], unidad), unidad)).strip(),
             font=_fuente(24, True), fill=color(w.get("color", TEXTO)))


def dibujar_texto(dib, w, fuentes):
    """Texto libre con marcadores. Admite varias lineas separadas por \\n."""
    x = _entero(w, "x", 0)
    y = _entero(w, "y", 0)
    contenido = texto_con_marcadores(w.get("texto", ""), fuentes)
    tamano = _tamano(w, "tamano", 28)
    limite = _entero(w, "ancho", 0)
    if limite <= 0:
        limite = ANCHO - x - 24
    lineas = contenido.split("\n") or [""]
    base = _fuente(tamano)
    mas_larga = max(lineas, key=lambda linea: dib.textlength(linea, font=base))
    fuente = _encajar(dib, mas_larga, tamano, limite)
    color_texto = color(w.get("color", TEXTO))
    interlineado = max(1, int(round(tamano * 1.3)))
    for i, linea in enumerate(lineas):
        dib.text((x, y + i * interlineado), linea, font=fuente, fill=color_texto)


def dibujar_reloj(dib, w, fuentes):
    """Reloj con formato de strftime."""
    x = _entero(w, "x", 0)
    y = _entero(w, "y", 0)
    formato = w.get("formato", "%H:%M")
    if not isinstance(formato, str):
        raise ErrorWidget("'formato' tiene que ser texto, no %r" % (formato,))
    try:
        texto = time.strftime(formato)
    except (ValueError, TypeError) as exc:
        raise ErrorWidget("formato de hora invalido %r: %s" % (formato, exc))
    tamano = _tamano(w, "tamano", 120)
    limite = _entero(w, "ancho", 0)
    if limite <= 0:
        limite = ANCHO - x - 24
    fuente = _encajar(dib, texto, tamano, limite, negrita=True)
    dib.text((x, y), texto, font=fuente, fill=color(w.get("color", TEXTO)))


TIPOS = {
    "dato": dibujar_dato, "data": dibujar_dato,
    "barra": dibujar_barra, "bar": dibujar_barra,
    "grafica": dibujar_grafica, "chart": dibujar_grafica,
    "texto": dibujar_texto, "text": dibujar_texto,
    "reloj": dibujar_reloj, "clock": dibujar_reloj,
}


# --------------------------------------------------------------------------- motor
def _dimension_del_tema(tema, campo, defecto):
    valor = tema.get(campo, defecto)
    if _es_numero(valor) and 16 <= int(valor) <= 8192:
        return int(valor)
    if campo in tema:
        _registrar_problema("'%s' del tema invalido (%r): se usa %d"
                            % (campo, valor, defecto), valor)
    return defecto


def _guardar_fallos(fallos):
    with _fallos_lock:
        ULTIMOS_FALLOS[:] = fallos
    _local.fallos = list(fallos)


def ultimos_fallos():
    """Fallos del ultimo render de ESTE hilo (lista de dicts: indice, tipo, mensaje).

    Si el hilo no ha renderizado nada todavia, devuelve el ultimo render de cualquier
    hilo (el contenido de `ULTIMOS_FALLOS`). Lista vacia = todos los widgets se dibujaron.
    """
    propios = getattr(_local, "fallos", None)
    if propios is not None:
        return list(propios)
    with _fallos_lock:
        return list(ULTIMOS_FALLOS)


def renderizar(tema, ruta, fuentes=None):
    """Dibuja el tema en `ruta` (PNG) y devuelve la ruta.

    Ningun widget puede abortar el render: cada uno va en su propio try/except y lo que
    falla se acumula en `ULTIMOS_FALLOS` (visible con `ultimos_fallos()`). Si el tema
    entero no es un dict, se dibuja un panel vacio con el fondo por defecto.
    """
    from PIL import Image, ImageDraw

    if not isinstance(tema, dict):
        _registrar_problema("el tema no es un objeto JSON (es %s): se dibuja vacio"
                            % type(tema).__name__, tema)
        tema = {}

    ancho = _dimension_del_tema(tema, "ancho", ANCHO)
    alto = _dimension_del_tema(tema, "alto", ALTO)
    lector = _Lector(fuentes)
    imagen = Image.new("RGB", (ancho, alto), color(tema.get("fondo", FONDO), color(FONDO)))
    dib = ImageDraw.Draw(imagen)

    widgets_del_tema = tema.get("widgets")
    if not isinstance(widgets_del_tema, list):
        _registrar_problema("el tema no trae una lista 'widgets': se dibuja solo el fondo",
                            widgets_del_tema)
        widgets_del_tema = []

    fallos = []
    for indice, w in enumerate(widgets_del_tema):
        if not isinstance(w, dict):
            fallos.append({"indice": indice, "tipo": "?",
                           "mensaje": "el widget no es un objeto (%s)" % type(w).__name__})
            continue
        tipo = str(w.get("tipo", "")).lower()
        funcion = TIPOS.get(tipo)
        if funcion is None:
            fallos.append({"indice": indice, "tipo": tipo or "?",
                           "mensaje": "tipo de widget desconocido: %r" % (w.get("tipo"),)})
            continue
        try:
            funcion(dib, w, lector)
        except Exception as exc:                          # un widget no tumba el tema
            fallos.append({"indice": indice, "tipo": tipo,
                           "mensaje": "%s: %s" % (type(exc).__name__, exc)})
    _guardar_fallos(fallos)
    for fallo in fallos:
        _log.warning("fallo el widget %s (indice %s): %s",
                     fallo["tipo"], fallo["indice"], fallo["mensaje"])

    imagen.save(ruta, "PNG")
    return ruta


def renderizar_datos(tema, fuentes=None, formato="PNG"):
    """PNG en memoria (bytes). Seguro para llamarse desde varios hilos a la vez.

    Usa un temporal con nombre unico (`tempfile.mkstemp`) que se borra SIEMPRE en un
    `finally`, incluso si el `formato` no es valido y Pillow lanza al guardar.
    """
    import io
    import tempfile

    from PIL import Image

    descriptor, temporal = tempfile.mkstemp(prefix="cfv235-render-", suffix=".png")
    os.close(descriptor)
    try:
        renderizar(tema, temporal, fuentes)
        with Image.open(temporal) as imagen:
            buffer = io.BytesIO()
            try:
                imagen.save(buffer, formato)
            except (KeyError, ValueError) as exc:
                raise ValueError("formato de imagen no soportado: %r (%s)"
                                 % (formato, exc))
        return buffer.getvalue()
    finally:
        try:
            os.unlink(temporal)
        except OSError:
            pass


def tema_por_defecto():
    """Tema de ejemplo: lo mismo que dibujaba el dashboard fijo, pero editable."""
    return {
        "version": 1,
        "ancho": ANCHO, "alto": ALTO, "fondo": FONDO,
        "widgets": [
            {"tipo": "reloj", "x": 60, "y": 52, "tamano": 122, "formato": "%H:%M"},
            {"tipo": "texto", "x": 66, "y": 226, "tamano": 30, "color": GRIS,
             "texto": "{fecha}"},
            {"tipo": "texto", "x": 66, "y": 268, "tamano": 22, "color": "#5c6675",
             "texto": "{cpu_modelo}"},
            {"tipo": "grafica", "x": 60, "y": 300, "ancho": 570, "alto": 142,
             "etiqueta": "CPU (%)", "fuente": "CPU Usage", "min": 0, "max": 100,
             "unidad": "%", "puntos": 120, "color_relleno": ACENTO, "auto": True},
            {"tipo": "dato", "x": 680, "y": 60, "ancho": 340, "alto": 342,
             "etiqueta": "CPU", "fuente": "CPU Usage", "unidad": "%",
             "color_relleno": ACENTO, "detalle": "{cpu_temp} C      {cpu_mhz} MHz"},
            {"tipo": "dato", "x": 1050, "y": 60, "ancho": 340, "alto": 342,
             "etiqueta": "MEMORIA", "fuente": "Memory Usage", "unidad": "%",
             "color_relleno": ACENTO2,
             "detalle": "{ram_usado_gb} / {ram_total_gb} GB"},
            {"tipo": "dato", "x": 1420, "y": 60, "ancho": 340, "alto": 342,
             "etiqueta": "GPU", "fuente": "GPU Usage", "unidad": "%",
             "color_relleno": ACENTO, "detalle": "{gpu_temp} C      {gpu_mhz} MHz"},
            {"tipo": "texto", "x": 680, "y": 418, "tamano": 22, "color": GRIS,
             "texto": "CPU {cpu_temp} C    {cpu_mhz} MHz    {cpu_uso} %    "
                      "RAM {ram_usado_gb}/{ram_total_gb} GB    Disco {disco_uso}%    "
                      "Red {red_bajada_mb} MB/s"},
        ],
    }


__all__ = [
    "ANCHO", "ALTO", "TIPOS", "MAPA_EDITOR", "ALIAS_CLAVES", "CLAVES_CONOCIDAS",
    "ErrorWidget", "color", "es_color", "formatear", "nombre_a_clave",
    "texto_con_marcadores", "crear_fuentes", "renderizar", "renderizar_datos",
    "dibujar_dato", "dibujar_barra", "dibujar_grafica", "dibujar_texto", "dibujar_reloj",
    "ultimos_fallos", "ultimos_problemas", "ULTIMOS_FALLOS", "FALLOS", "PROBLEMAS",
    "tema_por_defecto", "TAMANO_MAXIMO", "TAMANO_MINIMO",
]
