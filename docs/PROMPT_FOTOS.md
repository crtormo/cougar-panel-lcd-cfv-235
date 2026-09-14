# Prompt para generar FOTOS (imagenes fijas) del panel COUGAR CFV235

Una foto es una imagen **estatica** que se queda puesta. Es lo que sube
`cfv235 subir foto.png` como **capa de fondo**.

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

> **Genera una imagen de exactamente 1920 x 462 píxeles** (formato panoramico
> 4.16:1, pensada para una pantalla LCD de 9,16 pulgadas de una caja de PC).
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
- **Stable Diffusion**: `1920x462` en la resolucion (o genera a 1920x512 y recorta),
  y un *negative prompt* con `text, letters, watermark, border, frame, blur, noise`.

## 3. Que evitar

- Texto, numeros, logos y marcas de agua (la IA los escribe deformes).
- Marcos y bordes dibujados: el panel ya tiene el suyo.
- **Blanco puro** a pantalla completa: deslumbra en una habitacion a oscuras.
- Detalles muy finos, ruido o tramas: a 1-2 m vibran.
- Composiciones centradas y cuadradas: el panel es 4.16:1, muy apaisado.
- Mas de 62.5 MB (el protocolo no pasa) ni mucho menos acercarse a
  los 79 MB del panel.

## 4. Prepararla y subirla

```bash
# exacta, sin transparencia y comprimida
python3 - <<'FIN'
from PIL import Image
im = Image.open("original.jpg").convert("RGB").resize((1920, 462), Image.LANCZOS)
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

ANCHO, ALTO = 1920, 462

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
