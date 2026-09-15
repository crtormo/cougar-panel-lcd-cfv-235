"""Herramienta de linea de ordenes de la app cfv235.

    python3 -m cfv235 listar              hidraw del sistema y cual es el panel
    python3 -m cfv235 doctor              diagnostico completo del canal y del panel
    python3 -m cfv235 estado              propiedades del panel (JSON)
    python3 -m cfv235 brillo 100          brillo 0..100
    python3 -m cfv235 girar 270           orientacion (0/90/180/270)
    python3 -m cfv235 no-dormir           que no se apague sin trafico
    python3 -m cfv235 subir fondo.png --osd
    python3 -m cfv235 patron esquinas --osd
    python3 -m cfv235 tema ejemplos/x.json --subir
    python3 -m cfv235 dashboard --periodo 2
    python3 -m cfv235 telemetria          manda las metricas del PC (STATE all)
    python3 -m cfv235 sondear             sondeo del canal (descriptor + variantes)
    python3 -m cfv235 simular --traza     panel falso en /dev/pts/N

Con `--device /dev/pts/N` se apunta al panel simulado.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import canal as modulo_canal
from . import panel as modulo_panel
from . import protocolo as p


# ------------------------------------------------------------------ utilidades
def _imprimir_json(datos) -> None:
    print(json.dumps(datos, indent=2, ensure_ascii=False, default=str))


def _estado_panel(panel) -> dict:
    return panel.propiedades_seguras()


def _avisos(props: dict) -> list[str]:
    avisos = []
    if props.get("bootFinish") == 0:
        avisos.append("bootFinish=0: el panel no ha terminado de arrancar y rechazara "
                      "escrituras. Corta la alimentacion de verdad (~30 s) y vuelve a probar.")
    if props.get("brightness") == 0:
        avisos.append("brightness=0: la pantalla esta a brillo cero (se ve negra) aunque "
                      "todo lo demas funcione. Se arregla con: cfv235 brillo 100")
    if props.get("osdState") == 1:
        avisos.append("osdState=1: hay una capa OSD dibujandose ENCIMA del fondo; si subes "
                      "una imagen al fondo se vera con restos por encima.")
    espacio = props.get("space")
    if isinstance(espacio, int) and espacio < 20000:
        avisos.append(f"queda poco espacio en el panel ({espacio} KB); usa la capa OSD "
                      f"(no acumula) o `recovery` para liberar")
    return avisos


def _abrir(args, necesitar_panel: bool = True, silencioso: bool = False):
    """Devuelve un Panel listo (negociado) o sale con un mensaje claro.

    Si algo falla despues de abrir el descriptor, se cierra el panel antes de salir: antes se
    quedaba abierto (un descriptor y su bloqueo retenidos por cada intento, y con
    `--esperar-panel` acababa en EMFILE).
    """
    panel = None
    try:
        panel = modulo_panel.Panel(dispositivo=args.device, timeout=args.timeout,
                                   verboso=args.verbose)
        panel.abrir()
        if necesitar_panel:
            panel.canal.autonegociar(timeout=min(2.5, args.timeout))
        return panel
    except modulo_canal.SinPanel as exc:
        codigo, mensaje = 2, str(exc)
    except modulo_canal.SinPermisos as exc:
        codigo, mensaje = 3, str(exc)
    except (modulo_canal.ErrorCanal, OSError, ValueError) as exc:
        codigo, mensaje = 4, str(exc)
    if panel is not None:
        try:
            panel.cerrar()
        except Exception:                             # noqa: BLE001
            pass
    if not silencioso:
        print(f"!! {mensaje}", file=sys.stderr)
    raise SystemExit(codigo)


def _abrir_esperando(args, segundos: float):
    """Como `_abrir`, pero si otro proceso tiene el panel espera a que lo suelte.

    Es lo que evita el bucle de reinicios del servicio: con `Restart=always`, un dashboard
    que muere porque el panel esta ocupado se reinicia cada pocos segundos. Esperando, en
    cuanto el otro programa (la app de escritorio, el editor de COUGAR) suelta el panel, el
    dashboard vuelve solo.
    """
    if not segundos:
        return _abrir(args)
    limite = time.time() + segundos
    primero = True
    while True:
        try:
            return _abrir(args, silencioso=not primero)
        except SystemExit:
            primero = False
            restante = limite - time.time()
            if restante <= 0:
                raise
            print(f"el panel no esta libre; reintentando en 10 s "
                  f"({int(restante)} s de margen)", flush=True)
            time.sleep(min(10.0, max(1.0, restante)))


# ------------------------------------------------------------------ comandos
def cmd_listar(_args) -> int:
    dispositivos = modulo_canal.listar()
    if not dispositivos:
        print("no hay ningun /dev/hidraw*")
        return 1
    encontrado = False
    for d in dispositivos:
        es_panel = (d["vid"], d["pid"]) == (p.VID, p.PID)
        encontrado = encontrado or es_panel
        marca = "   <-- PANEL COUGAR CFV235" if es_panel else ""
        vid = "----" if d["vid"] is None else f"{d['vid']:04x}"
        pid = "----" if d["pid"] is None else f"{d['pid']:04x}"
        print(f"{d['ruta']:18s} {vid}:{pid}  {d['nombre']}{marca}")
        if es_panel and d["serie"]:
            print(f"{'':18s} serie: {d['serie']}")
    if not encontrado:
        print(f"\nno aparece el panel ({p.VID:#06x}:{p.PID:#06x}). "
              f"¿cable de datos? ¿regla udev instalada?")
        return 1
    return 0


def cmd_doctor(args) -> int:
    ruta = args.device or modulo_canal.buscar()
    print("== 1. Dispositivo ==")
    if not ruta:
        print(f"   NO aparece ningun hidraw {p.VID:#06x}:{p.PID:#06x}")
        print("   conecta el panel y comprueba con: cfv235 listar")
        return 1
    info = modulo_canal.info_dispositivo(ruta)
    print(f"   {ruta}  {info['nombre']}")
    print(f"   serie : {info.get('serie') or '(no disponible)'}")
    print(f"   USB   : {info.get('usb_manufacturer')} / {info.get('usb_product')} | "
          f"USB {info.get('usb_version')} | bcdDevice {info.get('usb_bcddevice')} | "
          f"{info.get('usb_maxpower')}")
    analisis = info.get("descriptor_analizado")
    if analisis:
        print(f"   descriptor HID: {analisis['bytes']} B, usage_page="
              f"0x{(analisis['usage_page'] or 0):04x}, report_ids="
              f"{analisis['report_ids'] or 'ninguno'}")
        for i in analisis["informes"]:
            print(f"      informe de {i['clase']}: {i['bytes']} B")
    else:
        print("   descriptor HID: no legible")

    print("\n== 2. Canal ==")
    try:
        panel = _abrir(args)
    except SystemExit as exc:
        return int(exc.code or 1)
    with panel:
        if panel.canal.variante:
            print(f"   variante de escritura: {panel.canal.variante.describe()}")
        for intento in panel.canal.negociacion:
            print(f"      {intento['variante']:12s} -> "
                  f"{'OK code=' + str(intento.get('code')) if intento.get('ok') else intento.get('error', 'sin respuesta')}")
        t0 = time.time()
        props = _estado_panel(panel)
        print(f"   latencia de conn: {(time.time() - t0) * 1000:.0f} ms")
        if not props:
            print("   !! el panel no contesta a `conn`")
            return 1

        print("\n== 3. Estado del panel ==")
        for clave in ("bootFinish", "space", "brightness", "degree", "osdState", "mode",
                      "logo", "background", "displayInSleep", "timeout",
                      "presetThemeId", "sleepClockId"):
            if clave in props:
                print(f"   {clave:16s} = {props[clave]!r}")
        version = props.get("version") or {}
        print(f"   version          = app {version.get('app')} / firmware "
              f"{version.get('firmware')} / sdk {version.get('sdk')} / hw {version.get('hardware')}")

        print("\n== 4. Sensores de este PC ==")
        try:
            from .sensores import Sensores
            s = Sensores()
            s.muestra()
            time.sleep(0.25)
            valores = s.muestra()
            con_dato = {k: v for k, v in valores.items() if v is not None}
            print(f"   {len(con_dato)}/{len(valores)} valores disponibles")
            for clave in ("cpu_uso", "cpu_temp", "cpu_mhz", "ram_uso", "gpu_temp",
                          "disco_uso", "red_bajada_mb", "cpu_vent", "bomba_vent"):
                if clave in valores:
                    print(f"      {clave:16s} = {valores[clave]}")
        except Exception as exc:                       # noqa: BLE001
            print(f"   (no se pudieron leer los sensores: {exc})")

        print("\n== 5. Avisos ==")
        avisos = _avisos(props)
        if avisos:
            for a in avisos:
                print(f"   ! {a}")
        else:
            print("   ninguno: el panel esta listo")

        if args.json:
            _imprimir_json({"dispositivo": info, "variante":
                            panel.canal.variante.describe() if panel.canal.variante else None,
                            "propiedades": props, "avisos": avisos})
    return 0


def cmd_estado(args) -> int:
    panel = _abrir(args)
    with panel:
        props = panel.propiedades()
        if not props:
            print("sin respuesta al saludo (POST conn)", file=sys.stderr)
            return 1
        _imprimir_json(props)
        for a in _avisos(props):
            print(f"! {a}", file=sys.stderr)
    return 0


def cmd_brillo(args) -> int:
    panel = _abrir(args)
    with panel:
        r = panel.brillo(args.valor)
        print(f"brillo -> {args.valor}: {'ok' if r.ok else f'code={r.code}'}")
    return 0 if r.ok else 1


def cmd_girar(args) -> int:
    panel = _abrir(args)
    with panel:
        r = panel.girar(args.grados)
        print(f"rotate -> {args.grados}: {'ok' if r.ok else f'code={r.code}'}")
    return 0 if r.ok else 1


def cmd_no_dormir(args) -> int:
    panel = _abrir(args)
    with panel:
        activo = not args.dormir
        r = panel.no_dormir(activo)
        if r.ok:
            print("apagado por espera: " + ("PERMITIDO" if args.dormir else "DESACTIVADO"))
        else:
            print(f"displayInSleep: code={r.code}", file=sys.stderr)
    return 0 if r.ok else 1


def cmd_power(args) -> int:
    panel = _abrir(args)
    with panel:
        r = panel.power(args.evento)
        print(f"power {args.evento}: {'ok' if r.ok else f'code={r.code}'}")
    return 0 if r.ok else 1


def cmd_recovery(args) -> int:
    if not args.si:
        print("`recovery` REINICIA el panel: desaparece del USB y tarda entre 2 y 8 minutos "
              "en volver. NO borra los medios (medido: no libera espacio, apaga la OSD y "
              "restaura el fondo anterior).\n"
              "Si de verdad quieres hacerlo: cfv235 recovery --si")
        return 2
    panel = _abrir(args)
    with panel:
        r = panel.recovery()
        print(f"recovery: {'ok (el panel se esta reiniciando)' if r.ok else f'code={r.code}'}")
    return 0 if r.ok else 1


def cmd_subir(args) -> int:
    if not os.path.isfile(args.ruta):
        print(f"!! no existe el fichero: {args.ruta}", file=sys.stderr)
        return 2
    capa = "osd" if args.osd else "fondo"
    panel = _abrir(args)
    with panel:
        props = _estado_panel(panel)
        if props.get("bootFinish") == 0:
            print("aviso: bootFinish=0, el panel rechazara la subida", file=sys.stderr)
        resultado = panel.subir_archivo(args.ruta, capa=capa, forzar=args.forzar)
        print(f"  fichero   : {args.ruta} ({resultado.bytes} B, {resultado.bloques} bloques, "
              f"capa {capa})")
        if resultado.transport:
            print(f"  transport : code={resultado.transport.code} "
                  f"{resultado.transport.cuerpo.strip()[:70]}")
        print(f"  acuse     : {'code=' + str(resultado.acuse_code) if resultado.acuse_llego else 'no llego'}")
        if resultado.transported:
            print(f"  transported: code={resultado.transported.code} "
                  f"{resultado.transported.cuerpo.strip()[:70]}")
        for aviso in resultado.avisos:
            print(f"  aviso     : {aviso}")
        print(f"  resultado : {'OK' if resultado.ok else 'FALLO'} - "
              f"{resultado.motivo or resultado.resumen()}")
        if resultado.ok:
            time.sleep(0.4)
            nuevo = _estado_panel(panel)
            print(f"  background ahora: {nuevo.get('background')}   "
                  f"space: {nuevo.get('space')} KB")
    return 0 if resultado.ok else 1


def cmd_patron(args) -> int:
    from . import patrones
    carpeta = args.carpeta or os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235")
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"patron-{args.nombre}.png")
    patrones.generar(args.nombre, ruta)
    print(f"patron generado: {ruta} ({os.path.getsize(ruta)} B)")
    if args.solo_generar:
        return 0
    return cmd_subir(argparse.Namespace(**{**vars(args), "ruta": ruta, "osd": args.osd,
                                           "forzar": False}))


def cmd_tema(args) -> int:
    from . import temas
    tema = temas.cargar(args.ruta)
    problemas = temas.validar(tema)
    print(f"tema: {args.ruta} ({len(tema.get('widgets', []))} widgets)")
    for problema in problemas:
        print(f"   ! {problema}")
    salida = args.png or os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                      "cfv235-tema.png")
    temas.renderizar(tema, salida)
    print(f"PNG: {salida} ({os.path.getsize(salida)} B)")
    if not args.subir:
        return 1 if (problemas and args.estricto) else 0
    return cmd_subir(argparse.Namespace(**{**vars(args), "ruta": salida, "osd": args.osd,
                                           "forzar": False}))


def cmd_dashboard(args) -> int:
    from . import dashboard, temas
    # El perfil se puede fijar en el entorno (CFV235_PERFIL): asi el servicio systemd puede
    # cambiar de diseno sin tocar la linea de ordenes.
    perfil = args.perfil or os.environ.get("CFV235_PERFIL") or "completo"
    if perfil not in dashboard.PERFILES:
        print(f"!! perfil desconocido: {perfil!r} (hay {', '.join(dashboard.PERFILES)})",
              file=sys.stderr)
        return 2
    ajustes = {clave: False for clave in args.sin}
    ajustes.update({clave: True for clave in args.con})
    if args.guardar_png:
        # Sin panel: sirve para revisar el diseno o guardar el tema para editarlo.
        from .sensores import Sensores
        s = Sensores(intervalo=1.0)
        s.muestra()
        time.sleep(0.3)
        valores = s.muestra()
        tema = (temas.cargar(args.tema) if args.tema
                else dashboard.tema_dashboard(perfil=perfil, ajustes=ajustes or None,
                                              valores=valores))
        temas.renderizar(tema, args.guardar_png, valores)
        print(f"tema dibujado en {args.guardar_png} "
              f"({os.path.getsize(args.guardar_png)} B, {len(tema['widgets'])} widgets, "
              f"perfil {perfil})")
        return 0
    panel = _abrir_esperando(args, args.esperar_panel)
    tema = temas.cargar(args.tema) if args.tema else None
    with panel:
        dash = dashboard.Dashboard(panel, tema=tema, png=args.png, capa=args.capa,
                                   verboso=args.verbose, perfil=perfil,
                                   ajustes=ajustes or None)
        props = _estado_panel(panel)
        if props.get("brightness") == 0 and not args.no_brillo:
            panel.brillo(100)
            print("(el brillo estaba en 0: subido a 100 para que se vea)")
        if not args.no_dormir:
            panel.no_dormir()

        def avisar(n, ok, error, ms):
            estado = "OK" if ok else f"FALLO ({error})"
            print(f"fotograma {n}: {estado}  ({ms:.0f} ms)")

        if args.una_vez:
            ok = dash.fotograma()
            avisar(1, ok, dash.ultimo_error, 0)
            return 0 if ok else 1
        try:
            n = dash.bucle(args.periodo, args.repeticiones, avisar=avisar)
        except KeyboardInterrupt:
            print("\ncortado")
            n = dash.fotogramas
        print(f"\nfotogramas subidos: {n}")
    return 0


def cmd_telemetria(args) -> int:
    from .panel import telemetria_desde_sensores, telemetria_demo
    panel = _abrir(args)
    with panel:
        if args.demo:
            datos = telemetria_demo()
        else:
            from .sensores import Sensores
            s = Sensores()
            s.muestra()
            time.sleep(0.25)
            datos = telemetria_desde_sensores(s.muestra())
        r = panel.telemetria(datos)
        print(f"STATE all: {'ok' if r.ok else f'code={r.code}'}")
        if args.json:
            _imprimir_json(datos)
    return 0 if r.ok else 1


def cmd_mantener(args) -> int:
    """Mantiene el panel despierto: telemetria cada pocos segundos y brillo vigilado.

    El panel vuelve solo a `brightness: 0` cuando se queda sin trafico del host, asi que para
    dejar una imagen fija puesta hay que darle flujo. Este comando hace justo eso.
    """
    from .panel import telemetria_desde_sensores
    from .sensores import Sensores
    panel = _abrir(args)
    sensores = Sensores()
    sensores.muestra()
    with panel:
        fin = time.time() + args.segundos if args.segundos else None
        n = 0
        print(f"manteniendo el panel despierto cada {args.periodo}s"
              + (f" durante {args.segundos}s" if args.segundos else " (Ctrl+C para parar)"))
        try:
            while fin is None or time.time() < fin:
                n += 1
                sensores.muestra()
                time.sleep(0.05)
                try:
                    r = panel.telemetria(telemetria_desde_sensores(sensores.muestra()))
                except modulo_canal.ErrorCanal as exc:
                    print(f"  aviso: {exc}; reintentando", file=sys.stderr)
                    panel.reabrir()
                    time.sleep(2.0)
                    continue
                # el panel se apaga solo (brightness -> 0): se reenvia el brillo de vez en cuando
                if n % max(1, int(30 / max(args.periodo, 0.5))) == 0:
                    props = panel.propiedades_seguras()
                    if props.get("brightness") == 0:
                        panel.brillo(args.brillo)
                        print("  (el panel estaba a brillo 0: reenviado)")
                if args.verbose and n % 10 == 0:
                    print(f"  {n} tramas, code={r.code}")
                time.sleep(args.periodo)
        except KeyboardInterrupt:
            print("\ncortado")
        print(f"tramas enviadas: {n}")
    return 0


def cmd_perfiles(_args) -> int:
    """Lista los perfiles de dashboard y las secciones que se pueden activar."""
    from . import dashboard
    print("Perfiles de dashboard (--perfil):")
    for nombre, cambios in dashboard.PERFILES.items():
        apagadas = ", ".join(k for k, v in cambios.items() if not v) or "todo activado"
        print(f"   {nombre:14s} sin: {apagadas}")
    print("\nSecciones (--con SECCION / --sin SECCION):")
    for s in dashboard.catalogo_secciones():
        print(f"   {s['clave']:14s} {s['etiqueta']:16s} {s['descripcion']}")
    print("\nEjemplos:")
    print("   cfv235 dashboard --perfil minimo")
    print("   cfv235 dashboard --perfil completo --sin red --sin sistema")
    print("   cfv235 dashboard --perfil esencial --con ventiladores")
    print("   cfv235 dashboard --guardar-png /tmp/mi_dashboard.png")
    return 0


def cmd_video(args) -> int:
    """`cfv235 video ver RUTA` (solo informe, sin panel) o `cfv235 video RUTA` (reproducir).

    El panel solo muestra PNG, asi que reproducir es subir fotogramas PNG uno detras de
    otro: una sola sesion, ~100-440 ms por fotograma y un techo realista de ~3 fps. El video se
    ve a saltos y sin sonido (el panel no tiene audio).
    """
    from . import video
    # Las dos formas comparten posicionales (`video ver RUTA` y `video RUTA`).
    if args.accion == "ver":
        ruta = args.ruta
    elif args.ruta is not None:
        print(f"!! sobra un argumento: {args.ruta!r} "
              f"(usa `cfv235 video RUTA` o `cfv235 video ver RUTA`)", file=sys.stderr)
        return 2
    else:
        ruta = args.accion
    if not ruta:
        print("!! falta la ruta: cfv235 video RUTA | cfv235 video ver RUTA", file=sys.stderr)
        return 2

    informe = video.inspeccionar(ruta)
    if args.accion == "ver":
        # Solo informe: no hace falta panel ni GStreamer (para GIF y secuencias).
        if args.json:
            _imprimir_json(informe)
        else:
            print(video.informe_legible(informe))
        return 0 if not informe["error"] else 2

    if informe["error"] and informe["tipo"] == "desconocido":
        print(f"!! {informe['error']}", file=sys.stderr)
        return 2
    if informe["necesita_gstreamer"] and not video.gstreamer_disponible():
        print(f"!! {video.motivo_sin_gstreamer()}", file=sys.stderr)
        return 2
    try:
        fuente = video.abrir_fuente(ruta, ajuste=args.ajuste)
    except video.ErrorVideo as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2

    panel = _abrir(args)
    with panel:
        props = _estado_panel(panel)
        if props.get("brightness") == 0:
            print("aviso: el panel esta a brillo 0 (no se vera nada). "
                  "Se arregla con: cfv235 brillo 100", file=sys.stderr)
        if props.get("bootFinish") == 0:
            print("aviso: bootFinish=0, el panel rechazara las subidas", file=sys.stderr)
        reproductor = video.Reproductor(panel, fuente, fps=args.fps, bucle=not args.sin_bucle,
                                        capa=args.capa, verboso=args.verbose)
        print(f"{fuente.describe()} -> panel {video.ANCHO}x{video.ALTO}")
        print(f"  ajuste {args.ajuste}, {args.fps:g} fps, "
              f"{'en bucle' if not args.sin_bucle else 'sin bucle'}, capa {args.capa}")
        if args.fps > video.FPS_MAXIMO_REALISTA:
            print(f"  aviso: por encima de {video.FPS_MAXIMO_REALISTA:g} fps el panel no da "
                  f"mas; se saltaran fotogramas")
        print("  recuerda: el panel solo muestra PNG; el video va a saltos y sin sonido")

        def avisar(n, ok, error, ms):
            if not ok:
                print(f"fotograma {n}: FALLO ({error})")
            elif args.verbose or n % 10 == 0:
                print(f"fotograma {n}: OK  ({ms:.0f} ms)")

        try:
            subidos = reproductor.reproducir(max_fotogramas=args.max, avisar=avisar)
        except KeyboardInterrupt:
            print("\ncortado")
            subidos = reproductor.fotogramas
        print(f"\n{reproductor.resumen()}")
    return 0 if subidos else 1


def cmd_stream(args) -> int:
    """`cfv235 stream`: reflejo de pantalla en vivo (escritorio -> capa OSD).

    Es `video` con la fuente de pantalla, que no se acaba: se para con Ctrl+C. El techo
    realista es ~3 fps (medido en el panel); lo comodo es 2 fps. La primera captura puede
    pedir permiso al portal de escritorio.
    """
    from . import video
    fuente = video.FuentePantalla(ajuste=args.ajuste)
    panel = _abrir(args)
    with panel:
        props = _estado_panel(panel)
        if props.get("brightness") == 0:
            print("aviso: el panel esta a brillo 0 (no se vera nada). "
                  "Se arregla con: cfv235 brillo 100", file=sys.stderr)
        if props.get("bootFinish") == 0:
            print("aviso: bootFinish=0, el panel rechazara las subidas", file=sys.stderr)
        reproductor = video.Reproductor(panel, fuente, fps=args.fps, bucle=True,
                                        capa=args.capa, verboso=args.verbose)
        print(f"reflejo de pantalla -> panel {video.ANCHO}x{video.ALTO}")
        print(f"  ajuste {args.ajuste}, {args.fps:g} fps, capa {args.capa}")
        if args.fps > video.FPS_MAXIMO_REALISTA:
            print(f"  aviso: por encima de {video.FPS_MAXIMO_REALISTA:g} fps el panel no da "
                  f"mas; se saltaran fotogramas")
        print("  la primera captura puede pedir permiso (portal de escritorio)")
        print("  Ctrl+C para parar")

        def avisar(n, ok, error, ms):
            if not ok:
                print(f"fotograma {n}: FALLO ({error})")
            elif args.verbose or n % 10 == 0:
                print(f"fotograma {n}: OK  ({ms:.0f} ms)")

        try:
            subidos = reproductor.reproducir(max_fotogramas=args.max, avisar=avisar)
        except KeyboardInterrupt:
            print("\ncortado")
            subidos = reproductor.fotogramas
        print(f"\n{reproductor.resumen()}")
    return 0 if subidos else 1


def cmd_modo(args) -> int:
    """`mode`: comando no documentado por el kit (medido: existe y acepta 0..3)."""
    panel = _abrir(args)
    with panel:
        try:
            r = panel.modo(args.valor)
        except ValueError as exc:
            print(f"!! {exc}", file=sys.stderr)
            return 2
        print(f"mode -> {args.valor}: {'ok' if r.ok else f'code={r.code}'}")
        if r.ok:
            print(f"   mode ahora = {panel.propiedades_seguras().get('mode')}")
    return 0 if r.ok else 1


def cmd_servicio(args) -> int:
    """Gestiona el servicio de usuario que mantiene el dashboard en marcha."""
    from . import servicio
    accion = args.accion
    if accion == "estado":
        est = servicio.estado()
        print(f"servicio {servicio.NOMBRE}: {est['descripcion']}")
        print(f"   habilitado al iniciar sesion: {'si' if est['habilitado'] else 'no'}")
        print(f"   reinicios desde que arranco : {est['reinicios']}")
        pid = servicio.pid_del_bloqueo()
        if pid:
            quien = " (es el servicio)" if servicio.ocupado_por_el_servicio() else ""
            print(f"   el panel lo tiene el pid    : {pid}{quien}")
        else:
            print("   el panel esta libre")
        return 0
    if accion == "parar":
        ok, mensaje = servicio.parar()
        print(mensaje or ("servicio parado" if ok else "no se pudo parar"))
        return 0 if ok else 1
    if accion == "arrancar":
        ok, mensaje = servicio.arrancar()
        print(mensaje or ("servicio arrancado" if ok else "no se pudo arrancar"))
        return 0 if ok else 1
    if accion == "reiniciar":
        ok, mensaje = servicio.reiniciar()
        print(mensaje or ("servicio reiniciado" if ok else "no se pudo reiniciar"))
        return 0 if ok else 1
    if accion == "log":
        print(servicio.registro(args.lineas))
        return 0
    if accion == "liberar":
        ok, mensaje = servicio.liberar_panel(args.device)
        print(mensaje)
        return 0 if ok else 1
    print(f"accion desconocida: {accion}", file=sys.stderr)
    return 2


def cmd_sondear(args) -> int:
    import subprocess
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "herramientas", "sondear_canal.py")
    orden = [sys.executable, ruta]
    if args.device:
        orden += ["--device", args.device]
    if args.json:
        orden += ["--json", args.json]
    return subprocess.call(orden)


def cmd_simular(args) -> int:
    from . import simulador
    sys.argv = ["cfv235.simulador", "--traza"] if args.traza else ["cfv235.simulador"]
    if args.salida:
        sys.argv += ["--salida", args.salida]
    if args.tarde_ms:
        sys.argv += ["--tarde-ms", str(args.tarde_ms)]
    if args.caduca:
        sys.argv += ["--caduca", str(args.caduca)]
    if args.sin_arrancar:
        sys.argv += ["--sin-arrancar"]
    return simulador.main()


# ------------------------------------------------------------------ parser
def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cfv235", description="Panel COUGAR CFV235 desde Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--device", help="/dev/hidrawN o /dev/pts/N (por defecto: autodetecta)")
    ap.add_argument("--timeout", type=float, default=3.0, help="espera por respuesta (s)")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--json", nargs="?", const=True, default=None,
                    help="salida en JSON (y fichero, si se da una ruta)")
    ap.add_argument("--version", action="version", version="cfv235 1.0.0")

    sub = ap.add_subparsers(dest="comando")

    sub.add_parser("listar", help="hidraw del sistema y cual es el panel").set_defaults(
        funcion=cmd_listar)

    sub.add_parser("doctor", help="diagnostico completo (canal + panel + sensores)"
                   ).set_defaults(funcion=cmd_doctor)

    sub.add_parser("estado", help="propiedades del panel (POST conn)"
                   ).set_defaults(funcion=cmd_estado)

    p = sub.add_parser("brillo", help="brillo 0..100")
    p.add_argument("valor", type=int)
    p.set_defaults(funcion=cmd_brillo)

    p = sub.add_parser("girar", help="orientacion")
    p.add_argument("grados", type=int, choices=[0, 90, 180, 270])
    p.set_defaults(funcion=cmd_girar)

    p = sub.add_parser("no-dormir", help="conmuta displayInSleep (efecto sobre el apagado sin confirmar)")
    p.add_argument("--dormir", action="store_true", help="volver a permitir el apagado")
    p.set_defaults(funcion=cmd_no_dormir)

    p = sub.add_parser("power", help="despertar el panel")
    p.add_argument("evento", nargs="?", default="resume")
    p.set_defaults(funcion=cmd_power)

    p = sub.add_parser("recovery", help="Reset: reinicia (no borra los medios)")
    p.add_argument("--si", action="store_true", help="confirmar")
    p.set_defaults(funcion=cmd_recovery)

    p = sub.add_parser("subir", help="subir un PNG/JPEG/GIF")
    p.add_argument("ruta")
    p.add_argument("--osd", action="store_true", help="capa OSD (no acumula espacio)")
    p.add_argument("--forzar", action="store_true", help="subir aunque no quepa")
    p.set_defaults(funcion=cmd_subir)

    p = sub.add_parser("patron", help="generar un patron de prueba (y subirlo)")
    p.add_argument("nombre")
    p.add_argument("--osd", action="store_true")
    p.add_argument("--carpeta")
    p.add_argument("--solo-generar", action="store_true")
    p.set_defaults(funcion=cmd_patron)

    p = sub.add_parser("tema", help="dibujar un tema JSON (y subirlo)")
    p.add_argument("ruta")
    p.add_argument("--subir", action="store_true")
    p.add_argument("--osd", action="store_true")
    p.add_argument("--png")
    p.add_argument("--estricto", action="store_true")
    p.set_defaults(funcion=cmd_tema)

    p = sub.add_parser("dashboard", help="dashboard en vivo (metricas del PC)")
    p.add_argument("--tema", help="tema JSON propio")
    p.add_argument("--periodo", type=float, default=2.0)
    p.add_argument("--repeticiones", type=int, default=0)
    p.add_argument("--png")
    p.add_argument("--capa", default="osd", choices=["osd", "fondo"])
    p.add_argument("--una-vez", action="store_true", help="un solo fotograma")
    p.add_argument("--perfil", default=None,
                   help="completo | esencial | graficas | minimo | presentacion "
                        "(por defecto: CFV235_PERFIL o completo)")
    p.add_argument("--sin", action="append", default=[], metavar="SECCION",
                   help="desactiva una seccion (repetible: --sin red --sin grafica)")
    p.add_argument("--con", action="append", default=[], metavar="SECCION",
                   help="activa una seccion del perfil")
    p.add_argument("--guardar-png", metavar="RUTA",
                   help="en vez de subirlo, guarda el tema dibujado en un PNG")
    p.add_argument("--esperar-panel", type=float, default=0,
                   help="segundos a esperar si otro programa tiene el panel (0 = fallar; "
                        "el servicio usa 3600 para no entrar en bucle de reinicios)")
    p.add_argument("--no-brillo", action="store_true", help="no tocar el brillo")
    p.add_argument("--no-dormir", action="store_true", help="no tocar displayInSleep")
    p.set_defaults(funcion=cmd_dashboard)

    p = sub.add_parser("telemetria", help="mandar STATE all con los sensores del PC")
    p.add_argument("--demo", action="store_true", help="valores de ejemplo")
    p.set_defaults(funcion=cmd_telemetria)

    p = sub.add_parser("mantener", help="mantener el panel despierto (evita que se apague)")
    p.add_argument("--periodo", type=float, default=2.0, help="segundos entre tramas")
    p.add_argument("--segundos", type=float, default=0, help="duracion (0 = sin limite)")
    p.add_argument("--brillo", type=int, default=100)
    p.set_defaults(funcion=cmd_mantener)

    sub.add_parser("perfiles", help="perfiles y secciones del dashboard"
                   ).set_defaults(funcion=cmd_perfiles)

    p = sub.add_parser("video", help="GIF/secuencia/video al panel, fotograma a fotograma")
    p.add_argument("accion", nargs="?", metavar="ver|RUTA",
                   help="RUTA (reproducir) o 'ver' para solo inspeccionar (sin panel)")
    p.add_argument("ruta", nargs="?", help="RUTA cuando se usa 'video ver RUTA'")
    p.add_argument("--fps", type=float, default=4.0,
                   help="fotogramas por segundo (medido: el panel sostiene ~3)")
    p.add_argument("--sin-bucle", action="store_true", help="parar al acabar la fuente")
    p.add_argument("--ajuste", default="ajustar", choices=["ajustar", "recortar", "estirar"])
    p.add_argument("--max", type=int, default=0, metavar="N",
                   help="maximo de fotogramas (0 = sin limite)")
    p.add_argument("--capa", default="osd", choices=["osd", "fondo"])
    p.set_defaults(funcion=cmd_video)

    p = sub.add_parser("stream", help="reflejo de pantalla en vivo (escritorio -> panel)")
    p.add_argument("--fps", type=float, default=2.0,
                   help="fotogramas por segundo (medido: el panel sostiene ~3)")
    p.add_argument("--ajuste", default="ajustar", choices=["ajustar", "recortar", "estirar"])
    p.add_argument("--max", type=int, default=0, metavar="N",
                   help="maximo de fotogramas (0 = sin limite)")
    p.add_argument("--capa", default="osd", choices=["osd", "fondo"])
    p.set_defaults(funcion=cmd_stream)

    p = sub.add_parser("modo", help="modo de pantalla (comando no documentado, 0..3)")
    p.add_argument("valor", type=int, choices=[0, 1, 2, 3])
    p.set_defaults(funcion=cmd_modo)

    p = sub.add_parser("servicio", help="gestionar el servicio del dashboard")
    p.add_argument("accion", nargs="?", default="estado",
                   choices=["estado", "parar", "arrancar", "reiniciar", "log", "liberar"],
                   help="estado | parar | arrancar | reiniciar | log | liberar")
    p.add_argument("--lineas", type=int, default=20, help="lineas del registro (accion log)")
    p.set_defaults(funcion=cmd_servicio)

    p = sub.add_parser("sondear", help="sondeo del canal (descriptor + variantes)")
    p.add_argument("--json", nargs="?", const="sondeo_canal.json")
    p.set_defaults(funcion=cmd_sondear)

    p = sub.add_parser("simular", help="panel falso en un /dev/pts/N")
    p.add_argument("--traza", action="store_true")
    p.add_argument("--salida")
    p.add_argument("--caduca", type=float)
    p.add_argument("--tarde-ms", type=float)
    p.add_argument("--sin-arrancar", action="store_true")
    p.set_defaults(funcion=cmd_simular)

    return ap


def main(argv=None) -> int:
    ap = construir_parser()
    args = ap.parse_args(argv)
    if not args.comando:
        ap.print_help()
        return 1
    if args.json and isinstance(args.json, str):
        # volcar tambien a fichero lo que se imprima en JSON
        try:
            return args.funcion(args)
        finally:
            pass
    return args.funcion(args)


if __name__ == "__main__":
    sys.exit(main())
