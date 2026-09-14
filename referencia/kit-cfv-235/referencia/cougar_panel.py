#!/usr/bin/env python3
"""
Cliente minimo para el panel LCD COUGAR CFV235 (9.16" 1920x462) desde Linux,
hablando HID directo, sin el COUGAR LCD Editor.

Protocolo reconstruido a partir del log del editor 1.0.14.
Documentacion completa: cougar-cfv235-protocol.md

Uso rapido:
    sudo python3 cougar_panel.py --list                 # localizar el panel
    sudo python3 cougar_panel.py conn                   # handshake + propiedades
    sudo python3 cougar_panel.py power                  # encender / despertar
    sudo python3 cougar_panel.py brightness 60          # 0..100
    sudo python3 cougar_panel.py rotate 270             # 0/90/180/270
    sudo python3 cougar_panel.py state --demo           # enviar datos en vivo
    sudo python3 cougar_panel.py transport fondo.png    # handshake de fichero
    sudo python3 cougar_panel.py listen                 # solo escuchar

Ingenieria inversa (modo raw, mensajes arbitrarios):
    # desatascar el panel (equivale al boton Reset del editor)
    sudo python3 cougar_panel.py raw POST recovery

    # listar los medios del panel
    sudo python3 cougar_panel.py raw GET waterBlockScreen

    # borrar un fichero por su ruta
    sudo python3 cougar_panel.py raw DELETE mediaDelete --body '{"path":"/xxx"}'
    sudo python3 cougar_panel.py raw DELETE mediaDelete --header FileName=grande.png

    # bloque de subida (cabeceras del protocolo de ficheros)
    sudo python3 cougar_panel.py raw POST transport --header FileName=f.png \
        --header FileBlockId=1 --header FileSize=3744 --header ContentRange=0-3743 \
        --body-file bloque.bin

Comandos conocidos del protocolo:
    conn  waterBlockScreen  waterBlockScreenId  brightness  rotate  recovery
    sysinfoDisplay  displayInSleep  fanLCDSet  mediaDelete  config  power
    realtimeDisplay
Metodos: GET | POST | STATE | DELETE
Cabeceras usadas por el editor: SeqNumber AckNumber ContentLength ContentType
    FileName FileBlockId FileSize ContentRange Counter Option

Permisos: hace falta acceso de lectura/escritura al /dev/hidrawN.
O usa sudo, o crea /etc/udev/rules.d/60-cougar-lcd.rules con:
    KERNEL=="hidraw*", ATTRS{idVendor}=="1d6b", ATTRS{idProduct}=="0126", MODE="0666"
y luego: sudo udevadm control --reload-rules && sudo udevadm trigger
"""

import argparse
import glob
import json
import os
import re
import struct
import sys
import time

VID = 0x1D6B          # VID de Linux Foundation, es lo que reporta el panel
PID = 0x0126
START = 0x5A
ESC = 0x5B            # prefijo de escape dentro del payload (0x5A -> 5B 01, 0x5B -> 5B 02)
READ_SIZE = 4096      # tamano del buffer de lectura (los informes llegan de 1024 B)
REPORT_SIZE = 1025     # informe HID completo: byte de report ID 0x00 + 1024 de datos

_seq = 0
_report_size = REPORT_SIZE
_exact_write = False   # True = una sola escritura 0x00+trama, sin relleno (--exacto)
_prefix = True         # anteponer el byte de report ID 0x00 (--sin-prefijo lo quita)
_envios = 0            # peticiones enviadas (para distinguir retransmisiones)
_ultima = None         # ultima trama aceptada
_envios_ultima = -1    # valor de _envios cuando llego esa trama


# --------------------------------------------------------------------------- #
# Localizacion del dispositivo
# --------------------------------------------------------------------------- #
def _hid_id(path):
    """Devuelve (vid, pid) del nodo hidraw leyendo sysfs."""
    base = os.path.basename(path)
    uevent = f"/sys/class/hidraw/{base}/device/uevent"
    try:
        with open(uevent) as fh:
            for line in fh:
                if line.startswith("HID_ID="):
                    _, vid, pid = line.strip().split("=")[1].split(":")
                    return int(vid, 16), int(pid, 16)
    except OSError:
        pass
    return None, None


def list_devices():
    out = []
    for path in sorted(glob.glob("/dev/hidraw*")):
        vid, pid = _hid_id(path)
        out.append((path, vid, pid))
    return out


def find_device(vid=VID, pid=PID):
    for path, v, p in list_devices():
        if v == vid and p == pid:
            return path
    return None


