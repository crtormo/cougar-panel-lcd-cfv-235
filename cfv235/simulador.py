"""Panel CFV235 simulado, para desarrollar y probar sin hardware.

Levanta un PTY (`/dev/pts/N`) que se comporta como el panel: contesta a `conn`,
`brightness`, `rotate`, `power`, `displayInSleep`, `realtimeDisplay`, `recovery` y al
protocolo de subida, **con los mismos modos de fallo**:

  * si los bloques llegan mas de `caduca_ms` despues del `transport`, los acusa con
    `1 400 AckNumber=0` (igual que el panel real) y el `transported` contesta 200 con el
    cuerpo VACIO, que es su forma de decir "no aceptado";
  * al recibir todos los bloques a tiempo manda el acuse `1 200 AckNumber=0` (lo que hace
    el panel real y que el kit cfv-235 no esperaba);
  * `--retransmitir` repite respuestas, como hace el panel de vez en cuando;
  * `--tarde-ms` retrasa el primer bloque a proposito, para provocar el fallo.

Ademas reconstruye el fichero subido, para comprobar byte a byte lo que se le mando.

Acepta las dos formas de escribir: con el byte de report ID 0x00 delante (Windows) y sin el
(Linux, que es lo que declara el descriptor del panel).
"""

from __future__ import annotations

import argparse
import json
import os
import select
import sys
import time

from . import protocolo as p

PERFIL = {
    "OS": "Linux",
    "version": {"app": "V1.0.5", "firmware": "V1.0.5", "sdk": "V1.2.7", "hardware": "V2.0"},
    "space": 81756,
    "brightness": 100,
    "degree": 270,
    "sn": "BYZL-SIMULADO-0001",
    "osdState": 0,
    "mode": 0,
    "logo": 2,
    "timeout": 60,
    "bootFinish": 1,
    "background": [],
    "displayInSleep": 0,
    "presetThemeId": 0,
    "sleepClockId": 0,
}


