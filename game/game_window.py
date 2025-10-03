# -*- coding: utf-8 -*-
"""
四国军棋游戏主窗口
"""

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLabel, QStatusBar, QMessageBox, QMenu, QComboBox, QLineEdit, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QSize, QPoint
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QMouseEvent, QCursor, QPixmap, QPolygon
from .game_logic import GameLogic, GameState
from .board import Position, CellType
from .piece import Player
import os
import random

from .ws_client import WSClient

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
        # 记录基础尺寸，便于缩放
        self.base_cell_size = self.cell_size
        self.base_cell_spacing = self.cell_spacing
        self.base_margin = self.margin
        
        # 计算总尺寸
        self.total_width = self.board.cols * (self.cell_size + self.cell_spacing) + 2 * self.margin
        self.total_height = self.board.rows * (self.cell_size + self.cell_spacing) + 2 * self.margin
        self.setMinimumSize(self.total_width, self.total_height)

    def sizeHint(self) -> QSize:
        """为滚动区域提供内容的理想尺寸，以便正确居中"""
        return QSize(self.total_width, self.total_height)
    
    def recalc_dimensions(self):
        """依据当前显示参数重新计算尺寸"""
        self.total_width = self.board.cols * (self.cell_size + self.cell_spacing) + 2 * self.margin
        self.total_height = self.board.rows * (self.cell_size + self.cell_spacing) + 2 * self.margin
        self.setMinimumSize(self.total_width, self.total_height)
    
    def set_scale_factor(self, f: float):
        """按比例缩放棋盘显示参数，限制最大为1.0，最小为0.3"""
        f = max(0.3, min(1.0, f))
        self.cell_size = int(self.base_cell_size * f)
        self.cell_spacing = int(self.base_cell_spacing * f)
        self.margin = int(self.base_margin * f)
        self.recalc_dimensions()
        self.update()
    
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
        
        
        polygon = QPolygon([QPoint(x, y) for x, y in points])
        painter.drawPolygon(polygon)

    # 新增：在棋子右下角绘制战绩标记（1/2杀“^”、3及以上为小星），颜色高饱和度洋红色
    def _draw_kill_marks(self, painter, cell_top_left_x: int, cell_top_left_y: int, piece):
        kill_count = getattr(piece, 'kill_count', 0)
        if kill_count <= 0:
            return
        magenta = QColor(255, 0, 255)  # 与红/蓝/绿/橙明显区分的高饱和色
        # 右下角叠加区域：更贴近右下角，尽量不遮挡中央文字
        padding = max(1, self.cell_size // 40)
        overlay_w = max(16, int(self.cell_size * 0.42))
        overlay_h = max(16, int(self.cell_size * 0.42))
        rect = QRect(
            cell_top_left_x + self.cell_size - overlay_w - padding,
            cell_top_left_y + self.cell_size - overlay_h - padding,
            overlay_w,
            overlay_h
        )
        if kill_count >= 3:
            # 三杀及以上：小五角星（洋红色），更贴近右下角（整体再小一号）
            star_size = max(7, int(self.cell_size * 0.12))
            center_x = rect.right() - star_size // 2
            center_y = rect.bottom() - star_size // 2
            painter.setPen(QPen(magenta, 2))
            painter.setBrush(QBrush(QColor(255, 0, 255, 220)))
            self._draw_star(painter, center_x, center_y, star_size)
        else:
            # 1/2杀：改用矢量绘制“^”，锚定右下角，略微缩小以提升协调性
            stroke_w = max(2, int(self.cell_size * 0.04))
            margin = max(1, int(self.cell_size * 0.04))
            length = max(10, int(self.cell_size * 0.28))
            height = max(6, int(length * 0.52))
            anchor_x = cell_top_left_x + self.cell_size - margin  # 右边界内缩
            anchor_y = cell_top_left_y + self.cell_size - margin  # 下边界内缩
            left_x = anchor_x - length
            right_x = anchor_x
            apex_x = anchor_x - length // 2
            apex_y = anchor_y - height
            pen = QPen(magenta, stroke_w)
            painter.setPen(pen)
            painter.drawLine(left_x, anchor_y, apex_x, apex_y)
            painter.drawLine(apex_x, apex_y, right_x, anchor_y)
    
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
            Player.PLAYER2: QColor(135, 206, 250),    # 淡蓝色（原为深蓝色）
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
    ws_connected = pyqtSignal()
    ws_error = pyqtSignal(str)
    chat_message_received = pyqtSignal(dict)
    chat_received_ack = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.game_logic = GameLogic()
        # 开发模式开关（默认关闭）
        self.dev_mode: bool = False
        # 初始化我方与三家AI的人物与席位分配数据结构（需在 UI 创建前准备）
        self.me_name = "saki"
        self.me_name_edit = None
        self.seat_combos = {}
        self.seat_name_labels = {}
        self.seat_avatar_labels = {}
        # 三个AI席位默认随机分配（仅对非我方位置）
        self.seat_assignments = {
            Player.PLAYER2: "random",
            Player.PLAYER3: "random",
            Player.PLAYER4: "random",
        }
        # 加载AI人物资源与名称映射
        self._init_personas()
        
        # WebSocket 客户端相关（解耦为 WSClient）
        self.WS_URL = "ws://localhost:8765"
        self.ws_client = None
        self.ws_connected_flag = False
        
        # 聊天控件与状态
        self.me_chat_input = None
        self.me_chat_send_btn = None
        self.chat_locked = False
        
        # 头像定位参数
        self.avatar_frames = {}
        self.avatar_padding = 48
        self.setup_ui()
        self.connect_signals()
        # 启动时自动布局四方后刷新显示
        self.update_display()
        # 初始布局棋盘与头像
        self._layout_play_area()
    
    def setup_ui(self):
        """设置用户界面"""
        self.setWindowTitle("四国军棋")
        self.setMinimumSize(900, 700)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局（垂直），其中包含一个棋盘+头像框的网格容器
        main_layout = QVBoxLayout(central_widget)
        # 去除默认边距和间距，使居中更准确
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 创建状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.update_status()
        
        # 播放区域：承载棋盘与头像框，采用绝对定位
        self.board_widget = BoardWidget(self.game_logic)
        self.play_area = QWidget()
        # 允许缩小到较小尺寸以触发自适应缩放
        self.play_area.setMinimumSize(QSize(200, 200))
        # 扩展以占据可用空间
        self.play_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # 棋盘作为播放区域子控件
        self.board_widget.setParent(self.play_area)
        
        # 创建四个头像框并设置为播放区域子控件
        top_frame = self._create_avatar_frame(Player.PLAYER3)
        top_frame.setParent(self.play_area)
        left_frame = self._create_avatar_frame(Player.PLAYER2)
        left_frame.setParent(self.play_area)
        right_frame = self._create_avatar_frame(Player.PLAYER4)
        right_frame.setParent(self.play_area)
        bottom_frame = self._create_avatar_frame(Player.PLAYER1, is_me=True)
        bottom_frame.setParent(self.play_area)
        
        # 保存映射以便定位
        self.avatar_frames = {
            Player.PLAYER1: bottom_frame,
            Player.PLAYER3: top_frame,
            Player.PLAYER2: left_frame,
            Player.PLAYER4: right_frame,
        }
        
        # 将播放区域加入主布局
        main_layout.addWidget(self.play_area, stretch=1)
        
        # 控制按钮区
        button_layout = QHBoxLayout()
        self.dev_mode_button = QPushButton("开发模式")
        self.dev_mode_button.setCheckable(True)
        self.dev_mode_button.setChecked(self.dev_mode)
        self.dev_mode_button.setVisible(DEV_MODE_BUTTON_VISIBLE)
        button_layout.addWidget(self.dev_mode_button)
        
        self.start_button = QPushButton("开始游戏")
        self.reset_button = QPushButton("重置游戏")
        self.auto_layout_button = QPushButton("自动布局")
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
        """窗口显示时，居中棋盘并定位头像"""
        super().showEvent(event)
        self._layout_play_area()

    def resizeEvent(self, event):
        """窗口尺寸变化时，保持棋盘居中并重新定位头像"""
        super().resizeEvent(event)
        self._layout_play_area()

    def connect_signals(self):
        """连接信号和槽"""
        self.board_widget.cell_clicked.connect(self.on_cell_clicked)
        self.board_widget.cell_dragged.connect(self.on_cell_dragged)
        self.start_button.clicked.connect(self.start_game)
        self.reset_button.clicked.connect(self.reset_game)
        self.auto_layout_button.clicked.connect(self.auto_layout)
        self.skip_button.clicked.connect(self.skip_turn)
        self.surrender_button.clicked.connect(self.surrender)
        self.dev_mode_button.toggled.connect(self.toggle_dev_mode)
        # 头像选择下拉事件
        for seat, combo in self.seat_combos.items():
            combo.currentTextChanged.connect(lambda _text, s=seat: self._on_seat_selection_changed(s))
        # 聊天发送按钮
        if self.me_chat_send_btn:
            self.me_chat_send_btn.clicked.connect(self._on_send_chat_clicked)
        # WebSocket 事件信号
        self.ws_connected.connect(self._on_ws_connected)
        self.ws_error.connect(lambda msg: self.status_bar.showMessage(f"WS错误：{msg}"))
        self.chat_received_ack.connect(self._on_chat_received_ack)
        self.chat_message_received.connect(self._on_chat_message_broadcast)

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
            # 开始前为随机席位分配唯一AI人物
            self.finalize_ai_assignments()
            # 根据当前开发模式状态同步测试模式与可见性
            self.game_logic.testing_mode = self.dev_mode
            for pos, cell in self.game_logic.board.cells.items():
                if cell.piece:
                    cell.piece.visible = self.game_logic.testing_mode
            # 锁定角色选择与玩家名编辑：隐藏下拉并禁用编辑
            for combo in self.seat_combos.values():
                combo.setVisible(False)
                combo.setEnabled(False)
            if self.me_name_edit:
                self.me_name_edit.setReadOnly(True)
            self.update_display()
            # 启动WebSocket客户端，待连接成功后将发送 start_game
            if not self.ws_client:
                self.ws_client = WSClient(
                    self.WS_URL,
                    on_connected=lambda: self.ws_connected.emit(),
                    on_error=lambda msg: self.ws_error.emit(msg),
                    on_chat_received=lambda d: self.chat_received_ack.emit(d),
                    on_chat_message=lambda d: self.chat_message_received.emit(d),
                    on_message=lambda d: None
                )
                self.ws_client.start()
            # 进入对战阶段：允许输入聊天文本，发送按钮在连接建立前保持禁用
            if self.me_chat_input:
                self.me_chat_input.setEnabled(True)
            if self.me_chat_send_btn:
                self.me_chat_send_btn.setEnabled(False)
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
        # 恢复AI席位分配为随机
        self.seat_assignments = {
            Player.PLAYER2: "random",
            Player.PLAYER3: "random",
            Player.PLAYER4: "random",
        }
        # 重新刷新下拉与名称显示
        self._refresh_persona_options()
        for seat, label in self.seat_name_labels.items():
            label.setText("随机")
        # 清空随机席位的头像（刷新下拉时阻断了信号，未触发头像清空）
        for seat, avatar_label in self.seat_avatar_labels.items():
            if self.seat_assignments.get(seat) == "random" and avatar_label:
                avatar_label.clear()
        # 重新显示并启用下拉，允许布局阶段选择
        for combo in self.seat_combos.values():
            combo.setVisible(True)
            combo.setEnabled(True)
        # 玩家名恢复可编辑
        if self.me_name_edit:
            self.me_name_edit.setReadOnly(False)
            self.me_name_edit.setEnabled(True)
        # 关键：BoardWidget缓存了旧的Board引用，这里需要重绑
        self.board_widget.game_logic = self.game_logic
        self.board_widget.board = self.game_logic.board
        self.board_widget.clear_selection()
        # 同步开发模式：重置后保持当前开发模式体验
        self.game_logic.testing_mode = self.dev_mode
        for pos, cell in self.game_logic.board.cells.items():
            if cell.piece:
                cell.piece.visible = self.game_logic.testing_mode
        # 重新显示头像框并重新定位
        for frame in self.avatar_frames.values():
            frame.setVisible(True)
        # 恢复我方名字输入框样式（黑色、居中、半粗）
        if self.me_name_edit:
            self.me_name_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.me_name_edit.setStyleSheet("color: #000000; font-weight: 500;")
        self._layout_play_area()
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
        # 每次刷新后确保居中并定位头像
        self._layout_play_area()
    
    def update_status(self):
        """更新状态栏，并根据阶段更新聊天控件可用性"""
        if self.game_logic.game_state == GameState.SETUP:
            status = f"布局阶段 - 当前玩家: {self.game_logic.current_player.value}"
            # 布局阶段：禁用聊天输入与发送
            if self.me_chat_input:
                self.me_chat_input.setEnabled(False)
            if self.me_chat_send_btn:
                self.me_chat_send_btn.setEnabled(False)
        elif self.game_logic.game_state == GameState.PLAYING:
            status = f"游戏进行中 - 当前回合: {self.game_logic.current_player.value}（逆时针）"
            # 对战阶段：允许输入文本，发送按钮随WS连接状态与锁定标记
            if self.me_chat_input:
                self.me_chat_input.setEnabled(True)
            if self.me_chat_send_btn:
                self.me_chat_send_btn.setEnabled(self.ws_connected_flag and not self.chat_locked)
        else:
            status = "游戏结束"
            # 结束阶段：禁用聊天输入与发送
            if self.me_chat_input:
                self.me_chat_input.setEnabled(False)
            if self.me_chat_send_btn:
                self.me_chat_send_btn.setEnabled(False)
        
        self.status_bar.showMessage(status)

    # === 全局控制模块（开发模式按钮显示/隐藏）===
    # 修改方法：将 DEV_MODE_BUTTON_VISIBLE 设为 False 可隐藏主界面上的“开发模式”按钮。
    # DEV_MODE_BUTTON_VISIBLE 的全局定义已移动到文件末尾统一管理

    def _init_personas(self):
        """初始化三家AI人物信息与资源路径。"""
        assets_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets"))
        self.personas = {
            "player1": {"name": "外卖剩一半", "avatar": os.path.join(assets_dir, "player_1.png")},
            "player2": {"name": "旧刊夹页", "avatar": os.path.join(assets_dir, "player_2.png")},
            "player3": {"name": "老陈夜茶凉", "avatar": os.path.join(assets_dir, "player_3.png")},
        }
        self.me_avatar_path = os.path.join(assets_dir, "player_me.jpg")
        # 当前未被占用的候选池（用于随机）
        self.available_personas = set(self.personas.keys())
        # 名称到键的反查
        self._persona_name_to_key = {v["name"]: k for k, v in self.personas.items()}
    
    def _create_avatar_frame(self, seat: Player, is_me: bool = False) -> QWidget:
        """创建头像框组件，包含头像与姓名显示；AI席位附带选择下拉。"""
        frame = QWidget()
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        # 头像
        avatar_label = QLabel()
        avatar_label.setFixedSize(96, 96)
        avatar_label.setStyleSheet("border: 1px solid #cccccc; border-radius: 4px;")
        avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 姓名：我方可编辑，其余显示为当前选择/随机
        if is_me:
            name_edit = QLineEdit(self.me_name)
            name_edit.setPlaceholderText("点击修改你的名字")
            name_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_edit.setStyleSheet("color: #000000; font-weight: 500;")
            name_edit.textChanged.connect(lambda text: setattr(self, "me_name", text))
            self.me_name_edit = name_edit
            # 加载我方头像
            pix = QPixmap(self.me_avatar_path)
            if not pix.isNull():
                avatar_label.setPixmap(pix.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            v.addWidget(avatar_label, alignment=Qt.AlignmentFlag.AlignCenter)
            v.addWidget(name_edit, alignment=Qt.AlignmentFlag.AlignCenter)
            # 聊天输入与发送按钮（占位，后续集成服务器）
            self.me_chat_input = QLineEdit()
            self.me_chat_input.setPlaceholderText("输入聊天内容")
            self.me_chat_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.me_chat_input.setEnabled(False)  # 游戏开始后再启用
            self.me_chat_send_btn = QPushButton("发送")
            self.me_chat_send_btn.setEnabled(False)
            # 移除占位点击连接，真实发送逻辑在 connect_signals 中绑定
            btn_row = QHBoxLayout()
            btn_row.addWidget(self.me_chat_input)
            btn_row.addWidget(self.me_chat_send_btn)
            v.addLayout(btn_row)
        else:
            name_label = QLabel("随机")
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_label.setStyleSheet("font-weight: 500;")
            self.seat_name_labels[seat] = name_label
            # 记录头像标签，便于后续更新
            self.seat_avatar_labels[seat] = avatar_label
            # AI下拉选择
            combo = QComboBox()
            combo.setMinimumWidth(140)
            self.seat_combos[seat] = combo
            v.addWidget(avatar_label, alignment=Qt.AlignmentFlag.AlignCenter)
            v.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignCenter)
            v.addWidget(combo, alignment=Qt.AlignmentFlag.AlignCenter)
            # 初始头像：随机状态不显示头像（空白）
            avatar_label.clear()
        
        # 根据席位刷新下拉选项
        self._refresh_persona_options()
        return frame
    
    def _refresh_persona_options(self):
        """根据当前分配状态刷新每个席位的下拉选项，确保唯一性。"""
        used = {assign for seat, assign in self.seat_assignments.items() if assign != "random"}
        for seat, combo in self.seat_combos.items():
            current = self.seat_assignments.get(seat, "random")
            # 为该席位可选集合：所有 - 其他已用
            allowed = [k for k in self.personas.keys() if k not in (used - ({current} if current != "random" else set()))]
            # 生成显示文本数组
            items = ["随机"] + [self.personas[k]["name"] for k in allowed]
            prev_text = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(items)
            # 恢复原选择（若仍可用），否则保持随机
            if current != "random":
                name = self.personas[current]["name"]
                idx = combo.findText(name)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                else:
                    combo.setCurrentIndex(0)
            else:
                # 保持随机
                combo.setCurrentIndex(0)
            combo.blockSignals(False)
    
    def _on_seat_selection_changed(self, seat: Player):
        """当某席位选择变化时，更新分配并刷新其他席位可选项与名称显示与头像。"""
        combo = self.seat_combos.get(seat)
        if not combo:
            return
        text = combo.currentText()
        avatar_label = self.seat_avatar_labels.get(seat)
        name_label = self.seat_name_labels.get(seat)
        if text == "随机":
            self.seat_assignments[seat] = "random"
            if name_label:
                name_label.setText("随机")
            if avatar_label:
                avatar_label.clear()
        else:
            # 文本到key
            key = self._persona_name_to_key.get(text)
            if key:
                self.seat_assignments[seat] = key
                if name_label:
                    name_label.setText(text)
                # 更新头像
                persona = self.personas.get(key)
                avatar_path = persona.get("avatar")
                if avatar_label:
                    if avatar_path:
                        pix = QPixmap(avatar_path)
                        if not pix.isNull():
                            avatar_label.setPixmap(pix.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                        else:
                            avatar_label.clear()
                    else:
                        avatar_label.clear()
        # 刷新其他席位的选项，保证唯一性
        self._refresh_persona_options()
    
    def finalize_ai_assignments(self):
        """开始游戏前，将随机席位从可用池中唯一分配，并更新名称显示。"""
        chosen = {assign for assign in self.seat_assignments.values() if assign != "random"}
        remaining = [k for k in self.personas.keys() if k not in chosen]
        # 为每个随机席位分配未使用的人物
        for seat, assign in list(self.seat_assignments.items()):
            if assign == "random":
                if not remaining:
                    # 若资源不足，回收全部后再随机（理论上不会发生，因为总席位=3）
                    remaining = list(self.personas.keys())
                pick = random.choice(remaining)
                remaining.remove(pick)
                self.seat_assignments[seat] = pick
                # 更新名称
                name = self.personas[pick]["name"]
                if self.seat_name_labels.get(seat):
                    self.seat_name_labels[seat].setText(name)
                # 更新头像
                avatar_label = self.seat_avatar_labels.get(seat)
                avatar_path = self.personas[pick].get("avatar")
                if avatar_label:
                    pix = QPixmap(avatar_path) if avatar_path else QPixmap()
                    if not pix.isNull():
                        avatar_label.setPixmap(pix.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    else:
                        avatar_label.clear()
        # 刷新下拉选项，使其与最终分配一致
        self._refresh_persona_options()

    def _layout_play_area(self):
        """在播放区域中居中棋盘，并按指定参考坐标放置头像框"""
        if not hasattr(self, 'play_area') or self.play_area is None:
            return
        # 允许播放区域缩小
        self.play_area.setMinimumSize(QSize(200, 200))
        
        # 根据可用空间计算缩放比例（不放大，只缩小）
        area_w = self.play_area.width()
        area_h = self.play_area.height()
        base_total_w = self.board_widget.board.cols * (self.board_widget.base_cell_size + self.board_widget.base_cell_spacing) + 2 * self.board_widget.base_margin
        base_total_h = self.board_widget.board.rows * (self.board_widget.base_cell_size + self.board_widget.base_cell_spacing) + 2 * self.board_widget.base_margin
        if area_w > 0 and area_h > 0:
            scale = min(1.0, area_w / base_total_w, area_h / base_total_h)
            self.board_widget.set_scale_factor(scale)
        
        # 居中棋盘
        bw = self.board_widget.total_width
        bh = self.board_widget.total_height
        self._board_offset_x = max((area_w - bw) // 2, 0)
        self._board_offset_y = max((area_h - bh) // 2, 0)
        self.board_widget.move(self._board_offset_x, self._board_offset_y)
        self.board_widget.resize(bw, bh)
        
        # 重新定位头像框
        self._position_avatar_frames()

    def _cell_top_left_in_play_area(self, row: int, col: int) -> tuple[int, int]:
        """根据棋盘全局(row,col)计算在播放区域中的格子左上角像素坐标"""
        step = self.board_widget.cell_size + self.board_widget.cell_spacing
        x = self._board_offset_x + self.board_widget.margin + col * step
        y = self._board_offset_y + self.board_widget.margin + row * step
        return x, y

    def _compute_global_from_local(self, seat: Player, local_row: int, local_col: int) -> tuple[int, int]:
        """依据各方局部坐标换算为棋盘全局(row,col)。局部坐标从1开始。"""
        if seat == Player.PLAYER1:  # 南方：行11-16、列6-10
            return (local_row + 10, local_col + 5)
        elif seat == Player.PLAYER3:  # 北方：行0-5、列6-10（180度）
            return (6 - local_row, 11 - local_col)
        elif seat == Player.PLAYER4:  # 东方：行6-10、列11-16（顺时针90度）
            return (11 - local_col, local_row + 10)
        elif seat == Player.PLAYER2:  # 西方：行6-10、列0-5（逆时针90度）
            return (local_col + 5, 6 - local_row)
        else:
            return (0, 0)

    def _position_avatar_frames(self):
        """将头像框放置到用户指定的空白区域：
        - 南：南3,5以东
        - 北：北3,5以西
        - 东：东6,3以东
        - 西：西6,3以西
        """
        if not hasattr(self, 'avatar_frames'):
            return
        gap = self.board_widget.cell_spacing + 16
        cell_size = self.board_widget.cell_size
        
        def place(seat: Player, local_rc: tuple[int, int], side: str):
            if seat not in self.avatar_frames:
                return
            row, col = self._compute_global_from_local(seat, local_rc[0], local_rc[1])
            x, y = self._cell_top_left_in_play_area(row, col)
            frame = self.avatar_frames[seat]
            fw = frame.width()
            fh = frame.height()
            if side == 'east':
                fx = x + cell_size + gap
            else:  # 'west'
                fx = x - gap - fw
            fy = y + cell_size // 2 - fh // 2
            frame.move(fx, fy)
        
        # 依据参考坐标放置四个头像框
        place(Player.PLAYER1, (3, 5), 'east')  # 南方
        place(Player.PLAYER3, (3, 5), 'west')  # 北方
        place(Player.PLAYER4, (6, 3), 'east')  # 东方
        place(Player.PLAYER2, (6, 3), 'west')  # 西方

    def _on_send_chat_clicked(self):
        """发送聊天：提交后锁定，等待广播解锁"""
        if not self.ws_connected_flag:
            QMessageBox.warning(self, "未连接", "尚未连接到服务器，稍后再试。")
            return
        if not self.me_chat_input:
            return
        text = (self.me_chat_input.text() or "").strip()
        if not text:
            self.status_bar.showMessage("请输入聊天内容再发送。")
            return
        # 锁定输入与按钮，待广播后解锁
        self.chat_locked = True
        self.me_chat_input.setEnabled(False)
        if self.me_chat_send_btn:
            self.me_chat_send_btn.setEnabled(False)
        self.status_bar.showMessage("聊天已提交，等待广播...")
        # 发送到服务器
        payload = {
            "type": "submit_chat",
            "text": text,
            "utterance_target": "all",
        }
        if self.ws_client:
            self.ws_client.send(payload)

    def _on_ws_connected(self):
        """WS连接建立后，启用聊天控件，并在需要时同步人物分配与开始游戏"""
        self.ws_connected_flag = True
        if self.me_chat_input:
            self.me_chat_input.setEnabled(True)
        if self.me_chat_send_btn:
            self.me_chat_send_btn.setEnabled(True)
        self.status_bar.showMessage("已连接服务器，聊天可用。")
        # 连接后优先发送人物分配（finalize_ai_assignments 已在 start_game 前完成）
        try:
            if self.ws_client:
                assignments = {}
                for seat, persona_key in self.seat_assignments.items():
                    if isinstance(persona_key, str) and persona_key != "random":
                        assignments[str(seat.value)] = persona_key
                if assignments:
                    self.ws_client.send({
                        "type": "set_persona_assignments",
                        "assignments": assignments,
                    })
        except Exception:
            pass
        # 如果当前已经进入对战阶段，通知服务器开始游戏（保证顺序在人物分配之后）
        try:
            if self.game_logic.game_state == GameState.PLAYING:
                if self.ws_client:
                    self.ws_client.send({"type": "start_game"})
        except Exception:
            pass

    def _on_chat_received_ack(self, data: dict):
        """服务器确认已收到聊天（不解锁，等待广播）"""
        self.status_bar.showMessage("服务器已收到聊天，稍后将广播。")

    def _on_chat_message_broadcast(self, data: dict):
        """服务器广播聊天后，解锁输入并清空"""
        self.chat_locked = False
        if self.me_chat_input:
            self.me_chat_input.clear()
            self.me_chat_input.setEnabled(True)
        if self.me_chat_send_btn:
            self.me_chat_send_btn.setEnabled(True)
        # 状态栏显示收到的聊天内容
        t = data.get("text")
        p = data.get("player_id")
        self.status_bar.showMessage(f"聊天广播：玩家{p}：{t}")

# === 全局控制模块（开发模式按钮显示/隐藏，统一放置文件末尾）===
# 将下方变量设为 False 可隐藏主界面上的“开发模式”按钮；设为 True 则显示。
DEV_MODE_BUTTON_VISIBLE = False