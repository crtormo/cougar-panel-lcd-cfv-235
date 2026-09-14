#!/usr/bin/env python3
"""cougar — herramienta de linea de ordenes del panel CFV235 (usa el paquete `cougar`).

    cougar listar                       ver los hidraw y cual es el panel
    cougar conn                         versiones, espacio, brillo, capas (JSON)
    cougar estado                       mandar telemetria de ejemplo (STATE all)
    cougar no-dormir                    que la pantalla no se apague sin trafico
    cougar subir fondo.png              subir una imagen (capa de fondo)
    cougar subir fondo.png --osd        subirla a la capa OSD
    cougar patron rejilla --osd         generar un patron de prueba y subirlo
    cougar tema ejemplos/tema_dashboard.json --subir
    cougar bucle --tema ejemplos/tema_dashboard.json --periodo 2
    cougar raw POST transport --body '{"type":"media","fileSize":100,"fileName":"a.png"}'
    cougar escuchar                     mostrar todo lo que manda el panel

Con `--device /dev/pts/N` se puede apuntar al panel simulado (`python3 -m cougar.simulador`).
"""

import argparse
import json
import os
import sys
import time

from . import panel as modulo_panel
from . import patrones, temas


def mostrar(respuesta, etiqueta):
    if respuesta is None or respuesta.code is None:
        print(f"   {etiqueta}: SIN RESPUESTA")
        return False
    extra = "" if respuesta.checksum_ok else " (CHECKSUM MAL)"
    print(f"   {etiqueta}: {respuesta.code}{extra}  {respuesta.cabecera}")
    if respuesta.cuerpo.strip():
        print("   " + respuesta.cuerpo.strip().replace("\n", "\n   "))
    return respuesta.code == 200


def cmd_listar(_panel, _args):
    encontrado = False
    for ruta, vid, pid in modulo_panel.listar_dispositivos():
        marca = ""
        if vid == modulo_panel.p.VID and pid == modulo_panel.p.PID:
            marca, encontrado = "   <-- PANEL COUGAR", True
        print(f"{ruta}  {vid if vid is None else hex(vid)}:{pid if pid is None else hex(pid)}{marca}")
    if not encontrado:
        print("no aparece el panel (1d6b:0126): mira el cable de datos y 'lsusb'")
    return 0


def cmd_conn(panel, _args):
    propiedades = panel.propiedades()
    if not propiedades:
        print("sin respuesta al saludo (POST conn)")
        return 1
    print(json.dumps(propiedades, indent=2, ensure_ascii=False))
    boot = propiedades.get("bootFinish")
    print(f"\n>>> bootFinish = {boot}  "
          f"({'listo' if boot == 1 else 'PANEL NO ARRANCADO: no atendera ordenes'})")
    if "space" in propiedades:
        print(f">>> espacio libre = {propiedades['space']} KB")
    if propiedades.get("osdState") == 1:
        print(">>> AVISO: osdState=1 -> hay una capa OSD que se dibuja ENCIMA; "
              "usa 'cougar recovery' antes de subir")
    return 0


def cmd_estado(panel, args):
    datos = None
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            datos = json.load(fh)
    return 0 if mostrar(panel.telemetria(datos), "state") else 1


def cmd_power(panel, args):
    return 0 if mostrar(panel.power(args.evento), "power") else 1


def cmd_brillo(panel, args):
    return 0 if mostrar(panel.brillo(args.valor), "brightness") else 1


def cmd_girar(panel, args):
    return 0 if mostrar(panel.girar(args.grados), "rotate") else 1


def cmd_recovery(panel, _args):
    print("recovery REINICIA el panel y borra los medios que tenga")
    return 0 if mostrar(panel.recovery(), "recovery") else 1


def cmd_no_dormir(panel, args):
    respuesta = panel.no_dormir(activo=not args.dormir)
    if not mostrar(respuesta, "displayInSleep"):
        return 1
    print("apagado por espera: " + ("PERMITIDO (se apagara sin flujo)" if args.dormir
                                    else "DESACTIVADO (se queda encendido sin flujo)"))
    return 0


def cmd_subir(panel, args):
    capa = "osd" if args.osd else "fondo"
    try:
        respuesta = panel.subir(args.ruta, capa=capa, verboso=True)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    print(f"subida confirmada (capa {capa}): {respuesta.cuerpo.strip()}")
    return 0


