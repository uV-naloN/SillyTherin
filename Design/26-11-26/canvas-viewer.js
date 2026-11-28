const viewport = document.querySelector("#viewport");
const world = document.querySelector("#world");
const boardList = document.querySelector("#board-list");
const search = document.querySelector("#search");
const status = document.querySelector("#load-status");
const preview = document.querySelector("#preview");
const previewStage = document.querySelector("#preview-stage");
const previewClip = document.querySelector("#preview-clip");
const themeControls = document.querySelector("#theme-controls");

const groups = [
  { label: "App screens", matches: (board) => /^(Page-|Shell\.)/.test(board.file) },
  { label: "Kaggle stories", matches: (board) => board.file.startsWith("Story-") },
  { label: "Prototypes", matches: (board) => board.file.startsWith("Proto-") },
];
const themeWidth = 1440;
const themeGap = 80;
const camera = { x: 0, y: 0, scale: 1 };
const boards = [];
let bounds;
let selected;
let drag;
let didDrag = false;
let previewFrame;
let previewTheme;

function createFrame(board) {
  const frame = document.createElement("iframe");
  frame.title = board.title;
  frame.src = board.file;
  return frame;
}

function updateBoardPreviews() {
  const overscan = 160;
  for (const board of boards) {
    const left = camera.x + board.x * camera.scale;
    const top = camera.y + board.y * camera.scale;
    const right = left + board.w * camera.scale;
    const bottom = top + board.h * camera.scale;
    const isNearViewport = right > -overscan && bottom > -overscan
      && left < viewport.clientWidth + overscan && top < viewport.clientHeight + overscan;
    const shouldRender = isNearViewport && board.w * camera.scale >= 120;
    if (shouldRender && !board.frame) {
      board.frame = createFrame(board);
      board.frame.tabIndex = -1;
      board.frame.setAttribute("aria-hidden", "true");
      board.element.prepend(board.frame);
    } else if (!shouldRender && board.frame) {
      board.frame.remove();
      board.frame = undefined;
    }
  }
}

function renderCamera() {
  world.style.transform = `translate(${camera.x}px, ${camera.y}px) scale(${camera.scale})`;
  document.querySelector("#zoom-level").value = `${Math.round(camera.scale * 100)}%`;
  updateBoardPreviews();
}

function fitCanvas(mode) {
  const padding = 30;
  const width = Math.max(1, viewport.clientWidth - padding * 2);
  const height = Math.max(1, viewport.clientHeight - padding * 2);
  const scale = width / bounds.width;
  camera.scale = mode === "all" ? Math.min(scale, height / bounds.height) : scale;
  camera.x = (viewport.clientWidth - bounds.width * camera.scale) / 2 - bounds.left * camera.scale;
  camera.y = padding - bounds.top * camera.scale;
  renderCamera();
}

function zoomAt(factor, x = viewport.clientWidth / 2, y = viewport.clientHeight / 2) {
  if (!bounds) return;
  const scale = Math.min(2, Math.max(0.004, camera.scale * factor));
  const ratio = scale / camera.scale;
  camera.x = x - (x - camera.x) * ratio;
  camera.y = y - (y - camera.y) * ratio;
  camera.scale = scale;
  renderCamera();
}

function selectBoard(board) {
  selected?.element.classList.remove("selected");
  selected?.link.removeAttribute("aria-current");
  selected = board;
  board.element.classList.add("selected");
  board.link.setAttribute("aria-current", "true");
}

function focusBoard(board) {
  selectBoard(board);
  camera.scale = Math.min(1, Math.max(0.004, (viewport.clientWidth - 60) / board.w),
    Math.max(0.004, (viewport.clientHeight - 90) / board.h));
  camera.x = (viewport.clientWidth - board.w * camera.scale) / 2 - board.x * camera.scale;
  camera.y = (viewport.clientHeight - board.h * camera.scale) / 2 - board.y * camera.scale;
  renderCamera();
}

function renderArtboard(board) {
  const element = document.createElement("article");
  element.className = "artboard";
  Object.assign(element.style, {
    left: `${board.x}px`, top: `${board.y}px`, width: `${board.w}px`, height: `${board.h}px`,
  });
  const label = document.createElement("div");
  label.className = "artboard-label";
  label.textContent = board.title;
  const hit = document.createElement("button");
  hit.className = "artboard-hit";
  hit.setAttribute("aria-label", `Preview ${board.title}`);
  hit.title = board.title;
  hit.addEventListener("click", (event) => {
    if (event.detail === 0) openPreview(board);
    else if (!didDrag) selectBoard(board);
  });
  hit.addEventListener("dblclick", () => {
    if (!didDrag) openPreview(board);
  });
  element.append(label, hit);
  world.append(element);
  board.element = element;
}

function renderAnnotation(annotation) {
  const label = document.createElement("div");
  label.className = "annotation";
  label.textContent = annotation.text;
  Object.assign(label.style, {
    left: `${annotation.x}px`,
    top: `${annotation.y}px`,
    width: `${annotation.maxW ?? annotation.w}px`,
  });
  world.append(label);
}

function renderNavigation() {
  for (const group of groups) {
    const section = document.createElement("section");
    section.className = "board-group";
    const heading = document.createElement("h3");
    heading.textContent = group.label;
    section.append(heading);
    group.boards = boards.filter(group.matches);
    group.element = section;
    for (const board of group.boards) {
      const link = document.createElement("button");
      const number = document.createElement("span");
      number.className = "board-number";
      number.textContent = board.file.match(/-(\d+)-/)?.[1] ?? "—";
      const title = document.createElement("span");
      title.textContent = board.title.replace(/^\d+ · /, "");
      link.className = "board-link";
      link.append(number, title);
      link.addEventListener("click", (event) => {
        if (event.detail === 0) openPreview(board);
        else focusBoard(board);
      });
      link.addEventListener("dblclick", () => openPreview(board));
      section.append(link);
      board.link = link;
    }
    boardList.append(section);
  }
}

