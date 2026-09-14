"""Tramas y mensajes del panel COUGAR CFV235. Protocolo **verificado en hardware**.

Modelo, tal cual se midio en el panel y coincide con la implementacion independiente
`cougarLCD.cpp` para este mismo modelo:

    cable : 5A | len(BE16) | payload_escapado | checksum | 5A | ceros de relleno
    informe: 1 byte de report ID (0x00) + 1024 bytes de datos = 1025 B

    escape   : 0x5A -> 5B 01  y  0x5B -> 5B 02. Se aplica AL PAYLOAD, A LOS DOS BYTES
               DE LONGITUD Y AL CHECKSUM.
    len      : payload_sin_escapar + 5. OJO: no cuenta los bytes de codigo del escape,
               asi que la trama del cable es MAS LARGA que len cuando hay escapes
               (medido: len=371, cable=373).
    checksum : (byte_alto + byte_bajo + suma(payload)) & 0xFF. Se suman los DOS BYTES de
               longitud, NO el valor len. Con len<256 ambas reglas coinciden; con
               len=371 -> 1+115=116, mientras que 371 mod 256 = 115: de ahi el desfase
               de exactamente 1 que se midio en la respuesta de propiedades.

Y por que el fin de trama se busca por el 0x5A de cierre y nunca por `len`: cortar en
`len` falla en cuanto hay un escape (el numero de serie BYZL..., cuya 'Z' es 0x5A,
basta) y la respuesta entera se pierde en silencio.
"""

import json
import re
import time

# --------------------------------------------------------------------------- constantes
VID = 0x1D6B              # VID "Linux Foundation": es lo que reporta el panel
PID = 0x0126
START = 0x5A
ESC = 0x5B
READ_SIZE = 4096          # buffer de lectura holgado: con uno corto el kernel da EINVAL
REPORT_SIZE = 1025        # informe completo: byte de report ID 0x00 + 1024 de datos

# Informes de medios (la subida de imagenes). NO llevan checksum ni escapes.
MEDIA_START = 0x5C        # delimitador
MEDIA_TIPO_FONDO = 0x02   # capa de fondo: ACUMULA memoria (~170 KB por imagen grande)
MEDIA_TIPO_OSD = 0x01     # capa OSD: reutiliza su hueco (~2-19 KB), va ENCIMA del fondo
MEDIA_MENSAJE = 0x16      # contador de la transferencia; el panel no exige un valor fijo
MEDIA_TROZO = 1000        # bytes de datos por bloque (1000 = 1025 - 25, encaja justo)
MEDIA_CABECERA = 24       # cabecera del cuerpo, SIN el byte de report ID

# Tiempos criticos de la subida (medidos del editor oficial):
#   transport -> primer bloque ........ 87 ms   (la sesion caduca en menos de 1 s)
#   ultimo bloque -> transported ...... 6 ms
MS_PRIMER_BLOQUE = 87
MS_TRANSPORTED = 6


# --------------------------------------------------------------------------- trama
def checksum(payload: bytes, total: int) -> int:
    """(byte_alto + byte_bajo + suma(payload)) & 0xFF. Ver la nota del modulo."""
    return ((total >> 8) + (total & 0xFF) + sum(payload)) & 0xFF


def append_escaped(valor: int, salida: bytearray) -> None:
    """Escribe un byte aplicando el escape del protocolo (0x5A -> 5B 01, 0x5B -> 5B 02)."""
    if valor in (0x5A, 0x5B):
        salida.append(ESC)
        salida.append(0x01 if valor == 0x5A else 0x02)
    else:
        salida.append(valor)


def build_frame(payload: bytes) -> bytes:
    """Trama completa sin relleno: 5A | len(BE16) | payload | checksum | 5A."""
    total = len(payload) + 5
    trama = bytearray()
    trama.append(START)
    append_escaped((total >> 8) & 0xFF, trama)
    append_escaped(total & 0xFF, trama)
    for valor in payload:
        append_escaped(valor, trama)
    append_escaped(checksum(payload, total), trama)
    trama.append(START)
    return bytes(trama)


def decode_frame(buf: bytes):
    """Decodifica la trama que empieza en buf[0]; None si aun falta informacion.

    Devuelve un dict con: payload sin escapar, len declarado, si cuadra, el checksum
    leido y si cuadra, los escapes encontrados y cuantos bytes consume del buffer.
    """
    if not buf or buf[0] != START:
        return None
    trama = bytearray([START])
    escapes = []
    i = 1
    terminada = False
    while i < len(buf):
        valor = buf[i]
        if valor == START:
            trama.append(valor)
            i += 1
            terminada = True
            break
        if valor == ESC:
            if i + 1 >= len(buf):
                return None                       # falta el byte de codigo
            codigo = buf[i + 1]
            if codigo not in (0x01, 0x02):
                return None                       # escape invalido
            trama.append(0x5A if codigo == 0x01 else 0x5B)
            escapes.append(0x5A if codigo == 0x01 else 0x5B)
            i += 2
        else:
            trama.append(valor)
            i += 1
    if not terminada or len(trama) < 5:
        return None
    declarado = (trama[1] << 8) | trama[2]
    payload = bytes(trama[3:-2])
    return {
        "payload": payload,
        "len": declarado,
        "len_ok": declarado == len(trama),
        "checksum": trama[-2],
        "checksum_ok": checksum(payload, declarado) == trama[-2],
        "trailer_ok": trama[-1] == START,
        "escapes": escapes,
        "consumidos": i,
    }


