/* ===========================================================================
   交付面板 (step 4) — 万流归宗粒子流 / 圆盘扫光 / 输出目录选择 /
   验证码解锁 / 双横向进度条 / 拷贝完成校验后重置页面
   依赖 window.ProjectCommon（project-common.js）
   =========================================================================== */
(function () {
  "use strict";

  var PC = window.ProjectCommon;
  var proj = PC.proj;

  var _destDir = "";
  var _delivering = false; // 全局交付态（粒子加速 / 解锁按钮）
  var _barCount = 24;

  var _streamRaf = null;

  /* ================= 万流归宗粒子流（Canvas） ================= */

  function startStream() {
    var panel = document.getElementById("deliverPanel");
    var canvas = document.getElementById("deliverStream");
    if (!panel || !canvas) return;

    var ctx = canvas.getContext("2d");
    var W = 0, H = 0;
    var particles = [];
    var spawnTimer = 0;

    function resize() {
      var rect = panel.getBoundingClientRect();
      var dpr = window.devicePixelRatio || 1;
      W = rect.width;
      H = rect.height;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width = W + "px";
      canvas.style.height = H + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();

    function coreCenter() {
      var core = document.getElementById("deliverCore");
      if (!core) return { x: W * 0.5, y: H * 0.5 };
      var pr = panel.getBoundingClientRect();
      var cr = core.getBoundingClientRect();
      return { x: cr.left - pr.left + cr.width / 2, y: cr.top - pr.top + cr.height / 2 };
    }

    function spawn() {
      var colors = ["91,182,212", "74,133,117", "108,224,187", "61,99,144"];
      var y = Math.random() * H;
      var vel = 2.4 + Math.random() * 3.2;   // 搬运时高速推进
      particles.push({
        x: -10 - Math.random() * 40,
        y: y,
        vx: vel,
        wobbleAmp: 6 + Math.random() * 18,
        wobbleSpeed: 0.05 + Math.random() * 0.05,
        wobblePhase: Math.random() * Math.PI * 2,
        r: 0.9 + Math.random() * 1.5,      // 更纤细：粒度 0.9~2.4
        color: colors[(Math.random() * colors.length) | 0],
        seed: Date.now() + Math.random() * 1000,
      });
    }

    function tick(t) {
      if (_streamRaf === null) return; // 已停止
      if (canvas.width === 0 || canvas.height === 0) {
        _streamRaf = requestAnimationFrame(tick);
        return;
      }
      var c = coreCenter();

      // 粒子「万流归宗」仅在搬运（交付）过程中播放
      if (!_delivering) {
        if (ctx) ctx.clearRect(0, 0, W, H);
        _streamRaf = requestAnimationFrame(tick);
        return;
      }

      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";
      ctx.lineCap = "round";

      // 高速生成粒子
      spawnTimer += 2.4;
      var target = 230;   // 粒子更多，氛围更浓
      if (spawnTimer >= 3 && particles.length < target) { spawn(); spawnTimer = 0; }

      // 右侧少量汇入，强化“万流归宗”汇聚感
      if (Math.random() < 0.07 && particles.length < target) {
        particles.push({
          x: W + 8,
          y: Math.random() * H,
          vx: -(1.8 + Math.random() * 1.8),
          wobbleAmp: 4 + Math.random() * 12,
          wobbleSpeed: 0.05 + Math.random() * 0.05,
          wobblePhase: Math.random() * Math.PI * 2,
          r: 0.8 + Math.random() * 1.2,   // 更纤细
          color: ["91,182,212", "74,133,117"][(Math.random() * 2) | 0],
          seed: t + Math.random() * 1000,
        });
      }

      for (var i = particles.length - 1; i >= 0; i--) {
        var p = particles[i];
        var dx = c.x - p.x;
        var dy = c.y - p.y;
        var dist = Math.sqrt(dx * dx + dy * dy);

        // 逼近核心后淡出
        if (dist < 70 || p.x > W + 30 || p.x < -30 || p.y < -30 || p.y > H + 30) {
          particles.splice(i, 1);
          continue;
        }

        var nx = dx / (dist || 1);
        var ny = dy / (dist || 1);
        var wob = Math.sin(p.seed * 0.001 + t * 0.003 + p.wobblePhase) * p.wobbleAmp;
        p.x += (nx * p.vx + (-ny) * wob * 0.03);
        p.y += (ny * p.vx + nx * wob * 0.03);

        // 越靠近核心速度越快（加速汇聚）→ 拉丝更长
        var alpha = Math.max(0, 1 - dist / (Math.max(W, H) * 0.72));
        var trailLen = p.r * 11 + p.vx * 3.2;   // 彗尾更长（华丽），随速度拉长

        // 拉丝彗尾（沿运动方向反向拖出，越远越淡）
        var gradW = ctx.createLinearGradient(p.x, p.y, p.x - nx * trailLen, p.y - ny * trailLen);
        gradW.addColorStop(0, "rgba(" + p.color + "," + (alpha * 0.9).toFixed(3) + ")");
        gradW.addColorStop(1, "rgba(" + p.color + ",0)");
        ctx.shadowColor = "rgba(" + p.color + ",0.75)";
        ctx.shadowBlur = 9;                     // 更柔光晕
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x - nx * trailLen, p.y - ny * trailLen);
        ctx.strokeStyle = gradW;
        ctx.lineWidth = p.r * 1.05;             // 更纤细线条
        ctx.stroke();

        // 头部亮点（更大迸发感）
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r * 1.15, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(255,255,255," + (alpha * 0.95).toFixed(3) + ")";
        ctx.fill();
      }

      ctx.globalCompositeOperation = "source-over";
      _streamRaf = requestAnimationFrame(tick);
    }

    stopStream();
    _streamRaf = requestAnimationFrame(tick);

    PC.state._deliverStreamResize = resize;
    window.addEventListener("resize", resize);
  }

  function stopStream() {
    if (_streamRaf !== null) {
      cancelAnimationFrame(_streamRaf);
      _streamRaf = null;
    }
    if (PC.state._deliverStreamResize) {
      window.removeEventListener("resize", PC.state._deliverStreamResize);
      PC.state._deliverStreamResize = null;
    }
  }

  /* ================= 进度条（分段柱条，横置放大） ================= */

  function buildBar(trackEl) {
    if (!trackEl) return;
    trackEl.innerHTML = "";
    for (var i = 0; i < _barCount; i++) {
      var d = document.createElement("div");
      d.className = "dbar";
      trackEl.appendChild(d);
    }
  }

  function updateBar(trackId, numId, color, pct) {
    var track = document.getElementById(trackId);
    var num = document.getElementById(numId);
    if (!track) return;
    var bars = track.children;
    var fillCount = Math.round(pct * bars.length);
    if (fillCount > bars.length) fillCount = bars.length;
    var onCls = color === "green" ? "on-green" : "on-blue";
    for (var i = 0; i < bars.length; i++) {
      bars[i].classList.toggle(onCls, i < fillCount);
    }
    if (num) num.textContent = Math.round(pct * 100) + "%";
  }

  /* ================= 输出目录选择 ================= */

  function bindFolderPicker() {
    var pickBtn = document.getElementById("deliverPickBtn");
    var prompt = document.getElementById("deliverDestPrompt");
    var nameEl = document.getElementById("deliverDestName");
    if (!pickBtn) return;
    pickBtn.addEventListener("click", function () {
      if (_delivering) return;
      fetch("/api/pick-folder", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status !== "ok" || !data.path) return;
          _destDir = data.path;
          prompt.style.display = "none";
          nameEl.textContent = data.path;
          nameEl.style.display = "";
          var keepWrap = document.getElementById("deliverKeepMediaWrap");
          if (keepWrap) keepWrap.style.display = "";
          setNextEnabled(_destDir !== "" && !_delivering);
        })
        .catch(function () {});
    });
  }

  /* ================= 下一步按钮 → 确认交付 ================= */

  function setNextEnabled(enabled) {
    var btnNext = document.getElementById("btnNext");
    if (!btnNext) return;
    btnNext.disabled = !enabled;
    btnNext.classList.toggle("disabled", !enabled);
  }

  function setDeliverBusy(busy) {
    _delivering = busy;
    PC.state.panelBusy = busy;
    var pickBtn = document.getElementById("deliverPickBtn");
    if (pickBtn) {
      pickBtn.disabled = busy;
      pickBtn.classList.toggle("disabled", busy);
    }
    setNextEnabled(_destDir !== "" && !busy);
  }

  /* ================= 验证码确认弹窗（苹果式四框输码） ================= */

  var _expectCode = "";
  var _codeLocked = false;

  // 绑定四个输码框的一次性事件（启动时调用一次）
  function bindCodeSlots() {
    var slots = document.querySelectorAll(".deliver-slot");
    if (!slots.length) return;

    function getSlots() {
      return Array.prototype.slice.call(document.querySelectorAll(".deliver-slot"));
    }
    function focusIdx(idx) {
      var s = getSlots();
      if (s[idx]) s[idx].focus();
    }
    function setFilled() {
      var s = getSlots();
      for (var i = 0; i < s.length; i++) {
        s[i].classList.toggle("filled", s[i].value !== "");
      }
    }

    for (var i = 0; i < slots.length; i++) {
      (function (idx) {
        slots[idx].addEventListener("keydown", function (e) {
          // 退格：当前为空则回退并清除上一位
          if (e.key === "Backspace") {
            if (this.value === "" && idx > 0) {
              var prev = getSlots()[idx - 1];
              prev.value = "";
              prev.classList.remove("filled");
              prev.focus();
              this.value = "";
            }
            return;
          }
          if (e.key === "ArrowLeft" && idx > 0) focusIdx(idx - 1);
          if (e.key === "ArrowRight" && idx < getSlots().length - 1) focusIdx(idx + 1);
        });
        slots[idx].addEventListener("input", function () {
          var v = this.value.replace(/\D/g, "").slice(0, 1);
          this.value = v;
          setFilled();
          if (v !== "" && idx < getSlots().length - 1) {
            focusIdx(idx + 1);
            return;
          }
          // 填满最后一位 → 自动判定
          var combined = getSlots().map(function (s) { return s.value; }).join("");
          if (combined.length === 4) {
            if (combined === _expectCode && !_codeLocked) {
              _codeLocked = true;
              closeCodeModal();
              startDelivery();
            } else if (!_codeLocked) {
              failCode();
            }
          }
        });
        slots[idx].addEventListener("paste", function (e) {
          e.preventDefault();
          var txt = (e.clipboardData || window.clipboardData).getData("text").replace(/\D/g, "").slice(0, 4);
          if (!txt) return;
          var s = getSlots();
          for (var k = 0; k < txt.length && k < s.length; k++) {
            s[k].value = txt[k];
          }
          setFilled();
          if (txt.length >= 4) {
            if (txt === _expectCode && !_codeLocked) {
              _codeLocked = true;
              closeCodeModal();
              startDelivery();
            } else if (!_codeLocked) {
              failCode();
            }
          } else {
            focusIdx(txt.length);
          }
        });
      })(i);
    }

    function failCode() {
      var err = document.getElementById("deliverCodeErr");
      var wrap = document.getElementById("deliverCodeSlots");
      if (err) err.textContent = "验证码错误，请重试";
      if (wrap) {
        wrap.classList.remove("shake");
        void wrap.offsetWidth;
        wrap.classList.add("shake");
      }
      var s = getSlots();
      s.forEach(function (x) { x.value = ""; x.classList.remove("filled"); });
      setTimeout(function () { s[0] && s[0].focus(); }, 30);
      setTimeout(function () { if (err) err.textContent = ""; }, 1600);
    }
  }

  function startCodeModal() {
    if (_delivering) return;
    if (!_destDir) { PC.showNotice("error", "请先选择输出目录"); return; }

    var modal = document.getElementById("deliverCodeModal");
    var show = document.getElementById("deliverCodeShow");
    var err = document.getElementById("deliverCodeErr");

    var code = String(Math.floor(1000 + Math.random() * 9000));
    _expectCode = code;
    _codeLocked = false;
    if (show) show.textContent = code;
    if (err) err.textContent = "";

    var s = document.querySelectorAll(".deliver-slot");
    for (var i = 0; i < s.length; i++) { s[i].value = ""; s[i].classList.remove("filled"); }

    modal.style.display = "";
    PC.state.panelBusy = true;
    setTimeout(function () { s[0] && s[0].focus(); }, 80);
  }

  function closeCodeModal() {
    var modal = document.getElementById("deliverCodeModal");
    if (modal) modal.style.display = "none";
    PC.state.panelBusy = false;
  }

  /* ================= 发起交付 ================= */

  function startDelivery() {
    setDeliverBusy(true);
    updateBar("deliverBarTotal", "deliverBarTotalNum", "green", 0);
    updateBar("deliverBarCurrent", "deliverBarCurrentNum", "blue", 0);
    PC.ensureSSE(proj.nameEncoded);

    fetch("/api/project/" + proj.nameEncoded + "/deliver-start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destDir: _destDir, keepMedia: !!(document.getElementById("deliverKeepMedia") || {}).checked }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status !== "ok") {
          setDeliverBusy(false);
          PC.showNotice("error", data.message || "交付启动失败");
        }
      })
      .catch(function () {
        setDeliverBusy(false);
        PC.showNotice("error", "网络错误，交付启动失败");
      });
  }

  /* ================= 交付进度（SSE） ================= */

  function handleDeliverData(data) {
    if (!data.status || data.status === "not_found") return;

    var leftPct = data.total > 0 ? data.done / data.total : 0;
    var rightPct = data.current_file_total > 0 ? data.current_file_done / data.current_file_total : 0;
    updateBar("deliverBarTotal", "deliverBarTotalNum", "green", leftPct);
    updateBar("deliverBarCurrent", "deliverBarCurrentNum", "blue", rightPct);

    if (data.status === "copying" || data.status === "verify") {
      setDeliverBusy(true);
      return;
    }

    setDeliverBusy(false);

    if (data.status === "done") {
      // 校验一致且已清空素材 → 提示完成 → 退出到主页
      PC.showNotice("success", data.message || "交付完成，校验一致", function () {
        resetDeliverAndExit();
      });
    } else if (data.status === "mismatch") {
      PC.showNotice("error", data.message || "拷贝文件校验不一致");
    } else if (data.status === "error") {
      PC.showNotice("error", "交付失败：" + (data.message || "未知错误"));
    }
  }

  /* ================= 重置交付页 ================= */

  function resetDeliver() {
    fetch("/api/project/" + proj.nameEncoded + "/deliver-reset", { method: "POST" })
      .catch(function () {})
      .finally(function () {
        PC.loadPanel(4);
      });
  }

  // 交付成功校验一致后：清理交付态并退出到主页
  function resetDeliverAndExit() {
    fetch("/api/project/" + proj.nameEncoded + "/deliver-reset", { method: "POST" })
      .catch(function () {})
      .finally(function () {
        window.location.href = "/main";
      });
  }

  /* ================= 面板初始化 ================= */

  function initDeliverPanel() {
    stopStream();
    PC.state.panelBusy = false;
    _delivering = false;
    _destDir = "";
    var keepWrap = document.getElementById("deliverKeepMediaWrap");
    var keepCb = document.getElementById("deliverKeepMedia");
    if (keepWrap) keepWrap.style.display = "none";
    if (keepCb) keepCb.checked = false;

    buildBar(document.getElementById("deliverBarTotal"));
    buildBar(document.getElementById("deliverBarCurrent"));
    updateBar("deliverBarTotal", "deliverBarTotalNum", "green", 0);
    updateBar("deliverBarCurrent", "deliverBarCurrentNum", "blue", 0);

    bindFolderPicker();
    bindCodeSlots();

    // 交付步骤不涉及批处理指示灯（照片处理/视频压缩/水印添加，step1 专用），隐藏
    var indGroup = document.getElementById("indicatorGroup");
    if (indGroup) indGroup.style.display = "none";

    // 下一步按钮 → 确认交付
    var btnNext = document.getElementById("btnNext");
    if (btnNext) btnNext.textContent = "确认交付";

    var cancelBtn = document.getElementById("deliverCodeCancel");
    if (cancelBtn) cancelBtn.addEventListener("click", closeCodeModal);
    var modal = document.getElementById("deliverCodeModal");
    if (modal) {
      modal.addEventListener("click", function (e) {
        if (e.target === modal) closeCodeModal();
      });
    }

    PC.setBtnNextHandler(startCodeModal);
    setNextEnabled(false);
    setDeliverBusy(false);

    // 恢复进度：若上次交付仍在拷贝/校验，恢复忙碌与进度
    fetch("/api/project/" + proj.nameEncoded + "/deliver-progress")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || data.status === "not_found") return;
        if (data.status === "copying" || data.status === "verify") {
          var lp = data.total > 0 ? data.done / data.total : 0;
          var rp = data.current_file_total > 0 ? data.current_file_done / data.current_file_total : 0;
          updateBar("deliverBarTotal", "deliverBarTotalNum", "green", lp);
          updateBar("deliverBarCurrent", "deliverBarCurrentNum", "blue", rp);
          setDeliverBusy(true);
        } else if (data.status === "done" || data.status === "mismatch" || data.status === "error") {
          // 陈旧终态 → 静默重置
          fetch("/api/project/" + proj.nameEncoded + "/deliver-reset", { method: "POST" }).catch(function () {});
        }
      })
      .catch(function () {});

    startStream();

    // 确保 SSE 已连接
    if (proj) PC.ensureSSE(proj.nameEncoded);
  }

  PC.registerPanel(4, initDeliverPanel);
  PC.registerSseHandler("deliver", handleDeliverData);
})();