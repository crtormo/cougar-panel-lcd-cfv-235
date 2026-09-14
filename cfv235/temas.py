"""temas.py - el JSON que describe lo que se dibuja en los 1920x462 del panel.

Un tema es un diccionario sencillo, pensado para que el editor grafico lo lea y lo
escriba sin conversiones:

    {
      "version": 1,
      "ancho": 1920, "alto": 462, "fondo": "#0e1117",
      "widgets": [
        {"tipo": "reloj",  "x": 60, "y": 52, "tamano": 122, "formato": "%H:%M"},
        {"tipo": "dato",   "x": 680, "y": 60, "ancho": 340, "alto": 342,
         "etiqueta": "CPU", "fuente": "CPU Usage", "unidad": "%",
         "detalle": "{cpu_temp} C   {cpu_mhz} MHz"},
        {"tipo": "grafica", "x": 60, "y": 300, "ancho": 570, "alto": 142,
         "etiqueta": "CPU (%)", "fuente": "CPU Usage", "puntos": 120, "auto": True}
      ]
    }

Este modulo es la cara "de datos" del motor de dibujo (`cfv235.widgets`): el esquema
(`CATALOGO`), los valores por defecto de cada tipo, las fuentes de datos que acepta el
motor (`fuentes_disponibles`), los marcadores de texto (`marcadores`), el validador
(`validar`) y el guardado/carga en JSON.

Dependencia de `cfv235.sensores`
--------------------------------
La importacion de `sensores` es **perezosa y tolerante**: si el modulo no existe o falla,
este modulo se importa y funciona igual (las listas de claves conocidas tienen respaldo
propio) y los temas se dibujan con "--". Asi `temas` y `widgets` se pueden probar sin el.

Requiere Pillow >= 8.2 (por `ImageDraw.rounded_rectangle`); ver `widgets.py`.
"""

import json
import logging
import os
import time

from . import widgets as motor

ANCHO, ALTO = motor.ANCHO, motor.ALTO
VERSION = 1

_log = logging.getLogger("cfv235.temas")
_log.addHandler(logging.NullHandler())

# Avisos no fatales del ultimo `normalizar`/`cargar` (temas corruptos, fondos invalidos).
AVISOS = []

