"""
gemini_client.py
Wrapper del SDK de Gemini con:
  - MODO MOCK: respuestas pre-fabricadas, 0 tokens gastados (para desarrollo/pruebas)
  - MODO REAL: llamadas a la API de Gemini con retry automático
  - Forzado de respuesta en formato JSON
  - Logging de tokens usados en cada llamada real
  - Soporte de proxy corporativo (HTTP_PROXY / HTTPS_PROXY en .env)
  - Detección y clasificación de errores de red (DNS, proxy, timeout)

Modelo por defecto: gemini-2.5-flash (compatible con el tier gratuito).

Modo mock se activa cuando:
  · GEMINI_MOCK_MODE=true en .env
  · O cuando GEMINI_API_KEY no existe / tiene el valor por defecto
"""
from __future__ import annotations

import json
import os
import re
import socket
import time

from loguru import logger

from core.prompt_templates import PROMPT_MAP, build_prompt
from core.token_manager import FREE_TIER_RPD

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
_CALL_TIMEOUT = 45          # segundos máximo por llamada a Gemini
_RETRY_WAITS  = (2, 4, 8)   # backoff entre reintentos (segundos)

# Dominios que el DNS corporativo bloquea → resolvemos via DNS público
_BLOCKED_DOMAINS = (
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
)
_ALT_DNS = "8.8.8.8"   # Google Public DNS (también vale "1.1.1.1")

# ---------------------------------------------------------------------------
# Patch de DNS: resuelve dominios bloqueados vía DNS alternativo
# ---------------------------------------------------------------------------
_original_getaddrinfo = socket.getaddrinfo

def _patched_getaddrinfo(host, port, *args, **kwargs):
    """
    Intercepta getaddrinfo. Si el host es uno de los dominios bloqueados
    por el DNS corporativo, lo resuelve directamente via DNS público (8.8.8.8)
    usando dnspython. Resto de hosts pasan por el resolvedor normal.
    """
    if isinstance(host, str) and any(host.endswith(d) for d in _BLOCKED_DOMAINS):
        try:
            import dns.resolver
            resolver = dns.resolver.Resolver(configure=False)
            resolver.nameservers = [_ALT_DNS]
            answers = resolver.resolve(host, "A")
            ip = str(answers[0])
            logger.debug(f"[DNS-patch] {host} → {ip} (via {_ALT_DNS})")
            return _original_getaddrinfo(ip, port, *args, **kwargs)
        except Exception as exc:
            logger.warning(f"[DNS-patch] Falló resolución alternativa para {host}: {exc}")
            # Continuar con el resolvedor normal (fallará igual, pero no peor)
    return _original_getaddrinfo(host, port, *args, **kwargs)


def _install_dns_patch() -> bool:
    """
    Instala el patch de DNS si dnspython está disponible.
    Devuelve True si el patch se instaló, False si dnspython no está instalado.
    """
    try:
        import dns.resolver  # noqa: F401
        socket.getaddrinfo = _patched_getaddrinfo
        logger.info(f"[DNS-patch] Activado: dominios de Gemini → DNS {_ALT_DNS}")
        return True
    except ImportError:
        logger.warning(
            "[DNS-patch] dnspython no instalado. "
            "Ejecuta: pip install dnspython   para resolver el bloqueo DNS corporativo."
        )
        return False


