"""
wp_taxonomy.py
Gestión de categorías y etiquetas de WordPress para clasificación automática de posts.

Flujo:
  1. fetch_categories() / fetch_tags() — obtiene taxonomías existentes de la REST API
  2. classify_post()                   — Gemini elige 1 categoría existing + N etiquetas
  3. resolve_tags()                    — mapea nombres de tags a IDs, crea los inexistentes
  4. assign_taxonomy()                  — función de conveniencia que orquesta todo lo anterior
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import requests
from loguru import logger
from requests.auth import HTTPBasicAuth

if TYPE_CHECKING:
    from core.gemini_client import GeminiClient


# ---------------------------------------------------------------------------
# Funciones de lectura/escritura contra la REST API de WordPress
# ---------------------------------------------------------------------------

def fetch_categories(base_url: str, auth: HTTPBasicAuth) -> list[dict]:
    """
    Obtiene todas las categorías existentes en WordPress.

    Returns:
        Lista de dicts con keys: id, name, slug, count
    """
    url = f"{base_url.rstrip('/')}/wp-json/wp/v2/categories"
    params = {"per_page": 100, "hide_empty": False}
    try:
        resp = requests.get(url, params=params, auth=auth, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[Taxonomy] {len(data)} categorías obtenidas de WordPress")
        return [{"id": c["id"], "name": c["name"], "slug": c["slug"], "count": c["count"]}
                for c in data]
    except Exception as exc:
        logger.error(f"[Taxonomy] Error al obtener categorías: {exc}")
        return []


def fetch_tags(base_url: str, auth: HTTPBasicAuth) -> list[dict]:
    """
    Obtiene todas las etiquetas existentes en WordPress.

    Returns:
        Lista de dicts con keys: id, name, slug, count
    """
    url = f"{base_url.rstrip('/')}/wp-json/wp/v2/tags"
    params = {"per_page": 100, "hide_empty": False}
    try:
        resp = requests.get(url, params=params, auth=auth, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[Taxonomy] {len(data)} etiquetas obtenidas de WordPress")
        return [{"id": t["id"], "name": t["name"], "slug": t["slug"], "count": t["count"]}
                for t in data]
    except Exception as exc:
        logger.error(f"[Taxonomy] Error al obtener etiquetas: {exc}")
        return []


def create_tag(base_url: str, auth: HTTPBasicAuth, name: str) -> int | None:
    """
    Crea una nueva etiqueta en WordPress.

    Returns:
        ID de la nueva etiqueta, o None si falló.
    """
    url = f"{base_url.rstrip('/')}/wp-json/wp/v2/tags"
    try:
        resp = requests.post(url, json={"name": name}, auth=auth, timeout=15)
        resp.raise_for_status()
        tag_id = resp.json()["id"]
        logger.info(f"[Taxonomy] Etiqueta creada: '{name}' → ID {tag_id}")
        return tag_id
    except Exception as exc:
        logger.error(f"[Taxonomy] Error al crear etiqueta '{name}': {exc}")
        return None


# ---------------------------------------------------------------------------
# Clasificador con Gemini
# ---------------------------------------------------------------------------

def classify_post(
    gemini: "GeminiClient",
    title: str,
    content_snippet: str,
    post_type: str,
    categories: list[dict],
) -> dict:
    """
    Usa Gemini para que elija 1 categoría existing y sugiera entre 3 y 6 etiquetas.

    Args:
        gemini:          Instancia de GeminiClient.
        title:           Título del post.
        content_snippet: Primeros ~400 caracteres del contenido (sin HTML).
        post_type:       Tipo de post (comparativa, guia, opinion, etc.)
        categories:      Lista de dicts {id, name, slug} con las categorías existentes.

    Returns:
        Dict con keys:
          - "category_id": int (ID de la categoría elegida)
          - "category_name": str (nombre legible)
          - "tags": list[str] (nombres de etiquetas sugeridas)
    """
    if not categories:
        logger.warning("[Taxonomy] Sin categorías disponibles, saltando clasificación")
        return {"category_id": None, "category_name": "", "tags": []}

    cats_list = "\n".join(
        f'  - ID {c["id"]}: "{c["name"]}" (slug: {c["slug"]})'
        for c in categories
    )

    # Limpiar HTML básico del snippet
    import re
    clean_snippet = re.sub(r"<[^>]+>", " ", content_snippet)[:400].strip()

    prompt = f"""Analiza el siguiente artículo de blog y clasifícalo.

