"""Pruebas del panel simulado.

La logica de respuestas se prueba en cualquier sistema (las respuestas se escriben en un
fichero, que es un descriptor valido). La parte del PTY, que es como se conecta el cliente
de verdad, solo se puede probar en Linux y se salta en el resto.

Lo que se comprueba es justo lo que interesa al escribir un editor: que la subida se acepte
cuando los bloques llegan a tiempo y que **falle igual que el panel real** cuando no.
"""

import argparse
import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

from cougar import panel as modulo_panel                                  # noqa: E402
from cougar import patrones, protocolo as p                               # noqa: E402
from cougar import simulador                                              # noqa: E402

FALLOS = []
SALIDA = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-pruebas")
os.makedirs(SALIDA, exist_ok=True)


def comprobar(condicion, mensaje):
    if condicion:
        print(f"   OK   {mensaje}")
    else:
        print(f"   FALLA {mensaje}")
        FALLOS.append(mensaje)


def opciones(**extra):
    base = {"traza": False, "caduca": 1000.0, "salida": None, "retransmitir": False}
    base.update(extra)
    return argparse.Namespace(**base)


class Buzon:
    """Lee las respuestas que el panel simulado escribe en un fichero."""

    def __init__(self, ruta):
        self.ruta = ruta
        # O_BINARY solo existe en Windows: alli, sin el, os.open traduce \n a \r\n y los
        # bytes del protocolo llegan cambiados (en Linux el PTY no traduce nada).
        self.fd = os.open(ruta, os.O_RDWR | os.O_CREAT | os.O_TRUNC
                          | getattr(os, "O_BINARY", 0))
        self.leido = 0

    def nuevas(self):
        """Tramas completas aparecidas desde la ultima llamada."""
        with open(self.ruta, "rb") as fh:
            datos = fh.read()
        trozo = datos[self.leido:]
        self.leido = len(datos)
        salida = []
        i = 0
        while i < len(trozo):
            if trozo[i] != p.START:
                i += 1
                continue
            info = p.decode_frame(trozo[i:])
            if info is None:
                i += 1
                continue
            code, cabecera, cuerpo = p.parse_response(info["payload"])
            salida.append({"code": code, "ack": p.parse_ack(cabecera), "cuerpo": cuerpo,
                           "checksum_ok": info["checksum_ok"]})
            i += info["consumidos"]
        return salida

    def cerrar(self):
        os.close(self.fd)


def payload_de(cmd, cuerpo=None, metodo="POST", seq=1):
    """Payload (sin trama) de una peticion, para darselo al simulador."""
    trama, _ = p.build_request(cmd, cuerpo, metodo, seq=seq)
    return p.decode_frame(trama)["payload"]


# --------------------------------------------------------------------------- 1
print("1) El panel simulado contesta como el de verdad")
buzon = Buzon(os.path.join(SALIDA, "simulador.log"))
falso = simulador.PanelFalso(opciones())

falso.atender(buzon.fd, payload_de("conn"))
nuevas = buzon.nuevas()
comprobar(len(nuevas) == 1 and nuevas[0]["code"] == 200, "conn -> 200")
comprobar(nuevas and '"bootFinish":1' in nuevas[0]["cuerpo"], "y trae las propiedades del panel")
comprobar(nuevas and nuevas[0]["ack"] == 2, "AckNumber = seq + 1, como el panel real")

falso.atender(buzon.fd, payload_de("brightness", {"value": 40}, seq=2))
falso.atender(buzon.fd, payload_de("displayInSleep", {"enable": False}, seq=3))
buzon.nuevas()
comprobar(falso.perfil["brightness"] == 40, "brightness cambia el estado simulado")
comprobar(falso.perfil["displayInSleep"] == 0, "no-dormir queda apuntado")

# --------------------------------------------------------------------------- 2
print("\n2) Subida correcta: se acepta y se reconstruye el fichero")
png = os.path.join(SALIDA, "subida.png")
patrones.generar("esquinas", png)
with open(png, "rb") as fh:
    esperado = fh.read()
reconstruido = os.path.join(SALIDA, "reconstruido.png")
falso = simulador.PanelFalso(opciones(salida=reconstruido))
falso.atender(buzon.fd, payload_de("transport",
                                  {"type": "media", "fileSize": len(esperado),
                                   "fileName": "subida.png"}, seq=10))
