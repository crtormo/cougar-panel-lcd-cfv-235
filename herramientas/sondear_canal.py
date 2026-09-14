#!/usr/bin/env python3
"""Sondeo del canal de comunicacion PC <-> panel COUGAR CFV235 (USB HID).

Responde a la pregunta que el kit deja abierta: **como hay que escribirle al panel en
Linux**. El descriptor HID que el panel publica en /sys dice:

    Usage Page 0xFF00, Usage 1, Report Size 8, Report Count 0x400 (1024) -> Input
    Usage Page 0xFF00, Usage 1, Report Size 8, Report Count 0x400 (1024) -> Output
    (sin ningun item Report ID)

Es decir: informes de **1024 bytes y sin byte de report ID**. Pero el kit escribe
**1025 bytes con un 0x00 delante**, que es lo que exige la API HID de Windows. En
hidraw, ese byte de mas suele ser un EINVAL.

Este script prueba varias formas de escribir la MISMA peticion `POST conn` (inocua:
es el sondeo que hace el editor cada 6 s) y mide cual acepta el panel, cuanto tarda y
si la respuesta trae o no byte de report ID.

    python3 sondear_canal.py                 # todas las variantes
    python3 sondear_canal.py --device /dev/hidraw4
    python3 sondear_canal.py --json salida.json

No cambia nada en la pantalla: solo manda `conn` (lectura de propiedades).
"""

import argparse
import glob
import json
import os
import select
import struct
import sys
import time

VID, PID = 0x1D6B, 0x0126
START, ESC = 0x5A, 0x5B
REPORT = 1024


# --------------------------------------------------------------------- framing
def escapar(valor, salida):
    if valor in (0x5A, 0x5B):
        salida.append(ESC)
        salida.append(0x01 if valor == 0x5A else 0x02)
    else:
        salida.append(valor)


def checksum(payload, total):
    return ((total >> 8) + (total & 0xFF) + sum(payload)) & 0xFF


def build_frame(payload: bytes) -> bytes:
    total = len(payload) + 5
    trama = bytearray([START])
    escapar((total >> 8) & 0xFF, trama)
    escapar(total & 0xFF, trama)
    for b in payload:
        escapar(b, trama)
    escapar(checksum(payload, total), trama)
    trama.append(START)
    return bytes(trama)


def build_conn(seq: int) -> bytes:
    payload = (f"POST conn 1\r\nSeqNumber={seq}\r\nDate={int(time.time() * 1000)}\r\n\r\n"
               ).encode("ascii")
    return build_frame(payload)


def decode(buf: bytes):
    """Decodifica la primera trama de buf -> dict, o None."""
    if not buf or buf[0] != START:
        return None
    trama = bytearray([START])
    escapes, i, fin = 0, 1, False
    while i < len(buf):
        v = buf[i]
        if v == START:
            trama.append(v); i += 1; fin = True; break
        if v == ESC:
            if i + 1 >= len(buf):
                return None
            cod = buf[i + 1]
            if cod not in (0x01, 0x02):
                return None
            trama.append(0x5A if cod == 0x01 else 0x5B)
            escapes += 1; i += 2
        else:
            trama.append(v); i += 1
    if not fin or len(trama) < 5:
        return None
    declarado = (trama[1] << 8) | trama[2]
    payload = bytes(trama[3:-2])
    return {"payload": payload, "len": declarado, "cable": len(trama), "escapes": escapes,
            "checksum": trama[-2], "checksum_ok": checksum(payload, declarado) == trama[-2],
            "consumidos": i}


def parse_respuesta(payload: bytes):
    cab, _, cuerpo = payload.partition(b"\r\n\r\n")
    texto = cab.decode("ascii", "replace")
    code = None
    for parte in texto.split():
        if parte.isdigit() and len(parte) == 3:
            code = int(parte); break
    ack = None
    for campo in texto.replace("\r\n", " ").split():
        if campo.startswith("AckNumber="):
            ack = int(campo.split("=", 1)[1])
    return code, ack, texto, cuerpo.decode("utf-8", "replace")


# --------------------------------------------------------------------- dispositivo
def _hid_id(ruta):
    base = os.path.basename(ruta)
    try:
        with open(f"/sys/class/hidraw/{base}/device/uevent") as fh:
            for linea in fh:
                if linea.startswith("HID_ID="):
                    _, v, p = linea.strip().split("=")[1].split(":")
                    return int(v, 16), int(p, 16)
    except OSError:
        pass
    return None, None


def buscar_panel(patron="/dev/hidraw*"):
    for ruta in sorted(glob.glob(patron)):
        v, p = _hid_id(ruta)
        if (v, p) == (VID, PID):
            return ruta
    return None


def leer_descriptor(ruta):
    """Devuelve (bytes, analisis) del report descriptor HID, si es legible."""
    base = os.path.basename(ruta)
    rutas = [f"/sys/class/hidraw/{base}/device/report_descriptor",
             f"/sys/class/hidraw/{base}/device/../report_descriptor"]
    for r in rutas:
        try:
            with open(r, "rb") as fh:
                datos = fh.read()
            if datos:
                return datos, analizar_descriptor(datos)
        except OSError:
            continue
    return None, None


