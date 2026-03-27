"""
topic_discovery.py
==================
Módulo para el descubrimiento diario de tópicos usando Gemini.

Funcionamiento:
  - Consulta Gemini UNA VEZ al día y guarda el resultado en logs/daily_topics.json.
  - En recargas posteriores devuelve el cache sin gastar tokens.
  - Si el usuario pulsa "Refrescar" se fuerza una nueva consulta.

Estructura del cache:
    {
      "fecha": "YYYY-MM-DD",
      "medicina_relacionados":    ["Título 1", ..., "Título 5"],
      "medicina_no_relacionados": ["Título 1", ..., "Título 5"],
      "psicologia_relacionados":    ["Título 1", ..., "Título 5"],
      "psicologia_no_relacionados": ["Título 1", ..., "Título 5"]
    }
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.gemini_client import GeminiClient

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
_CACHE_PATH = Path("logs/daily_topics.json")

PROMPT_TOPIC_DISCOVERY = """\
Eres un editor jefe de un blog de referencia en salud, medicina clínica y psicología. \
Tu misión es generar los 20 temas más relevantes y con mayor potencial de impacto para hoy, {today}.

Genera EXACTAMENTE 20 títulos divididos en 4 grupos de 5:

━━━ GRUPO 1 — MEDICINA · Noticias y tendencias de actualidad ━━━
5 tópicos DIRECTAMENTE relacionados con medicina, salud física o investigación clínica.
Fuente: avances médicos, estudios recientes, enfermedades en auge, nuevos tratamientos, \
alertas de salud pública. Titula desde la perspectiva médica informativa.

━━━ GRUPO 2 — MEDICINA · Actualidad global reinterpretada ━━━
5 tópicos de noticias o eventos del mundo (economía, clima, tecnología, deportes, \
política, etc.) abordados DESDE la óptica de un médico hacia sus lectores.
Ejemplo: si hay subida del precio de la gasolina → \
"Cómo la crisis del combustible eleva los niveles de cortisol en las familias trabajadoras".

━━━ GRUPO 3 — PSICOLOGÍA · Noticias y tendencias de actualidad ━━━
5 tópicos DIRECTAMENTE relacionados con psicología, salud mental, neurociencia o bienestar \
emocional. Fuente: estudios publicados, tendencias en salud mental, nuevos enfoques \
terapéuticos, redes sociales y mente, etc.

━━━ GRUPO 4 — PSICOLOGÍA · Actualidad global reinterpretada ━━━
5 tópicos de noticias o eventos globales (economía, deportes, tecnología, cultura pop, \
política, etc.) abordados DESDE la óptica de un psicólogo.
Ejemplo: si hay un campeonato deportivo mundial → \
"La psicología detrás del miedo al fracaso en los atletas de élite y cómo superarlo".

━━━ REGLAS DE ORO PARA LOS TÍTULOS ━━━
• Máximo 15 palabras por título.
• Deben generar un deseo irresistible de hacer clic (alto CTR).
• Usa cifras cuando aporten impacto: "el 73% de las personas…", "en solo 3 semanas…".
• Usa palabras de poder: secreto, descubierto, alerta, clave, transformar, definitivo, real.
• Evita títulos vagos o genéricos; cada título debe ser ultra específico.
• Escribe en español natural de España.
• NO incluyas numeración ni puntos al inicio de cada título.

Responde ÚNICAMENTE con el siguiente objeto JSON válido:

