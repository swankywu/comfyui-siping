import { app } from "/scripts/app.js";

// SPPreviewImage / SPImageSaveWithExtraMetadata：
// 把 metadata_extra 这个 multiline 文本框的高度限制为最多 5 行，
// 并让节点整体高度随之收缩，使下方预览图向上贴紧。
//
// ComfyUI 1.58 前端里，multiline textarea 是 DOM widget（type="customtext"），
// 它的高度由两个机制决定，改 CSS height 或 computedHeight 都没用：
//   1. options.minNodeSize = [400, 200] → V2ToV1 时把 200 作为 widget 的 minHeight
//   2. computeLayoutSize() 读 CSS 变量 --comfy-widget-min-height 作为 minHeight
//
// 所以要真正控制高度，必须同时：
//   - 覆盖 widget.options.minNodeSize[1] 为 5 行高度
//   - 设置 textarea 的 --comfy-widget-min-height / --comfy-widget-max-height
//   - 锁定 textarea 实际 DOM 高度
//   - 重排节点，让预览图（img DOM widget）按新的 y 累加贴紧

const TARGET_NODES = new Set([
    "siping_SPPreviewImage",
    "siping_SPImageSaveWithExtraMetadata",
]);

const MAX_ROWS = 5;
const LINE_HEIGHT = 1.35; // em
const TEXTAREA_HEIGHT = `calc(${MAX_ROWS} * ${LINE_HEIGHT}em + 8px)`;
// 5 行约 124px，作为 minNodeSize[1] 与 CSS 变量的值
const MIN_HEIGHT_PX = Math.round(MAX_ROWS * LINE_HEIGHT * 16) + 8;

function clampMetadataExtra(node) {
    const widget = node.widgets?.find((w) => w.name === "metadata_extra");
    if (!widget) return;

    // 1) 覆盖 minNodeSize，去掉默认的 200px 最小高度
    if (widget.options) {
        widget.options.minNodeSize = [400, MIN_HEIGHT_PX];
        widget.options.minHeight = MIN_HEIGHT_PX;
    }

    const el = widget.element || widget.inputEl;
    if (!el) return;

    const textarea = el.classList?.contains("comfy-multiline-input")
        ? el
        : el.querySelector?.("textarea.comfy-multiline-input, textarea");

    if (textarea) {
        // 2) 通过 CSS 变量告诉 computeLayoutSize 正确的 min/max 高度
        textarea.style.setProperty("--comfy-widget-min-height", `${MIN_HEIGHT_PX}px`);
        textarea.style.setProperty("--comfy-widget-max-height", `${MIN_HEIGHT_PX}px`);

        // 3) 锁定实际 DOM 高度
        textarea.style.height = TEXTAREA_HEIGHT;
        textarea.style.minHeight = TEXTAREA_HEIGHT;
        textarea.style.maxHeight = TEXTAREA_HEIGHT;
        textarea.style.resize = "none";
        textarea.style.overflowY = "auto";
    }

    // 4) 重排节点，让预览图（img DOM widget）按新高度贴紧
    if (typeof node.setSize === "function") {
        const size = node.computeSize();
        if (size && size[1] != null) {
            node.setSize(size);
        }
    }
}

app.registerExtension({
    name: "comfyui-siping.MetadataExtraRows",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!TARGET_NODES.has(nodeData.name)) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            clampMetadataExtra(this);
            return r;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            requestAnimationFrame(() => clampMetadataExtra(this));
            return r;
        };

        // 预览图是 onExecuted 之后才动态挂载的 DOM widget，
        // 必须在它挂载完成后再次重排，才能消除 textarea 与预览图之间的空白。
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const r = onExecuted ? onExecuted.apply(this, arguments) : undefined;
            requestAnimationFrame(() => clampMetadataExtra(this));
            return r;
        };
    },
});
