"""Diffing two or three executions, step by step (Phase 4).

The compare view answers "why did these runs differ", so everything here is
rule-based and derived from the traces alone: no LLM is required, and the answer
is the same every time the same runs are compared.

The steps are aligned by step number, which is what the trace itself numbers
them by, so an insertion early in a run shows up as one divergence followed by a
shifted column rather than a wall of mismatches. A run with fewer steps is
padded with "no step" entries instead of being cropped.
"""

from __future__ import annotations

from typing import Any

from app.api.timeline import _clean, _step_kind

MAX_RUNS = 3
NO_STEP = "no step"

# Fields compared per aligned row, in the order the table shows them.
COMPARED_FIELDS = ("kind", "tool", "status", "duration_ms", "tokens", "args")


def _tool(step: dict[str, Any] | None) -> str | None:
    if not step:
        return None
    return (step.get("tool_name") or "").strip() or None


def _tokens(step: dict[str, Any] | None) -> int | None:
    if not step:
        return None
    for field in ("tokens", "total_tokens", "usage"):
        value = step.get(field)
        if isinstance(value, int):
            return value
        if isinstance(value, dict):
            total = value.get("total_tokens") or (value.get("input_tokens", 0) or 0) + (value.get("output_tokens", 0) or 0)
            if isinstance(total, int) and total:
                return total
    metadata = step.get("metadata")
    if isinstance(metadata, dict):
        return _tokens({"tokens": metadata.get("tokens") or metadata.get("total_tokens")})
    return None


MISSING = "-"


def _args(step: dict[str, Any] | None) -> str | None:
    """The step's arguments (or its input) as one redacted string."""
    if not step:
        return None
    args = step.get("tool_arguments")
    if args in (None, "", [], {}):
        # A task or LLM step has no tool arguments; its input is what it was
        # asked to do, which is exactly what differs between runs.
        args = step.get("input")
    if args in (None, "", [], {}):
        return None
    return _clean(args, 400) or None


def _row(runs: list[dict[str, Any]], number: int) -> dict[str, Any]:
    """One aligned step row across every run."""
    cells = []
    for run in runs:
        step = run["by_number"].get(number)
        if step is None:
            cells.append(
                {
                    "present": False,
                    "kind": None,
                    "tool": None,
                    "status": None,
                    "duration_ms": None,
                    "tokens": None,
                    "args": MISSING,
                    "error": None,
                    "summary": None,
                    "label": NO_STEP,
                }
            )
            continue
        kind = _step_kind(str(step.get("type")))
        cells.append(
            {
                "present": True,
                "kind": kind,
                "tool": _tool(step),
                "status": str(step.get("status") or "unknown"),
                "duration_ms": step.get("duration_ms") if isinstance(step.get("duration_ms"), (int, float)) else None,
                "tokens": _tokens(step),
                # Shown as "-" when the trace recorded no input; a field
                # nobody recorded is not a difference between the runs.
                "args": _args(step) or MISSING,
                "error": _clean(step.get("error"), 300),
                "summary": _clean(step.get("error") or step.get("tool_result") or step.get("output"), 160),
                "label": _tool(step) or kind,
            }
        )

    def value(cell: dict[str, Any], field: str) -> Any:
        if not cell["present"]:
            return None  # a missing step is handled by "kind" alone
        raw = cell[field]
        if field == "args" and raw in (MISSING, None):
            return MISSING  # not recorded: never a difference
        return raw

    differing = sorted(
        field for field in COMPARED_FIELDS if len({value(cell, field) for cell in cells}) > 1
    )
    return {
        "number": number,
        "cells": cells,
        "differing_fields": differing,
        "is_divergence": bool(differing),
    }


def _describe_kind(kind: str | None) -> str:
    return {
        "task": "a task step",
        "plan": "a planning step",
        "llm": "an LLM call",
        "tool": "a tool call",
        "retry": "a retry",
        "error": "an error record",
        "output": "an output step",
        None: "no step",
    }.get(kind, "a step")


def _describe_args(cells: list[dict[str, Any]]) -> str:
    """The differing inputs, quoted, so the sentence reads as a sentence."""
    parts = []
    for cell in cells:
        if not cell["present"]:
            parts.append(NO_STEP)
        elif cell["args"] in (None, MISSING):
            parts.append("not recorded")
        else:
            parts.append(f'"{str(cell["args"]).strip()}"')
    return " vs ".join(parts)


def _describe(field: str, cells: list[dict[str, Any]]) -> str:
    """One clause of the summary, in words a reader can act on."""
    values = [cell[field] if cell["present"] else None for cell in cells]
    readable = []
    for value, cell in zip(values, cells):
        if not cell["present"]:
            readable.append(NO_STEP)
        elif value is None:
            readable.append("not recorded")
        elif field == "kind":
            readable.append(_describe_kind(value))
        elif field == "duration_ms":
            readable.append(f"{int(value)} ms")
        elif field == "tokens":
            readable.append(f"{value} tokens")
        elif field == "args":
            readable.append("args -" if value == MISSING else f"args {value}")
        else:
            readable.append(str(value))
    return " vs ".join(readable)


