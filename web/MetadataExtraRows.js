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

    // 4) 重排节点，让预览图（img DOM widget）按新高度贴紧。
    //    尊重用户手动高度：用户拖过则保留，否则只收缩消除空白。
    if (typeof node.setSize === "function" && typeof node.computeSize === "function") {
        const computedH = node.computeSize()?.[1];
        const userH = node.__userHeight;
        let targetH = null;
        if (userH != null) {
            targetH = Math.max(userH, computedH ?? 0);
        } else if (computedH != null && node.size?.[1] > computedH) {
            targetH = computedH;
        }
        if (targetH != null) {
            node.__clampLock = true;
            node.setSize([node.size[0], targetH]);
            node.__clampLock = false;
        }
    }
}

function installUserSizeMemory(node) {
    // 记录用户手动拖动后的高度，避免执行时被官方 computeSize 重置。
    // 通过 onResize 回调捕捉用户手动调整（含拖动底边/角）。
    if (node.__userSizeHooked) return;
    node.__userSizeHooked = true;

    const onResize = node.onResize;
    node.onResize = function (...args) {
        const r = onResize ? onResize.apply(this, args) : undefined;
        // 记录用户手动调整后的高度（排除本扩展自己 setSize 触发的 resize）
        if (node.__clampLock) return r;
        if (node.size && node.size[1] != null) {
            node.__userHeight = node.size[1];
        }
        return r;
    };
}

app.registerExtension({
    name: "comfyui-siping.MetadataExtraRows",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!TARGET_NODES.has(nodeData.name)) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            installUserSizeMemory(this);
            clampMetadataExtra(this);
            return r;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            installUserSizeMemory(this);
            requestAnimationFrame(() => clampMetadataExtra(this));
            return r;
        };

        // 预览图是 onExecuted 之后才动态挂载的 DOM widget，
        // 官方会在挂载时用 computeSize() 重置节点高度。
        // 这里在官方重置完成后，若用户手动拖过高度则恢复之，否则收缩消除空白。
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const r = onExecuted ? onExecuted.apply(this, arguments) : undefined;
            const restore = () => {
                const userH = this.__userHeight;
                const computedH = this.computeSize()?.[1];
                let targetH = null;
                if (userH != null) {
                    // 用户手动拖过：恢复用户高度（不小于内容最小高度，避免盖住预览图）
                    targetH = Math.max(userH, computedH ?? 0);
                } else if (computedH != null && this.size?.[1] > computedH) {
                    // 未拖过：只收缩消除空白
                    targetH = computedH;
                }
                if (targetH != null) {
                    this.__clampLock = true;
                    this.setSize([this.size[0], targetH]);
                    this.__clampLock = false;
                }
            };
            requestAnimationFrame(restore);
            return r;
        };
    },
});
