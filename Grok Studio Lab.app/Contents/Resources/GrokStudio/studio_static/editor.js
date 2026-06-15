const params = new URLSearchParams(window.location.search);
const itemId = params.get("item_id") || "";
const sourceUrl = params.get("source") || "";
const imageName = params.get("name") || "Image";

const els = {
  editor: document.querySelector("#imageEditor"),
  font: document.querySelector("#fontSelect"),
  fontControl: document.querySelector(".font-control"),
  featherControl: document.querySelector(".feather-control"),
  featherRange: document.querySelector("#featherRange"),
  featherValue: document.querySelector("#featherValue"),
  cropRatios: document.querySelector(".crop-ratios"),
  save: document.querySelector("#saveEditorBtn"),
  close: document.querySelector("#closeEditorBtn"),
  status: document.querySelector("#editorStatus"),
  ratios: Array.from(document.querySelectorAll("[data-ratio]")),
};

let imageEditor = null;
let selectedTextId = null;
let selectedShapeId = null;
let selectedObjectId = null;
let selectedObjectMenu = "";
let pendingFont = "";
let originalAspect = 1;
let wheelZoomLevel = 1;
let lastWheelZoomAt = 0;
let handMode = false;
let handDragging = false;
let handLastPoint = null;
let selectionMode = "";
let selectionShape = "rect";
let selectionInverse = false;
let selectionFeather = 0;
let selectionStart = null;
let selectionGuide = null;
let selectionInverseGuide = null;
let selectionFilterBaseline = null;
let pixelClipboard = null;
let movedSelectionId = null;
let transformAngle = 0;
let eraserMode = false;
let eraserSoft = false;
let eraserSize = 28;
let eraserOpacity = 100;
let pendingIconKind = "fill";
let pendingIconName = "";
let selectedIconId = null;
let editorUserChoseMenu = false;
const customPanels = {};
const customIconPaths = {
  bubbleTailCenter: "M2.25 12.76c0 1.6 1.123 2.994 2.707 3.227 1.068.157 2.148.279 3.238.364.466.037.893.281 1.153.671L12 21l2.652-3.978c.26-.39.687-.634 1.153-.67 1.09-.086 2.17-.208 3.238-.365 1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z",
  bubbleTailLeft: "M2.25 12.76c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.076-4.076a1.526 1.526 0 0 1 1.037-.443 48.282 48.282 0 0 0 5.68-.494c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z",
  bubbleRound: "M12 20.25c4.97 0 9-3.694 9-8.25s-4.03-8.25-9-8.25S3 7.444 3 12c0 2.104.859 4.023 2.273 5.48.432.447.74 1.04.586 1.641a4.483 4.483 0 0 1-.923 1.785A5.969 5.969 0 0 0 6 21c1.282 0 2.47-.402 3.445-1.087.81.22 1.668.337 2.555.337Z",
};
const builtInMenus = ["crop", "draw", "shape", "icon", "text", "filter"];
const customMenus = ["selection", "transform", "eraser"];
const opacityControls = new Map();
const objectOpacityMenus = new Map();
const opacityValues = {
  draw: 100,
  shape: 100,
  icon: 100,
  text: 100,
};
const virtualRangeSteps = [
  [".tie-rotate-range", 1 / 720],
  [".tie-draw-range", 1 / 25],
  [".tie-stroke-range", 1 / 298],
  [".tie-text-range", 1 / 90],
  [".tie-removewhite-distance-range", 0.1],
  [".tie-brightness-range", 0.05],
  [".tie-noise-range", 0.01],
  [".tie-pixelate-range", 1 / 18],
  [".tie-colorfilter-threshold-range", 0.1],
  ["#tie-filter-tint-opacity", 0.1],
];
const virtualInputRangeSettings = [
  [".tie-rotate-range", -360, 360, 1],
  [".tie-draw-range", 5, 30, 1],
  [".tie-stroke-range", 2, 300, 1],
  [".tie-text-range", 10, 100, 1],
];

