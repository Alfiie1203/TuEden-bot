"""
token_manager.py
Sistema de gestión de pool de API Keys de Gemini con:

  · Múltiples claves (GEMINI_API_KEY_1, _2, _3...)
  · Contador de tokens reales por clave (prompt + respuesta)
  · Persistencia en JSON (sobrevive reinicios de la app)
  · Rotación automática cuando una clave alcanza el límite diario
  · Rotación manual desde la GUI
  · Estimación de blogs restantes basada en consumo histórico

Límites del tier GRATUITO de Gemini 1.5 Flash (marzo 2026):
  · 15 requests por minuto (RPM)
  · 1,000,000 tokens por minuto (TPM)  ← prácticamente ilimitado
  · 1,500 requests por día (RPD)
  · Sin límite de tokens por día en gratuito ← lo que más importa rastrear

Costo aproximado por blog completo (3 posts × ~2000 tokens c/u):
  · ~6,000 tokens por sesión completa (prompt + respuesta)
  · Con 1,500 RPD = ~500 sesiones/día teóricas (en la práctica menos)
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Constantes del tier gratuito de Gemini (actualizado marzo 2026)
# ---------------------------------------------------------------------------
# gemini-2.5-flash free tier: 20 RPD, 10 RPM por proyecto
# Fuente: error 429 API → quota_id GenerateRequestsPerDayPerProjectPerModel-FreeTier
FREE_TIER_RPD          = 20         # Requests por día máximo (tier gratuito, por proyecto)
FREE_TIER_RPM          = 10         # Requests por minuto (tier gratuito)
TOKENS_PER_BLOG_EST    = 6_000      # Estimación de tokens por sesión de 3 posts
TOKENS_PER_POST_EST    = 2_000      # Estimación por post individual
WARN_THRESHOLD_PCT     = 0.75       # Alerta cuando se usa el 75% de requests

# Ruta del archivo de persistencia
_STATE_FILE = Path("logs/token_usage.json")


# ---------------------------------------------------------------------------
# Modelo de datos de una clave
# ---------------------------------------------------------------------------
class ApiKeyStats:
    """Estadísticas de uso de una sola API key."""

    def __init__(self, alias: str, key: str):
        self.alias              = alias          # "Clave 1", "Clave 2", etc.
        self.key                = key            # valor real de la clave
        self.key_preview        = self._preview(key)
        self.total_tokens       = 0              # tokens acumulados histórico
        self.total_requests     = 0              # requests acumuladas histórico
        self.today_tokens       = 0              # tokens del día actual
        self.today_requests     = 0              # requests del día actual
        self.last_used: Optional[str] = None     # ISO timestamp del último uso
        self.last_reset_date: str = str(date.today())  # fecha del último reset diario
        self.errors_today       = 0              # errores 429 u otros hoy
        self.active             = True           # False = desactivada manualmente

    def _preview(self, key: str) -> str:
        """Muestra solo los primeros 8 y últimos 4 caracteres."""
        if not key or len(key) < 12 or "REEMPLAZA" in key:
            return "(no configurada)"
        return f"{key[:8]}...{key[-4:]}"

    @property
    def is_valid(self) -> bool:
        return bool(self.key) and "REEMPLAZA" not in self.key and len(self.key) > 20

    @property
    def requests_remaining_today(self) -> int:
        return max(0, FREE_TIER_RPD - self.today_requests)

    @property
    def blogs_remaining_today(self) -> int:
        """Cuántos blogs completos (3 posts) quedan con esta clave hoy."""
        # Cada blog = 3 requests (1 por post)
        return self.requests_remaining_today // 3

    @property
    def pct_used_today(self) -> float:
        """Porcentaje de requests diarias usadas (0.0 → 1.0)."""
        return min(1.0, self.today_requests / FREE_TIER_RPD)

    @property
    def is_exhausted_today(self) -> bool:
        return self.today_requests >= FREE_TIER_RPD

    @property
    def needs_warning(self) -> bool:
        return self.pct_used_today >= WARN_THRESHOLD_PCT

    def reset_daily_counters(self):
        """Resetea contadores diarios — se llama automáticamente al cambiar de día."""
        logger.info(f"[TokenManager] Reset diario para {self.alias}")
        self.today_tokens    = 0
        self.today_requests  = 0
        self.errors_today    = 0
        self.last_reset_date = str(date.today())

    def record_usage(self, prompt_tokens: int, response_tokens: int):
        """Registra el uso de una llamada exitosa."""
        total = prompt_tokens + response_tokens
        self.total_tokens    += total
        self.today_tokens    += total
        self.total_requests  += 1
        self.today_requests  += 1
        self.last_used        = datetime.now().isoformat()
        logger.debug(
            f"[{self.alias}] +{total} tokens "
            f"(prompt:{prompt_tokens} + resp:{response_tokens}) | "
            f"hoy: {self.today_requests} req / {self.today_tokens} tokens"
        )

    def record_error(self):
        self.errors_today += 1

    def to_dict(self) -> dict:
        return {
            "alias":            self.alias,
            "key_preview":      self.key_preview,
            "total_tokens":     self.total_tokens,
            "total_requests":   self.total_requests,
            "today_tokens":     self.today_tokens,
            "today_requests":   self.today_requests,
            "last_used":        self.last_used,
            "last_reset_date":  self.last_reset_date,
            "errors_today":     self.errors_today,
            "active":           self.active,
        }

    def load_from_dict(self, data: dict):
        self.total_tokens    = data.get("total_tokens", 0)
        self.total_requests  = data.get("total_requests", 0)
        self.today_tokens    = data.get("today_tokens", 0)
        self.today_requests  = data.get("today_requests", 0)
        self.last_used       = data.get("last_used")
        self.last_reset_date = data.get("last_reset_date", str(date.today()))
        self.errors_today    = data.get("errors_today", 0)
        self.active          = data.get("active", True)


# ---------------------------------------------------------------------------
# Gestor del pool de claves
# ---------------------------------------------------------------------------
class TokenManager:
    """
    Gestiona un pool de API Keys de Gemini.

    Uso básico:
        tm = TokenManager.from_env()
        key = tm.get_active_key()        # clave activa actual
        tm.record_usage(1200, 800)       # después de cada llamada exitosa
        tm.rotate()                      # rotar manualmente a la siguiente clave
    """

    def __init__(self, keys: list[str]):
        """
        Args:
            keys: Lista de API keys en orden de preferencia.
        """
        if not keys:
            raise ValueError("Se necesita al menos una API key.")

        self._keys: list[ApiKeyStats] = []
        for i, k in enumerate(keys, start=1):
            stats = ApiKeyStats(alias=f"Clave {i}", key=k)
            self._keys.append(stats)

        self._active_idx: int = 0
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load_state()
        self._check_daily_resets()
        self._select_best_key()

        valid = sum(1 for k in self._keys if k.is_valid)
        logger.info(
            f"[TokenManager] Pool iniciado: {len(self._keys)} claves cargadas, "
            f"{valid} válidas. Activa: {self.active_key.alias}"
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "TokenManager":
        """
        Lee las claves desde variables de entorno.
        Soporta: GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_3
        (también acepta GEMINI_API_KEY como clave 1 si no hay GEMINI_API_KEY_1)
        """
        keys = []
        # Intentar GEMINI_API_KEY_1, _2, _3, ..., _9
        for i in range(1, 10):
            k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
            if k:
                keys.append(k)

        # Fallback: GEMINI_API_KEY (clave única antigua)
        if not keys:
            fallback = os.getenv("GEMINI_API_KEY", "").strip()
            if fallback:
                keys.append(fallback)

        if not keys:
            # Pool vacío con placeholder para no crashear en modo mock
            keys = ["REEMPLAZA_CON_TU_CLAVE"]

        return cls(keys)

    # ------------------------------------------------------------------
    # Acceso a la clave activa
    # ------------------------------------------------------------------

    @property
    def active_key(self) -> ApiKeyStats:
        return self._keys[self._active_idx]

    def get_active_key(self) -> str:
        """Devuelve el valor de la API key activa."""
        return self.active_key.key

    def get_all_keys(self) -> list[ApiKeyStats]:
        return list(self._keys)

    # ------------------------------------------------------------------
    # Registro de uso
    # ------------------------------------------------------------------

    def record_usage(self, prompt_tokens: int, response_tokens: int):
        """Registra tokens consumidos en la clave activa y guarda estado."""
        self.active_key.record_usage(prompt_tokens, response_tokens)
        self._save_state()

    def record_error(self):
        """Registra un error (ej. 429 quota exceeded) en la clave activa."""
        self.active_key.record_error()
        self._save_state()

    # ------------------------------------------------------------------
    # Rotación de claves
    # ------------------------------------------------------------------

    def rotate(self, reason: str = "manual") -> bool:
        """
        Rota a la siguiente clave disponible (no agotada, no desactivada, válida).

        Returns:
            True si se rotó exitosamente, False si no hay más claves disponibles.
        """
        start_idx = self._active_idx
        total     = len(self._keys)

        for _ in range(total - 1):
            next_idx  = (self._active_idx + 1) % total
            candidate = self._keys[next_idx]

            if candidate.is_valid and candidate.active and not candidate.is_exhausted_today:
                self._active_idx = next_idx
                logger.info(
                    f"[TokenManager] Rotación ({reason}): "
                    f"{self._keys[start_idx].alias} → {self.active_key.alias}"
                )
                self._save_state()
                return True

            self._active_idx = next_idx  # seguir buscando

        # No se encontró una clave disponible
        self._active_idx = start_idx
        logger.warning("[TokenManager] No hay claves disponibles para rotar.")
        return False

    def rotate_if_exhausted(self) -> bool:
        """Rota automáticamente si la clave activa está agotada."""
        if self.active_key.is_exhausted_today:
            logger.warning(
                f"[TokenManager] {self.active_key.alias} agotada hoy "
                f"({FREE_TIER_RPD} req). Rotando automáticamente…"
            )
            return self.rotate(reason="auto-exhausted")
        return False

    def set_active_key(self, alias: str) -> bool:
        """Selecciona una clave específica por alias (para cambio manual desde GUI)."""
        for i, k in enumerate(self._keys):
            if k.alias == alias:
                self._active_idx = i
                logger.info(f"[TokenManager] Cambio manual a: {alias}")
                self._save_state()
                return True
        return False

    def deactivate_key(self, alias: str):
        """Desactiva una clave (la excluye de la rotación)."""
        for k in self._keys:
            if k.alias == alias:
                k.active = False
                self._save_state()
                logger.info(f"[TokenManager] {alias} desactivada.")
                break

    # ------------------------------------------------------------------
    # Estadísticas globales del pool
    # ------------------------------------------------------------------

    @property
    def pool_total_tokens(self) -> int:
        return sum(k.total_tokens for k in self._keys)

    @property
    def pool_today_tokens(self) -> int:
        return sum(k.today_tokens for k in self._keys)

    @property
    def pool_today_requests(self) -> int:
        return sum(k.today_requests for k in self._keys)

    @property
    def pool_blogs_remaining_today(self) -> int:
        """Blogs completos que quedan en TODO el pool hoy."""
        return sum(k.blogs_remaining_today for k in self._keys if k.is_valid and k.active)

    @property
    def valid_keys_count(self) -> int:
        return sum(1 for k in self._keys if k.is_valid)

    @property
    def any_key_available(self) -> bool:
        return any(
            k.is_valid and k.active and not k.is_exhausted_today
            for k in self._keys
        )

    def get_summary(self) -> dict:
        """Resumen completo del pool para mostrar en la GUI."""
        return {
            "active_alias":         self.active_key.alias,
            "active_preview":       self.active_key.key_preview,
            "valid_keys":           self.valid_keys_count,
            "total_keys":           len(self._keys),
            "pool_total_tokens":    self.pool_total_tokens,
            "pool_today_tokens":    self.pool_today_tokens,
            "pool_today_requests":  self.pool_today_requests,
            "pool_blogs_remaining": self.pool_blogs_remaining_today,
            "tokens_per_blog_est":  TOKENS_PER_BLOG_EST,
            "free_tier_rpd":        FREE_TIER_RPD,
            "keys":                 [k.to_dict() for k in self._keys],
        }

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _save_state(self):
        """Guarda el estado del pool en JSON."""
        state = {
            "saved_at":   datetime.now().isoformat(),
            "active_idx": self._active_idx,
            "keys":       [k.to_dict() for k in self._keys],
        }
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _load_state(self):
        """Carga el estado guardado si existe."""
        if not _STATE_FILE.exists():
            return
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

            self._active_idx = state.get("active_idx", 0)
            saved_keys       = state.get("keys", [])

            for i, saved in enumerate(saved_keys):
                if i < len(self._keys):
                    self._keys[i].load_from_dict(saved)
                    # Preservar alias del estado guardado si coincide el índice
                    if saved.get("alias"):
                        self._keys[i].alias = saved["alias"]

            logger.debug("[TokenManager] Estado cargado desde disco.")
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f"[TokenManager] No se pudo cargar estado: {exc}")

    def _check_daily_resets(self):
        """Resetea contadores de claves cuya fecha de reset es de ayer o anterior."""
        today = str(date.today())
        for k in self._keys:
            if k.last_reset_date != today:
                k.reset_daily_counters()

    def _select_best_key(self):
        """Al iniciar, selecciona la clave con más requests disponibles hoy."""
        best_idx      = self._active_idx
        best_remaining = self._keys[self._active_idx].requests_remaining_today

        for i, k in enumerate(self._keys):
            if k.is_valid and k.active and k.requests_remaining_today > best_remaining:
                best_remaining = k.requests_remaining_today
                best_idx       = i

        if best_idx != self._active_idx:
            logger.info(
                f"[TokenManager] Clave óptima seleccionada al inicio: "
                f"{self._keys[best_idx].alias} "
                f"({self._keys[best_idx].requests_remaining_today} req restantes)"
            )
            self._active_idx = best_idx
