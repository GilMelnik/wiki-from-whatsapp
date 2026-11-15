/* The accessibility menu of the header (partials/wiki-a11y.html) and keyboard fixes to Material.

   Settings: a text size (one of data-text-sizes, in percent) and on/off switches (the names in
   data-toggles). They are kept per browser under the data-storage-key key of localStorage and
   applied to <html> as soon as this script runs in <head>, so a saved choice never flashes:
   the text size as the custom property --wiki-a11y-scale, each switch that is on as the class
   wiki-a11y-<name>. wiki.css does the rest. Without storage the page works; the choice then
   lasts until the page changes.

   The menu is a disclosure: a real button with aria-expanded opens and closes it; Esc closes it
   and returns the focus to the button; a click outside it, or the focus leaving it, closes it.

   Material fixes: its menu button (a label of a hidden checkbox) opens with Enter and Space,
   says whether it is open and moves the focus into the drawer it opens; Esc closes the drawer.
   Its search dialog gets the name of its search field.
   A table wider than its box scrolls inside it: it takes the focus, so the arrow keys scroll it. */
(function () {
  "use strict";
  var script = document.currentScript;
  var key = script.dataset.storageKey;
  var sizes = JSON.parse(script.dataset.textSizes);
  var toggles = JSON.parse(script.dataset.toggles);
  var root = document.documentElement;
  var settings = load();

  function load() {
    try {
      var saved = JSON.parse(window.localStorage.getItem(key) || "{}");
      return saved && typeof saved === "object" ? saved : {};
    } catch (error) {
      return {}; // storage unavailable or unreadable: the defaults
    }
  }

  function save() {
    try {
      window.localStorage.setItem(key, JSON.stringify(settings));
    } catch (error) {
      /* storage unavailable: the choice lasts until the page changes */
    }
  }

  function each(selector, action) {
    document.querySelectorAll(selector).forEach(action);
  }

  function sizeIndex() {
    var index = sizes.indexOf(settings.size);
    return index < 0 ? 0 : index;
  }

  function focusableTables() {
    each(".md-typeset__scrollwrap", function (wrap) {
      if (wrap.scrollWidth > wrap.clientWidth) wrap.setAttribute("tabindex", "0");
      else wrap.removeAttribute("tabindex");
    });
  }

  // The settings on <html>, and the menu's buttons telling them.
  function apply() {
    var index = sizeIndex();
    if (index === 0) root.style.removeProperty("--wiki-a11y-scale");
    else root.style.setProperty("--wiki-a11y-scale", String(sizes[index] / sizes[0]));
    toggles.forEach(function (name) {
      root.classList.toggle("wiki-a11y-" + name, settings[name] === true);
    });
    each("[data-wiki-a11y-toggle]", function (button) {
      button.setAttribute("aria-pressed", settings[button.dataset.wikiA11yToggle] === true ? "true" : "false");
    });
    each(".wiki-a11y__size-value", function (output) {
      output.textContent = sizes[index] + "%";
    });
    each("[data-wiki-a11y-size]", function (button) {
      var stuck = Number(button.dataset.wikiA11ySize) > 0 ? index === sizes.length - 1 : index === 0;
      button.setAttribute("aria-disabled", stuck ? "true" : "false");
    });
    focusableTables();
  }

  function change(option) {
    if (option.dataset.wikiA11ySize !== undefined) {
      var step = Number(option.dataset.wikiA11ySize);
      settings.size = sizes[step === 0 ? 0 : Math.min(sizes.length - 1, Math.max(0, sizeIndex() + step))];
    } else if (option.dataset.wikiA11yToggle !== undefined) {
      var name = option.dataset.wikiA11yToggle;
      settings[name] = settings[name] !== true;
    } else {
      settings = {}; // "reset everything"
    }
    save();
    apply();
  }

  function seen(element) {
    return getComputedStyle(element).visibility === "visible" && element.getClientRects().length > 0;
  }

  // The drawer's markup comes after the header's: its first link that can be seen takes the focus.
  function focusDrawer() {
    var links = document.querySelectorAll(".md-sidebar--primary a[href], .md-sidebar--primary [tabindex='0']");
    for (var i = 0; i < links.length; i++) {
      if (seen(links[i])) {
        links[i].focus();
        return;
      }
    }
  }

  // Moving to another level of the open drawer hides the item that had the focus once the level
  // has slid in (0.25 s): the new level's first item takes it.
  function keepFocusInDrawer() {
    var drawer = document.getElementById("__drawer");
    var active = document.activeElement;
    if (drawer && drawer.checked && (active === document.body || (active.closest(".md-sidebar--primary") && !seen(active)))) {
      focusDrawer();
    }
  }

  function isOpen(menu) {
    return !menu.querySelector(".wiki-a11y__menu").hidden;
  }

  function setOpen(menu, open, focusButton) {
    var button = menu.querySelector(".wiki-a11y__button");
    menu.querySelector(".wiki-a11y__menu").hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    if (focusButton) button.focus();
  }

  root.classList.add("wiki-js");
  apply();

  document.addEventListener("click", function (event) {
    var menu = event.target.closest(".wiki-a11y");
    each(".wiki-a11y", function (other) {
      if (other !== menu && isOpen(other)) setOpen(other, false, false); // a click outside
    });
    if (!menu) return;
    if (event.target.closest(".wiki-a11y__button")) {
      setOpen(menu, !isOpen(menu), false);
    } else if (event.target.closest(".wiki-a11y__close")) {
      setOpen(menu, false, true);
    } else {
      var option = event.target.closest("button.wiki-a11y__option");
      if (option && option.getAttribute("aria-disabled") !== "true") change(option);
    }
  });

  // A key handled here goes no further: Material's own key handler (on window) would act on the
  // label that has just taken the focus.
  document.addEventListener("keydown", function (event) {
    if (!(event.target instanceof Element)) return;
    var menu = event.target.closest(".wiki-a11y");
    var drawer = document.getElementById("__drawer");
    var label = event.target.closest('label[role="button"][for]');
    if (event.key === "Escape" && menu && isOpen(menu)) {
      setOpen(menu, false, true);
    } else if (event.key === "Escape" && drawer && drawer.checked) {
      drawer.click();
      document.querySelector('label[role="button"][for="__drawer"]').focus();
    } else if ((event.key === "Enter" || event.key === " ") && label) {
      event.preventDefault();
      label.click();
      if (drawer && drawer.checked && label.htmlFor === drawer.id) focusDrawer();
    } else {
      return;
    }
    event.stopPropagation();
  });

  document.addEventListener("focusout", function (event) {
    var menu = event.target instanceof Element && event.target.closest(".wiki-a11y");
    if (menu && event.relatedTarget && !menu.contains(event.relatedTarget)) setOpen(menu, false, false);
  });

  window.addEventListener("resize", focusableTables);

  // Another tab changed the settings.
  window.addEventListener("storage", function (event) {
    if (event.key !== key) return;
    settings = load();
    apply();
  });

  // The navigation's labels open and close its levels. Material makes only some of them focusable;
  // in the drawer (Material's breakpoint) the section titles, the "back" title of each level and
  // the label that opens the page's table of contents need the focus too. All of them are buttons
  // that say whether their level is open.
  var drawerMode = window.matchMedia("screen and (max-width: 76.234375em)");

  function navLabels() {
    each(".md-sidebar--primary label[for]:not([for='__drawer'])", function (label) {
      if (label.dataset.wikiTabindex === undefined) label.dataset.wikiTabindex = label.getAttribute("tabindex") || "";
      if (label.dataset.wikiTabindex === "0" || drawerMode.matches) {
        label.setAttribute("tabindex", "0");
        label.setAttribute("role", "button");
      } else {
        label.removeAttribute("tabindex");
        label.removeAttribute("role");
      }
    });
  }

  drawerMode.addEventListener("change", navLabels);

  // The menu exists once the body is parsed; Material wraps the tables on its document$, which
  // fires after that and again after every page change when instant navigation is on.
  function onPage() {
    apply();
    navLabels();
    each(".md-sidebar--primary .md-nav__toggle", function (toggle) {
      toggle.addEventListener("change", function () {
        window.setTimeout(keepFocusInDrawer, 300);
      });
    });
    each('label[role="button"][for]', function (label) {
      var input = document.getElementById(label.htmlFor);
      if (!input) return;
      var sync = function () {
        label.setAttribute("aria-expanded", input.checked ? "true" : "false");
      };
      sync();
      input.addEventListener("change", sync);
    });
    // Material's search is a dialog without a name: it takes the search field's.
    each('.md-search[role="dialog"]:not([aria-label])', function (dialog) {
      var field = dialog.querySelector("input[aria-label]");
      if (field) dialog.setAttribute("aria-label", field.getAttribute("aria-label"));
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (window.document$) window.document$.subscribe(onPage);
    else onPage();
  });
})();
