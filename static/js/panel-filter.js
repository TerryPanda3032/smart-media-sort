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
  var _aiPollTimer = null;
  var _aiAnimTimer = null;
  var _rescuedPaths = {};
  var _aiDone = false;

  function initFilterPanel() {
    PC.state.panelBusy = false;
    if (_filterPollTimer) { clearTimeout(_filterPollTimer); _filterPollTimer = null; }
    if (_fakeProgressTimer) { clearInterval(_fakeProgressTimer); _fakeProgressTimer = null; }
    var pfEl = document.querySelector(".panel-filter");
    if (pfEl) {
      pfEl.classList.remove("pf-text-appeared", "pf-ring-settled", "pf-cards-settled",
                             "pf-arcs-shrinking", "pf-arcs-spinning", "pf-inner-spinning");
    }
    initFilterRing();
    var indGroup = document.getElementById("indicatorGroup");
    if (indGroup) indGroup.style.display = "none";
    _rescuedPaths = {};
    _aiDone = false;
    var btnNext = document.getElementById("btnNext");
    if (btnNext) btnNext.classList.add("disabled");
    // 下一步按钮
    PC.setBtnNextHandler(function () {
      if (PC.state.panelBusy) return;
      if (!_aiDone) { PC.showNotice("warning", "请先完成 AI 筛检"); return; }
      var modal = document.getElementById("filterConfirmModal");
      if (modal) modal.style.display = "";
    });
    initFilterConfirm();
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

    function polarToCartesian(cx, cy, r, angleDeg) {
      var rad = (angleDeg - 90) * Math.PI / 180;
      return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
    }

    function describeArc(cx, cy, r, startAngle, endAngle) {
      var start = polarToCartesian(cx, cy, r, endAngle);
      var end = polarToCartesian(cx, cy, r, startAngle);
      var largeArc = endAngle - startAngle <= 180 ? "0" : "1";
      return "M " + start.x.toFixed(1) + " " + start.y.toFixed(1) + " A " + r + " " + r + " 0 " + largeArc + " 0 " + end.x.toFixed(1) + " " + end.y.toFixed(1);
    }

    function describeBrushArc(cx, cy, r, startAngle, endAngle, maxWidth, segments) {
      segments = segments || 60;
      var rad = function(deg) { return (deg - 90) * Math.PI / 180; };
      var pt = function(angle, radius) {
        return { x: cx + radius * Math.cos(rad(angle)), y: cy + radius * Math.sin(rad(angle)) };
      };
      var outer = [];
      var inner = [];
      for (var i = 0; i <= segments; i++) {
        var t = i / segments;
        var angle = startAngle + (endAngle - startAngle) * t;
        var half = (maxWidth / 2) * Math.sin(Math.PI * t);
        outer.push(pt(angle, r + half));
        inner.push(pt(angle, r - half));
      }
      var d = "M " + outer[0].x.toFixed(1) + " " + outer[0].y.toFixed(1);
      for (var i = 1; i < outer.length; i++) d += " L " + outer[i].x.toFixed(1) + " " + outer[i].y.toFixed(1);
      for (var i = inner.length - 1; i >= 0; i--) d += " L " + inner[i].x.toFixed(1) + " " + inner[i].y.toFixed(1);
      d += " Z";
      return d;
    }

    function setArcs() {
      var size = ringWrap.offsetWidth;
      if (size <= 0) return;
      var c = size / 2;

      var innerSvg = document.getElementById("arcSvgInner");
      if (innerSvg) {
        innerSvg.setAttribute("width", size);
        innerSvg.setAttribute("height", size);
        innerSvg.setAttribute("viewBox", "0 0 " + size + " " + size);
        var ir1 = (size / 2) - 36;
        var ir2 = (size / 2) - 16;
        var el1 = document.getElementById("innerArc1");
        var el2 = document.getElementById("innerArc2");
        if (el1) el1.setAttribute("d", describeArc(c, c, ir1, 205, 335) + " " + describeArc(c, c, ir1, 25, 155));
        if (el2) el2.setAttribute("d", describeArc(c, c, ir2, 215, 325) + " " + describeArc(c, c, ir2, 35, 145));
      }

      var outerSub = document.getElementById("arcSvgOuterSubtle");
      var outerMain = document.getElementById("arcSvgOuterMain");
      [outerSub, outerMain].forEach(function(svg) {
        if (!svg) return;
        svg.setAttribute("width", size);
        svg.setAttribute("height", size);
        svg.setAttribute("viewBox", "0 0 " + size + " " + size);
      });
      var mainRadius = (size / 2) - 10;
      var orOuter = size * 0.78;
      var llMain = document.getElementById("arcLL_main");
      var llSub = document.getElementById("arcLL_sub");
      var urMain = document.getElementById("arcUR_main");
      var urSub = document.getElementById("arcUR_sub");
      if (llMain) llMain.setAttribute("d", describeBrushArc(c, c, orOuter, 180, 270, 3));
      if (llSub) llSub.setAttribute("d", describeBrushArc(c, c, mainRadius, 180, 270, 1.8));
      if (urMain) urMain.setAttribute("d", describeBrushArc(c, c, orOuter, 0, 90, 3));
      if (urSub) urSub.setAttribute("d", describeBrushArc(c, c, mainRadius, 0, 90, 1.8));
    }

    setArcs();

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
        // ① 关弹窗 + 文字出现（环中心数字已在长按时固定，不再变动）
        hideFilterModal();
        if (ringStart) ringStart.style.display = "none";
        if (ringProgressInfo) ringProgressInfo.style.display = "";
        void ringProgressInfo.offsetHeight;
        progressCircle.style.transition = "";
        progressCircle.style.strokeDashoffset = "0";
        _pfHoldCompleted = true;
        var pfEl = document.querySelector(".panel-filter");
        if (pfEl) pfEl.classList.add("pf-text-appeared");

        // ② 500ms → 环左移 + 内容区显现
        setTimeout(function() {
          if (pfEl) pfEl.classList.add("pf-ring-settled");

          // ③ 700ms → 卡片浮现
          setTimeout(function() {
            if (pfEl) pfEl.classList.add("pf-cards-settled");

            // ④ 1200ms → 外环缩小（等待全部卡片浮现完毕）
            setTimeout(function() {
              if (pfEl) pfEl.classList.add("pf-arcs-shrinking");

              // ⑤ 500ms → 外环旋转（保持缩小状态）
              setTimeout(function() {
                if (pfEl) pfEl.classList.add("pf-arcs-spinning");

                // ⑥ 300ms → 内环旋转
                setTimeout(function() {
                  if (pfEl) pfEl.classList.add("pf-inner-spinning");
                  if (onComplete) onComplete();
                }, 300);

              }, 500);

            }, 1200);

          }, 700);

        }, 500);

      }, minDelay);
    }

    function updateAiProgress(done, total) {
      if (_aiAnimTimer) { clearInterval(_aiAnimTimer); _aiAnimTimer = null; }
      var doneEl = document.getElementById("rpDone");
      if (!doneEl) return;

      var currentDisplay = parseInt(doneEl.textContent) || 0;
      if (done <= currentDisplay) return;

      var diff = done - currentDisplay;
      var step = 1;
      var interval = Math.max(10, Math.min(80, Math.floor(500 / diff)));

      var running = currentDisplay;
      _aiAnimTimer = setInterval(function() {
        running += step;
        if (running >= done) {
          running = done;
          clearInterval(_aiAnimTimer);
          _aiAnimTimer = null;
        }
        doneEl.textContent = String(running);
        var pctEl = document.getElementById("rpPercent");
        if (pctEl && total > 0) {
          pctEl.innerHTML = Math.round(running / total * 100) + '<span class="rp-pct">%</span>';
        }
      }, interval);
    }

    function startAiFilterPolling() {
      if (_aiPollTimer) return;
      var pname = proj ? proj.nameEncoded : "";
      if (!pname) return;

      function poll() {
        fetch("/api/project/" + pname + "/ai-filter-progress")
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.status === "running") {
              updateAiProgress(data.done, data.total);
            } else if (data.status === "done") {
              if (rpDone) rpDone.textContent = String(data.total || 0);
              if (rpPercent) rpPercent.innerHTML = '100<span class="rp-pct">%</span>';
              var pfEl = document.querySelector(".panel-filter");
              if (pfEl) pfEl.classList.remove("pf-filtering");
              PC.showNotice("success", data.message || "AI 筛检完成");
              _aiPollTimer = null;
              fetchSummaryAndAnimate();
              enableNextBtn();
              return;
            } else if (data.status === "error") {
              var pfEl = document.querySelector(".panel-filter");
              if (pfEl) pfEl.classList.remove("pf-filtering");
              PC.showNotice("error", data.message || "AI 筛检出错");
              _aiPollTimer = null;
              return;
            }
            _aiPollTimer = setTimeout(poll, 500);
          })
          .catch(function () {
            _aiPollTimer = setTimeout(poll, 1000);
          });
      }
      poll();
    }

    function startAiFilter() {
      fetch("/api/project/" + proj.nameEncoded + "/ai-filter-summary")
        .then(function (r) { return r.json(); })
        .then(function (s) {
          if (s.exists) {
            var total = (s.type2 || 0) + (s.type3 || 0) + (s.type4 || 0);
            if (rpTotal) rpTotal.textContent = String(total);
            if (rpDone) rpDone.textContent = String(total);
            if (rpPercent) rpPercent.innerHTML = '100<span class="rp-pct">%</span>';
            var pfEl = document.querySelector(".panel-filter");
            if (pfEl) pfEl.classList.remove("pf-filtering");
            fetchSummaryAndAnimate();
            enableNextBtn();
            PC.showNotice("success", "已存在分类结果，直接加载");
            return;
          }
          fetch("/api/project/" + proj.nameEncoded + "/ai-filter-count")
            .then(function (r) { return r.json(); })
            .then(function (d) {
              var total = d.total || 0;
              if (rpTotal) rpTotal.textContent = String(total);
              if (rpDone) rpDone.textContent = "0";
              if (rpPercent) rpPercent.innerHTML = '0<span class="rp-pct">%</span>';
              var pfEl = document.querySelector(".panel-filter");
              if (pfEl) pfEl.classList.add("pf-filtering");
              return fetch("/api/project/" + proj.nameEncoded + "/ai-filter-start", { method: "POST" });
            })
            .then(function (r) { return r.json(); })
            .then(function (d2) {
              if (d2.status !== "ok") {
                var pfEl = document.querySelector(".panel-filter");
                if (pfEl) pfEl.classList.remove("pf-filtering");
                PC.showNotice("error", d2.message || "启动 AI 筛检失败");
                return;
              }
              startAiFilterPolling();
            })
            .catch(function () {
              var pfEl = document.querySelector(".panel-filter");
              if (pfEl) pfEl.classList.remove("pf-filtering");
              PC.showNotice("error", "网络错误，请重试");
            });
        })
        .catch(function () {
          PC.showNotice("error", "检查分类结果失败");
        });
    }

    function fetchSummaryAndAnimate() {
      var pname = proj ? proj.nameEncoded : "";
      if (!pname) return;
      fetch("/api/project/" + pname + "/ai-filter-summary")
        .then(function (r) { return r.json(); })
        .then(function (s) {
          var counts = {2: s.type2 || 0, 3: s.type3 || 0, 4: s.type4 || 0};
          Object.keys(counts).forEach(function (type) {
            var target = counts[type];
            var el = document.getElementById("wasteCount" + type);
            if (!el) return;
            var cur = 0;
            var diff = target - cur;
            if (diff <= 0) { el.textContent = String(target); return; }
            var step = Math.max(1, Math.floor(diff / 20));
            var interval = Math.max(12, Math.min(60, Math.floor(400 / diff * step)));
            var t = setInterval(function () {
              cur += step;
              if (cur >= target) {
                cur = target;
                clearInterval(t);
              }
              el.textContent = String(cur);
            }, interval);
          });
        })
        .catch(function () {});
    }

    function initWasteViewer() {
      var overlay = document.getElementById("wasteViewer");
      if (!overlay) return;
      var closeBtn = document.getElementById("wasteViewerClose");
      var titleEl = document.getElementById("wasteViewerTitle");
      var gridEl = document.getElementById("wasteViewerGrid");
      var pname = proj ? proj.nameEncoded : "";

      function openViewer(type) {
        var labels = {2: "过曝欠曝", 3: "内容无物", 4: "文件损坏"};
        var label = labels[type] || "废片";
        fetch("/api/project/" + pname + "/ai-filter-photos?type=" + type)
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var paths = d.paths || [];
            titleEl.textContent = label + " — 共 " + paths.length + " 张";
            gridEl.innerHTML = "";
            if (paths.length === 0) {
              gridEl.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:60px 0;color:rgba(0,0,0,0.35);font-size:16px">暂无废片</div>';
            } else {
              paths.forEach(function (p) {
                var item = document.createElement("div");
                item.className = "waste-viewer-item";
                if (_rescuedPaths[p]) item.classList.add("rescued");
                var img = document.createElement("img");
                img.src = "/api/project/" + pname + "/photo-file/" + p.split(/[\\/]/).map(encodeURIComponent).join("/");
                img.alt = p;
                img.onerror = function () { this.style.display = "none"; };
                item.appendChild(img);
                item.dataset.path = p;
                item.addEventListener("click", function () {
                  if (_rescuedPaths[p]) {
                    _rescuedPaths[p] = false;
                    delete _rescuedPaths[p];
                    this.classList.remove("rescued");
                  }
                });
                item.addEventListener("dblclick", function () {
                  if (!_rescuedPaths[p]) {
                    _rescuedPaths[p] = true;
                    this.classList.add("rescued");
                  }
                });
                gridEl.appendChild(item);
              });
            }
            overlay.classList.add("show");
          })
          .catch(function () { PC.showNotice("error", "加载失败"); });
      }

      function closeViewer() {
        overlay.classList.remove("show");
        // 提交拯救到后端
        var rescued = Object.keys(_rescuedPaths);
        if (rescued.length > 0) {
          var queue = rescued.slice();
          function doNext() {
            if (queue.length === 0) {
              fetchSummaryAndAnimate();
              return;
            }
            var p = queue.shift();
            fetch("/api/project/" + pname + "/ai-filter-rescue", {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({path: p})
            }).then(function (r) { return r.json(); })
              .then(function (res) {
                if (res.status === "ok") {
                  _rescuedPaths[p] = false;
                  delete _rescuedPaths[p];
                }
                doNext();
              })
              .catch(function () { doNext(); });
          }
          doNext();
        }
        gridEl.innerHTML = "";
      }

      document.querySelectorAll(".pf-card-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var type = parseInt(btn.getAttribute("data-type"));
          if (type) openViewer(type);
        });
      });

      if (closeBtn) closeBtn.addEventListener("click", closeViewer);
      overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeViewer();
      });
    }

    initWasteViewer();

    function enableNextBtn() {
      _aiDone = true;
      var btn = document.getElementById("btnNext");
      if (btn) {
        btn.removeAttribute("disabled");
        btn.classList.remove("disabled");
        btn.classList.add("lit");
      }
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
                  startAiFilter();
                }, 2000);
              });
              return;
            } else if (data.status === "error") {
              hideFilterModal();
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
            PC.showNotice("error", "网络错误，请重试");
          });
      }
    }

    function startHold() {
      if (_pfHoldCompleted) return;
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
      setArcs();
      if (_pfHoldCompleted) {
        progressCircle.style.transition = "none";
        progressCircle.style.strokeDashoffset = "0";
      } else {
        progressCircle.style.transition = "none";
        progressCircle.style.strokeDashoffset = circumference;
      }
    };
    window.addEventListener("resize", PC.state.filterResizeHandler);
  }

  // 注册到公共分发器
  PC.registerPanel(2, initFilterPanel);
})();
