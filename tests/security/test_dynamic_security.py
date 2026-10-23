"""Динамический анализ безопасности (рантайм-тесты, без сети).

Проверяет на живом коде:
  * инъекции в JSON-поле (аналог NoSQL-инъекции) — не проходят валидацию;
  * XSS-строки как ИНН/дата — корректно отвергаются типизированной валидацией;
  * SSRF через параметр api_url — конструктор принимает только строку,
    сетевой вызов идёт ровно на один URL из payload (заглушка фиксирует это);
  * отсутствие куки/CSRF-состояния в клиенте (stateless API-клиент);
  * логи не содержат полного ИНН (защита PII);
  * таймауты применяются (защита от Slowloris-подобных зависаний).
"""

import logging
import unittest
from unittest import mock

from self_employed_checker import (
    InvalidDateError,
    InvalidInnError,
    SelfEmployedChecker,
)

XSS_PAYLOADS = [
    "<script>alert('xss')</script>",
    "\"><img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "{{7*7}}",  # SSTI
    "' OR '1'='1 --",  # SQLi-строка
    "1234567890; DROP TABLE users--",
]


class TestInjectionDefenses(unittest.TestCase):
    """Инъекции любого вида не должны достигать сетевого слоя."""

    def test_xss_sqli_ssti_inn_rejected_before_network(self):
        with mock.patch.object(SelfEmployedChecker, "_get_default_session") as sess:
            for payload in XSS_PAYLOADS:
                with self.subTest(payload=payload):
                    with self.assertRaises(InvalidInnError):
                        SelfEmployedChecker.check_status(payload, "2023-10-01")
            sess.return_value.post.assert_not_called()

    def test_xss_sqli_date_rejected_before_network(self):
        checker = SelfEmployedChecker()
        for payload in XSS_PAYLOADS + ["2023-10-01' or 1=1--"]:
            with self.subTest(payload=payload):
                with self.assertRaises(InvalidDateError):
                    checker._validate_date(payload)

    def test_dict_json_injection_blocked(self):
        """NoSQL-подобная инъекция ({"$ne": null}) как ИНН отсекается типом."""
        checker = SelfEmployedChecker()
        with self.assertRaises(InvalidInnError):
            checker._validate_inn({"$ne": None})  # type: ignore[arg-type]
        with self.assertRaises(InvalidInnError):
            checker._validate_inn(None)  # type: ignore[arg-type]

    def test_payload_is_strictly_typed_after_validation(self):
        """В JSON-payload попадают только проверенные цифры и дата YYYY-MM-DD."""
        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"status": True}
        fake_session = mock.Mock()
        fake_session.post.return_value = fake_resp
        checker = SelfEmployedChecker(session=fake_session)
        self.assertTrue(checker.check("7777777775", "2023-10-01"))
        _, kwargs = fake_session.post.call_args
        inn, rdate = kwargs["json"]["inn"], kwargs["json"]["requestDate"]
        self.assertTrue(inn.isdigit() and len(inn) == 10)
        self.assertRegex(rdate, r"^\d{4}-\d{2}-\d{2}$")


class TestSSRFSurface(unittest.TestCase):
    """Поверхность SSRF: единственный источник URL — константа или явный аргумент."""

    def test_default_url_is_https_fns(self):
        self.assertTrue(SelfEmployedChecker.API_URL.startswith("https://"))
        self.assertIn("nalog.ru", SelfEmployedChecker.API_URL)

    def test_no_user_input_in_url(self):
        """ИНН/дата не могут попасть в URL — они передаются только в JSON-теле."""
        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"status": False}
        fake_session = mock.Mock()
        fake_session.post.return_value = fake_resp
        checker = SelfEmployedChecker(session=fake_session)
        checker.check("7777777775", "2023-10-01")
        args, kwargs = fake_session.post.call_args
        url = args[0] if args else kwargs.get("url", "")
        self.assertNotIn("7777777775", url)
        self.assertEqual(url, checker.api_url)


class TestStatelessnessAndCSRF(unittest.TestCase):
    """Клиент stateless: нет cookie-хранилища с секретами и форм-эндпоинтов."""

    def test_client_sends_no_cookies_by_default(self):
        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"status": False}
        fake_session = mock.Session = mock.MagicMock()
        fake_session.post.return_value = fake_resp
        real = SelfEmployedChecker()
        real._session = requests_session = __import__("requests").Session()
        requests_session.post = mock.Mock(return_value=fake_resp)
        real.check("7777777775", "2023-10-01")
        sent_headers = requests_session.post.call_args.kwargs.get("headers") or {}
        req = requests_session.post.call_args
        self.assertNotIn("Cookie", sent_headers)
        self.assertNotIn("X-CSRF", str(req))
        requests_session.close()


class TestPIILogging(unittest.TestCase):
    """Логи не должны содержать полный ИНН (12 цифр подряд)."""

    def test_logs_mask_free_of_full_inn(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        cap = Capture()
        logger = logging.getLogger("self_employed_checker.checker")
        logger.addHandler(cap)
        old_level, logger.level = logger.level, logging.DEBUG
        try:
            fake_resp = mock.Mock()
            fake_resp.json.return_value = {"status": True}
            fake_session = mock.Mock()
            fake_session.post.return_value = fake_resp
            checker = SelfEmployedChecker(session=fake_session)
            checker.check("777777777708", "2023-10-01")
            joined = " ".join(records)
            self.assertNotIn("777777777708", joined)
        finally:
            logger.removeHandler(cap)
            logger.setLevel(old_level)


class TestTimeoutEnforcement(unittest.TestCase):
    """Таймаут обязателен в каждом запросе (анти-Slowloris)."""

    def test_timeout_always_passed(self):
        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"status": False}
        fake_session = mock.Mock()
        fake_session.post.return_value = fake_resp
        checker = SelfEmployedChecker(session=fake_session)
        checker.check("7777777775", "2023-10-01")
        timeout = fake_session.post.call_args.kwargs["timeout"]
        self.assertIsNotNone(timeout)
        connect_t, read_t = timeout
        self.assertLess(connect_t, 10)
        self.assertLess(read_t, 60)


if __name__ == "__main__":
    unittest.main()
