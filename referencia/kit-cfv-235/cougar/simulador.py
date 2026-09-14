"""Panel CFV235 **falso**, para desarrollar el editor sin la pantalla delante.

Levanta un PTY (que en Linux es un fichero como cualquier otro) y se comporta como el
panel: contesta a `conn`, `STATE all`, `power`, `brightness`, `rotate`, `recovery`,
`displayInSleep`, y sobre todo al protocolo de subida, con **los mismos modos de fallo**
que el de verdad:

  * si los bloques llegan mas de `--caduca` milisegundos despues del `transport`, los acusa
    con `1 400 AckNumber=0` (igual que el panel real);
  * si la subida no cuadra, `transported` contesta **200 con el cuerpo vacio**;
  * `--tarde-ms` retrasa a proposito el primer bloque, para probar ese error;
  * `--retransmitir` manda respuestas repetidas, como hace el panel de vez en cuando.

Y ademas reconstruye el fichero que le hayas subido, para que puedas comprobar byte a byte
que tu editor genera el PNG que crees (`--salida subido.png`).

    python3 -m cougar.simulador --traza --salida subido.png &
    python3 herramientas/cougar --device /dev/pts/3 conn
    python3 herramientas/cougar --device /dev/pts/3 subir fondo.png

Solo funciona en Linux (necesita `pty`). No sustituye a probar con el panel de verdad: el
simulador lo escribi leyendo el protocolo medido, y el panel tiene sus manias.
"""

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
    "space": 81388,
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
    """Estado del panel simulado."""

    def __init__(self, opciones):
        self.opciones = opciones
        self.perfil = json.loads(json.dumps(PERFIL))
        self.secuencia = 0
        self.buffer = bytearray()
        self.medios = {"nombre": None, "tamano": 0, "bloques": 0, "recibidos": {},
                       "inicio": None, "tipo": None}
        self.ultimo_error = ""
        self.subidas_ok = 0
        self.subidas_fallidas = 0
        self.traza = opciones.traza

    # ------------------------------------------------------------------ salida
    def respuesta(self, fd, seq, code, cuerpo=None, retransmitir=False):
        """Escribe una respuesta con la forma exacta del panel: `1 <code>` + AckNumber."""
        texto = f"1 {code}\r\nAckNumber={seq + 1}\r\n"
        if cuerpo is None:
            texto += "ContentLength=0\r\n\r\n"
        else:
            bruto = json.dumps(cuerpo, separators=(",", ":")).encode("utf-8")
            texto += f"ContentType=json\r\nContentLength={len(bruto)}\r\n\r\n"
            datos = texto.encode("ascii") + bruto
            trama = p.build_frame(datos)
            os.write(fd, trama)
            if retransmitir:
                os.write(fd, trama)                    # el panel a veces repite
            return
        trama = p.build_frame(texto.encode("ascii"))
        os.write(fd, trama)
        if retransmitir:
            os.write(fd, trama)

    def aviso(self, fd, code=400):
        """Aviso no pedido, con AckNumber=0 (el panel hace esto de vez en cuando)."""
        texto = f"1 {code}\r\nAckNumber=0\r\nContentLength=0\r\n\r\n"
        os.write(fd, p.build_frame(texto.encode("ascii")))

    # ------------------------------------------------------------------ peticiones
    def atender(self, fd, payload):
        """payload = cabecera + cuerpo de una peticion ya decodificada."""
        texto = payload.decode("utf-8", "replace")
        cabecera, _, cuerpo = texto.partition("\r\n\r\n")
        lineas = cabecera.split("\r\n")
        metodo, cmd = (lineas[0].split(" ") + ["", ""])[:2]
        seq = 0
        for linea in lineas:
            if linea.startswith("SeqNumber="):
                seq = int(linea.split("=", 1)[1])
        try:
            datos = json.loads(cuerpo) if cuerpo.strip() else None
        except ValueError:
            datos = None
        if self.traza:
            eco = json.dumps(datos, ensure_ascii=False) if datos else ""
            print(f"  <- {metodo} {cmd}  seq={seq}  {eco[:100]}")

        if cmd == "conn":
            self.perfil["background"] = [self.medios["nombre"]] if self.medios["nombre"] else []
            self.respuesta(fd, seq, 200, self.perfil, self.opciones.retransmitir)
            return

        if cmd == "all":                                # telemetria del PC
            self.respuesta(fd, seq, 200, {"state": "success"}, self.opciones.retransmitir)
            return

        if cmd == "power":
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            return

        if cmd == "brightness":
            self.perfil["brightness"] = (datos or {}).get("value", self.perfil["brightness"])
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            return

        if cmd == "rotate":
            self.perfil["degree"] = (datos or {}).get("degree", self.perfil["degree"])
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            return

        if cmd == "displayInSleep":
            self.perfil["displayInSleep"] = 1 if (datos or {}).get("enable") else 0
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            return

        if cmd == "realtimeDisplay":
            self.perfil["osdState"] = 1 if (datos or {}).get("enable") else 0
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            return

        if cmd == "recovery":                           # Reset: reinicia y borra medios
            self.medios = {"nombre": None, "tamano": 0, "bloques": 0, "recibidos": {},
                           "inicio": None, "tipo": None}
            self.perfil["background"] = []
            self.perfil["osdState"] = 0
            self.perfil["bootFinish"] = 0               # arranca de nuevo...
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            time.sleep(0.4)
            self.perfil["bootFinish"] = 1
            return

        if cmd == "transport":
            datos = datos or {}
            self.medios = {"nombre": datos.get("fileName", "subida.png"),
                           "tamano": int(datos.get("fileSize", 0)),
                           "bloques": p.bloques_de(int(datos.get("fileSize", 0))),
                           "recibidos": {}, "inicio": time.time(), "tipo": None}
            if self.opciones.traza:
                print(f"     transport: {self.medios['nombre']} "
                      f"{self.medios['tamano']} B -> {self.medios['bloques']} bloques")
            cuerpo = {"state": "success", "blockMaxSize": 1024}
            self.respuesta(fd, seq, 200, cuerpo, self.opciones.retransmitir)
            return

        if cmd == "transported":
            self.cerrar_subida(fd, seq, datos or {})
            return

        if self.opciones.traza:
            print(f"     (comando desconocido: {cmd})")
        self.respuesta(fd, seq, 400, None, self.opciones.retransmitir)

    # ------------------------------------------------------------------ medios
    def informe_medio(self, fd, informe):
        """Un informe de medios (0x5C). Aqui esta la gracia del simulador."""
        # Admite tanto el informe pelado como el que lleva delante el byte de report ID.
        if len(informe) > 1 and informe[0] == 0x00 and informe[1] == p.MEDIA_START:
            informe = informe[1:]
        info = p.cabecera_media(informe)
        if not info["valido"]:
            return
        if self.medios["inicio"] is None:
            return                                       # bloques sin transport: se ignoran
        retraso_ms = (time.time() - self.medios["inicio"]) * 1000
        if retraso_ms > self.opciones.caduca and not self.medios["recibidos"]:
            # Igual que el panel real: la sesion ha caducado y lo acusa asi.
            if self.opciones.traza:
                print(f"     bloque fuera de plazo ({retraso_ms:.0f} ms): sesion caducada")
            self.medios["inicio"] = None
            self.aviso(fd, 400)
            return
        self.medios["tipo"] = info["tipo"]
        self.medios["recibidos"][info["indice"]] = informe[p.MEDIA_CABECERA:]
        if self.opciones.traza:
            capa = "OSD" if info["tipo"] == p.MEDIA_TIPO_OSD else "fondo"
            print(f"     bloque {info['indice'] + 1}/{info['total_bloques']} "
                  f"({info['datos']} B, {capa})")

    def cerrar_subida(self, fd, seq, datos):
        """`transported`: acepta o contesta 200 con el cuerpo vacio, como el panel."""
        completo = (self.medios["inicio"] is not None
                    and len(self.medios["recibidos"]) == self.medios["bloques"]
                    and self.medios["bloques"] > 0)
        if not completo:
            self.subidas_fallidas += 1
            self.ultimo_error = (f"bloques {len(self.medios['recibidos'])}/"
                                 f"{self.medios['bloques']} con sesion "
                                 f"{'abierta' if self.medios['inicio'] else 'CADUCADA'}")
            if self.opciones.traza:
                print(f"     transported: RECHAZADO ({self.ultimo_error}) -> 200 sin cuerpo")
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            return

        # Reconstruye el fichero, para que puedas compararlo con el original.
        datos_completos = b"".join(self.medios["recibidos"][i]
                                   for i in sorted(self.medios["recibidos"]))
        if self.opciones.salida:
            with open(self.opciones.salida, "wb") as fh:
                fh.write(datos_completos)
        esperado = self.medios["tamano"]
        if esperado and len(datos_completos) != esperado:
            self.subidas_fallidas += 1
            self.ultimo_error = f"tamano {len(datos_completos)} != {esperado} anunciado"
            self.respuesta(fd, seq, 200, None, self.opciones.retransmitir)
            return

        firma = datos_completos[:8]
        valido = (firma[:8] == b"\x89PNG\r\n\x1a\n" or firma[:2] == b"\xff\xd8"
                  or datos_completos[:6] in (b"GIF87a", b"GIF89a"))
        self.subidas_ok += 1
        self.perfil["background"] = [self.medios["nombre"]]
        self.perfil["osdState"] = 1 if self.medios["tipo"] == p.MEDIA_TIPO_OSD else 0
        if self.opciones.traza:
            print(f"     transported: OK  {self.medios['nombre']} "
                  f"{len(datos_completos)} B"
                  + ("" if valido else "  (AVISO: no parece PNG/JPEG/GIF)")
                  + (f" -> guardado en {self.opciones.salida}" if self.opciones.salida else ""))
        self.respuesta(fd, seq, 200, {"state": "success"}, self.opciones.retransmitir)
        self.medios["inicio"] = None


