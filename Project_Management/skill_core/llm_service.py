"""
LLM 服务 - 连接 vLLM 部署的模型（用于项目管理快报分析文字生成）
支持：指标分析文字生成、预警归因分析、建议动作生成

模型端点默认跟随平台主模型环境变量（VLLM_BASE_URL/VLLM_MODEL），
也可由 YAML 配置覆盖。
"""

import os
import logging
import json
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
_DEFAULT_MODEL = os.getenv("VLLM_MODEL", "your-model")
_DEFAULT_API_KEY = os.getenv("VLLM_API_KEY", "")

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


class LLMConfig:
    """LLM 配置"""
    def __init__(self, base_url: str = None, model: str = None,
                 api_key: str = None, temperature: float = 0.3,
                 max_tokens: int = 1024, timeout: int = 180,
                 enabled: bool = True):
        self.base_url = base_url or _DEFAULT_BASE_URL
        self.model = model or _DEFAULT_MODEL
        self.api_key = api_key or _DEFAULT_API_KEY
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.enabled = enabled

    @classmethod
    def from_yaml(cls, config: dict):
        llm_cfg = config.get("llm", {})
        return cls(
            base_url=llm_cfg.get("base_url", _DEFAULT_BASE_URL),
            model=llm_cfg.get("model", _DEFAULT_MODEL),
            api_key=llm_cfg.get("api_key", _DEFAULT_API_KEY),
            temperature=llm_cfg.get("temperature", 0.3),
            max_tokens=llm_cfg.get("max_tokens", 1024),
            timeout=llm_cfg.get("timeout", 180),
            enabled=llm_cfg.get("enabled", True),
        )


class LLMService:
    """
    LLM 服务：封装与 vLLM 的交互
    - chat_completion: 通用对话补全
    - generate_metric_analysis: 生成指标分析文字
    - generate_warning_summary: 生成预警摘要
    """

    def __init__(self, config: LLMConfig = None):
        self.config = config or LLMConfig()
        self._available = None

    def is_available(self) -> bool:
        if not self.config.enabled:
            return False
        if self._available is not None:
            return self._available
        try:
            if HAS_HTTPX:
                with httpx.Client(timeout=10) as client:
                    resp = client.get(f"{self.config.base_url}/models")
                    self._available = resp.status_code == 200
            else:
                self._available = True
        except Exception as e:
            logger.warning(f"LLM 服务不可用: {e}")
            self._available = False
        return self._available

    def _raw_request(self, messages: List[Dict], **kwargs) -> Optional[str]:
        url = f"{self.config.base_url}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "chat_template_kwargs": {"enable_thinking": False},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        if HAS_HTTPX:
            try:
                with httpx.Client(timeout=self.config.timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"].get("content")
            except Exception as e:
                logger.warning(f"LLM 请求失败: {e}")
                return None
        return None

    def chat_completion(self, system_prompt: str, user_prompt: str,
                        **kwargs) -> Optional[str]:
        if not self.is_available():
            return None
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._raw_request(messages, **kwargs)

    def generate_metric_analysis(self, domain: str, metrics: List[Dict[str, Any]],
                                 report_period: str = "") -> Optional[str]:
        """生成某领域的指标分析文字"""
        system = (
            "你是一名 PMO 项目分析师。根据指标数据用两句话概括该领域表现。"
            "第一句说核心数据表现，第二句点出最突出的风险或建议。"
            "不超过80字，不用 Markdown 格式。"
        )
        data_lines = []
        for m in metrics[:8]:
            name = m.get("name", "")
            val = m.get("value")
            level = m.get("warning_level", "")
            line = f"- {name}: {val}"
            if level in ("intervention", "concern"):
                line += f" [{level}]"
            data_lines.append(line)

        user = (
            f"期间：{report_period}，{domain}\n"
            f"数据：\n" + "\n".join(data_lines) + "\n"
            f"请用两句话概括（不超过80字）。"
        )
        return self.chat_completion(system, user, max_tokens=150)

    def generate_warning_summary(self, warnings: List[str]) -> Optional[str]:
        """生成预警摘要"""
        system = (
            "你是一名项目管理顾问。根据预警清单用一句话总结核心风险。"
            "不超过50字，不用 Markdown 格式。"
        )
        user = "预警清单：\n" + "\n".join(f"- {w}" for w in warnings[:10])
        return self.chat_completion(system, user, max_tokens=100)


_llm_service: Optional[LLMService] = None


def get_llm_service(config: LLMConfig = None) -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService(config or LLMConfig())
    return _llm_service
