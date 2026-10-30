# -*- coding: utf-8 -*-
"""验收一份布局：把这一轮真踩过的坑变成机器判据。

为什么要有它 —— 布局图看着对、渲染出来错，是最难查的一类问题：
  · 两条路都铺过路口 → 两个同高面共面 → 画面里一片闪烁
  · 车落在人行道上 → 看着像"停得很自然"，实际压着人行道
  · 地块宽 16cm 的路缘被当成坏数据丢掉 → 整条路缘消失
  · 物件落在所有地块之外 → 渲染出来悬空
这些用眼睛看平面图都很难发现，但用坐标一算就出来。

用法：
    python check_layout.py layout.json
    python check_layout.py layout.json --strict     # WARN 也算失败
退出码 0 = 通过；1 = 有 ERROR（或 --strict 下有 WARN）。
"""
import argparse
import json
import sys

# 各类型地块的渲染高度（cm）。同高度的两块重叠 = 共面 = 闪。
BAND_H = {"road": 2, "lot": 8, "verge": 6, "walk": 14, "curb": 18, "far": 0}
EPS = 0.5


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, msg):
        self.items.append((level, msg))

    def err(self, m): self.add("ERROR", m)
    def warn(self, m): self.add("WARN", m)
    def info(self, m): self.add("INFO", m)

    def dump(self):
        if not self.items:
            print("  ✓ 没有发现问题")
            return 0, 0
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        for lv, m in sorted(self.items, key=lambda x: order[x[0]]):
            print("  [%-5s] %s" % (lv, m))
        e = sum(1 for lv, _ in self.items if lv == "ERROR")
        w = sum(1 for lv, _ in self.items if lv == "WARN")
        return e, w


