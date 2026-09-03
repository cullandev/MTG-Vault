"""Application configuration.

Every environment variable the application reads is declared here. Nothing else in
the codebase may touch ``os.environ`` -- a single typed settings object keeps the
config surface documented and testable (ARCHITECTURE.md section 7).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- required -----------------------------------------------------------
    secret_key: str = Field(min_length=16)
    lan_hostname: str = "vault.home.arpa"
    lan_ip: str = "127.0.0.1"
    """The machine's LAN address. Caddy issues the certificate for this too, because a
    phone can reach an IP without any name resolution at all (ADR-002)."""
    app_password: str | None = None
    """Seeds the argon2id hash on first run only; ignored once a hash exists."""

    # --- storage ------------------------------------------------------------
    data_dir: Path = Path("/data")
    static_dir: Path | None = None
    backup_dir: Path | None = None
    backup_keep_days: int = 30
    backup_mirror_dir: Path | None = None
    """Second copy of every verified backup -- point it at a NAS mount or second
    drive so the backups do not live on the disk they protect. Optional."""
    image_cache_max_mb: int = 4096

    # --- behaviour ----------------------------------------------------------
    tz: str = "UTC"
    log_level: str = "INFO"
    session_ttl_days: int = 90
    price_move_flag_pct: float = 15.0
    enable_scheduler: bool = True

    auth_disabled: bool = False
    """Development escape hatch: skip the login requirement.

    Off by default and never appropriate on the homelab host. Auth itself stays wired
    into the router (ADR-013) so that turning this back off re-protects every endpoint,
    including ones written while it was on. Startup logs a warning when it is set.
    """

    # --- scryfall -----------------------------------------------------------
    scryfall_user_agent: str = "MTGVault/1.1 (self-hosted)"
    scryfall_min_interval_ms: int = 100
    scryfall_bulk_type: Literal["default_cards", "all_cards", "oracle_cards"] = "default_cards"

    # --- scanning (Phase 2) -------------------------------------------------
    ocr_engine: Literal["tesseract", "paddle"] = "tesseract"
    scan_max_concurrency: int = 2
    scan_accept_score: int = 88
    """rapidfuzz score at or above which a name match is taken as certain."""
    scan_ambiguous_score: int = 82
    """Between this and ``scan_accept_score`` the user gets a picker instead."""
    scan_default_lang: str = "en"
    """Preferred language when a collector number resolves to several printings."""
    scan_debug_frames: int = 0
    """Keep this many recent scans on disk for diagnosis: the frame as uploaded, every
    rectified candidate, and what the pipeline concluded. Off by default; bounded, so
    leaving it on cannot fill the disk."""

    # --- AI (Phase 5) -------------------------------------------------------
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    ai_monthly_token_budget: int = 2_000_000

    # --- external sources ---------------------------------------------------
    enable_edhrec: bool = True
    enable_spellbook: bool = True
    meta_sources_enabled: str = "edhtop16"
    """Comma-separated opt-in list, read through the ``meta_sources`` property
    by the snapshot job and System status (ADR-016)."""
    top_deck_formats: str = "Modern,Standard"
    """Comma-separated 60-card formats whose MTGO Challenge results become playable
    decks, when ``mtgo`` is among the meta sources. Read through
    ``top_deck_format_list``."""

    # --- Forge battle sidecar (ADR-031) -------------------------------------
    enable_forge: bool = False
    """Real AI-vs-AI games through the Forge sidecar. Off unless the operator
    started the ``battles`` compose profile and flipped this on."""
    forge_url: str = "http://forge:8080"
    forge_games_default: int = 3

    @field_validator("lan_hostname")
    @classmethod
    def _reject_mdns_hostname(cls, value: str) -> str:
        """Reject ``.local`` hostnames, which collide with mDNS resolution (ADR-002)."""
        if value.endswith(".local"):
            raise ValueError(
                "LAN_HOSTNAME must not end in .local -- it collides with mDNS and "
                "resolves inconsistently on iOS/Android. Use e.g. vault.home.arpa."
            )
        return value

    @property
    def db_path(self) -> Path:
        """Absolute path of the SQLite database file."""
        return self.data_dir / "mtgvault.db"

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for the SQLite database."""
        return f"sqlite+pysqlite:///{self.db_path.as_posix()}"

    @property
    def backups_path(self) -> Path:
        """Directory nightly backups are written to."""
        return self.backup_dir or (self.data_dir / "backups")

    @property
    def backup_mirror_path(self) -> Path | None:
        """Off-volume mirror for verified backups, or ``None`` when unset."""
        return self.backup_mirror_dir

    @property
    def images_path(self) -> Path:
        """Directory the card image cache lives in."""
        return self.data_dir / "images"

    @property
    def bulk_path(self) -> Path:
        """Directory downloaded Scryfall bulk files live in."""
        return self.data_dir / "bulk"

    @property
    def logs_path(self) -> Path:
        """Directory structured log files are written to."""
        return self.data_dir / "logs"

    @property
    def ai_enabled(self) -> bool:
        """Whether AI features are available at all (ADR-008)."""
        return bool(self.anthropic_api_key)

    @property
    def meta_sources(self) -> tuple[str, ...]:
        """Meta sources the operator has explicitly opted into (ADR-016)."""
        return tuple(s.strip() for s in self.meta_sources_enabled.split(",") if s.strip())

    @property
    def top_deck_format_list(self) -> tuple[str, ...]:
        """The 60-card formats to keep top decks for, capitalised as MTGO names them."""
        return tuple(s.strip().capitalize() for s in self.top_deck_formats.split(",") if s.strip())

    def ensure_directories(self) -> None:
        """Create every directory the application writes to."""
        for path in (
            self.data_dir,
            self.backups_path,
            self.images_path,
            self.bulk_path,
            self.logs_path,
        ):
            path.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]  # values come from the environment
