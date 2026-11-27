// Set the theme before the first stylesheet paints, including on protected API docs.
(() => {
  const key = 'orchpact.appearance';
  const attribute = 'data-op-theme';
  const change = 'orchpact:appearance';
  const root = document.documentElement;
  const explicit = value => value === 'light' || value === 'dark';

  function savedPreference() {
    try { return localStorage.getItem(key); }
    catch { return null; }
  }

  function apply(value) {
    if (explicit(value)) root.setAttribute(attribute, value);
    else root.removeAttribute(attribute);
  }
  apply(savedPreference());

  window.OrchpactAppearance = Object.freeze({
    current() { return root.getAttribute(attribute) || 'system'; },
    select(value) {
      apply(value);
      try {
        if (explicit(value)) localStorage.setItem(key, value);
        else localStorage.removeItem(key);
      } catch { /* This page still honors the user's selection. */ }
      window.dispatchEvent(new Event(change));
    },
    subscribe(listener) {
      window.addEventListener(change, listener);
      return () => window.removeEventListener(change, listener);
    },
  });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    apply(savedPreference());
    window.dispatchEvent(new Event(change));
  });
})();
