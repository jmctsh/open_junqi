#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋游戏主程序
"""

import sys
import os
from PyQt6.QtWidgets import QApplication
from game.game_window import GameWindow

# 加载 .env 文件中的环境变量（如果存在）
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
except Exception:
    pass

def main():
    app = QApplication(sys.argv)
    
    # 设置应用程序信息
    app.setApplicationName("四国军棋")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("JunQi AI")
    
    # 创建并显示游戏窗口
    window = GameWindow()

    # 统一实例：服务器、进程、AI代理
    try:
        from server.game_server import GameServer
        from server.game_process import GameProcess
        from ai.agent import JunqiAgent
        # 创建实例
        server = GameServer()
        # 启动WebSocket服务器（后台线程运行，不阻塞Qt事件循环）
        try:
            server.start_ws_server()
        except Exception:
            pass
        process = GameProcess()
        # 读取 LLM 配置（允许从环境变量读取 ARK_API_KEY）
        api_key = os.environ.get("ARK_API_KEY")
        model = os.environ.get("ARK_MODEL", "doubao-seed-1.6-250615")
        agent = None
        if api_key:
            try:
                agent = JunqiAgent(api_key=api_key, model=model)
            except Exception:
                agent = None
        # 注册AI代理与消费回调：将AI选择的动作交给服务器执行
        if process:
            try:
                if agent:
                    process.attach_agent(agent)
                # 消费回调：解析AI动作并执行到统一规则层
                def _consume_ai_action(action: dict):
                    try:
                        from server.game_server import GameServer as _GS
                    except Exception:
                        pass
                    # 仅在 server 存在时执行
                    srv = server
                    if not srv:
                        return
                    try:
                        srv.apply_move_command(action)
                    except Exception:
                        pass
                process.set_ai_action_consumer(_consume_ai_action)
                # 新增：广播消费者——将进程层的广播事件转发到窗口信号，由窗口播放TTS
                def _consume_broadcast(data: dict):
                    try:
                        window.chat_message_received.emit(data)
                    except Exception:
                        pass
                process.set_broadcast_consumer(_consume_broadcast)
            except Exception:
                pass
        # 将统一实例接线到窗口（完成规则层信号绑定与共享）
        if hasattr(window, "set_managers"):
            try:
                window.set_managers(server, process)
            except Exception:
                pass
    except Exception:
        pass

    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()