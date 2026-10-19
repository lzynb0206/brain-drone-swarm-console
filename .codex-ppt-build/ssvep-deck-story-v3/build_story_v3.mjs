import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

process.on("uncaughtException", (error) => {
  console.error(`BUILD_ERROR: ${error?.message ?? error}`);
  console.error(error?.stack ?? "");
  process.exit(1);
});
process.on("unhandledRejection", (error) => {
  console.error(`BUILD_REJECTION: ${error?.message ?? error}`);
  console.error(error?.stack ?? "");
  process.exit(1);
});

const ROOT = "/Users/geniusalu/Documents/brain-drone-swarm-console";
const SOURCE = path.join(ROOT, "output/pptx/SSVEP在线识别稳定性研究_图示重构版_20261019.pptx");
const TEMPLATE = "/Users/geniusalu/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_f2dv19yyfpih22_853a/temp/drag/严颖PPT模板 - 副本.pptx";
const WORK = path.join(ROOT, ".codex-ppt-build/ssvep-deck-story-v3");
const CANDIDATE = path.join(WORK, ".finalizer/candidate-story-v3.pptx");
const FINAL = path.join(ROOT, "output/pptx/SSVEP在线识别低准确率_实验历程详细版_20261019.pptx");
const RECEIPT = path.join(WORK, ".finalizer/SSVEP在线识别低准确率_实验历程详细版_20261019.validation.json");
const RENDER = path.join(WORK, "rendered");
const SKILL = "/Users/geniusalu/.codex/plugins/cache/openai-primary-runtime/presentations/26.1015.12040/skills/presentations";
const MODULES = "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules";
process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES = MODULES;

const { FileBlob, PresentationFile } = await import(pathToFileURL(path.join(MODULES, "@oai/artifact-tool/dist/artifact_tool.mjs")).href);
const { finalizePresentation } = await import(pathToFileURL(path.join(SKILL, "container_tools/artifact_tool_utils.mjs")).href);

const OLD_WORK = path.join(ROOT, ".codex-ppt-build/ssvep-deck-rev2");
const P = {
  hardware: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-a8c59fa3-7916-48d9-95fe-35be9b6a750a.png",
  lslLive: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-95a086a7-1c2f-48e5-8b6e-01593f3093dc.png",
  qualityLive: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-7fc6738c-4054-42d8-ba1b-f12dcbd6bd96.png",
  acquisitionLive: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-7b8d8fa2-ef8c-4560-b9e5-825a440eb399.png",
  oldFreqTest: "/var/folders/k6/qh3cpy2d0d37ypt56fnk4sdm0000gn/T/codex-clipboard-f6eb3940-a802-483b-b761-522b330983a9.png",
  onlineDrive: path.join(OLD_WORK, "screens/ssvep-online-drive.png"),
  commandTest: path.join(OLD_WORK, "screens/ssvep-command-test.png"),
  isolated: path.join(OLD_WORK, "screens/single-target-isolated.png"),
  guided: path.join(OLD_WORK, "screens/single-target-guided.png"),
  singlePage: path.join(OLD_WORK, "screens/research-single-target.png"),
  algorithm: path.join(OLD_WORK, "screens/research-algorithm.png"),
  experiment: path.join(OLD_WORK, "screens/research-experiment.png"),
  paradigm: path.join(OLD_WORK, "screens/research-paradigm.png"),
  mapping: path.join(OLD_WORK, "screens/research-mapping.png"),
};
const bytes = Object.fromEntries(await Promise.all(Object.entries(P).map(async ([key, value]) => [key, new Uint8Array(await fs.readFile(value))])));

const C = {
  blue: "#1F2096", blue2: "#1D41D5", indigo: "#5D69AE", cyan: "#00AFC6",
  red: "#C00000", orange: "#E67E22", green: "#178A5B", ink: "#202124",
  gray: "#5B6472", muted: "#8A93A3", line: "#D6DDED", pale: "#F4F7FD",
  bluePale: "#EAF0FF", cyanPale: "#E9FAFC", redPale: "#FFF2F2", orangePale: "#FFF7EC",
  greenPale: "#EFF9F3", white: "#FFFFFF",
};
const FONT = "微软雅黑";
const BODY = "等线";

await fs.mkdir(path.dirname(CANDIDATE), { recursive: true });
await fs.mkdir(path.dirname(FINAL), { recursive: true });
await fs.mkdir(RENDER, { recursive: true });

const presentation = await PresentationFile.importPptx(await FileBlob.load(SOURCE));
presentation.slides.getItem(17).duplicate();
presentation.slides.getItem(18).duplicate();
const cover = presentation.slides.getItem(0);
const contents = presentation.slides.getItem(1);
const contentSlides = presentation.slides.items.slice(2, 20);
const thanks = presentation.slides.getItem(20);

const inspection = await presentation.inspect({ select: "$.slides[*]", include: ["id", "index", "elements.id"] });
for (const record of inspection.ndjson.trim().split("\n").map((line) => JSON.parse(line))) {
  if (record.index >= 2 && record.index <= 19) {
    const ids = (record.elements ?? []).map((item) => item.id).filter(Boolean);
    if (ids.length) presentation.delete(ids);
  }
}

