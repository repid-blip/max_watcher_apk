package com.example.maxwatcher

import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.WebView
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * Порт send_max_message() из десктопной версии.
 * Работает через evaluateJavascript + addJavascriptInterface.
 *
 * Использование (из UI-потока):
 *   val sender = MaxMessageSender(webView)
 *   sender.send("Избранное", "Привет") { r -> Log.i("MW", "ok=${r.ok} err=${r.error}") }
 */
class MaxMessageSender(
    private val webView: WebView,
    private val sendButtonSelector: String? = Config.sendButtonSelector,
) {
    private val main = Handler(Looper.getMainLooper())
    private val pending = ConcurrentHashMap<String, (Result) -> Unit>()

    data class Result(val ok: Boolean, val error: String?, val log: List<String>)

    init {
        webView.addJavascriptInterface(Bridge(), "AndroidBridge")
    }

    private inner class Bridge {
        @JavascriptInterface
        fun onResult(requestId: String, json: String) {
            val cb = pending.remove(requestId) ?: return
            main.post {
                val logs = mutableListOf<String>()
                val ok: Boolean; val err: String?
                try {
                    val o = JSONObject(json)
                    ok = o.optBoolean("ok")
                    err = if (o.isNull("error")) null else o.optString("error")
                    o.optJSONArray("log")?.let { a ->
                        for (i in 0 until a.length()) logs.add(a.getString(i))
                    }
                } catch (e: Exception) {
                    cb(Result(false, "parse: ${e.message}", emptyList())); return@post
                }
                cb(Result(ok, err, logs))
            }
        }
    }

    fun send(chat: String, text: String, cb: (Result) -> Unit) {
        val reqId = UUID.randomUUID().toString()
        pending[reqId] = cb
        main.postDelayed({
            pending.remove(reqId)?.invoke(Result(false, "timeout", emptyList()))
        }, Config.REPLY_TIMEOUT_MS)
        val js = buildScript(chat, text, reqId)
        webView.post { webView.evaluateJavascript(js, null) }
    }

    private fun buildScript(chat: String, text: String, reqId: String): String {
        val c = JSONObject.quote(chat)
        val t = JSONObject.quote(text)
        val r = JSONObject.quote(reqId)
        val s = if (sendButtonSelector.isNullOrBlank()) "null" else JSONObject.quote(sendButtonSelector)
        return """
        (async () => {
          const chatName=$c, text=$t, reqId=$r, sendSel=$s;
          const log=[]; const L=(m)=>log.push(m);
          const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
          const finish=(ok,err)=>AndroidBridge.onResult(reqId, JSON.stringify({ok,error:err||null,log}));
          try {
            L('open chat '+chatName);
            const chatEl = await openChat(chatName, 8000);
            if (!chatEl) return finish(false, 'chat not found');
            await sleep(${Config.PAGE_SETTLE_MS});

            L('wait input');
            const input = await waitForInput(8000);
            if (!input) return finish(false, 'input field not found');

            L('clear+insert');
            focusAndClear(input);
            await sleep(100);
            document.execCommand('insertText', false, text);
            await sleep(150);
            if (!currentText(input)) {
              L('char-by-char fallback');
              focusAndClear(input);
              for (const ch of text) {
                if (ch === '\n') document.execCommand('insertLineBreak');
                else document.execCommand('insertText', false, ch);
                await sleep(15);
              }
              await sleep(150);
            }
            if (!currentText(input)) return finish(false, 'could not insert text');

            const before = currentText(input);
            const clicked = trySend(input, sendSel);
            L('send via '+(clicked?'button':'enter'));
            await sleep(${Config.SEND_CHECK_MS});
            const after = currentText(input);
            const cleared = !after || after !== before;
            L('cleared='+cleared);
            finish(cleared, cleared ? null : 'input not cleared after send');
          } catch(e) { finish(false, String((e&&e.message)||e)); }

          function currentText(el) { return ((el.value!==undefined?el.value:el.innerText)||'').trim(); }
          function openChat(name, tmo) {
            return new Promise((resolve) => {
              const dl = Date.now() + tmo;
              const tick = () => {
                const all=[...document.querySelectorAll('*')];
                const c=all.filter(e => (e.textContent||'').trim()===name &&
                  ![...e.children].some(ch => (ch.textContent||'').trim()===name));
                if (c.length) {
                  c.sort((a,b)=>a.getBoundingClientRect().left-b.getBoundingClientRect().left);
                  try { c[0].scrollIntoView({block:'center'}); } catch(_){}
                  c[0].click(); return resolve(c[0]);
                }
                if (Date.now()>dl) return resolve(null);
                setTimeout(tick, 200);
              }; tick();
            });
          }
          function waitForInput(tmo) {
            return new Promise((resolve) => {
              const dl = Date.now() + tmo;
              const tick = () => {
                const el = findInput();
                if (el) return resolve(el);
                if (Date.now()>dl) return resolve(null);
                setTimeout(tick, 150);
              }; tick();
            });
          }
          function findInput() {
            const generic = "[contenteditable]:not([contenteditable='false']), "+
                            "[role='textbox'], textarea, input[type='text'], input:not([type])";
            const pool = sel => { try { return [...document.querySelectorAll(sel)]; } catch(e){ return []; } };
            const vw=window.innerWidth, vh=window.innerHeight;
            let best=null, bs=-Infinity;
            for (const e of pool(generic)) {
              if (e.disabled || e.readOnly) continue;
              const cs=getComputedStyle(e);
              if (cs.display==='none' || cs.visibility==='hidden') continue;
              const r=e.getBoundingClientRect();
              if (r.width<40 || r.bottom<0 || r.top>vh || r.right<0 || r.left>vw) continue;
              const sc = r.top + Math.min(r.width, 800) * 0.2;
              if (sc > bs) { best=e; bs=sc; }
            }
            return best;
          }
          function focusAndClear(el) {
            el.focus();
            if (el.value !== undefined) {
              try { el.setSelectionRange(0, el.value.length); } catch(_){}
              document.execCommand('delete', false, null);
            } else {
              const rng = document.createRange(); rng.selectNodeContents(el);
              const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(rng);
              document.execCommand('delete', false, null);
            }
          }
          function trySend(input, customSel) {
            if (customSel) {
              try { const b = document.querySelector(customSel);
                    if (b) { b.click(); return 'button'; } } catch(_){}
            }
            const btn = findSendButton(input);
            if (btn) { btn.click(); return 'button'; }
            const mk = t => new KeyboardEvent(t, {key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true});
            input.dispatchEvent(mk('keydown'));
            input.dispatchEvent(mk('keypress'));
            input.dispatchEvent(mk('keyup'));
            return 'enter';
          }
          function findSendButton(input) {
            const sels = ['button[aria-label*="тправить" i]','button[aria-label*="end" i]',
                          'button[title*="тправить" i]','button[title*="end" i]',
                          'button[type="submit"]','[data-testid*="send" i]',
                          '[class*="send" i][role="button"]'];
            const ir = input.getBoundingClientRect();
            let node = input.parentElement;
            for (let i=0; i<8 && node; i++, node=node.parentElement) {
              for (const s of sels) {
                for (const b of node.querySelectorAll(s)) {
                  const r=b.getBoundingClientRect();
                  if (r.width<10 || r.height<10) continue;
                  if (r.left >= ir.right-8 || r.bottom >= ir.top) return b;
                }
              }
            }
            return null;
          }
        })();
        """.trimIndent()
    }
}