"""Temas: el JSON que describe lo que se dibuja en los 1920x462 del panel.

Un tema es un diccionario sencillo, pensado para que un editor grafico lo lea y lo
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

Aqui estan el esquema, los valores por defecto de cada tipo (`CATALOGO`), la lista de
fuentes de datos que acepta el motor (`fuentes_disponibles`) y el validador
(`validar`), que es lo que un editor deberia llamar antes de aplicar nada al panel.
La referencia completa, campo a campo, esta en `ESQUEMA_TEMA.md`.
"""

import json
import os
import time

from . import fuentes as modulo_fuentes
from . import widgets as motor

ANCHO, ALTO = motor.ANCHO, motor.ALTO
VERSION = 1

# --------------------------------------------------------------------------- catalogo
# Campos de cada tipo de widget, con su valor por defecto y para que sirve.
# Los tres ultimos del editor oficial (showPrefix, addZero, rotate, maxTickCount,
# cornerRadius) NO estan implementados: se listan en `CAMPOS_DEL_EDITOR` para que quien
# migre un tema sepa que se ignoran.
CATALOGO = {
    "dato": {
        "descripcion": "Tarjeta con etiqueta, valor grande, detalle y barra de progreso.",
        "campos": {
            "x": (int, 0, "esquina izquierda"), "y": (int, 0, "borde superior"),
            "ancho": (int, 340, "ancho de la tarjeta"), "alto": (int, 300, "alto de la tarjeta"),
            "etiqueta": (str, "", "titulo pequeno de arriba"),
            "fuente": (str, "", "fuente de datos (nombre del editor o clave interna)"),
            "unidad": (str, "", "se dibuja pequena junto al valor"),
            "detalle": (str, "", "linea de abajo; admite {marcadores}"),
            "tamano": (int, 74, "cuerpo del valor (se encoge solo si no cabe)"),
            "tamano_detalle": (int, 30, "cuerpo de la linea de detalle"),
            "barra": (bool, True, "dibujar la barra de progreso"),
            "mostrar_porcentaje": (bool, False, "escribir el % sobre la barra"),
            "radio": (int, 18, "redondeo de las esquinas"),
            "min": (int, 0, "minimo de la barra"), "max": (int, 100, "maximo de la barra"),
            "color": (str, "#ebf0f8", "color del valor"),
            "color_etiqueta": (str, "#788291", "color de etiqueta y unidad"),
            "color_detalle": (str, "#9aa6b8", "color de la linea de detalle"),
            "color_relleno": (str, "#00d0ff", "color de la barra"),
            "fondo": (str, "#161b24", "fondo de la tarjeta"),
            "borde": (str, "#2c3442", "borde de la tarjeta"),
        },
    },
    "barra": {
        "descripcion": "Barra horizontal con etiqueta y valor.",
        "campos": {
            "x": (int, 0, "esquina izquierda"), "y": (int, 0, "borde superior"),
            "ancho": (int, 500, "largo de la barra"), "alto": (int, 34, "grosor"),
            "etiqueta": (str, "", "rotulo encima"), "fuente": (str, "", "fuente de datos"),
            "unidad": (str, "%", "unidad"), "min": (int, 0, "minimo"), "max": (int, 100, "maximo"),
            "radio": (int, 12, "redondeo"),
            "color": (str, "#ebf0f8", "color del valor"),
            "color_etiqueta": (str, "#788291", "color del rotulo"),
            "color_relleno": (str, "#00d0ff", "color de la barra"),
        },
    },
    "grafica": {
        "descripcion": "Grafica de linea con historial (necesita dos muestras para dibujar).",
        "campos": {
            "x": (int, 0, "esquina izquierda"), "y": (int, 0, "borde superior"),
            "ancho": (int, 560, "ancho"), "alto": (int, 120, "alto"),
            "etiqueta": (str, "", "rotulo"), "fuente": (str, "", "fuente de datos"),
            "puntos": (int, 120, "cuantos valores del historial se dibujan"),
            "auto": (bool, False, "escala automatica con los datos (si no, usa min/max)"),
            "min": (int, 0, "minimo fijo"), "max": (int, 100, "maximo fijo"),
            "grosor": (int, 3, "grosor de la linea"),
            "radio": (int, 14, "redondeo del marco"),
            "unidad": (str, "", "unidad"),
            "color": (str, "#ebf0f8", "color del valor actual"),
            "color_etiqueta": (str, "#788291", "color del rotulo"),
            "color_relleno": (str, "#00d0ff", "color de la linea"),
            "fondo": (str, "#161b24", "fondo"), "borde": (str, "#2c3442", "borde"),
        },
    },
    "texto": {
        "descripcion": "Linea de texto libre con {marcadores} de datos.",
        "campos": {
            "x": (int, 0, "esquina izquierda"), "y": (int, 0, "borde superior"),
            "texto": (str, "", "texto; admite {clave} y {fuente:Nombre del editor}"),
            "tamano": (int, 28, "cuerpo (se encoge solo si no cabe)"),
            "ancho": (int, 0, "limite de ancho; 0 = hasta el borde del panel"),
            "color": (str, "#ebf0f8", "color"),
        },
    },
    "reloj": {
        "descripcion": "Reloj con formato de strftime.",
        "campos": {
            "x": (int, 0, "esquina izquierda"), "y": (int, 0, "borde superior"),
            "formato": (str, "%H:%M", "formato strftime, p. ej. %H:%M o %H:%M:%S"),
            "tamano": (int, 120, "cuerpo"), "color": (str, "#ebf0f8", "color"),
        },
    },
}
ALIAS_TIPOS = {"clock": "reloj"}
CAMPOS_DEL_EDITOR = ["showPrefix", "addZero", "rotate", "maxTickCount", "cornerRadius"]


