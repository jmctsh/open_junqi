#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋游戏主程序
"""

import sys
from PyQt6.QtWidgets import QApplication
from game.game_window import GameWindow

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