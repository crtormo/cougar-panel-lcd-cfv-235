# ESQUEMA_TEMA.md — el JSON que dibuja la pantalla

Un tema es un objeto JSON normal y corriente. El motor lo lee, dibuja un PNG de **1920×462** y
ese PNG se sube al panel. Nada más: no hay plantillas ni binarios intermedios, así que tu
editor puede guardar y cargar temas con cualquier herramienta.

```json
{
  "version": 1,
  "ancho": 1920,
  "alto": 462,
  "fondo": "#0e1117",
  "widgets": [
    {"tipo": "reloj", "x": 60, "y": 52, "tamano": 122, "formato": "%H:%M"},
    {"tipo": "dato",  "x": 680, "y": 60, "ancho": 340, "alto": 342,
     "etiqueta": "CPU", "fuente": "CPU Usage", "unidad": "%",
     "detalle": "{cpu_temp} C      {cpu_mhz} MHz"},
    {"tipo": "grafica", "x": 60, "y": 300, "ancho": 570, "alto": 142,
     "etiqueta": "CPU (%)", "fuente": "CPU Usage", "puntos": 120, "auto": True},
    {"tipo": "texto", "x": 66, "y": 226, "tamano": 30, "color": "#788291", "texto": "{fecha}"}
  ]
}
```

Programáticamente: `cougar.temas.tema_por_defecto()`, `temas.plantilla(tipo, **campos)`,
`temas.validar(tema)`, `temas.normalizar(tema)`, `temas.renderizar(tema, "salida.png")` y
`temas.renderizar_datos(tema)` (bytes PNG en memoria).

---

## Claves de arriba

| Clave | Tipo | Por defecto | Qué es |
|---|---|---|---|
| `version` | int | `1` | versión del formato |
| `ancho`, `alto` | int | `1920`, `462` | tamaño del lienzo; el panel es fijo, otro valor es un aviso del validador |
| `fondo` | color | `#0e1117` | color de fondo del lienzo |
| `widgets` | lista | `[]` | los widgets, **en orden de dibujo** (el último queda encima) |

Colores: `#RRGGBB`. Paleta del ejemplo: fondo `#0e1117`, tarjeta `#161b24`, borde `#2c3442`,
texto `#ebf0f8`, gris `#788291`, acento `#00d0ff`, acento2 `#ff00c8`.

## Campos comunes a todos los widgets

| Clave | Tipo | Por defecto | Qué es |
|---|---|---|---|
| `tipo` | str | — | `dato`, `barra`, `grafica`, `texto`, `reloj` (alias: `clock`) |
| `x`, `y` | int | `0`, `0` | esquina superior izquierda, en píxeles |

## `dato` — tarjeta con valor grande

Tarjeta redondeada con rótulo, número grande (que se encoge solo si no cabe), unidad, línea de
detalle y barra de progreso.

| Clave | Tipo | Por defecto | Qué es |
|---|---|---|---|
| `ancho`, `alto` | int | `340`, `300` | tamaño de la tarjeta |
| `etiqueta` | str | `""` | rótulo pequeño de arriba |
| `fuente` | str | `""` | **obligatoria**: nombre del editor (`CPU Usage`) o clave interna (`cpu_uso`) |
| `unidad` | str | `""` | se dibuja pequeña junto al valor |
| `detalle` | str | `""` | línea de abajo; admite marcadores |
| `tamano` | int | `74` | cuerpo del valor |
| `tamano_detalle` | int | `30` | cuerpo del detalle |
| `barra` | bool | `true` | dibujar la barra de progreso |
| `mostrar_porcentaje` | bool | `false` | escribir el `%` sobre la barra |
| `min`, `max` | int | `0`, `100` | rango de la barra |
| `radio` | int | `18` | redondeo de esquinas |
| `color` | color | `#ebf0f8` | color del valor |
| `color_etiqueta` | color | `#788291` | rótulo y unidad |
| `color_detalle` | color | `#9aa6b8` | línea de detalle |
| `color_relleno` | color | `#00d0ff` | barra |
| `fondo`, `borde` | color | `#161b24`, `#2c3442` | tarjeta |

## `barra` — barra horizontal

| Clave | Tipo | Por defecto | Qué es |
|---|---|---|---|
| `ancho`, `alto` | int | `500`, `34` | largo y grosor |
| `etiqueta` | str | `""` | rótulo **encima** de la barra |
| `fuente` | str | `""` | obligatoria |
| `unidad` | str | `"%"` | unidad |
| `min`, `max` | int | `0`, `100` | rango |
| `radio` | int | `12` | redondeo |
| `color` | color | `#ebf0f8` | color del valor |
| `color_etiqueta` | color | `#788291` | rótulo |
| `color_relleno` | color | `#00d0ff` | barra |

## `grafica` — historial