{{
  "fecha": "{today}",
  "medicina_relacionados": [
    "Título 1",
    "Título 2",
    "Título 3",
    "Título 4",
    "Título 5"
  ],
  "medicina_no_relacionados": [
    "Título 1",
    "Título 2",
    "Título 3",
    "Título 4",
    "Título 5"
  ],
  "psicologia_relacionados": [
    "Título 1",
    "Título 2",
    "Título 3",
    "Título 4",
    "Título 5"
  ],
  "psicologia_no_relacionados": [
    "Título 1",
    "Título 2",
    "Título 3",
    "Título 4",
    "Título 5"
  ]
}}
"""


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cached_topics() -> dict | None:
    """
    Carga los tópicos desde el archivo de cache.
    Devuelve None si el archivo no existe o pertenece a un día anterior.
    """
    if not _CACHE_PATH.exists():
        return None
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("fecha") == str(date.today()):
            return data
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return None


def save_topics_cache(topics: dict) -> None:
    """Persiste los tópicos en el archivo de cache."""
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Consulta a Gemini
# ---------------------------------------------------------------------------

def fetch_daily_topics(gemini_client: "GeminiClient") -> dict:
    """
    Llama a Gemini para obtener los 20 tópicos del día, guarda el cache y devuelve el dict.
    Lanza RuntimeError si la respuesta no es parseable.
    """
    today_str = str(date.today())
    prompt = PROMPT_TOPIC_DISCOVERY.format(today=today_str)
    raw_text = gemini_client.call_raw(prompt)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: extraer el primer bloque JSON del texto
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            data = json.loads(match.group())
        else:
            raise RuntimeError(
                f"No se pudo parsear la respuesta de tópicos de Gemini:\n{raw_text[:400]}"
            )

    # Garantizar estructura mínima con los 4 grupos
    data["fecha"] = today_str
    for key in ("medicina_relacionados", "medicina_no_relacionados",
                "psicologia_relacionados", "psicologia_no_relacionados"):
        data.setdefault(key, [])

    save_topics_cache(data)
    return data


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------

def get_topics(gemini_client: "GeminiClient", force_refresh: bool = False) -> dict:
    """
    Devuelve los tópicos del día.

    - Si ya existe cache para hoy y `force_refresh` es False → devuelve cache sin coste.
    - En caso contrario → llama a Gemini, guarda cache y devuelve resultado.

    Returns:
        dict con claves: fecha, medicina_relacionados, medicina_no_relacionados,
                         psicologia_relacionados, psicologia_no_relacionados
    """
    if not force_refresh:
        cached = load_cached_topics()
        if cached:
            return cached
    return fetch_daily_topics(gemini_client)



# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cached_topics() -> dict | None:
    """
    Carga los tópicos desde el archivo de cache.
    Devuelve None si el archivo no existe o pertenece a un día anterior.
    """
    if not _CACHE_PATH.exists():
        return None
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("fecha") == str(date.today()):
            return data
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return None


def save_topics_cache(topics: dict) -> None:
    """Persiste los tópicos en el archivo de cache."""
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Consulta a Gemini
# ---------------------------------------------------------------------------

def fetch_daily_topics(gemini_client: "GeminiClient") -> dict:
    """
    Llama a Gemini para obtener los 10 tópicos del día, guarda el cache y devuelve el dict.
    Lanza RuntimeError si la respuesta no es parseable.
    """
    today_str = str(date.today())
    prompt = PROMPT_TOPIC_DISCOVERY.format(today=today_str)
    raw_text = gemini_client.call_raw(prompt)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: extraer el primer bloque JSON del texto
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            data = json.loads(match.group())
        else:
            raise RuntimeError(
                f"No se pudo parsear la respuesta de tópicos de Gemini:\n{raw_text[:400]}"
            )

    # Garantizar estructura mínima
    data["fecha"] = today_str
    data.setdefault("relacionados",    [])
    data.setdefault("no_relacionados", [])

    save_topics_cache(data)
    return data


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------

def get_topics(gemini_client: "GeminiClient", force_refresh: bool = False) -> dict:
    """
    Devuelve los tópicos del día.

    - Si ya existe cache para hoy y `force_refresh` es False → devuelve cache sin coste.
    - En caso contrario → llama a Gemini, guarda cache y devuelve resultado.

    Returns:
        dict con claves: fecha (str), relacionados (list[str]), no_relacionados (list[str])
    """
    if not force_refresh:
        cached = load_cached_topics()
        if cached:
            return cached
    return fetch_daily_topics(gemini_client)
