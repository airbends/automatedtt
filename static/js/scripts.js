(function () {
    "use strict";

    function normalizeFormControls() {
        const forms = document.querySelectorAll("form");
        forms.forEach((form) => {
            const controls = form.querySelectorAll("input, select, textarea");
            controls.forEach((control) => {
                if (control.tagName === "SELECT") {
                    if (!control.classList.contains("form-select")) {
                        control.classList.add("form-select");
                    }
                    return;
                }

                if (control.tagName === "TEXTAREA") {
                    if (!control.classList.contains("form-control")) {
                        control.classList.add("form-control");
                    }
                    return;
                }

                const type = (control.getAttribute("type") || "text").toLowerCase();
                if (["checkbox", "radio"].includes(type)) {
                    if (!control.classList.contains("form-check-input")) {
                        control.classList.add("form-check-input");
                    }
                    return;
                }

                if (["hidden", "submit", "button", "reset", "file"].includes(type)) {
                    return;
                }

                if (!control.classList.contains("form-control")) {
                    control.classList.add("form-control");
                }
            });
        });
    }

    function styleStandaloneForms() {
        const forms = document.querySelectorAll("form");
        forms.forEach((form) => {
            if (form.classList.contains("d-inline")) return;
            if (form.classList.contains("app-form")) return;
            if (form.closest(".card")) return;
            if (form.closest(".table-responsive")) return;
            if (form.querySelectorAll("input, select, textarea").length < 2) return;
            form.classList.add("app-form");
        });
    }

    function highlightCurrentNavLink() {
        const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
        const links = document.querySelectorAll(".navbar .nav-link[href]");
        links.forEach((link) => {
            const href = link.getAttribute("href");
            if (!href || href.startsWith("#")) return;
            const path = new URL(href, window.location.origin).pathname.replace(/\/+$/, "") || "/";
            if (currentPath === path || (path !== "/" && currentPath.startsWith(path + "/"))) {
                link.classList.add("is-active");
            }
        });
    }

    function setupRevealAnimation() {
        const revealTargets = Array.from(document.querySelectorAll(
            ".card, .table-responsive, .alert, .app-form, section.bg-primary, section.bg-light"
        ));
        if (!revealTargets.length) return;

        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reducedMotion || typeof IntersectionObserver === "undefined") {
            revealTargets.forEach((el) => el.classList.add("reveal-in"));
            return;
        }

        revealTargets.forEach((el, idx) => {
            el.classList.add("reveal-ready");
            el.style.transitionDelay = `${Math.min(idx * 40, 280)}ms`;
        });

        const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                entry.target.classList.remove("reveal-ready");
                entry.target.classList.add("reveal-in");
                obs.unobserve(entry.target);
            });
        }, { threshold: 0.12 });

        revealTargets.forEach((el) => observer.observe(el));
    }

    function addInputStateFeedback() {
        const controls = document.querySelectorAll("input, select, textarea");
        controls.forEach((control) => {
            const syncState = () => {
                if (control.value && String(control.value).trim() !== "") {
                    control.classList.add("has-value");
                } else {
                    control.classList.remove("has-value");
                }
            };
            control.addEventListener("input", syncState);
            control.addEventListener("change", syncState);
            syncState();
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        normalizeFormControls();
        styleStandaloneForms();
        highlightCurrentNavLink();
        setupRevealAnimation();
        addInputStateFeedback();
    });
})();
