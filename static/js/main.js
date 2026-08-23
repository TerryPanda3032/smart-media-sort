(function () {
  "use strict";

  // 应用模式默认窗口尺寸：基于当前窗口一次性调整（竖向缩小 15%，横向加大 5%）。
  // 用 sessionStorage 标记，同一窗口刷新/跳转不再重复缩放，避免尺寸连续累积变小。
  try {
    if (window.resizeTo && !sessionStorage.getItem("sort2_resized")) {
      var _w = window.outerWidth || window.innerWidth || 1000;
      var _h = window.outerHeight || window.innerHeight || 700;
      window.resizeTo(Math.round(_w * 1.2), Math.round(_h * 0.85));
      sessionStorage.setItem("sort2_resized", "1");
    }
  } catch (e) { /* 忽略：普通浏览器/受限环境无需调整 */ }

  function showNotice(type, message) {
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
    btn.textContent = "确定";
    body.appendChild(msg);
    actions.appendChild(btn);
    card.appendChild(header);
    card.appendChild(body);
    card.appendChild(actions);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    var timer = setTimeout(close, 3000);
    function close() { clearTimeout(timer); overlay.style.opacity = "0"; overlay.style.transition = "opacity 0.3s"; setTimeout(function () { overlay.remove(); }, 300); }
    btn.addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
  }

  function showConfirm(message, onOk) {
    var modal = document.getElementById("confirmModal");
    var msgEl = document.getElementById("confirmMessage");
    var okBtn = document.getElementById("confirmOkBtn");
    var cancelBtn = document.getElementById("confirmCancelBtn");
    if (!modal || !msgEl || !okBtn || !cancelBtn) return;
    msgEl.textContent = message;
    modal.style.display = "";
    function cleanup() {
      modal.style.display = "none";
      okBtn.removeEventListener("click", onOkHandler);
      cancelBtn.removeEventListener("click", onCancelHandler);
      modal.removeEventListener("click", onOverlayClick);
    }
    function onOkHandler() { cleanup(); if (onOk) onOk(); }
    function onCancelHandler() { cleanup(); }
    function onOverlayClick(e) { if (e.target === modal) cleanup(); }
    okBtn.addEventListener("click", onOkHandler);
    cancelBtn.addEventListener("click", onCancelHandler);
    modal.addEventListener("click", onOverlayClick);
  }

  var CONFIG_URL = "/api/config";
  var PROJECTS_URL = "/api/projects";

  var projects = [];

  var projectBody = document.getElementById("projectBody");
  var emptyState = document.getElementById("emptyState");
  var settingsOverlay = document.getElementById("settingsOverlay");
  var settingsForm = document.getElementById("settingsForm");
  var settingsCancel = document.getElementById("settingsCancel");
  var settingsBtn = document.getElementById("settingsBtn");
  var browseBtn = document.getElementById("browseBtn");
  var browseFfmpegBtn = document.getElementById("browseFfmpegBtn");
  var testStatus = document.getElementById("testStatus");
  var saveBtn = document.getElementById("saveBtn");
  var dropdownMenu = document.getElementById("dropdownMenu");
  var newProjectOverlay = document.getElementById("newProjectOverlay");
  var newProjectBtn = document.getElementById("newProjectBtn");
  var newProjectConfirm = document.getElementById("newProjectConfirm");
  var newProjectCancel = document.getElementById("newProjectCancel");
  var newProjectName = document.getElementById("newProjectName");

  function syncEmptyState() {
    var hasRows = projectBody && projectBody.children.length > 0;
    if (emptyState) emptyState.style.display = hasRows ? "none" : "flex";
  }

  function init() {
    AppEvents.on("refresh:projects", loadProjects);
    setInterval(loadProjects, 30000);
    projectBody.addEventListener("htmx:afterSwap", syncEmptyState);
    checkConfig();
    bindEvents();
  }

  function checkConfig() {
    fetch(CONFIG_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.exists) {
          fillSettingsForm(data.config);
          showOverlay(settingsOverlay);
          return;
        }
        if (data.config) fillSettingsForm(data.config);
        loadProjects();
      })
      .catch(function () {
        showOverlay(settingsOverlay);
      });
  }

  function fillSettingsForm(config) {
      var els = settingsForm.elements;
      if (config.work_dir != null) els.work_dir.value = config.work_dir;
      if (config.api_url != null) els.api_url.value = config.api_url;
      if (config.api_key != null) els.api_key.value = config.api_key;
      if (config.model != null) els.model.value = config.model;
      if (config.fast_model != null) els.fast_model.value = config.fast_model;
      if (config.fast_model_no_cot != null) els.fast_model_no_cot.checked = config.fast_model_no_cot;
      if (config.reasoning_effort != null) els.reasoning_effort.value = config.reasoning_effort;
      if (config.ffmpeg_path != null) els.ffmpeg_path.value = config.ffmpeg_path;
  }

  function loadProjects() {
    fetch(PROJECTS_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        projects = data.projects || [];
        renderTable();
      })
      .catch(function () {
        renderTable();
      });
  }

  function renderTable() {
    if (projects.length === 0) {
      projectBody.innerHTML = "";
      emptyState.style.display = "flex";
      return;
    }
    emptyState.style.display = "none";

    var html = "";
    for (var i = 0; i < projects.length; i++) {
      var p = projects[i];
      var idx = i + 1;
      html += "<tr>";
      html += '<td class="col-index">' + idx + "</td>";
      html += '<td class="col-name"><a href="/project/' + escAttr(encodeURIComponent(p.name)) + '" class="project-link">' + esc(p.name) + "</a></td>";
      html += '<td class="col-date">' + esc(p.created_at) + "</td>";
      html += '<td class="col-action">';
      html += '<button class="btn-dots" data-path="' + escAttr(p.path) + '">';
      html += '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><circle cx="8" cy="3" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="8" cy="13" r="1.5"/></svg>';
      html += "</button></td>";
      html += "</tr>";
    }
    projectBody.innerHTML = html;
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function escAttr(s) {
    return s.replace(/"/g, "&quot;");
  }

  function bindEvents() {
    settingsForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var els = settingsForm.elements;
      var data = {
        work_dir: els.work_dir.value.trim(),
        api_url: els.api_url.value.trim(),
        api_key: els.api_key.value.trim(),
        api_key_masked: true,
        model: els.model.value.trim(),
        fast_model: els.fast_model.value.trim(),
        fast_model_no_cot: els.fast_model_no_cot.checked,
        reasoning_effort: els.reasoning_effort.value,
        ffmpeg_path: els.ffmpeg_path.value.trim(),
      };

      saveBtn.disabled = true;
      saveBtn.textContent = "测试中…";
      testStatus.textContent = "";
      testStatus.className = "test-status";

      fetch(CONFIG_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      })
        .then(function (r) { return r.json(); })
        .then(function () {
          return fetch("/api/test-connection", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
        })
        .then(function (r) { return r.json(); })
        .then(function (result) {
          if (result.status === "ok") {
            testStatus.textContent = "✅ " + result.message;
            testStatus.className = "test-status success";
            // 让用户看到成功提示再关闭（1.2s 后移除 show 触发 fade-out）
            setTimeout(function () {
              hideOverlay(settingsOverlay);
              // 等 fade-out 动画完成后再刷新项目列表
              setTimeout(function () { AppEvents.emit("refresh:projects"); }, 400);
            }, 1200);
          } else {
            testStatus.textContent = "❌ " + result.message;
            testStatus.className = "test-status error";
          }
        })
        .catch(function (err) {
          testStatus.textContent = "❌ 测试请求失败：" + (err.message || "");
          testStatus.className = "test-status error";
        })
        .finally(function () {
          saveBtn.disabled = false;
          saveBtn.textContent = "保存并测试";
        });
    });

    browseBtn.addEventListener("click", function () {
      browseBtn.disabled = true;
      browseBtn.textContent = "选择中…";
      fetch("/api/pick-folder", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === "ok" && data.path) {
            settingsForm.elements.work_dir.value = data.path;
          }
        })
        .catch(function () {
          testStatus.textContent = "❌ 无法打开文件夹选择窗口";
          testStatus.className = "test-status error";
        })
        .finally(function () {
          browseBtn.disabled = false;
          browseBtn.textContent = "浏览";
        });
    });

    browseFfmpegBtn.addEventListener("click", function () {
      browseFfmpegBtn.disabled = true;
      browseFfmpegBtn.textContent = "选择中…";
      fetch("/api/pick-file", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === "ok" && data.path) {
            settingsForm.elements.ffmpeg_path.value = data.path;
          }
        })
        .catch(function () {
          testStatus.textContent = "❌ 无法打开文件选择窗口";
          testStatus.className = "test-status error";
        })
        .finally(function () {
          browseFfmpegBtn.disabled = false;
          browseFfmpegBtn.textContent = "浏览";
        });
    });

    settingsCancel.addEventListener("click", function () {
      hideOverlay(settingsOverlay);
      if (projects.length === 0) AppEvents.emit("refresh:projects");
    });

    settingsBtn.addEventListener("click", function () {
      fetch(CONFIG_URL)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.config) fillSettingsForm(data.config);
          showOverlay(settingsOverlay);
        })
        .catch(function () {
          showOverlay(settingsOverlay);
        });
    });

    projectBody.addEventListener("click", function (e) {
      var btn = e.target.closest(".btn-dots");
      if (btn) {
        e.stopPropagation();
        showDropdown(btn, btn.getAttribute("data-path"));
      }
    });

    dropdownMenu.addEventListener("click", function (e) {
      var item = e.target.closest(".dropdown-item");
      if (!item) return;
      var action = item.getAttribute("data-action");
      var path = dropdownMenu.getAttribute("data-path");
      hideDropdown();
      if (action === "open") {
        fetch("/api/project/open", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: path }),
        }).catch(function () {});
      } else if (action === "delete") {
        showConfirm("确定要删除此项目文件夹吗？", function () {
          fetch("/api/project/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: path }),
          })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d.status === "ok") {
                showNotice("success", "删除成功");
                AppEvents.emit("refresh:projects");
              } else {
                showNotice("error", d.message || "删除失败");
              }
            });
        });
      }
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest(".dropdown-menu") && !e.target.closest(".btn-dots")) {
        hideDropdown();
      }
    });

    newProjectBtn.addEventListener("click", function () {
      newProjectName.value = "";
      showOverlay(newProjectOverlay);
      setTimeout(function () { newProjectName.focus(); }, 100);
    });

    newProjectConfirm.addEventListener("click", function () {
      var name = newProjectName.value.trim();
      if (!name) return;
      fetch("/api/project/new", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === "ok") {
            hideOverlay(newProjectOverlay);
            showNotice("success", "创建成功");
            AppEvents.emit("refresh:projects");
          } else {
            showNotice("error", data.message || "创建失败");
          }
        })
        .catch(function () {
          showNotice("error", "创建请求失败，请检查后端是否运行");
        });
    });

    newProjectCancel.addEventListener("click", function () {
      hideOverlay(newProjectOverlay);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        hideOverlay(settingsOverlay);
        hideOverlay(newProjectOverlay);
        hideDropdown();
      }
    });
  }

  function showOverlay(el) { el.classList.add("show"); }

  function hideOverlay(el) { el.classList.remove("show"); }

  function showDropdown(btn, path) {
    var rect = btn.getBoundingClientRect();
    dropdownMenu.setAttribute("data-path", path);
    dropdownMenu.style.left = Math.round(rect.right - 140) + "px";
    dropdownMenu.style.top = Math.round(rect.bottom + 4) + "px";
    dropdownMenu.classList.add("show");
  }

  function hideDropdown() {
    dropdownMenu.classList.remove("show");
  }

  init();
})();
