#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三方视角管理模块：为东/西/北三位AI玩家维护各自独立视角的棋面信息，并基于历史战斗结果进行推理。

功能目标：
- 为每个视角统一导出 `id_coords`（棋子ID -> 本地坐标），其中仅对该视角可公开的棋面附带 `face`；
- 记录战斗推理（可能身份与排除集合），并在注入给LLM与本地算法时提供结构化的 `inferences`；
- 遵循规则约束：地雷仅在后两排、地雷与军旗不可移动、炸弹对战同归于尽等；
- 公开规则：当某玩家 `司令(COMMANDER)` 死亡后，其 `军旗(FLAG)` 的位置在全场公开。

注：该模块只读访问 GameLogic/Board 以构建视角，不改变棋盘状态。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple

from game.game_logic import GameLogic
from game.board import Position, CellType
from game.piece import Player, PieceType


# --- 基础工具 ---

FACTION_PREFIX = {
    Player.PLAYER1: "south",
    Player.PLAYER2: "west",
    Player.PLAYER3: "north",
    Player.PLAYER4: "east",
}

PREFIX_TO_PLAYER = {"south": Player.PLAYER1, "west": Player.PLAYER2, "north": Player.PLAYER3, "east": Player.PLAYER4}

POWER_RANK: Dict[PieceType, int] = {
    PieceType.COMMANDER: 10,
    PieceType.GENERAL: 9,
    PieceType.DIVISION: 8,
    PieceType.BRIGADE: 7,
    PieceType.REGIMENT: 6,
    PieceType.BATTALION: 5,
    PieceType.COMPANY: 4,
    PieceType.PLATOON: 3,
    PieceType.ENGINEER: 2,
    # 特殊：炸弹/地雷/军旗
    PieceType.BOMB: 1,
    PieceType.MINE: 0,
    PieceType.FLAG: -1,
}


def piece_types_all() -> List[PieceType]:
    return [
        PieceType.COMMANDER,
        PieceType.GENERAL,
        PieceType.DIVISION,
        PieceType.BRIGADE,
        PieceType.REGIMENT,
        PieceType.BATTALION,
        PieceType.COMPANY,
        PieceType.PLATOON,
        PieceType.ENGINEER,
        PieceType.BOMB,
        PieceType.MINE,
        PieceType.FLAG,
    ]


@dataclass
class PieceInference:
    """单个未知棋子的推理信息（相对某视角）。"""
    possible: Set[str] = field(default_factory=set)   # 可能身份集合（字符串枚举值）
    excluded: Set[str] = field(default_factory=set)   # 排除集合
    notes: List[str] = field(default_factory=list)    # 简要注释（来源与理由）
    last_update_turn: Optional[int] = None            # 最近依据的历史步号

    def to_dict(self) -> Dict[str, Any]:
        return {
            "possible": sorted(list(self.possible)) if self.possible else [],
            "excluded": sorted(list(self.excluded)) if self.excluded else [],
            "notes": self.notes[-5:] if self.notes else [],
            "last_update_turn": self.last_update_turn,
        }


@dataclass
class SeatPerspective:
    """单个席位（阵营）视角：公开棋面与推理集合。"""
    player: Player
    id_coords: Dict[str, Dict[str, int]] = field(default_factory=dict)  # {pid: {row, col}}
    faces_public: Dict[str, str] = field(default_factory=dict)          # {pid: face_str}
    inferences: Dict[str, PieceInference] = field(default_factory=dict) # 对非公开对手棋子的推理
    own_types: Dict[str, str] = field(default_factory=dict)             # 本方棋子ID->类型（固定已知）

    def build_payload(self) -> Dict[str, Any]:
        # 将 id_coords 与 faces_public 合并为统一注入结构
        mapping: Dict[str, Any] = {}
        for pid, pos in self.id_coords.items():
            entry = {"row": int(pos.get("row", 0)), "col": int(pos.get("col", 0))}
            face = self.faces_public.get(pid)
            if isinstance(face, str) and face:
                entry["face"] = face
            mapping[pid] = entry
        # 转换推理为注入结构
        inf_map = {pid: inf.to_dict() for pid, inf in self.inferences.items()}
        return {
            "for_faction": FACTION_PREFIX.get(self.player),
            "id_coords": mapping,
            "inferences": inf_map,
        }