nuevas = buzon.nuevas()
comprobar(nuevas and nuevas[0]["code"] == 200 and "blockMaxSize" in nuevas[0]["cuerpo"],
          "transport -> 200 con blockMaxSize")
bloques = p.bloques_de(len(esperado))
for i in range(bloques):
    trozo = esperado[i * p.MEDIA_TROZO:(i + 1) * p.MEDIA_TROZO]
    falso.informe_medio(buzon.fd, p.build_media_report(i, bloques, trozo, p.MEDIA_TIPO_FONDO))
falso.atender(buzon.fd, payload_de("transported", {"md5": "todo", "fileName": "subida.png"},
                                  seq=11))
nuevas = buzon.nuevas()
comprobar(nuevas and '"success"' in nuevas[-1]["cuerpo"],
          f"transported -> {nuevas[-1]['cuerpo'].strip() if nuevas else 'nada'}")
comprobar(falso.subidas_ok == 1 and falso.subidas_fallidas == 0, "la subida se da por buena")
comprobar(os.path.exists(reconstruido) and open(reconstruido, "rb").read() == esperado,
          f"el fichero reconstruido ({bloques} bloques) es identico al original")

# --------------------------------------------------------------------------- 3
print("\n3) Subida tardia: el simulador falla EXACTAMENTE como el panel real")
falso = simulador.PanelFalso(opciones(caduca=1.0))        # sesion de 1 ms
falso.atender(buzon.fd, payload_de("transport",
                                  {"type": "media", "fileSize": len(esperado),
                                   "fileName": "tarde.png"}, seq=20))
buzon.nuevas()
time.sleep(0.05)                                          # llega tarde a proposito
falso.informe_medio(buzon.fd, p.build_media_report(0, 1, esperado[:1000], p.MEDIA_TIPO_FONDO))
nuevas = buzon.nuevas()
comprobar(any(n["code"] == 400 and n["ack"] == 0 for n in nuevas),
          "el bloque tarde se acusa con 1 400 AckNumber=0")
falso.atender(buzon.fd, payload_de("transported", {"md5": "todo", "fileName": "tarde.png"},
                                  seq=21))
nuevas = buzon.nuevas()
comprobar(nuevas and nuevas[-1]["code"] == 200 and not nuevas[-1]["cuerpo"].strip(),
          "transported contesta 200 CON EL CUERPO VACIO (el sintoma del panel real)")
comprobar(falso.subidas_fallidas == 1, "la subida se cuenta como fallida")

# --------------------------------------------------------------------------- 4
print("\n4) recovery reinicia y vacia los medios")
falso = simulador.PanelFalso(opciones())
falso.medios["nombre"] = "algo.png"
falso.atender(buzon.fd, payload_de("recovery", {"enable": True}, seq=30))
buzon.nuevas()
comprobar(falso.medios["nombre"] is None and falso.perfil["osdState"] == 0,
          "recovery borra los medios y deja osdState en 0")

# --------------------------------------------------------------------------- 5
print("\n5) De punta a punta por PTY (solo Linux)")
try:
    import pty
    import threading
    import tty
except ImportError:
    print("   (no hay Linux: se salta esta parte. El kit se ha probado en el resto.)")
else:
    maestro, esclavo = pty.openpty()
    tty.setraw(esclavo)
    tty.setraw(maestro)
    nombre = os.ttyname(esclavo)
    falso = simulador.PanelFalso(opciones(salida=os.path.join(SALIDA, "pty.png")))
    hilo = threading.Thread(target=simulador.servir, args=(maestro, falso), daemon=True)
    hilo.start()
    try:
        with modulo_panel.Panel(dispositivo=nombre, timeout=3.0) as panel:
            comprobar(panel.propiedades().get("bootFinish") == 1, "el cliente habla con el PTY")
            panel.subir(png, capa="osd")
            comprobar(falso.subidas_ok == 1, "la subida por PTY se acepta")
            comprobar(open(os.path.join(SALIDA, "pty.png"), "rb").read() == esperado,
                      "y el fichero recibido es identico al enviado")
    except Exception as exc:                              # noqa: BLE001
        comprobar(False, f"PTY: {exc}")
    finally:
        os.close(maestro)
        os.close(esclavo)

buzon.cerrar()
print("")
if FALLOS:
    print(f"{len(FALLOS)} comprobaciones FALLIDAS")
    for fallo in FALLOS:
        print(f"  - {fallo}")
    sys.exit(1)
print("todas las comprobaciones del simulador han pasado")
