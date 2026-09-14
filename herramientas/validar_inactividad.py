#!/usr/bin/env python3
"""Comprueba si el panel se apaga por espera SIN consultarlo durante el silencio.

El problema de medir esto: **cualquier `conn` es trafico y despierta el panel**, asi que
consultar `brightness` no dice nada (medido: un `conn` tarda 324 ms y devuelve brightness 100
justo despues de verlo apagado).

El indicador que se usa aqui es **la latencia del primer `conn`**: si estaba dormido tiene que
despertar y tarda mucho mas que en rafaga. Se toma una rafaga de referencia (despierto), se deja
el panel **en silencio total** el tiempo pedido y despues se mide el primero.

Uso:
    python3 herramientas/validar_inactividad.py --minutos 12 --salida /tmp/inactividad.log
"""

import argparse
import json
import sys
import time

RAIZ = __import__("os").path.dirname(__import__("os").path.dirname(
    __import__("os").path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import canal as modulo_canal  # noqa: E402
from cfv235.canal import Canal  # noqa: E402


def media(valores):
    return sum(valores) / len(valores) if valores else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutos", type=float, default=12.0)
    ap.add_argument("--salida", default="/tmp/inactividad.log")
    args = ap.parse_args()

    def escribir(linea):
        with open(args.salida, "a", encoding="utf-8") as fh:
            fh.write(linea + "\n")
        print(linea, flush=True)

    open(args.salida, "w", encoding="utf-8").close()
    ruta = modulo_canal.buscar()
    if not ruta:
        escribir("!! no encuentro el panel")
        return 2

    canal = Canal(ruta, timeout=8.0, sin_bloqueo=True)
    canal.abrir()
    canal.autonegociar(timeout=5.0)

    def medir():
        t0 = time.perf_counter()
        r = canal.peticion("conn", timeout=10.0)
        ms = (time.perf_counter() - t0) * 1000
        cuerpo = r.cuerpo
        d = json.loads(cuerpo) if isinstance(cuerpo, str) and cuerpo else {}
        return ms, d

    escribir(f"panel: {ruta}")
    ms, d = medir()
    escribir(f"displayInSleep al empezar: {d.get('displayInSleep')}  "
             f"brightness={d.get('brightness')}  bootFinish={d.get('bootFinish')}")
    escribir(f"modo: {args.minutos:.0f} minutos de silencio TOTAL (ni una consulta)")

    # --- rafaga de referencia: el panel esta despierto
    rafaga = []
    for _ in range(5):
        ms, _d = medir()
        rafaga.append(ms)
        time.sleep(0.3)
    escribir("rafaga (despierto): " + " ".join(f"{m:.0f}" for m in rafaga) +
             f"   -> media {media(rafaga):.0f} ms")

    # --- silencio absoluto
    escribir(f"silencio durante {args.minutos:.0f} min...")
    time.sleep(args.minutos * 60)

    # --- el primer conn despues del silencio
    primera, d = medir()
    escribir(f"PRIMER conn tras el silencio: {primera:.0f} ms   "
             f"brightness={d.get('brightness')}  displayInSleep={d.get('displayInSleep')}")
    segundas = []
    for _ in range(3):
        ms, _d = medir()
        segundas.append(ms)
        time.sleep(0.3)
    escribir("siguientes: " + " ".join(f"{m:.0f}" for m in segundas) +
             f"   -> media {media(segundas):.0f} ms")

    # --- conclusion
    base = media(rafaga)
    escribir("")
    if primera > max(base * 3, 150):
        escribir(f"CONCLUSION: el panel ESTABA DORMIDO (el primer conn tardo {primera:.0f} ms "
                 f"frente a {base:.0f} ms en rafaga)")
    else:
        escribir(f"CONCLUSION: el panel SEGUIA DESPIERTO (el primer conn tardo {primera:.0f} ms, "
                 f"parecido a los {base:.0f} ms en rafaga)")
    escribir("(ojo: la lectura de brightness de ese primer conn NO vale: la consulta lo "
             "despierta, y devuelve 100 aunque estuviera apagado)")
    canal.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
