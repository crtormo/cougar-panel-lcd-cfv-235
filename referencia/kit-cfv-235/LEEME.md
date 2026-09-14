# cfv-235-linux — kit para desarrollar tu propio editor del panel COUGAR CFV235

Este panel (9.16", **1920×462**, USB `1d6b:0126`) se puede manejar por completo desde Linux:
todo lo que hace el COUGAR LCD Editor de Windows (fondos, temas, widgets, telemetría) está
aquí en Python, sin Wine, sin WSL y sin el programa de COUGAR.

Esta carpeta no es un dashboard: es el **kit para que escribas el editor a tu manera**. Trae
la biblioteca, un editor visual que ya funciona como punto de partida, un **panel simulado**
para desarrollar sin hardware, patrones de calibración y la documentación del protocolo
medido byte a byte sobre el panel real.

```bash
./herramientas/probar.sh                     # comprueba entorno y panel, sin cambiar nada
python3 -m cougar.editor --abrir             # editor visual (http://127.0.0.1:8777)
python3 -m cougar.simulador --traza          # panel falso + un /dev/pts/N al que apuntar
./herramientas/cougar --help                 # orden de línea de comandos
./pruebas/ejecutar.sh                        # todas las pruebas
```

Antes de tocar código, lee **`DESARROLLO.md`** (arquitectura y las trampas del panel) y
**`ESQUEMA_TEMA.md`** (el formato JSON que dibuja la pantalla).

---

## Qué hay dentro

| Carpeta / fichero | Para qué |
|---|---|
| `cougar/` | **La biblioteca.** `protocolo` (tramas), `panel` (el objeto que habla con el panel), `temas`, `widgets` (motor de dibujo), `fuentes`+`sensores` (datos del PC), `editor` (backend HTTP + `editor.html`), `simulador`, `patrones`, `cli` |
| `herramientas/cougar` | Orden de línea de comandos: `listar`, `conn`, `estado`, `subir`, `patron`, `tema`, `bucle`, `raw`, `escuchar`… |
| `herramientas/instalar.sh` | Instala el paquete, la orden `cougar`, la regla udev y (opcional) el dashboard |
| `herramientas/probar.sh` | Diagnóstico de menos a más: dependencias → pruebas → panel → sensores → dibujo |
| `herramientas/probar_tramas.py` | Analizador de tramas en crudo (sin panel) |
| `herramientas/analizar_log.py` | Saca estadísticas de un log de escrituras del editor |
| `pruebas/` | Pruebas del protocolo (contra capturas reales), de temas y del simulador |
| `ejemplos/` | Temas JSON: mínimo, dashboard completo y uno de gráficas |
| `docs/` | `PROTOCOLO.md` (lo medido), `API_DEL_EDITOR.md`, `FUNCIONES_DEL_EDITOR.md`, `INGENIERIA_INVERSA.md` y las revisiones del código de otros proyectos |
| `referencia/cougar_panel.py` | La implementación original, sin tocar. `pruebas/test_protocolo.py` comprueba que la biblioteca produce **exactamente las mismas tramas** |
| `referencia-banco-windows/` | El banco de pruebas de Windows: sonda en Node, gancho para espiar al editor oficial por su inspector y el primer dashboard en PowerShell |
| `tramas_reales/` | Respuestas reales del panel y tramas documentadas con su checksum |

## Y `docs/PROTOCOLO.md` es el que manda

Si algo de este kit y `docs/PROTOCOLO.md` se contradicen, gana el documento: todo lo que hay
ahí se midió sobre el panel.

---

## Lo que está probado y lo que no

**Probado** (sin panel delante, con `./pruebas/ejecutar.sh`):

- el protocolo, reconstruyendo **byte a byte** 8 tramas documentadas y decodificando las dos
  respuestas reales de `tramas_reales/` (incluida la de 371 B con escapes);
- la coherencia con la implementación original: mismas tramas, mismo checksum, mismos
  informes de medios;
- el motor de dibujo: temas de ejemplo renderizados a 1920×462, y el validador detectando
  tipos desconocidos, coordenadas fuera del panel, fuentes inexistentes y colores inválidos;
- el simulador: acepta una subida completa y la rechaza **igual que el panel real** cuando los
  bloques llegan tarde (acuse `1 400 AckNumber=0` y `transported` con 200 y cuerpo vacío).

**Sin probar en Linux** (este PC es Windows): el acceso a `/dev/hidraw` y los permisos, las
lecturas de `hwmon`/`thermal`/CPUFreq, el PTY del simulador y los guiones `instalar.sh` /
`probar.sh`. En Windows sí se probó, contra el panel de verdad, todo el protocolo y las
subidas: la implementación de la que sale esta biblioteca es la que funcionó.
