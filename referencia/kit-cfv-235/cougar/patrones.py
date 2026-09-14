"""Dibujos de prueba de 1920x462 para calibrar y depurar la pantalla.

Cuando algo "no se ve bien" en el panel conviene separar tres cosas: si el problema es la
imagen, si es la subida o si es la pantalla. Estos patrones lo dicen enseguida:

    barras     colores planos: si salen mal, el panel no esta mostrando lo que le mandas
    rejilla    cuadricula numerada: sirve para medir desplazamientos y recortes
    esquinas   marcas en las cuatro esquinas y cruz central: comprueba el encuadre
    degradado  degradado suave: revela si hay banding o si se pierden niveles
    cuadros    tablero de 1 px y de 8 px: distingue reescalado de pixel perfecto
    texto      el mismo texto en varios tamanos: legibilidad a la distancia real

    python3 -m cougar.patrones --todos /tmp/patrones
    python3 -m cougar.patrones rejilla /tmp/rejilla.png --etiquetas
"""

import argparse
import os
import sys

from . import widgets as motor

ANCHO, ALTO = motor.ANCHO, motor.ALTO
NEGRO, BLANCO = (0, 0, 0), (255, 255, 255)


def _lienzo(fondo=NEGRO):
    from PIL import Image
    return Image.new("RGB", (ANCHO, ALTO), fondo)


def barras(ruta, columnas=8):
    """Barras verticales de colores planos."""
    dib = None
    from PIL import ImageDraw
    imagen = _lienzo()
    dib = ImageDraw.Draw(imagen)
    paleta = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
              (255, 0, 255), (255, 0, 0), (0, 0, 255), (16, 16, 16)]
    ancho = ANCHO // columnas
    for i in range(columnas):
        dib.rectangle([i * ancho, 0, (i + 1) * ancho - 1, ALTO - 1], fill=paleta[i % len(paleta)])
    # franja de grises debajo, para ver el nivel de negro y el de blanco
    for i in range(16):
        nivel = round(i * 255 / 15)
        x0 = round(i * ANCHO / 16)
        x1 = round((i + 1) * ANCHO / 16) - 1
        dib.rectangle([x0, ALTO - 70, x1, ALTO - 1], fill=(nivel, nivel, nivel))
    imagen.save(ruta)
    return ruta


def rejilla(ruta, paso=60, etiquetas=False):
    """Cuadricula; con `etiquetas` numera cada cruce."""
    from PIL import ImageDraw
    imagen = _lienzo((10, 12, 16))
    dib = ImageDraw.Draw(imagen)
    for x in range(0, ANCHO, paso):
        dib.line([x, 0, x, ALTO], fill=(0, 140, 190))
        if etiquetas and x % (paso * 2) == 0:
            dib.text((x + 3, 4), str(x), font=motor._fuente(16), fill=(220, 230, 240))
    for y in range(0, ALTO, paso):
        dib.line([0, y, ANCHO, y], fill=(0, 140, 190))
        if etiquetas and y % (paso * 2) == 0:
            dib.text((3, y + 3), str(y), font=motor._fuente(16), fill=(220, 230, 240))
    imagen.save(ruta)
    return ruta


