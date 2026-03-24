"""
Normalizacion de numeros de telefono.

Problema: los numeros llegan en muchos formatos:
  - Kapso: "51916973136" (sin +)
  - DB: "+51916973136" (con +)
  - Usuario dice: "999 888 555", "916973136", "+51 916 973 136"

Solucion:
  - normalizar(): limpia a formato "+51XXXXXXXXX" para guardar
  - extraer_digitos(): saca solo los ultimos 9 digitos para buscar
"""

import re

# Codigo de pais por defecto (Peru)
COUNTRY_CODE = "51"
LOCAL_DIGITS = 9  # Peru usa 9 digitos despues del codigo de pais


def normalizar(numero: str) -> str:
    """
    Normaliza un numero a formato +51XXXXXXXXX.

    Ejemplos:
      "999888555"       → "+51999888555"
      "51916973136"     → "+51916973136"
      "+51 916 973 136" → "+51916973136"
      "916973136"       → "+51916973136"
      "+51916973136"    → "+51916973136"
      "0051999888555"   → "+51999888555"
    """
    if not numero:
        return ""

    # Quitar todo excepto digitos
    digitos = re.sub(r"[^\d]", "", numero)

    if not digitos:
        return ""

    # Si empieza con 00 (formato internacional), quitar
    if digitos.startswith("00"):
        digitos = digitos[2:]

    # Si tiene codigo de pais (51) + 9 digitos = 11 digitos
    if len(digitos) == len(COUNTRY_CODE) + LOCAL_DIGITS and digitos.startswith(COUNTRY_CODE):
        return f"+{digitos}"

    # Si son solo 9 digitos locales, agregar codigo de pais
    if len(digitos) == LOCAL_DIGITS:
        return f"+{COUNTRY_CODE}{digitos}"

    # Si son mas de 11 digitos, tomar los ultimos 9 y agregar codigo
    if len(digitos) > len(COUNTRY_CODE) + LOCAL_DIGITS:
        return f"+{COUNTRY_CODE}{digitos[-LOCAL_DIGITS:]}"

    # Cualquier otro caso, agregar + al inicio
    return f"+{digitos}"


def extraer_digitos(numero: str) -> str:
    """
    Extrae los ultimos 9 digitos de un numero (para busqueda tolerante).

    Ejemplos:
      "+51916973136" → "916973136"
      "51916973136"  → "916973136"
      "916973136"    → "916973136"
      "999 888 555"  → "999888555"
    """
    if not numero:
        return ""
    digitos = re.sub(r"[^\d]", "", numero)
    if len(digitos) >= LOCAL_DIGITS:
        return digitos[-LOCAL_DIGITS:]
    return digitos
