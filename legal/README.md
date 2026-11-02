# fanno-legal

`fanno-legal` is the legal-office environment plugin for `fanno-work`. The
environment profile defines public actions and policy; capabilities implement
their semantics; backends operate real software; tasks provide inputs and
initial state; evaluators inspect results independently.

## Layout

```text
src/fanno_legal/
  runtime/          lifecycle and action dispatch
  capabilities/     agent-visible filesystem, document and record semantics
  backends/         local and container software adapters, including LibreOffice UNO
  state/            SQLite state, snapshots and append-only audit records
  evaluation/       reusable legal-domain checks

environments/legal-office/v1/
  environment.json  enabled actions and runtime limits
  actions.json      Action/Observation contracts
  compose.yaml      reproducible image-level validation
  images/runtime/    self-contained legal-office runtime image

tasks/               task specifications, seeds and source assets
evaluators/          task-level evaluator configuration
examples/            inspectable manual demonstrations
tests/unit/          fast policy and capability tests
tests/integration/   real backend tests
tests/e2e/           complete environment lifecycle tests
runs/                ignored local output
```

## Test on the host

The real LibreOffice tests are skipped if LibreOffice and PyUNO are absent.

```bash
cd /data1/rjj/fanno-work
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:legal/src \
python -m unittest discover -s legal/tests -v
```

## Build and test the runtime image

```bash
cd /data1/rjj/fanno-work
docker build \
  --network=host \
  --progress=plain \
  -f legal/environments/legal-office/v1/images/runtime/Dockerfile \
  -t fanno-legal-office:runtime-v0.9 \
  .

docker run --rm fanno-legal-office:runtime-v0.9
```

## Per-task Docker execution

`environment.json` is the local profile used by fast tests.
`environment.container.json` is the container profile used by integration and
Harbor runs. In container mode one isolated Docker container is created per
prepared environment. It drops Linux capabilities, uses a read-only root
filesystem, mounts `inputs/` read-only, and applies CPU, memory, PID and action
timeout limits. Email and calendar services are reachable only on the task's
internal service network; outbound access remains controlled by the harness.

The filesystem worker, Writer UNO worker and Calc UNO worker execute inside the
same task container and operate on the same `/workspace` mount. The public
Action contracts do not change when switching backends.

Run the cross-backend contract test after building the image:

```bash
cd /data1/rjj/fanno-work
PYTHONPATH=src:legal/src \
python -m unittest \
  legal.tests.integration.test_container_action_contracts -v
```

## Generate an inspectable UNO run

Choose a new empty output directory for each run.

```bash
cd /data1/rjj/fanno-work
mkdir -p legal/runs/document-uno-manual

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -v /data1/rjj/fanno-work/legal/runs/document-uno-manual:/demo-output \
  fanno-legal-office:runtime-v0.9 \
  python /app/legal/examples/run_document_uno_demo.py \
    --environment /app/legal/environments/legal-office/v1/environment.json \
    --task /app/legal/tasks/examples/document-append/v1/task.json \
    --output-dir /demo-output
```

Open `legal/runs/document-uno-manual/REPORT.html` to compare the original and
edited PDFs and inspect the persisted DOCX, trajectory, audit log and reset
evidence.

## Current boundary

The environment supports controlled filesystem access, deterministic text
extraction, real LibreOffice conversion, Writer editing and Calc inspection
through UNO, SQLite business records, GreenMail email, Radicale calendar,
audit logging, snapshots, artifact preservation, reset, and optional per-task
Docker isolation. The core Reward Engine and Harbor task compiler are present;
task-specific semantic checkers remain intentionally separate from the
environment. OCR, complex document redlining, and broader personal-agent
scenario generation are not yet implemented.
