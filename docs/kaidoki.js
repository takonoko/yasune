// 「お得な買い時」：近い日のポイント付与率を出して、高い日を案内する。
// 付与率はYahoo!ショッピングアプリの「お得な買い時」を見て、スプレッドシートに手で書く。
// シートに無い日でも「5のつく日」（5・15・25日）は自動で出す。
(function(){
  const SHEET="1AdcrGOlsK1kkmGUhZemJIdOn1YarvPD0fuUT_uiqb4E";
  const box=document.getElementById("kaidoki");
  if(!box) return;
  const css=document.createElement("style");
  css.textContent=`.kd{background:var(--surface);border-radius:14px;padding:10px 14px;margin:14px 0 0;font-size:.85rem;line-height:1.6}
.kd h3{font:700 .95rem var(--round);margin:0 0 6px}
.kd .days{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}
.kd .day{flex:0 0 auto;border:1px solid var(--line);border-radius:10px;padding:4px 10px;text-align:center;font-variant-numeric:tabular-nums}
.kd .day b{display:block;font:700 1.05rem var(--round)}
.kd .day small{display:block;font-size:.7rem;color:var(--muted)}
.kd .day.top{border-color:var(--accent);background:var(--accent-soft);color:var(--accent-ink)}
.kd .day.top small{color:inherit}
.kd .tip{margin:6px 0 0;font-weight:700;color:var(--accent-ink)}
.kd .src{margin:4px 0 0;font-size:.72rem;color:var(--muted)}`;
  document.head.append(css);
  const W=["日","月","火","水","木","金","土"];
  // 日本時間の今日（0時）
  const jst=new Date(Date.now()+9*36e5); const today=Date.UTC(jst.getUTCFullYear(),jst.getUTCMonth(),jst.getUTCDate());
  const label=t=>{const d=new Date(t);const diff=Math.round((t-today)/864e5);
    const md=`${d.getUTCMonth()+1}/${d.getUTCDate()}(${W[d.getUTCDay()]})`;return diff===0?`今日 ${md}`:diff===1?`明日 ${md}`:md};
  function parse(t){return t.trim().split(/\r?\n/).slice(1).map(l=>[...l.matchAll(/"((?:[^"]|"")*)"/g)].map(m=>m[1].replace(/""/g,'"')))}
  function toDay(s){const n=(s||"").match(/\d+/g);if(!n||n.length<3)return null;
    let [a,b,c]=n.map(Number);const y=a>999?a:c,m=a>999?b:a,d=a>999?c:b;return Date.UTC(y,m-1,d)}
  function show(rows){
    const map=new Map();
    rows.forEach(r=>{const t=toDay(r[0]);const rate=parseFloat(String(r[1]||"").replace(/[^\d.]/g,""));
      if(t!=null&&t>=today&&t<today+14*864e5) map.set(t,{t,rate:isNaN(rate)?null:rate,memo:r[2]||""})});
    // 5のつく日は、シートに無くても近いものを足す
    for(let i=0;i<11;i++){const t=today+i*864e5;const d=new Date(t).getUTCDate();
      if([5,15,25].includes(d)){const o=map.get(t)||{t,rate:null,memo:""};if(!/5のつく日/.test(o.memo))o.memo=(o.memo?o.memo+"・":"")+"5のつく日";map.set(t,o)}}
    const list=[...map.values()].sort((a,b)=>a.t-b.t).slice(0,7);
    if(!list.length){box.hidden=true;return}
    const rated=list.filter(x=>x.rate!=null);
    const max=rated.length?Math.max(...rated.map(x=>x.rate)):null;
    const best=rated.find(x=>x.rate===max);
    const now=list.find(x=>x.t===today);
    let tip="";
    if(best&&now&&now.rate!=null&&best.t!==today&&best.rate-now.rate>=2) tip=`急ぎでなければ ${label(best.t)} がおすすめ（最大${best.rate}%、今日より+${Math.round((best.rate-now.rate)*10)/10}%）`;
    else if(best&&best.t===today) tip=`今日が近い日の中でいちばん高還元（最大${best.rate}%）`;
    else if(best&&!now) tip=`近い日の中では ${label(best.t)} がいちばん高還元（最大${best.rate}%）`;
    box.innerHTML=`<div class="kd"><h3>お得な買い時（ポイント付与率）</h3>
      <div class="days">${list.map(x=>`<div class="day${best&&x.t===best.t?" top":""}"><small>${label(x.t)}</small><b>${x.rate!=null?`最大${x.rate}%`:"−"}</b>${x.memo?`<small>${x.memo.replace(/[&<>"]/g,"")}</small>`:""}</div>`).join("")}</div>
      ${tip?`<p class="tip">${tip}</p>`:""}
      <p class="src">Yahoo!ショッピングアプリの「お得な買い時」で見た最大の付与率です。エントリーや支払い方法などの条件・上限があり、変わることもあります。</p></div>`;
    box.hidden=false;
  }
  fetch(`https://docs.google.com/spreadsheets/d/${SHEET}/gviz/tq?tqx=out:csv`).then(r=>r.text()).then(t=>show(parse(t))).catch(()=>show([]));
})();
