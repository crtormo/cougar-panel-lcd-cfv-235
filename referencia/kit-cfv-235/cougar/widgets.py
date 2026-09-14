#!/usr/bin/env python3
"""widgets.py - motor de widgets del panel COUGAR CFV235.

Lee un tema en JSON, resuelve las fuentes de datos y dibuja el PNG de 1920x462 que se sube
al panel. Reproduce los tipos de widget del editor de COUGAR (texto, dato, barra, grafica)
y acepta **sus nombres de fuente** (`CPU Temperature`, `GPU Usage`...) ademas de los nuestros.

Uso:
    python3 widgets.py --tema tema.json --salida panel.png
    python3 widgets.py --por-defecto tema.json     # escribe un tema de ejemplo
    python3 widgets.py --datos                     # fuentes disponibles ahora mismo

Tipos de widget
---------------
  reloj     formato strftime (por defecto "%H:%M")
  texto     texto fijo con marcadores {clave} y {fuente:Nombre}
  dato      tarjeta con etiqueta, valor grande, unidad y barra opcional   <- "data" del editor
  barra     etiqueta + barra horizontal + porcentaje
  grafica   grafica de linea con el historial de una fuente               <- "chart" del editor

Campos comunes: x, y, color, tamano. En `dato`/`barra`/`grafica`: ancho, alto, etiqueta,
fuente, unidad, min, max, color_relleno, radio, puntos.
"""

import argparse
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

ANCHO, ALTO = 1920, 462
FONDO = "#0e1117"
TARJETA = "#161b24"
BORDE = "#2c3442"
TEXTO = "#ebf0f8"
GRIS = "#788291"
ACENTO = "#00d0ff"
ACENTO2 = "#ff00c8"
_fuentes_cache = {}


def color(valor, por_defecto=(255, 255, 255)):
    if not valor:
        return por_defecto
    valor = str(valor).lstrip("#")
    if len(valor) == 6:
        return tuple(int(valor[i:i + 2], 16) for i in (0, 2, 4))
    return por_defecto


def _fuente(tam, negrita=False):
    clave = (tam, negrita)
    if clave in _fuentes_cache:
        return _fuentes_cache[clave]
    from PIL import ImageFont
    nombre = "DejaVuSans-Bold.ttf" if negrita else "DejaVuSans.ttf"
    candidatas = [
        f"/usr/share/fonts/truetype/dejavu/{nombre}",
        f"/usr/share/fonts/dejavu/{nombre}",
        f"/usr/share/fonts/TTF/{nombre}",
        os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "arialbd.ttf" if negrita else "arial.ttf"),
        os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "segoeuib.ttf" if negrita else "segoeui.ttf"),
    ]
    for ruta in candidatas:
        if os.path.exists(ruta):
            _fuentes_cache[clave] = ImageFont.truetype(ruta, tam)
            return _fuentes_cache[clave]
    _fuentes_cache[clave] = ImageFont.load_default()
    return _fuentes_cache[clave]


def formatear(valor, unidad=None):
    """Presenta el valor como lo haria el editor: entero para %, un decimal para el resto."""
    if valor is None:
        return "--"
    if isinstance(valor, str):
        return valor
    if unidad == "%":
        return f"{valor:.0f}"
    if isinstance(valor, float) and valor != int(valor):
        return f"{valor:.1f}"
    return f"{int(valor)}"


def texto_con_marcadores(plantilla, fuentes):
    """Sustituye {clave} y {fuente:Nombre del editor} por los valores actuales."""
    import re

    def sustituir(m):
        dentro = m.group(1)
        if dentro.startswith("fuente:"):
            valor = fuentes.valor(dentro[len("fuente:"):])
        else:
            valor = fuentes.valor(dentro)
        return formatear(valor)

    return re.sub(r"\{([^}]+)\}", sustituir, plantilla)


