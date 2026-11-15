/* ──────────────────────────────────────────────────────────────────────────
   Table Builder — interactive grid editor: custom headers/cells, styling,
   merged cells, live-bill tokens, save-as-template, export (xlsx/pdf/print).
   Grid is held as a 2D array of cell objects; saved/exported as the sparse
   schema { rows, cols, colWidths, cells:{"r,c":{t,b,i,a,bg,fg,h,rs,cs,hidden}} }.
   ────────────────────────────────────────────────────────────────────────── */
(function () {
    'use strict';
    var CFG = window.TB_CONFIG || {};
    var gridEl = document.getElementById('tbGrid');
    if (!gridEl) return;

    var grid = [];           // grid[r][c] = cell obj
    var ROWS = 0, COLS = 0;
    var sel = null;          // {r1,c1,r2,c2}
    var anchor = null;       // {r,c} for shift-extend
    var lastFocus = null;    // {r,c} last edited cell
    var previewOn = false;
    var billId = '';
    var tokenValues = null;  // {token: value} for the chosen bill
    var templateId = CFG.templateId || null;

    function emptyCell() { return { t: '', a: 'left' }; }

    // ── load ────────────────────────────────────────────────────────────────
    function fromSparse(data) {
        grid = []; ROWS = data.rows; COLS = data.cols;
        for (var r = 0; r < ROWS; r++) {
            var row = [];
            for (var c = 0; c < COLS; c++) {
                var cell = (data.cells && data.cells[r + ',' + c]) ? data.cells[r + ',' + c] : emptyCell();
                if (cell.a === undefined) cell.a = 'left';
                row.push(cell);
            }
            grid.push(row);
        }
    }
    function defaultData() {
        var cells = {};
        for (var c = 0; c < 3; c++) cells['0,' + c] = { t: 'Header ' + (c + 1), h: 1, a: 'center', b: 1 };
        return { rows: 3, cols: 3, colWidths: [], cells: cells };
    }
    fromSparse(CFG.data && CFG.data.rows ? CFG.data : defaultData());

    // ── serialize ─────────────────────────────────────────────────────────────
    function toSparse() {
        var cells = {};
        for (var r = 0; r < ROWS; r++) for (var c = 0; c < COLS; c++) {
            var cell = grid[r][c], o = {};
            if (cell.t) o.t = cell.t;
            if (cell.b) o.b = 1;
            if (cell.i) o.i = 1;
            if (cell.a && cell.a !== 'left') o.a = cell.a;
            if (cell.bg) o.bg = cell.bg;
            if (cell.fg) o.fg = cell.fg;
            if (cell.h) o.h = 1;
            if (cell.rs && cell.rs > 1) o.rs = cell.rs;
            if (cell.cs && cell.cs > 1) o.cs = cell.cs;
            if (cell.hidden) o.hidden = 1;
            if (Object.keys(o).length) cells[r + ',' + c] = o;
        }
        return { rows: ROWS, cols: COLS, colWidths: [], cells: cells };
    }

    // ── token resolution (mirrors server bill_token_map) ────────────────────
    function resolve(text) {
        if (!text || text.indexOf('{{') < 0 || !tokenValues) return text;
        return text.replace(/\{\{\s*(\w+)\s*\}\}/g, function (m, k) {
            return (k in tokenValues) ? tokenValues[k] : m;
        });
    }

    // ── render ──────────────────────────────────────────────────────────────
    function inSel(r, c) { return sel && r >= sel.r1 && r <= sel.r2 && c >= sel.c1 && c <= sel.c2; }

    function render() {
        gridEl.innerHTML = '';
        var editable = !previewOn && CFG.canManage;
        for (var r = 0; r < ROWS; r++) {
            var tr = document.createElement('tr');
            for (var c = 0; c < COLS; c++) {
                var cell = grid[r][c];
                if (cell.hidden) continue;
                var td = document.createElement('td');
                td.dataset.r = r; td.dataset.c = c;
                if (cell.rs && cell.rs > 1) td.rowSpan = cell.rs;
                if (cell.cs && cell.cs > 1) td.colSpan = cell.cs;
                if (cell.h) td.classList.add('tb-header');
                if (inSel(r, c)) td.classList.add('tb-sel');
                td.style.textAlign = cell.a || 'left';
                td.style.fontWeight = (cell.b || cell.h) ? '800' : '400';
                td.style.fontStyle = cell.i ? 'italic' : 'normal';
                if (cell.bg) td.style.background = cell.bg;
                if (cell.fg) td.style.color = cell.fg;
                td.textContent = previewOn ? resolve(cell.t || '') : (cell.t || '');
                if (editable) td.contentEditable = 'true';
                bindCell(td, r, c);
                tr.appendChild(td);
            }
            gridEl.appendChild(tr);
        }
    }

    function bindCell(td, r, c) {
        td.addEventListener('mousedown', function (e) {
            if (e.shiftKey && anchor) {
                sel = norm(anchor.r, anchor.c, r, c);
            } else {
                anchor = { r: r, c: c };
                sel = norm(r, c, r, c);
            }
            paintSel();
        });
        td.addEventListener('focus', function () { lastFocus = { r: r, c: c }; });
        td.addEventListener('input', function () { grid[r][c].t = td.innerText; });
        td.addEventListener('blur', function () { grid[r][c].t = td.innerText; });
    }

    function norm(r1, c1, r2, c2) {
        return { r1: Math.min(r1, r2), c1: Math.min(c1, c2), r2: Math.max(r1, r2), c2: Math.max(c1, c2) };
    }
    // repaint just the selection outline without losing caret/focus
    function paintSel() {
        gridEl.querySelectorAll('td').forEach(function (td) {
            var r = +td.dataset.r, c = +td.dataset.c;
            td.classList.toggle('tb-sel', inSel(r, c));
        });
    }

    // ── selection helpers ─────────────────────────────────────────────────────
    function selectedCells() {   // visible (non-hidden) cells inside sel
        var out = [];
        if (!sel) return out;
        for (var r = sel.r1; r <= sel.r2; r++) for (var c = sel.c1; c <= sel.c2; c++) {
            if (!grid[r][c].hidden) out.push({ r: r, c: c, cell: grid[r][c] });
        }
        return out;
    }
    function apply(fn) {
        var cells = selectedCells();
        if (!cells.length && lastFocus) cells = [{ r: lastFocus.r, c: lastFocus.c, cell: grid[lastFocus.r][lastFocus.c] }];
        cells.forEach(function (x) { fn(x.cell); });
        render();
    }

    // ── merge / unmerge ───────────────────────────────────────────────────────
    function unmergeAnchor(r, c) {
        var cell = grid[r][c];
        var rs = cell.rs || 1, cs = cell.cs || 1;
        for (var rr = r; rr < r + rs; rr++) for (var cc = c; cc < c + cs; cc++) {
            grid[rr][cc].hidden = false;
        }
        cell.rs = 1; cell.cs = 1;
    }
    function unmergeIntersecting(kind, index) {  // before deleting a row/col
        for (var r = 0; r < ROWS; r++) for (var c = 0; c < COLS; c++) {
            var cell = grid[r][c], rs = cell.rs || 1, cs = cell.cs || 1;
            if (rs > 1 || cs > 1) {
                if (kind === 'row' && index >= r && index < r + rs) unmergeAnchor(r, c);
                if (kind === 'col' && index >= c && index < c + cs) unmergeAnchor(r, c);
            }
        }
    }

    var ACTIONS = {
        addRow: function () { var row = []; for (var c = 0; c < COLS; c++) row.push(emptyCell()); grid.push(row); ROWS++; render(); },
        addCol: function () { for (var r = 0; r < ROWS; r++) grid[r].push(emptyCell()); COLS++; render(); },
        delRow: function () { if (ROWS <= 1) return; unmergeIntersecting('row', ROWS - 1); grid.pop(); ROWS--; sel = null; render(); },
        delCol: function () { if (COLS <= 1) return; unmergeIntersecting('col', COLS - 1); grid.forEach(function (row) { row.pop(); }); COLS--; sel = null; render(); },
        bold: function () { var all = selectedCells().every(function (x) { return x.cell.b; }); apply(function (c) { c.b = all ? 0 : 1; }); },
        italic: function () { var all = selectedCells().every(function (x) { return x.cell.i; }); apply(function (c) { c.i = all ? 0 : 1; }); },
        alignleft: function () { apply(function (c) { c.a = 'left'; }); },
        aligncenter: function () { apply(function (c) { c.a = 'center'; }); },
        alignright: function () { apply(function (c) { c.a = 'right'; }); },
        header: function () { var all = selectedCells().every(function (x) { return x.cell.h; }); apply(function (c) { c.h = all ? 0 : 1; }); },
        clearfmt: function () { apply(function (c) { delete c.bg; delete c.fg; }); },
        merge: function () {
            if (!sel || (sel.r1 === sel.r2 && sel.c1 === sel.c2)) { alert('Select a range of cells first (click, then Shift+click another cell).'); return; }
            for (var r = sel.r1; r <= sel.r2; r++) for (var c = sel.c1; c <= sel.c2; c++) {
                var cell = grid[r][c];
                if ((cell.rs || 1) > 1 || (cell.cs || 1) > 1) unmergeAnchor(r, c);
            }
            var a = grid[sel.r1][sel.c1];
            a.rs = sel.r2 - sel.r1 + 1; a.cs = sel.c2 - sel.c1 + 1;
            for (var rr = sel.r1; rr <= sel.r2; rr++) for (var cc = sel.c1; cc <= sel.c2; cc++) {
                if (rr === sel.r1 && cc === sel.c1) continue;
                grid[rr][cc].hidden = true;
            }
            render();
        },
        unmerge: function () {
            selectedCells().forEach(function (x) {
                if ((x.cell.rs || 1) > 1 || (x.cell.cs || 1) > 1) unmergeAnchor(x.r, x.c);
            });
            render();
        },
        preview: function () {
            previewOn = !previewOn;
            document.getElementById('tbPreview').classList.toggle('bg-emerald-100', previewOn);
            if (previewOn && billId && !tokenValues) { fetchTokens(render); } else { render(); }
        },
        excel: function () { exportXlsx(); },
        pdf: function () { exportPdf(); },
        print: function () { printTable(); },
    };

    document.querySelectorAll('[data-act]').forEach(function (btn) {
        btn.addEventListener('mousedown', function (e) { e.preventDefault(); }); // keep selection
        btn.addEventListener('click', function () { var a = ACTIONS[btn.dataset.act]; if (a) a(); });
    });

    // colour inputs
    var bgIn = document.getElementById('tbBg'), fgIn = document.getElementById('tbFg');
    if (bgIn) bgIn.addEventListener('input', function () { apply(function (c) { c.bg = bgIn.value; }); });
    if (fgIn) fgIn.addEventListener('input', function () { apply(function (c) { c.fg = fgIn.value; }); });

    // token palette
    var tokenSel = document.getElementById('tbToken');
    if (tokenSel) tokenSel.addEventListener('change', function () {
        var tok = tokenSel.value; tokenSel.value = '';
        if (!tok) return;
        var target = lastFocus || (sel ? { r: sel.r1, c: sel.c1 } : null);
        if (!target) { alert('Click a cell first, then insert a token.'); return; }
        var cell = grid[target.r][target.c];
        cell.t = (cell.t || '') + '{{' + tok + '}}';
        render();
    });

    // bill picker
    var billSel = document.getElementById('tbBill');
    if (billSel) {
        (CFG.bills || []).forEach(function (b) {
            var o = document.createElement('option');
            o.value = b.id;
            o.textContent = (b.invoice_number || ('#' + b.id)) + ' — ' + (b.supplier_name || '');
            billSel.appendChild(o);
        });
        billSel.addEventListener('change', function () {
            billId = billSel.value; tokenValues = null;
            if (previewOn) { billId ? fetchTokens(render) : render(); }
        });
    }

    function fetchTokens(cb) {
        if (!billId) { tokenValues = {}; if (cb) cb(); return; }
        fetch(CFG.urls.tokens + '?bill_id=' + encodeURIComponent(billId))
            .then(function (r) { return r.json(); })
            .then(function (d) { tokenValues = d.tokens || {}; if (cb) cb(); })
            .catch(function () { tokenValues = {}; if (cb) cb(); });
    }

    // ── save ──────────────────────────────────────────────────────────────────
    function postJSON(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
            body: JSON.stringify(body)
        });
    }
    function doSave(asNew) {
        var name = (document.getElementById('tbName').value || '').trim() || 'Untitled Table';
        postJSON(CFG.urls.save, { id: asNew ? null : templateId, name: name, data: toSparse() })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.ok) {
                    templateId = d.id;
                    // reflect the id in the URL so later saves update the same template
                    if (history.replaceState) history.replaceState(null, '', '/tables/' + d.id + '/edit/');
                    flash('Saved ✓');
                } else { alert('Save failed.'); }
            }).catch(function () { alert('Save failed (network).'); });
    }
    var saveBtn = document.getElementById('tbSave'); if (saveBtn) saveBtn.addEventListener('click', function () { doSave(false); });
    var saveAsBtn = document.getElementById('tbSaveAs'); if (saveAsBtn) saveAsBtn.addEventListener('click', function () { doSave(true); });

    function flash(msg) {
        var el = document.createElement('div');
        el.textContent = msg;
        el.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#10b981;color:#fff;padding:8px 16px;border-radius:8px;font-weight:700;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,.2)';
        document.body.appendChild(el);
        setTimeout(function () { el.remove(); }, 1800);
    }

    // ── exports ───────────────────────────────────────────────────────────────
    function exportXlsx() {
        var name = (document.getElementById('tbName').value || 'table').trim() || 'table';
        postJSON(CFG.urls.export, { data: toSparse(), name: name, bill_id: billId })
            .then(function (r) { return r.blob(); })
            .then(function (blob) {
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url; a.download = name.replace(/[^a-z0-9 _-]/gi, '') + '.xlsx';
                document.body.appendChild(a); a.click(); a.remove();
                URL.revokeObjectURL(url);
            }).catch(function () { alert('Excel export failed.'); });
    }

    // A detached, resolved clone of the grid for PDF/print (tokens filled if a bill is chosen)
    function resolvedClone() {
        var wasPreview = previewOn;
        if (!tokenValues && billId) { /* best-effort: values may be absent */ }
        var clone = gridEl.cloneNode(true);
        clone.querySelectorAll('td').forEach(function (td) {
            td.classList.remove('tb-sel');
            td.removeAttribute('contenteditable');
            var r = +td.dataset.r, c = +td.dataset.c;
            if (grid[r] && grid[r][c]) td.textContent = resolve(grid[r][c].t || '');
        });
        return clone;
    }
    function exportPdf() {
        var name = (document.getElementById('tbName').value || 'table').trim() || 'table';
        var go = function () {
            var holder = document.createElement('div');
            holder.style.padding = '10px';
            holder.appendChild(resolvedClone());
            if (typeof html2pdf === 'undefined') { alert('PDF library unavailable (offline). Use Print → Save as PDF.'); return; }
            html2pdf().set({
                margin: 0.3, filename: name.replace(/[^a-z0-9 _-]/gi, '') + '.pdf',
                image: { type: 'jpeg', quality: 0.98 },
                html2canvas: { scale: 2, backgroundColor: '#ffffff' },
                jsPDF: { unit: 'in', format: 'a4', orientation: 'landscape' }
            }).from(holder).save();
        };
        if (billId && !tokenValues) fetchTokens(go); else go();
    }
    function printTable() {
        var go = function () {
            var restore = previewOn;
            previewOn = true; render();          // resolved view for printing
            window.print();
            previewOn = restore; render();
        };
        if (billId && !tokenValues) fetchTokens(go); else go();
    }

    render();
})();
