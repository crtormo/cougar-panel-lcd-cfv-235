"""Backend del editor visual. Este es el punto de partida para tu propio editor.

Es el mismo servidor que ya funcionaba (`widgets/editor.py` del proyecto original, con los
endpoints intactos para que `editor.html` siga sirviendo tal cual), pasado a la biblioteca
y con dos endpoints nuevos pensados para quien escriba su propio front:

    GET  /                 el editor (cougar/editor.html)
    GET  /estado           valores en vivo, tema actual, nombres de fuente, estado del bucle
    GET  /catalogo         tipos de widget con sus campos, fuentes y marcadores  [NUEVO]
    POST /guardar          {"tema": {...}}            -> guarda el JSON en disco
    POST /validar          {"tema": {...}}            -> lista de problemas        [NUEVO]
    POST /aplicar          {"tema": {...}}            -> dibuja y sube al panel
    POST /bucle            {"periodo":2} | {"parar":true}  -> tema animado
    POST /tema             {"ruta": "/ruta/tema.json"}     -> carga un tema de disco
    POST /plantilla        {}                              -> tema de ejemplo

Si no hay panel conectado, todo funciona igual menos `/aplicar` y `/bucle`, que avisan del
motivo: se puede disenar el tema sin tener la pantalla delante.

    python3 -m cougar.editor --puerto 8777 --abrir
"""

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import fuentes as modulo_fuentes
from . import panel as modulo_panel
from . import temas
from . import widgets as motor

AQUI = os.path.dirname(os.path.abspath(__file__))
PNG_POR_DEFECTO = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-editor.png")
TEMA_POR_DEFECTO = os.path.join(
    os.path.dirname(AQUI), "ejemplos", "tema_dashboard.json")

ESTADO = {
    "tema": None,
    "ruta_tema": TEMA_POR_DEFECTO,
    "fuentes": None,
    "png": PNG_POR_DEFECTO,
    "bucle": None,
    "ultimo": "",
    "panel": None,
}


# --------------------------------------------------------------------------- panel
def abrir_panel():
    """Panel compartido por todas las peticiones, o None si no hay ninguno conectado."""
    if ESTADO["panel"] is not None and ESTADO["panel"].fd is not None:
        return ESTADO["panel"]
    try:
        ESTADO["panel"] = modulo_panel.Panel().abrir()
    except (FileNotFoundError, PermissionError) as exc:
        return str(exc)
    return ESTADO["panel"]


def _subir_al_panel(png):
    """(ok, detalle). Nunca lanza: devuelve el motivo si el panel no esta."""
    panel = abrir_panel()
    if isinstance(panel, str):
        return False, f"sin panel ({panel})"
    try:
        respuesta = panel.subir(png, capa="osd")     # capa OSD: reutiliza hueco
        return True, f"code={respuesta.code} {respuesta.cuerpo.strip()[:60]}"
    except Exception as exc:                          # noqa: BLE001 (informe claro)
        return False, str(exc)


def _fuentes():
    if ESTADO["fuentes"] is None:
        ESTADO["fuentes"] = modulo_fuentes.Fuentes()
    ESTADO["fuentes"].muestra()
    return ESTADO["fuentes"]


# --------------------------------------------------------------------------- bucle
def _bucle_trabajador(periodo, repeticiones):
    n = 0
    while not ESTADO["bucle"]["parar"].is_set():
        if repeticiones and n >= repeticiones:
            break
        n += 1
        try:
            fuentes = _fuentes()
            motor.renderizar(ESTADO["tema"], ESTADO["png"], fuentes)
            ok, detalle = _subir_al_panel(ESTADO["png"])
            ESTADO["ultimo"] = f"bucle {n}: {'OK' if ok else 'FALLO'} ({detalle})"
        except Exception as exc:                       # noqa: BLE001
            ESTADO["ultimo"] = f"bucle {n}: error {exc}"
        if ESTADO["bucle"]["parar"].wait(periodo):
            break
    ESTADO["bucle"] = None


