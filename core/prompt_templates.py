# =============================================================================
#  PLANTILLAS DE PROMPT PARA GENERACIÓN DE CONTENIDO SEO
#  Modo Amazon: comparativa + guía + reseña (con CTAs de afiliado)
#  Modo Libre:  opinion + listicle + how-to (sin producto, cualquier tópico)
#
#  Variables disponibles en todos los prompts:
#    {topic}            — tópico o producto principal
#    {affiliate_url}    — URL de afiliado (Amazon) o "#" en modo libre
#    {focus}            — enfoque específico del artículo (puede estar vacío "")
#    {reviewer_persona} — persona que revisará el contenido (médico, psicólogo, editor)
# =============================================================================

# -----------------------------------------------------------------------------
# BLOQUE DE ENFOQUE — se inyecta solo cuando el usuario especifica un enfoque
# -----------------------------------------------------------------------------
_FOCUS_BLOCK = """\

ENFOQUE ESPECÍFICO DEL ARTÍCULO:
El usuario ha indicado el siguiente ángulo o perspectiva para abordar el tema:
\"{focus}\"
Todo el desarrollo del artículo debe girar en torno a este enfoque.
No te desvíes hacia otros ángulos aunque sean relevantes.
"""

_FOCUS_BLOCK_EMPTY = ""  # sin enfoque → no se inyecta nada

# -----------------------------------------------------------------------------
# BLOQUE DE REVISOR — adapta el tono y rigor según quién revisará el contenido
# -----------------------------------------------------------------------------
_REVIEWER_BLOCKS: dict[str, str] = {
    "Médico": """\

IMPORTANTE — REVISIÓN MÉDICA:
Este artículo será revisado y validado por un médico antes de publicarse.
Por ello debes:
- Usar terminología clínica correcta y actualizada (DSM-5 / CIE-11 / evidencia EBM cuando aplique).
- Citar o referir estudios, estadísticas o guías clínicas reconocidas (menciona fuente y año).
- Evitar afirmaciones absolutas no respaldadas; usa "estudios sugieren", "la evidencia muestra".
- Diferenciar claramente entre síntomas, factores de riesgo y diagnóstico.
- Incluir una nota de advertencia al final: "Este artículo es de carácter informativo y no sustituye la consulta médica profesional."
- Mantener un tono claro y accesible para el lector general, sin sacrificar rigor científico.
""",
    "Psicólogo": """\

IMPORTANTE — REVISIÓN PSICOLÓGICA:
Este artículo será revisado y validado por un psicólogo antes de publicarse.
Por ello debes:
- Usar lenguaje psicológico preciso (cognición, conducta, emoción, apego, etc.) según corresponda.
- Referenciar corrientes o enfoques psicológicos relevantes (TCC, psicoanálisis, humanismo, neuropsicología…).
- Evitar estigmatizar condiciones de salud mental; usa lenguaje centrado en la persona.
- Distinguir entre salud mental, bienestar emocional y trastornos clínicos cuando sea pertinente.
- Incluir recursos de apoyo si el tema lo amerita (líneas de crisis, terapia, etc.).
- Incluir una nota: "Este contenido es informativo y no reemplaza la orientación de un profesional de la salud mental."
- Mantener empatía y accesibilidad en el tono sin perder base científica.
""",
    "Editor": """\

REVISIÓN EDITORIAL:
Este artículo será revisado por un editor de contenidos antes de publicarse.
Por ello debes:
- Priorizar claridad, fluidez y coherencia narrativa en cada párrafo.
- Usar frases de transición naturales entre secciones.
- Evitar repeticiones de palabras clave en el mismo párrafo (distribución natural).
- Cuidar la pirámide invertida: lo más importante primero.
- Asegurarte de que el título, la intro y la conclusión estén perfectamente alineados.
""",
}

_REVIEWER_DEFAULT = ""  # sin revisor específico


def _build_focus_block(focus: str) -> str:
    """Devuelve el bloque de enfoque formateado o vacío si no hay enfoque."""
    f = (focus or "").strip()
    if not f:
        return _FOCUS_BLOCK_EMPTY
    return _FOCUS_BLOCK.format(focus=f)


def _build_reviewer_block(reviewer: str) -> str:
    """Devuelve el bloque de instrucciones del revisor o vacío."""
    return _REVIEWER_BLOCKS.get(reviewer or "", _REVIEWER_DEFAULT)


