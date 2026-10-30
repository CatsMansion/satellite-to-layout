# -*- coding: utf-8 -*-
"""场地布局编辑器的本地小服务（零依赖，只用标准库）。

它负责三件事：
  1. 把编辑器页面发出去（编辑器本身在 scripts/layout_editor.html）
  2. 把你工作目录里的布局文件列出来（GET /api/list），让编辑器下拉能切方案
  3. 收 POST /save?file=xxx.json，把调好的布局写回那个文件（写前自动留 .bak）

用法：
    python layout_server.py                      # 服务当前目录
    python layout_server.py --dir D:/my/site     # 服务指定目录
    python layout_server.py --dir . --port 8800
然后浏览器打开
    http://127.0.0.1:8777/layout_editor.html
    http://127.0.0.1:8777/layout_editor.html?layout=plan_b.json   # 指定读哪个

⚠️ 只监听 127.0.0.1，不对外网开放。
⚠️ 只能写 --dir 里的 *.json，文件名做白名单校验，防路径穿越。
"""
import argparse
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SAFE_NAME = re.compile(r"^[A-Za-z0-9._\-\u4e00-\u9fff]+\.json$")   # 允许中文文件名

# 一份布局必须有的字段。缺了宁可拒绝写入，也不要让下游拿到一份坏数据。
REQUIRED_LIST = ("poles", "lamps", "trees", "grass", "cars", "bands")


def list_layouts(d):
    out = []
    try:
        for n in sorted(os.listdir(d)):
            if n.lower().endswith(".json") and os.path.isfile(os.path.join(d, n)):
                if n.endswith(".bak"):
                    continue
                out.append(n)
    except OSError:
        pass
    return out


def validate(data):
    """返回错误字符串；None = 通过。
    ⛔ 只查「字段在不在、类型对不对」，不查语义 —— 语义交给 check_layout.py。
       这条边界很重要：编辑器保存不该因为你把两条路画重叠了就拒绝你。"""
    if not isinstance(data, dict):
        return "顶层不是对象"
    for k in REQUIRED_LIST:
        if not isinstance(data.get(k), list):
            return "缺字段或类型不对: %s（必须是数组）" % k
    b = data.get("building")
    if b is not None:
        if not isinstance(b, dict):
            return "building 必须是对象"
        box = b.get("box")
        if not isinstance(box, dict):
            return "building.box 缺失"
        for q in ("x0", "y0", "x1", "y1"):
            if not isinstance(box.get(q), (int, float)) or isinstance(box.get(q), bool):
                return "building.box.%s 必须是数字" % q
    for t in data["trees"]:
        if not isinstance(t, dict) or not isinstance(t.get("x"), (int, float)) \
                or not isinstance(t.get("y"), (int, float)):
            return "树的 x/y 必须是数字（这条救过整张图：路灯缺 y → NaN → 全画到画布外）"
    for c in data["cars"]:
        if not isinstance(c, dict):
            return "cars 元素必须是对象"
        for q in ("x", "y"):
            if not isinstance(c.get(q), (int, float)):
                return "车的 x/y 必须是数字"
    return None


class Handler(http.server.BaseHTTPRequestHandler):
    workdir = "."
    server_version = "layout-server/1.0"

    # ---------- 工具 ----------
    def _send(self, body, ctype, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def _resolve(self, name):
        """静态文件：先找工作目录，再找编辑器目录。返回绝对路径或 None。"""
        name = name.lstrip("/") or "layout_editor.html"
        for base in (self.workdir, HERE):
            base = os.path.abspath(base)
            p = os.path.abspath(os.path.join(base, name))
            try:
                if os.path.commonpath([p, base]) != base:
                    continue                  # 越界，丢掉
            except ValueError:
                continue
            if os.path.isfile(p):
                return p
        return None

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/list":
            self._json({"files": list_layouts(self.workdir), "dir": os.path.abspath(self.workdir)})
            return

        if path == "/api/ping":
            self._json({"ok": True, "dir": os.path.abspath(self.workdir)})
            return

        p = self._resolve(path)
        if p is None:
            self._send(b"404 not found", "text/plain; charset=utf-8", 404)
            return
        ext = os.path.splitext(p)[1].lower()
        ctype = {".html": "text/html; charset=utf-8",
                 ".json": "application/json; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}.get(ext, "application/octet-stream")
        with open(p, "rb") as f:
            self._send(f.read(), ctype)

    # ---------- POST ----------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/save":
            self._send(b"404", "text/plain", 404)
            return

        name = (urllib.parse.parse_qs(u.query).get("file") or ["layout.json"])[0]
        if not SAFE_NAME.match(name):
            self._json({"ok": False, "err": "文件名不合法（只允许字母数字中文和 ._-，且必须是 .json）"})
            return

        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            self._json({"ok": False, "err": "空请求体"})
            return
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self._json({"ok": False, "err": "JSON 解析失败: %s" % e})
            return

        err = validate(data)
        if err:
            self._json({"ok": False, "err": err})
            return

        dst = os.path.abspath(os.path.join(self.workdir, name))
        if os.path.dirname(dst) != os.path.abspath(self.workdir):
            self._json({"ok": False, "err": "拒绝写到工作目录外"})
            return

        # 写之前先留一份上一版，救过手滑
        if os.path.isfile(dst):
            try:
                shutil.copyfile(dst, dst + ".bak")
            except OSError:
                pass

        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, dst)          # 原子替换：中途崩了也不会留半个文件

        cnt = "杆%d 灯%d 树%d 草%d 车%d 地块%d" % (
            len(data["poles"]), len(data["lamps"]), len(data["trees"]),
            len(data["grass"]), len(data["cars"]), len(data["bands"]))
        print("[SAVE] %s  %s" % (name, cnt))
        self._json({"ok": True, "file": name, "path": dst, "counts": cnt})

    def log_message(self, fmt, *args):
        line = fmt % args
        if "/api/list" in line or "/api/ping" in line:
            return                    # 编辑器每次开都会问一次，别刷屏
        sys.stderr.write("  %s\n" % line)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="场地布局编辑器本地服务")
    ap.add_argument("--dir", default=".", help="布局文件所在目录（默认当前目录）")
    ap.add_argument("--port", type=int, default=8777)
    a = ap.parse_args()

    Handler.workdir = os.path.abspath(a.dir)
    if not os.path.isdir(Handler.workdir):
        sys.exit("目录不存在: %s" % Handler.workdir)

    files = list_layouts(Handler.workdir)
    print("布局目录: %s" % Handler.workdir)
    print("发现 %d 个布局文件: %s" % (len(files), "、".join(files) if files else "（还没有，用 make_layout.py 生成）"))
    print("编辑器  : http://127.0.0.1:%d/layout_editor.html" % a.port)
    print("Ctrl+C 停止")
    with Server(("127.0.0.1", a.port), Handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n停。")


if __name__ == "__main__":
    main()
