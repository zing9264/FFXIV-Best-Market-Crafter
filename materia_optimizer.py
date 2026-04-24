"""Cheapest-meld optimizer for FFXIV crafting gear.

Given target stats (craftsmanship/control/CP), a list of gear pieces with
per-stat caps, and live materia market prices, finds the cheapest way to
fill available materia slots so the sum of resulting stats meets the target.

Formulation (ILP, solved with PuLP):

    variables   x[piece, socket, materia]  in {0, 1}
    objective   minimize  Σ x * price
    s.t.
        (1) each socket picks at most one materia
        (2) Σ stat gain in piece ≤ headroom[piece, stat]   (per piece cap)
        (3) Σ all stat gain ≥ target deficit                (global target)
        (4) max-tier materia forbidden in later overmeld sockets

Half-materia (匠心/武備) are not modelled yet — they require per-piece
attribute budgeting that is outside this first pass. Adding them later only
needs extra materia rows in the stats CSV; the solver code already handles
any materia that exposes (stat_type, stat_value) rows.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

import pulp


REPO_ROOT = Path(__file__).resolve().parent
MATERIA_STATS_PATH = REPO_ROOT / "data" / "materia_stats.csv"
MELD_SLOTS_PATH = REPO_ROOT / "data" / "meld_slots.csv"
GEAR_PRESETS_PATH = REPO_ROOT / "data" / "gear_presets.csv"
MELD_SUCCESS_RATES_PATH = REPO_ROOT / "data" / "meld_success_rates.csv"

# Class base stats at lv100 that are not modelled on any gear piece. For a DoH
# at 100 the baseline CP is 180 (class) + race adjustments. We just expose the
# combined base as a preset-level constant so users who load a preset get the
# full picture without having to add it manually.
#
# Keyed by preset_key — separate from gear_presets.csv so CSV rows stay focused
# on per-piece gear stats.
PRESET_CLASS_BASE: dict[str, dict[str, int]] = {
    "crp_745_explorer": {"craftsmanship": 0, "control": 0, "cp": 180},
}

PRESET_LABELS: dict[str, str] = {
    "crp_745_explorer": "刻木匠 7.45 探求永恆 + 黑星石 + 卡扎納爾",
}

STAT_KEYS = ("craftsmanship", "control", "cp")
STAT_LABELS = {
    "craftsmanship": "作業精度",
    "control": "加工精度",
    "cp": "制作力",
}

# Current expansion's top tier. Sockets past the first overmeld can't use it.
CURRENT_MAX_TIER = 12


@dataclass(frozen=True)
class Materia:
    item_id: int
    name: str
    series: str
    tier: int
    stat_type: str
    stat_value: int


@dataclass(frozen=True)
class SlotConfig:
    """How the sockets on one type of gear are structured."""

    slot_key: str
    label: str
    safe_sockets: int  # Sockets with 100% meld rate
    total_sockets: int  # Total socket count including overmeld


@dataclass
class GearPiece:
    """A concrete piece of gear the player wants to meld.

    Setting `locked=True` removes this piece from the optimization entirely:
    no variables are created for its sockets, and `locked_contribution` is
    treated as already-achieved stats (folded into the global base). Use this
    when the player has already melded a piece and wants to keep it as-is.
    """

    slot_key: str
    label: str
    # Per-stat headroom: how much more stat this piece can absorb from materia
    # before hitting its cap. Usually computed as cap - base.
    headroom: dict[str, int] = field(default_factory=lambda: {k: 0 for k in STAT_KEYS})
    locked: bool = False
    locked_contribution: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in STAT_KEYS}
    )


@dataclass
class OptimizationResult:
    status: str
    total_cost: float
    total_stats: dict[str, int]
    target_stats: dict[str, int]
    base_stats: dict[str, int]
    assignments: list[dict]  # one row per filled socket
    unfilled: int  # sockets left empty


def load_materia_stats(path: Path = MATERIA_STATS_PATH) -> list[Materia]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            Materia(
                item_id=int(row["item_id"]),
                name=row["name"],
                series=row["series"],
                tier=int(row["tier"]),
                stat_type=row["stat_type"],
                stat_value=int(row["stat_value"]),
            )
            for row in reader
        ]


def load_gear_presets(
    path: Path = GEAR_PRESETS_PATH,
) -> dict[str, dict]:
    """Load the built-in gear presets keyed by preset_key.

    Each entry contains the ordered list of 12 pieces, their per-stat base
    values, their per-stat caps, and the derived total base stats (used by
    the optimizer as `base_stats`). `class_base` from PRESET_CLASS_BASE is
    added so totals match the player's actual starting point.
    """
    presets: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = row["preset_key"]
            entry = presets.setdefault(
                key,
                {
                    "label": PRESET_LABELS.get(key, key),
                    "pieces": [],
                    "base_stats_total": {k: 0 for k in STAT_KEYS},
                },
            )
            piece_info = {
                "order": int(row["piece_order"]),
                "slot_key": row["slot_key"],
                "label": row["label"],
                "base": {
                    "craftsmanship": int(row["base_craft"]),
                    "control": int(row["base_ctrl"]),
                    "cp": int(row["base_cp"]),
                },
                "cap": {
                    "craftsmanship": int(row["cap_craft"]),
                    "control": int(row["cap_ctrl"]),
                    "cp": int(row["cap_cp"]),
                },
            }
            piece_info["headroom"] = {
                stat: piece_info["cap"][stat] - piece_info["base"][stat]
                for stat in STAT_KEYS
            }
            entry["pieces"].append(piece_info)
            for stat in STAT_KEYS:
                entry["base_stats_total"][stat] += piece_info["base"][stat]

    # Add the non-gear class base (e.g. 180 CP for DoH at 100).
    for key, entry in presets.items():
        entry["pieces"].sort(key=lambda p: p["order"])
        cls_base = PRESET_CLASS_BASE.get(key, {})
        for stat, value in cls_base.items():
            entry["base_stats_total"][stat] += value

    return presets


def pieces_from_preset(preset: dict) -> list[GearPiece]:
    return [
        GearPiece(
            slot_key=p["slot_key"],
            label=p["label"],
            headroom=dict(p["headroom"]),
        )
        for p in preset["pieces"]
    ]


def load_success_rates(
    path: Path = MELD_SUCCESS_RATES_PATH,
) -> dict[str, float]:
    """Load the per-socket_position meld success rate.

    Per the current (7.x) rules and user-supplied rates, each overmeld slot
    has a flat success rate regardless of materia tier (so long as the tier
    itself is allowed by the 'tier-12 only in first overmeld' rule, which is
    enforced elsewhere). If you later want tier-sensitive rates, expand the
    CSV with a materia_tier column and switch this back to keyed lookups.
    """
    rates: dict[str, float] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            pos = row["socket_position"].strip()
            try:
                rate = float(row["success_rate"])
            except (TypeError, ValueError):
                continue
            if rate > 0:
                rates[pos] = rate
    return rates


def get_success_rate(
    rates: Mapping[str, float],
    socket_position: str,
    tier: int,  # kept for signature compat; unused with flat-rate CSV
) -> float:
    """Resolve the meld success rate for a socket position.

    Safe slots always return 1.0. Missing overmeld positions fall back to
    the lowest rate listed (pessimistic default), then to 1.0.
    """
    if socket_position == "safe":
        return 1.0
    if socket_position in rates:
        return rates[socket_position]
    # Fall back: use the minimum of any listed non-safe rate (most pessimistic),
    # or 1.0 if nothing is listed.
    non_safe = [r for k, r in rates.items() if k != "safe"]
    return min(non_safe) if non_safe else 1.0


def socket_position_label(socket_idx: int, safe_count: int) -> str:
    """Return the socket_position key used in the success-rate table.

    socket_idx is 0-based. overmeld_1 is the first non-safe socket, etc.
    """
    if socket_idx < safe_count:
        return "safe"
    return f"overmeld_{socket_idx - safe_count + 1}"


def load_slot_configs(path: Path = MELD_SLOTS_PATH) -> dict[str, SlotConfig]:
    configs: dict[str, SlotConfig] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            slot = SlotConfig(
                slot_key=row["slot_key"],
                label=row["slot_label"],
                safe_sockets=int(row["safe_sockets"]),
                total_sockets=int(row["total_sockets"]),
            )
            configs[slot.slot_key] = slot
    return configs


def _socket_is_safe(slot_conf: SlotConfig, socket_idx: int) -> bool:
    return socket_idx < slot_conf.safe_sockets


def _socket_is_first_overmeld(slot_conf: SlotConfig, socket_idx: int) -> bool:
    return socket_idx == slot_conf.safe_sockets


def _tier_allowed_at_socket(slot_conf: SlotConfig, socket_idx: int, tier: int) -> bool:
    """Return whether `tier` materia may be placed at this socket.

    Rule set (current patch):
      - Safe sockets: any tier.
      - First overmeld socket: any tier (including current top tier).
      - Later overmeld sockets: must be <= CURRENT_MAX_TIER - 1 (tier 12 forbidden).
    """
    if _socket_is_safe(slot_conf, socket_idx):
        return True
    if _socket_is_first_overmeld(slot_conf, socket_idx):
        return True
    return tier < CURRENT_MAX_TIER


def _prune_dominated(materia: list[Materia], prices: Mapping[int, float]) -> list[Materia]:
    """Drop materia that are Pareto-dominated within the same stat type.

    A tier is dominated if a CHEAPER materia in the same stat exists with
    equal-or-greater stat value — it's never worth including in the ILP.
    Dramatically shrinks the solver's search space without changing the
    optimal objective value.
    """
    by_stat: dict[str, list[Materia]] = {}
    for m in materia:
        by_stat.setdefault(m.stat_type, []).append(m)

    kept: list[Materia] = []
    for stat, group in by_stat.items():
        group = sorted(group, key=lambda m: (prices[m.item_id], -m.stat_value))
        best_stat_at_price = -1
        for m in group:
            if m.stat_value > best_stat_at_price:
                kept.append(m)
                best_stat_at_price = m.stat_value
    return kept


def optimize(
    targets: Mapping[str, int],
    base_stats: Mapping[str, int],
    pieces: Iterable[GearPiece],
    prices: Mapping[int, float],
    slot_configs: Mapping[str, SlotConfig] | None = None,
    materia: Iterable[Materia] | None = None,
    solver_timeout_seconds: int = 10,
    top_k: int = 1,
) -> list[OptimizationResult]:
    """Solve for the cheapest materia layout(s) that meet the stat targets.

    Only materia with a matching price in `prices` are considered — we can't
    recommend something the market doesn't list. With `top_k > 1`, iteratively
    solve the ILP while injecting a no-good cut after each run, yielding the
    k cheapest distinct layouts (deduped by materia multiset).
    """

    slot_configs = slot_configs or load_slot_configs()
    materia = list(materia) if materia is not None else load_materia_stats()
    pieces = list(pieces)
    success_rates = load_success_rates()

    priced_materia = [m for m in materia if prices.get(m.item_id, 0) > 0]
    if not priced_materia:
        raise ValueError("No priced materia found — refresh materia prices first.")

    # NOTE: `_prune_dominated` is a tempting speed-up but drops cheap
    # low-stat materia that are actually valuable for padding tight headrooms
    # (e.g. a stat needs exactly +2 more — a ¥210 tier-8 is strictly better
    # than a ¥8k tier-10 even though the tier-10 has "better" stat-per-gil).
    # Keep the full set for now; revisit with a smarter dominance check later.

    prob = pulp.LpProblem("ffxiv_materia_meld", pulp.LpMinimize)

    # Build a variable for every (piece_index, socket_index, materia) triple
    # that is actually placeable at that socket.
    x: dict[tuple[int, int, int], pulp.LpVariable] = {}
    socket_index: dict[tuple[int, int], list[int]] = {}

    # Locked pieces don't contribute variables — their current materia is
    # baked into the global base deficit instead.
    effective_base = dict(base_stats)
    for piece in pieces:
        if piece.locked:
            for stat in STAT_KEYS:
                effective_base[stat] = effective_base.get(stat, 0) + int(
                    piece.locked_contribution.get(stat, 0)
                )

    for p_idx, piece in enumerate(pieces):
        slot_conf = slot_configs.get(piece.slot_key)
        if slot_conf is None:
            raise ValueError(f"Unknown slot_key: {piece.slot_key}")
        if piece.locked:
            continue
        for s_idx in range(slot_conf.total_sockets):
            socket_index[(p_idx, s_idx)] = []
            for m in priced_materia:
                if not _tier_allowed_at_socket(slot_conf, s_idx, m.tier):
                    continue
                var = pulp.LpVariable(
                    f"x_{p_idx}_{s_idx}_{m.item_id}", cat=pulp.LpBinary
                )
                x[(p_idx, s_idx, m.item_id)] = var
                socket_index[(p_idx, s_idx)].append(m.item_id)

    # (1) each socket picks at most one materia
    for (p_idx, s_idx), mat_ids in socket_index.items():
        prob += pulp.lpSum(x[(p_idx, s_idx, mid)] for mid in mat_ids) <= 1

    # (2) per-piece cap (headroom) per stat
    for p_idx, piece in enumerate(pieces):
        if piece.locked:
            continue
        slot_conf = slot_configs[piece.slot_key]
        for stat in STAT_KEYS:
            headroom = piece.headroom.get(stat, 0)
            stat_terms = [
                m.stat_value * x[(p_idx, s_idx, m.item_id)]
                for s_idx in range(slot_conf.total_sockets)
                for m in priced_materia
                if m.stat_type == stat
                and (p_idx, s_idx, m.item_id) in x
            ]
            if stat_terms:
                prob += pulp.lpSum(stat_terms) <= headroom, f"cap_{p_idx}_{stat}"

    # (3) global target — sum of materia stat >= target - (base + locked contribution)
    for stat in STAT_KEYS:
        deficit = max(0, targets.get(stat, 0) - effective_base.get(stat, 0))
        stat_terms = [
            m.stat_value * x[k]
            for k, _ in x.items()
            for m in priced_materia
            if m.item_id == k[2] and m.stat_type == stat
        ]
        if stat_terms:
            prob += pulp.lpSum(stat_terms) >= deficit, f"target_{stat}"

    # Objective: minimise total EXPECTED cost, i.e. price weighted by 1/rate.
    # Pre-compute effective cost per (socket, materia) because the rate depends
    # on both socket_position and tier.
    effective_cost: dict[tuple[int, int, int], float] = {}
    for (p_idx, s_idx, mid) in x:
        piece = pieces[p_idx]
        slot_conf = slot_configs[piece.slot_key]
        m = next(mat for mat in priced_materia if mat.item_id == mid)
        pos = socket_position_label(s_idx, slot_conf.safe_sockets)
        rate = get_success_rate(success_rates, pos, m.tier)
        effective_cost[(p_idx, s_idx, mid)] = prices[mid] / max(rate, 1e-6)

    materia_by_id = {m.item_id: m for m in priced_materia}
    prob += pulp.lpSum(
        effective_cost[k] * x[k] for k in x
    )

    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=solver_timeout_seconds)
    materia_by_id = {m.item_id: m for m in priced_materia}
    results: list[OptimizationResult] = []
    seen_multisets: set[tuple] = set()

    for iteration in range(top_k):
        status_code = prob.solve(solver)
        status_name = pulp.LpStatus.get(status_code, "Unknown")

        if status_name != "Optimal":
            if iteration == 0:
                results.append(
                    OptimizationResult(
                        status=status_name,
                        total_cost=0.0,
                        total_stats={k: 0 for k in STAT_KEYS},
                        target_stats={k: int(targets.get(k, 0)) for k in STAT_KEYS},
                        base_stats={k: int(base_stats.get(k, 0)) for k in STAT_KEYS},
                        assignments=[],
                        unfilled=0,
                    )
                )
            break

        assignments: list[dict] = []
        total_stats = {k: 0 for k in STAT_KEYS}
        total_cost = 0.0
        unfilled = 0
        active_vars: list[tuple[int, int, int]] = []

        for p_idx, piece in enumerate(pieces):
            slot_conf = slot_configs[piece.slot_key]
            if piece.locked:
                assignments.append(
                    {
                        "piece_index": p_idx,
                        "piece_label": piece.label,
                        "socket_index": -1,
                        "socket_kind": "locked",
                        "materia": None,
                        "locked": True,
                        "locked_contribution": {
                            stat: int(piece.locked_contribution.get(stat, 0))
                            for stat in STAT_KEYS
                        },
                    }
                )
                for stat in STAT_KEYS:
                    total_stats[stat] += int(piece.locked_contribution.get(stat, 0))
                continue
            for s_idx in range(slot_conf.total_sockets):
                chosen: Materia | None = None
                chosen_key: tuple[int, int, int] | None = None
                for mid in socket_index[(p_idx, s_idx)]:
                    v = x[(p_idx, s_idx, mid)]
                    if v.value() and v.value() > 0.5:
                        chosen = materia_by_id[mid]
                        chosen_key = (p_idx, s_idx, mid)
                        break
                socket_kind = (
                    "安全孔"
                    if _socket_is_safe(slot_conf, s_idx)
                    else ("overmeld-1" if _socket_is_first_overmeld(slot_conf, s_idx) else "overmeld")
                )
                if chosen is None:
                    unfilled += 1
                    assignments.append(
                        {
                            "piece_index": p_idx,
                            "piece_label": piece.label,
                            "socket_index": s_idx,
                            "socket_kind": socket_kind,
                            "materia": None,
                        }
                    )
                else:
                    active_vars.append(chosen_key)
                    pos = socket_position_label(s_idx, slot_conf.safe_sockets)
                    rate = get_success_rate(success_rates, pos, chosen.tier)
                    exp_cost = prices[chosen.item_id] / max(rate, 1e-6)
                    assignments.append(
                        {
                            "piece_index": p_idx,
                            "piece_label": piece.label,
                            "socket_index": s_idx,
                            "socket_kind": socket_kind,
                            "materia": {
                                "item_id": chosen.item_id,
                                "name": chosen.name,
                                "series": chosen.series,
                                "tier": chosen.tier,
                                "stat_type": chosen.stat_type,
                                "stat_value": chosen.stat_value,
                                "price": prices[chosen.item_id],
                                "success_rate": rate,
                                "expected_cost": exp_cost,
                            },
                        }
                    )
                    total_stats[chosen.stat_type] += chosen.stat_value
                    # total_cost is the EXPECTED cost now — factors in meld rate.
                    total_cost += exp_cost

        # Dedup by materia multiset — two layouts that use the same bag of
        # materia are effectively the same solution to the player, even if
        # the arrangement across sockets differs.
        multiset = tuple(sorted(k[2] for k in active_vars))
        if multiset not in seen_multisets:
            seen_multisets.add(multiset)
            display_total = {
                k: total_stats[k] + base_stats.get(k, 0) for k in STAT_KEYS
            }
            results.append(
                OptimizationResult(
                    status=status_name,
                    total_cost=total_cost,
                    total_stats=display_total,
                    target_stats={k: int(targets.get(k, 0)) for k in STAT_KEYS},
                    base_stats={k: int(base_stats.get(k, 0)) for k in STAT_KEYS},
                    assignments=assignments,
                    unfilled=unfilled,
                )
            )

        # No-good cut: forbid exactly this assignment vector on next solve.
        # Σ (selected vars) ≤ N-1 forces at least one selection to flip.
        if iteration + 1 < top_k and active_vars:
            prob += (
                pulp.lpSum(x[k] for k in active_vars) <= len(active_vars) - 1,
                f"no_good_cut_{iteration}",
            )

    return results
