// Install the staff app from the browser (no APK) and register for call notifications.
(function () {
  var deferredPrompt = null;
  var isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;

  window.staffSW = ('serviceWorker' in navigator)
    ? navigator.serviceWorker.register('/staff/sw.js', { scope: '/staff/' }).catch(function () { return null; })
    : Promise.resolve(null);

  function installButtons() { return document.querySelectorAll('[data-install-app]'); }

  function refreshButtons() {
    installButtons().forEach(function (b) { b.style.display = isStandalone ? 'none' : ''; });
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
  });
  window.addEventListener('appinstalled', function () {
    isStandalone = true;
    deferredPrompt = null;
    refreshButtons();
  });

  window.installStaffApp = function () {
    if (deferredPrompt) {
      deferredPrompt.prompt();
      deferredPrompt.userChoice.finally(function () { deferredPrompt = null; });
      return;
    }
    var ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    alert(ios
      ? 'على الآيفون: افتح الصفحة من Safari، اكبس زر المشاركة ⬆️ ثم "إضافة إلى الشاشة الرئيسية".'
      : 'من Chrome: اكبس ⋮ فوق ثم "تثبيت التطبيق" أو "إضافة إلى الشاشة الرئيسية".');
  };

  function urlBase64ToUint8Array(s) {
    var padding = '='.repeat((4 - s.length % 4) % 4);
    var raw = atob((s + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }

  // Ask for notification permission and save this phone's push subscription.
  // Resolves true when the phone will get call notifications in the background.
  window.subscribeStaffPush = function (vapidKey) {
    if (!vapidKey || !('PushManager' in window) || !('Notification' in window)) return Promise.resolve(false);
    return Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') return false;
      return window.staffSW.then(function () { return navigator.serviceWorker.ready; }).then(function (reg) {
        return reg.pushManager.getSubscription().then(function (sub) {
          return sub || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(vapidKey) });
        });
      }).then(function (sub) {
        return fetch('/api/staff/push-subscribe/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(sub.toJSON()),
        }).then(function (r) { return r.ok; });
      });
    }).catch(function () { return false; });
  };

  // Resolves true when this phone already gets call notifications.
  window.staffPushEnabled = function () {
    if (!('PushManager' in window) || !('Notification' in window) || Notification.permission !== 'granted') return Promise.resolve(false);
    return window.staffSW.then(function () { return navigator.serviceWorker.ready; })
      .then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (sub) { return !!sub; })
      .catch(function () { return false; });
  };

  // Stops call notifications on this phone.
  window.unsubscribeStaffPush = function () {
    if (!('PushManager' in window)) return Promise.resolve(true);
    return window.staffSW.then(function () { return navigator.serviceWorker.ready; })
      .then(function (reg) { return reg.pushManager.getSubscription(); })
      .then(function (sub) {
        if (!sub) return true;
        var endpoint = sub.endpoint;
        return sub.unsubscribe().then(function () {
          return fetch('/api/staff/push-unsubscribe/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ endpoint: endpoint }),
          }).then(function () { return true; });
        });
      }).catch(function () { return false; });
  };

  document.addEventListener('DOMContentLoaded', refreshButtons);
})();
