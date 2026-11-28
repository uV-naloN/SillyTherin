# SillyTherin design canvas

A consistency pass on the 26 November 2026 export. Artboards reuse the app shell and share the same light and dark design tokens as the canvas viewer. The composer includes the agent mode control, the notebook includes the Knowledge tab, and Settings includes Providers. Chat history starts collapsed outside the history examples and opens from the left rail.

## Contents

- `Page-01-notebook-open.dc.html`
- `Page-02-datasets-tab.dc.html`
- `Page-03-start-closed.dc.html`
- `Page-04-workspace-menu.dc.html`
- `Page-05-new-workspace.dc.html`
- `Page-06-model-picker.dc.html`
- `Page-07-effort-tooltip.dc.html`
- `Page-08-settings-general.dc.html`
- `Page-09-settings-answers.dc.html`
- `Page-10-settings-tools.dc.html`
- `Page-11-settings-account.dc.html`
- `Page-12-settings-about.dc.html`
- `Page-13-chat-answer.dc.html`
- `Page-14-chat-steps-open.dc.html`
- `Proto-24-message-sending.dc.html`
- `Proto-25-message-received.dc.html`
- `Proto-26-message-not-sent.dc.html`
- `Proto-27-mode-menu.dc.html`
- `Proto-28-describe-then-import.dc.html`
- `Proto-29-manual-asks-tool.dc.html`
- `Proto-30-risky-asks-first.dc.html`
- `Proto-31-steer-reply.dc.html`
- `Proto-32-goal-and-checklist.dc.html`
- `Proto-33-checklist-needs-you.dc.html`
- `Proto-34-result-for-review.dc.html`
- `Proto-35-knowledge-sources.dc.html`
- `Proto-36-no-provider-yet.dc.html`
- `Proto-37-settings-providers.dc.html`
- `Proto-38-models-by-provider.dc.html`
- `Proto-40-pick-colab-runtime.dc.html`
- `Proto-41-cell-on-colab.dc.html`
- `Proto-42-free-picks-dataset.dc.html`
- `Proto-43-chat-history-list.dc.html`
- `Proto-44-chat-history-empty.dc.html`
- `Proto-45-chat-history-delete.dc.html`
- `Proto-46-chat-history-hidden.dc.html`
- `Proto-47-chat-history-replying.dc.html`
- `Shell.dc.html`
- `Story-15-slash-menu.dc.html`
- `Story-16-kaggle-list.dc.html`
- `Story-17-kaggle-loading.dc.html`
- `Story-18-kaggle-not-connected.dc.html`
- `Story-19-kaggle-empty.dc.html`
- `Story-20-kaggle-unreachable.dc.html`
- `Story-21-download-states.dc.html`
- `Story-22-large-asks-first.dc.html`
- `Story-23-datasets-tab-kaggle.dc.html`
- `canvas.json` — the canvas: its pages, where each artboard sits, its notes and shapes
- `design-tokens.css` — shared light and dark styling
- `_runtime/` — browser runtime for the exported artboards

## Viewing

Start the local server from this folder:

```sh
python3 -m http.server 18765 --bind 127.0.0.1
```

Open [the design canvas](http://127.0.0.1:18765/). Drag to pan and scroll to zoom. **Fit all** shows the full layout. Zoom in to see the artboard thumbnails. Search the sidebar and click a result to center it.

Double-click an artboard or sidebar result to open its prototype. You can also focus it with the keyboard and press Enter. Choose **Light**, **Dark**, or **Both** for paired designs, or use **Open artboard** to open the exported HTML in a separate tab.

## Agent activity

Artboards 13 and 14 show compact and expanded activity. Open the reply activity to see a timeline, then expand a tool row to inspect its input and result. Artboards 25, 29–34, and 42 show running tools, approvals, a stopped tool, and completed work. Artboard interactions use example data.

## Chat history screens

`Proto-43` to `Proto-47` show the chat history side list (stories CH-1 to CH-6). The panel icon at the top of the left sidebar hides it, and the same icon on the narrow strip shows it again. Open artboard 43 to try the sidebar toggle, title search, and the per-chat menu with sample data. Settings sits at the bottom in both layouts.