def campos(tipo):
    """Campos de un tipo de widget (dict nombre -> (tipo, por_defecto, ayuda))."""
    return CATALOGO[ALIAS_TIPOS.get(str(tipo).lower(), str(tipo).lower())]["campos"]


def catalogo_json():
    """El catalogo en algo que se puede mandar por JSON (los tipos van como texto).

    Es lo que devuelve `GET /catalogo` del editor: una interfaz grafica puede construir su
    panel de propiedades a partir de esto sin duplicar la lista de campos.
    """
    salida = {}
    for tipo, definicion in CATALOGO.items():
        campos_json = {}
        for nombre, (clase, por_defecto, ayuda) in definicion["campos"].items():
            campos_json[nombre] = {"tipo": getattr(clase, "__name__", str(clase)),
                                   "por_defecto": por_defecto, "ayuda": ayuda}
        salida[tipo] = {"descripcion": definicion["descripcion"], "campos": campos_json}
    return salida


def plantilla(tipo, **valores):
    """Crea un widget de `tipo` con los valores por defecto y los que le pases."""
    normalizado = ALIAS_TIPOS.get(str(tipo).lower(), str(tipo).lower())
    if normalizado not in CATALOGO:
        raise ValueError(f"tipo de widget desconocido: {tipo!r}")
    widget = {"tipo": normalizado}
    for nombre, (_, por_defecto, _ayuda) in CATALOGO[normalizado]["campos"].items():
        widget[nombre] = por_defecto
    for nombre, valor in valores.items():
        if nombre not in widget and nombre != "tipo":
            raise ValueError(f"el widget {normalizado!r} no tiene el campo {nombre!r}")
        widget[nombre] = valor
    return widget


def tema_nuevo(**widgets_):
    """Tema vacio (o con los widgets que le pases) listo para llenar."""
    return {"version": VERSION, "ancho": ANCHO, "alto": ALTO, "fondo": motor.FONDO,
            "widgets": list(widgets_.get("widgets", [])) if widgets_ else []}


def tema_por_defecto():
    """El tema de ejemplo (el mismo que `ejemplos/tema_dashboard.json`)."""
    return motor.tema_por_defecto()


# --------------------------------------------------------------------------- fuentes
def fuentes_disponibles(instanciar=True):
    """Nombres de datos que se pueden pedir en `fuente`.

    Devuelve `{"editor": [...], "propias": [...], "valores": {...}}`. Los nombres de
    "editor" son los 35 del editor oficial; las "propias" son las claves internas
    (`cpu_temp`, `ram_uso`...), que se pueden usar igual.
    """
    propias = sorted(modulo_fuentes.MAPA_EDITOR.values())
    datos = {"editor": sorted(modulo_fuentes.MAPA_EDITOR.keys()),
             "propias": propias, "valores": {}}
    if instanciar:
        try:
            f = modulo_fuentes.Fuentes()
            datos["valores"] = f.muestra().get("valores", {})
        except Exception:                                     # noqa: BLE001 (sin /proc, etc.)
            pass
    return datos


def marcadores():
    """Marcadores que se pueden escribir entre llaves en `texto` y `detalle`."""
    return {
        "clave": sorted(set(modulo_fuentes.MAPA_EDITOR.values())),
        "forma": ["{cpu_temp}", "{ram_uso}", "{hora}", "{fuente:CPU Usage}"],
    }


# --------------------------------------------------------------------------- ficheros
def cargar(ruta):
    """Lee un tema de un JSON. Si el fichero no existe, devuelve el tema de ejemplo."""
    if not ruta or not os.path.exists(ruta):
        return tema_por_defecto()
    with open(ruta, encoding="utf-8") as fh:
        return json.load(fh)


def guardar(ruta, tema):
    """Escribe el tema con sangria legible (un editor debe escribirlo asi)."""
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(tema, fh, indent=2, ensure_ascii=False)
    return ruta


