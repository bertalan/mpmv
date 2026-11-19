// FIT.3: the cascading node picker of the Fits page. The native select of every blank row stays
// in the page as the submitted field (and the no-script fallback); this adds one select per level
// (Make, Model, Variant), each filled from the JSON endpoint, and writes the last choice into it.
// A row that already has an item keeps its native select. If the endpoint fails, the native
// select comes back. Strings and the URL come from data attributes; no inline script (CSP).
(function () {
  var root = document.querySelector("[data-fitment-picker-url]");
  if (!root || !window.fetch) return;
  var url = root.getAttribute("data-fitment-picker-url");
  var firstText = root.getAttribute("data-fitment-picker-first") || "{label}";
  var countText = root.getAttribute("data-fitment-picker-count") || "{n}";
  var moreText = root.getAttribute("data-fitment-picker-more") || "";
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
  }

  document.querySelectorAll("select[data-fitment-picker]").forEach(function (native) {
    if (!native.value) enhance(native);
  });
})();
