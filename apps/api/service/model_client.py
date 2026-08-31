from config import settings
from openai import AsyncOpenAI

# API Key 只在连接模型时解封，其他模块不会接触敏感值。
client = AsyncOpenAI(
    api_key=settings.deepseek_api_key.get_secret_value(),
    base_url=settings.deepseek_base_url,
)
"""唯一的模型客户端出口，集中处理供应商连接配置。"""
