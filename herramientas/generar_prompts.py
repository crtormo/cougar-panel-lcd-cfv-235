#!/usr/bin/env python3
"""Genera los tres prompts para crear contenido para el panel: fotos, GIF y videos.

Los datos del panel (resolucion, fps reales, limites, capas, ajustes) se sacan del **codigo
real**, asi que los documentos no pueden quedar desfasados.

    python3 herramientas/generar_prompts.py

Escribe:
    docs/PROMPT_FOTOS.md
    docs/PROMPT_GIF.md
    docs/PROMPT_VIDEOS.md
"""

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import protocolo as p  # noqa: E402
from cfv235 import temas, video  # noqa: E402

ANCHO, ALTO = temas.ANCHO, temas.ALTO
ASPECTO = ANCHO / ALTO
FPS = video.FPS_POR_DEFECTO
FPS_MAX = video.FPS_MAXIMO_REALISTA
MAX_PROTOCOLO = p.MEDIA_MAX_BLOQUES * p.MEDIA_TROZO

# Espacio medido en el panel real (docs/CANAL.md).
ESPACIO_PANEL_MB = 79
MS_POR_FOTOGRAMA = 160

CABECERA_PANEL = f"""| Dato | Valor |
|---|---|
| Panel | LCD **COUGAR CFV235**, 9,16 pulgadas, montado en la caja del PC |
| **Resolucion** | **{ANCHO} x {ALTO} px** |
| **Relacion de aspecto** | **{ASPECTO:.2f} : 1** (muy panoramica) |
| Color | panel **LCD** de 24 bits; el negro no es absoluto como en un OLED |
| Brillo | ajustable de 0 a 100 (a 0 la pantalla se ve negra) |
| Orientacion | 0 / 90 / 180 / 270 grados (lo normal en esta caja: 270) |
| Distancia de visionado | 1-2 m, de frente: hace falta **contraste alto** y trazos gruesos |
| Formato que muestra | **PNG** (acepta JPEG y GIF como fichero, pero el **GIF se ve en blanco**) |
| Refresco | el PC sube una imagen nueva cada vez: **~{MS_POR_FOTOGRAMA} ms por fotograma** |
| Espacio | unos {ESPACIO_PANEL_MB} MB libres, compartidos con todo lo que subas |
| Limite por fichero | {MAX_PROTOCOLO / 1048576:.1f} MB (65535 bloques de 1000 B) |"""

APROVECHAR = f"""### Como aprovechar las caracteristicas del panel

1. **El formato es muy panoramico ({ASPECTO:.2f}:1)**: compon en **bandas horizontales**
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
7. **Margen de seguridad de 24 px**: el chasis tapa un poco los bordes."""