# -----------------------------------------------------------------------------
# CONTEXTO BASE — Modo Amazon (producto/afiliado)
# Se inyecta en los 3 prompts para no repetir instrucciones y ahorrar tokens.
# -----------------------------------------------------------------------------
BASE_CONTEXT = """\
Eres un redactor SEO senior especializado en blogs de tecnología, salud y productos Amazon.
Escribes siempre en español de España (castellano peninsular). Usa «vosotros» cuando te dirijas al lector en plural, «ordenador» en vez de «computadora», «móvil» en vez de «celular», «coche» en vez de «carro», etc. Tono: cercano, profesional y empático.
Normas de formato:
  - Usa H2 para secciones principales y H3 para subsecciones.
  - Párrafos de 3-5 oraciones máximo para favorecer la legibilidad.
  - Incluye la keyword principal de forma natural al menos 3 veces (sin keyword stuffing).
  - El HTML debe ser limpio y semántico, listo para WordPress (sin <html>, <head>, <body>).
Normas de salida:
  - Responde ÚNICAMENTE con un objeto JSON válido, sin texto extra ni bloques markdown.
  - Dentro del campo "content", NO uses comillas dobles sin escapar; usa comillas simples o \\".
  - El JSON debe estar completo y bien cerrado. No lo cortes a mitad.
{focus_block}{reviewer_block}"""

# -----------------------------------------------------------------------------
# CONTEXTO BASE — Modo Libre (tópico sin producto)
# -----------------------------------------------------------------------------
BASE_CONTEXT_LIBRE = """\
Eres un redactor SEO senior especializado en contenido editorial de alta autoridad para blogs.
Escribes siempre en español de España (castellano peninsular). Usa «vosotros» cuando te dirijas al lector en plural, «ordenador» en vez de «computadora», «móvil» en vez de «celular», «coche» en vez de «carro», etc. Adapta el tono al tópico: informativo, divulgativo o práctico.
Normas de formato:
  - Usa H2 para secciones principales y H3 para subsecciones.
  - Párrafos de 3-5 oraciones máximo para favorecer la legibilidad.
  - Incluye la keyword principal de forma natural al menos 3 veces (sin keyword stuffing).
  - El HTML debe ser limpio y semántico, listo para WordPress (sin <html>, <head>, <body>).
  - NO incluyas CTAs ni referencias a Amazon; el contenido es puramente informativo/educativo.
Normas de salida:
  - Responde ÚNICAMENTE con un objeto JSON válido, sin texto extra ni bloques markdown.
  - Dentro del campo "content", NO uses comillas dobles sin escapar; usa comillas simples o \\".
  - El JSON debe estar completo y bien cerrado. No lo cortes a mitad.
{focus_block}{reviewer_block}"""

# -----------------------------------------------------------------------------
# POST A — Comparativa del producto con un competidor directo
# -----------------------------------------------------------------------------
PROMPT_COMPARATIVA = BASE_CONTEXT + """
TAREA: Escribe un artículo comparativo en español sobre: "{topic}".

Estructura requerida (en este orden exacto):
1. <h2>Introducción</h2> — Por qué importa elegir bien entre ambas opciones (contexto, problema del lector, gancho emocional). 2-3 párrafos.
2. <h2>Comparativa rápida</h2> — Tabla HTML (<table class='comparison-table'>) con mínimo 7 criterios:
   precio estimado, rendimiento, diseño/materiales, compatibilidad/ecosistema, consumo energético, garantía, relación calidad-precio.
   Usa <thead> con fondo oscuro y <tbody> con filas alternas.
3. <h2>Análisis a fondo: [Opción A]</h2> — H3 por cada criterio crítico; explica puntos fuertes y débiles con datos concretos.
4. <h2>Análisis a fondo: [Opción B]</h2> — Mismo nivel de profundidad que la sección anterior.
5. <h2>¿Cuál debes elegir?</h2> — 3 sub-secciones <h3> por perfil de usuario: presupuesto ajustado, usuario avanzado, usuario casual.
6. CTA final prominente:
   <div class='cta-box'><a href='{affiliate_url}' target='_blank' rel='nofollow sponsored' class='cta-amazon'>🛒 Ver precio actualizado en Amazon</a></div>

Requisitos de calidad:
- Longitud: 900–1100 palabras.
- Incluye al menos 1 dato numérico real o estadística verificable por sección.
- Usa listas <ul> o <ol> donde mejore la escaneabilidad.
- La conclusión debe ser clara y accionable; no dejes al lector sin una recomendación.

Devuelve SOLO este JSON (sin nada más):
{{
  "title": "Título SEO comparativo (máximo 60 caracteres, incluye VS o Comparativa)",
  "meta_description": "Meta description con intención comparativa (máximo 160 caracteres)",
  "focus_keyword": "keyword principal tipo 'X vs Y' o 'mejor X'",
  "content": "<h2>...</h2>...HTML completo del artículo..."
}}
"""

