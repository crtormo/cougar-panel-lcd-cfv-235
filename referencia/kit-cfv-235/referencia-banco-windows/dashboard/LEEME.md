# `dashboard/` — panel con métricas propias, sin el editor

> ⚠️ **Superado por `widgets/`.** Este dashboard fue el primer paso: dibuja un diseño **fijo**
> con PowerShell. El **motor de widgets** (`widgets/`) hace lo mismo pero configurable por
> JSON, con más datos, gráficas y editor visual — y además funciona en Windows y Linux.
> Se mantiene aquí como referencia; para uso diario, usa `widgets/`.
>
> Revisando el PNG que genera (2026-09-13) se ven **dos defectos**: las barras de las tarjetas
> salen **sin relleno de color** y el texto del pie se **sale por la derecha**. El motor de
> widgets no tiene ninguno de los dos: ajusta el tamaño de letra solo y pinta las barras bien.

**Mira la pantalla del CFV235:** mientras esto corre, el panel deja de mostrar el tema del
editor y muestra **nuestro** dashboard (reloj, fecha y tarjetas de CPU / memoria / GPU), que
se redibuja y se sube al panel cada pocos segundos.

Todo el camino funciona sin el COUGAR LCD Editor: no hace falta que esté instalado ni abierto.

## Cómo se usa

```powershell
cd C:\Users\Maximo\Desktop\CFV235-Linux\sondas
$env:COUGAR_NODE_HID = "C:/Users/Maximo/AppData/Local/Temp/nodehid/nodehid.js"

# un fotograma suelto (solo dibujar, sin panel)
powershell -NoProfile -ExecutionPolicy Bypass -File ..\dashboard\panel-dashboard.ps1

# dashboard en marcha: un fotograma cada 5 s, sin límite (Ctrl+C para parar)
node cougar_hid_node.js bucle 5

# 12 fotogramas cada 5 s y terminar
node cougar_hid_node.js bucle 5 12
```

Variables: `COUGAR_RENDER` (ruta del guion que dibuja) y `COUGAR_PNG` (PNG intermedio).

## Cómo funciona

1. `panel-dashboard.ps1` lee las métricas y **dibuja un PNG de 1920×462** con `System.Drawing`
   (viene con Windows, no hay que instalar nada).
2. El cliente HID sube ese PNG con el protocolo del panel y **la sincronización obligatoria**:
   los bloques van ~90 ms después del `transport` y el `transported` ~10 ms después del último
   bloque (la sesión de transferencia caduca en menos de un segundo).
3. Entre fotogramas se manda `STATE all` cada segundo para que el panel **no se apague**.

## Métricas: por qué con CIM y no con `Get-Counter`

`Get-Counter` usa los nombres de los contadores **traducidos al idioma del sistema**: en un
Windows en español `\Processor(_Total)\% Processor Time` no existe y falla. Las clases CIM
(WMI) tienen nombres fijos, así que se usan estas:

| Métrica | Origen |
|---|---|
| CPU % | `Win32_PerfFormattedData_PerfOS_Processor` (`_Total`) |
| Memoria % | `Win32_OperatingSystem` (`FreePhysicalMemory` / `TotalVisibleMemorySize`) |
| Disco % | `Win32_PerfFormattedData_PerfDisk_PhysicalDisk` (`_Total`) |
| Red | `Win32_PerfFormattedData_Tcpip_NetworkInterface` (`BytesTotalPersec`) |
| GPU % | `Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine` (`engtype_3D`) |
| Temperatura CPU | opcional (`-Temperaturas`): del `sysinfo.json` del editor si existe |

Si una métrica no está disponible, la tarjeta muestra `--` y **el panel sigue funcionando**.

## Notas

- **Si ves restos de la imagen anterior por encima del dashboard**, la causa es la **capa OSD**:
  el editor la enciende al aplicar un tema (`osdState: 1`) y se dibuja por encima de nuestra
  imagen (que va como fondo). Se arregla con un Reset antes de arrancar el bucle:
  ```powershell
  node cougar_hid_node.js recovery    # borra los medios y deja osdState=0
  node cougar_hid_node.js bucle 5     # el bucle ya avisa si detecta osdState=1
  ```
- **Política de ejecución**: si Windows bloquea el `.ps1`, lánzalo con
  `powershell -NoProfile -ExecutionPolicy Bypass -File ...` (el cliente ya lo hace así).
- Cada fotograma tarda ~2,3 s (≈1,5 s de arrancar PowerShell + dibujar, y ~0,8 s de subida con
  el PNG de ~30 KB). Con un periodo de 4-5 s va sobrado; para algo más fluido habría que
  mantener el dibujo dentro del bucle en vez de lanzar un proceso por fotograma.
- **No** lo ejecutes a la vez que el COUGAR LCD Editor: solo un programa puede mandar en el
  panel.
- El panel **se apaga si no recibe flujo** y puede quedarse con **brillo 0**: si lo ves negro,
  mira `cougar_hid_node.js comprobar` (campo `brightness`).
