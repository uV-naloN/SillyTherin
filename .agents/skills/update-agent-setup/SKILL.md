---
name: update-agent-setup
description: Compare this project's Codex, OpenCode, and Claude setup against the My-Agent-Setup Claude source, or refresh project-local setup files from it. Use only on an explicit request to check or refresh the setup.
metadata:
  opencode/autoinvoke: false
---

# Update the project agent setup

Use [My-Agent-Setup](https://github.com/uV-naloN/My-Agent-Setup) as the upstream Claude source. Clone its default branch into a temporary directory and record the commit. The initial port used `1ff8b2ee1142018069f231118ca619ce23d0cbce`.

- **Status** (default). Compare upstream `CLAUDE.md`, `settings.json`, and `skills/` with this checkout's `AGENTS.md`, `CLAUDE.md`, `.codex/`, `.opencode/`, `.claude/`, and `.agents/skills/`. Summarize upstream changes and project adaptations separately.
- **Pull**. Review upstream changes, then merge the requested changes into project-local files. Preserve the ResearchAgent shipping and verification rules, native Codex hooks, OpenCode plugin, project-local providers and MCP settings, Claude settings, and user edits. Keep Claude skills discoverable through `.claude/skills/` links to `.agents/skills/`; copy reference files and scripts required by changed skills. Check the result and report the revision and changes.
- **Push**. The upstream repository contains the Claude source. If asked to publish the Codex or OpenCode port, establish distinct upstream paths with the user before publishing. Keep credentials, local MCP secret files, and session data out of the change.
