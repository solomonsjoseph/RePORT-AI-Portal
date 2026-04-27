/* =========================================================================
   RePORT AI Portal bridge.js
   Delegated click bridge: visual rail buttons → hidden Streamlit buttons.
   Installed once per page via st.iframe().
   ========================================================================= */
(function () {
    var pDoc;
    try {
        pDoc = window.parent.document;
    } catch (e) {
        return;
    }

    function installModelPillAdapter() {
        var version = '2026-04-23.model-pill.bound.1';
        if (pDoc._rplnModelPillAdapterVersion === version) {
            if (typeof pDoc._rplnSyncModelPill === 'function') {
                pDoc._rplnSyncModelPill();
            }
            return;
        }
        pDoc._rplnModelPillAdapterVersion = version;

        function findModelPopover() {
            var header = pDoc.querySelector('.rpln-model-menu-header');
            return header ? header.closest('[data-baseweb="popover"]') : null;
        }

        function isVisible(el) {
            if (!el) return false;
            var rect = el.getBoundingClientRect();
            var style = (pDoc.defaultView || window).getComputedStyle(el);
            return rect.width > 0 &&
                rect.height > 0 &&
                style.display !== 'none' &&
                style.visibility !== 'hidden';
        }

        function currentComposerShell() {
            var shells = Array.prototype.slice.call(
                pDoc.querySelectorAll('[class*="st-key-rpln_composer_shell"]')
            );
            for (var i = shells.length - 1; i >= 0; i--) {
                if (!shells[i].closest('[data-stale="true"]')) return shells[i];
            }
            return null;
        }

        function bindModelPillToComposer() {
            var shell = currentComposerShell();
            if (!shell) return;
            var slot = shell.querySelector('.rpln-composer-pill-slot[data-rpln-pill-slot="1"]');
            var host = shell.querySelector('[class*="st-key-rpln_model_pill_host"]');
            if (!slot || !host) return;

            var pill = host.querySelector('.st-key-rpln_composer_model');
            var trigger = pill && pill.querySelector('[data-testid="stPopoverButton"]');

            /* Backward compatibility for a page that still has the previous
               hoisted node until the next Streamlit rerun. Do not move it. */
            if (!pill) {
                trigger = slot.querySelector('[data-testid="stPopoverButton"]');
                if (trigger) {
                    var hoisted = trigger.closest('.st-key-rpln_composer_model');
                    pill = hoisted || null;
                }
            }
            if (!trigger) return;

            var slotRect = slot.getBoundingClientRect();
            var shellRect = shell.getBoundingClientRect();
            var triggerRect = trigger.getBoundingClientRect();
            var width = Math.ceil(triggerRect.width || 72);
            var height = Math.ceil(triggerRect.height || 22);

            pDoc.body.style.setProperty('--rpln-model-pill-w', width + 'px');
            slot.style.width = width + 'px';
            slot.style.minWidth = width + 'px';
            slot.style.height = height + 'px';

            if (host.contains(trigger)) {
                host.style.display = 'block';
                host.style.position = 'absolute';
                host.style.left = Math.round(slotRect.left - shellRect.left) + 'px';
                host.style.top = Math.round(slotRect.top - shellRect.top) + 'px';
                host.style.width = width + 'px';
                host.style.maxWidth = width + 'px';
                host.style.height = height + 'px';
                host.style.margin = '0';
                host.style.padding = '0';
                host.style.zIndex = '4';
                host.style.pointerEvents = 'auto';
            }
        }

        function positionModelPopover() {
            var trigger = pDoc.querySelector(
                '[class*="st-key-rpln_model_pill_host"] [data-testid="stPopoverButton"]'
            ) || pDoc.querySelector('.rpln-composer-pill-slot [data-testid="stPopoverButton"]');
            var pop = findModelPopover();
            if (!trigger || !pop) return;
            var body = pop.querySelector('[data-testid="stPopoverBody"]') || pop;
            var win = pDoc.defaultView || window;
            var triggerRect = trigger.getBoundingClientRect();
            var bodyRect = body.getBoundingClientRect();
            var menuWidth = bodyRect.width || 320;
            var menuHeight = bodyRect.height || 0;
            var gap = 10;
            var left = triggerRect.right - menuWidth;
            var top = triggerRect.top - menuHeight - gap;
            left = Math.max(8, Math.min(left, win.innerWidth - menuWidth - 8));
            top = Math.max(8, top);
            pop.style.position = 'fixed';
            pop.style.inset = 'auto';
            pop.style.left = left + 'px';
            pop.style.top = top + 'px';
            pop.style.right = 'auto';
            pop.style.bottom = 'auto';
            pop.style.transform = 'none';
            pop.style.zIndex = '1010';
        }

        function syncModelPill() {
            bindModelPillToComposer();
            positionModelPopover();
        }

        pDoc._rplnSyncModelPill = syncModelPill;
        syncModelPill();
        try {
            new MutationObserver(syncModelPill)
                .observe((pDoc || document).body, { childList: true, subtree: true });
        } catch (err) {}
        (pDoc.defaultView || window).addEventListener('resize', positionModelPopover);
        (pDoc.defaultView || window).addEventListener('scroll', positionModelPopover, true);
    }

    installModelPillAdapter();

    // Idempotent — only install handlers once, even across Streamlit reruns.
    if (pDoc._rplnMsgHandlers) return;
    pDoc._rplnMsgHandlers = true;

    /* ------ Click bridge: visual button → hidden st.button --------------- */
    function clickBridge(key) {
        var all = pDoc.querySelectorAll('[class*="st-key-' + key + '"]');
        for (var i = 0; i < all.length; i++) {
            var cls = all[i].className || "";
            if (
                cls.indexOf("st-key-" + key + " ") >= 0 ||
                cls.slice(-(key.length + 7)) === "st-key-" + key
            ) {
                var b = all[i].querySelector("button");
                if (b) {
                    // ★ React reentrancy fix: defer the synthesized click by one
                    // tick so it doesn't fire inside a trusted-click's capture
                    // flush (which React silently swallows).
                    setTimeout(function () { b.click(); }, 0);
                    return true;
                }
            }
        }
        return false;
    }

    function getComposerForm() {
        return pDoc.querySelector(
            '[data-testid="stForm"]:has(.st-key-rpln_composer_textarea)'
        );
    }

    function getComposerTextarea() {
        var form = getComposerForm();
        return form ? form.querySelector('textarea[aria-label="Message"]') : null;
    }

    function getComposerSubmitButton() {
        var form = getComposerForm();
        return form ? form.querySelector('[data-testid="stFormSubmitButton"] button') : null;
    }

    function dispatchComposerSubmit() {
        var fbtn = getComposerSubmitButton();
        if (!fbtn || fbtn.disabled) return false;
        setTimeout(function () {
            try {
                fbtn.dispatchEvent(new MouseEvent("click", {
                    bubbles: true, cancelable: true, view: pDoc.defaultView
                }));
            } catch (err) {
                fbtn.click();
            }
        }, 0);
        return true;
    }

    function syncComposerUi() {
        var form = getComposerForm();
        var ta = getComposerTextarea();
        var fbtn = getComposerSubmitButton();
        var shell = pDoc.querySelector('[class*="st-key-rpln_composer_shell"]');
        if (!form || !ta || !fbtn) return;

        var pending = !!pDoc.querySelector('.rpln-composer-streaming-sentinel');
        var hasText = !!(ta.value && ta.value.trim());
        fbtn.disabled = pending || !hasText;
        if (shell) {
            shell.setAttribute('data-rpln-dirty', hasText ? 'true' : 'false');
        }

        if (ta._rplnComposerBound) return;
        ta._rplnComposerBound = true;

        ta.addEventListener('input', function () {
            syncComposerUi();
        });

        ta.addEventListener('keydown', function (e) {
            if (e.isComposing) return;
            if (e.key === 'Enter' && !e.shiftKey) {
                if (!ta.value || !ta.value.trim()) return;
                e.preventDefault();
                dispatchComposerSubmit();
            }
        });
    }

    function getThreadShell() {
        return pDoc.querySelector('[class*="st-key-rpln_thread_shell"]');
    }

    function getJumpLatestButton() {
        return pDoc.querySelector('.rpln-jump-latest');
    }

    function isNearThreadBottom(root) {
        if (!root) return true;
        return (root.scrollHeight - root.clientHeight - root.scrollTop) < 80;
    }

    function syncJumpLatestButton() {
        var root = getThreadShell();
        var btn = getJumpLatestButton();
        if (!root || !btn) return;
        btn.classList.toggle('rpln-visible', !isNearThreadBottom(root));
    }

    function scrollThreadToLatest(smooth) {
        var root = getThreadShell();
        if (!root) return;
        var behavior = smooth ? 'smooth' : 'auto';
        if (typeof root.scrollTo === 'function') {
            root.scrollTo({ top: root.scrollHeight, behavior: behavior });
        } else {
            root.scrollTop = root.scrollHeight;
        }
        root.dataset.rplnPinnedToBottom = 'true';
        syncJumpLatestButton();
    }

    function bindThreadShell() {
        var root = getThreadShell();
        if (!root || root._rplnThreadBound) return;
        root._rplnThreadBound = true;
        root.dataset.rplnPinnedToBottom = 'true';
        root.addEventListener('scroll', function () {
            root.dataset.rplnPinnedToBottom = isNearThreadBottom(root) ? 'true' : 'false';
            syncJumpLatestButton();
        }, { passive: true });
        syncJumpLatestButton();
    }

    function maybeStickThreadToLatest() {
        var root = getThreadShell();
        if (!root) return;
        bindThreadShell();
        if (root.dataset.rplnPinnedToBottom !== 'false') {
            scrollThreadToLatest(false);
            return;
        }
        syncJumpLatestButton();
    }

    function handler(e) {
        var t = e.target.closest("[data-rpln-action]");
        if (!t) return;

        var action = t.getAttribute("data-rpln-action");
        var row = t.closest("[data-rpln-msg-idx]");
        var idx = row ? row.getAttribute("data-rpln-msg-idx") : null;

        // Sidebar conversation rows
        if (action === "switch-conv") {
            // WP-F.05.11c — clicks inside an active inline-rename editor must
            // not navigate away. closest() catches the contenteditable title.
            if (e.target.closest && e.target.closest('[contenteditable="plaintext-only"]')) {
                return;
            }
            var convId = t.getAttribute("data-rpln-conv-id");
            if (convId) {
                e.preventDefault();
                clickBridge("rpln_switch_" + convId);
            }
            return;
        }
        if (action === "delete-conv") {
            var did = t.getAttribute("data-rpln-conv-id");
            if (did) {
                e.preventDefault();
                e.stopPropagation();
                clickBridge("rpln_del_" + did);
            }
            return;
        }
        if (action === "pin-conv") {
            var pid = t.getAttribute("data-rpln-conv-id");
            if (pid) {
                e.preventDefault();
                e.stopPropagation();
                clickBridge("rpln_pin_" + pid);
            }
            return;
        }

        // v0-parity conversation row kebab (⋯) — toggles the row's menu panel.
        if (action === "toggle-conv-menu") {
            e.preventDefault();
            e.stopPropagation();
            var wrap = t.closest(".rpln-row-menu-wrap");
            if (!wrap) return;
            var wasOpen = wrap.classList.contains("open");
            // Close any other open menus first.
            var others = pDoc.querySelectorAll(".rpln-row-menu-wrap.open");
            for (var i = 0; i < others.length; i++) {
                others[i].classList.remove("open");
            }
            if (!wasOpen) wrap.classList.add("open");
            return;
        }

        // WP-F.05.11c — inline conversation rename. Activate contentEditable on
        // the matching .rpln-conv-title, focus + select, finish on Enter/blur,
        // cancel on Esc. On commit: payload "<cid>||<title>" → hidden
        // text_input via native setter + clickBridge(rpln_rename_apply).
        if (action === "rename-conv") {
            var rcid = t.getAttribute("data-rpln-conv-id");
            if (!rcid) return;
            e.preventDefault();
            e.stopPropagation();
            beginInlineRename(rcid);
            return;
        }

        // WP-F.05.11c — cancel the editing banner (clears composer + pending flag).
        if (action === "cancel-edit") {
            e.preventDefault();
            clickBridge("rpln_cancel_edit");
            return;
        }

        // Sidebar top rail
        if (action === "new-chat") {
            e.preventDefault();
            clickBridge("rpln_new_chat");
            return;
        }

        // 2026-04-22 — share-conversation handler removed (migrated to native
        // st.popover with download buttons in the topbar).

        // Legacy composer proxy — if present, forward its click to the real
        // form submit button.
        if (action === "submit-composer") {
            e.preventDefault();
            if (t.hasAttribute("disabled") || t.getAttribute("aria-disabled") === "true") {
                return;
            }
            dispatchComposerSubmit();
            return;
        }

        if (action === "copy-code") {
            e.preventDefault();
            var codeTarget = t.getAttribute("data-rpln-code-target");
            var codeEl = codeTarget ? pDoc.getElementById(codeTarget) : null;
            var codeText = codeEl ? (codeEl.value || codeEl.textContent || "") : "";
            if (!codeText) return;
            var clipboard = (pDoc.defaultView.navigator && pDoc.defaultView.navigator.clipboard) ||
                (navigator && navigator.clipboard);
            if (clipboard) {
                clipboard.writeText(codeText).then(function () {
                    flashIcon(t, "check");
                    toast("Code copied");
                }).catch(function () {});
            }
            return;
        }

        // WP-F.05.11b — search modal opens via sidebar "Search chats" click.
        if (action === "open-search") {
            e.preventDefault();
            clickBridge("rpln_open_search");
            return;
        }

        if (action === "jump-latest") {
            e.preventDefault();
            scrollThreadToLatest(true);
            return;
        }

        // WP-F.05.08 — responsive drawer toggle (<=840px viewports).
        if (action === "toggle-mobile-nav") {
            e.preventDefault();
            e.stopPropagation();
            var sb = pDoc.querySelector('[data-testid="stSidebar"]');
            var scrim = pDoc.querySelector("[data-rpln-mobile-scrim]");
            if (sb) sb.classList.toggle("rpln-mobile-open");
            if (scrim) scrim.classList.toggle("rpln-open");
            return;
        }

        // Profile dock popover — pure JS toggle, no Streamlit rerun on open/close.
        // Caret rotation: dock gets `.menu-open` in lockstep with popover `.open`.
        if (action === "toggle-profile-menu") {
            e.preventDefault();
            e.stopPropagation();
            var pop = pDoc.querySelector("[data-rpln-profile-popover]");
            var dock = pDoc.querySelector(".rpln-profile-dock");
            if (pop) pop.classList.toggle("open");
            if (dock) dock.classList.toggle("menu-open", pop && pop.classList.contains("open"));
            return;
        }
        if (action === "profile-settings") {
            e.preventDefault();
            var popS = pDoc.querySelector("[data-rpln-profile-popover]");
            var dockS = pDoc.querySelector(".rpln-profile-dock");
            if (popS) popS.classList.remove("open");
            if (dockS) dockS.classList.remove("menu-open");
            clickBridge("rpln_profile_settings_btn");
            return;
        }
        if (action === "profile-logout") {
            e.preventDefault();
            var popL = pDoc.querySelector("[data-rpln-profile-popover]");
            var dockL = pDoc.querySelector(".rpln-profile-dock");
            if (popL) popL.classList.remove("open");
            if (dockL) dockL.classList.remove("menu-open");
            clickBridge("rpln_profile_logout_btn");
            return;
        }

        // 2026-04-22 — open-tweaks / close-tweaks removed. Knobs live inside
        // Settings → General tab now (see web_ui.py `_build_appearance_section_html`).

        var TWEAK_KEY = "report_ai_portal_appearance_v1";
        var TWEAK_FIELDS = {
            "set-theme": "theme",
            "set-accent": "accent",
            "set-bubble": "bubble",
            "set-aprose": "aprose",
            "set-density": "density",
        };
        // Reference v1.html:1816: theme click also resets accent to that
        // theme's default. Keeps accent coherent until user tweaks it.
        var THEME_ACCENT = {
            terracotta: "C96442", graphite: "A8A29E", midnight: "7AA2F7",
            forest: "8AA878", plum: "B69AD6", rose: "D98A9F",
            sand: "D9B77A", ocean: "6EC2C8",
        };
        if (TWEAK_FIELDS[action]) {
            e.preventDefault();
            var field = TWEAK_FIELDS[action];
            var val = t.getAttribute("data-rpln-val") || "";
            if (!val) return;
            var writes = {};
            writes[field] = val;
            if (field === "theme" && THEME_ACCENT[val]) {
                writes.accent = THEME_ACCENT[val];
            }
            Object.keys(writes).forEach(function (k) {
                pDoc.body.setAttribute("data-" + k, writes[k]);
            });
            try {
                var cur = {};
                var raw = localStorage.getItem(TWEAK_KEY);
                if (raw) { try { cur = JSON.parse(raw) || {}; } catch (err2) {} }
                Object.keys(writes).forEach(function (k) { cur[k] = writes[k]; });
                localStorage.setItem(TWEAK_KEY, JSON.stringify(cur));
            } catch (err) {}
            // Reflect [aria-pressed] on peer buttons (visible active state)
            // for every field we wrote. Pure-JS path — no Streamlit rerun.
            Object.keys(writes).forEach(function (k) {
                var peers = pDoc.querySelectorAll(
                    "[data-rpln-action='set-" + k + "']"
                );
                for (var p = 0; p < peers.length; p++) {
                    peers[p].setAttribute(
                        "aria-pressed",
                        peers[p].getAttribute("data-rpln-val") === writes[k] ? "true" : "false"
                    );
                }
            });
            return;
        }

        if (action === "toggle-group") {
            e.preventDefault();
            var group = t.closest(".rpln-group");
            if (!group) return;
            var name = group.getAttribute("data-group");
            var open = group.getAttribute("data-open") === "1" ? "0" : "1";
            group.setAttribute("data-open", open);
            try {
                sessionStorage.setItem("rpln-group-" + name, open);
            } catch (err) {}
            return;
        }

        // Per-message actions
        if (idx == null) return;

        if (action === "retry-user") {
            e.preventDefault();
            clickBridge("rpln_retry_user_" + idx);
        } else if (action === "edit-user") {
            e.preventDefault();
            clickBridge("rpln_edit_user_" + idx);
        } else if (action === "copy-user") {
            e.preventDefault();
            var bubble = row.querySelector(".rpln-user-bubble");
            var text = bubble ? bubble.innerText : "";
            navigator.clipboard.writeText(text).then(function () {
                flashIcon(t, "check");
                toast("Copied to clipboard");
            });
        } else if (action === "copy-assistant") {
            e.preventDefault();
            var prose = row.querySelector(".rpln-assistant-prose");
            var text = prose ? prose.innerText : "";
            navigator.clipboard.writeText(text).then(function () {
                flashIcon(t, "check");
                toast("Copied to clipboard");
            });
        } else if (action === "regen-assistant") {
            e.preventDefault();
            clickBridge("rpln_regen_assistant_" + idx);
        } else if (action === "feedback-up") {
            e.preventDefault();
            clickBridge("rpln_fb_up_" + idx);
            toast("Thanks for the feedback");
        } else if (action === "feedback-down") {
            e.preventDefault();
            clickBridge("rpln_fb_down_" + idx);
            toast("Noted");
        }
    }

    function flashIcon(btn, symbol) {
        var icon = btn.querySelector(".material-symbols-rounded");
        if (!icon) return;
        var orig = icon.textContent;
        icon.textContent = symbol;
        btn.classList.add("rpln-ok");
        setTimeout(function () {
            icon.textContent = orig;
            btn.classList.remove("rpln-ok");
        }, 1200);
    }

    /* WP-F.05.11b — toast helper. Matches reference HTML:1854-1862.
       Slides a pill up from bottom-center, auto-dismisses in 1800 ms.
       Uses DOM methods (no innerHTML) to stay XSS-safe even if callers
       pass untrusted copy. */
    function toast(msg) {
        var host = pDoc.querySelector(".rpln-toast-host");
        if (!host) {
            host = pDoc.createElement("div");
            host.className = "rpln-toast-host";
            pDoc.body.appendChild(host);
        }
        var t = pDoc.createElement("div");
        t.className = "rpln-toast";
        var icon = pDoc.createElement("span");
        icon.className = "material-symbols-rounded";
        icon.textContent = "check_circle";
        var label = pDoc.createElement("span");
        label.textContent = msg || "";
        t.appendChild(icon);
        t.appendChild(label);
        host.appendChild(t);
        requestAnimationFrame(function () { t.classList.add("in"); });
        setTimeout(function () {
            t.classList.remove("in");
            setTimeout(function () { t.remove(); }, 220);
        }, 1800);
    }
    try { pDoc.defaultView.rplnToast = toast; } catch (err) {}

    /* WP-F.05.11c — set a Streamlit text_input's value from JS so the server
       picks it up on the next rerun. Streamlit's text_input only syncs to
       session_state on blur/Enter, not on input — so after the native-setter
       + input event (which React controlled components require), we also
       dispatch blur so the value commits to the backend before we click
       the apply button. */
    function setStInputValue(key, value) {
        var wrap = pDoc.querySelector('.st-key-' + key);
        if (!wrap) return false;
        var el = wrap.querySelector('input, textarea');
        if (!el) return false;
        var Proto = (el.tagName === 'TEXTAREA')
            ? pDoc.defaultView.HTMLTextAreaElement.prototype
            : pDoc.defaultView.HTMLInputElement.prototype;
        var setter = Object.getOwnPropertyDescriptor(Proto, 'value').set;
        setter.call(el, value == null ? '' : String(value));
        el.dispatchEvent(new Event('input', { bubbles: true }));
        // Focus-then-blur forces Streamlit's BaseWidget to commit.
        try { el.focus(); } catch (err) {}
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        try { el.blur(); } catch (err) {}
        return true;
    }

    /* WP-F.05.11c — inline rename: contentEditable + commit bridge. */
    function beginInlineRename(cid) {
        var title = pDoc.querySelector(
            '.rpln-conv-title[data-rpln-conv-id="' + cid + '"]'
        );
        if (!title || title.getAttribute('contenteditable') === 'plaintext-only') return;
        var original = title.textContent;
        title.setAttribute('contenteditable', 'plaintext-only');
        title.classList.add('rpln-rename-editing');
        title.focus();
        try {
            var range = pDoc.createRange();
            range.selectNodeContents(title);
            var sel = pDoc.defaultView.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        } catch (err) {}
        var cancelled = false;
        function finish() {
            title.removeEventListener('blur', onBlur);
            title.removeEventListener('keydown', onKey);
            title.setAttribute('contenteditable', 'false');
            title.classList.remove('rpln-rename-editing');
            var next = (title.textContent || '').trim();
            if (cancelled || !next || next === original.trim()) {
                title.textContent = original;
                return;
            }
            // Commit: push "<cid>||<title>" through hidden text_input, click
            // the form's submit button. st.form syncs ALL its widgets atomically
            // on submit, so the payload hits session_state in the same rerun
            // as the submit-button callback.
            if (setStInputValue('rpln_rename_payload', cid + '||' + next)) {
                setTimeout(function () {
                    clickBridge('FormSubmitter-rpln_rename_form-rename-apply');
                }, 80);
            } else {
                title.textContent = original;
            }
        }
        function onBlur() { finish(); }
        function onKey(ev) {
            if (ev.key === 'Enter') { ev.preventDefault(); title.blur(); return; }
            if (ev.key === 'Escape' || ev.key === 'Esc') {
                ev.preventDefault();
                cancelled = true;
                title.textContent = original;
                title.blur();
            }
        }
        title.addEventListener('blur', onBlur, { once: true });
        title.addEventListener('keydown', onKey);
    }

    pDoc.body.addEventListener("click", handler, true);

    /* ------ Outside-click closer for profile popover + tweaks panel ----- */
    pDoc.addEventListener("click", function (e) {
        var t = e.target;
        var pop = pDoc.querySelector("[data-rpln-profile-popover].open");
        if (pop && !(t.closest && (
            t.closest("[data-rpln-profile-popover]") ||
            t.closest('[data-rpln-action="toggle-profile-menu"]')
        ))) {
            pop.classList.remove("open");
        }
        // WP-F.05.08 — scrim click closes the mobile drawer.
        if (t.closest && t.closest("[data-rpln-mobile-scrim].rpln-open")) {
            var sbC = pDoc.querySelector('[data-testid="stSidebar"].rpln-mobile-open');
            var scrimC = pDoc.querySelector("[data-rpln-mobile-scrim].rpln-open");
            if (sbC) sbC.classList.remove("rpln-mobile-open");
            if (scrimC) scrimC.classList.remove("rpln-open");
        }
        // v0-parity: close any open conversation-row kebab menu on outside click.
        var openRowMenus = pDoc.querySelectorAll(".rpln-row-menu-wrap.open");
        if (openRowMenus.length && !(t.closest && t.closest(".rpln-row-menu-wrap.open"))) {
            for (var j = 0; j < openRowMenus.length; j++) {
                openRowMenus[j].classList.remove("open");
            }
        }
    });

    /* ------ Restore sidebar group open/closed from sessionStorage -------- */
    function restoreGroups() {
        var groups = pDoc.querySelectorAll(".rpln-group[data-group]");
        groups.forEach(function (g) {
            var n = g.getAttribute("data-group");
            try {
                var v = sessionStorage.getItem("rpln-group-" + n);
                if (v === "0" || v === "1") g.setAttribute("data-open", v);
            } catch (err) {}
        });
        try {
            ["pinned", "recents"].forEach(function (n) {
                localStorage.removeItem("rpln-group-" + n);
            });
        } catch (err) {}
    }

    /* ------ Re-apply the 5 appearance attrs if <body> reflows ------------ */
    // Belt-and-braces with _inject_redesign_css()'s hydration script. If
    // Streamlit reruns and drops attrs before paint, we catch it here.
    // Reflects [aria-pressed] off body attrs (the source of truth after
    // hydration) so unpersisted defaults still show as active.
    function restoreAppearance() {
        try {
            var raw = localStorage.getItem("report_ai_portal_appearance_v1");
            var a = {};
            if (raw) { try { a = JSON.parse(raw) || {}; } catch (e) {} }
            var FIELDS = ["theme", "bubble", "aprose", "density", "accent"];
            FIELDS.forEach(function (k) {
                if (a[k] && pDoc.body.getAttribute("data-" + k) !== a[k]) {
                    pDoc.body.setAttribute("data-" + k, a[k]);
                }
                var cur = pDoc.body.getAttribute("data-" + k);
                var peers = pDoc.querySelectorAll(
                    "[data-rpln-action='set-" + k + "']"
                );
                for (var p = 0; p < peers.length; p++) {
                    peers[p].setAttribute(
                        "aria-pressed",
                        peers[p].getAttribute("data-rpln-val") === cur ? "true" : "false"
                    );
                }
            });
        } catch (err) {}
    }

    /* ------ Suppress password-manager helper chrome on API-key fields ----- */
    function suppressPasswordManagerHints() {
        var fields = pDoc.querySelectorAll('input[type="password"]');
        for (var i = 0; i < fields.length; i++) {
            var field = fields[i];
            field.setAttribute("autocomplete", "new-password");
            field.setAttribute("autocapitalize", "off");
            field.setAttribute("autocorrect", "off");
            field.setAttribute("spellcheck", "false");
            field.setAttribute("data-1p-ignore", "true");
            field.setAttribute("data-lpignore", "true");
            field.setAttribute("data-form-type", "other");
        }
    }

    restoreGroups();
    restoreAppearance();
    suppressPasswordManagerHints();
    var mo = new MutationObserver(function () {
        restoreGroups();
        restoreAppearance();
        suppressPasswordManagerHints();
        bindThreadShell();
        maybeStickThreadToLatest();
        syncSidebarCollapsedAttr();
        syncComposerUi();
    });
    mo.observe(pDoc.body, { childList: true, subtree: true });

    /* ------ Sidebar-collapsed body attribute (Safari <15.4 `:has()` fallback) */
    function syncSidebarCollapsedAttr() {
        var sb = pDoc.querySelector('[data-testid="stSidebar"]');
        var collapsed = !!(sb && sb.getAttribute("aria-expanded") === "false");
        if (collapsed) {
            pDoc.body.setAttribute("data-rpln-sidebar-collapsed", "true");
        } else {
            pDoc.body.removeAttribute("data-rpln-sidebar-collapsed");
        }
    }
    syncSidebarCollapsedAttr();
    bindThreadShell();
    maybeStickThreadToLatest();
    syncComposerUi();

    /* ------ Esc-key closers ---------------------------------------------- */
    /* 2026-04-22 — Cmd+K and Cmd+Shift+, removed per
       `feedback_no_keyboard_shortcuts.md`. Esc kept as a baseline close
       affordance for the profile popover and the mobile drawer. */
    pDoc.addEventListener("keydown", function (e) {
        var key = e.key || "";
        if (key === "Escape" || key === "Esc") {
            var pop = pDoc.querySelector("[data-rpln-profile-popover].open");
            if (pop) {
                pop.classList.remove("open");
                var dockE = pDoc.querySelector(".rpln-profile-dock.menu-open");
                if (dockE) dockE.classList.remove("menu-open");
                e.preventDefault();
                return;
            }
            var sbE = pDoc.querySelector('[data-testid="stSidebar"].rpln-mobile-open');
            if (sbE) {
                sbE.classList.remove("rpln-mobile-open");
                var scE = pDoc.querySelector("[data-rpln-mobile-scrim].rpln-open");
                if (scE) scE.classList.remove("rpln-open");
                e.preventDefault();
                return;
            }
        }
    });
})();
