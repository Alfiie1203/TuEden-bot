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
import re
import threading
import time
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
_LOCK_FILE = Path("logs/token_usage.lock")
_GEMINI_KEY_PATTERN = re.compile(r"^AIza[0-9A-Za-z_-]{20,}$")


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
        return bool(self.key) and "REEMPLAZA" not in self.key and bool(_GEMINI_KEY_PATTERN.match(self.key))

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
        self._recent_requests: list[float] = []
        self._process_lock = threading.Lock()
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
        Soporta cualquier variable GEMINI_API_KEY_<n> ordenada por <n>.
        También acepta GEMINI_API_KEY como clave 1 si no hay numeradas.
        """
        keys: list[str] = []
        indexed_keys: list[tuple[int, str]] = []

        for env_name, env_value in os.environ.items():
            if not env_name.startswith("GEMINI_API_KEY_"):
                continue
            suffix = env_name.removeprefix("GEMINI_API_KEY_")
            if not suffix.isdigit():
                continue
            key_value = env_value.strip()
            if key_value:
                indexed_keys.append((int(suffix), key_value))

        indexed_keys.sort(key=lambda item: item[0])
        keys.extend(value for _, value in indexed_keys)

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
        self._ensure_fresh_day()
        return self.active_key.key

    def get_all_keys(self) -> list[ApiKeyStats]:
        self._ensure_fresh_day()
        return list(self._keys)

    # ------------------------------------------------------------------
    # Registro de uso
    # ------------------------------------------------------------------

    def record_usage(self, prompt_tokens: int, response_tokens: int):
        """Registra tokens consumidos en la clave activa y guarda estado."""
        self._ensure_fresh_day()
        self.active_key.record_usage(prompt_tokens, response_tokens)
        self._save_state()

    def record_error(self):
        """Registra un error (ej. 429 quota exceeded) en la clave activa."""
        self._ensure_fresh_day()
        self.active_key.record_error()
        self._save_state()

    def acquire_request_slot(self) -> None:
        """Bloquea hasta que haya hueco respetando el límite global de RPM."""
        self._ensure_fresh_day()
        while True:
            wait_seconds = 0.0
            with self._process_lock:
                self._acquire_file_lock()
                try:
                    self._load_state()
                    now = time.time()
                    self._prune_recent_requests(now)
                    if len(self._recent_requests) < FREE_TIER_RPM:
                        self._recent_requests.append(now)
                        self._save_state()
                        logger.debug(
                            f"[TokenManager] Slot RPM reservado: {len(self._recent_requests)}/{FREE_TIER_RPM} en los últimos 60s"
                        )
                        return
                    oldest = min(self._recent_requests)
                    wait_seconds = max(1.0, 60 - (now - oldest) + 0.25)
                finally:
                    self._release_file_lock()

            logger.warning(
                f"[TokenManager] Límite interno de {FREE_TIER_RPM} RPM alcanzado. Esperando {wait_seconds:.1f}s…"
            )
            time.sleep(wait_seconds)

    # ------------------------------------------------------------------
    # Rotación de claves
    # ------------------------------------------------------------------

    def rotate(self, reason: str = "manual") -> bool:
        """
        Rota a la siguiente clave disponible (no agotada, no desactivada, válida).

        Returns:
            True si se rotó exitosamente, False si no hay más claves disponibles.
        """
        self._ensure_fresh_day()
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
        self._ensure_fresh_day()
        if self.active_key.is_exhausted_today:
            logger.warning(
                f"[TokenManager] {self.active_key.alias} agotada hoy "
                f"({FREE_TIER_RPD} req). Rotando automáticamente…"
            )
            return self.rotate(reason="auto-exhausted")
        return False

    def set_active_key(self, alias: str) -> bool:
        """Selecciona una clave específica por alias (para cambio manual desde GUI)."""
        self._ensure_fresh_day()
        for i, k in enumerate(self._keys):
            if k.alias == alias:
                if not k.is_valid:
                    logger.warning(f"[TokenManager] No se puede activar {alias}: clave inválida.")
                    return False
                if not k.active:
                    logger.warning(f"[TokenManager] No se puede activar {alias}: clave desactivada.")
                    return False
                self._active_idx = i
                logger.info(f"[TokenManager] Cambio manual a: {alias}")
                self._save_state()
                return True
        return False

    def deactivate_key(self, alias: str):
        """Desactiva una clave (la excluye de la rotación)."""
        self._ensure_fresh_day()
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
        self._ensure_fresh_day()
        return sum(k.total_tokens for k in self._keys)

    @property
    def pool_today_tokens(self) -> int:
        self._ensure_fresh_day()
        return sum(k.today_tokens for k in self._keys)

    @property
    def pool_today_requests(self) -> int:
        self._ensure_fresh_day()
        return sum(k.today_requests for k in self._keys)

    @property
    def pool_blogs_remaining_today(self) -> int:
        """Blogs completos que quedan en TODO el pool hoy."""
        self._ensure_fresh_day()
        return sum(k.blogs_remaining_today for k in self._keys if k.is_valid and k.active)

    @property
    def valid_keys_count(self) -> int:
        self._ensure_fresh_day()
        return sum(1 for k in self._keys if k.is_valid)

    @property
    def available_keys_count(self) -> int:
        self._ensure_fresh_day()
        return sum(
            1
            for k in self._keys
            if k.is_valid and k.active and not k.is_exhausted_today
        )

    @property
    def any_key_available(self) -> bool:
        return self.available_keys_count > 0

    def mark_key_exhausted(self, alias: str | None = None) -> bool:
        """Marca una clave como agotada hoy para evitar reutilizarla tras un 429."""
        self._ensure_fresh_day()
        target_alias = alias or self.active_key.alias
        for key_stats in self._keys:
            if key_stats.alias != target_alias:
                continue
            key_stats.today_requests = FREE_TIER_RPD
            key_stats.last_used = datetime.now().isoformat()
            self._save_state()
            logger.warning(f"[TokenManager] {target_alias} marcada como agotada hoy.")
            return True
        return False

    def get_summary(self) -> dict:
        """Resumen completo del pool para mostrar en la GUI."""
        self._ensure_fresh_day()
        return {
            "active_alias":         self.active_key.alias,
            "active_preview":       self.active_key.key_preview,
            "valid_keys":           self.valid_keys_count,
            "total_keys":           len(self._keys),
            "pool_total_tokens":    self.pool_total_tokens,
            "pool_today_tokens":    self.pool_today_tokens,
            "pool_today_requests":  self.pool_today_requests,
            "available_keys":       self.available_keys_count,
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
            "recent_requests": self._recent_requests,
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

            saved_keys = state.get("keys", [])
            self._recent_requests = [
                float(ts)
                for ts in state.get("recent_requests", [])
                if isinstance(ts, (int, float))
            ]
            self._prune_recent_requests(time.time())
            saved_by_preview = {
                saved.get("key_preview"): saved
                for saved in saved_keys
                if saved.get("key_preview")
            }

            for key_stats in self._keys:
                saved = saved_by_preview.get(key_stats.key_preview)
                if saved:
                    key_stats.load_from_dict(saved)

            saved_active_idx = state.get("active_idx", 0)
            if 0 <= saved_active_idx < len(saved_keys):
                saved_active_preview = (saved_keys[saved_active_idx] or {}).get("key_preview")
                if saved_active_preview:
                    for i, key_stats in enumerate(self._keys):
                        if key_stats.key_preview == saved_active_preview:
                            self._active_idx = i
                            break

            logger.debug("[TokenManager] Estado cargado desde disco.")
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f"[TokenManager] No se pudo cargar estado: {exc}")

    def _check_daily_resets(self) -> bool:
        """Resetea contadores de claves cuya fecha de reset es de ayer o anterior."""
        today = str(date.today())
        changed = False
        for k in self._keys:
            if k.last_reset_date != today:
                k.reset_daily_counters()
                changed = True
        return changed

    def _ensure_fresh_day(self) -> None:
        """Aplica reset diario aunque el proceso lleve días en ejecución."""
        with self._process_lock:
            if self._check_daily_resets():
                self._save_state()

    def _prune_recent_requests(self, now: float) -> None:
        self._recent_requests = [ts for ts in self._recent_requests if (now - ts) < 60]

    def _acquire_file_lock(self) -> None:
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
                self._lock_fd = fd
                return
            except FileExistsError:
                if time.time() >= deadline:
                    try:
                        _LOCK_FILE.unlink(missing_ok=True)
                    except Exception:
                        pass
                    deadline = time.time() + 10
                time.sleep(0.05)

    def _release_file_lock(self) -> None:
        fd = getattr(self, "_lock_fd", None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self._lock_fd = None
        try:
            _LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass

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
