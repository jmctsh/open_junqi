#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋游戏主程序
"""

import sys
import os
import logging
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
    # 初始化日志：采用简单控制台输出，保持只打印到终端，不写文件
    try:
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        has_stream = any(isinstance(h, logging.StreamHandler) for h in root.handlers)
        if not has_stream:
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
            root.addHandler(ch)
        # 关键子模块采用 INFO，避免干扰
        logging.getLogger("server.game_process").setLevel(logging.INFO)
        logging.getLogger("server.strategies.search").setLevel(logging.INFO)
    except Exception:
        pass

    app = QApplication(sys.argv)
    
    # 设置应用程序信息
    app.setApplicationName("四国军棋")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("JunQi AI")
    
    # 创建并显示游戏窗口
    window = GameWindow()

    # 本地模式：仅使用进程层与AI代理，无服务器/WS
    try:
        from server.game_process import GameProcess
        from ai.agent import JunqiAgent
        from game.board import Position
        process = GameProcess()
        # 读取 LLM 配置：支持全局与按人格（player1/2/3）密钥
        model = os.environ.get("ARK_MODEL", "doubao-seed-1.6-250615")
        agents_map = {}
        # 人格密钥优先（.env.example 提供 ARK_API_KEY_P1/P2/P3）
        try:
            p1 = os.environ.get("ARK_API_KEY_P1")
            p2 = os.environ.get("ARK_API_KEY_P2")
            p3 = os.environ.get("ARK_API_KEY_P3")
            if p1:
                try:
                    agents_map["player1"] = JunqiAgent(api_key=p1, model=model)
                except Exception:
                    pass
            if p2:
                try:
                    agents_map["player2"] = JunqiAgent(api_key=p2, model=model)
                except Exception:
                    pass
            if p3:
                try:
                    agents_map["player3"] = JunqiAgent(api_key=p3, model=model)
                except Exception:
                    pass
        except Exception:
            agents_map = {}
        # 若未提供人格密钥，则尝试使用全局 ARK_API_KEY
        global_agent = None
        if not agents_map:
            api_key = os.environ.get("ARK_API_KEY")
            if api_key:
                try:
                    global_agent = JunqiAgent(api_key=api_key, model=model)
                except Exception:
                    global_agent = None
        # 注册AI代理与消费回调：直接作用于规则层
        if process:
            try:
                if agents_map:
                    process.attach_persona_agents(agents_map)
                elif global_agent:
                    process.attach_agent(global_agent)
                # 解析AI动作，直接调用规则层 move_piece
                def _consume_ai_action(action: dict):
                    try:
                        move = action.get("move") if isinstance(action, dict) else None
                        if isinstance(move, dict):
                            src = move.get("from") or {}
                            dst = move.get("to") or {}
                        else:
                            src = action.get("from") or {}
                            dst = action.get("to") or {}
                        src_pos = Position(row=int(src.get("row")), col=int(src.get("col")))
                        dst_pos = Position(row=int(dst.get("row")), col=int(dst.get("col")))
                    except Exception:
                        return
                    try:
                        window.game_logic.move_piece(src_pos, dst_pos)
                    except Exception:
                        pass
                process.set_ai_action_consumer(_consume_ai_action)
                # 广播消费者：转发到窗口信号，由窗口播放TTS与刷新
                def _consume_broadcast(data: dict):
                    try:
                        window.chat_message_received.emit(data)
                    except Exception:
                        pass
                process.set_broadcast_consumer(_consume_broadcast)
            except Exception:
                pass
        # 进程层与窗口直接接线（无服务器）
        if hasattr(window, "set_managers"):
            try:
                window.set_managers(None, process)
            except Exception:
                pass
    except Exception:
        pass

    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()