TÍTULO: {title}
TIPO DE POST: {post_type}
EXTRACTO: {clean_snippet}

CATEGORÍAS DISPONIBLES EN EL BLOG (elige EXACTAMENTE 1, no puedes crear nuevas):
{cats_list}

INSTRUCCIONES:
1. Selecciona la categoría más adecuada de la lista anterior.
2. Sugiere entre 3 y 6 etiquetas relevantes en español (pueden ser nuevas).
3. Las etiquetas deben ser palabras clave específicas y útiles para SEO.
4. Responde ÚNICAMENTE con un JSON válido, sin explicaciones adicionales.

FORMATO DE RESPUESTA (JSON):
{{
  "category_id": <número entero del ID de la categoría elegida>,
  "category_name": "<nombre de la categoría elegida>",
  "tags": ["etiqueta1", "etiqueta2", "etiqueta3"]
}}"""

    try:
        raw = gemini.call_raw(prompt)
        # Limpiar posibles markdown code blocks
        import re as _re
        clean = _re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        result = json.loads(clean)

        # Validar que la categoría devuelta existe en nuestra lista
        valid_ids = {c["id"] for c in categories}
        if result.get("category_id") not in valid_ids:
            logger.warning(
                f"[Taxonomy] Gemini devolvió category_id={result.get('category_id')} "
                f"que no existe. Usando la primera categoría disponible."
            )
            result["category_id"]   = categories[0]["id"]
            result["category_name"] = categories[0]["name"]

        # Limpiar etiquetas
        result["tags"] = [str(t).strip() for t in result.get("tags", []) if str(t).strip()][:6]

        logger.info(
            f"[Taxonomy] Clasificación: categoría='{result['category_name']}' "
            f"| tags={result['tags']}"
        )
        return result

    except Exception as exc:
        logger.error(f"[Taxonomy] Error en classify_post: {exc}")
        return {"category_id": categories[0]["id"] if categories else None,
                "category_name": categories[0]["name"] if categories else "",
                "tags": []}


# ---------------------------------------------------------------------------
# Resolución de tags: mapear nombres → IDs, crear los que no existen
# ---------------------------------------------------------------------------

def resolve_tags(
    base_url: str,
    auth: HTTPBasicAuth,
    tag_names: list[str],
    existing_tags: list[dict],
) -> list[int]:
    """
    Convierte una lista de nombres de etiquetas en IDs de WordPress.
    Si una etiqueta no existe, la crea.

    Args:
        base_url:      URL base de WordPress.
        auth:          Credenciales HTTPBasicAuth.
        tag_names:     Nombres de etiquetas a resolver.
        existing_tags: Lista actual de tags de WP (de fetch_tags()).

    Returns:
        Lista de IDs de etiquetas (existentes o recién creadas).
    """
    # Índice nombre (normalizado) → ID
    existing_index = {t["name"].strip().lower(): t["id"] for t in existing_tags}
    # También indexar por slug
    existing_slug_index = {t["slug"].strip().lower(): t["id"] for t in existing_tags}

    ids: list[int] = []
    for name in tag_names:
        clean_name = name.strip()
        key = clean_name.lower()

        if key in existing_index:
            ids.append(existing_index[key])
            logger.debug(f"[Taxonomy] Tag existente: '{clean_name}' → ID {existing_index[key]}")
        elif key.replace(" ", "-") in existing_slug_index:
            slug_key = key.replace(" ", "-")
            ids.append(existing_slug_index[slug_key])
            logger.debug(f"[Taxonomy] Tag found by slug: '{clean_name}' → ID {existing_slug_index[slug_key]}")
        else:
            new_id = create_tag(base_url, auth, clean_name)
            if new_id:
                ids.append(new_id)
                # Actualizar índice para evitar duplicados dentro del mismo lote
                existing_index[key] = new_id

    return ids


# ---------------------------------------------------------------------------
# Generador de palabras clave SEO via Gemini
# ---------------------------------------------------------------------------

def generate_seo_keywords(
    gemini: "GeminiClient",
    title: str,
    content: str,
    focus_keyword: str = "",
) -> list[str]:
    """
    Usa Gemini para generar 7 palabras clave SEO orientadas a búsquedas reales.
    Incluye keywords cortas y frases de cola larga (2-4 palabras).

    Returns:
        Lista de strings en minúsculas, vacía si falla.
    """
    import re as _re
    clean_snippet = _re.sub(r"<[^>]+>", " ", content)[:500].strip()

    prompt = f"""Eres un experto SEO para un blog en español sobre tecnología, salud, bienestar y hogar.
