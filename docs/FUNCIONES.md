# Funciones del código — referencia en extenso

Descripción de **todos los módulos y sus funciones públicas**, cómo se relacionan entre sí y
qué hace cada pieza. Es la guía para leer o modificar el código sin tener que abrir los diez
módulos a la vez. El detalle del protocolo USB está en [`CANAL.md`](CANAL.md); aquí va el mapa
del código.

```
capa USB/HID        protocolo.py  →  canal.py  →  panel.py
capa de contenido   sensores.py → widgets.py / temas.py / dashboard.py → patrones.py / video.py
capa de servicio    config.py · servicio.py · simulador.py · cli.py
capa gráfica        cfv235_gtk/: app.py · ventana.py · paginas_extra.py · estilo.py
```

---

## cfv235/protocolo.py — el framing del canal

Traduce entre bytes crudos del USB HID y mensajes con estructura. Nada aquí habla con el
panel: solo empaqueta, desempaqueta y valida.

| Elemento | Qué hace |
|---|---|
| `checksum(payload, total)` | Calcula el checksum que el panel espera al final de cada payload. |
| `_append_escapado(valor, salida)` | Añade un byte aplicando el escape del protocolo (los bytes de control no pueden aparecer crudos). |
| `build_frame(payload)` | Construye la trama completa: cabecera + payload escapado + checksum. Es lo que sale por el `hidraw`. |
| `Trama` | Resultado de decodificar: guarda payload, longitud y validez. |
| `Trama.len_ok() / checksum_ok() / ok()` | Validaciones sucesivas; `ok()` es el atajo para «¿esta trama vale?». |
| `decode_frame(buf)` | Inverso de `build_frame`: de un búfer crudo a una `Trama` (o `None` si está corrupta). |
| `Respuesta` | Una respuesta del panel: código, estado, cuerpo JSON. `.ok()` dice si fue 200-equivalente; `.json()` parsea el cuerpo. |
| `parse_respuesta(payload)` | Descompone el payload de respuesta en `(seq, code, status, texto, cuerpo)`. |
| `build_request(cmd, cuerpo, metodo, seq)` | Construye la petición estilo-HTTP que el panel espera (`POST /brillo` y semejantes) ya enmarcada. |
| `nueva_secuencia() / reiniciar_secuencia()` | Contador global de `seq` para emparejar petición-respuesta. |
| `build_media_report(indice, total, trozo, ...)` | Trama especial `mediaReport`: los trozos de una subida de fichero. |

## cfv235/canal.py — el canal USB

La única clase del proyecto que toca el dispositivo directamente. Todo lo demás pasa por
`Canal` o por `Panel` (que lo envuelve).

| Elemento | Qué hace |
|---|---|
| `ErrorCanal` y hijas (`SinPermisos`, `SinPanel`, `PanelOcupado`) | Excepciones con significado: el CLI y la GTK las traducen a mensajes accionables. |
| `tomar_bloqueo(dispositivo, esperar) / soltar_bloqueo(testigo)` | Cerrojo por fichero en `/run`: impide que el dashboard del servicio y la app GTK peleen por el panel. |
| `Variante` | El panel tiene dos variantes de framing (según estado de arranque). `autonegociar()` descubre cuál responde y la devuelve. |
| `listar(patron) / buscar(vid, pid)` | Encuentran los `/dev/hidraw*` candidatos y el del CFV235 en particular. |
| `analizar_descriptor(datos) / leer_descriptor(ruta) / info_dispositivo(ruta)` | Leen y parsean el descriptor HID del kernel: needed para saber qué interfaz responde. |
| `Canal` | La conexión: `abrir()`/`cerrar()` (context manager), `enviar()`/`enviar_informe()`, `leer_trama()`, `drenar()`. |
| `Canal._empaquetar() / _escribir() / _leer() / extraer_trama()` | Las piezas internas: escritura por reportes de salida, lectura con timeout y re-ensamblado de tramas que llegan partidas. |
| `Canal.operacion()` | Context manager que toma el cerrojo + autonegocia: la forma correcta de hablar con el panel. |
| `Canal.peticion(cmd, cuerpo, metodo)` | Petición de alto nivel: `build_request` → enviar → `esperar_respuesta` → `Respuesta`. |
| `Canal.esperar_respuesta(seq, timeout)` | Lee tramas hasta encontrar la respuesta de la `seq` pedida (descarta ruido y acuses intermedios). |
| `Canal.autonegociar(timeout)` | Sondea con las dos variantes y devuelve la que responde. |