# --------------------------------------------------------------------------- #
# Trama
#
# Modelo VERIFICADO contra el panel y confirmado por la implementacion
# independiente cougarLCD.cpp (docs/PROTOCOL.md) para este mismo modelo:
#
#   cable : 5A | len(BE16) | payload_escapado | checksum | 5A | ceros de relleno
#   informe: 1 byte de report ID (0x00) + 1024 bytes de datos = 1025 B
#
#   escape   : 0x5A -> 5B 01   y   0x5B -> 5B 02
#              Se aplica AL PAYLOAD, A LOS DOS BYTES DE LONGITUD Y AL CHECKSUM.
#   len      : payload_sin_escapar + 5. OJO: no cuenta los bytes de codigo que
#              inserta el escape, asi que la trama del cable es MAS LARGA que len
#              cuando hay escapes (medido: len=371, cable=373).
#   checksum : (byte_alto + byte_bajo + suma(payload_sin_escapar)) & 0xFF
#              Se suman los DOS BYTES de longitud, no el valor `len`.
#
# Por que la regla simple parecia correcta: con len < 256 (todas las tramas cortas,
# incluidas las del log del editor, 336/336 muestras) sumar los dos bytes equivale
# a sumar len. Con len=371 -> 1+115=116, mientras que 371 mod 256 = 115: de ahi el
# desfase de exactamente 1 que se midio en la respuesta de propiedades.
#
# Y por que el parser se desincronizaba: cortar la trama en `len` falla en cuanto
# hay un escape (el numero de serie BYZL..., cuya 'Z' es 0x5A, basta), y la respuesta
# entera se pierde en silencio. El fin de trama hay que detectarlo por el 0x5A de
# cierre, porque el payload nunca contiene un 0x5A crudo.
# --------------------------------------------------------------------------- #
def checksum(payload: bytes, total: int) -> int:
    """checksum = (byte_alto + byte_bajo + suma(payload)) & 0xFF

    Confirmado por la implementacion independiente cougarLCD.cpp (docs/PROTOCOL.md)
    y coherente con lo medido en este panel. OJO: se suman los DOS BYTES de
    longitud, NO el valor `total`. Para tramas de menos de 256 B coincide con la
    regla simple (sum(payload) + total) & 0xFF, que es la que se verifico en
    336/336 muestras del log del editor; para tramas largas difiere, y esa
    diferencia es exactamente el desfase de 1 que se midio en la respuesta de
    propiedades: total=371 -> bytes 1 y 115 -> 116, mientras que 371 mod 256 = 115.
    """
    return ((total >> 8) + (total & 0xFF) + sum(payload)) & 0xFF


def append_escaped(value: int, out: bytearray) -> None:
    """Escribe un byte aplicando el escape del protocolo (0x5A -> 5B 01, 0x5B -> 5B 02)."""
    if value in (0x5A, 0x5B):
        out.append(ESC)
        out.append(0x01 if value == 0x5A else 0x02)
    else:
        out.append(value)


def build_frame(payload: bytes) -> bytes:
    """Trama completa sin relleno: 5A | len(BE16) | payload | checksum | 5A.

    El escape se aplica TAMBIEN a los dos bytes de longitud y al checksum, no solo
    al payload: asi lo hace el firmware y la implementacion de referencia.
    """
    total = len(payload) + 5
    frame = bytearray()
    frame.append(START)
    append_escaped((total >> 8) & 0xFF, frame)
    append_escaped(total & 0xFF, frame)
    for value in payload:
        append_escaped(value, frame)
    append_escaped(checksum(payload, total), frame)
    frame.append(START)
    return bytes(frame)


def decode_frame(buf: bytes):
    """Decodifica la trama que empieza en buf[0]; None si aun falta informacion.

    El fin de trama se detecta por el 0x5A de CIERRE, no por el campo de longitud:
    `len` no cuenta los bytes de codigo que inserta el escape, asi que la trama del
    cable es mas larga que `len` cuando hay escapes (len=371 frente a 373 en el
    cable). Un parser que corte en `len` se desincroniza y pierde la respuesta
    entera; el payload nunca contiene un 0x5A crudo, asi que el cierre es fiable.
    """
    if not buf or buf[0] != START:
        return None
    frame = bytearray([START])
    escapes = []
    i = 1
    terminada = False
    while i < len(buf):
        value = buf[i]
        if value == START:
            frame.append(value)
            i += 1
            terminada = True
            break
        if value == ESC:
            if i + 1 >= len(buf):
                return None                      # falta el byte de codigo
            code = buf[i + 1]
            if code not in (0x01, 0x02):
                return None                      # escape invalido
            frame.append(0x5A if code == 0x01 else 0x5B)
            escapes.append(0x5A if code == 0x01 else 0x5B)
            i += 2
        else:
            frame.append(value)
            i += 1
    if not terminada or len(frame) < 5:
        return None
    declarado = (frame[1] << 8) | frame[2]
    payload = bytes(frame[3:-2])
    return {
        "payload": payload,
        "len": declarado,
        "len_ok": declarado == len(frame),
        "checksum": frame[-2],
        "checksum_ok": checksum(payload, declarado) == frame[-2],
        "trailer_ok": frame[-1] == START,
        "escapes": escapes,
        "consumidos": i,
    }


