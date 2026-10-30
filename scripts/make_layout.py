# -*- coding: utf-8 -*-
"""按真实尺寸生成一份布局骨架（layout.json），交给编辑器微调。

设计原则：**不做计算机视觉**。
自动从卫星图里识别楼和路看起来聪明，实际上边缘一糊就全错，而且错得没边。
真正可靠的流程是人眼读数 + 一个准确的标尺：

    1) 认出一处已知尺寸（楼宽、车道宽、车位宽都行）
    2) python make_layout.py scale --px 235 --meters 29.12
       → 告诉你这张图上 1 像素等于多少厘米
    3) 其余要素按同一个比例从图上量出来
    4) python make_layout.py init --pattern corner ... 生成骨架
    5) python layout_server.py --dir . 开编辑器微调
    6) python check_layout.py plan.json 验收

坐标约定（全项目统一，别改）：
    +x 向右，+y 向下（画在屏幕上就是「街道在下方」）
    建筑左下（靠街那一侧）角 = 原点 (0,0)
    单位一律 cm

用法示例见 references/workflow.md。
"""
import argparse
import json
import os
import sys

# 常用真实尺寸（cm）。这些是澳洲/中国都大差不差的量级，拿来当默认值，
# 具体项目还是要按现场照片修正。来源与讨论见 references/real-world-dimensions.md
D = {
    "bay_w": 250.0,      # 垂直车位宽
    "bay_d": 525.0,      # 垂直车位深（含 30cm 余量）
    "car_w": 160.0,      # 小车宽
    "car_l": 430.0,      # 小车长
    "walk": 250.0,       # 人行道宽
    "curb": 16.0,        # 路缘石宽
    "road": 700.0,       # 双向两车道
    "road_wide": 1094.0, # 宽一点的城市路（这次那张卫星图就是它）
}


def f(v):
    return float("%.2f" % float(v))


# ============================================================
# scale：像素 ↔ 厘米
# ============================================================
def cmd_scale(a):
    if a.px <= 0:
        sys.exit("--px 必须 > 0")
    cm_per_px = a.meters * 100.0 / a.px
    print("图上一段 %g 像素  =  实际 %g 米" % (a.px, a.meters))
    print("  ⇒ 1 像素 = %.2f 厘米" % cm_per_px)
    print("  ⇒ 1 厘米 = %.4f 像素" % (1.0 / cm_per_px))
    print()
    print("反查表（这张图上的像素长度 → 真实尺寸）：")
    for cm in (16, 160, 250, 430, 525, 700, 1000, 1094, 2912):
        print("    %5d cm  =  %7.1f px" % (cm, cm / cm_per_px))
    print()
    print("用法：把上面「1 像素 = X 厘米」记下来，图上量到的像素数乘它，就是厘米。")
    print("      或者反过来：你想放一个 5.25 米深的车位，图上应该是 %.1f 像素。" % (525 / cm_per_px))


