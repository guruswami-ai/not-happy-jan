# Security policy

## Supported versions

Not-Happy-Jan is pre-release software. Security fixes are applied to the latest
commit on the default branch until the first tagged release establishes a
versioned support policy.

## Reporting a vulnerability

Do not open a public issue for a vulnerability or suspected credential leak.
Use GitHub's private vulnerability reporting for this repository:

<https://github.com/guruswami-ai/not-happy-jan/security/advisories/new>

Include the affected component, reproduction steps, impact, and any suggested
mitigation. Avoid including real credentials, private voice samples, or other
sensitive user data in the report.

The prompt secret guard is an opt-in local heuristic. It does not replace a
secret manager, repository scanning, credential rotation, or provider-side
revocation.