# Respuestas pre-fabricadas para el modo MOCK (0 tokens, 0 coste)
# ---------------------------------------------------------------------------
_MOCK_RESPONSES: dict[str, dict] = {
    "comparativa": {
        "title": "[MOCK] {topic}: Comparativa completa vs su principal rival en 2026",
        "meta_description": "¿{topic} o su competidor? Analizamos precio, rendimiento y calidad para ayudarte a elegir la mejor opción.",
        "focus_keyword": "{topic} comparativa",
        "content": """
<h2>¿Por qué comparar {topic}?</h2>
<p>Este es un <strong>post de demostración (modo MOCK)</strong>.
No se realizó ninguna llamada a la API de Gemini — <em>0 tokens gastados</em>.</p>

<h2>Tabla Comparativa</h2>
<table>
  <thead>
    <tr><th>Criterio</th><th>{topic}</th><th>Competidor</th></tr>
  </thead>
  <tbody>
    <tr><td>Precio</td><td>⭐⭐⭐⭐</td><td>⭐⭐⭐</td></tr>
    <tr><td>Calidad de construcción</td><td>⭐⭐⭐⭐⭐</td><td>⭐⭐⭐⭐</td></tr>
    <tr><td>Garantía</td><td>2 años</td><td>1 año</td></tr>
    <tr><td>Soporte técnico</td><td>24/7</td><td>Horario laboral</td></tr>
    <tr><td>Relación calidad-precio</td><td>Excelente</td><td>Buena</td></tr>
    <tr><td>Facilidad de uso</td><td>⭐⭐⭐⭐⭐</td><td>⭐⭐⭐</td></tr>
  </tbody>
</table>

<h2>¿Para quién es cada opción?</h2>
<ul>
  <li><strong>{topic}</strong>: ideal para usuarios que priorizan calidad y durabilidad.</li>
  <li><strong>Competidor</strong>: mejor opción si el presupuesto es el factor clave.</li>
</ul>

<h2>Conclusión</h2>
<p>Si buscas la mejor opción sin compromisos, <strong>{topic}</strong> es la elección ganadora.</p>
<p><a href="{affiliate_url}" rel="nofollow sponsored" target="_blank">👉 Ver precio de {topic} en Amazon</a></p>
""",
    },

    "guia": {
        "title": "[MOCK] Guía completa de {topic}: beneficios, usos y consejos en 2026",
        "meta_description": "Descubre todo sobre {topic}: sus principales beneficios, casos de uso reales y consejos para aprovecharlos al máximo.",
        "focus_keyword": "guía {topic}",
        "content": """
<h2>¿Qué es {topic} y para qué sirve?</h2>
<p>Este es un <strong>post de demostración (modo MOCK)</strong>.
No se realizó ninguna llamada a la API de Gemini — <em>0 tokens gastados</em>.</p>

<h2>5 Beneficios Principales</h2>
<ol>
  <li><strong>Ahorro de tiempo</strong>: automatiza tareas repetitivas de forma sencilla.</li>
  <li><strong>Mejor rendimiento</strong>: resultados notables desde el primer uso.</li>
  <li><strong>Diseño ergonómico</strong>: pensado para un uso cómodo y prolongado.</li>
  <li><strong>Alta durabilidad</strong>: materiales de calidad que resisten el paso del tiempo.</li>
  <li><strong>Soporte incluido</strong>: acceso a comunidad y recursos de ayuda.</li>
</ol>

<h2>Casos de Uso Reales</h2>
<h3>1. Uso doméstico</h3>
<p>Perfecto para el hogar, simplifica la rutina diaria de toda la familia.</p>
<h3>2. Uso profesional</h3>
<p>Adoptado por profesionales que necesitan fiabilidad en su día a día.</p>
<h3>3. Uso ocasional</h3>
<p>Ideal para quienes buscan un producto sin curva de aprendizaje.</p>

<h2>¿Para quién es ideal?</h2>
<ul>
  <li>✅ Principiantes sin experiencia previa</li>
  <li>✅ Profesionales con necesidades específicas</li>
  <li>✅ Familias que buscan practicidad</li>
</ul>

<p><a href="{affiliate_url}" rel="nofollow sponsored" target="_blank">🛒 Consigue {topic} en Amazon</a></p>
""",
    },

    "resena_seo": {
        "title": "[MOCK] {topic} — Reseña y opinión honesta 2026 ¿Vale la pena?",
        "meta_description": "Reseña completa de {topic}: analizamos sus puntos fuertes, débiles y si realmente vale la pena comprarlo en 2026.",
        "focus_keyword": "{topic} opiniones 2026",
        "content": """
<h2>Introducción: ¿Por qué analizar {topic}?</h2>
<p>Este es un <strong>post de demostración (modo MOCK)</strong>.
No se realizó ninguna llamada a la API de Gemini — <em>0 tokens gastados</em>.</p>

<h2>Características Principales</h2>
<ul>
  <li>🔧 Tecnología de última generación integrada</li>
  <li>📦 Incluye todos los accesorios necesarios</li>
  <li>🌍 Compatible con estándares internacionales</li>
  <li>⚡ Configuración rápida en menos de 10 minutos</li>
</ul>

<h2>Pros y Contras</h2>
<h3>✅ Lo que nos encantó</h3>
<ul>
  <li>Excelente relación calidad-precio del mercado</li>
  <li>Construcción sólida y materiales premium</li>
  <li>Garantía extendida de 2 años incluida</li>
</ul>
<h3>❌ Lo que mejoraríamos</h3>
<ul>
  <li>El manual de instrucciones podría ser más detallado</li>
  <li>Los colores disponibles son limitados</li>
</ul>

<h2>Veredicto Final</h2>
<p>⭐⭐⭐⭐ (4.2/5) — <strong>Muy recomendado</strong> para la mayoría de perfiles de usuario.
Relación calidad-precio difícil de superar en su rango de precio.</p>

<p>
  <a href="{affiliate_url}" rel="nofollow sponsored" target="_blank">🛒 Comprar {topic} en Amazon con el mejor precio</a>
</p>

<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Review",
  "itemReviewed": {{ "@type": "Product", "name": "{topic}" }},
  "reviewRating": {{ "@type": "Rating", "ratingValue": "4.2", "bestRating": "5" }},
  "author": {{ "@type": "Person", "name": "Editor del Blog" }},
  "reviewBody": "Excelente producto con una relación calidad-precio destacada en 2026."
}}
</script>
""",
    },
}