# --------------------------------------------------------------------------- HTTP
class Manejador(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):            # silencio en consola
        pass

    def _json(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _leer_cuerpo(self):
        largo = int(self.headers.get("Content-Length", 0))
        if not largo:
            return {}
        try:
            return json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(AQUI, "editor.html"), "rb") as fh:
                cuerpo = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return

        if self.path == "/estado":
            muestra = _fuentes().muestra()
            self._json({
                "valores": muestra["valores"],
                "tema": ESTADO["tema"],
                "ruta": ESTADO["ruta_tema"],
                "nombres_editor": sorted(modulo_fuentes.MAPA_EDITOR.keys()),
                "bucle": bool(ESTADO["bucle"]),
                "ultimo": ESTADO["ultimo"],
                "png": ESTADO["png"],
            })
            return

        if self.path == "/catalogo":
            panel = abrir_panel()
            self._json({
                "panel": panel if isinstance(panel, str) else panel.dispositivo,
                "ancho": temas.ANCHO, "alto": temas.ALTO,
                "tipos": temas.catalogo_json(),
                "alias": temas.ALIAS_TIPOS,
                "ignorados": temas.CAMPOS_DEL_EDITOR,
                "fuentes": temas.fuentes_disponibles(instanciar=False),
                "marcadores": temas.marcadores()["forma"],
                "tema_por_defecto": temas.tema_por_defecto(),
            })
            return

        self._json({"error": "ruta desconocida"}, 404)

    def do_POST(self):
        datos = self._leer_cuerpo()

        if self.path == "/guardar":
            tema = datos.get("tema")
            if not tema:
                self._json({"ok": False, "error": "falta el tema"}, 400)
                return
            ESTADO["tema"] = tema
            temas.guardar(ESTADO["ruta_tema"], tema)
            self._json({"ok": True, "ruta": ESTADO["ruta_tema"],
                        "problemas": temas.validar(tema)})
            return

        if self.path == "/validar":
            tema = datos.get("tema") or ESTADO["tema"]
            problemas = temas.validar(tema, estricto=bool(datos.get("estricto")))
            self._json({"ok": not problemas, "problemas": problemas})
            return

        if self.path == "/aplicar":
            if datos.get("tema"):
                ESTADO["tema"] = datos["tema"]
            problemas = temas.validar(ESTADO["tema"])
            try:
                motor.renderizar(ESTADO["tema"], ESTADO["png"], _fuentes())
            except Exception as exc:                  # noqa: BLE001
                self._json({"ok": False, "error": f"el render fallo: {exc}",
                            "problemas": problemas}, 500)
                return
            ok, detalle = _subir_al_panel(ESTADO["png"])
            ESTADO["ultimo"] = f"aplicar: {'OK' if ok else 'FALLO'} ({detalle})"
            self._json({"ok": ok, "detalle": detalle, "png": ESTADO["png"],
                        "problemas": problemas})
            return

        if self.path == "/bucle":
            if datos.get("parar"):
                if ESTADO["bucle"]:
                    ESTADO["bucle"]["parar"].set()
                self._json({"ok": True, "bucle": False})
                return
            if ESTADO["bucle"]:
                self._json({"ok": True, "bucle": True, "nota": "ya estaba en marcha"})
                return
            if datos.get("tema"):
                ESTADO["tema"] = datos["tema"]
            periodo = float(datos.get("periodo", 2))
            repeticiones = int(datos.get("repeticiones", 0))
            ESTADO["bucle"] = {"parar": threading.Event()}
            threading.Thread(target=_bucle_trabajador, args=(periodo, repeticiones),
                             daemon=True).start()
            self._json({"ok": True, "bucle": True, "periodo": periodo})
            return

        if self.path == "/tema":
            ruta = datos.get("ruta") or ESTADO["ruta_tema"]
            try:
                with open(ruta, encoding="utf-8") as fh:
                    ESTADO["tema"] = json.load(fh)
                ESTADO["ruta_tema"] = ruta
                self._json({"ok": True, "tema": ESTADO["tema"], "ruta": ruta})
            except (OSError, ValueError) as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
            return

        if self.path == "/plantilla":
            ESTADO["tema"] = temas.tema_por_defecto()
            self._json({"ok": True, "tema": ESTADO["tema"]})
            return

        self._json({"error": "ruta desconocida"}, 404)


# --------------------------------------------------------------------------- arranque
def main():
    ap = argparse.ArgumentParser(description="Editor visual de temas del panel CFV235")
    ap.add_argument("--puerto", type=int, default=8777)
    ap.add_argument("--tema", default=TEMA_POR_DEFECTO, help="tema JSON de partida")
    ap.add_argument("--abrir", action="store_true", help="abrir el navegador al arrancar")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    ESTADO["ruta_tema"] = args.tema
    ESTADO["tema"] = temas.cargar(args.tema)
    print(f"tema: {args.tema}  ({len(ESTADO['tema'].get('widgets', []))} widgets)")

    panel = abrir_panel()
    if isinstance(panel, str):
        print(f"aviso: {panel}")
        print("       el editor funciona igual: puedes disenar, guardar y validar;")
        print("       solo fallaran 'Aplicar' y 'Bucle'.")
    else:
        print(f"panel: {panel.dispositivo}")
        if panel.avisar_si_hay_osd():
            print("aviso: osdState=1 -> hay una capa OSD encima; usa Reset antes de subir")

    direccion = f"http://{args.host}:{args.puerto}/"
    print(f"editor en {direccion}   (Ctrl+C para salir)")
    servidor = ThreadingHTTPServer((args.host, args.puerto), Manejador)
    if args.abrir:
        threading.Timer(0.4, lambda: webbrowser.open(direccion)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\ncerrando...")
    finally:
        servidor.server_close()
        if ESTADO["bucle"]:
            ESTADO["bucle"]["parar"].set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
