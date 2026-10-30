"""兼容旧导入。模型入口见 model_client。"""

from src.model_client import OpenAICompatModel, select_model

def build_live_model():
    return OpenAICompatModel.from_env()


__all__ = ["build_live_model", "select_model"]