function setStatus(message, kind = "") {
  els.status.textContent = message || "";
  els.status.classList.toggle("error", kind === "error");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

function blackTheme() {
  return {
    "common.backgroundColor": "#08090a",
    "common.border": "0px",
    "common.bi.image": "",
    "common.bi.display": "none",
    "menu.backgroundColor": "#111315",
    "menu.normalIcon.color": "#9ca4a8",
    "menu.activeIcon.color": "#fbad2e",
    "menu.disabledIcon.color": "#4f565a",
    "menu.hoverIcon.color": "#ffffff",
    "submenu.backgroundColor": "#171a1d",
    "submenu.normalIcon.color": "#a9b1b5",
    "submenu.activeIcon.color": "#fbad2e",
    "submenu.disabledIcon.color": "#4f565a",
    "submenu.hoverIcon.color": "#ffffff",
  };
}

function makeOpacityControl(menuName) {
  const host = document.createElement("li");
  const control = document.createElement("div");
  const label = document.createElement("span");
  const inputs = document.createElement("span");
  const range = document.createElement("input");
  const value = document.createElement("input");

  host.className = "custom-opacity-options tui-image-editor-newline";
  control.className = "custom-range-control opacity-control";
  label.className = "custom-range-label";
  label.textContent = "Opacity";
  inputs.className = "custom-range-inputs";
  range.type = "range";
  range.min = "0";
  range.max = "100";
  range.step = "1";
  range.value = "100";
  range.setAttribute("aria-label", `${menuName} opacity`);
  value.type = "number";
  value.min = "0";
  value.max = "100";
  value.step = "1";
  value.value = "100";
  value.setAttribute("aria-label", `${menuName} opacity value`);
  inputs.append(range, value);
  control.append(label, inputs);
  host.appendChild(control);
  opacityControls.set(menuName, { host, range, value });
  return host;
}

function syncNativeRangeFill(range) {
  if (!range) return;
  const min = Number(range.min) || 0;
  const max = Number(range.max) || 100;
  const value = Number(range.value) || 0;
  const percent = max === min ? 0 : ((value - min) / (max - min)) * 100;
  range.style.setProperty("--range-fill", `${Math.min(100, Math.max(0, percent))}%`);
}

function normalizeEditorLabels() {
  document.querySelectorAll(".tui-image-editor-menu-text label.range").forEach((label) => {
    if (label.textContent.trim().toLowerCase() === "text size") label.textContent = "Text Size";
  });
  ["icon", "text"].forEach((menuName) => {
    document.querySelectorAll(`.tui-image-editor-menu-${menuName} label`).forEach((label) => {
      if (label.textContent.trim().toLowerCase() === "color") label.textContent = "Fill";
    });
  });
}

function installCustomOptionPanels() {
  const cropList = document.querySelector(".tui-image-editor-menu-crop .tui-image-editor-submenu-item");
  const textList = document.querySelector(".tui-image-editor-menu-text .tui-image-editor-submenu-item");
  const shapeList = document.querySelector(".tui-image-editor-menu-shape .tui-image-editor-submenu-item");
  if (cropList && els.cropRatios) {
    const host = document.createElement("li");
    host.className = "custom-crop-options";
    host.appendChild(els.cropRatios);
    cropList.insertBefore(host, cropList.firstElementChild);
  }
  if (textList && els.fontControl) {
    const host = document.createElement("li");
    host.className = "custom-font-options";
    host.appendChild(els.fontControl);
    textList.insertBefore(host, textList.firstElementChild);
  }
  if (shapeList && els.featherControl) {
    const host = document.createElement("li");
    host.className = "custom-feather-options tui-image-editor-newline";
    host.appendChild(els.featherControl);
    shapeList.appendChild(host);
  }
  ["draw", "shape", "icon", "text"].forEach((menuName) => {
    const list = document.querySelector(`.tui-image-editor-menu-${menuName} .tui-image-editor-submenu-item`);
    if (list) list.appendChild(makeOpacityControl(menuName));
  });
}

function setFeatherControl(id = null) {
  selectedShapeId = id;
  const props = id
    ? imageEditor.getObjectProperties(id, ["type", "rx", "ry", "width", "height"])
    : null;
  const enabled = props?.type === "rect";
  const max = enabled
    ? Math.max(1, Math.round(Math.min(Number(props.width) || 0, Number(props.height) || 0) / 2))
    : 100;
  const value = enabled ? Math.round(Number(props.rx || props.ry) || 0) : 0;
  els.featherControl.classList.toggle("is-disabled", !enabled);
  [els.featherRange, els.featherValue].forEach((input) => {
    input.disabled = !enabled;
    input.max = String(max);
    input.value = String(Math.min(value, max));
  });
  syncNativeRangeFill(els.featherRange);
}

function applyFeather(rawValue) {
  if (!selectedShapeId) return;
  const value = Math.max(0, Math.round(Number(rawValue) || 0));
  els.featherRange.value = String(value);
  els.featherValue.value = String(value);
  syncNativeRangeFill(els.featherRange);
  imageEditor.setObjectPropertiesQuietly(selectedShapeId, { rx: value, ry: value });
}

function resetSelectionFilterBaseline() {
  selectionFilterBaseline = null;
}

function activeOpacityMenu() {
  const main = document.querySelector(".tui-image-editor-main");
  return ["draw", "shape", "icon", "text"]
    .find((menuName) => main?.classList.contains(`tui-image-editor-menu-${menuName}`)) || "";
}

function opacityMenuForType(rawType) {
  const type = String(rawType || "").toLowerCase();
  if (type === "path" || type === "line") return "draw";
  if (["rect", "circle", "triangle"].includes(type)) return "shape";
  if (type.includes("text")) return "text";
  if (type === "icon" || type === "path-group") return "icon";
  return "";
}

function setOpacityControl(menuName, rawValue) {
  const control = opacityControls.get(menuName);
  if (!control) return;
  const value = Math.min(100, Math.max(0, Math.round(Number(rawValue) || 0)));
  opacityValues[menuName] = value;
  control.range.value = String(value);
  control.value.value = String(value);
  syncNativeRangeFill(control.range);
}

function syncSelectedOpacity(id, rawType = "") {
  selectedObjectId = id || null;
  selectedObjectMenu = objectOpacityMenus.get(selectedObjectId) || opacityMenuForType(rawType);
  if (!selectedObjectId || !selectedObjectMenu) return;
  window.setTimeout(() => {
    const props = imageEditor.getObjectProperties(selectedObjectId, "opacity");
    if (props) setOpacityControl(selectedObjectMenu, Number(props.opacity) * 100);
  }, 0);
}

function applyOpacity(menuName, rawValue) {
  setOpacityControl(menuName, rawValue);
  if (!selectedObjectId || selectedObjectMenu !== menuName) return;
  imageEditor.setObjectPropertiesQuietly(selectedObjectId, {
    opacity: opacityValues[menuName] / 100,
  });
}

function bindOpacityControls() {
  opacityControls.forEach((control, menuName) => {
    control.range.addEventListener("input", () => applyOpacity(menuName, control.range.value));
    control.value.addEventListener("input", () => applyOpacity(menuName, control.value.value));
    syncNativeRangeFill(control.range);
  });
}

function adjustNativeRange(range, direction) {
  if (!range || range.disabled) return false;
  const min = Number(range.min) || 0;
  const max = Number(range.max) || 100;
  const step = Number(range.step) || 1;
  const value = Number(range.value) || 0;
  const next = Math.min(max, Math.max(min, value + direction * step));
  if (next === value) return false;
  range.value = String(next);
  range.dispatchEvent(new Event("input", { bubbles: true }));
  range.dispatchEvent(new Event("change", { bubbles: true }));
  syncNativeRangeFill(range);
  return true;
}

function virtualRangeStep(range) {
  return virtualRangeSteps.find(([selector]) => range.matches(selector))?.[1] || 0.05;
}

function adjustVirtualRange(range, direction) {
  const pointer = range?.querySelector(".tui-image-editor-virtual-range-pointer");
  if (!pointer || range.classList.contains("tui-image-editor-disabled")) return false;
  const input = range.closest(".tui-image-editor-range-wrap")
    ?.querySelector(".tui-image-editor-range-value");
  const setting = virtualInputRangeSettings.find(([selector]) => range.matches(selector));
  if (input && setting) {
    const [, min, max, step] = setting;
    const value = Number(input.value) || 0;
    const next = Math.min(max, Math.max(min, value + direction * step));
    if (next === value) return false;
    input.value = String(next);
    input.dispatchEvent(new Event("blur"));
    return true;
  }
  const rangeWidth = Math.max(1, range.getBoundingClientRect().width - pointer.getBoundingClientRect().width);
  const delta = direction * Math.max(1.5, rangeWidth * virtualRangeStep(range));
  const start = pointer.getBoundingClientRect().left + pointer.getBoundingClientRect().width / 2;
  pointer.dispatchEvent(new MouseEvent("mousedown", {
    bubbles: true,
    buttons: 1,
    clientX: start,
    screenX: start,
  }));
  document.dispatchEvent(new MouseEvent("mousemove", {
    bubbles: true,
    buttons: 1,
    clientX: start + delta,
    screenX: start + delta,
  }));
  document.dispatchEvent(new MouseEvent("mouseup", {
    bubbles: true,
    clientX: start + delta,
    screenX: start + delta,
  }));
  return true;
}

function bindWheelRangeControls() {
  const allowedMenus = [
    ".tui-image-editor-menu-rotate",
    ".tui-image-editor-menu-transform",
    ".tui-image-editor-menu-selection",
    ".tui-image-editor-menu-eraser",
    ".tui-image-editor-menu-draw",
    ".tui-image-editor-menu-shape",
    ".tui-image-editor-menu-icon",
    ".tui-image-editor-menu-text",
    ".tui-image-editor-menu-filter",
  ].join(", ");
  els.editor.addEventListener("wheel", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest(allowedMenus)) return;
    const direction = event.deltaY < 0 ? 1 : -1;
    const customInputs = target.closest(".custom-range-inputs, .feather-inputs");
    const nativeRange = customInputs?.querySelector('input[type="range"]');
    const virtualRange = target.closest(".tui-image-editor-range")
      || target.closest(".tui-image-editor-range-wrap")?.querySelector(".tui-image-editor-range");
    const changed = nativeRange
      ? adjustNativeRange(nativeRange, direction)
      : adjustVirtualRange(virtualRange, direction);
    if (changed) event.preventDefault();
  }, { passive: false });
}

function editorFitSize() {
  return {
    width: Math.max(420, window.innerWidth - 560),
    height: Math.max(500, window.innerHeight - 170),
  };
}

function refreshEditorFit() {
  const graphics = imageEditor?._graphics;
  if (!graphics?.setCssMaxDimension || !graphics?.adjustCanvasDimension) return;
  try {
    graphics.setCssMaxDimension(editorFitSize());
    graphics.adjustCanvasDimension();
  } catch (error) {
    // TUI may still be settling while the image loads; the next scheduled pass will retry.
  }
}

function scheduleEditorLayoutRefresh(delay = 0) {
  window.setTimeout(() => {
    refreshEditorFit();
    updateActiveSubmenuLayout();
  }, delay);
}

function editorCanvas() {
  return imageEditor?._graphics?.getCanvas?.() || null;
}

function makeCustomRange(labelText, min, max, value, onInput) {
  const control = document.createElement("div");
  const label = document.createElement("span");
  const inputs = document.createElement("span");
  const range = document.createElement("input");
  const number = document.createElement("input");
  control.className = "custom-range-control";
  label.className = "custom-range-label";
  label.textContent = labelText;
  inputs.className = "custom-range-inputs";
  range.type = "range";
  range.min = String(min);
  range.max = String(max);
  range.step = "1";
  range.value = String(value);
  number.type = "number";
  number.min = String(min);
  number.max = String(max);
  number.step = "1";
  number.value = String(value);
  const update = (raw) => {
    const next = Math.min(max, Math.max(min, Math.round(Number(raw) || 0)));
    range.value = String(next);
    number.value = String(next);
    syncNativeRangeFill(range);
    onInput(next);
  };
  range.addEventListener("input", () => update(range.value));
  number.addEventListener("input", () => update(number.value));
  inputs.append(range, number);
  control.append(label, inputs);
  syncNativeRangeFill(range);
  return { control, range, number, update };
}

function makeColorOption(labelText, value, onInput) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  const name = document.createElement("span");
  label.className = "custom-color-option";
  input.type = "color";
  input.value = value;
  name.textContent = labelText;
  input.addEventListener("input", () => onInput(input.value));
  label.append(input, name);
  return { label, input };
}

function setOptionDisabled(option, disabled) {
  option.label.classList.toggle("is-disabled", disabled);
  option.input.disabled = disabled;
}