# --------------------------------------------------------------------------- catalogo
# Campos de cada tipo: CATALOGO[tipo][campo] = (tipo_python, por_defecto, ayuda).
# Los campos del editor oficial que NO estan implementados se listan aparte, en
# `CAMPOS_DEL_EDITOR`, para que quien migre un tema sepa que se ignoran.
CATALOGO = {
    "dato": {
        "x": (int, 0, "esquina izquierda"),
        "y": (int, 0, "borde superior"),
        "ancho": (int, 340, "ancho de la tarjeta"),
        "alto": (int, 300, "alto de la tarjeta"),
        "etiqueta": (str, "", "titulo pequeno de arriba"),
        "fuente": (str, "", "fuente de datos (nombre del editor o clave interna)"),
        "unidad": (str, "", "se dibuja pequena junto al valor"),
        "sufijo": (str, "", "como 'unidad', pero con el espacio incluido (' C')"),
        "detalle": (str, "", "linea de abajo; admite {marcadores}"),
        "tamano": (int, 74, "cuerpo del valor (se encoge solo si no cabe)"),
        "tamano_detalle": (int, 30, "cuerpo de la linea de detalle"),
        "barra": (bool, True, "dibujar la barra de progreso"),
        "mostrar_porcentaje": (bool, False, "escribir el % sobre la barra"),
        "radio": (int, 18, "redondeo de las esquinas"),
        "min": (int, 0, "minimo de la barra"),
        "max": (int, 100, "maximo de la barra"),
        "color": (str, "#ebf0f8", "color del valor"),
        "color_etiqueta": (str, "#788291", "color de etiqueta y unidad"),
        "color_detalle": (str, "#9aa6b8", "color de la linea de detalle"),
        "color_relleno": (str, "#00d0ff", "color de la barra"),
        "fondo": (str, "#161b24", "fondo de la tarjeta"),
        "borde": (str, "#2c3442", "borde de la tarjeta"),
    },
    "barra": {
        "x": (int, 0, "esquina izquierda"),
        "y": (int, 0, "borde superior"),
        "ancho": (int, 500, "largo de la barra"),
        "alto": (int, 34, "grosor"),
        "etiqueta": (str, "", "rotulo encima"),
        "fuente": (str, "", "fuente de datos"),
        "unidad": (str, "%", "unidad"),
        "sufijo": (str, "", "como 'unidad', pero con el espacio incluido"),
        "min": (int, 0, "minimo"),
        "max": (int, 100, "maximo"),
        "color": (str, "#ebf0f8", "color del valor"),
        "color_etiqueta": (str, "#788291", "color del rotulo"),
        "color_relleno": (str, "#00d0ff", "color de la barra"),
        "fondo": (str, "#262d3a", "color de la pista"),
    },
    "grafica": {
        "x": (int, 0, "esquina izquierda"),
        "y": (int, 0, "borde superior"),
        "ancho": (int, 560, "ancho"),
        "alto": (int, 120, "alto"),
        "etiqueta": (str, "", "rotulo"),
        "fuente": (str, "", "fuente de datos"),
        "puntos": (int, 120, "cuantos valores del historial se dibujan"),
        "auto": (bool, False, "escala automatica con los datos (si no, usa min/max)"),
        "min": (int, 0, "minimo fijo"),
        "max": (int, 100, "maximo fijo"),
        "grosor": (int, 3, "grosor de la linea"),
        "radio": (int, 14, "redondeo del marco"),
        "unidad": (str, "", "unidad"),
        "sufijo": (str, "", "como 'unidad', pero con el espacio incluido"),
        "color": (str, "#ebf0f8", "color del valor actual"),
        "color_etiqueta": (str, "#788291", "color del rotulo"),
        "color_relleno": (str, "#00d0ff", "color de la linea"),
        "fondo": (str, "#161b24", "fondo"),
        "borde": (str, "#2c3442", "borde"),
    },
    "texto": {
        "x": (int, 0, "esquina izquierda"),
        "y": (int, 0, "borde superior"),
        "texto": (str, "", "texto; admite {clave}, {clave:.1f} y {fuente:Nombre}"),
        "tamano": (int, 28, "cuerpo (se encoge solo si no cabe)"),
        "ancho": (int, 0, "limite de ancho; 0 = hasta el borde del panel"),
        "color": (str, "#ebf0f8", "color"),
    },
    "reloj": {
        "x": (int, 0, "esquina izquierda"),
        "y": (int, 0, "borde superior"),
        "formato": (str, "%H:%M", "formato strftime, p. ej. %H:%M o %H:%M:%S"),
        "tamano": (int, 120, "cuerpo"),
        "ancho": (int, 0, "limite de ancho; 0 = hasta el borde del panel"),
        "color": (str, "#ebf0f8", "color"),
    },
}

DESCRIPCIONES = {
    "dato": "Tarjeta con etiqueta, valor grande, detalle y barra de progreso.",
    "barra": "Barra horizontal con etiqueta y valor.",
    "grafica": "Grafica de linea con historial (necesita dos muestras para dibujar).",
    "texto": "Linea (o varias) de texto libre con {marcadores} de datos.",
    "reloj": "Reloj con formato de strftime.",
}

# Alias que acepta el motor al leer un tema. Son los nombres del editor oficial y los
# nuestros, para que un tema escrito con cualquiera de los dos funcione tal cual.
ALIAS_TIPOS = {
    "clock": "reloj",
    "chart": "grafica",
    "bar": "barra",
    "text": "texto",
    "data": "dato",
}

