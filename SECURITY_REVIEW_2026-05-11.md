# APIS — Security & Architecture Review

**Date:** 2026-05-11
**Branch:** `aks-sec-updates`
**Scope:** Full project, OWASP Top 10 2021 + API Top 10 2023 + ASVS v4.0.3 + selected cheat sheets + CIS Docker/Kubernetes Benchmark
**Reviewer:** Parallel-domain audit, six investigators
**Out of scope (already tracked):** P1 #5 webhook order-completion race — see [`SECURITY_BACKLOG.md`](SECURITY_BACKLOG.md).

---

## TL;DR

The codebase has a **strong security posture overall**. The CLAUDE.md standards are observed in the code: strict allowlists, fail-closed middleware, hardened containers, manifest-hashed static delivery, no raw SQL, no mass assignment, signature-verified webhooks. The major gaps are not architectural flaws — they're **incomplete defenses** (rate limiting, audit trails, dependency scanning) and **a small number of specific vulnerabilities** in templates and view code.

| Severity | Count | Top concern |
|---|---|---|
| **Critical** | 0 | — |
| **High** | 6 | XSS in payment templates; BOLA in `attendee_details`; missing SCA in CI |
| **Medium** | 14 | Rate-limit gaps on registration & webhooks; PII in logs; image-digest pin drift |
| **Low / Info** | 7 | Unused unsafe form; dev-overlay debug mode; HPA/PDB mismatch |

**Top 5 things to fix first** (effort vs. impact ranked):

1. **[HIGH-3]** Quote `{{ event.*Email }}` in `<script>` blocks (4 templates, 30 min)
2. **[HIGH-2]** Add per-terminal authorization to `complete_*_transaction` (2-4 hours)
3. **[HIGH-6]** Add `pip-audit` + `npm audit` + Trivy steps to CI (1 hour)
4. **[HIGH-1]** Bind `attendee_details` access to terminal/event scope (2 hours)
5. **[HIGH-4]** Stop logging email in `cart.py:210` ban-list path (5 min)

---

## Architecture summary

The project supports two deployment topologies with deliberately separate trust boundaries:

**VM topology** (`docker-compose.prod.yaml`): Cloudflare proxied DNS → `apis-nginx:443` (TLS termination, CF-Connecting-IP realip, Cloudflare CIDR allowlist) → `apis:8000` (gunicorn) on the docker bridge. Postgres and Redis on bind-mounted disks.

**AKS topology** (`aks/`): Application Gateway + WAF v2 → AGIC → `apis-app:8000` Service → gunicorn pod. No nginx sidecar (AGIC replaces it). Stateful workloads as StatefulSets on `managed-csi-premium` PVCs.

The single sharpest architectural trade-off is **statefulness**: both paths host Postgres and Redis in-cluster instead of using Azure Database for PostgreSQL Flexible Server + Azure Cache for Redis. The repo's docs explicitly recommend the managed services but don't enforce them. This is operationally fine for a low-traffic single-VM deploy; it's a significant gap for the AKS path where pod replacement + PV durability isn't a substitute for managed backup/PITR.

---

## High findings