class PerspectiveManager:
    """管理三方（东/西/北）视角，并根据历史更新推理。"""
    def __init__(self) -> None:
        self._gl: Optional[GameLogic] = None
        # 仅为AI席位维护视角：东(4)/西(2)/北(3)
        self._seats: Dict[Player, SeatPerspective] = {
            Player.PLAYER2: SeatPerspective(Player.PLAYER2),
            Player.PLAYER3: SeatPerspective(Player.PLAYER3),
            Player.PLAYER4: SeatPerspective(Player.PLAYER4),
        }

    def attach_game_logic(self, gl: GameLogic) -> None:
        self._gl = gl
        self.refresh(gl)

    # ---- 公开规则：司令死亡则军旗位置公开 ----
    def _commander_dead(self, owner: Player) -> bool:
        if self._gl is None:
            return False
        # 扫描棋盘，若不存在该玩家的司令则判定死亡
        for pos, cell in self._gl.board.cells.items():
            if cell.piece and cell.piece.player == owner and cell.piece.piece_type == PieceType.COMMANDER:
                return False
        return True

    def _find_flag_id_and_pos(self, owner: Player) -> Optional[Tuple[str, Tuple[int, int]]]:
        if self._gl is None:
            return None
        for pos, cell in self._gl.board.cells.items():
            if cell.piece and cell.piece.player == owner and cell.piece.piece_type == PieceType.FLAG:
                # 转换为该 owner 的本地坐标
                lr, lc = self._gl._get_local_coords(pos, owner)
                pid = cell.piece.piece_id or ""
                return pid, (lr, lc)
        return None

    def _collect_id_coords(self, viewer: Player) -> Dict[str, Dict[str, int]]:
        """统一采集棋子ID->本地坐标映射（以 viewer 的本地坐标系输出）。"""
        mapping: Dict[str, Dict[str, int]] = {}
        if self._gl is None:
            return mapping
        for pos, cell in self._gl.board.cells.items():
            if cell and cell.piece:
                # 输出为 viewer 的本地坐标
                lr, lc = self._gl._get_local_coords(pos, viewer)
                pid = cell.piece.piece_id
                if pid:
                    mapping[str(pid)] = {"row": int(lr), "col": int(lc)}
        return mapping

    def _collect_own_types(self, viewer: Player) -> Dict[str, str]:
        """采集本方棋子的类型（固定已知）。"""
        types: Dict[str, str] = {}
        if self._gl is None:
            return types
        for pos, cell in self._gl.board.cells.items():
            if cell and cell.piece and cell.piece.player == viewer:
                pid = cell.piece.piece_id
                if pid:
                    types[str(pid)] = cell.piece.piece_type.value
        return types

    def _is_last_two_rows(self, owner: Player, global_pos) -> bool:
        if self._gl is None:
            return False
        lr, lc = self._gl._get_local_coords(global_pos, owner)
        return lr in (5, 6)

    def refresh(self, gl: Optional[GameLogic] = None) -> None:
        """从当前棋盘与历史重建三方视角（不跨对局持久化）。"""
        if gl is not None:
            self._gl = gl
        if self._gl is None:
            return
        # 为每个视角重建坐标与公开棋面
        for seat, sp in self._seats.items():
            sp.id_coords = self._collect_id_coords(seat)
            sp.own_types = self._collect_own_types(seat)
            sp.faces_public = {}
            # 自己的棋面始终公开
            for pid, face in sp.own_types.items():
                sp.faces_public[pid] = face
            # 公开规则：若某方司令死亡，公开其军旗位置与棋面
            for owner in Player:
                if owner == Player.PLAYER1:  # 南方通常为真人；仍可公开其旗位（规则层允许）
                    pass
                if self._commander_dead(owner):
                    flag_info = self._find_flag_id_and_pos(owner)
                    if flag_info:
                        fid, (lr, lc) = flag_info
                        # 在 viewer 坐标系中标注该旗位坐标
                        # 统一 id_coords 映射，如果已有则仅附带面，否则添加
                        sp.faces_public[fid] = PieceType.FLAG.value
                        # 标注 viewer 坐标系下的旗位坐标
                        # 若 id_coords 中无该ID（例如既已被吃或清空），则跳过
                        if fid in sp.id_coords:
                            sp.id_coords[fid] = sp.id_coords[fid]
        # 重建推理：遍历历史记录，根据本方参与的战斗更新可能集合
        self._rebuild_inferences()

    def _rebuild_inferences(self) -> None:
        for sp in self._seats.values():
            sp.inferences = {}
        if self._gl is None:
            return
        # 索引：某ID是否曾移动过（用于排除地雷/军旗）
        moved_ids: Set[str] = set()
        for rec in self._gl.history.records:
            try:
                if isinstance(rec.piece_id, str) and rec.piece_id:
                    moved_ids.add(rec.piece_id)
            except Exception:
                pass
        # 遍历历史以构建视角推理
        for rec in self._gl.history.records:
            turn = rec.turn
            attacker_id = rec.piece_id or ""
            defender_id = rec.defender_piece_id or ""
            outcome = rec.outcome or "move"
            # 抽取攻击方阵营
            attacker_faction = str(rec.player_faction or "")
            attacker_player = PREFIX_TO_PLAYER.get(attacker_faction)
            # 两侧记录必要：找出 defender 所属阵营（通过ID前缀）
            defender_player = None
            try:
                prefix = defender_id.split("_")[0] if defender_id else ""
                defender_player = PREFIX_TO_PLAYER.get(prefix)
            except Exception:
                defender_player = None
            # 判定本次战斗是否发生在某方大本营（用于军旗参与的同归于尽推理）
            in_headquarters = False
            in_def_last_two_rows = False
            try:
                if isinstance(rec.to_local, (tuple, list)) and attacker_player is not None:
                    lr, lc = int(rec.to_local[0]), int(rec.to_local[1])
                    gr, gc = self._to_global_coords(attacker_player, lr, lc)
                    cell = self._gl.board.get_cell(Position(gr, gc))
                    in_headquarters = bool(cell and cell.cell_type == CellType.HEADQUARTERS)
                    # 是否位于防守方的后两排（用于地雷推理强化）
                    if defender_player is not None:
                        in_def_last_two_rows = self._is_last_two_rows(defender_player, Position(gr, gc))
            except Exception:
                in_headquarters = False
                in_def_last_two_rows = False
            # 针对每个视角分别推理：若视角为攻击方，则推理防守方；若视角为防守方，则推理攻击方
            for seat, sp in self._seats.items():
                # 视角为攻击方
                if attacker_player == seat:
                    atk_face_str = sp.own_types.get(attacker_id)
                    if not atk_face_str:
                        # 攻击方若不是本视角（异常），跳过
                        continue
                    # 初始化目标ID的推理容器
                    inf = sp.inferences.get(defender_id)
                    if inf is None:
                        inf = PieceInference()
                        sp.inferences[defender_id] = inf
                    # 根据 outcome 与攻击子类型进行约束
                    self._update_constraints_for_defender(inf, atk_face_str, outcome, defender_id, moved_ids, turn, defender_player, in_headquarters, in_def_last_two_rows)
                # 视角为防守方
                elif defender_player == seat:
                    # 防守方本视角一定知道自己的防守子类型（若仍在场）
                    def_face_str = sp.own_types.get(defender_id)
                    # 即使子已死亡，own_types 可能看不到，但本方对自己棋子的类型应始终已知；
                    # 若当前 own_types 不含该ID，则跳过推理攻击方（保守）
                    if not def_face_str:
                        continue
                    inf = sp.inferences.get(attacker_id)
                    if inf is None:
                        inf = PieceInference()
                        sp.inferences[attacker_id] = inf
                    self._update_constraints_for_attacker(inf, def_face_str, outcome, attacker_id, moved_ids, turn, attacker_player, in_headquarters, in_def_last_two_rows)

    def _to_global_coords(self, seat: Player, local_row: int, local_col: int) -> tuple[int, int]:
        """将给定席位的本地坐标转换为全局(row,col)。优先使用GameLogic提供的查找。"""
        try:
            if self._gl is None:
                return (0, 0)
            pos = self._gl._find_global_by_local(seat, int(local_row), int(local_col))
            if pos is not None:
                return (pos.row, pos.col)
        except Exception:
            pass
        return (0, 0)

    # --- 约束更新（攻击/防守两个方向） ---
    def _update_constraints_for_defender(
        self,
        inf: PieceInference,
        attacker_face: str,
        outcome: str,
        defender_id: str,
        moved_ids: Set[str],
        turn: int,
        defender_owner: Optional[Player],
        in_headquarters: bool,
        in_defender_last_two_rows: bool,
    ) -> None:
        """攻击方视角：给出对防守子的可能身份约束。"""
        try:
            inf.last_update_turn = turn
            # 初始可能集合：全部
            if not inf.possible:
                inf.possible = set([pt.value for pt in piece_types_all()])
            # 通用排除：军旗不可作为防守主动战斗单位（但可被吃），仍保留在可能中用于后续排除
            # 若 defender 曾移动，则排除地雷/军旗（它们不可移动）
            if defender_id and defender_id in moved_ids:
                inf.excluded.update({PieceType.MINE.value, PieceType.FLAG.value})
                inf.notes.append("曾移动：排除地雷/军旗")
            # 规则：地雷仅后两排（根据其当前或战斗位置判断）。若不在后两排则排除地雷
            if defender_owner is not None and self._gl is not None:
                # 找到 defender 当前坐标（全局）
                for pos, cell in self._gl.board.cells.items():
                    if cell.piece and cell.piece.piece_id == defender_id:
                        if not self._is_last_two_rows(defender_owner, pos):
                            inf.excluded.add(PieceType.MINE.value)
                            inf.notes.append("位置非后两排：排除地雷")
                        break
                # 若当前未找到位置（比如该子已死），但战斗发生在防守方后两排，可作为地雷可能性增强线索
                if in_defender_last_two_rows:
                    inf.notes.append("战斗发生在防守方后两排：地雷可能性增强")
            # 依据战斗结果更新上下界
            atk_pt = self._face_to_type(attacker_face)
            if outcome == "attack_both_die":
                # 同归于尽：炸弹参与或同级打兑
                if atk_pt == PieceType.BOMB:
                    # 炸弹参与：若发生在大本营，则防守可能为军旗；否则排除军旗
                    if in_headquarters:
                        inf.possible.add(PieceType.FLAG.value)
                        inf.notes.append("HQ同归于尽：防守可能为军旗")
                    else:
                        inf.excluded.add(PieceType.FLAG.value)
                        inf.notes.append("非HQ同归于尽：排除军旗")
                    # 其它可能保持广泛（按后续排除清理）
                    # 若战斗在防守方后两排，地雷也可能与炸弹同归于尽（炸弹对任何子同归于尽）
                    if in_defender_last_two_rows:
                        inf.possible.add(PieceType.MINE.value)
                        inf.notes.append("后两排同归于尽：防守可能为地雷")
                else:
                    # 同级打兑：防守可能为与攻击同级；炸弹也可能导致同归于尽（若另一方是炸弹）
                    inf.possible.update({PieceType.BOMB.value, atk_pt.value})
                    inf.excluded.add(PieceType.MINE.value)
                    inf.notes.append("打兑或炸弹命中：可能为同级或炸弹，排除地雷")
            elif outcome == "attack_defender_wins":
                # 非工程师打地雷也会攻击方死亡；一般情况下“防守子战力 >= 攻击子”或“防守子为地雷且攻击非工程师”
                # 建立可能上界/下界约束
                stronger_or_equal = [pt.value for pt, pw in POWER_RANK.items() if pw >= POWER_RANK.get(atk_pt, -99)]
                inf.possible.update(stronger_or_equal)
                # 若攻击子不是工程师，则地雷仍可能；若是工程师则排除地雷（工程师对地雷攻击方不会死亡）
                if atk_pt == PieceType.ENGINEER:
                    inf.excluded.add(PieceType.MINE.value)
                    inf.notes.append("工程师被防守打败：排除地雷")
                else:
                    inf.possible.add(PieceType.MINE.value)
                    if in_defender_last_two_rows:
                        inf.notes.append("后两排防守胜：地雷可能性更高")
                    else:
                        inf.notes.append("攻击方死亡：可能为更大级别或地雷")
                # 炸弹不会仅防守胜出（通常同归于尽），故排除炸弹
                inf.excluded.add(PieceType.BOMB.value)
            elif outcome == "attack_attacker_wins":
                # 工程师打地雷会攻击方胜利；否则一般“防守子战力 <= 攻击子”
                weaker_or_equal = [pt.value for pt, pw in POWER_RANK.items() if pw <= POWER_RANK.get(atk_pt, -99)]
                inf.possible.update(weaker_or_equal)
                if atk_pt == PieceType.ENGINEER:
                    inf.possible.add(PieceType.MINE.value)
                    if in_defender_last_two_rows:
                        inf.notes.append("后两排工程师胜：强烈指向地雷")
                    else:
                        inf.notes.append("工程师胜：可能为地雷")
                else:
                    # 非工程师不可战胜地雷，排除地雷
                    inf.excluded.add(PieceType.MINE.value)
                # 炸弹通常同归于尽，排除炸弹
                inf.excluded.add(PieceType.BOMB.value)
            else:
                # 单纯移动不产生约束
                pass
            # 清理：possible 与 excluded 相减
            if inf.possible:
                inf.possible = set([p for p in inf.possible if p not in inf.excluded])
        except Exception:
            # 保守失败不影响其他记录
            pass

    def _update_constraints_for_attacker(
        self,
        inf: PieceInference,
        defender_face: str,
        outcome: str,
        attacker_id: str,
        moved_ids: Set[str],
        turn: int,
        attacker_owner: Optional[Player],
        in_headquarters: bool,
        in_defender_last_two_rows: bool,
    ) -> None:
        """防守方视角：给出对攻击子的可能身份约束。"""
        try:
            inf.last_update_turn = turn
            if not inf.possible:
                inf.possible = set([pt.value for pt in piece_types_all()])
            # 若攻击方曾移动：排除地雷/军旗（它们不可移动）
            if attacker_id and attacker_id in moved_ids:
                inf.excluded.update({PieceType.MINE.value, PieceType.FLAG.value})
                inf.notes.append("曾移动：排除地雷/军旗")
            # 防守子为炸弹时通常同归于尽；若 outcome 非同归于尽则排除炸弹
            def_pt = self._face_to_type(defender_face)
            if outcome == "attack_both_die":
                # 同归于尽两类：炸弹参与或同级打兑
                if def_pt == PieceType.BOMB:
                    # 防守为炸弹：攻击可能为除军旗外任意（军旗不能移动）
                    inf.excluded.add(PieceType.FLAG.value)
                    inf.notes.append("同归于尽：防守为炸弹，攻击排除军旗")
                elif def_pt == PieceType.FLAG:
                    # 防守为军旗：仅在HQ可能发生；攻击必为炸弹
                    inf.possible = {PieceType.BOMB.value}
                    inf.notes.append("HQ同归于尽：军旗被炸弹击中，攻击为炸弹")
                else:
                    # 防守非炸弹/军旗：攻击可能为炸弹或与防守同级
                    inf.possible.update({PieceType.BOMB.value, def_pt.value})
                    inf.excluded.add(PieceType.MINE.value)
                    inf.notes.append("同级打兑或炸弹：攻击可能为同级或炸弹，排除地雷")
            elif outcome == "attack_attacker_wins":
                # 攻击方战力 >= 防守方 或 工程师挖地雷
                stronger_or_equal = [pt.value for pt, pw in POWER_RANK.items() if pw >= POWER_RANK.get(def_pt, -99)]
                inf.possible.update(stronger_or_equal)
                if def_pt == PieceType.MINE:
                    # 若防守为地雷，则攻击子可能为工程师
                    inf.possible.add(PieceType.ENGINEER.value)
                    inf.notes.append("胜地雷：可能为工程师或更强子")
            elif outcome == "attack_defender_wins":
                # 防守方胜：攻击方战力 <= 防守方；炸弹不适用（通常同归于尽）
                weaker_or_equal = [pt.value for pt, pw in POWER_RANK.items() if pw <= POWER_RANK.get(def_pt, -99)]
                inf.possible.update(weaker_or_equal)
                inf.excluded.add(PieceType.BOMB.value)
            else:
                pass
            if inf.possible:
                inf.possible = set([p for p in inf.possible if p not in inf.excluded])
        except Exception:
            pass

    def _face_to_type(self, face_str: str) -> PieceType:
        # 将中文棋面转换为枚举；若不匹配则回退为排长（最低普通子）
        try:
            for pt in piece_types_all():
                if pt.value == face_str:
                    return pt
        except Exception:
            pass
        return PieceType.PLATOON

    # ---- 对外接口：构建当前席位的注入负载 ----
    def build_perspective_payload(self, player: Player) -> Dict[str, Any]:
        sp = self._seats.get(player)
        if not sp:
            # 对南方或异常情况返回空结构
            return {"for_faction": FACTION_PREFIX.get(player, "south"), "id_coords": {}, "inferences": {}}
        return sp.build_payload()

    # ---- 新增：位置线索负载（完整坐标网格 + 棋子ID/公开棋面/隐藏棋面线索） ----
    def build_location_clues_payload(self, player: Player) -> Dict[str, Any]:
        """
        构建供LLM使用的“位置线索”负载，统一包含：
        - 棋盘尺寸（rows/cols）
        - 全部坐标的占位信息（has_piece）
        - 若有棋子：piece_id；如棋面公开则给出 face；否则给出该未知棋子的推理线索（possible/excluded/notes）。
        坐标一律使用 viewer（player）本地坐标系。
        """
        if self._gl is None:
            return {"for_faction": FACTION_PREFIX.get(player, "south"), "board": {"rows": 0, "cols": 0}, "location_clues": []}
        sp = self._seats.get(player)
        if not sp:
            return {"for_faction": FACTION_PREFIX.get(player, "south"), "board": {"rows": self._gl.board.rows, "cols": self._gl.board.cols}, "location_clues": []}
        rows = int(self._gl.board.rows)
        cols = int(self._gl.board.cols)
        clues: List[Dict[str, Any]] = []
        for pos, cell in self._gl.board.cells.items():
            lr, lc = self._gl._get_local_coords(pos, player)
            entry: Dict[str, Any] = {"row": int(lr), "col": int(lc)}
            has_piece = bool(cell and getattr(cell, "piece", None))
            entry["has_piece"] = has_piece
            if has_piece:
                pid = getattr(cell.piece, "piece_id", None) or ""
                if pid:
                    entry["piece_id"] = str(pid)
                    # 面是否公开：优先使用 faces_public
                    face = sp.faces_public.get(str(pid))
                    if isinstance(face, str) and face:
                        entry["face"] = face
                    else:
                        # 若棋面未公开，且存在推理信息，则附带线索
                        inf = sp.inferences.get(str(pid))
                        if inf:
                            entry["clues"] = inf.to_dict()
                else:
                    # 没有ID（异常情况），仅标注占位
                    pass
            clues.append(entry)
        return {
            "for_faction": FACTION_PREFIX.get(player),
            "board": {"rows": rows, "cols": cols},
            "location_clues": clues,
        }