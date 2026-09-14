# Tasa de fotogramas del panel, medida

Todo lo de este documento está **medido sobre el panel real** con
`herramientas/medir_fps.py`, en `/dev/hidraw1`, el 2026-09-14. Sustituye a las estimaciones
anteriores (que decían "4-5 fps" y "160 ms por fotograma": las dos eran optimistas).

---

## 1. Resumen

| Dato | Valor medido |
|---|---|
| **Techo real sostenido** | **~3,1 fps** (bucle del dashboard, sin fallos) |
| **Recomendado** | **2 fps** (se sostiene clavado, sin saltos) |
| Con 4 fps pedidos | se consiguen **2,5 fps** |
| Coste de **dibujar** el PNG (19 widgets) | **~51 ms** |
| Coste de **subir** un fotograma | **100 - 440 ms** (mediana ~250) |
| Fotograma del dashboard | ~57 KB, 57 bloques |
| Espacio que ocupa en el panel | **~4 KB por fotograma** (capa OSD) |

Conclusión práctica: **dejarlo a 2 fps**. A partir de ahí el panel no da más y solo se
consiguen saltos.

## 2. Coste de subir un fotograma, segun el tipo de imagen

Mediana de 5 subidas por tipo, capa OSD, nombre fijo (reutiliza el hueco):

| Tipo de PNG | Bytes | Bloques | ms (mediana) | ms (min) | ms (max) | Aceptado |
|---|---|---|---|---|---|---|
| Color plano | 3 725 | 4 | 438 | 104 | 447 | si |
| Tipo dashboard | 4 661 | 5 | 437 | 117 | 446 | si |
| Foto (ruido) | 2 665 356 | 2666 | 1 619 | 1 448 | 1 763 | si |

Dos cosas que enseña la tabla:

- **El numero de bloques manda**: 5 bloques tardan ~440 ms y 2666 bloques tardan ~1,6 s.
  El tamano del fichero importa mucho mas que el contenido.
- **La diferencia entre el minimo y la mediana es enorme** (104 ms frente a 438 ms). No es
  ruido: es una latencia **bimodal** del panel. Esta en la fase de cierre (ver abajo).

## 3. En que se va el tiempo (desglose por fases)

PNG de 4 661 B (5 bloques), 6 subidas seguidas, midiendo cada fase:

| Intento | `transport` | bloques | acuse | `transported` | TOTAL |
|---|---|---|---|---|---|
| 1 | 19 ms | 1 ms | 1 ms | **68 ms** | 89 ms |
| 2 | 17 ms | 1 ms | 1 ms | **403 ms** | 422 ms |
| 3 | 28 ms | 1 ms | 1 ms | **68 ms** | 98 ms |
| 4 | 29 ms | 1 ms | 1 ms | **70 ms** | 101 ms |
| 5 | 23 ms | 1 ms | 1 ms | **399 ms** | 424 ms |
| 6 | 18 ms | 1 ms | 1 ms | **74 ms** | 94 ms |
| **mediana** | **21 ms** | **1 ms** | **1 ms** | **72 ms** | **99 ms** |

Lo que se deduce:

1. **El acuse de los bloques llega siempre y en 1 ms**: esperarlo no cuesta nada (y sigue
   siendo necesario: es lo que dice si el panel acepto los bloques).
2. **El coste esta en `transported`**, que contesta en **~70 ms o en ~400 ms**, alternando.
   Es el panel cerrando la escritura, no el PC ni el cable.
3. Por eso la media de una subida baila entre 100 y 440 ms: **el fotograma mas lento marca el
   ritmo**.

## 4. Cuanto se sostiene de verdad

Bucle del dashboard (dibujar + subir) durante 3 s por frecuencia:

| Objetivo | Pedidos | Subidos | Fallos | FPS reales | ms por fotograma |
|---|---|---|---|---|---|
| 1 fps | 3 | 3 | 0 | **1,0** | 193 |
| 2 fps | 6 | 6 | 0 | **2,0** | 186 |
| 4 fps | 12 | 12 | 0 | 2,5 | 198 |
| 6 fps | 18 | 18 | 0 | 3,1 | 206 |
| 8 fps | 24 | 24 | 0 | 3,1 | 204 |
| 10 fps | 30 | 30 | 0 | 3,1 | 210 |
| 15 fps | 45 | 45 | 0 | 3,0 | 197 |

**Ninguna subida falla** (no se pierden fotogramas): lo que pasa es que **el bucle se alarga**,
porque cada fotograma tarda lo que tarda. A partir de 4 fps el objetivo no se cumple: el panel
ya esta dando todo lo que tiene.

## 5. Espacio en el panel

Tras ~150 fotogramas del dashboard en la capa **OSD**: 81 360 KB -> 80 804 KB
(**-556 KB, unos 4 KB por fotograma**).

Confirma la medicion del kit: la capa OSD **reutiliza su hueco** en vez de acumular. Si los
mismos fotogramas fueran a la capa de **fondo**, cada uno ocuparia sus ~57 KB completos y el
panel (unos 79 MB) se llenaria en unas 1 400 subidas, con el riesgo de atascarlo.

## 6. Que se ha cambiado con estos datos

