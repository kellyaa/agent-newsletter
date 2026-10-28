# Policies

Policy-as-code for this repository (ACMM `acmm:policy-as-code`). `agent-policy.yaml` states, in a form tools can read, the rules that `AGENTS.md` states for humans: hold labels, forbidden paths, DCO sign-off and architecture boundaries. `tests/test_agent_policy.py` validates its shape and that every path it references exists. Change a rule in both places.