# --------------------------------------------------------------------------- validar
def validar(tema, estricto=False):
    """Lista de problemas del tema. Vacia = se puede dibujar y subir tal cual.

    Comprueba lo que de verdad rompe el render (tipos, coordenadas fuera del panel,
    valores imposibles) y avisa de lo que solo lo estropea (fuente desconocida, color
    mal escrito). Con `estricto=True` las fuentes desconocidas tambien son error.
    """
    problemas = []
    if not isinstance(tema, dict):
        return ["el tema no es un objeto JSON"]

    ancho, alto = tema.get("ancho", ANCHO), tema.get("alto", ALTO)
    if (ancho, alto) != (ANCHO, ALTO):
        problemas.append(f"el panel es {ANCHO}x{ALTO}, pero el tema dice {ancho}x{alto}")
    if not isinstance(tema.get("widgets"), list):
        return problemas + ["falta la lista 'widgets'"]
    if tema.get("fondo") and not _color_valido(tema["fondo"]):
        problemas.append(f"color de fondo invalido: {tema['fondo']!r}")

    conocidas = set(modulo_fuentes.MAPA_EDITOR) | set(modulo_fuentes.MAPA_EDITOR.values())
    for i, w in enumerate(tema["widgets"]):
        quien = f"widget {i} ({w.get('tipo', '?') if isinstance(w, dict) else '?'})"
        if not isinstance(w, dict):
            problemas.append(f"{quien}: no es un objeto")
            continue
        tipo = ALIAS_TIPOS.get(str(w.get("tipo", "")).lower(), str(w.get("tipo", "")).lower())
        if tipo not in CATALOGO:
            problemas.append(f"{quien}: tipo desconocido {w.get('tipo')!r} "
                             f"(validos: {', '.join(sorted(CATALOGO))})")
            continue
        x, y = w.get("x", 0), w.get("y", 0)
        if not (isinstance(x, int) and isinstance(y, int)):
            problemas.append(f"{quien}: 'x' e 'y' tienen que ser enteros")
        elif not (0 <= x < ANCHO and 0 <= y < ALTO):
            problemas.append(f"{quien}: x={x}, y={y} queda fuera del panel")
        for campo in ("ancho", "alto", "tamano", "radio", "puntos", "grosor"):
            if campo in w and not isinstance(w[campo], int):
                problemas.append(f"{quien}: '{campo}' tiene que ser entero")
        if tipo in ("dato", "barra", "grafica"):
            if not w.get("fuente"):
                problemas.append(f"{quien}: le falta 'fuente'")
            elif w["fuente"] not in conocidas:
                problemas.append(f"{quien}: fuente desconocida {w['fuente']!r}"
                                 + (" (error)" if estricto else " (se dibujara '--')"))
            minimo, maximo = w.get("min", 0), w.get("max", 100)
            if isinstance(minimo, (int, float)) and isinstance(maximo, (int, float)) \
                    and maximo <= minimo:
                problemas.append(f"{quien}: max ({maximo}) no puede ser menor o igual que min ({minimo})")
        for campo, valor in w.items():
            if campo.startswith("color") or campo in ("fondo", "borde"):
                if not _color_valido(valor):
                    problemas.append(f"{quien}: color invalido en '{campo}': {valor!r}")
        if tipo == "reloj":
            try:
                time.strftime(str(w.get("formato", "%H:%M")))
            except ValueError as exc:
                problemas.append(f"{quien}: formato de hora invalido: {exc}")
        for campo in CAMPOS_DEL_EDITOR:
            if campo in w:
                problemas.append(f"{quien}: '{campo}' es del editor oficial y aqui se ignora")
    return problemas


def _color_valido(valor):
    if valor in (None, ""):
        return True
    texto = str(valor).lstrip("#")
    if len(texto) != 6:
        return False
    try:
        int(texto, 16)
        return True
    except ValueError:
        return False


def normalizar(tema):
    """Rellena lo que falte con los valores por defecto del catalogo (copia nueva)."""
    salida = {"version": tema.get("version", VERSION),
              "ancho": tema.get("ancho", ANCHO), "alto": tema.get("alto", ALTO),
              "fondo": tema.get("fondo", motor.FONDO), "widgets": []}
    for w in tema.get("widgets", []):
        if not isinstance(w, dict):
            continue
        tipo = ALIAS_TIPOS.get(str(w.get("tipo", "")).lower(), str(w.get("tipo", "")).lower())
        if tipo not in CATALOGO:
            continue
        completo = plantilla(tipo)
        completo.update(w)
        completo["tipo"] = tipo
        salida["widgets"].append(completo)
    return salida


# --------------------------------------------------------------------------- dibujar
def renderizar(tema, salida, fuentes=None):
    """Dibuja el tema en `salida` (PNG). Atajo de `widgets.renderizar` que crea fuentes."""
    if fuentes is None:
        fuentes = modulo_fuentes.Fuentes()
        fuentes.muestra()
    return motor.renderizar(tema, salida, fuentes)


def renderizar_datos(tema, fuentes=None, formato="PNG"):
    """Igual que `renderizar`, pero devuelve los bytes en memoria (util para subir
    sin escribir en disco: `panel.subir_datos(temas.renderizar_datos(tema), "t.png")`)."""
    import io

    if fuentes is None:
        fuentes = modulo_fuentes.Fuentes()
        fuentes.muestra()
    temporal = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-render.png")
    motor.renderizar(tema, temporal, fuentes)
    from PIL import Image
    with Image.open(temporal) as imagen:
        buffer = io.BytesIO()
        imagen.save(buffer, formato)
    os.unlink(temporal)
    return buffer.getvalue()
