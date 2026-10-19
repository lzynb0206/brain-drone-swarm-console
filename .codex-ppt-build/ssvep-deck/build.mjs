import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

process.on("uncaughtException", (error) => {
  console.error(`BUILD_ERROR: ${error?.message ?? error}`);
  console.error(String(error?.stack ?? "").split("\n").filter((line) => line.includes("build.mjs")).join("\n"));
  process.exit(1);
});
process.on("unhandledRejection", (error) => {
  console.error(`BUILD_REJECTION: ${error?.message ?? error}`);
  console.error(String(error?.stack ?? "").split("\n").filter((line) => line.includes("build.mjs")).join("\n"));
  process.exit(1);
});

const artifactRoot = "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";
const skillRoot = "/Users/geniusalu/.codex/plugins/cache/openai-primary-runtime/presentations/26.1015.12040/skills/presentations";
process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES = "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules";
const { FileBlob, PresentationFile } = await import(pathToFileURL(artifactRoot).href);
const {
  finalizePresentation,
  applyPresentationChartFont,
} = await import(pathToFileURL(path.join(skillRoot, "container_tools/artifact_tool_utils.mjs")).href);

const ROOT = "/Users/geniusalu/Documents/brain-drone-swarm-console";
const TEMPLATE = "/Users/geniusalu/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_f2dv19yyfpih22_853a/temp/drag/严颖PPT模板 - 副本.pptx";
const WORK = path.join(ROOT, ".codex-ppt-build/ssvep-deck");
const CANDIDATE = path.join(WORK, ".finalizer/candidate.pptx");
const FINAL = path.join(ROOT, "output/pptx/SSVEP在线识别稳定性研究_严老师模板_20261019.pptx");
const RENDER = path.join(WORK, "rendered");
const IMG_CAP = "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-a8c59fa3-7916-48d9-95fe-35be9b6a750a.png";
const IMG_EEG = "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-e3374c95-1a4f-424a-b1d5-f248670facf3.png";
const IMG_UI = "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-f6eb3940-a802-483b-b761-522b330983a9.png";
const IMG_CAP_BYTES = new Uint8Array(await fs.readFile(IMG_CAP));
const IMG_UI_BYTES = new Uint8Array(await fs.readFile(IMG_UI));

const C = {
  blue: "#1F2096", blue2: "#1D41D5", blue3: "#5D69AE", cyan: "#00A6A6",
  navy: "#132A63", red: "#C00000", orange: "#E67E22", green: "#1B8A5A",
  ink: "#202124", gray: "#595959", mid: "#8A8F98", light: "#EEF2FF",
  pale: "#F6F8FC", white: "#FFFFFF", line: "#D8DEEA", black: "#000000",
};
const FONT = "微软雅黑";
const BODY = "等线";

await fs.mkdir(path.dirname(CANDIDATE), { recursive: true });
await fs.mkdir(path.dirname(FINAL), { recursive: true });
await fs.mkdir(RENDER, { recursive: true });

const presentation = await PresentationFile.importPptx(await FileBlob.load(TEMPLATE));
const cover = presentation.slides.getItem(0);
const contents = presentation.slides.getItem(1);
const contentSource = presentation.slides.getItem(2);
const thanks = presentation.slides.getItem(3);
const contentSlides = [contentSource];
for (let i = 0; i < 14; i += 1) contentSlides.push(contentSource.duplicate());
presentation.slides.reorder([
  `sl/${cover.id}`, `sl/${contents.id}`,
  ...contentSlides.map((slide) => `sl/${slide.id}`),
  `sl/${thanks.id}`,
]);

function addText(slide, text, position, opts = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox", name: opts.name,
    position, fill: opts.fill ?? "none",
    line: opts.line ?? { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontSize: opts.fontSize ?? 24,
    typeface: opts.typeface ?? BODY,
    color: opts.color ?? C.ink,
    bold: opts.bold ?? false,
    alignment: opts.alignment ?? "left",
    verticalAlignment: opts.verticalAlignment ?? "top",
    autoFit: opts.autoFit ?? "shrinkText",
    wrap: "square",
    lineSpacing: opts.lineSpacing ?? 1.08,
    insets: opts.insets ?? { left: 8, right: 8, top: 5, bottom: 5 },
  };
  return shape;
}
function addShape(slide, position, opts = {}) {
  const geometry = opts.geometry ?? "roundRect";
  return slide.shapes.add({
    geometry, name: opts.name,
    position, fill: opts.fill ?? C.white,
    line: opts.line ?? { style: "solid", fill: opts.lineColor ?? C.line, width: opts.lineWidth ?? 1 },
    ...(["rect", "textbox", "roundRect"].includes(geometry) ? { borderRadius: opts.borderRadius ?? "rounded-xl" } : {}),
    shadow: opts.shadow,
  });
}
function addTitle(slide, text, section) {
  addText(slide, text, { left: 40, top: 36, width: 1010, height: 55 }, {
    fontSize: 40, typeface: FONT, color: C.blue, bold: true, verticalAlignment: "middle", insets: 0,
  });
  if (section) {
    addShape(slide, { left: 1085, top: 42, width: 112, height: 36 }, { fill: C.light, lineColor: C.blue3 });
    addText(slide, section, { left: 1088, top: 43, width: 106, height: 32 }, {
      fontSize: 16, color: C.blue, bold: true, alignment: "center", verticalAlignment: "middle", insets: 0,
    });
  }
}
function addCard(slide, x, y, w, h, title, body, opts = {}) {
  addShape(slide, { left: x, top: y, width: w, height: h }, {
    fill: opts.fill ?? C.white, lineColor: opts.lineColor ?? C.line, shadow: opts.shadow,
  });
  if (opts.accent) addShape(slide, { left: x, top: y, width: 8, height: h }, { geometry: "rect", fill: opts.accent, line: { fill: "none", width: 0 } });
  addText(slide, title, { left: x + 18, top: y + 14, width: w - 36, height: 36 }, {
    fontSize: opts.titleSize ?? 23, color: opts.titleColor ?? C.blue, bold: true, verticalAlignment: "middle", insets: 0,
  });
  addText(slide, body, { left: x + 18, top: y + 56, width: w - 36, height: h - 70 }, {
    fontSize: opts.bodySize ?? 19, color: opts.bodyColor ?? C.ink, insets: 0, lineSpacing: 1.08,
  });
}
function addBulletList(slide, items, position, opts = {}) {
  const box = addText(slide, "", position, {
    fontSize: opts.fontSize ?? 23, color: opts.color ?? C.ink, insets: opts.insets ?? 0,
  });
  box.text.set(items.map((item) => ({
    bulletCharacter: "•", marginLeft: 24, indent: -14,
    spaceAfter: opts.spaceAfter ?? 10,
    runs: typeof item === "string" ? [item] : item,
  })));
  box.text.style = {
    fontSize: opts.fontSize ?? 23, typeface: BODY, color: opts.color ?? C.ink,
    verticalAlignment: "top", autoFit: "shrinkText", wrap: "square", insets: opts.insets ?? 0,
  };
  return box;
}
function addArrow(slide, x, y, w = 42, h = 28, color = C.blue3) {
  addShape(slide, { left: x, top: y, width: w, height: h }, {
    geometry: "rightArrow", fill: color, line: { style: "solid", fill: color, width: 0 }, borderRadius: 0,
  });
}
function addFooter(slide, text) {
  addText(slide, text, { left: 42, top: 691, width: 1120, height: 20 }, {
    fontSize: 11, color: C.mid, insets: 0, verticalAlignment: "middle",
  });
}
function setNotes(slide, notes) {
  slide.speakerNotes.text = notes;
}
function styleTable(table, { header = C.blue, headerText = C.white, fontSize = 17, rows, columns } = {}) {
  table.borders.assign({ style: "solid", fill: C.line, width: 1 });
  table.cells.block({ row: 0, column: 0, rowCount: 1, columnCount: columns }).assign({
    fill: header, textStyle: { bold: true, color: headerText, fontSize, typeface: FONT },
    margins: { left: 7, right: 7, top: 6, bottom: 6 }, anchor: "middle",
  });
  if (rows > 1) {
    table.cells.block({ row: 1, column: 0, rowCount: rows - 1, columnCount: columns }).assign({
      textStyle: { color: C.ink, fontSize, typeface: BODY },
      margins: { left: 7, right: 7, top: 5, bottom: 5 }, anchor: "middle",
    });
  }
  for (let r = 1; r < rows; r += 2) {
    table.cells.block({ row: r, column: 0, rowCount: 1, columnCount: columns }).fill = C.pale;
  }
}

