# CLAUDE.md — APIS project context

This file is loaded by Claude Code automatically. Instructions in this file are **NON-OPTIONAL**: they MUST be honored on every change to this repository, without exception. If a request appears to conflict with this file, surface the conflict and ask for explicit confirmation before proceeding.

---

## Project at a glance

- **What it does:** APIS is the attendee registration platform used by FurTheMore and other fan conventions. It exposes **public-facing event-registration portals** that handle PII (names, addresses, emails, sometimes age verification), payments (Square + PayPal), and authentication (django-allauth + MFA).
- **Stack:** Django 6 (Python 3.13+), allauth + MFA, Postgres 16, Redis (cache + Celery broker + idempotency lock), gunicorn (uvicorn worker, ASGI), nginx (separate image), Celery, Vite frontend, django-prometheus.
- **Topology in production (docker-compose / single VM):** Two containers. `apis-nginx` (terminating TLS from Cloudflare on 443, enforcing the Cloudflare IP allowlist) → TCP → `apis` (gunicorn on :8000). See `nginx/README.md` and `docker-compose.prod.yaml`.
- **Topology in AKS:** Application Gateway Ingress Controller (AGIC) terminates TLS at the gateway → plaintext over the private subnet → `apis-app` Service :8000 → gunicorn pod. No nginx sidecar — AGIC replaces it. See `aks/README.md`.
- **Deployment target:** **Azure** (App Service / Container Apps). Prefer Azure-native primitives (Front Door + WAF, App Service, Key Vault, Managed Identity, Application Insights, Azure Cache for Redis, Azure Database for PostgreSQL Flexible Server).

---

## Security baseline — NON-OPTIONAL

This project **MUST** adhere to the OWASP standards listed below. These are not aspirational; they are the bar every change is reviewed against.

### Mandatory standards

The project conforms to the following OWASP standards. Any change that would violate one of these requires an **explicit, documented exception** approved by the user — not Claude.

1. **OWASP Top 10 — 2021 (web application risks)** — full mitigation expected:
   - A01 Broken Access Control
   - A02 Cryptographic Failures
   - A03 Injection (SQL, NoSQL, command, ORM, template, log)
   - A04 Insecure Design
   - A05 Security Misconfiguration
   - A06 Vulnerable and Outdated Components
   - A07 Identification and Authentication Failures
   - A08 Software and Data Integrity Failures (incl. supply chain)
   - A09 Security Logging and Monitoring Failures
   - A10 Server-Side Request Forgery (SSRF)

2. **OWASP API Security Top 10 — 2023** — applies to every JSON / webhook / AJAX endpoint:
   - API1 Broken Object Level Authorization (BOLA)
   - API2 Broken Authentication
   - API3 Broken Object Property Level Authorization
   - API4 Unrestricted Resource Consumption
   - API5 Broken Function Level Authorization
   - API6 Unrestricted Access to Sensitive Business Flows
   - API7 Server Side Request Forgery
   - API8 Security Misconfiguration
   - API9 Improper Inventory Management
   - API10 Unsafe Consumption of APIs

3. **OWASP ASVS v4.0.3** — Application Security Verification Standard:
   - **Level 2 minimum** for the entire application (handles PII + payments).
   - **Level 3** for the authentication, session, MFA, and payment-webhook code paths.
   - All ASVS V1–V14 control families apply; in particular V2 (Authentication), V3 (Session), V4 (Access Control), V5 (Validation/Sanitization/Encoding), V6 (Stored Cryptography), V7 (Error Handling/Logging), V8 (Data Protection), V9 (Communications), V10 (Malicious Code), V11 (Business Logic), V12 (Files), V13 (API and Web Service), V14 (Configuration).