## cfv235/panel.py — el panel como objeto

Envuelve a `Canal` y le da vocabulario de panel: brillo, subidas, recuperación. Aquí vive la
lógica de negocio de hablar con el CFV235.

| Elemento | Qué hace |
|---|---|
| `ResultadoSubida` | Registro de una subida: bytes, bloques, tiempo, resultado. `.resumen()` lo hace texto. |
| `Panel` | Context manager (`with Panel() as p:`). Crea/abre el `Canal` y mantiene `propiedades()` cacheadas. |
| `Panel.propiedades() / propiedades_seguras()` | `GET /info` del panel (versión, espacio, `bootFinish`, `osdState`...). La «segura» nunca lanza. |
| `Panel.espacio_libre_kb() / listo()` | Atajos: espacio en medios y «¿bootFinish=1?» (sin eso el panel rechaza subidas). |
| `Panel.reabrir() / esperar_arranque()` | Reconexión tras desenchufar y espera activa al arranque del panel. |
| `Panel.brillo(valor) / girar(grados) / power(evento) / modo(valor)` | Comandos simples de control (algunos, como `modo`, no documentados por el kit: medidos a mano). |
| `Panel.recovery() / no_dormir(activo)` | `recovery` reinicia el panel y restaura el fondo (NO libera espacio, eso está medido); `no_dormir` evita el apagado por espera. |
| `Panel.telemetria(datos) / realtime(activo)` | Empuja los datos del PC que el firmware pinta con sus widgets propios. |
| `Panel.subir_archivo(ruta, capa, ...) / subir_datos(datos, nombre, capa)` | Subida de imágenes: trocea en `mediaReport`, vigila el acuse, respeta el límite real de ~5-10 MB. `capa` es `fondo` u `osd` (las dos capas del panel, ver `CANAL.md`). |
| `Panel._subir_datos() / _leer_acuse()` | La máquina de subida: handshake, troceo, informe de progreso, acuse final. |
| `Panel.diagnostico(negociar)` | Batería de comprobaciones que alimenta `cfv235 doctor` y la página Diagnóstico. |
| `telemetria_demo() / telemetria_desde_sensores(valores)` | Datos de telemetría sintéticos (tests/demo) o desde la muestra de `Sensores`. |

## cfv235/sensores.py — las métricas del PC

Lectura directa de `/proc`, `sysfs` y `hwmon`, sin dependencias: nada de `psutil`. Descubre
los sensores disponibles y solo reporta los que existen de verdad.

| Elemento | Qué hace |
|---|---|
| `Sensores(intervalo)` | La clase pública. `muestra()` devuelve el diccionario completo (CPU, RAM, discos, red, GPU, batería, uptime...). Con `intervalo > 0` cachea entre lecturas para no leer `/proc` más de lo necesario. |
| `Sensores.resumen()` | Versión compacta y lista para texto/telemetría. |
| `_descubrir_hwmon() / _candidatos() / _primer_valor()` | Búsqueda de sensores hwmon por tipo (temp, fan, in) y por etiqueta, con prioridad configurable. |
| `_leer_cpu_stat() / _datos_cpuinfo() / _frecuencia_cpu_mhz()` | Uso de CPU (deltas de `/proc/stat`), modelo y frecuencia. |
| `_memoria() / _velocidad_ram_mhz() / _leer_meminfo()` | RAM usada/libre y velocidad del módulo (DMIDECODE-free: lee `sysfs`). |
| `_leer_diskstats() / _uso_disco(punto) / _es_disco_fisico()` | Actividad de discos y espacio por punto de montaje, descartando loop/ramdisk. |
| `_leer_net_dev() / _actualizar_deltas()` | Tráfico de red por interfaz, con deltas entre muestras. |
| `_gpu_uso() / _gpu_vram_gb() / _frecuencia_gpu_mhz()` | GPU vía `sysfs`/DRM cuando existe (en equipos sin GPU dedicada devuelven nada y el dashboard omite la sección). |
| `_cargas_y_procesos() / _uptime_horas() / _procesos() / _bateria()` | Piezas menores del pie del dashboard. |
| `_leer_texto() / _leer_entero() / _glob_seguro() / _entradas_seguro()` | Utilerías de lectura robusta: nunca lanzan, devuelven `None` ante lo inesperado. |

