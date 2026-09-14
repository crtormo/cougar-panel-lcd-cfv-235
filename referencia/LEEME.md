# `referencia/` — otras implementaciones y material del banco de pruebas

Aquí está todo lo que se sabe del panel pero **no forma parte de la app**: código de otros que
habla con este mismo panel y el material con el que se hizo la ingeniería inversa.

## `kit-cfv-235/` — otra implementación completa, en Python

Es el kit de desarrollo que se escribió **desde el banco de pruebas de Windows** para este mismo
panel: biblioteca de protocolo, motor de widgets y temas, editor visual en el navegador, panel
simulado, patrones de calibración, pruebas y documentación. Está completo y funciona con el
panel de verdad (fue el que subió imágenes y midió los tiempos).

**Para qué sirve aquí:**

- **Contrastar el protocolo.** Sus dos implementaciones son independientes y coinciden byte a
  byte: si algún día un cambio hace que el framing no cuadre, comparar con esta es la forma más
  rápida de saber cuál de las dos se ha desviado.
- **Ver alternativas de diseño.** Su motor de temas **valida** el JSON antes de subirlo y expone
  un catálogo de campos para que una interfaz se construya sola; su simulador reproduce el fallo
  de sesión caducada; sus patrones de calibración (barras, rejilla, esquinas, degradado, cuadros,
  texto) son útiles para comprobar la pantalla.
- **Saber de dónde salió cada medición.** Su `PROTOCOLO.md` y su carpeta `docs/` cuentan lo
  mismo que `docs/CANAL.md` pero desde el otro lado, con los intentos que fallaron.

**Lo que no trae:** su propio repositorio git (tiene uno aparte, con sus commits) ni su carpeta
`_solo-windows/`, cuyas herramientas están en `herramientas/windows/` de este proyecto.

**Aviso importante:** los nombres de sus módulos se parecen a los de este proyecto
(`protocolo.py`, `panel.py`, `widgets.py`, `temas.py`, `canal.py` en un caso y `panel.py` en el
otro) y **no son los mismos**. No copies ficheros de aquí dentro de `cfv235/`: son implementaciones
distintas, con ideas distintas. Están aquí para leer y comparar.

## `herramientas/windows/` — con qué se averiguó todo

Ahí están el gancho que espía al editor oficial por su inspector de Electron, la sonda en Node
para hablar con el panel desde Windows, el primer dashboard en PowerShell y el procedimiento
completo. Su `LEEME.md` explica cada pieza y cómo se usó.

## `docs/evidencia/` — los datos crudos

Las capturas de `conn` del panel, las tramas documentadas, los sondeos del canal y la historia
de las capturas que se perdieron. Es lo que sostiene los números de los documentos: quien dude de
una cifra, puede mirar aquí el byte que la respalda.
