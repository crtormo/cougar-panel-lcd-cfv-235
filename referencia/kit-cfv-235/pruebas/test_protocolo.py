"""Pruebas del protocolo: tramas reales del panel + coherencia con la implementacion
original (`referencia/cougar_panel.py`, la que se verifico contra el hardware).

Se ejecuta con `python3 pruebas/test_protocolo.py` y no necesita ni panel ni Linux.
"""

import importlib.util
import os
import re
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

from cougar import protocolo as p                                            # noqa: E402

FALLOS = []


def comprobar(condicion, mensaje):
    if condicion:
        print(f"   OK   {mensaje}")
    else:
        print(f"   FALLA {mensaje}")
        FALLOS.append(mensaje)


def cargar_referencia():
    """La implementacion original, tal cual, como modulo suelto."""
    ruta = os.path.join(RAIZ, "referencia", "cougar_panel.py")
    if not os.path.exists(ruta):
        return None
    especificacion = importlib.util.spec_from_file_location("cougar_panel_referencia", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def tramas_en(buffer):
    """Todas las tramas completas de un buffer (saltando lo que no sea trama)."""
    salida = []
    i = 0
    while i < len(buffer):
        if buffer[i] != p.START:
            i += 1
            continue
        info = p.decode_frame(bytes(buffer[i:]))
        if info is None:
            i += 1
            continue
        salida.append(bytes(buffer[i:i + info["consumidos"]]))
        i += info["consumidos"]
    return salida


def texto_a_bytes(texto):
    """Convierte el `\\r\\n` literal del fichero de tramas en bytes de verdad."""
    return texto.encode("ascii", "ignore").decode("unicode_escape").encode("latin-1")


# --------------------------------------------------------------------------- 1
print("1) Respuestas REALES del panel (tramas_reales/*.bin)")
for nombre in ("respuesta_conn_1.bin", "respuesta_conn_2.bin"):
    ruta = os.path.join(RAIZ, "tramas_reales", nombre)
    if not os.path.exists(ruta):
        comprobar(False, f"falta {nombre}")
        continue
    with open(ruta, "rb") as fh:
        datos = fh.read()
    encontradas = tramas_en(datos)
    comprobar(bool(encontradas), f"{nombre}: se encuentra al menos una trama ({len(encontradas)})")
    for trama in encontradas:
        info = p.decode_frame(trama)
        comprobar(info["checksum_ok"], f"{nombre}: checksum correcto (len={info['len']})")
        comprobar(info["len_ok"], f"{nombre}: longitud declarada cuadra con la trama")
        payload, ok = p.parse_frame(trama)
        code, cabecera, cuerpo = p.parse_response(payload)
        comprobar(ok and code == 200, f"{nombre}: respuesta {code}")
        if cuerpo:
            comprobar("bootFinish" in cuerpo or "version" in cuerpo,
                      f"{nombre}: el cuerpo trae las propiedades del panel")
        if info["escapes"]:
            print(f"        (trama con {len(info['escapes'])} escapes: len={info['len']}, "
                  f"en el cable {info['consumidos']} B)")

# --------------------------------------------------------------------------- 2
print("\n2) Tramas conocidas: reconstruirlas byte a byte (tramas_reales/tramas_de_prueba.txt)")
ruta_txt = os.path.join(RAIZ, "tramas_reales", "tramas_de_prueba.txt")
if os.path.exists(ruta_txt):
    with open(ruta_txt, encoding="utf-8") as fh:
        contenido = fh.read()
    payloads = re.findall(r"^\s*payload : (.*)$", contenido, re.MULTILINE)
    hexes = re.findall(r"^\s*hex\s*: ([0-9a-fA-F]+)\s*$", contenido, re.MULTILINE)
    comprobar(len(payloads) == len(hexes) and payloads, f"{len(payloads)} tramas documentadas")
    for i, (texto_payload, texto_hex) in enumerate(zip(payloads, hexes), 1):
        esperado = bytes.fromhex(texto_hex)
        # el fichero trae el payload como texto con \r\n literales
        if texto_payload.startswith("hex:"):
            payload = bytes.fromhex(texto_payload[4:].strip())
        else:
            payload = texto_a_bytes(texto_payload)
        construida = p.build_frame(payload)
        comprobar(construida == esperado,
                  f"trama {i}: build_frame devuelve exactamente los mismos bytes")
        info = p.decode_frame(construida)
        comprobar(info is not None and info["checksum_ok"] and info["len_ok"],
                  f"trama {i}: se vuelve a decodificar con checksum y longitud correctos")
else:
    comprobar(False, "falta tramas_reales/tramas_de_prueba.txt")

# --------------------------------------------------------------------------- 3
print("\n3) Checksum: los dos casos medidos")
payload = bytes(range(200))
comprobar(p.checksum(payload, 205) == (205 + sum(payload)) & 0xFF,
          "con len<256 coincide con sum(payload)+len")
# caso medido: len=371 -> bytes 1 y 115 -> hay que sumar 116, no 115
comprobar(p.checksum(b"", 371) == 116, "len=371 -> se suman los DOS bytes de longitud (1+115=116)")
comprobar(p.checksum(b"", 371) != (371 & 0xFF), "y NO el valor len (371 & 0xFF = 115)")

# --------------------------------------------------------------------------- 4
print("\n4) Informes de medios (la subida)")
datos = bytes(range(256)) * 5
informe = p.build_media_report(0, 3, datos[:1000])
info = p.cabecera_media(informe)
comprobar(len(informe) == 1024, "cabecera de 24 B + 1000 de datos = 1024 (el 0x00 lo pone el envio)")
comprobar(info["longitud"] == 1021, "la longitud escrita es 21 + datos")
comprobar(info["total_bloques"] == 3 and info["indice"] == 0, "bloques e indice correctos")
comprobar(info["tipo"] == p.MEDIA_TIPO_FONDO, "capa de fondo por defecto (0x02)")
comprobar(p.cabecera_media(p.build_media_report(1, 3, b"x" * 10, p.MEDIA_TIPO_OSD))["tipo"]
          == p.MEDIA_TIPO_OSD, "se puede pedir la capa OSD (0x01)")
comprobar(informe[0] == p.MEDIA_START, "el informe empieza por 0x5C")
comprobar(len(p.build_media_report(2, 3, b"y" * 744)) == 24 + 744,
          "el ultimo bloque va SIN relleno (768 B, no 1025)")
comprobar(p.bloques_de(3744) == 4, "3744 B -> 4 bloques de 1000 (el medido con el editor)")

# --------------------------------------------------------------------------- 5
print("\n5) Coherencia con la implementacion original (referencia/cougar_panel.py)")
referencia = cargar_referencia()
if referencia is None:
    print("   (no esta la referencia: se salta)")
else:
    for payload in (b"POST conn 1\r\nSeqNumber=1\r\nDate=1\r\n\r\n",
                    b"STATE all 1\r\nSeqNumber=99\r\nDate=2\r\n\r\n{}",
                    bytes(range(256)) * 2):      # con bytes altos, para forzar escapes
        comprobar(p.build_frame(payload) == referencia.build_frame(payload),
                  f"build_frame identico ({len(payload)} B)")
    for cmd, cuerpo, metodo in (("conn", None, "POST"),
                                ("brightness", {"value": 42}, "POST"),
                                ("all", {"cpu": {"load": 7}}, "STATE")):
        # El campo Date lleva la hora en milisegundos y entra en el checksum, asi que se
        # fija el reloj para que las dos implementaciones generen exactamente lo mismo.
        reloj = time.time
        time.time = lambda: 1789254723.754
        try:
            nuestra, seq1 = p.build_request(cmd, cuerpo, metodo, seq=7)
            suya, seq2 = referencia.build_request(cmd, cuerpo, metodo, seq=7)
        finally:
            time.time = reloj
        comprobar(nuestra == suya and seq1 == seq2,
                  f"build_request identico ({metodo} {cmd})")
        if nuestra != suya:
            for i in range(min(len(nuestra), len(suya))):
                if nuestra[i] != suya[i]:
                    comprobar(False, f"   primer byte distinto en {i}: "
                                     f"{nuestra[i]:#04x} vs {suya[i]:#04x}")
                    break
    trozo = bytes(range(256)) * 4
    comprobar(p.build_media_report(2, 5, trozo[:1000], p.MEDIA_TIPO_FONDO, 0x16)
              == referencia.build_media_report(2, 5, trozo[:1000]),
              "build_media_report identico (fondo)")
    for total in (5, 255, 371, 1024, 65535):
        comprobar(p.checksum(b"abc", total) == referencia.checksum(b"abc", total),
                  f"checksum identico con len={total}")

# --------------------------------------------------------------------------- fin
print("")
if FALLOS:
    print(f"{len(FALLOS)} comprobaciones FALLIDAS")
    for fallo in FALLOS:
        print(f"  - {fallo}")
    sys.exit(1)
print("todas las comprobaciones del protocolo han pasado")
