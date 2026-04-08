from __future__ import annotations

import os
import warnings
from functools import lru_cache

import requests
from loguru import logger
from urllib3.exceptions import InsecureRequestWarning


_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in _FALSE_VALUES


@lru_cache(maxsize=1)
def get_wp_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "TuEden/1.0"})

    verify_ssl = _env_flag("WP_SSL_VERIFY", True)
    if not verify_ssl:
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
        session.verify = False
        logger.warning("[WP SSL] Verificación TLS desactivada por WP_SSL_VERIFY=false")

    return session


def wp_request(method: str, url: str, **kwargs) -> requests.Response:
    return get_wp_session().request(method=method, url=url, **kwargs)


def build_ssl_error_help(exc: Exception) -> str:
    detail = str(exc).lower()
    if "unable to get local issuer certificate" in detail or "certificate verify failed" in detail:
        return (
            "La conexión HTTPS no pudo validar la cadena TLS del dominio destino. "
            "Revisa el certificado servido por WordPress o, como último recurso, usa WP_SSL_VERIFY=false."
        )
    return "Verifica la cadena del certificado TLS del dominio WordPress."
