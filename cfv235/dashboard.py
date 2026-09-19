"""Dashboard en vivo: dibuja las metricas del PC como tema y lo sube al panel.

El dashboard es un **tema JSON** normal (los mismos widgets que el editor), asi que se puede
editar sin tocar codigo: `cfv235 dashboard --perfil esencial --sin red`.

DISENO
------
El panel tiene 1920x462 y muy poca altura, asi que el layout va a una **rejilla calculada**
en vez de a coordenadas a mano. Lo que hacia ilegible el dashboard anterior: el widget `dato`
ocupa por defecto 340x300 y dibuja su barra interna en `alto - 102`, de modo que las tarjetas
se comian todo lo que hubiera debajo y los textos se pisaban. Aqui cada tarjeta lleva `alto` y
`barra: false` explicitos, y las barras van aparte, con su propia fila.

    y  18 .. 76    cabecera: titulo, reloj y fecha
    y  92 .. 236   tarjeta de dato (etiqueta + valor grande)
    y 252 .. 270   barra de progreso de la misma metrica
    y 282 .. 306   linea de detalle (texto pequeno)
    y 316 .. 426   zona baja: grafica + red / sistema
    y 430 .. 452   pie

PERFILES
--------
`completo` (todo), `esencial` (sin zona baja), `graficas` (grafica grande + red),
`minimo` (dos tarjetas) y `presentacion` (reloj y titulo grandes, sin metricas).
Cada seccion se puede activar o desactivar por separado.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import temas

# Nombre fijo del fichero en el panel: al reutilizarlo, el panel no acumula copias.
NOMBRE = "cfv235_dashboard.png"

# ------------------------------------------------------------------ rejilla
ANCHO, ALTO = 1920, 462
MARGEN = 24
HUECO = 20                      # separacion entre columnas

CABECERA_Y = 18
TARJETA_Y = 92
TARJETA_ALTO = 144
BARRA_Y = 252
BARRA_ALTO = 18
DETALLE_Y = 282
ZONA_BAJA_Y = 316
ZONA_BAJA_ALTO = 96
PIE_Y = 424

TITULO_TAM = 34
RELOJ_TAM = 46
FECHA_TAM = 18
VALOR_TAM = 52
DETALLE_TAM = 21
PIE_TAM = 17

COLOR_FONDO = "#0b0e14"
COLOR_TARJETA = "#141922"
COLOR_BORDE = "#1f2836"
COLOR_TITULO = "#00b4ff"
COLOR_TEXTO = "#e8edf5"
COLOR_GRIS = "#8a94a6"
COLOR_TENUE = "#6b7484"

# colores por metrica: se reutilizan en la tarjeta, la barra y el detalle
COLOR_CPU = "#ff9f43"
COLOR_GPU = "#4cd137"
COLOR_RAM = "#00b4ff"
COLOR_DISCO = "#e1b12c"
COLOR_RED = "#00d2d3"
COLOR_TEMP = "#ff6b6b"
COLOR_CLIMA = "#7dd3fc"


# ------------------------------------------------------------------ secciones
@dataclass
class Seccion:
    """Un bloque del dashboard, para poder activarlo o desactivarlo."""
    clave: str
    etiqueta: str
    descripcion: str
    por_defecto: bool = True


SECCIONES: tuple[Seccion, ...] = (
    Seccion("titulo", "Titulo", "Nombre del equipo o titulo del dashboard"),
    Seccion("reloj", "Reloj", "Hora y fecha arriba a la derecha"),
    Seccion("cpu", "CPU", "Temperatura, uso, frecuencia y ventilador"),
    Seccion("gpu", "GPU", "Temperatura, uso, frecuencia y memoria"),
    Seccion("ram", "Memoria", "Uso y ocupacion de RAM"),
    Seccion("disco", "Disco", "Uso, ocupacion y actividad"),
    Seccion("red", "Red", "Velocidad de bajada y subida, y trafico total"),
    Seccion("grafica", "Grafica", "Historial de uso de CPU"),
    Seccion("sistema", "Sistema", "Arranque, procesos y carga media"),
    Seccion("ventiladores", "Ventiladores", "RPM de CPU, bomba y caja (si el equipo los expone)"),
    Seccion("temperaturas", "Temperaturas", "Temperatura de placa y VRM (si el equipo las expone)"),
    Seccion("clima", "Clima", "Tiempo actual (requiere activar el clima en config)", por_defecto=False),
)

PERFILES: dict[str, dict] = {
    "completo": {},
    "esencial": {"grafica": False, "red": False, "sistema": False, "ventiladores": False,
                 "temperaturas": False},
    "graficas": {"ventiladores": False, "temperaturas": False, "disco": False},
    "minimo": {"gpu": False, "ram": False, "disco": False, "red": False, "grafica": False,
               "sistema": False, "ventiladores": False, "temperaturas": False},
    "presentacion": {"cpu": False, "gpu": False, "ram": False, "disco": False, "red": False,
                     "grafica": False, "sistema": False, "ventiladores": False,
                     "temperaturas": False},
}


def secciones_activas(perfil: str = "completo", ajustes: dict | None = None) -> dict:
    """Estado final de cada seccion: perfil + ajustes sueltos."""
    if perfil not in PERFILES:
        raise ValueError(f"perfil desconocido: {perfil!r} (hay {', '.join(PERFILES)})")
    activas = {s.clave: s.por_defecto for s in SECCIONES}
    activas.update({k: v for k, v in PERFILES[perfil].items()})
    for clave, valor in (ajustes or {}).items():
        if clave not in activas:
            raise ValueError(f"seccion desconocida: {clave!r} "
                             f"(hay {', '.join(activas)})")
        activas[clave] = bool(valor)
    return activas


def catalogo_secciones() -> list[dict]:
    """Para que la interfaz pueda pintar las casillas."""
    return [{"clave": s.clave, "etiqueta": s.etiqueta, "descripcion": s.descripcion,
             "por_defecto": s.por_defecto} for s in SECCIONES]


# ------------------------------------------------------------------ ayudas de rejilla
def columnas(cuantas: int) -> list[tuple[int, int]]:
    """[(x, ancho)] de `cuantas` columnas iguales, con margen y hueco."""
    if cuantas < 1:
        raise ValueError(f"hacen falta al menos 1 columna, no {cuantas}")
    util = ANCHO - 2 * MARGEN - HUECO * (cuantas - 1)
    ancho = util // cuantas
    if ancho < 40:
        raise ValueError(f"no caben {cuantas} columnas en {ANCHO} px")
    return [(MARGEN + i * (ancho + HUECO), ancho) for i in range(cuantas)]


def tarjeta(x: int, ancho: int, etiqueta: str, fuente: str, sufijo: str, color: str,
            partes=None, valores: dict | None = None, detalle: str | None = None,
            barra: bool = True) -> list[dict]:
    """Tarjeta de dato + su barra + su linea de detalle, ya alineadas.

    `partes` es [(clave, plantilla), ...]: solo se incluyen las que tienen dato en este
    equipo, para no acabar con un detalle lleno de "--". `detalle` fija la linea a mano
    (para texto que no son sensores, como la descripcion del clima) y `barra=False` deja
    la tarjeta sin barra de progreso, porque su valor no es un porcentaje.
    """
    # El ancho de la barra deja sitio al valor que `barra` dibuja a la derecha.
    ancho_barra = ancho - 86
    detalle = detalle if detalle is not None else _con_datos(valores, partes or [])
    widgets = [
        {"tipo": "dato", "x": x, "y": TARJETA_Y, "ancho": ancho, "alto": TARJETA_ALTO,
         "etiqueta": etiqueta, "fuente": fuente, "sufijo": sufijo, "tamano": VALOR_TAM,
         "barra": barra, "fondo": COLOR_TARJETA, "borde": COLOR_BORDE,
         "color": color, "color_etiqueta": COLOR_GRIS},
    ]
    if barra:
        widgets.append(
            {"tipo": "barra", "x": x, "y": BARRA_Y, "ancho": ancho_barra, "alto": BARRA_ALTO,
             "etiqueta": "", "fuente": fuente, "color_relleno": color,
             "color": COLOR_TEXTO, "fondo": "#1b2230"})
    widgets.append({"tipo": "texto", "x": x, "y": DETALLE_Y, "tamano": DETALLE_TAM,
                    "texto": detalle, "color": COLOR_GRIS})
    return widgets


def tarjetas_clima(activas: dict, valores: dict | None = None,
                   libres: list[tuple[int, int]] | None = None) -> list[dict]:
    """Tarjeta del clima exterior, o nada.

    Doble condicion, y las dos hacen falta: la seccion tiene que estar activa (**opt-in**:
    el clima se descarga de la red y no todo el mundo lo quiere) y tiene que haber dato
    (`clima_temp`), porque `fuentes_ext.Clima` devuelve `{}` o claves a `None` cuando esta
    sin red. Es el mismo criterio que usa `_zona_baja` con los ventiladores.

    `libres` son las columnas que deja libres la fila de tarjetas: el clima ocupa la
    primera, para no pisar ninguna y mantener el alineamiento de filas del README.
    """
    if not (activas.get("clima") and _hay(valores, "clima_temp")):
        return []
    if libres is None:
        libres = columnas_libres(activas, valores)
    if not libres:
        return []
    x, ancho = libres[0]
    # El detalle lleva marcadores (no el valor de la muestra) para que se refresque en cada
    # fotograma; con el clima apagado a mitad de bucle el motor dibuja "--", y ese caso no
    # llega aqui porque `_hay` ya lo corta antes.
    return tarjeta(x, ancho, "Exterior", "clima_temp", "°C", COLOR_CLIMA,
                   detalle="{clima_descripcion} · {clima_humedad}% hum", barra=False)


def _cabecera(titulo: str, activas: dict) -> list[dict]:
    widgets: list[dict] = []
    if activas.get("titulo"):
        widgets.append({"tipo": "texto", "texto": titulo, "x": MARGEN, "y": CABECERA_Y,
                        "tamano": TITULO_TAM, "color": COLOR_TITULO})
        widgets.append({"tipo": "texto", "texto": "panel COUGAR CFV235", "x": MARGEN + 2,
                        "y": CABECERA_Y + 44, "tamano": FECHA_TAM, "color": COLOR_TENUE})
    if activas.get("reloj"):
        widgets.append({"tipo": "reloj", "formato": "%H:%M", "x": 1608, "y": CABECERA_Y,
                        "tamano": RELOJ_TAM, "color": COLOR_TEXTO})
        widgets.append({"tipo": "reloj", "formato": "%d/%m/%Y", "x": 1620,
                        "y": CABECERA_Y + 52, "tamano": FECHA_TAM, "color": COLOR_GRIS})
    return widgets


def _hay(valores, *claves) -> bool:
    """True si alguna de esas claves trae un numero (para no pintar lineas con '--')."""
    if not valores:
        return True
    for clave in claves:
        valor = valores.get(clave)
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            return True
    return False


# Clave que delata que una tarjeta tiene dato, por columna de la rejilla. Se usa para que
# la tarjeta del clima (que va al final y es opt-in) no pise la de un bloque que, por
# tener su seccion activa, ya ocupa ese sitio.
_CLAVES_TARJETA = (
    ("cpu_temp", "cpu_uso"),
    ("gpu_temp", "gpu_uso"),
    ("ram_uso",),
    ("disco_uso",),
)

# Seccion que dibuja cada columna, en el mismo orden que `_CLAVES_TARJETA`.
_CLAVES_TARJETA_DE = ("cpu", "gpu", "ram", "disco")


def columnas_libres(activas: dict, valores: dict | None = None) -> list[tuple[int, int]]:
    """Columnas de la fila de tarjetas que no ocupa ninguna seccion activa con datos."""
    if not valores:
        return []
    libres = []
    for indice, (x, ancho) in enumerate(columnas(4)):
        seccion = _CLAVES_TARJETA_DE[indice]
        if activas.get(seccion) and _hay(valores, *_CLAVES_TARJETA[indice]):
            continue                # la ocupa su seccion; si no hay dato, el hueco queda libre
        libres.append((x, ancho))
    return libres


def _con_datos(valores, partes, separador: str = "     ") -> str:
    """Une plantillas SOLO de los datos que existen en este equipo.

    Las plantillas conservan los `{marcadores}` para que el valor se actualice en cada
    fotograma; lo que se decide aqui es que campos aparecen. Asi un equipo sin sensores de
    ventilador no muestra una linea de "-- rpm" que ocupa sitio sin informar.
    """
    return separador.join(plantilla for clave, plantilla in partes
                          if valores is None or _hay(valores, clave))


def _zona_baja(activas: dict, valores: dict | None = None) -> list[dict]:
    """Grafica a la izquierda y red/sistema a la derecha (o una de las dos a lo ancho)."""
    widgets: list[dict] = []
    hay_grafica = bool(activas.get("grafica"))
    # Las secciones que dependen de sensores que este equipo no expone no se pintan: una
    # linea de "-- rpm" ocupa sitio y no informa de nada.
    red = bool(activas.get("red")) and _hay(valores, "red_bajada_mb", "red_subida_mb", "red_total_gb")
    ventiladores = bool(activas.get("ventiladores")) and _hay(valores, "cpu_vent", "bomba_vent",
                                                              "gpu_vent")
    temperatura = bool(activas.get("temperaturas")) and _hay(valores, "chipset_temp")
    # El bloque de sistema va al pie cuando hay grafica (abajo a la izquierda); si no hay
    # grafica, ocupa el hueco de la derecha. Asi no se repite en los dos sitios.
    sistema = bool(activas.get("sistema")) and not hay_grafica
    hay_derecha = bool(red or sistema or ventiladores or temperatura)

    if hay_grafica and hay_derecha:
        ancho_grafica = 1080
        x_derecha = MARGEN + ancho_grafica + HUECO * 2
        ancho_derecha = ANCHO - MARGEN - x_derecha
    elif hay_grafica:
        ancho_grafica = ANCHO - 2 * MARGEN
        x_derecha = ancho_derecha = 0
    else:
        ancho_grafica = 0
        x_derecha = MARGEN
        ancho_derecha = ANCHO - 2 * MARGEN

    if hay_grafica:
        widgets.append({"tipo": "grafica", "x": MARGEN, "y": ZONA_BAJA_Y,
                        "ancho": ancho_grafica, "alto": ZONA_BAJA_ALTO,
                        "etiqueta": "USO DE CPU (%)", "fuente": "cpu_uso", "puntos": 120,
                        "auto": True, "color": COLOR_RED, "grosor": 3,
                        "fondo": COLOR_TARJETA, "borde": COLOR_BORDE,
                        "color_etiqueta": COLOR_GRIS})

    if hay_derecha:
        lineas = []
        if red:
            lineas.append("RED    bajada {red_bajada_mb:.2f} MB/s    "
                          "subida {red_subida_mb:.2f} MB/s    total {red_total_gb:.1f} GB")
        if ventiladores:
            partes = []
            if _hay(valores, "cpu_vent"):
                partes.append("cpu {cpu_vent:.0f} rpm")
            if _hay(valores, "bomba_vent"):
                partes.append("bomba {bomba_vent:.0f} rpm")
            if _hay(valores, "gpu_vent"):
                partes.append("gpu {gpu_vent:.0f} rpm")
            if partes:
                lineas.append("VENT   " + "    ".join(partes))
        if temperatura:
            lineas.append("PLACA  {chipset_temp:.0f} C")
        if sistema:
            lineas.append("SIST   arranque {uptime_h:.1f} h    {procesos} procesos    "
                          "carga {carga_1m:.2f}")
        if lineas:
            # Interlineado normal (1.3 x tamano): con lineas en blanco de mas, la ultima se
            # salia por el borde inferior del panel.
            widgets.append({"tipo": "texto", "x": x_derecha, "y": ZONA_BAJA_Y + 18,
                            "tamano": 22, "color": COLOR_TEXTO,
                            "texto": "\n".join(lineas),
                            "ancho": ancho_derecha if ancho_derecha else None})
    return widgets


# ------------------------------------------------------------------ el tema
def tema_dashboard(titulo: str = "CFV 235", perfil: str = "completo",
                   ajustes: dict | None = None, valores: dict | None = None) -> dict:
    """Tema del dashboard ya colocado en la rejilla. Listo para `temas.renderizar`.

    `valores` es una muestra de los sensores: sirve para **no pintar** los bloques cuyos
    datos no existen en este equipo (ventiladores, temperatura de placa...), que si no
    aparecen como una linea de "-- rpm" que ocupa sitio y no informa.
    """
    activas = secciones_activas(perfil, ajustes)
    widgets = _cabecera(titulo, activas)
    # Columnas de la fila de tarjetas que ya ocupan los bloques de siempre. La del clima,
    # que es opt-in y va al final, coge la primera que quede libre: asi nunca pisa ninguna
    # tarjeta y, si el equipo no da dato de ese bloque, se mete en su hueco.
    libres = columnas_libres(activas, valores)

    if activas.get("cpu") and _hay(valores, *_CLAVES_TARJETA[0]):
        x, ancho = columnas(4)[0]
        widgets += tarjeta(x, ancho, "CPU", "cpu_temp", " C", COLOR_CPU, [
            ("cpu_uso", "carga {cpu_uso:.0f} %"),
            ("cpu_mhz", "{cpu_mhz:.0f} MHz"),
            ("cpu_vent", "{cpu_vent:.0f} rpm"),
        ], valores)
    if activas.get("gpu") and _hay(valores, *_CLAVES_TARJETA[1]):
        x, ancho = columnas(4)[1]
        widgets += tarjeta(x, ancho, "GPU", "gpu_temp", " C", COLOR_GPU, [
            ("gpu_uso", "uso {gpu_uso:.0f} %"),
            ("gpu_mhz", "{gpu_mhz:.0f} MHz"),
            ("gpu_vram_usado_gb", "vram {gpu_vram_usado_gb:.1f} GB"),
        ], valores)
    if activas.get("ram") and _hay(valores, *_CLAVES_TARJETA[2]):
        x, ancho = columnas(4)[2]
        widgets += tarjeta(x, ancho, "MEMORIA", "ram_uso", " %", COLOR_RAM, [
            ("ram_usado_gb", "{ram_usado_gb:.1f} / {ram_total_gb:.1f} GB"),
            ("ram_velocidad", "{ram_velocidad:.0f} MHz"),
        ], valores)
    if activas.get("disco") and _hay(valores, *_CLAVES_TARJETA[3]):
        x, ancho = columnas(4)[3]
        widgets += tarjeta(x, ancho, "DISCO", "disco_uso", " %", COLOR_DISCO, [
            ("disco_usado_gb", "{disco_usado_gb:.0f} / {disco_total_gb:.0f} GB"),
            ("disco_lectura_mb", "lectura {disco_lectura_mb:.0f}  escritura "
                                 "{disco_escritura_mb:.0f} MB/s"),
        ], valores)

    widgets += tarjetas_clima(activas, valores, libres)

    widgets += _zona_baja(activas, valores)

    # Pie: solo cuando hay grafica, porque entonces el bloque de sistema no tiene sitio
    # arriba a la derecha y baja aqui.
    if activas.get("sistema") and activas.get("grafica"):
        widgets.append({"tipo": "texto", "x": MARGEN, "y": PIE_Y, "tamano": PIE_TAM,
                        "color": COLOR_TENUE,
                        "texto": "arranque {uptime_h:.1f} h     {procesos} procesos     "
                                 "carga {carga_1m:.2f} {carga_5m:.2f} {carga_15m:.2f}"})

    # los textos vacios (una seccion que no aplica) no se dibujan
    limpios = [w for w in widgets
               if w["tipo"] != "texto" or (w.get("texto") or "").strip()]
    return {"nombre": f"dashboard cfv235 ({perfil})", "fondo": COLOR_FONDO,
            "widgets": limpios}


def tema_esencial(titulo: str = "CFV 235") -> dict:
    return tema_dashboard(titulo, perfil="esencial")


def tema_minimo(titulo: str = "CFV 235") -> dict:
    return tema_dashboard(titulo, perfil="minimo")


def tema_desde_fichero(ruta: str) -> dict:
    return temas.cargar(ruta)


# ------------------------------------------------------------------ el bucle
class Dashboard:
    """Dibuja el dashboard y lo sube al panel."""

    def __init__(self, panel, sensores=None, tema: dict | None = None,
                 png: str | None = None, capa: str = "osd", verboso: bool = False,
                 perfil: str = "completo", ajustes: dict | None = None):
        import os
        from .sensores import Sensores
        self.panel = panel
        # intervalo=1.0: red y disco se miden por diferencia entre dos muestras, y con
        # ventanas de milisegundos el dato sale ruidoso.
        self.sensores = sensores or Sensores(intervalo=1.0)
        self.perfil = perfil
        self.ajustes = dict(ajustes or {})
        # El historial de la grafica y los deltas de red/disco necesitan varias muestras.
        self.sensores.muestra()
        time.sleep(0.1)
        primera = self.sensores.muestra()
        self.tema = tema or tema_dashboard(perfil=perfil, ajustes=self.ajustes,
                                           valores=primera)
        self.png = png or os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                       "cfv235-dashboard.png")
        self.capa = capa
        self.verboso = verboso
        self.fotogramas = 0
        self.ultimo_error = ""
        self.espacio_kb: int | None = None
        self.ultimos_bytes: bytes | None = None
        # Guardar el PNG en disco en cada fotograma no hace falta para subirlo; se activa
        # solo si alguien quiere previsualizarlo.
        self.escribir_png = bool(png)
        # el historial de la grafica necesita varias muestras
        self.sensores.muestra()

    def fotograma(self) -> bool:
        """Dibuja un fotograma y lo sube. Devuelve True si el panel lo acepto.

        El PNG se compone **en memoria** (`temas.renderizar_datos`) y se sube tal cual: antes
        se escribia y se volvia a leer un fichero de ~54 KB en /tmp en cada fotograma, o sea
        I/O de disco 1-2 veces por segundo sin ninguna necesidad. Si se pidio `png`, ademas se
        guarda en disco (util para previsualizar).
        """
        valores = self.sensores.muestra()
        try:
            datos = temas.renderizar_datos(self.tema, valores)
        except Exception as exc:                      # noqa: BLE001
            self.ultimo_error = f"al dibujar: {exc}"
            return False
        self.ultimos_bytes = datos
        if self.png and self.escribir_png:
            try:
                with open(self.png, "wb") as fh:
                    fh.write(datos)
            except OSError:
                pass                                  # el PNG en disco es opcional
        try:
            resultado = self.panel.subir_datos(datos, NOMBRE, capa=self.capa)
        except Exception as exc:                      # noqa: BLE001
            self.ultimo_error = f"al subir: {exc}"
            return False
        if not resultado.ok:
            self.ultimo_error = resultado.motivo
            return False
        self.fotogramas += 1
        self.ultimo_error = ""
        return True

    def bucle(self, periodo: float = 2.0, repeticiones: int = 0, parar=None,
              avisar=None, espacio_minimo_kb: int = 20000) -> int:
        """Bucle de fotogramas. `parar` es un threading.Event para cortarlo desde fuera.

        Cada 10 fotogramas se comprueba el espacio libre del panel y el bucle se detiene
        antes de llenarlo: un panel sin memoria se queda atascado con `bootFinish: 0` y solo
        se recupera cortandole la alimentacion. La capa OSD reutiliza su hueco (medido:
        ~4 KB por fotograma de 54 KB), pero mas vale no depender de ello.

        Tambien manda telemetria mientras espera al siguiente fotograma: medido, con
        `displayInSleep: 0` el panel se apaga igual en ~2 minutos sin trafico.
        """
        n = 0
        while not (parar is not None and parar.is_set()):
            if repeticiones and n >= repeticiones:
                break
            n += 1
            inicio = time.time()
            ok = self.fotograma()
            if avisar:
                avisar(n, ok, self.ultimo_error, (time.time() - inicio) * 1000)
            if n % 10 == 0:
                libre = self.panel.espacio_libre_kb()
                self.espacio_kb = libre
                if libre is not None and libre < espacio_minimo_kb:
                    self.ultimo_error = (f"queda muy poco espacio en el panel ({libre} KB): "
                                         f"bucle detenido")
                    if avisar:
                        avisar(n, False, self.ultimo_error, 0)
                    break
            fin = inicio + periodo
            while time.time() < fin and not (parar is not None and parar.is_set()):
                from .panel import telemetria_desde_sensores
                try:
                    self.panel.telemetria(telemetria_desde_sensores(self.sensores.muestra()))
                except Exception:                     # noqa: BLE001
                    pass
                time.sleep(max(0.05, min(1.0, fin - time.time())))
        return n
