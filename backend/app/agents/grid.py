"""Grid / Utility — actions on the SIMULATED feeder-zone model and dependency analysis.

Reasons only over the explicit dependency graph in the snapshot overlay (feeder zone → signal →
real OSM junction). It proposes isolating a feeder zone only when the evidence for that zone is
sensor-corroborated or operator-confirmed; it never invents a hazard or a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.mobility import ProposedAction
from app.schemas.agents import SUPPORTING_STATUSES, EvidenceAssessment
from app.simulation.state import CitySnapshot


@dataclass
class GridProposal:
    actions: list[ProposedAction]
    consequences: list[dict[str, Any]]
    dependency_paths: list[list[str]]
    reasoning: list[str]
    uncertainty: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.actions:
            return "No utility action required (simulated infrastructure model)."
        return "; ".join(f"{a.action_type} {a.target_id}" for a in self.actions) + (
            f". Affects {', '.join(c['id'] for c in self.consequences)}" if self.consequences else "")

    def as_dict(self) -> dict[str, Any]:
        return {"actions": [a.__dict__ for a in self.actions], "consequences": self.consequences,
                "dependency_paths": self.dependency_paths, "reasoning": self.reasoning,
                "uncertainty": self.uncertainty, "simulated_model": True}


def propose(snapshot: CitySnapshot, evidence: EvidenceAssessment) -> GridProposal:
    actions: list[ProposedAction] = []
    consequences: list[dict[str, Any]] = []
    paths: list[list[str]] = []
    reasoning: list[str] = []
    uncertainty: list[str] = []

    for hazard in evidence.infrastructure_hazards:
        node = snapshot.nodes.get(hazard.target_id)
        if node is None or node.kind != "substation":
            continue
        if hazard.status not in SUPPORTING_STATUSES:
            reasoning.append(f"{node.name}: {hazard.hazard.replace('_', ' ')} evidence is {hazard.status}; no isolation proposed.")
            if hazard.status in ("CONTRADICTORY", "STALE"):
                uncertainty.append(hazard.explanation)
            continue
        if node.status != "energized":
            reasoning.append(f"{node.name} is already {node.status}.")
            continue
        actions.append(ProposedAction(
            "isolate_substation", node.id, {"reason": f"{hazard.status.lower()} {hazard.hazard.replace('_', ' ')}"},
            f"{hazard.hazard.replace('_', ' ').capitalize()} in {node.name} ({hazard.explanation}). Isolate the "
            "simulated feeder to remove the electrical hazard.",
        ))
        lost = snapshot.dependents_of(node.id)
        for cid in lost:
            c = snapshot.nodes[cid]
            consequences.append({"id": cid, "name": c.name, "kind": c.kind, "effect": "goes dark"})
            for e in snapshot.edges_of_kind("controls"):
                if e.source == cid:
                    paths.append([node.id, cid, e.target])
        reasoning.append(f"Isolating {node.id} de-energizes {', '.join(lost) or 'nothing'} in the simulated model "
                         "(no other active feed).")

    if not actions and not reasoning:
        reasoning.append("No feeder-zone hazard in the evidence; no utility action required.")
    return GridProposal(actions, consequences, paths, reasoning, uncertainty)


def backup_power_options(
    snapshot: CitySnapshot, signals: list[str], isolating: set[str]
) -> tuple[list[ProposedAction], list[str]]:
    """For each signal, find a standby feed from an energized zone the plan is not isolating."""
    actions, problems = [], []
    for sig in signals:
        feeds = snapshot.feeds_for(sig)
        primary = next((f for f in feeds if f.status == "active"), None)
        backup = next((f for f in feeds if f.status == "standby" and f.source not in isolating
                       and snapshot.nodes[f.source].status == "energized"), None)
        if primary is None or backup is None:
            standby = [f for f in feeds if f.status == "standby"]
            if not standby:
                problems.append(f"{sig} has no backup feed.")
            else:
                problems.append(f"{sig} backup feed {standby[0].id} comes from {standby[0].source}, which is "
                                f"{snapshot.nodes[standby[0].source].status}.")
            continue
        actions.append(ProposedAction(
            "transfer_signal_power", sig, {"from_feed": primary.id, "to_feed": backup.id},
            f"Move {sig} from {primary.source} to backup feed {backup.id} ({backup.source}) before isolation (simulated).",
        ))
    return actions, problems