def cmd_patron(panel, args):
    carpeta = args.carpeta or os.environ.get("TMPDIR", "/tmp")
    ruta = os.path.join(carpeta, f"patron-{args.nombre}.png")
    patrones.generar(args.nombre, ruta, etiquetas=args.etiquetas, lado=args.lado)
    print(f"patron generado: {ruta} ({os.path.getsize(ruta)} B)")
    if args.solo_generar:
        return 0
    try:
        respuesta = panel.subir(ruta, capa="osd" if args.osd else "fondo")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    print(f"subido: {respuesta.cuerpo.strip()}")
    return 0


def cmd_tema(panel, args):
    tema = temas.cargar(args.ruta)
    problemas = temas.validar(tema)
    print(f"tema: {args.ruta}  ({len(tema.get('widgets', []))} widgets)")
    for problema in problemas:
        print(f"   ! {problema}")
    salida = args.png or os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-tema.png")
    temas.renderizar(tema, salida)
    print(f"PNG: {salida} ({os.path.getsize(salida)} B)")
    if not args.subir:
        return 1 if problemas and args.estricto else 0
    return cmd_subir(panel, argparse.Namespace(ruta=salida, osd=args.osd))


def cmd_bucle(panel, args):
    tema = temas.cargar(args.tema) if args.tema else None
    panel.no_dormir()
    print(f"bucle cada {args.periodo}s"
          + (f", {args.repeticiones} fotogramas" if args.repeticiones else ", sin limite"))
    salida = args.png or os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-bucle.png")
    n = 0
    try:
        while not args.repeticiones or n < args.repeticiones:
            n += 1
            inicio = time.time()
            if tema is not None:
                temas.renderizar(tema, salida)
            else:
                patrones.generar("rejilla", salida)
            try:
                respuesta = panel.subir(salida, capa="osd")
                estado = f"OK {respuesta.code}"
            except (OSError, ValueError, RuntimeError) as exc:
                estado = f"FALLO ({exc})"
            print(f"fotograma {n}: {estado}  ({(time.time() - inicio) * 1000:.0f} ms)")
            # el panel se apaga sin flujo: telemetria cada segundo mientras dura el bucle
            fin = inicio + args.periodo
            while time.time() < fin:
                panel.telemetria()
                time.sleep(max(0.05, min(1.0, fin - time.time())))
    except KeyboardInterrupt:
        print("\ncortado")
    return 0


def cmd_raw(panel, args):
    cuerpo = None
    bruto = None
    if args.body_file:
        with open(args.body_file, "rb") as fh:
            bruto = fh.read()
        print(f"cuerpo binario: {len(bruto)} B")
    elif args.body:
        cuerpo = json.loads(args.body)
    cabeceras = []
    for item in (args.header or []):
        if "=" not in item:
            print(f"!! cabecera invalida (falta '='): {item}", file=sys.stderr)
            return 2
        clave, valor = item.split("=", 1)
        cabeceras.append((clave, valor))
    respuesta = panel.peticion(args.cmd, cuerpo, method=args.metodo,
                               cabeceras=cabeceras, cuerpo_bruto=bruto, timeout=args.timeout)
    return 0 if mostrar(respuesta, args.cmd) else 1


