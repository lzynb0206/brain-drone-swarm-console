import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

process.on("uncaughtException", (error) => {
  console.error(`BUILD_ERROR: ${error?.message ?? error}`);
  console.error(String(error?.stack ?? "").split("\n").filter((line) => line.includes("build_rev2.mjs")).join("\n"));
  process.exit(1);
});
process.on("unhandledRejection", (error) => {
  console.error(`BUILD_REJECTION: ${error?.message ?? error}`);
  console.error(String(error?.stack ?? "").split("\n").filter((line) => line.includes("build_rev2.mjs")).join("\n"));
  process.exit(1);
});

const ROOT = "/Users/geniusalu/Documents/brain-drone-swarm-console";
const SOURCE = path.join(ROOT, "output/pptx/SSVEP在线识别稳定性研究_严老师模板_20261019.pptx");
const TEMPLATE = "/Users/geniusalu/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_f2dv19yyfpih22_853a/temp/drag/严颖PPT模板 - 副本.pptx";
const WORK = path.join(ROOT, ".codex-ppt-build/ssvep-deck-rev2");
const CANDIDATE = path.join(WORK, ".finalizer/candidate-rev2.pptx");
const FINAL = path.join(ROOT, "output/pptx/SSVEP在线识别稳定性研究_图示重构版_20261019.pptx");
const RECEIPT = path.join(WORK, ".finalizer/SSVEP在线识别稳定性研究_图示重构版_20261019.validation.json");
const RENDER = path.join(WORK, "rendered");
const SKILL = "/Users/geniusalu/.codex/plugins/cache/openai-primary-runtime/presentations/26.1015.12040/skills/presentations";
const MODULES = "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules";
process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES = MODULES;

const { FileBlob, PresentationFile } = await import(pathToFileURL(path.join(MODULES, "@oai/artifact-tool/dist/artifact_tool.mjs")).href);
const { finalizePresentation } = await import(pathToFileURL(path.join(SKILL, "container_tools/artifact_tool_utils.mjs")).href);

const P = {
  hardware: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-a8c59fa3-7916-48d9-95fe-35be9b6a750a.png",
  lslLive: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-95a086a7-1c2f-48e5-8b6e-01593f3093dc.png",
  qualityLive: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-7fc6738c-4054-42d8-ba1b-f12dcbd6bd96.png",
  acquisitionLive: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-7b8d8fa2-ef8c-4560-b9e5-825a440eb399.png",
  onlineDrive: path.join(WORK, "screens/ssvep-online-drive.png"),
  commandTest: path.join(WORK, "screens/ssvep-command-test.png"),
  isolated: path.join(WORK, "screens/single-target-isolated.png"),
  guided: path.join(WORK, "screens/single-target-guided.png"),
  singlePage: path.join(WORK, "screens/research-single-target.png"),
  algorithm: path.join(WORK, "screens/research-algorithm.png"),
  experiment: path.join(WORK, "screens/research-experiment.png"),
  paradigm: path.join(WORK, "screens/research-paradigm.png"),
  mapping: path.join(WORK, "screens/research-mapping.png"),
};
const bytes = Object.fromEntries(await Promise.all(Object.entries(P).map(async ([key, value]) => [key, new Uint8Array(await fs.readFile(value))])));

const C = {
  blue: "#1F2096", blue2: "#1D41D5", indigo: "#5D69AE", cyan: "#00AFC6",
  red: "#C00000", orange: "#E67E22", green: "#178A5B", ink: "#202124",
  gray: "#5B6472", muted: "#8A93A3", line: "#D6DDED", pale: "#F4F7FD",
  bluePale: "#EAF0FF", cyanPale: "#E9FAFC", redPale: "#FFF2F2", orangePale: "#FFF7EC",
  white: "#FFFFFF", black: "#000000",
};
const FONT = "微软雅黑";
const BODY = "等线";

await fs.mkdir(path.dirname(CANDIDATE), { recursive: true });
await fs.mkdir(path.dirname(FINAL), { recursive: true });
await fs.mkdir(RENDER, { recursive: true });

const presentation = await PresentationFile.importPptx(await FileBlob.load(SOURCE));
presentation.slides.getItem(16).duplicate();
const cover = presentation.slides.getItem(0);
const contents = presentation.slides.getItem(1);
const contentSlides = presentation.slides.items.slice(2, 18);
const thanks = presentation.slides.getItem(18);

const inspectBefore = await presentation.inspect({ select: "$.slides[*]", include: ["id", "index", "elements.id"] });
for (const rec of inspectBefore.ndjson.trim().split("\n").map((line) => JSON.parse(line))) {
  if (rec.index >= 2 && rec.index <= 17) {
    const ids = (rec.elements ?? []).map((e) => e.id).filter(Boolean);
    if (ids.length) presentation.delete(ids);
  }
}