function makeToolPanel(name) {
  const submenu = document.querySelector(".tui-image-editor-submenu");
  if (!submenu) return document.createElement("ul");
  const panel = document.createElement("div");
  const list = document.createElement("ul");
  panel.className = `tui-image-editor-menu-${name} custom-tool-panel custom-tool-panel-${name}`;
  panel.dataset.toolPanel = name;
  list.className = "tui-image-editor-submenu-item custom-tool-options";
  panel.appendChild(list);
  const style = submenu.querySelector(".tui-image-editor-submenu-style");
  submenu.insertBefore(panel, style || null);
  customPanels[name] = panel;
  return list;
}

function makeOptionDivider() {
  const divider = document.createElement("div");
  divider.className = "custom-option-divider";
  return divider;
}

function hideCustomPanels() {
  Object.values(customPanels).forEach((panel) => panel.classList.remove("active"));
  document.querySelectorAll(".custom-main-tool").forEach((button) => button.classList.remove("active"));
}

function clearCustomMenuState() {
  const main = document.querySelector(".tui-image-editor-main");
  customMenus.forEach((name) => {
    main?.classList.remove(`tui-image-editor-menu-${name}`);
  });
  hideCustomPanels();
}

function showCustomPanel(name) {
  clearBuiltInMenuState();
  hideCustomPanels();
  const main = document.querySelector(".tui-image-editor-main");
  main?.classList.add(`tui-image-editor-menu-${name}`);
  customPanels[name]?.classList.add("active");
  document.querySelector(`[data-custom-tool="${name}"]`)?.classList.add("active");
  scheduleEditorLayoutRefresh();
}

function addCustomMainTool(name, label, iconClass, position = "last") {
  const menu = document.querySelector(".tui-image-editor-menu");
  if (!menu) return null;
  const button = document.createElement("li");
  button.className = `tui-image-editor-item normal custom-main-tool ${iconClass}`;
  button.dataset.customTool = name;
  button.setAttribute("tooltip-content", label);
  button.innerHTML = `<button type="button" aria-label="${label}"><span aria-hidden="true"></span></button>`;
  if (position === "first") {
    menu.insertBefore(button, menu.firstElementChild);
  } else if (position === "after-crop") {
    const crop = menu.querySelector(".tie-btn-crop");
    if (crop) crop.after(button);
    else menu.appendChild(button);
  } else if (position === "after-draw") {
    const draw = menu.querySelector(".tie-btn-draw");
    if (draw) draw.after(button);
    else menu.appendChild(button);
  } else {
    menu.appendChild(button);
  }
  return button;
}

function installSubmenuTitles() {
  const labels = {
    selection: "Selection",
    crop: "Crop",
    transform: "Transform",
    draw: "Draw",
    eraser: "Eraser",
    shape: "Shape",
    icon: "Icon",
    text: "Text",
    filter: "Filter",
  };
  Object.entries(labels).forEach(([menuName, label]) => {
    const submenu = document.querySelector(`.tui-image-editor-menu-${menuName}`);
    if (!submenu || submenu.querySelector(".custom-submenu-title")) return;
    const title = document.createElement("h3");
    title.className = "custom-submenu-title";
    title.textContent = label;
    submenu.prepend(title);
  });
}

function updateActiveSubmenuLayout() {
  const main = document.querySelector(".tui-image-editor-main");
  const activeClass = Array.from(main?.classList || [])
    .find((className) => className.startsWith("tui-image-editor-menu-"));
  if (!activeClass) return;
  const menuName = activeClass.replace("tui-image-editor-menu-", "");
  const panel = document.querySelector(`.tui-image-editor-submenu > .tui-image-editor-menu-${menuName}`);
  const title = panel?.querySelector(".custom-submenu-title");
  const list = panel?.querySelector(".tui-image-editor-submenu-item");
  if (!panel || !title || !list) return;
  const panelRect = panel.getBoundingClientRect();
  const listRect = list.getBoundingClientRect();
  const titleRect = title.getBoundingClientRect();
  if (!panelRect.height || !listRect.height || !titleRect.height) return;
  const listTop = (panelRect.height - listRect.height) / 2;
  const titleGap = titleRect.height * 2;
  title.style.top = `${Math.max(8, Math.round(listTop - titleGap - titleRect.height))}px`;
}

function activateBuiltInMenu(menuName) {
  clearCustomMenuState();
  selectionMode = "";
  eraserMode = false;
  const menu = document.querySelector(`.tie-btn-${menuName}`);
  menu?.click();
}

function clearBuiltInMenuState() {
  document.querySelectorAll(".tui-image-editor-menu > .tui-image-editor-item:not(.custom-main-tool)").forEach((item) => {
    item.classList.remove("active");
  });
  const main = document.querySelector(".tui-image-editor-main");
  main?.classList.forEach((className) => {
    if (className.startsWith("tui-image-editor-menu-")) main.classList.remove(className);
  });
}

function activeMenuName() {
  const main = document.querySelector(".tui-image-editor-main");
  const activeClass = Array.from(main?.classList || [])
    .find((className) => className.startsWith("tui-image-editor-menu-"));
  return activeClass ? activeClass.replace("tui-image-editor-menu-", "") : "";
}

function builtInMenuNameFromButton(button) {
  return builtInMenus.find((menuName) => button.classList.contains(`tie-btn-${menuName}`)) || "";
}

function keepMenuOpen(menuName) {
  const main = document.querySelector(".tui-image-editor-main");
  const button = document.querySelector(`.tie-btn-${menuName}`);
  if (!main || !button) return;
  customMenus.forEach((name) => main.classList.remove(`tui-image-editor-menu-${name}`));
  main.classList.add(`tui-image-editor-menu-${menuName}`);
  button.classList.add("active");
  scheduleEditorLayoutRefresh();
}

function resetEditorInteraction(options = {}) {
  const canvas = editorCanvas();
  handMode = false;
  handDragging = false;
  handLastPoint = null;
  selectionMode = "";
  eraserMode = false;
  imageEditor?.stopDrawingMode?.();
  if (!options.keepSelectionGuide) removeSelectionGuide();
  if (canvas) {
    canvas.defaultCursor = "default";
    canvas.selection = true;
    canvas.getObjects().forEach((object) => {
      if (!object.grokSelectionGuide) object.evented = true;
    });
    canvas.requestRenderAll();
  }
}

function setSelectionShape(shape) {
  selectionShape = shape === "ellipse" ? "ellipse" : "rect";
  document.querySelectorAll(".selection-rectangle").forEach((button) => {
    button.classList.toggle("active", selectionShape === "rect");
  });
  document.querySelectorAll(".selection-ellipse").forEach((button) => {
    button.classList.toggle("active", selectionShape === "ellipse");
  });
}

function enableSelectionDrawing(shape = selectionShape) {
  const canvas = editorCanvas();
  if (!canvas) return;
  resetEditorInteraction({ keepSelectionGuide: true });
  selectionMode = "select";
  setSelectionShape(shape);
  canvas.discardActiveObject();
  canvas.selection = false;
  canvas.defaultCursor = "crosshair";
  canvas.getObjects().forEach((object) => {
    if (!object.grokSelectionGuide) object.evented = false;
  });
  canvas.requestRenderAll();
}

function makeSelectionShapeButton(shape) {
  const isEllipse = shape === "ellipse";
  const button = makeActionButton(isEllipse ? "Ellipse" : "Rectangle", () => {
    enableSelectionDrawing(isEllipse ? "ellipse" : "rect");
  }, isEllipse ? "selection-ellipse" : "selection-rectangle");
  button.textContent = "";
  button.setAttribute("aria-label", isEllipse ? "Ellipse selection" : "Rectangle selection");
  if ((!isEllipse && selectionShape === "rect") || (isEllipse && selectionShape === "ellipse")) {
    button.classList.add("active");
  }
  return button;
}

function makeSelectionShapeRow() {
  const shapes = document.createElement("div");
  shapes.className = "custom-tool-choice-row custom-selection-shape-row";
  shapes.append(makeSelectionShapeButton("rect"), makeSelectionShapeButton("ellipse"));
  return shapes;
}

function installFilterSelectionOptions() {
  const list = document.querySelector(".tui-image-editor-menu-filter .tui-image-editor-submenu-item");
  if (!list || list.querySelector(".custom-filter-selection-options")) return;
  const host = document.createElement("li");
  host.className = "custom-filter-selection-options tui-image-editor-newline";
  host.append(makeSelectionShapeRow(), makeOptionDivider());
  list.insertBefore(host, list.firstElementChild);
}