def build_request(cmd, body=None, method="POST", seq=None,
                  extra_headers=None, raw_body=None):
    """Construye la trama de una peticion.

    body=None       -> sin ContentType/ContentLength (p. ej. conn)
    raw_body=bytes  -> cuerpo binario tal cual (bloques de fichero)
    extra_headers   -> lista de (clave, valor) insertados tras Date, p. ej.
                       [("FileName","f.png"), ("FileBlockId","1"),
                        ("FileSize","3744"), ("ContentRange","0-3743")]
    """
    global _seq
    if seq is None:
        _seq += 1
        seq = _seq
    head = f"{method} {cmd} 1\r\nSeqNumber={seq}\r\nDate={int(time.time() * 1000)}\r\n"
    for key, value in (extra_headers or []):
        head += f"{key}={value}\r\n"
    payload = head.encode("ascii")
    if raw_body is not None:
        payload += f"ContentType=json\r\nContentLength={len(raw_body)}\r\n\r\n".encode("ascii")
        payload += raw_body
    elif body is not None:
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        payload += b"ContentType=json\r\n" + f"ContentLength={len(raw)}\r\n".encode("ascii")
        payload += b"\r\n" + raw
    else:
        payload += b"\r\n"
    return build_frame(payload), seq


def parse_frame(frame: bytes):
    """Valida y descompone una trama completa -> (payload, checksum_ok)."""
    info = decode_frame(frame)
    if info is None:
        return b"", False
    return info["payload"], (info["checksum_ok"] and info["trailer_ok"] and info["len_ok"])


def parse_response(payload: bytes):
    """Respuesta -> (code, cabecera, body_texto)."""
    parts = payload.split(b"\r\n\r\n", 1)
    head = parts[0].decode("ascii", "replace").replace("\r\n", " | ")
    body = parts[1].decode("utf-8", "replace") if len(parts) > 1 else ""
    m = re.match(r"\s*\d+\s+(\d{3})", head)
    return (int(m.group(1)) if m else None), head, body


# --------------------------------------------------------------------------- #
# E/S HID
# --------------------------------------------------------------------------- #
def send_frame(fd, frame: bytes):
    """Trocea la trama en reports con el byte de report ID 0x00 delante.

    Con --exacto se hace UNA sola escritura 0x00+trama, sin relleno: es la forma que usa
    el editor en Windows para los frames pequenos y la que se verifico contra el panel.
    Medido: el tamano del report NO decide si el panel responde (54 B exactos, 64 B y
    1024 B dieron 3/3 respuestas cada uno).
    """
    global _envios
    _envios += 1
    prefix = b"\x00" if _prefix else b""
    if _exact_write:
        os.write(fd, prefix + frame)
        return
    usable = _report_size - len(prefix)
    for i in range(0, len(frame), usable):
        chunk = frame[i:i + usable]
        report = prefix + chunk + b"\x00" * (usable - len(chunk))
        os.write(fd, report)


def read_report(fd, timeout=3.0):
    """Lee un report con timeout, o None.

    Se pide un buffer HOLGADO (4096 B): los informes del panel llegan de 1024 B y con
    un buffer corto el kernel puede devolver EINVAL en vez de recortar.
    """
    import select
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    try:
        return os.read(fd, READ_SIZE)
    except (BlockingIOError, OSError):
        return None


def read_frame(fd, timeout=3.0, verbose=False):
    """Acumula reports hasta tener una trama completa (consciente de los escapes)."""
    buf = bytearray()
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = read_report(fd, max(0.05, deadline - time.time()))
        if chunk is None:
            continue
        if verbose:
            print(f"    rx report ({len(chunk)} B): {chunk[:16].hex()}"
                  f"{'...' if len(chunk) > 16 else ''}")
        buf += chunk
        while buf and buf[0] != START:
            buf.pop(0)
        info = decode_frame(bytes(buf))
        if info is None:
            continue
        frame = bytes(buf[:info["consumidos"]])
        del buf[:info["consumidos"]]
        if verbose and info["escapes"]:
            print(f"    (trama con {len(info['escapes'])} escapes: "
                  f"len={info['len']} cable={info['consumidos']})")
        # El panel RETRANSMITE respuestas identicas (medido: el mismo frame
        # "1 400 AckNumber=1" llego dos veces con 18 s de diferencia). Solo se
        # descarta si no se ha enviado nada nuevo desde que llego: asi una
        # segunda peticion identica sigue teniendo su propia respuesta.
        global _ultima, _envios_ultima
        if frame == _ultima and _envios == _envios_ultima:
            if verbose:
                print("    (retransmision descartada)")
            continue
        _ultima, _envios_ultima = frame, _envios
        return frame
    return None