# ============================================================
# init：生成骨架
# ============================================================
def cmd_init(a):
    W = a.building_w * 100.0
    Dp = a.building_d * 100.0
    units = max(1, a.units)
    corr_d = min(Dp, a.corridor * 100.0)

    bands = []
    y = Dp                                   # 从楼的前沿往街道方向排

    def add(kind, name, x0, y0, x1, y1, **kw):
        d = {"kind": kind, "name": name, "x0": f(x0), "y0": f(y0), "x1": f(x1), "y1": f(y1)}
        d.update(kw)
        bands.append(d)

    cars = []
    apron_d = 0.0
    if a.parking == "front":
        apron_d = a.bay_d * 100.0
        n_bays = a.bays if a.bays > 0 else int(W // (a.bay_w * 100.0))
        n_bays = max(0, n_bays)
        # 楼前停车前场：车位从楼的左端对齐开始，一路排到楼右端
        add("lot", "楼前停车前场", 0, y, W, y + apron_d, locked=True, pitch=f(a.bay_w * 100.0))
        for i in range(n_bays):
            bx = i * a.bay_w * 100.0
            if bx + a.bay_w * 100.0 > W + 1:
                break
            cars.append({"x": f(bx + (a.bay_w * 100.0 - a.car_w * 100.0) / 2.0),
                         "y": f(y + 20.0),
                         "len": f(a.car_l * 100.0), "rot": 90})
        y += apron_d
        front_obstruction = "lot"
    else:
        add("walk", "人行道", -a.overhang * 100.0, y, W + a.overhang * 100.0, y + a.walk * 100.0, locked=True)
        y += a.walk * 100.0
        front_obstruction = "walk"

    add("curb", "路缘石", -a.overhang * 100.0, y, W + a.overhang * 100.0, y + a.curb * 100.0, locked=True)
    y += a.curb * 100.0

    road_y0 = y
    road_w = a.road * 100.0
    add("road", "车行道", -a.overhang * 100.0, y, W + a.overhang * 100.0, y + road_w, locked=True)
    y += road_w

    if a.pattern == "corner":
        # 街角：第二条街竖着切在楼的右侧，两条车行道在路口「相邻但不重叠」
        #   ⛔ 两条路都铺过路口 = 两个同高面共面 = 渲染时闪
        cross_w = a.road * 100.0
        gap = a.side * 100.0                      # 楼右端到横街人行道的距离（草坪带）
        wx0 = W + gap
        wx1 = wx0 + a.walk * 100.0
        rx0 = wx1 + a.curb * 100.0
        rx1 = rx0 + cross_w
        south = road_y0 + road_w                  # 横街车行道的南边线
        # 主街车行道在路口前收住，让竖街独占那一块方地
        bands[[b["kind"] for b in bands].index("road")]["x1"] = f(rx0)
        # ⚠️ 侧街的人行道/路缘要止于主街路缘的**上边线**，不能一直铺到 road_y0。
        #    铺到 road_y0 会跟主街路缘在转角处重叠一个 16×16cm 的小方块 ——
        #    同高共面，渲染时那个角会闪。（这条是 check_layout.py 当场抓出来的）
        corner_y = road_y0 - a.curb * 100.0
        add("walk", "侧街人行道", wx0, -a.yard * 100.0, wx1, corner_y, locked=True)
        add("curb", "侧街路缘", wx1, -a.yard * 100.0, rx0, corner_y, locked=True)
        add("road", "侧街车行道", rx0, -a.yard * 100.0, rx1, south, locked=True)
        # 主街对面的人行道
        add("walk", "对侧人行道", -a.overhang * 100.0, south, rx0, south + a.walk * 100.0, locked=True)
        # 侧院草坪：从楼右墙一直铺到侧街人行道
        if gap > 0:
            add("verge", "侧院草坪", W, -a.yard * 100.0, wx0, road_y0)
        # 对面楼
        if a.far > 0:
            add("far", "对面楼", rx1, -a.yard * 100.0, rx1 + a.far * 100.0, south, locked=True)
    else:
        if a.far > 0:
            add("far", "对面楼", -a.overhang * 100.0, y, W + a.overhang * 100.0, y + a.far * 100.0, locked=True)

    grass = []
    if a.yard > 0:
        grass.append({"x0": 0, "y0": f(-a.yard * 100.0), "x1": f(W), "y1": 0})

    lay = {
        "version": 3,
        "meta": {
            "note": "由 make_layout.py 生成，坐标单位 cm，+y 向街道",
            "pattern": a.pattern,
        },
        "building": {
            "box": {"x0": 0, "y0": 0, "x1": f(W), "y1": f(Dp)},
            "corr": {"y0": f(Dp - corr_d), "y1": f(Dp)},
            "units": units,
            "noStart": a.no_start,
            "label": a.label,
        },
        "bands": bands,
        "poles": [],
        "lamps": [],
        "trees": [],
        "grass": grass,
        "cars": cars,
    }
    if a.stair:
        lay["building"]["stair"] = {"x0": f(W + 40), "y0": f(Dp - 120), "x1": f(W + 390), "y1": f(Dp - 30)}

    if a.out == "-":
        json.dump(lay, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(lay, fp, ensure_ascii=False, indent=1)
            fp.write("\n")
        print("已生成 %s" % a.out)
        print("  楼体    %g × %g m，%d 个开间，房号 %d 起" % (W / 100, Dp / 100, units, a.no_start))
        print("  地块    %d 条：%s" % (len(bands), "、".join(b["name"] for b in bands)))
        print("  车位    %d 个（%s）" % (len(cars), "垂直、车头朝楼" if cars else "没放"))
        print()
        print("下一步：")
        print("  python layout_server.py --dir %s" % (os.path.dirname(a.out) or "."))
        print("  浏览器开 http://127.0.0.1:8777/layout_editor.html")


def main():
    ap = argparse.ArgumentParser(description="卫星图 → 场地布局骨架")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scale", help="用一处已知尺寸标定：图上一段多少像素 = 实际多少米")
    s.add_argument("--px", type=float, required=True, help="图上量到的像素长度")
    s.add_argument("--meters", type=float, required=True, help="这段对应的真实长度（米）")
    s.set_defaults(func=cmd_scale)

    i = sub.add_parser("init", help="按真实尺寸生成一份布局骨架")
    i.add_argument("--out", default="layout.json")
    i.add_argument("--pattern", choices=["straight", "corner"], default="corner",
                   help="straight=一条街；corner=L 形街角（默认）")
    i.add_argument("--building-w", type=float, default=29.12, help="楼宽（米）")
    i.add_argument("--building-d", type=float, default=5.40, help="楼进深（米）")
    i.add_argument("--units", type=int, default=8, help="开间数")
    i.add_argument("--no-start", type=int, default=101, help="房号起始")
    i.add_argument("--label", default="主楼")
    i.add_argument("--corridor", type=float, default=1.20, help="外廊进深（米）")
    i.add_argument("--stair", action="store_true", help="加一部外挂楼梯")
    i.add_argument("--parking", choices=["front", "street"], default="front",
                   help="front=楼前垂直车位；street=只留人行道，车停在路面上")
    i.add_argument("--bay-w", type=float, default=D["bay_w"] / 100.0, help="车位宽（米）")
    i.add_argument("--bay-d", type=float, default=D["bay_d"] / 100.0, help="车位深（米）")
    i.add_argument("--bays", type=int, default=0, help="车位个数，0=按楼宽自动铺满")
    i.add_argument("--car-w", type=float, default=D["car_w"] / 100.0, help="车身宽（米）")
    i.add_argument("--car-l", type=float, default=D["car_l"] / 100.0, help="车长（米）")
    i.add_argument("--road", type=float, default=D["road_wide"] / 100.0, help="车行道宽（米）")
    i.add_argument("--walk", type=float, default=D["walk"] / 100.0, help="人行道宽（米）")
    i.add_argument("--curb", type=float, default=D["curb"] / 100.0, help="路缘石宽（米）")
    i.add_argument("--side", type=float, default=9.88, help="corner：楼右墙到侧街人行道的距离（米），也就是侧院草坪宽")
    i.add_argument("--yard", type=float, default=22.0, help="后院进深（米）")
    i.add_argument("--far", type=float, default=20.0, help="对面楼进深（米），0=不放")
    i.add_argument("--overhang", type=float, default=20.0, help="地块向左右探出的距离（米），只为画面不死板")
    i.set_defaults(func=cmd_init)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
