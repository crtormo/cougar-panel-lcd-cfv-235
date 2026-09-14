"""Pruebas del framing y del protocolo (sin panel: no hace falta hardware).

    python3 -m unittest discover -s tests -v
    bash tests/ejecutar.sh

Se contrasta contra las tramas que trae el kit cfv-235 (`tramas_de_prueba.txt` y las dos
capturas reales de `tramas_reales/`), que son datos medidos del panel de verdad.
"""

import glob
import json
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import protocolo as p  # noqa: E402

KIT = "/home/maximo/cfv235/cfv-235-linux"


class TestFraming(unittest.TestCase):

    def test_ida_y_vuelta(self):
        for payload in (b"", b"POST conn 1\r\n\r\n", bytes(range(256)), b"\x5a\x5b" * 40):
            trama = p.build_frame(payload)
            info = p.decode_frame(trama)
            self.assertIsNotNone(info, f"no decodifica {payload[:20]!r}")
            self.assertEqual(info.payload, payload)
            self.assertTrue(info.checksum_ok, "checksum mal")
            self.assertTrue(info.len_ok, "longitud declarada != trama")
            self.assertEqual(info.consumidos, len(trama))

    def test_escape(self):
        # 0x5A -> 5B 01 y 0x5B -> 5B 02
        trama = p.build_frame(b"\x5a\x5b")
        self.assertIn(bytes([0x5B, 0x01]), trama)
        self.assertIn(bytes([0x5B, 0x02]), trama)

    def test_len_no_cuenta_los_escapes(self):
        """La trama del cable es MAS LARGA que `len` cuando hay escapes."""
        payload = b"\x5a" * 10
        trama = p.build_frame(payload)
        info = p.decode_frame(trama)
        self.assertEqual(info.declarado, len(payload) + 5)
        self.assertGreater(len(trama), info.declarado)

    def test_checksum_suma_los_dos_bytes_de_longitud(self):
        """Con len >= 256 la regla vieja (sumar `len`) falla por 1."""
        payload = b"A" * 366                       # len = 371
        self.assertEqual(p.checksum(payload, 371), (1 + 115 + sum(payload)) & 0xFF)
        self.assertNotEqual(p.checksum(payload, 371), (sum(payload) + 371) & 0xFF)

    def test_parser_no_se_desincroniza_con_escapes(self):
        """Un parser que cortase por `len` perderia la respuesta; este la encuentra."""
        payload = b'{"sn":"BYZL2611WC01CM001018","background":["g_40mb.jpg"]}'
        trama = p.build_frame(payload)
        relleno = b"\x00" * 500
        info = p.decode_frame(trama + relleno)
        self.assertIsNotNone(info)
        self.assertEqual(info.payload, payload)

    def test_decode_devuelve_none_si_falta_trozo(self):
        trama = p.build_frame(b"POST conn 1\r\n\r\n")
        for corte in range(1, len(trama)):
            self.assertIsNone(p.decode_frame(trama[:corte]),
                              f"con {corte} bytes no deberia estar completa")


class TestRespuestas(unittest.TestCase):

    def test_respuesta_con_cuerpo(self):
        payload = (b"1 200\r\nAckNumber=13\r\nContentType=json\r\nContentLength=9\r\n\r\n"
                   b'{"a": 1}')
        code, ack, cabecera, cuerpo, campos = p.parse_respuesta(payload)
        self.assertEqual(code, 200)
        self.assertEqual(ack, 13)
        self.assertEqual(json.loads(cuerpo), {"a": 1})
        self.assertEqual(campos.get("ContentType"), "json")

    def test_respuesta_vacia(self):
        code, ack, _, cuerpo, _ = p.parse_respuesta(
            b"1 200\r\nAckNumber=0\r\nContentLength=0\r\n\r\n")
        self.assertEqual(code, 200)
        self.assertEqual(ack, 0)
        self.assertEqual(cuerpo.strip(), "")

    def test_ack_puede_ser_cero_y_seq_mas_uno(self):
        """El panel manda acuses con AckNumber=0 y respuestas con seq+1."""
        for cabecera, esperado in ((b"AckNumber=0", 0), (b"AckNumber=755", 755)):
            _, ack, _, _, _ = p.parse_respuesta(b"1 200\r\n" + cabecera + b"\r\n\r\n")
            self.assertEqual(ack, esperado)