# Campos del editor oficial de COUGAR que este motor NO implementa: se aceptan (no
# rompen nada) pero se ignoran, y `validar` avisa de que estan de mas.
CAMPOS_DEL_EDITOR = ["showPrefix", "addZero", "rotate", "maxTickCount", "cornerRadius"]


# --------------------------------------------------------------------------- avisos
def _avisar(mensaje):
    """Apunta un aviso no fatal (y lo manda al log). Nunca lanza."""
    try:
        AVISOS.append(mensaje)
        del AVISOS[:-64]
    except Exception:                                     # pragma: no cover (defensivo)
        pass
    _log.warning("%s", mensaje)
    return mensaje


def ultimos_avisos():
    """Copia de los ultimos avisos de `normalizar`/`cargar`."""
    return list(AVISOS)


# --------------------------------------------------------------------------- sensores
_cache_sensores = {"hecho": False, "modulo": None}


def _modulo_sensores():
    """`cfv235.sensores` si se puede importar (perezoso), o None. Nunca lanza."""
    if not _cache_sensores["hecho"]:
        try:
            from . import sensores
            _cache_sensores["modulo"] = sensores
        except Exception as exc:                          # el modulo puede no existir aun
            _log.info("cfv235.sensores no disponible (%s): se trabaja sin sus claves", exc)
            _cache_sensores["modulo"] = None
        _cache_sensores["hecho"] = True
    return _cache_sensores["modulo"]


def _claves_datos():
    """Claves internas que el motor sabe resolver (respaldo + catalogo de sensores)."""
    claves = set(motor.CLAVES_CONOCIDAS)
    claves.update(motor.ALIAS_CLAVES.values())
    modulo = _modulo_sensores()
    catalogo = getattr(modulo, "CATALOGO", None) if modulo is not None else None
    if isinstance(catalogo, (list, tuple)):
        for fila in catalogo:
            try:
                claves.add(fila[0])
            except (TypeError, IndexError, KeyError):
                continue
    return {clave for clave in claves if clave}


def fuentes_disponibles(instanciar=True):
    """Fuentes de datos que se pueden pedir en `fuente`.

    Devuelve `{"editor": [...], "propias": [...], "valores": {...}}`. Los nombres de
    "editor" son los del editor oficial de COUGAR; las "propias" son las claves internas
    (`cpu_temp`, `ram_uso`...), que se pueden usar igual. Con `instanciar=True` y
    `cfv235.sensores` disponible, "valores" trae una muestra real.
    """
    datos = {
        "editor": sorted(motor.MAPA_EDITOR),
        "propias": sorted(_claves_datos()),
        "valores": {},
    }
    if instanciar:
        modulo = _modulo_sensores()
        clase = getattr(modulo, "Sensores", None) if modulo is not None else None
        if callable(clase):
            try:
                datos["valores"] = clase().muestra()
            except Exception as exc:                      # sin /proc, sin permisos...
                _log.info("no se pudo tomar una muestra de sensores: %s", exc)
    return datos


def marcadores():
    """Marcadores que se pueden escribir entre llaves en `texto` y en `detalle`."""
    return {
        "clave": sorted(_claves_datos()),
        "editor": sorted(motor.MAPA_EDITOR),
        "forma": [
            "{cpu_temp}",
            "{cpu_uso:.0f}",
            "{ram_usado_gb:.1f}",
            "{hora}",
            "{fecha}",
            "{uptime}",
            "{fuente:CPU Usage}",
        ],
    }


# --------------------------------------------------------------------------- catalogo api
def _tipo_normalizado(tipo):
    if not isinstance(tipo, str):
        return ""
    clave = tipo.strip().lower()
    return ALIAS_TIPOS.get(clave, clave)


def campos(tipo):
    """Campos de un tipo de widget: dict nombre -> (tipo_python, por_defecto, ayuda)."""
    normalizado = _tipo_normalizado(tipo)
    if normalizado not in CATALOGO:
        raise ValueError("tipo de widget desconocido: %r" % (tipo,))
    return CATALOGO[normalizado]


