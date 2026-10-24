"""Deterministic plan validator — the safety authority. No LLM is consulted here.

Rules
  R1 ROAD_SAFETY          routes must be valid traversals of the real road graph (direction-aware)
                          and may not use closed segments or the reported hazard area
  R2 INFRA_DEPENDENCY     routes may not depend on signals the plan (or current state) leaves dark
                          (signal locations are real OSM data; their power model is simulated)
  R3 ACTION_VALIDITY      action types, targets and parameters must match the allowlist
  R4 APPROVAL_REQUIRED    high-impact actions need operator approval of this exact plan version
  R5 STALE_STATE          a plan validated/approved against an older city state must be revalidated
  R6 EVIDENCE             high-impact actions need sensor-corroborated or operator-confirmed evidence
  R7 UNRESOLVED_CONFLICT  added by the coordinator when revisions are exhausted
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.schemas.agents import SUPPORTING_STATUSES, EvidenceAssessment
from app.simulation.state import ActionSpec, CitySnapshot, apply_actions
from app.validation.actions import ACTION_TYPES, ROUTE_ACTIONS, validate_params


@dataclass
class Finding:
    rule_id: str
    severity: str  # blocking | warning | info
    title: str
    message: str
    remediation: str | None = None
    agent: str | None = None
    action_seq: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def can_proceed(self) -> bool:
        return self.severity != "blocking"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["can_proceed"] = self.can_proceed
        return d


@dataclass
class ValidationResult:
    findings: list[Finding]
    projected_changes: list[dict[str, Any]]

    @property
    def passed(self) -> bool:
        return not any(f.severity == "blocking" for f in self.findings)

    def blocking(self, rule_id: str | None = None) -> list[Finding]:
        return [f for f in self.findings if f.severity == "blocking" and (rule_id is None or f.rule_id == rule_id)]

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)


def _segments(a: ActionSpec) -> list[str]:
    return list(a.params.get("segment_ids") or [])


def _target_ok(snapshot: CitySnapshot, a: ActionSpec, kind: str) -> str | None:
    if kind == "road":
        if snapshot.road(a.target_id) is None:
            return f"'{a.target_id}' is not a road segment in the {snapshot.city_name} graph."
        missing = [s for s in _segments(a) if snapshot.road(s) is None]
        if missing:
            return f"Unknown road segments: {', '.join(missing[:5])}."
        return None
    if kind == "intersection":
        return None if snapshot.has_road_node(a.target_id) else f"'{a.target_id}' is not a node of the road graph."
    node = snapshot.nodes.get(a.target_id)
    if node is None or node.kind != kind:
        return f"'{a.target_id}' is not a known {kind} in the infrastructure model."
    return None


def validate_plan(
    actions: list[ActionSpec],
    snapshot: CitySnapshot,
    evidence: EvidenceAssessment | None,
    *,
    expected_state_version: int | None = None,
) -> ValidationResult:
    findings: list[Finding] = []

    # R5 — stale state ------------------------------------------------------------------------
    if expected_state_version is not None and expected_state_version != snapshot.state_version:
        findings.append(Finding(
            "R5_STALE_STATE", "blocking", "City state changed since this plan was checked",
            f"The plan was validated against city state v{expected_state_version}, but the city is now at "
            f"v{snapshot.state_version}. Its safety conclusions no longer hold.",
            "Revalidate the plan against the current state and request a fresh approval.",
            evidence={"expected_state_version": expected_state_version, "current_state_version": snapshot.state_version},
        ))

    # R3 — action validity --------------------------------------------------------------------
    valid: list[ActionSpec] = []
    seen: set[tuple[str, str, str]] = set()
    for a in actions:
        spec = ACTION_TYPES.get(a.action_type)
        if spec is None:
            findings.append(Finding(
                "R3_ACTION_VALIDITY", "blocking", f"Unknown action '{a.action_type}'",
                f"Action #{a.seq} uses a type that is not on the simulation allowlist.",
                "Remove the action; only allowlisted simulated actions can be executed.",
                agent=a.proposed_by, action_seq=a.seq, evidence={"allowed": sorted(ACTION_TYPES)},
            ))
            continue
        err = validate_params(a.action_type, a.params)
        if err:
            findings.append(Finding("R3_ACTION_VALIDITY", "blocking", f"Malformed parameters for {a.action_type}",
                                    f"Action #{a.seq}: {err}", "Supply parameters that match the action schema.",
                                    agent=a.proposed_by, action_seq=a.seq))
            continue
        bad_target = _target_ok(snapshot, a, spec.target_kind)
        if bad_target:
            findings.append(Finding("R3_ACTION_VALIDITY", "blocking", f"Invalid target for {a.action_type}",
                                    f"Action #{a.seq}: {bad_target}", "Target an existing element of the correct kind.",
                                    agent=a.proposed_by, action_seq=a.seq))
            continue
        if a.action_type == "transfer_signal_power":
            bad = _check_transfer(snapshot, a)
            if bad:
                findings.append(Finding("R3_ACTION_VALIDITY", "blocking", "Invalid power transfer", bad,
                                        "Transfer only between existing feeds of this signal.",
                                        agent=a.proposed_by, action_seq=a.seq))
                continue
        key = (a.action_type, a.target_id, str(a.params.get("purpose", "")))
        if key in seen and a.action_type != "issue_public_advisory":
            findings.append(Finding("R3_ACTION_VALIDITY", "warning", "Duplicate action",
                                    f"Action #{a.seq} repeats {a.action_type} on {a.target_id}.",
                                    "Remove the duplicate.", agent=a.proposed_by, action_seq=a.seq))
        seen.add(key)
        valid.append(a)

    projected, changes = apply_actions(snapshot, valid)
    closed_by_plan = {s for a in valid if a.action_type == "close_roads" for s in _segments(a)}
    routes = [a for a in valid if a.action_type in ROUTE_ACTIONS]

    # R1 — road safety ------------------------------------------------------------------------
    hazard_segments: set[str] = set()
    hazard_status = None
    if evidence and evidence.location_hazard and evidence.location_hazard.status != "NOT_INDICATED":
        hazard_segments = set(evidence.location_hazard.segment_ids)
        hazard_status = evidence.location_hazard.status
    currently_closed = snapshot.closed_segments()
    for r in routes:
        p = r.params
        problems = snapshot.graph.is_continuous(p["node_path"], p["edge_path"])
        if p["node_path"][0] != p["origin"] or p["node_path"][-1] != p["destination"]:
            problems.insert(0, "Route does not start at its origin or end at its destination.")
        if problems:
            findings.append(Finding("R1_ROAD_SAFETY", "blocking", "Route is not a valid path on the road graph",
                                    " ".join(problems[:3]), "Recompute the route on the road graph.",
                                    agent=r.proposed_by, action_seq=r.seq, evidence={"problems": problems[:10]}))
        edges = p["edge_path"]
        for label, bad, why in (
            ("closed in the current city state", [e for e in edges if e in currently_closed], "closed"),
            ("closed by this same plan", [e for e in edges if e in closed_by_plan and e not in currently_closed], "plan_closed"),
            (f"inside the reported hazard area (evidence {hazard_status})",
             [e for e in edges if e in hazard_segments and e not in closed_by_plan and e not in currently_closed], "hazard"),
        ):
            if bad:
                names = sorted({snapshot.road(e).name for e in bad if snapshot.road(e)})
                findings.append(Finding(
                    "R1_ROAD_SAFETY", "blocking", f"{p['name']} uses {len(bad)} road segment(s) {label}",
                    f"Action #{r.seq} travels {', '.join(names[:4])}{'…' if len(names) > 4 else ''}, which is {label}.",
                    "Reroute around these segments.", agent=r.proposed_by, action_seq=r.seq,
                    evidence={"segments": bad, "reason": why}))
        caution = [e for e in edges if snapshot.road_status.get(e) == "caution"]
        if caution:
            findings.append(Finding("R1_ROAD_SAFETY", "warning", f"{p['name']} uses {len(caution)} caution segment(s)",
                                    "Some segments on the route are under caution.", "Prefer open segments if available.",
                                    agent=r.proposed_by, action_seq=r.seq, evidence={"segments": caution}))

    # R2 — infrastructure dependencies --------------------------------------------------------
    utility_actions = [a for a in valid if ACTION_TYPES[a.action_type].owner == "grid"]
    for r in routes:
        for nid in r.params["node_path"]:
            for sig in projected.signals_controlling(nid):
                if projected.nodes[sig].status != "dark":
                    continue
                cause = _darkening_cause(snapshot, utility_actions, sig)
                place = snapshot.node_label(nid)
                if cause is not None:
                    sub = cause.target_id
                    findings.append(Finding(
                        "R2_INFRA_DEPENDENCY", "blocking",
                        f"Isolating {sub} disables {sig}, which {r.params['name']} depends on",
                        f"Grid action #{cause.seq} isolates {snapshot.nodes[sub].name} (simulated). In the simulated power "
                        f"model {sig} is fed only by {sub}, so it goes dark. Mobility action #{r.seq} routes responders "
                        f"through {place} ({nid}), the real OSM junction this signal controls. It would become an "
                        f"uncontrolled junction on the route.",
                        "Reroute around the junction, or move the signal to an energized backup feed before isolating.",
                        agent="mobility", action_seq=r.seq,
                        evidence={"dependency_chain": [sub, sig, nid], "route_action_seq": r.seq,
                                  "utility_action_seq": cause.seq, "involved_agents": ["mobility", "grid"],
                                  "signal": sig, "intersection": nid, "substation": sub, "simulated_power_model": True},
                    ))
                else:
                    findings.append(Finding(
                        "R2_INFRA_DEPENDENCY", "blocking", f"{r.params['name']} depends on dark signal {sig}",
                        f"{snapshot.nodes[sig].name} has no power in the projected (simulated) state; the route passes {place}.",
                        "Reroute or restore power to the signal.", agent="mobility", action_seq=r.seq,
                        evidence={"dependency_chain": [sig, nid], "signal": sig, "intersection": nid,
                                  "involved_agents": ["mobility"]},
                    ))

    for a in utility_actions:
        if a.action_type != "isolate_substation":
            continue
        lost = [c for c in changes if c["entity"] == "node" and c["after"] in ("dark", "offline")]
        if lost:
            findings.append(Finding(
                "R2_INFRA_DEPENDENCY", "info", f"Downstream effects of isolating {a.target_id} (simulated)",
                "Projected consequences: " + ", ".join(f"{c['name']} → {c['after']}" for c in lost) + ".",
                None, agent="grid", action_seq=a.seq, evidence={"consequences": lost},
            ))

    # R6 — evidence ---------------------------------------------------------------------------
    if evidence is not None:
        for a in valid:
            spec = ACTION_TYPES[a.action_type]
            if spec.impact != "high":
                continue
            hazard = None
            if a.action_type in ROUTE_ACTIONS or a.action_type == "close_roads":
                hazard = evidence.location_hazard
                if hazard is None:
                    findings.append(Finding("R6_EVIDENCE", "blocking", f"{spec.label} has no hazard evidence",
                                            "The incident has no assessed location hazard.",
                                            "Locate the incident on the road graph first.", agent=a.proposed_by,
                                            action_seq=a.seq))
                    continue
            elif a.action_type == "isolate_substation":
                hazard = evidence.hazard_for(a.target_id)
                if hazard is None:
                    findings.append(Finding(
                        "R6_EVIDENCE", "blocking", f"No evidence supports isolating {a.target_id}",
                        "No sensor reading or operator confirmation indicates a hazard in this feeder zone.",
                        "Drop the isolation or gather evidence first.", agent=a.proposed_by, action_seq=a.seq))
                    continue
            if hazard is None:
                continue
            if hazard.status not in SUPPORTING_STATUSES:
                findings.append(Finding(
                    "R6_EVIDENCE", "blocking", f"{spec.label} relies on {hazard.status.lower()} evidence",
                    f"Action #{a.seq} responds to {hazard.hazard.replace('_', ' ')} at {hazard.target_name}, but the "
                    f"evidence is {hazard.status}: {hazard.explanation}",
                    "Use verification-first steps (caution, field check, advisory), or have an operator confirm the "
                    "hazard on record, before high-impact actions.",
                    agent=a.proposed_by, action_seq=a.seq, evidence={"hazard": hazard.model_dump(mode="json")},
                ))
        for w in evidence.warnings:
            findings.append(Finding("R6_EVIDENCE", "warning", "Evidence quality warning", w,
                                    "Treat affected readings as unverified; prefer field verification.", agent="sentinel"))

    # R4 — approval requirement ---------------------------------------------------------------
    high = [a for a in valid if ACTION_TYPES[a.action_type].impact == "high"]
    findings.append(Finding(
        "R4_APPROVAL_REQUIRED", "info", "Operator approval required",
        f"{len(high)} high-impact and {len(valid) - len(high)} low-impact simulated action(s). Nothing executes "
        "until an authenticated operator approves this exact plan version against the current city state.",
        evidence={"high_impact_seqs": [a.seq for a in high]},
    ))
    if not valid:
        findings.append(Finding("R3_ACTION_VALIDITY", "blocking", "Plan has no executable actions",
                                "There is nothing valid to execute.", "Escalate to a human coordinator."))

    return ValidationResult(findings, changes)


def _check_transfer(snapshot: CitySnapshot, a: ActionSpec) -> str | None:
    feeds = {f.id: f for f in snapshot.feeds_for(a.target_id)}
    src, dst = a.params["from_feed"], a.params["to_feed"]
    if src not in feeds or dst not in feeds:
        return f"Feeds {src} → {dst} are not both feeds of {a.target_id}."
    if feeds[dst].status != "standby":
        return f"Backup feed {dst} is not in standby."
    if snapshot.nodes[feeds[dst].source].status != "energized":
        return f"Backup source {feeds[dst].source} is not energized."
    return None


def _darkening_cause(snapshot: CitySnapshot, utility_actions: list[ActionSpec], signal_id: str) -> ActionSpec | None:
    for a in utility_actions:
        if a.action_type == "isolate_substation" and signal_id in snapshot.dependents_of(a.target_id):
            return a
    return None