class PanelFalso:
    """Estado y respuestas del panel simulado."""

    def __init__(self, caduca_ms: float = 1000, salida: str | None = None,
                 traza: bool = False, retransmitir: bool = False, tarde_ms: float = 0,
                 boot_finish: int = 1):
        self.caduca_ms = caduca_ms
        self.salida = salida
        self.traza = traza
        self.retransmitir = retransmitir
        self.tarde_ms = tarde_ms
        self.perfil = json.loads(json.dumps(PERFIL))
        self.perfil["bootFinish"] = boot_finish
        self.buffer = bytearray()
        self.medios = self._medios_vacios()
        self.subidas_ok = 0
        self.subidas_fallidas = 0
        self.ultimo_error = ""
        self.ultima_peticion = ""
        self.acuse_enviado = False

    @staticmethod
    def _medios_vacios() -> dict:
        return {"nombre": None, "tamano": 0, "bloques": 0, "recibidos": {},
                "inicio": None, "tipo": None}

    # -------------------------------------------------------------- salida
    def _escribir(self, fd, trama: bytes) -> None:
        try:
            os.write(fd, trama)
            if self.retransmitir:
                os.write(fd, trama)
        except OSError:
            pass

    def respuesta(self, fd, seq: int, code: int, cuerpo=None) -> None:
        texto = f"1 {code}\r\nAckNumber={seq + 1}\r\n"
        if cuerpo is None:
            texto += "ContentLength=0\r\n\r\n"
            self._escribir(fd, p.build_frame(texto.encode("ascii")))
            return
        bruto = json.dumps(cuerpo, separators=(",", ":")).encode("utf-8")
        texto += f"ContentType=json\r\nContentLength={len(bruto)}\r\n\r\n"
        self._escribir(fd, p.build_frame(texto.encode("ascii") + bruto))

    def acuse(self, fd, code: int) -> None:
        """Acuse de los bloques: `1 <code> AckNumber=0`, sin peticion que lo pida."""
        texto = f"1 {code}\r\nAckNumber=0\r\nContentLength=0\r\n\r\n"
        self._escribir(fd, p.build_frame(texto.encode("ascii")))
        self.acuse_enviado = True

    # -------------------------------------------------------------- peticiones
    def atender(self, fd, payload: bytes) -> None:
        texto = payload.decode("utf-8", "replace")
        cabecera, _, cuerpo = texto.partition("\r\n\r\n")
        lineas = cabecera.split("\r\n")
        partes = (lineas[0].split(" ") + ["", ""])[:2]
        metodo, cmd = partes[0], partes[1]
        seq = 0
        for linea in lineas:
            if linea.startswith("SeqNumber="):
                try:
                    seq = int(linea.split("=", 1)[1])
                except ValueError:
                    seq = 0
        try:
            datos = json.loads(cuerpo) if cuerpo.strip() else None
        except ValueError:
            datos = None
        self.ultima_peticion = f"{metodo} {cmd}"
        if self.traza:
            eco = json.dumps(datos, ensure_ascii=False) if datos else ""
            print(f"  <- {metodo} {cmd}  seq={seq}  {eco[:90]}")

        if cmd == "conn":
            self.perfil["background"] = [self.medios["nombre"]] if self.medios["nombre"] else []
            self.respuesta(fd, seq, 200, self.perfil)
        elif cmd == "all":
            self.respuesta(fd, seq, 200, {"state": "success"})
        elif cmd == "power":
            self.respuesta(fd, seq, 200, None)
        elif cmd == "brightness":
            self.perfil["brightness"] = (datos or {}).get("value", self.perfil["brightness"])
            self.respuesta(fd, seq, 200, None)
        elif cmd == "rotate":
            self.perfil["degree"] = (datos or {}).get("degree", self.perfil["degree"])
            self.respuesta(fd, seq, 200, None)
        elif cmd == "displayInSleep":
            self.perfil["displayInSleep"] = 1 if (datos or {}).get("enable") else 0
            self.respuesta(fd, seq, 200, None)
        elif cmd == "realtimeDisplay":
            self.perfil["osdState"] = 1 if (datos or {}).get("enable") else 0
            self.respuesta(fd, seq, 200, None)
        elif cmd == "recovery":
            self.medios = self._medios_vacios()
            self.perfil["background"] = []
            self.perfil["osdState"] = 0
            self.perfil["bootFinish"] = 0
            self.respuesta(fd, seq, 200, None)
            time.sleep(0.2)
            self.perfil["bootFinish"] = 1
        elif cmd == "transport":
            datos = datos or {}
            tamano = int(datos.get("fileSize", 0))
            # `inicio` marca cuando el panel ABRE la sesion: la caducidad se cuenta desde
            # aqui, no desde el primer bloque (era el error del simulador anterior).
            self.medios = {"nombre": datos.get("fileName", "subida.png"), "tamano": tamano,
                           "bloques": p.bloques_de(tamano), "recibidos": {},
                           "inicio": time.time(), "tipo": None}
            self.acuse_enviado = False
            if self.traza:
                print(f"     transport: {self.medios['nombre']} {tamano} B -> "
                      f"{self.medios['bloques']} bloques")
            self.respuesta(fd, seq, 200, {"state": "success", "blockMaxSize": 1024})
        elif cmd == "transported":
            self.cerrar_subida(fd, seq, datos or {})
        else:
            if self.traza:
                print(f"     (comando desconocido: {cmd})")
            self.respuesta(fd, seq, 400, None)

    # -------------------------------------------------------------- medios
    def informe_medio(self, fd, informe: bytes) -> None:
        info = p.cabecera_media(informe)
        if not info["valido"]:
            return
        if self.medios["bloques"] == 0 or self.medios["inicio"] is None:
            return                                   # bloques sin transport (o sesion cerrada)
        if self.tarde_ms and not self.medios["recibidos"]:
            time.sleep(self.tarde_ms / 1000.0)       # retraso a proposito del primer bloque
        retraso = (time.time() - self.medios["inicio"]) * 1000
        if retraso > self.caduca_ms and not self.medios["recibidos"]:
            if self.traza:
                print(f"     bloque fuera de plazo ({retraso:.0f} ms): sesion caducada")
            self.medios["inicio"] = None
            self.acuse(fd, 400)                      # sesion caducada: rechazo
            return
        self.medios["tipo"] = info["tipo"]
        self.medios["recibidos"][info["indice"]] = info["payload"]
        if self.traza:
            capa = "OSD" if info["tipo"] == p.MEDIA_TIPO_OSD else "fondo"
            print(f"     bloque {info['indice'] + 1}/{info['total_bloques']} "
                  f"({info['datos']} B, {capa})")
        if (not self.acuse_enviado and self.medios["bloques"] > 0
                and len(self.medios["recibidos"]) == self.medios["bloques"]):
            self.acuse(fd, 200)                      # todos a tiempo: aceptados

    def cerrar_subida(self, fd, seq: int, datos: dict) -> None:
        completo = (self.medios["inicio"] is not None
                    and self.medios["bloques"] > 0
                    and len(self.medios["recibidos"]) == self.medios["bloques"])
        if not completo:
            self.subidas_fallidas += 1
            self.ultimo_error = (f"bloques {len(self.medios['recibidos'])}/"
                                 f"{self.medios['bloques']} con sesion "
                                 f"{'abierta' if self.medios['inicio'] else 'CADUCADA'}")
            if self.traza:
                print(f"     transported: RECHAZADO ({self.ultimo_error}) -> 200 sin cuerpo")
            self.respuesta(fd, seq, 200, None)
            return
        datos_completos = b"".join(self.medios["recibidos"][i]
                                   for i in sorted(self.medios["recibidos"]))
        if self.salida:
            with open(self.salida, "wb") as fh:
                fh.write(datos_completos)
        if self.medios["tamano"] and len(datos_completos) != self.medios["tamano"]:
            self.subidas_fallidas += 1
            self.ultimo_error = f"tamano {len(datos_completos)} != {self.medios['tamano']}"
            self.respuesta(fd, seq, 200, None)
            return
        self.subidas_ok += 1
        self.perfil["background"] = [self.medios["nombre"]]
        self.perfil["osdState"] = 1 if self.medios["tipo"] == p.MEDIA_TIPO_OSD else 0
        if self.traza:
            print(f"     transported: OK  {self.medios['nombre']} {len(datos_completos)} B"
                  + (f" -> {self.salida}" if self.salida else ""))
        self.respuesta(fd, seq, 200, {"state": "success"})
        self.medios["inicio"] = None