class TestPeticiones(unittest.TestCase):

    def test_peticion_sin_cuerpo(self):
        trama, seq = p.build_request("conn", None, seq=7)
        info = p.decode_frame(trama)
        texto = info.payload.decode()
        self.assertIn("POST conn 1\r\n", texto)
        self.assertIn("SeqNumber=7\r\n", texto)
        self.assertIn("Date=", texto)
        self.assertNotIn("ContentLength", texto)
        self.assertTrue(texto.endswith("\r\n\r\n"))

    def test_peticion_con_cuerpo(self):
        trama, _ = p.build_request("brightness", {"value": 100}, seq=1)
        texto = p.decode_frame(trama).payload.decode()
        self.assertIn("ContentType=json", texto)
        self.assertIn("ContentLength=13", texto)
        self.assertTrue(texto.endswith('{"value":100}'))

    def test_cuerpo_binario(self):
        datos = bytes(range(256)) * 4
        trama, _ = p.build_request("transport", None, seq=1, cuerpo_bruto=datos)
        info = p.decode_frame(trama)
        self.assertTrue(info.payload.endswith(datos))
        self.assertIn(f"ContentLength={len(datos)}", info.payload.decode("ascii", "replace"))


class TestInformesDeMedios(unittest.TestCase):

    def test_cabecera_y_datos(self):
        trozo = b"\x89PNG\r\n\x1a\n" + b"x" * 500
        informe = p.build_media_report(3, 9, trozo, p.MEDIA_TIPO_FONDO)
        self.assertEqual(informe[0], p.MEDIA_START)
        self.assertEqual((informe[1] << 8) | informe[2], 21 + len(trozo))
        self.assertEqual(informe[3], p.MEDIA_MENSAJE)
        self.assertEqual((informe[4] << 8) | informe[5], 9)
        self.assertEqual((informe[6] << 8) | informe[7], 3)
        self.assertEqual(informe[8], p.MEDIA_TIPO_FONDO)
        self.assertEqual(len(informe), p.MEDIA_CABECERA + len(trozo))
        # los datos empiezan en el offset 24 del cuerpo (25 del informe con report ID)
        self.assertEqual(informe[p.MEDIA_CABECERA:], trozo)

    def test_ultimo_bloque_corto(self):
        """El ultimo bloque va SIN relleno: no se completa a 1024."""
        informe = p.build_media_report(3, 4, b"x" * 744)
        self.assertEqual(len(informe), 24 + 744)

    def test_capa(self):
        self.assertEqual(p.build_media_report(0, 1, b"x", p.MEDIA_TIPO_OSD)[8],
                         p.MEDIA_TIPO_OSD)
        self.assertEqual(p.build_media_report(0, 1, b"x", p.MEDIA_TIPO_FONDO)[8],
                         p.MEDIA_TIPO_FONDO)

    def test_cabecera_media_acepta_con_y_sin_report_id(self):
        informe = p.build_media_report(1, 2, b"datos", p.MEDIA_TIPO_OSD)
        for variante in (informe, b"\x00" + informe):
            info = p.cabecera_media(variante)
            self.assertTrue(info["valido"])
            self.assertEqual(info["indice"], 1)
            self.assertEqual(info["total_bloques"], 2)
            self.assertEqual(info["tipo"], p.MEDIA_TIPO_OSD)
            self.assertEqual(info["payload"], b"datos")

    def test_bloques_de(self):
        self.assertEqual(p.bloques_de(1), 1)
        self.assertEqual(p.bloques_de(1000), 1)
        self.assertEqual(p.bloques_de(1001), 2)
        self.assertEqual(p.bloques_de(3744), 4)
        self.assertEqual(p.bloques_de(8078), 9)

    def test_rechaza_bloques_demasiado_grandes(self):
        with self.assertRaises(ValueError):
            p.build_media_report(0, 1, b"x" * (p.MEDIA_TROZO + 1))