def exchange(fd, cmd, body=None, method="POST", timeout=3.0, verbose=False,
             quiet=False, extra_headers=None, raw_body=None):
    """Envia una peticion y devuelve (code, cabecera, body)."""
    frame, seq = build_request(cmd, body, method,
                               extra_headers=extra_headers, raw_body=raw_body)
    if verbose:
        print(f"--> {method} {cmd}  seq={seq}  trama={len(frame)} B")
        print(f"    {frame.hex()}")
    send_frame(fd, frame)
    rframe = read_frame(fd, timeout, verbose)
    if rframe is None:
        print("!! sin respuesta (timeout)", file=sys.stderr)
        return None, "", ""
    payload, ok = parse_frame(rframe)
    code, head, rbody = parse_response(payload)
    if not quiet:
        info = decode_frame(rframe)
        detalle = ""
        if info and info["escapes"]:
            detalle = (f"  [len={info['len']} cable={len(rframe)} "
                       f"escapes={len(info['escapes'])}]")
        if not ok:
            print("!! checksum de la respuesta NO cuadra", file=sys.stderr)
        print(f"<-- code={code}{detalle}   {head}")
    return code, head, rbody


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #
def cmd_conn(fd, args):
    code, head, body = exchange(fd, "conn", None, verbose=args.verbose)
    if body:
        try:
            props = json.loads(body)
            print(json.dumps(props, indent=2, ensure_ascii=False))
            if "bootFinish" in props:
                bf = props["bootFinish"]
                print(f"\n>>> bootFinish = {bf}  "
                      f"({'listo' if bf == 1 else 'PANEL NO ARRANCADO: no atendra ordenes'})")
            if "space" in props:
                print(f">>> espacio libre = {props['space']} KB")
        except json.JSONDecodeError:
            print(body)
    return 0


def cmd_power(fd, args):
    code, _, _ = exchange(fd, "power", {"event": "resume"}, verbose=args.verbose)
    return 0 if code == 200 else 1


def cmd_brightness(fd, args):
    code, _, _ = exchange(fd, "brightness", {"value": int(args.value)}, verbose=args.verbose)
    return 0 if code == 200 else 1


def cmd_rotate(fd, args):
    code, _, _ = exchange(fd, "rotate", {"degree": int(args.degree)}, verbose=args.verbose)
    return 0 if code == 200 else 1


DEMO_STATE = {
    "network": {"upload": 0, "download": 1},
    "memory": {"total": 32675, "used": 11028, "load": 33, "temperature": 0, "speed": 1065},
    "cpu": {"load": 11, "temperature": 62, "speedAverage": 3775,
            "power": 46, "voltage": 1.273, "usage": 8},
    "gpu": {"load": 3, "temperature": 29, "fan": 0, "speed": 76, "power": 0, "voltage": 0.664},
    "disk": {"total": 465, "used": 50, "load": 10, "activity": 0, "temperature": 0,
             "readSpeed": 0, "writeSpeed": 0},
    "fans": [{"onBoard": True, "type": "Pump", "name": "Fan AIO Pump", "value": 2566},
             {"onBoard": True, "type": "Fan", "name": "Fan CPU", "value": 1323},
             {"onBoard": True, "type": "Chassis", "name": "Fan Chassis3", "value": 897}],
    "motherboard": {"temperature": 25},
    "timestamp": int(time.time() * 1000),
}


def cmd_state(fd, args):
    if args.file:
        with open(args.file) as fh:
            state = json.load(fh)
    else:
        state = dict(DEMO_STATE)
        state["timestamp"] = int(time.time() * 1000)
    code, _, _ = exchange(fd, "all", state, method="STATE", verbose=args.verbose)
    return 0 if code == 200 else 1


MEDIA_START = 0x5C         # delimitador de los informes de medios (no llevan checksum)
MEDIA_TYPE = 0x02          # [9] tipo de medio: 0x02 imagen de fondo, 0x01 capa OSD
MEDIA_MSG = 0x16           # [4] contador de la transferencia. Capturado del editor: 0x16 al
                           # subir un fondo, 0x18 una capa OSD, 0x13 en otras. Va cambiando
                           # entre transferencias; el panel no parece exigir un valor concreto
MEDIA_CHUNK = 1000         # bytes de datos por bloque (1000 = 1025 - 25, encaja justo)
MEDIA_HEADER = 24          # cabecera del cuerpo del informe, SIN el byte de report ID
                           # (la referencia cuenta 25 porque incluye ese byte 0x00)


