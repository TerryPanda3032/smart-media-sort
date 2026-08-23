(function () {
  "use strict";

  var btn = document.getElementById("launchBtn");
  var col = document.getElementById("startCol");
  var statusEl = document.getElementById("startStatus");
  var body = document.body;

  var LAKE_BLUE = "61, 99, 144";
  var DURATION = 2400;
  var MORPH_MS = 620;

  var fill = null;
  var particleRafs = [];
  var progressDone = false;
  var preflightDone = false;
  var preflightResult = null;

  function spawnParticle(edgeX, h) {
    var el = document.createElement("div");
    var size = 4 + Math.random() * 5;
    var px = edgeX + (Math.random() - 0.5) * 4;
    var py = h / 2 + (Math.random() - 0.5) * h * 0.7;
    el.style.cssText =
      "position:absolute;left:" + px + "px;top:" + py + "px;" +
      "width:" + size + "px;height:" + size + "px;border-radius:50%;" +
      "background:rgba(" + LAKE_BLUE + ",0.85);pointer-events:none;" +
      "opacity:1";
    btn.appendChild(el);

    var vx = (Math.random() - 0.5) * 1.6;
    var vy = (Math.random() - 0.5) * 1.6;
    var life = 1;
    var rafId;

    function step() {
      life -= 0.022;
      if (life <= 0) { el.remove(); return; }
      px += vx;
      py += vy;
      el.style.left = px + "px";
      el.style.top = py + "px";
      el.style.opacity = (life * 0.75).toFixed(2);
      el.style.width = (size * life) + "px";
      el.style.height = (size * life) + "px";
      rafId = requestAnimationFrame(step);
    }
    rafId = requestAnimationFrame(step);
    particleRafs.push(rafId);
  }

  function cleanupParticles() {
    for (var i = 0; i < particleRafs.length; i++) {
      cancelAnimationFrame(particleRafs[i]);
    }
    particleRafs = [];
    var particles = btn.querySelectorAll("div:not(.progress-fill)");
    for (var i = 0; i < particles.length; i++) {
      particles[i].remove();
    }
  }

  function runFakeProgress(onDone) {
    var start = performance.now();

    function step(now) {
      var t = Math.min(1, (now - start) / DURATION);
      var eased = 1 - Math.pow(1 - t, 2);
      var percent = eased * 100;
      fill.style.width = percent.toFixed(2) + "%";

      if (t < 1) {
        var wrapW = btn.clientWidth;
        var wrapH = btn.clientHeight;
        var edgeX = (percent / 100) * wrapW;
        for (var i = 0; i < 3 + Math.floor(Math.random() * 3); i++) {
          spawnParticle(edgeX, wrapH);
        }
        requestAnimationFrame(step);
      } else {
        if (typeof onDone === "function") onDone();
      }
    }
    requestAnimationFrame(step);
  }

  function preflight() {
    return fetch("/api/preflight", { method: "POST" })
      .then(function (r) {
        if (!r.ok) {
          return { status: "error", message: "后端返回状态码 " + r.status };
        }
        return r.json();
      })
      .then(function (data) {
        if (data && typeof data === "object" && data.status === "ok") {
          return data;
        }
        return { status: "error", message: (data && data.message) || "自检异常" };
      })
      .catch(function (err) {
        return { status: "error", message: "无法连接后端：" + err.message };
      });
  }

  function showStatus(data) {
    if (data && data.status === "ok") {
      statusEl.textContent = "程序正常 · 就绪";
      statusEl.className = "start-status show";
    } else {
      statusEl.textContent = (data && data.message) || "检测异常";
      statusEl.className = "start-status show error";
    }
  }

  function goToMain() {
    window.location.href = "/main";
  }

  function checkBothAndNavigate() {
    if (!progressDone || !preflightDone) return;
    showStatus(preflightResult);
    setTimeout(function () {
      cleanupParticles();
      body.classList.add("fade-out");
      setTimeout(goToMain, 1000);
    }, 1500);
  }

  function autoLaunch() {
    try {
      col.classList.add("activated");

      requestAnimationFrame(function () {
        btn.textContent = "";
        fill = document.createElement("div");
        fill.className = "progress-fill";
        btn.appendChild(fill);
        btn.classList.add("morphing");
      });

      setTimeout(function () {
        runFakeProgress(function () {
          progressDone = true;
          checkBothAndNavigate();
        });
      }, MORPH_MS);

      preflight().then(function (data) {
        preflightResult = data;
        preflightDone = true;
        checkBothAndNavigate();
      });
    } catch (err) {
      statusEl.textContent = "启动异常：" + err.message;
      statusEl.className = "start-status show error";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoLaunch);
  } else {
    autoLaunch();
  }
})();
