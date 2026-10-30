---
name: satellite-to-layout
slug: satellite-to-layout
displayName: Satellite Image → Editable Site Layout
description: Turn a satellite/aerial photo (or a street-view screenshot) into an editable site layout — a plain-JSON plan of road/sidewalk/curb/lawn/parking bands plus building, trees, poles, lamps and cars, all in centimetres — then hand it over in a drag-and-drop browser editor. Use when the user says "turn this satellite image into a layout", "make me an editable site plan from this photo", "recreate this block layout", "I want to adjust the road/parking/lawn myself", or Chinese equivalents "卫星地图转布局图", "照这张卫星图做个布局", "把这个街区做成能改的平面图", "楼前要一排垂直车位", "路宽/人行道我自己调". Includes a pixel-to-centimetre calibration helper, a layout generator, a validator that catches coplanar-overlap/object-floating/car-on-sidewalk mistakes, and a zero-dependency local server. Not for architectural modelling — output is a site plan, not a building.
description_zh: 把卫星/航拍照片变成一份可拖拽编辑的场地布局（纯 JSON，单位 cm：车行道/人行道/路缘/草地/停车场地块 + 楼体 + 树杆灯车），并附一个单文件网页编辑器。含像素→厘米标定、骨架生成、坏布局校验（共面重叠/物件悬空/车压人行道）和零依赖本地服务。触发词：卫星地图转布局图、照卫星图做布局、街区平面图能改、楼前垂直车位。
category: design-tool
version: 1.0.0
author: WorkBuddy
agent_created: true
---

# 卫星图 → 可编辑场地布局

## 这个 skill 解决什么

拿到一张卫星图/航拍图，说「照这个做个布局」，通常最后交付的是一张**图片** ——
用户只能说「路太宽了」「车位不对」，你只能再改一遍再出图。

这个 skill 交付的是**一份数据 + 一个编辑器**：用户自己拖、自己拉边、自己改数字。

```
卫星图  →  标定比例  →  生成布局骨架 JSON  →  网页编辑器微调  →  校验  →  交给下游（3D/渲染/施工图）
                                                   ↑ 用户自己操作
```

## 关键取舍：不做图像识别

自动识别楼和路看起来很聪明，实际上边缘一糊就全错，而且错得没边、没法复核。
**可靠的流程是人眼读数 + 一个准确的标尺。** 这个 skill 只提供标尺和脚手架：

- `make_layout.py scale` —— 用一处已知尺寸（楼宽、车道宽、车位宽都行）标定整张图
- `make_layout.py init` —— 按真实尺寸生成骨架
- `layout_editor.html` —— 人在这里拖

## 五步

> 下文的 `scripts/xxx.py` 都相对**本 skill 目录**。实际调用时用绝对路径，别假设 cwd。

### 1. 认一处已知尺寸
卫星图上最容易认的参照物，按可靠度排序：

| 参照物 | 典型值 | 备注 |
|---|---|---|
| 垂直车位宽 | 250 cm | 最可靠 —— 一排车位的总宽 ÷ 个数 |
| 小车长 | 430~470 cm | 一排车并排时很好量 |
| 双向两车道 | 700 cm | 有中心虚线的话，两侧各 350 |
| 人行道 | 250 cm（住宅）/ 400+（商业） | |
| 楼宽 | 按开间数推 | 一开间 300~400 cm |

⛔ **别拿"看起来差不多"的数字当标定**。一处标定错 10%，整张图全错 10%。
不确定就问用户，或者用两个参照物交叉验证（比如车位宽 + 楼宽各算一次，看差多少）。

### 2. 标定
```bash
python scripts/make_layout.py scale --px 235 --meters 29.12
# ⇒ 1 像素 = 12.39 厘米
```
`--px` 是你在这张图上量到的像素长度（截图工具、看图软件都能量）。

### 3. 生成骨架
```bash
python scripts/make_layout.py init --out site.json \
    --pattern corner \          # corner=L 形街角；straight=一条街
    --building-w 29.12 --building-d 5.40 --units 8 --no-start 201 \
    --parking front --bays 11 \
    --road 10.94 --walk 2.5
```
默认参数就是一套「8 户外廊楼 + 楼前垂直车位 + 街角两条街」的常见组合，
改数字比从零画快。所有参数 `--help` 都有说明。