def build_media_report(index: int, total_blocks: int, chunk: bytes) -> bytes:
    """Cuerpo del informe de medios (24 B de cabecera + datos), tal como lo define el firmware.

    Estructura sobre el informe completo (con el byte de report ID en la posicion 0):
        [0]=0x00 ID | [1]=0x5C | [2..3]=longitud BE | [4]=0x13 | [5..6]=n bloques
        [7..8]=indice | [9]=0x02 | [10..24]=ceros | [25...]=datos
    La longitud que se escribe es 21 + datos: cuenta desde el byte 0x13 hasta el
    ultimo byte de la cabecera, mas los datos.
    """
    head = bytearray(MEDIA_HEADER)
    head[0] = MEDIA_START
    total = 21 + len(chunk)
    head[1] = (total >> 8) & 0xFF
    head[2] = total & 0xFF
    head[3] = MEDIA_MSG
    head[4] = (total_blocks >> 8) & 0xFF
    head[5] = total_blocks & 0xFF
    head[6] = (index >> 8) & 0xFF
    head[7] = index & 0xFF
    head[8] = MEDIA_TYPE
    return bytes(head) + chunk


def send_media_report(fd, cuerpo: bytes):
    """Escribe un informe de medios TAL CUAL, sin relleno.

    A diferencia de las tramas de control, el informe de medios se escribe con su tamano
    exacto (el editor manda 1025 B para los bloques llenos y 769 B para el ultimo, sin
    rellenar). Se antepone el byte de report ID 0x00.
    """
    _envios += 1
    os.write(fd, (b"\x00" if _prefix else b"") + cuerpo)


def cmd_no_dormir(fd, args):
    """Deja el panel encendido sin necesidad de flujo (o vuelve a permitir que se apague).

    Medido: con `displayInSleep: 0` el panel sigue con `brightness: 100` despues de mas de 6
    minutos sin ningun trafico; con el valor por defecto (`1`) la pantalla se apaga y el panel
    reporta `brightness: 0`. Es lo que permite dejar una imagen fija sin ningun bucle.
    """
    dormir = bool(getattr(args, "dormir", False))
    code, _, body = exchange(fd, "displayInSleep", {"enable": dormir},
                             timeout=args.timeout, verbose=args.verbose)
    if code != 200:
        print(f"!! el panel no acepto displayInSleep (code={code})")
        return 1
    print("apagado por espera: " + ("PERMITIDO (se apagara sin flujo)" if dormir
                                    else "DESACTIVADO (se queda encendido sin flujo)"))
    return 0


def subir_archivo(fd, ruta, timeout=10.0, quiet=False, verbose=False):
    """Sube un PNG al panel. Devuelve 0 si lo acepto, 1 si no, 2 si el fichero no vale.

    Protocolo capturado del propio editor (parcheando su node-hid con el inspector de
    Electron, 2026-09-13) y verificado en hardware: el panel adopta la imagen como fondo.

    ⚠️ LO CRITICO ES EL TIEMPO: el panel abre una sesion de transferencia al contestar al
    `transport` y **caduca en menos de un segundo**. El editor manda los bloques 87 ms
    despues y el `transported` 6 ms despues. Si se espera (nosotros esperabamos segundos),
    los bloques se rechazan con acuse 400 y el `transported` devuelve cuerpo vacio.

    `exchange` devuelve en cuanto llega la respuesta, asi que la sincronizacion es correcta
    siempre que no se meta ninguna pausa entre el handshake, los bloques y el cierre.
    """
    with open(ruta, "rb") as fh:
        data = fh.read()
    # El editor acepta .png, .jpg, .mp4 y .gif. Aqui se admiten las tres imagenes que el panel
    # muestra (el GIF puede ser animado; el mp4 queda por explorar).
    es_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    es_jpeg = data[:2] == b"\xff\xd8"
    es_gif = data[:6] in (b"GIF87a", b"GIF89a")
    if not (es_png or es_jpeg or es_gif):
        print("!! el fichero no es PNG, JPEG ni GIF "
              f"(empieza por {data[:8].hex()})", file=sys.stderr)
        return 2
    name = os.path.basename(ruta)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,127}", name):
        print("!! nombre no valido: solo A-Z a-z 0-9 . _ - y hasta 127 caracteres",
              file=sys.stderr)
        return 2
    blocks = (len(data) + MEDIA_CHUNK - 1) // MEDIA_CHUNK
    if blocks > 0xFFFF:
        print("!! demasiado grande para el protocolo (>65535 bloques)", file=sys.stderr)
        return 2
    if not quiet:
        print(f"fichero: {name}  {len(data)} B  ->  {blocks} bloques de {MEDIA_CHUNK} B")

    # Fase 1: anunciar el fichero. El panel debe contestar 200 con "blockMaxSize".
    code, _, body = exchange(fd, "transport",
                             {"type": "media", "fileSize": len(data), "fileName": name},
                             timeout=timeout, verbose=verbose)
    if code != 200 or "blockMaxSize" not in body:
        print("!! el panel rechazo el handshake 'transport' "
              f"(code={code}). Se esperaba 200 con \"blockMaxSize\":1024.")
        print("   Causas tipicas: el panel no ha terminado de arrancar "
              "(mira 'conn': bootFinish debe ser 1), no queda espacio, o hay otra "
              "aplicacion usando el panel.")
        return 1
    if not quiet:
        print(f"handshake OK: {body.strip()}")

    # Fase 2: los bloques, SIN PAUSA. Un informe por bloque, delimitador 0x5C, sin checksum
    # ni escapes, sin relleno (el ultimo va corto), datos a partir del byte 25 del informe.
    for index in range(blocks):
        chunk = data[index * MEDIA_CHUNK:(index + 1) * MEDIA_CHUNK]
        send_media_report(fd, build_media_report(index, blocks, chunk))
        if not quiet:
            print(f"  bloque {index + 1}/{blocks}", end="\r", flush=True)
    if not quiet:
        print(f"  {blocks} bloques enviados          ")

    # Fase 3: confirmar, tambien SIN PAUSA (el editor lo hace 6 ms despues del ultimo
    # bloque). No se espera el acuse de los bloques: el editor tampoco lo espera, y si no
    # llega se perderia la ventana de la sesion.
    code, _, body = exchange(fd, "transported",
                             {"md5": "todo", "fileName": name},
                             timeout=timeout, verbose=verbose)
    if code != 200:
        print(f"!! la confirmacion 'transported' devolvio code={code}")
        return 1
    if '"success"' in body:
        if not quiet:
            print(f"subida confirmada: {body.strip()}")
    else:
        # Este firmware puede contestar 200 con ContentLength=0 cuando NO ha aceptado la
        # subida (el fichero no queda como fondo). Con la subida buena devuelve
        # {"state":"success"}.
        print("!! 'transported' devolvio 200 pero SIN cuerpo: el panel no acepto la subida.")
        print("   Suele ser por enviar los bloques demasiado tarde (la sesion caduca) o por")
        print("   un nombre de fichero que el firmware rechaza.")
        return 1
    return 0


