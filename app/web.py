from fastapi.responses import HTMLResponse


LANDING_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Trendly Agentic Support</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root { --ink:#172321; --muted:#65736e; --paper:#fbfaf6; --teal:#173b35; --mint:#d9eee5; --coral:#e76f51; font-family:'DM Sans','Trebuchet MS',sans-serif; }
    * { box-sizing:border-box; } body { margin:0; min-height:100vh; color:var(--ink); background:var(--paper); background-image:linear-gradient(135deg,rgba(217,238,229,.68),transparent 40%),linear-gradient(315deg,rgba(231,111,81,.1),transparent 34%); }
    .page { width:min(1080px,100%); min-height:100vh; margin:auto; padding:28px clamp(22px,5vw,64px); display:flex; flex-direction:column; }
    nav { display:flex; align-items:center; justify-content:space-between; }.brand { display:flex; align-items:center; gap:10px; font:700 1.2rem 'Space Grotesk',sans-serif; }.mark { display:grid; place-items:center; width:31px; height:31px; color:var(--teal); background:#f7c9a9; border-radius:9px; }
    .nav-link { color:var(--teal); font-size:.8rem; font-weight:700; text-decoration:none; }.nav-link:hover { text-decoration:underline; }
    main { display:grid; grid-template-columns:1.05fr .95fr; align-items:center; gap:clamp(40px,8vw,100px); flex:1; padding:56px 0 72px; }.kicker { margin:0 0 17px; color:var(--coral); font-size:.7rem; font-weight:700; letter-spacing:.13em; text-transform:uppercase; }.title { max-width:560px; margin:0; font:700 clamp(2.7rem,6vw,5rem)/.98 'Space Grotesk',sans-serif; letter-spacing:0; }.intro { max-width:510px; margin:22px 0 30px; color:var(--muted); font-size:1.05rem; line-height:1.6; }.cta { display:inline-flex; align-items:center; gap:12px; padding:13px 17px; color:#fff; background:var(--teal); border-radius:7px; font-weight:700; text-decoration:none; box-shadow:0 9px 22px rgba(23,59,53,.16); }.cta:hover { background:#24574e; }.arrow { font-size:1.1rem; }
    .brief { padding:25px; color:#eff8f3; background:var(--teal); border-radius:12px; box-shadow:0 22px 44px rgba(23,59,53,.16); }.brief h2 { margin:0 0 20px; font:600 1.2rem 'Space Grotesk',sans-serif; }.item { display:flex; gap:12px; align-items:flex-start; padding:15px 0; border-top:1px solid rgba(239,248,243,.16); }.item:first-of-type { border-top:0; }.item-icon { display:grid; place-items:center; width:28px; height:28px; flex:0 0 28px; margin-top:0; color:var(--teal); background:#f7c9a9; border-radius:7px; font-size:.78rem; font-weight:700; }.item strong { display:block; font-size:.84rem; }.item > div span { display:block; margin-top:4px; color:#b9d7cd; font-size:.77rem; line-height:1.45; }
    footer { display:flex; justify-content:space-between; gap:20px; padding-top:18px; color:#87938e; border-top:1px solid #dce4df; font-size:.72rem; }
    @media (max-width:700px) { .page { padding-top:20px; } nav .nav-link { display:none; } main { display:flex; flex-direction:column; align-items:stretch; justify-content:center; padding:54px 0; gap:38px; }.title { font-size:3rem; }.intro { font-size:.95rem; }.brief { padding:21px; } footer { flex-direction:column; gap:5px; } }
  </style>
</head>
<body><div class="page"><nav><div class="brand"><span class="mark">T</span> trendly</div><a class="nav-link" href="/agent">Open agent&nbsp; →</a></nav>
  <main><section><p class="kicker">Agentic support assistant</p><h1 class="title">Support that helps customers move forward.</h1><p class="intro">This agent can verify order details, explain policy clearly, guide return and exchange flows, and escalate exceptions only when needed. It stays grounded in approved data and avoids unsafe guesses.</p><a class="cta" href="/agent">Try the support agent <span class="arrow">→</span></a></section>
    <section class="brief"><h2>What this agent can do</h2><div class="item"><span class="item-icon">1</span><div><strong>Order-aware conversations</strong><span>Checks the right order, confirms status, and keeps the flow grounded in verified information.</span></div></div><div class="item"><span class="item-icon">2</span><div><strong>Policy-based guidance</strong><span>Explains shipping, returns, refunds, and exchange rules using the approved policy source.</span></div></div><div class="item"><span class="item-icon">3</span><div><strong>Safe handoffs</strong><span>Escalates only when a case is unclear, sensitive, or outside policy with useful context for a human.</span></div></div></section>
  </main><footer><span>Support agent demo</span><span>Grounded answers · safe actions · human handoffs</span></footer>
</div></body></html>"""


CHAT_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Trendly Support Desk</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root { color-scheme: light; --ink:#172321; --muted:#6f7b77; --line:#dce4df; --paper:#fbfaf6; --panel:#fff; --mint:#d9eee5; --teal:#1e655b; --coral:#e76f51; --shadow:0 24px 70px rgba(31,57,48,.12); font-family:'DM Sans', 'Trebuchet MS', sans-serif; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; color:var(--ink); background:var(--paper); background-image:linear-gradient(135deg,rgba(217,238,229,.58),transparent 36%),linear-gradient(315deg,rgba(231,111,81,.08),transparent 34%); }
    button,input,textarea { font:inherit; }
    button { cursor:pointer; }
    /* Exactly one viewport tall, never more. A min-height taller than a short
       window made the box overflow while `margin:auto` centred it, pushing the
       header above the scroll origin where nothing could reach it. `dvh` keeps
       that true when mobile browser chrome slides away. */
    .app { width:min(1180px,100%); height:100vh; height:100dvh; margin:auto; display:grid; grid-template-columns:248px 1fr; background:var(--panel); box-shadow:var(--shadow); overflow:hidden; }
    aside { min-height:0; overflow-y:auto; display:flex; flex-direction:column; padding:14px 12px; color:#eff8f3; background:#173b35; }
    .brand { display:flex; gap:10px; align-items:center; padding:10px 11px 8px; font:700 1.18rem 'Space Grotesk',sans-serif; letter-spacing:0; }
    .brand-mark { display:grid; place-items:center; width:29px; height:29px; color:#173b35; background:#f7c9a9; border-radius:8px; font-size:.9rem; }
    .operator-name { padding:0 11px 22px; color:#8fb6a9; font-size:.69rem; }
    .new-session { display:flex; align-items:center; gap:9px; width:100%; padding:10px 11px; color:#173b35; background:#f7c9a9; border:0; border-radius:7px; text-align:left; font-size:.8rem; font-weight:700; }
    .new-session:hover { background:#f9d7bd; }
    .nav-icon { font-size:1rem; line-height:1; }
    .recent-label { margin:27px 11px 9px; color:#8fb6a9; font-size:.68rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }
    .recent-chat { display:block; width:100%; overflow:hidden; padding:9px 11px; color:#d9eee5; background:rgba(217,238,229,.1); border:0; border-radius:6px; text-align:left; text-overflow:ellipsis; white-space:nowrap; font-size:.78rem; }
    .picker-label { margin:27px 11px 9px; color:#8fb6a9; font-size:.68rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }
    .verify-card { display:flex; gap:9px; align-items:flex-start; margin:0 11px; padding:10px 11px; background:rgba(217,238,229,.1); border:1px solid rgba(217,238,229,.22); border-radius:7px; }
    .verify-card strong { display:block; color:#eff8f3; font-size:.78rem; }
    .verify-card span#verify-detail { display:block; margin-top:3px; color:#8fb6a9; font-size:.68rem; line-height:1.45; }
    .verify-dot { width:7px; height:7px; flex:0 0 7px; margin-top:5px; background:#c98b6b; border-radius:50%; box-shadow:0 0 0 3px rgba(201,139,107,.22); }
    .switch-hint { margin:9px 11px 0; padding:8px 10px; color:#f7c9a9; background:rgba(247,201,169,.1); border:1px solid rgba(247,201,169,.28); border-radius:7px; font-size:.68rem; line-height:1.5; }
    .switch-hint strong { color:#f7c9a9; }
    .verify-card.is-verified .verify-dot { background:#55ae87; box-shadow:0 0 0 3px rgba(85,174,135,.22); }
    .profile { margin:10px 11px 0; padding:11px 12px; background:rgba(217,238,229,.07); border:1px solid rgba(217,238,229,.16); border-radius:7px; animation:rise .3s ease both; }
    .profile dt { color:#8fb6a9; font-size:.63rem; font-weight:700; letter-spacing:.09em; text-transform:uppercase; }
    .profile dd { margin:2px 0 10px; overflow:hidden; color:#eff8f3; font-size:.76rem; text-overflow:ellipsis; white-space:nowrap; }
    .profile dd:last-of-type { margin-bottom:0; }
    .account { display:flex; align-items:center; gap:9px; margin-top:auto; padding:11px 9px; border-top:1px solid rgba(239,248,243,.15); }
    .avatar { display:grid; place-items:center; width:30px; height:30px; flex:0 0 30px; color:#173b35; background:#b9ded0; border-radius:50%; font-size:.72rem; font-weight:700; }
    .identity { min-width:0; }.identity strong { display:block; overflow:hidden; color:#eff8f3; font-size:.78rem; text-overflow:ellipsis; white-space:nowrap; }.identity span { display:block; margin-top:2px; color:#8fb6a9; font-size:.68rem; }
    .main { min-width:0; min-height:0; display:flex; flex-direction:column; background:#fff; }
    header { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:25px 34px 22px; border-bottom:1px solid var(--line); }
    .header-copy h1 { margin:0; font:700 1.52rem 'Space Grotesk',sans-serif; }.header-copy p { margin:6px 0 0; color:var(--muted); font-size:.84rem; }
    .live { display:flex; align-items:center; gap:7px; color:var(--teal); font-size:.75rem; font-weight:700; white-space:nowrap; }.live-dot { width:7px; height:7px; background:#55ae87; border-radius:50%; box-shadow:0 0 0 4px #e4f3eb; }
    #messages { flex:1; min-height:0; overflow-y:auto; overscroll-behavior:contain; scroll-behavior:smooth; padding:34px clamp(22px,6vw,78px); display:flex; flex-direction:column; gap:18px; }
    .empty { max-width:650px; margin:auto; text-align:center; animation:rise .4s ease both; }.empty-icon { display:grid; place-items:center; width:60px; height:60px; margin:0 auto 18px; color:#fff; background:var(--teal); border-radius:18px; font:700 1.15rem 'Space Grotesk',sans-serif; }
    .legend { max-width:520px; margin:14px auto 22px !important; padding:9px 12px; color:var(--muted) !important; background:#f6f9f7; border:1px solid #e0eae4; border-radius:7px; font-size:.76rem !important; line-height:1.5; }
    .empty h2 { margin:0; font:600 1.65rem 'Space Grotesk',sans-serif; }.empty p { margin:9px auto 26px; max-width:470px; color:var(--muted); line-height:1.5; }
    .quick { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; }.quick button { padding:9px 12px; color:var(--teal); background:#f1f7f3; border:1px solid #d4e8df; border-radius:6px; font-size:.78rem; font-weight:600; }.quick button:hover { background:var(--mint); border-color:#a9d3c3; }
    .message { max-width:min(650px,82%); padding:13px 16px; border-radius:12px; line-height:1.5; font-size:.91rem; animation:rise .25s ease both; }.message.agent { align-self:flex-start; color:var(--ink); background:#f1f5f1; border:1px solid #e3ebe5; border-bottom-left-radius:3px; }.message.user { align-self:flex-end; color:#fff; background:var(--teal); border-bottom-right-radius:3px; }.message strong { font-weight:700; }
    .meta { align-self:flex-start; max-width:min(650px,82%); margin-top:-10px; padding-left:4px; color:var(--muted); font-size:.7rem; line-height:1.65; }
    .typing { display:flex; align-items:center; gap:5px; width:auto; padding:16px 18px; }
    .typing i { width:6px; height:6px; background:#7f9a90; border-radius:50%; animation:pulse 1.1s ease-in-out infinite; }
    .typing i:nth-child(2) { animation-delay:.18s; }
    .typing i:nth-child(3) { animation-delay:.36s; }
    .cite { display:inline-block; margin:0 5px 3px 0; padding:1px 7px; color:var(--teal); background:#eaf4ee; border:1px solid #c8e2d5; border-radius:20px; font-size:.68rem; font-weight:700; }
    .tag { display:inline-block; margin-right:6px; padding:1px 7px; border-radius:4px; font-size:.66rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
    .tag.escalated { color:#8a3d17; background:#fbe4d8; }
    .tag.degraded { color:#7a5c12; background:#fbf0d0; }
    .tag.failed { color:#8a1f1f; background:#fadddd; }
    .diag { display:block; margin-top:3px; color:#9aa6a1; font-size:.66rem; font-family:ui-monospace,'Cascadia Mono',Consolas,monospace; }
    .composer { padding:16px 34px 26px; border-top:1px solid var(--line); background:#fff; } form { display:flex; align-items:flex-end; gap:10px; max-width:800px; margin:auto; }
    textarea { flex:1; min-height:48px; max-height:120px; padding:13px 14px; color:var(--ink); border:1px solid #cddad3; border-radius:8px; outline:0; resize:none; } textarea:focus { border-color:var(--teal); box-shadow:0 0 0 3px rgba(30,101,91,.1); }
    .send { display:grid; place-items:center; width:50px; height:48px; color:#fff; background:var(--coral); border:0; border-radius:8px; font-size:1.15rem; }.send:hover { background:#d85e42; }.send:disabled { opacity:.55; cursor:wait; }
    .composer-foot { display:flex; justify-content:space-between; max-width:800px; margin:8px auto 0; color:#8a9791; font-size:.68rem; }.new-session { padding:7px 10px; color:#d9eee5; background:transparent; border:1px solid rgba(217,238,229,.35); border-radius:6px; font-size:.72rem; }.new-session:hover { color:#173b35; background:#d9eee5; }
    @keyframes rise { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } } @keyframes pulse { 0%,100% { opacity:.35; transform:translateY(0); } 50% { opacity:1; transform:translateY(-2px); } }
    @media (max-width:760px) { body { background:#fff; }.app { display:flex; height:100vh; height:100dvh; }.app aside { display:none; } header { padding:20px; }.header-copy h1 { font-size:1.28rem; } #messages { padding:24px 16px; }.message { max-width:90%; }.composer { padding:12px 16px 18px; }.composer-foot { display:none; } }
  </style>
</head>
<body><div class="app">
  <aside>
    <div class="brand"><span class="brand-mark">T</span> trendly</div>
    <button class="new-session" id="new-session" type="button"><span class="nav-icon">+</span> New chat</button>
    <div class="recent-label">Today</div><button class="recent-chat" id="recent-chat" type="button">New support conversation</button>
    <div class="picker-label">Session</div>
    <div class="verify-card" id="verify-card">
      <span class="verify-dot" id="verify-dot"></span>
      <div><strong id="verify-state">Not verified</strong><span id="verify-detail">The agent will ask for your customer ID and order ID.</span></div>
    </div>
    <p class="switch-hint" id="switch-hint" hidden>Testing another customer? This chat is locked to the verified account &mdash; use <strong>+ New chat</strong>.</p>
    <dl class="profile" id="profile" hidden>
      <dt>Name</dt><dd id="p-name"></dd>
      <dt>Email</dt><dd id="p-email"></dd>
      <dt>Mobile</dt><dd id="p-mobile"></dd>
    </dl>
    <div class="account"><span class="avatar" id="avatar">?</span><div class="identity"><strong id="identity-name">Guest</strong><span id="customer-label">Identity not yet confirmed</span></div></div>
  </aside>
  <main class="main"><input type="hidden" id="session"><header><div class="header-copy"><h1>Trendly support agent</h1><p>Order checks, policy guidance, and safe customer handoffs</p></div><div class="live"><span class="live-dot"></span>Agent ready</div></header>
    <section id="messages" aria-live="polite"><div class="empty" id="empty"><div class="empty-icon">TS</div><h2>Ready when you are.</h2><p>Ask me to check an order, explain a return or exchange policy, or escalate an exception with the right context.</p><p class="legend">Every reply carries an inspection line beneath it — the tools that ran, the policy sections cited, and any case or return reference created. That is the audit trail, not part of the answer the customer reads.</p><div class="quick"><button type="button" data-message="What is the status of my order?">Where is my order?</button><button type="button" data-message="I want to return something I bought">Start a return</button><button type="button" data-message="What is Trendly's return window?">Ask about policy</button><button type="button" data-message="My parcel never arrived">Parcel not delivered</button><button type="button" data-message="Can I exchange an item for a different size?">Exchange an item</button></div></div></section>
    <div class="composer"><form id="chat"><textarea id="message" placeholder="Write a message..." required aria-label="Message" rows="1"></textarea><button class="send" id="send" type="submit" aria-label="Send message" title="Send message">↑</button></form><div class="composer-foot"><span>Support can help with order questions</span><span>Replies stay grounded in policy</span></div></div>
  </main>
</div>
<script>
  const messages = document.querySelector('#messages'), empty = document.querySelector('#empty'), form = document.querySelector('#chat'), input = document.querySelector('#message'), send = document.querySelector('#send'), session = document.querySelector('#session'), avatar = document.querySelector('#avatar'), identityName = document.querySelector('#identity-name'), customerLabel = document.querySelector('#customer-label'), verifyCard = document.querySelector('#verify-card'), verifyState = document.querySelector('#verify-state'), verifyDetail = document.querySelector('#verify-detail');
  // The browser never asserts who the customer is. Identity is claimed in the
  // conversation and confirmed by the server; this panel only reports what the
  // server decided, so it can never be the thing that grants access.
  const STATES = {
    unverified: ['Not verified', 'The agent will ask for your customer ID and order ID.'],
    identifiers_collected: ['Checking details', 'Matching the identifiers you gave against our records.'],
    verifying: ['Checking details', 'Matching the identifiers you gave against our records.'],
    verified: ['Verified', 'Confirmed by the server. Only this account is reachable in this chat.'],
    verification_failed: ['Not verified', 'Those details did not match an account. The agent will ask again.']
  };
  const profile = document.querySelector('#profile'), pName = document.querySelector('#p-name'), pEmail = document.querySelector('#p-email'), pMobile = document.querySelector('#p-mobile');
  function initials(name) { return name.split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0]).join('').toUpperCase() || '?'; }
  const switchHint = document.querySelector('#switch-hint');
  function resetIdentity() { switchHint.hidden = true; verifyCard.classList.remove('is-verified'); verifyState.textContent = STATES.unverified[0]; verifyDetail.textContent = STATES.unverified[1]; identityName.textContent = 'Guest'; avatar.textContent = '?'; customerLabel.textContent = 'Identity not yet confirmed'; profile.hidden = true; }
  function applyVerification(d) {
    const state = (d.diagnostics?.verification_state || 'unverified').toLowerCase(), copy = STATES[state] || STATES.unverified;
    verifyState.textContent = copy[0]; verifyDetail.textContent = copy[1];
    verifyCard.classList.toggle('is-verified', state === 'verified');
    // The panel is driven entirely by what the server returned. `customer` is
    // null until the turn actually reached VERIFIED, so there is nothing to
    // show before then and nothing the page can reveal on its own.
    switchHint.hidden = state !== 'verified';
    if (d.customer) {
      pName.textContent = d.customer.name; pEmail.textContent = d.customer.email; pMobile.textContent = d.customer.mobile;
      pEmail.title = d.customer.email; profile.hidden = false;
      identityName.textContent = d.customer.name; avatar.textContent = initials(d.customer.name);
      customerLabel.textContent = d.customer.customer_id + ' · verified';
    } else if (state === 'verification_failed') { resetIdentity(); verifyState.textContent = copy[0]; verifyDetail.textContent = copy[1]; }
  }
  function newSession() { session.value = 'browser-' + crypto.randomUUID(); clearMessages(); resetIdentity(); }
  function clearMessages() { messages.replaceChildren(); messages.append(empty); empty.hidden = false; }
  function render(text) {
    return String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/^\|[\s:|-]+\|$/gm,'')                                  // drop markdown table rules
      .replace(/^\|(.+)\|$/gm,(m,row)=>'• '+row.split('|').map(c=>c.trim()).filter(Boolean).join(' — '))
      .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g,'$1<em>$2</em>')   // single-asterisk italics, else they render raw
      .replace(/^[-*] (.*)$/gm,'• $1')
      .replace(/_([^_\n]+)_/g,'<em>$1</em>')
      .replace(/\n{3,}/g,'\n\n').replace(/\n/g,'<br>');
  }
  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  function add(text, kind, metaHtml='') { empty.hidden = true; const el=document.createElement('div'); el.className='message '+kind; el.innerHTML=render(text); messages.append(el); if(metaHtml){const m=document.createElement('div');m.className='meta';m.innerHTML=metaHtml;messages.append(m)} messages.scrollTop=messages.scrollHeight; }
  // Builds the audit line under a reply. Everything here comes from the response
  // body, so what the reviewer reads is what the turn actually did.
  function metaFor(d) {
    const line=[];
    if(d.status && d.status!=='completed') line.push('<span class="tag '+esc(d.status)+'">'+esc(d.status)+'</span>');
    line.push(d.mode==='llm'?'model-orchestrated':'fallback router');
    if(d.tool_trace?.length) line.push('grounded via '+esc(d.tool_trace.join(' → ')));
    if(d.actions?.length) line.push(esc(d.actions.map(a=>a.type+' ('+a.reference+')').join(', ')));
    let html=line.join('&nbsp; ·&nbsp; ');
    if(d.policy_sections?.length) html+='<br>cites '+d.policy_sections.map(s=>'<span class="cite">§'+esc(s)+'</span>').join('');
    const g=d.diagnostics;
    if(g){ const t=g.timings_ms&&Object.keys(g.timings_ms).length?'  '+Object.entries(g.timings_ms).map(([k,v])=>k+' '+Math.round(v)+'ms').join('  '):'';
      html+='<span class="diag">trace '+esc(g.trace_id||'—')+'  ·  '+g.agent_steps+' steps  ·  '+g.tool_calls+' tools  ·  '+Math.round(g.elapsed_ms)+'ms total'+esc(t)+(g.loop_limit_reached?'  ·  LOOP LIMIT':'')+'</span>'; }
    return html;
  }
  function showTyping() { const el=document.createElement('div'); el.className='message agent typing'; el.id='typing'; el.innerHTML='<i></i><i></i><i></i>'; messages.append(el); messages.scrollTop=messages.scrollHeight; }
  async function sendMessage(text) { const session_id=session.value.trim(); if(!text||!session_id)return; add(text,'user'); input.value=''; input.style.height='auto'; send.disabled=true; showTyping(); try { const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,session_id})}); const data=await r.json(); if(!r.ok) throw new Error(data.detail||'Request failed'); document.querySelector('#typing')?.remove(); applyVerification(data); add(data.message,'agent',metaFor(data)); } catch(err) { document.querySelector('#typing')?.remove(); add('I could not reach support right now: '+err.message,'agent'); } finally { send.disabled=false; input.focus(); } }
  form.addEventListener('submit', e => { e.preventDefault(); sendMessage(input.value.trim()); }); input.addEventListener('input', () => { input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,120)+'px'; });
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!send.disabled) sendMessage(input.value.trim()); } });
  document.querySelector('#new-session').addEventListener('click', () => { newSession(); input.focus(); }); document.querySelector('#recent-chat').addEventListener('click', () => { input.focus(); });
  document.querySelectorAll('[data-message]').forEach(b => b.addEventListener('click', () => { sendMessage(b.dataset.message); }));
  window.addEventListener('pageshow', newSession); newSession();
</script></body></html>"""


def chat_page() -> HTMLResponse:
    return HTMLResponse(CHAT_PAGE)


def landing_page() -> HTMLResponse:
  return HTMLResponse(LANDING_PAGE)
