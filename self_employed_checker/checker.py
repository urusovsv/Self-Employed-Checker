"""Модуль проверки статуса самозанятого через открытое API ФНС России.

Примечания по производительности:
    * HTTP-соединения пулируются и переиспользуются через ``requests.Session``
      с адаптивным пулом соединений (keep-alive). При повторных вызовах это
      устраняет накладные расходы на рукопожатия TCP + TLS, которые обычно
      доминируют в задержке запросов к эндпоинту ФНС.
    * Один лениво создаваемый сеанс по умолчанию разделяется вызовами
      classmethod, поэтому ``SelfEmployedChecker.check_status(...)`` получает
      преимущество переиспользования соединений без изменений в коде вызывающей
      стороны.
    * Разбор даты использует быстрый путь с предскомпилированным регулярным
      выражением перед обращением к ``datetime.strptime``, что позволяет
      избежать относительно дорогого вызова strptime для корректных входных
      данных.
    * Валидация ИНН локально проверяет длину и официальные контрольные цифры,
      поэтому заведомо некорректные номера вообще не приводят к сетевому
      обмену.
"""

import logging
import re
from datetime import date, datetime
from typing import Optional, Union

import requests

logger = logging.getLogger(__name__)

__all__ = [
    "SelfEmployedChecker",
    "SelfEmployedCheckerError",
    "InvalidDateError",
    "InvalidInnError",
    "APIError",
]

#: Допустимый тип входных данных для даты: строка ``"YYYY-MM-DD"`` либо объект date/datetime.
DateLike = Union[str, date, datetime]

