// www.mod.go.jp のページをブラウザの中で取得し、1 つの JSON（ページ束）として保存する。
//
// HTML はスクリプトから取得できない（README 参照）ので、通常のブラウザで www.mod.go.jp の
// ページを開いた状態で、開発者ツールのコンソール等から実行する。保存した JSON は
// mod/ingest_pages.py で raw/mod/pages/ に展開する。
//
//   modPages.start(['/j/policy/agenda/meeting/index.html', ...], 'mod_pages.json')
//   modPages.status()   // 進み具合
//   modPages.save()     // 終わったら JSON をダウンロードフォルダに保存
//
// 1 件ずつ、2 秒強の間隔をあけて取得する。403・429 やボット検証の応答が返ったらそこで止める。
// 検証を回避する処理は入れていない。
window.modPages = (() => {
  const state = { name: 'mod_pages.json', total: 0, records: [], stopped: null, finished: false };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function grab(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    const bytes = new Uint8Array(await res.arrayBuffer());
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return {
      url,
      status: res.status,
      content_type: res.headers.get('content-type'),
      last_modified: res.headers.get('last-modified'),
      cf_mitigated: res.headers.get('cf-mitigated'),
      fetched_at: new Date().toISOString(),
      body_b64: btoa(binary),
    };
  }

  async function run(urls) {
    for (const url of urls) {
      try {
        const rec = await grab(url);
        state.records.push(rec);
        if (rec.status === 403 || rec.status === 429 || rec.cf_mitigated) {
          state.stopped = `${url}: status ${rec.status}`;
          break;
        }
      } catch (e) {
        state.records.push({ url, error: String(e), fetched_at: new Date().toISOString() });
      }
      await sleep(2000 + Math.floor(Math.random() * 500));
    }
    state.finished = true;
  }

  return {
    start(urls, name) {
      Object.assign(state, { name: name || state.name, total: urls.length, records: [], stopped: null, finished: false });
      run(urls);
      return `started: ${urls.length} pages`;
    },
    status() {
      return { done: state.records.length, total: state.total, stopped: state.stopped, finished: state.finished };
    },
    save() {
      const blob = new Blob([JSON.stringify(state.records)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = state.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      return `${state.name}: ${blob.size} bytes`;
    },
  };
})();
