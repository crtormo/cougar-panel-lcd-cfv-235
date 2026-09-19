"""La seccion clima del dashboard: aparece con datos y se auto-oculta sin ellos.

La seccion es **opt-in** (`por_defecto=False`): el clima se descarga de la red y muchos
equipos no lo quieren, asi que solo se pinta si ademas hay datos (`clima_temp`). Es el
mismo criterio que el resto del dashboard aplica a ventiladores y temperatura de placa.

Sitio en la rejilla: la fila de tarjetas son 4 columnas de 453 px y el clima entra el
ultimo, en la primera columna que quede libre. Si las cuatro tienen dato (perfil `completo`
en un equipo con CPU, GPU, RAM y disco) no hay hueco y **no se pinta ninguna tarjeta**:
antes que pisar una tarjeta, el clima no sale. Con `presentacion`/`minimo`, que dejan
columnas vacias, si aparece.
"""
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)          # ejecutable tambien a mano: python3 -B tests/...

from cfv235 import dashboard

# Muestra completa: las cuatro columnas de la fila de tarjetas tienen dato.
VALORES_COMPLETO = {
    "cpu_uso": 12.0, "cpu_temp": 45.0,
    "gpu_uso": 4.0, "gpu_temp": 38.0,
    "ram_uso": 41.0, "disco_uso": 55.0,
    "clima_temp": 14.3, "clima_sensacion": 12.8, "clima_humedad": 62,
    "clima_viento": 11.2, "clima_codigo": 3,
    "clima_descripcion": "Nublado", "clima_icono": "Nubes",
}


def _tarjetas_clima(tema: dict) -> list[dict]:
    """Tarjetas del tema cuya etiqueta es la del clima."""
    return [w for w in tema.get("widgets", []) if w.get("etiqueta") == "Exterior"]


def _detalle_clima(tema: dict) -> list[dict]:
    return [w for w in tema.get("widgets", [])
            if w.get("tipo") == "texto" and "{clima_humedad}" in (w.get("texto") or "")]


class TestCatalogoSeccion(unittest.TestCase):
    def test_seccion_esta_en_el_catalogo(self):
        claves = {s.clave for s in dashboard.SECCIONES}
        self.assertIn("clima", claves)

    def test_por_defecto_apagada(self):
        seccion = next(s for s in dashboard.SECCIONES if s.clave == "clima")
        self.assertFalse(seccion.por_defecto)

    def test_ningun_perfil_la_activa(self):
        """Opt-in puro: sin `--con clima` ningun perfil la enciende."""
        for perfil in dashboard.PERFILES:
            self.assertFalse(dashboard.secciones_activas(perfil)["clima"],
                             f"el perfil {perfil!r} activa el clima por su cuenta")

    def test_se_puede_activar_por_ajustes(self):
        activas = dashboard.secciones_activas("completo", {"clima": True})
        self.assertTrue(activas["clima"])


class TestAutoOcultado(unittest.TestCase):
    def test_sin_activar_no_pinta_nada(self):
        """Con datos de clima, si la seccion no esta activa no hay tarjeta."""
        tema = dashboard.tema_dashboard(perfil="presentacion", valores=VALORES_COMPLETO)
        self.assertEqual(_tarjetas_clima(tema), [])

    def test_activa_sin_datos_no_pinta_nada(self):
        """La seccion activada pero sin datos de clima: no hay tarjeta."""
        tema = dashboard.tema_dashboard(perfil="presentacion", ajustes={"clima": True},
                                        valores={"cpu_uso": 10})
        self.assertEqual(_tarjetas_clima(tema), [])

    def test_activa_con_clima_a_none_no_pinta_nada(self):
        """`Combinada` puede traer las claves con None: tampoco se pinta."""
        tema = dashboard.tema_dashboard(perfil="presentacion", ajustes={"clima": True},
                                        valores={**VALORES_COMPLETO, "clima_temp": None})
        self.assertEqual(_tarjetas_clima(tema), [])
        self.assertEqual(_detalle_clima(tema), [])

    def test_sin_muestra_no_pinta_nada(self):
        """Sin `valores` no se sabe si hay clima: se auto-oculta."""
        tema = dashboard.tema_dashboard(perfil="presentacion", ajustes={"clima": True})
        self.assertEqual(_tarjetas_clima(tema), [])


