# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

This entry covers the full set of changes since the last shared baseline; the
project version is `0.25.0` (untagged — no release has been cut). It bundles a
large security-hardening pass, the settings-module collapse, an environment-file
selector model, a new Kubernetes deployment topology, an nginx sidecar, four
database migrations, and dependency CVE remediation. Audience tags are inline:
`[dev]` developers/contributors, `[ops]` operators/deployers, `[user]` end users.

### Upgrade / operator notes

This release contains security fixes; deploy it in full at the earliest
scheduled window and do not cherry-pick.

- Apply database migrations 0121–0124 in order — blocking:yes;
  reversible:partial (0121/0123/0124 reverse cleanly; 0122 is one-way — its
  reverse re-creates the column but cannot restore destroyed plaintext, so a
  rollback past 0122 requires re-provisioning every onsite terminal);
  prereq:none (linear chain on 0120); existing creds/data: existing terminal
  bearer tokens keep authenticating unchanged across the forward deploy (the
  0122 backfill hashes in place, never regenerates). (`47e2d6cf`, `57105532`,
  `60c5d92f`, `895a803f`)
- Adopt the single canonical env-driven `settings.py` and the `.env.*`
  selector model — blocking:yes; reversible:yes (revert restores the old
  per-target `settings.py.*` set); prereq:none; existing creds/data: copy
  real secrets into a gitignored `.env.production` from
  `.env.production.example`; `APIS_ENV` selects posture and a production
  process started with a test/DEBUG posture now hard-fails at boot.
  (`89282d1e`, `ca0ff301`, `05379587`, `ffdb4f2a`)
- Reconcile environment-file names across README, compose and deploy paths
  (`development.env`/`example.env`/`database.env` → `.env.dev`/`.env.test`/
  `.env.ci`/`.env.production.example`/`database.env.example`) —
  blocking:yes (compose `env_file:` and the Makefile/up.sh sourcing now read
  the new names); reversible:yes; prereq:do this together with the settings
  collapse above; existing creds/data: re-copy local dev/test env files from
  the tracked `*.example` templates. (`ca0ff301`, `ad2d6a2e`)
- Provision/verify the trusted-proxy allow-list and client-IP header before
  exposing the app — blocking:yes (with a non-empty allow-list the resolver
  fails closed on an untrusted peer); reversible:yes (config-only);
  prereq:set the trusted client-IP header for the chosen topology and the
  trusted-proxy CIDR list in the production env; existing creds/data:
  unaffected — this only governs which forwarded client-IP headers are
  honoured. SAFETY: an EMPTY allow-list SKIPS the peer check entirely (any
  upstream may inject the forwarded client-IP header) — that is acceptable
  only for dev/test; a production deployment MUST set a non-empty
  trusted-proxy CIDR list or client-IP spoofing of lockout/rate-limit/audit
  is possible. (`40fa1afc`, `a88c654a`, `a2232f41`, `f3510eec`)
- Stand up the new container-orchestration topology (standalone production
  compose, the nginx TLS/edge sidecar image, the optional monitoring stack,
  and the Kubernetes base+overlays) — blocking:no for the existing path,
  required only when migrating to it; reversible:yes (additive; the prior
  single-image path is unaffected); prereq:pull the pinned app/sidecar
  images, mount TLS/secret material, expose the documented ports, and apply
  the hardening flags before switching traffic; existing creds/data:
  unchanged — same database/secret inputs, new delivery surface.
  (`47e2d6cf`, `994e3606`, `e96c3868`, `b03473eb`)
- Re-resolve the dependency lockfile to clear known CVEs and adopt the new
  lock manager — blocking:yes (the SCA CI gate now fails a merge on a
  HIGH/CRITICAL advisory); reversible:yes; prereq:none; existing creds/data:
  unaffected. The lock manager moved from `poetry.lock` to `uv.lock`; Django
  and urllib3 were advanced to their patched releases. (`7823b310`,
  `a9a52f67`)
