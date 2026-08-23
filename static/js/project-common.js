/* ===========================================================================
   项目页公共逻辑 — 步骤条 / 面板加载分发 / 弹窗 / SSE 总线 / 指示灯
   由 project.js 引导，各面板模块（panel-*.js）通过 window.ProjectCommon 注册。
   =========================================================================== */
(function () {
  "use strict";

  var proj = window.__PROJECT__;
  var panelBody = document.getElementById("panelBody");
  var menuBtn = document.getElementById("projectMenuBtn");
  var dropdown = document.getElementById("projectDropdown");

  // 跨面板共享状态（面板模块经 PC.state 读写）
  var state = {
    leftCol: null,
    rightCol: null,
    panelBusy: false,
    ringChart: null,
    filterResizeHandler: null,
    btnNextHandler: null,
  };

  var _btnNextHandler = null;

  function showNotice(type, message, onConfirm) {
    var overlay = document.createElement("div");
    overlay.className = "collect-modal-overlay";
    overlay.style.zIndex = "9999";
    var card = document.createElement("div");
    card.className = "collect-modal-card";
    card.style.width = "340px";
    card.style.textAlign = "center";
    var header = document.createElement("div");
    header.className = "collect-modal-header";
    header.textContent = type === "success" ? "成功" : type === "error" ? "错误" : "提示";
    if (type === "error") header.style.color = "var(--danger)";
    var body = document.createElement("div");
    body.className = "collect-modal-body";
    var msg = document.createElement("p");
    msg.textContent = message;
    msg.style.cssText = "font-size:14px;color:var(--text-primary);text-align:center;letter-spacing:1px;line-height:1.6;margin:0";
    var actions = document.createElement("div");
    actions.className = "collect-modal-actions";
    actions.style.justifyContent = "center";
    actions.style.marginTop = "20px";
    var btn = document.createElement("button");
    btn.className = "collect-modal-btn collect-modal-btn-confirm";
    body.appendChild(msg);
    actions.appendChild(btn);
    card.appendChild(header);
    card.appendChild(body);
    card.appendChild(actions);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    function close() { overlay.style.opacity = "0"; overlay.style.transition = "opacity 0.3s"; setTimeout(function () { overlay.remove(); }, 300); }
    if (onConfirm) {
      btn.textContent = "进入下一步";
      btn.addEventListener("click", function () { close(); onConfirm(); });
    } else {
      btn.textContent = "确定";
      var timer = setTimeout(close, 3000);
      btn.addEventListener("click", close);
      overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    }
  }

  /* ---------------- 步骤条（纯展示：仅根据 step 数据刷新样式） ---------------- */

  function updateStepBar(step) {
    var items = document.querySelectorAll(".step-item");
    for (var i = 0; i < items.length; i++) {
      var s = parseInt(items[i].getAttribute("data-step-num"), 10);
      if (s < step) {
        items[i].className = "step-item step-done";
        items[i].setAttribute("data-status", "done");
      } else if (s === step) {
        items[i].className = "step-item step-current";
        items[i].setAttribute("data-status", "current");
      } else {
        items[i].className = "step-item";
        items[i].setAttribute("data-status", "pending");
      }
    }
    var connectors = document.querySelectorAll(".step-connector");
    for (var j = 0; j < connectors.length; j++) {
      connectors[j].classList.toggle("conn-done", j + 1 < step);
    }
    proj.currentStep = step;
  }

  function setBtnNextHandler(fn) {
    var btn = document.getElementById("btnNext");
    if (!btn) return;
    if (_btnNextHandler) btn.removeEventListener("click", _btnNextHandler);
    _btnNextHandler = fn;
    btn.addEventListener("click", _btnNextHandler);
  }

  /* ---------------- 面板加载分发 ---------------- */

  var panels = {};

  function registerPanel(step, initFn) {
    panels[step] = initFn;
  }

  function cleanupAnimations() {
    if (state.ringChart) { state.ringChart.dispose(); state.ringChart = null; }
    if (state.filterResizeHandler) { window.removeEventListener("resize", state.filterResizeHandler); state.filterResizeHandler = null; }
  }

  function initPanel(step) {
    cleanupAnimations();
    if (panels[step]) panels[step]();
  }

  function loadPanel(step) {
    var url = "/api/project/" + proj.nameEncoded + "/panel/" + step;
    panelBody.innerHTML = '<div class="content-placeholder"><span class="content-placeholder-text">加载中...</span></div>';

    // 第 3 步会把「视频/照片」模式滑块移到全局底部栏（acModeSwitch），
    // 进入其它步骤时应清除，避免残留到交付（step4）等页面。
    if (step !== 3) {
      var leftover = document.getElementById("acModeSwitch");
      if (leftover && leftover.parentNode) leftover.parentNode.removeChild(leftover);
    }

    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (html) {
        panelBody.innerHTML = html;
        updateStepBar(step);
        initPanel(step);
      })
      .catch(function (err) {
        panelBody.innerHTML =
          '<div class="content-placeholder"><span class="content-placeholder-text">加载失败：' +
          err.message + "</span></div>";
      });
  }

  /* ---------------- 菜单 ---------------- */

  function bindMenu() {
    menuBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      dropdown.classList.toggle("show");
    });

    dropdown.addEventListener("click", function (e) {
      var item = e.target.closest(".project-dropdown-item");
      if (!item) return;
      var action = item.getAttribute("data-action");
      dropdown.classList.remove("show");
      if (action === "back") {
        window.location.href = "/main";
      }
    });

    document.addEventListener("click", function () {
      dropdown.classList.remove("show");
    });
  }

  /* ---------------- SSE 事件总线 ---------------- */

  var _batchSSE = null;
  var sseHandlers = {};

  function registerSseHandler(type, fn) {
    sseHandlers[type] = fn;
  }

  function ensureSSE(pname) {
    // 如果已经连接且未关闭，不重复创建
    if (_batchSSE && _batchSSE.readyState !== EventSource.CLOSED) return;
    var url = "/api/project/" + pname + "/events";
    _batchSSE = new EventSource(url);

    // 连接建立后主动拉取当前复制进度（页面刷新后立即恢复进度条）
    _batchSSE.onopen = function () {
      fetch("/api/project/" + pname + "/collect-copy-progress")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data && data.status && data.status !== "not_found") {
            var h = sseHandlers.copy;
            if (h) h(data);
          }
        })
        .catch(function () {});
    };

    _batchSSE.addEventListener("batch", function (e) {
      try {
        var data = JSON.parse(e.data);
        delete data._type;
        updateBatchUI(data);
        if (data.status === "done") {
          // 仅当仍在 批处理/废片 阶段（step<3）才提示并可跳回 step2；
          // 已进入 step3 后的重放/迟到事件一律不弹窗、不跳转（防止闪退回 step2）
          if (!proj || proj.currentStep < 3) {
            showNotice("success", data.message || "全部处理完成！", function () {
              fetch("/api/project/" + pname + "/progress", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ step: 2 }),
              }).then(function () { loadPanel(2); }).catch(function () { loadPanel(2); });
            });
          }
        }
        if (data.status === "error") {
          showNotice("error", data.message || "处理出错");
        }
      } catch (err) {}
    });

    _batchSSE.addEventListener("copy", function (e) {
      try {
        var data = JSON.parse(e.data);
        delete data._type;
        var h = sseHandlers.copy;
        if (h) h(data);
      } catch (err) {}
    });

    _batchSSE.addEventListener("deliver", function (e) {
      try {
        var data = JSON.parse(e.data);
        delete data._type;
        if (proj && proj.currentStep !== 4) return;
        var h = sseHandlers.deliver;
        if (h) h(data);
      } catch (err) {}
    });

    _batchSSE.addEventListener("video_tagger", function (e) {
      try {
        var data = JSON.parse(e.data);
        delete data._type;
        if (proj && proj.currentStep !== 3) return;
        var h = sseHandlers.video_tagger;
        if (h) h(data);
      } catch (err) {}
    });

    _batchSSE.addEventListener("photo_classify", function (e) {
      try {
        var data = JSON.parse(e.data);
        delete data._type;
        if (proj && proj.currentStep !== 3) return;
        var h = sseHandlers.photo_classify;
        if (h) h(data);
      } catch (err) {}
    });
  }

  /* ---------------- 批处理状态（指示灯 + 文件夹进度条） ---------------- */

  function updateBatchUI(data) {
    // 状态灯: locked(蓝) → processing(红脉冲) → 回到 locked
    // photo_active → indCopy(照片处理) / video_active → indZip(视频压缩) / watermark_active → indWater(水印添加)
    var indIds = ["indCopy", "indZip", "indWater"];
    var stages = ["photo", "video", "watermark"];
    var grp = document.getElementById("indicatorGroup");
    var anyActive = false;
    for (var i = 0; i < stages.length; i++) {
      var el = document.getElementById(indIds[i]);
      if (!el) continue;
      var flag = data[stages[i] + "_active"];
      if (flag === true) {
        el.classList.add("processing");
        anyActive = true;
      } else {
        el.classList.remove("processing");
      }
    }
    if (grp) grp.classList.toggle("processing", anyActive);

    // 更新文件夹进度条: 遍历所有 folder-item，按 data-name 属性匹配
    if (data.folder_name) {
      var items = document.querySelectorAll('.folder-item');
      for (var i = 0; i < items.length; i++) {
        var item = items[i];
        if (item.getAttribute('data-name') === data.folder_name) {
          var fill = item.querySelector('.progress-fill');
          if (fill && data.folder_total > 0) {
            fill.style.width = (data.folder_done / data.folder_total * 100) + '%';
          }
          break;
        }
      }
    }
  }

  /* ---------------- 指示灯 ---------------- */

  function lightUpIndicators(immediate) {
    var ids = ["indCopy", "indZip", "indWater"];
    for (var i = 0; i < ids.length; i++) {
      var el = document.getElementById(ids[i]);
      if (!el) continue;
      if (immediate) {
        el.classList.add("locked");
      } else {
        (function(el, idx) {
          setTimeout(function() {
            el.classList.add("locked");
          }, idx * 200);
        })(el, i);
      }
    }
    var group = document.getElementById("indicatorGroup");
    if (group) group.classList.add("running");
  }

  /* ---------------- 启动 ---------------- */

  function init() {
    AppEvents.on("refresh:panel", function () {
      if (proj && proj.currentStep) loadPanel(proj.currentStep);
    });
    AppEvents.on("refresh:stepbar", function () {
      if (proj) updateStepBar(proj.currentStep);
    });

    bindMenu();
    if (proj && proj.currentStep) {
      loadPanel(proj.currentStep);
    }
  }

  window.ProjectCommon = {
    proj: proj,
    state: state,
    init: init,
    showNotice: showNotice,
    updateStepBar: updateStepBar,
    setBtnNextHandler: setBtnNextHandler,
    registerPanel: registerPanel,
    registerSseHandler: registerSseHandler,
    ensureSSE: ensureSSE,
    loadPanel: loadPanel,
    lightUpIndicators: lightUpIndicators,
    updateBatchUI: updateBatchUI,
  };
})();
