#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小可行的 WebSocket 测试客户端：
- 连接到 ws://localhost:8765
- 接收欢迎消息并打印
- 发送 start_game（可选先发送 auto_layout）
- 监听服务器广播（game_started、piece_moved、error、chat_message等）
- 若 5 秒内未出现 AI 行动（piece_moved），则主动发送 ai_move 触发一次AI
"""

import asyncio
import json
import websockets

WS_URL = "ws://localhost:8765"

async def send_json(ws, payload):
    await ws.send(json.dumps(payload))

async def receive_loop(ws, success_event):
    """持续接收并打印消息"""
    try:
        async for msg in ws:
            try:
                data = json.loads(msg)
            except Exception:
                print("[CLIENT] <RAW> ", msg)
                continue
            t = data.get("type")
            if t in {"welcome", "player_joined", "game_started", "start_game_failed", "piece_moved", "error", "chat_message", "turn_skipped"}:
                print("[CLIENT] ", json.dumps(data, ensure_ascii=False))
                # 收到关键事件则判定为成功
                if t in {"game_started", "piece_moved"} and not success_event.is_set():
                    success_event.set()
            else:
                # 降噪打印（仍可查看完整内容）
                print("[CLIENT] ", t, json.dumps(data, ensure_ascii=False))
    except websockets.exceptions.ConnectionClosed:
        print("[CLIENT] 连接已关闭")

async def main():
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"[CLIENT] 第 {attempt}/{max_retries} 次尝试，连接到 {WS_URL} ...")
        try:
            async with websockets.connect(WS_URL) as ws:
                print("[CLIENT] 已连接，等待欢迎消息...")
                # 读取欢迎消息（一次）
                msg = await ws.recv()
                try:
                    data = json.loads(msg)
                except Exception:
                    print("[CLIENT] 欢迎消息解析失败：", msg)
                    data = {}
                print("[CLIENT] 欢迎：", json.dumps(data, ensure_ascii=False))

                # 启动接收打印（后台任务）并设置成功事件
                success_event = asyncio.Event()
                recv_task = asyncio.create_task(receive_loop(ws, success_event))

                # 发送开始游戏
                print("[CLIENT] 发送 start_game ...")
                await send_json(ws, {"type": "start_game"})

                # 等待 5 秒，如果没有出现 AI 行动的广播，则主动尝试触发一次 AI
                ai_triggered = False
                async def wait_for_ai_then_trigger():
                    nonlocal ai_triggered
                    await asyncio.sleep(5)
                    if not ai_triggered and not success_event.is_set():
                        print("[CLIENT] 5秒内未见 AI 行动，主动发送 ai_move 触发一次...")
                        try:
                            await send_json(ws, {"type": "ai_move"})
                            ai_triggered = True
                        except Exception as e:
                            print("[CLIENT] 发送 ai_move 失败：", e)
                asyncio.create_task(wait_for_ai_then_trigger())

                # 观察期：最多等待 20 秒，若收到关键事件则视为成功
                try:
                    await asyncio.wait_for(success_event.wait(), timeout=20)
                    print("[CLIENT] 测试成功：收到关键事件。")
                    # 清理任务并返回成功
                    recv_task.cancel()
                    try:
                        await recv_task
                    except asyncio.CancelledError:
                        pass
                    print("[CLIENT] 测试结束，关闭连接。")
                    return
                except asyncio.TimeoutError:
                    print("[CLIENT] 观察期内未收到关键事件，视为失败。")
                finally:
                    # 统一清理接收任务
                    if not recv_task.done():
                        recv_task.cancel()
                        try:
                            await recv_task
                        except asyncio.CancelledError:
                            pass
        except Exception as e:
            print(f"[CLIENT] 连接或测试过程出错：{e}")

        # 若失败则在下一次重试前稍作等待
        if attempt < max_retries:
            await asyncio.sleep(1)
            print("[CLIENT] 重试中...")

    print("[CLIENT] 所有重试已用尽，测试未成功。")

if __name__ == "__main__":
    asyncio.run(main())