function createStylePanel(menuName, options = {}) {
  const list = document.querySelector(`.tui-image-editor-menu-${menuName} .tui-image-editor-submenu-item`);
  if (!list) return null;
  const host = document.createElement("li");
  const colors = document.createElement("div");
  const nativeFill = findNativeFillOption(list);
  host.className = `custom-style-options custom-${menuName}-style-options tui-image-editor-newline`;
  if (nativeFill) host.classList.add("custom-style-replacement");
  colors.className = "custom-style-colors";
  const fill = makeColorOption("Fill", "#fbad2e", (color) => applyObjectStyle(menuName, { fill: color }));
  const stroke = makeColorOption("Stroke", "#ffffff", (color) => applyObjectStyle(menuName, { stroke: color }));
  colors.append(fill.label, stroke.label);
  const strokeWidth = makeCustomRange("Stroke", 0, 40, 2, (value) => applyObjectStyle(menuName, { strokeWidth: value }));
  host.append(colors, strokeWidth.control);
  if (nativeFill) {
    nativeFill.classList.add("custom-native-fill-hidden");
    list.insertBefore(host, nativeFill);
  } else {
    list.appendChild(host);
  }
  const panel = { host, fill, stroke, strokeWidth, kind: options.kind || "both" };
  setStylePanelKind(panel, panel.kind);
  return panel;
}

function findNativeFillOption(list) {
  return Array.from(list.children).find((item) => {
    if (item.classList.contains("custom-opacity-options")
      || item.classList.contains("custom-style-options")
      || item.classList.contains("custom-font-options")) return false;
    if (item.querySelector(".tie-text-color, .tie-icon-color")) return true;
    const text = item.textContent.trim().toLowerCase();
    return (text === "fill" || text === "color" || text.endsWith("fill") || text.endsWith("color"))
      && item.querySelector('input[type="color"], .tui-colorpicker-palette-button');
  }) || null;
}

function setStylePanelKind(panel, kind) {
  if (!panel) return;
  panel.kind = kind;
  setOptionDisabled(panel.fill, kind === "stroke");
  setOptionDisabled(panel.stroke, kind === "fill");
  panel.strokeWidth.control.classList.toggle("is-disabled", kind === "fill");
  panel.strokeWidth.range.disabled = kind === "fill";
  panel.strokeWidth.number.disabled = kind === "fill";
}

let iconStylePanel = null;
let textStylePanel = null;

function applyObjectStyle(menuName, styles) {
  if (menuName === "icon") {
    const id = selectedIconId || selectedObjectId;
    if (id) imageEditor.setObjectPropertiesQuietly(id, styles);
    return;
  }
  if (menuName === "text" && selectedTextId) {
    imageEditor.changeTextStyle(selectedTextId, styles).catch(() => {});
  }
}

function installIconAndTextStyles() {
  iconStylePanel = createStylePanel("icon", { kind: "fill" });
  textStylePanel = createStylePanel("text", { kind: "both" });
  document.querySelectorAll(".tui-image-editor-menu-icon .tui-image-editor-button").forEach((button) => {
    button.addEventListener("click", () => {
      pendingIconKind = "fill";
      pendingIconName = "";
      setCustomIconButtonState(null);
      setStylePanelKind(iconStylePanel, "fill");
    });
  });
}

function setCustomIconButtonState(activeButton) {
  document.querySelectorAll(".tui-image-editor-menu-icon .custom-icon-button").forEach((button) => {
    button.classList.toggle("active", button === activeButton);
  });
  if (!activeButton) return;
  document.querySelectorAll(".tui-image-editor-menu-icon .tui-image-editor-button.active:not(.custom-icon-button)").forEach((button) => {
    button.classList.remove("active");
  });
}

function installCustomIcons() {
  imageEditor.registerIcons(customIconPaths);
  const host = document.querySelector(".tui-image-editor-menu-icon .tie-icon-add-button");
  if (!host) return;
  Object.entries(customIconPaths).forEach(([name, path], index) => {
    const button = document.createElement("div");
    button.type = "button";
    button.className = "tui-image-editor-button custom-icon-button";
    button.dataset.icontype = name;
    button.setAttribute("role", "button");
    button.setAttribute("tabindex", "0");
    button.setAttribute("aria-label", `Bubble${index + 1}`);
    button.innerHTML = `<div><svg class="svg_ic-submenu" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"></path></svg></div><label>Bubble${index + 1}</label>`;
    button.addEventListener("click", () => {
      pendingIconKind = "stroke";
      pendingIconName = name;
      setCustomIconButtonState(button);
      setStylePanelKind(iconStylePanel, "stroke");
      imageEditor.startDrawingMode("ICON");
      imageEditor.setDrawingIcon(name, {
        fill: "transparent",
        stroke: iconStylePanel?.stroke.input.value || "#ffffff",
        strokeWidth: Number(iconStylePanel?.strokeWidth.range.value || 2),
      });
    });
    button.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") button.click();
    });
    host.appendChild(button);
  });
}

function installHandTool() {
  const handButton = document.querySelector(".tie-btn-hand");
  const canvas = editorCanvas();
  if (!handButton || !canvas) return;
  handButton.addEventListener("click", () => {
    handMode = !handMode;
    selectionMode = "";
    eraserMode = false;
    handButton.classList.toggle("active", handMode);
    canvas.defaultCursor = handMode ? "grab" : "default";
    canvas.selection = !handMode;
    canvas.getObjects().forEach((object) => {
      if (!object.grokSelectionGuide) object.evented = !handMode;
    });
    canvas.requestRenderAll();
  });
  canvas.on("mouse:down", (event) => {
    if (!handMode) return;
    handDragging = true;
    handLastPoint = { x: event.e.clientX, y: event.e.clientY };
    canvas.defaultCursor = "grabbing";
  });
  canvas.on("mouse:move", (event) => {
    if (!handMode || !handDragging || !handLastPoint) return;
    const next = { x: event.e.clientX, y: event.e.clientY };
    canvas.relativePan(new fabric.Point(next.x - handLastPoint.x, next.y - handLastPoint.y));
    handLastPoint = next;
  });
  canvas.on("mouse:up", () => {
    if (!handMode) return;
    handDragging = false;
    handLastPoint = null;
    canvas.defaultCursor = "grab";
  });
}

function removeSelectionGuide() {
  const canvas = editorCanvas();
  if (selectionGuide && canvas) canvas.remove(selectionGuide);
  if (selectionInverseGuide && canvas) canvas.remove(selectionInverseGuide);
  selectionGuide = null;
  selectionInverseGuide = null;
  selectionInverse = false;
  resetSelectionFilterBaseline();
}

function selectionBounds() {
  if (!selectionGuide) return null;
  const size = imageEditor.getCanvasSize();
  const left = Math.max(0, selectionGuide.left || 0);
  const top = Math.max(0, selectionGuide.top || 0);
  const width = Math.min(Math.max(1, selectionGuide.getScaledWidth()), Math.max(1, size.width - left));
  const height = Math.min(Math.max(1, selectionGuide.getScaledHeight()), Math.max(1, size.height - top));
  return {
    left,
    top,
    width,
    height,
    shape: selectionGuide.type === "ellipse" ? "ellipse" : "rect",
  };
}

function traceMaskShape(context, bounds) {
  context.beginPath();
  if (bounds.shape === "ellipse") {
    context.ellipse(
      bounds.left + bounds.width / 2,
      bounds.top + bounds.height / 2,
      bounds.width / 2,
      bounds.height / 2,
      0,
      0,
      Math.PI * 2,
    );
  } else {
    context.rect(bounds.left, bounds.top, bounds.width, bounds.height);
  }
}

function drawMaskShape(context, bounds) {
  traceMaskShape(context, bounds);
  context.fill();
}

function selectionGuideStyle() {
  return {
    fill: "transparent",
    stroke: "#ffffff",
    strokeWidth: 2,
    strokeDashArray: [8, 6],
    selectable: false,
    evented: false,
    excludeFromExport: true,
    grokSelectionGuide: true,
  };
}

function applySelectionGuideBounds(bounds) {
  if (!selectionGuide) return;
  selectionGuide.set({
    left: bounds.left,
    top: bounds.top,
    width: bounds.width,
    height: bounds.height,
  });
  if (selectionGuide.type === "ellipse") {
    selectionGuide.set({ rx: bounds.width / 2, ry: bounds.height / 2 });
  }
  selectionGuide.setCoords();
  updateInverseSelectionGuide();
}