# --------------------------------------------------------------------------- widgets
def dibujar_dato(dib, w, fuentes):
    x, y = w.get("x", 0), w.get("y", 0)
    ancho, alto = w.get("ancho", 340), w.get("alto", 300)
    dib.rounded_rectangle([x, y, x + ancho, y + alto], radius=w.get("radio", 18),
                          fill=color(w.get("fondo", TARJETA)), outline=color(w.get("borde", BORDE)),
                          width=2)
    dib.text((x + 28, y + 24), str(w.get("etiqueta", "")), font=_fuente(26), fill=color(w.get("color_etiqueta", GRIS)))
    unidad = w.get("unidad", "")
    valor = formatear(fuentes.valor(w.get("fuente", "")), unidad)
    tamano = w.get("tamano", 74)
    # el valor tambien se encoge si no cabe en la tarjeta (numeros largos con unidad)
    fuente_valor = _encajar(dib, valor, tamano, ancho - 90)
    dib.text((x + 24, y + 74), valor, font=fuente_valor, fill=color(w.get("color", TEXTO)))
    ancho_valor = dib.textlength(valor, font=fuente_valor)
    if unidad:
        dib.text((x + 34 + ancho_valor, y + 74 + tamano * 0.42), unidad, font=_fuente(28),
                 fill=color(w.get("color_etiqueta", GRIS)))
    # "detalle": segunda linea con otro dato (p. ej. temperatura y frecuencia bajo el uso).
    # Si al resolverlo solo quedan guiones (el sistema no expone esos sensores) NO se dibuja:
    # una linea de "-- C  -- MHz" queda peor que el hueco.
    if w.get("detalle"):
        texto_detalle = texto_con_marcadores(str(w["detalle"]), fuentes)
        if any(c.isdigit() for c in texto_detalle):
            fuente_detalle = _encajar(dib, texto_detalle, w.get("tamano_detalle", 30), ancho - 56)
            dib.text((x + 28, y + 74 + tamano + 26), texto_detalle, font=fuente_detalle,
                     fill=color(w.get("color_detalle", "#9aa6b8")))
    if w.get("barra", True):
        numero = fuentes.valor(w.get("fuente", ""))
        bx, by, bw, bh = x + 28, y + alto - 102, ancho - 56, 26
        dib.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=color("#262d3a"))
        if isinstance(numero, (int, float)):
            minimo = w.get("min", 0)
            maximo = w.get("max", 100)
            frac = 0 if maximo == minimo else (numero - minimo) / (maximo - minimo)
            relleno = max(0.0, min(1.0, frac)) * bw
            if relleno > 2:
                dib.rounded_rectangle([bx, by, bx + relleno, by + bh], radius=8,
                                      fill=color(w.get("color_relleno", ACENTO)))
        if w.get("mostrar_porcentaje", False):
            # por defecto NO: el valor grande de arriba ya lo dice y quedaba duplicado
            dib.text((bx, by + 36), f"{valor} {unidad}".strip(), font=_fuente(20),
                     fill=color(w.get("color_etiqueta", GRIS)))


