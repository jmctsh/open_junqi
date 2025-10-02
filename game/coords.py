from typing import List, Tuple, Optional

# 统一坐标转换工具：提供玩家区域本地坐标 <-> 全局(row,col) 的转换。
# 注意：本模块不依赖棋盘渲染，仅做坐标推导。
try:
    # 尝试导入 Player 枚举以便统一接口；若运行于纯脚本环境无法导入也允许退化。
    from .piece import Player  # type: ignore
except Exception:  # pragma: no cover
    Player = None  # 退化模式：允许传入字符串 '南/西/北/东'

# 该模块仅提供“边线五点”与“桥接目标五点”的生成器，统一从局部坐标描述生成全局(row,col)。
# 约定：局部坐标均为 1..5，对应靠九宫格一侧为 1，远离九宫格为 5。


def north_edge(local_col: int) -> List[Tuple[int, int]]:
    """北侧边线五点：局部列=1或5，行=1..5。
    修正后的正确映射：局部(r,c) -> 全局(row=6-r, col=11-c)
    - c=1 -> 全局列10；c=5 -> 全局列6
    返回顺序：从靠角的一端开始（r=1..5）。
    """
    assert 1 <= local_col <= 5
    col = 11 - local_col
    return [(6 - r, col) for r in range(1, 6)]


def south_edge(local_col: int) -> List[Tuple[int, int]]:
    """南侧边线五点：局部列=1或5，行=1..5。
    - 局部(r,c) -> 全局(row=10+r, col=5+c)
    - c=1 -> 全局列6；c=5 -> 全局列10
    返回顺序：从靠角的一端开始（r=1..5）。
    """
    assert 1 <= local_col <= 5
    col = 5 + local_col
    return [(10 + r, col) for r in range(1, 6)]


def west_near_center_edge() -> List[Tuple[int, int]]:
    """西侧靠九宫格的边（局部列=5 的五点）。
    正确全局映射：全部位于全局行 10，列为 5→1（按 r=1..5 递减）。
    即 West(1..5,5) -> (10,5),(10,4),(10,3),(10,2),(10,1)。"""
    return [(10, c) for c in range(5, 0, -1)]

def west_far_edge() -> List[Tuple[int, int]]:
    """西侧远离九宫格的边（局部列=1 的五点）。
    正确全局映射：全部位于全局行 6，列为 5→1（按 r=1..5 递减）。
    即 West(1..5,1) -> (6,5),(6,4),(6,3),(6,2),(6,1)。"""
    return [(6, c) for c in range(5, 0, -1)]


def east_near_center_edge() -> List[Tuple[int, int]]:
    """东侧靠九宫格的边（局部列=1 的五点）。
    正确全局映射：全部位于全局行 10，列为 11→15（按 r=1..5 递增）。
    即 East(1..5,1) -> (10,11),(10,12),(10,13),(10,14),(10,15)。"""
    return [(10, c) for c in range(11, 16)]

def east_far_edge() -> List[Tuple[int, int]]:
    """东侧远离九宫格的边（局部列=5 的五点）。
    正确全局映射：全部位于全局行 6，列为 11→15（按 r=1..5 递增）。
    即 East(1..5,5) -> (6,11),(6,12),(6,13),(6,14),(6,15)。"""
    return [(6, c) for c in range(11, 16)]


def row10_west_line() -> List[Tuple[int, int]]:
    """南角桥接到西侧的横线（全局行10，列5..1）。"""
    return [(10, c) for c in range(5, 0, -1)]


def row10_east_line() -> List[Tuple[int, int]]:
    """南角桥接到东侧的横线（全局行10，列11..15）。"""
    return [(10, c) for c in range(11, 16)]


# === 通用坐标转换API ===
def to_global(player: "Player|str", local_row: int, local_col: int) -> Tuple[int, int]:
    """将各阵营本地坐标转换为全局(row,col)。
    - 南(6x5):  (r,c) -> (10+r, 5+c)
    - 北(6x5):  (r,c) -> (6-r, 11-c)
    - 西(5x6):  (r,c) -> (5+c, 6-r)
    - 东(5x6):  (r,c) -> (11-c, 10+r)
    player 可传入 Player 枚举或 '南/西/北/东' 字符串。
    """
    side = player.name if hasattr(player, "name") else str(player)
    if side in ("PLAYER1", "南"):
        return 10 + local_row, 5 + local_col
    if side in ("PLAYER3", "北"):
        return 6 - local_row, 11 - local_col
    if side in ("PLAYER2", "西"):
        return 5 + local_col, 6 - local_row
    # 默认东方
    return 11 - local_col, 10 + local_row


def from_global(player: "Player|str", row: int, col: int) -> Tuple[int, int]:
    """将全局(row,col)转换为各阵营本地坐标(r,c)。与 to_global 互逆。
    - 南:  r=row-10, c=col-5
    - 北:  r=6-row,  c=11-col
    - 西:  r=6-col,  c=row-5
    - 东:  r=col-10, c=11-row
    """
    side = player.name if hasattr(player, "name") else str(player)
    if side in ("PLAYER1", "南"):
        return row - 10, col - 5
    if side in ("PLAYER3", "北"):
        return 6 - row, 11 - col
    if side in ("PLAYER2", "西"):
        return 6 - col, row - 5
    # 东方
    return col - 10, 11 - row


def corner_positions() -> dict:
    """返回四侧两角的全局坐标字典，键为 ('南1,1','南1,5',...)/中文标签。"""
    corners = {
        "南1,1": to_global("南", 1, 1),
        "南1,5": to_global("南", 1, 5),
        "北1,1": to_global("北", 1, 1),
        "北1,5": to_global("北", 1, 5),
        "西1,1": to_global("西", 1, 1),
        "西1,5": to_global("西", 1, 5),
        "东1,1": to_global("东", 1, 1),
        "东1,5": to_global("东", 1, 5),
    }
    return corners