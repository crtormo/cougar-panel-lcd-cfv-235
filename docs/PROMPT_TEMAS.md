# Prompt para generar temas del panel COUGAR CFV235

Copia **todo este documento** y pégalo en la IA que prefieras. Al final hay ejemplos y una
plantilla de petición; cambia solo la última línea para pedir el tema que quieras.

---

## 1. Tu tarea

Eres un diseñador de paneles. Vas a generar un **tema JSON** para el panel LCD de una caja de
PC (**COUGAR CFV235**), que se dibuja a **1920 × 462 píxeles** y se muestra a distancia.

Devuelve **solo el JSON**, sin explicaciones, sin markdown alrededor y sin comentarios dentro
del JSON. Si te pido cambios, devuelve el JSON completo otra vez.

## 2. El panel (especificaciones técnicas)

| Dato | Valor |
|---|---|
| Modelo | COUGAR CFV235 (LCD de la caja), 9,16 pulgadas |
| Resolución | **1920 × 462 px** (apaisado, muy panorámico: proporción 4.16:1) |
| Formato de imagen que muestra | **PNG** (el panel acepta JPEG y GIF, pero el GIF sale en blanco) |
| Color | fondo opaco; no cuentes con transparencias útiles |
| Distancia de visionado | 1-2 m: **texto grande y contraste alto** |
| Refresco | el host sube una imagen nueva cada 1-2 s como máximo |
| Contenido encima | puede haber una capa OSD encima del fondo (evita detalles en los bordes) |

Zona segura: deja **24 px** de margen a los lados y arriba/abajo. La rejilla que mejor
funciona es de **4 columnas** de 453 px con 20 px de separación; sus esquinas
izquierdas son x = 24, 497, 970, 1443.

## 3. Formato del tema

```json
{
  "nombre": "mi tema",
  "fondo": "#0b0e14",
  "widgets": [ { "tipo": "texto", "texto": "HOLA", "x": 40, "y": 40, "tamano": 60 } ]
}
```

- `fondo`: color del lienzo completo.
- `widgets`: lista de elementos. Se dibujan **en orden**: el último queda encima.
- Cada widget tiene `tipo`, `x` (desde la izquierda) e `y` (desde arriba).

### Colores

Se aceptan `"#rgb"`, `"#rrggbb"`, `"rrggbb"` y `[r, g, b]`. Si un color está mal escrito, el
programa avisa y usa blanco: **no rompas la estética por un color raro, pero escríbelos bien**.

## 4. Tipos de widget y sus campos

### `dato`  (alias: data)

Tarjeta con etiqueta, valor grande, detalle y barra de progreso.

| campo | tipo | por defecto | para qué |
|---|---|---|---|
| `x` | int | `0` | esquina izquierda |
| `y` | int | `0` | borde superior |
| `ancho` | int | `340` | ancho de la tarjeta |
| `alto` | int | `300` | alto de la tarjeta |
| `etiqueta` | str | `""` | titulo pequeno de arriba |
| `fuente` | str | `""` | fuente de datos (nombre del editor o clave interna) |
| `unidad` | str | `""` | se dibuja pequena junto al valor |
| `sufijo` | str | `""` | como 'unidad', pero con el espacio incluido (' C') |
| `detalle` | str | `""` | linea de abajo; admite {marcadores} |
| `tamano` | int | `74` | cuerpo del valor (se encoge solo si no cabe) |
| `tamano_detalle` | int | `30` | cuerpo de la linea de detalle |
| `barra` | bool | `True` | dibujar la barra de progreso |
| `mostrar_porcentaje` | bool | `False` | escribir el % sobre la barra |
| `radio` | int | `18` | redondeo de las esquinas |
| `min` | int | `0` | minimo de la barra |
| `max` | int | `100` | maximo de la barra |
| `color` | str | `"#ebf0f8"` | color del valor |
| `color_etiqueta` | str | `"#788291"` | color de etiqueta y unidad |
| `color_detalle` | str | `"#9aa6b8"` | color de la linea de detalle |
| `color_relleno` | str | `"#00d0ff"` | color de la barra |
| `fondo` | str | `"#161b24"` | fondo de la tarjeta |
| `borde` | str | `"#2c3442"` | borde de la tarjeta |

### `barra`  (alias: bar)

Barra horizontal con etiqueta y valor.

