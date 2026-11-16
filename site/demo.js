// Animates the illustrative spreadsheet in the page header. The numbers are
// generated here, in the browser - the same thing the extension's built-in
// "calcrtd.sim" test source does inside Calc.
(function () {
  var cells = {};
  document.querySelectorAll("[data-sim]").forEach(function (cell) {
    cells[cell.getAttribute("data-sim")] = cell;
  });
  var state = { counter: 0, acme: 38.5, globex: 112.4 };

  function flash(cell, direction) {
    cell.classList.remove("up", "down");
    void cell.offsetWidth;
    cell.classList.add(direction);
    setTimeout(function () { cell.classList.remove(direction); }, 150);
  }

  function set(name, text, direction) {
    var cell = cells[name];
    if (!cell || cell.textContent === text) return;
    cell.textContent = text;
    if (direction) flash(cell, direction);
  }

  function walk(key, name) {
    var before = state[key];
    state[key] = Math.max(0.01, before * (1 + (Math.random() - 0.5) * 0.004));
    var now = state[key].toFixed(2);
    if (now !== before.toFixed(2)) set(name, now, state[key] > before ? "up" : "down");
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  setInterval(function () {
    var d = new Date();
    set("clock", pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()));
  }, 250);
  setInterval(function () { state.counter += 1; set("counter", String(state.counter), "up"); }, 1000);
  setInterval(function () { walk("acme", "acme"); }, 600);
  setInterval(function () { walk("globex", "globex"); }, 850);
})();
