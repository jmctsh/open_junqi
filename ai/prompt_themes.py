from typing import Dict

"""
发言主题权重配置模块：将不同玩家的主题偏好从 agent 中剥离，便于单独修改。

主题类别说明（权重为百分比，总建议为 100）：
- explain_truth: 解释自己步骤并说真话
- deception: 对所有人说假话以迷惑他人（不得捏造未公开身份）
- taunt_enemy: 骂敌人/挑衅/阴阳怪气（结合当前局面）
- taunt_teammate: 骂队友/说丧气话（围绕配合/局面失误吐槽）
- memes: 玩梗和吐槽（结合棋局常见梗）
- analysis: 分析其他玩家走棋动机与猜子（概率性推测）

你可以直接修改 PERSONA_THEME_WEIGHTS 中的各“人物（persona）”权重，或增删主题类别（同时调整 render_theme_prompt 文本）。
"""

# 默认权重（作为兜底）
DEFAULT_THEME_WEIGHTS: Dict[str, int] = {
    "explain_truth": 5,
    "deception": 30,
    "taunt_enemy": 20,
    "taunt_teammate": 10,
    "memes": 15,
    "analysis": 20,
}

# 各“人物（persona）”的主题权重（可按需调整）：persona_key -> weights
# 注意：persona 与棋盘方位/颜色无关，席位到 persona 的对应关系由布局阶段的选择决定。
PERSONA_THEME_WEIGHTS: Dict[str, Dict[str, int]] = {
    # persona: 外卖剩一半
    "player1": {
        "explain_truth": 5,
        "deception": 20,
        "taunt_enemy": 20,
        "taunt_teammate": 10,
        "memes": 35,
        "analysis": 10,
    },
    # persona: 旧刊夹页（沿用默认）
    "player2": DEFAULT_THEME_WEIGHTS.copy(),
    # persona: 老陈夜茶凉
    "player3": {
        "explain_truth": 10,
        "deception": 15,
        "taunt_enemy": 40,
        "taunt_teammate": 25,
        "memes": 5,
        "analysis": 5,
    },
}


def _sum_weights(weights: Dict[str, int]) -> int:
    return sum(int(v) for v in weights.values())


def get_theme_weights_by_persona(persona_key: str | None) -> Dict[str, int]:
    """返回指定人物（persona）的主题权重。若未配置或总和不为100，则返回默认权重。"""
    if not persona_key:
        return DEFAULT_THEME_WEIGHTS.copy()
    w = PERSONA_THEME_WEIGHTS.get(persona_key)
    if not w:
        return DEFAULT_THEME_WEIGHTS.copy()
    total = _sum_weights(w)
    if total != 100:
        # 为避免影响风格，遇到不合法总和时直接回退到默认
        return DEFAULT_THEME_WEIGHTS.copy()
    return w.copy()


def get_theme_weights(player_id: int) -> Dict[str, int]:
    """兼容入口：按玩家编号返回主题权重。
    说明：player_id 仅作为索引映射到 persona（1->player1, 2->player2, 3->player3）。
    映射关系不与棋盘方位/颜色绑定，实际席位与 persona 的对应在布局阶段决定。
    """
    mapping = {1: "player1", 2: "player2", 3: "player3"}
    persona_key = mapping.get(int(player_id))
    return get_theme_weights_by_persona(persona_key)


def render_theme_prompt(weights: Dict[str, int]) -> str:
    """根据权重渲染到系统提示中的“发言主题概率”文本块。"""
    lines = [
        "发言主题概率（用于采样切换风格；可结合当前局面选择最合理主题）：\n",
        f"- {weights['explain_truth']}% 解释自己步骤并说真话；\n",
        f"- {weights['deception']}% 对所有人说假话以迷惑他人（不得捏造未公开身份，只能基于局面夸大/模糊、制造偏见）；\n",
        f"- {weights['taunt_enemy']}% 骂敌人/挑衅/阴阳怪气（必须结合对方刚走的棋或当前局面体现‘蠢/贪/怂’等）；\n",
        f"- {weights['taunt_teammate']}% 骂队友/说丧气话（不得辱骂个人，需围绕配合/局面失误吐槽且有事实依据）；\n",
        f"- {weights['memes']}% 玩梗和吐槽（结合棋局，如铁路快攻、九宫格、踩雷、行营卡位等热门梗）；\n",
        f"- {weights['analysis']}% 分析其他玩家走棋动机与猜子（只做概率型推测，不得下‘绝对结论’）。\n",
    ]
    return "".join(lines)