def analizar_descriptor(d):
    """Mini-parser de report descriptor: usage page, report IDs y tamanos de informe."""
    i, info = 0, {"usage_page": None, "usage": None, "report_ids": [], "informes": [],
                  "bytes": len(d)}
    tam = tam_cuenta = None
    pila = []
    while i < len(d):
        b = d[i]
        if b == 0xFE:                                     # long item
            tam_dato = d[i + 1] if i + 1 < len(d) else 0
            i += 3 + tam_dato
            continue
        tam_dato = b & 0x03
        tam_dato = 4 if tam_dato == 3 else tam_dato
        tipo = (b >> 2) & 0x03                            # 0 main, 1 global, 2 local
        etiqueta = (b >> 4) & 0x0F
        crudo = d[i + 1:i + 1 + tam_dato]
        valor = int.from_bytes(crudo, "little") if crudo else 0
        if tipo == 1:                                     # global
            if etiqueta == 0x0:
                info["usage_page"] = valor
            elif etiqueta == 0x7:
                tam = valor
            elif etiqueta == 0x8:
                info["report_ids"].append(valor)
            elif etiqueta == 0x9:
                tam_cuenta = valor
        elif tipo == 2:                                   # local
            if etiqueta == 0x0:
                info["usage"] = valor
            elif etiqueta == 0x8:                         # usage minimum (array)
                pila.append(valor)
        elif tipo == 0:                                   # main
            if etiqueta == 0x8:                           # Input
                tipo_inf = "entrada"
            elif etiqueta == 0x9:                         # Output
                tipo_inf = "salida"
            elif etiqueta == 0xB:                         # Feature
                tipo_inf = "feature"
            else:
                tipo_inf = None
            if tipo_inf and tam and tam_cuenta:
                info["informes"].append({"clase": tipo_inf, "bits": tam * tam_cuenta,
                                         "bytes": tam * tam_cuenta // 8})
            if etiqueta in (0x8, 0x9, 0xB, 0xA, 0xC):
                tam_cuenta = None
        i += 1 + tam_dato
    return info


# --------------------------------------------------------------------- sondeo
def drenar(fd, segundos=0.3):
    """Lee y descarta lo que quede pendiente."""
    fin = time.time() + segundos
    while time.time() < fin:
        r, _, _ = select.select([fd], [], [], max(0.0, fin - time.time()))
        if not r:
            break
        try:
            os.read(fd, 4096)
        except OSError:
            break


def escribir_variante(fd, trama, modo):
    """Escribe la trama segun la variante y devuelve (ok, detalle, bytes_escritos)."""
    if modo["prefijo"]:
        cuerpo = b"\x00" + trama
    else:
        cuerpo = trama
    if modo["relleno"]:
        destino = modo["informe"] - (1 if modo["prefijo"] else 0)
        if len(cuerpo) < modo["informe"]:
            cuerpo = cuerpo + b"\x00" * (modo["informe"] - len(cuerpo))
    try:
        n = os.write(fd, cuerpo)
        return True, f"escritos {n} B", n
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}", 0


def probar(fd, trama, modo, timeout=3.0):
    drenar(fd)
    ok, detalle, n = escribir_variante(fd, trama, modo)
    res = {"variante": modo["nombre"], "escritura_ok": ok, "detalle_escritura": detalle,
           "bytes_escritos": n, "respuesta": None, "bytes_leidos": 0,
           "primer_byte": None, "latencia_ms": None, "error": None}
    if not ok:
        return res
    t0 = time.time()
    fin = t0 + timeout
    buffer = bytearray()
    while time.time() < fin:
        r, _, _ = select.select([fd], [], [], max(0.0, fin - time.time()))
        if not r:
            continue
        try:
            trozo = os.read(fd, 4096)
        except OSError as exc:
            res["error"] = f"{type(exc).__name__}: {exc}"
            break
        if not trozo:
            continue
        if res["primer_byte"] is None:
            res["primer_byte"] = trozo[0]
            res["latencia_ms"] = round((time.time() - t0) * 1000, 1)
        res["bytes_leidos"] += len(trozo)
        buffer += trozo
        info = decode(bytes(buffer))
        if info:
            code, ack, cabecera, cuerpo = parse_respuesta(info["payload"])
            res["respuesta"] = {"code": code, "ack": ack, "len": info["len"],
                                "cable": info["cable"], "escapes": info["escapes"],
                                "checksum_ok": info["checksum_ok"],
                                "campos_json": cuerpo.count(":") if cuerpo else 0}
            res["cabecera"] = cabecera.replace("\r\n", " | ")
            res["cuerpo"] = cuerpo[:200]
            break
    if res["respuesta"] is None and res["error"] is None:
        res["error"] = "sin respuesta (timeout)"
    return res


VARIANTES = [
    {"nombre": "A  1025 B = 0x00 + trama + relleno  (lo que hace el kit)",
     "informe": 1025, "prefijo": True, "relleno": True},
    {"nombre": "B  1024 B = trama + relleno         (lo que dice el descriptor)",
     "informe": 1024, "prefijo": False, "relleno": True},
    {"nombre": "C  exacto = 0x00 + trama (sin relleno)",
     "informe": 0, "prefijo": True, "relleno": False},
    {"nombre": "D  exacto = trama (sin prefijo ni relleno)",
     "informe": 0, "prefijo": False, "relleno": False},
    {"nombre": "E  64 B = trama + relleno a multiplo de 64, sin prefijo",
     "informe": 64, "prefijo": False, "relleno": False},
    {"nombre": "F  1025 B = 0x00 + trama + relleno a 1024 (igual que A, control)",
     "informe": 1025, "prefijo": True, "relleno": True},
]


def main():
    ap = argparse.ArgumentParser(description="Sondeo del canal HID del panel CFV235")
    ap.add_argument("--device", help="/dev/hidrawN (por defecto: autodetecta)")
    ap.add_argument("--timeout", type=float, default=3.0, help="espera por respuesta (s)")
    ap.add_argument("--json", help="guardar el resultado completo en JSON")
    ap.add_argument("--solo-descriptor", action="store_true",
                    help="no escribe al panel: solo lee el descriptor HID")
    args = ap.parse_args()

    ruta = args.device or buscar_panel()
    if not ruta:
        print(f"!! no encuentro el panel {VID:#06x}:{PID:#06x}", file=sys.stderr)
        return 2
    print(f"panel: {ruta}   ({VID:#06x}:{PID:#06x})\n")

    descriptor, analisis = leer_descriptor(ruta)
    print("== Descriptor HID (lo que el panel declara) ==")
    if descriptor is None:
        print("   (no legible sin root)")
    else:
        print(f"   {analisis['bytes']} B | usage_page={analisis['usage_page']} "
              f"usage={analisis['usage']} report_ids={analisis['report_ids'] or 'ninguno'}")
        for inf in analisis["informes"]:
            print(f"   informe de {inf['clase']}: {inf['bytes']} B ({inf['bits']} bits)")
        print(f"   descriptor crudo: {descriptor.hex()}")
    print()

    if args.solo_descriptor:
        if args.json:
            with open(args.json, "w") as fh:
                json.dump({"descriptor": descriptor.hex() if descriptor else None,
                           "analisis": analisis}, fh, indent=2)
        return 0

    try:
        fd = os.open(ruta, os.O_RDWR | os.O_NONBLOCK)
    except OSError as exc:
        print(f"!! no puedo abrir {ruta}: {exc}\n"
              f"   instala la regla udev o prueba con sudo", file=sys.stderr)
        return 3

    resultados = []
    try:
        print("== Variantes de escritura de `POST conn` ==")
        seq = 1
        for modo in VARIANTES:
            trama = build_conn(seq)
            res = probar(fd, trama, modo, args.timeout)
            res["trama_cable"] = len(trama)
            resultados.append(res)
            estado = "OK " if res["respuesta"] else "-- "
            extra = ""
            if res["respuesta"]:
                extra = (f"code={res['respuesta']['code']} ack={res['respuesta']['ack']} "
                         f"len={res['respuesta']['len']} campos={res['respuesta']['campos_json']}")
            print(f"  {estado}{modo['nombre']}")
            print(f"        escritura : {res['detalle_escritura']}")
            if res["respuesta"]:
                print(f"        lectura   : {res['bytes_leidos']} B, primer byte "
                      f"0x{res['primer_byte']:02x}, {res['latencia_ms']} ms")
                print(f"        respuesta : {extra}")
            else:
                print(f"        error     : {res['error']}")
            seq += 1
            time.sleep(0.4)
    finally:
        os.close(fd)

    print("\n== Veredicto ==")
    validas = [r for r in resultados if r["respuesta"] and r["respuesta"]["code"] == 200]
    if not validas:
        print("   ninguna variante obtuvo respuesta 200: revisa el cable y que el panel arranque")
    else:
        mejor = validas[0]
        print(f"   {len(validas)}/{len(resultados)} variantes responden 200")
        for r in validas:
            print(f"     - {r['variante']}")
        print(f"   la primera que funciona: {mejor['variante']}")
        pb = {r["primer_byte"] for r in validas}
        print(f"   primer byte de la lectura: {[hex(x) for x in pb if x is not None]}"
              f"   -> {'la respuesta NO trae report ID' if 0x5A in pb else 'ojo: trae report ID'}")
        lecturas = {r["bytes_leidos"] for r in validas}
        print(f"   tamanos de lectura vistos: {sorted(lecturas)}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"dispositivo": ruta,
                       "descriptor": descriptor.hex() if descriptor else None,
                       "analisis_descriptor": analisis,
                       "variantes": resultados}, fh, indent=2, ensure_ascii=False)
        print(f"\nresultado guardado en {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
