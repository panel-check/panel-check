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
        for sid in ("registro", "oposicion_adm", "desistimiento", "vigilancia"):
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
        a = tc.alerta("vista", "2026-10-10", hoy=HOY, etapa=5)        # vencida, pero ya se contestó
        self.assertEqual((a["nivel"], a["texto"]), ("cumplido", "✓ Escrito presentado"))
        self.assertEqual(tc.alerta("vista", "2026-10-29", hoy=HOY, etapa=2)["nivel"], "verde")

    def test_oposicion_con_acuerdo(self):
        self.assertEqual(tc.alerta("oposicion", "2026-11-04", hoy=HOY, etapa=7)["nivel"], "cumplido")
        self.assertEqual(tc.alerta("oposicion", "2026-11-04", hoy=HOY, etapa=6)["nivel"], "verde")

    def test_cumplidos_van_despues_de_los_pendientes(self):
        filas = [tc.completar({"marca": "HECHA", "servicio": "vista", "notificacion": "2026-10-20", "estado": "activo", "etapa": 5}, HOY),
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
        t = tc.completar({"servicio": "oposicion", "etapa": 6, "estado": "activo"}, HOY)
        self.assertEqual((t["etapa_nombre"], t["proxima_etapa"], t["etapas_total"]), ("Negociando", "Acuerdo o retiro", 7))
        t = tc.completar({"servicio": "registro", "etapa": 0, "estado": "activo"}, HOY)
        self.assertEqual(t["etapa_nombre"], "Esperando seña")
        t = tc.completar({"servicio": "vista", "etapa": 99, "estado": "activo"}, HOY)
        self.assertEqual((t["etapa"], t["proxima_etapa"]), (6, None))

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
            rowcount = 1
            def execute(self, sql, params=None):
                ejecutadas.append((sql, params))

        tc.crear_tablas(Cur())
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS trabajos (" in s for s, _ in ejecutadas))
        inserts = [p for s, p in ejecutadas if s.strip().startswith("INSERT INTO trabajos ")]
        self.assertEqual(len(inserts), len(tc.CARGA_INICIAL))
        self.assertTrue(all("ON CONFLICT (clave_carga) DO NOTHING" in s for s, _ in ejecutadas if s.strip().startswith("INSERT INTO trabajos ")))
        # la renumeración de etapas va antes de la carga inicial (que ya usa la numeración nueva)
        orden = [s.strip().split()[0:3] for s, _ in ejecutadas]
        i_mig = next(i for i, (s, _) in enumerate(ejecutadas) if s.startswith("UPDATE trabajos SET etapa"))
        i_carga = next(i for i, (s, _) in enumerate(ejecutadas) if s.strip().startswith("INSERT INTO trabajos "))
        self.assertLess(i_mig, i_carga)


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



class EtapasRegistroInpi(unittest.TestCase):
    def test_nombres_de_etapas(self):
        self.assertEqual(tc.POR_ID["registro"]["etapas"],
                         ["Seña pagada", "Formulario y poder", "Informe enviado", "Marca publicada", "Marca en boletín",
                          "Marca en proyecto de resolución", "Concedida"])
        self.assertEqual(tc.POR_ID["oposicion"]["etapas"][:4], ["Seña cobrada", "Formulario y poder", "Toma de intervención", "Análisis"])
        self.assertEqual(tc.POR_ID["vista"]["etapas"][:4], ["Seña cobrada", "Formulario y poder", "Toma de intervención", "Análisis"])
        # el plazo se cumple con el escrito presentado / acuerdo o retiro (numeración nueva)
        self.assertEqual(tc.POR_ID["vista"]["etapas"][tc.POR_ID["vista"]["cumple_en"] - 1], "Escrito presentado")
        self.assertEqual(tc.POR_ID["oposicion"]["etapas"][tc.POR_ID["oposicion"]["cumple_en"] - 1], "Acuerdo o retiro")

    def test_etapa_de_acta(self):
        self.assertEqual(tc.etapa_de_acta({"ubicacion_actual": "Sector Estudio Fondo 583/25"}), 4)
        self.assertEqual(tc.etapa_de_acta({}), 4)                                    # recién cargada, sin leer
        self.assertEqual(tc.etapa_de_acta({"fecha_publicacion": "2026-10-01"}), 5)
        self.assertEqual(tc.etapa_de_acta({"ubicacion_actual": "División Publicación"}), 4)   # espera salir en el boletín
        self.assertEqual(tc.etapa_de_acta({"fecha_publicacion": "2099-01-01"}), 4)            # fecha futura: todavía no salió
        self.assertEqual(tc.etapa_de_acta({"ubicacion_actual": "División Notificaciones"}), 5)
        self.assertEqual(tc.etapa_de_acta({"ubicacion_actual": "División Notificaciones", "fecha_publicacion": "2026-10-01"}), 5)
        self.assertEqual(tc.etapa_de_acta({"ubicacion_actual": "División Resoluciones", "fecha_publicacion": "2026-10-01"}), 6)
        self.assertEqual(tc.etapa_de_acta({"ubicacion_actual": "División Resoluciones", "estado_tramite": "Concedida"}), 7)
        self.assertIsNone(tc.etapa_de_acta({"estado_tramite": "Denegada"}))

    def test_varias_actas_cuenta_la_mas_atrasada(self):
        cartera = [
            {"acta": "4790682", "cliente_id": 7, "denominacion": "MAS", "clase": 36, "estado_tramite": "Concedida"},
            {"acta": "4790681", "cliente_id": 7, "denominacion": "Más", "clase": 35, "fecha_publicacion": "2026-10-01"},
            {"acta": "4790683", "cliente_id": 7, "denominacion": "MAS", "clase": 9, "estado_tramite": "Denegada"},
            {"acta": "4790684", "cliente_id": 7, "denominacion": "OTRA MARCA", "clase": 35},
            {"acta": "4790685", "cliente_id": 8, "denominacion": "MAS", "clase": 35},          # otro cliente
            {"acta": "4790686", "cliente_id": 7, "denominacion": "LBV", "grupo_marca": "MÁS", "clase": 41},
        ]
        actas = tc.actas_del_trabajo({"cliente_id": 7, "marca": "MÁS", "acta": None}, cartera)
        self.assertEqual([a["acta"] for a in actas], ["4790683", "4790681", "4790682", "4790686"])
        info = tc.etapa_registro(actas)
        self.assertEqual((info["etapa"], info["nombre"]), (4, "Marca publicada"))   # la 4790686 recién presentada
        info = tc.etapa_registro(actas[:3])
        self.assertEqual(info["etapa"], 5)                                         # la denegada no cuenta
        self.assertIn("más atrasada de 2 actas", tc.texto_avance(info["nombre"], info))
        self.assertIsNone(tc.etapa_registro([]))
        self.assertIsNone(tc.etapa_registro([cartera[2]])["etapa"])

    def test_acta_propia_aunque_tenga_otro_nombre(self):
        cartera = [{"acta": "123456", "cliente_id": 7, "denominacion": None, "clase": 35}]
        self.assertEqual(len(tc.actas_del_trabajo({"cliente_id": 7, "marca": "AKRO LAB", "acta": "123456"}, cartera)), 1)

    def test_migracion_una_sola_vez(self):
        class Cur:
            def __init__(s): s.sql, s.hechas, s.rowcount = [], set(), 0
            def execute(s, sql, val=None):
                s.sql.append((sql, val))
                if sql.startswith("INSERT INTO trabajos_migraciones"):
                    s.rowcount = 0 if val[0] in s.hechas else 1
                    s.hechas.add(val[0])
        cur = Cur()
        tc.migrar_etapas(cur)
        updates = [x for x in cur.sql if x[0].startswith("UPDATE trabajos")]
        self.assertEqual(len(updates), 5)
        reg = [u for u in updates if u[1] == ("registro",)][0][0]
        self.assertIn("WHEN 3 THEN 4", reg); self.assertIn("WHEN 5 THEN 7", reg)
        n = len(cur.sql); tc.migrar_etapas(cur)
        self.assertFalse([x for x in cur.sql[n:] if x[0].startswith("UPDATE")])   # la segunda vez no toca nada



class ActasConVistaUOposicion(unittest.TestCase):
    CAND = [
        {"acta": "4811636", "denominacion": "VISION SOBERANA", "clase": 41, "tuvo_vista": True, "vista_fecha_notificacion": "2026-11-05", "fuente": "boletin"},
        {"acta": "4811635", "denominacion": "Visión Soberana", "clase": 35, "tuvo_vista": True, "vista_fecha_notificacion": "2026-11-04", "fuente": "boletin"},
        {"acta": "4700001", "denominacion": "OTRA", "clase": 9, "tuvo_oposicion": True, "oposicion_fecha_notificacion": "2026-10-14", "fuente": "boletin"},
        {"acta": "4700002", "denominacion": "SIN NADA", "clase": 9, "fuente": "boletin"},
    ]

    def test_varias_actas_mismo_nombre(self):
        el, motivo, del_tipo = tc.elegir_actas_conflicto({"servicio": "vista", "marca": "VISION SOBERANA"}, self.CAND)
        self.assertEqual([c["acta"] for c in el], ["4811635", "4811636"])
        self.assertEqual(motivo, "mismo nombre de marca")
        self.assertEqual(tc.notificacion_de(el, "vista"), dt.date(2026, 11, 4))   # la más temprana
        self.assertIn("Actas 4811635 (cl. 35), 4811636 (cl. 41) detectadas solas", tc.texto_conflicto(el, motivo, "vista"))

    def test_unica_del_tipo_aunque_cambie_el_nombre(self):
        el, motivo, _ = tc.elegir_actas_conflicto({"servicio": "oposicion", "marca": "MÁS"}, self.CAND)
        self.assertEqual(([c["acta"] for c in el], motivo), (["4700001"], "única acta del cliente con oposición"))
        self.assertEqual(tc.notificacion_de(el, "oposicion"), dt.date(2026, 10, 14))

    def test_varias_sin_coincidencia_no_asigna(self):
        el, motivo, del_tipo = tc.elegir_actas_conflicto({"servicio": "vista", "marca": "OTRA COSA"}, self.CAND)
        self.assertEqual((el, motivo), ([], None))
        self.assertEqual(len(del_tipo), 2)

    def test_cartera_con_oposicion_cuenta_para_los_dos(self):
        cart = [{"acta": "1", "denominacion": "X", "tuvo_oposicion": True, "fuente": "cartera"}]
        self.assertEqual(len(tc.elegir_actas_conflicto({"servicio": "vista", "marca": "X"}, cart)[0]), 1)
        self.assertEqual(len(tc.elegir_actas_conflicto({"servicio": "oposicion", "marca": "X"}, cart)[0]), 1)
        self.assertEqual(tc.elegir_actas_conflicto({"servicio": "registro", "marca": "X"}, cart)[0], [])



class SolicitudesDeLaMarca(unittest.TestCase):
    # MATEACIDO: 3 solicitudes, solo la 4811637 recibió vista; el trabajo arrancó con la 4811635 (de la planilla)
    FILAS = [
        {"acta": "4811635", "denominacion": "MATEACIDO", "clase": 41, "estado_tramite": "En trámite", "tuvo_oposicion": False, "fuente": "cartera"},
        {"acta": "4811635", "denominacion": "MATEACIDO", "clase": 41, "fecha_presentacion": "2026-10-16", "fuente": "escaneo"},
        {"acta": "4811636", "denominacion": "MATEACIDO", "clase": 35, "fecha_presentacion": "2026-10-16", "fuente": "escaneo"},
        {"acta": "4811637", "denominacion": "MATEACIDO", "clase": 43, "fecha_presentacion": "2026-10-16", "fuente": "escaneo"},
        {"acta": "4811637", "denominacion": "MATEACIDO", "clase": 43, "tuvo_vista": True, "vista_fecha_notificacion": "2026-10-29", "fuente": "boletin"},
        {"acta": "4700000", "denominacion": "OTRA MARCA", "clase": 9, "fuente": "escaneo"},
    ]

    def test_fusion_y_orden(self):
        sols = tc.fusionar_solicitudes(self.FILAS)
        self.assertEqual(len(sols), 4)
        t = {"servicio": "vista", "marca": "Mateácido", "acta": "4811635", "actas_extra": None}
        lista = tc.solicitudes_de_marca(t, sols)
        self.assertEqual([m["acta"] for m in lista], ["4811637", "4811635", "4811636"])   # la de la vista primero
        self.assertTrue(lista[0]["con_problema"]); self.assertFalse(lista[1]["con_problema"])
        m35 = [m for m in lista if m["acta"] == "4811635"][0]
        self.assertEqual((m35["estado_tramite"], m35["fecha_presentacion"], m35["en_cartera"]), ("En trámite", "2026-10-16", True))

    def test_texto_para_copiar(self):
        self.assertEqual(tc.texto_solicitud("MÁS", {"acta": "4790681", "clase": 36, "tipo": "M"}), "MÁS (clase 36, acta 4790681, MIXTA)")
        self.assertEqual(tc.texto_solicitud("MÁS", {"acta": "4790681", "clase": 36, "tipo": "Denominativa"}), "MÁS (clase 36, acta 4790681, DENOMINATIVA)")
        self.assertEqual(tc.texto_solicitud("X", {"acta": "1"}), "X (acta 1)")
        filas = [dict(f, tipo="M") for f in self.FILAS]
        lista = tc.solicitudes_de_marca({"servicio": "vista", "marca": "Mateácido", "acta": "4811635"}, tc.fusionar_solicitudes(filas))
        self.assertEqual(lista[0]["para_copiar"], "Mateácido (clase 43, acta 4811637, MIXTA)")

    def test_la_principal_pasa_a_ser_la_de_la_vista(self):
        sols = tc.fusionar_solicitudes(self.FILAS)
        el, motivo, del_tipo = tc.elegir_actas_conflicto({"servicio": "vista", "marca": "MATEACIDO"}, sols)
        self.assertEqual(([c["acta"] for c in el], motivo), (["4811637"], "mismo nombre de marca"))
        self.assertNotIn("4811635", {c["acta"] for c in del_tipo})   # la cargada no tiene vista → se cambia
        self.assertEqual(tc.notificacion_de(el, "vista"), dt.date(2026, 10, 29))

    def test_vista_de_marcas_en_la_cartera_es_vista_no_oposicion(self):
        sols = tc.fusionar_solicitudes([{"acta": "1", "denominacion": "X", "tuvo_oposicion": True,
                                         "detalle_oposicion": "/Date(1793715300000)/ - Vista de Marcas - None", "fuente": "cartera"}])
        self.assertTrue(sols[0].get("tuvo_vista"))
        self.assertTrue(tc.es_del_tipo(sols[0], "vista"))
        self.assertFalse(tc.es_del_tipo(sols[0], "oposicion"))



class NosOponemos(unittest.TestCase):
    def test_primero_30_dias_despues_3_meses(self):
        t = tc.completar({"servicio": "oposicion", "etapa": 0, "estado": "activo", "alerta_id": 9,
                          "plazo_presentar": "2026-12-04"}, HOY)
        self.assertEqual((t["vencimiento"], t["vence_presentar"], t["alerta"]["nivel"]), (dt.date(2026, 12, 4), True, "verde"))
        t = tc.completar({"servicio": "oposicion", "etapa": 3, "estado": "activo", "alerta_id": 9,
                          "plazo_presentar": "2026-12-04", "notificacion": "2026-12-20"}, HOY)
        self.assertEqual((t["vencimiento"], t["vence_presentar"]), (dt.date(2027, 3, 20), False))

    def test_sin_publicar_todavia(self):
        t = tc.completar({"servicio": "oposicion", "etapa": 0, "estado": "activo", "alerta_id": 9}, HOY)
        self.assertEqual((t["alerta"]["nivel"], t["alerta"]["texto"]), ("falta", "Esperando la publicación"))
        self.assertEqual(tc.plazo_para_oponerse("2026-11-04"), dt.date(2026, 12, 4))

    def test_oposicion_comun_no_cambia(self):
        t = tc.completar({"servicio": "oposicion", "etapa": 1, "estado": "activo", "plazo_presentar": "2026-12-04"}, HOY)
        self.assertEqual(t["alerta"]["nivel"], "falta")   # sin alerta de origen, el plazo de presentar no cuenta
        self.assertFalse(t["nos_oponemos"])


class TomaIntervencion(unittest.TestCase):
    def test_toma_en_grilla(self):
        g = ["2026-11-20|Toma Intervención|TOMA INTERVENCIÓN", "2026-11-20|Recibo de Ingreso|Toma de Intervención",
             "2026-11-20|Otros|Poder", "2026-11-20|Formulario|"]
        self.assertEqual(tc.toma_en_grilla(g, "2026-11-18"), tc.a_fecha("2026-11-20"))
        self.assertIsNone(tc.toma_en_grilla(g, "2026-11-21"))           # toma anterior a la contratación: otro agente
        self.assertIsNone(tc.toma_en_grilla(["2026-11-20|Otros|Poder"], None))
        self.assertIsNone(tc.toma_en_grilla(None, None))

    def test_novedades_sin_repetir(self):
        import notificaciones_inpi as ni
        pre = "Nuevo movimiento en el expediente: 20/11/2026 · "
        filas = [{"tipo": "movimiento", "texto": pre + "Recibo de Ingreso — Toma de Intervención"},
                 {"tipo": "movimiento", "texto": pre + "Toma Intervención — TOMA INTERVENCIÓN"},
                 {"tipo": "movimiento", "texto": pre + "Formulario"},
                 {"tipo": "movimiento", "texto": pre + "Otros — Poder"},
                 {"tipo": "movimiento", "texto": pre + "Formulario"},
                 {"tipo": "movimiento", "texto": pre + "Otros — Poder"},
                 {"tipo": "movimiento", "texto": pre + "Recibo de Ingreso — Escritos de Marcas"},
                 {"tipo": "movimiento", "texto": "Nuevo movimiento en el expediente: 25/11/2026 · Otros — Poder"}]
        out = [f["texto"].replace(pre, "") for f in ni.novedades_sin_repetir(filas)]
        self.assertEqual(out, ["Toma Intervención — TOMA INTERVENCIÓN", "Otros — Poder",
                               "Recibo de Ingreso — Escritos de Marcas",
                               "Nuevo movimiento en el expediente: 25/11/2026 · Otros — Poder"])

    def test_sin_formulario_y_contestacion_unica(self):
        import notificaciones_inpi as ni
        pre = "Nuevo movimiento en el expediente: 13/11/2026 · "
        filas = [{"tipo": "movimiento", "texto": pre + "Otros — Escrito de contestación de la vista"},
                 {"tipo": "movimiento", "texto": pre + "Formulario"},
                 {"tipo": "movimiento", "texto": pre + "Formulario — "},
                 {"tipo": "movimiento", "texto": pre + "Recibo de Ingreso — Contestacion de vistas"},
                 {"tipo": "movimiento", "texto": pre + "Formulario de cambio de domicilio"},
                 {"tipo": "vista", "texto": "Formulario"}]
        out = [f["texto"].replace(pre, "") for f in ni.novedades_sin_repetir(filas)]
        self.assertEqual(out, ["Otros — Escrito de contestación de la vista", "Formulario de cambio de domicilio",
                               "Formulario"])

    def test_dictamenes_y_resoluciones(self):
        import notificaciones_inpi as ni
        pre = "Nuevo movimiento en el expediente: 25/11/2026 · "
        r = ni.resultado_inpi(pre + "Dictamen aconseja denegatoria fondo")
        self.assertEqual((r["titulo"], r["nivel"]), ("Dictamen: aconseja denegar", "rojo"))
        self.assertIn("fondo", r["explicacion"])
        self.assertEqual(ni.resultado_inpi(pre + "Dictamen aconseja concesión")["nivel"], "verde")
        self.assertEqual(ni.resultado_inpi(pre + "Resolución Denegatoria")["titulo"], "Resolución: denegada")
        self.assertIsNone(ni.resultado_inpi(pre + "Proyecto de resolución"))
        self.assertIsNone(ni.resultado_inpi(pre + "Escrito de contestación de la vista"))
        c = ni.clasificar_movimiento({"tipo": "movimiento", "acta": "1", "texto": pre + "Dictamen aconseja denegatoria fondo"})
        self.assertEqual((c["tipo"], c["icono"]), ("Dictamen: aconseja denegar", "⛔"))

    def test_menciones_en_notas(self):
        us = ["pame", "tomi", "Pame.G"]
        self.assertEqual(tc.menciones_en("Hola @Pame, mirá esto. @tomi", us, autor="tomi"), ["pame"])
        self.assertEqual(tc.menciones_en("Le escribí a x@pame.com", us), [])
        self.assertEqual(tc.menciones_en("@pame.g ok", us), ["Pame.G"])
        self.assertEqual(tc.menciones_en("Gracias @pame.", us), ["pame"])
        self.assertEqual(tc.menciones_en("@pamela no existe", us), [])
        self.assertEqual(tc.menciones_en("@pame @PAME", us), ["pame"])

    def test_etapa_automatica(self):
        for sid in tc.SERVICIOS_TOMA:
            s = tc.POR_ID[sid]
            self.assertEqual(s["etapas"][tc.ETAPA_TOMA - 1], "Toma de intervención")
            self.assertIn(tc.ETAPA_TOMA, s["auto_etapas"])


if __name__ == "__main__":
    unittest.main()
