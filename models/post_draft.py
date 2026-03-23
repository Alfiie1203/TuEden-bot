from __future__ import annotations
from pydantic import BaseModel, field_validator
from enum import Enum
from datetime import datetime
from typing import Optional, List, Dict, Any


class PostType(str, Enum):
    # Modo Amazon (producto específico)
    COMPARATIVA = "comparativa"
    GUIA        = "guia"
    RESENA_SEO  = "resena_seo"
    # Modo tópico libre (sin producto/afiliado)
    OPINION     = "opinion"      # Artículo de opinión / columna compartible
    LISTICLE    = "listicle"     # Lista (Top 10, Mejores X...)
    HOWTO       = "howto"        # Guía paso a paso / tutorial


# Etiquetas legibles para mostrar en la GUI
POST_TYPE_LABELS: dict[str, str] = {
    PostType.COMPARATIVA: "📊 Post A – Comparativa con Competidor",
    PostType.GUIA:        "📖 Post B – Guía de Beneficios y Casos de Uso",
    PostType.RESENA_SEO:  "🔍 Post C – Reseña SEO con CTA de Afiliados",
    PostType.OPINION:     "💬 Post A – Artículo de Opinión",
    PostType.LISTICLE:    "📋 Post B – Listicle / Top N",
    PostType.HOWTO:       "🛠️ Post C – Guía Paso a Paso",
}


class PostDraft(BaseModel):
    post_type:        PostType
    title:            str
    content:          str           # HTML listo para WordPress
    meta_description: str           # ≤ 160 caracteres
    focus_keyword:    str
    affiliate_url:    Optional[str] = None   # URL de afiliado de Amazon
    wp_post_id:       Optional[int] = None   # ID asignado por WordPress
    draft_file:       Optional[str] = None   # Nombre del archivo JSON local (modo simulado)
    created_at:       str = ""               # Timestamp ISO
    images:           List[Dict[str, Any]] = []  # [{src, alt, caption, position_label, marker}]

    def model_post_init(self, __context) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @field_validator("meta_description")
    @classmethod
    def meta_description_length(cls, v: str) -> str:
        if len(v) > 160:
            return v[:157] + "..."
        return v

    @property
    def label(self) -> str:
        return POST_TYPE_LABELS.get(self.post_type, self.post_type)
