import{evidence}from'../stores/evidence.js';import{getEvidence,exportEvidence}from'../api/evidence.js';import{createClaim}from'../api/batch.js';import{header}from'../components/common/header.js';import{table}from'../components/common/table.js';import{badge}from'../components/common/badge.js';import{esc,json}from'../utils/html.js';import{notify,submit}from'../hooks/feedback.js';import{session}from'../stores/session.js';

const pendingKey=batchId=>'qr-claim-pending-'+batchId;
const fmt=value=>value==null?'（空）':String(value);
const byIndex=rows=>new Map((rows||[]).map(r=>[Number(r.index),r]));
const pairLabel=(pairs,left,right)=>{const L=byIndex(left),R=byIndex(right);return pairs.map(([li,rj])=>{const l=L.get(Number(li)),r=R.get(Number(rj));return `左#${li}（${esc(fmt(l?.customer))} / ${esc(fmt(l?.route))} / ${esc(fmt(l?.amount))}）↔ 右#${rj}（${esc(fmt(r?.customer))} / ${esc(fmt(r?.route))} / ${esc(fmt(r?.amount))}）`}).join('<br>')};

function rowTable(rows,title){
  return `<h2>${title}</h2>${table(['行号','客户','线路','金额'],rows.slice(0,60).map(r=>[`<span class="mono">#${r.index}</span>`,esc(r.customer),esc(r.route),esc(r.amount)]))}<p class="helper">行号用于认领；最多预览 60 行，完整结果在 JSON 中。</p>`;
}

function ticketlessPanel(t,batchId){
  const write=session.get().role!=='viewer';
  const left=t.left_rows||[],right=t.right_rows||[];
  const L=byIndex(left),R=byIndex(right);
  let stateBlock='';
  if(t.status==='passed'){
    stateBlock=`<p class="helper">全配且配法唯一：左侧 ${t.pair_count} 行与右侧 ${t.pair_count} 行一一配对，没有第二种配法。批次结论为通过。</p>`;
  }else if(t.status==='different'){
    stateBlock=`<p class="error">无法配齐：${esc(t.issue||'存在无法配对的结算行')}。没有可认领的全配方案。</p>`;
  }else if(t.status==='pending_claim'){
    stateBlock=`<p class="helper">全配存在但配法不唯一，页面待认领。每次可只选一对；固定后剩余行仍须能配齐，全部唯一后才通过。${t.candidates_truncated?'候选较多，仅列出部分。':''}</p>`;
  }else if(t.status==='claim_conflict'){
    stateBlock=`<p class="error">${esc(t.issue||'已入库认领合并后无法配齐，请重新认领')}</p>`;
  }
  const classes=t.candidate_classes||[];
  const picker=classes.length?`<form id="claim-form"><h2>选择要认领的配对</h2><p class="helper">每行是一个“金额类别对”；同值重复行互换不算另一种配法，认领时取该类别中各一份（列出全部可用行号供核对）。可只选一对，也可一次选多对；后端会合并全部已认领依据后重验。</p><div class="claim-list">${classes.map(c=>`<label class="claim-row"><input type="checkbox" name="pair" value="${c.left_rows[0]},${c.right_rows[0]}"> ${esc(fmt(c.customer))} / ${esc(fmt(c.route))}：左 <span class="mono">${esc(c.left_amount)}</span>（行 ${c.left_rows.map(i=>'#'+i).join('、')}）↔ 右 <span class="mono">${esc(c.right_amount)}</span>（行 ${c.right_rows.map(i=>'#'+i).join('、')}）</label>`).join('')}</div><div class="actions"><button class="primary" type="submit"${write?'':' disabled'}>认领所选配对 →</button><span class="muted">认领依据与结论同一条记录入库</span></div></form>`:'';
  const claims=t.claims?.length?`<h2>认领记录</h2>${table(['时间','账号','依据','合并结论'],t.claims.map(c=>[esc(c.created_at),esc(c.actor),pairLabel(c.pairs,left,right),esc(c.conclusion.status==='passed'?'通过（全配唯一）':c.conclusion.status)]))}`:'';
  const examples=t.example_matchings?.length?`<h2>两种配法示例</h2><pre class="json">${json(t.example_matchings)}</pre>`:'';
  return `<section class="panel"><div class="actions" style="margin-top:0"><h2 style="margin:0">无单号对账</h2>${badge(t.status)}<span class="muted">容差 ${esc(t.tolerance)} · ${esc(t.rule)}</span></div>${stateBlock}${picker}${claims}${examples}<details ${t.status==='pending_claim'?'':'open'}><summary>当前配对明细</summary><pre class="json">${json({status:t.status,matching_count:t.matching_count,matching:t.matching,issue:t.issue})}</pre></details>${rowTable(left,'原始结果行')}${rowTable(right,'候选结果行')}</section>`;
}

