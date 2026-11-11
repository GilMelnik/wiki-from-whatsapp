/* References toggle: off by default, remembered per browser.
   Loaded in <head> (before the first paint) so the saved choice never flashes.
   It only flips the class "wiki-refs-on" on <html>; wiki.css shows or hides the
   reference marks and the references list. The localStorage key comes from the
   data-storage-key attribute of this script tag (config.json, site.refs_toggle). */
(function () {
  "use strict";
  var key = document.currentScript.dataset.storageKey;
  var root = document.documentElement;

  function saved() {
    try {
      return window.localStorage.getItem(key) === "1";
    } catch (error) {
      return false; // storage unavailable: the references stay off
    }
  }

  function remember(on) {
    try {
      window.localStorage.setItem(key, on ? "1" : "0");
    } catch (error) {
      /* storage unavailable: the choice lasts until the page changes */
    }
  }

  function apply(on) {
    root.classList.toggle("wiki-refs-on", on);
    document.querySelectorAll(".wiki-refs-toggle").forEach(function (button) {
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  root.classList.add("wiki-js");
  apply(saved());

  // One listener on the document, so it also serves buttons that arrive with instant navigation.
  document.addEventListener("click", function (event) {
    if (!event.target.closest(".wiki-refs-toggle")) return;
    var on = !root.classList.contains("wiki-refs-on");
    remember(on);
    apply(on);
  });

  // Another tab changed the choice.
  window.addEventListener("storage", function (event) {
    if (event.key === key) apply(event.newValue === "1");
  });

  // The buttons exist only once the body is parsed; Material's document$ fires after every
  // page change when instant navigation is on.
  document.addEventListener("DOMContentLoaded", function () {
    apply(root.classList.contains("wiki-refs-on"));
    if (window.document$) {
      window.document$.subscribe(function () {
        apply(root.classList.contains("wiki-refs-on"));
      });
    }
  });
})();
