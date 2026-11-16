"""Verify: did the remediation actually work?

A fix is never assumed to have worked. Verification compares the *original*
trace with a follow-up trace the operator submits, and returns one of five
statuses:

``VERIFIED_RESOLVED``
    The follow-up completed, the original root-cause step is no longer failing,
    and no new failure appeared.
``PARTIALLY_RESOLVED``
    Progress on the original cause (the step runs, or its error changed), but
    something else still fails.
``STILL_FAILING``
    The same failure, at the same step or with the same error signature.
``NEW_FAILURE``
    The original cause is gone but a different failure appeared.
``INCONCLUSIVE``
    Not enough to judge: the trace does not cover the step, the follow-up did
    not run, or the two traces are not comparable.

Every result also says how it was verified. A **simulated** verification is a
follow-up trace supplied by hand (the normal case: this app does not run the
agent under test). **Real** verification would need the agent's own runtime to
report back, which this app never pretends to have.
"""

from __future__ import annotations

import re
from typing import Any

VERIFIED_RESOLVED = "VERIFIED_RESOLVED"
PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
STILL_FAILING = "STILL_FAILING"
NEW_FAILURE = "NEW_FAILURE"
INCONCLUSIVE = "INCONCLUSIVE"

VERIFICATION_STATUSES = (
    VERIFIED_RESOLVED,
    PARTIALLY_RESOLVED,
    STILL_FAILING,
    NEW_FAILURE,
    INCONCLUSIVE,
)

SIMULATED = "simulated"
REAL = "real"

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_WS_RE = re.compile(r"\s+")


def error_signature(text: str | None) -> str:
    """A stable signature for an error message.

    Numbers, quoted values and whitespace are normalised away, so
    "read timeout=30" and "read timeout=60" are the same failure while two
    genuinely different errors stay different.
    """
    if not text:
        return ""
    lowered = _WS_RE.sub(" ", str(text)).strip().lower()
    lowered = _NUMBER_RE.sub("<n>", lowered)
    return lowered[:240]


def _failed_step(observation: dict[str, Any]) -> dict[str, Any] | None:
    for step in observation["failed_steps"]:
        if step.get("error"):
            return step
    return observation["failed_steps"][0] if observation["failed_steps"] else None


def _signature_of(observation: dict[str, Any]) -> str:
    step = _failed_step(observation)
    if not step:
        return ""
    return error_signature(step.get("error") or step.get("name"))


