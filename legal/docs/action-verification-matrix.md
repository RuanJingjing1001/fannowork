# Legal Office Action Verification Matrix

This register describes implementation-level verification. It does not claim
that a professional legal task has been completed correctly.

The machine-checked source of truth is
`legal/docs/action-verification-matrix.json`.

## Verification dimensions

| Dimension | Meaning |
|---|---|
| Contract | The Action ID, arguments, effect and error contract are declared. |
| Backend | A real local or container backend executes the operation. |
| Observation | Tests inspect the Agent-visible result. |
| Independent check | A second read path checks the backend or persisted result. |
| Permission | At least one relevant authorization boundary is tested. |
| Failure | Important invalid input or backend failure behavior is tested. |
| Reset | Persistent changes are restored to the prepared state. |

`core_verified` means the principal success path, real backend and state
semantics are verified. A `partial` dimension remains explicit and must not be
interpreted as exhaustive error coverage.

## Current summary

| Capability | Actions | Current result | Main remaining gap |
|---|---:|---|---|
| Filesystem | 3 | Core verified | Broader list failure cases |
| Document extraction/conversion | 3 | Core verified | Scanned PDF/OCR is outside scope |
| Writer/UNO | 5 | Core verified | Crash recovery and concurrency hardening deferred |
| Spreadsheet/Calc | 3 | Core read Actions verified | No update/write Actions yet |
| Email/GreenMail | 4 | Core verified | Folder/flag/reply Actions are not yet exposed |
| Calendar/Radicale | 5 | Core CalDAV lifecycle implemented | Calendar-level ACLs and update-event Action remain partial |
| Records/SQLite | 3 | Core verified | No create Action until a real task requires it |

## Rules

1. Every declared Action must have exactly one matrix entry.
2. Every matrix entry must reference an existing test file.
3. Read-only Actions use `reset: not_applicable` and must demonstrate that
   state does not change.
4. Mutating Actions require an independent state check and reset evidence.
5. A task-level Checker and Reward are separate from this register.
