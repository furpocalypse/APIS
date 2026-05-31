# Production deploy preflight

> Resolves SECURITY_REVIEW_2026-05-11 **MED-9** (image digest pinning, CIS
> Docker 4.2 / OWASP A08) and **MED-12** (NetworkPolicy egress CIDRs, CIS
> Kubernetes 5.4.1). Both require **operator-supplied values** that cannot
> be hard-coded in the repo (registry digests change per rebuild; cluster
> CIDRs are environment-specific). This checklist makes setting them a
> deliberate, reviewed step rather than an omission.

## MED-9 — pin secondary image digests

The main app image already carries the `@sha256:` pattern (see the comment
block in `docker-compose.prod.yaml`). The secondary images (nginx,
postgres, redis, gotenberg, `apis-nginx`) are tag-only and would follow a
registry tag swap on the next `docker compose pull`.

Before every production release:

1. Resolve current digests (no pull, uses buildx imagetools):

   ```
   make refresh-digests
   ```

2. For each printed `ref@sha256:…` line, bake the digest into:
   - `docker-compose.prod.yaml` — every `image:` (nginx/postgres/redis/
     gotenberg/`apis-nginx`/`apis`), as `image: <ref>@sha256:<digest>`.
   - `nginx/Dockerfile` — the `FROM mirror.gcr.io/library/nginx:1.27-alpine`
     line → `FROM …nginx:1.27-alpine@sha256:<digest>`.
   - `aks/overlays/prod/kustomization.yaml` — the `images:` block
     `newTag: "0.5.3"` → `newTag: 0.5.3@sha256:<digest>` (and keep
     `imagePullPolicy: IfNotPresent` is then acceptable per MED-11).

3. Commit the digest bump as its own reviewed change. A digest that does
   not match the intended release is a release blocker, not a warning.

> Why not committed in-repo: a digest is only meaningful for one specific
> built artifact. Hard-coding a stale digest would either break the deploy
> or, worse, pin an old image silently. The deliberate `make
> refresh-digests` + review step is the control.

## MED-12 — confirm NetworkPolicy egress CIDRs

`aks/base/networkpolicy.yaml`'s `allow-app-egress` policy excludes the
RFC-1918 ranges `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` from its
`0.0.0.0/0:443/587/8883` egress allowance. **These are AKS placeholder
defaults.** If the cluster's pod / service / node CIDRs differ, the policy
does not actually segregate pod-to-pod traffic.

Before applying the prod overlay:

1. Determine the cluster's real pod, service, and node CIDRs:

   ```
   az aks show -g <rg> -n <cluster> \
     --query "networkProfile.{pod:podCidr,svc:serviceCidr}" -o table
   # node CIDR = the AKS subnet's address prefix
   ```

2. Replace the `except:` list in the prod overlay's NetworkPolicy patch
   (`aks/overlays/prod/kustomization.yaml`, `MED-12` patch block) with the
   actual pod/service/node CIDRs. Do **not** leave the AKS defaults.

3. Likewise replace `TRUSTED_PROXY_CIDRS` (currently the placeholder
   `10.224.0.0/16`) with the real Application Gateway subnet CIDR — this
   ties into S13 / MED-13.

A mismatch here is silent: traffic still flows, but the intended
pod-to-pod segregation is absent. Treat unconfirmed CIDRs as a deploy
blocker.
