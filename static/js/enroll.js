/* Face enrollment wizard.
   Preferred path: getUserMedia -> draw the frame to a canvas -> POST the JPEG.
   Fallback path: <input type="file" capture="user">, used when getUserMedia is
   missing or permission is denied. Both paths post to the same endpoint. */
(function () {
  "use strict";

  var root = document.querySelector("[data-capture-card]");
  if (!root) return;

  var csrf = JSON.parse(document.getElementById("csrf-token").textContent);
  var endpoint = JSON.parse(document.getElementById("capture-url").textContent);

  var stage = root.querySelector("[data-stage]");
  var video = root.querySelector("[data-video]");
  var preview = root.querySelector("[data-preview]");
  var canvas = root.querySelector("[data-canvas]");
  var fallback = root.querySelector("[data-fallback]");
  var fallbackNote = root.querySelector("[data-fallback-note]");
  var fallbackInput = root.querySelector("[data-fallback-input]");
  var actions = root.querySelector("[data-actions]");
  var shootBtn = root.querySelector("[data-shoot]");
  var reviewBox = root.querySelector("[data-review]");
  var retakeBtn = root.querySelector("[data-retake]");
  var useBtn = root.querySelector("[data-use]");
  var busy = root.querySelector("[data-busy]");
  var message = root.querySelector("[data-message]");
  var stepTitle = root.querySelector("[data-step-title]");
  var stepHint = root.querySelector("[data-step-hint]");
  var doneCard = document.querySelector("[data-done-card]");
  var progressText = document.querySelector("[data-progress-text]");

  var stream = null;
  var pendingBlob = null;
  var currentStep = firstPendingStep();

  function firstPendingStep() {
    var row = document.querySelector(".steplist__item:not(.is-done)");
    return row ? Number(row.getAttribute("data-step-row")) : null;
  }

  function say(text, kind) {
    message.textContent = text || "";
    message.className = "capture-msg" + (text ? " capture-msg--" + (kind || "info") : "");
  }

  function setBusy(state) {
    busy.hidden = !state;
    [shootBtn, retakeBtn, useBtn, fallbackInput].forEach(function (el) {
      if (el) el.disabled = state;
    });
  }

  /* ----------------------------------------------------------- camera setup */
  /* The file-input path is shown from the start and only taken away once the
     live camera is actually running, so a hung or blocked getUserMedia can
     never leave the student with nothing to press. */
  function useFallback(reason) {
    if (stage) stage.hidden = true;
    if (actions) actions.hidden = true;
    fallback.hidden = false;
    if (reason) {
      fallbackNote.textContent = reason;
      fallbackNote.classList.remove("fallback__note--neutral");
    }
  }

  function startCamera() {
    // If the permission prompt is never answered the promise simply never
    // settles; don't leave the note saying "opening" forever.
    var settled = false;
    var giveUp = window.setTimeout(function () {
      if (!settled) {
        useFallback(
          "The camera is taking too long to start. Use the button below — it " +
          "opens your normal camera app and works just as well."
        );
      }
    }, 10000);

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      settled = true;
      window.clearTimeout(giveUp);
      useFallback(
        "This browser can't open the camera directly. Use the button below — " +
        "it opens your normal camera app."
      );
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 960 } } })
      .then(function (mediaStream) {
        settled = true;
        window.clearTimeout(giveUp);
        stream = mediaStream;
        video.srcObject = mediaStream;
        stage.hidden = false;
        actions.hidden = false;
        fallback.hidden = true;
      })
      .catch(function (error) {
        settled = true;
        window.clearTimeout(giveUp);
        // NotAllowedError is the user denying permission; everything else is a
        // device problem. Either way the file-input path still works.
        var denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
        useFallback(
          denied
            ? "Camera permission was denied. You can still finish using the button " +
              "below, which opens your normal camera app — or allow camera access " +
              "in your browser settings and reload."
            : "The camera couldn't be started (" + ((error && error.name) || "unknown") +
              "). Use the button below instead."
        );
      });
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      stream = null;
    }
  }

  /* -------------------------------------------------------------- capturing */
  function shoot() {
    if (!video.videoWidth) {
      say("The camera isn't ready yet — give it a second and try again.", "error");
      return;
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    var ctx = canvas.getContext("2d");
    // The preview is mirrored for comfort; the stored frame must not be, or the
    // left/right turn steps would be recorded the wrong way round.
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(function (blob) {
      if (!blob) {
        say("Couldn't capture the frame. Try again.", "error");
        return;
      }
      pendingBlob = blob;
      preview.src = URL.createObjectURL(blob);
      preview.hidden = false;
      stage.classList.add("is-shot");
      shootBtn.hidden = true;
      reviewBox.hidden = false;
      say("Happy with this one?", "info");
    }, "image/jpeg", 0.92);
  }

  function resetShot() {
    pendingBlob = null;
    if (preview.src) URL.revokeObjectURL(preview.src);
    preview.hidden = true;
    stage.classList.remove("is-shot");
    reviewBox.hidden = true;
    shootBtn.hidden = false;
    if (fallbackInput) fallbackInput.value = "";
  }

  function retake() {
    resetShot();
    say("");
  }

  /* ---------------------------------------------------------------- sending */
  function send(blob) {
    if (currentStep === null) return;
    var data = new FormData();
    data.append("step", currentStep);
    data.append("image", blob, "capture.jpg");

    setBusy(true);
    say("");

    fetch(endpoint, {
      method: "POST",
      body: data,
      headers: { "X-CSRFToken": csrf },
      credentials: "same-origin"
    })
      .then(function (response) { return response.json().catch(function () {
        throw new Error("The server sent an unreadable reply.");
      }); })
      .then(handleResult)
      .catch(function () {
        setBusy(false);
        say(
          "Couldn't reach the server. Check your connection and try the photo again.",
          "error"
        );
      });
  }

  function handleResult(data) {
    setBusy(false);

    if (!data.ok) {
      // A rejected capture keeps the same step. Clear the shot but keep the
      // reason on screen — it is the only thing telling them what to fix.
      resetShot();
      say(data.message, "error");
      return;
    }

    syncCompleted(data.completed_steps);
    updateProgress(data.progress);

    if (data.complete) {
      stopCamera();
      root.hidden = true;
      if (doneCard) doneCard.hidden = false;
      say("");
      if (window.showToast) window.showToast("Face setup complete.", "success");
      return;
    }

    currentStep = data.next_step ? data.next_step.index : firstPendingStep();
    if (data.next_step) {
      stepTitle.textContent = data.next_step.title;
      stepHint.textContent = data.next_step.hint;
    }
    resetShot();
    say(
      data.message + (data.next_step ? " Next: " + data.next_step.title.toLowerCase() : ""),
      "success"
    );
  }

  /* Trust the server's list of finished steps rather than tracking it here. */
  function syncCompleted(indexes) {
    if (!indexes || !indexes.length) {
      markDone(currentStep);
      return;
    }
    indexes.forEach(markDone);
  }

  function markDone(index) {
    var row = document.querySelector('[data-step-row="' + index + '"]');
    if (row) {
      row.classList.add("is-done");
      var pill = row.querySelector("[data-row-done]");
      if (pill) pill.hidden = false;
    }
    var tick = document.querySelector('[data-tick="' + index + '"]');
    if (tick) tick.classList.add("is-done");
  }

  function updateProgress(p) {
    if (p && progressText) progressText.textContent = p.done + " of " + p.total + " done";
  }

  /* ------------------------------------------------------------------ wiring */
  if (shootBtn) shootBtn.addEventListener("click", shoot);
  if (retakeBtn) retakeBtn.addEventListener("click", retake);
  if (useBtn) useBtn.addEventListener("click", function () {
    if (pendingBlob) send(pendingBlob);
  });

  if (fallbackInput) {
    fallbackInput.addEventListener("change", function () {
      var file = fallbackInput.files && fallbackInput.files[0];
      if (file) send(file);
    });
  }

  window.addEventListener("pagehide", stopCamera);

  if (currentStep !== null) startCamera();
})();