def cmd_subir(fd, args):
    """Subida manual de un PNG (sube y ademas aplica recovery+rotate, como el editor)."""
    resultado = subir_archivo(fd, args.path, timeout=args.timeout,
                              quiet=args.quiet, verbose=args.verbose)
    if resultado != 0:
        return resultado
    # secuencia que usa el editor tras subir el fondo del tema (ojo: recovery REINICIA el
    # panel, asi que solo se hace si se pide explicitamente)
    if getattr(args, "aplicar", False):
        exchange(fd, "recovery", {"enable": True}, timeout=args.timeout)
        exchange(fd, "rotate", {"degree": 270}, timeout=args.timeout)
    return 0


def usar_capa_osd():
    """Pasa los envios a la CAPA OSD (0x01) en vez del fondo (0x02).

    Medido en el panel: la capa OSD **reutiliza su hueco** (-16 KB en 6 subidas) mientras que
    el fondo **acumula** (~1 MB en 6 subidas, con liberacion posterior). Por eso los modos
    continuos (bucle, stream) deben ir como OSD: se puede emitir durante horas sin llenar la
    memoria del panel. Es tambien lo que hace el editor: sube la capa OSD ~2 veces por segundo.
    """
    global MEDIA_TYPE
    MEDIA_TYPE = 0x01


def avisar_si_hay_osd(fd, timeout=4.0):
    """Avisa si la capa OSD esta activa, porque se dibujaria ENCIMA de lo que subamos.

    El panel compone dos capas: fondo (`[9]=0x02`) y OSD (`[9]=0x01`, encima). El editor
    enciende la OSD al aplicar un tema (`osdState: 1`); si subimos una imagen de pantalla
    completa como fondo, la capa OSD anterior se sigue viendo por encima. `recovery` la apaga.
    """
    code, _, body = exchange(fd, "conn", None, timeout=timeout, quiet=True)
    if code != 200 or not body:
        return
    m = re.search(r'"osdState":(\d+)', body)
    if m and m.group(1) == "1":
        print("!! AVISO: osdState=1 -> hay una capa OSD activa y se vera ENCIMA de tu imagen.")
        print("   Para dejarlo limpio:  cougar-panel recovery   (Reset: borra los medios y")
        print("   deja osdState=0) y despues sube la imagen o arranca el bucle.")


