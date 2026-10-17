# Security

## Reporting

Please do not open a public issue for a vulnerability. Email the maintainer (see the GitHub profile) with steps to reproduce; expect a reply within a week.

## What is in place

- JWT access + rotating refresh tokens, reuse detection, logout-all.
- Passwords hashed; login is rate limited by a Redis sliding-window limiter (atomic Lua), failing open to a local limiter.
- Per-user data isolation tested for IDOR (`tests/test_security.py`); error responses do not leak internals.
- Upstox OAuth uses a server-side `state` in Redis; broker tokens are stored server-side only and expire daily.
- `.env` files are git-ignored; the `secret-guard` workflow fails if one is tracked.
- Dependabot for pip, npm, Actions and Docker.

## If a secret leaks

Rotate it at the provider first, then remove it from history. Rotating is what actually protects you.