def esquinas(ruta, grosor=10, largo=140):
    """Marco, esquinas gruesas y cruz central."""
    from PIL import ImageDraw
    imagen = _lienzo((8, 8, 12))
    dib = ImageDraw.Draw(imagen)
    dib.rectangle([0, 0, ANCHO - 1, ALTO - 1], outline=(255, 80, 80), width=grosor)
    for (x, y, dx, dy) in ((0, 0, 1, 1), (ANCHO - 1, 0, -1, 1), (0, ALTO - 1, 1, -1),
                           (ANCHO - 1, ALTO - 1, -1, -1)):
        # rectangulo en forma de dos esquinas: [(x0, y0), (x1, y1)]
        dib.rectangle([(min(x, x + dx * largo), min(y, y + dy * 90)),
                       (max(x, x + dx * largo), max(y, y + dy * 90))],
                      fill=(255, 220, 0))
    dib.line([ANCHO // 2, 0, ANCHO // 2, ALTO], fill=(0, 255, 120), width=3)
    dib.line([0, ALTO // 2, ANCHO, ALTO // 2], fill=(0, 255, 120), width=3)
    dib.text((ANCHO // 2 + 12, ALTO // 2 + 12), f"{ANCHO}x{ALTO}",
             font=motor._fuente(34, True), fill=(255, 255, 255))
    imagen.save(ruta)
    return ruta


def degradado(ruta, pasos=256):
    """Degradado horizontal con el numero de niveles que le pidas (banding)."""
    from PIL import Image
    imagen = _lienzo()
    pixeles = imagen.load()
    for x in range(ANCHO):
        nivel = round((x / (ANCHO - 1)) * 255)
        if pasos < 256:
            nivel = round(round(nivel / (255 / (pasos - 1))) * (255 / (pasos - 1)))
        for y in range(ALTO):
            pixeles[x, y] = (nivel, nivel, nivel)
    imagen.save(ruta)
    return ruta


def cuadros(ruta, lado=8):
    """Tablero de ajedrez: con lado=1 se ve si la pantalla es pixel perfecto."""
    from PIL import Image
    imagen = _lienzo()
    pixeles = imagen.load()
    for y in range(ALTO):
        for x in range(ANCHO):
            if ((x // lado) + (y // lado)) % 2 == 0:
                pixeles[x, y] = BLANCO
    imagen.save(ruta)
    return ruta


def texto(ruta):
    """El mismo texto en varios tamanos y colores: legibilidad."""
    from PIL import ImageDraw
    imagen = _lienzo((12, 14, 20))
    dib = ImageDraw.Draw(imagen)
    dib.text((40, 20), "COUGAR CFV235  1920x462", font=motor._fuente(64, True),
             fill=(255, 255, 255))
    y = 110
    for tam in (84, 60, 44, 34, 26, 20, 16, 13, 10):
        dib.text((40, y), f"{tam} px  ABCDEFGHIJKLM 0123456789  %  /  -  .  :",
                 font=motor._fuente(tam), fill=(235, 240, 248))
        y += tam + 12
    dib.text((1200, 110), "blanco", font=motor._fuente(30), fill=(255, 255, 255))
    dib.text((1200, 150), "gris", font=motor._fuente(30), fill=(128, 132, 140))
    dib.text((1200, 190), "acento", font=motor._fuente(30), fill=(0, 208, 255))
    dib.text((1200, 230), "magenta", font=motor._fuente(30), fill=(255, 0, 200))
    for i, color in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        dib.rectangle([1200 + i * 90, 290, 1270 + i * 90, 400], fill=color)
    imagen.save(ruta)
    return ruta


def plano(ruta, color):
    """Un color plano (por ejemplo (255,255,255) o (0,0,0)) a pantalla completa."""
    _lienzo(color).save(ruta)
    return ruta


PATRONES = {
    "barras": barras, "rejilla": rejilla, "esquinas": esquinas, "degradado": degradado,
    "cuadros": cuadros, "texto": texto,
    "negro": lambda ruta: plano(ruta, NEGRO),
    "blanco": lambda ruta: plano(ruta, BLANCO),
}


def _opciones_de(nombre, opciones):
    """Solo las opciones que ese patron acepta (`rejilla` no entiende `lado`, y al reves)."""
    import inspect
    parametros = inspect.signature(PATRONES[nombre]).parameters
    utiles = {clave: valor for clave, valor in opciones.items()
              if clave in parametros and valor is not None}
    if nombre in ("negro", "blanco"):
        utiles = {}
    return utiles


def generar(nombre, ruta, **opciones):
    """Genera un patron por su nombre. Lanza ValueError si no existe."""
    if nombre not in PATRONES:
        raise ValueError(f"patron desconocido: {nombre} (hay: {', '.join(sorted(PATRONES))})")
    return PATRONES[nombre](ruta, **_opciones_de(nombre, opciones))


def todos(directorio, **opciones):
    """Escribe todos los patrones en un directorio y devuelve la lista de rutas."""
    os.makedirs(directorio, exist_ok=True)
    salidas = []
    for nombre in sorted(PATRONES):
        salidas.append(PATRONES[nombre](os.path.join(directorio, f"{nombre}.png"),
                                        **_opciones_de(nombre, opciones)))
    return salidas


def main():
    ap = argparse.ArgumentParser(description="Patrones de prueba para el panel CFV235")
    ap.add_argument("patron", nargs="?", help="barras | rejilla | esquinas | degradado | cuadros | texto | negro | blanco")
    ap.add_argument("salida", nargs="?", help="PNG de salida")
    ap.add_argument("--todos", metavar="CARPETA", help="escribe todos los patrones ahi")
    ap.add_argument("--etiquetas", action="store_true", help="rejilla: numerar los cruces")
    ap.add_argument("--lado", type=int, default=8, help="cuadros: lado del cuadro en px")
    args = ap.parse_args()

    if args.todos:
        for ruta in todos(args.todos, etiquetas=args.etiquetas, lado=args.lado):
            print(f"  {ruta}  ({os.path.getsize(ruta)} B)")
        return 0
    if not args.patron or not args.salida:
        ap.print_help()
        return 1
    ruta = generar(args.patron, args.salida, etiquetas=args.etiquetas, lado=args.lado)
    print(f"{ruta}  ({os.path.getsize(ruta)} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
