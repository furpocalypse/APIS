# Load testing

Locust scenarios for verifying APIS can absorb the registration-opening
stampede (10,000+ concurrent page loads).

## Scenarios

See [locustfile.py](locustfile.py).

- `BrowsingUser` (weight 9): walks the public registration pages.
- `HealthProbeUser` (weight 1): hits `/healthz` + `/readyz` like ACA probes do.

## Run locally against docker-compose

```
docker-compose --profile loadtest up -d
# Open http://localhost:8089 for the Locust web UI, then point it at
# http://app:80 and ramp up. Or run headless from the CLI:
docker-compose --profile loadtest run --rm locust \
  -f /loadtest/locustfile.py \
  --host http://app:80 \
  --users 200 --spawn-rate 20 --run-time 2m --headless
```

## Run from a separate Azure VM against the stage environment

A laptop NIC + Wi-Fi will bottleneck before APIS does. Spin up a single
`Standard_D4s_v5` VM in the same region as the stage Container App, then:

```
sudo apt-get install -y python3-pip
pip install --user 'locust>=2.30'
locust -f locustfile.py \
  --host https://stage.apis.example.com \
  --users 10000 --spawn-rate 200 --run-time 6m --headless \
  --csv stampede
```

Pass criteria (per the deployment plan):

- p95 latency on `GET /registration/` < 1500 ms
- 0 5xx responses
- ACA reaches steady state replica count within 90s of ramp start
- Postgres CPU < 80% (Azure portal -> Flexible Server -> Metrics)
- Redis CPU < 60%

## Distributed mode (when one VM isn't enough)

```
# master
locust -f locustfile.py --master --host https://stage.apis.example.com
# workers (repeat on N machines)
locust -f locustfile.py --worker --master-host <master-vm-private-ip>
```
