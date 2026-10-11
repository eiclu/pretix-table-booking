/* Table and seat picker for the pretix "Numbered seats" plugin. */
(function () {
    'use strict';

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    var STATE_CLASS = {f: 'free', sel: 'sel', m: 'mine', h: 'held', s: 'sold', x: 'blocked'};

    function init() {
        var root = document.getElementById('numbered-spaces');
        var cfgEl = document.getElementById('numbered-spaces-config');
        if (!root || !cfgEl) return;
        var cfg = JSON.parse(cfgEl.textContent);
        var t = cfg.strings;

        var ownForm = document.getElementById('numbered-spaces-form');
        var mainBtn = document.getElementById('btn-add-to-cart');
        var mainForm = mainBtn ? mainBtn.closest('form') : null;
        var ownSubmit = document.querySelector('[data-numbered-submit]');
        var summary = root.querySelector('[data-numbered-summary]');

        // The popup only fills in the form. If the shop has its own "Add to cart" button, the
        // selection is submitted together with the other products.
        var holder = document.createElement('div');
        holder.hidden = true;
        (mainForm || ownForm).appendChild(holder);
        if (mainForm && ownSubmit) {
            ownSubmit.hidden = true;
            document.querySelector('[data-numbered-hint]').hidden = false;
        }

        var dialog = document.getElementById('numbered-dialog');
        var grid = dialog.querySelector('[data-numbered-grid]');
        var alertBox = dialog.querySelector('[data-numbered-alert]');
        var selection = {};  // table id -> sorted list of selected numbers
        var need = 0;        // filter: minimum free spaces a table must have, for all rooms
        var current = null;  // {room, table} shown in the dialog
        var working = [];    // selection inside the open dialog

        var tableIndex = {};
        Object.keys(cfg.rooms).forEach(function (rid) {
            cfg.rooms[rid].tables.forEach(function (table) { tableIndex[table.id] = {room: cfg.rooms[rid], rid: rid, table: table}; });
        });

        // Groups of space numbers that must be bought together: fixed pairs or single spaces.
        function groupsOf(room, table) {
            var out = [], step = room.pairs ? 2 : 1;
            for (var n = 1; n <= table.s.length; n += step) {
                out.push(step === 2 ? [n, n + 1] : [n]);
            }
            return out;
        }
        function isFree(table, group) { return group.every(function (n) { return table.s[n - 1] === 'f'; }); }
        function freeCount(room, table) {
            return groupsOf(room, table).reduce(function (a, g) { return a + (isFree(table, g) ? g.length : 0); }, 0);
        }
        function numbersIn(table, state) {
            var out = [];
            for (var n = 1; n <= table.s.length; n++) if (table.s[n - 1] === state) out.push(n);
            return out;
        }
        function selected(table) { return selection[table.id] || []; }

        function renderRoom(rid) {
            var room = cfg.rooms[rid];
            var box = root.querySelector('[data-numbered-room="' + rid + '"]');
            var list = box.querySelector('[data-numbered-tables]');
            var shown = 0, roomFree = 0, roomSize = 0;
            list.textContent = '';
            room.tables.forEach(function (table) {
                var free = freeCount(room, table), sel = selected(table), mine = numbersIn(table, 'm');
                var size = table.s.length - numbersIn(table, 'x').length;  // spaces that are for sale at all
                roomFree += free;
                roomSize += size;
                if (free < need && !sel.length) return;
                shown++;
                var card = el('button', 'numbered-table' + (sel.length ? ' has-selection' : ''));
                card.type = 'button';
                card.dataset.table = table.id;
                card.disabled = !free && !sel.length;
                card.appendChild(el('b', '', table.name));
                card.appendChild(el('span', free ? 'numbered-free' : 'numbered-full',
                    free ? t.tableFree.replace('%f', free).replace('%t', size) : t.soldOut));
                if (sel.length) card.appendChild(el('span', 'numbered-sel', t.selected.replace('%s', sel.join(', '))));
                if (mine.length) card.appendChild(el('span', '', t.inCart.replace('%s', mine.join(', '))));
                list.appendChild(card);
            });
            if (!shown) list.appendChild(el('p', 'text-muted', room.t.noTables));
            box.querySelector('[data-numbered-shown]').textContent =
                need ? '· ' + t.shown.replace('%a', shown).replace('%b', room.tables.length) : '';
            var pill = box.querySelector('[data-numbered-room-free]');
            pill.textContent = roomFree ? room.t.roomFree.replace('%f', roomFree).replace('%t', roomSize) : t.soldOut;
            pill.className = roomFree ? 'text-success' : 'text-danger';
        }
        function renderRooms() { Object.keys(cfg.rooms).forEach(renderRoom); }

        function syncForm() {
            holder.textContent = '';
            summary.textContent = '';
            var total = 0;
            Object.keys(selection).forEach(function (id) {
                var entry = tableIndex[id];
                if (!selection[id].length) return;
                selection[id].forEach(function (n) {
                    var input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = entry.room.field;
                    input.value = 'numbered-' + id + '-' + n;
                    holder.appendChild(input);
                    total++;
                });
                var line = el('div', 'numbered-summary-line');
                line.appendChild(el('span', '', entry.room.t.summaryLine.replace('%r', entry.room.name)
                    .replace('%t', entry.table.name).replace('%s', selection[id].join(', '))));
                line.appendChild(el('span', 'numbered-summary-price', selection[id].length + ' × ' + entry.room.price));
                var clear = el('button', 'btn btn-link btn-xs', '×');
                clear.type = 'button';
                clear.dataset.clear = id;
                clear.setAttribute('aria-label', t.clear + ': ' + entry.table.name);
                line.appendChild(clear);
                summary.appendChild(line);
            });
            summary.hidden = !total;
            if (total) {
                summary.insertBefore(el('strong', '', total === 1 ? t.countOne : t.countMany.replace('%d', total)),
                    summary.firstChild);
            }
            if (total && mainForm) {
                // pretix refuses to submit the shop form unless a quantity field is filled in
                var marker = document.createElement('input');
                marker.type = 'text';
                marker.className = 'input-item-count';
                marker.name = 'numberedspaces';
                marker.value = total;
                holder.appendChild(marker);
            }
            if (ownSubmit) ownSubmit.querySelector('button').disabled = !total;
        }

        function renderDialog() {
            var room = current.room, table = current.table;
            grid.classList.toggle('is-pairs', room.pairs);
            grid.textContent = '';
            groupsOf(room, table).forEach(function (group) {
                var has = function (c) { return group.some(function (n) { return table.s[n - 1] === c; }); };
                var on = group.every(function (n) { return working.indexOf(n) !== -1; });
                var state = on ? 'sel' : has('m') ? 'm' : has('s') ? 's' : has('h') ? 'h' : has('x') ? 'x' : 'f';
                var btn = el('button', 'numbered-space is-' + STATE_CLASS[state]);
                btn.type = 'button';
                btn.disabled = state !== 'sel' && state !== 'f';
                btn.setAttribute('aria-pressed', on ? 'true' : 'false');
                btn.setAttribute('aria-label', room.t.space.replace('%s', group.join(' + ')) + ', ' + t.states[state]);
                btn.dataset.numbers = group.join(',');
                btn.appendChild(el('b', '', group.join(' + ')));
                btn.appendChild(el('span', '', t.states[state]));
                grid.appendChild(btn);
            });
            var count = working.length;
            dialog.querySelector('[data-numbered-sum]').textContent = !count ? t.none
                : (count === 1 ? room.t.countOne : room.t.countMany.replace('%d', count)) + ' · ' + count + ' × ' + room.price;
        }

        // Drops selected spaces that are no longer free, always as whole groups.
        function prune(room, table, numbers) {
            var gone = [];
            groupsOf(room, table).forEach(function (group) {
                var picked = group.filter(function (n) { return numbers.indexOf(n) !== -1; });
                if (picked.length && !isFree(table, group)) gone = gone.concat(picked);
            });
            return gone;
        }

        function refresh() {
            fetch(cfg.statusUrl, {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) return;
                    Object.keys(data.tables).forEach(function (id) {
                        if (tableIndex[id]) tableIndex[id].table.s = data.tables[id];
                    });
                    Object.keys(selection).forEach(function (id) {
                        var entry = tableIndex[id], gone = prune(entry.room, entry.table, selection[id]);
                        selection[id] = selection[id].filter(function (n) { return gone.indexOf(n) === -1; });
                    });
                    if (current) {
                        var gone = prune(current.room, current.table, working);
                        if (gone.length) {
                            working = working.filter(function (n) { return gone.indexOf(n) === -1; });
                            alertBox.textContent = current.room.t.gone.replace('%s', gone.join(', '));
                            alertBox.hidden = false;
                        }
                        renderDialog();
                    }
                    renderRooms();
                    syncForm();
                })
                .catch(function () { /* keep the data from page load */ });
        }

        root.addEventListener('click', function (e) {
            var clear = e.target.closest('[data-clear]');
            if (clear) {
                selection[clear.dataset.clear] = [];
                renderRooms();
                syncForm();
                return;
            }
            var card = e.target.closest('[data-table]');
            if (!card || card.disabled) return;
            current = tableIndex[card.dataset.table];
            working = selected(current.table).slice();
            dialog.querySelector('#numbered-dialog-title').textContent = current.room.name + ' · ' + current.table.name;
            dialog.querySelector('[data-numbered-sub]').textContent = current.room.t.sub;
            alertBox.hidden = true;
            renderDialog();
            dialog.showModal();
            refresh();
        });

        function toggleRoom(btn, open) {
            var body = document.getElementById(btn.getAttribute('aria-controls'));
            if (body.hidden !== open) return;
            body.hidden = !open;
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
            var label = btn.querySelector('span');
            var alt = btn.dataset.labelAlt;
            btn.dataset.labelAlt = label.textContent;
            label.textContent = alt;
        }
        root.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-numbered-toggle]');
            if (btn) toggleRoom(btn, btn.getAttribute('aria-expanded') !== 'true');
        });
        var toggles = root.querySelectorAll('[data-numbered-toggle]');
        if (toggles.length === 1) toggleRoom(toggles[0], true);

        root.addEventListener('change', function (e) {
            if (!e.target.matches('[data-numbered-need]')) return;
            need = parseInt(e.target.value, 10) || 0;
            renderRooms();
        });

        grid.addEventListener('click', function (e) {
            var btn = e.target.closest('.numbered-space');
            if (!btn || btn.disabled) return;
            var numbers = btn.dataset.numbers.split(',').map(Number);
            var on = numbers.every(function (n) { return working.indexOf(n) !== -1; });
            working = working.filter(function (n) { return numbers.indexOf(n) === -1; });
            if (!on) working = working.concat(numbers);
            renderDialog();
            var again = grid.querySelector('[data-numbers="' + btn.dataset.numbers + '"]');
            if (again) again.focus();
        });

        dialog.querySelector('[data-numbered-apply]').addEventListener('click', function () {
            selection[current.table.id] = working.slice().sort(function (a, b) { return a - b; });
            var rid = current.rid;
            dialog.close();
            renderRoom(rid);
            syncForm();
        });
        dialog.querySelector('[data-numbered-cancel]').addEventListener('click', function () { dialog.close(); });
        dialog.addEventListener('close', function () { current = null; });

        renderRooms();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
