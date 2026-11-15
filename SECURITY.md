# Security policy

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub:
**Security → Advisories → Report a vulnerability**
(https://github.com/joekwn/REDZONE/security/advisories/new).

Include what you found, how to reproduce it, and what could be affected. We will reply as soon as
we can and credit you in the fix if you wish.

## What is in scope

- The website and API (`webapp/`), account and session handling, rate limits
- Deployment files (`deploy/`, `vercel.json`, workflows)

## How REDZONE protects users

- Passwords: Argon2id hashes only. Session tokens: only SHA-256 hashes are stored.
- Cookies: HttpOnly, SameSite=Lax, Secure on HTTPS.
- Every state-changing API request requires the `X-RedZone` header (CSRF protection).
- Sign-in lockout after repeated failures; rate limits on sign-up, sign-in and look-ups.
- Scheduled endpoints (`/api/cron/*`) require a secret bearer token.
- All keys and secrets come from environment variables; none are stored in the repository.

## If you run your own copy

Keep `FIRMS_MAP_KEY`, `MOTHERDUCK_TOKEN`, `REDZONE_SECRET`, `CRON_SECRET`, `data/secret.key` and
SSH keys private. If one leaks, replace it at the provider and update your server or Vercel settings.
