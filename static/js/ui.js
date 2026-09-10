/* Shared chrome: mobile nav, toast dismissal, confirmation modals.
   No framework, no jQuery — every file under static/js/ is plain ES2019. */
(function () {
  "use strict";

  /* -- mobile nav ------------------------------------------------------- */
  var navToggle = document.querySelector("[data-nav-toggle]");
  var navPanel = document.getElementById("site-nav");
  if (navToggle && navPanel) {
    navToggle.addEventListener("click", function () {
      var open = navPanel.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /* -- toasts ----------------------------------------------------------- */
  var region = document.querySelector("[data-flash-region]");

  function dismiss(toast) {
    if (toast && toast.parentNode) toast.parentNode.removeChild(toast);
  }

  document.addEventListener("click", function (event) {
    var closer = event.target.closest("[data-toast-close]");
    if (closer) dismiss(closer.closest(".toast"));
  });

  Array.prototype.forEach.call(document.querySelectorAll(".toast"), function (toast) {
    if (toast.classList.contains("toast--error")) return; // errors stay until dismissed
    window.setTimeout(function () { dismiss(toast); }, 6000);
  });

  /* Programmatic toasts, announced via the aria-live region. */
  window.showToast = function (text, kind) {
    if (!region) return null;
    var toast = document.createElement("div");
    toast.className = "toast toast--" + (kind || "info");
    var icon = { success: "✓", error: "!", warning: "▲" }[kind] || "i";
    toast.innerHTML =
      '<span class="toast__icon" aria-hidden="true"></span>' +
      '<div class="toast__body"></div>' +
      '<button class="toast__close" type="button" data-toast-close aria-label="Dismiss message">×</button>';
    toast.querySelector(".toast__icon").textContent = icon;
    toast.querySelector(".toast__body").textContent = text;
    region.appendChild(toast);
    if (kind !== "error") window.setTimeout(function () { dismiss(toast); }, 6000);
    return toast;
  };

  /* -- confirmation modal ------------------------------------------------
     Any element with data-confirm="<message>" opens a modal before its
     default action (form submit or link navigation) is allowed through. */
  var modal = null;
  var pendingAction = null;

  function buildModal() {
    var el = document.createElement("div");
    el.className = "modal";
    el.hidden = true;
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-labelledby", "confirm-modal-title");
    el.innerHTML =
      '<div class="modal__panel">' +
      '  <div class="modal__head"><h2 id="confirm-modal-title">Please confirm</h2></div>' +
      '  <div class="modal__body" data-confirm-body></div>' +
      '  <div class="modal__foot">' +
      '    <button class="btn btn--secondary" type="button" data-confirm-cancel>Cancel</button>' +
      '    <button class="btn btn--primary" type="button" data-confirm-ok>Confirm</button>' +
      '  </div>' +
      '</div>';
    document.body.appendChild(el);
    el.addEventListener("click", function (event) {
      if (event.target === el || event.target.closest("[data-confirm-cancel]")) close();
      if (event.target.closest("[data-confirm-ok]")) {
        var action = pendingAction;
        close();
        if (action) action();
      }
    });
    return el;
  }

  function close() {
    if (modal) modal.hidden = true;
    pendingAction = null;
    document.body.style.removeProperty("overflow");
  }

  window.confirmAction = function (message, okLabel, onConfirm) {
    if (!modal) modal = buildModal();
    modal.querySelector("[data-confirm-body]").textContent = message;
    modal.querySelector("[data-confirm-ok]").textContent = okLabel || "Confirm";
    pendingAction = onConfirm;
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    modal.querySelector("[data-confirm-ok]").focus();
  };

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-confirm]");
    if (!trigger) return;
    event.preventDefault();
    var form = trigger.form || trigger.closest("form");
    var label = trigger.getAttribute("data-confirm-label") || "Confirm";
    window.confirmAction(trigger.getAttribute("data-confirm"), label, function () {
      if (trigger.tagName === "A") {
        window.location.href = trigger.href;
      } else if (form) {
        if (trigger.name) {
          var hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = trigger.name;
          hidden.value = trigger.value;
          form.appendChild(hidden);
        }
        form.submit();
      }
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && modal && !modal.hidden) close();
  });

  /* -- mark the current nav link ---------------------------------------- */
  Array.prototype.forEach.call(document.querySelectorAll(".nav a"), function (link) {
    if (link.pathname === window.location.pathname ||
        (link.pathname !== "/" && window.location.pathname.indexOf(link.pathname) === 0)) {
      link.classList.add("is-active");
      link.setAttribute("aria-current", "page");
    }
  });
})();