4. **OWASP Proactive Controls v3.0** — defaults during implementation:
   - C1 Define Security Requirements
   - C2 Leverage Security Frameworks and Libraries (don't hand-roll crypto / authn)
   - C3 Secure Database Access (parameterized queries via the ORM only)
   - C4 Encode and Escape Data
   - C5 Validate All Inputs
   - C6 Implement Digital Identity (allauth + MFA, no DIY)
   - C7 Enforce Access Controls
   - C8 Protect Data Everywhere (TLS in transit, KMS at rest)
   - C9 Implement Security Logging and Monitoring
   - C10 Handle All Errors and Exceptions

5. **OWASP cheat sheets that are binding for this codebase**:
   - Django Security Cheat Sheet
   - Authentication Cheat Sheet
   - Session Management Cheat Sheet
   - Password Storage Cheat Sheet (Django's default Argon2/PBKDF2 chain is acceptable; do not weaken)
   - Cross-Site Request Forgery (CSRF) Prevention Cheat Sheet
   - Cross-Site Scripting (XSS) Prevention Cheat Sheet
   - Content Security Policy Cheat Sheet
   - HTTP Headers (Security Headers) Cheat Sheet
   - Input Validation Cheat Sheet
   - SQL Injection Prevention Cheat Sheet
   - Logging Cheat Sheet
   - Logging Vocabulary Cheat Sheet
   - Error Handling Cheat Sheet
   - File Upload Cheat Sheet
   - REST Security Cheat Sheet
   - Authorization Cheat Sheet
   - Access Control Cheat Sheet
   - Multifactor Authentication Cheat Sheet
   - Forgot Password Cheat Sheet
   - Choosing and Using Security Questions Cheat Sheet (do **not** use security questions)
   - User Privacy Protection Cheat Sheet
   - Transport Layer Protection Cheat Sheet
   - Secure Cookie Attribute Cheat Sheet
   - Clickjacking Defense Cheat Sheet
   - Denial of Service Cheat Sheet
   - Mass Assignment Cheat Sheet
   - Server Side Request Forgery Prevention Cheat Sheet
   - Unvalidated Redirects and Forwards Cheat Sheet
   - Insecure Direct Object Reference Prevention Cheat Sheet
   - Vulnerability Disclosure Cheat Sheet
   - Third Party JavaScript Management Cheat Sheet
   - DOM-based XSS Prevention Cheat Sheet
   - HTML5 Security Cheat Sheet
   - JSON Web Token (JWT) for Java Cheat Sheet (applies in spirit to PyJWT usage)
   - Webhook Security Cheat Sheet (signature verification, replay protection)
   - Docker Security Cheat Sheet
   - Kubernetes Security Cheat Sheet (when deployed via AKS / Container Apps)
   - Secrets Management Cheat Sheet
   - Key Management Cheat Sheet

6. **OWASP Dependency-Check / SCA discipline** — every PR must clear:
   - `uv lock --check` and `pip-audit` (or equivalent) on Python deps.
   - `npm audit --omit=dev --audit-level=high` on `registration/frontend`.
   - No new dependency with a known unfixed High or Critical CVE.

7. **OWASP Secure Coding Practices Quick Reference Guide** — used as the secondary checklist when ASVS doesn't speak directly to the change.

8. **OWASP SAMM** is used as the program-level maturity reference; it informs *process*, not per-PR review.

### Operational rules derived from the standards

These are how the standards above translate into day-to-day decisions on this codebase:

- **Inputs at trust boundaries are always validated and bounded.** Never trust the client. Forms/serializers must declare types, lengths, and allowed values. Reject — don't sanitize-and-pray — on invalid input.
- **Output is encoded for its sink.** Templates rely on Django auto-escape; never use `|safe` / `mark_safe` on user-controlled data. SQL goes through the ORM or parameterized queries — no f-string/`%` interpolation into raw SQL.
- **Authentication uses allauth.** Don't bypass it, don't write parallel auth flows, don't downgrade MFA or password validators.
- **Session and CSRF cookies must be `Secure`, `HttpOnly`, `SameSite=Lax` (or stricter).** CSRF is enforced on every state-changing endpoint; webhooks are exempted only when they validate a provider signature.
- **Webhooks (Square, PayPal) MUST verify the provider's signature on every request, reject on mismatch, and protect against replay** (use `event_id` / `transmission_id` idempotency).
- **Secrets live in Azure Key Vault** (Managed Identity-bound at runtime) — never in source, never in committed env files. The repo ships `.env.dev.example` and `.env.prod.example` as templates only. Local dev uses `.env.dev` (gitignored, copied from `.env.dev.example`); production uses `.env` on the VM (copied from `.env.prod.example`, with values sourced from Key Vault at boot).
- **Rate limiting and abuse controls** are required on: login, password reset, signup, MFA challenge, registration submit, payment intent creation, webhook ingestion, and any endpoint that fans out to external services. Burst handling for "registration opens" traffic is a primary design concern, not an afterthought.
- **`DEBUG=False` in any non-development environment.** No `debug_toolbar` middleware, no Django Debug Toolbar URLs, no Werkzeug debugger, no `manage.py runserver_plus` in production images.
- **`ALLOWED_HOSTS` is an explicit list** of FQDNs. `CSRF_TRUSTED_ORIGINS` is an explicit list of `https://` origins. Wildcards are forbidden in production.
- **Security headers** (set by Django's `SecurityMiddleware` + an explicit CSP middleware): HSTS (≥ 1y, preload after staging), X-Content-Type-Options, X-Frame-Options=DENY, Referrer-Policy=strict-origin-when-cross-origin, Permissions-Policy=minimal, Content-Security-Policy with no `unsafe-inline` (use Vite hashed assets + nonces).
- **TLS** terminates at Azure Front Door / App Gateway. Internal hop to App Service is also TLS where possible. `SECURE_SSL_REDIRECT=True`, `SESSION_COOKIE_SECURE=True`, `CSRF_COOKIE_SECURE=True`, `SECURE_HSTS_SECONDS≥31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS=True`, `SECURE_HSTS_PRELOAD=True`.
- **Trusted-proxy chain is explicit.** The list of upstream proxies (Front Door, App Gateway) is configured; client IP is derived only from headers set by trusted hops. See the `remote_addr` / `X-Forwarded-For` handling note in the Architecture section below.
- **Logging is structured, scrubbed, and centralized.** No PII, no payment instrument data, no session IDs, no auth tokens, no webhook bodies in logs. Sentry's `send_default_pii=True` must be reviewed — default to **False** unless the user explicitly opts in.
- **Authorization is checked at every view.** `@login_required`, `PermissionRequiredMixin`, or explicit object-level checks. BOLA is the most likely class of bug here — every view that loads an object by id must verify the requesting user is allowed to see/modify it.
- **Idempotency keys protect payment and registration submit endpoints.** Don't remove or weaken `django-idempotency-key` coverage on those flows.
- **File uploads are restricted by type, size, and content sniff.** Stored outside the static tree. Never executed.
- **No `open redirects`** — `next` parameters are validated against an allowlist of paths or against `url_has_allowed_host_and_scheme`.
- **No SSRF** — outbound HTTP from the server validates the target against an allowlist; never fetch arbitrary user-supplied URLs.
- **Mass assignment** — forms / serializers / `Model.objects.create(**request.POST)` patterns are forbidden. Use explicit field lists.
- **Dependency upgrades** are reviewed for advisories before merge; lockfiles are committed and immutable in CI.

### When in doubt

If a change would touch authentication, sessions, payments, webhooks, secrets, headers, or the proxy chain, **pause and confirm with the user before applying it.** The cost of a misstep here is materially higher than for ordinary feature work.

---

## Architecture notes Claude must know

- **Settings layout:** Single canonical file at `fm_eventmanager/settings.py` (env-driven for all environments — dev vs prod is controlled by `.env` values, not by separate settings files). `fm_eventmanager/settings_test.py` is the test-runner override (CELERY_TASK_ALWAYS_EAGER, etc.). The Docker image bakes `settings.py` directly; the dev compose bind-mounts it for hot-reload of in-flight edits.
- **Compose layout:** `docker-compose.yaml` (dev, with `build:` + source mounts + named volumes) is the default — `docker compose up` Just Works after copying `.env.dev.example` → `.env.dev`. The dev compose loads `.env.dev` (not `.env`) so a prod-style `.env` can coexist on the same machine without clobbering dev. `docker-compose.prod.yaml` is the standalone production file (loads `.env`, pulls `furpocalypse.azurecr.io/apis:x.x.x`, full hardening, bind mounts to `${APIS_DATA_DIR}` on a persistent disk). `docker-compose.monitoring.yaml` is an optional overlay for the InfluxDB 3 + Prometheus + node-exporter + Grafana stack.
- **Proxy / client-IP chain (VM / Cloudflare topology):** Client → Cloudflare proxied edge → `apis-nginx` (terminates TLS, validates source IP is in a Cloudflare CIDR, rewrites `$remote_addr` from `CF-Connecting-IP`) → TCP `apis:8000` → gunicorn. nginx sets `X-Real-IP`/`X-Forwarded-For` to the rewritten address and `X-Forwarded-Proto: https`. Django reads these via `SECURE_PROXY_SSL_HEADER` + `ALLAUTH_TRUSTED_CLIENT_IP_HEADER`.
- **Proxy / client-IP chain (AKS topology):** Client → Front Door / AppGW (TLS terminates) → AGIC → `apis-app:8000` → gunicorn. AppGW writes `X-Forwarded-For`; trust the AppGW subnet CIDR via `TRUSTED_PROXY_CIDRS`. See ASVS V14, OWASP "HTTP Headers" and "Logging" cheat sheets.
- **Two-header reality (MED-14, by design):** T1 (Cloudflare/nginx) → the apis-nginx sidecar writes the real client into `X-Real-IP`; T2 (AKS) → AppGW/AGIC writes only `X-Forwarded-For`. allauth/`clientip` resolve via `ALLAUTH_TRUSTED_CLIENT_IP_HEADER` (`TRUSTED_CLIENT_IP_HEADER` env): set it to `X-Real-IP` for T1, `X-Forwarded-For` for T2. The `_IS_PROD` settings guard hard-fails if it is empty in production, so the topology choice is never silently wrong.
- **`TRUSTED_PROXY_CIDRS` is enforced in code (MED-13), not just config:** `RequireClientIPMiddleware` rejects (403 + `fm_eventmanager.security` WARNING) any request whose peer `REMOTE_ADDR` is outside the allowlist before any forwarded client-IP header is honored. Empty list = disabled (dev/test, or T1 where nginx does its own realip origin-lock). Keep this guard when touching the proxy chain.
- **OAuth callback paths are frozen** for the related wow-tankgear project (`/bnet-callback`, `/wcl-callback`). That memory note is unrelated to APIS but lives in the same Claude memory namespace; do not repurpose APIS routes with those names.
- **Prometheus metrics** are exposed on container port 81. This port **must not** be reachable from the public internet — bind it to the management network only.
- **Maintenance mode** is on (`django-maintenance-mode`); `/accounts/` is allowlisted. Any new admin-only path must be added to `MAINTENANCE_MODE_IGNORE_URLS` if it should remain accessible during a maintenance window.
- **Terminal trust model** (onsite point-of-sale): the bearer-token "terminal" tier vs the `@staff_member_required` tier is a deliberate kiosk model, bounded by the order↔terminal binding. The design is documented in [`docs/terminal-trust-model.md`](docs/terminal-trust-model.md) — read it before touching `complete_*_transaction`, `terminal_square_token`, or `Firebase`/terminal auth.

---

## Container registry + image naming

The official APIS container registry is **`furpocalypse.azurecr.io`** (Azure Container Registry, Furpocalypse organisation). Images are tagged `apis:x.x.x` where `x.x.x` is a semantic version (e.g. `apis:0.3.1`). The fully-qualified production reference is **`furpocalypse.azurecr.io/apis:x.x.x`**, optionally pinned by SHA256 digest for CIS Docker 4.2 compliance.

- Production compose / deploy scripts: always use the fully-qualified ACR path.
- Local dev builds: the Makefile currently defaults `IMAGE` to a different (GHCR) path; update to `furpocalypse.azurecr.io/apis` when refreshing the CI pipeline.
- Never reference an unscoped `apis:x.x.x` in production — it's ambiguous and risks pulling from Docker Hub if the local cache misses.

## Open security work

Planned but not yet implemented audit items live in
[`SECURITY_BACKLOG.md`](SECURITY_BACKLOG.md). They are tracked rather than
forgotten — any review touching the listed areas should read that file
first and either implement the deferred fix or document why it remains
deferred.

## Memory pointer

User-specific preferences and project history live in Claude's auto-memory (`~/.claude/projects/.../memory/`), indexed in `MEMORY.md`. This `CLAUDE.md` is the **project**-level binding context; memory is the **user/session** context. If the two ever conflict, this file wins for security/standards questions; memory wins for personal-style preferences.
