#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋棋盘模块
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum
from .piece import Piece, Player
# 已移除未使用的 .coords 边线/行线函数导入（本文件现全部使用硬编码坐标）

@dataclass
class Position:
    """棋盘位置"""
    row: int
    col: int
    
    def __hash__(self):
        return hash((self.row, self.col))
    
    def __eq__(self, other):
        if not isinstance(other, Position):
            return False
        return self.row == other.row and self.col == other.col

class CellType(Enum):
    """格子类型"""
    NORMAL = "普通"
    RAILWAY = "铁路"
    CAMP = "行营"
    HEADQUARTERS = "大本营"

@dataclass
class Cell:
    """棋盘格子"""
    position: Position
    cell_type: CellType = CellType.NORMAL
    piece: Optional[Piece] = None
    player_area: Optional[Player] = None

class Board:
    """四国军棋棋盘"""
    
    def __init__(self):
        # 棋盘总尺寸：17x17（包含4个6x5单人棋盘和中央5x5九宫格）
        self.rows = 17
        self.cols = 17
        self.cells: Dict[Position, Cell] = {}
        self.player_marks: Dict[Position, str] = {}
        
        self._setup_board()
    
    def _setup_board(self):
        """设置棋盘布局"""
        # 清空棋盘
        self.cells.clear()
        
        # 设置中央九宫格（5x5，只显示奇数位置）
        self._setup_center_grid()
        
        # 设置四个玩家区域
        self._setup_player_areas()
        
        # 设置铁路连接
        self._setup_railways()
        
        # 设置行营和大本营
        self._setup_camps()
        self._setup_headquarters()
    
    def _setup_player_areas(self):
        """设置四个玩家的棋盘区域，确保与中央九宫格有间隔"""
        # 玩家1（正南，下方）：6行5列，行11-16，列6-10
        self._create_south_player_area(Player.PLAYER1, 11, 6)
        
        # 玩家2（正西，左方）：旋转90°后变成5行6列，行6-10，列0-5
        self._create_west_player_area(Player.PLAYER2, 6, 0)
        
        # 玩家3（正北，上方）：旋转180°后仍是6行5列，行0-5，列6-10
        self._create_north_player_area(Player.PLAYER3, 0, 6)
        
        # 玩家4（正东，右方）：旋转90°后变成5行6列，行6-10，列11-16
        self._create_east_player_area(Player.PLAYER4, 6, 11)
    
    def _create_player_area(self, player: Player, start_row: int, end_row: int, start_col: int, end_col: int):
        """创建单个玩家的6x5棋盘区域"""
        # 根据玩家位置确定棋盘朝向
        if player == Player.PLAYER1:  # 南方玩家，棋盘朝北
            self._create_south_player_area(player, start_row, start_col)
        elif player == Player.PLAYER2:  # 西方玩家，棋盘朝东
            self._create_west_player_area(player, start_row, start_col)
        elif player == Player.PLAYER3:  # 北方玩家，棋盘朝南
            self._create_north_player_area(player, start_row, start_col)
        elif player == Player.PLAYER4:  # 东方玩家，棋盘朝西
            self._create_east_player_area(player, start_row, start_col)
    
    def _create_south_player_area(self, player: Player, start_row: int, start_col: int):
        """创建南方玩家区域（棋盘朝北，第一排在上方）"""
        for r in range(6):
            for c in range(5):
                position = Position(start_row + r, start_col + c)
                cell_type = self._get_cell_type_for_player_area(r, c)
                cell = Cell(position=position, cell_type=cell_type, player_area=player)
                self.cells[position] = cell
    
    def _create_west_player_area(self, player: Player, start_row: int, start_col: int):
        """创建西方玩家区域（将南方玩家棋盘逆时针旋转90°）"""
        # 西方玩家棋盘是5行6列
        for r in range(5):  # 新的行
            for c in range(6):  # 新的列
                position = Position(start_row + r, start_col + c)
                cell_type = self._get_west_cell_type(r, c)
                cell = Cell(position=position, cell_type=cell_type, player_area=player)
                self.cells[position] = cell
    
    def _create_north_player_area(self, player: Player, start_row: int, start_col: int):
        """创建北方玩家区域（将南方玩家棋盘旋转180°）"""
        # 北方玩家棋盘是6行5列
        for r in range(6):  # 新的行
            for c in range(5):  # 新的列
                position = Position(start_row + r, start_col + c)
                cell_type = self._get_north_cell_type(r, c)
                cell = Cell(position=position, cell_type=cell_type, player_area=player)
                self.cells[position] = cell
    
    def _create_east_player_area(self, player: Player, start_row: int, start_col: int):
        """创建东方玩家区域（将南方玩家棋盘顺时针旋转90°）"""
        # 东方玩家棋盘是5行6列
        for r in range(5):  # 新的行
            for c in range(6):  # 新的列
                position = Position(start_row + r, start_col + c)
                cell_type = self._get_east_cell_type(r, c)
                cell = Cell(position=position, cell_type=cell_type, player_area=player)
                self.cells[position] = cell
    
    # 全局棋盘模板 - 基于南方玩家的正确布局
    @staticmethod
    def _get_player_area_template():
        """返回玩家区域的标准模板布局"""
        return {
            'railway_positions': {
                (1,1), (1,2), (1,3), (1,4), (1,5),  # 第1行全部
                (5,1), (5,2), (5,3), (5,4), (5,5),  # 第5行全部
                (2,1), (3,1), (4,1),                # 第1列（除第1、5、6行）
                (2,5), (3,5), (4,5)                 # 第5列（除第1、5、6行）
            },
            'camp_positions': {
                (2,2), (2,4), (3,3), (4,2), (4,4)
            },
            'headquarters_positions': {
                (6,2), (6,4)
            }
        }
    
    def _get_cell_type_by_template(self, local_row: int, local_col: int, player: Player) -> CellType:
        """使用全局模板根据玩家区域内的本地坐标确定格子类型。
        做法：把各玩家的本地坐标逆映射到“南方模板坐标”，用同一套模板判定。"""
        # 转换为1基索引（输入是0基）
        r = local_row + 1
        c = local_col + 1

        # 逆映射到南方模板坐标 (rs, cs)
        # 南方模板尺寸：6行x5列 -> rs∈[1..6], cs∈[1..5]
        if player == Player.PLAYER1:  # 南方：无需变换
            rs, cs = r, c
        elif player == Player.PLAYER2:  # 西方：南方棋盘逆时针90°后的坐标 -> 逆变换为顺时针90°
            # 西方本地(5x6)：(rw, cw) -> 南方模板(rs, cs)
            # 正向(南->西)：(rs, cs) -> (rw=cs, cw=6-rs+1)
            # 逆向(西->南)：rs = 6 - c + 1, cs = r
            rs, cs = 6 - c + 1, r
        elif player == Player.PLAYER3:  # 北方：南方棋盘旋转180° -> 逆变换仍是180°
            # 北方本地(6x5)：(rw, cw) -> 南方模板(rs, cs)
            # 正向(南->北)：(rs, cs) -> (rw=6-rs+1, cw=5-cs+1)
            # 逆向(北->南)：rs = 6 - r + 1, cs = 5 - c + 1
            rs, cs = 6 - r + 1, 5 - c + 1
        elif player == Player.PLAYER4:  # 东方：南方棋盘顺时针90° -> 逆变换为逆时针90°
            # 东方本地(5x6)：(rw, cw) -> 南方模板(rs, cs)
            # 正向(南->东)：(rs, cs) -> (rw=5-cs+1, cw=rs)
            # 逆向(东->南)：rs = c, cs = 5 - r + 1
            rs, cs = c, 5 - r + 1
        else:
            rs, cs = r, c

        template = self._get_player_area_template()

        if (rs, cs) in template['railway_positions']:
            return CellType.RAILWAY
        if (rs, cs) in template['camp_positions']:
            return CellType.CAMP
        if (rs, cs) in template['headquarters_positions']:
            return CellType.HEADQUARTERS
        return CellType.NORMAL
    
    def _get_cell_type_for_player_area(self, local_row: int, local_col: int) -> CellType:
        """根据玩家区域内的本地坐标确定格子类型（南方玩家）- 使用全局模板"""
        return self._get_cell_type_by_template(local_row, local_col, Player.PLAYER1)
        
    def print_board_coordinates(self):
        """打印整个棋盘的坐标信息，用于开发调试和配置"""
        print("=== 军棋棋盘坐标映射 ===")
        print("格式: 全局坐标(row,col) -> 阵营:本地坐标(local_row,local_col) [格子类型]")
        print()
        
        # 按行遍历整个棋盘
        for global_row in range(17):  # 0-16行
            for global_col in range(17):  # 0-16列
                pos = Position(global_row, global_col)
                if pos in self.cells:
                    cell = self.cells[pos]
                    
                    # 确定阵营和本地坐标
                    if cell.player_area is None:
                        # 中央九宫格
                        if 6 <= global_row <= 10 and 6 <= global_col <= 10:
                            # 转换为九宫格本地坐标（1-5）
                            center_row = (global_row - 6) // 2 + 1
                            center_col = (global_col - 6) // 2 + 1
                            area_info = f"中央:{center_row},{center_col}"
                        else:
                            area_info = "未知区域"
                    else:
                        # 玩家区域
                        player_name = {
                            Player.PLAYER1: "南方",
                            Player.PLAYER2: "西方", 
                            Player.PLAYER3: "北方",
                            Player.PLAYER4: "东方"
                        }.get(cell.player_area, "未知")
                        
                        # 计算本地坐标
                        if cell.player_area == Player.PLAYER1:  # 南方
                            local_row = global_row - 11 + 1  # 转换为1-6
                            local_col = global_col - 6 + 1   # 转换为1-5
                        elif cell.player_area == Player.PLAYER2:  # 西方
                            local_row = global_row - 6 + 1   # 转换为1-5
                            local_col = global_col - 0 + 1   # 转换为1-6
                        elif cell.player_area == Player.PLAYER3:  # 北方
                            local_row = global_row - 0 + 1   # 转换为1-6
                            local_col = global_col - 6 + 1   # 转换为1-5
                        elif cell.player_area == Player.PLAYER4:  # 东方
                            local_row = global_row - 6 + 1   # 转换为1-5
                            local_col = global_col - 11 + 1  # 转换为1-6
                        else:
                            local_row = local_col = 0
                            
                        area_info = f"{player_name}:{local_row},{local_col}"
                    
                    # 格子类型
                    cell_type_name = {
                        CellType.NORMAL: "普通",
                        CellType.RAILWAY: "铁路",
                        CellType.CAMP: "行营",
                        CellType.HEADQUARTERS: "大本营"
                    }.get(cell.cell_type, "未知")
                    
                    print(f"({global_row:2d},{global_col:2d}) -> {area_info:8s} [{cell_type_name}]")
        
        print("\n=== 坐标映射完成 ===")
        print("请复制上述信息，告诉我需要修改的位置和类型")
    
    def _get_west_cell_type(self, local_row: int, local_col: int) -> CellType:
        """根据西方玩家区域内的本地坐标确定格子类型（5行6列）- 使用全局模板"""
        return self._get_cell_type_by_template(local_row, local_col, Player.PLAYER2)
    
    def _get_north_cell_type(self, local_row: int, local_col: int) -> CellType:
        """根据北方玩家区域内的本地坐标确定格子类型（6行5列）- 使用全局模板"""
        return self._get_cell_type_by_template(local_row, local_col, Player.PLAYER3)
    
    def _get_east_cell_type(self, local_row: int, local_col: int) -> CellType:
        """根据东方玩家区域内的本地坐标确定格子类型（5行6列）- 使用全局模板"""
        return self._get_cell_type_by_template(local_row, local_col, Player.PLAYER4)
    
    def _setup_center_grid(self):
        """设置中央5x5九宫格，只显示奇数位置"""
        center_start_row = 6
        center_start_col = 6
        
        # 5x5网格，只创建奇数位置的格子（形成九宫格）
        for r in range(5):
            for c in range(5):
                # 只在奇数位置创建格子
                if r % 2 == 0 and c % 2 == 0:
                    position = Position(center_start_row + r, center_start_col + c)
                    cell = Cell(
                        position=position,
                        cell_type=CellType.RAILWAY,  # 九宫格全部是铁路
                        player_area=None  # 中央区域不属于任何玩家
                    )
                    self.cells[position] = cell
    
    # 移除重复的占位实现，统一在下方的实现中设置铁路
    
    def _setup_railways(self):
        """设置铁路线 - 保持与棋盘模板一致，避免多余覆盖"""
        # 精确标记中央九宫格九个节点为铁路（与绘制坐标一致）
        center_positions = [
            Position(6, 6), Position(6, 8), Position(6, 10),
            Position(8, 6), Position(8, 8), Position(8, 10),
            Position(10, 6), Position(10, 8), Position(10, 10),
        ]
        for pos in center_positions:
            if pos in self.cells:
                self.cells[pos].cell_type = CellType.RAILWAY
        # 按你的要求移除“过渡点”补丁，仅保留九宫格本身的铁路节点。
    
    def _setup_camps(self):
        """设置行营位置 - 已禁用，由各玩家区域方法直接控制"""
        # 注释掉全局行营设置，避免覆盖各玩家区域中写死的坐标
        pass
    
    def _setup_headquarters(self):
        """设置大本营位置 - 已禁用，由各玩家区域方法直接控制"""
        # 注释掉全局大本营设置，避免覆盖各玩家区域中写死的坐标
        pass
    
    def get_player_area(self, player: Player) -> List[Position]:
        """获取玩家的棋子摆放区域（允许普通格、铁路和大本营；禁止行营）"""
        positions = []
        for position, cell in self.cells.items():
            if cell.player_area == player and cell.cell_type != CellType.CAMP:
                positions.append(position)
        return positions
    
    def get_cell(self, position: Position) -> Optional[Cell]:
        """获取指定位置的格子"""
        return self.cells.get(position)
    
    def place_piece(self, position: Position, piece: Piece) -> bool:
        """在指定位置放置棋子"""
        cell = self.get_cell(position)
        if cell and cell.piece is None:
            cell.piece = piece
            return True
        return False
    
    def remove_piece(self, position: Position) -> Optional[Piece]:
        """移除指定位置的棋子"""
        cell = self.get_cell(position)
        if cell and cell.piece:
            piece = cell.piece
            cell.piece = None
            # 清除该位置的标记（标记面向棋子）
            if position in self.player_marks:
                del self.player_marks[position]
            return piece
        return None
    
    def get_adjacent_positions(self, position: Position) -> List[Position]:
        """获取相邻位置，包含玩家棋盘与中央九宫格的特殊连接规则"""
        adjacent = []
        # 对中央九宫格使用步长为2的横竖邻接（节点相距2格）
        center_rows = range(6, 11)  # 6-10行
        center_cols = range(6, 11)  # 6-10列
        is_center_node = (
            position.row in center_rows and position.col in center_cols and
            position.row % 2 == 0 and position.col % 2 == 0
        )

        # 中央九宫格节点：
        # - 内部相邻采用步长2（只连接九宫格内的偶数节点）
        # - 同时允许朝玩家区域方向采用步长1，以便与玩家棋盘入口连接
        if is_center_node:
            directions = [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, 0), (1, 0), (0, -1), (0, 1)]
        else:
            directions = [(-1, 0), (1, 0), (0, -1), (0, 1)]  # 上下左右
        

        for dr, dc in directions:
            new_row = position.row + dr
            new_col = position.col + dc
            new_pos = Position(new_row, new_col)
            
            # 检查新位置是否在棋盘上
            if new_pos in self.cells:
                # 检查是否为玩家棋盘与中央九宫格之间的连接
                if self._is_valid_player_center_connection(position, new_pos):
                    adjacent.append(new_pos)

        # 额外规则：行营允许与其四个对角位置互相邻接（仅限同一玩家区域）
        # 说明：界面为行营绘制了八方向细线，但之前未纳入移动逻辑，导致角上位置无法进入行营。
        # 这里将行营与四个对角位置视为相邻，满足原棋规。
        camp_diagonals = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
        cur_cell = self.get_cell(position)
        if cur_cell:
            for dr, dc in camp_diagonals:
                diag_pos = Position(position.row + dr, position.col + dc)
                if diag_pos in self.cells:
                    diag_cell = self.get_cell(diag_pos)
                    if not diag_cell:
                        continue
                    # 只在两格之一为行营，且两者属于同一玩家区域时，添加邻接
                    if (cur_cell.cell_type == CellType.CAMP or diag_cell.cell_type == CellType.CAMP):
                        if cur_cell.player_area is not None and cur_cell.player_area == diag_cell.player_area:
                            # 避免重复加入
                            if diag_pos not in adjacent:
                                adjacent.append(diag_pos)

        return adjacent
    
    def _is_valid_player_center_connection(self, pos1: Position, pos2: Position) -> bool:
        """检查两个位置之间的连接是否有效，特别处理玩家棋盘与中央九宫格的连接以及玩家间连接"""
        # 中央九宫格的范围
        center_rows = range(6, 11)  # 6-10行
        center_cols = range(6, 11)  # 6-10列
        
        # 判断是否为中央九宫格位置
        def is_center_position(pos):
            return pos.row in center_rows and pos.col in center_cols
        
        # 判断是否为玩家区域位置
        def is_player_area_position(pos):
            return pos in self.cells and self.cells[pos].player_area is not None
        
        # 如果两个位置都不涉及中央九宫格和玩家区域的边界，则允许连接
        if not (is_center_position(pos1) or is_center_position(pos2)):
            # 检查是否为相邻玩家之间的特殊连接
            if is_player_area_position(pos1) and is_player_area_position(pos2):
                player1 = self.cells[pos1].player_area
                player2 = self.cells[pos2].player_area
                
                # 如果是同一个玩家区域，允许连接
                if player1 == player2:
                    return True
                
                # 检查相邻玩家之间的特殊连接
                return self._is_valid_inter_player_connection(pos1, pos2, player1, player2)
            
            return True
        
        # 如果两个位置都在中央九宫格内，则只允许横竖连接
        if is_center_position(pos1) and is_center_position(pos2):
            # 检查是否为相邻位置（只允许上下左右）
            row_diff = abs(pos1.row - pos2.row)
            col_diff = abs(pos1.col - pos2.col)
            
            # 只允许直接相邻（上下左右），不允许对角连接
            if (row_diff == 2 and col_diff == 0) or (row_diff == 0 and col_diff == 2):
                return True
        
        # 如果两个位置都在同一个玩家区域内，则允许连接
        if (is_player_area_position(pos1) and is_player_area_position(pos2) and
            self.cells[pos1].player_area == self.cells[pos2].player_area):
            return True
        
        # 处理玩家区域与中央九宫格之间的连接
        center_pos = pos1 if is_center_position(pos1) else pos2
        player_pos = pos2 if is_center_position(pos1) else pos1
        
        if is_center_position(center_pos) and is_player_area_position(player_pos):
            player_area = self.cells[player_pos].player_area
            
            # 南方玩家（PLAYER1）：只允许1、3、5列连接
            if player_area == Player.PLAYER1:
                # 南方玩家第一排是行11，只有列6、8、10（对应1、3、5列）可以连接到九宫格
                return (player_pos.row == 11 and player_pos.col in [6, 8, 10] and
                        center_pos.row == 10 and center_pos.col == player_pos.col)
            
            # 北方玩家（PLAYER3）：只允许1、3、5列连接
            elif player_area == Player.PLAYER3:
                # 北方玩家第一排是行5，只有列6、8、10（对应1、3、5列）可以连接到九宫格
                return (player_pos.row == 5 and player_pos.col in [6, 8, 10] and
                        center_pos.row == 6 and center_pos.col == player_pos.col)
            
            # 西方玩家（PLAYER2）：只允许1、3、5行连接
            elif player_area == Player.PLAYER2:
                # 西方玩家第一列是列5，只有行6、8、10（对应1、3、5行）可以连接到九宫格
                return (player_pos.col == 5 and player_pos.row in [6, 8, 10] and
                        center_pos.col == 6 and center_pos.row == player_pos.row)
            
            # 东方玩家（PLAYER4）：只允许1、3、5行连接
            elif player_area == Player.PLAYER4:
                # 东方玩家第一列是列11，只有行6、8、10（对应1、3、5行）可以连接到九宫格
                return (player_pos.col == 11 and player_pos.row in [6, 8, 10] and
                        center_pos.col == 10 and center_pos.row == player_pos.row)
        
        # 默认不允许连接
        return False
    
    def _is_valid_inter_player_connection(self, pos1: Position, pos2: Position, player1: Player, player2: Player) -> bool:
        """检查相邻玩家之间的特殊连接"""
        # 南方玩家与西方玩家的连接：南方(1,1) <-> 西方(1,5)
        if ((player1 == Player.PLAYER1 and player2 == Player.PLAYER2) or
            (player1 == Player.PLAYER2 and player2 == Player.PLAYER1)):
            # 南方玩家的(1,1)位置：行11，列6
            # 西方玩家的(1,5)位置：行10，列5（按界面坐标映射）
            south_pos = pos1 if player1 == Player.PLAYER1 else pos2
            west_pos = pos2 if player1 == Player.PLAYER1 else pos1
            return (south_pos.row == 11 and south_pos.col == 6 and
                    west_pos.row == 10 and west_pos.col == 5)
        
        # 南方玩家与东方玩家的连接：南方(1,5) <-> 东方(1,1)
        if ((player1 == Player.PLAYER1 and player2 == Player.PLAYER4) or
            (player1 == Player.PLAYER4 and player2 == Player.PLAYER1)):
            # 南方玩家的(1,5)位置：行11，列10
            # 东方玩家的(1,1)位置：行6，列11
            south_pos = pos1 if player1 == Player.PLAYER1 else pos2
            east_pos = pos2 if player1 == Player.PLAYER1 else pos1
            return (south_pos.row == 11 and south_pos.col == 10 and
                    east_pos.row == 6 and east_pos.col == 11)
        
        # 北方玩家与西方玩家的连接：北方(1,1) <-> 西方(5,1)
        if ((player1 == Player.PLAYER3 and player2 == Player.PLAYER2) or
            (player1 == Player.PLAYER2 and player2 == Player.PLAYER3)):
            # 北方玩家的(1,1)位置：行5，列10（修正）
            # 西方玩家的(5,1)位置：行10，列5
            north_pos = pos1 if player1 == Player.PLAYER3 else pos2
            west_pos = pos2 if player1 == Player.PLAYER3 else pos1
            return (north_pos.row == 5 and north_pos.col == 10 and
                    west_pos.row == 10 and west_pos.col == 5)
        
        # 北方玩家与东方玩家的连接：北方(1,5) <-> 东方(5,1)
        if ((player1 == Player.PLAYER3 and player2 == Player.PLAYER4) or
            (player1 == Player.PLAYER4 and player2 == Player.PLAYER3)):
            # 北方玩家的(1,5)位置：行5，列6（修正）
            # 东方玩家的(5,1)位置：行10，列11
            north_pos = pos1 if player1 == Player.PLAYER3 else pos2
            east_pos = pos2 if player1 == Player.PLAYER3 else pos1
            return (north_pos.row == 5 and north_pos.col == 6 and
                    east_pos.row == 10 and east_pos.col == 11)

        # 额外直连：西方(1,1) <-> 北方(1,5)
        if ((player1 == Player.PLAYER2 and player2 == Player.PLAYER3) or
            (player1 == Player.PLAYER3 and player2 == Player.PLAYER2)):
            west_pos = pos1 if player1 == Player.PLAYER2 else pos2
            north_pos = pos2 if player1 == Player.PLAYER2 else pos1
            # 修正：西方(1,1) -> 全局(6,5)；北方(1,5) -> 全局(5,6)
            if (west_pos.row == 6 and west_pos.col == 5 and
                north_pos.row == 5 and north_pos.col == 6):
                return True

        return False
    
    def get_railway_connected_positions(self, position: Position) -> Set[Position]:
        """获取工兵在铁路上所有可达的位置（可自由拐弯）"""
        from collections import deque

        start_cell = self.get_cell(position)
        if not start_cell or start_cell.cell_type != CellType.RAILWAY:
            return set()

        q = deque([position])
        visited = {position}
        reachable = set()

        while q:
            current = q.popleft()

            # 角点跨玩家铁路直连（尊重占用：友军阻挡；敌方为终点但不扩展；空格入队）
            corner_links = [
                (Position(6, 5), Position(5, 6)),  # 西1,1 <-> 北1,5
                (Position(10, 5), Position(11, 6)),  # 西1,5 <-> 南1,1
                (Position(11, 10), Position(10, 11)),  # 南1,5 <-> 东1,1
                (Position(6, 11), Position(5, 10)),  # 东1,5 <-> 北1,1
            ]
            def try_bridge(to_pos: Position):
                if to_pos in visited:
                    return
                to_cell = self.get_cell(to_pos)
                if not to_cell or to_cell.cell_type != CellType.RAILWAY:
                    return
                visited.add(to_pos)
                if to_cell.piece:
                    if start_cell.piece and not self._are_allied(to_cell.piece.player, start_cell.piece.player):
                        reachable.add(to_pos)
                    # 无论敌我，都不入队继续扩展
                    return
                # 空铁路格：加入可达并继续扩展
                reachable.add(to_pos)
                q.append(to_pos)

            for a, b in corner_links:
                if current == a:
                    try_bridge(b)
                elif current == b:
                    try_bridge(a)

            for adj in self.get_adjacent_positions(current):
                if adj in visited:
                    continue

                adj_cell = self.get_cell(adj)
                if not adj_cell or adj_cell.cell_type != CellType.RAILWAY:
                    continue

                visited.add(adj)

                # 如果邻接点有棋子
                if adj_cell.piece:
                    # 如果是敌方棋子，加入可达列表，但不再从此扩展
                    if start_cell.piece and not self._are_allied(adj_cell.piece.player, start_cell.piece.player):
                        reachable.add(adj)
                    # 不论敌我，都不能穿越，故不加入队列
                    continue

                # 如果是空铁路格，加入可达列表并继续扩展
                reachable.add(adj)
                q.append(adj)

        return reachable

    def _is_corner_straight_pair(self, a: Position, b: Position) -> bool:
        """四个跨玩家角点连线视为直线"""
        corner_pairs = {
            (Position(6, 5), Position(5, 6)),   # 西1,1 <-> 北1,5
            (Position(10, 5), Position(11, 6)), # 西1,5 <-> 南1,1
            (Position(11, 10), Position(10, 11)), # 南1,5 <-> 东1,1
            (Position(6, 11), Position(5, 10)), # 东1,5 <-> 北1,1
        }
        return (a, b) in corner_pairs or (b, a) in corner_pairs

    def get_railway_straight_reachable_positions(self, position: Position) -> Set[Position]:
        """非工兵直线铁路移动：
        - 仅沿一个轴向（横或纵）延伸，不允许拐弯；
        - 在靠近九宫格的固定角点处，且“起点属于该角点所在边线五个点之一”时，允许一次硬编码桥接到对侧边线五个点；
        - 桥接最多一次；
        - 阻挡规则：友方阻挡停止；首个敌方为终点且停止。
        """
        start_cell = self.get_cell(position)
        if not start_cell or start_cell.cell_type != CellType.RAILWAY:
            return set()
        reachable: Set[Position] = set()
        # 角点（靠近九宫格的四个方向，两端各一个）
        south_left_corner = Position(11, 6)   # 南(1,1)角
        south_right_corner = Position(11, 10) # 南(1,5)角
        north_left_corner = Position(5, 6)    # 北(1,5)角
        north_right_corner = Position(5, 10)    # 北(1,1)角
        west_top_corner = Position(6, 5)      # 西(1,1)角
        west_bottom_corner = Position(10, 5)  # 西(1,5)角
        east_top_corner = Position(6, 11)     # 东(1,5)角
        east_bottom_corner = Position(10, 11) # 东(1,1)角

        # 已移除对 .coords 的依赖，相关边线在 get_railway_straight_reachable_positions 内硬编码
        def to_positions(points: List[Tuple[int, int]]) -> List[Position]:
            return [Position(r, c) for r, c in points]

        # === 边线五点（硬编码）===
        # 南侧：局部列1/5 -> 全局列6/10，行11..15
        south_left_edge = [
            Position(11, 6), Position(12, 6), Position(13, 6), Position(14, 6), Position(15, 6)
        ]
        south_right_edge = [
            Position(11, 10), Position(12, 10), Position(13, 10), Position(14, 10), Position(15, 10)
        ]
        # 北侧：局部列1/5 -> 全局列10/6，行5..1（从靠角的一端开始）
        north_1_edge = [
            Position(5, 10), Position(4, 10), Position(3, 10), Position(2, 10), Position(1, 10)
        ]
        north_5_edge = [
            Position(5, 6), Position(4, 6), Position(3, 6), Position(2, 6), Position(1, 6)
        ]
        # 西侧靠九宫格（局部列=5）与远侧（局部列=1）
        west_edge_five = [
            Position(10, 5), Position(10, 4), Position(10, 3), Position(10, 2), Position(10, 1)
        ]
        west_edge_one = [
            Position(6, 5), Position(6, 4), Position(6, 3), Position(6, 2), Position(6, 1)
        ]
        # 东侧靠九宫格（局部列=1）与远侧（局部列=5）
        east_edge_one = [
            Position(10, 11), Position(10, 12), Position(10, 13), Position(10, 14), Position(10, 15)
        ]
        east_edge_five = [
            Position(6, 11), Position(6, 12), Position(6, 13), Position(6, 14), Position(6, 15)
        ]
        
        # 起点属于哪条边线，决定在哪个角点允许桥接（硬编码）
        allowed_sources_by_corner: Dict[Position, List[Position]] = {
            south_left_corner: south_left_edge,
            south_right_corner: south_right_edge,
            # 注意：north_left_corner=(5, 6) 是“北(1,5)”角；north_right_corner=(5, 10) 是“北(1,1)”角
            north_left_corner: north_5_edge,
            north_right_corner: north_1_edge,
            west_top_corner: west_edge_one,
            west_bottom_corner: west_edge_five,
            east_top_corner: east_edge_five,
            east_bottom_corner: east_edge_one,
        }

        # 每个角点桥接到“对侧边线五点”（硬编码）
        bridge_targets_by_corner: Dict[Position, List[Position]] = {
            # 南(1,1)角 -> 西（1..5,5）：行10，列5..1
            south_left_corner: [Position(10, 5), Position(10, 4), Position(10, 3), Position(10, 2), Position(10, 1)],
            # 南(1,5)角 -> 东（1..5,1）：行10，列11..15
            south_right_corner: [Position(10, 11), Position(10, 12), Position(10, 13), Position(10, 14), Position(10, 15)],
            # 北(1,5)角 -> 西（1..5,5）：列5，行6..10（竖向）
            north_left_corner: [Position(6, 5), Position(6, 4), Position(6, 3), Position(6, 2), Position(6, 1)],
            # 北(1,1)角 -> 东（1..5,1）：列11，行6..10（竖向）
            north_right_corner: [Position(6, 11), Position(6, 12), Position(6, 13), Position(6, 14), Position(6, 15)],
            # 西(1,1)角 -> 北（1..5,5）：列6，行5..1
            west_top_corner: [Position(5, 6), Position(4, 6), Position(3, 6), Position(2, 6), Position(1, 6)],
            # 西(1,5)角 -> 南（1..5,1）：列6，行11..15
            west_bottom_corner: [Position(11, 6), Position(12, 6), Position(13, 6), Position(14, 6), Position(15, 6)],
            # 东(1,5)角 -> 北（1..5,1）：列10，行5..1
            east_top_corner: [Position(5, 10), Position(4, 10), Position(3, 10), Position(2, 10), Position(1, 10)],
            # 东(1,1)角 -> 北（1..5,5）：列10，行11..15
            east_bottom_corner: [Position(11, 10), Position(12, 10), Position(13, 10), Position(14, 10), Position(15, 10)],
        }

        def add_step(next_pos: Position) -> Tuple[bool, bool]:
            """返回 (should_stop, added)
            - 有敌：加入并停止 -> (True, True)
            - 友方：不加入并停止 -> (True, False)
            - 空铁路：加入并继续 -> (False, True)
            - 非铁路：停止 -> (True, False)
            """
            cell = self.get_cell(next_pos)
            if not cell or cell.cell_type != CellType.RAILWAY:
                return True, False
            if cell.piece:
                if start_cell.piece and not self._are_allied(cell.piece.player, start_cell.piece.player):
                    reachable.add(next_pos)
                    return True, True
                return True, False
            reachable.add(next_pos)
            return False, True

        def next_along_axis(curr: Position, axis: str, sign: int, prev: Optional[Position]) -> Optional[Position]:
            """在相同轴向上按给定方向找到最近的相邻铁路节点（兼容中心±2步）。"""
            candidates: List[Position] = []
            for adj in self.get_adjacent_positions(curr):
                adj_cell = self.get_cell(adj)
                if not adj_cell or adj_cell.cell_type != CellType.RAILWAY:
                    continue
                if axis == 'h' and adj.row == curr.row:
                    dc = adj.col - curr.col
                    if (sign > 0 and dc > 0) or (sign < 0 and dc < 0):
                        if prev is None or adj != prev:
                            candidates.append(adj)
                elif axis == 'v' and adj.col == curr.col:
                    dr = adj.row - curr.row
                    if (sign > 0 and dr > 0) or (sign < 0 and dr < 0):
                        if prev is None or adj != prev:
                            candidates.append(adj)
            if not candidates:
                return None
            # 选择最近的一个（中心相邻为±2步，普通为±1步）
            if axis == 'h':
                candidates.sort(key=lambda p: abs(p.col - curr.col))
            else:
                candidates.sort(key=lambda p: abs(p.row - curr.row))
            return candidates[0]

        def perform_bridge(curr: Position) -> bool:
            """在硬编码角点执行桥接，返回是否已桥接。
            要求：起点属于该角点对应的边线五点之一；仅在到达该角点时触发；
            行为：从对侧边线靠角点的那个点开始，按远离九宫格方向依次 add_step，遵守阻挡后停止。
            """
            # 找到符合当前角点的起点边线
            if curr not in allowed_sources_by_corner:
                return False
            source_edge = allowed_sources_by_corner[curr]
            # 仅当“起点属于该角点的边线五点之一”时，才允许桥接（角点本身也在边线五点集合中）
            if position not in source_edge and position != curr:
                return False
            targets = bridge_targets_by_corner.get(curr, [])
            for t in targets:
                stop, _ = add_step(t)
                if stop:
                    # 首次遇到阻挡或敌人即停止，不再向更远处延伸
                    return True
            return True

        def scan_axis(axis: str, sign: int):
            curr = position
            prev: Optional[Position] = None
            bridged = False
            # 仅当“起点属于某个角点的源边线五点集合”时，本次移动才允许桥接；不依赖轴向
            can_bridge = any(
                position in edge for edge in allowed_sources_by_corner.values()
            )
            # 若起点本身就在角点（且属于该角的源边线），先尝试桥接
            if can_bridge and not bridged:
                if perform_bridge(curr):
                    # 桥接一次后，仍继续沿当前轴向扫描，以保持南北直线贯通
                    bridged = True

            while True:
                nxt = next_along_axis(curr, axis, sign, prev)
                if nxt is None:
                    break
                stop, _ = add_step(nxt)
                if stop:
                    break
                prev, curr = curr, nxt

                # 在到达角点后，若尚未桥接，尝试一次桥接
                if can_bridge and not bridged:
                    if perform_bridge(curr):
                        bridged = True
                        # 桥接后继续当前轴扫描，保证贯通直线不中断

        # 横向左右各扫描（不允许桥接）；纵向上下各扫描（允许一次桥接）
        scan_axis('h', +1)
        scan_axis('h', -1)
        scan_axis('v', +1)
        scan_axis('v', -1)

        return reachable
    
    def can_move(self, from_pos: Position, to_pos: Position) -> bool:
        """检查是否可以移动"""
        from_cell = self.get_cell(from_pos)
        to_cell = self.get_cell(to_pos)
        
        if not from_cell or not to_cell or not from_cell.piece:
            return False
        
        piece = from_cell.piece
        
        # 任何进入大本营的棋子不能再移动
        if from_cell.cell_type == CellType.HEADQUARTERS:
            return False
        
        # 地雷和军旗不能移动
        if piece.is_mine() or piece.is_flag():
            return False
        
        # 目标位置不能有友方（含己方）棋子
        if to_cell.piece and self._are_allied(to_cell.piece.player, piece.player):
            return False
        
        # 行营内的棋子不能被攻击，但可以移出
        if to_cell.piece and to_cell.cell_type == CellType.CAMP:
            return False
        
        # 铁路移动规则
        if from_cell.cell_type == CellType.RAILWAY and to_cell.cell_type == CellType.RAILWAY:
            if piece.is_engineer():
                # 工兵：可在任意连接起来的铁路上拐弯，遵守阻挡/首敌停
                connected_positions = self.get_railway_connected_positions(from_pos)
                return to_pos in connected_positions
            else:
                # 其他棋子：只能沿一条直线行进（包含四个拐角直线对），遵守阻挡/首敌停
                straight_positions = self.get_railway_straight_reachable_positions(from_pos)
                return to_pos in straight_positions
        
        # 普通移动：只能移动到相邻位置
        return to_pos in self.get_adjacent_positions(from_pos)
    
    def move_piece(self, from_pos: Position, to_pos: Position) -> bool:
        """移动棋子"""
        if not self.can_move(from_pos, to_pos):
            return False
        
        from_cell = self.get_cell(from_pos)
        to_cell = self.get_cell(to_pos)
        
        moving_piece = from_cell.piece
        target_piece = to_cell.piece
        
        # 标记处理：记录起止位置是否有标记（标记面向棋子，随棋子移动/死亡而清除）
        had_from_mark = from_pos in self.player_marks
        had_to_mark = to_pos in self.player_marks

        # 记录在此次移动/战斗中死亡的司令所属玩家集合
        dead_commander_players: Set[Player] = set()

        # 处理战斗
        if target_piece:
            winner = self._battle(moving_piece, target_piece)
            if winner == moving_piece:
                # 攻击方获胜
                # 若防守方是司令，记录该司令所属玩家
                if target_piece.piece_type.name == 'COMMANDER':
                    dead_commander_players.add(target_piece.player)
                # 战绩：攻击方继承防守方战绩并+1
                moving_piece.kill_count += (target_piece.kill_count + 1)
                to_cell.piece = moving_piece
                from_cell.piece = None
                # 移动方的标记随棋子移动到新位置；防守方棋子死亡，其标记清除
                if had_from_mark:
                    self.player_marks[to_pos] = self.player_marks.pop(from_pos)
                if had_to_mark:
                    # 防守方死亡，清除其标记
                    del self.player_marks[to_pos]
            elif winner == target_piece:
                # 防守方获胜（攻击方死亡）
                # 若攻击方是司令，记录该司令所属玩家
                if moving_piece.piece_type.name == 'COMMANDER':
                    dead_commander_players.add(moving_piece.player)
                # 战绩：防守方继承攻击方战绩并+1
                target_piece.kill_count += (moving_piece.kill_count + 1)
                from_cell.piece = None
                # 攻击方死亡，清除其标记；防守方存活，其标记保留
                if had_from_mark:
                    del self.player_marks[from_pos]
            else:
                # 同归于尽（双方同时死亡）
                # 若任一方为司令，记录对应玩家
                if moving_piece.piece_type.name == 'COMMANDER':
                    dead_commander_players.add(moving_piece.player)
                if target_piece.piece_type.name == 'COMMANDER':
                    dead_commander_players.add(target_piece.player)
                from_cell.piece = None
                to_cell.piece = None
                # 双方标记均清除
                if had_from_mark:
                    del self.player_marks[from_pos]
                if had_to_mark:
                    del self.player_marks[to_pos]
        else:
            # 移动到空位置
            to_cell.piece = moving_piece
            from_cell.piece = None
            # 标记随棋子移动到新位置
            if had_from_mark:
                self.player_marks[to_pos] = self.player_marks.pop(from_pos)
        
        # 若司令死亡，仅亮明该司令所属玩家的军旗位置；若双方司令同归于尽，则各自所属玩家的军旗均亮明
        if dead_commander_players:
            self._reveal_flags_for_players(dead_commander_players)

        return True
    
    def _battle(self, attacker: Piece, defender: Piece) -> Optional[Piece]:
        """战斗逻辑"""
        # 军旗被攻击，直接被攻占（移动方胜）
        if defender.is_flag():
            return attacker
        
        # 炸弹与任意棋子相遇（包括地雷/工兵），同归于尽
        if attacker.is_bomb() or defender.is_bomb():
            return None
        
        # 地雷：被工兵挖掉，其他棋子碰触则地雷不消失
        if defender.is_mine():
            return attacker if attacker.is_engineer() else defender
        
        # 地雷不能主动攻击
        if attacker.is_mine():
            return defender
        
        # 比较战斗力
        attacker_power = attacker.get_power()
        defender_power = defender.get_power()
        
        if attacker_power > defender_power:
            return attacker
        elif attacker_power < defender_power:
            return defender
        else:
            return None  # 同归于尽
    
    def is_game_over(self) -> bool:
        """检查游戏是否结束：当一队两个玩家全部阵亡时结束（南+北 或 东+西）"""
        alive: Set[Player] = set()
        for cell in self.cells.values():
            if cell.piece:
                alive.add(cell.piece.player)
        south_north_dead = (Player.PLAYER1 not in alive) and (Player.PLAYER3 not in alive)
        east_west_dead = (Player.PLAYER2 not in alive) and (Player.PLAYER4 not in alive)
        return south_north_dead or east_west_dead
        flags_remaining = 0
        for cell in self.cells.values():
            if cell.piece and cell.piece.is_flag():
                flags_remaining += 1
        
        return flags_remaining <= 1
    
    def set_mark(self, position: Position, mark: str):
        """设置位置标记"""
        self.player_marks[position] = mark
    
    def get_mark(self, position: Position) -> Optional[str]:
        """获取位置标记"""
        return self.player_marks.get(position)
    
    def reveal_all_pieces(self) -> None:
        """将棋盘上所有棋子设置为可见"""
        for cell in self.cells.values():
            if cell.piece:
                cell.piece.visible = True
    def _get_axis_players(self, player: Player) -> Set[Player]:
        """根据玩家方向返回同轴玩家集合：南北轴(PLAYER1/PLAYER3)或东西轴(PLAYER2/PLAYER4)"""
        if player in {Player.PLAYER1, Player.PLAYER3}:
            return {Player.PLAYER1, Player.PLAYER3}
        else:
            return {Player.PLAYER2, Player.PLAYER4}
    def _are_allied(self, a: Player, b: Player) -> bool:
        """判断两名玩家是否为同一队（南+北；东+西）"""
        return a in self._get_axis_players(b)
    def _reveal_flags_for_players(self, players: Set[Player]) -> None:
        """将指定玩家集合的军旗设置为可见"""
        for cell in self.cells.values():
            if cell.piece and cell.piece.is_flag() and cell.piece.player in players:
                cell.piece.visible = True

    def has_player_any_legal_move(self, player: Player) -> bool:
        """检查指定玩家是否存在至少一个合法走法（考虑总部禁动、地雷/军旗不可动、铁路规则与友军阻挡）"""
        # 遍历该玩家的所有棋子
        for cell in self.cells.values():
            if not cell.piece or cell.piece.player != player:
                continue
            # 快速跳过不可移动的起点
            if cell.cell_type == CellType.HEADQUARTERS or cell.piece.is_mine() or cell.piece.is_flag():
                continue
            from_pos = cell.position
            # 收集候选目的地
            if cell.cell_type == CellType.RAILWAY:
                if cell.piece.is_engineer():
                    candidates = self.get_railway_connected_positions(from_pos)
                else:
                    candidates = self.get_railway_straight_reachable_positions(from_pos)
            else:
                candidates = set(self.get_adjacent_positions(from_pos))
            # 只要存在一个 can_move 成立的目的地，即认为该玩家有可动子
            for to_pos in candidates:
                if self.can_move(from_pos, to_pos):
                    return True
        return False