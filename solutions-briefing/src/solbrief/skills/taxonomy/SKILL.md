---
name: taxonomy
description: How the problem taxonomy works and the rules for proposing changes. Use when classifying problems or cards, or when a problem doesn't fit any class.
---

# Problem taxonomy

- The current classes live in `src/solbrief/taxonomy/v1.yaml` (frozen) and the `problem_classes` table.
- Class ids are stable slugs. **Never rename, merge, or delete a class.** Use the most specific child class;
  use a parent only if no child fits.
- If nothing fits, do not force it. You may **propose** a new class: write a `taxonomy_changes` row with
  `change_type = 'propose'`, `actor = 'agent'`, a suggested id/label/parent in `after`, and a reason with
  2–3 example items. Only a human can accept it (enforced by a database constraint).
- Seed mapping to the Solutions Journalism Network's Story Tracker issue areas (`external_tag`) is added only
  once SJN grants permission.
