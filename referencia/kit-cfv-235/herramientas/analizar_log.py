#!/usr/bin/env python3
"""
Reconstruye el protocolo del panel COUGAR CFV235 a partir del log del editor.

El COUGAR LCD Editor (Windows) escribe en su log CADA trama que envia y recibe,
en hexadecimal completo. Este script las extrae, valida (longitud + checksum),
decodifica y empareja peticion/respuesta, y saca un JSONL + un resumen.

Uso:
    python3 analizar_log.py --log '2026-9-12.log'
    python3 analizar_log.py --log log.txt --jsonl protocolo.jsonl
    python3 analizar_log.py --dir /ruta/con/varios/logs

Los logs del editor estan en:
    %APPDATA%\\cougar_lcd_editor\\logs\\*.log     (Windows)
Copialos a Linux y pasaselos a este script.

Cuando aparezcan cabeceras FileName / FileBlockId / FileSize / ContentRange,
el script lo avisa: eso es el protocolo de subida por bloques en accion.
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

FRAME_RE = re.compile(r"data hex: (5a[0-9a-fA-F]{8,})")
TIME_RE = re.compile(r"(\d\d:\d\d:\d\d\.\d+)")
BLOCK_HEADERS = ("FileName", "FileBlockId", "FileSize", "ContentRange", "Counter", "Option")

# Una sola fuente de verdad: el modelo de trama verificado, que vive en referencia/.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "referencia"))
import cougar_panel as panel   # noqa: E402


def looks_like_frame(hexstr: str):
    """Valida la trama y devuelve (bytes, payload, escapes) o None.

    Usa el modelo CORREGIDO (cougar_panel.decode_frame):
      - el fin de trama se detecta por el 0x5A de CIERRE, no por el campo de longitud;
      - el checksum suma los DOS BYTES de longitud, no el valor `len`.

    La version anterior exigia `len == longitud total` y sumaba `len`: eso descartaba EN
    SILENCIO todas las tramas con escapes (la respuesta de propiedades mide 373 B y declara
    371), que son justo las respuestas de `conn`.
    """
    if len(hexstr) % 2:
        return None
    try:
        b = bytes.fromhex(hexstr)
    except ValueError:
        return None
    if len(b) < 6 or b[0] != 0x5A:
        return None
    info = panel.decode_frame(b)
    if info is None or info["consumidos"] != len(b):
        return None
    if not (info["checksum_ok"] and info["len_ok"] and info["trailer_ok"]):
        return None
    return b, info["payload"], len(info["escapes"])


def decode(payload: bytes):
    """Decodifica el payload tipo HTTP y devuelve un dict."""
    text = payload.decode("utf-8", "replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    first = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if "=" in line:
            k, v = line.split("=", 1)
            headers[k.strip()] = v.strip()

    m = re.match(r"^(\d+)\s+(\d{3})$", first.strip())
    if m:
        return {"kind": "response", "subtype": int(m.group(1)),
                "code": int(m.group(2)), "headers": headers, "body": body}

    m = re.match(r"^([A-Z]+)\s+(\S+)\s+(\d+)$", first.strip())
    if m:
        return {"kind": "request", "method": m.group(1), "cmd": m.group(2),
                "headers": headers, "body": body}

    return {"kind": "unknown", "first": first, "headers": headers, "body": body}


def scan(path):
    """Devuelve la lista de mensajes encontrados en un fichero de log."""
    msgs = []
    ultima_hora = ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            # La marca de tiempo suele ir en la linea ANTERIOR a la del hex (p. ej.
            # "21:54:44.108 [debug] [main] resp:" y despues "\t data hex: 5a...").
            # Se arrastra la ultima vista para poder correlacionar en el tiempo.
            t = TIME_RE.search(line)
            if t:
                ultima_hora = t.group(1)
            for m in FRAME_RE.finditer(line):
                parsed = looks_like_frame(m.group(1))
                if not parsed:
                    continue
                b, payload, escapes = parsed
                info = decode(payload)
                info["escapes"] = escapes
                info["time"] = ultima_hora
                info["len"] = len(b)
                info["hex"] = m.group(1)
                seq = info["headers"].get("SeqNumber")
                ack = info["headers"].get("AckNumber")
                info["seq"] = int(seq) if seq and seq.isdigit() else None
                info["ack"] = int(ack) if ack and ack.isdigit() else None
                # recorta bodies gigantes en el JSONL
                if len(info["body"]) > 4000:
                    info["body"] = info["body"][:4000] + "...[recortado]"
                msgs.append(info)
    return msgs


def main():
    ap = argparse.ArgumentParser(description="Analizador del log del COUGAR LCD Editor")
    ap.add_argument("--log", action="append", help="fichero de log (repetible)")
    ap.add_argument("--dir", help="carpeta con logs (*.log)")
    ap.add_argument("--jsonl", help="escribir los mensajes en este fichero JSONL")
    args = ap.parse_args()

    paths = list(args.log or [])
    if args.dir:
        paths += sorted(glob.glob(os.path.join(args.dir, "*.log")))
    if not paths:
        default = os.path.expandvars(
            r"%APPDATA%\cougar_lcd_editor\logs\*.log") if os.name == "nt" else None
        paths = sorted(glob.glob(default)) if default else []
    if not paths:
        ap.print_help()
        print("\nNecesito al menos un log: --log fichero.log o --dir carpeta", file=sys.stderr)
        return 2

    total = 0
    todos = []
    for path in paths:
        msgs = scan(path)
        total += len(msgs)
        todos += msgs
        print(f"{path}: {len(msgs)} mensajes validos")

    if not todos:
        print("\nNo he encontrado ninguna trama valida. "
              "¿Es un log del COUGAR LCD Editor?", file=sys.stderr)
        return 1

    # resumen de comandos
    reqs = [m for m in todos if m["kind"] == "request"]
    resps = [m for m in todos if m["kind"] == "response"]
    print(f"\n=== RESUMEN ===")
    print(f"peticiones: {len(reqs)}   respuestas: {len(resps)}")
    print("\n-- peticiones por metodo+cmd --")
    for (method, cmd), n in Counter((m["method"], m["cmd"]) for m in reqs).most_common():
        print(f"   {method:<6} {cmd:<22} {n}")
    print("\n-- respuestas por codigo --")
    for code, n in Counter(m["code"] for m in resps).most_common():
        print(f"   code {code}: {n}")

    # emparejar por SeqNumber / AckNumber
    pend = {}
    emparejados = 0
    for m in todos:
        if m["kind"] == "request" and m["seq"] is not None:
            pend[m["seq"]] = m
        elif m["kind"] == "response" and m["ack"] is not None:
            req = pend.pop(m["ack"] - 1, None)
            if req is not None:
                m["answers"] = f'{req["method"]} {req["cmd"]}'
                emparejados += 1
    print(f"\npeticion/respuesta emparejadas: {emparejados}")

    exitos = Counter()
    fallos = Counter()
    respondidas = Counter()
    pedidas = Counter(f'{m["method"]} {m["cmd"]}' for m in todos if m["kind"] == "request")
    for m in todos:
        if m["kind"] == "response" and m.get("answers"):
            respondidas[m["answers"]] += 1
            if m["code"] == 200:
                exitos[m["answers"]] += 1
            else:
                fallos[(m["answers"], m["code"])] += 1

    print("\n-- respuestas 200 por comando (lo que SI acepta el panel) --")
    for cmd, n in exitos.most_common(15):
        print(f"   {cmd:<24} 200  x{n}")
    if fallos:
        print("\n-- respuestas NO-200 (comando -> codigo) --")
        for (cmd, code), n in fallos.most_common(15):
            print(f"   {cmd:<24} -> {code}  x{n}")
    print("\n-- peticiones sin respuesta (<comando>: sin contestar / enviadas) --")
    for clave, n in pedidas.most_common(10):
        faltan = n - respondidas.get(clave, 0)
        if faltan > 0:
            print(f"   {clave:<24} {faltan} / {n}")

    # protocolo de ficheros
    fich = [m for m in todos if any(h in m["headers"] for h in BLOCK_HEADERS)]
    print(f"\n=== MENSAJES DEL PROTOCOLO DE FICHEROS: {len(fich)} ===")
    if fich:
        print("   ¡Aqui esta el protocolo de subida por bloques!")
        for m in fich[:10]:
            hdrs = {k: v for k, v in m["headers"].items() if k in BLOCK_HEADERS}
            print(f"   [{m['time']}] {m['kind']:<8} {json.dumps(hdrs, ensure_ascii=False)}")
        if len(fich) > 10:
            print(f"   ... y {len(fich) - 10} mas (ver el JSONL)")
    else:
        print("   NO aparecen. El editor nunca completo una subida: el panel rechazo")
        print("   todos los 'transport' con 400. Desatasca el panel y repite la captura.")

    if args.jsonl:
        with open(args.jsonl, "w", encoding="utf-8") as fh:
            for m in todos:
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        print(f"\nescrito {args.jsonl} con {len(todos)} mensajes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