def _format_fields(fields: list[str]) -> str:
    labels = [{"duration_ms": "latency", "kind": "step type", "tool": "tool", "status": "status", "tokens": "tokens", "args": "arguments"}.get(f, f) for f in fields]
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _task_text(step: dict[str, Any] | None) -> str | None:
    """The task a run was given, normalised for comparison."""
    if not step:
        return None
    text = step.get("input") or step.get("task_description")
    if isinstance(text, dict):
        text = text.get("task") or text.get("query") or text.get("prompt")
    cleaned = _clean(text, 400)
    return " ".join(cleaned.split()).lower() if cleaned else None


def _reason_clause(run: dict[str, Any]) -> str:
    """The cause of a failed run as a clause that follows "because".

    "The model ran out of usable context at step 2" -> "the model ran out of
    usable context at step 2". A sentence without a verb of its own (the
    reasoning template) keeps its own words but loses its capital.
    """
    cause = (run.get("root_cause") or "").split(" Evidence:")[0].strip().rstrip(".")
    category = (run.get("categories") or [None])[0]
    # A bare category id is not a sentence, and neither is a root cause that is
    # just the category echoed back: say the category in words instead.
    if not cause or cause == category:
        cause = str(category).replace("_", " ") if category else "an undiagnosed error"
    return cause[:1].lower() + cause[1:]


def _outcome(run: dict[str, Any]) -> str:
    """What happened to this run, in one sentence: the outcome, not the input."""
    name = run["name"]
    status = str(run.get("status") or "").lower()
    if status in {"failed", "error"}:
        return f"{name} failed because {_reason_clause(run)}"
    if status == "partial":
        return f"{name} returned a partial result because {_reason_clause(run)}"
    if status == "recovered":
        return f"{name} recovered and completed"
    if status == "success":
        return f"{name} completed"
    return f"{name} has no diagnosis"


