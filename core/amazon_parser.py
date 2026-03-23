"""
amazon_parser.py
Extrae el nombre del producto desde una URL de Amazon.

Estrategia:
  1. Intenta parsear el slug de la URL (rápido, sin petición HTTP).
  2. Si falla o la URL no tiene slug legible, hace scraping del <title> de la página.
  3. Limpia el resultado para obtener solo el nombre del producto.
"""
from __future__ import annotations

import re
import warnings
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

# Suprimir advertencia SSL al usar verify=False (entornos corporativos con proxy)
warnings.filterwarnings("ignore", message=".*verify=False.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="httpx")


# User-Agent básico para no ser bloqueado de inmediato
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

# Sufijos que Amazon añade al título y que queremos eliminar
_AMAZON_TITLE_SUFFIXES = [
    r"\s*[-|:]\s*Amazon\..*$",
    r"\s*[-|:]\s*Comprar en Amazon.*$",
    r"\s*[-|:]\s*Amazon\.es.*$",
    r"\s*[-|:]\s*Amazon\.com.*$",
]


def extract_product_name(amazon_url: str) -> str:
    """
    Dado una URL de Amazon, devuelve el nombre del producto como string.
    Si no puede obtenerlo, devuelve una versión limpia del slug de la URL.

    Args:
        amazon_url: URL completa del producto en Amazon.
    Returns:
        Nombre del producto como cadena de texto.
    """
    # --- Intento 1: parsear slug de la URL --------------------------------
    slug_name = _extract_from_slug(amazon_url)
    if slug_name and len(slug_name) > 10:
        logger.info(f"[Amazon] Nombre extraído del slug: «{slug_name}»")
        return slug_name

    # --- Intento 2: scraping del <title> ----------------------------------
    scraped_name = _scrape_title(amazon_url)
    if scraped_name:
        logger.info(f"[Amazon] Nombre extraído por scraping: «{scraped_name}»")
        return scraped_name

    # --- Fallback: usar el slug crudo -------------------------------------
    fallback = slug_name or "producto Amazon"
    logger.warning(f"[Amazon] No se pudo obtener nombre; usando fallback: «{fallback}»")
    return fallback


# ----------------------------------------------------------------------
# Helpers privados
# ----------------------------------------------------------------------

def _extract_from_slug(url: str) -> str | None:
    """
    Amazon incluye el nombre del producto como slug en la URL:
      https://www.amazon.es/Sony-WH-1000XM5-Auriculares/dp/B09XS7JWHH/
      https://www.amazon.es/-/en/Cudy-P5-Cellular/dp/B0B711GG7N/
    El slug es la parte antes de /dp/, ignorando segmentos como '-' o 'en'.
    """
    try:
        path = urlparse(url).path   # ej: /-/en/Cudy-P5-Cellular/dp/B0B...
        # Buscar todos los segmentos antes de /dp/
        before_dp = path.split("/dp/")[0]   # '/-/en/Cudy-P5-Cellular'
        segments = [s for s in before_dp.split("/") if s and s not in ("-", "en", "es")]
        if segments:
            slug = segments[-1]   # último segmento con contenido real
            return slug.replace("-", " ").strip()
    except Exception:
        pass
    return None


def _scrape_title(url: str) -> str | None:
    """
    Hace una petición GET a la URL de Amazon y extrae el <title>.
    Amazon puede bloquear esto; es un intento de mejor esfuerzo.
    """
    try:
        with httpx.Client(
            headers=_HEADERS,
            timeout=10,
            follow_redirects=True,
            verify=False,           # evita SSLCertificateVerifyFailed en entornos corp
        ) as client:
            response = client.get(url)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Intentar el tag <title>
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()
            title = _clean_amazon_title(title)
            if title:
                return title

        # Intentar el span de título del producto
        product_title = soup.find("span", {"id": "productTitle"})
        if product_title:
            return product_title.get_text(strip=True)

    except Exception as exc:
        logger.warning(f"[Amazon] Scraping falló: {exc}")

    return None


def _clean_amazon_title(title: str) -> str:
    """Elimina los sufijos que Amazon añade al <title>."""
    for pattern in _AMAZON_TITLE_SUFFIXES:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    return title.strip()


def is_amazon_url(text: str) -> bool:
    """Detecta si el string dado parece una URL de Amazon."""
    return bool(re.search(r"amazon\.(com|es|com\.mx|co\.uk|de|fr)", text, re.IGNORECASE))
