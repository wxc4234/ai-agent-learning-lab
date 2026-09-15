"""集中读取和校验应用运行配置，避免配置散落在路由与服务代码中。"""

from decimal import Decimal
from pathlib import Path
from typing import Literal
import re

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 以文件位置定位目录，而不是依赖终端当前所在目录，跨平台启动时更稳定。
APP_DIR = Path(__file__).resolve().parent

# 应用源码位于 apps/api/app；API_DIR 保留运行时数据和应用级配置的稳定位置。
API_DIR = APP_DIR.parent

# apps/api 的上两层就是仓库根目录，其中保存了不提交 Git 的 .env。
PROJECT_ROOT = API_DIR.parents[1]

# 登录签发、身份查询和后续登出共用同一个 Cookie 名称。
LOGIN_COOKIE_NAME = "agent_session"

class Settings(BaseSettings):
    """定义配置契约：必填字段缺失时，在服务启动阶段明确失败。"""

    # 缺省保持账号模式；本地启动配置显式开启，避免部署时意外免认证。
    app_mode: Literal["account", "local"] = Field(default="account", validation_alias="APP_MODE")
    local_runtime_token: SecretStr = Field(default=SecretStr(""), validation_alias="LOCAL_RUNTIME_TOKEN")

    @model_validator(mode="after")
    def validate_local_mode(self):
        if self.app_mode == "local" and not re.fullmatch(r"[a-f0-9]{64}", self.local_runtime_token.get_secret_value()):
            raise ValueError("Local mode requires a 64-character random runtime token")
        return self

    # SecretStr 会在日志或 print 时隐藏真实 Key，降低误泄露风险。
    deepseek_api_key: SecretStr = Field(
        validation_alias="DEEPSEEK_API_KEY",
    )

    # 后续切换模型或服务地址时，只改环境变量，无需修改业务代码。
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )

    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias="DEEPSEEK_MODEL",
    )

    # 单次 Agent 运行的累计 Token 续跑预算，不是模型上下文窗口大小。
    agent_max_total_tokens: int = Field(
        default=8_000,
        ge=1,
        validation_alias="AGENT_MAX_TOTAL_TOKENS",
    )

    # DeepSeek 高峰时段人民币价格

    deepseek_peak_cache_hit_input_cny_per_million: Decimal = Field(
        default=Decimal("0.10"),
        ge=0,
        validation_alias="DEEPSEEK_PEAK_CACHE_HIT_INPUT_CNY_PER_MILLION",
    )

    deepseek_peak_cache_miss_input_cny_per_million: Decimal = Field(
        default=Decimal("3.0"),
        ge=0,
        validation_alias="DEEPSEEK_PEAK_CACHE_MISS_INPUT_CNY_PER_MILLION",
    )

    deepseek_peak_output_cny_per_million: Decimal = Field(
        default=Decimal("9.0"),
        ge=0,
        validation_alias="DEEPSEEK_PEAK_OUTPUT_CNY_PER_MILLION",
    )

    # DeepSeek 空闲时段人民币价格

    deepseek_off_peak_cache_hit_input_cny_per_million: Decimal = Field(
        default=Decimal("0.05"),
        ge=0,
        validation_alias="DEEPSEEK_OFF_PEAK_CACHE_HIT_INPUT_CNY_PER_MILLION",
    )

    deepseek_off_peak_cache_miss_input_cny_per_million: Decimal = Field(
        default=Decimal("1.5"),
        ge=0,
        validation_alias="DEEPSEEK_OFF_PEAK_CACHE_MISS_INPUT_CNY_PER_MILLION",
    )

    deepseek_off_peak_output_cny_per_million: Decimal = Field(
        default=Decimal("4.5"),
        ge=0,
        validation_alias="DEEPSEEK_OFF_PEAK_OUTPUT_CNY_PER_MILLION",
    )

    # 未设置环境变量时使用应用目录的 SQLite 文件，便于首次启动和本地回退。
    database_url: str = Field(
        default=f"sqlite:///{API_DIR / 'chat.db'}",
        validation_alias="DATABASE_URL",
    )

    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        validation_alias="REDIS_URL",
    )

    # 默认面向 HTTPS；本地 HTTP 调试通过环境变量显式关闭
    login_cookie_secure: bool = Field(
        default=True,
        validation_alias="LOGIN_COOKIE_SECURE",

    )

    # 精确匹配 scheme、host、port，不使用通配符或字符串前缀匹配
    login_allowed_origins: tuple[str, ...] = Field(
        default=(
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ),
        validation_alias="LOGIN_ALLOWED_ORIGINS",
    )

    # 统一从根目录 .env 读取，并忽略暂未定义的环境变量，方便逐步扩展配置。
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# BaseSettings 会在运行时读取 .env；Pylance 无法静态推断该来源，因此仅忽略这条误报。
settings = Settings()  # pyright: ignore[reportCallIssue]