class TestImagenes(unittest.TestCase):

    def test_tipo_de_imagen(self):
        self.assertEqual(p.tipo_de_imagen(b"\x89PNG\r\n\x1a\n" + b"x"), "png")
        self.assertEqual(p.tipo_de_imagen(b"\xff\xd8\xff\xe0"), "jpeg")
        self.assertEqual(p.tipo_de_imagen(b"GIF89a..."), "gif")
        self.assertIsNone(p.tipo_de_imagen(b"no soy una imagen"))

    def test_nombre_seguro(self):
        self.assertEqual(p.nombre_seguro("/tmp/fondo.png"), "fondo.png")
        self.assertEqual(p.nombre_seguro("C:\\Users\\x\\fondo.png"), "fondo.png")
        for malo in ("", "a" * 200, "con espacio.png", "a/../b.png", "ñ.png", "a;b.png"):
            with self.assertRaises(ValueError, msg=f"deberia rechazar {malo!r}"):
                p.nombre_seguro(malo)


@unittest.skipUnless(os.path.isdir(KIT), "no esta el kit cfv-235 para contrastar")
class TestContraElKit(unittest.TestCase):
    """Contrasta el framing con las tramas medidas que trae el kit."""

    def _tramas_documentadas(self):
        ruta = os.path.join(KIT, "tramas_reales", "tramas_de_prueba.txt")
        if not os.path.isfile(ruta):
            self.skipTest("no esta tramas_de_prueba.txt")
        salida = []
        for linea in open(ruta, encoding="utf-8", errors="replace"):
            # el fichero trae las tramas como "    hex     : 5a0036..."
            if ":" not in linea or "hex" not in linea.lower():
                continue
            hexa = linea.split(":", 1)[1].strip()
            if (hexa and len(hexa) % 2 == 0
                    and all(c in "0123456789abcdefABCDEF" for c in hexa)):
                salida.append(bytes.fromhex(hexa))
        return salida

    def test_tramas_documentadas(self):
        tramas = self._tramas_documentadas()
        self.assertGreaterEqual(len(tramas), 4, "esperaba varias tramas documentadas")
        for i, trama in enumerate(tramas, 1):
            info = p.decode_frame(trama)
            self.assertIsNotNone(info, f"trama {i}: no decodifica")
            self.assertTrue(info.checksum_ok, f"trama {i}: checksum")
            self.assertTrue(info.len_ok, f"trama {i}: longitud")
            self.assertEqual(info.consumidos, len(trama), f"trama {i}: consumidos")

    def test_capturas_reales(self):
        rutas = sorted(glob.glob(os.path.join(KIT, "tramas_reales", "*.bin")))
        if not rutas:
            self.skipTest("no hay capturas reales")
        for ruta in rutas:
            crudo = open(ruta, "rb").read()
            info = p.decode_frame(crudo)
            self.assertIsNotNone(info, f"{ruta}: no decodifica")
            self.assertTrue(info.checksum_ok, f"{ruta}: checksum mal")
            self.assertTrue(info.len_ok, f"{ruta}: longitud declarada != trama")
            code, ack, _, cuerpo, _ = p.parse_respuesta(info.payload)
            self.assertEqual(code, 200, f"{ruta}: se esperaba 200")
            datos = json.loads(cuerpo)
            for clave in ("bootFinish", "space", "background", "sn"):
                self.assertIn(clave, datos, f"{ruta}: falta {clave}")
            # con escapes, la trama del cable es mas larga que len
            if info.escapes:
                self.assertGreater(len(crudo.rstrip(b"\x00")), info.declarado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