def servir(fd, panel):
    """Bucle principal: lee del maestro del PTY y contesta."""
    panel.falso_fd = fd
    while True:
        listos, _, _ = select.select([fd], [], [], 0.5)
        if listos:
            try:
                trozo = os.read(fd, p.READ_SIZE)
            except OSError:
                break
            if not trozo:
                continue
            panel.buffer += trozo
            while panel.buffer and panel.buffer[0] != p.START and panel.buffer[0] != p.MEDIA_START:
                panel.buffer.pop(0)
            if panel.buffer and panel.buffer[0] == p.MEDIA_START:
                # Un informe de medios: 24 B de cabecera + hasta 1000 de datos.
                # (el byte de report ID 0x00 ya lo ha quitado el bucle de arriba)
                if len(panel.buffer) < p.MEDIA_CABECERA:
                    continue
                longitud = (panel.buffer[1] << 8) | panel.buffer[2]
                total = p.MEDIA_CABECERA + (longitud - 21)   # cabecera + datos
                if len(panel.buffer) < total:
                    continue
                informe = bytes(panel.buffer[:total])
                del panel.buffer[:total]
                panel.informe_medio(fd, informe)
                continue
            info = p.decode_frame(bytes(panel.buffer))
            if info is None:
                continue
            trama = bytes(panel.buffer[:info["consumidos"]])
            del panel.buffer[:info["consumidos"]]
            panel.atender(fd, info["payload"])


