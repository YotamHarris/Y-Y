# Decisions — Process

Append-only. The **Status:** line under each heading says which entry holds.

---

### D1 Agent Studio manages mobile development

**Status:** active

Replace the previous board and Discord service with the native Agent Studio board
and manager at the commit recorded in studio/UPSTREAM.md. Keep the iOS signing,
upload, processing, internal tester assignment and readiness workflow intact.
The YYEngine adapter enforces game scope, performs trusted validation, serializes
publication and tracks exact-commit TestFlight delivery. Managed providers commit
and return; the supervisor publishes. Retain the existing developer allowlist.
The board runs locally without the retired public tunnel. Real iPhone acceptance
remains separate from desktop and simulator measurements.
