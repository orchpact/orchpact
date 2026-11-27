(() => {
  const root = document.documentElement;
  const themeInput = document.getElementById('theme');
  const themeQuery = window.matchMedia('(prefers-color-scheme: dark)');
  let preferred = 'system';
  try {
    const saved = localStorage.getItem('orchpact.brand-preview.theme');
    if (saved === 'light' || saved === 'dark') preferred = saved;
  } catch { /* The local viewer can operate without storage. */ }

  function applyTheme() {
    const dark = preferred === 'dark' || (preferred === 'system' && themeQuery.matches);
    root.dataset.opTheme = dark ? 'dark' : 'light';
  }

  applyTheme();
  themeInput.value = preferred;
  themeInput.closest('label').hidden = false;
  themeInput.addEventListener('change', () => {
    preferred = themeInput.value;
    applyTheme();
    try {
      if (preferred === 'system') localStorage.removeItem('orchpact.brand-preview.theme');
      else localStorage.setItem('orchpact.brand-preview.theme', preferred);
    } catch { /* Session-only preference. */ }
  });
  themeQuery.addEventListener('change', applyTheme);
})();
