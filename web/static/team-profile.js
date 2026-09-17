/* 팀 공용 업무는 서버에 저장한다. 개인 작업실의 localStorage와 구분한다. */
window.TeamProfile = (() => {
  let saved = null, loading = false;
  const q = s => document.querySelector(s);
  const message = (text, error=false) => {
    q('#team-profile-status').textContent = text;
    q('#team-profile-status').classList.toggle('form-error', error);
  };
  async function request(options) {
    const res = await fetch('/api/team-profile', {cache:'no-store', ...options});
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '팀 업무를 처리하지 못했습니다. 다시 시도해 주세요.');
    return data;
  }
  function showMeta() {
    q('#team-profile-meta').textContent = saved.updated_at
      ? '팀 공용 · 수정 '+new Date(saved.updated_at).toLocaleString('ko-KR')
      : '기존 팀 업무를 불러왔습니다. 수정 후 저장해 주세요.';
  }
  async function load() {
    if (saved || loading) return;
    loading = true;
    try {
      saved = await request();
      q('#team-profile-content').value = saved.content;
      q('#team-profile-content').disabled = false;
      q('#team-profile-save').disabled = false;
      showMeta(); message('');
    } catch(e) {message(e.message, true);}
    finally {loading = false;}
  }
  document.addEventListener('submit', async e => {
    if (e.target.id !== 'team-profile-form') return;
    e.preventDefault();
    if (!saved || loading) return;
    const content = q('#team-profile-content').value;
    if (!content.trim()) {message('팀 업무를 입력해 주세요.', true); return;}
    loading = true;
    q('#team-profile-save').disabled = true;
    q('#team-profile-content').readOnly = true;
    q('#team-profile-cancel').disabled = true;
    message('저장 중…');
    try {
      saved = await request({method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({content, revision:saved.revision})});
      q('#team-profile-content').value = saved.content;
      q('#team-profile-latest').hidden = true;
      showMeta(); message('팀 업무를 저장했습니다. 이후 생성하는 해설에 반영됩니다.');
    } catch(e) {message(e.message+' 입력한 내용은 유지됩니다.', true);}
    finally {
      loading = false;
      q('#team-profile-save').disabled = false;
      q('#team-profile-content').readOnly = false;
      q('#team-profile-cancel').disabled = false;
    }
  });
  document.addEventListener('click', async e => {
    if (e.target.closest('#team-profile-cancel') && saved && !loading) {
      q('#team-profile-content').value = saved.content;
      message('마지막으로 불러오거나 저장한 내용으로 되돌렸습니다.');
    }
    if (e.target.closest('#team-profile-reload') && !loading) {
      if (!saved) {await load(); return;}
      loading = true;
      try {
        const latest = await request();
        if (latest.revision !== saved.revision) {
          q('#team-profile-latest').hidden = false;
          q('#team-profile-latest-content').textContent = latest.content;
          saved = latest;
          showMeta();
          message('최신 업무를 아래에 표시했습니다. 작성 중인 내용에 필요한 변경을 합친 뒤 저장해 주세요.');
        } else {message('최신 버전입니다. 작성 중인 내용은 유지했습니다.');}
      } catch(err) {message(err.message,true);}
      finally {loading = false;}
    }
  });
  window.addEventListener('beforeunload', e => {
    if (saved && q('#team-profile-content').value !== saved.content) {e.preventDefault();e.returnValue='';}
  });
  return {load};
})();