class TestTarjetaConHuecoLibre(unittest.TestCase):
    """Con una columna vacia (`presentacion` no pinta CPU/GPU/RAM/disco) el clima entra."""

    def _tema(self, **extra):
        return dashboard.tema_dashboard(perfil="presentacion", ajustes={"clima": True, **extra},
                                        valores=VALORES_COMPLETO)

    def test_tarjeta_aparece_con_datos(self):
        tarjetas = _tarjetas_clima(self._tema())
        self.assertEqual(len(tarjetas), 1)
        dato = tarjetas[0]

        self.assertEqual(dato["tipo"], "dato")
        self.assertEqual(dato["fuente"], "clima_temp")
        self.assertEqual(dato["sufijo"], "°C")
        self.assertFalse(dato["barra"])
        self.assertEqual(dato["y"], dashboard.TARJETA_Y)
        # La tarjeta del clima no lleva barra: su valor no es un porcentaje.
        barras = [w for w in self._tema()["widgets"]
                  if w.get("tipo") == "barra" and w.get("fuente") == "clima_temp"]
        self.assertEqual(barras, [])

    def test_detalle_lleva_marcadores(self):
        """El detalle se resuelve en cada fotograma, no con el valor de la muestra."""
        detalle = _detalle_clima(self._tema())
        self.assertEqual(len(detalle), 1)
        self.assertIn("{clima_descripcion}", detalle[0]["texto"])
        self.assertIn("{clima_humedad}", detalle[0]["texto"])
        self.assertEqual(detalle[0]["y"], dashboard.DETALLE_Y)
        self.assertEqual(detalle[0]["x"], _tarjetas_clima(self._tema())[0]["x"])

    def test_el_motor_lo_dibuja_sin_fallos(self):
        """El valor y el detalle se pintan de verdad al renderizar."""
        from cfv235 import temas, widgets
        dibujo = temas.renderizar_datos(self._tema(), VALORES_COMPLETO)
        self.assertGreater(len(dibujo), 1000)
        self.assertEqual(widgets.ultimos_fallos(), [])

    def test_ocupa_una_columna_de_la_rejilla_sin_solapes(self):
        """La columna es una de las 4 de `columnas(4)`, y no pisa ninguna tarjeta."""
        tema = self._tema(grafica=False, red=False, sistema=False, ventiladores=False,
                          temperaturas=False)
        tarjetas = [w for w in tema["widgets"]
                    if w.get("tipo") == "dato" and w.get("y") == dashboard.TARJETA_Y]
        ocupadas = sorted((w["x"], w["x"] + w["ancho"]) for w in tarjetas)
        for (_, fin), (inicio, _) in zip(ocupadas, ocupadas[1:]):
            self.assertLessEqual(fin, inicio, f"tarjetas solapadas: {ocupadas}")
        self.assertGreaterEqual(ocupadas[0][0], 0)
        self.assertLessEqual(ocupadas[-1][1], dashboard.ANCHO)
        self.assertIn((_tarjetas_clima(tema)[0]["x"], 453), dashboard.columnas(4))


class TestSinHuecoLibre(unittest.TestCase):
    """Perfil `completo` con las cuatro columnas ocupadas: no hay sitio, no se pisa."""

    def test_no_pisa_la_fila_de_tarjetas(self):
        tema = dashboard.tema_dashboard(ajustes={"clima": True}, valores=VALORES_COMPLETO)
        tarjetas = [w for w in tema["widgets"]
                    if w.get("tipo") == "dato" and w.get("y") == dashboard.TARJETA_Y]
        self.assertEqual([w["etiqueta"] for w in tarjetas],
                         ["CPU", "GPU", "MEMORIA", "DISCO"])
        self.assertEqual(_tarjetas_clima(tema), [])

    def test_columnas_libres_del_completo(self):
        activas = dashboard.secciones_activas("completo", {"clima": True})
        self.assertEqual(dashboard.columnas_libres(activas, VALORES_COMPLETO), [])
        # Un equipo sin GPU (o sin la seccion) deja su columna al clima.
        sin_gpu = {k: v for k, v in VALORES_COMPLETO.items()
                   if k not in ("gpu_temp", "gpu_uso")}
        self.assertEqual(dashboard.columnas_libres(activas, sin_gpu),
                         [dashboard.columnas(4)[1]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
