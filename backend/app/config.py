"""Environment-based configuration. Every business rule threshold lives here.

Values come from environment variables (or a .env file). Secrets are never hard-coded.
"""
from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class UnknownValueAction(StrEnum):
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class HybridOverCapAction(StrEnum):
    """What to do when a hybrid tender's *service component* exceeds SERVICE_MAX_VALUE_INR."""
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class AdjacentAction(StrEnum):
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SUPPRESS = "SUPPRESS"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- runtime ---
    ENVIRONMENT: str = "development"  # development | production
    DATABASE_URL: str = "sqlite:///./tender_intel.db"
    SECRET_KEY: str = Field(default="", description="JWT signing key. Required in production.")
    FERNET_KEY: str = Field(default="", description="Key for encrypting stored portal secrets.")
    DASHBOARD_BASE_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000"
    DOCUMENT_STORAGE_DIR: str = "./data/documents"

    # --- commercial value rules (section 5 / 20) ---
    SERVICE_MAX_VALUE_INR: int = 3_000_000
    SERVICE_VALUE_RULE_ENABLED: bool = True
    SERVICE_UNKNOWN_VALUE_ACTION: UnknownValueAction = UnknownValueAction.MANUAL_REVIEW
    OEM_VALUE_LIMIT_ENABLED: bool = False
    OEM_MAX_VALUE_INR: int = 0  # only used when OEM_VALUE_LIMIT_ENABLED
    HYBRID_REVIEW_ENABLED: bool = True
    HYBRID_SERVICE_OVER_CAP_ACTION: HybridOverCapAction = HybridOverCapAction.MANUAL_REVIEW
    # When a service tender states no value, its EMD implies a range (GFR: EMD = 2–5% of value).
    SERVICE_EMD_OVER_CAP_ACTION: str = "MANUAL_REVIEW"  # MANUAL_REVIEW | REJECT (whole range above the cap)
    SERVICE_EMD_WITHIN_CAP_ACTION: str = "ACCEPT"  # ACCEPT | MANUAL_REVIEW (whole range within the cap)
    # Days of bid preparation each opportunity type comfortably needs (timeline score)
    LEAD_DAYS_SERVICE: int = 7
    LEAD_DAYS_OEM: int = 14
    LEAD_DAYS_HYBRID: int = 21

    # --- relevance / scoring (section 14 / 15 / 20) ---
    MIN_RELEVANCE_SCORE: int = 40
    HOT_SCORE_THRESHOLD: int = 90
    HIGH_SCORE_THRESHOLD: int = 75
    MEDIUM_SCORE_THRESHOLD: int = 60
    LOW_SCORE_THRESHOLD: int = 40
    ADJACENT_MATCH_ACTION: AdjacentAction = AdjacentAction.MANUAL_REVIEW
    STRONG_MATCH_MIN_CONFIDENCE: int = 70
    WEIGHT_CAPABILITY: float = 30
    WEIGHT_TECHNICAL: float = 20
    WEIGHT_OEM: float = 20
    WEIGHT_COMMERCIAL: float = 10
    WEIGHT_ELIGIBILITY: float = 10
    WEIGHT_TIMELINE: float = 5
    WEIGHT_STRATEGIC: float = 5
    # Comma-separated organisation keywords considered strategic (BFSI regulators, defence, CERTs...).
    STRATEGIC_ORG_KEYWORDS: str = "bank,reserve bank,insurance,irdai,sebi,nabard,nhb,uidai,cert-in,police,defence,ministry,nic,ncrb,stock exchange"

    # --- semantic matching (Phase 4); thresholds tuned with `python -m app.evaluation.run` ---
    SEMANTIC_MATCHING_ENABLED: bool = True
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_CACHE_DIR: str = ""  # where the model is cached; baked into the Docker image
    SEMANTIC_STRONG_THRESHOLD: float = 0.80
    SEMANTIC_ADJACENT_THRESHOLD: float = 0.78
    SEMANTIC_NEGATIVE_MARGIN: float = 0.04
    SEMANTIC_MIN_WORDS: int = 4

    # --- LLM (section 22) ---
    LLM_ENABLED: bool = True
    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = "claude-opus-5-5"
    LLM_EFFORT: str = "medium"
    LLM_MAX_CONTEXT_CHARS: int = 180_000
    LLM_FALLBACKS_ENABLED: bool = True

    # --- discovery / scheduling (section 8) ---
    DEFAULT_DISCOVERY_INTERVAL_MINUTES: int = 45
    DISCOVERY_MAX_PAGES: int = 30
    DISCOVERY_STOP_AFTER_KNOWN_PAGES: int = 2
    PORTAL_REQUEST_DELAY_SECONDS: float = 2.0
    HTTP_USER_AGENT: str = "TenderIntelBot/1.0 (+contact: set HTTP_USER_AGENT)"
    WORKER_POLL_SECONDS: float = 5.0
    JOB_MAX_ATTEMPTS: int = 3

    # --- documents (section 9) ---
    MAX_DOCUMENT_BYTES: int = 50 * 1024 * 1024
    MAX_ZIP_TOTAL_BYTES: int = 200 * 1024 * 1024
    MAX_ZIP_MEMBERS: int = 200
    OCR_ENABLED: bool = True
    OCR_LANG: str = "eng"
    CLAMAV_HOST: str = ""  # optional clamd host for malware scanning
    CLAMAV_PORT: int = 3310

    # --- email (section 17) ---
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True
    ALERT_FROM: str = "tender-intel@localhost"
    ALERT_RECIPIENTS: str = ""  # comma-separated
    HIGH_ALERT_MODE: str = "immediate"  # immediate | digest
    ALERT_ON_MANUAL_REVIEW: bool = True  # email HOT/HIGH tenders even when they await manual review
    DIGEST_HOUR_IST: int = 9

    # --- auth (section 23) ---
    ACCESS_TOKEN_MINUTES: int = 480
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10
    API_RATE_LIMIT_PER_MINUTE: int = 300
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    BOOTSTRAP_ADMIN_PASSWORD: str = ""

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def alert_recipients(self) -> list[str]:
        return [e.strip() for e in self.ALERT_RECIPIENTS.split(",") if e.strip()]

    @property
    def strategic_keywords(self) -> list[str]:
        return [k.strip().lower() for k in self.STRATEGIC_ORG_KEYWORDS.split(",") if k.strip()]

    @property
    def llm_available(self) -> bool:
        return self.LLM_ENABLED and bool(self.ANTHROPIC_API_KEY)

    def validate_for_production(self) -> None:
        problems = []
        if len(self.SECRET_KEY) < 32:
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if self.DATABASE_URL.startswith("sqlite"):
            problems.append("DATABASE_URL must point to PostgreSQL in production")
        if problems:
            raise RuntimeError("Invalid production configuration: " + "; ".join(problems))

    def public_view(self) -> dict:
        """Non-secret settings, safe to show on the dashboard."""
        hidden = {"SECRET_KEY", "FERNET_KEY", "ANTHROPIC_API_KEY", "SMTP_PASSWORD", "DATABASE_URL",
                  "BOOTSTRAP_ADMIN_PASSWORD", "SMTP_USERNAME"}
        return {k: (str(v) if isinstance(v, StrEnum) else v)
                for k, v in self.model_dump().items() if k not in hidden}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production:
        s.validate_for_production()
    return s