## cfv235/widgets.py — el dibujo de los widgets del tema

Un «tema» del panel es un JSON con `fondo` y una lista de `widgets` (dato, barra, gráfica...).
Este módulo sabe **pintar cada tipo de widget** sobre una imagen Pillow, con los datos de
`Sensores`.

| Elemento | Qué hace |
|---|---|
| `ErrorWidget` | Error de definición de widget con contexto (índice, campo). |
| `nombre_a_clave(nombre)` | Normaliza «Temperatura CPU» → `temperatura_cpu` (las claves de `fuentes`). |
| `es_color() / color(valor, por_defecto)` | Validación y normalización de colores (nombre CSS, `#rrggbb`, tupla). |
| `_fuente(tamano, negrita) / _rutas_de_fuente(negrita)` | Carga perezosa de las fuentes TrueType embebidas en `cfv235/fuentes/`. |
| `formatear(valor, unidad)` | Formato humano: 1234567 → «1.2 GB», 0.83 → «83 %», según la unidad pedida. |
| `_valor_sintetico(clave, valores)` | Valores derivados (`cpu_uso` a partir de los deltas, totals de red...). |
| `texto_con_marcadores(plantilla, fuentes)` | Sustituye `{cpu_uso}` y marcadores en textos de widgets. |
| `_anotar_historial(valores) / _aplanar_muestra(crudo)` | Mantiene series históricas en memoria para las gráficas. |
| `crear_fuentes() / _Lector` | El puente entre `Sensores` y el dibujo: `_Lector.valor(nombre)` y `.serie(nombre)` dan el dato puntual o la serie. |
| `_encajar(dib, contenido, tamano, limite)` | Dibuja texto recortándolo/escalándolo para que quepa en el ancho del widget. |
| `dibujar_dato / dibujar_barra / dibujar_grafica` | Los tres tipos de widget: etiqueta+valor grande, barra de progreso, y gráfica de serie. |
| `ultimos_problemas() / _registrar_problema()` | Los problemas de validación quedan coleccionados para mostrarlos en vez de romper el dashboard. |

## cfv235/temas.py — la validación y el render del tema

El ciclo de vida del JSON: cargar → validar → normalizar → renderizar. Compartido por el CLI
(`cfv235 tema`), la app GTK (Editor de temas) y el dashboard.

| Elemento | Qué hace |
|---|---|
| `fuentes_disponibles(instanciar)` | Catálogo de datos que un widget puede pedir (desde `Sensores`), para validar claves. |
| `marcadores() / campos(tipo) / catalogo_json()` | Documentación auto-generada: marcadores de texto, campos por tipo de widget y el catálogo completo para `PROMPT_TEMAS.md`. |
| `plantilla(tipo, **valores) / tema_vacio() / tema_nuevo() / tema_por_defecto()` | Constructores de temas. |
| `cargar(ruta) / guardar(ruta, tema)` | JSON del disco con los avisos del validador. |
| `validar(tema, estricto)` | Valida cada widget (índice, campos, tipos, colores, fuentes conocidas). `estricto` lanza; suave colecciona. |
| `normalizar(tema)` | Aplica defectos, corrige tipos y rellena campos faltantes: después de esto, el render no pregunta. |
| `renderizar(tema, ruta) / renderizar_datos(tema, fuentes, formato)` | El tema a imagen: `renderizar` escribe a fichero, `renderizar_datos` devuelve bytes (el Editor GTK prevé en memoria con esta). |

## cfv235/dashboard.py — el dashboard en vivo

Combina `Sensores` + `temas` para producir el fotograma del PC y subirlo al panel en bucle.
Toda la rejilla calculada que describe el README vive aquí.

