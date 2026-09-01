"""Юнит-тесты нормализации ссылок «блок с ID N» → [N] (services/citation.py)."""
from app.services.citation import normalize_citations


class TestNormalizeCitations:
    MAX = 6

    def test_block_with_id_phrase(self):
        assert normalize_citations("Однако, в блоке с ID 2 и 4 упоминаются справочники.", self.MAX) == (
            "Однако, в [2] и [4] упоминаются справочники."
        )

    def test_plain_block_reference(self):
        assert normalize_citations("см. блок 3 для деталей", self.MAX) == "см. [3] для деталей"

    def test_block_number_phrase(self):
        assert normalize_citations("описано в блоке номер 2", self.MAX) == "описано в [2]"

    def test_block_hash_sign(self):
        assert normalize_citations("смотри блок №2", self.MAX) == "смотри [2]"

    def test_blocks_plural(self):
        assert normalize_citations("в блоках 2 и 4 сказано", self.MAX) == "в [2] и [4] сказано"

    def test_existing_citation_untouched(self):
        assert normalize_citations("текст [2] уже корректен", self.MAX) == "текст [2] уже корректен"

    def test_out_of_range_number_untouched(self):
        """«блоке 3002» — номер отсутствия, не ссылка на блок контекста."""
        text = "создаётся отсутствие в блоке 3002 Открытый Больничный"
        assert normalize_citations(text, self.MAX) == text

    def test_out_of_range_id_untouched(self):
        text = "по сообщению блока с ID 100 формируется журнал"
        assert normalize_citations(text, self.MAX) == text

    def test_zero_and_negative_untouched(self):
        assert normalize_citations("в блоке 0 нет данных", self.MAX) == "в блоке 0 нет данных"

    def test_no_numbers_after_block_word_untouched(self):
        """Слово «блокируется» не является ссылкой и не трогается."""
        text = "ЭЛН блокируется перед сохранением отсутствия"
        assert normalize_citations(text, self.MAX) == text

    def test_empty_answer_and_zero_max(self):
        assert normalize_citations("", self.MAX) == ""
        assert normalize_citations("в блоке 2", 0) == "в блоке 2"