# ---------------------------------------------------------------------------
# Cliente principal
# ---------------------------------------------------------------------------

class GeminiClient:
    """
    Cliente para la API de Gemini con modo MOCK integrado y gestión de tokens.

    Características:
      · mock_mode=True  → respuestas pre-fabricadas, 0 tokens gastados
      · mock_mode=False → llamadas reales a Gemini con tracking de tokens
      · Integración con TokenManager: rotación automática de API keys
      · Registro real de tokens prompt + respuesta por cada llamada
      · Auto-rotación cuando una clave supera el límite diario
    """

    def __init__(
        self,
        token_manager=None,        # instancia de TokenManager (opcional)
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        mock_mode: bool | None = None,
    ) -> None:
        from core.token_manager import TokenManager

        # ── Configurar proxy ANTES de importar google.generativeai ────────
        # El SDK de Gemini usa gRPC que respeta las variables de entorno
        # HTTP_PROXY / HTTPS_PROXY estándar.
        _apply_proxy_from_env()

        # ── Patch DNS: resuelve dominios de Gemini bloqueados por DNS corp ─
        _install_dns_patch()

        # ── Determinar si usamos mock ──────────────────────────────────────
        if mock_mode is None:
            env_mock = os.getenv("GEMINI_MOCK_MODE", "false").lower()
            mock_mode = env_mock in ("true", "1", "yes")

        # Si nos pasan un TokenManager lo usamos; si no, creamos uno
        if token_manager is not None:
            self.token_manager = token_manager
        else:
            self.token_manager = TokenManager.from_env()

        # Auto-activar mock si ninguna clave es válida
        if not mock_mode and not self.token_manager.valid_keys_count:
            logger.warning(
                "⚠️  No hay API keys válidas configuradas. "
                "Activando MODO MOCK automáticamente (0 tokens gastados)."
            )
            mock_mode = True

        # También activar mock si la única clave es el placeholder
        if not mock_mode:
            key = self.token_manager.get_active_key()
            if not key or "REEMPLAZA" in key or len(key) < 20:
                logger.warning(
                    "⚠️  GEMINI_API_KEY no configurada o inválida. "
                    "Activando MODO MOCK automáticamente (0 tokens gastados)."
                )
                mock_mode = True

        self.mock_mode   = mock_mode
        self._model_name = model
        self._model      = None
        self._genai      = None

        if not mock_mode:
            try:
                import google.generativeai as genai
                self._genai = genai
                self._init_model(self.token_manager.get_active_key())
                logger.info(
                    f"GeminiClient → MODO REAL | modelo: {model} | "
                    f"clave activa: {self.token_manager.active_key.alias} "
                    f"({self.token_manager.active_key.key_preview})"
                )
            except ImportError:
                logger.warning(
                    "⚠️  Librería 'google-generativeai' no instalada. "
                    "Activando MODO MOCK automáticamente."
                )
                self.mock_mode = True
        else:
            logger.info("GeminiClient → MODO MOCK (0 tokens gastados)")

    def _init_model(self, api_key: str):
        """Inicializa (o re-inicializa) el modelo con la clave dada."""
        self._genai.configure(api_key=api_key)
        self._model = self._genai.GenerativeModel(
            model_name=self._model_name,
            generation_config=self._genai.GenerationConfig(
                temperature=0.7,
                max_output_tokens=8192,   # máximo del tier gratuito gemini-2.5-flash
            ),
        )

    # ------------------------------------------------------------------
    # Método público principal
    # ------------------------------------------------------------------

    def generate_draft(
        self,
        post_type: str,
        topic: str,
        affiliate_url: str | None,
        prompt_map: dict | None = None,
        focus: str = "",
        reviewer: str = "",
    ) -> dict:
        """
        Genera un borrador de post.
        En modo MOCK devuelve respuesta pre-fabricada instantáneamente.
        En modo REAL llama a Gemini con retry automático.

        Args:
            prompt_map: mapa {post_type: prompt} a usar. Si es None usa PROMPT_MAP (modo Amazon).
            focus:      Enfoque específico del artículo (ángulo/perspectiva). Puede ser vacío.
            reviewer:   Persona que revisará el contenido ("Médico", "Psicólogo", "Editor" o "").

        Returns:
            dict con keys: title, meta_description, focus_keyword,
                           content, post_type, affiliate_url
        """
        active_map = prompt_map if prompt_map is not None else PROMPT_MAP
        if post_type not in active_map:
            raise ValueError(
                f"post_type '{post_type}' no reconocido. "
                f"Opciones: {list(active_map.keys())}"
            )

        if self.mock_mode:
            return self._generate_mock(post_type, topic, affiliate_url)
        return self._generate_real(
            post_type, topic, affiliate_url,
            prompt_map = active_map,
            focus      = focus,
            reviewer   = reviewer,
        )

    # ------------------------------------------------------------------
    # Backend MOCK
    # ------------------------------------------------------------------

    def _generate_mock(self, post_type: str, topic: str, affiliate_url: str | None) -> dict:
        """Devuelve contenido pre-fabricado sin tocar la API."""
        time.sleep(0.6)  # simula latencia de red para que la barra de progreso se vea bien
        template = _MOCK_RESPONSES[post_type]
        aff = affiliate_url or "#"

        data = {
            "title":            template["title"].format(topic=topic, affiliate_url=aff),
            "meta_description": template["meta_description"].format(topic=topic, affiliate_url=aff),
            "focus_keyword":    template["focus_keyword"].format(topic=topic),
            "content":          template["content"].format(topic=topic, affiliate_url=aff),
            "post_type":        post_type,
            "affiliate_url":    affiliate_url,
            "_mock":            True,   # flag para que la GUI lo indique visualmente
        }
        logger.success(
            f"[MOCK] Draft '{post_type}' generado para «{topic}» — 0 tokens gastados"
        )
        return data

    # ------------------------------------------------------------------
    # Backend REAL (con tracking de tokens y auto-rotación de clave)
    # ------------------------------------------------------------------

    def _generate_real(
        self,
        post_type: str,
        topic: str,
        affiliate_url: str | None,
        prompt_map: dict | None = None,
        focus: str = "",
        reviewer: str = "",
    ) -> dict:
        """Llama a la API de Gemini con hasta 3 reintentos y registra tokens reales."""
        # Rotar clave si la activa está agotada antes de intentar
        self.token_manager.rotate_if_exhausted()
        self._init_model(self.token_manager.get_active_key())

        active_map = prompt_map if prompt_map is not None else PROMPT_MAP

        # Usar build_prompt para inyectar focus_block y reviewer_block correctamente
        prompt = build_prompt(
            prompt_template = active_map[post_type],
            topic           = topic,
            affiliate_url   = affiliate_url or "#",
            focus           = focus,
            reviewer        = reviewer,
        )

        logger.info(
            f"[Gemini] Generando '{post_type}' para «{topic}» | "
            f"clave: {self.token_manager.active_key.alias}"
        )

        last_exc: Exception | None = None
        for attempt in range(1, 4):       # 3 intentos
            try:
                # ── Timeout real: se aplica con signal/threading via wrapper ─
                response = _call_with_timeout(
                    self._model, prompt, timeout=_CALL_TIMEOUT
                )

                # ── Registrar tokens reales ────────────────────────────────
                prompt_tokens   = 0
                response_tokens = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    meta            = response.usage_metadata
                    prompt_tokens   = getattr(meta, "prompt_token_count", 0) or 0
                    response_tokens = getattr(meta, "candidates_token_count", 0) or 0
                    total_tokens    = prompt_tokens + response_tokens
                    logger.info(
                        f"[Tokens] {self.token_manager.active_key.alias} | "
                        f"prompt: {prompt_tokens} + resp: {response_tokens} "
                        f"= {total_tokens} tokens | "
                        f"hoy: {self.token_manager.active_key.today_requests + 1} req"
                    )

                self.token_manager.record_usage(prompt_tokens, response_tokens)

                # Avisar si la clave está cerca del límite
                if self.token_manager.active_key.needs_warning:
                    logger.warning(
                        f"[TokenManager] ⚠️  {self.token_manager.active_key.alias} al "
                        f"{self.token_manager.active_key.pct_used_today * 100:.0f}% "
                        f"del límite diario."
                    )

                # ── Parsear respuesta ──────────────────────────────────────
                raw_text = _strip_markdown_codeblock(response.text.strip())
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as exc:
                    repaired = _repair_truncated_json(raw_text)
                    if repaired:
                        logger.warning(
                            f"[Gemini] JSON truncado reparado automáticamente "
                            f"(resp_tokens={response_tokens})."
                        )
                        data = repaired
                    else:
                        logger.error(f"[Gemini] Respuesta no es JSON válido: {raw_text[:300]}")
                        raise ValueError(f"Gemini no devolvió JSON válido: {exc}") from exc

                data["post_type"]     = post_type
                data["affiliate_url"] = affiliate_url
                data["_mock"]         = False
                data["_tokens_used"]  = prompt_tokens + response_tokens
                data["_key_alias"]    = self.token_manager.active_key.alias

                logger.success(
                    f"[Gemini] '{post_type}' generado | "
                    f"{prompt_tokens + response_tokens} tokens | "
                    f"Título: {data.get('title', '(sin título)')}"
                )
                return data

            except Exception as exc:
                error_str = str(exc)

                # ── Clasificar el error ────────────────────────────────────
                net_error = _classify_network_error(error_str)
                if net_error:
                    # Error de red: no tiene sentido esperar mucho, reintentar rápido
                    last_exc = exc
                    wait = _RETRY_WAITS[attempt - 1]
                    logger.warning(
                        f"[Red] {net_error} | "
                        f"Intento {attempt}/3 — reintentando en {wait}s…"
                    )
                    time.sleep(wait)
                    continue

                # ── Error de cuota (429) ───────────────────────────────────
                if _is_quota_error(error_str):
                    self.token_manager.record_error()

                    # Extraer retry_delay que la API nos indica (ej: "retry_delay { seconds: 23 }")
                    retry_match = re.search(
                        r'retry[_\s]*delay[^0-9]*(\d+(?:\.\d+)?)', error_str
                    ) or re.search(r'retry[^0-9]*(\d+(?:\.\d+)?)\s*s', error_str)
                    suggested_wait = float(retry_match.group(1)) + 2 if retry_match else 30

                    logger.warning(
                        f"[TokenManager] Cuota agotada en "
                        f"{self.token_manager.active_key.alias} "
                        f"(límite diario: {FREE_TIER_RPD} req). Rotando clave…"
                    )
                    rotated = self.token_manager.rotate(reason="quota-error")
                    if rotated:
                        self._init_model(self.token_manager.get_active_key())
                        last_exc = exc
                        logger.info(f"[Gemini] Nueva clave activa. Esperando {suggested_wait}s…")
                        time.sleep(suggested_wait)
                        continue
                    else:
                        # No hay más claves — esperar el tiempo sugerido y reintentar misma clave
                        logger.warning(
                            f"[TokenManager] Sin claves alternativas. "
                            f"Esperando {suggested_wait}s para respetar rate limit…"
                        )
                        last_exc = exc
                        time.sleep(suggested_wait)
                        continue

                # ── Cualquier otro error ───────────────────────────────────
                last_exc = exc
                wait = _RETRY_WAITS[attempt - 1]
                logger.warning(
                    f"[Gemini] Intento {attempt}/3 falló: {exc}. "
                    f"Reintentando en {wait}s…"
                )
                time.sleep(wait)

        raise RuntimeError(f"Gemini falló tras 3 intentos: {last_exc}") from last_exc

    def test_connection(self) -> bool:
        """Prueba rápida de conectividad. En modo MOCK siempre devuelve True."""
        if self.mock_mode:
            logger.info("[MOCK] test_connection → True (sin red)")
            return True
        try:
            self._model.generate_content("Responde solo: OK")
            logger.success(
                f"[Gemini] Conexión verificada | "
                f"clave: {self.token_manager.active_key.alias}"
            )
            return True
        except Exception as exc:
            logger.error(f"[Gemini] Error de conexión: {exc}")
            return False


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _strip_markdown_codeblock(text: str) -> str:
    """
    Elimina bloques ```json ... ``` que el modelo incluye por error.
    Maneja variantes: con/sin 'json', con espacios extra, multilínea.
    """
    stripped = text.strip()
    # Caso 1: bloque completo ``` ... ```
    pattern = r"^```(?:json|JSON)?\s*\n?([\s\S]*?)\n?\s*```\s*$"
    match = re.match(pattern, stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Caso 2: comienza con ``` pero no cierra limpio (respuesta truncada o extra texto)
    if stripped.startswith("```"):
        # Quitar primera línea (```json) y último ```
        lines = stripped.splitlines()
        # Eliminar primera línea con los backticks
        lines = lines[1:]
        # Eliminar última línea si son backticks
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _repair_truncated_json(text: str) -> dict | None:
    """
    Intenta recuperar un JSON truncado que el modelo cortó a mitad de generación.

    Estrategia:
      1. Si el JSON está casi completo (falta solo cerrar el último campo y el objeto),
         intenta cerrarlo añadiendo '"}' o similar.
      2. Extrae campo a campo con regex como fallback para rescatar title/meta/keyword.
      3. Si no se puede reparar, devuelve None para que el caller reintente.
    """
    stripped = text.strip()

    # --- Intento 1: cierre directo con variantes ---
    for suffix in ['"}', '"}\n}', '}', '\n}', '..."}', '...\n"}']:
        candidate = stripped + suffix
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # --- Intento 2: extraer campos individuales con regex ---
    # Funciona aunque el campo "content" esté truncado
    fields = {}

    for field in ("title", "meta_description", "focus_keyword"):
        m = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"', stripped)
        if m:
            fields[field] = m.group(1)

    # Para "content" capturar todo lo que haya aunque esté incompleto
    content_match = re.search(r'"content"\s*:\s*"([\s\S]*)', stripped)
    if content_match:
        raw_content = content_match.group(1)
        # Quitar trailing comillas/llaves incompletas y limpiar
        raw_content = re.sub(r'["\s}]+$', '', raw_content).strip()
        # Asegurarse de que el HTML quede cerrado básicamente
        if raw_content and not raw_content.endswith(">"):
            raw_content += "... <em>[contenido truncado — regenerar si es necesario]</em>"
        fields["content"] = raw_content

    if "title" in fields and "content" in fields:
        # Rellenar campos opcionales si no se extrajeron
        fields.setdefault("meta_description", fields["title"][:155])
        fields.setdefault("focus_keyword", "")
        logger.warning(
            f"[Gemini] JSON reparado por extracción de campos. "
            f"Title: '{fields.get('title', '')[:50]}'"
        )
        return fields

    return None


# ---------------------------------------------------------------------------
# Helpers de timeout, proxy y clasificación de errores de red
# ---------------------------------------------------------------------------

def _apply_proxy_from_env() -> None:
    """
    Configura las variables de entorno HTTP_PROXY / HTTPS_PROXY antes de que
    el SDK de google.generativeai (gRPC + httpx) las lea.
    Si no están definidas en .env, limpia cualquier valor residual.
    """
    https_proxy = os.getenv("HTTPS_PROXY", "").strip()
    http_proxy  = os.getenv("HTTP_PROXY",  "").strip()

    if https_proxy:
        os.environ["HTTPS_PROXY"] = https_proxy
        os.environ["HTTP_PROXY"]  = http_proxy or https_proxy
        logger.info(f"[Proxy] Proxy corporativo activo: {https_proxy}")
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY",  None)


def _call_with_timeout(model, prompt: str, timeout: int = _CALL_TIMEOUT):
    """
    Envuelve model.generate_content() con un timeout real usando threading.
    Si la llamada supera `timeout` segundos, lanza TimeoutError.
    """
    import threading

    result: list   = []
    error:  list   = []

    def _worker():
        try:
            result.append(
                model.generate_content(
                    prompt,
                    request_options={"timeout": timeout},
                )
            )
        except Exception as exc:
            error.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout + 5)   # +5 s de gracia para el SDK

    if thread.is_alive():
        raise TimeoutError(
            f"La llamada a Gemini superó {timeout}s. "
            "Comprueba la conexión o el proxy corporativo."
        )
    if error:
        raise error[0]
    return result[0]