_seq = 0


def nueva_secuencia() -> int:
    """Numero de secuencia de la siguiente peticion."""
    global _seq
    _seq += 1
    return _seq


def build_request(cmd, body=None, method="POST", seq=None,
                  extra_headers=None, raw_body=None):
    """Trama de una peticion y su numero de secuencia.

    body=None       -> sin ContentType/ContentLength (p. ej. `conn`)
    raw_body=bytes  -> cuerpo binario tal cual (bloques de fichero)
    extra_headers   -> lista de (clave, valor) tras Date, p. ej.
                       [("FileName","f.png"), ("FileBlockId","1")]
    """
    global _seq
    if seq is None:
        _seq += 1
        seq = _seq
    cabecera = f"{method} {cmd} 1\r\nSeqNumber={seq}\r\nDate={int(time.time() * 1000)}\r\n"
    for clave, valor in (extra_headers or []):
        cabecera += f"{clave}={valor}\r\n"
    payload = cabecera.encode("ascii")
    if raw_body is not None:
        payload += f"ContentType=json\r\nContentLength={len(raw_body)}\r\n\r\n".encode("ascii")
        payload += raw_body
    elif body is not None:
        bruto = json.dumps(body, separators=(",", ":")).encode("utf-8")
        payload += b"ContentType=json\r\n" + f"ContentLength={len(bruto)}\r\n".encode("ascii")
        payload += b"\r\n" + bruto
    else:
        payload += b"\r\n"
    return build_frame(payload), seq


def parse_frame(frame: bytes):
    """Valida y descompone una trama completa -> (payload, todo_ok)."""
    info = decode_frame(frame)
    if info is None:
        return b"", False
    return info["payload"], (info["checksum_ok"] and info["trailer_ok"] and info["len_ok"])


def parse_response(payload: bytes):
    """Respuesta -> (code, cabecera_legible, cuerpo_texto)."""
    partes = payload.split(b"\r\n\r\n", 1)
    cabecera = partes[0].decode("ascii", "replace").replace("\r\n", " | ")
    cuerpo = partes[1].decode("utf-8", "replace") if len(partes) > 1 else ""
    coincidencia = re.match(r"\s*\d+\s+(\d{3})", cabecera)
    return (int(coincidencia.group(1)) if coincidencia else None), cabecera, cuerpo


def parse_ack(cabecera: str):
    """AckNumber de la cabecera legible, o None.

    El panel acusa casi siempre con AckNumber = SeqNumber + 1, pero a veces responde con
    el MISMO numero (medido: seq 755 -> Ack 755) y de vez en cuando manda un aviso no
    pedido con AckNumber=0 (`1 400 AckNumber=0`, `1 200 AckNumber=0`). Por eso quien
    empareja respuestas tiene que aceptar los tres casos.
    """
    coincidencia = re.search(r"AckNumber=(\d+)", cabecera)
    return int(coincidencia.group(1)) if coincidencia else None


# --------------------------------------------------------------------------- medios
def build_media_report(indice: int, total_bloques: int, trozo: bytes,
                       tipo: int = MEDIA_TIPO_FONDO, mensaje: int = MEDIA_MENSAJE) -> bytes:
    """Informe de medios: 24 B de cabecera + datos (SIN relleno y SIN checksum).

    Sobre el informe completo (con el byte de report ID en la posicion 0):
        [0]=0x00 ID | [1]=0x5C | [2..3]=longitud BE | [4]=contador | [5..6]=n bloques
        [7..8]=indice | [9]=tipo de capa | [10..24]=ceros | [25...]=datos

    La longitud que se escribe es 21 + datos: cuenta desde el byte del contador hasta el
    ultimo byte de la cabecera, mas los datos. El byte de report ID lo pone quien escribe.
    """
    cabecera = bytearray(MEDIA_CABECERA)
    cabecera[0] = MEDIA_START
    total = 21 + len(trozo)
    cabecera[1] = (total >> 8) & 0xFF
    cabecera[2] = total & 0xFF
    cabecera[3] = mensaje
    cabecera[4] = (total_bloques >> 8) & 0xFF
    cabecera[5] = total_bloques & 0xFF
    cabecera[6] = (indice >> 8) & 0xFF
    cabecera[7] = indice & 0xFF
    cabecera[8] = tipo
    return bytes(cabecera) + trozo


def bloques_de(tamano: int) -> int:
    """Cuantos bloques ocupa un fichero de `tamano` bytes."""
    return (tamano + MEDIA_TROZO - 1) // MEDIA_TROZO


def cabecera_media(reporte: bytes) -> dict:
    """Descompone un informe de medios ya recibido (para depurar/tests)."""
    if len(reporte) < MEDIA_CABECERA or reporte[0] != MEDIA_START:
        return {"valido": False}
    return {
        "valido": True,
        "longitud": (reporte[1] << 8) | reporte[2],
        "mensaje": reporte[3],
        "total_bloques": (reporte[4] << 8) | reporte[5],
        "indice": (reporte[6] << 8) | reporte[7],
        "tipo": reporte[8],
        "datos": len(reporte) - MEDIA_CABECERA,
    }