function addText(slide, text, position, opts = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox", name: opts.name,
    position, fill: opts.fill ?? "none",
    line: opts.line ?? { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontSize: opts.fontSize ?? 22, typeface: opts.typeface ?? BODY,
    color: opts.color ?? C.ink, bold: opts.bold ?? false,
    alignment: opts.alignment ?? "left", verticalAlignment: opts.verticalAlignment ?? "top",
    autoFit: opts.autoFit ?? "shrinkText", wrap: "square",
    lineSpacing: opts.lineSpacing ?? 1.08,
    insets: opts.insets ?? { left: 7, right: 7, top: 5, bottom: 5 },
  };
  return shape;
}
function box(slide, position, opts = {}) {
  const geometry = opts.geometry ?? "roundRect";
  return slide.shapes.add({
    geometry, position, name: opts.name,
    fill: opts.fill ?? C.white,
    line: opts.line ?? { style: "solid", fill: opts.lineColor ?? C.line, width: opts.lineWidth ?? 1 },
    ...(["rect", "textbox", "roundRect"].includes(geometry) ? { borderRadius: opts.borderRadius ?? "rounded-lg" } : {}),
  });
}
function title(slide, text) {
  addText(slide, text, { left: 40, top: 36, width: 930, height: 55 }, {
    fontSize: 39, typeface: FONT, color: C.blue, bold: true, verticalAlignment: "middle", insets: 0,
  });
}
function node(slide, x, y, w, h, head, sub = "", opts = {}) {
  box(slide, { left: x, top: y, width: w, height: h }, { fill: opts.fill ?? C.pale, lineColor: opts.lineColor ?? C.indigo, lineWidth: opts.lineWidth ?? 1.2 });
  if (opts.accent) box(slide, { left: x, top: y, width: 7, height: h }, { geometry: "rect", fill: opts.accent, line: { style: "solid", fill: opts.accent, width: 0 }, borderRadius: 0 });
  addText(slide, head, { left: x + 14, top: y + 12, width: w - 28, height: sub ? 34 : h - 22 }, {
    fontSize: opts.headSize ?? 22, color: opts.headColor ?? C.blue, bold: true,
    alignment: opts.alignment ?? "center", verticalAlignment: sub ? "middle" : "middle", insets: 0,
  });
  if (sub) addText(slide, sub, { left: x + 15, top: y + 52, width: w - 30, height: h - 64 }, {
    fontSize: opts.subSize ?? 17, color: opts.subColor ?? C.gray,
    alignment: opts.alignment ?? "center", verticalAlignment: "middle", insets: 0,
  });
}
function arrow(slide, x, y, w = 38, h = 24, fill = C.indigo) {
  box(slide, { left: x, top: y, width: w, height: h }, { geometry: "rightArrow", fill, line: { style: "solid", fill, width: 0 } });
}
function line(slide, x, y, w, h, fill = C.indigo) {
  box(slide, { left: x, top: y, width: w, height: h }, { geometry: "rect", fill, line: { style: "solid", fill, width: 0 }, borderRadius: 0 });
}
function img(slide, key, position, opts = {}) {
  if (opts.frame !== false) box(slide, { left: position.left - 3, top: position.top - 3, width: position.width + 6, height: position.height + 6 }, { fill: C.white, lineColor: opts.lineColor ?? C.line, lineWidth: 1.2 });
  return slide.images.add({
    blob: bytes[key], contentType: "image/png", alt: opts.alt ?? key,
    fit: opts.fit ?? "cover", position,
    geometry: opts.geometry ?? "roundRect", borderRadius: opts.borderRadius ?? "rounded-lg",
    ...(opts.crop ? { crop: opts.crop } : {}),
  });
}
function caption(slide, text, x, y, w) {
  addText(slide, text, { left: x, top: y, width: w, height: 25 }, { fontSize: 13, color: C.muted, alignment: "center", insets: 0, verticalAlignment: "middle" });
}
function footer(slide, text) {
  addText(slide, text, { left: 42, top: 691, width: 1120, height: 18 }, { fontSize: 10.5, color: C.muted, insets: 0, verticalAlignment: "middle" });
}
function notes(slide, text) { slide.speakerNotes.text = text; }

