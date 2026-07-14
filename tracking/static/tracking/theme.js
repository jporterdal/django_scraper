(function () {
  'use strict';

  var STORAGE_KEY = 'pricing-tracker-theme';
  var EVENT_NAME = 'pricing-tracker-themechange';

  function getTheme() {
    var attr = document.documentElement.getAttribute('data-bs-theme');
    return attr === 'light' ? 'light' : 'dark';
  }

  function updateToggle(theme) {
    var btn = document.getElementById('theme-toggle');
    if (!btn) {
      return;
    }
    if (theme === 'dark') {
      btn.textContent = '\u2600';
      btn.setAttribute('aria-label', 'Switch to light mode');
    } else {
      btn.textContent = '\u263E';
      btn.setAttribute('aria-label', 'Switch to dark mode');
    }
  }

  function setTheme(theme) {
    if (theme !== 'light' && theme !== 'dark') {
      theme = 'dark';
    }
    document.documentElement.setAttribute('data-bs-theme', theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) {
      /* private mode / blocked storage */
    }
    updateToggle(theme);
    document.documentElement.dispatchEvent(
      new CustomEvent(EVENT_NAME, { detail: { theme: theme } })
    );
  }

  function init() {
    updateToggle(getTheme());
    var btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        setTheme(getTheme() === 'dark' ? 'light' : 'dark');
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.addEventListener('storage', function (event) {
    if (event.key !== STORAGE_KEY) {
      return;
    }
    var theme = event.newValue === 'light' || event.newValue === 'dark' ? event.newValue : 'dark';
    document.documentElement.setAttribute('data-bs-theme', theme);
    updateToggle(theme);
    document.documentElement.dispatchEvent(
      new CustomEvent(EVENT_NAME, { detail: { theme: theme } })
    );
  });
})();
