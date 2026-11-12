/* Makes overlay.tips installable as an app (Add to Home Screen). */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () { navigator.serviceWorker.register('/sw.js').catch(function () {}); });
}
