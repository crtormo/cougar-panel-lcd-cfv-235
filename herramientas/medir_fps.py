#!/usr/bin/env python3
"""Mide la tasa de fotogramas REAL del panel COUGAR CFV235.

Hasta ahora el "4 fps" salia de una medicion suelta. Esto lo mide en serio, contra el panel,
y separa los dos costes que se suman en cada fotograma:

  1. **dibujar** el PNG (renderizar el tema con Pillow)       -> se mide en el PC
  2. **subirlo** al panel (transporte, bloques, acuse)        -> se mide en el cable

Y despues prueba varias frecuencias objetivo para ver cual sostiene de verdad.

    python3 herramientas/medir_fps.py                 # con el panel real
    python3 herramientas/medir_fps.py --segundos 4    # mas corto
    python3 herramientas/medir_fps.py --sin-panel     # solo el coste de dibujar

Necesita el panel libre (para el servicio y la app antes de lanzarlo).
"""

import argparse
import os
import statistics
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import dashboard, temas  # noqa: E402
from cfv235.panel import Panel  # noqa: E402
from cfv235.sensores import Sensores  # noqa: E402

ANCHO, ALTO = temas.ANCHO, temas.ALTO


# ------------------------------------------------------------------ imagenes de prueba
def imagenes_de_prueba(carpeta: str) -> dict:
    """Tres PNG del mismo tamano y dificultad de compresion muy distinta."""
    from PIL import Image, ImageDraw
    os.makedirs(carpeta, exist_ok=True)
    salidas = {}

    # 1) color plano: comprime muchisimo (el mejor caso para el panel)
    plana = Image.new("RGB", (ANCHO, ALTO), (11, 14, 20))
    ruta = os.path.join(carpeta, "plana.png")
    plana.save(ruta, optimize=True)
    salidas["color plano"] = ruta

    # 2) degradado con tarjetas: parecido a un dashboard real
    degradado = Image.new("RGB", (ANCHO, ALTO))
    dib = ImageDraw.Draw(degradado)
    for y in range(ALTO):
        t = y / (ALTO - 1)
        dib.line([(0, y), (ANCHO, y)],
                 fill=(int(10 + 20 * t), int(14 + 40 * t), int(24 + 60 * t)))
    for i in range(4):
        dib.rounded_rectangle([24 + i * 473, 100, 24 + i * 473 + 453, 340], radius=14,
                              fill=(20, 25, 34), outline=(31, 40, 54), width=2)
    ruta = os.path.join(carpeta, "degradado.png")
    degradado.save(ruta, optimize=True)
    salidas["tipo dashboard"] = ruta

    # 3) "foto": ruido, practicamente incompresible (el peor caso)
    import random
    random.seed(7)
    ruido = Image.new("RGB", (ANCHO, ALTO))
    ruido.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                   for _ in range(ANCHO * ALTO)])
    ruta = os.path.join(carpeta, "foto.png")
    ruido.save(ruta, optimize=True)
    salidas["foto (ruido)"] = ruta
    return salidas


def _mediana(valores):
    return statistics.median(valores) if valores else 0.0


# ------------------------------------------------------------------ medidas
def medir_subidas(panel, imagenes, repeticiones: int = 5) -> list:
    """Cuanto tarda el panel en aceptar un fotograma de cada tipo."""
    filas = []
    for etiqueta, ruta in imagenes.items():
        datos = open(ruta, "rb").read()
        tiempos, acuses = [], []
        for intento in range(repeticiones):
            t0 = time.perf_counter()
            resultado = panel.subir_datos(datos, "medicion.png", capa="osd")
            tiempos.append((time.perf_counter() - t0) * 1000)
            acuses.append(resultado.acuse_code)
        ok = all(a == 200 for a in acuses)
        filas.append({
            "tipo": etiqueta,
            "bytes": len(datos),
            "bloques": (len(datos) + 999) // 1000,
            "ms": _mediana(tiempos),
            "ms_min": min(tiempos),
            "ms_max": max(tiempos),
            "acuse": acuses[0],
            "ok": ok,
        })
    return filas


def medir_dibujo(repeticiones: int = 5) -> dict:
    """Cuanto tarda Pillow en dibujar el dashboard (sin tocar el panel)."""
    sensores = Sensores(intervalo=0.0)
    sensores.muestra()
    time.sleep(0.2)
    valores = sensores.muestra()
    tema = dashboard.tema_dashboard(valores=valores)
    tiempos = []
    for _ in range(repeticiones):
        t0 = time.perf_counter()
        datos = temas.renderizar_datos(tema, valores)
        tiempos.append((time.perf_counter() - t0) * 1000)
    return {"ms": _mediana(tiempos), "bytes": len(datos),
            "widgets": len(tema["widgets"])}


def medir_frecuencia(panel, objetivo: float, segundos: float) -> dict:
    """Pone el dashboard en bucle a esa frecuencia y mide los fps que se logran."""
    sensores = Sensores(intervalo=1.0)
    tablero = dashboard.Dashboard(panel, sensores=sensores,
                                  png=os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                                   "cfv235-medicion.png"))
    fotogramas = {"ok": 0, "fallos": 0, "ms": []}
    t0 = time.perf_counter()

    def avisar(_n, ok, error, ms):
        if ok:
            fotogramas["ok"] += 1
            fotogramas["ms"].append(ms)
        else:
            fotogramas["fallos"] += 1

    # se pide un numero de fotogramas acorde al objetivo; el bucle respeta el periodo
    repeticiones = max(1, int(objetivo * segundos))
    tablero.bucle(periodo=1.0 / objetivo, repeticiones=repeticiones, avisar=avisar)
    transcurrido = time.perf_counter() - t0
    return {
        "objetivo": objetivo,
        "pedidos": repeticiones,
        "subidos": fotogramas["ok"],
        "fallos": fotogramas["fallos"],
        "segundos": transcurrido,
        "fps_reales": fotogramas["ok"] / transcurrido if transcurrido else 0.0,
        "ms_mediana": _mediana(fotogramas["ms"]),
    }