def doc_fotos() -> str:
    return f"""# Prompt para generar FOTOS (imagenes fijas) del panel COUGAR CFV235

Una foto es una imagen **estatica** que se queda puesta. Es lo que sube
`cfv235 subir foto.png` como **capa de fondo**.

---

## 1. El panel (lo que condiciona el diseno)

{CABECERA_PANEL}

{APROVECHAR}

## 2. El prompt (copiar y pegar)

> **Genera una imagen de exactamente {ANCHO} x {ALTO} píxeles** (formato panoramico
> {ASPECTO:.2f}:1, pensada para una pantalla LCD de 9,16 pulgadas de una caja de PC).
> Motivo: **[MOTIVO: un degradado suave / montanas geometricas en capas / ondas / un cielo
> con estrellas / una ciudad al atardecer / una cuadricula tecnica]**.
> Estilo: **[ESTILO: minimalista, geometrico, retro, futurista]**.
> Paleta: **[COLORES: negro azulado y cian / ambar y negro / verde terminal]**.
> Fondo oscuro, **alto contraste**, formas grandes y limpias.
> **Deja la franja central despejada y oscura** porque encima iran los datos del PC.
> **Sin texto, sin letras, sin numeros, sin logos, sin marcas de agua y sin bordes.**
> La imagen se vera a 1-2 metros de distancia y a 462 píxeles de alto.

Ajustes utiles segun la IA:

- **Midjourney**: anade `--ar 4:1 --style raw --no text, watermark, border`.
- **DALL-E / Gemini / Firefly**: pide "wide panoramic banner, 1920x462, no text".
- **Stable Diffusion**: `{ANCHO}x{ALTO}` en la resolucion (o genera a 1920x512 y recorta),
  y un *negative prompt* con `text, letters, watermark, border, frame, blur, noise`.

## 3. Que evitar

- Texto, numeros, logos y marcas de agua (la IA los escribe deformes).
- Marcos y bordes dibujados: el panel ya tiene el suyo.
- **Blanco puro** a pantalla completa: deslumbra en una habitacion a oscuras.
- Detalles muy finos, ruido o tramas: a 1-2 m vibran.
- Composiciones centradas y cuadradas: el panel es {ASPECTO:.2f}:1, muy apaisado.
- Mas de {MAX_PROTOCOLO / 1048576:.1f} MB (el protocolo no pasa) ni mucho menos acercarse a
  los {ESPACIO_PANEL_MB} MB del panel.

## 4. Prepararla y subirla

```bash
# exacta, sin transparencia y comprimida
python3 - <<'FIN'
from PIL import Image
im = Image.open("original.jpg").convert("RGB").resize(({ANCHO}, {ALTO}), Image.LANCZOS)
im.save("foto.png", optimize=True)
print("foto.png", im.size, __import__("os").path.getsize("foto.png"), "B")
FIN

cfv235 subir foto.png            # capa de FONDO (para algo fijo esta bien)
cfv235 subir foto.png --osd      # capa OSD (si vas a cambiarla a menudo)
cfv235 estado | grep background  # comprobar que el panel la ha adoptado
```

Y con el dashboard encima:

```bash
cfv235 subir foto.png            # 1) la foto como fondo
cfv235 dashboard --perfil minimo # 2) las metricas encima, en la capa OSD
```

## 5. Generarla por codigo (medidas exactas garantizadas)

```python
from PIL import Image, ImageDraw, ImageFilter

ANCHO, ALTO = {ANCHO}, {ALTO}

def degradado(arriba, abajo, salida="foto.png"):
    im = Image.new("RGB", (ANCHO, ALTO))
    dib = ImageDraw.Draw(im)
    for y in range(ALTO):
        t = y / (ALTO - 1)
        dib.line([(0, y), (ANCHO, y)],
                 fill=tuple(int(arriba[i] + (abajo[i] - arriba[i]) * t) for i in range(3)))
    im.save(salida, optimize=True)

def luz_centrada(color, salida="foto_luz.png"):
    base = Image.new("RGB", (ANCHO, ALTO), (8, 10, 18))
    mascara = Image.new("L", (ANCHO, ALTO), 0)
    ImageDraw.Draw(mascara).ellipse(
        [ANCHO // 2 - 700, ALTO // 2 - 260, ANCHO // 2 + 700, ALTO // 2 + 260], fill=90)
    base.paste(Image.new("RGB", (ANCHO, ALTO), color), (0, 0),
               mascara.filter(ImageFilter.GaussianBlur(120)))
    base.save(salida, optimize=True)

degradado((10, 14, 22), (0, 55, 90))     # azul COUGAR
luz_centrada((0, 90, 140))               # brillo suave en el centro
```

## 6. Diez ideas que quedan bien

1. Degradado del azul COUGAR con un brillo suave en el centro.
2. Montanas geometricas en capas, siluetas oscuras sobre cielo ambar.
3. Ondas tipo topografia en cian sobre casi negro.
4. Cuadricula tecnica tenue con un punto de luz (aire de sala de control).
5. Ciudad panoramica al atardecer, muy horizontal.
6. Campo de estrellas con nebulosa suave (sin grano).
7. Lineas diagonales en dos tonos de azul.
8. Degradado ambar a negro con una linea de horizonte.
9. Fondo liso con un circulo difuminado descentrado (deja libre el centro).
10. Fibra de carbono muy sutil en gris oscuro.
"""


