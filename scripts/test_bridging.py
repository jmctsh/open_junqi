from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game.board import Board, Position

def fmt_positions(ps):
    return [(p.row, p.col) for p in sorted(ps, key=lambda p:(p.row, p.col))]

def main():
    b = Board()

    north_2_1 = Position(4, 10)
    r = b.get_railway_straight_reachable_positions(north_2_1)
    print('北2,1 reachable count:', len(r))
    print('含(6,10)?', Position(6,10) in r)
    print('含(6,11)?', Position(6,11) in r)
    print('含角(5,10)?', Position(5,10) in r)
    print('行6上的点:', fmt_positions([p for p in r if p.row == 6]))
    print('东面列11上的点:', fmt_positions([p for p in r if p.col == 11]))

    east_1_4 = Position(6, 14)
    r2 = b.get_railway_straight_reachable_positions(east_1_4)
    print('东1,4 reachable count:', len(r2))
    print('是否包含北列10上的点:', any(p.col == 10 and 1 <= p.row <= 5 for p in r2))
    print('是否包含北列11上的点:', any(p.col == 11 and 1 <= p.row <= 5 for p in r2))
    print('东1,4所有reachable(前20):', fmt_positions(list(r2))[:20])

    west_edge = Position(6, 5)
    r3 = b.get_railway_straight_reachable_positions(west_edge)
    print('西1,1近中心边(6,5) reachable count:', len(r3))
    print('是否包含北(5,6)?', Position(5,6) in r3)
    print('是否包含南(11,6)?', Position(11,6) in r3)
    print('列5上的点:', fmt_positions([p for p in r3 if p.col == 5]))
    print('列6上的点(北/南):', fmt_positions([p for p in r3 if p.col == 6 and (p.row <= 5 or p.row >= 11)]))

    west_bottom_corner = Position(10, 5)
    r6 = b.get_railway_straight_reachable_positions(west_bottom_corner)
    print('西1,5角(10,5) reachable count:', len(r6))
    print('西1,5角是否包含南列6(>=11行):', fmt_positions([p for p in r6 if p.col == 6 and p.row >= 11])[:10])

    # 东侧角点桥接验证
    east_top_corner = Position(6, 11)
    r4 = b.get_railway_straight_reachable_positions(east_top_corner)
    print('东1,5角(6,11) reachable count:', len(r4))
    print('东1,5角是否包含北列10(<=5行):', fmt_positions([p for p in r4 if p.col == 10 and p.row <= 5])[:10])

    east_bottom_corner = Position(10, 11)
    r5 = b.get_railway_straight_reachable_positions(east_bottom_corner)
    print('东1,1角(10,11) reachable count:', len(r5))
    print('东1,1角是否包含南列10(>=11行):', fmt_positions([p for p in r5 if p.col == 10 and p.row >= 11])[:10])

    # 北侧角点起点桥接验证
    north_right_corner = Position(5, 10)
    r7 = b.get_railway_straight_reachable_positions(north_right_corner)
    print('北1,5角(5,10) 起点 bridge count:', len(r7))
    print('北1,5角是否包含东近中心竖列11(6..10):', fmt_positions([p for p in r7 if p.col == 11 and 6 <= p.row <= 10]))

    north_left_corner = Position(5, 6)
    r8 = b.get_railway_straight_reachable_positions(north_left_corner)
    print('北1,1角(5,6) 起点 bridge count:', len(r8))
    print('北1,1角是否包含西近中心竖列5(6..10):', fmt_positions([p for p in r8 if p.col == 5 and 6 <= p.row <= 10]))

    # 南侧角点起点桥接验证
    south_left_corner = Position(11, 6)
    r9 = b.get_railway_straight_reachable_positions(south_left_corner)
    print('南1,1角(11,6) 起点 bridge count:', len(r9))
    print('南1,1角是否包含行10西线(列5..1):', fmt_positions([p for p in r9 if p.row == 10 and 1 <= p.col <= 5]))

    south_right_corner = Position(11, 10)
    r10 = b.get_railway_straight_reachable_positions(south_right_corner)
    print('南1,5角(11,10) 起点 bridge count:', len(r10))
    print('南1,5角是否包含行10东线(列11..15):', fmt_positions([p for p in r10 if p.row == 10 and 11 <= p.col <= 15]))

if __name__ == '__main__':
    main()