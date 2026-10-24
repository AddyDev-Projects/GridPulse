"""Coordinator — explicit, persisted workflow state machine for one incident.

REPORTED → TRIAGED → EVIDENCE_GATHERED → PLANS_PROPOSED → VALIDATING
   ├─ passed ─────────────────────────────────────────────→ AWAITING_APPROVAL
   └─ blocked → REVISION_REQUIRED → (revise) → VALIDATING_AGAIN → …   (≤ max_revisions)
        ├─ no remediation exists ─→ NO_SAFE_PLAN
        └─ revision limit reached ─→ ESCALATED
Any unexpected error → FAILED. Each step commits so the UI can follow progress, and nothing here
touches simulated city state — only an approved execution can.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents import grid, mobility, sentinel, traffic
from app.agents.services import ROUTE_PURPOSE, ServicePlan, select_services
from app.agents.evidence import assess_evidence, fetch_observations, relevant_sensors
from app.agents.geo_context import incident_geo
from app.core.config import get_settings
from app.db.base import utcnow
from app.db.session import session_factory
from app.llm.provider import get_provider
from app.models import AgentProposal, AgentRun, Incident, Operator, PlanRevision, ResponsePlan
from app.orchestration.revision import RevisionFailure, diff_actions, make_action, resequence, revise
from app.schemas.agents import OperatorConfirmation
from app.services import audit
from app.services.plans import append_finding, create_plan, store_validation
from app.simulation.repository import load_snapshot
from app.simulation.state import ActionSpec, CitySnapshot
from app.validation.validator import Finding, validate_plan

log = logging.getLogger("gridpulse.coordinator")
# A required responder that cannot reach the incident at all means there is no safe plan.
BASE_ROUTES = {ROUTE_PURPOSE["fire_rescue"], ROUTE_PURPOSE["police"]}


class Coordinator:
    def __init__(self, db: Session, incident: Incident):
        self.db = db
        self.incident = incident
        self.settings = get_settings()
        self.departments: list[dict[str, Any]] | None = None
        self.route_legs: list[dict[str, Any]] = []

    # ---- helpers ---------------------------------------------------------------------------
    def _pause(self) -> None:
        if self.settings.workflow_step_delay_ms > 0:
            time.sleep(self.settings.workflow_step_delay_ms / 1000)

    def _commit(self) -> None:
        self.db.commit()
        self._pause()

    def _run(self, agent: str, step: str, engine: str, inp: dict[str, Any], revision: int = 0) -> AgentRun:
        run = AgentRun(incident_id=self.incident.id, agent=agent, step=step, engine=engine, input=inp,
                       revision_number=revision, status="running", started_at=utcnow())
        self.db.add(run)
        self.db.flush()
        return run

    def _finish(self, run: AgentRun, output: dict[str, Any], *, model: str | None = None,
                fallback_used: bool = False, fallback_reason: str | None = None, engine: str | None = None) -> None:
        run.output = output
        run.status = "succeeded"
        run.model = model
        run.fallback_used = fallback_used
        run.fallback_reason = fallback_reason
        if engine:
            run.engine = engine
        run.finished_at = utcnow()
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)

    def _proposal(self, run: AgentRun, agent: str, summary: str, payload: dict[str, Any], revision: int = 0) -> AgentProposal:
        p = AgentProposal(incident_id=self.incident.id, agent_run_id=run.id, agent=agent, summary=summary,
                          payload=payload, revision_number=revision)
        self.db.add(p)
        self.db.flush()
        return p

    def _transition(self, status: str, reason: str | None = None) -> None:
        audit.transition(self.db, self.incident, status, reason)

    # ---- workflow --------------------------------------------------------------------------
    def run(self) -> None:
        inc = self.incident
        snapshot = load_snapshot(self.db)
        now = datetime.now(timezone.utc)
        audit.record(self.db, "workflow.started", "Coordinator started the agent workflow.", incident_id=inc.id,
                     actor="agent:coordinator", details={"state_version": snapshot.state_version})

        if inc.lat is None or inc.lon is None:
            raise RuntimeError("Incident has no map location; legacy incidents cannot be processed.")
        indicators = list(inc.indicators or [])
        reported_category = inc.category or "other"

        # 1. Sentinel triage (LLM if configured, else transparent rules) on the report text only.
        provider = get_provider(self.settings)
        pre_geo = incident_geo(snapshot, inc.lat, inc.lon, reported_category, indicators)
        run = self._run("sentinel", "triage", f"llm:{provider.name}" if provider else "rules",
                        {"report_text": inc.report_text, "reported_category": reported_category,
                         "indicators": indicators, "nearest_road": pre_geo.segment_name})
        outcome = sentinel.triage(inc.report_text, reported_category, indicators, pre_geo, provider)
        a = outcome.assessment
        self._finish(run, a.model_dump(mode="json"), model=outcome.model, fallback_used=outcome.fallback_used,
                     fallback_reason=outcome.fallback_reason, engine=outcome.engine)
        inc.urgency = a.urgency
        if outcome.fallback_used and provider is not None:
            audit.record(self.db, "llm.fallback", outcome.fallback_reason or "LLM failed", incident_id=inc.id,
                         actor="agent:sentinel", severity="warning")
        audit.record(self.db, "sentinel.assessment",
                     f"Sentinel assessed: {a.summary} (confidence {a.confidence:.2f}, engine {outcome.engine}).",
                     incident_id=inc.id, actor="agent:sentinel",
                     details={"category": a.category, "reported_category": reported_category, "urgency": a.urgency,
                              "fallback_used": outcome.fallback_used})
        self._transition("TRIAGED", a.summary)

        # Which responder services the report justifies. Recomputed on every run; never reused from an
        # earlier plan. Only "required" services get routes (app.agents.services).
        s_run = self._run("coordinator", "select_services", "deterministic",
                          {"reported_category": reported_category, "indicators": indicators,
                           "sentinel_recommendations": [r.responder_type for r in a.responder_recommendations]})
        services = select_services(reported_category, indicators, a)
        self._finish(s_run, {"services": services.as_list()})
        audit.record(self.db, "services.selected",
                     "Services: " + "; ".join(f"{d.service.replace('_', ' ')} {d.status.replace('_', ' ')}"
                                              for d in services.decisions) + ".",
                     incident_id=inc.id, actor="agent:coordinator", details={"services": services.as_list()})
        self._commit()

        # 2. Geography + evidence (deterministic). The citizen's category drives the hazard model;
        #    Sentinel's assessed category is advisory and shown alongside it.
        geo = incident_geo(snapshot, inc.lat, inc.lon, reported_category, indicators)
        inc.segment_id = geo.segment_id
        sensors = relevant_sensors(snapshot, geo)
        confirmation = None
        if inc.verified_at is not None:
            verifier = self.db.get(Operator, inc.verified_by_id) if inc.verified_by_id else None
            confirmation = OperatorConfirmation(operator=verifier.username if verifier else "unknown",
                                                confirmed_at=inc.verified_at, note=inc.verification_note)
        run = self._run("sentinel", "evidence", "deterministic",
                        {"lat": inc.lat, "lon": inc.lon, "segment_id": geo.segment_id, "sensors": sensors,
                         "operator_confirmed": confirmation is not None})
        rows = fetch_observations(self.db, snapshot, sensors)
        evidence = assess_evidence(snapshot, geo, reported_category, rows, now, self.settings.evidence_fresh_minutes,
                                   confirmation, electrical_hazard_reported=(
                                       "power_lines_down" in indicators or reported_category == "power_outage"))
        self._finish(run, evidence.model_dump(mode="json"))
        hz = evidence.location_hazard
        audit.record(self.db, "evidence.retrieved",
                     f"Snapped to {geo.segment_name or 'no road'}; {len(geo.hazard_segment_ids)} segment(s) in the "
                     f"{geo.hazard_radius_m:.0f} m hazard model; {len(evidence.observations)} synthetic observation(s); "
                     f"location hazard {hz.status if hz else 'n/a'}.",
                     incident_id=inc.id, actor="agent:sentinel",
                     severity="warning" if evidence.warnings or (hz and hz.status != "CORROBORATED") else "info",
                     details={"location_hazard": hz.model_dump(mode="json") if hz else None,
                              "infrastructure": [h.model_dump(mode="json") for h in evidence.infrastructure_hazards],
                              "warnings": evidence.warnings})
        self._transition("EVIDENCE_GATHERED", f"{len(evidence.observations)} observations, hazard {hz.status if hz else 'n/a'}")
        self._commit()

        # 3. Independent proposals: Mobility and Grid each optimise their own objective.
        m_run = self._run("mobility", "propose", "deterministic",
                          {"segment_id": geo.segment_id, "hazard_segments": len(geo.hazard_segment_ids),
                           "radius_m": geo.hazard_radius_m})
        mp = mobility.propose(snapshot, a, evidence, geo, indicators, services=services)
        self._finish(m_run, mp.as_dict())
        m_prop = self._proposal(m_run, "mobility", mp.summary, mp.as_dict())
        # Department context every validation of this incident's plans checks against (R9).
        self.departments, self.route_legs = services.as_list(), mp.route_legs
        audit.record(self.db, "mobility.proposal", f"Mobility proposed: {mp.summary}", incident_id=inc.id,
                     actor="agent:mobility", details={"actions": [x.action_type for x in mp.actions], "failed": mp.failed})

        g_run = self._run("grid", "propose", "deterministic", {"infrastructure_hazards": [h.target_id for h in evidence.infrastructure_hazards]})
        gp = grid.propose(snapshot, evidence)
        self._finish(g_run, gp.as_dict())
        g_prop = self._proposal(g_run, "grid", gp.summary, gp.as_dict())
        audit.record(self.db, "grid.proposal", f"Grid/Utility proposed: {gp.summary}", incident_id=inc.id,
                     actor="agent:grid", details={"actions": [x.action_type for x in gp.actions],
                                                   "dependency_paths": gp.dependency_paths})

        actions = resequence([make_action(i, p) for i, p in enumerate(mp.actions + gp.actions, start=1)])
        if not actions:
            actions = resequence([make_action(1, mobility.ProposedAction(
                "dispatch_field_verification", geo.segment_id or "",
                {"question": "Assess the reported situation on site; no automated action applies."},
                "No closure, route or utility action applies to this report category."), "sentinel")]) if geo.segment_id else []
        # AnalyseTraffic may change the draft (reroute around caution, diversion notice) before validation.
        actions, adjustments = self._traffic_adjust(actions, snapshot, evidence, reported_category)
        plan = create_plan(self.db, inc.id, 1, actions, state_version=snapshot.state_version,
                           summary=self._summarize(actions, snapshot),
                           rationale=("Initial plan assembled from independent Mobility and Grid/Utility proposals, "
                                      "adjusted by AnalyseTraffic. Live traffic unavailable: travel times are free-flow "
                                      "estimates. Traffic-management guidance is simulated and does not change real "
                                      "roads or signals."),
                           proposal_ids={"mobility": m_prop.id, "grid": g_prop.id})
        audit.record(self.db, "plan.created", f"Plan v1 assembled with {len(actions)} actions.", incident_id=inc.id,
                     plan_id=plan.id, actor="agent:coordinator", details={"traffic_adjustments": adjustments})
        self._transition("PLANS_PROPOSED", f"Plan v1 with {len(actions)} actions")
        self._commit()

        ctx = self._traffic_analysis(plan, actions, snapshot, evidence, adjustments, 0)
        missing = [p for p in mp.failed if p in BASE_ROUTES]
        if missing:
            vrun = store_validation(self.db, plan, validate_plan(actions, snapshot, evidence, traffic=ctx,
                                                                 plan_version=plan.version, departments=self.departments,
                                                                 route_legs=self.route_legs),
                                    snapshot.state_version, "initial")
            self._escalate(plan, vrun, "NO_SAFE_PLAN",
                           "Mobility found no feasible route for a required responder on the road graph: "
                           + " ".join(mp.reasoning[-2:]),
                           [{"strategy": "mobility_route", "agent": "mobility", "viable": False,
                             "detail": "; ".join(mp.reasoning)}])
            return
        if not geo.in_coverage or geo.segment_id is None:
            vrun = store_validation(self.db, plan, validate_plan(actions, snapshot, evidence, traffic=ctx,
                                                                 plan_version=plan.version, departments=self.departments,
                                                                 route_legs=self.route_legs),
                                    snapshot.state_version, "initial")
            self._escalate(plan, vrun, "ESCALATED", "The report is outside the mapped coverage area or not near a road; "
                                                    "it needs manual handling.", [])
            return

        self._validate_loop(plan, actions, snapshot, a, evidence, geo, indicators, services, reported_category, ctx)

    # ---- traffic -----------------------------------------------------------------------------
    def _traffic_adjust(self, actions: list[ActionSpec], snapshot: CitySnapshot, evidence, category: str
                        ) -> tuple[list[ActionSpec], list[dict[str, Any]]]:
        try:
            adjusted, changes = traffic.adjust_plan(snapshot, actions, evidence, category)
        except Exception as exc:  # noqa: BLE001 — a traffic failure must not corrupt or block the plan
            log.exception("traffic adjustment failed for incident %s", self.incident.id)
            audit.record(self.db, "traffic.failed", f"AnalyseTraffic could not adjust the plan ({type(exc).__name__}); "
                         "the draft is unchanged.", incident_id=self.incident.id, actor="agent:traffic", severity="warning")
            return actions, []
        return resequence(adjusted), changes

    def _traffic_analysis(self, plan: ResponsePlan, actions: list[ActionSpec], snapshot: CitySnapshot, evidence,
                          adjustments: list[dict[str, Any]], revision: int) -> dict[str, Any]:
        """Analyse this exact plan version; the result is stamped with the plan and state version it describes."""
        run = self._run("traffic", "analyse", "deterministic",
                        {"plan_version": plan.version, "state_version": snapshot.state_version,
                         "routes": [a.params["purpose"] for a in actions if a.action_type == "designate_response_route"],
                         "live_traffic_configured": False}, revision)
        try:
            tp = traffic.analyse_plan(snapshot, actions, evidence, plan_version=plan.version, adjustments=adjustments)
        except Exception as exc:  # noqa: BLE001
            log.exception("traffic analysis failed for incident %s", self.incident.id)
            run.status, run.error, run.finished_at = "failed", type(exc).__name__, utcnow()
            audit.record(self.db, "traffic.failed", f"AnalyseTraffic failed on plan v{plan.version} ({type(exc).__name__}); "
                         "validation continues without it.", incident_id=self.incident.id, plan_id=plan.id,
                         actor="agent:traffic", severity="warning")
            return traffic.failed_context(type(exc).__name__, plan.version, snapshot.state_version)
        out = tp.as_dict()
        self._finish(run, out)
        self._proposal(run, "traffic", tp.summary, out, revision)
        audit.record(self.db, "traffic.analysis", f"AnalyseTraffic (plan v{plan.version}): {tp.summary}",
                     incident_id=self.incident.id, plan_id=plan.id, actor="agent:traffic",
                     severity="warning" if tp.conflicts else "info",
                     details={"plan_version": plan.version, "state_version": snapshot.state_version,
                              "conflicts": tp.conflicts, "adjustments": tp.adjustments,
                              "recommendations": tp.traffic_management_recommendations})
        self._commit()  # visible to the operator while validation runs
        return out

    def _validate_loop(self, plan: ResponsePlan, actions: list[ActionSpec], snapshot: CitySnapshot,
                       assessment, evidence, geo, indicators, services: ServicePlan, category: str,
                       traffic_context: dict[str, Any]) -> None:
        inc = self.incident
        max_rev = self.settings.max_revisions
        revision = 0
        while True:
            self._transition("VALIDATING" if revision == 0 else "VALIDATING_AGAIN", f"Validating plan v{plan.version}")
            result = validate_plan(actions, snapshot, evidence, traffic=traffic_context, plan_version=plan.version,
                                   departments=self.departments, route_legs=self.route_legs)
            vrun = store_validation(self.db, plan, result, snapshot.state_version, "initial" if revision == 0 else "revision")
            for f in result.blocking():
                audit.record(self.db, "validation.finding", f"[{f.rule_id}] {f.title}", incident_id=inc.id,
                             plan_id=plan.id, actor="validator", severity="error", details=f.as_dict())
            audit.record(self.db, "validation.completed",
                         f"Plan v{plan.version} {'passed' if result.passed else 'failed'} validation "
                         f"({result.count('blocking')} blocking, {result.count('warning')} warnings).",
                         incident_id=inc.id, plan_id=plan.id, actor="validator",
                         severity="success" if result.passed else "warning")

            if result.passed:
                plan.status = "AWAITING_APPROVAL"
                self._transition("AWAITING_APPROVAL", f"Plan v{plan.version} passed validation; operator approval required.")
                audit.record(self.db, "approval.requested", f"Operator approval requested for plan v{plan.version}.",
                             incident_id=inc.id, plan_id=plan.id, actor="agent:coordinator")
                self._finish_run()
                return

            plan.status = "REVISION_REQUIRED"
            if revision >= max_rev:
                self._escalate(plan, vrun, "ESCALATED",
                               f"Conflicts remain after {revision} revision(s) (limit {max_rev}).", [])
                return
            self._transition("REVISION_REQUIRED", f"{result.count('blocking')} blocking finding(s) on plan v{plan.version}")
            self._commit()

            revision += 1
            outcome = revise(actions, result, snapshot, assessment, evidence, geo, indicators, services=services,
                             category=category)
            for c in outcome.contributions:
                r = self._run(c.agent, c.step, "deterministic", c.input, revision)
                self._finish(r, c.output)
                self._proposal(r, c.agent, c.summary, c.output, revision)
                if c.agent == "mobility" and "route_legs" in c.output:
                    self.route_legs = c.output["route_legs"]  # the revised routes' legs, not v1's
            if isinstance(outcome, RevisionFailure):
                self._escalate(plan, vrun, "NO_SAFE_PLAN", outcome.reason, outcome.alternatives)
                return

            # The revised routes are new, so traffic adjusts and analyses them again; nothing is reused.
            new_actions, adjustments = self._traffic_adjust(outcome.actions, snapshot, evidence, category)
            new_plan = create_plan(self.db, inc.id, plan.version + 1, new_actions,
                                   state_version=snapshot.state_version,
                                   summary=self._summarize(new_actions, snapshot), rationale=outcome.rationale,
                                   parent_plan_id=plan.id)
            diff = diff_actions(actions, new_actions)
            self.db.add(PlanRevision(incident_id=inc.id, from_plan_id=plan.id, to_plan_id=new_plan.id,
                                     revision_number=revision, strategy=outcome.strategy,
                                     triggered_by=outcome.triggered_by, alternatives=outcome.alternatives,
                                     diff=diff, rationale=outcome.rationale))
            plan.status = "SUPERSEDED"
            audit.record(self.db, "plan.revised", f"Plan v{plan.version} → v{new_plan.version}: {outcome.rationale}",
                         incident_id=inc.id, plan_id=new_plan.id, actor="agent:coordinator",
                         details={"strategy": outcome.strategy, "alternatives": outcome.alternatives,
                                  "traffic_adjustments": adjustments})
            self._commit()
            plan, actions = new_plan, new_actions
            traffic_context = self._traffic_analysis(plan, actions, snapshot, evidence, adjustments, revision)

    def _escalate(self, plan: ResponsePlan, vrun, status: str, reason: str, alternatives: list[dict]) -> None:
        append_finding(self.db, vrun, Finding(
            "R7_UNRESOLVED_CONFLICT", "blocking", "Conflict could not be resolved", reason,
            "A human coordinator must decide. No simulated action will be executed.",
            evidence={"alternatives": alternatives}))
        plan.status = "ESCALATED"
        audit.record(self.db, "workflow.escalated", reason, incident_id=self.incident.id, plan_id=plan.id,
                     actor="agent:coordinator", severity="error", details={"alternatives": alternatives})
        self._transition(status, reason)
        self._finish_run()

    def _finish_run(self) -> None:
        self.incident.run_finished_at = utcnow()
        self.db.commit()

    @staticmethod
    def _summarize(actions: list[ActionSpec], snapshot: CitySnapshot) -> str:
        parts = []
        for a in actions:
            if a.action_type == "designate_response_route":
                parts.append(f"{a.params['name']} ({a.params['length_m'] / 1000:.2f} km, ~{a.params['travel_s'] / 60:.0f} min)")
            elif a.action_type in ("close_roads", "set_roads_caution"):
                verb = "close" if a.action_type == "close_roads" else "caution on"
                parts.append(f"{verb} {len(a.params['segment_ids'])} road segment(s) around {snapshot.road(a.target_id).name}")
            elif a.target_id in snapshot.nodes:
                parts.append(f"{a.action_type.replace('_', ' ')} {snapshot.nodes[a.target_id].name}")
            elif snapshot.road(a.target_id):
                parts.append(f"{a.action_type.replace('_', ' ')} at {snapshot.road(a.target_id).name}")
            else:
                parts.append(f"{a.action_type.replace('_', ' ')} {a.target_id}")
        text = "; ".join(parts) or "No actions"
        return text[0].upper() + text[1:]


def run_workflow(incident_id: uuid.UUID) -> None:
    """Entry point for the background task. Opens its own session; never leaves an incident mid-flight."""
    db = session_factory()()
    try:
        inc = db.get(Incident, incident_id)
        if inc is None:
            return
        Coordinator(db, inc).run()
    except Exception as exc:  # noqa: BLE001 — record any failure, never leave a false success
        log.exception("workflow failed for incident %s", incident_id)
        db.rollback()
        inc = db.get(Incident, incident_id)
        if inc is not None:
            audit.transition(db, inc, "FAILED", f"Workflow error: {type(exc).__name__}")
            inc.run_finished_at = utcnow()
            audit.record(db, "workflow.failed", f"Workflow failed ({type(exc).__name__}); no actions were taken.",
                         incident_id=incident_id, actor="agent:coordinator", severity="error")
            db.commit()
    finally:
        db.close()