def catalogo_json():
    """El catalogo en algo que se puede mandar por JSON (los tipos van como texto).

    Es lo que consume el editor grafico para construir su panel de propiedades sin
    duplicar la lista de campos.
    """
    salida = {}
    for tipo, definicion in CATALOGO.items():
        campos_json = {}
        for nombre, (clase, por_defecto, ayuda) in definicion.items():
            campos_json[nombre] = {
                "tipo": getattr(clase, "__name__", str(clase)),
                "por_defecto": por_defecto,
                "ayuda": ayuda,
            }
        salida[tipo] = {
            "descripcion": DESCRIPCIONES.get(tipo, ""),
            "campos": campos_json,
            "alias": sorted(alias for alias, destino in ALIAS_TIPOS.items()
                            if destino == tipo),
        }
    return salida


def plantilla(tipo, **valores):
    """Crea un widget de `tipo` con los valores por defecto y los que le pases."""
    normalizado = _tipo_normalizado(tipo)
    if normalizado not in CATALOGO:
        raise ValueError("tipo de widget desconocido: %r" % (tipo,))
    widget = {"tipo": normalizado}
    for nombre, (_clase, por_defecto, _ayuda) in CATALOGO[normalizado].items():
        widget[nombre] = por_defecto
    for nombre, valor in valores.items():
        if nombre != "tipo" and nombre not in widget:
            raise ValueError("el widget %r no tiene el campo %r" % (normalizado, nombre))
        widget[nombre] = valor
    return widget


def tema_vacio():
    """Tema valido sin ningun widget (lo que devuelve `normalizar` con basura)."""
    return {"version": VERSION, "ancho": ANCHO, "alto": ALTO, "fondo": motor.FONDO,
            "widgets": []}


def tema_nuevo(widgets_=None):
    """Tema vacio (o con los widgets que le pases) listo para llenar."""
    tema = tema_vacio()
    tema["widgets"] = list(widgets_ or [])
    return tema


def tema_por_defecto():
    """El tema de ejemplo (el mismo que `ejemplos/tema_dashboard.json`)."""
    return motor.tema_por_defecto()


# --------------------------------------------------------------------------- ficheros
def cargar(ruta):
    """Lee un tema de un JSON.

    Si el fichero no existe, no se puede leer o no es JSON valido, devuelve el tema de
    ejemplo y deja el motivo en `AVISOS` (`ultimos_avisos()`).
    """
    if not ruta:
        _avisar("cargar: sin ruta; se usa el tema de ejemplo")
        return tema_por_defecto()
    try:
        with open(ruta, encoding="utf-8") as fichero:
            return json.load(fichero)
    except FileNotFoundError:
        _avisar("cargar: no existe %r; se usa el tema de ejemplo" % (ruta,))
        return tema_por_defecto()
    except (OSError, json.JSONDecodeError) as exc:
        _avisar("cargar: no se pudo leer %r (%s); se usa el tema de ejemplo" % (ruta, exc))
        return tema_por_defecto()


def guardar(ruta, tema):
    """Escribe el tema con sangria legible (es lo que hace el editor al guardar).

    No normaliza: guarda exactamente lo que le des, para que el editor pueda hacer un
    ciclo cargar -> editar -> guardar sin perder campos.
    """
    with open(ruta, "w", encoding="utf-8") as fichero:
        json.dump(tema, fichero, indent=2, ensure_ascii=False)
    return None


