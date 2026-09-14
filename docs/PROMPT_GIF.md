# Prompt para generar GIF animados del panel COUGAR CFV235

**Lo primero, para no perder el tiempo**: el panel **acepta un GIF como fichero pero lo
muestra en blanco** (comprobado en el panel real). El GIF sirve como **formato de entrada**:
la app lo abre, saca sus fotogramas y los manda como **PNG**, uno detras de otro.

Es decir: un GIF es una forma comoda de **disenar una animacion** de pocos fotogramas; lo que
acaba en el panel son PNG.

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

### Lo que cambia respecto a una foto

| Dato | Valor |
|---|---|
| Fotogramas por segundo reales | **2 fps** recomendado; techo ~3.0 |
| Por que | cada fotograma es un PNG de 1920x462 y tarda ~160 ms en subirse |
| Duracion util de un GIF | 2 fps -> **12 fotogramas = 3 segundos**; 40 fotogramas = 10 s |
| Colores | el GIF admite **256 colores**; los degradados finos salen con bandas |
| Audio | no existe |
| Capa recomendada | **OSD** (`--capa osd`): reutiliza su hueco y no llena el panel |

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

> **Genera un GIF animado de exactamente 1920 x 462 píxeles** (formato panoramico
> 4.16:1) para la pantalla LCD de 9,16 pulgadas de una caja de PC.
> **En bucle perfecto**: el primer y el ultimo fotograma tienen que coincidir.
> **Entre 8 y 16 fotogramas**, con **movimiento lento y continuo** (se reproducira a
> 2 fotogramas por segundo: lo rapido se ve a saltos).
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

- **Movimiento rapido**: a 2 fps se convierte en una sucesion de saltos.
- **Degradados finos y fotos**: el GIF los corta en bandas.
- **Muchos fotogramas** (mas de 40): no se gana nada, se ven a la misma velocidad.
- Texto y numeros generados por la IA.
- Blanco puro a pantalla completa.

## 4. Reproducirlo en el panel

```bash
cfv235 video ver animacion.gif          # ver que es, sin tocar el panel
cfv235 video animacion.gif              # reproducir en bucle (2 fps, capa OSD)
cfv235 video animacion.gif --fps 2      # mas lento, mas nitido
cfv235 video animacion.gif --sin-bucle --max 12   # una sola vuelta
```

## 5. Generarlo por codigo

```python
from PIL import Image, ImageDraw

ANCHO, ALTO = 1920, 462
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
                 duration=int(1000 / 2), loop=0)
```

## 6. Diez ideas que se ven bien a 2 fps

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
