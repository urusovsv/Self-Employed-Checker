# tests/test_checker.py

import unittest
from self_employed_checker.checker import SelfEmployedChecker

class TestSelfEmployedChecker(unittest.TestCase):
    
    def test_check_status_valid(self):
        # Замените на реальные тестовые данные
        result = SelfEmployedChecker.check_status("1234567890", "2023-10-01")
        self.assertIsInstance(result, bool)  # Ожидаем, что результат — булево значение

    def test_check_status_invalid_date(self):
        result = SelfEmployedChecker.check_status("1234567890", "2025-01-01")
        self.assertFalse(result)  # Дата в будущем

    def test_check_status_invalid_inn(self):
        result = SelfEmployedChecker.check_status("invalid_inn", "2023-10-01")
        self.assertFalse(result)  # Неверный ИНН

if __name__ == '__main__':
    unittest.main()