| campo | tipo | por defecto | para qué |
|---|---|---|---|
| `x` | int | `0` | esquina izquierda |
| `y` | int | `0` | borde superior |
| `ancho` | int | `500` | largo de la barra |
| `alto` | int | `34` | grosor |
| `etiqueta` | str | `""` | rotulo encima |
| `fuente` | str | `""` | fuente de datos |
| `unidad` | str | `"%"` | unidad |
| `sufijo` | str | `""` | como 'unidad', pero con el espacio incluido |
| `min` | int | `0` | minimo |
| `max` | int | `100` | maximo |
| `color` | str | `"#ebf0f8"` | color del valor |
| `color_etiqueta` | str | `"#788291"` | color del rotulo |
| `color_relleno` | str | `"#00d0ff"` | color de la barra |
| `fondo` | str | `"#262d3a"` | color de la pista |

### `grafica`  (alias: chart)

Grafica de linea con historial (necesita dos muestras para dibujar).

| campo | tipo | por defecto | para qué |
|---|---|---|---|
| `x` | int | `0` | esquina izquierda |
| `y` | int | `0` | borde superior |
| `ancho` | int | `560` | ancho |
| `alto` | int | `120` | alto |
| `etiqueta` | str | `""` | rotulo |
| `fuente` | str | `""` | fuente de datos |
| `puntos` | int | `120` | cuantos valores del historial se dibujan |
| `auto` | bool | `False` | escala automatica con los datos (si no, usa min/max) |
| `min` | int | `0` | minimo fijo |
| `max` | int | `100` | maximo fijo |
| `grosor` | int | `3` | grosor de la linea |
| `radio` | int | `14` | redondeo del marco |
| `unidad` | str | `""` | unidad |
| `sufijo` | str | `""` | como 'unidad', pero con el espacio incluido |
| `color` | str | `"#ebf0f8"` | color del valor actual |
| `color_etiqueta` | str | `"#788291"` | color del rotulo |
| `color_relleno` | str | `"#00d0ff"` | color de la linea |
| `fondo` | str | `"#161b24"` | fondo |
| `borde` | str | `"#2c3442"` | borde |

### `texto`  (alias: text)

Linea (o varias) de texto libre con {marcadores} de datos.

| campo | tipo | por defecto | para qué |
|---|---|---|---|
| `x` | int | `0` | esquina izquierda |
| `y` | int | `0` | borde superior |
| `texto` | str | `""` | texto; admite {clave}, {clave:.1f} y {fuente:Nombre} |
| `tamano` | int | `28` | cuerpo (se encoge solo si no cabe) |
| `ancho` | int | `0` | limite de ancho; 0 = hasta el borde del panel |
| `color` | str | `"#ebf0f8"` | color |

### `reloj`  (alias: clock)

Reloj con formato de strftime.

| campo | tipo | por defecto | para qué |
|---|---|---|---|
| `x` | int | `0` | esquina izquierda |
| `y` | int | `0` | borde superior |
| `formato` | str | `"%H:%M"` | formato strftime, p. ej. %H:%M o %H:%M:%S |
| `tamano` | int | `120` | cuerpo |
| `ancho` | int | `0` | limite de ancho; 0 = hasta el borde del panel |
| `color` | str | `"#ebf0f8"` | color |

## 5. Datos disponibles

Estas son las claves que puedes usar tanto en el campo `fuente` como dentro de `{marcadores}`
en los textos. **Si un dato no existe en el equipo, se dibuja `--`**: no pasa nada, pero no
construyas un panel entero sobre un dato que puede faltar.

| clave (para `fuente` y para `{marcadores}`) | qué es | unidad |
|---|---|---|
| `cpu_uso` | Uso de CPU | % |
| `cpu_temp` | Temperatura de CPU | C |
| `cpu_mhz` | Frecuencia de CPU | MHz |
| `cpu_modelo` | Modelo de CPU | - |
| `cpu_nucleos` | Nucleos de CPU | - |
| `cpu_vent` | Ventilador de CPU | RPM |
| `ram_total_gb` | RAM total | GB |
| `ram_usado_gb` | RAM usada | GB |
| `ram_uso` | Uso de RAM | % |
| `ram_velocidad` | Velocidad de RAM | MHz |
| `gpu_uso` | Uso de GPU | % |
| `gpu_temp` | Temperatura de GPU | C |
| `gpu_mhz` | Frecuencia de GPU | MHz |
| `gpu_vram_usado_gb` | VRAM usada | GB |
| `gpu_vent` | Ventilador de GPU | RPM |
| `disco_total_gb` | Disco total | GB |
| `disco_usado_gb` | Disco usado | GB |
| `disco_uso` | Uso de disco | % |
| `disco_lectura_mb` | Lectura de disco | MB/s |
| `disco_escritura_mb` | Escritura de disco | MB/s |
| `red_subida_mb` | Subida de red | MB/s |
| `red_bajada_mb` | Bajada de red | MB/s |
| `red_total_gb` | Trafico de red | GB |
| `carga_1m` | Carga 1 min | - |
| `carga_5m` | Carga 5 min | - |
| `carga_15m` | Carga 15 min | - |
| `uptime_h` | Encendido | h |
| `procesos` | Procesos | - |
| `bateria` | Bateria | % |
| `bomba_vent` | Bomba de agua | RPM |
| `chipset_temp` | Temperatura de chipset | C |

