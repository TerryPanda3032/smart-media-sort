(function () {
  "use strict";

  window.AppEvents = {
    emit: function (name, detail) {
      document.dispatchEvent(new CustomEvent("app:" + name, { detail: detail }));
    },
    on: function (name, handler) {
      document.addEventListener("app:" + name, handler);
    },
    off: function (name, handler) {
      document.removeEventListener("app:" + name, handler);
    },
  };
})();