| Elemento | Qué hace |
|---|---|
| `Seccion` | Una sección del dashboard (cpu, red, ventiladores...) con su condición de existencia. |
| `secciones_activas(perfil, ajustes)` | Aplica perfil (`completo`, `esencial`, `graficas`, `minimo`, `presentacion`) + `--con`/`--sin`, y descarta secciones sin datos en este equipo. |
| `catalogo_secciones() / columnas(cuantas)` | Utilerías de la rejilla (4 columnas de 453 px). |
| `tarjeta(x, ancho, etiqueta, ...)` | Construye el widget `dato` estándar en la posición dada. |
| `_cabecera() / _zona_baja() / _hay() / _con_datos()` | Piezas del tema: cabecera (título/reloj/fecha), pie (sistema/procesos) y helpers de contenido condicional. |
| `tema_dashboard(titulo, perfil, ...)` | El tema completo armado. `tema_esencial()` y `tema_minimo()` son atajos. |
| `tema_desde_fichero(ruta)` | Carga un tema propio y lo usa de dashboard. |
| `Dashboard(panel, sensores, tema, ...)` | El bucle: `fotograma()` lee sensores, renderiza y sube; `bucle(periodo, repeticiones, parar)` lo repite con control de parada limpio. |

## cfv235/patrones.py — patrones de prueba

Imágenes de diagnóstico para medir el panel: cuadrículas, degradados, cartas de color.

| Elemento | Qué hace |
|---|---|
| `generar(nombre, ruta, lado, etiquetas)` | Genera un patrón PNG (los nombres en `_patron_bytes` de la GTK usan el mismo catálogo). |
| `generar_todos(carpeta)` | La colección completa a disco. |
| `main()` | CLI: `python3 -m cfv235.patrones CARPETA`. |

## cfv235/video.py — GIF, vídeo y pantalla en el panel

El panel solo muestra PNG: «vídeo» es subir fotogramas PNG uno detrás de otro. Este módulo
abstrae las fuentes y controla el ritmo.

| Elemento | Qué hace |
|---|---|
| `ErrorVideo` | Error de fuente o de reproducción. |
| `gstreamer_disponible() / motivo_sin_gstreamer() / _cargar_gstreamer()` | GStreamer es opcional (solo para vídeos mp4/mkv): aquí se prueba y se explica si falta. |
| `ajustar_imagen(imagen, ajuste, ancho, alto)` | `ajustar` (letterbox), `recortar` (centrado), `estirar` — a 1920×462. |
| `Fuente` | La base: `siguiente()` da el fotograma siguiente (PNG ajustado), `reiniciar()`, context manager, iterable. |
| `FuenteGIF(ruta)` | GIF con Pillow, respetando la duración de cada fotograma. |
| `FuenteSecuencia(rutas)` | Carpeta/lista de PNG-JPG ordenada naturalmente. |
| `FuenteVideo(ruta)` | Vídeo vía GStreamer (`decodebin` → `appsink`, `drop=false`: el detalle que arregló la pérdida de 34 de 120 fotogramas). |
| `FuentePantalla(ajuste, timeout, capturador)` | **Reflejo de pantalla en vivo**: captura el escritorio por el portal XDG (Wayland) y sirve fotogramas sin fin. `capturador` inyectable para tests. Es lo que alimenta `cfv235 stream`. |
| `Reproductor(panel, fuente, fps, bucle, capa, verboso)` | El motor: sube cada fotograma a la capa pedida respetando el techo real (~3 fps medidos), contando OK/fallos; `resumen()` para el informe final. |
| `informe_legible(informe) / _informe_video()` | Informe en texto de la corrida. |

## cfv235/config.py — la configuración de la app

| Elemento | Qué hace |
|---|---|
| `directorio() / ruta()` | Dónde vive `config.json` (XDG, con override por entorno). |
| `defectos()` | Los valores por defecto (incluye `keepalive`, añadido con la galería de fondos). |
| `leer() / obtener(clave, defecto) / escribir(**cambios) / escribir_todo(datos)` | API de configuración; `escribir` fusiona y persiste atómicamente. |
| `_normalizar(bruto)` | Fusiona con defectos y valida tipos: un `config.json` a medio editar no rompe la app. |

