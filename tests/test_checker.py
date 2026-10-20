"""Модульные тесты для :mod:`self_employed_checker.checker`.

Весь сетевой доступ замокан; общий сеанс, используемый classmethod API,
заменяется новым ``requests.Session`` в каждом тесте для изоляции.
"""

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

import requests

from self_employed_checker.checker import (
    APIError,
    InvalidDateError,
    InvalidInnError,
    SelfEmployedChecker,
    SelfEmployedCheckerError,
)

# ИНН с корректными официальными контрольными суммами.
VALID_INN10 = "7707083893"     # формат юрлица (10 цифр)
VALID_INN12 = "500100732259"   # формат физлица (12 цифр)


def make_response(json_payload=None, status_code=200, raise_error=None):
    """Создаёт мок-объект, похожий на ``requests.Response``."""
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = json_payload or {}
    if raise_error is not None:
        response.raise_for_status.side_effect = raise_error
    else:
        response.raise_for_status.return_value = None
    return response


class CheckerTestBase(unittest.TestCase):
    """Базовый класс, изолирующий каждый тест от общего сеанса по умолчанию."""

    def setUp(self):
        # Заставляем classmethod API создавать сеанс через
        # ``requests.Session()``, чтобы его можно было подменять в каждом
        # тесте и чтобы тесты не протекали состоянием друг в друга.
        patcher = patch.object(SelfEmployedChecker, "_default_session", None)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestCheckStatusHappyPath(CheckerTestBase):
    """Успешное взаимодействие с API."""

    def test_status_confirmed(self):
        """Подтверждённый статус возвращает True и отправляет ожидаемый payload."""
        session = Mock(spec=requests.Session)
        session.post.return_value = make_response({"status": True})

        with patch.object(SelfEmployedChecker, "_default_session", session):
            result = SelfEmployedChecker.check_status(VALID_INN12, "2023-10-01")

            self.assertTrue(result)
            session.post.assert_called_once()
            _, kwargs = session.post.call_args
            self.assertEqual(kwargs["json"],
                             {"inn": VALID_INN12, "requestDate": "2023-10-01"})

    def test_status_not_confirmed(self):
        """Неподтверждённый статус возвращает False."""
        session = Mock(spec=requests.Session)
        session.post.return_value = make_response({"status": False})
        with patch.object(SelfEmployedChecker, "_default_session", session):
            self.assertFalse(
                SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
            )

    def test_missing_status_field_defaults_false(self):
        """Ответ без ключа ``status`` трактуется как неподтверждённый."""
        session = Mock(spec=requests.Session)
        session.post.return_value = make_response({})
        with patch.object(SelfEmployedChecker, "_default_session", session):
            self.assertFalse(
                SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
            )

    def test_instance_api_with_custom_session(self):
        """Экземплярный ``check`` использует внедрённый сеанс."""
        session = Mock(spec=requests.Session)
        session.post.return_value = make_response({"status": True})
        with SelfEmployedChecker(session=session) as checker:
            self.assertTrue(checker.check(VALID_INN10, date(2023, 10, 1)))
        session.post.assert_called_once()


class TestApiErrorHandling(CheckerTestBase):
    """Преобразование HTTP-ошибок."""

    @staticmethod
    def _mock_session(post_side_effect):
        session = Mock(spec=requests.Session)
        session.post.side_effect = post_side_effect
        return session

    def _http_error(self, code_payload, status_code=400):
        exc = requests.exceptions.HTTPError()
        exc.response = make_response(code_payload, status_code=status_code)
        return exc

    def test_validation_failed_returns_false(self):
        session = self._mock_session(
            self._http_error({"code": "validation.failed"})
        )
        with patch.object(SelfEmployedChecker, "_default_session", session):
            self.assertFalse(
                SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
            )

    def test_service_unavailable_returns_false(self):
        session = self._mock_session(
            self._http_error(
                {"code": "taxpayer.status.service.unavailable.error"}, 503
            )
        )
        with patch.object(SelfEmployedChecker, "_default_session", session):
            self.assertFalse(
                SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
            )

    def test_rate_limited_returns_false(self):
        session = self._mock_session(
            self._http_error(
                {"code": "taxpayer.status.service.limited.error"}, 429
            )
        )
        with patch.object(SelfEmployedChecker, "_default_session", session):
            self.assertFalse(
                SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
            )

    def test_raise_on_api_error(self):
        """При raise_on_api_error=True выбрасывается APIError с кодом и HTTP-статусом."""
        session = self._mock_session(
            self._http_error({"code": "validation.failed"}, 400)
        )
        with patch.object(SelfEmployedChecker, "_default_session", session):
            with self.assertRaises(APIError) as ctx:
                SelfEmployedChecker.check_status(
                    VALID_INN10, "2023-10-01", raise_on_api_error=True
                )
            self.assertEqual(ctx.exception.code, "validation.failed")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_connection_error_returns_false(self):
        session = self._mock_session(requests.exceptions.ConnectionError())
        with patch.object(SelfEmployedChecker, "_default_session", session):
            self.assertFalse(
                SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
            )

    def test_timeout_returns_false(self):
        session = self._mock_session(requests.exceptions.Timeout())
        with patch.object(SelfEmployedChecker, "_default_session", session):
            self.assertFalse(
                SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
            )

    def test_timeout_raises_when_requested(self):
        session = self._mock_session(requests.exceptions.Timeout())
        with patch.object(SelfEmployedChecker, "_default_session", session):
            with self.assertRaises(APIError):
                SelfEmployedChecker.check_status(
                    VALID_INN10, "2023-10-01", raise_on_api_error=True
                )


