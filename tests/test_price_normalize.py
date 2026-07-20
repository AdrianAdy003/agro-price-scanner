import pytest
from scrapers.base import normalize_price

def test_romanian_format():
    assert normalize_price("1.234,56") == 1234.56

def test_simple_decimal_comma():
    assert normalize_price("89,99") == 89.99

def test_simple_float():
    assert normalize_price("45.50") == 45.5

def test_with_currency():
    assert normalize_price("125,00 RON") == 125.0
    assert normalize_price("89.99 lei") == 89.99

def test_thousands_and_decimal():
    assert normalize_price("2.500,00") == 2500.0

def test_integer():
    assert normalize_price("150") == 150.0

def test_none():
    assert normalize_price("") is None
    assert normalize_price("N/A") is None
