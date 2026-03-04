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
        self.default_plan_cpu = int(os.getenv("DEFAULT_PLAN_CPU", "1"))
        self.default_plan_ram_mb = int(os.getenv("DEFAULT_PLAN_RAM_MB", "1024"))
        self.initial_credits = float(os.getenv("INITIAL_CREDITS", "100"))

        self.default_flavor_name = os.getenv("DEFAULT_FLAVOR_NAME", "t2.micro")
        self.default_flavor_cpu = int(os.getenv("DEFAULT_FLAVOR_CPU", "1"))
        self.default_flavor_ram_mb = int(os.getenv("DEFAULT_FLAVOR_RAM_MB", "1024"))
        self.default_flavor_rate = float(os.getenv("DEFAULT_FLAVOR_RATE", "1"))

        self.default_image_code = os.getenv("DEFAULT_IMAGE_CODE", "ubuntu-22.04")
        self.default_image_ref = os.getenv("DEFAULT_IMAGE_REF", "ubuntu:22.04")
        self.default_image_name = os.getenv("DEFAULT_IMAGE_NAME", "Ubuntu 22.04 LTS")

        self.secondary_image_code = os.getenv("SECONDARY_IMAGE_CODE", "alpine-3.20")
        self.secondary_image_ref = os.getenv("SECONDARY_IMAGE_REF", "alpine:3.20")
        self.secondary_image_name = os.getenv("SECONDARY_IMAGE_NAME", "Alpine 3.20")

        self.domain = os.getenv("DOMAIN", "")
        self.deployment_network_name = os.getenv("DEPLOYMENT_NETWORK_NAME", "iaas-backbone")
        self.nginx_container_name = os.getenv("NGINX_CONTAINER_NAME", "iaas-nginx")
        self.deployment_public_path_prefix = os.getenv("DEPLOYMENT_PUBLIC_PATH_PREFIX", "hosted")
        self.deployment_public_scheme = os.getenv("DEPLOYMENT_PUBLIC_SCHEME", "https")

        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        self.gemini_proxy_url = os.getenv("GEMINI_PROXY_URL", "")
        self.gemini_proxy_scheme = os.getenv("GEMINI_PROXY_SCHEME", "http")
        self.gemini_proxy_host = os.getenv("GEMINI_PROXY_HOST", "")
        self.gemini_proxy_port = os.getenv("GEMINI_PROXY_PORT", "")
        self.gemini_proxy_username = os.getenv("GEMINI_PROXY_USERNAME", "")
        self.gemini_proxy_password = os.getenv("GEMINI_PROXY_PASSWORD", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
