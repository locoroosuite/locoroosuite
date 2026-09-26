/*
 * PWA install promotion & post-install notifications onboarding (U24.23 - U24.26, U24.28).
 *
 * Included from layout.html for customer sessions only. The native
 * beforeinstallprompt event is captured as early as possible by a head shim
 * (layout.html) into window.__lrBeforeInstallPrompt; this file consumes it:
 *
 *   - Install banner (#pwa-install-banner): mobile + not installed + not
 *     dismissed. Install button on browsers with a captured prompt; iOS
 *     instructions otherwise. Dismissal is permanent (localStorage).
 *   - Settings install entry (#pwa-install-settings, U24.24): revealed on the
 *     settings page when the app is not installed yet, regardless of banner
 *     dismissal.
 *   - Notifications onboarding (#pwa-notif-prompt, U24.25/U24.28): shown in
 *     standalone mode or on mobile browsers whenever notifications are
 *     supported and not decided yet. Dismissal (✕) hides it for 7 days;
 *     "Don't ask again" opts out permanently. Accepting requests permission
 *     and subscribes via the existing /app/mail/push/* endpoints
 *     (U24.16/U24.22).
 *
 * Exposes window.LR.pwa helpers so settings.html reuses the same subscribe
 * flow instead of duplicating it.
 */
