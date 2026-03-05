import os
from functools import lru_cache


def _env_bool(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "Hackathon IaaS API")
        self.api_prefix = "/api/v1"
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@localhost:5432/iaas",
        )
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-in-production")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.access_token_expire_minutes = int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120")
        )
        self.cookie_samesite = os.getenv("COOKIE_SAMESITE", "lax").strip().lower()
        self.cookie_secure = _env_bool("COOKIE_SECURE", "false")

        self.default_plan_name = os.getenv("DEFAULT_PLAN_NAME", "starter")
        self.default_plan_cpu = int(os.getenv("DEFAULT_PLAN_CPU", "4"))
        self.default_plan_ram_mb = int(os.getenv("DEFAULT_PLAN_RAM_MB", "4096"))
        self.initial_credits = float(os.getenv("INITIAL_CREDITS", "100"))

        self.default_flavor_name = os.getenv("DEFAULT_FLAVOR_NAME", "t2.micro")
        self.default_flavor_cpu = int(os.getenv("DEFAULT_FLAVOR_CPU", "1"))
        self.default_flavor_ram_mb = int(os.getenv("DEFAULT_FLAVOR_RAM_MB", "1024"))
        self.default_flavor_rate = float(os.getenv("DEFAULT_FLAVOR_RATE", "1"))

        self.default_image_code = os.getenv("DEFAULT_IMAGE_CODE", "ubuntu-22.04")
        self.default_image_ref = os.getenv(
            "DEFAULT_IMAGE_REF",
            "ghcr.io/nikitaburinsky/mtc-hackathon-iaas-solution/iaas-ubuntu-ssh:latest",
        )
        self.default_image_name = os.getenv("DEFAULT_IMAGE_NAME", "Ubuntu 22.04 LTS")

        self.secondary_image_code = os.getenv("SECONDARY_IMAGE_CODE", "alpine-3.20")
        self.secondary_image_ref = os.getenv(
            "SECONDARY_IMAGE_REF",
            "ghcr.io/nikitaburinsky/mtc-hackathon-iaas-solution/iaas-alpine-ssh:latest",
        )
        self.secondary_image_name = os.getenv("SECONDARY_IMAGE_NAME", "Alpine 3.20")
        self.postgres_image_code = os.getenv("POSTGRES_IMAGE_CODE", "postgres-16")
        self.postgres_image_ref = os.getenv(
            "POSTGRES_IMAGE_REF",
            "ghcr.io/nikitaburinsky/mtc-hackathon-iaas-solution/iaas-postgres-ssh:latest",
        )
        self.postgres_image_name = os.getenv("POSTGRES_IMAGE_NAME", "Postgres 16")
        self.docker_image_code = os.getenv("DOCKER_IMAGE_CODE", "docker-24")
        self.docker_image_ref = os.getenv(
            "DOCKER_IMAGE_REF",
            "ghcr.io/nikitaburinsky/mtc-hackathon-iaas-solution/iaas-docker-ssh:latest",
        )
        self.docker_image_name = os.getenv("DOCKER_IMAGE_NAME", "Docker 24")

        self.cpu_price_per_vcpu_min = float(os.getenv("CPU_PRICE_PER_VCPU_MIN", "1"))
        self.ram_price_per_gb_min = float(os.getenv("RAM_PRICE_PER_GB_MIN", "5"))

        self.domain = os.getenv("DOMAIN", "")
        self.deployment_network_name = os.getenv(
            "DEPLOYMENT_NETWORK_NAME", "iaas-backbone"
        )
        self.nginx_container_name = os.getenv("NGINX_CONTAINER_NAME", "iaas-nginx")
        self.deployment_public_scheme = os.getenv("DEPLOYMENT_PUBLIC_SCHEME", "http")
        self.deployment_tls_cert_path = os.getenv("DEPLOYMENT_TLS_CERT_PATH", "")
        self.deployment_tls_key_path = os.getenv("DEPLOYMENT_TLS_KEY_PATH", "")

        self.PROXYAPI_API_KEY = os.getenv("PROXYAPI_API_KEY", "")
        self.proxyapi_base_url = os.getenv(
            "PROXYAPI_BASE_URL",
            "https://api.proxyapi.ru/openrouter/v1",
        )
        self.proxyapi_model = os.getenv(
            "PROXYAPI_MODEL",
            "deepseek/deepseek-chat",
        )
        self.proxyapi_timeout_sec = float(os.getenv("PROXYAPI_TIMEOUT_SEC", "120"))

        self.ai_deploy_max_attempts = min(
            3,
            max(1, int(os.getenv("AI_DEPLOY_MAX_ATTEMPTS", "3"))),
        )
        self.ai_deploy_retry_context_max_chars = max(
            20_000,
            int(os.getenv("AI_DEPLOY_RETRY_CONTEXT_MAX_CHARS", "120000")),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
