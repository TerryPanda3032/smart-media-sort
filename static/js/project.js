/* ===========================================================================
   项目页引导入口 — 初始化公共模块（面板注册在各 panel-*.js 中完成）
   加载顺序: app-events.js → project-common.js → panel-collect.js
             → panel-filter.js → panel-ai-classify.js → project.js
   =========================================================================== */
(function () {
  "use strict";

  var PC = window.ProjectCommon;
  PC.init();
})();
