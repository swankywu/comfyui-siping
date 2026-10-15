import { app } from "/scripts/app.js";

// SPPreviewText：调用 ComfyUI 0.39 内置的官方文本预览 API，
// 在节点上渲染文本预览区（自带 Markdown / 纯文本切换），
// 节点执行完成后把后端输出的 text 实时显示出来。
app.registerExtension({
    name: "SPPreviewText.Preview",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "siping_SPPreviewText") return;

        const api = () => window.comfyAPI?.textPreviewWidgets;
        // 前端过旧、没有官方文本预览 API 时静默跳过（仍可正常连接输出）
        if (!api()) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            api().addTextPreviewWidgets(this);
            return r;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const r = onExecuted ? onExecuted.apply(this, arguments) : undefined;
            api().updateTextPreviewWidgets(this, message);
            return r;
        };
    },
});