function addText(slide, text, position, opts = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox", name: opts.name, position,
    fill: opts.fill ?? "none",
    line: opts.line ?? { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontSize: opts.fontSize ?? 21,
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

function box(slide, position, opts = {}) {
  const geometry = opts.geometry ?? "rect";
  return slide.shapes.add({
    geometry, position, name: opts.name,
    fill: opts.fill ?? C.white,
    line: opts.line ?? { style: "solid", fill: opts.lineColor ?? C.line, width: opts.lineWidth ?? 1 },
    ...(["rect", "textbox", "roundRect"].includes(geometry) ? { borderRadius: opts.borderRadius ?? "rounded-md" } : {}),
  });
}

function title(slide, text) {
  addText(slide, text, { left: 40, top: 34, width: 1010, height: 58 }, {
    fontSize: 37, typeface: FONT, color: C.blue, bold: true,
    verticalAlignment: "middle", insets: 0,
  });
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
  addText(slide, text, { left: x, top: y, width: w, height: 24 }, { fontSize: 13, color: C.muted, alignment: "center", verticalAlignment: "middle", insets: 0 });
}

function footer(slide, text) {
  addText(slide, text, { left: 42, top: 691, width: 1150, height: 17 }, { fontSize: 10.5, color: C.muted, insets: 0 });
}

function notes(slide, text) { slide.speakerNotes.text = text; }

function arrow(slide, x, y, w = 34, h = 22, fill = C.indigo) {
  box(slide, { left: x, top: y, width: w, height: h }, { geometry: "rightArrow", fill, line: { style: "solid", fill, width: 0 } });
}

function detailSection(slide, x, y, w, h, label, body, color = C.blue2, opts = {}) {
  box(slide, { left: x, top: y, width: w, height: h }, { fill: opts.fill ?? C.white, lineColor: opts.lineColor ?? C.line, lineWidth: 1 });
  box(slide, { left: x, top: y, width: 8, height: h }, { fill: color, line: { style: "solid", fill: color, width: 0 }, borderRadius: 0 });
  addText(slide, label, { left: x + 18, top: y + 9, width: w - 34, height: 28 }, { fontSize: opts.labelSize ?? 18, bold: true, color, insets: 0, verticalAlignment: "middle" });
  addText(slide, body, { left: x + 18, top: y + 40, width: w - 34, height: h - 48 }, { fontSize: opts.bodySize ?? 17, color: C.gray, insets: 0, lineSpacing: 1.05 });
}

function conclusionStrip(slide, text, opts = {}) {
  addText(slide, text, { left: 75, top: opts.top ?? 607, width: 1130, height: opts.height ?? 58 }, {
    fontSize: opts.fontSize ?? 23, bold: true, color: opts.color ?? C.red,
    alignment: "center", verticalAlignment: "middle",
    fill: opts.fill ?? C.redPale,
    line: { style: "solid", fill: opts.lineColor ?? "#F3B4B4", width: 1 },
  });
}

function imageExplanationSlide(slide, slideTitle, imageKey, imagePosition, captionText, sections, noteText, opts = {}) {
  title(slide, slideTitle);
  img(slide, imageKey, imagePosition, { alt: captionText, crop: opts.crop });
  caption(slide, captionText, imagePosition.left, imagePosition.top + imagePosition.height + 9, imagePosition.width);
  const x = opts.sectionX ?? 720;
  const w = opts.sectionW ?? 490;
  const startY = opts.startY ?? 118;
  const gap = opts.gap ?? 14;
  const h = opts.sectionH ?? 106;
  sections.forEach(([label, body, color], index) => detailSection(slide, x, startY + index * (h + gap), w, h, label, body, color));
  if (opts.conclusion) conclusionStrip(slide, opts.conclusion, { top: opts.conclusionTop ?? 610, height: opts.conclusionHeight ?? 55, fontSize: opts.conclusionSize ?? 22 });
  notes(slide, noteText);
}

// Cover.
for (const shape of cover.shapes.items) {
  if (shape.name === "副标题 8") {
    shape.text = "SSVEP 在线识别低准确率的排查历程与下一步算法";
    shape.text.style = { fontSize: 43, typeface: FONT, color: "#0070C0", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  } else if (shape.name === "文本占位符 6") {
    shape.text = "汇报人：________";
    shape.text.style = { fontSize: 34, typeface: FONT, color: "#8C8C8C", bold: true, alignment: "center", verticalAlignment: "middle" };
  } else if (shape.name === "文本占位符 4") {
    shape.text = "从 FBCCA 调试、Transformer 尝试到个人模板与重叠窗口问题";
    shape.text.style = { fontSize: 23, typeface: BODY, color: "#777777", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  }
}
notes(cover, "本报告按实际调试时间顺序说明问题。研究目标是提升单人六指令 SSVEP 在线识别，不讨论蜂群任务分配。所有结果均区分主观现场观察、内部一致性和独立在线准确率。\n项目路径：/Users/geniusalu/Documents/brain-drone-swarm-console");

// Contents becomes a simple route map.
for (const shape of contents.shapes.items) {
  if (shape.name === "文本框 92") {
    shape.text = "1. 原始 FBCCA 出现了什么问题\n2. 我依次排查并调整了什么\n3. Transformer 为什么没有改善\n4. 个人模板为什么有所改善\n5. 为什么总体识别率仍然偏低\n6. 重叠窗口带来的连续误判问题\n7. 最终研究问题与候选算法\n8. 下一步实验和希望老师指导的内容";
    shape.text.style = { fontSize: 24, typeface: FONT, color: C.blue2, bold: true, alignment: "left", verticalAlignment: "top", autoFit: "shrinkText", lineSpacing: 1.12 };
  }
}
notes(contents, "目录刻意按实验历程排列。汇报时先让老师明白发生了什么，再讨论算法，而不是先给出抽象创新框架。");

// 3. One-slide chronological overview.
{
  const s = contentSlides[0]; title(s, "1  整段工作的时间顺序");
  const steps = [
    ["原始 FBCCA", "六目标能运行，但经常把注视目标识别成其他指令", C.red],
    ["检查信号", "发现干电极接触、头发和坏通道使脑电质量不稳定", C.orange],
    ["连续调试", "改频率、色块大小、布局、阈值、通道数和增益", C.blue2],
    ["训练 Transformer", "低质量小样本没有支撑模型，验证结果反而更差", C.red],
    ["加入个人模板", "每类单独采十次，在线检索个人特征并融合 FBCCA", C.green],
    ["发现窗口问题", "个人模板有所改善，但高度重叠窗口会让误判连续出现", C.orange],
  ];
  steps.forEach(([head, body, color], i) => {
    const x = 48 + i * 202;
    box(s, { left: x, top: 185, width: 176, height: 250 }, { fill: i % 2 ? C.pale : C.white, lineColor: color, lineWidth: 1.4 });
    addText(s, String(i + 1), { left: x + 58, top: 145, width: 60, height: 60 }, { fontSize: 32, bold: true, color: C.white, alignment: "center", verticalAlignment: "middle", fill: color, line: { style: "solid", fill: color, width: 0 }, insets: 0 });
    addText(s, head, { left: x + 14, top: 220, width: 148, height: 45 }, { fontSize: 20, bold: true, color, alignment: "center", verticalAlignment: "middle", insets: 0 });
    addText(s, body, { left: x + 16, top: 285, width: 144, height: 120 }, { fontSize: 16.5, color: C.gray, alignment: "center", verticalAlignment: "middle", insets: 0, lineSpacing: 1.08 });
    if (i < steps.length - 1) arrow(s, x + 180, 294, 20, 18, C.indigo);
  });
  conclusionStrip(s, "最终问题不是“哪个频率不好”，而是低质量 EEG、个体差异和在线判决共同造成指令不稳定。", { top: 520, height: 86, fontSize: 25 });
  footer(s, "后续各页均按“为什么做、做了什么、看到了什么、得到什么结论”展开");
  notes(s, "这是全文路线图。先说明不是一下子提出新算法，而是从复现失败和多轮调试中逐步定位到两个算法问题：个体解码和重叠窗口决策。");
}

// 4. System goal.
{
  const s = contentSlides[1]; title(s, "2  已完成的系统与最初目标");
  img(s, "hardware", { left: 50, top: 122, width: 350, height: 375 }, { alt: "后枕区电极帽与采集盒", crop: { left: 0.04, top: 0.15, right: 0.08, bottom: 0.20 } });
  img(s, "onlineDrive", { left: 435, top: 122, width: 790, height: 375 }, { alt: "六目标 SSVEP 在线操控界面" });
  caption(s, "八通道 EEG 硬件，主要使用后枕区和顶枕区", 50, 505, 350);
  caption(s, "六个闪烁目标对应起飞、前进、后退、左移、右移和降落", 435, 505, 790);
  detailSection(s, 75, 555, 350, 96, "输入", "用户持续注视一个目标，设备以约 250 Hz 采集 EEG。", C.blue2, { bodySize: 16.5 });
  detailSection(s, 465, 555, 350, 96, "处理", "4 秒数据进入 SSVEP 解码器，每 0.25 秒更新一次。", C.cyan, { bodySize: 16.5 });
  detailSection(s, 855, 555, 350, 96, "期望输出", "注视哪个目标，系统稳定输出对应指令，未确定时保持空闲。", C.green, { bodySize: 16.5 });
  notes(s, "当前频率配置为 8、9、10、11、12、13 Hz。系统已经完成采集、科研测试、动作映射和模拟控制。真正未解决的是识别准确率和错误触发。");
}

// 5. Original FBCCA symptom.
imageExplanationSlide(
  contentSlides[2],
  "3  原始 FBCCA 的实际问题",
  "commandTest",
  { left: 45, top: 125, width: 635, height: 395 },
  "六目标同时闪烁的在线指令测试界面",
  [
    ["原始方法", "FBCCA 将 EEG 与每个频率的正弦、余弦和谐波参考信号比较，最高分对应输出类别。", C.blue2],
    ["现场现象", "即使一直看“前进”，系统也可能长期输出“起飞”。遮住起飞目标后，又更容易输出前进或其他类别。", C.red],
    ["说明的问题", "某一频率得分最高并不等于用户真的在看它。固定偏置、噪声或通道异常都可能让某类长期占优。", C.orange],
    ["核心问题", "六类之间不能稳定区分，输出指令与真实注视目标不一致。", C.red],
  ],
  "FBCCA 是无训练基线。现场出现单一类别偏置和错误指令。这里不能只看有没有输出，而要用系统提示的目标作为标准答案计算正确率和错误触发率。",
  { sectionX: 725, sectionW: 485, startY: 115, sectionH: 115, gap: 10 }
);

// 6. Signal quality discovery.
imageExplanationSlide(
  contentSlides[3],
  "4  第一项发现：脑电信号本身不稳定",
  "qualityLive",
  { left: 45, top: 125, width: 700, height: 410 },
  "一次真实采集中，软件只判定 4 / 8 个通道可用",
  [
    ["为什么检查", "FBCCA 一直偏向少数类别，首先需要确认输入是否真的是可用的后枕区脑电。", C.blue2],
    ["实际情况", "没有导电膏且头发较厚，干电极接触容易变化。不同佩戴中，可用通道数量也不同。", C.red],
    ["已有数据", "最近 60 段校准中，Oz 100%、PO4 98.3%、PO3 85%；O1 只有 6.7%。", C.orange],
    ["得到的结论", "八个通道并不等于八份有效信息。坏通道进入融合后会增加噪声，固定通道组合也可能跨会话失效。", C.red],
  ],
  "通道可用率来自 models/personal_template_ssvep_experimental_metadata.json。界面截图来自用户真实采集。增加增益会同时放大脑电和噪声，无法替代接触质量。",
  { sectionX: 785, sectionW: 425, startY: 115, sectionH: 116, gap: 8 }
);

// 7. Frequency changes.
{
  const s = contentSlides[4]; title(s, "5  第二项尝试：修改闪烁频率");
  img(s, "oldFreqTest", { left: 45, top: 120, width: 540, height: 340 }, { alt: "曾使用 14 到 24 Hz 的六目标测试界面" });
  img(s, "onlineDrive", { left: 695, top: 120, width: 540, height: 340 }, { alt: "当前 8 到 13 Hz 的六目标界面" });
  caption(s, "曾测试较高频率组合，界面长时间显示未识别", 45, 470, 540);
  caption(s, "后来按学长原版参数改回 8–13 Hz", 695, 470, 540);
  arrow(s, 610, 265, 55, 30, C.indigo);
  detailSection(s, 60, 525, 270, 120, "为什么改", "怀疑 11 Hz 与自然脑电或某些谐波重叠，导致某类得分异常。", C.blue2, { bodySize: 16.5 });
  detailSection(s, 360, 525, 270, 120, "怎么改", "尝试替换频率、拉开频率间隔，并恢复学长原版 8–13 Hz 方案。", C.cyan, { bodySize: 16.5 });
  detailSection(s, 660, 525, 270, 120, "看到什么", "偏置类别会变化，但并没有形成稳定的“看哪个就识别哪个”。", C.orange, { bodySize: 16.5 });
  detailSection(s, 960, 525, 270, 120, "结论", "频率会影响可分性，但换频率不能修复低信噪比和个体差异。", C.red, { bodySize: 16.5 });
  notes(s, "用户先怀疑 11 Hz 盖过其他频率，之后尝试不同频率，并最终要求复现学长的 8–13 Hz 配置。结果说明频率是影响因素，不是唯一根因。");
}

// 8. Stimulus size and calibration protocol.
{
  const s = contentSlides[5]; title(s, "6  第三项尝试：放大色块并统一校准布局");
  img(s, "isolated", { left: 45, top: 125, width: 370, height: 250 }, { alt: "同位置单目标独闪界面" });
  img(s, "guided", { left: 455, top: 125, width: 370, height: 250 }, { alt: "六目标引导校准界面" });
  img(s, "onlineDrive", { left: 865, top: 125, width: 370, height: 250 }, { alt: "正式六目标在线操控界面" });
  caption(s, "单目标独闪，其他位置为黑色", 45, 385, 370);
  caption(s, "六目标同布局引导采集", 455, 385, 370);
  caption(s, "正式六目标在线识别", 865, 385, 370);
  detailSection(s, 60, 445, 270, 145, "原来的怀疑", "色块太小、彼此太近，视觉刺激不足或注意力被相邻目标干扰。", C.blue2, { bodySize: 17 });
  detailSection(s, 360, 445, 270, 145, "实际修改", "色块面积扩大约 2.7 倍，放到屏幕边缘；校准与测试保持相同尺寸和位置。", C.cyan, { bodySize: 17 });
  detailSection(s, 660, 445, 270, 145, "改善之处", "刺激更明显，单目标采集也更容易按类别保存和复查。", C.green, { bodySize: 17 });
  detailSection(s, 960, 445, 270, 145, "仍然存在", "正式测试时六个目标同时闪烁，视觉竞争仍不同于单目标独闪。", C.red, { bodySize: 17 });
  conclusionStrip(s, "放大和拉开色块改善了实验条件，但没有单独解决分类准确率。", { top: 620, height: 45, fontSize: 21 });
  notes(s, "单目标校准必须使用与正式界面相同的位置和尺寸，只有当前目标闪烁，其余保持黑色。这样减少几何域差异，但多目标竞争仍需独立在线测试。");
}

// 9. Threshold, channels, gain.
imageExplanationSlide(
  contentSlides[6],
  "7  第四项尝试：阈值、通道数与增益",
  "algorithm",
  { left: 45, top: 122, width: 650, height: 420 },
  "科研算法页可切换 FBCCA、个人模板和 Transformer，并显示当前参数",
  [
    ["阈值和差值", "放宽置信度与前两类差值后，更容易出指令，但错误触发也明显增加。收紧后又会长时间没有结果。", C.red],
    ["连续确认", "要求连续两个窗口同类，可以抑制偶发抖动，但两个窗口高度重叠，并非两次独立证据。", C.orange],
    ["通道数量", "先用 8 通道，之后尝试 2、3、4 通道。少通道有时更稳，因为排除了坏通道。", C.blue2],
    ["硬件增益", "增益只扩大电压幅度，也会扩大工频和接触噪声。识别需要提高信噪比，而不是只把波形画得更大。", C.cyan],
  ],
  "当前 FBCCA 默认使用 Oz、PO3、PO4。个人实验模型保存了 Oz、PO3、PO4、P3 四通道。参数修改能改变输出频率，但不能替代可靠标签和独立验证。",
  { sectionX: 735, sectionW: 475, startY: 115, sectionH: 116, gap: 8 }
);

// 10. Summary of tuning attempts.
{
  const s = contentSlides[7]; title(s, "8  前四轮调试得到的共同结论");
  const rows = [
    ["改频率", "避开可疑频段，改变各类别得分", "类别偏置会变化，仍不能稳定对应注视目标"],
    ["放大和拉开色块", "增强视觉刺激，降低空间干扰", "更容易注视，但分类错误仍存在"],
    ["调阈值和差值", "控制系统愿不愿意输出", "只能在“乱出指令”和“完全不出”之间权衡"],
    ["改通道数和增益", "减少坏通道或扩大幅度", "短时表现变化，跨佩戴仍不稳定"],
  ];
  addText(s, "调整内容", { left: 70, top: 125, width: 230, height: 55 }, { fontSize: 22, bold: true, color: C.white, alignment: "center", verticalAlignment: "middle", fill: C.blue2 });
  addText(s, "它实际改变了什么", { left: 300, top: 125, width: 420, height: 55 }, { fontSize: 22, bold: true, color: C.white, alignment: "center", verticalAlignment: "middle", fill: C.indigo });
  addText(s, "为什么仍没有解决", { left: 720, top: 125, width: 490, height: 55 }, { fontSize: 22, bold: true, color: C.white, alignment: "center", verticalAlignment: "middle", fill: C.red });
  rows.forEach((row, i) => {
    const y = 180 + i * 92;
    const fill = i % 2 ? C.pale : C.white;
    addText(s, row[0], { left: 70, top: y, width: 230, height: 82 }, { fontSize: 20, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", fill, line: { style: "solid", fill: C.line, width: 1 } });
    addText(s, row[1], { left: 300, top: y, width: 420, height: 82 }, { fontSize: 18, color: C.gray, alignment: "center", verticalAlignment: "middle", fill, line: { style: "solid", fill: C.line, width: 1 } });
    addText(s, row[2], { left: 720, top: y, width: 490, height: 82 }, { fontSize: 18, color: C.gray, alignment: "center", verticalAlignment: "middle", fill, line: { style: "solid", fill: C.line, width: 1 } });
  });
  conclusionStrip(s, "这些调整主要改变输入条件或输出门槛，没有学到“这个人看每个频率时的真实脑电特征”。", { top: 585, height: 72, fontSize: 24 });
  notes(s, "这一页是第一阶段结论。参数调节不是无用，而是证明了错误并非由单个频率或一个阈值造成。下一步因此转向数据驱动模型。");
}

// 11. Transformer attempt.
imageExplanationSlide(
  contentSlides[8],
  "9  第五项尝试：使用采集数据训练 Transformer",
  "experiment",
  { left: 45, top: 125, width: 660, height: 410 },
  "科研实验页用于回放 CSV、选择算法并查看测试结果",
  [
    ["为什么尝试", "希望模型直接从多通道时序中学习六类差异，减少人工参考信号和固定阈值的限制。", C.blue2],
    ["已有数据", "累计采集了数百段或窗口，但有效、独立、标签可靠的训练样本远少于表面数量。", C.orange],
    ["一次实际结果", "保存的训练记录中，78 条训练、27 条验证，验证准确率为 7.4%，低于七类随机基线约 14.3%。", C.red],
    ["得到的结论", "复杂模型没有自动修复输入质量。当前数据规模和质量不足以支撑 Transformer。", C.red],
  ],
  "Transformer 数值来自此前训练元数据：验证准确率 7.4074%，Macro-F1 2.857%，idle 假触发 100%。若后续重新训练，必须重新报告对应元数据。",
  { sectionX: 745, sectionW: 465, startY: 115, sectionH: 116, gap: 8 }
);

// 12. Why Transformer failed.
{
  const s = contentSlides[9]; title(s, "10  Transformer 识别率更低的原因");
  addText(s, "Transformer", { left: 500, top: 290, width: 280, height: 105 }, { fontSize: 31, bold: true, color: C.white, alignment: "center", verticalAlignment: "middle", fill: C.red, line: { style: "solid", fill: C.red, width: 0 } });
  const causes = [
    [70, 125, "信号质量低", "模型首先学到接触变化、漂移和工频噪声，而不是稳定的 SSVEP 差异。", C.red],
    [790, 125, "独立样本不足", "重叠窗口数量很多，但相邻窗口内容近乎相同，不能当成大量独立样本。", C.orange],
    [70, 465, "跨会话漂移", "训练和测试佩戴状态变化后，通道幅值与空间分布也会改变。", C.blue2],
    [790, 465, "模型容量过大", "小数据条件下参数多，更容易记住训练数据而不能泛化到新会话。", C.cyan],
  ];
  causes.forEach(([x, y, head, body, color]) => {
    detailSection(s, x, y, 420, 150, head, body, color, { bodySize: 19, labelSize: 23 });
  });
  box(s, { left: 488, top: 195, width: 6, height: 95 }, { fill: C.indigo, line: { style: "solid", fill: C.indigo, width: 0 }, borderRadius: 0 });
  box(s, { left: 780, top: 195, width: 6, height: 95 }, { fill: C.indigo, line: { style: "solid", fill: C.indigo, width: 0 }, borderRadius: 0 });
  box(s, { left: 488, top: 395, width: 6, height: 70 }, { fill: C.indigo, line: { style: "solid", fill: C.indigo, width: 0 }, borderRadius: 0 });
  box(s, { left: 780, top: 395, width: 6, height: 70 }, { fill: C.indigo, line: { style: "solid", fill: C.indigo, width: 0 }, borderRadius: 0 });
  conclusionStrip(s, "因此当前阶段继续加深网络意义不大，先建立可重复的个人特征和严格在线评价更重要。", { top: 625, height: 44, fontSize: 21 });
  notes(s, "高度重叠窗口如果随机拆分到训练和验证会形成数据泄漏。因此必须按完整试次或会话划分。这里说明 Transformer 失败原因，而不是断言 Transformer 本身不适合 SSVEP。");
}

// 13. Personal calibration collection.
imageExplanationSlide(
  contentSlides[10],
  "11  第六项尝试：为每个指令建立个人模板",
  "singlePage",
  { left: 45, top: 120, width: 650, height: 420 },
  "单目标页面按类别采集并训练个人实验模型",
  [
    ["采集方式", "每次只让一个指定位置闪烁，其他五个位置保持黑色。当前目标的位置和大小与正式六目标界面一致。", C.blue2],
    ["采集数量", "六个类别分别记录 10 次，共 60 段。每段保留真实标签、采样率、通道质量和刺激条件。", C.cyan],
    ["核心想法", "同一类别的多次 EEG 即使不完全相同，也可能共享个人频谱和周期波形特征。", C.green],
    ["在线使用", "新的 4 秒窗口先与六类个人数据比较，再把个人得分与 FBCCA 得分进行加权。", C.orange],
  ],
  "当前个人模型来自 data/single_target_calibration/single-20261018-210353-640369，共 60 段、每类 10 段。",
  { sectionX: 735, sectionW: 475, startY: 112, sectionH: 118, gap: 7 }
);

// 14. Actual personal algorithm details.
{
  const s = contentSlides[11]; title(s, "12  当前个人模型到底做了什么");
  const stages = [
    ["1. 选通道", "使用 Oz、PO3、PO4、P3\n保留会话内较稳定的后枕区信息", C.blue2],
    ["2. 提取特征", "6–45 Hz 对数频谱占 75%\n自相关周期波形占 25%\n两者均消除绝对相位影响", C.cyan],
    ["3. 检索类别", "把当前窗口与 60 个历史试次比较\n核岭检索让相似试次共同投票", C.green],
    ["4. 融合 FBCCA", "个人模板权重 97.5%\nFBCCA 权重 2.5%\n输出六类相对得分", C.orange],
    ["5. 接受或拒绝", "最高得分至少 0.21\n前两类差值至少 0.015\n在线还要求连续两次同类", C.red],
  ];
  stages.forEach(([head, body, color], i) => {
    const x = 45 + i * 244;
    detailSection(s, x, 150, 215, 335, head, body, color, { bodySize: 17.5, labelSize: 20 });
    if (i < stages.length - 1) arrow(s, x + 219, 300, 20, 18, C.indigo);
  });
  addText(s, "个人得分不是准确率，也不是校准概率。它只表示当前窗口在六类个人样本中的相对相似程度。", { left: 120, top: 525, width: 1040, height: 72 }, { fontSize: 24, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", fill: C.bluePale, line: { style: "solid", fill: C.indigo, width: 1 } });
  conclusionStrip(s, "这一版模型可以科研测试，但 metadata 明确标记为 production_approved = false，不能直接控制实机。", { top: 615, height: 48, fontSize: 20 });
  notes(s, "算法细节来自 src/ml/personal_template_features.py、personal_template_training.py、personal_template_runtime.py 和 src/bci/personal_template_decoder.py。实验模型使用核岭试次检索与软 FBCCA 融合。");
}

// 15. Improvement and measurement boundary.
{
  const s = contentSlides[12]; title(s, "13  个人模板确实改善了现场表现，但还不能叫高准确率");
  detailSection(s, 60, 125, 540, 165, "现场主观观察", "加入个人模板后，系统不再像原始 FBCCA 那样长期被单一类别完全占据，注视目标与输出类别的对应关系明显改善。这个现象说明个人数据有价值。", C.green, { bodySize: 20, labelSize: 25, fill: C.greenPale });
  detailSection(s, 60, 320, 540, 165, "仍然存在的现象", "六目标同时闪烁时仍会识别错，阈值放宽后误触发增加；阈值收紧后又可能没有输出。跨佩戴和跨会话稳定性也没有完成验证。", C.red, { bodySize: 20, labelSize: 25, fill: C.redPale });
  addText(s, "38.3%", { left: 720, top: 125, width: 380, height: 125 }, { fontSize: 58, bold: true, color: C.orange, alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "留一试次内部一致性", { left: 720, top: 250, width: 380, height: 45 }, { fontSize: 25, bold: true, color: C.blue, alignment: "center", insets: 0 });
  addText(s, "每次拿出一段，用其余 59 段组成模型再判断。\n它只检查当前 60 段内部是否可重复。", { left: 680, top: 310, width: 460, height: 95 }, { fontSize: 19, color: C.gray, alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "这不是独立在线准确率", { left: 715, top: 430, width: 390, height: 58 }, { fontSize: 25, bold: true, color: C.red, alignment: "center", verticalAlignment: "middle", fill: C.redPale, line: { style: "solid", fill: C.red, width: 1 } });
  conclusionStrip(s, "正确表述：个人模板带来明显的现场改善迹象，但总体识别率仍低，需要有标准答案的独立在线实验量化。", { top: 560, height: 92, fontSize: 24 });
  notes(s, "38.33% 来自实验模型 metadata 的 leave_one_trial_consistency。不要将它写成在线准确率。用户的现场感受可作为观察结果，但论文必须由系统随机提示目标并记录标准答案。");
}

// 16. Why accuracy remains low.
imageExplanationSlide(
  contentSlides[13],
  "14  加入个人模板后，为什么总体识别率仍然偏低",
  "acquisitionLive",
  { left: 45, top: 125, width: 665, height: 405 },
  "实时波形显示不同通道仍存在漂移、噪声和幅度差异",
  [
    ["模板本身不够稳定", "60 段内部一致性只有 38.3%，说明相同目标的多次 EEG 仍然差异很大。", C.red],
    ["校准与测试条件不同", "单目标独闪时没有周边竞争，正式六目标同时闪烁时视觉输入发生变化。", C.orange],
    ["融合权重来自同一批数据", "97.5% 的模板权重是实验性设置，没有通过独立验证集确定。", C.blue2],
    ["在线判决仍过于简单", "连续两次同类就确认，但两次窗口 93.75% 重叠，错误也会连续重复。", C.red],
  ],
  "界面截图来自真实采集。个人模型 metadata 明确说明门槛故意放宽、禁止实机，当前结果只能支持继续研究。",
  { sectionX: 750, sectionW: 460, startY: 115, sectionH: 116, gap: 8 }
);

// 17. Overlap issue.
{
  const s = contentSlides[14]; title(s, "15  新发现：连续两次判断并不是两次独立证据");
  addText(s, "论文复现参数", { left: 65, top: 125, width: 510, height: 52 }, { fontSize: 23, bold: true, color: C.white, alignment: "center", verticalAlignment: "middle", fill: C.blue2 });
  addText(s, "当前项目参数", { left: 705, top: 125, width: 510, height: 52 }, { fontSize: 23, bold: true, color: C.white, alignment: "center", verticalAlignment: "middle", fill: C.red });
  detailSection(s, 65, 190, 510, 135, "2 秒窗口，每 0.25 秒更新", "相邻窗口重叠 87.5%。论文还要求连续两次同类确认，但没有把这两次结果的相关性单独建模。", C.blue2, { bodySize: 19 });
  detailSection(s, 705, 190, 510, 135, "4 秒窗口，每 0.25 秒更新", "相邻窗口重叠 93.75%。第二次判断只有 0.25 秒新数据，占整个窗口 6.25%。", C.red, { bodySize: 19 });
  box(s, { left: 90, top: 395, width: 920, height: 64 }, { fill: "#D7E4FF", lineColor: C.indigo });
  box(s, { left: 150, top: 475, width: 920, height: 64 }, { fill: "#BDECEE", lineColor: C.cyan });
  box(s, { left: 1010, top: 475, width: 60, height: 64 }, { fill: "#FFB5B0", lineColor: C.red, borderRadius: 0 });
  addText(s, "窗口 t：4.00 秒", { left: 90, top: 395, width: 920, height: 64 }, { fontSize: 22, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "窗口 t+1：3.75 秒旧数据 + 0.25 秒新数据", { left: 150, top: 475, width: 920, height: 64 }, { fontSize: 22, bold: true, color: "#126A6A", alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "新增", { left: 1010, top: 540, width: 60, height: 26 }, { fontSize: 15, bold: true, color: C.red, alignment: "center", insets: 0 });
  conclusionStrip(s, "一次错误若由旧数据造成，下一窗口很可能继续犯同样错误。连续两次同类不能简单理解为可靠性翻倍。", { top: 590, height: 72, fontSize: 23 });
  footer(s, "准确说法：这是论文方案的潜在局限；当前项目把窗口扩大到 4 秒后，重叠进一步升高");
  notes(s, "论文参数为 2 秒窗口、0.25 秒步长、连续两次确认，因此重叠率为 87.5%。当前项目为 4 秒窗口、0.25 秒步长，重叠 93.75%。重叠本身不是错误，但把相关窗口当独立证据会高估确认强度。");
}

// 18. Exact research problem.
{
  const s = contentSlides[15]; title(s, "16  最终可以把研究问题说成一句话");
  addText(s, "在低质量、跨会话变化的少通道 EEG 条件下，如何融合个人模板与 FBCCA，并在高度重叠的在线窗口中估计真正新增的证据，从而降低错误指令，同时保持可接受的响应速度？", { left: 100, top: 130, width: 1080, height: 170 }, { fontSize: 31, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", fill: C.bluePale, line: { style: "solid", fill: C.blue2, width: 2 } });
  detailSection(s, 95, 360, 500, 205, "子问题一：怎样识别得更准", "需要对付通道质量变化和个体差异。候选方向是质量加权的 TDCA 或 eTRCA，再与 FBCCA 提供的频率先验融合。", C.cyan, { bodySize: 22, labelSize: 26, fill: C.cyanPale });
  detailSection(s, 685, 360, 500, 205, "子问题二：怎样避免连续误触发", "需要根据窗口新增信息量累积证据，并显式建模空闲、候选、确认和释放状态，而不是只数连续几次同类。", C.orange, { bodySize: 22, labelSize: 26, fill: C.orangePale });
  conclusionStrip(s, "这不是无人机任务分配问题。研究对象仍然是单人的 SSVEP 指令识别与在线决策。", { top: 610, height: 52, fontSize: 22 });
  notes(s, "这是建议对老师使用的主问题。它从实验事实出发，并自然分成解码器和在线判决两个算法子问题。");
}

// 19. Existing methods and algorithm candidates.
{
  const s = contentSlides[16]; title(s, "17  可以请老师选择的算法方向");
  const methods = [
    ["FBCCA", "保留为无训练基线", "利用固定正弦参考和多子带。优点是数据需求小，缺点是不能充分适应个人相位与空间模式。", C.blue2],
    ["eTRCA", "个人模板强基线", "从多次校准中寻找可重复的空间成分，适合回答“相同目标能否重复出现相似脑电”。", C.cyan],
    ["TDCA", "重点候选主体算法", "把目标与非目标数据投影到更可分的空间，常用于短窗和个人化 SSVEP 识别。", C.green],
    ["质量加权融合", "与当前问题直接对应", "根据通道质量和会话相似度，动态调整个人模型与 FBCCA 的权重。", C.orange],
    ["序贯决策或 HMM", "解决窗口与误触发", "按照新增证据更新状态，区分空闲、候选、确认、保持和释放。", C.red],
  ];
  methods.forEach(([head, role, body, color], i) => {
    const y = 118 + i * 101;
    addText(s, head, { left: 55, top: y, width: 180, height: 82 }, { fontSize: 23, bold: true, color, alignment: "center", verticalAlignment: "middle", fill: C.pale, line: { style: "solid", fill: color, width: 1 } });
    addText(s, role, { left: 235, top: y, width: 260, height: 82 }, { fontSize: 19, bold: true, color: C.blue, alignment: "center", verticalAlignment: "middle", fill: C.white, line: { style: "solid", fill: C.line, width: 1 } });
    addText(s, body, { left: 495, top: y, width: 730, height: 82 }, { fontSize: 18, color: C.gray, verticalAlignment: "middle", fill: C.white, line: { style: "solid", fill: C.line, width: 1 }, insets: { left: 18, right: 18, top: 6, bottom: 6 } });
  });
  conclusionStrip(s, "建议主线：TDCA 或 eTRCA 负责提高可分性，质量权重负责跨会话稳定，序贯决策负责减少连续误触发。", { top: 625, height: 43, fontSize: 20 });
  notes(s, "参考文献：Chen et al., FBCCA, J Neural Eng 2015, https://pubmed.ncbi.nlm.nih.gov/26035476/；Nakanishi et al., eTRCA, IEEE TBME 2018, https://pubmed.ncbi.nlm.nih.gov/28436836/；Liu et al., TDCA, IEEE TNSRE 2021, https://bingchuanliu.github.io/assets/pdf/liu_tnsre.pdf；训练数据 CCA, https://pubmed.ncbi.nlm.nih.gov/32380925/。个人模板加 FBCCA 本身不是新算法，创新需要体现在质量和在线证据建模。");
}

// 20. Next experiment and teacher ask.
{
  const s = contentSlides[17]; title(s, "18  下一步实验与希望老师指导的内容");
  img(s, "paradigm", { left: 45, top: 122, width: 520, height: 330 }, { alt: "科研范式与实验参数页面" });
  caption(s, "当前科研界面可以配置自动实验、窗口和目标参数", 45, 462, 520);
  detailSection(s, 615, 120, 590, 130, "第一步：建立可信基线", "重新检查电极接触；按会话划分数据；统一校准与测试布局；比较 FBCCA、当前个人模型、eTRCA 和 TDCA。", C.blue2, { bodySize: 19, labelSize: 23 });
  detailSection(s, 615, 270, 590, 130, "第二步：做完整消融", "依次去掉通道质量权重、个人模板融合、重叠修正和 idle/释放，观察哪一部分真正降低错误触发。", C.cyan, { bodySize: 19, labelSize: 23 });
  detailSection(s, 615, 420, 590, 130, "第三步：独立在线验证", "系统随机提示要注视的目标，记录正确率、Macro-F1、每分钟错误触发数和响应时延。先模拟控制，再考虑低风险实机。", C.red, { bodySize: 19, labelSize: 23 });
  addText(s, "希望老师重点判断", { left: 65, top: 535, width: 500, height: 42 }, { fontSize: 25, bold: true, color: C.red, alignment: "center", verticalAlignment: "middle", insets: 0 });
  addText(s, "① 主体解码器选 TDCA 还是 eTRCA\n② “质量加权 + 重叠感知序贯决策”能否形成论文主线\n③ 需要多少跨会话数据和受试者才能完成有效实验", { left: 65, top: 580, width: 500, height: 90 }, { fontSize: 19, color: C.gray, alignment: "left", insets: 0, lineSpacing: 1.1 });
  notes(s, "这一页是向老师提出可回答的具体问题，不再笼统说‘请给一个新算法’。实验必须报告有标准答案的独立在线结果，现场感受只作为前期观察。");
}

// Thank-you slide.
for (const shape of thanks.shapes.items) {
  if (shape.name === "文本框 1") {
    shape.text = "感 谢 各 位 专 家\n请批评指正";
    shape.text.style = { fontSize: 72, typeface: FONT, color: "#FF0000", bold: true, alignment: "center", verticalAlignment: "middle", autoFit: "shrinkText" };
  }
}
notes(thanks, "建议现场最后用一句话收束：个人模板证明个体信息有价值，但低信号质量和重叠窗口仍限制在线稳定性，希望老师指导主体解码器和在线序贯决策的组合。");

await (await PresentationFile.exportPptx(presentation)).save(CANDIDATE);
const result = await finalizePresentation({
  workspaceDir: ROOT,
  candidatePath: CANDIDATE,
  finalPath: FINAL,
  receiptPath: RECEIPT,
  pythonExecutable: "/Users/geniusalu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3",
  integrityValidatorPath: path.join(SKILL, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(SKILL, "container_tools/inspect_presentation_layout_geometry.py"),
  explicitTotalSlideCount: 21,
  layoutArgs: [
    "--cover-role", "cover", "--cover-word-limit", "55",
    "--expected-aspect", "16:9", "--expected-slide-size-emu", "12192000,6858000",
    "--max-content-prose-words", "270",
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