- Provide first-run admin provisioning via the `bootstrap_admin` management
  command — blocking:no (only needed on a fresh instance); reversible:n/a
  (idempotent — refuses to run once any superuser exists); prereq:none;
  existing creds/data: existing admins are left untouched; supply the
  credential via Key Vault-bound environment in IaC or run it interactively.
  (`7540342a`)

Census closure: migrations 0121–0124 (apply order) ✓; settings collapse +
`.env.*` selector ✓; trusted-proxy / client-IP fail-closed ✓; terminal-token
reversibility ✓; dependency CVE bumps (Django, urllib3) ✓; new topology
(prod compose / nginx sidecar / monitoring / Kubernetes) ✓ — every
`### Security` entry below is self-contained: it carries its CWE/OWASP id,
the S33/audit cross-id, the fixing commit, and an explicit
`CLOSED`/`DEFERRED`/`ACCEPTED` annotation where work remains. The
standalone security-backlog / security-review markdown files were
retired; this `### Security` ledger plus the git remediation trail is now
the durable, self-contained record. Still-tracked items are
enumerated in **Open security work** immediately below.

### Open security work

Planned-but-deferred and accepted-risk audit items, tracked here so they
are not forgotten (no separate backlog file):

- **HIGH-7 — no automated backup/PITR for in-cluster stateful Postgres/
  Redis** (single-replica StatefulSets / bind-mounted PVCs;
  `aks/base/statefulset-postgres.yaml`, `aks/base/statefulset-redis.yaml`,
  `docker-compose.prod.yaml`). `DEFERRED` — operations-infrastructure
  decision, not a code fix: preferred path is migrating prod durable
  state to Azure Database for PostgreSQL Flexible Server + Azure Cache
  for Redis (managed backups + PITR, makes the finding moot by
  construction); otherwise add a Velero schedule or a `pg_dump` CronJob
  to Azure Blob Storage with a documented RPO. Routed to the ops
  follow-up; not silently dropped and not claimed fixed.
- **INFO-1 — managed-identity / tenant ids in the AKS overlay.**
  `ACCEPTED` (public, non-secret identifiers; info-only) — recorded so
  the prior acknowledgement is not lost with the retired review file.
- **HIGH-3 (JS-context XSS) and the P1 #5 admin status-writer residual
  are now `CLOSED`** — see the CWE-79 and CWE-362 entries below.

### Breaking changes

- The per-target `settings.py.*` files and `settings_test.py` are removed in
  favour of one env-driven `settings.py`; deployments must supply the
  matching `.env.*` posture. Cross-reference: see the settings-collapse and
  env-file-rename items under Upgrade / operator notes (`ca0ff301`,
  `ad2d6a2e`).
- Database migration 0122 is irreversible with respect to terminal-token
  data: rolling back past it requires re-provisioning every terminal.
  Cross-reference: see the migrations item under Upgrade / operator notes
  (`60c5d92f`).
- CI now blocks a merge on software-composition-analysis (HIGH/CRITICAL CVE)
  findings and on any mypy type error. Cross-reference: see the
  dependency-lockfile item under Upgrade / operator notes and the CI-gate
  entries under Changed (`7823b310`, `07b4266d`).

### Added

- [ops] New Kubernetes deployment topology: base manifests plus dev/prod
  overlays (Deployments, StatefulSets for Postgres/Redis, HPA, PDB,
  NetworkPolicy, Workload-Identity/CSI secret provider, Ingress). The
  operator action for adopting it is recorded once under the
  container-orchestration item in Upgrade / operator notes. (`47e2d6cf`)
- [ops] New nginx edge/TLS sidecar image with edge-provider-aware real-IP
  and origin-lock configuration, plus a standalone hardened production
  compose file and an optional monitoring stack compose file. The operator
  action is recorded once under the container-orchestration item in
  Upgrade / operator notes. (`47e2d6cf`)
- [ops] New operator documentation shipped: a deploy-preflight checklist
  (image-digest pinning, NetworkPolicy CIDRs). (`e96c3868`)
