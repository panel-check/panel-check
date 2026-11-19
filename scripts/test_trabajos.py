"""
Pruebas de Seguimientos (panel/trabajos_core.py): vencimientos, alertas y orden del tablero.
    cd scripts && python3 -m unittest test_trabajos -v
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "panel"))
import trabajos_core as tc  # noqa: E402

HOY = dt.date(2026, 11, 18)


class Vencimiento(unittest.TestCase):
    def test_oposicion_tres_meses(self):
        self.assertEqual(tc.vencimiento("oposicion", "2026-10-14"), dt.date(2027, 1, 14))
        self.assertEqual(tc.vencimiento("oposicion", "2026-11-04"), dt.date(2027, 2, 4))

    def test_oposicion_fin_de_mes(self):
        # 30/11 + 3 meses = 28/02 (no existe el 30/02)
        self.assertEqual(tc.vencimiento("oposicion", "2026-11-30"), dt.date(2027, 2, 28))

    def test_vista_treinta_dias(self):
        self.assertEqual(tc.vencimiento("vista", "2026-10-29"), dt.date(2026, 11, 28))
        self.assertEqual(tc.vencimiento("vista", "2026-11-05"), dt.date(2026, 12, 5))

    def test_sin_vencimiento(self):
        for sid in ("registro", "oposicion_adm", "vigilancia"):
            self.assertIsNone(tc.vencimiento(sid, "2026-11-01"))
        self.assertIsNone(tc.vencimiento("vista", None))
        self.assertIsNone(tc.vencimiento("desconocido", "2026-11-01"))


class Alerta(unittest.TestCase):
    def test_niveles(self):
        self.assertEqual(tc.alerta("vista", "2026-10-29", hoy=HOY)["nivel"], "verde")       # vence 28/11: 10 días
        self.assertEqual(tc.alerta("vista", "2026-10-25", hoy=HOY)["nivel"], "amarillo")    # 24/11: 6 días
        self.assertEqual(tc.alerta("vista", "2026-10-21", hoy=HOY)["nivel"], "rojo")        # 20/11: 2 días
        a = tc.alerta("vista", "2026-10-19", hoy=HOY)                                        # 18/11: hoy
        self.assertEqual((a["nivel"], a["texto"]), ("rojo", "Vence hoy"))
        a = tc.alerta("vista", "2026-10-10", hoy=HOY)                                        # 09/11: vencido
        self.assertEqual((a["nivel"], a["dias"]), ("vencido", -9))

    def test_falta_notificacion_y_sin_plazo(self):
        self.assertEqual(tc.alerta("oposicion", None, hoy=HOY)["nivel"], "falta")
        self.assertEqual(tc.alerta("registro", None, hoy=HOY)["nivel"], "ninguno")
        self.assertEqual(tc.alerta("oposicion_adm", "2026-11-01", hoy=HOY)["nivel"], "ninguno")
        self.assertEqual(tc.alerta("vista", "2026-10-29", "cerrado", hoy=HOY)["nivel"], "cerrado")


class PlazoCumplido(unittest.TestCase):
    def test_vista_con_escrito_presentado(self):
        a = tc.alerta("vista", "2026-10-10", hoy=HOY, etapa=3)        # vencida, pero ya se contestó
        self.assertEqual((a["nivel"], a["texto"]), ("cumplido", "✓ Escrito presentado"))
        self.assertEqual(tc.alerta("vista", "2026-10-29", hoy=HOY, etapa=2)["nivel"], "verde")

    def test_oposicion_con_acuerdo(self):
        self.assertEqual(tc.alerta("oposicion", "2026-11-04", hoy=HOY, etapa=5)["nivel"], "cumplido")
        self.assertEqual(tc.alerta("oposicion", "2026-11-04", hoy=HOY, etapa=4)["nivel"], "verde")

    def test_cumplidos_van_despues_de_los_pendientes(self):
        filas = [tc.completar({"marca": "HECHA", "servicio": "vista", "notificacion": "2026-10-20", "estado": "activo", "etapa": 3}, HOY),
                 tc.completar({"marca": "SIN", "servicio": "vista", "notificacion": None, "estado": "activo", "etapa": 1}, HOY),
                 tc.completar({"marca": "PEND", "servicio": "vista", "notificacion": "2026-11-05", "estado": "activo", "etapa": 1}, HOY)]
        self.assertEqual([t["marca"] for t in tc.ordenar(filas)], ["PEND", "SIN", "HECHA"])


class Informes(unittest.TestCase):
    def test_separar_por_marca(self):
        suyos, otros = tc.separar_informes({"marca": "AKRO LAB"}, [
            {"id": 1, "tipo": "cliente", "marca": "Akro Lab"}, {"id": 2, "tipo": "interno", "marca": "AKRO LAB"},
            {"id": 3, "tipo": "cliente", "marca": "OTRA"}])
        self.assertEqual([i["id"] for i in suyos], [1, 2])
        self.assertEqual(suyos[0]["tipo_nombre"], "Informe preliminar")
        self.assertEqual([i["id"] for i in otros], [3])


class Orden(unittest.TestCase):
    def _t(self, nombre, sid, notif=None, contr="2026-11-01", estado="activo"):
        return tc.completar({"marca": nombre, "servicio": sid, "notificacion": notif,
                             "contratacion": contr, "estado": estado, "etapa": 1}, HOY)

    def test_por_vencimiento_mas_cercano(self):
        filas = [self._t("C", "vista", "2026-11-05"), self._t("A", "vista", "2026-10-29"),
                 self._t("SIN", "vista", None), self._t("B", "vista", "2026-11-04"),
                 self._t("VENCIDA", "vista", "2026-10-10")]
        self.assertEqual([t["marca"] for t in tc.ordenar(filas)], ["VENCIDA", "A", "B", "C", "SIN"])

    def test_sin_plazo_mas_nuevo_arriba_y_cerrados_al_final(self):
        filas = [self._t("VIEJA", "registro", contr="2026-10-16"),
                 self._t("CERRADA", "registro", contr="2026-11-20", estado="cerrado"),
                 self._t("NUEVA", "registro", contr="2026-11-17")]
        self.assertEqual([t["marca"] for t in tc.ordenar(filas)], ["NUEVA", "VIEJA", "CERRADA"])


class Varios(unittest.TestCase):
    def test_marca_limpia(self):
        self.assertEqual(tc.marca_limpia("TIMBEROS (BBATACAZO)"), "TIMBEROS")
        self.assertEqual(tc.marca_limpia(" vision soberana "), "VISION SOBERANA")

    def test_etapas(self):
        t = tc.completar({"servicio": "oposicion", "etapa": 4, "estado": "activo"}, HOY)
        self.assertEqual((t["etapa_nombre"], t["proxima_etapa"], t["etapas_total"]), ("Negociando", "Acuerdo o retiro", 5))
        t = tc.completar({"servicio": "registro", "etapa": 0, "estado": "activo"}, HOY)
        self.assertEqual(t["etapa_nombre"], "Esperando seña")
        t = tc.completar({"servicio": "vista", "etapa": 99, "estado": "activo"}, HOY)
        self.assertEqual((t["etapa"], t["proxima_etapa"]), (4, None))

    def test_carga_inicial_coherente(self):
        claves = [c[0] for c in tc.CARGA_INICIAL]
        self.assertEqual(len(claves), len(set(claves)))
        for fila in tc.CARGA_INICIAL:
            self.assertIsNotNone(tc.servicio(fila[3]), fila[0])
        # Leonardo (cliente 32): dos trabajos de registro sin acta todavía
        leo = [f for f in tc.CARGA_INICIAL if f[1] == 32]
        self.assertEqual(sorted(f[2] for f in leo), ["MAPIAPPS", "TIMBEROS"])
        # Los vencimientos calculados coinciden con los que se cargaron en Notion
        venc = {f[2]: tc.vencimiento(f[3], f[6]) for f in tc.CARGA_INICIAL}
        self.assertEqual(venc["MATEACIDO"], dt.date(2026, 11, 28))
        self.assertEqual(venc["CANVA PADEL"], dt.date(2027, 1, 14))
        self.assertEqual(venc["BARRIOKEY"], dt.date(2027, 2, 4))

    def test_tabla_crea_y_carga(self):
        ejecutadas = []

        class Cur:
            def execute(self, sql, params=None):
                ejecutadas.append((sql, params))

        tc.crear_tablas(Cur())
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS trabajos (" in s for s, _ in ejecutadas))
        inserts = [p for s, p in ejecutadas if s.strip().startswith("INSERT INTO trabajos")]
        self.assertEqual(len(inserts), len(tc.CARGA_INICIAL))
        self.assertTrue(all("ON CONFLICT (clave_carga) DO NOTHING" in s for s, _ in ejecutadas if "INSERT" in s))


class DeteccionActa(unittest.TestCase):
    T = {"id": 2, "marca": "TIMBEROS", "servicio": "registro", "contratacion": "2026-10-16", "cliente_id": 32}
    CLIENTE = {"nombre": "Leonardo Mazziotti", "cuit": "20300000001"}

    def _c(self, acta, **k):
        base = {"acta": acta, "denominacion": "TIMBEROS", "titular": "BBATACAZO SRL", "cuit": "30711111111",
                "fecha": "2026-11-10", "matricula": None, "fuente": "escaneo", "clase": 41}
        base.update(k)
        return base

    def test_segura_por_matricula_del_estudio(self):
        elegida, _ = tc.elegir_acta(self.T, [self._c("4812000", matricula="2906")], self.CLIENTE, {"2906"})
        self.assertEqual(elegida["acta"], "4812000")
        self.assertIn("matrícula", elegida["motivos"][0])

    def test_segura_por_cuit_o_nombre_del_cliente(self):
        e, _ = tc.elegir_acta(self.T, [self._c("4812001", cuit="20300000001")], self.CLIENTE, set())
        self.assertEqual(e["acta"], "4812001")
        e, _ = tc.elegir_acta(self.T, [self._c("4812002", titular="MAZZIOTTI LEONARDO 100.00%")], self.CLIENTE, set())
        self.assertEqual(e["acta"], "4812002")

    def test_dudosa_no_se_asigna(self):
        # Mismo nombre pero de un tercero (otro CUIT, otro agente): se muestra pero no se carga sola
        e, evaluadas = tc.elegir_acta(self.T, [self._c("4800000", matricula="1234")], self.CLIENTE, {"2906"})
        self.assertIsNone(e)
        self.assertEqual([x["acta"] for x in evaluadas], ["4800000"])

    def test_otro_nombre_o_muy_vieja_se_descarta(self):
        _, ev = tc.elegir_acta(self.T, [self._c("1", denominacion="TIMBEROS PLUS", matricula="2906"),
                                        self._c("2", fecha="2025-01-01", matricula="2906")], self.CLIENTE, {"2906"})
        self.assertEqual(ev, [])

    def test_tildes_y_varias_clases(self):
        t = {**self.T, "marca": "CINE CATASTROFE"}
        cands = [self._c("4812100", denominacion="Cine Catástrofe", matricula="2906", clase=41),
                 self._c("4812050", denominacion="CINE  CATÁSTROFE", matricula="2906", clase=9)]
        e, ev = tc.elegir_acta(t, cands, self.CLIENTE, {"2906"})
        self.assertEqual(e["acta"], "4812050")          # la de número más bajo
        texto = tc.texto_deteccion(e, ev, "prueba")
        self.assertIn("4812100", texto)                   # la otra clase queda nombrada en la bitácora

    def test_patron_sql(self):
        self.assertEqual(tc.patron_ilike("Cine Catástrofe"), "%C_N_%C_T_STR_F_%")


if __name__ == "__main__":
    unittest.main()