function createSelectionGuide(bounds) {
  const canvas = editorCanvas();
  removeSelectionGuide();
  const style = selectionGuideStyle();
  selectionGuide = bounds.shape === "ellipse"
    ? new fabric.Ellipse({ ...style, left: bounds.left, top: bounds.top, width: bounds.width, height: bounds.height, rx: bounds.width / 2, ry: bounds.height / 2 })
    : new fabric.Rect({ ...style, left: bounds.left, top: bounds.top, width: bounds.width, height: bounds.height });
  canvas.add(selectionGuide);
  updateInverseSelectionGuide();
  canvas.requestRenderAll();
}

function updateInverseSelectionGuide() {
  const canvas = editorCanvas();
  if (!canvas) return;
  if (selectionInverseGuide) {
    canvas.remove(selectionInverseGuide);
    selectionInverseGuide = null;
  }
  if (!selectionInverse || !selectionGuide) return;
  const size = imageEditor.getCanvasSize();
  selectionInverseGuide = new fabric.Rect({
    ...selectionGuideStyle(),
    left: 0,
    top: 0,
    width: size.width,
    height: size.height,
  });
  canvas.add(selectionInverseGuide);
  canvas.bringToFront(selectionGuide);
  canvas.requestRenderAll();
}

function activateSelectionTool() {
  const canvas = editorCanvas();
  if (!canvas) return;
  enableSelectionDrawing(selectionShape);
  showCustomPanel("selection");
}

function activateInitialSelection(attempt = 0) {
  if (editorUserChoseMenu) return;
  const button = document.querySelector('[data-custom-tool="selection"] button');
  if (editorCanvas() && button) {
    button.click();
    scheduleEditorLayoutRefresh(120);
    scheduleEditorLayoutRefresh(320);
    if (attempt < 10) {
      window.setTimeout(() => activateInitialSelection(attempt + 1), 220);
    }
    return;
  }
  if (attempt < 60) {
    window.setTimeout(() => activateInitialSelection(attempt + 1), 100);
  }
}

function activateTransformTool() {
  const canvas = editorCanvas();
  resetEditorInteraction({ keepSelectionGuide: true });
  showCustomPanel("transform");
  if (!canvas) return;
  canvas.selection = true;
  canvas.defaultCursor = "default";
  canvas.requestRenderAll();
}

function activateEraserTool() {
  resetEditorInteraction();
  eraserMode = true;
  showCustomPanel("eraser");
  applyEraserBrush();
}

function selectFullImage(options = {}) {
  const previousMode = selectionMode;
  resetSelectionFilterBaseline();
  setSelectionShape("rect");
  if (!options.preserveMenu) activateSelectionTool();
  const size = imageEditor.getCanvasSize();
  createSelectionGuide({ left: 0, top: 0, width: size.width, height: size.height, shape: "rect" });
  if (options.preserveMenu) selectionMode = previousMode;
  setStatus("All selected.");
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = url;
  });
}

function imageWithoutSelection() {
  const canvas = editorCanvas();
  const guides = [selectionGuide, selectionInverseGuide].filter(Boolean);
  guides.forEach((guide) => canvas.remove(guide));
  const url = imageEditor.toDataURL({ format: "png" });
  guides.forEach((guide) => canvas.add(guide));
  canvas.requestRenderAll();
  return url;
}

function selectionFilterKey(filterType, bounds, inverse, feather) {
  return JSON.stringify({
    filterType,
    inverse,
    feather,
    left: Math.round(bounds.left),
    top: Math.round(bounds.top),
    width: Math.round(bounds.width),
    height: Math.round(bounds.height),
    shape: bounds.shape,
  });
}

async function renderFilteredImage(sourceUrl, filterType, options) {
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-10000px;top:-10000px;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none;";
  document.body.appendChild(host);
  const tempEditor = new tui.ImageEditor(host, {
    cssMaxWidth: 4096,
    cssMaxHeight: 4096,
    usageStatistics: false,
  });
  try {
    await tempEditor.loadImageFromURL(sourceUrl, imageName);
    await tempEditor.applyFilter(filterType, options || {});
    return tempEditor.toDataURL({ format: "png" });
  } finally {
    tempEditor.destroy();
    host.remove();
  }
}

async function applyFilterToSelection(filterType, options) {
  const bounds = selectionBounds();
  if (!bounds) throw new Error("Make a selection first.");
  const inverse = selectionInverse;
  const feather = selectionFeather;
  const key = selectionFilterKey(filterType, bounds, inverse, feather);
  if (!selectionFilterBaseline || selectionFilterBaseline.key !== key) {
    selectionFilterBaseline = {
      key,
      filterType,
      bounds: { ...bounds },
      inverse,
      feather,
      sourceUrl: imageWithoutSelection(),
    };
  }
  const source = await loadImage(selectionFilterBaseline.sourceUrl);
  const filtered = await loadImage(await renderFilteredImage(selectionFilterBaseline.sourceUrl, filterType, options));
  const mask = document.createElement("canvas");
  const filteredLayer = document.createElement("canvas");
  const output = document.createElement("canvas");
  mask.width = filteredLayer.width = output.width = source.width;
  mask.height = filteredLayer.height = output.height = source.height;
  const maskContext = mask.getContext("2d");
  const filteredContext = filteredLayer.getContext("2d");
  const outputContext = output.getContext("2d");

  maskContext.fillStyle = "#fff";
  if (inverse) {
    maskContext.fillRect(0, 0, mask.width, mask.height);
    maskContext.globalCompositeOperation = "destination-out";
  }
  maskContext.filter = feather ? `blur(${feather}px)` : "none";
  drawMaskShape(maskContext, bounds);

  filteredContext.drawImage(filtered, 0, 0);
  filteredContext.globalCompositeOperation = "destination-in";
  filteredContext.drawImage(mask, 0, 0);
  outputContext.drawImage(source, 0, 0);
  outputContext.drawImage(filteredLayer, 0, 0);

  selectionGuide = null;
  selectionInverseGuide = null;
  await imageEditor.loadImageFromURL(output.toDataURL("image/png"), imageName);
  createSelectionGuide({ ...bounds, shape: bounds.shape });
  selectionInverse = inverse;
  if (selectionInverse) updateInverseSelectionGuide();
  selectionFeather = feather;
  setStatus("Filter applied to selection.");
}

async function restoreSelectionFilterBaseline(filterType) {
  if (!selectionFilterBaseline || selectionFilterBaseline.filterType !== filterType) return;
  const { sourceUrl, bounds, inverse, feather } = selectionFilterBaseline;
  selectionGuide = null;
  selectionInverseGuide = null;
  selectionFilterBaseline = null;
  await imageEditor.loadImageFromURL(sourceUrl, imageName);
  createSelectionGuide({ ...bounds, shape: bounds.shape });
  selectionInverse = inverse;
  selectionFeather = feather;
  if (selectionInverse) updateInverseSelectionGuide();
  setStatus("Selection filter removed.");
}

function bindSelectionFilterProxy() {
  if (!imageEditor?.applyFilter || imageEditor.applyFilter.__grokSelectionProxy) return;
  const nativeApplyFilter = imageEditor.applyFilter.bind(imageEditor);
  const nativeRemoveFilter = imageEditor.removeFilter?.bind(imageEditor);
  imageEditor.applyFilter = (filterType, options, isSilent) => {
    if (selectionGuide) {
      return applyFilterToSelection(filterType, options).catch((error) => {
        setStatus(error.message, "error");
        throw error;
      });
    }
    resetSelectionFilterBaseline();
    return nativeApplyFilter(filterType, options, isSilent);
  };
  if (nativeRemoveFilter) {
    imageEditor.removeFilter = (filterType) => {
      if (selectionGuide && selectionFilterBaseline?.filterType === filterType) {
        return restoreSelectionFilterBaseline(filterType).catch((error) => {
          setStatus(error.message, "error");
          throw error;
        });
      }
      resetSelectionFilterBaseline();
      return nativeRemoveFilter(filterType);
    };
    imageEditor.removeFilter.__grokSelectionProxy = true;
  }
  imageEditor.applyFilter.__grokSelectionProxy = true;
}