## cfv235/servicio.py — el servicio systemd

Envoltorio de `systemctl` para el unit `cfv235-dashboard.service`, con las reglas de
convivencia sobre el panel.

| Elemento | Qué hace |
|---|---|
| `disponible() / es_unidad_instalada()` | ¿Hay systemd y está el unit instalado? |
| `estado() / propiedades() / activo() / registro(lineas)` | Estado y log del servicio. |
| `parar() / arrancar() / reiniciar() / habilitar(activar)` | Control básico. |
| `pid_del_bloqueo() / ocupado_por_el_servicio()` | ¿El servicio tiene el cerrojo del panel ahora mismo? |
| `liberar_panel(dispositivo, espera)` | Lo para si hace falta para que la app GTK (o un comando) use el panel, y lo deja anotado. |

## cfv235/simulador.py — el panel falso

Un CFV235 de mentira sobre un PTY para los tests y para desarrollar sin panel.

| Elemento | Qué hace |
|---|---|
| `PanelFalso(caduca_ms, salida)` | Implementa el lado del panel: autonegociación, comandos, acuses de subida, caducidad de sesión. `_medios_vacios()` simula el estado de medios. |
| `atender(fd, payload) / respuesta() / acuse() / informe_medio() / cerrar_subida()` | El despacho de tramas y la máquina de subida del lado servidor. |
| `extraer_mensaje(panel)` | Utilería de tests para leer lo que el cliente escribió. |
| `servir(fd, panel, parar) / abrir_pty() / main()` | El bucle del simulador (`cfv235 simular`). |

## cfv235/cli.py — los comandos

Cada `cmd_*` es un subcomando de `cfv235`. Todos comparten `_abrir()` (panel + avisos) y
`_estado_panel()`/`_avisos()` para el contexto común.

| Comando | Qué hace |
|---|---|
| `listar` | Los `/dev/hidraw*` con probabilidad de ser el panel. |
| `doctor` | El chequeo completo: permisos, descriptor, autonegociación, propiedades, espacio, subida de prueba. |
| `estado / brillo / girar / no_dormir / power / recovery / modo` | Control directo (ver `Panel`). |
| `subir` | Un PNG/JPG a la capa que pidas. |
| `patron / tema` | Generar patrones / validar y subir un tema JSON. |
| `dashboard` | El bucle del dashboard (`--una-vez`, `--periodo`, `--perfil`, `--guardar-png`). |
| `telemetria / mantener` | Telemetría puntual o mantenida (el «no apagar» con tráfico real). |
| `perfiles` | Lista los perfiles y secciones disponibles. |
| `video` | Reproducir GIF/carpeta/vídeo (ver `Reproductor`). |
| `stream` | **Reflejo de pantalla en vivo** (escritorio → capa OSD). Techo ~3 fps; se para con Ctrl+C. |
| `servicio` | Gestión del unit systemd (estado, parar, arrancar, log, liberar). |
| `sondear / simular` | Herramientas de investigación del canal y el panel falso. |
| `construir_parser() / main()` | El árbol de argumentos y el punto de entrada (`python3 -m cfv235`). |

---

## cfv235_gtk/ — la app de escritorio

### app.py
`Aplicacion(Adw.Application)` con `id_de_aplicacion()` y `main()`: ciclo de vida GTK4,
acciones globales y arranque de la ventana.

### estilo.py
CSS del programa (`aplicar()`), esquema claro/oscuro (`esquema_actual`, `poner_esquema`,
`menu_de_esquema`).

### ventana.py — la ventana principal (las páginas)

Cada página se añade con `_anadir_pagina(contenido, nombre, titulo, icono)`. El orden en la
barra lateral:

