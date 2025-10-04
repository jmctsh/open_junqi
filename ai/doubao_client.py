import os
import logging
import json
from typing import List, Dict, Optional

from volcenginesdkarkruntime import Ark

logger = logging.getLogger(__name__)


class DoubaoClient:
    """
    Doubao Ark SDK 封装：非流式调用，默认禁用深度思考。

    使用环境变量 ARK_API_KEY 读取密钥，或在构造函数中显式传入。
    """

    def __init__(self, api_key: Optional[str] = None, timeout: int = 1800,
                 model: str = "doubao-seed-1.6-250615") -> None:
        self.api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self.api_key:
            raise ValueError("Missing ARK_API_KEY. Please set env or pass api_key explicitly.")
        self.model = model
        self._client = Ark(api_key=self.api_key, timeout=timeout)
        logger.info(f"[DEBUG] DoubaoClient 初始化完成，模型: {self.model}")
        # --- LLM 交互日志文件配置 ---
        self.llm_logger = logging.getLogger("junqi_ai.llm")
        self.llm_logger.setLevel(logging.INFO)
        logs_dir = os.path.join(os.getcwd(), "logs")
        try:
            os.makedirs(logs_dir, exist_ok=True)
        except Exception:
            pass
        log_path = os.path.join(logs_dir, "llm_chat.log")
        # 防止重复添加多个文件处理器
        need_handler = True
        for h in self.llm_logger.handlers:
            if isinstance(h, logging.FileHandler):
                try:
                    if getattr(h, "baseFilename", None) == log_path:
                        need_handler = False
                        break
                except Exception:
                    continue
        if need_handler:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(logging.Formatter('%(asctime)s\t%(message)s'))
            self.llm_logger.addHandler(fh)
        # 控制台输出：截断完整提示词事件，避免污染终端（文件日志保持完整）
        # 停止向根logger传播，防止重复输出
        self.llm_logger.propagate = False
        # 若未配置控制台处理器，则添加一个带截断过滤器的处理器
        has_stream = any(isinstance(h, logging.StreamHandler) for h in self.llm_logger.handlers)
        if not has_stream:
            class _LlmTruncatingFilter(logging.Filter):
                def __init__(self, max_chars: int = 50) -> None:
                    super().__init__()
                    self.max_chars = max_chars
                def filter(self, record: logging.LogRecord) -> bool:
                    try:
                        msg = record.getMessage()
                        # 仅截断包含完整提示词/完整响应的事件
                        if '"event": "llm_request_full"' in msg or '"event": "llm_response_full"' in msg:
                            truncated = msg[:self.max_chars]
                            record.msg = truncated
                            record.args = None
                    except Exception:
                        pass
                    return True
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.addFilter(_LlmTruncatingFilter(max_chars=50))
            ch.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
            self.llm_logger.addHandler(ch)
        self._llm_log_path = log_path

    def chat(self, messages: List[Dict[str, str]], thinking_type: str = "disabled", temperature: float = 0.2, top_p: float = 0.9) -> str:
        """
        发起一次非流式对话请求，并返回模型文本响应（assistant content）。
        messages 形如：[{"role": "user", "content": "..."}, {"role": "system", "content": "..."}]
        """
        # 记录请求摘要（裁剪敏感prompt）
        try:
            roles = []
            for m in messages:
                r = m.get("role", "unknown")
                roles.append(r)
            self.llm_logger.info(json.dumps({
                "event": "llm_request",
                "model": self.model,
                "thinking_type": thinking_type,
                "temperature": temperature,
                "top_p": top_p,
                "message_count": len(messages),
                "roles": roles,
            }, ensure_ascii=False))
            # 新增：记录完整提示词（保持log完整，debug仍为折叠）
            try:
                self.llm_logger.info(json.dumps({
                    "event": "llm_request_full",
                    "model": self.model,
                    "messages": messages,
                }, ensure_ascii=False))
            except Exception:
                pass
        except Exception:
            logger.warning("[DEBUG] 写入 LLM 请求日志失败（忽略，不影响调用）")
        logger.info(f"[DEBUG] 发起Ark chat，消息数: {len(messages)}，模型: {self.model}")
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                thinking={"type": thinking_type},
                temperature=temperature,
                top_p=top_p,
            )
        except Exception as e:
            # 记录错误事件（摘要）
            try:
                self.llm_logger.info(json.dumps({
                    "event": "llm_error",
                    "model": self.model,
                    "error": str(e),
                }, ensure_ascii=False))
            except Exception:
                pass
            logger.error(f"[DEBUG] Ark 调用失败: {e}")
            raise
        # 兼容 dict / 属性式响应
        try:
            content = resp["choices"][0]["message"]["content"]
        except Exception:
            try:
                content = resp.choices[0].message.content
            except Exception as e:
                # 记录错误响应摘要（不写原始内容）
                try:
                    self.llm_logger.info(json.dumps({
                        "event": "llm_error",
                        "model": self.model,
                        "error": "Unexpected response format",
                        "response_type": type(resp).__name__,
                    }, ensure_ascii=False))
                except Exception:
                    pass
                # 安全日志：不输出完整响应对象，避免泄露内容
                resp_type = type(resp).__name__
                safe_info = {"response_type": resp_type}
                if isinstance(resp, dict):
                    try:
                        safe_info["keys"] = list(resp.keys())
                    except Exception:
                        pass
                logger.error(f"[DEBUG] Ark 响应格式异常，摘要: {safe_info}")
                raise RuntimeError(f"Unexpected response format (type={resp_type})") from e
        # 记录响应摘要（仅长度）
        try:
            # 新增：记录完整LLM文本响应
            self.llm_logger.info(json.dumps({
                "event": "llm_response_full",
                "model": self.model,
                "content": content,
            }, ensure_ascii=False))
            # 保留原有摘要日志
            self.llm_logger.info(json.dumps({
                "event": "llm_response",
                "model": self.model,
                "length": len(content),
            }, ensure_ascii=False))
        except Exception:
            logger.warning("[DEBUG] 写入 LLM 响应日志失败（忽略）")
        logger.info(f"[DEBUG] Ark 返回长度: {len(content)}")
        return content

    def ask(self, prompt: str, system: Optional[str] = None) -> str:
        """
        便捷调用：仅提供 user prompt，可选 system 指令，返回文本结果。
        默认禁用深度思考。
        """
        msgs: List[Dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return self.chat(msgs, thinking_type="disabled")