# -----------------------------------------------------------------------------
# POST B — Guía de beneficios y casos de uso
# -----------------------------------------------------------------------------
PROMPT_GUIA = BASE_CONTEXT + """
TAREA: Escribe una guía de beneficios y casos de uso en español sobre: "{topic}".

Estructura requerida (en este orden exacto):
1. <h2>Introducción</h2> — Abre con una pregunta retórica o estadística impactante. Explica qué problema resuelve este producto/servicio. 2-3 párrafos.
2. <h2>7 beneficios principales</h2> — Lista <ul> con emoji por ítem; cada punto en 1-2 oraciones. Sé específico y cuantificable cuando sea posible.
3. <h2>Casos de uso reales</h2> — 3 escenarios concretos, cada uno con <h3> propio. Describe el contexto, el usuario y el resultado esperado.
4. <h2>¿Para quién es ideal?</h2> — Tabla HTML con 2 columnas: "Perfil de usuario" y "Motivo de idoneidad". Mínimo 4 perfiles.
5. <h2>Consejos para sacarle el máximo provecho</h2> — Lista numerada <ol> con 5 consejos prácticos y accionables.
6. CTA:
   <div class='cta-box'><a href='{affiliate_url}' target='_blank' rel='nofollow sponsored' class='cta-amazon'>✅ Consíguelo ahora en Amazon</a></div>

Requisitos de calidad:
- Longitud: 800–1000 palabras.
- Tono orientado a solucionar el problema del lector, no a vender.
- Termina con una frase motivadora que refuerce la decisión de compra.

Devuelve SOLO este JSON:
{{
  "title": "Título SEO orientado a beneficios (máximo 60 caracteres)",
  "meta_description": "Meta con propuesta de valor clara (máximo 160 caracteres)",
  "focus_keyword": "keyword principal tipo 'para qué sirve X' o 'beneficios de X'",
  "content": "<h2>...</h2>...HTML completo del artículo..."
}}
"""

# -----------------------------------------------------------------------------
# POST C — Reseña SEO optimizada con CTA de afiliados
# -----------------------------------------------------------------------------
PROMPT_RESENA_SEO = BASE_CONTEXT + """
TAREA: Escribe una reseña SEO completa y optimizada en español sobre: "{topic}".

Estructura requerida (en este orden exacto):
1. <h2>¿Vale la pena?</h2> — Veredicto rápido en 2-3 oraciones al inicio (hook para el lector impaciente). Incluye puntuación: ⭐⭐⭐⭐ (X/5).
2. <h2>Ficha técnica</h2> — Tabla HTML (<table>) con: modelo exacto, precio orientativo, dimensiones/peso, materiales, conectividad, garantía, compatibilidad.
3. <h2>Características destacadas</h2> — Mínimo 3 H3 con descripción técnica y cómo impacta en la experiencia real de uso.
4. <h2>Pros y contras</h2> — Dos listas <ul> una al lado de la otra (usa <div class='pros-contras'>):
   <div class='pros'><h3>✅ Pros</h3><ul>...</ul></div>
   <div class='contras'><h3>❌ Contras</h3><ul>...</ul></div>
5. <h2>Experiencia de uso</h2> — Relato en primera persona del plural ("al usarlo notamos…"). Menciona al menos 2 escenarios de uso diario.
6. <h2>Comparación con alternativas</h2> — Menciona 1-2 competidores y por qué este producto sale ganando (o no) en cada punto.
7. <h2>Veredicto final</h2> — Resumen ejecutivo con puntuación detallada por categoría (rendimiento, diseño, precio, soporte) y recomendación clara.
8. Mínimo 2 CTAs:
   - Primero tras la ficha: <a href='{affiliate_url}' target='_blank' rel='nofollow sponsored' class='cta-amazon'>💰 Ver precio en Amazon</a>
   - Último al final: <a href='{affiliate_url}' target='_blank' rel='nofollow sponsored' class='cta-amazon'>🛒 Comprar con el mejor precio</a>

Requisitos de calidad:
- Longitud: 950–1150 palabras.
- Optimiza para las intenciones: "mejor {topic}", "{topic} precio", "{topic} opiniones", "{topic} análisis".
- Añade schema JSON-LD de tipo Review al final del content (dentro de <script type='application/ld+json'>).

Devuelve SOLO este JSON:
{{
  "title": "Título SEO de reseña (máximo 60 caracteres, incluye 'opinión' o 'análisis')",
  "meta_description": "Meta con veredicto resumido (máximo 160 caracteres)",
  "focus_keyword": "keyword principal tipo 'X opiniones' o 'mejor X'",
  "content": "<h2>...</h2>...HTML completo del artículo incluido JSON-LD..."
}}
"""

