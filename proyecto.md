# 🤖 Sistema de Generación de Contenido para WordPress con Gemini API
### Arquitectura Human-in-the-Loop · AdSense & Amazon Affiliates

---

## 📋 Índice

1. [Visión General del Proyecto](#1-visión-general-del-proyecto)
2. [Arquitectura del Sistema](#2-arquitectura-del-sistema)
3. [Librerías y Dependencias Python](#3-librerías-y-dependencias-python)
4. [Estructura del Código](#4-estructura-del-código)
5. [Lógica de los 3 Prompts (Modelo 1→3)](#5-lógica-de-los-3-prompts-modelo-13)
6. [Interfaz Visual (GUI con Streamlit)](#6-interfaz-visual-gui-con-streamlit)
7. [Integración con WordPress REST API](#7-integración-con-wordpress-rest-api)
8. [Configuración de Campos Personalizados para Afiliados](#8-configuración-de-campos-personalizados-para-afiliados)
9. [Flujo de Trabajo Completo](#9-flujo-de-trabajo-completo)
10. [Variables de Entorno y Configuración](#10-variables-de-entorno-y-configuración)
11. [Requisitos Previos y Setup Inicial](#11-requisitos-previos-y-setup-inicial)

---

## 1. Visión General del Proyecto

### Objetivo
Construir un pipeline semi-automatizado de creación de contenido SEO para un blog WordPress monetizado, donde un solo input del usuario genera **3 borradores distintos** listos para revisión humana antes de publicarse.

### Principios de Diseño
| Principio | Descripción |
|---|---|
| **Human-in-the-Loop** | Ningún post se publica automáticamente. Todo pasa por revisión manual. |
| **Eficiencia de tokens** | Los prompts se diseñan para ser concisos y reutilizar contexto base. |
| **Separación de responsabilidades** | Módulo de IA, módulo WordPress y GUI son componentes independientes. |
| **Trazabilidad** | Cada borrador generado queda registrado en un log local. |

---

## 2. Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────┐
│                    INTERFAZ (Streamlit GUI)              │
│  Input: Tópico libre  ──OR──  URL de Amazon             │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│               ORQUESTADOR (orchestrator.py)             │
│  · Parsea el input                                      │
│  · Selecciona plantillas de prompt                      │
│  · Llama a Gemini API (x3 en paralelo o secuencial)     │
│  · Recibe y estructura los 3 borradores                 │
└──────────┬────────────────────────────────┬─────────────┘
           │                                │
           ▼                                ▼
┌──────────────────────┐       ┌────────────────────────────┐
│  MÓDULO GEMINI AI    │       │   MÓDULO WORDPRESS API     │
│  (gemini_client.py)  │       │   (wp_client.py)           │
│  · Gestión de tokens │       │   · Autenticación JWT/AppPw│
│  · Retry logic       │       │   · Crear post como draft  │
│  · Prompt templates  │       │   · Asignar custom fields  │
└──────────────────────┘       └────────────────────────────┘
           │                                │
           ▼                                ▼
┌─────────────────────────────────────────────────────────┐
│                   WORDPRESS (Backend)                   │
│         3 Posts en estado DRAFT para revisión           │
│    Post A: Comparativa  │  Post B: Guía  │  Post C: SEO │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Librerías y Dependencias Python

### `requirements.txt`

```txt
# ── IA / Gemini ──────────────────────────────────────────
google-generativeai>=0.5.0       # SDK oficial de Gemini API

# ── WordPress Integration ────────────────────────────────
requests>=2.31.0                 # Llamadas a WordPress REST API
python-wordpress-xmlrpc>=2.3     # Alternativa XML-RPC (opcional/legacy)

# ── Interfaz Gráfica ─────────────────────────────────────
streamlit>=1.33.0                # GUI web local

# ── Scraping de Amazon (para parsear URLs) ───────────────
beautifulsoup4>=4.12.0           # Parsear HTML de páginas de producto
httpx>=0.27.0                    # Cliente HTTP asíncrono (alternativa a requests)

# ── Utilidades ───────────────────────────────────────────
python-dotenv>=1.0.0             # Cargar variables de entorno desde .env
pydantic>=2.0.0                  # Validación de datos y modelos
tenacity>=8.2.0                  # Retry automático para llamadas a API
loguru>=0.7.0                    # Logging estructurado
tiktoken>=0.7.0                  # Estimación de tokens (compatible con conteos)
```

### Notas sobre la elección de librerías

> **¿`requests` o `python-wordpress-xmlrpc`?**
> Se recomienda **`requests` + WordPress REST API** sobre XML-RPC porque:
> - XML-RPC está deshabilitado por defecto en WordPress modernos por seguridad.
> - La REST API (`/wp-json/wp/v2/`) es el estándar actual, soporta Application Passwords y JWT.
> - Mayor control sobre custom fields y metadatos.

---

## 4. Estructura del Código

```
proyecto/
│
├── .env                          # Credenciales (NO subir a git)
├── .gitignore
├── requirements.txt
│
├── app.py                        # Entry point de Streamlit (GUI)
│
├── core/
│   ├── __init__.py
│   ├── orchestrator.py           # Lógica central: coordina IA + WP
│   ├── gemini_client.py          # Wrapper de Gemini API
│   ├── wp_client.py              # Wrapper de WordPress REST API
│   ├── prompt_templates.py       # Las 3 plantillas de prompt
│   └── amazon_parser.py          # Extrae datos de URLs de Amazon
│
├── models/
│   ├── __init__.py
│   └── post_draft.py             # Modelo Pydantic para un borrador
│
├── logs/
│   └── generation_log.jsonl      # Registro de cada generación
│
└── tests/
    ├── test_gemini_client.py
    └── test_wp_client.py
```

---

## 5. Lógica de los 3 Prompts (Modelo 1→3)

### Modelo de datos: `models/post_draft.py`

```python
from pydantic import BaseModel
from enum import Enum

class PostType(str, Enum):
    COMPARATIVA = "comparativa"
    GUIA        = "guia"
    RESENA_SEO  = "resena_seo"

class PostDraft(BaseModel):
    post_type:    PostType
    title:        str
    content:      str          # HTML o Markdown
    meta_description: str      # ≤ 160 caracteres para SEO
    focus_keyword: str
    affiliate_url: str | None  # URL de afiliado de Amazon
    wp_post_id:   int | None   # ID asignado por WordPress tras subir
```

---

### Plantillas de Prompt: `core/prompt_templates.py`

```python
# ────────────────────────────────────────────────────────────────────────────
# CONTEXTO BASE (se inyecta en los 3 prompts para ahorrar tokens repetidos)
# ────────────────────────────────────────────────────────────────────────────
BASE_CONTEXT = """
Eres un redactor SEO experto en blogs de tecnología y productos Amazon.
Escribe siempre en español. Usa un tono cercano pero profesional.
El contenido debe estar estructurado con H2 y H3.
Incluye naturalmente la keyword principal al menos 3 veces.
El output debe ser HTML limpio, listo para pegar en WordPress.
"""

# ────────────────────────────────────────────────────────────────────────────
# POST A — Comparativa del producto con un competidor
# ────────────────────────────────────────────────────────────────────────────
PROMPT_COMPARATIVA = BASE_CONTEXT + """
TAREA: Escribe un artículo comparativo sobre "{topic}".
- Compara "{topic}" con su principal competidor del mercado.
- Usa una tabla HTML de comparación con al menos 6 criterios (precio, rendimiento, diseño, garantía, etc.).
- Concluye con una recomendación clara indicando para qué perfil de usuario es cada opción.
- Incluye un CTA (call-to-action) con el texto "Ver precio en Amazon" apuntando a: {affiliate_url}
- Longitud objetivo: 1200-1500 palabras.
- Devuelve también: título SEO (≤60 chars), meta description (≤160 chars) y focus keyword.

Formato de respuesta (JSON):
{{
  "title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content": "...HTML..."
}}
"""

# ────────────────────────────────────────────────────────────────────────────
# POST B — Guía de beneficios y casos de uso
# ────────────────────────────────────────────────────────────────────────────
PROMPT_GUIA = BASE_CONTEXT + """
TAREA: Escribe una guía práctica sobre "{topic}".
- Explica los 5 principales beneficios del producto.
- Describe al menos 3 casos de uso reales con ejemplos concretos.
- Añade una sección "¿Para quién es ideal?" con bullets.
- Incluye un CTA con el texto "Consíguelo ahora en Amazon" apuntando a: {affiliate_url}
- Longitud objetivo: 1000-1300 palabras.
- Devuelve también: título SEO (≤60 chars), meta description (≤160 chars) y focus keyword.

Formato de respuesta (JSON):
{{
  "title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content": "...HTML..."
}}
"""

# ────────────────────────────────────────────────────────────────────────────
# POST C — Reseña SEO optimizada con CTA de afiliados
# ────────────────────────────────────────────────────────────────────────────
PROMPT_RESENA_SEO = BASE_CONTEXT + """
TAREA: Escribe una reseña completa y optimizada para SEO sobre "{topic}".
- Estructura: Introducción → Características principales → Pros y Contras (lista HTML) → Veredicto final.
- Optimiza el contenido para la intención de búsqueda "mejor {topic}" y "{topic} opiniones".
- Añade schema markup de tipo Review en JSON-LD al final del contenido.
- Incluye mínimo 2 CTAs con texto "Comprar en Amazon con descuento" apuntando a: {affiliate_url}
- Longitud objetivo: 1400-1800 palabras.
- Devuelve también: título SEO (≤60 chars), meta description (≤160 chars) y focus keyword.

Formato de respuesta (JSON):
{{
  "title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content": "...HTML..."
}}
"""

PROMPT_MAP = {
    "comparativa": PROMPT_COMPARATIVA,
    "guia":        PROMPT_GUIA,
    "resena_seo":  PROMPT_RESENA_SEO,
}
```

---

### Cliente Gemini: `core/gemini_client.py`

```python
import google.generativeai as genai
import json
from tenacity import retry, stop_after_attempt, wait_exponential
from loguru import logger
from core.prompt_templates import PROMPT_MAP

class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        genai.configure(api_key=api_key)
        # gemini-1.5-flash: equilibrio óptimo velocidad/costo/tokens
        self.model = genai.GenerativeModel(
            model_name=model,
            generation_config={
                "temperature": 0.7,       # Creatividad moderada
                "max_output_tokens": 4096, # Suficiente para 1500 palabras HTML
                "response_mime_type": "application/json",  # Fuerza respuesta JSON
            }
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def generate_draft(self, post_type: str, topic: str, affiliate_url: str) -> dict:
        """Genera un borrador. Reintentos automáticos ante fallos de red."""
        prompt_template = PROMPT_MAP[post_type]
        prompt = prompt_template.format(topic=topic, affiliate_url=affiliate_url or "#")

        logger.info(f"Generando post tipo '{post_type}' para tópico: '{topic}'")
        response = self.model.generate_content(prompt)

        # Parsear la respuesta JSON devuelta por el modelo
        draft_data = json.loads(response.text)
        draft_data["post_type"] = post_type
        draft_data["affiliate_url"] = affiliate_url

        logger.success(f"Draft '{post_type}' generado. Título: {draft_data.get('title')}")
        return draft_data
```

---

### Orquestador: `core/orchestrator.py`

```python
from core.gemini_client import GeminiClient
from core.wp_client import WordPressClient
from core.amazon_parser import extract_product_name
from models.post_draft import PostDraft, PostType
from loguru import logger
import json
from pathlib import Path

class ContentOrchestrator:
    def __init__(self, gemini_client: GeminiClient, wp_client: WordPressClient):
        self.gemini  = gemini_client
        self.wp      = wp_client
        self.log_path = Path("logs/generation_log.jsonl")

    def run(self, user_input: str, is_amazon_url: bool = False) -> list[PostDraft]:
        """
        Recibe 1 input → genera 3 borradores → los sube a WP como drafts.
        Retorna la lista de PostDraft con los IDs de WordPress asignados.
        """
        # 1. Determinar tópico y URL de afiliado
        if is_amazon_url:
            topic        = extract_product_name(user_input)  # scraping del título
            affiliate_url = user_input
        else:
            topic        = user_input
            affiliate_url = None

        drafts = []

        # 2. Generar los 3 borradores llamando a Gemini
        for post_type in ["comparativa", "guia", "resena_seo"]:
            raw = self.gemini.generate_draft(post_type, topic, affiliate_url)

            draft = PostDraft(
                post_type      = PostType(post_type),
                title          = raw["title"],
                content        = raw["content"],
                meta_description = raw["meta_description"],
                focus_keyword  = raw["focus_keyword"],
                affiliate_url  = affiliate_url,
                wp_post_id     = None,
            )

            # 3. Subir a WordPress como DRAFT
            wp_id = self.wp.create_draft(draft)
            draft.wp_post_id = wp_id
            drafts.append(draft)

            # 4. Log local
            self._log(draft)

        logger.success(f"✅ 3 borradores creados en WordPress para: '{topic}'")
        return drafts

    def _log(self, draft: PostDraft):
        self.log_path.parent.mkdir(exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(draft.model_dump_json() + "\n")
```

---

## 6. Interfaz Visual (GUI con Streamlit)

### `app.py`

```python
import streamlit as st
from dotenv import load_dotenv
import os
from core.gemini_client import GeminiClient
from core.wp_client import WordPressClient
from core.orchestrator import ContentOrchestrator

load_dotenv()

# ── Configuración de página ──────────────────────────────
st.set_page_config(
    page_title="🤖 Blog Content Generator",
    page_icon="✍️",
    layout="centered"
)

st.title("✍️ Generador de Contenido para WordPress")
st.caption("Genera 3 borradores SEO con un solo input · Human-in-the-Loop")

# ── Formulario de entrada ────────────────────────────────
with st.form("input_form"):
    user_input = st.text_input(
        label="Tópico o URL de Amazon",
        placeholder="Ej: auriculares inalámbricos Sony WH-1000XM5  ó  https://amazon.es/dp/XXXXXXXXX"
    )
    is_url = st.checkbox("Es una URL de Amazon (con link de afiliado)")
    submit = st.form_submit_button("🚀 Generar 3 Borradores")

# ── Procesamiento ────────────────────────────────────────
if submit and user_input:
    gemini_client = GeminiClient(api_key=os.getenv("GEMINI_API_KEY"))
    wp_client     = WordPressClient(
        base_url  = os.getenv("WP_BASE_URL"),
        username  = os.getenv("WP_USERNAME"),
        app_password = os.getenv("WP_APP_PASSWORD"),
    )
    orchestrator = ContentOrchestrator(gemini_client, wp_client)

    with st.spinner("Generando contenido con Gemini y subiendo a WordPress..."):
        drafts = orchestrator.run(user_input, is_amazon_url=is_url)

    st.success("✅ ¡3 borradores creados exitosamente en WordPress!")

    # ── Mostrar resumen de resultados ────────────────────
    for draft in drafts:
        type_labels = {
            "comparativa": "📊 Post A – Comparativa",
            "guia":        "📖 Post B – Guía de Beneficios",
            "resena_seo":  "🔍 Post C – Reseña SEO",
        }
        with st.expander(f"{type_labels[draft.post_type]} · WP ID: {draft.wp_post_id}"):
            st.markdown(f"**Título:** {draft.title}")
            st.markdown(f"**Meta Description:** {draft.meta_description}")
            st.markdown(f"**Focus Keyword:** `{draft.focus_keyword}`")
            wp_url = f"{os.getenv('WP_BASE_URL')}/wp-admin/post.php?post={draft.wp_post_id}&action=edit"
            st.link_button("📝 Editar en WordPress", wp_url)
```

---

## 7. Integración con WordPress REST API

### `core/wp_client.py`

```python
import requests
from requests.auth import HTTPBasicAuth
from models.post_draft import PostDraft
from loguru import logger

class WordPressClient:
    def __init__(self, base_url: str, username: str, app_password: str):
        # Usar Application Passwords (disponible desde WP 5.6)
        # Generar en: WP Admin → Usuarios → Tu perfil → Application Passwords
        self.base_url = base_url.rstrip("/")
        self.auth     = HTTPBasicAuth(username, app_password)
        self.headers  = {"Content-Type": "application/json"}

    def create_draft(self, draft: PostDraft) -> int:
        """Crea un post en estado 'draft' y retorna su ID de WordPress."""
        endpoint = f"{self.base_url}/wp-json/wp/v2/posts"

        payload = {
            "title":   draft.title,
            "content": draft.content,
            "status":  "draft",               # ← NUNCA se publica automáticamente
            "meta": {
                # Campos personalizados (requiere plugin o código en functions.php)
                "_yoast_wpseo_metadesc":       draft.meta_description,
                "_yoast_wpseo_focuskw":        draft.focus_keyword,
                "affiliate_url":               draft.affiliate_url or "",
                "post_type_label":             draft.post_type,
            }
        }

        response = requests.post(
            endpoint,
            json    = payload,
            auth    = self.auth,
            headers = self.headers,
            timeout = 30,
        )
        response.raise_for_status()

        wp_id = response.json()["id"]
        logger.info(f"Post creado en WP con ID {wp_id} (estado: draft)")
        return wp_id
```

> **Nota de seguridad:** Usar siempre **Application Passwords** de WordPress 5.6+ en lugar de la contraseña principal. XML-RPC debe estar desactivado.

---

## 8. Configuración de Campos Personalizados para Afiliados

### Opción A: Registrar campos en `functions.php` (sin plugin)

Añade este código al archivo `functions.php` de tu tema hijo en WordPress:

```php
<?php
// Registrar campos custom para posts generados por IA
function register_affiliate_meta_fields() {
    $fields = [
        'affiliate_url'    => 'URL del producto en Amazon (con tag de afiliado)',
        'post_type_label'  => 'Tipo de post IA: comparativa | guia | resena_seo',
        'focus_keyword'    => 'Keyword principal para SEO',
        'ai_generated'     => 'Flag: contenido generado por IA (1 = sí)',
    ];

    foreach ($fields as $key => $description) {
        register_post_meta('post', $key, [
            'show_in_rest'  => true,   // Exponer en la REST API ← IMPORTANTE
            'single'        => true,
            'type'          => 'string',
            'description'   => $description,
            'auth_callback' => function() {
                return current_user_can('edit_posts');
            },
        ]);
    }
}
add_action('init', 'register_affiliate_meta_fields');
```

> ⚠️ **`'show_in_rest' => true`** es obligatorio para que el campo sea accesible y escribible desde la REST API de Python.

---

### Opción B: Con el plugin Advanced Custom Fields (ACF)

1. Instalar el plugin **Advanced Custom Fields** desde el repositorio de WordPress.
2. Ir a **ACF → Field Groups → Add New**.
3. Crear un grupo llamado `"Datos de Afiliado"` con los siguientes campos:

| Nombre del campo | Tipo | Slug del campo |
|---|---|---|
| URL de Afiliado Amazon | URL | `affiliate_url` |
| Tipo de Post IA | Select (`comparativa`, `guia`, `resena_seo`) | `post_type_label` |
| Keyword Principal | Text | `focus_keyword` |
| Generado por IA | True/False | `ai_generated` |

4. Asignar el grupo de campos a: **Post Type → Posts**.
5. Con ACF PRO, los campos son accesibles desde la REST API automáticamente.
   Con ACF gratuito, añadir en `functions.php`:

```php
add_filter('acf/rest_api/post/get_fields', '__return_true');
```

---

### Cómo usar `affiliate_url` en una plantilla de WordPress

```php
<?php
// En single.php o en un bloque personalizado
$affiliate_url = get_post_meta(get_the_ID(), 'affiliate_url', true);
if ($affiliate_url) : ?>
    <div class="affiliate-cta">
        <a href="<?php echo esc_url($affiliate_url); ?>"
           target="_blank"
           rel="nofollow sponsored"
           class="btn-amazon">
            🛒 Ver precio en Amazon
        </a>
    </div>
<?php endif; ?>
```

> **Atributo `rel="nofollow sponsored"`**: requerido por las directrices de Google para links de afiliado. Evita penalizaciones SEO.

---

## 9. Flujo de Trabajo Completo

```
Usuario ingresa tópico/URL
         │
         ▼
    [Streamlit GUI]
         │
         ▼
  ContentOrchestrator
         │
    ┌────┴────┐
    │  Gemini │  → Prompt A (Comparativa)  → Draft JSON A
    │   API   │  → Prompt B (Guía)         → Draft JSON B
    └─────────┘  → Prompt C (Reseña SEO)  → Draft JSON C
         │
         ▼
  WordPressClient
    · POST /wp-json/wp/v2/posts  (status: draft) × 3
    · Asigna meta fields (affiliate_url, keyword, etc.)
         │
         ▼
  WordPress Admin
    · 3 posts en estado BORRADOR
    · Revisar contenido, imágenes, formato
    · Ajustar CTAs y links de afiliado si es necesario
    · Cambiar estado a PUBLICADO manualmente
         │
         ▼
  Blog publicado ✅ (con links de afiliado + SEO optimizado)
```

---

## 10. Variables de Entorno y Configuración

### Archivo `.env`

```dotenv
# ── Gemini AI ───────────────────────────────────────────
GEMINI_API_KEY=AIzaSy...tu_clave_aqui

# ── WordPress ───────────────────────────────────────────
WP_BASE_URL=https://tu-blog.com
WP_USERNAME=tu_usuario_wordpress
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
# Application Password: WP Admin → Usuarios → Perfil → Application Passwords → Add New

# ── Amazon Affiliates ───────────────────────────────────
AMAZON_AFFILIATE_TAG=tu-tag-20
```

### `.gitignore`

```gitignore
.env
__pycache__/
*.pyc
logs/
.streamlit/secrets.toml
```

---

## 11. Requisitos Previos y Setup Inicial

### Paso 1 — Crear entorno virtual e instalar dependencias

```cmd
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Paso 2 — Obtener clave de Gemini API

1. Ir a [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Crear proyecto y generar API Key.
3. El modelo recomendado es **`gemini-1.5-flash`** por su balance costo/velocidad/contexto (1M tokens).

### Paso 3 — Configurar Application Password en WordPress

1. `WordPress Admin → Usuarios → Tu Perfil`
2. Scroll hasta la sección **"Application Passwords"**
3. Nombre: `"Content Generator Bot"` → clic en **"Add New Application Password"**
4. Copiar la contraseña generada (formato: `xxxx xxxx xxxx xxxx xxxx xxxx`) al `.env`

### Paso 4 — Registrar meta fields en WordPress

Copiar el código PHP de la [Sección 8](#8-configuración-de-campos-personalizados-para-afiliados) en el `functions.php` de tu tema hijo.

### Paso 5 — Ejecutar la aplicación

```cmd
streamlit run app.py
```

La GUI estará disponible en `http://localhost:8501`

---

## ✅ Checklist de Implementación

- [ ] Crear entorno virtual y ejecutar `pip install -r requirements.txt`
- [ ] Configurar archivo `.env` con todas las credenciales
- [ ] Registrar Application Password en WordPress
- [ ] Añadir meta fields en `functions.php` (con `show_in_rest: true`)
- [ ] Verificar que la REST API de WordPress está activa: `GET /wp-json/wp/v2/posts`
- [ ] Probar llamada básica a Gemini API con `gemini_client.py`
- [ ] Probar creación de draft en WordPress con `wp_client.py`
- [ ] Ejecutar flujo completo desde Streamlit
- [ ] Revisar y publicar borradores manualmente desde WordPress Admin
- [ ] Verificar atributos `rel="nofollow sponsored"` en links de afiliado

---

*Documento generado el 23 de marzo de 2026 · Arquitectura Human-in-the-Loop v1.0*
