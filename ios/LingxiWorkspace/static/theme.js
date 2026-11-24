(() => {
  'use strict';
  const storageKey = 'workspace-theme';
  const root = document.documentElement;
  const systemTheme = matchMedia('(prefers-color-scheme: dark)');
  const choices = [
    ['dark', '深色', '<path d="M20.9 13.3A9 9 0 0 1 10.7 3.1a9 9 0 1 0 10.2 10.2Z"/>'],
    ['system', '跟随系统', '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4m-4 0h8"/>'],
    ['light', '浅色', '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19m0-14-1.4 1.4M6.4 17.6 5 19"/>'],
  ];
  let preference;
  let switcher;
  let transitionTimer;

  function apply(value, animate = false) {
    preference = choices.some(([mode]) => mode === value) ? value : 'light';
    const dark = preference === 'dark' || (preference === 'system' && systemTheme.matches);
    const theme = dark ? 'dark' : 'light';
    if (animate && root.dataset.theme !== theme) {
      root.classList.add('workspace-theme-changing');
      clearTimeout(transitionTimer);
      transitionTimer = setTimeout(() => root.classList.remove('workspace-theme-changing'), 260);
    }
    root.dataset.theme = theme;
    if (switcher) switcher.querySelector(`input[value="${preference}"]`).checked = true;
  }

  apply(localStorage.getItem(storageKey));
  systemTheme.addEventListener('change', () => {
    if (preference === 'system') apply(preference, true);
  });
  window.addEventListener('storage', event => {
    if (event.key === storageKey) apply(event.newValue, true);
  });
  function mountSwitcher(host) {
    switcher = document.createElement('fieldset');
    switcher.className = 'workspace-theme';
    switcher.innerHTML = `<legend>界面主题</legend><div class="workspace-theme-options">
      ${choices.map(([mode, label, icon]) => `<label title="${label}">
        <input type="radio" name="workspace-theme" value="${mode}" aria-label="${label}">
        <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icon}</svg>
          <span aria-hidden="true">${mode === 'system' ? '系统' : label}</span>
        </span>
      </label>`).join('')}
    </div>`;
    switcher.querySelector(`input[value="${preference}"]`).checked = true;
    switcher.addEventListener('change', event => {
      apply(event.target.value, true);
      localStorage.setItem(storageKey, preference);
    });
    if (host.classList.contains('workspace-nav-footer')) host.prepend(switcher);
    else host.append(switcher);
  }
  window.mountWorkspaceTheme = mountSwitcher;
  document.addEventListener('DOMContentLoaded', () => {
    if (switcher) return;
    const host = document.querySelector('.workspace-nav-footer, .access-card');
    if (host) mountSwitcher(host);
  });
})();
