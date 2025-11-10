from __future__ import annotations

import time
import random
import copy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from game.board import Board, Position
from game.piece import Player, Piece
from game.history import HistoryRecorder
from server.strategies.behaviors import classify_move, CATEGORY_ATTACK, CATEGORY_DEFEND, CATEGORY_PROBE
from server.strategies.scoring import evaluate_move
import logging


@dataclass
class SearchConfig:
    depth: int = 3
    beam_width: int = 8
    discount: float = 0.95
    time_limit_ms: int = 5000
    use_alpha_beta: bool = True
    apply_style_filter_first_ply: bool = True


@dataclass
class SearchResult:
    best_move: Optional[Tuple[Position, Position]] = None
    score: float = 0.0
    explored_nodes: int = 0
    cutoff: bool = False


def _players_turn_order(start: Player) -> List[Player]:
    order = [Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4]
    # rotate so that start is first
    while order[0] != start:
        order.append(order.pop(0))
    return order


def _next_player(board: Board, current: Player) -> Player:
    order = _players_turn_order(current)
    # current is first, so pick next with legal moves (or the next regardless if none have moves)
    for p in order[1:] + order[:1]:
        try:
            moves = board.enumerate_player_legal_moves(p)
        except Exception:
            # If enumeration fails for this player, skip
            moves = []
        if moves:
            return p
    # fallback: simple rotation
    idx = [Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4].index(current)
    return [Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4][(idx + 1) % 4]


def _are_allied(board: Board, p1: Player, p2: Player) -> bool:
    try:
        return board._are_allied(p1, p2)
    except Exception:
        # Simple axis rule: 1 with 3, 2 with 4
        return (p1 in (Player.PLAYER1, Player.PLAYER3) and p2 in (Player.PLAYER1, Player.PLAYER3)) or (
            p1 in (Player.PLAYER2, Player.PLAYER4) and p2 in (Player.PLAYER2, Player.PLAYER4)
        )


def _simulate_move(board: Board, move: Tuple[Position, Position]) -> Optional[Board]:
    src, dst = move
    b2 = copy.deepcopy(board)
    try:
        ok = b2.move_piece(src, dst)
        if not ok:
            try:
                logging.getLogger("junqi_ai.search").info(f"[SEARCH] simulate_move failed (can_move returned False) for move=({src.row},{src.col})->({dst.row},{dst.col})")
            except Exception:
                pass
            return None
        return b2
    except Exception:
        try:
            logging.getLogger("junqi_ai.search").info(f"[SEARCH] simulate_move exception for move=({src.row},{src.col})->({dst.row},{dst.col})")
        except Exception:
            pass
        return None


def _hash_board(board: Board) -> str:
    # Create a simple deterministic hash from piece positions
    items: List[str] = []
    try:
        # Board.cells is keyed by Position; use .row/.col
        for pos, cell in board.cells.items():
            if not cell:
                continue
            if cell.piece:
                p: Piece = cell.piece
                pid = getattr(p, "piece_id", None)
                items.append(
                    f"{pos.row},{pos.col}:{p.player.name}:{p.piece_type.name}:{int(p.visible)}:{pid if pid is not None else 'X'}"
                )
    except Exception as ex:
        try:
            logging.getLogger("junqi_ai.search").info(f"[SEARCH] _hash_board error: {ex}")
        except Exception:
            pass
    items.sort()
    return "|".join(items)


def _evaluate_state(board: Board, max_player: Player, history: Optional[HistoryRecorder] = None) -> float:
    # Team value difference: best immediate move for max side minus best for opponents
    try:
        allies = [p for p in [Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4] if _are_allied(board, p, max_player)]
        enemies = [p for p in [Player.PLAYER1, Player.PLAYER2, Player.PLAYER3, Player.PLAYER4] if not _are_allied(board, p, max_player)]
        ally_best = -1e9
        enemy_best = -1e9
        for ap in allies:
            for m in board.enumerate_player_legal_moves(ap):
                fp, tp = m
                from_cell = board.get_cell(fp)
                attacker = from_cell.piece if from_cell else None
                if not attacker:
                    continue
                s, *_ = evaluate_move(board, ap, attacker, fp, tp, history)
                if s > ally_best:
                    ally_best = s
        for ep in enemies:
            for m in board.enumerate_player_legal_moves(ep):
                fp, tp = m
                from_cell = board.get_cell(fp)
                attacker = from_cell.piece if from_cell else None
                if not attacker:
                    continue
                s, *_ = evaluate_move(board, ep, attacker, fp, tp, history)
                if s > enemy_best:
                    enemy_best = s
        if ally_best == -1e9:
            ally_best = 0.0
        if enemy_best == -1e9:
            enemy_best = 0.0
        return ally_best - enemy_best
    except Exception:
        return 0.0


