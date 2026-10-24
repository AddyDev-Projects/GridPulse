"""Bounded plan revision driven by validator findings.

Given a blocked plan and the findings that blocked it, ask the responsible agents for a
remediation and return a genuinely different action list, or a failure that explains why
no remediation exists. Pure function: the coordinator persists the result.

Remediation policy (deterministic, in order):
  1. R3 invalid actions are dropped.
  2. R6 evidence findings → verification-first: closures become cautions, routes and isolations
     are withheld, and a field check + advisory are added.
  3. R1/R2 route findings → (a) Mobility recomputes the routes avoiding closed segments and the
     junctions whose signals the plan darkens; preferred because it adds no new infrastructure
     switching. (b) Otherwise Grid moves the affected signals to an energized backup feed before
     isolation (simulated). (c) Otherwise no safe plan exists.
Hazard-driven isolations are never dropped to make a route work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents import grid, mobility
from app.agents.services import ServicePlan
from app.agents.traffic import SENSITIVE_CATEGORIES
from app.schemas.agents import EvidenceAssessment, IncidentGeo, SentinelAssessment
from app.simulation.state import ActionSpec, CitySnapshot, apply_actions
from app.validation.actions import ACTION_TYPES, ROUTE_ACTIONS
from app.validation.validator import ValidationResult

ORDER = {
    "close_roads": 0, "set_roads_caution": 0, "transfer_signal_power": 1, "isolate_substation": 2,
    "designate_response_route": 3, "dispatch_field_verification": 4, "issue_public_advisory": 5,
}


@dataclass
class AgentContribution:
    agent: str
    step: str
    input: dict[str, Any]
    output: dict[str, Any]
    summary: str


@dataclass
class RevisionOutcome:
    actions: list[ActionSpec]
    strategy: str
    rationale: str
    alternatives: list[dict[str, Any]]
    contributions: list[AgentContribution]
    triggered_by: list[dict[str, Any]]


@dataclass
class RevisionFailure:
    reason: str
    alternatives: list[dict[str, Any]]
    contributions: list[AgentContribution] = field(default_factory=list)


def make_action(seq: int, p: mobility.ProposedAction, owner: str | None = None) -> ActionSpec:
    spec = ACTION_TYPES[p.action_type]
    return ActionSpec(seq, p.action_type, p.target_id, p.params, owner or spec.owner, spec.impact, p.rationale)


def resequence(actions: list[ActionSpec]) -> list[ActionSpec]:
    ordered = sorted(actions, key=lambda a: (ORDER.get(a.action_type, 9), a.seq))
    return [ActionSpec(i + 1, a.action_type, a.target_id, a.params, a.proposed_by, a.impact, a.rationale)
            for i, a in enumerate(ordered)]


def _signature(actions: list[ActionSpec]) -> list[str]:
    import json

    return sorted(json.dumps([a.action_type, a.target_id, a.params], sort_keys=True) for a in actions)


def revise(
    actions: list[ActionSpec],
    result: ValidationResult,
    snapshot: CitySnapshot,
    assessment: SentinelAssessment,
    evidence: EvidenceAssessment,
    geo: IncidentGeo,
    indicators: list[str],
    *,
    services: ServicePlan | None = None,
    category: str | None = None,
) -> RevisionOutcome | RevisionFailure:
    blocking = result.blocking()
    triggered = [{"rule_id": f.rule_id, "title": f.title, "action_seq": f.action_seq} for f in blocking]
    new = list(actions)
    strategies: list[str] = []
    rationale: list[str] = []
    alternatives: list[dict[str, Any]] = []
    contributions: list[AgentContribution] = []
    next_seq = max((a.seq for a in actions), default=0) + 1

    # 1 — invalid actions
    invalid = {f.action_seq for f in blocking if f.rule_id == "R3_ACTION_VALIDITY" and f.action_seq is not None}
    if invalid:
        new = [a for a in new if a.seq not in invalid]
        strategies.append("drop_invalid_actions")
        rationale.append(f"Dropped actions {sorted(invalid)} that are not on the allowlist or are malformed.")

    # 2 — evidence
    ev_seqs = {f.action_seq for f in blocking if f.rule_id == "R6_EVIDENCE" and f.action_seq is not None}
    if ev_seqs:
        kept: list[ActionSpec] = []
        for a in new:
            if a.seq not in ev_seqs:
                kept.append(a)
            elif a.action_type == "close_roads":
                kept.append(make_action(a.seq, mobility.ProposedAction(
                    "set_roads_caution", a.target_id, {**a.params, "reason": "Unverified hazard report"},
                    "Evidence does not support the hazard; warn drivers instead of closing until verified.")))
                rationale.append(f"Replaced closure of {len(a.params['segment_ids'])} segment(s) with a caution status.")
            else:
                rationale.append(f"Withheld {a.action_type.replace('_', ' ')} until the hazard is corroborated or "
                                 "confirmed by an operator.")
        new = kept
        loc = evidence.location_id
        name = snapshot.road(loc).name if loc and snapshot.road(loc) else "the reported location"
        if loc and not any(a.action_type == "dispatch_field_verification" for a in new):
            new.append(make_action(next_seq, mobility.ProposedAction(
                "dispatch_field_verification", loc, {"question": f"Confirm the reported hazard and passability at {name}."},
                "Sentinel requests ground truth because the hazard is not corroborated."), "sentinel"))
            next_seq += 1
        # No public advisory for crime, violence or medical reports: it would broadcast a sensitive allegation.
        if loc and (category or assessment.category) not in SENSITIVE_CATEGORIES \
                and not any(a.action_type == "issue_public_advisory" for a in new):
            new.append(make_action(next_seq, mobility.ProposedAction(
                "issue_public_advisory", loc,
                {"message": f"Possible {(category or assessment.category).replace('_', ' ')} reported near {name}. Avoid the area "
                            "if possible while it is verified. (Simulation)"},
                "Low-impact public warning while the report is verified."), "coordinator"))
            next_seq += 1
        strategies.append("verification_first")
        contributions.append(AgentContribution(
            "sentinel", "revise_evidence", {"findings": [f.as_dict() for f in blocking if f.rule_id == "R6_EVIDENCE"]},
            {"hazard": evidence.location_hazard.model_dump(mode="json") if evidence.location_hazard else None},
            "Hazard not corroborated; recommends verification before high-impact action."))

    # 3 — route safety / dependencies
    route_findings = [f for f in blocking if f.rule_id in ("R1_ROAD_SAFETY", "R2_INFRA_DEPENDENCY")]
    route_actions = [a for a in new if a.action_type in ROUTE_ACTIONS]
    flagged = {f.action_seq for f in route_findings}
    if route_findings and any(a.seq in flagged for a in route_actions):
        projected, _ = apply_actions(snapshot, new)
        dark_junctions = {
            nid for s in projected.nodes.values() if s.kind == "signal" and s.status == "dark"
            for nid in [e.target for e in projected.edges_of_kind("controls") if e.source == s.id]
        }
        closed = {sid for a in new if a.action_type == "close_roads" for sid in a.params["segment_ids"]}
        isolating = {a.target_id for a in new if a.action_type == "isolate_substation"}
        dark_on_routes = sorted({s for a in route_actions for nid in a.params["node_path"]
                                 for s in projected.signals_controlling(nid) if projected.nodes[s].status == "dark"})

        mp = mobility.propose(snapshot, assessment, evidence, geo, indicators, services=services,
                              exclude_nodes=dark_junctions, exclude_edges=closed)
        contributions.append(AgentContribution(
            "mobility", "revise_route",
            {"findings": [f.as_dict() for f in route_findings], "avoid_junctions": sorted(dark_junctions),
             "avoid_segments": len(closed)},
            mp.as_dict(), mp.summary))
        old = {a.params["purpose"]: a for a in route_actions}
        new_routes = {r.purpose: r for r in mp.routes}
        reroute_ok = all(p in new_routes for p in old)
        for purpose, a in old.items():
            if purpose in new_routes:
                r = new_routes[purpose].route
                alternatives.append({
                    "strategy": "mobility_reroute", "agent": "mobility", "viable": True, "purpose": purpose,
                    "detail": f"{new_routes[purpose].name}: {r.length_m:.0f} m ({r.length_m - a.params['length_m']:+.0f} m), "
                              f"~{r.travel_s / 60:.1f} min, avoiding {', '.join(sorted(dark_junctions)) or 'closed roads'}.",
                    "length_m": r.length_m})
            else:
                alternatives.append({"strategy": "mobility_reroute", "agent": "mobility", "viable": False,
                                     "purpose": purpose,
                                     "detail": f"No feasible {purpose.replace('_', ' ')} route avoids the closures and "
                                               f"junctions {', '.join(sorted(dark_junctions)) or '(none)'}."})

        only_power = all(f.rule_id == "R2_INFRA_DEPENDENCY" for f in route_findings)
        b_actions, b_problems = ([], ["Route problem is not a power dependency."])
        if dark_on_routes:
            b_actions, b_problems = grid.backup_power_options(snapshot, dark_on_routes, isolating)
            contributions.append(AgentContribution(
                "grid", "revise_power", {"signals": dark_on_routes, "isolating": sorted(isolating)},
                {"actions": [a.__dict__ for a in b_actions], "problems": b_problems, "simulated_model": True},
                "; ".join(b_problems) if b_problems else f"Backup feeds available for {', '.join(dark_on_routes)}."))
        backup_viable = bool(dark_on_routes) and not b_problems and only_power
        alternatives.append({
            "strategy": "grid_backup_power", "agent": "grid", "viable": backup_viable,
            "detail": ("Transfer " + ", ".join(f"{a.target_id} to {a.params['to_feed']}" for a in b_actions)
                       + " before isolating (simulated); keeps the original routes.") if backup_viable
                      else " ".join(b_problems) or "Not applicable.",
        })

        if reroute_ok:
            replaced = []
            for a in new:
                if a.action_type in ROUTE_ACTIONS and a.params["purpose"] in new_routes:
                    pr = new_routes[a.params["purpose"]]
                    replaced.append(make_action(a.seq, pr.action(
                        f"Rerouted to avoid {', '.join(sorted(dark_junctions)) or 'closed segments'} after validator findings."),
                        "mobility"))
                else:
                    replaced.append(a)
            new = replaced
            strategies.append("mobility_reroute")
            rationale.append("Mobility recomputed the routes on the road graph avoiding "
                             f"{', '.join(sorted(dark_junctions)) or 'the closed segments'}. Selected over backup power "
                             "transfer because it adds no further high-impact switching.")
            for alt in alternatives:
                alt["selected"] = alt["strategy"] == "mobility_reroute" and alt.get("viable", False)
        elif backup_viable:
            for b in b_actions:
                new.append(make_action(next_seq, b, "grid"))
                next_seq += 1
            strategies.append("grid_backup_power")
            rationale.append("No alternative route exists; Grid moves " + ", ".join(dark_on_routes)
                             + " to an energized backup feed before isolating (simulated), keeping the routes controlled.")
            for alt in alternatives:
                alt["selected"] = alt["strategy"] == "grid_backup_power"
        else:
            return RevisionFailure(
                "No remediation resolves the conflict: " + " ".join(a["detail"] for a in alternatives),
                alternatives, contributions)

    unresolved = [f for f in blocking if f.rule_id not in ("R1_ROAD_SAFETY", "R2_INFRA_DEPENDENCY", "R3_ACTION_VALIDITY", "R6_EVIDENCE")]
    if unresolved:
        return RevisionFailure("Findings cannot be remediated automatically: "
                               + "; ".join(f.title for f in unresolved), alternatives, contributions)

    revised = resequence(new)
    if _signature(revised) == _signature(actions):
        return RevisionFailure("Revision produced no change to the plan.", alternatives, contributions)
    return RevisionOutcome(revised, "+".join(strategies) or "none", " ".join(rationale), alternatives, contributions, triggered)


def _route_summary(actions: list[ActionSpec]) -> dict[str, Any]:
    return {a.params["purpose"]: {"node_path": a.params["node_path"], "edge_path": a.params["edge_path"],
                                  "length_m": a.params["length_m"], "travel_s": a.params["travel_s"],
                                  "name": a.params["name"]}
            for a in actions if a.action_type in ROUTE_ACTIONS}


def diff_actions(old: list[ActionSpec], new: list[ActionSpec]) -> dict[str, Any]:
    def key(a: ActionSpec) -> tuple[str, str, str]:
        return (a.action_type, a.target_id, str(a.params.get("purpose", "")))

    def brief(a: ActionSpec) -> dict[str, Any]:
        params = {k: v for k, v in a.params.items() if k not in ("edge_path", "node_path")}
        if "segment_ids" in params:
            params["segment_count"] = len(params.pop("segment_ids"))
        return {"seq": a.seq, "action_type": a.action_type, "target_id": a.target_id, "params": params,
                "proposed_by": a.proposed_by, "impact": a.impact}

    old_map, new_map = {key(a): a for a in old}, {key(a): a for a in new}
    added = [brief(a) for k, a in new_map.items() if k not in old_map]
    removed = [brief(a) for k, a in old_map.items() if k not in new_map]
    changed, unchanged = [], []
    for k in old_map.keys() & new_map.keys():
        if old_map[k].params != new_map[k].params:
            changed.append({"before": brief(old_map[k]), "after": brief(new_map[k])})
        else:
            unchanged.append(brief(new_map[k]))
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged,
            "routes_before": _route_summary(old), "routes_after": _route_summary(new)}
