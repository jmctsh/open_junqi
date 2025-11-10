# 深度搜索与评分逻辑说明

本文档介绍 `server/strategies/search.py` 与 `server/strategies/scoring.py` 的深度搜索（Alpha-Beta）流程、评分组成以及与历史记录 `HistoryRecorder` 的贯通与使用策略。

## 总览

- 搜索入口函数：`alpha_beta_search(board, start_player, config, history, first_ply_moves, preferred_category)`。
- 核心流程：
  - 首层可选“风格过滤”与候选池限定；
  - 采用 Alpha-Beta 递归，按 `beam_width` 做简单束搜索；
  - 每步的即时收益由 `evaluate_move(...)` 提供；
  - 子节点分数按 `discount` 折算并累加；
  - 超时或深度为 0 时调用 `_evaluate_state(...)` 做静态评估。
- 历史贯通：`history` 由上层传入搜索，在排序、静态评估与即时评分中统一传递到 `evaluate_move(...)`，实现“入侵者/频繁进攻试探”等历史敏感加成。

## 搜索机制

- `SearchConfig` 主要参数：
  - `depth`: 搜索深度（默认 3）
  - `beam_width`: 束宽限制（默认 8，超过则截断）
  - `discount`: 子节点折扣（默认 0.95）
- `time_limit_ms`: 超时时间，毫秒（默认 5000）
  - `use_alpha_beta`: 是否启用 Alpha-Beta（默认 True）
  - `apply_style_filter_first_ply`: 首层是否按风格过滤（默认 True）

- 递归逻辑：
  - 若超时或 `depth==0` → `_evaluate_state(board, start_player, history)`；
  - 生成合法走法，首层可按 `preferred_category` 过滤；
  - 排序：`_sort_moves_for_side(..., history)` 使用即时评分对走法打分；
  - 束宽控制：保留前 `beam_width` 条；
  - 最大化层：`total = s_now + discount * child_score`；
  - 最小化层：`total = -s_now + discount * child_score`；
  - Alpha-Beta 剪枝：`beta <= alpha` 时剪枝。

- 静态评估 `_evaluate_state(board, max_player, history)`：
  - 计算“同盟方最佳即时分数”的最大值与“敌方最佳即时分数”的最大值之差；
  - 即时分数来源仍是 `evaluate_move(...)`，因此历史加成在静态评估中同样生效。

## 排序与即时评分

- 排序函数 `_sort_moves_for_side(board, player, moves, maximizing, history)`：
  - 对候选走法调用 `evaluate_move(...)` 获取分数 `s`，按 `maximizing` 决定升降序；
  - 使“历史敏感”的奖励与惩罚能够在走法顺序上体现（例如优先反击入侵者）。

- 即时评分 `evaluate_move(board, player, attacker, from_pos, to_pos, history)` 返回 5 项：
  - `score`: 综合即时收益（作为搜索即时分数使用）
  - `attack_ev`: 进攻期望值（含未知目标的先验与历史修正）
  - `risk`: 风险成本（含被反吃/暴露风险等）
  - `pos_gain`: 位形/机动收益（占位、中心控制、铁路冲刺等）
  - `defense`: 防守增益（守旗/守营等）

## 评分细节（关键启发）

下面列出当前启用的核心启发与历史驱动的加成逻辑（位于 `server/strategies/scoring.py`）：

### 未知单位进攻与先验

- 工兵位（本地坐标行 1，列 2/3/4）未知单位：旅/团/师进攻有额外奖励，强调排/探雷与信息价值。
- 敌方后两行（对方本地行 5/6）未知单位：
  - 旅/团/师进攻奖励提升（强调信息与对方后防清理）。
  - 司令/军长在此场景下进攻受惩罚（不鼓励高子参与低效排雷）。
- 频繁进攻/试探的未知单位：
  - 历史中累计移动/进攻活跃的目标，提升其“高价值”先验；
  - 旅/团/师对其进攻的“信息价值”再额外加成。

### 入侵者与反击鼓励

- 入侵者定义：最近一段历史中吃掉过我方单位的敌方单位（可见或未知）。
- 旅/团/师/师长反击入侵者：
  - 额外奖励，体现抓住进攻主力的价值与信息收益；
- 军长（将军）反击入侵者：
  - 若“敌司令已可见或已阵亡”，额外鼓励（风险相对更可控）。
- 炸弹对入侵者：
  - 当我方缺乏其他可用高子（司令/军长/师长仅存或不可用）且入侵者威胁大时，使用炸弹更优于放走入侵者（降低“假司令”制造与持续骚扰）。

### 军旗相关与抓旗

- 司令/军长对抓旗的价值随局面变化：
  - 非残局（敌高子未尽或司令未死）时，抓旗收益相对降低；
  - 敌司令已死或可见可控时，军长抓旗的价值提升；
  - 残局时（敌高子显著减少）抓旗鼓励显著上升。

### 后两行移动惩罚（工程兵豁免）

- 对“从我方后两行移动且非进攻”的走法，按照棋子价值给予惩罚，避免轻率拉出后防高子；
- 若“敌方工程兵在两步内威胁到 from_pos”，则给予豁免（体现对真实工兵威胁的合理响应）。

## 历史使用点与接口

- `evaluate_move(..., history)`：
  - 传入历史以识别：入侵者、频繁进攻/试探、敌司令状态（可见/阵亡）等；
  - 这些影响进攻期望与信息价值，以及局部惩罚与奖励。
- 搜索期间：
  - `_evaluate_state(..., history)` 与 `_sort_moves_for_side(..., history)` 均传递历史；
  - 保证排序与静态估值均具备历史感知能力。
- 反击路径选择：
  - `choose_best_move_styled(...)` 在检测到反击目标时，使用 `search_best_move_in_pool(..., history)` 仅在反击候选池内进行深度搜索。

## 程序链路与阻塞检查

- 历史贯通：
  - `search.py` 内部所有 `evaluate_move`、`_evaluate_state`、`_sort_moves_for_side` 现已统一传入 `history`（可空）。
- 深度控制与终止：
  - 超时与最大深度双重终止；
  - `beam_width` 控制拓展规模；
  - `discount < 1` 保证分值收敛与父子分值平衡。
- 剪枝与缓存：
  - Alpha-Beta 满足基本剪枝条件；
  - 简单 TT（转置表）按 `state_key` 缓存同状态值以快速返回（未按深度细分，属于轻量优化，不会造成阻塞）。
- 目前未发现链路阻塞或死循环风险；如需更严格的 TT（深度兼容）或更细粒度的时间分配，我可以按你的需求继续增强。

## 参考代码位置

- 搜索流程：`server/strategies/search.py`
  - `_evaluate_state(...)`
  - `_sort_moves_for_side(...)`
  - `alpha_beta_search(...)`
  - `search_best_move_in_pool(...)`
- 评分逻辑：`server/strategies/scoring.py`
  - `evaluate_move(...)`
  - `_attack_ev(...)`
  - 反击目标识别：`_find_counterattack_targets(...)`
  - 行为分类：`server/strategies/behaviors.py` 的 `classify_move(...)`

---

如需对“工程兵试探轨迹识别”或“入侵者时效窗口”做更精细化建模，请告知具体时间窗、阈值和判定口径，我将进一步完善评分与历史解析。