### 4. 开编辑器，让用户自己拖
```bash
python scripts/layout_server.py --dir <布局文件所在目录>
# 浏览器开 http://127.0.0.1:8777/layout_editor.html
```
编辑器能做的：
- 拖地块 / 拖对象；拉地块四边改宽度；属性面板改精确数字
- 加树、电线杆、路灯、草丛、车位（车位有「朝向」：沿路 / 垂直车位）
- 场地地块有「划线间距」= 车位宽，自动画白色车位分隔线
- 目录里有多个 `.json` 时，顶上下拉直接切方案；保存写回当前那个文件

### 5. 验收
```bash
python scripts/check_layout.py site.json          # 有问题会列出 ERROR/WARN
python scripts/check_layout.py site.json --strict # WARN 也算失败
```
它抓的是**平面图上肉眼看不出来、渲染出来才现形**的那一类错：

| 判据 | 为什么值得单列一条 |
|---|---|
| 同类型地块重叠 | 两个同高面共面 → 渲染时整片闪 |
| 人行道压在车行道上 | 路口没断开，人行道横铺到车道了 |
| 地块尺寸 < 1cm | 16cm 宽的路缘石曾被杀「坏数据」的逻辑整块丢掉 |
| 车压人行道 / 车在楼里 | 街景里一眼假 |
| 物件不在任何地块上 | 渲染出来像悬空 |
| 车比车位数多 | 会挤到隔壁车位线上 |

## 布局文件长什么样

单位一律 **cm**，`+x` 向右、`+y` 向下（画在屏幕上就是「街道在下方」）。

```jsonc
{
  "version": 3,
  "building": {                       // 楼体（只有一栋；多栋就拆成多个文件或用 far 地块凑体量）
    "box":  {"x0":0, "y0":0, "x1":2912, "y1":540},
    "corr": {"y0":420, "y1":540},     // 外廊那条带，没有就删掉
    "stair":{"x0":2952,"x1":3302,"y0":420,"y1":510},  // 外挂梯，可选
    "units": 8,                       // 开间数 → 画分隔线 + 房号
    "noStart": 201                    // 房号从几号起
  },
  "bands": [                          // 地面，可以任意多块，叠放顺序 = 数组顺序
    {"kind":"lot",  "name":"楼前停车前场", "x0":0, "y0":540,  "x1":2912, "y1":1065, "pitch":250},
    {"kind":"curb", "name":"路缘石",      "x0":-2000,"y0":1065,"x1":4912,"y1":1081},
    {"kind":"road", "name":"车行道",      "x0":-2000,"y0":1081,"x1":4166,"y1":2175},
    {"kind":"walk", "name":"对侧人行道",  "x0":-2000,"y0":2175,"x1":4166,"y1":2425},
    {"kind":"verge","name":"侧院草坪",    "x0":2912, "y0":-2200,"x1":3900,"y1":1065}
  ],
  "trees": [{"x":3300,"y":-600,"scale":0.6,"rot":0,"model":"tree","w":420,"h":720}],
  "poles": [{"x":400,"y":1000}],
  "lamps": [{"x":1000,"y":1000,"h":450,"side":"right"}],
  "grass": [{"x0":0,"y0":-2200,"x1":2912,"y1":0}],
  "cars":  [{"x":45,"y":560,"len":430,"rot":90}]        // rot:90 = 垂直车位（车长沿 y）
}
```

`bands.kind` 六种，各有固定的材质和渲染高度：

| kind | 含义 | 渲染高度 |
|---|---|---|
| `road` | 车行道（沥青） | 2 cm |
| `lot` | 停车场 / 场地铺装 | 8 cm |
| `verge` | 草地 | 6 cm |
| `walk` | 人行道 | 14 cm |
| `curb` | 路缘石 | 18 cm |
| `far` | 对面楼（体量） | 0 |

⚠️ **高度不是真实构造，只是防共面闪烁**。同 kind 的两块叠在一起就会闪 —— 这条交给 `check_layout.py`。

## 交给下游

`layout.json` 是纯数据，谁都能吃。这次实际的下游是 Blender：
`layout.json` → 一个 Python 模块按 `bands.kind` 出盒子 → 渲一张俯视图和原照片并排核对。

⚠️ **如果下游是"先烘一遍场景再用"，改完布局一定要重烘** ——
少跑这一步，几何不会变，你会以为布局没生效（我踩过，白渲一轮）。

## 更多

- `references/real-world-dimensions.md` —— 真实尺寸参考表（车位/车道/人行道/树冠/路缘）
- `references/calibration.md` —— 像素标定法，含「两个参照物交叉验证」的做法
- `references/pitfalls.md` —— 这一整套东西真踩过的坑，改代码前先读
