from fastapi.responses import HTMLResponse


CHAT_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Trendly Support</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; background:#f8f7fc; color:#201d2b; display:grid; place-items:center; }
    main { width:min(820px,100%); height:min(760px,100vh); background:#fff; display:flex; flex-direction:column; box-shadow:0 20px 60px #2a21451a; }
    header { padding:22px 28px; color:#fff; background:linear-gradient(120deg,#5c2db9,#8c47dd); }
    h1 { margin:0; font-size:1.25rem; } header p { margin:5px 0 0; opacity:.85; font-size:.9rem; }
    .setup { padding:12px 20px; background:#f0eafe; display:flex; gap:10px; flex-wrap:wrap; align-items:center; font-size:.85rem; }
    label { font-weight:600; } input { border:1px solid #d8d1e5; border-radius:7px; padding:8px; font:inherit; width:130px; }
    #messages { flex:1; overflow:auto; padding:24px; display:flex; flex-direction:column; gap:14px; }
    .message { max-width:82%; padding:12px 15px; border-radius:14px; white-space:pre-wrap; line-height:1.4; }
    .agent { align-self:flex-start; background:#f1edf8; border-bottom-left-radius:3px; }
    .user { align-self:flex-end; background:#652fc2; color:#fff; border-bottom-right-radius:3px; }
    .meta { font-size:.74rem; color:#746d82; margin-top:-9px; padding:0 4px; }
    form { display:flex; gap:10px; padding:18px; border-top:1px solid #eeeaf3; }
    textarea { flex:1; resize:none; min-height:48px; max-height:120px; padding:12px; font:inherit; border:1px solid #d8d1e5; border-radius:9px; }
    button { border:0; border-radius:9px; padding:0 19px; font-weight:700; cursor:pointer; background:#652fc2; color:#fff; }
    button:disabled { opacity:.55; cursor:wait; }
    .examples { padding:0 20px 15px; font-size:.8rem; color:#625b6e; } .examples button { padding:5px 8px; margin:3px; font-size:.78rem; background:#eee8f8; color:#52269c; }
  </style>
</head>
<body><main>
  <header><h1>Trendly Support</h1><p>Order help, returns, exchanges, and policy questions</p></header>
  <div class="setup"><label>Signed-in customer <input id="customer" value="C-101" aria-label="Customer ID"></label><label>Session <input id="session" value="demo-1" aria-label="Session ID"></label></div>
  <section id="messages" aria-live="polite"><div class="message agent">Hi! I can help with your Trendly order, returns, exchanges, shipping, and refund questions. What can I look into?</div></section>
  <div class="examples">Try: <button type="button" data-message="Where is TR-4524?">Track a partial shipment</button><button type="button" data-message="I want to return TR-4530">Start a return</button><button type="button" data-message="Where is TR-4526?">Lost parcel</button></div>
  <form id="chat"><textarea id="message" placeholder="Type your message…" required aria-label="Message"></textarea><button id="send" type="submit">Send</button></form>
</main>
<script>
  const messages = document.querySelector('#messages'), form = document.querySelector('#chat'), input = document.querySelector('#message'), send = document.querySelector('#send');
  function add(text, kind, meta='') { const el=document.createElement('div'); el.className='message '+kind; el.textContent=text; messages.append(el); if(meta){const m=document.createElement('div');m.className='meta';m.textContent=meta;messages.append(m)} messages.scrollTop=messages.scrollHeight; }
  async function sendMessage(text) { const customer_id=document.querySelector('#customer').value.trim(), session_id=document.querySelector('#session').value.trim(); if(!text||!customer_id||!session_id)return; add(text,'user'); input.value=''; send.disabled=true; try { const r=await fetch('/v1/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,customer_id,session_id})}); const data=await r.json(); if(!r.ok) throw new Error(data.detail||'Request failed'); const action=data.actions?.length ? 'Action: '+data.actions.map(a=>a.type+' ('+a.reference+')').join(', ') : 'Mode: '+data.mode; add(data.reply,'agent',action); } catch(err) { add('I could not reach support right now: '+err.message,'agent'); } finally { send.disabled=false; input.focus(); } }
  form.addEventListener('submit', e => {e.preventDefault(); sendMessage(input.value.trim())});
  document.querySelectorAll('[data-message]').forEach(b => b.addEventListener('click', () => sendMessage(b.dataset.message)));
</script></body></html>"""


def chat_page() -> HTMLResponse:
    return HTMLResponse(CHAT_PAGE)
