"""
test_amazon_parser.py
Pruebas unitarias para el módulo amazon_parser (no requieren red).
"""
import pytest
from core.amazon_parser import is_amazon_url, _extract_from_slug


class TestIsAmazonUrl:
    def test_amazon_es(self):
        assert is_amazon_url("https://www.amazon.es/dp/B09XS7JWHH") is True

    def test_amazon_com(self):
        assert is_amazon_url("https://amazon.com/product/dp/B09XS7JWHH") is True

    def test_amazon_mx(self):
        assert is_amazon_url("https://www.amazon.com.mx/dp/B09XS7JWHH") is True

    def test_not_amazon(self):
        assert is_amazon_url("auriculares Sony WH-1000XM5") is False

    def test_random_url(self):
        assert is_amazon_url("https://www.pccomponentes.com/producto") is False


class TestExtractFromSlug:
    def test_standard_url(self):
        url = "https://www.amazon.es/Sony-WH-1000XM5-Auriculares-Inalambricos/dp/B09XS7JWHH/"
        result = _extract_from_slug(url)
        assert result is not None
        assert "Sony" in result
        assert "WH" in result

    def test_short_dp_url(self):
        url = "https://www.amazon.es/dp/B09XS7JWHH"
        result = _extract_from_slug(url)
        # URL sin slug legible, debe retornar None o cadena muy corta
        assert result is None or len(result) <= 10

    def test_invalid_url(self):
        result = _extract_from_slug("no_es_una_url")
        assert result is None
