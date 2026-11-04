# Validation record — reusable edition 1.0.0

Original development folder preserved: `D:\CODING WORKS\ChatGPT\MemberPay`.
All edits and builds were performed in a separate source folder.

Passing checks:

- Existing smoke flow: lock/unlock, add member, payments, reports and receipt.
- Settings integration: password change, demo data, clear, backup, restore,
  legacy import, safety backups, retention and document save hooks.
- Finance integration: monthly allocations, statements, reminder amounts,
  expenses, audit history, legacy restore and multipage PDF generation.
- Four installer regressions: redirected Desktop handling, useful shortcut error
  reporting, preservation of existing records, and worker error reporting without
  worker-thread Tk calls.
- Five group regression tests: setup gating and password hashing; independent
  group databases; joining-month dues, new-member defaults and historical target
  protection; custom-logo backup/restore without credential changes; rejection
  of cross-origin writes.

Tests use synthetic records and temporary databases. Product source does not
ship test databases or a default password. Historical regression fixtures use
explicit synthetic credentials and amounts only in test code.

This record distinguishes source/package checks from physical clean-PC testing.
A clean Windows PC installation and a real group's full accounting acceptance
test are not claimed. This edition supports UGX only and locks some historical
settings rather than implementing effective-dated rate changes.
