"""Pruebas de los avisos por Telegram con temas (scripts/telegram_avisos.py), sin red ni base.
cd scripts && python3 -m unittest test_telegram_avisos"""

import json
import os
import unittest
from unittest import mock

import telegram_avisos as ta


class Resp:
    def __init__(self, datos, status=200):
        self._d, self.status_code, self.text = datos, status, json.dumps(datos)

    def json(self):
        return self._d


class FakeTelegram:
    """Un grupo con temas: guarda lo que se manda y los temas creados."""
    def __init__(self, foro=True, chat="-1001"):
        self.foro, self.chat, self.temas, self.enviados, self.siguiente = foro, chat, set(), [], 10

    def __call__(self, metodo, **d):
        if metodo == "getMe":
            return Resp({"ok": True, "result": {"username": "bot"}})
        if metodo == "getUpdates":
            return Resp({"ok": True, "result": [{"message": {"chat": {"id": int(self.chat)}, "text": "/start"}}]})
        if metodo == "getChat":
            return Resp({"ok": True, "result": {"is_forum": self.foro}})
        if metodo == "createForumTopic":
            self.siguiente += 1
            self.temas.add(self.siguiente)
            return Resp({"ok": True, "result": {"message_thread_id": self.siguiente, "name": d["name"]}})
        if metodo == "sendMessage":
            hilo = d.get("message_thread_id")
            if hilo and hilo not in self.temas:
                return Resp({"ok": False, "description": "Bad Request: message thread not found"}, 400)
            self.enviados.append((hilo, d["text"]))
            return Resp({"ok": True})
        raise AssertionError(metodo)


class TestTemas(unittest.TestCase):
    def setUp(self):
        self.config = {}
        self.parches = [
            mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "1234567:" + "a" * 30}, clear=False),
            mock.patch.object(ta, "_leer", lambda conn, k: self.config.get(k)),
            mock.patch.object(ta, "_guardar", lambda conn, k, v: self.config.__setitem__(k, v)),
            mock.patch.object(ta, "_conectar", lambda: None),
        ]
        for p in self.parches:
            p.start()
        os.environ.pop("TELEGRAM_CHAT_ID", None)

    def tearDown(self):
        for p in self.parches:
            p.stop()

    def test_cada_tema_a_su_hilo_y_se_crea_una_sola_vez(self):
        tg = FakeTelegram()
        with mock.patch.object(ta, "_tg", tg):
            self.assertTrue(ta.enviar("a", tema="corridas"))
            self.assertTrue(ta.enviar("b", tema="corridas"))
            self.assertTrue(ta.enviar("c", tema="sistema"))
        self.assertEqual(len(tg.temas), 2)
        hilos = [h for h, _ in tg.enviados]
        self.assertEqual(hilos[0], hilos[1])
        self.assertNotEqual(hilos[0], hilos[2])

    def test_tema_borrado_se_vuelve_a_crear(self):
        tg = FakeTelegram()
        with mock.patch.object(ta, "_tg", tg):
            ta.enviar("a", tema="boletines")
            tg.temas.clear()
            self.assertTrue(ta.enviar("b", tema="boletines"))
        self.assertEqual(len(tg.enviados), 2)

    def test_chat_privado_sin_temas(self):
        tg = FakeTelegram(foro=False, chat="555")
        with mock.patch.object(ta, "_tg", tg):
            self.assertTrue(ta.enviar("a", tema="oposiciones"))
        self.assertEqual(tg.enviados, [(None, "a")])

    def test_preparar_temas_crea_los_cinco(self):
        tg = FakeTelegram()
        with mock.patch.object(ta, "_tg", tg):
            self.assertTrue(ta.preparar_temas())
        self.assertEqual(len(tg.temas), len(ta.TEMAS))

    def test_grupo_migrado_a_supergrupo(self):
        ups = [{"message": {"chat": {"id": -5}, "text": "/start"}},
               {"message": {"chat": {"id": -5}, "migrate_to_chat_id": -1009}}]
        self.assertEqual(ta.chat_de_actualizaciones(ups), "-1009")


if __name__ == "__main__":
    unittest.main()