def rect(b):
    x0, y0, x1, y1 = b["x0"], b["y0"], b["x1"], b["y1"]
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def overlap_area(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    return (w * h) if (w > EPS and h > EPS) else 0.0


def inside(px, py, a, pad=0.0):
    return a[0] - pad <= px <= a[2] + pad and a[1] - pad <= py <= a[3] + pad


def main():
    ap = argparse.ArgumentParser(description="布局验收")
    ap.add_argument("path")
    ap.add_argument("--strict", action="store_true", help="WARN 也算失败")
    a = ap.parse_args()

    try:
        d = json.load(open(a.path, encoding="utf-8"))
    except Exception as e:
        sys.exit("读不了 %s：%s" % (a.path, e))

    r = Report()
    bands = d.get("bands") or []
    if not bands:
        r.err("一条地块都没有 —— 场景会没有地面")
    bld = (d.get("building") or {})
    box = bld.get("box")

    # ---------- 1. 基本字段 ----------
    for k in ("poles", "lamps", "trees", "grass", "cars"):
        if not isinstance(d.get(k), list):
            r.err("缺字段 %s（必须是数组）" % k)
    if not box:
        r.err("缺 building.box —— 编辑器会退回一栋 30×6m 的假楼")

    # ---------- 2. 地块几何 ----------
    kinds = {}
    for i, b in enumerate(bands):
        nm = b.get("name") or (b.get("kind") or "?") + "#%d" % i
        kd = b.get("kind")
        if kd not in BAND_H:
            r.err("%s：未知类型 %r（编辑器会把它当 lot 画）" % (nm, kd))
            continue
        try:
            rc = rect(b)
        except Exception:
            r.err("%s：坐标不是四个数字" % nm)
            continue
        w, h = rc[2] - rc[0], rc[3] - rc[1]
        if w < 1.0 or h < 1.0:
            r.err("%s：尺寸退化 %.1f×%.1f cm —— 下游会把它整块丢掉（16cm 宽的路缘当年就是这么消失的）" % (nm, w, h))
        if b["x1"] < b["x0"] or b["y1"] < b["y0"]:
            r.warn("%s：x1<x0 或 y1<y0（能跑，但说明是手改出来的，检查一下）" % nm)
        kinds.setdefault(kd, []).append((nm, rc, b))

    names = [b.get('name') or '' for b in bands]   # 只用于报错时指认名字

    # ---------- 3. 同类型重叠（共面闪烁） ----------
    for kd, lst in kinds.items():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                ov = overlap_area(lst[i][1], lst[j][1])
                if ov > EPS:
                    r.err("两条 %s 重叠 %.0f cm²（%.0f×%.0f m 那块）—— 同高共面，渲染时整片闪\n            %s ↔ %s"
                          % (kd, ov, (min(lst[i][1][2], lst[j][1][2]) - max(lst[i][1][0], lst[j][1][0])) / 100,
                             (min(lst[i][1][3], lst[j][1][3]) - max(lst[i][1][1], lst[j][1][1])) / 100,
                             lst[i][0], lst[j][0]))

    # ---------- 4. 人行道压在车行道上 ----------
    for wn, wr, _ in kinds.get("walk", []):
        for rn, rr, _ in kinds.get("road", []):
            if overlap_area(wr, rr) > EPS:
                r.err("人行道「%s」压在车行道「%s」上 —— 路口没断开，人行道横铺到车道了" % (wn, rn))

    # ---------- 5. 楼体压地块 ----------
    if box:
        br = rect(box)
        for kd, lst in kinds.items():
            for nm, rc, _ in lst:
                if overlap_area(br, rc) > EPS:
                    r.warn("楼体压在「%s」上（%.0f cm²）" % (nm, overlap_area(br, rc)))

    # ---------- 6. 路口接不接得上 ----------
    roads = kinds.get("road", [])
    if len(roads) >= 2:
        for i in range(len(roads)):
            for j in range(i + 1, len(roads)):
                A, B = roads[i][1], roads[j][1]
                touch_x = abs(A[2] - B[0]) < 1 or abs(B[2] - A[0]) < 1
                touch_y = abs(A[3] - B[1]) < 1 or abs(B[3] - A[1]) < 1
                span_x = min(A[2], B[2]) - max(A[0], B[0]) > 1
                span_y = min(A[3], B[3]) - max(A[1], B[1]) > 1
                if not ((touch_x and span_y) or (touch_y and span_x)):
                    r.warn("车行道「%s」和「%s」没接上 —— 路口会是个断口（或者它们只是离得很远的平行街）"
                           % (roads[i][0], roads[j][0]))

    # ---------- 7. 物件落位 ----------
    # ⚠️ 地面有两种来源：bands（路/人行道/铺装/草坪地块）+ grass（后院的草丛区块）。
    #    只算 bands 会把长在草地上的树误判成悬空 —— 这条是自测时自己撞出来的。
    all_rects = [rc for _, rc, _ in sum(kinds.values(), [])]
    for g in (d.get("grass") or []):
        try:
            all_rects.append(rect(g))
        except Exception:
            pass
    br = rect(box) if box else None
    if br:
        all_rects.append(br)

    def on_ground(px, py):
        return any(inside(px, py, rc, pad=1.0) for rc in all_rects)

    for i, t in enumerate(d.get("trees") or []):
        if not on_ground(t["x"], t["y"]):
            r.warn("树 #%d 在 (%.0f, %.0f) 不站在任何地块上 —— 渲染出来会像悬空" % (i + 1, t["x"], t["y"]))
        elif br and inside(t["x"], t["y"], br):
            r.warn("树 #%d 长在楼体轮廓里（真在屋顶花园就当没看见）" % (i + 1))
    for i, p in enumerate(d.get("poles") or []):
        if not on_ground(p["x"], p["y"]):
            r.warn("电线杆 #%d 不在任何地块上" % (i + 1))
    for i, p in enumerate(d.get("lamps") or []):
        if not on_ground(p["x"], p["y"]):
            r.warn("路灯 #%d 不在任何地块上" % (i + 1))

    # ---------- 8. 车停哪 ----------
    walks = [(nm, rc) for nm, rc, _ in kinds.get("walk", [])]
    for i, c in enumerate(d.get("cars") or []):
        L = c.get("len") or 430
        if c.get("rot"):
            fp = (c["x"], c["y"], c["x"] + 160, c["y"] + L)
        else:
            fp = (c["x"], c["y"], c["x"] + L, c["y"] + 160)
        for wn, wr in walks:
            if overlap_area(fp, wr) > 400:          # 4 平米以上才算压上去
                r.err("车 #%d 压在人行道「%s」上（%.1f m²）—— 街景里一眼假" % (i + 1, wn, overlap_area(fp, wr) / 1e4))
                break
        if not on_ground(fp[0] + 1, fp[1] + 1):
            r.warn("车 #%d 停在所有地块之外" % (i + 1))
        elif br and overlap_area(fp, br) > 400:
            r.err("车 #%d 停在楼体轮廓里（%.1f m²）—— 不是穿墙就是标错了 y" % (i + 1, overlap_area(fp, br) / 1e4))

    # ---------- 9. 车位数量 vs 划线条数 ----------
    for b in bands:
        if b.get("kind") == "lot" and (b.get("pitch") or 0) > 0:
            nm = b.get("name") or "lot"
            rc = rect(b)
            horiz = (rc[2] - rc[0]) >= (rc[3] - rc[1])
            span = (rc[2] - rc[0]) if horiz else (rc[3] - rc[1])
            n_slot = int(span // b["pitch"])
            n_car = sum(1 for c in (d.get("cars") or [])
                        if inside(c["x"] + 80, c["y"] + 80, rc))
            r.info("「%s」划线间距 %g cm ⇒ 约 %d 个车位，里面放了 %d 台车" % (nm, b["pitch"], n_slot, n_car))
            if n_car > n_slot:
                r.warn("车比车位多（%d > %d），会挤到隔壁车位线上" % (n_car, n_slot))

    # ---------- 汇总 ----------
    print("布局：%s" % a.path)
    print("  地块 %d 条（%s）｜杆 %d 灯 %d 树 %d 草 %d 车 %d"
          % (len(bands), "、".join("%s×%d" % (k, len(v)) for k, v in kinds.items()),
             len(d.get("poles") or []), len(d.get("lamps") or []),
             len(d.get("trees") or []), len(d.get("grass") or []), len(d.get("cars") or [])))
    if box:
        print("  楼体 %.1f × %.1f m，%s 个开间"
              % ((box["x1"] - box["x0"]) / 100, (box["y1"] - box["y0"]) / 100, bld.get("units")))
    print()
    e, w = r.dump()
    print()
    if e or (a.strict and w):
        print("❌ 不通过（%d 个 ERROR，%d 个 WARN）" % (e, w))
        sys.exit(1)
    print("✅ 通过（%d 个 WARN 不致命）" % w)


if __name__ == "__main__":
    main()
