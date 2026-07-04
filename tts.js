// tts.js — Word highlighting + click-to-jump read aloud
// Prof Dr Tan | IBKR Options Income System
(function() {
  var ttsActive = false;
  var ttsRate   = 0.72;
  var ttsVoice  = null;
  var SKIP = new Set(['NAV','FOOTER','BUTTON','SELECT','OPTION',
                      'SCRIPT','STYLE','INPUT','TEXTAREA']);

  function getVoices() {
    var v = window.speechSynthesis.getVoices();
    ttsVoice = v.find(function(x) {
                 return x.lang === 'en-GB' && x.name.toLowerCase().includes('female');
               }) || v.find(function(x) { return x.lang === 'en-GB'; })
                 || v.find(function(x) { return x.lang.startsWith('en'); })
                 || v[0] || null;
  }
  window.speechSynthesis.onvoiceschanged = getVoices;
  getVoices();

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
    var frag = document.createDocumentFragment();
    parts.forEach(function(w) {
      if (!w) return;
      if (/^\s+$/.test(w)) {
        frag.appendChild(document.createTextNode(w));
        return;
      }
      var span = document.createElement('span');
      span.className = 'tts-word';
      span.textContent = w;
      span.addEventListener('click', function(e) {
        e.stopPropagation();
        if (!ttsActive) ttsStart();
        setTimeout(function() { jumpToSpan(span); }, 100);
      });
      frag.appendChild(span);
    });
    parent.replaceChild(frag, textNode);
    parent.dataset.ttsWrapped = '1';
  }

  function unwrapAll() {
    document.querySelectorAll('[data-tts-wrapped]').forEach(function(el) {
      var text = el.textContent;
      while (el.firstChild) el.removeChild(el.firstChild);
      el.appendChild(document.createTextNode(text));
      delete el.dataset.ttsWrapped;
    });
  }

  function clearHighlight() {
    document.querySelectorAll('.tts-word.tts-active').forEach(function(s) {
      s.classList.remove('tts-active');
    });
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
  }

  function getAllSpans() {
    return Array.from(document.querySelectorAll('.tts-word'));
  }

  function speakFromSpanIndex(allSpans, startIdx) {
    if (!ttsActive || startIdx >= allSpans.length) {
      ttsStop(); return;
    }
    // Build text from startIdx onward
    var text = allSpans.slice(startIdx).map(function(s) {
      return s.textContent;
    }).join(' ');

    var utt = new SpeechSynthesisUtterance(text);
    utt.rate   = ttsRate;
    utt.pitch  = 1.0;
    utt.volume = 1.0;
    if (ttsVoice) utt.voice = ttsVoice;

    utt.onboundary = function(e) {
      if (e.name !== 'word') return;
      clearHighlight();
      // Count words spoken so far to find which span
      var textBefore = text.substring(0, e.charIndex);
      var wordCount  = textBefore.split(/\s+/).filter(Boolean).length;
      var spanIdx    = startIdx + wordCount;
      if (spanIdx < allSpans.length) {
        allSpans[spanIdx].classList.add('tts-active');
        allSpans[spanIdx].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        var lbl = document.getElementById('tts-label');
        if (lbl) lbl.textContent = '\u25b6 ' + allSpans[spanIdx].textContent;
      }
    };

    utt.onend = function() {
      clearHighlight();
      if (ttsActive) ttsStop();
    };

    utt.onerror = function() {
      clearHighlight();
    };

    window.speechSynthesis.speak(utt);
    window._ttsCurrentUtt = utt;
    window._ttsAllSpans   = allSpans;
    window._ttsStartIdx   = startIdx;
  }

  function jumpToSpan(span) {
    window.speechSynthesis.cancel();
    clearHighlight();
    var allSpans = getAllSpans();
    var idx = allSpans.indexOf(span);
    if (idx < 0) idx = 0;
    span.classList.add('tts-active');
    span.scrollIntoView({ behavior: 'smooth', block: 'center' });
    speakFromSpanIndex(allSpans, idx);
  }

  function ttsStart() {
    ttsActive = true;
    var btn = document.getElementById('tts-btn');
    var lbl = document.getElementById('tts-label');
    if (btn) { btn.textContent = '\u23f9 Stop'; btn.classList.add('stop'); }
    if (lbl) lbl.textContent = 'Click any word to jump \u2022 Reading...';
    window.speechSynthesis.cancel();
    wrapAll();
    var allSpans = getAllSpans();
    if (!allSpans.length) { ttsStop(); return; }
    speakFromSpanIndex(allSpans, 0);
  }

  window.ttsToggle = function() {
    if (ttsActive) { ttsStop(); return; }
    ttsStart();
  };

  window.ttsStop = function() {
    ttsActive = false;
    window.speechSynthesis.cancel();
    clearHighlight();
    unwrapAll();
    var btn = document.getElementById('tts-btn');
    var lbl = document.getElementById('tts-label');
    if (btn) { btn.textContent = '\ud83d\udd0a Read Aloud'; btn.classList.remove('stop'); }
    if (lbl) lbl.textContent = 'Press Read Aloud \u2022 Click any text to start from that point';
  };

  window.ttsSetSpeed = function(val) {
    ttsRate = parseFloat(val);
    if (ttsActive) {
      var allSpans = getAllSpans();
      var activeIdx = allSpans.findIndex(function(s) {
        return s.classList.contains('tts-active');
      });
      if (activeIdx < 0) activeIdx = 0;
      window.speechSynthesis.cancel();
      speakFromSpanIndex(allSpans, activeIdx);
    }
  };

  window.addEventListener('beforeunload', function() {
    window.speechSynthesis.cancel();
    unwrapAll();
  });
})();