// Cover: preserve the template logo and replace only the text.
for (const shape of cover.shapes.items) {
  if (shape.name === "副标题 8") {
    shape.text = "面向脑控无人机的 SSVEP 在线识别稳定性研究";
    shape.text.style = { fontSize: 46, typeface: FONT, color: "#0070C0", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  } else if (shape.name === "文本占位符 6") {
    shape.text = "汇报人：________";
    shape.text.style = { fontSize: 34, typeface: FONT, color: C.gray, bold: true, alignment: "center", verticalAlignment: "middle" };
  } else if (shape.name === "文本占位符 4") {
    shape.text = "个人校准解码与重叠感知序贯决策 · 2026 年 10 月";
    shape.text.style = { fontSize: 24, typeface: BODY, color: C.gray, bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  }
}
setNotes(cover, "本报告基于现有项目、现场采集记录与本地论文复现情况整理。题目聚焦在线识别稳定性，而不是无人机任务分配。\n本地参考论文：/Users/geniusalu/Desktop/论文.pdf");

// Contents slide.
for (const shape of contents.shapes.items) {
  if (shape.name === "文本框 92") {
    shape.text = "1. 介绍背景\n2. 问题是什么？\n3. 为什么重要？\n4. 挑战与难点\n5. 已有方法（文献综述）\n6. 尚未解决的难点\n7. 拟议创新与主要贡献\n8. 方法细节\n9. 实验验证方案与当前基线\n10. 总结";
    shape.text.style = { fontSize: 23, typeface: FONT, color: C.blue2, bold: true, alignment: "left", verticalAlignment: "top", autoFit: "shrinkText", lineSpacing: 1.05 };
  }
}
setNotes(contents, "目录严格对应老师要求的十部分结构。第 7—9 部分采用‘拟议方法—待验证’表述，避免把尚未完成的实验写成结论。");

const preClearInspect = await presentation.inspect({
  select: "$.slides[*]",
  include: ["id", "index", "elements.id", "elements.kind"],
});
for (const record of preClearInspect.ndjson.trim().split("\n").map((line) => JSON.parse(line))) {
  if (record.index >= 2 && record.index <= 16) {
    const ids = (record.elements ?? []).map((element) => element.id).filter(Boolean);
    if (ids.length) presentation.delete(ids);
  }
}

// 3 — Background.
{
  const s = contentSlides[0]; addTitle(s, "1  研究背景：SSVEP 脑控无人机", "背景");
  s.images.add({ blob: IMG_CAP_BYTES, contentType: "image/png", alt: "OpenBCI 后枕区脑电帽与采集盒", fit: "cover", geometry: "roundRect", borderRadius: "rounded-xl", position: { left: 55, top: 125, width: 390, height: 330 }, crop: { left: 0.05, top: 0.18, right: 0.08, bottom: 0.22 } });
  s.images.add({ blob: IMG_UI_BYTES, contentType: "image/png", alt: "六目标 SSVEP 在线测试界面", fit: "cover", geometry: "roundRect", borderRadius: "rounded-xl", position: { left: 475, top: 125, width: 742, height: 330 } });
  addCard(s, 55, 480, 360, 170, "任务目标", "用户注视某一闪烁目标，系统把对应频率解码为起飞、前进、后退、左移、右移或降落。", { accent: C.blue, fill: C.pale, bodySize: 19 });
  addCard(s, 460, 480, 350, 170, "当前路线", "8–13 Hz 六频刺激；FBCCA、Transformer 与个人模板均已尝试，现阶段默认走个人模板融合。", { accent: C.cyan, fill: C.pale, bodySize: 19 });
  addCard(s, 855, 480, 362, 170, "研究边界", "本研究聚焦“看哪个就识别哪个”的稳定解码，不研究蜂群任务分配或无人机调度。", { accent: C.orange, fill: C.pale, bodySize: 19 });
  addFooter(s, "数据来源：项目实时界面与设备照片（2026-10）；界面截图仅作系统现状展示");
  setNotes(s, "讲解重点：系统已具备硬件接入、刺激呈现、实时采集和多种解码器，但核心问题仍是在线意图识别不稳定。\n图片来源：用户提供的设备照片和项目界面截图。\n本地项目：/Users/geniusalu/Documents/brain-drone-swarm-console；/Users/geniusalu/Desktop/brain-drone-swarm-server");
}

// 4 — Problem and evidence table.
{
  const s = contentSlides[1]; addTitle(s, "2  问题是什么？——“有输出”不等于“识别正确”", "问题");
  addText(s, "核心问题：在自由注视的在线场景中，分类器会稳定地产生错误指令，或在阈值稍严时完全不输出。", { left: 70, top: 108, width: 1135, height: 54 }, { fontSize: 26, bold: true, color: C.red, fill: "#FFF4F4", line: { style: "solid", fill: "#F2B8B5", width: 1 }, verticalAlignment: "middle" });
  const t = s.tables.add({ rows: 5, columns: 4, left: 60, top: 185, width: 1160, height: 355, columnWidths: [200, 280, 300, 380], values: [
    ["环节", "当前证据", "结果", "正确解释"],
    ["FBCCA 在线", "六目标自由注视", "频繁误触发 / 阈值严时拒识", "尚无逐试次标准答案，不能凭屏幕输出计算准确率"],
    ["Transformer", "78 条训练、27 条验证", "验证准确率 7.4%；空闲误触发率 100%", "低于 7 类随机基线 14.3%，当前模型不可用"],
    ["个人模板融合", "60 段单目标数据，每类 10 段", "留一试次一致性 38.3%", "仅是内部一致性，不是独立在线准确率"],
    ["在线判决", "4 s 窗，0.25 s 更新", "相邻窗口重叠 93.75%", "连续判断高度相关，不能当作独立证据"],
  ] });
  styleTable(t, { fontSize: 16, rows: 5, columns: 4 });
  addCard(s, 60, 563, 565, 92, "需要回答的科学问题", "如何在低信噪比、少校准数据和强窗口相关性下，提高在线识别正确率并抑制错误指令？", { fill: C.light, accent: C.blue, bodySize: 18 });
  addCard(s, 655, 563, 565, 92, "当前不能声称的结论", "不能把“模板识别 4 轮”“屏幕有指令”或 38.3% 内部一致性写成真实在线准确率。", { fill: "#FFF4F4", accent: C.red, bodySize: 18 });
  setNotes(s, "数据来自当前项目模型元数据和个人模板报告。Transformer：78 train / 27 validation，validation accuracy=7.4074%，macro-F1=2.857%，idle false activation=100%。个人模板：60 trials，LOO consistency=38.33%。\n强调：这里把‘问题’从主观感受转换为可检验指标。随机基线：7 类含 idle 时约 14.3%；6 指令类约 16.7%。");
}

// 5 — Importance.
{
  const s = contentSlides[2]; addTitle(s, "3  为什么这个问题很重要？", "重要性");
  const labels = [
    ["频率误判", "看前进，却识别为起飞"],
    ["意图误译", "用户意图与指令不一致"],
    ["控制误触发", "错误动作被连续发送"],
    ["系统风险", "安全性、可用性与信任下降"],
  ];
  labels.forEach(([a, b], i) => {
    const x = 60 + i * 300;
    addShape(s, { left: x, top: 170, width: 245, height: 155 }, { fill: i === 3 ? "#FFF4F4" : C.pale, lineColor: i === 3 ? C.red : C.blue3 });
    addText(s, String(i + 1).padStart(2, "0"), { left: x + 18, top: 186, width: 50, height: 40 }, { fontSize: 28, bold: true, color: i === 3 ? C.red : C.blue2, insets: 0 });
    addText(s, a, { left: x + 72, top: 187, width: 155, height: 38 }, { fontSize: 24, bold: true, color: C.blue, insets: 0 });
    addText(s, b, { left: x + 20, top: 240, width: 205, height: 65 }, { fontSize: 18, color: C.gray, alignment: "center", insets: 0, verticalAlignment: "middle" });
    if (i < 3) addArrow(s, x + 254, 235, 34, 24);
  });
  addText(s, "因此，评价指标不能只有离线分类准确率", { left: 70, top: 375, width: 1135, height: 48 }, { fontSize: 28, bold: true, color: C.blue, alignment: "center", insets: 0 });
  const metrics = [["ACC / Macro-F1", "识别是否正确"], ["FTR/min", "每分钟错误触发"], ["Latency", "决策等待时间"], ["ITR", "速度—准确率权衡"]];
  metrics.forEach(([a, b], i) => addCard(s, 65 + i * 297, 455, 260, 150, a, b, { fill: i === 1 ? "#FFF4F4" : C.light, accent: i === 1 ? C.red : C.cyan, titleSize: 22, bodySize: 20 }));
  addFooter(s, "在线脑控应同时报告正确性、误触发与时延；单独追求输出频率会掩盖安全问题");
  setNotes(s, "这一页把识别错误与无人机控制后果连起来。后续实验以 cue-guided ground truth 为准，分别报告命令正确率、空闲状态错误触发率、每分钟错误指令数和决策时延。");
}

// 6 — Challenges with channel chart.
{
  const s = contentSlides[3]; addTitle(s, "4  挑战与难点：信号、刺激、数据、决策相互耦合", "挑战");
  const chart = s.charts.add("bar", {
    position: { left: 55, top: 135, width: 670, height: 440 },
    title: "60 段个人校准数据中的通道可用率（%）",
    titleTextStyle: { fontSize: 18, bold: true, color: C.blue },
    categories: ["O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4"],
    series: [{ name: "可用率", values: [6.7, 100, 53.3, 85, 50, 98.3, 71.7, 25], fill: C.blue2 }],
    hasLegend: false,
    barOptions: { direction: "bar", grouping: "clustered", gapWidth: 55 },
    xAxis: { minimumScale: 0, maximumScale: 100, majorUnit: 20, title: "可用率（%）" },
    yAxis: { majorGridlines: { style: "solid", fill: "#E4E8F0", width: 1 } },
    dataLabels: { showValue: true, position: "outEnd", textStyle: { fontSize: 12, color: C.gray } },
    chartFill: C.white, chartLine: { style: "solid", fill: C.line, width: 1 }, plotAreaFill: C.white,
  });
  applyPresentationChartFont(chart, { fontFamily: FONT });
  addCard(s, 760, 135, 455, 112, "① 信号质量", "厚头发、干电极接触、REF/GND 与工频干扰会使有效通道数量波动。", { accent: C.red, fill: "#FFF7F6", bodySize: 18 });
  addCard(s, 760, 262, 455, 112, "② 刺激一致性", "单目标校准与六目标同时闪烁的视觉竞争、位置和亮度并不等价。", { accent: C.orange, fill: "#FFF9F1", bodySize: 18 });
  addCard(s, 760, 389, 455, 112, "③ 小样本与漂移", "跨时段、跨佩戴的幅值和相位会改变，单日模板容易过拟合。", { accent: C.blue, fill: C.light, bodySize: 18 });
  addCard(s, 760, 516, 455, 112, "④ 在线证据相关", "4 s 窗每 0.25 s 更新，相邻输出主要来自同一批样本。", { accent: C.cyan, fill: "#F1FBFB", bodySize: 18 });
  addFooter(s, "注：通道可用率是本批 60 段校准数据的质量统计，不代表生理响应强度");
  setNotes(s, "图表来自个人模板训练数据的通道质量统计：O1 6.7, Oz 100, O2 53.3, PO3 85, POz 50, PO4 98.3, P3 71.7, P4 25。当前个人模型使用 Oz/PO3/PO4/P3，其中 P3 是手动保留，低于原 80% 可用率门槛。\n不要把‘增益放大’等同于信噪比提高：放大会同时放大噪声，关键是接触质量、参考、滤波和可重复性。");
}

// 7 — Literature methods table.
{
  const s = contentSlides[4]; addTitle(s, "5.1  已有方法：从无训练到个人校准", "文献综述");
  const t = s.tables.add({ rows: 6, columns: 5, left: 50, top: 120, width: 1180, height: 465, columnWidths: [150, 220, 235, 265, 310], values: [
    ["方法", "核心信息", "校准需求", "主要优点", "主要局限"],
    ["CCA", "正弦/余弦参考相关性", "无", "实现简单、可解释", "忽略个体相位与空间结构"],
    ["FBCCA", "多子带与谐波加权", "无", "利用谐波，提高稳健性", "仍依赖理想参考；弱响应时易混淆"],
    ["训练数据 CCA", "由个人试次学习参考", "少量", "更贴合个体响应", "对跨时段漂移敏感"],
    ["eTRCA", "最大化跨试次可重复成分", "每类多试次", "个人模板性能强、解释清楚", "需要高质量重复校准"],
    ["TDCA", "判别性时空滤波 + 延迟/参考投影", "每类多试次", "短窗和类别分离能力较好", "参数与校准协议仍需严格验证"],
  ] });
  styleTable(t, { fontSize: 16, rows: 6, columns: 5 });
  addText(s, "结论：‘个人模板 + FBCCA’属于已有技术组合，本身不足以构成论文创新。", { left: 85, top: 610, width: 1110, height: 48 }, { fontSize: 25, bold: true, color: C.red, alignment: "center", fill: "#FFF4F4", line: { style: "solid", fill: "#F2B8B5", width: 1 }, verticalAlignment: "middle" });
  setNotes(s, "主要文献：\nChen et al., Filter bank canonical correlation analysis for implementing a high-speed SSVEP-based brain-computer interface, J Neural Eng, 2015. https://pubmed.ncbi.nlm.nih.gov/26035476/\nNakanishi et al., Enhancing Detection of SSVEPs for a High-Speed BCI Using TRCA, IEEE TBME, 2018. https://pubmed.ncbi.nlm.nih.gov/28436836/\nWei et al., Training-data-driven CCA, Int J Neural Syst, 2020. https://pubmed.ncbi.nlm.nih.gov/32380925/\nLiu et al., TDCA, IEEE TNSRE, 2021. https://bingchuanliu.github.io/assets/pdf/liu_tnsre.pdf");
}

// 8 — Deep and adaptive methods.
{
  const s = contentSlides[5]; addTitle(s, "5.2  已有方法：深度学习与自适应通道", "文献综述");
  addCard(s, 60, 135, 350, 430, "Transformer / 深度网络", "能从时空序列中自动学习特征。\n\n适合：大样本、多被试预训练、严格数据划分。\n\n本项目问题：只有数百条且信号质量不稳定，当前验证准确率 7.4%，网络学不到稳定的类别结构。", { accent: C.blue, fill: C.light, bodySize: 20 });
  addCard(s, 465, 135, 350, 430, "通道选择与加权", "依据 SNR、工频污染、跨试次可重复性选择或加权通道。\n\n价值：避免 O1/P4 等低质量通道拖累融合。\n\n注意：权重必须只用训练折估计，不能偷看测试标签。", { accent: C.cyan, fill: "#F1FBFB", bodySize: 20 });
  addCard(s, 870, 135, 350, 430, "跨被试 / 迁移学习", "利用其他被试或历史会话减少个人校准。\n\n价值：缓解小样本。\n\n限制：设备、电极位置和刺激时序不一致时，域偏移可能比收益更大。", { accent: C.orange, fill: "#FFF9F1", bodySize: 20 });
  addText(s, "当前优先级：先把信号与评测协议做可靠，再讨论 Transformer；网络结构不能替代可用数据。", { left: 80, top: 595, width: 1120, height: 56 }, { fontSize: 25, color: C.blue, bold: true, alignment: "center", fill: C.pale, line: { style: "solid", fill: C.line, width: 1 }, verticalAlignment: "middle" });
  setNotes(s, "当前 Transformer 失败不能推导出‘Transformer 不适合 SSVEP’，只能说明当前数据量、信号质量、标签/频率配置和验证流程不足以支撑该模型。\n近期自适应通道例：Adaptive subject-specific channel and subspace selection TRCA. https://pmc.ncbi.nlm.nih.gov/articles/PMC12944370/");
}

// 9 — Online windows and stopping.
{
  const s = contentSlides[6]; addTitle(s, "5.3  已有方法：在线滑动窗与停止策略", "文献综述");
  addText(s, "同一段脑电被重复使用", { left: 70, top: 108, width: 1140, height: 38 }, { fontSize: 23, bold: true, color: C.blue, alignment: "center", insets: 0 });
  addShape(s, { left: 100, top: 170, width: 720, height: 56 }, { geometry: "rect", fill: "#D6E3FF", lineColor: C.blue2 });
  addText(s, "窗口 t：4.00 s 数据", { left: 100, top: 170, width: 720, height: 56 }, { fontSize: 21, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", insets: 0 });
  addShape(s, { left: 145, top: 245, width: 720, height: 56 }, { geometry: "rect", fill: "#BFE9E9", lineColor: C.cyan });
  addText(s, "窗口 t+1：仅新增 0.25 s，93.75% 与上窗相同", { left: 145, top: 245, width: 720, height: 56 }, { fontSize: 21, bold: true, color: "#126A6A", alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "新增证据", { left: 805, top: 310, width: 120, height: 32 }, { fontSize: 17, bold: true, color: C.red, alignment: "center", insets: 0 });
  addShape(s, { left: 820, top: 225, width: 45, height: 76 }, { geometry: "rect", fill: "#FFB3AE", lineColor: C.red });
  addCard(s, 910, 160, 300, 155, "论文复现设置", "2 s 窗 / 0.25 s 步长\n重叠 87.5%\n连续 2 次 + 0.6 置信度", { accent: C.blue, fill: C.light, bodySize: 19 });
  addCard(s, 65, 370, 350, 230, "已有：固定连续确认", "要求连续若干窗口同类。实现简单，但重叠窗口并非独立，错误结果也可能连续出现。", { accent: C.blue3, bodySize: 20 });
  addCard(s, 465, 370, 350, 230, "已有：自适应窗 / 动态停止", "根据信心提前停止或延长窗口，已有 FBCCA 自适应窗和校准高效动态停止研究。", { accent: C.cyan, bodySize: 20 });
  addCard(s, 865, 370, 350, 230, "仍需解决", "怎样估计每次更新真正新增的信息，并让空闲/释放状态参与决策，而不是无限累积重复证据？", { accent: C.red, fill: "#FFF4F4", bodySize: 20 });
  addFooter(s, "重叠本身不是错误；问题在于把强相关窗口当成相互独立的重复确认");
  setNotes(s, "本地复现论文：2 s 窗、0.25 s 步长，87.5% 重叠，连续 2 次、置信度 0.6、冷却 500 ms。论文未对窗口独立性和不同重叠率做消融，因此这里称为‘潜在方法学局限’，不能称为论文错误。\n一般滑动窗独立性讨论：https://pmc.ncbi.nlm.nih.gov/articles/PMC3634773/\nAdaptive Window FBCCA: https://ieeexplore.ieee.org/ielx7/7333/10031624/09931747.pdf\nCalibration-efficient dynamic stopping/control-state UAV (2026): https://ieeexplore.ieee.org/abstract/document/11693034/");
}

// 10 — Open gaps.
{
  const s = contentSlides[7]; addTitle(s, "6  还有哪些未解决的难点？", "研究空白");
  const t = s.tables.add({ rows: 6, columns: 4, left: 58, top: 125, width: 1165, height: 440, columnWidths: [235, 300, 315, 315], values: [
    ["未解决难点", "已有方法覆盖", "本项目缺口", "可验证研究问题"],
    ["通道质量动态变化", "固定通道 / 离线选择", "佩戴后可用通道每次不同", "质量加权能否降低误触发？"],
    ["单目标—多目标域差异", "模板方法通常假设协议一致", "校准与正式界面视觉竞争不同", "相同几何的引导校准是否更稳？"],
    ["高度重叠的在线证据", "连续确认 / 动态停止", "当前把强相关窗口重复计数", "按新增信息量修正能否改善稳定性？"],
    ["空闲与释放控制", "阈值拒识", "缺少独立 idle 类和释放逻辑", "控制状态机能否降 FTR/min？"],
    ["真实在线评估", "离线交叉验证较常见", "缺少逐试次提示与标准答案", "跨会话在线实验能否复现收益？"],
  ] });
  styleTable(t, { fontSize: 16, rows: 6, columns: 4 });
  addText(s, "研究空白应落到可证伪的问题，而不是“再换一个更复杂的网络”。", { left: 100, top: 600, width: 1080, height: 52 }, { fontSize: 26, bold: true, color: C.blue, alignment: "center", fill: C.light, line: { style: "solid", fill: C.blue3, width: 1 }, verticalAlignment: "middle" });
  setNotes(s, "这一页可作为老师判断‘是否已有方法处理这些问题’的汇总。重点不是声称没人研究，而是说明已有模块分别存在、但在当前硬件与在线控制约束下仍缺少联合验证。后续需要继续做系统性查新，特别关注质量估计、动态停止和 SSVEP control-state 文献。");
}

// 11 — Proposed contributions.
{
  const s = contentSlides[8]; addTitle(s, "7  拟议创新点与主要贡献", "创新候选");
  addText(s, "候选题目：质量感知、重叠感知的个人校准 SSVEP 在线解码框架", { left: 65, top: 110, width: 1150, height: 50 }, { fontSize: 27, bold: true, color: C.blue, alignment: "center", fill: C.light, line: { style: "solid", fill: C.blue3, width: 1 }, verticalAlignment: "middle" });
  addCard(s, 60, 190, 555, 165, "贡献 1｜一致校准协议", "单目标引导时保留与六目标测试相同的尺寸、位置与背景，只让目标位置闪烁，减少域差异。", { accent: C.blue, bodySize: 20 });
  addCard(s, 665, 190, 555, 165, "贡献 2｜质量感知个人解码", "用通道 SNR、工频污染与跨试次可重复性生成权重，融合 TDCA/eTRCA 与 FBCCA。", { accent: C.cyan, bodySize: 20 });
  addCard(s, 60, 385, 555, 165, "贡献 3｜重叠感知序贯证据", "每 0.25 s 更新时只按新增信息比例累积证据，避免把 93.75% 重叠窗口当作独立投票。", { accent: C.orange, bodySize: 20 });
  addCard(s, 665, 385, 555, 165, "贡献 4｜显式 idle / 释放状态", "把“不发指令”和“释放旧指令”纳入状态机，以 FTR/min 与延迟共同优化控制安全性。", { accent: C.red, fill: "#FFF7F6", bodySize: 20 });
  addText(s, "严谨表述：这是拟议框架与创新候选；需完成查新、消融和跨会话在线实验后，才能声称有效或新颖。", { left: 80, top: 590, width: 1120, height: 62 }, { fontSize: 22, bold: true, color: C.red, alignment: "center", fill: "#FFF4F4", line: { style: "solid", fill: "#F2B8B5", width: 1 }, verticalAlignment: "middle" });
  setNotes(s, "‘个人模板 + FBCCA’不是创新；TRCA/TDCA、通道选择、自适应窗和动态停止都有文献。可能形成论文贡献的是：在同一在线问题中，给出质量估计、重叠证据校正和控制状态的一体化方法，并用严格的在线指标与消融证明每一部分的必要性。这里暂不声称世界首创。");
}

// 12 — Overall method.
{
  const s = contentSlides[9]; addTitle(s, "8.1  提出方法的总体结构", "方法");
  const stages = [
    ["① 一致校准", "相同布局\n单目标引导"], ["② 质量评估", "SNR / 50 Hz\n可重复性"],
    ["③ 个人解码", "TDCA / eTRCA\n模板分数"], ["④ 无训练证据", "FBCCA\n频率谐波"],
    ["⑤ 序贯融合", "重叠校正\n动态停止"], ["⑥ 控制状态", "idle / 释放\n安全触发"],
  ];
  stages.forEach(([a, b], i) => {
    const x = 45 + i * 203;
    addShape(s, { left: x, top: 190, width: 165, height: 180 }, { fill: i === 5 ? "#FFF4F4" : (i % 2 ? "#F1FBFB" : C.light), lineColor: i === 5 ? C.red : C.blue3 });
    addText(s, a, { left: x + 10, top: 207, width: 145, height: 44 }, { fontSize: 21, bold: true, color: i === 5 ? C.red : C.blue, alignment: "center", insets: 0, verticalAlignment: "middle" });
    addText(s, b, { left: x + 12, top: 270, width: 141, height: 70 }, { fontSize: 18, color: C.gray, alignment: "center", insets: 0, verticalAlignment: "middle" });
    if (i < 5) addArrow(s, x + 169, 265, 28, 24, C.blue3);
  });
  addText(s, "训练阶段", { left: 45, top: 145, width: 565, height: 34 }, { fontSize: 18, bold: true, color: C.blue2, alignment: "center", fill: "#EDF3FF", verticalAlignment: "middle" });
  addText(s, "在线阶段", { left: 654, top: 145, width: 609, height: 34 }, { fontSize: 18, bold: true, color: "#126A6A", alignment: "center", fill: "#E7F8F8", verticalAlignment: "middle" });
  addCard(s, 75, 440, 350, 155, "输入", "后枕区多通道 EEG\n刺激频率 8–13 Hz\n4 s 主窗 / 0.25 s 更新", { accent: C.blue, fill: C.pale, bodySize: 19 });
  addCard(s, 465, 440, 350, 155, "输出", "六类指令概率 + idle 概率\n置信度、间隔与有效证据量", { accent: C.cyan, fill: C.pale, bodySize: 19 });
  addCard(s, 855, 440, 350, 155, "优化目标", "最大化在线正确率与 ITR\n最小化 FTR/min 与响应延迟", { accent: C.red, fill: "#FFF7F6", bodySize: 19 });
  addFooter(s, "所有权重和阈值只在训练折 / 校准集确定；测试会话只做一次前向推理");
  setNotes(s, "总体结构是研究假设，不是已完成算法。训练与测试必须按会话分开，避免同一连续记录切出的相邻窗口落入训练和验证，造成数据泄漏。");
}

// 13 — Quality-aware decoding detail.
{
  const s = contentSlides[10]; addTitle(s, "8.2  质量感知的个人解码", "方法细节");
  addCard(s, 55, 125, 360, 470, "A. 通道质量权重", "对每个通道 c 估计：\n\n• 目标频段 SNR\n• 50 Hz 污染比例\n• 重复试次相关性\n• 幅值饱和 / 漂移\n\n得到 0–1 权重 q_c，并对低质量通道降权，而不是固定选四个通道。", { accent: C.blue, fill: C.light, bodySize: 21 });
  addCard(s, 460, 125, 360, 470, "B. 两类互补证据", "TDCA / eTRCA：利用个人重复试次的时空模板，提高个体判别性。\n\nFBCCA：利用频率与谐波的物理先验，在模板漂移或校准不足时提供保底证据。", { accent: C.cyan, fill: "#F1FBFB", bodySize: 21 });
  addCard(s, 865, 125, 360, 470, "C. 训练内确定融合", "X′_c = q_c · X_c\n\np_t = α_t p_TDCA\n      + (1−α_t) p_FBCCA\n\nα_t 由训练折的质量特征估计；阈值只在训练/校准数据上选择。", { accent: C.orange, fill: "#FFF9F1", bodySize: 21 });
  addText(s, "关键对比：固定通道 vs 质量加权；FBCCA vs TDCA；固定 α vs 自适应 α。", { left: 85, top: 620, width: 1110, height: 45 }, { fontSize: 22, bold: true, color: C.blue, alignment: "center", insets: 0 });
  setNotes(s, "公式表达的是拟议方案。质量特征应在每次佩戴后用无标签或校准数据估计，并通过训练折确定到概率映射。可从 eTRCA/TDCA 开始，因为它们比 Transformer 更适合当前的小样本个人校准场景。TDCA 参考：https://bingchuanliu.github.io/assets/pdf/liu_tnsre.pdf；TRCA 参考：https://pubmed.ncbi.nlm.nih.gov/28436836/");
}

// 14 — Overlap-aware sequential detail.
{
  const s = contentSlides[11]; addTitle(s, "8.3  重叠感知的序贯判决", "方法细节");
  addCard(s, 55, 125, 360, 215, "重叠量", "窗口长度 L，更新步长 S\n重叠率 r = 1 − S/L\n新增信息比例 Δn = S/L\n\n论文：Δn = 0.125\n当前：Δn = 0.0625", { accent: C.blue, fill: C.light, bodySize: 22 });
  addCard(s, 460, 125, 360, 215, "初版证据累积", "E_t = E_{t−1} + Δn · log[(p₁+ε)/(p₂+ε)]\n\n只按新增样本比例累积分数，避免相邻窗口被完整重复计票。", { accent: C.cyan, fill: "#F1FBFB", bodySize: 21 });
  addCard(s, 865, 125, 360, 215, "触发 / 释放", "触发：E_t > θ_on 且质量合格\n保持：同类证据持续\n释放：idle > θ_off 或质量失效\n冷却：防止短时重复发送", { accent: C.red, fill: "#FFF7F6", bodySize: 20 });
  const steps = [["1", "读取本窗概率与质量"], ["2", "估计有效新增证据"], ["3", "更新类证据与 idle"], ["4", "达到阈值才触发"], ["5", "记录延迟和误触发"]];
  steps.forEach(([n, txt], i) => {
    const x = 65 + i * 238;
    addShape(s, { left: x, top: 410, width: 198, height: 105 }, { fill: i === 4 ? "#FFF4F4" : C.pale, lineColor: i === 4 ? C.red : C.blue3 });
    addText(s, n, { left: x + 10, top: 426, width: 42, height: 42 }, { fontSize: 28, bold: true, color: i === 4 ? C.red : C.blue2, alignment: "center", verticalAlignment: "middle", insets: 0 });
    addText(s, txt, { left: x + 55, top: 423, width: 132, height: 58 }, { fontSize: 17, bold: true, color: C.gray, alignment: "center", verticalAlignment: "middle", insets: 0 });
    if (i < 4) addArrow(s, x + 201, 450, 28, 20, C.blue3);
  });
  addText(s, "该公式只是可实现的第一版假设；后续可用相关性估计、贝叶斯滤波或 HMM 替代，需要消融实验决定。", { left: 85, top: 570, width: 1110, height: 65 }, { fontSize: 22, bold: true, color: C.red, alignment: "center", fill: "#FFF4F4", line: { style: "solid", fill: "#F2B8B5", width: 1 }, verticalAlignment: "middle" });
  setNotes(s, "这里不要宣称公式已经证明。它把‘4 秒窗口每 0.25 秒更新’的直观问题显式化：每次更新只有 6.25% 新样本。初版可以作为 baseline，再与普通 consecutive voting、动态窗口、HMM/贝叶斯证据累积比较。一般重叠窗口独立性讨论：https://pmc.ncbi.nlm.nih.gov/articles/PMC3634773/");
}

// 15 — Experiment plan.
{
  const s = contentSlides[12]; addTitle(s, "9.1  实验设计：如何验证方法有效？", "实验");
  const t = s.tables.add({ rows: 7, columns: 4, left: 55, top: 120, width: 1170, height: 460, columnWidths: [180, 310, 350, 330], values: [
    ["维度", "设计", "对照 / 消融", "主要输出"],
    ["数据划分", "按会话分组，禁止相邻窗跨集合", "随机切窗划分作为泄漏警示", "跨会话 ACC、Macro-F1"],
    ["离线基线", "FBCCA、eTRCA、TDCA、当前个人融合、Transformer", "同通道、同窗口、同频率", "类别混淆矩阵、校准曲线"],
    ["在线任务", "提示目标 → 注视 → 空闲；每类随机多轮", "普通连续确认 vs 动态停止", "指令正确率、FTR/min、Latency"],
    ["质量消融", "去掉 q_c / 固定四通道", "质量加权完整模型", "坏通道条件下性能下降"],
    ["重叠消融", "不修正 / Δn 修正 / 相关性估计", "2 s 与 4 s，多种步长", "稳定性、误触发、ITR"],
    ["安全验证", "先模拟器，后低风险实机", "启用/禁用 idle 释放", "错误动作数、停止成功率"],
  ] });
  styleTable(t, { fontSize: 15, rows: 7, columns: 4 });
  addText(s, "统计要求：报告均值±置信区间；按试次或会话做配对比较；预先固定阈值与停止规则。", { left: 80, top: 608, width: 1120, height: 46 }, { fontSize: 22, bold: true, color: C.blue, alignment: "center", fill: C.light, line: { style: "solid", fill: C.blue3, width: 1 }, verticalAlignment: "middle" });
  setNotes(s, "实验建议分三阶段：1）跨会话离线；2）有提示的在线闭环但不控制真实无人机；3）安全约束下实机。第一阶段必须先把标准答案和数据划分做对。避免同一连续 EEG 切出的相邻窗口同时进入训练与验证，这会因高重叠造成严重泄漏。");
}

// 16 — Current baseline and status.
{
  const s = contentSlides[13]; addTitle(s, "9.2  当前基线与验证状态", "实验现状");
  const chart = s.charts.add("bar", {
    position: { left: 60, top: 145, width: 520, height: 360 },
    title: "相邻在线窗口的样本重叠率",
    titleTextStyle: { fontSize: 19, bold: true, color: C.blue },
    categories: ["论文设置\n2 s / 0.25 s", "当前设置\n4 s / 0.25 s"],
    series: [{ name: "重叠率", values: [87.5, 93.75], fill: C.orange, points: [{ idx: 0, fill: C.blue2 }, { idx: 1, fill: C.red }] }],
    hasLegend: false,
    barOptions: { direction: "column", grouping: "clustered", gapWidth: 85 },
    yAxis: { minimumScale: 0, maximumScale: 100, majorUnit: 20, title: "重叠率（%）", majorGridlines: { style: "solid", fill: "#E4E8F0", width: 1 } },
    dataLabels: { showValue: true, position: "outEnd", textStyle: { fontSize: 14, color: C.gray } },
    chartFill: C.white, chartLine: { style: "solid", fill: C.line, width: 1 }, plotAreaFill: C.white,
  });
  applyPresentationChartFont(chart, { fontFamily: FONT });
  addCard(s, 630, 135, 575, 220, "已完成", "✓ 八通道接入与实时显示\n✓ 六频刺激与 FBCCA 流程\n✓ 60 段单目标个人校准数据\n✓ Transformer 基线与个人模板基线\n✓ 已记录通道质量和窗口配置", { accent: C.green, fill: "#F2FBF7", bodySize: 20, titleColor: C.green });
  addCard(s, 630, 385, 575, 220, "尚未完成（决定论文可信度）", "□ 独立的有提示在线测试集\n□ 跨会话复测\n□ eTRCA / TDCA 强基线\n□ FTR/min、Latency、ITR\n□ 质量与重叠模块消融\n□ 刺激实际频率的光电验证", { accent: C.red, fill: "#FFF7F6", bodySize: 20, titleColor: C.red });
  addText(s, "当前证据只能支持“发现了问题并建立了基线”，不能支持“新方法已有效”。", { left: 90, top: 625, width: 1100, height: 42 }, { fontSize: 22, bold: true, color: C.red, alignment: "center", insets: 0 });
  setNotes(s, "论文设置来自本地论文 /Users/geniusalu/Desktop/论文.pdf：2 s window, 0.25 s stride, 87.5% overlap。当前项目为 4 s / 0.25 s，93.75%。当前仅有软件时序日志，刺激频率尚未用光电二极管或高速相机做物理验证。个人模板 38.3% 是留一试次内部一致性，不放到‘在线准确率’栏。");
}

// 17 — Summary.
{
  const s = contentSlides[14]; addTitle(s, "10  总结与下一步", "总结");
  addCard(s, 65, 135, 350, 410, "已经明确的问题", "1. 低信号质量与通道波动\n\n2. 单目标校准与六目标测试不一致\n\n3. 高度重叠窗口使连续确认相关\n\n4. 缺少 idle/释放与在线标准答案", { accent: C.red, fill: "#FFF7F6", bodySize: 22 });
  addCard(s, 465, 135, 350, 410, "拟议解决方案", "1. 一致布局的个人校准\n\n2. 质量感知 TDCA/eTRCA + FBCCA\n\n3. 重叠感知序贯证据\n\n4. 显式控制状态与多指标优化", { accent: C.blue, fill: C.light, bodySize: 22 });
  addCard(s, 865, 135, 350, 410, "下一步最小闭环", "1. 实现 eTRCA/TDCA 强基线\n\n2. 采集独立、随机、有提示在线测试\n\n3. 做跨会话与消融\n\n4. 根据结果收缩创新点并写论文", { accent: C.green, fill: "#F2FBF7", bodySize: 22 });
  addText(s, "建议向老师提出：请重点指导“质量估计 + 重叠感知在线决策”是否具有足够研究价值，以及优先选 TDCA 还是 eTRCA 作为主体算法。", { left: 85, top: 585, width: 1110, height: 75 }, { fontSize: 23, bold: true, color: C.blue, alignment: "center", fill: C.pale, line: { style: "solid", fill: C.blue3, width: 1 }, verticalAlignment: "middle" });
  setNotes(s, "汇报收口：现在已有系统与真实失败证据，可以向老师说明为什么不是简单调阈值或换 Transformer。最值得老师拍板的是：①研究问题是否收敛到在线稳定性；②主体解码器选 TDCA 还是 eTRCA；③重叠感知和控制状态是否作为论文主要创新；④实验样本量与安全流程。");
}

// Thank-you slide: keep template styling, update subtitle only.
for (const shape of thanks.shapes.items) {
  if (shape.name === "文本框 1") {
    shape.text = "感 谢 各 位 专 家\n请批评指正";
    shape.text.style = { fontSize: 72, typeface: FONT, color: "#FF0000", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  }
}
setNotes(thanks, "建议现场重点请教：主体算法选择、创新边界、实验规模和在线安全评价指标。");

const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(CANDIDATE);

const layoutArgs = [
  "--cover-role", "cover",
  "--cover-word-limit", "50",
  "--expected-aspect", "16:9",
  "--expected-slide-size-emu", "12192000,6858000",
  "--require-native-table-slide", "4",
  "--require-native-table-slide", "7",
  "--require-native-table-slide", "10",
  "--require-native-table-slide", "15",
  "--max-content-prose-words", "190",
  "--approved-dense-slide", "4",
  "--approved-dense-slide", "7",
  "--approved-dense-slide", "10",
  "--approved-dense-slide", "15",
];

const result = await finalizePresentation({
  workspaceDir: ROOT,
  candidatePath: CANDIDATE,
  finalPath: FINAL,
  pythonExecutable: "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3",
  integrityValidatorPath: path.join(skillRoot, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(skillRoot, "container_tools/inspect_presentation_layout_geometry.py"),
  explicitTotalSlideCount: 18,
  layoutArgs,
  requiredNativeTableOwnerSlides: [4, 7, 10, 15],
  requiredNativeChartOwnerSlides: [6, 16],
  nativeChartTargetApplication: "powerpoint",
  approvedSourceFigureExceptionSlides: [],
  materializeLiteralChartWorkbooks: true,
  verifyArtifactToolImport: true,
  fontPolicy: {
    basis: "reference",
    families: ["方正大黑简体", "等线", "黑体", "微软雅黑", "Arial"],
    referencePath: TEMPLATE,
    referenceSha256: "987f0c4a1eb7cbfd9da023ff9975b42a8504b6a4fa6a603c7e7034532195a5da",
    scriptFonts: { ea: "微软雅黑" },
  },
});

const finalPresentation = await PresentationFile.importPptx(await FileBlob.load(FINAL));
for (const [index, slide] of finalPresentation.slides.items.entries()) {
  const out = path.join(RENDER, `slide-${String(index + 1).padStart(2, "0")}.png`);
  const png = await finalPresentation.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(out, new Uint8Array(await png.arrayBuffer()));
  const layout = await slide.export({ format: "layout", detail: "full" });
  await fs.writeFile(path.join(RENDER, `slide-${String(index + 1).padStart(2, "0")}.layout.json`), await layout.text());
}
const montage = await finalPresentation.export({ format: "webp", montage: true, scale: 1 });
await fs.writeFile(path.join(RENDER, "montage.webp"), new Uint8Array(await montage.arrayBuffer()));
const inspect = await finalPresentation.inspect({
  select: "$.slides[*]",
  include: ["id", "index", "title", "elements.id", "elements.kind", "elements.name", "elements.text", "elements.position", "notes.text"],
});
await fs.writeFile(path.join(WORK, "final-content.ndjson"), inspect.ndjson);
await fs.writeFile(path.join(WORK, "finalizer-result.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ final: FINAL, slides: finalPresentation.slides.items.length, render: RENDER, result }, null, 2));
