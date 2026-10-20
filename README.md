# Self Employed Checker

Небольшая быстрая Python-библиотека для проверки статуса самозанятого
(плательщика НПД — налога на профессиональный доход) через открытое API
Федеральной налоговой службы (ФНС) России.

## Возможности

- **Пулирование соединений и keep-alive** — общий `requests.Session` с
  адаптивным HTTP-адаптером переиспользует TCP+TLS соединения между вызовами,
  устраняя накладные расходы на рукопожатия, которые обычно доминируют в задержке
  каждого запроса.
- **Локальная валидация ИНН (формат + официальная контрольная сумма)** —
  некорректные номера отклоняются за микросекунды *без* сетевого обмена.
- **Быстрая работа с датами** — предскомпилированная regex-фильтрация плюс
  нативная поддержка объектов `date`/`datetime` (не нужно форматировать строки).
- **Fail-fast таймауты** — по умолчанию `(connect=3.05s, read=10s)`, настраиваются.
- **Структурированное логирование** — каждый сбой API логируется с кодом ошибки
  ФНС; ошибки больше не проглатываются молча.
- **Явный режим ошибок** — передайте `raise_on_api_error=True`, чтобы получать
  насыщенные исключения `APIError(code=..., status_code=...)` вместо `False`.
- Иерархия пользовательских исключений, полные type hints, docstrings в стиле
  Google.
- 28 модульных тестов, весь доступ в сеть замокан, прогон <0.1 с.

## Установка

```bash
git clone <repository-url>
cd self_employed_checker_repo
pip install -r requirements.txt      # или: pip install .
```

## Быстрый старт

```python
from self_employed_checker import SelfEmployedChecker

is_self_employed = SelfEmployedChecker.check_status("500100732259", "2023-10-01")
```

### Продвинутое использование

```python
from datetime import date
from self_employed_checker import (
    SelfEmployedChecker, APIError, InvalidDateError, InvalidInnError,
)

# Собственный сеанс: свои таймауты, прокси, заголовки; context manager закрывает его.
with SelfEmployedChecker(timeout=(2, 5)) as checker:
    try:
        # Объекты date/datetime принимаются напрямую — форматирование не требуется.
        result = checker.check("7707083893", date(2023, 10, 1),
                               raise_on_api_error=True)
    except InvalidInnError:
        ...  # неверная длина/контрольная сумма ИНН (такие запросы не уходят в API)
    except InvalidDateError:
        ...  # неверный формат или дата в будущем
    except APIError as e:
        print(e.code, e.status_code)  # код ошибки ФНС + HTTP-статус
```

## API

| Метод | Описание |
|-------|----------|
| `SelfEmployedChecker.check_status(inn, request_date, *, raise_on_api_error=False)` | Classmethod, использующий общий сеанс с пулом соединений. |
| `checker.check(inn, request_date, *, raise_on_api_error=False)` | Экземплярный метод, использующий собственный сеанс объекта. |
| `SelfEmployedChecker(api_url=None, session=None, timeout=(3.05, 10))` | Конструктор; поддерживает внедрение зависимостей и протокол context manager. |

### Параметры

| Параметр | Тип | Описание |
|----------|-----|----------|
| `inn` | `str` | Идентификационный номер налогоплательщика — 10 или 12 цифр, проверяется локально по контрольной сумме. |
| `request_date` | `str \| date \| datetime` | Дата запроса (`YYYY-MM-DD`). Не может быть в будущем. |
| `raise_on_api_error` | `bool` | Выбрасывать `APIError` при сбоях вместо возврата `False`. |

### Возвращаемое значение

`True` — статус подтверждён; `False` — не подтверждён либо произошла ошибка
(если только не указан `raise_on_api_error=True`).

### Исключения

| Исключение | Возникает, когда |
|------------|------------------|
| `SelfEmployedCheckerError` | Базовый класс всех ошибок библиотеки. |
| `InvalidInnError` | ИНН имеет неверную длину/формат или не проходит официальную проверку контрольной суммы. |
| `InvalidDateError` | Дата не разбирается, не является реальным календарным днём или лежит в будущем. |
| `APIError` | Сбой API/сети при `raise_on_api_error=True`; содержит `.code` и `.status_code`. |

Известные коды ошибок ФНС переводятся в человекочитаемые сообщения
(`validation.failed`, `taxpayer.status.service.unavailable.error`,
`taxpayer.status.service.limited.error`) и логируются через стандартный модуль
`logging` логгером `self_employed_checker.checker`.

## Примечания по производительности

- Повторные вызовы `check_status()` переиспользуют одно пулированное
  HTTPS-соединение: после первого запроса каждый последующий вызов пропускает
  ~1 RTT на TCP-соединение и TLS-рукопожатие.
- Некорректные ИНН никогда не попадают в сеть — локальная проверка
  контрольной суммы стоит микросекунды.
- Регулярные выражения и таблицы кодов ошибок строятся один раз при импорте.

## Запуск тестов

```bash
python -m unittest discover -s tests -v
```

## Структура проекта

```
.
├── self_employed_checker/
│   ├── __init__.py          # Публичные экспорты и версия
│   └── checker.py           # Основная реализация
├── tests/
│   ├── __init__.py
│   └── test_checker.py      # Модульные тесты (полностью замоканы, без сети)
├── pyproject.toml           # Метаданные пакета
├── requirements.txt         # Зависимости времени выполнения
└── README.md
```

## Лицензия

Распространяется по лицензии, указанной в файле [LICENSE](LICENSE).

## Примечание

Модуль использует публичное API Федеральной налоговой службы России
(`statusnpd.nalog.ru`) для проверки статуса плательщика налога на
профессиональный доход (НПД).
