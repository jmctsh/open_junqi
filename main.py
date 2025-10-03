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
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()