Necesita **al menos dos muestras** para dibujar línea (el historial lo lleva `Fuentes`, hasta
300 valores). Es la que da vida al dashboard.

| Clave | Tipo | Por defecto | Qué es |
|---|---|---|---|
| `ancho`, `alto` | int | `560`, `120` | tamaño |
| `etiqueta` | str | `""` | rótulo |
| `fuente` | str | `""` | obligatoria |
| `puntos` | int | `120` | cuántos valores del historial se dibujan |
| `auto` | bool | `false` | escala automática según los datos (si no, usa `min`/`max`) |
| `min`, `max` | int | `0`, `100` | rango fijo |
| `grosor` | int | `3` | grosor de la línea |
| `radio` | int | `14` | redondeo del marco |
| `unidad` | str | `""` | unidad del valor actual |
| `color` | color | `#ebf0f8` | valor actual (arriba a la derecha) |
| `color_etiqueta` | color | `#788291` | rótulo |
| `color_relleno` | color | `#00d0ff` | línea de la gráfica |
| `fondo`, `borde` | color | `#161b24`, `#2c3442` | marco |

## `texto` — línea libre

| Clave | Tipo | Por defecto | Qué es |
|---|---|---|---|
| `texto` | str | `""` | admite marcadores `{clave}` y `{fuente:Nombre del editor}` |
| `tamano` | int | `28` | cuerpo (se encoge solo si no cabe) |
| `ancho` | int | `0` | límite de ancho; `0` = hasta el borde del panel |
| `color` | color | `#ebf0f8` | color |

## `reloj` — hora

| Clave | Tipo | Por defecto | Qué es |
|---|---|---|---|
| `formato` | str | `"%H:%M"` | formato `strftime`: `%H:%M`, `%H:%M:%S`, `%A %d`… |
| `tamano` | int | `120` | cuerpo |
| `color` | color | `#ebf0f8` | color |

---

## Marcadores

En `texto`, `detalle` y cualquier texto se sustituye `{clave}` por el valor actual del dato.
Formato de presentación: entero para `%`, un decimal para el resto, `--` si no hay dato.

- por clave interna: `{cpu_temp}`, `{ram_uso}`, `{disco_uso}`, `{hora}`, `{fecha}`, `{uptime}`…
- por nombre del editor: `{fuente:CPU Usage}`
- `cougar.temas.marcadores()` devuelve la lista completa.

## Fuentes de datos

`cougar.temas.fuentes_disponibles()` devuelve `editor` (los **35 nombres** del editor oficial),
`propias` (las claves internas) y `valores` (lo que se lee ahora mismo en tu máquina).

Nombres del editor: `CPU Temperature`, `CPU Usage`, `CPU Hz`, `CPU Power`, `CPU Fan`,
`CPU Brand`, `CPU Platform`, `CPU ID`, `GPU Temperature`, `GPU Usage`, `GPU Hz`, `GPU Power`,
`GPU Fan Speed`, `Memory Usage`, `Memory Size`, `Memory Type`, `Memory Speed`,
`Memory Channels Support`, `Disk Space`, `Motherboard Name`, `Motherboard Chipset`,
`Network Card`, `System Information`, `Fan Control` y sus variantes de nombre.

De dónde sale cada valor en Linux, en `DESARROLLO.md` y en `docs/PROTOCOLO.md`.

---

## Reglas del validador

`temas.validar(tema)` devuelve la lista de problemas (vacía = perfecto). Comprueba:

| Problema | Gravedad |
|---|---|
| el tema no es un objeto, o falta `widgets` | error |
| tamaño distinto de 1920×462 | aviso |
| `tipo` desconocido | error |
| `x`/`y` fuera del panel, o no enteros | error |
| falta `fuente` en `dato`/`barra`/`grafica` | error |
| `fuente` que no existe | aviso (`--` en pantalla); con `estricto=True`, error |
| color que no es `#RRGGBB` | aviso |
| `max` ≤ `min` | aviso |
| formato de hora inválido | aviso |
| campos del editor oficial (`showPrefix`, `addZero`, `rotate`, `maxTickCount`, `cornerRadius`) | aviso: se ignoran |

`temas.normalizar(tema)` rellena todos los campos que falten con sus valores por defecto y
descarta los widgets de tipo desconocido: útil antes de guardar desde una interfaz gráfica.

## Ejemplos listos

- `ejemplos/tema_minimo.json` — reloj, texto y una tarjeta (3 widgets).
- `ejemplos/tema_dashboard.json` — el dashboard completo (8 widgets).
- `ejemplos/tema_graficas.json` — tres gráficas y tres tarjetas (6 widgets).

```bash
./herramientas/cougar tema ejemplos/tema_graficas.json --subir
python3 -m cougar.patrones --todos /tmp/patrones   # patrones de calibración de la pantalla
```