def doc_gif() -> str:
    return f"""# Prompt para generar GIF animados del panel COUGAR CFV235

**Lo primero, para no perder el tiempo**: el panel **acepta un GIF como fichero pero lo
muestra en blanco** (comprobado en el panel real). El GIF sirve como **formato de entrada**:
la app lo abre, saca sus fotogramas y los manda como **PNG**, uno detras de otro.

Es decir: un GIF es una forma comoda de **disenar una animacion** de pocos fotogramas; lo que
acaba en el panel son PNG.

---

## 1. El panel (lo que condiciona el diseno)

{CABECERA_PANEL}

### Lo que cambia respecto a una foto

| Dato | Valor |
|---|---|
| Fotogramas por segundo reales | **{FPS:.0f} fps** recomendado; techo ~{FPS_MAX:.1f} |
| Por que | cada fotograma es un PNG de {ANCHO}x{ALTO} y tarda ~{MS_POR_FOTOGRAMA} ms en subirse |
| Duracion util de un GIF | {FPS:.0f} fps -> **12 fotogramas = 3 segundos**; 40 fotogramas = 10 s |
| Colores | el GIF admite **256 colores**; los degradados finos salen con bandas |
| Audio | no existe |
| Capa recomendada | **OSD** (`--capa osd`): reutiliza su hueco y no llena el panel |

{APROVECHAR}

## 2. El prompt (copiar y pegar)

> **Genera un GIF animado de exactamente {ANCHO} x {ALTO} píxeles** (formato panoramico
> {ASPECTO:.2f}:1) para la pantalla LCD de 9,16 pulgadas de una caja de PC.
> **En bucle perfecto**: el primer y el ultimo fotograma tienen que coincidir.
> **Entre 8 y 16 fotogramas**, con **movimiento lento y continuo** (se reproducira a
> {FPS:.0f} fotogramas por segundo: lo rapido se ve a saltos).
> Motivo: **[MOTIVO: ondas de luz / un pulso que crece y decrece / una cuadricula que se
> desplaza / particulas flotando lentamente / un anillo girando]**.
> **Colores planos y saturados** (GIF solo admite 256 colores), fondo oscuro, alto contraste,
> formas grandes.
> **Sin texto, sin letras, sin numeros, sin logos, sin marcas de agua, sin bordes** y sin
> cambios bruscos de escena.

Notas:

- Si la IA no hace GIF, pide los fotogramas por separado ("frame 1", "frame 2"...) o un
  video corto y conviertelo con `cfv235 video ver` + `--max`.
- **Pide el bucle explicito**: sin eso, cada vuelta da un salto visible.
- Un GIF de **3 segundos a 4 fps** (12 fotogramas) es el tamano ideal.

## 3. Que evitar

- **Movimiento rapido**: a {FPS:.0f} fps se convierte en una sucesion de saltos.
- **Degradados finos y fotos**: el GIF los corta en bandas.
- **Muchos fotogramas** (mas de 40): no se gana nada, se ven a la misma velocidad.
- Texto y numeros generados por la IA.
- Blanco puro a pantalla completa.

## 4. Reproducirlo en el panel

```bash
cfv235 video ver animacion.gif          # ver que es, sin tocar el panel
cfv235 video animacion.gif              # reproducir en bucle ({FPS:.0f} fps, capa OSD)
cfv235 video animacion.gif --fps 2      # mas lento, mas nitido
cfv235 video animacion.gif --sin-bucle --max 12   # una sola vuelta
```

## 5. Generarlo por codigo

```python
from PIL import Image, ImageDraw

ANCHO, ALTO = {ANCHO}, {ALTO}
FOTOGRAMAS = 12                      # 3 segundos a 4 fps

imagenes = []
for i in range(FOTOGRAMAS):
    im = Image.new("RGB", (ANCHO, ALTO), (8, 12, 20))
    dib = ImageDraw.Draw(im)
    # el pulso va y vuelve: el primer y el ultimo fotograma coinciden (bucle perfecto)
    avance = abs((i / (FOTOGRAMAS - 1)) * 2 - 1)
    radio = int(60 + 120 * avance)
    cx, cy = ANCHO // 2, ALTO // 2
    dib.ellipse([cx - radio, cy - radio, cx + radio, cy + radio],
                fill=(0, int(120 + 100 * avance), 255))
    imagenes.append(im)

imagenes[0].save("animacion.gif", save_all=True, append_images=imagenes[1:],
                 duration=int(1000 / {FPS:.0f}), loop=0)
```

## 6. Diez ideas que se ven bien a {FPS:.0f} fps

1. Un pulso de luz que crece y decrece.
2. Ondas concentricas lentas, estilo radar.
3. Una barra que se llena y se vacia.
4. Particulas pequenas subiendo.
5. Degradado que cambia de tono muy despacio.
6. Lineas diagonales desplazandose en bucle.
7. Un reloj analogico con segundero continuo.
8. Barras de ecualizador que suben y bajan.
9. Un anillo girando despacio.
10. Estrellas que aparecen y desaparecen sobre negro.
"""