# Mapa de acceso por tipo de post
PROMPT_MAP: dict[str, str] = {
    "comparativa": PROMPT_COMPARATIVA,
    "guia":        PROMPT_GUIA,
    "resena_seo":  PROMPT_RESENA_SEO,
}

# =============================================================================
#  MODO LIBRE — Tópico sin producto/afiliado
# =============================================================================

# -----------------------------------------------------------------------------
# POST A libre — Artículo de Opinión / Columna
# -----------------------------------------------------------------------------
PROMPT_OPINION = BASE_CONTEXT_LIBRE + """
TAREA: Escribe un artículo de opinión / columna en español sobre: "{topic}".

Un artículo de opinión toma una postura clara, argumenta con datos, genera conversación
y es altamente compartible en redes. No es neutral: defiende un punto de vista con rigor.

Estructura requerida (en este orden exacto):
1. <h2>El problema que nadie quiere ver</h2> — Gancho de entrada: plantea la situación actual
   de forma directa y provocadora. 2-3 párrafos. Termina con la tesis central del artículo
   expresada en una oración memorable.
2. <h2>Por qué esto importa más de lo que creemos</h2> — Contextualiza el tema con datos,
   cifras o estudios recientes. Mínimo 1 estadística citada con fuente. 2-3 párrafos.
3. <h2>El argumento central</h2> — 3 sub-secciones <h3>, cada una con:
   - Un argumento concreto que soporte la tesis.
   - Evidencia, ejemplo real o analogía que lo respalde.
   - 1-2 párrafos por argumento.
4. <h2>La otra cara: objeciones habituales</h2> — Presenta 2-3 contraargumentos comunes
   usando <blockquote> para cada objeción, seguido de una refutación sólida.
5. <h2>Qué debería cambiar (y quién debe hacerlo)</h2> — Propuestas concretas y accionables.
   Lista <ul> con mínimo 4 puntos. Cada punto: acción + responsable + impacto esperado.
6. <h2>Reflexión final</h2> — Cierra con una conclusión que reafirme la tesis, conecte
   emocionalmente con el lector y termine con una pregunta abierta que invite a comentar
   o compartir el artículo.

Requisitos de calidad:
- Longitud: 900–1100 palabras.
- Tono: directo, apasionado pero argumentado; voz de experto que no teme tomar partido.
- Usa la primera persona del plural ("debemos", "nos enfrentamos") para conectar con el lector.
- Optimiza para: "{topic} opinión", "por qué {topic}", "{topic} reflexión", "{topic} análisis".
- El artículo debe provocar que el lector quiera compartirlo o comentarlo.

Devuelve SOLO este JSON:
{{
  "title": "Título de opinión directo y provocador (máximo 65 caracteres)",
  "meta_description": "Meta que expresa la postura del artículo (máximo 160 caracteres)",
  "focus_keyword": "keyword principal tipo '{topic} opinión' o 'por qué {topic}'",
  "content": "<h2>...</h2>...HTML completo del artículo..."
}}
"""

# -----------------------------------------------------------------------------
# POST B libre — Listicle / Top N
# -----------------------------------------------------------------------------
PROMPT_LISTICLE = BASE_CONTEXT_LIBRE + """
TAREA: Escribe un listicle SEO riguroso y atractivo en español sobre: "{topic}".

Los listicles de alta calidad combinan el formato escaneaable con profundidad real; evita los listicles superficiales de solo bullets.

Estructura requerida (en este orden exacto):
1. <h2>Introducción</h2> — 2-3 párrafos que:
   a) Justifiquen por qué este listado es relevante hoy.
   b) Anticipen el valor que recibirá el lector.
   c) Incluyan un dato o estadística de impacto.
2. <h2>Los N [elementos] de {topic}</h2> (usa número concreto entre 7 y 10) — Cada ítem con:
   - <h3>N. Nombre del elemento</h3>
   - Párrafo de 60-100 palabras explicando el punto con contexto, mecanismo y ejemplo real.
   - (Cuando aplique) <blockquote>Cita de experto o estadística relevante con fuente</blockquote>
3. <h2>¿Cómo aplicar este conocimiento?</h2> — Lista <ol> con 4-5 pasos concretos para que el lector implemente lo aprendido.
4. <h2>Conclusión</h2> — Recapitulación de los 3 puntos más importantes + llamada a la reflexión o siguiente acción del lector.

Requisitos de calidad:
- Longitud: 950–1150 palabras.
- Cada punto del listado debe aportar información única; evita repeticiones o puntos genéricos.
- Usa datos cuantitativos cuando existan (porcentajes, estudios, fechas).
- Optimiza para: "mejores {topic}", "top {topic}", "los más importantes {topic}".

Devuelve SOLO este JSON:
{{
  "title": "Título SEO listicle con número (máximo 60 caracteres, tipo 'Los 8 mejores X')",
  "meta_description": "Meta con número y promesa de valor (máximo 160 caracteres)",
  "focus_keyword": "keyword principal tipo 'mejores X' o 'top X'",
  "content": "<h2>...</h2>...HTML completo del artículo..."
}}
"""