### HIGH-1: BOLA in `attendee_details`
- **Where:** [registration/views/onsite_admin.py:1192](registration/views/onsite_admin.py#L1192)
- **What:** Returns PII (name, email, phone, address, DOB) for any badge ID supplied in the request without verifying the requesting terminal/staff is allowed to see *that specific attendee*. A compromised terminal token (or a curious staff user) can enumerate badge IDs.
- **Standard:** OWASP A01 / API1 (BOLA) / ASVS V4.1.3
- **Fix idea:** Bind each badge lookup to the active terminal's scope — terminals can only fetch attendees whose order/checkout was opened on that terminal, or attendees of an event the staff user is assigned to. Reject anything outside the allowed set with a generic 404.

### HIGH-2: Order-completion endpoints don't verify the order belongs to the calling terminal
- **Where:** [registration/views/onsite_admin.py:481](registration/views/onsite_admin.py#L481) (`complete_square_transaction`), [registration/views/onsite_admin.py:736](registration/views/onsite_admin.py#L736) (`complete_cash_transaction`)
- **What:** Both endpoints look up orders by `Order.reference` (a 16-char base32 token, ~74 bits of entropy — strong, but not infinite). The terminal authenticates via bearer token, but there's no check that the order being completed was *initiated by that terminal*. With a leaked/guessed reference plus a valid terminal token, an attacker can mark an unrelated order as `COMPLETED` (Square path) or trigger downstream Celery email + capacity-counter side effects (cash path).
- **Standard:** OWASP API1 (BOLA) / A01 / ASVS V4.1.3
- **Fix idea:** When an order is created at a terminal, persist `order.opened_at_terminal_id = terminal.id`. In the complete handlers, assert `terminal.id == order.opened_at_terminal_id` before processing; reject with a generic 404 otherwise. Pair this with [HIGH-1].

### HIGH-3: JavaScript-context XSS in payment + dealer templates
- **Where:**
  - [registration/templates/registration/attendee-upgrade.html:394, 403](registration/templates/registration/attendee-upgrade.html#L394)
  - [registration/templates/registration/dealer/dealer-payment.html:396, 405](registration/templates/registration/dealer/dealer-payment.html#L396)
  - [registration/templates/registration/staff/returning-staff-payment.html:163](registration/templates/registration/staff/returning-staff-payment.html#L163)
  - [registration/templates/registration/dealer/dealerasst-add.html:233](registration/templates/registration/dealer/dealerasst-add.html#L233)
- **What:** `{{ event.registrationEmail }}` and `{{ event.dealerEmail }}` are interpolated directly into JavaScript string literals — sometimes without surrounding quotes (`dealerasst-add.html:233`), sometimes inside `alert(...)` strings (the others). Django auto-escapes for HTML context, but **does not escape for JS-string context**. An admin-controlled email containing `';</script>...` becomes script. `result.message` (server-controlled) is also concatenated into `alert(...)` strings without escaping — fine today, but a footgun if the field ever takes user input.
- **Standard:** OWASP A03 / ASVS V5.3.3 / DOM XSS Prevention Cheat Sheet
- **Fix idea:** Use `{{ event.registrationEmail|json_script:"reg-email" }}` and read via `JSON.parse(...)`, matching the pattern the codebase already uses (see `dealerasst-add.html:218,240` — good example). Same fix for `result.message`: client-side escape with `$('<div>').text(result.message).html()` or just don't interpolate it as raw HTML/JS.

### HIGH-4: PII (email) logged in plaintext on ban-list reject
- **Where:** [registration/views/cart.py:210](registration/views/cart.py#L210)
- **What:** `logger.error(f"***ban list registration attempt: {pda['email']}***")` writes the registrant's email to stdout (and Sentry if escalated). The asterisks aren't redaction — they're emphasis. Sentry's `before_send` hook scrubs request bodies but not application-level logger calls.
- **Standard:** OWASP A09 / ASVS V7.1.1 / OWASP User Privacy Protection cheat sheet
- **Fix idea:** Replace with `logger.error("ban list registration attempt", extra={"email_hash": hashlib.sha256(pda['email'].encode()).hexdigest()[:8]})`. The 8-char hash is enough for log correlation without being PII.

### HIGH-5: Firebase terminal token stored plaintext at rest
- **Where:** [registration/models.py:1226](registration/models.py#L1226) (the `token` field on `Firebase`)
- **What:** Terminal bearer tokens are persisted as plaintext UUIDs. The recent `token_hash` field (SHA-256, used with `secrets.compare_digest` — see HIGH-5 positive note below) is the right design, but the plaintext column hasn't been dropped yet. A DB read by an attacker (or a leaked backup) yields every terminal credential without any guessing.
- **Standard:** OWASP A02 / ASVS V2.10.4 / V2.7.4 / Secrets Management cheat sheet
- **Fix idea:** Add a data migration that ensures every row has `token_hash` populated; then a follow-up that drops the plaintext `token` column. Switch all callers to `find_by_token()`-only (it already uses constant-time comparison). Document this as a P1 follow-up to the existing tracked migration.

### HIGH-6: No SCA in CI — known-vulnerable dependencies wouldn't block a merge
- **Where:** [.github/workflows/django.yml](.github/workflows/django.yml), [.github/workflows/docker.yml](.github/workflows/docker.yml)
- **What:** CodeQL scans source code; there's no `pip-audit`, `npm audit --audit-level=high`, or Trivy image scan with fail-on-CRITICAL/HIGH. The `uv.lock` is committed and `uv sync --frozen` runs in the container build (good), but a transitive package with a known CVE could be merged without anyone noticing until a manual scan.
- **Standard:** OWASP A06 / NIST SSDF
- **Fix idea:** Add three CI steps:
  ```yaml
  - run: uv run pip-audit --strict --desc
  - run: cd registration/frontend && npm audit --audit-level=high
  - uses: aquasecurity/trivy-action@<sha>
    with: { image-ref: 'apis:dev', severity: 'CRITICAL,HIGH', exit-code: '1' }
  ```
  Fail the build on HIGH/CRITICAL. Set a weekly schedule on the same workflow so new CVEs in already-locked deps surface even without a code change.

### HIGH-7: In-cluster stateful workloads have no backup/PITR strategy
- **Where:** [aks/base/statefulset-postgres.yaml](aks/base/statefulset-postgres.yaml), [aks/base/statefulset-redis.yaml](aks/base/statefulset-redis.yaml), [docker-compose.prod.yaml:177-195](docker-compose.prod.yaml#L177)
- **What:** Single-replica Postgres + Redis with PVC persistence. If a managed disk corrupts, or someone runs `kubectl delete pvc` accidentally, there's no automated recovery. AKS README mentions Azure Database for PostgreSQL as the recommended path but the manifests don't enforce it.
- **Standard:** OWASP Proactive Controls C8 (Data Protection); operationally this is also a PCI-DSS-tier expectation given payment data lives here.
- **Fix idea:** Two paths:
  1. **Migrate to managed services** for prod (Azure DB for PostgreSQL Flexible Server + Azure Cache for Redis). Lift the workload off Kubernetes for the durable-state portion. *Strongly preferred.*
  2. Keep StatefulSets and add a Velero schedule (or a `pg_dump` CronJob streaming to Azure Blob Storage with lifecycle rules). Document the RPO target (e.g. "≤24h data loss") so the trade-off is explicit.
- **Remediation status (S33 / Decision #9):** **DEFERRED — operations-infrastructure decision, not a code fix.** No change to APIS source can make a stateful-workload backup strategy correct; this is a half-day architectural decision (managed Azure DB for PostgreSQL Flexible Server + Azure Cache for Redis — *path 1, the CLAUDE.md-preferred Azure-native primitive, which makes this finding moot by construction* — vs an in-cluster Velero/`pg_dump` schedule with a documented RPO). Tracked in the remediation plan's Findings ledger with rationale and routed to the ops follow-up; explicitly **not silently dropped** and **not** claimed fixed.

---

## Medium findings

### MED-1: Missing function-level authz on `terminal_square_token` (and adjacent)
- **Where:** [registration/views/onsite_admin.py:1216](registration/views/onsite_admin.py#L1216)
- **What:** Endpoint is `@csrf_exempt` but NOT `@staff_member_required`. It relies entirely on terminal-bearer-token auth. That's consistent with the kiosk model, but the design isn't documented — and `complete_square_transaction` + `complete_cash_transaction` follow the same pattern, blurring the line between "terminal" and "staff" capabilities.
- **Standard:** OWASP API5 / ASVS V4.1.2
- **Fix idea:** Either add `@staff_member_required` if the design intent is "staff-operated terminal", OR document the kiosk model explicitly (in CLAUDE.md or a `docs/terminal-trust-model.md`) and pair it with [HIGH-2] so the terminal token has narrow per-order capabilities, not blanket order-write.

### MED-2: Terminal session binding read from URL param
- **Where:** [registration/views/onsite_admin.py:501, 1160-1164](registration/views/onsite_admin.py#L501)
- **What:** `get_terminal_from_request()` reads the terminal ID from either the URL/POST body OR the session. A logged-in staff user's session can have its "active terminal" context flipped by passing a different `terminal` param. Probably not exploitable in practice (terminal still needs a valid token elsewhere), but the auth/identity coupling is loose.
- **Standard:** OWASP A07 / ASVS V3.2.1
- **Fix idea:** Re-validate the terminal token (via signed cookie or short-lived re-auth) on each request that mutates state, instead of trusting session-stored terminal ID indefinitely.

### MED-3: No rate limiting on registration submit
- **Where:** [registration/views/ordering.py](registration/views/ordering.py), [registration/views/dealers.py](registration/views/dealers.py), [registration/views/staff.py](registration/views/staff.py)
- **What:** Login/signup are rate-limited by allauth (20/min/IP, default). The custom registration submit, dealer apply, and staff registration flows aren't. An attacker can enumerate emails, drain capacity, or trigger mass email loops at line rate.
- **Standard:** OWASP API4 / ASVS V11.1.1 / Denial of Service cheat sheet
- **Fix idea:** `django-ratelimit` on the submit endpoints — e.g. 10 reg submissions / hour / IP, 30 dealer-apply / day / IP. Idempotency keys already exist for `Idempotency-Key` headers, which mitigates accidental retries but not deliberate floods.

### MED-4: No rate limiting on webhook endpoints
- **Where:** [registration/views/webhooks.py:92](registration/views/webhooks.py#L92), [registration/views/paypal_webhooks.py:166](registration/views/paypal_webhooks.py#L166)
- **What:** Both verify signatures + protect against replay via `event_id`. But no rate limit. If a signing key ever leaks, an attacker can flood thousands of forged-but-valid events per second.
- **Standard:** Webhook Security cheat sheet
- **Fix idea:** Limit by source IP (Square/PayPal publish their egress ranges) or by `event_id` insertion rate. 100/min/source is generous for legitimate traffic.

### MED-5: MQTT JWT secret length not validated at startup
- **Where:** [registration/mqtt.py:158](registration/mqtt.py#L158), [fm_eventmanager/settings.py:778](fm_eventmanager/settings.py#L778)
- **What:** `base64.b64decode(MQTT_JWT_SECRET)` is passed directly to `jwt.encode()`. No check that the decoded key is ≥ 32 bytes (the HMAC-SHA256 minimum for adequate strength). A short secret leaves MQTT auth forgeable.
- **Standard:** OWASP A02 / ASVS V6.1.1
- **Fix idea:** Add a startup assertion in settings.py: `if len(base64.b64decode(MQTT_JWT_SECRET)) < 32: raise RuntimeError(...)`.

### MED-6: No central PII redaction in the logging pipeline
- **Where:** [fm_eventmanager/settings.py:88-124](fm_eventmanager/settings.py#L88)
- **What:** Sentry's `before_send` scrubs request bodies, but plain `logger.*` calls (like HIGH-4) flow straight to stdout. There's no logging.Filter doing pattern-based redaction.
- **Standard:** OWASP A09 / ASVS V7.1.1 / Logging cheat sheet
- **Fix idea:** Add a custom `logging.Filter` subclass that regex-redacts emails, phone numbers, credit-card patterns. Attach to the console handler. Doesn't catch every leak but raises the floor.

### MED-7: PayPal access token not cached
- **Where:** [registration/views/paypal_webhooks.py:56-145](registration/views/paypal_webhooks.py#L56)
- **What:** Every webhook calls `_get_paypal_access_token()`, which hits PayPal's OAuth2 endpoint. Couples webhook latency to PayPal's auth uptime and adds avoidable load. Tokens are valid for ~1h.
- **Standard:** OWASP A10 (loosely) / ASVS V13.2.1
- **Fix idea:** Cache the token in Redis with a TTL of 50min (10 min safety margin below PayPal's 1h). Invalidate on 401 from the verify call.

### MED-8: `Order.apiData` stores Square responses unencrypted
- **Where:** [registration/models.py:1096](registration/models.py#L1096), [registration/payments.py:175-182](registration/payments.py#L175)
- **What:** Full Square API responses are dumped to a `JSONField`. No card data (Square doesn't return it — PCI-DSS compliance), but billing names, addresses, partial card metadata. A DB read leaks all of it.
- **Standard:** OWASP A02 / ASVS V6.2.1 / Privacy Protection cheat sheet
- **Fix idea:** Either field-level encryption (`django-cryptography` with a Key-Vault-managed key), or store only the strictly necessary fields (payment ID, status, last-4) and discard the rest.

### MED-9: Image digests not pinned (nginx, postgres, redis, ACR images)
- **Where:**
  - [nginx/Dockerfile:10](nginx/Dockerfile#L10) — `nginx:1.27-alpine` (tag only)
  - [docker-compose.prod.yaml:178, 196](docker-compose.prod.yaml#L178) — `postgres:16`, `redis:8`
  - [docker-compose.prod.yaml:62, 119, 146](docker-compose.prod.yaml#L62) — `apis-nginx:0.5.2`, `apis:0.5.3` (no `@sha256:...`)
- **What:** Main app image is correctly SHA-pinned (CIS 4.2). Secondary images aren't. A tag swap at the registry would land on the next `docker compose pull`.
- **Standard:** CIS Docker Benchmark 4.2 / OWASP A08
- **Fix idea:** Resolve each digest (`docker pull ... && docker inspect --format='{{.RepoDigests}}'`), bake into the file: `image: nginx:1.27-alpine@sha256:...`. Automate in a `make refresh-digests` target so refreshes are deliberate.

### MED-10: Floor-only Python dep constraints + GitHub Actions not SHA-pinned
- **Where:** [pyproject.toml](pyproject.toml) (`>=X.Y.Z` patterns), [.github/workflows/django.yml](.github/workflows/django.yml) (most `@v4`/`@v5` action refs)
- **What:** `uv.lock` already pins exactly, so the actual prod risk is low. But mutable action refs let a compromised action author inject CI-level code. One step in `docker.yml` is already SHA-pinned (`appleboy/ssh-action`) — apply that same discipline globally.
- **Standard:** OWASP A08 / GitHub Security Best Practice
- **Fix idea:** Tighten the most security-sensitive deps to `~=X.Y` and SHA-pin all third-party actions (`actions/checkout`, `astral-sh/setup-uv`, etc.). Renovate or Dependabot can auto-update the SHAs.

### MED-11: AKS `imagePullPolicy: IfNotPresent` in prod
- **Where:** [aks/base/deployment-app.yaml:67](aks/base/deployment-app.yaml#L67), [deployment-worker.yaml:49](aks/base/deployment-worker.yaml#L49)
- **What:** A node with a stale or compromised image cached for `apis:0.5.3` will reuse it across pod replacements rather than re-pulling. Dev overlay correctly forces `Always` for the `dev` tag.
- **Standard:** CIS Kubernetes Benchmark
- **Fix idea:** Either pin the image to a digest (covered by [MED-9]) and keep `IfNotPresent`, OR add a prod patch setting `imagePullPolicy: Always`. Pin-to-digest is the stronger guarantee.

### MED-12: NetworkPolicy egress carve-outs are AKS defaults, not your cluster's CIDRs
- **Where:** [aks/base/networkpolicy.yaml:145-147](aks/base/networkpolicy.yaml#L145)
- **What:** The `allow-app-egress` policy lets pods reach `0.0.0.0/0:443/587/8883` *except* `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`. These are placeholder defaults. If your cluster VNet uses a different range, the policy doesn't actually segregate pod-to-pod traffic.
- **Standard:** CIS Kubernetes Benchmark 5.4.1
- **Fix idea:** Move the `except` CIDR list into the overlay's `configMapGenerator` and reference it explicitly. Add an aks/README.md pre-flight checklist item: "confirm cluster pod/service/node CIDRs match the netpol exceptions".

### MED-13: `TRUSTED_PROXY_CIDRS` set in config but not enforced in Django code
- **Where:** [aks/base/apis-config.env:22-27](aks/base/apis-config.env#L22), [fm_eventmanager/settings.py:182-194](fm_eventmanager/settings.py#L182)
- **What:** The env var documents intent (only trust XFF from AppGW subnet) but no Django middleware checks the peer IP against the CIDR list before honoring `X-Forwarded-For`. A misconfigured ingress (or an attacker who reaches the Service directly) could inject spoofed XFFs.
- **Standard:** OWASP HTTP Headers cheat sheet / ASVS V13.1.4
- **Fix idea:** Extend `RequireClientIPMiddleware` (or a new sibling middleware) to verify `request.META['REMOTE_ADDR']` is in `TRUSTED_PROXY_CIDRS` before reading client-IP headers. Reject with 403 + WARN-level log otherwise.

### MED-14: Two header conventions across topologies, code reads only one
- **Where:** [fm_eventmanager/middleware.py:28](fm_eventmanager/middleware.py#L28), [aks/base/apis-config.env:25,27](aks/base/apis-config.env#L25)
- **What:** Cloudflare path → nginx writes `X-Real-IP`. AGIC path → AppGW writes only `X-Forwarded-For`. Config defines both `CLIENT_IP_HEADER` and `TRUSTED_CLIENT_IP_HEADER`, but `RequireClientIPMiddleware` reads only `TRUSTED_CLIENT_IP_HEADER` (default `X-Real-IP`). In AKS that header isn't set unless the operator overrides — meaning under AKS the middleware may either fail-closed (refuse all traffic) or accept whatever's in `X-Forwarded-For` unchecked.
- **Standard:** ASVS V13.1.4
- **Fix idea:** Either (a) set `TRUSTED_CLIENT_IP_HEADER=X-Forwarded-For` in the AKS overlay, with the middleware parsing the leftmost untrusted hop; (b) deploy an AGIC config that writes `X-Real-IP`; or (c) document the two-header reality in CLAUDE.md and make the code branch on it.

---

## Low / Info findings

| ID | Title | Location | Note |
|---|---|---|---|
| LOW-1 | Bare `except:` in auth-header parse | [onsite_admin.py:484](registration/views/onsite_admin.py#L484) | Replace with `except (AttributeError, TypeError):`. |
| LOW-2 | Unused `SignatureUploadForm` without validation | [forms.py:45-48](registration/forms.py#L45) | Delete or implement properly — currently not wired to any view, but the trap is set for the next dev. |
| LOW-3 | PayPal webhook verify has no timeout/backoff | [paypal_webhooks.py:76-161](registration/views/paypal_webhooks.py#L76) | Add backoff + circuit-breaker; tied to MED-7. |
| LOW-4 | Cash drawer admin actions not audit-logged | [onsite_admin.py:620-690](registration/views/onsite_admin.py#L620) | Write to a tamper-evident audit table (who/what/when/where/outcome). |
| LOW-5 | HPA `minReplicas=2-3` vs PDB `minAvailable=1` | [aks/base/hpa.yaml](aks/base/hpa.yaml), [aks/base/pdb.yaml:19](aks/base/pdb.yaml#L19) | Bump PDB `minAvailable` to `minReplicas - 1` on the overlay. |
| LOW-6 | `DJANGO_DEBUG=True` in AKS dev overlay | [aks/overlays/dev/kustomization.yaml:101](aks/overlays/dev/kustomization.yaml#L101) | Acceptable if the dev cluster is on a private VNet; document the assumption. |
| INFO-1 | Tenant + MI client ID committed in prod overlay | [aks/overlays/prod/patch-sa-wi.yaml](aks/overlays/prod/patch-sa-wi.yaml), [patch-spc-wi.yaml](aks/overlays/prod/patch-spc-wi.yaml) | These are public identifiers (not secrets per Azure's docs). Topology disclosure only; weigh against the deploy ergonomics of keeping them in-repo. |

---

## What's GOOD (positive controls observed)

Things the codebase does *correctly* that should be preserved:

- **`RequireClientIPMiddleware` fail-closes** when the trusted edge doesn't set `X-Real-IP` in non-DEBUG mode ([middleware.py:33-93](fm_eventmanager/middleware.py#L33)). Many Django apps silently fall back to spoofable `REMOTE_ADDR`; this one refuses to.
- **Webhook signature + replay protection** on both payment providers ([webhooks.py:48-87](registration/views/webhooks.py#L48), [paypal_webhooks.py:76-161](registration/views/paypal_webhooks.py#L76)). `event_id` uniqueness with `IntegrityError` handling closes the obvious race.
- **Constant-time bearer-token comparison** in `Firebase.find_by_token` ([models.py:1293-1310](registration/models.py#L1293)) via `secrets.compare_digest`. Combined with SHA-256 hashed lookup.
- **`ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` validation in settings.py** ([settings.py:155-180](fm_eventmanager/settings.py#L155)): explicit allowlists, wildcard rejection, RuntimeError-on-missing under `DEBUG=False`. Refuses to start misconfigured.
- **Sentry PII scrubbing** ([settings.py:28-48](fm_eventmanager/settings.py#L28)): drops webhook bodies and sensitive headers before send, `send_default_pii=False`.
- **Manifest-hashed static delivery** via `SelectiveManifestStaticFilesStorage` + `WhiteNoise` ([file_storage.py](fm_eventmanager/file_storage.py), [settings.py](fm_eventmanager/settings.py)). Hashed filenames get `Cache-Control: immutable`; the selective storage skips Vite's already-hashed bundle to avoid double-hashing.
- **Capacity TOCTOU protection** in `_check_capacity()` uses `select_for_update()` to lock price levels (single-request case correct; the multi-worker race is the tracked SECURITY_BACKLOG item).
- **`url_has_allowed_host_and_scheme()` on `next=` parameters** ([printing.py `_safe_internal_url`](registration/views/printing.py)). Open-redirect defense.
- **`json_script` template tag** used in the new templates ([dealerasst-add.html:218,240](registration/templates/registration/dealer/dealerasst-add.html#L218) etc.) — the *correct* pattern. HIGH-3 is about the templates that haven't been converted yet.
- **No raw SQL, no mass assignment**: zero `.raw(`/`.extra(`/`cursor.execute(`/`os.system(`/f-string-SQL findings; every `ModelForm` uses explicit `fields = (...)`, no `__all__`.
- **`SECRET_KEY` required at import time** ([settings.py:83](fm_eventmanager/settings.py#L83)): no fallback, app refuses to start without it. Prevents accidental git commits of a default.
- **CIS Docker hardening** on the prod compose: `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, `tmpfs` with `nosuid,nodev`, SHA-pinned main runtime image, SUID/SGID neutralized in the Dockerfile, non-root runtime users (apis uid 1000, nginx uid 101). Pre-commit lint runs on every commit.
- **AKS Pod Security `restricted`** enforced at the namespace label level — every pod must run non-root, drop ALL caps, `seccompProfile=RuntimeDefault`. Default-deny NetworkPolicy + explicit allow matrix. Workload Identity + Key Vault CSI driver — no service-principal secrets in the cluster.

---

## Recommended priority order

If you do one fix per work session, do them in this order. The first column is rough time, the second is whether it requires an image rebuild.

| # | Item | Effort | Rebuild? |
|---|---|---|---|
| 1 | **HIGH-3** Convert remaining `{{ event.*Email }}` to `json_script` | 30 min | yes (templates baked) |
| 2 | **HIGH-4** Stop logging email in ban-list path | 5 min | yes |
| 3 | **HIGH-6** Add `pip-audit` + `npm audit` + Trivy to CI | 1 hr | no |
| 4 | **MED-9** Pin nginx/postgres/redis/ACR images by SHA digest | 30 min | no |
| 5 | **HIGH-1** + **HIGH-2** Bind `attendee_details` + complete handlers to terminal scope (do together, share tests) | 2-4 hr | yes |
| 6 | **MED-3** + **MED-4** Rate-limit registration + webhook endpoints | 1-2 hr | yes |
| 7 | **HIGH-5** Drop plaintext `Firebase.token` (migration + caller sweep) | 2 hr | yes |
| 8 | **HIGH-7** Decide: managed Postgres/Redis OR a CronJob backup plan | half-day decision, days of execution | depends |
| 9 | **MED-13** + **MED-14** Make trusted-proxy CIDR + header chain enforceable in code, not just config | 2-3 hr | yes |
| 10 | **MED-6** Add a PII-redaction logging filter | 2 hr | yes |

Everything else can be folded into normal feature work.

---

## What this review did NOT cover

- **Performance / load** under registration-opens burst. The codebase has burst-handling primitives (HPA, rate limiting where present, idempotency keys), but a real load test against the AKS overlay would validate them.
- **PCI-DSS scope analysis.** Square and PayPal both keep card data off the origin, but a formal scope-reduction audit is out of scope for this review.
- **Frontend security in depth** (the Solid.js side). Spot-checks didn't find `dangerouslySetInnerHTML` / `eval`; a fuller frontend pass is worth doing separately.
- **The deferred webhook race** ([P1 #5 in SECURITY_BACKLOG.md](SECURITY_BACKLOG.md)). Already audited and tracked; intentionally not re-litigated here.

---

*Generated 2026-05-11 by parallel review across six security domains. No code changes were made — this report is read-only.*
