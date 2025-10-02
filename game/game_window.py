#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋游戏主窗口
"""

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QStatusBar, QMessageBox, QMenu, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QSize, QTimer
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QMouseEvent, QCursor
from .game_logic import GameLogic, GameState
from .board import Position, CellType
from .piece import Player

class BoardWidget(QWidget):
    """棋盘渲染组件"""
    cell_clicked = pyqtSignal(int, int, int)  # row, col, button (1=left, 2=right)
    cell_dragged = pyqtSignal(int, int, int, int)  # from_row, from_col, to_row, to_col
    
    def __init__(self, game_logic: GameLogic):
        super().__init__()
        self.game_logic = game_logic
        self.board = game_logic.board
        self.selected_position = None
        self.valid_moves = []
        self._drag_start = None
        
        # 棋盘显示参数 - 适配17x17布局，格子分开显示
        self.cell_size = 30  # 格子大小
        self.cell_spacing = 10  # 格子间距
        self.margin = 60
        
        # 计算总尺寸
        self.total_width = self.board.cols * (self.cell_size + self.cell_spacing) + 2 * self.margin
        self.total_height = self.board.rows * (self.cell_size + self.cell_spacing) + 2 * self.margin
        self.setMinimumSize(self.total_width, self.total_height)

    def sizeHint(self) -> QSize:
        """为滚动区域提供内容的理想尺寸，以便正确居中"""
        return QSize(self.total_width, self.total_height)
    
    def paintEvent(self, event):
        """绘制棋盘"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 绘制棋盘网格
        self._draw_grid(painter)
        
        # 绘制特殊格子（铁路、行营、大本营）
        self._draw_special_cells(painter)
        
        # 绘制棋子
        self._draw_pieces(painter)
        
        # 绘制选中状态和可移动位置
        self._draw_selection(painter)
        
        # 绘制玩家标记
        self._draw_marks(painter)

        # 绘制回合指示标记
        self._draw_turn_indicator(painter)
    
    def _draw_grid(self, painter):
        """绘制基本网格和连接线"""
        # 绘制格子和连接线
        for pos, cell in self.board.cells.items():
            x = self.margin + pos.col * (self.cell_size + self.cell_spacing)
            y = self.margin + pos.row * (self.cell_size + self.cell_spacing)
            
            # 绘制格子
            rect = QRect(x, y, self.cell_size, self.cell_size)
            
            # 根据格子类型设置颜色
            if cell.cell_type == CellType.RAILWAY:
                painter.fillRect(rect, QColor(173, 216, 230, 150))  # 浅蓝色
                painter.setPen(QPen(QColor(0, 0, 255), 2))
            elif cell.cell_type == CellType.CAMP:
                painter.fillRect(rect, QColor(255, 255, 0, 150))  # 黄色
                painter.setPen(QPen(QColor(255, 165, 0), 2))
            elif cell.cell_type == CellType.HEADQUARTERS:
                painter.fillRect(rect, QColor(255, 0, 0, 150))  # 红色
                painter.setPen(QPen(QColor(139, 0, 0), 3))
            else:
                # 根据玩家区域设置不同颜色
                if cell.player_area:
                    player_color = self._get_player_area_color(cell.player_area)
                    painter.fillRect(rect, player_color)
                else:
                    painter.fillRect(rect, QColor(240, 240, 240))  # 中央区域浅灰色
                painter.setPen(QPen(QColor(100, 100, 100), 1))
            
            painter.drawRect(rect)
            
            # 临时显示坐标信息（开发调试用）
            # 注意：开发模式由 GameLogic.testing_mode 控制
            # 原行为为始终显示；现改为仅当 testing_mode=True 时显示
            if self.game_logic.testing_mode:
                self._draw_coordinate_info(painter, x, y, pos, cell)
            
            # 绘制连接线
            center_x = x + self.cell_size // 2
            center_y = y + self.cell_size // 2
            
            # 获取相邻位置
            adjacent_positions = self.board.get_adjacent_positions(pos)
            for adj_pos in adjacent_positions:
                if adj_pos in self.board.cells:
                    adj_x = self.margin + adj_pos.col * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                    adj_y = self.margin + adj_pos.row * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                    
                    # 只绘制一次连线（避免重复）
                    if pos.row < adj_pos.row or (pos.row == adj_pos.row and pos.col < adj_pos.col):
                        # 铁路连接用加粗线
                        if (cell.cell_type == CellType.RAILWAY and 
                            self.board.cells[adj_pos].cell_type == CellType.RAILWAY):
                            painter.setPen(QPen(QColor(0, 0, 255), 3))
                        else:
                            painter.setPen(QPen(QColor(100, 100, 100), 1))
                        
                        painter.drawLine(center_x, center_y, adj_x, adj_y)
        
        # 绘制玩家棋盘与中央九宫格的特殊连接线
        self._draw_special_connections(painter)
    
    def _draw_coordinate_info(self, painter, x, y, pos, cell):
        """临时显示坐标信息（开发调试用）"""
        # 若未开启开发模式，则不绘制坐标
        if not self.game_logic.testing_mode:
            return
        
        # 设置字体
        font = QFont("Arial", 6)
        painter.setFont(font)
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        
        # 计算本地坐标
        if cell.player_area is None:
            # 中央九宫格
            if 6 <= pos.row <= 10 and 6 <= pos.col <= 10:
                center_row = (pos.row - 6) // 2 + 1
                center_col = (pos.col - 6) // 2 + 1
                coord_text = f"中{center_row},{center_col}"
            else:
                coord_text = f"({pos.row},{pos.col})"
        else:
            # 玩家区域
            if cell.player_area == Player.PLAYER1:  # 南方
                local_row = pos.row - 11 + 1
                local_col = pos.col - 6 + 1
                coord_text = f"南{local_row},{local_col}"
            elif cell.player_area == Player.PLAYER2:  # 西方
                # 西方玩家需要90度逆时针旋转：row,col -> (6-col+1, row-6+1)
                global_row_in_area = pos.row - 6  # 0-4
                global_col_in_area = pos.col - 0  # 0-5
                # 旋转变换：(row,col) -> (5-col, row)
                local_row = 5 - global_col_in_area + 1  # 转换为1-6
                local_col = global_row_in_area + 1      # 转换为1-5
                coord_text = f"西{local_row},{local_col}"
            elif cell.player_area == Player.PLAYER3:  # 北方
                # 北方玩家需要180度旋转：row,col -> (6-row, 5-col+1)
                global_row_in_area = pos.row - 0  # 0-5
                global_col_in_area = pos.col - 6  # 0-4
                # 旋转变换：(row,col) -> (5-row, 4-col)
                local_row = 5 - global_row_in_area + 1  # 转换为1-6
                local_col = 4 - global_col_in_area + 1  # 转换为1-5
                coord_text = f"北{local_row},{local_col}"
            elif cell.player_area == Player.PLAYER4:  # 东方
                # 东方玩家需要90度顺时针旋转：row,col -> (col+1, 5-row+1)
                global_row_in_area = pos.row - 6  # 0-4
                global_col_in_area = pos.col - 11  # 0-5
                # 旋转变换：(row,col) -> (col, 4-row)
                local_row = global_col_in_area + 1      # 转换为1-6
                local_col = 4 - global_row_in_area + 1  # 转换为1-5
                coord_text = f"东{local_row},{local_col}"
            else:
                coord_text = f"({pos.row},{pos.col})"
        
        # 在格子中央绘制坐标文本
        text_rect = QRect(x + 2, y + 2, self.cell_size - 4, self.cell_size - 4)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, coord_text)
    
    def _draw_special_connections(self, painter):
        """绘制玩家棋盘与中央九宫格的特殊连接线（只有1、3、5列）"""
        # 中央九宫格的位置
        center_positions = [
            Position(6, 6), Position(6, 8), Position(6, 10),  # 上排
            Position(8, 6), Position(8, 8), Position(8, 10),  # 中排
            Position(10, 6), Position(10, 8), Position(10, 10)  # 下排
        ]
        
        # 设置连接线样式
        painter.setPen(QPen(QColor(0, 0, 255), 3))  # 蓝色粗线表示铁路连接
        
        # 南方玩家（PLAYER1）的连接：第一排（行11）的1、3、5列连接到九宫格上排
        south_connections = [
            (Position(11, 6), Position(10, 6)),   # 南方1列 -> 九宫格左上
            (Position(11, 8), Position(10, 8)),   # 南方3列 -> 九宫格上中
            (Position(11, 10), Position(10, 10))  # 南方5列 -> 九宫格右上
        ]
        
        # 北方玩家（PLAYER3）的连接：第一排（行5）的1、3、5列连接到九宫格下排
        north_connections = [
            (Position(5, 6), Position(6, 6)),     # 北方1列 -> 九宫格左下
            (Position(5, 8), Position(6, 8)),     # 北方3列 -> 九宫格下中
            (Position(5, 10), Position(6, 10))    # 北方5列 -> 九宫格右下
        ]
        
        # 西方玩家（PLAYER2）的连接：第一排（列5）的1、3、5行连接到九宫格左排
        west_connections = [
            (Position(6, 5), Position(6, 6)),     # 西方1行 -> 九宫格左上
            (Position(8, 5), Position(8, 6)),     # 西方3行 -> 九宫格左中
            (Position(10, 5), Position(10, 6))    # 西方5行 -> 九宫格左下
        ]
        
        # 东方玩家（PLAYER4）的连接：第一排（列10）的1、3、5行连接到九宫格右排
        east_connections = [
            (Position(6, 11), Position(6, 10)),   # 东方1行 -> 九宫格右上
            (Position(8, 11), Position(8, 10)),   # 东方3行 -> 九宫格右中
            (Position(10, 11), Position(10, 10))  # 东方5行 -> 九宫格右下
        ]
        
        # 额外的跨玩家粗线铁路连接
        # 角点跨玩家铁路直连（按界面坐标映射）
        extra_player_links = [
            # 西1,1 和 北1,5 -> (6,5) <-> (5,6)
            (Position(6, 5), Position(5, 6)),
            # 西1,5 和 南1,1 -> (10,5) <-> (11,6)
            (Position(10, 5), Position(11, 6)),
            # 南1,5 和 东1,1 -> (11,10) <-> (10,11)
            (Position(11, 10), Position(10, 11)),
            # 东1,5 和 北1,1 -> (6,11) <-> (5,10)
            (Position(6, 11), Position(5, 10)),
        ]

        # 绘制所有连接线
        all_connections = south_connections + north_connections + west_connections + east_connections + extra_player_links
        
        for from_pos, to_pos in all_connections:
            # 检查两个位置是否都存在
            if from_pos in self.board.cells and to_pos in self.board.cells:
                from_x = self.margin + from_pos.col * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                from_y = self.margin + from_pos.row * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                to_x = self.margin + to_pos.col * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                to_y = self.margin + to_pos.row * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                
                painter.drawLine(from_x, from_y, to_x, to_y)

        # 为行营添加八方向细线连接（不影响移动逻辑，仅绘制）
        painter.setPen(QPen(QColor(100, 100, 100), 1))
        for pos, cell in self.board.cells.items():
            if cell.cell_type == CellType.CAMP:
                from_x = self.margin + pos.col * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                from_y = self.margin + pos.row * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        to_pos = Position(pos.row + dr, pos.col + dc)
                        if to_pos in self.board.cells:
                            to_x = self.margin + to_pos.col * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                            to_y = self.margin + to_pos.row * (self.cell_size + self.cell_spacing) + self.cell_size // 2
                            painter.drawLine(from_x, from_y, to_x, to_y)
    
    def _get_player_area_color(self, player: Player) -> QColor:
        """获取玩家区域的背景颜色"""
        colors = {
            Player.PLAYER1: QColor(255, 200, 200, 100),  # 浅红色
            Player.PLAYER2: QColor(200, 255, 200, 100),  # 浅绿色
            Player.PLAYER3: QColor(200, 200, 255, 100),  # 浅蓝色
            Player.PLAYER4: QColor(255, 255, 200, 100),  # 浅黄色
        }
        return colors.get(player, QColor(240, 240, 240, 100))
    
    def _draw_special_cells(self, painter):
        """绘制特殊格子标记"""
        for pos, cell in self.board.cells.items():
            x = self.margin + pos.col * (self.cell_size + self.cell_spacing)
            y = self.margin + pos.row * (self.cell_size + self.cell_spacing)
            
            # 在格子中心绘制特殊标记
            center_x = x + self.cell_size // 2
            center_y = y + self.cell_size // 2
            
            if cell.cell_type == CellType.CAMP:
                # 行营 - 绘制三角形
                painter.setPen(QPen(QColor(255, 165, 0), 2))
                painter.setBrush(QBrush(QColor(255, 255, 0, 200)))
                triangle_size = 8
                points = [
                    (center_x, center_y - triangle_size),
                    (center_x - triangle_size, center_y + triangle_size),
                    (center_x + triangle_size, center_y + triangle_size)
                ]
                from PyQt6.QtGui import QPolygon
                from PyQt6.QtCore import QPoint
                polygon = QPolygon([QPoint(x, y) for x, y in points])
                painter.drawPolygon(polygon)
                
            elif cell.cell_type == CellType.HEADQUARTERS:
                # 大本营 - 绘制五角星
                painter.setPen(QPen(QColor(139, 0, 0), 2))
                painter.setBrush(QBrush(QColor(255, 0, 0, 200)))
                star_size = 10
                self._draw_star(painter, center_x, center_y, star_size)
    
    def _draw_star(self, painter, center_x, center_y, size):
        """绘制五角星"""
        import math
        points = []
        for i in range(10):
            angle = i * math.pi / 5
            if i % 2 == 0:
                # 外点
                x = center_x + size * math.cos(angle - math.pi / 2)
                y = center_y + size * math.sin(angle - math.pi / 2)
            else:
                # 内点
                x = center_x + size * 0.4 * math.cos(angle - math.pi / 2)
                y = center_y + size * 0.4 * math.sin(angle - math.pi / 2)
            points.append((int(x), int(y)))
        
        from PyQt6.QtGui import QPolygon
        from PyQt6.QtCore import QPoint
        polygon = QPolygon([QPoint(x, y) for x, y in points])
        painter.drawPolygon(polygon)

    # 新增：在棋子右下角绘制战绩标记（1杀“^”、2杀上下“^ ^”、3及以上为小星）
    def _draw_kill_marks(self, painter, cell_top_left_x: int, cell_top_left_y: int, piece):
        kill_count = getattr(piece, 'kill_count', 0)
        if kill_count <= 0:
            return
        padding = 3
        overlay_w = max(12, self.cell_size // 4)
        overlay_h = max(12, self.cell_size // 4)
        rect = QRect(
            cell_top_left_x + self.cell_size - overlay_w - padding,
            cell_top_left_y + self.cell_size - overlay_h - padding,
            overlay_w,
            overlay_h
        )
        if kill_count == 1:
            # 单杀：右下角一个“^”
            mark_font = QFont("SimHei", max(8, self.cell_size // 7), QFont.Weight.Bold)
            painter.setFont(mark_font)
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "^")
        elif kill_count == 2:
            # 双杀：上下紧凑排列两个“^”
            mark_font = QFont("SimHei", max(7, self.cell_size // 8), QFont.Weight.Bold)
            painter.setFont(mark_font)
            painter.setPen(QPen(QColor(255, 255, 255)))
            half_h = rect.height() // 2
            rect_top = QRect(rect.left(), rect.top(), rect.width(), half_h)
            rect_bottom = QRect(rect.left(), rect.top() + half_h - 1, rect.width(), half_h)
            painter.drawText(rect_top, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "^")
            painter.drawText(rect_bottom, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "^")
        else:
            # 三杀及以上：右下角小金色五角星
            star_size = max(4, self.cell_size // 10)
            center_x = cell_top_left_x + self.cell_size - star_size - padding
            center_y = cell_top_left_y + self.cell_size - star_size - padding
            painter.setPen(QPen(QColor(255, 215, 0), 2))
            painter.setBrush(QBrush(QColor(255, 215, 0, 220)))
            self._draw_star(painter, center_x, center_y, star_size)

    def _draw_pieces(self, painter):
        """绘制棋子"""
        font = QFont("SimHei", 10, QFont.Weight.Bold)
        painter.setFont(font)
        
        for pos, cell in self.board.cells.items():
            if cell.piece:
                x = self.margin + pos.col * (self.cell_size + self.cell_spacing)
                y = self.margin + pos.row * (self.cell_size + self.cell_spacing)
                
                # 根据玩家设置颜色
                color = self._get_player_color(cell.piece.player)
                
                # 绘制棋子背景圆圈
                painter.setBrush(QBrush(color))
                painter.setPen(QPen(QColor(0, 0, 0), 2))
                center_x = x + self.cell_size // 2
                center_y = y + self.cell_size // 2
                radius = self.cell_size // 3
                painter.drawEllipse(center_x - radius, center_y - radius, 
                                  radius * 2, radius * 2)
                
                # 绘制棋子文字
                painter.setPen(QPen(QColor(255, 255, 255)))
                text = self._get_piece_display_text(cell.piece)
                # 有标记时，不显示“?”
                if text == "?" and pos in self.board.player_marks and self.board.player_marks[pos]:
                    text = ""
                text_rect = QRect(x, y, self.cell_size, self.cell_size)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
                
                # 新增：叠加绘制战绩标记（随棋子移动、棋子死亡不显示）
                self._draw_kill_marks(painter, x, y, cell.piece)
                # 恢复主字体，避免影响后续绘制
                painter.setFont(font)
    
    def _get_player_color(self, player: Player) -> QColor:
        """获取玩家颜色"""
        colors = {
            Player.PLAYER1: QColor(255, 0, 0),    # 红色
            Player.PLAYER2: QColor(0, 0, 255),    # 蓝色  
            Player.PLAYER3: QColor(0, 128, 0),    # 绿色
            Player.PLAYER4: QColor(255, 165, 0),  # 橙色
        }
        return colors.get(player, QColor(128, 128, 128))
    
    def _get_piece_display_text(self, piece) -> str:
        """获取棋子显示文字"""
        # 第一视角：始终显示南方玩家的棋子；开发模式或显式可见也显示
        # 修复 Bug：开始后只有首手玩家可见；改为南位（玩家1）始终自见
        if piece.player == Player.PLAYER1 or piece.visible or self.game_logic.testing_mode:
            return piece.piece_type.value
        else:
            return "?"
    
    def _draw_selection(self, painter):
        """绘制选中状态和可移动位置"""
        if self.selected_position:
            # 绘制选中的格子
            x = self.margin + self.selected_position.col * (self.cell_size + self.cell_spacing)
            y = self.margin + self.selected_position.row * (self.cell_size + self.cell_spacing)
            rect = QRect(x + 1, y + 1, self.cell_size - 2, self.cell_size - 2)
            painter.setPen(QPen(QColor(255, 0, 0), 3))
            painter.drawRect(rect)
        
        # 绘制可移动位置
        painter.setBrush(QBrush(QColor(0, 255, 0, 100)))
        painter.setPen(QPen(QColor(0, 255, 0), 2))
        for pos in self.valid_moves:
            x = self.margin + pos.col * (self.cell_size + self.cell_spacing)
            y = self.margin + pos.row * (self.cell_size + self.cell_spacing)
            center_x = x + self.cell_size // 2
            center_y = y + self.cell_size // 2
            painter.drawEllipse(center_x - 8, center_y - 8, 16, 16)
    
    def _draw_marks(self, painter):
        """绘制玩家标记"""
        font = QFont("SimHei", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(0, 0, 0)))
        
        for pos, mark in self.board.player_marks.items():
            x = self.margin + pos.col * (self.cell_size + self.cell_spacing)
            y = self.margin + pos.row * (self.cell_size + self.cell_spacing)
            # 在格子左上角显示标记
            text_rect = QRect(x, y, 16, 16)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, mark)

    def _draw_turn_indicator(self, painter):
        """在当前玩家棋盘本地坐标(6,3)的正下方绘制回合标记（避免遮挡）"""
        if self.game_logic.game_state != GameState.PLAYING:
            return
        player = self.game_logic.current_player

        # 选取锚点本地坐标：所有阵营统一使用(6,3)
        local_row, local_col = 6, 3

        # 将本地坐标转换为全局棋盘坐标
        if player == Player.PLAYER1:  # 南方：6x5，起点(11,6)
            gr = 10 + local_row
            gc = 5 + local_col
        elif player == Player.PLAYER3:  # 北方：6x5，起点(0,6)，旋转180°
            gr = 6 - local_row
            gc = 11 - local_col
        elif player == Player.PLAYER2:  # 西方：5x6，起点(6,0)，逆时针90°
            gr = 6 + (local_col - 1)
            gc = 6 - local_row
        else:  # Player.PLAYER4 东方：起点(6,11)，顺时针90°
            gr = 11 - local_col
            gc = 10 + local_row

        anchor = Position(gr, gc)
        if anchor not in self.board.cells:
            return

        # 计算该锚点格子的屏幕位置（格子下方一点）
        x = self.margin + anchor.col * (self.cell_size + self.cell_spacing)
        y = self.margin + anchor.row * (self.cell_size + self.cell_spacing)
        center_x = x + self.cell_size // 2
        center_y = y + self.cell_size // 2

        # 根据阵营方向，将标记放到“远离中心”的空白处
        offset = max(10, self.cell_spacing)
        if player == Player.PLAYER1:  # 南方：向下
            px, py = center_x, y + self.cell_size + offset
        elif player == Player.PLAYER3:  # 北方：向上
            px, py = center_x, y - offset
        elif player == Player.PLAYER2:  # 西方：向左
            px, py = x - offset, center_y
        else:  # Player.PLAYER4 东方：向右
            px, py = x + self.cell_size + offset, center_y

        # 小圆标记，尽量靠近棋盘但不覆盖格子
        radius = 6
        painter.setBrush(QBrush(QColor(50, 205, 50, 200)))  # 亮绿色
        painter.setPen(QPen(QColor(34, 139, 34), 2))
        painter.drawEllipse(int(px - radius), int(py - radius), radius * 2, radius * 2)
    
    def mousePressEvent(self, event: QMouseEvent):
        """处理鼠标点击"""
        if event.button() in [Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton]:
            # 计算点击的格子坐标（考虑格子间距）
            col = int((event.position().x() - self.margin) // (self.cell_size + self.cell_spacing))
            row = int((event.position().y() - self.margin) // (self.cell_size + self.cell_spacing))
            
            # 检查点击的位置是否是有效的格子
            position = Position(row, col)
            if position in self.board.cells:
                button = 1 if event.button() == Qt.MouseButton.LeftButton else 2
                self.cell_clicked.emit(row, col, button)
                if button == 1:
                    self._drag_start = position

    def mouseReleaseEvent(self, event: QMouseEvent):
        """处理鼠标释放（拖拽交换）"""
        if self._drag_start and event.button() == Qt.MouseButton.LeftButton:
            col = int((event.position().x() - self.margin) // (self.cell_size + self.cell_spacing))
            row = int((event.position().y() - self.margin) // (self.cell_size + self.cell_spacing))
            to_pos = Position(row, col)
            if to_pos in self.board.cells:
                self.cell_dragged.emit(self._drag_start.row, self._drag_start.col, row, col)
        self._drag_start = None
    
    def set_selection(self, position: Position, valid_moves: list):
        """设置选中状态"""
        self.selected_position = position
        self.valid_moves = valid_moves
        self.update()
    
    def clear_selection(self):
        """清除选中状态"""
        self.selected_position = None
        self.valid_moves = []
        self.update()

class GameWindow(QMainWindow):
    """游戏主窗口"""
    
    def __init__(self):
        super().__init__()
        self.game_logic = GameLogic()
        # 开发模式开关（默认关闭）
        self.dev_mode: bool = False
        self.setup_ui()
        self.connect_signals()
        # 启动时自动布局四方后刷新显示
        self.update_display()
        # 初始居中棋盘
        self._center_board_in_scrollarea()
    
    def setup_ui(self):
        """设置用户界面"""
        self.setWindowTitle("四国军棋")
        self.setMinimumSize(900, 700)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建布局
        main_layout = QVBoxLayout(central_widget)
        
        # 创建状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.update_status()
        
        # 创建棋盘组件并放入可滚动区域，居中显示以适配窗口变化
        self.board_widget = BoardWidget(self.game_logic)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.board_widget)
        # 固定内容尺寸，避免填满视口导致无法居中
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.scroll_area)
        
        # 创建控制按钮
        button_layout = QHBoxLayout()
        
        # 开发模式按钮（可开关）：允许显示全局上帝视角、操作所有棋子、显示坐标
        self.dev_mode_button = QPushButton("开发模式")
        self.dev_mode_button.setCheckable(True)
        self.dev_mode_button.setChecked(self.dev_mode)
        # 通过底部全局控制模块决定是否显示按钮
        try:
            self.dev_mode_button.setVisible(DEV_MODE_BUTTON_VISIBLE)
        except NameError:
            # 若全局控制未定义，默认显示
            self.dev_mode_button.setVisible(True)
        button_layout.addWidget(self.dev_mode_button)
        
        self.start_button = QPushButton("开始游戏")
        self.reset_button = QPushButton("重置游戏")
        self.auto_layout_button = QPushButton("自动布局")
        # 跳过与投降按钮：与其他按钮平行，位于右下角（伸展后）
        self.skip_button = QPushButton("跳过回合")
        self.surrender_button = QPushButton("投降")
        
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.reset_button)
        button_layout.addWidget(self.auto_layout_button)
        button_layout.addStretch()
        button_layout.addWidget(self.skip_button)
        button_layout.addWidget(self.surrender_button)
        
        main_layout.addLayout(button_layout)

    def showEvent(self, event):
        """窗口显示时，居中滚动内容"""
        super().showEvent(event)
        self._center_board_in_scrollarea()

    def resizeEvent(self, event):
        """窗口尺寸变化时，保持棋盘居中"""
        super().resizeEvent(event)
        self._center_board_in_scrollarea()

    def _center_board_in_scrollarea(self):
        """将滚动区域视口中心对齐棋盘"""
        if not hasattr(self, 'scroll_area') or self.scroll_area is None:
            return
        sa = self.scroll_area
        widget = sa.widget()
        vp = sa.viewport()
        if not widget or not vp:
            return
        hbar = sa.horizontalScrollBar()
        vbar = sa.verticalScrollBar()
        h_offset = max(widget.width() - vp.width(), 0) // 2
        v_offset = max(widget.height() - vp.height(), 0) // 2
        # 延迟到布局更新后再居中，确保滚动条范围已计算
        def apply_center():
            hbar.setValue(h_offset)
            vbar.setValue(v_offset)
        QTimer.singleShot(0, apply_center)

    def connect_signals(self):
        """连接信号和槽"""
        self.board_widget.cell_clicked.connect(self.on_cell_clicked)
        self.board_widget.cell_dragged.connect(self.on_cell_dragged)
        self.start_button.clicked.connect(self.start_game)
        self.reset_button.clicked.connect(self.reset_game)
        self.auto_layout_button.clicked.connect(self.auto_layout)
        self.skip_button.clicked.connect(self.skip_turn)
        self.surrender_button.clicked.connect(self.surrender)
        # 开发模式按钮开关：动态切换坐标显示与棋子权限/可见性
        self.dev_mode_button.toggled.connect(self.toggle_dev_mode)

    def on_cell_clicked(self, row: int, col: int, button: int):
        """处理格子点击事件"""
        position = Position(row, col)
        
        if button == 1:  # 左键点击
            self.handle_left_click(position)
        elif button == 2:  # 右键点击
            self.handle_right_click(position)
    
    def handle_left_click(self, position: Position):
        """处理左键点击"""
        if self.game_logic.game_state == GameState.SETUP:
            # 布局阶段：支持点击选中后进行交换
            cell = self.game_logic.board.get_cell(position)
            if cell and cell.piece:
                # 非开发模式：只能选择当前玩家的棋子
                if (not self.game_logic.testing_mode) and (cell.piece.player != self.game_logic.current_player):
                    self.status_bar.showMessage("非开发模式下，仅可调整当前玩家的棋子")
                    self.board_widget.clear_selection()
                    return
                if self.board_widget.selected_position is None:
                    self.board_widget.set_selection(position, [])
                else:
                    if self.game_logic.swap_setup_positions(self.board_widget.selected_position, position):
                        self.board_widget.clear_selection()
                        self.update_display()
                    else:
                        self.board_widget.clear_selection()
                        QMessageBox.warning(self, "非法调整", "该位置不符合布局规则，已恢复。")
        elif self.game_logic.game_state == GameState.PLAYING:
            # 游戏阶段 - 处理移动
            self.handle_piece_move(position)

    def on_cell_dragged(self, fr: int, fc: int, tr: int, tc: int):
        """拖拽交换（布局阶段）"""
        if self.game_logic.game_state != GameState.SETUP:
            return
        from_pos = Position(fr, fc)
        to_pos = Position(tr, tc)
        # 非开发模式：起点必须为当前玩家棋子
        from_cell = self.game_logic.board.get_cell(from_pos)
        if (not self.game_logic.testing_mode) and (not from_cell or not from_cell.piece or from_cell.piece.player != self.game_logic.current_player):
            self.status_bar.showMessage("非开发模式下，仅可调整当前玩家的棋子")
            self.board_widget.clear_selection()
            return
        if self.game_logic.swap_setup_positions(from_pos, to_pos):
            self.board_widget.clear_selection()
            self.update_display()
        else:
            self.board_widget.clear_selection()
            QMessageBox.warning(self, "非法调整", "该位置不符合布局规则，已恢复。")

    def handle_piece_move(self, position: Position):
        """处理棋子移动"""
        # 非开发模式：仅允许玩家1在自己回合进行操作，禁止在电脑玩家回合进行任何移动/选择
        if not self.game_logic.testing_mode and self.game_logic.current_player != Player.PLAYER1:
            self.status_bar.showMessage("当前为电脑玩家回合，无法操作")
            self.board_widget.clear_selection()
            return
        if self.board_widget.selected_position:
            # 已有选中的棋子，尝试移动
            from_pos = self.board_widget.selected_position
            # 委托棋盘层处理移动及标记的跟随/清除（标记面向棋子）
            if self.game_logic.move_piece(from_pos, position):
                self.board_widget.clear_selection()
                self.update_display()
            else:
                # 移动失败，重新选择
                self.select_piece(position)
        else:
            # 选择棋子
            self.select_piece(position)

    def select_piece(self, position: Position):
        """选择棋子"""
        cell = self.game_logic.board.get_cell(position)
        if (cell and cell.piece and 
            (self.game_logic.testing_mode or (self.game_logic.current_player == Player.PLAYER1 and cell.piece.player == Player.PLAYER1))):
            # 获取可移动位置
            valid_moves = self.get_valid_moves(position)
            self.board_widget.set_selection(position, valid_moves)
        else:
            self.board_widget.clear_selection()
    
    def get_valid_moves(self, position: Position) -> list:
        """获取棋子的有效移动位置"""
        valid_moves = []
        
        # 获取相邻位置
        adjacent_positions = self.game_logic.board.get_adjacent_positions(position)
        
        for adj_pos in adjacent_positions:
            if self.game_logic.board.can_move(position, adj_pos):
                valid_moves.append(adj_pos)
        
        # 在铁路上：工兵连通拐弯，其它棋子直线行进
        cell = self.game_logic.board.get_cell(position)
        if cell and cell.piece and cell.cell_type == CellType.RAILWAY:
            if cell.piece.is_engineer():
                railway_positions = self.game_logic.board.get_railway_connected_positions(position)
                for rail_pos in railway_positions:
                    if (rail_pos != position and 
                        self.game_logic.board.can_move(position, rail_pos)):
                        valid_moves.append(rail_pos)
            else:
                straight_positions = self.game_logic.board.get_railway_straight_reachable_positions(position)
                for rail_pos in straight_positions:
                    if (rail_pos != position and 
                        self.game_logic.board.can_move(position, rail_pos)):
                        valid_moves.append(rail_pos)
        
        return valid_moves
    
    def handle_right_click(self, position: Position):
        """处理右键点击 - 显示标记菜单（仅在对局阶段，允许标记任何已有棋子）"""
        if self.game_logic.game_state == GameState.PLAYING:
            cell = self.game_logic.board.get_cell(position)
            if cell and cell.piece:
                self.show_mark_menu(position)
    
    def show_mark_menu(self, position: Position):
        """显示标记菜单"""
        menu = QMenu(self)
        
        marks = ["司", "军", "师", "旅", "团", "营", "连", "排", "工", "炸", "雷", "旗", "清除"]
        
        for mark in marks:
            action = menu.addAction(mark)
            if mark == "清除":
                action.triggered.connect(lambda: self.set_mark(position, ""))
            else:
                action.triggered.connect(lambda checked, m=mark: self.set_mark(position, m))
        
        # 在鼠标当前位置显示菜单（全局屏幕坐标）
        menu.exec(QCursor.pos())
    
    def set_mark(self, position: Position, mark: str):
        """设置位置标记"""
        if mark:
            self.game_logic.board.set_mark(position, mark)
        else:
            # 清除标记
            if position in self.game_logic.board.player_marks:
                del self.game_logic.board.player_marks[position]
        
        self.board_widget.update()
    
    def start_game(self):
        """开始游戏"""
        if self.game_logic.start_game():
            # 根据当前开发模式状态同步测试模式与可见性
            self.game_logic.testing_mode = self.dev_mode
            for pos, cell in self.game_logic.board.cells.items():
                if cell.piece:
                    cell.piece.visible = self.game_logic.testing_mode
            self.update_display()
            QMessageBox.information(self, "游戏开始", "游戏已开始！")
        else:
            QMessageBox.warning(self, "无法开始", "请先完成棋子布局！")

    def skip_turn(self):
        """跳过当前回合"""
        if self.game_logic.skip_turn():
            self.update_display()
        else:
            QMessageBox.warning(self, "无法跳过", "当前不在对战阶段。")

    def surrender(self):
        """投降当前玩家（第一视角：玩家1）"""
        if self.game_logic.surrender():
            self.update_display()
            QMessageBox.information(self, "投降", "已投降，回合切换至下一家。")
        else:
            QMessageBox.warning(self, "无法投降", "当前不在对战阶段。")
    
    def reset_game(self):
        """重置游戏"""
        self.game_logic.reset_game()
        # 关键：BoardWidget缓存了旧的Board引用，这里需要重绑
        self.board_widget.game_logic = self.game_logic
        self.board_widget.board = self.game_logic.board
        self.board_widget.clear_selection()
        # 同步开发模式：重置后保持当前开发模式体验
        self.game_logic.testing_mode = self.dev_mode
        for pos, cell in self.game_logic.board.cells.items():
            if cell.piece:
                cell.piece.visible = self.game_logic.testing_mode
        self.update_display()
        QMessageBox.information(self, "游戏重置", "游戏已重置！")

    def auto_layout(self):
        """自动布局当前玩家的棋子"""
        current_player = self.game_logic.current_player
        if self.game_logic.auto_layout_player(current_player):
            self.update_display()
            QMessageBox.information(self, "自动布局", f"玩家{current_player.value}的棋子已自动布局完成！")
        else:
            QMessageBox.warning(self, "布局失败", "自动布局失败！")
    
    def update_display(self):
        """更新显示"""
        self.board_widget.update()
        self.update_status()
        self._update_play_controls()
        # 每次刷新后确保居中
        self._center_board_in_scrollarea()
    
    def update_status(self):
        """更新状态栏"""
        if self.game_logic.game_state == GameState.SETUP:
            status = f"布局阶段 - 当前玩家: {self.game_logic.current_player.value}"
        elif self.game_logic.game_state == GameState.PLAYING:
            status = f"游戏进行中 - 当前回合: {self.game_logic.current_player.value}（逆时针）"
        else:
            status = "游戏结束"
        
        self.status_bar.showMessage(status)

    def _update_play_controls(self):
        """根据状态控制跳过与投降按钮（第一视角：玩家1）"""
        in_play = self.game_logic.game_state == GameState.PLAYING
        is_p1_turn = self.game_logic.current_player == Player.PLAYER1
        self.skip_button.setVisible(in_play and is_p1_turn)
        self.surrender_button.setVisible(in_play and is_p1_turn)

    def toggle_dev_mode(self, checked: bool):
        """切换开发模式"""
        self.dev_mode = checked
        self.game_logic.testing_mode = self.dev_mode
        # 同步所有棋子可见性
        for pos, cell in self.game_logic.board.cells.items():
            if cell.piece:
                cell.piece.visible = self.game_logic.testing_mode
        # 切换后刷新显示
        self.update_display()
        self.status_bar.showMessage("开发模式：开启" if self.dev_mode else "开发模式：关闭")

    def select_piece(self, position: Position):
        """选择棋子"""
        cell = self.game_logic.board.get_cell(position)
        if (cell and cell.piece and 
            (self.game_logic.testing_mode or (self.game_logic.current_player == Player.PLAYER1 and cell.piece.player == Player.PLAYER1))):
            # 获取可移动位置
            valid_moves = self.get_valid_moves(position)
            self.board_widget.set_selection(position, valid_moves)
        else:
            self.board_widget.clear_selection()
    
    def get_valid_moves(self, position: Position) -> list:
        """获取棋子的有效移动位置"""
        valid_moves = []
        
        # 获取相邻位置
        adjacent_positions = self.game_logic.board.get_adjacent_positions(position)
        
        for adj_pos in adjacent_positions:
            if self.game_logic.board.can_move(position, adj_pos):
                valid_moves.append(adj_pos)
        
        # 在铁路上：工兵连通拐弯，其它棋子直线行进
        cell = self.game_logic.board.get_cell(position)
        if cell and cell.piece and cell.cell_type == CellType.RAILWAY:
            if cell.piece.is_engineer():
                railway_positions = self.game_logic.board.get_railway_connected_positions(position)
                for rail_pos in railway_positions:
                    if (rail_pos != position and 
                        self.game_logic.board.can_move(position, rail_pos)):
                        valid_moves.append(rail_pos)
            else:
                straight_positions = self.game_logic.board.get_railway_straight_reachable_positions(position)
                for rail_pos in straight_positions:
                    if (rail_pos != position and 
                        self.game_logic.board.can_move(position, rail_pos)):
                        valid_moves.append(rail_pos)
        
        return valid_moves
    
    def handle_right_click(self, position: Position):
        """处理右键点击 - 显示标记菜单（仅在对局阶段，允许标记任何已有棋子）"""
        if self.game_logic.game_state == GameState.PLAYING:
            cell = self.game_logic.board.get_cell(position)
            if cell and cell.piece:
                self.show_mark_menu(position)
    
    def show_mark_menu(self, position: Position):
        """显示标记菜单"""
        menu = QMenu(self)
        
        marks = ["司", "军", "师", "旅", "团", "营", "连", "排", "工", "炸", "雷", "旗", "清除"]
        
        for mark in marks:
            action = menu.addAction(mark)
            if mark == "清除":
                action.triggered.connect(lambda: self.set_mark(position, ""))
            else:
                action.triggered.connect(lambda checked, m=mark: self.set_mark(position, m))
        
        # 在鼠标右键点击处显示菜单（全局屏幕坐标）
        menu.exec(QCursor.pos())
    
    def set_mark(self, position: Position, mark: str):
        """设置位置标记"""
        if mark:
            self.game_logic.board.set_mark(position, mark)
        else:
            # 清除标记
            if position in self.game_logic.board.player_marks:
                del self.game_logic.board.player_marks[position]
        
        self.board_widget.update()
    
    def start_game(self):
        """开始游戏"""
        if self.game_logic.start_game():
            # 根据当前开发模式状态同步测试模式与可见性
            self.game_logic.testing_mode = self.dev_mode
            for pos, cell in self.game_logic.board.cells.items():
                if cell.piece:
                    cell.piece.visible = self.game_logic.testing_mode
            self.update_display()
            QMessageBox.information(self, "游戏开始", "游戏已开始！")
        else:
            QMessageBox.warning(self, "无法开始", "请先完成棋子布局！")

    def skip_turn(self):
        """跳过当前回合"""
        if self.game_logic.skip_turn():
            self.update_display()
        else:
            QMessageBox.warning(self, "无法跳过", "当前不在对战阶段。")

    def surrender(self):
        """投降当前玩家（第一视角：玩家1）"""
        if self.game_logic.surrender():
            self.update_display()
            QMessageBox.information(self, "投降", "已投降，回合切换至下一家。")
        else:
            QMessageBox.warning(self, "无法投降", "当前不在对战阶段。")
    
    def reset_game(self):
        """重置游戏"""
        self.game_logic.reset_game()
        # 关键：BoardWidget缓存了旧的Board引用，这里需要重绑
        self.board_widget.game_logic = self.game_logic
        self.board_widget.board = self.game_logic.board
        self.board_widget.clear_selection()
        # 同步开发模式：重置后保持当前开发模式体验
        self.game_logic.testing_mode = self.dev_mode
        for pos, cell in self.game_logic.board.cells.items():
            if cell.piece:
                cell.piece.visible = self.game_logic.testing_mode
        self.update_display()
        QMessageBox.information(self, "游戏重置", "游戏已重置！")

    def auto_layout(self):
        """自动布局当前玩家的棋子"""
        current_player = self.game_logic.current_player
        if self.game_logic.auto_layout_player(current_player):
            self.update_display()
            QMessageBox.information(self, "自动布局", f"玩家{current_player.value}的棋子已自动布局完成！")
        else:
            QMessageBox.warning(self, "布局失败", "自动布局失败！")
    
    def update_display(self):
        """更新显示"""
        self.board_widget.update()
        self.update_status()
        self._update_play_controls()
        # 每次刷新后确保居中
        self._center_board_in_scrollarea()
    
    def update_status(self):
        """更新状态栏"""
        if self.game_logic.game_state == GameState.SETUP:
            status = f"布局阶段 - 当前玩家: {self.game_logic.current_player.value}"
        elif self.game_logic.game_state == GameState.PLAYING:
            status = f"游戏进行中 - 当前回合: {self.game_logic.current_player.value}（逆时针）"
        else:
            status = "游戏结束"
        
        self.status_bar.showMessage(status)

# === 全局控制模块（开发模式按钮显示/隐藏）===
# 修改方法：将 DEV_MODE_BUTTON_VISIBLE 设为 False 可隐藏主界面上的“开发模式”按钮。
DEV_MODE_BUTTON_VISIBLE = True