// Cover and contents.
for (const shape of cover.shapes.items) {
  if (shape.name === "副标题 8") {
    shape.text = "面向脑控无人机的 SSVEP 在线识别稳定性研究";
    shape.text.style = { fontSize: 46, typeface: FONT, color: "#0070C0", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  } else if (shape.name === "文本占位符 6") {
    shape.text = "汇报人：________";
    shape.text.style = { fontSize: 34, typeface: FONT, color: "#8C8C8C", bold: true, alignment: "center", verticalAlignment: "middle" };
  } else if (shape.name === "文本占位符 4") {
    shape.text = "系统链路、问题定位与改进方案 · 2026 年 10 月";
    shape.text.style = { fontSize: 24, typeface: BODY, color: "#777777", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  }
}
notes(cover, "本报告使用当前项目真实界面截图，聚焦 SSVEP 在线识别稳定性。研究范围不包含无人机任务分配。\n项目路径：/Users/geniusalu/Documents/brain-drone-swarm-console");
for (const shape of contents.shapes.items) {
  if (shape.name === "文本框 92") {
    shape.text = "1. 系统背景与现有链路\n2. 核心问题与问题树\n3. 问题的重要性\n4. 关键挑战\n5. 已有方法与文献综述\n6. 尚未解决的难点\n7. 拟议创新与贡献\n8. 方法细节\n9. 实验验证\n10. 总结";
    shape.text.style = { fontSize: 24, typeface: FONT, color: C.blue2, bold: true, alignment: "left", verticalAlignment: "top", autoFit: "shrinkText", lineSpacing: 1.05 };
  }
}
notes(contents, "目录对应老师要求的十部分结构。新版本用系统链路和问题树替代分散的问题描述。");

// 3 Background: actual hardware and online control.
{
  const s = contentSlides[0]; title(s, "1  系统背景：从视觉刺激到无人机动作");
  img(s, "hardware", { left: 55, top: 125, width: 365, height: 385 }, { alt: "后枕区电极帽和采集盒", crop: { left: 0.04, top: 0.15, right: 0.08, bottom: 0.20 } });
  img(s, "onlineDrive", { left: 455, top: 125, width: 760, height: 385 }, { alt: "当前项目 SSVEP 在线驾驶界面" });
  caption(s, "后枕区八通道 EEG 采集", 55, 518, 365);
  caption(s, "当前项目的六目标 SSVEP 在线驾驶界面（8–13 Hz）", 455, 518, 760);
  addText(s, "目标：用户注视哪个闪烁块，系统就输出对应指令。研究重点是解码正确性与在线稳定性。", { left: 75, top: 565, width: 1125, height: 72 }, { fontSize: 27, bold: true, color: C.blue, alignment: "center", fill: C.bluePale, line: { style: "solid", fill: C.indigo, width: 1 }, verticalAlignment: "middle" });
  footer(s, "图片：项目设备照片；本次从当前 PySide 工程重新渲染的在线驾驶窗口");
  notes(s, "这一页只讲任务目标和已完成系统。不要展开算法细节。当前配置来自 src/bci/config.py：六类命令对应 8–13 Hz。界面截图由当前项目代码现场重新渲染。");
}

// 4 Editable system chain plus live LSL screen.
{
  const s = contentSlides[1]; title(s, "1  现有系统的数据与控制链路");
  img(s, "lslLive", { left: 65, top: 115, width: 500, height: 300 }, { alt: "真实 LSL 脑电收集页面", crop: { left: 0, top: 0.03, right: 0, bottom: 0.02 } });
  addText(s, "实时 LSL 收集：8 通道、约 250 Hz，并保留时间戳和 CSV", { left: 65, top: 425, width: 500, height: 48 }, { fontSize: 18, bold: true, color: C.blue, alignment: "center", insets: 0 });
  const stages = [
    ["EEG 采集", "O1 / Oz / O2\nPO3 / POz / PO4 / P3 / P4"],
    ["LSL 与预处理", "时间戳、滤波\n窗口切分"],
    ["SSVEP 解码", "FBCCA\n个人模板 / Transformer"],
    ["指令判决", "阈值、置信度\n连续确认"],
    ["任务映射", "起飞、移动\n降落"],
    ["无人机飞行", "安全门控\n控制执行"],
  ];
  stages.forEach(([h, sub], i) => {
    const x = 595 + (i % 3) * 205;
    const y = 125 + Math.floor(i / 3) * 185;
    node(s, x, y, 165, 115, h, sub, { fill: i < 3 ? C.bluePale : C.cyanPale, accent: i === 5 ? C.red : (i < 3 ? C.blue2 : C.cyan), headSize: 20, subSize: 15 });
    if (i % 3 < 2) arrow(s, x + 170, y + 45, 27, 20);
    if (i === 2) {
      line(s, 1135, 240, 4, 42, C.indigo);
      line(s, 730, 279, 409, 4, C.indigo);
      arrow(s, 705, 270, 28, 20, C.indigo);
    }
  });
  addText(s, "当前故障位置不在单一模块，而在整条链路的共同作用。", { left: 625, top: 520, width: 560, height: 70 }, { fontSize: 25, bold: true, color: C.red, alignment: "center", fill: C.redPale, line: { style: "solid", fill: "#F3B4B4", width: 1 }, verticalAlignment: "middle" });
  footer(s, "系统主链：硬件 EEG → LSL/预处理 → 解码 → 判决 → 动作映射 → 飞行控制");
  notes(s, "这一页建立全文统一主线。当前项目 README 说明，士兵脑控链路会启动 ExpandableEEG、发布 obci_eeg1，由 EEGWorker 运行解码，再经过安全门控进入 Flow Hover 控制。截图由用户提供的真实采集会话获得。");
}

// 5 Problem tree.
{
  const s = contentSlides[2]; title(s, "2  核心问题：注视目标与输出指令不一致");
  node(s, 55, 265, 250, 150, "在线错误指令", "盯着“前进”时\n可能输出“起飞”", { fill: C.redPale, lineColor: C.red, accent: C.red, headColor: C.red, headSize: 28, subSize: 20 });
  line(s, 305, 338, 70, 5, C.red);
  line(s, 372, 155, 5, 372, C.red);
  const causes = [
    ["信号质量", "电极接触、头发\n坏通道、工频污染", C.blue2, C.bluePale],
    ["刺激与校准", "单目标与六目标\n视觉条件不一致", C.cyan, C.cyanPale],
    ["解码器", "FBCCA 偏置\n模板漂移、小样本", C.orange, C.orangePale],
    ["在线判决", "93.75% 窗口重叠\n缺少 idle / 释放", C.red, C.redPale],
  ];
  causes.forEach(([h, sub, color, fill], i) => {
    const y = 115 + i * 135;
    line(s, 372, y + 55, 55, 4, color);
    node(s, 425, y, 330, 110, h, sub, { fill, lineColor: color, accent: color, alignment: "left", headSize: 22, subSize: 17 });
  });
  line(s, 755, 337, 70, 5, C.indigo);
  node(s, 825, 210, 390, 260, "表现为两个相反结果", "阈值放宽：频繁出现错误指令\n\n阈值收紧：长时间完全不输出\n\n“有输出”不能代表“识别正确”", { fill: C.pale, lineColor: C.indigo, accent: C.indigo, headSize: 25, subSize: 21, alignment: "left" });
  footer(s, "问题树把分散现象归并成四类原因，后续方法与实验逐项对应");
  notes(s, "核心问题只有一个：在线输出与真实注视意图不一致。四个分支是原因，不是四个平行研究题目。汇报时按信号、协议、解码器、判决逻辑顺序展开。");
}

// 6 Importance with mapping screenshot and risk chain.
{
  const s = contentSlides[3]; title(s, "3  错误识别会直接变成飞行动作");
  img(s, "mapping", { left: 55, top: 120, width: 565, height: 370 }, { alt: "SSVEP 频率与无人机动作映射页面" });
  caption(s, "科研界面把六个频率直接映射为飞行动作", 55, 500, 565);
  const risk = [
    ["频率误判", "9 Hz 被判为 8 Hz"],
    ["意图误译", "前进变成起飞"],
    ["错误触发", "动作进入控制队列"],
    ["飞行风险", "碰撞、失控或重复动作"],
  ];
  risk.forEach(([h, sub], i) => {
    const y = 125 + i * 125;
    node(s, 720, y, 410, 92, `${String(i + 1).padStart(2, "0")}  ${h}`, sub, { fill: i === 3 ? C.redPale : C.pale, lineColor: i === 3 ? C.red : C.indigo, accent: i === 3 ? C.red : C.cyan, alignment: "left", headSize: 21, subSize: 16 });
    if (i < 3) box(s, { left: 905, top: y + 95, width: 32, height: 24 }, { geometry: "downArrow", fill: C.indigo, line: { style: "solid", fill: C.indigo, width: 0 } });
  });
  addText(s, "评价必须同时报告正确率、错误触发次数和响应时延。", { left: 85, top: 595, width: 1110, height: 54 }, { fontSize: 26, bold: true, color: C.blue, alignment: "center", fill: C.bluePale, line: { style: "solid", fill: C.indigo, width: 1 }, verticalAlignment: "middle" });
  notes(s, "截图由当前项目科研映射页面重新渲染。无人机控制不同于普通分类：错误类别会被映射成具体动作，因此需要 FTR/min、Latency、ITR 和 idle 指标，不能只报告离线准确率。");
}

// 7 Challenge: signal quality screenshot.
{
  const s = contentSlides[4]; title(s, "4  挑战一：每次佩戴的有效通道不同");
  img(s, "qualityLive", { left: 50, top: 115, width: 760, height: 445 }, { alt: "真实信号质量页面显示 4/8 通道可用" });
  caption(s, "一次真实采集中，软件只判定 4 / 8 个通道可用", 50, 570, 760);
  node(s, 855, 130, 340, 125, "干电极与头发", "接触阻抗和运动伪迹随佩戴改变", { fill: C.redPale, lineColor: C.red, accent: C.red, alignment: "left" });
  node(s, 855, 280, 340, 125, "坏通道会拖累融合", "通道更多不等于信息更多", { fill: C.orangePale, lineColor: C.orange, accent: C.orange, alignment: "left" });
  node(s, 855, 430, 340, 125, "固定三通道仍有限", "Oz、PO3、PO4较稳，但跨会话仍会变化", { fill: C.bluePale, lineColor: C.blue2, accent: C.blue2, alignment: "left" });
  addText(s, "因此需要根据当前会话质量自动加权，而不是单纯增加增益或通道数。", { left: 105, top: 615, width: 1070, height: 48 }, { fontSize: 24, bold: true, color: C.red, alignment: "center", insets: 0 });
  notes(s, "用户提供的真实界面显示 4/8 通道可用。项目最近 60 段个人校准数据中，Oz/PO3/PO4 跨试次可用率较高，O1 和 P4 较低。增加硬件增益会同时放大信号和噪声，不能替代接触质量与通道质量估计。");
}

// 8 Protocol mismatch and overlap.
{
  const s = contentSlides[5]; title(s, "4  挑战二：校准条件与在线识别并不等价");
  img(s, "isolated", { left: 55, top: 125, width: 485, height: 300 }, { alt: "单目标独闪采集窗口" });
  img(s, "commandTest", { left: 665, top: 125, width: 550, height: 300 }, { alt: "六目标同时闪烁的指令测试窗口" });
  caption(s, "单目标模板：只有一个位置闪烁", 55, 435, 485);
  caption(s, "正式测试：六个目标同时闪烁", 665, 435, 550);
  arrow(s, 575, 258, 55, 30, C.red);
  addText(s, "视觉竞争与周边刺激发生变化", { left: 535, top: 305, width: 150, height: 62 }, { fontSize: 16, bold: true, color: C.red, alignment: "center", insets: 0 });
  line(s, 105, 525, 700, 54, "#D7E4FF");
  line(s, 148, 591, 700, 54, "#BDECEE");
  addText(s, "窗口 t：4.00 s", { left: 105, top: 525, width: 700, height: 54 }, { fontSize: 20, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "窗口 t+1：只新增 0.25 s，93.75% 与上一窗相同", { left: 148, top: 591, width: 700, height: 54 }, { fontSize: 20, bold: true, color: "#126A6A", alignment: "center", verticalAlignment: "middle", insets: 0 });
  node(s, 900, 525, 300, 120, "两个相关性问题", "校准与测试的场景差异\n相邻在线窗口的数据重复", { fill: C.redPale, lineColor: C.red, accent: C.red, headColor: C.red, headSize: 22, subSize: 17 });
  notes(s, "两个问题都属于‘训练/证据条件不一致’：单目标模板的视觉条件与六目标在线场景不同；在线窗口又高度重叠。重叠本身不是错误，问题是把强相关输出当成独立确认。");
}

// 9 Literature map with algorithm page.
{
  const s = contentSlides[6]; title(s, "5  已有方法分别解决链路中的不同环节");
  const methods = [
    ["CCA / FBCCA", "无训练频率参考", "利用基频、谐波和多子带", C.blue2, C.bluePale],
    ["训练数据 CCA", "个人参考模板", "适应个体相位与波形", C.cyan, C.cyanPale],
    ["eTRCA / TDCA", "个人时空滤波", "强调重复性与类别分离", C.orange, C.orangePale],
    ["Transformer", "数据驱动表征", "需要更大且稳定的数据集", C.red, C.redPale],
  ];
  methods.forEach(([h, tag, sub, color, fill], i) => {
    const y = 125 + i * 123;
    node(s, 55, y, 470, 98, h, `${tag}：${sub}`, { fill, lineColor: color, accent: color, alignment: "left", headSize: 21, subSize: 16 });
  });
  img(s, "algorithm", { left: 570, top: 120, width: 645, height: 420 }, { alt: "当前科研算法页面" });
  caption(s, "当前系统已提供 FBCCA、个人模板和 Transformer 三条算法路线", 570, 550, 645);
  addText(s, "个人模板 + FBCCA 属于已有技术组合，论文创新需要落到尚未解决的在线问题。", { left: 95, top: 605, width: 1090, height: 54 }, { fontSize: 23, bold: true, color: C.red, alignment: "center", fill: C.redPale, line: { style: "solid", fill: "#F3B4B4", width: 1 }, verticalAlignment: "middle" });
  notes(s, "主要文献：Chen et al., FBCCA, J Neural Eng 2015, https://pubmed.ncbi.nlm.nih.gov/26035476/；Nakanishi et al., eTRCA, IEEE TBME 2018, https://pubmed.ncbi.nlm.nih.gov/28436836/；Liu et al., TDCA, IEEE TNSRE 2021, https://bingchuanliu.github.io/assets/pdf/liu_tnsre.pdf；训练数据 CCA：https://pubmed.ncbi.nlm.nih.gov/32380925/。右图由当前项目算法页面重新渲染。");
}

// 10 Online decision literature.
{
  const s = contentSlides[7]; title(s, "5  在线判决：连续确认与动态停止仍有局限");
  const xs = [65, 365, 665, 965];
  const labels = [
    ["固定窗口", "积累固定时长 EEG\n再做一次分类", C.blue2],
    ["连续确认", "要求若干窗口同类\n实现简单", C.indigo],
    ["动态停止", "证据足够时提前输出\n证据弱时延长", C.cyan],
    ["现有缺口", "高度重叠窗口的\n有效新增证据未明确", C.red],
  ];
  labels.forEach(([h, sub, color], i) => {
    node(s, xs[i], 175, 235, 165, h, sub, { fill: i === 3 ? C.redPale : C.pale, lineColor: color, accent: color, headColor: i === 3 ? C.red : C.blue, headSize: 24, subSize: 18 });
    if (i < 3) arrow(s, xs[i] + 245, 245, 38, 24, C.indigo);
  });
  addText(s, "论文复现：2 s 窗、0.25 s 步长，重叠 87.5%", { left: 100, top: 405, width: 490, height: 58 }, { fontSize: 23, bold: true, color: C.blue, alignment: "center", fill: C.bluePale, line: { style: "solid", fill: C.indigo, width: 1 }, verticalAlignment: "middle" });
  addText(s, "当前项目：4 s 窗、0.25 s 步长，重叠 93.75%", { left: 690, top: 405, width: 490, height: 58 }, { fontSize: 23, bold: true, color: C.red, alignment: "center", fill: C.redPale, line: { style: "solid", fill: C.red, width: 1 }, verticalAlignment: "middle" });
  node(s, 190, 520, 900, 110, "研究问题", "如何估计每次更新真正新增的信息，并让 idle / 释放状态参与决策？", { fill: C.cyanPale, lineColor: C.cyan, accent: C.cyan, headSize: 23, subSize: 20 });
  footer(s, "重叠不是论文错误；论文没有分析窗口独立性或不同重叠率的影响");
  notes(s, "本地复现论文使用 2 s 窗、0.25 s 步长、连续 2 次确认、置信度 0.6。论文没有把强重叠明确建模。Adaptive Window FBCCA: https://ieeexplore.ieee.org/ielx7/7333/10031624/09931747.pdf；一般重叠窗口独立性讨论：https://pmc.ncbi.nlm.nih.gov/articles/PMC3634773/。");
}

// 11 Remaining gaps, aligned to problem tree.
{
  const s = contentSlides[8]; title(s, "6  尚未解决的难点与可验证问题");
  const gaps = [
    ["通道质量动态变化", "质量加权能否降低误触发？", C.blue2, C.bluePale],
    ["单目标与多目标域差异", "相同几何的引导校准是否更稳？", C.cyan, C.cyanPale],
    ["窗口高度重叠", "按新增信息量修正能否改善稳定性？", C.orange, C.orangePale],
    ["缺少 idle 与释放", "显式控制状态能否降低 FTR/min？", C.red, C.redPale],
  ];
  line(s, 640, 135, 5, 435, C.indigo);
  gaps.forEach(([h, q, color, fill], i) => {
    const y = 120 + i * 135;
    node(s, 60, y, 470, 105, h, "现有方法只覆盖部分场景", { fill, lineColor: color, accent: color, alignment: "left", headSize: 22, subSize: 15 });
    line(s, 530, y + 50, 110, 4, color);
    arrow(s, 650, y + 40, 38, 24, color);
    node(s, 690, y, 520, 105, "研究问题", q, { fill: C.white, lineColor: color, accent: color, alignment: "left", headSize: 18, subSize: 19 });
  });
  addText(s, "每个创新模块都必须对应一个可证伪问题。", { left: 180, top: 620, width: 920, height: 45 }, { fontSize: 25, bold: true, color: C.blue, alignment: "center", insets: 0 });
  notes(s, "这一页连接问题树和创新点。不能把‘换更复杂网络’作为研究空白。真正的研究问题必须能通过对照实验回答。");
}

// 12 Innovation overview.
{
  const s = contentSlides[9]; title(s, "7  拟议贡献：四个模块共同服务在线稳定识别");
  node(s, 470, 265, 340, 150, "稳定在线识别", "注视目标与输出指令一致\n降低错误触发", { fill: C.bluePale, lineColor: C.blue2, lineWidth: 2.2, headSize: 29, subSize: 20 });
  const modules = [
    [70, 125, "一致校准协议", "正式界面相同尺寸与位置", C.blue2, C.bluePale],
    [870, 125, "质量感知解码", "动态通道权重 + TDCA/FBCCA", C.cyan, C.cyanPale],
    [70, 465, "重叠感知证据", "只累积真正新增的信息", C.orange, C.orangePale],
    [870, 465, "idle / 释放状态", "未形成意图时不发指令", C.red, C.redPale],
  ];
  modules.forEach(([x, y, h, sub, color, fill]) => node(s, x, y, 330, 125, h, sub, { fill, lineColor: color, accent: color, headSize: 22, subSize: 17 }));
  line(s, 400, 185, 120, 4, C.indigo); line(s, 760, 185, 110, 4, C.indigo);
  line(s, 400, 525, 120, 4, C.indigo); line(s, 760, 525, 110, 4, C.indigo);
  line(s, 517, 185, 4, 80, C.indigo); line(s, 760, 185, 4, 80, C.indigo);
  line(s, 517, 415, 4, 112, C.indigo); line(s, 760, 415, 4, 112, C.indigo);
  addText(s, "创新候选，需要继续查新并通过消融和跨会话在线实验验证。", { left: 200, top: 625, width: 880, height: 46 }, { fontSize: 21, bold: true, color: C.red, alignment: "center", insets: 0 });
  notes(s, "四个模块分别对应前页四个未解决问题。个人模板、FBCCA、TDCA、通道选择和动态停止都有文献，不能单独声称创新。可能的贡献在于对当前在线链路的统一建模及严格验证。");
}

// 13 Full proposed pipeline.
{
  const s = contentSlides[10]; title(s, "8.1  拟议方法的完整流程");
  const stages = [
    ["校准数据", "相同布局\n随机目标"], ["质量估计", "SNR、工频\n重复性"],
    ["个人解码", "TDCA / eTRCA\n模板概率"], ["频率先验", "FBCCA\n谐波概率"],
    ["概率融合", "质量决定\n融合权重"], ["序贯判决", "重叠修正\n动态停止"],
    ["控制状态", "idle、触发\n保持、释放"],
  ];
  stages.forEach(([h, sub], i) => {
    const x = 45 + i * 172;
    node(s, x, 205, 140, 160, `${i + 1}. ${h}`, sub, { fill: i < 4 ? C.bluePale : (i < 6 ? C.cyanPale : C.redPale), lineColor: i === 6 ? C.red : (i < 4 ? C.blue2 : C.cyan), headSize: 19, subSize: 16 });
    if (i < 6) arrow(s, x + 145, 270, 22, 18, C.indigo);
  });
  addText(s, "训练阶段", { left: 45, top: 145, width: 656, height: 42 }, { fontSize: 20, bold: true, color: C.blue, alignment: "center", fill: C.bluePale, verticalAlignment: "middle" });
  addText(s, "在线阶段", { left: 733, top: 145, width: 516, height: 42 }, { fontSize: 20, bold: true, color: "#126A6A", alignment: "center", fill: C.cyanPale, verticalAlignment: "middle" });
  node(s, 70, 445, 340, 130, "输入", "8 通道 EEG，250 Hz\n4 s 主窗，每 0.25 s 更新", { fill: C.pale, lineColor: C.indigo, accent: C.blue2, headSize: 22, subSize: 18 });
  node(s, 470, 445, 340, 130, "输出", "六类指令概率 + idle 概率\n有效证据量与决策状态", { fill: C.pale, lineColor: C.indigo, accent: C.cyan, headSize: 22, subSize: 18 });
  node(s, 870, 445, 340, 130, "优化目标", "提高正确率与 ITR\n降低 FTR/min 与响应时延", { fill: C.redPale, lineColor: C.red, accent: C.red, headSize: 22, subSize: 18 });
  addText(s, "训练和测试必须按会话分开，禁止相邻重叠窗口跨集合。", { left: 170, top: 620, width: 940, height: 42 }, { fontSize: 22, bold: true, color: C.red, alignment: "center", insets: 0 });
  notes(s, "方法结构是拟议方案。主体个人解码器优先比较 TDCA 与 eTRCA。所有通道权重、融合系数和阈值只在训练折或校准集确定。");
}

// 14 Quality-aware detail with real waveform screenshot.
{
  const s = contentSlides[11]; title(s, "8.2  质量感知个人解码");
  img(s, "acquisitionLive", { left: 45, top: 115, width: 660, height: 385 }, { alt: "真实八通道波形与频谱页面" });
  caption(s, "实时波形和频谱可用于估计通道质量、工频污染与漂移", 45, 510, 660);
  const qs = [["目标频段 SNR", C.blue2], ["50 Hz 污染比例", C.red], ["跨试次重复性", C.cyan], ["饱和与慢漂移", C.orange]];
  qs.forEach(([text, color], i) => node(s, 760, 125 + i * 94, 420, 72, text, "", { fill: C.pale, lineColor: color, accent: color, alignment: "left", headSize: 20 }));
  addText(s, "X′c = qc · Xc", { left: 760, top: 515, width: 200, height: 46 }, { fontSize: 26, bold: true, color: C.blue, alignment: "center", fill: C.bluePale, verticalAlignment: "middle" });
  addText(s, "pt = αt pTDCA + (1−αt) pFBCCA", { left: 975, top: 515, width: 230, height: 46 }, { fontSize: 20, bold: true, color: "#126A6A", alignment: "center", fill: C.cyanPale, verticalAlignment: "middle" });
  addText(s, "质量差的通道降权；当前会话越接近个人模板，αt 越大。", { left: 100, top: 590, width: 1080, height: 62 }, { fontSize: 24, bold: true, color: C.blue, alignment: "center", fill: C.pale, line: { style: "solid", fill: C.line, width: 1 }, verticalAlignment: "middle" });
  notes(s, "用户提供的真实采集页面展示八通道波形和实时频谱。qc 是每个通道的质量权重；αt 是个人模型与 FBCCA 的融合权重。两者必须用训练数据校准，不能偷看测试标签。");
}

// 15 Overlap-aware decision detail.
{
  const s = contentSlides[12]; title(s, "8.3  重叠感知的序贯决策");
  line(s, 75, 145, 760, 58, "#D7E4FF");
  line(s, 125, 220, 760, 58, "#BDECEE");
  addText(s, "窗口 t：4.00 s 脑电", { left: 75, top: 145, width: 760, height: 58 }, { fontSize: 21, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "窗口 t+1：新增 0.25 s，新增比例 Δn = S/L = 0.0625", { left: 125, top: 220, width: 760, height: 58 }, { fontSize: 21, bold: true, color: "#126A6A", alignment: "center", verticalAlignment: "middle", insets: 0 });
  box(s, { left: 835, top: 220, width: 50, height: 58 }, { geometry: "rect", fill: "#FFB5B0", lineColor: C.red, borderRadius: 0 });
  addText(s, "新增\n证据", { left: 822, top: 285, width: 75, height: 45 }, { fontSize: 16, bold: true, color: C.red, alignment: "center", insets: 0 });
  node(s, 930, 145, 285, 170, "初版证据更新", "Et = Et−1 + Δn × log[(p1+ε)/(p2+ε)]\n\n只按新增样本比例累积", { fill: C.pale, lineColor: C.indigo, accent: C.indigo, headSize: 22, subSize: 17 });
  const states = [["idle", "不输出"], ["候选", "证据上升"], ["确认", "超过 θon"], ["保持", "继续同类"], ["释放", "idle > θoff"]];
  states.forEach(([h, sub], i) => {
    const x = 65 + i * 238;
    node(s, x, 405, 195, 105, h, sub, { fill: i === 4 ? C.redPale : C.bluePale, lineColor: i === 4 ? C.red : C.blue2, headColor: i === 4 ? C.red : C.blue, headSize: 21, subSize: 16 });
    if (i < 4) arrow(s, x + 200, 448, 28, 20, C.indigo);
  });
  addText(s, "公式是第一版可实现假设；后续与普通连续确认、动态停止、HMM 或贝叶斯滤波比较。", { left: 85, top: 570, width: 1110, height: 66 }, { fontSize: 22, bold: true, color: C.red, alignment: "center", fill: C.redPale, line: { style: "solid", fill: "#F3B4B4", width: 1 }, verticalAlignment: "middle" });
  notes(s, "该公式尚未证明有效。实验要比较普通连续确认、按新增比例修正、按实测相关性修正、HMM/贝叶斯证据累积。触发和释放阈值分开，可降低命令抖动和错误保持。");
}

// 16 Experiment validation with screenshots.
{
  const s = contentSlides[13]; title(s, "9  实验验证：从校准到安全在线测试");
  img(s, "guided", { left: 45, top: 125, width: 360, height: 230 }, { alt: "六目标引导校准窗口" });
  img(s, "experiment", { left: 460, top: 125, width: 360, height: 230 }, { alt: "科研 CSV 实验与算法验证页面" });
  img(s, "commandTest", { left: 875, top: 125, width: 360, height: 230 }, { alt: "六目标指令测试窗口" });
  caption(s, "① 同布局引导校准", 45, 365, 360);
  caption(s, "② 按会话离线比较", 460, 365, 360);
  caption(s, "③ 有提示在线测试", 875, 365, 360);
  arrow(s, 416, 225, 32, 22, C.indigo); arrow(s, 830, 225, 32, 22, C.indigo);
  node(s, 55, 430, 350, 150, "基线", "FBCCA、当前个人模板\neTRCA、TDCA、Transformer", { fill: C.bluePale, lineColor: C.blue2, accent: C.blue2, headSize: 22, subSize: 18 });
  node(s, 465, 430, 350, 150, "消融", "去掉质量权重\n去掉重叠修正\n去掉 idle / 释放", { fill: C.cyanPale, lineColor: C.cyan, accent: C.cyan, headSize: 22, subSize: 18 });
  node(s, 875, 430, 350, 150, "指标", "ACC、Macro-F1、FTR/min\nLatency、ITR、拒识率", { fill: C.redPale, lineColor: C.red, accent: C.red, headSize: 22, subSize: 18 });
  addText(s, "先做模拟控制，再进入低风险实机验证。", { left: 220, top: 620, width: 840, height: 42 }, { fontSize: 24, bold: true, color: C.red, alignment: "center", insets: 0 });
  notes(s, "三张图均由当前项目重新渲染：六目标引导校准、科研实验回放、六目标指令测试。在线实验必须由系统随机提示目标，提示标签作为标准答案。数据按会话划分，禁止相邻窗跨集合。");
}

// 17 Current evidence and claim boundary.
{
  const s = contentSlides[14]; title(s, "9  当前证据只能支持问题定位与基线建立");
  const nums = [
    ["7.4%", "Transformer 验证准确率", "低于 7 类随机基线约 14.3%", C.red],
    ["38.3%", "个人模板内部一致性", "不是独立在线准确率", C.orange],
    ["93.75%", "当前相邻窗口重叠率", "连续输出高度相关", C.blue2],
  ];
  nums.forEach(([n, h, sub, color], i) => {
    const y = 125 + i * 150;
    addText(s, n, { left: 55, top: y, width: 205, height: 90 }, { fontSize: 50, bold: true, color, alignment: "center", verticalAlignment: "middle", insets: 0 });
    addText(s, h, { left: 255, top: y + 5, width: 330, height: 38 }, { fontSize: 22, bold: true, color: C.blue, insets: 0 });
    addText(s, sub, { left: 255, top: y + 47, width: 330, height: 40 }, { fontSize: 17, color: C.gray, insets: 0 });
  });
  img(s, "singlePage", { left: 625, top: 120, width: 590, height: 385 }, { alt: "单目标个人模板采集页面" });
  caption(s, "当前单目标页面明确标注：留一试次一致性不等于真实准确率", 625, 515, 590);
  addText(s, "下一步必须补齐：跨会话数据、独立有提示在线测试、TDCA/eTRCA 强基线和完整消融。", { left: 80, top: 590, width: 1120, height: 70 }, { fontSize: 23, bold: true, color: C.red, alignment: "center", fill: C.redPale, line: { style: "solid", fill: "#F3B4B4", width: 1 }, verticalAlignment: "middle" });
  notes(s, "Transformer 元数据：78 条训练、27 条验证，验证准确率 7.4074%，空闲误触发率 100%。个人模板：60 段，每类 10 段，留一试次内部一致性 38.33%。这些数据不能支持‘新方法有效’，只能支持‘问题存在且已有基线’。");
}

// 18 Summary.
{
  const s = contentSlides[15]; title(s, "10  总结与下一步");
  node(s, 65, 135, 350, 410, "问题已收敛", "一个核心问题：\n注视目标与输出指令不一致\n\n四类原因：\n信号质量、校准协议、解码器、在线判决", { fill: C.redPale, lineColor: C.red, accent: C.red, alignment: "left", headSize: 25, subSize: 21 });
  node(s, 465, 135, 350, 410, "拟议方案", "一致布局校准\n\n质量感知 TDCA/eTRCA + FBCCA\n\n重叠感知序贯证据\n\n显式 idle 与释放", { fill: C.bluePale, lineColor: C.blue2, accent: C.blue2, alignment: "left", headSize: 25, subSize: 21 });
  node(s, 865, 135, 350, 410, "最小实验闭环", "复现 eTRCA / TDCA\n\n采集跨会话数据\n\n完成有提示在线实验\n\n根据消融结果收缩创新点", { fill: C.cyanPale, lineColor: C.cyan, accent: C.cyan, alignment: "left", headSize: 25, subSize: 21 });
  addText(s, "希望老师重点指导：主体算法选 TDCA 还是 eTRCA，以及“质量估计 + 重叠感知决策”是否足以形成论文主线。", { left: 90, top: 585, width: 1100, height: 75 }, { fontSize: 23, bold: true, color: C.blue, alignment: "center", fill: C.pale, line: { style: "solid", fill: C.indigo, width: 1 }, verticalAlignment: "middle" });
  notes(s, "收尾时不要说‘请老师给我一个新算法’，而是请老师在明确的问题和候选方案上做判断：主体算法选择、创新边界、实验规模和论文可行性。");
}

// Thank-you slide remains from template.
for (const shape of thanks.shapes.items) {
  if (shape.name === "文本框 1") {
    shape.text = "感 谢 各 位 专 家\n请批评指正";
    shape.text.style = { fontSize: 72, typeface: FONT, color: "#FF0000", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  }
}
notes(thanks, "建议现场请教：TDCA 或 eTRCA 的主体选择；质量感知与重叠感知是否有足够研究价值；在线实验样本量与安全流程。");

await (await PresentationFile.exportPptx(presentation)).save(CANDIDATE);
const result = await finalizePresentation({
  workspaceDir: ROOT,
  candidatePath: CANDIDATE,
  finalPath: FINAL,
  receiptPath: RECEIPT,
  pythonExecutable: "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3",
  integrityValidatorPath: path.join(SKILL, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(SKILL, "container_tools/inspect_presentation_layout_geometry.py"),
  explicitTotalSlideCount: 19,
  layoutArgs: [
    "--cover-role", "cover", "--cover-word-limit", "50",
    "--expected-aspect", "16:9", "--expected-slide-size-emu", "12192000,6858000",
    "--max-content-prose-words", "180",
  ],
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [],
  nativeChartTargetApplication: "powerpoint",
  approvedSourceFigureExceptionSlides: [],
  verifyArtifactToolImport: true,
  fontPolicy: {
    basis: "reference",
    families: ["方正大黑简体", "等线", "黑体", "微软雅黑", "Arial"],
    referencePath: TEMPLATE,
    referenceSha256: "987f0c4a1eb7cbfd9da023ff9975b42a8504b6a4fa6a603c7e7034532195a5da",
    scriptFonts: { ea: "微软雅黑" },
  },
});

const finalDeck = await PresentationFile.importPptx(await FileBlob.load(FINAL));
for (const [index, slide] of finalDeck.slides.items.entries()) {
  const stem = `slide-${String(index + 1).padStart(2, "0")}`;
  const png = await finalDeck.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(path.join(RENDER, `${stem}.png`), new Uint8Array(await png.arrayBuffer()));
  const layout = await slide.export({ format: "layout", detail: "full" });
  await fs.writeFile(path.join(RENDER, `${stem}.layout.json`), await layout.text());
}
const montage = await finalDeck.export({ format: "webp", montage: true, scale: 1 });
await fs.writeFile(path.join(RENDER, "montage.webp"), new Uint8Array(await montage.arrayBuffer()));
await fs.writeFile(path.join(WORK, "finalizer-result.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ final: FINAL, slides: finalDeck.slides.items.length, render: RENDER, sha256: result.finalSha256 }, null, 2));