- [dev] New Azure VM deploy script and monitoring-stack setup script.
  (`47e2d6cf`)

### Changed

- [dev] Local dev compose no longer runs an nginx service: gunicorn is
  exposed directly on `:8000`, the dev image is `apis:dev`, and services
  load `.env.dev` (not `.env`). Local app URL/port and the env selector
  changed accordingly. (`47e2d6cf`)
- [ops] All base container images in the dev/prod compose are pulled through
  a public registry mirror instead of Docker Hub directly. (`994e3606`)
- [ops] Database migrations are now single-locus: `start.sh` runs
  `migrate` only on the web entrypoint, never on worker replicas, removing
  the cold-start race where every web+worker replica migrated concurrently
  (S34). The settings-collapse and env-file-rename operator changes that
  previously sat here are recorded once under Upgrade / operator notes.
  (`837e3db7`)
- [dev,ops] CI gate hardening: a blocking software-composition-analysis
  workflow (pip-audit/npm-audit/Trivy) was added and mypy was flipped to
  blocking; contributors and release pipelines must now satisfy both.
  Third-party GitHub Actions are SHA-pinned. (`7823b310`, `772a3d10`,
  `34aef692`, `07b4266d`)
- [ops] The MQTT push-token default TTL is read from settings at call time
  rather than from a module global, so it can be tuned without a code
  change and is bounded. (`ec0dc518`)
- [dev] The InfluxDB reporting path adds a v3 client while keeping the
  legacy v1/v2 clients for backward compatibility. (`47e2d6cf`)

### Removed

- [dev,ops] Removed the per-target `settings.py.{ci,devel,direnv,docker,
  example}` files and `settings_test.py` (superseded by the single
  env-driven `settings.py`). (`ca0ff301`)
- [dev] Removed the unused `debug_server` helper and the stale `.flake8` /
  `.isort.cfg` / `.coveragerc` configs (consolidated into the ruff/pytest
  tooling). The `poetry.lock`→`uv.lock` lock-manager swap and the unused
  unvalidated signature-upload form removal are recorded once under
  Upgrade / operator notes and `### Security` (LOW-2) respectively.
  (`7823b310`, `020479f3`)

### Fixed

- [dev,ops] Webhook age-window check moved into a neutral,
  settings-driven module and folded inside signature verification so it is
  enforced only on a cryptographically verified request and never drops a
  legitimate first delivery. (`eeb54236`, `d697fb25`, `8328f8d3`)
- [ops] Webhook handling no longer breaks on Django's removal of
  `django.utils.timezone.utc` (S2). (`ca8b9644`)
- [dev] e2e auth no longer 403s on the allauth client-IP contract; e2e now
  runs with brute-force protection active. (`ead14e6e`, `146b3c90`)
- [dev] PayPal webhook handling fixed a `NoneType` class bug and the
  migration graph is now env-independent so `makemigrations --check` is
  clean. (`7823b310`, `895a803f`)
- [dev] Database migration 0121: replace the global unique constraint on
  payment-webhook notification ids with a composite uniqueness on
  `(integration, event_id)` so a PayPal id colliding with a Square event id
  no longer silently drops one of the two events. (`47e2d6cf`)
- [ops] Database migration 0122: hash terminal bearer tokens at rest and
  drop the plaintext column (in-place backfill, no terminal re-provision on
  the forward deploy). (`60c5d92f`)
- [dev] Database migration 0123: add the nullable `Order.opened_at_terminal`
  binding column (fail-safe, no backfill). (`57105532`)
- [dev] Database migration 0124: switch the Event email-field defaults to a
  module-level callable so the migration graph no longer diverges by
  environment. (`895a803f`)

### Security

