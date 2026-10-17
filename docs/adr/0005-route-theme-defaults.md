# 0005 — Route-based theme defaults

**Status:** Accepted

## Context
The landing page is a cinematic dark canvas (black, red, off-white, liquid glass). The working screens are read for long periods and suit an off-white surface.

## Decision
`/` defaults to dark; all other routes default to light. A choice made with the toggle is stored in `nexus-theme` and overrides the default everywhere. The theme is set by an inline head script before first paint.

## Consequences
No theme flash. Both themes must pass the 16 token contrast checks.