def _sort_moves_for_side(board: Board, player: Player, moves: List[Tuple[Position, Position]], maximizing: bool, history: Optional[HistoryRecorder] = None) -> List[Tuple[Position, Position]]:
    scored: List[Tuple[float, Tuple[Position, Position]]] = []
    logger = logging.getLogger("junqi_ai.search")
    for fp, tp in moves:
        from_cell = board.get_cell(fp)
        attacker = from_cell.piece if from_cell else None
        if not attacker:
            continue
        try:
            s, *_ = evaluate_move(board, player, attacker, fp, tp, history)
        except Exception as ex:
            try:
                logger.info(f"[SEARCH] evaluate_move error in ordering for ({fp.row},{fp.col})->({tp.row},{tp.col}): {ex}")
            except Exception:
                pass
            s = 0.0
        scored.append((s, (fp, tp)))
    scored.sort(key=lambda x: x[0], reverse=maximizing)
    return [m for _, m in scored]


def alpha_beta_search(
    board: Board,
    start_player: Player,
    config: Optional[SearchConfig] = None,
    history: Optional[HistoryRecorder] = None,
    first_ply_moves: Optional[List[Tuple[Position, Position]]] = None,
    preferred_category: Optional[str] = None,
) -> SearchResult:
    cfg = config or SearchConfig()
    start_time = time.time()
    tt: Dict[str, Tuple[int, float]] = {}
    explored = 0
    logger = logging.getLogger("junqi_ai.search")
    try:
        logger.info(
            f"[SEARCH] alpha_beta start: player={start_player}, depth={cfg.depth}, beam_width={cfg.beam_width}, time_limit_ms={cfg.time_limit_ms}, preferred_category={preferred_category}"
        )
        try:
            initial_legal_cnt = len(board.enumerate_player_legal_moves(start_player))
            logger.info(f"[SEARCH] first-ply legal moves before filter: {initial_legal_cnt}")
        except Exception:
            pass
    except Exception:
        pass

    def time_exceeded() -> bool:
        return (time.time() - start_time) * 1000.0 >= cfg.time_limit_ms

    def recurse(b: Board, player: Player, depth: int, alpha: float, beta: float) -> Tuple[float, Optional[Tuple[Position, Position]]]:
        nonlocal explored
        if time_exceeded():
            try:
                logger.info(f"[SEARCH] time_exceeded at depth={depth} for player={player}")
            except Exception:
                pass
            return _evaluate_state(b, start_player, history), None
        if depth == 0:
            return _evaluate_state(b, start_player, history), None

        state_key = _hash_board(b) + f"|{player.name}|{depth}"
        if state_key in tt:
            # use cached value for this depth
            return tt[state_key][1], None

        maximizing = _are_allied(b, player, start_player)
        try:
            legal = b.enumerate_player_legal_moves(player)
        except Exception:
            legal = []

        if depth == cfg.depth and first_ply_moves is not None:
            # restrict to provided first-ply pool
            before_pool_cnt = len(legal)
            legal = [m for m in legal if m in first_ply_moves]
            try:
                logger.info(
                    f"[SEARCH] first-ply pool filter: before={before_pool_cnt}, after={len(legal)}"
                )
            except Exception:
                pass
            if len(legal) == 0:
                try:
                    logger.info("[SEARCH] first-ply pool became empty after filter; returning static eval")
                except Exception:
                    pass
                val = _evaluate_state(b, start_player, history)
                tt[state_key] = (depth, val)
                return val, None

        # optional style filter at first ply
        if depth == cfg.depth and cfg.apply_style_filter_first_ply and preferred_category is not None:
            filtered: List[Tuple[Position, Position]] = []
            for fp, tp in legal:
                cat = classify_move(b, player, fp, tp)
                if cat == preferred_category:
                    filtered.append((fp, tp))
            if filtered:
                legal = filtered
                try:
                    logger.info(
                        f"[SEARCH] style filter applied at first ply: category={preferred_category}, count={len(legal)}"
                    )
                except Exception:
                    pass

        if not legal:
            # no moves -> evaluate static
            val = _evaluate_state(b, start_player, history)
            tt[state_key] = (depth, val)
            try:
                logger.info(
                    f"[SEARCH] no legal moves at depth={depth} for player={player}; returning static eval={val}"
                )
            except Exception:
                pass
            return val, None

        # order moves
        legal = _sort_moves_for_side(b, player, legal, maximizing, history)
        if cfg.beam_width and len(legal) > cfg.beam_width:
            before_bw = len(legal)
            legal = legal[: cfg.beam_width]
            try:
                logger.info(
                    f"[SEARCH] beam width applied: before={before_bw}, after={len(legal)}"
                )
            except Exception:
                pass
        try:
            logger.info(f"[SEARCH] exploring {len(legal)} moves at depth={depth} for player={player}, maximizing={maximizing}")
        except Exception:
            pass

        best_move_local: Optional[Tuple[Position, Position]] = None
        if maximizing:
            value = -1e18
            for m in legal:
                b2 = _simulate_move(b, m)
                if b2 is None:
                    try:
                        fp, tp = m
                        logger.info(f"[SEARCH] skipping move due to simulation failure: ({fp.row},{fp.col})->({tp.row},{tp.col})")
                    except Exception:
                        pass
                    continue
                explored += 1
                next_player = _next_player(b2, player)
                child_score, _ = recurse(b2, next_player, depth - 1, alpha, beta)
                # immediate reward from current move
                fp, tp = m
                from_cell = b.get_cell(fp)
                attacker = from_cell.piece if from_cell else None
                if not attacker:
                    continue
                try:
                    s_now, *_ = evaluate_move(b, player, attacker, fp, tp, history)
                except Exception as ex:
                    try:
                        logger.info(f"[SEARCH] evaluate_move error at root/max for ({fp.row},{fp.col})->({tp.row},{tp.col}): {ex}")
                    except Exception:
                        pass
                    s_now = 0.0
                total = s_now + cfg.discount * child_score
                if total > value:
                    value = total
                    if depth == cfg.depth:
                        best_move_local = m
                        try:
                            logger.info(f"[SEARCH] new best at root: ({fp.row},{fp.col})->({tp.row},{tp.col}), score={round(total,3)}")
                        except Exception:
                            pass
                alpha = max(alpha, value)
                if cfg.use_alpha_beta and beta <= alpha:
                    break
            tt[state_key] = (depth, value)
            return value, best_move_local
        else:
            value = 1e18
            for m in legal:
                b2 = _simulate_move(b, m)
                if b2 is None:
                    try:
                        fp, tp = m
                        logger.info(f"[SEARCH] skipping move due to simulation failure: ({fp.row},{fp.col})->({tp.row},{tp.col})")
                    except Exception:
                        pass
                    continue
                explored += 1
                next_player = _next_player(b2, player)
                child_score, _ = recurse(b2, next_player, depth - 1, alpha, beta)
                fp, tp = m
                from_cell = b.get_cell(fp)
                attacker = from_cell.piece if from_cell else None
                if not attacker:
                    continue
                try:
                    s_now, *_ = evaluate_move(b, player, attacker, fp, tp, history)
                except Exception as ex:
                    try:
                        logger.info(f"[SEARCH] evaluate_move error at root/min for ({fp.row},{fp.col})->({tp.row},{tp.col}): {ex}")
                    except Exception:
                        pass
                    s_now = 0.0
                total = -s_now + cfg.discount * child_score
                if total < value:
                    value = total
                    if depth == cfg.depth:
                        best_move_local = m
                beta = min(beta, value)
                if cfg.use_alpha_beta and beta <= alpha:
                    break
            tt[state_key] = (depth, value)
            return value, best_move_local

    score, move = recurse(board, start_player, cfg.depth, -1e18, 1e18)
    try:
        logger.info(
            f"[SEARCH] alpha_beta finished: explored={explored}, best_move={'None' if move is None else 'set'}, score={round(score,3)}"
        )
    except Exception:
        pass
    return SearchResult(best_move=move, score=score, explored_nodes=explored, cutoff=False)


def search_best_move_in_pool(
    board: Board,
    player: Player,
    candidate_moves: List[Tuple[Position, Position]],
    preferred_category: Optional[str],
    config: Optional[SearchConfig] = None,
    history: Optional[HistoryRecorder] = None,
) -> SearchResult:
    if not candidate_moves:
        return SearchResult(best_move=None, score=0.0, explored_nodes=0, cutoff=False)
    cfg = config or SearchConfig()
    logger = logging.getLogger("junqi_ai.search")
    try:
        logger.info(
            f"[SEARCH] start pool search: player={player}, candidates={len(candidate_moves)}, preferred_category={preferred_category}, depth={cfg.depth}, beam_width={cfg.beam_width}, time_limit_ms={cfg.time_limit_ms}"
        )
    except Exception:
        pass
    return alpha_beta_search(
        board=board,
        start_player=player,
        config=cfg,
        history=history,
        first_ply_moves=candidate_moves,
        preferred_category=preferred_category,
    )