def cmd_escuchar(panel, args):
    print("escuchando (Ctrl+C para salir)...")
    fin = time.time() + args.segundos if args.segundos else None
    try:
        while fin is None or time.time() < fin:
            trama = panel.leer_trama(2.0)
            if trama is None:
                continue
            payload, ok = modulo_panel.p.parse_frame(trama)
            code, cabecera, texto = modulo_panel.p.parse_response(payload)
            print(f"[{time.strftime('%H:%M:%S')}] {len(trama)} B  "
                  f"checksum={'ok' if ok else 'MAL'}  code={code}  {cabecera}"
                  + (f"  {texto[:200]}" if texto else ""))
    except KeyboardInterrupt:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser(description="Herramienta del panel COUGAR CFV235")
    ap.add_argument("--device", help="/dev/hidrawN o /dev/pts/N (por defecto: autodetecta)")
    ap.add_argument("--informe", type=int, default=modulo_panel.p.REPORT_SIZE,
                    help="tamano de la escritura, con el byte de report ID (1025)")
    ap.add_argument("--sin-prefijo", action="store_true", help="no anteponer el 0x00")
    ap.add_argument("--exacto", action="store_true", help="una sola escritura, sin rellenar")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--verbose", "-v", action="store_true")
    sub = ap.add_subparsers(dest="comando")

    sub.add_parser("listar", help="dispositivos HID del sistema")
    sub.add_parser("conn", help="saludo y propiedades (JSON)")

    p = sub.add_parser("estado", help="mandar telemetria (STATE all)")
    p.add_argument("--file", help="telemetria desde un JSON")

    p = sub.add_parser("power", help="encender/despertar")
    p.add_argument("evento", nargs="?", default="resume")

    p = sub.add_parser("brillo", help="brillo 0..100")
    p.add_argument("valor", type=int)

    p = sub.add_parser("girar", help="rotar el contenido")
    p.add_argument("grados", type=int)

    sub.add_parser("recovery", help="reset: reinicia y borra los medios")

    p = sub.add_parser("no-dormir", help="que no se apague sin trafico")
    p.add_argument("--dormir", action="store_true", help="volver a permitir el apagado")

    p = sub.add_parser("subir", help="subir un PNG/JPEG/GIF")
    p.add_argument("ruta")
    p.add_argument("--osd", action="store_true", help="a la capa OSD en vez del fondo")

    p = sub.add_parser("patron", help="generar un patron de prueba y subirlo")
    p.add_argument("nombre")
    p.add_argument("--osd", action="store_true")
    p.add_argument("--carpeta", help="donde dejar el PNG")
    p.add_argument("--solo-generar", action="store_true")
    p.add_argument("--etiquetas", action="store_true", help="rejilla: numerar los cruces")
    p.add_argument("--lado", type=int, default=8, help="cuadros: lado del cuadro")

    p = sub.add_parser("tema", help="dibujar un tema JSON (y subirlo con --subir)")
    p.add_argument("ruta")
    p.add_argument("--subir", action="store_true")
    p.add_argument("--osd", action="store_true")
    p.add_argument("--png", help="donde dejar el PNG")
    p.add_argument("--estricto", action="store_true", help="fallar si el tema tiene avisos")

    p = sub.add_parser("bucle", help="dibujar y subir en bucle")
    p.add_argument("--tema", help="tema JSON; sin el, manda un patron de rejilla")
    p.add_argument("--periodo", type=float, default=2.0)
    p.add_argument("--repeticiones", type=int, default=0)
    p.add_argument("--png")

    p = sub.add_parser("raw", help="mandar un mensaje arbitrario")
    p.add_argument("metodo", help="GET | POST | STATE | DELETE")
    p.add_argument("cmd")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--header", action="append")

    p = sub.add_parser("escuchar", help="mostrar lo que manda el panel")
    p.add_argument("--segundos", type=float, default=0)

    args = ap.parse_args()
    if not args.comando:
        ap.print_help()
        return 1

    funciones = {
        "listar": cmd_listar,
        "conn": cmd_conn, "estado": cmd_estado, "power": cmd_power, "brillo": cmd_brillo,
        "girar": cmd_girar, "recovery": cmd_recovery, "no-dormir": cmd_no_dormir,
        "subir": cmd_subir, "patron": cmd_patron, "tema": cmd_tema, "bucle": cmd_bucle,
        "raw": cmd_raw, "escuchar": cmd_escuchar,
    }
    # Los comandos que no hablan con el panel funcionan sin tenerlo conectado: asi se puede
    # generar un patron o dibujar un tema en una maquina distinta de la del panel.
    if (args.comando == "listar"
            or (args.comando == "patron" and args.solo_generar)
            or (args.comando == "tema" and not args.subir)):
        return funciones[args.comando](None, args)

    try:
        with modulo_panel.Panel(dispositivo=args.device, informe=args.informe,
                                prefijo=not args.sin_prefijo, exacto=args.exacto,
                                timeout=args.timeout, verboso=args.verbose) as panel:
            print(f"dispositivo: {panel.dispositivo}")
            return funciones[args.comando](panel, args)
    except (FileNotFoundError, PermissionError) as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
