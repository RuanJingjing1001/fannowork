# Third-party notices

## Radicale and Python CalDAV

The legal-office calendar backend interoperates with the Radicale CalDAV
server through the Python `caldav` client library. Those projects remain
separate third-party components and retain their respective licenses. The
Fanno Action contracts, adapters, lifecycle code, snapshots and tests in this
repository are project-owned code.

## ClawMark architecture reference

ClawMark's use of a per-run Radicale service, CalDAV-based calendar state and
seed/reset lifecycle was reviewed as an architecture reference. Fanno does not
import ClawMark modules or use ClawMark filesystem paths at runtime. ClawMark
is distributed under CC BY-NC 4.0; see the vendored upstream repository for
its full license and attribution.