async function maskedSelectionData(inverse = selectionInverse) {
  const bounds = selectionBounds();
  if (!bounds) throw new Error("Make a selection first.");
  const source = await loadImage(imageWithoutSelection());
  const mask = document.createElement("canvas");
  const output = document.createElement("canvas");
  mask.width = output.width = source.width;
  mask.height = output.height = source.height;
  const maskContext = mask.getContext("2d");
  const outputContext = output.getContext("2d");
  maskContext.fillStyle = "#fff";
  if (inverse) {
    maskContext.fillRect(0, 0, mask.width, mask.height);
    maskContext.globalCompositeOperation = "destination-out";
  }
  maskContext.filter = selectionFeather ? `blur(${selectionFeather}px)` : "none";
  drawMaskShape(maskContext, bounds);
  outputContext.drawImage(source, 0, 0);
  outputContext.globalCompositeOperation = "destination-in";
  outputContext.drawImage(mask, 0, 0);
  if (inverse) return { url: output.toDataURL("image/png"), left: 0, top: 0 };
  const crop = document.createElement("canvas");
  crop.width = Math.ceil(bounds.width);
  crop.height = Math.ceil(bounds.height);
  crop.getContext("2d").drawImage(
    output,
    bounds.left,
    bounds.top,
    bounds.width,
    bounds.height,
    0,
    0,
    bounds.width,
    bounds.height,
  );
  return { url: crop.toDataURL("image/png"), left: bounds.left, top: bounds.top };
}

async function copySelection() {
  pixelClipboard = await maskedSelectionData();
  setStatus("Selection copied.");
}

async function replaceWithDeletedSelection() {
  const bounds = selectionBounds();
  if (!bounds) throw new Error("Make a selection first.");
  const source = await loadImage(imageWithoutSelection());
  const output = document.createElement("canvas");
  output.width = source.width;
  output.height = source.height;
  const context = output.getContext("2d");
  context.drawImage(source, 0, 0);
  context.globalCompositeOperation = "destination-out";
  context.filter = selectionFeather ? `blur(${selectionFeather}px)` : "none";
  if (selectionInverse) {
    context.fillRect(0, 0, output.width, output.height);
    context.globalCompositeOperation = "source-over";
    context.filter = "none";
    context.save();
    traceMaskShape(context, bounds);
    context.clip();
    context.drawImage(source, 0, 0);
    context.restore();
  } else {
    drawMaskShape(context, bounds);
  }
  removeSelectionGuide();
  await imageEditor.loadImageFromURL(output.toDataURL("image/png"), imageName);
  setStatus("Selection deleted.");
}

async function pasteSelection() {
  if (!pixelClipboard?.url) throw new Error("Nothing has been copied.");
  const props = await imageEditor.addImageObject(pixelClipboard.url);
  movedSelectionId = props?.id || null;
  if (movedSelectionId) {
    const imageProps = imageEditor.getObjectProperties(movedSelectionId, ["width", "height"]);
    imageEditor.setObjectPropertiesQuietly(movedSelectionId, {
      left: pixelClipboard.left + Number(imageProps?.width || 0) / 2,
      top: pixelClipboard.top + Number(imageProps?.height || 0) / 2,
    });
    const canvas = editorCanvas();
    const object = canvas?.getObjects().find((candidate) => candidate.id === movedSelectionId);
    if (object) {
      object.set({ selectable: true, evented: true, hasControls: true, hasBorders: true });
      canvas.setActiveObject(object);
      canvas.requestRenderAll();
    }
  }
  setStatus("Selection pasted.");
}

async function cutSelection() {
  await copySelection();
  await replaceWithDeletedSelection();
}

async function transformSelection() {
  await cutSelection();
  await pasteSelection();
  selectionMode = "";
  const canvas = editorCanvas();
  canvas.selection = true;
  canvas.defaultCursor = "default";
  setStatus("Free Transform ready.");
}

async function flipSelectionOrImage(axis) {
  if (selectionGuide) {
    await flipMovedSelection(axis);
    return;
  }
  if (axis === "x") {
    await imageEditor.flipX();
  } else {
    await imageEditor.flipY();
  }
}

async function flipMovedSelection(axis) {
  if (!movedSelectionId) await transformSelection();
  const prop = axis === "x" ? "flipX" : "flipY";
  const current = imageEditor.getObjectProperties(movedSelectionId, prop)?.[prop];
  imageEditor.setObjectPropertiesQuietly(movedSelectionId, { [prop]: !current });
}

function applyTransformAngle(value) {
  transformAngle = Number(value) || 0;
  imageEditor.setAngle(transformAngle).catch((error) => setStatus(error.message, "error"));
}

function rotateTransform(delta) {
  transformAngle = Math.max(-360, Math.min(360, transformAngle + delta));
  imageEditor.rotate(delta).catch((error) => setStatus(error.message, "error"));
  const range = document.querySelector(".transform-angle-control input[type='range']");
  const number = document.querySelector(".transform-angle-control input[type='number']");
  if (range && number) {
    range.value = String(transformAngle);
    number.value = String(transformAngle);
    syncNativeRangeFill(range);
  }
}

function makeActionButton(label, onClick, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `custom-tool-button ${className}`.trim();
  button.textContent = label;
  button.addEventListener("click", () => Promise.resolve(onClick()).catch((error) => setStatus(error.message, "error")));
  return button;
}

function makeFlipActionButton(axis, onClick) {
  const label = axis === "x" ? "Flip X" : "Flip Y";
  const button = makeActionButton(label, onClick, `custom-flip-button flip-${axis}`);
  button.innerHTML = `<svg class="svg_ic-submenu" aria-hidden="true"><use xlink:href="#ic-flip-${axis}" class="normal use-default"></use><use xlink:href="#ic-flip-${axis}" class="active use-default"></use></svg><label>${label}</label>`;
  return button;
}

function installSelectionTool() {
  const button = addCustomMainTool("selection", "Selection", "custom-selection-tool", "first");
  const panel = makeToolPanel("selection");
  const shapes = makeSelectionShapeRow();
  const actions = document.createElement("div");
  actions.className = "custom-tool-action-grid";
  const feather = makeCustomRange("Feather", 0, 100, 0, (value) => {
    selectionFeather = value;
  });
  actions.append(
    makeActionButton("Inverse", () => {
      selectionInverse = !selectionInverse;
      updateInverseSelectionGuide();
      setStatus(selectionInverse ? "Inverse selection enabled." : "Inverse selection disabled.");
    }),
  );
  panel.append(shapes, feather.control, actions);
  button?.addEventListener("click", (event) => {
    if (event.isTrusted) editorUserChoseMenu = true;
    activateSelectionTool();
  });
  const canvas = editorCanvas();
  canvas.on("mouse:down", (event) => {
    if (selectionMode !== "select") return;
    const point = canvas.getPointer(event.e);
    selectionStart = point;
    removeSelectionGuide();
    selectionInverse = false;
    const base = {
      left: point.x,
      top: point.y,
      width: 1,
      height: 1,
      ...selectionGuideStyle(),
    };
    selectionGuide = selectionShape === "ellipse" ? new fabric.Ellipse({ ...base, rx: 1, ry: 1 }) : new fabric.Rect(base);
    canvas.add(selectionGuide);
  });
  canvas.on("mouse:move", (event) => {
    if (selectionMode !== "select" || !selectionStart || !selectionGuide) return;
    const point = canvas.getPointer(event.e);
    const rawWidth = point.x - selectionStart.x;
    const rawHeight = point.y - selectionStart.y;
    const constrained = event.e.shiftKey;
    const size = Math.max(Math.abs(rawWidth), Math.abs(rawHeight));
    const width = constrained ? size : Math.abs(rawWidth);
    const height = constrained ? size : Math.abs(rawHeight);
    const left = rawWidth < 0 ? selectionStart.x - width : selectionStart.x;
    const top = rawHeight < 0 ? selectionStart.y - height : selectionStart.y;
    applySelectionGuideBounds({ left, top, width, height, shape: selectionShape });
    canvas.requestRenderAll();
  });
  canvas.on("mouse:up", () => {
    if (selectionMode === "select") selectionStart = null;
  });
}