def doc_videos() -> str:
    return f"""# Prompt para generar VIDEOS del panel COUGAR CFV235

**Lo primero**: el panel **no reproduce video**. No tiene decodificador: lo que muestra son
**fotogramas PNG** que le manda el PC. Un video sirve como **fuente**: la app lo abre con
GStreamer, saca los fotogramas y los sube uno a uno (`cfv235 video pelicula.mp4`).

Es decir: el video es la forma comoda de **disenar y montar** la animacion; lo que ve el panel
son PNG a **{FPS:.0f} fotogramas por segundo**.

---

## 1. El panel (lo que condiciona el diseno)

{CABECERA_PANEL}

### Lo que cambia respecto a un video normal

| Dato | Valor |
|---|---|
| Fotogramas por segundo reales | **{FPS:.0f} fps** recomendado; techo ~{FPS_MAX:.1f} |
| Que pasa con los demas | **se descartan**: de un video a 30 fps se ven 4 de cada 30 |
| Duracion | mejor bucles cortos: **4 - 8 segundos** |
| Audio | **no hay altavoces**: el audio se ignora |
| Resolucion de origen | que sea **{ANCHO}x{ALTO}**; si no, la app lo ajusta (`--ajuste`) |
| Formato | **mp4 (H.264)** es lo mas comodo; tambien mkv, webm, avi y mov |
| Capa recomendada | **OSD** (`--capa osd`): reutiliza su hueco (no llena el panel) |

{APROVECHAR}

## 2. El prompt (copiar y pegar)

> **Genera un video de exactamente {ANCHO} x {ALTO} píxeles** (formato panoramico
> {ASPECTO:.2f}:1), de **unos 6 segundos**, para la pantalla LCD de 9,16 pulgadas de una caja
> de PC.
> **En bucle perfecto**: el primer y el ultimo fotograma tienen que coincidir.
> **Movimiento lento y continuo, sin cortes de escena y sin movimiento de camara.**
> Motivo: **[MOTIVO: ondas de luz que se desplazan / un degradado que respira / una cuadricula
> en movimiento lento / particulas flotando / un anillo girando]**.
> Fondo oscuro, **alto contraste**, formas grandes, estilo **[ESTILO]**.
> **Sin texto, sin letras, sin numeros, sin logos, sin marcas de agua y sin audio.**
> Salida en **H.264 / mp4**.
> Se reproducira en el panel a {FPS:.0f} fotogramas por segundo, asi que el movimiento debe ser
> lento y legible a esa velocidad.

Ajustes utiles:

- Si la IA entrega 16:9, no pasa nada: `--ajuste ajustar` (por defecto) anade bandas negras y
  `--ajuste recortar` recorta el centro a {ASPECTO:.2f}:1.
- **Pide 6 segundos y bucle**: a {FPS:.0f} fps son unos 24 fotogramas distintos, suficiente
  para que no se note la repeticion.
- Si la IA solo da imagenes, pide "los fotogramas clave" y monta el video tu (abajo).

## 3. Que evitar

- **Movimiento rapido o camara en movimiento**: a {FPS:.0f} fps es una sucesion de saltos.
- **Cortes de escena**: se ven como parpadeos.
- **Audio**: se ignora.
- Videos largos (minutos): no aportan nada y tardan mas en preparar.
- Texto y numeros generados por la IA.

## 4. Prepararlo y reproducirlo

```bash
cfv235 video ver pelicula.mp4        # tipo, resolucion, fps de origen, duracion
cfv235 video pelicula.mp4            # reproducir en bucle ({FPS:.0f} fps, capa OSD)
cfv235 video pelicula.mp4 --fps 2    # mas lento
cfv235 video pelicula.mp4 --ajuste recortar   # ajustar | recortar | estirar
cfv235 video pelicula.mp4 --sin-bucle --max 60
```

Si la app dice que no puede leerlo, falta el decodificador de ese formato en el sistema
(GStreamer): con H.264/mp4 suele estar.

## 5. Generarlo por codigo

Con **GStreamer** (viene instalado; no hace falta ffmpeg):

```bash
# 4 segundos a 12 fps de una bola moviendose, en {ANCHO}x{ALTO}
gst-launch-1.0 -q videotestsrc num-buffers=48 pattern=ball \\
  ! video/x-raw,width={ANCHO},height={ALTO},framerate=12/1 \\
  ! x264enc speed-preset=ultrafast key-int-max=12 ! mp4mux ! filesink location=movimiento.mp4
```

Y si tienes **ffmpeg**, desde una carpeta de fotogramas PNG:

```bash
ffmpeg -framerate {FPS:.0f} -i cuadro%03d.png -c:v libx264 -pix_fmt yuv420p -crf 18 video.mp4
```

## 6. Diez ideas que se ven bien a {FPS:.0f} fps

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
"""


def main() -> int:
    documentos = {
        "PROMPT_FOTOS.md": doc_fotos(),
        "PROMPT_GIF.md": doc_gif(),
        "PROMPT_VIDEOS.md": doc_videos(),
    }
    carpeta = os.path.join(RAIZ, "docs")
    os.makedirs(carpeta, exist_ok=True)
    for nombre, contenido in documentos.items():
        ruta = os.path.join(carpeta, nombre)
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write(contenido)
        print(f"escrito {nombre} ({contenido.count(chr(10)) + 1} lineas)")

    # Los documentos antiguos quedan cubiertos por estos tres.
    for viejo in ("PROMPT_FONDOS.md", "PROMPT_ANIMACIONES.md"):
        ruta = os.path.join(carpeta, viejo)
        if os.path.exists(ruta):
            os.remove(ruta)
            print(f"eliminado {viejo} (lo sustituyen los tres nuevos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
