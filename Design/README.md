# SillyTherin design

Editable design pages for the SillyTherin app, including designs for the [Project-created datasets epic](../docs/tasks/done/02-project-created-datasets/README.md). Every screen is drawn twice, light on the left and dark on the right.

The files are a **Design canvas**: each `.dc.html` file is one board, and `project/canvas.json` places the boards on the canvas. They can be loaded back into a claude.ai Design artifact at any time (see [Load into an artifact](#load-into-an-artifact)).

## Folder layout

```
Design/
├── README.md                     this file
└── project/
    ├── canvas.json               board positions, titles, order and section titles
    ├── Shell.dc.html             the one shared app page: every screen is drawn here
    ├── Page-01 … Page-14.dc.html the app as it runs today (one board per screen)
    └── Story-15 … Story-23.dc.html the leftover stories PCD-11, PCD-12, PCD-13
```

Every `Page-*` and `Story-*` file is tiny: it mounts `Shell.dc.html` twice with `<dc-import name="Shell" theme="light|dark" screen="sNN">`. **To change how something looks, edit `Shell.dc.html` once** and every board updates.

## Screens

The `screen` prop picks what `Shell.dc.html` draws. All screens are 1440 × 900.

### The app today

| Screen | Board file | What the user sees |
|---|---|---|
| s01 | `Page-01-notebook-open` | Start screen in the chat, notebook panel open on the Notebook tab with one empty code cell |
| s02 | `Page-02-datasets-tab` | Notebook panel on the Datasets tab, empty: "No datasets in this project yet." |
| s03 | `Page-03-start-closed` | Start screen, notebook closed: logo, "Ask anything, or paste a dataset link to get started.", message box |
| s04 | `Page-04-workspace-menu` | Workspace menu open from the header: "Default" and "New workspace…" |
| s05 | `Page-05-new-workspace` | Workspace menu with the "Workspace name" field and a Create button |
| s06 | `Page-06-model-picker` | Model picker with "Search 476 models" and the model list |
| s07 | `Page-07-effort-tooltip` | Tooltip "Effort: Model default" above the effort dial |
| s08 | `Page-08-settings-general` | Settings → General: Theme (Light / Dark), Notebook panel (Ctrl+\) |
| s09 | `Page-09-settings-answers` | Settings → Answers: Show thinking |
| s10 | `Page-10-settings-tools` | Settings → Tools: Web search, Notebook, Risky code "Always asks" |
| s11 | `Page-11-settings-account` | Settings → Account: Kaggle account "Not connected" with Connect |
| s12 | `Page-12-settings-about` | Settings → About: Version, Runs on 127.0.0.1, Models from OpenRouter |
| s13 | `Page-13-chat-answer` | A chat: user message, snake mark beside the reply, "1 step · 1s", answer, Copy answer |
| s14 | `Page-14-chat-steps-open` | Same chat with the steps opened to show the model's "Thinking" |

These were matched to screenshots of the running app (`frontend/dist`) and to the colour tokens in `frontend/src/styles/theme.css`.

### Leftover stories (not built yet)

| Screen | Board file | Story | What the user sees |
|---|---|---|---|
| s15 | `Story-15-slash-menu` | PCD-11 | Typing `/` in an empty message box opens a "Commands" menu above it with **/kaggle** and its description; keys ↑ ↓, Enter/Tab, Esc |
| s16 | `Story-16-kaggle-list` | PCD-12 + PCD-13 | "My Kaggle datasets" window: search box, one row per dataset (title, owner/name, size, licence, updated) and a **Download** button; a dataset already in the project shows "In this project" |
| s17 | `Story-17-kaggle-loading` | PCD-12 | "Loading your Kaggle datasets…" with placeholder rows |
| s18 | `Story-18-kaggle-not-connected` | PCD-12 | "Connect your Kaggle account to see your datasets" with **Connect** |
| s19 | `Story-19-kaggle-empty` | PCD-12 | "You have no datasets on Kaggle yet." |
| s20 | `Story-20-kaggle-unreachable` | PCD-12 | "Couldn't reach Kaggle. Try again." with **Try again** |
| s21 | `Story-21-download-states` | PCD-13 | Rows mid-download: "Downloading… 42%", "In this project", and a failed row with a red reason and Download again |
| s22 | `Story-22-large-asks-first` | PCD-13 | A 1.2 GB dataset asks "This dataset is 1.2 GB. Download it into this project?" with Download / Skip before anything downloads |
| s23 | `Story-23-datasets-tab-kaggle` | PCD-13 | Datasets tab with "Kaggle" cards: downloading (progress, 20 of 48 MB), one opened with details, full path, files and actions, one ready, one failed with Try again |

Not drawn: PCD-12's "Kaggle signed you out. Connect again." (same window as s20 with a Connect button).

Design choices to confirm before building:
- The dataset list and the Download button share one window (PCD-12 and PCD-13 together).
- The large-dataset prompt (s22) sits inside the window, styled like the app's existing Download / Skip prompt card; its title "Large dataset, check first" is new wording.
- Dataset names, sizes, dates, "your-name" and the "Traffic study" workspace are sample data.

## Design tokens

Defined in `TOKENS` at the bottom of `Shell.dc.html` and applied as CSS variables on the page root.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--bg` | `#ffffff` | `#09090b` | page and card background |
| `--fg` | `#09090b` | `#fafafa` | main text |
| `--card` | `#ffffff` | `#0e0e11` | menus and popovers |
| `--muted` | `#f4f4f5` | `#27272a` | chips, selected rows, user bubble |
| `--mfg` | `#5d5d65` | `#a4a4ad` | secondary text and icons |
| `--border` | `#e4e4e7` | `#27272a` | borders and dividers |
| `--primary` | `#1975d2` | `#42a5f5` | send button, links, progress, switches |
| `--pfg` | `#ffffff` | `#09090b` | text on primary |
| `--panel` | `#fcfcfc` | `#0d0d10` | notebook panel |
| `--nav` | `#fafafa` | `#17171a` | Settings side nav, table headers, path box |
| `--logo` | `#14213D` | `#ffffff` | "SillyTherin" lettering in the logo |
| `--ok` | `#15803d` | `#4ade80` | "In this project" |
| `--err` | `#b91c1c` | `#f87171` | failed downloads and errors |
| `--tint` / `--tintb` | primary at 10% / 30% | primary at 14% / 35% | badges, icon tiles, prompt card |

Type: Inter (400, 500, 600) for the interface, JetBrains Mono for code, paths and the `/kaggle` command. Icons are inline lucide SVGs, the same set the app uses.

## Load into an artifact

From Claude Code, publish the folder as a Design canvas:

1. Create a canvas from the Design artifact type (or reuse an existing one by its URL).
2. Publish with `root` set to this `Design` folder, `file_path` set to `Design/project/canvas.json`, and every other file under `project/` in `files` (for example `"project/Shell.dc.html": "project/Shell.dc.html"`).

The current published copy is https://claude.ai/artifact/FHJtaa1kYhm5ircieTXLmF (private to its owner).

## Other copies

- **Penpot:** the 14 app screens in light and dark (28 boards) are on the page "SillyTherin screens" of the local Penpot file "New File 1" (`http://localhost:9001`). The story screens s15–s23 are not in Penpot yet.
- **Reference screenshots:** the screenshots of the running app that these pages were matched against are stored as assets on the published artifact; they are not copied into this folder.
