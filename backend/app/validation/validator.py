"""Deterministic plan validator — the safety authority. No LLM is consulted here.

Rules
  R1 ROAD_SAFETY          routes must be valid traversals of the real road graph (direction-aware)
                          and may not use closed segments or the reported hazard area
  R2 INFRA_DEPENDENCY     routes may not depend on signals the plan (or current state) leaves dark
                          (signal locations are real OSM data; their power model is simulated)
  R3 ACTION_VALIDITY      action types, targets and parameters must match the allowlist; one route per
                          purpose, and a route's service must match its purpose
  R4 APPROVAL_REQUIRED    high-impact actions need operator approval of this exact plan version
  R5 STALE_STATE          a plan validated/approved against an older city state must be revalidated
  R6 EVIDENCE             high-impact actions need sensor-corroborated or operator-confirmed evidence
  R7 UNRESOLVED_CONFLICT  added by the coordinator when revisions are exhausted
  R8 TRAFFIC_DATA         warnings on the traffic picture: no live feed, a failed analysis, or an
                          analysis made for another plan or city-state version
  R9 DEPARTMENTS          a route only for a department the incident requires (blocking); missing
                          reasons and routes that could not be calculated are reported
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.agents.services import DEPARTMENT_LABEL, PURPOSE_SERVICE, plan_departments
from app.schemas.agents import SUPPORTING_STATUSES, EvidenceAssessment
from app.simulation.state import ActionSpec, CitySnapshot, apply_actions
from app.validation.actions import ACTION_TYPES, ROUTE_ACTIONS, ROUTE_SERVICE, validate_params


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
    traffic: dict[str, Any] | None = None,
    plan_version: int | None = None,
    departments: list[dict[str, Any]] | None = None,
    route_legs: list[dict[str, Any]] | None = None,
) -> ValidationResult:
    findings: list[Finding] = []

    # R5 — stale state ------------------------------------------------------------------------
    if expected_state_version is not None and expected_state_version != snapshot.state_version:
        findings.append(Finding(
            "R5_STALE_STATE", "blocking", "City state changed since this plan was checked",
            "The city state has changed since this plan was checked, so its safety conclusions no longer hold.",
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

    # One route per purpose, and each route's service must match its purpose: no duplicate or
    # contradictory dispatch (e.g. a "police response" that starts at a fire station).
    by_purpose: dict[str, list[ActionSpec]] = {}
    for a in valid:
        if a.action_type in ROUTE_ACTIONS:
            by_purpose.setdefault(a.params["purpose"], []).append(a)
            service = a.params.get("service")
            if service is not None and ROUTE_SERVICE.get(a.params["purpose"]) != service:
                findings.append(Finding(
                    "R3_ACTION_VALIDITY", "blocking", "Route service does not match its purpose",
                    f"Action #{a.seq} is a {a.params['purpose'].replace('_', ' ')} route labelled for {service}.",
                    "Recompute the route from a facility of the right service.", agent=a.proposed_by, action_seq=a.seq))
    for purpose, items in by_purpose.items():
        for dup in items[1:]:
            findings.append(Finding(
                "R3_ACTION_VALIDITY", "blocking", f"Duplicate {purpose.replace('_', ' ')} route",
                f"Action #{dup.seq} designates a second {purpose.replace('_', ' ')} route (first: #{items[0].seq}).",
                "Keep one route per purpose.", agent=dup.proposed_by, action_seq=dup.seq))

    projected, changes = apply_actions(snapshot, valid)
    closed_by_plan = {s for a in valid if a.action_type == "close_roads" for s in _segments(a)}
    caution_by_plan = {s for a in valid if a.action_type == "set_roads_caution" for s in _segments(a)}
    contradictory_controls = sorted(closed_by_plan & caution_by_plan)
    if contradictory_controls:
        for a in valid:
            if a.action_type != "set_roads_caution":
                continue
            overlap = sorted(set(_segments(a)) & set(contradictory_controls))
            if overlap:
                findings.append(Finding(
                    "R1_ROAD_SAFETY", "blocking", "Contradictory road controls in one plan",
                    f"Action #{a.seq} cautions segment(s) that another action closes: {', '.join(overlap[:6])}.",
                    "Reconcile the traffic recommendation with the closure; a segment cannot be both closed and cautioned.",
                    agent=a.proposed_by, action_seq=a.seq, evidence={"segments": overlap},
                ))
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

    # R8 — traffic-data quality --------------------------------------------------------------
    # Warnings only: unsafe routes are blocked by R1/R2 above. R8 says how much the traffic picture
    # behind the plan can be trusted (no live feed, a failed analysis, or one made for another version).
    if traffic is not None:
        tv_state, tv_plan = traffic.get("state_version"), traffic.get("plan_version")
        if traffic.get("status") == "failed":
            findings.append(Finding(
                "R8_TRAFFIC_DATA", "warning", "Traffic analysis unavailable",
                f"AnalyseTraffic could not assess this plan ({traffic.get('error') or 'error'}). Routes were still "
                "checked against closures, the hazard area and power dependencies.",
                "Review the routes manually before approving.", agent="traffic",
                evidence={"status": "failed", "plan_version": tv_plan}))
        elif (tv_state is not None and tv_state != snapshot.state_version) or (
                plan_version is not None and tv_plan is not None and tv_plan != plan_version):
            findings.append(Finding(
                "R8_TRAFFIC_DATA", "warning", "Traffic analysis is out of date",
                "The traffic analysis was made for a different plan version or an earlier city state.",
                "Re-run the analysis; do not rely on its route assessments.", agent="traffic",
                evidence={"analysis_plan_version": tv_plan, "analysis_state_version": tv_state,
                          "plan_version": plan_version, "current_state_version": snapshot.state_version}))
        if traffic.get("live_traffic", "unavailable") != "available":
            findings.append(Finding(
                "R8_TRAFFIC_DATA", "warning", "Live traffic data unavailable",
                "Traffic impacts and travel times are estimated from the versioned OSM road graph and simulated "
                "closures; no live congestion or signal timing was observed.",
                "Treat the estimate as uncertain and verify current access through authorized operators before any "
                "real-world action. GridPulse execution remains simulation-only.",
                agent="traffic", evidence={"live_traffic": traffic.get("live_traffic", "unavailable"),
                                             "data_source": traffic.get("data_source"),
                                             "freshness": traffic.get("freshness")},
            ))
        for ra in traffic.get("route_assessments", []):
            if ra.get("status") != "blocked" and not ra.get("alternative") and ra.get("alternative_note"):
                findings.append(Finding(
                    "R8_TRAFFIC_DATA", "info", f"No alternative for {ra['name']}",
                    ra["alternative_note"], "If this route becomes blocked there is no fallback; monitor it.",
                    agent="traffic", evidence={"purpose": ra.get("purpose")}))

    # R9 — departments ------------------------------------------------------------------------
    # Hard rule: a route (simulated dispatch) only for a department the incident requires. Missing reasons
    # and routes that could not be calculated are surfaced, never hidden.
    if departments is not None:
        depts = plan_departments(departments, route_legs or [], valid)
        status = {d["service"]: d["status"] for d in depts}
        for r in routes:
            svc = r.params.get("service") or PURPOSE_SERVICE.get(r.params["purpose"])
            if status.get(svc) != "required":
                findings.append(Finding(
                    "R9_DEPARTMENTS", "blocking", f"{DEPARTMENT_LABEL.get(svc, svc)} is not justified for this incident",
                    f"Action #{r.seq} routes {DEPARTMENT_LABEL.get(svc, svc)}, but the department assessment marks it "
                    f"{(status.get(svc) or 'not assessed').replace('_', ' ')}.",
                    "Remove the route, or record evidence that makes the department required.",
                    agent=r.proposed_by, action_seq=r.seq, evidence={"service": svc, "status": status.get(svc)}))
        for d in depts:
            if d["status"] != "required":
                continue
            if not (d.get("rationale") or "").strip():
                findings.append(Finding("R9_DEPARTMENTS", "warning", f"{d['label']}: reason not provided — review required",
                                        "The department is required but no justification was recorded.",
                                        "Review the report before approving.", agent="coordinator",
                                        evidence={"service": d["service"]}))
            for leg in d["routes"]:
                if leg["status"] == "planned":
                    continue
                if d["service"] == "ambulance" and leg["leg"] == "to_incident":
                    findings.append(Finding("R9_DEPARTMENTS", "info", "Ambulance starting point unknown", leg["reason"],
                                            "Confirm where the ambulance will come from through authorized channels.",
                                            agent="mobility", evidence=leg))
                elif leg["status"] == "withheld":
                    findings.append(Finding("R9_DEPARTMENTS", "info", f"{d['label']} route withheld", leg["reason"],
                                            None, agent="coordinator", evidence=leg))
                else:
                    findings.append(Finding("R9_DEPARTMENTS", "warning",
                                            f"No valid road route could be calculated for {d['label']}", leg["reason"],
                                            "Plan this department's access manually; the plan does not include it.",
                                            agent="mobility", evidence=leg))

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
