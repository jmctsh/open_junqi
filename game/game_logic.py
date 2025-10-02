#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四国军棋游戏逻辑控制器
"""

from typing import Optional, List, Dict, Set
import random
from enum import Enum
from .board import Board, Position, CellType
from .piece import PieceType, Piece, create_player_pieces
from .formations import list_formations as fm_list, has_formation as fm_has, FORMATIONS, char_to_piece_type
from .piece import Piece, Player, PieceType, create_player_pieces

class GameState(Enum):
    """游戏状态"""
    SETUP = "布局阶段"
    PLAYING = "游戏中"
    FINISHED = "游戏结束"

class GameLogic:
    """游戏逻辑控制器"""
    
    def __init__(self):
        self.board = Board()
        self.game_state = GameState.SETUP
        self.current_player = Player.PLAYER1
        self.player_pieces: Dict[Player, List[Piece]] = {}
        self.setup_complete: Dict[Player, bool] = {}
        # 已被淘汰的玩家集合（投降或被消灭）
        self.eliminated_players: Set[Player] = set()
        # 测试模式：显示所有棋子并允许操作所有玩家（默认关闭）
        self.testing_mode: bool = False
        
        # 初始化玩家棋子
        for player in Player:
            self.player_pieces[player] = create_player_pieces(player)
            self.setup_complete[player] = False

        # 程序加载时为四方自动布局（使用模板）
        self.auto_layout_all_players()
    
    def get_player_setup_area(self, player: Player) -> List[Position]:
        """获取玩家的布局区域（仅兵站与大本营，排除行营）"""
        return self.board.get_player_area(player)
    
    def can_place_piece(self, position: Position, piece: Piece) -> bool:
        """检查是否可以在指定位置放置棋子"""
        cell = self.board.get_cell(position)
        if not cell or cell.piece is not None:
            return False
        
        # 检查是否在正确的玩家区域
        if cell.player_area != piece.player:
            return False
        # 布局禁止行营；允许普通格、铁路、大本营
        if cell.cell_type == CellType.CAMP:
            return False
        
        # 检查特殊棋子的放置限制
        if piece.piece_type == PieceType.BOMB:
            # 炸弹不能放在第一排
            if self._is_first_row(position, piece.player):
                return False
        
        elif piece.piece_type == PieceType.MINE:
            # 地雷只能放在后两排
            if not self._is_last_two_rows(position, piece.player):
                return False
        
        elif piece.piece_type == PieceType.FLAG:
            # 军旗只能放在大本营
            if cell.cell_type.name != 'HEADQUARTERS':
                return False
        
        return True
    
    def _is_first_row(self, position: Position, player: Player) -> bool:
        """检查是否为第一排（按各阵营本地坐标定义）"""
        local_row, _ = self._get_local_coords(position, player)
        return local_row == 1
    
    def _is_last_two_rows(self, position: Position, player: Player) -> bool:
        """检查是否为后两排（按各阵营本地坐标定义）"""
        local_row, _ = self._get_local_coords(position, player)
        # 统一使用南方模板的本地坐标：所有阵营后两排为 5、6
        return local_row in (5, 6)

    def _get_local_coords(self, position: Position, player: Player) -> tuple:
        """将全局坐标转换为各阵营的本地坐标(row,col)，均为1基"""
        if player == Player.PLAYER1:  # 南方：6x5，起点(11,6)
            local_row = position.row - 11 + 1
            local_col = position.col - 6 + 1
        elif player == Player.PLAYER2:  # 西方：5x6，起点(6,0)，逆时针90°
            global_row_in_area = position.row - 6  # 0..4
            global_col_in_area = position.col - 0  # 0..5
            local_row = 5 - global_col_in_area + 1  # 1..5
            local_col = global_row_in_area + 1      # 1..6
        elif player == Player.PLAYER3:  # 北方：6x5，起点(0,6)，旋转180°
            global_row_in_area = position.row - 0  # 0..5
            global_col_in_area = position.col - 6  # 0..4
            local_row = 6 - global_row_in_area     # 1..6
            local_col = 5 - global_col_in_area     # 1..5
        else:  # Player.PLAYER4 东方：5x6，起点(6,11)，顺时针90°
            global_row_in_area = position.row - 6  # 0..4
            global_col_in_area = position.col - 11 # 0..5
            local_row = global_col_in_area + 1     # 1..5
            local_col = 5 - global_row_in_area     # 1..6
        return local_row, local_col
    
    def place_piece(self, position: Position, piece: Piece) -> bool:
        """放置棋子"""
        if self.game_state != GameState.SETUP:
            return False
        
        if not self.can_place_piece(position, piece):
            return False
        
        # 测试阶段：所有棋子可见
        if self.testing_mode:
            piece.visible = True
        return self.board.place_piece(position, piece)
    
    def auto_layout_player(self, player: Player):
        """为玩家自动布局（优先使用名阵模板；若无有效模板则返回失败）"""
        if self.game_state != GameState.SETUP:
            return
        
        # 清除该玩家已有的棋子
        self._clear_player_pieces(player)
        # 选择随机名阵
        available_formations = fm_list()
        if not available_formations:
            # 无模板则失败（弃用旧随机布局）
            return False
        name = random.choice(available_formations)
        success = self.apply_formation(player, name)
        if success:
            self.setup_complete[player] = True
            return True
        return False

    def apply_formation(self, player: Player, name: str) -> bool:
        """按指定名阵布阵：名阵以南方本地坐标6x5字符矩阵表示"""
        if not fm_has(name):
            return False
        grid = FORMATIONS[name]
        # 按本地坐标迭代：row 1..6, col 1..5
        for r_idx, row in enumerate(grid, start=1):
            # 行长度不足5时以空补齐
            row_padded = row.ljust(5, '·')
            for c_idx, ch in enumerate(row_padded, start=1):
                # 跳过空位（支持“·”、Unicode省略号“…”，以及误输入的单个“.”）
                if ch in ('·', ' ', '', '…', '.'):
                    continue
                pt = char_to_piece_type(ch)
                if not pt:
                    continue
                # 找到对应的全局坐标
                target = self._find_global_by_local(player, r_idx, c_idx)
                if not target:
                    return False
                piece = Piece(pt, player, visible=self.testing_mode)
                if not self.place_piece(target, piece):
                    return False
        return True

    def _find_global_by_local(self, player: Player, local_row: int, local_col: int) -> Position | None:
        """在玩家区域中查找与给定本地坐标匹配的全局位置"""
        for pos in self.get_player_setup_area(player):
            lr, lc = self._get_local_coords(pos, player)
            if lr == local_row and lc == local_col:
                return pos
        return None
    
    def _clear_player_pieces(self, player: Player):
        """清除玩家的所有棋子"""
        for position, cell in self.board.cells.items():
            if cell.piece and cell.piece.player == player:
                cell.piece = None
        self.setup_complete[player] = False
    
    def start_game(self) -> bool:
        """开始游戏"""
        # 检查所有玩家是否都完成了布局
        if not all(self.setup_complete.values()):
            return False
        # 开始游戏：随机选择一个玩家先手，关闭测试模式限制为当前玩家操作
        self.game_state = GameState.PLAYING
        self.current_player = random.choice(list(Player))
        self.testing_mode = False
        # 清空淘汰列表
        self.eliminated_players.clear()
        return True
    
    def move_piece(self, from_pos: Position, to_pos: Position) -> bool:
        """移动棋子"""
        if self.game_state != GameState.PLAYING:
            return False
        
        from_cell = self.board.get_cell(from_pos)
        if not from_cell or not from_cell.piece:
            return False
        
        # 检查是否是当前玩家的棋子
        if (not self.testing_mode) and (from_cell.piece.player != self.current_player):
            return False
        
        # 记录防守方是否为军旗，以便战斗后判定强制淘汰
        pre_to_cell = self.board.get_cell(to_pos)
        defender_flag_owner = None
        if pre_to_cell and pre_to_cell.piece and pre_to_cell.piece.is_flag():
            defender_flag_owner = pre_to_cell.piece.player

        # 尝试移动
        if self.board.move_piece(from_pos, to_pos):
            # 若本次战斗吃掉了军旗，则强制淘汰该玩家并清除其所有棋子
            if defender_flag_owner is not None:
                # 判定目标格是否仍为军旗，若不是则视为军旗被吃
                post_to_cell = self.board.get_cell(to_pos)
                if (not post_to_cell) or (not post_to_cell.piece) or (not post_to_cell.piece.is_flag()):
                    # 强制淘汰：加入淘汰列表并清除该玩家所有棋子
                    self.eliminated_players.add(defender_flag_owner)
                    for position, cell in self.board.cells.items():
                        if cell.piece and cell.piece.player == defender_flag_owner:
                            cell.piece = None

            # 若只剩一个军旗，立即结束；否则正常切换
            if self.board.is_game_over():
                self.game_state = GameState.FINISHED
            else:
                # 战斗后可能有人已无子或军旗被吃，更新淘汰状态
                self._ensure_elimination_status()
                self._next_turn()
            return True
        return False

    # === 布局阶段：拖拽交换 ===
    def can_piece_stand_at(self, piece: Piece, position: Position) -> bool:
        """检查棋子在目标位置是否满足布局规则（不考虑占用）"""
        cell = self.board.get_cell(position)
        if not cell:
            return False
        # 必须在本方区域且非行营
        if cell.player_area != piece.player:
            return False
        if cell.cell_type == CellType.CAMP:
            return False
        # 特殊单位限制
        if piece.piece_type == PieceType.BOMB:
            if self._is_first_row(position, piece.player):
                return False
        elif piece.piece_type == PieceType.MINE:
            if not self._is_last_two_rows(position, piece.player):
                return False
        elif piece.piece_type == PieceType.FLAG:
            if cell.cell_type != CellType.HEADQUARTERS:
                return False
        else:
            # 普通子不可进入总部
            if cell.cell_type == CellType.HEADQUARTERS:
                return False
        return True

    def swap_setup_positions(self, from_pos: Position, to_pos: Position) -> bool:
        """布局阶段：尝试在同一玩家区域内交换或移动；非法则不改动并返回False"""
        if self.game_state != GameState.SETUP:
            return False
        from_cell = self.board.get_cell(from_pos)
        to_cell = self.board.get_cell(to_pos)
        if not from_cell or not to_cell:
            return False
        if not from_cell.piece:
            return False
        piece_a = from_cell.piece
        piece_b = to_cell.piece
        # 仅允许操作本方棋子；测试模式下放宽允许操作所有方
        if (not self.testing_mode) and (piece_a.player != self.current_player):
            return False
        # 目标必须属于对应玩家区域
        if to_cell.player_area != piece_a.player:
            return False
        # 单步移动到空位
        if piece_b is None:
            if not self.can_piece_stand_at(piece_a, to_pos):
                return False
            # 执行移动
            from_cell.piece = None
            to_cell.piece = piece_a
            return True
        # 交换：双方都需合法
        if to_cell.player_area != piece_b.player or from_cell.player_area != piece_a.player:
            return False
        if not (self.can_piece_stand_at(piece_a, to_pos) and self.can_piece_stand_at(piece_b, from_pos)):
            return False
        # 执行交换
        from_cell.piece, to_cell.piece = piece_b, piece_a
        return True

    def auto_layout_all_players(self) -> None:
        """为四方玩家各自自动布局（使用模板），失败则保持空但不抛异常"""
        for player in Player:
            try:
                self.auto_layout_player(player)
            except Exception:
                # 保守处理：不中断启动
                pass
    
    def _next_turn(self):
        """切换到下一个玩家"""
        # 逆时针顺序：南→东→北→西→南
        ccw_order = [Player.PLAYER1, Player.PLAYER4, Player.PLAYER3, Player.PLAYER2]
        current_index = ccw_order.index(self.current_player)
        
        # 若仅剩一位未淘汰玩家，则游戏结束
        if len(self.eliminated_players) >= len(ccw_order) - 1:
            self.game_state = GameState.FINISHED
            return

        # 尝试找到下一个未淘汰的玩家
        for step in range(1, len(ccw_order) + 1):
            candidate = ccw_order[(current_index + step) % len(ccw_order)]
            if candidate not in self.eliminated_players:
                self.current_player = candidate
                return
        # 理论上不会到达这里；保险处理为游戏结束
        self.game_state = GameState.FINISHED

    def skip_turn(self) -> bool:
        """跳过当前回合，切换到下一位玩家"""
        if self.game_state != GameState.PLAYING:
            return False
        self._next_turn()
        return True

    def surrender(self) -> bool:
        """当前玩家投降：清除其所有棋子并标记淘汰，随后切换回合"""
        if self.game_state != GameState.PLAYING:
            return False
        loser = self.current_player
        # 清除该玩家所有棋子（包括军旗）
        for position, cell in self.board.cells.items():
            if cell.piece and cell.piece.player == loser:
                cell.piece = None
        # 若因此只剩一个军旗（或更少），直接结束
        if self.board.is_game_over():
            self.game_state = GameState.FINISHED
            return True
        # 标记淘汰并切换到下一位未淘汰玩家
        self.eliminated_players.add(loser)
        self._next_turn()
        return True
    
    def reset_game(self):
        """重置游戏"""
        # 恢复到初始化时的状态：新棋盘、布局阶段、当前玩家重置
        self.board = Board()
        self.game_state = GameState.SETUP
        self.current_player = Player.PLAYER1
        self.eliminated_players.clear()

        # 重新初始化玩家棋子与标记布局未完成
        for player in Player:
            self.player_pieces[player] = create_player_pieces(player)
            self.setup_complete[player] = False

        # 与构造函数保持一致：为四方随机选择名阵并自动布局
        # 这样重置后立即进入可调整的布局阶段，等待用户点击开始游戏
        self.auto_layout_all_players()
    
    def get_game_state(self) -> Dict:
        """获取游戏状态信息"""
        return {
            'state': self.game_state,
            'current_player': self.current_player,
            'setup_complete': self.setup_complete.copy()
        }

    # === 辅助：同步淘汰状态 ===
    def _ensure_elimination_status(self) -> None:
        """检查各玩家是否已无子，若无则加入淘汰列表"""
        remaining: Dict[Player, int] = {p: 0 for p in Player}
        for cell in self.board.cells.values():
            if cell.piece:
                remaining[cell.piece.player] += 1
        for p, count in remaining.items():
            if count == 0:
                self.eliminated_players.add(p)