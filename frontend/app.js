(function(){
  const $ = s=>document.querySelector(s);
  const progressPanel = $('#progressPanel');
  const bar = $('#bar'), pctText = $('#pctText'), stageText = $('#stageText'), etaText = $('#etaText'), streetText = $('#streetText');
  const equityText = $('#equity-text'), equityBar = $('#equity-bar');
  const adviceBox = $('#advice'), oddsText = $('#odds-text');

  let es = null, currentTask = null;

  function secsToHHMMSS(s){
    if(s==null) return "";
    s = Math.max(0, s|0);
    const h = (s/3600)|0, m = ((s%3600)/60)|0, sec = s%60;
    if(h>0) return `${h}h ${m}m ${sec}s`;
    if(m>0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }

  function calcPotOdds(callAmt, potAmt){
    if(!callAmt || !potAmt) return null;
    const c = Number(callAmt), p = Number(potAmt);
    if(!(c>0) || !(p>=0)) return null;
    return c / (c + p); // 需要的最小胜率
  }

  function pickStreet({flop, turn, river}){
    if(river) return 'River';
    if(turn && flop) return 'Turn';
    if(flop) return 'Flop';
    return null;
  }

  async function start(){
    if(!window.API_BASE){
      alert('未配置后端地址：请在 config.js 中设置 window.API_BASE');
      return;
    }

    // 读取输入
    const get = id => (document.getElementById(id)?.value || '').trim();
    const hero = [get('hole1'), get('hole2')].filter(Boolean).join(' ');
    const flop = [get('b1'), get('b2'), get('b3')].filter(Boolean).join(' ');
    const turn = get('b4');
    const river = get('b5');
    const pot  = get('pot');     // 前端命名
    const call = get('call');    // 前端命名

    // 组装后端字段
    const fd = new FormData();
    fd.append('hero', hero);
    if(flop) fd.append('flop', flop);
    if(turn) fd.append('turn', turn);
    if(river) fd.append('river', river);
    fd.append('villains', '2');             // 你也可以改成用户可选
    if(pot)  fd.append('pot_bb', pot);      // 映射到后端字段

    // 把 call* 映射到“当前所到达的街”
    const street = pickStreet({flop, turn, river});
    if(call && street){
      if(street==='Flop')  fd.append('call_flop',  call);
      if(street==='Turn')  fd.append('call_turn',  call);
      if(street==='River') fd.append('call_river', call);
    }

    // UI 重置
    progressPanel.style.display = 'block';
    bar.style.width = '0%'; pctText.textContent = '0%';
    stageText.textContent = '排队中…'; etaText.textContent=''; streetText.textContent='';
    equityText && (equityText.textContent = '—');
    equityBar  && (equityBar.style.width = '0%');
    adviceBox  && (adviceBox.textContent = '// 等待后端…');

    // 本地计算并显示 Pot Odds（方便对齐后端建议）
    const po = calcPotOdds(call, pot);
    oddsText && (oddsText.textContent = po!=null ? `${(po*100).toFixed(2)}%` : '—');

    // 调后端 /start
    let resp;
    try{
      resp = await fetch(`${window.API_BASE}/start`, { method:'POST', body: fd });
    }catch(e){
      stageText.textContent = '网络错误：无法连接后端';
      return;
    }
    if(!resp.ok){
      stageText.textContent = '启动失败';
      try{
        const err = await resp.json();
        if(err && err.error) stageText.textContent = `启动失败：${err.error}`;
      }catch(_){}
      return;
    }

    const { task_id } = await resp.json();
    currentTask = task_id;

    // 订阅 SSE
    if(es) es.close();
    es = new EventSource(`${window.API_BASE}/stream/${task_id}`);
    es.onmessage = (evt)=>{
      const s = JSON.parse(evt.data);

      // 进度条
      bar.style.width = (s.pct||0) + '%';
      pctText.textContent = (s.pct||0) + '%';
      stageText.textContent = s.stage || '';
      etaText.textContent = s.eta!=null ? ('ETA ' + secsToHHMMSS(s.eta)) : '';
      streetText.textContent = s.detail && s.detail.street ? ('当前街：' + s.detail.street) : '';

      // 渲染“当前街”的结果（后端会逐街追加 results）
      if(Array.isArray(s.results) && s.results.length){
        const last = s.results[s.results.length - 1];

        // 胜率
        if(equityText){
          const eq = Number(last.equity || 0);
          equityText.textContent = isFinite(eq) ? (eq*100).toFixed(2)+'%' : '—';
          equityBar.style.width  = isFinite(eq) ? (eq*100).toFixed(2)+'%' : '0%';
        }

        // 建议
        if(adviceBox){
          adviceBox.innerHTML = (last.advice_text || '').replaceAll('\n','<br/>') || '// 暂无建议';
        }
      }

      if(s.done){
        es.close();
        stageText.textContent='完成';
        etaText.textContent='';
        bar.style.width='100%'; pctText.textContent='100%';
      }
    };

    es.addEventListener('error', ()=>{
      // SSE 断线时浏览器会自动尝试重连；这里做个友好提示
      stageText.textContent = '连接中断，重试中…';
    });
  }

  // 提供给 index.html 调用
  window.__RUN_SIM__ = start;
})();