| Página | Contenido |
|---|---|
| **Estado** | Propiedades del panel en vivo, espacio, `bootFinish`, interruptores de control (brillo, no-dormir, **keepalive**) y acciones de recuperación. El interruptor de keepalive (`_cambiar_keepalive` → `_arrancar_keepalive` / `_parar_hilo_keepalive` → `_bucle_keepalive`) manda telemetría cada `INTERVALO_KEEPALIVE` (25 s, medido: el panel se apaga ~1 min sin tráfico) **solo cuando ni el dashboard ni el vídeo están corriendo**; el hilo usa `compartido.sesion()` en cada trama, así que sobrevive a la reconexión del panel, y se restaura solo si quedó activado en la sesión anterior. |
| **Patrones** | Galería de patrones de prueba con miniaturas (`pagina_patrones`, `_GaleriaPatrones`). |
| **Fondos** | Galería de fondos generados por código (`pagina_fondos`): cinco composiciones en bandas horizontales a 1920×462 (`degradado-azul`, `montanas`, `olas`, `estrellas`, `ciudad`), con previsualización y subida a la capa fondo. Sigue las reglas de `docs/PROMPT_FOTOS.md`. |
| **Editor** | Editor del JSON del tema con validación en vivo, previa EN MEMORIA (`temas.renderizar_datos`) y subida (`pagina_temas`, `_EditorTemas`). |
| **Paletas** | Paletas de color con muestra, contraste y variantes (`pagina_paletas`). |
| **Imagen** | Subir ficheros, previsualizar y elegir capa. |
| **Temas** | Gestión de temas guardados (cargar/guardar/borrar). |
| **Dashboard** | Control del dashboard: perfil, secciones, arranque/parada. |
| **Video** | Reproducción de GIF/vídeo/stream con los ajustes de `video.py` (incluye la nota de 60 Hz del modo vídeo). |
| **Diagnóstico** | `Panel.diagnostico()` con resultados accionables. |

Otros bloques de `ventana.py`: el diálogo de ficheros y su testabilidad
(`TestDialogoDeFicheros`), el manejo de sesión compartida (`compartido.sesion()`), la
vigilancia de desconexión del panel y la ayuda de atajos (Ctrl+6 entre ellos).

### paginas_extra.py — las páginas auxiliares

Utilerías compartidas (`_avisar`, `_subir_bytes`, `_panel_compartido`, `_nombre_seguro`),
galerías (`_GaleriaPatrones`, la de fondos con `_fondo_bytes` y las funciones `_fondo_*`),
el editor de temas y las paletas (con `_muestra_color`, `_contraste`, `_luminancia` para
asegurar legibilidad).

---

## herramientas/ — scripts de apoyo

| Script | Qué hace |
|---|---|
| `instalar.sh` | Permisos udev + instalación de órdenes y servicio. |
| `capturar_pantalla.py` | Captura del escritorio por el portal XDG: `capturar_bytes()` (PNG en memoria, para `stream`) y `capturar(destino)` (CLI). |
| `capturar_ventana.py` | Captura de una ventana concreta. |
| `analizar_tramas.py` | Análisis fuera de línea de capturas de tramas. |
| `sondear_canal.py` | Sondeos manuales del canal (la base de la ingeniería inversa). |
| `medir_fps.py / medir_tamano.py` | Las mediciones publicadas en `RENDIMIENTO.md`. |
| `validar_inactividad.py` | Verifica el apagado por espera de tráfico. |
| `generar_prompts.py / generar_prompt_temas.py` | Regeneran los `PROMPT_*.md` desde el código real. |
| `windows/sondas/vigilar_boot.js` | Sonda del banco Windows: vigila `bootFinish` sobreviviendo a la desconexión USB. |

## tests/

| Fichero | Cubre |
|---|---|
| `test_protocolo.py` | Framing, escapes, checksum, parseo de respuestas. |
| `test_simulador.py` | El `PanelFalso` contra el cliente real. |
| `test_ejemplos.py` | Los temas y ejemplos del repo se cargan y validan. |
| `test_temas.py` | Validador y renderizador de temas. |
| `test_video.py` | Fuentes (GIF, secuencia, vídeo con capturador falso, pantalla con capturador inyectado), ajustes y `Reproductor`. |
| `test_rendimiento.py` | Regresiones de tiempo de render. |
| `test_regresiones.py` | Bugs concretos ya corregidos, incluido el diálogo de ficheros GTK. |

Cobertura medida: **110 passed + 4 skipped** (el único test dependiente de `gi` corre en un
equipo con GTK4 instalado).