- **CWE-639 / OWASP API1 (BOLA)** in `registration/views/onsite_admin.py`
  and `registration/views/ordering.py` — attendee-detail and
  order-completion endpoints loaded objects by client-supplied id without
  verifying the requesting terminal owned that order/attendee, so a valid
  terminal token plus a guessed reference could read PII or complete an
  unrelated order; the fix persists the opening terminal on the order and
  rejects cross-terminal access with a generic 404. Severity: HIGH.
  Tracking: S33 HIGH-1/HIGH-2, SECURITY_REVIEW HIGH-1/HIGH-2, Decision #9.
  (see Upgrade notes — migration 0123) (`57105532`)
- **OWASP API5 / ASVS V4.1.2 (function-level authz model undocumented)** in
  the terminal vs staff capability tiers — the bearer-terminal and
  `@staff_member_required` trust tiers were implemented but undocumented, so
  the access-control model could not be reviewed or safely changed; the fix
  ships `docs/terminal-trust-model.md` describing the kiosk model and the
  order↔terminal binding bound. Severity: MED. Tracking: S33 MED-1,
  SECURITY_REVIEW MED-1, Decision #9. (`7a7bbd45`)
- **CWE-79 / OWASP A03 (DOM/JS-context XSS)** in payment/dealer templates —
  an admin-controlled event email was interpolated directly into JavaScript
  string literals (auto-escape covers HTML context, not JS-string context),
  so a crafted email could break out of the string into script; the fix
  emits the value via `json_script` and reads it with `JSON.parse`.
  Severity: HIGH. Tracking: S33 HIGH-3, SECURITY_REVIEW HIGH-3, Decision #9.
  (`1ad76705`)
  (CLOSED — S33 HIGH-3. The earlier pass was partial (only
  `dealerasst-add.html`/`dealer-form.html`/`master.html`/`onsite.html`).
  Validation found the class was broader than the originally-named 3
  templates: **every** admin-set `Event` string reaching a JS-string
  literal is now escaped — `registrationEmail`/`dealerEmail`/`staffEmail`
  and `event.name` (incl. `str(event)` = `{{ event }}` interpolated into
  AJAX-payload object literals), standardised on `|escapejs` to match the
  accepted `onsite.html`/`dealer-form.html` precedent (the user-chosen
  encoder; `json_script` is retained where it already serialises
  structured payloads). Coverage: all string-literal sinks across 9
  templates — `attendee-upgrade.html`, `dealer/dealer-payment.html`,
  `staff/returning-staff-payment.html`, `onsite-checkout.html`,
  `onsite.html`, `registration-form.html`, `staff/new-staff-payment.html`,
  `dealer/dealer-form.html`, and `attendee-locate.html`. Peer review also
  surfaced the same JS-string-context class for *attendee-supplied*
  CharFields (`attendee.state`/`attendee.country`) in
  `dealer/dealerasst-add.html` — escaped in the same pass. The 3 numeric
  `{{ event.*Discount.amountOff }}` `<script>` sites (and the numeric
  `dealer.id`/`partnerMax`/`discount.amountOff`/`percentOff` sites) are
  intentionally out of scope — `DecimalField`/`IntegerField`,
  admin-validated, rendered as canonical numbers, XSS-safe by type.
  Guarded by a new DB-free deterministic source test plus per-view
  render tests (`registration/tests/test_xss_escapejs.py`). Realistic
  severity is
  defense-in-depth (admin-only input behind allauth+MFA, plus the
  no-`unsafe-inline` CSP); the fix is still required per ASVS V5
  encode-for-sink. The render-test guard is a fixed-point regression
  guard for the known sinks, not class-level recurrence prevention.)
- **CWE-601 (Open Redirect)** in `registration/admin.py` (admin order/refund
  views) — a redirect target derived from request-controlled input was
  followed without allow-list validation, so an authenticated admin could be
  sent to an attacker-chosen destination; the fix route-reverses the redirect
  to the refund/order-change page (the static-analysis-recognized sanitizer)
  and no longer echoes a client-supplied target. Severity: MED.
  Tracking: CodeQL #40/#41, CI #43. (`1dc97acc`, `92ffa3a4`)
