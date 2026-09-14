"""Patrones de calibracion para el panel: imagenes de prueba para comprobar como se ve.

Se dibujan a 1920x462 (el tamano exacto del panel) y sirven para verificar que no se recorta
nada, que los colores salen donde deben y que la nitidez es la esperada.

    from cfv235 import patrones
    patrones.generar("esquinas", "/tmp/esquinas.png")
    cfv235 patron rejilla
"""

from __future__ import annotations

import os

ANCHO, ALTO = 1920, 462

# nombre -> (funcion, descripcion)
CATALOGO = {
    "rejilla": "Cuadricula con numeros en los cruces, para comprobar el encuadre",
    "barras": "Barras verticales de colores, para comprobar el color y el contraste",
    "degradado": "Degradado horizontal de negro a blanco, para ver banding",
    "esquinas": "Marcas en las cuatro esquinas y en el centro de cada borde",
    "cuadros": "Damero de cuadros, para detectar escalado o interpolacion",
    "texto": "Texto de varios tamanos, para comprobar la nitidez de las fuentes",
}


def _fuente(tamano, negrita=False):
    from PIL import ImageFont
    nombre = "DejaVuSans-Bold.ttf" if negrita else "DejaVuSans.ttf"
    for carpeta in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
                    "/usr/share/fonts/TTF"):
        ruta = os.path.join(carpeta, nombre)
        if os.path.exists(ruta):
            return ImageFont.truetype(ruta, tamano)
    return ImageFont.load_default()


def _lienzo(color=(8, 10, 14)):
    from PIL import Image
    return Image.new("RGB", (ANCHO, ALTO), color)


