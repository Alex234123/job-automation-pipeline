# -*- coding: utf-8 -*-
"""
collect_server.py — 采集结果落盘服务

浏览器页面无法直接写本地文件（Node 沙箱无 fs 模块），
因此用本地 HTTP 服务接收页面上报的数据并落盘。

架构：
    Browser (page.evaluate)
        ↓ fetch('http://127.0.0.1:18765/collect', {results: [...]})
    collect_server (Python HTTPServer)
        ↓ 去重（title|company）
        ↓ 追加写入
    data/jd_dump/index.jsonl
    data/seen_cards.txt        # 防重复点击
    data/collection_state.json # 进度

关键设计：
    1. Chrome 把 127.0.0.1 视为可信来源，页面 fetch 可直连本地服务
    2. 服务端设置 CORS 头允许跨域
    3. 内存缓存 _EX 集合实现 O(1) 去重

用法：
    python collect_server.py
    # 监听 127.0.0.1:18765
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ============ 配置（部署时修改此处）============
DATA_DIR = os.environ.get('JOB_DATA_DIR', './data')
PORT = int(os.environ.get('COLLECT_PORT', '18765'))

IDX = os.path.join(DATA_DIR, 'jd_dump', 'index.jsonl')
VER = os.path.join(DATA_DIR, 'jd_dump', 'verdicts.jsonl')
SEEN = os.path.join(DATA_DIR, 'seen_cards.txt')
STATE = os.path.join(DATA_DIR, 'collection_state.json')
APPLIED = os.path.join(DATA_DIR, 'applied_jobs.csv')
APPLY_STATE = os.path.join(DATA_DIR, 'apply_state.json')

_lock = threading.Lock()


def ensure_dirs():
    """确保数据目录存在"""
    for p in [os.path.dirname(IDX), os.path.dirname(VER)]:
        if p:
            os.makedirs(p, exist_ok=True)


def load_existing():
    """启动时加载已有记录到内存，用于 O(1) 去重"""
    ex = set()
    if not os.path.exists(IDX):
        return ex
    with open(IDX, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ex.add((r.get('title', '').strip(), r.get('company', '').strip()))
            except Exception:
                pass
    return ex


_EX = None


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # GET /seen — 返回已见卡片 key 集，供采集脚本跳过
        if self.path == '/seen':
            keys = set()
            if os.path.exists(SEEN):
                with open(SEEN, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            keys.add(line)
            if os.path.exists(IDX):
                with open(IDX, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                            keys.add(r.get('title', '').strip() + '|' + r.get('company', '').strip())
                        except Exception:
                            pass
            return self._json({'seen': sorted(keys)})

        # GET /apply/queue — 返回待投队列
        if self.path == '/apply/queue':
            verdicts = {}
            if os.path.exists(VER):
                with open(VER, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            v = json.loads(line)
                            verdicts[v['key']] = v['verdict']
                        except Exception:
                            pass

            applied = set()
            if os.path.exists(APPLIED):
                with open(APPLIED, encoding='utf-8-sig') as f:
                    for line in f:
                        parts = line.strip().split(',')
                        if len(parts) >= 5:
                            applied.add(parts[1].strip() + '|' + parts[4].strip())

            queue, no_url = [], 0
            seen_keys = set()
            if os.path.exists(IDX):
                with open(IDX, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        key = r.get('title', '').strip() + '|' + r.get('company', '').strip()
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        if verdicts.get(key) != '投':
                            continue
                        if key in applied:
                            continue
                        if not r.get('job_url'):
                            no_url += 1
                            continue
                        queue.append({
                            'title': r['title'],
                            'company': r['company'],
                            'salary': r.get('salary', ''),
                            'city': r.get('city_area', ''),
                            'job_url': r['job_url'],
                            'batch': r.get('batch', '')
                        })
            # 排序策略：优先批次（可自定义）
            queue.sort(key=lambda x: (x.get('batch', ''), x.get('city', '')))
            return self._json({'queue': queue, 'no_url': no_url})

        # GET /state — 采集进度
        if self.path == '/state':
            n = 0
            if os.path.exists(IDX):
                with open(IDX, encoding='utf-8') as f:
                    n = sum(1 for line in f if line.strip())
            st = {}
            if os.path.exists(STATE):
                try:
                    st = json.load(open(STATE, encoding='utf-8'))
                except Exception:
                    pass
            return self._json({'total_records': n, 'state': st})

        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self):
        global _EX

        # POST /apply/result — 记录投递结果
        if self.path == '/apply/result':
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            results = payload.get('results', [])
            with _lock:
                with open(APPLIED, 'a', encoding='utf-8-sig', newline='') as f:
                    for r in results:
                        f.write(','.join([
                            r.get('time', ''), r.get('title', ''), r.get('salary', ''),
                            r.get('city', ''), r.get('company', ''), r.get('result', '')
                        ]) + '\n')
                st = {'count': 0}
                if os.path.exists(APPLY_STATE):
                    try:
                        st = json.load(open(APPLY_STATE, encoding='utf-8'))
                    except Exception:
                        pass
                st['count'] = st.get('count', 0) + len(results)
                st['last_update'] = results[-1].get('time', '') if results else ''
                json.dump(st, open(APPLY_STATE, 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=1)
            return self._json({'recorded': len(results)})

        # POST /collect — 采集结果落盘
        if self.path != '/collect':
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        length = int(self.headers.get('Content-Length', 0))
        payload = json.loads(self.rfile.read(length).decode('utf-8'))
        results = payload.get('results', [])
        kw = payload.get('kw', '')

        new, dups, mismatch, jd_empty = [], 0, 0, 0
        with _lock:
            for r in results:
                st = r.get('_status', 'ok')
                if st == 'mismatch':
                    mismatch += 1
                    continue
                if st == 'jd_empty':
                    jd_empty += 1
                    continue
                key = (r.get('title', '').strip(), r.get('company', '').strip())
                if not key[0] or not key[1] or key in _EX:
                    dups += 1
                    continue
                _EX.add(key)
                r.pop('_status', None)
                new.append(r)

            if new:
                with open(IDX, 'a', encoding='utf-8') as f:
                    for r in new:
                        f.write(json.dumps(r, ensure_ascii=False) + '\n')

            # 所有处理过的卡（含 mismatch/jd_empty）都记 seen，避免重复点击
            seen_lines = []
            for r in results:
                k = r.get('title', '').strip() + '|' + r.get('company', '').strip()
                if k != '|' and k.strip('|'):
                    seen_lines.append(k + '\n')
            if seen_lines:
                with open(SEEN, 'a', encoding='utf-8') as f:
                    f.writelines(seen_lines)

            # 更新进度
            state = {}
            if os.path.exists(STATE):
                try:
                    state = json.load(open(STATE, encoding='utf-8'))
                except Exception:
                    pass
            state['last_keyword'] = kw
            state['last_count'] = len(new)
            state.setdefault('keywords_done', [])
            if payload.get('complete') and kw and kw not in state['keywords_done']:
                state['keywords_done'].append(kw)
            json.dump(state, open(STATE, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)

        return self._json({
            'saved': len(new), 'dups': dups,
            'mismatch': mismatch, 'jd_empty': jd_empty
        })

    def log_message(self, *args):
        pass


def main():
    global _EX
    ensure_dirs()
    _EX = load_existing()
    print(f'collect server on 127.0.0.1:{PORT} (loaded {len(_EX)} existing records)', flush=True)
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
