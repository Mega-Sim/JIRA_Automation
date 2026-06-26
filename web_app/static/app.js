/* ── 전역 상태 ── */
let issues = [];          // 현재 검색된 이슈 목록
let selected = {};        // { key: bool }
let weeklyKeys = new Set();
let currentMode = 'not_target';
let sortState = { col: null, desc: false };
let validateES = null;    // EventSource

const BASE_URL = () => document.getElementById('base-url').value.trim().replace(/\/+$/, '');

/* ── 유틸 ── */
function setStatus(msg) {
  document.getElementById('status-msg').textContent = msg;
}

function appendLog(msg, cls) {
  const el = document.getElementById('log-area');
  const div = document.createElement('div');
  div.textContent = msg;
  if (cls) div.className = cls;
  // FAIL 포함 시 강조
  if (!cls && /FAIL/i.test(msg)) div.className = 'log-fail';
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
}

function appendLogs(logs) {
  (logs || []).forEach(l => appendLog(l));
}

function getCredentials() {
  return {
    base_url: document.getElementById('base-url').value.trim(),
    user: document.getElementById('user-id').value,
    password: document.getElementById('user-pw').value,
    verify_ssl: document.getElementById('ssl-verify').checked,
  };
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

/* ── 로그인 ── */
async function checkLogin() {
  const btn = document.getElementById('btn-login');
  const lbl = document.getElementById('login-status');
  btn.disabled = true;
  lbl.textContent = '확인 중...';
  lbl.className = 'login-status';
  try {
    const creds = getCredentials();
    const res = await postJSON('/api/login', creds);
    if (res.ok) {
      lbl.textContent = `로그인 성공: ${res.name || ''}`;
      lbl.className = 'login-status ok';
      appendLog(`[로그인 성공] ${res.name || ''}`);
    } else {
      lbl.textContent = '로그인 실패';
      lbl.className = 'login-status fail';
      appendLog(`[로그인 실패] ${res.error || ''}`);
    }
  } catch (e) {
    lbl.textContent = '로그인 실패';
    lbl.className = 'login-status fail';
    appendLog(`[로그인 오류] ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('user-pw').addEventListener('keydown', e => {
  if (e.key === 'Enter') checkLogin();
});
document.getElementById('user-id').addEventListener('keydown', e => {
  if (e.key === 'Enter') checkLogin();
});

/* ── 프리셋 ── */
function onSccbNotTarget() {
  currentMode = 'not_target';
  document.getElementById('jql-input').value =
    '(category = AMHS_SW or category = AMHS) AND (status = "In Verification" or status = "Approval") ' +
    'AND ("개발 DR 완료일" >= 2022-01-01) AND ("SCCB 상태" = "미 대상") ' +
    'ORDER BY updated DESC';
  onSearch();
}

function onSccbTarget() {
  currentMode = 'target';
  document.getElementById('jql-input').value =
    '(category = AMHS_SW or category = AMHS) AND (status = "In Verification" or status = "Approval") ' +
    'AND ("개발 DR 완료일" >= 2022-01-01) ' +
    'AND ("SCCB 상태" is EMPTY OR "SCCB 상태" = "SCCB 완료" OR "SCCB 상태" = "사전 SCCB 의뢰 완료") ' +
    'ORDER BY updated DESC';
  onSearch();
}

function onVocComplete() {
  currentMode = 'voc_complete';
  onSearch();
}

/* ── 검색 ── */
async function onSearch() {
  // 진행 중인 검증 SSE 종료
  if (validateES) { validateES.close(); validateES = null; }

  const jql = document.getElementById('jql-input').value.trim();
  const weeklyUrl = document.getElementById('weekly-url').value.trim();
  const maxResults = parseInt(document.getElementById('max-results').value) || 50;

  if (currentMode !== 'voc_complete' && !jql) {
    alert('JQL이 비어 있습니다.');
    return;
  }

  setStatus('검색 중...');
  clearTable();

  try {
    const res = await postJSON('/api/search', {
      mode: currentMode,
      jql,
      weekly_url: weeklyUrl,
      max_results: maxResults,
    });

    appendLogs(res.logs);

    if (!res.ok) {
      appendLog(`[Search 실패] ${res.error || ''}`, 'log-fail');
      setStatus('0 issues');
      return;
    }

    issues = res.issues || [];
    weeklyKeys = new Set(res.weekly_keys || []);
    selected = {};
    issues.forEach(i => { selected[i.key] = false; });

    renderTable();

    const matched = issues.filter(i => weeklyKeys.has(i.key)).length;
    let statusMsg = `${issues.length} issues`;
    if (res.total > issues.length) statusMsg += ` (표시: ${issues.length}/${res.total})`;
    if (weeklyKeys.size) statusMsg += ` / 주간SCCB 일치 ${matched}건`;
    setStatus(statusMsg);

    // SCCB 대상 모드: 검증 스트림 시작
    if (currentMode === 'target' && issues.length > 0) {
      startValidation(issues.map(i => i.key));
    }
  } catch (e) {
    appendLog(`[Search 오류] ${e.message}`, 'log-fail');
    setStatus('0 issues');
  }
}

/* ── 검증 스트리밍 ── */
async function startValidation(keys) {
  const startRes = await postJSON('/api/validate/start', { keys });
  if (!startRes.ok) {
    appendLog(`[검증 시작 실패] ${startRes.error || ''}`, 'log-fail');
    return;
  }
  const jobId = startRes.job_id;
  let completed = 0;

  validateES = new EventSource(`/api/validate/stream/${jobId}`);
  validateES.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }

    if (msg.type === 'update') {
      const d = msg.data;
      updateIssueRow(d.key, d);
      completed++;
      setStatus(`${issues.length} issues (검증 중: ${completed}/${keys.length})`);
    } else if (msg.type === 'done') {
      validateES.close();
      validateES = null;
      setStatus(`${issues.length} issues (검증 완료)`);
    }
  };
  validateES.onerror = () => {
    validateES.close();
    validateES = null;
  };
}

function updateIssueRow(key, data) {
  const idx = issues.findIndex(i => i.key === key);
  if (idx === -1) return;
  Object.assign(issues[idx], data);
  const row = document.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
  if (!row) return;
  const cols = ['body_len', 'rollout', 'err_table', 'links', 'tcgen', 'aio_test', 'pr_merge'];
  cols.forEach(col => {
    const td = row.querySelector(`td[data-col="${col}"]`);
    if (td && data[col] !== undefined) {
      td.textContent = data[col];
      td.className = valClass(col, data[col]);
    }
  });
}

/* ── 테이블 렌더링 ── */
function clearTable() {
  document.getElementById('result-tbody').innerHTML = '';
  issues = [];
  selected = {};
  weeklyKeys = new Set();
  document.getElementById('select-all').checked = false;
}

function valClass(col, val) {
  if (!val || val === '...') return 'val-pend';
  const v = val.toUpperCase();
  if (col === 'pr_merge') {
    if (v === 'MERGED') return 'val-ok';
    if (v.startsWith('OPEN') || v === 'NONE' || v.startsWith('ERR')) return 'val-fail';
    return '';
  }
  if (col === 'body_len') {
    const m = val.match(/(\d+)/);
    if (m && parseInt(m[1]) < 300) return 'body-low';
    return '';
  }
  if (v === 'OK' || v.startsWith('OK(')) return 'val-ok';
  if (v === 'FAIL' || v.startsWith('FAIL(')) return 'val-fail';
  if (v === 'ERR' || v.startsWith('ERR(')) return 'val-err';
  return '';
}

function renderTable() {
  const tbody = document.getElementById('result-tbody');
  tbody.innerHTML = '';
  issues.forEach(issue => {
    tbody.appendChild(makeRow(issue));
  });
  applySort();
}

function makeRow(issue) {
  const tr = document.createElement('tr');
  tr.dataset.key = issue.key;
  if (weeklyKeys.has(issue.key)) tr.classList.add('weekly-match');

  // 선택 체크박스
  const tdSel = document.createElement('td');
  tdSel.className = 'col-sel';
  const chk = document.createElement('input');
  chk.type = 'checkbox';
  chk.checked = !!selected[issue.key];
  chk.onchange = () => {
    selected[issue.key] = chk.checked;
    refreshSelectAll();
  };
  tdSel.appendChild(chk);
  tr.appendChild(tdSel);

  // KEY (클릭 → 새 탭)
  const tdKey = document.createElement('td');
  tdKey.className = 'col-key';
  tdKey.textContent = issue.key;
  tdKey.dataset.col = 'key';
  tdKey.title = `${BASE_URL()}/browse/${issue.key}`;
  tdKey.onclick = () => window.open(`${BASE_URL()}/browse/${issue.key}`, '_blank');
  tr.appendChild(tdKey);

  // 나머지 컬럼
  const textCols = [
    { col: 'summary',   cls: 'col-summary' },
    { col: 'body_len',  cls: '' },
    { col: 'rollout',   cls: '' },
    { col: 'err_table', cls: '' },
    { col: 'links',     cls: '' },
    { col: 'tcgen',     cls: '' },
    { col: 'aio_test',  cls: '' },
    { col: 'pr_merge',  cls: '' },
    { col: 'status',    cls: '' },
    { col: 'assignee',  cls: '' },
    { col: 'duedate',   cls: '' },
  ];
  textCols.forEach(({ col, cls }) => {
    const td = document.createElement('td');
    td.dataset.col = col;
    td.textContent = issue[col] || '';
    td.title = issue[col] || '';
    if (cls) td.className = cls;
    else td.className = valClass(col, issue[col] || '');
    tr.appendChild(td);
  });

  return tr;
}

/* ── 전체 선택 ── */
function toggleSelectAll(checked) {
  issues.forEach(i => { selected[i.key] = checked; });
  document.querySelectorAll('#result-tbody input[type="checkbox"]').forEach(chk => {
    chk.checked = checked;
  });
}

function refreshSelectAll() {
  const vals = Object.values(selected);
  const allChk = document.getElementById('select-all');
  if (!vals.length) { allChk.checked = false; allChk.indeterminate = false; return; }
  const trueCount = vals.filter(Boolean).length;
  if (trueCount === vals.length) { allChk.checked = true; allChk.indeterminate = false; }
  else if (trueCount === 0) { allChk.checked = false; allChk.indeterminate = false; }
  else { allChk.checked = false; allChk.indeterminate = true; }
}

/* ── 정렬 ── */
function sortTable(col) {
  if (sortState.col === col) {
    sortState.desc = !sortState.desc;
  } else {
    sortState.col = col;
    sortState.desc = false;
  }
  applySort();
  updateSortIndicators();
}

function parseSortVal(col, val) {
  const s = (val || '').toString().trim();
  if (col === 'body_len') {
    const m = s.match(/(\d+)/);
    return m ? parseInt(m[1]) : Infinity;
  }
  if (col === 'duedate') {
    return s ? new Date(s).getTime() : Infinity;
  }
  return s.toLowerCase();
}

function applySort() {
  if (!sortState.col) return;
  const col = sortState.col;
  issues.sort((a, b) => {
    const va = parseSortVal(col, a[col]);
    const vb = parseSortVal(col, b[col]);
    if (va < vb) return sortState.desc ? 1 : -1;
    if (va > vb) return sortState.desc ? -1 : 1;
    return 0;
  });
  renderTableRows();
}

function renderTableRows() {
  const tbody = document.getElementById('result-tbody');
  tbody.innerHTML = '';
  issues.forEach(issue => {
    tbody.appendChild(makeRow(issue));
  });
  // 선택 상태 복원
  issues.forEach(issue => {
    const tr = document.querySelector(`tr[data-key="${CSS.escape(issue.key)}"]`);
    if (tr) {
      const chk = tr.querySelector('input[type="checkbox"]');
      if (chk) chk.checked = !!selected[issue.key];
    }
  });
}

function updateSortIndicators() {
  document.querySelectorAll('thead th').forEach(th => {
    const col = th.dataset.col;
    th.classList.remove('sort-asc', 'sort-desc');
    if (col === sortState.col) {
      th.classList.add(sortState.desc ? 'sort-desc' : 'sort-asc');
    }
  });
}

/* ── 이슈 열기 ── */
function openSelected() {
  const keys = Object.entries(selected).filter(([, v]) => v).map(([k]) => k);
  if (!keys.length) { alert('선택된 이슈가 없습니다.'); return; }
  window.open(`${BASE_URL()}/browse/${keys[0]}`, '_blank');
}

/* ── Approval 전이 ── */
async function onApproval() {
  const keys = Object.entries(selected).filter(([, v]) => v).map(([k]) => k);
  if (!keys.length) { alert('처리할 이슈를 선택하세요.'); return; }
  if (!confirm(`${keys.length}건을 Approval 상태로 전이하시겠습니까?\n${keys.join(', ')}`)) return;

  setStatus('Approval 전이 중...');
  try {
    const res = await postJSON('/api/transition/approval', { keys, mode: currentMode });
    appendLogs(res.logs);
    if (res.statuses) {
      Object.entries(res.statuses).forEach(([k, st]) => {
        const idx = issues.findIndex(i => i.key === k);
        if (idx !== -1) issues[idx].status = st;
        const td = document.querySelector(`tr[data-key="${CSS.escape(k)}"] td[data-col="status"]`);
        if (td) td.textContent = st;
      });
    }
    setStatus(res.ok ? 'Approval 전이 완료' : `실패: ${res.error || ''}`);
  } catch (e) {
    appendLog(`[Approval 오류] ${e.message}`, 'log-fail');
    setStatus('오류');
  }
}

/* ── Complete 전이 ── */
async function onComplete() {
  const keys = Object.entries(selected).filter(([, v]) => v).map(([k]) => k);
  if (!keys.length) { alert('처리할 이슈를 선택하세요.'); return; }
  const label = currentMode === 'voc_complete' ? 'VOC 완료처리' : 'Complete 처리';
  if (!confirm(`${keys.length}건을 ${label}하시겠습니까?\n${keys.join(', ')}`)) return;

  setStatus(`${label} 중...`);
  try {
    const res = await postJSON('/api/transition/complete', { keys, mode: currentMode });
    appendLogs(res.logs);
    if (res.statuses) {
      Object.entries(res.statuses).forEach(([k, st]) => {
        const idx = issues.findIndex(i => i.key === k);
        if (idx !== -1) issues[idx].status = st;
        const td = document.querySelector(`tr[data-key="${CSS.escape(k)}"] td[data-col="status"]`);
        if (td) td.textContent = st;
      });
    }
    setStatus(res.ok ? `${label} 완료` : `실패: ${res.error || ''}`);
  } catch (e) {
    appendLog(`[Complete 오류] ${e.message}`, 'log-fail');
    setStatus('오류');
  }
}

/* ── SCCB 페이지 생성 ── */
async function onCreateSccbPage() {
  const weeklyUrl = document.getElementById('weekly-url').value.trim();
  if (!weeklyUrl) { alert('이번주 SCCB URL을 입력하세요.'); return; }
  setStatus('SCCB Page 생성 중...');
  appendLog(`[SCCB Page] 이전 주 페이지 포맷 복제 시작 - 원본: ${weeklyUrl}`);
  try {
    const res = await postJSON('/api/create_sccb_page', { weekly_url: weeklyUrl });
    if (res.ok) {
      const r = res.result || {};
      const created = r.created ? '생성 완료' : '이미 존재';
      let msg = `[SCCB Page] ${created}: ${r.title || ''}`;
      if (r.source_title) msg += ` (원본: ${r.source_title})`;
      if (r.yearly_parent_title) {
        msg += r.yearly_parent_created
          ? ` / 상위 페이지 생성: ${r.yearly_parent_title}`
          : ` / 상위 페이지 사용: ${r.yearly_parent_title}`;
      }
      if (r.url) msg += ` / ${r.url}`;
      appendLog(msg, 'log-ok');
      if (r.url) {
        document.getElementById('weekly-url').value = r.url;
        window.open(r.url, '_blank');
      }
      setStatus('SCCB Page 생성 완료');
      alert(msg);
    } else {
      appendLog(`[SCCB Page 실패] ${res.error || ''}`, 'log-fail');
      setStatus('SCCB Page 생성 실패');
      alert(`SCCB Page 생성 실패\n${res.error || ''}`);
    }
  } catch (e) {
    appendLog(`[SCCB Page 오류] ${e.message}`, 'log-fail');
    setStatus('오류');
  }
}

/* ── 회의록 페이지 생성 다이얼로그 ── */
function onCreateMeetingPage() {
  document.getElementById('meeting-url-input').value = '';
  document.getElementById('meeting-dialog').style.display = 'flex';
  document.getElementById('meeting-url-input').focus();
}

function closeMeetingDialog() {
  document.getElementById('meeting-dialog').style.display = 'none';
}

document.getElementById('meeting-url-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') submitMeetingDialog();
  if (e.key === 'Escape') closeMeetingDialog();
});

document.getElementById('meeting-dialog').addEventListener('click', e => {
  if (e.target === document.getElementById('meeting-dialog')) closeMeetingDialog();
});

async function submitMeetingDialog() {
  const sourceUrl = document.getElementById('meeting-url-input').value.trim();
  if (!sourceUrl) { alert('회의록 원본 URL을 입력하세요.'); return; }
  closeMeetingDialog();
  setStatus('회의록 Page 생성 중...');
  appendLog(`[회의록 Page] 이전 주 페이지 포맷 복제 시작 - 원본: ${sourceUrl}`);
  try {
    const res = await postJSON('/api/create_meeting_page', { source_url: sourceUrl });
    if (res.ok) {
      const r = res.result || {};
      const created = r.created ? '생성 완료' : '이미 존재';
      let msg = `[회의록 Page] ${created}: ${r.title || ''}`;
      if (r.source_title) msg += ` (원본: ${r.source_title})`;
      if (r.url) msg += ` / ${r.url}`;
      appendLog(msg, 'log-ok');
      if (r.url) window.open(r.url, '_blank');
      setStatus('회의록 Page 생성 완료');
      alert(msg);
    } else {
      appendLog(`[회의록 Page 실패] ${res.error || ''}`, 'log-fail');
      setStatus('회의록 Page 생성 실패');
      alert(`회의록 Page 생성 실패\n${res.error || ''}`);
    }
  } catch (e) {
    appendLog(`[회의록 Page 오류] ${e.message}`, 'log-fail');
    setStatus('오류');
  }
}

/* ── Excel 내보내기 ── */
async function onExportExcel() {
  if (!issues.length) { alert('내보낼 데이터가 없습니다.'); return; }
  const rows = issues.map(i => ({
    key: i.key,
    summary: i.summary,
    body_len: i.body_len,
    rollout: i.rollout,
    err_table: i.err_table,
    links: i.links,
    tcgen: i.tcgen,
    aio_test: i.aio_test,
    pr_merge: i.pr_merge,
    status: i.status,
    assignee: i.assignee,
    duedate: i.duedate,
  }));

  setStatus('Excel 생성 중...');
  try {
    const r = await fetch('/api/export_excel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rows }),
    });
    if (!r.ok) {
      const err = await r.json();
      alert(`Excel 생성 실패: ${err.error || ''}`);
      setStatus('Excel 생성 실패');
      return;
    }
    const blob = await r.blob();
    const cd = r.headers.get('Content-Disposition') || '';
    const fnMatch = cd.match(/filename="?([^";\n]+)"?/);
    const filename = fnMatch ? fnMatch[1] : `sccb_validation.xlsx`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    appendLog(`[Excel Export] ${filename} 다운로드 완료`, 'log-ok');
    setStatus('Excel Export 완료');
  } catch (e) {
    appendLog(`[Excel 오류] ${e.message}`, 'log-fail');
    setStatus('오류');
  }
}
