/**
 * Password visibility toggle (HLD N8).
 * Progressively enhances every <input type="password"> with an eye/eye-off
 * button so users can verify what they type. No markup changes required:
 * the script wraps the input in a relative container and overlays the button.
 */
(function () {
  'use strict';

  var EYE_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF_ICON =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function enhance(input) {
    if (input.dataset.passwordToggle === 'done') return;
    input.dataset.passwordToggle = 'done';

    var wrapper = document.createElement('div');
    wrapper.className = 'relative';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    var button = document.createElement('button');
    button.type = 'button';
    button.className =
      'absolute inset-y-0 right-0 flex items-center px-2 text-slate-400 hover:text-slate-600';
    button.setAttribute('aria-label', window.LR.t('Show password'));
    button.setAttribute('aria-pressed', 'false');
    button.innerHTML = EYE_ICON;

    button.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.innerHTML = show ? EYE_OFF_ICON : EYE_ICON;
      button.setAttribute('aria-label', show ? window.LR.t('Hide password') : window.LR.t('Show password'));
      button.setAttribute('aria-pressed', show ? 'true' : 'false');
    });

    wrapper.appendChild(button);

    // Reserve room for the button without clobbering existing classes.
    input.classList.add('pr-9');
  }

  function init() {
    document.querySelectorAll('input[type="password"]').forEach(enhance);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