# -----------------------------------------------------------------------------
# POST C libre — Guía paso a paso / How-To
# -----------------------------------------------------------------------------
PROMPT_HOWTO = BASE_CONTEXT_LIBRE + """
TAREA: Escribe una guía práctica paso a paso en español sobre: "{topic}".

Las guías how-to de alta calidad posicionan en featured snippets y búsquedas de intención "cómo hacer".

Estructura requerida (en este orden exacto):
1. <h2>Introducción</h2> — Qué logrará el lector, en cuánto tiempo y qué necesita previamente. Usa tono motivador. 2 párrafos.
2. <h2>Antes de empezar: requisitos y conceptos previos</h2> — Lista <ul> con lo que el lector necesita saber o tener. Explica brevemente cada ítem.
3. <h2>Guía paso a paso</h2> — Mínimo 5 pasos, cada uno con:
   - <h3>Paso N: [título descriptivo del paso]</h3>
   - Explicación clara (60-100 palabras) con instrucción precisa, no ambigua.
   - Ejemplo o caso concreto de aplicación.
   - <blockquote>💡 Consejo profesional: ...</blockquote> o <em>⚠️ Atención: ...</em> según corresponda.
4. <h2>Errores comunes y cómo evitarlos</h2> — Lista <ul> con 4-5 errores frecuentes; para cada uno: descripción del error + solución práctica.
5. <h2>Preguntas frecuentes (FAQ)</h2> — Mínimo 3 preguntas reales que busca el usuario + respuesta directa de 2-3 oraciones. Usa <h3> para cada pregunta.
6. <h2>Próximos pasos</h2> — Qué hacer después de completar la guía; recurso adicional o acción recomendada.

Requisitos de calidad:
- Longitud: 950–1150 palabras.
- Instrucciones en imperativo ("realiza", "verifica", "evita"…) para máxima claridad.
- El FAQ está estructurado para posicionar en "People Also Ask" de Google.
- Optimiza para: "cómo {topic}", "guía {topic}", "paso a paso {topic}", "tutorial {topic}".

Devuelve SOLO este JSON:
{{
  "title": "Título SEO how-to (máximo 60 caracteres, empieza con 'Cómo' o 'Guía')",
  "meta_description": "Meta con promesa de aprendizaje (máximo 160 caracteres)",
  "focus_keyword": "keyword principal tipo 'cómo X' o 'guía X'",
  "content": "<h2>...</h2>...HTML completo del artículo..."
}}
"""

# Mapa para modo libre
PROMPT_MAP_LIBRE: dict[str, str] = {
    "opinion":  PROMPT_OPINION,
    "listicle": PROMPT_LISTICLE,
    "howto":    PROMPT_HOWTO,
}


# =============================================================================
# Función de construcción de prompt final
# Se usa en gemini_client para inyectar focus + reviewer en el formato
# =============================================================================

def build_prompt(
    prompt_template: str,
    topic: str,
    affiliate_url: str,
    focus: str = "",
    reviewer: str = "",
) -> str:
    """
    Construye el prompt final inyectando focus_block y reviewer_block
    en el BASE_CONTEXT, luego formatea {topic} y {affiliate_url}.
    """
    focus_block    = _build_focus_block(focus)
    reviewer_block = _build_reviewer_block(reviewer)

    # Primero resolver los bloques en el contexto base
    prompt = prompt_template.format(
        focus_block    = focus_block,
        reviewer_block = reviewer_block,
        topic          = topic,
        affiliate_url  = affiliate_url,
    )
    return prompt
