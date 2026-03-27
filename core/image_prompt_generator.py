"""
image_prompt_generator.py
=========================
Genera 4 prompts de imagen de alta calidad para cada post del blog:
  - 1 portada  (hero image 16:9, muy visual y llamativa)
  - 3 imágenes de contenido (intro / desarrollo / cierre)

Los prompts están en inglés y son listos para pegar en cualquier generador
de imágenes IA: Gemini (chat), Midjourney, DALL-E, Stable Diffusion, etc.

El objetivo es que cada prompt describa con precisión una imagen que
tenga sentido total con el post al que pertenece.
"""
from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.gemini_client import GeminiClient


# ---------------------------------------------------------------------------
# Helper: strip HTML
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    class _S(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []
        def handle_data(self, data: str):
            self.parts.append(data)
    p = _S()
    p.feed(html)
    return " ".join(x.strip() for x in p.parts if x.strip())


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

PROMPT_IMAGE_GENERATION = """\
You are a professional art director for a medical and psychology blog.

Based on the following blog post, generate exactly 4 high-quality image prompts \
in English suitable for AI image generators (Gemini, Midjourney, DALL-E, Stable Diffusion).

Blog post title: {title}
Post type: {post_type}
Content excerpt: {excerpt}
Blog context: health, medicine, and psychology blog — trustworthy, informative, professional.

Generate exactly 4 prompts:
1. PORTADA — The hero/cover image. 16:9 format. Must visually represent the core topic \
   in a compelling, eye-catching way that makes someone want to read the article.
2. IMG1 — For the introduction section. Sets the emotional tone of the article.
3. IMG2 — For the main content section. Visually explains the key concept or data.
4. IMG3 — For the closing/conclusion. Leaves the reader with an inspiring, actionable feeling.

Style rules for ALL prompts:
- Write in English, 60–90 words per prompt.
- Include: main subject, setting/background, mood, lighting, color palette, art style.
- Style: professional medical/psychology illustration, photorealistic or semi-realistic, \
  clean modern aesthetic, warm trustworthy tones (blues, teals, soft whites, warm neutrals).
- For medicine topics: healthcare professionals, anatomy concepts, lab environments, \
  nature-healing metaphors, scientific visuals.
- For psychology topics: mind metaphors, human emotions, brain and neural concepts, \
  interpersonal connections, light and shadow symbolism, vibrant inner landscapes.
- NO text, watermarks, logos, or numbers in the image.
- NO disturbing or graphic medical imagery.
- Quality: 4K, ultra-detailed, award-winning photography or illustration.

Respond ONLY with this valid JSON object (no extra text, no markdown blocks):
{{
  "portada": "...",
  "img1": "...",
  "img2": "...",
  "img3": "..."
}}
"""


# ---------------------------------------------------------------------------
# Mock responses
# ---------------------------------------------------------------------------

_MOCK_BASE = {
    "portada": (
        "A serene, professional medical consultation scene: a compassionate doctor "
        "in a white coat sitting face-to-face with a patient in a warm, softly lit "
        "modern office. Anatomical charts on the wall, natural light through large "
        "windows, muted blue and white tones, photorealistic, 4K, wide angle. "
        "No text or logos. Topic: {topic}"
    ),
    "img1": (
        "Close-up of two hands gently clasped together on a wooden desk, symbolizing "
        "empathy and healthcare. Warm golden-hour light, shallow depth of field, "
        "soft cream and warm tones, professional photography style, ultra-detailed. "
        "Medical and psychological wellness theme. Topic: {topic}"
    ),
    "img2": (
        "Glowing human brain with active neural connections rendered as electric-blue "
        "light filaments against a deep navy background. Neuroscience visualization, "
        "futuristic yet clean, no labels or text, 4K digital art, cinematic lighting. "
        "Represents knowledge and medical research. Topic: {topic}"
    ),
    "img3": (
        "A person standing at the edge of a sunlit cliff overlooking a vast green "
        "valley, arms slightly raised, facing the horizon — representing healing, "
        "recovery and hope. Golden hour, wide landscape shot, warm amber and green "
        "tones, professional nature photography, 4K resolution. Topic: {topic}"
    ),
}


def _make_mock(title: str) -> dict:
    short = title[:50]
    time.sleep(0.3)
    return {k: v.format(topic=short) for k, v in _MOCK_BASE.items()}


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def generate_image_prompts(
    gemini_client: "GeminiClient",
    title: str,
    content: str,
    post_type: str = "",
    reviewer: str = "",
) -> dict:
    """
    Genera 4 prompts de imagen para un post.

    Returns:
        dict con claves: portada, img1, img2, img3
        (cada valor es un prompt en inglés listo para usar en Gemini/Midjourney/DALL-E)
    """
    if gemini_client.mock_mode:
        return _make_mock(title)

    excerpt = _strip_html(content)[:600].strip()
    prompt  = PROMPT_IMAGE_GENERATION.format(
        title     = title,
        post_type = post_type or "blog article",
        excerpt   = excerpt,
    )

    try:
        raw = gemini_client.call_raw(prompt)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                data = json.loads(m.group())
            else:
                raise ValueError(f"JSON no parseable: {raw[:200]}")

        for key in ("portada", "img1", "img2", "img3"):
            data.setdefault(key, f"Professional medical illustration related to: {title[:60]}")
        return data

    except Exception as exc:
        from loguru import logger
        logger.warning(f"[ImagePrompts] Fallback mock para '{title[:50]}': {exc}")
        return _make_mock(title)
