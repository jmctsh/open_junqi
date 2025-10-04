# -*- coding: utf-8 -*-
"""
WebSocket 客户端封装：在后台线程内运行 asyncio 事件循环，
提供简单的 start()/send() 接口，并通过回调分发事件。
"""
import asyncio
import json
import threading
import websockets

class WSClient:
    def __init__(self, url: str,
                 on_connected=None,
                 on_error=None,
                 on_chat_received=None,
                 on_chat_message=None,
                 on_message=None):
        self.url = url
        self.on_connected = on_connected
        self.on_error = on_error
        self.on_chat_received = on_chat_received
        self.on_chat_message = on_chat_message
        self.on_message = on_message
        self._loop = None
        self._ws = None
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        def runner():
            try:
                loop = asyncio.new_event_loop()
                self._loop = loop
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._connect_and_receive())
            except Exception as e:
                if self.on_error:
                    try:
                        self.on_error(str(e))
                    except Exception:
                        pass
        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()

    async def _connect_and_receive(self):
        try:
            async with websockets.connect(self.url) as ws:
                self._ws = ws
                # 回调：连接成功（不再阻塞等待首条服务器消息）
                if self.on_connected:
                    try:
                        self.on_connected()
                    except Exception:
                        pass
                # 接收循环
                async for msg in ws:
                    try:
                        d = json.loads(msg)
                    except Exception:
                        continue
                    # 通用消息分发（优先）：让上层自行判断 type
                    if self.on_message:
                        try:
                            self.on_message(d)
                        except Exception:
                            pass
                    t = d.get("type")
                    if t == "chat_received":
                        if self.on_chat_received:
                            try:
                                self.on_chat_received(d)
                            except Exception:
                                pass
                    elif t == "chat_message":
                        if self.on_chat_message:
                            try:
                                self.on_chat_message(d)
                            except Exception:
                                pass
                    elif t == "error":
                        if self.on_error:
                            try:
                                self.on_error(d.get("message", "未知错误"))
                            except Exception:
                                pass
        except Exception as e:
            if self.on_error:
                try:
                    self.on_error(str(e))
                except Exception:
                    pass

    def send(self, payload: dict):
        """线程安全地向WS连接发送JSON消息"""
        if not self._loop:
            return
        async def _impl():
            if self._ws:
                await self._ws.send(json.dumps(payload))
        try:
            asyncio.run_coroutine_threadsafe(_impl(), self._loop)
        except Exception:
            # 静默失败，具体错误由 on_error 捕获
            pass

    def stop(self):
        try:
            if self._ws:
                coro = self._ws.close()
                if self._loop:
                    asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception:
            pass