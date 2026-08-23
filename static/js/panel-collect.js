/* ===========================================================================
   收集面板 (step 1) — 文件夹选择 / 复制 / 进度条 / 环形图 / 批处理启动
   依赖 window.ProjectCommon（project-common.js）
   =========================================================================== */
(function () {
  "use strict";
 
  var PC = window.ProjectCommon;
  var proj = PC.proj;
  var _leftCol = null;
  var _rightCol = null;
  var _selectedPath = "";
  var _editFolderName = null;
  var _showCopyModal = null;
 
  /* ======== 文件夹选择器交互 ======== */
 
  function initFolderSelector() {
    var pickBtn = document.getElementById("fsPickBtn");
    var copyBtn = document.getElementById("fsCopyBtn");
    var copyGroup = document.getElementById("fsCopyGroup");
    var prompt = document.getElementById("fsPrompt");
    var detail = document.getElementById("fsDetail");
    var nameEl = document.getElementById("fsName");
    var statEl = document.getElementById("fsStat");
    var reselectBtn = document.getElementById("fsReselectBtn");
    var popupConfirmBtn = document.getElementById("fsPopupConfirmBtn");
    var selectedPath = "";
 
    var copyModal = document.getElementById("copyModal");
    var copyFolderName = document.getElementById("copyFolderName");
    var copyAuthor = document.getElementById("copyAuthor");
    var copyAddWatermark = document.getElementById("copyAddWatermark");
    var copyCompressPhoto = document.getElementById("copyCompressPhoto");
    var copyCompressVideo = document.getElementById("copyCompressVideo");
    var copyCancelBtn = document.getElementById("copyCancelBtn");
    var copyConfirmBtn = document.getElementById("copyConfirmBtn");
 
    function resetSelection() {
      selectedPath = "";
      _editFolderName = null;
      prompt.style.display = "";
      detail.style.display = "none";
      pickBtn.style.display = "";
      if (copyGroup) copyGroup.style.display = "none";
    }
 
    function showCopyModal(folderData) {
      if (!copyModal) return;
      if (folderData) {
        _editFolderName = folderData.name;
        copyFolderName.value = folderData.name;
        copyAuthor.value = folderData.author || "";
        copyAddWatermark.checked = folderData.addwatermark !== "false";
        copyCompressPhoto.checked = folderData.compressphoto !== "false";
        copyCompressVideo.checked = folderData.compressvideo !== "false";
        copyConfirmBtn.textContent = "保存修改";
      } else {
        if (!selectedPath) return;
        _editFolderName = null;
        copyFolderName.value = "";
        copyAuthor.value = "";
        copyAddWatermark.checked = true;
        copyCompressPhoto.checked = true;
        copyCompressVideo.checked = true;
        copyConfirmBtn.textContent = "确认复制";
      }
      PC.state.panelBusy = true;
      copyModal.style.display = "";
    }
    _showCopyModal = showCopyModal;
 
    function hideCopyModal() {
      if (!copyModal) return;
      copyModal.style.display = "none";
      PC.state.panelBusy = false;
    }
 
    if (!pickBtn) return;
 
    pickBtn.addEventListener("click", function () {
      PC.state.panelBusy = true;
      pickBtn.disabled = true;
      pickBtn.textContent = "选择中";
      fetch("/api/pick-folder", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status !== "ok" || !data.path) return;
          selectedPath = data.path;
          return fetch("/api/folder-info", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: data.path }),
          });
        })
        .then(function (r) { return r ? r.json() : null; })
        .then(function (info) {
          if (!info || info.status !== "ok") return;
          prompt.style.display = "none";
          nameEl.textContent = info.folderName;
          statEl.textContent = info.itemCount.toLocaleString() + " 项 · " + info.totalSizeGB + " GB";
          detail.style.display = "";
          pickBtn.style.display = "none";
          if (copyGroup) copyGroup.style.display = "";
        })
        .catch(function () {
          // 静默
        })
        .finally(function () {
          PC.state.panelBusy = false;
          pickBtn.disabled = false;
          pickBtn.textContent = "浏览";
        });
    });
 
    if (copyBtn) {
      copyBtn.addEventListener("click", function () { showCopyModal(); });
    }
 
    if (reselectBtn) {
      reselectBtn.addEventListener("click", function () {
        resetSelection();
      });
    }
 
    if (popupConfirmBtn) {
      popupConfirmBtn.addEventListener("click", function () { showCopyModal(); });
    }
 
    if (copyCancelBtn) {
      copyCancelBtn.addEventListener("click", function () {
        hideCopyModal();
        PC.state.panelBusy = false;
      });
    }
 
    if (copyModal) {
      copyModal.addEventListener("click", function (e) {
        if (e.target === copyModal) {
          hideCopyModal();
          PC.state.panelBusy = false;
        }
      });
    }
 
    if (copyConfirmBtn) {
      copyConfirmBtn.addEventListener("click", function () {
        var folderName = copyFolderName.value.trim();
        var author = copyAuthor.value.trim();
        var addWatermark = copyAddWatermark.checked;
        var compressPhoto = copyCompressPhoto.checked;
        var compressVideo = copyCompressVideo.checked;
        if (!folderName) { PC.showNotice("error", "请输入文件夹名称"); return; }
 
        if (_editFolderName) {
          // 编辑模式：更新元数据（支持改名）
          fetch("/api/project/" + proj.nameEncoded + "/folder/" + encodeURIComponent(_editFolderName) + "/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: folderName,
              author: author,
              addWatermark: addWatermark,
              compressPhoto: compressPhoto,
              compressVideo: compressVideo,
            }),
          })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.status === "ok") {
              hideCopyModal();
              PC.showNotice("success", "保存成功");
              PC.loadPanel(proj.currentStep);
            } else {
              PC.showNotice("error", data.message || "保存失败");
            }
          })
          .catch(function () {
            PC.showNotice("error", "网络错误，请重试");
          });
          return;
        }
 
        // 新建模式：拷贝文件
        fetch("/api/project/" + proj.nameEncoded + "/collect-copy", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sourcePath: selectedPath,
            folderName: folderName,
            author: author,
            addWatermark: addWatermark,
            compressPhoto: compressPhoto,
            compressVideo: compressVideo,
          }),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status === "ok") {
            hideCopyModal();
            setSelectorBusy(true);
            if (_leftCol) updateProgressBars(_leftCol, 0);
            if (_rightCol) updateProgressBars(_rightCol, 0);
            pollCopyProgress();
          } else {
            PC.showNotice("error", data.message || "启动拷贝失败");
          }
        })
        .catch(function () {
          PC.showNotice("error", "网络错误，请重试");
        });
      });
    }
 
    // 左侧文件夹卡片点击 → 编辑元数据
    var folderItems = document.querySelectorAll(".folder-item");
    for (var fi = 0; fi < folderItems.length; fi++) {
      folderItems[fi].addEventListener("click", function () {
        if (this.getAttribute("data-name")) {
          showCopyModal(this.dataset);
        }
      });
    }
  }
 
  /* ======== 收集面板 (step 1) ======== */
 
  function initCollectPanel() {
    PC.state.panelBusy = false;
    _leftCol = document.getElementById("vbarColLeft");
    _rightCol = document.getElementById("vbarColRight");
 
    // 生成竖向柱状条（初始化全空）
    if (_leftCol) createVBars(_leftCol, 40, "green");
    if (_rightCol) createVBars(_rightCol, 40, "");
 
    // 文件夹选择器
    initFolderSelector();
 
    // ECharts 环形图
    buildRingChart();
 
    // 每次加载面板都确保 SSE 已连接（刷新后恢复进度条实时推送）
    if (proj) PC.ensureSSE(proj.nameEncoded);
 
    // 下一步按钮
    PC.setBtnNextHandler(function () {
      if (PC.state.panelBusy) return;
      if (document.getElementById("stepConfirmModal")) {
        document.getElementById("stepConfirmModal").style.display = "";
      }
    });
 
    // 显示指示灯
    var indGroup = document.getElementById("indicatorGroup");
    if (indGroup) indGroup.style.display = "";
 
    // 检查是否已锁定 → 恢复锁状态
    var pcEl = document.querySelector(".panel-collect");
    if (pcEl && pcEl.getAttribute("data-step1-locked") === "true") {
      lockSelector(true);
    }
 
    // 长按确认弹窗
    initStepConfirm();
  }
 
  /* ======== 锁定选择器 ======== */
 
  function lockSelector(immediate) {
    var ids = ["fsPickBtn", "fsCopyBtn", "fsReselectBtn"];
    for (var i = 0; i < ids.length; i++) {
      var btn = document.getElementById(ids[i]);
      if (!btn) continue;
      btn.disabled = true;
      btn.style.opacity = "0.35";
      btn.style.cursor = "not-allowed";
    }
    var items = document.querySelectorAll(".folder-item");
    for (var fi = 0; fi < items.length; fi++) {
      items[fi].style.pointerEvents = "none";
      items[fi].style.opacity = "0.45";
    }
    PC.lightUpIndicators(immediate);
    var btnNext = document.getElementById("btnNext");
    if (btnNext) {
      btnNext.disabled = true;
      btnNext.classList.add("disabled");
    }
  }
 
  /* ======== ECharts 环形图 ======== */
 
  var RING_COLORS = ["#4A8575", "#3D6390", "#D4946A"];
 
  function buildRingChart() {
    var chartDom = document.getElementById("chartRing");
    if (!chartDom) return;
    if (PC.state.ringChart) { PC.state.ringChart.dispose(); PC.state.ringChart = null; }
 
    PC.state.ringChart = echarts.init(chartDom);
 
    fetch("/api/project/" + proj.nameEncoded + "/folder-stats")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status !== "ok") return;
        if (data.folders.length === 0) {
          chartDom.innerHTML = '<div class="content-placeholder" style="height:100%;display:flex;align-items:center;justify-content:center"><span class="content-placeholder-text">暂无文件夹</span></div>';
          return;
        }
        updateCenterText(data);
        renderRingChart(PC.state.ringChart, data);
      })
      .catch(function () {});
  }
 
  function renderRingChart(chart, data) {
    var folders = data.folders;
    var total = data.totalFiles || 1;
 
    var chartData = folders.map(function (f, i) {
      return {
        value: (f.photoCount + f.videoCount) || 1,
        name: f.name,
        itemStyle: { color: RING_COLORS[i % RING_COLORS.length] },
        _raw: f,
      };
    });
 
    var option = {
      backgroundColor: "transparent",
      tooltip: { show: false },
      series: [{
        type: "pie",
        radius: ["63%", "80%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: false,
        padAngle: 1.2,
        itemStyle: {
          borderRadius: 3,
          borderColor: "rgba(255,255,255,0.35)",
          borderWidth: 1.5,
        },
        label: { show: false },
        emphasis: {
          scale: true,
          scaleSize: 8,
          focus: "self",
          label: { show: false },
          itemStyle: {
            shadowBlur: 14,
            shadowOffsetX: 0,
            shadowColor: "rgba(0,0,0,0.10)",
          },
        },
        blur: {
          itemStyle: { opacity: 0.25 },
        },
        data: chartData,
        animationDuration: 800,
        animationEasing: "cubicOut",
      }],
    };
 
    chart.setOption(option);
 
    chart.on("mouseover", function (params) {
      var folder = data.folders[params.dataIndex];
      if (folder) {
        document.getElementById("ringDetail").classList.add("show");
        updateDetail(folder);
      }
    });
 
    // mouseleave 监听整个容器，避免移出 chart 就消失
    var wrap = chart.getDom().closest(".pc-center-wrap");
    if (wrap) {
      wrap.addEventListener("mouseleave", function () {
        hideDetail();
      });
    }
  }
 
  function updateCenterText(data) {
    var sizeEl = document.getElementById("ringTotalSize");
    var photoEl = document.getElementById("ringTotalPhotos");
    var videoEl = document.getElementById("ringTotalVideos");
    if (sizeEl) sizeEl.textContent = data.totalSizeGB + " GB";
    if (photoEl) photoEl.textContent = data.totalPhotos;
    if (videoEl) videoEl.textContent = data.totalVideos;
  }
 
  function updateDetail(folder) {
    var body = document.getElementById("rdBody");
    var nameEl = document.getElementById("rdName");
    var authorEl = document.getElementById("rdAuthor");
    var statsEl = document.getElementById("rdStats");
 
    if (!body) return;
 
    if (nameEl) nameEl.textContent = folder.name;
    if (authorEl) authorEl.textContent = folder.author ? "作者：" + folder.author : "";
    if (statsEl) statsEl.textContent = "照片 " + folder.photoCount + " 张  · 视频 " + folder.videoCount + " 个  · " + folder.totalSizeGB + " GB";
  }
 
  function hideDetail() {
    var detail = document.getElementById("ringDetail");
    if (detail) detail.classList.remove("show");
  }
 
  /* ======== 下一步确认弹窗（长按 3s + rAF 填色） ======== */
 
  function initStepConfirm() {
    var modal = document.getElementById("stepConfirmModal");
    var cancelBtn = document.getElementById("stepConfirmCancel");
    var confirmBtn = document.getElementById("stepConfirmBtn");
    var fill = document.getElementById("lpFill");
    var textEl = document.getElementById("lpText");
    var raf = null;
    var confirmed = false;
    var HOLD_MS = 3000;
 
    if (!modal) { console.warn("[stepConfirm] modal missing"); return; }
    if (!confirmBtn || !fill || !textEl) {
      console.warn("[stepConfirm] btn/fill/text missing", { confirmBtn: !!confirmBtn, fill: !!fill, textEl: !!textEl });
      return;
    }
    var ORIG_TEXT = textEl.textContent;
 
    function resetFill() {
      confirmBtn.classList.remove("active");
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      fill.style.width = "0%";
      textEl.textContent = ORIG_TEXT;
    }
 
    function startHold() {
      if (confirmed) return;
      confirmBtn.classList.add("active");
      fill.style.width = "0%";
      textEl.textContent = "长按中…";
      var startTime = Date.now();
 
      function tick() {
        var elapsed = Date.now() - startTime;
        var pct = Math.min(elapsed / HOLD_MS, 1);
        fill.style.width = (pct * 100) + "%";
        if (pct >= 1) {
          raf = null;
          confirmed = true;
          doConfirm();
          return;
        }
        raf = requestAnimationFrame(tick);
      }
      raf = requestAnimationFrame(tick);
    }
 
    function cancelHold() {
      if (confirmed) return;
      resetFill();
    }
 
    confirmBtn.addEventListener("mousedown", startHold);
    confirmBtn.addEventListener("mouseup", cancelHold);
    confirmBtn.addEventListener("mouseleave", cancelHold);
    confirmBtn.addEventListener("touchstart", function (e) { e.preventDefault(); startHold(); });
    confirmBtn.addEventListener("touchend", cancelHold);
    confirmBtn.addEventListener("touchcancel", cancelHold);
 
    cancelBtn.addEventListener("click", function () {
      modal.style.display = "none";
      cancelHold();
    });
 
    modal.addEventListener("click", function (e) {
      if (e.target === modal) {
        modal.style.display = "none";
        cancelHold();
      }
    });
 
    function doConfirm() {
      modal.style.display = "none";
      lockSelector();
      var pname = (window.__PROJECT__ || {}).nameEncoded;
      if (pname) {
        fetch("/api/project/" + pname + "/lock-step1", { method: "POST" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.status === "ok") {
              PC.showNotice("success", "已锁定素材选择器");
              startBatchProcessing(pname);
            }
          })
          .catch(function () {
            PC.showNotice("error", "锁定失败，请重试");
          });
      } else {
        PC.showNotice("success", "已锁定素材选择器");
      }
    }
  }
 
  function createVBars(col, count, colorClass) {
    for (var i = 0; i < count; i++) {
      var bar = document.createElement("div");
      bar.className = "vbar" + (colorClass ? " " + colorClass : "");
      col.appendChild(bar);
    }
  }
 
  /*** 进度条控制 ***/
 
  function updateProgressBars(col, pct) {
    if (!col) return;
    var bars = Array.from(col.children);
    var fillCount = Math.round(pct * bars.length);
    if (fillCount > bars.length) fillCount = bars.length;
    for (var i = 0; i < bars.length; i++) {
      bars[i].classList.toggle("filled", i < fillCount);
    }
  }
 
  function snapBars() {
    var cols = [_leftCol, _rightCol];
    for (var ci = 0; ci < cols.length; ci++) {
      var col = cols[ci];
      if (!col) continue;
      for (var i = 0; i < col.children.length; i++) {
        col.children[i].classList.add("no-transition");
      }
    }
    void document.body.offsetHeight;
    for (var ci = 0; ci < cols.length; ci++) {
      var col = cols[ci];
      if (!col) continue;
      for (var i = 0; i < col.children.length; i++) {
        col.children[i].classList.remove("no-transition");
      }
    }
  }
 
  function pollCopyProgress() {
    // 复制进度通过 SSE 推送，不再轮询
    // SSE 连接已在 ensureSSE 中建立
    // 但复制可能在没有 SSE 连接时启动，确保连接
    PC.ensureSSE(proj.nameEncoded);
  }
 
  function refreshFolderList() {
    fetch("/api/project/" + proj.nameEncoded + "/panel/1")
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var tmp = document.createElement("div");
        tmp.innerHTML = html;
        var newList = tmp.querySelector(".pc-folder-panel");
        var oldList = document.querySelector(".pc-folder-panel");
        if (newList && oldList) {
          oldList.replaceWith(newList);
        }
        var items = document.querySelectorAll(".folder-item");
        for (var fi = 0; fi < items.length; fi++) {
          items[fi].addEventListener("click", function () {
            if (this.getAttribute("data-name")) {
              if (_showCopyModal) _showCopyModal(this.dataset);
            }
          });
        }
        if (document.querySelector(".panel-collect[data-step1-locked='true']")) {
          lockSelector(true);
        }
        buildRingChart();
      })
      .catch(function () {});
  }
 
  function handleCopyData(data) {
    if (data.status === "not_found" || !data.status) return;
 
    var leftPct = data.total > 0 ? data.done / data.total : 0;
    var rightPct = data.current_file_total > 0 ? data.current_file_done / data.current_file_total : 0;
    updateProgressBars(_leftCol, leftPct);
    updateProgressBars(_rightCol, rightPct);
 
    if (data.status === "done") {
      snapBars();
      PC.showNotice("success", "复制完成！");
      AppEvents.emit("refresh:stepbar");
      setTimeout(function () {
        var p = document.getElementById("fsPrompt");
        var d = document.getElementById("fsDetail");
        var pb = document.getElementById("fsPickBtn");
        var cg = document.getElementById("fsCopyGroup");
        if (p) p.style.display = "";
        if (d) d.style.display = "none";
        if (pb) pb.style.display = "";
        if (cg) cg.style.display = "none";
        setSelectorBusy(false);
        refreshFolderList();
      }, 3000);
    } else if (data.status === "error") {
      snapBars();
      setSelectorBusy(false);
      PC.showNotice("error", "拷贝失败：" + (data.message || "未知错误"));
    }
  }
 
  function setSelectorBusy(busy) {
    var pickBtn = document.getElementById("fsPickBtn");
    var copyBtn = document.getElementById("fsCopyBtn");
    var reselectBtn = document.getElementById("fsReselectBtn");
    var btns = [pickBtn, copyBtn, reselectBtn];
    for (var i = 0; i < btns.length; i++) {
      var btn = btns[i];
      if (!btn) continue;
      btn.disabled = busy;
      btn.style.opacity = busy ? "0.4" : "";
      btn.style.cursor = busy ? "not-allowed" : "";
    }
  }
 
  function startBatchProcessing(pname) {
    // 先建立 SSE 连接，再启动批处理
    PC.ensureSSE(pname);
    fetch("/api/project/" + pname + "/start-batch", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status !== "ok") {
          PC.showNotice("error", d.message || "启动批处理失败");
          return;
        }
      })
      .catch(function () {
        PC.showNotice("error", "启动批处理失败");
      });
  }
 
  // 注册到公共分发器
  PC.registerPanel(1, initCollectPanel);
  PC.registerSseHandler("copy", handleCopyData);
})();