/* 표 헤더 클릭 정렬 — 연차·인사 화면 공용 (2026-08-06)
 *
 * 쓰는 법: 정렬을 붙일 <table> 에 class="js-sort" 만 달면 된다.
 *   <table class="table js-sort"> ... <thead><tr><th>이름</th>...
 *
 * 규칙
 *  - thead 의 마지막 행에 있는 th 를 클릭 대상으로 잡는다(2단 헤더 대응).
 *  - colspan/rowspan 이 섞인 헤더는 열 위치를 못 믿으므로 그 표는 건너뛴다.
 *  - 숫자·날짜는 숫자로, 나머지는 한글 정렬로 비교한다. '-' 와 빈칸은 항상 뒤로 보낸다.
 *  - 첫 열이 순번(#)이면 정렬 후 1부터 다시 매긴다.
 *  - tbody 가 여러 개면 첫 번째만 정렬한다(합계행 등을 건드리지 않기 위해).
 */
(function () {
  "use strict";

  function cellText(row, col) {
    var c = row.cells[col];
    return c ? c.textContent.trim() : "";
  }

  // 숫자로 볼 수 있으면 숫자를, 아니면 null
  function asNumber(s) {
    if (!s) return null;
    var t = s.replace(/[,\s]/g, "").replace(/[일건명시간원%]/g, "");
    if (t === "" || t === "-") return null;
    return /^[+-]?\d+(\.\d+)?$/.test(t) ? parseFloat(t) : null;
  }

  function isSeqHeader(th) {
    var t = (th.textContent || "").trim();
    return t === "#" || t === "No" || t === "NO" || t === "번호" || t === "순번";
  }

  function sortBy(table, col, th) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows);
    if (rows.length < 2) return;

    var dir = th.dataset.sortDir === "asc" ? "desc" : "asc";

    rows.sort(function (a, b) {
      var sa = cellText(a, col), sb = cellText(b, col);
      var ea = (sa === "" || sa === "-"), eb = (sb === "" || sb === "-");
      if (ea && eb) return 0;
      if (ea) return 1;          // 빈 값은 방향과 무관하게 항상 뒤
      if (eb) return -1;
      var na = asNumber(sa), nb = asNumber(sb);
      var cmp = (na !== null && nb !== null)
        ? na - nb
        : sa.localeCompare(sb, "ko", { numeric: true });
      return dir === "asc" ? cmp : -cmp;
    });

    var headRow = table.tHead.rows[table.tHead.rows.length - 1];
    var renumber = headRow.cells.length && isSeqHeader(headRow.cells[0]);
    rows.forEach(function (r, i) {
      if (renumber && r.cells[0]) r.cells[0].textContent = i + 1;
      tbody.appendChild(r);
    });

    // 아이콘 갱신
    Array.prototype.forEach.call(headRow.cells, function (c) {
      var ic = c.querySelector(".ts-icon");
      if (ic) ic.textContent = "";
      delete c.dataset.sortDir;
    });
    th.dataset.sortDir = dir;
    var icon = th.querySelector(".ts-icon");
    if (icon) icon.textContent = dir === "asc" ? " ▲" : " ▼";
  }

  function setup(table) {
    if (!table.tHead || !table.tBodies.length) return;
    var rows = table.tHead.rows;
    if (!rows.length) return;
    var headRow = rows[rows.length - 1];

    // 병합 헤더가 있으면 열 위치가 어긋나므로 정렬을 붙이지 않는다
    for (var i = 0; i < headRow.cells.length; i++) {
      var c = headRow.cells[i];
      if (c.colSpan > 1 || c.rowSpan > 1) return;
    }

    Array.prototype.forEach.call(headRow.cells, function (th, idx) {
      if (th.dataset.nosort !== undefined) return;
      th.style.cursor = "pointer";
      th.style.userSelect = "none";
      if (!th.title) th.title = "클릭하면 이 열로 정렬합니다";
      if (!th.querySelector(".ts-icon")) {
        var s = document.createElement("span");
        s.className = "ts-icon";
        s.style.color = "#2563eb";
        th.appendChild(s);
      }
      th.addEventListener("click", function () { sortBy(table, idx, th); });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table.js-sort").forEach(setup);
  });
})();
