import requests
from datetime import datetime

class SelfEmployedChecker:
    API_URL = "https://statusnpd.nalog.ru:443/api/v1/tracker/taxpayer_status"

    @staticmethod
    def check_status(inn, request_date):
        # Проверка формата даты
        try:
            request_date_obj = datetime.strptime(request_date, "%Y-%m-%d")
        except ValueError:
            return False  # Неверный формат даты

        if request_date_obj > datetime.now():
            return False  # Дата не может быть в будущем

        payload = {
            "inn": inn,
            "requestDate": request_date
        }

        try:
            response = requests.post(SelfEmployedChecker.API_URL, json=payload)
            response.raise_for_status()  # Проверка статуса ответа
            
            data = response.json()
            return data.get("status", False)  # Возвращаем статус

        except requests.exceptions.HTTPError as http_err:
            error_data = response.json()
            error_code = error_data.get("code")
            
            # Обработка специфичных ошибок
            if error_code == "validation.failed":
                return False  # Ошибка валидации
            elif error_code == "taxpayer.status.service.unavailable.error":
                return False  # Сервис временно недоступен
            elif error_code == "taxpayer.status.service.limited.error":
                return False  # Превышено количество запросов
            else:
                return False  # Неизвестная ошибка

        except requests.exceptions.RequestException:
            return False  # Ошибка соединения