(function () {
  'use strict';

  var LS_BANNER_DISMISSED = 'lr-install-banner-dismissed';
  var LS_NOTIF_NEVER = 'lr-notif-never-ask';
  var LS_NOTIF_DISMISSED_AT = 'lr-notif-dismissed-at';
  var NOTIF_REASK_MS = 7 * 24 * 60 * 60 * 1000;

  var BANNER_INSTALL_TEXT = window.LR.t('Add the app to your home screen for a faster, full-screen experience.');
  var BANNER_IOS_TEXT = window.LR.t("Install the app: tap the Share button, then choose 'Add to Home Screen'.");
  var BANNER_FALLBACK_TEXT = window.LR.t("You can install it from your browser's menu (\u22EE \u2192 Install app).");
  var SETTINGS_IOS_HINT = window.LR.t('Add it via Share \u2192 Add to Home Screen to use the app full screen and get notifications.');

  function lsGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_e) {
      return null;
    }
  }

  function lsSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_e) {
      // Private browsing: in-memory dismissal only (dataset flag on the banner).
    }
  }

  function lsRemove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (_e) {
      // Private browsing: best effort.
    }
  }

  function isStandalone() {
    return (
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
      window.navigator.standalone === true
    );
  }

  function isMobileBrowser() {
    var ua = navigator.userAgent || '';
    if (/android|iphone|ipod|ipad/i.test(ua)) return true;
    // iPadOS 13+ masquerades as desktop Safari but is multi-touch.
    if (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1) return true;
    return false;
  }

  function isIOS() {
    var ua = navigator.userAgent || '';
    if (/iphone|ipod|ipad/i.test(ua)) return true;
    if (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1) return true;
    return false;
  }

  function getDeferredPrompt() {
    return window.__lrBeforeInstallPrompt || null;
  }

  function promptInstall() {
    var ev = getDeferredPrompt();
    if (!ev) return Promise.resolve('unavailable');
    return ev
      .prompt()
      .then(function () {
        return ev.userChoice.then(function (choice) {
          return choice && choice.outcome === 'accepted' ? 'accepted' : 'dismissed';
        });
      })
      .catch(function (_err) {
        // Chrome refuses back-to-back prompts; guide the user to the menu.
        return 'unavailable';
      });
  }

  function urlBase64ToBytes(base64) {
    var padding = '='.repeat((4 - (base64.length % 4)) % 4);
    var b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/');
    var raw = window.atob(b64);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function subscribePush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      return Promise.reject(new Error(window.LR.t('This browser does not support push notifications.')));
    }
    return fetch('/app/mail/push/key', { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) {
          throw new Error(window.LR.t('Push notifications are not configured on the server. Please contact your administrator.'));
        }
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.public_key) {
          throw new Error(window.LR.t('Push notifications are not configured on the server.'));
        }
        return navigator.serviceWorker.ready.then(function (reg) {
          return reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToBytes(data.public_key)
          });
        });
      })
      .then(function (sub) {
        return fetch('/app/mail/push/subscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(sub.toJSON())
        }).then(function (r) {
          if (!r.ok) throw new Error(window.LR.t('Could not save the subscription. Please retry.'));
        });
      });
  }

  // --- Install banner (U24.23) ---

  function updateBanner() {
    var banner = document.getElementById('pwa-install-banner');
    if (!banner || banner.dataset.dismissed === '1') return;
    if (isStandalone()) {
      banner.classList.add('hidden');
      return;
    }
    if (!isMobileBrowser()) return;
    if (lsGet(LS_BANNER_DISMISSED) === '1') return;
    var ios = isIOS();
    var deferred = getDeferredPrompt();
    if (!ios && !deferred) return;
    var textEl = document.getElementById('pwa-install-banner-text');
    var accept = document.getElementById('pwa-install-banner-accept');
    var actions = document.getElementById('pwa-install-banner-actions');
    if (ios && !deferred) {
      textEl.textContent = BANNER_IOS_TEXT;
      accept.classList.add('hidden');
      if (actions) actions.classList.add('hidden');
    } else {
      textEl.textContent = BANNER_INSTALL_TEXT;
      accept.classList.remove('hidden');
      if (actions) actions.classList.remove('hidden');
    }
    banner.classList.remove('hidden');
  }

  function wireBanner() {
    var banner = document.getElementById('pwa-install-banner');
    if (!banner) return;
    var accept = document.getElementById('pwa-install-banner-accept');
    var dismiss = document.getElementById('pwa-install-banner-dismiss');
    var textEl = document.getElementById('pwa-install-banner-text');

    if (accept) {
      accept.addEventListener('click', function () {
        accept.disabled = true;
        promptInstall().then(function (outcome) {
          if (outcome === 'accepted') {
            banner.classList.add('hidden');
          } else if (outcome === 'unavailable') {
            textEl.textContent = BANNER_FALLBACK_TEXT;
            accept.classList.add('hidden');
            var actions = document.getElementById('pwa-install-banner-actions');
            if (actions) actions.classList.add('hidden');
          }
          // 'dismissed': keep the banner; the user may try again later.
        }).catch(function (_err) {
          accept.disabled = false;
        });
      });
    }

    if (dismiss) {
      dismiss.addEventListener('click', function () {
        banner.dataset.dismissed = '1';
        banner.classList.add('hidden');
        lsSet(LS_BANNER_DISMISSED, '1');
      });
    }
  }

  // --- Settings install entry (U24.24) ---

  function updateSettingsEntry() {
    var box = document.getElementById('pwa-install-settings');
    if (!box) return;
    if (isStandalone()) return;
    if (!isMobileBrowser()) return;
    var ios = isIOS();
    var deferred = getDeferredPrompt();
    var btn = document.getElementById('pwa-install-settings-btn');
    var iosList = document.getElementById('pwa-install-settings-ios');
    var hint = document.getElementById('pwa-install-settings-hint');
    if (ios && !deferred) {
      if (hint) hint.textContent = SETTINGS_IOS_HINT;
      iosList.classList.remove('hidden');
      btn.classList.add('hidden');
      box.classList.remove('hidden');
      return;
    }
    if (deferred) {
      iosList.classList.add('hidden');
      btn.classList.remove('hidden');
      box.classList.remove('hidden');
    }
    // Android without a captured prompt yet: nothing actionable, stay hidden
    // until 'lr:install-available' arrives (see listener below).
  }

  function wireSettingsEntry() {
    var btn = document.getElementById('pwa-install-settings-btn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      btn.disabled = true;
      promptInstall().then(function (outcome) {
        if (outcome === 'accepted') {
          var box = document.getElementById('pwa-install-settings');
          if (box) box.classList.add('hidden');
        }
      }).catch(function (_err) {
        btn.disabled = false;
      });
    });
  }

  // --- Notifications onboarding after install (U24.25, U24.28) ---

  function notifDismissedRecently() {
    var raw = lsGet(LS_NOTIF_DISMISSED_AT);
    if (!raw) return false;
    var at = parseInt(raw, 10);
    if (!isFinite(at)) return false;
    return Date.now() - at < NOTIF_REASK_MS;
  }

  function runNotifOnboarding() {
    var prompt = document.getElementById('pwa-notif-prompt');
    if (!prompt) return;
    // U24.28: prompt in standalone mode and in mobile browsers — anyone who
    // installed the PWA (or is a phone user) gets a clear invitation.
    if (!isStandalone() && !isMobileBrowser()) return;
    if (lsGet(LS_NOTIF_NEVER) === '1') return;
    if (!('Notification' in window)) return;
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

    var finish = function (subscribed, err) {
      prompt.classList.add('hidden');
      lsRemove(LS_NOTIF_DISMISSED_AT);
      if (subscribed && window.LR && window.LR.notifySuccess) {
        window.LR.notifySuccess(window.LR.t('Notifications enabled for this device.'));
      } else if (err && window.LR && window.LR.notifyError) {
        window.LR.notifyError(err);
      }
    };

    if (Notification.permission === 'denied') {
      // Hard-denied: never re-prompt. Settings keeps remediation guidance.
      lsSet(LS_NOTIF_NEVER, '1');
      return;
    }
    if (Notification.permission === 'granted') {
      // Already permitted: silently ensure a subscription exists.
      subscribePush()
        .then(function () {
          lsRemove(LS_NOTIF_DISMISSED_AT);
        })
        .catch(function (_err) {
          // Settings -> Notifications remains the manual path.
        });
      return;
    }
    if (notifDismissedRecently()) return;

    prompt.classList.remove('hidden');
    var accept = document.getElementById('pwa-notif-accept');
    var dismiss = document.getElementById('pwa-notif-dismiss');
    var never = document.getElementById('pwa-notif-never');

    if (accept) {
      accept.addEventListener('click', function () {
        accept.disabled = true;
        Notification.requestPermission()
          .then(function (permission) {
            if (permission !== 'granted') {
              finish(false, null);
              return;
            }
            return subscribePush().then(
              function () {
                finish(true, null);
              },
              function (err) {
                finish(false, (err && err.message) || window.LR.t('Could not turn on notifications.'));
              }
            );
          })
          .catch(function () {
            finish(false, window.LR.t('Could not turn on notifications.'));
          });
      });
    }
    if (dismiss) {
      dismiss.addEventListener('click', function () {
        // Soft dismissal: re-ask after 7 days (U24.28).
        prompt.classList.add('hidden');
        lsSet(LS_NOTIF_DISMISSED_AT, String(Date.now()));
      });
    }
    if (never) {
      never.addEventListener('click', function () {
        prompt.classList.add('hidden');
        lsSet(LS_NOTIF_NEVER, '1');
      });
    }
  }

  // --- Boot ---

  window.LR = window.LR || {};
  window.LR.pwa = {
    isStandalone: isStandalone,
    isMobileBrowser: isMobileBrowser,
    isIOS: isIOS,
    promptInstall: promptInstall,
    subscribePush: subscribePush
  };

  updateBanner();
  wireBanner();
  updateSettingsEntry();
  wireSettingsEntry();
  runNotifOnboarding();

  window.addEventListener('lr:install-available', function () {
    updateBanner();
    updateSettingsEntry();
  });

  window.addEventListener('appinstalled', function () {
    var banner = document.getElementById('pwa-install-banner');
    if (banner) banner.classList.add('hidden');
    var box = document.getElementById('pwa-install-settings');
    if (box) box.classList.add('hidden');
  });
})();