function installTransformTool() {
  const button = addCustomMainTool("transform", "Transform", "custom-transform-tool", "after-crop");
  const panel = makeToolPanel("transform");
  const shapes = makeSelectionShapeRow();
  const flips = document.createElement("div");
  const rotations = document.createElement("div");
  flips.className = "custom-tool-action-grid";
  rotations.className = "custom-tool-action-grid";
  flips.append(
    makeFlipActionButton("x", () => flipSelectionOrImage("x")),
    makeFlipActionButton("y", () => flipSelectionOrImage("y")),
  );
  rotations.append(
    makeActionButton("Rotate -90", () => rotateTransform(-90), "custom-rotate-button"),
    makeActionButton("Rotate +90", () => rotateTransform(90), "custom-rotate-button"),
  );
  const angle = makeCustomRange("Angle", -360, 360, 0, applyTransformAngle);
  angle.control.classList.add("transform-angle-control");
  panel.append(shapes, makeOptionDivider(), flips, rotations, angle.control);
  button?.addEventListener("click", (event) => {
    if (event.isTrusted) editorUserChoseMenu = true;
    activateTransformTool();
  });
}

function applyEraserBrush() {
  const canvas = editorCanvas();
  if (!canvas) return;
  imageEditor.startDrawingMode("FREE_DRAWING");
  imageEditor.setBrush({
    width: eraserSize,
    color: `rgba(0,0,0,${eraserOpacity / 100})`,
  });
  if (canvas.freeDrawingBrush) {
    canvas.freeDrawingBrush.width = eraserSize;
    canvas.freeDrawingBrush.color = `rgba(0,0,0,${eraserOpacity / 100})`;
    canvas.freeDrawingBrush.shadow = eraserSoft
      ? new fabric.Shadow({ color: `rgba(0,0,0,${eraserOpacity / 100})`, blur: eraserSize / 2 })
      : null;
  }
}

function installEraserTool() {
  const button = addCustomMainTool("eraser", "Eraser", "custom-eraser-tool", "after-draw");
  const panel = makeToolPanel("eraser");
  const brushes = document.createElement("div");
  brushes.className = "custom-tool-choice-row";
  const hard = makeActionButton("Hard", () => {
    eraserSoft = false;
    hard.classList.add("active");
    soft.classList.remove("active");
    applyEraserBrush();
  }, "eraser-hard active");
  hard.textContent = "";
  hard.setAttribute("aria-label", "Hard eraser");
  const soft = makeActionButton("Soft", () => {
    eraserSoft = true;
    soft.classList.add("active");
    hard.classList.remove("active");
    applyEraserBrush();
  }, "eraser-soft");
  soft.textContent = "";
  soft.setAttribute("aria-label", "Soft eraser");
  brushes.append(hard, soft);
  const size = makeCustomRange("Size", 1, 200, eraserSize, (value) => {
    eraserSize = value;
    applyEraserBrush();
  });
  const opacity = makeCustomRange("Opacity", 1, 100, eraserOpacity, (value) => {
    eraserOpacity = value;
    applyEraserBrush();
  });
  panel.append(brushes, size.control, opacity.control);
  button?.addEventListener("click", (event) => {
    if (event.isTrusted) editorUserChoseMenu = true;
    activateEraserTool();
  });
  editorCanvas().on("path:created", (event) => {
    if (!eraserMode || !event.path) return;
    event.path.set({
      globalCompositeOperation: "destination-out",
      opacity: eraserOpacity / 100,
      shadow: eraserSoft
        ? new fabric.Shadow({ color: "#000", blur: eraserSize / 2 })
        : null,
    });
    editorCanvas().requestRenderAll();
  });
}

function bindEditorShortcuts() {
  document.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLInputElement
      || event.target instanceof HTMLTextAreaElement
      || event.target instanceof HTMLSelectElement
      || event.target?.isContentEditable) return;
    const command = event.metaKey || event.ctrlKey;
    const key = event.key.toLowerCase();
    const cropActive = document.querySelector(".tui-image-editor-main")?.classList.contains("tui-image-editor-menu-crop");
    if (!command && key === "enter" && cropActive) {
      event.preventDefault();
      event.stopImmediatePropagation();
      clickCropAction("apply");
      return;
    }
    if (!command && key === "escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (cropActive) {
        clickCropAction("cancel");
      } else if (selectionGuide) {
        removeSelectionGuide();
      }
      return;
    }
    if (command && key === "z") {
      event.preventDefault();
      event.stopImmediatePropagation();
      (event.shiftKey ? imageEditor.redo() : imageEditor.undo()).catch(() => {});
      return;
    }
    if (command && key === "a") {
      event.preventDefault();
      event.stopImmediatePropagation();
      selectFullImage({ preserveMenu: true });
      return;
    }
    if (!command && key === "m") {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.shiftKey) selectionShape = selectionShape === "rect" ? "ellipse" : "rect";
      document.querySelector('[data-custom-tool="selection"] button')?.click();
      document.querySelector(`.selection-${selectionShape === "rect" ? "rectangle" : "ellipse"}`)?.click();
    } else if (!command && key === "e") {
      event.preventDefault();
      event.stopImmediatePropagation();
      document.querySelector('[data-custom-tool="eraser"] button')?.click();
    } else if (selectionGuide && command && key === "c") {
      event.preventDefault();
      event.stopImmediatePropagation();
      copySelection().catch((error) => setStatus(error.message, "error"));
    } else if (selectionGuide && command && key === "x") {
      event.preventDefault();
      event.stopImmediatePropagation();
      cutSelection().catch((error) => setStatus(error.message, "error"));
    } else if (pixelClipboard && command && key === "v") {
      event.preventDefault();
      event.stopImmediatePropagation();
      pasteSelection().catch((error) => setStatus(error.message, "error"));
    } else if (selectionGuide && command && key === "d") {
      event.preventDefault();
      event.stopImmediatePropagation();
      removeSelectionGuide();
    } else if (selectionGuide && command && event.shiftKey && key === "i") {
      event.preventDefault();
      event.stopImmediatePropagation();
      selectionInverse = !selectionInverse;
      updateInverseSelectionGuide();
      setStatus(selectionInverse ? "Inverse selection enabled." : "Inverse selection disabled.");
    } else if (selectionGuide && command && key === "t") {
      event.preventDefault();
      event.stopImmediatePropagation();
      transformSelection().catch((error) => setStatus(error.message, "error"));
    } else if (selectionGuide && (event.key === "Delete" || event.key === "Backspace")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      replaceWithDeletedSelection().catch((error) => setStatus(error.message, "error"));
    }
  }, true);
}

function clickCropAction(action) {
  const selectors = [
    `.tie-crop-button .tui-image-editor-button.${action}`,
    `.tie-crop-button.${action} .tui-image-editor-button`,
    `.tie-crop-button .${action}`,
  ];
  const button = selectors.map((selector) => document.querySelector(selector)).find(Boolean);
  button?.click();
}

function bindWheelZoom() {
  const surface = document.querySelector(".tui-image-editor-wrap");
  if (!surface) return;
  surface.addEventListener("wheel", (event) => {
    event.preventDefault();
    const now = Date.now();
    if (now - lastWheelZoomAt < 90) return;
    lastWheelZoomAt = now;
    const direction = event.deltaY < 0 ? 1 : -1;
    const next = Math.min(5, Math.max(0.1, wheelZoomLevel + direction * 0.25));
    if (next === wheelZoomLevel) return;
    const canvasSize = imageEditor.getCanvasSize();
    imageEditor.zoom({
      x: canvasSize.width / 2,
      y: canvasSize.height / 2,
      zoomLevel: next,
    });
    wheelZoomLevel = next;
    setStatus(`${Math.round(next * 100)}%`);
  }, { passive: false });
}

async function loadSystemFonts() {
  try {
    const data = await api("/api/system-fonts");
    const fonts = Array.isArray(data.fonts) ? data.fonts : [];
    const fragment = document.createDocumentFragment();
    fonts.forEach((font) => {
      const option = document.createElement("option");
      option.value = font;
      option.textContent = font;
      option.style.fontFamily = `"${font.replaceAll('"', '\\"')}"`;
      fragment.appendChild(option);
    });
    els.font.appendChild(fragment);
    els.font.options[0].textContent = `${fonts.length} system fonts`;
  } catch (error) {
    els.font.options[0].textContent = "System fonts unavailable";
    setStatus(error.message, "error");
  }
}