# ------------------------------------------------------------------ bucle del servidor
def extraer_mensaje(panel: PanelFalso):
    """Saca UN mensaje completo del buffer: ("media", informe) | ("trama", payload) | None.

    Se vacia el buffer ANTES de preguntar al descriptor: una sola lectura puede traer varios
    mensajes (el cliente manda la subida entera de golpe) y, si se llama a select() con
    mensajes ya en el buffer, el descriptor no tiene nada nuevo que ofrecer y el resto se
    queda ahi para siempre. Ese era el bloqueo del simulador del kit.
    """
    buffer = panel.buffer
    while buffer and buffer[0] not in (p.START, p.MEDIA_START):
        buffer.pop(0)                                # relleno de ceros y report ID
    if not buffer:
        return None
    if buffer[0] == p.MEDIA_START:
        if len(buffer) < p.MEDIA_CABECERA:
            return None
        longitud = (buffer[1] << 8) | buffer[2]
        total = p.MEDIA_CABECERA + (longitud - 21)
        if len(buffer) < total:
            return None
        informe = bytes(buffer[:total])
        del buffer[:total]
        return ("media", informe)
    trama = p.decode_frame(bytes(buffer))
    if trama is None:
        return None
    del buffer[:trama.consumidos]
    return ("trama", trama.payload)


def servir(fd: int, panel: PanelFalso, parar=None) -> None:
    """Bucle principal: lee del maestro del PTY y contesta.

    `parar` es un `threading.Event` opcional: cuando se activa, el bucle termina de forma
    limpia. Conviene usarlo (y hacer `join`) antes de cerrar el descriptor, porque si el
    hilo sigue vivo mientras el numero de descriptor se reutiliza, acaba leyendo los bytes
    de otro PTY.
    """
    while parar is None or not parar.is_set():
        mensaje = extraer_mensaje(panel)
        if mensaje is not None:
            clase, carga = mensaje
            if clase == "media":
                panel.informe_medio(fd, carga)
            else:
                panel.atender(fd, carga)
            continue
        try:
            listos, _, _ = select.select([fd], [], [], 0.5)
        except (OSError, ValueError):
            break                                    # el PTY se ha cerrado
        if not listos:
            continue
        try:
            trozo = os.read(fd, p.READ_SIZE)
        except OSError:
            break
        if not trozo:
            continue
        panel.buffer += trozo


def abrir_pty():
    """(maestro, esclavo, nombre) de un PTY en modo raw."""
    import pty
    import tty
    maestro, esclavo = pty.openpty()
    try:
        tty.setraw(esclavo)
        tty.setraw(maestro)
    except Exception:                                # noqa: BLE001
        pass
    return maestro, esclavo, os.ttyname(esclavo)


def main() -> int:
    ap = argparse.ArgumentParser(description="Panel CFV235 simulado")
    ap.add_argument("--caduca", type=float, default=1000,
                    help="ms de vida de la sesion de transferencia (el panel real: <1000)")
    ap.add_argument("--tarde-ms", type=float, default=0,
                    help="retrasa a proposito el primer bloque")
    ap.add_argument("--salida", help="escribe aqui lo que se suba, para compararlo")
    ap.add_argument("--traza", action="store_true", help="cuenta todo lo que pasa")
    ap.add_argument("--retransmitir", action="store_true", help="repite cada respuesta")
    ap.add_argument("--sin-arrancar", action="store_true",
                    help="empieza con bootFinish=0 (como el panel atascado)")
    args = ap.parse_args()

    try:
        maestro, esclavo, nombre = abrir_pty()
    except ImportError:
        print("!! este simulador necesita Linux (modulo 'pty')", file=sys.stderr)
        return 2

    print(f"panel simulado en: {nombre}")
    print(f"  caducidad de la sesion: {args.caduca:.0f} ms"
          + (f"   primer bloque retrasado {args.tarde_ms:.0f} ms" if args.tarde_ms else ""))
    print("usalo con:")
    print(f"  python3 -m cfv235.cli --device {nombre} estado")
    print(f"  python3 -m cfv235.cli --device {nombre} subir fondo.png")
    if args.salida:
        print(f"lo que se suba se guardara en {args.salida}")
    print("Ctrl+C para parar\n")

    panel = PanelFalso(args.caduca, args.salida, args.traza, args.retransmitir,
                       args.tarde_ms, boot_finish=0 if args.sin_arrancar else 1)
    try:
        servir(maestro, panel)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(maestro)
        os.close(esclavo)
        print(f"\nsubidas aceptadas: {panel.subidas_ok}   rechazadas: {panel.subidas_fallidas}")
        if panel.ultimo_error:
            print(f"ultimo fallo: {panel.ultimo_error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