- **CWE-362 / OWASP A04 (concurrency / TOCTOU race)** in
  `registration/payments.py`, `registration/paypal_webhook_handlers.py` and
  the onsite/expiry paths — duplicate provider webhook deliveries could both
  pass a read-time status guard and double-complete an order, double-decrement
  capacity counters, and double-send the registration email; the fix
  introduces a fused compare-and-set status primitive and serializes every
  async webhook status writer under a row lock with side effects deferred to
  commit. Severity: HIGH. Tracking: S24, P1 #5 (webhook
  order-completion race), Decision #10 R1. (`ebb766e9`, `e93998e0`,
  `9f184066`, `1cd12658`)
  (CLOSED — P1 #5. The webhook order-completion race is closed and
  CI-gated. The previously-documented residual — the synchronous admin
  status writer in `registration/admin.py` (`OrderAdmin.save_model`),
  which the prior note dismissed "by design" — was validated to be a
  real admin-vs-webhook clobber and is now itself remediated: a
  permission-tier user can no longer edit `status` at all
  (`OrderAdmin.get_readonly_fields` makes it read-only without
  `registration.issue_refund`, so Django excludes it from the form —
  the no-perm race is eliminated by construction), and a *permitted*
  status change is routed through the same fused
  `transition_order_status` CAS in one atomic row-locked UPDATE (which
  also runs the capacity transition the old raw `obj.save()` skipped —
  capacity now moves correctly on manual admin status corrections, with
  no double-move: the delta is computed under the row lock and is a
  no-op when `old == new`). No raw `Order.status` writer remains in
  `registration/admin.py`. Peer review also caught a sibling bypass: the
  `import_export` bulk-CSV path (OrderAdmin had no field-restricted
  resource) could mass-assign `status`, sidestepping
  `get_readonly_fields`/the CAS (CWE-862); `OrderAdmin.has_import_permission`
  now also requires `registration.issue_refund`. Covered by new
  `OrderAdmin` `get_readonly_fields`/`save_model`/`has_import_permission`
  unit tests and an admin-vs-webhook convergence test
  (`registration/tests/test_admin.py`,
  `registration/tests/test_concurrency.py`).)
- **CWE-922 / OWASP A02 (sensitive data at rest)** in
  `registration/models.py` / migration 0122 — terminal bearer tokens were
  persisted in plaintext, so a database read or leaked backup yielded every
  terminal credential; the fix stores only a SHA-256 hash, looks tokens up
  by hash with a constant-time compare and a generic 401 on miss, and drops
  the plaintext column. Severity: HIGH. Tracking: S21/S17, SECURITY_REVIEW
  HIGH-5, Decision #8. (see Upgrade notes — migration 0122 is one-way)
  (`60c5d92f`)
- **CWE-757 / CWE-347 (signed-token context confusion)** in
  `registration/signing.py` and its callers — every `TimestampSigner` shared
  the default salt, so a token minted for one signed purpose validated for
  another (a print-capability blob replayable as a terminal-token cookie);
  the fix centralizes per-purpose salts and a validated terminal-token
  payload bound to the terminal id and a rotation epoch, so a leaked cookie
  is invalidated when the terminal token is rotated. Severity: HIGH.
  Tracking: Decision #10 R1, MED-2, SECURITY_REVIEW MED-2. (`5d098664`,
  `d24f8690`)
- **CWE-532 / OWASP A09 (PII in logs)** in `registration/views/cart.py` and
  the logging pipeline — the ban-list reject path logged the registrant
  email in plaintext and there was no central log-redaction floor; the fix
  removes the email from the log line and adds a logging filter that
  pattern-redacts emails/phones/PAN-like tokens while preserving audit IP
  and timestamp fields. Severity: HIGH. Tracking: S33 HIGH-4 / MED-6,
  SECURITY_REVIEW HIGH-4/MED-6, Decision #10 R1. (`020479f3`, `7a7bbd45`,
  `0db8063d`)
