"""
orchestrator.py
Cerebro del sistema: coordina el flujo completo 1 input → 3 borradores → WordPress/simulado.

Responsabilidades:
  1. Determinar si el input es una URL de Amazon o un tópico libre.
  2. Llamar a Gemini para generar los 3 tipos de post.
  3. Subir cada borrador a WordPress (real o simulado).
  4. Guardar un log JSONL de cada sesión de generación.
  5. Devolver la lista de PostDraft con sus IDs asignados.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from loguru import logger

from core.amazon_parser import extract_product_name, is_amazon_url
from core.gemini_client import GeminiClient
from core.wp_client import WordPressClient
from models.post_draft import PostDraft, PostType


# Orden y etiquetas según el modo de generación
POST_TYPES_AMAZON: list[dict] = [
    {"key": "comparativa", "label": "Post A – Comparativa"},
    {"key": "guia",        "label": "Post B – Guía de Beneficios"},
    {"key": "resena_seo",  "label": "Post C – Reseña SEO"},
]

POST_TYPES_LIBRE: list[dict] = [
    {"key": "opinion",  "label": "Post A – Artículo de Opinión"},
    {"key": "listicle",  "label": "Post B – Listicle / Top N"},
    {"key": "howto",     "label": "Post C – Guía Paso a Paso"},
]

# Alias para retrocompatibilidad
POST_TYPES = POST_TYPES_AMAZON


class ContentOrchestrator:
    """
    Coordina la generación de 3 borradores a partir de un solo input.

    Args:
        gemini_client: Instancia configurada de GeminiClient.
        wp_client:     Instancia configurada de WordPressClient (simulado o real).
        log_dir:       Carpeta donde se guarda el log JSONL de generaciones.
        progress_cb:   Callback opcional para reportar progreso a la GUI.
                       Firma: progress_cb(step: int, total: int, message: str)
    """

    def __init__(
        self,
        gemini_client: GeminiClient,
        wp_client: WordPressClient,
        log_dir: str = "logs",
        progress_cb: Callable[[int, int, str], None] | None = None,
    ):
        self.gemini      = gemini_client
        self.wp          = wp_client
        self.log_path    = Path(log_dir) / "generation_log.jsonl"
        self.progress_cb = progress_cb or (lambda *_: None)

        # Asegurar que el directorio de logs existe
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Método principal
    # ------------------------------------------------------------------

    def run(
        self,
        user_input: str,
        mode: str = "auto",
        focus: str = "",
        reviewer: str = "",
    ) -> list[PostDraft]:
        """
        Flujo completo: recibe 1 input → genera 3 borradores → los sube a WP.

        Args:
            user_input: Tópico libre ("medicina moderna") o URL de Amazon.
            mode:       "auto"   → detecta automáticamente según el input.
                        "amazon" → fuerza modo Amazon (comparativa/guía/reseña).
                        "libre"  → fuerza modo tópico libre (opinion/listicle/howto).
            focus:      Enfoque específico del artículo (ángulo, perspectiva, audiencia).
            reviewer:   Persona que revisará el contenido: "Médico", "Psicólogo", "Editor" o "".

        Returns:
            Lista de 3 PostDraft con wp_post_id relleno.
        """
        from core.prompt_templates import PROMPT_MAP, PROMPT_MAP_LIBRE

        # 1. Resolver tópico y URL de afiliado
        topic, affiliate_url = self._resolve_input(user_input)

        # 2. Elegir el set de post_types y prompt_map según el modo
        if mode == "libre" or (mode == "auto" and not affiliate_url):
            post_types = POST_TYPES_LIBRE
            prompt_map = PROMPT_MAP_LIBRE
            affiliate_url = None   # nunca afiliado en modo libre
        else:
            post_types = POST_TYPES_AMAZON
            prompt_map = PROMPT_MAP

        logger.info(
            f"🚀 Iniciando generación [{mode.upper()}] para: «{topic}»"
            + (f" | URL afiliado: {affiliate_url}" if affiliate_url else "")
            + (f" | Enfoque: {focus[:60]}" if focus else "")
            + (f" | Revisor: {reviewer}" if reviewer else "")
        )

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        drafts: list[PostDraft] = []
        total = len(post_types)

        # 3. Generar y subir los 3 borradores en secuencia
        for step, post_info in enumerate(post_types, start=1):
            post_type = post_info["key"]
            label     = post_info["label"]

            self.progress_cb(step, total, f"Generando {label}…")
            logger.info(f"[{step}/{total}] Generando {label}…")

            try:
                # Llamada a Gemini (pasamos el prompt_map y los nuevos parámetros)
                raw = self.gemini.generate_draft(
                    post_type, topic, affiliate_url,
                    prompt_map = prompt_map,
                    focus      = focus,
                    reviewer   = reviewer,
                )

                draft = PostDraft(
                    post_type        = PostType(post_type),
                    title            = raw["title"],
                    content          = raw["content"],
                    meta_description = raw["meta_description"],
                    focus_keyword    = raw["focus_keyword"],
                    affiliate_url    = affiliate_url,
                    wp_post_id       = None,
                )

                # Subir a WordPress (simulado o real)
                self.progress_cb(step, total, f"Subiendo {label} a WordPress…")
                wp_id = self.wp.create_draft(draft)
                draft.wp_post_id = wp_id

                drafts.append(draft)
                self._log_draft(session_id, draft, focus=focus, reviewer=reviewer)

                logger.success(f"✅ {label} listo | WP ID: {wp_id}")

            except Exception as exc:
                logger.error(f"❌ Error generando {label}: {exc}")
                # Crear un draft de error para no interrumpir los demás
                error_draft = PostDraft(
                    post_type        = PostType(post_type),
                    title            = f"[ERROR] {label}",
                    content          = f"<p>Error durante la generación: {exc}</p>",
                    meta_description = "",
                    focus_keyword    = topic,
                    affiliate_url    = affiliate_url,
                    wp_post_id       = None,
                )
                drafts.append(error_draft)

        self.progress_cb(total, total, "¡Generación completada!")
        logger.success(f"🎉 Sesión {session_id} completada: {len(drafts)} borradores.")
        return drafts

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _resolve_input(self, user_input: str) -> tuple[str, str | None]:
        """
        Determina si el input es una URL de Amazon o un tópico libre.

        Returns:
            Tupla (topic: str, affiliate_url: str | None)
        """
        cleaned = user_input.strip()

        if is_amazon_url(cleaned):
            logger.info("[Orchestrator] Input detectado como URL de Amazon.")
            product_name = extract_product_name(cleaned)
            return product_name, cleaned
        else:
            logger.info("[Orchestrator] Input detectado como tópico libre.")
            return cleaned, None

    def _log_draft(self, session_id: str, draft: PostDraft, focus: str = "", reviewer: str = "") -> None:
        """Añade una entrada al log JSONL de generaciones."""
        entry = {
            "session_id":    session_id,
            "timestamp":     datetime.now().isoformat(),
            "wp_mode":       self.wp.mode,
            "post_type":     draft.post_type,
            "title":         draft.title,
            "focus_keyword": draft.focus_keyword,
            "affiliate_url": draft.affiliate_url or "",
            "wp_post_id":    draft.wp_post_id,
            "draft_file":    draft.draft_file or "",
            "tokens_used":   getattr(draft, "_tokens_used", 0),
            "focus":         focus,
            "reviewer":      reviewer,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Factory conveniente para construir desde variables de entorno
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, token_manager=None, gemini_model: str | None = None, **kwargs) -> "ContentOrchestrator":
        """
        Construye el orquestador completo leyendo la configuración del entorno.
        No requiere GEMINI_API_KEY si GEMINI_MOCK_MODE=true.

        Args:
            token_manager: instancia existente de TokenManager (para compartirlo
                           con la GUI y mantener el estado de tokens entre llamadas).
            gemini_model:  nombre del modelo Gemini a usar (ej. "gemini-2.5-flash").
                           Si no se indica, usa el valor por defecto del GeminiClient.
        """
        import os
        from dotenv import load_dotenv
        load_dotenv()

        gemini_kwargs = {"token_manager": token_manager}
        if gemini_model:
            gemini_kwargs["model"] = gemini_model

        gemini = GeminiClient(**gemini_kwargs)
        wp     = WordPressClient.from_env()

        return cls(gemini_client=gemini, wp_client=wp, **kwargs)
