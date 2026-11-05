package com.example.maxwatcher

/**
 * JS-скрипты, портированные из десктопной версии max_watcher_android.pyw.
 * Возвращают JSON-строку (evaluateJavascript) или пишут в AndroidBridge.
 */
object WatcherJs {

    /** {chatName: unreadCount} для всех чатов со значком непрочитанных. */
    fun chatsJs(badgeSel: String, maxH: Int, titleSel: String, ignored: List<String>): String {
        val ignoredJson = ignored.joinToString(",", "[", "]") { "\"" + it.replace("\"", "\\\"") + "\"" }
        return """
        (() => {
          const result = {};
          const timeRe = /^\d{1,2}[:.]\d{2}${'$'}/;
          const badgeSel = ${q(badgeSel)};
          const maxH = $maxH;
          const titleSel = ${if (titleSel.isBlank()) "null" else q(titleSel)};
          const ignored = $ignoredJson;
          for (const b of document.querySelectorAll(badgeSel)) {
            const d = (b.textContent || '').replace(/\D/g, '');
            const count = d ? parseInt(d, 10) : 1;
            let row = b;
            while (row.parentElement) {
              const p = row.parentElement;
              if (p.getBoundingClientRect().height > maxH) break;
              row = p;
            }
            const rr = row.getBoundingClientRect();
            if (rr.height < 20 || rr.width < 100) continue;
            let title = null;
            if (titleSel) {
              const t = row.querySelector(titleSel);
              title = t ? (t.textContent || '').trim() : null;
            } else {
              const pick = noAvatar => [...row.querySelectorAll('*')].filter(e => {
                if (e.children.length || b.contains(e)) return false;
                const s = (e.textContent || '').trim();
                if (!s || timeRe.test(s) || /^\d+${'$'}/.test(s)) return false;
                if (noAvatar && e.closest('[class*="avatar" i]')) return false;
                const r = e.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
              });
              let leaves = pick(true);
              if (!leaves.length) leaves = pick(false);
              leaves.sort((x, y) => {
                const a = x.getBoundingClientRect(), c = y.getBoundingClientRect();
                const ta = Math.round(a.top / 8), tc = Math.round(c.top / 8);
                return ta !== tc ? ta - tc : a.left - c.left;
              });
              title = leaves.length ? (leaves[0].textContent || '').trim() : null;
            }
            if (!title || ignored.includes(title)) continue;
            result[title] = Math.max(result[title] || 0, count);
          }
          return JSON.stringify(result);
        })();
        """.trimIndent()
    }

    /** [{i, text, time}] для файловых кнопок в открытом чате. */
    fun fileButtonsJs(btnSel: String, btnText: String): String = """
        (() => {
          const sel = ${q(btnSel)}, word = ${q(btnText)};
          const out = [];
          document.querySelectorAll(sel).forEach((el, i) => {
            if (!(el.innerText || '').includes(word)) return;
            const m = (el.innerText || '').match(/\d{1,2}:\d{2}/);
            out.push({i, text: (el.innerText || '').trim().slice(0, 200), time: m ? m[0] : ''});
          });
          return JSON.stringify(out);
        })();
    """.trimIndent()

    /** Открыть чат по названию. Возвращает {ok, error}. */
    fun openChatJs(chatName: String, settleMs: Long): String = """
        (() => {
          const name = ${q(chatName)};
          const all = [...document.querySelectorAll('*')];
          const cands = all.filter(e => (e.textContent || '').trim() === name &&
              ![...e.children].some(c => (c.textContent || '').trim() === name));
          if (!cands.length) return JSON.stringify({ok: false, error: 'chat not found'});
          cands.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
          try { cands[0].scrollIntoView({block: 'center'}); } catch (_) {}
          cands[0].click();
          return JSON.stringify({ok: true});
        })();
    """.trimIndent()

    /** Клик по кнопке «Скачать» по индексу — триггерит DownloadListener. */
    fun clickDownloadJs(btnSel: String, btnText: String, index: Int): String = """
        (() => {
          const sel = ${q(btnSel)}, word = ${q(btnText)};
          const els = [];
          document.querySelectorAll(sel).forEach((el) => {
            if ((el.innerText || '').includes(word)) els.push(el);
          });
          const el = els[$index];
          if (!el) return JSON.stringify({ok: false, error: 'button not found'});
          el.click();
          return JSON.stringify({ok: true});
        })();
    """.trimIndent()

    /** [{idx, time, caption, ratio, src, loaded}] для картинок открытого чата. */
    fun imagesJs(minW: Int, minH: Int): String = """
        (() => {
          const timeRe = /\b\d{1,2}:\d{2}\b/;
          const timeAll = /\b\d{1,2}:\d{2}\b/g;
          const out = [];
          document.querySelectorAll('img').forEach((img, idx) => {
            const r = img.getBoundingClientRect();
            if (r.width < $minW || r.height < $minH) return;
            if (img.closest('[class*="avatar" i], [class*="sticker" i], [class*="emoji" i]')) return;
            let bubble = null, n = img.parentElement;
            for (let i = 0; i < 10 && n; i++, n = n.parentElement) {
              const t = (n.innerText || '').match(timeAll) || [];
              if (t.length > 1) break;
              if (t.length === 1) bubble = n;
            }
            const time = bubble ? ((bubble.innerText || '').match(timeRe) || [''])[0] : '';
            let caption = false;
            if (bubble) {
              for (const e of bubble.querySelectorAll('*')) {
                if (e.children.length) continue;
                const s = (e.textContent || '').trim();
                if (!s || !/\p{L}/u.test(s)) continue;
                const q = e.getBoundingClientRect();
                if (q.width > 0 && q.top >= r.bottom - 4) { caption = true; break; }
              }
            }
            const loaded = img.complete && img.naturalWidth > 0;
            out.push({idx, time, caption, loaded,
                      ratio: loaded ? (img.naturalWidth / img.naturalHeight).toFixed(2) : '',
                      src: img.currentSrc || img.src || ''});
          });
          return JSON.stringify(out);
        })();
    """.trimIndent()

    /**
     * Скачивает картинку как base64. Результат приходит в
     * AndroidBridge.imageData(reqId, b64, ext) или imageError(reqId, err).
     */
    fun fetchImageJs(reqId: String, src: String): String = """
        (async () => {
          const reqId = ${q(reqId)}, src = ${q(src)};
          try {
            const r = await fetch(src);
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const blob = await r.blob();
            const b64 = await new Promise((res, rej) => {
              const fr = new FileReader();
              fr.onload = () => res(String(fr.result).split(',')[1]);
              fr.onerror = rej;
              fr.readAsDataURL(blob);
            });
            const mime = blob.type || '';
            const ext = mime.includes('png') ? 'png'
                      : mime.includes('gif') ? 'gif'
                      : mime.includes('webp') ? 'webp'
                      : 'jpg';
            AndroidBridge.imageData(reqId, b64, ext);
          } catch (e) {
            AndroidBridge.imageError(reqId, String(e));
          }
        })();
    """.trimIndent()

    private fun q(s: String): String =
        "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
}