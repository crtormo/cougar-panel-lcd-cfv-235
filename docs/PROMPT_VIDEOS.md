# Prompt para generar VIDEOS del panel COUGAR CFV235

**Lo primero**: el panel **no reproduce video**. No tiene decodificador: lo que muestra son
**fotogramas PNG** que le manda el PC. Un video sirve como **fuente**: la app lo abre con
GStreamer, saca los fotogramas y los sube uno a uno (`cfv235 video pelicula.mp4`).

Es decir: el video es la forma comoda de **disenar y montar** la animacion; lo que ve el panel
son PNG a **2 fotogramas por segundo**.

---

## 1. El panel (lo que condiciona el diseno)

| Dato | Valor |
|---|---|
| Panel | LCD **COUGAR CFV235**, 9,16 pulgadas, montado en la caja del PC |
| **Resolucion** | **1920 x 462 px** |
| **Relacion de aspecto** | **4.16 : 1** (muy panoramica) |
| Color | panel **LCD** de 24 bits; el negro no es absoluto como en un OLED |
| Brillo | ajustable de 0 a 100 (a 0 la pantalla se ve negra) |
| Orientacion | 0 / 90 / 180 / 270 grados (lo normal en esta caja: 270) |
| Distancia de visionado | 1-2 m, de frente: hace falta **contraste alto** y trazos gruesos |
| Formato que muestra | **PNG** (acepta JPEG y GIF como fichero, pero el **GIF se ve en blanco**) |
| Refresco | el PC sube una imagen nueva cada vez: **~160 ms por fotograma** |
| Espacio | unos 79 MB libres, compartidos con todo lo que subas |
| Limite por fichero | 62.5 MB (65535 bloques de 1000 B) |

### Lo que cambia respecto a un video normal

| Dato | Valor |
|---|---|
| Fotogramas por segundo reales | **2 fps** recomendado; techo ~3.0 |
| Que pasa con los demas | **se descartan**: de un video a 30 fps se ven 4 de cada 30 |
| Duracion | mejor bucles cortos: **4 - 8 segundos** |
| Audio | **no hay altavoces**: el audio se ignora |
| Resolucion de origen | que sea **1920x462**; si no, la app lo ajusta (`--ajuste`) |
| Formato | **mp4 (H.264)** es lo mas comodo; tambien mkv, webm, avi y mov |
| Capa recomendada | **OSD** (`--capa osd`): reutiliza su hueco (no llena el panel) |

### Como aprovechar las caracteristicas del panel

1. **El formato es muy panoramico (4.16:1)**: compon en **bandas horizontales**
   (degradados, horizontes, olas, lineas). Las composiciones cuadradas o centradas
   desperdician dos tercios de la pantalla.
2. **Se ve a 1-2 m**: contraste alto y formas grandes. Los detalles finos (tramas, ruido,
   texto pequeno) no se leen y ensucian.
3. **Es LCD, no OLED**: el negro es gris muy oscuro. Un fondo totalmente negro se nota
   apagado; es mejor un **casi negro con algo de color** (#0b0e14, #0a1526) que un #000 puro.
4. **El brillo molesta de noche**: los fondos muy claros deslumbran en una habitacion a
   oscuras. Si eliges uno claro, baja el brillo del panel.
5. **El panel compone DOS capas**: el **fondo** (lo que subes con `cfv235 subir`) y una
   **capa OSD** encima (donde va el dashboard). Si vas a poner metricas encima, deja la
   **franja central despejada y oscura**.
6. **La capa de fondo acumula memoria; la OSD reutiliza su hueco**. Para imagenes que vas a
   cambiar a menudo, subelas con `--osd`.
7. **Margen de seguridad de 24 px**: el chasis tapa un poco los bordes.

## 2. El prompt (copiar y pegar)

> **Genera un video de exactamente 1920 x 462 píxeles** (formato panoramico
> 4.16:1), de **unos 6 segundos**, para la pantalla LCD de 9,16 pulgadas de una caja
> de PC.
> **En bucle perfecto**: el primer y el ultimo fotograma tienen que coincidir.
> **Movimiento lento y continuo, sin cortes de escena y sin movimiento de camara.**
> Motivo: **[MOTIVO: ondas de luz que se desplazan / un degradado que respira / una cuadricula
> en movimiento lento / particulas flotando / un anillo girando]**.
> Fondo oscuro, **alto contraste**, formas grandes, estilo **[ESTILO]**.
> **Sin texto, sin letras, sin numeros, sin logos, sin marcas de agua y sin audio.**
> Salida en **H.264 / mp4**.
> Se reproducira en el panel a 2 fotogramas por segundo, asi que el movimiento debe ser
> lento y legible a esa velocidad.

Ajustes utiles:

- Si la IA entrega 16:9, no pasa nada: `--ajuste ajustar` (por defecto) anade bandas negras y
  `--ajuste recortar` recorta el centro a 4.16:1.
- **Pide 6 segundos y bucle**: a 2 fps son unos 24 fotogramas distintos, suficiente
  para que no se note la repeticion.
- Si la IA solo da imagenes, pide "los fotogramas clave" y monta el video tu (abajo).

## 3. Que evitar

- **Movimiento rapido o camara en movimiento**: a 2 fps es una sucesion de saltos.
- **Cortes de escena**: se ven como parpadeos.
- **Audio**: se ignora.
- Videos largos (minutos): no aportan nada y tardan mas en preparar.
- Texto y numeros generados por la IA.

## 4. Prepararlo y reproducirlo

```bash
cfv235 video ver pelicula.mp4        # tipo, resolucion, fps de origen, duracion
cfv235 video pelicula.mp4            # reproducir en bucle (2 fps, capa OSD)
cfv235 video pelicula.mp4 --fps 2    # mas lento
cfv235 video pelicula.mp4 --ajuste recortar   # ajustar | recortar | estirar
cfv235 video pelicula.mp4 --sin-bucle --max 60
```

Si la app dice que no puede leerlo, falta el decodificador de ese formato en el sistema
(GStreamer): con H.264/mp4 suele estar.

## 5. Generarlo por codigo

Con **GStreamer** (viene instalado; no hace falta ffmpeg):

```bash
# 4 segundos a 12 fps de una bola moviendose, en 1920x462
gst-launch-1.0 -q videotestsrc num-buffers=48 pattern=ball \
  ! video/x-raw,width=1920,height=462,framerate=12/1 \
  ! x264enc speed-preset=ultrafast key-int-max=12 ! mp4mux ! filesink location=movimiento.mp4
```

Y si tienes **ffmpeg**, desde una carpeta de fotogramas PNG:

```bash
ffmpeg -framerate 2 -i cuadro%03d.png -c:v libx264 -pix_fmt yuv420p -crf 18 video.mp4
```

## 6. Diez ideas que se ven bien a 2 fps

1. Ondas de luz que se desplazan de izquierda a derecha en bucle.
2. Un degradado que cambia de ambar a cian muy despacio.
3. Una cuadricula que se mueve en diagonal (aire de tunel).
4. Particulas flotando hacia arriba sobre fondo oscuro.
5. Un anillo que gira lentamente alrededor del centro.
6. La silueta de una ciudad con luces que se encienden y se apagan.
7. Una linea de horizonte con un sol que sube y baja lentamente.
8. Fibra optica: filamentos de luz recorriendo la pantalla.
9. Un latido: un nucleo que se expande y se contrae.
10. Lluvia digital muy lenta en verde sobre negro.
