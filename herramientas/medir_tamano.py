#!/usr/bin/env python3
"""Mide hasta donde llega el panel con ficheros grandes.

REGLA DE SEGURIDAD, NO NEGOCIABLE
---------------------------------
El panel se puede quedar inservible (brick) si se le desborda la memoria. Por eso:

  * **techo duro de 20 MB por fichero**: si algo pide mas, se aborta sin enviarlo;
  * se lee el espacio libre ANTES de cada subida y se aborta si no sobra margen;
  * despues de cada subida se comprueba que el panel sigue respondiendo y con
    `bootFinish=1`; si no, se para en el acto y se avisa;
  * se sube a la capa **OSD**, que reutiliza su hueco en vez de acumular.

    python3 herramientas/medir_tamano.py            # la escalera completa
    python3 herramientas/medir_tamano.py --mb 1 5   # solo esos tamanos
"""

import argparse
import io
import json
import os
import random
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from PIL import Image  # noqa: E402

from cfv235 import temas  # noqa: E402
from cfv235.panel import Panel  # noqa: E402

# ------------------------------------------------------------------ limites
TECHO_MB = 20.0        # limite del usuario: NO se pasa de aqui por nada del mundo
MARGEN_MB = 30.0       # espacio que debe quedar libre despues de subir
CARPETA = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-tamano")


def estado(panel) -> dict:
    """Lee `conn` como diccionario."""
    r = panel.canal.peticion("conn", timeout=8.0)
    return json.loads(r.cuerpo) if isinstance(r.cuerpo, str) else r.cuerpo


def generar(mb: float) -> str:
    """Crea un JPEG de ~`mb` MB con ruido, para que no comprima de mas.

    El ruido se crea con `Image.frombytes` sobre bytes aleatorios: en Python puro, rellenar
    millones de pixeles uno a uno tardaria minutos.
    """
    os.makedirs(CARPETA, exist_ok=True)
    ruta = os.path.join(CARPETA, "prueba-%05.1fMB.jpg" % mb)
    objetivo = int(mb * 1024 * 1024)
    proporcion = temas.ANCHO / temas.ALTO

    # bytes por pixel del JPEG con ruido: se empieza con una estimacion y se corrige con lo
    # que salga de verdad, para dar con el tamano pedido en dos o tres vueltas.
    bytes_por_pixel = 1.15
    for _intento in range(7):
        pixeles = max(temas.ANCHO * temas.ALTO, int(objetivo / bytes_por_pixel))
        alto = max(temas.ALTO, min(4600, int((pixeles / proporcion) ** 0.5)))
        ancho = max(temas.ANCHO, min(12000, int(alto * proporcion)))
        imagen = Image.frombytes("RGB", (ancho, alto), os.urandom(ancho * alto * 3))
        imagen.save(ruta, format="JPEG", quality=95)
        tamano = os.path.getsize(ruta)
        if tamano >= objetivo * 0.93:
            break
        bytes_por_pixel *= tamano / objetivo
    return ruta


def main() -> int:
    ap = argparse.ArgumentParser(description="Hasta donde llega el panel con ficheros grandes")
    ap.add_argument("--mb", type=float, nargs="*", default=[1.0, 5.0, 10.0, 15.0, 19.0],
                    help="tamanos a probar, en MB (nunca por encima de 20)")
    args = ap.parse_args()

    pedidos = sorted(args.mb)
    if any(mb > TECHO_MB for mb in pedidos):
        print(f"!! algun tamano pasa del techo de {TECHO_MB:.0f} MB: se aborta", file=sys.stderr)
        return 2

    print("=" * 78)
    print("  TAMANO MAXIMO DE FICHERO DEL PANEL (medido, con guardas)")
    print("=" * 78)
    print(f"  techo duro: {TECHO_MB:.0f} MB   |   margen libre exigido: {MARGEN_MB:.0f} MB")
    print(f"  a probar: {', '.join('%.0f MB' % m for m in pedidos)}")

    try:
        panel = Panel(timeout=8.0)
        panel.abrir()
        panel.canal.autonegociar(timeout=5.0)
    except Exception as exc:                          # noqa: BLE001
        print(f"\n!! no puedo abrir el panel: {exc}", file=sys.stderr)
        return 2

    filas = []
    with panel:
        panel.brillo(100)
        inicial = estado(panel)
        libre_inicial = (inicial.get("space") or 0) / 1024
        print(f"\n  espacio libre al empezar: {libre_inicial:.1f} MB "
              f"(bootFinish={inicial.get('bootFinish')})")

        for mb in pedidos:
            ruta = generar(mb)
            tamano = os.path.getsize(ruta)
            tamano_mb = tamano / 1048576
            if tamano_mb > TECHO_MB:
                print(f"\n  !! {tamano_mb:.1f} MB pasa del techo: NO se envia")
                continue

            antes = estado(panel)
            libre = (antes.get("space") or 0) / 1024
            if libre < tamano_mb + MARGEN_MB:
                print(f"\n  !! solo quedan {libre:.1f} MB libres y el fichero ocupa "
                      f"{tamano_mb:.1f} MB: se PARA por seguridad")
                break

            print(f"\n  --- fichero de {tamano_mb:.1f} MB ({antes.get('space')} KB libres) ---")
            datos = open(ruta, "rb").read()
            t0 = time.perf_counter()
            resultado = panel.subir_datos(datos, "prueba_grande.jpg", capa="osd")
            ms = (time.perf_counter() - t0) * 1000

            time.sleep(0.8)
            despues = estado(panel)
            libre_despues = (despues.get("space") or 0) / 1024
            sigue_vivo = bool(despues.get("bootFinish"))
            filas.append((tamano_mb, resultado.acuse_code, ms, libre_despues, sigue_vivo))
            acuse = resultado.acuse_code if resultado.acuse_code is not None else "-"
            print(f"      ok={resultado.ok}  acuse={acuse}  {ms:.0f} ms  "
                  f"{resultado.bloques} bloques  libres={libre_despues:.1f} MB  "
                  f"bootFinish={despues.get('bootFinish')}")

            os.remove(ruta)
            if not sigue_vivo or not resultado.ok:
                print("\n  !! el panel no acepto el fichero o no arranca: se PARA aqui.")
                print("     (no se sigue subiendo nada mas)")
                break

        final = estado(panel)
        print("\n" + "=" * 78)
        print("  RESUMEN")
        print("=" * 78)
        print(f"  {'tamano':>8} {'acuse':>6} {'ms':>9} {'libres MB':>11} {'vivo':>6}")
        for tamano_mb, code, ms, libre, vivo in filas:
            print(f"  {tamano_mb:7.1f}M {str(code):>6} {ms:>9.0f} {libre:>11.1f} "
                  f"{'si' if vivo else 'NO':>6}")
        print(f"\n  espacio final: {final.get('space')} KB "
              f"(inicio {inicial.get('space')} KB)")
        print(f"  bootFinish final: {final.get('bootFinish')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