### Marcadores en los textos

Los campos `texto` y `detalle` aceptan marcadores que se rellenan con el valor del momento:

```
{cpu_temp}
{cpu_uso:.0f}
{ram_usado_gb:.1f}
{hora}
{fecha}
{uptime}
{fuente:CPU Usage}
```

Ejemplos: `"{cpu_temp:.0f} C"`, `"{ram_uso:.1f} %"`, `"{cpu_uso:.0f}"`, `"{fuente:CPU Usage}"`.
Se admite el formato de Python (`:.0f`, `:.1f`, `:.2f`).

## 6. Reglas de diseño (importantes)

1. **Nada se puede solapar ni salirse.** Comprueba que `x + ancho <= 1920` y
   `y + alto <= 462`. Es el error más común y arruina el panel.
2. **Ojo con el widget `dato`**: ocupa `ancho × alto` (por defecto **340 × 300**) y dibuja su
   propia barra en `alto - 102`. Si lo usas con su barra interna, dale `alto` mayor que
   `tamano + 232`; si quieres una tarjeta compacta, pon `"barra": false` y dibuja la barra
   aparte con un widget `barra`. **Nunca dejes el `alto` por defecto si pones algo debajo.**
3. **La barra del widget `barra` escribe el valor a la derecha**: deja unos **90 px** libres
   (`ancho` = ancho de columna - 90).
4. **Tamaños legibles**: valores grandes entre 46 y 74; etiquetas 20-26; textos de apoyo 18-22.
5. **Jerarquía**: 1-2 datos protagonistas por zona, el resto en gris. No llenes los 462 px
   de altura: el panel es muy apaisado, así que aprovecha el ancho y agrupa en columnas.
