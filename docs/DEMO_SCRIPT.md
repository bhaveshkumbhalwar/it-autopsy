# Demo script

A 5–7 minute walkthrough. Every command below was run during final acceptance;
expected output is what the project actually produced.

## 0. Setup (one minute, once)

```bash
cd /Users/bhavesh/fai_mini_project
.venv/bin/python --version        # Python 3.14.6
.venv/bin/python -m pytest -q     # 78 passed
```

## 1. Show the agent loop without the UI

```bash
.venv/bin/python - <<'PY'
from src import agent, verification

run = agent.Agent(os_name="macos").run(
    "My Mac connects to Wi-Fi but has no internet access"
)
for t in run["trace"]:
    print(t["step"], t["decision"], t["tool_name"] or "")
print("outcome evidence:", len(run["evidence"]), "stopped:", run["stopped_reason"])
print(verification.Verifier().verify(run["evidence"], run["results"], run=run)["outcome"])
PY
```

Expected: four observations (Wi-Fi → DNS → ping → VPN), `observations_complete`,
and `needs_user_confirmation`. Point out each next step was chosen from the
previous result.

## 2. Show a failure changing the decision

```bash
.venv/bin/python - <<'PY'
from src import agent, tools

class Lossy(tools.ToolRegistry):
    def _sim(self, name, params):
        d = super()._sim(name, params)
        if name == "sim_ping_host":
            d.update(received=0, sent=4)
        return d

run = agent.Agent(os_name="macos", registry=Lossy(os_name="macos")).run(
    "My Mac connects to Wi-Fi but has no internet access"
)
print([r["tool_name"] for r in run["results"]])
print(run["stopped_reason"], run["escalated"])
PY
```

Expected: the chain stops at `sim_ping_host`; `sim_check_vpn` does **not** run;
`reachability_failed True`.

## 3. Show safety boundaries

```bash
.venv/bin/python - <<'PY'
from src import policy, tools
from src.policy import PolicyViolation

for name, params in [
    ("rm_rf_root", {}),
    ("sim_get_wifi_status", {"cmd": "rm -rf /"}),
    ("sim_ping_host", {"host": "a.com; whoami"}),
    ("macos_wifi_status", {}),
]:
    try:
        tools.ToolRegistry("macos").call(name, params)
        print("ALLOWED", name)
    except PolicyViolation as exc:
        print("BLOCKED", name, "->", exc)
PY
```

Expected: all four blocked. Then show the approval gate:

```bash
.venv/bin/python - <<'PY'
from src import tools
from src.policy import PolicyViolation

try:
    tools.ToolRegistry("macos").call("sim_renew_dhcp_lease", {})
except PolicyViolation as e:
    print("without approval:", e)
res = tools.ToolRegistry("macos").call("sim_renew_dhcp_lease", {}, approved=True)
print("with approval:", res["data"])
PY
```

Expected: blocked without approval; with approval the simulated result says
`{'changed': False}`.

## 4. Show escalation when evidence is missing

```bash
.venv/bin/python - <<'PY'
from src import agent
for text in [
    "My office printer is jammed and needs toner",
    "My laptop has ransomware and files are encrypted",
]:
    run = agent.Agent(os_name="unknown").run(text)
    print(text, "->", run["stopped_reason"], run["escalated"], len(run["results"]))
PY
```

Expected: `insufficient_evidence` and `security_concern`; zero tools run; both
escalated. This is the anti-hallucination behaviour.

## 5. Launch the UI

```bash
.venv/bin/python -m streamlit run app.py
```

Open <http://localhost:8501>. In **New case**:

1. Enter *"My Mac connects to Wi-Fi but has no internet access"*, OS `macos`,
   run. Show category/priority, cited Apple evidence, decision trace, simulated
   observations, and the confirmation buttons.
2. Enter *"My office printer is jammed"*, run. Show the escalation handoff with
   symptoms, sources attempted, and `not_attempted_reason`.
3. Open **Cases** to show the persisted, redacted event log.
4. Open **Evaluation** to show the real metrics (hybrid accuracy 0.8356,
   macro-F1 0.8017; base model accuracy 0.7473, macro-F1 0.6449; EOL-rule
   caveat called out).
5. Open **Safety** to show the closed allowlist and approval flags.

## 6. Regenerate all metrics

```bash
.venv/bin/python -m src.evaluation
cat reports/evaluation.md
```

Expected summary line:

```text
classification acc=0.8356, retrieval hit@1=1.0000,
agent 7/7, security 16/16, robustness 12/12
```

## Talking points

- This is an agent, not a chatbot: observations choose the next step, and a
  failed observation stops the chain.
- The default path is deterministic and needs no API key.
- Simulated tools are always labelled; real diagnostics are macOS-only,
  read-only, opt-in, and approval-gated.
- The agent would rather escalate than invent a fix; retrieval has a hard
  threshold and OS filter.
- Honest metrics: the base model beats the majority baseline but never predicts
  EOL; a deterministic phrase rule raises the hybrid score, and its test result
  is explicitly labelled optimistic. The retrieval evaluation set is tiny.
- No resolution success is claimed without a human confirmation.
