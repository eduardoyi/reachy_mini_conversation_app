// The end-of-game card for every phone game: when the game's state carries `card` (games/end_card.py), draw it as a
// 1080x1350 picture (4:5, the shape Instagram and WhatsApp show whole) and offer to save or share it.
// Each game's page loads this script with its join code and calls RichieEndCard.update(state) on every state.
(function () {
  'use strict';
  var TEAMS = {
    red: ['#e0464c', '#ffffff'], blue: ['#3a6ff0', '#ffffff'], green: ['#12a46c', '#ffffff'],
    yellow: ['#ffb21e', '#2b1f00']
  };
  var PLAIN = ['#4b3a8f', '#ffffff'];
  var WORDS = {
    es: {open: 'Ver tu tarjeta', save: 'Guardar', share: 'Compartir', close: 'Cerrar',
         hint: 'O mantén pulsada la imagen para guardarla.', footer: 'Jugado con Richie · Reachy Mini'},
    en: {open: 'See your card', save: 'Save', share: 'Share', close: 'Close',
         hint: 'Or press and hold the picture to save it.', footer: 'Played with Richie · Reachy Mini'}
  };
  var FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  var shown = null, opened = false, button = null, overlay = null;

  function fit(ctx, text, width, largest, weight) {
    var size = largest;
    do { ctx.font = weight + ' ' + size + 'px ' + FONT; size -= 4; } while (ctx.measureText(text).width > width && size > 24);
    return ctx.font;
  }

  function robot(ctx, x, y, s, ink, paper) {
    ctx.lineWidth = s * 0.06; ctx.strokeStyle = ink; ctx.fillStyle = paper; ctx.lineCap = 'round';
    [[-0.28, -0.42], [0.28, 0.42]].forEach(function (a) {
      ctx.beginPath(); ctx.moveTo(x + a[0] * s, y - s * 0.3); ctx.lineTo(x + a[1] * s, y - s * 0.95); ctx.stroke();
      ctx.beginPath(); ctx.arc(x + a[1] * s, y - s * 0.95, s * 0.07, 0, 7); ctx.fillStyle = ink; ctx.fill();
    });
    ctx.fillStyle = paper;
    var w = s * 1.3, h = s * 0.85, r = h / 2;
    ctx.beginPath();
    ctx.moveTo(x - w / 2 + r, y - h / 2); ctx.arcTo(x + w / 2, y - h / 2, x + w / 2, y + h / 2, r);
    ctx.arcTo(x + w / 2, y + h / 2, x - w / 2, y + h / 2, r); ctx.arcTo(x - w / 2, y + h / 2, x - w / 2, y - h / 2, r);
    ctx.arcTo(x - w / 2, y - h / 2, x + w / 2, y - h / 2, r); ctx.closePath(); ctx.fill(); ctx.stroke();
    ctx.fillStyle = ink;
    [-0.25, 0.25].forEach(function (dx) { ctx.beginPath(); ctx.arc(x + dx * s, y, s * 0.13, 0, 7); ctx.fill(); });
  }

  function draw(card, lang) {
    var colors = TEAMS[card.team] || PLAIN, bg = colors[0], ink = colors[1];
    var canvas = document.createElement('canvas'); canvas.width = 1080; canvas.height = 1350;
    var ctx = canvas.getContext('2d'), w = WORDS[lang];
    ctx.fillStyle = bg; ctx.fillRect(0, 0, 1080, 1350);
    ctx.fillStyle = ink; ctx.textBaseline = 'alphabetic';
    ctx.font = '700 40px ' + FONT; ctx.globalAlpha = 0.8;
    ctx.fillText(String(card.game || '').toUpperCase(), 90, 130); ctx.globalAlpha = 1;
    robot(ctx, 540, 470, 200, ink, bg);
    ctx.textAlign = 'center';
    ctx.font = fit(ctx, card.title, 900, 132, '800'); ctx.fillText(card.title, 540, 830);
    ctx.font = fit(ctx, card.name, 900, 84, '700'); ctx.fillText(card.name, 540, 960);
    ctx.globalAlpha = 0.85; ctx.font = fit(ctx, card.line, 900, 56, '600'); ctx.fillText(card.line, 540, 1050);
    ctx.globalAlpha = 0.7; ctx.font = '600 34px ' + FONT;
    ctx.fillText(w.footer + ' · ' + new Date().toLocaleDateString(lang === 'en' ? 'en-GB' : 'es-ES'), 540, 1260);
    ctx.globalAlpha = 1;
    return canvas;
  }

  function style() {
    var css = '.rec-open{position:fixed;left:50%;bottom:calc(16px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);' +
      'z-index:50;border:0;border-radius:999px;padding:14px 22px;font:700 16px ' + FONT + ';background:#ffb21e;color:#2b1f00;' +
      'box-shadow:0 6px 20px rgba(0,0,0,.25)}' +
      '.rec-sheet{position:fixed;inset:0;z-index:60;background:rgba(10,12,30,.82);display:flex;flex-direction:column;' +
      'align-items:center;justify-content:center;gap:14px;padding:16px}' +
      '.rec-sheet img{max-width:100%;max-height:68vh;border-radius:18px;box-shadow:0 10px 40px rgba(0,0,0,.4)}' +
      '.rec-row{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}' +
      '.rec-row a,.rec-row button{border:0;border-radius:999px;padding:12px 20px;font:700 16px ' + FONT + ';text-decoration:none;' +
      'background:#fff;color:#1a1f3d}.rec-row .rec-main{background:#ffb21e;color:#2b1f00}' +
      '.rec-hint{color:#dfe3f3;font:500 13px ' + FONT + ';text-align:center}';
    var el = document.createElement('style'); el.textContent = css; document.head.appendChild(el);
  }

  function open(card, lang) {
    close();
    var w = WORDS[lang], canvas = draw(card, lang), url = canvas.toDataURL('image/png');
    var file = 'richie-' + String(card.game || 'game').toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.png';
    overlay = document.createElement('div'); overlay.className = 'rec-sheet';
    var img = document.createElement('img'); img.src = url; img.alt = card.title + ' · ' + card.name;
    var row = document.createElement('div'); row.className = 'rec-row';
    var save = document.createElement('a'); save.className = 'rec-main'; save.href = url; save.download = file;
    save.textContent = w.save; row.appendChild(save);
    canvas.toBlob(function (blob) {
      if (!blob || !navigator.canShare) return;
      var shared = new File([blob], file, {type: 'image/png'});
      if (!navigator.canShare({files: [shared]})) return;
      var share = document.createElement('button'); share.type = 'button'; share.textContent = w.share;
      share.onclick = function () { navigator.share({files: [shared]}).catch(function () {}); };
      row.insertBefore(share, save.nextSibling);
    });
    var shut = document.createElement('button'); shut.type = 'button'; shut.textContent = w.close; shut.onclick = close;
    row.appendChild(shut);
    var hint = document.createElement('div'); hint.className = 'rec-hint'; hint.textContent = w.hint;
    overlay.appendChild(img); overlay.appendChild(row); overlay.appendChild(hint);
    overlay.onclick = function (ev) { if (ev.target === overlay) close(); };
    document.body.appendChild(overlay);
  }

  function close() { if (overlay) { overlay.remove(); overlay = null; } }

  function update(state) {
    var card = state && state.card, lang = state && state.language === 'en' ? 'en' : 'es';
    if (!card) {
      shown = null; opened = false; close();
      if (button) { button.remove(); button = null; }
      return;
    }
    shown = card;
    if (!button) {
      button = document.createElement('button'); button.type = 'button'; button.className = 'rec-open';
      document.body.appendChild(button);
    }
    button.textContent = WORDS[lang].open;
    button.onclick = function () { open(shown, lang); };
    if (!opened) { opened = true; setTimeout(function () { if (shown) open(shown, lang); }, 2500); }
  }

  style();
  window.RichieEndCard = {update: update, draw: draw};
})();
