# Representative legal task references from the 180-task sample

This directory contains seven task-specific source snapshots selected from the
180-task Legal Workflow Discovery sample. They are evidence and design inputs
for building `fanno-work/legal`; they are not native Fanno TaskSpecs and are not
registered as runnable tasks.

## Selection objective

The set maximizes workflow and environment-capability coverage while retaining
task-specific source files and materials that are present in the local upstream
checkouts. Selection was based on source files, task specifications, materials,
and checkers—not on task titles alone.

| # | Source | Sample index | Task | Workflow shape | Important capability evidence |
|---|---|---:|---|---|---|
| 1 | Harvey LAB | 7 | Trademark license counter-redline | Compare two agreement versions, apply a playbook, validate financial terms, produce redline and cover note | DOCX editing/comparison, XLSX, PPTX, EML, multi-deliverable generation |
| 2 | Harvey LAB | 9 | Series C cap-table analysis | Reconcile cap table and transaction documents, calculate ownership/dilution, produce analysis memo | XLSX formulas/data, cross-document extraction, financial calculation, DOCX output |
| 3 | Harvey LAB | 30 | Litigation invoice charge review | Test invoice entries against billing rules and correspondence, calculate adjustments | XLSX row analysis, EML, policy application, arithmetic, audit memo |
| 4 | Harvey LAB | 31 | Data-localization expansion review | Synthesize contracts, architecture, audit and jurisdiction guidance into a compliance roadmap | Multi-document evidence synthesis, EML, risk matrix, structured memo |
| 5 | Harvey LAB | 59 | DDRL-to-VDR reconciliation | Compare request list, VDR index and tracker; identify missing or mismatched diligence items | Multiple XLSX files, DOCX, EML, record reconciliation, status output |
| 6 | ClawMark | 102 | Real-estate transaction risk assessment | Stateful, multi-stage transaction review with new events and constrained communications | Files, PDF/image/audio, email, CRM/Notion, sheets, calendar, confidentiality, stage lifecycle |
| 7 | ClawMark | 104 | Commercial-lease LOI comparison | Compare LOIs, incorporate later lawyer/operations evidence, update recommendations | Multi-stage control flow, email, CRM/Notion, sheets, document comparison |

## Layout

- `sources/`: complete task-specific files found in each upstream task
  directory. Shared benchmark runtimes are deliberately not duplicated.
- `annotations/deepseek-v0.1/`: existing model extraction results. These are
  analytical annotations, not source facts or execution specifications.
- `licenses/`: source repository license snapshots.
- `selection_manifest.json`: provenance, hashes, capability rationale and
  known execution gaps.

## Important boundaries

1. No file in this directory is automatically loaded by the Fanno runtime.
2. “Task-specific snapshot complete” means all files under the selected
   upstream task directory were copied. It does not mean the shared upstream
   harness and services were copied.
3. Harvey LAB tasks still require a compatible document sandbox, tool layer and
   evaluator. ClawMark tasks still require its shared service stack and task
   lifecycle unless adapted to Fanno.
4. ClawMark is licensed CC BY-NC 4.0 and therefore carries a non-commercial
   restriction. Consult `licenses/CLAWMARK_LICENSE.txt` before reuse.
5. Do not expose criteria, hidden checks or expected facts to the agent when
   converting a reference task into a native task.
6. A native derivative should use independently authored or clearly licensed
   materials, record provenance, and receive its own independent evaluator.

## Why some sources were not selected

- LegalAgentBench contributes 99 of the 180 sample records, but the locally
  inspected implementation depends on external knowledge interfaces/corpora
  and the repository contains no license file despite an MIT badge in its
  README. It remains useful for capability analysis, but was not copied into
  this redistribution-oriented reference set.
- ALE offers strong evaluator examples, but the selected legal task source
  directories do not contain their staged PDF/text input payloads, and the
  legal tasks appear in ALE's unlicensed list. Copying them here would imply a
  level of material completeness and reuse permission that has not been
  established.

## Recommended adaptation order

1. Invoice review: closest to the current filesystem/document/records slice and
   adds XLSX + EML + deterministic arithmetic checks.
2. Cap-table analysis: adds richer spreadsheet and cross-document calculations.
3. DDRL/VDR reconciliation: adds multi-sheet status reconciliation.
4. Trademark redline: adds genuine DOCX comparison and controlled editing.
5. Real-estate task 4: adds stateful services, staged events, permissions and
   prohibited-side-effect checks.

