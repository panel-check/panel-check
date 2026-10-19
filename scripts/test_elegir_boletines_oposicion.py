"""Pruebas de la rotación nocturna de oposiciones (elegir_boletines_oposicion.elegir).

Sin base: se simula el cursor con filas en memoria.
Ejecutar: cd scripts && python3 -m unittest test_elegir_boletines_oposicion

Corrige el bug detectado el 19/10/2026: la rotación elegía siempre el mismo
top-5 por cercanía de cierre (boletines 11117-11121, tres noches seguidas)
y nunca avanzaba a boletines menos urgentes dentro de la ventana de
oposición.
"""
import datetime as dt
import unittest

import elegir_boletines_oposicion as m

TZ = dt.timezone.utc
HOY = dt.date(2026, 10, 19)


class _FakeCursor:
    def __init__(self, filas):
        self._filas = filas

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._filas


class _FakeConn:
    def __init__(self, filas):
        self._filas = filas

    def cursor(self, **k):
        return _FakeCursor(self._filas)


def _fila(numero, fecha, ultima=None):
    return {"numero": numero, "fecha": fecha, "oposiciones_buscadas_en": ultima}


class RotacionNocturna(unittest.TestCase):
    def test_no_repite_el_mismo_top5_por_urgencia_cuando_hay_mas_candidatos(self):
        # 5 boletines muy urgentes ya revisados anoche + 2 menos urgentes
        # nunca revisados: antes del fix, los 2 nunca entraban.
        revisados_anoche = dt.datetime(2026, 10, 18, 2, tzinfo=TZ)
        filas = [_fila(str(n), dt.date(2026, 9, 16), revisados_anoche) for n in range(11117, 11122)]
        filas += [_fila(str(n), dt.date(2026, 9, 23)) for n in (11122, 11123)]

        elegidos = m.elegir(_FakeConn(filas), HOY, maximo=5)

        self.assertIn("11122", elegidos)
        self.assertIn("11123", elegidos)

    def test_nunca_revisados_van_primero_entre_si_por_urgencia(self):
        filas = [
            _fila("11200", dt.date(2026, 9, 16)),  # cierra antes (16/09)
            _fila("11201", dt.date(2026, 9, 18)),  # cierra después (18/09)
        ]
        self.assertEqual(m.elegir(_FakeConn(filas), HOY, maximo=5), ["11200", "11201"])

    def test_boletin_recien_revisado_pasa_al_final_de_la_cola(self):
        recien = dt.datetime(2026, 10, 19, 1, tzinfo=TZ)
        filas = [
            _fila("11300", dt.date(2026, 9, 16), recien),  # más urgente pero recién revisado
            _fila("11301", dt.date(2026, 9, 18)),  # nunca revisado, menos urgente
        ]
        self.assertEqual(m.elegir(_FakeConn(filas), HOY, maximo=5), ["11301", "11300"])

    def test_respeta_la_ventana_de_oposicion(self):
        # fecha + 30 (cierre) + hasta 6 días (miércoles de notificación) ya pasó
        filas = [_fila("11400", dt.date(2026, 1, 1))]
        self.assertEqual(m.elegir(_FakeConn(filas), HOY, maximo=5), [])


if __name__ == "__main__":
    unittest.main()
