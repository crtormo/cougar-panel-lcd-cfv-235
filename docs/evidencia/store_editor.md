# store.json del editor de COUGAR (índice de temas y medios)

Capturado el 2026-09-14 desde `%APPDATA%\cougar_lcd_editor\store.json` (133 KB), en el equipo
del banco de pruebas. Resuelve el pendiente **#5** y parte del **#2** de `docs/PENDIENTE_WINDOWS.md`.

## Índice de temas (`presetTheme`, 8) — pendiente #5

Los `id` son marcas de tiempo Unix en **milisegundos**.

| id | Título |
|---|---|
| 1722413507022 | Default Theme |
| 1723630367584 | Default Theme for 480X480 |
| 1749264214769 | Default Theme for 1920X462 |
| 1753751960077 | COUGAR_02 |
| 1753751965867 | COUGAR_03 |
| 1753751968220 | COUGAR_04 |
| 1753751971925 | COUGAR_05 |
| 1753751975326 | COUGAR_06 |

Hay además dos temas personalizados (`customizationTheme`, ids `1789257523661` y
`1789278813238`) con la estructura completa del tema (`id`, `title`, `url`, `widget[]`,
`background`, `antvX6`, `ratio`, `dpi`). El "Default Theme for 480X480" confirma que el mismo
editor maneja paneles de **480×480** y de **1920×462**.

## Lista de medios del editor (`waterBlockScreenCustomMedia`, 3) — pendiente #2 (la lista)

Cada medio es un objeto `{type, path, ratio, name, ...}`:

| type | name | ratio | ruta |
|---|---|---|---|
| PNG | 2026-09-12_20-57-20-896.png | 320:77 | `media\temp\...896.png` |
| PNG | 2026-09-13_02-53-10-086.png | 320:77 | `media\temp\...086.png` |
| PNG | 2026-09-14_16-51-23-779.png | 320:77 | `media\temp\...779.png` |

- `ratio` **320:77** es 1920:462 simplificado (el panel real).
- **`2026-09-13_02-53-10-086.png` es exactamente el fichero que el panel restauró como fondo
  tras el `recovery`** (medido en la sesión del Reset): es decir, el "fondo configurado antes"
  que devuelve el reset es el último medio personalizado del editor. Une las dos mediciones.

## Lo que NO queda en disco (y por qué hace falta el CDP)

No hay ningún `.osd`, `.mp4`, `.gif` ni `.jpg` en la carpeta del editor: el `.osd` (pendiente
#3) y lo que se sube al cargar un vídeo/GIF (pendientes #6-9) se generan en memoria o en un
directorio temporal durante la subida y se borran. Para capturarlos hay que espiar al editor en
caliente por el inspector (`herramientas/windows/sondas/cdp_*.js`).

## Equipo del banco de pruebas

`specCPU`: AMD Ryzen 7 5800X3D · `specGPU`: AMD Radeon RX 9060 XT. `devicesConfig` guarda
configuración por número de serie (`BYZL2611WC01CM001018`).