def cmd_bucle(fd, args):
    """Dashboard propio: renderiza el PNG y lo sube en bucle.

    Con `--tema archivo.json` usa el MOTOR DE WIDGETS (carpeta `widgets/`) y dibuja el tema
    definido ahi; sin `--tema` usa `panel_dashboard.py` (dashboard fijo). Entre fotogramas
    manda `STATE all` para que el panel no se apague.
    """
    fuentes = None
    motor = None
    tema = None
    if args.tema:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "widgets"))
            import widgets as motor                      # noqa: N813
            from fuentes import Fuentes
        except ImportError as exc:
            print(f"!! no puedo cargar el motor de widgets: {exc}", file=sys.stderr)
            print("   Se busca la carpeta 'widgets' (widgets.py + fuentes.py) junto al cliente.",
                  file=sys.stderr)
            return 2
        with open(args.tema, encoding="utf-8") as fh:
            tema = json.load(fh)
        print(f"tema: {args.tema}  ({len(tema.get('widgets', []))} widgets)")
        fuentes = Fuentes()
        fuentes.muestra()                                # prepara deltas e historial
    else:
        try:
            import panel_dashboard
        except ImportError as exc:
            print(f"!! falta panel_dashboard.py o sus dependencias: {exc}", file=sys.stderr)
            print("   En Debian/Ubuntu:  sudo apt install python3-pil", file=sys.stderr)
            return 2

    avisar_si_hay_osd(fd)
    usar_capa_osd()                                      # no llenar la memoria del panel
    # y que no se duerma aunque el flujo se interrumpa
    exchange(fd, "displayInSleep", {"enable": False}, timeout=args.timeout, quiet=True)
    print(f"bucle del dashboard: cada {args.periodo}s"
          + (f", {args.repeticiones} fotogramas" if args.repeticiones else ", sin limite"))
    exchange(fd, "power", {"event": "resume"}, timeout=args.timeout, quiet=True)
    exchange(fd, "brightness", {"value": 100}, timeout=args.timeout, quiet=True)
    # El panel se apaga sin flujo: se manda telemetria cada segundo mientras dure el bucle.
    demo = dict(DEMO_STATE)

    n = 0
    while not args.repeticiones or n < args.repeticiones:
        n += 1
        t0 = time.time()
        try:
            if tema is not None:
                fuentes.muestra()                        # nueva muestra (alimenta las graficas)
                motor.renderizar(tema, args.png, fuentes)
            else:
                panel_dashboard.renderizar(args.png)
        except Exception as exc:                       # noqa: BLE001 (informe claro)
            print(f"!! el render fallo: {exc}", file=sys.stderr)
            return 1
        resultado = subir_archivo(fd, args.png, timeout=args.timeout, quiet=True,
                                  verbose=args.verbose)
        gastado = time.time() - t0
        print(f"fotograma {n}: " + ("subido y aceptado" if resultado == 0 else "FALLO")
              + f"  ({gastado * 1000:.0f} ms)")
        fin = t0 + args.periodo
        k = 0
        while time.time() < fin:
            k += 1
            estado = dict(demo)
            estado["timestamp"] = int(time.time() * 1000)
            estado["cpu"] = dict(demo["cpu"], load=8 + (k % 40))
            exchange(fd, "all", estado, method="STATE", quiet=True, timeout=2.0)
            time.sleep(max(0.05, min(1.0, fin - time.time())))
    return 0


def cmd_transport(fd, args):
    size = os.path.getsize(args.path)
    name = os.path.basename(args.path)
    print(f"solo handshake: anunciar {name} ({size} B)")
    code, _, body = exchange(fd, "transport",
                             {"type": "media", "fileSize": size, "fileName": name},
                             timeout=args.timeout, verbose=args.verbose)
    if code != 200:
        print("\nEl panel rechazo el handshake (400). Con 'subir <png>' se hace el "
              "protocolo completo.")
        return 1
    print(f"\nHandshake 200: {body.strip()}")
    print("Los bloques van en informes de medios: usa 'subir <png>'.")
    return 0


