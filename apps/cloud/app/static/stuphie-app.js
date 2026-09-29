(() => {
  "use strict";

  const standalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;

  if (standalone) {
    document.documentElement.classList.add(
      "stuphie-standalone"
    );
  }

  document.documentElement.classList.add(
    "stuphie-mobile-capable"
  );

  /*
   * We deliberately do NOT cache staff pages, event details,
   * availability responses or authenticated data in V1.
   */
})();
