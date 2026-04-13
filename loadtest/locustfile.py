"""
Locust scenarios for APIS registration.

Two scenarios:
  - ``BrowsingUser``: hits read-only public endpoints (root redirect, registration
    landing, /pricelevels/, /events/) at a steady rate. Mirrors the visitors who
    open the page during the registration window but don't necessarily complete a
    purchase.
  - ``HealthProbeUser``: hammers /healthz and /readyz the way ACA's probes will,
    so we can see what they cost when the autoscaler flares.

Stampede shape: configure on the CLI with ``--users 10000 --spawn-rate 200
--run-time 6m`` to ramp 0->10k over 50s and hold for 5m. See loadtest/README.md.
"""

from locust import HttpUser, between, task


class BrowsingUser(HttpUser):
    weight = 9
    wait_time = between(1, 4)

    @task(5)
    def landing(self):
        self.client.get("/registration/", name="GET /registration/")

    @task(2)
    def price_levels(self):
        self.client.get("/registration/pricelevels/", name="GET /pricelevels/")

    @task(2)
    def events(self):
        self.client.get("/registration/events/", name="GET /events/")

    @task(1)
    def departments(self):
        self.client.get("/registration/departments/", name="GET /departments/")


class HealthProbeUser(HttpUser):
    weight = 1
    wait_time = between(1, 1)

    @task(2)
    def healthz(self):
        self.client.get("/healthz", name="GET /healthz")

    @task(1)
    def readyz(self):
        self.client.get("/readyz", name="GET /readyz")
