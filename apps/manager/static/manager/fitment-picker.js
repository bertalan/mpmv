// FIT.3: the cascading node picker of the Fits page. The native select of every blank row stays
// in the page as the submitted field (and the no-script fallback); this adds one select per level
// (Make, Model, Variant), each filled from the JSON endpoint, and writes the last choice into it.
// A row that already has an item keeps its native select. If the endpoint fails, the native
// select comes back. Above the cascade, a search box (ARIA combobox with a listbox) finds a node by
// typed text through `?q=` (debounced, arrows/Enter/Escape) and sets the native select to it; the
// cascade is left as it is. Strings and the URL come from data attributes; no inline script (CSP).
(function () {
  var root = document.querySelector("[data-fitment-picker-url]");
  if (!root || !window.fetch) return;
  var url = root.getAttribute("data-fitment-picker-url");
  var firstText = root.getAttribute("data-fitment-picker-first") || "{label}";
  var countText = root.getAttribute("data-fitment-picker-count") || "{n}";
  var moreText = root.getAttribute("data-fitment-picker-more") || "";
  var searchLabel = root.getAttribute("data-fitment-picker-search") || "";
  var noneText = root.getAttribute("data-fitment-picker-none") || "";
  var foundText = root.getAttribute("data-fitment-picker-found") || "{n}";
  var chosenText = root.getAttribute("data-fitment-picker-chosen") || "{name}";
  var status = document.getElementById("fitment-picker-status");
  function announce(text) {
    if (status) status.textContent = text;
  }

  function load(parent) {
    var query = parent ? "?parent=" + encodeURIComponent(parent) : "";
    return fetch(url + query, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then(function (response) {
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    });
  }

  function enhance(native) {
    var serial = 0;
    var box = document.createElement("div");
    box.className = "fitment-picker";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", native.getAttribute("aria-label") || "");
    native.after(box);
    native.hidden = true;

    function giveUp() {
      box.remove();
      native.hidden = false;
    }

    function trim(depth) {
      while (box.children.length > depth) box.lastChild.remove();
    }

    function choose(depth, value) {
      trim(depth + 1);
      native.value = value || (depth > 0 ? box.children[depth - 1].value : "");
      native.dispatchEvent(new Event("change", { bubbles: true }));
      if (value) show(value, depth + 1);
    }

    function show(parent, depth) {
      var mine = ++serial;
      load(parent).then(function (data) {
        if (mine !== serial && depth > 0) return;
        if (!data.results.length) return;
        trim(depth);
        var select = document.createElement("select");
        select.className = native.className;
        select.setAttribute("aria-label", data.label);
        var blank = document.createElement("option");
        blank.value = "";
        blank.textContent = firstText.replace("{label}", data.label);
        select.appendChild(blank);
        data.results.forEach(function (item) {
          var option = document.createElement("option");
          option.value = item.id;
          option.textContent = item.name;
          select.appendChild(option);
        });
        select.addEventListener("change", function () {
          choose(depth, select.value);
        });
        box.appendChild(select);
        announce(
          data.label + ": " + countText.replace("{n}", data.results.length) +
            (data.more ? ". " + moreText : "")
        );
      }).catch(function () {
        if (depth === 0) giveUp();
      });
    }

    show("", 0);
    addSearch(native, box);
  }

  var searches = 0;

  function addSearch(native, box) {
    var id = "fitment-search-" + searches++;
    var wrap = document.createElement("div");
    wrap.className = "fitment-picker__search";
    var input = document.createElement("input");
    input.type = "search";
    input.className = native.className;
    input.id = id;
    input.autocomplete = "off";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-label", searchLabel);
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", id + "-list");
    var list = document.createElement("ul");
    list.id = id + "-list";
    list.className = "fitment-picker__results";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", searchLabel);
    list.hidden = true;
    wrap.appendChild(input);
    wrap.appendChild(list);
    box.before(wrap);
    var timer = null;
    var ticket = 0;
    var active = -1;

    function options() {
      return list.querySelectorAll("[role=option]");
    }
    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }
    function mark(index) {
      var items = options();
      if (!items.length) return;
      active = (index + items.length) % items.length;
      items.forEach(function (item, i) {
        item.setAttribute("aria-selected", i === active ? "true" : "false");
      });
      input.setAttribute("aria-activedescendant", items[active].id);
      items[active].scrollIntoView({ block: "nearest" });
    }
    function pick(item) {
      var found = native.querySelector('option[value="' + item.id + '"]');
      if (!found) {
        found = document.createElement("option");
        found.value = item.id;
        found.textContent = item.path;
        native.appendChild(found);
      }
      native.value = item.id;
      native.dispatchEvent(new Event("change", { bubbles: true }));
      input.value = item.path;
      close();
      announce(chosenText.replace("{name}", item.path));
    }
    function render(results) {
      list.textContent = "";
      results.forEach(function (item, i) {
        var li = document.createElement("li");
        li.id = id + "-opt-" + i;
        li.className = "fitment-picker__option";
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", "false");
        li.textContent = item.path;
        li.addEventListener("mousedown", function (event) {
          event.preventDefault();
          pick(item);
        });
        list.appendChild(li);
      });
      if (!results.length) {
        var empty = document.createElement("li");
        empty.className = "fitment-picker__none";
        empty.setAttribute("role", "presentation");
        empty.textContent = noneText;
        list.appendChild(empty);
      }
      list.hidden = false;
      input.setAttribute("aria-expanded", results.length ? "true" : "false");
      list.results = results;
      announce(results.length ? foundText.replace("{n}", results.length) : noneText);
    }
    function search() {
      var text = input.value.trim();
      var mine = ++ticket;
      if (text.length < 2) {
        close();
        return;
      }
      fetch(url + "?q=" + encodeURIComponent(text), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      }).then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      }).then(function (data) {
        if (mine === ticket) render(data.results);
      }).catch(function () {
        if (mine === ticket) close();
      });
    }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(search, 250);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (list.hidden) return;
        event.preventDefault();
        mark(active < 0 ? (event.key === "ArrowDown" ? 0 : -1) : active + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Enter") {
        if (!list.hidden && active >= 0 && list.results) {
          event.preventDefault();
          pick(list.results[active]);
        } else if (!list.hidden) {
          event.preventDefault();
        }
      } else if (event.key === "Escape" && !list.hidden) {
        event.preventDefault();
        close();
      }
    });
    input.addEventListener("blur", close);
  }

  document.querySelectorAll("select[data-fitment-picker]").forEach(function (native) {
    if (!native.value) enhance(native);
  });
})();
