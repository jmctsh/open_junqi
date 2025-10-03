# -*- coding: utf-8 -*-
"""
Doubao/OpenSpeech 语音合成(TTS) HTTP客户端封装。
- 官方示例: tts_http_demo.py
- 本封装：提供更易用的类接口，支持从环境变量读取配置并返回音频字节或保存到文件。

环境变量（可选，未传入构造参数时读取）：
- DOUBAO_TTS_APPID
- DOUBAO_TTS_ACCESS_TOKEN
- DOUBAO_TTS_CLUSTER
- DOUBAO_TTS_VOICE_TYPE

注意：为保持与官方示例一致，HTTP请求体中的 app.token 字段保留为字符串 "access_token"，鉴权主要依赖 HTTP Header 的 Authorization。
"""
import os
import base64
import json
import uuid
from typing import Optional, Dict, Any

import requests


class DoubaoTTSClient:
    """
    Doubao/OpenSpeech TTS HTTP 客户端封装。
    """

    def __init__(
        self,
        appid: Optional[str] = None,
        access_token: Optional[str] = None,
        secret_key: Optional[str] = None,
        cluster: Optional[str] = None,
        voice_type: Optional[str] = None,
        host: str = "openspeech.bytedance.com",
        encoding: str = "mp3",
        timeout: int = 30,
    ) -> None:
        self.appid = appid or os.environ.get("DOUBAO_TTS_APPID")
        self.access_token = access_token or os.environ.get("DOUBAO_TTS_ACCESS_TOKEN")
        self.secret_key = secret_key or os.environ.get("DOUBAO_TTS_SECRET_KEY")
        self.cluster = cluster or os.environ.get("DOUBAO_TTS_CLUSTER")
        # 默认语音可按需替换，这里给出一个常见中文女声占位示例
        self.voice_type = voice_type or os.environ.get("DOUBAO_TTS_VOICE_TYPE") or "zh_female_shuangzi_mandarin_multinominal"
        self.host = host
        self.api_url = f"https://{host}/api/v1/tts"
        self.encoding = encoding
        self.timeout = timeout

        if not all([self.appid, self.access_token, self.secret_key, self.cluster, self.voice_type]):
            raise ValueError(
                "Missing TTS credentials: please set env DOUBAO_TTS_APPID, DOUBAO_TTS_ACCESS_TOKEN, DOUBAO_TTS_SECRET_KEY, "
                "DOUBAO_TTS_CLUSTER, DOUBAO_TTS_VOICE_TYPE or pass explicitly."
            )

    def _build_payload(
        self,
        text: str,
        *,
        speed_ratio: float = 1.0,
        volume_ratio: float = 1.0,
        pitch_ratio: float = 1.0,
        text_type: str = "plain",
        with_frontend: int = 1,
        frontend_type: str = "unitTson",
        operation: str = "query",
        uid: Optional[str] = None,
        reqid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        构建与官方示例一致的请求体。
        """
        return {
            "app": {
                "appid": self.appid,
                "token": "access_token",  # 保持与官方示例一致
                "cluster": self.cluster,
            },
            "user": {
                "uid": uid or "junqi_ai",
            },
            "audio": {
                "voice_type": self.voice_type,
                "encoding": self.encoding,
                "speed_ratio": speed_ratio,
                "volume_ratio": volume_ratio,
                "pitch_ratio": pitch_ratio,
            },
            "request": {
                "reqid": reqid or str(uuid.uuid4()),
                "text": text,
                "text_type": text_type,
                "operation": operation,
                "with_frontend": with_frontend,
                "frontend_type": frontend_type,
            },
        }

    def synthesize(self, text: str, **kwargs) -> bytes:
        """
        将文本合成为音频字节（默认mp3）。
        返回音频二进制数据；异常时抛出 RuntimeError/ValueError。
        """
        headers = {"Authorization": f"Bearer;{self.access_token}"}
        payload = self._build_payload(text, **kwargs)
        # 与官方示例保持一致：使用 json.dumps 作为请求体
        resp = requests.post(self.api_url, data=json.dumps(payload), headers=headers, timeout=self.timeout)
        try:
            body = resp.json()
        except Exception as e:
            raise RuntimeError(f"TTS response is not JSON: status={resp.status_code}, text={resp.text}") from e

        if "data" in body:
            try:
                return base64.b64decode(body["data"])
            except Exception as e:
                raise RuntimeError("Failed to decode TTS base64 data") from e

        # 返回错误信息（若存在）
        raise RuntimeError(f"TTS failed: body={body}")

    def synthesize_to_file(self, text: str, out_path: str, **kwargs) -> str:
        """
        文本合成并保存到文件，返回文件路径。
        """
        audio_bytes = self.synthesize(text, **kwargs)
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path