function filterNavigation() {
  const query = search.value.trim().toLowerCase();
  let matches = 0;
  for (const group of groups) {
    for (const board of group.boards ?? []) {
      board.link.hidden = !`${board.title} ${board.file}`.toLowerCase().includes(query);
      if (!board.link.hidden) matches++;
    }
    if (group.element) group.element.hidden = group.boards.every((board) => board.link.hidden);
  }
  document.querySelector("#board-count").textContent = query ? `${matches} of ${boards.length}` : boards.length;
}

function fitPreview() {
  if (!preview.open || !previewFrame) return;
  const visibleWidth = previewTheme === "both" ? selected.w : themeWidth;
  const offset = previewTheme === "dark" ? themeWidth + themeGap : 0;
  const scale = Math.min(1, Math.max(1, previewStage.clientWidth - 24) / visibleWidth,
    Math.max(1, previewStage.clientHeight - 24) / selected.h);
  previewClip.style.width = `${visibleWidth * scale}px`;
  previewClip.style.height = `${selected.h * scale}px`;
  previewFrame.style.transform = `translateX(${-offset * scale}px) scale(${scale})`;
}

function setPreviewTheme(theme) {
  previewTheme = theme;
  for (const button of themeControls.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.theme === theme));
  }
  fitPreview();
}

function openPreview(board) {
  selectBoard(board);
  document.querySelector("#preview-title").textContent = board.title;
  document.querySelector("#open-original").href = board.file;
  previewFrame = createFrame(board);
  previewFrame.width = board.w;
  previewFrame.height = board.h;
  previewFrame.addEventListener("load", () => {
    previewFrame?.contentDocument?.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !event.defaultPrevented) preview.close();
    });
  });
  previewClip.replaceChildren(previewFrame);
  const hasThemePair = board.w === 2 * themeWidth + themeGap;
  themeControls.hidden = !hasThemePair;
  if (!preview.open) preview.showModal();
  setPreviewTheme(hasThemePair ? "light" : "both");
}

viewport.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 && event.button !== 1) return;
  didDrag = false;
  drag = { id: event.pointerId, x: event.clientX, y: event.clientY, originX: camera.x, originY: camera.y };
});

viewport.addEventListener("pointermove", (event) => {
  if (drag?.id !== event.pointerId) return;
  const x = event.clientX - drag.x;
  const y = event.clientY - drag.y;
  if (!didDrag && Math.hypot(x, y) < 4) return;
  if (!didDrag) viewport.setPointerCapture(event.pointerId);
  didDrag = true;
  viewport.classList.add("dragging");
  camera.x = drag.originX + x;
  camera.y = drag.originY + y;
  renderCamera();
});

for (const type of ["pointerup", "pointercancel", "lostpointercapture", "pointerleave"]) {
  viewport.addEventListener(type, (event) => {
    if (event.pointerId !== drag?.id) return;
    if (type === "pointerleave" && viewport.hasPointerCapture(event.pointerId)) return;
    drag = undefined;
    viewport.classList.remove("dragging");
  });
}

viewport.addEventListener("wheel", (event) => {
  event.preventDefault();
  const position = viewport.getBoundingClientRect();
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : 1);
  zoomAt(Math.exp(-delta * 0.0025), event.clientX - position.left, event.clientY - position.top);
}, { passive: false });

viewport.addEventListener("keydown", (event) => {
  const directions = { ArrowUp: [0, 80], ArrowDown: [0, -80], ArrowLeft: [80, 0], ArrowRight: [-80, 0] };
  const direction = directions[event.key];
  if (!direction) return;
  event.preventDefault();
  camera.x += direction[0];
  camera.y += direction[1];
  renderCamera();
});

document.querySelector("#zoom-out").addEventListener("click", () => zoomAt(1 / 1.25));
document.querySelector("#zoom-in").addEventListener("click", () => zoomAt(1.25));
document.querySelector("#fit-width").addEventListener("click", () => bounds && fitCanvas("width"));
document.querySelector("#fit-all").addEventListener("click", () => bounds && fitCanvas("all"));
document.querySelector("#close-preview").addEventListener("click", () => preview.close());
preview.addEventListener("close", () => {
  previewFrame?.remove();
  previewFrame = undefined;
});
themeControls.addEventListener("click", (event) => {
  if (event.target.dataset.theme) setPreviewTheme(event.target.dataset.theme);
});
search.addEventListener("input", filterNavigation);
new ResizeObserver(fitPreview).observe(previewStage);
new ResizeObserver(updateBoardPreviews).observe(viewport);

try {
  const response = await fetch("./canvas.json");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const { artboards, annotations = [] } = await response.json();
  boards.push(...artboards);
  const left = Math.min(...boards.map(({ x }) => x), ...annotations.map(({ x }) => x));
  const top = Math.min(...boards.map(({ y }) => y), ...annotations.map(({ y }) => y));
  const right = Math.max(...boards.map(({ x, w }) => x + w));
  const bottom = Math.max(...boards.map(({ y, h }) => y + h));
  bounds = { left, top, width: right - left, height: bottom - top };
  renderNavigation();
  annotations.forEach(renderAnnotation);
  boards.forEach(renderArtboard);
  filterNavigation();
  fitCanvas("width");
  status.hidden = true;
} catch (error) {
  console.error("Canvas could not load", error);
  status.textContent = "Could not load canvas.json. Open this folder through a local web server.";
}
