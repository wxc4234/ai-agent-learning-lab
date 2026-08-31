"""集中读取和校验应用运行配置，避免配置散落在路由与服务代码中。"""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 以文件位置定位目录，而不是依赖终端当前所在目录，跨平台启动时更稳定。
APP_DIR = Path(__file__).resolve().parent
# apps/api 的上两层就是仓库根目录，其中保存了不提交 Git 的 .env。
PROJECT_ROOT = APP_DIR.parents[1]


class Settings(BaseSettings):
    """定义配置契约：必填字段缺失时，在服务启动阶段明确失败。"""

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

    # 当前 SQLite 仍使用文件路径；保留 URL 是为后续 SQLAlchemy/PostgreSQL 迁移准备。
    database_url: str = Field(
        default=f"sqlite:///{APP_DIR / 'chat.db'}",
        validation_alias="DATABASE_URL",
    )

    # 统一从根目录 .env 读取，并忽略暂未定义的环境变量，方便逐步扩展配置。
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

# BaseSettings 会在运行时读取 .env；Pylance 无法静态推断该来源，因此仅忽略这条误报。
settings = Settings()  # pyright: ignore[reportCallIssue]
