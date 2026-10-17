# Architecture decision records

| # | Decision | Status |
|---|---|---|
| [0001](0001-estimation-risk.md) | Model estimation risk in ARISE; never report 0% or 100% | Accepted |
| [0002](0002-conservatism-haircut.md) | Conservatism is a named, reported input, not a hidden bias | Accepted |
| [0003](0003-informed-consent-filter.md) | Risk/goal mismatches are flags, not hard blocks | Accepted |
| [0004](0004-asyncio-event-loop.md) | Run the dev API on the asyncio loop, not uvloop | Accepted |
| [0005](0005-route-theme-defaults.md) | Dark landing, light app, explicit choice wins | Accepted |
| [0006](0006-generated-verified-data.md) | Landing figures and tokens are generated and gated | Accepted |
| [0007](0007-models-chosen-out-of-sample.md) | Models ship only if they win their own out-of-sample test | Accepted |

Template: Context → Decision → Consequences.
