# ðŸ¤– Sistema de GeneraciÃ³n de Contenido para WordPress con Gemini API
### Arquitectura Human-in-the-Loop Â· AdSense & Amazon Affiliates

---

## ðŸ“‹ Ãndice

1. [VisiÃ³n General del Proyecto](#1-visiÃ³n-general-del-proyecto)
2. [Arquitectura del Sistema](#2-arquitectura-del-sistema)
3. [LibrerÃ­as y Dependencias Python](#3-librerÃ­as-y-dependencias-python)
4. [Estructura del CÃ³digo](#4-estructura-del-cÃ³digo)
5. [LÃ³gica de los 3 Prompts (Modelo 1â†’3)](#5-lÃ³gica-de-los-3-prompts-modelo-13)
6. [Interfaz Visual (GUI con Flask)](#6-interfaz-visual-gui-con-flask)
7. [IntegraciÃ³n con WordPress REST API](#7-integraciÃ³n-con-wordpress-rest-api)
8. [ConfiguraciÃ³n de Campos Personalizados para Afiliados](#8-configuraciÃ³n-de-campos-personalizados-para-afiliados)
9. [Flujo de Trabajo Completo](#9-flujo-de-trabajo-completo)
10. [Variables de Entorno y ConfiguraciÃ³n](#10-variables-de-entorno-y-configuraciÃ³n)
11. [Requisitos Previos y Setup Inicial](#11-requisitos-previos-y-setup-inicial)

---

## 1. VisiÃ³n General del Proyecto

### Objetivo
Construir un pipeline semi-automatizado de creaciÃ³n de contenido SEO para un blog WordPress monetizado, donde un solo input del usuario genera **3 borradores distintos** listos para revisiÃ³n humana antes de publicarse.

### Principios de DiseÃ±o
| Principio | DescripciÃ³n |
|---|---|
| **Human-in-the-Loop** | NingÃºn post se publica automÃ¡ticamente. Todo pasa por revisiÃ³n manual. |
| **Eficiencia de tokens** | Los prompts se diseÃ±an para ser concisos y reutilizar contexto base. |
| **SeparaciÃ³n de responsabilidades** | MÃ³dulo de IA, mÃ³dulo WordPress y GUI son componentes independientes. |
| **Trazabilidad** | Cada borrador generado queda registrado en un log local. |

---

## 2. Arquitectura del Sistema

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚              INTERFAZ WEB (Flask + Bootstrap 5)               â”‚
â”‚  Rutas: / Â· /historial Â· /topicos Â· /borrador               â”‚
â”‚  API REST: /api/generar Â· /api/tokens Â· /api/borrador/*      â”‚
â”‚  SSE: progreso en tiempo real via EventSource               â”‚
â”‚  Input: TÃ³pico libre  â”€â”€ORâ”€â”€  URL de Amazon               â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                            â”‚
                            â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚               ORQUESTADOR (orchestrator.py)             â”‚
â”‚  Â· Parsea el input                                      â”‚
â”‚  Â· Selecciona plantillas de prompt                      â”‚
â”‚  Â· Llama a Gemini API (x3 en paralelo o secuencial)     â”‚
â”‚  Â· Recibe y estructura los 3 borradores                 â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
           â”‚                                â”‚
           â–¼                                â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”       â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚  MÃ“DULO GEMINI AI    â”‚       â”‚   MÃ“DULO WORDPRESS API     â”‚
â”‚  (gemini_client.py)  â”‚       â”‚   (wp_client.py)           â”‚
â”‚  Â· GestiÃ³n de tokens â”‚       â”‚   Â· AutenticaciÃ³n JWT/AppPwâ”‚
â”‚  Â· Retry logic       â”‚       â”‚   Â· Crear post como draft  â”‚
â”‚  Â· Prompt templates  â”‚       â”‚   Â· Asignar custom fields  â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜       â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
           â”‚                                â”‚
           â–¼                                â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                   WORDPRESS (Backend)                   â”‚
â”‚         3 Posts en estado DRAFT para revisiÃ³n           â”‚
â”‚    Post A: Comparativa  â”‚  Post B: GuÃ­a  â”‚  Post C: SEO â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

---

## 3. LibrerÃ­as y Dependencias Python

### `requirements.txt`

```txt
# â”€â”€ IA / Gemini â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
google-generativeai>=0.5.0       # SDK oficial de Gemini API

# â”€â”€ WordPress Integration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
requests>=2.31.0                 # Llamadas a WordPress REST API
python-wordpress-xmlrpc>=2.3     # Alternativa XML-RPC (opcional/legacy)

# â”€â”€ Interfaz GrÃ¡fica â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
flask>=3.0.0                     # Framework web (reemplaza Streamlit)

# â”€â”€ Scraping de Amazon (para parsear URLs) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
beautifulsoup4>=4.12.0           # Parsear HTML de pÃ¡ginas de producto
httpx>=0.27.0                    # Cliente HTTP asÃ­ncrono (alternativa a requests)

# â”€â”€ Utilidades â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
python-dotenv>=1.0.0             # Cargar variables de entorno desde .env
pydantic>=2.0.0                  # ValidaciÃ³n de datos y modelos
tenacity>=8.2.0                  # Retry automÃ¡tico para llamadas a API
loguru>=0.7.0                    # Logging estructurado
tiktoken>=0.7.0                  # EstimaciÃ³n de tokens (compatible con conteos)
```

### Notas sobre la elecciÃ³n de librerÃ­as

> **Â¿`requests` o `python-wordpress-xmlrpc`?**
> Se recomienda **`requests` + WordPress REST API** sobre XML-RPC porque:
> - XML-RPC estÃ¡ deshabilitado por defecto en WordPress modernos por seguridad.
> - La REST API (`/wp-json/wp/v2/`) es el estÃ¡ndar actual, soporta Application Passwords y JWT.
> - Mayor control sobre custom fields y metadatos.

---

## 4. Estructura del CÃ³digo

```
proyecto/
â”‚
â”œâ”€â”€ .env                          # Credenciales (NO subir a git)
â”œâ”€â”€ .gitignore
â”œâ”€â”€ requirements.txt
â”‚
â”œâ”€â”€ app.py                        # Entry point de Flask (servidor web + API REST)
â”‚
â”œâ”€â”€ core/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ orchestrator.py           # LÃ³gica central: coordina IA + WP
â”‚   â”œâ”€â”€ gemini_client.py          # Wrapper de Gemini API
â”‚   â”œâ”€â”€ wp_client.py              # Wrapper de WordPress REST API
â”‚   â”œâ”€â”€ prompt_templates.py       # Las 3 plantillas de prompt
â”‚   â””â”€â”€ amazon_parser.py          # Extrae datos de URLs de Amazon
â”‚
â”œâ”€â”€ models/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â””â”€â”€ post_draft.py             # Modelo Pydantic para un borrador
â”‚
â”œâ”€â”€ logs/
â”‚   â””â”€â”€ generation_log.jsonl      # Registro de cada generaciÃ³n
â”‚
â””â”€â”€ tests/
    â”œâ”€â”€ test_gemini_client.py
    â””â”€â”€ test_wp_client.py
```

---

## 5. LÃ³gica de los 3 Prompts (Modelo 1â†’3)

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
    meta_description: str      # â‰¤ 160 caracteres para SEO
    focus_keyword: str
    affiliate_url: str | None  # URL de afiliado de Amazon
    wp_post_id:   int | None   # ID asignado por WordPress tras subir
```

---

### Plantillas de Prompt: `core/prompt_templates.py`

```python
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CONTEXTO BASE (se inyecta en los 3 prompts para ahorrar tokens repetidos)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BASE_CONTEXT = """
Eres un redactor SEO experto en blogs de tecnologÃ­a y productos Amazon.
Escribe siempre en espaÃ±ol. Usa un tono cercano pero profesional.
El contenido debe estar estructurado con H2 y H3.
Incluye naturalmente la keyword principal al menos 3 veces.
El output debe ser HTML limpio, listo para pegar en WordPress.
"""

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# POST A â€” Comparativa del producto con un competidor
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
PROMPT_COMPARATIVA = BASE_CONTEXT + """
TAREA: Escribe un artÃ­culo comparativo sobre "{topic}".
- Compara "{topic}" con su principal competidor del mercado.
- Usa una tabla HTML de comparaciÃ³n con al menos 6 criterios (precio, rendimiento, diseÃ±o, garantÃ­a, etc.).
- Concluye con una recomendaciÃ³n clara indicando para quÃ© perfil de usuario es cada opciÃ³n.
- Incluye un CTA (call-to-action) con el texto "Ver precio en Amazon" apuntando a: {affiliate_url}
- Longitud objetivo: 1200-1500 palabras.
- Devuelve tambiÃ©n: tÃ­tulo SEO (â‰¤60 chars), meta description (â‰¤160 chars) y focus keyword.

Formato de respuesta (JSON):
{{
  "title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content": "...HTML..."
}}
"""

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# POST B â€” GuÃ­a de beneficios y casos de uso
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
PROMPT_GUIA = BASE_CONTEXT + """
TAREA: Escribe una guÃ­a prÃ¡ctica sobre "{topic}".
- Explica los 5 principales beneficios del producto.
- Describe al menos 3 casos de uso reales con ejemplos concretos.
- AÃ±ade una secciÃ³n "Â¿Para quiÃ©n es ideal?" con bullets.
- Incluye un CTA con el texto "ConsÃ­guelo ahora en Amazon" apuntando a: {affiliate_url}
- Longitud objetivo: 1000-1300 palabras.
- Devuelve tambiÃ©n: tÃ­tulo SEO (â‰¤60 chars), meta description (â‰¤160 chars) y focus keyword.

Formato de respuesta (JSON):
{{
  "title": "...",
  "meta_description": "...",
  "focus_keyword": "...",
  "content": "...HTML..."
}}
"""

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# POST C â€” ReseÃ±a SEO optimizada con CTA de afiliados
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
PROMPT_RESENA_SEO = BASE_CONTEXT + """
TAREA: Escribe una reseÃ±a completa y optimizada para SEO sobre "{topic}".
- Estructura: IntroducciÃ³n â†’ CaracterÃ­sticas principales â†’ Pros y Contras (lista HTML) â†’ Veredicto final.
- Optimiza el contenido para la intenciÃ³n de bÃºsqueda "mejor {topic}" y "{topic} opiniones".
- AÃ±ade schema markup de tipo Review en JSON-LD al final del contenido.
- Incluye mÃ­nimo 2 CTAs con texto "Comprar en Amazon con descuento" apuntando a: {affiliate_url}
- Longitud objetivo: 1400-1800 palabras.
- Devuelve tambiÃ©n: tÃ­tulo SEO (â‰¤60 chars), meta description (â‰¤160 chars) y focus keyword.

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
        # gemini-1.5-flash: equilibrio Ã³ptimo velocidad/costo/tokens
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
        """Genera un borrador. Reintentos automÃ¡ticos ante fallos de red."""
        prompt_template = PROMPT_MAP[post_type]
        prompt = prompt_template.format(topic=topic, affiliate_url=affiliate_url or "#")

        logger.info(f"Generando post tipo '{post_type}' para tÃ³pico: '{topic}'")
        response = self.model.generate_content(prompt)

        # Parsear la respuesta JSON devuelta por el modelo
        draft_data = json.loads(response.text)
        draft_data["post_type"] = post_type
        draft_data["affiliate_url"] = affiliate_url

        logger.success(f"Draft '{post_type}' generado. TÃ­tulo: {draft_data.get('title')}")
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
        Recibe 1 input â†’ genera 3 borradores â†’ los sube a WP como drafts.
        Retorna la lista de PostDraft con los IDs de WordPress asignados.
        """
        # 1. Determinar tÃ³pico y URL de afiliado
        if is_amazon_url:
            topic        = extract_product_name(user_input)  # scraping del tÃ­tulo
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

        logger.success(f"âœ… 3 borradores creados en WordPress para: '{topic}'")
        return drafts

    def _log(self, draft: PostDraft):
        self.log_path.parent.mkdir(exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(draft.model_dump_json() + "\n")
```

---

## 6. Interfaz Visual (GUI con Flask)

### Stack tecnolÃ³gico
- **Flask** â€” Framework web Python
- **Jinja2** â€” Motor de plantillas HTML
- **Bootstrap 5** â€” DiseÃ±o responsivo (CDN, sin dependencias npm)
- **JavaScript Vanilla + Fetch API** â€” Llamadas AJAX a la API REST
- **Server-Sent Events (SSE)** â€” Progreso en tiempo real durante la generaciÃ³n

### PÃ¡ginas disponibles

| Ruta | DescripciÃ³n |
|------|-------------|
| `GET /` | Formulario principal de generaciÃ³n |
| `GET /historial` | Historial de generaciones con filtros |
| `GET /topicos` | Descubrimiento de tÃ³picos del dÃ­a |
| `GET /borrador?file=<nombre>` | Editor WYSIWYG de borradores |

### API REST

| MÃ©todo | Ruta | FunciÃ³n |
|--------|------|----------|
| `GET`  | `/api/tokens` | Estado del pool de API Keys |
| `POST` | `/api/generar` | Inicia generaciÃ³n (devuelve `task_id`) |
| `GET`  | `/api/progreso/<task_id>` | SSE stream de progreso |
| `POST` | `/api/topicos/cargar` | Carga tÃ³picos del dÃ­a |
| `POST` | `/api/topicos/sugerir` | Sugiere tÃ­tulos con Gemini |
| `POST` | `/api/topicos/generar` | Genera desde mÃºltiples tÃ³picos |
| `GET`  | `/api/borradores` | Lista borradores guardados |
| `GET`  | `/api/borrador/<f>` | Obtiene datos de un borrador |
| `POST` | `/api/borrador/<f>/guardar` | Guarda cambios |
| `POST` | `/api/borrador/<f>/publicar` | Publica en WordPress |
| `POST` | `/api/borrador/<f>/eliminar` | Elimina borrador |
| `POST` | `/api/borrador/<f>/imagen` | Sube imagen al borrador |

### Lanzar el servidor

```cmd
python app.py
```

El servidor queda disponible en **http://localhost:5000**.

### `app.py`

```python
import streamlit as st
from dotenv import load_dotenv
import os
from core.gemini_client import GeminiClient
from core.wp_client import WordPressClient
from core.orchestrator import ContentOrchestrator

load_dotenv()

# â”€â”€ ConfiguraciÃ³n de pÃ¡gina â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
st.set_page_config(
    page_title="ðŸ¤– Blog Content Generator",
    page_icon="âœï¸",
    layout="centered"
)

st.title("âœï¸ Generador de Contenido para WordPress")
st.caption("Genera 3 borradores SEO con un solo input Â· Human-in-the-Loop")

# â”€â”€ Formulario de entrada â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
with st.form("input_form"):
    user_input = st.text_input(
        label="TÃ³pico o URL de Amazon",
        placeholder="Ej: auriculares inalÃ¡mbricos Sony WH-1000XM5  Ã³  https://amazon.es/dp/XXXXXXXXX"
    )
    is_url = st.checkbox("Es una URL de Amazon (con link de afiliado)")
    submit = st.form_submit_button("ðŸš€ Generar 3 Borradores")

# â”€â”€ Procesamiento â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

    st.success("âœ… Â¡3 borradores creados exitosamente en WordPress!")

    # â”€â”€ Mostrar resumen de resultados â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for draft in drafts:
        type_labels = {
            "comparativa": "ðŸ“Š Post A â€“ Comparativa",
            "guia":        "ðŸ“– Post B â€“ GuÃ­a de Beneficios",
            "resena_seo":  "ðŸ” Post C â€“ ReseÃ±a SEO",
        }
        with st.expander(f"{type_labels[draft.post_type]} Â· WP ID: {draft.wp_post_id}"):
            st.markdown(f"**TÃ­tulo:** {draft.title}")
            st.markdown(f"**Meta Description:** {draft.meta_description}")
            st.markdown(f"**Focus Keyword:** `{draft.focus_keyword}`")
            wp_url = f"{os.getenv('WP_BASE_URL')}/wp-admin/post.php?post={draft.wp_post_id}&action=edit"
            st.link_button("ðŸ“ Editar en WordPress", wp_url)
```

---

## 7. IntegraciÃ³n con WordPress REST API

### `core/wp_client.py`

```python
import requests
from requests.auth import HTTPBasicAuth
from models.post_draft import PostDraft
from loguru import logger

class WordPressClient:
    def __init__(self, base_url: str, username: str, app_password: str):
        # Usar Application Passwords (disponible desde WP 5.6)
        # Generar en: WP Admin â†’ Usuarios â†’ Tu perfil â†’ Application Passwords
        self.base_url = base_url.rstrip("/")
        self.auth     = HTTPBasicAuth(username, app_password)
        self.headers  = {"Content-Type": "application/json"}

    def create_draft(self, draft: PostDraft) -> int:
        """Crea un post en estado 'draft' y retorna su ID de WordPress."""
        endpoint = f"{self.base_url}/wp-json/wp/v2/posts"

        payload = {
            "title":   draft.title,
            "content": draft.content,
            "status":  "draft",               # â† NUNCA se publica automÃ¡ticamente
            "meta": {
                # Campos personalizados (requiere plugin o cÃ³digo en functions.php)
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

> **Nota de seguridad:** Usar siempre **Application Passwords** de WordPress 5.6+ en lugar de la contraseÃ±a principal. XML-RPC debe estar desactivado.

---

## 8. ConfiguraciÃ³n de Campos Personalizados para Afiliados

### OpciÃ³n A: Registrar campos en `functions.php` (sin plugin)

AÃ±ade este cÃ³digo al archivo `functions.php` de tu tema hijo en WordPress:

```php
<?php
// Registrar campos custom para posts generados por IA
function register_affiliate_meta_fields() {
    $fields = [
        'affiliate_url'    => 'URL del producto en Amazon (con tag de afiliado)',
        'post_type_label'  => 'Tipo de post IA: comparativa | guia | resena_seo',
        'focus_keyword'    => 'Keyword principal para SEO',
        'ai_generated'     => 'Flag: contenido generado por IA (1 = sÃ­)',
    ];

    foreach ($fields as $key => $description) {
        register_post_meta('post', $key, [
            'show_in_rest'  => true,   // Exponer en la REST API â† IMPORTANTE
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

> âš ï¸ **`'show_in_rest' => true`** es obligatorio para que el campo sea accesible y escribible desde la REST API de Python.

---

### OpciÃ³n B: Con el plugin Advanced Custom Fields (ACF)

1. Instalar el plugin **Advanced Custom Fields** desde el repositorio de WordPress.
2. Ir a **ACF â†’ Field Groups â†’ Add New**.
3. Crear un grupo llamado `"Datos de Afiliado"` con los siguientes campos:

| Nombre del campo | Tipo | Slug del campo |
|---|---|---|
| URL de Afiliado Amazon | URL | `affiliate_url` |
| Tipo de Post IA | Select (`comparativa`, `guia`, `resena_seo`) | `post_type_label` |
| Keyword Principal | Text | `focus_keyword` |
| Generado por IA | True/False | `ai_generated` |

4. Asignar el grupo de campos a: **Post Type â†’ Posts**.
5. Con ACF PRO, los campos son accesibles desde la REST API automÃ¡ticamente.
   Con ACF gratuito, aÃ±adir en `functions.php`:

```php
add_filter('acf/rest_api/post/get_fields', '__return_true');
```

---

### CÃ³mo usar `affiliate_url` en una plantilla de WordPress

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
            ðŸ›’ Ver precio en Amazon
        </a>
    </div>
<?php endif; ?>
```

> **Atributo `rel="nofollow sponsored"`**: requerido por las directrices de Google para links de afiliado. Evita penalizaciones SEO.

---

## 9. Flujo de Trabajo Completo

```
Usuario ingresa tÃ³pico/URL
         â”‚
         â–¼
    [Flask GUI]
         â”‚
         â–¼
  ContentOrchestrator
         â”‚
    â”Œâ”€â”€â”€â”€â”´â”€â”€â”€â”€â”
    â”‚  Gemini â”‚  â†’ Prompt A (Comparativa)  â†’ Draft JSON A
    â”‚   API   â”‚  â†’ Prompt B (GuÃ­a)         â†’ Draft JSON B
    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â†’ Prompt C (ReseÃ±a SEO)  â†’ Draft JSON C
         â”‚
         â–¼
  WordPressClient
    Â· POST /wp-json/wp/v2/posts  (status: draft) Ã— 3
    Â· Asigna meta fields (affiliate_url, keyword, etc.)
         â”‚
         â–¼
  WordPress Admin
    Â· 3 posts en estado BORRADOR
    Â· Revisar contenido, imÃ¡genes, formato
    Â· Ajustar CTAs y links de afiliado si es necesario
    Â· Cambiar estado a PUBLICADO manualmente
         â”‚
         â–¼
  Blog publicado âœ… (con links de afiliado + SEO optimizado)
```

---

## 10. Variables de Entorno y ConfiguraciÃ³n

### Archivo `.env`

```dotenv
# â”€â”€ Gemini AI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
GEMINI_API_KEY=AIzaSy...tu_clave_aqui

# â”€â”€ WordPress â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
WP_BASE_URL=https://tu-blog.com
WP_USERNAME=tu_usuario_wordpress
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
# Application Password: WP Admin â†’ Usuarios â†’ Perfil â†’ Application Passwords â†’ Add New

# â”€â”€ Amazon Affiliates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
AMAZON_AFFILIATE_TAG=tu-tag-20
```

### `.gitignore`

```gitignore
.env
__pycache__/
*.pyc
logs/
.env  # (Flask usa .env, no .streamlit/)
```

---

## 11. Requisitos Previos y Setup Inicial

### Paso 1 â€” Crear entorno virtual e instalar dependencias

```cmd
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Paso 2 â€” Obtener clave de Gemini API

1. Ir a [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Crear proyecto y generar API Key.
3. El modelo recomendado es **`gemini-1.5-flash`** por su balance costo/velocidad/contexto (1M tokens).

### Paso 3 â€” Configurar Application Password en WordPress

1. `WordPress Admin â†’ Usuarios â†’ Tu Perfil`
2. Scroll hasta la secciÃ³n **"Application Passwords"**
3. Nombre: `"Content Generator Bot"` â†’ clic en **"Add New Application Password"**
4. Copiar la contraseÃ±a generada (formato: `xxxx xxxx xxxx xxxx xxxx xxxx`) al `.env`

### Paso 4 â€” Registrar meta fields en WordPress

Copiar el cÃ³digo PHP de la [SecciÃ³n 8](#8-configuraciÃ³n-de-campos-personalizados-para-afiliados) en el `functions.php` de tu tema hijo.

### Paso 5 â€” Ejecutar la aplicaciÃ³n

```cmd
python app.py
```

La interfaz web estarÃ¡ disponible en `http://localhost:5000`

---

## âœ… Checklist de ImplementaciÃ³n

- [ ] Crear entorno virtual y ejecutar `pip install -r requirements.txt`
- [ ] Configurar archivo `.env` con todas las credenciales
- [ ] Registrar Application Password en WordPress
- [ ] AÃ±adir meta fields en `functions.php` (con `show_in_rest: true`)
- [ ] Verificar que la REST API de WordPress estÃ¡ activa: `GET /wp-json/wp/v2/posts`
- [ ] Probar llamada bÃ¡sica a Gemini API con `gemini_client.py`
- [ ] Probar creaciÃ³n de draft en WordPress con `wp_client.py`
- [ ] Ejecutar flujo completo desde la interfaz Flask (http://localhost:5000)
- [ ] Revisar y publicar borradores manualmente desde WordPress Admin
- [ ] Verificar atributos `rel="nofollow sponsored"` en links de afiliado

---

*Documento actualizado el 27 de marzo de 2026 Â· Arquitectura Human-in-the-Loop v2.0 Â· Flask*

