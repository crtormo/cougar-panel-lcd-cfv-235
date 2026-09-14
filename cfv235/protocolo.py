"""Tramas del panel COUGAR CFV235 (9,16", 1920x462, USB HID 1d6b:0126).

Este modulo es la unica fuente de verdad del *framing*. Lo que hay aqui esta contrastado
con tres fuentes independientes:

1. las capturas reales del panel (`respuesta_conn_*.bin` del kit cfv-235),
2. el cliente que el autor del kit verifico contra el hardware,
3. el proyecto independiente `cougarLCD.cpp` (MIT), cuyo `docs/PROTOCOL.md` describe el
   mismo formato y cuyo `src/linux/hid_transport.cpp` es la referencia del protocolo de
   subida.

Formato en el cable:

    informe : [0x00] 5A | len(BE16) | payload_escapado | checksum | 5A | ceros
              ^^^^^^ byte de report ID: SOLO en Windows/hidapi. Ver `canal.py`.

    len      : payload_sin_escapar + 5. NO cuenta los bytes que inserta el escape, asi que
               la trama del cable es mas larga que `len` cuando hay escapes (medido: 371
               declarado, 373 en el cable).
    escape   : 0x5A -> 5B 01, 0x5B -> 5B 02, aplicado AL PAYLOAD, A LOS DOS BYTES DE
               LONGITUD Y AL CHECKSUM.
    checksum : (byte_alto + byte_bajo + suma(payload)) & 0xFF. Se suman los DOS BYTES de
               longitud, no el valor `len` (con len >= 256 las dos reglas no coinciden).

Y el fin de trama se busca por el 0x5A de cierre, NUNCA por `len`: el payload no puede
contener un 0x5A crudo porque va escapado, asi que el cierre es inequivoco. Un parser que
corte por `len` se desincroniza en cuanto aparece un escape.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

# ------------------------------------------------------------------ constantes del canal
VID = 0x1D6B                  # VID de Linux Foundation: es lo que reporta el panel
PID = 0x0126
START = 0x5A
ESC = 0x5B

# El descriptor HID del panel declara, sin ningun item Report ID:
#   Usage Page 0xFF00, Usage 1, Report Size 8, Report Count 0x400 -> Input  (1024 B)
#   Usage Page 0xFF00, Usage 1, Report Size 8, Report Count 0x400 -> Output (1024 B)
# Por eso en Linux (hidraw) el informe son 1024 bytes SIN byte de report ID, mientras que
# la API HID de Windows exige anteponerlo (1025). `canal.py` autodetecta cual acepta.
INFORME_DATOS = 1024          # bytes utiles segun el descriptor HID del panel
INFORME_WINDOWS = 1025        # 0x00 + 1024, lo que exige la API HID de Windows
READ_SIZE = 4096              # lectura holgada: con buffers cortos el kernel da EINVAL

# ------------------------------------------------------------------ informes de medios
MEDIA_START = 0x5C
MEDIA_TIPO_FONDO = 0x02       # capa de fondo: ACUMULA memoria
MEDIA_TIPO_OSD = 0x01         # capa OSD: va ENCIMA y reutiliza su hueco
MEDIA_MENSAJE = 0x13          # valor que usa cougarLCD.cpp (el kit capturo 0x16/0x18)
MEDIA_TROZO = 1000            # bytes de datos por bloque
MEDIA_CABECERA = 24           # cabecera del informe SIN el byte de report ID
MEDIA_CABECERA_CON_ID = 25    # ... y CON el byte de report ID delante
MEDIA_MAX_BLOQUES = 0xFFFF


# ------------------------------------------------------------------ framing
def checksum(payload: bytes, total: int) -> int:
    """(byte_alto + byte_bajo + suma(payload)) & 0xFF."""
    return ((total >> 8) + (total & 0xFF) + sum(payload)) & 0xFF


def _append_escapado(valor: int, salida: bytearray) -> None:
    if valor in (START, ESC):
        salida.append(ESC)
        salida.append(0x01 if valor == START else 0x02)
    else:
        salida.append(valor)


def build_frame(payload: bytes) -> bytes:
    """Trama completa sin relleno: 5A | len(BE16) | payload | checksum | 5A."""
    total = len(payload) + 5
    if total > 0xFFFF:
        raise ValueError(f"el payload no cabe en una trama ({len(payload)} B)")
    trama = bytearray([START])
    _append_escapado((total >> 8) & 0xFF, trama)
    _append_escapado(total & 0xFF, trama)
    for valor in payload:
        _append_escapado(valor, trama)
    _append_escapado(checksum(payload, total), trama)
    trama.append(START)
    return bytes(trama)


@dataclass
class Trama:
    """Una trama decodificada."""
    payload: bytes
    declarado: int
    cable: int
    escapes: int
    checksum_leido: int
    consumidos: int

    @property
    def len_ok(self) -> bool:
        return self.declarado == self.cable

    @property
    def checksum_ok(self) -> bool:
        return checksum(self.payload, self.declarado) == self.checksum_leido

    @property
    def ok(self) -> bool:
        return self.len_ok and self.checksum_ok


def decode_frame(buf: bytes) -> Trama | None:
    """Decodifica la trama que empieza en buf[0]. None si aun falta informacion."""
    if not buf or buf[0] != START:
        return None
    trama = bytearray([START])
    escapes = 0
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
                return None                      # falta el byte de codigo del escape
            codigo = buf[i + 1]
            if codigo not in (0x01, 0x02):
                return None                      # escape invalido: no es una trama
            trama.append(START if codigo == 0x01 else ESC)
            escapes += 1
            i += 2
        else:
            trama.append(valor)
            i += 1
    if not terminada or len(trama) < 5:
        return None
    declarado = (trama[1] << 8) | trama[2]
    return Trama(payload=bytes(trama[3:-2]), declarado=declarado, cable=len(trama),
                 escapes=escapes, checksum_leido=trama[-2], consumidos=i)


# ------------------------------------------------------------------ mensajes
@dataclass
class Respuesta:
    """Respuesta del panel: `code`, cabecera, cuerpo (texto) y campos utiles."""
    code: int | None = None
    ack: int | None = None
    cabecera: str = ""
    cuerpo: str = ""
    crudo: bytes = b""
    checksum_ok: bool = True
    campos: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code == 200

    @property
    def vacia(self) -> bool:
        return not self.cuerpo.strip()

    def json(self):
        try:
            return json.loads(self.cuerpo) if self.cuerpo.strip() else None
        except ValueError:
            return None

    def __repr__(self) -> str:
        return f"<Respuesta {self.code} ack={self.ack} {len(self.cuerpo)} B>"


def parse_respuesta(payload: bytes) -> tuple[int | None, int | None, str, str, dict]:
    """(code, ack, cabecera legible, cuerpo, campos de la cabecera)."""
    cabecera_bytes, _, cuerpo_bytes = payload.partition(b"\r\n\r\n")
    cabecera = cabecera_bytes.decode("ascii", "replace")
    cuerpo = cuerpo_bytes.decode("utf-8", "replace") if cuerpo_bytes else ""
    coincidencia = re.match(r"\s*\d+\s+(\d{3})", cabecera)
    code = int(coincidencia.group(1)) if coincidencia else None
    campos: dict[str, str] = {}
    for linea in cabecera.replace("\r\n", "\n").split("\n"):
        if "=" in linea:
            clave, valor = linea.split("=", 1)
            campos[clave.strip()] = valor.strip()
        elif " " in linea:
            subtipo, _, resto = linea.partition(" ")
            campos.setdefault("_tipo", subtipo.strip())
            if resto.strip().isdigit():
                campos.setdefault("_code", resto.strip())
    ack = int(campos["AckNumber"]) if campos.get("AckNumber", "").isdigit() else None
    return code, ack, cabecera, cuerpo, campos


def build_request(cmd: str, cuerpo=None, metodo: str = "POST", seq: int | None = None,
                  cabeceras: list[tuple[str, str]] | None = None,
                  cuerpo_bruto: bytes | None = None) -> tuple[bytes, int]:
    """Trama de una peticion y su numero de secuencia."""
    if seq is None:
        seq = nueva_secuencia()
    cabecera = f"{metodo} {cmd} 1\r\nSeqNumber={seq}\r\nDate={int(time.time() * 1000)}\r\n"
    for clave, valor in (cabeceras or []):
        cabecera += f"{clave}={valor}\r\n"
    payload = cabecera.encode("ascii")
    if cuerpo_bruto is not None:
        payload += (f"ContentType=json\r\nContentLength={len(cuerpo_bruto)}\r\n\r\n"
                    ).encode("ascii") + cuerpo_bruto
    elif cuerpo is not None:
        bruto = json.dumps(cuerpo, separators=(",", ":")).encode("utf-8")
        payload += (b"ContentType=json\r\n"
                    + f"ContentLength={len(bruto)}\r\n".encode("ascii")
                    + b"\r\n" + bruto)
    else:
        payload += b"\r\n"
    return build_frame(payload), seq


_seq = 0


def nueva_secuencia() -> int:
    """Numero de secuencia siguiente (el panel empareja por AckNumber)."""
    global _seq
    _seq += 1
    return _seq


def reiniciar_secuencia() -> None:
    global _seq
    _seq = 0


# ------------------------------------------------------------------ informes de medios
def build_media_report(indice: int, total_bloques: int, trozo: bytes,
                       tipo: int = MEDIA_TIPO_FONDO,
                       mensaje: int = MEDIA_MENSAJE) -> bytes:
    """Informe de medios SIN relleno: 24 B de cabecera + datos (sin report ID).

    Con el byte de report ID delante (informe de 1025), los datos empiezan en el offset 25;
    sin el (informe de 1024, como en Linux), en el 24. El offset 4 es el contador de la
    transferencia: `cougarLCD.cpp` usa 0x13 fijo y el editor oficial un valor que
    incrementa, asi que lo dejo parametrizable.
    """
    if not 0 <= indice <= MEDIA_MAX_BLOQUES:
        raise ValueError(f"indice de bloque fuera de rango: {indice}")
    if len(trozo) > MEDIA_TROZO:
        raise ValueError(f"el bloque no puede pasar de {MEDIA_TROZO} B (son {len(trozo)})")
    total = 21 + len(trozo)
    cabecera = bytearray(MEDIA_CABECERA)
    cabecera[0] = MEDIA_START
    cabecera[1] = (total >> 8) & 0xFF
    cabecera[2] = total & 0xFF
    cabecera[3] = mensaje & 0xFF
    cabecera[4] = (total_bloques >> 8) & 0xFF
    cabecera[5] = total_bloques & 0xFF
    cabecera[6] = (indice >> 8) & 0xFF
    cabecera[7] = indice & 0xFF
    cabecera[8] = tipo & 0xFF
    return bytes(cabecera) + trozo


def cabecera_media(informe: bytes) -> dict:
    """Descompone un informe de medios (para depurar y para el simulador)."""
    if len(informe) > 1 and informe[0] == 0x00 and informe[1] == MEDIA_START:
        informe = informe[1:]                     # venia con el byte de report ID
    if len(informe) < MEDIA_CABECERA or informe[0] != MEDIA_START:
        return {"valido": False}
    return {
        "valido": True,
        "longitud": (informe[1] << 8) | informe[2],
        "mensaje": informe[3],
        "total_bloques": (informe[4] << 8) | informe[5],
        "indice": (informe[6] << 8) | informe[7],
        "tipo": informe[8],
        "datos": len(informe) - MEDIA_CABECERA,
        "payload": informe[MEDIA_CABECERA:],
    }


def bloques_de(tamano: int, trozo: int = MEDIA_TROZO) -> int:
    """Cuantos bloques ocupa un fichero de `tamano` bytes."""
    return (tamano + trozo - 1) // trozo if tamano > 0 else 0


# ------------------------------------------------------------------ validacion de imagenes
FIRMAS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8", "jpeg"),
          (b"GIF87a", "gif"), (b"GIF89a", "gif"))
NOMBRE_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,127}$")


def tipo_de_imagen(datos: bytes) -> str | None:
    """'png' | 'jpeg' | 'gif' | None. El panel solo muestra PNG (medido por el kit)."""
    for firma, nombre in FIRMAS:
        if datos.startswith(firma):
            return nombre
    return None


def nombre_seguro(nombre: str) -> str:
    """Nombre de fichero que el panel acepta (el editor rechaza el resto).

    Se queda con el nombre base de una ruta, pero **rechaza** cualquier nombre que intente
    salir de su carpeta (`..`), que traiga caracteres raros o que sea demasiado largo: ese
    nombre viaja al panel y acaba siendo el nombre del medio que guarda.
    """
    if not nombre or ".." in nombre:
        raise ValueError(f"nombre de fichero no valido para el panel: {nombre!r}")
    base = nombre.replace("\\", "/").rsplit("/", 1)[-1]
    if not NOMBRE_VALIDO.match(base) or not base.strip("."):
        # "." o ".." pasan la expresion regular pero no son un nombre de fichero
        raise ValueError(f"nombre de fichero no valido para el panel: {nombre!r}")
    return base