- **CWE-348 (reliance on untrusted client-supplied address)** in
  `fm_eventmanager/middleware.py` and `fm_eventmanager/clientip.py` — the
  client-IP / forwarded-for trust chain was honoured without first verifying
  the request's immediate peer is an allow-listed upstream proxy, so a
  client could spoof its origin address (which feeds brute-force lockout,
  rate-limit buckets and audit logs); the fix routes all client-IP
  resolution through one pinned resolver and rejects, fail-closed, any
  request whose peer is outside the configured trusted-proxy allow-list
  before any forwarded header is read. Severity: HIGH. Tracking: MED-13,
  MED-14, S7–S13, Decision #10 R1. (see Upgrade notes — trusted-proxy /
  client-IP) (`40fa1afc`, `a88c654a`, `a2232f41`, `f3510eec`)
  (HISTORICAL NOTE: the HEAD code enforces the trusted-proxy allow-list
  and MED-13/MED-14 are fixed (`40fa1afc`, `a88c654a`, `a2232f41`,
  `f3510eec`). Commit `1713650`'s message text states MED-13/14 were
  "deferred" — that wording predates the in-band fix and is stale; git
  history is immutable so it is left as-is. Code state wins: fixed, not a
  code gap.)
- **CWE-770 / OWASP API4 (unrestricted resource consumption)** in
  `registration/ratelimit.py` and the registration/dealer/staff/webhook
  surfaces — registration submit, dealer/staff apply and webhook ingestion
  had no rate limit, allowing enumeration, capacity drain or forged-event
  floods if a signing key leaked; the fix adds IP-keyed rate limiting (keyed
  off the trusted client-IP resolver, returning a JSON 429), env-gated off
  for the test posture. Severity: MED. Tracking: S33 MED-3/MED-4,
  SECURITY_REVIEW MED-3/MED-4, Decision #9. (`a3da813b`, `9b6ae30a`)
- **CWE-359 / OWASP A02 (PII at rest)** in `registration/payments_sanitize.py`
  and `registration/payments.py` — `Order.apiData` stored the entire
  Square/PayPal response including billing/shipping/payer names, addresses,
  emails and phones; the fix recursively strips PII container/scalar keys
  before persistence while preserving the operational fields refund/dispute
  flows need. Severity: MED. Tracking: S33 MED-8, SECURITY_REVIEW MED-8,
  Decision #10 R1. (`95eb1d35`, `2c671f43`)
- **CWE-345 / OWASP A10 (unsafe consumption of an upstream API)** in
  `registration/views/paypal_webhooks.py` — every webhook re-fetched a
  PayPal OAuth token, coupling webhook latency/availability to the provider
  auth endpoint; the fix caches the token with a bounded TTL and a bounded
  401-triggered refresh/backoff. Severity: LOW. Tracking: S33 MED-7/LOW-3,
  SECURITY_REVIEW MED-7/LOW-3, Decision #9. (`bc76af0d`)
- **CWE-1395 / OWASP A06 (vulnerable dependencies; no SCA gate)** in CI and
  the dependency lockfile — there was no software-composition-analysis gate,
  and Django/urllib3 carried fixable known CVEs; the fix adds a blocking
  SCA workflow and advances Django and urllib3 to their patched releases.
  Severity: MED. Tracking: S33 HIGH-6, SECURITY_REVIEW HIGH-6, Decision #9,
  CLAUDE.md item 6. (see Upgrade notes — dependency lockfile) (`772a3d10`,
  `34aef692`, `a9a52f67`)
- **CWE-330 / OWASP A02 (weak key not validated)** in
  `fm_eventmanager/security_checks.py` / `registration/mqtt.py` — the MQTT
  push JWT secret was used without verifying it decodes to at least the
  HMAC-SHA256 key floor, leaving MQTT auth forgeable with a short secret;
  the fix asserts a minimum decoded key length at boot under the production
  guard. Severity: MED. Tracking: S33 MED-5, SECURITY_REVIEW MED-5,
  Decision #9. (`7a7bbd45`)
