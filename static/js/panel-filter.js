/* ===========================================================================
   过滤面板 (step 2) — 筛检启动 / 星盘环形动画 / AI 废片筛检 / 废片拯救
   依赖 window.ProjectCommon（project-common.js）
   =========================================================================== */
(function () {
  "use strict";

  var PC = window.ProjectCommon;
  var proj = PC.proj;

  var _pfHoldCompleted = false;
  var _filterPollTimer = null;
  var _filterModalStartTime = 0;
  var _fakeProgressTimer = null;
  var _filterAnimTimer = null;
  var _qualityPollTimer = null;
  var _lastLive = null;

  /* ---------------- 废片率环（接口预留） ---------------- */
  // 磨砂玻璃透明环，默认无填充、数值 "--"。
  // 后续后端数据同步时调用 updateWasteRate(percent) 即可（percent 为 0~100）。
  function initWasteRateRing() {
    var wr = document.getElementById("wrProgress");
    if (!wr) return;
    var circ = 2 * Math.PI * 88;
    wr.style.strokeDasharray = circ + " " + circ;
    wr.style.strokeDashoffset = circ; // 默认无填充
  }

  // percent: 0~100 的废片率；传 null/undefined/NaN 时环无填充且显示 "--"
  function updateWasteRate(percent) {
    var wr = document.getElementById("wrProgress");
    var val = document.getElementById("wrValue");
    var circ = 2 * Math.PI * 88;
    if (wr) {
      if (percent == null || isNaN(percent)) {
        wr.style.strokeDashoffset = circ;
      } else {
        var clamped = Math.max(0, Math.min(100, percent));
        wr.style.strokeDashoffset = circ - (clamped / 100) * circ;
      }
    }
    if (val) val.textContent = (percent == null || isNaN(percent)) ? "--" : Math.round(percent) + "%";
  }

  /* ---------------- 三级过滤：实时联动 ---------------- */
  var MAIN_RING_CIRC = 2 * Math.PI * 180;
  var REASON_LABELS = { exposure: "曝光问题", focus: "对焦问题", face: "人脸问题" };

  function _photoUrl(p) {
    var pname = proj ? proj.nameEncoded : "";
    return "/api/project/" + pname + "/photo-file/" +
      p.split(/[\\/]/).map(encodeURIComponent).join("/");
  }

  function _fmtNum(v) {
    if (v == null || v === "" || isNaN(v)) return "--";
    return String(v);
  }
  function _fmtPct(v) {
    if (v == null || isNaN(v)) return "--";
    return (v * 100).toFixed(1) + "%";
  }

  // 用后端汇总更新废片率环 + 三卡片
  function applySummary(d) {
    if (!d) return;
    if (d.counts) {
      var ce = document.getElementById("wasteCountExposure");
      var cf = document.getElementById("wasteCountFocus");
      var cfa = document.getElementById("wasteCountFace");
      if (ce) ce.textContent = String(d.counts.exposure || 0);
      if (cf) cf.textContent = String(d.counts.focus || 0);
      if (cfa) cfa.textContent = String(d.counts.face || 0);
    }
    if (d.waste_rate != null) updateWasteRate(d.waste_rate);
  }

  // 叠加层绘制（归一化坐标 0~1，SVG viewBox 0~100，vector-effect 保证描边等比）
  function drawOverlay(svg, ov) {
    if (!svg) return;
    svg.innerHTML = "";
    if (!ov) return;
    var NS = "http://www.w3.org/2000/svg";
    function rect(x, y, w, h, cls) {
      var r = document.createElementNS(NS, "rect");
      r.setAttribute("x", x * 100); r.setAttribute("y", y * 100);
      r.setAttribute("width", w * 100); r.setAttribute("height", h * 100);
      r.setAttribute("class", cls);
      svg.appendChild(r);
    }
    var g = ov.grid || 3;
    for (var i = 1; i < g; i++) {
      var p = (i / g) * 100;
      var v = document.createElementNS(NS, "line");
      v.setAttribute("x1", p); v.setAttribute("y1", 0);
      v.setAttribute("x2", p); v.setAttribute("y2", 100);
      v.setAttribute("class", "og-grid"); svg.appendChild(v);
      var hln = document.createElementNS(NS, "line");
      hln.setAttribute("x1", 0); hln.setAttribute("y1", p);
      hln.setAttribute("x2", 100); hln.setAttribute("y2", p);
      hln.setAttribute("class", "og-grid"); svg.appendChild(hln);
    }
    (ov.over_blocks || []).forEach(function (b) { rect(b[0], b[1], b[2], b[3], "og-over"); });
    (ov.under_blocks || []).forEach(function (b) { rect(b[0], b[1], b[2], b[3], "og-under"); });
    (ov.faces || []).forEach(function (f, idx) {
      rect(f[0], f[1], f[2], f[3], idx === ov.face_primary ? "og-face og-face-primary" : "og-face");
    });
  }

  // 预览区：容器尺寸恒定；竖图整体旋转 90°（不拉伸）
  function updatePreview(live) {
    var frame = document.getElementById("pfImgFrame");
    var img = document.getElementById("pfImg");
    var svg = document.getElementById("pfOverlay");
    var area = document.getElementById("pfImageArea");
    if (!frame || !img || !svg || !area) return;
    if (!live || !live.path) { updateParams(live); return; }
    _lastLive = live;

    var url = _photoUrl(live.path);

    function layout() {
      var cw = area.clientWidth, ch = area.clientHeight;
      var nw = img.naturalWidth, nh = img.naturalHeight;
      if (!nw || !nh) return;
      // 等比缩放铺满预览窗口范围内（竖图同样竖着显示，不旋转、不拉伸）
      var k = Math.min(cw / nw, ch / nh);
      frame.style.width = (nw * k) + "px";
      frame.style.height = (nh * k) + "px";
      frame.style.transform = "none";
      frame.style.display = "";
      var hint = document.getElementById("pfImgHint");
      if (hint) hint.style.display = "none";
      drawOverlay(svg, live.overlay);
    }

    img.onload = layout;
    if (img.getAttribute("data-src") !== url) {
      img.setAttribute("data-src", url);
      img.src = url;
    } else if (img.complete && img.naturalWidth) {
      layout();
    }
    updateParams(live);
  }

  // 技术参数区
  function updateParams(live) {
    var list = document.getElementById("pfParamsList");
    var hint = document.getElementById("pfParamsHint");
    if (!list) return;
    if (!live) return;
    var m = live.metrics || {};
    if (hint) hint.style.display = "none";
    var verdict = live.verdict === "waste"
      ? "废片 · " + (REASON_LABELS[live.reason] || "废片")
      : "合格";
    if (m.skipped) verdict = "跳过（不可解析）";
    var rows = [
      ["尺寸", m.width ? (m.width + "×" + m.height) : "--"],
      ["灰度均值", _fmtNum(m.gray_mean)],
      ["过曝占比", _fmtPct(m.over_ratio)],
      ["欠曝占比", _fmtPct(m.under_ratio)],
      ["过曝块数", _fmtNum(m.over_blocks)],
      ["欠曝块数", _fmtNum(m.under_blocks)],
      ["Tenengrad", _fmtNum(m.tenengrad_mean)],
      ["清晰块占比", m.sharp_block_ratio == null ? "--" : _fmtPct(m.sharp_block_ratio)],
      ["最清晰区参考", m.sharp_ref == null ? "--" : _fmtNum(m.sharp_ref)],
      ["人脸数", _fmtNum(m.face_count)],
      ["人脸区过曝块", _fmtNum(m.face_over_blocks)],
      ["最大三脸分数", (m.face_scores && m.face_scores.length) ? m.face_scores.join(" / ") : "--"],
      ["人脸质量分", m.face_quality == null ? "--" : _fmtNum(m.face_quality)],
      ["人脸质量阈值", m.face_quality_threshold == null ? "--" : _fmtNum(m.face_quality_threshold)],
      ["判定", verdict]
    ];
    list.innerHTML = rows.map(function (r) {
      var warn = (r[0] === "判定" && live.verdict === "waste") ? " warn" : "";
      return '<div class="pf-param-row' + warn + '"><span class="pk">' + r[0] +
        '</span><span class="pv">' + r[1] + "</span></div>";
    }).join("");
  }

  // 右上：主体人脸（保留到出现下一张照片的主体人脸）
  function updateFace(thumb) {
    if (!thumb) return;
    var img = document.getElementById("pfFaceImg");
    var empty = document.getElementById("pfFaceEmpty");
    if (img) { img.src = thumb; img.style.display = ""; }
    if (empty) empty.style.display = "none";
  }

  // 重置面板实时区（重新开始时）
  function resetLiveUI() {
    var frame = document.getElementById("pfImgFrame");
    var hint = document.getElementById("pfImgHint");
    var img = document.getElementById("pfImg");
    var svg = document.getElementById("pfOverlay");
    var list = document.getElementById("pfParamsList");
    var paramsHint = document.getElementById("pfParamsHint");
    var faceImg = document.getElementById("pfFaceImg");
    var faceEmpty = document.getElementById("pfFaceEmpty");
    if (frame) { frame.style.display = "none"; frame.style.transform = "none"; }
    if (hint) hint.style.display = "";
    if (img) { img.removeAttribute("data-src"); img.removeAttribute("src"); }
    if (svg) svg.innerHTML = "";
    if (list && paramsHint) {
      list.innerHTML = "";
      list.appendChild(paramsHint);
      paramsHint.style.display = "";
    }
    if (faceImg) { faceImg.style.display = "none"; faceImg.removeAttribute("src"); }
    if (faceEmpty) faceEmpty.style.display = "";
    applySummary({ counts: { exposure: 0, focus: 0, face: 0 }, waste_rate: null });
  }

  // 用质量快照刷新主环 + 废片环 + 三卡片 + 预览 + 人脸
  function updateQualityLive(s) {
    if (!s) return;
    var total = s.total || 0, done = s.done || 0, pct = s.percent || 0;
    var pc = document.getElementById("progressCircle");
    if (pc) {
      pc.style.transition = "stroke-dashoffset 0.3s ease";
      pc.style.strokeDashoffset = MAIN_RING_CIRC - (pct / 100) * MAIN_RING_CIRC;
    }
    var rpPercent = document.getElementById("rpPercent");
    var rpDone = document.getElementById("rpDone");
    var rpTotal = document.getElementById("rpTotal");
    if (rpPercent) rpPercent.innerHTML = Math.round(pct) + '<span class="rp-pct">%</span>';
    if (rpDone) rpDone.textContent = String(done);
    if (rpTotal) rpTotal.textContent = String(total);
    var pfEl = document.querySelector(".panel-filter");
    if (pfEl) pfEl.classList.add("pf-text-appeared");

    applySummary(s);
    updatePreview(s.current);
    if (s.face_thumb) updateFace(s.face_thumb);
  }

  // 面板重开时恢复已完成的检测状态（存在 筛选结果.json）
  function restoreQualityState() {
    var pname = proj ? proj.nameEncoded : "";
    if (!pname) return;
    fetch("/api/project/" + pname + "/quality-filter-summary")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.exists) return;
        applySummary(d);
        var pc = document.getElementById("progressCircle");
        if (pc) { pc.style.transition = "none"; pc.style.strokeDashoffset = "0"; }
        var rpPercent = document.getElementById("rpPercent");
        var rpDone = document.getElementById("rpDone");
        var rpTotal = document.getElementById("rpTotal");
        if (rpPercent) rpPercent.innerHTML = '100<span class="rp-pct">%</span>';
        if (rpDone) rpDone.textContent = String(d.total || 0);
        if (rpTotal) rpTotal.textContent = String(d.total || 0);
        var ringStart = document.getElementById("ringStart");
        var ringInfo = document.getElementById("ringProgressInfo");
        if (ringStart) ringStart.style.display = "none";
        if (ringInfo) ringInfo.style.display = "";
        var pfEl = document.querySelector(".panel-filter");
        if (pfEl) pfEl.classList.add("pf-text-appeared");
        setBurstEnabled(true);
      })
      .catch(function () {});
  }

  function initFilterPanel() {
    PC.state.panelBusy = false;
    if (_filterPollTimer) { clearTimeout(_filterPollTimer); _filterPollTimer = null; }
    if (_fakeProgressTimer) { clearInterval(_fakeProgressTimer); _fakeProgressTimer = null; }
    var pfEl = document.querySelector(".panel-filter");
    if (pfEl) {
      pfEl.classList.remove("pf-text-appeared", "pf-filtering");
    }
    initFilterRing();
    var indGroup = document.getElementById("indicatorGroup");
    if (indGroup) indGroup.style.display = "none";
    if (_qualityPollTimer) { clearTimeout(_qualityPollTimer); _qualityPollTimer = null; }
    resetLiveUI();
    // step2 非必做：三级筛选与连拍快筛谁先谁后都行，两个按钮开局即可用
    setBurstEnabled(true);
    var btnNext = document.getElementById("btnNext");
    if (btnNext) {
      btnNext.removeAttribute("disabled");
      btnNext.classList.remove("disabled");
      btnNext.classList.add("lit");
    }
    // 下一步：不强制完成 step2，可直接进入 step3
    PC.setBtnNextHandler(function () {
      if (PC.state.panelBusy) return;
      var modal = document.getElementById("filterConfirmModal");
      if (modal) modal.style.display = "";
    });
    initFilterConfirm();
    initWasteViewer();
    initBurstViewer();
    restoreQualityState();
    function initFilterConfirm() {
      var modal = document.getElementById("filterConfirmModal");
      if (!modal) return;
      var cancelBtn = document.getElementById("filterConfirmCancel");
      var confirmBtn = document.getElementById("filterConfirmBtn");
      var fill = document.getElementById("filterLpFill");
      var textEl = document.getElementById("filterLpText");
      if (!confirmBtn || !fill || !textEl) return;
      var ORIG_TEXT = textEl.textContent;
      var raf = null, confirmed = false;
      var HOLD_MS = 3000;

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
            raf = null; confirmed = true;
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
        var pname = proj ? proj.nameEncoded : "";
        if (!pname) return;
        fetch("/api/project/" + pname + "/ai-filter-confirm", { method: "POST" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.status === "ok") {
              PC.showNotice("success", "已移除 " + d.moved + " 个废片，进入 AI 分类");
              var btn = document.getElementById("btnNext");
              if (btn) btn.classList.remove("lit");
              PC.loadPanel(3);
            } else {
              PC.showNotice("error", d.message || "确认失败");
            }
          })
          .catch(function () {
            PC.showNotice("error", "网络错误，请重试");
          });
      }
    }
  }

  function initFilterRing() {
    var ringWrap = document.getElementById("ringWrap");
    var progressCircle = document.getElementById("progressCircle");
    if (!ringWrap || !progressCircle) return;

    var radius = 180;
    var circumference = 2 * Math.PI * radius;

    progressCircle.style.strokeDasharray = circumference + " " + circumference;
    progressCircle.style.strokeDashoffset = circumference;

    function setProgress(percent) {
      var offset = circumference - (percent / 100) * circumference;
      progressCircle.style.strokeDashoffset = offset;
    }

    function animateNumber(el, target, duration) {
      if (!el) return;
      duration = duration || 1200;
      var start = 0;
      var startTime = performance.now();
      function update(now) {
        var t = Math.min((now - startTime) / duration, 1);
        var eased = 1 - Math.pow(1 - t, 3);
        el.textContent = String(Math.floor(start + (target - start) * eased)).padStart(2, "0");
        if (t < 1) requestAnimationFrame(update);
      }
      requestAnimationFrame(update);
    }

    initWasteRateRing();

    progressCircle.style.strokeDashoffset = circumference;
    var startBtn = document.getElementById("startBtn");
    var ringStart = document.getElementById("ringStart");
    var ringProgressInfo = document.getElementById("ringProgressInfo");
    var rpPercent = document.getElementById("rpPercent");
    var rpDone = document.getElementById("rpDone");
    var rpTotal = document.getElementById("rpTotal");
    var holdRaf = null;
    var holdStart = 0;
    var HOLD_MS = 1000;
    _pfHoldCompleted = false;

    function resetHold() {
      if (holdRaf) { cancelAnimationFrame(holdRaf); holdRaf = null; }
      _pfHoldCompleted = false;
      progressCircle.style.transition = "none";
      progressCircle.style.strokeDashoffset = circumference;
    }

    function showFilterModal() {
      _filterModalStartTime = Date.now();
      var modal = document.getElementById("filterProgressModal");
      if (!modal) return;
      modal.style.display = "";
      var fill = document.getElementById("fpmProgressFill");
      var doneEl = document.getElementById("fpmDone");
      var totalEl = document.getElementById("fpmTotal");
      if (fill) fill.style.width = "0%";
      if (doneEl) doneEl.textContent = "0";
      if (totalEl) totalEl.textContent = "0";
      // 伪进度：缓慢填充到 95%，等待后端真实数据
      if (_fakeProgressTimer) { clearInterval(_fakeProgressTimer); _fakeProgressTimer = null; }
      _fakeProgressTimer = setInterval(function() {
        var cur = fill ? parseFloat(fill.style.width) || 0 : 0;
        var step = Math.max(1, (95 - cur) / 20);
        var next = Math.min(cur + step, 95);
        if (fill) fill.style.width = next + "%";
        if (next >= 95 && _fakeProgressTimer) {
          clearInterval(_fakeProgressTimer);
          _fakeProgressTimer = null;
        }
      }, 80);
    }

    function stopFakeProgress() {
      if (_fakeProgressTimer) {
        clearInterval(_fakeProgressTimer);
        _fakeProgressTimer = null;
      }
    }

    function hideFilterModal() {
      var modal = document.getElementById("filterProgressModal");
      if (!modal) return;
      if (modal.classList.contains("fpm-closing")) return;
      modal.classList.add("fpm-closing");
      setTimeout(function() {
        modal.style.display = "none";
        modal.classList.remove("fpm-closing");
      }, 280);
    }

    function updateFilterModal(data) {
      var fill = document.getElementById("fpmProgressFill");
      var doneEl = document.getElementById("fpmDone");
      var totalEl = document.getElementById("fpmTotal");
      var total = data.total || 0;
      if (fill && total > 0) fill.style.width = data.percent + "%";
      if (totalEl) totalEl.textContent = String(total);
      if (!doneEl) return;

      var target = data.done || 0;
      if (target === total || data.status === "done") {
        if (_filterAnimTimer) { clearInterval(_filterAnimTimer); _filterAnimTimer = null; }
        doneEl.textContent = String(target);
        return;
      }

      var currentDisplay = parseInt(doneEl.textContent) || 0;
      if (target <= currentDisplay) return;

      if (_filterAnimTimer) { clearInterval(_filterAnimTimer); _filterAnimTimer = null; }

      var diff = target - currentDisplay;
      var steps = Math.min(diff, 20);
      var stepSize = Math.ceil(diff / steps);
      var running = currentDisplay;

      _filterAnimTimer = setInterval(function() {
        running += stepSize;
        if (running >= target) {
          running = target;
          clearInterval(_filterAnimTimer);
          _filterAnimTimer = null;
        }
        doneEl.textContent = String(running);
      }, 30);
    }

    function settleLayout(kept, total, onComplete) {
      // 弹窗最小时长 1.5s
      var elapsed = Date.now() - _filterModalStartTime;
      var minDelay = Math.max(0, 1500 - elapsed);

      setTimeout(function() {
        // 环固定于最终位置，无移动动画：关弹窗 + 中心文字出现并显示 100%
        hideFilterModal();
        _pfHoldCompleted = true;
        if (rpPercent) rpPercent.innerHTML = '100<span class="rp-pct">%</span>';
        if (rpDone) rpDone.textContent = String(total || 0);
        if (rpTotal) rpTotal.textContent = String(total || 0);
        var pfEl = document.querySelector(".panel-filter");
        if (pfEl) pfEl.classList.add("pf-text-appeared");
        if (onComplete) onComplete();
      }, minDelay);
    }

    function startFilterPolling() {
      if (_filterPollTimer) return;
      var pname = proj ? proj.nameEncoded : "";
      if (!pname) return;

      function poll() {
        fetch("/api/project/" + pname + "/filter-progress")
          .then(function (r) { return r.json(); })
          .then(function (data) {
            stopFakeProgress();
            if (data.status === "running") {
              updateFilterModal(data);
            } else if (data.status === "done") {
              updateFilterModal(data);
              var fill = document.getElementById("fpmProgressFill");
              if (fill) fill.style.width = "100%";
              _filterPollTimer = null;
              settleLayout(data.kept, data.total, function () {
                setTimeout(function () {
                  onNormalFilterDone();
                }, 500);
              });
              return;
            } else if (data.status === "error") {
              hideFilterModal();
              setBurstEnabled(true);
              PC.showNotice("error", data.message || "筛检出错");
              _filterPollTimer = null;
              return;
            }
            _filterPollTimer = setTimeout(poll, 500);
          })
          .catch(function () {
            _filterPollTimer = setTimeout(poll, 1000);
          });
      }
      poll();
    }

    function completeHold() {
      if (_pfHoldCompleted) return;
      _pfHoldCompleted = true;
      setBurstEnabled(false);   // 三级筛选开跑 → 期间禁用连拍快筛（两者互斥）
      if (holdRaf) { cancelAnimationFrame(holdRaf); holdRaf = null; }
      if (ringStart) ringStart.style.display = "none";
      progressCircle.style.transition = "";
      progressCircle.style.strokeDashoffset = "0";

      if (ringProgressInfo) {
        ringProgressInfo.style.display = "";
        ringProgressInfo.style.opacity = "1";
        ringProgressInfo.style.transform = "scale(1)";
      }
      if (rpPercent) rpPercent.innerHTML = '0<span class="rp-pct">%</span>';
      if (rpDone) rpDone.textContent = "0";
      if (rpTotal) rpTotal.textContent = "-";

      showFilterModal();

      if (proj) {
        fetch("/api/project/" + proj.nameEncoded + "/filter-start", { method: "POST" })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d.status !== "ok") {
              hideFilterModal();
              setBurstEnabled(true);
              PC.showNotice("error", d.message || "启动筛检失败");
              return;
            }
            var totalEl = document.getElementById("fpmTotal");
            if (totalEl && d.total != null) totalEl.textContent = String(d.total);
            if (rpTotal && d.total != null) rpTotal.textContent = String(d.total);
            startFilterPolling();
          })
          .catch(function () {
            hideFilterModal();
            setBurstEnabled(true);
            PC.showNotice("error", "网络错误，请重试");
          });
      }
    }

    function startHold() {
      if (_pfHoldCompleted) return;
      if (_burstBusy) { PC.showNotice("warning", "连拍快筛正在进行，请等它完成后再启动三级筛选"); return; }
      resetHold();
      holdStart = performance.now();
      progressCircle.style.transition = "none";

      function tick(now) {
        var elapsed = now - holdStart;
        var pct = Math.min(elapsed / HOLD_MS, 1);
        var offset = circumference - pct * circumference;
        progressCircle.style.strokeDashoffset = offset;
        if (pct >= 1) {
          holdRaf = null;
          completeHold();
          return;
        }
        holdRaf = requestAnimationFrame(tick);
      }
      holdRaf = requestAnimationFrame(tick);
    }

    function cancelHold() {
      if (_pfHoldCompleted) return;
      resetHold();
    }

    if (startBtn) {
      startBtn.addEventListener("mousedown", startHold);
      startBtn.addEventListener("mouseup", cancelHold);
      startBtn.addEventListener("mouseleave", cancelHold);
      startBtn.addEventListener("touchstart", function (e) { e.preventDefault(); startHold(); }, { passive: false });
      startBtn.addEventListener("touchend", cancelHold);
      startBtn.addEventListener("touchcancel", cancelHold);
    }

    if (PC.state.filterResizeHandler) window.removeEventListener("resize", PC.state.filterResizeHandler);
    PC.state.filterResizeHandler = function () {
      if (_pfHoldCompleted) {
        progressCircle.style.transition = "none";
        progressCircle.style.strokeDashoffset = "0";
      } else {
        progressCircle.style.transition = "none";
        progressCircle.style.strokeDashoffset = circumference;
      }
      if (_lastLive) updatePreview(_lastLive);
    };
    window.addEventListener("resize", PC.state.filterResizeHandler);
  }

  // 连拍快筛按钮可用性：与三级筛选互斥期间置灰，其余时间常亮
  function setBurstEnabled(on) {
    var bb = document.getElementById("btnBurst");
    if (!bb) return;
    bb.style.display = "";
    bb.disabled = !on;
    bb.classList.toggle("lit", on);
  }

  /* ---------------- 图片懒加载（附近优先，缓解大图量卡顿） ---------------- */
  // 图片先挂 data-src，进入视口附近（rootMargin 400px）才真正加载；
  // 浏览器自带同域并发上限（约 6），天然限制并发，避免一次性请求打爆。
  function lazyLoadImages(container, imgs) {
    if (!imgs || !imgs.length) return;
    if (!("IntersectionObserver" in window)) {
      imgs.forEach(function (im) { if (im.dataset.src) im.src = im.dataset.src; });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var im = en.target;
        io.unobserve(im);
        if (im.dataset.src) { im.src = im.dataset.src; im.removeAttribute("data-src"); }
      });
    }, { root: container, rootMargin: "400px 0px", threshold: 0.01 });
    imgs.forEach(function (im) { io.observe(im); });
  }

  /* ---------------- 废片浏览：网格 + 放大 ---------------- */
  function initWasteViewer() {
    var overlay = document.getElementById("wasteViewer");
    var gridEl = document.getElementById("wasteViewerGrid");
    var titleEl = document.getElementById("wasteViewerTitle");
    var closeBtn = document.getElementById("wasteViewerClose");
    var zoomOverlay = document.getElementById("wasteZoom");
    var wzImg = document.getElementById("wzImg");
    if (!overlay || !gridEl) return;
    var pname = proj ? proj.nameEncoded : "";
    var items = [];
    var zoomIndex = -1;
    var clickTimer = null;

    function setStatus(path, rescued) {
      return fetch("/api/project/" + pname + "/quality-filter-set-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path, rescued: rescued })
      }).then(function (r) { return r.json(); })
        .then(function (d) { if (d && d.status === "ok") applySummary(d); return d; })
        .catch(function () {});
    }

    function itemEl(idx) { return gridEl.querySelector('[data-idx="' + idx + '"]'); }

    function renderGrid() {
      gridEl.innerHTML = "";
      if (!items.length) {
        gridEl.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:60px 0;color:rgba(0,0,0,0.35);font-size:16px">暂无废片</div>';
        return;
      }
      var imgs = [];
      items.forEach(function (it, idx) {
        var div = document.createElement("div");
        div.className = "waste-viewer-item" + (it.rescued ? " rescued" : "");
        div.setAttribute("data-idx", idx);
        var img = document.createElement("img");
        img.dataset.src = _photoUrl(it.path);   // 附近优先懒加载，缓解卡顿
        img.alt = it.path;
        img.onerror = function () { this.style.display = "none"; };
        div.appendChild(img);
        imgs.push(img);
        // 单击切换拯救/放弃（延迟以区分双击）
        div.addEventListener("click", function () {
          if (clickTimer) return;
          clickTimer = setTimeout(function () {
            clickTimer = null;
            it.rescued = !it.rescued;
            div.classList.toggle("rescued", it.rescued);
            if (idx === zoomIndex) updateZoomButtons();
            setStatus(it.path, it.rescued);
          }, 220);
        });
        div.addEventListener("dblclick", function () {
          if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
          openZoom(idx);
        });
        gridEl.appendChild(div);
      });
      lazyLoadImages(gridEl, imgs);
    }

    function openViewer(reason) {
      fetch("/api/project/" + pname + "/quality-filter-photos?reason=" + encodeURIComponent(reason))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          items = d.items || [];
          if (titleEl) titleEl.textContent = (d.label || "废片") + " — 共 " + items.length + " 张";
          renderGrid();
          overlay.classList.add("show");
        })
        .catch(function () { PC.showNotice("error", "加载失败"); });
    }

    function closeViewer() {
      overlay.classList.remove("show");
      closeZoom();
    }

    /* ---- 放大面板 ---- */
    function openZoom(idx) {
      if (!items.length || !zoomOverlay) return;
      zoomIndex = idx;
      renderZoom();
      zoomOverlay.style.display = "";
    }
    function closeZoom() {
      if (!zoomOverlay) return;
      zoomOverlay.style.display = "none";
      zoomIndex = -1;
    }
    function renderZoom() {
      var it = items[zoomIndex];
      if (!it || !wzImg) return;
      wzImg.src = _photoUrl(it.path);
      updateZoomButtons();
    }
    function moveZoom(delta) {
      if (!items.length) return;
      zoomIndex = (zoomIndex + delta + items.length) % items.length;
      renderZoom();
    }
    function updateZoomButtons() {
      var it = items[zoomIndex];
      if (!it) return;
      var d = document.getElementById("wzDiscard");
      var r = document.getElementById("wzRescue");
      if (d) d.classList.toggle("active", !it.rescued);
      if (r) r.classList.toggle("active", it.rescued);
    }
    function zoomSet(rescued) {
      var it = items[zoomIndex];
      if (!it || it.rescued === rescued) return;
      it.rescued = rescued;
      var el = itemEl(zoomIndex);
      if (el) el.classList.toggle("rescued", rescued);
      updateZoomButtons();
      setStatus(it.path, rescued);
    }

    // 三类小卡片 → 打开对应类别废片
    [["cardExposure", "exposure"], ["cardFocus", "focus"], ["cardFace", "face"]].forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (el) el.addEventListener("click", function () { openViewer(pair[1]); });
    });

    if (closeBtn) closeBtn.addEventListener("click", closeViewer);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) closeViewer(); });

    if (zoomOverlay) {
      var wzClose = document.getElementById("wzClose");
      var wzPrev = document.getElementById("wzPrev");
      var wzNext = document.getElementById("wzNext");
      var wzDiscard = document.getElementById("wzDiscard");
      var wzRescue = document.getElementById("wzRescue");
      if (wzClose) wzClose.addEventListener("click", closeZoom);
      if (wzPrev) wzPrev.addEventListener("click", function () { moveZoom(-1); });
      if (wzNext) wzNext.addEventListener("click", function () { moveZoom(1); });
      if (wzDiscard) wzDiscard.addEventListener("click", function () { zoomSet(false); });
      if (wzRescue) wzRescue.addEventListener("click", function () { zoomSet(true); });
      zoomOverlay.addEventListener("click", function (e) { if (e.target === zoomOverlay) closeZoom(); });
    }

    // 键盘：放大态下 a/d 切图、空格切换拯救与否、Esc 关闭
    if (PC.state.wasteKeyHandler) document.removeEventListener("keydown", PC.state.wasteKeyHandler);
    PC.state.wasteKeyHandler = function (e) {
      if (!zoomOverlay || zoomOverlay.style.display === "none") return;
      if (e.key === "a" || e.key === "A" || e.key === "ArrowLeft") {
        e.preventDefault(); moveZoom(-1);
      } else if (e.key === "d" || e.key === "D" || e.key === "ArrowRight") {
        e.preventDefault(); moveZoom(1);
      } else if (e.key === " ") {
        e.preventDefault();
        var it = items[zoomIndex];
        if (it) zoomSet(!it.rescued);
      } else if (e.key === "Escape") {
        closeZoom();
      }
    };
    document.addEventListener("keydown", PC.state.wasteKeyHandler);
  }

  /* ---------------- 三级过滤：启动与轮询 ---------------- */
  // 筛检读条完成后：分流（视频/照片分开）→ 启动三级废片过滤
  function onNormalFilterDone() {
    var pname = proj ? proj.nameEncoded : "";
    if (!pname) return;
    fetch("/api/project/" + pname + "/material-split", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status !== "ok") { setBurstEnabled(true); PC.showNotice("error", d.message || "分流失败"); return; }
        startQualityFilter(pname);
      })
      .catch(function () { setBurstEnabled(true); PC.showNotice("error", "分流启动失败"); });
  }

  function startQualityFilter(pname) {
    fetch("/api/project/" + pname + "/quality-filter-start", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status !== "ok") {
          setBurstEnabled(true);   // 被互斥拒绝或启动失败 → 恢复快筛按钮
          PC.showNotice(d.status === "busy" ? "warning" : "error",
                        d.message || "启动废片检测失败");
          return;
        }
        startQualityPolling();
      })
      .catch(function () {
        setBurstEnabled(true);
        PC.showNotice("error", "网络错误，请重试");
      });
  }

  function startQualityPolling() {
    var pname = proj ? proj.nameEncoded : "";
    if (!pname) return;
    if (_qualityPollTimer) { clearTimeout(_qualityPollTimer); _qualityPollTimer = null; }

    function poll() {
      fetch("/api/project/" + pname + "/quality-filter-progress")
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.status === "running") {
            updateQualityLive(s);
            _qualityPollTimer = setTimeout(poll, 350);
            return;
          }
          if (s.status === "done") {
            updateQualityLive(s);
            _qualityPollTimer = null;
            setBurstEnabled(true);   // 三级筛选结束 → 连拍快筛恢复可用
            PC.showNotice("success", s.message || "废片检测完成");
            return;
          }
          if (s.status === "error") {
            _qualityPollTimer = null;
            setBurstEnabled(true);   // 出错也要恢复，避免快筛按钮永久置灰
            PC.showNotice("error", s.message || "废片检测出错");
            return;
          }
          _qualityPollTimer = setTimeout(poll, 500);
        })
        .catch(function () { _qualityPollTimer = setTimeout(poll, 800); });
    }
    poll();
  }

  /* ---------------- 连拍组筛选模式 ---------------- */
  var _burstGroups = [];
  var _burstIndex = 0;        // 当前连拍组下标
  var _burstPhotoIndex = 0;   // 当前组内照片下标
  var _burstBusy = false;

  function initBurstViewer() {
    var zoomOverlay = document.getElementById("burstZoom");
    if (!zoomOverlay) return;
    var pname = proj ? proj.nameEncoded : "";

    /* ---- 进度弹窗（复用筛检进度弹窗） ---- */
    function showBurstProgress(title) {
      var m = document.getElementById("filterProgressModal");
      var h = document.getElementById("fpmHeader");
      if (h) h.textContent = title || "正在识别连拍组…";
      var fill = document.getElementById("fpmProgressFill");
      if (fill) fill.style.width = "0%";
      var d = document.getElementById("fpmDone");
      if (d) d.textContent = "0";
      var t = document.getElementById("fpmTotal");
      if (t) t.textContent = "0";
      if (m) m.style.display = "";
    }
    function updateBurstProgressModal(s) {
      var fill = document.getElementById("fpmProgressFill");
      if (fill && s.total > 0) fill.style.width = (s.percent || 0) + "%";
      var d = document.getElementById("fpmDone");
      if (d) d.textContent = String(s.done || 0);
      var t = document.getElementById("fpmTotal");
      if (t) t.textContent = String(s.total || 0);
    }
    function hideBurstProgress() {
      var m = document.getElementById("filterProgressModal");
      if (m) m.style.display = "none";
      var h = document.getElementById("fpmHeader");
      if (h) h.textContent = "正在筛检常规素材";
    }

    /* ---- 检测：启动 + 轮询 ---- */
    function pollBurstProgress() {
      fetch("/api/project/" + pname + "/burst-progress")
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.status === "running") {
            updateBurstProgressModal(s);
            setTimeout(pollBurstProgress, 400);
            return;
          }
          if (s.status === "done") {
            updateBurstProgressModal(s);
            setTimeout(function () {
              hideBurstProgress();
              fetch("/api/project/" + pname + "/burst-groups")
                .then(function (r) { return r.json(); })
                .then(function (d) {
                  _burstBusy = false;
                  if (!d || !d.exists || !(d.groups || []).length) {
                    PC.showNotice("info", "未识别到连拍组");
                    return;
                  }
                  _burstGroups = d.groups;
                  openBurstViewer();
                });
            }, 300);
            return;
          }
          if (s.status === "error") {
            hideBurstProgress();
            _burstBusy = false;
            PC.showNotice("error", s.message || "连拍检测出错");
            return;
          }
          setTimeout(pollBurstProgress, 500);
        })
        .catch(function () { setTimeout(pollBurstProgress, 800); });
    }

    function openBurstMode() {
      if (!pname || _burstBusy) return;
      _burstBusy = true;
      fetch("/api/project/" + pname + "/burst-groups")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          // 快照与最新三级筛选结果一致 → 直接展示，等价于一次刷新
          if (d && d.exists && !d.stale) {
            _burstBusy = false;
            if (!(d.groups || []).length) {
              PC.showNotice("info", "未识别到连拍组");
              return;
            }
            _burstGroups = d.groups;
            openBurstViewer();
            return;
          }
          // 未检测 或 快照已过期（含新增废片/拯救照片）→ 按最新结果重算
          return fetch("/api/project/" + pname + "/burst-start", { method: "POST" })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d && d.status !== "ok") {   // busy=三级筛选中(互斥)；error=建索引失败
                _burstBusy = false;
                PC.showNotice(d.status === "busy" ? "warning" : "error",
                              d.message || "连拍检测启动失败");
                return;
              }
              showBurstProgress("正在识别连拍组…");
              pollBurstProgress();
            });
        })
        .catch(function () {
          _burstBusy = false;
          PC.showNotice("error", "加载连拍组失败");
        });
    }

    /* ---- 网格渲染 ---- */
    function setBurstPhoto(path, keep) {
      var g = _burstGroups[_burstIndex];
      if (!g) return;
      fetch("/api/project/" + pname + "/burst-set-photo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: g.id, path: path, keep: keep })
      }).then(function (r) { return r.json(); })
        .then(function (d) { if (d && d.status !== "ok") PC.showNotice("error", d.message || "保存失败"); })
        .catch(function () {});
    }

    /* ---- 单张切换保留/舍弃 ---- */
    function _burstPhotos() {
      var g = _burstGroups[_burstIndex];
      return (g && g.photos) || [];
    }

    // 底部提示条：默认展示操作明细，越界时临时提示 2s
    var _burstHintTimer = null;
    var BURST_HINT_DEFAULT = "A/D 本组前后 · 空格 保留/舍弃 · Q/E 切换连拍组 · Esc 关闭";
    function burstHint(text) {
      var el = document.getElementById("bzHint");
      if (!el) return;
      if (_burstHintTimer) { clearTimeout(_burstHintTimer); _burstHintTimer = null; }
      el.textContent = text || BURST_HINT_DEFAULT;
      if (!text) return;
      _burstHintTimer = setTimeout(function () {
        _burstHintTimer = null;
        el.textContent = BURST_HINT_DEFAULT;
      }, 2000);
    }

    function updateBurstButtons() {
      var ph = _burstPhotos()[_burstPhotoIndex];
      if (!ph) return;
      var d = document.getElementById("bzDiscard");
      var r = document.getElementById("bzRescue");
      if (d) d.classList.toggle("active", !ph.keep);
      if (r) r.classList.toggle("active", ph.keep);
    }

    function renderBurstPhoto() {
      var g = _burstGroups[_burstIndex];
      if (!g) return;
      var photos = _burstPhotos();
      _burstPhotoIndex = Math.max(0, Math.min(_burstPhotoIndex, photos.length - 1));
      var ph = photos[_burstPhotoIndex];
      var img = document.getElementById("bzImg");
      if (ph && img) {
        img.src = _photoUrl(ph.path);
        img.alt = ph.name || ph.path;
      }
      var gi = document.getElementById("bzGroupInfo");
      if (gi) gi.textContent = "第 " + (_burstIndex + 1) + " / " + _burstGroups.length + " 组";
      var pi = document.getElementById("bzPhotoInfo");
      if (pi) pi.textContent = "本组 " + (photos.length ? (_burstPhotoIndex + 1) : 0) + " / " + photos.length + " 张";
      var rt = document.getElementById("bzRecTag");
      if (rt) rt.style.display = (ph && ph.recommended) ? "" : "none";
      updateBurstButtons();
      preloadBurstNeighbors();
    }

    function preloadBurstNeighbors() {
      var photos = _burstPhotos();
      [1, -1].forEach(function (d) {
        var j = _burstPhotoIndex + d;
        if (j < 0 || j >= photos.length) return;
        var im = new Image();
        im.src = _photoUrl(photos[j].path);
      });
    }

    /* ---- 本组前后 / 切换连拍组（越界提示，不环绕） ---- */
    function moveBurstPhoto(delta) {
      var photos = _burstPhotos();
      if (!photos.length) return;
      var j = _burstPhotoIndex + delta;
      if (j < 0) { burstHint("已经是本组第一张"); return; }
      if (j >= photos.length) { burstHint("已经是本组最后一张"); return; }
      _burstPhotoIndex = j;
      renderBurstPhoto();
    }

    function switchBurstGroup(delta) {
      if (!_burstGroups.length) return;
      var j = _burstIndex + delta;
      if (j < 0) { burstHint("已经是第一组"); return; }
      if (j >= _burstGroups.length) { burstHint("已经是最后一组"); return; }
      _burstIndex = j;
      _burstPhotoIndex = 0;
      renderBurstPhoto();
    }

    function burstZoomSet(keep) {
      var ph = _burstPhotos()[_burstPhotoIndex];
      if (!ph || ph.keep === keep) return;
      ph.keep = keep;
      updateBurstButtons();
      setBurstPhoto(ph.path, keep);
    }

    /* ---- 打开 / 关闭（放大即唯一的连拍筛选界面） ---- */
    function openBurstViewer() {
      if (!_burstGroups.length) return;
      _burstIndex = 0;          // 每次进入都从第一组第一张开始
      _burstPhotoIndex = 0;
      burstHint(BURST_HINT_DEFAULT);
      renderBurstPhoto();
      zoomOverlay.style.display = "";
    }
    function closeBurstViewer() {
      zoomOverlay.style.display = "none";
      if (_burstHintTimer) { clearTimeout(_burstHintTimer); _burstHintTimer = null; }
    }

    /* ---- 事件绑定 ---- */
    // 底部按钮常驻于 project.html，面板重载时需先解绑旧处理器，避免重复绑定
    var btnBurst = document.getElementById("btnBurst");
    if (btnBurst) {
      if (PC.state.burstBtnHandler) btnBurst.removeEventListener("click", PC.state.burstBtnHandler);
      PC.state.burstBtnHandler = function () {
        if (btnBurst.disabled) return;
        openBurstMode();
      };
      btnBurst.addEventListener("click", PC.state.burstBtnHandler);
    }

    var bzClose = document.getElementById("bzClose");
    var bzPrev = document.getElementById("bzPrev");
    var bzNext = document.getElementById("bzNext");
    var bzDiscard = document.getElementById("bzDiscard");
    var bzRescue = document.getElementById("bzRescue");
    if (bzClose) bzClose.addEventListener("click", closeBurstViewer);
    if (bzPrev) bzPrev.addEventListener("click", function () { moveBurstPhoto(-1); });
    if (bzNext) bzNext.addEventListener("click", function () { moveBurstPhoto(1); });
    if (bzDiscard) bzDiscard.addEventListener("click", function () { burstZoomSet(false); });
    if (bzRescue) bzRescue.addEventListener("click", function () { burstZoomSet(true); });
    zoomOverlay.addEventListener("click", function (e) { if (e.target === zoomOverlay) closeBurstViewer(); });

    // 键盘：a/d 本组前后、空格切换保留、q/e 切换连拍组、Esc 关闭
    if (PC.state.burstKeyHandler) document.removeEventListener("keydown", PC.state.burstKeyHandler);
    PC.state.burstKeyHandler = function (e) {
      if (zoomOverlay.style.display === "none") return;
      if (e.key === "a" || e.key === "A" || e.key === "ArrowLeft") {
        e.preventDefault(); moveBurstPhoto(-1);
      } else if (e.key === "d" || e.key === "D" || e.key === "ArrowRight") {
        e.preventDefault(); moveBurstPhoto(1);
      } else if (e.key === "q" || e.key === "Q") {
        e.preventDefault(); switchBurstGroup(-1);
      } else if (e.key === "e" || e.key === "E") {
        e.preventDefault(); switchBurstGroup(1);
      } else if (e.key === " ") {
        e.preventDefault();
        var ph = _burstPhotos()[_burstPhotoIndex];
        if (ph) burstZoomSet(!ph.keep);
      } else if (e.key === "Escape") {
        closeBurstViewer();
      }
    };
    document.addEventListener("keydown", PC.state.burstKeyHandler);
  }

  // 注册到公共分发器
  PC.registerPanel(2, initFilterPanel);
})();