class TestDateValidation(CheckerTestBase):
    """Поведение и нормализация ``_validate_date``."""

    def test_valid_string_normalised(self):
        self.assertEqual(
            SelfEmployedChecker._validate_date("2023-10-01"), "2023-10-01"
        )

    def test_datetime_object_accepted(self):
        self.assertEqual(
            SelfEmployedChecker._validate_date(datetime(2023, 10, 1, 12, 30)),
            "2023-10-01",
        )

    def test_date_object_accepted(self):
        self.assertEqual(
            SelfEmployedChecker._validate_date(date(2023, 10, 1)), "2023-10-01"
        )

    def test_invalid_format_rejected(self):
        for bad in ("invalid-date", "01-10-2023", "2023-10-1", "", None):
            with self.subTest(bad=bad), self.assertRaises(InvalidDateError):
                SelfEmployedChecker._validate_date(bad)

    def test_impossible_calendar_date_rejected(self):
        with self.assertRaises(InvalidDateError):
            SelfEmployedChecker._validate_date("2023-02-30")

    def test_future_date_rejected(self):
        future = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        with self.assertRaises(InvalidDateError):
            SelfEmployedChecker._validate_date(future)

    def test_today_is_allowed(self):
        today = date.today().strftime("%Y-%m-%d")
        self.assertEqual(SelfEmployedChecker._validate_date(today), today)


class TestInnValidation(CheckerTestBase):
    """Локальная проверка формата и контрольной суммы ИНН."""

    def test_valid_inns_pass(self):
        for inn in (VALID_INN10, VALID_INN12):
            with self.subTest(inn=inn):
                SelfEmployedChecker._validate_inn(inn)  # no exception

    def test_bad_length_rejected(self):
        for inn in ("1234", "12345678901", ""):
            with self.subTest(inn=inn), self.assertRaises(InvalidInnError):
                SelfEmployedChecker._validate_inn(inn)

    def test_non_digits_rejected(self):
        with self.assertRaises(InvalidInnError):
            SelfEmployedChecker._validate_inn("770708389a")

    def test_bad_checksum_rejected(self):
        with self.assertRaises(InvalidInnError):
            SelfEmployedChecker._validate_inn("123456789012")

    def test_invalid_inn_never_hits_network(self):
        """Защита производительности: некорректные ИНН отсекаются до любого HTTP-вызова."""
        session = Mock(spec=requests.Session)
        with patch.object(SelfEmployedChecker, "_default_session", session):
            for bad_inn in ("123456789012", "123"):  # неверная контрольная сумма / длина
                with self.subTest(inn=bad_inn), self.assertRaises(InvalidInnError):
                    SelfEmployedChecker.check_status(bad_inn, "2023-10-01")
            session.post.assert_not_called()


class TestSessionReuse(CheckerTestBase):
    """Поведение пулирования и переиспользования соединений."""

    def test_default_session_created_once(self):
        first = SelfEmployedChecker._get_default_session()
        second = SelfEmployedChecker._get_default_session()
        self.assertIs(first, second)

    def test_shared_session_survives_classmethod_calls(self):
        """Повторные вызовы check_status должны переиспользовать один сеанс из пула."""
        session = Mock(spec=requests.Session)
        session.post.return_value = make_response({"status": True})
        SelfEmployedChecker._default_session = session

        SelfEmployedChecker.check_status(VALID_INN10, "2023-10-01")
        SelfEmployedChecker.check_status(VALID_INN12, "2023-10-02")

        self.assertEqual(session.post.call_count, 2)
        session.close.assert_not_called()  # общий сеанс закрывать нельзя

    def test_instance_closes_own_session(self):
        with patch("self_employed_checker.checker.requests.Session") as Session:
            instance_session = Session.return_value
            checker = SelfEmployedChecker()
            checker.close()
            instance_session.close.assert_called_once()

    def test_instance_does_not_close_injected_session(self):
        session = Mock(spec=requests.Session)
        with SelfEmployedChecker(session=session):
            pass
        session.close.assert_not_called()


class TestExceptionHierarchy(unittest.TestCase):
    def test_hierarchy(self):
        self.assertTrue(issubclass(InvalidDateError, SelfEmployedCheckerError))
        self.assertTrue(issubclass(InvalidInnError, SelfEmployedCheckerError))
        self.assertTrue(issubclass(APIError, SelfEmployedCheckerError))


if __name__ == "__main__":
    unittest.main()
