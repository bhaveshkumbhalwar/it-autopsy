"""Verification and escalation.

The agent never claims an issue is resolved just because a tool returned
successfully. Tool success only proves "an observation was collected". The
possible outcomes are exactly the set in :data:`src.config.OUTCOMES`:

* ``needs_user_confirmation`` - diagnostics and citations are available; only the
  user can confirm whether their problem is gone.
* ``verified_resolved`` / ``not_resolved`` - only set after an explicit user
  confirmation (see :meth:`Verifier.confirm`).
* ``escalated`` - weak evidence, failed observations, unsupported environment,
  security concern, or exhausted budget.
* ``unable_to_verify`` - mixed observations where neither success nor failure can
  be established.
"""

from __future__ import annotations

from src import config


class Verifier:
    def verify(self, evidence: list, results: list, run: dict | None = None) -> dict:
        evidence_ids = [e.get("doc_id") for e in evidence if e.get("doc_id")]
        run = run or {}

        if run.get("escalated"):
            return self._result(
                config.OUTCOME_ESCALATED,
                f"Agent stopped early ({run.get('stopped_reason', 'unspecified')}); "
                "a human should review the collected observations.",
                evidence_ids,
                "Can an administrator reproduce this with the details above?",
            )

        if not evidence:
            return self._result(
                config.OUTCOME_ESCALATED,
                "No sufficiently relevant official source was found; the agent does "
                "not guess unsupported fixes.",
                evidence_ids,
                "Can you share the exact error text, screenshots, or when it started?",
            )

        if not results:
            return self._result(
                config.OUTCOME_UNABLE,
                "Evidence was found but no safe diagnostic could be run in this "
                "environment, so the result cannot be verified.",
                evidence_ids,
                "Which operating system and network are you on?",
            )

        ok = [r for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]

        if failed and not ok:
            return self._result(
                config.OUTCOME_ESCALATED,
                "Every diagnostic failed; escalate with the errors and citations.",
                evidence_ids,
                "Can a technician run these checks locally with admin access?",
            )

        if failed and ok:
            return self._result(
                config.OUTCOME_UNABLE,
                "Some observations succeeded and some failed, so the state is "
                "ambiguous and cannot be verified automatically.",
                evidence_ids,
                "Which of the suggested checks did you complete, and what did you see?",
            )

        return self._result(
            config.OUTCOME_NEEDS_CONFIRMATION,
            "Read-only observations completed against cited sources, but tool "
            "success is not proof of resolution.",
            evidence_ids,
            "Is the problem resolved now? Reply yes or no; if no, what changed?",
        )

    def confirm(self, verification: dict, resolved: bool) -> dict:
        """Apply an explicit user confirmation to a pending verification."""
        updated = dict(verification)
        if verification.get("outcome") != config.OUTCOME_NEEDS_CONFIRMATION:
            return updated
        updated["outcome"] = (
            config.OUTCOME_VERIFIED if resolved else config.OUTCOME_NOT_RESOLVED
        )
        updated["reason"] = (
            "User confirmed the issue is resolved."
            if resolved
            else "User reported the issue persists after the suggested steps."
        )
        updated["follow_up_question"] = (
            "" if resolved else "Escalate to a human with the observations above."
        )
        return updated

    @staticmethod
    def _result(outcome: str, reason: str, evidence_ids: list[str], question: str) -> dict:
        return {
            "outcome": outcome,
            "reason": reason,
            "evidence_ids": evidence_ids,
            "follow_up_question": question,
        }


class Escalator:
    def build(self, run: dict, verification: dict) -> dict:
        results = run.get("results", [])
        return {
            "summary": "IT help desk escalation handoff (evidence-based).",
            "category": run.get("triage", {}).get("category", "unknown"),
            "priority": run.get("triage", {}).get("priority", "unknown"),
            "symptoms": run.get("issue", ""),
            "sources": [e.get("citation", "") for e in run.get("evidence", [])],
            "actions_attempted": [r.get("tool_name", "") for r in results],
            "results": [
                (r.get("summary") or r.get("error") or "no summary") for r in results
            ],
            "not_attempted_reason": run.get("stopped_reason", ""),
            "next_steps": [
                "Human review required.",
                verification.get("follow_up_question", ""),
            ],
            "to": "human support",
        }
