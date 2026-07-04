// tts.js — Timer-based word highlight + click-to-jump
// Works on all browsers including Chrome Android (no onboundary dependency)
(function() {
  var ttsActive    = false;
  var ttsRate      = 0.72;
  var ttsVoice     = null;
  var ttsTimer     = null;
  var ttsSpanIdx   = 0;
  var ttsAllSpans  = [];

  var SKIP = new Set(['NAV','FOOTER','BUTTON','SELECT','OPTION',
                      'SCRIPT','STYLE','INPUT','TEXTAREA']);

  // ── VOICE SETUP ────────────────────────────────────────────────────────────
  function getVoices() {
    var v = window.speechSynthesis.getVoices();
    ttsVoice = v.find(function(x) {
                 return x.lang === 'en-GB' && x.name.toLowerCase().includes('female');
               })
             || v.find(function(x) { return x.lang === 'en-GB'; })
             || v.find(function(x) { return x.lang.startsWith('en'); })
             || v[0] || null;
  }
  window.speechSynthesis.onvoiceschanged = getVoices;
  getVoices();

  // ── NODE HELPERS ───────────────────────────────────────────────────────────
  function isReadable(node) {
    var el = node.parentElement;
    while (el) {
      if (SKIP.has(el.tagName)) return false;
      if (el.classList && el.classList.contains('tts-bar')) return false;
      el = el.parentElement;
    }
    return node.textContent.trim().length > 0;
  }

  function wrapNode(textNode) {
    var parent = textNode.parentElement;
    if (!parent || parent.dataset.ttsWrapped) return;
    var parts = textNode.textContent.split(/(\s+)/);
    var frag  = document.createDocumentFragment();
    parts.forEach(function(w) {
      if (!w) return;
      if (/^\s+$/.test(w)) { frag.appendChild(document.createTextNode(w)); return; }
      var span = document.createElement('span');
      span.className   = 'tts-word';
      span.textContent = w;
      span.addEventListener('click', function(e) {
        e.stopPropagation();
        var idx = ttsAllSpans.indexOf(span);
        if (idx < 0) return;
        if (!ttsActive) ttsStart(idx);
        else            ttsJumpTo(idx);
      });
      frag.appendChild(span);
    });
    parent.replaceChild(frag, textNode);
    parent.dataset.ttsWrapped = '1';
  }

  function wrapAll() {
    var walker = document.createTreeWalker(
      document.body, NodeFilter.SHOW_TEXT,
      { acceptNode: function(n) {
          return isReadable(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }}
    );
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(wrapNode);
    ttsAllSpans = Array.from(document.querySelectorAll('.tts-word'));
  }

  function unwrapAll() {
    document.querySelectorAll('[data-tts-wrapped]').forEach(function(el) {
      var text = el.textContent;
      while (el.firstChild) el.removeChild(el.firstChild);
      el.appendChild(document.createTextNode(text));
      delete el.dataset.ttsWrapped;
    });
    ttsAllSpans = [];
  }

  function clearHighlight() {
    document.querySelectorAll('.tts-word.tts-active').forEach(function(s) {
      s.classList.remove('tts-active');
    });
  }

  function highlightSpan(idx) {
    clearHighlight();
    if (idx < ttsAllSpans.length) {
      var span = ttsAllSpans[idx];
      span.classList.add('tts-active');
      span.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      var lbl = document.getElementById('tts-label');
      if (lbl) lbl.textContent = '\u25b6 ' + span.textContent;
    }
  }

  // ── TIMER-BASED HIGHLIGHT ─────────────────────────────────────────────────
  // Estimate reading time per word based on rate
  // Average speaking rate ~150 words/min at rate=1.0
  // Adjusted: wordsPerSec = (150/60) * ttsRate
  function msPerWord(word) {
    var base = 60000 / 150; // ms per word at rate 1.0
    var adjusted = base / ttsRate;
    // Longer words take slightly longer
    var lengthFactor = 1 + (word.length - 4) * 0.03;
    return Math.max(80, adjusted * Math.max(0.7, lengthFactor));
  }

  function scheduleHighlights(startIdx) {
    if (ttsTimer) clearTimeout(ttsTimer);
    var delay = 0;

    function step(idx) {
      if (!ttsActive || idx >= ttsAllSpans.length) return;
      ttsTimer = setTimeout(function() {
        ttsSpanIdx = idx;
        highlightSpan(idx);
        step(idx + 1);
      }, delay);
      delay += msPerWord(ttsAllSpans[idx] ? ttsAllSpans[idx].textContent : 'word');
    }

    step(startIdx);
  }

  // ── SPEECH ────────────────────────────────────────────────────────────────
  function speakFrom(startIdx) {
    if (!ttsActive) return;
    window.speechSynthesis.cancel();
    if (ttsTimer) { clearTimeout(ttsTimer); ttsTimer = null; }
    clearHighlight();

    if (startIdx >= ttsAllSpans.length) { ttsStop(); return; }

    var text = ttsAllSpans.slice(startIdx).map(function(s) {
      return s.textContent;
    }).join(' ');

    var utt = new SpeechSynthesisUtterance(text);
    utt.rate   = ttsRate;
    utt.pitch  = 1.0;
    utt.volume = 1.0;
    if (ttsVoice) utt.voice = ttsVoice;

    // Try onboundary first (works on desktop Chrome/Edge/Firefox)
    var boundaryWorked = false;
    utt.onboundary = function(e) {
      if (e.name !== 'word') return;
      boundaryWorked = true;
      if (ttsTimer) { clearTimeout(ttsTimer); ttsTimer = null; }
      var textBefore = text.substring(0, e.charIndex);
      var wordCount  = textBefore.split(/\s+/).filter(Boolean).length;
      ttsSpanIdx     = startIdx + wordCount;
      highlightSpan(ttsSpanIdx);
    };

    utt.onstart = function() {
      // Wait 300ms — if onboundary never fired, fall back to timer
      setTimeout(function() {
        if (!boundaryWorked && ttsActive) {
          scheduleHighlights(startIdx);
        }
      }, 300);
    };

    utt.onend = function() {
      clearHighlight();
      if (ttsTimer) { clearTimeout(ttsTimer); ttsTimer = null; }
      if (ttsActive) ttsStop();
    };

    utt.onerror = function() {
      clearHighlight();
      if (ttsTimer) { clearTimeout(ttsTimer); ttsTimer = null; }
    };

    window.speechSynthesis.speak(utt);
  }

  // ── PUBLIC API ────────────────────────────────────────────────────────────
  function ttsStart(fromIdx) {
    ttsActive  = true;
    ttsSpanIdx = fromIdx || 0;
    var btn = document.getElementById('tts-btn');
    var lbl = document.getElementById('tts-label');
    if (btn) { btn.textContent = '\u23f9 Stop'; btn.classList.add('stop'); }
    if (lbl) lbl.textContent = 'Click any word to jump there \u2022 Reading...';
    wrapAll();
    speakFrom(ttsSpanIdx);
  }

  function ttsJumpTo(idx) {
    if (ttsTimer) { clearTimeout(ttsTimer); ttsTimer = null; }
    window.speechSynthesis.cancel();
    ttsSpanIdx = idx;
    highlightSpan(idx);
    setTimeout(function() { speakFrom(idx); }, 150);
  }

  window.ttsToggle = function() {
    if (ttsActive) { ttsStop(); return; }
    ttsStart(0);
  };

  window.ttsStop = function() {
    ttsActive = false;
    window.speechSynthesis.cancel();
    if (ttsTimer) { clearTimeout(ttsTimer); ttsTimer = null; }
    clearHighlight();
    unwrapAll();
    var btn = document.getElementById('tts-btn');
    var lbl = document.getElementById('tts-label');
    if (btn) { btn.textContent = '\ud83d\udd0a Read Aloud'; btn.classList.remove('stop'); }
    if (lbl) lbl.textContent = 'Press Read Aloud \u2022 Click any word to start from that point';
  };

  window.ttsSetSpeed = function(val) {
    ttsRate = parseFloat(val);
    if (ttsActive) ttsJumpTo(ttsSpanIdx);
  };

  window.addEventListener('beforeunload', function() {
    window.speechSynthesis.cancel();
    if (ttsTimer) clearTimeout(ttsTimer);
  });

})();