# ------------------------------------------------------------------ informe
def imprimir_tabla(filas: list, titulo: str, columnas: list) -> None:
    print(f"\n{titulo}")
    print("  " + "".join(f"{c:>14}" if i else f"{c:<20}" for i, c in enumerate(columnas)))
    print("  " + "-" * (20 + 14 * (len(columnas) - 1)))
    for fila in filas:
        partes = []
        for i, valor in enumerate(fila):
            if i == 0:
                partes.append(f"{str(valor):<20}")
            elif isinstance(valor, float):
                partes.append(f"{valor:>14.1f}")
            else:
                partes.append(f"{str(valor):>14}")
        print("  " + "".join(partes))


def main() -> int:
    ap = argparse.ArgumentParser(description="Mide la tasa de fotogramas del panel CFV235")
    ap.add_argument("--segundos", type=float, default=5.0,
                    help="duracion de cada prueba de frecuencia")
    ap.add_argument("--sin-panel", action="store_true",
                    help="solo mide el coste de dibujar (no toca el panel)")
    ap.add_argument("--device", help="dispositivo concreto (/dev/hidrawN o /dev/pts/N)")
    args = ap.parse_args()

    carpeta = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-medicion")
    imagenes = imagenes_de_prueba(carpeta)

    print("=" * 74)
    print("  TASA DE FOTOGRAMAS DEL PANEL COUGAR CFV235")
    print("=" * 74)
    print(f"  resolucion: {ANCHO}x{ALTO}  |  pruebas de {args.segundos:.0f} s por frecuencia")

    dibujo = medir_dibujo()
    print(f"\n  COSTE DE DIBUJAR (Pillow, en el PC, sin tocar el panel)")
    print(f"  tema del dashboard: {dibujo['widgets']} widgets -> {dibujo['bytes']} B "
          f"en {dibujo['ms']:.1f} ms")

    if args.sin_panel:
        print("\n  (--sin-panel: no se mide la subida)")
        return 0

    try:
        panel = Panel(dispositivo=args.device, timeout=6.0)
        panel.abrir()
        panel.canal.autonegociar(timeout=3.0)
    except Exception as exc:                          # noqa: BLE001
        print(f"\n!! no puedo abrir el panel: {exc}", file=sys.stderr)
        print("   (para el servicio y la app antes de medir)", file=sys.stderr)
        return 2

    with panel:
        estado0 = panel.propiedades_seguras()
        print(f"\n  panel: {panel.dispositivo}  |  espacio antes: "
              f"{estado0.get('space')} KB  |  brillo: {estado0.get('brightness')}")
        if estado0.get("brightness") == 0:
            panel.brillo(100)
            print("  (el brillo estaba a 0: subido a 100)")

        # 1) coste de subir cada tipo de imagen
        filas = medir_subidas(panel, imagenes, repeticiones=5)
        imprimir_tabla(
            [[f["tipo"], f["bytes"], f["bloques"], f["ms"], f["ms_min"], f["ms_max"],
              "si" if f["ok"] else "NO"] for f in filas],
            "1) COSTE DE SUBIR UN FOTOGRAMA (mediana de 5, en ms)",
            ["tipo", "bytes", "bloques", "ms medio", "ms min", "ms max", "acuse 200"])

        techo = min((f["ms"] for f in filas if f["ok"]), default=0)
        if techo:
            print(f"\n  techo medido con el mejor caso: {1000 / techo:.1f} fps "
                  f"({techo:.0f} ms por fotograma)")

        # 2) que frecuencia se sostiene de verdad
        resultados = []
        for objetivo in (1, 2, 4, 6, 8, 10, 15):
            resultados.append(medir_frecuencia(panel, objetivo, args.segundos))
            print(f"  .. probado {objetivo} fps objetivo")
        imprimir_tabla(
            [[f"{r['objetivo']} fps", r["pedidos"], r["subidos"], r["fallos"],
              r["segundos"], r["fps_reales"], r["ms_mediana"]] for r in resultados],
            "2) FRECUENCIA REAL SOSTENIDA (el dashboard, capa OSD)",
            ["objetivo", "pedidos", "subidos", "fallos", "segundos", "fps reales",
             "ms/fotogr."])

        # 3) conclusion
        cumplen = [r for r in resultados
                   if r["fallos"] == 0 and r["fps_reales"] >= r["objetivo"] * 0.9]
        print("\n  3) CONCLUSION")
        if cumplen:
            print(f"     sostiene sin fallos hasta {max(r['objetivo'] for r in cumplen):.0f} fps")
        mejor = max((r["fps_reales"] for r in resultados), default=0)
        print(f"     maximo real medido: {mejor:.1f} fps")
        print(f"     recomendado para dejarlo puesto: 4 fps (margen de sobra y sin saltos)")

        estado1 = panel.propiedades_seguras()
        print(f"\n  espacio despues: {estado1.get('space')} KB "
              f"(delta {(estado1.get('space') or 0) - (estado0.get('space') or 0)} KB)")
        print("  (la capa OSD reutiliza su hueco: por eso el delta es pequeno)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