def dibujar_barra(dib, w, fuentes):
    x, y = w.get("x", 0), w.get("y", 0)
    ancho, alto = w.get("ancho", 500), w.get("alto", 34)
    unidad = w.get("unidad", "%")
    valor = fuentes.valor(w.get("fuente", ""))
    dib.text((x, y - 30), str(w.get("etiqueta", "")), font=_fuente(22), fill=color(w.get("color_etiqueta", GRIS)))
    dib.rounded_rectangle([x, y, x + ancho, y + alto], radius=alto // 2, fill=color("#262d3a"))
    if isinstance(valor, (int, float)):
        minimo, maximo = w.get("min", 0), w.get("max", 100)
        frac = 0 if maximo == minimo else (valor - minimo) / (maximo - minimo)
        relleno = max(0.0, min(1.0, frac)) * ancho
        if relleno > 2:
            dib.rounded_rectangle([x, y, x + relleno, y + alto], radius=alto // 2,
                                  fill=color(w.get("color_relleno", ACENTO)))
    dib.text((x + ancho + 16, y + 4), f"{formatear(valor, unidad)} {unidad}".strip(),
             font=_fuente(22), fill=color(w.get("color", TEXTO)))


def dibujar_grafica(dib, w, fuentes):
    x, y = w.get("x", 0), w.get("y", 0)
    ancho, alto = w.get("ancho", 560), w.get("alto", 120)
    serie = fuentes.serie(w.get("fuente", ""))[-(w.get("puntos", 120)):]
    dib.rounded_rectangle([x, y, x + ancho, y + alto], radius=w.get("radio", 14),
                          fill=color(w.get("fondo", TARJETA)), outline=color(w.get("borde", BORDE)), width=2)
    if w.get("etiqueta"):
        dib.text((x + 18, y + 10), str(w["etiqueta"]), font=_fuente(20), fill=color(w.get("color_etiqueta", GRIS)))

    minimo = w.get("min", min(serie) if serie else 0)
    maximo = w.get("max", (max(serie) if serie else 100) or 1)
    if w.get("auto") and len(serie) > 1:
        # autoescala: si el rango real es mucho menor que 0-100 (una CPU al 8 %), la linea
        # se aplastaba abajo; asi se ven las variaciones.
        bajo, alto_serie = min(serie), max(serie)
        margen = max(1.0, (alto_serie - bajo) * 0.25)
        minimo = max(0, bajo - margen)
        maximo = alto_serie + margen
    rango = (maximo - minimo) or 1
    izq, der = x + 18, x + ancho - 18
    arr, aba = y + 34, y + alto - 16

    if len(serie) < 2:
        # Aun no hay historial: se dibuja una linea plana con el valor actual, que se ve
        # mucho mejor que un hueco vacio mientras el panel arranca.
        valor = fuentes.valor(w.get("fuente", ""))
        if isinstance(valor, (int, float)):
            frac = max(0.0, min(1.0, (valor - minimo) / rango))
            yy = aba - (aba - arr) * frac
            dib.line([izq, yy, der, yy], fill=color(w.get("color_relleno", ACENTO)),
                     width=w.get("grosor", 3))
            dib.text((der - 90, y + 10),
                     f"{formatear(valor, w.get('unidad'))} {w.get('unidad', '')}".strip(),
                     font=_fuente(24, True), fill=color(w.get("color", TEXTO)))
        dib.text((izq, aba - 6), "recogiendo historial...", font=_fuente(16),
                 fill=color(w.get("color_etiqueta", GRIS)))
        return

    # rejilla
    for i in range(1, 4):
        yy = arr + (aba - arr) * i / 4
        dib.line([izq, yy, der, yy], fill=color("#232a36"), width=1)
    puntos = []
    for i, valor in enumerate(serie):
        px = izq + (der - izq) * i / max(1, len(serie) - 1)
        py = aba - (aba - arr) * max(0.0, min(1.0, (valor - minimo) / rango))
        puntos.append((px, py))
    relleno = color(w.get("color_relleno", ACENTO))
    dib.polygon(puntos + [(der, aba), (izq, aba)], fill=tuple(int(c * 0.22) for c in relleno))
    dib.line(puntos, fill=relleno, width=w.get("grosor", 3), joint="curve")
    ultimo = serie[-1]
    dib.text((der - 90, y + 10), f"{formatear(ultimo, w.get('unidad'))} {w.get('unidad', '')}".strip(),
             font=_fuente(24, True), fill=color(w.get("color", TEXTO)))


def _encajar(dib, contenido, tamano, limite, maximo=None):
    """Devuelve una fuente cuyo texto quepa en `limite` (baja el tamano si hace falta).

    Es lo que evita el fallo mas visible de cualquier tema: que un texto largo se salga del
    panel o se meta encima de otro widget.
    """
    tamano = int(maximo or tamano)
    fuente = _fuente(tamano)
    while tamano > 10 and dib.textlength(contenido, font=fuente) > limite:
        tamano -= 1
        fuente = _fuente(tamano)
    return fuente


def dibujar_texto(dib, w, fuentes):
    contenido = texto_con_marcadores(str(w.get("texto", "")), fuentes)
    tamano = w.get("tamano", 28)
    # Si no se indica ancho, el limite es lo que queda hasta el borde del panel.
    limite = w.get("ancho") or (ANCHO - w.get("x", 0) - 24)
    fuente = _encajar(dib, contenido, tamano, limite)
    dib.text((w.get("x", 0), w.get("y", 0)), contenido, font=fuente,
             fill=color(w.get("color", TEXTO)))


def dibujar_reloj(dib, w, fuentes):
    import time
    dib.text((w.get("x", 0), w.get("y", 0)), time.strftime(w.get("formato", "%H:%M")),
             font=_fuente(w.get("tamano", 120), True), fill=color(w.get("color", TEXTO)))


TIPOS = {
    "dato": dibujar_dato, "data": dibujar_dato,
    "barra": dibujar_barra, "bar": dibujar_barra,
    "grafica": dibujar_grafica, "chart": dibujar_grafica,
    "texto": dibujar_texto, "text": dibujar_texto,
    "reloj": dibujar_reloj, "clock": dibujar_reloj,
}


# --------------------------------------------------------------------------- motor
def renderizar(tema, salida, fuentes):
    """Dibuja el tema (dict) en `salida`. Devuelve la ruta."""
    from PIL import Image, ImageDraw

    imagen = Image.new("RGB", (tema.get("ancho", ANCHO), tema.get("alto", ALTO)),
                       color(tema.get("fondo", FONDO)))
    dib = ImageDraw.Draw(imagen)
    for w in tema.get("widgets", []):
        funcion = TIPOS.get(str(w.get("tipo", "")).lower())
        if funcion is None:
            print(f"!! tipo de widget desconocido: {w.get('tipo')!r}", file=sys.stderr)
            continue
        try:
            funcion(dib, w, fuentes)
        except Exception as exc:                      # noqa: BLE001 (un widget no debe tumbar el tema)
            print(f"!! fallo el widget {w.get('tipo')} ({w.get('fuente', '')}): {exc}",
                  file=sys.stderr)
    imagen.save(salida, "PNG")
    return salida


def tema_por_defecto():
    """Tema de ejemplo: lo mismo que dibujaba el dashboard fijo, pero editable."""
    return {
        "version": 1,
        "ancho": 1920, "alto": 462, "fondo": "#0e1117",
        "widgets": [
            {"tipo": "reloj", "x": 60, "y": 52, "tamano": 122, "formato": "%H:%M"},
            {"tipo": "texto", "x": 66, "y": 226, "tamano": 30, "color": "#788291",
             "texto": "{fecha}"},
            {"tipo": "texto", "x": 66, "y": 268, "tamano": 22, "color": "#5c6675",
             "texto": "{cpu_modelo}"},
            {"tipo": "grafica", "x": 60, "y": 300, "ancho": 570, "alto": 142,
             "etiqueta": "CPU (%)", "fuente": "CPU Usage", "min": 0, "max": 100,
             "unidad": "%", "puntos": 120, "color_relleno": "#00d0ff", "auto": True},
            {"tipo": "dato", "x": 680, "y": 60, "ancho": 340, "alto": 342,
             "etiqueta": "CPU", "fuente": "CPU Usage", "unidad": "%",
             "color_relleno": "#00d0ff", "detalle": "{cpu_temp} C      {cpu_mhz} MHz"},
            {"tipo": "dato", "x": 1050, "y": 60, "ancho": 340, "alto": 342,
             "etiqueta": "MEMORIA", "fuente": "Memory Usage", "unidad": "%",
             "color_relleno": "#ff00c8",
             "detalle": "{ram_usado_gb} / {ram_total_gb} GB"},
            {"tipo": "dato", "x": 1420, "y": 60, "ancho": 340, "alto": 342,
             "etiqueta": "GPU", "fuente": "GPU Usage", "unidad": "%",
             "color_relleno": "#00d0ff", "detalle": "{gpu_temp} C      {gpu_mhz} MHz"},
            {"tipo": "texto", "x": 680, "y": 418, "tamano": 22, "color": "#788291",
             "texto": "CPU {cpu_temp} C    {cpu_mhz} MHz    {cpu_w} W    "
                      "RAM {ram_usado_gb}/{ram_total_gb} GB    Disco {disco_uso}%    "
                      "Red {red_mb} MB/s"},
        ],
    }


def main():
    ap = argparse.ArgumentParser(description="Motor de widgets del panel CFV235")
    ap.add_argument("--tema", help="tema en JSON")
    ap.add_argument("--salida", default="/tmp/cougar-dashboard.png")
    ap.add_argument("--por-defecto", metavar="RUTA", help="escribe el tema de ejemplo y sale")
    ap.add_argument("--datos", action="store_true", help="muestra las fuentes disponibles")
    args = ap.parse_args()

    if args.por_defecto:
        with open(args.por_defecto, "w", encoding="utf-8") as fh:
            json.dump(tema_por_defecto(), fh, indent=2, ensure_ascii=False)
        print(f"tema de ejemplo escrito en {args.por_defecto}")
        return 0

    try:
        from fuentes import Fuentes
    except ImportError as exc:
        print(f"!! falta fuentes.py: {exc}", file=sys.stderr)
        return 2

    fuentes = Fuentes()
    fuentes.muestra()

    if args.datos:
        import time
        time.sleep(1.0)
        datos = fuentes.muestra()["valores"]
        for clave in sorted(datos):
            print(f"   {clave:<18} {datos[clave]}")
        return 0

    if not args.tema:
        tema = tema_por_defecto()
        print("(sin --tema: se usa el tema de ejemplo)")
    else:
        with open(args.tema, encoding="utf-8") as fh:
            tema = json.load(fh)

    try:
        renderizar(tema, args.salida, fuentes)
    except ImportError:
        print("Falta Pillow. Instalalo con:  pip install pillow", file=sys.stderr)
        return 1
    print(f"PNG: {args.salida}  ({os.path.getsize(args.salida)} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
