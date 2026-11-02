# Canonical Task Bundles

A canonical task bundle is the immutable input to Fanno execution,
evaluation, and target-specific packaging such as Harbor.

The authoring task directory remains the source of truth. Bundles are
generated outputs and must not be edited by hand.

## Layout

```text
canonical/
├── task.json
├── environment.json
├── actions.json
├── seed.json
├── evaluator.json
├── provenance.json
├── assets/
└── manifest.json
```

The manifest records the task, environment, action catalog, evaluator,
runtime image reference, every file hash, and a deterministic digest of
the complete bundle. `manifest.json` is excluded from its own digest.

Assets are resolved from:

- `task.json` input declarations;
- `seed.json` references below `assets/`;
- `provenance.json` material references below `assets/`.

This ensures that service-seeded material such as source email messages
is included even when it is not exposed as a workspace input file.

## Build

```bash
PYTHONPATH=src:legal/src \
python -m fanno_work task build \
  --task legal/tasks/imported/harvey-lab/litigation-invoice-review/v1/task.json \
  --environment legal/environments/legal-office/v1/environment.container.json
```

The default output is:

```text
build/tasks/<task-id>/canonical/
```

Use `--evaluator` to provide an explicit evaluator specification. When it
is omitted, the current task `verifier` declaration is normalized into an
`evaluator.json`. Such synthesized evaluators retain their declared status
and must not be represented as trusted semantic reward implementations.

`--force` replaces only an empty directory or an existing generated bundle
with the same `bundle_id`. It refuses to delete arbitrary directories.

## Validate

```bash
PYTHONPATH=src:legal/src \
python -m fanno_work task validate \
  --bundle build/tasks/<task-id>/canonical
```

Validation checks:

- required files and supported manifest version;
- path confinement and absence of symlinked bundle files;
- SHA-256 and size of every declared file;
- rejection of undeclared extra files;
- deterministic bundle digest;
- Task and Environment identity compatibility;
- Action Catalog uniqueness and enabled Action coverage;
- evaluator identity and adapter;
- declared task inputs in the asset inventory.

The generated bundle is the input to the future Reward Engine and
`HarborTaskCompiler`.

## Compile for Harbor

After building the canonical bundle, compile the generated Harbor package:

```bash
PYTHONPATH=src:legal/src \
python -m fanno_work.integrations.harbor compile \
  --bundle build/tasks/legal.harvey-lab.litigation-invoice-review.v1/canonical \
  --output build/tasks/legal.harvey-lab.litigation-invoice-review.v1/harbor
```

Both directories below `build/` are generated outputs. Rebuild them from the
authoring task, environment profile, evaluator, and assets instead of editing
their contents by hand.
