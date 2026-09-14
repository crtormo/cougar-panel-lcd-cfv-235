"""Pruebas de temas: esquema, validador y dibujo (necesita Pillow, no necesita panel)."""

import glob
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

from cougar import temas                                                  # noqa: E402
from cougar.fuentes import Fuentes                                        # noqa: E402

FALLOS = []
SALIDA = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-pruebas")


def comprobar(condicion, mensaje):
    if condicion:
        print(f"   OK   {mensaje}")
    else:
        print(f"   FALLA {mensaje}")
        FALLOS.append(mensaje)


os.makedirs(SALIDA, exist_ok=True)

# --------------------------------------------------------------------------- 1
print("1) El tema de ejemplo es valido")
tema = temas.tema_por_defecto()
problemas = temas.validar(tema)
comprobar(not problemas, f"tema por defecto sin problemas {problemas or ''}")
comprobar(len(tema["widgets"]) == 8, "trae los 8 widgets de ejemplo")
comprobar((tema["ancho"], tema["alto"]) == (1920, 462), "lienzo de 1920x462")

# --------------------------------------------------------------------------- 2
print("\n2) El validador detecta lo que rompe el render")
casos = {
    "tipo desconocido": {"version": 1, "widgets": [{"tipo": "queso", "x": 0, "y": 0}]},
    "coordenada fuera": {"version": 1, "widgets": [
        {"tipo": "texto", "x": 5000, "y": 0, "texto": "hola"}]},
    "sin fuente": {"version": 1, "widgets": [{"tipo": "dato", "x": 0, "y": 0}]},
    "fuente desconocida": {"version": 1, "widgets": [
        {"tipo": "dato", "x": 0, "y": 0, "fuente": "Temperatura del cafe"}]},
    "color invalido": {"version": 1, "widgets": [
        {"tipo": "texto", "x": 0, "y": 0, "texto": "x", "color": "#GGGGGG"}]},
    "min y max al reves": {"version": 1, "widgets": [
        {"tipo": "barra", "x": 0, "y": 0, "fuente": "CPU Usage", "min": 100, "max": 0}]},
    "campo del editor oficial": {"version": 1, "widgets": [
        {"tipo": "texto", "x": 0, "y": 0, "texto": "x", "rotate": 90}]},
    "sin lista de widgets": {"version": 1},
}
for nombre, roto in casos.items():
    encontrados = temas.validar(roto)
    comprobar(bool(encontrados), f"'{nombre}' -> avisa ({encontrados[0][:70] if encontrados else 'NADA'})")
comprobar(not temas.validar(temas.tema_por_defecto()), "y el tema bueno sigue pasando")

# --------------------------------------------------------------------------- 3
print("\n3) Catalogo, plantillas y marcadores")
comprobar(set(temas.CATALOGO) == {"dato", "barra", "grafica", "texto", "reloj"},
          "los cinco tipos de widget")
for tipo in temas.CATALOGO:
    campos = temas.campos(tipo)
    comprobar("x" in campos and "y" in campos, f"{tipo}: tiene x e y en el catalogo")
    comprobar(all(len(v) == 3 for v in campos.values()),
              f"{tipo}: cada campo trae (tipo, por_defecto, ayuda)")
minimo = temas.tema_nuevo(widgets=[temas.plantilla("reloj", x=40, y=40, formato="%H:%M:%S")])
comprobar(not temas.validar(minimo), "una plantilla de reloj es valida tal cual")
comprobar(temas.plantilla("clock")["tipo"] == "reloj", "'clock' es alias de 'reloj'")
try:
    temas.plantilla("dato", chorrada=1)
    comprobar(False, "plantilla rechaza campos que no existen")
except ValueError:
    comprobar(True, "plantilla rechaza campos que no existen")
fuentes = temas.fuentes_disponibles(instanciar=False)
comprobar(len(fuentes["editor"]) >= 35, f"{len(fuentes['editor'])} nombres de fuente del editor")
comprobar("CPU Usage" in fuentes["editor"] and "cpu_temp" in fuentes["propias"],
          "acepta tanto nombres del editor como claves internas")
comprobar(any("{cpu_temp}" == m for m in temas.marcadores()["forma"]),
          "los marcadores incluyen la forma {clave}")

# --------------------------------------------------------------------------- 4
print("\n4) Dibujo")
comprobar(not temas.validar(temas.normalizar(temas.tema_por_defecto())), "normalizar no rompe nada")
f = Fuentes()
f.muestra()
ruta = os.path.join(SALIDA, "tema.png")
temas.renderizar(temas.tema_por_defecto(), ruta, f)
comprobar(os.path.getsize(ruta) > 5000, f"el PNG se dibuja ({os.path.getsize(ruta)} B)")
from PIL import Image                                                      # noqa: E402
with Image.open(ruta) as imagen:
    comprobar(imagen.size == (1920, 462), f"el PNG mide {imagen.size[0]}x{imagen.size[1]}")
    comprobar(len(imagen.getcolors(maxcolors=1 << 24) or []) > 4, "el dibujo tiene varios colores")
datos = temas.renderizar_datos(temas.tema_por_defecto(), f)
comprobar(datos[:8] == b"\x89PNG\r\n\x1a\n", "renderizar_datos devuelve un PNG en memoria")
comprobar(len(datos) == os.path.getsize(ruta), "y pesa lo mismo que el escrito en disco")

# --------------------------------------------------------------------------- 5
print("\n5) Los ejemplos del kit son validos")
for ruta_ejemplo in sorted(glob.glob(os.path.join(RAIZ, "ejemplos", "*.json"))):
    try:
        with open(ruta_ejemplo, encoding="utf-8") as fh:
            ejemplo = json.load(fh)
    except ValueError as exc:
        comprobar(False, f"{os.path.basename(ruta_ejemplo)}: JSON invalido ({exc})")
        continue
    problemas = temas.validar(ejemplo)
    comprobar(not problemas, f"{os.path.basename(ruta_ejemplo)}: {len(ejemplo.get('widgets', []))} "
                             f"widgets, sin problemas {problemas or ''}")
    salida = os.path.join(SALIDA, os.path.basename(ruta_ejemplo).replace(".json", ".png"))
    temas.renderizar(ejemplo, salida, f)
    comprobar(os.path.getsize(salida) > 3000, f"{os.path.basename(ruta_ejemplo)}: se dibuja")

print("")
if FALLOS:
    print(f"{len(FALLOS)} comprobaciones FALLIDAS")
    for fallo in FALLOS:
        print(f"  - {fallo}")
    sys.exit(1)
print(f"todas las comprobaciones de temas han pasado (PNGs en {SALIDA})")