_NETWORK_PATTERNS: list[tuple[str, str]] = [
    ("dns",                    "Error DNS: no se puede resolver generativelanguage.googleapis.com"),
    ("wsa error 11001",        "Error DNS (WSA 11001): host desconocido"),
    ("getaddrinfo failed",     "Error DNS: getaddrinfo falló"),
    ("getaddrinfo",            "Error DNS: no se pudo resolver el host"),
    ("no such host",           "Error DNS: host no encontrado"),
    ("name or service",        "Error DNS: nombre de servicio no encontrado"),
    ("tcp handshaker",         "Fallo TLS/handshake (proxy corporativo o firewall)"),
    ("ssl",                    "Error SSL/TLS (posible intercepción por proxy corporativo)"),
    ("proxy",                  "Error de proxy: verifica HTTP_PROXY/HTTPS_PROXY en .env"),
    ("connection refused",     "Conexión rechazada por el servidor remoto"),
    ("network is unreachable", "Red inalcanzable: sin conectividad"),
    ("timed out",              "Timeout de red: la conexión tardó demasiado"),
    ("timeout",                "Timeout: la API no respondió a tiempo"),
]


def _classify_network_error(error_str: str) -> str | None:
    """
    Analiza el mensaje de error y devuelve una descripción amigable si
    es un problema de red/proxy/DNS, o None si no lo es.
    """
    lower = error_str.lower()
    for pattern, message in _NETWORK_PATTERNS:
        if pattern in lower:
            return message
    return None


def _is_quota_error(error_str: str) -> bool:
    """Devuelve True si el error indica cuota agotada (HTTP 429)."""
    lower = error_str.lower()
    return any(k in lower for k in ("429", "quota", "exhausted", "resource_exhausted"))
