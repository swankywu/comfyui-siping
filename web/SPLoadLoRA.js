import { app } from "/scripts/app.js";

// SPLoadLoRA：filter_key 实时过滤 lora_name 下拉框，只显示名称包含关键词的项
app.registerExtension({
    name: "SPLoadLoRA.Filter",
    beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "siping_SPLoadLoRA") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;

            const loraWidget = this.widgets?.find((w) => w.name === "lora_name");
            const filterWidget = this.widgets?.find((w) => w.name === "filter_key");
            if (!loraWidget || !filterWidget) return r;

            // 创建时快照完整列表（filter_key 为空时显示全部）
            const allOptions = Array.isArray(loraWidget.options?.values)
                ? [...loraWidget.options.values]
                : [];

            // 兼容旧版原生 <select>：直接重建 option
            const rebuildSelect = () => {
                const sel = loraWidget.inputEl;
                if (sel && sel.tagName === "SELECT") {
                    sel.innerHTML = "";
                    for (const v of loraWidget.options.values) {
                        const opt = document.createElement("option");
                        opt.value = v;
                        opt.text = v;
                        if (v === loraWidget.value) opt.selected = true;
                        sel.appendChild(opt);
                    }
                }
            };

            const applyFilter = () => {
                const key = (filterWidget.value ?? "").toString().toLowerCase();
                const filtered = key
                    ? allOptions.filter((o) => o.toLowerCase().includes(key))
                    : allOptions;
                loraWidget.options.values = filtered;

                // 选中项失效时落到第一个匹配项
                if (
                    loraWidget.value != null &&
                    !filtered.includes(loraWidget.value) &&
                    filtered.length
                ) {
                    loraWidget.value = filtered[0];
                }
                rebuildSelect();
                app.graph.setDirtyCanvas(true, true);
            };

            // 核心 ComfyUI 控件回调属性是 callback（不是 onChange）
            const origCallback = filterWidget.callback;
            filterWidget.callback = function () {
                const res = origCallback?.apply(this, arguments);
                applyFilter();
                return res;
            };
            // 部分前端版本走 onChange
            filterWidget.onChange = applyFilter;
            // DOM 事件兜底
            const el = filterWidget.inputEl || filterWidget.element;
            if (el) {
                el.addEventListener("input", applyFilter);
                el.addEventListener("change", applyFilter);
            }
            // 新版 Vue 前端兜底：拦截 value 属性赋值，任何写入都触发过滤
            let storedFilter = filterWidget.value;
            try {
                Object.defineProperty(filterWidget, "value", {
                    configurable: true,
                    get() {
                        return storedFilter;
                    },
                    set(v) {
                        storedFilter = v;
                        applyFilter();
                    },
                });
            } catch (e) {
                /* 某些环境属性不可配置，忽略 */
            }

            applyFilter();
            return r;
        };
    },
});
