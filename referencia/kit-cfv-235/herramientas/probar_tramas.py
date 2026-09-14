#!/usr/bin/env python3
"""Comprueba el modelo de trama contra tramas REALES capturadas del panel.

Uso:
    python3 probar_tramas.py                 # usa tramas_reales/*.bin
    python3 probar_tramas.py captura.bin ...  # ficheros concretos

Cada fichero debe contener un informe HID crudo (con ceros de relleno) o una trama
suelta. Sirve como prueba de regresion: si el modelo de framing se rompe, falla.
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "referencia"))
import cougar_panel as panel   # noqa: E402  (la implementacion original, en referencia/)


def comprobar(ruta):
    with open(ruta, "rb") as fh:
        crudo = fh.read()
    inicio = crudo.find(bytes([panel.START]))
    if inicio < 0:
        return False, "no hay ningun 0x5A en el fichero"
    info = panel.decode_frame(crudo[inicio:])
    if info is None:
        return False, "decode_frame devolvio None"

    fallos = []
    if not info["len_ok"]:
        fallos.append(f"len declarado {info['len']} != tamano decodificado")
    if not info["checksum_ok"]:
        fallos.append(f"checksum 0x{info['checksum']:02x} no cuadra")
    if not info["trailer_ok"]:
        fallos.append("no termina en 0x5A")

    # La formula VIEJA (sumar el valor len) debe fallar cuando len >= 256 y la
    # trama lleva escapes: es la comprobacion de que la correccion es real.
    vieja = (sum(info["payload"]) + info["len"]) & 0xFF
    nueva = panel.checksum(info["payload"], info["len"])

    code, cabecera, cuerpo = panel.parse_response(info["payload"])
    if code != 200:
        fallos.append(f"code={code}")
    propiedades = None
    if cuerpo:
        try:
            propiedades = json.loads(cuerpo)
        except json.JSONDecodeError as exc:
            fallos.append(f"el cuerpo no es JSON: {exc}")

    print(f"\n{ruta}")
    print(f"  informe crudo        : {len(crudo)} B")
    print(f"  len declarado        : {info['len']}")
    print(f"  bytes de la trama    : {info['consumidos']}  "
          f"(len + {info['consumidos'] - info['len']} por los escapes)")
    print(f"  escapes              : {len(info['escapes'])} "
          f"{[hex(e) for e in info['escapes']]}")
    print(f"  cabecera             : {cabecera}")
    print(f"  checksum formula NUEVA (bytes de longitud) : 0x{nueva:02x} "
          f"{'CUADRA' if nueva == info['checksum'] else 'NO CUADRA'}")
    print(f"  checksum formula VIEJA (valor len)         : 0x{vieja:02x} "
          f"{'cuadra' if vieja == info['checksum'] else 'NO cuadra (esperado)'}")
    if propiedades:
        print(f"  JSON                 : {len(propiedades)} campos")
        for clave in ("bootFinish", "space", "logo", "brightness", "degree",
                      "background", "sn", "timeout"):
            if clave in propiedades:
                print(f"      {clave} = {propiedades[clave]!r}")
    if fallos:
        print(f"  RESULTADO            : FALLO -> {'; '.join(fallos)}")
        return False, "; ".join(fallos)
    print("  RESULTADO            : OK")
    return True, ""


def main(rutas):
    if not rutas:
        # en este kit las capturas estan un nivel mas arriba, en tramas_reales/
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for base in (os.path.join(raiz, "tramas_reales"),
                     os.path.join(os.path.dirname(os.path.abspath(__file__)), "tramas_reales")):
            rutas = sorted(glob.glob(os.path.join(base, "*.bin")))
            if rutas:
                break
    if not rutas:
        print("No hay tramas que comprobar.", file=sys.stderr)
        return 2
    ok = 0
    for ruta in rutas:
        try:
            bien, _ = comprobar(ruta)
        except Exception as exc:                      # noqa: BLE001 (informe claro)
            print(f"\n{ruta}\n  EXCEPCION: {exc}")
            bien = False
        ok += 1 if bien else 0
    print(f"\n=== {ok}/{len(rutas)} tramas correctas ===")
    return 0 if ok == len(rutas) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