Genera exactamente 7 palabras clave SEO en español para este artículo.
Deben ser búsquedas reales de Google: combina keywords cortas (1-2 palabras) y frases de cola larga (3-4 palabras).
NO repitas el título exacto ni la keyword principal. Varía con sinónimos e intenciones de búsqueda distintas.

Título: {title}
Keyword principal: {focus_keyword}
Extracto del contenido: {clean_snippet}

Devuelve SOLO este JSON (sin texto extra ni bloques markdown):
{{"keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5", "keyword6", "keyword7"]}}"""

    try:
        raw   = gemini.call_raw(prompt)
        clean = _re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
        data  = json.loads(clean)
        kws   = [str(k).strip().lower() for k in data.get("keywords", []) if str(k).strip()]
        logger.info(f"[Taxonomy] Keywords SEO generadas: {kws}")
        return kws[:7]
    except Exception as exc:
        logger.warning(f"[Taxonomy] No se pudieron generar keywords SEO: {exc}")
        return []


# ---------------------------------------------------------------------------
# Función de conveniencia: orquesta todo el flujo
# ---------------------------------------------------------------------------

def assign_taxonomy(
    gemini: "GeminiClient",
    base_url: str,
    auth: HTTPBasicAuth,
    title: str,
    content: str,
    post_type: str,
    focus_keyword: str = "",
) -> tuple[list[int], list[int], list[str]]:
    """
    Flujo completo: fetch → classify → generate SEO keywords → resolve → return.

    Args:
        gemini:        Instancia de GeminiClient.
        base_url:      URL base de WordPress (ej. "https://miblog.com").
        auth:          HTTPBasicAuth con credenciales de WordPress.
        title:         Título del post.
        content:       Contenido HTML del post.
        post_type:     Tipo de post.
        focus_keyword: Keyword principal del post (mejora la generación de keywords).

    Returns:
        Tupla (category_ids, tag_ids, keyword_strings) donde:
          - category_ids:    exactamente 1 elemento (o [] si falla)
          - tag_ids:         IDs de etiquetas (taxonomía + keywords SEO)
          - keyword_strings: strings de keywords SEO generadas (para AIOSEO, etc.)
    """
    categories    = fetch_categories(base_url, auth)
    existing_tags = fetch_tags(base_url, auth)

    classification = classify_post(
        gemini          = gemini,
        title           = title,
        content_snippet = content,
        post_type       = post_type,
        categories      = categories,
    )

    cat_id       = classification.get("category_id")
    category_ids = [cat_id] if cat_id else []

    # Tags de taxonomía (organización del blog)
    taxonomy_tag_names = classification.get("tags", [])

    # Palabras clave SEO generadas por IA (búsquedas reales de Google)
    keyword_strings = generate_seo_keywords(gemini, title, content, focus_keyword)

    # Unificar ambas listas (sin duplicados) y resolver en un solo pase
    seen: set[str] = {t.lower() for t in taxonomy_tag_names}
    combined_names  = taxonomy_tag_names + [k for k in keyword_strings if k.lower() not in seen]
    tag_ids = resolve_tags(base_url, auth, combined_names, existing_tags) if combined_names else []

    return category_ids, tag_ids, keyword_strings