# --------------------------------------------------------------------------- validar
def _es_numero(valor):
    """Numero de verdad: los booleanos NO cuentan (True no es una coordenada)."""
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _es_entero(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _tipo_de(valor):
    if valor is None:
        return "nulo"
    if isinstance(valor, bool):
        return "booleano"
    if isinstance(valor, int):
        return "entero"
    if isinstance(valor, float):
        return "decimal"
    if isinstance(valor, str):
        return "texto"
    if isinstance(valor, (list, tuple)):
        return "lista"
    if isinstance(valor, dict):
        return "objeto"
    return type(valor).__name__


def _color_valido(valor):
    """None y "" son validos (significan "usa el color por defecto del motor")."""
    if valor is None or valor == "":
        return True
    return motor.es_color(valor)


def _fuente_conocida(nombre, conocidas):
    if not isinstance(nombre, str):
        return False
    return nombre in motor.MAPA_EDITOR or motor.nombre_a_clave(nombre) in conocidas


def _validar_widget(indice, w, conocidas, estricto):
    """Problemas de un widget. Lista vacia = se dibuja sin sorpresas."""
    if not isinstance(w, dict):
        return ["widget %d: no es un objeto (%s)" % (indice, _tipo_de(w))]

    problemas = []
    tipo_crudo = w.get("tipo")
    tipo = _tipo_normalizado(tipo_crudo)
    quien = "widget %d (%s)" % (indice, tipo or _tipo_de(tipo_crudo))
    if tipo not in CATALOGO:
        return ["%s: tipo desconocido %r (validos: %s)"
                % (quien, tipo_crudo, ", ".join(sorted(CATALOGO)))]

    # --- coordenadas: numericas, no booleanas, dentro del panel ---
    x, y = w.get("x", 0), w.get("y", 0)
    for campo, valor, limite in (("x", x, ANCHO), ("y", y, ALTO)):
        if not _es_numero(valor):
            problemas.append("%s: '%s' tiene que ser un numero, no %s"
                             % (quien, campo, _tipo_de(valor)))
        elif not 0 <= valor < limite:
            problemas.append("%s: %s=%s queda fuera del panel (0..%d)"
                             % (quien, campo, valor, limite - 1))

    # --- tamano de la caja ---
    ancho_w = alto_w = None
    for campo in ("ancho", "alto"):
        if campo not in w:
            continue
        valor = w[campo]
        if not _es_numero(valor):
            problemas.append("%s: '%s' tiene que ser un numero, no %s"
                             % (quien, campo, _tipo_de(valor)))
            continue
        if campo == "ancho":
            ancho_w = valor
            if valor == 0 and tipo in ("dato", "barra", "grafica"):
                problemas.append("%s: 'ancho' tiene que ser positivo (es 0)" % quien)
            elif valor < 0:
                problemas.append("%s: 'ancho' no puede ser negativo (es %s)"
                                 % (quien, valor))
        else:
            alto_w = valor
            if valor <= 0:
                problemas.append("%s: 'alto' tiene que ser positivo (es %s)"
                                 % (quien, valor))

    # --- no salirse del panel por tamano ---
    if ancho_w and _es_numero(x) and x + ancho_w > ANCHO:
        problemas.append("%s: se sale del panel a lo ancho (x=%s + ancho=%s > %d)"
                         % (quien, x, ancho_w, ANCHO))
    if alto_w and _es_numero(y) and y + alto_w > ALTO:
        problemas.append("%s: se sale del panel a lo alto (y=%s + alto=%s > %d)"
                         % (quien, y, alto_w, ALTO))

    # --- cuerpos de letra ---
    for campo in ("tamano", "tamano_detalle"):
        if campo not in w:
            continue
        valor = w[campo]
        if not _es_numero(valor):
            problemas.append("%s: '%s' tiene que ser un numero, no %s"
                             % (quien, campo, _tipo_de(valor)))
        elif valor <= 0:
            problemas.append("%s: '%s' tiene que ser positivo (es %s)"
                             % (quien, campo, valor))
        elif valor > motor.TAMANO_MAXIMO:
            problemas.append("%s: '%s'=%s es enorme; el motor lo recorta a %d"
                             % (quien, campo, valor, motor.TAMANO_MAXIMO))

    # --- redondeo, puntos y grosor ---
    if "radio" in w:
        valor = w["radio"]
        if not _es_numero(valor):
            problemas.append("%s: 'radio' tiene que ser un numero, no %s"
                             % (quien, _tipo_de(valor)))
        elif valor < 0:
            problemas.append("%s: 'radio' no puede ser negativo (es %s)" % (quien, valor))
    if "puntos" in w:
        valor = w["puntos"]
        if not _es_numero(valor):
            problemas.append("%s: 'puntos' tiene que ser un numero, no %s"
                             % (quien, _tipo_de(valor)))
        elif valor < 1:
            problemas.append("%s: 'puntos' tiene que ser 1 o mas (es %s)" % (quien, valor))
    if "grosor" in w:
        valor = w["grosor"]
        if not _es_numero(valor):
            problemas.append("%s: 'grosor' tiene que ser un numero, no %s"
                             % (quien, _tipo_de(valor)))
        elif valor < 1:
            problemas.append("%s: 'grosor' tiene que ser 1 o mas (es %s)" % (quien, valor))

    # --- datos: fuente, rango ---
    if tipo in ("dato", "barra", "grafica"):
        fuente = w.get("fuente")
        if fuente is None or fuente == "":
            problemas.append("%s: le falta 'fuente'" % quien)
        elif not isinstance(fuente, str):
            problemas.append("%s: 'fuente' tiene que ser texto, no %s"
                             % (quien, _tipo_de(fuente)))
        elif not _fuente_conocida(fuente, conocidas):
            problemas.append("%s: fuente desconocida %r%s"
                             % (quien, fuente, " (error)" if estricto
                                else " (se dibujara '--')"))
        for campo in ("min", "max"):
            if campo in w and not _es_numero(w[campo]):
                problemas.append("%s: '%s' tiene que ser un numero, no %s"
                                 % (quien, campo, _tipo_de(w[campo])))
        minimo, maximo = w.get("min", 0), w.get("max", 100)
        if (_es_numero(minimo) and _es_numero(maximo) and not w.get("auto")
                and maximo <= minimo):
            problemas.append("%s: max (%s) no puede ser menor o igual que min (%s)"
                             % (quien, maximo, minimo))

    # --- colores ---
    for campo, valor in w.items():
        if campo.startswith("color") or campo in ("fondo", "borde"):
            if not _color_valido(valor):
                problemas.append("%s: color invalido en '%s': %r" % (quien, campo, valor))

    # --- formato del reloj ---
    if tipo == "reloj":
        formato = w.get("formato", "%H:%M")
        if not isinstance(formato, str):
            problemas.append("%s: 'formato' tiene que ser texto, no %s"
                             % (quien, _tipo_de(formato)))
        else:
            try:
                time.strftime(formato)
            except (ValueError, TypeError) as exc:
                problemas.append("%s: formato de hora invalido: %s" % (quien, exc))

    # --- campos del editor oficial que se ignoran ---
    for campo in CAMPOS_DEL_EDITOR:
        if campo in w:
            problemas.append("%s: '%s' es del editor oficial y aqui se ignora"
                             % (quien, campo))
    return problemas


def validar(tema, estricto=False):
    """Lista de problemas del tema. Vacia = se puede dibujar y subir tal cual.

    Comprueba lo que de verdad rompe o estropea el render: tipos equivocados
    (`tamano: -1`, `tamano` en texto, `puntos <= 0`, `radio < 0`, `min` no numerico,
    `x`/`y` booleanos, `ancho`/`alto` en texto), widgets que se salen del panel
    (x + ancho > 1920) y colores mal escritos. Con `estricto=True` las fuentes
    desconocidas tambien cuentan como error (por defecto solo avisan: se dibuja "--").

    Garantia: si `validar` devuelve una lista vacia, `renderizar` no aborta.
    """
    problemas = []
    if not isinstance(tema, dict):
        return ["el tema no es un objeto JSON (es %s)" % _tipo_de(tema)]

    ancho, alto = tema.get("ancho", ANCHO), tema.get("alto", ALTO)
    if not _es_entero(ancho) or not _es_entero(alto):
        problemas.append("'ancho'/'alto' del tema tienen que ser enteros (son %s y %s)"
                         % (_tipo_de(ancho), _tipo_de(alto)))
    elif (ancho, alto) != (ANCHO, ALTO):
        problemas.append("el panel es %dx%d, pero el tema dice %sx%s"
                         % (ANCHO, ALTO, ancho, alto))
    if not _color_valido(tema.get("fondo")):
        problemas.append("color de fondo invalido: %r" % (tema.get("fondo"),))

    widgets_del_tema = tema.get("widgets")
    if not isinstance(widgets_del_tema, list):
        return problemas + ["falta la lista 'widgets'"]

    conocidas = _claves_datos()
    for indice, w in enumerate(widgets_del_tema):
        problemas.extend(_validar_widget(indice, w, conocidas, estricto))
    return problemas


# --------------------------------------------------------------------------- normalizar
def normalizar(tema):
    """Rellena lo que falte con los valores por defecto del catalogo (copia nueva).

    No arregla valores invalidos (un `tamano: -1` sigue siendo -1, para que `validar` lo
    siga viendo), pero si garantiza la forma: dict, panel 1920x462 y fondo utilizable.
    Con algo que no sea un dict devuelve un tema valido vacio y lo registra en `AVISOS`.
    """
    if not isinstance(tema, dict):
        _avisar("normalizar: el tema no es un objeto JSON (es %s); se devuelve un tema vacio"
                % _tipo_de(tema))
        return tema_vacio()

    salida = tema_vacio()
    if _es_entero(tema.get("version")):
        salida["version"] = tema["version"]
    if isinstance(tema.get("nombre"), str):
        salida["nombre"] = tema["nombre"]

    fondo = tema.get("fondo")
    if motor.es_color(fondo):
        salida["fondo"] = fondo
    elif fondo not in (None, ""):
        _avisar("normalizar: fondo invalido %r; se usa %r" % (fondo, motor.FONDO))

    lista = tema.get("widgets")
    if not isinstance(lista, list):
        if lista is not None:
            _avisar("normalizar: 'widgets' no es una lista (%s); el tema queda sin widgets"
                    % _tipo_de(lista))
        return salida

    for indice, w in enumerate(lista):
        if not isinstance(w, dict):
            _avisar("normalizar: el widget %d no es un objeto (%s); se descarta"
                    % (indice, _tipo_de(w)))
            continue
        tipo = _tipo_normalizado(w.get("tipo"))
        if tipo not in CATALOGO:
            _avisar("normalizar: el widget %d tiene un tipo desconocido (%r); se conserva"
                    % (indice, w.get("tipo")))
            salida["widgets"].append(dict(w))
            continue
        completo = plantilla(tipo)
        completo.update(w)
        completo["tipo"] = tipo
        salida["widgets"].append(completo)
    return salida


# --------------------------------------------------------------------------- dibujar
def renderizar(tema, ruta, fuentes=None):
    """Dibuja el tema en `ruta` (PNG) y devuelve la ruta.

    Atajo de `widgets.renderizar`. Con `fuentes=None` se crean las de `cfv235.sensores`
    de forma perezosa (o se dibuja con valores vacios si no esta disponible). Acepta
    tambien el dict que devuelve `Sensores.muestra()`.
    """
    return motor.renderizar(tema, ruta, fuentes)


def renderizar_datos(tema, fuentes=None, formato="PNG"):
    """Igual que `renderizar`, pero devuelve los bytes en memoria.

    Util para subir sin escribir en disco. Seguro entre hilos: el temporal es unico y se
    borra siempre.
    """
    return motor.renderizar_datos(tema, fuentes, formato)
