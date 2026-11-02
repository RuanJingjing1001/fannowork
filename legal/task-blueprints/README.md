# Legal task blueprints

Task blueprints are review documents that translate upstream benchmark evidence
into implementation requirements for Fanno. They are not agent-visible
TaskSpecs, Environment Profiles, or evaluator configurations.

The intended flow is:

```text
upstream task and materials
        -> task blueprint
        -> native/imported TaskSpec
        -> Environment Profile and Action Catalog
        -> backend adapters
        -> hidden evaluator and Reward Engine
```

## Rules

1. A blueprint must cite source evidence for every `source_confirmed` claim.
2. Model annotations are analysis aids and never become source facts by
   themselves.
3. Execution order and dependencies inferred from prose must be marked
   `rule_inferred` or `design_decision`.
4. Agent-visible requirements and hidden evaluation facts must remain separate.
5. Blueprint files must never be mounted into an agent workspace because they
   may contain hidden checks and expected facts.
6. A blueprint does not authorize reuse of source materials; license and
   provenance must be checked independently.

## Provenance values

- `source_confirmed`: explicitly supported by upstream task text, material, or
  checker.
- `implementation_confirmed`: verified against the current Fanno code/runtime.
- `design_decision`: deliberately chosen for the Fanno adaptation.
- `rule_inferred`: inferred from explicit rules or relationships but not stated
  directly by the source.
- `unverified`: not yet established.

## Lifecycle

Use the following blueprint statuses:

```text
draft -> source_audited -> implementation_ready -> implemented -> validated
```

Copy `TEMPLATE.yaml`, complete the source audit, and validate it against
`schema/task-blueprint.schema.json` before implementing a new task.