def verify(
    original: dict[str, Any],
    followup: dict[str, Any],
    plan: dict[str, Any],
    *,
    mode: str = SIMULATED,
) -> dict[str, Any]:
    """Compare two observations and grade the remediation.

    ``original`` and ``followup`` are :func:`app.agent.observe.observe` output.
    """
    criteria = plan.get("verification_criteria") or "The failing step no longer fails."
    original_failed = _failed_step(original)
    followup_failed = _failed_step(followup)
    followup_status = followup["signals"]["status"]
    original_status = original["signals"]["status"]

    checks: list[dict[str, Any]] = []
    reasons: list[str] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    # ---- did the follow-up actually run?
    if not followup["execution"]["step_count"]:
        return _result(
            INCONCLUSIVE,
            mode,
            checks,
            ["The follow-up trace has no steps, so there is nothing to compare."],
            criteria,
            original,
            followup,
        )
    if followup_status == "unknown":
        return _result(
            INCONCLUSIVE,
            mode,
            checks,
            ["The follow-up trace's outcome is unknown, so it cannot confirm or deny a fix."],
            criteria,
            original,
            followup,
        )

    original_sig = _signature_of(original)
    followup_sig = _signature_of(followup)

    # ---- the original cause
    if original_failed is None:
        # Nothing failed to begin with: a follow-up can only confirm that.
        check("original_cause_present", False, "The original run recorded no failing step.")
        return _result(
            INCONCLUSIVE,
            mode,
            checks,
            ["The original run had no failing step, so there is no cause to verify against."],
            criteria,
            original,
            followup,
        )

    original_step = original_failed.get("number") or original_failed.get("index")
    same_step_failed = bool(followup_failed) and (
        (followup_failed.get("tool_name") or "") == (original_failed.get("tool_name") or "")
        and (followup_failed.get("number") or followup_failed.get("index")) == original_step
    )
    same_error = bool(followup_sig) and followup_sig == original_sig

    check(
        "same_failure_absent",
        not (same_step_failed and same_error),
        (
            f"The same step ({original_failed.get('tool_name') or 'step ' + str(original_step)}) fails with the same error."
            if same_step_failed and same_error
            else "The original step does not fail with the same error again."
        ),
    )
    check(
        "followup_completed",
        followup_status in {"success", "recovered"},
        f"The follow-up run finished as {followup_status}.",
    )
    check(
        "no_new_failure",
        followup_failed is None,
        (
            f"A new failure appeared: {followup_failed.get('error') or followup_failed.get('name')}."
            if followup_failed
            else "The follow-up recorded no failing step."
        ),
    )

    if same_step_failed and same_error:
        reasons.append(
            f"The same failure recurs at step {original_step} "
            f"({(original_failed.get('error') or '')[:160]})."
        )
        return _result(STILL_FAILING, mode, checks, reasons, criteria, original, followup)

    if followup_failed is None and followup_status in {"success", "recovered"}:
        reasons.append(
            f"The follow-up finished as {followup_status} and the original failure "
            f"at step {original_step} did not recur."
        )
        if original_status in {"success", "recovered"}:
            reasons.append("The original run had not failed, so this confirms the earlier outcome.")
        return _result(VERIFIED_RESOLVED, mode, checks, reasons, criteria, original, followup)

    if followup_failed is not None and followup_sig != original_sig:
        # A different failure, after the original step: progress, then a wall.
        if same_step_failed:
            reasons.append(
                f"Step {original_step} still fails, but with a different error "
                f"({(followup_failed.get('error') or '')[:160]}): the original cause changed, it did not disappear."
            )
        else:
            reasons.append(
                f"The original failure at step {original_step} is gone, but a new failure appeared at "
                f"step {followup_failed.get('number') or followup_failed.get('index')} "
                f"({(followup_failed.get('error') or '')[:160]})."
            )
        status = PARTIALLY_RESOLVED if same_step_failed else NEW_FAILURE
        return _result(status, mode, checks, reasons, criteria, original, followup)

    # The step is gone from the run entirely and the run did not complete.
    reasons.append(
        f"The follow-up did not reach step {original_step}, and finished as {followup_status}: "
        "the trace cannot show whether the cause is fixed."
    )
    return _result(INCONCLUSIVE, mode, checks, reasons, criteria, original, followup)


def _result(
    status: str,
    mode: str,
    checks: list[dict[str, Any]],
    reasons: list[str],
    criteria: str,
    original: dict[str, Any],
    followup: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "mode": mode,
        "resolved": status == VERIFIED_RESOLVED,
        "checks": checks,
        "reasons": reasons,
        "criteria": criteria,
        "evidence": {
            "original": _evidence(original),
            "followup": _evidence(followup),
        },
        "note": (
            "Verified against a follow-up trace you submitted. This app does not run the agent under test, "
            "so the verification is only as good as that trace."
            if mode == SIMULATED
            else "Verified by the agent's own runtime reporting back."
        ),
    }


def _evidence(observation: dict[str, Any]) -> dict[str, Any]:
    step = _failed_step(observation)
    return {
        "execution_id": observation["execution"]["execution_id"],
        "status": observation["signals"]["status"],
        "step_count": observation["execution"]["step_count"],
        "failed_step": None
        if step is None
        else {
            "number": step.get("number"),
            "tool_name": step.get("tool_name"),
            "error": step.get("error"),
        },
    }
