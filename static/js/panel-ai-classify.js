/* ===========================================================================
   AI 分类面板 (step 3) — 视频标签提取、结果渲染
   依赖 window.ProjectCommon（project-common.js）
   照片分类模块已移除，待重做
   =========================================================================== */
(function () {
  "use strict";

  var PC = window.ProjectCommon;
  var proj = PC.proj;

  var _acStartBtn = null;
  var _acNumRaf = null;
  var _acRowStates = {};
  var _acRunning = false;   // 任一分类任务进行中
  var _acMode = "video";    // 当前分类模式：video / photo

  function initAiClassifyPanel() {
    // 隐藏底部指示灯
    var indGroup = document.getElementById("indicatorGroup");
    if (indGroup) indGroup.style.display = "none";

    PC.setBtnNextHandler(function () {
      // 照片分类进行中禁止跳转；分类完成后进入交付(step4)
      if (_acpExecuting) {
        PC.showNotice("error", "照片分类进行中，完成后可进入下一步");
        return;
      }
      if (!proj) return;
      PC.showNotice("success", "进入交付", function () {
        fetch("/api/project/" + proj.nameEncoded + "/progress", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: 4 }),
        }).then(function () { PC.loadPanel(4); }).catch(function () { PC.loadPanel(4); });
      });
    });

    bindAcVideoStart();
    bindAcModeSwitch();
    bindAcFolderTree();
    moveAcThumb();
    initAcpPlan();

    // 确保 SSE 已连接
    if (proj) PC.ensureSSE(proj.nameEncoded);

    // 素材前置初始化（幂等兜底）：老项目 step 已是 3 但未提取时自动补齐
    initPhotoMaterial().then(function () {
      loadAcVideos();
      loadAcProgress();
    });
  }

  function initPhotoMaterial() {
    if (!proj) return Promise.resolve(false);
    return fetch("/api/project/" + proj.nameEncoded + "/photo-material-init", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.status !== "ok") return false;
        return true;
      })
      .catch(function () { return false; });
  }

  /* ---------- 视频分类：手动开始 ---------- */

  function bindAcVideoStart() {
    _acStartBtn = document.getElementById("acVideoStart");
    if (!_acStartBtn || !proj) return;
    _acStartBtn.addEventListener("click", function () {
      if (_acStartBtn.disabled) return;
      fetch("/api/project/" + proj.nameEncoded + "/video-tagger/start", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.status !== "ok") {
            PC.showNotice("error", d.message || "启动视频分类失败");
            return;
          }
          setAcRunning(true);
        })
        .catch(function () {
          PC.showNotice("error", "启动视频分类失败");
        });
    });
  }

  function setAcRunning(running) {
    _acRunning = running;
    var ast = document.getElementById("acAstrolabe");
    if (ast) ast.classList.toggle("is-running", running);
    if (_acStartBtn) _acStartBtn.disabled = running;
    // 任务进行中锁定模式滑块，禁止切换视频/照片
    var sw = document.getElementById("acModeSwitch");
    if (sw) sw.classList.toggle("is-locked", running);
  }

  /* ---------- 分类模式切换：药丸滑块（视频 / 照片） ---------- */

  function moveAcThumb() {
    var sw = document.getElementById("acModeSwitch");
    var thumb = document.getElementById("acModeThumb");
    if (!sw || !thumb) return;
    var active = sw.querySelector(".ac-mode-item.is-active");
    if (!active) return;
    thumb.style.width = active.offsetWidth + "px";
    // 滑块定位到当前激活项（相对容器 padding 起点）
    thumb.style.transform = "translateX(" + (active.offsetLeft) + "px)";
  }

  function switchAcMode(mode) {
    if (mode === _acMode) return;
    if (_acRunning) {
      PC.showNotice("error", "任务进行中，无法切换分类模式");
      return;
    }
    _acMode = mode;
    var sw = document.getElementById("acModeSwitch");
    if (sw) {
      var items = sw.querySelectorAll(".ac-mode-item");
      for (var i = 0; i < items.length; i++) {
        items[i].classList.toggle("is-active", items[i].getAttribute("data-mode") === mode);
      }
    }
    var astrolabe = document.getElementById("acAstrolabe");
    var treePanel = document.getElementById("acTreePanel");
    var videoPanel = document.getElementById("acVideoPanel");
    var photoPanel = document.getElementById("acPhotoPanel");
    var isVideo = mode === "video";
    if (astrolabe) astrolabe.style.display = isVideo ? "" : "none";
    if (treePanel) treePanel.style.display = isVideo ? "none" : "";
    if (videoPanel) videoPanel.style.display = isVideo ? "" : "none";
    if (photoPanel) photoPanel.style.display = isVideo ? "none" : "";
    if (!isVideo) loadAcFolderTree();
    moveAcThumb();
  }

  function bindAcModeSwitch() {
    var sw = document.getElementById("acModeSwitch");
    if (!sw) return;
    var items = sw.querySelectorAll(".ac-mode-item");
    for (var i = 0; i < items.length; i++) {
      items[i].addEventListener("click", function () {
        switchAcMode(this.getAttribute("data-mode"));
      });
    }
  }

  /* ---------- 照片模式左栏：文件夹结构树（渲染逻辑取自 abc，原样） ---------- */

  // ---- Inline SVG Icons（abc 原样） ----
  var AC_TREE_ICONS = {
    folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>',
    folderOpen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 14l1.5-4.5A2 2 0 0 1 9.4 8H20"></path><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>',
    chevDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>',
    chevRight: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>',
    image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>',
    fileCode: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><polyline points="10 13 8 15 10 17"></polyline><polyline points="14 13 16 15 14 17"></polyline></svg>',
    file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>'
  };

  var _acTreeData = [];      // 树数据：{ name, type, fileType, expanded, children }
  var _acTreeLoaded = false; // 接口只拉一次

  function bindAcFolderTree() {
    var searchInput = document.getElementById("search-input");
    if (searchInput) {
      searchInput.addEventListener("input", function () {
        var treeContainer = document.getElementById("tree-container");
        if (!treeContainer) return;
        var query = this.value.toLowerCase().trim();
        var items = treeContainer.querySelectorAll(".tree-item");
        for (var i = 0; i < items.length; i++) {
          var nameEl = items[i].querySelector(".tree-name");
          if (!nameEl) continue;
          var match = !query || nameEl.textContent.toLowerCase().indexOf(query) !== -1;
          items[i].style.display = match ? "" : "none";
        }
      });
    }
  }

  // 接口节点 {name, type, fileType, children} → abc 渲染节点（补 expanded）
  function toAcTreeNode(node, depth) {
    return {
      name: node.name,
      type: node.type || "folder",
      fileType: node.fileType,
      expanded: depth < 1,
      children: (node.children || []).map(function (c) { return toAcTreeNode(c, depth + 1); })
    };
  }

  function loadAcFolderTree() {
    if (_acTreeLoaded || !proj) return;
    _acTreeLoaded = true;
    fetch("/api/project/" + proj.nameEncoded + "/photo-folder-tree")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || d.status !== "ok" || !d.tree) return;
        _acTreeData = [toAcTreeNode(d.tree, 0)];
        acTreeRefresh();
      })
      .catch(function () {});
  }

  function acMakeSVG(iconKey, size) {
    var span = document.createElement("span");
    span.innerHTML = AC_TREE_ICONS[iconKey];
    var svg = span.firstChild;
    if (size) { svg.setAttribute("width", size); svg.setAttribute("height", size); }
    return svg;
  }

  function acBuildPath(prefix, name) { return prefix ? prefix + "/" + name : name; }

  // ---- renderNode（abc 原样；纯显示：文件夹可展开，文件无交互） ----
  function acRenderNode(node, depth, parentPath) {
    var filePath = acBuildPath(parentPath, node.name);
    var isFolder = node.type === "folder";
    var indent = depth * 18;

    var item = document.createElement("div");
    item.className = "tree-item";
    item.setAttribute("data-path", filePath);

    var row = document.createElement("div");
    row.className = "tree-row";
    row.style.paddingLeft = (8 + indent) + "px";

    // Chevron
    var chevSpan = document.createElement("span");
    chevSpan.className = "chev";
    if (isFolder) {
      chevSpan.appendChild(acMakeSVG(node.expanded ? "chevDown" : "chevRight"));
    }
    row.appendChild(chevSpan);

    // Icon
    var iconSpan = document.createElement("span");
    if (isFolder) {
      iconSpan.className = "ticon folder";
      iconSpan.appendChild(acMakeSVG(node.expanded ? "folderOpen" : "folder"));
    } else if (node.fileType === "image") {
      iconSpan.className = "ticon image";
      iconSpan.appendChild(acMakeSVG("image"));
    } else if (node.fileType === "json") {
      iconSpan.className = "ticon json";
      iconSpan.appendChild(acMakeSVG("fileCode"));
    } else {
      iconSpan.className = "ticon file";
      iconSpan.appendChild(acMakeSVG("file"));
    }
    row.appendChild(iconSpan);

    // Name
    var nameSpan = document.createElement("span");
    nameSpan.className = "tree-name";
    nameSpan.textContent = node.name;
    row.appendChild(nameSpan);

    // Click：仅文件夹展开/收起（显示需要），文件不响应
    (function(nd) {
      row.addEventListener("click", function(e) {
        e.stopPropagation();
        if (nd.type === "folder") {
          nd.expanded = !nd.expanded;
          acTreeRefresh();
        }
      });
    })(node);

    item.appendChild(row);

    if (isFolder && node.children) {
      var childWrap = document.createElement("div");
      childWrap.className = "tree-children";
      childWrap.style.maxHeight = node.expanded ? "5000px" : "0";
      childWrap.style.opacity = node.expanded ? "1" : "0";
      for (var i = 0; i < node.children.length; i++) {
        childWrap.appendChild(acRenderNode(node.children[i], depth + 1, filePath));
      }
      item.appendChild(childWrap);
    }
    return item;
  }

  function acTreeRefresh() {
    var treeContainer = document.getElementById("tree-container");
    if (!treeContainer) return;
    treeContainer.innerHTML = "";
    for (var i = 0; i < _acTreeData.length; i++) {
      treeContainer.appendChild(acRenderNode(_acTreeData[i], 0, ""));
    }
  }

  /* ---------- SSE 逐视频事件：行内进度条 → 标签/失败 ---------- */

  function handleAcVideoEvent(data) {
    if (data.status === "running") {
      setAcRunning(true);
      if (typeof data.percent === "number") tweenAcNumber(data.percent);
    } else if (data.status === "video_start") {
      setAcRowState(data.path, "running", null, false);
    } else if (data.status === "video_done") {
      setAcRowState(data.path, "done", data.tags, data.failed);
    } else if (data.status === "done") {
      setAcRunning(false);
      tweenAcNumber(100);
      PC.showNotice("success", data.message || "视频分类完成");
      loadAcResult();
    } else if (data.status === "error") {
      setAcRunning(false);
      PC.showNotice("error", data.message || "视频分类出错");
    }
  }

  function setAcRowState(path, state, tags, failed) {
    var box = document.getElementById("acVideoList");
    if (!box) return;
    var rows = box.querySelectorAll(".ac-video-row");
    var target = null;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute("data-path") === path) {
        target = rows[i];
        break;
      }
    }
    if (!target) {
      // 列表尚未渲染，先暂存，待列表出现后补渲染
      _acRowStates[path] = { state: state, tags: tags, failed: failed };
      return;
    }
    delete _acRowStates[path];
    var cell = target.querySelector(".ac-video-status");
    if (!cell) return;
    target.classList.toggle("is-running", state === "running");
    cell.innerHTML = "";
    if (state === "running") {
      var track = document.createElement("div");
      track.className = "ac-ind-track is-active";
      var bar = document.createElement("div");
      bar.className = "ac-ind-bar";
      track.appendChild(bar);
      cell.appendChild(track);
    } else if (failed) {
      var fail = document.createElement("span");
      fail.className = "ac-tag ac-tag-fail";
      fail.textContent = "失败";
      cell.appendChild(fail);
    } else {
      var list = tags || [];
      for (var j = 0; j < list.length; j++) {
        var tag = document.createElement("span");
        tag.className = "ac-tag";
        tag.textContent = list[j];
        cell.appendChild(tag);
      }
    }
  }

  /* ---------- 左侧数字实时进度（缓动计数，与 step2 一致） ---------- */

  function tweenAcNumber(target) {
    var el = document.getElementById("acProgressNum");
    if (!el) return;
    var start = parseInt(el.textContent, 10) || 0;
    if (target === start) return;
    if (_acNumRaf) cancelAnimationFrame(_acNumRaf);
    var startTime = performance.now();
    var duration = 1200;
    function update(now) {
      var t = Math.min((now - startTime) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = String(Math.floor(start + (target - start) * eased));
      if (t < 1) {
        _acNumRaf = requestAnimationFrame(update);
      } else {
        _acNumRaf = null;
      }
    }
    _acNumRaf = requestAnimationFrame(update);
  }

  /* ---------- 视频列表与结果标签 ---------- */

  function loadAcVideos() {
    if (!proj) return;
    fetch("/api/project/" + proj.nameEncoded + "/videos")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var list = d.videos || [];
        var box = document.getElementById("acVideoList");
        var empty = document.getElementById("acVideoEmpty");
        if (!box) return;
        box.innerHTML = "";
        if (list.length === 0) {
          if (empty) empty.style.display = "";
          return;
        }
        if (empty) empty.style.display = "none";
        for (var i = 0; i < list.length; i++) {
          var row = document.createElement("div");
          row.className = "ac-video-row";
          row.setAttribute("data-path", list[i]);
          var name = document.createElement("div");
          name.className = "ac-video-name";
          name.title = list[i];
          name.textContent = list[i];
          var status = document.createElement("div");
          status.className = "ac-video-status";
          row.appendChild(name);
          row.appendChild(status);
          box.appendChild(row);
        }
        // 补渲染列表出现前到达的逐视频事件
        for (var p in _acRowStates) {
          if (!_acRowStates.hasOwnProperty(p)) continue;
          var st = _acRowStates[p];
          setAcRowState(p, st.state, st.tags, st.failed);
        }
        loadAcResult();
      })
      .catch(function () {});
  }

  function loadAcResult() {
    if (!proj) return;
    fetch("/api/project/" + proj.nameEncoded + "/video-tagger/result")
      .then(function (r) { return r.json(); })
      .then(function (d) { renderAcResult(d); })
      .catch(function () {});
  }

  function renderAcResult(data) {
    if (!data || !data.exists) return;
    var tags = data.tags || {};
    var box = document.getElementById("acVideoList");
    if (!box) return;
    var rows = box.querySelectorAll(".ac-video-row");
    for (var i = 0; i < rows.length; i++) {
      var path = rows[i].getAttribute("data-path");
      var list = tags[path];
      if (!list || !list.length) continue;
      var statusEl = rows[i].querySelector(".ac-video-status");
      if (!statusEl || statusEl.childElementCount > 0) continue;
      for (var j = 0; j < list.length; j++) {
        var tag = document.createElement("span");
        tag.className = "ac-tag";
        tag.textContent = list[j];
        statusEl.appendChild(tag);
      }
    }
  }

  /* ---------- 恢复进行中状态 ---------- */

  function loadAcProgress() {
    if (!proj) return;
    fetch("/api/project/" + proj.nameEncoded + "/video-tagger-status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.status === "running") {
          setAcRunning(true);
          if (typeof d.percent === "number") tweenAcNumber(d.percent);
        } else if (d && d.status === "done") {
          setAcRunning(false);
          tweenAcNumber(100);
          loadAcResult();
        } else if (d && d.status === "error") {
          setAcRunning(false);
          loadAcResult();
        } else {
          // 内存进度已清（如后端重启）：结果文件存在则直接加载，对齐 step2 语义
          fetch("/api/project/" + proj.nameEncoded + "/video-tagger/result")
            .then(function (r) { return r.json(); })
            .then(function (s) {
              if (!s || !s.exists) return;
              tweenAcNumber(100);
              setAcRunning(false);
              PC.showNotice("success", "已存在分类结果，直接加载");
            })
            .catch(function () {});
        }
      })
      .catch(function () {});
  }

  /* ===========================================================================
     照片分类 — 计划对话（输入 + 发送 + 跑马灯 + 回复面板）
     =========================================================================== */

  var _acpOriginalRequest = "";       // 保留用户原始要求
  var _acpClarifications = [];        // 历史问答 [{question, answer}]
  var _acpPendingQuestions = [];      // AI 最近一次提出的待回答问题列表
  var _acpLoading = false;            // AI 是否正在回复
  var _acpFinalPlan = null;           // AI 最终确认的计划（点「开始执行」时提交）
  var _acpFinalOriginalRequest = "";  // 计划对应的用户原始要求
  var _acpExecuting = false;          // 照片分类是否正在执行

  function initAcpPlan() {
    var input = document.getElementById("acPlanInput");
    var sendBtn = document.getElementById("acPlanSend");
    var reply = document.getElementById("acPlanReply");
    var error = document.getElementById("acPlanError");
    var retry = document.getElementById("acPlanRetry");

    if (!input || !sendBtn) return;

    // 输入框内容变化 → 自适应高度 + 控制发送按钮启用/禁用
    function updateSendState() {
      sendBtn.disabled = _acpLoading || !input.value.trim();
    }

    function autoGrow() {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 96) + "px";
    }

    input.addEventListener("input", function () {
      autoGrow();
      updateSendState();
    });

    // 发送按钮点击
    sendBtn.addEventListener("click", function () {
      if (_acpLoading || sendBtn.disabled) return;
      doAcpRequest();
    });

    // 回车发送，Shift+Enter 换行
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !_acpLoading && !sendBtn.disabled) {
        e.preventDefault();
        doAcpRequest();
      }
    });

    // 重试按钮
    retry.addEventListener("click", function () {
      retryAcp();
    });

    // 开始执行按钮：接受方案 → 落盘 → 启动分类
    var execBtn = document.getElementById("acPlanExecute");
    if (execBtn) {
      execBtn.addEventListener("click", function () {
        startAcpExecute();
      });
    }

    // 初始状态
    updateSendState();

    // 恢复执行态：任务进行中 → 直接进入执行界面；方案已存 → 恢复计划与执行按钮
    restoreAcpState();
  }

  function showAcpLoading(loading) {
    _acpLoading = loading;
    var inputBar = document.getElementById("acPlanInputBar");
    var input = document.getElementById("acPlanInput");
    var sendBtn = document.getElementById("acPlanSend");
    var error = document.getElementById("acPlanError");

    if (inputBar) inputBar.classList.toggle("is-loading", loading);
    if (input) input.disabled = loading;
    if (sendBtn) {
      sendBtn.disabled = loading || !input.value.trim();
    }
    if (error) error.style.display = "none";
  }

  var _acpLastPayload = null;         // 最近一次请求载荷（用于失败重试）

  /* 将用户输入中的英文直双引号替换为中文弯引号，避免破坏 JSON 结构 */
  function sanitizeText(s) {
    var result = "";
    var open = true;
    for (var i = 0; i < s.length; i++) {
      if (s[i] === '"') {
        result += open ? "\u201C" : "\u201D";
        open = !open;
      } else {
        result += s[i];
      }
    }
    return result;
  }

  function doAcpRequest() {
    var input = document.getElementById("acPlanInput");
    var reply = document.getElementById("acPlanReply");
    var error = document.getElementById("acPlanError");
    if (!input) return;

    var text = sanitizeText(input.value.trim());
    if (!text) return;

    var payload;
    if (!_acpOriginalRequest) {
      // 第一次提交：完整保留用户原始要求
      _acpOriginalRequest = text;
      payload = { original_request: _acpOriginalRequest, clarifications: [] };
    } else {
      // 后续提交：一次性回答多个问题（每行回答一个问题）
      var lines = text.split(/\r?\n/).map(function (s) { return s.trim(); });
      for (var i = 0; i < _acpPendingQuestions.length; i++) {
        _acpClarifications.push({
          questions: [_acpPendingQuestions[i]],
          answer: lines[i] || "",
        });
      }
      _acpPendingQuestions = [];
      payload = {
        original_request: _acpOriginalRequest,
        clarifications: _acpClarifications.slice(),
      };
    }

    // 清空输入框，收起旧回复，恢复默认提示；清空旧执行日志
    input.value = "";
    input.style.height = "auto";
    input.placeholder = "请输入照片整理要求，例如：先把照片分成 A 类和 B 类，再按需继续细分。";
    if (reply) reply.style.display = "none";
    if (error) error.style.display = "none";
    clearAcpLogPanel();

    _acpLastPayload = JSON.parse(JSON.stringify(payload));
    sendAcpPayload(payload);
  }

  function retryAcp() {
    if (_acpLoading || !_acpLastPayload) return;
    sendAcpPayload(JSON.parse(JSON.stringify(_acpLastPayload)));
  }

  function sendAcpPayload(payload) {
    showAcpLoading(true);

    fetch("/api/project/" + proj.nameEncoded + "/photo-classify/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        showAcpLoading(false);
        if (data.status !== "ok" || !data.result) {
          showAcpError(data.message || "请求失败");
          return;
        }
        var result = data.result;
        if (result.status === "question") {
          // AI 有疑问：记录问题列表，显示并等待用户逐行回答
          _acpPendingQuestions = (result.questions && result.questions.length) ? result.questions.slice() : [];
          renderAcpQuestion(result);
          // 恢复输入框供用户回答
          var inp = document.getElementById("acPlanInput");
          if (inp) {
            inp.disabled = false;
            inp.placeholder = "请逐行回答上面的问题（每行回答一个问题），Shift+Enter 换行";
            inp.focus();
          }
          var sendBtn = document.getElementById("acPlanSend");
          if (sendBtn) sendBtn.disabled = false;
        } else if (result.status === "plan") {
          // AI 给出最终计划：暂存供「开始执行」提交
          renderAcpPlan(result);
          finishAcpPlan();
          _acpFinalPlan = result.plan || null;
          _acpFinalOriginalRequest = _acpOriginalRequest;
          _acpOriginalRequest = "";   // 重置，允许新计划
          _acpClarifications = [];
          _acpPendingQuestions = [];
          _acpLastPayload = null;
        } else {
          showAcpError("AI 返回异常状态");
        }
      })
      .catch(function (err) {
        showAcpLoading(false);
        showAcpError("网络错误：" + err.message);
      });
  }

  function finishAcpPlan() {
    // 输入框渐隐消失，原位弹出「开始执行」按钮
    var inputBar = document.getElementById("acPlanInputBar");
    var executeBtn = document.getElementById("acPlanExecute");
    if (inputBar) {
      inputBar.classList.add("acp-fade-out");
      setTimeout(function () {
        if (inputBar) inputBar.style.display = "none";
        if (executeBtn && !_acpExecuting) executeBtn.style.display = "";
      }, 300); // 与渐隐动画时长一致
    } else if (executeBtn) {
      executeBtn.style.display = "";
    }
  }

  /* ---------- 开始执行：接受方案 → 落盘 → 启动 agent 分类 ---------- */

  function startAcpExecute() {
    if (_acpExecuting || !_acpFinalPlan || !proj) return;
    var btn = document.getElementById("acPlanExecute");
    if (btn) btn.disabled = true;
    fetch("/api/project/" + proj.nameEncoded + "/photo-classify/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        original_request: _acpFinalOriginalRequest || _acpOriginalRequest || "",
        plan: _acpFinalPlan,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status !== "ok") {
          if (btn) btn.disabled = false;
          PC.showNotice("error", d.message || "启动分类失败");
          return;
        }
        enterAcpExecuting();
      })
      .catch(function () {
        if (btn) btn.disabled = false;
        PC.showNotice("error", "启动分类失败：网络错误");
      });
  }

  // 进入执行态：显示新执行态UI，隐藏旧元素，初始化圆环
  function enterAcpExecuting() {
    _acpExecuting = true;
    setAcRunning(true); // 锁模式滑块，防切换
    var btn = document.getElementById("acPlanExecute");
    if (btn) { btn.style.display = "none"; btn.disabled = false; }
    var reply = document.getElementById("acPlanReply");
    if (reply) reply.style.display = "none";
    var errBox = document.getElementById("acPlanError");
    if (errBox) errBox.style.display = "none";
    var inputBar = document.getElementById("acPlanInputBar");
    if (inputBar) inputBar.style.display = "none";
    var treePanel = document.getElementById("acTreePanel");
    if (treePanel) treePanel.classList.add("is-classifying");
    var planBox = document.getElementById("acPlan");
    if (planBox) planBox.classList.add("is-classifying");
    // 显示执行态UI
    var execArea = document.getElementById("acExecArea");
    if (execArea) execArea.style.display = "flex";
    // 隐藏旧状态条
    var oldStatus = document.getElementById("acPlanExecStatus");
    if (oldStatus) oldStatus.style.display = "none";
    // 初始化圆环
    initAcpDonutChart();
    refreshAcFolderTree();
  }

  // 退出执行态：撤掉跑马灯，隐藏执行态UI
  function exitAcpExecuting() {
    _acpExecuting = false;
    setAcRunning(false);
    var treePanel = document.getElementById("acTreePanel");
    if (treePanel) treePanel.classList.remove("is-classifying");
    var planBox = document.getElementById("acPlan");
    if (planBox) planBox.classList.remove("is-classifying");
    var execArea = document.getElementById("acExecArea");
    if (execArea) execArea.style.display = "none";
    var oldStatus = document.getElementById("acPlanExecStatus");
    if (oldStatus) oldStatus.style.display = "none";
  }

  // 执行态状态条：静态文字（不滚动字幕），点击进入控制台
  function showAcpExecStatus(text) {
    var barText = document.getElementById("acExecBarText");
    if (barText) {
      barText.innerHTML = "";
      var span = document.createElement("span");
      span.textContent = text || "任务正在执行…";
      barText.appendChild(span);
    }
  }

  /* ---------- 执行日志面板：每轮提示词 / AI 输出 / 工具调用全记录 ---------- */

  var AC_LOG_KIND_LABEL = {
    system: "系统提示词",
    user: "输入",
    assistant: "AI 输出",
    tool_call: "工具调用",
    tool_result: "工具返回",
    image: "附图",
    info: "信息",
    error: "错误",
  };

  function appendAcpLogEntry(entry) {
    // 日志只写入控制台弹窗（点击顶部灯条展开查看），页面下方不再有日志区
    var body = document.getElementById("acExecConsoleBody");
    if (!body) return;
    // 近底部时自动跟随滚动
    var follow = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
    var row = document.createElement("div");
    row.className = "ac-log-entry ac-log-" + (entry.kind || "info");
    var meta = document.createElement("span");
    meta.className = "ac-log-meta";
    var batch = entry.batch ? "批" + entry.batch + " " : "";
    meta.textContent = entry.ts + " " + batch + (AC_LOG_KIND_LABEL[entry.kind] || entry.kind);
    var text = document.createElement("span");
    text.className = "ac-log-text";
    text.textContent = entry.text || "";
    row.appendChild(meta);
    row.appendChild(text);
    body.appendChild(row);
    // 防爆：仅保留最近 800 条 DOM
    while (body.childElementCount > 800) body.removeChild(body.firstChild);
    if (follow) body.scrollTop = body.scrollHeight;
  }

  function clearAcpLogPanel() {
    var body = document.getElementById("acExecConsoleBody");
    if (body) body.innerHTML = "";
  }

  function loadAcpLogs() {
    if (!proj) return;
    fetch("/api/project/" + proj.nameEncoded + "/photo-classify/logs")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.logs || !d.logs.length) return;
        clearAcpLogPanel();
        for (var i = 0; i < d.logs.length; i++) appendAcpLogEntry(d.logs[i]);
      })
      .catch(function () {});
  }

  // API 失败确认弹窗（继续重试 / 停止任务）
  function showAcpApiConfirmDialog(msg) {
    var old = document.getElementById("acApiConfirmOverlay");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var overlay = document.createElement("div");
    overlay.id = "acApiConfirmOverlay";
    overlay.className = "ac-api-confirm-overlay";
    var card = document.createElement("div");
    card.className = "ac-api-confirm-card";
    var title = document.createElement("div");
    title.className = "ac-api-confirm-title";
    title.textContent = "API 调用连续失败";
    var text = document.createElement("div");
    text.className = "ac-api-confirm-text";
    text.textContent = msg || "API 连续调用失败（可能因限流或超时），是否继续重试？";
    var btns = document.createElement("div");
    btns.className = "ac-api-confirm-btns";
    var okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "ac-api-confirm-btn is-ok";
    okBtn.textContent = "继续重试";
    var stopBtn = document.createElement("button");
    stopBtn.type = "button";
    stopBtn.className = "ac-api-confirm-btn is-stop";
    stopBtn.textContent = "停止任务";
    btns.appendChild(okBtn);
    btns.appendChild(stopBtn);
    card.appendChild(title);
    card.appendChild(text);
    card.appendChild(btns);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    function reply(cont) {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      showAcpExecStatus(cont ? "已选择继续，10 秒后重试…" : "已选择停止，正在结束任务…");
      fetch("/api/project/" + proj.nameEncoded + "/photo-classify/retry-confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ "continue": cont }),
      }).catch(function () {});
    }
    okBtn.addEventListener("click", function () { reply(true); });
    stopBtn.addEventListener("click", function () { reply(false); });
  }

  // SSE 事件：photo_classify — 更新状态文案 + 日志追加 + 实时刷新左树 + 圆环进度 + Token
  function handlePhotoClassifyEvent(data) {
    if (!data || !data.status) return;
    if (data.status === "log") {
      if (data.entry) appendAcpLogEntry(data.entry);
      return;
    }
    if (data.status === "api_confirm") {
      showAcpApiConfirmDialog(data.message);
      return;
    }
    if (data.status === "running") {
      if (!_acpExecuting) enterAcpExecuting();
      var txt = data.message || "分类中…";
      if (typeof data.done === "number" && typeof data.total === "number" && data.total > 0) {
        txt += "（" + data.done + "/" + data.total + "）";
      }
      showAcpExecStatus(txt);
      // 圆环进度
      if (typeof data.percent === "number") {
        tweenAcpDonut(data.percent);
      }
      // Token 统计
      if (typeof data.input_tokens === "number" || typeof data.output_tokens === "number") {
        updateAcpTokenStats(data.input_tokens || 0, data.output_tokens || 0);
      }
      refreshAcFolderTree();
    } else if (data.status === "done") {
      // 完成：弹窗提醒，但保留右侧执行态面板不消失（不用 exitAcpExecuting）
      _acpExecuting = false;
      setAcRunning(false);               // 解锁模式滑块、关闭运行灯
      var tP = document.getElementById("acTreePanel");
      if (tP) tP.classList.remove("is-classifying");
      var pB = document.getElementById("acPlan");
      if (pB) pB.classList.remove("is-classifying");
      tweenAcpDonut(100);
      refreshAcFolderTree();
      PC.showNotice("success", data.message || "照片分类完成");
    } else if (data.status === "error") {
      exitAcpExecuting();
      refreshAcFolderTree();
      PC.showNotice("error", data.message || "照片分类出错");
    }
  }

  // 强制重拉文件夹树（执行中每批移动后实时刷新）
  function refreshAcFolderTree() {
    _acTreeLoaded = false;
    loadAcFolderTree();
  }

  // 面板加载时恢复执行态 / 完成态（running / done / error / 未执行 四种终态）
  function restoreAcpState() {
    if (!proj) return;
    fetch("/api/project/" + proj.nameEncoded + "/photo-classify/progress")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var status = d && d.status;
        if (status === "running") {
          restoreAcpExecuting("running", d);
          return;
        }
        if (status === "done") {
          restoreAcpExecuting("done", d);
          return;
        }
        if (status === "error") return; // 出错保持可重新发起
        // 未执行 / 无运行中任务：若已有落盘方案 → 恢复计划展示与执行按钮
        restoreAcpPlanSaved();
      })
      .catch(function () {});
  }

  // 恢复 执行中/已完成 界面：强制切到照片模式并进入执行态（或完成态）。
  // 先临时解除运行锁再切换，规避 SSE 连接后立即推 running → enterAcpExecuting
  // → setAcRunning(true) 锁滑块，导致后续 switchAcMode("photo") 被 _acRunning 拦下的竞态。
  function restoreAcpExecuting(mode, data) {
    _acRunning = false;
    _acpExecuting = false;
    switchAcMode("photo");
    enterAcpExecuting();
    loadAcpLogs();
    // 确保计划预览/开始执行/错误框 不残留
    var btn = document.getElementById("acPlanExecute");
    if (btn) { btn.style.display = "none"; btn.disabled = false; }
    var reply = document.getElementById("acPlanReply");
    if (reply) reply.style.display = "none";
    var errBox = document.getElementById("acPlanError");
    if (errBox) errBox.style.display = "none";
    handlePhotoClassifyEvent(data); // running → 更新进度；done → 进完成态(弹窗、保留面板)
  }

  // 未执行场景：有落盘方案才恢复计划预览 + 开始执行按钮（允许重发起）
  function restoreAcpPlanSaved() {
    if (!proj) return;
    fetch("/api/project/" + proj.nameEncoded + "/photo-classify/plan-saved")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (!s || !s.exists || !s.plan) return;
        if (_acpFinalPlan) return; // 本会话已有新计划，不覆盖
        _acpFinalPlan = s.plan;
        _acpFinalOriginalRequest = s.original_request || "";
        renderAcpPlan({ plan: s.plan });
        finishAcpPlan();
      })
      .catch(function () {});
  }

  function renderAcpQuestion(result) {
    var reply = document.getElementById("acPlanReply");
    if (!reply) return;
    reply.style.display = "";
    var qs = (result.questions && result.questions.length) ? result.questions : [];
    var html = '<div class="ac-plan-label">需要澄清</div>';
    if (qs.length) {
      html += '<ol class="ac-plan-question-list">';
      for (var i = 0; i < qs.length; i++) {
        html += "<li>" + escHtml(qs[i]) + "</li>";
      }
      html += "</ol>";
    }
    html += '<div class="ac-plan-hint">请在输入框中逐行回答上面的问题（每行回答一个问题），回答后发送。</div>';
    reply.innerHTML = html;
    reply.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderAcpPlan(result) {
    var reply = document.getElementById("acPlanReply");
    if (!reply) return;
    reply.style.display = "";
    var plan = result.plan || {};
    var html = '<div class="ac-plan-label">分类计划</div>';

    if (plan.summary) {
      html += '<div class="ac-plan-summary">' + escHtml(plan.summary) + "</div>";
    }

    if (plan.steps && plan.steps.length) {
      html += '<ul class="ac-plan-steps">';
      for (var i = 0; i < plan.steps.length; i++) {
        var s = plan.steps[i];
        html += '<li class="ac-plan-step">';
        html += '<div class="ac-plan-step-head">';
        html += '<span class="ac-plan-step-num">步骤 ' + s.step + "</span>";
        html += '<span class="ac-plan-step-desc">' + escHtml(s.description || "") + "</span>";
        html += "</div>";
        if (s.parent_folder) {
          html += '<div class="ac-plan-step-meta">父目录：' + escHtml(s.parent_folder) + "</div>";
        }
        html += '<div class="ac-plan-step-meta">范围：' + escHtml(s.scope || "") + "</div>";
        if (s.folders && s.folders.length) {
          html += '<ul class="ac-plan-folders">';
          for (var j = 0; j < s.folders.length; j++) {
            var f = s.folders[j];
            html += '<li class="ac-plan-folder">';
            html += '<div class="ac-plan-folder-name">' + escHtml(f.name || "") + "</div>";
            if (f.criteria) {
              html += '<div class="ac-plan-folder-criteria">标准：' + escHtml(f.criteria) + "</div>";
            }
            if (f.operation) {
              html += '<div class="ac-plan-folder-operation">操作：' + escHtml(f.operation) + "</div>";
            }
            html += "</li>";
          }
          html += "</ul>";
        }
        if (s.expected_result) {
          html += '<div class="ac-plan-step-result">' + escHtml(s.expected_result) + "</div>";
        }
        html += "</li>";
      }
      html += "</ul>";
    }

    reply.innerHTML = html;
    reply.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function showAcpError(msg) {
    var error = document.getElementById("acPlanError");
    var msgEl = document.getElementById("acPlanErrorMsg");
    if (error && msgEl) {
      msgEl.textContent = msg;
      error.style.display = "";
    }
  }

  function escHtml(str) {
    if (!str) return "";
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  /* ===========================================================================
     执行态UI：圆环进度条 / Token 更新 / 图片预览 / 旋转 / 控制台弹窗
     =========================================================================== */

  var _acpDonutCanvas = null;
  var _acpDonutCtx = null;
  var _acpDonutPercent = 0;
  var _acpDonutTarget = 0;
  var _acpDonutRaf = null;
  var _acpImageRotation = 0; // 当前旋转角度（0/90/180/270）

  function initAcpDonutChart() {
    var canvas = document.getElementById("acDonutChart");
    if (!canvas) return;
    _acpDonutCanvas = canvas;
    _acpDonutCtx = canvas.getContext("2d");
    _acpDonutPercent = 0;
    _acpDonutTarget = 0;
    // 初始绘制空白圆环
    drawAcpDonut(0);
  }

  function drawAcpDonut(percent) {
    var ctx = _acpDonutCtx;
    if (!ctx) return;
    var canvas = _acpDonutCanvas;
    var w = canvas.width;
    var h = canvas.height;
    var cx = w / 2;
    var cy = h / 2;
    var outerR = 58;
    var innerR = 44;
    var startAngle = -Math.PI / 2;
    var endAngle = startAngle + (percent / 100) * Math.PI * 2;

    ctx.clearRect(0, 0, w, h);

    // 背景圆环（浅灰）
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
    ctx.arc(cx, cy, innerR, Math.PI * 2, 0, true);
    ctx.closePath();
    ctx.fillStyle = "rgba(0,0,0,0.05)";
    ctx.fill();

    // 前景圆环（青瓷绿渐变）
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, startAngle, endAngle);
    ctx.arc(cx, cy, innerR, endAngle, startAngle, true);
    ctx.closePath();
    var gradient = ctx.createLinearGradient(0, 0, w, h);
    gradient.addColorStop(0, "#4A8575");
    gradient.addColorStop(1, "#2EA98E");
    ctx.fillStyle = gradient;
    ctx.fill();

    // 发光描边
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, startAngle, endAngle);
    ctx.strokeStyle = "rgba(46, 169, 142, 0.3)";
    ctx.lineWidth = 2;
    ctx.stroke();

    // 更新中心文字
    var centerEl = document.getElementById("acDonutCenter");
    if (centerEl) {
      centerEl.textContent = Math.round(percent) + "%";
    }
  }

  // 圆环缓动跳字（与视频模式 tweenAcNumber 一致手法）
  function tweenAcpDonut(target) {
    if (target === _acpDonutTarget) return;
    _acpDonutTarget = target;
    if (_acpDonutRaf) cancelAnimationFrame(_acpDonutRaf);
    var start = _acpDonutPercent;
    var startTime = performance.now();
    var duration = 1200;
    function update(now) {
      var t = Math.min((now - startTime) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      var current = start + (target - start) * eased;
      _acpDonutPercent = current;
      drawAcpDonut(current);
      if (t < 1) {
        _acpDonutRaf = requestAnimationFrame(update);
      } else {
        _acpDonutRaf = null;
        _acpDonutPercent = target;
      }
    }
    _acpDonutRaf = requestAnimationFrame(update);
  }

  // 更新 Token 统计
  function updateAcpTokenStats(inputTokens, outputTokens) {
    var inputEl = document.getElementById("acTokenInput");
    var outputEl = document.getElementById("acTokenOutput");
    var totalEl = document.getElementById("acTokenTotal");
    if (inputEl) inputEl.textContent = formatTokenNum(inputTokens || 0);
    if (outputEl) outputEl.textContent = formatTokenNum(outputTokens || 0);
    if (totalEl) totalEl.textContent = formatTokenNum((inputTokens || 0) + (outputTokens || 0));
  }

  function formatTokenNum(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "K";
    return String(n);
  }

  // 修改树节点点击：文件可选中并在预览器中显示图片
  function acRenderNodeWithPreview(node, depth, parentPath) {
    var filePath = acBuildPath(parentPath, node.name);
    var isFolder = node.type === "folder";
    var indent = depth * 18;

    var item = document.createElement("div");
    item.className = "tree-item";
    item.setAttribute("data-path", filePath);

    var row = document.createElement("div");
    row.className = "tree-row";
    row.style.paddingLeft = (8 + indent) + "px";

    // Chevron
    var chevSpan = document.createElement("span");
    chevSpan.className = "chev";
    if (isFolder) {
      chevSpan.appendChild(acMakeSVG(node.expanded ? "chevDown" : "chevRight"));
    }
    row.appendChild(chevSpan);

    // Icon
    var iconSpan = document.createElement("span");
    if (isFolder) {
      iconSpan.className = "ticon folder";
      iconSpan.appendChild(acMakeSVG(node.expanded ? "folderOpen" : "folder"));
    } else if (node.fileType === "image") {
      iconSpan.className = "ticon image";
      iconSpan.appendChild(acMakeSVG("image"));
    } else if (node.fileType === "json") {
      iconSpan.className = "ticon json";
      iconSpan.appendChild(acMakeSVG("fileCode"));
    } else {
      iconSpan.className = "ticon file";
      iconSpan.appendChild(acMakeSVG("file"));
    }
    row.appendChild(iconSpan);

    // Name
    var nameSpan = document.createElement("span");
    nameSpan.className = "tree-name";
    nameSpan.textContent = node.name;
    row.appendChild(nameSpan);

    // Click：文件夹展开/收起，文件选中并在预览器中显示
    (function(nd, fp) {
      row.addEventListener("click", function(e) {
        e.stopPropagation();
        if (nd.type === "folder") {
          nd.expanded = !nd.expanded;
          acTreeRefreshWithPreview();
        } else if (nd.fileType === "image") {
          // 取消其他选中
          var allRows = document.querySelectorAll(".tree-panel .tree-row");
          for (var i = 0; i < allRows.length; i++) {
            allRows[i].classList.remove("selected");
          }
          row.classList.add("selected");
          loadAcpImagePreview(fp);
        }
      });
    })(node, filePath);

    item.appendChild(row);

    if (isFolder && node.children) {
      var childWrap = document.createElement("div");
      childWrap.className = "tree-children";
      childWrap.style.maxHeight = node.expanded ? "5000px" : "0";
      childWrap.style.opacity = node.expanded ? "1" : "0";
      for (var i = 0; i < node.children.length; i++) {
        childWrap.appendChild(acRenderNodeWithPreview(node.children[i], depth + 1, filePath));
      }
      item.appendChild(childWrap);
    }
    return item;
  }

  function acTreeRefreshWithPreview() {
    var treeContainer = document.getElementById("tree-container");
    if (!treeContainer) return;
    treeContainer.innerHTML = "";
    for (var i = 0; i < _acTreeData.length; i++) {
      treeContainer.appendChild(acRenderNodeWithPreview(_acTreeData[i], 0, ""));
    }
  }

  // 加载图片到预览器
  function loadAcpImagePreview(filePath) {
    if (!proj) return;
    var img = document.getElementById("acImageView");
    var placeholder = document.getElementById("acImagePlaceholder");
    if (!img || !placeholder) return;
    // 重置旋转
    _acpImageRotation = 0;
    img.style.transform = "rotate(0deg)";
    // 加载图片
    var url = "/api/project/" + proj.nameEncoded + "/photo-file/" + encodeURIComponent(filePath);
    img.src = url;
    img.style.display = "";
    placeholder.style.display = "none";
    img.onerror = function () {
      img.style.display = "none";
      placeholder.style.display = "";
      placeholder.textContent = "无法加载此图片";
    };
  }

  // 绑定旋转按钮
  function bindAcpRotateButtons() {
    var leftBtn = document.getElementById("acRotateLeft");
    var rightBtn = document.getElementById("acRotateRight");
    if (leftBtn) {
      leftBtn.addEventListener("click", function () {
        _acpImageRotation = (_acpImageRotation - 90 + 360) % 360;
        applyAcpImageRotation();
      });
    }
    if (rightBtn) {
      rightBtn.addEventListener("click", function () {
        _acpImageRotation = (_acpImageRotation + 90) % 360;
        applyAcpImageRotation();
      });
    }
  }

  function applyAcpImageRotation() {
    var img = document.getElementById("acImageView");
    if (img) {
      img.style.transform = "rotate(" + _acpImageRotation + "deg)";
    }
  }

  // 绑定控制台弹窗（日志已实时写入控制台，打开即看最新）
  function bindAcpConsoleOverlay() {
    var bar = document.getElementById("acExecBar");
    var overlay = document.getElementById("acExecConsole");
    var closeBtn = document.getElementById("acExecConsoleClose");
    if (bar) {
      bar.addEventListener("click", function () {
        if (overlay) overlay.style.display = "flex";
      });
    }
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        if (overlay) overlay.style.display = "none";
      });
    }
    if (overlay) {
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) overlay.style.display = "none";
      });
    }
  }

  // 覆盖 acTreeRefresh 以使用带预览的版本
  acTreeRefresh = function () {
    acTreeRefreshWithPreview();
  };

  // 在 init 中绑定执行态UI
  var _origInit = initAiClassifyPanel;
  initAiClassifyPanel = function () {
    _origInit();
    bindAcpRotateButtons();
    bindAcpConsoleOverlay();
    relocateAcModeSwitch();
  };

  // 把「视频分类/照片分类」滑块移到「下一步」按钮左侧，为右侧面板让出顶部空间
  function relocateAcModeSwitch() {
    var sw = document.getElementById("acModeSwitch");
    var btnNext = document.getElementById("btnNext");
    if (!sw || !btnNext || !btnNext.parentNode) return;
    if (sw.parentNode !== btnNext.parentNode) {
      btnNext.parentNode.insertBefore(sw, btnNext);
    }
    moveAcThumb();
  }

  /* ---- 注册到公共分发器 ---- */

  PC.registerPanel(3, initAiClassifyPanel);
  PC.registerSseHandler("video_tagger", handleAcVideoEvent);
  PC.registerSseHandler("photo_classify", handlePhotoClassifyEvent);
  window.addEventListener("resize", moveAcThumb);
})();
