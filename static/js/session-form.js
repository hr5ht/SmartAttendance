/* Teacher dashboard: dependent dropdowns, drag-and-drop photo upload with
   thumbnail previews, and timetable prefill. Vanilla, no dependencies. */
(function () {
  "use strict";

  var form = document.querySelector("[data-session-form]");
  if (!form) return;

  var departmentSelect = form.querySelector("#id_department");
  var yearSelect = form.querySelector("#id_year");
  var sectionSelect = form.querySelector("#id_section");
  var subjectSelect = form.querySelector("#id_subject");
  var periodSelect = form.querySelector("#id_period");
  var input = form.querySelector('input[type="file"]');
  var dropzone = form.querySelector("[data-dropzone]");
  var thumbs = form.querySelector("[data-thumbs]");
  var submit = form.querySelector("[data-submit]");
  var uploading = form.querySelector("[data-uploading]");

  var mapNode = document.getElementById("assignment-map");
  var assignments = mapNode ? JSON.parse(mapNode.textContent) : [];

  /* ---------------------------------------------------------------- selects
     Options are remembered up front so filtering can put them back. */
  function snapshot(select) {
    return Array.prototype.map.call(select.options, function (option) {
      return { value: option.value, text: option.text };
    });
  }

  var allYears = yearSelect ? snapshot(yearSelect) : [];
  var allSections = sectionSelect ? snapshot(sectionSelect) : [];
  var allSubjects = subjectSelect ? snapshot(subjectSelect) : [];

  function refill(select, options, allowed) {
    var previous = select.value;
    select.textContent = "";
    options.forEach(function (option) {
      // Keep the empty "Select …" placeholder whatever the filter says.
      if (option.value !== "" && allowed && allowed.indexOf(Number(option.value)) === -1) return;
      var el = document.createElement("option");
      el.value = option.value;
      el.textContent = option.text;
      select.appendChild(el);
    });
    select.value = previous;
    if (select.value !== previous) select.selectedIndex = 0;
  }

  function idsFor(key, filters) {
    var fields = Object.keys(filters);
    // Every step above this one must be chosen first. Without that a teacher
    // who takes two years sees both years' sections at once, and since a
    // section is labelled by its letter alone they read as duplicate A/B/C.
    if (fields.some(function (field) { return !filters[field]; })) return [];

    var seen = [];
    assignments.forEach(function (row) {
      var matches = fields.every(function (field) {
        return row[field] === filters[field];
      });
      if (matches && seen.indexOf(row[key]) === -1) seen.push(row[key]);
    });
    return seen;
  }

  /* Branch -> year -> section -> subject. Each step only offers what the
     teacher is actually assigned to further down the chain. */
  function syncSelects() {
    if (!assignments.length) return;
    var department = Number(departmentSelect.value) || null;

    refill(yearSelect, allYears, idsFor("year", { department: department }));
    var year = Number(yearSelect.value) || null;

    refill(sectionSelect, allSections, idsFor("section", {
      department: department, year: year
    }));
    var section = Number(sectionSelect.value) || null;

    refill(subjectSelect, allSubjects, idsFor("subject", {
      department: department, year: year, section: section
    }));
  }

  if (departmentSelect && yearSelect && sectionSelect && subjectSelect) {
    departmentSelect.addEventListener("change", syncSelects);
    yearSelect.addEventListener("change", syncSelects);
    sectionSelect.addEventListener("change", syncSelects);
    syncSelects();
  }

  /* ------------------------------------------------------------ file picker
     A DataTransfer holds the working list so a file can be removed. */
  var files = [];

  function syncInput() {
    var transfer = new DataTransfer();
    files.forEach(function (file) { transfer.items.add(file); });
    input.files = transfer.files;
  }

  function render() {
    thumbs.textContent = "";
    files.forEach(function (file, index) {
      var li = document.createElement("li");
      var img = document.createElement("img");
      img.alt = file.name;
      img.src = URL.createObjectURL(file);
      img.addEventListener("load", function () { URL.revokeObjectURL(img.src); });

      var name = document.createElement("span");
      name.className = "thumbs__name";
      name.textContent = file.name;

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "thumbs__remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Remove " + file.name);
      remove.addEventListener("click", function () {
        files.splice(index, 1);
        syncInput();
        render();
      });

      li.appendChild(img);
      li.appendChild(name);
      li.appendChild(remove);
      thumbs.appendChild(li);
    });
  }

  function addFiles(list) {
    Array.prototype.forEach.call(list, function (file) {
      if (!file.type || file.type.indexOf("image/") !== 0) {
        if (window.showToast) window.showToast(file.name + " is not an image.", "error");
        return;
      }
      var duplicate = files.some(function (existing) {
        return existing.name === file.name && existing.size === file.size;
      });
      if (!duplicate) files.push(file);
    });
    syncInput();
    render();
  }

  if (input && dropzone && thumbs) {
    input.addEventListener("change", function () {
      // The picker replaces the selection; merge it into what we already hold.
      var picked = Array.prototype.slice.call(input.files);
      if (picked.length) {
        var incoming = picked.filter(function (file) {
          return !files.some(function (existing) {
            return existing.name === file.name && existing.size === file.size;
          });
        });
        files = files.concat(incoming);
      }
      syncInput();
      render();
    });

    ["dragenter", "dragover"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.remove("is-dragging");
      });
    });
    dropzone.addEventListener("drop", function (event) {
      if (event.dataTransfer && event.dataTransfer.files) addFiles(event.dataTransfer.files);
    });
    dropzone.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
  }

  /* -------------------------------------------------------- submit feedback
     Re-encoding several photos server-side is not instant. */
  form.addEventListener("submit", function () {
    if (submit) {
      submit.disabled = true;
      submit.textContent = "Uploading…";
    }
    if (uploading) uploading.hidden = false;
  });

  /* ------------------------------------------------------ timetable prefill */
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-prefill]");
    if (!button) return;
    departmentSelect.value = button.getAttribute("data-department");
    syncSelects();
    yearSelect.value = button.getAttribute("data-year");
    syncSelects();
    sectionSelect.value = button.getAttribute("data-section");
    syncSelects();
    subjectSelect.value = button.getAttribute("data-subject");
    periodSelect.value = button.getAttribute("data-period");
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    if (window.showToast) window.showToast("Class details filled in. Add the photos.", "info");
  });

  /* --------------------------------------- "Add photos" on an existing draft */
  document.addEventListener("change", function (event) {
    var picker = event.target.closest("[data-autosubmit]");
    if (picker && picker.files.length) picker.form.submit();
  });
})();