# Предскомпилированные шаблоны (компиляция один раз при импорте заметно
# быстрее повторного разбора шаблона при каждом вызове).
_INN_RE = re.compile(r"^\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Таблицы весов для официальной проверки контрольной суммы ИНН.
_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_CONTROL_WEIGHTS = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


class SelfEmployedCheckerError(Exception):
    """Базовое исключение для всех ошибок SelfEmployedChecker."""


class InvalidDateError(SelfEmployedCheckerError):
    """Возникает при неверном формате даты или дате в будущем."""


class InvalidInnError(SelfEmployedCheckerError):
    """Возникает при недопустимой длине ИНН или несовпадении контрольной суммы."""


class APIError(SelfEmployedCheckerError):
    """Возникает, когда API возвращает ошибочный ответ.

    Атрибуты:
        code: Машинночитаемый код ошибки, возвращённый API ФНС (если есть).
        status_code: HTTP-код ошибочного ответа (если доступен).
    """

    def __init__(self, message: str, code: Optional[str] = None,
                 status_code: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _validate_inn_checksum(inn: str) -> bool:
    """Проверяет контрольные цифры ИНН (10- и 12-значный форматы).

    Это чисто вычислительная защита, предотвращающая бессмысленные сетевые
    обмены для некорректных номеров; выполняется за микросекунды.
    """
    digits = [int(ch) for ch in inn]

    def control(digits_: tuple, weights: tuple) -> int:
        return sum(d * w for d, w in zip(digits_, weights)) % 11 % 10

    if len(digits) == 10:
        return control(tuple(digits), _INN10_WEIGHTS) == digits[9]

    # 12-значный ИНН: две контрольные цифры (11-я и 12-я).
    return (
        control(tuple(digits[:10]), _INN12_WEIGHTS) == digits[10]
        and control(tuple(digits[:11]), _INN12_CONTROL_WEIGHTS) == digits[11]
    )


class SelfEmployedChecker:
    """Проверяет статус самозанятого физического лица через API ФНС России.

    Класс предоставляет как функциональный API (classmethod), работающий с
    общим сеансом с пулом соединений, так и объектно-ориентированный API, где
    каждый экземпляр владеет собственным :class:`requests.Session` (удобно для
    пользовательских заголовков, прокси или явного управления жизненным
    циклом)::

        # Простое использование — пулирование соединений происходит автоматически:
        SelfEmployedChecker.check_status("1234567890", "2023-10-01")

        # Продвинутое использование — собственный сеанс, поддержка context manager:
        with SelfEmployedChecker(timeout=(3.05, 10)) as checker:
            checker.check_status("1234567890", "2023-10-01")

    Атрибуты:
        API_URL (str): URL эндпоинта ФНС ``taxpayer_status``.
    """

    API_URL = "https://statusnpd.nalog.ru:443/api/v1/tracker/taxpayer_status"

    #: Таймауты по умолчанию (connect, read) в секундах. Короткий таймаут
    #: соединения позволяет быстро реагировать на недоступность сервиса;
    #: таймаут чтения ограничивает время ожидания медленных ответов.
    DEFAULT_TIMEOUT = (3.05, 10)

    #: Человекочитаемые сообщения для известных кодов ошибок ФНС.
    ERROR_MESSAGES = {
        "validation.failed": "Ошибка валидации",
        "taxpayer.status.service.unavailable.error": "Сервис временно недоступен",
        "taxpayer.status.service.limited.error": "Превышен лимит запросов",
    }

    _default_session: Optional[requests.Session] = None

    # ------------------------------------------------------------------ #
    # Управление сеансами
    # ------------------------------------------------------------------ #

    @classmethod
    def _get_default_session(cls) -> requests.Session:
        """Возвращает лениво создаваемый общий сеанс с настроенным пулом.

        Параметры ``pool_connections``/``pool_maxsize`` включают keep-alive
        переиспользование TCP+TLS соединений между вызовами — это основной
        источник ускорения при повторных проверках.
        """
        if cls._default_session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=10, pool_maxsize=10
            )
            session.mount("https://", adapter)
            session.headers.update({
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            cls._default_session = session
        return cls._default_session

    def __init__(self, api_url: Optional[str] = None,
                 session: Optional[requests.Session] = None,
                 timeout: tuple = DEFAULT_TIMEOUT):
        """
        Args:
            api_url: Переопределение эндпоинта ФНС (полезно для тестов/зеркал).
            session: Пользовательский :class:`requests.Session`; если не передан,
                создаётся новый.
            timeout: Таймаут ``(connect, read)`` в секундах.
        """
        self.api_url = api_url or self.API_URL
        self.timeout = timeout
        self._session = session or requests.Session()
        self._owns_session = session is None

    def close(self) -> None:
        """Закрывает базовый сеанс, если данный экземпляр создал его сам."""
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> "SelfEmployedChecker":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Публичный API
    # ------------------------------------------------------------------ #

    @classmethod
    def check_status(cls, inn: str, request_date: DateLike, *,
                     raise_on_api_error: bool = False) -> bool:
        """Проверяет, имеет ли лицо текущий статус самозанятого.

        Использует общий сеанс с пулом соединений для минимальной задержки
        на один вызов.

        Args:
            inn: Идентификационный номер налогоплательщика (ИНН) — 10 или 12
                цифр. Проверяется локально (формат + официальная контрольная
                сумма) до любого сетевого вызова.
            request_date: Дата запроса в виде строки ``YYYY-MM-DD`` или объекта
                ``date``/``datetime``. Не может лежать в будущем.
            raise_on_api_error: При ``True`` сбои API/сети вызывают
                :class:`APIError` вместо тихого возврата ``False``.

        Returns:
            ``True``, если статус самозанятого подтверждён; иначе ``False``.

        Raises:
            InvalidInnError: Если ИНН некорректен или проверка контрольной
                суммы не пройдена.
            InvalidDateError: Если формат даты неверен или дата в будущем.
            APIError: Только при ``raise_on_api_error=True`` и сбое запроса.
        """
        checker = cls()
        checker._session = cls._get_default_session()
        checker._owns_session = False  # общий сеанс закрывать нельзя
        return checker._check_status_impl(inn, request_date,
                                          raise_on_api_error=raise_on_api_error)

    def check(self, inn: str, request_date: DateLike, *,
              raise_on_api_error: bool = False) -> bool:
        """Экземплярный вариант :meth:`check_status`, использующий сеанс объекта."""
        return self._check_status_impl(inn, request_date,
                                       raise_on_api_error=raise_on_api_error)

    # ------------------------------------------------------------------ #
    # Внутренняя реализация
    # ------------------------------------------------------------------ #

    def _check_status_impl(self, inn: str, request_date: DateLike, *,
                           raise_on_api_error: bool) -> bool:
        self._validate_inn(inn)
        normalized_date = self._validate_date(request_date)

        payload = {"inn": inn, "requestDate": normalized_date}

        try:
            response = self._session.post(
                self.api_url, json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            status = bool(response.json().get("status", False))
            # PII-гигиена: в логи попадает только префикс ИНН (первые 4 цифры),
            # полный номер налогоплательщика не должен оседать в лог-хранилищах.
            logger.debug("Статус ФНС для ИНН=%s**** дата=%s: %s",
                         inn[:4], normalized_date, status)
            return status
        except requests.exceptions.HTTPError as exc:
            return self._handle_http_error(exc.response, inn,
                                           raise_on_api_error)
        except requests.exceptions.RequestException as exc:
            # Покрывает Timeout, ConnectionError и любой другой сетевой сбой.
            logger.warning("Запрос к ФНС завершился ошибкой для ИНН=%s****: %s", inn[:4], exc)
            if raise_on_api_error:
                raise APIError(f"Сбой запроса к API ФНС: {exc}") from exc
            return False

    @staticmethod
    def _validate_inn(inn: str) -> None:
        """Проверяет формат и контрольную сумму ИНН без обращения к сети.

        Raises:
            InvalidInnError: Если ИНН не состоит из 10/12 цифр или проверка
                контрольной суммы не пройдена.
        """
        if not isinstance(inn, str) or not _INN_RE.match(inn) or len(inn) not in (10, 12):
            raise InvalidInnError(
                f"Неверный ИНН: {inn!r}. Ожидается 10 или 12 цифр."
            )
        if not _validate_inn_checksum(inn):
            raise InvalidInnError(
                f"Проверка контрольной суммы ИНН не пройдена: {inn!r}"
            )

    @staticmethod
    def _validate_date(request_date: DateLike) -> str:
        """Проверяет дату и приводит её к формату API ``YYYY-MM-DD``.

        Быстрый путь: предскомпилированное регулярное выражение отсеивает
        корректные строки до относительно дорогого вызова ``strptime``,
        который подтверждает календарную валидность.

        Args:
            request_date: Строка ``YYYY-MM-DD`` либо объект ``date``/``datetime``.

        Returns:
            Нормализованная строка ``YYYY-MM-DD``.

        Raises:
            InvalidDateError: Если значение неразбираемо или лежит в будущем.
        """
        if isinstance(request_date, datetime):
            parsed = request_date
        elif isinstance(request_date, date):
            parsed = datetime.combine(request_date, datetime.min.time())
        elif isinstance(request_date, str) and _DATE_RE.match(request_date):
            try:
                parsed = datetime.strptime(request_date, "%Y-%m-%d")
            except ValueError as e:  # синтаксически OK, но не реальный день
                raise InvalidDateError(
                    f"Неверная дата: {request_date}. Такого календарного дня не существует."
                ) from e
        else:
            raise InvalidDateError(
                f"Неверный формат даты: {request_date!r}. Ожидается YYYY-MM-DD."
            )

        if parsed.date() > date.today():
            raise InvalidDateError(
                f"Дата не может быть в будущем: {request_date}"
            )

        return parsed.strftime("%Y-%m-%d")

    def _handle_http_error(self, response: Optional[requests.Response],
                           inn: str, raise_on_api_error: bool) -> bool:
        """Преобразует HTTP-ошибку в запись лога / APIError / False.

        Args:
            response: Ошибочный HTTP-ответ (в редких случаях может быть ``None``).
            inn: Проверяемый ИНН (для структурированного логирования).
            raise_on_api_error: Выбрасывать ли исключение вместо возврата ``False``.

        Returns:
            ``False``, если ``raise_on_api_error`` отключён.
        """
        code: Optional[str] = None
        if response is not None:
            try:
                code = response.json().get("code")
            except ValueError:
                code = None

        message = self.ERROR_MESSAGES.get(code, "Неизвестная ошибка API")
        logger.warning("Ошибка API ФНС для ИНН=%s****: http=%s код=%s (%s)",
                       inn[:4],
                       response.status_code if response is not None else "?",
                       code, message)

        if raise_on_api_error:
            raise APIError(message, code=code,
                           status_code=response.status_code if response else None)
        return False
