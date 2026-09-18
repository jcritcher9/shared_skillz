/**
 * Phase 2 progressive enhancement: isolated import cards submit on pick or
 * drop; CRM-dupe start drop only populates the file field.
 * Without this script, File options + native picker + Add this file /
 * Upload and continue still work.
 */
(function () {
  "use strict";

  var ACCEPTED = [".csv", ".xlsx"];

  function acceptedName(name) {
    var lower = String(name || "").toLowerCase();
    for (var i = 0; i < ACCEPTED.length; i++) {
      if (lower.endsWith(ACCEPTED[i])) return true;
    }
    return false;
  }

  function errorEl(root) {
    return root.querySelector("[data-file-drop-error]");
  }

  function showError(root, message) {
    var el = errorEl(root);
    if (!el) return;
    el.textContent = message;
    el.removeAttribute("hidden");
  }

  function clearError(root) {
    var el = errorEl(root);
    if (!el) return;
    el.textContent = "";
    el.setAttribute("hidden", "hidden");
  }

  function fileFromList(list) {
    if (!list || list.length === 0) {
      return { error: "Choose one CSV or Excel file." };
    }
    if (list.length > 1) {
      return { error: "Drop one CSV or Excel file at a time." };
    }
    var file = list[0];
    if (!acceptedName(file && file.name)) {
      return { error: "Use a CSV or Excel (.xlsx) file." };
    }
    return { file: file };
  }

  function assignToInput(input, file) {
    try {
      var dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      return true;
    } catch (err) {
      return false;
    }
  }

  function bindDragHighlight(zone) {
    function setOver(on) {
      if (on) zone.classList.add("is-dragover");
      else zone.classList.remove("is-dragover");
    }
    zone.addEventListener("dragenter", function (ev) {
      ev.preventDefault();
      setOver(true);
    });
    zone.addEventListener("dragover", function (ev) {
      ev.preventDefault();
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = "copy";
      setOver(true);
    });
    zone.addEventListener("dragleave", function (ev) {
      if (!zone.contains(ev.relatedTarget)) setOver(false);
    });
    zone.addEventListener("drop", function () {
      setOver(false);
    });
  }

  function enhanceIsolated(card) {
    var form = card.querySelector('form[data-file-drop-form="isolated"]');
    if (!form) return;
    var input = form.querySelector('input[type="file"][name="file"]');
    if (!input || input.disabled) return;
    var zone = card;
    var submitWrap = form.querySelector("[data-file-drop-submit]");
    if (submitWrap) submitWrap.setAttribute("hidden", "hidden");
    card.setAttribute("data-file-drop-enhanced", "1");

    bindDragHighlight(zone);

    function acceptAndSubmit(files) {
      var result = fileFromList(files);
      if (result.error) {
        showError(form, result.error);
        input.value = "";
        return;
      }
      clearError(form);
      if (files !== input.files) {
        if (!assignToInput(input, result.file)) {
          showError(form, "Could not attach that file. Use Choose file instead.");
          return;
        }
      }
      if (form.getAttribute("data-file-drop-submitting") === "1") return;
      form.setAttribute("data-file-drop-submitting", "1");
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    }

    input.addEventListener("change", function () {
      if (!input.files || input.files.length === 0) return;
      acceptAndSubmit(input.files);
    });

    zone.addEventListener("drop", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      acceptAndSubmit(ev.dataTransfer ? ev.dataTransfer.files : null);
    });
  }

  function enhancePopulate(zone) {
    var input = zone.querySelector('input[type="file"]');
    if (!input) return;
    // Bind even when the input starts disabled. CRM-dupe defaults to
    // acquire_all and the page script disables population_file before this
    // deferred enhancer runs. Switching to uploaded_population reenables
    // the input but does not re-run initialization.
    if (zone.getAttribute("data-file-drop-enhanced") === "1") return;
    zone.setAttribute("data-file-drop-enhanced", "1");
    bindDragHighlight(zone);

    function acceptOnly(files, fromDrop) {
      if (input.disabled) return;
      var result = fileFromList(files);
      if (result.error) {
        showError(zone, result.error);
        if (!fromDrop) input.value = "";
        return;
      }
      clearError(zone);
      if (fromDrop && !assignToInput(input, result.file)) {
        showError(zone, "Could not attach that file. Use Choose file instead.");
      }
    }

    input.addEventListener("change", function () {
      if (!input.files || input.files.length === 0) return;
      acceptOnly(input.files, false);
    });

    zone.addEventListener("drop", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      acceptOnly(ev.dataTransfer ? ev.dataTransfer.files : null, true);
    });
  }

  var isolated = document.querySelectorAll('[data-file-drop="isolated"]');
  for (var i = 0; i < isolated.length; i++) enhanceIsolated(isolated[i]);

  var populate = document.querySelectorAll('[data-file-drop="populate"]');
  for (var j = 0; j < populate.length; j++) enhancePopulate(populate[j]);

  var journeyForm = document.getElementById("crm-duplicate-journey-form");
  if (journeyForm) {
    journeyForm.addEventListener("dragover", function (ev) {
      ev.preventDefault();
    });
    journeyForm.addEventListener("drop", function (ev) {
      ev.preventDefault();
    });
  }
})();