export async function evidencePage(el){
  const records=await evidence.load();
  const ident=Number(location.hash.split('/')[1])||records[0]?.batch_id;
  const detail=ident?await getEvidence(ident):null;
  const r=detail?.result;
  const tl=r?.ticketless;
  el.innerHTML=header('04','从结果一直查到执行计划','证据来源于 PostgreSQL 实际执行。保留两侧行数、摘要、查询计划和差异行，供结算复核人独立检查。')+`<section class="panel"><label>选择复核证据</label><select id="choose"><option value="">请选择已完成批次</option>${records.map(x=>{const s=x.ticketless_status?({passed:'无单号通过',different:'无单号差异',pending_claim:'待认领',claim_conflict:'认领冲突'}[x.batch_status||x.ticketless_status]||'无单号对账'):(x.equivalent?'结果一致':'存在差异');return `<option value="${x.batch_id}" ${x.batch_id===ident?'selected':''}>批次 #${x.batch_id} · ${x.error||x.ticketless_error?'执行失败':s}</option>`}).join('')}</select></section>`+(r?`<section class="panel"><div class="actions" style="margin-top:0"><h2 style="margin:0">复核批次 #${ident}</h2>${badge(r.error||r.ticketless_error?'failed':tl?tl.status:r.equivalent?'passed':'different')}<button id="export">下载 JSON 证据 ↓</button></div>${r.error||r.ticketless_error?'<p class="error">'+esc(r.error||r.ticketless_error)+'</p>':''}${r.ticketless_error?'<p class="helper">精确比对结论如下；无单号对账未生成，因为结果列或金额不符合口径。</p>':''}${r.error?'':`<div class="metrics"><div><small>原始结果行数</small><strong>${r.original.row_count}</strong></div><div><small>候选结果行数</small><strong>${r.candidate.row_count}</strong></div><div><small>原始 / 候选查询耗时 ms</small><strong>${r.original.elapsed_ms} / ${r.candidate.elapsed_ms}</strong></div></div><p class="helper">${esc(r.comparison)}<br>${esc(r.note)}</p><div class="split"><div><h2>候选实际结果</h2>${table(r.candidate.columns,r.candidate.rows.slice(0,20).map(row=>row.map(esc)))}<p class="helper">最多预览20行，完整结果在 JSON 中。</p><h2>差异定位</h2><pre class="json">${json({equivalent:r.equivalent,missing_rows:r.missing_rows,extra_rows:r.extra_rows})}</pre></div><aside><h2>结果摘要 SHA-256</h2><pre class="json">原始：${esc(r.original_checksum)}
候选：${esc(r.candidate_checksum)}</pre><details open><summary>候选 EXPLAIN ANALYZE</summary><pre class="json">${json(r.candidate.plan)}</pre></details><details><summary>原始 EXPLAIN ANALYZE</summary><pre class="json">${json(r.original.plan)}</pre></details></aside></div>`}</section>`+(tl?ticketlessPanel(tl,ident):''):'<div class="empty">还没有执行证据。到 SQL 复核页提交一份候选 SQL，worker 完成后在这里查看。</div>');
  el.querySelector('#choose').onchange=e=>{if(e.target.value)location.hash='evidence/'+e.target.value};
  const b=el.querySelector('#export');if(b)b.onclick=()=>exportEvidence(ident).catch(e=>notify(e.message));
  const form=el.querySelector('#claim-form');
  if(form){
    const keyName=pendingKey(ident);
    form.onsubmit=e=>{
      e.preventDefault();
      const pairs=[...form.querySelectorAll('input[name=pair]:checked')].map(x=>x.value.split(',').map(Number));
      if(!pairs.length)return notify('请至少选择一对要认领的配对');
      submit(form,async()=>{
        // 幂等键只在“同一批选择”重试时复用：断线重试会取回原结果，不会重复认领；
        // 改了选择就换新键，避免把新认领误判成旧请求的重试。
        const fingerprint=JSON.stringify(pairs);
        let saved=null;
        try{saved=JSON.parse(localStorage.getItem(keyName)||'null')}catch(_){}
        let key=saved&&saved.fingerprint===fingerprint?saved.key:null;
        if(!key){key='claim-'+ident+'-'+Date.now()+'-'+Math.random().toString(36).slice(2,8);localStorage.setItem(keyName,JSON.stringify({key,fingerprint}))}
        try{
          const claim=await createClaim(ident,{pairs,idempotency_key:key});
          localStorage.removeItem(keyName);
          notify('认领 #'+claim.id+' 已生效：'+(claim.conclusion.status==='passed'?'合并后全配唯一，批次通过':'仍需继续认领'));
          await evidencePage(el);
        }catch(err){
          if(err.status===409){
            // 服务端明确拒绝（合并后无解）：本次没有入库，清掉键让用户重选。
            localStorage.removeItem(keyName);
            throw new Error(err.message+'；请重新勾选能配齐的配对');
          }
          // 断线等结果不确定的情况保留键，重试会返回原来入库的结论。
          throw err;
        }
      });
    };
  }
}
