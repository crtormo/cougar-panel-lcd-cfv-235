#!/usr/bin/env python3
"""Analiza tramas del panel ya capturadas: no necesita ni panel ni permisos.

Sirve para lo que aparece cuando algo falla: mirar los bytes que se midieron y comprobar que el
framing sigue cuadrando. Analiza capturas crudas de hidraw (`.bin`) y tambien ficheros de texto
con las tramas en hexadecimal (`tramas_de_prueba.txt`).

    python3 herramientas/analizar_tramas.py                       # la evidencia del proyecto
    python3 herramientas/analizar_tramas.py captura.bin
    python3 herramientas/analizar_tramas.py captura.bin --json
    python3 herramientas/analizar_tramas.py --todas

Contrasta cada trama con `cfv235.protocolo`, que es la unica fuente de verdad del framing: si
aqui algo no cuadra, el parser (o el documento) tienen un problema, no el panel.
"""

import argparse
import glob
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import protocolo as p                                          # noqa: E402

EVIDENCIA = os.path.join(RAIZ, "docs", "evidencia", "tramas_reales")

CAMPOS_INTERESANTES = ("bootFinish", "space", "brightness", "displayInSleep", "osdState",
                       "mode", "degree", "background", "logo", "timeout", "sn", "version")


def tramas_de_bytes(datos: bytes):
    """Todas las tramas completas de un buffer, saltando el relleno y lo que no sea trama."""
    salida = []
    i = 0
    while i < len(datos):
        if datos[i] != p.START:
            i += 1
            continue
        trama = p.decode_frame(datos[i:])
        if trama is None:
            i += 1
            continue
        salida.append((datos[i:i + trama.consumidos], trama))
        i += trama.consumidos
    return salida


def tramas_de_texto(ruta: str):
    """Tramas escritas en hexadecimal, una por linea (`hex : 5a0036...`)."""
    salida = []
    for linea in open(ruta, encoding="utf-8", errors="replace"):
        if "hex" not in linea.lower() or ":" not in linea:
            continue
        hexa = linea.split(":", 1)[1].strip()
        if hexa and len(hexa) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in hexa):
            crudo = bytes.fromhex(hexa)
            trama = p.decode_frame(crudo)
            if trama is not None:
                salida.append((crudo, trama))
    return salida


def resumen_de_cuerpo(cuerpo: str):
    try:
        datos = json.loads(cuerpo)
    except ValueError:
        return None
    if not isinstance(datos, dict):
        return None
    return {k: datos[k] for k in CAMPOS_INTERESANTES if k in datos}


def analizar(ruta: str, verboso: bool = False):
    datos = open(ruta, "rb").read()
    tramas = tramas_de_bytes(datos)
    modo = "binario"
    if not tramas:
        tramas = tramas_de_texto(ruta)
        modo = "texto (hex)"
    informe = {"fichero": os.path.basename(ruta), "bytes": len(datos), "modo": modo,
               "tramas": [], "correctas": 0, "fallos": []}
    for indice, (crudo, trama) in enumerate(tramas, 1):
        code, ack, cabecera, cuerpo, campos = p.parse_respuesta(trama.payload)
        campos_ok = trama.len_ok and trama.checksum_ok
        if campos_ok:
            informe["correctas"] += 1
        else:
            informe["fallos"].append(
                f"trama {indice}: len {'ok' if trama.len_ok else 'MAL'} "
                f"({trama.declarado} declarado / {trama.cable} en el cable), "
                f"checksum {'ok' if trama.checksum_ok else 'MAL'}")
        informe["tramas"].append({
            "n": indice, "declarado": trama.declarado, "cable": trama.cable,
            "escapes": trama.escapes, "checksum": f"0x{trama.checksum_leido:02x}",
            "code": code, "ack": ack, "cabecera": cabecera.strip(),
            "cuerpo": resumen_de_cuerpo(cuerpo), "correcta": campos_ok,
        })
        if verboso:
            print(f"  [{indice}] declarado={trama.declarado} cable={trama.cable} "
                  f"escapes={trama.escapes} code={code} ack={ack}"
                  + (f"  {resumen_de_cuerpo(cuerpo)}" if resumen_de_cuerpo(cuerpo) else ""))
    return informe


def main():
    ap = argparse.ArgumentParser(description="Analiza tramas ya capturadas del panel CFV235")
    ap.add_argument("ficheros", nargs="*", help="capturas (.bin o texto con hex)")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    ap.add_argument("--todas", action="store_true",
                    help="analiza toda la evidencia del proyecto (docs/evidencia/tramas_reales)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    rutas = list(args.ficheros)
    if args.todas or not rutas:
        rutas = sorted(glob.glob(os.path.join(EVIDENCIA, "*.bin"))) + \
                sorted(glob.glob(os.path.join(EVIDENCIA, "*.txt")))
    if not rutas:
        print(f"no hay capturas en {EVIDENCIA}", file=sys.stderr)
        return 2

    informes = [analizar(ruta, args.verbose) for ruta in rutas if os.path.isfile(ruta)]
    if args.json:
        print(json.dumps(informes, indent=2, ensure_ascii=False))
        return 0 if all(i["correctas"] == len(i["tramas"]) for i in informes) else 1

    total = correctas = 0
    for informe in informes:
        total += len(informe["tramas"])
        correctas += informe["correctas"]
        print(f"\n{informe['fichero']}  ({informe['bytes']:,} B, {informe['modo']})")
        for t in informe["tramas"]:
            marca = "ok  " if t["correcta"] else "MAL "
            print(f"  {marca}[{t['n']}] len={t['declarado']:>4} cable={t['cable']:>4} "
                  f"escapes={t['escapes']} code={t['code']} ack={t['ack']}")
            if t["cuerpo"]:
                for clave, valor in t["cuerpo"].items():
                    print(f"        {clave} = {valor!r}")
            elif t["cabecera"]:
                print(f"        {t['cabecera'][:100]}")
        for fallo in informe["fallos"]:
            print(f"  !! {fallo}")
    print(f"\n=== {correctas}/{total} tramas correctas ===")
    return 0 if correctas == total else 1


if __name__ == "__main__":
    sys.exit(main())
