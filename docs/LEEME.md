# `docs/` — qué contesta cada documento

Aquí está todo lo que se sabe del panel. Si buscas algo, esta tabla te lleva directo:

| Documento | Qué contesta | De dónde viene |
|---|---|---|
| `CANAL.md` | **El protocolo, medición a medición**: descriptor HID, framing, mensajes, subida, comandos que existen y los que no | Linux (equipo del panel) |
| `HALLAZGOS.md` | El resumen: lo importante del panel en dos páginas | Linux |
| `RENDIMIENTO.md` | Cuántos fotogramas aguanta, desglose por fases y el límite de tamaño | Linux |
| `DIARIO.md` | **Cómo y cuándo se averiguó cada cosa**, sesión a sesión, con la evidencia y los pendientes | Los dos lados |
| `VERIFICACION_INDEPENDIENTE.md` | La revisión desde otro equipo: qué se confirma, qué se matiza y qué no se pudo reproducir | Windows (banco de pruebas) |
| `INGENIERIA_INVERSA.md` | **Cómo se descubrió** el protocolo: el log del editor, las capturas por el inspector, los intentos que fallaron | Windows |
| `FUNCIONES_DEL_EDITOR.md` | Qué hace el editor oficial, pantalla a pantalla, y las **35 fuentes de datos** que usa | Windows |
| `API_DEL_EDITOR.md` | Los comandos y nombres que expone su API interna (de ahí salieron `mode` y `waterBlockScreen`) | Windows |
| `REVISION_cougarLCD.cpp.md` | Qué coincide con `cougarLCD.cpp`, otra implementación del mismo protocolo, y qué se aprendió de ella | Windows |
| `REVISION_poseidon-linux-display.md` | Lo mismo con otro proyecto que habla con este panel desde Linux | Windows |
| `evidencia/` | **Los datos crudos**: capturas de `conn` del panel, tramas documentadas y sondeos del canal | Los dos lados |
| `PROMPT_*.md` | Prompts para generar fondos, GIF, vídeos y temas con otra IA | Linux |

## Por dónde empezar

1. `HALLAZGOS.md` — cinco minutos para saber qué es este panel y qué trampas tiene.
2. `CANAL.md` — el protocolo de verdad, si vas a tocar `cfv235/protocolo.py` o `canal.py`.
3. `DIARIO.md` — antes de dar algo por sentado: aquí está lo que se midió, cuándo y con qué.
4. `VERIFICACION_INDEPENDIENTE.md` — los puntos donde dos equipos no coinciden y qué falta por comprobar.

## Qué no está aquí, y por qué

- **El documento `PROTOCOLO.md` del kit cfv-235** no se copia: `CANAL.md` cubre lo mismo y está
  mejor ordenado. Si algún día hace falta el detalle byte a byte del informe de medios o la
  derivación del checksum, está en el kit y en `INGENIERIA_INVERSA.md`.
- **El analizador del log del editor** (kit) tampoco: ese log no incluye los bloques de subida,
  que era justo lo que hacía falta, y por eso se acabó usando el gancho del inspector que está
  en `herramientas/windows/`. Para analizar capturas, en cambio, sí hay herramienta propia:
  `herramientas/analizar_tramas.py`.
- **El motor de widgets y temas del kit** no se trae: este proyecto tiene el suyo, más completo.
  Del kit sí interesa la idea de **validar el tema antes de subirlo** y el catálogo de campos en
  JSON para que una interfaz se construya sola.

## Cómo se mantiene

Cuando se mida algo nuevo o se corrija un documento, se añade una entrada en `DIARIO.md` con la
evidencia y el commit, y se arregla el documento afectado. Nada se borra: lo viejo se queda como
historia, porque muchas veces la explicación de un número está en el intento que falló antes.