def compare_basis(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether the runs are comparable, and on what.

    Different agents, or a different task, means a step-by-step diff explains
    much less: the two runs are answering different questions, so the summary
    leads with the outcome instead of the first step that happens to differ.
    """
    # The agent is compared when the run knows its own name. A run labelled by
    # its execution ("Agent A (retry)") is still the same agent, and saying so
    # would be wrong: only an actual agent_name decides this.
    named = [run for run in runs if run.get("agent_name")]
    agents = {" ".join(str(run["agent_name"]).lower().split()) for run in named}
    agents_seen = len(agents) > 1
    tasks = {_task_text((run.get("steps") or [None])[0]) for run in runs}
    # "Limited" is only ever claimed on evidence: two runs are different agents
    # or different tasks when an agent name or a task text says so, or when a
    # run's task was simply not recorded and cannot be matched.
    same_agent = not agents_seen
    same_task = len(tasks) == 1

    differences = []
    if not same_agent:
        names = [str(run.get("agent_name") or run.get("name") or "unnamed agent") for run in runs]
        differences.append("agent (" + " vs ".join(names) + ")")
    if not same_task:
        tasks_text = [
            (_task_text((run.get("steps") or [None])[0]) or "no task recorded")[:80] for run in runs
        ]
        differences.append("task (" + " vs ".join(f'"{text}"' for text in tasks_text) + ")")

    return {
        "same_agent": same_agent,
        "same_task": same_task,
        "limited": bool(differences),
        "banner": "Different agents or tasks - the diff is limited" if differences else None,
        "reason": " and ".join(differences) or None,
        "outcomes": [_outcome(run) for run in runs],
    }


def _outcome_summary(runs: list[dict[str, Any]], basis: dict[str, Any], first: dict[str, Any] | None) -> str:
    """The summary for runs that are not directly comparable: outcome first."""
    lead = "; ".join(basis["outcomes"])
    if first is None:
        return f"{lead}. No step-by-step differences found, although the runs differ."
    # The outcome leads, and the inputs that differ are named: with a different
    # task that is the difference that matters, not the step count.
    detail = _describe_args(first["cells"]) if "args" in first["differing_fields"] else None
    other = [field for field in first["differing_fields"] if field != "args"]
    if detail is not None:
        text = f"{lead}; the input at step {first['number']} differed ({detail})"
    else:
        text = f"{lead}; the first step-level difference is at step {first['number']} ({_format_fields(other)} or more)"
    return text + ", which need not be the cause when the runs are answering different questions."


def _outcome_difference(outcomes: list[str], runs: list[dict[str, Any]]) -> str | None:
    """One line naming how the runs ended differently, if they did."""
    statuses = {str(run.get("status") or "").lower() for run in runs}
    causes = {str(run.get("root_cause") or "").strip() for run in runs}
    if len(statuses) == 1 and len(causes) == 1:
        return None
    return "; ".join(outcomes)


def _summary(runs: list[dict[str, Any]], first: dict[str, Any] | None, identical: bool) -> str:
    names = [run["name"] for run in runs]
    longest = max((len(run["steps"]) for run in runs), default=0)
    if first is None:
        return f"No differences found in the {longest} aligned steps of {' vs. '.join(names)}."

    number, fields = first["number"], first["differing_fields"]
    cells = first["cells"]

    # What each run actually did at that step, for the fields that decide it.
    shapes = []
    for cell in cells:
        if not cell["present"]:
            shapes.append("no step")
        else:
            tool = cell["tool"] or _describe_kind(cell["kind"])
            shapes.append(f"{cell['status']} {tool}")
    if len({shape.replace("failed", "").replace("success", "") for shape in shapes}) == 1 and "status" in fields:
        text = f"Step {number} ({shapes[0].split(' ', 1)[-1]}) succeeded in some runs and failed in others"
    else:
        if fields == ["args"]:
            what = [cell["summary"] or cell["args"] or "nothing recorded" for cell in cells]
            text = f"Step {number} ({shapes[0].split(' ', 1)[-1]}) was given different input: " + " vs ".join(f'"{text[:90]}"' for text in what)
        else:
            text = f"Step {number} differs on {_format_fields(fields)}: " + " vs ".join(shapes)

    slower = [cell for cell in cells if cell["present"] and cell["duration_ms"] is not None]
    if "duration_ms" in fields and len(slower) > 1:
        fastest = min(slower, key=lambda cell: cell["duration_ms"])
        slowest = max(slower, key=lambda cell: cell["duration_ms"])
        if slowest["duration_ms"] > 2 * max(fastest["duration_ms"], 1):
            name = runs[[cell["duration_ms"] for cell in cells].index(slowest["duration_ms"])]["name"]
            text += f"; {name} spent {int(slowest['duration_ms'])} ms there against {int(fastest['duration_ms'])} ms"

    short = [run for run in runs if run["last_number"] is not None and first["number"] > run["last_number"]]
    if short:
        names_short = ", ".join(f"{run['name']} ({run['last_number']} step{'s' if run['last_number'] != 1 else ''})" for run in short)
        text += f". The runs are not the same length: {names_short} end{'s' if len(short) == 1 else ''} before this step"
    return text + "."


def build_comparison(
    runs: list[dict[str, Any]],
    llm_note: str | None = None,
) -> dict[str, Any]:
    """Align the steps of two or three executions and name the divergence.

    ``runs`` is a list of ``{"id", "name", "steps", ...}`` where ``steps`` are
    the raw normalised steps of that execution. The steps are redacted here.
    """
    if len(runs) < 2:
        raise ValueError("comparison needs at least two executions")
    runs = runs[:MAX_RUNS]

    prepared = []
    for run in runs:
        steps = [step for step in (run.get("steps") or []) if isinstance(step, dict)]
        by_number = {}
        for position, step in enumerate(steps, start=1):
            index = step.get("index")
            by_number[(index + 1) if isinstance(index, int) else position] = step
        prepared.append(
            {
                **run,
                "steps": steps,
                "steps_named": steps,
                "by_number": by_number,
                "last_number": max(by_number) if by_number else None,
            }
        )

    longest = max((len(run["steps"]) for run in prepared), default=0)
    rows = [_row(prepared, number) for number in range(1, longest + 1)]
    divergences = [row for row in rows if row["is_divergence"]]

    first = divergences[0] if divergences else None
    identical = first is None
    basis = compare_basis(prepared)
    summary = (
        _outcome_summary(prepared, basis, first)
        if basis["limited"]
        else _summary(prepared, first, identical)
    )

    return {
        "runs": [
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "execution_id": run.get("execution_id"),
                "status": run.get("status"),
                "step_count": len(run["steps"]),
                "duration_ms": run.get("duration_ms"),
                "tool_calls": run.get("tool_call_count"),
                "categories": run.get("categories") or [],
                "root_cause": (run.get("root_cause") or None),
                "agent_name": run.get("agent_name"),
                "outcome": _outcome(run),
            }
            for run in prepared
        ],
        "rows": rows,
        "first_divergence": first,
        "identical": identical,
        "divergence_count": len(divergences),
        "total_steps": longest,
        "summary": summary,
        "summary_source": "rules",
        # False when the runs answer different questions: the page shows the
        # banner and leads with each run's outcome instead of first-step input.
        "comparable": not basis["limited"],
        "comparison_banner": basis["banner"],
        # A headline difference the page can lead with before any step text.
        "outcome_difference": _outcome_difference(basis["outcomes"], runs),
        "comparison_caveat": basis["reason"],
        "outcomes": basis["outcomes"],
        "same_agent": basis["same_agent"],
        "same_task": basis["same_task"],
        "llm_note": llm_note,
    }