def main():
    ap = argparse.ArgumentParser(description="Panel CFV235 simulado (para desarrollar sin hardware)")
    ap.add_argument("--caduca", type=float, default=1000,
                    help="ms de vida de la sesion de transferencia (el panel real: <1000)")
    ap.add_argument("--tarde-ms", type=float, default=0,
                    help="retrasa a proposito el primer bloque (para probar el fallo)")
    ap.add_argument("--salida", help="escribe aqui el fichero que se suba (para comparar)")
    ap.add_argument("--traza", action="store_true", help="cuenta todo lo que pasa")
    ap.add_argument("--retransmitir", action="store_true",
                    help="repite cada respuesta, como hace el panel a veces")
    args = ap.parse_args()

    try:
        import pty
    except ImportError:
        print("!! este simulador necesita Linux (modulo 'pty')", file=sys.stderr)
        return 2

    maestro, esclavo = pty.openpty()
    # Modo raw OBLIGATORIO: si no, la disciplina de linea del PTY traduce 0x0D/0x0A y
    # manglea los bytes binarios del protocolo.
    try:
        import tty
        tty.setraw(esclavo)
        tty.setraw(maestro)
    except Exception as exc:                              # noqa: BLE001
        print(f"aviso: no pude poner el PTY en modo raw ({exc})")
    nombre = os.ttyname(esclavo)
    print(f"panel simulado en: {nombre}")
    print(f"  caducidad de la sesion: {args.caduca:.0f} ms"
          + (f"   primer bloque retrasado {args.tarde_ms:.0f} ms" if args.tarde_ms else ""))
    print("usalo con:")
    print(f"  python3 herramientas/cougar --device {nombre} conn")
    print(f"  python3 herramientas/cougar --device {nombre} subir fondo.png")
    if args.salida:
        print(f"lo que se suba se guardara en {args.salida}")
    print("Ctrl+C para parar\n")

    panel = PanelFalso(args)
    if args.tarde_ms:
        original = panel.informe_medio

        def con_retraso(fd, informe):
            if not panel.medios["recibidos"]:
                time.sleep(args.tarde_ms / 1000.0)
            return original(fd, informe)

        panel.informe_medio = con_retraso

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