- **CWE-778 / OWASP A09 (missing audit log)** in
  `registration/views/onsite_admin.py` — cash-drawer admin actions were not
  audit-logged; the fix records who/what/when for those actions.
  Severity: LOW. Tracking: S33 LOW-4, SECURITY_REVIEW LOW-4, Decision #9.
  (`58cc2568`)
- **CWE-396 (overly-broad / missing exception handling — LOW-1
  "bare except in auth-header parse")** in
  `registration/views/onsite_admin.py` — the auth-header parse path called
  `.removeprefix("Bearer ")` directly on a possibly-absent Authorization
  header (an unguarded call that raised on missing/garbage input), and a
  separate broad `except Exception:` elsewhere in the same file masked
  failures; the fix wraps the header parse in a narrowly-scoped
  `except (AttributeError, TypeError)` (mirroring the guarded
  `complete_square_transaction` path) and tightens the separate broad
  catch. Scope note: LOW-1 covers the auth-header parse path only; other
  broad `except Exception:` blocks elsewhere in the file are outside
  LOW-1's scope and are not claimed fixed here.
  Severity: LOW. Tracking: S33 LOW-1, Decision #9 / #10 R1. (`5d098664`)
  (HISTORICAL NOTE: an earlier audit record mis-attributed LOW-1 to
  `020479f`; that commit does not touch this path. The actual fix is in
  `5d098664` (Decision #10 R1 FIX-B / ATTACK-4) — the attribution already
  used in this entry's commit trail above.)
- **CWE-1164 (unused, unvalidated form — latent injection trap)** in
  `registration/forms.py` — an unused `SignatureUploadForm` with no
  validation was present and would have been an unvalidated file-upload
  footgun if later wired to a view; the fix deletes the dead form.
  Severity: LOW. Tracking: S33 LOW-2, SECURITY_REVIEW LOW-2, Decision #9.
  (`7a7bbd45`)
- **CWE-1104 / OWASP A05 (unmanaged third-party JS / debug surface in prod)**
  in the frontend entrypoints — dev inspector/devtools modules were imported
  unconditionally, shipping a runtime introspection surface in production
  bundles; the fix gates them behind a build-time dev flag and moves them to
  dev dependencies so they are dead-code-eliminated from production builds.
  Severity: LOW. Tracking: SECURITY_REVIEW LOW-6 (analog). (`47e2d6cf`)
- **OWASP A04 / API3 (over-permissive secret/identity provisioning)** in
  `registration/management/commands/bootstrap_admin.py` — first-run admin
  creation needed a decisive identity/secret split and an idempotency guard
  so reruns or an attacker reaching the command cannot silently create a
  parallel admin; the fix splits identity from secret, validates the
  password against the configured validators, and refuses to run once any
  superuser exists (transaction + row lock). Severity: MED.
  Tracking: CodeQL R2, CI #43. (`7540342a`)
- **OWASP A05 / A06 (security misconfiguration & supply-chain hardening,
  Kubernetes/container topology)** in the new `aks/**`, nginx sidecar and
  production-compose surfaces — the new deployment topology was hardened in
  the same range: prod image pull-policy/digest-pin tooling and a
  NetworkPolicy-CIDR preflight, SHA-pinned GitHub Actions, PDB/HPA
  reconciliation, and a documented dev-overlay debug posture. Severity: MED.
  Tracking: S33 MED-9/MED-10/MED-11/MED-12/LOW-5/LOW-6, SECURITY_REVIEW
  MED-9..MED-12/LOW-5/LOW-6, Decision #9/#10 R1. (see Upgrade notes — new
  topology) (`e96c3868`, `34aef692`, `b03473eb`, `f3510eec`)
  (DEFERRED — HIGH-7: no automated backup/PITR for the in-cluster
  stateful Postgres/Redis workloads — an operations-infrastructure
  decision, not a code fix. Full rationale and remediation paths are
  stated inline under **Open security work** at the top of this entry;
  routed to the ops follow-up, not silently dropped and not claimed
  fixed; tracking S33 HIGH-7 / Decision #9.) (`1713650`)
