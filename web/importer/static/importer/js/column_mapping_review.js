/**
 * MAP-R4 progressive enhancement for column mapping review.
 * - Filters + column search (visibility only; row identity unchanged)
 * - One accessible field-picker dialog with local alias-aware search
 * - Stage Apply into native selects (no full-page reload)
 * - Focus restore after Apply even when filters would hide the row
 * - Listbox arrow-key navigation; Escape cancels
 * Without this script the server-rendered multi-row form still works.
 */
(function () {
  "use strict";

  var root = document.getElementById("mapping-review");
  if (!root) return;
  if (root.getAttribute("data-choices-blocked") === "1") {
    // Fail closed: keep no-JS surface only; do not enhance an empty inventory.
    var err = document.getElementById("mapping-choices-error");
    if (err && typeof err.focus === "function") {
      try {
        err.focus();
      } catch (e) {
        /* ignore */
      }
    }
    return;
  }

  var form = document.getElementById("mapping-review-form");
  var table = document.getElementById("mapping-table");
  var live = document.getElementById("mapping-live");
  var searchInput = document.getElementById("mapping-column-search");
  var dialog = document.getElementById("mapping-field-picker");
  var systemIgnore =
    (form && form.getAttribute("data-system-ignore")) || "system:ignore";

  var choices = [];
  try {
    var raw = document.getElementById("mapping-choices-data");
    if (raw && raw.textContent) {
      choices = JSON.parse(raw.textContent);
      if (!Array.isArray(choices)) choices = [];
    }
  } catch (err) {
    choices = [];
  }

  function announce(msg) {
    if (!live) return;
    live.textContent = "";
    window.setTimeout(function () {
      live.textContent = msg;
    }, 20);
  }

  function rowEls() {
    if (!table) return [];
    return Array.prototype.slice.call(
      table.querySelectorAll("tbody tr.mapping-row")
    );
  }

  function selectFor(ordinal) {
    return document.getElementById("choice_" + ordinal);
  }

  function isVisible(el) {
    if (!el) return false;
    if (el.hidden) return false;
    var tr = el.closest ? el.closest("tr") : null;
    if (tr && tr.hidden) return false;
    // offsetParent is null for display:none / some position:fixed cases;
    // also treat zero-size clipped nodes as not focusable targets.
    if (el.disabled) return false;
    var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (style && (style.visibility === "hidden" || style.display === "none")) {
      return false;
    }
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  function mapsLabelForChoice(choiceId) {
    if (!choiceId) return "Needs review";
    if (choiceId === systemIgnore) return "Ignored";
    for (var i = 0; i < choices.length; i++) {
      if (choices[i].mapping_choice_id === choiceId) {
        var scope = choices[i].object_scope || "";
        var label = choices[i].label || choiceId;
        if (scope) return scope + " · " + label;
        return label;
      }
    }
    return "Mapped";
  }

  function computeFilterKey(choiceId) {
    if (!choiceId) return "needs_review";
    if (choiceId === systemIgnore) return "ignored";
    return "mapped";
  }

  function statusForFilter(filterKey) {
    if (filterKey === "needs_review") return "Needs review";
    if (filterKey === "ignored") return "Ignore column";
    return "Changed by you";
  }

  function setActiveFilter(filter) {
    root.querySelectorAll(".mapping-filters [data-filter]").forEach(function (b) {
      var on = b.getAttribute("data-filter") === filter;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function activeFilter() {
    var activeBtn = root.querySelector(
      '.mapping-filters [data-filter][aria-pressed="true"]'
    );
    return (activeBtn && activeBtn.getAttribute("data-filter")) || "all";
  }

  function recountFilters() {
    var counts = { all: 0, needs_review: 0, mapped: 0, ignored: 0 };
    rowEls().forEach(function (tr) {
      var key = tr.getAttribute("data-filter") || "needs_review";
      counts.all += 1;
      if (counts[key] !== undefined) counts[key] += 1;
    });
    root.querySelectorAll(".mapping-filters [data-filter]").forEach(function (btn) {
      var f = btn.getAttribute("data-filter");
      var span = btn.querySelector(".tab-count");
      if (span && counts[f] !== undefined) span.textContent = String(counts[f]);
      if (f === "needs_review") {
        btn.classList.toggle("has-rows", counts.needs_review > 0);
      }
    });
    var confirmBtn = form
      ? form.querySelector('button[name="action"][value="confirm_mapping"]')
      : null;
    var hint = document.getElementById("mapping-confirm-hint");
    if (confirmBtn) {
      var ok = counts.needs_review === 0;
      confirmBtn.disabled = !ok;
      confirmBtn.setAttribute("aria-disabled", ok ? "false" : "true");
      confirmBtn.title = ok
        ? "Confirm mapping and continue"
        : "Resolve or ignore every column that needs review first";
    }
    if (hint) {
      hint.hidden = counts.needs_review === 0;
    }
    return counts;
  }

  function applyVisibility() {
    var filter = activeFilter();
    var q = (searchInput && searchInput.value ? searchInput.value : "")
      .trim()
      .toLowerCase();

    rowEls().forEach(function (tr) {
      var f = tr.getAttribute("data-filter") || "needs_review";
      var header = (
        tr.getAttribute("data-source-label") ||
        tr.getAttribute("data-source-header") ||
        ""
      ).toLowerCase();
      var maps = "";
      var mapsEl = tr.querySelector("[data-maps-label]");
      if (mapsEl) maps = (mapsEl.textContent || "").toLowerCase();
      var matchFilter = filter === "all" || f === filter;
      var matchSearch = !q || header.indexOf(q) !== -1 || maps.indexOf(q) !== -1;
      tr.hidden = !(matchFilter && matchSearch);
    });
  }

  /**
   * After Apply, ensure the row remains visible so focus can return to Change.
   * Switching to All is intentional — filters must not strand keyboard focus.
   */
  function ensureRowFocusable(tr) {
    if (!tr) return;
    var key = tr.getAttribute("data-filter") || "needs_review";
    var filter = activeFilter();
    var needsAll = filter !== "all" && filter !== key;
    var q = (searchInput && searchInput.value ? searchInput.value : "").trim();
    if (needsAll || q) {
      if (needsAll) setActiveFilter("all");
      if (q && searchInput) searchInput.value = "";
      applyVisibility();
      if (needsAll) {
        announce("Showing all columns so the updated row stays reachable");
      }
    } else {
      applyVisibility();
    }
    tr.hidden = false;
  }

  function restoreFocusToRowAction(ordinal) {
    var tr = document.getElementById("mapping-row-" + ordinal);
    if (tr) ensureRowFocusable(tr);
    var btn =
      (tr && tr.querySelector("[data-open-picker]")) ||
      document.querySelector(
        '[data-open-picker][data-ordinal="' + ordinal + '"]'
      );
    if (btn && isVisible(btn) && typeof btn.focus === "function") {
      btn.focus();
      return true;
    }
    // Fallback: first visible action, then confirm / save.
    var first = root.querySelector(
      "tbody tr.mapping-row:not([hidden]) [data-open-picker]"
    );
    if (first && typeof first.focus === "function") {
      first.focus();
      return true;
    }
    var save =
      form && form.querySelector('button[name="action"][value="save_draft"]');
    if (save && typeof save.focus === "function") {
      save.focus();
      return true;
    }
    return false;
  }

  // Enable enhanced controls
  root.classList.add("map-r4-enhanced");
  if (searchInput) {
    searchInput.disabled = false;
    searchInput.addEventListener("input", applyVisibility);
  }

  // Keep native selects in the form for submit, but out of tab order.
  rowEls().forEach(function (tr) {
    var sel = selectFor(tr.getAttribute("data-ordinal"));
    if (sel) {
      sel.setAttribute("tabindex", "-1");
      sel.setAttribute("aria-hidden", "true");
    }
    var bulk = tr.querySelector(".mapping-bulk-checkbox");
    if (bulk) {
      // Bulk remains available for keyboard; leave tabbable.
    }
  });

  root.querySelectorAll(".mapping-filters [data-filter]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setActiveFilter(btn.getAttribute("data-filter") || "all");
      applyVisibility();
      announce("Showing " + (btn.textContent || "").trim());
    });
  });

  // Reveal Change/Review buttons.
  rowEls().forEach(function (tr) {
    var btn = tr.querySelector("[data-open-picker]");
    if (btn) {
      btn.hidden = false;
      btn.removeAttribute("hidden");
    }
  });

  function syncRowFromSelect(tr) {
    var ordinal = tr.getAttribute("data-ordinal");
    var sel = selectFor(ordinal);
    var choiceId = sel ? String(sel.value || "") : "";
    tr.setAttribute("data-choice-id", choiceId);
    var filterKey = computeFilterKey(choiceId);
    tr.setAttribute("data-filter", filterKey);
    tr.setAttribute(
      "data-disposition",
      filterKey === "needs_review"
        ? "unresolved"
        : filterKey === "ignored"
          ? "ignored"
          : "mapped"
    );
    tr.className = "mapping-row mapping-row-" + filterKey;
    var mapsEl = tr.querySelector("[data-maps-label]");
    if (mapsEl) mapsEl.textContent = mapsLabelForChoice(choiceId);
    var statusEl = tr.querySelector(".mapping-status-pill");
    if (statusEl) {
      statusEl.textContent = statusForFilter(filterKey);
      statusEl.className = "mapping-status-pill status-" + filterKey;
    }
    var actionBtn = tr.querySelector("[data-open-picker]");
    if (actionBtn) {
      actionBtn.textContent =
        filterKey === "needs_review" && !choiceId ? "Review" : "Change";
    }
    var bulkCell = tr.querySelector(".col-bulk");
    if (bulkCell) {
      var existing = bulkCell.querySelector(".mapping-bulk-checkbox");
      if (filterKey === "needs_review") {
        if (!existing) {
          bulkCell.innerHTML =
            '<label class="bulk-check"><input type="checkbox" name="bulk_ordinal" value="' +
            ordinal +
            '" class="mapping-bulk-checkbox"><span class="visually-hidden">Select for bulk ignore</span></label>';
        }
      } else if (existing) {
        bulkCell.innerHTML = '<span class="muted" aria-hidden="true">·</span>';
      }
    }
  }

  rowEls().forEach(function (tr) {
    var ordinal = tr.getAttribute("data-ordinal");
    var sel = selectFor(ordinal);
    if (sel) {
      sel.addEventListener("change", function () {
        syncRowFromSelect(tr);
        recountFilters();
        applyVisibility();
      });
    }
  });

  // --- Picker dialog ---
  var activeOrdinal = null;
  var returnFocus = null;
  var selectedChoiceId = null;
  var activeOptionIndex = -1;
  var pickerList = document.getElementById("mapping-picker-list");
  var pickerSearch = document.getElementById("mapping-picker-search");
  var pickerSource = document.getElementById("mapping-picker-source");
  var pickerExamples = document.getElementById("mapping-picker-examples");
  var fieldPanel = document.getElementById("mapping-picker-field-panel");
  var ignorePanel = document.getElementById("mapping-picker-ignore-panel");
  var pickerForm = document.getElementById("mapping-picker-form");

  function fieldChoices() {
    return choices.filter(function (c) {
      return !c.is_ignore && c.mapping_choice_id !== systemIgnore;
    });
  }

  function ignoreChoiceId() {
    for (var i = 0; i < choices.length; i++) {
      if (choices[i].is_ignore || choices[i].mapping_choice_id === systemIgnore) {
        return choices[i].mapping_choice_id;
      }
    }
    return systemIgnore;
  }

  function choiceSearchHaystack(c) {
    var parts = [
      c.label || "",
      c.object_scope || "",
      c.mapping_choice_id || "",
    ];
    var aliases = c.search_aliases;
    if (Array.isArray(aliases)) {
      for (var i = 0; i < aliases.length; i++) {
        parts.push(String(aliases[i] || ""));
      }
    }
    return parts.join(" ").toLowerCase();
  }

  function groupFields(list) {
    var groups = {};
    list.forEach(function (c) {
      var scope = c.object_scope || "Fields";
      if (!groups[scope]) groups[scope] = [];
      groups[scope].push(c);
    });
    return Object.keys(groups)
      .sort(function (a, b) {
        if (a === "Fields") return 1;
        if (b === "Fields") return -1;
        return a.localeCompare(b);
      })
      .map(function (scope) {
        return {
          scope: scope,
          items: groups[scope].slice().sort(function (a, b) {
            return (a.label || "").localeCompare(b.label || "");
          }),
        };
      });
  }

  function optionButtons() {
    if (!pickerList) return [];
    return Array.prototype.slice.call(
      pickerList.querySelectorAll(".mapping-picker-option")
    );
  }

  function setActiveOption(index) {
    var opts = optionButtons();
    if (!opts.length) {
      activeOptionIndex = -1;
      return;
    }
    if (index < 0) index = 0;
    if (index >= opts.length) index = opts.length - 1;
    activeOptionIndex = index;
    opts.forEach(function (btn, i) {
      var on = i === index;
      btn.classList.toggle("is-active", on);
      if (on) {
        btn.setAttribute("tabindex", "0");
        try {
          btn.focus();
        } catch (e) {
          /* ignore */
        }
      } else {
        btn.setAttribute("tabindex", "-1");
      }
    });
  }

  function renderPickerList(query) {
    if (!pickerList) return;
    var q = (query || "").trim().toLowerCase();
    var list = fieldChoices().filter(function (c) {
      if (!q) return true;
      return choiceSearchHaystack(c).indexOf(q) !== -1;
    });
    pickerList.innerHTML = "";
    activeOptionIndex = -1;
    var groups = groupFields(list);
    if (!groups.length) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.setAttribute("role", "status");
      empty.textContent = "No fields match your search.";
      pickerList.appendChild(empty);
      return;
    }
    groups.forEach(function (g) {
      var heading = document.createElement("div");
      heading.className = "mapping-picker-group-title";
      heading.setAttribute("role", "presentation");
      heading.textContent = g.scope;
      pickerList.appendChild(heading);
      g.items.forEach(function (c) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "mapping-picker-option";
        btn.setAttribute("role", "option");
        btn.setAttribute("data-choice-id", c.mapping_choice_id);
        btn.setAttribute("tabindex", "-1");
        var selected = selectedChoiceId === c.mapping_choice_id;
        btn.setAttribute("aria-selected", selected ? "true" : "false");
        if (selected) btn.classList.add("is-selected");
        var labelSpan = document.createElement("span");
        labelSpan.className = "picker-option-label";
        labelSpan.textContent = c.label;
        btn.appendChild(labelSpan);
        if (selected) {
          var check = document.createElement("span");
          check.className = "picker-check";
          check.setAttribute("aria-hidden", "true");
          check.textContent = "✓";
          btn.appendChild(check);
        }
        btn.addEventListener("click", function () {
          selectedChoiceId = c.mapping_choice_id;
          var modeField = pickerForm.querySelector(
            'input[name="picker_mode"][value="field"]'
          );
          if (modeField) modeField.checked = true;
          setMode("field");
          renderPickerList(pickerSearch ? pickerSearch.value : "");
          // Keep selection highlighted after re-render.
          var opts = optionButtons();
          for (var i = 0; i < opts.length; i++) {
            if (opts[i].getAttribute("data-choice-id") === selectedChoiceId) {
              setActiveOption(i);
              break;
            }
          }
        });
        pickerList.appendChild(btn);
      });
    });
    // Activate currently selected option when present.
    var opts = optionButtons();
    var idx = -1;
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].getAttribute("data-choice-id") === selectedChoiceId) {
        idx = i;
        break;
      }
    }
    if (idx >= 0) {
      opts[idx].setAttribute("tabindex", "0");
      activeOptionIndex = idx;
    } else if (opts.length) {
      opts[0].setAttribute("tabindex", "0");
      activeOptionIndex = 0;
    }
  }

  function setMode(mode) {
    if (fieldPanel) fieldPanel.hidden = mode === "ignore";
    if (ignorePanel) ignorePanel.hidden = mode !== "ignore";
  }

  function openPicker(tr, trigger) {
    if (!dialog || typeof dialog.showModal !== "function") return;
    activeOrdinal = tr.getAttribute("data-ordinal");
    returnFocus = trigger || null;
    var sel = selectFor(activeOrdinal);
    selectedChoiceId = sel ? String(sel.value || "") : "";
    if (!selectedChoiceId) {
      selectedChoiceId = tr.getAttribute("data-suggested-id") || "";
    }
    var isIgnore =
      selectedChoiceId === systemIgnore ||
      selectedChoiceId === ignoreChoiceId();
    var modeIgnore = pickerForm.querySelector(
      'input[name="picker_mode"][value="ignore"]'
    );
    var modeField = pickerForm.querySelector(
      'input[name="picker_mode"][value="field"]'
    );
    if (isIgnore && modeIgnore) {
      modeIgnore.checked = true;
      setMode("ignore");
    } else if (modeField) {
      modeField.checked = true;
      setMode("field");
    }
    if (pickerSource) {
      pickerSource.textContent =
        "Source column: " +
        (tr.getAttribute("data-source-label") ||
          tr.getAttribute("data-source-header") ||
          "");
    }
    if (pickerExamples) {
      var samples = Array.prototype.slice
        .call(tr.querySelectorAll(".sample-text"))
        .map(function (el) {
          return (el.textContent || "").trim();
        })
        .filter(function (t) {
          return t && t !== "—";
        });
      pickerExamples.textContent = samples.length
        ? "Examples: " + samples.join(" · ")
        : "";
    }
    if (pickerSearch) pickerSearch.value = "";
    renderPickerList("");
    dialog.showModal();
    window.setTimeout(function () {
      if (pickerSearch && !isIgnore) pickerSearch.focus();
      else if (modeIgnore && isIgnore) modeIgnore.focus();
    }, 10);
    announce("Edit mapping dialog opened");
  }

  function closePicker(restore) {
    var ordinal = activeOrdinal;
    if (dialog && dialog.open) dialog.close();
    if (restore && ordinal != null) {
      // Always restore to the row Change control when possible (filter-safe).
      window.setTimeout(function () {
        restoreFocusToRowAction(ordinal);
        returnFocus = null;
        activeOrdinal = null;
      }, 0);
      return;
    }
    if (restore && returnFocus && isVisible(returnFocus)) {
      returnFocus.focus();
    }
    returnFocus = null;
    activeOrdinal = null;
  }

  root.querySelectorAll("[data-open-picker]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var tr = btn.closest("tr.mapping-row");
      if (tr) openPicker(tr, btn);
    });
  });

  if (pickerForm) {
    pickerForm.querySelectorAll('input[name="picker_mode"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        setMode(radio.value);
        if (radio.value === "field" && pickerSearch) pickerSearch.focus();
      });
    });
  }

  if (pickerSearch) {
    pickerSearch.addEventListener("input", function () {
      renderPickerList(pickerSearch.value);
    });
    pickerSearch.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        setActiveOption(0);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        var opts = optionButtons();
        if (opts.length) setActiveOption(opts.length - 1);
      } else if (ev.key === "Enter") {
        // Prefer applying the active/selected field without leaving dialog empty.
        var opts2 = optionButtons();
        if (opts2.length && activeOptionIndex >= 0 && opts2[activeOptionIndex]) {
          ev.preventDefault();
          opts2[activeOptionIndex].click();
        }
      }
    });
  }

  if (pickerList) {
    pickerList.addEventListener("keydown", function (ev) {
      var opts = optionButtons();
      if (!opts.length) return;
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        setActiveOption(activeOptionIndex < 0 ? 0 : activeOptionIndex + 1);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        setActiveOption(
          activeOptionIndex < 0 ? opts.length - 1 : activeOptionIndex - 1
        );
      } else if (ev.key === "Home") {
        ev.preventDefault();
        setActiveOption(0);
      } else if (ev.key === "End") {
        ev.preventDefault();
        setActiveOption(opts.length - 1);
      } else if (ev.key === "Enter" || ev.key === " ") {
        if (activeOptionIndex >= 0 && opts[activeOptionIndex]) {
          ev.preventDefault();
          opts[activeOptionIndex].click();
        }
      }
    });
  }

  if (pickerForm) {
    pickerForm.addEventListener("submit", function (ev) {
      var submitter = ev.submitter;
      var value = submitter ? submitter.value : "cancel";
      ev.preventDefault();
      if (value === "apply" && activeOrdinal != null) {
        var modeEl = pickerForm.querySelector(
          'input[name="picker_mode"]:checked'
        );
        var mode = modeEl ? modeEl.value : "field";
        var choiceId =
          mode === "ignore" ? ignoreChoiceId() : selectedChoiceId || "";
        if (mode === "field" && !choiceId) {
          announce("Choose a target field or Ignore column");
          return;
        }
        var ordinal = activeOrdinal;
        var sel = selectFor(ordinal);
        if (sel) {
          sel.value = choiceId;
          if (sel.value !== choiceId) {
            var opt = document.createElement("option");
            opt.value = choiceId;
            opt.textContent = mapsLabelForChoice(choiceId);
            sel.appendChild(opt);
            sel.value = choiceId;
          }
        }
        var tr = document.getElementById("mapping-row-" + ordinal);
        if (tr) {
          syncRowFromSelect(tr);
          recountFilters();
          // Make row visible under current filters BEFORE focus restore.
          ensureRowFocusable(tr);
        }
        announce("Mapping updated for column");
        closePicker(true);
        return;
      }
      closePicker(true);
      announce("Edit cancelled");
    });
  }

  if (dialog) {
    dialog.addEventListener("cancel", function () {
      var ordinal = activeOrdinal;
      window.setTimeout(function () {
        if (ordinal != null) restoreFocusToRowAction(ordinal);
        else if (returnFocus && isVisible(returnFocus)) returnFocus.focus();
        returnFocus = null;
        activeOrdinal = null;
      }, 0);
    });
  }

  // Focus trap: keep Tab inside open dialog
  if (dialog) {
    dialog.addEventListener("keydown", function (ev) {
      if (ev.key !== "Tab" || !dialog.open) return;
      var focusables = dialog.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      var list = Array.prototype.filter.call(focusables, function (el) {
        if (el.disabled || el.hidden) return false;
        if (el.closest && el.closest("[hidden]")) return false;
        return true;
      });
      if (!list.length) return;
      var first = list[0];
      var last = list[list.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    });
  }

  // Hash focus for no-JS validation anchors (also helps after full page loads).
  if (location.hash) {
    var target = document.querySelector(location.hash);
    if (target) {
      if (typeof target.focus === "function") {
        try {
          target.focus();
        } catch (e) {
          /* ignore */
        }
      }
      if (target.scrollIntoView) {
        target.scrollIntoView({ block: "center" });
      }
    }
  }

  recountFilters();
  applyVisibility();
})();