function bindEditorEvents() {
  imageEditor.on("objectActivated", (props) => {
    const type = String(props?.type || "").toLowerCase();
    syncSelectedOpacity(props?.id, type);
    selectedTextId = type.includes("text") ? props.id : null;
    selectedIconId = type === "icon" || type === "path-group" ? props.id : null;
    setFeatherControl(type === "rect" ? props.id : null);
    if (selectedIconId) {
      const iconProps = imageEditor.getObjectProperties(selectedIconId, ["fill", "stroke", "strokeWidth"]);
      const kind = iconProps?.fill === "transparent" || iconProps?.fill === "rgba(0,0,0,0)" ? "stroke" : "fill";
      setStylePanelKind(iconStylePanel, kind);
      if (iconProps?.fill && kind !== "stroke") iconStylePanel.fill.input.value = iconProps.fill;
      if (iconProps?.stroke && kind !== "fill") iconStylePanel.stroke.input.value = iconProps.stroke;
      iconStylePanel.strokeWidth.update(Number(iconProps?.strokeWidth || 0));
    }
    if (selectedTextId) {
      const textProps = imageEditor.getObjectProperties(selectedTextId, ["fill", "stroke", "strokeWidth"]);
      if (textProps?.fill) textStylePanel.fill.input.value = textProps.fill;
      if (textProps?.stroke) textStylePanel.stroke.input.value = textProps.stroke;
      textStylePanel.strokeWidth.update(Number(textProps?.strokeWidth || 0));
    }
    if (!selectedTextId) return;
    const fontFamily = imageEditor.getObjectProperties(selectedTextId, "fontFamily")?.fontFamily;
    if (fontFamily && Array.from(els.font.options).some((option) => option.value === fontFamily)) {
      els.font.value = fontFamily;
    }
  });

  imageEditor.on("objectAdded", (props) => {
    const type = String(props?.type || "").toLowerCase();
    const menuName = activeOpacityMenu();
    if (props?.id && menuName) {
      objectOpacityMenus.set(props.id, menuName);
      imageEditor.setObjectPropertiesQuietly(props.id, {
        opacity: opacityValues[menuName] / 100,
      });
    }
    if ((type === "icon" || type === "path-group") && props?.id) {
      selectedIconId = props.id;
      if (pendingIconKind === "stroke") {
        imageEditor.setObjectPropertiesQuietly(props.id, {
          fill: "transparent",
          stroke: iconStylePanel?.stroke.input.value || "#ffffff",
          strokeWidth: Number(iconStylePanel?.strokeWidth.range.value || 2),
        });
      }
    }
    syncSelectedOpacity(props?.id, type);
    if (type === "rect") setFeatherControl(props.id);
    if (!type.includes("text") || !pendingFont) return;
    selectedTextId = props.id;
    imageEditor.changeTextStyle(props.id, { fontFamily: pendingFont }).catch(() => {});
  });

  imageEditor.on("selectionCleared", () => {
    selectedObjectId = null;
    selectedObjectMenu = "";
    selectedTextId = null;
    selectedIconId = null;
    setFeatherControl();
  });

  els.font.addEventListener("change", () => {
    pendingFont = els.font.value;
    if (!pendingFont) return;
    if (!selectedTextId) {
      setStatus("Font selected. Add or select text.");
      return;
    }
    imageEditor.changeTextStyle(selectedTextId, { fontFamily: pendingFont })
      .then(() => setStatus(pendingFont))
      .catch((error) => setStatus(error.message, "error"));
  });

  els.ratios.forEach((button) => {
    button.addEventListener("click", () => {
      els.ratios.forEach((candidate) => candidate.classList.toggle("active", candidate === button));
      const cropMenu = document.querySelector(".tui-image-editor-menu-crop");
      if (cropMenu && !cropMenu.classList.contains("active")) cropMenu.click();
      window.setTimeout(() => {
        imageEditor.startDrawingMode("CROPPER");
        const ratioName = button.dataset.ratio;
        const ratio = ratioName === "original"
          ? originalAspect
          : Number(ratioName);
        imageEditor.setCropzoneRect(Number.isFinite(ratio) && ratio > 0 ? ratio : undefined);
      }, 0);
    });
  });

  els.featherRange.addEventListener("input", () => applyFeather(els.featherRange.value));
  els.featherValue.addEventListener("input", () => applyFeather(els.featherValue.value));
  bindOpacityControls();
  els.save.addEventListener("click", saveEdit);
  els.close.addEventListener("click", () => window.close());
}

function bindBuiltInMenuReset() {
  document.querySelectorAll(".tui-image-editor-menu > .tui-image-editor-item:not(.custom-main-tool)").forEach((button) => {
    button.addEventListener("click", (event) => {
      if (event.isTrusted) editorUserChoseMenu = true;
      const menuName = builtInMenuNameFromButton(button);
      if (menuName && activeMenuName() === menuName && button.classList.contains("active")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        keepMenuOpen(menuName);
        return;
      }
      clearCustomMenuState();
      resetEditorInteraction({ keepSelectionGuide: button.classList.contains("tie-btn-filter") });
      scheduleEditorLayoutRefresh();
    }, true);
  });
}

function bindZoomButtons() {
  const zoomIn = document.querySelector(".tie-btn-zoomIn");
  const zoomOut = document.querySelector(".tie-btn-zoomOut");
  const applyZoom = (direction) => {
    const next = Math.min(5, Math.max(0.1, wheelZoomLevel + direction * 0.25));
    if (next === wheelZoomLevel) return;
    const size = imageEditor.getCanvasSize();
    imageEditor.zoom({ x: size.width / 2, y: size.height / 2, zoomLevel: next });
    wheelZoomLevel = next;
    setStatus(`${Math.round(next * 100)}%`);
  };
  zoomIn?.addEventListener("click", (event) => {
    event.stopImmediatePropagation();
    applyZoom(1);
  }, true);
  zoomOut?.addEventListener("click", (event) => {
    event.stopImmediatePropagation();
    applyZoom(-1);
  }, true);
}

async function saveEdit() {
  if (!imageEditor || !itemId) return;
  els.save.disabled = true;
  setStatus("Saving...");
  try {
    const isJpeg = /\.(jpe?g)(?:$|\?)/i.test(sourceUrl);
    const image = imageEditor.toDataURL({
      format: isJpeg ? "jpeg" : "png",
      quality: 0.96,
    });
    const data = await api("/api/image-editor/save", {
      method: "POST",
      body: JSON.stringify({ item_id: itemId, source_url: sourceUrl, image }),
    });
    setStatus(`Saved ${data.item?.title || ""}`);
    window.opener?.postMessage(
      { type: "grok-studio-image-edit-saved", itemId: data.item?.id || "" },
      window.location.origin,
    );
    window.setTimeout(() => window.close(), 450);
  } catch (error) {
    setStatus(error.message, "error");
    els.save.disabled = false;
  }
}

function init() {
  if (!itemId || !sourceUrl || !window.tui?.ImageEditor) {
    setStatus("The image editor could not be opened.", "error");
    els.save.disabled = true;
    return;
  }

  imageEditor = new tui.ImageEditor(els.editor, {
    includeUI: {
      loadImage: { path: sourceUrl, name: imageName },
      theme: blackTheme(),
      menu: ["crop", "draw", "shape", "icon", "text", "filter"],
      initMenu: "",
      uiSize: { width: "100%", height: "100%" },
      menuBarPosition: "left",
    },
    cssMaxWidth: editorFitSize().width,
    cssMaxHeight: editorFitSize().height,
    usageStatistics: false,
  });
  installCustomOptionPanels();
  installFilterSelectionOptions();
  normalizeEditorLabels();
  installIconAndTextStyles();
  installCustomIcons();
  installHandTool();
  installSelectionTool();
  installTransformTool();
  installEraserTool();
  installSubmenuTitles();
  bindEditorEvents();
  bindBuiltInMenuReset();
  bindEditorShortcuts();
  bindWheelRangeControls();
  bindWheelZoom();
  bindZoomButtons();
  bindSelectionFilterProxy();
  setFeatherControl();
  loadSystemFonts();
  activateInitialSelection();
  window.setTimeout(() => {
    const canvasSize = imageEditor.getCanvasSize();
    if (canvasSize.width && canvasSize.height) originalAspect = canvasSize.width / canvasSize.height;
    scheduleEditorLayoutRefresh();
  }, 250);
  window.addEventListener("resize", () => window.requestAnimationFrame(() => {
    refreshEditorFit();
    updateActiveSubmenuLayout();
  }));
}

init();
window.setInterval(() => {
  fetch("/api/heartbeat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    keepalive: true,
  }).catch(() => {});
}, 3000);
