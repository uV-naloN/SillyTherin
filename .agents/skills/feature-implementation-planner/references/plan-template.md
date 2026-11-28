## Output template

The plan file uses these exact headings so it is easy to scan. Work the steps in their numbered order (each one needs the one before), but write the file in **reading order**: what the user must decide and the shape of the change come straight after the requirement, and the supporting detail follows.

```
# Feature Plan: [Feature name]
Status: Draft — awaiting answers · v1 · Source: [link to story/ticket] · Date: YYYY-MM-DD
## 1. Requirement (User Stories & Acceptance Criteria)      ← Step 1
## 2. Questions & Confirmations                              ← Step 8
## 3. Schema Change                                          ← Step 3c (or "No schema change" and why)
## 4. Workflow Diagram                                       ← Step 6
## 5. Codebase Patterns                                      ← Step 2
## 6. Existing Schema & Relationships                        ← Step 3a, 3b
### 6.1 Existing Tables
### 6.2 Relationships (+ ER diagram)
## 7. Implementation Idea                                    ← Step 4
### 7.1 Approach & Files Affected
### 7.2 Endpoints
### 7.3 DTOs
## 8. Security Review                                        ← Step 5
## 9. TDD Test Plan                                          ← Step 7
### Functional groups
### Security groups
## Decision log            (added once the user answers: | D# | Question | Answer | What changed in the plan |)
## Scaffold report & verification   (Steps 9–10; link to <ID>-scaffold-report.md, added after the scaffold)
```

Step 9 (after approval) uses its own report format, "Scaffold report" in `steps-9-10-scaffold.md`, and is always followed by the Step 10 checkpoint in the same response. Both go into the separate scaffold report file; the plan only links to it.