- `cfv235.video.FPS_POR_DEFECTO`: **4 -> 2** (a 4 el panel no llega).
- `cfv235.video.FPS_MAXIMO_REALISTA`: **5 -> 3** (el techo medido es 3,1).
- Los textos que decian "3-5 fps" o "160 ms por fotograma" ahora dicen lo medido.
- Los prompts de fotos, GIF y videos (`docs/PROMPT_*.md`) se regeneran con estos numeros.

---

## 8. Tamano maximo de fichero (medido con guardas)

Medido sobre el panel real con `herramientas/medir_tamano.py`, que lleva **guardas**: techo
duro de 20 MB, comprobacion del espacio libre antes de cada subida y parada automatica si el
panel deja de responder. **No se paso de 20 MB por nada.**

| Fichero | Resultado | Tiempo | Espacio libre despues | `bootFinish` |
|---|---|---|---|---|
| 1,0 MB | correcto | 1,2 s | 77,9 MB | 1 |
| 5,1 MB | correcto | 2,8 s | 73,3 MB | 1 |
| **10,2 MB** | **el panel se atasca** | 12,2 s | 64,6 MB | **0** |
| 15 MB, 19 MB | **no se enviaron**: la guarda paro al ver el atasco | - | - | - |

### La evidencia del atasco (la causa raiz, en el propio panel)

El caso real esta capturado en `referencia/kit-cfv-235/tramas_reales/respuesta_conn_1.bin` y
`respuesta_conn_2.bin`: dos respuestas `conn` tomadas **mientras el panel estaba atascado**.

```
bootFinish = 0          <- dejo de atender ordenes
space      = 40008 KB   <- ~39 MB, la MITAD de los ~80 MB de siempre
background = ['g_40mb.jpg']   <- un JPEG de ~40 MB
sn         = BYZL2611WC01CM001018
osdState   = 1
```

Es la prueba de fuego: el panel **acepto** un fichero de ~40 MB, se quedo con ~40 MB y no volvio
a servir. Explica tambien por que `recovery` tardaba entre 2 y 8 minutos (no reiniciaba: digeria
ese fichero) y por que el limite real esta muy por debajo de los 20 MB que anuncia el fabricante.

### Lo que enseña

1. **Entre 5 y 10 MB esta el limite.** A 10,2 MB el panel contesta `bootFinish=0` y deja de
   aceptar escrituras durante un rato. **Se recupera solo** (unos minutos despues volvio a
   responder con normalidad), pero es exactamente el riesgo de quedar inservible.
2. **El espacio se gasta de verdad.** Cada subida descuenta aproximadamente su tamano
   (79,7 -> 73,3 -> 64,6 MB), **aunque se repita el mismo nombre de fichero**. La idea de que
   "la capa OSD reutiliza su hueco" solo se cumple para fotogramas pequenos y repetidos: con
   ficheros grandes el panel guarda cada uno.
3. Por eso la app **baja su limite de 65 MB a 8 MB** (con aviso desde 4 MB): el limite del
   protocolo no tiene nada que ver con lo que el panel aguanta.

## 9. Los modos `mode` 0-3

Los cuatro responden **200** y el panel los guarda (`conn` devuelve el mismo valor), pero
**ninguno cambia nada observable por el protocolo**: ni `osdState`, ni la memoria libre, ni el
brillo. Su efecto, si lo tiene, solo se ve en la pantalla fisica.

## 10. El `recovery` no borra los medios (y tarda mucho)

Probado dos veces en el panel real, con el panel ya tocado por las pruebas de tamano:

| Intento | Que paso |
|---|---|
| 1 (desde el panel) | el panel dejo de responder **~107 s** y volvio con `bootFinish=1`. El espacio y el fondo **iguales** |
| 2 (desde `cfv235`) | `recovery` contesto **200**, el panel dejo de responder **~8 minutos** y volvio con `bootFinish=1`. El espacio (64,6 MB) y el fondo (`prueba_grande.jpg`) **exactamente igual** |

Conclusiones:

1. **`recovery` no libera la memoria** en este panel (firmware V1.0.5): se acepta, el panel se
   reinicia, pero los medios siguen ahi. El kit dice que "borra los medios y deja osdState en
   0"; medido, **no lo hace**.
2. **El reinicio tarda entre ~2 y ~8 minutos**, no los 60-80 s que anuncia la app. Durante ese
   rato el panel **sigue en el USB** (`lsusb` lo ve) y `/dev/hidrawN` existe, pero **no
   contesta a `conn`** (`code=None`). Es normal: hay que esperar, no desconectarlo.

Para saber si el panel esta vivo en ese estado, lo util no es que conteste (no lo hara), sino
que **siga apareciendo en `lsusb`** con `ID 1d6b:0126`.

## 11. Como repetir la medicion

```bash
# deja libre el panel (app y servicio)
systemctl --user stop cfv235-gtk cfv235-dashboard

python3 herramientas/medir_fps.py                  # todo
python3 herramientas/medir_fps.py --segundos 8     # pruebas mas largas
python3 herramientas/medir_fps.py --sin-panel      # solo el coste de dibujar
```

Y las pruebas automaticas que vigilan que no se prometa de mas:

```bash
python3 -B tests/test_rendimiento.py     # 8 pruebas, con el simulador
```

Entre ellas hay una que **falla a proposito** si alguien sube `FPS_MAXIMO_REALISTA` por encima
del techo medido: asi los valores por defecto no se pueden separar de la realidad sin que salte.