def generar(nombre: str, ruta: str, lado: int = 8, etiquetas: bool = False) -> str:
    """Dibuja el patron `nombre` y lo guarda en `ruta`. Devuelve la ruta.

    Los parametros que no aplican a un patron se ignoran, para que el comando de linea de
    ordenes pueda pasarlos siempre.
    """
    from PIL import ImageDraw
    if nombre not in CATALOGO:
        raise ValueError(f"patron desconocido: {nombre!r} (hay {', '.join(CATALOGO)})")
    if lado < 1:
        raise ValueError(f"'lado' tiene que ser >= 1, no {lado}")

    imagen = _lienzo()
    dib = ImageDraw.Draw(imagen)

    if nombre == "rejilla":
        paso = max(8, lado * 8)
        for x in range(0, ANCHO + 1, paso):
            dib.line([(x, 0), (x, ALTO)], fill=(40, 60, 90), width=1)
        for y in range(0, ALTO + 1, paso):
            dib.line([(0, y), (ANCHO, y)], fill=(40, 60, 90), width=1)
        if etiquetas:
            fuente = _fuente(16)
            for i, x in enumerate(range(0, ANCHO + 1, paso)):
                for j, y in enumerate(range(0, ALTO + 1, paso)):
                    if i % 2 == 0 and j % 2 == 0:
                        dib.text((x + 3, y + 3), f"{x},{y}", font=fuente,
                                 fill=(120, 200, 255))

    elif nombre == "barras":
        colores = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255),
                   (255, 0, 255), (255, 255, 255), (0, 0, 0)]
        columnas = max(1, len(colores))
        ancho = ANCHO // columnas
        for i, color in enumerate(colores):
            dib.rectangle([i * ancho, 0, (i + 1) * ancho - 1, ALTO], fill=color)
        # escala de grises debajo, para comprobar el contraste
        for i in range(16):
            gris = i * 17
            dib.rectangle([i * (ANCHO // 16), ALTO - 60, (i + 1) * (ANCHO // 16) - 1, ALTO],
                          fill=(gris, gris, gris))

    elif nombre == "degradado":
        for x in range(ANCHO):
            valor = int(255 * x / max(1, ANCHO - 1))
            dib.line([(x, 0), (x, ALTO)], fill=(valor, valor, valor))

    elif nombre == "esquinas":
        margen, largo, grosor = 0, 90, 6
        color = (0, 200, 255)
        for (x, y, dx, dy) in ((margen, margen, 1, 1), (ANCHO - 1, margen, -1, 1),
                               (margen, ALTO - 1, 1, -1), (ANCHO - 1, ALTO - 1, -1, -1)):
            dib.line([(x, y), (x + dx * largo, y)], fill=color, width=grosor)
            dib.line([(x, y), (x, y + dy * largo)], fill=color, width=grosor)
        # cruces en el centro de cada borde
        for (x, y) in ((ANCHO // 2, 0), (ANCHO // 2, ALTO - 1), (0, ALTO // 2),
                       (ANCHO - 1, ALTO // 2)):
            dib.line([(x - 30, y), (x + 30, y)], fill=(255, 120, 0), width=4)
            dib.line([(x, y - 30), (x, y + 30)], fill=(255, 120, 0), width=4)
        dib.text((ANCHO // 2 - 60, ALTO // 2 - 20), "CENTRO", font=_fuente(30, True),
                 fill=(255, 255, 255))

    elif nombre == "cuadros":
        for j, y in enumerate(range(0, ALTO, lado)):
            for i, x in enumerate(range(0, ANCHO, lado)):
                if (i + j) % 2 == 0:
                    dib.rectangle([x, y, x + lado - 1, y + lado - 1], fill=(230, 230, 230))

    elif nombre == "texto":
        fuente_grande = _fuente(64, True)
        fuente_media = _fuente(32)
        fuente_pequena = _fuente(18)
        dib.text((30, 20), "1920 x 462  -  nitidez de fuentes", font=fuente_grande,
                 fill=(255, 255, 255))
        dib.text((30, 120), "ABCDEFGHIJKLMNOPQRSTUVWXYZ  abcdefghijklmnopqrstuvwxyz",
                 font=fuente_media, fill=(200, 230, 255))
        dib.text((30, 170), "0123456789  !\"#$%&/()=?*+-.,;:  arbol, camion, nino",
                 font=fuente_media, fill=(200, 230, 255))
        for i, tamano in enumerate((18, 22, 26, 30, 40, 52)):
            dib.text((30 + i * 300, 260), f"{tamano} px", font=_fuente(tamano),
                     fill=(255, 200, 120))
        # lineas de 1 px, para ver si el panel las reproduce
        for i in range(8):
            y = 400 + i * 6
            dib.line([(30, y), (ANCHO - 30, y)], fill=(255, 255, 255), width=1)

    carpeta = os.path.dirname(os.path.abspath(ruta))
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    imagen.save(ruta)
    return ruta


def generar_todos(carpeta: str) -> list[str]:
    """Genera todos los patrones en una carpeta. Devuelve las rutas."""
    os.makedirs(carpeta, exist_ok=True)
    return [generar(nombre, os.path.join(carpeta, f"patron-{nombre}.png"), etiquetas=True)
            for nombre in CATALOGO]


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Patrones de calibracion del panel CFV235")
    ap.add_argument("nombre", nargs="?", help=f"uno de: {', '.join(CATALOGO)}")
    ap.add_argument("--todos", metavar="CARPETA", help="genera todos en una carpeta")
    ap.add_argument("--salida", help="donde guardar el PNG (por defecto /tmp)")
    ap.add_argument("--etiquetas", action="store_true", help="rejilla: numerar los cruces")
    ap.add_argument("--lado", type=int, default=8, help="tamano del cuadro (cuadros/rejilla)")
    args = ap.parse_args()

    if args.todos:
        for ruta in generar_todos(args.todos):
            print(ruta)
        return 0
    if not args.nombre:
        ap.print_help()
        return 1
    salida = args.salida or os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                         f"patron-{args.nombre}.png")
    generar(args.nombre, salida, lado=args.lado, etiquetas=args.etiquetas)
    print(salida)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
