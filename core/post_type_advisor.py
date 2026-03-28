"""
post_type_advisor.py
====================
Dado uno o varios tópicos seleccionados, consulta Gemini para sugerir los
3 mejores títulos de blog (evergreen / listicle / how-to) con máxima
indexabilidad SEO.

Coste: 1 request de Gemini para todos los tópicos juntos (batch).

Estructura de retorno:
    {
      "sugerencias": [
        {
          "topico":   "Título exacto del tópico",
          "evergreen": "Título artículo evergreen ultra-indexable",
          "listicle":  "Los 7 (o N) mejores... título listicle",
          "howto":     "Cómo... guía paso a paso título"
        },
        ...
      ]
    }
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.gemini_client import GeminiClient

# ---------------------------------------------------------------------------
# Tipos simples
# ---------------------------------------------------------------------------

class PostSuggestion:
    """Sugerencia de 3 títulos para un tópico dado."""

    def __init__(self, topico: str, evergreen: str, listicle: str, howto: str):
        self.topico    = topico
        self.evergreen = evergreen
        self.listicle  = listicle
        self.howto     = howto

    def to_dict(self) -> dict:
        return {
            "topico":    self.topico,
            "evergreen": self.evergreen,
            "listicle":  self.listicle,
            "howto":     self.howto,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PostSuggestion":
        return cls(
            topico    = d.get("topico", ""),
            evergreen = d.get("evergreen", ""),
            listicle  = d.get("listicle", ""),
            howto     = d.get("howto", ""),
        )


# ---------------------------------------------------------------------------
# MOCK
# ---------------------------------------------------------------------------

def _mock_suggestions(topics: list[str]) -> list[PostSuggestion]:
    """Genera sugerencias ficticias (modo MOCK, 0 tokens)."""
    time.sleep(0.5)
    result = []
    for t in topics:
        short = t[:40]
        result.append(PostSuggestion(
            topico    = t,
            evergreen = f"[MOCK] La guía definitiva sobre {short}: todo lo que necesitas saber",
            listicle  = f"[MOCK] Los 7 aspectos clave de {short} que cambiarán tu perspectiva",
            howto     = f"[MOCK] Cómo abordar {short} paso a paso desde la consulta médica",
        ))
    return result


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _build_prompt(topics: list[str]) -> str:
    topics_json = json.dumps(topics, ensure_ascii=False, indent=2)
    _meses = ["enero","febrero","marzo","abril","mayo","junio",
              "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    hoy = date.today()
    today_str = f"{hoy.day} de {_meses[hoy.month - 1]} de {hoy.year}"
    current_year = hoy.year
    return f"""\
Eres un experto en SEO y estrategia de contenidos médico-psicológicos en español.

La fecha de hoy es {today_str}. Esto es MUY IMPORTANTE: estamos en el año {current_year}.

Para cada tópico de la lista, sugiere EXACTAMENTE 3 artículos de blog con los títulos \
más indexables y atractivos posibles para Google en {current_year}.

Los 3 tipos de artículo son SIEMPRE estos (en este orden):
1. EVERGREEN — Artículo atemporal de fondo (reflexión, análisis, columna de opinión) \
que posiciona en búsquedas informativas de alta competencia.
2. LISTICLE — Lista tipo "Los N mejores / principales / formas de…" \
que atrae tráfico masivo. Incluye SIEMPRE un número entre 5 y 12 en el título.
3. HOWTO — Guía paso a paso que posiciona en featured snippets y \
búsquedas de tipo "cómo…". El título DEBE empezar por "Cómo" o "Guía para".

REGLAS PARA TÍTULOS DE ALTO IMPACTO:
• Máximo 65 caracteres por título.
• Si incluyes un año en el título, usa preferentemente {current_year}. Puedes citar años anteriores solo si el dato es real y relevante.
• Usa palabras de alto CTR: secreto, clave, definitivo, descubierto, alerta, real, probado.
• Sé ultra-específico: evita títulos vagos o genéricos.
• Orientado al lector del blog médico-psicológico: pacientes, familiares y profesionales.

Lista de tópicos:
{topics_json}

Responde ÚNICAMENTE con este objeto JSON válido, sin texto extra ni bloques markdown:
{{
  "sugerencias": [
    {{
      "topico":    "nombre exacto del tópico tal como aparece en la lista",
      "evergreen": "título del artículo evergreen",
      "listicle":  "título del listicle con número",
      "howto":     "título de la guía paso a paso"
    }}
  ]
}}
"""


# ---------------------------------------------------------------------------
# Función pública
# ---------------------------------------------------------------------------

def suggest_post_structure(
    gemini_client: "GeminiClient",
    topics: list[str],
) -> list[PostSuggestion]:
    """
    Consulta Gemini (1 request) para obtener los 3 mejores títulos de blog
    por cada tópico de la lista.

    Args:
        gemini_client: instancia configurada de GeminiClient.
        topics:        lista de tópicos seleccionados por el usuario.

    Returns:
        Lista de PostSuggestion (una por tópico).
    """
    if not topics:
        return []

    if gemini_client.mock_mode:
        return _mock_suggestions(topics)

    prompt   = _build_prompt(topics)
    raw_text = gemini_client.call_raw(prompt)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            data = json.loads(match.group())
        else:
            raise RuntimeError(
                f"No se pudo parsear la respuesta del asesor de posts:\n{raw_text[:400]}"
            )

    sugerencias_raw = data.get("sugerencias", [])

    # Mapear por tópico para preservar orden y cubrir tópicos no devueltos
    by_topic: dict[str, dict] = {s.get("topico", ""): s for s in sugerencias_raw}

    results: list[PostSuggestion] = []
    for t in topics:
        raw = by_topic.get(t, {})
        results.append(PostSuggestion(
            topico    = t,
            evergreen = raw.get("evergreen", f"Artículo sobre {t[:50]}"),
            listicle  = raw.get("listicle",  f"Los 7 aspectos clave de {t[:40]}"),
            howto     = raw.get("howto",     f"Cómo abordar {t[:50]} paso a paso"),
        ))
    return results
