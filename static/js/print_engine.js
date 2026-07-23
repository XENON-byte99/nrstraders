/* ──────────────────────────────────────────────────────────────────────────
   PrintEngine — the single implementation of every layout edit a printed
   document supports.

   Both entry points use it, so an edit behaves identically wherever it is
   made:
     • documents/print_template_manager.html  → one document at a time
     • documents/print_bundle.html            → the joint/bulk print screen

   Everything here is scoped to a "root" element (the A4 sheet, or a bundle
   wrapper) so the same call works for one document or for twenty on a page.
   ────────────────────────────────────────────────────────────────────────── */
(function (global) {
    'use strict';

    var A4_W = 793.92;   // 8.27in @ 96dpi
    var A4_H = 1122.24;  // 11.69in @ 96dpi

    /* Measuring styles.
       "data" pass  → the table minus its header, at max-content with nothing
                      wrapping: each column's width is what its VALUES need to
                      sit on one line.
       "word" pass  → the header row at min-content: the longest single word.
       A header like "Unit price Incl. Tax" is allowed to wrap onto two lines,
       so it must not be what decides the column width. */
    var MEASURE_CSS =
        'table.pe-measure-data { table-layout: auto !important; width: max-content !important; }' +
        'table.pe-measure-data td { white-space: nowrap !important; word-break: normal !important; }' +
        'table.pe-measure-word { table-layout: auto !important; width: min-content !important; }' +
        'table.pe-measure-word th { white-space: normal !important; word-break: normal !important; overflow-wrap: break-word !important; }' +
        '.pe-measure-host { position: absolute !important; left: -99999px !important; top: 0 !important; width: auto !important; visibility: hidden !important; pointer-events: none !important; }';

    function ensureMeasureCss() {
        if (document.getElementById('pe-measure-css')) return;
        var st = document.createElement('style');
        st.id = 'pe-measure-css';
        st.textContent = MEASURE_CSS;
        document.head.appendChild(st);
    }

    /* ── Header text ──────────────────────────────────────────────────────
       The label without the edit-mode controls that get injected into it.
       One definition, so a fingerprint taken on the bundle screen matches the
       one taken on the single-document screen (they used to differ, which
       silently threw away saved column layouts).                            */
    function thText(th) {
        if (!th) return '';
        if (th.dataset.origText) {
            var tmp = document.createElement('div');
            tmp.innerHTML = th.dataset.origText;
            return tmp.textContent.trim();
        }
        var clone = th.cloneNode(true);
        clone.querySelectorAll('.resizer, .hide-col-btn, .section-drag-handle').forEach(function (n) { n.remove(); });
        return clone.textContent.trim();
    }

    function thHtml(th) {
        var clone = th.cloneNode(true);
        clone.querySelectorAll('.resizer, .hide-col-btn, .section-drag-handle').forEach(function (n) { n.remove(); });
        return clone.innerHTML;
    }

    function headers(table) {
        return Array.prototype.slice.call(table.querySelectorAll('thead th'));
    }

    function fingerprint(table) {
        return headers(table).map(thText).join('|');
    }

    /* ── Colspan bookkeeping ─────────────────────────────────────────────
       Idempotent: each cell remembers its untouched span, so running this after
       a hide and again after an unhide always lands on the right numbers.   */
    function syncColspans(table) {
        var ths = headers(table);
        if (!ths.length) return;
        var isColHidden = ths.map(function (th) { return th.classList.contains('hidden-col'); });

        table.querySelectorAll('tbody tr').forEach(function (tr) {
            var cells = Array.prototype.slice.call(tr.children);
            var hasColspan = false;

            cells.forEach(function (cell) {
                if (!cell.dataset.origColspan) {
                    cell.dataset.origColspan = cell.getAttribute('colspan') || '1';
                }
                if (parseInt(cell.dataset.origColspan, 10) > 1) hasColspan = true;
            });

            var visualColCount = cells.reduce(function (n, cell) {
                return n + parseInt(cell.dataset.origColspan || '1', 10);
            }, 0);
            if (visualColCount !== ths.length) return;

            if (!hasColspan) {
                cells.forEach(function (cell, colIdx) {
                    cell.classList.toggle('hidden-col', !!isColHidden[colIdx]);
                });
                return;
            }

            var currentColIdx = 0;
            cells.forEach(function (cell) {
                var origColspan = parseInt(cell.dataset.origColspan || '1', 10);
                var hiddenCount = 0;
                for (var c = currentColIdx; c < currentColIdx + origColspan; c++) {
                    if (isColHidden[c]) hiddenCount++;
                }
                var newColspan = origColspan - hiddenCount;
                if (newColspan > 0) {
                    cell.setAttribute('colspan', newColspan);
                    cell.classList.remove('hidden-col');
                } else {
                    cell.classList.add('hidden-col');
                }
                currentColIdx += origColspan;
            });
        });
    }

    /* ── Auto-fit columns ────────────────────────────────────────────────
       After a column is hidden or shown, re-deal the freed width so every
       cell's text sits on ONE line wherever that is possible.

       Short columns (SL, Date, Qty, money) get exactly the width their
       widest value needs; the descriptive column absorbs whatever is left.
       If even that is not enough, the short columns are scaled down together
       rather than letting one column wrap on its own.                       */
    /* Two widths per VISIBLE column, measured on off-screen clones inside the
       same parent so every inherited style still applies:

         natural — what the column needs for its values to sit on ONE line
                   (body cells at max-content, floored by the header's longest
                    word: a header may wrap, a value should not)
         floor   — the longest single word anywhere in the column. Going below
                   this cannot help: the word just breaks instead.

       A money column is one unbreakable token, so its floor equals its natural
       width and it is never the column that gets squeezed.                    */
    function measureNaturalWidths(table) {
        var host = document.createElement('div');
        host.className = 'pe-measure-host';
        (table.parentNode || document.body).appendChild(host);

        function buildClone(headerOnly) {
            var c = table.cloneNode(true);
            c.querySelectorAll('.resizer, .hide-col-btn').forEach(function (n) { n.remove(); });
            c.querySelectorAll('th, td').forEach(function (cell) { cell.style.width = ''; });
            c.querySelectorAll('tr').forEach(function (tr) {
                Array.prototype.slice.call(tr.children).forEach(function (cell) {
                    if (cell.classList.contains('hidden-col')) cell.remove();
                });
            });
            var head = c.querySelector('thead');
            var body = c.querySelector('tbody');
            if (headerOnly === true) { if (body) body.remove(); }
            else if (headerOnly === false && head) { head.remove(); }
            // headerOnly === null keeps both, for the longest-word pass
            return c;
        }

        function widthsOf(clone, cls) {
            clone.classList.add(cls);
            host.appendChild(clone);
            var row = clone.querySelector('tr');
            // table-layout:auto shares one width per column, so any row will do.
            var w = row ? Array.prototype.slice.call(row.children).map(function (cell) {
                return cell.getBoundingClientRect().width;
            }) : [];
            return w;
        }

        var dataW = widthsOf(buildClone(false), 'pe-measure-data');
        var headW = widthsOf(buildClone(true), 'pe-measure-word');
        var wordW = widthsOf(buildClone(null), 'pe-measure-word');   // head + body, min-content
        host.remove();

        var n = Math.max(dataW.length, headW.length, wordW.length);
        var natural = [], floor = [];
        for (var i = 0; i < n; i++) {
            var nat = Math.ceil(Math.max(dataW[i] || 0, headW[i] || 0)) + 2;  // +2 = breathing room
            natural.push(nat);
            floor.push(Math.min(nat, Math.ceil(wordW[i] || 0) + 2));
        }
        return { natural: natural, floor: floor };
    }

    function autoFitColumns(table) {
        if (!table) return;
        var ths = headers(table);
        if (!ths.length) return;
        var visible = ths.filter(function (th) { return !th.classList.contains('hidden-col'); });
        if (!visible.length) return;

        var avail = table.clientWidth || table.offsetWidth;
        if (!avail) return;

        ensureMeasureCss();

        // Remember the template's own widths once, so "reset" can get back to them.
        ths.forEach(function (th) {
            if (th.dataset.origWidth === undefined) th.dataset.origWidth = th.style.width || '';
        });

        var m = measureNaturalWidths(table);
        var natural = m.natural, floor = m.floor;
        if (!natural.length) return;

        var add = function (a, b) { return a + b; };
        var total = natural.reduce(add, 0);
        var widths;

        if (total <= avail) {
            // Everything fits on one line. Justify: share the spare width out over
            // ALL the remaining columns in proportion to what each already uses,
            // so hiding a column widens the whole row evenly instead of dumping
            // the freed space into one column.
            var surplus = avail - total;
            widths = natural.map(function (w) { return w + surplus * (w / total); });
        } else {
            // Too many columns to keep every cell on one line. Take the squeeze
            // only out of what a column can give up without breaking a word:
            // prose columns narrow, money and dates keep their width, so as few
            // cells as possible drop onto a second line.
            var floorTotal = floor.reduce(add, 0);
            if (floorTotal >= avail) {
                // Even the longest words alone overflow — nothing left but to scale.
                widths = floor.map(function (w) { return w * avail / floorTotal; });
            } else {
                var slack = natural.map(function (w, i) { return Math.max(0, w - floor[i]); });
                var slackTotal = slack.reduce(add, 0);
                var extra = avail - floorTotal;
                widths = floor.map(function (w, i) {
                    return w + (slackTotal > 0 ? extra * (slack[i] / slackTotal) : extra / floor.length);
                });
            }
        }

        // Percentages, so the widths survive the whole-page rescale.
        var sum = widths.reduce(function (a, b) { return a + b; }, 0);
        visible.forEach(function (th, i) {
            th.style.width = (widths[i] / sum * 100).toFixed(3) + '%';
        });
        ths.forEach(function (th) {
            if (th.classList.contains('hidden-col')) th.style.width = '';
        });
    }

    function autoFitAll(root) {
        (root || document).querySelectorAll('table').forEach(function (t) {
            if (t.querySelector('thead th')) autoFitColumns(t);
        });
    }

    /* ── Hide / show one column ──────────────────────────────────────────
       The single path both screens call, so the two always behave the same.
       Width redistribution is NOT done here — callers finish with reflow(),
       which measures against the sheet's base width and then rescales once. */
    function setColumnHidden(table, th, hidden) {
        th.classList.toggle('hidden-col', !!hidden);
        syncColspans(table);
    }

    /* ── The sheet a node belongs to ─────────────────────────────────── */
    function sheetOf(node) {
        if (!node) return null;
        if (node.classList && node.classList.contains('a4-invoice')) return node;
        return node.closest ? node.closest('.a4-invoice, .quotation-container') : null;
    }

    /* ── Reflow: re-deal column widths, then re-fit the page ─────────────
       Column widths must be measured with the sheet at its true A4 width —
       fit() stretches .fit-wrap and scales it back down, so measuring while
       that stretch is applied hands the description column width it does not
       actually have, and the page then overflows and shrinks to the floor. */
    function reflow(a4, opts) {
        a4 = sheetOf(a4);
        if (!a4) return 1;
        opts = opts || {};
        var wrap = a4.querySelector('.fit-wrap');
        var baseW = opts.baseW || a4.clientWidth || A4_W;
        if (wrap) {
            wrap.style.transform = 'none';
            wrap.style.width = baseW + 'px';
            a4.removeAttribute('data-fit-scale');
        }
        autoFitAll(a4);
        return fit(a4, opts);
    }

    /* ── Table state (save / restore) ────────────────────────────────── */
    function readTableState(table) {
        var state = { fingerprint: fingerprint(table), cols: [] };
        headers(table).forEach(function (th) {
            state.cols.push({
                text: thHtml(th),
                width: th.style.width,
                hidden: th.classList.contains('hidden-col')
            });
        });
        return state;
    }

    function resetTable(table) {
        headers(table).forEach(function (th) {
            th.classList.remove('hidden-col');
            if (th.dataset.origText) th.innerHTML = th.dataset.origText;
            th.style.width = th.dataset.origWidth !== undefined ? th.dataset.origWidth : '';
        });
        table.querySelectorAll('tbody tr').forEach(function (tr) {
            Array.prototype.slice.call(tr.children).forEach(function (td) {
                td.classList.remove('hidden-col');
                if (td.dataset.origColspan) td.setAttribute('colspan', td.dataset.origColspan);
            });
        });
    }

    function applyTableState(table, tState, opts) {
        if (!tState || !tState.cols) return false;
        opts = opts || {};
        var ths = headers(table);
        if (tState.cols.length !== ths.length) return false;
        if (tState.fingerprint && tState.fingerprint !== fingerprint(table)) return false;

        tState.cols.forEach(function (colState, i) {
            var th = ths[i];
            if (!th) return;
            if (!th.dataset.origText) th.dataset.origText = thHtml(th);
            if (th.dataset.origWidth === undefined) th.dataset.origWidth = th.style.width || '';
            if (colState.text) {
                th.innerHTML = colState.text;
                if (opts.onHeaderReplaced) opts.onHeaderReplaced(th, table, i);
            }
            th.style.width = colState.width || '';
            th.classList.toggle('hidden-col', !!colState.hidden);
        });
        syncColspans(table);
        return true;
    }

    /* ── Edit-mode controls on the column headers ────────────────────── */
    function attachColumnControls(root, onChange) {
        (root || document).querySelectorAll('table').forEach(function (table) {
            headers(table).forEach(function (th, idx) {
                if (!th.dataset.origText) th.dataset.origText = thHtml(th);
                if (th.dataset.origWidth === undefined) th.dataset.origWidth = th.style.width || '';

                th.classList.add('resizable');
                th.contentEditable = 'true';

                if (!th.querySelector('.resizer')) {
                    var resizer = document.createElement('div');
                    resizer.className = 'resizer no-print';
                    resizer.contentEditable = 'false';
                    th.appendChild(resizer);

                    resizer.addEventListener('mousedown', function (e) {
                        e.stopPropagation();
                        e.preventDefault();
                        var startX = e.clientX;
                        var startW = th.offsetWidth;
                        var nextTh = th.nextElementSibling;
                        while (nextTh && nextTh.classList.contains('hidden-col')) {
                            nextTh = nextTh.nextElementSibling;
                        }
                        var nextStartW = nextTh ? nextTh.offsetWidth : 0;
                        var tableW = table.offsetWidth || 1;

                        var onMove = function (ev) {
                            var diff = ev.clientX - startX;
                            if (startW + diff < 20) diff = 20 - startW;
                            if (nextTh && nextStartW - diff < 20) diff = nextStartW - 20;
                            // Percent widths keep the drag honest once the sheet is rescaled.
                            th.style.width = ((startW + diff) / tableW * 100).toFixed(3) + '%';
                            if (nextTh) nextTh.style.width = ((nextStartW - diff) / tableW * 100).toFixed(3) + '%';
                        };
                        var onUp = function () {
                            document.removeEventListener('mousemove', onMove);
                            document.removeEventListener('mouseup', onUp);
                            if (onChange) onChange('resize', table, th);
                        };
                        document.addEventListener('mousemove', onMove);
                        document.addEventListener('mouseup', onUp);
                    });
                }

                if (!th.querySelector('.hide-col-btn')) {
                    var hbtn = document.createElement('i');
                    hbtn.className = 'fas fa-eye-slash hide-col-btn no-print';
                    hbtn.contentEditable = 'false';
                    hbtn.title = 'Hide this column';
                    th.appendChild(hbtn);
                    hbtn.addEventListener('click', function (e) {
                        e.stopPropagation();
                        setColumnHidden(table, th, true);
                        if (onChange) onChange('hide-column', table, th);
                    });
                }
            });
        });
    }

    function detachColumnControls(root) {
        root = root || document;
        root.querySelectorAll('.resizer, .hide-col-btn').forEach(function (el) { el.remove(); });
        root.querySelectorAll('table th').forEach(function (th) {
            th.classList.remove('resizable');
            th.contentEditable = 'false';
        });
    }

    function showColumn(table, th, onChange) {
        setColumnHidden(table, th, false);
        if (onChange) onChange('show-column', table, th);
    }

    /* ── Edit-mode controls on the totals rows ───────────────────────── */
    function attachRowControls(root, onChange, skipIds) {
        skipIds = skipIds || ['grand-total', 'subtotal'];
        (root || document).querySelectorAll('.amount-row').forEach(function (row) {
            var rowId = row.dataset.amountRowId;
            if (!rowId || skipIds.indexOf(rowId) !== -1) return;
            if (row.querySelector('.hide-row-btn')) return;
            var hbtn = document.createElement('i');
            hbtn.className = 'fas fa-eye-slash hide-row-btn no-print';
            hbtn.contentEditable = 'false';
            hbtn.title = 'Hide this row';
            row.appendChild(hbtn);
            hbtn.addEventListener('click', function (e) {
                e.stopPropagation();
                row.classList.add('hidden-row');
                if (onChange) onChange('hide-row', row);
            });
        });
    }

    function detachRowControls(root) {
        (root || document).querySelectorAll('.hide-row-btn').forEach(function (el) { el.remove(); });
    }

    /* ── One-page fit ────────────────────────────────────────────────────
       Scales .fit-wrap down until the document fits an A4 sheet in BOTH
       directions. Used by the single-document pages and by the bundle, so a
       document is scaled the same either way.                               */
    function fit(a4, opts) {
        if (!a4) return 1;
        var wrap = a4.querySelector('.fit-wrap');
        if (!wrap) return 1;
        opts = opts || {};

        var baseW = opts.baseW || a4.clientWidth || A4_W;
        var avail = (opts.availH || a4.clientHeight || A4_H) - 12;
        var manual = parseFloat(a4.getAttribute('data-user-scale')) || 1;

        wrap.style.transform = 'none';
        wrap.style.width = baseW + 'px';

        var k = 1;
        for (var iter = 0; iter < 5; iter++) {
            wrap.style.width = (baseW / k) + 'px';
            var natH = wrap.scrollHeight;
            var natW = wrap.scrollWidth;

            var newK = 1;
            if (natH > avail) newK = Math.min(newK, avail / natH);
            // Saved column widths can add up to more than the sheet is wide
            // (fixed px widths resized on a roomier screen). Without this the
            // extra width simply ran off the right edge.
            if (natW > baseW) newK = Math.min(newK, baseW / natW);

            newK = Math.min(newK, manual);
            if (newK < 0.4) newK = 0.4;
            if (Math.abs(k - newK) < 0.005) { k = newK; break; }
            k = newK;
        }

        if (k < 1) {
            wrap.style.width = (baseW / k) + 'px';
            wrap.style.transform = 'scale(' + k + ')';
            a4.setAttribute('data-fit-scale', k.toFixed(3));
        } else {
            wrap.style.width = baseW + 'px';
            wrap.style.transform = 'none';
            a4.removeAttribute('data-fit-scale');
        }
        return k;
    }


    /* ── PDF download ────────────────────────────────────────────────────
       One implementation for every print screen. The things that used to go
       wrong, all of which bite hardest on a phone:

         • html2pdf comes from a CDN. On a phone with no signal it never
           loads, the click threw a ReferenceError and the button looked
           dead. Now it says so and offers the print dialog instead.
         • html2canvas lays the page out at the CURRENT window width, so a
           390px-wide phone captured a squashed sheet. windowWidth/width pin
           it to a real A4.
         • margin:0.15in on an element that is already exactly A4 shrank the
           sheet and could spill a sliver onto a second page.
         • Edit-mode furniture (resize grips, hide-column eyes, drag handles)
           is inside the sheet and html2canvas does not honour @media print,
           so it was being baked into the PDF.                               */
    function downloadPdf(element, filename, opts) {
        opts = opts || {};
        if (!element) return Promise.resolve(false);

        if (typeof html2pdf === 'undefined') {
            if (window.confirm(
                'The PDF library could not be loaded — this usually means no internet connection.\n\n' +
                'Open the print dialog instead? From there you can choose "Save as PDF".')) {
                window.print();
            }
            return Promise.resolve(false);
        }

        // Make sure what we capture is what is on screen.
        if (typeof window.fitInvoiceToPage === 'function') window.fitInvoiceToPage();

        var oldShadow = element.style.boxShadow;
        element.style.boxShadow = 'none';

        var settings = {
            margin: 0,
            filename: filename,
            image: { type: 'jpeg', quality: 0.98 },
            html2canvas: {
                scale: opts.scale || 2,
                useCORS: true,
                letterRendering: true,
                backgroundColor: '#ffffff',
                scrollX: 0,
                scrollY: 0,
                windowWidth: opts.windowWidth || A4_W,
                ignoreElements: function (el) {
                    return el.classList && (el.classList.contains('no-print') ||
                                            el.classList.contains('resizer') ||
                                            el.classList.contains('hide-col-btn') ||
                                            el.classList.contains('hide-row-btn') ||
                                            el.classList.contains('section-drag-handle'));
                }
            },
            jsPDF: { unit: 'in', format: opts.format || 'a4', orientation: opts.orientation || 'portrait' },
            pagebreak: opts.pagebreak || { mode: ['css', 'legacy'] }
        };

        var restore = function () {
            element.style.boxShadow = oldShadow || '';
        };

        return html2pdf().set(settings).from(element).save()
            .then(function () { restore(); return true; })
            .catch(function (err) {
                console.error('PDF generation failed:', err);
                restore();
                window.alert('Could not build the PDF: ' + (err && err.message ? err.message : err) +
                             '\n\nTry the Print button and choose "Save as PDF".');
                return false;
            });
    }

    /* Wire a button to downloadPdf with a busy state, so a slow phone does not
       look like nothing happened. */
    function bindDownloadButton(button, getElement, getFilename, opts) {
        if (!button) return;
        button.addEventListener('click', function () {
            if (button.dataset.busy === '1') return;
            var label = button.innerHTML;
            button.dataset.busy = '1';
            button.disabled = true;
            button.innerHTML = 'Preparing PDF…';
            Promise.resolve(downloadPdf(getElement(), getFilename(), opts)).then(function () {
                button.dataset.busy = '';
                button.disabled = false;
                button.innerHTML = label;
            });
        });
    }

    global.PrintEngine = {
        A4_W: A4_W,
        A4_H: A4_H,
        thText: thText,
        thHtml: thHtml,
        headers: headers,
        fingerprint: fingerprint,
        syncColspans: syncColspans,
        autoFitColumns: autoFitColumns,
        autoFitAll: autoFitAll,
        setColumnHidden: setColumnHidden,
        sheetOf: sheetOf,
        reflow: reflow,
        showColumn: showColumn,
        readTableState: readTableState,
        applyTableState: applyTableState,
        resetTable: resetTable,
        attachColumnControls: attachColumnControls,
        detachColumnControls: detachColumnControls,
        attachRowControls: attachRowControls,
        detachRowControls: detachRowControls,
        fit: fit,
        downloadPdf: downloadPdf,
        bindDownloadButton: bindDownloadButton
    };
})(window);
