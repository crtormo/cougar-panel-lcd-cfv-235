# `referencia/` — la implementación original, sin tocar

`cougar_panel.py` es el cliente que se escribió **mientras se hablaba con el panel de verdad**
(desde un PC con Windows, que hizo de banco de pruebas): es la versión que subió imágenes al
panel, la que midió los tiempos de la sesión de transferencia y con la que se comprobó que el
checksum suma los dos bytes de longitud y no el valor `len`.

Se guarda aquí por dos razones:

1. **Como red de seguridad del kit.** `pruebas/test_protocolo.py` carga este fichero y compara
   los bytes que produce la biblioteca nueva (`cougar/protocolo.py`) con los suyos: mismo
   `build_frame`, mismo `build_request`, mismo `build_media_report`, mismo checksum. Si alguna
   vez se toca la biblioteca y el protocolo cambia, esa prueba lo dice.

2. **Como documento.** Está comentado con lo que se midió, incluido lo que costó averiguar
   (el escape, por qué el parser tiene que cortar por el `0x5A` de cierre, por qué el chequeo
   de longitud fallaba). Para entender el protocolo, este fichero y `docs/PROTOCOLO.md` son
   las fuentes.

No se usa desde el kit: es sólo referencia. Para trabajar, `cougar/panel.py`.