6. **Paleta**: fondo oscuro (#0b0e14, #11151c) y un color por métrica (naranja CPU, verde GPU,
   azul memoria, ámbar disco, cian red). Texto principal casi blanco (#e8edf5) y secundario
   gris (#8a94a6).
7. **Un dato, un sitio**: no repitas la misma métrica en dos widgets (confunde y ocupa).

## 7. Qué rechaza el programa (evítalo)

- Tipos de widget que no existen.
- `x + ancho > 1920` o `y + alto > 462`.
- `tamano <= 0` o no numérico, `puntos < 1`, `radio < 0`, `min`/`max` no numéricos.
- `x` o `y` con valor booleano.
- Fuentes (tipografías) o colores inexistentes.

Si el validador encuentra algo, lo dice con el número de widget y el campo: no lo ignores.

## 8. Ejemplo mínimo (3 widgets)

```json
{
  "nombre": "minimo",
  "fondo": "#0b0e14",
  "widgets": [
    {"tipo": "texto", "texto": "MI PC", "x": 24, "y": 24, "tamano": 40, "color": "#00b4ff"},
    {"tipo": "dato", "etiqueta": "CPU", "fuente": "cpu_temp", "sufijo": " C",
      "x": 24, "y": 100, "ancho": 453, "alto": 144, "tamano": 52, "barra": false,
      "color": "#ff9f43", "fondo": "#141922", "borde": "#1f2836"},
    {"tipo": "reloj", "formato": "%H:%M", "x": 1608, "y": 24, "tamano": 46,
      "color": "#e8edf5"}
  ]
}
```

## 9. Ejemplo completo (4 columnas de métricas + gráfica)

Fíjate en la rejilla: mismas `y` para las tarjetas, mismas `y` para las barras, y el detalle
debajo. Copia esta estructura y cambia lo que quieras.

```json
{
  "nombre": "dashboard",
  "fondo": "#0b0e14",
  "widgets": [
    {"tipo": "texto", "texto": "CFV 235", "x": 24, "y": 18, "tamano": 34, "color": "#00b4ff"},
    {"tipo": "reloj", "formato": "%H:%M", "x": 1608, "y": 18, "tamano": 46, "color": "#e8edf5"},

    {"tipo": "dato", "etiqueta": "CPU", "fuente": "cpu_temp", "sufijo": " C",
      "x": 24, "y": 92, "ancho": 453, "alto": 144, "tamano": 52, "barra": false,
      "color": "#ff9f43", "fondo": "#141922", "borde": "#1f2836"},
    {"tipo": "barra", "x": 24, "y": 252, "ancho": 367, "alto": 18, "etiqueta": "",
      "fuente": "cpu_uso", "color_relleno": "#ff9f43", "color": "#e8edf5"},
    {"tipo": "texto", "texto": "carga {cpu_uso:.0f} %     {cpu_mhz:.0f} MHz",
      "x": 24, "y": 282, "tamano": 21, "color": "#8a94a6"},

    {"tipo": "dato", "etiqueta": "GPU", "fuente": "gpu_temp", "sufijo": " C",
      "x": 497, "y": 92, "ancho": 453, "alto": 144, "tamano": 52, "barra": false,
      "color": "#4cd137", "fondo": "#141922", "borde": "#1f2836"},
    {"tipo": "barra", "x": 497, "y": 252, "ancho": 367, "alto": 18, "etiqueta": "",
      "fuente": "gpu_uso", "color_relleno": "#4cd137", "color": "#e8edf5"},
    {"tipo": "texto", "texto": "uso {gpu_uso:.0f} %     {gpu_mhz:.0f} MHz",
      "x": 497, "y": 282, "tamano": 21, "color": "#8a94a6"},

    {"tipo": "dato", "etiqueta": "MEMORIA", "fuente": "ram_uso", "sufijo": " %",
      "x": 970, "y": 92, "ancho": 453, "alto": 144, "tamano": 52, "barra": false,
      "color": "#00b4ff", "fondo": "#141922", "borde": "#1f2836"},
    {"tipo": "barra", "x": 970, "y": 252, "ancho": 367, "alto": 18, "etiqueta": "",
      "fuente": "ram_uso", "color_relleno": "#00b4ff", "color": "#e8edf5"},
    {"tipo": "texto", "texto": "{ram_usado_gb:.1f} / {ram_total_gb:.1f} GB",
      "x": 970, "y": 282, "tamano": 21, "color": "#8a94a6"},

    {"tipo": "dato", "etiqueta": "DISCO", "fuente": "disco_uso", "sufijo": " %",
      "x": 1443, "y": 92, "ancho": 453, "alto": 144, "tamano": 52, "barra": false,
      "color": "#e1b12c", "fondo": "#141922", "borde": "#1f2836"},
    {"tipo": "barra", "x": 1443, "y": 252, "ancho": 367, "alto": 18, "etiqueta": "",
      "fuente": "disco_uso", "color_relleno": "#e1b12c", "color": "#e8edf5"},
    {"tipo": "texto", "texto": "{disco_usado_gb:.0f} / {disco_total_gb:.0f} GB",
      "x": 1443, "y": 282, "tamano": 21, "color": "#8a94a6"},

    {"tipo": "grafica", "x": 24, "y": 316, "ancho": 1080, "alto": 96,
      "etiqueta": "USO DE CPU (%)", "fuente": "cpu_uso", "puntos": 120, "auto": true,
      "color": "#ff6b6b", "grosor": 3, "fondo": "#141922", "borde": "#1f2836"},
    {"tipo": "texto", "texto": "RED    bajada {red_bajada_mb:.2f} MB/s    subida {red_subida_mb:.2f} MB/s\nVENT   cpu {cpu_vent:.0f} rpm",
      "x": 1152, "y": 334, "tamano": 22, "color": "#e8edf5", "ancho": 744}
  ]
}
```

## 10. Qué NO hagas

- No inventes campos ni tipos de widget que no estén en la tabla.
- No pongas `ancho`/`alto` a un `texto` esperando que se ajuste solo: el texto se dibuja desde
  `x` y, si es largo, se encoge hasta caber en el ancho disponible.
- No uses más de 20-25 widgets: el panel refresca subiendo una imagen y cuanto más compleja,
  más tarda.
- No repitas el mismo dato en dos sitios.
- No dejes ningún widget fuera del lienzo.

---

## 11. Cómo probar el tema que te devuelva la IA

Guarda el JSON en un fichero y pásalo por la app: te dirá si algo no cuadra **antes** de
subirlo al panel.

```bash
cfv235 tema mi_tema.json                 # dibuja el PNG y lista los problemas
cfv235 tema mi_tema.json --png /tmp/vista.png   # verlo sin panel
cfv235 tema mi_tema.json --subir --osd   # mandarlo al panel (capa OSD)
cfv235 subir /tmp/vista.png --osd        # o subir el PNG ya dibujado
```

El comando devuelve la lista de problemas con el numero de widget y el campo, y avisa si un
texto se sale del panel o si un color esta mal escrito.

---

## Plantilla de petición

> Con las especificaciones de arriba, genera un tema para el panel con:
> - título «__________»
> - métricas principales: __________
> - estilo: __________ (por ejemplo: minimalista oscuro, tipo panel de coche, retro verde)
> - el resto de cosas que quieras: __________
>
> Devuelve solo el JSON.