def cmd_raw(fd, args):
    """Envia un mensaje arbitrario: la herramienta de ingenieria inversa."""
    body = None
    raw_body = None
    if args.body_file:
        with open(args.body_file, "rb") as fh:
            raw_body = fh.read()
        print(f"body binario: {len(raw_body)} B desde {args.body_file}")
    elif args.body:
        body = json.loads(args.body)
    headers = []
    for item in (args.header or []):
        if "=" not in item:
            print(f"cabecera invalida (falta '='): {item}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        headers.append((key, value))
    if headers:
        print("cabeceras extra: " + ", ".join(f"{k}={v}" for k, v in headers))
    code, head, rbody = exchange(fd, args.cmd, body, method=args.method,
                                timeout=args.timeout, verbose=args.verbose,
                                extra_headers=headers, raw_body=raw_body)
    if rbody:
        print(f"body ({len(rbody)} B):")
        try:
            print(json.dumps(json.loads(rbody), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(rbody)
    return 0 if code == 200 else 1


def cmd_listen(fd, args):
    print("escuchando... (Ctrl+C para salir)")
    while True:
        frame = read_frame(fd, 5.0, args.verbose)
        if frame is None:
            continue
        payload, ok = parse_frame(frame)
        code, head, body = parse_response(payload)
        print(f"[{time.strftime('%H:%M:%S')}] {len(frame)} B  "
              f"checksum={'ok' if ok else 'MAL'}  code={code}  {head}"
              + (f"  body={body[:200]}" if body else ""))


# --------------------------------------------------------------------------- #
def main():
    global _report_size, _exact_write, _prefix
    ap = argparse.ArgumentParser(description="Cliente HID del panel COUGAR CFV235")
    ap.add_argument("--device", help="/dev/hidrawN (por defecto: autodetecta)")
    ap.add_argument("--vid", default=hex(VID))
    ap.add_argument("--pid", default=hex(PID))
    ap.add_argument("--report-size", type=int, default=REPORT_SIZE,
                    help="tamano del informe de salida, con el byte de report ID (1025)")
    ap.add_argument("--exacto", action="store_true",
                    help="una sola escritura 0x00+trama, sin relleno (lo que hace el editor)")
    ap.add_argument("--sin-prefijo", action="store_true",
                    help="no anteponer el byte de report ID 0x00 (si el kernel da EINVAL)")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--list", action="store_true", help="listar dispositivos HID")
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("conn")
    sub.add_parser("power")
    p = sub.add_parser("brightness"); p.add_argument("value")
    p = sub.add_parser("rotate"); p.add_argument("degree")
    p = sub.add_parser("state")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--file")
    p = sub.add_parser("transport"); p.add_argument("path")
    p.add_argument("--timeout", type=float, default=6.0)
    p = sub.add_parser("subir", help="subir un PNG al panel (protocolo de medios)")
    p.add_argument("path")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--quiet", "-q", action="store_true")
    p.add_argument("--aplicar", action="store_true",
                   help="tras subir, mandar recovery + rotate (como el editor; REINICIA el panel)")
    p = sub.add_parser("no-dormir", help="que el panel no se apague aunque no reciba flujo")
    p.add_argument("--dormir", action="store_true",
                   help="volver a permitir el apagado por espera")
    p.add_argument("--timeout", type=float, default=6.0)
    p = sub.add_parser("bucle", help="dashboard propio: renderizar y subir en bucle")
    p.add_argument("--periodo", type=float, default=5.0, help="segundos entre fotogramas")
    p.add_argument("--repeticiones", type=int, default=0, help="0 = sin limite")
    p.add_argument("--png", default="/tmp/cougar-dashboard.png")
    p.add_argument("--tema", help="tema JSON del motor de widgets (carpeta widgets/)")
    p.add_argument("--timeout", type=float, default=10.0)
    sub.add_parser("listen")

    p = sub.add_parser("raw", help="enviar un mensaje arbitrario (ingenieria inversa)")
    p.add_argument("method", help="GET | POST | STATE | DELETE")
    p.add_argument("cmd", help="comando a enviar")
    p.add_argument("--body", help="body JSON, p. ej. '{\"value\":50}'")
    p.add_argument("--body-file", help="body binario leido de un fichero")
    p.add_argument("--header", action="append",
                   help="cabecera extra K=V (repetible), p. ej. FileName=x.png")
    p.add_argument("--timeout", type=float, default=3.0)

    args = ap.parse_args()
    _report_size = args.report_size
    _exact_write = args.exacto
    _prefix = not args.sin_prefijo

    if args.list:
        for path, v, p in list_devices():
            mark = "  <-- PANEL COUGAR" if (v == VID and p == PID) else ""
            print(f"{path}  VID={v:#06x} PID={p:#06x}{mark}")
        return 0

    if not args.command:
        ap.print_help()
        return 1

    dev = args.device or find_device(int(args.vid, 16), int(args.pid, 16))
    if not dev:
        print(f"No encuentro el panel ({args.vid}/{args.pid}). Prueba --list.", file=sys.stderr)
        return 1
    print(f"dispositivo: {dev}")

    try:
        fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    except OSError as exc:
        print(f"No puedo abrir {dev}: {exc}. Prueba con sudo o una regla udev.",
              file=sys.stderr)
        return 1

    try:
        handler = {
            "conn": cmd_conn, "power": cmd_power, "brightness": cmd_brightness,
            "rotate": cmd_rotate, "state": cmd_state, "transport": cmd_transport,
            "subir": cmd_subir, "bucle": cmd_bucle, "raw": cmd_raw, "listen": cmd_listen,
            "no-dormir": cmd_no_dormir,
        }[args.command]
        return handler(fd, args)
    except KeyboardInterrupt:
        return 130
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
