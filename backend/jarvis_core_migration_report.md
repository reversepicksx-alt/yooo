# JarvisCore Phase 1 Migration Report

## Status

Phase 1 is implemented in shadow mode beside the Reverse Picks control
implementation. No subscriber route is redirected and no JarvisCore value is
fed back into production prediction, saving, settlement, or calibration.

## Matched or reusable

- Canonical soccer request identity and verified fixture/player resolution are
  already available in the JARVIS prediction path.
- Bayesian prior, momentum, covariate, Monte Carlo, calibration, prop-safety,
  factor-ledger, and normalized response fields are already produced by the
  control implementation.
- JARVIS audit snapshots already provide immutable production values,
  provenance, UNKNOWN handling, and settlement postmortem hooks.
- Tactical Memory is a separate advisory store and remains fail-open.

## Shadow-only or not yet independent

- JarvisCore currently wraps the control result for contract and parity
  measurement; it does not duplicate raw provider data or reimplement
  production math.
- Independent tactical fingerprint synthesis, matchup interaction,
  adversarial opposite-case generation, robustness reruns, and stage-level
  quantitative recomputation remain explicitly UNKNOWN or shadow-only.
- Monte Carlo parity is not claimed as exact because the control simulation is
  unseeded.

## Dependencies and risks

- Provider availability and Atlas persistence remain external dependencies.
- A disabled feature flag or persistence failure must not change subscriber
  behavior.
- Current verified fixture, lineup, role, manager/regime, injury, and tactical
  evidence must supersede stale Tactical Memory.

## Cutover gates

Subscriber cutover requires independent, leakage-safe replay evidence across
the supported soccer props, positions, venues, and thin-sample cases; exact
pre-simulation parity; documented Monte Carlo tolerance; provenance parity;
and explicit approval after reviewing persisted shadow disagreements.