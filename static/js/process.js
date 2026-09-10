/* Session processing: fire the (blocking) pipeline request, and poll a light
   status endpoint alongside it so the spinner can name the photo being worked
   on. Nothing here is required for correctness — the POST does the work — this
   is purely so the teacher isn't staring at an unlabelled spinner. */
(function () {
  "use strict";

  var card = document.querySelector("[data-process-card]");
  var urlsNode = document.getElementById("process-urls");
  if (!card || !urlsNode) return;

  var urls = JSON.parse(urlsNode.textContent);
  var csrf = JSON.parse(document.getElementById("csrf-token").textContent);
  var text = card.querySelector("[data-process-text]");
  var problemCard = document.querySelector("[data-problem-card]");
  var problemMessage = document.querySelector("[data-problem-message]");
  var results = document.querySelector("[data-results]");

  var polling = null;

  function say(message) {
    if (text) text.textContent = message;
  }

  function startPolling() {
    polling = window.setInterval(function () {
      fetch(urls.status, { credentials: "same-origin" })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (!data.total) return;
          if (data.finished) {
            say("Finishing up…");
          } else {
            say("Processing image " + data.current + " of " + data.total + "…");
          }
        })
        .catch(function () { /* the POST is the source of truth; ignore */ });
    }, 700);
  }

  function stopPolling() {
    if (polling) window.clearInterval(polling);
    polling = null;
  }

  function showProblem(message) {
    card.hidden = true;
    if (problemMessage) problemMessage.textContent = message;
    if (problemCard) problemCard.hidden = false;
  }

  function run() {
    if (problemCard) problemCard.hidden = true;
    if (results) results.hidden = true;
    card.hidden = false;
    say("Starting…");
    startPolling();

    fetch(urls.process, {
      method: "POST",
      headers: { "X-CSRFToken": csrf },
      credentials: "same-origin"
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        stopPolling();
        if (!data.ok) {
          showProblem(data.message);
          return;
        }
        say("Done. Reloading the results…");
        if (window.showToast) window.showToast(data.message, "success");
        // The results are rendered server-side; simplest correct thing is to
        // re-request the page now that the records exist.
        window.location.reload();
      })
      .catch(function () {
        stopPolling();
        showProblem(
          "Lost contact with the server while processing. Check your connection " +
          "and use “Try again” — the photos are still saved."
        );
      });
  }

  // Auto-run on arrival when there are unprocessed photos.
  if (!card.hidden) run();

  Array.prototype.forEach.call(
    document.querySelectorAll("[data-reprocess]"),
    function (button) { button.addEventListener("click", run); }
  );
})();
